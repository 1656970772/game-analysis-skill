#!/usr/bin/env python3
"""Safely export Wwise WEM and SoundBank audio with vgmstream.

The input tree is treated as read-only. Every vgmstream metadata probe uses
``-m -I`` so that the CLI cannot fall back to its default ``<input>.wav``
write. Every decode has an explicit ``-o`` target below the configured output
root. Detailed indices stay outside the classified audio directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
NUMERIC_STREAM_NAME = re.compile(r"^\s*(\d+)")
BANK_SUBSONG_NAME = re.compile(r"__(\d+)\.wav$", re.IGNORECASE)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass
class MediaSemantic:
    media_id: str
    short_name: str = ""
    wwise_path: str = ""
    language: str = ""
    locations: set[str] = field(default_factory=set)
    banks: set[str] = field(default_factory=set)
    events: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)


@dataclass
class EventSemantic:
    bank: str
    event_id: str
    event_name: str
    object_path: str
    duration_type: str
    duration_min: str
    duration_max: str
    media_id: str
    media_location: str


@dataclass
class ConversionRow:
    kind: str
    source: str
    bank: str = ""
    subsong: str = ""
    media_id: str = ""
    semantic_name: str = ""
    language: str = ""
    output: str = ""
    classified_output: str = ""
    status: str = ""
    bytes: int = 0
    frames: int = 0
    sample_rate: int = 0
    channels: int = 0
    duration_seconds: float = 0.0
    message: str = ""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_within(path: Path, root: Path) -> bool:
    try:
        canonical(path).relative_to(canonical(root))
        return True
    except ValueError:
        return False


def require_within(path: Path, root: Path, label: str) -> Path:
    resolved = canonical(path)
    if not is_within(resolved, root):
        raise ValueError(f"{label} escaped configured root: {resolved}")
    return resolved


def validate_roots(
    input_dir: Path,
    output_dir: Path,
    index_dir: Path,
    classified_dir: Path | None = None,
    log_dir: Path | None = None,
) -> None:
    input_root = canonical(input_dir)
    write_roots = [
        ("output", canonical(output_dir)),
        ("index", canonical(index_dir)),
    ]
    if classified_dir is not None:
        write_roots.append(("classified", canonical(classified_dir)))
    if log_dir is not None:
        write_roots.append(("log", canonical(log_dir)))

    for label, root in write_roots:
        if is_within(root, input_root) or is_within(input_root, root):
            raise ValueError(
                f"{label} root overlaps read-only input root: {root} vs {input_root}"
            )

    for position, (left_label, left_root) in enumerate(write_roots):
        for right_label, right_root in write_roots[position + 1 :]:
            if left_root == right_root:
                raise ValueError(
                    f"{left_label} and {right_label} roots must be distinct: {left_root}"
                )


def sanitize_component(value: str, max_length: int = 72) -> str:
    cleaned = INVALID_WINDOWS_CHARS.sub("_", value)
    cleaned = WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "unnamed"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_length:
        digest = hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:8]
        cleaned = f"{cleaned[: max_length - 10].rstrip(' ._')}__{digest}"
    return cleaned


def semantic_stem(short_name: str, fallback: str) -> str:
    if short_name:
        normalized = short_name.replace("\\", "/").rstrip("/")
        name = Path(normalized).stem
        if name:
            return sanitize_component(name, max_length=60)
    return sanitize_component(fallback, max_length=60)


def external_output_path(
    *,
    source: Path,
    input_root: Path,
    output_root: Path,
    media_id: str,
    semantic_name: str,
    language: str,
) -> Path:
    input_root = canonical(input_root)
    output_root = canonical(output_root)
    relative = canonical(source).relative_to(input_root)
    parent_parts = [sanitize_component(part, max_length=48) for part in relative.parts[:-1]]
    language_part = sanitize_component(language or "Unknown", max_length=32)
    filename = (
        f"{sanitize_component(media_id or source.stem, max_length=24)}"
        f"__{semantic_stem(semantic_name, source.stem)}.wav"
    )
    target = output_root / "外置WEM" / language_part
    for part in parent_parts:
        target /= part
    target /= filename
    return require_within(target, output_root, "external WEM output")


def bank_output_directory(
    *,
    bank: Path,
    input_root: Path,
    output_root: Path,
) -> Path:
    relative = canonical(bank).relative_to(canonical(input_root))
    parent_parts = [sanitize_component(part, max_length=48) for part in relative.parts[:-1]]
    target = canonical(output_root) / "银行内嵌WAV"
    for part in parent_parts:
        target /= part
    target /= sanitize_component(bank.stem, max_length=64)
    return require_within(target, output_root, "bank output")


def build_metadata_command(
    vgmstream: Path,
    source: Path,
    subsong: int | None = None,
) -> list[str]:
    command = [str(vgmstream), "-m", "-I"]
    if subsong is not None:
        command.extend(["-s", str(subsong)])
    command.append(str(source))
    return command


def build_decode_command(
    vgmstream: Path,
    source: Path,
    output: Path,
) -> list[str]:
    return [str(vgmstream), "-i", "-o", str(output), str(source)]


def build_bank_decode_command(
    vgmstream: Path,
    source: Path,
    output_template: Path,
) -> list[str]:
    return [
        str(vgmstream),
        "-i",
        "-S",
        "0",
        "-o",
        str(output_template),
        str(source),
    ]


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("vgmstream did not emit a JSON object")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def probe_bank(
    vgmstream: Path,
    bank: Path,
    *,
    subsong: int | None = None,
    runner: Runner = subprocess.run,
    cwd: Path | None = None,
) -> dict[str, Any]:
    command = build_metadata_command(vgmstream, bank, subsong=subsong)
    completed = run_command(command, cwd=cwd, runner=runner)
    if completed.returncode != 0:
        raise RuntimeError(
            f"vgmstream metadata failed ({completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    payload = parse_json_stdout(completed.stdout)
    stream_info = payload.get("streamInfo") or {}
    total = int(stream_info.get("total") or 0)
    return {
        "stream_count": total,
        "stream_index": int(stream_info.get("index") or 0),
        "stream_name": str(stream_info.get("name") or ""),
        "sample_rate": int(payload.get("sampleRate") or 0),
        "channels": int(payload.get("channels") or 0),
        "number_of_samples": int(payload.get("numberOfSamples") or 0),
        "encoding": str(payload.get("encoding") or ""),
        "metadata_source": str(payload.get("metadataSource") or ""),
        "raw": payload,
    }


def add_media(
    media: dict[str, MediaSemantic],
    file_node: ET.Element,
    *,
    location: str,
    bank: str = "",
    event_name: str = "",
    event_id: str = "",
) -> str:
    media_id = (file_node.get("Id") or "").strip()
    if not media_id:
        return ""
    row = media.setdefault(media_id, MediaSemantic(media_id=media_id))
    short_name = (file_node.findtext("ShortName") or "").strip()
    wwise_path = (file_node.findtext("Path") or "").strip()
    language = (file_node.get("Language") or "").strip()
    if short_name and not row.short_name:
        row.short_name = short_name
    if wwise_path and not row.wwise_path:
        row.wwise_path = wwise_path
    if language and not row.language:
        row.language = language
    if location:
        row.locations.add(location)
    if bank:
        row.banks.add(bank)
    if event_name:
        row.events.add(event_name)
    if event_id:
        row.event_ids.add(event_id)
    return media_id


def parse_soundbanks_info(
    xml_path: Path,
) -> tuple[dict[str, MediaSemantic], list[EventSemantic], dict[str, dict[str, Any]]]:
    root = ET.parse(xml_path).getroot()
    media: dict[str, MediaSemantic] = {}
    events: list[EventSemantic] = []
    banks: dict[str, dict[str, Any]] = {}

    for file_node in root.findall("./StreamedFiles/File"):
        add_media(media, file_node, location="Loose")

    for bank_node in root.findall("./SoundBanks/SoundBank"):
        bank_short = (bank_node.findtext("ShortName") or "").strip()
        bank_path = (bank_node.findtext("Path") or "").strip()
        bank_key = bank_path or (f"{bank_short}.bnk" if bank_short else "")
        bank_info = banks.setdefault(
            bank_key,
            {
                "bank_id": (bank_node.get("Id") or "").strip(),
                "bank_name": bank_short,
                "bank_path": bank_path,
                "language": (bank_node.get("Language") or "").strip(),
                "memory_media_ids": set(),
                "loose_media_ids": set(),
                "event_count": 0,
            },
        )

        for location, container_name in (
            ("Memory", "IncludedMemoryFiles"),
            ("Loose", "ReferencedStreamedFiles"),
        ):
            direct = bank_node.find(container_name)
            if direct is not None:
                for file_node in direct.findall("File"):
                    media_id = add_media(
                        media,
                        file_node,
                        location=location,
                        bank=bank_key,
                    )
                    if media_id:
                        bank_info[
                            "memory_media_ids" if location == "Memory" else "loose_media_ids"
                        ].add(media_id)

        included_events = bank_node.find("IncludedEvents")
        if included_events is None:
            continue
        for event_node in included_events.findall("Event"):
            bank_info["event_count"] += 1
            event_id = (event_node.get("Id") or "").strip()
            event_name = (event_node.get("Name") or "").strip()
            object_path = (event_node.get("ObjectPath") or "").strip()
            duration_type = (event_node.get("DurationType") or "").strip()
            duration_min = (event_node.get("DurationMin") or "").strip()
            duration_max = (event_node.get("DurationMax") or "").strip()
            associations = 0
            for location, container_name in (
                ("Memory", "IncludedMemoryFiles"),
                ("Loose", "ReferencedStreamedFiles"),
            ):
                container = event_node.find(container_name)
                if container is None:
                    continue
                for file_node in container.findall("File"):
                    media_id = add_media(
                        media,
                        file_node,
                        location=location,
                        bank=bank_key,
                        event_name=event_name,
                        event_id=event_id,
                    )
                    if not media_id:
                        continue
                    associations += 1
                    bank_info[
                        "memory_media_ids" if location == "Memory" else "loose_media_ids"
                    ].add(media_id)
                    events.append(
                        EventSemantic(
                            bank=bank_key,
                            event_id=event_id,
                            event_name=event_name,
                            object_path=object_path,
                            duration_type=duration_type,
                            duration_min=duration_min,
                            duration_max=duration_max,
                            media_id=media_id,
                            media_location=location,
                        )
                    )
            if associations == 0:
                events.append(
                    EventSemantic(
                        bank=bank_key,
                        event_id=event_id,
                        event_name=event_name,
                        object_path=object_path,
                        duration_type=duration_type,
                        duration_min=duration_min,
                        duration_max=duration_max,
                        media_id="",
                        media_location="",
                    )
                )
    return media, events, banks


def discover_xml(input_dir: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        path = canonical(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"SoundbanksInfo.xml not found: {path}")
        if not is_within(path, input_dir):
            raise ValueError(f"SoundbanksInfo.xml must be inside input: {path}")
        return path
    candidates = sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.name.casefold() == "soundbanksinfo.xml"
        ),
        key=lambda path: (len(path.parts), str(path).casefold()),
    )
    return candidates[0] if candidates else None


def discover_files(input_dir: Path, suffix: str) -> list[Path]:
    wanted = suffix.casefold()
    return sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.casefold() == wanted
        ),
        key=lambda path: str(path).casefold(),
    )


def snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        snapshot[relative] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def describe_snapshot(snapshot: dict[str, tuple[int, int]]) -> dict[str, int]:
    return {
        "file_count": len(snapshot),
        "total_bytes": sum(size for size, _mtime_ns in snapshot.values()),
    }


def snapshot_diff(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> dict[str, list[str]]:
    before_names = set(before)
    after_names = set(after)
    return {
        "added": sorted(after_names - before_names),
        "removed": sorted(before_names - after_names),
        "changed": sorted(
            name
            for name in before_names & after_names
            if before[name] != after[name]
        ),
    }


def validate_wav(path: Path) -> tuple[bool, dict[str, Any], str]:
    if not path.is_file():
        return False, {}, "output_missing"
    if path.stat().st_size <= 44:
        return False, {"bytes": path.stat().st_size}, "output_too_small"
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
        if header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
            return False, {"bytes": path.stat().st_size}, "invalid_riff_wave_header"
        with wave.open(str(path), "rb") as wav:
            frames = int(wav.getnframes())
            sample_rate = int(wav.getframerate())
            channels = int(wav.getnchannels())
        if frames <= 0 or sample_rate <= 0 or channels <= 0:
            return (
                False,
                {
                    "bytes": path.stat().st_size,
                    "frames": frames,
                    "sample_rate": sample_rate,
                    "channels": channels,
                },
                "non_positive_audio_duration",
            )
        return (
            True,
            {
                "bytes": path.stat().st_size,
                "frames": frames,
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_seconds": frames / sample_rate,
            },
            "",
        )
    except (OSError, EOFError, wave.Error) as exc:
        return False, {"bytes": path.stat().st_size}, f"wav_validation_error: {exc}"


def archive_classified(
    source: Path,
    output_root: Path,
    classified_root: Path | None,
    link_mode: str,
) -> tuple[str, str]:
    if classified_root is None or link_mode == "none":
        return "", ""
    relative = canonical(source).relative_to(canonical(output_root))
    target = require_within(
        canonical(classified_root) / relative,
        classified_root,
        "classified output",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            if os.path.samefile(source, target):
                return str(target), "existing_hardlink"
        except OSError:
            pass
        target.unlink()
    if link_mode == "hardlink":
        os.link(source, target)
        return str(target), "hardlinked"
    if link_mode == "copy":
        shutil.copy2(source, target)
        return str(target), "copied"
    raise ValueError(f"Unsupported link mode: {link_mode}")


def write_text_log(
    log_dir: Path,
    name: str,
    command: Sequence[str],
    completed: subprocess.CompletedProcess[str],
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = [
        "command:",
        json.dumps(list(command), ensure_ascii=False),
        f"exit_code: {completed.returncode}",
        "",
        "stdout:",
        completed.stdout or "",
        "",
        "stderr:",
        completed.stderr or "",
    ]
    (log_dir / name).write_text("\n".join(payload), encoding="utf-8")


def decode_external(
    *,
    source: Path,
    input_root: Path,
    output_root: Path,
    classified_root: Path | None,
    semantic: MediaSemantic | None,
    vgmstream: Path,
    log_dir: Path,
    link_mode: str,
    overwrite: bool,
    dry_run: bool,
) -> ConversionRow:
    media_id = source.stem
    semantic_name = semantic.short_name if semantic else ""
    language = semantic.language if semantic else ""
    output = external_output_path(
        source=source,
        input_root=input_root,
        output_root=output_root,
        media_id=media_id,
        semantic_name=semantic_name,
        language=language,
    )
    row = ConversionRow(
        kind="external_wem",
        source=str(source),
        media_id=media_id,
        semantic_name=semantic_name,
        language=language,
        output=str(output),
    )
    if dry_run:
        row.status = "planned"
        return row
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not overwrite:
        valid, details, message = validate_wav(output)
        if valid:
            row.status = "existing_valid"
            row.bytes = int(details["bytes"])
            row.frames = int(details["frames"])
            row.sample_rate = int(details["sample_rate"])
            row.channels = int(details["channels"])
            row.duration_seconds = float(details["duration_seconds"])
            try:
                classified, archive_status = archive_classified(
                    output, output_root, classified_root, link_mode
                )
                row.classified_output = classified
                row.message = archive_status
            except OSError as exc:
                row.status = "failed"
                row.message = f"classification_archive_error: {exc}"
            return row
        row.message = f"existing_invalid: {message}"

    command = build_decode_command(vgmstream, source, output)
    completed = run_command(command, cwd=log_dir)
    write_text_log(
        log_dir,
        f"wem_{sanitize_component(media_id, 32)}.log",
        command,
        completed,
    )
    valid, details, validation_message = validate_wav(output)
    if completed.returncode != 0 or not valid:
        row.status = "failed"
        row.message = (
            f"exit={completed.returncode}; "
            f"{validation_message or completed.stderr.strip() or completed.stdout.strip()}"
        )
        return row
    row.status = "converted"
    row.bytes = int(details["bytes"])
    row.frames = int(details["frames"])
    row.sample_rate = int(details["sample_rate"])
    row.channels = int(details["channels"])
    row.duration_seconds = float(details["duration_seconds"])
    try:
        classified, archive_status = archive_classified(
            output, output_root, classified_root, link_mode
        )
        row.classified_output = classified
        row.message = archive_status
    except OSError as exc:
        row.status = "failed"
        row.message = f"classification_archive_error: {exc}"
    return row


def normalize_stream_media_id(stream_name: str) -> str:
    match = NUMERIC_STREAM_NAME.match(stream_name)
    return match.group(1) if match else ""


def expected_memory_ids(
    bank: Path,
    input_root: Path,
    banks: dict[str, dict[str, Any]],
) -> set[str]:
    relative = bank.relative_to(input_root).as_posix()
    candidates = {
        bank.name.casefold(),
        relative.casefold(),
        relative.replace("/", "\\").casefold(),
    }
    for key, info in banks.items():
        info_candidates = {
            key.casefold(),
            str(info.get("bank_path") or "").replace("\\", "/").casefold(),
            Path(str(info.get("bank_path") or "")).name.casefold(),
        }
        if candidates & info_candidates:
            return set(info.get("memory_media_ids") or set())
    return set()


def decode_bank(
    *,
    bank: Path,
    input_root: Path,
    output_root: Path,
    classified_root: Path | None,
    media: dict[str, MediaSemantic],
    banks: dict[str, dict[str, Any]],
    vgmstream: Path,
    log_dir: Path,
    link_mode: str,
    overwrite: bool,
    dry_run: bool,
    workers: int,
) -> tuple[list[ConversionRow], dict[str, Any]]:
    bank_relative = bank.relative_to(input_root).as_posix()
    expected_ids = expected_memory_ids(bank, input_root, banks)
    output_dir = bank_output_directory(
        bank=bank,
        input_root=input_root,
        output_root=output_root,
    )
    bank_summary: dict[str, Any] = {
        "bank": bank_relative,
        "expected_memory_media_count": len(expected_ids),
        "expected_memory_media_ids": sorted(expected_ids),
        "stream_count": 0,
        "decoded_wav_count": 0,
        "valid_wav_count": 0,
        "status": "",
        "message": "",
    }
    try:
        probe = probe_bank(vgmstream, bank, cwd=log_dir)
    except (RuntimeError, ValueError) as exc:
        if expected_ids:
            bank_summary["status"] = "failed_probe"
        else:
            bank_summary["status"] = "skipped_no_decodable_streams"
        bank_summary["message"] = str(exc)
        return [], bank_summary

    stream_count = int(probe["stream_count"])
    bank_summary["stream_count"] = stream_count
    if stream_count <= 0:
        bank_summary["status"] = (
            "failed_zero_streams_with_expected_memory"
            if expected_ids
            else "skipped_no_decodable_streams"
        )
        return [], bank_summary

    if dry_run:
        rows = [
            ConversionRow(
                kind="bank_subsong",
                source=str(bank),
                bank=bank_relative,
                subsong=str(index),
                status="planned",
            )
            for index in range(1, stream_count + 1)
        ]
        bank_summary["status"] = "planned"
        return rows, bank_summary

    output_dir.mkdir(parents=True, exist_ok=True)
    bank_stem = sanitize_component(bank.stem, max_length=64)
    output_template = require_within(
        output_dir / f"{bank_stem}__?s.wav",
        output_root,
        "bank output template",
    )

    existing: dict[int, Path] = {}
    for output in output_dir.glob(f"{bank_stem}__*.wav"):
        match = BANK_SUBSONG_NAME.search(output.name)
        if match:
            existing[int(match.group(1))] = output
    existing_valid = {
        index
        for index, path in existing.items()
        if 1 <= index <= stream_count and validate_wav(path)[0]
    }
    if overwrite or len(existing_valid) != stream_count:
        command = build_bank_decode_command(vgmstream, bank, output_template)
        completed = run_command(command, cwd=log_dir)
        write_text_log(
            log_dir,
            f"bank_{sanitize_component(bank_relative, 64)}.log",
            command,
            completed,
        )
        if completed.returncode != 0:
            bank_summary["status"] = "failed_decode"
            bank_summary["message"] = (
                completed.stderr.strip() or completed.stdout.strip()
            )
    else:
        bank_summary["message"] = "all outputs already valid"

    output_by_index: dict[int, Path] = {}
    for output in output_dir.glob(f"{bank_stem}__*.wav"):
        match = BANK_SUBSONG_NAME.search(output.name)
        if match:
            output_by_index[int(match.group(1))] = output

    def probe_subsong(index: int) -> tuple[int, dict[str, Any] | None, str]:
        try:
            return index, probe_bank(
                vgmstream,
                bank,
                subsong=index,
                cwd=log_dir,
            ), ""
        except (RuntimeError, ValueError) as exc:
            return index, None, str(exc)

    stream_metadata: dict[int, dict[str, Any]] = {}
    metadata_errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(probe_subsong, index)
            for index in range(1, stream_count + 1)
        ]
        for future in as_completed(futures):
            index, payload, error = future.result()
            if payload is not None:
                stream_metadata[index] = payload
            elif error:
                metadata_errors[index] = error

    rows: list[ConversionRow] = []
    for index in range(1, stream_count + 1):
        output = output_by_index.get(index)
        metadata = stream_metadata.get(index) or {}
        media_id = normalize_stream_media_id(str(metadata.get("stream_name") or ""))
        semantic = media.get(media_id)
        row = ConversionRow(
            kind="bank_subsong",
            source=str(bank),
            bank=bank_relative,
            subsong=str(index),
            media_id=media_id,
            semantic_name=semantic.short_name if semantic else "",
            language=semantic.language if semantic else "",
            output=str(output) if output else "",
        )
        if output is None:
            row.status = "failed"
            row.message = "output_missing"
            if index in metadata_errors:
                row.message += f"; metadata_probe: {metadata_errors[index]}"
            rows.append(row)
            continue
        valid, details, validation_message = validate_wav(output)
        if not valid:
            row.status = "failed"
            row.message = validation_message
            rows.append(row)
            continue
        row.status = "converted" if len(existing_valid) != stream_count or overwrite else "existing_valid"
        row.bytes = int(details["bytes"])
        row.frames = int(details["frames"])
        row.sample_rate = int(details["sample_rate"])
        row.channels = int(details["channels"])
        row.duration_seconds = float(details["duration_seconds"])
        if index in metadata_errors:
            row.message = f"metadata_probe: {metadata_errors[index]}"
        try:
            classified, archive_status = archive_classified(
                output, output_root, classified_root, link_mode
            )
            row.classified_output = classified
            row.message = "; ".join(
                part for part in (row.message, archive_status) if part
            )
        except OSError as exc:
            row.status = "failed"
            row.message = f"classification_archive_error: {exc}"
        rows.append(row)

    bank_summary["decoded_wav_count"] = len(output_by_index)
    bank_summary["valid_wav_count"] = sum(
        1 for row in rows if row.status in {"converted", "existing_valid"}
    )
    if bank_summary["valid_wav_count"] == stream_count:
        bank_summary["status"] = "complete"
    elif bank_summary["status"] != "failed_decode":
        bank_summary["status"] = "incomplete"
    if expected_ids and len(expected_ids) != stream_count:
        mismatch = (
            f"SoundbanksInfo memory count {len(expected_ids)} "
            f"!= vgmstream stream count {stream_count}"
        )
        bank_summary["message"] = "; ".join(
            part for part in (bank_summary["message"], mismatch) if part
        )
    return rows, bank_summary


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_indices(
    *,
    index_dir: Path,
    output_dir: Path,
    media: dict[str, MediaSemantic],
    events: list[EventSemantic],
    bank_summaries: list[dict[str, Any]],
    conversions: list[ConversionRow],
    summary: dict[str, Any],
    input_files: list[Path],
    input_root: Path,
) -> None:
    media_rows = []
    for media_id in sorted(media, key=lambda value: (len(value), value)):
        item = media[media_id]
        media_rows.append(
            {
                "media_id": media_id,
                "short_name": item.short_name,
                "wwise_path": item.wwise_path,
                "language": item.language,
                "locations": "|".join(sorted(item.locations)),
                "banks": "|".join(sorted(item.banks)),
                "events": "|".join(sorted(item.events)),
                "event_ids": "|".join(sorted(item.event_ids)),
            }
        )
    write_csv(
        index_dir / "Wwise媒体语义映射.csv",
        media_rows,
        [
            "media_id",
            "short_name",
            "wwise_path",
            "language",
            "locations",
            "banks",
            "events",
            "event_ids",
        ],
    )
    write_csv(
        index_dir / "Wwise事件索引.csv",
        (asdict(row) for row in events),
        list(EventSemantic.__dataclass_fields__),
    )
    bank_rows = []
    for item in bank_summaries:
        row = dict(item)
        row["expected_memory_media_ids"] = "|".join(
            item.get("expected_memory_media_ids") or []
        )
        bank_rows.append(row)
    write_csv(
        index_dir / "Wwise银行索引.csv",
        bank_rows,
        [
            "bank",
            "expected_memory_media_count",
            "stream_count",
            "decoded_wav_count",
            "valid_wav_count",
            "status",
            "message",
            "expected_memory_media_ids",
        ],
    )
    conversion_rows = [asdict(row) for row in conversions]
    write_csv(
        index_dir / "Wwise转换索引.csv",
        conversion_rows,
        list(ConversionRow.__dataclass_fields__),
    )
    failures = [
        row
        for row in conversion_rows
        if row["status"] not in {"converted", "existing_valid", "planned"}
    ]
    with (index_dir / "Wwise失败记录.jsonl").open("w", encoding="utf-8") as handle:
        for row in failures:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (index_dir / "Wwise导出摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    raw_inventory = []
    for path in input_files:
        stat = path.stat()
        raw_inventory.append(
            {
                "relative_path": path.relative_to(input_root).as_posix(),
                "suffix": path.suffix.lower(),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    write_csv(
        output_dir / "源输入清单.csv",
        raw_inventory,
        ["relative_path", "suffix", "bytes", "mtime_ns"],
    )
    (output_dir / "导出清单.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def copy_source_metadata(
    *,
    xml_path: Path | None,
    input_root: Path,
    output_root: Path,
) -> list[str]:
    copied: list[str] = []
    metadata_root = output_root / "源元数据"
    candidates: list[Path] = []
    if xml_path is not None:
        candidates.append(xml_path)
        plugin_info = xml_path.with_name("PluginInfo.xml")
        if plugin_info.is_file():
            candidates.append(plugin_info)
    for source in candidates:
        relative = source.relative_to(input_root)
        target = require_within(metadata_root / relative, output_root, "metadata copy")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all Wwise WEM files and decodable BNK subsongs safely."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--vgmstream", required=True, type=Path)
    parser.add_argument("--classified-dir", type=Path)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--soundbanks-info", type=Path)
    parser.add_argument(
        "--link-mode",
        choices=("hardlink", "copy", "none"),
        default="hardlink",
        help="How decoded WAVs are archived into --classified-dir.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=0,
        help="Limit WEM and BNK inputs independently; 0 exports all.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    input_dir = canonical(args.input_dir)
    output_dir = canonical(args.output_dir)
    index_dir = canonical(args.index_dir)
    classified_dir = canonical(args.classified_dir) if args.classified_dir else None
    log_dir = canonical(args.log_dir) if args.log_dir else index_dir / "logs"
    vgmstream = canonical(args.vgmstream)

    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    if not vgmstream.is_file():
        raise FileNotFoundError(f"vgmstream not found: {vgmstream}")
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.sample_limit < 0:
        raise ValueError("--sample-limit cannot be negative")
    if classified_dir is None and args.link_mode != "none":
        raise ValueError("--classified-dir is required unless --link-mode none")
    validate_roots(input_dir, output_dir, index_dir, classified_dir, log_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    if classified_dir is not None:
        classified_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    before_snapshot = snapshot_tree(input_dir)
    xml_path = discover_xml(input_dir, args.soundbanks_info)
    media: dict[str, MediaSemantic] = {}
    events: list[EventSemantic] = []
    banks: dict[str, dict[str, Any]] = {}
    if xml_path is not None:
        media, events, banks = parse_soundbanks_info(xml_path)

    wem_files = discover_files(input_dir, ".wem")
    bank_files = discover_files(input_dir, ".bnk")
    if args.sample_limit:
        wem_files = wem_files[: args.sample_limit]
        bank_files = bank_files[: args.sample_limit]

    conversions: list[ConversionRow] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                decode_external,
                source=source,
                input_root=input_dir,
                output_root=output_dir,
                classified_root=classified_dir,
                semantic=media.get(source.stem),
                vgmstream=vgmstream,
                log_dir=log_dir,
                link_mode=args.link_mode,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            for source in wem_files
        ]
        for future in as_completed(futures):
            conversions.append(future.result())
    conversions.sort(key=lambda row: (row.kind, row.source, row.subsong))

    bank_summaries: list[dict[str, Any]] = []
    for bank in bank_files:
        rows, bank_summary = decode_bank(
            bank=bank,
            input_root=input_dir,
            output_root=output_dir,
            classified_root=classified_dir,
            media=media,
            banks=banks,
            vgmstream=vgmstream,
            log_dir=log_dir,
            link_mode=args.link_mode,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            workers=args.workers,
        )
        conversions.extend(rows)
        bank_summaries.append(bank_summary)

    copied_metadata = (
        []
        if args.dry_run
        else copy_source_metadata(
            xml_path=xml_path,
            input_root=input_dir,
            output_root=output_dir,
        )
    )
    after_snapshot = snapshot_tree(input_dir)
    source_diff = snapshot_diff(before_snapshot, after_snapshot)
    source_unchanged = not any(source_diff.values())
    successful_statuses = {"converted", "existing_valid"}
    planned_statuses = {"planned"}
    successful = sum(row.status in successful_statuses for row in conversions)
    failures = sum(
        row.status not in successful_statuses | planned_statuses
        for row in conversions
    )
    bank_failures = sum(
        item["status"] in {"failed_probe", "failed_decode", "incomplete", "failed_zero_streams_with_expected_memory"}
        for item in bank_summaries
    )
    summary: dict[str, Any] = {
        "status": (
            "dry_run"
            if args.dry_run
            else (
                "complete"
                if failures == 0 and bank_failures == 0 and source_unchanged
                else "complete_with_concerns"
            )
        ),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "classified_dir": str(classified_dir) if classified_dir else "",
        "index_dir": str(index_dir),
        "log_dir": str(log_dir),
        "vgmstream": str(vgmstream),
        "soundbanks_info": str(xml_path) if xml_path else "",
        "dry_run": bool(args.dry_run),
        "sample_limit": int(args.sample_limit),
        "workers": int(args.workers),
        "link_mode": args.link_mode,
        "input_before": describe_snapshot(before_snapshot),
        "input_after": describe_snapshot(after_snapshot),
        "input_unchanged": source_unchanged,
        "input_diff": source_diff,
        "discovered_wem_count": len(discover_files(input_dir, ".wem")),
        "selected_wem_count": len(wem_files),
        "discovered_bank_count": len(discover_files(input_dir, ".bnk")),
        "selected_bank_count": len(bank_files),
        "xml_media_unique_count": len(media),
        "xml_loose_media_unique_count": sum(
            "Loose" in item.locations for item in media.values()
        ),
        "xml_memory_media_unique_count": sum(
            "Memory" in item.locations for item in media.values()
        ),
        "xml_event_media_rows": len(events),
        "bank_stream_count": sum(
            int(item.get("stream_count") or 0) for item in bank_summaries
        ),
        "planned_or_indexed_audio_count": len(conversions),
        "successful_audio_count": successful,
        "failed_audio_count": failures,
        "bank_failure_count": bank_failures,
        "decoded_wav_bytes": sum(
            row.bytes for row in conversions if row.status in successful_statuses
        ),
        "classified_link_count": sum(
            bool(row.classified_output) for row in conversions
        ),
        "copied_source_metadata": copied_metadata,
        "elapsed_seconds": round(time.time() - started, 3),
        "bank_summaries": bank_summaries,
    }
    all_input_files = sorted(
        (path for path in input_dir.rglob("*") if path.is_file()),
        key=lambda path: str(path).casefold(),
    )
    write_indices(
        index_dir=index_dir,
        output_dir=output_dir,
        media=media,
        events=events,
        bank_summaries=bank_summaries,
        conversions=conversions,
        summary=summary,
        input_files=all_input_files,
        input_root=input_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not source_unchanged:
        return 2
    if not args.dry_run and (failures or bank_failures):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

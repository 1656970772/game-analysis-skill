#!/usr/bin/env python3
"""Classify files from a recovered Godot project without modifying it."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import uuid
import wave
import zlib
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Sequence

from PIL import Image


CHUNK_SIZE = 1024 * 1024


class OrganizerError(ValueError):
    """Raised when inputs or classification configuration are unsafe."""


@dataclass(frozen=True)
class ImageRule:
    rule_id: str
    destination: PurePosixPath
    patterns: tuple[re.Pattern[str], ...]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def canonical_output_root(candidate: Path, study_root: Path, name: str) -> Path:
    """Resolve existing reparse components and keep fixed outputs inside study-root."""
    resolved = candidate.resolve(strict=False)
    if resolved == study_root or not is_within(resolved, study_root):
        raise OrganizerError(f"{name} is outside StudyRoot: {resolved}")
    return resolved


def safe_relative(relative: Path) -> PurePosixPath:
    posix = relative.as_posix()
    pure = PurePosixPath(posix)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise OrganizerError(f"恢复文件路径不安全：{posix}")
    return pure


def safe_destination(value: str) -> PurePosixPath:
    destination = PurePosixPath(value)
    if destination.is_absolute() or any(part in {"", ".", ".."} for part in destination.parts):
        raise OrganizerError(f"分类 destination 不安全：{value!r}")
    allowed = (
        destination.parts[:1] == ("图片",)
        or destination.parts == ("特效", "粒子贴图")
    )
    if not allowed:
        raise OrganizerError(f"图片分类 destination 超出允许目录：{value!r}")
    return destination


def load_config(path: Path) -> tuple[dict, list[ImageRule]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrganizerError(f"无法读取分类配置 {path}: {exc}") from exc
    if config.get("schema_version") != 1:
        raise OrganizerError("Godot 分类配置 schema_version 必须为 1")

    rules: list[ImageRule] = []
    for index, raw in enumerate(config.get("image_rules", [])):
        rule_id = str(raw.get("id") or f"rule_{index}")
        destination = safe_destination(str(raw.get("destination", "")))
        try:
            patterns = tuple(re.compile(value, re.IGNORECASE) for value in raw.get("patterns", []))
        except re.error as exc:
            raise OrganizerError(f"图片分类规则 {rule_id} 的正则无效：{exc}") from exc
        if not patterns:
            raise OrganizerError(f"图片分类规则 {rule_id} 没有 patterns")
        rules.append(ImageRule(rule_id, destination, patterns))
    if not rules:
        raise OrganizerError("Godot 分类配置没有 image_rules")

    safe_destination(str(config.get("default_image_destination", "")))
    return config, rules


def valid_png(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    chunk_index = 0
    saw_ihdr = False
    saw_idat = False
    idat_size = 0
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            return False
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            return False
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            return False
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width, height = struct.unpack_from(">II", chunk_data, 0)
            if width < 1 or height < 1 or chunk_data[10:12] != b"\x00\x00":
                return False
            if chunk_data[12] not in {0, 1}:
                return False
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            return False
        if chunk_type == b"IDAT":
            saw_idat = True
            idat_size += length
        if chunk_type == b"IEND":
            if length != 0 or chunk_end != len(data):
                return False
            saw_iend = True
            break
        offset = chunk_end
        chunk_index += 1
    if not (saw_ihdr and saw_idat and idat_size > 0 and saw_iend):
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                return False
            image.verify()
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                return False
            image.load()
        return True
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return False


def valid_image(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return valid_png(path.read_bytes())
    expected_formats = {
        ".jpg": {"JPEG"},
        ".jpeg": {"JPEG"},
        ".bmp": {"BMP"},
        ".gif": {"GIF"},
        ".webp": {"WEBP"},
        ".tga": {"TGA"},
    }.get(suffix)
    if expected_formats is None:
        return False
    try:
        with Image.open(path) as image:
            if image.format not in expected_formats:
                return False
            image.verify()
        with Image.open(path) as image:
            if image.format not in expected_formats or image.width < 1 or image.height < 1:
                return False
            image.load()
        return True
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return False


def valid_audio(path: Path) -> bool:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                if (
                    handle.getnchannels() < 1
                    or handle.getsampwidth() < 1
                    or handle.getframerate() < 1
                ):
                    return False
                remaining = handle.getnframes()
                while remaining:
                    count = min(remaining, 65_536)
                    if len(handle.readframes(count)) < count * handle.getnchannels() * handle.getsampwidth():
                        return False
                    remaining -= count
                return True
        except (EOFError, OSError, wave.Error):
            return False
    if path.suffix.lower() == ".ogg":
        return valid_ogg(path.read_bytes())
    return False


def ogg_crc(page: bytes) -> int:
    crc = 0
    for value in page:
        crc ^= value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def valid_ogg(data: bytes) -> bool:
    offset = 0
    page_index = 0
    first_packet = bytearray()
    first_packet_complete = False
    saw_eos = False
    while offset < len(data):
        if offset + 27 > len(data) or data[offset : offset + 4] != b"OggS":
            return False
        if data[offset + 4] != 0:
            return False
        header_type = data[offset + 5]
        if page_index == 0 and not (header_type & 0x02):
            return False
        segment_count = data[offset + 26]
        segment_table_end = offset + 27 + segment_count
        if segment_table_end > len(data):
            return False
        segments = data[offset + 27 : segment_table_end]
        body_length = sum(segments)
        page_end = segment_table_end + body_length
        if page_end > len(data):
            return False
        page = bytearray(data[offset:page_end])
        expected_crc = int.from_bytes(page[22:26], "little")
        page[22:26] = b"\0\0\0\0"
        if ogg_crc(page) != expected_crc:
            return False
        if not first_packet_complete:
            cursor = segment_table_end
            for segment_length in segments:
                first_packet.extend(data[cursor : cursor + segment_length])
                cursor += segment_length
                if segment_length < 255:
                    first_packet_complete = True
                    break
        saw_eos = saw_eos or bool(header_type & 0x04)
        offset = page_end
        page_index += 1
    known_header = first_packet.startswith((b"\x01vorbis", b"OpusHead", b"\x80theora"))
    return page_index > 0 and first_packet_complete and known_header and saw_eos


def valid_sfnt(data: bytes, offset: int) -> bool:
    if offset < 0 or offset + 12 > len(data):
        return False
    signature = data[offset : offset + 4]
    if signature not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"}:
        return False
    table_count = struct.unpack_from(">H", data, offset + 4)[0]
    if table_count < 1 or table_count > 4096:
        return False
    directory_end = offset + 12 + (table_count * 16)
    if directory_end > len(data):
        return False
    tags: set[bytes] = set()
    for index in range(table_count):
        record = offset + 12 + (index * 16)
        tag = data[record : record + 4]
        table_offset, table_length = struct.unpack_from(">II", data, record + 8)
        if tag in tags or not all(32 <= value <= 126 for value in tag):
            return False
        tags.add(tag)
        if table_offset > len(data) or table_length > len(data) - table_offset:
            return False
    return True


def valid_binary_fnt(data: bytes) -> bool:
    if len(data) < 4 or data[:3] != b"BMF" or data[3] != 3:
        return False
    offset = 4
    block_types: set[int] = set()
    while offset < len(data):
        if offset + 5 > len(data):
            return False
        block_type = data[offset]
        block_size = struct.unpack_from("<I", data, offset + 1)[0]
        offset += 5
        if block_type not in {1, 2, 3, 4, 5} or block_type in block_types:
            return False
        if block_size > len(data) - offset:
            return False
        block_types.add(block_type)
        offset += block_size
    return {1, 2, 3, 4}.issubset(block_types)


def valid_text_fnt(data: bytes) -> bool:
    try:
        text = data.decode("utf-8", errors="strict").lstrip("\ufeff\r\n\t ")
    except UnicodeDecodeError:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    prefixes = {line.split(maxsplit=1)[0] for line in lines}
    return {"info", "common", "page", "chars"}.issubset(prefixes)


def valid_font(path: Path) -> bool:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".ttf", ".otf"}:
        return valid_sfnt(data, 0)
    if suffix == ".ttc":
        if len(data) < 12 or data[:4] != b"ttcf":
            return False
        font_count = struct.unpack_from(">I", data, 8)[0]
        if font_count < 1 or font_count > 4096 or 12 + (font_count * 4) > len(data):
            return False
        offsets = struct.unpack_from(f">{font_count}I", data, 12)
        return len(set(offsets)) == font_count and all(valid_sfnt(data, offset) for offset in offsets)
    if suffix == ".fnt":
        return valid_binary_fnt(data) if data.startswith(b"BMF") else valid_text_fnt(data)
    return False


def valid_script(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8", errors="strict")
        return True
    except UnicodeDecodeError:
        return False


def choose_image_destination(relative: PurePosixPath, config: dict, rules: list[ImageRule]) -> tuple[PurePosixPath, str]:
    probe = relative.as_posix().lower()
    for rule in rules:
        if any(pattern.search(probe) for pattern in rule.patterns):
            return rule.destination, rule.rule_id
    return safe_destination(str(config["default_image_destination"])), "default_image"


def copy_resumable(source: Path, destination: Path, overwrite: bool) -> tuple[str, str, str]:
    source_hash = sha256(source)
    existed_before = destination.exists()
    if existed_before:
        if not destination.is_file():
            return "conflict", source_hash, "destination_exists_and_is_not_file"
        destination_hash = sha256(destination)
        if destination_hash == source_hash:
            return "unchanged", source_hash, "same_sha256"
        if not overwrite:
            return "conflict", source_hash, f"different_sha256:{destination_hash}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        shutil.copy2(source, temporary)
        if sha256(temporary) != source_hash:
            raise OSError("temporary copy SHA-256 mismatch")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ("overwritten" if existed_before and overwrite else "copied"), source_hash, ""


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "source_relative_path",
        "destination_relative_path",
        "asset_kind",
        "classification_rule",
        "status",
        "size",
        "sha256",
        "detail",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def organize(
    recovery_root: Path,
    study_root: Path,
    config_path: Path,
    overwrite: bool = False,
) -> tuple[dict, list[dict], list[dict]]:
    study_root = study_root.resolve()
    recovery_root = recovery_root.resolve()
    if not study_root.is_dir():
        raise OrganizerError(f"study-root 不存在：{study_root}")
    if not recovery_root.is_dir():
        raise OrganizerError(f"recovery-root 不存在：{recovery_root}")
    if not is_within(recovery_root, study_root) or recovery_root == study_root:
        raise OrganizerError("recovery-root 必须是 study-root 内的独立子目录")

    classified_root = canonical_output_root(
        study_root / "1.分类资源", study_root, "classification root"
    )
    code_root = canonical_output_root(
        study_root / "3.代码" / "脚本", study_root, "code root"
    )
    index_root = canonical_output_root(
        study_root / "4.临时目录" / "中间索引" / "Godot",
        study_root,
        "index root",
    )
    for output_root in (classified_root, code_root, index_root):
        if is_within(output_root, recovery_root):
            raise OrganizerError("recovery-root 不得包含分类、代码或索引输出目录")

    config, rules = load_config(config_path)
    image_extensions = {value.lower() for value in config["image_extensions"]}
    audio_extensions = {value.lower() for value in config["audio_extensions"]}
    font_extensions = {value.lower() for value in config["font_extensions"]}
    script_extensions = {value.lower() for value in config["script_extensions"]}

    rows: list[dict] = []
    failures: list[dict] = []
    files = sorted(
        (path for path in recovery_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(recovery_root).as_posix().lower(),
    )
    for source in files:
        relative_path = source.relative_to(recovery_root)
        relative = safe_relative(relative_path)
        source_relative = relative.as_posix()
        suffix = source.suffix.lower()
        kind = ""
        rule_id = ""
        destination: Path | None = None
        validator = None

        if source.is_symlink():
            failures.append({"stage": "path_validation", "source": source_relative, "error": "symbolic_link_rejected"})
            rows.append({"source_relative_path": source_relative, "status": "failed", "detail": "symbolic_link_rejected"})
            continue
        if suffix in script_extensions:
            kind = "script"
            rule_id = "gdscript_relative_path"
            destination = code_root.joinpath(*relative.parts)
            validator = valid_script
        elif suffix in audio_extensions:
            kind = "audio"
            rule_id = "validated_audio"
            destination = classified_root / "音频" / Path(*relative.parts)
            validator = valid_audio
        elif suffix in font_extensions:
            kind = "font"
            rule_id = "validated_font"
            destination = classified_root / "渲染" / "其他" / "字体" / Path(*relative.parts)
            validator = valid_font
        elif suffix in image_extensions:
            destination_prefix, rule_id = choose_image_destination(relative, config, rules)
            kind = "image"
            destination = classified_root.joinpath(*destination_prefix.parts, *relative.parts)
            validator = valid_image
        else:
            rows.append(
                {
                    "source_relative_path": source_relative,
                    "destination_relative_path": "",
                    "asset_kind": "unclassified",
                    "classification_rule": "unsupported_extension",
                    "status": "unclassified",
                    "size": source.stat().st_size,
                    "sha256": sha256(source),
                    "detail": suffix or "no_extension",
                }
            )
            continue

        assert destination is not None and validator is not None
        resolved_parent = destination.parent.resolve(strict=False)
        expected_root = code_root if kind == "script" else classified_root
        if not is_within(resolved_parent, expected_root.resolve(strict=False)):
            raise OrganizerError(f"分类输出越过允许根目录：{destination}")
        if not validator(source):
            error = f"invalid_{kind}_content"
            failures.append({"stage": "content_validation", "source": source_relative, "error": error})
            rows.append(
                {
                    "source_relative_path": source_relative,
                    "destination_relative_path": destination.relative_to(study_root).as_posix(),
                    "asset_kind": kind,
                    "classification_rule": rule_id,
                    "status": "failed",
                    "size": source.stat().st_size,
                    "sha256": sha256(source),
                    "detail": error,
                }
            )
            continue

        status, source_hash, detail = copy_resumable(source, destination, overwrite)
        if status == "conflict":
            failures.append(
                {
                    "stage": "copy",
                    "source": source_relative,
                    "destination": destination.relative_to(study_root).as_posix(),
                    "error": detail,
                }
            )
        rows.append(
            {
                "source_relative_path": source_relative,
                "destination_relative_path": destination.relative_to(study_root).as_posix(),
                "asset_kind": kind,
                "classification_rule": rule_id,
                "status": status,
                "size": source.stat().st_size,
                "sha256": source_hash,
                "detail": detail,
            }
        )

    status_counts = Counter(row["status"] for row in rows)
    kind_counts = Counter(row.get("asset_kind", "unknown") for row in rows if row["status"] not in {"failed", "unclassified"})
    summary = {
        "schema_version": 1,
        "recovery_root": str(recovery_root),
        "study_root": str(study_root),
        "index_root": str(index_root),
        "classification_config": str(config_path.resolve()),
        "scanned_file_count": len(files),
        "copied_count": status_counts["copied"] + status_counts["overwritten"],
        "unchanged_count": status_counts["unchanged"],
        "conflict_count": status_counts["conflict"],
        "unclassified_count": status_counts["unclassified"],
        "failure_count": len(failures),
        "asset_kind_counts": dict(sorted(kind_counts.items())),
        "spritesheet_boundary": "整张 spritesheet 进入图片/图集，不冒充已切分序列帧。",
        "status": "completed" if not failures else "completed_with_failures",
    }
    return summary, rows, failures


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="组织 GDRETools 恢复的 Godot 项目资源")
    parser.add_argument("--recovery-root", required=True, type=Path)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument(
        "--classification-config",
        type=Path,
        default=root / "references" / "godot-resource-classification.json",
    )
    parser.add_argument("--overwrite", action="store_true", help="显式覆盖内容不同的目标；默认记录冲突")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary, rows, failures = organize(
            args.recovery_root,
            args.study_root,
            args.classification_config,
            overwrite=args.overwrite,
        )
        index_root = Path(summary["index_root"])
        index_root.mkdir(parents=True, exist_ok=True)
        write_csv(index_root / "organized_files.csv", rows)
        (index_root / "organize_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_jsonl(index_root / "organize_failures.jsonl", failures)
    except (OSError, OrganizerError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

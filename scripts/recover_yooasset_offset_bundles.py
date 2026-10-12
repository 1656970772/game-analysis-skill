#!/usr/bin/env python3
"""Recover zero-prefixed YooAsset UnityFS bundles without byte transforms.

This is format normalization, not decryption.  A manifest-marked ``.bundle`` is
copied from its embedded UnityFS signature only when the prefix is entirely
zero and the embedded header is internally consistent.  Every other entry is
reported as blocked and is left untouched.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import struct
import sys
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ENTRY_FIELDS = [
    "package_name",
    "bundle_id",
    "bundle_name",
    "file_hash_expected",
    "source_relative_path",
    "source_size",
    "source_sha256_before",
    "manifest_encrypted",
    "recognized_magic",
    "magic_offset",
    "prefix_all_zero",
    "format_version",
    "unity_version",
    "generator_version",
    "embedded_declared_size",
    "embedded_actual_size",
    "source_embedded_sha256",
    "recovered_relative_path",
    "recovered_size",
    "recovered_sha256",
    "status",
    "block_reason",
]


class RecoveryError(RuntimeError):
    """A configuration or safety failure that prevents the run."""


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RecoveryError(f"JSON root must be an object: {path}")
    return value


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _existing_chain(path: Path) -> list[Path]:
    absolute = Path(os.path.abspath(path))
    chain: list[Path] = []
    cursor = absolute
    while True:
        if cursor.exists() or cursor.is_symlink():
            chain.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    chain.reverse()
    return chain


def _reject_reparse_chain(path: Path, label: str) -> None:
    for component in _existing_chain(path):
        if _is_reparse(component):
            raise RecoveryError(f"{label} contains a reparse point: {component}")


def _canonical(path: Path) -> Path:
    return Path(os.path.realpath(os.path.abspath(path)))


def _overlap(left: Path, right: Path) -> bool:
    try:
        common = Path(os.path.commonpath([_canonical(left), _canonical(right)]))
    except ValueError:
        return False
    return common == _canonical(left) or common == _canonical(right)


def _safe_relative_path(raw: str) -> PurePosixPath:
    if not raw or raw != raw.strip() or "\\" in raw or "\x00" in raw:
        raise ValueError("relative path is empty, non-canonical, or contains a backslash/NUL")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("relative path is absolute or contains an unsafe segment")
    return relative


def _child(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    try:
        if os.path.commonpath([_canonical(root), _canonical(candidate)]) != str(_canonical(root)):
            raise ValueError("resolved path escapes its root")
    except ValueError as exc:
        raise ValueError("resolved path escapes its root") from exc
    return candidate


def _validate_config(config: dict) -> dict:
    if config.get("schema_version") != 1:
        raise RecoveryError("unsupported recovery config schema_version")
    discovery = config.get("discovery")
    header = config.get("unityfs_header")
    safety = config.get("safety")
    outputs = config.get("outputs")
    validation = config.get("validation")
    if not all(isinstance(item, dict) for item in (discovery, header, safety, outputs, validation)):
        raise RecoveryError("recovery config sections are missing or invalid")

    recognized = discovery.get("recognized_magic")
    recoverable = discovery.get("recoverable_magic")
    if not isinstance(recognized, list) or not recognized or not all(
        isinstance(item, str) and item.isascii() and item for item in recognized
    ):
        raise RecoveryError("discovery.recognized_magic must be a non-empty ASCII string list")
    if len(set(recognized)) != len(recognized):
        raise RecoveryError("discovery.recognized_magic contains duplicates")
    if not isinstance(recoverable, list) or not recoverable or not set(recoverable) <= set(recognized):
        raise RecoveryError("recoverable_magic must be a non-empty subset of recognized_magic")
    max_offset = discovery.get("max_magic_offset_bytes")
    min_prefix = discovery.get("minimum_zero_prefix_bytes")
    if not isinstance(max_offset, int) or max_offset < 1:
        raise RecoveryError("max_magic_offset_bytes must be a positive integer")
    if not isinstance(min_prefix, int) or min_prefix < 0 or min_prefix > max_offset:
        raise RecoveryError("minimum_zero_prefix_bytes is outside the scan range")

    versions = header.get("allowed_format_versions")
    if not isinstance(versions, list) or not versions or not all(
        isinstance(item, int) and item > 0 for item in versions
    ):
        raise RecoveryError("allowed_format_versions must be a non-empty positive integer list")
    max_string = header.get("max_version_string_bytes")
    if not isinstance(max_string, int) or max_string < 1:
        raise RecoveryError("max_version_string_bytes must be positive")
    chunk_size = safety.get("copy_chunk_bytes")
    if not isinstance(chunk_size, int) or chunk_size < 4096:
        raise RecoveryError("copy_chunk_bytes must be at least 4096")
    patterns = safety.get("blocked_path_regexes")
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise RecoveryError("blocked_path_regexes must be a string list")
    try:
        compiled_patterns = [re.compile(item) for item in patterns]
    except re.error as exc:
        raise RecoveryError(f"invalid blocked path regex: {exc}") from exc

    for name in ("entry_csv", "summary_json"):
        value = outputs.get(name)
        if not isinstance(value, str) or not value or Path(value).name != value:
            raise RecoveryError(f"outputs.{name} must be a plain filename")
    normalized = dict(config)
    normalized["_compiled_block_patterns"] = compiled_patterns
    return normalized


def _find_magic(data: bytes, magic_values: Iterable[str], max_offset: int) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for magic in magic_values:
        needle = magic.encode("ascii")
        start = 0
        while True:
            offset = data.find(needle, start)
            if offset < 0 or offset > max_offset:
                break
            matches.append((offset, magic))
            start = offset + 1
    return sorted(matches)


def _read_cstring(data: bytes, cursor: int, maximum: int, field: str) -> tuple[str, int]:
    end_limit = min(len(data), cursor + maximum + 1)
    end = data.find(b"\0", cursor, end_limit)
    if end < 0:
        raise ValueError(f"{field} is not NUL-terminated within the configured limit")
    raw = data[cursor:end]
    try:
        value = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field} is not ASCII") from exc
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise ValueError(f"{field} contains non-printable characters")
    return value, end + 1


def _parse_unityfs_header(data: bytes, offset: int, physical_size: int, config: dict) -> dict:
    header_config = config["unityfs_header"]
    max_string = header_config["max_version_string_bytes"]
    signature, cursor = _read_cstring(data, offset, max_string, "signature")
    if signature != "UnityFS":
        raise ValueError("only UnityFS has a configured recoverable header parser")
    if cursor + 4 > len(data):
        raise ValueError("truncated UnityFS format version")
    format_version = struct.unpack_from(">I", data, cursor)[0]
    cursor += 4
    if format_version not in header_config["allowed_format_versions"]:
        raise ValueError(f"UnityFS format version is not allowed: {format_version}")
    unity_version, cursor = _read_cstring(data, cursor, max_string, "unity_version")
    generator_version, cursor = _read_cstring(data, cursor, max_string, "generator_version")
    if header_config.get("require_nonempty_unity_version") and not unity_version:
        raise ValueError("unity_version is empty")
    if header_config.get("require_nonempty_generator_version") and not generator_version:
        raise ValueError("generator_version is empty")
    if cursor + 20 > len(data):
        raise ValueError("truncated UnityFS size/blocks-info header")
    declared_size, compressed_size, uncompressed_size, flags = struct.unpack_from(">QIII", data, cursor)
    cursor += 20
    embedded_size = physical_size - offset
    if declared_size < cursor - offset:
        raise ValueError("embedded declared size is smaller than its parsed header")
    if header_config.get("require_declared_size_equals_embedded_length") and declared_size != embedded_size:
        raise ValueError(
            f"embedded declared size mismatch: declared={declared_size}, actual={embedded_size}"
        )
    if header_config.get("require_positive_blocks_info_sizes") and (
        compressed_size <= 0 or uncompressed_size <= 0
    ):
        raise ValueError("UnityFS blocks-info sizes must be positive")
    if compressed_size > declared_size or uncompressed_size > declared_size:
        raise ValueError("UnityFS blocks-info sizes exceed the embedded bundle size")
    return {
        "format_version": format_version,
        "unity_version": unity_version,
        "generator_version": generator_version,
        "embedded_declared_size": declared_size,
        "embedded_actual_size": embedded_size,
        "compressed_blocks_info_size": compressed_size,
        "uncompressed_blocks_info_size": uncompressed_size,
        "flags": flags,
        "header_size": cursor - offset,
    }


def _hash_file(path: Path, algorithms: Iterable[str], chunk_size: int) -> tuple[int, dict[str, str]]:
    hashers = {name: hashlib.new(name) for name in set(algorithms)}
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            for hasher in hashers.values():
                hasher.update(chunk)
    return size, {name: hasher.hexdigest() for name, hasher in hashers.items()}


def _generation(path: Path) -> tuple[int, int, int, int]:
    info = path.stat(follow_symlinks=False)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _matches_generation(path: Path, expected: tuple[int, int, int, int]) -> bool:
    try:
        return _generation(path) == expected
    except OSError:
        return False


def _unlink_owned(path: Path, expected: tuple[int, int, int, int]) -> None:
    if _matches_generation(path, expected):
        path.unlink()


def _publish_no_replace(partial: Path, target: Path) -> tuple[int, int, int, int]:
    """Atomically add a target name for partial without replacing a late target."""
    generation = _generation(partial)
    published = False
    try:
        try:
            os.link(partial, target, follow_symlinks=False)
            published = True
        except BaseException:
            # Coordinate the rare wrapper-after-syscall case from filesystem facts.
            if _matches_generation(target, generation):
                published = True
            else:
                raise
        partial.unlink()
        if not _matches_generation(target, generation):
            raise RecoveryError(f"published target generation changed: {target}")
        return generation
    except BaseException:
        cleanup_errors: list[str] = []
        for candidate in (target if published else None, partial):
            if candidate is None:
                continue
            try:
                _unlink_owned(candidate, generation)
            except OSError as exc:
                cleanup_errors.append(f"{candidate}: {exc}")
        if cleanup_errors:
            raise RecoveryError(
                "no-replace publication failed and exact-generation cleanup was incomplete: "
                + "; ".join(cleanup_errors)
            )
        raise


def _snapshot_digest(records: list[dict], sha_key: str, size_key: str) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: item["source_relative_path"]):
        payload = {
            "path": record["source_relative_path"],
            "size": int(record[size_key]),
            "sha256": record[sha_key],
        }
        digest.update(
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )
    return digest.hexdigest()


def _copy_embedded(
    source: Path, target: Path, offset: int, expected_size: int, chunk_size: int
) -> tuple[int, str, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(target.parent, "output parent")
    if target.exists() or target.is_symlink():
        raise RecoveryError(f"recovered target already exists: {target}")
    partial = target.parent / f".{target.name}.{uuid.uuid4().hex}.partial"
    source_digest = hashlib.sha256()
    written = 0
    published_generation: tuple[int, int, int, int] | None = None
    try:
        with source.open("rb") as reader, partial.open("xb") as writer:
            reader.seek(offset)
            while True:
                chunk = reader.read(chunk_size)
                if not chunk:
                    break
                writer.write(chunk)
                source_digest.update(chunk)
                written += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if written != expected_size:
            raise RecoveryError(
                f"embedded byte count differs from declared size: expected={expected_size}, actual={written}"
            )
        published_generation = _publish_no_replace(partial, target)
        recovered_size, recovered_hashes = _hash_file(target, ["sha256"], chunk_size)
        if not _matches_generation(target, published_generation):
            raise RecoveryError(f"recovered target changed during verification: {target}")
        if recovered_size != written or recovered_hashes["sha256"] != source_digest.hexdigest():
            raise RecoveryError(f"recovered output digest mismatch: {target}")
        return written, source_digest.hexdigest(), recovered_hashes["sha256"]
    except BaseException:
        cleanup_errors: list[str] = []
        if published_generation is not None:
            try:
                _unlink_owned(target, published_generation)
            except OSError as exc:
                cleanup_errors.append(f"{target}: {exc}")
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{partial}: {exc}")
        if cleanup_errors:
            raise RecoveryError(
                "embedded copy failed and exact-generation cleanup was incomplete: "
                + "; ".join(cleanup_errors)
            )
        raise


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise RecoveryError(f"audit artifact already exists: {path}")
    partial = path.parent / f".{path.name}.{uuid.uuid4().hex}.partial"
    published_generation: tuple[int, int, int, int] | None = None
    try:
        with partial.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        published_generation = _publish_no_replace(partial, path)
        if not _matches_generation(path, published_generation):
            raise RecoveryError(f"audit artifact changed during publication: {path}")
    except BaseException:
        cleanup_errors: list[str] = []
        if published_generation is not None:
            try:
                _unlink_owned(path, published_generation)
            except OSError as exc:
                cleanup_errors.append(f"{path}: {exc}")
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{partial}: {exc}")
        if cleanup_errors:
            raise RecoveryError(
                "audit publication failed and exact-generation cleanup was incomplete: "
                + "; ".join(cleanup_errors)
            )
        raise


def _csv_bytes(rows: list[dict]) -> bytes:
    import io

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=ENTRY_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in ENTRY_FIELDS} for row in rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _partial_files(*roots: Path) -> list[str]:
    found: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.name.endswith(".partial"):
                found.append(str(path))
    return sorted(found)


def _blank_entry(row: dict) -> dict:
    return {
        "package_name": row.get("package_name", ""),
        "bundle_id": row.get("bundle_id", ""),
        "bundle_name": row.get("bundle_name", ""),
        "file_hash_expected": row.get("file_hash_expected", ""),
        "source_relative_path": row.get("resolved_file_relative_path", ""),
        "source_size": "",
        "source_sha256_before": "",
        "manifest_encrypted": row.get("encrypted", ""),
        "recognized_magic": "",
        "magic_offset": "",
        "prefix_all_zero": "",
        "format_version": "",
        "unity_version": "",
        "generator_version": "",
        "embedded_declared_size": "",
        "embedded_actual_size": "",
        "source_embedded_sha256": "",
        "recovered_relative_path": "",
        "recovered_size": "",
        "recovered_sha256": "",
        "status": "blocked",
        "block_reason": "",
    }


def _read_manifest_bundle_rows(bundle_csv: Path, extension: str) -> list[dict]:
    with bundle_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "package_name",
            "bundle_id",
            "bundle_name",
            "bundle_extension",
            "file_hash_expected",
            "file_size_actual",
            "encrypted",
            "resolved_file_relative_path",
            "file_status",
        }
        if reader.fieldnames is None or not required <= set(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or []))
            raise RecoveryError(f"bundle CSV is missing required columns: {missing}")
        rows = [row for row in reader if row["bundle_extension"].casefold() == extension.casefold()]
    if not rows:
        raise RecoveryError(f"bundle CSV contains no manifest rows for extension {extension}")
    return rows


def recover(
    source_root: Path,
    bundle_csv: Path,
    output_root: Path,
    audit_dir: Path,
    config_path: Path,
) -> dict:
    config = _validate_config(_load_json(config_path))
    discovery = config["discovery"]
    safety = config["safety"]
    validation = config["validation"]
    outputs = config["outputs"]
    source_root = Path(os.path.abspath(source_root))
    bundle_csv = Path(os.path.abspath(bundle_csv))
    output_root = Path(os.path.abspath(output_root))
    audit_dir = Path(os.path.abspath(audit_dir))
    config_path = Path(os.path.abspath(config_path))

    if not source_root.is_dir():
        raise RecoveryError(f"source root is not a directory: {source_root}")
    if not bundle_csv.is_file():
        raise RecoveryError(f"bundle CSV is not a file: {bundle_csv}")
    if not config_path.is_file():
        raise RecoveryError(f"config is not a file: {config_path}")
    if safety.get("reject_reparse_points"):
        for path, label in (
            (source_root, "source root"),
            (bundle_csv, "bundle CSV"),
            (config_path, "config"),
            (output_root, "output root"),
            (audit_dir, "audit directory"),
        ):
            _reject_reparse_chain(path, label)
    if safety.get("reject_input_output_overlap"):
        pairs = (
            (source_root, output_root, "source/output"),
            (source_root, audit_dir, "source/audit"),
            (output_root, audit_dir, "output/audit"),
        )
        for left, right, label in pairs:
            if _overlap(left, right):
                raise RecoveryError(f"{label} roots overlap")
    if safety.get("require_fresh_output_root") and (output_root.exists() or output_root.is_symlink()):
        raise RecoveryError(f"output root must be fresh: {output_root}")
    if audit_dir.exists() and not audit_dir.is_dir():
        raise RecoveryError(f"audit path exists but is not a directory: {audit_dir}")
    for filename in (outputs["entry_csv"], outputs["summary_json"]):
        if (audit_dir / filename).exists():
            raise RecoveryError(f"audit output already exists: {audit_dir / filename}")

    rows = _read_manifest_bundle_rows(bundle_csv, discovery["manifest_bundle_extension"])
    seen_paths: set[str] = set()
    entries: list[dict] = []
    eligible: list[tuple[dict, Path, PurePosixPath, dict]] = []
    chunk_size = safety["copy_chunk_bytes"]
    scan_size = discovery["max_magic_offset_bytes"] + max(
        len(item.encode("ascii")) for item in discovery["recognized_magic"]
    )

    for row in rows:
        entry = _blank_entry(row)
        entries.append(entry)
        try:
            if row["file_status"] != "ok":
                raise ValueError(f"manifest audit file_status is not ok: {row['file_status']}")
            relative = _safe_relative_path(row["resolved_file_relative_path"])
            key = relative.as_posix().casefold()
            if key in seen_paths:
                raise ValueError("duplicate manifest physical bundle path")
            seen_paths.add(key)
            if relative.suffix.casefold() != discovery["manifest_bundle_extension"].casefold():
                raise ValueError("resolved path extension does not match manifest bundle extension")
            signal_text = f"{row.get('bundle_name', '')}/{relative.as_posix()}"
            if any(pattern.search(signal_text) for pattern in config["_compiled_block_patterns"]):
                raise ValueError("path contains a configured DRM/anti-cheat/access-control signal")
            source = _child(source_root, relative)
            if not source.is_file():
                raise ValueError("source bundle is missing or not a regular file")
            if safety.get("reject_reparse_points"):
                _reject_reparse_chain(source, "source bundle")
            expected_size = int(row["file_size_actual"])
            hash_algorithm = row.get("file_hash_algorithm", "").strip().lower()
            algorithms = ["sha256"]
            if hash_algorithm:
                try:
                    hashlib.new(hash_algorithm)
                except ValueError as exc:
                    raise ValueError(f"unsupported manifest hash algorithm: {hash_algorithm}") from exc
                algorithms.append(hash_algorithm)
            size, digests = _hash_file(source, algorithms, chunk_size)
            entry["source_size"] = size
            entry["source_sha256_before"] = digests["sha256"]
            if size != expected_size:
                raise ValueError(f"physical size differs from audited CSV: expected={expected_size}, actual={size}")
            expected_hash = row.get("file_hash_expected", "").strip().lower()
            if hash_algorithm and expected_hash and digests[hash_algorithm].lower() != expected_hash:
                raise ValueError("physical hash differs from audited CSV")
            with source.open("rb") as handle:
                head = handle.read(min(size, scan_size))
            matches = _find_magic(
                head, discovery["recognized_magic"], discovery["max_magic_offset_bytes"]
            )
            if len(matches) != 1:
                raise ValueError(f"expected one recognized magic in scan window, found {len(matches)}")
            offset, magic = matches[0]
            entry["recognized_magic"] = magic
            entry["magic_offset"] = offset
            if offset < discovery["minimum_zero_prefix_bytes"]:
                raise ValueError("magic offset is smaller than the configured wrapper minimum")
            prefix_zero = all(value == 0 for value in head[:offset])
            entry["prefix_all_zero"] = str(prefix_zero)
            if not prefix_zero:
                raise ValueError("wrapper prefix contains non-zero bytes")
            if magic not in discovery["recoverable_magic"]:
                raise ValueError(f"recognized magic has no configured header parser: {magic}")
            header = _parse_unityfs_header(head, offset, size, config)
            for field in (
                "format_version",
                "unity_version",
                "generator_version",
                "embedded_declared_size",
                "embedded_actual_size",
            ):
                entry[field] = header[field]
            eligible.append((entry, source, relative, header))
        except (OSError, ValueError) as exc:
            entry["block_reason"] = str(exc)

    output_root.mkdir(parents=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    if safety.get("reject_reparse_points"):
        _reject_reparse_chain(output_root, "created output root")
        _reject_reparse_chain(audit_dir, "created audit directory")

    for entry, source, relative, header in eligible:
        try:
            target = _child(output_root, relative)
            size, source_embedded_sha256, recovered_sha256 = _copy_embedded(
                source,
                target,
                int(entry["magic_offset"]),
                int(header["embedded_declared_size"]),
                chunk_size,
            )
            entry["source_embedded_sha256"] = source_embedded_sha256
            entry["recovered_relative_path"] = relative.as_posix()
            entry["recovered_size"] = size
            entry["recovered_sha256"] = recovered_sha256
            entry["status"] = "recovered"
            entry["block_reason"] = ""
        except (OSError, RecoveryError, ValueError) as exc:
            entry["status"] = "blocked"
            entry["block_reason"] = str(exc)

    source_after_records: list[dict] = []
    for entry in entries:
        if not entry["source_relative_path"] or not entry["source_sha256_before"]:
            continue
        try:
            relative = _safe_relative_path(str(entry["source_relative_path"]))
            source = _child(source_root, relative)
            size, digests = _hash_file(source, ["sha256"], chunk_size)
            source_after_records.append(
                {
                    "source_relative_path": relative.as_posix(),
                    "source_size_after": size,
                    "source_sha256_after": digests["sha256"],
                }
            )
        except (OSError, ValueError):
            source_after_records.append(
                {
                    "source_relative_path": str(entry["source_relative_path"]),
                    "source_size_after": -1,
                    "source_sha256_after": "unavailable",
                }
            )
    before_records = [entry for entry in entries if entry["source_sha256_before"]]
    before_snapshot = _snapshot_digest(before_records, "source_sha256_before", "source_size")
    after_snapshot = _snapshot_digest(
        source_after_records, "source_sha256_after", "source_size_after"
    )
    source_unchanged = before_snapshot == after_snapshot and len(before_records) == len(
        source_after_records
    )
    recovered_entries = [entry for entry in entries if entry["status"] == "recovered"]
    blocked_entries = [entry for entry in entries if entry["status"] != "recovered"]
    offsets = Counter(str(entry["magic_offset"]) for entry in recovered_entries)
    magic_distribution = Counter(entry["recognized_magic"] for entry in recovered_entries)
    format_distribution = Counter(str(entry["format_version"]) for entry in recovered_entries)
    partials_before_audit = _partial_files(output_root, audit_dir)
    checks = {
        "all_manifest_bundles_accounted": len(entries) == len(rows),
        "all_manifest_bundles_recovered": not blocked_entries,
        "source_unchanged_during_recovery": source_unchanged,
        "all_recovered_hashes_match_embedded_suffix": all(
            entry["source_embedded_sha256"] == entry["recovered_sha256"]
            for entry in recovered_entries
        ),
        "all_recovered_sizes_match_embedded_declaration": all(
            int(entry["recovered_size"]) == int(entry["embedded_declared_size"])
            for entry in recovered_entries
        ),
        "no_partial_files": not partials_before_audit,
    }
    if not validation.get("fail_on_blocked_entry"):
        checks["all_manifest_bundles_recovered"] = True
    if not validation.get("fail_on_source_change"):
        checks["source_unchanged_during_recovery"] = True
    if not validation.get("fail_on_partial_files"):
        checks["no_partial_files"] = True
    all_checks_passed = all(checks.values())
    summary = {
        "schema_version": 1,
        "mode": "zero_prefix_offset_wrapper_recovery",
        "inputs": {
            "source_root": str(source_root),
            "bundle_csv": str(bundle_csv),
            "config": str(config_path),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "outputs": {
            "recovered_root": str(output_root),
            "entry_csv": str(audit_dir / outputs["entry_csv"]),
        },
        "boundaries": {
            "source_modified": False,
            "byte_transform_applied": False,
            "key_or_algorithm_used": False,
            "rawfile_processed_as_unityfs": False,
            "manifest_encrypted_is_declaration_only": True,
        },
        "totals": {
            "manifest_bundle_count": len(rows),
            "recovered_count": len(recovered_entries),
            "blocked_count": len(blocked_entries),
            "recovered_bytes": sum(int(entry["recovered_size"]) for entry in recovered_entries),
        },
        "offset_distribution": dict(sorted(offsets.items(), key=lambda item: int(item[0]))),
        "magic_distribution": dict(sorted(magic_distribution.items())),
        "format_version_distribution": dict(
            sorted(format_distribution.items(), key=lambda item: int(item[0]))
        ),
        "block_reason_distribution": dict(
            sorted(Counter(entry["block_reason"] for entry in blocked_entries).items())
        ),
        "source_integrity": {
            "file_count_before": len(before_records),
            "file_count_after": len(source_after_records),
            "snapshot_before_sha256": before_snapshot,
            "snapshot_after_sha256": after_snapshot,
            "unchanged_during_recovery": source_unchanged,
        },
        "validation": {
            "checks": checks,
            "all_checks_passed": all_checks_passed,
            "partial_files": partials_before_audit,
        },
    }
    _atomic_write(audit_dir / outputs["entry_csv"], _csv_bytes(entries))
    _atomic_write(
        audit_dir / outputs["summary_json"],
        (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        ),
    )
    partials_after_audit = _partial_files(output_root, audit_dir)
    if partials_after_audit:
        raise RecoveryError(f"partial files remain after audit publication: {partials_after_audit}")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover strictly validated zero-prefixed YooAsset UnityFS bundles."
    )
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--bundle-csv", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = recover(
            args.source_root,
            args.bundle_csv,
            args.output_root,
            args.audit_dir,
            args.config,
        )
    except (OSError, RecoveryError, json.JSONDecodeError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if summary["validation"]["all_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

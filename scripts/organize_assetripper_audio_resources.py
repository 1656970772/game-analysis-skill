#!/usr/bin/env python3
"""Classify real AssetRipper audio while preserving Assets-relative paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "references" / "assetripper-audio-classification.json"
INDEX_FIELDS = [
    "extension",
    "source_relative_path",
    "classified_relative_path",
    "size",
    "sha256",
    "transfer_method",
    "status",
]


class UnsafePathError(ValueError):
    """Raised when a configured path escapes the study-root or crosses reparse data."""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("configuration root must be an object")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse(path: Path) -> bool:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _assert_no_reparse_components(root: Path, target: Path, field: str) -> None:
    if not _is_relative_to(target, root):
        raise UnsafePathError(f"{field} escapes study-root: {target}")
    current = root
    if _is_reparse(current):
        raise UnsafePathError(f"study-root is a reparse point: {current}")
    for part in target.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise UnsafePathError(f"{field} crosses a reparse point: {current}")


def _resolve_study_path(study_root: Path, value: object, field: str) -> Path:
    raw = Path(str(value))
    if not raw.parts or ".." in raw.parts:
        raise UnsafePathError(f"{field} must not be empty or contain '..': {value!r}")
    candidate = raw if raw.is_absolute() else study_root / raw
    lexical = Path(os.path.abspath(candidate))
    if not _is_relative_to(lexical, study_root):
        raise UnsafePathError(f"{field} must stay under study-root: {value!r}")
    _assert_no_reparse_components(study_root, lexical, field)
    resolved = lexical.resolve(strict=False)
    if not _is_relative_to(resolved, study_root.resolve(strict=True)):
        raise UnsafePathError(f"{field} resolves outside study-root: {value!r}")
    return lexical


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_relative_to(left, right) or _is_relative_to(right, left)


def _build_extensions(config: dict[str, Any]) -> set[str]:
    extensions: set[str] = set()
    for raw_extension in config.get("audio_extensions", []):
        extension = str(raw_extension).strip().lower()
        if (
            not extension.startswith(".")
            or len(extension) < 2
            or "/" in extension
            or "\\" in extension
            or extension in extensions
        ):
            raise ValueError(f"invalid or duplicate audio extension: {raw_extension!r}")
        extensions.add(extension)
    if not extensions:
        raise ValueError("audio_extensions must contain at least one extension")
    return extensions


def _validate_transfer_methods(config: dict[str, Any]) -> list[str]:
    methods = [str(item).lower() for item in config.get("transfer_methods", [])]
    if not methods or any(item not in {"hardlink", "copy"} for item in methods):
        raise ValueError("transfer_methods must contain only hardlink and/or copy")
    if len(set(methods)) != len(methods):
        raise ValueError("transfer_methods must not contain duplicates")
    return methods


def _entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        entry_stat = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return entry.is_symlink() or bool(attributes & reparse_flag)


def _scan_files(root: Path) -> list[Path]:
    """Return regular files without following or accepting any reparse entry."""
    if not root.is_dir():
        raise FileNotFoundError(f"directory not found: {root}")
    pending = [root]
    files: list[Path] = []
    while pending:
        directory = pending.pop()
        _assert_no_reparse_components(root, directory, "scan path")
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
        for entry in ordered:
            entry_path = Path(entry.path)
            if _entry_is_reparse(entry):
                raise UnsafePathError(f"reparse entry is not allowed: {entry_path}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(entry_path)
            elif entry.is_file(follow_symlinks=False):
                files.append(entry_path)
    return sorted(
        files,
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _manifest_sha256(records: Iterable[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        line = (
            f"{record['relative_path']}\0{record['size']}\0{record['sha256']}\n"
        )
        digest.update(line.encode("utf-8"))
    return digest.hexdigest().upper()


def _snapshot(root: Path, files: list[Path]) -> tuple[dict[str, Any], list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    extension_counts: dict[str, int] = {}
    extension_bytes: dict[str, int] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        extension = path.suffix.lower()
        records.append(
            {
                "relative_path": relative,
                "size": size,
                "sha256": _sha256(path),
            }
        )
        extension_counts[extension] = extension_counts.get(extension, 0) + 1
        extension_bytes[extension] = extension_bytes.get(extension, 0) + size
    snapshot: dict[str, Any] = {
        "file_count": len(records),
        "total_bytes": sum(int(record["size"]) for record in records),
        "manifest_sha256": _manifest_sha256(records),
        "extension_counts": dict(sorted(extension_counts.items())),
        "extension_bytes": dict(sorted(extension_bytes.items())),
    }
    return snapshot, records


def _same_content(source: Path, destination: Path, source_hash: str) -> bool:
    return (
        source.stat().st_size == destination.stat().st_size
        and source_hash == _sha256(destination)
    )


def _transfer(source: Path, destination: Path, methods: list[str]) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for method in methods:
        try:
            if method == "hardlink":
                os.link(source, destination)
                return "hardlink"
            if method == "copy":
                shutil.copy2(source, destination)
                return "copy"
            raise ValueError(f"unsupported transfer method: {method}")
        except OSError as exc:
            errors.append(f"{method}: {exc}")
            if destination.exists() and not _is_reparse(destination):
                destination.unlink()
    raise OSError("; ".join(errors))


def _atomic_write_text(path: Path, text: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text, encoding=encoding, newline="")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _write_errors(path: Path, errors: list[dict[str, object]]) -> None:
    payload = "".join(
        json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n" for error in errors
    )
    _atomic_write_text(path, payload, "utf-8")


def classify_audio_resources(
    study_root: Path,
    config_path: Path,
    source_assets: Path | None = None,
    classified_root: Path | None = None,
    index_root: Path | None = None,
) -> dict[str, Any]:
    lexical_study_root = Path(os.path.abspath(study_root))
    if not lexical_study_root.is_dir():
        raise FileNotFoundError(f"study-root not found: {lexical_study_root}")
    if _is_reparse(lexical_study_root):
        raise UnsafePathError(f"study-root is a reparse point: {lexical_study_root}")
    study_root = lexical_study_root.resolve(strict=True)

    config = _read_json(config_path)
    extensions = _build_extensions(config)
    methods = _validate_transfer_methods(config)
    source_root = _resolve_study_path(
        study_root,
        source_assets if source_assets is not None else config["source_assets"],
        "source_assets",
    )
    output_root = _resolve_study_path(
        study_root,
        classified_root if classified_root is not None else config["classified_root"],
        "classified_root",
    )
    output_index_root = _resolve_study_path(
        study_root,
        index_root if index_root is not None else config["index_root"],
        "index_root",
    )
    if _paths_overlap(source_root, output_root):
        raise UnsafePathError("source_assets and classified_root must not overlap")
    if _paths_overlap(source_root, output_index_root):
        raise UnsafePathError("source_assets and index_root must not overlap")
    if _paths_overlap(output_root, output_index_root):
        raise UnsafePathError("classified_root and index_root must not overlap")
    if not source_root.is_dir():
        raise FileNotFoundError(f"AssetRipper Assets directory not found: {source_root}")

    all_source_files = _scan_files(source_root)
    candidates = [path for path in all_source_files if path.suffix.lower() in extensions]
    source_before, source_records = _snapshot(source_root, candidates)
    source_record_map = {
        str(record["relative_path"]): record for record in source_records
    }

    output_root.mkdir(parents=True, exist_ok=True)
    output_index_root.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_components(study_root, output_root, "classified_root")
    _assert_no_reparse_components(study_root, output_index_root, "index_root")

    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    expected_destinations: dict[Path, Path] = {}
    created_count = 0
    reused_count = 0
    conflict_count = 0
    failed_count = 0
    hardlinked_count = 0
    copied_count = 0

    for source in candidates:
        relative = source.relative_to(source_root)
        destination = output_root / relative
        expected_destinations[destination] = source
        source_hash = str(source_record_map[relative.as_posix()]["sha256"])
        transfer_method = ""
        status = ""
        try:
            _assert_no_reparse_components(output_root, destination, "destination")
            if destination.exists():
                if _is_reparse(destination):
                    raise UnsafePathError(
                        f"destination is a reparse point: {destination}"
                    )
                if _same_content(source, destination, source_hash):
                    reused_count += 1
                    status = "reused"
                    try:
                        transfer_method = (
                            "hardlink"
                            if os.path.samefile(source, destination)
                            else "copy"
                        )
                    except OSError:
                        transfer_method = "copy"
                else:
                    conflict_count += 1
                    status = "conflict"
                    transfer_method = "existing_mismatch"
                    errors.append(
                        {
                            "code": "destination_conflict",
                            "source_relative_path": relative.as_posix(),
                            "classified_relative_path": destination.relative_to(
                                study_root
                            ).as_posix(),
                            "message": "existing destination has different content; not overwritten",
                        }
                    )
            else:
                _assert_no_reparse_components(
                    output_root, destination.parent, "destination parent"
                )
                transfer_method = _transfer(source, destination, methods)
                created_count += 1
                status = "created"
        except Exception as exc:
            failed_count += 1
            status = "failed"
            transfer_method = ""
            errors.append(
                {
                    "code": "transfer_failed",
                    "source_relative_path": relative.as_posix(),
                    "classified_relative_path": destination.relative_to(
                        study_root
                    ).as_posix(),
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )

        if transfer_method == "hardlink":
            hardlinked_count += 1
        elif transfer_method == "copy":
            copied_count += 1
        rows.append(
            {
                "extension": source.suffix.lower(),
                "source_relative_path": relative.as_posix(),
                "classified_relative_path": destination.relative_to(
                    study_root
                ).as_posix(),
                "size": source.stat().st_size,
                "sha256": source_hash,
                "transfer_method": transfer_method,
                "status": status,
            }
        )

    all_source_files_after = _scan_files(source_root)
    candidates_after = [
        path for path in all_source_files_after if path.suffix.lower() in extensions
    ]
    source_after, _ = _snapshot(source_root, candidates_after)
    source_unchanged = source_before == source_after
    if not source_unchanged:
        errors.append(
            {
                "code": "source_changed",
                "message": "AssetRipper audio candidate snapshot changed during classification",
            }
        )

    actual_files = _scan_files(output_root)
    actual_snapshot, _ = _snapshot(output_root, actual_files)
    missing: list[str] = []
    for destination, source in expected_destinations.items():
        relative = source.relative_to(source_root).as_posix()
        source_hash = str(source_record_map[relative]["sha256"])
        if (
            not destination.is_file()
            or _is_reparse(destination)
            or not _same_content(source, destination, source_hash)
        ):
            missing.append(destination.relative_to(study_root).as_posix())
    extra = [
        path.relative_to(study_root).as_posix()
        for path in actual_files
        if path not in expected_destinations
    ]
    impure = [
        path.relative_to(study_root).as_posix()
        for path in actual_files
        if path.suffix.lower() not in extensions
    ]
    for relative in extra:
        errors.append(
            {
                "code": "unexpected_classified_file",
                "classified_relative_path": relative,
                "message": "file is not mapped from an AssetRipper audio candidate",
            }
        )
    reconciliation = {
        "file_count_match": source_before["file_count"]
        == actual_snapshot["file_count"],
        "total_bytes_match": source_before["total_bytes"]
        == actual_snapshot["total_bytes"],
        "manifest_sha256_match": source_before["manifest_sha256"]
        == actual_snapshot["manifest_sha256"],
    }

    summary: dict[str, Any] = {
        "schema_version": 1,
        "ok": source_unchanged
        and all(reconciliation.values())
        and not missing
        and not extra
        and not impure
        and conflict_count == 0
        and failed_count == 0,
        "study_root": str(study_root),
        "config_path": str(config_path.resolve()),
        "source_root": str(source_root),
        "classified_root": str(output_root),
        "index_root": str(output_index_root),
        "allowed_extensions": sorted(extensions),
        "source_candidate_count": len(candidates),
        "source_candidate_bytes": source_before["total_bytes"],
        "classified_file_count": len(actual_files),
        "classified_total_bytes": actual_snapshot["total_bytes"],
        "created_count": created_count,
        "reused_count": reused_count,
        "hardlinked_count": hardlinked_count,
        "copied_count": copied_count,
        "conflict_count": conflict_count,
        "failed_count": failed_count,
        "missing_count": len(missing),
        "extra_count": len(extra),
        "impure_count": len(impure),
        "error_count": len(errors),
        "source_snapshot_before": source_before,
        "source_snapshot_after": source_after,
        "source_unchanged": source_unchanged,
        "classified_snapshot": actual_snapshot,
        "reconciliation": reconciliation,
        "missing": missing,
        "extra": extra,
        "impure": impure,
    }
    _write_csv(output_index_root / "音频分类索引.csv", rows)
    _write_errors(output_index_root / "音频分类错误.jsonl", errors)
    _atomic_write_text(
        output_index_root / "音频分类摘要.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify real AssetRipper audio with relative paths and hash reconciliation."
    )
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--source-assets",
        type=Path,
        help="Override config source_assets; absolute values must remain under study-root.",
    )
    parser.add_argument(
        "--classified-root",
        type=Path,
        help="Override config classified_root; absolute values must remain under study-root.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        help="Override config index_root; absolute values must remain under study-root.",
    )
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        summary = classify_audio_resources(
            study_root=args.study_root,
            config_path=args.config,
            source_assets=args.source_assets,
            classified_root=args.classified_root,
            index_root=args.index_root,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

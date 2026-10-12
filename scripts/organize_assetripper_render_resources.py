#!/usr/bin/env python3
"""Classify AssetRipper render resources while preserving Assets-relative paths."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "references" / "assetripper-render-classification.json"
INDEX_FIELDS = [
    "category",
    "extension",
    "source_relative_path",
    "classified_relative_path",
    "size",
    "sha256",
    "transfer_method",
    "status",
]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_relative_path(value: object, field: str) -> Path:
    path = Path(str(value))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{field} must be a safe relative path: {value!r}")
    return path


def _build_extension_map(config: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for category in config.get("categories", []):
        name_path = _safe_relative_path(category.get("name"), "categories[].name")
        if len(name_path.parts) != 1:
            raise ValueError("categories[].name must be one directory name")
        name = name_path.name
        for raw_extension in category.get("extensions", []):
            extension = str(raw_extension).lower()
            if not extension.startswith(".") or extension in result:
                raise ValueError(f"invalid or duplicate render extension: {raw_extension!r}")
            result[extension] = name
    if not result:
        raise ValueError("categories must declare at least one extension")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _same_content(source: Path, destination: Path) -> tuple[bool, str]:
    if source.stat().st_size != destination.stat().st_size:
        return False, ""
    source_hash = _sha256(source)
    return source_hash == _sha256(destination), source_hash


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
            if destination.exists():
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


def classify_render_resources(study_root: Path, config_path: Path) -> dict[str, Any]:
    study_root = study_root.resolve()
    config = _read_json(config_path)
    extension_map = _build_extension_map(config)
    source_root = study_root / _safe_relative_path(config["source_assets"], "source_assets")
    classified_root = study_root / _safe_relative_path(
        config["classified_root"], "classified_root"
    )
    index_root = study_root / _safe_relative_path(config["index_root"], "index_root")
    methods = [str(item).lower() for item in config.get("transfer_methods", [])]
    if not methods or any(item not in {"hardlink", "copy"} for item in methods):
        raise ValueError("transfer_methods must contain hardlink and/or copy")
    if not source_root.is_dir():
        raise FileNotFoundError(f"AssetRipper Assets directory not found: {source_root}")

    candidates = sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() in extension_map
        ),
        key=lambda path: path.relative_to(source_root).as_posix().lower(),
    )
    rows: list[dict[str, object]] = []
    created_count = 0
    reused_count = 0
    conflict_count = 0
    hardlinked_count = 0
    copied_count = 0
    expected_destinations: dict[Path, Path] = {}
    source_counts = {category["name"]: 0 for category in config["categories"]}

    for source in candidates:
        relative = source.relative_to(source_root)
        extension = source.suffix.lower()
        category = extension_map[extension]
        destination = classified_root / category / relative
        expected_destinations[destination] = source
        source_counts[category] += 1

        transfer_method = ""
        status = ""
        source_hash = ""
        if destination.exists():
            same, source_hash = _same_content(source, destination)
            if same:
                reused_count += 1
                status = "reused"
                try:
                    transfer_method = (
                        "hardlink" if os.path.samefile(source, destination) else "copy"
                    )
                except OSError:
                    transfer_method = "copy"
            else:
                conflict_count += 1
                status = "conflict"
                transfer_method = "existing_mismatch"
        else:
            transfer_method = _transfer(source, destination, methods)
            created_count += 1
            status = "created"
            source_hash = _sha256(source)

        if transfer_method == "hardlink":
            hardlinked_count += 1
        elif transfer_method == "copy":
            copied_count += 1
        rows.append(
            {
                "category": category,
                "extension": extension,
                "source_relative_path": relative.as_posix(),
                "classified_relative_path": destination.relative_to(study_root).as_posix(),
                "size": source.stat().st_size,
                "sha256": source_hash,
                "transfer_method": transfer_method,
                "status": status,
            }
        )

    actual_files = (
        sorted(path for path in classified_root.rglob("*") if path.is_file())
        if classified_root.is_dir()
        else []
    )
    missing = [
        destination.relative_to(study_root).as_posix()
        for destination, source in expected_destinations.items()
        if not destination.is_file() or not _same_content(source, destination)[0]
    ]
    extra = [
        path.relative_to(study_root).as_posix()
        for path in actual_files
        if path not in expected_destinations
    ]
    classified_counts = {category["name"]: 0 for category in config["categories"]}
    for path in actual_files:
        relative = path.relative_to(classified_root)
        if relative.parts and relative.parts[0] in classified_counts:
            classified_counts[relative.parts[0]] += 1

    summary: dict[str, Any] = {
        "schema_version": 1,
        "ok": not missing
        and not extra
        and conflict_count == 0
        and len(candidates) == len(actual_files),
        "study_root": str(study_root),
        "config_path": str(config_path.resolve()),
        "source_root": str(source_root),
        "classified_root": str(classified_root),
        "index_root": str(index_root),
        "source_candidate_count": len(candidates),
        "classified_file_count": len(actual_files),
        "created_count": created_count,
        "reused_count": reused_count,
        "hardlinked_count": hardlinked_count,
        "copied_count": copied_count,
        "missing_count": len(missing),
        "extra_count": len(extra),
        "duplicate_count": 0,
        "conflict_count": conflict_count,
        "source_counts": source_counts,
        "classified_counts": classified_counts,
        "missing": missing,
        "extra": extra,
    }
    _write_csv(index_root / "渲染分类索引.csv", rows)
    _atomic_write_text(
        index_root / "渲染分类摘要.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify real AssetRipper render resources with relative paths."
    )
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        summary = classify_render_resources(args.study_root, args.config)
    except Exception as exc:  # machine-readable failure boundary
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

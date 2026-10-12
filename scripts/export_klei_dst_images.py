#!/usr/bin/env python3
"""Safely supplement Klei/DST images from multiple read-only sources.

The source manifest keeps game and Mod roots explicit.  Loose assets and safe
ZIP members are copied into ``0.原始导出/KleiDST`` before any conversion.
Only verified PNG files enter the classified image directories; XML atlas
metadata remains in the raw export and the audit index.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image


INDEX_BASENAME = "klei_dst_image_export_index"
TERRAIN_INDEX_BASENAME = "klei_dst_terrain_image_index"
INDEX_FIELDS = [
    "source_id",
    "source_kind",
    "source_display_name",
    "output_prefix",
    "terrain_prefix",
    "origin_kind",
    "archive",
    "archive_chain",
    "member",
    "source_relative",
    "source",
    "raw_output",
    "output",
    "sha256",
    "output_sha256",
    "width",
    "height",
    "terrain_confidence",
    "terrain_reason",
    "terrain_category",
    "terrain_output",
    "terrain_status",
    "raw_status",
    "status",
    "message",
]
CONFIDENCE_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


class ManifestError(ValueError):
    """Raised when a source manifest or rules config violates the contract."""


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    root: Path
    output_prefix: str
    prefix_parts: tuple[str, ...]
    terrain_prefix: str
    terrain_prefix_parts: tuple[str, ...]
    kind: str
    display_name: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative_parts(value: str, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized:
        if allow_empty:
            return ()
        raise ManifestError(f"{label} must not be empty")
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise ManifestError(f"{label} must be a relative path: {value!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in parts):
        raise ManifestError(f"unsafe {label}: {value!r}")
    return tuple(parts)


def _safe_member_parts(value: str) -> tuple[str, ...] | None:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    parts = normalized.split("/")
    if not parts or any(part in {"", ".", ".."} or ":" in part or "\x00" in part for part in parts):
        return None
    return tuple(parts)


def _terrain_prefix_parts(value: str) -> tuple[str, ...]:
    if value and not value.replace("\\", "/").strip("/"):
        raise ManifestError(f"unsafe terrain_prefix: {value!r}")
    parts = _relative_parts(value, "terrain_prefix", allow_empty=True)
    reserved = re.compile(
        r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
    )
    invalid_characters = set('<>:"|?*')
    for part in parts:
        if (
            any(character in invalid_characters for character in part)
            or any(unicodedata.category(character) == "Cc" for character in part)
            or part.endswith((" ", "."))
            or reserved.match(part)
        ):
            raise ManifestError(f"unsafe terrain_prefix: {value!r}")
    return parts


def _without_suffix(parts: tuple[str, ...]) -> tuple[str, ...]:
    if not parts:
        return parts
    return (*parts[:-1], PurePosixPath(parts[-1]).stem)


def _posix(parts: Iterable[str]) -> str:
    return "/".join(parts)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _atomic_temp_path(destination: Path) -> Path:
    return destination.parent / f".{destination.stem}.{uuid.uuid4().hex}.tmp{destination.suffix}"


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _atomic_temp_path(destination)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _atomic_temp_path(destination)
    try:
        shutil.copyfile(source, temporary)
        actual = sha256_file(temporary)
        if actual != expected_sha256:
            raise OSError("source changed while it was being copied")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_png(path: Path) -> tuple[bool, int, int, str]:
    if not path.is_file():
        return False, 0, 0, "PNG output is missing"
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                return False, 0, 0, f"expected PNG, got {image.format or 'unknown'}"
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
        if width <= 0 or height <= 0:
            return False, 0, 0, "PNG has invalid dimensions"
        return True, width, height, ""
    except Exception as exc:  # Pillow exposes several format-specific exceptions.
        return False, 0, 0, f"invalid PNG: {exc}"


def run_stex(stex: Path, source: Path, output: Path) -> tuple[int, str]:
    """Run Stex once.  Kept as a small seam for deterministic tests."""

    try:
        process = subprocess.run(
            [str(stex), "decompress", "-i", str(source), "-o", str(output)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        return process.returncode, process.stdout
    except Exception as exc:
        return -1, str(exc)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"{label} root must be an object: {path}")
    return payload


def load_sources(manifest_path: Path, study_root: Path) -> list[SourceSpec]:
    manifest_path = manifest_path.resolve(strict=True)
    payload = _load_json(manifest_path, "sources manifest")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ManifestError("sources manifest must contain a non-empty sources array")

    sources: list[SourceSpec] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_sources):
        if not isinstance(item, dict):
            raise ManifestError(f"sources[{index}] must be an object")
        required = (
            "id",
            "root",
            "output_prefix",
            "terrain_prefix",
            "kind",
            "display_name",
        )
        missing = [key for key in required if key not in item]
        if missing:
            raise ManifestError(f"sources[{index}] missing fields: {', '.join(missing)}")

        source_id = str(item["id"]).strip()
        if not source_id or source_id in seen_ids:
            raise ManifestError(f"source id must be non-empty and unique: {source_id!r}")
        seen_ids.add(source_id)

        root_value = Path(str(item["root"]))
        if not root_value.is_absolute():
            root_value = manifest_path.parent / root_value
        try:
            root = root_value.resolve(strict=True)
        except OSError as exc:
            raise ManifestError(f"source root does not exist for {source_id}: {root_value}") from exc
        if not root.is_dir():
            raise ManifestError(f"source root is not a directory for {source_id}: {root}")
        if root == study_root or _is_within(study_root, root) or _is_within(root, study_root):
            raise ManifestError(f"source/study overlap is not allowed for {source_id}: {root} <> {study_root}")

        output_prefix = str(item["output_prefix"])
        prefix_parts = _relative_parts(output_prefix, "output_prefix", allow_empty=True)
        terrain_prefix = str(item["terrain_prefix"])
        terrain_prefix_parts = _terrain_prefix_parts(terrain_prefix)
        sources.append(
            SourceSpec(
                source_id=source_id,
                root=root,
                output_prefix=_posix(prefix_parts),
                prefix_parts=prefix_parts,
                terrain_prefix=_posix(terrain_prefix_parts),
                terrain_prefix_parts=terrain_prefix_parts,
                kind=str(item["kind"]).strip() or "unknown",
                display_name=str(item["display_name"]).strip() or source_id,
            )
        )
    return sources


class ImageExporter:
    def __init__(self, study_root: Path, skill_root: Path, config: dict[str, Any], force: bool) -> None:
        self.study_root = study_root.resolve()
        self.skill_root = skill_root.resolve(strict=True)
        self.config = config
        self.force = force
        self.rows: list[dict[str, Any]] = []
        self.claims: dict[str, str] = {}
        self.previous_owners: dict[str, str] = {}

        for name in ("0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"):
            (self.study_root / name).mkdir(parents=True, exist_ok=True)

        scan = config.get("scan")
        paths = config.get("paths")
        tools = config.get("tools")
        terrain = config.get("terrain")
        if not all(isinstance(value, dict) for value in (scan, paths, tools, terrain)):
            raise ManifestError("config must contain scan, paths, tools, and terrain objects")

        self.texture_extensions = self._extensions(scan, "texture_extensions")
        self.direct_extensions = self._extensions(scan, "direct_image_extensions")
        self.metadata_extensions = self._extensions(scan, "atlas_metadata_extensions")
        self.archive_extensions = self._extensions(scan, "archive_extensions")
        self.max_nested_depth = self._positive_int(scan, "max_nested_archive_depth")
        self.max_loose_bytes = self._positive_int(scan, "max_loose_file_bytes")
        self.max_member_bytes = self._positive_int(scan, "max_member_uncompressed_bytes")
        self.max_archive_selected_bytes = self._positive_int(scan, "max_archive_selected_uncompressed_bytes")
        self.max_compression_ratio = float(scan.get("max_compression_ratio", 0))
        if self.max_compression_ratio <= 0:
            raise ManifestError("scan.max_compression_ratio must be positive")

        self.raw_root = self._study_path(paths, "raw_root")
        self.raw_packages_name = self._path_parts(paths, "raw_packages")
        self.raw_extracted_name = self._path_parts(paths, "raw_packages_extracted")
        self.raw_textures_name = self._path_parts(paths, "raw_textures")
        self.raw_direct_name = self._path_parts(paths, "raw_direct_images")
        self.raw_metadata_name = self._path_parts(paths, "raw_atlas_metadata")
        self.classified_root = self._study_path(paths, "classified_images")
        self.terrain_root = self._study_path(paths, "classified_terrain")
        self.index_root = self._study_path(paths, "index_root")
        for path in (self.raw_root, self.classified_root, self.terrain_root, self.index_root):
            path.mkdir(parents=True, exist_ok=True)

        stex_parts = _relative_parts(str(tools.get("stex_relative_path", "")), "tools.stex_relative_path", allow_empty=False)
        self.stex = self.skill_root.joinpath(*stex_parts)

        minimum = str(terrain.get("archive_minimum_confidence", "high")).lower()
        if minimum not in CONFIDENCE_RANK:
            raise ManifestError(f"unknown terrain confidence: {minimum}")
        self.terrain_minimum_rank = CONFIDENCE_RANK[minimum]
        rules = terrain.get("rules")
        if not isinstance(rules, list):
            raise ManifestError("terrain.rules must be an array")
        self.terrain_rules: list[
            tuple[int, str, str, str, re.Pattern[str]]
        ] = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise ManifestError("each terrain rule must be an object")
            confidence = str(rule.get("confidence", "none")).lower()
            if confidence not in CONFIDENCE_RANK:
                raise ManifestError(f"unknown terrain rule confidence: {confidence}")
            try:
                pattern = re.compile(str(rule["path_regex"]), re.IGNORECASE)
            except (KeyError, re.error) as exc:
                raise ManifestError(f"invalid terrain rule: {rule}") from exc
            category_parts = _relative_parts(
                str(rule.get("category", "")),
                f"terrain rule {rule.get('id', 'unnamed')} category",
                allow_empty=False,
            )
            if len(category_parts) != 1:
                raise ManifestError(
                    f"terrain rule category must be one path component: {rule}"
                )
            category = category_parts[0]
            reason = f"{rule.get('id', 'unnamed')}: {rule.get('reason', '')}".strip()
            self.terrain_rules.append(
                (CONFIDENCE_RANK[confidence], confidence, reason, category, pattern)
            )
        self._load_previous_owners()

    @staticmethod
    def _extensions(scan: dict[str, Any], key: str) -> set[str]:
        values = scan.get(key)
        if not isinstance(values, list) or not values:
            raise ManifestError(f"scan.{key} must be a non-empty array")
        result = {str(value).lower() for value in values}
        if any(not value.startswith(".") or "/" in value or "\\" in value for value in result):
            raise ManifestError(f"scan.{key} contains an invalid extension")
        return result

    @staticmethod
    def _positive_int(container: dict[str, Any], key: str) -> int:
        value = container.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ManifestError(f"scan.{key} must be a positive integer")
        return value

    @staticmethod
    def _path_parts(paths: dict[str, Any], key: str) -> tuple[str, ...]:
        if key not in paths:
            raise ManifestError(f"config path is missing: {key}")
        return _relative_parts(str(paths[key]), f"paths.{key}", allow_empty=False)

    def _study_path(self, paths: dict[str, Any], key: str) -> Path:
        destination = self.study_root.joinpath(*self._path_parts(paths, key)).resolve()
        if not _is_within(destination, self.study_root):
            raise ManifestError(f"configured destination leaves study-root: {key}")
        return destination

    def _load_previous_owners(self) -> None:
        path = self.index_root / f"{INDEX_BASENAME}.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = payload.get("rows", []) if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            return
        for row in rows:
            if not isinstance(row, dict) or row.get("status") in {"conflict", "unsafe_archive_member", "archive_error"}:
                continue
            identity = self._row_identity(row)
            for field in ("raw_output", "output", "terrain_output"):
                value = row.get(field)
                if not value:
                    continue
                candidate = Path(str(value)).resolve()
                if candidate.exists() and _is_within(candidate, self.study_root):
                    self.previous_owners[str(candidate).casefold()] = identity

    @staticmethod
    def _row_identity(row: dict[str, Any]) -> str:
        fields = ("source_id", "origin_kind", "source_relative", "archive_chain", "member")
        return "|".join(str(row.get(field, "")) for field in fields)

    def _claim(self, destination: Path, identity: str) -> str | None:
        resolved = destination.resolve()
        if not _is_within(resolved, self.study_root):
            raise ManifestError(f"destination leaves study-root: {destination}")
        key = str(resolved).casefold()
        owner = self.claims.get(key) or self.previous_owners.get(key)
        if owner is not None and owner != identity:
            return owner
        self.claims[key] = identity
        return None

    @staticmethod
    def _base_row(spec: SourceSpec, **values: Any) -> dict[str, Any]:
        row: dict[str, Any] = {field: "" for field in INDEX_FIELDS}
        row.update(
            {
                "source_id": spec.source_id,
                "source_kind": spec.kind,
                "source_display_name": spec.display_name,
                "output_prefix": spec.output_prefix,
                "terrain_prefix": spec.terrain_prefix,
            }
        )
        row.update(values)
        return row

    @staticmethod
    def _message_tail(message: str) -> str:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def _write_raw_file(self, source: Path, destination: Path) -> tuple[str, bool, str]:
        source_sha = sha256_file(source)
        old_sha = sha256_file(destination) if destination.is_file() else None
        if destination.exists() and not destination.is_file():
            raise OSError(f"raw destination is not a file: {destination}")
        changed = old_sha != source_sha
        if self.force or old_sha != source_sha:
            _atomic_copy(source, destination, source_sha)
            status = "raw_updated" if old_sha is not None else "raw_copied"
        else:
            status = "raw_up_to_date"
        return source_sha, changed, status

    def _write_raw_bytes(self, payload: bytes, destination: Path) -> tuple[str, bool, str]:
        source_sha = sha256_bytes(payload)
        old_sha = sha256_file(destination) if destination.is_file() else None
        if destination.exists() and not destination.is_file():
            raise OSError(f"raw destination is not a file: {destination}")
        changed = old_sha != source_sha
        if self.force or old_sha != source_sha:
            _atomic_write_bytes(destination, payload)
            status = "raw_updated" if old_sha is not None else "raw_copied"
        else:
            status = "raw_up_to_date"
        return source_sha, changed, status

    def _convert_tex(self, raw: Path, output: Path, raw_changed: bool) -> tuple[str, int, int, str]:
        valid_before, width, height, _ = validate_png(output)
        if valid_before and not raw_changed and not self.force:
            return "up_to_date", width, height, ""

        existed_before = output.exists()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = _atomic_temp_path(output)
        try:
            code, log = run_stex(self.stex, raw, temporary)
            valid, width, height, validation_error = validate_png(temporary)
            if code != 0 or not valid:
                output.unlink(missing_ok=True)
                message = self._message_tail(log)
                if validation_error:
                    message = f"{message}; {validation_error}".strip("; ")
                return "failed", 0, 0, message or f"Stex exit code {code}"
            os.replace(temporary, output)
            status = "updated" if existed_before else "converted"
            return status, width, height, self._message_tail(log)
        except Exception as exc:
            if output.is_file():
                output.unlink(missing_ok=True)
            return "failed", 0, 0, str(exc)
        finally:
            temporary.unlink(missing_ok=True)

    def _convert_direct(self, raw: Path, output: Path, raw_changed: bool) -> tuple[str, int, int, str]:
        valid_before, width, height, _ = validate_png(output)
        if valid_before and not raw_changed and not self.force:
            return "up_to_date", width, height, ""

        existed_before = output.exists()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = _atomic_temp_path(output)
        try:
            with Image.open(raw) as image:
                image.verify()
            with Image.open(raw) as image:
                image.load()
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                normalized = image.convert("RGBA" if has_alpha else "RGB")
                normalized.save(temporary, format="PNG")
            valid, width, height, validation_error = validate_png(temporary)
            if not valid:
                output.unlink(missing_ok=True)
                return "failed", 0, 0, validation_error
            os.replace(temporary, output)
            status = "updated" if existed_before else "converted"
            return status, width, height, ""
        except Exception as exc:
            if output.is_file():
                output.unlink(missing_ok=True)
            return "failed", 0, 0, f"direct image validation failed: {exc}"
        finally:
            temporary.unlink(missing_ok=True)

    def _terrain_match_details(self, logical_path: str) -> tuple[str, str, str]:
        best_rank = 0
        best_confidence = ""
        best_reason = ""
        best_category = ""
        for rank, confidence, reason, category, pattern in self.terrain_rules:
            if pattern.search(logical_path) and rank > best_rank:
                best_rank = rank
                best_confidence = confidence
                best_reason = reason
                best_category = category
        return best_confidence, best_reason, best_category

    def _terrain_match(self, logical_path: str) -> tuple[str, str]:
        confidence, reason, _ = self._terrain_match_details(logical_path)
        return confidence, reason

    def _archive_terrain(
        self,
        output: Path,
        identity: str,
        terrain_prefix_parts: tuple[str, ...],
        category: str,
    ) -> tuple[str, str, str]:
        destination = self.terrain_root.joinpath(
            category, *terrain_prefix_parts, output.name
        )
        owner = self._claim(destination, identity)
        if owner:
            return str(destination), "conflict", f"terrain destination already owned by {owner}"
        temporary: Path | None = None
        try:
            output_sha = sha256_file(output)
            valid, _, _, _ = validate_png(destination)
            if valid and sha256_file(destination) == output_sha and not self.force:
                return str(destination), "up_to_date", ""
            if destination.exists() and not destination.is_file():
                raise OSError(f"terrain destination is not a file: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = _atomic_temp_path(destination)
            method = "hardlink"
            try:
                os.link(output, temporary)
            except OSError:
                temporary.unlink(missing_ok=True)
                shutil.copyfile(output, temporary)
                method = "copy"
            os.replace(temporary, destination)
            valid, _, _, validation_error = validate_png(destination)
            if not valid:
                destination.unlink(missing_ok=True)
                raise OSError(validation_error)
            return str(destination), method, ""
        except Exception as exc:
            return str(destination), "failed", str(exc)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _finish_image(
        self,
        spec: SourceSpec,
        row: dict[str, Any],
        identity: str,
        raw: Path,
        output: Path,
        raw_changed: bool,
        asset_type: str,
        terrain_key: str,
    ) -> None:
        owner = self._claim(output, identity)
        row["output"] = str(output)
        if owner:
            row.update(status="conflict", message=f"classified destination already owned by {owner}")
            self.rows.append(row)
            return

        if asset_type == "tex":
            status, width, height, message = self._convert_tex(raw, output, raw_changed)
        else:
            status, width, height, message = self._convert_direct(raw, output, raw_changed)
        row.update(status=status, width=width or "", height=height or "", message=message)
        if output.is_file():
            row["output_sha256"] = sha256_file(output)

        confidence, reason, category = self._terrain_match_details(terrain_key)
        row["terrain_confidence"] = confidence
        row["terrain_reason"] = reason
        row["terrain_category"] = category
        if status != "failed" and confidence and CONFIDENCE_RANK[confidence] >= self.terrain_minimum_rank:
            terrain_output, terrain_status, terrain_message = self._archive_terrain(
                output,
                identity,
                spec.terrain_prefix_parts,
                category,
            )
            row["terrain_output"] = terrain_output
            row["terrain_status"] = terrain_status
            if terrain_status in {"failed", "conflict"}:
                row["status"] = "failed" if terrain_status == "failed" else "conflict"
                row["message"] = terrain_message
        self.rows.append(row)

    def _process_loose_asset(self, spec: SourceSpec, source: Path, relative_parts: tuple[str, ...], asset_type: str) -> None:
        relative = _posix(relative_parts)
        origin = {
            "tex": "loose_tex",
            "direct": "loose_direct_image",
            "metadata": "loose_atlas_metadata",
        }[asset_type]
        if asset_type == "tex":
            raw = self.raw_root.joinpath(*self.raw_textures_name, *spec.prefix_parts, *relative_parts)
            output_parts = (*spec.prefix_parts, *self.raw_textures_name, *_without_suffix(relative_parts))
        elif asset_type == "direct":
            raw = self.raw_root.joinpath(*self.raw_direct_name, *spec.prefix_parts, *relative_parts)
            output_parts = (*spec.prefix_parts, *self.raw_direct_name, *_without_suffix(relative_parts))
        else:
            raw = self.raw_root.joinpath(*self.raw_metadata_name, *spec.prefix_parts, *relative_parts)
            output_parts = ()

        row = self._base_row(
            spec,
            origin_kind=origin,
            source_relative=relative,
            source=str(source),
            raw_output=str(raw),
        )
        identity = self._row_identity(row)
        owner = self._claim(raw, identity)
        if owner:
            row.update(status="conflict", message=f"raw destination already owned by {owner}")
            self.rows.append(row)
            return
        try:
            if source.stat().st_size > self.max_loose_bytes:
                raise OSError(f"loose file exceeds configured size limit: {source.stat().st_size}")
            source_sha, raw_changed, raw_status = self._write_raw_file(source, raw)
            row.update(sha256=source_sha, raw_status=raw_status)
        except Exception as exc:
            row.update(status="failed", message=str(exc))
            self.rows.append(row)
            return

        if asset_type == "metadata":
            row["status"] = "metadata_up_to_date" if raw_status == "raw_up_to_date" else "metadata_exported"
            self.rows.append(row)
            return
        output = self.classified_root.joinpath(*output_parts).with_suffix(".png")
        terrain_key = _posix((*spec.prefix_parts, *relative_parts))
        self._finish_image(spec, row, identity, raw, output, raw_changed, asset_type, terrain_key)

    def _archive_source(self, archive_path: Path, chain: tuple[str, ...], member: str = "") -> str:
        suffix = list(chain[1:])
        if member:
            suffix.append(member)
        return str(archive_path) + ("::" + "::".join(suffix) if suffix else "")

    def _unsafe_archive_row(
        self,
        spec: SourceSpec,
        archive_path: Path,
        archive_relative: str,
        chain: tuple[str, ...],
        member: str,
        message: str,
    ) -> None:
        self.rows.append(
            self._base_row(
                spec,
                origin_kind="archive_member",
                archive=archive_relative,
                archive_chain="::".join(chain),
                member=member,
                source_relative="::".join((*chain, member)),
                source=self._archive_source(archive_path, chain, member),
                status="unsafe_archive_member",
                message=message,
            )
        )

    def _process_archive_asset(
        self,
        spec: SourceSpec,
        archive_path: Path,
        archive_relative: str,
        chain: tuple[str, ...],
        member: str,
        member_parts: tuple[str, ...],
        extraction_base: tuple[str, ...],
        payload: bytes,
        asset_type: str,
    ) -> None:
        origin = {
            "tex": "archive_tex",
            "direct": "archive_direct_image",
            "metadata": "archive_atlas_metadata",
            "nested": "nested_archive",
        }[asset_type]
        raw = self.raw_root.joinpath(
            *self.raw_extracted_name,
            *spec.prefix_parts,
            *extraction_base,
            *member_parts,
        )
        source_relative = "::".join((*chain, member))
        row = self._base_row(
            spec,
            origin_kind=origin,
            archive=archive_relative,
            archive_chain="::".join(chain),
            member=member,
            source_relative=source_relative,
            source=self._archive_source(archive_path, chain, member),
            raw_output=str(raw),
        )
        identity = self._row_identity(row)
        owner = self._claim(raw, identity)
        if owner:
            row.update(status="conflict", message=f"raw destination already owned by {owner}")
            self.rows.append(row)
            return
        try:
            source_sha, raw_changed, raw_status = self._write_raw_bytes(payload, raw)
            row.update(sha256=source_sha, raw_status=raw_status)
        except Exception as exc:
            row.update(status="failed", message=str(exc))
            self.rows.append(row)
            return

        if asset_type == "metadata":
            row["status"] = "metadata_up_to_date" if raw_status == "raw_up_to_date" else "metadata_exported"
            self.rows.append(row)
            return
        if asset_type == "nested":
            row["status"] = "raw_up_to_date" if raw_status == "raw_up_to_date" else "raw_copied"
            self.rows.append(row)
            return

        output_parts = (*spec.prefix_parts, *extraction_base, *_without_suffix(member_parts))
        output = self.classified_root.joinpath(*output_parts).with_suffix(".png")
        terrain_key = _posix((*spec.prefix_parts, *extraction_base, *member_parts))
        self._finish_image(spec, row, identity, raw, output, raw_changed, asset_type, terrain_key)

    def _scan_zip(
        self,
        spec: SourceSpec,
        archive_path: Path,
        archive_relative: str,
        archive: zipfile.ZipFile,
        chain: tuple[str, ...],
        extraction_base: tuple[str, ...],
        depth: int,
    ) -> None:
        selected_total = 0
        for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
            if info.is_dir():
                continue
            member = info.filename.replace("\\", "/")
            parts = _safe_member_parts(member)
            mode = (info.external_attr >> 16) & 0o170000
            if parts is None or mode == stat.S_IFLNK:
                self._unsafe_archive_row(spec, archive_path, archive_relative, chain, member, "unsafe path or symbolic link")
                continue
            extension = PurePosixPath(parts[-1]).suffix.lower()
            recognized = (
                extension in self.texture_extensions
                or extension in self.direct_extensions
                or extension in self.metadata_extensions
                or extension in self.archive_extensions
            )
            if not recognized:
                continue
            ratio = info.file_size / max(info.compress_size, 1)
            if (
                info.flag_bits & 0x1
                or info.file_size > self.max_member_bytes
                or ratio > self.max_compression_ratio
                or selected_total + info.file_size > self.max_archive_selected_bytes
            ):
                self._unsafe_archive_row(
                    spec,
                    archive_path,
                    archive_relative,
                    chain,
                    member,
                    "encrypted member or configured ZIP size/compression limit exceeded",
                )
                continue
            try:
                payload = archive.read(info)
            except Exception as exc:
                row = self._base_row(
                    spec,
                    origin_kind="archive_member",
                    archive=archive_relative,
                    archive_chain="::".join(chain),
                    member=member,
                    source_relative="::".join((*chain, member)),
                    source=self._archive_source(archive_path, chain, member),
                    status="failed",
                    message=f"ZIP member read failed: {exc}",
                )
                self.rows.append(row)
                continue
            selected_total += len(payload)

            if extension in self.archive_extensions:
                self._process_archive_asset(
                    spec,
                    archive_path,
                    archive_relative,
                    chain,
                    member,
                    parts,
                    extraction_base,
                    payload,
                    "nested",
                )
                if depth >= self.max_nested_depth:
                    self.rows[-1]["status"] = "nested_archive_depth_limit"
                    self.rows[-1]["message"] = f"configured max_nested_archive_depth={self.max_nested_depth}"
                    continue
                try:
                    with zipfile.ZipFile(io.BytesIO(payload)) as nested:
                        self._scan_zip(
                            spec,
                            archive_path,
                            archive_relative,
                            nested,
                            (*chain, member),
                            (*extraction_base, *_without_suffix(parts)),
                            depth + 1,
                        )
                except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
                    self.rows.append(
                        self._base_row(
                            spec,
                            origin_kind="nested_archive",
                            archive=archive_relative,
                            archive_chain="::".join((*chain, member)),
                            member=member,
                            source_relative="::".join((*chain, member)),
                            source=self._archive_source(archive_path, chain, member),
                            raw_output=str(
                                self.raw_root.joinpath(
                                    *self.raw_extracted_name,
                                    *spec.prefix_parts,
                                    *extraction_base,
                                    *parts,
                                )
                            ),
                            status="archive_error",
                            message=f"nested ZIP open failed: {exc}",
                        )
                    )
                continue

            asset_type = "tex" if extension in self.texture_extensions else "direct" if extension in self.direct_extensions else "metadata"
            self._process_archive_asset(
                spec,
                archive_path,
                archive_relative,
                chain,
                member,
                parts,
                extraction_base,
                payload,
                asset_type,
            )

    def _process_archive(self, spec: SourceSpec, source: Path, relative_parts: tuple[str, ...]) -> None:
        relative = _posix(relative_parts)
        raw = self.raw_root.joinpath(*self.raw_packages_name, *spec.prefix_parts, *relative_parts)
        row = self._base_row(
            spec,
            origin_kind="archive",
            archive=relative,
            archive_chain=relative,
            source_relative=relative,
            source=str(source),
            raw_output=str(raw),
        )
        identity = self._row_identity(row)
        owner = self._claim(raw, identity)
        if owner:
            row.update(status="conflict", message=f"raw archive destination already owned by {owner}")
            self.rows.append(row)
            return
        try:
            source_sha, _, raw_status = self._write_raw_file(source, raw)
            row.update(
                sha256=source_sha,
                raw_status=raw_status,
                status="raw_up_to_date" if raw_status == "raw_up_to_date" else "raw_copied",
            )
            self.rows.append(row)
            with zipfile.ZipFile(raw) as archive:
                self._scan_zip(
                    spec,
                    source,
                    relative,
                    archive,
                    (relative,),
                    _without_suffix(relative_parts),
                    1,
                )
        except (OSError, zipfile.BadZipFile, NotImplementedError) as exc:
            row["status"] = "archive_error"
            row["message"] = str(exc)
            if row not in self.rows:
                self.rows.append(row)

    def scan_source(self, spec: SourceSpec) -> None:
        try:
            candidates = sorted((path for path in spec.root.rglob("*") if path.is_file()), key=lambda path: str(path).casefold())
        except OSError as exc:
            self.rows.append(self._base_row(spec, origin_kind="source", source=str(spec.root), status="failed", message=str(exc)))
            return
        for source in candidates:
            try:
                resolved = source.resolve(strict=True)
            except OSError as exc:
                self.rows.append(self._base_row(spec, origin_kind="source_path", source=str(source), status="failed", message=str(exc)))
                continue
            if not _is_within(resolved, spec.root):
                self.rows.append(
                    self._base_row(
                        spec,
                        origin_kind="source_path",
                        source=str(source),
                        status="unsafe_source_path",
                        message="resolved source leaves configured source root",
                    )
                )
                continue
            relative_parts = tuple(source.relative_to(spec.root).parts)
            extension = source.suffix.lower()
            if extension in self.archive_extensions:
                self._process_archive(spec, source, relative_parts)
            elif extension in self.texture_extensions:
                self._process_loose_asset(spec, source, relative_parts, "tex")
            elif extension in self.direct_extensions:
                self._process_loose_asset(spec, source, relative_parts, "direct")
            elif extension in self.metadata_extensions:
                self._process_loose_asset(spec, source, relative_parts, "metadata")

    def summary(self, sources: list[SourceSpec]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        source_counts: dict[str, dict[str, int]] = {source.source_id: {} for source in sources}
        for row in self.rows:
            status = str(row.get("status", ""))
            status_counts[status] = status_counts.get(status, 0) + 1
            per_source = source_counts.setdefault(str(row.get("source_id", "")), {})
            per_source[status] = per_source.get(status, 0) + 1
        failed_rows = sum(
            row.get("status") in {"failed", "archive_error"}
            or row.get("terrain_status") == "failed"
            for row in self.rows
        )
        return {
            "version": 1,
            "source_count": len(sources),
            "sources": source_counts,
            "total": len(self.rows),
            "converted": status_counts.get("converted", 0),
            "updated": status_counts.get("updated", 0),
            "up_to_date": status_counts.get("up_to_date", 0),
            "metadata": status_counts.get("metadata_exported", 0) + status_counts.get("metadata_up_to_date", 0),
            "failed": failed_rows,
            "conflicts": status_counts.get("conflict", 0),
            "unsafe": status_counts.get("unsafe_archive_member", 0) + status_counts.get("unsafe_source_path", 0),
            "terrain_archived": sum(
                row.get("terrain_status") in {"hardlink", "copy", "up_to_date"} for row in self.rows
            ),
            "status_counts": status_counts,
        }

    def write_indices(self, summary: dict[str, Any]) -> None:
        self.index_root.mkdir(parents=True, exist_ok=True)
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(self.rows)
        _atomic_write_bytes(
            self.index_root / f"{INDEX_BASENAME}.csv",
            ("\ufeff" + csv_buffer.getvalue()).encode("utf-8"),
        )
        json_payload = {"version": 1, "summary": summary, "rows": self.rows}
        _atomic_write_bytes(
            self.index_root / f"{INDEX_BASENAME}.json",
            (json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        jsonl = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows)
        _atomic_write_bytes(self.index_root / f"{INDEX_BASENAME}.jsonl", jsonl.encode("utf-8"))

        terrain_rows = []
        for row in self.rows:
            confidence = str(row.get("terrain_confidence", "")).lower()
            terrain_output = row.get("terrain_output")
            if (
                CONFIDENCE_RANK.get(confidence, 0) < self.terrain_minimum_rank
                or row.get("terrain_status") not in {"hardlink", "copy", "up_to_date"}
                or not terrain_output
            ):
                continue
            output_path = Path(str(terrain_output)).resolve()
            valid, _, _, _ = validate_png(output_path)
            if _is_within(output_path, self.terrain_root) and valid:
                terrain_rows.append(row)

        terrain_csv_buffer = io.StringIO(newline="")
        terrain_writer = csv.DictWriter(
            terrain_csv_buffer, fieldnames=INDEX_FIELDS, extrasaction="ignore"
        )
        terrain_writer.writeheader()
        terrain_writer.writerows(terrain_rows)
        _atomic_write_bytes(
            self.index_root / f"{TERRAIN_INDEX_BASENAME}.csv",
            ("\ufeff" + terrain_csv_buffer.getvalue()).encode("utf-8"),
        )
        _atomic_write_bytes(
            self.index_root / f"{TERRAIN_INDEX_BASENAME}.json",
            (
                json.dumps({"version": 1, "rows": terrain_rows}, ensure_ascii=False, indent=2)
                + "\n"
            ).encode("utf-8"),
        )
        _atomic_write_bytes(
            self.index_root / "klei_dst_image_export_summary.json",
            (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )


def run_export(
    study_root: Path,
    sources_manifest: Path,
    skill_root: Path,
    config_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    study_root = Path(study_root).resolve()
    skill_root = Path(skill_root).resolve(strict=True)
    if config_path is None:
        config_path = skill_root / "references" / "klei-dst-image-export-config.json"
    config = _load_json(Path(config_path).resolve(strict=True), "Klei/DST image export config")
    sources = load_sources(Path(sources_manifest), study_root)
    exporter = ImageExporter(study_root, skill_root, config, force)
    for source in sources:
        exporter.scan_source(source)
    summary = exporter.summary(sources)
    exporter.write_indices(summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--sources-manifest", required=True, type=Path)
    parser.add_argument("--skill-root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_export(
            study_root=args.study_root,
            sources_manifest=args.sources_manifest,
            skill_root=args.skill_root,
            config_path=args.config,
            force=args.force,
        )
    except (ManifestError, OSError) as exc:
        print(f"Klei/DST image export failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(summary.get(field, 0) > 0 for field in ("failed", "conflicts", "unsafe")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Archive and index directly readable loose files from a Unity build."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

from PIL import Image


CHUNK_SIZE = 1024 * 1024
ALLOWED_STUDY_DIRECTORIES = (
    "0.原始导出",
    "1.分类资源",
    "2.报告",
    "3.代码",
    "4.临时目录",
)
CSV_FIELDS = (
    "source_rel",
    "output_rel",
    "extension",
    "size",
    "sha256",
    "kind",
    "classification",
    "status",
    "error",
    "discovery_rule",
    "validation",
    "raw_status",
    "classified_output_rel",
    "classification_status",
)


class ExportError(ValueError):
    """Raised when an input, configuration, or output path is unsafe."""


@dataclass(frozen=True)
class DiscoveryRule:
    rule_id: str
    path_glob: str


@dataclass(frozen=True)
class IncludeType:
    extension: str
    kind: str
    validator: str
    formats: tuple[str, ...]


@dataclass(frozen=True)
class RegexRule:
    rule_id: str
    patterns: tuple[re.Pattern[str], ...]
    classification: str = ""


@dataclass(frozen=True)
class LoadedConfig:
    raw: dict
    discovery_rules: tuple[DiscoveryRule, ...]
    include_types: dict[str, IncludeType]
    exclude_rules: tuple[RegexRule, ...]
    image_rules: tuple[RegexRule, ...]
    default_image_classification: str


@dataclass(frozen=True)
class SourceFile:
    path: Path
    source_rel: str
    discovery_rule: str
    include_type: IncludeType


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def has_reparse_attribute(path: Path) -> bool:
    """Detect symbolic links and Windows reparse points without following them."""
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def path_chain_has_reparse(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if has_reparse_attribute(current):
            return True
    return False


def safe_config_path(value: str, label: str) -> str:
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ExportError(f"{label} 不是安全的相对路径模式：{value!r}")
    return normalized


def safe_relative_path(value: str, label: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ExportError(f"{label} 不是安全的相对路径：{value!r}")
    return pure


def safe_classification(value: str, label: str) -> str:
    pure = safe_relative_path(value, label)
    if len(pure.parts) != 1:
        raise ExportError(f"{label} 必须是 图片/ 下的单层目录名：{value!r}")
    return pure.parts[0]


def compile_patterns(values: object, label: str) -> tuple[re.Pattern[str], ...]:
    if not isinstance(values, list) or not values:
        raise ExportError(f"{label} 必须是非空正则列表")
    try:
        return tuple(re.compile(str(value), re.IGNORECASE) for value in values)
    except re.error as exc:
        raise ExportError(f"{label} 含无效正则：{exc}") from exc


def load_config(path: Path) -> LoadedConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ExportError("Unity loose resource 配置 schema_version 必须为 1")

    discovery_rules: list[DiscoveryRule] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload.get("discovery_roots", [])):
        if not isinstance(item, dict):
            raise ExportError(f"discovery_roots[{index}] 必须是对象")
        rule_id = str(item.get("id", "")).strip()
        if not rule_id or rule_id in seen_ids:
            raise ExportError(f"discovery_roots[{index}] 的 id 缺失或重复")
        seen_ids.add(rule_id)
        discovery_rules.append(
            DiscoveryRule(
                rule_id=rule_id,
                path_glob=safe_config_path(str(item.get("glob", "")), f"discovery_roots[{index}].glob"),
            )
        )
    if not discovery_rules:
        raise ExportError("配置必须包含 discovery_roots")

    include_types: dict[str, IncludeType] = {}
    valid_validators = {"none", "json", "pillow"}
    for index, item in enumerate(payload.get("include_extensions", [])):
        if not isinstance(item, dict):
            raise ExportError(f"include_extensions[{index}] 必须是对象")
        extension = str(item.get("extension", "")).strip().lower()
        kind = str(item.get("kind", "")).strip()
        validator = str(item.get("validator", "none")).strip().lower()
        raw_formats = item.get("formats", [])
        if not extension.startswith(".") or "/" in extension or "\\" in extension:
            raise ExportError(f"include_extensions[{index}] 的 extension 无效：{extension!r}")
        if extension in include_types or not kind:
            raise ExportError(f"include_extensions[{index}] 的 extension 重复或 kind 缺失")
        if validator not in valid_validators:
            raise ExportError(f"include_extensions[{index}] 的 validator 无效：{validator!r}")
        if not isinstance(raw_formats, list):
            raise ExportError(f"include_extensions[{index}].formats 必须是列表")
        formats = tuple(str(value).upper() for value in raw_formats if str(value).strip())
        if kind == "image" and (validator != "pillow" or not formats):
            raise ExportError(f"图片扩展 {extension} 必须配置 pillow validator 与 formats")
        if kind != "image" and validator == "pillow":
            raise ExportError(f"非图片扩展 {extension} 不得使用 pillow validator")
        if kind == "json" and validator != "json":
            raise ExportError(f"JSON 扩展 {extension} 必须使用 json validator")
        include_types[extension] = IncludeType(extension, kind, validator, formats)
    if not include_types:
        raise ExportError("配置必须包含 include_extensions")

    exclude_rules: list[RegexRule] = []
    for index, item in enumerate(payload.get("exclude_rules", [])):
        if not isinstance(item, dict):
            raise ExportError(f"exclude_rules[{index}] 必须是对象")
        rule_id = str(item.get("id", "")).strip() or f"exclude_{index}"
        exclude_rules.append(
            RegexRule(rule_id, compile_patterns(item.get("patterns"), f"exclude_rules[{index}].patterns"))
        )

    image_rules: list[RegexRule] = []
    for index, item in enumerate(payload.get("image_classification_rules", [])):
        if not isinstance(item, dict):
            raise ExportError(f"image_classification_rules[{index}] 必须是对象")
        rule_id = str(item.get("id", "")).strip() or f"image_rule_{index}"
        classification = safe_classification(
            str(item.get("classification", "")),
            f"image_classification_rules[{index}].classification",
        )
        image_rules.append(
            RegexRule(
                rule_id,
                compile_patterns(item.get("patterns"), f"image_classification_rules[{index}].patterns"),
                classification,
            )
        )
    if not image_rules:
        raise ExportError("配置必须包含 image_classification_rules")
    default_classification = safe_classification(
        str(payload.get("default_image_classification", "")),
        "default_image_classification",
    )
    return LoadedConfig(
        raw=payload,
        discovery_rules=tuple(discovery_rules),
        include_types=include_types,
        exclude_rules=tuple(exclude_rules),
        image_rules=tuple(image_rules),
        default_image_classification=default_classification,
    )


def validate_study_root(path: Path) -> Path:
    requested = path.expanduser()
    if not requested.is_dir():
        raise ExportError(f"study-root 不存在或不是目录：{requested}")
    if has_reparse_attribute(requested):
        raise ExportError(f"study-root 本身不得是 symlink/reparse point：{requested}")
    resolved = requested.resolve(strict=True)
    children = list(resolved.iterdir())
    expected = set(ALLOWED_STUDY_DIRECTORIES)
    actual = {child.name for child in children}
    if actual != expected or len(children) != len(expected):
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ExportError(f"study-root 顶层必须恰含 5 个编号目录；缺失={missing}，额外={extra}")
    for name in ALLOWED_STUDY_DIRECTORIES:
        child = resolved / name
        if not child.is_dir() or has_reparse_attribute(child):
            raise ExportError(f"study-root 顶层项必须是真实目录且不能是 reparse point：{child}")
        child_resolved = child.resolve(strict=True)
        if child_resolved.parent != resolved:
            raise ExportError(f"study-root 顶层目录 canonical 边界异常：{child}")
    return resolved


def validate_source_root(path: Path, study_root: Path) -> Path:
    requested = path.expanduser()
    if not requested.is_dir():
        raise ExportError(f"source 不存在或不是目录：{requested}")
    if has_reparse_attribute(requested):
        raise ExportError(f"source 本身不得是 symlink/reparse point：{requested}")
    resolved = requested.resolve(strict=True)
    if is_within(resolved, study_root) or is_within(study_root, resolved):
        raise ExportError("source 与 study-root 不得互相包含，避免修改或重新扫描源目录")
    return resolved


def ensure_safe_directory(base: Path, relative: PurePosixPath) -> Path:
    current = base
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            if has_reparse_attribute(current) or not current.is_dir():
                raise ExportError(f"输出目录存在冲突或 reparse point：{current}")
        else:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            if has_reparse_attribute(current) or not current.is_dir():
                raise ExportError(f"无法安全创建输出目录：{current}")
        resolved = current.resolve(strict=True)
        if not is_within(resolved, base.resolve(strict=True)):
            raise ExportError(f"输出目录越过 canonical 边界：{current}")
    return current


def ensure_safe_parent(root: Path, relative: PurePosixPath) -> Path:
    if len(relative.parts) == 1:
        return root
    return ensure_safe_directory(root, PurePosixPath(*relative.parts[:-1]))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def copy_without_overwrite(source: Path, destination: Path, root: Path, expected_hash: str) -> tuple[str, str]:
    relative = safe_relative_path(destination.relative_to(root).as_posix(), "输出相对路径")
    ensure_safe_parent(root, relative)
    if os.path.lexists(destination):
        if has_reparse_attribute(destination) or not destination.is_file():
            return "conflict", "destination_exists_and_is_not_a_regular_file"
        destination_hash = hash_file(destination)
        if destination_hash == expected_hash:
            return "reused", "same_sha256"
        return "conflict", f"different_sha256:{destination_hash}"

    created = False
    try:
        with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
            created = True
            shutil.copyfileobj(source_handle, destination_handle, length=CHUNK_SIZE)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        destination_hash = hash_file(destination)
        if destination_hash != expected_hash:
            destination.unlink(missing_ok=True)
            return "error", f"source_changed_during_copy:{destination_hash}"
        return "copied", ""
    except FileExistsError:
        if os.path.lexists(destination) and not has_reparse_attribute(destination) and destination.is_file():
            destination_hash = hash_file(destination)
            if destination_hash == expected_hash:
                return "reused", "same_sha256"
            return "conflict", f"different_sha256:{destination_hash}"
        return "conflict", "destination_created_concurrently"
    except OSError as exc:
        if created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        return "error", f"copy_failed:{type(exc).__name__}:{exc}"


def add_error(errors: list[dict], stage: str, source_rel: str, output_rel: str, message: str) -> None:
    errors.append(
        {
            "stage": stage,
            "source_rel": source_rel,
            "output_rel": output_rel,
            "error": message,
        }
    )


def select_image_classification(source_rel: str, config: LoadedConfig) -> tuple[str, str]:
    for rule in config.image_rules:
        if any(pattern.search(source_rel) for pattern in rule.patterns):
            return rule.classification, rule.rule_id
    return config.default_image_classification, "default"


def excluded_by(source_rel: str, config: LoadedConfig) -> str:
    for rule in config.exclude_rules:
        if any(pattern.search(source_rel) for pattern in rule.patterns):
            return rule.rule_id
    return ""


def validate_archived(path: Path, include_type: IncludeType) -> tuple[str, str]:
    if include_type.validator == "none":
        return "not_required", ""
    if include_type.validator == "json":
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                json.load(handle)
            return "valid", ""
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return "invalid", f"invalid_json:{type(exc).__name__}:{exc}"
    try:
        with Image.open(path) as image:
            if image.format not in include_type.formats:
                return "invalid", f"unexpected_image_format:{image.format}"
            image.verify()
        with Image.open(path) as image:
            if image.format not in include_type.formats or image.width < 1 or image.height < 1:
                return "invalid", "invalid_image_dimensions_or_format"
            image.load()
        return "valid", ""
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        return "invalid", f"invalid_image:{type(exc).__name__}:{exc}"


def find_discovery_roots(source: Path, config: LoadedConfig, errors: list[dict]) -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for rule in config.discovery_rules:
        try:
            candidates = sorted(source.glob(rule.path_glob), key=lambda item: item.as_posix().casefold())
        except (OSError, ValueError) as exc:
            add_error(errors, "discovery", rule.path_glob, "", f"glob_failed:{type(exc).__name__}:{exc}")
            continue
        for candidate in candidates:
            try:
                candidate_rel = candidate.relative_to(source).as_posix()
            except ValueError:
                add_error(errors, "path_validation", str(candidate), "", "discovery_root_outside_source")
                continue
            if path_chain_has_reparse(candidate, source):
                add_error(errors, "path_validation", candidate_rel, "", "discovery_root_reparse_rejected")
                continue
            if not candidate.is_dir():
                add_error(errors, "discovery", candidate_rel, "", "discovery_root_is_not_directory")
                continue
            resolved = candidate.resolve(strict=True)
            if not is_within(resolved, source) or resolved == source:
                add_error(errors, "path_validation", candidate_rel, "", "discovery_root_canonical_escape")
                continue
            if resolved not in seen:
                seen.add(resolved)
                roots.append((resolved, rule.rule_id))
    return roots


def scan_source_files(
    source: Path,
    discovery_roots: Iterable[tuple[Path, str]],
    config: LoadedConfig,
    errors: list[dict],
) -> tuple[list[SourceFile], list[dict], int]:
    files: dict[str, SourceFile] = {}
    rejected_rows: list[dict] = []
    excluded_count = 0
    for discovery_root, rule_id in discovery_roots:
        stack = [discovery_root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name.casefold())
            except OSError as exc:
                source_rel = directory.relative_to(source).as_posix()
                add_error(errors, "scan", source_rel, "", f"scandir_failed:{type(exc).__name__}:{exc}")
                continue
            child_directories: list[Path] = []
            for entry in entries:
                path = Path(entry.path)
                source_rel = path.relative_to(source).as_posix()
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    add_error(errors, "scan", source_rel, "", f"lstat_failed:{type(exc).__name__}:{exc}")
                    continue
                attributes = getattr(info, "st_file_attributes", 0)
                reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                is_reparse = entry.is_symlink() or bool(attributes & reparse_flag)
                extension = path.suffix.lower()
                include_type = config.include_types.get(extension)
                if is_reparse:
                    add_error(errors, "path_validation", source_rel, "", "symlink_or_reparse_rejected")
                    if include_type is not None and stat.S_ISREG(info.st_mode):
                        classification = (
                            select_image_classification(source_rel, config)[0]
                            if include_type.kind == "image"
                            else ""
                        )
                        rejected_rows.append(
                            {
                                "source_rel": source_rel,
                                "output_rel": "",
                                "extension": extension,
                                "size": info.st_size,
                                "sha256": "",
                                "kind": include_type.kind,
                                "classification": classification,
                                "status": "rejected",
                                "error": "symlink_or_reparse_rejected",
                                "discovery_rule": rule_id,
                                "validation": "skipped",
                                "raw_status": "rejected",
                                "classified_output_rel": "",
                                "classification_status": "skipped",
                            }
                        )
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child_directories.append(path)
                    continue
                if not stat.S_ISREG(info.st_mode) or include_type is None:
                    continue
                if excluded_by(source_rel, config):
                    excluded_count += 1
                    continue
                if source_rel not in files:
                    files[source_rel] = SourceFile(path, source_rel, rule_id, include_type)
            stack.extend(reversed(child_directories))
    return (
        sorted(files.values(), key=lambda item: item.source_rel.casefold()),
        sorted(rejected_rows, key=lambda row: str(row["source_rel"]).casefold()),
        excluded_count,
    )


def process_source_file(
    item: SourceFile,
    source: Path,
    study_root: Path,
    raw_root: Path,
    classified_image_root: Path,
    config: LoadedConfig,
    errors: list[dict],
) -> dict:
    relative = safe_relative_path(item.source_rel, "source_rel")
    raw_destination = raw_root.joinpath(*relative.parts)
    output_rel = raw_destination.relative_to(study_root).as_posix()
    classification = ""
    classification_rule = ""
    if item.include_type.kind == "image":
        classification, classification_rule = select_image_classification(item.source_rel, config)

    row = {
        "source_rel": item.source_rel,
        "output_rel": output_rel,
        "extension": item.include_type.extension,
        "size": "",
        "sha256": "",
        "kind": item.include_type.kind,
        "classification": classification,
        "status": "error",
        "error": "",
        "discovery_rule": item.discovery_rule,
        "validation": "skipped",
        "raw_status": "error",
        "classified_output_rel": "",
        "classification_status": "skipped",
    }
    problems: list[str] = []
    try:
        if path_chain_has_reparse(item.path, source):
            raise ExportError("source_path_became_reparse")
        resolved_source = item.path.resolve(strict=True)
        if not is_within(resolved_source, source) or not resolved_source.is_file():
            raise ExportError("source_canonical_escape_or_not_file")
        info = resolved_source.stat()
        source_hash = hash_file(resolved_source)
        row["size"] = info.st_size
        row["sha256"] = source_hash
    except (OSError, ExportError) as exc:
        message = f"source_validation_failed:{type(exc).__name__}:{exc}"
        row["error"] = message
        row["status"] = "rejected"
        row["raw_status"] = "rejected"
        add_error(errors, "path_validation", item.source_rel, output_rel, message)
        return row

    raw_status, raw_detail = copy_without_overwrite(resolved_source, raw_destination, raw_root, source_hash)
    row["raw_status"] = raw_status
    if raw_status in {"conflict", "error"}:
        message = f"raw_{raw_status}:{raw_detail}"
        problems.append(message)
        add_error(errors, "raw_copy", item.source_rel, output_rel, message)
        row["status"] = raw_status
        row["error"] = " | ".join(problems)
        return row

    validation, validation_error = validate_archived(raw_destination, item.include_type)
    row["validation"] = validation
    if validation == "invalid":
        problems.append(validation_error)
        add_error(errors, "validation", item.source_rel, output_rel, validation_error)
        row["status"] = "archived_invalid"
        row["error"] = " | ".join(problems)
        return row

    if item.include_type.kind == "image":
        classified_relative = PurePosixPath(classification, *relative.parts)
        classified_destination = classified_image_root.joinpath(*classified_relative.parts)
        classified_output_rel = classified_destination.relative_to(study_root).as_posix()
        row["classified_output_rel"] = classified_output_rel
        classification_status, classification_detail = copy_without_overwrite(
            raw_destination,
            classified_destination,
            classified_image_root,
            source_hash,
        )
        row["classification_status"] = classification_status
        if classification_status in {"conflict", "error"}:
            message = f"classification_{classification_status}:{classification_detail}"
            problems.append(message)
            add_error(errors, "classification_copy", item.source_rel, classified_output_rel, message)
            row["status"] = classification_status
        else:
            row["status"] = raw_status
        row["discovery_rule"] = f"{item.discovery_rule};image_rule={classification_rule}"
    else:
        row["classification_status"] = "not_applicable"
        row["status"] = raw_status
    row["error"] = " | ".join(problems)
    return row


def atomic_text_write(path: Path, text: str, encoding: str) -> None:
    if os.path.lexists(path) and (has_reparse_attribute(path) or not path.is_file()):
        raise ExportError(f"索引目标存在冲突或 reparse point：{path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding=encoding, newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if os.path.lexists(path) and (has_reparse_attribute(path) or not path.is_file()):
        raise ExportError(f"CSV 索引目标存在冲突或 reparse point：{path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_loose_resources(source: Path, study_root: Path, config_path: Path) -> tuple[dict, list[dict], list[dict]]:
    config = load_config(config_path.resolve(strict=True))
    study_root = validate_study_root(study_root)
    source = validate_source_root(source, study_root)

    raw_root = ensure_safe_directory(study_root / "0.原始导出", PurePosixPath("UnityLooseResources"))
    classified_image_root = ensure_safe_directory(study_root / "1.分类资源", PurePosixPath("图片"))
    index_root = ensure_safe_directory(
        study_root / "4.临时目录",
        PurePosixPath("中间索引", "UnityLooseResources"),
    )

    errors: list[dict] = []
    discovery_roots = find_discovery_roots(source, config, errors)
    source_files, rejected_rows, excluded_count = scan_source_files(source, discovery_roots, config, errors)
    rows = list(rejected_rows)
    rows.extend(
        process_source_file(
            item,
            source,
            study_root,
            raw_root,
            classified_image_root,
            config,
            errors,
        )
        for item in source_files
    )
    rows.sort(key=lambda row: str(row["source_rel"]).casefold())

    status_counts = Counter(str(row["status"]) for row in rows)
    raw_status_counts = Counter(str(row["raw_status"]) for row in rows)
    validation_counts = Counter(str(row["validation"]) for row in rows)
    classification_status_counts = Counter(str(row["classification_status"]) for row in rows)
    kind_counts = Counter(str(row["kind"]) for row in rows)
    summary = {
        "schema_version": 1,
        "source": str(source),
        "study_root": str(study_root),
        "config": str(config_path.resolve(strict=True)),
        "discovery_roots": [
            {"path": root.relative_to(source).as_posix(), "rule": rule_id}
            for root, rule_id in discovery_roots
        ],
        "discovery_root_count": len(discovery_roots),
        "discovered_count": len(rows),
        "excluded_count": excluded_count,
        "index_row_count": len(rows),
        "error_count": len(errors),
        "status_counts": dict(sorted(status_counts.items())),
        "raw_status_counts": dict(sorted(raw_status_counts.items())),
        "validation_counts": dict(sorted(validation_counts.items())),
        "classification_status_counts": dict(sorted(classification_status_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "raw_output_root": raw_root.relative_to(study_root).as_posix(),
        "classified_image_root": classified_image_root.relative_to(study_root).as_posix(),
        "index_root": index_root.relative_to(study_root).as_posix(),
        "csv_encoding": "utf-8-sig (UTF-8 BOM)",
        "json_encoding": "utf-8; ensure_ascii=false",
        "reconciliation": {
            "index_rows_equal_discovered": len(rows) == len(source_files) + len(rejected_rows),
            "status_counts_equal_index_rows": sum(status_counts.values()) == len(rows),
            "raw_status_counts_equal_index_rows": sum(raw_status_counts.values()) == len(rows),
        },
        "path_safety_boundary": {
            "implemented": [
                "配置路径拒绝绝对路径、盘符与 ..",
                "源扫描不跟随 symlink，并通过 lstat 拒绝 Windows reparse point",
                "源文件与发现根在读取前执行 canonical containment 检查",
                "输出目录逐级创建并拒绝 symlink/reparse point；文件以 exclusive create 写入",
                "同 SHA256 目标复用，不同 SHA256 目标记录 conflict 且不覆盖",
            ],
            "limitation": "基于 pathlib/lstat 的 best-effort 检查未持有目录句柄；恶意并发在检查与打开之间替换路径的 TOCTOU 竞态无法完全消除。固定索引 CSV/JSON/JSONL 会在确认其为普通文件后原子重建。",
        },
        "status": "completed" if not errors else "completed_with_issues",
    }

    write_csv(index_root / "loose_resources_index.csv", rows)
    atomic_text_write(
        index_root / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "utf-8",
    )
    atomic_text_write(
        index_root / "errors.jsonl",
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in errors),
        "utf-8",
    )
    return summary, rows, errors


def build_parser() -> argparse.ArgumentParser:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="导出 Unity 构建中直接可读的 loose 资源")
    parser.add_argument("--source", required=True, type=Path, help="授权的游戏根目录")
    parser.add_argument("--study-root", required=True, type=Path, help="已存在且含 5 个编号目录的学习目录")
    parser.add_argument(
        "--config",
        type=Path,
        default=skill_root / "references" / "unity-loose-resource-config.json",
        help="发现、扩展名、排除和图片分类配置",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary, _rows, errors = export_loose_resources(args.source, args.study_root, args.config)
    except (ExportError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

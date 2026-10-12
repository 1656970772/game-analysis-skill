from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
import sys
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


COPY_CHUNK_SIZE = 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class AuditError(ValueError):
    """Raised when input or configuration cannot be audited safely."""


def _reject_constant(value: str) -> None:
    raise AuditError(f"JSON contains a non-standard constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"JSON contains a duplicate object key: {key!r}")
        result[key] = value
    return result


def read_json_strict(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
        return json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except UnicodeError as exc:
        raise AuditError(f"JSON is not valid UTF-8: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AuditError(
            f"JSON parse failed: {path}: line={exc.lineno} column={exc.colno}: {exc.msg}"
        ) from exc


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def encode_crc32(value: int, encoding: str) -> str:
    if encoding == "big_endian_hex":
        return f"{value & 0xFFFFFFFF:08x}"
    if encoding == "little_endian_bytes":
        return (value & 0xFFFFFFFF).to_bytes(4, "little").hex()
    raise AuditError(f"Unsupported file_crc32_encoding: {encoding!r}")


def hash_payload(path: Path, algorithm: str, crc_encoding: str) -> tuple[str, str]:
    digest = hashlib.new(algorithm)
    crc = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK_SIZE), b""):
            digest.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return digest.hexdigest().lower(), encode_crc32(crc, crc_encoding)


def is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as exc:
        raise AuditError(f"Cannot inspect path attributes: {path}: {exc}") from exc
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def reject_reparse_chain(path: Path, label: str) -> None:
    resolved = path.resolve(strict=True)
    current = path.absolute()
    while True:
        if current.exists() and is_reparse(current):
            raise AuditError(f"{label} contains a symlink or Windows reparse point: {current}")
        if current.parent == current:
            break
        current = current.parent
    if not resolved.exists():
        raise AuditError(f"{label} does not exist: {path}")


def safe_walk_files(root: Path, *, recursive: bool = True) -> list[Path]:
    files: list[Path] = []
    for current_text, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_text)
        if current != root and is_reparse(current):
            raise AuditError(f"Refusing reparse directory below input root: {current}")
        for directory_name in list(directory_names):
            directory = current / directory_name
            if is_reparse(directory):
                raise AuditError(f"Refusing reparse directory below input root: {directory}")
        for file_name in file_names:
            path = current / file_name
            if is_reparse(path):
                raise AuditError(f"Refusing reparse file below input root: {path}")
            files.append(path)
        if not recursive:
            directory_names.clear()
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_paths(manifest_root: Path, streaming_root: Path, output_dir: Path) -> None:
    if not manifest_root.is_dir():
        raise AuditError(f"--manifest-root is not a directory: {manifest_root}")
    if not streaming_root.is_dir():
        raise AuditError(f"--streaming-assets-root is not a directory: {streaming_root}")
    reject_reparse_chain(manifest_root, "manifest root")
    reject_reparse_chain(streaming_root, "streaming assets root")
    manifest_resolved = manifest_root.resolve(strict=True)
    streaming_resolved = streaming_root.resolve(strict=True)
    output_resolved = output_dir.resolve(strict=False)
    if is_relative_to(output_resolved, manifest_resolved) or is_relative_to(
        output_resolved, streaming_resolved
    ):
        raise AuditError("Output directory must not be inside either read-only input root")
    if output_dir.exists():
        if not output_dir.is_dir():
            raise AuditError(f"--output-dir exists but is not a directory: {output_dir}")
        reject_reparse_chain(output_dir, "output directory")


def require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuditError(f"{label} must be an array")
    return value


def require_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise AuditError(f"{label} must be a string")
    return value


def require_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AuditError(f"{label} must be an integer")
    return value


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise AuditError(f"{label} must be a boolean")
    return value


def require_string_list(value: Any, label: str) -> list[str]:
    items = require_list(value, label)
    for index, item in enumerate(items):
        require_str(item, f"{label}[{index}]")
    return items


def load_config(path: Path) -> dict[str, Any]:
    config = require_dict(read_json_strict(path), "config")
    if require_int(config.get("schema_version"), "config.schema_version") != 1:
        raise AuditError("Only config schema_version=1 is supported")
    discovery = require_dict(config.get("discovery"), "config.discovery")
    require_string_list(discovery.get("manifest_globs"), "config.discovery.manifest_globs")
    runtime = require_dict(config.get("runtime"), "config.runtime")
    require_string_list(
        runtime.get("package_directory_candidates"),
        "config.runtime.package_directory_candidates",
    )
    require_bool(runtime.get("scan_recursive"), "config.runtime.scan_recursive")
    require_string_list(runtime.get("payload_extensions"), "config.runtime.payload_extensions")
    require_string_list(runtime.get("metadata_patterns"), "config.runtime.metadata_patterns")
    styles = require_dict(config.get("output_name_styles"), "config.output_name_styles")
    for style_id, style_value in styles.items():
        style = require_dict(style_value, f"config.output_name_styles[{style_id!r}]")
        require_str(style.get("name"), f"config.output_name_styles[{style_id!r}].name")
        require_string_list(
            style.get("file_name_templates"),
            f"config.output_name_styles[{style_id!r}].file_name_templates",
        )
    require_string_list(
        config.get("fallback_file_name_templates"), "config.fallback_file_name_templates"
    )
    algorithms = require_dict(
        config.get("hash_algorithms_by_hex_length"),
        "config.hash_algorithms_by_hex_length",
    )
    for length, algorithm in algorithms.items():
        if not str(length).isdigit():
            raise AuditError(f"Hash length key is not numeric: {length!r}")
        algorithm_name = require_str(algorithm, f"hash algorithm for length {length}")
        try:
            hashlib.new(algorithm_name)
        except ValueError as exc:
            raise AuditError(f"Unsupported hash algorithm in config: {algorithm_name}") from exc
    crc_encoding = require_str(config.get("file_crc32_encoding"), "config.file_crc32_encoding")
    if crc_encoding not in {"big_endian_hex", "little_endian_bytes"}:
        raise AuditError(
            "config.file_crc32_encoding must be big_endian_hex or little_endian_bytes"
        )
    outputs = require_dict(config.get("outputs"), "config.outputs")
    for key in ("asset_csv", "bundle_csv", "summary_json", "summary_markdown"):
        output_name = require_str(outputs.get(key), f"config.outputs.{key}")
        if Path(output_name).name != output_name:
            raise AuditError(f"Output name must be a plain filename: {output_name!r}")
    limits = require_dict(config.get("limits"), "config.limits")
    if require_int(limits.get("max_issue_examples"), "config.limits.max_issue_examples") < 0:
        raise AuditError("config.limits.max_issue_examples must be non-negative")
    validation = require_dict(config.get("validation"), "config.validation")
    require_bool(validation.get("fail_on_mismatch"), "config.validation.fail_on_mismatch")
    return config


def discover_manifests(root: Path, patterns: list[str]) -> list[Path]:
    matches: list[Path] = []
    for path in safe_walk_files(root):
        relative = path.relative_to(root).as_posix()
        if any(
            fnmatch.fnmatchcase(relative, pattern)
            or fnmatch.fnmatchcase(path.name, pattern)
            for pattern in patterns
        ):
            matches.append(path)
    if not matches:
        raise AuditError(f"No YooAsset JSON manifests matched below: {root}")
    return matches


def render_safe_relative(template: str, values: dict[str, str], label: str) -> str:
    try:
        rendered = template.format_map(values)
    except KeyError as exc:
        raise AuditError(f"Unknown placeholder in {label}: {exc}") from exc
    candidate = PurePosixPath(rendered.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise AuditError(f"Unsafe rendered relative path for {label}: {rendered!r}")
    return candidate.as_posix()


def resolve_package_dir(
    manifest: Path,
    manifest_root: Path,
    streaming_root: Path,
    package_name: str,
    templates: list[str],
) -> tuple[Path, str]:
    relative_parent = manifest.parent.relative_to(manifest_root).as_posix()
    values = {
        "ManifestRelativeParent": relative_parent,
        "PackageName": package_name,
    }
    candidates: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for template in templates:
        if template == "{ManifestRelativeParent}" and relative_parent == ".":
            continue
        relative = render_safe_relative(template, values, "package directory template")
        key = relative.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidate = streaming_root / Path(*PurePosixPath(relative).parts)
        if candidate.is_dir():
            candidates.append((candidate, relative))
    if not candidates:
        raise AuditError(
            f"No runtime package directory found for {manifest} (PackageName={package_name!r})"
        )
    canonical = {path.resolve(strict=True) for path, _ in candidates}
    if len(canonical) != 1:
        raise AuditError(
            f"Multiple runtime package directories matched for {manifest}: "
            + ", ".join(str(path) for path, _ in candidates)
        )
    package_dir, relative = candidates[0]
    reject_reparse_chain(package_dir, "runtime package directory")
    return package_dir, relative


def file_snapshot(root: Path) -> tuple[dict[str, list[int]], str]:
    snapshot: dict[str, list[int]] = {}
    for path in safe_walk_files(root):
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = [
            stat.st_size,
            stat.st_mtime_ns,
            getattr(stat, "st_dev", 0),
            getattr(stat, "st_ino", 0),
        ]
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return snapshot, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_manifest(path: Path) -> dict[str, Any]:
    manifest = require_dict(read_json_strict(path), f"manifest {path}")
    for key in ("FileVersion", "PackageName", "PackageVersion"):
        require_str(manifest.get(key), f"{path.name}.{key}")
    require_int(manifest.get("OutputNameStyle"), f"{path.name}.OutputNameStyle")
    assets = require_list(manifest.get("AssetList"), f"{path.name}.AssetList")
    bundles = require_list(manifest.get("BundleList"), f"{path.name}.BundleList")
    for index, raw in enumerate(assets):
        asset = require_dict(raw, f"{path.name}.AssetList[{index}]")
        for key in ("Address", "AssetPath", "AssetGUID"):
            require_str(asset.get(key), f"{path.name}.AssetList[{index}].{key}")
        require_string_list(
            asset.get("AssetTags"), f"{path.name}.AssetList[{index}].AssetTags"
        )
        require_int(asset.get("BundleID"), f"{path.name}.AssetList[{index}].BundleID")
    for index, raw in enumerate(bundles):
        bundle = require_dict(raw, f"{path.name}.BundleList[{index}]")
        require_str(bundle.get("BundleName"), f"{path.name}.BundleList[{index}].BundleName")
        require_int(bundle.get("UnityCRC"), f"{path.name}.BundleList[{index}].UnityCRC")
        require_str(bundle.get("FileHash"), f"{path.name}.BundleList[{index}].FileHash")
        require_str(bundle.get("FileCRC"), f"{path.name}.BundleList[{index}].FileCRC")
        file_size = require_int(
            bundle.get("FileSize"), f"{path.name}.BundleList[{index}].FileSize"
        )
        if file_size < 0:
            raise AuditError(f"{path.name}.BundleList[{index}].FileSize must be non-negative")
        require_bool(bundle.get("Encrypted"), f"{path.name}.BundleList[{index}].Encrypted")
        require_string_list(bundle.get("Tags"), f"{path.name}.BundleList[{index}].Tags")
        dependencies = require_list(
            bundle.get("DependIDs"), f"{path.name}.BundleList[{index}].DependIDs"
        )
        for dependency_index, dependency in enumerate(dependencies):
            require_int(
                dependency,
                f"{path.name}.BundleList[{index}].DependIDs[{dependency_index}]",
            )
    return manifest


def file_name_candidates(
    bundle: dict[str, Any], output_style: int, config: dict[str, Any]
) -> tuple[str, list[str]]:
    styles = config["output_name_styles"]
    style = styles.get(str(output_style))
    if style is None:
        style_name = f"unknown:{output_style}:fallback"
        templates = config["fallback_file_name_templates"]
    else:
        style_name = style["name"]
        templates = style["file_name_templates"]
    bundle_name = bundle["BundleName"]
    extension = Path(bundle_name).suffix
    values = {
        "FileHash": bundle["FileHash"],
        "BundleName": bundle_name,
        "BundleExtension": extension,
    }
    rendered: list[str] = []
    seen: set[str] = set()
    for template in templates:
        relative = render_safe_relative(template, values, "bundle filename template")
        if "/" in relative:
            raise AuditError(f"Bundle filename template must render one filename: {relative!r}")
        key = relative.casefold()
        if key not in seen:
            rendered.append(relative)
            seen.add(key)
    if not rendered:
        raise AuditError("Bundle filename template list rendered no candidates")
    return style_name, rendered


def classify_runtime_files(
    runtime_files: list[Path],
    package_dir: Path,
    matched_paths: set[Path],
    config: dict[str, Any],
) -> dict[str, Any]:
    metadata_patterns = config["runtime"]["metadata_patterns"]
    payload_extensions = {item.casefold() for item in config["runtime"]["payload_extensions"]}
    metadata: list[str] = []
    extra_payload: list[str] = []
    extra_unknown: list[str] = []
    for path in runtime_files:
        if path in matched_paths:
            continue
        relative = path.relative_to(package_dir).as_posix()
        if any(
            fnmatch.fnmatchcase(relative, pattern)
            or fnmatch.fnmatchcase(path.name, pattern)
            for pattern in metadata_patterns
        ):
            metadata.append(relative)
        elif path.suffix.casefold() in payload_extensions:
            extra_payload.append(relative)
        else:
            extra_unknown.append(relative)
    return {
        "metadata_files": metadata,
        "extra_payload_files": extra_payload,
        "extra_unknown_files": extra_unknown,
    }


def append_issue(
    issues: list[dict[str, Any]], limit: int, category: str, package: str, detail: str
) -> None:
    if len(issues) < limit:
        issues.append({"category": category, "package": package, "detail": detail})


def analyze(
    manifest_root: Path,
    streaming_root: Path,
    config_path: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_paths = discover_manifests(
        manifest_root, config["discovery"]["manifest_globs"]
    )
    snapshot_before, snapshot_before_sha256 = file_snapshot(streaming_root)
    asset_rows: list[dict[str, Any]] = []
    bundle_rows: list[dict[str, Any]] = []
    package_reports: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    max_issues = config["limits"]["max_issue_examples"]

    total_keys = [
        "manifest_count",
        "asset_count",
        "bundle_count",
        "declared_total_bytes",
        "matched_actual_total_bytes",
        "encrypted_bundle_count",
        "unencrypted_bundle_count",
        "missing_file_count",
        "extra_payload_file_count",
        "extra_unknown_file_count",
        "size_mismatch_count",
        "file_hash_mismatch_count",
        "file_crc_mismatch_count",
        "invalid_asset_bundle_id_count",
        "invalid_dependency_id_count",
        "self_dependency_count",
        "duplicate_dependency_id_count",
        "runtime_manifest_mismatch_count",
    ]
    totals = {key: 0 for key in total_keys}
    totals["manifest_count"] = len(manifest_paths)

    seen_package_names: set[str] = set()
    for manifest_path in manifest_paths:
        manifest = parse_manifest(manifest_path)
        package_name = manifest["PackageName"]
        package_key = package_name.casefold()
        if package_key in seen_package_names:
            raise AuditError(f"Duplicate PackageName across manifests: {package_name!r}")
        seen_package_names.add(package_key)
        package_version = manifest["PackageVersion"]
        output_style = manifest["OutputNameStyle"]
        assets = manifest["AssetList"]
        bundles = manifest["BundleList"]
        package_dir, package_dir_relative = resolve_package_dir(
            manifest_path,
            manifest_root,
            streaming_root,
            package_name,
            config["runtime"]["package_directory_candidates"],
        )
        runtime_files = safe_walk_files(
            package_dir, recursive=config["runtime"]["scan_recursive"]
        )
        runtime_by_name: dict[str, list[Path]] = {}
        for runtime_path in runtime_files:
            relative = runtime_path.relative_to(package_dir).as_posix()
            runtime_by_name.setdefault(relative.casefold(), []).append(runtime_path)

        archived_manifest_sha256 = hash_file(manifest_path)
        runtime_manifest_path = package_dir / manifest_path.name
        runtime_manifest_exists = runtime_manifest_path.is_file() and not is_reparse(
            runtime_manifest_path
        )
        runtime_manifest_sha256 = (
            hash_file(runtime_manifest_path) if runtime_manifest_exists else ""
        )
        runtime_manifest_matches = (
            runtime_manifest_exists and runtime_manifest_sha256 == archived_manifest_sha256
        )
        if not runtime_manifest_matches:
            totals["runtime_manifest_mismatch_count"] += 1
            append_issue(
                issues,
                max_issues,
                "runtime_manifest_mismatch",
                package_name,
                manifest_path.name,
            )

        package_counts = Counter()
        matched_paths: set[Path] = set()
        declared_names: dict[str, int] = {}
        duplicate_declared_names: list[str] = []
        extension_distribution: Counter[str] = Counter()

        for bundle_id, bundle in enumerate(bundles):
            depend_ids = bundle["DependIDs"]
            invalid_dependencies = [
                dependency
                for dependency in depend_ids
                if dependency < 0 or dependency >= len(bundles)
            ]
            self_dependencies = [dependency for dependency in depend_ids if dependency == bundle_id]
            duplicate_dependencies = len(depend_ids) - len(set(depend_ids))
            package_counts["invalid_dependency_id_count"] += len(invalid_dependencies)
            package_counts["self_dependency_count"] += len(self_dependencies)
            package_counts["duplicate_dependency_id_count"] += duplicate_dependencies
            for dependency in invalid_dependencies:
                append_issue(
                    issues,
                    max_issues,
                    "invalid_dependency_id",
                    package_name,
                    f"bundle_id={bundle_id} dependency={dependency} bundle_count={len(bundles)}",
                )
            if self_dependencies:
                append_issue(
                    issues,
                    max_issues,
                    "self_dependency",
                    package_name,
                    f"bundle_id={bundle_id}",
                )
            if duplicate_dependencies:
                append_issue(
                    issues,
                    max_issues,
                    "duplicate_dependency_id",
                    package_name,
                    f"bundle_id={bundle_id} depend_ids={compact_json(depend_ids)}",
                )

            style_name, candidates = file_name_candidates(bundle, output_style, config)
            primary_name = candidates[0]
            primary_key = primary_name.casefold()
            if primary_key in declared_names:
                duplicate_declared_names.append(primary_name)
            declared_names[primary_key] = bundle_id
            candidate_paths: list[tuple[Path, str]] = []
            for candidate in candidates:
                for actual_path in runtime_by_name.get(candidate.casefold(), []):
                    candidate_paths.append((actual_path, candidate))
            unique_matches = {path for path, _ in candidate_paths}
            resolved_path: Path | None = None
            resolution_candidate = ""
            ambiguous = len(unique_matches) > 1
            if len(unique_matches) == 1:
                resolved_path = next(iter(unique_matches))
                resolution_candidate = next(
                    candidate for path, candidate in candidate_paths if path == resolved_path
                )
                matched_paths.add(resolved_path)

            expected_hash = bundle["FileHash"].lower()
            file_crc_expected = bundle["FileCRC"].lower()
            hash_algorithm = config["hash_algorithms_by_hex_length"].get(
                str(len(expected_hash)), ""
            )
            hash_supported = bool(hash_algorithm and HEX_RE.fullmatch(expected_hash))
            crc_supported = bool(
                not file_crc_expected
                or (len(file_crc_expected) == 8 and HEX_RE.fullmatch(file_crc_expected))
            )
            actual_size: int | str = ""
            actual_hash = ""
            actual_crc = ""
            size_matches: bool | str = ""
            hash_matches: bool | str = ""
            crc_matches: bool | str = ""
            status_parts: list[str] = []
            if ambiguous:
                status_parts.append("ambiguous_file_match")
                append_issue(
                    issues,
                    max_issues,
                    "ambiguous_file_match",
                    package_name,
                    f"bundle_id={bundle_id} candidates={compact_json(candidates)}",
                )
            elif resolved_path is None:
                status_parts.append("missing")
                package_counts["missing_file_count"] += 1
                append_issue(
                    issues,
                    max_issues,
                    "missing_file",
                    package_name,
                    f"bundle_id={bundle_id} expected={primary_name}",
                )
            else:
                actual_size = resolved_path.stat().st_size
                package_counts["matched_actual_total_bytes"] += actual_size
                size_matches = actual_size == bundle["FileSize"]
                if not size_matches:
                    status_parts.append("size_mismatch")
                    package_counts["size_mismatch_count"] += 1
                if hash_supported:
                    actual_hash, actual_crc = hash_payload(
                        resolved_path, hash_algorithm, config["file_crc32_encoding"]
                    )
                    hash_matches = actual_hash == expected_hash
                    if not hash_matches:
                        status_parts.append("file_hash_mismatch")
                        package_counts["file_hash_mismatch_count"] += 1
                else:
                    status_parts.append("unsupported_file_hash")
                    hash_matches = False
                    package_counts["file_hash_mismatch_count"] += 1
                if file_crc_expected and crc_supported:
                    if not actual_crc:
                        _, actual_crc = hash_payload(
                            resolved_path,
                            hash_algorithm or "md5",
                            config["file_crc32_encoding"],
                        )
                    crc_matches = actual_crc == file_crc_expected
                    if not crc_matches:
                        status_parts.append("file_crc_mismatch")
                        package_counts["file_crc_mismatch_count"] += 1
                elif file_crc_expected:
                    status_parts.append("unsupported_file_crc")
                    crc_matches = False
                    package_counts["file_crc_mismatch_count"] += 1
                else:
                    crc_matches = ""
                for category in status_parts:
                    append_issue(
                        issues,
                        max_issues,
                        category,
                        package_name,
                        f"bundle_id={bundle_id} file={resolved_path.name}",
                    )

            file_status = "+".join(status_parts) if status_parts else "ok"
            extension = Path(bundle["BundleName"]).suffix.lower()
            extension_distribution[extension or "<none>"] += 1
            if bundle["Encrypted"]:
                package_counts["encrypted_bundle_count"] += 1
            else:
                package_counts["unencrypted_bundle_count"] += 1
            package_counts["declared_total_bytes"] += bundle["FileSize"]
            bundle_rows.append(
                {
                    "manifest_relative_path": manifest_path.relative_to(manifest_root).as_posix(),
                    "package_name": package_name,
                    "package_version": package_version,
                    "bundle_id": bundle_id,
                    "bundle_name": bundle["BundleName"],
                    "bundle_extension": extension,
                    "unity_crc": bundle["UnityCRC"],
                    "file_hash_algorithm": hash_algorithm,
                    "file_hash_expected": expected_hash,
                    "file_hash_actual": actual_hash,
                    "file_hash_matches": hash_matches,
                    "file_crc_expected": file_crc_expected,
                    "file_crc_actual": actual_crc,
                    "file_crc_matches": crc_matches,
                    "file_size_expected": bundle["FileSize"],
                    "file_size_actual": actual_size,
                    "file_size_matches": size_matches,
                    "encrypted": bundle["Encrypted"],
                    "tags_json": compact_json(bundle["Tags"]),
                    "depend_ids_json": compact_json(depend_ids),
                    "depend_count": len(depend_ids),
                    "invalid_depend_ids_json": compact_json(invalid_dependencies),
                    "self_dependency": bool(self_dependencies),
                    "duplicate_dependency_count": duplicate_dependencies,
                    "output_name_style": output_style,
                    "output_name_style_name": style_name,
                    "candidate_file_names_json": compact_json(candidates),
                    "declared_file_name": primary_name,
                    "resolved_file_relative_path": (
                        resolved_path.relative_to(streaming_root).as_posix()
                        if resolved_path is not None
                        else ""
                    ),
                    "resolution_candidate": resolution_candidate,
                    "file_status": file_status,
                }
            )

        for asset_index, asset in enumerate(assets):
            bundle_id = asset["BundleID"]
            valid_bundle_id = 0 <= bundle_id < len(bundles)
            if not valid_bundle_id:
                package_counts["invalid_asset_bundle_id_count"] += 1
                append_issue(
                    issues,
                    max_issues,
                    "invalid_asset_bundle_id",
                    package_name,
                    f"asset_index={asset_index} bundle_id={bundle_id} bundle_count={len(bundles)}",
                )
            bundle = bundles[bundle_id] if valid_bundle_id else None
            asset_rows.append(
                {
                    "manifest_relative_path": manifest_path.relative_to(manifest_root).as_posix(),
                    "package_name": package_name,
                    "package_version": package_version,
                    "asset_index": asset_index,
                    "address": asset["Address"],
                    "asset_path": asset["AssetPath"],
                    "asset_guid": asset["AssetGUID"],
                    "asset_tags_json": compact_json(asset["AssetTags"]),
                    "bundle_id": bundle_id,
                    "bundle_id_valid": valid_bundle_id,
                    "bundle_name": bundle["BundleName"] if bundle is not None else "",
                    "bundle_file_hash": bundle["FileHash"] if bundle is not None else "",
                }
            )

        classification = classify_runtime_files(
            runtime_files, package_dir, matched_paths, config
        )
        package_counts["extra_payload_file_count"] = len(
            classification["extra_payload_files"]
        )
        package_counts["extra_unknown_file_count"] = len(
            classification["extra_unknown_files"]
        )
        for relative in classification["extra_payload_files"]:
            append_issue(issues, max_issues, "extra_payload_file", package_name, relative)
        for relative in classification["extra_unknown_files"]:
            append_issue(issues, max_issues, "extra_unknown_file", package_name, relative)
        for name in duplicate_declared_names:
            append_issue(
                issues, max_issues, "duplicate_declared_filename", package_name, name
            )

        package_counts["asset_count"] = len(assets)
        package_counts["bundle_count"] = len(bundles)
        package_failed = any(
            package_counts[key]
            for key in (
                "missing_file_count",
                "extra_payload_file_count",
                "extra_unknown_file_count",
                "size_mismatch_count",
                "file_hash_mismatch_count",
                "file_crc_mismatch_count",
                "invalid_asset_bundle_id_count",
                "invalid_dependency_id_count",
                "self_dependency_count",
                "duplicate_dependency_id_count",
            )
        ) or not runtime_manifest_matches or bool(duplicate_declared_names)

        for key in total_keys:
            if key in {"manifest_count", "runtime_manifest_mismatch_count"}:
                continue
            totals[key] += package_counts[key]
        package_reports.append(
            {
                "manifest": {
                    "relative_path": manifest_path.relative_to(manifest_root).as_posix(),
                    "size": manifest_path.stat().st_size,
                    "sha256": archived_manifest_sha256,
                    "file_version": manifest["FileVersion"],
                },
                "package_name": package_name,
                "package_version": package_version,
                "output_name_style": output_style,
                "asset_count": len(assets),
                "bundle_count": len(bundles),
                "declared_total_bytes": package_counts["declared_total_bytes"],
                "matched_actual_total_bytes": package_counts[
                    "matched_actual_total_bytes"
                ],
                "encrypted_bundle_count": package_counts["encrypted_bundle_count"],
                "unencrypted_bundle_count": package_counts["unencrypted_bundle_count"],
                "bundle_extension_distribution": dict(sorted(extension_distribution.items())),
                "boundary_validation": {
                    key: package_counts[key]
                    for key in (
                        "invalid_asset_bundle_id_count",
                        "invalid_dependency_id_count",
                        "self_dependency_count",
                        "duplicate_dependency_id_count",
                    )
                },
                "runtime_manifest": {
                    "relative_path": (
                        runtime_manifest_path.relative_to(streaming_root).as_posix()
                        if runtime_manifest_exists
                        else ""
                    ),
                    "exists": runtime_manifest_exists,
                    "sha256": runtime_manifest_sha256,
                    "matches_archived_manifest": runtime_manifest_matches,
                },
                "runtime_files": {
                    "package_directory": package_dir_relative,
                    "scanned_file_count": len(runtime_files),
                    "matched_payload_file_count": len(matched_paths),
                    "metadata_file_count": len(classification["metadata_files"]),
                    "metadata_files": classification["metadata_files"],
                    "extra_payload_file_count": len(
                        classification["extra_payload_files"]
                    ),
                    "extra_payload_files": classification["extra_payload_files"],
                    "extra_unknown_file_count": len(
                        classification["extra_unknown_files"]
                    ),
                    "extra_unknown_files": classification["extra_unknown_files"],
                },
                "duplicate_declared_filenames": duplicate_declared_names,
                "validation_passed": not package_failed,
            }
        )

    snapshot_after, snapshot_after_sha256 = file_snapshot(streaming_root)
    source_unchanged = snapshot_before == snapshot_after
    if not source_unchanged:
        append_issue(
            issues,
            max_issues,
            "source_changed_during_analysis",
            "<all>",
            "streaming assets file identity/size/mtime snapshot changed",
        )
    failed_checks = {
        key: value
        for key, value in totals.items()
        if key
        not in {
            "manifest_count",
            "asset_count",
            "bundle_count",
            "declared_total_bytes",
            "matched_actual_total_bytes",
            "encrypted_bundle_count",
            "unencrypted_bundle_count",
        }
        and value
    }
    if not source_unchanged:
        failed_checks["source_changed_during_analysis"] = 1
    all_checks_passed = not failed_checks and all(
        package["validation_passed"] for package in package_reports
    )
    report = {
        "schema_version": 1,
        "mode": "read_only_yooasset_manifest_audit",
        "inputs": {
            "manifest_root": str(manifest_root.resolve(strict=True)),
            "streaming_assets_root": str(streaming_root.resolve(strict=True)),
            "config": str(config_path.resolve(strict=True)),
            "config_sha256": hash_file(config_path),
        },
        "source_integrity": {
            "snapshot_basis": "relative_path,size,mtime_ns,device,inode",
            "file_count_before": len(snapshot_before),
            "file_count_after": len(snapshot_after),
            "snapshot_before_sha256": snapshot_before_sha256,
            "snapshot_after_sha256": snapshot_after_sha256,
            "unchanged_during_analysis": source_unchanged,
        },
        "totals": totals,
        "packages": package_reports,
        "issues": issues,
        "issue_examples_truncated": len(issues) >= max_issues and bool(failed_checks),
        "validation": {
            "all_checks_passed": all_checks_passed,
            "failed_checks": failed_checks,
            "fail_on_mismatch": config["validation"]["fail_on_mismatch"],
        },
        "boundaries": {
            "bundle_contents_extracted": False,
            "encryption_interpreted_as_manifest_declaration_only": True,
            "asset_values_included_for_local_authorized_traceability": True,
        },
    }
    return report, asset_rows, bundle_rows


ASSET_FIELDS = [
    "manifest_relative_path",
    "package_name",
    "package_version",
    "asset_index",
    "address",
    "asset_path",
    "asset_guid",
    "asset_tags_json",
    "bundle_id",
    "bundle_id_valid",
    "bundle_name",
    "bundle_file_hash",
]

BUNDLE_FIELDS = [
    "manifest_relative_path",
    "package_name",
    "package_version",
    "bundle_id",
    "bundle_name",
    "bundle_extension",
    "unity_crc",
    "file_hash_algorithm",
    "file_hash_expected",
    "file_hash_actual",
    "file_hash_matches",
    "file_crc_expected",
    "file_crc_actual",
    "file_crc_matches",
    "file_size_expected",
    "file_size_actual",
    "file_size_matches",
    "encrypted",
    "tags_json",
    "depend_ids_json",
    "depend_count",
    "invalid_depend_ids_json",
    "self_dependency",
    "duplicate_dependency_count",
    "output_name_style",
    "output_name_style_name",
    "candidate_file_names_json",
    "declared_file_name",
    "resolved_file_relative_path",
    "resolution_candidate",
    "file_status",
]


def atomic_write_text(path: Path, text: str, *, encoding: str) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_text(text, encoding=encoding, newline="")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def csv_text(rows: Iterable[dict[str, Any]], fields: list[str]) -> str:
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# YooAsset Manifest 只读审计摘要",
        "",
        "## 观察到的事实",
        "",
        f"- Manifest：{totals['manifest_count']} 个；Asset：{totals['asset_count']} 条；Bundle：{totals['bundle_count']} 条。",
        f"- Manifest 声明总字节：{totals['declared_total_bytes']}；已匹配实际总字节：{totals['matched_actual_total_bytes']}。",
        f"- 加密状态（仅按 manifest 声明）：Encrypted=true {totals['encrypted_bundle_count']}，false {totals['unencrypted_bundle_count']}。",
        f"- 文件对账：缺失 {totals['missing_file_count']}，额外 payload {totals['extra_payload_file_count']}，额外未知文件 {totals['extra_unknown_file_count']}。",
        f"- 校验不一致：大小 {totals['size_mismatch_count']}，FileHash {totals['file_hash_mismatch_count']}，FileCRC {totals['file_crc_mismatch_count']}。",
        f"- ID 边界：Asset BundleID 越界 {totals['invalid_asset_bundle_id_count']}，DependIDs 越界 {totals['invalid_dependency_id_count']}，自依赖 {totals['self_dependency_count']}，重复依赖 {totals['duplicate_dependency_id_count']}。",
        f"- 源读取期间快照不变：{'是' if report['source_integrity']['unchanged_during_analysis'] else '否'}。",
        f"- 全部审计检查通过：{'是' if report['validation']['all_checks_passed'] else '否'}。",
        "",
        "| 包 | 版本 | Asset | Bundle | 声明字节 | 匹配字节 | Encrypted=true | 包校验 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for package in report["packages"]:
        lines.append(
            f"| {package['package_name']} | {package['package_version']} | "
            f"{package['asset_count']} | {package['bundle_count']} | "
            f"{package['declared_total_bytes']} | {package['matched_actual_total_bytes']} | "
            f"{package['encrypted_bundle_count']} | "
            f"{'通过' if package['validation_passed'] else '未通过'} |"
        )
    lines.extend(
        [
            "",
            "## 我的判断",
            "",
            "- AssetList 的 `BundleID` 与 BundleList 的零基索引可形成可追溯映射；本报告只在边界校验通过时填充关联 Bundle 字段。",
            "- 物理文件名按配置中的 `OutputNameStyle` 模板解释；大小、FileHash 和 FileCRC 均以实际文件字节复算，不从文件名反推校验成功。",
            "- `Encrypted` 仅表示 manifest 声明状态，不代表已验证加密算法、密钥或运行时解密行为。",
            "",
            "## 待验证假设",
            "",
            "- 未知 `OutputNameStyle` 会使用配置化 fallback；若未来 YooAsset 版本新增命名枚举，应先补配置和合成测试，再解释物理文件名。",
            "- 本流程没有打开、提取或修改 bundle 内部内容，无法据此判断其中具体 Unity 对象是否可读。",
            "",
            "## 边界",
            "",
            "- 输入只读；输出仅为本地授权学习用 CSV、JSON 和中文 Markdown 索引。",
            "- 详细字段值保留在 CSV 以便本地追溯，摘要不复制大段原始 manifest。",
        ]
    )
    if report["issues"]:
        lines.extend(
            [
                "",
                "## 问题样例",
                "",
                "| 类别 | 包 | 详情 |",
                "|---|---|---|",
            ]
        )
        for issue in report["issues"]:
            detail = str(issue["detail"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {issue['category']} | {issue['package']} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    output_dir: Path,
    config: dict[str, Any],
    report: dict[str, Any],
    asset_rows: list[dict[str, Any]],
    bundle_rows: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = config["outputs"]
    atomic_write_text(
        output_dir / outputs["asset_csv"],
        "\ufeff" + csv_text(asset_rows, ASSET_FIELDS),
        encoding="utf-8",
    )
    atomic_write_text(
        output_dir / outputs["bundle_csv"],
        "\ufeff" + csv_text(bundle_rows, BUNDLE_FIELDS),
        encoding="utf-8",
    )
    atomic_write_text(
        output_dir / outputs["summary_json"],
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    atomic_write_text(
        output_dir / outputs["summary_markdown"],
        build_markdown(report),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    default_config = (
        Path(__file__).resolve().parents[1]
        / "references"
        / "yooasset-manifest-audit-config.json"
    )
    parser = argparse.ArgumentParser(
        description="只读映射 YooAsset JSON manifest，并对账 StreamingAssets payload"
    )
    parser.add_argument("--manifest-root", required=True, type=Path)
    parser.add_argument("--streaming-assets-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=default_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        validate_paths(args.manifest_root, args.streaming_assets_root, args.output_dir)
        report, asset_rows, bundle_rows = analyze(
            args.manifest_root,
            args.streaming_assets_root,
            args.config,
            config,
        )
        write_outputs(args.output_dir, config, report, asset_rows, bundle_rows)
    except (AuditError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "manifest_count": report["totals"]["manifest_count"],
                "asset_count": report["totals"]["asset_count"],
                "bundle_count": report["totals"]["bundle_count"],
                "all_checks_passed": report["validation"]["all_checks_passed"],
                "output_dir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    if config["validation"]["fail_on_mismatch"] and not report["validation"][
        "all_checks_passed"
    ]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

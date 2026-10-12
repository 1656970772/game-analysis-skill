#!/usr/bin/env python3
"""Build a read-only structural map of loose JSON tables without emitting values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


CSV_FIELDS = [
    "source_rel",
    "bytes",
    "sha256",
    "parse_status",
    "encoding",
    "top_level_type",
    "record_count",
    "top_level_key_count",
    "sample_keys",
    "filename_module",
    "filename_table",
    "filename_pattern_matched",
    "error_detail",
]

WINDOWS_REPARSE_POINT = 0x0400
ALLOWED_DECODERS = {"utf-8-bom", "utf-8", "gb18030"}


class ConfigError(ValueError):
    """Raised when the analysis configuration is unsafe or inconsistent."""


class PathSafetyError(ValueError):
    """Raised when source/output path boundaries cannot be accepted."""


class NonStandardJSONConstant(ValueError):
    """Raised for NaN/Infinity constants accepted by Python's default decoder."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_config = Path(__file__).resolve().parent.parent / "references" / "loose-json-analysis-config.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Read-only source base directory")
    parser.add_argument("--output", type=Path, required=True, help="Artifact output directory")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument(
        "--scan-root",
        action="append",
        dest="scan_roots",
        help="Source-relative directory to scan; repeat to override config scan roots",
    )
    parser.add_argument("--expected-file-count", type=int)
    parser.add_argument("--expected-total-bytes", type=int)
    parser.add_argument("--inventory-name", help="Override configured CSV basename")
    parser.add_argument("--summary-name", help="Override configured summary JSON basename")
    parser.add_argument("--report-name", help="Override configured Markdown basename")
    return parser.parse_args(argv)


def reject_nonstandard_constant(_value: str) -> None:
    raise NonStandardJSONConstant("non-standard JSON constant rejected")


def load_strict_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"cannot read config: {path}: {type(exc).__name__}") from exc
    try:
        return json.loads(text, parse_constant=reject_nonstandard_constant)
    except (json.JSONDecodeError, NonStandardJSONConstant) as exc:
        raise ConfigError(f"invalid strict JSON config: {path}: {type(exc).__name__}") from exc


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value


def require_nonnegative_int(value: Any, label: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{label} must be a non-negative integer" + (" or null" if allow_none else ""))
    return value


def validate_output_basename(value: Any, label: str, suffix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty basename")
    candidate = Path(value)
    if candidate.name != value or candidate.is_absolute() or candidate.drive or value in {".", ".."}:
        raise ConfigError(f"{label} must be a basename without directories: {value!r}")
    if candidate.suffix.casefold() != suffix.casefold():
        raise ConfigError(f"{label} must end with {suffix}: {value!r}")
    return value


def validate_relative_root(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("scan.relative_roots entries must be non-empty strings")
    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
        raise ConfigError(f"scan root must be source-relative without '..': {value!r}")
    return value


def validate_config(raw: Any, args: argparse.Namespace) -> dict[str, Any]:
    config = require_mapping(raw, "config")
    scan = require_mapping(config.get("scan"), "scan")
    expected = require_mapping(config.get("expected"), "expected")
    naming = require_mapping(config.get("naming"), "naming")
    limits = require_mapping(config.get("limits"), "limits")
    sampling = require_mapping(config.get("sampling"), "sampling")
    outputs = require_mapping(config.get("outputs"), "outputs")
    privacy = require_mapping(config.get("privacy"), "privacy")

    configured_roots = args.scan_roots if args.scan_roots is not None else scan.get("relative_roots")
    if not isinstance(configured_roots, list) or not configured_roots:
        raise ConfigError("scan.relative_roots must be a non-empty array")
    relative_roots = [validate_relative_root(value) for value in configured_roots]

    extension = scan.get("extension")
    if not isinstance(extension, str) or not extension.startswith(".") or len(extension) < 2:
        raise ConfigError("scan.extension must be a file extension beginning with '.'")

    decoding_order = config.get("decoding_order")
    if not isinstance(decoding_order, list) or not decoding_order:
        raise ConfigError("decoding_order must be a non-empty array")
    if any(not isinstance(item, str) or item not in ALLOWED_DECODERS for item in decoding_order):
        raise ConfigError(f"decoding_order may only contain {sorted(ALLOWED_DECODERS)}")
    if len(set(decoding_order)) != len(decoding_order):
        raise ConfigError("decoding_order must not contain duplicates")

    pattern_text = naming.get("pattern")
    module_group = naming.get("module_group")
    table_group = naming.get("table_group")
    if not all(isinstance(item, str) and item for item in (pattern_text, module_group, table_group)):
        raise ConfigError("naming pattern/module_group/table_group must be non-empty strings")
    flags = re.IGNORECASE if naming.get("ignore_case", False) else 0
    try:
        filename_pattern = re.compile(pattern_text, flags)
    except re.error as exc:
        raise ConfigError(f"invalid naming.pattern: {exc}") from exc
    for group_name in (module_group, table_group):
        if group_name not in filename_pattern.groupindex:
            raise ConfigError(f"naming.pattern is missing named group {group_name!r}")
    unmatched_module = naming.get("unmatched_module", "")
    if not isinstance(unmatched_module, str):
        raise ConfigError("naming.unmatched_module must be a string")

    limit_names = [
        "sample_key_count",
        "sample_key_characters",
        "error_characters",
        "largest_table_count",
        "module_top_count",
        "sensitive_field_top_count",
        "system_example_count",
        "matched_keyword_top_count",
    ]
    checked_limits = {
        name: require_nonnegative_int(limits.get(name), f"limits.{name}") for name in limit_names
    }

    raw_buckets = config.get("record_count_buckets")
    if not isinstance(raw_buckets, list) or not raw_buckets:
        raise ConfigError("record_count_buckets must be a non-empty array")
    buckets: list[dict[str, Any]] = []
    labels: set[str] = set()
    for index, item in enumerate(raw_buckets):
        bucket = require_mapping(item, f"record_count_buckets[{index}]")
        low = require_nonnegative_int(bucket.get("min"), f"record_count_buckets[{index}].min")
        high = require_nonnegative_int(
            bucket.get("max"), f"record_count_buckets[{index}].max", allow_none=True
        )
        label = bucket.get("label")
        if not isinstance(label, str) or not label or label in labels:
            raise ConfigError("record count bucket labels must be unique non-empty strings")
        if high is not None and high < low:
            raise ConfigError(f"record count bucket {label!r} has max < min")
        labels.add(label)
        buckets.append({"min": low, "max": high, "label": label})
    for left, right in zip(buckets, buckets[1:]):
        if left["max"] is None or right["min"] != left["max"] + 1:
            raise ConfigError("record_count_buckets must be ordered, contiguous, and non-overlapping")
    if buckets[0]["min"] != 0 or buckets[-1]["max"] is not None:
        raise ConfigError("record_count_buckets must cover every non-negative integer")

    sample_size = require_nonnegative_int(sampling.get("sha256_sample_size"), "sampling.sha256_sample_size")
    sample_seed = sampling.get("sha256_seed")
    if not isinstance(sample_seed, (str, int)) or isinstance(sample_seed, bool):
        raise ConfigError("sampling.sha256_seed must be a string or integer")

    systems = config.get("system_candidates")
    if not isinstance(systems, dict):
        raise ConfigError("system_candidates must be an object")
    checked_systems: dict[str, list[str]] = {}
    for system_name, keywords in systems.items():
        if not isinstance(system_name, str) or not system_name:
            raise ConfigError("system candidate names must be non-empty strings")
        if not isinstance(keywords, list) or any(not isinstance(word, str) or not word for word in keywords):
            raise ConfigError(f"system_candidates.{system_name} must be an array of non-empty strings")
        checked_systems[system_name] = keywords

    raw_sensitive_patterns = privacy.get("sensitive_field_name_patterns")
    if not isinstance(raw_sensitive_patterns, list):
        raise ConfigError("privacy.sensitive_field_name_patterns must be an array")
    sensitive_patterns: list[dict[str, Any]] = []
    for index, item in enumerate(raw_sensitive_patterns):
        entry = require_mapping(item, f"privacy.sensitive_field_name_patterns[{index}]")
        target = entry.get("target")
        pattern = entry.get("pattern")
        if target not in {"raw", "normalized"} or not isinstance(pattern, str) or not pattern:
            raise ConfigError("sensitive field patterns require target raw/normalized and a pattern")
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ConfigError(f"invalid sensitive field pattern at index {index}: {exc}") from exc
        sensitive_patterns.append({"target": target, "pattern": pattern, "compiled": compiled})

    expected_file_count = (
        args.expected_file_count
        if args.expected_file_count is not None
        else require_nonnegative_int(expected.get("file_count"), "expected.file_count", allow_none=True)
    )
    expected_total_bytes = (
        args.expected_total_bytes
        if args.expected_total_bytes is not None
        else require_nonnegative_int(expected.get("total_bytes"), "expected.total_bytes", allow_none=True)
    )
    expected_file_count = require_nonnegative_int(
        expected_file_count, "--expected-file-count", allow_none=True
    )
    expected_total_bytes = require_nonnegative_int(
        expected_total_bytes, "--expected-total-bytes", allow_none=True
    )

    output_names = {
        "inventory_csv": validate_output_basename(
            args.inventory_name or outputs.get("inventory_csv"), "outputs.inventory_csv", ".csv"
        ),
        "summary_json": validate_output_basename(
            args.summary_name or outputs.get("summary_json"), "outputs.summary_json", ".json"
        ),
        "analysis_markdown": validate_output_basename(
            args.report_name or outputs.get("analysis_markdown"), "outputs.analysis_markdown", ".md"
        ),
    }
    if len(set(name.casefold() for name in output_names.values())) != len(output_names):
        raise ConfigError("configured output basenames must be distinct")

    return {
        "schema_version": str(config.get("schema_version", "1.0")),
        "scan": {"relative_roots": relative_roots, "extension": extension.casefold()},
        "expected": {"file_count": expected_file_count, "total_bytes": expected_total_bytes},
        "decoding_order": decoding_order,
        "naming": {
            "pattern": pattern_text,
            "compiled": filename_pattern,
            "module_group": module_group,
            "table_group": table_group,
            "unmatched_module": unmatched_module,
        },
        "limits": checked_limits,
        "record_count_buckets": buckets,
        "sampling": {"sha256_sample_size": sample_size, "sha256_seed": str(sample_seed)},
        "outputs": output_names,
        "system_candidates": checked_systems,
        "privacy": {"sensitive_field_name_patterns": sensitive_patterns},
    }


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )


def assert_no_link_or_reparse_components(path: Path, label: str) -> None:
    absolute = lexical_absolute(path)
    chain = list(reversed(absolute.parents)) + [absolute]
    for component in chain:
        if os.path.lexists(os.fspath(component)) and is_link_or_reparse(component):
            raise PathSafetyError(f"{label} uses a symlink or Windows reparse point: {component}")


def path_is_within(child: Path, parent: Path) -> bool:
    try:
        common = os.path.commonpath(
            [os.path.normcase(os.fspath(child)), os.path.normcase(os.fspath(parent))]
        )
    except ValueError:
        return False
    return common == os.path.normcase(os.fspath(parent))


def resolve_paths(args: argparse.Namespace, config: dict[str, Any]) -> tuple[Path, Path, list[Path]]:
    source_lexical = lexical_absolute(args.source)
    output_lexical = lexical_absolute(args.output)
    assert_no_link_or_reparse_components(source_lexical, "source")
    assert_no_link_or_reparse_components(output_lexical, "output")
    if not source_lexical.is_dir():
        raise PathSafetyError(f"source directory does not exist: {source_lexical}")
    source = source_lexical.resolve(strict=True)
    output = output_lexical.resolve(strict=False)
    if path_is_within(output, source):
        raise PathSafetyError(f"output must not be the source directory or inside it: {output}")

    scan_roots: list[Path] = []
    seen: set[str] = set()
    for relative in config["scan"]["relative_roots"]:
        candidate_lexical = lexical_absolute(source / relative)
        assert_no_link_or_reparse_components(candidate_lexical, "scan root")
        if not candidate_lexical.is_dir():
            raise PathSafetyError(f"configured scan root does not exist: {candidate_lexical}")
        candidate = candidate_lexical.resolve(strict=True)
        if not path_is_within(candidate, source):
            raise PathSafetyError(f"configured scan root escapes source: {relative!r}")
        key = os.path.normcase(os.fspath(candidate))
        if key not in seen:
            seen.add(key)
            scan_roots.append(candidate)

    output.mkdir(parents=True, exist_ok=True)
    assert_no_link_or_reparse_components(output, "output")
    return source, output.resolve(strict=True), scan_roots


def source_relative(path: Path, source: Path) -> str:
    return path.relative_to(source).as_posix()


def discover_json_files(
    source: Path, scan_roots: Sequence[Path], extension: str
) -> tuple[list[Path], list[str], list[str]]:
    discovered: dict[str, Path] = {}
    skipped_special: set[str] = set()
    walk_errors: list[str] = []

    def on_walk_error(exc: OSError) -> None:
        filename = Path(exc.filename) if exc.filename else source
        try:
            rel = source_relative(filename, source)
        except ValueError:
            rel = "<outside-source>"
        walk_errors.append(f"{rel}: {type(exc).__name__}")

    for scan_root in scan_roots:
        for directory, dir_names, file_names in os.walk(
            scan_root, topdown=True, followlinks=False, onerror=on_walk_error
        ):
            directory_path = Path(directory)
            retained_dirs: list[str] = []
            for name in sorted(dir_names, key=str.casefold):
                candidate = directory_path / name
                if is_link_or_reparse(candidate):
                    skipped_special.add(source_relative(candidate, source))
                else:
                    retained_dirs.append(name)
            dir_names[:] = retained_dirs
            for name in sorted(file_names, key=str.casefold):
                candidate = directory_path / name
                if candidate.suffix.casefold() != extension:
                    continue
                if is_link_or_reparse(candidate):
                    skipped_special.add(source_relative(candidate, source))
                    continue
                rel = source_relative(candidate, source)
                discovered[rel.casefold()] = candidate
    files = sorted(discovered.values(), key=lambda path: source_relative(path, source).casefold())
    return files, sorted(skipped_special, key=str.casefold), sorted(walk_errors, key=str.casefold)


def build_source_snapshot(
    source: Path, files: Sequence[Path], skipped_special: Sequence[str], walk_errors: Sequence[str]
) -> dict[str, Any]:
    digest = hashlib.sha256()
    total_bytes = 0
    stat_failures: list[str] = []
    for path in files:
        rel = source_relative(path, source)
        try:
            info = path.stat()
        except OSError as exc:
            stat_failures.append(f"{rel}: {type(exc).__name__}")
            size = 0
            modified_ns = -1
        else:
            size = info.st_size
            modified_ns = info.st_mtime_ns
        total_bytes += size
        digest.update(rel.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(modified_ns).encode("ascii"))
        digest.update(b"\n")
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "listing_metadata_sha256": digest.hexdigest(),
        "stat_failure_count": len(stat_failures),
        "stat_failures": stat_failures,
        "skipped_link_or_reparse_count": len(skipped_special),
        "skipped_link_or_reparse_entries": list(skipped_special),
        "walk_error_count": len(walk_errors),
        "walk_errors": list(walk_errors),
    }


def decode_bytes(raw: bytes, decoding_order: Sequence[str]) -> tuple[str | None, str, str]:
    errors: list[str] = []
    for configured_name in decoding_order:
        if configured_name == "utf-8-bom":
            if not raw.startswith(b"\xef\xbb\xbf"):
                continue
            codec = "utf-8-sig"
        else:
            codec = configured_name
        try:
            return raw.decode(codec), configured_name, ""
        except UnicodeDecodeError as exc:
            errors.append(f"{configured_name}: byte_offset={exc.start}")
    return None, "unresolved", "; ".join(errors)


def json_error_detail(exc: BaseException, limit: int) -> str:
    if isinstance(exc, json.JSONDecodeError):
        detail = (
            f"JSONDecodeError: {exc.msg}; line={exc.lineno}; "
            f"column={exc.colno}; char_offset={exc.pos}"
        )
    elif isinstance(exc, NonStandardJSONConstant):
        detail = "NonStandardJSONConstant: NaN/Infinity constants are rejected"
    elif isinstance(exc, RecursionError):
        detail = "RecursionError: JSON nesting exceeds parser capacity"
    else:
        detail = type(exc).__name__
    return detail[:limit]


def top_level_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def structural_record_count(value: Any) -> int:
    if isinstance(value, (dict, list)):
        return len(value)
    return 0 if value is None else 1


def collect_key_counts(value: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                counts[str(key)] += 1
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)
    return counts


def compact_sample_keys(value: Any, count_limit: int, char_limit: int) -> str:
    prefix = ""
    keys: list[str] = []
    if isinstance(value, dict):
        keys = [str(key) for key in value]
    elif isinstance(value, list):
        first_mapping = next((item for item in value[:20] if isinstance(item, dict)), None)
        if first_mapping is not None:
            prefix = "item."
            keys = [str(key) for key in first_mapping]
    compact = json.dumps([prefix + key for key in keys[:count_limit]], ensure_ascii=False)
    if len(compact) <= char_limit:
        return compact
    if char_limit == 0:
        return ""
    return compact[: max(0, char_limit - 1)] + "…"


def normalize_key_name(key: str) -> str:
    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", with_boundaries).strip("_").casefold()


def is_sensitive_field_name(key: str, patterns: Sequence[dict[str, Any]]) -> bool:
    normalized = normalize_key_name(key)
    for entry in patterns:
        target = key if entry["target"] == "raw" else normalized
        if entry["compiled"].search(target):
            return True
    return False


def keyword_hit(text: str, keyword: str) -> bool:
    folded_text = text.casefold()
    folded_keyword = keyword.casefold()
    if re.search(r"[\u4e00-\u9fff]", folded_keyword):
        return folded_keyword in folded_text
    if len(folded_keyword) <= 3:
        return re.search(
            rf"(?<![a-z]){re.escape(folded_keyword)}(?=$|[^a-z])", folded_text
        ) is not None
    return folded_keyword in folded_text


def matched_keywords(texts: Iterable[str], keywords: Sequence[str]) -> list[str]:
    materialized = list(texts)
    return sorted(
        {word for word in keywords if any(keyword_hit(text, word) for text in materialized)},
        key=str.casefold,
    )


def bucket_label(record_count: int, buckets: Sequence[dict[str, Any]]) -> str:
    for bucket in buckets:
        if record_count >= bucket["min"] and (
            bucket["max"] is None or record_count <= bucket["max"]
        ):
            return bucket["label"]
    raise AssertionError(f"record count was not covered by configured buckets: {record_count}")


def atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def csv_text(rows: Sequence[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def verify_csv(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "data_rows": len(rows),
        "bytes_sum": sum(int(row["bytes"]) for row in rows),
    }


def deterministic_sha256_sample(
    source: Path, rows: Sequence[dict[str, Any]], sample_size: int, seed: str
) -> dict[str, Any]:
    candidates = [row for row in rows if row["sha256"]]
    ranked = sorted(
        candidates,
        key=lambda row: (
            hashlib.sha256(f"{seed}\0{row['source_rel']}".encode("utf-8")).hexdigest(),
            str(row["source_rel"]).casefold(),
        ),
    )
    selected = ranked[: min(sample_size, len(ranked))]
    checks: list[dict[str, Any]] = []
    for row in selected:
        path = source / Path(row["source_rel"])
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            actual = ""
            error = type(exc).__name__
        else:
            error = ""
        checks.append(
            {
                "source_rel": row["source_rel"],
                "indexed_sha256": row["sha256"],
                "actual_sha256": actual,
                "match": actual == row["sha256"],
                "error": error,
            }
        )
    return {
        "selection_method": "ascending SHA256(seed + NUL + source_rel)",
        "seed": seed,
        "requested_sample_size": sample_size,
        "available_candidate_count": len(candidates),
        "actual_sample_size": len(checks),
        "match_count": sum(1 for item in checks if item["match"]),
        "all_match": all(item["match"] for item in checks),
        "checks": checks,
    }


def markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        output.append("| " + " | ".join(markdown_cell(value) for value in row) + " |")
    return output


def expectation_text(expected: int | None, actual: int) -> str:
    if expected is None:
        return "未配置"
    return f"{expected:,}（{'匹配' if expected == actual else '不匹配'}）"


def build_report(summary: dict[str, Any]) -> str:
    scan = summary["scan"]
    parsing = summary["parsing"]
    naming = summary["naming"]
    validation = summary["validation"]
    sample = validation["sha256_sample"]
    lines: list[str] = [
        "# loose JSON 数据地图",
        "",
        "> 仅统计授权源中的路径、字节、哈希、结构、文件名和字段名；不复制 JSON 字段值。",
        "",
        "## 观察到的事实",
        "",
        f"- 扫描源：`{summary['source_root']}`。",
        f"- 配置扫描根：{', '.join(f'`{item}`' for item in summary['scan_relative_roots'])}。",
        f"- 扫描到 **{scan['file_count']:,}** 个 JSON，共 **{scan['total_bytes']:,}** 字节。",
        f"- 预期文件数：{expectation_text(scan['expected_file_count'], scan['file_count'])}；预期字节：{expectation_text(scan['expected_total_bytes'], scan['total_bytes'])}。",
        f"- 严格 JSON 解析成功 **{parsing['success_count']:,}** 个，失败 **{parsing['failure_count']:,}** 个；`NaN`/`Infinity` 被拒绝。",
        f"- 文件名规则匹配 **{naming['matched_count']:,}** 个，未匹配 **{naming['unmatched_count']:,}** 个；未匹配项保留完整 stem。",
        f"- 固定种子 SHA-256 抽样复算 {sample['actual_sample_size']} 项，匹配 {sample['match_count']} 项。",
        f"- 源前后清单元数据摘要{'一致' if validation['source_snapshot_unchanged'] else '不一致'}。",
        "",
        "### 编码分布",
        "",
    ]
    lines.extend(
        markdown_table(
            ["编码", "文件数"],
            sorted(parsing["encoding_distribution"].items(), key=lambda item: (-item[1], item[0])),
        )
    )
    lines.extend(["", "### 顶层类型分布", ""])
    lines.extend(
        markdown_table(
            ["顶层类型", "文件数"],
            sorted(
                parsing["top_level_type_distribution"].items(),
                key=lambda item: (-item[1], item[0]),
            ),
        )
    )
    lines.extend(["", "### record_count 分桶", ""])
    lines.extend(
        markdown_table(
            ["结构计数区间", "文件数"],
            parsing["record_count_bucket_distribution"].items(),
        )
    )
    lines.extend(
        [
            "",
            "`record_count` 仅指 object/array 顶层成员数（标量为 1、null 为 0），不是运行时实体数。",
            "",
            "### 模块文件数 TOP",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["模块", "文件数", "字节"],
            (
                (item["module"], item["file_count"], f"{item['bytes']:,}")
                for item in naming["module_top"]
            ),
        )
    )
    lines.extend(["", "### 最大表（按文件字节）", ""])
    lines.extend(
        markdown_table(
            ["模块", "表", "字节", "record_count", "顶层类型", "源相对路径"],
            (
                (
                    item["filename_module"] or "<unmatched>",
                    item["filename_table"],
                    f"{item['bytes']:,}",
                    item["record_count"],
                    item["top_level_type"],
                    item["source_rel"],
                )
                for item in summary["largest_tables_by_bytes"]
            ),
        )
    )
    lines.extend(["", "### 解析失败清单", ""])
    if parsing["failures"]:
        lines.extend(
            markdown_table(
                ["源相对路径", "状态", "编码", "错误类型/位置"],
                (
                    (
                        item["source_rel"],
                        item["parse_status"],
                        item["encoding"],
                        item["error_detail"],
                    )
                    for item in parsing["failures"]
                ),
            )
        )
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "### 系统候选（仅词法信号）",
            "",
            "文件名与嵌套字段名的关键词命中可重叠；它不是运行时调用链或业务边界证明。",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["候选系统", "命中文件", "文件名命中", "键名命中", "字节", "示例路径"],
            (
                (
                    name,
                    info["file_count"],
                    info["filename_signal_file_count"],
                    info["key_signal_file_count"],
                    f"{info['bytes']:,}",
                    "；".join(info["examples"]),
                )
                for name, info in summary["system_candidates"].items()
            ),
        )
    )
    lines.extend(
        [
            "",
            "### 潜在敏感字段名信号",
            "",
            "仅展示字段名和计数，不展示字段值；命中只表示配置模式信号。",
            "",
        ]
    )
    sensitive = summary["sensitive_field_name_signals"]
    if sensitive:
        lines.extend(
            markdown_table(
                ["字段名", "出现次数", "涉及文件数"],
                (
                    (item["field_name"], item["occurrences"], item["file_count"])
                    for item in sensitive
                ),
            )
        )
    else:
        lines.append("未命中配置模式。")

    lines.extend(
        [
            "",
            "## 我的判断",
            "",
            "- 规则化文件名、模块规模和顶层结构适合用作后续代码/资源交叉检索入口。",
            "- 文件字节和结构计数只能帮助安排分析优先级，不能直接代表运行时热度、玩法重要性或代码复杂度。",
            "",
            "## 待验证假设",
            "",
            "- 文件名拆出的模块/表可能对应生成链或加载边界，但仍需加载代码或运行时证据确认。",
            "- 顶层成员可能对应记录；只有验证 ID 唯一性、引用关系和加载方式后才能解释为实体数量。",
            "- 词法系统候选需要与代码引用图或运行时行为交叉验证。",
            "",
            "## 对账与安全边界",
            "",
        ]
    )
    combined_checks = {
        **validation["reconciliation_checks"],
        **validation.get("format_self_checks", {}),
        **validation.get("artifact_readback_checks", {}),
    }
    lines.extend(
        markdown_table(
            ["检查", "结果"],
            ((name, "通过" if passed else "失败") for name, passed in combined_checks.items()),
        )
    )
    lines.extend(
        [
            "",
            f"- 前快照：{validation['source_before']['file_count']:,} 文件 / {validation['source_before']['total_bytes']:,} 字节。",
            f"- 后快照：{validation['source_after']['file_count']:,} 文件 / {validation['source_after']['total_bytes']:,} 字节。",
            f"- 抽样选择：`{sample['selection_method']}`；种子 `{sample['seed']}`。",
            "- 路径门禁会拒绝已观察到的 source/output/scan-root 符号链接与 Windows reparse point，并在输出位于源内时停止。",
            "- TOCTOU 边界：这些检查是进程内 best-effort；无法防止有权限的并发进程在检查与读取/替换之间改换路径。源前后快照与哈希抽样用于发现部分并发变化，但不是内核级锁定。",
            "",
        ]
    )
    return "\n".join(lines)


def analyze(args: argparse.Namespace) -> int:
    raw_config = load_strict_json(lexical_absolute(args.config))
    config = validate_config(raw_config, args)
    source, output, scan_roots = resolve_paths(args, config)

    before_files, before_skipped, before_walk_errors = discover_json_files(
        source, scan_roots, config["scan"]["extension"]
    )
    before = build_source_snapshot(source, before_files, before_skipped, before_walk_errors)

    rows: list[dict[str, Any]] = []
    module_file_counts: Counter[str] = Counter()
    module_bytes: Counter[str] = Counter()
    sensitive_occurrences: Counter[str] = Counter()
    sensitive_file_counts: Counter[str] = Counter()
    system_stats: dict[str, dict[str, Any]] = {
        name: {
            "file_count": 0,
            "filename_signal_file_count": 0,
            "key_signal_file_count": 0,
            "bytes": 0,
            "examples": [],
            "matched_filename_keywords": Counter(),
            "matched_key_keywords": Counter(),
        }
        for name in config["system_candidates"]
    }
    limits = config["limits"]

    for path in before_files:
        rel = source_relative(path, source)
        match = config["naming"]["compiled"].fullmatch(path.name)
        if match:
            filename_module = match.group(config["naming"]["module_group"])
            filename_table = match.group(config["naming"]["table_group"])
        else:
            filename_module = config["naming"]["unmatched_module"]
            filename_table = path.stem
        module_label = filename_module or "<unmatched>"
        row: dict[str, Any] = {
            "source_rel": rel,
            "bytes": 0,
            "sha256": "",
            "parse_status": "read_error",
            "encoding": "unresolved",
            "top_level_type": "",
            "record_count": "",
            "top_level_key_count": "",
            "sample_keys": "[]",
            "filename_module": filename_module,
            "filename_table": filename_table,
            "filename_pattern_matched": "true" if match else "false",
            "error_detail": "",
        }
        key_counts: Counter[str] = Counter()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            row["error_detail"] = type(exc).__name__[: limits["error_characters"]]
        else:
            row["bytes"] = len(raw)
            row["sha256"] = hashlib.sha256(raw).hexdigest()
            decoded, encoding, decode_error = decode_bytes(raw, config["decoding_order"])
            row["encoding"] = encoding
            if decoded is None:
                row["parse_status"] = "decode_error"
                row["error_detail"] = decode_error[: limits["error_characters"]]
            else:
                try:
                    value = json.loads(decoded, parse_constant=reject_nonstandard_constant)
                except (
                    json.JSONDecodeError,
                    NonStandardJSONConstant,
                    RecursionError,
                ) as exc:
                    row["parse_status"] = "json_error"
                    row["error_detail"] = json_error_detail(
                        exc, limits["error_characters"]
                    )
                else:
                    row["parse_status"] = "ok"
                    row["top_level_type"] = top_level_type(value)
                    row["record_count"] = structural_record_count(value)
                    row["top_level_key_count"] = len(value) if isinstance(value, dict) else 0
                    row["sample_keys"] = compact_sample_keys(
                        value,
                        limits["sample_key_count"],
                        limits["sample_key_characters"],
                    )
                    key_counts = collect_key_counts(value)

        module_file_counts[module_label] += 1
        module_bytes[module_label] += int(row["bytes"])
        file_sensitive_names: set[str] = set()
        for key, occurrences in key_counts.items():
            if is_sensitive_field_name(
                key, config["privacy"]["sensitive_field_name_patterns"]
            ):
                sensitive_occurrences[key] += occurrences
                file_sensitive_names.add(key)
        for key in file_sensitive_names:
            sensitive_file_counts[key] += 1

        filename_text = f"{filename_module} {filename_table}"
        key_names = list(key_counts)
        for system_name, keywords in config["system_candidates"].items():
            filename_hits = matched_keywords([filename_text], keywords)
            key_hits = matched_keywords(key_names, keywords) if key_names else []
            if not filename_hits and not key_hits:
                continue
            stat_item = system_stats[system_name]
            stat_item["file_count"] += 1
            stat_item["filename_signal_file_count"] += int(bool(filename_hits))
            stat_item["key_signal_file_count"] += int(bool(key_hits))
            stat_item["bytes"] += int(row["bytes"])
            if len(stat_item["examples"]) < limits["system_example_count"]:
                stat_item["examples"].append(rel)
            stat_item["matched_filename_keywords"].update(filename_hits)
            stat_item["matched_key_keywords"].update(key_hits)
        rows.append(row)

    names = config["outputs"]
    inventory_path = output / names["inventory_csv"]
    summary_path = output / names["summary_json"]
    report_path = output / names["analysis_markdown"]
    atomic_write_text(inventory_path, csv_text(rows))
    csv_verification = verify_csv(inventory_path)

    parse_status_dist = Counter(str(row["parse_status"]) for row in rows)
    parsed_rows = [row for row in rows if row["parse_status"] == "ok"]
    encoding_dist = Counter(str(row["encoding"]) for row in rows)
    top_type_dist = Counter(str(row["top_level_type"]) for row in parsed_rows)
    record_count_exact = Counter(int(row["record_count"]) for row in parsed_rows)
    record_bucket_dist = Counter(
        bucket_label(int(row["record_count"]), config["record_count_buckets"])
        if row["parse_status"] == "ok"
        else "parse_failed"
        for row in rows
    )
    ordered_bucket_dist = {
        bucket["label"]: record_bucket_dist.get(bucket["label"], 0)
        for bucket in config["record_count_buckets"]
    }
    ordered_bucket_dist["parse_failed"] = record_bucket_dist.get("parse_failed", 0)

    largest_tables = sorted(
        rows, key=lambda row: (-int(row["bytes"]), str(row["source_rel"]).casefold())
    )[: limits["largest_table_count"]]
    module_top = [
        {"module": module, "file_count": count, "bytes": module_bytes[module]}
        for module, count in sorted(
            module_file_counts.items(),
            key=lambda item: (-item[1], -module_bytes[item[0]], item[0].casefold()),
        )[: limits["module_top_count"]]
    ]
    sensitive_rows = [
        {
            "field_name": key,
            "occurrences": occurrences,
            "file_count": sensitive_file_counts[key],
        }
        for key, occurrences in sorted(
            sensitive_occurrences.items(),
            key=lambda item: (
                -item[1],
                -sensitive_file_counts[item[0]],
                item[0].casefold(),
            ),
        )[: limits["sensitive_field_top_count"]]
    ]
    system_summary: dict[str, Any] = {}
    for name, stat_item in system_stats.items():
        system_summary[name] = {
            "file_count": stat_item["file_count"],
            "filename_signal_file_count": stat_item["filename_signal_file_count"],
            "key_signal_file_count": stat_item["key_signal_file_count"],
            "bytes": stat_item["bytes"],
            "examples": stat_item["examples"],
            "matched_filename_keywords_top": [
                {"keyword": key, "file_count": count}
                for key, count in stat_item["matched_filename_keywords"].most_common(
                    limits["matched_keyword_top_count"]
                )
            ],
            "matched_key_keywords_top": [
                {"keyword": key, "file_count": count}
                for key, count in stat_item["matched_key_keywords"].most_common(
                    limits["matched_keyword_top_count"]
                )
            ],
        }

    sha_sample = deterministic_sha256_sample(
        source,
        rows,
        config["sampling"]["sha256_sample_size"],
        config["sampling"]["sha256_seed"],
    )
    after_files, after_skipped, after_walk_errors = discover_json_files(
        source, scan_roots, config["scan"]["extension"]
    )
    after = build_source_snapshot(source, after_files, after_skipped, after_walk_errors)
    source_unchanged = before == after
    actual_total_bytes = sum(int(row["bytes"]) for row in rows)
    success_count = parse_status_dist.get("ok", 0)
    failure_count = len(rows) - success_count
    matched_count = sum(1 for row in rows if row["filename_pattern_matched"] == "true")
    expected_file_count = config["expected"]["file_count"]
    expected_total_bytes = config["expected"]["total_bytes"]

    checks = {
        "扫描阶段无目录遍历错误": before["walk_error_count"] == 0
        and after["walk_error_count"] == 0,
        "源快照无 stat 失败": before["stat_failure_count"] == 0
        and after["stat_failure_count"] == 0,
        "CSV 行数等于扫描文件数": csv_verification["data_rows"] == len(rows),
        "CSV bytes 合计等于扫描读取字节": csv_verification["bytes_sum"] == actual_total_bytes,
        "扫描读取字节等于前快照总字节": actual_total_bytes == before["total_bytes"],
        "解析成功数加失败数等于扫描文件数": success_count + failure_count == len(rows),
        "顶层类型分布之和等于解析成功数": sum(top_type_dist.values()) == success_count,
        "record_count 分桶之和等于扫描文件数": sum(record_bucket_dist.values()) == len(rows),
        "模块文件数之和等于扫描文件数": sum(module_file_counts.values()) == len(rows),
        "固定种子 SHA-256 抽样全部匹配": sha_sample["all_match"],
        "源目录前后清单元数据摘要一致": source_unchanged,
        "预期文件数未配置或匹配": expected_file_count is None
        or expected_file_count == len(rows),
        "预期总字节未配置或匹配": expected_total_bytes is None
        or expected_total_bytes == actual_total_bytes,
    }

    summary: dict[str, Any] = {
        "schema_version": "1.0",
        "config_schema_version": config["schema_version"],
        "source_root": str(source),
        "output_root": str(output),
        "scan_relative_roots": config["scan"]["relative_roots"],
        "execution_argv": [str(item) for item in sys.argv],
        "method": {
            "scope": "recursive case-insensitive configured-extension enumeration; source read-only",
            "decode_policy": config["decoding_order"],
            "strict_json": True,
            "record_count_definition": "object/array top-level member count; scalar=1; null=0",
            "sample_keys_definition": "field names only; top-level object or first mapping from first 20 array items",
            "filename_pattern": config["naming"]["pattern"],
            "unmatched_filename_policy": "configured unmatched module; table retains complete Path.stem",
            "privacy_policy": "field names/counts only; JSON values are never emitted",
            "system_candidate_policy": "filename and nested-key lexical signals only; overlaps allowed; no runtime validation",
            "path_safety": "reject observed source/output/scan-root symlink or Windows reparse components; output cannot be inside source",
            "toctou_boundary": "best-effort userspace checks; concurrent privileged path replacement cannot be prevented",
        },
        "scan": {
            "file_count": len(rows),
            "total_bytes": actual_total_bytes,
            "expected_file_count": expected_file_count,
            "expected_total_bytes": expected_total_bytes,
            "matches_expected_file_count": expected_file_count is None
            or expected_file_count == len(rows),
            "matches_expected_total_bytes": expected_total_bytes is None
            or expected_total_bytes == actual_total_bytes,
            "skipped_link_or_reparse_entries": before["skipped_link_or_reparse_entries"],
        },
        "parsing": {
            "success_count": success_count,
            "failure_count": failure_count,
            "parse_status_distribution": dict(sorted(parse_status_dist.items())),
            "encoding_distribution": dict(sorted(encoding_dist.items())),
            "top_level_type_distribution": dict(sorted(top_type_dist.items())),
            "record_count_bucket_distribution": ordered_bucket_dist,
            "record_count_exact_top": [
                {"record_count": count, "file_count": frequency}
                for count, frequency in sorted(
                    record_count_exact.items(), key=lambda item: (-item[1], item[0])
                )[:50]
            ],
            "failures": [
                {
                    "source_rel": row["source_rel"],
                    "parse_status": row["parse_status"],
                    "encoding": row["encoding"],
                    "error_detail": row["error_detail"],
                }
                for row in rows
                if row["parse_status"] != "ok"
            ],
        },
        "naming": {
            "matched_count": matched_count,
            "unmatched_count": len(rows) - matched_count,
            "module_count": len(module_file_counts),
            "module_distribution": {
                module: module_file_counts[module]
                for module in sorted(module_file_counts, key=str.casefold)
            },
            "module_top": module_top,
            "unmatched_files": [
                row["source_rel"]
                for row in rows
                if row["filename_pattern_matched"] != "true"
            ],
        },
        "largest_tables_by_bytes": [
            {
                key: row[key]
                for key in (
                    "source_rel",
                    "bytes",
                    "filename_module",
                    "filename_table",
                    "top_level_type",
                    "record_count",
                    "top_level_key_count",
                )
            }
            for row in largest_tables
        ],
        "system_candidates": system_summary,
        "sensitive_field_name_signals": sensitive_rows,
        "validation": {
            "source_before": before,
            "source_after": after,
            "source_snapshot_unchanged": source_unchanged,
            "sha256_sample": sha_sample,
            "csv_verification": csv_verification,
            "reconciliation_checks": checks,
            "format_self_checks": {},
            "artifact_readback_checks": {},
            "all_checks_passed": all(checks.values()),
        },
        "artifacts": {
            "inventory_csv": str(inventory_path),
            "summary_json": str(summary_path),
            "analysis_markdown": str(report_path),
            "config": str(lexical_absolute(args.config)),
            "script": str(Path(__file__).resolve()),
        },
    }

    candidate_json = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    candidate_report = build_report(summary)
    format_checks = {
        "summary JSON 严格序列化并可内存回读": isinstance(
            json.loads(candidate_json, parse_constant=reject_nonstandard_constant), dict
        ),
        "Markdown 包含扫描总量和事实/判断/假设分区": (
            f"**{len(rows):,}**" in candidate_report
            and f"**{actual_total_bytes:,}**" in candidate_report
            and "## 观察到的事实" in candidate_report
            and "## 我的判断" in candidate_report
            and "## 待验证假设" in candidate_report
        ),
    }
    summary["validation"]["format_self_checks"] = format_checks
    summary["validation"]["all_checks_passed"] = all(checks.values()) and all(
        format_checks.values()
    )
    atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    atomic_write_text(report_path, build_report(summary))

    try:
        readback_summary = json.loads(
            summary_path.read_text(encoding="utf-8"),
            parse_constant=reject_nonstandard_constant,
        )
        readback_report = report_path.read_text(encoding="utf-8")
        temp_residue = [
            item.name
            for item in output.iterdir()
            if item.is_file() and item.name.endswith(".tmp")
        ]
    except (OSError, json.JSONDecodeError, NonStandardJSONConstant) as exc:
        readback_checks = {"CSV/JSON/Markdown 文件可回读且无临时残留": False}
        readback_error = type(exc).__name__
    else:
        readback_checks = {
            "summary JSON 回读总量与扫描一致": readback_summary.get("scan", {}).get(
                "file_count"
            )
            == len(rows)
            and readback_summary.get("scan", {}).get("total_bytes") == actual_total_bytes,
            "Markdown 回读包含扫描总量": f"**{len(rows):,}**" in readback_report
            and f"**{actual_total_bytes:,}**" in readback_report,
            "输出目录无原子写入临时残留": not temp_residue,
        }
        readback_error = ""
    summary["validation"]["artifact_readback_checks"] = readback_checks
    summary["validation"]["artifact_readback_error"] = readback_error
    summary["validation"]["all_checks_passed"] = (
        all(checks.values())
        and all(format_checks.values())
        and all(readback_checks.values())
    )
    atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    atomic_write_text(report_path, build_report(summary))

    final_result = {
        "file_count": len(rows),
        "total_bytes": actual_total_bytes,
        "parse_success": success_count,
        "parse_failure": failure_count,
        "filename_matched": matched_count,
        "sha256_sample_all_match": sha_sample["all_match"],
        "source_snapshot_unchanged": source_unchanged,
        "all_checks_passed": summary["validation"]["all_checks_passed"],
        "artifacts": summary["artifacts"],
    }
    print(json.dumps(final_result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if summary["validation"]["all_checks_passed"] else 2


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return analyze(parse_args(argv))
    except (ConfigError, PathSafetyError) as exc:
        print(f"analyze_loose_json_tables.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

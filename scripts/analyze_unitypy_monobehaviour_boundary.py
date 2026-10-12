#!/usr/bin/env python3
"""Stream a UnityPy index and report the evidence boundary for MonoBehaviour data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Sequence

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_ROOT / "references" / "unity-monobehaviour-boundary-config.json"
DEFAULT_CONTRACT = SKILL_ROOT / "references" / "unity-monobehaviour-boundary-contract.json"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
CANONICAL_BYTE_SIZE_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")


class AnalysisError(ValueError):
    """Raised when an input or contract cannot be analyzed safely."""


class ArtifactTransactionError(AnalysisError):
    """Raised when an artifact transaction needs explicit recovery."""

    def __init__(self, message: str, transaction: dict[str, Any]) -> None:
        super().__init__(message)
        self.transaction = transaction


class NonStandardJSONConstant(ValueError):
    """Raised for NaN and Infinity, which are not strict JSON."""


class InputSnapshot(NamedTuple):
    """Stable identity and digest for one input file read."""

    path: Path
    display_path: str
    byte_count: int
    sha256: str
    identity: tuple[int, int, int, int]

    def evidence(self) -> dict[str, Any]:
        return {
            "bytes": self.byte_count,
            "path": self.display_path,
            "sha256": self.sha256,
        }


class ContentSnapshot(NamedTuple):
    """Stable bytes and identity for an existing output artifact."""

    path: Path
    data: bytes
    identity: tuple[int, int, int, int]


class StagedArtifact(NamedTuple):
    """One verified staged artifact awaiting set-level commit."""

    final_path: Path
    stage_file: Any
    data: bytes
    identity: tuple[int, int, int, int]
    private_name: str


class RecoveryCandidate(NamedTuple):
    """One exact generation that must remain reachable after rollback."""

    opened: Any
    data: bytes
    identity: tuple[int, int, int, int]
    label: str


@dataclass
class OriginalArtifact:
    """Pre-transaction state and optional rollback backup for one artifact."""

    final_path: Path
    snapshot: ContentSnapshot | None
    backup_file: Any | None
    target_file: Any | None = None
    backup_reservation: Any | None = None
    retired: bool = False


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metadata-root", type=Path)
    parser.add_argument("--probe-evidence", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    return parser.parse_args(argv)


def reject_nonstandard_constant(value: str) -> None:
    raise NonStandardJSONConstant(f"non-standard JSON constant rejected: {value}")


def parse_strict_json_bytes(data: bytes, path: Path, label: str) -> Any:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AnalysisError(f"invalid UTF-8 {label}: {path}: {exc}") from exc
    try:
        return json.loads(text, parse_constant=reject_nonstandard_constant)
    except (json.JSONDecodeError, NonStandardJSONConstant) as exc:
        raise AnalysisError(f"invalid strict JSON {label}: {path}: {type(exc).__name__}: {exc}") from exc


def load_strict_json(path: Path, label: str) -> Any:
    data, _snapshot = read_bytes_snapshot(path, label=label)
    return parse_strict_json_bytes(data, path, label)


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{label} must be an object")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"{label} must be a non-empty string")
    return value


def require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AnalysisError(f"{label} must be an array of non-empty strings")
    return list(value)


def output_basename(value: Any, label: str, suffix: str) -> str:
    name = require_string(value, label)
    candidate = Path(name)
    if candidate.name != name or candidate.is_absolute() or candidate.drive or name in {".", ".."}:
        raise AnalysisError(f"{label} must be a basename")
    if candidate.suffix.casefold() != suffix.casefold():
        raise AnalysisError(f"{label} must end with {suffix}")
    return name


def stat_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def snapshot_display_path(path: Path, relative_to: Path | None) -> str:
    return str(path) if relative_to is None else path.relative_to(relative_to).as_posix()


def capture_hash_snapshot(
    path: Path, *, label: str = "input", relative_to: Path | None = None
) -> InputSnapshot:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        path_after = resolved.stat()
    except OSError as exc:
        raise AnalysisError(f"cannot read {label}: {resolved}: {type(exc).__name__}: {exc}") from exc
    identity = stat_identity(before)
    if identity != stat_identity(after) or identity != stat_identity(path_after):
        raise AnalysisError(f"input changed while hashing {label}: {resolved}")
    return InputSnapshot(
        path=resolved,
        display_path=snapshot_display_path(resolved, relative_to),
        byte_count=int(before.st_size),
        sha256=digest.hexdigest(),
        identity=identity,
    )


def read_bytes_snapshot(
    path: Path, *, label: str = "input", relative_to: Path | None = None
) -> tuple[bytes, InputSnapshot]:
    resolved = path.resolve(strict=True)
    try:
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            data = handle.read()
            after = os.fstat(handle.fileno())
        path_after = resolved.stat()
    except OSError as exc:
        raise AnalysisError(f"cannot read {label}: {resolved}: {type(exc).__name__}: {exc}") from exc
    identity = stat_identity(before)
    if identity != stat_identity(after) or identity != stat_identity(path_after):
        raise AnalysisError(f"input changed while reading {label}: {resolved}")
    if len(data) != before.st_size:
        raise AnalysisError(f"input length changed while reading {label}: {resolved}")
    return data, InputSnapshot(
        path=resolved,
        display_path=snapshot_display_path(resolved, relative_to),
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        identity=identity,
    )


def read_json_snapshot(
    path: Path, label: str, *, relative_to: Path | None = None
) -> tuple[Any, InputSnapshot]:
    data, snapshot = read_bytes_snapshot(path, label=label, relative_to=relative_to)
    return parse_strict_json_bytes(data, snapshot.path, label), snapshot


def verify_snapshot(snapshot: InputSnapshot, label: str) -> None:
    current = capture_hash_snapshot(snapshot.path, label=label)
    if current.identity != snapshot.identity or current.sha256 != snapshot.sha256:
        raise AnalysisError(f"input changed after analysis {label}: {snapshot.path}")


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def count_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(counter[key]) for key in sorted(counter, key=lambda item: str(item))}


def normalize_source(value: str) -> str:
    return value.replace("\\", "/")


def validate_config(raw: Any) -> dict[str, Any]:
    config = require_mapping(raw, "config")
    if config.get("config_schema_version") != "1.1.0":
        raise AnalysisError("unsupported config_schema_version")

    columns = require_mapping(config.get("index_columns"), "index_columns")
    checked_columns = {
        key: require_string(columns.get(key), f"index_columns.{key}")
        for key in ("asset_type", "export_path", "remarks", "source_file", "status")
    }
    if len(set(checked_columns.values())) != len(checked_columns):
        raise AnalysisError("index column names must be unique")

    statuses = require_mapping(config.get("statuses"), "statuses")
    checked_statuses = {
        key: require_string_list(statuses.get(key), f"statuses.{key}")
        for key in ("exported", "known", "read_error")
    }
    if not set(checked_statuses["exported"]).issubset(checked_statuses["known"]):
        raise AnalysisError("statuses.exported must be included in statuses.known")
    if not set(checked_statuses["read_error"]).issubset(checked_statuses["known"]):
        raise AnalysisError("statuses.read_error must be included in statuses.known")

    patterns = require_mapping(config.get("patterns"), "patterns")
    expected_actual_text = require_string(patterns.get("expected_actual"), "patterns.expected_actual")
    try:
        expected_actual = re.compile(expected_actual_text)
    except re.error as exc:
        raise AnalysisError(f"invalid patterns.expected_actual: {exc}") from exc
    if not {"expected", "actual"}.issubset(expected_actual.groupindex):
        raise AnalysisError("patterns.expected_actual requires named expected and actual groups")

    groups = require_mapping(config.get("source_groups"), "source_groups")
    default_group = require_string(groups.get("default_id"), "source_groups.default_id")
    raw_rules = groups.get("rules")
    if not isinstance(raw_rules, list):
        raise AnalysisError("source_groups.rules must be an array")
    group_rules: list[dict[str, Any]] = []
    group_ids = {default_group}
    for index, raw_rule in enumerate(raw_rules):
        rule = require_mapping(raw_rule, f"source_groups.rules[{index}]")
        group_id = require_string(rule.get("id"), f"source_groups.rules[{index}].id")
        pattern_text = require_string(rule.get("pattern"), f"source_groups.rules[{index}].pattern")
        if group_id in group_ids:
            raise AnalysisError(f"duplicate source group id: {group_id}")
        try:
            compiled = re.compile(pattern_text, re.IGNORECASE)
        except re.error as exc:
            raise AnalysisError(f"invalid source group regex {group_id}: {exc}") from exc
        group_ids.add(group_id)
        group_rules.append({"id": group_id, "pattern": pattern_text, "compiled": compiled})

    metadata = require_mapping(config.get("metadata"), "metadata")
    generic_keys = require_string_list(metadata.get("generic_header_keys"), "metadata.generic_header_keys")
    type_tree_key = require_string(metadata.get("type_tree_key"), "metadata.type_tree_key")

    evidence_columns = require_mapping(config.get("evidence_columns"), "evidence_columns")
    byte_candidates = require_string_list(
        evidence_columns.get("byte_size_candidates"), "evidence_columns.byte_size_candidates"
    )
    raw_candidates = require_string_list(
        evidence_columns.get("raw_sha256_candidates"), "evidence_columns.raw_sha256_candidates"
    )
    for label, candidates in (
        ("byte_size_candidates", byte_candidates),
        ("raw_sha256_candidates", raw_candidates),
    ):
        if len(set(candidates)) != len(candidates):
            raise AnalysisError(f"evidence_columns.{label} must not contain duplicates")
    if set(byte_candidates) & set(raw_candidates):
        raise AnalysisError("byte-size and raw-SHA candidate columns must be disjoint")
    structural_columns = set(checked_columns.values())
    for label, candidates in (
        ("byte_size_candidates", byte_candidates),
        ("raw_sha256_candidates", raw_candidates),
    ):
        conflicts = sorted(structural_columns & set(candidates))
        if conflicts:
            raise AnalysisError(
                f"evidence_columns.{label} conflicts with structural index columns: {conflicts}"
            )
    selection_policy = require_string(
        evidence_columns.get("selection_policy"), "evidence_columns.selection_policy"
    )
    conflict_policy = require_string(
        evidence_columns.get("valid_alias_conflict"),
        "evidence_columns.valid_alias_conflict",
    )
    invalid_alias_effect = require_string(
        evidence_columns.get("invalid_alias_effect"),
        "evidence_columns.invalid_alias_effect",
    )
    if selection_policy != "first_valid_by_candidate_order":
        raise AnalysisError("unsupported evidence_columns.selection_policy")
    if conflict_policy != "fail_closed":
        raise AnalysisError("unsupported evidence_columns.valid_alias_conflict")
    if invalid_alias_effect != "partial":
        raise AnalysisError("unsupported evidence_columns.invalid_alias_effect")
    byte_size_syntax = require_string(
        evidence_columns.get("byte_size_syntax"), "evidence_columns.byte_size_syntax"
    )
    leading_zero_policy = require_string(
        evidence_columns.get("byte_size_leading_zero_policy"),
        "evidence_columns.byte_size_leading_zero_policy",
    )
    zero_allowed = evidence_columns.get("byte_size_zero_allowed")
    if byte_size_syntax != "canonical_ascii_nonnegative_decimal":
        raise AnalysisError("unsupported evidence_columns.byte_size_syntax")
    if leading_zero_policy != "forbid_except_zero":
        raise AnalysisError("unsupported evidence_columns.byte_size_leading_zero_policy")
    if zero_allowed is not True:
        raise AnalysisError("evidence_columns.byte_size_zero_allowed must be true")

    limits = require_mapping(config.get("limits"), "limits")
    preview_limit = limits.get("unparsed_remark_preview_limit")
    preview_chars = limits.get("unparsed_remark_preview_chars")
    if not isinstance(preview_limit, int) or isinstance(preview_limit, bool) or not 1 <= preview_limit <= 10_000:
        raise AnalysisError("limits.unparsed_remark_preview_limit must be an integer from 1 to 10000")
    if not isinstance(preview_chars, int) or isinstance(preview_chars, bool) or not 1 <= preview_chars <= 10_000:
        raise AnalysisError("limits.unparsed_remark_preview_chars must be an integer from 1 to 10000")

    probe = require_mapping(config.get("probe_evidence"), "probe_evidence")
    pointer = require_string_list(probe.get("samples_pointer"), "probe_evidence.samples_pointer")
    checked_probe = {
        key: require_string(probe.get(key), f"probe_evidence.{key}")
        for key in ("exact_field", "sample_kind_field", "source_field", "status_field")
    }

    outputs = require_mapping(config.get("outputs"), "outputs")
    checked_outputs = {
        "summary_json": output_basename(outputs.get("summary_json"), "outputs.summary_json", ".json"),
        "report_markdown": output_basename(
            outputs.get("report_markdown"), "outputs.report_markdown", ".md"
        ),
    }

    safe_fallback = config.get("safe_fallback")
    if not isinstance(safe_fallback, list) or not safe_fallback:
        raise AnalysisError("safe_fallback must be a non-empty array")
    checked_fallback: list[dict[str, str]] = []
    for index, item in enumerate(safe_fallback):
        entry = require_mapping(item, f"safe_fallback[{index}]")
        checked_fallback.append(
            {
                "id": require_string(entry.get("id"), f"safe_fallback[{index}].id"),
                "acceptance": require_string(
                    entry.get("acceptance"), f"safe_fallback[{index}].acceptance"
                ),
            }
        )
    forbidden = require_string_list(config.get("forbidden_inference"), "forbidden_inference")

    return {
        "columns": checked_columns,
        "statuses": checked_statuses,
        "expected_actual": expected_actual,
        "expected_actual_text": expected_actual_text,
        "default_group": default_group,
        "group_rules": group_rules,
        "generic_keys": set(generic_keys),
        "type_tree_key": type_tree_key,
        "byte_candidates": byte_candidates,
        "raw_candidates": raw_candidates,
        "evidence_selection_policy": selection_policy,
        "evidence_conflict_policy": conflict_policy,
        "evidence_invalid_alias_effect": invalid_alias_effect,
        "byte_size_syntax": byte_size_syntax,
        "byte_size_leading_zero_policy": leading_zero_policy,
        "byte_size_zero_allowed": zero_allowed,
        "unparsed_remark_preview_limit": preview_limit,
        "unparsed_remark_preview_chars": preview_chars,
        "probe_pointer": pointer,
        "probe_fields": checked_probe,
        "outputs": checked_outputs,
        "safe_fallback": checked_fallback,
        "forbidden_inference": forbidden,
    }


def validate_contract(raw: Any) -> dict[str, Any]:
    contract = require_mapping(raw, "contract")
    required = require_string_list(contract.get("required_top_level_keys"), "required_top_level_keys")
    return {
        "contract_version": require_string(contract.get("contract_version"), "contract_version"),
        "schema_name": require_string(contract.get("output_schema_name"), "output_schema_name"),
        "schema_version": require_string(
            contract.get("output_schema_version"), "output_schema_version"
        ),
        "required_top_level_keys": required,
    }


def group_for(source_file: str, config: dict[str, Any]) -> str:
    normalized = normalize_source(source_file)
    for rule in config["group_rules"]:
        if rule["compiled"].search(normalized):
            return str(rule["id"])
    return str(config["default_group"])


def present_candidate_columns(fieldnames: list[str], candidates: list[str]) -> list[str]:
    return [candidate for candidate in candidates if candidate in fieldnames]


def validate_csv_header(
    fieldnames: list[str] | None,
    *,
    label: str,
    expected: Sequence[str] | None = None,
) -> list[str]:
    """Reject headers whose mapping would be ambiguous before reading data rows."""

    if not fieldnames:
        raise AnalysisError(f"{label} CSV header must be non-empty")
    header = list(fieldnames)
    invalid_positions = [
        index for index, name in enumerate(header) if not isinstance(name, str) or not name.strip()
    ]
    if invalid_positions:
        raise AnalysisError(
            f"{label} CSV header contains empty column names at positions: {invalid_positions}"
        )
    normalized: dict[str, str] = {}
    duplicates: list[str] = []
    for name in header:
        key = name.casefold()
        if key in normalized:
            duplicates.append(f"{normalized[key]!r}/{name!r}")
        else:
            normalized[key] = name
    if duplicates:
        raise AnalysisError(
            f"{label} CSV header column names must be globally unique under "
            f"case-insensitive semantics: {duplicates}"
        )
    if expected is not None and header != list(expected):
        raise AnalysisError(f"{label} CSV header changed between streaming passes")
    return header


def availability(
    columns: list[str],
    available_count: int,
    invalid_count: int,
    invalid_alias_value_count: int,
    total: int,
) -> str:
    if not columns:
        return "unavailable"
    if available_count == 0:
        return "invalid" if invalid_count else "unavailable"
    if invalid_count or invalid_alias_value_count or available_count < total:
        return "partial"
    return "available"


def select_row_evidence(
    row: dict[str, str],
    columns: list[str],
    *,
    evidence_name: str,
    row_number: int,
) -> tuple[str, str | None, int, int]:
    """Select the first valid configured alias and reject conflicting valid aliases."""

    nonempty = [(column, row.get(column, "")) for column in columns]
    nonempty = [(column, value) for column, value in nonempty if value.strip()]
    if not nonempty:
        return "empty", None, 0, 0

    valid: list[tuple[str, int | str]] = []
    invalid_count = 0
    for column, raw_value in nonempty:
        if evidence_name == "byte_size":
            if not CANONICAL_BYTE_SIZE_RE.fullmatch(raw_value):
                invalid_count += 1
                continue
            normalized: int | str = raw_value
        else:
            stripped = raw_value.strip()
            if not SHA256_RE.fullmatch(stripped):
                invalid_count += 1
                continue
            normalized = stripped.casefold()
        valid.append((column, normalized))

    if not valid:
        return "invalid", None, invalid_count, 0
    normalized_values = {value for _column, value in valid}
    if len(normalized_values) != 1:
        detail = ", ".join(column for column, _value in valid)
        raise AnalysisError(
            f"conflicting valid {evidence_name} aliases at CSV row {row_number}: {detail}"
        )
    return "valid", valid[0][0], invalid_count, len(valid) - 1


def evidence_availability_summary(
    *,
    columns: list[str],
    available_count: int,
    invalid_count: int,
    invalid_alias_value_count: int,
    consistent_alias_value_count: int,
    selected_columns: Counter[str],
    total: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "availability": availability(
            columns,
            available_count,
            invalid_count,
            invalid_alias_value_count,
            total,
        ),
        "available_count": available_count,
        "column": columns[0] if len(columns) == 1 else None,
        "consistent_alias_value_count": consistent_alias_value_count,
        "invalid_alias_effect": config["evidence_invalid_alias_effect"],
        "invalid_alias_value_count": invalid_alias_value_count,
        "invalid_count": invalid_count,
        "present_columns": columns,
        "selected_column_counts": count_dict(selected_columns),
        "selection_policy": config["evidence_selection_policy"],
        "source": "declared_index_column_only",
        "valid_alias_conflict": config["evidence_conflict_policy"],
    }


def observe_bounded_remark(
    sample: dict[str, tuple[int, str]],
    remark: str,
    limit: int,
    preview_chars: int,
) -> None:
    """Keep the deterministic lowest-SHA sample without retaining every unique remark."""

    digest = hashlib.sha256(remark.encode("utf-8")).hexdigest()
    existing = sample.get(digest)
    if existing is not None:
        sample[digest] = (existing[0] + 1, existing[1])
        return
    if len(sample) < limit:
        sample[digest] = (1, remark[:preview_chars])
        return
    largest = max(sample)
    if digest < largest:
        del sample[largest]
        sample[digest] = (1, remark[:preview_chars])


def stream_index(index_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    source_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    source_groups: dict[str, Counter[str]] = defaultdict(Counter)
    group_sources: dict[str, set[str]] = defaultdict(set)
    actual_counts: Counter[int] = Counter()
    expected_counts: Counter[int] = Counter()
    pair_counts: Counter[str] = Counter()
    unparsed_read_error_count = 0
    unparsed_read_error_sample: dict[str, tuple[int, str]] = {}
    total_rows = 0
    mono_rows = 0
    parsed_read_errors = 0
    expected_total = 0
    actual_total = 0
    unknown_statuses: Counter[str] = Counter()
    byte_available = 0
    byte_invalid = 0
    byte_invalid_alias = 0
    byte_consistent_alias = 0
    byte_selected_columns: Counter[str] = Counter()
    raw_available = 0
    raw_invalid = 0
    raw_invalid_alias = 0
    raw_consistent_alias = 0
    raw_selected_columns: Counter[str] = Counter()

    columns = config["columns"]
    try:
        with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = validate_csv_header(reader.fieldnames, label="index first pass")
            missing = sorted(set(columns.values()) - set(fieldnames))
            if missing:
                raise AnalysisError(f"index missing required columns: {missing}")
            byte_columns = present_candidate_columns(fieldnames, config["byte_candidates"])
            raw_columns = present_candidate_columns(fieldnames, config["raw_candidates"])
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise AnalysisError(f"malformed CSV row {row_number}: unexpected extra columns")
                total_rows += 1
                if row.get(columns["asset_type"], "") != "MonoBehaviour":
                    continue
                mono_rows += 1
                source = normalize_source(row.get(columns["source_file"], ""))
                status = row.get(columns["status"], "") or "<empty>"
                group = group_for(source, config)
                status_counts[status] += 1
                source_statuses[source][status] += 1
                source_groups[group][status] += 1
                group_sources[group].add(source)
                if status not in config["statuses"]["known"]:
                    unknown_statuses[status] += 1

                byte_state, byte_selected, invalid_aliases, consistent_aliases = (
                    select_row_evidence(
                        row,
                        byte_columns,
                        evidence_name="byte_size",
                        row_number=row_number,
                    )
                )
                if byte_state == "valid":
                    byte_available += 1
                    assert byte_selected is not None
                    byte_selected_columns[byte_selected] += 1
                    byte_invalid_alias += invalid_aliases
                    byte_consistent_alias += consistent_aliases
                elif byte_state == "invalid":
                    byte_invalid += 1

                raw_state, raw_selected, invalid_aliases, consistent_aliases = (
                    select_row_evidence(
                        row,
                        raw_columns,
                        evidence_name="raw_sha256",
                        row_number=row_number,
                    )
                )
                if raw_state == "valid":
                    raw_available += 1
                    assert raw_selected is not None
                    raw_selected_columns[raw_selected] += 1
                    raw_invalid_alias += invalid_aliases
                    raw_consistent_alias += consistent_aliases
                elif raw_state == "invalid":
                    raw_invalid += 1
                if status not in config["statuses"]["read_error"]:
                    continue
                remark = row.get(columns["remarks"], "")
                match = config["expected_actual"].fullmatch(remark)
                if match is None:
                    unparsed_read_error_count += 1
                    observe_bounded_remark(
                        unparsed_read_error_sample,
                        remark,
                        config["unparsed_remark_preview_limit"],
                        config["unparsed_remark_preview_chars"],
                    )
                    continue
                expected = int(match.group("expected"))
                actual = int(match.group("actual"))
                parsed_read_errors += 1
                expected_counts[expected] += 1
                actual_counts[actual] += 1
                pair_counts[f"{expected}/{actual}"] += 1
                expected_total += expected
                actual_total += actual
    except UnicodeDecodeError as exc:
        raise AnalysisError(f"index is not valid UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise AnalysisError(f"malformed CSV: {exc}") from exc
    except OSError as exc:
        raise AnalysisError(f"cannot stream index: {type(exc).__name__}: {exc}") from exc

    group_summary: dict[str, Any] = {}
    for group in sorted(source_groups):
        counts = source_groups[group]
        group_summary[group] = {
            "mono_behaviour_count": sum(counts.values()),
            "source_count": len(group_sources[group]),
            "status_counts": count_dict(counts),
        }
    source_summary = {
        source: {
            "source_group": group_for(source, config),
            "status_counts": count_dict(counts),
            "mono_behaviour_count": sum(counts.values()),
        }
        for source, counts in sorted(source_statuses.items())
    }
    read_error_count = sum(
        status_counts.get(status, 0) for status in config["statuses"]["read_error"]
    )
    unparsed_preview = [
        {
            "count": count,
            "remark_preview": preview,
            "remark_sha256": digest,
        }
        for digest, (count, preview) in sorted(
            unparsed_read_error_sample.items(),
            key=lambda item: (-item[1][0], item[0], item[1][1]),
        )
    ]
    return {
        "_validated_index_header": fieldnames,
        "evidence_availability": {
                "byte_size": evidence_availability_summary(
                    columns=byte_columns,
                    available_count=byte_available,
                    invalid_count=byte_invalid,
                    invalid_alias_value_count=byte_invalid_alias,
                    consistent_alias_value_count=byte_consistent_alias,
                    selected_columns=byte_selected_columns,
                    total=mono_rows,
                    config=config,
                ),
                "raw_sha256": evidence_availability_summary(
                    columns=raw_columns,
                    available_count=raw_available,
                    invalid_count=raw_invalid,
                    invalid_alias_value_count=raw_invalid_alias,
                    consistent_alias_value_count=raw_consistent_alias,
                    selected_columns=raw_selected_columns,
                    total=mono_rows,
                    config=config,
                ),
        },
        "index": {
                "mono_behaviour_count": mono_rows,
                "source_count": len(source_statuses),
                "status_counts": count_dict(status_counts),
                "total_row_count": total_rows,
                "unknown_status_counts": count_dict(unknown_statuses),
        },
        "read_errors": {
                "actual_bytes_counts": count_dict(actual_counts),
                "actual_bytes_total": actual_total,
                "expected_bytes_counts": count_dict(expected_counts),
                "expected_bytes_total": expected_total,
                "expected_minus_actual_total": expected_total - actual_total,
                "expected_vs_actual_counts": count_dict(pair_counts),
                "parsed_expected_actual_count": parsed_read_errors,
                "pattern": config["expected_actual_text"],
                "read_error_count": read_error_count,
                "unparsed_count": unparsed_read_error_count,
                "unparsed_preview_limit": config["unparsed_remark_preview_limit"],
                "unparsed_preview_strategy": "lowest_sha256_bounded_sample",
                "unparsed_remarks": unparsed_preview,
                "warning": "Expected bytes are parsed from error text and are not promoted to declared object byte_size.",
        },
        "source_groups": {
            "groups": group_summary,
            "sources": source_summary,
        },
    }


def resolve_metadata_path(
    index_root: Path, metadata_root: Path, export_path: str
) -> tuple[Path | None, str | None]:
    if not export_path.strip():
        return None, "export_path_empty"
    candidate_text = export_path.replace("/", os.sep).replace("\\", os.sep)
    relative = Path(candidate_text)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None, "unsafe_export_path"
    candidate = (index_root / relative).resolve(strict=False)
    try:
        candidate.relative_to(metadata_root)
    except ValueError:
        return None, "outside_metadata_root"
    return candidate, None


def paths_alias(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    if left_resolved == right_resolved:
        return True
    if not left_resolved.exists() or not right_resolved.exists():
        return False
    try:
        return os.path.samefile(left_resolved, right_resolved)
    except OSError as exc:
        raise AnalysisError(
            f"cannot verify path alias boundary: {left_resolved} vs {right_resolved}: {exc}"
        ) from exc


def ensure_no_output_aliases(output_targets: Sequence[Path], input_paths: Sequence[Path]) -> None:
    for index, target in enumerate(output_targets):
        for other in output_targets[index + 1 :]:
            if paths_alias(target, other):
                raise AnalysisError(f"output targets alias each other: {target} and {other}")
        for input_path in input_paths:
            if paths_alias(target, input_path):
                raise AnalysisError(f"output target aliases input: {target} and {input_path}")


def analyze_metadata(
    index_path: Path,
    index_root: Path,
    metadata_root: Path,
    config: dict[str, Any],
    output_targets: Sequence[Path],
    expected_header: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any], list[InputSnapshot], dict[str, int]]:
    classifications: Counter[str] = Counter()
    custom_key_counts: Counter[str] = Counter()
    group_classifications: dict[str, Counter[str]] = defaultdict(Counter)
    failure_reasons: Counter[str] = Counter()
    input_files: dict[str, InputSnapshot] = {}
    parsed_cache: dict[Path, tuple[str, list[str], str | None]] = {}
    total_rows = 0
    mono_rows = 0
    exported_row_count = 0
    columns = config["columns"]

    try:
        with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = validate_csv_header(
                reader.fieldnames,
                label="index metadata pass",
                expected=expected_header,
            )
            missing = sorted(set(columns.values()) - set(fieldnames))
            if missing:
                raise AnalysisError(f"index missing required columns on metadata pass: {missing}")
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise AnalysisError(
                        f"malformed CSV row {row_number} on metadata pass: unexpected extra columns"
                    )
                total_rows += 1
                if row.get(columns["asset_type"], "") != "MonoBehaviour":
                    continue
                mono_rows += 1
                status = row.get(columns["status"], "") or "<empty>"
                if status not in config["statuses"]["exported"]:
                    continue
                exported_row_count += 1
                source = normalize_source(row.get(columns["source_file"], ""))
                source_group = group_for(source, config)
                path, path_error = resolve_metadata_path(
                    index_root,
                    metadata_root,
                    row.get(columns["export_path"], ""),
                )
                if path_error is not None or path is None:
                    classification = "unavailable"
                    custom_keys: list[str] = []
                    reason = path_error
                elif path in parsed_cache:
                    classification, custom_keys, reason = parsed_cache[path]
                elif not path.is_file():
                    classification, custom_keys, reason = (
                        "unavailable",
                        [],
                        "metadata_file_missing",
                    )
                    parsed_cache[path] = (classification, custom_keys, reason)
                else:
                    ensure_no_output_aliases(output_targets, [path])
                    relative = path.relative_to(index_root).as_posix()
                    try:
                        data, snapshot = read_bytes_snapshot(
                            path,
                            label="MonoBehaviour metadata",
                            relative_to=index_root,
                        )
                    except (AnalysisError, OSError, ValueError):
                        raise
                    input_files[relative] = snapshot
                    try:
                        payload = parse_strict_json_bytes(
                            data, path, "MonoBehaviour metadata"
                        )
                        mapping = require_mapping(payload, f"metadata document {path}")
                        tree = mapping.get(config["type_tree_key"])
                        if not isinstance(tree, dict):
                            classification, custom_keys, reason = (
                                "parse_error",
                                [],
                                "type_tree_not_object",
                            )
                        else:
                            custom_keys = sorted(
                                str(key)
                                for key in tree
                                if key not in config["generic_keys"]
                            )
                            classification = (
                                "custom_fields_present"
                                if custom_keys
                                else "generic_header_only"
                            )
                            reason = None
                    except (AnalysisError, OSError, ValueError) as exc:
                        classification, custom_keys, reason = (
                            "parse_error",
                            [],
                            f"{type(exc).__name__}: {exc}",
                        )
                    parsed_cache[path] = (classification, custom_keys, reason)
                classifications[classification] += 1
                group_classifications[source_group][classification] += 1
                for key in custom_keys:
                    custom_key_counts[key] += 1
                if reason:
                    failure_reasons[reason] += 1
    except UnicodeDecodeError as exc:
        raise AnalysisError(f"index is not valid UTF-8 on metadata pass: {exc}") from exc
    except csv.Error as exc:
        raise AnalysisError(f"malformed CSV on metadata pass: {exc}") from exc
    except OSError as exc:
        raise AnalysisError(f"cannot stream index on metadata pass: {type(exc).__name__}: {exc}") from exc

    manifest_digest = hashlib.sha256()
    snapshots = [input_files[key] for key in sorted(input_files)]
    file_records = [snapshot.evidence() for snapshot in snapshots]
    for item in file_records:
        manifest_digest.update(
            f"{item['path']}\t{item['bytes']}\t{item['sha256']}\n".encode("utf-8")
        )
    metadata_summary = {
        "classification_counts": count_dict(classifications),
        "custom_key_counts": count_dict(custom_key_counts),
        "exported_row_count": exported_row_count,
        "failure_reason_counts": count_dict(failure_reasons),
        "group_classification_counts": {
            group: count_dict(counts) for group, counts in sorted(group_classifications.items())
        },
        "referenced_existing_file_count": len(input_files),
    }
    provenance = {
        "availability": "available" if metadata_root.is_dir() else "unavailable",
        "file_count": len(file_records),
        "files": file_records,
        "manifest_sha256": manifest_digest.hexdigest(),
        "root": str(metadata_root),
    }
    return (
        metadata_summary,
        provenance,
        snapshots,
        {
            "exported_row_count": exported_row_count,
            "mono_behaviour_count": mono_rows,
            "total_row_count": total_rows,
        },
    )


def follow_pointer(payload: Any, pointer: Iterable[str]) -> Any:
    current = payload
    for part in pointer:
        if not isinstance(current, dict) or part not in current:
            raise AnalysisError(f"probe evidence missing pointer component: {part}")
        current = current[part]
    return current


def analyze_probe(
    path: Path | None, config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, InputSnapshot | None]:
    if path is None:
        return (
            {
                "availability": "not_provided",
                "inference_boundary": "No probe evidence was provided.",
                "overall_recovery_estimate": None,
            },
            None,
            None,
        )
    payload, snapshot = read_json_snapshot(path, "probe evidence")
    samples = follow_pointer(payload, config["probe_pointer"])
    if not isinstance(samples, list):
        raise AnalysisError("probe samples pointer must resolve to an array")
    fields = config["probe_fields"]
    kind_counts: Counter[str] = Counter()
    kind_exact: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    group_exact: Counter[str] = Counter()
    exact_count = 0
    for index, sample in enumerate(samples):
        mapping = require_mapping(sample, f"probe sample {index}")
        kind = mapping.get(fields["sample_kind_field"], "<unspecified>")
        source = mapping.get(fields["source_field"], "")
        status = mapping.get(fields["status_field"], "<unspecified>")
        exact = mapping.get(fields["exact_field"])
        if not isinstance(kind, str) or not isinstance(source, str) or not isinstance(status, str):
            raise AnalysisError(f"probe sample {index} has non-string identity fields")
        if not isinstance(exact, bool):
            raise AnalysisError(f"probe sample {index} exact field must be boolean")
        group = group_for(source, config)
        kind_counts[kind] += 1
        status_counts[status] += 1
        group_counts[group] += 1
        if exact:
            exact_count += 1
            kind_exact[kind] += 1
            group_exact[group] += 1
    by_kind = {
        kind: {
            "exact_count": kind_exact.get(kind, 0),
            "exact_rate_within_sample": rate(kind_exact.get(kind, 0), count),
            "sample_count": count,
        }
        for kind, count in sorted(kind_counts.items())
    }
    by_group = {
        group: {
            "exact_count": group_exact.get(group, 0),
            "exact_rate_within_sample": rate(group_exact.get(group, 0), count),
            "sample_count": count,
        }
        for group, count in sorted(group_counts.items())
    }
    return (
        {
            "availability": "provided_exploratory_sample",
            "by_sample_kind": by_kind,
            "by_source_group": by_group,
            "exact_count": exact_count,
            "exact_rate_within_sample": rate(exact_count, len(samples)),
            "inference_boundary": (
                "These are provided targeted/stratified/control samples, not a random population sample. "
                "Rates apply only to these samples and must not be extrapolated."
            ),
            "overall_recovery_estimate": None,
            "sample_count": len(samples),
            "status_counts": count_dict(status_counts),
        },
        snapshot.evidence(),
        snapshot,
    )


def build_validation(
    summary: dict[str, Any],
    exported_statuses: list[str],
    metadata_pass_counts: dict[str, int],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    status_total = sum(summary["index"]["status_counts"].values())
    checks["status_sum_matches_monobehaviour_count"] = (
        status_total == summary["index"]["mono_behaviour_count"]
    )
    group_total = sum(
        item["mono_behaviour_count"] for item in summary["source_groups"]["groups"].values()
    )
    checks["source_group_sum_matches_monobehaviour_count"] = (
        group_total == summary["index"]["mono_behaviour_count"]
    )
    exported_count = sum(
        summary["index"]["status_counts"].get(status, 0) for status in exported_statuses
    )
    checks["metadata_classifications_match_exported_rows"] = (
        sum(summary["metadata"]["classification_counts"].values()) == exported_count
        == summary["metadata"]["exported_row_count"]
    )
    checks["second_index_pass_matches_first_pass"] = (
        metadata_pass_counts["total_row_count"] == summary["index"]["total_row_count"]
        and metadata_pass_counts["mono_behaviour_count"]
        == summary["index"]["mono_behaviour_count"]
        and metadata_pass_counts["exported_row_count"] == exported_count
    )
    checks["read_error_partition_matches"] = (
        summary["read_errors"]["parsed_expected_actual_count"]
        + summary["read_errors"]["unparsed_count"]
        == summary["read_errors"]["read_error_count"]
    )
    if summary["probe_evidence"]["availability"] == "provided_exploratory_sample":
        checks["probe_partition_matches"] = (
            sum(item["sample_count"] for item in summary["probe_evidence"]["by_sample_kind"].values())
            == summary["probe_evidence"]["sample_count"]
        )
        checks["probe_population_estimate_is_absent"] = (
            summary["probe_evidence"]["overall_recovery_estimate"] is None
        )
    checks["byte_size_not_inferred_from_error_text"] = (
        summary["evidence_availability"]["byte_size"]["source"]
        == "declared_index_column_only"
    )
    checks["raw_sha256_not_inferred"] = (
        summary["evidence_availability"]["raw_sha256"]["source"]
        == "declared_index_column_only"
    )
    checks["invalid_declared_evidence_is_not_unavailable"] = all(
        evidence["invalid_count"] == 0 or evidence["availability"] in {"invalid", "partial"}
        for evidence in summary["evidence_availability"].values()
    )
    checks["invalid_alias_values_are_explicitly_partial"] = all(
        evidence["invalid_alias_value_count"] == 0
        or evidence["availability"] == "partial"
        for evidence in summary["evidence_availability"].values()
    )
    return {"all_checks_passed": all(checks.values()), "checks": checks}


def markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def format_rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.6%}"


def build_report(summary: dict[str, Any]) -> str:
    lines = [
        "# UnityPy MonoBehaviour 证据边界分析",
        "",
        "## 结论先行",
        "",
        "### 观察到的事实",
        "",
        f"- 索引共 {summary['index']['total_row_count']:,} 行，其中 MonoBehaviour {summary['index']['mono_behaviour_count']:,} 行，来自 {summary['index']['source_count']:,} 个 source。",
        f"- MonoBehaviour 状态完全按索引原值统计；未知状态数为 {sum(summary['index']['unknown_status_counts'].values()):,}，不会被静默归入成功或失败。",
        f"- exported 行的实际 metadata 分类为：{json.dumps(summary['metadata']['classification_counts'], ensure_ascii=False, sort_keys=True)}。",
        f"- Expected-vs-actual 消息成功解析 {summary['read_errors']['parsed_expected_actual_count']:,} 条，未匹配 {summary['read_errors']['unparsed_count']:,} 条。expected 值只属于错误文本证据，不冒充索引声明的 byte_size。",
        f"- byte_size 证据为 `{summary['evidence_availability']['byte_size']['availability']}`；raw SHA-256 证据为 `{summary['evidence_availability']['raw_sha256']['availability']}`。",
        f"- byte_size 候选列/实际选列计数：`{markdown_escape(json.dumps(summary['evidence_availability']['byte_size']['present_columns'], ensure_ascii=False))}` / `{markdown_escape(json.dumps(summary['evidence_availability']['byte_size']['selected_column_counts'], ensure_ascii=False, sort_keys=True))}`；raw SHA-256 候选列/实际选列计数：`{markdown_escape(json.dumps(summary['evidence_availability']['raw_sha256']['present_columns'], ensure_ascii=False))}` / `{markdown_escape(json.dumps(summary['evidence_availability']['raw_sha256']['selected_column_counts'], ensure_ascii=False, sort_keys=True))}`。选择策略是按配置顺序取首个有效值；有效别名冲突会在写入前拒绝。",
        "",
        "### 独立判断",
        "",
        "- `read_error` 证明当前读取策略未能按类型树精确消费对象，不等同于源对象损坏、容器未发现或文件被截断。",
        "- `generic_header_only` 只证明通用 MonoBehaviour 头可见，不证明自定义实例字段已恢复。",
        "",
        "### 待验证假设",
        "",
        "- 具体 read_error 的根因可能涉及缺失类型树、脚本/资源版本漂移或解析器布局差异；本分析器不从索引单独确定唯一根因。",
        "- 任意额外恢复器必须另做 exact-consumption、版本身份、依赖许可和输出完整性验证。",
        "",
        "## 状态统计",
        "",
        "| 状态 | 数量 | 占 MonoBehaviour |",
        "|---|---:|---:|",
    ]
    total = summary["index"]["mono_behaviour_count"]
    for status, count in summary["index"]["status_counts"].items():
        lines.append(f"| `{markdown_escape(status)}` | {count:,} | {format_rate(rate(count, total))} |")

    lines.extend(
        [
            "",
            "## 来源分类（配置化规则）",
            "",
            "| 来源组 | source | MonoBehaviour | 状态 |",
            "|---|---:|---:|---|",
        ]
    )
    for group, item in summary["source_groups"]["groups"].items():
        lines.append(
            f"| `{markdown_escape(group)}` | {item['source_count']:,} | {item['mono_behaviour_count']:,} | `{markdown_escape(json.dumps(item['status_counts'], ensure_ascii=False, sort_keys=True))}` |"
        )

    lines.extend(
        [
            "",
            "## Exported metadata 完整性",
            "",
            "| 分类 | 数量 |",
            "|---|---:|",
        ]
    )
    for name, count in summary["metadata"]["classification_counts"].items():
        lines.append(f"| `{markdown_escape(name)}` | {count:,} |")

    lines.extend(
        [
            "",
            "## Expected-vs-actual 分布",
            "",
            f"- read_error 总数：{summary['read_errors']['read_error_count']:,}",
            f"- 已解析：{summary['read_errors']['parsed_expected_actual_count']:,}",
            f"- 未匹配：{summary['read_errors']['unparsed_count']:,}",
            f"- 已解析 expected / actual 总字节：{summary['read_errors']['expected_bytes_total']:,} / {summary['read_errors']['actual_bytes_total']:,}；差值 {summary['read_errors']['expected_minus_actual_total']:,}。",
            f"- actual bytes 分布：`{markdown_escape(json.dumps(summary['read_errors']['actual_bytes_counts'], ensure_ascii=False, sort_keys=True))}`",
            f"- expected/actual 组合数：{len(summary['read_errors']['expected_vs_actual_counts']):,}",
            "",
            "## 可选 probe 证据",
            "",
        ]
    )
    probe = summary["probe_evidence"]
    if probe["availability"] == "not_provided":
        lines.append("- 未提供 probe evidence；不生成恢复率判断。")
    else:
        lines.extend(
            [
                f"- 样本数：{probe['sample_count']:,}；样本内 exact：{probe['exact_count']:,}（{format_rate(probe['exact_rate_within_sample'])}）。",
                f"- 样本类型：`{markdown_escape(json.dumps(probe['by_sample_kind'], ensure_ascii=False, sort_keys=True))}`",
                "- 该比率只描述给定的定向/分层/控制样本；总体恢复估计固定为 `null`，不得外推。",
            ]
        )

    lines.extend(["", "## Safe fallback", ""])
    for item in summary["interpretation"]["safe_fallback"]:
        lines.append(f"- `{item['id']}`：{item['acceptance']}")
    lines.extend(["", "## Forbidden inference", ""])
    for item in summary["interpretation"]["forbidden_inference"]:
        lines.append(f"- {item}")

    provenance = summary["input_provenance"]
    lines.extend(
        [
            "",
            "## 来源与可复现性",
            "",
            f"- index：`{markdown_escape(provenance['index']['path'])}`；SHA-256 `{provenance['index']['sha256']}`。",
            f"- metadata manifest：{provenance['metadata']['file_count']:,} 文件；SHA-256 `{provenance['metadata']['manifest_sha256']}`。",
            f"- config SHA-256：`{provenance['config']['sha256']}`；contract SHA-256：`{provenance['contract']['sha256']}`。",
            f"- 严格对账：`{str(summary['validation']['all_checks_passed']).lower()}`。JSON 使用 UTF-8、sort_keys、allow_nan=False、尾随换行、原子替换并重开验证。",
            "",
        ]
    )
    return "\n".join(lines)


def absolute_lexical(path: Path) -> Path:
    """Return an absolute path without resolving symlinks or reparse points."""

    return Path(os.path.abspath(os.fspath(path)))


def is_reparse_or_symlink(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & reparse_flag)


def reject_reparse_components(path: Path, label: str) -> None:
    absolute = absolute_lexical(path)
    for component in reversed((absolute, *absolute.parents)):
        if component == Path(component.anchor) or not os.path.lexists(component):
            continue
        try:
            current = os.lstat(component)
        except OSError as exc:
            raise AnalysisError(
                f"cannot inspect {label} path component: {component}: {exc}"
            ) from exc
        if is_reparse_or_symlink(current):
            raise AnalysisError(f"{label} path contains a symlink/reparse point: {component}")


def nofollow_open_flags(base: int) -> int:
    return base | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))


if os.name == "nt":
    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]


    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]


    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_void_p),
            ("Information", ctypes.c_size_t),
        ]


    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("CreationTime", wintypes.FILETIME),
            ("LastAccessTime", wintypes.FILETIME),
            ("LastWriteTime", wintypes.FILETIME),
            ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD),
            ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD),
            ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        ]


    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]


    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _NTDLL = ctypes.WinDLL("ntdll", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _KERNEL32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _KERNEL32.SetFileInformationByHandle.restype = wintypes.BOOL
    _NTDLL.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _NTDLL.NtCreateFile.restype = ctypes.c_long
    _NTDLL.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    _NTDLL.NtSetInformationFile.restype = ctypes.c_long
    _NTDLL.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _NTDLL.RtlNtStatusToDosError.restype = wintypes.ULONG

    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_TRAVERSE = 0x00000020
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
    _OPEN_EXISTING = 3
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_OPEN_IF = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_DISPOSITION_INFO_CLASS = 4
    _NT_FILE_RENAME_INFORMATION_CLASS = 10


def _win_close_handle(handle: int) -> None:
    if os.name == "nt" and handle not in (0, _INVALID_HANDLE_VALUE):
        _KERNEL32.CloseHandle(wintypes.HANDLE(handle))


def _win_raise_status(status: int, label: str) -> None:
    code = int(_NTDLL.RtlNtStatusToDosError(status))
    error = ctypes.WinError(code)
    error.args = (*error.args, label)
    raise error


def _win_handle_information(handle: int) -> Any:
    information = _ByHandleFileInformation()
    if not _KERNEL32.GetFileInformationByHandle(
        wintypes.HANDLE(handle), ctypes.byref(information)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return information


def _win_directory_identity(handle: int) -> tuple[int, int, int]:
    information = _win_handle_information(handle)
    return (
        int(information.VolumeSerialNumber),
        int(information.FileIndexHigh),
        int(information.FileIndexLow),
    )


def _win_validate_handle_kind(handle: int, *, directory: bool, label: str) -> None:
    information = _win_handle_information(handle)
    attributes = int(information.FileAttributes)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise AnalysisError(f"{label} is a symlink/reparse point")
    is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != directory:
        expected = "directory" if directory else "regular file"
        raise AnalysisError(f"{label} must be a {expected}")


def _win_nt_open_relative(
    root_handle: int,
    name: str,
    *,
    directory: bool,
    create: bool,
    writable: bool = False,
    protect_generation: bool = False,
) -> int:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise AnalysisError(f"unsafe relative output component: {name!r}")
    name_buffer = ctypes.create_unicode_buffer(name)
    name_bytes = len(name.encode("utf-16-le"))
    unicode_name = _UnicodeString(
        Length=name_bytes,
        MaximumLength=name_bytes + ctypes.sizeof(wintypes.WCHAR),
        Buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = _ObjectAttributes(
        Length=ctypes.sizeof(_ObjectAttributes),
        RootDirectory=wintypes.HANDLE(root_handle),
        ObjectName=ctypes.pointer(unicode_name),
        Attributes=_OBJ_CASE_INSENSITIVE,
        SecurityDescriptor=None,
        SecurityQualityOfService=None,
    )
    io_status = _IoStatusBlock()
    result = wintypes.HANDLE()
    if directory:
        desired_access = (
            _FILE_LIST_DIRECTORY
            | _FILE_TRAVERSE
            | _FILE_READ_ATTRIBUTES
            | _SYNCHRONIZE
        )
        file_attributes = _FILE_ATTRIBUTE_DIRECTORY
        options = (
            _FILE_DIRECTORY_FILE
            | _FILE_SYNCHRONOUS_IO_NONALERT
            | _FILE_OPEN_REPARSE_POINT
        )
    else:
        desired_access = _GENERIC_READ | _SYNCHRONIZE
        if create or writable or protect_generation:
            desired_access |= _DELETE
        if writable:
            desired_access |= _GENERIC_WRITE
        file_attributes = _FILE_ATTRIBUTE_NORMAL
        options = (
            _FILE_NON_DIRECTORY_FILE
            | _FILE_SYNCHRONOUS_IO_NONALERT
            | _FILE_OPEN_REPARSE_POINT
        )
    disposition = _FILE_CREATE if create and not directory else (
        _FILE_OPEN_IF if create else _FILE_OPEN
    )
    share_mode = _FILE_SHARE_READ if protect_generation else _FILE_SHARE_ALL
    status = int(
        _NTDLL.NtCreateFile(
            ctypes.byref(result),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            file_attributes,
            share_mode,
            disposition,
            options,
            None,
            0,
        )
    )
    if status < 0:
        _win_raise_status(status, f"relative open/create {name}")
    handle = int(result.value)
    try:
        _win_validate_handle_kind(handle, directory=directory, label=name)
    except Exception:
        _win_close_handle(handle)
        raise
    return handle


def _win_open_directory_path(path: Path, *, create: bool) -> int:
    absolute = absolute_lexical(path)
    anchor = absolute.anchor
    if not anchor:
        raise AnalysisError(f"output directory is not absolute: {absolute}")
    handle = _KERNEL32.CreateFileW(
        anchor,
        _GENERIC_READ,
        _FILE_SHARE_ALL,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if int(handle) == _INVALID_HANDLE_VALUE:
        raise AnalysisError(
            f"cannot open output volume root: {anchor}: {ctypes.WinError(ctypes.get_last_error())}"
        )
    current = int(handle)
    try:
        _win_validate_handle_kind(current, directory=True, label=f"output root {anchor}")
        for component in absolute.parts[1:]:
            try:
                child = _win_nt_open_relative(
                    current,
                    component,
                    directory=True,
                    create=create,
                )
            except OSError as exc:
                raise AnalysisError(
                    f"cannot {'create' if create else 'open'} stable output directory "
                    f"component {component!r}: {exc}"
                ) from exc
            _win_close_handle(current)
            current = child
        return current
    except Exception:
        _win_close_handle(current)
        raise


def _win_file_handle(file_object: Any) -> int:
    return int(msvcrt.get_osfhandle(file_object.fileno()))


def _win_handle_path(file_object: Any) -> Path:
    """Query the kernel's current path for one exact open file object."""

    handle = wintypes.HANDLE(_win_file_handle(file_object))
    required = int(_KERNEL32.GetFinalPathNameByHandleW(handle, None, 0, 0))
    if required <= 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = int(
        _KERNEL32.GetFinalPathNameByHandleW(
            handle,
            buffer,
            len(buffer),
            0,
        )
    )
    if written <= 0 or written >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def _win_handle_basename(file_object: Any) -> str:
    """Query the kernel's current basename for one exact open file object."""

    current_path = _win_handle_path(file_object)
    name = current_path.name
    if not name or Path(name).name != name:
        raise AnalysisError(
            f"cannot derive current basename from open Windows handle: {current_path!s}"
        )
    return name


def _win_rename_handle(
    file_object: Any,
    root_handle: int,
    target_name: str,
    *,
    replace_existing: bool,
) -> None:
    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BYTE),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(target_name) + 1)),
        ]

    information = _FileRenameInformation()
    information.ReplaceIfExists = int(replace_existing)
    information.RootDirectory = wintypes.HANDLE(root_handle)
    information.FileNameLength = len(target_name.encode("utf-16-le"))
    information.FileName = target_name
    information_size = _FileRenameInformation.FileName.offset + information.FileNameLength
    io_status = _IoStatusBlock()
    status = int(_NTDLL.NtSetInformationFile(
        wintypes.HANDLE(_win_file_handle(file_object)),
        ctypes.byref(io_status),
        ctypes.byref(information),
        information_size,
        _NT_FILE_RENAME_INFORMATION_CLASS,
    ))
    if status < 0:
        _win_raise_status(status, f"relative rename to {target_name}")


def _win_delete_handle(file_object: Any) -> None:
    information = _FileDispositionInformation(DeleteFile=True)
    if not _KERNEL32.SetFileInformationByHandle(
        wintypes.HANDLE(_win_file_handle(file_object)),
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.WinError(ctypes.get_last_error())
        if getattr(error, "winerror", None) not in {2, 3, 1168}:
            raise error


class StableOpenFile:
    """An output file opened relative to one pinned output-directory object."""

    def __init__(self, directory: "StableOutputDirectory", name: str, handle: Any) -> None:
        self.directory = directory
        self.name = name
        self.handle = handle
        self.temporary = False
        self.closed = False
        # A Windows rename can complete before its wrapper or postcondition
        # coordinator fails.  Keep both atomic outcomes attached to the exact
        # open object until a later independent observation resolves them.
        self.rename_candidate_names: tuple[str, ...] = ()
        self.rename_expected_identity: tuple[int, int, int, int] | None = None
        self.rename_coordination_evidence: dict[str, Any] | None = None
        self.rename_coordination_history: list[dict[str, Any]] = []

    @property
    def path(self) -> Path:
        return self.directory.lexical_path / self.name

    def current_path(self) -> Path:
        """Return a path that names this exact open object at query time."""

        if os.name == "nt" and not self.closed:
            try:
                return _win_handle_path(self.handle)
            except BaseException:
                if self.rename_candidate_names and self.rename_expected_identity is not None:
                    resolved = self.directory.resolve_windows_open_file_location(
                        self,
                        self.rename_candidate_names,
                        self.rename_expected_identity,
                    )
                    return self.directory.lexical_path / resolved
                raise
        return self.path

    def identity(self) -> tuple[int, int, int, int]:
        return stat_identity(os.fstat(self.handle.fileno()))

    def read_exact_snapshot(self, label: str) -> ContentSnapshot:
        try:
            self.handle.seek(0)
            before = os.fstat(self.handle.fileno())
            data = self.handle.read()
            after = os.fstat(self.handle.fileno())
        except OSError as exc:
            raise AnalysisError(f"cannot read {label}: {self.path}: {exc}") from exc
        identity = stat_identity(before)
        if identity != stat_identity(after) or len(data) != before.st_size:
            raise AnalysisError(f"{label} changed while reading: {self.path}")
        return ContentSnapshot(path=self.path, data=data, identity=identity)

    def write_exact(self, data: bytes, label: str) -> tuple[int, int, int, int]:
        try:
            self.handle.seek(0)
            self.handle.truncate(0)
            self.handle.write(data)
            self.handle.flush()
            os.fsync(self.handle.fileno())
        except OSError as exc:
            raise AnalysisError(f"cannot stage {label}: {self.path}: {exc}") from exc
        reopened = self.read_exact_snapshot(f"staged {label}")
        if reopened.data != data:
            raise AnalysisError(f"staged {label} failed exact handle verification: {self.path}")
        return reopened.identity

    def delete(self) -> None:
        if self.closed:
            return
        try:
            if os.name == "nt":
                _win_delete_handle(self.handle)
            else:
                try:
                    current = os.stat(
                        self.name,
                        dir_fd=self.directory.handle,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return
                if stat_identity(current)[:2] == self.identity()[:2]:
                    os.unlink(self.name, dir_fd=self.directory.handle)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        if not self.closed:
            self.handle.close()
            self.closed = True


class StableOutputDirectory:
    """A no-reparse directory whose mutations are all relative to a pinned handle."""

    def __init__(self, lexical_path: Path, handle: Any, identity: Any) -> None:
        self.lexical_path = lexical_path
        self.handle = handle
        self.identity = identity
        self.commit_guard: Callable[[], None] | None = None
        self.commit_replace_existing: dict[str, bool] = {}

    @classmethod
    def open_or_create(cls, path: Path) -> "StableOutputDirectory":
        lexical = absolute_lexical(path)
        if os.name == "nt":
            handle = _win_open_directory_path(lexical, create=True)
            return cls(lexical, handle, _win_directory_identity(handle))

        flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | int(
            getattr(os, "O_NOFOLLOW", 0)
        )
        parts = lexical.parts
        descriptor = os.open(parts[0], flags)
        try:
            for component in parts[1:]:
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    os.mkdir(component, mode=0o777, dir_fd=descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                raise AnalysisError(f"output-dir is not a directory: {lexical}")
            return cls(lexical, descriptor, stat_identity(opened)[:2])
        except Exception:
            os.close(descriptor)
            raise

    def close(self) -> None:
        if self.handle is None:
            return
        if os.name == "nt":
            _win_close_handle(int(self.handle))
        else:
            os.close(int(self.handle))
        self.handle = None

    def verify_lexical_identity(self) -> None:
        try:
            if os.name == "nt":
                current = _win_open_directory_path(self.lexical_path, create=False)
                try:
                    identity = _win_directory_identity(current)
                finally:
                    _win_close_handle(current)
            else:
                current_dir = type(self).open_existing(self.lexical_path)
                try:
                    identity = current_dir.identity
                finally:
                    current_dir.close()
        except (AnalysisError, OSError) as exc:
            raise AnalysisError(
                f"output directory identity changed after it was pinned: {self.lexical_path}: {exc}"
            ) from exc
        if identity != self.identity:
            raise AnalysisError(
                f"output directory identity changed after it was pinned: {self.lexical_path}"
            )

    @classmethod
    def open_existing(cls, path: Path) -> "StableOutputDirectory":
        lexical = absolute_lexical(path)
        if os.name == "nt":
            handle = _win_open_directory_path(lexical, create=False)
            return cls(lexical, handle, _win_directory_identity(handle))
        flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | int(
            getattr(os, "O_NOFOLLOW", 0)
        )
        parts = lexical.parts
        descriptor = os.open(parts[0], flags)
        try:
            for component in parts[1:]:
                child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return cls(lexical, descriptor, stat_identity(os.fstat(descriptor))[:2])
        except Exception:
            os.close(descriptor)
            raise

    def open_file(
        self,
        name: str,
        *,
        create: bool,
        writable: bool,
        protect_generation: bool = False,
    ) -> StableOpenFile:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise AnalysisError(f"output artifact must be a basename: {name!r}")
        if create and protect_generation:
            raise AnalysisError("a generation-protected output open cannot create a file")
        if os.name == "nt":
            raw_handle = _win_nt_open_relative(
                int(self.handle),
                name,
                directory=False,
                create=create,
                writable=writable,
                protect_generation=protect_generation,
            )
            flags = int(getattr(os, "O_BINARY", 0)) | (os.O_RDWR if writable else os.O_RDONLY)
            try:
                descriptor = msvcrt.open_osfhandle(raw_handle, flags)
                file_object = os.fdopen(
                    descriptor,
                    "r+b" if writable else "rb",
                    buffering=0,
                )
            except Exception:
                _win_close_handle(raw_handle)
                raise
            return StableOpenFile(self, name, file_object)

        flags = (os.O_RDWR if writable else os.O_RDONLY) | int(
            getattr(os, "O_NOFOLLOW", 0)
        )
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(name, flags, 0o600, dir_fd=int(self.handle))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            os.close(descriptor)
            raise AnalysisError(f"output artifact must be a regular file: {name}")
        return StableOpenFile(
            self,
            name,
            os.fdopen(descriptor, "r+b" if writable else "rb", buffering=0),
        )

    def protect_open_file_generation(
        self,
        opened: StableOpenFile,
        expected: bytes,
        expected_identity: tuple[int, int, int, int],
        label: str,
    ) -> None:
        """Reopen one private file under a Windows mandatory no-write share lease."""

        if opened.directory is not self:
            raise AnalysisError("generation protection crossed stable output directories")
        if os.name != "nt":
            current = opened.read_exact_snapshot(label)
            if current.identity != expected_identity or current.data != expected:
                raise AnalysisError(f"{label} changed before generation protection")
            return
        name = opened.name
        was_temporary = opened.temporary
        opened.close()
        protected = self.open_file(
            name,
            create=False,
            writable=False,
            protect_generation=True,
        )
        try:
            current = protected.read_exact_snapshot(label)
            if current.identity != expected_identity or current.data != expected:
                raise AnalysisError(f"{label} changed before generation protection")
        except Exception:
            protected.close()
            raise
        opened.handle = protected.handle
        opened.closed = False
        opened.temporary = was_temporary
        protected.handle = None
        protected.closed = True

    def entry_exists(self, name: str) -> bool:
        try:
            opened = self.open_file(name, create=False, writable=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if getattr(exc, "winerror", None) in {2, 3} or getattr(exc, "errno", None) == 2:
                return False
            if getattr(exc, "winerror", None) == 5:
                return True
            raise
        opened.close()
        return True

    def entry_identity(self, name: str) -> tuple[int, int, int, int] | None:
        """Return one basename-relative identity without following a mutable parent."""

        try:
            opened = self.open_file(name, create=False, writable=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if getattr(exc, "winerror", None) in {2, 3} or getattr(exc, "errno", None) == 2:
                return None
            raise
        try:
            return opened.identity()
        finally:
            opened.close()

    def direct_windows_entry_identity(
        self, name: str
    ) -> tuple[int, int, int, int] | None:
        """Probe a candidate through the pinned NT directory handle directly.

        This deliberately does not call ``entry_identity`` or
        ``StableOpenFile.identity``.  It is an independent coordinator path for
        the case where a higher-level basename-relative reopen/identity wrapper
        fails after ``NtSetInformationFile`` has already moved the source.
        """

        if os.name != "nt":
            return self.entry_identity(name)
        raw_handle: int | None = None
        file_object: Any | None = None
        try:
            raw_handle = _win_nt_open_relative(
                int(self.handle),
                name,
                directory=False,
                create=False,
                writable=False,
            )
            descriptor = msvcrt.open_osfhandle(
                raw_handle,
                int(getattr(os, "O_BINARY", 0)) | os.O_RDONLY,
            )
            raw_handle = None
            file_object = os.fdopen(descriptor, "rb", buffering=0)
            return stat_identity(os.fstat(file_object.fileno()))
        except FileNotFoundError:
            return None
        except OSError as exc:
            if getattr(exc, "winerror", None) in {2, 3} or getattr(exc, "errno", None) == 2:
                return None
            raise
        finally:
            if file_object is not None:
                file_object.close()
            elif raw_handle is not None:
                _win_close_handle(raw_handle)

    def resolve_windows_open_file_location(
        self,
        source: StableOpenFile,
        candidate_names: Sequence[str],
        expected_identity: tuple[int, int, int, int],
    ) -> str:
        """Resolve one exact open generation using independent kernel signals.

        The primary handle-path query and the ordinary basename-relative reopen
        are intentionally redundant with a direct NT-relative probe.  A single
        coordinator fault, or two consecutive faults in different mechanisms,
        therefore cannot send transaction bookkeeping back to a stale cached
        basename.  Ambiguous or wholly unobservable outcomes fail closed while
        retaining the exact handle and candidate evidence on ``source``.
        """

        normalized_candidates: list[str] = []
        for name in candidate_names:
            if Path(name).name != name or name in {"", ".", ".."}:
                raise AnalysisError(f"unsafe Windows rename candidate: {name!r}")
            if name.casefold() not in {item.casefold() for item in normalized_candidates}:
                normalized_candidates.append(name)
        evidence: dict[str, Any] = {
            "candidate_names": list(normalized_candidates),
            "expected_identity": list(expected_identity),
            "handle_path": None,
            "ordinary_candidate_probes": {},
            "direct_candidate_probes": {},
            "errors": [],
        }
        handle_name: str | None = None
        try:
            handle_name = _win_handle_basename(source.handle)
            evidence["handle_path"] = handle_name
        except BaseException as exc:
            evidence["errors"].append(
                {
                    "method": "GetFinalPathNameByHandleW",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        matches: dict[str, set[str]] = {}

        def record_match(name: str, method: str, identity: Any) -> None:
            if identity == expected_identity:
                matches.setdefault(name.casefold(), set()).add(method)

        for name in normalized_candidates:
            try:
                identity = self.entry_identity(name)
                evidence["ordinary_candidate_probes"][name] = (
                    None if identity is None else list(identity)
                )
                record_match(name, "ordinary-relative-reopen", identity)
            except BaseException as exc:
                evidence["errors"].append(
                    {
                        "method": "ordinary-relative-reopen",
                        "name": name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        # Always run the direct probe when the primary evidence did not already
        # converge uniquely with a corroborating handle basename.  This keeps
        # the fallback executable in real tests rather than dead code.
        handle_key = handle_name.casefold() if handle_name is not None else None
        primary_converged = (
            handle_key is not None
            and handle_key in matches
            and len(matches) == 1
        )
        if not primary_converged:
            for name in normalized_candidates:
                try:
                    identity = self.direct_windows_entry_identity(name)
                    evidence["direct_candidate_probes"][name] = (
                        None if identity is None else list(identity)
                    )
                    record_match(name, "direct-nt-relative-reopen", identity)
                except BaseException as exc:
                    evidence["errors"].append(
                        {
                            "method": "direct-nt-relative-reopen",
                            "name": name,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        candidate_by_key = {name.casefold(): name for name in normalized_candidates}
        resolved_key: str | None = None
        if handle_key in candidate_by_key and handle_key in matches:
            resolved_key = handle_key
        elif len(matches) == 1:
            resolved_key = next(iter(matches))

        if resolved_key is None:
            source.rename_candidate_names = tuple(normalized_candidates)
            source.rename_expected_identity = expected_identity
            source.rename_coordination_evidence = evidence
            source.rename_coordination_history.append(evidence)
            raise AnalysisError(
                "cannot reconcile Windows rename from independent kernel evidence: "
                + json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
            )

        resolved_name = candidate_by_key[resolved_key]
        source.name = resolved_name
        source.rename_candidate_names = ()
        source.rename_expected_identity = None
        evidence["resolved_name"] = resolved_name
        evidence["resolved_methods"] = sorted(matches[resolved_key])
        source.rename_coordination_evidence = evidence
        source.rename_coordination_history.append(evidence)
        return resolved_name

    def reconcile_windows_rename(
        self,
        source: StableOpenFile,
        source_name: str,
        target_name: str,
        expected_identity: tuple[int, int, int, int],
    ) -> bool:
        """Bind Python bookkeeping to the exact kernel rename result.

        NtSetInformationFile may complete before a wrapper, tracing hook or
        asynchronous exception escapes.  The cached ``source.name`` is not an
        outcome oracle.  Query the exact open handle's current basename and
        prove that basename still resolves, relative to the pinned directory,
        to the same file identity.  Return whether the requested move occurred.
        """

        current_name = self.resolve_windows_open_file_location(
            source,
            (source_name, target_name),
            expected_identity,
        )
        if current_name.casefold() == target_name.casefold():
            return True
        if current_name.casefold() == source_name.casefold():
            return False
        raise AnalysisError(
            "cannot reconcile Windows rename: the exact source object is at an "
            f"unexpected basename {current_name!r}, expected {source_name!r} or "
            f"{target_name!r}"
        )

    def replace(
        self,
        source: StableOpenFile,
        target_name: str,
        *,
        replace_existing: bool = True,
    ) -> None:
        if Path(target_name).name != target_name or target_name in {"", ".", ".."}:
            raise AnalysisError(f"output artifact must be a basename: {target_name!r}")
        if source.directory is not self:
            raise AnalysisError("artifact rename crossed stable output directories")
        if os.name == "nt":
            source_name = source.name
            expected_identity = source.identity()
            source.rename_candidate_names = (source_name, target_name)
            source.rename_expected_identity = expected_identity
            try:
                _win_rename_handle(
                    source.handle,
                    int(self.handle),
                    target_name,
                    replace_existing=replace_existing,
                )
            except BaseException:
                # Always synchronize before propagating.  Higher transaction
                # layers can then roll back the operation that actually happened
                # instead of the operation the wrapper claims happened.
                self.reconcile_windows_rename(
                    source,
                    source_name,
                    target_name,
                    expected_identity,
                )
                raise
            moved = self.reconcile_windows_rename(
                source,
                source_name,
                target_name,
                expected_identity,
            )
            if not moved:
                raise AnalysisError(
                    f"Windows rename returned success but source remained at {source_name!r}"
                )
        else:
            if replace_existing:
                os.replace(
                    source.name,
                    target_name,
                    src_dir_fd=int(self.handle),
                    dst_dir_fd=int(self.handle),
                )
            else:
                os.link(
                    source.name,
                    target_name,
                    src_dir_fd=int(self.handle),
                    dst_dir_fd=int(self.handle),
                    follow_symlinks=False,
                )
                try:
                    os.unlink(source.name, dir_fd=int(self.handle))
                except Exception:
                    os.unlink(target_name, dir_fd=int(self.handle))
                    raise
            source.name = target_name


def open_exclusive_temporary(
    directory: StableOutputDirectory, final_path: Path, role: str
) -> StableOpenFile:
    token = secrets.token_hex(16)
    name = f".ga-monobehaviour-{role}-{token}.tmp"
    try:
        opened = directory.open_file(name, create=True, writable=True)
    except FileExistsError as exc:
        raise AnalysisError(
            f"exclusive temporary path collision: {directory.lexical_path / name}"
        ) from exc
    except OSError as exc:
        if directory.entry_exists(name):
            raise AnalysisError(
                f"exclusive temporary path collision: {directory.lexical_path / name}"
            ) from exc
        raise AnalysisError(
            f"cannot create exclusive temporary file: {directory.lexical_path / name}: {exc}"
        ) from exc
    opened_stat = os.fstat(opened.handle.fileno())
    if not stat.S_ISREG(opened_stat.st_mode) or int(getattr(opened_stat, "st_nlink", 1)) != 1:
        opened.close()
        raise AnalysisError(f"exclusive temporary file has unsafe identity: {opened.path}")
    opened.temporary = True
    return opened


def write_open_temporary_exact(
    opened: StableOpenFile, data: bytes, label: str
) -> tuple[int, int, int, int]:
    return opened.write_exact(data, label)


def reopen_artifact_exact(
    directory: StableOutputDirectory,
    name: str,
    expected: bytes,
    label: str,
    expected_identity: tuple[int, int, int, int] | None = None,
) -> None:
    opened = directory.open_file(name, create=False, writable=False)
    try:
        reopened = opened.read_exact_snapshot(label)
    finally:
        opened.close()
    if reopened.data != expected or (
        expected_identity is not None and reopened.identity != expected_identity
    ):
        raise AnalysisError(
            f"{label} failed exact stable-directory reopen verification: "
            f"{directory.lexical_path / name}"
        )


def replace_artifact(
    directory: StableOutputDirectory, source: StableOpenFile, target_name: str
) -> None:
    if directory.commit_guard is not None:
        directory.commit_guard()
    directory.verify_lexical_identity()
    if directory.commit_guard is not None:
        directory.commit_guard()
    directory.replace(
        source,
        target_name,
        replace_existing=directory.commit_replace_existing.get(target_name, True),
    )


def restore_artifact(
    directory: StableOutputDirectory, source: StableOpenFile, target_name: str
) -> None:
    """Restore without overwriting a destination that appeared during rollback."""

    directory.replace(source, target_name, replace_existing=False)


def restore_original_artifact(
    directory: StableOutputDirectory,
    original: OriginalArtifact,
) -> None:
    """Restore one old generation and synchronize its transaction state.

    A Windows rename can complete in the kernel before a wrapper or asynchronous
    exception escapes.  ``directory.replace`` has already reconciled the exact
    open handle's basename in that case; mirror the observed postcondition into
    the owning ``OriginalArtifact`` before propagating the fault.
    """

    backup = original.backup_file
    if backup is None:
        raise AnalysisError(f"rollback backup is missing: {original.final_path}")
    try:
        restore_artifact(directory, backup, original.final_path.name)
    except BaseException:
        if backup.name.casefold() == original.final_path.name.casefold():
            backup.temporary = False
            original.retired = False
        raise
    backup.temporary = False
    original.retired = False


def cleanup_created_files(files: Iterable[StableOpenFile]) -> dict[str, Any]:
    """Attempt every cleanup/close and report any recovered or unresolved warning."""

    owned = list(dict.fromkeys(files))
    warnings: list[dict[str, Any]] = []
    expected_identities: dict[StableOpenFile, tuple[int, int, int, int] | None] = {}
    cleanup_state_unknown = False
    for opened in owned:
        try:
            expected_identities[opened] = opened.identity() if not opened.closed else None
            if (
                os.name == "nt"
                and not opened.closed
                and opened.rename_candidate_names
                and opened.rename_expected_identity is not None
            ):
                opened.directory.resolve_windows_open_file_location(
                    opened,
                    opened.rename_candidate_names,
                    opened.rename_expected_identity,
                )
        except BaseException as exc:
            expected_identities[opened] = None
            cleanup_state_unknown = True
            warnings.append(
                {
                    "action": "snapshot-cleanup-identity",
                    "attempt": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                    "name": opened.name,
                }
            )
    for opened in owned:
        if opened.temporary:
            for attempt in range(1, 3):
                try:
                    opened.delete()
                    break
                except Exception as exc:
                    warnings.append(
                        {
                            "action": "delete",
                            "attempt": attempt,
                            "error": f"{type(exc).__name__}: {exc}",
                            "name": opened.name,
                        }
                    )
        for attempt in range(1, 3):
            try:
                opened.close()
                break
            except Exception as exc:
                warnings.append(
                    {
                        "action": "close",
                        "attempt": attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                        "name": opened.name,
                    }
                )

    remaining: list[str] = []
    for opened in owned:
        if not opened.temporary:
            continue
        try:
            if (
                os.name == "nt"
                and not opened.closed
                and opened.rename_candidate_names
                and opened.rename_expected_identity is not None
            ):
                opened.directory.resolve_windows_open_file_location(
                    opened,
                    opened.rename_candidate_names,
                    opened.rename_expected_identity,
                )
            current_identity = opened.directory.entry_identity(opened.name)
        except BaseException as exc:
            warnings.append(
                {
                    "action": "verify-cleanup",
                    "attempt": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                    "name": opened.name,
                }
            )
            cleanup_state_unknown = True
            continue
        expected_identity = expected_identities.get(opened)
        if current_identity is None:
            opened.temporary = False
        elif expected_identity is not None and current_identity == expected_identity:
            remaining.append(opened.name)
        else:
            # A different generation claimed the basename.  It is not an owned
            # cleanup target and must never be deleted or reported as such.
            warnings.append(
                {
                    "action": "preserve-nonowned-cleanup-generation",
                    "attempt": 1,
                    "error": "basename now resolves to a different file identity",
                    "name": opened.name,
                }
            )
            opened.temporary = False
    unresolved = sorted(set(remaining))
    if unresolved or cleanup_state_unknown:
        state = "warnings"
    elif warnings:
        state = "recovered_warnings"
    else:
        state = "clean"
    return {
        "cleanup_state": state,
        "cleanup_warnings": warnings,
        "cleanup_required_paths": unresolved,
    }


def collect_rename_coordination_failover_evidence(
    files: Sequence[StableOpenFile],
) -> list[dict[str, Any]]:
    """Return machine-readable evidence for coordinator paths that faulted.

    Successful ordinary reconciliations are intentionally omitted.  When one
    signal failed, however, retain every independent candidate probe and the
    exact expected generation identity so an outer recovery system does not
    need to infer the real rename outcome from an exception string or a stale
    pre-syscall basename.
    """

    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for opened in files:
        object_key = id(opened)
        if object_key in seen:
            continue
        seen.add(object_key)
        for event in opened.rename_coordination_history:
            if not event.get("errors"):
                continue
            result.append(
                {
                    "current_cached_basename": opened.name,
                    "exact_open_object_expected_identity": event.get(
                        "expected_identity"
                    ),
                    "evidence": event,
                }
            )
    return result


class ArtifactCommitResult(dict[str, Any]):
    """Dictionary-compatible commit result that owns published leases.

    A plain ``dict`` cannot carry the mandatory Windows generation leases over
    the function-return boundary.  Callers must consume this mapping while it
    is open, then call ``close`` (preferably through ``with``).  The CLI follows
    the same protocol: serialize while leases are held, authoritatively release
    them, and only then emit the already-consumed success payload.
    """

    def __init__(
        self,
        transaction: dict[str, Any],
        directory: StableOutputDirectory,
        published: Sequence[StagedArtifact],
    ) -> None:
        super().__init__(transaction)
        self._directory = directory
        self._published = tuple(published)
        for index, item in enumerate(self._published):
            item.stage_file.release_role = f"primary-{index}"
        self._closed = False
        self["handoff_protocol"] = "published-generation-lease-owner-v1"
        self["handoff_state"] = "lease_held"
        self["published_lease_state"] = "held"
        self.setdefault("release_errors", [])
        self.setdefault("release_required_paths", [])

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot_for_consumption(self) -> dict[str, Any]:
        if self._closed:
            raise AnalysisError("artifact commit result was consumed after lease release")
        snapshot = dict(self)
        snapshot["handoff_state"] = "consumed_while_lease_held"
        snapshot["published_lease_state"] = "held_during_consumption"
        return snapshot

    def _record_release_error(
        self,
        errors: list[dict[str, str]],
        operation: str,
        artifact: str,
        exc: BaseException,
    ) -> None:
        errors.append(
            {
                "artifact": artifact,
                "operation": operation,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    def _close_opened(
        self,
        opened: StableOpenFile,
        label: str,
        warnings: list[dict[str, Any]],
        errors: list[dict[str, str]],
    ) -> bool:
        for attempt in range(1, 3):
            try:
                opened.close()
                return True
            except BaseException as exc:
                warnings.append(
                    {
                        "action": "release-published-lease",
                        "attempt": attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                        "name": opened.name,
                    }
                )
                # A wrapper may raise after the underlying close succeeded.
                # ``closed`` is the exact in-process postcondition in that case.
                if opened.closed:
                    return True
        errors.append(
            {
                "artifact": str(opened.path),
                "operation": label,
                "error": "published generation lease could not be closed after two attempts",
            }
        )
        return False

    def close(self) -> None:
        if self._closed:
            return
        directory = self._directory
        warnings = list(self.get("cleanup_warnings", []))
        errors: list[dict[str, str]] = []
        guardian_pairs: list[tuple[StagedArtifact, StableOpenFile, StableOpenFile]] = []

        # Establish two independent leases for each final before releasing the
        # commit handles.  One guardian remains while the other is closed, so a
        # fault-after-close wrapper never creates an unguarded internal gap.
        try:
            directory.verify_lexical_identity()
            for index, item in enumerate(self._published):
                exact = item.stage_file.read_exact_snapshot(
                    f"published artifact {index} before handoff release"
                )
                if exact.identity != item.identity or exact.data != item.data:
                    raise AnalysisError(
                        f"published artifact handle drifted before handoff release: {item.final_path}"
                    )
                first = StableOpenFile(
                    directory,
                    item.final_path.name,
                    os.fdopen(os.dup(item.stage_file.handle.fileno()), "rb", buffering=0),
                )
                first.release_role = f"guardian-A-{index}"
                try:
                    first_snapshot = first.read_exact_snapshot(
                        f"published guardian A {index}"
                    )
                    if (
                        first_snapshot.identity != item.identity
                        or first_snapshot.data != item.data
                    ):
                        raise AnalysisError(
                            f"published guardian A does not name committed generation: {item.final_path}"
                        )
                    second = StableOpenFile(
                        directory,
                        item.final_path.name,
                        os.fdopen(os.dup(item.stage_file.handle.fileno()), "rb", buffering=0),
                    )
                    second.release_role = f"guardian-B-{index}"
                    second_snapshot = second.read_exact_snapshot(
                        f"published guardian B {index}"
                    )
                    if (
                        second_snapshot.identity != item.identity
                        or second_snapshot.data != item.data
                    ):
                        second.close()
                        raise AnalysisError(
                            f"published guardian B does not name committed generation: {item.final_path}"
                        )
                except BaseException:
                    first.close()
                    raise
                guardian_pairs.append((item, first, second))
        except BaseException as exc:
            self._record_release_error(
                errors,
                "establish-release-guardians",
                str(directory.lexical_path),
                exc,
            )

        if not errors:
            for index, item in enumerate(self._published):
                self._close_opened(
                    item.stage_file,
                    f"close-primary-published-lease-{index}",
                    warnings,
                    errors,
                )

        if not errors:
            for index, (item, first, second) in enumerate(guardian_pairs):
                for label, guardian in (("A", first), ("B", second)):
                    try:
                        snapshot = guardian.read_exact_snapshot(
                            f"published guardian {label} {index} before release"
                        )
                        if snapshot.identity != item.identity or snapshot.data != item.data:
                            raise AnalysisError("guardian generation changed")
                    except BaseException as exc:
                        self._record_release_error(
                            errors,
                            f"verify-release-guardian-{label}",
                            str(item.final_path),
                            exc,
                        )
                try:
                    reopen_artifact_exact(
                        directory,
                        item.final_path.name,
                        item.data,
                        f"published basename before release {index}",
                        item.identity,
                    )
                except BaseException as exc:
                    self._record_release_error(
                        errors,
                        "verify-published-basename-before-release",
                        str(item.final_path),
                        exc,
                    )

        # Release guardian A while B remains mandatory, then release B.  A
        # post-success close exception is followed by an exact basename reopen;
        # if an external generation won that final gap, report it as a release
        # failure rather than returning committed/clean.
        if not errors:
            for index, (_item, first, _second) in enumerate(guardian_pairs):
                self._close_opened(
                    first,
                    f"close-release-guardian-A-{index}",
                    warnings,
                    errors,
                )
        if not errors:
            for index, (item, _first, second) in enumerate(guardian_pairs):
                self._close_opened(
                    second,
                    f"close-release-guardian-B-{index}",
                    warnings,
                    errors,
                )
                try:
                    reopen_artifact_exact(
                        directory,
                        item.final_path.name,
                        item.data,
                        f"published basename after authoritative release {index}",
                        item.identity,
                    )
                except BaseException as exc:
                    self._record_release_error(
                        errors,
                        "verify-published-basename-after-release",
                        str(item.final_path),
                        exc,
                    )

        # Any guardian created before a later failure still needs an exhaustive
        # close attempt.  Keep the error machine-readable if that also fails.
        for index, (_item, first, second) in enumerate(guardian_pairs):
            for label, guardian in (("A", first), ("B", second)):
                if guardian.closed:
                    continue
                self._close_opened(
                    guardian,
                    f"close-release-guardian-{label}-{index}-after-error",
                    warnings,
                    errors,
                )

        # Guardian creation/verification can itself fail before the normal
        # primary-release phase.  The owner is already transitioning to a
        # machine-readable failure, but it must still exhaustively release
        # every original published handle instead of leaking a mandatory lease.
        for index, item in enumerate(self._published):
            if item.stage_file.closed:
                continue
            self._close_opened(
                item.stage_file,
                f"close-primary-published-lease-{index}-after-error",
                warnings,
                errors,
            )

        directory_closed = False
        for attempt in range(1, 3):
            try:
                directory.close()
                directory_closed = True
                break
            except BaseException as exc:
                warnings.append(
                    {
                        "action": "close-stable-output-directory-after-handoff",
                        "attempt": attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                        "name": str(directory.lexical_path),
                    }
                )
                if directory.handle is None:
                    directory_closed = True
                    break
        if not directory_closed:
            errors.append(
                {
                    "artifact": str(directory.lexical_path),
                    "operation": "close-stable-output-directory-after-handoff",
                    "error": "stable output directory could not be closed after two attempts",
                }
            )

        self["cleanup_warnings"] = warnings
        if warnings and self.get("cleanup_state") == "clean":
            self["cleanup_state"] = "recovered_warnings"
        self["release_errors"] = errors
        self["release_required_paths"] = (
            sorted(str(item.final_path) for item in self._published) if errors else []
        )
        # A release can be semantically unsuccessful (for example, a drift was
        # observed in the final unleased gap) while all resources were still
        # exhaustively closed.  Mark the owner closed in that case so __del__
        # does not retry a completed teardown and obscure the first evidence.
        self._closed = bool(
            directory_closed
            and all(item.stage_file.closed for item in self._published)
            and all(
                first.closed and second.closed
                for _item, first, second in guardian_pairs
            )
        )
        if errors:
            self["handoff_state"] = "release_incomplete"
            self["published_lease_state"] = "release_incomplete"
            self["transaction_state"] = "rollback_incomplete"
            self["published_generation"] = "external_or_unknown_after_release"
            self["recovery_required"] = True
            self["cleanup_state"] = "warnings"
            raise ArtifactTransactionError(
                f"published generation lease release failed: {errors}",
                dict(self),
            )
        self["handoff_state"] = "released"
        self["published_lease_state"] = "released"

    def __enter__(self) -> "ArtifactCommitResult":
        if self._closed:
            raise AnalysisError("artifact commit result owner is already closed")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False

    def __del__(self) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self.close()
        except BaseException:
            pass


def capture_original_artifact(
    directory: StableOutputDirectory, path: Path
) -> ContentSnapshot | None:
    try:
        opened = directory.open_file(path.name, create=False, writable=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if getattr(exc, "winerror", None) in {2, 3} or getattr(exc, "errno", None) == 2:
            return None
        raise
    try:
        return opened.read_exact_snapshot("existing output artifact")
    finally:
        opened.close()


def capture_original_artifact_lease(
    directory: StableOutputDirectory, path: Path
) -> tuple[ContentSnapshot | None, StableOpenFile | None]:
    """Capture an existing destination and keep its Windows write-denying lease."""

    try:
        opened = directory.open_file(
            path.name,
            create=False,
            writable=False,
            protect_generation=os.name == "nt",
        )
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        if getattr(exc, "winerror", None) in {2, 3} or getattr(exc, "errno", None) == 2:
            return None, None
        raise
    try:
        snapshot = opened.read_exact_snapshot("existing output artifact lease")
    except Exception:
        opened.close()
        raise
    return snapshot, opened


def retire_original_artifact(
    directory: StableOutputDirectory,
    original: OriginalArtifact,
) -> None:
    """Retire the exact planned Windows target, then require no-replace publish.

    The destination handle was opened with FILE_SHARE_READ only.  In-place
    writers (including writers through a hard-link alias), delete and pathname
    replacement are therefore rejected by the kernel.  The transaction still
    renames the exact opened file object through its own DELETE-capable handle,
    always with ReplaceIfExists=0.
    """

    if original.snapshot is None:
        return
    if os.name != "nt":
        raise AnalysisError(
            "planned-existing output replacement requires a mandatory target-generation "
            "primitive; this POSIX runtime must use a fresh output directory"
        )
    target = original.target_file
    reservation = original.backup_reservation
    if target is None or reservation is None:
        raise AnalysisError(f"existing output lease/backup reservation missing: {original.final_path}")
    backup_name = reservation.name
    reservation_name = backup_name.replace("backup-", "reservation-", 1)
    if reservation_name == backup_name:
        raise AnalysisError(f"invalid backup reservation name: {backup_name}")
    # Vacate the reserved backup basename without unlinking it.  Both renames
    # are no-replace; an attacker racing either private name can only make the
    # transaction fail while every file object remains recoverable.
    directory.replace(reservation, reservation_name, replace_existing=False)
    try:
        directory.replace(target, backup_name, replace_existing=False)
    except BaseException:
        if target.name.casefold() == backup_name.casefold():
            target.temporary = True
            original.backup_file = target
            original.retired = True
        raise
    target.temporary = True
    original.backup_file = target
    original.retired = True


def retire_committed_artifact_for_rollback(
    directory: StableOutputDirectory,
    item: StagedArtifact,
) -> None:
    """Move the exact transaction-owned final back to its private name."""

    if os.name != "nt":
        raise AnalysisError(
            "rollback of a planned-existing output requires a mandatory "
            "target-generation primitive on this POSIX runtime"
        )
    try:
        directory.replace(
            item.stage_file,
            item.private_name,
            replace_existing=False,
        )
    except BaseException:
        if item.stage_file.name.casefold() == item.private_name.casefold():
            item.stage_file.temporary = True
        raise
    item.stage_file.temporary = True


def ensure_open_files_do_not_alias_inputs(
    files: Sequence[StableOpenFile], input_snapshots: Sequence[InputSnapshot]
) -> None:
    identities: set[tuple[int, int]] = set()
    for opened in files:
        identity = opened.identity()[:2]
        if identity in identities:
            raise AnalysisError(f"output artifacts alias each other: {opened.path}")
        identities.add(identity)
        for snapshot in input_snapshots:
            if identity == snapshot.identity[:2]:
                raise AnalysisError(
                    f"output artifact aliases input: {opened.path} and {snapshot.path}"
                )


def verify_original_artifacts_unchanged(
    directory: StableOutputDirectory, originals: Sequence[OriginalArtifact]
) -> None:
    for original in originals:
        if original.snapshot is None:
            if directory.entry_exists(original.final_path.name):
                raise AnalysisError(
                    f"output artifact appeared before commit: {original.final_path}"
                )
            continue
        current = capture_original_artifact(directory, original.final_path)
        if (
            current is None
            or current.identity != original.snapshot.identity
            or current.data != original.snapshot.data
        ):
            raise AnalysisError(
                f"existing output artifact changed before commit: {original.final_path}"
            )


def verify_artifact_generation(
    directory: StableOutputDirectory,
    originals: Sequence[OriginalArtifact],
    staged: Sequence[StagedArtifact],
    committed: Sequence[StagedArtifact],
) -> None:
    """Bind the whole planned destination generation immediately before each commit."""

    staged_by_path = {item.final_path: item for item in staged}
    committed_paths = {item.final_path for item in committed}
    for original in originals:
        if original.final_path in committed_paths:
            expected = staged_by_path[original.final_path]
            current = capture_original_artifact(directory, original.final_path)
            if (
                current is None
                or current.identity != expected.identity
                or current.data != expected.data
            ):
                raise AnalysisError(
                    f"committed output artifact changed during artifact-set commit: "
                    f"{original.final_path}"
                )
            continue
        if original.retired:
            if directory.entry_exists(original.final_path.name):
                raise AnalysisError(
                    f"output artifact appeared after exact target retirement: "
                    f"{original.final_path}"
                )
            if original.backup_file is None or original.snapshot is None:
                raise AnalysisError(
                    f"retired output artifact lost its rollback generation: "
                    f"{original.final_path}"
                )
            backup = original.backup_file.read_exact_snapshot(
                "retired original output artifact"
            )
            if (
                backup.identity != original.snapshot.identity
                or backup.data != original.snapshot.data
            ):
                raise AnalysisError(
                    f"retired output artifact changed before no-replace publish: "
                    f"{original.final_path}"
                )
            continue
        if original.snapshot is None:
            if directory.entry_exists(original.final_path.name):
                raise AnalysisError(
                    f"output artifact appeared during artifact-set commit: "
                    f"{original.final_path}"
                )
            continue
        current = capture_original_artifact(directory, original.final_path)
        if (
            current is None
            or current.identity != original.snapshot.identity
            or current.data != original.snapshot.data
        ):
            raise AnalysisError(
                f"existing output artifact changed during artifact-set commit: "
                f"{original.final_path}"
            )


def add_recovery_candidate(
    candidates: list[RecoveryCandidate],
    opened: StableOpenFile,
    data: bytes,
    identity: tuple[int, int, int, int],
    label: str,
) -> None:
    """Preserve one exact generation for the final machine recovery manifest."""

    opened.temporary = False
    candidate = RecoveryCandidate(opened, data, identity, label)
    if candidate not in candidates:
        candidates.append(candidate)


def verify_recovery_candidates(
    directory: StableOutputDirectory,
    candidates: Sequence[RecoveryCandidate],
    errors: list[dict[str, str]],
) -> list[str]:
    """Emit only paths that currently exist and still name the expected object."""

    recovery_files: list[str] = []
    for candidate in candidates:
        opened = candidate.opened
        try:
            handle_snapshot = opened.read_exact_snapshot(
                f"exact recovery handle for {candidate.label}"
            )
            if (
                handle_snapshot.identity != candidate.identity
                or handle_snapshot.data != candidate.data
            ):
                raise AnalysisError("exact recovery handle no longer contains the expected generation")
            if (
                os.name == "nt"
                and opened.rename_candidate_names
                and opened.rename_expected_identity is not None
            ):
                directory.resolve_windows_open_file_location(
                    opened,
                    opened.rename_candidate_names,
                    opened.rename_expected_identity,
                )
            current = capture_original_artifact(directory, opened.path)
            if (
                current is None
                or current.identity != candidate.identity
                or current.data != candidate.data
            ):
                raise AnalysisError(
                    "reported recovery basename no longer resolves to the expected generation"
                )
            actual_path = opened.current_path()
            if not actual_path.exists():
                raise AnalysisError(
                    f"reported recovery path does not exist: {actual_path}"
                )
            reported_path = actual_path
            try:
                if opened.path.exists() and os.path.samefile(opened.path, actual_path):
                    reported_path = opened.path
            except OSError:
                pass
            recovery_files.append(str(reported_path))
        except BaseException as exc:
            errors.append(
                {
                    "artifact": str(opened.path),
                    "operation": "verify-recovery-file",
                    "error": f"{candidate.label}: {type(exc).__name__}: {exc}",
                }
            )
    return sorted(set(recovery_files))


def rollback_artifact_set(
    directory: StableOutputDirectory,
    originals: Sequence[OriginalArtifact],
    committed: Sequence[StagedArtifact],
) -> dict[str, Any]:
    original_by_path = {item.final_path: item for item in originals}
    errors: list[dict[str, str]] = []
    recovery_candidates: list[RecoveryCandidate] = []
    restored_paths: set[Path] = set()
    committed_paths = {item.final_path for item in committed}
    for item in reversed(committed):
        original = original_by_path[item.final_path]
        try:
            current = capture_original_artifact(directory, item.final_path)
        except BaseException as exc:
            current = None
            errors.append(
                {
                    "artifact": str(item.final_path),
                    "operation": "verify-current-generation",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if (
            current is None
            or current.identity != item.identity
            or current.data != item.data
        ):
            errors.append(
                {
                    "artifact": str(item.final_path),
                    "operation": "preserve-external-drift",
                    "error": "current destination is no longer the committed transaction artifact",
                }
            )
            if original.backup_file is not None and original.snapshot is not None:
                add_recovery_candidate(
                    recovery_candidates,
                    original.backup_file,
                    original.snapshot.data,
                    original.snapshot.identity,
                    "old generation after committed destination drift",
                )
            continue
        if original.snapshot is None:
            try:
                item.stage_file.delete()
                item.stage_file.close()
                item.stage_file.temporary = False
                restored_paths.add(item.final_path)
            except BaseException as exc:
                errors.append(
                    {
                        "artifact": str(item.final_path),
                        "operation": "delete-new-artifact",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        else:
            if original.backup_file is None:
                errors.append(
                    {
                        "artifact": str(item.final_path),
                        "operation": "restore-old-artifact",
                        "error": "rollback backup is missing",
                    }
                )
                continue
            try:
                # Retire the exact transaction-owned file object, then restore
                # with no-replace.  A late external final is never overwritten.
                retire_committed_artifact_for_rollback(directory, item)
                restore_original_artifact(directory, original)
                restored_paths.add(item.final_path)
            except BaseException as exc:
                add_recovery_candidate(
                    recovery_candidates,
                    original.backup_file,
                    original.snapshot.data,
                    original.snapshot.identity,
                    "old generation after restore failure",
                )
                try:
                    if item.stage_file.name == item.private_name:
                        directory.replace(
                            item.stage_file,
                            item.final_path.name,
                            replace_existing=False,
                        )
                        item.stage_file.temporary = False
                except BaseException as preserve_exc:
                    add_recovery_candidate(
                        recovery_candidates,
                        item.stage_file,
                        item.data,
                        item.identity,
                        "transaction generation after restore failure",
                    )
                    errors.append(
                        {
                            "artifact": str(item.final_path),
                            "operation": "preserve-current-after-restore-failure",
                            "error": f"{type(preserve_exc).__name__}: {preserve_exc}",
                        }
                    )
                errors.append(
                    {
                        "artifact": str(item.final_path),
                        "operation": "restore-old-artifact",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    for original in reversed(originals):
        if not original.retired or original.final_path in committed_paths:
            continue
        backup = original.backup_file
        if backup is None:
            errors.append(
                {
                    "artifact": str(original.final_path),
                    "operation": "restore-retired-uncommitted-artifact",
                    "error": "rollback backup is missing",
                }
            )
            continue
        try:
            if directory.entry_exists(original.final_path.name):
                raise AnalysisError(
                    "an independent destination appeared after exact target retirement"
                )
            restore_original_artifact(directory, original)
            restored_paths.add(original.final_path)
        except BaseException as exc:
            assert original.snapshot is not None
            add_recovery_candidate(
                recovery_candidates,
                backup,
                original.snapshot.data,
                original.snapshot.identity,
                "old uncommitted generation after restore failure",
            )
            errors.append(
                {
                    "artifact": str(original.final_path),
                    "operation": "restore-retired-uncommitted-artifact",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    for original in originals:
        if original.final_path not in restored_paths:
            continue
        try:
            if original.snapshot is None:
                if directory.entry_exists(original.final_path.name):
                    raise AnalysisError(
                        f"rollback left a new artifact: {original.final_path}"
                    )
            else:
                reopen_artifact_exact(
                    directory,
                    original.final_path.name,
                    original.snapshot.data,
                    "rolled back output artifact",
                )
        except BaseException as exc:
            if original.backup_file is not None and original.backup_file.name != original.final_path.name:
                assert original.snapshot is not None
                add_recovery_candidate(
                    recovery_candidates,
                    original.backup_file,
                    original.snapshot.data,
                    original.snapshot.identity,
                    "old generation after rollback verification failure",
                )
            errors.append(
                {
                    "artifact": str(original.final_path),
                    "operation": "verify-rollback",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    # A rollback is never complete merely because no branch recorded an
    # exception.  For every planned-existing artifact, prove that the old
    # generation is back at final or preserve its exact open object as a
    # verified recovery file.  This catches any stale high-level flag after a
    # successful kernel rename followed by a wrapper/asynchronous exception.
    for original in originals:
        if original.snapshot is None:
            continue
        try:
            current = capture_original_artifact(directory, original.final_path)
        except BaseException as exc:
            current = None
            errors.append(
                {
                    "artifact": str(original.final_path),
                    "operation": "audit-rolled-back-final",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if (
            current is not None
            and current.identity == original.snapshot.identity
            and current.data == original.snapshot.data
        ):
            continue
        preserved = False
        for opened in (original.backup_file, original.target_file):
            if opened is None:
                continue
            try:
                candidate_snapshot = opened.read_exact_snapshot(
                    "exact old generation recovery handle"
                )
                if (
                    os.name == "nt"
                    and opened.rename_candidate_names
                    and opened.rename_expected_identity is not None
                ):
                    directory.resolve_windows_open_file_location(
                        opened,
                        opened.rename_candidate_names,
                        opened.rename_expected_identity,
                    )
            except BaseException:
                candidate_snapshot = None
            if (
                candidate_snapshot is not None
                and candidate_snapshot.identity == original.snapshot.identity
                and candidate_snapshot.data == original.snapshot.data
            ):
                add_recovery_candidate(
                    recovery_candidates,
                    opened,
                    original.snapshot.data,
                    original.snapshot.identity,
                    "old generation not restored to final",
                )
                preserved = True
                break
        errors.append(
            {
                "artifact": str(original.final_path),
                "operation": "audit-rolled-back-final",
                "error": (
                    "old generation exists only at a recovery path"
                    if preserved
                    else "old generation is not reachable at final or a verified recovery path"
                ),
            }
        )
    recovery_files = verify_recovery_candidates(
        directory,
        recovery_candidates,
        errors,
    )
    return {
        "rollback_state": "incomplete" if errors else "complete",
        "rollback_errors": errors,
        "recovery_required": bool(errors),
        "recovery_files": sorted(set(recovery_files)),
    }


def commit_artifact_set(
    artifact_bytes: Sequence[tuple[Path, bytes]],
    input_snapshots: Sequence[InputSnapshot],
) -> ArtifactCommitResult:
    """Stage, verify, commit, and roll back a two-artifact evidence set."""

    if len(artifact_bytes) != 2:
        raise AnalysisError("MonoBehaviour output transaction requires exactly two artifacts")
    final_paths = [path for path, _data in artifact_bytes]
    if len({os.path.normcase(os.fspath(path)) for path in final_paths}) != len(final_paths):
        raise AnalysisError("output artifact paths must be unique")
    parent = final_paths[0].parent
    if any(path.parent != parent for path in final_paths):
        raise AnalysisError("output artifacts must share one directory")

    verify_all_inputs_unchanged(input_snapshots)
    reject_reparse_components(parent, "output directory")
    directory = StableOutputDirectory.open_or_create(parent)
    directory.verify_lexical_identity()
    verify_all_inputs_unchanged(input_snapshots)

    staged: list[StagedArtifact] = []
    originals: list[OriginalArtifact] = []
    committed: list[StagedArtifact] = []
    temporary_files: list[StableOpenFile] = []
    opened: list[tuple[StableOpenFile, bytes, Path, str]] = []
    transaction: dict[str, Any] = {
        "transaction_state": "not_committed",
        "published_generation": "original_or_absent",
        "rollback_state": "not_required",
        "rollback_errors": [],
        "recovery_required": False,
        "recovery_files": [],
        "rename_coordination_evidence": [],
    }
    caught: BaseException | None = None
    try:
        for final_path in final_paths:
            snapshot, target_file = capture_original_artifact_lease(
                directory, final_path
            )
            if target_file is not None:
                temporary_files.append(target_file)
            originals.append(
                OriginalArtifact(
                    final_path=final_path,
                    snapshot=snapshot,
                    backup_file=None,
                    target_file=target_file,
                )
            )
        if os.name != "nt" and any(item.snapshot is not None for item in originals):
            raise AnalysisError(
                "planned-existing output replacement is disabled on POSIX without "
                "a mandatory target-generation primitive; use a fresh output directory"
            )
        ensure_open_files_do_not_alias_inputs(
            [
                item.target_file
                for item in originals
                if item.target_file is not None
            ],
            input_snapshots,
        )
        for index, original in enumerate(originals):
            if original.snapshot is None:
                continue
            reservation = open_exclusive_temporary(
                directory, original.final_path, f"backup-{index}"
            )
            reservation_identity = reservation.identity()
            reservation.temporary = False
            try:
                directory.protect_open_file_generation(
                    reservation,
                    b"",
                    reservation_identity,
                    f"backup reservation {index} protected generation",
                )
            finally:
                reservation.temporary = True
            original.backup_reservation = reservation
            temporary_files.append(reservation)

        for index, (final_path, data) in enumerate(artifact_bytes):
            stage_file = open_exclusive_temporary(
                directory, final_path, f"stage-{index}"
            )
            temporary_files.append(stage_file)
            opened.append((stage_file, data, final_path, f"artifact {index}"))
        ensure_no_output_aliases(
            [*final_paths, *(item.path for item, _data, _final, _label in opened)],
            [snapshot.path for snapshot in input_snapshots],
        )
        ensure_open_files_do_not_alias_inputs(
            [item for item, _data, _final, _label in opened], input_snapshots
        )
        for stage_file, data, final_path, label in opened:
            identity = write_open_temporary_exact(stage_file, data, label)
            private_name = stage_file.name
            directory.protect_open_file_generation(
                stage_file,
                data,
                identity,
                f"staged {label} protected generation",
            )
            staged.append(
                StagedArtifact(
                    final_path,
                    stage_file,
                    data,
                    identity,
                    private_name,
                )
            )
        opened.clear()

        actual_files = [
            *(item.stage_file for item in staged),
            *(item.target_file for item in originals if item.target_file is not None),
            *(
                item.backup_reservation
                for item in originals
                if item.backup_reservation is not None
            ),
        ]
        ensure_open_files_do_not_alias_inputs(actual_files, input_snapshots)
        for original in originals:
            if original.snapshot is not None:
                for input_snapshot in input_snapshots:
                    if original.snapshot.identity[:2] == input_snapshot.identity[:2]:
                        raise AnalysisError(
                            f"output artifact aliases input: {original.final_path} and "
                            f"{input_snapshot.path}"
                        )
        ensure_no_output_aliases(
            [
                *final_paths,
                *(item.stage_file.path for item in staged),
                *(
                    item.backup_reservation.path
                    for item in originals
                    if item.backup_reservation is not None
                ),
            ],
            [snapshot.path for snapshot in input_snapshots],
        )
        for index, item in enumerate(staged):
            current_stage = item.stage_file.read_exact_snapshot(
                f"staged artifact {index} at commit point"
            )
            if current_stage.identity != item.identity or current_stage.data != item.data:
                raise AnalysisError(
                    f"staged artifact changed before commit: {item.stage_file.path}"
                )
        verify_all_inputs_unchanged(input_snapshots)
        verify_original_artifacts_unchanged(directory, originals)
        directory.verify_lexical_identity()

        directory.commit_replace_existing = {
            original.final_path.name: False for original in originals
        }
        directory.commit_guard = lambda: verify_artifact_generation(
            directory,
            originals,
            staged,
            committed,
        )
        try:
            for index, item in enumerate(staged):
                original = originals[index]
                if original.snapshot is not None:
                    if directory.commit_guard is not None:
                        directory.commit_guard()
                    directory.verify_lexical_identity()
                    if directory.commit_guard is not None:
                        directory.commit_guard()
                    retire_original_artifact(directory, original)
                try:
                    replace_artifact(directory, item.stage_file, item.final_path.name)
                except BaseException:
                    # A fault injected immediately after the underlying rename
                    # may escape from a wrapper before the normal append.  The
                    # stable handle name is authoritative for rollback scope.
                    if (
                        item.stage_file.name == item.final_path.name
                        and item not in committed
                    ):
                        committed.append(item)
                        item.stage_file.temporary = False
                    raise
                committed.append(item)
                # A committed handle now names a final artifact.  Generic
                # temporary cleanup must never delete a published generation.
                item.stage_file.temporary = False
                reopen_artifact_exact(
                    directory,
                    item.final_path.name,
                    item.data,
                    f"committed artifact {index}",
                    item.identity,
                )
                directory.verify_lexical_identity()
            verify_all_inputs_unchanged(input_snapshots)
            directory.verify_lexical_identity()
            verify_artifact_generation(directory, originals, staged, committed)
            for index, item in enumerate(staged):
                reopen_artifact_exact(
                    directory,
                    item.final_path.name,
                    item.data,
                    f"post-commit artifact {index}",
                    item.identity,
                )
            transaction.update(
                {
                    "transaction_state": "committed",
                    "published_generation": "new",
                }
            )
        except BaseException as exc:
            directory.commit_guard = None
            rollback = rollback_artifact_set(directory, originals, committed)
            transaction.update(rollback)
            if rollback["rollback_state"] == "complete":
                transaction.update(
                    {
                        "transaction_state": "rolled_back",
                        "published_generation": "original_or_absent",
                    }
                )
                caught = exc
            else:
                transaction.update(
                    {
                        "transaction_state": "rollback_incomplete",
                        "published_generation": "mixed_recovery_required",
                    }
                )
                caught = ArtifactTransactionError(
                    f"artifact-set rollback failed after {type(exc).__name__}: {exc}; "
                    f"rollback errors: {rollback['rollback_errors']}",
                    dict(transaction),
                )
    except BaseException as exc:
        caught = exc

    directory.commit_guard = None
    committed_success = (
        caught is None
        and transaction.get("transaction_state") == "committed"
        and len(committed) == len(staged)
    )
    cleanup_targets = (
        [
            opened
            for opened in temporary_files
            if opened not in {item.stage_file for item in committed}
        ]
        if committed_success
        else temporary_files
    )
    try:
        cleanup = cleanup_created_files(cleanup_targets)
        if cleanup is None:
            cleanup = {
                "cleanup_state": "clean",
                "cleanup_warnings": [],
                "cleanup_required_paths": [],
            }
    except Exception as exc:
        cleanup = {
            "cleanup_state": "warnings",
            "cleanup_warnings": [
                {
                    "action": "cleanup-orchestrator",
                    "attempt": 1,
                    "error": f"{type(exc).__name__}: {exc}",
                    "name": str(directory.lexical_path),
                }
            ],
            "cleanup_required_paths": ["cleanup-state-unknown"],
        }
    transaction.update(cleanup)

    if committed_success:
        try:
            # This is the return-side generation gate.  The exact published
            # handles remain open and mandatory after this check; ownership is
            # transferred to ArtifactCommitResult instead of being hidden in a
            # plain dictionary.
            directory.verify_lexical_identity()
            for index, item in enumerate(committed):
                exact = item.stage_file.read_exact_snapshot(
                    f"published artifact {index} at lease handoff"
                )
                if exact.identity != item.identity or exact.data != item.data:
                    raise AnalysisError(
                        f"published artifact changed before lease handoff: {item.final_path}"
                    )
                reopen_artifact_exact(
                    directory,
                    item.final_path.name,
                    item.data,
                    f"published basename at lease handoff {index}",
                    item.identity,
                )
        except BaseException as exc:
            transaction.update(
                {
                    "transaction_state": "rollback_incomplete",
                    "published_generation": "external_or_unknown_before_handoff",
                    "recovery_required": True,
                    "handoff_state": "not_transferred",
                    "published_lease_state": "held_after_handoff_failure",
                }
            )
            caught = ArtifactTransactionError(
                f"published generation failed the lease handoff gate: "
                f"{type(exc).__name__}: {exc}",
                dict(transaction),
            )
            committed_success = False
            try:
                handoff_cleanup = cleanup_created_files(
                    [item.stage_file for item in committed]
                )
                transaction["cleanup_warnings"] = [
                    *transaction.get("cleanup_warnings", []),
                    *handoff_cleanup.get("cleanup_warnings", []),
                ]
                transaction["cleanup_required_paths"] = sorted(
                    set(
                        transaction.get("cleanup_required_paths", [])
                        + handoff_cleanup.get("cleanup_required_paths", [])
                    )
                )
            except BaseException as cleanup_exc:
                transaction.setdefault("cleanup_warnings", []).append(
                    {
                        "action": "close-published-leases-after-handoff-failure",
                        "attempt": 1,
                        "error": f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                        "name": str(directory.lexical_path),
                    }
                )

    transaction["rename_coordination_evidence"] = (
        collect_rename_coordination_failover_evidence(temporary_files)
    )

    if committed_success:
        return ArtifactCommitResult(transaction, directory, committed)

    directory_closed = False
    for attempt in range(1, 3):
        try:
            directory.close()
            directory_closed = True
            break
        except Exception as exc:
            transaction["cleanup_warnings"].append(
                {
                    "action": "close-stable-output-directory",
                    "attempt": attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                    "name": str(directory.lexical_path),
                }
            )
    if not directory_closed:
        transaction["cleanup_required_paths"] = sorted(
            set(
                transaction["cleanup_required_paths"]
                + ["stable-output-directory-handle"]
            )
        )
        transaction["cleanup_state"] = "warnings"
    elif transaction["cleanup_warnings"] and transaction["cleanup_state"] == "clean":
        transaction["cleanup_state"] = "recovered_warnings"

    if caught is not None:
        if isinstance(caught, ArtifactTransactionError):
            caught.transaction = dict(transaction)
        else:
            try:
                setattr(caught, "transaction", dict(transaction))
            except Exception:
                pass
        raise caught
    raise ArtifactTransactionError(
        "artifact-set transaction did not produce a lease-owned success result",
        dict(transaction),
    )


class AnalysisRunResult(dict[str, Any]):
    """CLI/API result whose artifact transaction still owns final leases."""

    def __init__(self, payload: dict[str, Any], owner: ArtifactCommitResult) -> None:
        super().__init__(payload)
        self._artifact_owner = owner
        self["artifact_transaction"] = owner

    @property
    def closed(self) -> bool:
        return self._artifact_owner.closed

    def snapshot_for_consumption(self) -> dict[str, Any]:
        if self.closed:
            raise AnalysisError("analysis result was consumed after artifact lease release")
        snapshot = dict(self)
        snapshot["artifact_transaction"] = self._artifact_owner.snapshot_for_consumption()
        return snapshot

    def close(self) -> None:
        self._artifact_owner.close()

    def __enter__(self) -> "AnalysisRunResult":
        if self.closed:
            raise AnalysisError("analysis result owner is already closed")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False


def ensure_output_boundary(index_path: Path, output_dir: Path) -> None:
    index_root = index_path.parent.resolve()
    output_resolved = output_dir.resolve(strict=False)
    if output_resolved == index_root or index_root in output_resolved.parents:
        raise AnalysisError("output-dir must not be inside the UnityPy raw export root")


def verify_all_inputs_unchanged(snapshots: Sequence[InputSnapshot]) -> None:
    for snapshot in snapshots:
        verify_snapshot(snapshot, snapshot.display_path)


def validate_summary_contract(summary: dict[str, Any], contract: dict[str, Any]) -> None:
    missing = sorted(set(contract["required_top_level_keys"]) - set(summary))
    if missing:
        raise AnalysisError(f"generated summary missing contract keys: {missing}")
    schema = summary.get("schema")
    if not isinstance(schema, dict):
        raise AnalysisError("generated summary schema must be an object")
    if schema.get("name") != contract["schema_name"] or schema.get("version") != contract["schema_version"]:
        raise AnalysisError("generated summary schema identity does not match contract")


def run(args: argparse.Namespace) -> AnalysisRunResult:
    index_path = args.index.resolve(strict=True)
    config_path = args.config.resolve(strict=True)
    contract_path = args.contract.resolve(strict=True)
    probe_path = (
        args.probe_evidence.resolve(strict=True)
        if args.probe_evidence is not None
        else None
    )
    if not index_path.is_file():
        raise AnalysisError(f"index is not a file: {index_path}")
    for label, path in (("config", config_path), ("contract", contract_path)):
        if not path.is_file():
            raise AnalysisError(f"{label} is not a file: {path}")
    if probe_path is not None and not probe_path.is_file():
        raise AnalysisError(f"probe evidence is not a file: {probe_path}")

    config_payload, config_snapshot = read_json_snapshot(config_path, "config")
    config = validate_config(config_payload)
    contract_payload, contract_snapshot = read_json_snapshot(contract_path, "contract")
    contract = validate_contract(contract_payload)

    output_dir = absolute_lexical(args.output_dir)
    reject_reparse_components(output_dir, "output directory")
    if os.path.lexists(output_dir) and not output_dir.is_dir():
        raise AnalysisError(f"output-dir is not a directory: {output_dir}")
    ensure_output_boundary(index_path, output_dir)
    summary_path = output_dir / config["outputs"]["summary_json"]
    report_path = output_dir / config["outputs"]["report_markdown"]
    output_targets = [summary_path, report_path]
    initial_input_paths = [index_path, config_path, contract_path]
    if probe_path is not None:
        initial_input_paths.append(probe_path)
    ensure_no_output_aliases(output_targets, initial_input_paths)

    index_root = index_path.parent.resolve()
    metadata_root = (
        args.metadata_root.resolve(strict=False)
        if args.metadata_root is not None
        else (index_root / "metadata" / "MonoBehaviour").resolve(strict=False)
    )
    try:
        metadata_root.relative_to(index_root)
    except ValueError as exc:
        raise AnalysisError("metadata-root must be inside the UnityPy raw export root") from exc

    index_snapshot = capture_hash_snapshot(index_path, label="index")
    streamed = stream_index(index_path, config)
    validated_index_header = streamed.pop("_validated_index_header")
    verify_snapshot(index_snapshot, "index after first pass")
    (
        metadata_summary,
        metadata_provenance,
        metadata_snapshots,
        metadata_pass_counts,
    ) = analyze_metadata(
        index_path,
        index_root,
        metadata_root,
        config,
        output_targets,
        validated_index_header,
    )
    verify_snapshot(index_snapshot, "index after metadata pass")
    probe_summary, probe_provenance, probe_snapshot = analyze_probe(probe_path, config)

    summary: dict[str, Any] = {
        **streamed,
        "input_provenance": {
            "config": config_snapshot.evidence(),
            "contract": contract_snapshot.evidence(),
            "index": index_snapshot.evidence(),
            "metadata": metadata_provenance,
            "probe_evidence": probe_provenance,
        },
        "interpretation": {
            "fact_scope": (
                "Counts are facts derived from the supplied index and referenced metadata files. "
                "Probe counts, when present, are facts only about the supplied sample."
            ),
            "forbidden_inference": config["forbidden_inference"],
            "hypotheses": [
                "A read_error may be caused by a missing type tree, script/resource version drift, or parser layout differences; this index alone cannot select one cause.",
                "Any additional recovery layer requires separate exact-consumption, dependency identity, licensing, and output-completeness validation.",
            ],
            "judgments": [
                "A read_error is a failed semantic read under the current strategy, not evidence that the source object is truncated.",
                "generic_header_only is identity/header evidence and not custom-field recovery.",
            ],
            "safe_fallback": config["safe_fallback"],
        },
        "metadata": metadata_summary,
        "probe_evidence": probe_summary,
        "schema": {
            "contract_version": contract["contract_version"],
            "name": contract["schema_name"],
            "version": contract["schema_version"],
        },
    }
    summary["validation"] = build_validation(
        summary,
        config["statuses"]["exported"],
        metadata_pass_counts,
    )
    validate_summary_contract(summary, contract)

    json_text = json.dumps(
        summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    report_text = build_report(summary)
    all_snapshots = [config_snapshot, contract_snapshot, *metadata_snapshots]
    if probe_snapshot is not None:
        all_snapshots.append(probe_snapshot)
    all_snapshots.append(index_snapshot)
    verify_all_inputs_unchanged(all_snapshots)
    ensure_no_output_aliases(
        output_targets,
        [snapshot.path for snapshot in all_snapshots],
    )
    summary_bytes = json_text.encode("utf-8")
    report_bytes = report_text.encode("utf-8")
    parsed_staged_summary = parse_strict_json_bytes(
        summary_bytes, summary_path, "generated summary"
    )
    if parsed_staged_summary != summary:
        raise AnalysisError("generated JSON failed exact reopen verification")
    artifact_transaction = commit_artifact_set(
        [(summary_path, summary_bytes), (report_path, report_bytes)],
        all_snapshots,
    )
    run_status = (
        "ok"
        if artifact_transaction["cleanup_state"] == "clean"
        else "ok_with_cleanup_warnings"
    )
    return AnalysisRunResult(
        {
            "all_checks_passed": summary["validation"]["all_checks_passed"],
            "mono_behaviour_count": summary["index"]["mono_behaviour_count"],
            "report": str(report_path),
            "status": run_status,
            "summary": str(summary_path),
        },
        artifact_transaction,
    )


def main(argv: Sequence[str] | None = None) -> int:
    result: AnalysisRunResult | None = None
    try:
        result = run(parse_args(argv))
        consumed = result.snapshot_for_consumption()
        # Perform a real serialization pass while the two mandatory published
        # leases are still held.  A non-serializable caller-visible success can
        # therefore never cross the handoff boundary as a clean result.
        json.dumps(consumed, ensure_ascii=False, sort_keys=True, allow_nan=False)
        result.close()
        # File bytes were consumed only while the mandatory leases were held.
        # Merge release metadata after close without re-reading the artifacts.
        consumed_transaction = consumed["artifact_transaction"]
        current_transaction = result["artifact_transaction"]
        for key in (
            "cleanup_required_paths",
            "cleanup_state",
            "cleanup_warnings",
            "handoff_state",
            "published_lease_state",
            "release_errors",
            "release_required_paths",
        ):
            consumed_transaction[key] = current_transaction.get(key)
        serialized = json.dumps(
            consumed, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    except (AnalysisError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        release_exc: BaseException | None = None
        if result is not None and not result.closed:
            try:
                result.close()
            except BaseException as close_exc:
                release_exc = close_exc
        reported_exc = release_exc or exc
        error: dict[str, Any] = {
            "error": str(reported_exc),
            "error_type": type(reported_exc).__name__,
            "status": "error",
        }
        transaction = getattr(reported_exc, "transaction", None)
        if not isinstance(transaction, dict) and result is not None:
            transaction = dict(result["artifact_transaction"])
        if isinstance(transaction, dict):
            error["artifact_transaction"] = transaction
        print(
            "ERROR: " + json.dumps(error, ensure_ascii=False, sort_keys=True, allow_nan=False),
            file=sys.stderr,
        )
        return 2
    print(serialized)
    return 0 if consumed["all_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

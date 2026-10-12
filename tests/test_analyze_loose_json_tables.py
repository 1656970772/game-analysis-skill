from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "analyze_loose_json_tables.py"
DEFAULT_CONFIG = SKILL_ROOT / "references" / "loose-json-analysis-config.json"


def write_json(path: Path, value: object, *, encoding: str = "utf-8") -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode(encoding)
    path.write_bytes(raw)
    return raw


def write_config(tmp_path: Path, mutate=None) -> Path:
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(config)
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return path


def run_tool(
    source: Path,
    output: Path,
    *,
    config: Path = DEFAULT_CONFIG,
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-B",
        str(SCRIPT),
        "--source",
        str(source),
        "--output",
        str(output),
        "--config",
        str(config),
    ]
    if extra:
        command.extend(extra)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=environment,
    )


def read_summary(output: Path, name: str = "summary.json") -> dict:
    return json.loads((output / name).read_text(encoding="utf-8"))


def test_decoding_order_scan_root_and_configured_output_names(tmp_path: Path) -> None:
    source = tmp_path / "source"
    tables = source / "tables"
    ignored = source / "ignored"
    plain = json.dumps({"name": "普通 UTF-8"}, ensure_ascii=False).encode("utf-8")
    bom = codecs.BOM_UTF8 + json.dumps({"name": "带 BOM"}, ensure_ascii=False).encode("utf-8")
    gb18030 = json.dumps({"name": "国标编码"}, ensure_ascii=False).encode("gb18030")
    tables.mkdir(parents=True)
    (tables / "d_core.py.plain.json").write_bytes(plain)
    (tables / "d_core.py.bom.json").write_bytes(bom)
    (tables / "d_core.py.gb.json").write_bytes(gb18030)
    write_json(ignored / "d_ignore.py.not_scanned.json", {"ignored": True})

    def mutate(config: dict) -> None:
        config["scan"]["relative_roots"] = ["tables"]
        config["outputs"] = {
            "inventory_csv": "tables.csv",
            "summary_json": "tables-summary.json",
            "analysis_markdown": "tables-report.md",
        }

    config = write_config(tmp_path, mutate)
    output = tmp_path / "output"
    result = run_tool(source, output, config=config)
    assert result.returncode == 0, result.stderr
    summary = read_summary(output, "tables-summary.json")
    assert summary["scan"]["file_count"] == 3
    assert summary["scan_relative_roots"] == ["tables"]
    assert summary["parsing"]["encoding_distribution"] == {
        "gb18030": 1,
        "utf-8": 1,
        "utf-8-bom": 1,
    }
    assert (output / "tables.csv").is_file()
    assert (output / "tables-report.md").is_file()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_nonstandard_constants(tmp_path: Path, constant: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "d_bad.py.constant.json").write_text(
        '{"numeric": ' + constant + "}", encoding="utf-8"
    )
    output = tmp_path / "output"
    result = run_tool(source, output)
    assert result.returncode == 0, result.stderr
    summary = read_summary(output)
    assert summary["parsing"]["success_count"] == 0
    assert summary["parsing"]["failure_count"] == 1
    failure = summary["parsing"]["failures"][0]
    assert failure["parse_status"] == "json_error"
    assert "NonStandardJSONConstant" in failure["error_detail"]


def test_invalid_json_is_indexed_without_leaking_document_text(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = "DO_NOT_COPY_THIS_INVALID_VALUE_9F84"
    (source / "broken.json").write_text(
        '{"secret": "' + secret + '", trailing}', encoding="utf-8"
    )
    output = tmp_path / "output"
    result = run_tool(source, output)
    assert result.returncode == 0, result.stderr
    summary = read_summary(output)
    assert summary["parsing"]["failure_count"] == 1
    assert "line=" in summary["parsing"]["failures"][0]["error_detail"]
    for artifact in output.iterdir():
        if artifact.is_file():
            assert secret not in artifact.read_text(encoding="utf-8")


def test_unmatched_name_preserves_complete_stem(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_json(source / "maps" / "world.region.v2.json", {"nodes": []})
    output = tmp_path / "output"
    result = run_tool(source, output)
    assert result.returncode == 0, result.stderr
    summary = read_summary(output)
    assert summary["naming"]["matched_count"] == 0
    assert summary["naming"]["unmatched_files"] == ["maps/world.region.v2.json"]
    largest = summary["largest_tables_by_bytes"][0]
    assert largest["filename_module"] == ""
    assert largest["filename_table"] == "world.region.v2"


def test_nested_key_candidates_and_sensitive_values_never_leak(tmp_path: Path) -> None:
    source = tmp_path / "source"
    secret_address = "TOP_SECRET_VALUE_31D8A7"
    write_json(
        source / "d_profile.py.players.json",
        {
            "records": [
                {
                    "profile": {"contactAddress": secret_address},
                    "combatSkill": {"damage": 5},
                }
            ]
        },
    )
    output = tmp_path / "output"
    result = run_tool(source, output)
    assert result.returncode == 0, result.stderr
    summary = read_summary(output)
    sensitive = {item["field_name"]: item for item in summary["sensitive_field_name_signals"]}
    assert sensitive["contactAddress"]["occurrences"] == 1
    assert summary["system_candidates"]["战斗/技能"]["key_signal_file_count"] == 1
    for artifact_name in ("json-data-inventory.csv", "summary.json", "analysis.md"):
        assert secret_address not in (output / artifact_name).read_text(encoding="utf-8")


def test_expected_baseline_optional_matching_and_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    raw = write_json(source / "d_core.py.one.json", {"one": 1})

    optional_output = tmp_path / "optional"
    optional = run_tool(source, optional_output)
    assert optional.returncode == 0, optional.stderr
    assert read_summary(optional_output)["scan"]["expected_file_count"] is None

    matching_output = tmp_path / "matching"
    matching = run_tool(
        source,
        matching_output,
        extra=["--expected-file-count", "1", "--expected-total-bytes", str(len(raw))],
    )
    assert matching.returncode == 0, matching.stderr
    assert read_summary(matching_output)["validation"]["all_checks_passed"] is True

    mismatch_output = tmp_path / "mismatch"
    mismatch = run_tool(
        source,
        mismatch_output,
        extra=["--expected-file-count", "2", "--expected-total-bytes", str(len(raw) + 1)],
    )
    assert mismatch.returncode == 2
    mismatch_summary = read_summary(mismatch_output)
    assert mismatch_summary["scan"]["matches_expected_file_count"] is False
    assert mismatch_summary["scan"]["matches_expected_total_bytes"] is False
    assert mismatch_summary["validation"]["all_checks_passed"] is False


def test_output_inside_source_is_rejected_before_creation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_json(source / "d_core.py.one.json", {"one": 1})
    output = source / "analysis-output"
    result = run_tool(source, output)
    assert result.returncode == 2
    assert "inside it" in result.stderr
    assert not output.exists()


def test_source_and_output_links_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_json(source / "d_core.py.one.json", {"one": 1})
    linked_source = tmp_path / "linked-source"
    try:
        linked_source.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")
    source_result = run_tool(linked_source, tmp_path / "output-one")
    assert source_result.returncode == 2
    assert "symlink or Windows reparse point" in source_result.stderr

    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)
    output_result = run_tool(source, linked_output)
    assert output_result.returncode == 2
    assert "symlink or Windows reparse point" in output_result.stderr


def test_idempotent_rerun_is_byte_stable_and_leaves_no_temp_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_json(source / "d_core.py.one.json", {"one": {"nestedKey": 1}})
    write_json(source / "d_core.py.two.json", [{"two": 2}])
    source_before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*.json")
    }
    output = tmp_path / "output"
    first = run_tool(source, output)
    assert first.returncode == 0, first.stderr
    first_artifacts = {
        path.name: path.read_bytes() for path in output.iterdir() if path.is_file()
    }
    second = run_tool(source, output)
    assert second.returncode == 0, second.stderr
    second_artifacts = {
        path.name: path.read_bytes() for path in output.iterdir() if path.is_file()
    }
    assert first_artifacts == second_artifacts
    assert not [path for path in output.iterdir() if path.name.endswith(".tmp")]
    assert not list(tmp_path.rglob("__pycache__"))
    source_after = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*.json")
    }
    assert source_before == source_after


def test_cli_scan_root_overrides_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_json(source / "first" / "d_one.py.table.json", {"first": 1})
    write_json(source / "second" / "d_two.py.table.json", {"second": 2})
    output = tmp_path / "output"
    result = run_tool(source, output, extra=["--scan-root", "second"])
    assert result.returncode == 0, result.stderr
    summary = read_summary(output)
    assert summary["scan_relative_roots"] == ["second"]
    assert summary["scan"]["file_count"] == 1
    assert summary["naming"]["module_distribution"] == {"two": 1}

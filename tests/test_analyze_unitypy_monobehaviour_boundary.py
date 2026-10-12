from __future__ import annotations

import csv
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tracemalloc
from collections import Counter
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "analyze_unitypy_monobehaviour_boundary.py"
DEFAULT_CONFIG = SKILL_ROOT / "references" / "unity-monobehaviour-boundary-config.json"
DEFAULT_CONTRACT = SKILL_ROOT / "references" / "unity-monobehaviour-boundary-contract.json"
FIELDS = [
    "asset_id",
    "source_file",
    "path_id",
    "asset_type",
    "asset_name",
    "container_path",
    "export_kind",
    "export_path",
    "width",
    "height",
    "status",
    "remarks",
]


def strict_load(path: Path) -> dict:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def write_index(root: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> Path:
    path = root / "all_resources_index.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def metadata(root: Path, relative: str, tree: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type_tree": tree}, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def mb_row(
    asset_id: str,
    source: str,
    status: str,
    *,
    export_path: str = "",
    remarks: str = "",
) -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "source_file": source,
        "path_id": asset_id.rsplit("_", 1)[-1],
        "asset_type": "MonoBehaviour",
        "asset_name": "",
        "container_path": "",
        "export_kind": "metadata" if export_path else "",
        "export_path": export_path,
        "width": "",
        "height": "",
        "status": status,
        "remarks": remarks,
    }


def run_tool(
    index: Path,
    output: Path,
    *,
    config: Path = DEFAULT_CONFIG,
    contract: Path = DEFAULT_CONTRACT,
    probe: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-B",
        str(SCRIPT),
        "--index",
        str(index),
        "--output-dir",
        str(output),
        "--config",
        str(config),
        "--contract",
        str(contract),
    ]
    if probe is not None:
        command.extend(["--probe-evidence", str(probe)])
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


def load_module():
    spec = importlib.util.spec_from_file_location("monobehaviour_boundary_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def module_args(
    module,
    index: Path,
    output: Path,
    config: Path,
    contract: Path,
    probe: Path | None = None,
):
    values = [
        "--index",
        str(index),
        "--output-dir",
        str(output),
        "--config",
        str(config),
        "--contract",
        str(contract),
    ]
    if probe is not None:
        values.extend(["--probe-evidence", str(probe)])
    return module.parse_args(values)


def transaction_case(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    index, root = synthetic_case(tmp_path)
    config = tmp_path / "config.json"
    contract = tmp_path / "contract.json"
    probe = tmp_path / "probe.json"
    config.write_bytes(DEFAULT_CONFIG.read_bytes())
    contract.write_bytes(DEFAULT_CONTRACT.read_bytes())
    probe.write_text(
        json.dumps({"sampling": {"samples": []}}, allow_nan=False),
        encoding="utf-8",
    )
    metadata_input = root / "metadata/MonoBehaviour/generic.json"
    return index, config, contract, probe, metadata_input


def direct_transaction_case(module, tmp_path: Path):
    input_path = tmp_path / "input.bin"
    input_path.write_bytes(b"stable-input")
    input_snapshot = module.capture_hash_snapshot(input_path)
    output = tmp_path / "output"
    output.mkdir()
    finals = [output / "summary.json", output / "report.md"]
    old = [b"old-summary", b"old-report"]
    new = [b"new-summary", b"new-report"]
    for path, data in zip(finals, old, strict=True):
        path.write_bytes(data)
    return input_snapshot, output, finals, old, new


def create_directory_junction(link: Path, target: Path) -> None:
    command = (
        "$ErrorActionPreference='Stop'; "
        "New-Item -ItemType Junction -Path $env:GA_TEST_JUNCTION_LINK "
        "-Target $env:GA_TEST_JUNCTION_TARGET | Out-Null"
    )
    environment = os.environ.copy()
    environment["GA_TEST_JUNCTION_LINK"] = str(link)
    environment["GA_TEST_JUNCTION_TARGET"] = str(target)
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {result.stderr}")


def swap_directory_for_junction(output: Path, parked: Path, outside: Path) -> None:
    os.rename(output, parked)
    try:
        create_directory_junction(output, outside)
    except BaseException:
        if os.path.lexists(output):
            os.rmdir(output)
        os.rename(parked, output)
        raise


def restore_swapped_directory(output: Path, parked: Path) -> None:
    if os.path.lexists(output):
        os.rmdir(output)
    if parked.exists():
        os.rename(parked, output)


def synthetic_case(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "raw"
    generic_path = "metadata/MonoBehaviour/generic.json"
    custom_path = "metadata/MonoBehaviour/custom.json"
    rows = [
        mb_row(
            "a_1",
            "Game_Data/level7",
            "read_error",
            remarks="ValueError: Expected to read 52 bytes, but only read 32 bytes",
        ),
        mb_row("a_2", "Game_Data/level7", "exported", export_path=generic_path),
        mb_row("a_3", "Game_Data/StreamingAssets/module", "exported", export_path=custom_path),
        mb_row("a_4", "Game_Data/sharedassets12.assets", "future_status"),
        {
            **mb_row("a_5", "Game_Data/resources.assets", "exported"),
            "asset_type": "Texture2D",
        },
    ]
    index = write_index(root, rows)
    metadata(root, generic_path, {"m_GameObject": {}, "m_Enabled": 1, "m_Script": {}, "m_Name": ""})
    metadata(
        root,
        custom_path,
        {"m_GameObject": {}, "m_Enabled": 1, "m_Script": {}, "m_Name": "", "customValue": 7},
    )
    return index, root


def test_streaming_counts_unknown_status_metadata_and_unavailable_evidence(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 0, result.stderr
    summary = strict_load(output / "monobehaviour-boundary-summary.json")
    assert summary["index"]["total_row_count"] == 5
    assert summary["index"]["mono_behaviour_count"] == 4
    assert summary["index"]["unknown_status_counts"] == {"future_status": 1}
    assert summary["metadata"]["classification_counts"] == {
        "custom_fields_present": 1,
        "generic_header_only": 1,
    }
    assert summary["read_errors"]["expected_vs_actual_counts"] == {"52/32": 1}
    assert summary["read_errors"]["actual_bytes_total"] == 32
    assert summary["read_errors"]["expected_minus_actual_total"] == 20
    assert summary["evidence_availability"]["byte_size"]["availability"] == "unavailable"
    assert summary["evidence_availability"]["raw_sha256"]["availability"] == "unavailable"
    assert summary["validation"]["all_checks_passed"] is True
    assert "future_status" in (output / "monobehaviour-boundary-analysis.md").read_text(encoding="utf-8")


def test_source_classification_is_configurable(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["source_groups"] = {
        "default_id": "remainder",
        "rules": [{"id": "numbered_scene", "pattern": "(?:^|/)level[0-9]+$"}],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    output = tmp_path / "output"
    result = run_tool(index, output, config=config_path)
    assert result.returncode == 0, result.stderr
    groups = strict_load(output / "monobehaviour-boundary-summary.json")["source_groups"]["groups"]
    assert groups["numbered_scene"]["mono_behaviour_count"] == 2
    assert groups["remainder"]["mono_behaviour_count"] == 2


@pytest.mark.parametrize(
    ("document", "reason_fragment"),
    [
        ('{"type_tree": NaN}', "NonStandardJSONConstant"),
        ("[]", "metadata document"),
    ],
)
def test_metadata_parse_error_is_counted_and_included_once_in_provenance(
    tmp_path: Path, document: str, reason_fragment: str
) -> None:
    root = tmp_path / "raw"
    relative = "metadata/MonoBehaviour/broken.json"
    index = write_index(
        root,
        [
            mb_row("a_1", "Game_Data/level1", "exported", export_path=relative),
            mb_row("a_2", "Game_Data/level1", "exported", export_path=relative),
        ],
    )
    broken = root / relative
    broken.parent.mkdir(parents=True)
    broken.write_text(document, encoding="utf-8")
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 0, result.stderr
    summary = strict_load(output / "monobehaviour-boundary-summary.json")
    assert summary["metadata"]["classification_counts"] == {"parse_error": 2}
    assert summary["metadata"]["custom_key_counts"] == {}
    assert any(
        reason_fragment in key for key in summary["metadata"]["failure_reason_counts"]
    )
    provenance = summary["input_provenance"]["metadata"]
    assert summary["metadata"]["referenced_existing_file_count"] == 1
    assert provenance["file_count"] == 1
    assert provenance["files"] == [
        {
            "bytes": broken.stat().st_size,
            "path": relative,
            "sha256": hashlib.sha256(broken.read_bytes()).hexdigest(),
        }
    ]
    manifest_line = (
        f"{relative}\t{broken.stat().st_size}\t{hashlib.sha256(broken.read_bytes()).hexdigest()}\n"
    )
    assert provenance["manifest_sha256"] == hashlib.sha256(
        manifest_line.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("scenario", "fields", "values", "expected"),
    [
        (
            "missing",
            FIELDS,
            [{}, {}],
            {
                "byte_size": (None, "unavailable", 0, 0),
                "raw_sha256": (None, "unavailable", 0, 0),
            },
        ),
        (
            "all_empty",
            [*FIELDS, "byte_size", "raw_sha256"],
            [
                {"byte_size": "", "raw_sha256": ""},
                {"byte_size": "", "raw_sha256": ""},
            ],
            {
                "byte_size": ("byte_size", "unavailable", 0, 0),
                "raw_sha256": ("raw_sha256", "unavailable", 0, 0),
            },
        ),
        (
            "all_invalid",
            [*FIELDS, "byte_size", "raw_sha256"],
            [
                {"byte_size": "invalid", "raw_sha256": "not-a-sha"},
                {"byte_size": "-1", "raw_sha256": "f" * 63},
            ],
            {
                "byte_size": ("byte_size", "invalid", 0, 2),
                "raw_sha256": ("raw_sha256", "invalid", 0, 2),
            },
        ),
        (
            "partial",
            [*FIELDS, "byte_size", "raw_sha256"],
            [
                {"byte_size": "42", "raw_sha256": "a" * 64},
                {"byte_size": "invalid", "raw_sha256": "not-a-sha"},
            ],
            {
                "byte_size": ("byte_size", "partial", 1, 1),
                "raw_sha256": ("raw_sha256", "partial", 1, 1),
            },
        ),
    ],
)
def test_declared_evidence_availability_distinguishes_missing_empty_invalid_and_partial(
    tmp_path: Path,
    scenario: str,
    fields: list[str],
    values: list[dict[str, str]],
    expected: dict[str, tuple[str | None, str, int, int]],
) -> None:
    root = tmp_path / f"raw-{scenario}"
    rows = []
    for index, evidence_values in enumerate(values, start=1):
        rows.append(
            {
                **mb_row(
                    f"a_{index}",
                    "Game_Data/level1",
                    "read_error",
                    remarks="ValueError: Expected to read 52 bytes, but only read 32 bytes",
                ),
                **evidence_values,
            }
        )
    index_path = write_index(root, rows, fields)
    output = tmp_path / f"output-{scenario}"
    result = run_tool(index_path, output)
    assert result.returncode == 0, result.stderr
    summary = strict_load(output / "monobehaviour-boundary-summary.json")
    for name, (column, state, available_count, invalid_count) in expected.items():
        evidence = summary["evidence_availability"][name]
        assert evidence["column"] == column
        assert evidence["availability"] == state
        assert evidence["available_count"] == available_count
        assert evidence["invalid_count"] == invalid_count
    assert summary["validation"]["checks"][
        "invalid_declared_evidence_is_not_unavailable"
    ] is True
    assert summary["validation"]["all_checks_passed"] is True


def test_malformed_index_fails_closed_before_output(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    index = write_index(root, [{"asset_type": "MonoBehaviour", "status": "read_error"}], ["asset_type", "status"])
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 2
    assert "missing required columns" in result.stderr
    assert not output.exists()


def test_strict_json_config_rejects_nan(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text('{"config_schema_version": NaN}', encoding="utf-8")
    result = run_tool(index, tmp_path / "output", config=config_path)
    assert result.returncode == 2
    assert "non-standard JSON constant" in result.stderr


def test_probe_is_sample_only_and_has_no_population_estimate(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    probe = tmp_path / "probe.json"
    probe.write_text(
        json.dumps(
            {
                "sampling": {
                    "samples": [
                        {
                            "byte_consumption_exact": True,
                            "sample_kind": "stratified",
                            "source_file": "Game_Data/level7",
                            "status": "generated_exact",
                        },
                        {
                            "byte_consumption_exact": False,
                            "sample_kind": "targeted",
                            "source_file": "Game_Data/resources.assets",
                            "status": "generated_failed",
                        },
                    ]
                }
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    result = run_tool(index, output, probe=probe)
    assert result.returncode == 0, result.stderr
    summary = strict_load(output / "monobehaviour-boundary-summary.json")
    evidence = summary["probe_evidence"]
    assert evidence["sample_count"] == 2
    assert evidence["exact_rate_within_sample"] == 0.5
    assert evidence["overall_recovery_estimate"] is None
    assert "must not be extrapolated" in evidence["inference_boundary"]
    assert summary["input_provenance"]["probe_evidence"]["sha256"] == hashlib.sha256(
        probe.read_bytes()
    ).hexdigest()


def test_deterministic_idempotent_outputs_and_provenance_hashes(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    output = tmp_path / "output"
    first = run_tool(index, output)
    assert first.returncode == 0, first.stderr
    json_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    first_hashes = (
        hashlib.sha256(json_path.read_bytes()).hexdigest(),
        hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    second = run_tool(index, output)
    assert second.returncode == 0, second.stderr
    assert first_hashes == (
        hashlib.sha256(json_path.read_bytes()).hexdigest(),
        hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    summary = strict_load(json_path)
    assert summary["input_provenance"]["index"]["sha256"] == hashlib.sha256(index.read_bytes()).hexdigest()
    assert summary["input_provenance"]["config"]["sha256"] == hashlib.sha256(DEFAULT_CONFIG.read_bytes()).hexdigest()
    assert summary["input_provenance"]["contract"]["sha256"] == hashlib.sha256(DEFAULT_CONTRACT.read_bytes()).hexdigest()


def test_output_target_cannot_alias_config_before_any_write(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    colliding = output / "monobehaviour-boundary-summary.json"
    colliding.write_bytes(DEFAULT_CONFIG.read_bytes())
    before = colliding.read_bytes()
    result = run_tool(index, output, config=colliding)
    assert result.returncode == 2
    assert "aliases input" in result.stderr
    assert colliding.read_bytes() == before
    assert not (output / "monobehaviour-boundary-analysis.md").exists()


def test_output_target_cannot_alias_contract_or_probe_before_any_write(tmp_path: Path) -> None:
    index, _root = synthetic_case(tmp_path)

    contract_output = tmp_path / "contract-output"
    contract_output.mkdir()
    colliding_contract = contract_output / "monobehaviour-boundary-analysis.md"
    colliding_contract.write_bytes(DEFAULT_CONTRACT.read_bytes())
    contract_before = colliding_contract.read_bytes()
    contract_result = run_tool(
        index,
        contract_output,
        contract=colliding_contract,
    )
    assert contract_result.returncode == 2
    assert "aliases input" in contract_result.stderr
    assert colliding_contract.read_bytes() == contract_before
    assert not (contract_output / "monobehaviour-boundary-summary.json").exists()

    probe_output = tmp_path / "probe-output"
    probe_output.mkdir()
    colliding_probe = probe_output / "monobehaviour-boundary-summary.json"
    colliding_probe.write_text(
        json.dumps({"sampling": {"samples": []}}, allow_nan=False),
        encoding="utf-8",
    )
    probe_before = colliding_probe.read_bytes()
    probe_result = run_tool(index, probe_output, probe=colliding_probe)
    assert probe_result.returncode == 2
    assert "aliases input" in probe_result.stderr
    assert colliding_probe.read_bytes() == probe_before
    assert not (probe_output / "monobehaviour-boundary-analysis.md").exists()


def test_output_target_cannot_hardlink_alias_index_or_metadata(tmp_path: Path) -> None:
    index, root = synthetic_case(tmp_path)
    index_output = tmp_path / "index-output"
    index_output.mkdir()
    index_alias = index_output / "monobehaviour-boundary-summary.json"
    os.link(index, index_alias)
    index_before = index.read_bytes()
    index_result = run_tool(index, index_output)
    assert index_result.returncode == 2
    assert "aliases input" in index_result.stderr
    assert index.read_bytes() == index_before
    assert index_alias.read_bytes() == index_before

    metadata_output = tmp_path / "metadata-output"
    metadata_output.mkdir()
    metadata_input = root / "metadata/MonoBehaviour/generic.json"
    metadata_alias = metadata_output / "monobehaviour-boundary-analysis.md"
    os.link(metadata_input, metadata_alias)
    metadata_before = metadata_input.read_bytes()
    metadata_result = run_tool(index, metadata_output)
    assert metadata_result.returncode == 2
    assert "aliases input" in metadata_result.stderr
    assert metadata_input.read_bytes() == metadata_before
    assert metadata_alias.read_bytes() == metadata_before
    assert not (metadata_output / "monobehaviour-boundary-summary.json").exists()


def test_index_drift_fails_closed_and_preserves_existing_output(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "raw"
    index = write_index(
        root,
        [
            mb_row(
                "a_1",
                "Game_Data/level1",
                "read_error",
                remarks="ValueError: Expected to read 52 bytes, but only read 32 bytes",
            )
        ],
    )
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    summary_path.write_bytes(b"existing-summary")
    original_stream = module.stream_index

    def mutate_after_first_pass(path, config):
        streamed = original_stream(path, config)
        with index.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writerow(
                mb_row(
                    "a_2",
                    "Game_Data/level1",
                    "read_error",
                    remarks="ValueError: Expected to read 52 bytes, but only read 32 bytes",
                )
            )
        return streamed

    module.stream_index = mutate_after_first_pass
    args = module.parse_args(
        [
            "--index",
            str(index),
            "--output-dir",
            str(output),
            "--config",
            str(DEFAULT_CONFIG),
            "--contract",
            str(DEFAULT_CONTRACT),
        ]
    )
    with pytest.raises(module.AnalysisError, match="input changed after analysis"):
        module.run(args)
    assert summary_path.read_bytes() == b"existing-summary"
    assert not (output / "monobehaviour-boundary-analysis.md").exists()


def test_metadata_parsing_and_hash_share_snapshot_and_drift_fails_closed(
    tmp_path: Path,
) -> None:
    module = load_module()
    root = tmp_path / "raw"
    relative = "metadata/MonoBehaviour/example.json"
    index = write_index(
        root,
        [mb_row("a_1", "Game_Data/level1", "exported", export_path=relative)],
    )
    metadata_path = root / relative
    metadata_path.parent.mkdir(parents=True)
    original_payload = json.dumps(
        {"type_tree": {"m_GameObject": {}, "m_Enabled": 1}},
        sort_keys=True,
    )
    changed_payload = json.dumps(
        {"type_tree": {"m_GameObject": {}, "m_Enabled": 1, "custom": 7}},
        sort_keys=True,
    )
    metadata_path.write_text(original_payload, encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    summary_path.write_bytes(b"existing-summary")
    original_read = module.read_bytes_snapshot

    def mutate_after_snapshot(path, *, label="input", relative_to=None):
        data, snapshot = original_read(path, label=label, relative_to=relative_to)
        if label == "MonoBehaviour metadata":
            metadata_path.write_text(changed_payload, encoding="utf-8")
        return data, snapshot

    module.read_bytes_snapshot = mutate_after_snapshot
    args = module.parse_args(
        [
            "--index",
            str(index),
            "--output-dir",
            str(output),
            "--config",
            str(DEFAULT_CONFIG),
            "--contract",
            str(DEFAULT_CONTRACT),
        ]
    )
    with pytest.raises(module.AnalysisError, match="input changed after analysis"):
        module.run(args)
    assert summary_path.read_bytes() == b"existing-summary"
    assert not (output / "monobehaviour-boundary-analysis.md").exists()


@pytest.mark.parametrize(
    ("byte_values", "raw_values", "expected_state", "selected", "invalid_aliases", "consistent"),
    [
        (("", "42"), ("", "a" * 64), "available", "secondary", 0, 0),
        (("42", "42"), ("A" * 64, "a" * 64), "available", "primary", 0, 1),
        (("invalid", "42"), ("not-a-sha", "a" * 64), "partial", "secondary", 1, 0),
    ],
)
def test_candidate_alias_selection_is_config_ordered_and_explainable(
    tmp_path: Path,
    byte_values: tuple[str, str],
    raw_values: tuple[str, str],
    expected_state: str,
    selected: str,
    invalid_aliases: int,
    consistent: int,
) -> None:
    root = tmp_path / "raw"
    fields = [*FIELDS, "byte_size", "object_byte_size", "raw_sha256", "object_raw_sha256"]
    index = write_index(
        root,
        [
            {
                **mb_row(
                    "a_1",
                    "Game_Data/level1",
                    "read_error",
                    remarks="ValueError: Expected to read 52 bytes, but only read 32 bytes",
                ),
                "byte_size": byte_values[0],
                "object_byte_size": byte_values[1],
                "raw_sha256": raw_values[0],
                "object_raw_sha256": raw_values[1],
            }
        ],
        fields,
    )
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 0, result.stderr
    evidence = strict_load(output / "monobehaviour-boundary-summary.json")[
        "evidence_availability"
    ]
    expected_byte_column = "byte_size" if selected == "primary" else "object_byte_size"
    expected_raw_column = "raw_sha256" if selected == "primary" else "object_raw_sha256"
    for name, expected_column in (
        ("byte_size", expected_byte_column),
        ("raw_sha256", expected_raw_column),
    ):
        item = evidence[name]
        assert item["availability"] == expected_state
        assert item["column"] is None
        assert item["present_columns"] == (
            ["byte_size", "object_byte_size"]
            if name == "byte_size"
            else ["raw_sha256", "object_raw_sha256"]
        )
        assert item["selected_column_counts"] == {expected_column: 1}
        assert item["invalid_alias_value_count"] == invalid_aliases
        assert item["consistent_alias_value_count"] == consistent
        assert item["selection_policy"] == "first_valid_by_candidate_order"


def test_conflicting_valid_candidate_aliases_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    fields = [*FIELDS, "byte_size", "object_byte_size", "raw_sha256", "object_raw_sha256"]
    index = write_index(
        root,
        [
            {
                **mb_row("a_1", "Game_Data/level1", "read_error"),
                "byte_size": "42",
                "object_byte_size": "43",
                "raw_sha256": "a" * 64,
                "object_raw_sha256": "a" * 64,
            }
        ],
        fields,
    )
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 2
    assert "conflicting valid byte_size aliases" in result.stderr
    assert not output.exists()


def test_first_index_pass_does_not_retain_exported_row_dicts(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "raw"
    count = 20_000
    rows = (
        mb_row(
            f"a_{item}",
            "Game_Data/level1",
            "exported",
            export_path=f"metadata/MonoBehaviour/{item}.json",
        )
        for item in range(count)
    )
    index = root / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    config = module.validate_config(module.load_strict_json(DEFAULT_CONFIG, "config"))
    assert "exported_rows" not in inspect.getsource(module.stream_index)
    tracemalloc.start()
    streamed = module.stream_index(index, config)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert isinstance(streamed, dict)
    assert streamed["index"]["mono_behaviour_count"] == count
    assert peak < 5_000_000


@pytest.mark.parametrize("artifact_index", [0, 1], ids=["summary-stage", "report-stage"])
@pytest.mark.parametrize(
    "input_kind",
    ["config", "contract", "probe", "index", "metadata"],
)
def test_exclusive_random_stage_collision_with_hardlinked_input_fails_without_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_index: int, input_kind: str
) -> None:
    module = load_module()
    index, config, contract, probe, metadata_input = transaction_case(tmp_path)
    inputs = {
        "config": config,
        "contract": contract,
        "probe": probe,
        "index": index,
        "metadata": metadata_input,
    }
    attacked_input = inputs[input_kind]
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    summary_path.write_bytes(b"old-summary")
    report_path.write_bytes(b"old-report")
    token = "11" * 16
    stage = output / f".ga-monobehaviour-stage-{artifact_index}-{token}.tmp"
    os.link(attacked_input, stage)
    input_before = attacked_input.read_bytes()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: token)

    with pytest.raises(module.AnalysisError, match="exclusive temporary path collision"):
        module.run(module_args(module, index, output, config, contract, probe))

    assert attacked_input.read_bytes() == input_before
    assert summary_path.read_bytes() == b"old-summary"
    assert report_path.read_bytes() == b"old-report"


@pytest.mark.parametrize("artifact_index", [0, 1], ids=["summary-stage", "report-stage"])
def test_exclusive_random_stage_collision_with_symlink_input_fails_without_damage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_index: int
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    summary_path.write_bytes(b"old-summary")
    report_path.write_bytes(b"old-report")
    token = "22" * 16
    stage = output / f".ga-monobehaviour-stage-{artifact_index}-{token}.tmp"
    try:
        stage.symlink_to(config)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    config_before = config.read_bytes()
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: token)

    with pytest.raises(module.AnalysisError, match="exclusive temporary path collision"):
        module.run(module_args(module, index, output, config, contract, probe))

    assert config.read_bytes() == config_before
    assert summary_path.read_bytes() == b"old-summary"
    assert report_path.read_bytes() == b"old-report"


def test_output_directory_symlink_or_reparse_is_rejected_before_write(tmp_path: Path) -> None:
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    summary = real_output / "monobehaviour-boundary-summary.json"
    report = real_output / "monobehaviour-boundary-analysis.md"
    summary.write_bytes(b"old-summary")
    report.write_bytes(b"old-report")
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError:
        create_directory_junction(linked_output, real_output)

    result = run_tool(index, linked_output, config=config, contract=contract, probe=probe)
    assert result.returncode == 2
    assert "symlink/reparse point" in result.stderr
    assert summary.read_bytes() == b"old-summary"
    assert report.read_bytes() == b"old-report"


@pytest.mark.parametrize("artifact_index", [0, 1], ids=["summary-stage", "report-stage"])
def test_exclusive_random_stage_collision_with_directory_reparse_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_index: int
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    target = tmp_path / "junction-target"
    target.mkdir()
    token = "33" * 16
    stage = output / f".ga-monobehaviour-stage-{artifact_index}-{token}.tmp"
    create_directory_junction(stage, target)
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: token)

    with pytest.raises(module.AnalysisError, match="exclusive temporary path collision"):
        module.run(module_args(module, index, output, config, contract, probe))


@pytest.mark.parametrize("input_kind", ["index", "metadata", "config", "contract", "probe"])
def test_persistent_input_drift_after_first_commit_rolls_back_old_artifact_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
) -> None:
    module = load_module()
    index, config, contract, probe, metadata_input = transaction_case(tmp_path)
    inputs = {
        "index": index,
        "metadata": metadata_input,
        "config": config,
        "contract": contract,
        "probe": probe,
    }
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    summary_path.write_bytes(b"old-summary")
    report_path.write_bytes(b"old-report")
    original_replace = module.replace_artifact
    mutated = False

    def mutate_after_summary_commit(directory, source, target_name: str) -> None:
        nonlocal mutated
        source_name = source.name
        original_replace(directory, source, target_name)
        if (
            target_name == summary_path.name
            and "stage-0" in source_name
            and not mutated
        ):
            with inputs[input_kind].open("ab") as handle:
                handle.write(b"\n")
            mutated = True

    monkeypatch.setattr(module, "replace_artifact", mutate_after_summary_commit)
    with pytest.raises(module.AnalysisError, match="input changed after analysis"):
        module.run(module_args(module, index, output, config, contract, probe))

    assert mutated is True
    assert summary_path.read_bytes() == b"old-summary"
    assert report_path.read_bytes() == b"old-report"
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.parametrize("old_outputs_exist", [False, True], ids=["absent", "existing"])
@pytest.mark.parametrize("failure_kind", ["second-commit", "second-reopen"])
def test_artifact_set_transaction_rolls_back_second_artifact_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_outputs_exist: bool,
    failure_kind: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    if old_outputs_exist:
        summary_path.write_bytes(b"old-summary")
        report_path.write_bytes(b"old-report")

    if failure_kind == "second-commit":
        original_replace = module.replace_artifact
        failed = False

        def fail_report_commit(directory, source, target_name: str) -> None:
            nonlocal failed
            if target_name == report_path.name and "stage-1" in source.name and not failed:
                failed = True
                raise OSError("controlled second artifact commit failure")
            original_replace(directory, source, target_name)

        monkeypatch.setattr(module, "replace_artifact", fail_report_commit)
        expected_error = OSError
    else:
        original_reopen = module.reopen_artifact_exact
        failed = False

        def fail_report_reopen(
            directory,
            name: str,
            expected: bytes,
            label: str,
            expected_identity=None,
        ) -> None:
            nonlocal failed
            if name == report_path.name and label == "committed artifact 1" and not failed:
                failed = True
                raise module.AnalysisError("controlled second artifact reopen failure")
            original_reopen(directory, name, expected, label, expected_identity)

        monkeypatch.setattr(module, "reopen_artifact_exact", fail_report_reopen)
        expected_error = module.AnalysisError

    with pytest.raises(expected_error):
        module.run(module_args(module, index, output, config, contract, probe))

    assert failed is True
    if old_outputs_exist:
        assert summary_path.read_bytes() == b"old-summary"
        assert report_path.read_bytes() == b"old-report"
    else:
        assert not summary_path.exists()
        assert not report_path.exists()
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows directory junction")
@pytest.mark.parametrize(
    "attack_point",
    [
        "stage-create",
        "backup-create",
        "commit-1",
        "commit-2",
        "rollback-restore",
        "finally-cleanup",
    ],
)
def test_stable_output_directory_handle_prevents_parent_junction_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_point: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    parked = tmp_path / "output-parked"
    outside = tmp_path / "outside"
    output.mkdir()
    outside.mkdir()
    summary_path = output / "monobehaviour-boundary-summary.json"
    report_path = output / "monobehaviour-boundary-analysis.md"
    summary_path.write_bytes(b"old-summary")
    report_path.write_bytes(b"old-report")

    token = "44" * 16
    protected_names = [
        summary_path.name,
        report_path.name,
        f".ga-monobehaviour-stage-0-{token}.tmp",
        f".ga-monobehaviour-stage-1-{token}.tmp",
        f".ga-monobehaviour-backup-0-{token}.tmp",
        f".ga-monobehaviour-backup-1-{token}.tmp",
    ]
    for position, name in enumerate(protected_names):
        (outside / name).write_bytes(f"outside-sentinel-{position}".encode("ascii"))
    outside_before = {
        path.name: path.read_bytes() for path in sorted(outside.iterdir())
    }
    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: token)
    swap_attempted = False
    swapped = False

    def swap_once() -> None:
        nonlocal swap_attempted, swapped
        if not swap_attempted:
            swap_attempted = True
            try:
                swap_directory_for_junction(output, parked, outside)
            except PermissionError:
                # Once stage/backup file handles are open, Windows normally
                # denies renaming their parent.  The attack still must not
                # interrupt the transaction or touch the external directory.
                return
            swapped = True

    original_open = module.open_exclusive_temporary
    if attack_point in {"stage-create", "backup-create"}:
        target_role = "stage-0" if attack_point == "stage-create" else "backup-0"

        def attack_open(directory, final_path: Path, role: str):
            if role == target_role:
                swap_once()
            return original_open(directory, final_path, role)

        monkeypatch.setattr(module, "open_exclusive_temporary", attack_open)

    original_replace = module.replace_artifact
    if attack_point in {"commit-1", "commit-2"}:
        target_index = 0 if attack_point == "commit-1" else 1
        commit_count = 0

        def attack_commit(directory, source, target_name: str) -> None:
            nonlocal commit_count
            if commit_count == target_index:
                swap_once()
            commit_count += 1
            original_replace(directory, source, target_name)

        monkeypatch.setattr(module, "replace_artifact", attack_commit)

    if attack_point == "rollback-restore":
        failed = False

        def force_second_commit_failure(directory, source, target_name: str) -> None:
            nonlocal failed
            if target_name == report_path.name and not failed:
                failed = True
                raise OSError("controlled rollback trigger")
            original_replace(directory, source, target_name)

        original_restore = module.restore_artifact

        def attack_restore(directory, source, target_name: str) -> None:
            swap_once()
            original_restore(directory, source, target_name)

        monkeypatch.setattr(module, "replace_artifact", force_second_commit_failure)
        monkeypatch.setattr(module, "restore_artifact", attack_restore)

    if attack_point == "finally-cleanup":
        original_cleanup = module.cleanup_created_files

        def force_precommit_failure(_directory, _originals) -> None:
            raise module.AnalysisError("controlled cleanup trigger")

        def attack_cleanup(files) -> None:
            swap_once()
            original_cleanup(files)

        monkeypatch.setattr(
            module,
            "verify_original_artifacts_unchanged",
            force_precommit_failure,
        )
        monkeypatch.setattr(module, "cleanup_created_files", attack_cleanup)

    try:
        raised = None
        try:
            successful = module.run(
                module_args(module, index, output, config, contract, probe)
            )
            successful.snapshot_for_consumption()
            successful.close()
        except (module.AnalysisError, OSError) as exc:
            raised = exc
        assert swap_attempted is True, str(raised)
        if swapped or attack_point in {"rollback-restore", "finally-cleanup"}:
            assert raised is not None
        outside_after = {
            path.name: path.read_bytes() for path in sorted(outside.iterdir())
        }
        assert outside_after == outside_before
    finally:
        if swapped:
            restore_swapped_directory(output, parked)

    if raised is None:
        assert json.loads(summary_path.read_text(encoding="utf-8"))["validation"][
            "all_checks_passed"
        ] is True
        assert report_path.read_bytes() != b"old-report"
    else:
        assert summary_path.read_bytes() == b"old-summary"
        assert report_path.read_bytes() == b"old-report"
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows directory junction")
def test_handle_relative_mutations_stay_pinned_after_actual_parent_junction_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise every mutation primitive after the lexical parent really changed."""

    module = load_module()
    output = tmp_path / "output"
    parked = tmp_path / "output-parked"
    outside = tmp_path / "outside"
    output.mkdir()
    outside.mkdir()
    summary_name = "monobehaviour-boundary-summary.json"
    report_name = "monobehaviour-boundary-analysis.md"
    (output / summary_name).write_bytes(b"old-summary")
    (output / report_name).write_bytes(b"old-report")

    token = "55" * 16
    protected_names = [
        summary_name,
        report_name,
        f".ga-monobehaviour-stage-0-{token}.tmp",
        f".ga-monobehaviour-stage-1-{token}.tmp",
        f".ga-monobehaviour-backup-0-{token}.tmp",
        f".ga-monobehaviour-backup-1-{token}.tmp",
        f".ga-monobehaviour-finally-cleanup-{token}.tmp",
    ]
    for position, name in enumerate(protected_names):
        (outside / name).write_bytes(f"outside-sentinel-{position}".encode("ascii"))
    outside_before = {
        path.name: path.read_bytes() for path in sorted(outside.iterdir())
    }

    def assert_outside_unchanged() -> None:
        assert {
            path.name: path.read_bytes() for path in sorted(outside.iterdir())
        } == outside_before

    monkeypatch.setattr(module.secrets, "token_hex", lambda _size: token)
    directory = module.StableOutputDirectory.open_or_create(output)
    created = []
    swapped = False
    try:
        # This is the raced state after a successful identity check but before a
        # mutation: the lexical name now resolves to an attacker-owned junction.
        swap_directory_for_junction(output, parked, outside)
        swapped = True
        assert_outside_unchanged()

        stage_0 = module.open_exclusive_temporary(
            directory, output / summary_name, "stage-0"
        )
        created.append(stage_0)
        module.write_open_temporary_exact(stage_0, b"new-summary", "artifact 0")
        assert_outside_unchanged()
        stage_1 = module.open_exclusive_temporary(
            directory, output / report_name, "stage-1"
        )
        created.append(stage_1)
        module.write_open_temporary_exact(stage_1, b"new-report", "artifact 1")
        assert_outside_unchanged()

        backup_0 = module.open_exclusive_temporary(
            directory, output / summary_name, "backup-0"
        )
        created.append(backup_0)
        module.write_open_temporary_exact(backup_0, b"old-summary", "backup 0")
        assert_outside_unchanged()
        backup_1 = module.open_exclusive_temporary(
            directory, output / report_name, "backup-1"
        )
        created.append(backup_1)
        module.write_open_temporary_exact(backup_1, b"old-report", "backup 1")
        assert_outside_unchanged()

        # Use the handle-relative primitive directly to model a junction swap in
        # the narrow interval after replace_artifact's lexical identity gate.
        directory.replace(stage_0, summary_name)
        assert stage_0.read_exact_snapshot("committed artifact 0").data == b"new-summary"
        assert_outside_unchanged()
        directory.replace(stage_1, report_name)
        assert stage_1.read_exact_snapshot("committed artifact 1").data == b"new-report"
        assert_outside_unchanged()

        # Mirror rollback: delete committed handles, then restore backups by
        # basename under the same pinned directory handle.
        stage_1.delete()
        stage_1.close()
        stage_1.temporary = False
        directory.replace(backup_1, report_name)
        backup_1.temporary = False
        assert backup_1.read_exact_snapshot("restored artifact 1").data == b"old-report"
        assert_outside_unchanged()
        stage_0.delete()
        stage_0.close()
        stage_0.temporary = False
        directory.replace(backup_0, summary_name)
        backup_0.temporary = False
        assert backup_0.read_exact_snapshot("restored artifact 0").data == b"old-summary"
        assert_outside_unchanged()

        cleanup_file = module.open_exclusive_temporary(
            directory, output / summary_name, "finally-cleanup"
        )
        created.append(cleanup_file)
        module.write_open_temporary_exact(cleanup_file, b"cleanup", "cleanup")
        assert_outside_unchanged()
        module.cleanup_created_files(created)
        assert not list(parked.glob(".ga-monobehaviour-*.tmp"))
        assert_outside_unchanged()
    finally:
        module.cleanup_created_files(created)
        directory.close()
        if swapped:
            restore_swapped_directory(output, parked)

    assert (output / summary_name).read_bytes() == b"old-summary"
    assert (output / report_name).read_bytes() == b"old-report"
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.parametrize(
    ("byte_candidates", "raw_candidates", "message"),
    [
        (["status"], ["raw_sha256"], "structural index columns"),
        (["byte_size"], ["remarks"], "structural index columns"),
        (["shared"], ["shared"], "must be disjoint"),
    ],
)
def test_evidence_candidates_cannot_conflict_with_structural_or_other_roles(
    byte_candidates: list[str], raw_candidates: list[str], message: str
) -> None:
    module = load_module()
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["evidence_columns"]["byte_size_candidates"] = byte_candidates
    config["evidence_columns"]["raw_sha256_candidates"] = raw_candidates
    with pytest.raises(module.AnalysisError, match=message):
        module.validate_config(config)


@pytest.mark.parametrize("value", ["+7", "1_0", "１２", "00", "01", "-0", " 7", "7 "])
def test_byte_size_rejects_noncanonical_decimal_spellings(value: str) -> None:
    module = load_module()
    state, selected, invalid_count, consistent = module.select_row_evidence(
        {"byte_size": value},
        ["byte_size"],
        evidence_name="byte_size",
        row_number=2,
    )
    assert (state, selected, invalid_count, consistent) == ("invalid", None, 1, 0)


@pytest.mark.parametrize("value", ["0", "7", "10", "999999999999999999999999"])
def test_byte_size_accepts_canonical_ascii_nonnegative_decimal(value: str) -> None:
    module = load_module()
    state, selected, invalid_count, consistent = module.select_row_evidence(
        {"byte_size": value},
        ["byte_size"],
        evidence_name="byte_size",
        row_number=2,
    )
    assert (state, selected, invalid_count, consistent) == ("valid", "byte_size", 0, 0)


def test_unique_unparsed_remarks_use_bounded_memory_and_preview(tmp_path: Path) -> None:
    module = load_module()
    root = tmp_path / "raw"
    count = 50_000
    index = root / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for item in range(count):
            value = mb_row(f"a_{item}", "Game_Data/level1", "read_error")
            value["remarks"] = f"unparsed-{item:08d}-" + ("x" * 496)
            writer.writerow(value)
    config = module.validate_config(module.load_strict_json(DEFAULT_CONFIG, "config"))

    tracemalloc.start()
    streamed = module.stream_index(index, config)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    errors = streamed["read_errors"]
    assert errors["unparsed_count"] == count
    assert errors["unparsed_preview_strategy"] == "lowest_sha256_bounded_sample"
    assert errors["unparsed_preview_limit"] == 100
    assert len(errors["unparsed_remarks"]) == 100
    assert peak < 6_000_000


@pytest.mark.parametrize(
    ("restore_index", "trigger"),
    [(0, "second-commit"), (1, "second-reopen")],
    ids=["summary-restore", "report-restore"],
)
def test_secondary_restore_failure_preserves_current_final_and_unique_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_index: int,
    trigger: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    finals = [
        output / "monobehaviour-boundary-summary.json",
        output / "monobehaviour-boundary-analysis.md",
    ]
    old = [b"old-summary", b"old-report"]
    for path, data in zip(finals, old, strict=True):
        path.write_bytes(data)

    original_replace = module.replace_artifact
    if trigger == "second-commit":
        def fail_primary(directory, source, target_name: str) -> None:
            if target_name == finals[1].name and "stage-1" in source.name:
                raise OSError("controlled second commit failure")
            original_replace(directory, source, target_name)

        monkeypatch.setattr(module, "replace_artifact", fail_primary)
    else:
        original_reopen = module.reopen_artifact_exact

        def fail_primary(
            directory,
            name: str,
            expected: bytes,
            label: str,
            expected_identity=None,
        ) -> None:
            if name == finals[1].name and label == "committed artifact 1":
                raise module.AnalysisError("controlled second reopen failure")
            original_reopen(directory, name, expected, label, expected_identity)

        monkeypatch.setattr(module, "reopen_artifact_exact", fail_primary)

    original_restore = module.restore_artifact

    def fail_selected_restore(directory, source, target_name: str) -> None:
        if target_name == finals[restore_index].name:
            raise OSError("controlled rollback restore failure")
        original_restore(directory, source, target_name)

    monkeypatch.setattr(module, "restore_artifact", fail_selected_restore)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.run(module_args(module, index, output, config, contract, probe))

    transaction = raised.value.transaction
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["recovery_required"] is True
    backup = list(output.glob(f".ga-monobehaviour-backup-{restore_index}-*.tmp"))
    assert len(backup) == 1
    assert backup[0].read_bytes() == old[restore_index]
    assert finals[restore_index].exists()
    assert finals[restore_index].read_bytes() != old[restore_index]
    assert str(backup[0]) in transaction["recovery_files"]


@pytest.mark.parametrize("late_kind", ["plain", "hardlink-input"])
def test_absent_destination_late_arrival_fails_closed_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    late_kind: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary = output / "monobehaviour-boundary-summary.json"
    report = output / "monobehaviour-boundary-analysis.md"
    late = b"late-independent-writer"
    config_before = config.read_bytes()
    original_replace = module.replace_artifact

    def inject_late_target(directory, source, target_name: str) -> None:
        if target_name == summary.name and not summary.exists():
            if late_kind == "plain":
                summary.write_bytes(late)
            else:
                os.link(config, summary)
        original_replace(directory, source, target_name)

    monkeypatch.setattr(module, "replace_artifact", inject_late_target)
    with pytest.raises(module.AnalysisError, match="appeared during artifact-set commit"):
        module.run(module_args(module, index, output, config, contract, probe))

    assert summary.read_bytes() == (late if late_kind == "plain" else config_before)
    assert config.read_bytes() == config_before
    assert not report.exists()
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


def test_pending_destination_write_between_commits_is_blocked_by_generation_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary = output / "monobehaviour-boundary-summary.json"
    report = output / "monobehaviour-boundary-analysis.md"
    summary.write_bytes(b"old-summary")
    report.write_bytes(b"old-report")
    drift = b"persistent-report-drift-between-commits"
    original_replace = module.replace_artifact
    blocked = False

    def inject_drift(directory, source, target_name: str) -> None:
        nonlocal blocked
        source_name = source.name
        original_replace(directory, source, target_name)
        if target_name == summary.name and "stage-0" in source_name:
            try:
                report.write_bytes(drift)
            except PermissionError:
                blocked = True

    monkeypatch.setattr(module, "replace_artifact", inject_drift)
    result = module.run(module_args(module, index, output, config, contract, probe))

    assert blocked is True
    assert result["artifact_transaction"]["transaction_state"] == "committed"
    result.snapshot_for_consumption()
    result.close()
    assert summary.read_bytes() != b"old-summary"
    assert report.read_bytes() != drift
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-commit", "second-commit"])
@pytest.mark.parametrize("writer_path", ["final", "hardlink"], ids=["same-size", "hardlink"])
def test_existing_destination_write_after_final_guard_is_kernel_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
    writer_path: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    finals = [
        output / "monobehaviour-boundary-summary.json",
        output / "monobehaviour-boundary-analysis.md",
    ]
    old = [b"old-summary", b"old-report"]
    for path, data in zip(finals, old, strict=True):
        path.write_bytes(data)
    write_target = finals[artifact_index]
    if writer_path == "hardlink":
        write_target = output / f"external-hardlink-{artifact_index}.bin"
        os.link(finals[artifact_index], write_target)

    original_rename = module._win_rename_handle
    injected = False
    blocked = False
    replace_flags: list[bool] = []

    def inject_at_exact_target_retirement(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected, blocked
        replace_flags.append(replace_existing)
        if not injected and f"backup-{artifact_index}-" in target_name:
            injected = True
            try:
                with write_target.open("r+b", buffering=0) as handle:
                    handle.seek(0)
                    handle.write(b"X" * len(old[artifact_index]))
                    handle.flush()
                    os.fsync(handle.fileno())
            except PermissionError:
                blocked = True
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )

    monkeypatch.setattr(module, "_win_rename_handle", inject_at_exact_target_retirement)
    result = module.run(module_args(module, index, output, config, contract, probe))

    assert injected is True
    assert blocked is True
    assert result["artifact_transaction"]["transaction_state"] == "committed"
    assert all(flag is False for flag in replace_flags)
    result.snapshot_for_consumption()
    result.close()
    if writer_path == "hardlink":
        assert write_target.read_bytes() == old[artifact_index]
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-commit", "second-commit"])
@pytest.mark.parametrize("operation", ["delete", "replace"], ids=["delete", "replace"])
def test_existing_destination_delete_or_replace_after_final_guard_is_kernel_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
    operation: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    finals = [
        output / "monobehaviour-boundary-summary.json",
        output / "monobehaviour-boundary-analysis.md",
    ]
    old = [b"old-summary", b"old-report"]
    for path, data in zip(finals, old, strict=True):
        path.write_bytes(data)
    attacker = output / f"external-replacement-{artifact_index}.bin"
    attacker.write_bytes(b"external-replacement")

    original_rename = module._win_rename_handle
    injected = False
    blocked = False

    def inject_at_exact_target_retirement(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected, blocked
        if not injected and f"backup-{artifact_index}-" in target_name:
            injected = True
            try:
                if operation == "delete":
                    finals[artifact_index].unlink()
                else:
                    os.replace(attacker, finals[artifact_index])
            except PermissionError:
                blocked = True
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )

    monkeypatch.setattr(module, "_win_rename_handle", inject_at_exact_target_retirement)
    result = module.run(module_args(module, index, output, config, contract, probe))

    assert injected is True
    assert blocked is True
    assert result["artifact_transaction"]["transaction_state"] == "committed"
    result.snapshot_for_consumption()
    result.close()
    if operation == "replace":
        assert attacker.read_bytes() == b"external-replacement"
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
@pytest.mark.parametrize("restore_index", [0, 1], ids=["first-target", "second-target"])
def test_rollback_restore_never_overwrites_destination_appearing_at_nt_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_index: int,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    finals = [
        output / "monobehaviour-boundary-summary.json",
        output / "monobehaviour-boundary-analysis.md",
    ]
    old = [b"old-summary", b"old-report"]
    for path, data in zip(finals, old, strict=True):
        path.write_bytes(data)

    original_replace = module.replace_artifact
    if restore_index == 0:
        def trigger_failure(directory, source, target_name: str) -> None:
            if target_name == finals[1].name and "stage-1" in source.name:
                raise OSError("controlled second commit failure")
            original_replace(directory, source, target_name)

        monkeypatch.setattr(module, "replace_artifact", trigger_failure)
    else:
        original_reopen = module.reopen_artifact_exact

        def trigger_failure(
            directory,
            name: str,
            expected: bytes,
            label: str,
            expected_identity=None,
        ) -> None:
            if name == finals[1].name and label == "committed artifact 1":
                raise module.AnalysisError("controlled second reopen failure")
            original_reopen(directory, name, expected, label, expected_identity)

        monkeypatch.setattr(module, "reopen_artifact_exact", trigger_failure)

    original_rename = module._win_rename_handle
    final_publish_counts: Counter[str] = Counter()
    external = f"external-rollback-{restore_index}".encode("ascii")
    injected = False
    replace_flags: list[bool] = []

    def inject_at_restore_rename(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        replace_flags.append(replace_existing)
        if target_name == finals[restore_index].name:
            final_publish_counts[target_name] += 1
            if final_publish_counts[target_name] == 2:
                finals[restore_index].write_bytes(external)
                injected = True
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )

    monkeypatch.setattr(module, "_win_rename_handle", inject_at_restore_rename)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.run(module_args(module, index, output, config, contract, probe))

    transaction = raised.value.transaction
    assert injected is True
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["recovery_required"] is True
    assert finals[restore_index].read_bytes() == external
    backup = list(output.glob(f".ga-monobehaviour-backup-{restore_index}-*.tmp"))
    assert len(backup) == 1 and backup[0].read_bytes() == old[restore_index]
    assert all(flag is False for flag in replace_flags)


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_post_nt_fault_after_exact_old_retirement_restores_the_old_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    """AW-Q14: an old->backup syscall success cannot be lost in cached state."""

    module = load_module()
    input_snapshot, output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_rename = module._win_rename_handle
    injected = False
    replace_flags: list[bool] = []

    def succeed_retirement_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        source_name = module._win_handle_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        replace_flags.append(replace_existing)
        if (
            not injected
            and source_name == finals[artifact_index].name
            and f"backup-{artifact_index}-" in target_name
        ):
            injected = True
            raise OSError("controlled post-NT fault after exact old retirement")

    monkeypatch.setattr(module, "_win_rename_handle", succeed_retirement_then_raise)
    with pytest.raises(OSError) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = getattr(raised.value, "transaction")
    assert injected is True
    assert transaction["transaction_state"] == "rolled_back"
    assert transaction["rollback_state"] == "complete"
    assert transaction["recovery_required"] is False
    assert transaction["recovery_files"] == []
    assert [path.read_bytes() for path in finals] == old
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))
    assert replace_flags and all(flag is False for flag in replace_flags)


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("restore_index", [0, 1], ids=["first-target", "second-target"])
def test_post_nt_fault_after_backup_restore_reports_only_existing_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_index: int,
) -> None:
    """AW-Q15: backup->final success is reconciled before recovery reporting."""

    module = load_module()
    input_snapshot, _output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_reopen = module.reopen_artifact_exact
    triggered_rollback = False

    def fail_after_second_publish(
        directory,
        name: str,
        expected: bytes,
        label: str,
        expected_identity=None,
    ) -> None:
        nonlocal triggered_rollback
        if label == "committed artifact 1" and not triggered_rollback:
            triggered_rollback = True
            raise module.AnalysisError("controlled rollback trigger")
        original_reopen(directory, name, expected, label, expected_identity)

    monkeypatch.setattr(module, "reopen_artifact_exact", fail_after_second_publish)
    original_rename = module._win_rename_handle
    injected = False

    def succeed_restore_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        source_name = module._win_handle_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if (
            not injected
            and target_name == finals[restore_index].name
            and f"backup-{restore_index}-" in source_name
        ):
            injected = True
            raise OSError("controlled post-NT fault after backup restore")

    monkeypatch.setattr(module, "_win_rename_handle", succeed_restore_then_raise)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = raised.value.transaction
    assert triggered_rollback is True
    assert injected is True
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["recovery_required"] is True
    assert finals[restore_index].read_bytes() == old[restore_index]
    assert transaction["recovery_files"]
    for recovery_text in transaction["recovery_files"]:
        recovery_path = Path(recovery_text)
        assert recovery_path.exists()
        assert recovery_path.read_bytes() in {*old, *new}


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_post_nt_fault_after_reservation_move_cleans_the_actual_private_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    """reservation->private state is reconciled before finally cleanup."""

    module = load_module()
    input_snapshot, output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_rename = module._win_rename_handle
    injected = False

    def succeed_reservation_move_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        source_name = module._win_handle_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if (
            not injected
            and f"backup-{artifact_index}-" in source_name
            and f"reservation-{artifact_index}-" in target_name
        ):
            injected = True
            raise OSError("controlled post-NT fault after reservation move")

    monkeypatch.setattr(module, "_win_rename_handle", succeed_reservation_move_then_raise)
    with pytest.raises(OSError) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = getattr(raised.value, "transaction")
    assert injected is True
    assert transaction["transaction_state"] == "rolled_back"
    assert transaction["rollback_state"] == "complete"
    assert [path.read_bytes() for path in finals] == old
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
@pytest.mark.parametrize(
    "fault_type",
    [OSError, KeyboardInterrupt],
    ids=["wrapper-error", "async-baseexception"],
)
def test_post_nt_fault_after_stage_publish_is_recorded_as_committed_then_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
    fault_type: type[BaseException],
) -> None:
    """stage->final success must enter rollback scope even when its wrapper faults."""

    module = load_module()
    input_snapshot, output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_rename = module._win_rename_handle
    injected = False

    def succeed_publish_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        source_name = module._win_handle_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if (
            not injected
            and target_name == finals[artifact_index].name
            and f"stage-{artifact_index}-" in source_name
        ):
            injected = True
            raise fault_type("controlled post-NT fault after stage publish")

    monkeypatch.setattr(module, "_win_rename_handle", succeed_publish_then_raise)
    with pytest.raises(fault_type) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = getattr(raised.value, "transaction")
    assert injected is True
    assert transaction["transaction_state"] == "rolled_back"
    assert transaction["rollback_state"] == "complete"
    assert [path.read_bytes() for path in finals] == old
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_post_nt_fault_after_transaction_final_retirement_has_exact_recovery_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    """transaction-final->private-stage faults preserve both exact generations."""

    module = load_module()
    input_snapshot, _output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_reopen = module.reopen_artifact_exact
    triggered_rollback = False

    def trigger_after_both_commits(
        directory,
        name: str,
        expected: bytes,
        label: str,
        expected_identity=None,
    ) -> None:
        nonlocal triggered_rollback
        if label == "post-commit artifact 1" and not triggered_rollback:
            triggered_rollback = True
            raise module.AnalysisError("controlled post-commit rollback trigger")
        original_reopen(directory, name, expected, label, expected_identity)

    monkeypatch.setattr(module, "reopen_artifact_exact", trigger_after_both_commits)
    original_rename = module._win_rename_handle
    injected = False

    def succeed_private_retirement_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected
        source_name = module._win_handle_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if (
            not injected
            and source_name == finals[artifact_index].name
            and f"stage-{artifact_index}-" in target_name
        ):
            injected = True
            raise OSError("controlled post-NT fault after transaction final retirement")

    monkeypatch.setattr(module, "_win_rename_handle", succeed_private_retirement_then_raise)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = raised.value.transaction
    assert triggered_rollback is True
    assert injected is True
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["recovery_required"] is True
    assert transaction["recovery_files"]
    for recovery_text in transaction["recovery_files"]:
        recovery_path = Path(recovery_text)
        assert recovery_path.exists()
        assert recovery_path.read_bytes() in {*old, *new}


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize(
    "rename_stage",
    [
        "reservation-to-private",
        "old-to-backup",
        "stage-to-final",
        "final-to-private",
        "backup-to-final",
    ],
)
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
@pytest.mark.parametrize(
    "coordinator_fault",
    ["handle-path-query", "basename-relative-identity", "continuous-double-fault"],
)
def test_every_nt_rename_stage_survives_coordinator_faults_without_hidden_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rename_stage: str,
    artifact_index: int,
    coordinator_fault: str,
) -> None:
    """BD-Q16: independent coordinator failures cannot lose an exact generation."""

    module = load_module()
    input_snapshot, output, finals, old, new = direct_transaction_case(module, tmp_path)
    if rename_stage in {"final-to-private", "backup-to-final"}:
        original_reopen = module.reopen_artifact_exact

        def trigger_rollback_after_both_commits(
            directory,
            name: str,
            expected: bytes,
            label: str,
            expected_identity=None,
        ) -> None:
            if label == "post-commit artifact 1":
                raise module.AnalysisError("controlled rollback trigger")
            original_reopen(directory, name, expected, label, expected_identity)

        monkeypatch.setattr(module, "reopen_artifact_exact", trigger_rollback_after_both_commits)

    original_rename = module._win_rename_handle
    original_basename = module._win_handle_basename
    original_entry_identity = module.StableOutputDirectory.entry_identity
    injected = False
    active = False
    coordinator_faults: list[str] = []
    candidates: tuple[str, str] | None = None
    query_budget = 3 if coordinator_fault == "continuous-double-fault" else 1
    identity_budget = 4 if coordinator_fault == "continuous-double-fault" else 1

    def stage_matches(source_name: str, target_name: str) -> bool:
        if rename_stage == "reservation-to-private":
            return (
                f"backup-{artifact_index}-" in source_name
                and f"reservation-{artifact_index}-" in target_name
            )
        if rename_stage == "old-to-backup":
            return (
                source_name == finals[artifact_index].name
                and f"backup-{artifact_index}-" in target_name
            )
        if rename_stage == "stage-to-final":
            return (
                f"stage-{artifact_index}-" in source_name
                and target_name == finals[artifact_index].name
            )
        if rename_stage == "final-to-private":
            return (
                source_name == finals[artifact_index].name
                and f"stage-{artifact_index}-" in target_name
            )
        return (
            f"backup-{artifact_index}-" in source_name
            and target_name == finals[artifact_index].name
        )

    def succeed_target_rename_then_raise(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected, active, candidates
        source_name = original_basename(source_handle)
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if not injected and stage_matches(source_name, target_name):
            injected = True
            active = True
            candidates = (source_name, target_name)
            raise OSError(f"controlled post-syscall fault at {rename_stage}")

    def fail_handle_path(file_object) -> str:
        nonlocal query_budget
        if (
            active
            and coordinator_fault in {"handle-path-query", "continuous-double-fault"}
            and query_budget > 0
        ):
            query_budget -= 1
            coordinator_faults.append("handle-path-query")
            raise OSError("controlled GetFinalPathNameByHandleW failure")
        return original_basename(file_object)

    def fail_ordinary_identity(self, name: str):
        nonlocal identity_budget
        if (
            active
            and candidates is not None
            and name.casefold() in {item.casefold() for item in candidates}
            and coordinator_fault
            in {"basename-relative-identity", "continuous-double-fault"}
            and identity_budget > 0
        ):
            identity_budget -= 1
            coordinator_faults.append("basename-relative-identity")
            raise OSError("controlled ordinary relative identity failure")
        return original_entry_identity(self, name)

    monkeypatch.setattr(module, "_win_rename_handle", succeed_target_rename_then_raise)
    monkeypatch.setattr(module, "_win_handle_basename", fail_handle_path)
    monkeypatch.setattr(module.StableOutputDirectory, "entry_identity", fail_ordinary_identity)

    with pytest.raises((OSError, module.ArtifactTransactionError)) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    transaction = getattr(raised.value, "transaction")
    assert injected is True
    assert coordinator_faults
    assert transaction["transaction_state"] in {"rolled_back", "rollback_incomplete"}
    failover_evidence = transaction["rename_coordination_evidence"]
    assert failover_evidence
    recorded_methods = {
        error["method"]
        for item in failover_evidence
        for error in item["evidence"]["errors"]
    }
    if coordinator_fault in {"handle-path-query", "continuous-double-fault"}:
        assert "GetFinalPathNameByHandleW" in recorded_methods
    if coordinator_fault in {
        "basename-relative-identity",
        "continuous-double-fault",
    }:
        assert "ordinary-relative-reopen" in recorded_methods
    assert all(
        item["exact_open_object_expected_identity"]
        and item["evidence"]["candidate_names"]
        for item in failover_evidence
    )
    reported: set[str] = set()
    for recovery_text in transaction["recovery_files"]:
        path = Path(recovery_text)
        assert path.exists()
        assert path.read_bytes() in {*old, *new}
        reported.add(os.path.normcase(os.path.abspath(path)))
    for cleanup_text in transaction["cleanup_required_paths"]:
        path = Path(cleanup_text)
        if not path.is_absolute():
            path = output / path
        assert path.exists()
        reported.add(os.path.normcase(os.path.abspath(path)))
    residue = list(output.glob(".ga-monobehaviour-*.tmp"))
    assert all(os.path.normcase(os.path.abspath(path)) in reported for path in residue)
    assert all(path.exists() and path.read_bytes() in {*old, *new} for path in finals)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_commit_result_holds_both_published_generations_until_explicit_consumption(
    tmp_path: Path,
) -> None:
    """BD-Q17: the return value owns both mandatory generation leases."""

    module = load_module()
    input_snapshot, output, finals, _old, new = direct_transaction_case(module, tmp_path)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    attackers = [b"attacker-summary", b"attacker-report"]
    for index, final in enumerate(finals):
        replacement = output / f"external-before-consume-{index}.bin"
        replacement.write_bytes(attackers[index])
        with pytest.raises(PermissionError):
            os.replace(replacement, final)
    consumed = owner.snapshot_for_consumption()
    assert consumed["handoff_state"] == "consumed_while_lease_held"
    assert consumed["published_lease_state"] == "held_during_consumption"
    owner.close()
    assert owner.closed is True
    assert owner["handoff_state"] == "released"
    assert [path.read_bytes() for path in finals] == new
    for index, final in enumerate(finals):
        replacement = output / f"external-after-release-{index}.bin"
        replacement.write_bytes(attackers[index])
        os.replace(replacement, final)
    assert [path.read_bytes() for path in finals] == attackers


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_cleanup_return_gap_replacement_is_blocked_by_owned_published_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original BD-Q17 counterexample is now a permanent positive test."""

    module = load_module()
    input_snapshot, output, finals, _old, new = direct_transaction_case(module, tmp_path)
    original_cleanup = module.cleanup_created_files
    blocked: list[str] = []

    def cleanup_then_attack(files):
        result = original_cleanup(files)
        for index, final in enumerate(finals):
            replacement = output / f"external-cleanup-gap-{index}.bin"
            replacement.write_bytes(f"attacker-{index}".encode("ascii"))
            try:
                os.replace(replacement, final)
            except PermissionError:
                blocked.append(final.name)
        return result

    monkeypatch.setattr(module, "cleanup_created_files", cleanup_then_attack)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    assert blocked == [path.name for path in finals]
    assert owner["transaction_state"] == "committed"
    owner.snapshot_for_consumption()
    owner.close()
    assert [path.read_bytes() for path in finals] == new


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_post_success_primary_lease_close_fault_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    module = load_module()
    input_snapshot, _output, finals, _old, new = direct_transaction_case(module, tmp_path)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    original_close = module.StableOpenFile.close
    injected = False

    def close_then_raise(self) -> None:
        nonlocal injected
        role = getattr(self, "release_role", "")
        original_close(self)
        if not injected and role == f"primary-{artifact_index}":
            injected = True
            raise OSError("controlled wrapper fault after primary lease close")

    monkeypatch.setattr(module.StableOpenFile, "close", close_then_raise)
    owner.snapshot_for_consumption()
    owner.close()
    assert injected is True
    assert owner["handoff_state"] == "released"
    assert owner["release_errors"] == []
    assert [path.read_bytes() for path in finals] == new


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_last_guardian_close_gap_drift_is_machine_readable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    module = load_module()
    input_snapshot, output, finals, _old, new = direct_transaction_case(module, tmp_path)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    original_close = module.StableOpenFile.close
    attacker = b"attacker-after-last-guardian"
    injected = False

    def close_last_guardian_then_replace(self) -> None:
        nonlocal injected
        role = getattr(self, "release_role", "")
        original_close(self)
        if not injected and role == f"guardian-B-{artifact_index}":
            replacement = output / f"release-gap-attacker-{artifact_index}.bin"
            replacement.write_bytes(attacker)
            os.replace(replacement, finals[artifact_index])
            injected = True

    monkeypatch.setattr(module.StableOpenFile, "close", close_last_guardian_then_replace)
    owner.snapshot_for_consumption()
    with pytest.raises(module.ArtifactTransactionError) as raised:
        owner.close()
    transaction = raised.value.transaction
    assert injected is True
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["handoff_state"] == "release_incomplete"
    assert transaction["recovery_required"] is True
    assert str(finals[artifact_index]) in transaction["release_required_paths"]
    assert finals[artifact_index].read_bytes() == attacker


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_consumer_serialization_failure_closes_run_result_owner(
    tmp_path: Path,
) -> None:
    """A caller failure before handoff completion still releases exact leases."""

    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    result = module.run(module_args(module, index, output, config, contract, probe))
    with pytest.raises(TypeError):
        with result as leased:
            consumed = leased.snapshot_for_consumption()
            assert consumed["artifact_transaction"]["published_lease_state"] == (
                "held_during_consumption"
            )
            json.dumps({"consumed": consumed, "unserializable": object()})
    assert result.closed is True
    assert result["artifact_transaction"]["handoff_state"] == "released"
    assert (output / "monobehaviour-boundary-summary.json").is_file()
    assert (output / "monobehaviour-boundary-analysis.md").is_file()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_cli_serialization_failure_releases_owner_and_reports_machine_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    original_dumps = module.json.dumps

    def reject_success_payload(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("status") in {
            "ok",
            "ok_with_cleanup_warnings",
        }:
            raise TypeError("controlled CLI serialization failure")
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(module.json, "dumps", reject_success_payload)
    exit_code = module.main(
        [
            "--index",
            str(index),
            "--output-dir",
            str(output),
            "--config",
            str(config),
            "--contract",
            str(contract),
            "--probe-evidence",
            str(probe),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "controlled CLI serialization failure" in captured.err
    error = json.loads(captured.err.removeprefix("ERROR: "))
    transaction = error["artifact_transaction"]
    assert error["status"] == "error"
    assert transaction["handoff_state"] == "released"
    assert transaction["published_lease_state"] == "released"
    for index, name in enumerate(
        ["monobehaviour-boundary-summary.json", "monobehaviour-boundary-analysis.md"]
    ):
        replacement = output / f"after-serialization-failure-{index}.bin"
        replacement.write_bytes(b"external")
        os.replace(replacement, output / name)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_guardian_establishment_failure_exhaustively_closes_primary_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    input_snapshot, output, finals, _old, new = direct_transaction_case(module, tmp_path)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    original_dup = module.os.dup
    injected = False

    def fail_first_guardian_dup(handle: int) -> int:
        nonlocal injected
        if not injected:
            injected = True
            raise OSError("controlled guardian duplication failure")
        return original_dup(handle)

    monkeypatch.setattr(module.os, "dup", fail_first_guardian_dup)
    owner.snapshot_for_consumption()
    with pytest.raises(module.ArtifactTransactionError) as raised:
        owner.close()
    transaction = raised.value.transaction
    assert injected is True
    assert owner.closed is True
    assert transaction["handoff_state"] == "release_incomplete"
    assert transaction["release_errors"][0]["operation"] == (
        "establish-release-guardians"
    )
    for index, final in enumerate(finals):
        replacement = output / f"after-guardian-failure-{index}.bin"
        replacement.write_bytes(b"external")
        os.replace(replacement, final)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows mandatory share leases")
def test_post_success_directory_close_fault_is_reconciled_after_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    input_snapshot, _output, finals, _old, new = direct_transaction_case(module, tmp_path)
    owner = module.commit_artifact_set(
        list(zip(finals, new, strict=True)),
        [input_snapshot],
    )
    original_close = module.StableOutputDirectory.close
    injected = False

    def close_then_raise(self) -> None:
        nonlocal injected
        original_close(self)
        if not injected:
            injected = True
            raise OSError("controlled wrapper fault after directory close")

    monkeypatch.setattr(module.StableOutputDirectory, "close", close_then_raise)
    owner.snapshot_for_consumption()
    owner.close()
    assert injected is True
    assert owner.closed is True
    assert owner["handoff_state"] == "released"
    assert owner["release_errors"] == []
    assert [path.read_bytes() for path in finals] == new


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_vacated_backup_name_claim_is_preserved_for_each_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    """A claimant racing reservation->private cannot be overwritten or cleaned."""

    module = load_module()
    input_snapshot, _output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_rename = module._win_rename_handle
    attacker = b"attacker-private-name"
    raced_path: Path | None = None
    injected = False

    def claim_vacated_backup_name(
        source_handle,
        target_directory_handle: int,
        target_name: str,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal injected, raced_path
        original_rename(
            source_handle,
            target_directory_handle,
            target_name,
            replace_existing=replace_existing,
        )
        if not injected and f"reservation-{artifact_index}-" in target_name:
            injected = True
            raced_path = finals[0].parent / target_name.replace(
                "reservation-", "backup-", 1
            )
            raced_path.write_bytes(attacker)

    monkeypatch.setattr(module, "_win_rename_handle", claim_vacated_backup_name)
    with pytest.raises(OSError):
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    assert injected is True and raced_path is not None
    assert raced_path.read_bytes() == attacker
    assert [path.read_bytes() for path in finals] == old


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows NT rename path")
@pytest.mark.parametrize("artifact_index", [0, 1], ids=["first-target", "second-target"])
def test_private_stage_name_claim_is_preserved_for_each_artifact_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    """A claimant racing transaction-final->private remains untouched."""

    module = load_module()
    input_snapshot, output, finals, old, new = direct_transaction_case(module, tmp_path)
    original_reopen = module.reopen_artifact_exact

    def trigger_after_second_publish(
        directory,
        name: str,
        expected: bytes,
        label: str,
        expected_identity=None,
    ) -> None:
        if label == "committed artifact 1":
            raise module.AnalysisError("controlled rollback trigger")
        original_reopen(directory, name, expected, label, expected_identity)

    monkeypatch.setattr(module, "reopen_artifact_exact", trigger_after_second_publish)
    original_rollback = module.rollback_artifact_set
    attacker = b"attacker-private-stage"
    raced_path: Path | None = None

    def claim_private_stage(directory, originals, committed):
        nonlocal raced_path
        selected = next(
            item for item in committed if item.final_path == finals[artifact_index]
        )
        raced_path = output / selected.private_name
        raced_path.write_bytes(attacker)
        return original_rollback(directory, originals, committed)

    monkeypatch.setattr(module, "rollback_artifact_set", claim_private_stage)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.commit_artifact_set(
            list(zip(finals, new, strict=True)),
            [input_snapshot],
        )

    assert raced_path is not None and raced_path.read_bytes() == attacker
    assert finals[artifact_index].read_bytes() == new[artifact_index]
    backups = list(output.glob(f".ga-monobehaviour-backup-{artifact_index}-*.tmp"))
    assert len(backups) == 1 and backups[0].read_bytes() == old[artifact_index]
    assert raised.value.transaction["transaction_state"] == "rollback_incomplete"
    assert all(Path(path).exists() for path in raised.value.transaction["recovery_files"])


def test_committed_destination_drift_during_rollback_is_preserved_with_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary = output / "monobehaviour-boundary-summary.json"
    report = output / "monobehaviour-boundary-analysis.md"
    summary.write_bytes(b"old-summary")
    report.write_bytes(b"old-report")
    drift = b"external-summary-drift-during-rollback"
    original_replace = module.replace_artifact

    def fail_second_commit(directory, source, target_name: str) -> None:
        if target_name == report.name and "stage-1" in source.name:
            raise OSError("controlled second commit failure")
        original_replace(directory, source, target_name)

    original_rollback = module.rollback_artifact_set

    def drift_before_rollback(directory, originals, committed):
        committed[0].stage_file.close()
        drift_file = module.open_exclusive_temporary(
            directory, summary, "external-rollback-drift"
        )
        module.write_open_temporary_exact(drift_file, drift, "external rollback drift")
        directory.replace(drift_file, summary.name, replace_existing=True)
        drift_file.temporary = False
        drift_file.close()
        return original_rollback(directory, originals, committed)

    monkeypatch.setattr(module, "replace_artifact", fail_second_commit)
    monkeypatch.setattr(module, "rollback_artifact_set", drift_before_rollback)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.run(module_args(module, index, output, config, contract, probe))

    assert summary.read_bytes() == drift
    backup = list(output.glob(".ga-monobehaviour-backup-0-*.tmp"))
    assert len(backup) == 1 and backup[0].read_bytes() == b"old-summary"
    assert str(backup[0]) in raised.value.transaction["recovery_files"]


@pytest.mark.parametrize("failure_action", ["delete", "close"])
def test_transient_cleanup_failure_is_exhaustive_visible_and_recovers_without_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_action: str,
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary = output / "monobehaviour-boundary-summary.json"
    report = output / "monobehaviour-boundary-analysis.md"
    summary.write_bytes(b"old-summary")
    report.write_bytes(b"old-report")
    closed = False
    original_directory_close = module.StableOutputDirectory.close

    def record_directory_close(self) -> None:
        nonlocal closed
        closed = True
        original_directory_close(self)

    monkeypatch.setattr(module.StableOutputDirectory, "close", record_directory_close)
    failed: set[str] = set()
    if failure_action == "delete":
        original = module.StableOpenFile.delete

        def fail_once(self) -> None:
            if self.temporary and "backup-" in self.name and self.name not in failed:
                failed.add(self.name)
                raise OSError("controlled transient cleanup delete failure")
            original(self)

        monkeypatch.setattr(module.StableOpenFile, "delete", fail_once)
    else:
        original = module.StableOpenFile.close

        def fail_once(self) -> None:
            if self.temporary and "backup-" in self.name and self.name not in failed:
                failed.add(self.name)
                raise OSError("controlled transient cleanup close failure")
            original(self)

        monkeypatch.setattr(module.StableOpenFile, "close", fail_once)

    result = module.run(module_args(module, index, output, config, contract, probe))
    transaction = result["artifact_transaction"]
    assert result["status"] == "ok_with_cleanup_warnings"
    assert transaction["transaction_state"] == "committed"
    assert transaction["cleanup_state"] == "recovered_warnings"
    assert transaction["cleanup_required_paths"] == []
    assert len(failed) == 2
    result.snapshot_for_consumption()
    result.close()
    assert closed is True
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


def test_cleanup_orchestrator_failure_still_closes_directory_and_reports_published_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "monobehaviour-boundary-summary.json").write_bytes(b"old-summary")
    (output / "monobehaviour-boundary-analysis.md").write_bytes(b"old-report")
    closed = False
    original_close = module.StableOutputDirectory.close

    def record_close(self) -> None:
        nonlocal closed
        closed = True
        original_close(self)

    monkeypatch.setattr(module.StableOutputDirectory, "close", record_close)
    monkeypatch.setattr(
        module,
        "cleanup_created_files",
        lambda _files: (_ for _ in ()).throw(OSError("controlled cleanup orchestrator failure")),
    )
    result = module.run(module_args(module, index, output, config, contract, probe))

    transaction = result["artifact_transaction"]
    assert result["status"] == "ok_with_cleanup_warnings"
    assert transaction["transaction_state"] == "committed"
    assert transaction["published_generation"] == "new"
    assert transaction["cleanup_state"] == "warnings"
    assert transaction["cleanup_required_paths"] == ["cleanup-state-unknown"]
    result.snapshot_for_consumption()
    result.close()
    assert closed is True


def test_persistent_backup_cleanup_failures_try_every_item_and_report_exact_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "monobehaviour-boundary-summary.json").write_bytes(b"old-summary")
    (output / "monobehaviour-boundary-analysis.md").write_bytes(b"old-report")
    attempts: Counter[str] = Counter()
    original_delete = module.StableOpenFile.delete

    def fail_backup_delete(self) -> None:
        if self.temporary and "backup-" in self.name:
            attempts[self.name] += 1
            raise OSError("controlled persistent cleanup delete failure")
        original_delete(self)

    monkeypatch.setattr(module.StableOpenFile, "delete", fail_backup_delete)
    result = module.run(module_args(module, index, output, config, contract, probe))

    transaction = result["artifact_transaction"]
    residue = sorted(path.name for path in output.glob(".ga-monobehaviour-*.tmp"))
    assert result["status"] == "ok_with_cleanup_warnings"
    assert transaction["transaction_state"] == "committed"
    assert transaction["cleanup_state"] == "warnings"
    assert transaction["cleanup_required_paths"] == residue
    assert len(residue) == 2
    assert set(attempts) == set(residue)
    assert all(count == 2 for count in attempts.values())
    result.snapshot_for_consumption()
    result.close()


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows stable directory path")
def test_cleanup_does_not_report_or_delete_a_different_generation_reusing_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cleanup_required_paths contains only the exact owned identity."""

    module = load_module()
    output = tmp_path / "output"
    output.mkdir()
    directory = module.StableOutputDirectory.open_or_create(output)
    opened = module.open_exclusive_temporary(
        directory,
        output / "summary.json",
        "cleanup-reuse",
    )
    module.write_open_temporary_exact(opened, b"owned-generation", "cleanup reuse")
    attacker_source = output / "attacker.bin"
    attacker_source.write_bytes(b"external-generation")
    original_close = opened.close

    monkeypatch.setattr(opened, "delete", lambda: None)

    def close_then_reuse_name() -> None:
        original_close()
        os.replace(attacker_source, output / opened.name)

    monkeypatch.setattr(opened, "close", close_then_reuse_name)
    try:
        cleanup = module.cleanup_created_files([opened])
    finally:
        directory.close()

    assert cleanup["cleanup_state"] == "recovered_warnings"
    assert cleanup["cleanup_required_paths"] == []
    assert any(
        warning["action"] == "preserve-nonowned-cleanup-generation"
        for warning in cleanup["cleanup_warnings"]
    )
    assert (output / opened.name).read_bytes() == b"external-generation"


def test_absent_generation_rollback_delete_failure_is_explicit_and_not_generic_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    index, config, contract, probe, _metadata_input = transaction_case(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    summary = output / "monobehaviour-boundary-summary.json"
    report = output / "monobehaviour-boundary-analysis.md"
    original_replace = module.replace_artifact

    def fail_second_commit(directory, source, target_name: str) -> None:
        if target_name == report.name and "stage-1" in source.name:
            raise OSError("controlled second commit failure")
        original_replace(directory, source, target_name)

    original_delete = module.StableOpenFile.delete

    def fail_committed_delete(self) -> None:
        if not self.temporary and self.name == summary.name:
            raise OSError("controlled rollback delete failure")
        original_delete(self)

    monkeypatch.setattr(module, "replace_artifact", fail_second_commit)
    monkeypatch.setattr(module.StableOpenFile, "delete", fail_committed_delete)
    with pytest.raises(module.ArtifactTransactionError) as raised:
        module.run(module_args(module, index, output, config, contract, probe))

    transaction = raised.value.transaction
    assert transaction["transaction_state"] == "rollback_incomplete"
    assert transaction["recovery_required"] is True
    assert any(
        item["operation"] == "delete-new-artifact"
        for item in transaction["rollback_errors"]
    )
    assert summary.exists()
    assert not report.exists()
    assert not list(output.glob(".ga-monobehaviour-*.tmp"))


@pytest.mark.parametrize(
    "extra_header",
    [
        ["byte_size", "byte_size"],
        ["raw_sha256", "raw_sha256"],
        ["status"],
        ["byte_size", "Byte_Size"],
        [""],
    ],
    ids=["duplicate-byte", "duplicate-raw-sha", "duplicate-structural", "casefold", "empty"],
)
def test_ambiguous_csv_headers_fail_before_analysis_or_output(
    tmp_path: Path, extra_header: list[str]
) -> None:
    root = tmp_path / "raw"
    index = root / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    header = [*FIELDS, *extra_header]
    if extra_header == ["status"]:
        header = [*FIELDS, "status"]
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow([""] * len(header))
    output = tmp_path / "output"
    result = run_tool(index, output)
    assert result.returncode == 2
    assert "CSV header" in result.stderr
    assert not output.exists()


def test_metadata_pass_header_must_exactly_match_first_pass() -> None:
    module = load_module()
    with pytest.raises(module.AnalysisError, match="changed between streaming passes"):
        module.validate_csv_header(
            ["asset_type", "status", "remarks"],
            label="index metadata pass",
            expected=["status", "asset_type", "remarks"],
        )


def test_project_specific_names_counts_and_component_classes_are_not_hardcoded() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SCRIPT, DEFAULT_CONFIG, DEFAULT_CONTRACT)
    )
    forbidden = [
        "觅长生",
        "1104009",
        "1100789",
        "MapSeaCompent",
        "MapInstComport",
        "UIIconShow",
        "jsonData",
    ]
    assert not any(token in combined for token in forbidden)


def test_skill_and_direct_workflows_route_to_boundary_analysis() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    branch = (SKILL_ROOT / "references" / "branch-completion-checks.md").read_text(
        encoding="utf-8"
    )
    workflow = (SKILL_ROOT / "references" / "full-extraction-workflow.md").read_text(
        encoding="utf-8"
    )
    pitfalls = (SKILL_ROOT / "references" / "common-pitfalls.md").read_text(encoding="utf-8")
    for text in (skill, branch, workflow, pitfalls):
        assert "analyze_unitypy_monobehaviour_boundary.py" in text
    assert "overall_recovery_estimate" in workflow
    assert "probe" in branch


def test_destination_generation_contract_requires_kernel_binding_or_fresh_output() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    contract = strict_load(DEFAULT_CONTRACT)
    workflow = (SKILL_ROOT / "references" / "full-extraction-workflow.md").read_text(
        encoding="utf-8"
    )
    integrity = contract["semantic_requirements"]["input_integrity"]
    assert contract["contract_version"] == "1.5.0"
    assert "ReplaceIfExists=0" in integrity
    assert "no code path may use ReplaceIfExists=1" in integrity
    assert "mandatory target-generation primitive" in integrity
    assert "fresh output directory" in integrity
    assert "syscall postcondition coordinator" in integrity
    assert "query the exact open source handle's current basename" in integrity
    assert "must never leave cached pre-rename state" in integrity
    assert "unique old generation exists only at backup" in integrity
    assert "direct RootDirectory-relative NtCreateFile plus fstat identity probe" in integrity
    assert "published-generation lease owner" in integrity
    assert "CLI serializes" in integrity
    assert "release_required_paths" in integrity
    assert "class ArtifactCommitResult" in source
    assert "class AnalysisRunResult" in source
    assert "replace_existing=True" not in source
    assert "仅 `FILE_SHARE_READ`" in workflow
    assert "ReplaceIfExists=1" in workflow
    assert "不得出现" in workflow


def test_current_real_run_reconciles_when_environment_is_provided() -> None:
    index_text = os.environ.get("GAME_ANALYSIS_MONOBEHAVIOUR_REAL_INDEX")
    output_text = os.environ.get("GAME_ANALYSIS_MONOBEHAVIOUR_REAL_OUTPUT")
    if not index_text or not output_text:
        pytest.skip("real index/output environment not provided")
    index = Path(index_text)
    output = Path(output_text)
    probe_text = os.environ.get("GAME_ANALYSIS_MONOBEHAVIOUR_REAL_PROBE")
    result = run_tool(index, output, probe=Path(probe_text) if probe_text else None)
    assert result.returncode == 0, result.stderr
    summary = strict_load(output / "monobehaviour-boundary-summary.json")
    independent_status: Counter[str] = Counter()
    total_rows = 0
    mono_rows = 0
    with index.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            if row["asset_type"] == "MonoBehaviour":
                mono_rows += 1
                independent_status[row["status"] or "<empty>"] += 1
    assert summary["index"]["total_row_count"] == total_rows
    assert summary["index"]["mono_behaviour_count"] == mono_rows
    assert summary["index"]["status_counts"] == dict(sorted(independent_status.items()))
    assert summary["validation"]["all_checks_passed"] is True

from __future__ import annotations

import csv
import json
import os
import sys
import weakref
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


class FakeType:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeAssetsFile:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeData:
    def __init__(self, name: str) -> None:
        self.m_Name = name


class FakeObject:
    def __init__(
        self,
        path_id: int,
        name: str,
        *,
        asset_type: str = "Material",
        interrupt: bool = False,
    ) -> None:
        self.path_id = path_id
        self.type = FakeType(asset_type)
        self.assets_file = FakeAssetsFile("serialized.assets")
        self._name = name
        self._interrupt = interrupt

    def read(self) -> FakeData:
        if self._interrupt:
            raise KeyboardInterrupt("synthetic interruption")
        return FakeData(self._name)

    def read_typetree(self) -> dict[str, Any]:
        return {"name": self._name}


class FakeEnv:
    container: dict[str, Any] = {}

    def __init__(self, objects: list[FakeObject]) -> None:
        self.objects = objects


class PlannedUnityPy:
    plans: dict[str, list[FakeObject] | Exception] = {}
    calls: list[str] = []
    env_refs: list[weakref.ReferenceType[FakeEnv]] = []

    @classmethod
    def reset(cls) -> None:
        cls.plans = {}
        cls.calls = []
        cls.env_refs = []

    @classmethod
    def load(cls, path: str) -> FakeEnv:
        name = Path(path).name
        cls.calls.append(name)
        plan = cls.plans[name]
        if isinstance(plan, Exception):
            raise plan
        env = FakeEnv(plan)
        cls.env_refs.append(weakref.ref(env))
        return env


def make_sources(root: Path, *names: str) -> Path:
    source = root / "Game"
    data_dir = source / "Game_Data"
    data_dir.mkdir(parents=True)
    for name in names:
        (data_dir / name).write_bytes(b"synthetic")
    return source


def read_index(output: Path) -> list[dict[str, str]]:
    with (output / "all_resources_index.csv").open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def read_log(output: Path) -> list[dict[str, str]]:
    with (output / "export_log.csv").open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def output_file_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def write_legacy_checkpoint_fixture(
    source: Path,
    output: Path,
    *,
    status: str | None,
    rows: list[dict[str, Any]],
) -> tuple[Path, str]:
    boundary = extractor.OutputBoundary(output, source).initialize()
    discovered = extractor.discover_unity_sources(source)
    labels = [extractor.rel(path, source) for path in discovered]
    assert len(labels) == 1
    source_label = labels[0]
    identity = extractor.checkpoint_identity(
        source,
        output,
        "metadata",
        extractor.manifest_entries(discovered, source),
        extractor.manifest_entries(
            extractor.discover_unity_external_payloads(source),
            source,
        ),
        extractor.manifest_entries(sorted(source.rglob("catalog*.json")), source),
        protocol_version=extractor.LEGACY_RESUME_PROTOCOL_VERSION,
    )
    checkpoint_path = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    sources: dict[str, Any] = {}
    if status is not None:
        fragment_relative = (
            f"{extractor.RESUME_STATE_DIR_NAME}/fragments/"
            f"{extractor.source_fragment_name(0, source_label)}"
        )
        fragment_path = output / fragment_relative
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        with fragment_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=extractor.INDEX_FIELDS)
            writer.writerows(rows)
        sources[source_label] = {
            "status": status,
            "stage": "export",
            "attempts": 1,
            "fragment": fragment_relative,
            "fragment_sha256": extractor.sha256_file(fragment_path),
            "row_count": len(rows),
            "type_counts": {"Material": len(rows)} if rows else {},
            "exported_counts": {},
        }
    extractor.atomic_write_json(
        checkpoint_path,
        {
            "protocol_version": extractor.LEGACY_RESUME_PROTOCOL_VERSION,
            "identity": identity,
            "created_at": "synthetic",
            "updated_at": "synthetic",
            "status": "complete" if status == "completed" else "running",
            "resume_count": 0,
            "sources": sources,
        },
        output_boundary=boundary,
    )
    extractor.atomic_write_json(
        output / "summary.json",
        {
            "source_root": str(source),
            "resume": {
                "protocol_version": extractor.LEGACY_RESUME_PROTOCOL_VERSION,
                "resume_count": 0,
            },
        },
        output_boundary=boundary,
    )
    return checkpoint_path, source_label


def legacy_row(
    source_label: str,
    asset_id: str,
    *,
    export_path: str = "",
) -> dict[str, Any]:
    row = {field: "" for field in extractor.INDEX_FIELDS}
    row.update(
        {
            "asset_id": asset_id,
            "source_file": source_label,
            "path_id": "7",
            "asset_type": "Material",
            "asset_name": asset_id,
            "export_kind": "metadata",
            "export_path": export_path,
            "status": "read_error" if not export_path else "exported",
        }
    )
    return row


def test_interrupted_source_resumes_without_duplicate_rows_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "first.assets", "second.assets")
    output = tmp_path / "out"
    existing_partial_asset = output / "images" / "Texture2D" / "existing_partial.png"
    existing_partial_asset.parent.mkdir(parents=True)
    existing_partial_asset.write_bytes(b"preserve existing UnityPy output")
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {
        "first.assets": [FakeObject(7, "first")],
        "second.assets": [FakeObject(7, "interrupted", interrupt=True)],
    }
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)

    with pytest.raises(KeyboardInterrupt, match="synthetic interruption"):
        extractor.export_all(source, output)

    checkpoint_path = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["sources"]["Game_Data/first.assets"]["status"] == "completed"
    assert checkpoint["sources"]["Game_Data/second.assets"]["status"] == "processing"
    assert not (output / "all_resources_index.csv").exists()
    assert list((output / extractor.RESUME_STATE_DIR_NAME / "fragments").glob("*.partial"))

    PlannedUnityPy.calls = []
    PlannedUnityPy.plans["second.assets"] = [FakeObject(7, "second")]
    resumed_summary = extractor.export_all(source, output)

    assert PlannedUnityPy.calls == ["second.assets"]
    assert resumed_summary["resume"]["resumed"] is True
    assert resumed_summary["resume"]["skipped_source_count_this_run"] == 1
    assert resumed_summary["resume"]["retried_source_count_this_run"] == 1
    rows = read_index(output)
    assert len(rows) == 2
    assert len({row["asset_id"] for row in rows}) == 2
    assert {row["asset_name"] for row in rows} == {"first", "second"}
    assert existing_partial_asset.read_bytes() == b"preserve existing UnityPy output"

    PlannedUnityPy.calls = []
    idempotent_summary = extractor.export_all(source, output)
    assert PlannedUnityPy.calls == []
    assert idempotent_summary["resume"]["skipped_source_count_this_run"] == 2
    assert idempotent_summary["resume"]["retried_source_count_this_run"] == 0
    assert len(read_index(output)) == 2
    assert not list(output.rglob("*.tmp"))
    assert not list(output.rglob("*.partial"))


def test_load_error_is_logged_and_success_and_failure_paths_release_source_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path / "中文研究", "bad.assets", "good.assets")
    output = tmp_path / "中文导出"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {
        "bad.assets": OSError("synthetic load failure"),
        "good.assets": [FakeObject(12, "正常资源")],
    }
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    collect_calls: list[int] = []
    real_collect = extractor.gc.collect

    def tracking_collect() -> int:
        collect_calls.append(1)
        return real_collect()

    monkeypatch.setattr(extractor.gc, "collect", tracking_collect)

    summary = extractor.export_all(source, output)

    assert summary["completed_source_count"] == 1
    assert summary["failed_source_count"] == 1
    assert summary["failed_sources"] == ["Game_Data/bad.assets"]
    assert len(collect_calls) >= 2
    assert all(reference() is None for reference in PlannedUnityPy.env_refs)
    log_rows = {row["source"]: row for row in read_log(output)}
    assert log_rows["Game_Data/bad.assets"]["stage"] == "load"
    assert log_rows["Game_Data/bad.assets"]["status"] == "failed"
    assert "synthetic load failure" in log_rows["Game_Data/bad.assets"]["message"]
    checkpoint = json.loads(
        (output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert "中文研究" in checkpoint["identity"]["source_root"]
    assert "中文导出" in checkpoint["identity"]["export_root"]


def test_resume_rejects_changed_source_manifest_without_touching_final_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(1, "stable")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)
    original_index = (output / "all_resources_index.csv").read_bytes()

    (source / "Game_Data" / "only.assets").write_bytes(b"changed source bytes")
    with pytest.raises(extractor.ResumeStateError, match="source_list_fingerprint"):
        extractor.export_all(source, output)

    assert (output / "all_resources_index.csv").read_bytes() == original_index


def test_resume_rejects_output_affecting_option_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(1, "stable")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output, audio_mode="metadata")

    with pytest.raises(extractor.ResumeStateError, match="options"):
        extractor.export_all(source, output, audio_mode="samples")


def test_failed_source_is_retried_while_completed_source_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "bad.assets", "good.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {
        "bad.assets": OSError("first attempt fails"),
        "good.assets": [FakeObject(2, "already complete")],
    }
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)

    first = extractor.export_all(source, output)
    assert first["failed_sources"] == ["Game_Data/bad.assets"]
    assert PlannedUnityPy.calls == ["bad.assets", "good.assets"]

    PlannedUnityPy.calls = []
    PlannedUnityPy.plans["bad.assets"] = [FakeObject(1, "recovered")]
    second = extractor.export_all(source, output)

    assert PlannedUnityPy.calls == ["bad.assets"]
    assert second["failed_source_count"] == 0
    assert second["resume"]["skipped_source_count_this_run"] == 1
    assert second["resume"]["retried_source_count_this_run"] == 1
    rows = read_index(output)
    assert [row["asset_name"] for row in rows] == ["recovered", "already complete"]
    assert len({row["asset_id"] for row in rows}) == 2


def test_corrupt_completed_fragment_is_rejected_without_replacing_final_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(1, "stable")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)

    original_index = (output / "all_resources_index.csv").read_bytes()
    checkpoint = json.loads(
        (output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    record = checkpoint["sources"]["Game_Data/only.assets"]
    fragment = output / record["fragment"]
    fragment.write_text("corrupt fragment\n", encoding="utf-8")

    PlannedUnityPy.calls = []
    with pytest.raises(extractor.ResumeStateError, match="fragment hash mismatch"):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert (output / "all_resources_index.csv").read_bytes() == original_index


def test_requested_completed_source_is_atomically_reprocessed_while_others_are_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "first.assets", "second.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {
        "first.assets": [FakeObject(1, "first-v1")],
        "second.assets": [FakeObject(2, "second-v1")],
    }
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)

    checkpoint_path = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    before = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    second_before = dict(before["sources"]["Game_Data/second.assets"])
    PlannedUnityPy.calls = []
    PlannedUnityPy.plans["first.assets"] = [FakeObject(1, "first-v2")]

    summary = extractor.export_all(
        source,
        output,
        reprocess_sources=["Game_Data/first.assets"],
    )

    assert PlannedUnityPy.calls == ["first.assets"]
    assert summary["resume"]["requested_reprocess_sources"] == [
        "Game_Data/first.assets"
    ]
    assert summary["resume"]["requested_reprocess_source_count"] == 1
    assert summary["resume"]["reprocessed_sources"] == ["Game_Data/first.assets"]
    assert summary["resume"]["reprocessed_source_count_this_run"] == 1
    assert summary["resume"]["skipped_source_count_this_run"] == 1
    assert [row["asset_name"] for row in read_index(output)] == [
        "first-v2",
        "second-v1",
    ]

    after = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert after["sources"]["Game_Data/first.assets"]["attempts"] == 2
    assert after["sources"]["Game_Data/second.assets"] == second_before
    assert after["last_run"]["requested_reprocess_sources"] == [
        "Game_Data/first.assets"
    ]
    assert after["last_run"]["reprocessed_sources"] == ["Game_Data/first.assets"]

    PlannedUnityPy.calls = []
    PlannedUnityPy.plans["first.assets"] = [FakeObject(1, "first-v3")]
    repeated = extractor.export_all(
        source,
        output,
        reprocess_sources=["Game_Data/first.assets"],
    )

    assert PlannedUnityPy.calls == ["first.assets"]
    assert repeated["resume"]["reprocessed_sources"] == ["Game_Data/first.assets"]
    assert [row["asset_name"] for row in read_index(output)] == [
        "first-v3",
        "second-v1",
    ]
    repeated_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert repeated_checkpoint["protocol_version"] == extractor.RESUME_PROTOCOL_VERSION
    assert repeated_checkpoint["identity"] == before["identity"]
    assert repeated_checkpoint["sources"]["Game_Data/first.assets"]["attempts"] == 3


@pytest.mark.parametrize(
    "requested_source",
    ["", "   ", "Game_Data/not-discovered.assets"],
)
def test_invalid_reprocess_source_fails_without_mutating_checkpoint_or_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_source: str,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(1, "stable")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)

    checkpoint_path = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    checkpoint_before = checkpoint_path.read_bytes()
    fragment_bytes_before = {
        path.name: path.read_bytes()
        for path in (output / extractor.RESUME_STATE_DIR_NAME / "fragments").glob("*.csv")
    }
    index_before = (output / "all_resources_index.csv").read_bytes()
    PlannedUnityPy.calls = []

    with pytest.raises(extractor.ResumeStateError, match="reprocess source"):
        extractor.export_all(
            source,
            output,
            reprocess_sources=[requested_source],
        )

    assert PlannedUnityPy.calls == []
    assert checkpoint_path.read_bytes() == checkpoint_before
    assert {
        path.name: path.read_bytes()
        for path in (output / extractor.RESUME_STATE_DIR_NAME / "fragments").glob("*.csv")
    } == fragment_bytes_before
    assert (output / "all_resources_index.csv").read_bytes() == index_before


def test_main_forwards_repeatable_reprocess_source_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_export_all(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {}

    monkeypatch.setattr(extractor, "export_all", fake_export_all)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extract_all_readable_resources.py",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--reprocess-source",
            "Game_Data/first.assets",
            "--reprocess-source",
            "Game_Data/second.assets",
        ],
    )

    assert extractor.main() == 0
    assert captured["kwargs"]["reprocess_sources"] == [
        "Game_Data/first.assets",
        "Game_Data/second.assets",
    ]


@pytest.mark.parametrize(
    ("fixture_status", "duplicate_completed", "request_reprocess", "match"),
    [
        (None, False, False, "complete checkpoint"),
        ("processing", False, False, "every source must already be completed"),
        ("completed", True, False, "asset_id collision"),
        ("completed", True, True, "fresh protocol-2"),
    ],
)
def test_protocol_one_unsafe_work_is_rejected_before_any_persistent_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_status: str | None,
    duplicate_completed: bool,
    request_reprocess: bool,
    match: str,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    rows = [legacy_row(source_label, "legacy-one")]
    if duplicate_completed:
        rows.append(legacy_row(source_label, "legacy-one"))
    checkpoint_path, source_label = write_legacy_checkpoint_fixture(
        source,
        output,
        status=fixture_status,
        rows=rows,
    )
    before = output_file_bytes(output)
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)

    with pytest.raises(extractor.ResumeStateError, match=match):
        extractor.export_all(
            source,
            output,
            reprocess_sources=[source_label] if request_reprocess else None,
        )

    assert PlannedUnityPy.calls == []
    assert output_file_bytes(output) == before
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["resume_count"] == 0


def test_protocol_one_unique_completed_fragments_allow_pure_no_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)

    summary = extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert summary["resume"]["protocol_version"] == 1
    assert summary["resume"]["skipped_source_count_this_run"] == 1
    preflight = summary["resume"]["legacy_no_work_preflight"]
    assert preflight["mode"] == "validated_pure_no_work_only"
    assert preflight["globally_unique_asset_id_count"] == 1
    assert summary["resume"]["legacy_no_work_snapshot_binding"][
        "leased_fragment_count"
    ] == 1
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["resume_count"] == 1


def test_protocol_one_persists_committed_cleanup_warning_and_next_run_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)
    resume_before = json.loads(checkpoint_path.read_text(encoding="utf-8"))["resume_count"]
    real_unlink = extractor.OutputBoundary.unlink_expected
    injected = False

    def fail_one_committed_cleanup(
        boundary: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        nonlocal injected
        if not injected and path.name.startswith(".ga-group-retired-"):
            injected = True
            raise extractor.OutputBoundaryError("synthetic committed cleanup failure")
        real_unlink(boundary, path, expected)

    with monkeypatch.context() as cleanup_patch:
        cleanup_patch.setattr(
            extractor.OutputBoundary,
            "unlink_expected",
            fail_one_committed_cleanup,
        )
        with pytest.raises(extractor.GroupTransactionError) as error:
            extractor.export_all(source, output)

    assert injected is True
    assert error.value.transaction_state == "committed_cleanup_required"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["resume_count"] == resume_before + 1
    reference = checkpoint["last_run"]["group_transaction"]
    status_path = output / PurePosixPath(reference["status_path"])
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["schema"] == extractor.PROTOCOL_ONE_TRANSACTION_SCHEMA
    assert status["transaction_state"] == extractor.PROTOCOL_ONE_TRANSACTION_STATE
    assert {item["path"] for item in status["planned_target_contents"]} == {
        "summary.json",
        ".unitypy_resume/checkpoint.json",
    }
    assert error.value.cleanup_required_paths
    assert all(
        (output / PurePosixPath(path)).is_file()
        for path in error.value.cleanup_required_paths
    )

    recovered_summary = extractor.export_all(source, output)

    assert recovered_summary["resume"]["resume_count"] == resume_before + 2
    assert not list(output.rglob("*.partial"))
    latest_reference = recovered_summary["resume"]["group_transaction"]
    latest_status = json.loads(
        (output / PurePosixPath(latest_reference["status_path"])).read_text(encoding="utf-8")
    )
    assert latest_status["schema"] == extractor.PROTOCOL_ONE_TRANSACTION_SCHEMA
    assert latest_status["transaction_state"] == extractor.PROTOCOL_ONE_TRANSACTION_STATE
    assert not list(output.rglob("*.partial"))


@pytest.mark.parametrize(
    "aliased_path",
    [
        "metadata/Material/./payload.json",
        "metadata//Material/payload.json",
        "metadata\\Material\\payload.json",
        "metadata/Material/payload.json/",
        "metadata/Material./payload.json",
    ],
)
def test_protocol_one_rejects_noncanonical_physical_path_alias_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aliased_path: str,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[
            legacy_row(source_label, "legacy-one", export_path="metadata/Material/payload.json"),
            legacy_row(source_label, "legacy-two", export_path=aliased_path),
        ],
    )
    before = output_file_bytes(output)
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)

    with pytest.raises(extractor.ResumeStateError, match="[Nn]on-canonical physical export_path"):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert output_file_bytes(output) == before
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))["resume_count"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows leased snapshot contract")
@pytest.mark.parametrize("drift_target", ["fragment", "checkpoint"])
def test_protocol_one_leased_snapshot_blocks_post_preflight_drift_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_target: str,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    fragment_path = output / checkpoint["sources"][source_label]["fragment"]
    before = output_file_bytes(output)
    real_preflight = extractor.validate_legacy_protocol_one_no_work

    def preflight_then_attempt_drift(*args: Any, **kwargs: Any) -> Any:
        evidence = real_preflight(*args, **kwargs)
        target = fragment_path if drift_target == "fragment" else checkpoint_path
        target.write_bytes(b"drift must be denied while the snapshot is leased")
        return evidence

    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    monkeypatch.setattr(
        extractor,
        "validate_legacy_protocol_one_no_work",
        preflight_then_attempt_drift,
    )

    with pytest.raises(OSError):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert output_file_bytes(output) == before


def test_protocol_one_summary_checkpoint_group_commit_failure_restores_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)
    summary_path = output / "summary.json"
    protected_before = {
        checkpoint_path: checkpoint_path.read_bytes(),
        summary_path: summary_path.read_bytes(),
    }
    resume_before = json.loads(checkpoint_path.read_text(encoding="utf-8"))[
        "resume_count"
    ]
    real_rename_no_replace = extractor.OutputBoundary.rename_no_replace

    def fail_checkpoint_stage_after_summary_commit(
        boundary: extractor.OutputBoundary,
        temporary: Path,
        target: Path,
    ) -> None:
        if temporary.name.startswith(".ga-audio-stage-") and target.name == "checkpoint.json":
            raise extractor.OutputBoundaryError("synthetic checkpoint generation commit failure")
        real_rename_no_replace(boundary, temporary, target)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "rename_no_replace",
        fail_checkpoint_stage_after_summary_commit,
    )

    with pytest.raises(extractor.OutputBoundaryError, match="generation commit failure"):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert {path: path.read_bytes() for path in protected_before} == protected_before
    assert (
        json.loads(checkpoint_path.read_text(encoding="utf-8"))["resume_count"]
        == resume_before
    )
    assert not list(output.rglob("*.partial"))


def test_protocol_one_release_to_commit_gap_preserves_late_checkpoint_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permanent positive regression for T20AO's release/commit counterexample."""
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    summary_before = (output / "summary.json").read_bytes()
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    real_group_commit = extractor.write_export_file_group_if_changed
    late_generation = b'{"external_generation":true}\n'

    def inject_after_lease_release(
        plans: list[tuple[Path, bytes]],
        *,
        output_boundary: extractor.OutputBoundary,
        expected_generations: Any = None,
    ) -> list[str]:
        checkpoint_path.write_bytes(late_generation)
        return real_group_commit(
            plans,
            output_boundary=output_boundary,
            expected_generations=expected_generations,
        )

    monkeypatch.setattr(
        extractor,
        "write_export_file_group_if_changed",
        inject_after_lease_release,
    )

    with pytest.raises(extractor.ResumeStateError, match="target generation changed"):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    assert checkpoint_path.read_bytes() == late_generation
    assert (output / "summary.json").read_bytes() == summary_before


@pytest.mark.parametrize("late_kind", ["hardlink_same_bytes", "same_size", "aba"])
def test_protocol_one_conditional_checkpoint_generation_rejects_alias_and_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    late_kind: str,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    summary_before = (output / "summary.json").read_bytes()
    original = checkpoint_path.read_bytes()
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    real_group_commit = extractor.write_export_file_group_if_changed

    def inject_generation(
        plans: list[tuple[Path, bytes]],
        *,
        output_boundary: extractor.OutputBoundary,
        expected_generations: Any = None,
    ) -> list[str]:
        if late_kind == "hardlink_same_bytes":
            late = tmp_path / "late-hardlink-generation.json"
            late.write_bytes(original)
            checkpoint_path.unlink()
            os.link(late, checkpoint_path)
        elif late_kind == "same_size":
            checkpoint_path.write_bytes(b"X" * len(original))
        else:
            middle = tmp_path / "checkpoint-middle.json"
            restored = tmp_path / "checkpoint-restored.json"
            middle.write_bytes(b"Y" * len(original))
            restored.write_bytes(original)
            os.replace(middle, checkpoint_path)
            os.replace(restored, checkpoint_path)
        return real_group_commit(
            plans,
            output_boundary=output_boundary,
            expected_generations=expected_generations,
        )

    monkeypatch.setattr(
        extractor,
        "write_export_file_group_if_changed",
        inject_generation,
    )

    with pytest.raises(extractor.ResumeStateError, match="target generation changed"):
        extractor.export_all(source, output)

    assert PlannedUnityPy.calls == []
    if late_kind in {"hardlink_same_bytes", "aba"}:
        assert checkpoint_path.read_bytes() == original
    else:
        assert checkpoint_path.read_bytes() == b"X" * len(original)
    assert (output / "summary.json").read_bytes() == summary_before


def test_protocol_one_commit_interleaving_restores_summary_and_preserves_late_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)
    summary_path = output / "summary.json"
    summary_before = summary_path.read_bytes()
    late_generation = b'{"external_generation":"between_commits"}\n'
    real_rename_no_replace = extractor.OutputBoundary.rename_no_replace
    injected = False

    def inject_after_summary_publish(
        boundary: extractor.OutputBoundary,
        source_path: Path,
        target_path: Path,
    ) -> None:
        nonlocal injected
        real_rename_no_replace(boundary, source_path, target_path)
        if (
            not injected
            and source_path.name.startswith(".ga-audio-stage-")
            and target_path.name == "summary.json"
        ):
            checkpoint_path.write_bytes(late_generation)
            injected = True

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "rename_no_replace",
        inject_after_summary_publish,
    )

    with pytest.raises(extractor.ResumeStateError, match="target generation changed"):
        extractor.export_all(source, output)

    assert injected is True
    assert summary_path.read_bytes() == summary_before
    assert checkpoint_path.read_bytes() == late_generation
    assert not list(output.rglob("*.partial"))


def test_protocol_one_backup_move_rechecks_the_generation_it_actually_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a same-size change in the narrow precheck-to-backup-move window."""
    source = make_sources(tmp_path, "only.assets")
    output = tmp_path / "out"
    source_label = "Game_Data/only.assets"
    checkpoint_path, _ = write_legacy_checkpoint_fixture(
        source,
        output,
        status="completed",
        rows=[legacy_row(source_label, "legacy-unique")],
    )
    PlannedUnityPy.reset()
    PlannedUnityPy.plans = {"only.assets": [FakeObject(7, "must-not-load")]}
    monkeypatch.setattr(extractor, "UnityPy", PlannedUnityPy)
    extractor.export_all(source, output)
    summary_path = output / "summary.json"
    summary_before = summary_path.read_bytes()
    checkpoint_size = checkpoint_path.stat().st_size
    late_generation = b"Z" * checkpoint_size
    real_rename_no_replace = extractor.OutputBoundary.rename_no_replace
    injected = False

    def inject_before_checkpoint_backup_move(
        boundary: extractor.OutputBoundary,
        source_path: Path,
        target_path: Path,
    ) -> None:
        nonlocal injected
        if (
            not injected
            and source_path == checkpoint_path
            and target_path.name.startswith(".ga-audio-backup-")
        ):
            checkpoint_path.write_bytes(late_generation)
            injected = True
        real_rename_no_replace(boundary, source_path, target_path)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "rename_no_replace",
        inject_before_checkpoint_backup_move,
    )

    with pytest.raises(extractor.ResumeStateError, match="source generation changed"):
        extractor.export_all(source, output)

    assert injected is True
    assert summary_path.read_bytes() == summary_before
    assert checkpoint_path.read_bytes() == late_generation
    assert not list(output.rglob("*.partial"))

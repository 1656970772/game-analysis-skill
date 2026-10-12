from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor


def legacy_row(source_label: str) -> dict[str, str]:
    row = {field: "" for field in extractor.INDEX_FIELDS}
    row.update(
        {
            "asset_id": "legacy-unique",
            "source_file": source_label,
            "path_id": "7",
            "asset_type": "Material",
            "asset_name": "synthetic",
            "export_kind": "metadata",
            "status": "read_error",
        }
    )
    return row


def make_legacy_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "Game"
    data_dir = source / "Game_Data"
    data_dir.mkdir(parents=True)
    (data_dir / "only.assets").write_bytes(b"synthetic")
    output = tmp_path / "out"
    boundary = extractor.OutputBoundary(output, source).initialize()
    source_boundary = extractor.SourceBoundary(source).initialize()
    authorized = source_boundary.discover_files()
    discovered = extractor.discover_unity_sources(
        source,
        source_boundary=source_boundary,
        authorized_files=authorized,
    )
    source_label = extractor.rel(discovered[0], source)
    identity = extractor.checkpoint_identity(
        source,
        output,
        "metadata",
        extractor.manifest_entries(discovered, source),
        extractor.manifest_entries([], source),
        extractor.manifest_entries([], source),
        protocol_version=extractor.LEGACY_RESUME_PROTOCOL_VERSION,
    )
    fragment_rel = (
        f"{extractor.RESUME_STATE_DIR_NAME}/fragments/"
        f"{extractor.source_fragment_name(0, source_label)}"
    )
    fragment = output / PurePosixPath(fragment_rel)
    fragment.parent.mkdir(parents=True)
    with fragment.open("w", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=extractor.INDEX_FIELDS).writerow(
            legacy_row(source_label)
        )
    checkpoint = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    extractor.atomic_write_json(
        checkpoint,
        {
            "protocol_version": extractor.LEGACY_RESUME_PROTOCOL_VERSION,
            "identity": identity,
            "created_at": "synthetic",
            "updated_at": "synthetic",
            "status": "complete",
            "resume_count": 0,
            "sources": {
                source_label: {
                    "status": "completed",
                    "stage": "export",
                    "attempts": 1,
                    "fragment": fragment_rel,
                    "fragment_sha256": extractor.sha256_file(fragment),
                    "row_count": 1,
                    "type_counts": {"Material": 1},
                    "exported_counts": {},
                }
            },
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
    return source, output, checkpoint


def make_v2_recovery_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, extractor.OutputBoundary]:
    output = tmp_path / "recovery"
    transaction_id = "a" * 32
    status_relative = (
        f"{extractor.RESUME_STATE_DIR_NAME}/group_transactions/{transaction_id}.json"
    )
    reference = {
        "transaction_id": transaction_id,
        "status_path": status_relative,
        "status_sidecar_authoritative": True,
    }
    checkpoint = output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    summary = output / "summary.json"
    checkpoint.parent.mkdir(parents=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_payload = (
        json.dumps({"last_run": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    summary_payload = (
        json.dumps({"resume": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    checkpoint.write_bytes(checkpoint_payload)
    summary.write_bytes(summary_payload)
    record = {
        "schema": extractor.PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA,
        "transaction_id": transaction_id,
        "transaction_state": extractor.PROTOCOL_ONE_TRANSACTION_STATE,
        "protocol_version": extractor.LEGACY_RESUME_PROTOCOL_VERSION,
        "recorded_at": "synthetic",
        "planned_target_contents": [
            {
                "path": "summary.json",
                "byte_size": len(summary_payload),
                "sha256": hashlib.sha256(summary_payload).hexdigest(),
            },
            {
                "path": f"{extractor.RESUME_STATE_DIR_NAME}/checkpoint.json",
                "byte_size": len(checkpoint_payload),
                "sha256": hashlib.sha256(checkpoint_payload).hexdigest(),
            },
        ],
        "cleanup_ownership": {
            "token": transaction_id,
            "exact_filename_grammar": "legacy-v2",
        },
    }
    status = output / PurePosixPath(status_relative)
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    partial = output / f".ga-group-retired-{transaction_id}-000001.partial"
    partial.write_bytes(b"owned recovery generation")
    return (
        output,
        checkpoint,
        summary,
        partial,
        extractor.OutputBoundary(output).initialize(),
    )


def write_legacy_v2_head(output: Path, checkpoint: Path, transaction_id: str) -> Path:
    summary = output / "summary.json"
    status_relative = (
        f"{extractor.RESUME_STATE_DIR_NAME}/group_transactions/{transaction_id}.json"
    )
    reference = {
        "transaction_id": transaction_id,
        "status_path": status_relative,
        "status_sidecar_authoritative": True,
    }
    checkpoint_document = json.loads(checkpoint.read_text(encoding="utf-8"))
    summary_document = json.loads(summary.read_text(encoding="utf-8"))
    checkpoint_document["last_run"] = {"group_transaction": reference}
    summary_document.setdefault("resume", {})["group_transaction"] = reference
    checkpoint_payload = (
        json.dumps(checkpoint_document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    summary_payload = (
        json.dumps(summary_document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    checkpoint.write_bytes(checkpoint_payload)
    summary.write_bytes(summary_payload)
    record = {
        "schema": extractor.PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA,
        "transaction_id": transaction_id,
        "transaction_state": extractor.PROTOCOL_ONE_TRANSACTION_STATE,
        "protocol_version": extractor.LEGACY_RESUME_PROTOCOL_VERSION,
        "recorded_at": "synthetic-legacy-boundary",
        "planned_target_contents": [
            {
                "path": "summary.json",
                "byte_size": len(summary_payload),
                "sha256": hashlib.sha256(summary_payload).hexdigest(),
            },
            {
                "path": f"{extractor.RESUME_STATE_DIR_NAME}/checkpoint.json",
                "byte_size": len(checkpoint_payload),
                "sha256": hashlib.sha256(checkpoint_payload).hexdigest(),
            },
        ],
        "cleanup_ownership": {
            "token": transaction_id,
            "exact_filename_grammar": "legacy-v2-singleton",
        },
    }
    status = output / PurePosixPath(status_relative)
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status


def write_interrupted_v3_tail(
    output: Path,
    history: extractor.ProtocolOneTransactionHistory,
    transaction_id: str,
) -> Path:
    status = (
        output
        / extractor.RESUME_STATE_DIR_NAME
        / "group_transactions"
        / f"{transaction_id}.json"
    )
    link = extractor.protocol_one_history_link(
        transaction_id,
        history.anchor,
        history.head_sidecar_sha256,
    )
    reference = extractor.protocol_one_transaction_reference(
        transaction_id,
        status,
        output,
        link,
    )
    summary_payload = (
        json.dumps({"resume": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    checkpoint_payload = (
        json.dumps({"last_run": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    previous = {
        output / "summary.json": extractor.capture_expected_file_generation(
            output / "summary.json"
        ),
        output
        / extractor.RESUME_STATE_DIR_NAME
        / "checkpoint.json": extractor.capture_expected_file_generation(
            output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
        ),
    }
    record = extractor.protocol_one_transaction_record(
        transaction_id,
        [
            (output / "summary.json", summary_payload),
            (
                output / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json",
                checkpoint_payload,
            ),
        ],
        output,
        history_link=link,
        previous_generations=previous,
    )
    status.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status


def test_sidecar_failure_happens_before_control_commit_and_next_run_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, checkpoint = make_legacy_fixture(tmp_path)
    before = checkpoint.read_bytes()
    summary_before = (output / "summary.json").read_bytes()

    with monkeypatch.context() as patch:
        patch.setattr(
            extractor,
            "write_group_transaction_status_no_replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                extractor.OutputBoundaryError("synthetic sidecar failure")
            ),
        )
        with pytest.raises(extractor.OutputBoundaryError, match="sidecar failure"):
            extractor.export_all(source, output)

    assert checkpoint.read_bytes() == before
    assert (output / "summary.json").read_bytes() == summary_before
    completed = extractor.export_all(source, output)
    assert completed["resume"]["resume_count"] == 1
    reference = completed["resume"]["group_transaction"]
    assert (output / PurePosixPath(reference["status_path"])).is_file()


def test_interrupted_sidecar_stage_is_verified_and_cleaned_before_a_later_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, checkpoint = make_legacy_fixture(tmp_path)
    before = checkpoint.read_bytes()
    real_rename = extractor.OutputBoundary.rename_no_replace
    real_unlink = extractor.OutputBoundary.unlink_expected

    def fail_sidecar_rename(
        boundary: extractor.OutputBoundary,
        source_path: Path,
        target_path: Path,
    ) -> None:
        if target_path.parent.name == "group_transactions":
            raise extractor.OutputBoundaryError("synthetic publish failure")
        real_rename(boundary, source_path, target_path)

    def preserve_sidecar_stage(
        boundary: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        if path.name.startswith(".ga-group-status-"):
            raise extractor.OutputBoundaryError("synthetic stage cleanup failure")
        real_unlink(boundary, path, expected)

    with monkeypatch.context() as patch:
        patch.setattr(extractor.OutputBoundary, "rename_no_replace", fail_sidecar_rename)
        patch.setattr(extractor.OutputBoundary, "unlink_expected", preserve_sidecar_stage)
        with pytest.raises(extractor.GroupTransactionError) as error:
            extractor.export_all(source, output)
    assert error.value.transaction_state == "sidecar_publication_incomplete"
    assert checkpoint.read_bytes() == before
    assert list((output / extractor.RESUME_STATE_DIR_NAME / "group_transactions").glob("*.partial"))

    completed = extractor.export_all(source, output)
    assert completed["resume"]["resume_count"] == 1
    history = extractor.reconcile_protocol_one_transaction_records(
        output,
        output_boundary=extractor.OutputBoundary(output).initialize(),
    )
    assert len(history) == 1
    assert not list(output.rglob("*.partial"))


def test_cleanup_rename_success_then_wrapper_error_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, _ = make_legacy_fixture(tmp_path)
    real_rename = extractor.OutputBoundary.rename_no_replace
    injected = False

    def rename_then_raise(
        boundary: extractor.OutputBoundary,
        source_path: Path,
        target_path: Path,
    ) -> None:
        nonlocal injected
        real_rename(boundary, source_path, target_path)
        if (
            not injected
            and source_path.name.startswith(".ga-audio-backup-")
            and target_path.name.startswith(".ga-group-retired-")
        ):
            injected = True
            raise extractor.OutputBoundaryError("synthetic wrapper failure")

    monkeypatch.setattr(extractor.OutputBoundary, "rename_no_replace", rename_then_raise)
    completed = extractor.export_all(source, output)
    assert completed["resume"]["resume_count"] == 1
    assert injected is True
    assert not list(output.rglob("*.partial"))


def test_sidecar_publish_success_then_wrapper_error_still_precedes_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, _ = make_legacy_fixture(tmp_path)
    real_rename = extractor.OutputBoundary.rename_no_replace
    injected = False

    def publish_then_raise(
        boundary: extractor.OutputBoundary,
        source_path: Path,
        target_path: Path,
    ) -> None:
        nonlocal injected
        real_rename(boundary, source_path, target_path)
        if not injected and target_path.parent.name == "group_transactions":
            injected = True
            raise extractor.OutputBoundaryError("synthetic sidecar wrapper failure")

    monkeypatch.setattr(extractor.OutputBoundary, "rename_no_replace", publish_then_raise)
    completed = extractor.export_all(source, output)
    reference = completed["resume"]["group_transaction"]
    assert injected is True
    assert (output / PurePosixPath(reference["status_path"])).is_file()
    assert not list(output.rglob("*.partial"))


def test_sidecar_generation_is_leased_through_control_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, _ = make_legacy_fixture(tmp_path)
    real_group_commit = extractor.write_export_file_group_if_changed
    attempted = False

    def try_to_tamper_sidecar(
        plans: list[tuple[Path, bytes]],
        *,
        output_boundary: extractor.OutputBoundary,
        expected_generations: Any = None,
        transaction_token: str | None = None,
    ) -> extractor.GroupCommitResult:
        nonlocal attempted
        status_files = list(
            (output / extractor.RESUME_STATE_DIR_NAME / "group_transactions").glob("*.json")
        )
        assert len(status_files) == 1
        attempted = True
        if os.name == "nt":
            with pytest.raises(PermissionError):
                status_files[0].write_bytes(b"tampered")
        return real_group_commit(
            plans,
            output_boundary=output_boundary,
            expected_generations=expected_generations,
            transaction_token=transaction_token,
        )

    monkeypatch.setattr(
        extractor,
        "write_export_file_group_if_changed",
        try_to_tamper_sidecar,
    )
    completed = extractor.export_all(source, output)
    assert attempted is True
    reference = completed["resume"]["group_transaction"]
    status = json.loads(
        (output / PurePosixPath(reference["status_path"])).read_text(encoding="utf-8")
    )
    assert status["schema"] == extractor.PROTOCOL_ONE_TRANSACTION_SCHEMA


@pytest.mark.parametrize("target_index", [0, 1])
def test_control_publish_success_then_wrapper_error_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_index: int,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    targets = [tmp_path / "first.bin", tmp_path / "second.bin"]
    for target in targets:
        target.write_bytes(b"old")
    real_rename = extractor.OutputBoundary.rename_no_replace
    injected = False

    def rename_then_raise(
        current: extractor.OutputBoundary,
        source: Path,
        target: Path,
    ) -> None:
        nonlocal injected
        real_rename(current, source, target)
        if (
            not injected
            and source.name.startswith(".ga-audio-stage-")
            and target == targets[target_index]
        ):
            injected = True
            raise extractor.OutputBoundaryError("synthetic publish wrapper failure")

    monkeypatch.setattr(extractor.OutputBoundary, "rename_no_replace", rename_then_raise)
    result = extractor.write_export_file_group_if_changed(
        [(targets[0], b"first-new"), (targets[1], b"second-new")],
        output_boundary=boundary,
    )
    try:
        assert injected is True
        assert [target.read_bytes() for target in targets] == [b"first-new", b"second-new"]
    finally:
        result.close()
    assert not list(tmp_path.rglob("*.partial"))


def test_rollback_restore_success_then_wrapper_error_uses_actual_kernel_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first-old")
    second.write_bytes(b"second-old")
    real_rename = extractor.OutputBoundary.rename_no_replace
    blocked_second = False
    restored_then_raised = False

    def inject(
        current: extractor.OutputBoundary,
        source: Path,
        target: Path,
    ) -> None:
        nonlocal blocked_second, restored_then_raised
        if (
            not blocked_second
            and source.name.startswith(".ga-audio-stage-")
            and target == second
        ):
            blocked_second = True
            raise extractor.OutputBoundaryError("synthetic second publish failure")
        real_rename(current, source, target)
        if (
            blocked_second
            and not restored_then_raised
            and source.name.startswith(".ga-audio-backup-")
            and target == first
        ):
            restored_then_raised = True
            raise extractor.OutputBoundaryError("synthetic restore wrapper failure")

    monkeypatch.setattr(extractor.OutputBoundary, "rename_no_replace", inject)
    with pytest.raises(extractor.OutputBoundaryError, match="second publish failure"):
        extractor.write_export_file_group_if_changed(
            [(first, b"first-new"), (second, b"second-new")],
            output_boundary=boundary,
        )
    assert blocked_second is True
    assert restored_then_raised is True
    assert first.read_bytes() == b"first-old"
    assert second.read_bytes() == b"second-old"
    assert not list(tmp_path.rglob("*.partial"))


def test_cleanup_unlink_success_then_wrapper_error_does_not_report_missing_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, checkpoint, _, partial, boundary = make_v2_recovery_fixture(tmp_path)
    real_unlink = extractor.OutputBoundary.unlink_expected

    def unlink_then_raise(
        current: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        real_unlink(current, path, expected)
        raise extractor.OutputBoundaryError("synthetic wrapper failure after exact delete")

    monkeypatch.setattr(extractor.OutputBoundary, "unlink_expected", unlink_then_raise)
    recovered = extractor.recover_protocol_one_group_cleanup(
        output,
        checkpoint,
        output_boundary=boundary,
    )
    try:
        assert recovered is not None
        assert recovered["cleanup_required_paths"] == []
        assert not partial.exists()
    finally:
        assert recovered is not None
        recovered.close()


def test_group_result_holds_exact_target_until_explicit_handoff_close(
    tmp_path: Path,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    target = tmp_path / "target.bin"
    target.write_bytes(b"old")
    result = extractor.write_export_file_group_if_changed(
        [(target, b"new")],
        output_boundary=boundary,
    )
    try:
        result.verify_handoff()
        if os.name == "nt":
            with pytest.raises(PermissionError):
                target.write_bytes(b"external")
        assert target.read_bytes() == b"new"
    finally:
        result.close()
    target.write_bytes(b"after handoff")
    assert target.read_bytes() == b"after handoff"


def test_recovery_target_drift_is_blocked_for_entire_cleanup_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, checkpoint, summary, partial, boundary = make_v2_recovery_fixture(tmp_path)
    real_unlink = extractor.OutputBoundary.unlink_expected
    attempted = False

    def mutate_target_then_unlink(
        current: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        nonlocal attempted
        attempted = True
        summary.write_bytes(b'{"external":true}')
        real_unlink(current, path, expected)

    monkeypatch.setattr(extractor.OutputBoundary, "unlink_expected", mutate_target_then_unlink)
    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.recover_protocol_one_group_cleanup(
            output,
            checkpoint,
            output_boundary=boundary,
        )
    assert attempted is True
    assert error.value.transaction_state == "committed_cleanup_required"
    assert partial.is_file()
    assert error.value.recovery_files == [partial.relative_to(output).as_posix()]


def test_recovery_refuses_a_late_partial_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, checkpoint, _, partial, boundary = make_v2_recovery_fixture(tmp_path)
    real_unlink = extractor.OutputBoundary.unlink_expected
    external = b"late external generation"

    def replace_before_exact_delete(
        current: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        replacement = path.with_name("replacement.bin")
        replacement.write_bytes(external)
        os.replace(replacement, path)
        real_unlink(current, path, expected)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "unlink_expected",
        replace_before_exact_delete,
    )
    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.recover_protocol_one_group_cleanup(
            output,
            checkpoint,
            output_boundary=boundary,
        )
    assert partial.read_bytes() == external
    assert error.value.recovery_files == [partial.relative_to(output).as_posix()]


def test_recovery_second_delete_failure_reports_only_real_remaining_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, checkpoint, _, first, boundary = make_v2_recovery_fixture(tmp_path)
    transaction_id = "a" * 32
    second = output / f".ga-audio-backup-{transaction_id}-000002.partial"
    second.write_bytes(b"second recovery generation")
    real_unlink = extractor.OutputBoundary.unlink_expected

    def fail_second(
        current: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        if path == second:
            raise extractor.OutputBoundaryError("synthetic second exact-delete failure")
        real_unlink(current, path, expected)

    monkeypatch.setattr(extractor.OutputBoundary, "unlink_expected", fail_second)
    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.recover_protocol_one_group_cleanup(
            output,
            checkpoint,
            output_boundary=boundary,
        )
    assert not first.exists()
    assert second.is_file()
    assert error.value.cleanup_required_paths == [second.relative_to(output).as_posix()]
    assert error.value.recovery_files == [second.relative_to(output).as_posix()]


def test_post_cleanup_evidence_callback_cannot_mutate_leased_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    target = tmp_path / "reuse.bin"
    target.write_bytes(b"stable")
    real_evidence = extractor.expected_generation_evidence
    attempted = False

    def mutate_during_evidence(
        path: Path,
        generation: extractor.ExpectedFileGeneration,
        root: Path,
    ) -> dict[str, Any]:
        nonlocal attempted
        if path == target:
            attempted = True
            path.write_bytes(b"drift")
        return real_evidence(path, generation, root)

    monkeypatch.setattr(extractor, "expected_generation_evidence", mutate_during_evidence)
    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.write_export_file_group_if_changed(
            [(target, b"stable")],
            output_boundary=boundary,
        )
    assert attempted is True
    assert error.value.transaction_state == "committed_target_drift"
    assert target.read_bytes() == b"stable"


def test_transaction_record_plans_both_control_payloads_without_a_hash_cycle(
    tmp_path: Path,
) -> None:
    transaction_id = "b" * 32
    summary = tmp_path / "summary.json"
    checkpoint = tmp_path / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    history_link = extractor.protocol_one_history_link(transaction_id, None, None)
    reference = extractor.protocol_one_transaction_reference(
        transaction_id,
        tmp_path
        / extractor.RESUME_STATE_DIR_NAME
        / "group_transactions"
        / f"{transaction_id}.json",
        tmp_path,
        history_link,
    )
    summary_payload = (
        json.dumps({"resume": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    checkpoint_payload = (
        json.dumps({"last_run": {"group_transaction": reference}}, indent=2) + "\n"
    ).encode("utf-8")
    plans = [(summary, summary_payload), (checkpoint, checkpoint_payload)]
    record = extractor.protocol_one_transaction_record(
        transaction_id,
        plans,
        tmp_path,
        history_link=history_link,
    )
    assert record["transaction_state"] == extractor.PROTOCOL_ONE_TRANSACTION_STATE
    assert record["planned_target_contents"] == [
        {
            "path": "summary.json",
            "byte_size": len(summary_payload),
            "sha256": hashlib.sha256(summary_payload).hexdigest(),
        },
        {
            "path": f"{extractor.RESUME_STATE_DIR_NAME}/checkpoint.json",
            "byte_size": len(checkpoint_payload),
            "sha256": hashlib.sha256(checkpoint_payload).hexdigest(),
        },
    ]
    assert record["history"] == history_link
    assert record["planned_control_anchor"] == {
        "summary": reference,
        "checkpoint": reference,
    }


def test_history_genesis_then_append_binds_previous_sidecar_digest(
    tmp_path: Path,
) -> None:
    source, output, _ = make_legacy_fixture(tmp_path)
    first = extractor.export_all(source, output)
    first_reference = first["resume"]["group_transaction"]
    first_path = output / PurePosixPath(first_reference["status_path"])
    first_sha256 = extractor.sha256_file(first_path)
    first_record = json.loads(first_path.read_text(encoding="utf-8"))
    assert first_record["history"]["previous_count"] == 0
    assert first_record["history"]["previous_head_transaction_id"] is None
    assert first_reference["history"] == extractor.protocol_one_history_anchor_from_link(
        first_record["history"]
    )

    second = extractor.export_all(source, output)
    second_reference = second["resume"]["group_transaction"]
    second_record = json.loads(
        (output / PurePosixPath(second_reference["status_path"])).read_text(
            encoding="utf-8"
        )
    )
    assert second_record["history"]["previous_head_transaction_id"] == first_reference[
        "transaction_id"
    ]
    assert second_record["history"]["previous_sidecar_sha256"] == first_sha256
    assert second_record["history"]["previous_count"] == 1
    assert second_reference["history"]["count"] == 2
    history = extractor.reconcile_protocol_one_transaction_records(
        output,
        output_boundary=extractor.OutputBoundary(output).initialize(),
    )
    assert len(history) == 2


def test_control_commit_failure_leaves_one_strict_tail_that_next_run_cleans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output, checkpoint = make_legacy_fixture(tmp_path)
    before_checkpoint = checkpoint.read_bytes()
    before_summary = (output / "summary.json").read_bytes()

    with monkeypatch.context() as patch:
        patch.setattr(
            extractor,
            "write_export_file_group_if_changed",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                extractor.OutputBoundaryError("synthetic control commit failure")
            ),
        )
        with pytest.raises(extractor.OutputBoundaryError, match="control commit failure"):
            extractor.export_all(source, output)

    tails = list(
        (output / extractor.RESUME_STATE_DIR_NAME / "group_transactions").glob("*.json")
    )
    assert len(tails) == 1
    interrupted = tails[0]
    assert checkpoint.read_bytes() == before_checkpoint
    assert (output / "summary.json").read_bytes() == before_summary

    completed = extractor.export_all(source, output)
    assert not interrupted.exists()
    reference = completed["resume"]["group_transaction"]
    assert reference["history"]["count"] == 1
    assert len(list(interrupted.parent.glob("*.json"))) == 1


def test_reconcile_rejects_two_strict_interrupted_tails_as_a_fork(
    tmp_path: Path,
) -> None:
    source, output, _ = make_legacy_fixture(tmp_path)
    extractor.export_all(source, output)
    boundary = extractor.OutputBoundary(output).initialize()
    history = extractor.reconcile_protocol_one_transaction_records(
        output,
        output_boundary=boundary,
    )
    write_interrupted_v3_tail(output, history, "c" * 32)
    write_interrupted_v3_tail(output, history, "d" * 32)

    with pytest.raises(extractor.ResumeStateError, match="extra/forked"):
        extractor.reconcile_protocol_one_transaction_records(
            output,
            output_boundary=boundary,
        )


def test_single_anchored_legacy_v2_boundary_migrates_but_unreferenced_v2_rejects(
    tmp_path: Path,
) -> None:
    source, output, checkpoint = make_legacy_fixture(tmp_path)
    legacy = write_legacy_v2_head(output, checkpoint, "e" * 32)
    completed = extractor.export_all(source, output)
    current = completed["resume"]["group_transaction"]
    assert current["history"]["count"] == 2
    history = extractor.reconcile_protocol_one_transaction_records(
        output,
        output_boundary=extractor.OutputBoundary(output).initialize(),
    )
    assert list(history)[0] == legacy
    assert len(history) == 2

    other_source, other_output, _ = make_legacy_fixture(tmp_path / "other")
    del other_source
    orphan = other_output / extractor.RESUME_STATE_DIR_NAME / "group_transactions"
    orphan.mkdir(parents=True)
    orphan.joinpath(f"{'f' * 32}.json").write_text(
        legacy.read_text(encoding="utf-8").replace("e" * 32, "f" * 32),
        encoding="utf-8",
    )
    with pytest.raises(extractor.ResumeStateError, match="unanchored extra"):
        extractor.reconcile_protocol_one_transaction_records(
            other_output,
            output_boundary=extractor.OutputBoundary(other_output).initialize(),
        )

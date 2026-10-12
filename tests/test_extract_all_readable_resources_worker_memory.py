from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


def memory_sample(
    creation_time_100ns: int,
    working_set_bytes: int,
    private_bytes: int,
) -> dict[str, int]:
    return {
        "creation_time_100ns": creation_time_100ns,
        "working_set_bytes": working_set_bytes,
        "private_bytes": private_bytes,
    }


def install_fake_windows_process_api(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
) -> None:
    def query(pid: int) -> dict[str, int] | None:
        sample = state["samples"].get(pid)
        return dict(sample) if sample is not None else None

    def snapshot() -> list[dict[str, Any]] | None:
        value = state.get("snapshot")
        return [dict(entry) for entry in value] if value is not None else None

    monkeypatch.setattr(extractor, "_sample_windows_process_memory", query)
    monkeypatch.setattr(extractor, "_windows_process_snapshot", snapshot)


def process_record(evidence: dict[str, Any], pid: int) -> dict[str, Any]:
    return next(record for record in evidence["observed_processes"] if record["pid"] == pid)


def build_multilevel_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    state = {
        "samples": {
            801: memory_sample(8_000, 8, 10),
            802: memory_sample(8_100, 80, 100),
            803: memory_sample(8_100, 800, 1_000),
        },
        "snapshot": [
            {"pid": 801, "parent_pid": 400, "image_name": "launcher.exe"},
            {"pid": 802, "parent_pid": 801, "image_name": "python.exe"},
            {"pid": 803, "parent_pid": 802, "image_name": "worker.exe"},
        ],
    }
    install_fake_windows_process_api(monkeypatch, state)
    sampler = extractor.WindowsProcessTreeMemorySampler(801)
    sampler.sample()
    return sampler.evidence()


def test_direct_worker_uses_verified_root_identity_and_single_process_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "samples": {101: memory_sample(1_000, 120, 150)},
        "snapshot": [
            {"pid": 101, "parent_pid": 50, "image_name": "python.exe"},
        ],
    }
    install_fake_windows_process_api(monkeypatch, state)

    sampler = extractor.WindowsProcessTreeMemorySampler(101)
    sampler.sample()
    evidence = sampler.evidence()

    assert evidence["memory_method"] == extractor.WINDOWS_PROCESS_TREE_MEMORY_METHOD
    assert evidence["memory_peak_semantics"] == (
        "max_observed_single_process_in_launch_tree_not_sum"
    )
    assert evidence["observed_process_count"] == 1
    assert evidence["peak_working_set_bytes"] == 120
    assert evidence["private_bytes"] == 150
    assert evidence["peak_private_process"] == {
        "pid": 101,
        "creation_time_100ns": 1_000,
    }
    assert process_record(evidence, 101)["role"] == "launch_root"
    assert process_record(evidence, 101)["parent_pid"] is None
    assert process_record(evidence, 101)["parent_creation_time_100ns"] is None
    assert extractor.validate_worker_memory_evidence(evidence) is evidence


def test_launcher_child_peak_is_real_interpreter_max_not_tree_sum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "samples": {301: memory_sample(3_000, 4, 5)},
        "snapshot": [],
    }
    install_fake_windows_process_api(monkeypatch, state)
    sampler = extractor.WindowsProcessTreeMemorySampler(301)

    state["samples"] = {
        301: memory_sample(3_000, 10, 12),
        302: memory_sample(3_100, 600, 700),
    }
    state["snapshot"] = [
        {"pid": 301, "parent_pid": 90, "image_name": "python.exe"},
        {"pid": 302, "parent_pid": 301, "image_name": "python.exe"},
    ]
    sampler.sample()
    evidence = sampler.evidence()

    assert evidence["observed_process_count"] == 2
    assert evidence["peak_working_set_bytes"] == 600
    assert evidence["private_bytes"] == 700
    assert evidence["private_bytes"] != 12 + 700
    assert evidence["peak_private_process"]["pid"] == 302
    child = process_record(evidence, 302)
    assert child["parent_pid"] == 301
    assert child["parent_creation_time_100ns"] == 3_000
    assert child["role"] == "descendant"
    extractor.validate_worker_memory_evidence(evidence)


def test_multilevel_tree_persists_exact_parent_identities_and_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = build_multilevel_evidence(monkeypatch)

    assert evidence["observed_process_count"] == 3
    assert process_record(evidence, 801)["parent_pid"] is None
    assert process_record(evidence, 801)["parent_creation_time_100ns"] is None
    assert (
        process_record(evidence, 802)["parent_pid"],
        process_record(evidence, 802)["parent_creation_time_100ns"],
    ) == (801, 8_000)
    assert (
        process_record(evidence, 803)["parent_pid"],
        process_record(evidence, 803)["parent_creation_time_100ns"],
    ) == (802, 8_100)
    assert extractor.validate_worker_memory_evidence(evidence) is evidence


def test_validator_rejects_disconnected_descendant_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = copy.deepcopy(build_multilevel_evidence(monkeypatch))
    child = process_record(evidence, 802)
    grandchild = process_record(evidence, 803)
    child["parent_pid"] = 803
    child["parent_creation_time_100ns"] = 8_100
    grandchild["parent_pid"] = 802
    grandchild["parent_creation_time_100ns"] = 8_100

    with pytest.raises(extractor.LargeBundleWorkerError, match="contains a cycle"):
        extractor.validate_worker_memory_evidence(evidence)


def test_validator_rejects_wrong_or_future_parent_creation_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_parent = copy.deepcopy(build_multilevel_evidence(monkeypatch))
    process_record(wrong_parent, 802)["parent_creation_time_100ns"] = 8_001
    with pytest.raises(extractor.LargeBundleWorkerError, match="parent identity is absent"):
        extractor.validate_worker_memory_evidence(wrong_parent)

    future_parent = copy.deepcopy(build_multilevel_evidence(monkeypatch))
    child = process_record(future_parent, 802)
    grandchild = process_record(future_parent, 803)
    grandchild["creation_time_100ns"] = 8_200
    child["parent_pid"] = 803
    child["parent_creation_time_100ns"] = 8_200
    with pytest.raises(extractor.LargeBundleWorkerError, match="created before"):
        extractor.validate_worker_memory_evidence(future_parent)


def test_validator_rejects_reused_parent_pid_identity_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = copy.deepcopy(build_multilevel_evidence(monkeypatch))
    reused_parent = copy.deepcopy(process_record(evidence, 802))
    reused_parent["creation_time_100ns"] = 8_500
    reused_parent["parent_pid"] = 801
    reused_parent["parent_creation_time_100ns"] = 8_000
    evidence["observed_processes"].append(reused_parent)
    evidence["observed_process_count"] += 1
    evidence["successful_process_sample_count"] += reused_parent["sample_count"]
    process_record(evidence, 803)["parent_creation_time_100ns"] = 8_500

    with pytest.raises(extractor.LargeBundleWorkerError, match="ambiguous reused PID"):
        extractor.validate_worker_memory_evidence(evidence)


def test_late_child_exit_retains_peak_and_reused_pid_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "samples": {401: memory_sample(4_000, 20, 25)},
        "snapshot": [
            {"pid": 401, "parent_pid": 100, "image_name": "launcher.exe"},
        ],
    }
    install_fake_windows_process_api(monkeypatch, state)
    sampler = extractor.WindowsProcessTreeMemorySampler(401)
    sampler.sample()

    # The actual interpreter starts after the first poll.
    state["samples"][402] = memory_sample(4_100, 800, 900)
    state["snapshot"].append(
        {"pid": 402, "parent_pid": 401, "image_name": "python.exe"}
    )
    sampler.sample()

    # It exits before the next poll. A later unrelated process reuses PID 402;
    # creation time prevents its memory from replacing the worker's peak.
    state["samples"][402] = memory_sample(9_000, 9_999, 9_999)
    state["snapshot"] = [
        {"pid": 401, "parent_pid": 100, "image_name": "launcher.exe"},
        # Even if a reused PID appears under the same live launcher, it cannot
        # become a second persisted identity in this launch-tree evidence.
        {"pid": 402, "parent_pid": 401, "image_name": "replacement.exe"},
    ]
    sampler.sample()
    evidence = sampler.evidence()

    assert evidence["private_bytes"] == 900
    assert evidence["peak_private_process"]["pid"] == 402
    assert process_record(evidence, 402)["creation_time_100ns"] == 4_100
    assert evidence["pid_reuse_rejection_count"] == 1
    extractor.validate_worker_memory_evidence(evidence)


def test_sampling_failures_are_explicit_and_claimed_or_legacy_peaks_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"samples": {}, "snapshot": None}
    install_fake_windows_process_api(monkeypatch, state)

    unavailable = extractor.WindowsProcessTreeMemorySampler(501).evidence()

    assert unavailable["memory_method"] == "unavailable"
    assert unavailable["root_identity_verified"] is False
    assert unavailable["process_query_failure_count"] == 1
    extractor.validate_worker_memory_evidence(unavailable)

    claimed_peak = dict(unavailable)
    claimed_peak["private_bytes"] = 1
    with pytest.raises(extractor.LargeBundleWorkerError, match="claims a peak"):
        extractor.validate_worker_memory_evidence(claimed_peak)

    with pytest.raises(extractor.LargeBundleWorkerError, match="unverified single-PID"):
        extractor.validate_worker_memory_evidence(
            {
                "memory_method": "windows_GetProcessMemoryInfo_polled",
                "pid": 501,
                "private_bytes": 1,
            }
        )


def test_child_openprocess_failure_does_not_invalidate_verified_root_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "samples": {601: memory_sample(6_000, 30, 40)},
        "snapshot": [
            {"pid": 601, "parent_pid": 200, "image_name": "launcher.exe"},
            {"pid": 602, "parent_pid": 601, "image_name": "denied.exe"},
        ],
    }
    install_fake_windows_process_api(monkeypatch, state)

    sampler = extractor.WindowsProcessTreeMemorySampler(601)
    sampler.sample()
    evidence = sampler.evidence()

    assert evidence["memory_sampling_status"] == "partial"
    assert evidence["memory_sampling_completeness"] == (
        "partial_due_to_sampling_failures"
    )
    assert evidence["observed_process_count"] == 1
    assert evidence["process_query_failure_count"] == 1
    assert evidence["private_bytes"] == 40
    extractor.validate_worker_memory_evidence(evidence)


def test_validator_rejects_peak_not_reconciled_to_process_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "samples": {701: memory_sample(7_000, 70, 80)},
        "snapshot": [
            {"pid": 701, "parent_pid": 300, "image_name": "python.exe"},
        ],
    }
    install_fake_windows_process_api(monkeypatch, state)
    sampler = extractor.WindowsProcessTreeMemorySampler(701)
    sampler.sample()
    evidence = sampler.evidence()
    evidence["private_bytes"] = 81

    with pytest.raises(extractor.LargeBundleWorkerError, match="do not reconcile"):
        extractor.validate_worker_memory_evidence(evidence)

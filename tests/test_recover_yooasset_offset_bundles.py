from __future__ import annotations

import csv
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid
import importlib.util
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "recover_yooasset_offset_bundles.py"
CONFIG = SKILL_ROOT / "references" / "yooasset-offset-recovery-config.json"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("recover_yooasset_offset_bundles", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = SKILL_ROOT / "tests_runtime"
    path = parent / f"yooasset-offset-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def _unityfs_bundle(payload: bytes = b"test-payload", *, declared_size_delta: int = 0) -> bytes:
    prefix = (
        b"UnityFS\0"
        + struct.pack(">I", 8)
        + b"5.x.x\0"
        + b"2022.3.34f1\0"
    )
    header_tail = struct.pack(
        ">QIII", len(prefix) + 20 + len(payload) + declared_size_delta, 1, 1, 0
    )
    return prefix + header_tail + payload


def _write_bundle_csv(path: Path, relative_path: str, physical_size: int) -> None:
    path.parent.mkdir(parents=True)
    fields = [
        "manifest_relative_path",
        "package_name",
        "bundle_id",
        "bundle_name",
        "bundle_extension",
        "file_hash_expected",
        "file_size_actual",
        "encrypted",
        "resolved_file_relative_path",
        "file_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "manifest_relative_path": "TestPackage/PackageManifest_Test_1.0.json",
                "package_name": "TestPackage",
                "bundle_id": "0",
                "bundle_name": "test_assets.bundle",
                "bundle_extension": ".bundle",
                "file_hash_expected": "0" * 32,
                "file_size_actual": str(physical_size),
                "encrypted": "True",
                "resolved_file_relative_path": relative_path,
                "file_status": "ok",
            }
        )


def _run(source: Path, bundle_csv: Path, output: Path, audit: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--source-root",
            str(source),
            "--bundle-csv",
            str(bundle_csv),
            "--output-root",
            str(output),
            "--audit-dir",
            str(audit),
            "--config",
            str(CONFIG),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=environment,
    )


def test_recovers_zero_prefixed_unityfs_without_transforming_embedded_bytes(
    workspace_tmp_path: Path,
) -> None:
    source = workspace_tmp_path / "source"
    inner = _unityfs_bundle()
    wrapped = b"\0" * 37 + inner
    relative = "TestPackage/0123456789abcdef.bundle"
    source_file = source / Path(relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(wrapped)
    bundle_csv = workspace_tmp_path / "input" / "yooasset-bundles.csv"
    _write_bundle_csv(bundle_csv, relative, len(wrapped))
    output = workspace_tmp_path / "recovered"
    audit = workspace_tmp_path / "audit"

    result = _run(source, bundle_csv, output, audit)

    assert result.returncode == 0, result.stderr
    assert (output / Path(relative)).read_bytes() == inner
    summary = json.loads((audit / "offset-recovery-summary.json").read_text(encoding="utf-8"))
    assert summary["totals"]["manifest_bundle_count"] == 1
    assert summary["totals"]["recovered_count"] == 1
    assert summary["offset_distribution"] == {"37": 1}
    assert summary["validation"]["all_checks_passed"] is True
    assert not list(output.rglob("*.partial"))


@pytest.mark.parametrize(
    ("wrapped", "reason"),
    [
        (b"\0\0\x01" + _unityfs_bundle(), "wrapper prefix contains non-zero bytes"),
        (b"\0" * 19 + _unityfs_bundle(declared_size_delta=1), "embedded declared size mismatch"),
        (b"\0" * 11 + b"UnityRaw" + b"\0" * 7 + _unityfs_bundle(), "found 2"),
    ],
)
def test_blocks_unsafe_or_ambiguous_wrappers_without_publishing_a_bundle(
    workspace_tmp_path: Path, wrapped: bytes, reason: str
) -> None:
    source = workspace_tmp_path / "source"
    relative = "TestPackage/blocked.bundle"
    source_file = source / Path(relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(wrapped)
    bundle_csv = workspace_tmp_path / "input" / "yooasset-bundles.csv"
    _write_bundle_csv(bundle_csv, relative, len(wrapped))
    output = workspace_tmp_path / "recovered"
    audit = workspace_tmp_path / "audit"

    result = _run(source, bundle_csv, output, audit)

    assert result.returncode == 2
    assert not (output / Path(relative)).exists()
    with (audit / "offset-recovery-files.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["status"] == "blocked"
    assert reason in row["block_reason"]
    assert not list(output.rglob("*.partial"))


def test_rejects_source_output_overlap_before_creating_recovery_artifacts(
    workspace_tmp_path: Path,
) -> None:
    source = workspace_tmp_path / "source"
    inner = _unityfs_bundle()
    relative = "TestPackage/overlap.bundle"
    source_file = source / Path(relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"\0" * 9 + inner)
    bundle_csv = workspace_tmp_path / "input" / "yooasset-bundles.csv"
    _write_bundle_csv(bundle_csv, relative, source_file.stat().st_size)
    output = source / "recovered"
    audit = workspace_tmp_path / "audit"

    result = _run(source, bundle_csv, output, audit)

    assert result.returncode == 2
    assert "source/output roots overlap" in result.stderr
    assert not output.exists()
    assert not audit.exists()


def test_blocks_configured_access_control_signal_without_reading_or_recovering_it(
    workspace_tmp_path: Path,
) -> None:
    source = workspace_tmp_path / "source"
    inner = _unityfs_bundle()
    relative = "TestPackage/anti-cheat.bundle"
    source_file = source / Path(relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"\0" * 23 + inner)
    bundle_csv = workspace_tmp_path / "input" / "yooasset-bundles.csv"
    _write_bundle_csv(bundle_csv, relative, source_file.stat().st_size)
    output = workspace_tmp_path / "recovered"
    audit = workspace_tmp_path / "audit"

    result = _run(source, bundle_csv, output, audit)

    assert result.returncode == 2
    with (audit / "offset-recovery-files.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["status"] == "blocked"
    assert "DRM/anti-cheat/access-control signal" in row["block_reason"]
    assert not (output / Path(relative)).exists()


def test_rejects_non_fresh_output_root_and_preserves_existing_content(
    workspace_tmp_path: Path,
) -> None:
    source = workspace_tmp_path / "source"
    inner = _unityfs_bundle()
    relative = "TestPackage/freshness.bundle"
    source_file = source / Path(relative)
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"\0" * 13 + inner)
    bundle_csv = workspace_tmp_path / "input" / "yooasset-bundles.csv"
    _write_bundle_csv(bundle_csv, relative, source_file.stat().st_size)
    output = workspace_tmp_path / "recovered"
    output.mkdir()
    sentinel = output / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    audit = workspace_tmp_path / "audit"

    result = _run(source, bundle_csv, output, audit)

    assert result.returncode == 2
    assert "output root must be fresh" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not audit.exists()


def test_no_replace_publication_preserves_a_target_that_arrives_after_preflight(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script_module()
    partial = workspace_tmp_path / "owned.partial"
    target = workspace_tmp_path / "target.bundle"
    partial.write_bytes(b"owned")
    original_link = module.os.link

    def racing_link(source, destination, *, follow_symlinks=False):
        Path(destination).write_bytes(b"late-external")
        return original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(module.os, "link", racing_link)

    with pytest.raises(FileExistsError):
        module._publish_no_replace(partial, target)

    assert target.read_bytes() == b"late-external"
    assert not partial.exists()

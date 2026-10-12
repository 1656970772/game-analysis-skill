from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "analyze_yooasset_manifests.py"
CONFIG = SKILL_ROOT / "references" / "yooasset-manifest-audit-config.json"


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = SKILL_ROOT / "tests_runtime"
    path = parent / f"yooasset-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def _bundle(name: str, data: bytes, *, depend_ids: list[int], encrypted: bool) -> dict:
    return {
        "BundleName": name,
        "UnityCRC": 0 if name.endswith(".rawfile") else 123,
        "FileHash": hashlib.md5(data).hexdigest(),
        "FileCRC": (zlib.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little").hex(),
        "FileSize": len(data),
        "Encrypted": encrypted,
        "Tags": [],
        "DependIDs": depend_ids,
    }


def _write_package(
    manifest_root: Path,
    streaming_root: Path,
    *,
    package_name: str = "TestPackage",
    mutate_manifest=None,
    mutate_runtime=None,
) -> tuple[Path, dict[str, bytes]]:
    payloads = {
        "first.bundle": b"UnityFS-test-bundle",
        "second.rawfile": b"raw-file-payload",
    }
    bundles = [
        _bundle("test_first.bundle", payloads["first.bundle"], depend_ids=[], encrypted=True),
        _bundle("test_second.rawfile", payloads["second.rawfile"], depend_ids=[0], encrypted=False),
    ]
    manifest = {
        "FileVersion": "2.0.0",
        "EnableAddressable": True,
        "LocationToLower": False,
        "IncludeAssetGUID": True,
        "OutputNameStyle": 0,
        "BuildPipeline": "BuiltinBuildPipeline",
        "PackageName": package_name,
        "PackageVersion": "1.2.3",
        "AssetList": [
            {
                "Address": "First",
                "AssetPath": "Assets/Test/First.prefab",
                "AssetGUID": "a" * 32,
                "AssetTags": ["core"],
                "BundleID": 0,
            },
            {
                "Address": "Second",
                "AssetPath": "Assets/Test/Second.bytes",
                "AssetGUID": "b" * 32,
                "AssetTags": [],
                "BundleID": 1,
            },
        ],
        "BundleList": bundles,
    }
    if mutate_manifest:
        mutate_manifest(manifest)

    archive_package = manifest_root / package_name
    runtime_package = streaming_root / package_name
    archive_package.mkdir(parents=True)
    runtime_package.mkdir(parents=True)
    manifest_name = f"PackageManifest_{package_name}_1.2.3.json"
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    manifest_path = archive_package / manifest_name
    manifest_path.write_bytes(manifest_bytes)
    (runtime_package / manifest_name).write_bytes(manifest_bytes)

    for bundle, data in zip(bundles, payloads.values(), strict=True):
        suffix = Path(bundle["BundleName"]).suffix
        (runtime_package / f"{bundle['FileHash']}{suffix}").write_bytes(data)

    (runtime_package / manifest_name.replace(".json", ".bytes")).write_bytes(b"manifest-bytes")
    (runtime_package / manifest_name.replace(".json", ".hash")).write_text("hash", encoding="ascii")
    (runtime_package / f"PackageManifest_{package_name}.version").write_text(
        "1.2.3", encoding="ascii"
    )
    (runtime_package / "link.xml").write_text("<linker />", encoding="utf-8")
    if mutate_runtime:
        mutate_runtime(runtime_package, manifest)
    return manifest_path, payloads


def _run(manifest_root: Path, streaming_root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--manifest-root",
            str(manifest_root),
            "--streaming-assets-root",
            str(streaming_root),
            "--output-dir",
            str(output),
            "--config",
            str(CONFIG),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=environment,
    )


def _read_summary(output: Path) -> dict:
    return json.loads((output / "yooasset-summary.json").read_text(encoding="utf-8"))


def test_maps_assets_bundles_and_runtime_files_read_only_and_idempotently(
    workspace_tmp_path: Path,
) -> None:
    tmp_path = workspace_tmp_path
    manifest_root = tmp_path / "manifests"
    streaming_root = tmp_path / "runtime"
    _write_package(manifest_root, streaming_root)
    source_before = {
        path.relative_to(streaming_root).as_posix(): path.read_bytes()
        for path in streaming_root.rglob("*")
        if path.is_file()
    }
    output = tmp_path / "output"

    first = _run(manifest_root, streaming_root, output)
    assert first.returncode == 0, first.stderr
    summary = _read_summary(output)
    assert summary["totals"] == {
        "manifest_count": 1,
        "asset_count": 2,
        "bundle_count": 2,
        "declared_total_bytes": len(b"UnityFS-test-bundle") + len(b"raw-file-payload"),
        "matched_actual_total_bytes": len(b"UnityFS-test-bundle") + len(b"raw-file-payload"),
        "encrypted_bundle_count": 1,
        "unencrypted_bundle_count": 1,
        "missing_file_count": 0,
        "extra_payload_file_count": 0,
        "extra_unknown_file_count": 0,
        "size_mismatch_count": 0,
        "file_hash_mismatch_count": 0,
        "file_crc_mismatch_count": 0,
        "invalid_asset_bundle_id_count": 0,
        "invalid_dependency_id_count": 0,
        "self_dependency_count": 0,
        "duplicate_dependency_id_count": 0,
        "runtime_manifest_mismatch_count": 0,
    }
    assert summary["validation"]["all_checks_passed"] is True
    assert summary["source_integrity"]["unchanged_during_analysis"] is True
    assert summary["packages"][0]["runtime_manifest"]["matches_archived_manifest"] is True
    assert summary["packages"][0]["runtime_files"]["metadata_file_count"] == 5

    with (output / "yooasset-assets.csv").open(encoding="utf-8-sig", newline="") as handle:
        assets = list(csv.DictReader(handle))
    with (output / "yooasset-bundles.csv").open(encoding="utf-8-sig", newline="") as handle:
        bundles = list(csv.DictReader(handle))
    assert [row["bundle_id"] for row in assets] == ["0", "1"]
    assert [row["file_status"] for row in bundles] == ["ok", "ok"]
    assert bundles[1]["depend_ids_json"] == "[0]"

    artifacts_before = {
        path.name: path.read_bytes() for path in output.iterdir() if path.is_file()
    }
    second = _run(manifest_root, streaming_root, output)
    assert second.returncode == 0, second.stderr
    artifacts_after = {
        path.name: path.read_bytes() for path in output.iterdir() if path.is_file()
    }
    assert artifacts_after == artifacts_before
    assert not list(output.glob("*.tmp"))
    source_after = {
        path.relative_to(streaming_root).as_posix(): path.read_bytes()
        for path in streaming_root.rglob("*")
        if path.is_file()
    }
    assert source_after == source_before


def test_reports_missing_extra_size_hash_and_crc_mismatches(
    workspace_tmp_path: Path,
) -> None:
    tmp_path = workspace_tmp_path
    manifest_root = tmp_path / "manifests"
    streaming_root = tmp_path / "runtime"

    def mutate_runtime(package_dir: Path, manifest: dict) -> None:
        first = manifest["BundleList"][0]
        second = manifest["BundleList"][1]
        (package_dir / f"{first['FileHash']}.bundle").write_bytes(b"mutated-and-longer")
        (package_dir / f"{second['FileHash']}.rawfile").unlink()
        (package_dir / "unlisted.bundle").write_bytes(b"extra")
        (package_dir / "unknown.note").write_text("extra metadata-like file", encoding="utf-8")

    _write_package(manifest_root, streaming_root, mutate_runtime=mutate_runtime)
    output = tmp_path / "output"

    result = _run(manifest_root, streaming_root, output)
    assert result.returncode == 2
    summary = _read_summary(output)
    totals = summary["totals"]
    assert totals["missing_file_count"] == 1
    assert totals["extra_payload_file_count"] == 1
    assert totals["extra_unknown_file_count"] == 1
    assert totals["size_mismatch_count"] == 1
    assert totals["file_hash_mismatch_count"] == 1
    assert totals["file_crc_mismatch_count"] == 1
    assert summary["validation"]["all_checks_passed"] is False


def test_reports_asset_and_dependency_id_boundaries(workspace_tmp_path: Path) -> None:
    tmp_path = workspace_tmp_path
    manifest_root = tmp_path / "manifests"
    streaming_root = tmp_path / "runtime"

    def mutate_manifest(manifest: dict) -> None:
        manifest["AssetList"][0]["BundleID"] = 2
        manifest["BundleList"][0]["DependIDs"] = [0, 3, 3]

    _write_package(manifest_root, streaming_root, mutate_manifest=mutate_manifest)
    output = tmp_path / "output"

    result = _run(manifest_root, streaming_root, output)
    assert result.returncode == 2
    summary = _read_summary(output)
    totals = summary["totals"]
    assert totals["invalid_asset_bundle_id_count"] == 1
    assert totals["invalid_dependency_id_count"] == 2
    assert totals["self_dependency_count"] == 1
    assert totals["duplicate_dependency_id_count"] == 1
    assert summary["validation"]["all_checks_passed"] is False

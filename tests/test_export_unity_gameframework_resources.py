import csv
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_unity_gameframework_resources.py"


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = ROOT / "tests_runtime"
    path = parent / f"gameframework-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def encode_7bit(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def xor(data: bytes, key: bytes, limit: int | None = None) -> bytes:
    result = bytearray(data)
    count = len(result) if limit is None else min(len(result), limit)
    for index in range(count):
        result[index] ^= key[index % len(key)]
    return bytes(result)


def encrypted_string(value: str | None, key: bytes) -> bytes:
    if not value:
        return b"\0"
    raw = value.encode("utf-8")
    return bytes([len(raw)]) + xor(raw, key)


def build_gff(path: Path, entries: list[tuple[str, bytes]]) -> None:
    key = b"\x10\x20\x30\x40"
    max_count = len(entries)
    metadata_end = 20 + 12 * max_count + 256 * max_count
    payload_offset = ((metadata_end + 4095) // 4096) * 4096
    blocks = bytearray()
    strings = bytearray()
    payload = bytearray()
    cluster_index = payload_offset // 4096
    for string_index, (name, data) in enumerate(entries):
        blocks.extend(struct.pack("<iii", string_index, cluster_index, len(data)))
        raw_name = name.encode("utf-8")
        strings.extend(bytes([len(raw_name)]) + xor(raw_name, key))
        strings.extend(b"\0" * (255 - len(raw_name)))
        payload.extend(data)
        aligned = ((len(data) + 4095) // 4096) * 4096
        payload.extend(b"\0" * (aligned - len(data)))
        cluster_index += aligned // 4096
    header = b"GFF\0" + key + struct.pack(
        "<iii", max_count, max_count, max_count
    )
    prefix = header + blocks + strings
    prefix += b"\0" * (payload_offset - len(prefix))
    path.write_bytes(prefix + payload)


def build_package(
    path: Path,
    assets: list[tuple[str, list[int]]],
    resources: list[dict],
    file_systems: list[tuple[str, list[int]]],
) -> None:
    key = b"\x51\x62\x73\x84"
    data = bytearray(b"GFP\x02" + key)
    data.extend(encrypted_string("1.2.3", key))
    data.extend(encode_7bit(7))
    data.extend(encode_7bit(len(assets)))
    for name, dependencies in assets:
        data.extend(encrypted_string(name, key))
        data.extend(encode_7bit(len(dependencies)))
        for dependency in dependencies:
            data.extend(encode_7bit(dependency))
    data.extend(encode_7bit(len(resources)))
    for resource in resources:
        data.extend(encrypted_string(resource["name"], key))
        data.extend(encrypted_string(resource.get("variant"), key))
        data.extend(encrypted_string(resource.get("extension"), key))
        data.append(resource["load_type"])
        data.extend(encode_7bit(len(resource["plain"])))
        data.extend(struct.pack("<i", resource["hash_code"]))
        data.extend(encode_7bit(len(resource["asset_indexes"])))
        for asset_index in resource["asset_indexes"]:
            data.extend(encode_7bit(asset_index))
    data.extend(encode_7bit(len(file_systems)))
    for name, indexes in file_systems:
        data.extend(encrypted_string(name, key))
        data.extend(encode_7bit(len(indexes)))
        for resource_index in indexes:
            data.extend(encode_7bit(resource_index))
    data.extend(b"\0")  # resource group count
    path.write_bytes(data)


def run_export(source: Path, output: Path, index: Path, *extra: str):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(source),
            "--mode",
            "extract",
            "--output-dir",
            str(output),
            "--index-dir",
            str(index),
            *extra,
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_extracts_loose_and_gff_resources_with_full_and_quick_xor(
    workspace_tmp_path: Path,
):
    source = workspace_tmp_path / "StreamingAssets"
    output = workspace_tmp_path / "decoded"
    index = workspace_tmp_path / "index"
    source.mkdir()
    resources = [
        {
            "name": "GameMain/full",
            "extension": None,
            "load_type": 3,
            "hash_code": 0x12345678,
            "asset_indexes": [0],
            "plain": b"UnityFS\0full-payload",
        },
        {
            "name": "GameMain/quick",
            "extension": "dat",
            "load_type": 2,
            "hash_code": -1234567,
            "asset_indexes": [1],
            "plain": b"UnityFS\0" + (b"q" * 300),
        },
        {
            "name": "packed",
            "extension": None,
            "load_type": 0,
            "hash_code": 0,
            "asset_indexes": [],
            "plain": b"UnityFS\0packed",
        },
    ]
    build_package(
        source / "GameFrameworkVersion.dat",
        [("Assets/A", []), ("Assets/B", [0])],
        resources,
        [("Data", [2])],
    )
    full_key = struct.pack("<i", resources[0]["hash_code"])
    quick_key = struct.pack("<i", resources[1]["hash_code"])
    (source / "GameMain").mkdir()
    (source / "GameMain" / "full.dat").write_bytes(
        xor(resources[0]["plain"], full_key)
    )
    (source / "GameMain" / "quick.dat").write_bytes(
        xor(resources[1]["plain"], quick_key, 220)
    )
    build_gff(source / "Data.dat", [("packed.dat", resources[2]["plain"])])

    result = run_export(source, output, index)

    assert result.returncode == 0, result.stderr
    assert (output / "GameMain" / "full.dat").read_bytes() == resources[0]["plain"]
    assert (output / "GameMain" / "quick.dat").read_bytes() == resources[1]["plain"]
    assert (output / "packed.dat").read_bytes() == resources[2]["plain"]
    summary = json.loads((index / "summary.json").read_text(encoding="utf-8"))
    assert summary["resource_count"] == 3
    assert summary["status_counts"] == {"extracted": 3}
    assert summary["decoded_magic_counts"] == {"UnityFS": 3}
    with (index / "gameframework_resource_index.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert {row["source_kind"] for row in rows} == {"loose", "gff"}
    assert all(row["length_match"] == "True" for row in rows)
    assert not (index / "errors.jsonl").read_text(encoding="utf-8")


def test_probe_mode_writes_indexes_without_payloads(workspace_tmp_path: Path):
    source = workspace_tmp_path / "StreamingAssets"
    index = workspace_tmp_path / "index"
    source.mkdir()
    plain = b"UnityFS\0probe"
    resource = {
        "name": "probe",
        "extension": None,
        "load_type": 0,
        "hash_code": 0,
        "asset_indexes": [],
        "plain": plain,
    }
    build_package(source / "GameFrameworkVersion.dat", [], [resource], [])
    (source / "probe.dat").write_bytes(plain)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(source),
            "--mode",
            "probe",
            "--index-dir",
            str(index),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((index / "summary.json").read_text(encoding="utf-8"))
    assert summary["status_counts"] == {"probed": 1}


def test_rejects_unsafe_resource_paths(workspace_tmp_path: Path):
    source = workspace_tmp_path / "StreamingAssets"
    source.mkdir()
    resource = {
        "name": "../escape",
        "extension": None,
        "load_type": 0,
        "hash_code": 0,
        "asset_indexes": [],
        "plain": b"x",
    }
    build_package(source / "GameFrameworkVersion.dat", [], [resource], [])

    result = run_export(
        source,
        workspace_tmp_path / "decoded",
        workspace_tmp_path / "index",
    )

    assert result.returncode != 0
    assert "unsafe resource path" in result.stderr
    assert not (workspace_tmp_path / "escape.dat").exists()


def test_reports_length_mismatch_without_partial_output(workspace_tmp_path: Path):
    source = workspace_tmp_path / "StreamingAssets"
    source.mkdir()
    resource = {
        "name": "broken",
        "extension": None,
        "load_type": 0,
        "hash_code": 0,
        "asset_indexes": [],
        "plain": b"expected",
    }
    build_package(source / "GameFrameworkVersion.dat", [], [resource], [])
    (source / "broken.dat").write_bytes(b"short")

    result = run_export(
        source,
        workspace_tmp_path / "decoded",
        workspace_tmp_path / "index",
    )

    assert result.returncode != 0
    errors = (
        workspace_tmp_path / "index" / "errors.jsonl"
    ).read_text(encoding="utf-8")
    assert "length mismatch" in errors
    assert not (workspace_tmp_path / "decoded" / "broken.dat").exists()

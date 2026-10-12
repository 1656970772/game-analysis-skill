import csv
import hashlib
import json
import os
import struct
import subprocess
import sys
import shutil
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_godot_pck.py"


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = ROOT / "tests_runtime"
    path = parent / f"pck-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def _path_field(value: str | bytes) -> bytes:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    raw += b"\0"
    return raw + (b"\0" * ((-len(raw)) % 4))


def build_pck(
    path: Path,
    entries: list[dict],
    *,
    format_version: int = 1,
    reserved: list[int] | None = None,
) -> None:
    reserved_values = reserved or ([0] * 16)
    encoded_paths = [_path_field(entry["path"]) for entry in entries]
    directory_size = 88 + sum(4 + len(raw) + 8 + 8 + 16 for raw in encoded_paths)
    next_offset = directory_size
    table = bytearray()
    payload = bytearray()

    for entry, raw_path in zip(entries, encoded_paths, strict=True):
        data = entry.get("data", b"")
        offset = entry.get("offset", next_offset)
        declared_size = entry.get("declared_size", len(data))
        digest = entry.get("md5", hashlib.md5(data).digest())
        table.extend(struct.pack("<I", len(raw_path)))
        table.extend(raw_path)
        table.extend(struct.pack("<QQ", offset, declared_size))
        table.extend(digest)
        payload.extend(data)
        next_offset += len(data)

    header = bytearray(b"GDPC")
    header.extend(struct.pack("<IIII", format_version, 3, 2, 3))
    header.extend(struct.pack("<16I", *reserved_values))
    header.extend(struct.pack("<I", len(entries)))
    path.write_bytes(bytes(header + table + payload))


def run_probe(source: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/analyze_godot_pck.py",
            str(source.relative_to(ROOT)),
            "--output-dir",
            str(output_dir.relative_to(ROOT)),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_valid_format_zero_and_one_emit_json_csv_and_summary(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    for format_version in (0, 1):
        source = tmp_path / f"valid-{format_version}.pck"
        output_dir = tmp_path / f"out-{format_version}"
        build_pck(
            source,
            [
                {"path": "res://project.binary", "data": b"project"},
                {"path": "res://scripts/player.gdc", "data": b"bytecode"},
            ],
            format_version=format_version,
        )

        result = run_probe(source, output_dir)

        assert result.returncode == 0, result.stderr
        index = json.loads((output_dir / "godot_pck_index.json").read_text(encoding="utf-8"))
        assert index["format_version"] == format_version
        assert index["engine_version"] == "3.2.3"
        assert index["entry_count"] == 2
        assert [entry["path"] for entry in index["entries"]] == [
            "res://project.binary",
            "res://scripts/player.gdc",
        ]
        assert all(entry["md5_valid"] for entry in index["entries"])
        with (output_dir / "godot_pck_index.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 2
        assert "只读" in (output_dir / "godot_pck_summary.md").read_text(encoding="utf-8")


def test_zero_entry_package_is_valid(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "empty.pck"
    output_dir = tmp_path / "out"
    build_pck(source, [])

    result = run_probe(source, output_dir)

    assert result.returncode == 0, result.stderr
    index = json.loads((output_dir / "godot_pck_index.json").read_text(encoding="utf-8"))
    assert index["entry_count"] == 0
    assert index["entries"] == []


def test_rejects_out_of_bounds_entry(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "out-of-bounds.pck"
    build_pck(source, [{"path": "res://bad.bin", "data": b"x", "offset": 999_999}])

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "越界" in result.stderr


def test_rejects_overlapping_entries(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "overlap.pck"
    first_path = _path_field("res://a.bin")
    second_path = _path_field("res://b.bin")
    data_start = 88 + sum(4 + len(raw) + 8 + 8 + 16 for raw in (first_path, second_path))
    build_pck(
        source,
        [
            {"path": "res://a.bin", "data": b"abcd", "offset": data_start},
            {"path": "res://b.bin", "data": b"efgh", "offset": data_start + 2},
        ],
    )

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "重叠" in result.stderr


def test_rejects_md5_mismatch(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "md5.pck"
    build_pck(source, [{"path": "res://bad.bin", "data": b"payload", "md5": b"\0" * 16}])

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "MD5" in result.stderr


def test_rejects_path_traversal_and_absolute_paths(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    invalid_paths = [
        "res://../evil.txt",
        "res:///absolute.txt",
        "res://C:/drive.txt",
        "res:////server/share.txt",
        "C:/outside.txt",
        "//server/share.txt",
        "/outside.txt",
    ]
    for index, invalid_path in enumerate(invalid_paths):
        source = tmp_path / f"bad-path-{index}.pck"
        build_pck(source, [{"path": invalid_path, "data": b"x"}])

        result = run_probe(source, tmp_path / f"out-{index}")

        assert result.returncode != 0, invalid_path
        assert "路径" in result.stderr


@pytest.mark.parametrize(
    "invalid_path",
    [
        "res://CON.txt",
        "res://dir/aux.dat",
        "res://dir/file.txt:payload",
        "res://dir/name.",
        "res://dir/name ",
        "res://dir/que?st.txt",
        "res://dir/control\x01.txt",
    ],
)
def test_rejects_windows_unsafe_extraction_paths(
    workspace_tmp_path: Path,
    invalid_path: str,
):
    source = workspace_tmp_path / "windows-unsafe.pck"
    build_pck(source, [{"path": invalid_path, "data": b"x"}])

    result = run_probe(source, workspace_tmp_path / "out")

    assert result.returncode != 0, invalid_path
    assert "Windows" in result.stderr


@pytest.mark.parametrize(
    "first,second",
    [
        ("res://Foo.txt", "res://foo.txt"),
        ("res://Straße.txt", "res://STRASSE.txt"),
        ("res://caf\u00e9.txt", "res://cafe\u0301.txt"),
    ],
)
def test_rejects_unicode_normalized_casefold_path_collisions(
    workspace_tmp_path: Path,
    first: str,
    second: str,
):
    source = workspace_tmp_path / "windows-collision.pck"
    build_pck(
        source,
        [
            {"path": first, "data": b"a"},
            {"path": second, "data": b"b"},
        ],
    )

    result = run_probe(source, workspace_tmp_path / "out")

    assert result.returncode != 0
    assert "Windows" in result.stderr
    assert "冲突" in result.stderr


def test_rejects_invalid_utf8_path(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "bad-utf8.pck"
    build_pck(source, [{"path": b"res://\xff.bin", "data": b"x"}])

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "UTF-8" in result.stderr


def test_rejects_nonzero_reserved_field(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "reserved.pck"
    build_pck(source, [], reserved=[1] + ([0] * 15))

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "保留字段" in result.stderr


def test_rejects_huge_count_before_allocating(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "huge-count.pck"
    header = bytearray(b"GDPC")
    header.extend(struct.pack("<IIII", 1, 3, 2, 3))
    header.extend(struct.pack("<16I", *([0] * 16)))
    header.extend(struct.pack("<I", 1_000_001))
    source.write_bytes(header)

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "条目数" in result.stderr


def test_rejects_truncated_directory_entry(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    source = tmp_path / "truncated.pck"
    header = bytearray(b"GDPC")
    header.extend(struct.pack("<IIII", 1, 3, 2, 3))
    header.extend(struct.pack("<16I", *([0] * 16)))
    header.extend(struct.pack("<I", 1))
    header.extend(b"\x08\x00")
    source.write_bytes(header)

    result = run_probe(source, tmp_path / "out")

    assert result.returncode != 0
    assert "截断" in result.stderr

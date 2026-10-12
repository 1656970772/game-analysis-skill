from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path
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
    def __init__(self, name: str, script: bytes | None = None) -> None:
        self.m_Name = name
        if script is not None:
            self.m_Script = script


class FakeObject:
    def __init__(
        self,
        *,
        asset_type: str,
        path_id: int,
        assets_file_name: str,
        asset_name: str,
        script: bytes | None = None,
    ) -> None:
        self.type = FakeType(asset_type)
        self.path_id = path_id
        self.assets_file = FakeAssetsFile(assets_file_name)
        self._data = FakeData(asset_name, script)

    def read(self) -> FakeData:
        return self._data

    def read_typetree(self) -> dict[str, Any]:
        return {"serialized_file": self.assets_file.name}


class FakeEnv:
    container: dict[str, Any] = {}

    def __init__(self, objects: list[FakeObject]) -> None:
        self.objects = objects


class FakeUnityPy:
    objects: list[FakeObject] = []

    @classmethod
    def load(cls, _: str) -> FakeEnv:
        return FakeEnv(cls.objects)


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "Game"
    data_dir = source / "Game_Data"
    data_dir.mkdir(parents=True)
    (data_dir / "resources.unity3d").write_bytes(b"fake")
    return source


def read_index(output: Path) -> list[dict[str, str]]:
    with (output / "all_resources_index.csv").open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_same_path_id_in_different_serialized_files_has_stable_unique_paths(
    tmp_path: Path, monkeypatch
) -> None:
    source = make_source(tmp_path)
    FakeUnityPy.objects = [
        FakeObject(
            asset_type="Shader",
            path_id=64,
            assets_file_name="globalgamemanagers.assets",
            asset_name="Collision",
        ),
        FakeObject(
            asset_type="Shader",
            path_id=64,
            assets_file_name="unity_builtin_extra",
            asset_name="Collision",
        ),
    ]
    monkeypatch.setattr(extractor, "UnityPy", FakeUnityPy)

    outputs = [tmp_path / "out_first", tmp_path / "out_second"]
    path_runs: list[list[str]] = []
    id_runs: list[list[str]] = []
    for output in outputs:
        extractor.export_all(source, output)
        rows = read_index(output)
        paths = [row["export_path"] for row in rows]
        asset_ids = [row["asset_id"] for row in rows]
        assert len(set(paths)) == 2
        assert len(set(asset_ids)) == 2
        assert "globalgamemanagers.assets" in paths[0]
        assert "unity_builtin_extra" in paths[1]
        assert all((output / path).is_file() for path in paths)
        path_runs.append(paths)
        id_runs.append(asset_ids)

    assert path_runs[0] == path_runs[1]
    assert id_runs[0] == id_runs[1]


def test_text_asset_extension_is_appended_without_truncating_dotted_basename(
    tmp_path: Path, monkeypatch
) -> None:
    source = make_source(tmp_path)
    FakeUnityPy.objects = [
        FakeObject(
            asset_type="TextAsset",
            path_id=125,
            assets_file_name="resources.assets",
            asset_name="raptor-pro.atlas",
            script=b"atlas payload",
        )
    ]
    monkeypatch.setattr(extractor, "UnityPy", FakeUnityPy)

    output = tmp_path / "out"
    extractor.export_all(source, output)
    row = read_index(output)[0]

    assert row["export_path"].endswith("_raptor-pro.atlas.txt")
    assert (output / row["export_path"]).read_bytes() == b"atlas payload"


def test_bundle_discovery_requires_trusted_unity_bundle_magic(tmp_path: Path) -> None:
    (tmp_path / "valid.dat").write_bytes(b"UnityFS\x00synthetic")
    (tmp_path / "random.dat").write_bytes(b"not-a-unity-container")
    (tmp_path / "wrong-extension.bin").write_bytes(b"UnityFS\x00synthetic")

    discovered = [path.name for path in extractor.discover_unity_sources(tmp_path)]

    assert discovered == ["valid.dat", "wrong-extension.bin"]


def test_discovers_level_serialized_files_without_collecting_unrelated_payloads(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "Game_Data"
    data_dir.mkdir()
    for name in ("level0", "level1", "level340"):
        (data_dir / name).write_bytes(b"synthetic serialized file")
    (data_dir / "README").write_bytes(b"unrelated extensionless content")
    (data_dir / "random_cache").write_bytes(b"not a Unity serialized file")
    (data_dir / "resources.assets").write_bytes(b"serialized companion")
    (data_dir / "sharedassets0.assets").write_bytes(b"serialized companion")
    resources_dir = data_dir / "Resources"
    resources_dir.mkdir()
    (resources_dir / "unity default resources").write_bytes(b"built-in serialized file")
    (resources_dir / "unity_builtin_extra").write_bytes(b"built-in serialized file")
    (data_dir / "resources.assets.resS").write_bytes(b"external payload")
    (data_dir / "sharedassets0.assets.resS").write_bytes(b"external payload")
    (data_dir / "resources.resource").write_bytes(b"external payload")

    discovered = {
        path.relative_to(tmp_path).as_posix()
        for path in extractor.discover_unity_sources(tmp_path)
    }

    assert {
        "Game_Data/level0",
        "Game_Data/level1",
        "Game_Data/level340",
        "Game_Data/resources.assets",
        "Game_Data/sharedassets0.assets",
        "Game_Data/Resources/unity default resources",
        "Game_Data/Resources/unity_builtin_extra",
    } <= discovered
    assert {
        "Game_Data/README",
        "Game_Data/random_cache",
        "Game_Data/resources.assets.resS",
        "Game_Data/sharedassets0.assets.resS",
        "Game_Data/resources.resource",
    }.isdisjoint(discovered)

    payloads = {
        path.relative_to(tmp_path).as_posix()
        for path in extractor.discover_unity_external_payloads(tmp_path)
    }
    assert payloads == {
        "Game_Data/resources.assets.resS",
        "Game_Data/sharedassets0.assets.resS",
        "Game_Data/resources.resource",
    }


def test_export_summary_records_external_payloads_separately(
    tmp_path: Path, monkeypatch
) -> None:
    source = make_source(tmp_path)
    data_dir = source / "Game_Data"
    (data_dir / "resources.assets").write_bytes(b"serialized companion")
    (data_dir / "resources.assets.resS").write_bytes(b"external payload")
    (data_dir / "resources.resource").write_bytes(b"external payload")
    FakeUnityPy.objects = []
    monkeypatch.setattr(extractor, "UnityPy", FakeUnityPy)

    summary = extractor.export_all(source, tmp_path / "out")

    assert summary["external_payload_count"] == 2
    assert summary["external_payloads"] == [
        "Game_Data/resources.assets.resS",
        "Game_Data/resources.resource",
    ]
    assert set(summary["external_payloads"]).isdisjoint(summary["sources"])


@pytest.mark.skipif(os.name != "nt", reason="Windows junction authorization boundary")
@pytest.mark.parametrize("nested", [False, True])
def test_source_discovery_rejects_junctions_at_every_depth(
    tmp_path: Path,
    nested: bool,
) -> None:
    source = tmp_path / "authorized"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (outside / "external.assets").write_bytes(b"outside authorized root")
    parent = source
    if nested:
        parent = source / "nested"
        parent.mkdir()
    junction = parent / "linked"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot create isolated Windows junction: {result.stderr or result.stdout}")

    with pytest.raises(extractor.SourceBoundaryError, match="junction/reparse"):
        extractor.discover_unity_sources(source)
    with pytest.raises(extractor.SourceBoundaryError, match="junction/reparse"):
        extractor.discover_unity_external_payloads(source)
    with pytest.raises(extractor.SourceBoundaryError, match="junction/reparse"):
        extractor.discover_addressables_catalogs(source)
    output = tmp_path / "catalog-output"
    with pytest.raises(extractor.SourceBoundaryError, match="junction/reparse"):
        extractor.parse_addressables_catalogs(source, output)
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows source-generation lease")
def test_source_snapshot_pins_candidate_ancestor_against_reparse_swap(
    tmp_path: Path,
) -> None:
    source = tmp_path / "authorized"
    nested = source / "nested"
    nested.mkdir(parents=True)
    candidate = nested / "resources.assets"
    candidate.write_bytes(b"authorized")
    parked = source / "parked"

    boundary = extractor.SourceBoundary(source).initialize()
    files = boundary.discover_files()
    with boundary.acquire_snapshot(files) as snapshot:
        snapshot.verify(candidate)
        with pytest.raises(PermissionError):
            os.replace(nested, parked)
        snapshot.verify_all()

    os.replace(nested, parked)
    assert (parked / candidate.name).read_bytes() == b"authorized"

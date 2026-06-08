from __future__ import annotations

import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


class FakeType:
    name = "AudioClip"


class FakeAudioData:
    m_Name = "DangerAudio"

    @property
    def samples(self) -> dict[str, bytes]:
        raise AssertionError("default export must not touch native audio sample decoder")


class FakeObject:
    type = FakeType()
    path_id = 123

    def read(self) -> FakeAudioData:
        return FakeAudioData()


class FakeEnv:
    container = {}
    objects = [FakeObject()]


class FakeUnityPy:
    @staticmethod
    def load(_: str) -> FakeEnv:
        return FakeEnv()


def test_audio_clip_defaults_to_metadata_without_touching_samples(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Game"
    data_dir = source / "Game_Data"
    data_dir.mkdir(parents=True)
    (data_dir / "resources.assets").write_bytes(b"fake")

    monkeypatch.setattr(extractor, "UnityPy", FakeUnityPy)

    output = tmp_path / "out"
    summary = extractor.export_all(source, output)

    assert summary["object_count"] == 1
    rows = list(csv.DictReader((output / "all_resources_index.csv").open(encoding="utf-8-sig")))
    assert rows[0]["asset_type"] == "AudioClip"
    assert rows[0]["status"] == "indexed_audio_metadata"
    assert rows[0]["export_kind"] == "metadata"

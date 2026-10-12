from __future__ import annotations

import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import convert_klei_dst_resources as converter  # noqa: E402


class UnsupportedFsb5:
    def load(self, _data: bytes) -> object:
        raise ValueError("unsupported fsb for python-fsb5")


def test_vgmstream_fallback_indexes_each_generated_wav(tmp_path: Path, monkeypatch) -> None:
    study = tmp_path / "study"
    game_root = tmp_path / "game"
    bank_path = game_root / "data" / "sound" / "multi.fsb"
    bank_path.parent.mkdir(parents=True)
    bank_path.write_bytes(b"not parsed by fake fsb5")
    vgmstream = tmp_path / "vgmstream-cli.exe"
    vgmstream.write_text("fake exe", encoding="utf-8")

    monkeypatch.setattr(converter, "load_fsb5", lambda _pkg: UnsupportedFsb5())

    def fake_decode(_vgmstream: Path, _bank_path: Path, out_dir: Path, _force: bool) -> tuple[bool, int, int, str]:
        (out_dir / "1_alpha.wav").write_bytes(b"alpha")
        (out_dir / "2_beta.wav").write_bytes(b"beta")
        return True, 2, 2, "decoded 2 wavs"

    monkeypatch.setattr(converter, "decode_bank_with_vgmstream", fake_decode)

    dirs = converter.ensure_dirs(study)
    result = converter.convert_audio(
        game_root=game_root,
        fsb_pkg=tmp_path / "fsb_pkg",
        vgmstream=vgmstream,
        limit_banks=0,
        limit_samples=0,
        force=False,
        dirs=dirs,
    )

    assert result["samples_converted"] == 2
    index_path = dirs["indices"] / "klei_fsb_conversion_index.csv"
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert [row["sample_index"] for row in rows] == ["1", "2"]
    assert [row["sample_name"] for row in rows] == ["alpha", "beta"]
    assert {Path(row["output"]).name for row in rows} == {"1_alpha.wav", "2_beta.wav"}
    assert {row["status"] for row in rows} == {"converted"}

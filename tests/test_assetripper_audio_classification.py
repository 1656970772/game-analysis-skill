import csv
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "organize_assetripper_audio_resources.py"
CONFIG = ROOT / "references" / "assetripper-audio-classification.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("assetripper_audio", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assets_root(study_root: Path) -> Path:
    return study_root / "0.原始导出" / "AssetRipper" / "ExportedProject" / "Assets"


def _command(study_root: Path, config: Path = CONFIG) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "--study-root",
        str(study_root),
        "--config",
        str(config),
    ]


def test_audio_classification_contract_is_configuration_driven() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["classified_root"] == "1.分类资源/音频/AssetRipper"
    assert config["index_root"] == "4.临时目录/中间索引/AssetRipper音频分类"
    assert set(config["audio_extensions"]) == {
        ".wav",
        ".ogg",
        ".mp3",
        ".flac",
        ".aiff",
        ".aif",
        ".bank",
    }
    assert "292" not in SCRIPT.read_text(encoding="utf-8")


def test_audio_resources_preserve_relative_paths_are_pure_and_idempotent(
    tmp_path: Path,
) -> None:
    study_root = tmp_path / "中文 study root"
    assets_root = _assets_root(study_root)
    fixtures = {
        "Audio/音乐/开场 主题.ogg": b"OggS-fixture",
        "Audio/SFX/击中.wav": b"RIFF-fixture",
        "Banks/Main.bank": b"BANK-fixture",
    }
    for relative_path, payload in fixtures.items():
        path = assets_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        Path(f"{path}.meta").write_text("metadata", encoding="utf-8")
    (assets_root / "Audio" / "metadata.json").write_text("{}", encoding="utf-8")

    first = subprocess.run(
        _command(study_root), capture_output=True, text=True, encoding="utf-8"
    )
    assert first.returncode == 0, first.stderr

    index_root = study_root / "4.临时目录" / "中间索引" / "AssetRipper音频分类"
    summary_path = index_root / "音频分类摘要.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["source_candidate_count"] == 3
    assert summary["classified_file_count"] == 3
    assert summary["source_unchanged"] is True
    assert all(summary["reconciliation"].values())
    assert summary["missing_count"] == 0
    assert summary["extra_count"] == 0
    assert summary["impure_count"] == 0
    assert (index_root / "音频分类错误.jsonl").read_text(encoding="utf-8") == ""

    classified_root = study_root / "1.分类资源" / "音频" / "AssetRipper"
    for relative_path, source_payload in fixtures.items():
        destination = classified_root / relative_path
        assert destination.read_bytes() == source_payload
    classified_files = [path for path in classified_root.rglob("*") if path.is_file()]
    assert {path.suffix.lower() for path in classified_files} <= set(
        json.loads(CONFIG.read_text(encoding="utf-8"))["audio_extensions"]
    )

    with (index_root / "音频分类索引.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        first_rows = list(csv.DictReader(handle))
    assert [row["source_relative_path"] for row in first_rows] == sorted(
        fixtures, key=lambda value: (value.casefold(), value)
    )
    manifest_hash = summary["source_snapshot_before"]["manifest_sha256"]

    second = subprocess.run(
        _command(study_root), capture_output=True, text=True, encoding="utf-8"
    )
    assert second.returncode == 0, second.stderr
    rerun = json.loads(summary_path.read_text(encoding="utf-8"))
    assert rerun["created_count"] == 0
    assert rerun["reused_count"] == 3
    assert rerun["source_snapshot_before"]["manifest_sha256"] == manifest_hash
    assert rerun["classified_snapshot"]["manifest_sha256"] == manifest_hash


def test_hardlink_failure_falls_back_to_copy(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    study_root = tmp_path / "study"
    source = _assets_root(study_root) / "Audio" / "fallback.ogg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"OggS-copy-fallback")

    def fail_hardlink(_source, _destination):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(module.os, "link", fail_hardlink)
    summary = module.classify_audio_resources(study_root, CONFIG)
    destination = study_root / "1.分类资源" / "音频" / "AssetRipper" / "Audio" / "fallback.ogg"
    assert summary["ok"] is True
    assert summary["copied_count"] == 1
    assert summary["hardlinked_count"] == 0
    assert destination.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, destination)


def test_conflict_is_reported_and_not_overwritten(tmp_path: Path) -> None:
    study_root = tmp_path / "study"
    source = _assets_root(study_root) / "Audio" / "conflict.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source-audio")
    assert subprocess.run(_command(study_root), capture_output=True).returncode == 0

    destination = study_root / "1.分类资源" / "音频" / "AssetRipper" / "Audio" / "conflict.wav"
    destination.unlink()
    destination.write_bytes(b"existing-different")
    source_before = source.read_bytes()
    rerun = subprocess.run(
        _command(study_root), capture_output=True, text=True, encoding="utf-8"
    )
    assert rerun.returncode == 1
    summary = json.loads(
        (
            study_root
            / "4.临时目录"
            / "中间索引"
            / "AssetRipper音频分类"
            / "音频分类摘要.json"
        ).read_text(encoding="utf-8")
    )
    assert summary["ok"] is False
    assert summary["conflict_count"] == 1
    assert destination.read_bytes() == b"existing-different"
    assert source.read_bytes() == source_before


def test_path_escape_and_source_output_overlap_are_rejected(tmp_path: Path) -> None:
    study_root = tmp_path / "study"
    assets_root = _assets_root(study_root)
    assets_root.mkdir(parents=True)

    escape_config = tmp_path / "escape.json"
    escape = json.loads(CONFIG.read_text(encoding="utf-8"))
    escape["source_assets"] = "../outside"
    escape_config.write_text(json.dumps(escape), encoding="utf-8")
    escaped = subprocess.run(
        _command(study_root, escape_config),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert escaped.returncode == 1
    assert "UnsafePathError" in escaped.stderr

    overlap_config = tmp_path / "overlap.json"
    overlap = json.loads(CONFIG.read_text(encoding="utf-8"))
    overlap["classified_root"] = (
        "0.原始导出/AssetRipper/ExportedProject/Assets/organized"
    )
    overlap_config.write_text(json.dumps(overlap), encoding="utf-8")
    overlapping = subprocess.run(
        _command(study_root, overlap_config),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert overlapping.returncode == 1
    assert "must not overlap" in overlapping.stderr


def test_reparse_source_is_rejected(tmp_path: Path, monkeypatch) -> None:
    study_root = tmp_path / "study"
    real_assets = study_root / "real-assets"
    real_assets.mkdir(parents=True)
    (real_assets / "sample.ogg").write_bytes(b"OggS")
    assets_root = _assets_root(study_root)
    assets_root.parent.mkdir(parents=True)
    try:
        assets_root.symlink_to(real_assets, target_is_directory=True)
    except OSError as exc:
        # Some Windows hosts deny test-created symlinks. Exercise the same
        # rejection branch deterministically instead of weakening the contract.
        assets_root.mkdir()
        module = _load_module()
        original_is_reparse = module._is_reparse
        monkeypatch.setattr(
            module,
            "_is_reparse",
            lambda path: Path(path) == assets_root or original_is_reparse(path),
        )
        with pytest.raises(module.UnsafePathError, match="reparse"):
            module.classify_audio_resources(study_root, CONFIG)
        assert not (study_root / "1.分类资源" / "音频" / "AssetRipper").exists()
        return

    result = subprocess.run(
        _command(study_root), capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 1
    assert "reparse" in result.stderr.lower()
    assert not (study_root / "1.分类资源" / "音频" / "AssetRipper").exists()

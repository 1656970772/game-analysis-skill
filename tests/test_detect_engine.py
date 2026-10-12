from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from detect_engine import detect  # noqa: E402


def test_detects_nested_unreal_packaged_layout(tmp_path: Path) -> None:
    root = tmp_path / "Wandering Sword"
    paks = root / "Wandering_Sword" / "Content" / "Paks"
    binaries = root / "Wandering_Sword" / "Binaries" / "Win64"
    redist = root / "Engine" / "Extras" / "Redist" / "en-us"
    paks.mkdir(parents=True)
    binaries.mkdir(parents=True)
    redist.mkdir(parents=True)

    (root / "JH.exe").write_bytes(b"launcher")
    (paks / "Wandering_Sword-WindowsNoEditor.pak").write_bytes(b"pak")
    (binaries / "JH-Win64-Shipping.exe").write_bytes(b"exe")
    (redist / "UE4PrereqSetup_x64.exe").write_bytes(b"ue4")

    report = detect(root, max_depth=8, max_files=100)

    assert report["results"]
    assert report["results"][0]["engine"] == "Unreal Engine"
    markers = report["results"][0]["markers"]
    assert any(marker["path"].endswith("Content/Paks/Wandering_Sword-WindowsNoEditor.pak") for marker in markers)


def test_detects_klei_dst_packaged_layout(tmp_path: Path) -> None:
    root = tmp_path / "Don't Starve Together"
    (root / "bin64").mkdir(parents=True)
    (root / "data" / "databundles").mkdir(parents=True)
    (root / "data" / "anim").mkdir(parents=True)
    (root / "data" / "sound").mkdir(parents=True)

    (root / "bin64" / "dontstarve_steam_x64.exe").write_bytes(b"exe")
    (root / "data" / "databundles" / "scripts.zip").write_bytes(b"zip")
    (root / "data" / "databundles" / "images.zip").write_bytes(b"zip")
    (root / "data" / "anim" / "wilson.zip").write_bytes(b"zip")
    (root / "data" / "anim" / "wilson.tex").write_bytes(b"KTEX")
    (root / "data" / "anim" / "wilson.dyn").write_bytes(b"dyn")
    (root / "data" / "sound" / "sfx.fsb").write_bytes(b"FSB5")
    (root / "data" / "sound" / "sfx.fev").write_bytes(b"FEV")

    report = detect(root, max_depth=8, max_files=100)

    assert report["results"]
    assert report["results"][0]["engine"] == "Klei / Don't Starve Together"
    markers = report["results"][0]["markers"]
    assert any(marker["path"] == "data/databundles/scripts.zip" for marker in markers)
    assert any(marker["path"] == "bin64/dontstarve_steam_x64.exe" for marker in markers)

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

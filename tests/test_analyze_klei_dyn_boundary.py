from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_klei_dyn_boundary import analyze_dyn_boundary  # noqa: E402


def test_analyzes_dyn_headers_and_anim_dynamic_matches(tmp_path: Path) -> None:
    study = tmp_path / "DST Study"
    dyn_root = study / "0.原始导出" / "KleiDST" / "dynamic_dyn" / "data" / "anim" / "dynamic"
    anim_dynamic = (
        study
        / "0.原始导出"
        / "KleiDST"
        / "packages_extracted"
        / "data"
        / "databundles"
        / "anim_dynamic"
        / "anim"
        / "dynamic"
    )
    game_root = tmp_path / "Game"
    exe = game_root / "bin64" / "dontstarve_steam_x64.exe"
    dyn_root.mkdir(parents=True)
    anim_dynamic.mkdir(parents=True)
    exe.parent.mkdir(parents=True)

    header = bytes.fromhex("939492c585dd8c940744928e91858f90")
    (dyn_root / "wilson_formal.dyn").write_bytes(header + bytes(range(64)))
    (dyn_root / "wilson_shadow.dyn").write_bytes(header + bytes(range(63, -1, -1)))
    (anim_dynamic / "wilson_formal.zip").write_bytes(b"PK\x03\x04")
    exe.write_bytes(b"load_dynamic_anim\x00anim_dynamic\x00.dyn\x00")

    result = analyze_dyn_boundary(study, game_root=game_root)

    assert result["dyn_files"] == 2
    assert result["matched_anim_dynamic_zip"] == 1
    assert result["header_clusters"][0]["count"] == 2
    assert result["exe_string_hits"]
    assert (study / "4.临时目录" / "中间索引" / "KleiDST" / "dyn_boundary_inventory.csv").exists()
    summary = study / "4.临时目录" / "中间索引" / "KleiDST" / "dyn_boundary_inventory_summary.json"
    assert json.loads(summary.read_text(encoding="utf-8"))["dyn_files"] == 2

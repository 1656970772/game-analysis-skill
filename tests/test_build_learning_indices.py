from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from build_learning_indices import build_indices  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "asset_id",
        "source_file",
        "path_id",
        "asset_type",
        "asset_name",
        "container_path",
        "export_kind",
        "export_path",
        "width",
        "height",
        "status",
        "remarks",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_build_learning_indices_writes_learning_outputs(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    unitypy_index = study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_csv(
        unitypy_index,
        [
            {
                "asset_id": "sprite",
                "source_file": "Game_Data/resources.assets",
                "path_id": "1",
                "asset_type": "Sprite",
                "asset_name": "UI_Menu_Window_01",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/UI_Menu_Window_01.png",
                "width": "96",
                "height": "48",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "atlas",
                "source_file": "Game_Data/resources.assets",
                "path_id": "2",
                "asset_type": "Texture2D",
                "asset_name": "SharedAtlas",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Texture2D/SharedAtlas.png",
                "width": "2048",
                "height": "2048",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "shader",
                "source_file": "Game_Data/resources.assets",
                "path_id": "3",
                "asset_type": "Shader",
                "asset_name": "CharacterRim",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/Shader/CharacterRim.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "audio",
                "source_file": "Game_Data/resources.assets",
                "path_id": "4",
                "asset_type": "AudioClip",
                "asset_name": "HitHeavy",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "",
                "width": "",
                "height": "",
                "status": "indexed_audio_metadata",
                "remarks": "",
            },
            {
                "asset_id": "anim",
                "source_file": "Game_Data/resources.assets",
                "path_id": "5",
                "asset_type": "AnimationClip",
                "asset_name": "Hero_Attack_01",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/AnimationClip/Hero_Attack_01.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            },
        ],
    )
    cs_path = study_root / "3.代码" / "IL导出" / "Assembly-CSharp" / "HeroController.cs"
    cs_path.parent.mkdir(parents=True)
    cs_path.write_text(
        "namespace Forestrike.Characters;\npublic class HeroController {}\npublic enum HeroState { Idle }\n",
        encoding="utf-8",
    )

    summary = build_indices(study_root)
    output_root = study_root / "4.临时目录" / "中间索引" / "学习输出"

    size_rows = read_rows(output_root / "美术资源尺寸分布.csv")
    assert any(row["width"] == "96" and row["height"] == "48" for row in size_rows)
    assert any(row["width"] == "2048" and row["height"] == "2048" for row in size_rows)
    assert read_rows(output_root / "渲染资源索引.csv")[0]["asset_name"] == "CharacterRim"
    assert read_rows(output_root / "音频资源索引.csv")[0]["asset_name"] == "HitHeavy"
    assert read_rows(output_root / "动画资源索引.csv")[0]["asset_name"] == "Hero_Attack_01"
    assert read_rows(output_root / "代码命名空间索引.csv")[0]["namespace"] == "Forestrike.Characters"
    assert (output_root / "命名规范统计.csv").exists()
    assert json.loads((output_root / "学习输出摘要.json").read_text(encoding="utf-8"))["unitypy_index_count"] == 1
    assert summary["output_root"] == str(output_root)

from __future__ import annotations

import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from organize_unitypy_project_export import (  # noqa: E402
    classify_asset,
    normalize_sequence_group,
    organize,
    parse_sequence_name,
)


def write_index(path: Path, rows: list[dict[str, str]]) -> None:
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
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_parse_sequence_name_uses_game_action_and_one_based_output() -> None:
    assert parse_sequence_name("thug-walk-000") == ("thug", "walk", 0)
    assert parse_sequence_name("thug-floorkick 7") == ("thug", "floorkick", 7)
    assert parse_sequence_name("Hero_Flip_1") == ("Hero", "Flip", 1)
    assert normalize_sequence_group("wall-run") == "wall_run"


def test_classify_asset_prefers_actual_name_domains() -> None:
    assert classify_asset("uicharOnyaLaugh", "Sprite")[0] == "图片/角色立绘"
    assert classify_asset("UI_Menu_Window_01", "Sprite")[0] == "图片/UI"
    assert classify_asset("Jungle_waterfallSheet_0", "Sprite")[0] == "图片/场景"
    assert classify_asset("Foresight_Smoke01", "Sprite")[0] == "特效/粒子贴图"


def test_organize_copies_sequence_frames_with_action_names(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    image_dir = unitypy / "images" / "Sprite"
    image_dir.mkdir(parents=True)
    for name in ["thug-walk-000", "thug-walk-001", "thug-walk-002"]:
        (image_dir / f"{name}.png").write_bytes(b"png")

    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": f"id{i}",
                "source_file": "Game_Data/resources.assets",
                "path_id": str(i),
                "asset_type": "Sprite",
                "asset_name": f"thug-walk-00{i}",
                "container_path": "",
                "export_kind": "png",
                "export_path": f"images/Sprite/thug-walk-00{i}.png",
                "width": "28",
                "height": "39",
                "status": "exported",
                "remarks": "",
            }
            for i in range(3)
        ],
    )

    study_root = tmp_path / "Forestrike"
    summary = organize(index, unitypy, study_root, project_name="Forestrike")

    sequence_dir = study_root / "1.分类资源" / "图片" / "序列帧" / "thug" / "walk"
    assert (sequence_dir / "walk_01.png").read_bytes() == b"png"
    assert (sequence_dir / "walk_02.png").exists()
    assert (sequence_dir / "walk_03.png").exists()
    assert summary["sequence_frame_count"] == 3
    rows = list(
        csv.DictReader(
            (study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织索引.csv").open(encoding="utf-8-sig")
        )
    )
    assert rows[0]["original_asset_name"] == "thug-walk-000"
    assert rows[0]["output_relative_path"].endswith("walk/walk_01.png")


def test_organize_routes_learning_asset_buckets_and_audio_boundaries(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    image_dir = unitypy / "images" / "Sprite"
    texture_dir = unitypy / "images" / "Texture2D"
    audio_dir = unitypy / "audio" / "DangerAudio"
    image_dir.mkdir(parents=True)
    texture_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    (image_dir / "uicharOnyaLaugh.png").write_bytes(b"character")
    (image_dir / "UI_Menu_Window_01.png").write_bytes(b"ui")
    (image_dir / "Jungle_waterfallSheet_0.png").write_bytes(b"scene")
    (image_dir / "WeaponSwordIcon.png").write_bytes(b"prop")
    (texture_dir / "SharedAtlas.png").write_bytes(b"atlas")
    (audio_dir / "swing.wav").write_bytes(b"wav")

    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "char",
                "source_file": "Game_Data/resources.assets",
                "path_id": "1",
                "asset_type": "Sprite",
                "asset_name": "uicharOnyaLaugh",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/uicharOnyaLaugh.png",
                "width": "615",
                "height": "320",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "ui",
                "source_file": "Game_Data/resources.assets",
                "path_id": "2",
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
                "asset_id": "scene",
                "source_file": "Game_Data/resources.assets",
                "path_id": "3",
                "asset_type": "Sprite",
                "asset_name": "Jungle_waterfallSheet_0",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/Jungle_waterfallSheet_0.png",
                "width": "2048",
                "height": "1150",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "prop",
                "source_file": "Game_Data/resources.assets",
                "path_id": "4",
                "asset_type": "Sprite",
                "asset_name": "WeaponSwordIcon",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/WeaponSwordIcon.png",
                "width": "64",
                "height": "64",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "atlas",
                "source_file": "Game_Data/resources.assets",
                "path_id": "5",
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
                "asset_id": "audio_meta",
                "source_file": "Game_Data/resources.assets",
                "path_id": "6",
                "asset_type": "AudioClip",
                "asset_name": "DangerAudio",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "",
                "width": "",
                "height": "",
                "status": "indexed_audio_metadata",
                "remarks": "metadata only",
            },
            {
                "asset_id": "audio_sample",
                "source_file": "Game_Data/resources.assets",
                "path_id": "7",
                "asset_type": "AudioClip",
                "asset_name": "DangerAudio",
                "container_path": "",
                "export_kind": "audio",
                "export_path": "audio/DangerAudio/swing.wav",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            },
        ],
    )

    study_root = tmp_path / "Forestrike"
    summary = organize(index, unitypy, study_root, project_name="Forestrike")

    assert (study_root / "1.分类资源" / "图片" / "角色立绘" / "uicharOnyaLaugh" / "uicharOnyaLaugh.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu_Window_01.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "场景" / "Jungle" / "Jungle_waterfallSheet_0.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "道具" / "WeaponSwordIcon" / "WeaponSwordIcon.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "图集" / "SharedAtlas" / "SharedAtlas.png").exists()
    assert (study_root / "1.分类资源" / "音频" / "DangerAudio" / "swing.wav").exists()
    assert not (study_root / "1.分类资源" / "音频元数据").exists()
    assert summary["category_counts"]["音频"] == 1


def test_organize_does_not_copy_metadata_json_to_classified_resources(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    metadata_dir = unitypy / "metadata" / "MonoBehaviour"
    material_dir = unitypy / "metadata" / "Material"
    image_dir = unitypy / "images" / "Sprite"
    metadata_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    (metadata_dir / "Behaviour.json").write_text("{}", encoding="utf-8")
    (material_dir / "Mat.json").write_text("{}", encoding="utf-8")
    (image_dir / "Hero_Flip_1.png").write_bytes(b"png")

    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "behaviour",
                "source_file": "Game_Data/resources.assets",
                "path_id": "1",
                "asset_type": "MonoBehaviour",
                "asset_name": "Behaviour",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/MonoBehaviour/Behaviour.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "mat",
                "source_file": "Game_Data/resources.assets",
                "path_id": "2",
                "asset_type": "Material",
                "asset_name": "Mat",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/Material/Mat.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "sprite",
                "source_file": "Game_Data/resources.assets",
                "path_id": "3",
                "asset_type": "Sprite",
                "asset_name": "Hero_Flip_1",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/Hero_Flip_1.png",
                "width": "32",
                "height": "32",
                "status": "exported",
                "remarks": "",
            },
        ],
    )

    study_root = tmp_path / "Forestrike"
    summary = organize(index, unitypy, study_root, project_name="Forestrike")

    classified_files = list((study_root / "1.分类资源").rglob("*"))
    assert not [path for path in classified_files if path.is_file() and path.suffix == ".json"]
    assert (study_root / "1.分类资源" / "图片" / "序列帧" / "Hero" / "flip" / "flip_01.png").exists()
    assert summary["skipped_metadata_count"] == 2

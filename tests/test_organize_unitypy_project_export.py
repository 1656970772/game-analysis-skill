from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import organize_unitypy_project_export as organizer  # noqa: E402
from organize_unitypy_project_export import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_CONTRACT,
    UnsafePathError,
    classify_asset,
    normalize_sequence_group,
    normalize_category_roots,
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


def fixture_row(
    *,
    asset_id: str,
    asset_name: str,
    export_path: str,
    asset_type: str = "Sprite",
    export_kind: str = "png",
    path_id: str = "1",
    container_path: str = "",
) -> dict[str, str]:
    return {
        "asset_id": asset_id,
        "source_file": "Fixture_Data/resources.assets",
        "path_id": path_id,
        "asset_type": asset_type,
        "asset_name": asset_name,
        "container_path": container_path,
        "export_kind": export_kind,
        "export_path": export_path,
        "width": "32",
        "height": "32",
        "status": "exported",
        "remarks": "",
    }


def write_custom_config(tmp_path: Path, mutate: object) -> Path:
    value = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    mutate(value)
    path = tmp_path / "organization-config.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def install_one_cleanup_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    """Fail one exact-generation cleanup after backup quarantine."""

    state = {"denied": False}
    if os.name == "nt":
        original_delete = organizer._windows_delete_binary_handle

        def failing_delete(handle: object) -> None:
            name = organizer._windows_binary_handle_path(handle).name
            if ".ga-cleanup-orphan-" in name and not state["denied"]:
                state["denied"] = True
                raise PermissionError("synthetic cleanup denial")
            original_delete(handle)

        monkeypatch.setattr(organizer, "_windows_delete_binary_handle", failing_delete)
    else:
        original_unlink = organizer._lease_unlink_owned_generation

        def failing_unlink(
            lease: organizer.StableDirectoryLease,
            name: str,
            expected: organizer.ArtifactPathState | None,
        ) -> None:
            if ".ga-cleanup-orphan-" in name and not state["denied"]:
                state["denied"] = True
                raise PermissionError("synthetic cleanup denial")
            original_unlink(lease, name, expected)

        monkeypatch.setattr(organizer, "_lease_unlink_owned_generation", failing_unlink)
    return state


def test_parse_sequence_name_uses_game_action_and_one_based_output() -> None:
    assert parse_sequence_name("thug-walk-000") == ("thug", "walk", 0)
    assert parse_sequence_name("thug-floorkick 7") == ("thug", "floorkick", 7)
    assert parse_sequence_name("Hero_Flip_1") == ("Hero", "Flip", 1)
    assert normalize_sequence_group("wall-run") == "wall_run"


def test_classify_asset_prefers_actual_name_domains() -> None:
    assert classify_asset("Hero_Portrait_Laugh", "Sprite")[0] == "图片/角色立绘"
    assert classify_asset("UI_Menu_Window_01", "Sprite")[0] == "图片/UI"
    assert classify_asset("Scene_Waterfall_Sheet_0", "Sprite")[0] == "图片/场景"
    assert classify_asset("Impact_Smoke_01", "Sprite")[0] == "特效/粒子贴图"


def test_runtime_rules_accept_safe_project_local_index_subtree(tmp_path: Path) -> None:
    config_path = write_custom_config(
        tmp_path,
        lambda config: config["paths"].__setitem__(
            "index_root",
            "4.临时目录/中间索引/UnityPy组织音频/批次A",
        ),
    )

    rules = organizer.load_runtime_rules(config_path)

    assert rules.config["paths"]["index_root"] == "4.临时目录/中间索引/UnityPy组织音频/批次A"


@pytest.mark.parametrize(
    "index_root",
    [
        "4.临时目录/中间索引",
        "C:/临时目录/中间索引/UnityPy组织音频",
        "4.临时目录/中间索引/../越界",
        "4.临时目录\\中间索引\\UnityPy组织音频",
        "4.临时目录/其他索引/UnityPy组织音频",
        "1.分类资源/UnityPy组织音频",
        "4.临时目录/中间索引/UnityPy组织音频.",
        "4.临时目录/中间索引/CON",
    ],
)
def test_runtime_rules_reject_unsafe_or_out_of_contract_index_root(
    tmp_path: Path,
    index_root: str,
) -> None:
    config_path = write_custom_config(
        tmp_path,
        lambda config: config["paths"].__setitem__("index_root", index_root),
    )

    with pytest.raises((UnsafePathError, ValueError)):
        organizer.load_runtime_rules(config_path)


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

    study_root = tmp_path / "FixtureGame"
    summary = organize(index, unitypy, study_root, project_name="FixtureGame")

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
    (image_dir / "Hero_Portrait_Laugh.png").write_bytes(b"character")
    (image_dir / "UI_Menu_Window_01.png").write_bytes(b"ui")
    (image_dir / "Scene_Waterfall_Sheet_0.png").write_bytes(b"scene")
    (image_dir / "Weapon_Sword_Icon.png").write_bytes(b"prop")
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
                "asset_name": "Hero_Portrait_Laugh",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/Hero_Portrait_Laugh.png",
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
                "asset_name": "Scene_Waterfall_Sheet_0",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/Scene_Waterfall_Sheet_0.png",
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
                "asset_name": "Weapon_Sword_Icon",
                "container_path": "",
                "export_kind": "png",
                "export_path": "images/Sprite/Weapon_Sword_Icon.png",
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

    study_root = tmp_path / "FixtureGame"
    summary = organize(index, unitypy, study_root, project_name="FixtureGame")

    assert (study_root / "1.分类资源" / "图片" / "角色立绘" / "Hero" / "Hero_Portrait_Laugh.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu_Window_01.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "场景" / "Scene" / "Scene_Waterfall_Sheet_0.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "道具" / "Weapon" / "Weapon_Sword_Icon.png").exists()
    assert (study_root / "1.分类资源" / "图片" / "图集" / "SharedAtlas" / "SharedAtlas.png").exists()
    assert (study_root / "1.分类资源" / "音频" / "DangerAudio" / "swing.wav").exists()
    assert not (study_root / "1.分类资源" / "音频元数据").exists()
    assert summary["category_counts"]["音频"] == 1


def test_organize_does_not_copy_metadata_json_to_classified_resources(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    metadata_dir = unitypy / "metadata" / "MonoBehaviour"
    material_dir = unitypy / "metadata" / "Material"
    image_dir = unitypy / "animations"
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
                "export_path": "animations/Hero_Flip_1.png",
                "width": "32",
                "height": "32",
                "status": "exported",
                "remarks": "",
            },
        ],
    )

    study_root = tmp_path / "FixtureGame"
    summary = organize(index, unitypy, study_root, project_name="FixtureGame")

    classified_files = list((study_root / "1.分类资源").rglob("*"))
    assert not [path for path in classified_files if path.is_file() and path.suffix == ".json"]
    assert (study_root / "1.分类资源" / "图片" / "序列帧" / "Hero" / "flip" / "flip_01.png").exists()
    assert summary["skipped_metadata_count"] == 2


def test_model_tool_note_uses_current_project_name(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "mesh_1",
                "source_file": "Game_Data/resources.assets",
                "path_id": "1",
                "asset_type": "Mesh",
                "asset_name": "hero_mesh",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/Mesh/hero_mesh.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            }
        ],
    )
    study_root = tmp_path / "Amazing"

    organize(index, unitypy, study_root, project_name="Amazing")

    note = (study_root / "4.临时目录" / "模型工具需求.md").read_text(encoding="utf-8")
    assert "Amazing" in note
    assert "LegacyGame" not in note


def test_cli_can_route_model_tool_note_to_caller_owned_index_path(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "mesh_1",
                "source_file": "Game_Data/resources.assets",
                "path_id": "1",
                "asset_type": "Mesh",
                "asset_name": "hero_mesh",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/Mesh/hero_mesh.json",
                "width": "",
                "height": "",
                "status": "exported",
                "remarks": "",
            }
        ],
    )
    study_root = tmp_path / "Amazing"
    requirements_output = study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "模型工具需求.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "organize_unitypy_project_export.py"),
            "--index",
            str(index),
            "--unitypy-root",
            str(unitypy),
            "--study-root",
            str(study_root),
            "--project-name",
            "Amazing",
            "--requirements-output",
            str(requirements_output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert requirements_output.exists()
    assert "Amazing" in requirements_output.read_text(encoding="utf-8")
    assert not (study_root / "4.临时目录" / "模型工具需求.md").exists()


def test_organize_can_include_only_selected_category_roots(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payload_dir = unitypy / "payloads"
    payload_dir.mkdir(parents=True)
    (payload_dir / "ui.png").write_bytes(b"ui")
    (payload_dir / "smoke.png").write_bytes(b"smoke")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "ui",
                "source_file": "fixture.assets",
                "path_id": "1",
                "asset_type": "Sprite",
                "asset_name": "UI_Menu",
                "container_path": "",
                "export_kind": "png",
                "export_path": "payloads/ui.png",
                "width": "1",
                "height": "1",
                "status": "exported",
                "remarks": "",
            },
            {
                "asset_id": "vfx",
                "source_file": "fixture.assets",
                "path_id": "2",
                "asset_type": "Sprite",
                "asset_name": "SmokeParticle",
                "container_path": "",
                "export_kind": "png",
                "export_path": "payloads/smoke.png",
                "width": "1",
                "height": "1",
                "status": "exported",
                "remarks": "",
            },
        ],
    )
    study_root = tmp_path / "Fixture"

    summary = organize(
        index,
        unitypy,
        study_root,
        project_name="Fixture",
        include_category_roots={"图片"},
    )

    assert (study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu.png").read_bytes() == b"ui"
    assert not (study_root / "1.分类资源" / "特效").exists()
    assert summary["organized_count"] == 1
    assert summary["filtered_category_count"] == 1
    assert summary["category_counts"] == {"图片/UI": 1}
    assert summary["category_filter"]["include_roots"] == ["图片"]
    assert summary["category_filter"]["exclude_roots"] == []


def test_organize_can_exclude_selected_category_roots(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payload_dir = unitypy / "payloads"
    payload_dir.mkdir(parents=True)
    (payload_dir / "smoke.png").write_bytes(b"smoke")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            {
                "asset_id": "vfx",
                "source_file": "fixture.assets",
                "path_id": "1",
                "asset_type": "Sprite",
                "asset_name": "SmokeParticle",
                "container_path": "",
                "export_kind": "png",
                "export_path": "payloads/smoke.png",
                "width": "1",
                "height": "1",
                "status": "exported",
                "remarks": "",
            }
        ],
    )
    study_root = tmp_path / "Fixture"

    summary = organize(
        index,
        unitypy,
        study_root,
        project_name="Fixture",
        exclude_category_roots={"特效"},
    )

    assert not (study_root / "1.分类资源" / "特效").exists()
    assert summary["organized_count"] == 0
    assert summary["filtered_category_count"] == 1
    assert summary["filtered_category_counts"] == {"特效/粒子贴图": 1}
    assert summary["category_filter"]["include_roots"] == []
    assert summary["category_filter"]["exclude_roots"] == ["特效"]


@pytest.mark.parametrize("value", ["../图片", "图片/子目录", r"C:\图片", "..", "", "不存在"])
def test_category_root_filter_rejects_paths_and_unknown_roots(value: str) -> None:
    with pytest.raises(ValueError, match="invalid category root"):
        normalize_category_roots([value], "include_category_roots")


def test_sequence_ordinals_are_stable_from_sorted_source_frames(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    (payloads / "Mage_Attack_10.png").write_bytes(b"frame-10")
    (payloads / "Mage_Attack_2.png").write_bytes(b"frame-2")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            fixture_row(asset_id="late", asset_name="Mage_Attack_10", export_path="images/Mage_Attack_10.png", path_id="10"),
            fixture_row(asset_id="early", asset_name="Mage_Attack_2", export_path="images/Mage_Attack_2.png", path_id="2"),
        ],
    )

    study_root = tmp_path / "Study"
    summary = organize(index, unitypy, study_root, project_name="Fixture")

    sequence_dir = study_root / "1.分类资源" / "图片" / "序列帧" / "Mage" / "attack"
    assert (sequence_dir / "attack_01.png").read_bytes() == b"frame-2"
    assert (sequence_dir / "attack_02.png").read_bytes() == b"frame-10"
    assert summary["sequence_frame_count"] == 2


def test_single_numbered_action_without_sequence_context_stays_normal(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    (payloads / "Icon_Attack_01.png").write_bytes(b"ordinary-icon")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="icon", asset_name="Icon_Attack_01", export_path="images/Icon_Attack_01.png")])

    study_root = tmp_path / "Study"
    summary = organize(index, unitypy, study_root, project_name="Fixture")

    assert summary["sequence_frame_count"] == 0
    assert not (study_root / "1.分类资源" / "图片" / "序列帧").exists()
    assert (study_root / "1.分类资源" / "图片" / "UI" / "Icon" / "Icon_Attack_01.png").read_bytes() == b"ordinary-icon"


def test_config_contains_no_project_specific_names_and_can_be_extended(tmp_path: Path) -> None:
    implementation = (SCRIPT_DIR / "organize_unitypy_project_export.py").read_text(encoding="utf-8")
    default_config = DEFAULT_CONFIG.read_text(encoding="utf-8")
    for forbidden in ("Forestrike", "Onya", "Ogai"):
        assert forbidden not in implementation
        assert forbidden not in default_config

    def add_project_rule(config: dict[str, object]) -> None:
        rules = config["image_classification_rules"]
        assert isinstance(rules, list)
        rules.insert(0, {"id": "fixture_mage", "category": "图片/角色立绘", "patterns": ["(^|_)mymage(_|$)"]})

    config_path = write_custom_config(tmp_path, add_project_rule)
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    (payloads / "MyMage_Card.png").write_bytes(b"mage")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="mage", asset_name="MyMage_Card", export_path="images/MyMage_Card.png")])
    study_root = tmp_path / "Study"

    summary = organize(index, unitypy, study_root, project_name="Fixture", config_path=config_path)

    assert summary["ok"] is True
    assert (study_root / "1.分类资源" / "图片" / "角色立绘" / "MyMage" / "MyMage_Card.png").exists()


def test_repeat_run_reuses_hardlink_preserves_mtime_and_stabilizes_artifacts(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    source = payloads / "UI_Menu.png"
    source.write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    study_root = tmp_path / "Study"

    first = organize(index, unitypy, study_root, project_name="Fixture")
    destination = study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu.png"
    first_mtime = destination.stat().st_mtime_ns
    assert first["created_count"] == 1
    assert first["hardlinked_count"] == 1
    assert os.path.samefile(source, destination)

    second = organize(index, unitypy, study_root, project_name="Fixture")
    assert second["created_count"] == 0
    assert second["reused_count"] == 1
    assert destination.stat().st_mtime_ns == first_mtime
    index_root = study_root / "4.临时目录" / "中间索引" / "UnityPy组织"
    artifact_paths = sorted(path for path in index_root.iterdir() if path.is_file())
    stable = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifact_paths}

    third = organize(index, unitypy, study_root, project_name="Fixture")
    assert third["reused_count"] == 1
    assert destination.stat().st_mtime_ns == first_mtime
    assert stable == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifact_paths}
    assert not list(study_root.rglob("*.partial"))


def test_copy_fallback_is_atomic_and_hash_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    source = payloads / "UI_Panel.png"
    source.write_bytes(b"panel")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="panel", asset_name="UI_Panel", export_path="images/UI_Panel.png")])

    def hardlink_unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture hardlink unavailable")

    hardlink_helper = (
        "_windows_link_source_handle_to_lease"
        if os.name == "nt"
        else "_posix_link_source_handle_to_lease"
    )
    monkeypatch.setattr(organizer, hardlink_helper, hardlink_unavailable)
    study_root = tmp_path / "Study"
    summary = organize(index, unitypy, study_root, project_name="Fixture")
    destination = study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Panel.png"

    assert summary["ok"] is True
    assert summary["copied_count"] == 1
    assert summary["hardlinked_count"] == 0
    assert destination.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, destination)
    assert not list(study_root.rglob("*.partial"))


def test_different_content_collision_uses_one_stable_identity_suffix(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    (payloads / "UI_Menu.png").write_bytes(b"new")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    study_root = tmp_path / "Study"
    base = study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu.png"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"pre-existing")

    first = organize(index, unitypy, study_root, project_name="Fixture")
    resource_index = study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织索引.csv"
    row = next(csv.DictReader(resource_index.open(encoding="utf-8-sig")))
    collision_path = study_root / Path(row["output_relative_path"])
    assert first["conflict_count"] == 1
    assert first["resolved_conflict_count"] == 1
    assert row["collision_status"] == "resolved_stable_suffix"
    assert "__" in collision_path.name
    assert collision_path.read_bytes() == b"new"

    second = organize(index, unitypy, study_root, project_name="Fixture")
    row2 = next(csv.DictReader(resource_index.open(encoding="utf-8-sig")))
    assert row2["output_relative_path"] == row["output_relative_path"]
    assert second["reused_count"] == 1
    assert len(list(base.parent.glob("UI_Menu*.png"))) == 2
    assert not list(base.parent.glob("*__02.png"))


def test_mismatch_at_stable_collision_path_fails_closed(tmp_path: Path) -> None:
    def force_copy(config: dict[str, object]) -> None:
        config["transfer_methods"] = ["copy"]

    config_path = write_custom_config(tmp_path, force_copy)
    unitypy = tmp_path / "UnityPy"
    payloads = unitypy / "images"
    payloads.mkdir(parents=True)
    source = payloads / "UI_Menu.png"
    source.write_bytes(b"version-one")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    study_root = tmp_path / "Study"
    base = study_root / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu.png"
    base.parent.mkdir(parents=True)
    base.write_bytes(b"occupied")
    first = organize(index, unitypy, study_root, project_name="Fixture", config_path=config_path)
    assert first["ok"] is True
    resource_index = study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织索引.csv"
    first_row = next(csv.DictReader(resource_index.open(encoding="utf-8-sig")))
    stable_path = study_root / Path(first_row["output_relative_path"])
    assert stable_path.read_bytes() == b"version-one"

    source.write_bytes(b"version-two")
    second = organize(index, unitypy, study_root, project_name="Fixture", config_path=config_path)

    assert second["ok"] is False
    assert second["conflict_count"] == 1
    assert second["failed_count"] == 1
    assert stable_path.read_bytes() == b"version-one"
    assert len(list(base.parent.glob("UI_Menu*.png"))) == 2


def test_export_path_traversal_fails_before_source_read(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    (tmp_path / "outside.png").write_bytes(b"outside")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="escape", asset_name="UI_Escape", export_path="../outside.png")])
    study_root = tmp_path / "Study"

    with pytest.raises(UnsafePathError, match="canonical POSIX relative path"):
        organize(index, unitypy, study_root, project_name="Fixture")

    assert (tmp_path / "outside.png").read_bytes() == b"outside"
    assert not list((study_root / "1.分类资源").rglob("*.png"))
    assert not list(study_root.rglob("*.partial"))


def test_symlink_source_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "UI_Link.png").write_bytes(b"external")
    link = unitypy / "linked"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="link", asset_name="UI_Link", export_path="linked/UI_Link.png")])

    summary = organize(index, unitypy, tmp_path / "Study", project_name="Fixture")

    assert summary["ok"] is False
    assert summary["failed_count"] == 1


def test_reparse_component_detection_fails_closed_without_platform_symlink_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unitypy = tmp_path / "UnityPy"
    linked = unitypy / "linked"
    linked.mkdir(parents=True)
    (linked / "UI_Link.png").write_bytes(b"fixture")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="link", asset_name="UI_Link", export_path="linked/UI_Link.png")])
    original_is_reparse = organizer._is_reparse

    def simulated_reparse(path: Path) -> bool:
        return path == linked or original_is_reparse(path)

    monkeypatch.setattr(organizer, "_is_reparse", simulated_reparse)
    summary = organize(index, unitypy, tmp_path / "Study", project_name="Fixture")

    assert summary["ok"] is False
    assert summary["failed_count"] == 1
    assert not list((tmp_path / "Study" / "1.分类资源").rglob("*.png"))


def test_index_and_requirements_paths_fail_closed_outside_boundaries(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    outside_index = tmp_path / "outside.csv"
    write_index(outside_index, [])
    with pytest.raises(UnsafePathError, match="index must stay"):
        organize(outside_index, unitypy, tmp_path / "StudyA", project_name="Fixture")

    inside_index = unitypy / "all_resources_index.csv"
    write_index(inside_index, [])
    with pytest.raises(UnsafePathError, match="requirements_output"):
        organize(
            inside_index,
            unitypy,
            tmp_path / "StudyB",
            project_name="Fixture",
            requirements_output=tmp_path / "StudyB" / "2.报告" / "note.md",
        )


def test_contract_serialization_drift_is_rejected(tmp_path: Path) -> None:
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    contract["deterministic_serialization"]["json_sort_keys"] = False
    contract_path = tmp_path / "bad-contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])

    with pytest.raises(ValueError, match="deterministic_serialization"):
        organize(index, unitypy, tmp_path / "Study", project_name="Fixture", contract_path=contract_path)


@pytest.mark.parametrize("policy", ["artifact_policy", "control_input_policy"])
def test_contract_alias_and_snapshot_policy_drift_is_rejected(tmp_path: Path, policy: str) -> None:
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    contract[policy] = {}
    contract_path = tmp_path / "bad-contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    study = tmp_path / "Study"

    with pytest.raises(ValueError, match=policy):
        organize(index, unitypy, study, project_name="Fixture", contract_path=contract_path)

    assert not study.exists()


@pytest.mark.parametrize("input_kind", ["config", "contract"])
def test_contract_artifact_cannot_alias_control_input_before_any_write(tmp_path: Path, input_kind: str) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    study = tmp_path / "Study"
    alias = study / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织摘要.json"
    alias.parent.mkdir(parents=True)
    alias.write_bytes((DEFAULT_CONFIG if input_kind == "config" else DEFAULT_CONTRACT).read_bytes())
    before = (alias.read_bytes(), alias.stat().st_mtime_ns)
    kwargs = {f"{input_kind}_path": alias}

    with pytest.raises(UnsafePathError, match=f"aliases input {input_kind}"):
        organize(index, unitypy, study, project_name="Fixture", **kwargs)

    assert (alias.read_bytes(), alias.stat().st_mtime_ns) == before
    assert not (study / "1.分类资源").exists()
    assert not list(study.rglob("*.partial"))


def test_existing_hardlink_artifact_alias_to_config_is_rejected_before_write(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    config = tmp_path / "config.json"
    config.write_bytes(DEFAULT_CONFIG.read_bytes())
    study = tmp_path / "Study"
    alias = study / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织摘要.json"
    alias.parent.mkdir(parents=True)
    os.link(config, alias)
    before = (config.read_bytes(), config.stat().st_mtime_ns)

    with pytest.raises(UnsafePathError, match="aliases input config"):
        organize(index, unitypy, study, project_name="Fixture", config_path=config)

    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert not (study / "1.分类资源").exists()


def test_requirements_target_cannot_alias_contract_artifact_before_write(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    study = tmp_path / "Study"
    requirements = study / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织摘要.json"

    with pytest.raises(UnsafePathError, match="output artifacts"):
        organize(index, unitypy, study, project_name="Fixture", requirements_output=requirements)

    assert not study.exists()


@pytest.mark.parametrize("case", ["duplicate", "case_alias", "file_parent", "wrong_suffix"])
def test_contract_output_names_fail_closed_on_windows_aliases_and_type_mismatch(tmp_path: Path, case: str) -> None:
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    if case == "duplicate":
        contract["outputs"]["sequence_index"]["filename"] = contract["outputs"]["resource_index"]["filename"]
    elif case == "case_alias":
        contract["outputs"]["sequence_index"]["filename"] = "资源组织索引.CSV"
    elif case == "file_parent":
        contract["outputs"]["resource_index"]["filename"] = "nested/index.csv"
        contract["outputs"]["sequence_index"]["filename"] = "nested/index.csv/sequence.csv"
    else:
        contract["outputs"]["summary"]["filename"] = "资源组织摘要.csv"
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    study = tmp_path / "Study"

    with pytest.raises(ValueError, match="contract output"):
        organize(index, unitypy, study, project_name="Fixture", contract_path=contract_path)

    assert not study.exists()


@pytest.mark.parametrize("bad_fields", [["name", "name"], ["name", ""], ["name", 7]])
def test_contract_csv_fields_must_be_unique_non_empty_strings(tmp_path: Path, bad_fields: list[object]) -> None:
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    contract["outputs"]["resource_index"]["fields"] = bad_fields
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    study = tmp_path / "Study"

    with pytest.raises(ValueError, match="fields must contain unique non-empty strings"):
        organize(index, unitypy, study, project_name="Fixture", contract_path=contract_path)

    assert not study.exists()


def test_config_snapshot_drift_fails_before_writes_and_preserves_existing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "hero.png").write_bytes(b"hero")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="hero", asset_name="hero", export_path="images/hero.png")])
    config_path = tmp_path / "config.json"
    config_path.write_bytes(DEFAULT_CONFIG.read_bytes())
    study = tmp_path / "Study"
    baseline = organize(index, unitypy, study, project_name="Fixture", config_path=config_path)
    assert baseline["ok"] is True
    existing = {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    original_loader = organizer.load_runtime_rules

    def drifting_loader(value: Path | organizer.InputSnapshot) -> organizer.RuntimeRules:
        rules = original_loader(value)
        changed = json.loads(config_path.read_text(encoding="utf-8"))
        changed["main_character_candidate_patterns"] = ["^never-match$"]
        config_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        return rules

    monkeypatch.setattr(organizer, "load_runtime_rules", drifting_loader)
    with pytest.raises(organizer.InputChangedError, match="config changed"):
        organize(index, unitypy, study, project_name="Fixture", config_path=config_path)

    assert existing == {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    assert not list(study.rglob("*.partial"))


def test_contract_snapshot_drift_fails_before_writes_and_preserves_existing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    contract_path = tmp_path / "contract.json"
    contract_path.write_bytes(DEFAULT_CONTRACT.read_bytes())
    study = tmp_path / "Study"
    baseline = organize(index, unitypy, study, project_name="Fixture", contract_path=contract_path)
    assert baseline["ok"] is True
    existing = {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    original_loader = organizer.load_contract

    def drifting_loader(value: Path | organizer.InputSnapshot) -> dict[str, object]:
        parsed = original_loader(value)
        changed = json.loads(contract_path.read_text(encoding="utf-8"))
        changed["contract_id"] = "drifted-after-parse"
        contract_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        return parsed

    monkeypatch.setattr(organizer, "load_contract", drifting_loader)
    with pytest.raises(organizer.InputChangedError, match="contract changed"):
        organize(index, unitypy, study, project_name="Fixture", contract_path=contract_path)

    assert existing == {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    assert not list(study.rglob("*.partial"))


def test_config_drift_during_second_pass_aborts_staged_artifacts_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    config_path = tmp_path / "config.json"
    config_path.write_bytes(DEFAULT_CONFIG.read_bytes())
    study = tmp_path / "Study"
    baseline = organize(index, unitypy, study, project_name="Baseline", config_path=config_path)
    assert baseline["ok"] is True
    existing = {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    original_iter = organizer._iter_rows
    calls = 0

    def drifting_iter(path: Path, required_fields: list[str]):
        nonlocal calls
        calls += 1
        evidence = yield from original_iter(path, required_fields)
        if calls == 2:
            changed = json.loads(config_path.read_text(encoding="utf-8"))
            changed["main_character_candidate_patterns"] = ["^never-match$"]
            config_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        return evidence

    monkeypatch.setattr(organizer, "_iter_rows", drifting_iter)
    with pytest.raises(organizer.InputChangedError, match="config changed"):
        organize(index, unitypy, study, project_name="Would Replace Artifacts", config_path=config_path)

    assert existing == {
        path.relative_to(study).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in study.rglob("*")
        if path.is_file()
    }
    assert not list(study.rglob("*.partial"))


def test_source_index_drift_during_second_pass_can_leave_resource_but_never_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    original_iter = organizer._iter_rows
    calls = 0

    def drifting_iter(path: Path, required_fields: list[str]):
        nonlocal calls
        calls += 1
        evidence = yield from original_iter(path, required_fields)
        if calls == 2:
            path.write_bytes(path.read_bytes() + b"\n")
        return evidence

    monkeypatch.setattr(organizer, "_iter_rows", drifting_iter)
    study = tmp_path / "Study"
    summary = organize(index, unitypy, study, project_name="Fixture")

    assert summary["ok"] is False
    assert summary["source_index_unchanged"] is False
    assert summary["failed_count"] == 1
    assert (study / "1.分类资源" / "图片" / "UI" / "UI" / "UI_Menu.png").exists()
    persisted = json.loads(
        (study / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织摘要.json").read_text(encoding="utf-8")
    )
    assert persisted["ok"] is False


def test_outputs_have_source_hashes_strict_json_and_no_metadata_in_classified(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    metadata = unitypy / "metadata"
    images.mkdir(parents=True)
    metadata.mkdir()
    (images / "UI_Button.png").write_bytes(b"button")
    (metadata / "Data.json").write_text("{}", encoding="utf-8")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            fixture_row(asset_id="button", asset_name="UI_Button", export_path="images/UI_Button.png"),
            fixture_row(asset_id="data", asset_name="Data", export_path="metadata/Data.json", asset_type="MonoBehaviour", export_kind="metadata", path_id="2"),
        ],
    )
    study_root = tmp_path / "Study"

    summary = organize(index, unitypy, study_root, project_name="Fixture")

    assert summary["ok"] is True
    assert summary["source_index_sha256"] == organizer._sha256(index)
    assert summary["skipped_metadata_count"] == 1
    assert "generated_at" not in summary
    resource_index = study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织索引.csv"
    row = next(csv.DictReader(resource_index.open(encoding="utf-8-sig")))
    assert len(row["source_sha256"]) == 64
    assert row["source_sha256"] == organizer._sha256(images / "UI_Button.png")
    persisted = json.loads((study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织摘要.json").read_text(encoding="utf-8"))
    assert persisted["source_index_sha256"] == summary["source_index_sha256"]
    forbidden = {".json", ".csv", ".txt", ".md"}
    assert not [path for path in (study_root / "1.分类资源").rglob("*") if path.is_file() and path.suffix.lower() in forbidden]


def test_large_metadata_index_is_processed_in_two_streaming_passes(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    rows = [
        fixture_row(
            asset_id=f"meta-{number}",
            asset_name=f"Data_{number}",
            export_path="",
            asset_type="MonoBehaviour",
            export_kind="metadata",
            path_id=str(number),
        )
        for number in range(5000)
    ]
    write_index(index, rows)

    summary = organize(index, unitypy, tmp_path / "Study", project_name="Fixture")

    assert summary["ok"] is True
    assert summary["source_row_count"] == 5000
    assert summary["organized_count"] == 0
    source = (SCRIPT_DIR / "organize_unitypy_project_export.py").read_text(encoding="utf-8")
    assert "list(csv.DictReader" not in source


def artifact_group_paths(study: Path) -> list[Path]:
    index_root = study / "4.临时目录" / "中间索引" / "UnityPy组织"
    return [
        index_root / "资源组织索引.csv",
        index_root / "动画序列帧索引.csv",
        index_root / "主角资源候选索引.csv",
        index_root / "资源组织错误.jsonl",
        study / "4.临时目录" / "模型工具需求.md",
        index_root / "资源组织摘要.json",
    ]


def prepare_artifact_replacement(
    tmp_path: Path,
) -> tuple[Path, Path, Path, list[Path], dict[Path, bytes]]:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")],
    )
    study = tmp_path / "Study"
    baseline = organize(index, unitypy, study, project_name="Baseline")
    baseline.close()
    artifacts = artifact_group_paths(study)
    before = {path: path.read_bytes() for path in artifacts}
    (images / "UI_New.png").write_bytes(b"new")
    write_index(
        index,
        [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")],
    )
    return unitypy, index, study, artifacts, before


@pytest.mark.parametrize("failed_commit", [1, 3, 6])
def test_artifact_publish_success_then_wrapper_error_restores_exact_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_commit: int,
) -> None:
    _unitypy, index, study, artifacts, before = prepare_artifact_replacement(tmp_path)
    original_commit = organizer._commit_temporary
    calls = 0

    def failing_after_publish(temporary: Path, destination: Path) -> bool:
        nonlocal calls
        calls += 1
        changed = original_commit(temporary, destination)
        if calls == failed_commit:
            raise OSError(f"synthetic post-publish wrapper failure {failed_commit}")
        return changed

    monkeypatch.setattr(organizer, "_commit_temporary", failing_after_publish)
    with pytest.raises(OSError, match="post-publish wrapper failure"):
        organize(index, _unitypy, study, project_name="Replacement")

    assert calls == failed_commit
    assert before == {path: path.read_bytes() for path in artifacts}
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))


@pytest.mark.parametrize("artifact_index", [0, 4, 5])
def test_artifact_low_level_publish_success_then_wrapper_error_is_adopted_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    _unitypy, index, study, artifacts, before = prepare_artifact_replacement(tmp_path)
    selected = artifacts[artifact_index]
    injected = False
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def fail_after_publish(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal injected
            source_name = organizer._windows_binary_handle_path(handle).name
            original_rename(handle, root_handle, target_name)
            if (
                source_name.endswith(".partial")
                and target_name.casefold() == selected.name.casefold()
                and not injected
            ):
                injected = True
                raise OSError("synthetic low-level post-publish failure")

        monkeypatch.setattr(organizer, "_windows_rename_binary_handle", fail_after_publish)
    else:
        original_publish = organizer._lease_publish_no_replace

        def fail_after_publish_posix(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
        ) -> None:
            nonlocal injected
            original_publish(lease, source_name, destination_name)
            if (
                source_name.endswith(".partial")
                and destination_name == selected.name
                and not injected
            ):
                injected = True
                raise OSError("synthetic low-level post-publish failure")

        monkeypatch.setattr(
            organizer,
            "_lease_publish_no_replace",
            fail_after_publish_posix,
        )

    result = organize(index, _unitypy, study, project_name="Replacement")

    assert result["ok"] is True
    assert injected is True
    assert artifacts[artifact_index].read_bytes() != before[selected]
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))
    result.close()


@pytest.mark.parametrize("artifact_index", [0, 4, 5])
def test_artifact_backup_success_then_wrapper_error_updates_ledger_before_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    _unitypy, index, study, artifacts, before = prepare_artifact_replacement(tmp_path)
    selected = artifacts[artifact_index]
    injected = False
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def fail_after_backup(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal injected
            source_name = organizer._windows_binary_handle_path(handle).name
            original_rename(handle, root_handle, target_name)
            if (
                source_name.casefold() == selected.name.casefold()
                and ".ga-rollback-" in target_name
                and not injected
            ):
                injected = True
                raise OSError("synthetic post-backup wrapper failure")

        monkeypatch.setattr(organizer, "_windows_rename_binary_handle", fail_after_backup)
    else:
        original_move = organizer._lease_move_no_replace_reconciled

        def fail_after_backup_move(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
            expected: organizer.ArtifactPathState,
        ) -> tuple[bool, BaseException | None]:
            nonlocal injected
            moved, failure = original_move(
                lease,
                source_name,
                destination_name,
                expected,
            )
            if (
                moved
                and source_name == selected.name
                and ".ga-rollback-" in destination_name
                and not injected
            ):
                injected = True
                return True, OSError("synthetic post-backup wrapper failure")
            return moved, failure

        monkeypatch.setattr(
            organizer,
            "_lease_move_no_replace_reconciled",
            fail_after_backup_move,
        )

    with pytest.raises(OSError, match="post-backup wrapper failure"):
        organize(index, _unitypy, study, project_name="Replacement")

    assert injected is True
    assert before == {path: path.read_bytes() for path in artifacts}
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))


@pytest.mark.parametrize("artifact_index", [0, 4, 5])
def test_artifact_restore_success_then_wrapper_error_is_reconciled_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_index: int,
) -> None:
    _unitypy, index, study, artifacts, before = prepare_artifact_replacement(tmp_path)
    selected = artifacts[artifact_index]
    original_commit = organizer._commit_temporary
    commit_calls = 0
    injected = False

    def fail_last_commit(temporary: Path, destination: Path) -> bool:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 6:
            raise OSError("synthetic late commit failure")
        return original_commit(temporary, destination)

    monkeypatch.setattr(organizer, "_commit_temporary", fail_last_commit)
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def fail_after_restore(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal injected
            source_name = organizer._windows_binary_handle_path(handle).name
            original_rename(handle, root_handle, target_name)
            if (
                ".ga-rollback-" in source_name
                and target_name.casefold() == selected.name.casefold()
                and not injected
            ):
                injected = True
                raise OSError("synthetic post-restore wrapper failure")

        monkeypatch.setattr(organizer, "_windows_rename_binary_handle", fail_after_restore)
    else:
        original_move = organizer._lease_move_no_replace_reconciled

        def fail_after_restore_move(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
            expected: organizer.ArtifactPathState,
        ) -> tuple[bool, BaseException | None]:
            nonlocal injected
            moved, failure = original_move(
                lease,
                source_name,
                destination_name,
                expected,
            )
            if (
                moved
                and ".ga-rollback-" in source_name
                and destination_name == selected.name
                and not injected
            ):
                injected = True
                return True, OSError("synthetic post-restore wrapper failure")
            return moved, failure

        monkeypatch.setattr(
            organizer,
            "_lease_move_no_replace_reconciled",
            fail_after_restore_move,
        )

    with pytest.raises(OSError, match="late commit failure"):
        organize(index, _unitypy, study, project_name="Replacement")

    assert commit_calls == 6
    assert injected is True
    assert before == {path: path.read_bytes() for path in artifacts}
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))


@pytest.mark.parametrize("failed_commit", range(1, 7))
def test_artifact_group_commit_failure_restores_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_commit: int
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")])
    study = tmp_path / "Study"
    baseline = organize(index, unitypy, study, project_name="Baseline")
    assert baseline["ok"] is True
    artifacts = artifact_group_paths(study)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifacts}

    (images / "UI_New.png").write_bytes(b"new")
    write_index(index, [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")])
    original_commit = organizer._commit_temporary
    calls = 0

    def failing_commit(temporary: Path, destination: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == failed_commit:
            raise OSError(f"synthetic artifact commit failure {failed_commit}")
        return original_commit(temporary, destination)

    monkeypatch.setattr(organizer, "_commit_temporary", failing_commit)
    with pytest.raises(OSError, match="synthetic artifact commit failure"):
        organize(index, unitypy, study, project_name="Replacement")

    assert calls == failed_commit
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifacts}
    persisted = json.loads(artifacts[-1].read_text(encoding="utf-8"))
    assert persisted["ok"] is True
    assert persisted["project_name"] == "Baseline"
    assert persisted["output_validation"]["resource_index"]["sha256"] == organizer._sha256(artifacts[0])
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))


@pytest.mark.parametrize("drift_after_commit", range(1, 7))
def test_control_drift_during_any_artifact_commit_rolls_back_fresh_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_after_commit: int
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    config = tmp_path / "config.json"
    config.write_bytes(DEFAULT_CONFIG.read_bytes())
    study = tmp_path / "Study"
    original_commit = organizer._commit_temporary
    calls = 0

    def drifting_commit(temporary: Path, destination: Path) -> bool:
        nonlocal calls
        calls += 1
        changed = original_commit(temporary, destination)
        if calls == drift_after_commit:
            value = json.loads(config.read_text(encoding="utf-8"))
            value["main_character_candidate_patterns"] = [f"^drift-{drift_after_commit}$"]
            config.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return changed

    monkeypatch.setattr(organizer, "_commit_temporary", drifting_commit)
    with pytest.raises(organizer.InputChangedError, match="config changed"):
        organize(index, unitypy, study, project_name="Drift", config_path=config)

    assert calls == drift_after_commit
    assert not [path for path in artifact_group_paths(study) if path.exists()]
    assert not list(study.rglob("*.partial"))
    assert not list(study.rglob("*.ga-rollback-*.bak"))


def test_existing_path_alias_identity_queries_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_bytes(b"same inode")
    os.link(left, right)

    def denied_samefile(_left: object, _right: object) -> bool:
        raise PermissionError("synthetic samefile denial")

    monkeypatch.setattr(organizer.os.path, "samefile", denied_samefile)
    with pytest.raises(UnsafePathError, match="could not compare filesystem identity"):
        organizer._existing_paths_alias(left, right)


def test_existing_path_alias_stat_query_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_bytes(b"left")
    right.write_bytes(b"right")
    original_lstat = organizer.os.lstat

    def denied_lstat(path: object, *args: object, **kwargs: object):
        if Path(path) == left:
            raise PermissionError("synthetic lstat denial")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(organizer.os, "lstat", denied_lstat)
    with pytest.raises(UnsafePathError, match="could not verify filesystem identity"):
        organizer._existing_paths_alias(left, right)


def test_category_filter_applies_to_main_candidate_secondary_target(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    effects = unitypy / "effects"
    effects.mkdir(parents=True)
    (effects / "Hero_Smoke.png").write_bytes(b"smoke")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="hero", asset_name="Hero_Smoke", export_path="effects/Hero_Smoke.png")])
    study = tmp_path / "Study"

    summary = organize(
        index,
        unitypy,
        study,
        project_name="Filter",
        include_category_roots={"特效"},
    )

    assert summary["ok"] is True
    assert summary["organized_count"] == 1
    assert summary["main_character_candidate_count"] == 0
    assert summary["filtered_category_counts"]["图片/角色立绘/主角候选资源"] == 1
    assert (study / "1.分类资源" / "特效" / "粒子贴图" / "Hero" / "Hero_Smoke.png").exists()
    assert not (study / "1.分类资源" / "图片").exists()


def test_sequence_groups_do_not_merge_different_source_contexts_by_default(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    first = unitypy / "ui" / "menu-a" / "Icon_Attack_01.png"
    second = unitypy / "ui" / "menu-b" / "Icon_Attack_02.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"menu-a")
    second.write_bytes(b"menu-b")
    first_row = fixture_row(asset_id="1", asset_name="Icon_Attack_01", export_path="ui/menu-a/Icon_Attack_01.png")
    second_row = fixture_row(asset_id="2", asset_name="Icon_Attack_02", export_path="ui/menu-b/Icon_Attack_02.png")
    first_row.update(source_file="BundleA/ui.assets", container_path="UI/MenuA")
    second_row.update(source_file="BundleB/ui.assets", container_path="UI/MenuB")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [first_row, second_row])
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="Scoped")

    assert summary["ok"] is True
    assert summary["sequence_detection"]["candidate_group_count"] == 2
    assert summary["sequence_detection"]["confirmed_group_count"] == 0
    assert summary["sequence_frame_count"] == 0
    assert not (study / "1.分类资源" / "图片" / "序列帧").exists()


def test_explicit_animation_context_can_cross_sequence_source_scopes(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    first = unitypy / "animations" / "a" / "Hero_Attack_01.png"
    second = unitypy / "animations" / "b" / "Hero_Attack_02.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    first_row = fixture_row(asset_id="1", asset_name="Hero_Attack_01", export_path="animations/a/Hero_Attack_01.png")
    second_row = fixture_row(asset_id="2", asset_name="Hero_Attack_02", export_path="animations/b/Hero_Attack_02.png")
    first_row["source_file"] = "BundleA/anim.assets"
    second_row["source_file"] = "BundleB/anim.assets"
    index = unitypy / "all_resources_index.csv"
    write_index(index, [first_row, second_row])
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="Explicit")

    assert summary["ok"] is True
    assert summary["sequence_detection"]["explicit_context_group_count"] == 1
    assert summary["sequence_detection"]["confirmed_group_count"] == 1
    assert summary["sequence_frame_count"] == 2


def test_explicit_global_sequence_scope_config_can_merge_ordinary_contexts(tmp_path: Path) -> None:
    def enable_global_scope(config: dict[str, object]) -> None:
        sequence = config["sequence"]
        assert isinstance(sequence, dict)
        group_scope = sequence["group_scope"]
        assert isinstance(group_scope, dict)
        group_scope["mode"] = "global"

    config_path = write_custom_config(tmp_path, enable_global_scope)
    unitypy = tmp_path / "UnityPy"
    first = unitypy / "ui" / "a" / "Icon_Attack_01.png"
    second = unitypy / "ui" / "b" / "Icon_Attack_02.png"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    first_row = fixture_row(asset_id="1", asset_name="Icon_Attack_01", export_path="ui/a/Icon_Attack_01.png")
    second_row = fixture_row(asset_id="2", asset_name="Icon_Attack_02", export_path="ui/b/Icon_Attack_02.png")
    first_row["source_file"] = "BundleA/ui.assets"
    second_row["source_file"] = "BundleB/ui.assets"
    index = unitypy / "all_resources_index.csv"
    write_index(index, [first_row, second_row])

    summary = organize(index, unitypy, tmp_path / "Study", project_name="Global", config_path=config_path)

    assert summary["ok"] is True
    assert summary["sequence_detection"]["group_scope_mode"] == "global"
    assert summary["sequence_detection"]["confirmed_group_count"] == 1
    assert summary["sequence_frame_count"] == 2


def test_sequence_candidate_store_spills_safely_and_repeat_output_is_deterministic(tmp_path: Path) -> None:
    def force_small_spill(config: dict[str, object]) -> None:
        sequence = config["sequence"]
        assert isinstance(sequence, dict)
        candidate_store = sequence["candidate_store"]
        assert isinstance(candidate_store, dict)
        candidate_store["spill_threshold"] = 2

    config_path = write_custom_config(tmp_path, force_small_spill)
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    rows = []
    for number in range(1, 5):
        name = f"Hero_Attack_{number}"
        (images / f"{name}.png").write_bytes(str(number).encode("ascii"))
        rows.append(fixture_row(asset_id=str(number), asset_name=name, export_path=f"images/{name}.png"))
    index = unitypy / "all_resources_index.csv"
    write_index(index, rows)
    study = tmp_path / "Study"

    first = organize(index, unitypy, study, project_name="Spill", config_path=config_path)
    assert first["ok"] is True
    assert first["sequence_detection"]["candidate_store_spilled_to_sqlite"] is True
    assert first["sequence_detection"]["candidate_spill_threshold"] == 2
    assert first["sequence_frame_count"] == 4
    assert not list(study.rglob("*.sqlite*"))
    assert not list(study.rglob("*.partial"))

    organize(index, unitypy, study, project_name="Spill", config_path=config_path)
    artifacts = artifact_group_paths(study)
    stable = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifacts}
    third = organize(index, unitypy, study, project_name="Spill", config_path=config_path)
    assert third["reused_count"] == 8
    assert stable == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in artifacts}
    assert not list(study.rglob("*.sqlite*"))


@pytest.mark.parametrize("failed_restore", range(6))
def test_rollback_restore_failure_matrix_never_leaves_ok_true_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_restore: int,
) -> None:
    destinations = [tmp_path / f"artifact-{number}.dat" for number in range(5)]
    summary_path = tmp_path / "summary.json"
    destinations.append(summary_path)
    entries: list[organizer.ArtifactRollbackEntry] = []
    for number, destination in enumerate(destinations):
        if destination == summary_path:
            destination.write_text(json.dumps({"ok": True, "project_name": "new"}), encoding="utf-8")
        else:
            destination.write_bytes(f"new-{number}".encode("ascii"))
        backup = tmp_path / f".{destination.name}.ga-rollback-test-{number}.bak"
        if destination == summary_path:
            backup.write_text(json.dumps({"ok": True, "project_name": "old"}), encoding="utf-8")
        else:
            backup.write_bytes(f"old-{number}".encode("ascii"))
        identity = organizer._capture_artifact_path_state(destination, "fixture destination").identity
        entries.append(
            organizer.ArtifactRollbackEntry(
                name="summary" if destination == summary_path else f"artifact_{number}",
                destination=destination,
                changed=True,
                backup=backup,
                published_identity=identity,
            )
        )
    # Production transaction order keeps summary first in rollback bookkeeping.
    entries = [entries[-1], *entries[:-1]]
    selected = entries[failed_restore]
    selected_backup = selected.backup
    assert selected_backup is not None
    original_replace = organizer.os.replace

    def failing_replace(source: object, destination: object) -> None:
        if Path(source) == selected_backup and Path(destination) == selected.destination:
            raise PermissionError(f"synthetic restore denial {failed_restore}")
        original_replace(source, destination)

    monkeypatch.setattr(organizer.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="artifact group rollback failed"):
        organizer._rollback_artifact_group(entries, summary_path)

    persisted = json.loads(summary_path.read_text(encoding="utf-8"))
    assert persisted["ok"] is False
    assert persisted["transaction_state"] == "rollback_incomplete"
    assert selected.destination.exists()
    assert selected_backup.exists()


def test_source_index_aba_between_passes_is_rejected_by_consumed_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_A.png").write_bytes(b"a")
    (images / "UI_B.png").write_bytes(b"b")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="a", asset_name="UI_A", export_path="images/UI_A.png")])
    generation_a = index.read_bytes()
    alternate = tmp_path / "alternate.csv"
    write_index(alternate, [fixture_row(asset_id="b", asset_name="UI_B", export_path="images/UI_B.png")])
    generation_b = alternate.read_bytes()
    original_iter = organizer._iter_rows
    calls = 0

    def swapping_iter(path: Path, required_fields: list[str]):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(generation_b)
            try:
                evidence = yield from original_iter(path, required_fields)
            finally:
                path.write_bytes(generation_a)
            return evidence
        return (yield from original_iter(path, required_fields))

    monkeypatch.setattr(organizer, "_iter_rows", swapping_iter)
    study = tmp_path / "Study"
    summary = organize(index, unitypy, study, project_name="ABA")

    assert calls == 2
    assert summary["ok"] is False
    assert summary["source_index_unchanged"] is False
    assert summary["source_index_passes"]["first"]["sha256"] != summary["source_index_passes"]["second"]["sha256"]
    assert index.read_bytes() == generation_a
    assert (study / "1.分类资源" / "图片" / "UI" / "UI" / "UI_B.png").exists()
    persisted = json.loads(artifact_group_paths(study)[-1].read_text(encoding="utf-8"))
    assert persisted["ok"] is False


def test_late_artifact_appearance_fails_closed_and_rollback_keeps_external_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")])
    study = tmp_path / "Study"
    destination = artifact_group_paths(study)[0]
    original_commit = organizer._commit_temporary
    late_bytes = b"late concurrent writer"
    calls = 0

    def racing_commit(temporary: Path, current_destination: Path) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            current_destination.write_bytes(late_bytes)
        return original_commit(temporary, current_destination)

    monkeypatch.setattr(organizer, "_commit_temporary", racing_commit)
    with pytest.raises(organizer.InputChangedError, match="appeared or changed"):
        organize(index, unitypy, study, project_name="LateWriter")

    assert calls == 1
    assert destination.read_bytes() == late_bytes
    assert not artifact_group_paths(study)[-1].exists()
    assert not list(study.rglob("*.ga-rollback-*.bak"))


@pytest.mark.parametrize("failure_point", ["open", "header"])
def test_csv_writer_constructor_failure_closes_and_removes_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    destination = tmp_path / "artifact.csv"
    if failure_point == "open":
        original_open = Path.open

        def failing_open(path: Path, *args: object, **kwargs: object):
            handle = original_open(path, *args, **kwargs)
            handle.close()
            raise OSError("synthetic constructor open failure after creation")

        monkeypatch.setattr(Path, "open", failing_open)
    else:
        original_writeheader = csv.DictWriter.writeheader

        def failing_header(writer: csv.DictWriter) -> object:
            original_writeheader(writer)
            raise OSError("synthetic constructor header failure")

        monkeypatch.setattr(csv.DictWriter, "writeheader", failing_header)

    with pytest.raises(OSError, match="synthetic constructor"):
        organizer.AtomicCsvWriter(destination, ["name"])
    assert not list(tmp_path.glob("*.partial"))


def test_writer_prepare_fsync_failure_is_aborted_without_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    original_fsync = organizer.os.fsync
    denied = False

    def failing_fsync(fd: int) -> None:
        nonlocal denied
        if not denied:
            denied = True
            raise OSError("synthetic writer fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(organizer.os, "fsync", failing_fsync)
    study = tmp_path / "Study"
    with pytest.raises(OSError, match="synthetic writer fsync failure"):
        organize(index, unitypy, study, project_name="Fsync")
    assert not list(study.rglob("*.partial"))


def test_backup_cleanup_failure_is_warning_and_next_run_reconciles_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")])
    study = tmp_path / "Study"
    organize(index, unitypy, study, project_name="Baseline")
    (images / "UI_New.png").write_bytes(b"new")
    write_index(index, [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")])
    failure_state = install_one_cleanup_delete_failure(monkeypatch)
    replacement = organize(index, unitypy, study, project_name="Replacement")
    persisted = json.loads(artifact_group_paths(study)[-1].read_text(encoding="utf-8"))
    orphans = list(study.rglob("*.ga-cleanup-orphan-*.bak"))

    assert replacement["ok"] is True
    assert replacement["cleanup_state"] == "warnings"
    assert persisted["ok"] is True
    assert persisted["transaction_state"] == "committed"
    assert persisted["cleanup_state"] == "warnings"
    assert len(persisted["cleanup_warnings"]) == 1
    assert len(orphans) == 1
    assert failure_state["denied"] is True
    status_retired = list(
        artifact_group_paths(study)[-1].parent.glob(
            "*.ga-cleanup-status-retired-*.bak"
        )
    )
    assert len(status_retired) == 1
    replacement.close()
    assert not list(
        artifact_group_paths(study)[-1].parent.glob(
            "*.ga-cleanup-status-retired-*.bak"
        )
    )

    rerun = organize(index, unitypy, study, project_name="Replacement")
    assert rerun["ok"] is True
    assert rerun["cleanup_state"] == "clean"
    assert rerun["reconciled_cleanup_orphan_count"] == 1
    assert not list(study.rglob("*.ga-cleanup-orphan-*.bak"))
    rerun.close()


def test_backup_quarantine_failure_is_recorded_and_next_run_reconciles_recorded_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")])
    study = tmp_path / "Study"
    organize(index, unitypy, study, project_name="Baseline")
    (images / "UI_New.png").write_bytes(b"new")
    write_index(index, [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")])
    denied = False
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def failing_quarantine_rename(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal denied
            source_name = organizer._windows_binary_handle_path(handle).name
            if (
                ".ga-rollback-" in source_name
                and ".ga-cleanup-orphan-" in target_name
                and not denied
            ):
                denied = True
                raise PermissionError("synthetic backup quarantine denial")
            original_rename(handle, root_handle, target_name)

        monkeypatch.setattr(
            organizer,
            "_windows_rename_binary_handle",
            failing_quarantine_rename,
        )
    else:
        original_move = organizer._lease_move_no_replace_reconciled

        def failing_quarantine_move(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
            expected: organizer.ArtifactPathState,
        ) -> tuple[bool, BaseException | None]:
            nonlocal denied
            if (
                ".ga-rollback-" in source_name
                and ".ga-cleanup-orphan-" in destination_name
                and not denied
            ):
                denied = True
                return False, PermissionError("synthetic backup quarantine denial")
            return original_move(lease, source_name, destination_name, expected)

        monkeypatch.setattr(
            organizer,
            "_lease_move_no_replace_reconciled",
            failing_quarantine_move,
        )
    replacement = organize(index, unitypy, study, project_name="Replacement")
    persisted = json.loads(artifact_group_paths(study)[-1].read_text(encoding="utf-8"))
    rollback_orphans = list(study.rglob("*.ga-rollback-*.bak"))

    assert replacement["ok"] is True
    assert persisted["cleanup_state"] == "warnings"
    assert persisted["cleanup_warnings"][0]["code"] == "backup_cleanup_failed"
    assert len(rollback_orphans) == 1
    replacement.close()

    monkeypatch.undo()
    rerun = organize(index, unitypy, study, project_name="Replacement")
    assert rerun["ok"] is True
    assert rerun["cleanup_state"] == "clean"
    assert rerun["reconciled_cleanup_orphan_count"] == 1
    assert not list(study.rglob("*.ga-rollback-*.bak"))
    rerun.close()


def test_cleanup_status_publish_failure_invalidates_fixed_success_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(index, [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")])
    study = tmp_path / "Study"
    organize(index, unitypy, study, project_name="Baseline")
    (images / "UI_New.png").write_bytes(b"new")
    write_index(index, [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")])
    def failing_cleanup_status(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("synthetic cleanup status publish failure")

    failure_state = install_one_cleanup_delete_failure(monkeypatch)
    monkeypatch.setattr(organizer, "_rewrite_committed_summary_cleanup_state", failing_cleanup_status)

    with pytest.raises(RuntimeError, match="authoritative cleanup state"):
        organize(index, unitypy, study, project_name="Replacement")

    persisted = json.loads(artifact_group_paths(study)[-1].read_text(encoding="utf-8"))
    assert persisted["ok"] is False
    assert persisted["transaction_state"] == "committed_cleanup_status_unknown"
    assert persisted["cleanup_state"] == "recovery_required"
    assert {warning["code"] for warning in persisted["cleanup_warnings"]} == {
        "backup_cleanup_failed",
        "cleanup_status_publish_failed",
    }
    assert failure_state["denied"] is True


class _CloseFailureProxy:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def close(self) -> None:
        self.connection.close()  # type: ignore[attr-defined]
        raise OSError("synthetic sqlite close failure")

    def __getattr__(self, name: str) -> object:
        return getattr(self.connection, name)


def test_sqlite_close_failure_still_clears_connection_and_all_sidecars(tmp_path: Path) -> None:
    store = organizer.SequenceCandidateStore(tmp_path, 1)
    key = ("图片/序列帧", "Hero", "attack", "scope")
    store.add(key, 1, False)
    store.add(key, 2, False)
    assert store.connection is not None
    store.connection = _CloseFailureProxy(store.connection)

    with pytest.raises(RuntimeError, match="connection close"):
        store.close()

    assert store.connection is None
    assert not list(tmp_path.glob("*.sqlite*"))
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.skipif(os.name != "nt", reason="requires a real Windows junction")
@pytest.mark.parametrize("transfer_mode", ["hardlink", "copy"])
def test_classified_materialize_pins_parent_against_post_check_junction_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transfer_mode: str,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"authorized-source")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")],
    )
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", [transfer_mode]),
    )
    study = tmp_path / "Study"
    classified = study / "1.分类资源"
    parked = study / "classified-original"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-baseline")
    outside_before = {
        path.relative_to(outside).as_posix(): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    original_transfer = organizer._transfer_new
    swap_attempted = False
    swap_succeeded = False
    swap_error = ""

    def swapping_transfer(
        source: Path,
        destination: Path,
        methods: tuple[str, ...],
        lease: organizer.StableDirectoryLease,
    ) -> str:
        nonlocal swap_attempted, swap_succeeded, swap_error
        if not swap_attempted:
            swap_attempted = True
            try:
                os.rename(classified, parked)
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(classified), str(outside)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if result.returncode != 0:
                    raise OSError(result.stdout + result.stderr)
                swap_succeeded = True
            except OSError as exc:
                swap_error = f"{type(exc).__name__}: {exc}"
        return original_transfer(source, destination, methods, lease)

    monkeypatch.setattr(organizer, "_transfer_new", swapping_transfer)
    try:
        summary = organize(
            index,
            unitypy,
            study,
            project_name="PinnedParent",
            config_path=config_path,
        )
    finally:
        if organizer._is_reparse(classified):
            os.rmdir(classified)
        if parked.exists() and not classified.exists():
            os.rename(parked, classified)

    outside_after = {
        path.relative_to(outside).as_posix(): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    assert swap_attempted is True
    assert swap_succeeded is False
    assert swap_error
    assert outside_after == outside_before
    assert summary["ok"] is True
    assert (
        classified / "图片" / "UI" / "UI" / "UI_Menu.png"
    ).read_bytes() == b"authorized-source"
    assert not list(study.rglob("*.ga-dirlease-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory lease semantics")
@pytest.mark.parametrize("phase", ["nested_create", "verify"])
def test_classified_materialize_pins_nested_create_verify_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Menu.png").write_bytes(b"authorized-source")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="ui", asset_name="UI_Menu", export_path="images/UI_Menu.png")],
    )
    study = tmp_path / "Study"
    classified = study / "1.分类资源"
    parked = study / "classified-original"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.bin").write_bytes(b"outside-baseline")
    outside_before = {
        path.relative_to(outside).as_posix(): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    swap_attempted = False
    swap_succeeded = False
    swap_error = ""

    def attempt_swap() -> None:
        nonlocal swap_attempted, swap_succeeded, swap_error
        if swap_attempted:
            return
        swap_attempted = True
        try:
            os.rename(classified, parked)
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(classified), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                raise OSError(result.stdout + result.stderr)
            swap_succeeded = True
        except OSError as exc:
            swap_error = f"{type(exc).__name__}: {exc}"

    if phase == "nested_create":
        original_create = organizer.ClassifiedOutputBoundary._windows_create_child_directory

        def hooked_create(parent_handle: int, name: str) -> int:
            if classified.exists() and name == "图片":
                attempt_swap()
            return original_create(parent_handle, name)

        monkeypatch.setattr(
            organizer.ClassifiedOutputBoundary,
            "_windows_create_child_directory",
            staticmethod(hooked_create),
        )
    elif phase == "verify":
        original_same = organizer._lease_same_content

        def hooked_same(
            source: Path,
            lease: organizer.StableDirectoryLease,
            name: str,
            source_hash: str,
        ) -> bool:
            if name == "UI_Menu.png":
                attempt_swap()
            return original_same(source, lease, name, source_hash)

        monkeypatch.setattr(organizer, "_lease_same_content", hooked_same)
    try:
        summary = organize(index, unitypy, study, project_name=f"Pinned-{phase}")
    finally:
        if organizer._is_reparse(classified):
            os.rmdir(classified)
        if parked.exists() and not classified.exists():
            os.rename(parked, classified)

    outside_after = {
        path.relative_to(outside).as_posix(): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    assert swap_attempted is True
    assert swap_succeeded is False
    assert swap_error
    assert outside_after == outside_before
    assert summary["ok"] is True
    assert not list(study.rglob("*.ga-dirlease-*.tmp"))


def test_cleanup_status_publish_preserves_late_summary_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")],
    )
    study = tmp_path / "Study"
    organize(index, unitypy, study, project_name="Baseline")
    (images / "UI_New.png").write_bytes(b"new")
    write_index(
        index,
        [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")],
    )
    summary_path = artifact_group_paths(study)[-1]
    late_bytes = (
        json.dumps(
            {
                "schema_version": 1,
                "ok": False,
                "transaction_state": "external_late_writer",
                "cleanup_state": "external",
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    original_move = organizer._lease_move_no_replace_reconciled
    injected = False
    failure_state = install_one_cleanup_delete_failure(monkeypatch)

    def inject_before_publish(
        lease: organizer.StableDirectoryLease,
        temporary_name: str,
        destination_name: str,
        expected: organizer.ArtifactPathState,
    ) -> tuple[bool, BaseException | None]:
        nonlocal injected
        if (
            failure_state["denied"]
            and not injected
            and destination_name == summary_path.name
            and temporary_name.startswith(f".{summary_path.name}.ga-unitypy-")
        ):
            summary_path.write_bytes(late_bytes)
            injected = True
        return original_move(lease, temporary_name, destination_name, expected)

    monkeypatch.setattr(
        organizer,
        "_lease_move_no_replace_reconciled",
        inject_before_publish,
    )
    with pytest.raises(RuntimeError, match="authoritative cleanup state"):
        organize(index, unitypy, study, project_name="Replacement")

    assert failure_state["denied"] is True
    assert injected is True
    assert summary_path.read_bytes() == late_bytes
    assert json.loads(summary_path.read_text(encoding="utf-8"))["transaction_state"] == "external_late_writer"


def test_cleanup_failure_marker_preserves_summary_changed_after_caller_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "UI_Old.png").write_bytes(b"old")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="old", asset_name="UI_Old", export_path="images/UI_Old.png")],
    )
    study = tmp_path / "Study"
    organize(index, unitypy, study, project_name="Baseline")
    (images / "UI_New.png").write_bytes(b"new")
    write_index(
        index,
        [fixture_row(asset_id="new", asset_name="UI_New", export_path="images/UI_New.png")],
    )
    summary_path = artifact_group_paths(study)[-1]
    late_bytes = (
        json.dumps(
            {
                "schema_version": 1,
                "ok": False,
                "transaction_state": "external_after_marker_caller_check",
                "cleanup_state": "external",
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    original_marker = organizer._publish_rollback_failure_marker
    injected = False
    failure_state = install_one_cleanup_delete_failure(monkeypatch)

    def fail_cleanup_status(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("synthetic cleanup-status publication failure")

    def inject_before_marker(*args: object, **kwargs: object) -> None:
        nonlocal injected
        assert isinstance(kwargs.get("expected_current"), organizer.ArtifactPathState)
        summary_path.write_bytes(late_bytes)
        injected = True
        original_marker(*args, **kwargs)

    monkeypatch.setattr(organizer, "_rewrite_committed_summary_cleanup_state", fail_cleanup_status)
    monkeypatch.setattr(organizer, "_publish_rollback_failure_marker", inject_before_marker)
    with pytest.raises(RuntimeError, match="authoritative cleanup state"):
        organize(index, unitypy, study, project_name="Replacement")

    assert failure_state["denied"] is True
    assert injected is True
    assert summary_path.read_bytes() == late_bytes


@pytest.mark.parametrize(
    "denied_prefix",
    [
        ".资源组织索引.csv.ga-unitypy-",
        ".动画序列帧索引.csv.ga-unitypy-",
        ".主角资源候选索引.csv.ga-unitypy-",
        ".资源组织错误.jsonl.ga-unitypy-",
    ],
)
def test_second_pass_failure_attempts_every_writer_and_sqlite_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied_prefix: str,
) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    rows = []
    for number in (1, 2):
        name = f"Hero_Attack_{number}"
        (unitypy / f"{name}.png").write_bytes(str(number).encode("ascii"))
        rows.append(fixture_row(asset_id=str(number), asset_name=name, export_path=f"{name}.png"))
    index = unitypy / "all_resources_index.csv"
    write_index(index, rows)
    config_path = write_custom_config(
        tmp_path,
        lambda config: config["sequence"]["candidate_store"].__setitem__("spill_threshold", 1),
    )
    original_iter = organizer._iter_rows
    iter_calls = 0
    denied = False

    def fail_second_pass(path: Path, required_fields: list[str]):
        nonlocal iter_calls
        iter_calls += 1
        if iter_calls == 2:
            iterator = original_iter(path, required_fields)
            yield next(iterator)
            raise OSError("synthetic second-pass read failure")
        return (yield from original_iter(path, required_fields))

    monkeypatch.setattr(organizer, "_iter_rows", fail_second_pass)
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def deny_selected_partial_retirement(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal denied
            source_name = organizer._windows_binary_handle_path(handle).name
            if (
                source_name.startswith(denied_prefix)
                and source_name.endswith(".partial")
                and ".ga-owned-retired-" in target_name
                and not denied
            ):
                denied = True
                raise PermissionError("synthetic selected writer partial unlink denial")
            original_rename(handle, root_handle, target_name)

        monkeypatch.setattr(
            organizer,
            "_windows_rename_binary_handle",
            deny_selected_partial_retirement,
        )
    else:
        original_move = organizer._lease_move_no_replace_reconciled

        def deny_selected_partial_retirement_posix(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
            expected: organizer.ArtifactPathState,
        ) -> tuple[bool, BaseException | None]:
            nonlocal denied
            if (
                source_name.startswith(denied_prefix)
                and source_name.endswith(".partial")
                and ".ga-owned-retired-" in destination_name
                and not denied
            ):
                denied = True
                return False, PermissionError("synthetic selected writer partial unlink denial")
            return original_move(lease, source_name, destination_name, expected)

        monkeypatch.setattr(
            organizer,
            "_lease_move_no_replace_reconciled",
            deny_selected_partial_retirement_posix,
        )
    study = tmp_path / "Study"
    with pytest.raises(RuntimeError) as raised:
        organize(index, unitypy, study, project_name="CleanupMatrix", config_path=config_path)

    message = str(raised.value)
    assert "synthetic second-pass read failure" in message
    assert "cleanup also failed" in message
    assert denied is True
    partials = list(study.rglob("*.partial"))
    assert len(partials) == 1
    assert partials[0].name.startswith(denied_prefix)
    assert not [path for path in study.rglob("*") if path.is_file() and ".sqlite" in path.name]
    assert not [path for path in artifact_group_paths(study) if path.exists()]


def test_sequence_ordinals_ignore_every_non_deliverable_export_path(
    tmp_path: Path,
) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    (unitypy / "Hero_Attack_2.png").write_bytes(b"stale-read-error")
    (unitypy / "Hero_Attack_3.json").write_text("{}", encoding="utf-8")
    (unitypy / "Hero_Attack_5.png").write_bytes(b"frame-five")
    (unitypy / "Hero_Attack_9.png").write_bytes(b"frame-nine")
    index = unitypy / "all_resources_index.csv"
    rows = [
        fixture_row(asset_id="1", asset_name="Hero_Attack_1", export_path=""),
        fixture_row(asset_id="2", asset_name="Hero_Attack_2", export_path="Hero_Attack_2.png"),
        fixture_row(
            asset_id="3",
            asset_name="Hero_Attack_3",
            export_path="Hero_Attack_3.json",
            export_kind="metadata",
        ),
        fixture_row(asset_id="4", asset_name="Hero_Attack_4", export_path="Hero_Attack_4.png"),
        fixture_row(asset_id="5", asset_name="Hero_Attack_5", export_path="Hero_Attack_5.png"),
        fixture_row(asset_id="9", asset_name="Hero_Attack_9", export_path="Hero_Attack_9.png"),
    ]
    rows[0]["status"] = "read_error"
    rows[1]["status"] = "read_error"
    write_index(index, rows)
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="DeliverableOrdinals")

    action = study / "1.分类资源" / "图片" / "序列帧" / "Hero" / "attack"
    assert (action / "attack_01.png").read_bytes() == b"frame-five"
    assert (action / "attack_02.png").read_bytes() == b"frame-nine"
    assert not (action / "attack_03.png").exists()
    sequence_rows = list(csv.DictReader(artifact_group_paths(study)[1].open(encoding="utf-8-sig")))
    assert [row["source_frame_number"] for row in sequence_rows] == ["5", "9"]
    assert [row["normalized_frame_number"] for row in sequence_rows] == ["1", "2"]
    assert summary["sequence_frame_count"] == 2


def test_multi_export_paths_use_independent_classification_contexts(
    tmp_path: Path,
) -> None:
    unitypy = tmp_path / "UnityPy"
    for relative, content in {
        "ui/menu.png": b"ui",
        "effects/smoke.png": b"effect",
        "scenes/forest.png": b"scene",
        "props/key.png": b"prop",
    }.items():
        path = unitypy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            fixture_row(
                asset_id="mixed-a",
                asset_name="NeutralA",
                export_path="ui/menu.png;effects/smoke.png",
            ),
            fixture_row(
                asset_id="mixed-b",
                asset_name="NeutralB",
                export_path="scenes/forest.png;props/key.png",
                path_id="2",
            ),
        ],
    )
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="PerPathContext")

    rows = list(csv.DictReader(artifact_group_paths(study)[0].open(encoding="utf-8-sig")))
    categories = {row["source_export_path"]: row["category"] for row in rows}
    assert categories == {
        "ui/menu.png": "图片/UI",
        "effects/smoke.png": "特效/粒子贴图",
        "scenes/forest.png": "图片/场景",
        "props/key.png": "图片/道具",
    }
    assert summary["category_counts"] == {
        "图片/UI": 1,
        "图片/场景": 1,
        "图片/道具": 1,
        "特效/粒子贴图": 1,
    }


def test_multi_export_sequence_paths_use_independent_scopes_and_ordinals(
    tmp_path: Path,
) -> None:
    unitypy = tmp_path / "UnityPy"
    rows = []
    for frame in (1, 2):
        relative_paths = [
            f"sets/a/Hero_Attack_{frame}.png",
            f"sets/b/Hero_Attack_{frame}.png",
        ]
        for relative in relative_paths:
            path = unitypy / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{relative}:{frame}".encode("ascii"))
        rows.append(
            fixture_row(
                asset_id=str(frame),
                asset_name=f"Hero_Attack_{frame}",
                export_path=";".join(relative_paths),
                path_id=str(frame),
            )
        )
    index = unitypy / "all_resources_index.csv"
    write_index(index, rows)
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="PerPathSequenceScope")

    sequence_rows = list(csv.DictReader(artifact_group_paths(study)[1].open(encoding="utf-8-sig")))
    by_scope: dict[str, list[dict[str, str]]] = {}
    for row in sequence_rows:
        by_scope.setdefault(row["sequence_scope"], []).append(row)
    assert summary["sequence_detection"]["confirmed_group_count"] == 2
    assert summary["sequence_frame_count"] == 4
    assert len(by_scope) == 2
    assert all(
        [item["normalized_frame_number"] for item in sorted(items, key=lambda item: item["source_frame_number"])]
        == ["1", "2"]
        for items in by_scope.values()
    )
    assert {row["source_export_path"].rsplit("/", 1)[0] for row in sequence_rows} == {
        "sets/a",
        "sets/b",
    }
    assert len({row["output_relative_path"] for row in sequence_rows}) == 4


def test_contract_and_config_declare_generation_and_canonical_path_guards() -> None:
    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))

    assert contract["source_policy"]["classified_resource_mutations"] == (
        "stable_directory_generation_handle_or_fd"
    )
    assert contract["source_policy"]["sequence_candidate_eligibility"] == (
        "per_export_path_deliverable_existing_and_filter_allowed"
    )
    assert contract["source_policy"]["multi_export_path_semantics"] == (
        "per_physical_path_context"
    )
    assert contract["source_policy"]["source_resource_reads"] == (
        "stable_root_to_file_handle_or_fd_same_object_hash_and_transfer"
    )
    assert contract["source_policy"]["classified_target_ownership"] == (
        "created_generation_bound_retire_then_handle_or_fd_delete_without_foreign_cleanup"
    )
    assert contract["source_policy"]["export_path_canonicalization"] == (
        "strict_posix_relative_with_windows_alias_control"
    )
    assert contract["source_policy"]["duplicate_physical_sources"] == (
        "deduplicate_exact_reject_windows_alias"
    )
    assert contract["control_input_policy"]["cleanup_status_publish"] == (
        "generation_bound_retire_then_no_replace"
    )
    assert contract["control_input_policy"]["abort_cleanup"] == (
        "attempt_all_and_aggregate"
    )
    assert contract["control_input_policy"]["cleanup_status_post_publish_validation"] == (
        "exact_identity_and_sha256_through_caller_success_handoff"
    )
    assert contract["control_input_policy"]["artifact_destination_binding"] == (
        "planned_and_staged_exact_generation_with_stable_parent_lease"
    )
    assert contract["control_input_policy"]["artifact_directory_lease"] == (
        "held_from_stage_through_commit_rollback_and_cleanup"
    )
    assert contract["control_input_policy"]["artifact_rename_reconciliation"] == (
        "exact_generation_postcondition_after_success_or_exception"
    )
    assert contract["control_input_policy"]["cleanup_status_success_handoff"] == (
        "explicit_close_or_context_after_consumption_cli_closes_after_serialization"
    )
    assert contract["control_input_policy"]["owned_generation_cleanup"] == (
        "retire_exact_generation_then_handle_or_fd_delete"
    )
    assert contract["control_input_policy"]["no_replace_publish_reconciliation"] == (
        "hardlink_and_copy_adopt_only_exact_published_generation"
    )
    assert config["export_path_policy"] == {
        "canonical_format": "strict_posix_relative",
        "duplicate_semantics": "windows_case_insensitive",
        "exact_duplicate": "deduplicate",
        "alias_spelling": "reject",
    }


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows junction semantics")
def test_source_file_lease_blocks_post_check_parent_junction_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    images = unitypy / "images"
    images.mkdir(parents=True)
    (images / "NeutralImage.png").write_bytes(b"authorized-inside")
    parked = unitypy / "images-parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "NeutralImage.png"
    outside_file.write_bytes(b"external-outside")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [fixture_row(asset_id="1", asset_name="NeutralImage", export_path="images/NeutralImage.png")],
    )
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", ["copy"]),
    )
    original_get = organizer.HashCache.get
    attempted = False
    swapped = False
    swap_error = ""

    def attempt_swap_during_hash(
        cache: organizer.HashCache,
        source: organizer.SourceFileLease | Path,
    ) -> str:
        nonlocal attempted, swapped, swap_error
        if not attempted:
            attempted = True
            try:
                os.rename(images, parked)
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(images), str(outside)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if result.returncode != 0:
                    raise OSError(result.stdout + result.stderr)
                swapped = True
            except OSError as exc:
                swap_error = f"{type(exc).__name__}: {exc}"
        return original_get(cache, source)

    monkeypatch.setattr(organizer.HashCache, "get", attempt_swap_during_hash)
    study = tmp_path / "Study"
    try:
        summary = organize(
            index,
            unitypy,
            study,
            project_name="StableSource",
            config_path=config_path,
        )
    finally:
        if organizer._is_reparse(images):
            os.rmdir(images)
        if parked.exists() and not images.exists():
            os.rename(parked, images)

    outputs = list((study / "1.分类资源").rglob("*.png"))
    assert attempted is True
    assert swapped is False
    assert swap_error
    assert summary["ok"] is True
    assert len(outputs) == 1
    assert outputs[0].read_bytes() == b"authorized-inside"
    assert outside_file.read_bytes() == b"external-outside"


def _summary_with_required_keys(project_name: str) -> dict[str, object]:
    required = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))["outputs"][
        "summary"
    ]["required_keys"]
    result: dict[str, object] = {key: None for key in required}
    result.update(
        {
            "schema_version": 1,
            "ok": True,
            "transaction_state": "committed",
            "cleanup_state": "warnings",
            "cleanup_warnings": [],
            "project_name": project_name,
            "output_validation": {},
        }
    )
    return result


def test_cleanup_status_success_handoff_pins_generation_until_explicit_close(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index" / "summary.json"
    summary_path.parent.mkdir()
    old = _summary_with_required_keys("OldGeneration")
    intended = _summary_with_required_keys("IntendedGeneration")
    external = _summary_with_required_keys("ExternalLateWriter")
    encode = lambda value: (
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        + "\n"
    ).encode("utf-8")
    old_bytes = encode(old)
    intended_bytes = encode(intended)
    external_path = summary_path.with_name("external.json")
    summary_path.write_bytes(old_bytes)
    external_path.write_bytes(encode(external))
    required_keys = list(
        json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))["outputs"]["summary"][
            "required_keys"
        ]
    )

    result = organizer._rewrite_committed_summary_cleanup_state(
        summary_path,
        intended,
        required_keys,
        hashlib.sha256(old_bytes).hexdigest().upper(),
    )
    retired = list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))
    assert result["ok"] is True
    assert json.loads(json.dumps(result))["ok"] is True
    assert len(retired) == 1
    assert retired[0].read_bytes() == old_bytes

    replaced = False
    try:
        os.replace(external_path, summary_path)
        replaced = True
    except OSError:
        pass
    if replaced:
        with pytest.raises(organizer.InputChangedError):
            result.close()
        assert retired[0].read_bytes() == old_bytes
    else:
        result.close()
        assert summary_path.read_bytes() == intended_bytes
        assert not list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))


@pytest.mark.parametrize("phase", ["retire", "publish"])
def test_cleanup_status_rename_success_then_wrapper_error_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    summary_path = tmp_path / "index" / "summary.json"
    summary_path.parent.mkdir()
    old = _summary_with_required_keys("OldGeneration")
    intended = _summary_with_required_keys("IntendedGeneration")
    encode = lambda value: (
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        + "\n"
    ).encode("utf-8")
    old_bytes = encode(old)
    intended_bytes = encode(intended)
    summary_path.write_bytes(old_bytes)
    injected = False
    if os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def fail_after_rename(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal injected
            source_name = organizer._windows_binary_handle_path(handle).name
            original_rename(handle, root_handle, target_name)
            selected = (
                phase == "retire" and ".ga-cleanup-status-retired-" in target_name
            ) or (
                phase == "publish"
                and source_name.endswith(".partial")
                and target_name == summary_path.name
            )
            if selected and not injected:
                injected = True
                raise OSError(f"synthetic post-{phase} wrapper failure")

        monkeypatch.setattr(organizer, "_windows_rename_binary_handle", fail_after_rename)
    else:
        original_publish = organizer._lease_publish_no_replace

        def fail_after_rename_posix(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            destination_name: str,
        ) -> None:
            nonlocal injected
            original_publish(lease, source_name, destination_name)
            selected = (
                phase == "retire" and ".ga-cleanup-status-retired-" in destination_name
            ) or (
                phase == "publish"
                and source_name.endswith(".partial")
                and destination_name == summary_path.name
            )
            if selected and not injected:
                injected = True
                raise OSError(f"synthetic post-{phase} wrapper failure")

        monkeypatch.setattr(
            organizer,
            "_lease_publish_no_replace",
            fail_after_rename_posix,
        )
    required_keys = list(
        json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))["outputs"]["summary"][
            "required_keys"
        ]
    )

    result = organizer._rewrite_committed_summary_cleanup_state(
        summary_path,
        intended,
        required_keys,
        hashlib.sha256(old_bytes).hexdigest().upper(),
    )
    assert injected is True
    assert result["ok"] is True
    result.close()
    assert summary_path.read_bytes() == intended_bytes
    assert not list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))


@pytest.mark.skipif(os.name != "nt", reason="real junction replacement is Windows-specific")
def test_all_staged_artifacts_keep_root_directory_pinned_against_junction_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unitypy = tmp_path / "UnityPy"
    unitypy.mkdir()
    index = unitypy / "all_resources_index.csv"
    write_index(index, [])
    study = tmp_path / "Study"
    temporary_root = study / "4.临时目录"
    parked = tmp_path / "parked-temporary-root"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside-baseline")
    original_commit_group = organizer._commit_artifact_group
    attempted = False
    swapped = False
    swap_error = ""

    def attempt_root_swap(items: list[organizer.ArtifactGroupItem], *args: object, **kwargs: object):
        nonlocal attempted, swapped, swap_error
        attempted = True
        assert len(items) == 6
        assert all(item.temporary.exists() for item in items)
        try:
            os.rename(temporary_root, parked)
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(temporary_root), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                raise OSError(result.stdout + result.stderr)
            swapped = True
        except OSError as exc:
            swap_error = f"{type(exc).__name__}: {exc}"
        return original_commit_group(items, *args, **kwargs)

    monkeypatch.setattr(organizer, "_commit_artifact_group", attempt_root_swap)
    try:
        summary = organize(index, unitypy, study, project_name="PinnedArtifacts")
    finally:
        if organizer._is_reparse(temporary_root):
            os.rmdir(temporary_root)
        if parked.exists() and not temporary_root.exists():
            os.rename(parked, temporary_root)

    assert attempted is True
    assert swapped is False
    assert swap_error
    assert summary["ok"] is True
    assert sentinel.read_bytes() == b"outside-baseline"
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.parametrize("transfer_mode", ["hardlink", "copy"])
def test_publish_success_then_wrapper_error_has_no_ghost_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transfer_mode: str,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"authorized-source")
    output_root = tmp_path / "classified"
    destination = output_root / "图片" / "UI" / "Asset.png"
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", [transfer_mode]),
    )
    rules = organizer.load_runtime_rules(config_path)
    injected = False

    if transfer_mode == "hardlink":
        helper_name = (
            "_windows_link_source_handle_to_lease"
            if os.name == "nt"
            else "_posix_link_source_handle_to_lease"
        )
        original_link = getattr(organizer, helper_name)

        def fail_after_link(*args: object, **kwargs: object) -> None:
            nonlocal injected
            original_link(*args, **kwargs)
            injected = True
            raise OSError("synthetic post-hardlink wrapper failure")

        monkeypatch.setattr(organizer, helper_name, fail_after_link)
    elif os.name == "nt":
        original_rename = organizer._windows_rename_binary_handle

        def fail_after_copy_publish(
            handle: object,
            root_handle: int,
            target_name: str,
        ) -> None:
            nonlocal injected
            source_name = organizer._windows_binary_handle_path(handle).name
            original_rename(handle, root_handle, target_name)
            if source_name.endswith(".partial") and target_name == destination.name and not injected:
                injected = True
                raise OSError("synthetic post-copy wrapper failure")

        monkeypatch.setattr(organizer, "_windows_rename_binary_handle", fail_after_copy_publish)
    else:
        original_publish = organizer._lease_publish_no_replace

        def fail_after_copy_publish_posix(
            lease: organizer.StableDirectoryLease,
            source_name: str,
            target_name: str,
        ) -> None:
            nonlocal injected
            original_publish(lease, source_name, target_name)
            if source_name.endswith(".partial") and target_name == destination.name and not injected:
                injected = True
                raise OSError("synthetic post-copy wrapper failure")

        monkeypatch.setattr(
            organizer,
            "_lease_publish_no_replace",
            fail_after_copy_publish_posix,
        )

    result = organizer._materialize(
        source,
        destination,
        organizer._sha256(source),
        "A" * 64,
        output_root,
        rules,
        organizer.ClassifiedOutputBoundary(output_root).initialize(),
    )

    assert injected is True
    assert result.operation_status == "created"
    assert result.transfer_method == transfer_mode
    assert destination.read_bytes() == source.read_bytes()
    assert not list(output_root.rglob("*.partial"))


def test_generation_bound_delete_preserves_late_path_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "owned"
    directory.mkdir()
    target = directory / "artifact.bin"
    target.write_bytes(b"transaction-owned")
    owner = organizer.OwnedStableDirectoryLease(directory)
    expected = organizer._lease_artifact_state(owner.lease, target.name)
    late_bytes = b"external-late-generation"
    injected = False
    try:
        if os.name == "nt":
            original_rename = organizer._windows_rename_binary_handle

            def inject_after_retire(
                handle: object,
                root_handle: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                source_name = organizer._windows_binary_handle_path(handle).name
                original_rename(handle, root_handle, target_name)
                if (
                    source_name.casefold() == target.name.casefold()
                    and ".ga-owned-retired-" in target_name
                    and not injected
                ):
                    target.write_bytes(late_bytes)
                    injected = True

            monkeypatch.setattr(organizer, "_windows_rename_binary_handle", inject_after_retire)
        else:
            original_move = organizer._lease_move_no_replace_reconciled

            def inject_after_retire_posix(
                lease: organizer.StableDirectoryLease,
                source_name: str,
                destination_name: str,
                current: organizer.ArtifactPathState,
            ) -> tuple[bool, BaseException | None]:
                nonlocal injected
                moved, failure = original_move(lease, source_name, destination_name, current)
                if moved and source_name == target.name and ".ga-owned-retired-" in destination_name:
                    target.write_bytes(late_bytes)
                    injected = True
                return moved, failure

            monkeypatch.setattr(
                organizer,
                "_lease_move_no_replace_reconciled",
                inject_after_retire_posix,
            )

        organizer._lease_unlink_owned_generation(owner.lease, target.name, expected)
    finally:
        owner.close()

    assert injected is True
    assert target.read_bytes() == late_bytes
    assert not list(directory.glob("*.ga-owned-retired-*.partial"))


def test_cleanup_status_post_publish_replacement_never_returns_success_or_deletes_old(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path = tmp_path / "index" / "summary.json"
    summary_path.parent.mkdir()
    old = _summary_with_required_keys("OldGeneration")
    intended = _summary_with_required_keys("IntendedGeneration")
    external = _summary_with_required_keys("ExternalLateWriter")
    encode = lambda value: (
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        + "\n"
    ).encode("utf-8")
    old_bytes = encode(old)
    intended_bytes = encode(intended)
    external_bytes = encode(external)
    summary_path.write_bytes(old_bytes)
    original_validate = organizer._lease_validate_summary
    attempted = False
    replaced = False

    def replace_after_publish(
        lease: organizer.StableDirectoryLease,
        name: str,
        required_keys: list[str],
    ) -> None:
        nonlocal attempted, replaced
        if name == summary_path.name and not attempted:
            attempted = True
            late = summary_path.with_name("external-late.json")
            late.write_bytes(external_bytes)
            try:
                os.replace(late, summary_path)
                replaced = True
            except OSError:
                pass
        original_validate(lease, name, required_keys)

    monkeypatch.setattr(organizer, "_lease_validate_summary", replace_after_publish)
    required_keys = list(
        json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))["outputs"]["summary"][
            "required_keys"
        ]
    )
    if os.name == "nt":
        result = organizer._rewrite_committed_summary_cleanup_state(
            summary_path,
            intended,
            required_keys,
            hashlib.sha256(old_bytes).hexdigest().upper(),
        )
        assert result["ok"] is True
        assert list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))
        result.close()
    else:
        with pytest.raises((OSError, organizer.InputChangedError)):
            organizer._rewrite_committed_summary_cleanup_state(
                summary_path,
                intended,
                required_keys,
                hashlib.sha256(old_bytes).hexdigest().upper(),
            )

    assert attempted is True
    if replaced:
        assert summary_path.read_bytes() == external_bytes
        retired = list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))
        assert any(path.read_bytes() == old_bytes for path in retired)
    else:
        assert summary_path.read_bytes() == intended_bytes
        assert not list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))


def test_cleanup_status_rejects_same_bytes_from_a_nontransaction_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_path = tmp_path / "index" / "summary.json"
    summary_path.parent.mkdir()
    old_bytes = (
        json.dumps(
            _summary_with_required_keys("OldGeneration"),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    intended = _summary_with_required_keys("IntendedGeneration")
    intended_bytes = (
        json.dumps(
            intended,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    summary_path.write_bytes(old_bytes)
    original_move = organizer._lease_move_no_replace_reconciled
    replaced = False

    def replace_immediately_after_publish(
        lease: organizer.StableDirectoryLease,
        source_name: str,
        destination_name: str,
        expected: organizer.ArtifactPathState,
    ) -> tuple[bool, BaseException | None]:
        nonlocal replaced
        moved, failure = original_move(lease, source_name, destination_name, expected)
        if moved and destination_name == summary_path.name and not replaced:
            external = summary_path.with_name("external-same-bytes.json")
            external.write_bytes(intended_bytes)
            os.replace(external, summary_path)
            replaced = True
        return moved, failure

    monkeypatch.setattr(
        organizer,
        "_lease_move_no_replace_reconciled",
        replace_immediately_after_publish,
    )
    required_keys = list(
        json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))["outputs"]["summary"][
            "required_keys"
        ]
    )

    with pytest.raises(organizer.InputChangedError, match="exact temporary generation"):
        organizer._rewrite_committed_summary_cleanup_state(
            summary_path,
            intended,
            required_keys,
            hashlib.sha256(old_bytes).hexdigest().upper(),
        )

    assert replaced is True
    assert summary_path.read_bytes() == intended_bytes
    retired = list(summary_path.parent.glob("*.ga-cleanup-status-retired-*.bak"))
    assert any(path.read_bytes() == old_bytes for path in retired)


@pytest.mark.parametrize("transfer_mode", ["hardlink", "copy"])
@pytest.mark.parametrize("collision", [False, True])
def test_classified_target_generation_drift_never_deletes_late_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transfer_mode: str,
    collision: bool,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"authorized-source")
    output_root = tmp_path / "classified"
    destination = output_root / "图片" / "UI" / "Asset.png"
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", [transfer_mode]),
    )
    rules = organizer.load_runtime_rules(config_path)
    identity = "D" * 64
    if collision:
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"occupied-base")
        target = organizer._stable_collision_path(
            destination,
            identity,
            rules.identity_hash_length,
        )
    else:
        target = destination
    boundary = organizer.ClassifiedOutputBoundary(output_root).initialize()
    original_same = organizer._lease_same_content
    attempted = False
    replaced = False
    late_bytes = b"late-writer-generation"

    def replace_before_hash_verification(
        current_source: organizer.SourceFileLease | Path,
        lease: organizer.StableDirectoryLease,
        name: str,
        expected_hash: str,
    ) -> bool:
        nonlocal attempted, replaced
        if name == target.name and not attempted and (lease.path / name).exists():
            attempted = True
            late = lease.path / f".late-writer-{name}"
            late.write_bytes(late_bytes)
            try:
                os.replace(late, lease.path / name)
                replaced = True
            except OSError:
                pass
        return original_same(current_source, lease, name, expected_hash)

    monkeypatch.setattr(organizer, "_lease_same_content", replace_before_hash_verification)
    source_hash = organizer._sha256(source)
    raised: BaseException | None = None
    result: organizer.MaterializeResult | None = None
    try:
        result = organizer._materialize(
            source,
            destination,
            source_hash,
            identity,
            output_root,
            rules,
            boundary,
        )
    except BaseException as exc:
        raised = exc

    assert attempted is True
    if replaced:
        assert isinstance(raised, (OSError, organizer.InputChangedError))
        assert target.read_bytes() == late_bytes
    else:
        assert raised is None
        assert result is not None
        assert target.read_bytes() == b"authorized-source"
        assert (target.parent / f".late-writer-{target.name}").read_bytes() == late_bytes


def test_failed_verification_cleans_only_the_created_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"authorized-source")
    output_root = tmp_path / "classified"
    destination = output_root / "图片" / "UI" / "Asset.png"
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", ["copy"]),
    )
    rules = organizer.load_runtime_rules(config_path)

    monkeypatch.setattr(organizer, "_lease_same_content", lambda *_args: False)
    with pytest.raises(OSError, match="failed hash verification"):
        organizer._materialize(
            source,
            destination,
            organizer._sha256(source),
            "E" * 64,
            output_root,
            rules,
            organizer.ClassifiedOutputBoundary(output_root).initialize(),
        )

    assert not destination.exists()
    assert not list(output_root.rglob("*.partial"))


@pytest.mark.parametrize("transfer_mode", ["hardlink", "copy"])
def test_classified_target_preverify_gap_rejects_same_bytes_foreign_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transfer_mode: str,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"authorized-source")
    output_root = tmp_path / "classified"
    destination = output_root / "图片" / "UI" / "Asset.png"
    config_path = write_custom_config(
        tmp_path,
        lambda config: config.__setitem__("transfer_methods", [transfer_mode]),
    )
    rules = organizer.load_runtime_rules(config_path)
    original_transfer = organizer._transfer_new
    attempted = False
    replaced = False
    external_identity: tuple[int, int] | None = None

    def replace_after_transfer_before_verify(
        current_source: organizer.SourceFileLease,
        current_destination: Path,
        methods: tuple[str, ...],
        lease: organizer.StableDirectoryLease,
    ) -> organizer.TransferResult:
        nonlocal attempted, replaced, external_identity
        result = original_transfer(current_source, current_destination, methods, lease)
        attempted = True
        late = lease.path / f".late-gap-{current_destination.name}"
        late.write_bytes(b"authorized-source")
        late_stat = late.stat()
        external_identity = (late_stat.st_dev, late_stat.st_ino)
        try:
            os.replace(late, lease.path / current_destination.name)
            replaced = True
        except OSError:
            pass
        return result

    monkeypatch.setattr(organizer, "_transfer_new", replace_after_transfer_before_verify)
    raised: BaseException | None = None
    result: organizer.MaterializeResult | None = None
    try:
        result = organizer._materialize(
            source,
            destination,
            organizer._sha256(source),
            "F" * 64,
            output_root,
            rules,
            organizer.ClassifiedOutputBoundary(output_root).initialize(),
        )
    except BaseException as exc:
        raised = exc

    assert attempted is True
    assert external_identity is not None, repr(raised)
    if replaced:
        assert isinstance(raised, RuntimeError)
        assert destination.exists()
        destination_stat = destination.stat()
        assert (destination_stat.st_dev, destination_stat.st_ino) == external_identity
        assert destination.read_bytes() == source.read_bytes()
    else:
        assert raised is None
        assert result is not None
        assert os.path.samefile(source, destination)
        assert (destination.parent / f".late-gap-{destination.name}").exists()


@pytest.mark.parametrize(
    "value",
    [
        "images/./UI.png",
        "images//UI.png",
        "images\\UI.png",
        "../UI.png",
        "/images/UI.png",
        " images/UI.png",
        "images/UI.png ",
        "images/NUL.png",
        "images/aux.txt",
        "images/COM1.bin",
        "images/name./UI.png",
        "images/name /UI.png",
        "images/UI:stream.png",
        'images/UI?.png',
        'images/UI|alt.png',
        "images/Cafe\u0301.png",
        ".",
        "images/UI.png;",
    ],
)
def test_export_paths_require_strict_canonical_posix_spelling(value: str) -> None:
    with pytest.raises(UnsafePathError):
        organizer.split_export_paths(value)


def test_exact_duplicate_physical_export_path_is_organized_once(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    image = unitypy / "images" / "UI_Menu.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            fixture_row(
                asset_id="1",
                asset_name="UI_Menu",
                export_path="images/UI_Menu.png;images/UI_Menu.png",
            )
        ],
    )
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="Deduplicate")

    resource_rows = list(
        csv.DictReader(artifact_group_paths(study)[0].open(encoding="utf-8-sig"))
    )
    assert summary["ok"] is True
    assert summary["export_path_count"] == 1
    assert summary["duplicate_export_path_count"] == 1
    assert summary["organized_count"] == 1
    assert len(resource_rows) == 1


def test_exact_duplicate_across_index_rows_is_organized_once(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    image = unitypy / "images" / ".UI_Menu.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    duplicate = "images/.UI_Menu.png"
    write_index(
        index,
        [
            fixture_row(asset_id="1", asset_name="UI_Menu", export_path=duplicate),
            fixture_row(asset_id="2", asset_name="UI_Menu_AliasRow", export_path=duplicate),
        ],
    )
    study = tmp_path / "Study"

    summary = organize(index, unitypy, study, project_name="GlobalDeduplicate")

    resource_rows = list(
        csv.DictReader(artifact_group_paths(study)[0].open(encoding="utf-8-sig"))
    )
    assert summary["ok"] is True
    assert summary["source_row_count"] == 2
    assert summary["export_path_count"] == 1
    assert summary["duplicate_export_path_count"] == 1
    assert summary["organized_count"] == 1
    assert len(resource_rows) == 1


def test_windows_semantic_export_path_alias_fails_closed(tmp_path: Path) -> None:
    unitypy = tmp_path / "UnityPy"
    image = unitypy / "images" / "UI_Menu.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"menu")
    index = unitypy / "all_resources_index.csv"
    write_index(
        index,
        [
            fixture_row(
                asset_id="1",
                asset_name="UI_Menu",
                export_path="images/UI_Menu.png;IMAGES/ui_menu.PNG",
            )
        ],
    )
    study = tmp_path / "Study"

    with pytest.raises(UnsafePathError, match="alias under Windows semantics"):
        organize(index, unitypy, study, project_name="AliasRejected")

    assert not list((study / "1.分类资源").rglob("*"))

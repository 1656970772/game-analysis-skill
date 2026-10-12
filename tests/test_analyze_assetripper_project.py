from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_assetripper_project import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    analyze_layout_mismatch,
    analyze_render_settings,
    analyze_yaml_asset,
    load_strict_json,
    main,
    parse_build_settings,
    read_unity_editor_version,
    reconcile_build_settings,
    scene_group,
    write_report,
)


MINIMAL_UNITY_YAML = """--- !u!1 &1
GameObject:
  m_Name: Root
  m_Component:
  - component: {fileID: 2}
--- !u!4 &2
Transform:
  m_GameObject: {fileID: 1}
  m_Father: {fileID: 0}
"""


def write_yaml_asset(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MINIMAL_UNITY_YAML, encoding="utf-8")


def write_project_version(project_root: Path, content: str) -> None:
    version_path = project_root / "ProjectSettings" / "ProjectVersion.txt"
    version_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text(content, encoding="utf-8")


def write_project_settings(project_root: Path) -> None:
    settings_root = project_root / "ProjectSettings"
    settings_root.mkdir(parents=True, exist_ok=True)
    (settings_root / "EditorBuildSettings.asset").write_text(
        """EditorBuildSettings:
  m_Scenes:
  - enabled: 1
    path: Assets/Scenes/bootstrap.unity
    guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  - enabled: 0
    path: Assets/Scenes/optional.unity
    guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""",
        encoding="utf-8",
    )
    (settings_root / "GraphicsSettings.asset").write_text(
        """GraphicsSettings:
  m_CustomRenderPipeline: {fileID: 0}
  futureGraphicsToggle: 17
""",
        encoding="utf-8",
    )
    (settings_root / "QualitySettings.asset").write_text(
        """QualitySettings:
  m_QualitySettings:
  - serializedVersion: 3
    name: Portable
    antiAliasing: 0
    futureQualitySetting: 11
  - serializedVersion: 3
    name: Cinema
    antiAliasing: 8
    futureQualitySetting: 22
  m_PerPlatformDefaultQuality:
    Android: 0
    Standalone: 1
""",
        encoding="utf-8",
    )
    (settings_root / "ProjectSettings.asset").write_text(
        """PlayerSettings:
  defaultScreenWidth: 1600
  defaultScreenHeight: 900
  futurePlayerSetting: experimental
""",
        encoding="utf-8",
    )
    packages_root = project_root / "Packages"
    packages_root.mkdir(parents=True, exist_ok=True)
    (packages_root / "manifest.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "com.example.dynamic": "1.2.3",
                    "com.unity.modules.audio": "1.0.0",
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_read_unity_editor_version_detects_project_version(tmp_path: Path) -> None:
    project_root = tmp_path / "ExportedProject"
    write_project_version(
        project_root,
        "m_EditorVersion: 2018.4.36f1\nm_EditorVersionWithRevision: 2018.4.36f1 (deadbeef)\n",
    )

    assert read_unity_editor_version(project_root) == ("2018.4.36f1", "detected")


def test_read_unity_editor_version_marks_missing_and_unrecognized_files(tmp_path: Path) -> None:
    project_root = tmp_path / "ExportedProject"
    assert read_unity_editor_version(project_root) == ("unknown", "missing")

    write_project_version(project_root, "m_EditorVersion: not-a-unity-version\n")
    assert read_unity_editor_version(project_root) == ("unknown", "unrecognized")


def test_scene_group_supports_common_scene_layouts_and_root_boundaries(tmp_path: Path) -> None:
    project_root = tmp_path / "ExportedProject"
    cases = {
        project_root / "Assets" / "Scenes" / "Level2(Crane)" / "room.unity": "Level2(Crane)",
        project_root / "Assets" / "Game" / "Scenes" / "Levels" / "Forest" / "room.unity": "Forest",
        project_root / "Assets" / "Scenes" / "bootstrap.unity": "ScenesRoot",
        project_root / "Assets" / "Game" / "Scenes" / "bootstrap.unity": "ScenesRoot",
        project_root / "Assets" / "Game" / "Scenes" / "Levels" / "bootstrap.unity": "LevelsRoot",
    }

    for path, expected in cases.items():
        assert scene_group(path, project_root) == expected


def test_build_settings_uses_new_enabled_as_hard_boundary_and_reconciles_scene_set(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "ExportedProject"
    write_project_settings(project_root)
    config = load_strict_json(DEFAULT_CONFIG_PATH)

    entries = parse_build_settings(
        project_root / "ProjectSettings" / "EditorBuildSettings.asset",
        config,
    )
    reconciliation = reconcile_build_settings(
        entries,
        [
            {"relative_path": "Assets/Scenes/bootstrap.unity"},
            {"relative_path": "Assets/Scenes/index-only.unity"},
        ],
    )

    assert [(item["order"], item["enabled"], item["path"], item["guid"]) for item in entries] == [
        (0, True, "Assets/Scenes/bootstrap.unity", "a" * 32),
        (1, False, "Assets/Scenes/optional.unity", "b" * 32),
    ]
    assert entries[0]["anchors"]["path"]["line"] < entries[1]["anchors"]["enabled"]["line"]
    assert reconciliation["sets_match"] is False
    assert reconciliation["build_only_paths"] == ["Assets/Scenes/optional.unity"]
    assert reconciliation["index_only_paths"] == ["Assets/Scenes/index-only.unity"]


def test_build_settings_does_not_borrow_path_from_next_record(tmp_path: Path) -> None:
    path = tmp_path / "EditorBuildSettings.asset"
    path.write_text(
        """EditorBuildSettings:
  m_Scenes:
  - enabled: 1
    guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  - enabled: 1
    path: Assets/Scenes/second.unity
    guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""",
        encoding="utf-8",
    )

    try:
        parse_build_settings(path, load_strict_json(DEFAULT_CONFIG_PATH))
    except ValueError as exc:
        assert "before the next '- enabled' record" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("malformed first record must not consume the second record path")


def test_render_settings_extract_dynamic_values_names_platforms_and_package_dependencies(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "ExportedProject"
    write_project_settings(project_root)

    result = analyze_render_settings(project_root, load_strict_json(DEFAULT_CONFIG_PATH))

    assert result["quality"]["level_count"] == 2
    assert [level["name"] for level in result["quality"]["levels"]] == ["Portable", "Cinema"]
    assert [item["key"] for item in result["quality"]["platform_defaults"]] == ["Android", "Standalone"]
    assert result["quality"]["platform_defaults"][1]["level_name"] == "Cinema"
    assert any(item["key"] == "futureGraphicsToggle" and item["value"] == 17 for item in result["graphics"]["settings"])
    assert any(
        item["key"] == "futureQualitySetting" and item["value"] == 22
        for item in result["quality"]["levels"][1]["settings"]
    )
    assert any(item["key"] == "futurePlayerSetting" for item in result["player"]["settings"])
    assert [item["name"] for item in result["packages"]["dependencies"]] == [
        "com.example.dynamic",
        "com.unity.modules.audio",
    ]
    assert result["pipeline"]["judgment"] == "Built-in Render Pipeline"


def test_layout_mismatch_boundary_is_configured_and_component_names_come_from_input(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "runtime-verification.json"
    verification_path.write_text(
        json.dumps(
            {
                "runtime_log": {
                    "layout_mismatch_count": 7,
                    "selected_layout_mismatches": [
                        {"type": "FutureBrokenComponent", "count": 7, "guid": "abc123"}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    result = analyze_layout_mismatch(verification_path, load_strict_json(DEFAULT_CONFIG_PATH))

    assert result["status"] == "provided"
    assert result["total_count"] == 7
    assert result["affected_components"] == [
        {"name": "FutureBrokenComponent", "count": 7, "guid": "abc123"}
    ]
    assert any("FutureBrokenComponent" in item for item in result["forbidden_inference"])
    assert all("MapSeaCompent" not in item for item in result["forbidden_inference"])
    assert result["source"]["sha256"]


def test_report_derives_learning_entries_from_scanned_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "ExportedProject"
    scene_path = project_root / "Assets" / "Scenes" / "Level2(Crane)" / "room.unity"
    prefab_path = project_root / "Assets" / "Resources" / "prefabs" / "enemy" / "wolf.prefab"
    write_yaml_asset(scene_path)
    write_yaml_asset(prefab_path)

    scene_rows = [analyze_yaml_asset(scene_path, project_root)]
    prefab_rows = [analyze_yaml_asset(prefab_path, project_root)]
    file_rows = [
        {"relative_path": "Assets/Scenes/Level2(Crane)/room.unity", "size_bytes": scene_path.stat().st_size},
        {
            "relative_path": "Assets/Resources/prefabs/enemy/wolf.prefab",
            "size_bytes": prefab_path.stat().st_size,
        },
    ]
    report_path = tmp_path / "场景搭建分析.md"

    write_report(
        report_path,
        project_root,
        "测试项目",
        file_rows,
        Counter({".unity": 1, ".prefab": 1}),
        Counter({"scene": 1, "prefab": 1}),
        scene_rows,
        prefab_rows,
        [],
        "unknown",
        "missing",
    )

    report = report_path.read_text(encoding="utf-8")
    assert "场景学习入口（来自扫描）" in report
    assert "`Assets/Scenes`" in report
    assert "Prefab 学习入口（来自扫描）" in report
    assert "`Assets/Resources/prefabs`" in report
    assert "Assets/Game/Scenes/Levels" not in report
    assert "Assets/Game/Prefabs" not in report
    assert "运行时加载方式：待验证" in report
    assert "工程 Unity 编辑器版本为 `unknown`" in report
    assert "ProjectSettings/ProjectVersion.txt 缺失" in report


def test_main_writes_dynamic_editor_version_to_summary_and_report(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "ExportedProject"
    output_dir = tmp_path / "analysis"
    write_project_version(project_root, "m_EditorVersion: 2018.4.36f1\n")
    write_project_settings(project_root)
    write_yaml_asset(project_root / "Assets" / "Scenes" / "bootstrap.unity")
    runtime_verification = tmp_path / "runtime-verification.json"
    runtime_verification.write_text(
        json.dumps(
            {
                "runtime_log": {
                    "layout_mismatch_count": 2,
                    "selected_layout_mismatches": [
                        {"type": "SyntheticLayoutProblem", "count": 2, "guid": "c" * 32}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze_assetripper_project.py",
            "--project-root",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--title",
            "测试项目",
            "--runtime-verification",
            str(runtime_verification),
        ],
    )

    assert main() == 0
    summary_path = output_dir / "导出分析摘要.json"
    first_payload = summary_path.read_bytes()
    summary = json.loads(first_payload.decode("utf-8"))
    report = (output_dir / "场景搭建分析.md").read_text(encoding="utf-8")

    assert summary["unity_editor_version"] == "2018.4.36f1"
    assert summary["unity_editor_version_status"] == "detected"
    assert summary["build_settings"]["entries"][0]["order"] == 0
    assert summary["render_settings"]["quality"]["level_count"] == 2
    assert summary["layout_mismatch"]["affected_components"][0]["name"] == "SyntheticLayoutProblem"
    assert summary["sources"]["editor_build_settings"]["sha256"]
    assert summary["generation_verification"]["all_valid"] is True
    assert summary["generation_verification"]["json_reopen"] == "passed"
    assert "generated_at" not in summary
    assert "需要在 Unity 2018.4.36f1 或兼容版本中打开项目后" in report
    assert "2023.2.22f1" not in report

    assert main() == 0
    assert summary_path.read_bytes() == first_payload

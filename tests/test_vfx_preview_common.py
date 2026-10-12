from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.vfx_preview_common import (
    as_relative_path,
    ensure_inside_root,
    load_vfx_config,
    resolve_path,
    stable_vfx_id,
    vfx_paths,
    write_json_utf8,
    write_jsonl_utf8,
)


def test_load_vfx_config_contains_dynamic_unity_contract() -> None:
    config = load_vfx_config(ROOT)

    assert set(config) >= {
        "schema_version",
        "tool",
        "launcher",
        "paths",
        "manifest",
        "state_machine",
        "adapter",
        "unity_export",
        "name_keyword_rules",
        "limits",
    }
    assert config["schema_version"] == 1
    assert config["tool"]["name"] == "GameVfxViewer"
    assert config["tool"]["directory"] == "built-in-tools/GameVfxViewer_1.0.0"
    assert config["tool"]["source_template_dir"] == "assets/vfx-viewer-src"
    assert config["tool"]["exe_name"] == "GameVfxViewer.exe"
    assert config["launcher"]["environment_exe"] == "GAME_ANALYSIS_VFX_VIEWER"
    assert config["launcher"]["environment_skill_root"] == "GAME_ANALYSIS_SKILL_ROOT"
    assert config["unity_export"]["bootstrap_script_name"] == "GameAnalysisVfxBootstrap.cs"
    assert config["unity_export"]["asset_root"] == "Assets/GameAnalysisVfxPreview"
    assert config["manifest"]["preview_status"] == [
        "pending",
        "ready",
        "partial",
        "resource_only",
        "failed",
        "unsupported",
    ]
    assert set(config["manifest"]["reconstruction_status"]) >= {
        "pending",
        "full",
        "partial",
        "resource_only",
        "failed",
    }
    assert set(config["manifest"]["unity_export_status"]) >= {
        "pending",
        "ready",
        "partial",
        "skipped",
        "failed",
    }
    assert config["state_machine"]["manifest_initial"] == {
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }
    assert config["adapter"]["default_engine"] == "unity"
    assert set(config["adapter"]["unity"]["root_types"]) >= {
        "ParticleSystem",
        "TrailRenderer",
        "LineRenderer",
    }
    assert set(config["adapter"]["unity"]["supported_modules"]) >= {
        "main",
        "emission",
        "shape",
        "velocityOverLifetime",
        "colorOverLifetime",
        "sizeOverLifetime",
        "textureSheetAnimation",
        "renderer",
        "trail",
        "line",
    }
    assert config["adapter"]["unity"]["module_aliases"]["InitialModule"] == "main"
    assert config["adapter"]["unity"]["module_aliases"]["EmissionModule"] == "emission"
    assert config["adapter"]["unity"]["defaults"]["main"]["startSpeed"]["constant"] == 1.0
    assert config["adapter"]["unity"]["defaults"]["emission"]["rateOverTime"]["constant"] == 10.0
    assert set(config["vfx_type_rules"]) >= {
        "ParticleSystem",
        "ParticleSystemRenderer",
        "Texture2D",
        "Material",
        "Mesh",
        "AnimationClip",
    }
    assert config["vfx_type_rules"]["ParticleSystem"]["category"] == "配置/ParticleSystem"
    assert config["vfx_type_rules"]["ParticleSystem"]["kind"] == "particle_system_config"
    assert config["vfx_type_rules"]["ParticleSystem"]["preview_mode"] == "config_card"


def test_vfx_paths_are_inside_study_root(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)

    paths = vfx_paths(tmp_path, config)

    for value in paths.__dict__.values():
        if isinstance(value, Path):
            assert ensure_inside_root(value, tmp_path)
    assert config["paths"]["reconstructed_dir"] == "reconstructed"
    assert config["paths"]["preview_cache_dir"] == "preview-cache"
    assert config["paths"]["unity_export_dir"] == "unity-export"
    assert paths.launcher == tmp_path / "2.报告" / "打开特效预览.bat"
    assert paths.manifest == paths.preview_index_dir / config["paths"]["manifest_name"]
    assert paths.errors_jsonl == paths.preview_index_dir / config["paths"]["errors_name"]
    assert paths.reconstructed_dir == paths.preview_index_dir / "reconstructed"
    assert paths.preview_cache_dir == paths.preview_index_dir / "preview-cache"
    assert paths.unity_export_dir == paths.preview_index_dir / "unity-export"
    assert paths.unity_export_asset_root == paths.unity_export_dir / config["unity_export"]["asset_root"]


def test_ensure_inside_root_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "study"
    inside = root / "4.临时目录" / "中间索引" / "VFX预览"
    outside = tmp_path / "outside"

    assert ensure_inside_root(inside, root)
    assert not ensure_inside_root(outside, root)


def test_stable_vfx_id_is_deterministic() -> None:
    first = stable_vfx_id("resources.assets", "100", "ParticleSystem", "Fire/Loop")
    second = stable_vfx_id("resources.assets", "100", "ParticleSystem", r"Fire\Loop")
    different = stable_vfx_id("resources.assets", "101", "ParticleSystem", "Fire/Loop")

    assert first == second
    assert first != different
    assert len(first) == 16
    assert all(char in "0123456789abcdef" for char in first)


def test_write_json_and_jsonl_use_utf8(tmp_path: Path) -> None:
    json_path = tmp_path / "nested" / "manifest.json"
    jsonl_path = tmp_path / "nested" / "errors.jsonl"

    write_json_utf8(json_path, {"title": "Unity 特效预览", "items": []})
    write_jsonl_utf8(jsonl_path, [{"message": "粒子系统"}, {"message": "拖尾"}])

    assert json.loads(json_path.read_text(encoding="utf-8")) == {
        "title": "Unity 特效预览",
        "items": [],
    }
    assert [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()] == [
        {"message": "粒子系统"},
        {"message": "拖尾"},
    ]


@pytest.mark.parametrize(
    "value",
    [
        r"C:\absolute\path",
        r"\\server\share\manifest.json",
        "C:drive-relative",
        "../outside",
        "safe/../outside",
        "safe//empty",
    ],
)
def test_as_relative_path_rejects_windows_absolute_unc_drive_relative_traversal_and_empty_segments(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="config-relative path"):
        as_relative_path(value)


@pytest.mark.parametrize(
    "filename",
    [
        "CON",
        "NUL.txt",
        "bad:name.exe",
        "bad?.exe",
        "name. ",
        ".",
    ],
)
def test_load_vfx_config_rejects_windows_unsafe_filename_only_values(
    tmp_path: Path,
    filename: str,
) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config["tool"]["exe_name"] = filename
    config_path = tmp_path / "vfx-preview-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="tool.exe_name"):
        load_vfx_config(config_path)


@pytest.mark.parametrize(
    ("section", "key", "filename"),
    [
        ("launcher", "bat_name", "CON"),
        ("paths", "manifest_name", "bad:name.json"),
        ("paths", "errors_name", "NUL.txt"),
        ("unity_export", "bootstrap_script_name", "bad?.cs"),
    ],
)
def test_load_vfx_config_applies_windows_safe_filename_rules_to_single_file_fields(
    tmp_path: Path,
    section: str,
    key: str,
    filename: str,
) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config[section][key] = filename
    config_path = tmp_path / "vfx-preview-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{section}.{key}"):
        load_vfx_config(config_path)


def test_vfx_paths_and_resolve_path_reject_escape_attempts(tmp_path: Path) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config["paths"]["overview_name"] = "../escape.html"

    with pytest.raises(ValueError):
        resolve_path(tmp_path, "../escape")
    with pytest.raises(ValueError):
        vfx_paths(tmp_path, config)


def test_unity_reconstruction_schema_is_machine_validatable_json_schema() -> None:
    schema = json.loads((ROOT / "references" / "vfx-unity-reconstruction-schema.json").read_text(encoding="utf-8"))

    assert "$schema" in schema
    assert schema["type"] == "object"
    assert set(schema) >= {"properties", "required"}
    assert schema["properties"]["engine"]["enum"] == ["unity"]
    assert set(schema["required"]) >= {"schema_version", "engine", "id", "name", "kind", "modules"}
    module_properties = schema["properties"]["modules"]["properties"]
    for key in ["main", "emission", "shape", "renderer", "raw_unity"]:
        assert key in schema["properties"]["modules"]["required"]
        assert module_properties[key]["type"] == "object"


def write_temp_config(tmp_path: Path, config: dict[str, object]) -> Path:
    config_path = tmp_path / "vfx-preview-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_path


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config.pop("schema_version"), "schema_version"),
        (lambda config: config.__setitem__("schema_version", ""), "schema_version"),
        (lambda config: config["tool"].pop("name"), "tool.name"),
        (lambda config: config["tool"].__setitem__("name", ""), "tool.name"),
        (lambda config: config["tool"].pop("version"), "tool.version"),
        (lambda config: config["tool"].__setitem__("version", ""), "tool.version"),
        (lambda config: config["launcher"].pop("window_title"), "launcher.window_title"),
        (lambda config: config["launcher"].__setitem__("window_title", ""), "launcher.window_title"),
        (lambda config: config["launcher"].pop("environment_exe"), "launcher.environment_exe"),
        (lambda config: config["launcher"].__setitem__("environment_exe", ""), "launcher.environment_exe"),
        (lambda config: config["launcher"].pop("environment_skill_root"), "launcher.environment_skill_root"),
        (
            lambda config: config["launcher"].__setitem__("environment_skill_root", ""),
            "launcher.environment_skill_root",
        ),
        (lambda config: config["manifest"].pop("schema_version"), "manifest.schema_version"),
        (lambda config: config["manifest"].__setitem__("schema_version", ""), "manifest.schema_version"),
        (lambda config: config["manifest"].pop("max_error_records"), "manifest.max_error_records"),
        (lambda config: config["manifest"].__setitem__("max_error_records", ""), "manifest.max_error_records"),
        (lambda config: config["state_machine"].pop("preview_transitions"), "state_machine.preview_transitions"),
        (lambda config: config["state_machine"].__setitem__("preview_transitions", []), "state_machine.preview_transitions"),
        (
            lambda config: config["state_machine"].pop("reconstruction_transitions"),
            "state_machine.reconstruction_transitions",
        ),
        (
            lambda config: config["state_machine"].__setitem__("reconstruction_transitions", []),
            "state_machine.reconstruction_transitions",
        ),
        (
            lambda config: config["state_machine"].pop("unity_export_transitions"),
            "state_machine.unity_export_transitions",
        ),
        (
            lambda config: config["state_machine"].__setitem__("unity_export_transitions", []),
            "state_machine.unity_export_transitions",
        ),
        (
            lambda config: config["adapter"]["unity"]["module_aliases"].pop("EmissionModule"),
            "adapter.unity.module_aliases.EmissionModule",
        ),
        (
            lambda config: config["adapter"]["unity"]["module_aliases"].__setitem__("EmissionModule", ""),
            "adapter.unity.module_aliases.EmissionModule",
        ),
        (
            lambda config: config["adapter"]["unity"]["defaults"].pop("emission"),
            "adapter.unity.defaults.emission",
        ),
        (
            lambda config: config["adapter"]["unity"]["defaults"].__setitem__("emission", ""),
            "adapter.unity.defaults.emission",
        ),
        (lambda config: config.__setitem__("name_keyword_rules", "bad"), "name_keyword_rules"),
        (lambda config: config["name_keyword_rules"][0].pop("tag"), "name_keyword_rules[0].tag"),
        (lambda config: config["name_keyword_rules"][0].__setitem__("tag", ""), "name_keyword_rules[0].tag"),
        (lambda config: config["name_keyword_rules"][0].pop("keywords"), "name_keyword_rules[0].keywords"),
        (
            lambda config: config["name_keyword_rules"][0].__setitem__("keywords", ["fire", 1]),
            "name_keyword_rules[0].keywords[1]",
        ),
        (lambda config: config.__setitem__("limits", []), "limits"),
        (lambda config: config["limits"].pop("max_items"), "limits.max_items"),
        (lambda config: config["limits"].__setitem__("max_items", ""), "limits.max_items"),
        (lambda config: config["limits"].pop("max_config_depth"), "limits.max_config_depth"),
        (lambda config: config["limits"].__setitem__("max_config_depth", ""), "limits.max_config_depth"),
        (lambda config: config.pop("vfx_type_rules"), "vfx_type_rules"),
        (lambda config: config.__setitem__("vfx_type_rules", {}), "vfx_type_rules"),
        (lambda config: config["vfx_type_rules"].pop("ParticleSystem"), "vfx_type_rules.ParticleSystem"),
        (
            lambda config: config["vfx_type_rules"]["ParticleSystem"].pop("category"),
            "vfx_type_rules.ParticleSystem.category",
        ),
        (
            lambda config: config["vfx_type_rules"]["ParticleSystem"].pop("kind"),
            "vfx_type_rules.ParticleSystem.kind",
        ),
        (
            lambda config: config["vfx_type_rules"]["ParticleSystem"].pop("preview_mode"),
            "vfx_type_rules.ParticleSystem.preview_mode",
        ),
        (
            lambda config: config["vfx_type_rules"]["ParticleSystem"].__setitem__("requires_keyword", "yes"),
            "vfx_type_rules.ParticleSystem.requires_keyword",
        ),
    ],
)
def test_load_vfx_config_rejects_missing_or_empty_required_contract_fields(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    mutate(config)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_vfx_config(write_temp_config(tmp_path, config))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("environment_exe", "1BAD"),
        ("environment_exe", "BAD-NAME"),
        ("environment_exe", "BAD NAME"),
        ("environment_exe", "BAD%NAME"),
        ("environment_skill_root", "1BAD"),
        ("environment_skill_root", "BAD-NAME"),
        ("environment_skill_root", "BAD NAME"),
        ("environment_skill_root", "BAD%NAME"),
    ],
)
def test_load_vfx_config_rejects_invalid_launcher_env_var_names(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config["launcher"][key] = value

    with pytest.raises(ValueError, match=f"launcher\\.{key}"):
        load_vfx_config(write_temp_config(tmp_path, config))


@pytest.mark.parametrize(
    ("rule_name", "field"),
    [
        ("Sprite", "preview_mode"),
        ("TrailRenderer", "preview_mode"),
        ("LineRenderer", "kind"),
    ],
)
def test_load_vfx_config_validates_every_declared_vfx_type_rule(
    tmp_path: Path,
    rule_name: str,
    field: str,
) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config["vfx_type_rules"][rule_name].pop(field)

    with pytest.raises(ValueError, match=f"vfx_type_rules\\.{rule_name}\\.{field}"):
        load_vfx_config(write_temp_config(tmp_path, config))


def test_load_vfx_config_requires_adapter_root_types_in_vfx_type_rules(tmp_path: Path) -> None:
    config = copy.deepcopy(load_vfx_config(ROOT))
    config["vfx_type_rules"].pop("TrailRenderer")

    with pytest.raises(ValueError, match="vfx_type_rules\\.TrailRenderer"):
        load_vfx_config(write_temp_config(tmp_path, config))

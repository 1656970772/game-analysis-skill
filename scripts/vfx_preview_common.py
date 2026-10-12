from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CONFIG_RELATIVE_PATH = Path("references") / "vfx-preview-config.json"
PATH_SEPARATOR_RE = re.compile(r"[\\/]")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
WINDOWS_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
BAT_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

REQUIRED_TOP_LEVEL = [
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
    "vfx_type_rules",
]
REQUIRED_PATH_KEYS = [
    "report_dir",
    "overview_name",
    "unitypy_index",
    "unitypy_root",
    "classified_vfx_dir",
    "preview_index_dir",
    "manifest_name",
    "errors_name",
    "reconstructed_dir",
    "preview_cache_dir",
    "unity_export_dir",
]
OPTIONAL_PATH_KEYS = [
    "assetripper_prefab_index",
    "assetripper_export_root",
]
REQUIRED_UNITY_EXPORT_KEYS = [
    "asset_root",
    "prefabs_dir",
    "materials_dir",
    "textures_dir",
    "meshes_dir",
    "animations_dir",
    "scripts_dir",
    "metadata_dir",
    "bootstrap_script_name",
]
REQUIRED_VFX_TYPE_RULE_KEYS = [
    "ParticleSystem",
    "ParticleSystemRenderer",
    "Texture2D",
    "Material",
    "Mesh",
    "AnimationClip",
]
PREVIEW_STATUS = ["pending", "ready", "partial", "resource_only", "failed", "unsupported"]
RECONSTRUCTION_STATUS = ["pending", "full", "partial", "resource_only", "failed"]
UNITY_EXPORT_STATUS = ["pending", "ready", "partial", "skipped", "failed"]


@dataclass(frozen=True)
class VfxPaths:
    report_dir: Path
    overview: Path
    unitypy_index: Path
    unitypy_root: Path
    classified_vfx_dir: Path
    preview_index_dir: Path
    manifest: Path
    errors_jsonl: Path
    reconstructed_dir: Path
    preview_cache_dir: Path
    unity_export_dir: Path
    unity_export_asset_root: Path
    unity_export_prefabs_dir: Path
    unity_export_materials_dir: Path
    unity_export_textures_dir: Path
    unity_export_meshes_dir: Path
    unity_export_animations_dir: Path
    unity_export_scripts_dir: Path
    unity_export_metadata_dir: Path
    unity_export_bootstrap_script: Path
    launcher: Path


def load_vfx_config(skill_root: Path | None = None) -> dict[str, Any]:
    root = skill_root or Path(__file__).resolve().parents[1]
    config_path = root if root.is_file() else root / CONFIG_RELATIVE_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("VFX preview config must be an object")
    missing = [key for key in REQUIRED_TOP_LEVEL if key not in config]
    if missing:
        raise ValueError(f"VFX preview config missing keys: {', '.join(missing)}")
    validate_vfx_config(config)
    return config


def as_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("config-relative path must be a non-empty string")
    if value.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"invalid config-relative path {value!r}: absolute paths are not allowed")
    parts = PATH_SEPARATOR_RE.split(value)
    if any(part == "" for part in parts):
        raise ValueError(f"invalid config-relative path {value!r}: traversal or empty parts are not allowed")
    for part in parts:
        validate_windows_safe_filename(part, f"config-relative path segment {part!r}")
    return Path(*parts)


def resolve_path(root: Path, relative_value: str) -> Path:
    candidate = root / as_relative_path(relative_value)
    if not ensure_inside_root(candidate, root):
        raise ValueError(f"resolved VFX path is outside root: {candidate}")
    return candidate


def ensure_inside_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def vfx_paths(study_root: Path, config: dict[str, Any]) -> VfxPaths:
    root = study_root.resolve(strict=False)
    paths = config["paths"]
    unity_export = config["unity_export"]

    report_dir = resolve_path(root, paths["report_dir"])
    preview_index_dir = resolve_path(root, paths["preview_index_dir"])
    reconstructed_dir = _join_inside(preview_index_dir, paths["reconstructed_dir"], root)
    preview_cache_dir = _join_inside(preview_index_dir, paths["preview_cache_dir"], root)
    unity_export_dir = _join_inside(preview_index_dir, paths["unity_export_dir"], root)
    asset_root = _join_inside(unity_export_dir, unity_export["asset_root"], root)
    scripts_dir = _join_inside(asset_root, unity_export["scripts_dir"], root)

    return VfxPaths(
        report_dir=report_dir,
        overview=_join_inside(report_dir, paths["overview_name"], root),
        unitypy_index=resolve_path(root, paths["unitypy_index"]),
        unitypy_root=resolve_path(root, paths["unitypy_root"]),
        classified_vfx_dir=resolve_path(root, paths["classified_vfx_dir"]),
        preview_index_dir=preview_index_dir,
        manifest=_join_inside(preview_index_dir, paths["manifest_name"], root),
        errors_jsonl=_join_inside(preview_index_dir, paths["errors_name"], root),
        reconstructed_dir=reconstructed_dir,
        preview_cache_dir=preview_cache_dir,
        unity_export_dir=unity_export_dir,
        unity_export_asset_root=asset_root,
        unity_export_prefabs_dir=_join_inside(asset_root, unity_export["prefabs_dir"], root),
        unity_export_materials_dir=_join_inside(asset_root, unity_export["materials_dir"], root),
        unity_export_textures_dir=_join_inside(asset_root, unity_export["textures_dir"], root),
        unity_export_meshes_dir=_join_inside(asset_root, unity_export["meshes_dir"], root),
        unity_export_animations_dir=_join_inside(asset_root, unity_export["animations_dir"], root),
        unity_export_scripts_dir=scripts_dir,
        unity_export_metadata_dir=_join_inside(asset_root, unity_export["metadata_dir"], root),
        unity_export_bootstrap_script=_join_inside(scripts_dir, unity_export["bootstrap_script_name"], root),
        launcher=_join_inside(report_dir, config["launcher"]["bat_name"], root),
    )


def stable_vfx_id(source_file: str, path_id: str, asset_type: str, name: str) -> str:
    normalized_name = name.replace("\\", "/")
    payload = f"{source_file}\n{path_id}\n{asset_type}\n{normalized_name}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def write_json_utf8(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl_utf8(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_vfx_config(config: dict[str, Any]) -> None:
    require_int(config, "schema_version", "schema_version")

    tool = require_object(config, "tool")
    require_non_empty_string(tool, "name", "tool.name")
    require_non_empty_string(tool, "version", "tool.version")
    require_config_path(tool, "directory", "tool.directory")
    require_config_path(tool, "source_template_dir", "tool.source_template_dir")
    require_filename_only(tool, "exe_name", "tool.exe_name")

    launcher = require_object(config, "launcher")
    require_filename_only(launcher, "bat_name", "launcher.bat_name")
    require_bat_env_var_name(launcher, "environment_exe", "launcher.environment_exe")
    require_bat_env_var_name(launcher, "environment_skill_root", "launcher.environment_skill_root")
    require_non_empty_string(launcher, "window_title", "launcher.window_title")

    paths = require_object(config, "paths")
    for key in REQUIRED_PATH_KEYS:
        require_config_path(paths, key, f"paths.{key}")
    for key in OPTIONAL_PATH_KEYS:
        if key in paths:
            require_config_path(paths, key, f"paths.{key}")

    unity_export = require_object(config, "unity_export")
    for key in REQUIRED_UNITY_EXPORT_KEYS:
        require_config_path(unity_export, key, f"unity_export.{key}")

    manifest = require_object(config, "manifest")
    require_int(manifest, "schema_version", "manifest.schema_version")
    require_exact_string_list(manifest, "preview_status", "manifest.preview_status", PREVIEW_STATUS)
    require_contains_string_list(
        manifest,
        "reconstruction_status",
        "manifest.reconstruction_status",
        RECONSTRUCTION_STATUS,
    )
    require_contains_string_list(
        manifest,
        "unity_export_status",
        "manifest.unity_export_status",
        UNITY_EXPORT_STATUS,
    )
    require_int(manifest, "max_error_records", "manifest.max_error_records")

    state_machine = require_object(config, "state_machine")
    initial = require_object(state_machine, "manifest_initial", "state_machine.manifest_initial")
    require_non_empty_object(state_machine, "preview_transitions", "state_machine.preview_transitions")
    require_non_empty_object(
        state_machine,
        "reconstruction_transitions",
        "state_machine.reconstruction_transitions",
    )
    require_non_empty_object(
        state_machine,
        "unity_export_transitions",
        "state_machine.unity_export_transitions",
    )
    expected_initial = {
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }
    if initial != expected_initial:
        raise ValueError("VFX preview config field state_machine.manifest_initial must use pending statuses")

    adapter = require_object(config, "adapter")
    if require_string(adapter, "default_engine", "adapter.default_engine") != "unity":
        raise ValueError("VFX preview config field adapter.default_engine must be unity")
    unity = require_object(adapter, "unity", "adapter.unity")
    root_types = require_contains_string_list(
        unity,
        "root_types",
        "adapter.unity.root_types",
        ["ParticleSystem", "TrailRenderer", "LineRenderer"],
    )
    require_contains_string_list(
        unity,
        "supported_modules",
        "adapter.unity.supported_modules",
        [
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
        ],
    )
    aliases = require_object(unity, "module_aliases", "adapter.unity.module_aliases")
    if aliases.get("InitialModule") != "main":
        raise ValueError("VFX preview config field adapter.unity.module_aliases.InitialModule must be main")
    if aliases.get("EmissionModule") != "emission":
        raise ValueError("VFX preview config field adapter.unity.module_aliases.EmissionModule must be emission")
    defaults = require_object(unity, "defaults", "adapter.unity.defaults")
    main = require_object(defaults, "main", "adapter.unity.defaults.main")
    start_speed = require_object(main, "startSpeed", "adapter.unity.defaults.main.startSpeed")
    if start_speed.get("constant") != 1.0:
        raise ValueError("VFX preview config field adapter.unity.defaults.main.startSpeed.constant must be 1.0")
    require_object(defaults, "emission", "adapter.unity.defaults.emission")

    rules = require_non_empty_list(config, "name_keyword_rules", "name_keyword_rules")
    for index, rule in enumerate(rules):
        rule_path = f"name_keyword_rules[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"VFX preview config field {rule_path} must be an object")
        require_non_empty_string(rule, "tag", f"{rule_path}.tag")
        require_string_list(rule, "keywords", f"{rule_path}.keywords")

    limits = require_object(config, "limits")
    require_int(limits, "max_items", "limits.max_items")
    require_int(limits, "max_config_depth", "limits.max_config_depth")

    vfx_type_rules = require_non_empty_object(config, "vfx_type_rules", "vfx_type_rules")
    for key in [*REQUIRED_VFX_TYPE_RULE_KEYS, *root_types]:
        require_object(vfx_type_rules, key, f"vfx_type_rules.{key}")
    for key, value in vfx_type_rules.items():
        if not isinstance(key, str) or not key:
            raise ValueError("VFX preview config field vfx_type_rules keys must be non-empty strings")
        if not isinstance(value, dict):
            raise ValueError(f"VFX preview config field vfx_type_rules.{key} must be an object")
        rule = value
        require_config_path(rule, "category", f"vfx_type_rules.{key}.category")
        require_non_empty_string(rule, "kind", f"vfx_type_rules.{key}.kind")
        require_non_empty_string(rule, "preview_mode", f"vfx_type_rules.{key}.preview_mode")
        if "requires_keyword" in rule and type(rule["requires_keyword"]) is not bool:
            raise ValueError(f"VFX preview config field vfx_type_rules.{key}.requires_keyword must be a boolean")


def _join_inside(base: Path, relative_value: str, root: Path) -> Path:
    candidate = base / as_relative_path(relative_value)
    if not ensure_inside_root(candidate, root):
        raise ValueError(f"resolved VFX path is outside root: {candidate}")
    return candidate


def require_field(container: dict[str, Any], key: str, field_path: str) -> Any:
    if key not in container:
        raise ValueError(f"VFX preview config missing required field: {field_path}")
    return container[key]


def require_object(container: dict[str, Any], key: str, field_path: str | None = None) -> dict[str, Any]:
    path = field_path or key
    value = require_field(container, key, path)
    if not isinstance(value, dict):
        raise ValueError(f"VFX preview config field {path} must be an object")
    return value


def require_string(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_field(container, key, field_path)
    if not isinstance(value, str):
        raise ValueError(f"VFX preview config field {field_path} must be a string")
    return value


def require_non_empty_string(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_string(container, key, field_path)
    if value == "":
        raise ValueError(f"VFX preview config field {field_path} must be a non-empty string")
    return value


def require_int(container: dict[str, Any], key: str, field_path: str) -> int:
    value = require_field(container, key, field_path)
    if type(value) is not int:
        raise ValueError(f"VFX preview config field {field_path} must be an integer")
    return value


def require_config_path(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_string(container, key, field_path)
    try:
        as_relative_path(value)
    except ValueError as exc:
        raise ValueError(f"VFX preview config field {field_path} must be a config-relative path: {exc}") from exc
    return value


def require_filename_only(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_string(container, key, field_path)
    try:
        validate_windows_safe_filename(value, field_path)
    except ValueError as exc:
        raise ValueError(f"VFX preview config field {field_path} must be a filename-only value: {exc}") from exc
    if "/" in value or "\\" in value:
        raise ValueError(f"VFX preview config field {field_path} must be a filename-only value")
    return value


def require_bat_env_var_name(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_non_empty_string(container, key, field_path)
    if not BAT_ENV_VAR_NAME_RE.match(value):
        raise ValueError(f"VFX preview config field {field_path} must be a BAT environment variable name")
    return value


def require_non_empty_object(container: dict[str, Any], key: str, field_path: str) -> dict[str, Any]:
    value = require_object(container, key, field_path)
    if not value:
        raise ValueError(f"VFX preview config field {field_path} must be a non-empty object")
    return value


def require_non_empty_list(container: dict[str, Any], key: str, field_path: str) -> list[Any]:
    value = require_field(container, key, field_path)
    if not isinstance(value, list):
        raise ValueError(f"VFX preview config field {field_path} must be a list")
    if not value:
        raise ValueError(f"VFX preview config field {field_path} must be a non-empty list")
    return value


def require_exact_string_list(
    container: dict[str, Any],
    key: str,
    field_path: str,
    expected: list[str],
) -> list[str]:
    value = require_string_list(container, key, field_path)
    if value != expected:
        raise ValueError(f"VFX preview config field {field_path} must be {expected!r}")
    return value


def require_contains_string_list(
    container: dict[str, Any],
    key: str,
    field_path: str,
    expected_items: list[str],
) -> list[str]:
    value = require_string_list(container, key, field_path)
    missing = [item for item in expected_items if item not in value]
    if missing:
        raise ValueError(f"VFX preview config field {field_path} missing values: {', '.join(missing)}")
    return value


def require_string_list(container: dict[str, Any], key: str, field_path: str) -> list[str]:
    value = require_field(container, key, field_path)
    if not isinstance(value, list):
        raise ValueError(f"VFX preview config field {field_path} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"VFX preview config field {field_path}[{index}] must be a string")
    return value


def validate_windows_safe_filename(value: str, field_path: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_path} must be a non-empty Windows-safe filename")
    if value in {".", ".."}:
        raise ValueError(f"{field_path} must not be . or ..")
    if value.endswith((" ", ".")):
        raise ValueError(f"{field_path} must not end with a space or dot")
    if WINDOWS_INVALID_FILENAME_RE.search(value):
        raise ValueError(f"{field_path} contains characters Windows does not allow in filenames")
    reserved_base = value.split(".", 1)[0].upper()
    if reserved_base in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_path} uses reserved Windows device name {reserved_base}")

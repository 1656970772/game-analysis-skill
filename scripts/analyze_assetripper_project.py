from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None


CLASS_NAMES = {
    1: "GameObject",
    4: "Transform",
    20: "Camera",
    23: "MeshRenderer",
    33: "MeshFilter",
    50: "Rigidbody2D",
    54: "Rigidbody",
    58: "CircleCollider2D",
    60: "PolygonCollider2D",
    61: "BoxCollider2D",
    95: "Animator",
    104: "RenderSettings",
    108: "Light",
    114: "MonoBehaviour",
    157: "LightmapSettings",
    198: "ParticleSystem",
    199: "ParticleSystemRenderer",
    212: "SpriteRenderer",
    222: "CanvasRenderer",
    223: "Canvas",
    224: "RectTransform",
    225: "CanvasGroup",
}

MARKER_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)", re.MULTILINE)
NAME_RE = re.compile(r"^\s*m_Name:\s*(.*)$", re.MULTILINE)
GAMEOBJECT_RE = re.compile(r"m_GameObject:\s*\{fileID:\s*(-?\d+)")
FATHER_RE = re.compile(r"m_Father:\s*\{fileID:\s*(-?\d+)")
COMPONENT_RE = re.compile(r"component:\s*\{fileID:\s*(-?\d+)")
PROJECT_VERSION_LINE_RE = re.compile(r"^\s*m_EditorVersion:\s*(.*?)\s*$", re.MULTILINE)
UNITY_EDITOR_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+[abcfpx]\d+(?:[A-Za-z0-9._+-]*)?$")
UNITY_SCALAR_RE = re.compile(r"^(\s*)(?:-\s+)?([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "references" / "assetripper-project-analysis.json"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_strict_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is not allowed: {value}")

    return json.loads(read_text(path), parse_constant=reject_constant)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def source_descriptor(path: Path, project_root: Path | None = None) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "absolute_path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if project_root is not None:
        try:
            descriptor["project_relative_path"] = rel(path.resolve(), project_root.resolve())
        except ValueError:
            descriptor["project_relative_path"] = None
    return descriptor


def read_unity_editor_version(project_root: Path) -> tuple[str, str]:
    version_path = project_root / "ProjectSettings" / "ProjectVersion.txt"
    if not version_path.is_file():
        return "unknown", "missing"

    match = PROJECT_VERSION_LINE_RE.search(read_text(version_path))
    version = clean_name(match.group(1) if match else "")
    if not version or not UNITY_EDITOR_VERSION_RE.fullmatch(version):
        return "unknown", "unrecognized"
    return version, "detected"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def clean_name(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace("\x00", "").strip()


def parse_scalar_value(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value == "null":
        return None
    if value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", value):
        number = float(value)
        return number if math.isfinite(number) else value
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return clean_name(value)
    return clean_name(value)


def scalar_entry(key: str, raw: str, line_no: int, line: str) -> dict[str, Any]:
    return {
        "key": key,
        "value": parse_scalar_value(raw),
        "raw_value": raw.strip(),
        "anchor": {"line": line_no, "excerpt": line.strip()},
    }


def parse_build_settings(path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse every Build Settings record using ``- enabled`` as a hard boundary."""
    lines = read_text(path).splitlines()
    build_config = config["build_settings"]
    start_key = re.escape(str(build_config["record_start_key"]))
    path_key = re.escape(str(build_config["path_key"]))
    guid_key = re.escape(str(build_config["guid_key"]))
    start_re = re.compile(rf"\s*-\s*{start_key}:\s*(0|1|true|false)\s*$", re.IGNORECASE)
    path_re = re.compile(rf"\s*{path_key}:\s*(.*?)\s*$")
    guid_re = re.compile(rf"\s*{guid_key}:\s*([0-9a-fA-F]*)\s*$")
    starts = [(line_no, match) for line_no, line in enumerate(lines, 1) if (match := start_re.fullmatch(line))]
    entries: list[dict[str, Any]] = []
    for position, (line_no, match) in enumerate(starts):
        next_start = starts[position + 1][0] if position + 1 < len(starts) else len(lines) + 1
        scene_path = ""
        guid = ""
        path_line = None
        guid_line = None
        for next_no in range(line_no + 1, next_start):
            candidate = lines[next_no - 1]
            if path_match := path_re.fullmatch(candidate):
                scene_path = clean_name(path_match.group(1))
                path_line = next_no
            if guid_match := guid_re.fullmatch(candidate):
                guid = guid_match.group(1).lower()
                guid_line = next_no
        if not scene_path:
            raise ValueError(f"Build Settings entry at line {line_no} has no path before the next '- enabled' record")
        enabled_raw = match.group(1).casefold()
        entries.append(
            {
                "order": position,
                "enabled": enabled_raw in {"1", "true"},
                "path": scene_path,
                "guid": guid,
                "anchors": {
                    "enabled": {"line": line_no, "excerpt": lines[line_no - 1].strip()},
                    "path": {"line": path_line, "excerpt": lines[path_line - 1].strip()},
                    "guid": (
                        {"line": guid_line, "excerpt": lines[guid_line - 1].strip()}
                        if guid_line is not None
                        else None
                    ),
                },
            }
        )
    return entries


def reconcile_build_settings(entries: list[dict[str, Any]], scene_rows: list[dict[str, Any]]) -> dict[str, Any]:
    build_paths = [entry["path"] for entry in entries]
    index_paths = [str(row["relative_path"]) for row in scene_rows]
    return {
        "entry_count": len(entries),
        "enabled_count": sum(entry["enabled"] for entry in entries),
        "disabled_count": sum(not entry["enabled"] for entry in entries),
        "unique_path_count": len(set(build_paths)),
        "scene_index_count": len(index_paths),
        "sets_match": set(build_paths) == set(index_paths),
        "build_only_paths": sorted(set(build_paths) - set(index_paths)),
        "index_only_paths": sorted(set(index_paths) - set(build_paths)),
        "duplicate_build_paths": sorted(path for path, count in Counter(build_paths).items() if count > 1),
    }


def parse_unity_scalar_file(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        match = UNITY_SCALAR_RE.match(line)
        if match:
            entries.append(scalar_entry(match.group(2), match.group(3), line_no, line))
    return entries


def parse_quality_settings(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    lines = read_text(path).splitlines()
    render_config = config["render_settings"]
    levels_key = str(render_config["quality_levels_section_key"])
    record_key = str(render_config["quality_level_record_start_key"])
    defaults_key = str(render_config["quality_platform_defaults_section_key"])
    section_re = re.compile(rf"^(\s*){re.escape(levels_key)}:\s*$")
    defaults_re = re.compile(rf"^(\s*){re.escape(defaults_key)}:\s*$")
    record_re = re.compile(rf"^(\s*)-\s*{re.escape(record_key)}:\s*(.*?)\s*$")
    levels: list[dict[str, Any]] = []
    platform_defaults: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    mode = "outside"
    section_indent = -1
    for line_no, line in enumerate(lines, 1):
        if match := section_re.match(line):
            mode = "levels"
            section_indent = len(match.group(1))
            continue
        if match := defaults_re.match(line):
            if current is not None:
                levels.append(current)
                current = None
            mode = "defaults"
            section_indent = len(match.group(1))
            continue
        indent = len(line) - len(line.lstrip())
        record_match = record_re.match(line) if mode == "levels" else None
        if record_match:
            if current is not None:
                levels.append(current)
            current = {
                "order": len(levels),
                "settings": [scalar_entry(record_key, record_match.group(2), line_no, line)],
            }
            continue
        if mode in {"levels", "defaults"} and line.strip() and indent <= section_indent:
            if current is not None:
                levels.append(current)
                current = None
            mode = "outside"
        if mode == "levels":
            scalar_match = UNITY_SCALAR_RE.match(line)
            if current is not None and scalar_match:
                current["settings"].append(scalar_entry(scalar_match.group(2), scalar_match.group(3), line_no, line))
        elif mode == "defaults":
            scalar_match = UNITY_SCALAR_RE.match(line)
            if scalar_match:
                platform_defaults.append(scalar_entry(scalar_match.group(2), scalar_match.group(3), line_no, line))
    if current is not None:
        levels.append(current)
    for level in levels:
        name_entry = next((item for item in level["settings"] if item["key"] == "name"), None)
        level["name"] = name_entry["value"] if name_entry else None
    for item in platform_defaults:
        index = item["value"] if isinstance(item["value"], int) else None
        item["level_name"] = levels[index]["name"] if index is not None and 0 <= index < len(levels) else None
    return {
        "level_count": len(levels),
        "levels": levels,
        "platform_defaults": platform_defaults,
        "all_scalar_settings": parse_unity_scalar_file(path),
    }


def find_setting(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next((entry for entry in entries if entry["key"] == key), None)


def analyze_render_settings(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    render_config = config["render_settings"]
    paths = {
        "graphics": project_root / Path(str(render_config["graphics_file"])),
        "quality": project_root / Path(str(render_config["quality_file"])),
        "player": project_root / Path(str(render_config["player_file"])),
        "packages": project_root / Path(str(render_config["packages_file"])),
    }
    graphics_entries = parse_unity_scalar_file(paths["graphics"]) if paths["graphics"].is_file() else []
    player_entries = parse_unity_scalar_file(paths["player"]) if paths["player"].is_file() else []
    quality = parse_quality_settings(paths["quality"], config) if paths["quality"].is_file() else {
        "level_count": 0,
        "levels": [],
        "platform_defaults": [],
        "all_scalar_settings": [],
    }
    dependencies: list[dict[str, Any]] = []
    if paths["packages"].is_file():
        package_document = load_strict_json(paths["packages"])
        package_lines = read_text(paths["packages"]).splitlines()
        raw_dependencies = package_document.get("dependencies", {}) if isinstance(package_document, dict) else {}
        for name, version in sorted(raw_dependencies.items()):
            package_re = re.compile(rf'^\s*"{re.escape(str(name))}"\s*:\s*')
            anchor = next(
                ({"line": line_no, "excerpt": line.strip()} for line_no, line in enumerate(package_lines, 1) if package_re.search(line)),
                None,
            )
            dependencies.append({"name": str(name), "version": version, "anchor": anchor})
    custom_key = str(render_config["custom_render_pipeline_key"])
    custom_entry = find_setting(graphics_entries, custom_key)
    custom_file_id = None
    if custom_entry:
        match = re.search(r"fileID:\s*(-?\d+)", custom_entry["raw_value"])
        custom_file_id = int(match.group(1)) if match else None
    prefixes = tuple(str(prefix) for prefix in render_config.get("srp_package_prefixes", []))
    srp_packages = [item for item in dependencies if item["name"].startswith(prefixes)] if prefixes else []
    if custom_file_id == 0 and not srp_packages:
        pipeline_judgment = "Built-in Render Pipeline"
    else:
        pipeline_judgment = "requires_review"
    return {
        "graphics": {"status": "parsed" if paths["graphics"].is_file() else "missing", "settings": graphics_entries},
        "quality": {"status": "parsed" if paths["quality"].is_file() else "missing", **quality},
        "player": {"status": "parsed" if paths["player"].is_file() else "missing", "settings": player_entries},
        "packages": {"status": "parsed" if paths["packages"].is_file() else "missing", "dependencies": dependencies},
        "pipeline": {
            "custom_render_pipeline_file_id": custom_file_id,
            "srp_packages": srp_packages,
            "judgment": pipeline_judgment,
            "boundary": "项目设置与包依赖只能支持静态管线判断，不能证明逐相机覆盖、Shader variant 或运行时切换。",
        },
        "source_paths": {name: str(path.resolve()) for name, path in paths.items()},
    }


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    current = document
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(key)]
        elif isinstance(current, dict):
            current = current[key]
        else:
            raise KeyError(pointer)
    return current


def first_pointer(document: Any, pointers: list[str]) -> tuple[str | None, Any]:
    for pointer in pointers:
        try:
            return pointer, resolve_json_pointer(document, pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None, None


def first_field(item: dict[str, Any], fields: list[str]) -> Any:
    return next((item[field] for field in fields if field in item), None)


def analyze_layout_mismatch(path: Path | None, config: dict[str, Any]) -> dict[str, Any]:
    if path is None:
        return {
            "status": "not_provided",
            "total_count": None,
            "affected_components": [],
            "safe_usage": [],
            "forbidden_inference": [],
            "source": None,
        }
    document = load_strict_json(path)
    mismatch_config = config["layout_mismatch"]
    total_pointer, total = first_pointer(document, list(mismatch_config["total_json_pointers"]))
    entries_pointer, raw_entries = first_pointer(document, list(mismatch_config["entries_json_pointers"]))
    affected: list[dict[str, Any]] = []
    if isinstance(raw_entries, list):
        for raw_item in raw_entries:
            if not isinstance(raw_item, dict):
                continue
            name = first_field(raw_item, list(mismatch_config["name_fields"]))
            count = first_field(raw_item, list(mismatch_config["count_fields"]))
            guid = first_field(raw_item, list(mismatch_config["guid_fields"]))
            affected.append({"name": str(name) if name is not None else None, "count": count, "guid": guid})
    affected.sort(key=lambda item: (item["name"] or "", str(item["guid"] or ""), str(item["count"] or "")))
    names = sorted({item["name"] for item in affected if item["name"]})
    safe_usage = list(mismatch_config["safe_usage"]) if total or affected else []
    forbidden = list(mismatch_config["forbidden_inference"]) if total or affected else []
    if names:
        forbidden.append("受影响组件（由输入动态派生）：" + "、".join(names) + "。")
    return {
        "status": "provided" if total_pointer or entries_pointer else "provided_unrecognized",
        "total_count": total if isinstance(total, int) else None,
        "affected_components": affected,
        "safe_usage": safe_usage,
        "forbidden_inference": forbidden,
        "source": {
            **source_descriptor(path),
            "total_json_pointer": total_pointer,
            "entries_json_pointer": entries_pointer,
        },
    }


def iter_render_anchors(render_settings: dict[str, Any]):
    for group in ("graphics", "player"):
        for entry in render_settings[group]["settings"]:
            yield render_settings["source_paths"][group], entry["anchor"]
    for entry in render_settings["quality"]["all_scalar_settings"]:
        yield render_settings["source_paths"]["quality"], entry["anchor"]
    for entry in render_settings["packages"]["dependencies"]:
        if entry["anchor"]:
            yield render_settings["source_paths"]["packages"], entry["anchor"]


def verify_sources_and_anchors(
    sources: dict[str, dict[str, Any]],
    build_settings: dict[str, Any],
    render_settings: dict[str, Any],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    checked_anchor_count = 0
    for label, descriptor in sources.items():
        path = Path(descriptor["absolute_path"])
        if not path.is_file():
            errors.append({"kind": "source_missing", "label": label, "path": str(path)})
            continue
        if path.stat().st_size != descriptor["size_bytes"] or sha256_file(path) != descriptor["sha256"]:
            errors.append({"kind": "source_identity_changed", "label": label, "path": str(path)})
    build_source = sources.get("editor_build_settings")
    if build_source:
        build_lines = read_text(Path(build_source["absolute_path"])).splitlines()
        for entry in build_settings["entries"]:
            for label, anchor in entry["anchors"].items():
                if anchor is None:
                    continue
                checked_anchor_count += 1
                line_no = anchor["line"]
                if line_no > len(build_lines) or build_lines[line_no - 1].strip() != anchor["excerpt"]:
                    errors.append({"kind": "build_anchor", "order": entry["order"], "anchor": label})
    cached_lines: dict[str, list[str]] = {}
    for path_string, anchor in iter_render_anchors(render_settings):
        checked_anchor_count += 1
        if path_string not in cached_lines:
            cached_lines[path_string] = read_text(Path(path_string)).splitlines()
        lines = cached_lines[path_string]
        line_no = anchor["line"]
        if line_no > len(lines) or lines[line_no - 1].strip() != anchor["excerpt"]:
            errors.append({"kind": "render_anchor", "path": path_string, "line": line_no})
    return {
        "checked_source_count": len(sources),
        "checked_anchor_count": checked_anchor_count,
        "error_count": len(errors),
        "errors": errors,
        "all_valid": not errors,
    }


def iter_yaml_objects(text: str):
    matches = list(MARKER_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield int(match.group(1)), int(match.group(2)), text[match.start() : end]


def scene_group(path: Path, project_root: Path) -> str:
    parts = path.relative_to(project_root).parts
    directory_parts = parts[:-1]
    lower_parts = [part.casefold() for part in directory_parts]
    if "scenes" in lower_parts:
        scene_index = lower_parts.index("scenes")
        nested_parts = directory_parts[scene_index + 1 :]
        if nested_parts and nested_parts[0].casefold() == "levels":
            return nested_parts[1] if len(nested_parts) > 1 else "LevelsRoot"
        return nested_parts[0] if nested_parts else "ScenesRoot"
    if len(directory_parts) >= 2:
        return directory_parts[-1]
    if directory_parts and directory_parts[0].casefold() == "assets":
        return "AssetsRoot"
    return directory_parts[0] if directory_parts else "ProjectRoot"


def guess_category(path: Path) -> str:
    ext = path.suffix.lower()
    rel_lower = path.as_posix().lower()
    if ext == ".unity":
        return "scene"
    if ext == ".prefab":
        return "prefab"
    if ext in {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".psd"}:
        if "/ui/" in rel_lower or "icon" in rel_lower:
            return "image_ui_icon"
        if "character" in rel_lower or "/characters/" in rel_lower:
            return "image_character"
        if "effect" in rel_lower or "vfx" in rel_lower:
            return "image_effect"
        if "scene" in rel_lower or "level" in rel_lower or "map" in rel_lower:
            return "image_scene"
        return "image"
    if ext in {".anim", ".controller", ".overridecontroller", ".playable"}:
        return "animation"
    if ext in {".mat", ".shader", ".rendertexture", ".physicmaterial", ".physicsmaterial2d"}:
        return "material_shader"
    if ext in {".ogg", ".wav", ".mp3", ".bank"}:
        return "audio"
    if ext in {".ttf", ".otf"}:
        return "font"
    if ext in {".cs", ".dll"}:
        return "code_assembly"
    if ext in {".asset", ".json", ".txt", ".xml"}:
        return "data_config"
    if ext in {".bundle", ".resources", ".ress"}:
        return "streaming_package"
    if ext == ".meta":
        return "unity_meta"
    return "other"


def analyze_yaml_asset(path: Path, project_root: Path) -> dict:
    text = read_text(path)
    class_counts: Counter[int] = Counter()
    gameobject_names: dict[int, str] = {}
    transform_to_gameobject: dict[int, int] = {}
    transform_father: dict[int, int] = {}
    component_refs: dict[int, list[int]] = {}

    for class_id, file_id, block in iter_yaml_objects(text):
        class_counts[class_id] += 1
        if class_id == 1:
            name_match = NAME_RE.search(block)
            gameobject_names[file_id] = clean_name(name_match.group(1) if name_match else "")
            component_refs[file_id] = [int(item) for item in COMPONENT_RE.findall(block)]
        elif class_id in {4, 224}:
            go_match = GAMEOBJECT_RE.search(block)
            father_match = FATHER_RE.search(block)
            if go_match:
                transform_to_gameobject[file_id] = int(go_match.group(1))
            if father_match:
                transform_father[file_id] = int(father_match.group(1))

    roots = []
    for transform_id, go_id in transform_to_gameobject.items():
        father = transform_father.get(transform_id, 0)
        if father == 0 and go_id in gameobject_names:
            roots.append(gameobject_names[go_id])

    top_names = [name for name, _ in Counter(n for n in roots if n).most_common(16)]
    sample_names = [name for name, _ in Counter(n for n in gameobject_names.values() if n).most_common(24)]
    keywords = {
        "tilemap": text.lower().count("tilemap"),
        "grid": len(re.findall(r"\bGrid\b", text)),
        "sorting_layer": text.count("m_SortingLayer"),
        "sprite_guid_ref": text.count("m_Sprite: {fileID:"),
        "cinemachine": text.lower().count("cinemachine"),
        "timeline": text.lower().count("timeline"),
    }

    collider_count = sum(class_counts.get(cid, 0) for cid in (58, 60, 61))
    tags = []
    if class_counts.get(212, 0) >= 20:
        tags.append("SpriteRenderer分层")
    if class_counts.get(198, 0) > 0:
        tags.append("粒子特效")
    if class_counts.get(223, 0) > 0 or class_counts.get(224, 0) >= 20:
        tags.append("UI/RectTransform")
    if class_counts.get(95, 0) > 0:
        tags.append("Animator")
    if collider_count > 0:
        tags.append("2D碰撞")
    if keywords["tilemap"] > 0:
        tags.append("Tilemap线索")
    if keywords["cinemachine"] > 0:
        tags.append("Cinemachine线索")
    if not tags:
        tags.append("轻量场景片段")

    return {
        "relative_path": rel(path, project_root),
        "group": scene_group(path, project_root) if path.suffix.lower() == ".unity" else prefab_group(path, project_root),
        "file_size": path.stat().st_size,
        "yaml_object_count": sum(class_counts.values()),
        "class_counts": dict(class_counts),
        "gameobject_count": class_counts.get(1, 0),
        "transform_count": class_counts.get(4, 0),
        "recttransform_count": class_counts.get(224, 0),
        "spriterenderer_count": class_counts.get(212, 0),
        "particle_count": class_counts.get(198, 0),
        "animator_count": class_counts.get(95, 0),
        "canvas_count": class_counts.get(223, 0),
        "camera_count": class_counts.get(20, 0),
        "collider2d_count": collider_count,
        "monobehaviour_count": class_counts.get(114, 0),
        "tilemap_keyword_count": keywords["tilemap"],
        "grid_keyword_count": keywords["grid"],
        "sprite_guid_ref_count": keywords["sprite_guid_ref"],
        "cinemachine_keyword_count": keywords["cinemachine"],
        "timeline_keyword_count": keywords["timeline"],
        "root_names": " | ".join(top_names),
        "sample_names": " | ".join(sample_names),
        "scene_building_tags": " | ".join(tags),
    }


def prefab_group(path: Path, project_root: Path) -> str:
    parts = path.relative_to(project_root).parts
    if "Prefabs" in parts:
        idx = parts.index("Prefabs")
        return parts[idx + 1] if idx + 1 < len(parts) else "Prefabs"
    if "Resources" in parts:
        idx = parts.index("Resources")
        return parts[idx + 1] if idx + 1 < len(parts) else "Resources"
    return parts[0] if parts else "Unknown"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_file_inventory(project_root: Path) -> tuple[list[dict], Counter[str], Counter[str]]:
    rows = []
    ext_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower() or "(no_ext)"
        category = guess_category(path.relative_to(project_root))
        ext_counts[ext] += 1
        category_counts[category] += 1
        rows.append(
            {
                "relative_path": rel(path, project_root),
                "extension": ext,
                "category": category,
                "size_bytes": path.stat().st_size,
            }
        )
    rows.sort(key=lambda row: (row["relative_path"].casefold(), row["relative_path"]))
    return rows, ext_counts, category_counts


def collect_image_index(project_root: Path) -> list[dict]:
    if Image is None:
        return []
    rows = []
    for path in project_root.rglob("*.png"):
        try:
            with Image.open(path) as img:
                mode = img.mode
                has_alpha = mode in {"RGBA", "LA"} or ("transparency" in img.info)
                rows.append(
                    {
                        "relative_path": rel(path, project_root),
                        "width": img.width,
                        "height": img.height,
                        "mode": mode,
                        "has_alpha": str(has_alpha).lower(),
                        "category": guess_category(path.relative_to(project_root)),
                        "size_bytes": path.stat().st_size,
                    }
                )
        except Exception as exc:
            rows.append(
                {
                    "relative_path": rel(path, project_root),
                    "width": "",
                    "height": "",
                    "mode": "",
                    "has_alpha": "",
                    "category": guess_category(path.relative_to(project_root)),
                    "size_bytes": path.stat().st_size,
                    "error": str(exc),
                }
            )
    rows.sort(
        key=lambda row: (
            str(row.get("category")),
            str(row.get("relative_path")).casefold(),
            str(row.get("relative_path")),
        )
    )
    return rows


def make_markdown_table(rows: list[dict], columns: list[str], limit: int = 12) -> str:
    if not rows:
        return "暂无数据。\n"
    out = []
    out.append("| " + " | ".join(columns) + " |")
    out.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for row in rows[:limit]:
        values = []
        for col in columns:
            value = str(row.get(col, ""))
            if len(value) > 90:
                value = value[:87] + "..."
            values.append(value.replace("|", "/"))
        out.append("| " + " | ".join(values) + " |")
    return "\n".join(out) + "\n"


def learning_entry_counts(rows: list[dict], asset_kind: str) -> Counter[str]:
    entries: Counter[str] = Counter()
    for row in rows:
        parts = PurePosixPath(str(row["relative_path"])).parts
        directory_parts = parts[:-1]
        lower_parts = [part.casefold() for part in directory_parts]
        entry_parts: tuple[str, ...]

        if asset_kind == "scene" and "scenes" in lower_parts:
            marker_index = lower_parts.index("scenes")
            end_index = marker_index
            if marker_index + 1 < len(lower_parts) and lower_parts[marker_index + 1] == "levels":
                end_index += 1
            entry_parts = directory_parts[: end_index + 1]
        elif asset_kind == "prefab":
            marker_index = next(
                (index for index, part in enumerate(lower_parts) if part in {"prefab", "prefabs"}),
                None,
            )
            if marker_index is not None:
                entry_parts = directory_parts[: marker_index + 1]
            elif len(directory_parts) >= 2 and lower_parts[0] == "assets":
                entry_parts = directory_parts[:2]
            else:
                entry_parts = directory_parts
        else:
            entry_parts = directory_parts

        entries["/".join(entry_parts) if entry_parts else "."] += 1
    return entries


def format_learning_entries(entries: Counter[str], limit: int = 12) -> str:
    if not entries:
        return "未扫描到对应资源。"
    return "；".join(f"`{path}`（{count} 个）" for path, count in entries.most_common(limit))


def write_report(
    output_path: Path,
    project_root: Path,
    project_title: str,
    file_rows: list[dict],
    ext_counts: Counter[str],
    category_counts: Counter[str],
    scene_rows: list[dict],
    prefab_rows: list[dict],
    image_rows: list[dict],
    unity_editor_version: str,
    unity_editor_version_status: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    scene_groups = Counter(row["group"] for row in scene_rows)
    prefab_groups = Counter(row["group"] for row in prefab_rows)
    total_size = sum(int(row["size_bytes"]) for row in file_rows)
    top_sprite_scenes = sorted(scene_rows, key=lambda row: int(row["spriterenderer_count"]), reverse=True)
    top_particle_scenes = sorted(scene_rows, key=lambda row: int(row["particle_count"]), reverse=True)
    top_canvas_scenes = sorted(scene_rows, key=lambda row: int(row["canvas_count"]), reverse=True)
    top_go_scenes = sorted(scene_rows, key=lambda row: int(row["gameobject_count"]), reverse=True)
    top_prefabs = sorted(prefab_rows, key=lambda row: int(row["gameobject_count"]), reverse=True)
    large_images = sorted(
        image_rows,
        key=lambda row: (int(row["width"] or 0) * int(row["height"] or 0), int(row["size_bytes"] or 0)),
        reverse=True,
    )

    ext_table_rows = [
        {"扩展名": ext, "数量": count}
        for ext, count in ext_counts.most_common(18)
    ]
    cat_table_rows = [
        {"类别": cat, "数量": count}
        for cat, count in category_counts.most_common(18)
    ]
    scene_group_rows = [
        {"场景分组": group, "数量": count}
        for group, count in scene_groups.most_common()
    ]
    prefab_group_rows = [
        {"Prefab分组": group, "数量": count}
        for group, count in prefab_groups.most_common(18)
    ]
    scene_entries = learning_entry_counts(scene_rows, "scene")
    prefab_entries = learning_entry_counts(prefab_rows, "prefab")
    primary_scene_entry = scene_entries.most_common(1)[0][0] if scene_entries else "未扫描到场景目录"
    primary_prefab_entry = prefab_entries.most_common(1)[0][0] if prefab_entries else "未扫描到 Prefab 目录"
    if unity_editor_version_status == "detected":
        editor_version_validation = (
            f"需要在 Unity {unity_editor_version} 或兼容版本中打开项目后，进一步确认排序层、物理层、"
            "Addressables 分组和运行时加载逻辑。"
        )
    else:
        status_label = {
            "missing": "ProjectSettings/ProjectVersion.txt 缺失",
            "unrecognized": "ProjectSettings/ProjectVersion.txt 未识别",
        }.get(unity_editor_version_status, "版本状态未识别")
        editor_version_validation = (
            f"工程 Unity 编辑器版本为 `{unity_editor_version}`（{status_label}）；需先确认原始编辑器版本，"
            "再打开项目复核排序层、物理层、Addressables 分组和运行时加载逻辑。"
        )

    evidence_section = ""
    if evidence is not None:
        build = evidence["build_settings"]
        reconciliation = build["reconciliation"]
        render = evidence["render_settings"]
        quality = render["quality"]
        layout = evidence["layout_mismatch"]
        first_entries = build["entries"][:3]
        first_paths = " → ".join(f"`{entry['path']}`" for entry in first_entries) or "未扫描到条目"
        platform_defaults = "；".join(
            f"{item['key']}={item['value']}（{item['level_name'] or '未知档名'}）"
            for item in quality["platform_defaults"]
        ) or "未扫描到平台默认档"
        evidence_section = f"""
## Build Settings 与渲染设置证据

- 观察到的事实：Build Settings 共 `{reconciliation['entry_count']}` 项（启用 `{reconciliation['enabled_count']}`、禁用 `{reconciliation['disabled_count']}`），与场景索引集合对账：`{reconciliation['sets_match']}`；前三项仅表示配置顺序：{first_paths}。
- 观察到的事实：质量档位从文件动态解析为 `{quality['level_count']}` 档；平台默认项：{platform_defaults}。
- 我的判断：静态管线判断为 `{render['pipeline']['judgment']}`。{render['pipeline']['boundary']}
- layout mismatch 输入状态：`{layout['status']}`。安全使用边界：{'；'.join(layout['safe_usage']) or '未提供 runtime verification，未生成该边界'}
- 禁止推断：{'；'.join(layout['forbidden_inference']) or '无 runtime verification 派生项'}

Build Settings、Graphics/Quality/Player/Packages 的逐项值、来源 SHA-256 和动态行锚点保存在 `导出分析摘要.json`；Build Settings 顺序不得提升为运行时 SceneManager 调用图。
"""
    report = f"""# {project_title} 全量资源导出与场景搭建分析

## 来源与边界

- 观察到的事实：本报告读取本地授权生成的 Unity 项目结构，并基于其中的 `.unity`、`.prefab`、`.png` 和 Unity YAML 文本做本地统计；扫描根目录为 `{project_root}`。
- 观察到的事实：全量资源导出目录内共有 `{len(file_rows)}` 个文件，合计约 `{total_size / 1024 / 1024:.2f}` MiB。
- 我的判断：这些结果适合在本机学习 2D Unity 项目的层级组织、场景拆分、Prefab 复用、SpriteRenderer 分层、粒子和 UI 结构；原创项目应重新制作对应视觉内容。

## 全量资源概览

{make_markdown_table(ext_table_rows, ["扩展名", "数量"], limit=18)}

{make_markdown_table(cat_table_rows, ["类别", "数量"], limit=18)}

关键索引：

- 全量文件清单：`导出资源文件清单.csv`
- 场景索引：`场景索引.csv`
- Prefab 索引：`Prefab索引.csv`
- 图片尺寸索引：`图片资源尺寸索引.csv`

## 真实学习入口

- 场景学习入口（来自扫描）：{format_learning_entries(scene_entries)}
- Prefab 学习入口（来自扫描）：{format_learning_entries(prefab_entries)}

## 场景分组

观察到的事实：导出的 `.unity` 场景共 `{len(scene_rows)}` 个，主要分布如下。

{make_markdown_table(scene_group_rows, ["场景分组", "数量"], limit=30)}

我的判断：扫描到的多分组命名说明关卡内容被拆成许多小场景片段，便于按片段组织、复用和调试。运行时加载方式：待验证，不能仅凭静态目录判断是单场景、Additive、Addressables 还是自研加载流程。

## 场景搭建线索

SpriteRenderer 数量最高的场景：

{make_markdown_table(top_sprite_scenes, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "collider2d_count", "scene_building_tags"], limit=12)}

GameObject 数量最高的场景：

{make_markdown_table(top_go_scenes, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "canvas_count", "root_names"], limit=12)}

粒子系统数量最高的场景：

{make_markdown_table(top_particle_scenes, ["relative_path", "group", "gameobject_count", "particle_count", "spriterenderer_count", "root_names"], limit=12)}

Canvas/UI 数量最高的场景：

{make_markdown_table(top_canvas_scenes, ["relative_path", "group", "gameobject_count", "canvas_count", "recttransform_count", "root_names"], limit=12)}

我的判断：

- 场景主要不是单张背景图铺满，而是由多个 GameObject、SpriteRenderer、碰撞体、粒子和少量控制脚本组合出来。
- 学习时可以先看 SpriteRenderer 多、GameObject 多的场景，理解前景/中景/背景的层次拆法；再看 ParticleSystem 多的场景，理解氛围、攻击、机关或环境动效的挂载位置。
- UI 与关卡场景是分层存在的：带 Canvas/RectTransform 的场景更适合学习菜单、HUD、过场或交互面板，不应和关卡地形搭建混在一起分析。

## Prefab 组织

观察到的事实：导出的 `.prefab` 共 `{len(prefab_rows)}` 个，主要分组如下。

{make_markdown_table(prefab_group_rows, ["Prefab分组", "数量"], limit=18)}

GameObject 数量最高的 Prefab：

{make_markdown_table(top_prefabs, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "animator_count", "root_names"], limit=14)}

我的判断：Prefab 更适合学习“可复用对象”的拆法，例如角色、敌人、机关、特效、管理器和 UI 单元。看 Prefab 时重点关注根节点命名、子节点职责、Animator/ParticleSystem 是否被拆成子对象，以及同类对象是否保持一致层级。

## 图片资源入口

观察到的事实：导出的 PNG 图片共 `{len(image_rows)}` 张。面积最大的图片如下，适合先用作美术结构入口，但仍只建议本地查看学习。

{make_markdown_table(large_images, ["relative_path", "width", "height", "mode", "has_alpha", "category"], limit=16)}

我的判断：学习场景搭建时，不要只看单张图；更关键的是回到 `.unity` 场景里观察这些图如何被缩放、排序、分组、加碰撞、加粒子和绑定脚本。

{evidence_section}

## 推荐学习路径

1. 从扫描入口 `{primary_scene_entry}` 选一个 GameObject 数量高的场景，观察根节点和层级命名。
2. 打开同场景中 SpriteRenderer 数量高的对象，记录排序层、缩放、透明度、重复图块和遮挡关系。
3. 对照扫描入口 `{primary_prefab_entry}` 与其他 Prefab 目录，找出场景中反复出现的对象，理解哪些是关卡静态装饰，哪些是可复用玩法对象。
4. 单独查看 ParticleSystem 数量高的场景，提炼原创项目可迁移的粒子职责：环境漂浮、攻击反馈、受击反馈、机关提示、传送/过场。
5. 单独查看 Canvas/RectTransform 多的场景，拆 UI 的字号、图标尺寸、按钮状态、面板边距和视觉层级。

## 给原创项目的可迁移规范

- 场景拆分：按区域或房间拆小场景，每个场景保持清晰根节点，如 `Background`、`Middleground`、`Foreground`、`Collision`、`Triggers`、`FX`、`Lighting`、`Camera`。
- 图层组织：SpriteRenderer 以排序层和 Z/局部层级双重控制，避免把美术层、碰撞层、触发层混在同一个节点树里。
- Prefab 规范：可交互对象、敌人、机关和特效都做成可复用 Prefab；视觉子节点、碰撞子节点、脚本根节点分离，方便替换原创素材。
- 动效规范：角色和敌人用 Animator 管状态，环境氛围优先用粒子/短循环动画，UI 使用独立 Canvas 层。
- 色彩规范：为原创项目建立主色、辅助色、危险/交互提示色和低饱和环境色的分层表；同一关卡内控制冷暖对比，避免每个对象都抢视觉焦点。
- 形状语言：把角色、敌人、机关、地形、UI 图标分别定义成不同轮廓节奏；例如角色更强调可读外轮廓，机关强调硬边和方向性，背景强调大块面和重复节奏。
- 学习边界：只抽象结构方法、比例关系、层级命名习惯和制作约束；新项目应使用自己的角色、场景、图标、UI、动画和叙事表达。

## 待验证事项

- {editor_version_validation}
- 需要结合游戏内实际游玩画面，验证 YAML 统计推断出的前景/中景/背景关系是否与运行时镜头一致。
- 需要为原创项目重新制作色板、角色轮廓、UI 图标和关卡装饰，不应沿用本地学习样本的具体视觉表达。

## 文件说明

- 场景入口：{format_learning_entries(scene_entries)}
- Prefab 入口：{format_learning_entries(prefab_entries)}
- `{project_root.as_posix()}/Assets`：还包含更多由工具还原出的资源、脚本、材质、字体、音频和数据文件。
- `{project_root.as_posix()}/ProjectSettings`：Unity 项目设置，可辅助理解渲染、输入、标签层等配置。
- `{project_root.as_posix()}/Packages`：包配置，可辅助判断项目依赖。

"""
    output_path.write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title", default="目标游戏")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--runtime-verification",
        help="Optional strict JSON review/verification input containing layout mismatch evidence.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    config_path = Path(args.config).resolve(strict=True)
    runtime_verification_path = (
        Path(args.runtime_verification).resolve(strict=True) if args.runtime_verification else None
    )
    config = load_strict_json(config_path)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("AssetRipper project analysis config must be a schema_version=1 JSON object")
    output_dir.mkdir(parents=True, exist_ok=True)

    file_rows, ext_counts, category_counts = collect_file_inventory(project_root)
    scene_paths = sorted(project_root.rglob("*.unity"), key=lambda path: (rel(path, project_root).casefold(), rel(path, project_root)))
    prefab_paths = sorted(project_root.rglob("*.prefab"), key=lambda path: (rel(path, project_root).casefold(), rel(path, project_root)))
    scene_rows = [analyze_yaml_asset(path, project_root) for path in scene_paths]
    prefab_rows = [analyze_yaml_asset(path, project_root) for path in prefab_paths]
    image_rows = collect_image_index(project_root)
    unity_editor_version, unity_editor_version_status = read_unity_editor_version(project_root)
    build_settings_path = project_root / "ProjectSettings" / "EditorBuildSettings.asset"
    build_entries = parse_build_settings(build_settings_path, config) if build_settings_path.is_file() else []
    build_settings = {
        "status": "parsed" if build_settings_path.is_file() else "missing",
        "entries": build_entries,
        "reconciliation": reconcile_build_settings(build_entries, scene_rows),
    }
    render_settings = analyze_render_settings(project_root, config)
    layout_mismatch = analyze_layout_mismatch(runtime_verification_path, config)

    sources: dict[str, dict[str, Any]] = {"config": source_descriptor(config_path)}
    version_path = project_root / "ProjectSettings" / "ProjectVersion.txt"
    if version_path.is_file():
        sources["project_version"] = source_descriptor(version_path, project_root)
    if build_settings_path.is_file():
        sources["editor_build_settings"] = source_descriptor(build_settings_path, project_root)
    for name, path_string in render_settings["source_paths"].items():
        path = Path(path_string)
        if path.is_file():
            sources[f"render_{name}"] = source_descriptor(path, project_root)
    if runtime_verification_path is not None:
        sources["runtime_verification"] = source_descriptor(runtime_verification_path)

    inventory_fields = ["relative_path", "extension", "category", "size_bytes"]
    yaml_fields = [
        "relative_path",
        "group",
        "file_size",
        "yaml_object_count",
        "gameobject_count",
        "transform_count",
        "recttransform_count",
        "spriterenderer_count",
        "particle_count",
        "animator_count",
        "canvas_count",
        "camera_count",
        "collider2d_count",
        "monobehaviour_count",
        "tilemap_keyword_count",
        "grid_keyword_count",
        "sprite_guid_ref_count",
        "cinemachine_keyword_count",
        "timeline_keyword_count",
        "root_names",
        "sample_names",
        "scene_building_tags",
    ]
    image_fields = ["relative_path", "width", "height", "mode", "has_alpha", "category", "size_bytes", "error"]

    write_csv(output_dir / "导出资源文件清单.csv", file_rows, inventory_fields)
    write_csv(output_dir / "场景索引.csv", scene_rows, yaml_fields)
    write_csv(output_dir / "Prefab索引.csv", prefab_rows, yaml_fields)
    write_csv(output_dir / "图片资源尺寸索引.csv", image_rows, image_fields)
    summary = {
        "schema_version": 2,
        "project_root": str(project_root),
        "output_dir": str(output_dir),
        "file_count": len(file_rows),
        "scene_count": len(scene_rows),
        "prefab_count": len(prefab_rows),
        "png_count": len(image_rows),
        "unity_editor_version": unity_editor_version,
        "unity_editor_version_status": unity_editor_version_status,
        "top_extensions": [
            [name, count]
            for name, count in sorted(ext_counts.items(), key=lambda item: (-item[1], item[0]))[:30]
        ],
        "top_categories": [
            [name, count]
            for name, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:30]
        ],
        "sources": sources,
        "build_settings": build_settings,
        "render_settings": render_settings,
        "layout_mismatch": layout_mismatch,
    }
    verification = verify_sources_and_anchors(sources, build_settings, render_settings)
    summary["generation_verification"] = {
        **verification,
        "strict_json": True,
        "deterministic_serialization": "UTF-8, sort_keys=True, allow_nan=False, trailing newline",
        "json_reopen": "passed",
    }

    write_report(
        output_dir / "场景搭建分析.md",
        project_root,
        args.title,
        file_rows,
        ext_counts,
        category_counts,
        scene_rows,
        prefab_rows,
        image_rows,
        unity_editor_version,
        unity_editor_version_status,
        {
            "build_settings": build_settings,
            "render_settings": render_settings,
            "layout_mismatch": layout_mismatch,
        },
    )

    summary_path = output_dir / "导出分析摘要.json"
    payload = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    summary_path.write_text(payload, encoding="utf-8")
    reopened = load_strict_json(summary_path)
    if reopened != summary or summary_path.read_text(encoding="utf-8") != payload:
        raise RuntimeError("generated JSON failed exact reopen verification")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

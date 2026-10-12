#!/usr/bin/env python3
"""Build a model preview manifest for GameModelViewer."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import struct
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
        resolve_model_viewer_config_path,
        stable_model_id,
        write_json_utf8,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
        resolve_model_viewer_config_path,
        stable_model_id,
        write_json_utf8,
    )


def configured_path(root: Path, config: dict[str, Any], key: str) -> Path:
    root_path = resolve_existing(Path(root))
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else root_path / configured
    return ensure_inside_root(candidate, root_path)


def configured_child(root: Path, parent: Path, config: dict[str, Any], key: str) -> Path:
    root_path = resolve_existing(Path(root))
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else parent / configured
    return ensure_inside_root(candidate, root_path)


def _read_sidecar_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def is_external_local_uri(uri: str) -> bool:
    value = (uri or "").strip()
    if not value:
        return False
    if value.lower().startswith("data:"):
        return False
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return False
    if PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
        return False
    return True


def is_embedded_or_viewer_runtime_uri(uri: str) -> bool:
    value = (uri or "").strip().lower()
    return value.startswith(("data:", "blob:", "app:"))


def _read_gltf(model_file: Path) -> dict[str, Any]:
    payload = json.loads(model_file.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("glTF JSON root must be an object")
    return payload


def _read_glb(model_file: Path) -> dict[str, Any]:
    data = model_file.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB file is too short")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("invalid GLB magic")
    if version != 2:
        raise ValueError(f"unsupported GLB version: {version}")
    if declared_length > len(data):
        raise ValueError("GLB declared length exceeds file size")

    offset = 12
    while offset + 8 <= min(declared_length, len(data)):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_end > len(data):
            raise ValueError("GLB chunk exceeds file size")
        if chunk_type == 0x4E4F534A:
            payload = json.loads(data[offset:chunk_end].decode("utf-8").rstrip(" \t\r\n\x00"))
            if not isinstance(payload, dict):
                raise ValueError("GLB JSON root must be an object")
            return payload
        offset = chunk_end
    raise ValueError("GLB JSON chunk not found")


def _read_model_payload(model_file: Path) -> dict[str, Any]:
    suffix = model_file.suffix.lower()
    if suffix == ".gltf":
        return _read_gltf(model_file)
    if suffix == ".glb":
        return _read_glb(model_file)
    raise ValueError(f"unsupported model metadata format: {suffix}")


def _list_section(payload: dict[str, Any], section: str) -> list[Any]:
    value = payload.get(section, [])
    if value is None:
        raise ValueError(f"invalid glTF {section}: expected list")
    if not isinstance(value, list):
        raise ValueError(f"invalid glTF {section}: expected list")
    return value


def _collect_gltf_external_uris(payload: dict[str, Any]) -> list[str]:
    uris: list[str] = []
    for section in ("buffers", "images"):
        for entry in _list_section(payload, section):
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri")
            if isinstance(uri, str) and is_external_local_uri(uri):
                uris.append(uri)
    return uris


def _model_index_path(root: Path, config: dict[str, Any] | None = None) -> Path:
    configured = ""
    if isinstance(config, dict):
        paths = config.get("paths", {})
        if isinstance(paths, dict):
            configured = str(paths.get("unitypy_model_index") or "")
    candidate = Path(configured) if configured else Path("4.临时目录/中间索引/UnityPy模型导出/模型导出索引.csv")
    if not candidate.is_absolute():
        candidate = root / candidate
    return ensure_inside_root(candidate, root)


def _normalize_relative_model_path(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


def load_unitypy_model_index(root: Path, config: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    index_path = _model_index_path(resolve_existing(root), config)
    if not index_path.is_file():
        return {}
    rows_by_path: dict[str, dict[str, str]] = {}
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            gltf_path = _normalize_relative_model_path(row.get("gltf_path", ""))
            if gltf_path:
                rows_by_path[gltf_path] = {key: value or "" for key, value in row.items()}
    return rows_by_path


def model_index_row(root: Path, model_file: Path, rows_by_path: dict[str, dict[str, str]]) -> dict[str, str]:
    if not rows_by_path:
        return {}
    relative = _normalize_relative_model_path(model_file.relative_to(root).as_posix())
    return rows_by_path.get(relative, {})


def _collect_gltf_unsupported_uris(payload: dict[str, Any]) -> list[str]:
    uris: list[str] = []
    for section in ("buffers", "images"):
        for entry in _list_section(payload, section):
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                continue
            decoded = unquote(uri)
            if is_embedded_or_viewer_runtime_uri(decoded):
                continue
            if not is_external_local_uri(decoded):
                uris.append(uri)
    return uris


def collect_gltf_external_uris(model_file: Path) -> list[str]:
    if model_file.suffix.lower() != ".gltf":
        return []
    try:
        payload = _read_gltf(model_file)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return []
    return _collect_gltf_external_uris(payload)


def material_status(config: dict[str, Any], key: str) -> str:
    if key not in config["manifest"]["material_status"]:
        raise ValueError(f"unsupported material_status: {key}")
    return key


def _has_base_color_texture(payload: dict[str, Any]) -> bool:
    for material in _list_section(payload, "materials"):
        if not isinstance(material, dict):
            continue
        pbr = material.get("pbrMetallicRoughness", {})
        if isinstance(pbr, dict) and isinstance(pbr.get("baseColorTexture"), dict):
            return True
    return False


def resolve_gltf_external_uri(model_file: Path, uri: str) -> Path:
    decoded = unquote(uri)
    if not is_external_local_uri(decoded):
        raise ValueError(f"unsupported external resource uri: {uri}")
    model_dir = resolve_existing(model_file.parent)
    candidate = resolve_existing(model_dir / decoded)
    try:
        candidate.relative_to(model_dir)
    except ValueError as exc:
        raise ValueError(f"external resource outside model directory: {uri}") from exc
    return candidate


def inspect_gltf_resources(
    model_file: Path,
    config: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    if model_file.suffix.lower() not in {".gltf", ".glb"}:
        return "unknown", material_status(config, "unknown"), []

    try:
        payload = payload if payload is not None else _read_model_payload(model_file)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        return "unknown", material_status(config, "unknown"), [f"failed to read glTF: {exc}"]

    try:
        external_uris = _collect_gltf_external_uris(payload)
        unsupported_uris = _collect_gltf_unsupported_uris(payload)
        has_images = bool(_list_section(payload, "images"))
        has_textures = bool(_list_section(payload, "textures"))
        has_base_color_texture = _has_base_color_texture(payload)
    except ValueError as exc:
        return "unknown", material_status(config, "unknown"), [f"invalid glTF structure: {exc}"]

    resource_errors: list[str] = []
    for uri in unsupported_uris:
        resource_errors.append(f"unsupported external resource uri: {uri}")
    for uri in external_uris:
        try:
            resource_path = resolve_gltf_external_uri(model_file, uri)
        except ValueError as exc:
            resource_errors.append(str(exc))
            continue
        if not resource_path.exists():
            resource_errors.append(uri)
    if resource_errors:
        return "missing", material_status(config, "missing_external_resources"), resource_errors

    if has_images and has_base_color_texture:
        return "bound", material_status(config, "verified"), []
    if has_images or has_textures:
        return "same_name_candidate", material_status(config, "unknown"), []
    return "none", material_status(config, "unknown"), []


def _animation_duration(payload: dict[str, Any], animation: dict[str, Any]) -> float | None:
    accessors = _list_section(payload, "accessors")
    max_time: float | None = None
    for sampler in animation.get("samplers", []):
        if not isinstance(sampler, dict):
            continue
        input_index = sampler.get("input")
        if not isinstance(input_index, int) or input_index < 0 or input_index >= len(accessors):
            continue
        accessor = accessors[input_index]
        if not isinstance(accessor, dict):
            continue
        max_values = accessor.get("max")
        if isinstance(max_values, list) and max_values:
            try:
                value = float(max_values[0])
            except (TypeError, ValueError):
                continue
            max_time = value if max_time is None else max(max_time, value)
    return max_time


def animation_origin(payload: dict[str, Any] | None, animation_count: int) -> tuple[str, bool]:
    if animation_count <= 0:
        return "none", False
    if not isinstance(payload, dict):
        return "unknown", False

    extras = payload.get("extras", {})
    game_analysis = extras.get("game_analysis", {}) if isinstance(extras, dict) else {}
    if isinstance(game_analysis, dict):
        origin = str(game_analysis.get("animation_origin") or "").strip()
        if origin:
            is_generated = bool(game_analysis.get("is_generated_preview")) or origin == "procedural_preview"
            return origin, is_generated
    preview_accuracy = str(extras.get("preview_accuracy") or "") if isinstance(extras, dict) else ""
    if "procedural learning preview" in preview_accuracy.lower():
        return "procedural_preview", True
    return "embedded", False


def inspect_model_metadata(model_file: Path, payload: dict[str, Any] | None) -> tuple[dict[str, int], list[dict[str, Any]], str, str, bool]:
    stats = {
        "mesh_count": 0,
        "material_count": 0,
        "skin_count": 0,
        "animation_count": 0,
    }
    if payload is None:
        return stats, [], "unknown", "unknown", False

    meshes = _list_section(payload, "meshes")
    materials = _list_section(payload, "materials")
    skins = _list_section(payload, "skins")
    animations = _list_section(payload, "animations")
    stats.update(
        {
            "mesh_count": len(meshes),
            "material_count": len(materials),
            "skin_count": len(skins),
            "animation_count": len(animations),
        }
    )

    animation_items: list[dict[str, Any]] = []
    for index, animation in enumerate(animations):
        if not isinstance(animation, dict):
            continue
        name = animation.get("name")
        duration = _animation_duration(payload, animation)
        item: dict[str, Any] = {
            "index": index,
            "name": str(name).strip() if name else f"Animation {index + 1}",
        }
        if duration is not None:
            item["duration"] = round(duration, 4)
        animation_items.append(item)

    animation_status = "with" if animation_items else "none"
    origin, is_generated_preview = animation_origin(payload, stats["animation_count"])
    return stats, animation_items, animation_status, origin, is_generated_preview


def iter_model_files(models_dir: Path, supported_suffixes: list[str]) -> list[Path]:
    if not models_dir.exists():
        return []

    normalized_suffixes = {
        suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        for suffix in supported_suffixes
    }
    candidates = [
        path
        for path in models_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in normalized_suffixes
    ]
    return sorted(candidates, key=lambda path: path.as_posix().lower())


def build_tags(
    source_format: str,
    texture_status: str,
    preview_status: str,
    material_state: str,
    animation_status: str,
    animation_origin_value: str,
    config: dict[str, Any],
    material_binding_source: str = "",
    asset_role: str = "",
    visual_source: str = "",
) -> list[str]:
    rules = config["manifest"]["tag_rules"]
    tags: list[str] = []

    if rules.get("include_source_format") and source_format:
        tags.append(source_format)
    for prefix_key, value in (
        ("texture_prefix", texture_status),
        ("preview_prefix", preview_status),
        ("material_prefix", material_state),
        ("animation_prefix", animation_status),
        ("animation_origin_prefix", animation_origin_value),
    ):
        prefix = rules.get(prefix_key)
        if prefix and value:
            tags.append(f"{prefix}{value}")
    if material_binding_source:
        tags.append(f"texture-source:{material_binding_source}")
    if asset_role:
        tags.append(f"role:{asset_role}")
    if visual_source:
        tags.append(f"visual:{visual_source}")

    deduped: list[str] = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return deduped


def _infer_binding_source_from_gltf(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    for image in _list_section(payload, "images"):
        if not isinstance(image, dict):
            continue
        uri = str(image.get("uri") or "")
        if "待验证_同名前缀" in uri or "same_name" in uri.lower():
            return "same_name_candidate"
    return ""


def _binding_overrides(
    *,
    config: dict[str, Any],
    texture_state: str,
    material_state: str,
    material_binding_source: str,
) -> tuple[str, str]:
    if material_binding_source == "same_name_candidate":
        return "same_name_candidate", material_status(config, "unknown")
    if material_binding_source in {"unity_material_main_texture", "unity_material_configured_property"}:
        return "bound", material_status(config, "verified")
    return texture_state, material_state


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value not in ("", None):
            return str(value)
    return ""


def collect_original_source_fields(model_dir: Path) -> dict[str, str]:
    previous_metadata = _read_sidecar_json(model_dir / "metadata.json")
    previous_export_log = _read_sidecar_json(model_dir / "export-log.json")
    fields = {
        "source_file": _first_non_empty(
            previous_metadata.get("original_source_file"),
            previous_export_log.get("original_source_file"),
            previous_metadata.get("source_file"),
            previous_export_log.get("source_file"),
        ),
        "source_format": _first_non_empty(
            previous_metadata.get("original_source_format"),
            previous_export_log.get("original_source_format"),
            previous_metadata.get("source_format"),
            previous_export_log.get("source_format"),
        ),
        "source_model": _first_non_empty(
            previous_metadata.get("source_model"),
            previous_metadata.get("original_source_model"),
            previous_export_log.get("source_model"),
            previous_export_log.get("original_source_model"),
        ),
        "mesh_index": _first_non_empty(
            previous_metadata.get("mesh_index"),
            previous_metadata.get("original_mesh_index"),
            previous_export_log.get("mesh_index"),
            previous_export_log.get("original_mesh_index"),
        ),
        "mesh_offset": _first_non_empty(
            previous_metadata.get("mesh_offset"),
            previous_metadata.get("original_mesh_offset"),
            previous_export_log.get("mesh_offset"),
            previous_export_log.get("original_mesh_offset"),
        ),
        "texture_path": _first_non_empty(
            previous_metadata.get("texture_path"),
            previous_metadata.get("original_texture_path"),
            previous_export_log.get("texture_path"),
            previous_export_log.get("original_texture_path"),
        ),
        "texture_file": _first_non_empty(previous_metadata.get("texture_file"), previous_export_log.get("texture_file")),
        "vertex_count": _first_non_empty(previous_metadata.get("vertex_count"), previous_export_log.get("vertex_count")),
        "face_count": _first_non_empty(previous_metadata.get("face_count"), previous_export_log.get("face_count")),
        "uv_count": _first_non_empty(previous_metadata.get("uv_count"), previous_export_log.get("uv_count")),
    }
    return {key: value for key, value in fields.items() if value}


def normalize_original_source_format(original_source_fields: dict[str, str]) -> str:
    original_source_format = original_source_fields.get("source_format", "")
    if original_source_format == "Candide CDX embedded IndexedMesh":
        original_source_format = "Candide embedded IndexedMesh"
        original_source_fields["source_format"] = original_source_format
    return original_source_format


def matching_tmx_visual_source(original_source_file: str) -> str:
    if not original_source_file:
        return ""
    source = Path(original_source_file)
    if source.suffix.lower() != ".cmx":
        return ""
    candidate = source.with_suffix(".tmx")
    return str(candidate) if candidate.is_file() else ""


def derive_asset_role(
    *,
    original_source_file: str,
    original_source_format: str,
    texture_state: str,
    material_state: str,
) -> tuple[str, str, str]:
    normalized_source = original_source_file.replace("\\", "/").lower()
    normalized_format = original_source_format.lower()
    visual_source_path = matching_tmx_visual_source(original_source_file)

    if "/content/maps/" in normalized_source and normalized_source.endswith(".cmx"):
        if texture_state == "none" and visual_source_path:
            return "map_structure_mesh", "tmx_tileset_layers", visual_source_path
        return "map_embedded_mesh", "embedded_mesh", visual_source_path
    if texture_state == "bound" and material_state == "verified":
        return "textured_art_model", "model_material", ""
    if "indexedmesh" in normalized_format:
        return "embedded_mesh_candidate", "embedded_mesh", ""
    return "model_candidate", "model_file", ""


def _dependency_payload(
    *,
    source_path: Path,
    payload: dict[str, Any] | None,
    index_row: dict[str, str],
    resource_errors: list[str],
) -> dict[str, Any]:
    external_uris = _collect_gltf_external_uris(payload) if payload else []
    textures: list[dict[str, Any]] = []
    external_resources: list[dict[str, Any]] = []
    for uri in external_uris:
        resolved = resolve_gltf_external_uri(source_path, uri)
        record = {
            "uri": uri,
            "path": str(resolved),
            "exists": resolved.exists(),
        }
        external_resources.append(record)
        if resolved.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga"}:
            textures.append(record)
    return {
        "schema_version": 1,
        "source_model": str(source_path),
        "source_file": index_row.get("source_file", ""),
        "path_id": index_row.get("path_id", ""),
        "material_texture_name": index_row.get("material_texture_name", ""),
        "material_texture_uri": index_row.get("material_texture_uri", ""),
        "material_binding_source": index_row.get("material_binding_source", ""),
        "textures": textures,
        "external_resources": external_resources,
        "resource_errors": resource_errors,
    }


def write_model_sidecars(
    *,
    model_dir: Path,
    item: dict[str, Any],
    payload: dict[str, Any] | None,
    index_row: dict[str, str],
    resource_errors: list[str],
) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    dependency_payload = _dependency_payload(
        source_path=Path(item["source_path"]),
        payload=payload,
        index_row=index_row,
        resource_errors=resource_errors,
    )
    missing_resources = [
        resource for resource in dependency_payload["external_resources"] if not resource.get("exists")
    ]
    warnings: list[dict[str, str]] = []
    if item.get("material_binding_source") == "same_name_candidate":
        warnings.append({
            "code": "same_name_candidate_requires_review",
            "message": "同名前缀贴图仅用于预览候选，不等同于已验证 Unity 材质绑定。",
        })
    if item.get("texture_status") == "none":
        if item.get("asset_role") == "map_structure_mesh":
            warnings.append({
                "code": "map_structure_mesh_uses_tmx_visuals",
                "message": "该模型来自地图 CMX 内嵌结构网格；它本身不携带材质，地图视觉应回看同名 TMX 的瓦片层和 tileset。",
            })
        else:
            warnings.append({
                "code": "no_texture_binding",
                "message": "未找到可绑定贴图；可能是纯色/占位/特效模型，也可能需要进一步解析材质属性。",
            })
    original_source_fields = collect_original_source_fields(model_dir)
    original_source_format = normalize_original_source_format(original_source_fields)

    sidecars = {
        "metadata.json": {
            "schema_version": 1,
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "source_format": item.get("source_format", ""),
            "original_source_format": original_source_format,
            "source_path": item.get("source_path", ""),
            "original_source_file": original_source_fields.get("source_file", ""),
            "original_mesh_index": original_source_fields.get("mesh_index", ""),
            "original_mesh_offset": original_source_fields.get("mesh_offset", ""),
            "original_texture_path": original_source_fields.get("texture_path", ""),
            "preview_path": item.get("preview_path", ""),
            "stats": item.get("stats", {}),
            "texture_status": item.get("texture_status", ""),
            "material_status": item.get("material_status", ""),
            "material_binding_source": item.get("material_binding_source", ""),
            "asset_role": item.get("asset_role", ""),
            "visual_source": item.get("visual_source", ""),
            "visual_source_path": item.get("visual_source_path", ""),
            "animation_status": item.get("animation_status", ""),
            "animation_origin": item.get("animation_origin", ""),
            "is_generated_animation_preview": item.get("is_generated_animation_preview", False),
            "tags": item.get("tags", []),
        },
        "export-log.json": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "exported" if item.get("preview_status") != "failed" else "failed",
            "source_file": index_row.get("source_file", ""),
            "original_source_file": original_source_fields.get("source_file", ""),
            "original_source_format": original_source_format,
            "original_mesh_index": original_source_fields.get("mesh_index", ""),
            "original_mesh_offset": original_source_fields.get("mesh_offset", ""),
            "path_id": index_row.get("path_id", ""),
            "remarks": index_row.get("remarks", ""),
            "tool": "GameAnalysis build_model_gallery_manifest",
        },
        "dependencies.json": dependency_payload,
        "missing-resources.json": {
            "schema_version": 1,
            "missing_count": len(missing_resources),
            "missing_resources": missing_resources,
            "warnings": warnings,
            "errors": list(item.get("errors", [])),
        },
    }
    for name, content in sidecars.items():
        write_json_utf8(model_dir / name, content)


def build_item(
    root: Path,
    model_file: Path,
    config: dict[str, Any],
    unitypy_index_rows: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    root_path = resolve_existing(Path(root))
    source_path = ensure_inside_root(model_file, root_path)
    source_format = source_path.suffix.lower().lstrip(".")
    model_id = stable_model_id(source_path, root_path)
    preview_index = configured_path(root_path, config, "preview_index_dir")
    thumbs_dir = configured_child(root_path, preview_index, config, "thumbs_dir")
    preview_cache_dir = configured_child(root_path, preview_index, config, "preview_cache_dir")
    thumbnail_path = thumbs_dir / f"{model_id}.png"
    payload: dict[str, Any] | None = None
    metadata_errors: list[str] = []
    if source_format in {"gltf", "glb"}:
        try:
            payload = _read_model_payload(source_path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            metadata_errors.append(f"failed to read glTF: {exc}")
    texture_state, material_state, errors = inspect_gltf_resources(source_path, config, payload)
    index_row = model_index_row(root_path, source_path, unitypy_index_rows or {})
    material_binding_source = index_row.get("material_binding_source", "") or _infer_binding_source_from_gltf(payload)
    texture_state, material_state = _binding_overrides(
        config=config,
        texture_state=texture_state,
        material_state=material_state,
        material_binding_source=material_binding_source,
    )
    try:
        stats, animations, animation_status, animation_origin_value, is_generated_animation_preview = inspect_model_metadata(source_path, payload)
    except ValueError as exc:
        stats, animations, animation_status, animation_origin_value, is_generated_animation_preview = inspect_model_metadata(source_path, None)
        metadata_errors.append(f"invalid glTF metadata: {exc}")
    for error in metadata_errors:
        if error not in errors:
            errors.append(error)

    if source_format in config["formats"]["direct_preview"]:
        preview_status = "ready"
        preview_path = source_path
    elif source_format in config["formats"]["blender_preview"]:
        preview_status = "unsupported"
        preview_path = preview_cache_dir / f"{model_id}.preview.glb"
    else:
        preview_status = "unsupported"
        preview_path = source_path

    if source_format in {"gltf", "glb"} and errors:
        preview_status = "failed"

    original_source_fields = collect_original_source_fields(source_path.parent)
    original_source_format = normalize_original_source_format(original_source_fields)
    asset_role, visual_source, visual_source_path = derive_asset_role(
        original_source_file=original_source_fields.get("source_file", ""),
        original_source_format=original_source_format,
        texture_state=texture_state,
        material_state=material_state,
    )

    item = {
        "id": model_id,
        "name": source_path.stem,
        "source_format": source_format,
        "source_path": str(source_path),
        "model_dir": str(source_path.parent),
        "preview_status": preview_status,
        "preview_path": str(resolve_existing(preview_path)),
        "thumbnail_path": str(resolve_existing(thumbnail_path)),
        "texture_status": texture_state,
        "material_status": material_state,
        "material_binding_source": material_binding_source,
        "asset_role": asset_role,
        "visual_source": visual_source,
        "visual_source_path": visual_source_path,
        "material_texture_name": index_row.get("material_texture_name", ""),
        "material_texture_uri": index_row.get("material_texture_uri", ""),
        "stats": stats,
        "animations": animations,
        "animation_status": animation_status,
        "animation_origin": animation_origin_value,
        "is_generated_animation_preview": is_generated_animation_preview,
        "tags": build_tags(
            source_format,
            texture_state,
            preview_status,
            material_state,
            animation_status,
            animation_origin_value,
            config,
            material_binding_source,
            asset_role,
            visual_source,
        ),
        "errors": errors,
    }
    write_model_sidecars(
        model_dir=source_path.parent,
        item=item,
        payload=payload,
        index_row=index_row,
        resource_errors=errors,
    )
    return item


def _format_part_name(template: str, values: dict[str, str]) -> str:
    try:
        return template.format(**values)
    except KeyError:
        return ""


def _full_body_preview_part(item: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "item_id": item.get("id", ""),
        "name": item.get("name", ""),
        "source_format": item.get("source_format", ""),
        "source_path": item.get("source_path", ""),
        "preview_path": item.get("preview_path", ""),
        "model_dir": item.get("model_dir", ""),
    }


def attach_full_body_previews(items: list[dict[str, Any]], config: dict[str, Any]) -> None:
    preview_config = config.get("full_body_preview", {})
    if not isinstance(preview_config, dict) or not preview_config.get("enabled", False):
        return

    pattern_text = str(preview_config.get("name_pattern") or "")
    if not pattern_text:
        return
    try:
        name_pattern = re.compile(pattern_text)
    except re.error:
        return

    anchor_roles = {str(role) for role in preview_config.get("anchor_roles", [])}
    outfit_roles = [str(role) for role in preview_config.get("outfit_roles", [])]
    base_parts = [
        part
        for part in preview_config.get("base_parts", [])
        if isinstance(part, dict) and part.get("role") and part.get("name_template")
    ]
    tag = str(preview_config.get("tag") or "full-body-preview:available")
    item_by_name = {str(item.get("name") or ""): item for item in items}

    for item in items:
        name = str(item.get("name") or "")
        match = name_pattern.match(name)
        if not match:
            continue
        values = {key: str(value) for key, value in match.groupdict().items() if value is not None}
        role = values.get("role", "")
        if role not in anchor_roles:
            continue

        character = values.get("character", "")
        variant = values.get("variant", "")
        values["outfit_prefix"] = f"{character}_{variant}".strip("_")

        parts: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for part_rule in base_parts:
            part_name = _format_part_name(str(part_rule["name_template"]), values)
            part_item = item_by_name.get(part_name)
            if not part_item or part_name in seen_names:
                continue
            parts.append(_full_body_preview_part(part_item, str(part_rule["role"])))
            seen_names.add(part_name)

        for outfit_role in outfit_roles:
            part_name = f"{values['outfit_prefix']}_{outfit_role}"
            part_item = item_by_name.get(part_name)
            if not part_item or part_name in seen_names:
                continue
            parts.append(_full_body_preview_part(part_item, outfit_role))
            seen_names.add(part_name)

        if len(parts) < 2:
            continue

        name_template = str(preview_config.get("name_template") or "{outfit_prefix} 全身预览")
        preview_name = _format_part_name(name_template, values) or f"{name} 全身预览"
        item["full_body_preview"] = {
            "name": preview_name,
            "anchor_item_id": item.get("id", ""),
            "anchor_name": name,
            "parts": parts,
        }
        tags = item.setdefault("tags", [])
        if isinstance(tags, list) and tag not in tags:
            tags.append(tag)


def build_manifest(
    study_root: Path,
    config: dict[str, Any],
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    root_path = resolve_existing(Path(study_root))
    models_path = configured_path(root_path, config, "models_dir")
    preview_index = configured_path(root_path, config, "preview_index_dir")
    thumbs_dir = configured_child(root_path, preview_index, config, "thumbs_dir")
    preview_cache_dir = configured_child(root_path, preview_index, config, "preview_cache_dir")
    errors_path = configured_child(root_path, preview_index, config, "errors_name")

    thumbs_dir.mkdir(parents=True, exist_ok=True)
    preview_cache_dir.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.write_text("", encoding="utf-8")

    supported_suffixes = config["formats"]["source_priority"]
    unitypy_index_rows = load_unitypy_model_index(root_path, config)
    items = [
        build_item(root_path, model_file, config, unitypy_index_rows)
        for model_file in iter_model_files(models_path, supported_suffixes)
    ]
    attach_full_body_previews(items, config)

    manifest = {
        "schema_version": config["manifest"]["schema_version"],
        "viewer": {
            "required_tool": config["tool"]["name"],
            "tool_version": config["tool"].get("version", ""),
            "tool_directory": config["tool"].get("directory", ""),
            "tool_exe": config["tool"].get("exe_name", ""),
        },
        "study_root": str(root_path),
        "models_dir": str(models_path),
        "preview_index_dir": str(preview_index),
        "item_count": len(items),
        "items": items,
    }

    output_path = (
        preview_index / config["paths"]["manifest_name"]
        if manifest_path is None
        else Path(manifest_path)
    )
    if not output_path.is_absolute():
        output_path = root_path / output_path
    write_json_utf8(ensure_inside_root(output_path, root_path), manifest)
    return manifest


def errors_path_for(study_root: Path, config: dict[str, Any]) -> Path:
    root_path = resolve_existing(Path(study_root))
    preview_index = configured_path(root_path, config, "preview_index_dir")
    return configured_child(root_path, preview_index, config, "errors_name")


def record_blender_missing(study_root: Path, config: dict[str, Any], manifest: dict[str, Any]) -> Path:
    errors_path = errors_path_for(study_root, config)
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    items = manifest.get("items", [])
    with errors_path.open("a", encoding="utf-8", newline="\n") as handle:
        if isinstance(items, list) and items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                payload = {
                    "stage": "thumbnail",
                    "status": "skipped",
                    "reason": "blender_not_found",
                    "model_id": item.get("id", ""),
                    "source_path": item.get("source_path", ""),
                    "timestamp": timestamp,
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            payload = {
                "stage": "thumbnail",
                "status": "skipped",
                "reason": "blender_not_found",
                "timestamp": timestamp,
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return errors_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build GameModelViewer manifest.")
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument(
        "--config",
        default=None,
        type=Path,
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--record-blender-missing",
        action="store_true",
        help="Append thumbnail skipped records with reason=blender_not_found to errors.jsonl.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_model_viewer_config(resolve_model_viewer_config_path(args.config))
    manifest = build_manifest(args.study_root, config, args.manifest)
    if args.record_blender_missing:
        errors_path = record_blender_missing(args.study_root, config, manifest)
        print(f"recorded blender_not_found thumbnail skips in {errors_path}")
    print(f"wrote {manifest['item_count']} model items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

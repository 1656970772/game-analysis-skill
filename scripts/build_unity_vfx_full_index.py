#!/usr/bin/env python3
"""Build a complete, traceable Unity VFX component index and preview manifest.

This full-scale path avoids the quadratic same-source dependency scan and the
interactive preview manifest item cap while preserving the public manifest
contract. Project-specific root counts remain derived from source indexes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT_TYPES = {"ParticleSystem", "TrailRenderer", "LineRenderer"}
COMPONENT_TYPES = ROOT_TYPES | {"ParticleSystemRenderer"}
RESOURCE_TYPES = {"Material", "Texture2D", "Sprite", "Mesh", "AnimationClip"}
TARGET_CSV_FIELDS = [
    "source_file",
    "component_type",
    "component_path_id",
    "asset_id",
    "asset_name",
    "metadata_path",
    "metadata_exists",
    "game_object_file_id",
    "game_object_path_id",
    "game_object_name",
    "game_object_metadata_path",
    "paired_particle_system_path_ids",
    "paired_renderer_path_ids",
    "paired_trail_renderer_path_ids",
    "paired_line_renderer_path_ids",
    "material_refs",
    "resolved_material_paths",
    "texture_refs",
    "resolved_texture_paths",
    "mesh_refs",
    "resolved_mesh_paths",
    "unresolved_dependency_count",
    "trace_status",
]
DEPENDENCY_AUDIT_FIELDS = [
    "root_source_file",
    "root_component_type",
    "root_component_path_id",
    "relation",
    "owner_asset_type",
    "owner_path_id",
    "ordinal",
    "ref_file_id",
    "ref_path_id",
    "reference_state",
    "resolved_source_file",
    "resolved_asset_type",
    "resolved_asset_name",
    "resolved_export_path",
    "reason",
]
RECONSTRUCTION_STRUCTURE_FIELDS = [
    "source_file",
    "path_id",
    "root_type",
    "source_refs",
    "dependencies",
    "reconstruction_relative_path",
]
RECONSTRUCTION_STATE_FIELDS = [
    "preview_status",
    "reconstruction_status",
    "unity_export_status",
    "unity_export_relative_path",
    "unity_export_path",
    "unity_export_reason",
    "warnings",
]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga"}
VFX_YAML_RE = re.compile(r"^--- !u!(198|199|96|120) ", re.MULTILINE)
INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def resolve_config_path(study_root: Path, value: str, field: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} must be a non-empty POSIX relative path")
    if value.startswith("/") or WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"{field} must be relative to study-root")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field} contains an unsafe path segment")
    candidate = study_root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(study_root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"{field} resolves outside study-root") from exc
    return candidate


def resolve_full_paths(study_root: Path, config: dict[str, Any]) -> dict[str, Path]:
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("config.paths must be an object")
    defaults = {
        "unitypy_root": "0.原始导出/UnityPy/UnityPy全量可读资源",
        "unitypy_index": "0.原始导出/UnityPy/UnityPy全量可读资源/all_resources_index.csv",
        "preview_index_dir": "4.临时目录/中间索引/VFX预览",
        "classified_vfx_dir": "1.分类资源/特效",
        "full_structure_index_dir": "4.临时目录/中间索引/VFX结构",
        "assetripper_assets_root": "0.原始导出/AssetRipper/ExportedProject/Assets",
    }
    values = {key: str(paths.get(key) or default) for key, default in defaults.items()}
    return {
        "unitypy_root": resolve_config_path(study_root, values["unitypy_root"], "paths.unitypy_root"),
        "index_path": resolve_config_path(study_root, values["unitypy_index"], "paths.unitypy_index"),
        "preview_dir": resolve_config_path(study_root, values["preview_index_dir"], "paths.preview_index_dir"),
        "classified_root": resolve_config_path(
            study_root, values["classified_vfx_dir"], "paths.classified_vfx_dir"
        ),
        "structure_dir": resolve_config_path(
            study_root, values["full_structure_index_dir"], "paths.full_structure_index_dir"
        ),
        "assets_root": resolve_config_path(
            study_root, values["assetripper_assets_root"], "paths.assetripper_assets_root"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    return parser.parse_args()


def safe_name(value: str, fallback: str = "asset") -> str:
    result = INVALID_NAME_RE.sub("_", str(value)).strip(" ._")
    return (result[:140] or fallback)


def stable_id(source_file: str, path_id: str, asset_type: str, name: str) -> str:
    payload = f"{source_file}\n{path_id}\n{asset_type}\n{name.replace(chr(92), '/')}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def safe_child(root: Path, relative: str) -> Path | None:
    if not relative or relative.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(relative):
        return None
    try:
        root_resolved = root.resolve(strict=False)
        candidate = (root / relative).resolve(strict=False)
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def type_tree(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("type_tree")
    return value if isinstance(value, dict) else payload


def pp_ref(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    file_id = value.get("m_FileID", value.get("fileID", value.get("file_id")))
    path_id = value.get("m_PathID", value.get("pathID", value.get("path_id")))
    if path_id in (None, "", 0, "0"):
        return None
    return str(file_id or 0), str(path_id)


def pptr_with_state(value: Any) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    has_file = any(key in value for key in ("m_FileID", "fileID", "file_id"))
    has_path = any(key in value for key in ("m_PathID", "pathID", "path_id"))
    if not (has_file and has_path):
        return None
    file_id = value.get("m_FileID", value.get("fileID", value.get("file_id", 0)))
    path_id = value.get("m_PathID", value.get("pathID", value.get("path_id", 0)))
    file_text = str(file_id or 0)
    path_text = str(path_id or 0)
    if path_text == "0":
        state = "null"
    elif file_text != "0":
        state = "cross_bundle"
    else:
        state = "local"
    return file_text, path_text, state


def all_pptrs(value: Any) -> list[tuple[str, str, str]]:
    direct = pptr_with_state(value)
    if direct is not None:
        return [direct]
    output: list[tuple[str, str, str]] = []
    if isinstance(value, dict):
        for child in value.values():
            output.extend(all_pptrs(child))
    elif isinstance(value, list):
        for child in value:
            output.extend(all_pptrs(child))
    return output


def all_refs(value: Any) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    direct = pp_ref(value)
    if direct is not None:
        output.append(direct)
    if isinstance(value, dict):
        for child in value.values():
            output.extend(all_refs(child))
    elif isinstance(value, list):
        for child in value:
            output.extend(all_refs(child))
    return output


def unique_refs(refs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    output: list[tuple[str, str]] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            output.append(ref)
    return output


def component_resource_refs(asset_type: str, tree: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    material_refs: list[tuple[str, str]] = []
    mesh_refs: list[tuple[str, str]] = []
    if asset_type in {"ParticleSystemRenderer", "TrailRenderer", "LineRenderer"}:
        material_refs.extend(all_refs(tree.get("m_Materials", [])))
        for key, value in tree.items():
            if key.startswith("m_Mesh"):
                mesh_refs.extend(all_refs(value))
    return unique_refs(material_refs), unique_refs(mesh_refs)


def component_resource_pptrs(
    asset_type: str,
    tree: dict[str, Any],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    material_refs: list[tuple[str, str, str]] = []
    mesh_refs: list[tuple[str, str, str]] = []
    if asset_type in {"ParticleSystemRenderer", "TrailRenderer", "LineRenderer"}:
        material_refs.extend(all_pptrs(tree.get("m_Materials", [])))
        for key, value in tree.items():
            if key.startswith("m_Mesh"):
                mesh_refs.extend(all_pptrs(value))
    return material_refs, mesh_refs


def compact(value: Any, depth: int = 0) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if depth >= 5:
        return "<truncated>" if isinstance(value, (dict, list)) else value
    if isinstance(value, dict):
        return {str(key): compact(child, depth + 1) for key, child in list(value.items())[:96]}
    if isinstance(value, list):
        return [compact(child, depth + 1) for child in value[:32]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def resource_ref(row: dict[str, str], match_reason: str, confidence: str) -> dict[str, Any]:
    output: dict[str, Any] = dict(row)
    output.update(
        {
            "id": str(row.get("path_id", "")),
            "type": str(row.get("asset_type", "")),
            "name": str(row.get("asset_name", "")),
            "relative_path": str(row.get("export_path", "")).replace("\\", "/"),
            "match_reason": match_reason,
            "confidence": confidence,
        }
    )
    return output


def keyword_tags(text: str, config: dict[str, Any]) -> list[str]:
    lower = text.lower()
    return [
        str(rule["tag"])
        for rule in config.get("name_keyword_rules", [])
        if any(str(keyword).lower() in lower for keyword in rule.get("keywords", []) if str(keyword))
    ]


def write_json(path: Path, payload: Any, *, compact_payload: bool = False) -> None:
    serializable = compact(payload) if compact_payload else payload
    data = (json.dumps(serializable, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    write_bytes_if_changed(path, data)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    data = "".join(
        json.dumps(compact(row), ensure_ascii=False, allow_nan=False) + "\n" for row in rows
    ).encode("utf-8")
    write_bytes_if_changed(path, data)


def write_bytes_if_changed(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_equal(left: Path, right: Path) -> bool:
    return (
        left.is_file()
        and right.is_file()
        and left.stat().st_size == right.stat().st_size
        and file_sha256(left) == file_sha256(right)
    )


def copy_file_if_changed(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if files_equal(source, destination):
        return False
    shutil.copy2(source, destination)
    return True


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    write_bytes_if_changed(path, b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8"))


def reconstruction_structure_key(item: dict[str, Any]) -> str:
    payload = {field: item.get(field) for field in RECONSTRUCTION_STRUCTURE_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def merge_existing_reconstruction_state(
    manifest_path: Path,
    preview_dir: Path,
    items: list[dict[str, Any]],
) -> int:
    existing = load_json(manifest_path)
    old_items = existing.get("items")
    if not isinstance(old_items, list):
        return 0
    by_id = {
        str(item.get("id")): item
        for item in old_items
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    reused = 0
    for item in items:
        old = by_id.get(str(item.get("id") or ""))
        if old is None or reconstruction_structure_key(old) != reconstruction_structure_key(item):
            continue
        relative = str(item.get("reconstruction_relative_path") or "")
        reconstruction = safe_child(preview_dir, relative)
        if reconstruction is None or not reconstruction.is_file():
            continue
        if str(old.get("reconstruction_status") or "pending") == "pending":
            continue
        for field in RECONSTRUCTION_STATE_FIELDS:
            if field in old:
                item[field] = old[field]
        reused += 1
    return reused


def scan_rows(index_path: Path) -> tuple[list[dict[str, str]], dict[tuple[str, str], dict[str, str]]]:
    components: list[dict[str, str]] = []
    resources: dict[tuple[str, str], dict[str, str]] = {}
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            asset_type = row.get("asset_type", "")
            if asset_type in COMPONENT_TYPES:
                components.append(dict(row))
            elif asset_type in RESOURCE_TYPES:
                resources[(row.get("source_file", ""), str(row.get("path_id", "")))] = dict(row)
    return components, resources


def collect_component_details(
    components: list[dict[str, str]],
    unitypy_root: Path,
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    details: list[dict[str, Any]] = []
    game_object_keys: set[tuple[str, str]] = set()
    for row in components:
        metadata_path = safe_child(unitypy_root, row.get("export_path", ""))
        payload = load_json(metadata_path)
        tree = type_tree(payload)
        go_ref = pp_ref(tree.get("m_GameObject"))
        material_refs, mesh_refs = component_resource_refs(row.get("asset_type", ""), tree)
        material_pptrs, mesh_pptrs = component_resource_pptrs(row.get("asset_type", ""), tree)
        if go_ref is not None and go_ref[0] == "0":
            game_object_keys.add((row.get("source_file", ""), go_ref[1]))
        details.append(
            {
                "row": row,
                "metadata_path": metadata_path,
                "metadata_exists": bool(metadata_path and metadata_path.is_file()),
                "game_object_ref": go_ref,
                "material_refs": material_refs,
                "mesh_refs": mesh_refs,
                "material_pptrs": material_pptrs,
                "mesh_pptrs": mesh_pptrs,
            }
        )
    return details, game_object_keys


def collect_game_objects(index_path: Path, keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_type") != "GameObject":
                continue
            key = (row.get("source_file", ""), str(row.get("path_id", "")))
            if key in keys:
                output[key] = {"row": dict(row)}
    return output


def hydrate_game_objects(game_objects: dict[tuple[str, str], dict[str, Any]], unitypy_root: Path) -> None:
    for item in game_objects.values():
        row = item["row"]
        path = safe_child(unitypy_root, row.get("export_path", ""))
        payload = load_json(path)
        tree = type_tree(payload)
        item["metadata_path"] = path
        item["name"] = str(tree.get("m_Name") or row.get("asset_name") or f"pathid_{row.get('path_id', '')}")
        item["component_refs"] = unique_refs(all_refs(tree.get("m_Component", [])))


def resolve_local_resource(
    source_file: str,
    ref: tuple[str, str],
    rows: dict[tuple[str, str], dict[str, str]],
    accepted_types: set[str],
) -> dict[str, str] | None:
    file_id, path_id = ref
    if file_id != "0":
        return None
    row = rows.get((source_file, path_id))
    return row if row and row.get("asset_type") in accepted_types else None


def material_texture_refs(material_row: dict[str, str], unitypy_root: Path) -> list[tuple[str, str]]:
    path = safe_child(unitypy_root, material_row.get("export_path", ""))
    tree = type_tree(load_json(path))
    saved = tree.get("m_SavedProperties", tree)
    return unique_refs(all_refs(saved))


def material_texture_pptrs(
    material_row: dict[str, str], unitypy_root: Path
) -> list[tuple[str, str, str]]:
    path = safe_child(unitypy_root, material_row.get("export_path", ""))
    tree = type_tree(load_json(path))
    saved = tree.get("m_SavedProperties", tree)
    return all_pptrs(saved)


def reference_audit_row(
    *,
    root_row: dict[str, str],
    relation: str,
    owner_asset_type: str,
    owner_path_id: str,
    ordinal: int,
    ref: tuple[str, str, str],
    resources: dict[tuple[str, str], dict[str, str]],
    accepted_types: set[str],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    source = root_row.get("source_file", "")
    file_id, path_id, initial_state = ref
    resolved: dict[str, str] | None = None
    state = initial_state
    reason = ""
    if state == "local":
        candidate = resources.get((source, path_id))
        if candidate is not None and candidate.get("asset_type") in accepted_types:
            resolved = candidate
            state = "resolved"
        else:
            state = "unresolved"
            reason = "local_path_id_missing_or_type_mismatch"
    elif state == "cross_bundle":
        reason = "external_file_id_requires_serialized_external_reference_table"
    elif state == "null":
        reason = "explicit_null_pptr"
    row = {
        "root_source_file": source,
        "root_component_type": root_row.get("asset_type", ""),
        "root_component_path_id": root_row.get("path_id", ""),
        "relation": relation,
        "owner_asset_type": owner_asset_type,
        "owner_path_id": owner_path_id,
        "ordinal": ordinal,
        "ref_file_id": file_id,
        "ref_path_id": path_id,
        "reference_state": state,
        "resolved_source_file": (resolved or {}).get("source_file", ""),
        "resolved_asset_type": (resolved or {}).get("asset_type", ""),
        "resolved_asset_name": (resolved or {}).get("asset_name", ""),
        "resolved_export_path": (resolved or {}).get("export_path", ""),
        "reason": reason,
    }
    return row, resolved


def classification_config(
    detail: dict[str, Any],
    game_object: dict[str, Any] | None,
    pair_map: dict[tuple[str, str], dict[str, list[str]]],
    classified_root: Path,
) -> None:
    row = detail["row"]
    source = row.get("source_file", "")
    go_ref = detail["game_object_ref"]
    go_path_id = go_ref[1] if go_ref and go_ref[0] == "0" else ""
    source_slug = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    target = (
        classified_root
        / "配置"
        / row.get("asset_type", "Unknown")
        / source_slug
        / f"pathid_{safe_name(str(row.get('path_id', '0')), '0')}.json"
    )
    pairs = pair_map.get((source, go_path_id), {})
    configuration = type_tree(load_json(detail["metadata_path"]))
    payload = {
        "schema_version": 1,
        "classification": "可学习VFX组件配置",
        "source": {
            "source_file": source,
            "path_id": row.get("path_id", ""),
            "asset_type": row.get("asset_type", ""),
            "asset_name": row.get("asset_name", ""),
            "metadata_path": row.get("export_path", ""),
        },
        "game_object": {
            "path_id": go_path_id,
            "name": str((game_object or {}).get("name", "")),
        },
        "paired_components": pairs,
        "material_refs": detail["material_refs"],
        "mesh_refs": detail["mesh_refs"],
        "configuration": configuration,
        "boundary": "本卡为静态序列化配置；不代表完整复现 Unity runtime 粒子模拟。",
    }
    write_json(target, payload, compact_payload=True)


def copy_keyword_textures(
    resources: dict[tuple[str, str], dict[str, str]],
    unitypy_root: Path,
    classified_root: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    publish_candidates = bool(
        config.get("classification", {}).get("publish_keyword_texture_candidates", True)
    )
    for row in resources.values():
        if row.get("asset_type") not in {"Texture2D", "Sprite"}:
            continue
        tags = keyword_tags(
            " ".join(
                [
                    row.get("asset_name", ""),
                    row.get("container_path", ""),
                    row.get("export_path", ""),
                ]
            ),
            config,
        )
        if not tags:
            continue
        source = safe_child(unitypy_root, row.get("export_path", ""))
        if source is None or not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        group = safe_name(tags[0].split(":", 1)[-1], "未分组")
        asset_id = stable_id(
            row.get("source_file", ""),
            str(row.get("path_id", "")),
            row.get("asset_type", ""),
            row.get("asset_name", ""),
        )
        extension = source.suffix.lower()
        destination_name = f"{asset_id}_{safe_name(source.stem)}{extension}"
        destination = classified_root / "粒子贴图" / group / destination_name
        if publish_candidates:
            copy_file_if_changed(source, destination)
        legacy = classified_root / "粒子贴图" / group / f"{asset_id}_{safe_name(source.name)}"
        removed_legacy = False
        if publish_candidates and legacy != destination and not legacy.suffix and files_equal(source, legacy):
            legacy.unlink()
            removed_legacy = True
        copied.append(
            {
                "source_file": row.get("source_file", ""),
                "path_id": row.get("path_id", ""),
                "asset_type": row.get("asset_type", ""),
                "asset_name": row.get("asset_name", ""),
                "source_export_path": row.get("export_path", ""),
                "classified_path": str(destination) if publish_candidates else "",
                "proposed_relative_path": destination.relative_to(classified_root).as_posix(),
                "publication_status": "published" if publish_candidates else "evidence_only",
                "tags": tags,
                "removed_legacy_extensionless_copy": removed_legacy,
            }
        )
    return copied


def make_pair_map(details: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, list[str]]]:
    pair_map: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for detail in details:
        row = detail["row"]
        go_ref = detail["game_object_ref"]
        if go_ref is None or go_ref[0] != "0":
            continue
        pair_map[(row.get("source_file", ""), go_ref[1])][row.get("asset_type", "")].append(
            str(row.get("path_id", ""))
        )
    return pair_map


def dependency_state(
    detail: dict[str, Any],
    pair_map: dict[tuple[str, str], dict[str, list[str]]],
    component_rows: dict[tuple[str, str, str], dict[str, str]],
    resources: dict[tuple[str, str], dict[str, str]],
    unitypy_root: Path,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    row = detail["row"]
    source = row.get("source_file", "")
    go_ref = detail["game_object_ref"]
    go_id = go_ref[1] if go_ref and go_ref[0] == "0" else ""
    paired = pair_map.get((source, go_id), {})
    dependencies: dict[str, list[dict[str, Any]]] = {
        "renderers": [],
        "materials": [],
        "textures": [],
        "meshes": [],
        "animations": [],
    }
    errors: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    renderer_ids = paired.get("ParticleSystemRenderer", []) if row.get("asset_type") == "ParticleSystem" else []
    if row.get("asset_type") == "ParticleSystem" and not renderer_ids:
        audit_rows.append(
            {
                "root_source_file": source,
                "root_component_type": row.get("asset_type", ""),
                "root_component_path_id": row.get("path_id", ""),
                "relation": "renderer",
                "owner_asset_type": "ParticleSystem",
                "owner_path_id": row.get("path_id", ""),
                "ordinal": 0,
                "ref_file_id": "0",
                "ref_path_id": "0",
                "reference_state": "null",
                "resolved_source_file": "",
                "resolved_asset_type": "",
                "resolved_asset_name": "",
                "resolved_export_path": "",
                "reason": "no_same_game_object_particle_system_renderer",
            }
        )
    for ordinal, renderer_id in enumerate(renderer_ids):
        renderer_row = component_rows.get((source, "ParticleSystemRenderer", renderer_id))
        if renderer_row:
            dependencies["renderers"].append(resource_ref(renderer_row, "same_game_object", "high"))
        audit_rows.append(
            {
                "root_source_file": source,
                "root_component_type": row.get("asset_type", ""),
                "root_component_path_id": row.get("path_id", ""),
                "relation": "renderer",
                "owner_asset_type": "ParticleSystem",
                "owner_path_id": row.get("path_id", ""),
                "ordinal": ordinal,
                "ref_file_id": "0",
                "ref_path_id": renderer_id,
                "reference_state": "resolved" if renderer_row else "unresolved",
                "resolved_source_file": source if renderer_row else "",
                "resolved_asset_type": "ParticleSystemRenderer" if renderer_row else "",
                "resolved_asset_name": (renderer_row or {}).get("asset_name", ""),
                "resolved_export_path": (renderer_row or {}).get("export_path", ""),
                "reason": "same_game_object_pair" if renderer_row else "paired_renderer_missing_from_component_index",
            }
        )
        if renderer_row is None:
            errors.append(
                {
                    "code": "unresolved_renderer_ref",
                    "source_file": source,
                    "component_type": row.get("asset_type", ""),
                    "component_path_id": row.get("path_id", ""),
                    "file_id": "0",
                    "path_id": renderer_id,
                }
            )

    detail_lookup = component_rows["_detail_lookup"]  # type: ignore[index]
    material_owners: list[tuple[str, str, list[tuple[str, str, str]]]] = [
        (row.get("asset_type", ""), str(row.get("path_id", "")), list(detail["material_pptrs"]))
    ]
    mesh_owners: list[tuple[str, str, list[tuple[str, str, str]]]] = [
        (row.get("asset_type", ""), str(row.get("path_id", "")), list(detail["mesh_pptrs"]))
    ]
    for renderer_id in renderer_ids:
        linked = detail_lookup.get((source, "ParticleSystemRenderer", renderer_id))
        if linked:
            material_owners.append(("ParticleSystemRenderer", renderer_id, list(linked["material_pptrs"])))
            mesh_owners.append(("ParticleSystemRenderer", renderer_id, list(linked["mesh_pptrs"])))

    resolved_materials: dict[tuple[str, str], dict[str, str]] = {}
    for owner_type, owner_path_id, refs in material_owners:
        for ordinal, ref in enumerate(refs):
            audit, resolved = reference_audit_row(
                root_row=row,
                relation="material",
                owner_asset_type=owner_type,
                owner_path_id=owner_path_id,
                ordinal=ordinal,
                ref=ref,
                resources=resources,
                accepted_types={"Material"},
            )
            audit_rows.append(audit)
            if resolved is None:
                if audit["reference_state"] in {"unresolved", "cross_bundle"}:
                    errors.append(
                        {
                            "code": (
                                "cross_bundle_material_ref"
                                if audit["reference_state"] == "cross_bundle"
                                else "unresolved_material_ref"
                            ),
                            "source_file": source,
                            "component_type": row.get("asset_type", ""),
                            "component_path_id": row.get("path_id", ""),
                            "file_id": ref[0],
                            "path_id": ref[1],
                        }
                    )
                continue
            dependencies["materials"].append(resource_ref(resolved, "explicit_ref", "high"))
            resolved_materials[(resolved.get("source_file", ""), resolved.get("path_id", ""))] = resolved

    for resolved in resolved_materials.values():
        for ordinal, texture_ref in enumerate(material_texture_pptrs(resolved, unitypy_root)):
            audit, texture = reference_audit_row(
                root_row=row,
                relation="texture",
                owner_asset_type="Material",
                owner_path_id=str(resolved.get("path_id", "")),
                ordinal=ordinal,
                ref=texture_ref,
                resources=resources,
                accepted_types={"Texture2D", "Sprite"},
            )
            audit_rows.append(audit)
            if texture is None:
                if audit["reference_state"] in {"unresolved", "cross_bundle"}:
                    errors.append(
                        {
                            "code": (
                                "cross_bundle_texture_ref"
                                if audit["reference_state"] == "cross_bundle"
                                else "unresolved_texture_ref"
                            ),
                            "source_file": source,
                            "component_type": row.get("asset_type", ""),
                            "component_path_id": row.get("path_id", ""),
                            "material_path_id": resolved.get("path_id", ""),
                            "file_id": texture_ref[0],
                            "path_id": texture_ref[1],
                        }
                    )
                continue
            dependencies["textures"].append(resource_ref(texture, "material_texture_ref", "high"))

    for owner_type, owner_path_id, refs in mesh_owners:
        for ordinal, ref in enumerate(refs):
            audit, resolved = reference_audit_row(
                root_row=row,
                relation="mesh",
                owner_asset_type=owner_type,
                owner_path_id=owner_path_id,
                ordinal=ordinal,
                ref=ref,
                resources=resources,
                accepted_types={"Mesh"},
            )
            audit_rows.append(audit)
            if resolved is None:
                if audit["reference_state"] in {"unresolved", "cross_bundle"}:
                    errors.append(
                        {
                            "code": (
                                "cross_bundle_mesh_ref"
                                if audit["reference_state"] == "cross_bundle"
                                else "unresolved_mesh_ref"
                            ),
                            "source_file": source,
                            "component_type": row.get("asset_type", ""),
                            "component_path_id": row.get("path_id", ""),
                            "file_id": ref[0],
                            "path_id": ref[1],
                        }
                    )
                continue
            dependencies["meshes"].append(resource_ref(resolved, "explicit_ref", "high"))

    for bucket in dependencies:
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in dependencies[bucket]:
            key = (
                str(entry.get("source_file", "")),
                str(entry.get("asset_type", "")),
                str(entry.get("path_id", "")),
            )
            unique[key] = entry
        dependencies[bucket] = list(unique.values())
    return dependencies, errors, audit_rows


def build_manifest_item(
    detail: dict[str, Any],
    game_object: dict[str, Any] | None,
    dependencies: dict[str, list[dict[str, Any]]],
    dependency_errors: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    row = detail["row"]
    source = row.get("source_file", "")
    path_id = str(row.get("path_id", ""))
    asset_type = row.get("asset_type", "")
    go_name = str((game_object or {}).get("name", ""))
    item_id = stable_id(source, path_id, asset_type, go_name or row.get("asset_name", ""))
    display_name = f"{safe_name(go_name or asset_type)}_{item_id}"
    tag_text = " ".join(
        [
            go_name,
            row.get("asset_name", ""),
            source,
            *[str(dep.get("asset_name", "")) for values in dependencies.values() for dep in values],
        ]
    )
    warnings = sorted({str(error["code"]) for error in dependency_errors})
    if asset_type == "ParticleSystem" and not dependencies["renderers"]:
        warnings.append("missing_renderer")
    if asset_type in {"ParticleSystem", "TrailRenderer", "LineRenderer"} and not dependencies["materials"]:
        warnings.append("missing_material")
    if asset_type == "ParticleSystem" and not dependencies["textures"]:
        warnings.append("missing_texture")
    supported_by_type = {
        "ParticleSystem": [
            "main",
            "emission",
            "shape",
            "velocityOverLifetime",
            "colorOverLifetime",
            "sizeOverLifetime",
            "textureSheetAnimation",
            "renderer",
        ],
        "TrailRenderer": ["trail"],
        "LineRenderer": ["line"],
    }
    source_ref = dict(row)
    return {
        "schema_version": config["manifest"]["schema_version"],
        "engine": "unity",
        "id": item_id,
        "name": display_name,
        "display_name": go_name or row.get("asset_name", ""),
        "kind": {
            "ParticleSystem": "particle_system",
            "TrailRenderer": "trail_renderer",
            "LineRenderer": "line_renderer",
        }.get(asset_type, "unsupported"),
        "root_type": asset_type,
        "source_file": source,
        "path_id": path_id,
        "asset_type": asset_type,
        "reconstruction_relative_path": f"reconstructed/{display_name}-{path_id}.json",
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
        "source_refs": [source_ref],
        "supported_modules": supported_by_type.get(asset_type, []),
        "unsupported_modules": [],
        "tags": keyword_tags(tag_text, config),
        "dependencies": dependencies,
        "warnings": sorted(set(warnings)),
        "structure_trace": {
            "game_object_path_id": (
                detail["game_object_ref"][1]
                if detail["game_object_ref"] is not None and detail["game_object_ref"][0] == "0"
                else ""
            ),
            "game_object_name": go_name,
            "metadata_path": row.get("export_path", ""),
            "unresolved_dependency_count": len(dependency_errors),
        },
    }


def scan_assetripper_yaml(assets_root: Path, output_csv: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals = Counter()
    for path in assets_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".prefab", ".unity", ".asset"}:
            continue
        try:
            file_size = path.stat().st_size
            counts = Counter()
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    match = VFX_YAML_RE.match(line)
                    if match is not None:
                        counts[match.group(1)] += 1
        except OSError:
            continue
        if not counts:
            continue
        row = {
            "relative_path": path.relative_to(assets_root).as_posix(),
            "file_type": path.suffix.lower(),
            "particle_system_count": counts["198"],
            "particle_system_renderer_count": counts["199"],
            "trail_renderer_count": counts["96"],
            "line_renderer_count": counts["120"],
            "vfx_component_count": sum(counts.values()),
            "file_size": file_size,
        }
        rows.append(row)
        totals.update(counts)
    rows.sort(key=lambda row: str(row["relative_path"]).lower())
    write_csv(output_csv, list(rows[0].keys()) if rows else ["relative_path"], rows)
    return {
        "yaml_file_count": len(rows),
        "particle_yaml_file_count": sum(int(row["particle_system_count"]) > 0 for row in rows),
        "particle_system_renderer_yaml_file_count": sum(
            int(row["particle_system_renderer_count"]) > 0 for row in rows
        ),
        "particle_system_count": totals["198"],
        "particle_system_renderer_count": totals["199"],
        "trail_renderer_count": totals["96"],
        "line_renderer_count": totals["120"],
        "component_total": sum(totals.values()),
    }


def main() -> int:
    args = parse_args()
    study_root = args.study_root.resolve(strict=False)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    resolved_paths = resolve_full_paths(study_root, config)
    unitypy_root = resolved_paths["unitypy_root"]
    index_path = resolved_paths["index_path"]
    structure_dir = resolved_paths["structure_dir"]
    preview_dir = resolved_paths["preview_dir"]
    classified_root = resolved_paths["classified_root"]
    assets_root = resolved_paths["assets_root"]
    structure_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    classified_root.mkdir(parents=True, exist_ok=True)

    components, resources = scan_rows(index_path)
    details, go_keys = collect_component_details(components, unitypy_root)
    game_objects = collect_game_objects(index_path, go_keys)
    hydrate_game_objects(game_objects, unitypy_root)
    pair_map = make_pair_map(details)
    component_rows: dict[Any, Any] = {
        (
            detail["row"].get("source_file", ""),
            detail["row"].get("asset_type", ""),
            str(detail["row"].get("path_id", "")),
        ): detail["row"]
        for detail in details
    }
    component_rows["_detail_lookup"] = {
        (
            detail["row"].get("source_file", ""),
            detail["row"].get("asset_type", ""),
            str(detail["row"].get("path_id", "")),
        ): detail
        for detail in details
    }

    manifest_items: list[dict[str, Any]] = []
    structure_rows: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    dependency_audit_rows: list[dict[str, Any]] = []
    type_counts = Counter()
    source_type_counts = Counter()
    pair_counts = Counter()

    for detail in details:
        row = detail["row"]
        source = row.get("source_file", "")
        asset_type = row.get("asset_type", "")
        path_id = str(row.get("path_id", ""))
        type_counts[asset_type] += 1
        source_type_counts[(source, asset_type)] += 1
        go_ref = detail["game_object_ref"]
        go_id = go_ref[1] if go_ref and go_ref[0] == "0" else ""
        game_object = game_objects.get((source, go_id))
        pairs = pair_map.get((source, go_id), {})
        dependencies, dependency_errors, dependency_audit = dependency_state(
            detail,
            pair_map,
            component_rows,
            resources,
            unitypy_root,
        )
        all_errors.extend(dependency_errors)
        dependency_audit_rows.extend(dependency_audit)
        if asset_type == "ParticleSystem" and pairs.get("ParticleSystemRenderer"):
            pair_counts["particle_system_with_renderer"] += 1
        if asset_type == "ParticleSystem" and len(pairs.get("ParticleSystemRenderer", [])) == 1:
            pair_counts["particle_system_with_exactly_one_renderer"] += 1
        if asset_type in ROOT_TYPES:
            manifest_items.append(
                build_manifest_item(detail, game_object, dependencies, dependency_errors, config)
            )

        metadata_path = detail["metadata_path"]
        structure_rows.append(
            {
                "source_file": source,
                "component_type": asset_type,
                "component_path_id": path_id,
                "asset_id": row.get("asset_id", ""),
                "asset_name": row.get("asset_name", ""),
                "metadata_path": row.get("export_path", ""),
                "metadata_exists": detail["metadata_exists"],
                "game_object_file_id": go_ref[0] if go_ref else "",
                "game_object_path_id": go_id,
                "game_object_name": str((game_object or {}).get("name", "")),
                "game_object_metadata_path": str(
                    ((game_object or {}).get("row") or {}).get("export_path", "")
                ),
                "paired_particle_system_path_ids": "|".join(pairs.get("ParticleSystem", [])),
                "paired_renderer_path_ids": "|".join(pairs.get("ParticleSystemRenderer", [])),
                "paired_trail_renderer_path_ids": "|".join(pairs.get("TrailRenderer", [])),
                "paired_line_renderer_path_ids": "|".join(pairs.get("LineRenderer", [])),
                "material_refs": json.dumps(detail["material_refs"], ensure_ascii=False),
                "resolved_material_paths": "|".join(
                    str(value.get("export_path", "")) for value in dependencies["materials"]
                ),
                "texture_refs": "|".join(
                    f"{value.get('source_file', '')}:{value.get('path_id', '')}"
                    for value in dependencies["textures"]
                ),
                "resolved_texture_paths": "|".join(
                    str(value.get("export_path", "")) for value in dependencies["textures"]
                ),
                "mesh_refs": json.dumps(detail["mesh_refs"], ensure_ascii=False),
                "resolved_mesh_paths": "|".join(
                    str(value.get("export_path", "")) for value in dependencies["meshes"]
                ),
                "unresolved_dependency_count": len(dependency_errors),
                "trace_status": (
                    "complete"
                    if detail["metadata_exists"] and go_id and game_object
                    else "partial"
                ),
            }
        )
        classification_config(detail, game_object, pair_map, classified_root)

    structure_csv = structure_dir / "VFX组件全量结构索引.csv"
    write_csv(structure_csv, TARGET_CSV_FIELDS, structure_rows)

    source_count_csv = structure_dir / "VFX组件按源文件对账.csv"
    write_csv(
        source_count_csv,
        ["source_file", "component_type", "count"],
        (
            {"source_file": source, "component_type": asset_type, "count": count}
            for (source, asset_type), count in sorted(source_type_counts.items())
        ),
    )
    dependency_audit_csv = structure_dir / "VFX依赖引用审计.csv"
    write_csv(dependency_audit_csv, DEPENDENCY_AUDIT_FIELDS, dependency_audit_rows)

    copied_textures = copy_keyword_textures(resources, unitypy_root, classified_root, config)
    write_json(structure_dir / "VFX粒子贴图分类索引.json", copied_textures)
    write_jsonl(structure_dir / "VFX未解析依赖.jsonl", all_errors)
    write_jsonl(preview_dir / "errors.jsonl", all_errors)

    existing_manifest_path = preview_dir / "manifest.json"
    existing_manifest = load_json(existing_manifest_path)
    reused_reconstruction_state_count = merge_existing_reconstruction_state(
        existing_manifest_path, preview_dir, manifest_items
    )
    manifest = {
        "schema_version": config["manifest"]["schema_version"],
        "engine": "unity",
        "study_root": str(study_root),
        "source_index": str(index_path),
        "item_count": len(manifest_items),
        "items": manifest_items,
        "full_structure_index": str(structure_csv),
        "full_component_count": len(structure_rows),
        "reused_reconstruction_state_count": reused_reconstruction_state_count,
    }
    if "original_vfx_prefab_export" in existing_manifest:
        manifest["original_vfx_prefab_export"] = existing_manifest["original_vfx_prefab_export"]
    write_json(preview_dir / "manifest.json", manifest)

    assetripper_summary = scan_assetripper_yaml(
        assets_root,
        structure_dir / "AssetRipper_VFX_YAML全量对账.csv",
    )
    source_before = sum(1 for path in args.source_root.rglob("*") if path.is_file())
    source_bytes = sum(path.stat().st_size for path in args.source_root.rglob("*") if path.is_file())
    component_keys = {
        (str(row.get("source_file", "")), str(row.get("asset_type", "")), str(row.get("path_id", "")))
        for row in components
    }
    structure_keys = {
        (
            str(row.get("source_file", "")),
            str(row.get("component_type", "")),
            str(row.get("component_path_id", "")),
        )
        for row in structure_rows
    }
    expected_root_keys = {
        (str(row.get("source_file", "")), str(row.get("asset_type", "")), str(row.get("path_id", "")))
        for row in components
        if row.get("asset_type") in ROOT_TYPES
    }
    manifest_root_keys = {
        (str(item.get("source_file", "")), str(item.get("root_type", "")), str(item.get("path_id", "")))
        for item in manifest_items
    }
    dependency_state_counts = Counter(
        (str(row["relation"]), str(row["reference_state"])) for row in dependency_audit_rows
    )
    summary = {
        "schema_version": 1,
        "source_index": str(index_path),
        "component_counts": dict(sorted(type_counts.items())),
        "component_total": len(structure_rows),
        "manifest_root_count": len(manifest_items),
        "reused_reconstruction_state_count": reused_reconstruction_state_count,
        "pairing": dict(pair_counts),
        "game_object_key_count": len(go_keys),
        "resolved_game_object_count": len(game_objects),
        "unresolved_dependency_record_count": len(all_errors),
        "dependency_reference_audit": {
            "record_count": len(dependency_audit_rows),
            "state_counts": {
                f"{relation}:{state}": count
                for (relation, state), count in sorted(dependency_state_counts.items())
            },
            "csv": str(dependency_audit_csv),
        },
        "exact_set_reconciliation": {
            "component_index_count": len(component_keys),
            "structure_index_count": len(structure_keys),
            "component_duplicate_count": len(components) - len(component_keys),
            "structure_duplicate_count": len(structure_rows) - len(structure_keys),
            "component_missing_from_structure_count": len(component_keys - structure_keys),
            "structure_extra_vs_component_count": len(structure_keys - component_keys),
            "expected_manifest_root_count": len(expected_root_keys),
            "actual_manifest_root_count": len(manifest_root_keys),
            "manifest_root_duplicate_count": len(manifest_items) - len(manifest_root_keys),
            "manifest_root_missing_count": len(expected_root_keys - manifest_root_keys),
            "manifest_root_extra_count": len(manifest_root_keys - expected_root_keys),
            "ok": (
                len(components) == len(component_keys)
                and component_keys == structure_keys
                and len(manifest_items) == len(manifest_root_keys)
                and expected_root_keys == manifest_root_keys
            ),
        },
        "classified_texture_count": sum(
            1 for item in copied_textures if item.get("publication_status") == "published"
        ),
        "keyword_texture_candidate_count": len(copied_textures),
        "assetripper_yaml": assetripper_summary,
        "source_readonly_observation": {
            "file_count": source_before,
            "total_bytes": source_bytes,
        },
        "facts": [
            "全量组件计数来自 UnityPy all_resources_index.csv。",
            "ParticleSystemRenderer 通过同一 source_file + m_GameObject.m_PathID 与 ParticleSystem 配对。",
            "AssetRipper YAML 组件计数来自 class id 198/199/96/120。",
        ],
        "judgments": [
            "同 GameObject 配对是当前最可靠的静态 ParticleSystem/Renderer 结构关系。",
            "m_FileID 非 0 的跨 serialized-file 依赖在缺少 external reference 表时保持未解析，不做猜测。",
        ],
        "hypotheses": [
            "部分跨文件材质/贴图依赖可在 Unity 编辑器导入 OriginalAssets 后由 GUID 进一步补齐。",
        ],
    }
    write_json(structure_dir / "VFX全量结构摘要.json", summary)
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Export Unity Mesh objects to Blender-importable glTF folders.

UnityPy can expose Unity Mesh objects and export them as OBJ text. This script
uses that OBJ text in memory only, converts it into a minimal glTF 2.0 + BIN
pair per mesh, then places the result under the GameAnalysis study-root model
directory convention. OBJ files are not written to the study-root.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import re
import shutil
import struct
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

import UnityPy


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp"}
UNITY_BUNDLE_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")
DEFAULT_MATERIAL_CONFIG = Path(__file__).resolve().parents[1] / "references" / "unity-material-binding-config.json"
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def safe_relative_subdir(value: str, *, allow_empty: bool) -> Path:
    """Validate a canonical portable relative subpath before joining a fixed root."""
    text = str(value or "")
    if not text:
        if allow_empty:
            return Path()
        raise ValueError("relative subdir must not be empty")
    if text != text.strip() or "\\" in text or "\x00" in text:
        raise ValueError(f"unsafe relative subdir: {value!r}")
    posix = PurePosixPath(text)
    if posix.is_absolute() or PureWindowsPath(text).is_absolute():
        raise ValueError(f"relative subdir must not be absolute: {value!r}")
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"relative subdir contains unsafe segment: {value!r}")
    for part in parts:
        if ":" in part or part.endswith((".", " ")):
            raise ValueError(f"relative subdir contains Windows-unsafe segment: {value!r}")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"relative subdir contains Windows reserved name: {value!r}")
    return Path(*parts)


def write_bytes_if_changed(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return "reused"
    disposition = "updated" if path.exists() else "created"
    path.write_bytes(payload)
    return disposition


def write_text_if_changed(path: Path, payload: str) -> str:
    return write_bytes_if_changed(path, payload.encode("utf-8"))


def write_json_if_changed(path: Path, payload: object) -> str:
    return write_text_if_changed(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def safe_name(value: str, fallback: str = "model") -> str:
    value = value.strip() if value else ""
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._ ")
    return value[:120] or fallback


def allocate_unique_output_name(
    base_name: str,
    used_names: set[str],
    suffix_counters: dict[str, int],
) -> str:
    """Return a deterministic, case-insensitively unique output name."""
    base_key = base_name.casefold()
    if base_key not in used_names:
        used_names.add(base_key)
        suffix_counters.setdefault(base_key, 2)
        return base_name

    suffix = suffix_counters.get(base_key, 2)
    while True:
        candidate = f"{base_name}_{suffix:03d}"
        suffix += 1
        candidate_key = candidate.casefold()
        if candidate_key in used_names:
            continue
        used_names.add(candidate_key)
        suffix_counters[base_key] = suffix
        return candidate


def load_material_binding_config(path: Path | None = None) -> dict[str, object]:
    config_path = path or DEFAULT_MATERIAL_CONFIG
    if not config_path.is_file():
        return {
            "texture_property_priority": ["_MainTex"],
            "candidate_textures": {"max_per_model": 6, "include_keywords": [], "exclude_keywords": []},
            "binding_sources": {
                "configured_property": "unity_material_configured_property",
                "same_name_candidate": "same_name_candidate",
                "none": "none",
            },
        }
    return json.loads(config_path.read_text(encoding="utf-8"))


def has_unity_bundle_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(8).startswith(UNITY_BUNDLE_MAGICS)
    except OSError:
        return False


def iter_unity_sources(source_root: Path) -> list[Path]:
    candidates: list[Path] = []
    data_dirs = list(source_root.glob("*_Data"))
    search_roots = data_dirs if data_dirs else [source_root]
    names = {
        "globalgamemanagers",
        "globalgamemanagers.assets",
        "resources.assets",
        "resources.resource",
        "unity default resources",
        "unity_builtin_extra",
    }
    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if (
                path.name.casefold() in names
                or suffix in {".assets", ".ress", ".resource"}
                or re.match(r"level\d+$", path.name)
                or has_unity_bundle_magic(path)
            ):
                candidates.append(path)
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return sorted(result, key=lambda item: item.relative_to(source_root).as_posix().casefold())


def load_expected_mesh_entries(index_path: Path) -> dict[tuple[str, int], dict[str, str]]:
    """Load the exact Mesh set declared by a UnityPy all-resources index."""
    entries: dict[tuple[str, int], dict[str, str]] = {}
    with Path(index_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"source_file", "path_id", "asset_type", "asset_name", "container_path"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"expected index missing fields: {', '.join(sorted(missing))}")
        for row_number, raw_row in enumerate(reader, start=2):
            row = {str(key): str(value or "") for key, value in raw_row.items()}
            if row["asset_type"] != "Mesh":
                continue
            source_label = safe_relative_subdir(row["source_file"], allow_empty=False).as_posix()
            try:
                path_id = int(row["path_id"])
            except ValueError as exc:
                raise ValueError(f"invalid Mesh path_id at row {row_number}: {row['path_id']!r}") from exc
            key = (source_label, path_id)
            if key in entries:
                raise ValueError(f"duplicate expected Mesh key at row {row_number}: {key!r}")
            entries[key] = row
    return dict(sorted(entries.items(), key=lambda item: (item[0][0].casefold(), item[0][1])))


def load_unique_asset_names(index_path: Path, asset_type: str) -> dict[int, str]:
    candidates: dict[int, set[str]] = {}
    with Path(index_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"path_id", "asset_type", "asset_name"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"asset-name index missing fields: {', '.join(sorted(missing))}")
        for row in reader:
            if row.get("asset_type") != asset_type:
                continue
            try:
                path_id = int(row.get("path_id", ""))
            except ValueError:
                continue
            name = str(row.get("asset_name") or "")
            if name:
                candidates.setdefault(path_id, set()).add(name)
    return {path_id: next(iter(names)) for path_id, names in candidates.items() if len(names) == 1}


def material_binding_from_type_tree(
    type_tree: dict[str, object],
    config: dict[str, object],
) -> tuple[int | None, str]:
    priority = [str(value) for value in config.get("texture_property_priority", ["_MainTex"])]
    saved = type_tree.get("m_SavedProperties", {})
    tex_envs = saved.get("m_TexEnvs", []) if isinstance(saved, dict) else []
    values: dict[str, int] = {}
    for entry in tex_envs if isinstance(tex_envs, list) else []:
        if not isinstance(entry, list) or len(entry) < 2 or not isinstance(entry[1], dict):
            continue
        texture = entry[1].get("m_Texture", {})
        if not isinstance(texture, dict):
            continue
        try:
            path_id = int(texture.get("m_PathID", 0))
        except (TypeError, ValueError):
            continue
        if path_id:
            values[str(entry[0])] = path_id
    for property_name in priority:
        if property_name in values:
            return values[property_name], property_name
    return (next(iter(values.items()))[1], next(iter(values.items()))[0]) if values else (None, "")


def load_external_material_bindings(
    index_path: Path,
    texture_names: dict[int, str],
    config: dict[str, object],
) -> dict[int, list[dict[str, object]]]:
    result: dict[int, list[dict[str, object]]] = {}
    unitypy_root = Path(index_path).parent
    with Path(index_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"source_file", "path_id", "asset_type", "asset_name", "container_path", "export_path", "status"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"material evidence index missing fields: {', '.join(sorted(missing))}")
        for row in reader:
            if row.get("asset_type") != "Material" or row.get("status") != "exported":
                continue
            try:
                material_path_id = int(row.get("path_id", ""))
            except ValueError:
                continue
            metadata_paths = [
                unitypy_root / Path(*PurePosixPath(value).parts)
                for value in str(row.get("export_path") or "").split(";")
                if value and value.endswith(".json")
            ]
            metadata_path = next((path for path in metadata_paths if path.is_file()), None)
            if metadata_path is None:
                continue
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            type_tree = payload.get("type_tree", {}) if isinstance(payload, dict) else {}
            if not isinstance(type_tree, dict):
                continue
            texture_path_id, texture_property = material_binding_from_type_tree(type_tree, config)
            texture_name = texture_names.get(texture_path_id) if texture_path_id is not None else None
            if texture_path_id is None or not texture_name:
                continue
            record = {
                "material_path_id": material_path_id,
                "material_name": str(row.get("asset_name") or ""),
                "material_source_file": str(row.get("source_file") or ""),
                "material_container_path": str(row.get("container_path") or ""),
                "texture_path_id": texture_path_id,
                "texture_name": texture_name,
                "texture_property": texture_property,
            }
            records = result.setdefault(material_path_id, [])
            if record not in records:
                records.append(record)
    return result


def expected_source_paths(source_root: Path, entries: dict[tuple[str, int], dict[str, str]]) -> list[Path]:
    root = source_root.resolve(strict=True)
    paths: list[Path] = []
    for source_label in sorted({key[0] for key in entries}, key=str.casefold):
        candidate = source_root / Path(*PurePosixPath(source_label).parts)
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"expected source escapes source root: {source_label}") from exc
        if not resolved.is_file():
            raise ValueError(f"expected source is not a file: {source_label}")
        paths.append(candidate)
    return paths


def parse_obj(obj_text: str) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[tuple[float, float]], list[list[tuple[int, int | None, int | None]]]]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[list[tuple[int, int | None, int | None]]] = []

    for raw_line in obj_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "vn" and len(parts) >= 4:
            normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == "vt" and len(parts) >= 3:
            uvs.append((float(parts[1]), float(parts[2])))
        elif parts[0] == "f" and len(parts) >= 4:
            face: list[tuple[int, int | None, int | None]] = []
            for token in parts[1:]:
                chunks = token.split("/")
                vi = int(chunks[0])
                ti = int(chunks[1]) if len(chunks) > 1 and chunks[1] else None
                ni = int(chunks[2]) if len(chunks) > 2 and chunks[2] else None
                face.append((vi - 1, ti - 1 if ti else None, ni - 1 if ni else None))
            for i in range(1, len(face) - 1):
                faces.append([face[0], face[i], face[i + 1]])
    return positions, normals, uvs, faces


def aligned_extend(buffer: bytearray, data: bytes) -> tuple[int, int]:
    while len(buffer) % 4:
        buffer.append(0)
    offset = len(buffer)
    buffer.extend(data)
    return offset, len(data)


def pack_floats(values: Iterable[Iterable[float]]) -> bytes:
    flat: list[float] = []
    for row in values:
        flat.extend(float(x) for x in row)
    return struct.pack("<" + "f" * len(flat), *flat) if flat else b""


def pack_uints(values: Iterable[int]) -> bytes:
    vals = [int(v) for v in values]
    return struct.pack("<" + "I" * len(vals), *vals) if vals else b""


def obj_to_gltf(obj_text: str, model_name: str, gltf_path: Path, material_texture_uri: str | None = None) -> dict[str, object]:
    positions, normals, uvs, faces = parse_obj(obj_text)
    if not positions or not faces:
        raise ValueError("OBJ has no positions or faces")

    vertex_map: dict[tuple[int, int | None, int | None], int] = {}
    out_positions: list[tuple[float, float, float]] = []
    out_normals: list[tuple[float, float, float]] = []
    out_uvs: list[tuple[float, float]] = []
    indices: list[int] = []
    has_normals = bool(normals)
    has_uvs = bool(uvs)

    for face in faces:
        for key in face:
            if key not in vertex_map:
                vertex_map[key] = len(out_positions)
                vi, ti, ni = key
                out_positions.append(positions[vi])
                if has_normals:
                    out_normals.append(normals[ni] if ni is not None and 0 <= ni < len(normals) else (0.0, 1.0, 0.0))
                if has_uvs:
                    out_uvs.append(uvs[ti] if ti is not None and 0 <= ti < len(uvs) else (0.0, 0.0))
            indices.append(vertex_map[key])

    buffer = bytearray()
    buffer_views: list[dict[str, object]] = []
    accessors: list[dict[str, object]] = []

    def add_accessor(data: bytes, component_type: int, count: int, type_name: str, target: int | None = None, minmax: tuple[list[float], list[float]] | None = None) -> int:
        offset, length = aligned_extend(buffer, data)
        view: dict[str, object] = {"buffer": 0, "byteOffset": offset, "byteLength": length}
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        accessor: dict[str, object] = {"bufferView": len(buffer_views) - 1, "componentType": component_type, "count": count, "type": type_name}
        if minmax is not None:
            accessor["min"] = minmax[0]
            accessor["max"] = minmax[1]
        accessors.append(accessor)
        return len(accessors) - 1

    mins = [min(row[i] for row in out_positions) for i in range(3)]
    maxs = [max(row[i] for row in out_positions) for i in range(3)]
    pos_accessor = add_accessor(pack_floats(out_positions), 5126, len(out_positions), "VEC3", 34962, (mins, maxs))
    attributes: dict[str, int] = {"POSITION": pos_accessor}
    if has_normals:
        attributes["NORMAL"] = add_accessor(pack_floats(out_normals), 5126, len(out_normals), "VEC3", 34962)
    if has_uvs:
        attributes["TEXCOORD_0"] = add_accessor(pack_floats(out_uvs), 5126, len(out_uvs), "VEC2", 34962)
    index_accessor = add_accessor(pack_uints(indices), 5125, len(indices), "SCALAR", 34963)

    bin_path = gltf_path.with_suffix(".bin")
    bin_write = write_bytes_if_changed(bin_path, bytes(buffer))
    primitive: dict[str, object] = {"attributes": attributes, "indices": index_accessor, "mode": 4}
    gltf: dict[str, object] = {
        "asset": {"version": "2.0", "generator": "GameAnalysis UnityPy Mesh glTF exporter"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": model_name}],
        "meshes": [{"name": model_name, "primitives": [primitive]}],
        "buffers": [{"uri": bin_path.name, "byteLength": len(buffer)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    if material_texture_uri:
        gltf["images"] = [{"uri": material_texture_uri}]
        gltf["textures"] = [{"source": 0}]
        gltf["materials"] = [
            {
                "name": f"{model_name}_material",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                },
            }
        ]
        primitive["material"] = 0
    gltf_write = write_text_if_changed(gltf_path, json.dumps(gltf, ensure_ascii=False, indent=2))
    return {
        "vertices": len(out_positions),
        "triangles": len(indices) // 3,
        "gltf_write": gltf_write,
        "bin_write": bin_write,
    }


def pointer_path_id(value: object) -> int | None:
    path_id = getattr(value, "path_id", None)
    if isinstance(path_id, int):
        return path_id
    path_id = getattr(value, "m_PathID", None)
    if isinstance(path_id, int):
        return path_id
    return None


def object_name(data: object) -> str:
    value = getattr(data, "m_Name", "")
    return value if isinstance(value, str) else ""


def material_texture_path_id(material_data: object, config: dict[str, object] | None = None) -> tuple[int | None, str]:
    config = config or load_material_binding_config()
    priority = [str(item) for item in config.get("texture_property_priority", ["_MainTex"])]  # type: ignore[union-attr]
    saved = getattr(material_data, "m_SavedProperties", None)
    tex_envs = getattr(saved, "m_TexEnvs", []) if saved is not None else []
    values_by_key: dict[str, int] = {}
    fallback: tuple[int, str] | None = None
    for entry in tex_envs:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            key, value = entry[0], entry[1]
        else:
            key = getattr(entry, "first", getattr(entry, "key", ""))
            value = getattr(entry, "second", getattr(entry, "value", None))
        texture = getattr(value, "m_Texture", None) if value is not None else None
        texture_path_id = pointer_path_id(texture)
        if texture_path_id is None:
            continue
        key_text = str(key)
        values_by_key[key_text] = texture_path_id
        if fallback is None:
            fallback = (texture_path_id, key_text)
    for key in priority:
        if key in values_by_key:
            return values_by_key[key], key
    if fallback is not None:
        return fallback
    return None, ""


def material_main_texture_path_id(material_data: object) -> int | None:
    texture_path_id, _key = material_texture_path_id(material_data, {"texture_property_priority": ["_MainTex"]})
    return texture_path_id


def build_mesh_texture_binding_map(
    env: object,
    config: dict[str, object] | None = None,
    *,
    external_texture_names: dict[int, str] | None = None,
    external_material_bindings: dict[int, list[dict[str, object]]] | None = None,
) -> dict[int, dict[str, object]]:
    external_texture_names = external_texture_names or {}
    external_material_bindings = external_material_bindings or {}
    objects_by_path_id: dict[int, object] = {}
    texture_name_by_path_id: dict[int, str] = {}
    for obj in getattr(env, "objects", []):
        path_id = getattr(obj, "path_id", None)
        if isinstance(path_id, int):
            objects_by_path_id[path_id] = obj
        if getattr(getattr(obj, "type", None), "name", "") == "Texture2D":
            try:
                name = object_name(obj.read())
            except Exception:
                name = ""
            if isinstance(path_id, int) and name:
                texture_name_by_path_id[path_id] = name

    mesh_by_game_object: dict[int, int] = {}
    for obj in getattr(env, "objects", []):
        if getattr(getattr(obj, "type", None), "name", "") != "MeshFilter":
            continue
        try:
            mesh_filter = obj.read()
        except Exception:
            continue
        game_object_path_id = pointer_path_id(getattr(mesh_filter, "m_GameObject", None))
        mesh_path_id = pointer_path_id(getattr(mesh_filter, "m_Mesh", None))
        if game_object_path_id is not None and mesh_path_id is not None:
            mesh_by_game_object.setdefault(game_object_path_id, mesh_path_id)

    candidates_by_mesh: dict[int, list[dict[str, object]]] = {}
    renderers = sorted(
        getattr(env, "objects", []),
        key=lambda item: int(getattr(item, "path_id", 0) or 0),
    )
    for obj in renderers:
        if getattr(getattr(obj, "type", None), "name", "") not in {"MeshRenderer", "SkinnedMeshRenderer"}:
            continue
        try:
            renderer = obj.read()
        except Exception:
            continue
        mesh_path_id = pointer_path_id(getattr(renderer, "m_Mesh", None))
        if mesh_path_id is None:
            game_object_path_id = pointer_path_id(getattr(renderer, "m_GameObject", None))
            mesh_path_id = mesh_by_game_object.get(game_object_path_id) if game_object_path_id is not None else None
        if mesh_path_id is None:
            continue
        for material_ptr in getattr(renderer, "m_Materials", []) or []:
            material_path_id = pointer_path_id(material_ptr)
            material_obj = objects_by_path_id.get(material_path_id) if material_path_id is not None else None
            if material_obj is None:
                if material_path_id is not None:
                    for external in external_material_bindings.get(material_path_id, []):
                        record = dict(external)
                        record["renderer_path_id"] = getattr(obj, "path_id", None)
                        records = candidates_by_mesh.setdefault(mesh_path_id, [])
                        signature = tuple(record.get(key) for key in ("texture_path_id", "texture_property", "renderer_path_id", "material_path_id"))
                        if all(
                            tuple(existing.get(key) for key in ("texture_path_id", "texture_property", "renderer_path_id", "material_path_id")) != signature
                            for existing in records
                        ):
                            records.append(record)
                continue
            try:
                texture_path_id, property_name = material_texture_path_id(material_obj.read(), config)
            except Exception:
                continue
            texture_name = (
                texture_name_by_path_id.get(texture_path_id) or external_texture_names.get(texture_path_id)
                if texture_path_id is not None
                else None
            )
            if texture_name:
                record = {
                    "texture_name": texture_name,
                    "texture_path_id": texture_path_id,
                    "texture_property": property_name,
                    "renderer_path_id": getattr(obj, "path_id", None),
                    "material_path_id": material_path_id,
                }
                records = candidates_by_mesh.setdefault(mesh_path_id, [])
                signature = tuple(record[key] for key in ("texture_path_id", "texture_property", "renderer_path_id", "material_path_id"))
                if all(
                    tuple(existing[key] for key in ("texture_path_id", "texture_property", "renderer_path_id", "material_path_id")) != signature
                    for existing in records
                ):
                    records.append(record)

    result: dict[int, dict[str, object]] = {}
    for mesh_path_id, records in candidates_by_mesh.items():
        chosen = dict(records[0])
        chosen["binding_count"] = len(records)
        chosen["ambiguous"] = len({(record["texture_path_id"], record["texture_property"]) for record in records}) > 1
        chosen["bindings"] = records
        result[mesh_path_id] = chosen
    return result


def build_mesh_texture_name_map(env: object, config: dict[str, object] | None = None) -> dict[int, str]:
    return {
        mesh_path_id: str(binding["texture_name"])
        for mesh_path_id, binding in build_mesh_texture_binding_map(env, config).items()
    }


def texture_candidates(
    texture_root: Path,
    mesh_name: str,
    limit: int,
    config: dict[str, object] | None = None,
) -> list[Path]:
    if not texture_root.exists():
        return []
    config = config or load_material_binding_config()
    candidate_config = config.get("candidate_textures", {}) if isinstance(config, dict) else {}
    include_keywords = (
        [str(item).lower() for item in candidate_config.get("include_keywords", [])]
        if isinstance(candidate_config, dict)
        else []
    )
    exclude_keywords = (
        [str(item).lower() for item in candidate_config.get("exclude_keywords", [])]
        if isinstance(candidate_config, dict)
        else []
    )
    tokens = [token.lower() for token in re.split(r"[_\W]+", mesh_name) if len(token) >= 4]
    if not tokens:
        return []
    scored: list[tuple[int, Path]] = []
    for path in texture_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        stem = path.stem.lower()
        if any(keyword and keyword in stem for keyword in exclude_keywords):
            continue
        score = sum(1 for token in tokens if token in stem)
        if any(keyword and keyword in stem for keyword in include_keywords):
            score += 1
        if score:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], len(item[1].name), str(item[1]).lower()))
    return [path for _score, path in scored[:limit]]


def find_texture_by_asset_name(texture_root: Path, texture_name: str) -> Path | None:
    if not texture_root.exists() or not texture_name:
        return None
    target = texture_name.lower()
    matches: list[Path] = []
    for path in texture_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if path.stem.lower() == target:
            matches.append(path)
    if not matches:
        return None
    matches.sort(key=lambda path: (len(path.parts), len(path.name), str(path).lower()))
    return matches[0]


def copy_bound_texture(texture_root: Path, model_dir: Path, texture_name: str) -> str | None:
    source = find_texture_by_asset_name(texture_root, texture_name)
    if source is None:
        return None
    dest_dir = model_dir / "textures"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    if dest.exists():
        if not dest.is_file() or dest.read_bytes() != source.read_bytes():
            raise FileExistsError(f"bound texture target exists with different content: {dest}")
    else:
        shutil.copy2(source, dest)
    return dest.relative_to(model_dir).as_posix()


def copy_texture_candidates_with_uri(
    texture_root: Path,
    model_dir: Path,
    mesh_name: str,
    limit: int,
    config: dict[str, object] | None = None,
) -> tuple[int, str | None]:
    candidates = texture_candidates(texture_root, mesh_name, limit, config)
    if not candidates:
        return 0, None
    dest_dir = model_dir / "textures" / "待验证_同名前缀"
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    material_texture_uri: str | None = None
    for path in candidates:
        dest = dest_dir / path.name
        if dest.exists():
            if not dest.is_file() or dest.read_bytes() != path.read_bytes():
                raise FileExistsError(f"candidate texture target exists with different content: {dest}")
        else:
            shutil.copy2(path, dest)
        copied += 1
        if material_texture_uri is None:
            material_texture_uri = dest.relative_to(model_dir).as_posix()
    return copied, material_texture_uri


def copy_texture_candidates(texture_root: Path, model_dir: Path, mesh_name: str, limit: int) -> int:
    copied, _material_texture_uri = copy_texture_candidates_with_uri(texture_root, model_dir, mesh_name, limit)
    return copied


def classify_mesh_export_result(value: object) -> tuple[str, str]:
    if isinstance(value, str) and value.strip():
        return "exportable", ""
    if value is False or (isinstance(value, str) and not value.strip()):
        return (
            "skipped_no_geometry",
            "UnityPy MeshExporter returned no decoded vertex geometry; no glTF can be generated.",
        )
    return (
        "failed",
        f"UnityPy MeshExporter returned unexpected {type(value).__name__}.",
    )


def is_runtime_generated_mesh_placeholder(mesh: object) -> bool:
    name = str(getattr(mesh, "m_Name", "") or "")
    submeshes = getattr(mesh, "m_SubMeshes", None)
    return name.startswith("Skeleton Prefab Mesh") and not submeshes


def write_model_sidecars(
    *,
    model_dir: Path,
    source_label: str,
    path_id: int,
    mesh_name: str,
    output_name: str,
    container_path: str,
    gltf_relative_path: str,
    stats: dict[str, object],
    binding: dict[str, object] | None,
    material_texture_uri: str,
    material_binding_source: str,
) -> dict[str, str]:
    binding = binding or {}
    texture_uri = material_texture_uri or ""
    metadata = {
        "schema_version": 1,
        "original_source_file": source_label,
        "original_source_format": "UnityPy Mesh",
        "source_model": mesh_name,
        "output_name": output_name,
        "path_id": path_id,
        "container_path": container_path,
        "gltf_path": gltf_relative_path,
        "vertex_count": stats.get("vertices", 0),
        "face_count": stats.get("triangles", 0),
        "material_binding_source": material_binding_source or "none",
        "material_texture_uri": texture_uri,
        "configured_texture_name": binding.get("texture_name", ""),
        "configured_texture_property": binding.get("texture_property", ""),
        "configured_binding_count": binding.get("binding_count", 0),
        "configured_binding_ambiguous": bool(binding.get("ambiguous", False)),
    }
    export_log = {
        "schema_version": 1,
        "status": "exported",
        "steps": ["UnityPy Mesh.export('obj') in memory", "OBJ geometry converted to glTF 2.0 + external BIN"],
        "original_source_file": source_label,
        "path_id": path_id,
        "material_binding_source": material_binding_source or "none",
    }
    dependencies = {
        "schema_version": 1,
        "buffers": [{"uri": f"{output_name}.bin"}],
        "textures": ([{"uri": texture_uri, "binding_source": material_binding_source}] if texture_uri else []),
        "configured_material_bindings": binding.get("bindings", []),
        "skins": [],
        "animations": [],
    }
    missing: list[dict[str, str]] = []
    if binding.get("texture_name") and not texture_uri:
        missing.append({"kind": "texture", "name": str(binding["texture_name"]), "reason": "configured_texture_not_found_in_classified_images"})
    elif not texture_uri:
        missing.append({"kind": "material_texture", "name": "", "reason": "no_renderer_material_texture_or_same_name_candidate"})
    missing_resources = {"schema_version": 1, "items": missing}
    return {
        "metadata.json": write_json_if_changed(model_dir / "metadata.json", metadata),
        "export-log.json": write_json_if_changed(model_dir / "export-log.json", export_log),
        "dependencies.json": write_json_if_changed(model_dir / "dependencies.json", dependencies),
        "missing-resources.json": write_json_if_changed(model_dir / "missing-resources.json", missing_resources),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="User-authorized Unity game root.")
    parser.add_argument("--study-root", required=True, help="GameAnalysis study root.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max mesh count for smoke tests.")
    parser.add_argument("--texture-candidates", type=int, default=6, help="Same-name texture candidates to copy per model.")
    parser.add_argument("--material-config", type=Path, default=None, help="Optional material binding config JSON.")
    parser.add_argument(
        "--model-output-subdir",
        default="",
        help="Optional safe subdirectory below 1.分类资源/模型 (for isolated branches).",
    )
    parser.add_argument(
        "--index-subdir",
        default="UnityPy模型导出",
        help="Safe subdirectory below 4.临时目录/中间索引.",
    )
    parser.add_argument(
        "--expected-index",
        type=Path,
        default=None,
        help="Optional UnityPy all_resources_index.csv defining the exact Mesh set to track.",
    )
    args = parser.parse_args()

    source_root = Path(args.source)
    study_root = Path(args.study_root)
    model_subdir = safe_relative_subdir(args.model_output_subdir, allow_empty=True)
    index_subdir = safe_relative_subdir(args.index_subdir, allow_empty=False)
    model_root = study_root / "1.分类资源" / "模型" / model_subdir
    index_root = study_root / "4.临时目录" / "中间索引" / index_subdir
    texture_root = study_root / "1.分类资源" / "图片"
    material_config = load_material_binding_config(args.material_config)
    candidate_config = material_config.get("candidate_textures", {}) if isinstance(material_config, dict) else {}
    configured_candidate_limit = (
        int(candidate_config.get("max_per_model", args.texture_candidates))
        if isinstance(candidate_config, dict)
        else args.texture_candidates
    )
    candidate_limit = args.texture_candidates if args.texture_candidates != 6 else configured_candidate_limit
    model_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    used_output_names: set[str] = set()
    suffix_counters: dict[str, int] = {}
    exported = 0
    failed = 0
    skipped = 0
    expected_entries = load_expected_mesh_entries(args.expected_index) if args.expected_index else {}
    external_texture_names = load_unique_asset_names(args.expected_index, "Texture2D") if args.expected_index else {}
    external_material_bindings = (
        load_external_material_bindings(args.expected_index, external_texture_names, material_config)
        if args.expected_index
        else {}
    )
    expected_keys = set(expected_entries)
    seen_expected: set[tuple[str, int]] = set()
    sources = expected_source_paths(source_root, expected_entries) if args.expected_index else iter_unity_sources(source_root)
    write_actions = {"created": 0, "reused": 0, "updated": 0}

    for source in sources:
        source_label = source.relative_to(source_root).as_posix()
        try:
            env = UnityPy.load(str(source))
        except Exception as exc:
            source_expected = [key for key in expected_keys if key[0] == source_label]
            if source_expected:
                for key in sorted(source_expected, key=lambda item: item[1]):
                    expected = expected_entries[key]
                    rows.append({
                        "mesh_name": expected.get("asset_name", ""),
                        "source_file": source_label,
                        "path_id": key[1],
                        "container_path": expected.get("container_path", ""),
                        "status": "source_failed",
                        "remarks": f"{type(exc).__name__}: {exc}",
                    })
                    seen_expected.add(key)
                    failed += 1
            else:
                rows.append({"source_file": source_label, "status": "source_failed", "remarks": str(exc)})
            continue
        mesh_texture_binding_by_path_id = build_mesh_texture_binding_map(
            env,
            material_config,
            external_texture_names=external_texture_names,
            external_material_bindings=external_material_bindings,
        )
        for obj in env.objects:
            if obj.type.name != "Mesh":
                continue
            object_key = (source_label, int(obj.path_id))
            if expected_keys and object_key not in expected_keys:
                continue
            seen_expected.add(object_key)
            expected = expected_entries.get(object_key, {})
            mesh = None
            mesh_name = f"pathid_{obj.path_id}"
            try:
                mesh = obj.read()
                mesh_name = safe_name(getattr(mesh, "m_Name", "") or f"pathid_{obj.path_id}", f"pathid_{obj.path_id}")
                unique_name = allocate_unique_output_name(mesh_name, used_output_names, suffix_counters)
                obj_text = mesh.export("obj")
                export_status, export_reason = classify_mesh_export_result(obj_text)
                if export_status != "exportable":
                    if export_status == "failed":
                        failed += 1
                    else:
                        skipped += 1
                    rows.append({
                        "mesh_name": mesh_name,
                        "output_name": unique_name,
                        "source_file": source_label,
                        "path_id": obj.path_id,
                        "container_path": expected.get("container_path", ""),
                        "status": export_status,
                        "remarks": export_reason,
                    })
                    continue
                out_dir = model_root / unique_name
                out_dir.mkdir(parents=True, exist_ok=True)
                gltf_path = out_dir / f"{unique_name}.gltf"
                binding = mesh_texture_binding_by_path_id.get(obj.path_id)
                bound_texture_name = str(binding.get("texture_name", "")) if binding else ""
                material_texture_uri = copy_bound_texture(texture_root, out_dir, bound_texture_name or "")
                configured_binding_source = str(
                    material_config.get("binding_sources", {}).get(
                        "configured_property",
                        "unity_material_configured_property",
                    )
                )
                binding_source = configured_binding_source if material_texture_uri and binding else ""
                copied_textures, candidate_texture_uri = copy_texture_candidates_with_uri(
                    texture_root,
                    out_dir,
                    mesh_name,
                    candidate_limit,
                    material_config,
                )
                if material_texture_uri is None and candidate_texture_uri is not None:
                    material_texture_uri = candidate_texture_uri
                    binding_source = "same_name_candidate"
                stats = obj_to_gltf(obj_text, unique_name, gltf_path, material_texture_uri=material_texture_uri)
                for action_key in ("gltf_write", "bin_write"):
                    disposition = str(stats.get(action_key, ""))
                    if disposition in write_actions:
                        write_actions[disposition] += 1
                if binding_source == configured_binding_source:
                    property_name = str(binding.get("texture_property", "")) if binding else ""
                    remarks = f"glTF generated from UnityPy Mesh data; baseColorTexture bound from Unity material property `{property_name}` -> `{bound_texture_name}`."
                elif binding_source == "same_name_candidate":
                    remarks = "glTF generated from UnityPy Mesh data; baseColorTexture bound from a copied same-name texture candidate and requires verification."
                else:
                    remarks = "glTF generated from UnityPy Mesh data; no material texture or copied texture candidate was available for binding."
                gltf_relative_path = gltf_path.relative_to(study_root).as_posix()
                sidecar_actions = write_model_sidecars(
                    model_dir=out_dir,
                    source_label=source_label,
                    path_id=int(obj.path_id),
                    mesh_name=mesh_name,
                    output_name=unique_name,
                    container_path=expected.get("container_path", ""),
                    gltf_relative_path=gltf_relative_path,
                    stats=stats,
                    binding=binding,
                    material_texture_uri=material_texture_uri or "",
                    material_binding_source=binding_source,
                )
                for disposition in sidecar_actions.values():
                    if disposition in write_actions:
                        write_actions[disposition] += 1
                rows.append({
                    "mesh_name": mesh_name,
                    "output_name": unique_name,
                    "source_file": source_label,
                    "path_id": obj.path_id,
                    "container_path": expected.get("container_path", ""),
                    "gltf_path": gltf_relative_path,
                    "vertices": stats["vertices"],
                    "triangles": stats["triangles"],
                    "texture_candidates_copied": copied_textures,
                    "material_texture_name": bound_texture_name or "",
                    "material_texture_uri": material_texture_uri or "",
                    "material_binding_source": binding_source,
                    "configured_texture_property": binding.get("texture_property", "") if binding else "",
                    "configured_renderer_path_id": binding.get("renderer_path_id", "") if binding else "",
                    "configured_material_path_id": binding.get("material_path_id", "") if binding else "",
                    "configured_texture_path_id": binding.get("texture_path_id", "") if binding else "",
                    "configured_binding_count": binding.get("binding_count", 0) if binding else 0,
                    "configured_binding_ambiguous": bool(binding.get("ambiguous", False)) if binding else False,
                    "status": "exported",
                    "remarks": remarks,
                })
                exported += 1
                if args.limit and exported >= args.limit:
                    break
            except Exception as exc:
                if isinstance(exc, AssertionError) and mesh is not None and is_runtime_generated_mesh_placeholder(mesh):
                    status = "skipped_runtime_placeholder"
                    remarks = (
                        "Runtime-generated Spine Skeleton Prefab Mesh placeholder has no serialized "
                        "submesh geometry; no glTF can be generated."
                    )
                    skipped += 1
                else:
                    status = "failed"
                    remarks = f"{type(exc).__name__}: {exc}"
                    failed += 1
                rows.append({
                    "mesh_name": mesh_name,
                    "source_file": source_label,
                    "path_id": getattr(obj, "path_id", ""),
                    "container_path": expected.get("container_path", ""),
                    "status": status,
                    "remarks": remarks,
                })
        if args.limit and exported >= args.limit:
            break

    for key in sorted(expected_keys.difference(seen_expected), key=lambda item: (item[0].casefold(), item[1])):
        expected = expected_entries[key]
        rows.append({
            "mesh_name": expected.get("asset_name", ""),
            "source_file": key[0],
            "path_id": key[1],
            "container_path": expected.get("container_path", ""),
            "status": "missing_from_source",
            "remarks": "Mesh is declared by the expected UnityPy index but was not found in the loaded source container.",
        })
        failed += 1

    index_path = index_root / "模型导出索引.csv"
    fieldnames = [
            "mesh_name",
            "output_name",
            "source_file",
            "path_id",
            "container_path",
            "gltf_path",
            "vertices",
            "triangles",
            "texture_candidates_copied",
            "material_texture_name",
            "material_texture_uri",
            "material_binding_source",
            "configured_texture_property",
            "configured_renderer_path_id",
            "configured_material_path_id",
            "configured_texture_path_id",
            "configured_binding_count",
            "configured_binding_ambiguous",
            "status",
            "remarks",
    ]
    from io import StringIO

    index_buffer = StringIO(newline="")
    writer = csv.DictWriter(index_buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in fieldnames})
    index_write = write_text_if_changed(index_path, index_buffer.getvalue())

    summary = {
        "source_root": str(source_root),
        "study_root": str(study_root),
        "source_count": len(sources),
        "expected_index": str(args.expected_index.resolve(strict=False)) if args.expected_index else "",
        "expected_mesh_count": len(expected_entries) if args.expected_index else None,
        "tracked_expected_mesh_count": len(seen_expected.intersection(expected_keys)) if args.expected_index else None,
        "missing_expected_mesh_count": len(expected_keys.difference(seen_expected)) if args.expected_index else None,
        "exported": exported,
        "skipped": skipped,
        "failed": failed,
        "index": str(index_path),
        "notes": [
            "Generated glTF + BIN from UnityPy Mesh data.",
            "OBJ is used only as in-memory intermediate text and is not written.",
            "Unity renderer material configured texture properties are bound as glTF baseColorTexture when recoverable.",
            "Texture candidates are copied by same-name prefix and used as fallback candidate baseColorTexture when no Unity material texture is recoverable.",
            "Skinned bones and animation clips are not reconstructed in this fallback route.",
        ],
        "material_config": str((args.material_config or DEFAULT_MATERIAL_CONFIG).resolve(strict=False)),
        "model_output_subdir": model_subdir.as_posix(),
        "index_subdir": index_subdir.as_posix(),
    }
    summary_write = write_json_if_changed(index_root / "模型导出摘要.json", summary)
    runtime = {
        **summary,
        "runtime_write_actions": write_actions,
        "index_write": index_write,
        "summary_write": summary_write,
    }
    print(json.dumps(runtime, ensure_ascii=False))
    if args.expected_index:
        return 0 if failed == 0 and len(seen_expected.intersection(expected_keys)) == len(expected_keys) else 1
    return 0 if exported else 1


if __name__ == "__main__":
    raise SystemExit(main())

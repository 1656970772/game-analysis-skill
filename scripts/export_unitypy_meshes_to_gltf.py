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
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import UnityPy


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp"}


def safe_name(value: str, fallback: str = "model") -> str:
    value = value.strip() if value else ""
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._ ")
    return value[:120] or fallback


def iter_unity_sources(source_root: Path) -> list[Path]:
    candidates: list[Path] = []
    data_dirs = list(source_root.glob("*_Data"))
    search_roots = data_dirs if data_dirs else [source_root]
    names = {
        "globalgamemanagers",
        "globalgamemanagers.assets",
        "resources.assets",
        "resources.resource",
    }
    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if path.name in names or suffix in {".assets", ".ress", ".resource"} or re.match(r"level\d+$", path.name):
                candidates.append(path)
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return result


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


def obj_to_gltf(obj_text: str, model_name: str, gltf_path: Path) -> dict[str, object]:
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
    bin_path.write_bytes(bytes(buffer))
    gltf = {
        "asset": {"version": "2.0", "generator": "GameAnalysis UnityPy Mesh glTF exporter"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": model_name}],
        "meshes": [{"name": model_name, "primitives": [{"attributes": attributes, "indices": index_accessor, "mode": 4}]}],
        "buffers": [{"uri": bin_path.name, "byteLength": len(buffer)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    gltf_path.write_text(json.dumps(gltf, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"vertices": len(out_positions), "triangles": len(indices) // 3}


def texture_candidates(texture_root: Path, mesh_name: str, limit: int) -> list[Path]:
    if not texture_root.exists():
        return []
    tokens = [token.lower() for token in re.split(r"[_\W]+", mesh_name) if len(token) >= 4]
    if not tokens:
        return []
    scored: list[tuple[int, Path]] = []
    for path in texture_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        stem = path.stem.lower()
        score = sum(1 for token in tokens if token in stem)
        if score:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], len(item[1].name), str(item[1]).lower()))
    return [path for _score, path in scored[:limit]]


def copy_texture_candidates(texture_root: Path, model_dir: Path, mesh_name: str, limit: int) -> int:
    candidates = texture_candidates(texture_root, mesh_name, limit)
    if not candidates:
        return 0
    dest_dir = model_dir / "textures" / "待验证_同名前缀"
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in candidates:
        dest = dest_dir / path.name
        if not dest.exists():
            shutil.copy2(path, dest)
        copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="User-authorized Unity game root.")
    parser.add_argument("--study-root", required=True, help="GameAnalysis study root.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max mesh count for smoke tests.")
    parser.add_argument("--texture-candidates", type=int, default=6, help="Same-name texture candidates to copy per model.")
    args = parser.parse_args()

    source_root = Path(args.source)
    study_root = Path(args.study_root)
    model_root = study_root / "1.分类资源" / "模型"
    index_root = study_root / "4.临时目录" / "中间索引" / "UnityPy模型导出"
    texture_root = study_root / "1.分类资源" / "图片"
    model_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    name_counts: defaultdict[str, int] = defaultdict(int)
    exported = 0
    failed = 0
    sources = iter_unity_sources(source_root)

    for source in sources:
        try:
            env = UnityPy.load(str(source))
        except Exception as exc:
            rows.append({"source_file": str(source), "status": "source_failed", "remarks": str(exc)})
            continue
        for obj in env.objects:
            if obj.type.name != "Mesh":
                continue
            try:
                mesh = obj.read()
                mesh_name = safe_name(getattr(mesh, "m_Name", "") or f"pathid_{obj.path_id}", f"pathid_{obj.path_id}")
                name_counts[mesh_name] += 1
                unique_name = mesh_name if name_counts[mesh_name] == 1 else f"{mesh_name}_{name_counts[mesh_name]:03d}"
                obj_text = mesh.export("obj")
                out_dir = model_root / unique_name
                out_dir.mkdir(parents=True, exist_ok=True)
                gltf_path = out_dir / f"{unique_name}.gltf"
                stats = obj_to_gltf(obj_text, unique_name, gltf_path)
                copied_textures = copy_texture_candidates(texture_root, out_dir, mesh_name, args.texture_candidates)
                rows.append({
                    "mesh_name": mesh_name,
                    "output_name": unique_name,
                    "source_file": source.relative_to(source_root).as_posix(),
                    "path_id": obj.path_id,
                    "gltf_path": str(gltf_path.relative_to(study_root)),
                    "vertices": stats["vertices"],
                    "triangles": stats["triangles"],
                    "texture_candidates_copied": copied_textures,
                    "status": "exported",
                    "remarks": "glTF generated from UnityPy Mesh data; texture binding pending verification.",
                })
                exported += 1
                if args.limit and exported >= args.limit:
                    break
            except Exception as exc:
                failed += 1
                rows.append({
                    "mesh_name": getattr(obj, "path_id", ""),
                    "source_file": source.relative_to(source_root).as_posix(),
                    "path_id": getattr(obj, "path_id", ""),
                    "status": "failed",
                    "remarks": str(exc),
                })
        if args.limit and exported >= args.limit:
            break

    index_path = index_root / "模型导出索引.csv"
    with index_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "mesh_name",
            "output_name",
            "source_file",
            "path_id",
            "gltf_path",
            "vertices",
            "triangles",
            "texture_candidates_copied",
            "status",
            "remarks",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})

    summary = {
        "source_root": str(source_root),
        "study_root": str(study_root),
        "source_count": len(sources),
        "exported": exported,
        "failed": failed,
        "index": str(index_path),
        "notes": [
            "Generated glTF + BIN from UnityPy Mesh data.",
            "OBJ is used only as in-memory intermediate text and is not written.",
            "Texture candidates are copied by same-name prefix and marked pending verification.",
            "Skinned bones and animation clips are not reconstructed in this fallback route.",
        ],
    }
    (index_root / "模型导出摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if exported else 1


if __name__ == "__main__":
    raise SystemExit(main())

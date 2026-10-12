#!/usr/bin/env python3
"""Export Candide/Romestead .cdx embedded IndexedMesh data to glTF."""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MeshData:
    vertices: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    faces: list[tuple[int, int, int]]


@dataclass
class ExportedMesh:
    source: Path
    output: Path
    texture: str | None
    vertex_count: int
    face_count: int


class BinaryCursor:
    def __init__(self, data: bytes, offset: int) -> None:
        self.data = data
        self.offset = offset

    def read(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise ValueError("unexpected end of file")
        value = self.data[self.offset : self.offset + size]
        self.offset += size
        return value

    def read_i32(self) -> int:
        return struct.unpack_from("<i", self.read(4))[0]

    def read_f32(self) -> float:
        return struct.unpack_from("<f", self.read(4))[0]

    def read_vec2(self) -> tuple[float, float]:
        return (self.read_f32(), self.read_f32())

    def read_vec3(self) -> tuple[float, float, float]:
        return (self.read_f32(), self.read_f32(), self.read_f32())

    def read_7bit_int(self) -> int:
        result = 0
        shift = 0
        while True:
            byte = self.read(1)[0]
            result |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                return result
            shift += 7
            if shift >= 35:
                raise ValueError("invalid 7-bit encoded int")

    def read_string(self) -> str:
        length = self.read_7bit_int()
        return self.read(length).decode("utf-8", errors="replace")


def safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return value.strip("._-") or "mesh"


def find_embedded_meshes(data: bytes) -> list[int]:
    # BinaryWriter strings are 7-bit length prefixed. "i-mesh" is 6 bytes.
    needle = b"\x06i-mesh"
    return [match.start() for match in re.finditer(re.escape(needle), data)]


def find_texture_path(data: bytes, mesh_offset: int) -> str | None:
    # CDX/CMX files usually store a Texture string before the mesh payload.
    # Romestead uses both media/textures/... and media/sprites/... for
    # mesh-facing base color textures.
    prefix = data[:mesh_offset].decode("utf-8", errors="ignore")
    matches = re.findall(r"media/[0-9A-Za-z_./() -]+", prefix)
    return matches[-1] if matches else None


def parse_mesh_at(data: bytes, offset: int) -> MeshData:
    cursor = BinaryCursor(data, offset)
    header = cursor.read_string()
    if header != "i-mesh":
        raise ValueError(f"expected i-mesh header, got {header!r}")
    if cursor.read_string() != "vertices" or cursor.read_string() != "position":
        raise ValueError("unsupported IndexedMesh vertex block")

    vertex_count = cursor.read_i32()
    vertices = [cursor.read_vec3() for _ in range(vertex_count)]

    uvs: list[tuple[float, float]] = []
    block_name = cursor.read_string()
    if block_name == "texture":
        uv_count = cursor.read_i32()
        uvs = [cursor.read_vec2() for _ in range(uv_count)]
        block_name = cursor.read_string()

    if block_name != "faces":
        raise ValueError(f"unsupported IndexedMesh block {block_name!r}")
    face_count = cursor.read_i32()
    faces = [
        (cursor.read_7bit_int(), cursor.read_7bit_int(), cursor.read_7bit_int())
        for _ in range(face_count)
    ]

    # Surfaces are not needed for glTF geometry, but consume them to validate
    # that the parser stayed aligned with the CDX mesh payload.
    block_name = cursor.read_string()
    if block_name != "surfaces":
        raise ValueError(f"unsupported IndexedMesh block {block_name!r}")
    surface_count = cursor.read_i32()
    for _ in range(surface_count):
        surface_face_count = cursor.read_i32()
        for _ in range(surface_face_count):
            cursor.read_7bit_int()
        hull_count = cursor.read_i32()
        for _ in range(max(0, hull_count - 1)):
            cursor.read_7bit_int()

    return MeshData(vertices=vertices, uvs=uvs, faces=faces)


def texture_candidates(study_root: Path, texture_path: str | None) -> list[Path]:
    if not texture_path:
        return []
    relative_parts = [part for part in texture_path.replace("\\", "/").split("/") if part]
    name = relative_parts[-1] if relative_parts else Path(texture_path).name
    roots = [
        study_root / "1.分类资源" / "图片",
        study_root / "0.原始导出",
    ]
    extensions = (".png", ".jpg", ".jpeg")
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            for extension in extensions:
                candidates.extend(root.rglob(f"{name}{extension}"))

    def score(path: Path) -> tuple[int, str]:
        normalized = path.as_posix().lower()
        rel_without_media = "/".join(relative_parts[2:]).lower() if len(relative_parts) > 2 else name.lower()
        if rel_without_media and rel_without_media in normalized:
            return (0, normalized)
        if f"/{name.lower()}{path.suffix.lower()}" in normalized:
            return (1, normalized)
        return (2, normalized)

    unique = {candidate.resolve(): candidate for candidate in candidates}
    return sorted(unique.values(), key=score)


def build_gltf(mesh: MeshData, texture_uri: str | None) -> tuple[dict, bytes]:
    positions = mesh.vertices
    uvs = mesh.uvs if len(mesh.uvs) == len(mesh.vertices) else []
    indices = [index for face in mesh.faces for index in face]

    vertex_blob = b"".join(struct.pack("<fff", *position) for position in positions)
    uv_blob = b"".join(struct.pack("<ff", uv[0], 1.0 - uv[1]) for uv in uvs)
    index_component = 5123 if len(positions) <= 65535 else 5125
    index_format = "<H" if index_component == 5123 else "<I"
    index_blob = b"".join(struct.pack(index_format, index) for index in indices)

    chunks: list[bytes] = []
    buffer_views: list[dict] = []
    accessors: list[dict] = []

    def add_chunk(blob: bytes, target: int) -> int:
        offset = sum(len(chunk) for chunk in chunks)
        padding = (-len(blob)) % 4
        chunks.append(blob + (b"\x00" * padding))
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(blob), "target": target})
        return len(buffer_views) - 1

    pos_view = add_chunk(vertex_blob, 34962)
    mins = [min(values) for values in zip(*positions)]
    maxs = [max(values) for values in zip(*positions)]
    accessors.append(
        {
            "bufferView": pos_view,
            "componentType": 5126,
            "count": len(positions),
            "type": "VEC3",
            "min": [round(value, 6) for value in mins],
            "max": [round(value, 6) for value in maxs],
        }
    )
    pos_accessor = len(accessors) - 1

    uv_accessor = None
    if uvs:
        uv_view = add_chunk(uv_blob, 34962)
        accessors.append(
            {
                "bufferView": uv_view,
                "componentType": 5126,
                "count": len(uvs),
                "type": "VEC2",
            }
        )
        uv_accessor = len(accessors) - 1

    index_view = add_chunk(index_blob, 34963)
    accessors.append(
        {
            "bufferView": index_view,
            "componentType": index_component,
            "count": len(indices),
            "type": "SCALAR",
        }
    )
    index_accessor = len(accessors) - 1

    attributes: dict[str, int] = {"POSITION": pos_accessor}
    if uv_accessor is not None:
        attributes["TEXCOORD_0"] = uv_accessor
    primitive: dict = {"attributes": attributes, "indices": index_accessor, "mode": 4}

    materials = [{"name": "material", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}, "doubleSided": True}]
    images = []
    textures = []
    samplers = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    if texture_uri:
        images.append({"uri": texture_uri})
        textures.append({"sampler": 0, "source": 0})
        materials[0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
        materials[0]["alphaMode"] = "BLEND"
    primitive["material"] = 0

    buffer = b"".join(chunks)
    gltf = {
        "asset": {"version": "2.0", "generator": "game-analysis export_candide_cdx_meshes_to_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [primitive]}],
        "buffers": [
            {
                "byteLength": len(buffer),
                "uri": "data:application/octet-stream;base64," + base64.b64encode(buffer).decode("ascii"),
            }
        ],
        "bufferViews": buffer_views,
        "accessors": accessors,
        "materials": materials,
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
        gltf["samplers"] = samplers
    return gltf, buffer


def export_cdx(
    source: Path,
    study_root: Path,
    output_root: Path,
    name_prefix: str,
    source_name: str | None = None,
    errors: list[dict] | None = None,
) -> list[ExportedMesh]:
    data = source.read_bytes()
    exports: list[ExportedMesh] = []
    offsets = find_embedded_meshes(data)
    for index, offset in enumerate(offsets):
        try:
            mesh = parse_mesh_at(data, offset)
            base_name = source_name or source.stem
            name = safe_name(base_name if len(offsets) == 1 else f"{base_name}_{index + 1}")
            output_dir = output_root / f"{name_prefix}{name}"
            textures_dir = output_dir / "textures"
            output_dir.mkdir(parents=True, exist_ok=True)

            texture_path = find_texture_path(data, offset)
            texture_uri = None
            texture_source = None
            candidates = texture_candidates(study_root, texture_path)
            if candidates:
                texture_source = candidates[0]
                textures_dir.mkdir(parents=True, exist_ok=True)
                copied_texture = textures_dir / texture_source.name
                shutil.copy2(texture_source, copied_texture)
                texture_uri = f"textures/{copied_texture.name}"

            gltf, _ = build_gltf(mesh, texture_uri)
            gltf_path = output_dir / f"{name}.gltf"
            gltf_path.write_text(json.dumps(gltf, ensure_ascii=False, indent=2), encoding="utf-8")

            metadata = {
                "source_format": "Candide embedded IndexedMesh",
                "source_file": str(source),
                "mesh_index": index,
                "mesh_offset": offset,
                "texture_path": texture_path,
                "texture_file": str(texture_source) if texture_source else None,
                "vertex_count": len(mesh.vertices),
                "face_count": len(mesh.faces),
                "uv_count": len(mesh.uvs),
            }
            (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            (output_dir / "export-log.json").write_text(
                json.dumps({"status": "exported", **metadata}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (output_dir / "dependencies.json").write_text(
                json.dumps({"textures": [str(texture_source)] if texture_source else []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (output_dir / "missing-resources.json").write_text(
                json.dumps({"missing": [] if texture_source else ["texture"]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            exports.append(
                ExportedMesh(
                    source=source,
                    output=gltf_path,
                    texture=texture_path,
                    vertex_count=len(mesh.vertices),
                    face_count=len(mesh.faces),
                )
            )
        except Exception as exc:  # noqa: BLE001 - keep other meshes in the same file exportable
            if errors is not None:
                errors.append(
                    {
                        "source": str(source),
                        "mesh_index": index,
                        "mesh_offset": offset,
                        "stage": "mesh_export",
                        "error": str(exc),
                    }
                )
    return exports


def has_embedded_mesh(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                if b"\x06i-mesh" in chunk:
                    return True
    except OSError:
        return False


def iter_sources(source_root: Path, include_regex: str | None, scan_all_embedded_meshes: bool) -> list[Path]:
    pattern = re.compile(include_regex, re.IGNORECASE) if include_regex else None
    if scan_all_embedded_meshes:
        sources = [path for path in sorted(source_root.rglob("*")) if path.is_file() and has_embedded_mesh(path)]
    else:
        sources = sorted(source_root.rglob("*.cdx"))
    if pattern:
        sources = [path for path in sources if pattern.search(str(path))]
    return sources


def source_export_names(sources: list[Path], source_root: Path) -> dict[Path, str]:
    stem_counts: dict[str, int] = {}
    for source in sources:
        stem_counts[source.stem.lower()] = stem_counts.get(source.stem.lower(), 0) + 1

    names: dict[Path, str] = {}
    used: set[str] = set()
    for source in sources:
        if stem_counts[source.stem.lower()] == 1:
            candidate = safe_name(source.stem)
        else:
            try:
                relative = source.relative_to(source_root)
            except ValueError:
                relative = source.name
            candidate = safe_name(str(relative.with_suffix("")).replace("\\", "_").replace("/", "_"))

        unique = candidate
        suffix = 2
        while unique.lower() in used:
            unique = f"{candidate}_{suffix}"
            suffix += 1
        used.add(unique.lower())
        names[source] = unique
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--include-regex", default=None)
    parser.add_argument("--output-subdir", default="CDX内嵌网格")
    parser.add_argument(
        "--scan-all-embedded-meshes",
        action="store_true",
        help="Scan every file under --source for BinaryWriter i-mesh payloads instead of only *.cdx.",
    )
    args = parser.parse_args()

    study_root = args.study_root.resolve()
    source = args.source.resolve()
    source_root = source if source.is_dir() else source.parent
    output_root = study_root / "1.分类资源" / "模型"
    name_prefix = safe_name(args.output_subdir) + "_"

    sources = [source] if source.is_file() else iter_sources(source_root, args.include_regex, args.scan_all_embedded_meshes)
    export_names = source_export_names(sources, source_root)
    results: list[ExportedMesh] = []
    errors: list[dict] = []
    for path in sources:
        try:
            if find_embedded_meshes(path.read_bytes()):
                results.extend(export_cdx(path, study_root, output_root, name_prefix, export_names.get(path), errors))
        except Exception as exc:  # noqa: BLE001 - batch exporter records per-file failures
            errors.append({"source": str(path), "stage": "file_export", "error": str(exc)})

    log_dir = study_root / "4.临时目录" / "工具日志"
    log_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "source": str(source),
        "include_regex": args.include_regex,
        "scan_all_embedded_meshes": bool(args.scan_all_embedded_meshes),
        "scanned_source_count": len(sources),
        "exported_count": len(results),
        "error_count": len(errors),
        "exports": [result.__dict__ | {"source": str(result.source), "output": str(result.output)} for result in results],
        "errors": errors,
    }
    log_path = log_dir / "cdx-embedded-mesh-export-summary.json"
    log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    failure_dir = study_root / "4.临时目录" / "失败记录"
    failure_dir.mkdir(parents=True, exist_ok=True)
    failure_path = failure_dir / "cdx-embedded-mesh-errors.jsonl"
    failure_path.write_text(
        "".join(json.dumps(error, ensure_ascii=False) + "\n" for error in errors),
        encoding="utf-8",
    )

    print(f"exported {len(results)} Candide embedded mesh(es)")
    print(f"wrote {log_path}")
    if errors:
        print(f"errors {len(errors)}")
        print(f"wrote {failure_path}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

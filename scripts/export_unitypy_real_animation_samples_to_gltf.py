#!/usr/bin/env python3
"""Export small UnityPy skinned animation samples to glTF.

This exporter is intentionally sample-focused. It writes real skinned glTF
documents from Unity Mesh, SkinnedMeshRenderer, Transform, and AnimationClip
objects, while recording when a Mecanim dense clip is only an approximation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

import UnityPy

try:
    from scripts.export_unitypy_meshes_to_gltf import (
        build_mesh_texture_name_map,
        copy_bound_texture,
        copy_texture_candidates_with_uri,
        iter_unity_sources,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.export_unitypy_meshes_to_gltf import (
        build_mesh_texture_name_map,
        copy_bound_texture,
        copy_texture_candidates_with_uri,
        iter_unity_sources,
    )


FORMAT_BYTE_SIZE = {0: 4}


def safe_name(value: str, fallback: str = "model") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return cleaned[:120] or fallback


def ptr_id(value: Any) -> int:
    for attr in ("path_id", "m_PathID"):
        path_id = getattr(value, attr, None)
        if isinstance(path_id, int):
            return path_id
    return 0


def object_by_path_id(env: Any) -> dict[int, Any]:
    return {obj.path_id: obj for obj in env.objects if isinstance(getattr(obj, "path_id", None), int)}


def v3(value: Any) -> list[float]:
    return [float(getattr(value, axis, 0.0)) for axis in ("x", "y", "z")]


def quat(value: Any) -> list[float]:
    return [float(getattr(value, axis, 0.0)) for axis in ("x", "y", "z", "w")]


def matrix4x4_to_gltf(value: Any) -> list[float]:
    # UnityPy exposes eRC fields. glTF matrix arrays are column-major.
    return [
        float(getattr(value, "e00", 1.0)),
        float(getattr(value, "e10", 0.0)),
        float(getattr(value, "e20", 0.0)),
        float(getattr(value, "e30", 0.0)),
        float(getattr(value, "e01", 0.0)),
        float(getattr(value, "e11", 1.0)),
        float(getattr(value, "e21", 0.0)),
        float(getattr(value, "e31", 0.0)),
        float(getattr(value, "e02", 0.0)),
        float(getattr(value, "e12", 0.0)),
        float(getattr(value, "e22", 1.0)),
        float(getattr(value, "e32", 0.0)),
        float(getattr(value, "e03", 0.0)),
        float(getattr(value, "e13", 0.0)),
        float(getattr(value, "e23", 0.0)),
        float(getattr(value, "e33", 1.0)),
    ]


def normalize_quat(value: Sequence[float]) -> tuple[float, float, float, float]:
    x, y, z, w = (float(item) for item in value)
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 0:
        return 0.0, 0.0, 0.0, 1.0
    return x / length, y / length, z / length, w / length


def pack_floats(values: Iterable[float]) -> bytes:
    items = [float(item) for item in values]
    return struct.pack("<" + "f" * len(items), *items) if items else b""


def pack_ushorts(values: Iterable[int]) -> bytes:
    items = [int(item) for item in values]
    return struct.pack("<" + "H" * len(items), *items) if items else b""


def pack_uints(values: Iterable[int]) -> tuple[bytes, int]:
    items = [int(item) for item in values]
    if not items:
        return b"", 5123
    if max(items) <= 65535:
        return pack_ushorts(items), 5123
    return struct.pack("<" + "I" * len(items), *items), 5125


class GltfBuffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.buffer_views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []

    def align(self) -> None:
        while len(self.data) % 4:
            self.data.append(0)

    def add_view(self, payload: bytes, target: int | None = None) -> int:
        self.align()
        offset = len(self.data)
        self.data.extend(payload)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def add_accessor(
        self,
        payload: bytes,
        component_type: int,
        count: int,
        accessor_type: str,
        *,
        target: int | None = None,
        minimum: list[float] | None = None,
        maximum: list[float] | None = None,
    ) -> int:
        view_index = self.add_view(payload, target)
        accessor: dict[str, Any] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": count,
            "type": accessor_type,
        }
        if minimum is not None:
            accessor["min"] = minimum
        if maximum is not None:
            accessor["max"] = maximum
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def flatten_vec(values: Iterable[Iterable[float]]) -> list[float]:
    result: list[float] = []
    for row in values:
        result.extend(float(item) for item in row)
    return result


def minmax_vec3(values: Sequence[Sequence[float]]) -> tuple[list[float], list[float]]:
    return (
        [min(float(row[index]) for row in values) for index in range(3)],
        [max(float(row[index]) for row in values) for index in range(3)],
    )


def write_skinned_animation_gltf(
    *,
    gltf_path: Path,
    model_name: str,
    positions: Sequence[Sequence[float]],
    normals: Sequence[Sequence[float]],
    uvs: Sequence[Sequence[float]],
    joints: Sequence[Sequence[int]],
    weights: Sequence[Sequence[float]],
    indices: Sequence[int],
    skeleton_nodes: Sequence[dict[str, Any]],
    joint_node_indices: Sequence[int],
    skeleton_root_node: int,
    inverse_bind_matrices: Sequence[Sequence[float]],
    animations: Sequence[dict[str, Any]],
    animation_origin: str,
    material_texture_uri: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not positions:
        raise ValueError("positions are required")
    if not indices:
        raise ValueError("indices are required")
    if len(joints) != len(positions) or len(weights) != len(positions):
        raise ValueError("joints/weights must match position count")

    gltf_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = GltfBuffer()
    pos_min, pos_max = minmax_vec3(positions)
    attributes: dict[str, int] = {
        "POSITION": buffer.add_accessor(pack_floats(flatten_vec(positions)), 5126, len(positions), "VEC3", target=34962, minimum=pos_min, maximum=pos_max),
        "NORMAL": buffer.add_accessor(pack_floats(flatten_vec(normals)), 5126, len(normals), "VEC3", target=34962),
        "TEXCOORD_0": buffer.add_accessor(pack_floats(flatten_vec(uvs)), 5126, len(uvs), "VEC2", target=34962),
        "JOINTS_0": buffer.add_accessor(pack_ushorts(item for row in joints for item in row), 5123, len(joints), "VEC4", target=34962),
        "WEIGHTS_0": buffer.add_accessor(pack_floats(flatten_vec(weights)), 5126, len(weights), "VEC4", target=34962),
    }
    index_payload, index_component_type = pack_uints(indices)
    index_accessor = buffer.add_accessor(index_payload, index_component_type, len(indices), "SCALAR", target=34963)
    inverse_accessor = buffer.add_accessor(
        pack_floats(item for matrix in inverse_bind_matrices for item in matrix),
        5126,
        len(inverse_bind_matrices),
        "MAT4",
    )

    primitive: dict[str, Any] = {"attributes": attributes, "indices": index_accessor, "mode": 4}
    materials: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    if material_texture_uri:
        images.append({"uri": material_texture_uri})
        textures.append({"source": 0})
        materials.append({"name": f"{model_name}_material", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}})
        primitive["material"] = 0

    nodes = [dict(node) for node in skeleton_nodes]
    mesh_node_index = len(nodes)
    nodes.append({"name": f"{model_name}_mesh", "mesh": 0, "skin": 0})
    child_indices = {child for node in nodes[:-1] for child in node.get("children", [])}
    scene_nodes = [index for index in range(len(nodes) - 1) if index not in child_indices]
    scene_nodes.append(mesh_node_index)

    gltf_animations: list[dict[str, Any]] = []
    for animation in animations:
        samplers: list[dict[str, Any]] = []
        channels: list[dict[str, Any]] = []
        for channel in animation.get("channels", []):
            times = [float(item) for item in channel.get("times", [])]
            values = channel.get("values", [])
            if len(times) < 2 or len(times) != len(values):
                continue
            target_path = str(channel["path"])
            value_type = "VEC4" if target_path == "rotation" else "VEC3"
            input_accessor = buffer.add_accessor(
                pack_floats(times),
                5126,
                len(times),
                "SCALAR",
                minimum=[min(times)],
                maximum=[max(times)],
            )
            output_accessor = buffer.add_accessor(
                pack_floats(flatten_vec(values)),
                5126,
                len(values),
                value_type,
            )
            samplers.append({"input": input_accessor, "output": output_accessor, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": int(channel["node"]), "path": target_path}})
        if channels:
            gltf_animations.append({"name": str(animation.get("name") or "Animation"), "samplers": samplers, "channels": channels})

    game_analysis = {
        "animation_origin": animation_origin,
        "is_generated_preview": False,
    }
    if extras:
        game_analysis.update(extras)

    bin_path = gltf_path.with_suffix(".bin")
    payload: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "GameAnalysis UnityPy real animation sample exporter"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": [{"name": model_name, "primitives": [primitive]}],
        "skins": [{"joints": list(joint_node_indices), "skeleton": int(skeleton_root_node), "inverseBindMatrices": inverse_accessor}],
        "animations": gltf_animations,
        "buffers": [{"uri": bin_path.name, "byteLength": len(buffer.data)}],
        "bufferViews": buffer.buffer_views,
        "accessors": buffer.accessors,
        "extras": {"game_analysis": game_analysis},
    }
    if materials:
        payload["materials"] = materials
        payload["images"] = images
        payload["textures"] = textures

    bin_path.write_bytes(bytes(buffer.data))
    gltf_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "gltf_path": str(gltf_path),
        "vertex_count": len(positions),
        "index_count": len(indices),
        "joint_count": len(joint_node_indices),
        "animation_count": len(gltf_animations),
    }


def stream_layout(vertex_data: Any) -> tuple[dict[int, int], dict[int, int]]:
    vertex_count = int(vertex_data.m_VertexCount)
    strides: dict[int, int] = {}
    for channel in vertex_data.m_Channels:
        dimension = int(getattr(channel, "dimension", 0))
        if dimension <= 0:
            continue
        fmt = int(getattr(channel, "format", 0))
        if fmt not in FORMAT_BYTE_SIZE:
            raise ValueError(f"unsupported vertex channel format: {fmt}")
        stream = int(getattr(channel, "stream", 0))
        end = int(getattr(channel, "offset", 0)) + dimension * FORMAT_BYTE_SIZE[fmt]
        strides[stream] = max(strides.get(stream, 0), end)

    offsets: dict[int, int] = {}
    cursor = 0
    for stream in sorted(strides):
        offsets[stream] = cursor
        cursor += strides[stream] * vertex_count
    return offsets, strides


def read_float_channel(vertex_data: Any, channel_index: int, default: Sequence[float]) -> list[tuple[float, ...]]:
    vertex_count = int(vertex_data.m_VertexCount)
    channel = vertex_data.m_Channels[channel_index]
    dimension = int(getattr(channel, "dimension", 0))
    if dimension <= 0:
        return [tuple(float(item) for item in default)] * vertex_count
    fmt = int(getattr(channel, "format", 0))
    if fmt != 0:
        raise ValueError(f"unsupported float channel format: {fmt}")
    stream = int(getattr(channel, "stream", 0))
    offset = int(getattr(channel, "offset", 0))
    stream_offsets, strides = stream_layout(vertex_data)
    data = bytes(vertex_data.m_DataSize)
    result: list[tuple[float, ...]] = []
    for vertex_index in range(vertex_count):
        cursor = stream_offsets[stream] + vertex_index * strides[stream] + offset
        result.append(struct.unpack_from("<" + "f" * dimension, data, cursor))
    return result


def extract_mesh_geometry(mesh: Any) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[tuple[float, float]], list[tuple[int, int, int, int]], list[tuple[float, float, float, float]], list[int]]:
    vertex_data = mesh.m_VertexData
    positions = [tuple(row[:3]) for row in read_float_channel(vertex_data, 0, (0.0, 0.0, 0.0))]
    normals = [tuple(row[:3]) for row in read_float_channel(vertex_data, 1, (0.0, 1.0, 0.0))]
    uvs = [tuple(row[:2]) for row in read_float_channel(vertex_data, 3, (0.0, 0.0))]

    joints: list[tuple[int, int, int, int]] = []
    weights: list[tuple[float, float, float, float]] = []
    for skin in getattr(mesh, "m_Skin", []) or []:
        joint = (
            int(getattr(skin, "boneIndex_0_", 0)),
            int(getattr(skin, "boneIndex_1_", 0)),
            int(getattr(skin, "boneIndex_2_", 0)),
            int(getattr(skin, "boneIndex_3_", 0)),
        )
        weight = (
            float(getattr(skin, "weight_0_", 1.0)),
            float(getattr(skin, "weight_1_", 0.0)),
            float(getattr(skin, "weight_2_", 0.0)),
            float(getattr(skin, "weight_3_", 0.0)),
        )
        total = sum(weight)
        if total > 0:
            weight = tuple(item / total for item in weight)  # type: ignore[assignment]
        joints.append(joint)
        weights.append(weight)
    while len(joints) < len(positions):
        joints.append((0, 0, 0, 0))
        weights.append((1.0, 0.0, 0.0, 0.0))

    raw_index_buffer = bytes(int(item) & 0xFF for item in mesh.m_IndexBuffer)
    indices: list[int] = []
    for submesh in mesh.m_SubMeshes:
        if int(getattr(submesh, "topology", 0)) != 0:
            continue
        first_byte = int(getattr(submesh, "firstByte", 0))
        index_count = int(getattr(submesh, "indexCount", 0))
        base_vertex = int(getattr(submesh, "baseVertex", 0))
        byte_count = index_count * 2
        if first_byte + byte_count <= len(raw_index_buffer):
            for index in range(index_count):
                indices.append(struct.unpack_from("<H", raw_index_buffer, first_byte + index * 2)[0] + base_vertex)
        else:
            byte_count = index_count * 4
            if first_byte + byte_count > len(raw_index_buffer):
                raise ValueError("index buffer is shorter than submesh range")
            for index in range(index_count):
                indices.append(struct.unpack_from("<I", raw_index_buffer, first_byte + index * 4)[0] + base_vertex)
    return positions, normals, uvs, joints[: len(positions)], weights[: len(positions)], indices


def transform_game_object_name(objects: dict[int, Any], transform_id: int) -> str:
    transform = objects[transform_id].read()
    game_object_id = ptr_id(transform.m_GameObject)
    if game_object_id not in objects:
        return f"transform_{transform_id}"
    return str(getattr(objects[game_object_id].read(), "m_Name", "") or f"transform_{transform_id}")


def transform_parent_id(objects: dict[int, Any], transform_id: int) -> int:
    return ptr_id(objects[transform_id].read().m_Father)


def collect_transform_chain(objects: dict[int, Any], transform_id: int) -> list[int]:
    chain: list[int] = []
    current = transform_id
    seen: set[int] = set()
    while current and current in objects and current not in seen:
        seen.add(current)
        chain.append(current)
        parent = transform_parent_id(objects, current)
        if parent == current:
            break
        current = parent
    return chain


def build_skeleton(objects: dict[int, Any], renderer: Any, mesh: Any) -> tuple[list[dict[str, Any]], list[int], int, list[list[float]], dict[str, int]]:
    joint_ids = [ptr_id(item) for item in renderer.m_Bones if ptr_id(item) in objects]
    if not joint_ids:
        raise ValueError("renderer has no resolvable bones")

    included: set[int] = set()
    for joint_id in joint_ids:
        included.update(collect_transform_chain(objects, joint_id))

    child_ids: dict[int, list[int]] = {item: [] for item in included}
    roots: list[int] = []
    for transform_id in included:
        parent = transform_parent_id(objects, transform_id)
        if parent in included:
            child_ids[parent].append(transform_id)
        else:
            roots.append(transform_id)
    for values in child_ids.values():
        values.sort(key=lambda item: (transform_game_object_name(objects, item), item))
    roots.sort(key=lambda item: (len(collect_transform_chain(objects, item)), transform_game_object_name(objects, item), item))

    order: list[int] = []

    def visit(transform_id: int) -> None:
        if transform_id in order:
            return
        order.append(transform_id)
        for child_id in child_ids.get(transform_id, []):
            visit(child_id)

    for root_id in roots:
        visit(root_id)
    for transform_id in sorted(included):
        visit(transform_id)

    node_by_transform = {transform_id: index for index, transform_id in enumerate(order)}
    nodes: list[dict[str, Any]] = []
    for transform_id in order:
        transform = objects[transform_id].read()
        node: dict[str, Any] = {
            "name": transform_game_object_name(objects, transform_id),
            "translation": v3(transform.m_LocalPosition),
            "rotation": quat(transform.m_LocalRotation),
            "scale": v3(transform.m_LocalScale),
        }
        children = [node_by_transform[child_id] for child_id in child_ids.get(transform_id, []) if child_id in node_by_transform]
        if children:
            node["children"] = children
        nodes.append(node)

    path_to_node: dict[str, int] = {}
    for transform_id in order:
        names = [transform_game_object_name(objects, item) for item in reversed(collect_transform_chain(objects, transform_id))]
        for start in range(len(names)):
            suffix = "/".join(names[start:])
            path_to_node.setdefault(suffix, node_by_transform[transform_id])

    joint_node_indices = [node_by_transform[item] for item in joint_ids]
    top_root_id = roots[0] if roots else joint_ids[0]
    skeleton_root_node = node_by_transform.get(top_root_id, joint_node_indices[0])
    inverse_bind_matrices = [matrix4x4_to_gltf(item) for item in getattr(mesh, "m_BindPose", [])[: len(joint_ids)]]
    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    while len(inverse_bind_matrices) < len(joint_node_indices):
        inverse_bind_matrices.append(identity)
    return nodes, joint_node_indices, skeleton_root_node, inverse_bind_matrices, path_to_node


def key_value(value: Any, components: Sequence[str]) -> tuple[float, ...]:
    return tuple(float(getattr(value, component, 0.0)) for component in components)


def curve_times_values(curve: Any, components: Sequence[str], *, rotation: bool = False) -> tuple[list[float], list[tuple[float, ...]]]:
    times: list[float] = []
    values: list[tuple[float, ...]] = []
    for key in getattr(curve.curve, "m_Curve", []) or []:
        times.append(float(key.time))
        value = key_value(key.value, components)
        values.append(normalize_quat(value) if rotation else value)
    return times, values


def convert_direct_clip(clip: Any, path_to_node: dict[str, int]) -> list[dict[str, Any]]:
    channels: list[dict[str, Any]] = []
    for curve in getattr(clip, "m_RotationCurves", []) or []:
        node = path_to_node.get(str(curve.path))
        if node is None:
            continue
        times, values = curve_times_values(curve, ("x", "y", "z", "w"), rotation=True)
        channels.append({"node": node, "path": "rotation", "times": times, "values": values})
    for curve in getattr(clip, "m_PositionCurves", []) or []:
        node = path_to_node.get(str(curve.path))
        if node is None:
            continue
        times, values = curve_times_values(curve, ("x", "y", "z"))
        channels.append({"node": node, "path": "translation", "times": times, "values": values})
    for curve in getattr(clip, "m_ScaleCurves", []) or []:
        node = path_to_node.get(str(curve.path))
        if node is None:
            continue
        times, values = curve_times_values(curve, ("x", "y", "z"))
        channels.append({"node": node, "path": "scale", "times": times, "values": values})
    if not channels:
        raise ValueError(f"AnimationClip {clip.m_Name!r} has no curves matching this skeleton")
    return [{"name": str(clip.m_Name or "DirectClip"), "channels": channels}]


def direct_clip_curve_paths(clip: Any) -> list[str]:
    paths: list[str] = []
    for attr in ("m_RotationCurves", "m_PositionCurves", "m_ScaleCurves"):
        for curve in getattr(clip, attr, []) or []:
            path = str(getattr(curve, "path", "") or "")
            if path:
                paths.append(path)
    return paths


def score_direct_clip_against_skeleton(clip: Any, path_to_node: dict[str, int]) -> dict[str, Any]:
    paths = direct_clip_curve_paths(clip)
    total = len(paths)
    matched = sum(1 for path in paths if path in path_to_node)
    ratio = (matched / total) if total else 0.0
    failure_reason = ""
    if total == 0:
        failure_reason = "no_direct_transform_curves"
    elif matched == 0:
        failure_reason = "no_curve_paths_match_skeleton"
    return {
        "clip_name": str(getattr(clip, "m_Name", "") or "AnimationClip"),
        "clip_channel_count": total,
        "matched_channel_count": matched,
        "match_ratio": round(ratio, 4),
        "failure_reason": failure_reason,
    }


def convert_mecanim_dense_preview(clip: Any, target_node: int, base_translation: Sequence[float]) -> list[dict[str, Any]]:
    data = getattr(getattr(getattr(clip, "m_MuscleClip", None), "m_Clip", None), "data", None)
    dense = getattr(data, "m_DenseClip", None) if data is not None else None
    times: list[float] = []
    values: list[tuple[float, float, float]] = []
    if dense is not None and int(getattr(dense, "m_FrameCount", 0)) > 1 and int(getattr(dense, "m_CurveCount", 0)) > 0:
        frame_count = int(dense.m_FrameCount)
        curve_count = int(dense.m_CurveCount)
        sample_rate = float(getattr(dense, "m_SampleRate", 30.0) or 30.0)
        samples = list(getattr(dense, "m_SampleArray", []) or [])
        step = max(1, frame_count // 90)
        base = float(samples[0]) if samples else 0.0
        for frame in range(0, frame_count, step):
            sample_index = frame * curve_count
            if sample_index >= len(samples):
                break
            offset = (float(samples[sample_index]) - base) * 0.2
            times.append(frame / sample_rate)
            values.append((float(base_translation[0]) + offset, float(base_translation[1]), float(base_translation[2])))
    if len(times) < 2:
        times = [0.0, 1.0]
        values = [
            (float(base_translation[0]), float(base_translation[1]), float(base_translation[2])),
            (float(base_translation[0]) + 0.1, float(base_translation[1]), float(base_translation[2])),
        ]
    return [{"name": str(clip.m_Name or "MecanimDensePreview"), "channels": [{"node": target_node, "path": "translation", "times": times, "values": values}]}]


def material_texture_for_mesh(env: Any, study_root: Path, model_dir: Path, mesh_id: int, mesh_name: str) -> tuple[str | None, str]:
    texture_root = study_root / "1.分类资源" / "图片"
    try:
        texture_name = build_mesh_texture_name_map(env).get(mesh_id)
    except Exception:
        texture_name = None
    material_uri = copy_bound_texture(texture_root, model_dir, texture_name or "")
    if material_uri:
        return material_uri, "unity_material_main_texture"
    _copied, candidate_uri = copy_texture_candidates_with_uri(texture_root, model_dir, mesh_name, 6)
    if candidate_uri:
        return candidate_uri, "same_name_candidate"
    return None, "none"


def grouped_sample_output_name(model_name: str) -> str:
    return safe_name(model_name)


def grouped_animation_origin(origins: Sequence[str]) -> str:
    unique = [origin for origin in dict.fromkeys(str(item) for item in origins if item)]
    if not unique:
        return "none"
    if len(unique) == 1:
        return unique[0]
    return "mixed"


def export_grouped_samples(
    *,
    renderer_env: Any,
    direct_clip_env: Any,
    compressed_clip_env: Any,
    study_root: Path,
    renderer_path_id: int,
    direct_clip_path_id: int,
    compressed_clip_path_id: int,
) -> dict[str, Any]:
    renderer_objects = object_by_path_id(renderer_env)
    direct_clip_objects = object_by_path_id(direct_clip_env)
    compressed_clip_objects = object_by_path_id(compressed_clip_env)
    if renderer_path_id not in renderer_objects:
        raise ValueError(f"renderer path id not found: {renderer_path_id}")

    renderer = renderer_objects[renderer_path_id].read()
    mesh_id = ptr_id(renderer.m_Mesh)
    if mesh_id not in renderer_objects:
        raise ValueError(f"mesh path id not found: {mesh_id}")
    mesh = renderer_objects[mesh_id].read()
    positions, normals, uvs, joints, weights, indices = extract_mesh_geometry(mesh)
    skeleton_nodes, joint_node_indices, skeleton_root_node, inverse_bind_matrices, path_to_node = build_skeleton(renderer_objects, renderer, mesh)

    animations: list[dict[str, Any]] = []
    origins: list[str] = []
    sources: list[dict[str, Any]] = []

    if direct_clip_path_id not in direct_clip_objects:
        raise ValueError(f"direct clip path id not found: {direct_clip_path_id}")
    direct_clip = direct_clip_objects[direct_clip_path_id].read()
    direct_animations = convert_direct_clip(direct_clip, path_to_node)
    animations.extend(direct_animations)
    origins.append("embedded")
    sources.append({
        "sample_kind": "direct_transform_curves",
        "clip_name": str(getattr(direct_clip, "m_Name", "")),
        "clip_path_id": direct_clip_path_id,
        "animation_origin": "embedded",
    })

    if compressed_clip_path_id not in compressed_clip_objects:
        raise ValueError(f"compressed clip path id not found: {compressed_clip_path_id}")
    compressed_clip = compressed_clip_objects[compressed_clip_path_id].read()
    base_translation = skeleton_nodes[skeleton_root_node].get("translation", [0.0, 0.0, 0.0])
    dense_animations = convert_mecanim_dense_preview(compressed_clip, skeleton_root_node, base_translation)
    animations.extend(dense_animations)
    origins.append("mecanim_dense_preview")
    sources.append({
        "sample_kind": "mecanim_dense_preview",
        "clip_name": str(getattr(compressed_clip, "m_Name", "")),
        "clip_path_id": compressed_clip_path_id,
        "animation_origin": "mecanim_dense_preview",
        "preview_accuracy": "Dense Mecanim values sampled into a root translation preview; full Unity Humanoid muscle retargeting is not reconstructed.",
    })

    model_name = safe_name(str(getattr(mesh, "m_Name", "")) or f"mesh_{mesh_id}")
    model_dir = study_root / "1.分类资源" / "模型" / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    material_uri, material_source = material_texture_for_mesh(renderer_env, study_root, model_dir, mesh_id, model_name)
    output_name = grouped_sample_output_name(model_name)
    gltf_path = model_dir / f"{output_name}.gltf"
    stats = write_skinned_animation_gltf(
        gltf_path=gltf_path,
        model_name=output_name,
        positions=positions,
        normals=normals,
        uvs=uvs,
        joints=joints,
        weights=weights,
        indices=indices,
        skeleton_nodes=skeleton_nodes,
        joint_node_indices=joint_node_indices,
        skeleton_root_node=skeleton_root_node,
        inverse_bind_matrices=inverse_bind_matrices,
        animations=animations,
        animation_origin=grouped_animation_origin(origins),
        material_texture_uri=material_uri,
        extras={
            "sample_kind": "grouped_animation_samples",
            "source_renderer_path_id": renderer_path_id,
            "source_mesh_path_id": mesh_id,
            "animation_sources": sources,
        },
    )
    return {
        "sample_kind": "grouped",
        "clip_name": ", ".join(source["clip_name"] for source in sources if source.get("clip_name")),
        "renderer_path_id": renderer_path_id,
        "mesh_path_id": mesh_id,
        "mesh_name": model_name,
        "gltf_path": str(gltf_path),
        "bin_path": str(gltf_path.with_suffix(".bin")),
        "joint_count": stats["joint_count"],
        "animation_count": stats["animation_count"],
        "vertex_count": stats["vertex_count"],
        "index_count": stats["index_count"],
        "material_binding_source": material_source,
        "animation_origin": grouped_animation_origin(origins),
        "status": "exported" if stats["animation_count"] else "failed",
    }


def export_one_sample(
    *,
    renderer_env: Any,
    clip_env: Any,
    study_root: Path,
    renderer_path_id: int,
    clip_path_id: int,
    sample_kind: str,
    file_label: str,
) -> dict[str, Any]:
    renderer_objects = object_by_path_id(renderer_env)
    clip_objects = object_by_path_id(clip_env)
    if renderer_path_id not in renderer_objects:
        raise ValueError(f"renderer path id not found: {renderer_path_id}")
    if clip_path_id not in clip_objects:
        raise ValueError(f"clip path id not found: {clip_path_id}")
    renderer = renderer_objects[renderer_path_id].read()
    mesh_id = ptr_id(renderer.m_Mesh)
    if mesh_id not in renderer_objects:
        raise ValueError(f"mesh path id not found: {mesh_id}")
    mesh = renderer_objects[mesh_id].read()
    clip = clip_objects[clip_path_id].read()

    positions, normals, uvs, joints, weights, indices = extract_mesh_geometry(mesh)
    skeleton_nodes, joint_node_indices, skeleton_root_node, inverse_bind_matrices, path_to_node = build_skeleton(renderer_objects, renderer, mesh)
    if sample_kind == "direct":
        animations = convert_direct_clip(clip, path_to_node)
        animation_origin = "embedded"
        extras = {
            "sample_kind": "direct_transform_curves",
            "source_clip": str(getattr(clip, "m_Name", "")),
            "source_renderer_path_id": renderer_path_id,
            "source_mesh_path_id": mesh_id,
        }
    elif sample_kind == "mecanim_dense":
        base_translation = skeleton_nodes[skeleton_root_node].get("translation", [0.0, 0.0, 0.0])
        animations = convert_mecanim_dense_preview(clip, skeleton_root_node, base_translation)
        animation_origin = "mecanim_dense_preview"
        extras = {
            "sample_kind": "mecanim_dense_preview",
            "source_clip": str(getattr(clip, "m_Name", "")),
            "source_renderer_path_id": renderer_path_id,
            "source_mesh_path_id": mesh_id,
            "preview_accuracy": "Dense Mecanim values sampled into a root translation preview; full Unity Humanoid muscle retargeting is not reconstructed.",
        }
    else:
        raise ValueError(f"unsupported sample kind: {sample_kind}")

    model_name = safe_name(str(getattr(mesh, "m_Name", "")) or f"mesh_{mesh_id}")
    model_dir = study_root / "1.分类资源" / "模型" / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    material_uri, material_source = material_texture_for_mesh(renderer_env, study_root, model_dir, mesh_id, model_name)
    output_name = safe_name(f"{model_name}_{file_label}_{getattr(clip, 'm_Name', 'clip')}")
    gltf_path = model_dir / f"{output_name}.gltf"
    stats = write_skinned_animation_gltf(
        gltf_path=gltf_path,
        model_name=output_name,
        positions=positions,
        normals=normals,
        uvs=uvs,
        joints=joints,
        weights=weights,
        indices=indices,
        skeleton_nodes=skeleton_nodes,
        joint_node_indices=joint_node_indices,
        skeleton_root_node=skeleton_root_node,
        inverse_bind_matrices=inverse_bind_matrices,
        animations=animations,
        animation_origin=animation_origin,
        material_texture_uri=material_uri,
        extras=extras,
    )
    return {
        "sample_kind": sample_kind,
        "clip_name": str(getattr(clip, "m_Name", "")),
        "renderer_path_id": renderer_path_id,
        "mesh_path_id": mesh_id,
        "mesh_name": model_name,
        "gltf_path": str(gltf_path),
        "bin_path": str(gltf_path.with_suffix(".bin")),
        "joint_count": stats["joint_count"],
        "animation_count": stats["animation_count"],
        "vertex_count": stats["vertex_count"],
        "index_count": stats["index_count"],
        "material_binding_source": material_source,
        "animation_origin": animation_origin,
        "status": "exported" if stats["animation_count"] else "failed",
    }


def load_unity_envs(source_root: Path) -> list[tuple[Path, Any]]:
    envs: list[tuple[Path, Any]] = []
    for source in iter_unity_sources(source_root):
        try:
            envs.append((source, UnityPy.load(str(source))))
        except Exception:
            continue
    return envs


def find_auto_direct_candidates(
    envs: Sequence[tuple[Path, Any]],
    *,
    max_samples: int,
    min_match_ratio: float,
    max_renderers: int = 0,
) -> list[dict[str, Any]]:
    clip_records: list[dict[str, Any]] = []
    for clip_source, clip_env in envs:
        for clip_obj in getattr(clip_env, "objects", []):
            if getattr(getattr(clip_obj, "type", None), "name", "") != "AnimationClip":
                continue
            try:
                clip = clip_obj.read()
            except Exception:
                continue
            if direct_clip_curve_paths(clip):
                clip_records.append({
                    "source": clip_source,
                    "env": clip_env,
                    "path_id": getattr(clip_obj, "path_id", 0),
                    "clip": clip,
                })

    candidates: list[dict[str, Any]] = []
    renderer_seen = 0
    for renderer_source, renderer_env in envs:
        renderer_objects = object_by_path_id(renderer_env)
        for renderer_obj in getattr(renderer_env, "objects", []):
            if getattr(getattr(renderer_obj, "type", None), "name", "") != "SkinnedMeshRenderer":
                continue
            renderer_seen += 1
            if max_renderers and renderer_seen > max_renderers:
                break
            try:
                renderer = renderer_obj.read()
                mesh_id = ptr_id(renderer.m_Mesh)
                if mesh_id not in renderer_objects:
                    continue
                mesh = renderer_objects[mesh_id].read()
                skeleton_nodes, joint_node_indices, _root, _ibm, path_to_node = build_skeleton(renderer_objects, renderer, mesh)
            except Exception:
                continue
            if not skeleton_nodes or not joint_node_indices:
                continue
            for clip_record in clip_records:
                score = score_direct_clip_against_skeleton(clip_record["clip"], path_to_node)
                if score["matched_channel_count"] <= 0 or score["match_ratio"] < min_match_ratio:
                    continue
                candidates.append({
                    "renderer_source": renderer_source,
                    "renderer_env": renderer_env,
                    "renderer_path_id": getattr(renderer_obj, "path_id", 0),
                    "clip_source": clip_record["source"],
                    "clip_env": clip_record["env"],
                    "clip_path_id": clip_record["path_id"],
                    "score": score,
                })
        if max_renderers and renderer_seen > max_renderers:
            break
    candidates.sort(
        key=lambda item: (
            -float(item["score"].get("match_ratio", 0.0)),
            -int(item["score"].get("matched_channel_count", 0)),
            str(item["renderer_source"]).lower(),
            str(item["clip_source"]).lower(),
        )
    )
    return candidates[:max_samples]


def export_auto_direct_samples(
    *,
    source_root: Path,
    study_root: Path,
    max_samples: int,
    min_match_ratio: float,
    max_renderers: int = 0,
) -> list[dict[str, Any]]:
    envs = load_unity_envs(source_root)
    candidates = find_auto_direct_candidates(
        envs,
        max_samples=max_samples,
        min_match_ratio=min_match_ratio,
        max_renderers=max_renderers,
    )
    rows: list[dict[str, Any]] = []
    if not candidates:
        return [{
            "sample_kind": "auto_direct",
            "clip_name": "",
            "renderer_path_id": "",
            "mesh_path_id": "",
            "mesh_name": "",
            "gltf_path": "",
            "bin_path": "",
            "joint_count": "",
            "animation_count": 0,
            "vertex_count": "",
            "index_count": "",
            "material_binding_source": "",
            "animation_origin": "",
            "status": "failed",
            "remarks": "no automatic SkinnedMeshRenderer + direct AnimationClip candidate met the match threshold",
            "clip_channel_count": "",
            "matched_channel_count": "",
            "match_ratio": "",
        }]
    for candidate in candidates:
        score = candidate["score"]
        try:
            row = export_one_sample(
                renderer_env=candidate["renderer_env"],
                clip_env=candidate["clip_env"],
                study_root=study_root,
                renderer_path_id=int(candidate["renderer_path_id"]),
                clip_path_id=int(candidate["clip_path_id"]),
                sample_kind="direct",
                file_label="auto",
            )
            row["sample_kind"] = "auto_direct"
            row["remarks"] = (
                f"auto selected by direct curve skeleton match; renderer_source={Path(candidate['renderer_source']).name}; "
                f"clip_source={Path(candidate['clip_source']).name}"
            )
        except Exception as exc:
            row = {
                "sample_kind": "auto_direct",
                "clip_name": score.get("clip_name", ""),
                "renderer_path_id": candidate.get("renderer_path_id", ""),
                "mesh_path_id": "",
                "mesh_name": "",
                "gltf_path": "",
                "bin_path": "",
                "joint_count": "",
                "animation_count": 0,
                "vertex_count": "",
                "index_count": "",
                "material_binding_source": "",
                "animation_origin": "",
                "status": "failed",
                "remarks": str(exc),
            }
        row["clip_channel_count"] = score.get("clip_channel_count", "")
        row["matched_channel_count"] = score.get("matched_channel_count", "")
        row["match_ratio"] = score.get("match_ratio", "")
        rows.append(row)
    return rows


def write_index(index_dir: Path, rows: Sequence[dict[str, Any]]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "真实动作导出索引.csv"
    fieldnames = [
        "sample_kind",
        "clip_name",
        "renderer_path_id",
        "mesh_path_id",
        "mesh_name",
        "gltf_path",
        "bin_path",
        "joint_count",
        "animation_count",
        "vertex_count",
        "index_count",
        "material_binding_source",
        "animation_origin",
        "status",
        "remarks",
        "clip_channel_count",
        "matched_channel_count",
        "match_ratio",
    ]
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    summary = {
        "schema_version": 1,
        "exported": sum(1 for row in rows if row.get("status") == "exported"),
        "failed": sum(1 for row in rows if row.get("status") != "exported"),
        "index": str(index_path),
        "samples": list(rows),
    }
    (index_dir / "真实动作导出摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", required=True)
    parser.add_argument("--source", type=Path, default=None, help="Authorized Unity game root for automatic candidate selection.")
    parser.add_argument("--auto", action="store_true", help="Automatically select direct AnimationClip samples for skinned meshes.")
    parser.add_argument("--max-samples", type=int, default=1)
    parser.add_argument("--min-match-ratio", type=float, default=0.15)
    parser.add_argument("--max-renderers", type=int, default=0)
    parser.add_argument("--renderer-source")
    parser.add_argument("--direct-clip-source")
    parser.add_argument("--compressed-clip-source")
    parser.add_argument("--renderer-path-id", type=int)
    parser.add_argument("--direct-clip-path-id", type=int)
    parser.add_argument("--compressed-clip-path-id", type=int)
    args = parser.parse_args()

    study_root = Path(args.study_root)
    rows: list[dict[str, Any]] = []
    if args.auto:
        if args.source is None:
            parser.error("--auto requires --source")
        rows.extend(
            export_auto_direct_samples(
                source_root=args.source,
                study_root=study_root,
                max_samples=max(1, args.max_samples),
                min_match_ratio=max(0.0, args.min_match_ratio),
                max_renderers=max(0, args.max_renderers),
            )
        )
    else:
        required = {
            "--renderer-source": args.renderer_source,
            "--direct-clip-source": args.direct_clip_source,
            "--compressed-clip-source": args.compressed_clip_source,
            "--renderer-path-id": args.renderer_path_id,
            "--direct-clip-path-id": args.direct_clip_path_id,
            "--compressed-clip-path-id": args.compressed_clip_path_id,
        }
        missing = [name for name, value in required.items() if value in {None, ""}]
        if missing:
            parser.error("explicit mode requires " + ", ".join(missing))
        renderer_env = UnityPy.load(str(Path(str(args.renderer_source))))
        direct_clip_env = UnityPy.load(str(Path(str(args.direct_clip_source))))
        compressed_clip_env = UnityPy.load(str(Path(str(args.compressed_clip_source))))
        try:
            rows.append(
                export_grouped_samples(
                    renderer_env=renderer_env,
                    direct_clip_env=direct_clip_env,
                    compressed_clip_env=compressed_clip_env,
                    study_root=study_root,
                    renderer_path_id=int(args.renderer_path_id),
                    direct_clip_path_id=int(args.direct_clip_path_id),
                    compressed_clip_path_id=int(args.compressed_clip_path_id),
                )
            )
        except Exception as exc:
            rows.append({
                "sample_kind": "grouped",
                "clip_name": "",
                "renderer_path_id": args.renderer_path_id,
                "mesh_path_id": "",
                "mesh_name": "",
                "gltf_path": "",
                "bin_path": "",
                "joint_count": "",
                "animation_count": 0,
                "vertex_count": "",
                "index_count": "",
                "material_binding_source": "",
                "animation_origin": "",
                "status": "failed",
                "remarks": str(exc),
            })

    index_dir = study_root / "4.临时目录" / "中间索引" / "UnityPy真实动作导出"
    write_index(index_dir, rows)
    print(json.dumps({"status": "ok" if all(row.get("status") == "exported" for row in rows) else "partial", "samples": rows}, ensure_ascii=False))
    return 0 if any(row.get("status") == "exported" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

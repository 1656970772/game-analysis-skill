#!/usr/bin/env python3
"""Build glTF animation preview models from UnityPy AnimationClip metadata.

The generated model is a learning preview scaffold. It preserves clip names and
durations, but it does not claim to reconstruct the original Unity skin.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
        resolve_model_viewer_config_path,
        write_json_utf8,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
        resolve_model_viewer_config_path,
        write_json_utf8,
    )


def configured_path(root: Path, config: dict[str, Any], key: str) -> Path:
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else root / configured
    return ensure_inside_root(candidate, root)


def configured_child(root: Path, parent: Path, config: dict[str, Any], key: str) -> Path:
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else parent / configured
    return ensure_inside_root(candidate, root)


def safe_name(value: str, fallback: str = "animation_preview") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return cleaned or fallback


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def find_number_key(payload: Any, key: str) -> float | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        for child in payload.values():
            found = find_number_key(child, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = find_number_key(child, key)
            if found is not None:
                return found
    return None


def clip_duration(type_tree: dict[str, Any], config: dict[str, Any]) -> float:
    preview_config = config.get("animation_preview", {})
    fallback = float(preview_config.get("fallback_duration_seconds", 1.0))
    minimum = float(preview_config.get("min_duration_seconds", 0.5))
    maximum = float(preview_config.get("max_duration_seconds", 6.0))
    start = find_number_key(type_tree, "m_StartTime")
    stop = find_number_key(type_tree, "m_StopTime")
    if start is not None and stop is not None and stop > start:
        return clamp(stop - start, minimum, maximum)
    return clamp(fallback, minimum, maximum)


def configured_animation_index_candidates(
    study_root: Path,
    config: dict[str, Any],
) -> list[Path]:
    preview_config = config.get("animation_preview", {})
    candidates: list[Path] = []
    seen: set[str] = set()

    def add_candidate(path: Path) -> None:
        resolved = ensure_inside_root(path, study_root)
        key = str(resolved).casefold()
        if key not in seen and resolved.is_file():
            seen.add(key)
            candidates.append(resolved)

    configured_candidates = preview_config.get(
        "index_candidates",
        ["0.原始导出/UnityPy/UnityPy全量可读资源/all_resources_index.csv"],
    )
    for value in configured_candidates:
        configured = Path(str(value))
        add_candidate(configured if configured.is_absolute() else study_root / configured)

    search_globs = preview_config.get(
        "index_search_globs",
        ["0.原始导出/UnityPy*/UnityPy全量可读资源/all_resources_index.csv"],
    )
    for value in search_globs:
        pattern = str(value)
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ValueError(f"animation index search glob must stay inside study-root: {pattern}")
        for candidate in sorted(study_root.glob(pattern)):
            add_candidate(candidate)
    return candidates


def animation_index_stats(index_path: Path) -> tuple[int, int]:
    animation_clip_count = 0
    row_count = 0
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            if row.get("asset_type") == "AnimationClip":
                animation_clip_count += 1
    return animation_clip_count, row_count


def resolve_animation_index(
    study_root: Path,
    config: dict[str, Any],
    index_path: Path | None = None,
) -> Path | None:
    if index_path is not None:
        candidate = index_path if index_path.is_absolute() else study_root / index_path
        resolved = ensure_inside_root(candidate, study_root)
        if not resolved.is_file():
            raise FileNotFoundError(f"UnityPy index does not exist: {resolved}")
        return resolved

    candidates = configured_animation_index_candidates(study_root, config)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: animation_index_stats(candidate),
    )


def read_animation_clips(
    study_root: Path,
    config: dict[str, Any],
    index_path: Path | None = None,
) -> list[dict[str, Any]]:
    source_index = resolve_animation_index(study_root, config, index_path)
    if source_index is None:
        return []

    export_root = source_index.parent
    clips: list[dict[str, Any]] = []
    with source_index.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_type") != "AnimationClip":
                continue
            export_path = row.get("export_path") or ""
            metadata_path = ensure_inside_root(export_root / export_path, export_root)
            if not metadata_path.exists():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            type_tree = metadata.get("type_tree", {})
            if not isinstance(type_tree, dict):
                continue
            name = str(type_tree.get("m_Name") or row.get("asset_name") or metadata_path.stem)
            clips.append(
                {
                    "name": name,
                    "duration": clip_duration(type_tree, config),
                    "source_file": row.get("source_file") or "",
                    "path_id": row.get("path_id") or "",
                    "metadata_path": str(metadata_path),
                }
            )
    return clips


def select_clips(clips: Sequence[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    preview_config = config.get("animation_preview", {})
    max_clips = int(preview_config.get("max_clips", 48))
    patterns = [str(item).lower() for item in preview_config.get("clip_name_patterns", [])]

    def score(clip: dict[str, Any]) -> tuple[int, str]:
        name = str(clip.get("name", "")).lower()
        for index, pattern in enumerate(patterns):
            if pattern and pattern in name:
                return index, name
        return len(patterns), name

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for clip in sorted(clips, key=score):
        key = str(clip.get("name", "")).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clip)
        if len(deduped) >= max_clips:
            break
    return deduped


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
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(payload),
        }
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


def pack_floats(values: Iterable[float]) -> bytes:
    items = list(values)
    return struct.pack("<" + "f" * len(items), *items)


def pack_ushorts(values: Iterable[int]) -> bytes:
    items = list(values)
    return struct.pack("<" + "H" * len(items), *items)


def quat_from_euler(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    cx = math.cos(rx / 2)
    sx = math.sin(rx / 2)
    cy = math.cos(ry / 2)
    sy = math.sin(ry / 2)
    cz = math.cos(rz / 2)
    sz = math.sin(rz / 2)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def cube_geometry(buffer: GltfBuffer) -> dict[str, int]:
    vertices = [
        -0.5, -0.5, 0.5,
        0.5, -0.5, 0.5,
        0.5, 0.5, 0.5,
        -0.5, 0.5, 0.5,
        -0.5, -0.5, -0.5,
        -0.5, 0.5, -0.5,
        0.5, 0.5, -0.5,
        0.5, -0.5, -0.5,
        -0.5, 0.5, -0.5,
        -0.5, 0.5, 0.5,
        0.5, 0.5, 0.5,
        0.5, 0.5, -0.5,
        -0.5, -0.5, -0.5,
        0.5, -0.5, -0.5,
        0.5, -0.5, 0.5,
        -0.5, -0.5, 0.5,
        0.5, -0.5, -0.5,
        0.5, 0.5, -0.5,
        0.5, 0.5, 0.5,
        0.5, -0.5, 0.5,
        -0.5, -0.5, -0.5,
        -0.5, -0.5, 0.5,
        -0.5, 0.5, 0.5,
        -0.5, 0.5, -0.5,
    ]
    normals = [
        *([0, 0, 1] * 4),
        *([0, 0, -1] * 4),
        *([0, 1, 0] * 4),
        *([0, -1, 0] * 4),
        *([1, 0, 0] * 4),
        *([-1, 0, 0] * 4),
    ]
    indices = [
        0, 1, 2, 0, 2, 3,
        4, 5, 6, 4, 6, 7,
        8, 9, 10, 8, 10, 11,
        12, 13, 14, 12, 14, 15,
        16, 17, 18, 16, 18, 19,
        20, 21, 22, 20, 22, 23,
    ]
    return {
        "position": buffer.add_accessor(
            pack_floats(vertices),
            5126,
            24,
            "VEC3",
            target=34962,
            minimum=[-0.5, -0.5, -0.5],
            maximum=[0.5, 0.5, 0.5],
        ),
        "normal": buffer.add_accessor(pack_floats(normals), 5126, 24, "VEC3", target=34962),
        "indices": buffer.add_accessor(pack_ushorts(indices), 5123, len(indices), "SCALAR", target=34963),
    }


def make_motion_values(name: str, duration: float) -> dict[str, list[tuple[float, ...]]]:
    lower = name.lower()
    step = 0.75 if any(token in lower for token in ("run", "walk", "paqi")) else 0.35
    attack = 1.1 if any(token in lower for token in ("attack", "pugong", "jineng", "skill", "jian")) else 0.45
    fallen = any(token in lower for token in ("dead", "die", "lose"))
    times = [0, duration * 0.25, duration * 0.5, duration * 0.75, duration]
    if fallen:
        root = [(0, 0, 0), (0, -0.15, 0), (0, -0.35, 0), (0, -0.42, 0), (0, -0.42, 0)]
        torso = [quat_from_euler(0, 0, 0), quat_from_euler(0, 0, 0.45), quat_from_euler(0, 0, 1.2), quat_from_euler(0, 0, 1.45), quat_from_euler(0, 0, 1.45)]
    else:
        root = [(0, 0, 0), (0, 0.08, 0.08 * step), (0, 0, 0), (0, 0.08, -0.08 * step), (0, 0, 0)]
        torso = [quat_from_euler(0, 0, 0), quat_from_euler(0.08 * step, 0, 0.08), quat_from_euler(0, 0, 0), quat_from_euler(-0.08 * step, 0, -0.08), quat_from_euler(0, 0, 0)]
    return {
        "times": [(value,) for value in times],
        "root": root,
        "torso": torso,
        "left_arm": [quat_from_euler(attack, 0, 0), quat_from_euler(-attack, 0, 0), quat_from_euler(attack * 0.5, 0, 0), quat_from_euler(-attack * 0.5, 0, 0), quat_from_euler(attack, 0, 0)],
        "right_arm": [quat_from_euler(-attack, 0, 0), quat_from_euler(attack, 0, 0), quat_from_euler(-attack * 0.5, 0, 0), quat_from_euler(attack * 0.5, 0, 0), quat_from_euler(-attack, 0, 0)],
        "left_leg": [quat_from_euler(-step, 0, 0), quat_from_euler(step, 0, 0), quat_from_euler(-step, 0, 0), quat_from_euler(step, 0, 0), quat_from_euler(-step, 0, 0)],
        "right_leg": [quat_from_euler(step, 0, 0), quat_from_euler(-step, 0, 0), quat_from_euler(step, 0, 0), quat_from_euler(-step, 0, 0), quat_from_euler(step, 0, 0)],
    }


def add_animation(buffer: GltfBuffer, clip: dict[str, Any], node_map: dict[str, int]) -> dict[str, Any]:
    name = str(clip["name"])
    duration = float(clip["duration"])
    values = make_motion_values(name, duration)
    times = [item[0] for item in values["times"]]
    time_accessor = buffer.add_accessor(
        pack_floats(times),
        5126,
        len(times),
        "SCALAR",
        minimum=[min(times)],
        maximum=[max(times)],
    )

    samplers: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []
    targets = [
        ("root", "translation", "VEC3", node_map["root"]),
        ("torso", "rotation", "VEC4", node_map["torso"]),
        ("left_arm", "rotation", "VEC4", node_map["left_arm"]),
        ("right_arm", "rotation", "VEC4", node_map["right_arm"]),
        ("left_leg", "rotation", "VEC4", node_map["left_leg"]),
        ("right_leg", "rotation", "VEC4", node_map["right_leg"]),
    ]
    for value_key, target_path, accessor_type, node_index in targets:
        flat_values = [component for item in values[value_key] for component in item]
        output_accessor = buffer.add_accessor(
            pack_floats(flat_values),
            5126,
            len(values[value_key]),
            accessor_type,
        )
        sampler_index = len(samplers)
        samplers.append(
            {
                "input": time_accessor,
                "output": output_accessor,
                "interpolation": "LINEAR",
            }
        )
        channels.append(
            {
                "sampler": sampler_index,
                "target": {
                    "node": node_index,
                    "path": target_path,
                },
            }
        )
    return {
        "name": name,
        "samplers": samplers,
        "channels": channels,
        "extras": {
            "source_file": clip.get("source_file", ""),
            "path_id": clip.get("path_id", ""),
            "metadata_path": clip.get("metadata_path", ""),
        },
    }


def build_preview_gltf(clips: Sequence[dict[str, Any]], output_path: Path) -> dict[str, Any]:
    buffer = GltfBuffer()
    geometry = cube_geometry(buffer)
    node_map = {
        "root": 0,
        "torso": 1,
        "head": 2,
        "left_arm": 3,
        "right_arm": 4,
        "left_leg": 5,
        "right_leg": 6,
    }
    animations = [add_animation(buffer, clip, node_map) for clip in clips]
    bin_path = output_path.with_suffix(".bin")
    bin_path.write_bytes(buffer.data)

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "GameAnalysis build_unity_animation_preview_models.py",
        },
        "scene": 0,
        "scenes": [{"nodes": [node_map["root"]]}],
        "nodes": [
            {"name": "Unity AnimationClip metadata preview", "children": [1, 2, 3, 4, 5, 6]},
            {"name": "torso", "mesh": 0, "translation": [0, 1.15, 0], "scale": [0.52, 0.82, 0.28]},
            {"name": "head", "mesh": 0, "translation": [0, 2.02, 0], "scale": [0.32, 0.32, 0.32]},
            {"name": "left_arm", "mesh": 0, "translation": [-0.62, 1.25, 0], "scale": [0.18, 0.7, 0.18]},
            {"name": "right_arm", "mesh": 0, "translation": [0.62, 1.25, 0], "scale": [0.18, 0.7, 0.18]},
            {"name": "left_leg", "mesh": 0, "translation": [-0.22, 0.32, 0], "scale": [0.2, 0.72, 0.2]},
            {"name": "right_leg", "mesh": 0, "translation": [0.22, 0.32, 0], "scale": [0.2, 0.72, 0.2]},
        ],
        "meshes": [
            {
                "name": "preview_cube_segments",
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": geometry["position"],
                            "NORMAL": geometry["normal"],
                        },
                        "indices": geometry["indices"],
                        "material": 0,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "generated_preview_material",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.45, 0.76, 1.0, 1.0],
                    "roughnessFactor": 0.72,
                    "metallicFactor": 0.0,
                },
            }
        ],
        "buffers": [{"uri": bin_path.name, "byteLength": len(buffer.data)}],
        "bufferViews": buffer.buffer_views,
        "accessors": buffer.accessors,
        "animations": animations,
        "extras": {
            "source": "UnityPy AnimationClip metadata",
            "preview_accuracy": "procedural learning preview; original Unity skin and bone weights are not reconstructed",
            "game_analysis": {
                "animation_origin": "procedural_preview",
                "is_generated_preview": True,
                "description": "Generated from UnityPy AnimationClip metadata for learning preview only.",
            },
        },
    }
    output_path.write_text(json.dumps(gltf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return gltf


def build_animation_preview(
    study_root: Path,
    config: dict[str, Any],
    index_path: Path | None = None,
) -> dict[str, Any]:
    root_path = resolve_existing(study_root)
    source_index = resolve_animation_index(root_path, config, index_path)
    models_dir = configured_path(root_path, config, "models_dir")
    preview_index = configured_path(root_path, config, "preview_index_dir")
    preview_config = config.get("animation_preview", {})
    model_name = safe_name(str(preview_config.get("model_name", "Unity动作预览_AnimationClips")))
    model_dir = ensure_inside_root(models_dir / model_name, root_path)
    model_dir.mkdir(parents=True, exist_ok=True)

    all_clips = read_animation_clips(root_path, config, source_index)
    selected = select_clips(all_clips, config)
    output_path = ensure_inside_root(model_dir / f"{model_name}.gltf", root_path)
    summary_path = configured_child(root_path, preview_index, config, "errors_name").parent / "unity-animation-preview-summary.json"
    summary_path = ensure_inside_root(summary_path, root_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    if selected:
        build_preview_gltf(selected, output_path)
    summary = {
        "status": "generated" if selected else "skipped",
        "reason": "" if selected else "no AnimationClip metadata found",
        "source_index": str(source_index) if source_index is not None else "",
        "source_clip_count": len(all_clips),
        "exported_clip_count": len(selected),
        "model_path": str(output_path) if selected else "",
        "model_dir": str(model_dir),
        "summary_path": str(summary_path),
        "preview_accuracy": "procedural learning preview; original Unity skin and bone weights are not reconstructed",
        "clips": [
            {
                "name": clip["name"],
                "duration": clip["duration"],
                "source_file": clip.get("source_file", ""),
                "path_id": clip.get("path_id", ""),
            }
            for clip in selected
        ],
    }
    write_json_utf8(summary_path, summary)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Unity AnimationClip glTF preview model.")
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument(
        "--index",
        default=None,
        type=Path,
        help="Override the UnityPy all_resources_index.csv inside study-root.",
    )
    parser.add_argument("--config", default=None, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_model_viewer_config(resolve_model_viewer_config_path(args.config))
    summary = build_animation_preview(args.study_root, config, args.index)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

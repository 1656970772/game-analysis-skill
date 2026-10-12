#!/usr/bin/env python3
"""Blender-side worker for model preview thumbnail rendering."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector


DEFAULT_THUMBNAIL = {
    "width": 320,
    "height": 240,
    "background": "#20242a",
    "camera_yaw_degrees": 35,
    "camera_pitch_degrees": 22,
    "padding": 1.25,
    "light_type": "AREA",
    "light_energy": 450,
    "light_size": 5,
}


def resolve_existing(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def ensure_inside_root(path: Path, root: Path) -> Path:
    resolved_path = resolve_existing(path)
    resolved_root = resolve_existing(root)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path outside study-root: {resolved_path}") from exc
    return resolved_path


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_model(source_path: Path) -> None:
    suffix = source_path.suffix.lower()
    if suffix in {".gltf", ".glb"}:
        bpy.ops.import_scene.gltf(filepath=str(source_path))
        return
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source_path))
        return
    raise ValueError(f"unsupported model format: {suffix.lstrip('.')}")


def hex_to_rgba(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(channel * 2 for channel in text)
    if len(text) != 6:
        raise ValueError(f"invalid background color: {value}")
    red = int(text[0:2], 16) / 255.0
    green = int(text[2:4], 16) / 255.0
    blue = int(text[4:6], 16) / 255.0
    return red, green, blue, alpha


def _mesh_objects() -> list[Any]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def _scene_bounds() -> tuple[Vector, float]:
    points: list[Vector] = []
    for obj in _mesh_objects():
        matrix = obj.matrix_world
        points.extend(matrix @ Vector(corner) for corner in obj.bound_box)

    if not points:
        return Vector((0.0, 0.0, 0.0)), 1.0

    min_corner = Vector(
        (
            min(point.x for point in points),
            min(point.y for point in points),
            min(point.z for point in points),
        )
    )
    max_corner = Vector(
        (
            max(point.x for point in points),
            max(point.y for point in points),
            max(point.z for point in points),
        )
    )
    center = (min_corner + max_corner) * 0.5
    radius = max((point - center).length for point in points)
    return center, max(radius, 0.5)


def _add_light(center: Vector, radius: float, thumbnail: dict[str, Any]) -> None:
    light_type = str(thumbnail.get("light_type", DEFAULT_THUMBNAIL["light_type"])).upper()
    light = bpy.data.lights.new(name="Preview Light", type=light_type)
    light.energy = float(thumbnail.get("light_energy", DEFAULT_THUMBNAIL["light_energy"]))
    if hasattr(light, "size"):
        light.size = float(thumbnail.get("light_size", DEFAULT_THUMBNAIL["light_size"]))

    light_object = bpy.data.objects.new("Preview Light", light)
    bpy.context.collection.objects.link(light_object)
    light_object.location = center + Vector((radius * 1.5, -radius * 2.0, radius * 2.0))


def frame_camera(thumbnail: dict[str, Any]) -> None:
    center, radius = _scene_bounds()
    padding = float(thumbnail.get("padding", DEFAULT_THUMBNAIL["padding"]))
    yaw = math.radians(float(thumbnail.get("camera_yaw_degrees", DEFAULT_THUMBNAIL["camera_yaw_degrees"])))
    pitch = math.radians(float(thumbnail.get("camera_pitch_degrees", DEFAULT_THUMBNAIL["camera_pitch_degrees"])))
    distance = max(radius * padding * 3.0, 2.0)

    direction = Vector(
        (
            math.cos(pitch) * math.sin(yaw),
            -math.cos(pitch) * math.cos(yaw),
            math.sin(pitch),
        )
    )
    camera_location = center + direction.normalized() * distance

    camera_data = bpy.data.cameras.new("Preview Camera")
    camera = bpy.data.objects.new("Preview Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = camera_location
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = 55
    camera.data.clip_end = max(distance * 10.0, 1000.0)
    bpy.context.scene.camera = camera

    _add_light(center, radius, thumbnail)


def render_thumbnail(thumbnail_path: Path, thumbnail: dict[str, Any]) -> None:
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.resolution_x = int(thumbnail.get("width", DEFAULT_THUMBNAIL["width"]))
    scene.render.resolution_y = int(thumbnail.get("height", DEFAULT_THUMBNAIL["height"]))
    scene.render.film_transparent = False

    background = thumbnail.get("background", DEFAULT_THUMBNAIL["background"])
    scene.world = scene.world or bpy.data.worlds.new("Preview World")
    scene.world.color = hex_to_rgba(str(background))[:3]

    frame_camera(thumbnail)
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(thumbnail_path)
    bpy.ops.render.render(write_still=True)


def export_glb(preview_path: Path) -> None:
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(preview_path), export_format="GLB")


def process_job(job: dict[str, Any], thumbnail: dict[str, Any], study_root: Path | None = None) -> dict[str, Any]:
    source_path = Path(str(job["source_path"]))
    thumbnail_path = Path(str(job["thumbnail_path"]))
    preview_path = Path(str(job["preview_path"]))
    try:
        if study_root is not None:
            thumbnail_path = ensure_inside_root(thumbnail_path, study_root)
            if bool(job.get("export_glb")):
                preview_path = ensure_inside_root(preview_path, study_root)
        clear_scene()
        import_model(source_path)
        render_thumbnail(thumbnail_path, thumbnail)
        if bool(job.get("export_glb")):
            export_glb(preview_path)
        return {
            "id": str(job["id"]),
            "success": True,
            "source_path": str(source_path),
            "thumbnail_path": str(thumbnail_path),
            "preview_path": str(preview_path),
            "export_glb": bool(job.get("export_glb")),
        }
    except Exception as exc:  # Blender import/export exceptions are not stable by type.
        return {
            "id": str(job.get("id", "")),
            "success": False,
            "source_path": str(source_path),
            "thumbnail_path": str(thumbnail_path),
            "preview_path": str(preview_path),
            "export_glb": bool(job.get("export_glb")),
            "error": str(exc),
        }


def _worker_argv(argv: list[str] | None = None) -> list[str]:
    values = list(sys.argv if argv is None else argv)
    if "--" in values:
        return values[values.index("--") + 1 :]
    return values[1:] if argv is None else values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Blender model previews.")
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    return parser.parse_args(_worker_argv(argv))


def _read_jobs(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    study_root = None
    if isinstance(payload, dict):
        jobs = payload.get("jobs", [])
        thumbnail_config = payload.get("thumbnail", {})
        if payload.get("study_root"):
            study_root = Path(str(payload["study_root"]))
        if not isinstance(thumbnail_config, dict):
            thumbnail_config = {}
        thumbnail = {**DEFAULT_THUMBNAIL, **thumbnail_config}
    else:
        jobs = payload
        thumbnail = dict(DEFAULT_THUMBNAIL)
    if not isinstance(jobs, list):
        raise ValueError("jobs payload must be a list")
    return [job for job in jobs if isinstance(job, dict)], thumbnail, study_root


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    jobs, thumbnail, study_root = _read_jobs(args.jobs)
    results = [process_job(job, thumbnail, study_root=study_root) for job in jobs]
    result_path = args.results
    if study_root is not None:
        result_path = ensure_inside_root(result_path, study_root)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if all(result.get("success") for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

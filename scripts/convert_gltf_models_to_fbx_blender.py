#!/usr/bin/env python
"""Blender batch conversion from glTF model folders to FBX."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import bpy


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", default=os.environ.get("GAME_ANALYSIS_MODEL_ROOT", ""))
    parser.add_argument("--index-dir", default=os.environ.get("GAME_ANALYSIS_MODEL_INDEX_DIR", ""))
    parser.add_argument("--limit", type=int, default=0)
    args, _unknown = parser.parse_known_args()

    if not args.model_root or not args.index_dir:
        raise SystemExit("model-root and index-dir are required via args or GAME_ANALYSIS_MODEL_ROOT/GAME_ANALYSIS_MODEL_INDEX_DIR")

    model_root = Path(args.model_root)
    index_dir = Path(args.index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    gltf_files = sorted(model_root.glob("*/*.gltf"))
    converted = 0
    failed = 0

    for gltf_path in gltf_files:
        if args.limit and converted >= args.limit:
            break
        fbx_path = gltf_path.with_suffix(".fbx")
        try:
            clear_scene()
            bpy.ops.import_scene.gltf(filepath=str(gltf_path))
            bpy.ops.export_scene.fbx(
                filepath=str(fbx_path),
                use_selection=False,
                add_leaf_bones=False,
                path_mode="RELATIVE",
                embed_textures=False,
            )
            converted += 1
            rows.append({
                "model": gltf_path.parent.name,
                "gltf_path": str(gltf_path),
                "fbx_path": str(fbx_path),
                "status": "converted",
                "remarks": "Converted by Blender from glTF.",
            })
        except Exception as exc:
            failed += 1
            rows.append({
                "model": gltf_path.parent.name,
                "gltf_path": str(gltf_path),
                "fbx_path": str(fbx_path),
                "status": "failed",
                "remarks": str(exc),
            })

    with (index_dir / "gltf_to_fbx转换索引.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["model", "gltf_path", "fbx_path", "status", "remarks"])
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "model_root": str(model_root),
        "gltf_count": len(gltf_files),
        "converted": converted,
        "failed": failed,
        "notes": ["FBX files are generated from glTF with Blender and kept next to each glTF."],
    }
    (index_dir / "gltf_to_fbx转换摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if converted else 1


if __name__ == "__main__":
    raise SystemExit(main())

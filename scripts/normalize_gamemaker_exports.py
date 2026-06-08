#!/usr/bin/env python3
"""Normalize UndertaleModTool GameMaker exports into study indexes.

The script only reads an existing local export directory and writes CSV/JSON
indexes. It does not open, patch, or save the original GameMaker data file.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Iterable

from PIL import Image


IMAGE_EXTENSIONS = {".png", ".bmp", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".ogg", ".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a"}
FRAME_SUFFIX_RE = re.compile(r"_(\d+)\.[^.]+$")


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def frame_index(path: Path) -> int:
    match = FRAME_SUFFIX_RE.search(path.name)
    if match:
        return int(match.group(1))
    return 0


def category_for_image(path: Path, exports_root: Path) -> str:
    parts = path.relative_to(exports_root).parts
    if not parts:
        return "image"
    if parts[0] == "Sprites":
        return "sprite_frame"
    if parts[0] == "EmbeddedTextures":
        return "embedded_texture"
    if len(parts) >= 2 and parts[0] == "TextureItems":
        folder = parts[1].lower()
        if folder == "backgrounds":
            return "background"
        if folder == "fonts":
            return "font"
        if folder == "sprites":
            return "texture_sprite_frame"
    return "image"


def iter_files(root: Path, extensions: set[str]) -> Iterable[Path]:
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules"}]
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() in extensions:
                yield path


def image_rows(exports_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(iter_files(exports_root, IMAGE_EXTENSIONS), key=lambda p: rel(p, exports_root).lower()):
        with Image.open(path) as img:
            mode = img.mode
            has_alpha = mode in {"RGBA", "LA"} or ("transparency" in img.info)
            width, height = img.size
        rows.append(
            {
                "relative_path": rel(path, exports_root),
                "name": path.name,
                "category": category_for_image(path, exports_root),
                "width": str(width),
                "height": str(height),
                "mode": mode,
                "has_alpha": "true" if has_alpha else "false",
                "size_bytes": str(path.stat().st_size),
            }
        )
    return rows


def sprite_frame_rows(exports_root: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    frames = [row for row in rows if row["category"] == "sprite_frame"]
    frames.sort(key=lambda row: (Path(row["relative_path"]).parent.as_posix().lower(), frame_index(Path(row["relative_path"]))))
    output: list[dict[str, str]] = []
    for row in frames:
        path = Path(row["relative_path"])
        sprite_name = path.parent.name
        output.append(
            {
                "sprite_name": sprite_name,
                "frame_index": str(frame_index(path)),
                "relative_path": row["relative_path"],
                "width": row["width"],
                "height": row["height"],
                "has_alpha": row["has_alpha"],
                "size_bytes": row["size_bytes"],
            }
        )
    return output


def animation_rows(frame_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in frame_rows:
        grouped.setdefault(row["sprite_name"], []).append(row)

    animations: list[dict[str, str]] = []
    for sprite_name, frames in sorted(grouped.items()):
        frames.sort(key=lambda row: int(row["frame_index"]))
        widths = [int(row["width"]) for row in frames]
        heights = [int(row["height"]) for row in frames]
        total_area = sum(int(row["width"]) * int(row["height"]) for row in frames)
        animations.append(
            {
                "sprite_name": sprite_name,
                "frame_count": str(len(frames)),
                "first_frame": frames[0]["relative_path"],
                "width_min": str(min(widths)),
                "width_max": str(max(widths)),
                "height_min": str(min(heights)),
                "height_max": str(max(heights)),
                "total_area": str(total_area),
            }
        )
    return animations


def audio_rows(exports_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(iter_files(exports_root / "Sounds", AUDIO_EXTENSIONS), key=lambda p: rel(p, exports_root).lower()):
        rows.append(
            {
                "relative_path": rel(path, exports_root),
                "name": path.name,
                "extension": path.suffix.lower(),
                "group": path.parent.name,
                "size_bytes": str(path.stat().st_size),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_exports(exports_root: Path | str, output_dir: Path | str, title: str = "") -> dict[str, object]:
    exports_root = Path(exports_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    images = image_rows(exports_root)
    frames = sprite_frame_rows(exports_root, images)
    animations = animation_rows(frames)
    audio = audio_rows(exports_root)

    write_csv(
        output_dir / "gamemaker_image_index.csv",
        images,
        ["relative_path", "name", "category", "width", "height", "mode", "has_alpha", "size_bytes"],
    )
    write_csv(
        output_dir / "gamemaker_sprite_frame_index.csv",
        frames,
        ["sprite_name", "frame_index", "relative_path", "width", "height", "has_alpha", "size_bytes"],
    )
    write_csv(
        output_dir / "gamemaker_animation_index.csv",
        animations,
        ["sprite_name", "frame_count", "first_frame", "width_min", "width_max", "height_min", "height_max", "total_area"],
    )
    write_csv(output_dir / "gamemaker_audio_index.csv", audio, ["relative_path", "name", "extension", "group", "size_bytes"])

    summary: dict[str, object] = {
        "title": title,
        "exports_root": str(exports_root),
        "image_count": len(images),
        "sprite_frame_count": len(frames),
        "sprite_animation_count": len(animations),
        "audio_count": len(audio),
        "categories": {},
    }
    category_counts: dict[str, int] = {}
    for row in images:
        category_counts[row["category"]] = category_counts.get(row["category"], 0) + 1
    summary["categories"] = category_counts
    (output_dir / "gamemaker_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize UndertaleModTool GameMaker export folders into CSV/JSON indexes.")
    parser.add_argument("--exports-root", required=True, help="Root directory containing UTMT export folders such as Sprites and TextureItems.")
    parser.add_argument("--output-dir", required=True, help="Directory where normalized CSV/JSON indexes are written.")
    parser.add_argument("--title", default="", help="Optional study title stored in summary JSON.")
    args = parser.parse_args()
    summary = normalize_exports(args.exports_root, args.output_dir, args.title)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

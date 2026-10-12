from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageColor, ImageDraw, ImageOps

try:
    import image_gallery_common as gallery_common
except ModuleNotFoundError:
    from scripts import image_gallery_common as gallery_common


DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]


def iter_image_files(root: Path, extensions: Iterable[str]) -> list[Path]:
    allowed = {extension.lower() for extension in extensions}
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in allowed
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


def category_for(relative_path: Path) -> str:
    if len(relative_path.parts) <= 1:
        return "未分类"
    return relative_path.parts[0]


def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.name


def safe_resolved_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError, ValueError):
        return str(path)


def checkerboard(
    size: tuple[int, int],
    dark: str,
    light: str,
    cell: int,
) -> Image.Image:
    dark_rgba = (*ImageColor.getrgb(dark)[:3], 255)
    light_rgba = (*ImageColor.getrgb(light)[:3], 255)
    cell = max(1, cell)
    board = Image.new("RGBA", size, light_rgba)
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=dark_rgba)
    return board


def make_thumbnail(
    image_path: Path,
    thumbnail_path: Path,
    config: dict[str, Any],
) -> tuple[int, int, str, bool]:
    thumbnail_config = config["thumbnail"]
    max_size = thumbnail_config["max_size"]
    with Image.open(image_path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        width, height = image.size
        mode = image.mode
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        thumbnail = image.convert("RGBA")

    thumbnail.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    if has_alpha:
        output = checkerboard(
            thumbnail.size,
            thumbnail_config["checker_dark"],
            thumbnail_config["checker_light"],
            thumbnail_config["checker_cell"],
        )
        output.alpha_composite(thumbnail)
    else:
        output = thumbnail.convert("RGB")

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(thumbnail_path, format=thumbnail_config["format"])
    return width, height, mode, has_alpha


def image_item(
    image_path: Path,
    source_root: Path,
    source_label: str,
    study_root: Path,
    thumbnails_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    relative_path = image_path.resolve().relative_to(source_root.resolve())
    relative_posix = relative_path.as_posix()
    stat = image_path.stat()
    size_bytes = stat.st_size
    item_id = gallery_common.stable_id(source_label, relative_posix, size_bytes)
    thumbnail_path = thumbnails_dir / f"{item_id}.png"
    width, height, mode, has_alpha = make_thumbnail(image_path, thumbnail_path, config)
    resolved_image_path = str(image_path.resolve())

    return {
        "id": item_id,
        "source_label": source_label,
        "source_path": resolved_image_path,
        "path": resolved_image_path,
        "study_relative_path": image_path.resolve().relative_to(study_root.resolve()).as_posix()
        if gallery_common.ensure_inside(image_path, study_root)
        else "",
        "relative_path": relative_posix,
        "category": category_for(relative_path),
        "name": image_path.name,
        "file_name": image_path.name,
        "extension": image_path.suffix.lower(),
        "size_bytes": size_bytes,
        "modified_time": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "width": width,
        "height": height,
        "mode": mode,
        "has_alpha": has_alpha,
        "thumbnail_path": str(thumbnail_path.resolve()),
        "status": "ready",
    }


def build_manifest(
    study_root: Path | str,
    skill_root: Path | str = DEFAULT_SKILL_ROOT,
    extra_image_roots: Iterable[Path | str] | None = None,
    title: str | None = None,
    *,
    extra_roots: list[Path] | None = None,
) -> dict[str, Any]:
    study_root = Path(study_root).resolve()
    skill_root = Path(skill_root)
    config = gallery_common.load_config(skill_root)
    paths = gallery_common.gallery_paths(study_root, config)
    if extra_image_roots is None:
        extra_image_roots = extra_roots or []
    extra_root_paths = [Path(root) for root in extra_image_roots]
    roots = gallery_common.resolve_image_roots(study_root, config, extra_root_paths)

    paths.thumbnails_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    source_roots: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for source_label, source_root in roots:
        source_count = 0
        try:
            image_paths = iter_image_files(source_root, config["image_extensions"])
        except Exception as exc:
            errors.append(
                {
                    "source_label": source_label,
                    "source_path": safe_resolved_path(source_root),
                    "path": safe_resolved_path(source_root),
                    "relative_path": "",
                    "file_name": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            source_roots.append(
                {
                    "label": source_label,
                    "path": safe_resolved_path(source_root),
                    "item_count": source_count,
                }
            )
            continue

        for image_path in image_paths:
            try:
                item = image_item(
                    image_path,
                    source_root,
                    source_label,
                    study_root,
                    paths.thumbnails_dir,
                    config,
                )
            except Exception as exc:
                errors.append(
                    {
                        "source_label": source_label,
                        "source_path": safe_resolved_path(source_root),
                        "path": safe_resolved_path(image_path),
                        "relative_path": safe_relative_path(image_path, source_root),
                        "file_name": image_path.name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            items.append(item)
            source_count += 1

        source_roots.append(
            {
                "label": source_label,
                "path": safe_resolved_path(source_root),
                "item_count": source_count,
            }
        )

    manifest = {
        "schema_version": config["manifest"]["schema_version"],
        "tool": config["tool"],
        "study_root": str(study_root),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "title": title or f"{study_root.name} 图片画廊",
        "ui": config["ui"],
        "source_roots": source_roots,
        "items": items,
        "summary": {
            "total_roots": len(source_roots),
            "total_items": len(items),
            "error_count": len(errors),
            "item_count": len(items),
            "failed_count": len(errors),
            "thumbnail_count_created": len(items),
        },
    }

    max_errors = config["manifest"]["max_error_records"]
    paths.errors_jsonl.parent.mkdir(parents=True, exist_ok=True)
    paths.errors_jsonl.write_text(
        "".join(json.dumps(error, ensure_ascii=False) + "\n" for error in errors[:max_errors]),
        encoding="utf-8",
    )
    gallery_common.write_json(paths.manifest, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the GameAnalysis image gallery manifest.")
    parser.add_argument("study_root", nargs="?", type=Path, help="GameAnalysis study project root.")
    parser.add_argument(
        "--study-root",
        dest="study_root_option",
        type=Path,
        help="GameAnalysis study project root.",
    )
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=DEFAULT_SKILL_ROOT,
        help="Skill root containing references/image-gallery-viewer-config.json.",
    )
    parser.add_argument(
        "--image-root",
        "--extra-root",
        dest="image_roots",
        action="append",
        type=Path,
        default=[],
        help="Additional image root to scan. Can be provided multiple times.",
    )
    parser.add_argument("--title", help="Manifest display title.")
    args = parser.parse_args(argv)
    study_root = args.study_root_option or args.study_root
    if study_root is None:
        parser.error("study_root positional argument or --study-root is required")
    build_manifest(study_root, args.skill_root, args.image_roots, args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

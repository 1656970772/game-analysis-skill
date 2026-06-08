#!/usr/bin/env python3
"""Create a read-only asset inventory from local files.

The script never downloads, unpacks, extracts, or parses archive contents.
It only records filenames, extensions, sizes, and shallow categories.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "Library",
    "Temp",
    "obj",
    "bin",
}

CATEGORY_EXTENSIONS = {
    "images": {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".tga",
        ".psd",
        ".ase",
        ".aseprite",
        ".svg",
    },
    "audio": {".wav", ".ogg", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".xm", ".mod"},
    "video": {".mp4", ".mov", ".webm", ".avi", ".mkv"},
    "models": {".fbx", ".obj", ".gltf", ".glb", ".blend", ".dae", ".3ds"},
    "fonts": {".ttf", ".otf", ".woff", ".woff2", ".fnt"},
    "scripts": {".cs", ".js", ".ts", ".lua", ".gd", ".py", ".rpy", ".h", ".cpp", ".shader"},
    "shaders": {".shader", ".hlsl", ".glsl", ".cginc", ".compute", ".gdshader"},
    "engine-assets": {
        ".unity",
        ".prefab",
        ".mat",
        ".asset",
        ".uasset",
        ".umap",
        ".tscn",
        ".tres",
        ".res",
        ".yy",
        ".yyp",
        ".xnb",
    },
    "packages-archives": {
        ".pak",
        ".pck",
        ".rpa",
        ".bundle",
        ".assets",
        ".resource",
        ".unity3d",
        ".love",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bank",
        ".bytes",
    },
    "game-engine-packages": {
        ".win",
        ".ios",
        ".unx",
        ".droid",
    },
    "documents": {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv", ".json", ".xml", ".yaml", ".yml"},
    "config": {".ini", ".cfg", ".toml", ".plist", ".properties"},
}


def categorize(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("audiogroup") and path.suffix.lower() == ".dat":
        return "game-engine-packages"
    suffix = path.suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if suffix in extensions:
            return category
    return "other"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def iter_files(root: Path, max_depth: int, max_files: int) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    root_parts = len(root.resolve().parts)
    yielded = 0
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.resolve().parts) - root_parts
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and depth < max_depth]
        for name in files:
            yield current_path / name
            yielded += 1
            if yielded >= max_files:
                return


def inventory(root: Path, max_depth: int, max_files: int, largest: int) -> dict[str, object]:
    root = root.resolve()
    category_counts: Counter[str] = Counter()
    category_sizes: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    extension_sizes: Counter[str] = Counter()
    top_files: list[dict[str, object]] = []
    packaged_files: list[dict[str, object]] = []
    total_size = 0
    total_files = 0

    for path in iter_files(root, max_depth=max_depth, max_files=max_files):
        try:
            stat = path.stat()
        except OSError:
            continue
        size = int(stat.st_size)
        suffix = path.suffix.lower() or "[none]"
        category = categorize(path)
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = path.name

        total_files += 1
        total_size += size
        category_counts[category] += 1
        category_sizes[category] += size
        extension_counts[suffix] += 1
        extension_sizes[suffix] += size

        record = {"path": rel, "size": size, "size_human": format_size(size), "category": category}
        top_files.append(record)
        if category == "packages-archives":
            packaged_files.append(record)

    top_files.sort(key=lambda item: int(item["size"]), reverse=True)
    packaged_files.sort(key=lambda item: int(item["size"]), reverse=True)

    categories = []
    for category, count in category_counts.most_common():
        categories.append(
            {
                "category": category,
                "count": count,
                "size": category_sizes[category],
                "size_human": format_size(category_sizes[category]),
            }
        )

    extensions = []
    for extension, count in extension_counts.most_common():
        extensions.append(
            {
                "extension": extension,
                "count": count,
                "size": extension_sizes[extension],
                "size_human": format_size(extension_sizes[extension]),
            }
        )

    return {
        "root": str(root),
        "total_files": total_files,
        "total_size": total_size,
        "total_size_human": format_size(total_size),
        "max_depth": max_depth,
        "max_files": max_files,
        "categories": categories,
        "extensions": extensions,
        "largest_files": top_files[:largest],
        "packaged_files": packaged_files[:largest],
        "notice": "Read-only inventory; archive/package contents are not opened or extracted.",
    }


def to_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Asset Inventory",
        "",
        f"- Root: `{report['root']}`",
        f"- Files: {report['total_files']}",
        f"- Total size: {report['total_size_human']}",
        f"- Notice: {report['notice']}",
        "",
        "## Categories",
        "",
        "| Category | Files | Size |",
        "|---|---:|---:|",
    ]
    for item in report["categories"]:  # type: ignore[index]
        lines.append(f"| {item['category']} | {item['count']} | {item['size_human']} |")

    lines.extend(["", "## Top Extensions", "", "| Extension | Files | Size |", "|---|---:|---:|"])
    for item in report["extensions"][:20]:  # type: ignore[index]
        lines.append(f"| `{item['extension']}` | {item['count']} | {item['size_human']} |")

    lines.extend(["", "## Largest Files", "", "| File | Category | Size |", "|---|---|---:|"])
    for item in report["largest_files"]:  # type: ignore[index]
        lines.append(f"| `{item['path']}` | {item['category']} | {item['size_human']} |")

    packaged = report["packaged_files"]
    if packaged:
        lines.extend(["", "## Package/Archive Markers", "", "| File | Size |", "|---|---:|"])
        for item in packaged:  # type: ignore[assignment]
            lines.append(f"| `{item['path']}` | {item['size_human']} |")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Local file or folder to inspect.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--max-files", type=int, default=50000)
    parser.add_argument("--largest", type=int, default=20)
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        parser.error(f"path does not exist: {root}")

    report = inventory(root, max_depth=args.max_depth, max_files=args.max_files, largest=args.largest)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

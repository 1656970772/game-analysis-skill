#!/usr/bin/env python3
"""Report dominant colors from authorized local images.

Pillow is used when available. Without Pillow, the fallback reader supports
common non-interlaced 8-bit PNG files and uncompressed 24/32-bit BMP files.
This script does not download or extract game resources.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import zlib
from collections import Counter
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None  # type: ignore


IMAGE_EXTENSIONS = {".png", ".bmp", ".jpg", ".jpeg", ".webp", ".gif", ".tga"}
FALLBACK_EXTENSIONS = {".png", ".bmp"}


class UnsupportedImage(ValueError):
    pass


def clamp(value: int) -> int:
    return max(0, min(255, value))


def quantize_color(rgb: tuple[int, int, int], bucket: int) -> tuple[int, int, int]:
    if bucket <= 1:
        return rgb
    return tuple(clamp((channel // bucket) * bucket + bucket // 2) for channel in rgb)  # type: ignore[return-value]


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def iter_image_paths(path: Path, max_files: int) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path
        return

    yielded = 0
    for current, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules"}]
        for name in files:
            candidate = Path(current) / name
            if candidate.suffix.lower() in IMAGE_EXTENSIONS:
                yield candidate
                yielded += 1
                if yielded >= max_files:
                    return


def paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def unfilter_scanline(filter_type: int, scan: bytearray, prev: bytearray, bpp: int) -> bytearray:
    out = bytearray(scan)
    for i, value in enumerate(scan):
        left = out[i - bpp] if i >= bpp else 0
        up = prev[i] if prev else 0
        up_left = prev[i - bpp] if prev and i >= bpp else 0
        if filter_type == 0:
            out[i] = value
        elif filter_type == 1:
            out[i] = (value + left) & 0xFF
        elif filter_type == 2:
            out[i] = (value + up) & 0xFF
        elif filter_type == 3:
            out[i] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            out[i] = (value + paeth(left, up, up_left)) & 0xFF
        else:
            raise UnsupportedImage(f"unsupported PNG filter: {filter_type}")
    return out


def read_png_pixels(path: Path, max_pixels: int) -> tuple[int, int, list[tuple[int, int, int]]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UnsupportedImage("not a PNG file")

    pos = 8
    width = height = bit_depth = color_type = interlace = None
    palette: list[tuple[int, int, int]] = []
    idat_parts: list[bytes] = []

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif chunk_type == b"PLTE":
            palette = [tuple(chunk[i : i + 3]) for i in range(0, len(chunk), 3)]  # type: ignore[list-item]
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk)
        elif chunk_type == b"IEND":
            break

    if None in {width, height, bit_depth, color_type, interlace}:
        raise UnsupportedImage("missing PNG header")
    if bit_depth != 8:
        raise UnsupportedImage("fallback PNG reader supports only 8-bit depth")
    if interlace != 0:
        raise UnsupportedImage("fallback PNG reader does not support interlaced PNG")
    if color_type not in {2, 3, 6}:
        raise UnsupportedImage("fallback PNG reader supports RGB, RGBA, and indexed PNG")
    if color_type == 3 and not palette:
        raise UnsupportedImage("indexed PNG missing palette")

    channels = {2: 3, 3: 1, 6: 4}[color_type]
    row_bytes = width * channels  # type: ignore[operator]
    raw = zlib.decompress(b"".join(idat_parts))
    step = max(1, int(math.sqrt((width * height) / max_pixels)))  # type: ignore[operator]
    pixels: list[tuple[int, int, int]] = []
    prev = bytearray(row_bytes)
    offset = 0

    for y in range(height):  # type: ignore[arg-type]
        filter_type = raw[offset]
        offset += 1
        scan = bytearray(raw[offset : offset + row_bytes])
        offset += row_bytes
        row = unfilter_scanline(filter_type, scan, prev, channels)
        prev = row
        if y % step != 0:
            continue
        for x in range(0, width, step):  # type: ignore[arg-type]
            index = x * channels
            if color_type == 2:
                pixels.append((row[index], row[index + 1], row[index + 2]))
            elif color_type == 6:
                alpha = row[index + 3]
                if alpha >= 16:
                    pixels.append((row[index], row[index + 1], row[index + 2]))
            else:
                palette_index = row[index]
                if palette_index < len(palette):
                    pixels.append(palette[palette_index])
    return int(width), int(height), pixels


def read_bmp_pixels(path: Path, max_pixels: int) -> tuple[int, int, list[tuple[int, int, int]]]:
    data = path.read_bytes()
    if not data.startswith(b"BM"):
        raise UnsupportedImage("not a BMP file")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise UnsupportedImage("unsupported BMP DIB header")
    width = struct.unpack_from("<i", data, 18)[0]
    height_signed = struct.unpack_from("<i", data, 22)[0]
    planes = struct.unpack_from("<H", data, 26)[0]
    bit_count = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if planes != 1 or compression != 0 or bit_count not in {24, 32}:
        raise UnsupportedImage("fallback BMP reader supports uncompressed 24/32-bit BMP")

    width_abs = abs(width)
    height_abs = abs(height_signed)
    top_down = height_signed < 0
    bytes_per_pixel = bit_count // 8
    row_stride = ((width_abs * bytes_per_pixel + 3) // 4) * 4
    step = max(1, int(math.sqrt((width_abs * height_abs) / max_pixels)))
    pixels: list[tuple[int, int, int]] = []

    for y_out in range(0, height_abs, step):
        y = y_out if top_down else height_abs - 1 - y_out
        row_start = pixel_offset + y * row_stride
        for x in range(0, width_abs, step):
            offset = row_start + x * bytes_per_pixel
            blue, green, red = data[offset], data[offset + 1], data[offset + 2]
            if bit_count == 32:
                alpha = data[offset + 3]
                if alpha < 16:
                    continue
            pixels.append((red, green, blue))
    return width_abs, height_abs, pixels


def read_pixels(path: Path, max_pixels: int) -> tuple[int, int, list[tuple[int, int, int]], str]:
    if Image is not None:
        with Image.open(path) as img:  # type: ignore[union-attr]
            img = img.convert("RGBA")
            width, height = img.size
            step = max(1, int(math.sqrt((width * height) / max_pixels)))
            pixels = []
            for y in range(0, height, step):
                for x in range(0, width, step):
                    red, green, blue, alpha = img.getpixel((x, y))
                    if alpha >= 16:
                        pixels.append((red, green, blue))
            return width, height, pixels, "pillow"

    suffix = path.suffix.lower()
    if suffix == ".png":
        width, height, pixels = read_png_pixels(path, max_pixels=max_pixels)
        return width, height, pixels, "fallback-png"
    if suffix == ".bmp":
        width, height, pixels = read_bmp_pixels(path, max_pixels=max_pixels)
        return width, height, pixels, "fallback-bmp"
    raise UnsupportedImage("Pillow is not installed; fallback supports PNG/BMP only")


def analyze_image(path: Path, top: int, bucket: int, max_pixels: int) -> dict[str, object]:
    width, height, pixels, reader = read_pixels(path, max_pixels=max_pixels)
    counter: Counter[tuple[int, int, int]] = Counter()
    for rgb in pixels:
        counter[quantize_color(rgb, bucket)] += 1
    total = sum(counter.values())
    colors = []
    for rgb, count in counter.most_common(top):
        colors.append(
            {
                "hex": to_hex(rgb),
                "rgb": list(rgb),
                "count": count,
                "percent": round((count / total) * 100, 2) if total else 0,
            }
        )
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "reader": reader,
        "sampled_pixels": total,
        "bucket": bucket,
        "colors": colors,
    }


def build_report(path: Path, top: int, bucket: int, max_pixels: int, max_files: int) -> dict[str, object]:
    images = list(iter_image_paths(path, max_files=max_files))
    reports = []
    errors = []
    aggregate: Counter[tuple[int, int, int]] = Counter()

    for image_path in images:
        try:
            report = analyze_image(image_path, top=top, bucket=bucket, max_pixels=max_pixels)
        except Exception as exc:
            errors.append({"path": str(image_path), "error": str(exc)})
            continue
        reports.append(report)
        for color in report["colors"]:  # type: ignore[index]
            aggregate[tuple(color["rgb"])] += int(color["count"])  # type: ignore[arg-type]

    aggregate_total = sum(aggregate.values())
    aggregate_colors = [
        {
            "hex": to_hex(rgb),
            "rgb": list(rgb),
            "count": count,
            "percent": round((count / aggregate_total) * 100, 2) if aggregate_total else 0,
        }
        for rgb, count in aggregate.most_common(top)
    ]

    return {
        "root": str(path.resolve()),
        "image_files_found": len(images),
        "image_files_analyzed": len(reports),
        "pillow_available": Image is not None,
        "notice": "Analyze only owned or authorized images; do not reuse reference pixels as assets.",
        "aggregate_colors": aggregate_colors,
        "images": reports,
        "errors": errors,
    }


def to_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Image Palette Report",
        "",
        f"- Root: `{report['root']}`",
        f"- Images found: {report['image_files_found']}",
        f"- Images analyzed: {report['image_files_analyzed']}",
        f"- Pillow available: {report['pillow_available']}",
        f"- Notice: {report['notice']}",
        "",
        "## Aggregate Colors",
        "",
    ]
    if not report["aggregate_colors"]:
        lines.append("No supported image colors found.")
    else:
        lines.extend(["| Color | RGB | Percent | Count |", "|---|---|---:|---:|"])
        for color in report["aggregate_colors"]:  # type: ignore[index]
            lines.append(
                f"| `{color['hex']}` | {color['rgb']} | {color['percent']}% | {color['count']} |"
            )

    if report["errors"]:
        lines.extend(["", "## Skipped Files", "", "| File | Reason |", "|---|---|"])
        for item in report["errors"]:  # type: ignore[index]
            lines.append(f"| `{item['path']}` | {item['error']} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Authorized local image or folder.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--bucket", type=int, default=16, help="RGB quantization bucket size.")
    parser.add_argument("--max-pixels", type=int, default=100000)
    parser.add_argument("--max-files", type=int, default=500)
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        parser.error(f"path does not exist: {root}")
    if args.bucket < 1 or args.bucket > 128:
        parser.error("--bucket must be between 1 and 128")

    report = build_report(
        root,
        top=args.top,
        bucket=args.bucket,
        max_pixels=args.max_pixels,
        max_files=args.max_files,
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

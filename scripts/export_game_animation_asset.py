#!/usr/bin/env python3
"""Render or bundle an animation selected from a game animation manifest."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from PIL import GifImagePlugin, Image, ImageChops, ImageDraw


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlphaVertex:
    x: float
    y: float
    u: float
    v: float
    page: int


@dataclass(frozen=True)
class SymbolFrame:
    number: int
    duration: int
    alpha_index: int
    alpha_count: int


@dataclass(frozen=True)
class BuildData:
    name: str
    texture_names: tuple[str, ...]
    vertices: tuple[AlphaVertex, ...]
    symbols: dict[str, dict[int, SymbolFrame]]
    symbol_durations: dict[str, int]


@dataclass(frozen=True)
class RenderQuad:
    page: int
    source: tuple[tuple[float, float], ...]
    destination: tuple[tuple[float, float], ...]
    z_index: int


@dataclass(frozen=True)
class RenderedFrame:
    image: Image.Image
    anchor: tuple[float, float]
    element_count: int
    quad_count: int
    logical_bounds: tuple[float, float, float, float]


def is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(value, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def reject_reparse_chain(path: Path, boundary: Path, label: str) -> None:
    boundary_absolute = Path(os.path.abspath(boundary))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(boundary_absolute)
    except ValueError as error:
        raise ExportError(f"{label} escapes its allowed root: {path}") from error
    current = boundary_absolute
    if os.path.lexists(current) and is_reparse(current):
        raise ExportError(f"{label} root is a symlink/reparse point: {current}")
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and is_reparse(current):
            raise ExportError(f"{label} uses a symlink/reparse point: {current}")


def resolve_source(study_root: Path, relative_path: str) -> Path:
    root = study_root.resolve(strict=True)
    if not isinstance(relative_path, str) or not relative_path:
        raise ExportError("Manifest source path must be a non-empty relative POSIX path")
    posix = PurePosixPath(relative_path)
    if posix.is_absolute() or ".." in posix.parts or "\\" in relative_path:
        raise ExportError(f"Unsafe manifest source path: {relative_path!r}")
    lexical = root / Path(*posix.parts)
    reject_reparse_chain(lexical, root, "source")
    resolved = lexical.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ExportError(f"Manifest source path escapes study-root: {relative_path}") from error
    if not resolved.is_file():
        raise ExportError(f"Manifest source is not a file: {relative_path}")
    return resolved


def prepare_output_root(output_root: Path) -> Path:
    lexical = Path(os.path.abspath(output_root))
    if os.path.lexists(lexical) and is_reparse(lexical):
        raise ExportError(f"output-root is a symlink/reparse point: {lexical}")
    lexical.mkdir(parents=True, exist_ok=True)
    if not lexical.is_dir():
        raise ExportError(f"output-root is not a directory: {lexical}")
    return lexical.resolve(strict=True)


def safe_output_path(output_root: Path, *parts: str) -> Path:
    root = output_root.resolve(strict=True)
    candidate = root.joinpath(*parts)
    reject_reparse_chain(candidate, root, "output")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ExportError(f"Output path escapes output-root: {candidate}") from error
    return candidate


def make_output_directory(path: Path, output_root: Path) -> None:
    root = output_root.resolve(strict=True)
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            if is_reparse(current) or not current.is_dir():
                raise ExportError(f"Unsafe output directory component: {current}")
        else:
            current.mkdir()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bytes_idempotent(path: Path, payload: bytes, output_root: Path) -> str:
    make_output_directory(path.parent, output_root)
    reject_reparse_chain(path, output_root, "output")
    if os.path.lexists(path):
        if is_reparse(path) or not path.is_file():
            raise ExportError(f"Refusing to replace non-regular output: {path}")
        if path.read_bytes() == payload:
            return "reused"
        raise FileExistsError(f"Output exists with different content: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    reject_reparse_chain(temporary, output_root, "output")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return "written"


def parse_build(path: Path) -> BuildData:
    root = ET.parse(path).getroot()
    texture_names = tuple(
        node.attrib["filename"]
        for node in root.findall("Texture")
        if node.attrib.get("filename")
    )
    if not texture_names:
        raise ExportError(f"Build has no Texture declarations: {path}")
    parsed_vertices: list[AlphaVertex] = []
    for index, node in enumerate(root.findall("Alphavert")):
        try:
            values = tuple(float(node.attrib[key]) for key in ("x", "y", "u", "v"))
            raw_page = float(node.attrib.get("w", "0"))
            page = int(raw_page)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ExportError(f"Invalid Alphavert #{index} in {path}") from error
        if not all(math.isfinite(value) for value in (*values, raw_page)) or raw_page != page:
            raise ExportError(f"Invalid Alphavert #{index} in {path}")
        parsed_vertices.append(AlphaVertex(*values, page))
    vertices = tuple(parsed_vertices)
    symbols: dict[str, dict[int, SymbolFrame]] = {}
    durations: dict[str, int] = {}
    for symbol in root.findall("Symbol"):
        name = symbol.attrib.get("name", "")
        frames: dict[int, SymbolFrame] = {}
        for node in symbol.findall("Frame"):
            parsed = SymbolFrame(
                number=int(node.attrib["framenum"]),
                duration=int(node.attrib.get("duration", "1")),
                alpha_index=int(node.attrib["alphaidx"]),
                alpha_count=int(node.attrib["alphacount"]),
            )
            if (
                parsed.number < 0
                or parsed.duration <= 0
                or parsed.alpha_index < 0
                or parsed.alpha_count < 0
                or parsed.alpha_count % 6
            ):
                raise ExportError(f"Invalid symbol frame in {path}: {name}#{parsed.number}")
            frames[parsed.number] = parsed
        normalized_name = name.casefold()
        symbols[normalized_name] = frames
        durations[normalized_name] = max(
            (frame.number + frame.duration for frame in frames.values()), default=0
        )
    return BuildData(root.attrib.get("name", path.parent.name), texture_names, vertices, symbols, durations)


def resolve_symbol_frame(build: BuildData, name: str, requested: int) -> SymbolFrame:
    frames = build.symbols.get(name.casefold())
    duration = build.symbol_durations.get(name.casefold(), 0)
    if not frames or duration <= 0:
        raise ExportError(f"Build {build.name!r} has no symbol {name!r}")
    held = requested % duration
    for frame in sorted(frames.values(), key=lambda value: value.number):
        if frame.number <= held < frame.number + frame.duration:
            return frame
    raise ExportError(f"Symbol {name!r} has no frame covering {held}")


def transform_point(vertex: AlphaVertex, element: ET.Element) -> tuple[float, float]:
    a = float(element.attrib["m_a"])
    b = float(element.attrib["m_b"])
    c = float(element.attrib["m_c"])
    d = float(element.attrib["m_d"])
    tx = float(element.attrib["m_tx"])
    ty = float(element.attrib["m_ty"])
    return a * vertex.x + c * vertex.y + tx, b * vertex.x + d * vertex.y + ty


def collect_quads(
    build: BuildData,
    frame: ET.Element,
    atlas_sizes: Sequence[tuple[int, int]],
    missing_symbol_policy: str = "strict",
) -> tuple[list[RenderQuad], int]:
    quads: list[RenderQuad] = []
    rendered_element_count = 0
    elements = sorted(
        frame.findall("element"),
        key=lambda value: int(value.attrib["z_index"]),
        reverse=True,
    )
    for element in elements:
        symbol_name = element.attrib["name"]
        if symbol_name.casefold() not in build.symbols:
            if missing_symbol_policy == "skip":
                continue
            raise ExportError(f"Build {build.name!r} has no symbol {symbol_name!r}")
        symbol_frame = resolve_symbol_frame(build, symbol_name, int(element.attrib["frame"]))
        rendered_element_count += 1
        start = symbol_frame.alpha_index
        stop = start + symbol_frame.alpha_count
        vertices = build.vertices[start:stop]
        if len(vertices) != symbol_frame.alpha_count:
            raise ExportError(
                f"Symbol {symbol_name!r} references vertices {start}:{stop} outside the build"
            )
        for offset in range(0, len(vertices), 6):
            raw = vertices[offset : offset + 6]
            corners = (raw[0], raw[1], raw[4], raw[2])
            pages = {vertex.page for vertex in corners}
            if len(pages) != 1:
                raise ExportError(f"Symbol {symbol_name!r} quad crosses atlas pages")
            page = pages.pop()
            if page < 0 or page >= len(atlas_sizes):
                raise ExportError(f"Build references missing atlas page {page}")
            width, height = atlas_sizes[page]
            source = tuple(
                (vertex.u * width, (1.0 - vertex.v) * height) for vertex in corners
            )
            destination = tuple(transform_point(vertex, element) for vertex in corners)
            if abs(triangle_area2(source[0], source[1], source[3])) < 1e-8:
                raise ExportError(f"Symbol {symbol_name!r} has a degenerate source quad")
            if abs(triangle_area2(destination[0], destination[1], destination[3])) < 1e-8:
                raise ExportError(f"Symbol {symbol_name!r} has a degenerate destination quad")
            quads.append(
                RenderQuad(page, source, destination, int(element.attrib["z_index"]))
            )
    return quads, rendered_element_count


def triangle_area2(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def solve_affine(
    destination: Sequence[tuple[float, float]], source: Sequence[tuple[float, float]]
) -> tuple[float, float, float, float, float, float]:
    (x0, y0), (x1, y1), (x2, y2) = destination[:3]
    (u0, v0), (u1, v1), (u2, v2) = source[:3]
    denominator = x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1)
    if abs(denominator) < 1e-8:
        raise ExportError("Degenerate destination triangle")

    def coefficients(q0: float, q1: float, q2: float) -> tuple[float, float, float]:
        return (
            (q0 * (y1 - y2) + q1 * (y2 - y0) + q2 * (y0 - y1)) / denominator,
            (q0 * (x2 - x1) + q1 * (x0 - x2) + q2 * (x1 - x0)) / denominator,
            (
                q0 * (x1 * y2 - x2 * y1)
                + q1 * (x2 * y0 - x0 * y2)
                + q2 * (x0 * y1 - x1 * y0)
            )
            / denominator,
        )

    a, b, c = coefficients(u0, u1, u2)
    d, e, f = coefficients(v0, v1, v2)
    return a, b, c, d, e, f


def polygon_mask(size: tuple[int, int], points: Sequence[tuple[float, float]]) -> Image.Image:
    supersample = 4
    large = Image.new("L", (size[0] * supersample, size[1] * supersample), 0)
    ImageDraw.Draw(large).polygon(
        [(x * supersample, y * supersample) for x, y in points], fill=255
    )
    return large.resize(size, Image.Resampling.LANCZOS)


def render_quad(
    atlas: Image.Image,
    quad: RenderQuad,
    canvas: Image.Image,
    canvas_left: int,
    canvas_top: int,
) -> None:
    destination = tuple(
        (x - canvas_left, y - canvas_top) for x, y in quad.destination
    )
    left = math.floor(min(x for x, _ in destination)) - 1
    top = math.floor(min(y for _, y in destination)) - 1
    right = math.ceil(max(x for x, _ in destination)) + 1
    bottom = math.ceil(max(y for _, y in destination)) + 1
    size = max(1, right - left), max(1, bottom - top)
    local = tuple((x - left, y - top) for x, y in destination)
    coefficients = solve_affine(
        (local[0], local[1], local[3]),
        (quad.source[0], quad.source[1], quad.source[3]),
    )
    warped = atlas.transform(
        size,
        Image.Transform.AFFINE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    warped.putalpha(ImageChops.multiply(warped.getchannel("A"), polygon_mask(size, local)))
    canvas.alpha_composite(warped, (left, top))


def render_frame(
    build: BuildData,
    atlases: Sequence[Image.Image],
    frame: ET.Element,
    padding: int = 2,
    missing_symbol_policy: str = "strict",
) -> RenderedFrame:
    quads, element_count = collect_quads(
        build,
        frame,
        [atlas.size for atlas in atlases],
        missing_symbol_policy,
    )
    logical_width = float(frame.attrib.get("w", "0"))
    logical_height = float(frame.attrib.get("h", "0"))
    logical_x = float(frame.attrib.get("x", "0"))
    logical_y = float(frame.attrib.get("y", "0"))
    logical_bounds = (
        logical_x - logical_width / 2,
        logical_y - logical_height / 2,
        logical_x + logical_width / 2,
        logical_y + logical_height / 2,
    )
    if not quads:
        if element_count:
            raise ExportError("Animation frame elements produced no renderable quads")
        return RenderedFrame(
            Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
            (0.0, 0.0),
            0,
            0,
            logical_bounds,
        )
    xs = [x for quad in quads for x, _ in quad.destination]
    ys = [y for quad in quads for _, y in quad.destination]
    margin = padding + 3
    left = math.floor(min(logical_bounds[0], min(xs))) - margin
    top = math.floor(min(logical_bounds[1], min(ys))) - margin
    right = math.ceil(max(logical_bounds[2], max(xs))) + margin
    bottom = math.ceil(max(logical_bounds[3], max(ys))) + margin
    canvas = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
    for quad in quads:
        render_quad(atlases[quad.page], quad, canvas, left, top)
    alpha_bounds = canvas.getchannel("A").getbbox()
    if alpha_bounds is None:
        raise ExportError("Rendered frame is fully transparent")
    crop = (
        max(0, alpha_bounds[0] - padding),
        max(0, alpha_bounds[1] - padding),
        min(canvas.width, alpha_bounds[2] + padding),
        min(canvas.height, alpha_bounds[3] + padding),
    )
    return RenderedFrame(
        canvas.crop(crop),
        (-left - crop[0], -top - crop[1]),
        element_count,
        len(quads),
        logical_bounds,
    )


def normalize_frames(frames: Sequence[RenderedFrame]) -> list[Image.Image]:
    if not frames:
        raise ExportError("Cannot normalize an empty frame sequence")
    visible = [frame for frame in frames if frame.quad_count]
    if visible:
        left = math.floor(min(-frame.anchor[0] for frame in visible))
        top = math.floor(min(-frame.anchor[1] for frame in visible))
        right = math.ceil(
            max(-frame.anchor[0] + frame.image.width for frame in visible)
        )
        bottom = math.ceil(
            max(-frame.anchor[1] + frame.image.height for frame in visible)
        )
    else:
        left = math.floor(min(frame.logical_bounds[0] for frame in frames))
        top = math.floor(min(frame.logical_bounds[1] for frame in frames))
        right = math.ceil(max(frame.logical_bounds[2] for frame in frames))
        bottom = math.ceil(max(frame.logical_bounds[3] for frame in frames))
    width = max(1, right - left)
    height = max(1, bottom - top)
    normalized: list[Image.Image] = []
    for frame in frames:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if frame.quad_count:
            canvas.alpha_composite(
                frame.image,
                (round(-frame.anchor[0] - left), round(-frame.anchor[1] - top)),
            )
        normalized.append(canvas)
    return normalized


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def timing_distinct_transparent_frames(frames: Sequence[Image.Image]) -> list[Image.Image]:
    """Keep visually identical frames distinct without adding visible pixels."""
    if not frames:
        raise ExportError("Cannot encode an empty frame sequence")
    needs_border = any(frame.getpixel((0, 0))[3] != 0 for frame in frames)
    distinct: list[Image.Image] = []
    for index, frame in enumerate(frames):
        if needs_border:
            marked = Image.new("RGBA", (frame.width + 1, frame.height), (0, 0, 0, 0))
            marked.alpha_composite(frame, (1, 0))
        else:
            marked = frame.copy()
        marked.putpixel(
            (0, 0),
            (
                (index * 73 + 1) % 256,
                (index * 151 + 3) % 256,
                (index * 199 + 5) % 256,
                0,
            ),
        )
        distinct.append(marked)
    return distinct


def encode_apng(frames: Sequence[Image.Image], duration_ms: int) -> bytes:
    buffer = io.BytesIO()
    marked = timing_distinct_transparent_frames(frames)
    try:
        marked[0].save(
            buffer,
            format="PNG",
            save_all=True,
            append_images=marked[1:],
            duration=[duration_ms] * len(marked),
            loop=0,
            disposal=2,
            blend=1,
        )
    finally:
        for frame in marked:
            frame.close()
    return buffer.getvalue()


def encode_gif(frames: Sequence[Image.Image], duration_ms: int) -> bytes:
    converted: list[Image.Image] = []
    marked = timing_distinct_transparent_frames(frames)
    for index, frame in enumerate(marked):
        paletted = frame.convert("RGB").convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=254
        )
        transparent_index = 254 + index % 2
        transparent = frame.getchannel("A").point(lambda alpha: 255 if alpha < 128 else 0)
        paletted.paste(transparent_index, mask=transparent)
        palette = paletted.getpalette()
        palette[762:768] = [1, 0, 0, 0, 1, 0]
        paletted.putpalette(palette)
        paletted.info["transparency"] = transparent_index
        converted.append(paletted)
    for frame in marked:
        frame.close()
    buffer = io.BytesIO()
    header, _used_palette = GifImagePlugin.getheader(
        converted[0], info={"loop": 0, "optimize": False}
    )
    for block in header:
        buffer.write(block)
    for index, frame in enumerate(converted):
        for block in GifImagePlugin.getdata(
            frame,
            duration=duration_ms,
            disposal=2,
            transparency=int(frame.info["transparency"]),
            include_color_table=index > 0,
        ):
            buffer.write(block)
    buffer.write(b";")
    for frame in converted:
        frame.close()
    return buffer.getvalue()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:80] or "animation"


def select_item(manifest: dict[str, Any], requested: str) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("items", [])
        if requested in (item.get("id"), item.get("bank", {}).get("name"))
    ]
    if len(matches) != 1:
        raise ExportError(f"Item selector must match exactly one item: {requested!r}")
    return matches[0]


def select_build(item: dict[str, Any], requested: str | None) -> dict[str, Any]:
    selector = requested or item.get("default_build_id")
    if not selector:
        raise ExportError("No default build is available; select --build explicitly")
    matches = [
        candidate
        for candidate in item.get("build_candidates", [])
        if selector in (candidate.get("id"), candidate.get("build", {}).get("name"))
    ]
    if len(matches) != 1:
        raise ExportError(f"Build selector must match exactly one candidate: {selector!r}")
    return matches[0]


def select_clip(item: dict[str, Any], requested: str) -> dict[str, Any]:
    matches = [
        clip
        for clip in item.get("clips", [])
        if requested in (clip.get("id"), clip.get("name"))
    ]
    if len(matches) != 1:
        raise ExportError(f"Clip selector must match exactly one clip: {requested!r}")
    return matches[0]


def select_animation_node(path: Path, item: dict[str, Any], clip: dict[str, Any]) -> ET.Element:
    animations = ET.parse(path).getroot().findall("anim")
    xml_index = clip.get("xml_index")
    if isinstance(xml_index, int) and 0 <= xml_index < len(animations):
        candidate = animations[xml_index]
        if (
            candidate.attrib.get("name") == clip["name"]
            and candidate.attrib.get("root") == item["bank"]["name"]
        ):
            return candidate
    matches = [
        node
        for node in animations
        if node.attrib.get("name") == clip["name"]
        and node.attrib.get("root") == item["bank"]["name"]
    ]
    if len(matches) != 1:
        raise ExportError("Manifest clip does not identify exactly one ANIM node")
    return matches[0]


def source_bundle_bytes(
    study_root: Path,
    manifest: dict[str, Any],
    item: dict[str, Any],
    build_candidate: dict[str, Any],
    clip: dict[str, Any],
) -> tuple[bytes, list[dict[str, Any]]]:
    relative_paths = [
        clip["source_path"],
        build_candidate["build"]["source_path"],
        *build_candidate.get("atlases", []),
    ]
    files: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for relative in relative_paths:
        if relative in seen:
            continue
        seen.add(relative)
        files.append((relative, resolve_source(study_root, relative)))
    provenance = [
        {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for relative, path in files
    ]
    descriptor = {
        "schema_version": 1,
        "manifest_adapter": manifest.get("adapter"),
        "item_id": item["id"],
        "build_id": build_candidate["id"],
        "clip_id": clip["id"],
        "files": provenance,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, path in sorted(files):
            info = zipfile.ZipInfo(f"sources/{relative}", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo("provenance.json", (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(
            info,
            (json.dumps(descriptor, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
    return buffer.getvalue(), provenance


def export_animation(
    *,
    study_root: Path,
    manifest_path: Path,
    item_selector: str,
    clip_selector: str,
    output_root: Path,
    export_format: str,
    build_selector: str | None = None,
    frame_index: int = 0,
) -> dict[str, Any]:
    root = study_root.resolve(strict=True)
    manifest_lexical = Path(os.path.abspath(manifest_path))
    reject_reparse_chain(manifest_lexical, root, "manifest")
    manifest_resolved = manifest_lexical.resolve(strict=True)
    try:
        manifest_resolved.relative_to(root)
    except ValueError as error:
        raise ExportError("Manifest must be inside study-root") from error
    manifest = json.loads(manifest_resolved.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ExportError("Unsupported animation manifest schema_version")
    item = select_item(manifest, item_selector)
    clip = select_clip(item, clip_selector)
    build_candidate = select_build(item, build_selector)
    destination_root = prepare_output_root(output_root)
    stem = "__".join(
        slug(value)
        for value in (
            item["bank"]["package"],
            item["bank"]["name"],
            build_candidate["build"]["name"],
            clip["name"],
        )
    )
    if export_format == "source-bundle":
        payload, provenance = source_bundle_bytes(
            root, manifest, item, build_candidate, clip
        )
        destination = safe_output_path(destination_root, f"{stem}__source.zip")
        status = write_bytes_idempotent(destination, payload, destination_root)
        return {
            "status": status,
            "format": export_format,
            "input_frame_count": int(clip.get("frame_count", 0)),
            "outputs": [str(destination)],
            "source_provenance": provenance,
        }

    build_path = resolve_source(root, build_candidate["build"]["source_path"])
    anim_path = resolve_source(root, clip["source_path"])
    atlas_paths = [resolve_source(root, relative) for relative in build_candidate.get("atlases", [])]
    build = parse_build(build_path)
    missing_symbol_policy = str(build_candidate.get("missing_symbol_policy", "strict"))
    if missing_symbol_policy not in {"strict", "skip"}:
        raise ExportError(f"Unsupported missing symbol policy: {missing_symbol_policy}")
    if len(atlas_paths) != len(build.texture_names):
        raise ExportError("Manifest atlas list does not match Build Texture declarations")
    atlases: list[Image.Image] = []
    try:
        for path in atlas_paths:
            with Image.open(path) as image:
                atlases.append(image.convert("RGBA"))
        animation = select_animation_node(anim_path, item, clip)
        xml_frames = animation.findall("frame")
        if not xml_frames:
            raise ExportError("Animation clip has no frames")
        fps = float(animation.attrib.get("framerate", clip.get("fps", 0)))
        if not math.isfinite(fps) or fps <= 0:
            raise ExportError("Animation FPS must be positive")
        if export_format == "current-frame-png":
            if frame_index < 0 or frame_index >= len(xml_frames):
                raise ExportError(f"Frame index {frame_index} is outside 0..{len(xml_frames) - 1}")
            rendered = render_frame(
                build, atlases, xml_frames[frame_index], missing_symbol_policy=missing_symbol_policy
            )
            destination = safe_output_path(
                destination_root, f"{stem}__frame-{frame_index:04d}.png"
            )
            status = write_bytes_idempotent(
                destination, encode_png(rendered.image), destination_root
            )
            return {
                "status": status,
                "format": export_format,
                "frame_count": 1,
                "input_frame_count": len(xml_frames),
                "fps": fps,
                "outputs": [str(destination)],
            }
        rendered_frames = [
            render_frame(
                build, atlases, frame, missing_symbol_policy=missing_symbol_policy
            )
            for frame in xml_frames
        ]
        frames = normalize_frames(rendered_frames)
        duration_ms = max(1, round(1000 / fps))
        if export_format == "png-sequence":
            directory = safe_output_path(destination_root, f"{stem}__frames")
            make_output_directory(directory, destination_root)
            expected_names = {f"frame-{index:04d}.png" for index in range(len(frames))}
            extras = {
                path.name for path in directory.glob("frame-*.png") if path.name not in expected_names
            }
            if extras:
                raise FileExistsError(f"PNG sequence directory contains stale frames: {sorted(extras)}")
            statuses: list[str] = []
            outputs: list[str] = []
            for index, frame in enumerate(frames):
                destination = safe_output_path(directory, f"frame-{index:04d}.png")
                statuses.append(
                    write_bytes_idempotent(destination, encode_png(frame), destination_root)
                )
                outputs.append(str(destination))
            status = "reused" if statuses and set(statuses) == {"reused"} else "written"
        elif export_format == "apng":
            destination = safe_output_path(destination_root, f"{stem}.apng")
            status = write_bytes_idempotent(
                destination, encode_apng(frames, duration_ms), destination_root
            )
            outputs = [str(destination)]
        elif export_format == "gif":
            destination = safe_output_path(destination_root, f"{stem}.gif")
            status = write_bytes_idempotent(
                destination, encode_gif(frames, duration_ms), destination_root
            )
            outputs = [str(destination)]
        else:
            raise ExportError(f"Unsupported export format: {export_format}")
        return {
            "status": status,
            "format": export_format,
            "frame_count": len(frames),
            "input_frame_count": len(xml_frames),
            "fps": fps,
            "duration_ms_per_frame": duration_ms,
            "outputs": outputs,
        }
    finally:
        for atlas in atlases:
            atlas.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--build")
    parser.add_argument("--clip", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--format",
        dest="export_format",
        required=True,
        choices=("current-frame-png", "png-sequence", "apng", "gif", "source-bundle"),
    )
    parser.add_argument("--frame", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = export_animation(
        study_root=args.study_root,
        manifest_path=args.manifest,
        item_selector=args.item,
        build_selector=args.build,
        clip_selector=args.clip,
        output_root=args.output_root,
        export_format=args.export_format,
        frame_index=args.frame,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

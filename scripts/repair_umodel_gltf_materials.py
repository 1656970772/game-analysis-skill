#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote


TEXTURE_REF_RE = re.compile(r"Texture(?:2D|Cube|RenderTarget2D)?'([^']+)'")
PARAMETER_RE = re.compile(r"ParameterInfo\s*=\s*\{\s*Name=([^}]+)\s*\}")
TEXTURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".dds", ".hdr"}
NORMAL_HINTS = ("normal", "nrm")
EMISSIVE_HINTS = ("emissive", "emission", "glow")
BASE_HINTS = (
    "basecolor",
    "base color",
    "diffuse",
    "albedo",
    "color",
    "texture",
    "maintex",
)
LOW_PRIORITY_HINTS = ("distortion", "noise", "mask", "roughness", "metallic")
DUMMY_MATERIAL_NAMES = {"dummy_material_0", "dummy"}


def walk_files(root, suffixes=None):
    suffixes = {s.lower() for s in suffixes} if suffixes else None
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _err: None):
        base = Path(dirpath)
        for filename in filenames:
            path = base / filename
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            yield path


def rel_posix(path, root):
    return path.relative_to(root).as_posix()


def normalize_texture_ref(value):
    package = value.rsplit(".", 1)[0] if "." in value else value
    object_name = value.rsplit(".", 1)[-1] if "." in value else Path(value).name
    return package.replace("\\", "/").strip("/"), object_name


def parse_props(path):
    entries = []
    current_parameter = ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        param_match = PARAMETER_RE.search(line)
        if param_match:
            current_parameter = param_match.group(1).strip()
        value_match = TEXTURE_REF_RE.search(line)
        if value_match:
            package, object_name = normalize_texture_ref(value_match.group(1).strip())
            entries.append(
                {
                    "parameter": current_parameter,
                    "package": package,
                    "object_name": object_name,
                    "source_ref": value_match.group(1).strip(),
                }
            )
    return entries


def common_prefix_score(a, b):
    a_parts = [part.lower() for part in Path(a).parts]
    b_parts = [part.lower() for part in Path(b).parts]
    score = 0
    for left, right in zip(a_parts, b_parts):
        if left != right:
            break
        score += 1
    return score


def classify_texture(parameter, texture_path):
    text = f"{parameter} {texture_path.stem}".lower().replace("-", "_")
    stem = texture_path.stem.lower().replace("-", "_")
    if any(hint in text for hint in LOW_PRIORITY_HINTS):
        return "other"
    if any(hint in text for hint in NORMAL_HINTS):
        return "normal"
    if re.search(r"(^|[_\s.])n($|[_\s.])", stem):
        return "normal"
    if any(hint in text for hint in EMISSIVE_HINTS):
        return "emissive"
    if any(hint in text for hint in BASE_HINTS):
        return "base"
    if re.search(r"(^|[_\s.])d($|[_\s.])", stem):
        return "base"
    return "other"


def base_priority(entry):
    text = f"{entry['parameter']} {entry['texture'].stem}".lower().replace("-", "_")
    if "basecolor" in text or "base color" in text:
        return 0
    if "diffuse" in text or "albedo" in text:
        return 1
    if "texture" in text or "maintex" in text:
        return 2
    if any(hint in text for hint in LOW_PRIORITY_HINTS):
        return 9
    return 4


def find_raw_roots(converted_root):
    roots = []
    direct = converted_root / "raw_umodel"
    if direct.is_dir():
        roots.append(direct)
    for child in converted_root.iterdir():
        if not child.is_dir():
            continue
        raw = child / "raw_umodel"
        if raw.is_dir():
            roots.append(raw)
    return sorted(set(roots))


def build_indexes(raw_root):
    material_props = {}
    texture_by_package = {}
    texture_by_name = {}

    for props in walk_files(raw_root, suffixes={".txt"}):
        if not props.name.endswith(".props.txt"):
            continue
        material_name = props.name[: -len(".props.txt")]
        material_props.setdefault(material_name.lower(), []).append(props)

    for texture in walk_files(raw_root, suffixes=TEXTURE_EXTENSIONS):
        rel_no_ext = Path(rel_posix(texture, raw_root)).with_suffix("").as_posix().lower()
        texture_by_package.setdefault(rel_no_ext, texture)
        texture_by_name.setdefault(texture.stem.lower(), []).append(texture)

    return material_props, texture_by_package, texture_by_name


def pick_props(material_name, gltf_rel, material_props):
    candidates = material_props.get(material_name.lower(), [])
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: common_prefix_score(gltf_rel.parent, path.parent),
    )


def name_tokens(value):
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    return [token for token in tokens if len(token) >= 3]


def props_texture_entries_cached(props_path, cache):
    if props_path not in cache:
        cache[props_path] = parse_props(props_path)
    return cache[props_path]


def score_fallback_props(gltf_rel, props_path, props_entries):
    mesh_tokens = name_tokens(gltf_rel.stem)
    if not mesh_tokens:
        return 0

    material_tokens = name_tokens(props_path.name[: -len(".props.txt")])
    texture_tokens = []
    for entry in props_entries:
        texture_tokens.extend(name_tokens(entry["object_name"]))
        texture_tokens.extend(name_tokens(Path(entry["package"]).name))

    score = common_prefix_score(gltf_rel.parent, props_path.parent) * 2
    for token in mesh_tokens:
        if token in material_tokens:
            score += 40
        if any(token in candidate or candidate in token for candidate in material_tokens):
            score += 18
        if token in texture_tokens:
            score += 35
        if any(token in candidate or candidate in token for candidate in texture_tokens):
            score += 14

    if any("mesh" in token for token in texture_tokens):
        score += 8
    if props_entries:
        score += 4
    return score


def pick_fallback_props(gltf_rel, material_props, texture_by_package, texture_by_name):
    props_cache = {}
    candidates = []
    for props_list in material_props.values():
        for props_path in props_list:
            entries = props_texture_entries_cached(props_path, props_cache)
            score = score_fallback_props(gltf_rel, props_path, entries)
            if score <= 0:
                continue
            resolved_count = sum(1 for entry in entries if find_texture(entry, texture_by_package, texture_by_name))
            candidates.append((score, resolved_count, props_path))

    if not candidates:
        return None
    score, resolved_count, props_path = max(
        candidates,
        key=lambda item: (item[0], item[1], common_prefix_score(gltf_rel.parent, item[2].parent)),
    )
    if score < 45 or resolved_count == 0:
        return None
    return props_path


def find_texture(entry, texture_by_package, texture_by_name):
    package_key = entry["package"].lower()
    if package_key in texture_by_package:
        return texture_by_package[package_key]
    candidates = texture_by_name.get(entry["object_name"].lower(), [])
    if candidates:
        return candidates[0]
    package_name = Path(entry["package"]).name.lower()
    candidates = texture_by_name.get(package_name, [])
    if candidates:
        return candidates[0]
    return None


def link_or_copy(source, dest, overwrite=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        if overwrite:
            dest.unlink()
        else:
            return "exists"
    try:
        os.link(source, dest)
        return "hardlink"
    except OSError:
        shutil.copy2(source, dest)
        return "copy"


def safe_texture_name(texture, raw_root):
    rel = rel_posix(texture, raw_root)
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
    stem = texture.stem
    if len(stem) > 80:
        stem = stem[:80]
    return f"{digest}_{stem}{texture.suffix.lower()}"


def add_texture(data, output_gltf, source_texture, raw_root, overwrite):
    texture_dir = output_gltf.parent / "_textures"
    texture_name = safe_texture_name(source_texture, raw_root)
    dest = texture_dir / texture_name
    link_mode = link_or_copy(source_texture, dest, overwrite=overwrite)
    uri = quote(f"_textures/{texture_name}", safe="/._-")

    images = data.setdefault("images", [])
    textures = data.setdefault("textures", [])

    for index, existing in enumerate(textures):
        source_index = existing.get("source")
        if isinstance(source_index, int) and source_index < len(images):
            if images[source_index].get("uri") == uri:
                return index, dest, link_mode

    image_index = len(images)
    images.append({"uri": uri, "name": source_texture.stem})
    texture_index = len(textures)
    textures.append({"source": image_index, "name": source_texture.stem})
    return texture_index, dest, link_mode


def copy_existing_external_images(data, source_gltf, output_gltf, overwrite):
    for image in data.get("images", []):
        uri = image.get("uri", "")
        if not uri or uri.startswith("data:") or "://" in uri:
            continue
        source = source_gltf.parent / uri.replace("/", os.sep)
        if source.exists():
            dest = output_gltf.parent / uri.replace("/", os.sep)
            link_or_copy(source, dest, overwrite=overwrite)


def copy_buffers(data, source_gltf, output_gltf, overwrite):
    for buffer in data.get("buffers", []):
        uri = buffer.get("uri", "")
        if not uri or uri.startswith("data:") or "://" in uri:
            continue
        source = source_gltf.parent / uri.replace("/", os.sep)
        if source.exists():
            dest = output_gltf.parent / uri.replace("/", os.sep)
            link_or_copy(source, dest, overwrite=overwrite)


def material_texture_choices(props_path, texture_by_package, texture_by_name):
    entries = []
    for entry in parse_props(props_path):
        texture = find_texture(entry, texture_by_package, texture_by_name)
        if not texture:
            entry = dict(entry)
            entry["texture"] = None
            entry["kind"] = "missing"
            entries.append(entry)
            continue
        entry = dict(entry)
        entry["texture"] = texture
        entry["kind"] = classify_texture(entry["parameter"], texture)
        entries.append(entry)
    return entries


def first_by_kind(entries, kind):
    matches = [entry for entry in entries if entry.get("texture") and entry.get("kind") == kind]
    if not matches:
        return None
    if kind == "base":
        return sorted(matches, key=base_priority)[0]
    return matches[0]


def fallback_base(entries):
    candidates = [
        entry
        for entry in entries
        if entry.get("texture")
        and entry.get("kind") == "other"
        and not any(hint in f"{entry['parameter']} {entry['texture'].stem}".lower() for hint in LOW_PRIORITY_HINTS)
    ]
    return candidates[0] if candidates else None


def repair_gltf(gltf_path, raw_root, output_root, material_props, texture_by_package, texture_by_name, overwrite):
    slice_id = raw_root.parent.name
    gltf_rel = gltf_path.relative_to(raw_root)
    output_gltf = output_root / slice_id / gltf_rel
    output_gltf.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(gltf_path.read_text(encoding="utf-8"))
    copy_buffers(data, gltf_path, output_gltf, overwrite)
    copy_existing_external_images(data, gltf_path, output_gltf, overwrite)

    rows = []
    repaired_materials = 0
    linked_textures = 0

    materials = data.get("materials", [])
    if not materials:
        rows.append(
            {
                "slice_id": slice_id,
                "source_gltf": str(gltf_path),
                "output_gltf": str(output_gltf),
                "material_name": "",
                "status": "no_materials",
                "props_file": "",
                "base_color_texture": "",
                "normal_texture": "",
                "emissive_texture": "",
                "texture_count": 0,
                "notes": "",
            }
        )
    for material in materials:
        material_name = material.get("name", "")
        props = pick_props(material_name, gltf_rel, material_props) if material_name else None
        fallback_notes = ""
        if not props and material_name.lower() in DUMMY_MATERIAL_NAMES:
            props = pick_fallback_props(gltf_rel, material_props, texture_by_package, texture_by_name)
            if props:
                fallback_notes = "fallback_props_by_mesh_name"
        row = {
            "slice_id": slice_id,
            "source_gltf": str(gltf_path),
            "output_gltf": str(output_gltf),
            "material_name": material_name,
            "status": "",
            "props_file": str(props) if props else "",
            "base_color_texture": "",
            "normal_texture": "",
            "emissive_texture": "",
            "texture_count": 0,
            "notes": fallback_notes,
        }
        if not props:
            row["status"] = "no_props"
            rows.append(row)
            continue

        entries = material_texture_choices(props, texture_by_package, texture_by_name)
        resolved = [entry for entry in entries if entry.get("texture")]
        if not entries:
            row["status"] = "no_texture_refs"
            rows.append(row)
            continue
        if not resolved:
            row["status"] = "missing_texture_files"
            row["notes"] = ";".join(entry["source_ref"] for entry in entries)
            rows.append(row)
            continue

        pbr = material.setdefault("pbrMetallicRoughness", {})
        changed = False
        base = first_by_kind(entries, "base") or fallback_base(entries)
        normal = first_by_kind(entries, "normal")
        emissive = first_by_kind(entries, "emissive")

        if base:
            texture_index, dest, _mode = add_texture(data, output_gltf, base["texture"], raw_root, overwrite)
            pbr["baseColorTexture"] = {"index": texture_index}
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            row["base_color_texture"] = str(dest)
            linked_textures += 1
            changed = True
        if normal:
            texture_index, dest, _mode = add_texture(data, output_gltf, normal["texture"], raw_root, overwrite)
            material["normalTexture"] = {"index": texture_index}
            row["normal_texture"] = str(dest)
            linked_textures += 1
            changed = True
        if emissive:
            texture_index, dest, _mode = add_texture(data, output_gltf, emissive["texture"], raw_root, overwrite)
            material["emissiveTexture"] = {"index": texture_index}
            material.setdefault("emissiveFactor", [1.0, 1.0, 1.0])
            row["emissive_texture"] = str(dest)
            linked_textures += 1
            changed = True

        row["texture_count"] = len(resolved)
        row["status"] = "repaired" if changed else "texture_refs_unclassified"
        if changed:
            repaired_materials += 1
        rows.append(row)

    output_gltf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows, repaired_materials, linked_textures


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "slice_id",
        "source_gltf",
        "output_gltf",
        "material_name",
        "status",
        "props_file",
        "base_color_texture",
        "normal_texture",
        "emissive_texture",
        "texture_count",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Repair UModel glTF material texture links for Blender import.")
    parser.add_argument("--converted-root", required=True, help="FullConverted directory containing slice/raw_umodel outputs.")
    parser.add_argument("--output-root", required=True, help="Directory for Blender-ready glTF copies.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max glTF files to process.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite generated glTF, bin, and texture links.")
    args = parser.parse_args()

    converted_root = Path(args.converted_root).resolve()
    output_root = Path(args.output_root).resolve()
    raw_roots = find_raw_roots(converted_root)
    if not raw_roots:
        raise SystemExit(f"No raw_umodel directories found under: {converted_root}")

    all_rows = []
    total_gltf = 0
    repaired_materials = 0
    linked_textures = 0

    for raw_root in raw_roots:
        material_props, texture_by_package, texture_by_name = build_indexes(raw_root)
        for gltf_path in walk_files(raw_root, suffixes={".gltf"}):
            if args.limit and total_gltf >= args.limit:
                break
            rows, repaired_count, linked_count = repair_gltf(
                gltf_path,
                raw_root,
                output_root,
                material_props,
                texture_by_package,
                texture_by_name,
                args.overwrite,
            )
            all_rows.extend(rows)
            total_gltf += 1
            repaired_materials += repaired_count
            linked_textures += linked_count
        if args.limit and total_gltf >= args.limit:
            break

    repaired_gltf = len({row["output_gltf"] for row in all_rows if row["status"] == "repaired"})
    index_dir = output_root / "_material_repair_index"
    index_path = index_dir / "material_repair_index.csv"
    write_csv(index_path, all_rows)
    summary = {
        "converted_root": str(converted_root),
        "output_root": str(output_root),
        "raw_root_count": len(raw_roots),
        "total_gltf": total_gltf,
        "repaired_gltf": repaired_gltf,
        "material_rows": len(all_rows),
        "repaired_materials": repaired_materials,
        "linked_textures": linked_textures,
        "index": str(index_path),
    }
    (index_dir / "material_repair_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"OUTPUT_ROOT={output_root}")
    print(f"INDEX={index_path}")
    print(f"TOTAL_GLTF={total_gltf}")
    print(f"REPAIRED_GLTF={repaired_gltf}")
    print(f"REPAIRED_MATERIALS={repaired_materials}")
    print(f"LINKED_TEXTURES={linked_textures}")


if __name__ == "__main__":
    main()

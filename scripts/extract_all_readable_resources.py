from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import UnityPy
except ImportError:  # pragma: no cover - environment-specific
    UnityPy = None


UNITY_CONTAINER_SUFFIXES = {
    ".assets",
    ".bundle",
    ".unity3d",
    ".sharedassets",
}
UNITY_CONTAINER_NAMES = {
    "globalgamemanagers",
    "maindata",
    "resources.assets",
    "resources.resource",
}
ADDRESSABLE_HINTS = ("StreamingAssets", "aa")

INDEX_FIELDS = [
    "asset_id",
    "source_file",
    "path_id",
    "asset_type",
    "asset_name",
    "container_path",
    "export_kind",
    "export_path",
    "width",
    "height",
    "status",
    "remarks",
]


def safe_name(value: str, fallback: str) -> str:
    text = value.strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text[:96].strip("._") or fallback


def category_dir(asset_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", asset_type) or "Unknown"


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 7:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_len": len(value), "preview_hex": value[:32].hex()}
    if isinstance(value, (list, tuple)):
        if len(value) > 240:
            return [to_jsonable(v, depth + 1) for v in value[:240]] + [{"truncated_items": len(value) - 240}]
        return [to_jsonable(v, depth + 1) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 240:
                out["<truncated_keys>"] = len(value) - 240
                break
            out[str(k)] = to_jsonable(v, depth + 1)
        return out
    simple = {}
    for attr in ("m_FileID", "m_PathID", "path_id", "type", "m_Name"):
        if hasattr(value, attr):
            try:
                simple[attr] = to_jsonable(getattr(value, attr), depth + 1)
            except Exception:
                pass
    if simple:
        return simple
    return repr(value)


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def write_text_asset(data: Any, out_path: Path) -> tuple[str, str]:
    script = getattr(data, "m_Script", b"")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(script, str):
        out_path = out_path.with_suffix(".txt")
        out_path.write_text(script, encoding="utf-8", errors="replace")
        return str(out_path), ""
    if isinstance(script, bytes):
        try:
            text = script.decode("utf-8")
            out_path = out_path.with_suffix(".txt")
            out_path.write_text(text, encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            out_path = out_path.with_suffix(".bin")
            out_path.write_bytes(script)
        return str(out_path), ""
    out_path = out_path.with_suffix(".json")
    out_path.write_text(json.dumps(to_jsonable(script), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out_path), ""


def collect_container_paths(env: Any) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        for path, ptr in env.container.items():
            pid = getattr(ptr, "m_PathID", None)
            if isinstance(pid, int):
                mapping[pid] = path
    except Exception:
        pass
    return mapping


def find_unity_data_dirs(source_dir: Path) -> list[Path]:
    data_dirs = [path for path in source_dir.glob("*_Data") if path.is_dir()]
    if source_dir.name.endswith("_Data"):
        data_dirs.append(source_dir)
    return sorted(set(data_dirs), key=lambda path: str(path).lower())


def looks_like_unity_container(path: Path, source_dir: Path) -> bool:
    if not path.is_file():
        return False
    lower_name = path.name.lower()
    lower_suffix = path.suffix.lower()
    if lower_suffix in UNITY_CONTAINER_SUFFIXES:
        return True
    if lower_name in UNITY_CONTAINER_NAMES or lower_name.startswith("sharedassets"):
        return True
    try:
        parts = {part.lower() for part in path.relative_to(source_dir).parts}
    except ValueError:
        parts = {part.lower() for part in path.parts}
    if {"streamingassets", "aa"}.issubset(parts) and lower_suffix not in {".json", ".hash"}:
        return True
    return False


def discover_unity_sources(source_dir: Path) -> list[Path]:
    roots = [source_dir, *find_unity_data_dirs(source_dir)]
    seen: set[Path] = set()
    sources: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path in seen or not looks_like_unity_container(path, source_dir):
                continue
            seen.add(path)
            sources.append(path)
    return sorted(sources, key=lambda path: str(path.relative_to(source_dir) if path.is_relative_to(source_dir) else path).lower())


def parse_addressables_catalogs(source_dir: Path, export_root: Path) -> dict[str, Any]:
    out_dir = export_root / "scene_catalog"
    out_dir.mkdir(parents=True, exist_ok=True)
    catalogs = sorted(source_dir.rglob("catalog*.json"))
    if not catalogs:
        return {"exists": False, "scene_count": 0, "catalog_count": 0}
    scene_paths: list[str] = []
    asset_paths: list[str] = []
    catalog_rows: list[dict[str, str]] = []
    for catalog in catalogs:
        try:
            data = json.loads(catalog.read_text(encoding="utf-8"))
        except Exception as exc:
            catalog_rows.append({"catalog": rel(catalog, source_dir), "status": "read_error", "message": f"{type(exc).__name__}: {exc}"})
            continue
        internal_ids = data.get("m_InternalIds", [])
        for item in internal_ids:
            if not isinstance(item, str):
                continue
            if item.startswith("Assets/"):
                asset_paths.append(item)
                if item.endswith(".unity") or "/Scenes/" in item:
                    scene_paths.append(item)
        catalog_rows.append({"catalog": rel(catalog, source_dir), "status": "parsed", "message": str(len(internal_ids))})
    (out_dir / "scene_paths.txt").write_text("\n".join(scene_paths), encoding="utf-8")
    (out_dir / "asset_paths.txt").write_text("\n".join(asset_paths), encoding="utf-8")
    with (out_dir / "catalogs.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["catalog", "status", "message"])
        writer.writeheader()
        writer.writerows(catalog_rows)
    return {
        "exists": True,
        "catalog_count": len(catalogs),
        "scene_count": len(scene_paths),
        "asset_path_count": len(asset_paths),
        "scene_paths_file": "scene_catalog/scene_paths.txt",
        "asset_paths_file": "scene_catalog/asset_paths.txt",
        "catalogs_file": "scene_catalog/catalogs.csv",
    }


def export_all(source_dir: Path, export_root: Path, audio_mode: str = "metadata") -> dict[str, Any]:
    if UnityPy is None:
        raise SystemExit("UnityPy is required. Run scripts/setup_python_env.ps1 or install requirements.txt.")
    export_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    counts = Counter()
    exported = Counter()
    source_dir = source_dir.resolve()
    catalog_info = parse_addressables_catalogs(source_dir, export_root)
    sources = discover_unity_sources(source_dir)
    parsed_sources: list[str] = []

    for source_path in sources:
        source_rel = rel(source_path, source_dir)
        source_label = source_rel
        if not source_path.exists():
            logs.append({"source": source_label, "stage": "load", "status": "missing", "message": source_rel})
            continue
        try:
            env = UnityPy.load(str(source_path))
        except Exception as exc:
            logs.append({"source": source_label, "stage": "load", "status": "error", "message": f"{type(exc).__name__}: {exc}"})
            continue
        parsed_sources.append(source_label)
        container_paths = collect_container_paths(env)
        for obj in env.objects:
            asset_type = obj.type.name
            counts[asset_type] += 1
            asset_id = f"{safe_name(source_label, 'source')}_{obj.path_id}"
            name = f"pathid_{obj.path_id}"
            container_path = container_paths.get(obj.path_id, "")
            width = ""
            height = ""
            export_kind = "metadata"
            export_path = ""
            status = "indexed"
            remarks = ""
            try:
                data = obj.read()
                name = getattr(data, "m_Name", "") or getattr(data, "name", "") or name
            except Exception as exc:
                rows.append({
                    "asset_id": asset_id,
                    "source_file": source_label,
                    "path_id": obj.path_id,
                    "asset_type": asset_type,
                    "asset_name": name,
                    "container_path": container_path,
                    "export_kind": "",
                    "export_path": "",
                    "width": "",
                    "height": "",
                    "status": "read_error",
                    "remarks": f"{type(exc).__name__}: {exc}",
                })
                continue

            base_name = f"{asset_id}_{safe_name(name, asset_type)}"
            try:
                if asset_type in {"Texture2D", "Sprite"}:
                    img = data.image
                    if img is not None:
                        width, height = img.size
                        out_file = export_root / "images" / category_dir(asset_type) / f"{base_name}.png"
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        img.save(out_file)
                        export_kind = "png"
                        export_path = rel(out_file, export_root)
                        status = "exported"
                        exported[asset_type] += 1
                elif asset_type == "TextAsset":
                    out_base = export_root / "text_assets" / f"{base_name}"
                    saved, remarks = write_text_asset(data, out_base)
                    export_kind = "text_or_binary"
                    export_path = rel(Path(saved), export_root)
                    status = "exported"
                    exported[asset_type] += 1
                elif asset_type == "AudioClip" and audio_mode == "samples":
                    samples = getattr(data, "samples", {}) or {}
                    if samples:
                        audio_dir = export_root / "audio" / f"{base_name}"
                        audio_dir.mkdir(parents=True, exist_ok=True)
                        saved_files = []
                        for sample_name, sample_bytes in samples.items():
                            out_file = audio_dir / safe_name(sample_name, f"{base_name}.audio")
                            out_file.write_bytes(sample_bytes)
                            saved_files.append(rel(out_file, export_root))
                        export_kind = "audio"
                        export_path = ";".join(saved_files)
                        status = "exported"
                        exported[asset_type] += 1
                elif asset_type == "AudioClip":
                    export_kind = "metadata"
                    status = "indexed_audio_metadata"
                    remarks = "Audio sample export skipped by default to avoid native FMOD decoder crashes; rerun with --audio-mode samples if needed."
                else:
                    try:
                        tree = obj.read_typetree()
                    except Exception:
                        tree = {"repr": repr(data)}
                    out_file = export_root / "metadata" / category_dir(asset_type) / f"{base_name}.json"
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    payload = {
                        "source_file": source_label,
                        "path_id": obj.path_id,
                        "asset_type": asset_type,
                        "asset_name": name,
                        "container_path": container_path,
                        "type_tree": to_jsonable(tree),
                    }
                    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    export_path = rel(out_file, export_root)
                    status = "exported"
                    exported[asset_type] += 1
            except Exception as exc:
                status = "export_error"
                remarks = f"{type(exc).__name__}: {exc}"

            rows.append({
                "asset_id": asset_id,
                "source_file": source_label,
                "path_id": obj.path_id,
                "asset_type": asset_type,
                "asset_name": name,
                "container_path": container_path,
                "export_kind": export_kind,
                "export_path": export_path,
                "width": width,
                "height": height,
                "status": status,
                "remarks": remarks,
            })

    with (export_root / "all_resources_index.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (export_root / "export_log.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source", "stage", "status", "message"])
        writer.writeheader()
        writer.writerows(logs)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_dir),
        "sources": [rel(path, source_dir) for path in sources],
        "parsed_sources": parsed_sources,
        "source_count": len(sources),
        "object_count": len(rows),
        "type_counts": dict(counts),
        "exported_counts": dict(exported),
        "catalog": catalog_info,
        "notes": [
            "UnityPy route scans likely Unity containers and exports readable objects only.",
            "For full Unity project reconstruction, pair this with AssetRipper export and analyze_assetripper_project.py.",
            "AudioClip sample export defaults to metadata-only because UnityPy may call native FMOD decoders that can crash the Python process on some games.",
        ],
    }
    (export_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--audio-mode",
        choices=["metadata", "samples"],
        default="metadata",
        help="Default metadata avoids UnityPy native FMOD sample decoding crashes; use samples to export audio bytes.",
    )
    args = parser.parse_args()
    summary = export_all(Path(args.source), Path(args.output), audio_mode=args.audio_mode)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

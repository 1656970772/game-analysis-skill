#!/usr/bin/env python3
"""Run a read-only preflight scan for packaged Unity builds.

The script does not unpack, extract, or parse Unity asset payloads. It reads
small text/json files and the IL2CPP metadata header to choose the next local
workflow steps before launching heavier GUI or reverse-engineering tools.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any


METADATA_MAGIC = 0xFAB11BAF


def format_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def find_unity_data_dir(root: Path) -> Path | None:
    candidates = sorted(path for path in root.glob("*_Data") if path.is_dir())
    return candidates[0] if candidates else None


def read_app_info(data_dir: Path | None) -> dict[str, str | None]:
    if data_dir is None:
        return {"company": None, "name": None}
    path = data_dir / "app.info"
    if not path.exists():
        return {"company": None, "name": None}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"company": None, "name": None}
    return {
        "company": lines[0].strip() if len(lines) >= 1 and lines[0].strip() else None,
        "name": lines[1].strip() if len(lines) >= 2 and lines[1].strip() else None,
    }


def read_boot_config(data_dir: Path | None) -> dict[str, str]:
    if data_dir is None:
        return {}
    path = data_dir / "boot.config"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_scripting_assemblies(data_dir: Path | None) -> dict[str, Any]:
    if data_dir is None:
        return {"count": 0, "names": [], "gameplay_or_plugin_names": []}
    path = data_dir / "ScriptingAssemblies.json"
    if not path.exists():
        return {"count": 0, "names": [], "gameplay_or_plugin_names": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"count": 0, "names": [], "gameplay_or_plugin_names": []}
    names = [str(name) for name in payload.get("names", []) if isinstance(name, str)]
    interesting_prefixes = (
        "Assembly-",
        "Cinemachine",
        "DOTween",
        "FlowCanvas",
        "Rewired",
        "Coffee.",
        "Unity.2D",
        "Unity.Timeline",
        "UnityEngine.UI",
    )
    gameplay_or_plugin_names = [
        name
        for name in names
        if name.startswith(interesting_prefixes)
        or not name.startswith(("UnityEngine.", "Unity.", "System.", "mscorlib"))
    ]
    return {
        "count": len(names),
        "names": names,
        "gameplay_or_plugin_names": gameplay_or_plugin_names[:40],
    }


def read_metadata_header(metadata_path: Path | None) -> dict[str, Any]:
    if metadata_path is None or not metadata_path.exists():
        return {"path": None, "magic": None, "metadata_version": None, "valid_header": False}
    try:
        header = metadata_path.read_bytes()[:8]
    except OSError:
        return {
            "path": str(metadata_path),
            "magic": None,
            "metadata_version": None,
            "valid_header": False,
        }
    if len(header) < 8:
        return {
            "path": str(metadata_path),
            "magic": None,
            "metadata_version": None,
            "valid_header": False,
        }
    magic, version = struct.unpack("<II", header)
    return {
        "path": str(metadata_path),
        "magic": f"0x{magic:08X}",
        "metadata_version": version if magic == METADATA_MAGIC else None,
        "valid_header": magic == METADATA_MAGIC,
    }


def cpp2il_status(metadata_version: int | None) -> str:
    if metadata_version is None:
        return "unknown"
    if 24 <= metadata_version <= 29:
        return "possibly-supported"
    if metadata_version > 29:
        return "unsupported-likely"
    return "unknown"


def file_record(path: Path, root: Path) -> dict[str, Any]:
    size = path.stat().st_size
    return {"path": relpath(path, root), "size": size, "size_human": format_size(size)}


def collect_existing(paths: list[Path], root: Path, limit: int) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        if path.exists() and path.is_file():
            try:
                records.append(file_record(path, root))
            except OSError:
                continue
    records.sort(key=lambda item: int(item["size"]), reverse=True)
    return records[:limit]


def collect_resource_payloads(data_dir: Path | None, root: Path, limit: int) -> list[dict[str, Any]]:
    if data_dir is None:
        return []
    records = []
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".assets", ".ress", ".resource"} or re.fullmatch(r"level\d+", path.name):
            try:
                records.append(file_record(path, root))
            except OSError:
                continue
    records.sort(key=lambda item: int(item["size"]), reverse=True)
    return records[:limit]


def collect_under(base: Path | None, root: Path, limit: int) -> list[dict[str, Any]]:
    if base is None or not base.exists():
        return []
    records = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        try:
            records.append(file_record(path, root))
        except OSError:
            continue
    records.sort(key=lambda item: int(item["size"]), reverse=True)
    return records[:limit]


def preflight(root: Path, limit: int = 20) -> dict[str, Any]:
    root = root.resolve()
    data_dir = find_unity_data_dir(root)
    metadata_path = None
    if data_dir is not None:
        metadata_path = data_dir / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    metadata = read_metadata_header(metadata_path)
    has_gameassembly = (root / "GameAssembly.dll").exists()
    has_unityplayer = (root / "UnityPlayer.dll").exists()
    managed_assembly = data_dir / "Managed" / "Assembly-CSharp.dll" if data_dir is not None else None
    has_managed_assembly = bool(managed_assembly and managed_assembly.exists())

    if has_gameassembly and metadata["valid_header"]:
        scripting_backend = "IL2CPP"
    elif has_managed_assembly:
        scripting_backend = "Mono"
    else:
        scripting_backend = "Unknown"

    resource_payloads = collect_resource_payloads(data_dir, root, limit)
    streaming_assets = collect_under(data_dir / "StreamingAssets" if data_dir is not None else None, root, limit)
    native_plugins = collect_under(data_dir / "Plugins" if data_dir is not None else None, root, limit)
    core_binaries = collect_existing(
        [root / "GameAssembly.dll", root / "UnityPlayer.dll", root / "baselib.dll"],
        root,
        limit,
    )

    workflow_flags = []
    if data_dir is not None:
        workflow_flags.extend(["run_assetripper_export", "run_unitypy_after_assetripper"])
    if scripting_backend == "IL2CPP":
        workflow_flags.append("run_il2cpp_dumper_before_native_analysis")
    if cpp2il_status(metadata["metadata_version"]) == "unsupported-likely":
        workflow_flags.append("avoid_cpp2il_2022_for_this_metadata")
    if any(item["path"].lower().endswith((".ress", ".resource")) for item in resource_payloads) or streaming_assets:
        workflow_flags.append("external_streaming_or_resource_payloads_present")
    if native_plugins:
        workflow_flags.append("record_native_plugins_for_behavior_boundaries")

    return {
        "root": str(root),
        "unity_data_dir": relpath(data_dir, root) if data_dir is not None else None,
        "product": read_app_info(data_dir),
        "has_unityplayer": has_unityplayer,
        "scripting_backend": scripting_backend,
        "boot_config": read_boot_config(data_dir),
        "scripting_assemblies": read_scripting_assemblies(data_dir),
        "il2cpp": {
            "has_gameassembly": has_gameassembly,
            "has_global_metadata": bool(metadata["valid_header"]),
            "metadata_path": relpath(metadata_path, root) if metadata_path is not None and metadata_path.exists() else None,
            "metadata_magic": metadata["magic"],
            "metadata_version": metadata["metadata_version"],
            "cpp2il_2022_status": cpp2il_status(metadata["metadata_version"]),
        },
        "core_binaries": core_binaries,
        "resource_payloads": resource_payloads,
        "streaming_assets": streaming_assets,
        "native_plugins": native_plugins,
        "workflow_flags": workflow_flags,
        "notice": "Read-only Unity build preflight; asset payloads are not unpacked or extracted.",
    }


def to_markdown(report: dict[str, Any]) -> str:
    product = report["product"]
    product_bits = [value for value in [product.get("company"), product.get("name")] if value]
    product_text = " / ".join(product_bits) if product_bits else "Unknown"
    il2cpp = report["il2cpp"]
    lines = [
        "# Unity Build Preflight",
        "",
        f"- Root: `{report['root']}`",
        f"- Product: `{product_text}`",
        f"- Unity data dir: `{report['unity_data_dir'] or 'not found'}`",
        f"- Scripting backend: `{report['scripting_backend']}`",
        f"- Notice: {report['notice']}",
        "",
        "## IL2CPP",
        "",
        f"- GameAssembly.dll + global-metadata.dat: `{'yes' if il2cpp['has_gameassembly'] and il2cpp['has_global_metadata'] else 'no'}`",
        f"- Metadata version: `{il2cpp['metadata_version'] if il2cpp['metadata_version'] is not None else 'unknown'}`",
        f"- Cpp2IL 2022 status: `{il2cpp['cpp2il_2022_status']}`",
    ]
    if il2cpp["metadata_path"]:
        lines.append(f"- Metadata path: `{il2cpp['metadata_path']}`")

    assemblies = report["scripting_assemblies"]
    lines.extend(
        [
            "",
            "## Scripting Assemblies",
            "",
            f"- Count: {assemblies['count']}",
        ]
    )
    for name in assemblies["gameplay_or_plugin_names"][:12]:
        lines.append(f"- `{name}`")

    def add_table(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines.extend(["", f"## {title}", "", "| File | Size |", "|---|---:|"])
        for item in rows:
            lines.append(f"| `{item['path']}` | {item['size_human']} |")

    add_table("Core Binaries", report["core_binaries"])
    add_table("Largest Unity Payloads", report["resource_payloads"][:10])
    add_table("Streaming Assets", report["streaming_assets"][:10])
    add_table("Native Plugins", report["native_plugins"][:10])

    if report["workflow_flags"]:
        lines.extend(["", "## Workflow Flags", ""])
        for flag in report["workflow_flags"]:
            lines.append(f"- `{flag}`")

    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            "- Run AssetRipper for project, scene, prefab, and ProjectSettings recovery.",
            "- Run UnityPy after AssetRipper for scriptable Sprite/Texture2D/TextAsset/AudioClip indexing.",
        ]
    )
    if "run_il2cpp_dumper_before_native_analysis" in report["workflow_flags"]:
        lines.append("- Run Il2CppDumper before Ghidra/IDA; treat DummyDll as type metadata, not full source.")
    if "avoid_cpp2il_2022_for_this_metadata" in report["workflow_flags"]:
        lines.append("- Avoid Cpp2IL 2022 as the first-line tool for this metadata version.")
    if "external_streaming_or_resource_payloads_present" in report["workflow_flags"]:
        lines.append("- Track `.resS`, `.resource`, and StreamingAssets as external payloads during gallery/export validation.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Local Unity game root to inspect.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        parser.error(f"path does not exist: {root}")
    report = preflight(root, limit=args.limit)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the Unity VFX reconstruction exporter at full project scale.

The full-scale adapter normalization unwraps UnityPy's ``type_tree`` envelope,
normalizes Unity min/max curves to the public schema, reuses one adapter
instance, gives copied textures collision-free names, and can refresh the
complete AssetRipper VFX YAML dependency closure without rebuilding items.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import shutil
import sys
from collections import deque
from pathlib import Path
from typing import Any


def resolve_full_paths(exporter: Any, study_root: Path, config: dict[str, Any]) -> dict[str, Path]:
    paths = exporter.vfx_paths(study_root, config)
    configured = config.get("paths")
    if not isinstance(configured, dict):
        raise ValueError("config.paths must be an object")
    structure_dir = exporter._configured_study_path(
        study_root,
        configured.get("full_structure_index_dir"),
        Path("4.临时目录") / "中间索引" / "VFX结构",
    )
    if configured.get("assetripper_assets_root"):
        assets_root = exporter._configured_study_path(
            study_root,
            configured.get("assetripper_assets_root"),
            Path("0.原始导出") / "AssetRipper" / "ExportedProject" / "Assets",
        )
    else:
        export_root = exporter._configured_study_path(
            study_root,
            configured.get("assetripper_export_root"),
            Path("0.原始导出") / "AssetRipper",
        )
        assets_root = export_root / "ExportedProject" / "Assets"
    return {
        "preview_dir": paths.preview_index_dir,
        "manifest": paths.manifest,
        "unitypy_root": paths.unitypy_root,
        "classified_root": paths.classified_vfx_dir,
        "structure_dir": structure_dir,
        "assets_root": assets_root,
        "unity_export_dir": paths.unity_export_dir,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_if_changed(path: Path, data: Any) -> None:
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def copy_if_changed(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.is_file()
        and source.stat().st_size == destination.stat().st_size
        and file_sha256(source) == file_sha256(destination)
    ):
        return
    shutil.copy2(source, destination)


def install_idempotent_io(exporter: Any) -> None:
    exporter.write_json_utf8 = write_json_if_changed

    def copy_unity_asset_with_meta(
        source: Path, source_asset_root: Path, target_asset_root: Path
    ) -> None:
        relative = source.relative_to(source_asset_root)
        target = target_asset_root / relative
        if not exporter._path_is_inside(target.resolve(strict=False), target_asset_root):
            raise ValueError(f"Unity VFX original asset target escapes export root: {target}")
        copy_if_changed(source, target)
        meta = Path(str(source) + ".meta")
        if meta.is_file():
            copy_if_changed(meta, Path(str(target) + ".meta"))

    exporter._copy_unity_asset_with_meta = copy_unity_asset_with_meta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument(
        "--supplement-only",
        action="store_true",
        help="Refresh only the AssetRipper VFX YAML dependency closure and classifications.",
    )
    return parser.parse_args()


def load_module(path: Path) -> Any:
    name = "_t32_generic_vfx_exporter"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load exporter: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def sanitize_and_normalize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [sanitize_and_normalize(child) for child in value]
    if not isinstance(value, dict):
        return value
    output = {str(key): sanitize_and_normalize(child) for key, child in value.items()}
    if "scalar" in value or "minScalar" in value:
        scalar = value.get("scalar")
        minimum = value.get("minScalar")
        finite_scalar = scalar if isinstance(scalar, (int, float)) and not isinstance(scalar, bool) and math.isfinite(float(scalar)) else None
        finite_minimum = minimum if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and math.isfinite(float(minimum)) else None
        chosen = finite_scalar if finite_scalar is not None else finite_minimum
        if chosen is not None:
            output["constant"] = float(chosen)
            output["constantMax"] = float(chosen)
        if finite_minimum is not None:
            output["constantMin"] = float(finite_minimum)
    if "maxColor" in value and "color" not in output:
        output["color"] = sanitize_and_normalize(value["maxColor"])
    return output


def safe_child(root: Path, relative: str) -> Path | None:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    try:
        resolved_root = root.resolve(strict=False)
        candidate = (root / relative).resolve(strict=False)
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate


def metadata_for_item(item: dict[str, Any], unitypy_root: Path) -> dict[str, Any]:
    refs = item.get("source_refs")
    source = refs[0] if isinstance(refs, list) and refs and isinstance(refs[0], dict) else {}
    path = safe_child(unitypy_root, str(source.get("export_path") or ""))
    payload = read_json(path) if path is not None and path.is_file() else {}
    tree = payload.get("type_tree") if isinstance(payload.get("type_tree"), dict) else payload
    normalized = sanitize_and_normalize(tree)
    if not isinstance(normalized, dict):
        normalized = {}
    root_type = str(item.get("root_type") or item.get("asset_type") or "")
    if root_type == "ParticleSystem":
        if "lengthInSec" in normalized and "duration" not in normalized:
            normalized["duration"] = normalized["lengthInSec"]
        dependencies = item.get("dependencies", {})
        renderers = dependencies.get("renderers", []) if isinstance(dependencies, dict) else []
        if isinstance(renderers, list) and renderers:
            renderer = renderers[0] if isinstance(renderers[0], dict) else {}
            renderer_path = safe_child(unitypy_root, str(renderer.get("export_path") or ""))
            if renderer_path is not None and renderer_path.is_file():
                renderer_payload = read_json(renderer_path)
                renderer_tree = (
                    renderer_payload.get("type_tree")
                    if isinstance(renderer_payload.get("type_tree"), dict)
                    else renderer_payload
                )
                normalized["ParticleSystemRenderer"] = sanitize_and_normalize(renderer_tree)
    elif root_type == "TrailRenderer":
        normalized = {"trail": normalized}
    elif root_type == "LineRenderer":
        normalized = {"line": normalized}
    return normalized


def unique_texture_resolver(exporter: Any, adapter: Any) -> None:
    globals_dict = adapter.export_project_fragment.__func__.__globals__
    safe_export_candidate = globals_dict["_safe_export_candidate"]
    safe_name = globals_dict["_safe_name"]

    def resolve_texture_export(
        *,
        item: dict[str, Any],
        source_texture_relative_path: str,
        export_root: Path,
        asset_root: Path,
        export_asset_root: Path,
        export_textures_dir: Path,
    ) -> tuple[Path | None, str, str]:
        source_root_text = str(item.get("__unitypy_root") or "")
        if not source_root_text:
            return None, "", "Texture2D dependency is not copied or bound: missing UnityPy source root"
        source_root = Path(source_root_text)
        source_texture_path = safe_export_candidate(source_root, source_texture_relative_path)
        if source_texture_path is None or not source_texture_path.is_file():
            return None, "", f"Texture2D dependency is not copied or bound: {source_texture_relative_path}"
        source_name = Path(source_texture_relative_path.replace("\\", "/")).name
        destination_name = f"{safe_name(str(item.get('id') or 'vfx'))}_{safe_name(source_name)}"
        if not Path(destination_name).suffix:
            destination_name = f"{destination_name}.png"
        texture_path = asset_root / export_textures_dir / destination_name
        texture_asset_path = (export_asset_root / export_textures_dir / destination_name).as_posix()
        try:
            texture_path.resolve(strict=False).relative_to(export_root.resolve(strict=False))
        except (OSError, ValueError):
            return None, "", f"Texture2D dependency is not copied or bound: {source_texture_relative_path}"
        return texture_path, texture_asset_path, ""

    globals_dict["_resolve_texture_export"] = resolve_texture_export


def enforce_partial_dependency_status(adapter: Any) -> None:
    original = adapter.export_project_fragment

    def wrapped(
        item: dict[str, Any],
        reconstruction: dict[str, Any],
        export_root: Path,
    ) -> dict[str, Any]:
        result = original(item, reconstruction, export_root)
        if str(item.get("root_type") or "") != "ParticleSystem":
            return result
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, dict):
            dependencies = {}
        missing = [
            name
            for name in ["renderers", "materials", "textures"]
            if not isinstance(dependencies.get(name), list) or not dependencies.get(name)
        ]
        if not missing or str(result.get("status") or "") == "failed":
            return result
        reason = f"missing resolved dependencies: {', '.join(missing)}"
        warnings = result.setdefault("warnings", [])
        if isinstance(warnings, list) and reason not in warnings:
            warnings.append(reason)
        result.update(
            {
                "status": "partial",
                "viewer_runtime_supported": False,
                "reconstruction_status": "partial",
                "unity_export_status": "partial",
                "ready": False,
                "reason": reason,
            }
        )
        return result

    adapter.export_project_fragment = wrapped


def install_progress_and_resume(exporter: Any, manifest_path: Path) -> None:
    original = exporter._export_item
    state = {"processed": 0, "resumed": 0}

    def tracked(**kwargs: Any) -> bool:
        item = kwargs["item"]
        paths = kwargs["paths"]
        reconstruction_path = exporter._reconstruction_path(paths.preview_index_dir, item)
        status = str(item.get("reconstruction_status") or "pending")
        if status != "pending" and reconstruction_path.is_file():
            state["resumed"] += 1
            failed = str(item.get("unity_export_status") or "") == "failed"
        else:
            failed = bool(original(**kwargs))
        state["processed"] += 1
        if state["processed"] % 1000 == 0:
            print(
                json.dumps(
                    {
                        "stage": "reconstruction",
                        "processed": state["processed"],
                        "resumed": state["resumed"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if state["processed"] % 5000 == 0:
            exporter.write_json_utf8(manifest_path, kwargs["manifest"])
        return failed

    exporter._export_item = tracked


def supplement_all_vfx_yaml(
    exporter: Any,
    resolved_paths: dict[str, Path],
    config: dict[str, Any],
) -> dict[str, Any]:
    assets_root = resolved_paths["assets_root"]
    csv_path = resolved_paths["structure_dir"] / "AssetRipper_VFX_YAML全量对账.csv"
    preview_dir = resolved_paths["preview_dir"]
    export_root = preview_dir / config["paths"]["unity_export_dir"]
    asset_root = export_root / Path(config["unity_export"]["asset_root"])
    original_root = asset_root / "OriginalAssets"
    metadata_dir = asset_root / config["unity_export"]["metadata_dir"]
    selected: list[Path] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source = (assets_root / str(row.get("relative_path") or "")).resolve(strict=False)
            try:
                source.relative_to(assets_root.resolve(strict=False))
            except ValueError:
                continue
            if source.is_file():
                selected.append(source)
    guid_map = exporter._build_unity_guid_asset_map(assets_root)
    copied: set[Path] = set()
    queued: set[Path] = set()
    queue: deque[Path] = deque()
    for source in selected:
        resolved_source = source.resolve(strict=False)
        if resolved_source not in queued:
            queued.add(resolved_source)
            queue.append(resolved_source)
    selected_set = set(queued)
    large_selected = {
        source
        for source in selected_set
        if source.stat().st_size > 25_000_000
    }
    large_guid_counts: dict[str, int] = {}
    while queue:
        source = queue.popleft()
        if source in copied or not source.is_file():
            continue
        try:
            source.relative_to(assets_root.resolve(strict=False))
        except ValueError:
            continue
        copied.add(source)
        exporter._copy_unity_asset_with_meta(source, assets_root, original_root)
        guids = exporter._unity_guids_in_asset(source)
        if source in large_selected:
            large_guid_counts[source.relative_to(assets_root).as_posix()] = len(guids)
        for guid in guids:
            dependency = guid_map.get(guid.lower())
            if dependency and dependency not in copied and dependency not in queued:
                queued.add(dependency)
                queue.append(dependency)
    result = {
        "schema_version": 1,
        "selected_vfx_yaml_count": len(selected),
        "copied_asset_count": len(copied),
        "dependency_count": len(copied - set(selected)),
        "large_selected_file_count": len(large_selected),
        "large_selected_scanned_count": len(large_guid_counts),
        "large_selected_guid_counts": dict(sorted(large_guid_counts.items())),
        "selected_relative_paths": [
            path.relative_to(assets_root).as_posix()
            for path in sorted(selected, key=lambda value: value.as_posix().lower())
        ],
    }
    exporter.write_json_utf8(metadata_dir / "original-vfx-yaml-full.json", result)
    return result


def copy_classified_dependencies(
    resolved_paths: dict[str, Path], config: dict[str, Any]
) -> dict[str, int]:
    asset_root = (
        resolved_paths["unity_export_dir"]
        / Path(config["unity_export"]["asset_root"])
        / "OriginalAssets"
    )
    classified = resolved_paths["classified_root"]
    mapping = {
        ".png": "粒子贴图/AssetRipper原始依赖",
        ".jpg": "粒子贴图/AssetRipper原始依赖",
        ".jpeg": "粒子贴图/AssetRipper原始依赖",
        ".tga": "粒子贴图/AssetRipper原始依赖",
        ".psd": "粒子贴图/AssetRipper原始依赖",
        ".mat": "VFX材质/AssetRipper原始依赖",
        ".shader": "VFX材质/Shader",
        ".shadergraph": "VFX材质/Shader",
        ".fbx": "VFX模型/AssetRipper原始依赖",
        ".obj": "VFX模型/AssetRipper原始依赖",
        ".anim": "VFX动画/AssetRipper原始依赖",
        ".controller": "VFX动画/AssetRipper原始依赖",
    }
    counts: dict[str, int] = {}
    for source in asset_root.rglob("*") if asset_root.is_dir() else []:
        category = mapping.get(source.suffix.lower()) if source.is_file() else None
        if category is None:
            continue
        relative = source.relative_to(asset_root)
        destination = classified / Path(category) / relative
        copy_if_changed(source, destination)
        counts[category] = counts.get(category, 0) + 1
    return counts


def source_observation(root: Path) -> dict[str, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return {"file_count": len(files), "total_bytes": sum(path.stat().st_size for path in files)}


def main() -> int:
    args = parse_args()
    study_root = args.study_root.resolve(strict=False)
    skill_root = args.skill_root.resolve(strict=False)
    config = read_json(args.config)
    exporter = load_module(skill_root / "scripts" / "export_unity_vfx_reconstruction.py")
    install_idempotent_io(exporter)
    resolved_paths = resolve_full_paths(exporter, study_root, config)
    before = source_observation(args.source_root)
    summary_path = resolved_paths["preview_dir"] / "全量重建摘要.json"
    if args.supplement_only:
        full_yaml = supplement_all_vfx_yaml(exporter, resolved_paths, config)
        classified_counts = copy_classified_dependencies(resolved_paths, config)
        after = source_observation(args.source_root)
        summary = read_json(summary_path) if summary_path.is_file() else {}
        summary.update(
            {
                "full_yaml_export": full_yaml,
                "classified_dependency_counts": classified_counts,
                "source_before": before,
                "source_after": after,
                "source_unchanged": before == after,
                "dependency_closure_refresh": {
                    "scanner": "streaming_full_file",
                    "queue": "deque_with_queued_and_copied_sets",
                    "supplement_only": True,
                },
            }
        )
        exporter.write_json_utf8(summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if before == after else 2

    manifest_path = resolved_paths["manifest"]
    unitypy_root = resolved_paths["unitypy_root"]
    adapter = exporter.get_adapter("unity", config)
    unique_texture_resolver(exporter, adapter)
    enforce_partial_dependency_status(adapter)
    exporter.get_adapter = lambda _engine, _config: adapter
    exporter._metadata_for_item = lambda item, _root: metadata_for_item(item, unitypy_root)
    install_progress_and_resume(exporter, manifest_path)
    result = exporter.export_reconstruction(study_root, args.config, manifest_path)
    full_yaml = supplement_all_vfx_yaml(exporter, resolved_paths, config)
    classified_counts = copy_classified_dependencies(resolved_paths, config)
    after = source_observation(args.source_root)
    summary = {
        "generic_export_result": result,
        "full_yaml_export": full_yaml,
        "classified_dependency_counts": classified_counts,
        "source_before": before,
        "source_after": after,
        "source_unchanged": before == after,
        "adapter_notes": {
            "type_tree_unwrapped": True,
            "min_max_curve_normalized": True,
            "single_adapter_reused": True,
            "collision_free_texture_names": True,
        },
    }
    exporter.write_json_utf8(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if before == after and int(result.get("failed_count", 0)) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

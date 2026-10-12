#!/usr/bin/env python3
"""Export Unity VFX reconstruction JSON and Unity import fragments."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import sys
import types
from collections import deque
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = SKILL_ROOT / "scripts"
MISSING_MODULE = object()


def module_inside_root(module: Any) -> bool:
    module_file = getattr(module, "__file__", None)
    if module_file:
        try:
            Path(module_file).resolve(strict=False).relative_to(SKILL_ROOT.resolve(strict=False))
            return True
        except ValueError:
            return False
    module_paths = getattr(module, "__path__", None)
    if module_paths:
        for item in module_paths:
            try:
                Path(item).resolve(strict=False).relative_to(SKILL_ROOT.resolve(strict=False))
                return True
            except ValueError:
                continue
    return False


def load_skill_module(
    module_name: str,
    module_path: Path,
    *,
    submodule_search_locations: list[str] | None = None,
    force: bool = False,
) -> Any:
    existing = sys.modules.get(module_name)
    if not force and existing is not None and module_inside_root(existing):
        return existing
    spec = importlib.util.spec_from_file_location(
        module_name,
        module_path,
        submodule_search_locations=submodule_search_locations,
    )
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load VFX dependency module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def restore_modules(snapshot: dict[str, Any]) -> None:
    for name, module in snapshot.items():
        if module is MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def load_unity_adapter_class() -> Any:
    module_names = [
        "scripts",
        "scripts.vfx_preview_common",
        "scripts.vfx_adapters",
        "scripts.vfx_adapters.unity",
    ]
    snapshot = {name: sys.modules.get(name, MISSING_MODULE) for name in module_names}
    try:
        package = types.ModuleType("scripts")
        package.__path__ = [str(SKILL_SCRIPTS)]  # type: ignore[attr-defined]
        package.__package__ = "scripts"
        sys.modules["scripts"] = package
        sys.modules["scripts.vfx_preview_common"] = _common
        load_skill_module(
            "scripts.vfx_adapters",
            SKILL_SCRIPTS / "vfx_adapters" / "__init__.py",
            submodule_search_locations=[str(SKILL_SCRIPTS / "vfx_adapters")],
            force=True,
        )
        unity = load_skill_module(
            "scripts.vfx_adapters.unity",
            SKILL_SCRIPTS / "vfx_adapters" / "unity.py",
            force=True,
        )
        return unity.UnityVfxAdapter
    finally:
        restore_modules(snapshot)


def get_adapter(engine: str, config: dict[str, Any]) -> Any:
    if engine.lower() == "unity":
        return load_unity_adapter_class()(config)
    raise ValueError(f"Unsupported VFX engine: {engine}")


def create_report_launcher(study_root: Path, config_path: Path | None) -> Path:
    launcher_module = load_skill_module(
        "_game_analysis_create_vfx_viewer_launcher",
        SKILL_SCRIPTS / "create_vfx_viewer_launcher.py",
    )
    return launcher_module.create_launcher(study_root, SKILL_ROOT, config_path)


_common = load_skill_module("_game_analysis_vfx_preview_common", SKILL_SCRIPTS / "vfx_preview_common.py")

as_relative_path = _common.as_relative_path
ensure_inside_root = _common.ensure_inside_root
load_vfx_config = _common.load_vfx_config
vfx_paths = _common.vfx_paths
write_json_utf8 = _common.write_json_utf8


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
UNITY_GUID_RE = re.compile(r"guid:\s*([0-9a-fA-F]{32})")
UNITY_YAML_HEADER_RE = re.compile(r"^--- !u!(?P<class_id>\d+) &(?P<file_id>-?\d+)\s*$", re.MULTILINE)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def export_reconstruction(
    study_root: Path,
    config_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    study_root = study_root.resolve(strict=False)
    config = load_vfx_config(config_path)
    paths = vfx_paths(study_root, config)
    paths.preview_cache_dir.mkdir(parents=True, exist_ok=True)
    target_manifest = _resolve_manifest_path(study_root, manifest_path or paths.manifest)
    manifest = read_json(target_manifest)
    unitypy_root = _unitypy_root_from_manifest(study_root, manifest, paths.unitypy_root)

    updated_count = 0
    failed_count = 0
    items = manifest.get("items", [])
    if not isinstance(items, list):
        raise ValueError("manifest.items must be a list")

    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            export_failed = _export_item(
                item=item,
                manifest=manifest,
                config=config,
                paths=paths,
                unitypy_root=unitypy_root,
            )
        except Exception as exc:
            _mark_failed(item, str(exc))
            failed_count += 1
            continue
        updated_count += 1
        if export_failed:
            failed_count += 1

    original_prefabs = _export_original_assetripper_vfx_prefabs(study_root, config, paths.unity_export_dir)
    manifest["original_vfx_prefab_export"] = original_prefabs
    manifest["items"] = items
    write_json_utf8(target_manifest, manifest)
    launcher_path = create_report_launcher(study_root, config_path)
    return {
        "updated_count": updated_count,
        "failed_count": failed_count,
        "original_vfx_prefab_count": original_prefabs["prefab_count"],
        "original_vfx_dependency_count": original_prefabs["dependency_count"],
        "manifest_path": str(target_manifest),
        "launcher_path": str(launcher_path.resolve(strict=False)),
    }


def export_original_prefabs_only(
    study_root: Path,
    config_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    study_root = study_root.resolve(strict=False)
    config = load_vfx_config(config_path)
    paths = vfx_paths(study_root, config)
    target_manifest = _resolve_manifest_path(study_root, manifest_path or paths.manifest)
    manifest = read_json(target_manifest)
    original_prefabs = _export_original_assetripper_vfx_prefabs(
        study_root,
        config,
        paths.unity_export_dir,
    )
    manifest["original_vfx_prefab_export"] = original_prefabs
    write_json_utf8(target_manifest, manifest)
    launcher_path = create_report_launcher(study_root, config_path)
    return {
        "updated_count": 0,
        "failed_count": 0,
        "original_vfx_prefab_count": original_prefabs["prefab_count"],
        "original_vfx_dependency_count": original_prefabs["dependency_count"],
        "manifest_path": str(target_manifest),
        "launcher_path": str(launcher_path.resolve(strict=False)),
        "original_prefabs_only": True,
    }


def _export_item(
    *,
    item: dict[str, Any],
    manifest: dict[str, Any],
    config: dict[str, Any],
    paths: Any,
    unitypy_root: Path,
) -> bool:
    engine = str(item.get("engine") or manifest.get("engine") or config["adapter"]["default_engine"])
    adapter = get_adapter(engine, config)
    metadata = _metadata_for_item(item, unitypy_root)
    reconstruction = adapter.build_reconstruction(item, metadata)
    reconstruction["preview_status"] = "ready"
    reconstruction["reconstruction_status"] = "full"
    reconstruction["unity_export_status"] = "pending"
    item["__unitypy_root"] = str(unitypy_root)
    try:
        export_result = adapter.export_project_fragment(item, reconstruction, paths.unity_export_dir)
    finally:
        item.pop("__unitypy_root", None)

    reconstruction["preview_status"] = "ready" if export_result.get("viewer_runtime_supported") else "partial"
    reconstruction["reconstruction_status"] = str(export_result.get("reconstruction_status") or "partial")
    reconstruction["unity_export_status"] = str(export_result.get("unity_export_status") or "partial")
    reconstruction_path = _reconstruction_path(paths.preview_index_dir, item)
    write_json_utf8(reconstruction_path, reconstruction)

    item["preview_status"] = reconstruction["preview_status"]
    item["reconstruction_status"] = reconstruction["reconstruction_status"]
    item["unity_export_status"] = reconstruction["unity_export_status"]
    item["unity_export_relative_path"] = str(export_result.get("unity_export_relative_path") or "")
    item["unity_export_path"] = str(export_result.get("unity_export_path") or "")
    if export_result.get("reason"):
        item["unity_export_reason"] = str(export_result["reason"])
    warnings = export_result.get("warnings")
    if isinstance(warnings, list):
        item_warnings = item.setdefault("warnings", [])
        if isinstance(item_warnings, list):
            for warning in warnings:
                if warning not in item_warnings:
                    item_warnings.append(str(warning))
    return _export_result_failed(export_result)


def _resolve_manifest_path(study_root: Path, manifest_path: Path) -> Path:
    candidate = manifest_path if manifest_path.is_absolute() else study_root / manifest_path
    resolved = candidate.resolve(strict=False)
    if not ensure_inside_root(resolved, study_root):
        raise ValueError(f"manifest path is outside study-root: {manifest_path}")
    return resolved


def _unitypy_root_from_manifest(study_root: Path, manifest: dict[str, Any], default_root: Path) -> Path:
    # Manifest builder stores source_index after validating it is inside study_root.
    # The exporter preserves Task 3's custom-index behavior and uses index.parent as the metadata root.
    source_index = manifest.get("source_index")
    if not source_index:
        return default_root
    source_index_path = Path(str(source_index))
    if not source_index_path.is_absolute():
        source_index_path = study_root / source_index_path
    resolved = source_index_path.resolve(strict=False)
    if not ensure_inside_root(resolved, study_root):
        raise ValueError(f"manifest source_index is outside study-root: {source_index}")
    return resolved.parent


def _export_result_failed(export_result: dict[str, Any]) -> bool:
    return (
        str(export_result.get("status") or "").lower() == "failed"
        or str(export_result.get("unity_export_status") or "").lower() == "failed"
    )


def _metadata_for_item(item: dict[str, Any], unitypy_root: Path) -> dict[str, Any]:
    source_refs = item.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError("manifest item missing source_refs[0]")
    source = source_refs[0]
    if not isinstance(source, dict):
        raise ValueError("manifest item source_refs[0] must be an object")
    export_path = str(source.get("export_path") or "")
    if not export_path:
        return {}
    metadata_path = _safe_child_path(unitypy_root, export_path)
    if metadata_path is None:
        raise ValueError(f"metadata export_path is outside UnityPy root: {export_path}")
    if not metadata_path.is_file():
        return {}
    return read_json(metadata_path)


def _safe_child_path(root: Path, relative_value: str) -> Path | None:
    if relative_value.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(relative_value):
        return None
    try:
        resolved_root = root.resolve(strict=False)
        candidate = (root / relative_value).resolve(strict=False)
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate


def _reconstruction_path(preview_index_dir: Path, item: dict[str, Any]) -> Path:
    relative = str(item.get("reconstruction_relative_path") or "")
    if not relative:
        relative = f"reconstructed/{_safe_name(str(item.get('name') or 'vfx'))}-{_safe_name(str(item.get('path_id') or '0'))}.json"
        item["reconstruction_relative_path"] = relative
    candidate = preview_index_dir / as_relative_path(relative)
    if not ensure_inside_root(candidate, preview_index_dir):
        raise ValueError(f"reconstruction path is outside preview index root: {relative}")
    return candidate


def _safe_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value)).strip(" ._")
    return safe or "vfx"


def _mark_failed(item: dict[str, Any], reason: str) -> None:
    item["preview_status"] = "failed"
    item["reconstruction_status"] = "failed"
    item["unity_export_status"] = "failed"
    item["unity_export_relative_path"] = ""
    item["unity_export_path"] = ""
    item["unity_export_reason"] = reason


def _export_original_assetripper_vfx_prefabs(
    study_root: Path,
    config: dict[str, Any],
    unity_export_dir: Path,
) -> dict[str, Any]:
    configured_paths = config.get("paths", {})
    index_path = _configured_study_path(
        study_root,
        configured_paths.get("assetripper_prefab_index"),
        Path("4.临时目录") / "中间索引" / "AssetRipper分析" / "Prefab索引.csv",
    )
    asset_export_root = _configured_study_path(
        study_root,
        configured_paths.get("assetripper_export_root"),
        Path("0.原始导出") / "AssetRipper",
    )
    fallback_asset_root = asset_export_root / "ExportedProject" / "Assets"
    asset_root = unity_export_dir / as_relative_path(str(config["unity_export"]["asset_root"]))
    original_assets_root = asset_root / "OriginalAssets"
    metadata_dir = asset_root / str(config["unity_export"].get("metadata_dir") or "Metadata")
    index_output = metadata_dir / "original-vfx-prefabs.json"

    result: dict[str, Any] = {
        "schema_version": 1,
        "source": "AssetRipper Prefab索引.csv",
        "source_index": str(index_path.resolve(strict=False)),
        "source_assets_root": str(fallback_asset_root.resolve(strict=False)),
        "original_assets_root": str(original_assets_root.resolve(strict=False)),
        "unity_asset_root": str(as_relative_path(str(config["unity_export"]["asset_root"])).as_posix()),
        "prefab_count": 0,
        "dependency_count": 0,
        "copied_asset_count": 0,
        "prefabs": [],
        "dependencies": [],
        "warnings": [],
    }
    if not index_path.is_file():
        result["warnings"].append(f"AssetRipper prefab index not found: {index_path}")
        return result

    rows = _read_assetripper_prefab_rows(index_path)
    prefab_sources = _selected_assetripper_vfx_prefabs(asset_export_root, rows)
    source_asset_root = _infer_assetripper_assets_root(prefab_sources) or fallback_asset_root
    result["source_assets_root"] = str(source_asset_root.resolve(strict=False))
    if not source_asset_root.is_dir():
        result["warnings"].append(f"AssetRipper Assets directory not found: {source_asset_root}")
        return result
    guid_map = _build_unity_guid_asset_map(source_asset_root)
    copied_assets: set[Path] = set()
    copied_prefabs: set[Path] = set()
    queued_assets: set[Path] = set()
    queue: deque[Path] = deque()
    for source in prefab_sources:
        resolved_source = source.resolve(strict=False)
        if resolved_source not in queued_assets:
            queued_assets.add(resolved_source)
            queue.append(resolved_source)

    while queue:
        source = queue.popleft()
        if source in copied_assets:
            continue
        if not _path_is_inside(source, source_asset_root) or not source.is_file():
            continue
        copied_assets.add(source)
        if source.suffix.lower() == ".prefab" and source in prefab_sources:
            copied_prefabs.add(source)
        _copy_unity_asset_with_meta(source, source_asset_root, original_assets_root)
        for guid in _unity_guids_in_asset(source):
            dependency = guid_map.get(guid.lower())
            if dependency and dependency not in copied_assets and dependency not in queued_assets:
                queued_assets.add(dependency)
                queue.append(dependency)

    prefab_entries = []
    preview_root = metadata_dir / "OriginalPrefabPreviews"
    for source in sorted(copied_prefabs, key=lambda item: item.as_posix().lower()):
        relative = source.relative_to(source_asset_root).as_posix()
        preview_path = preview_root / Path(relative).with_suffix(".json")
        preview = _build_original_prefab_preview(source, relative)
        write_json_utf8(preview_path, preview)
        preview_unity_path = (
            f"{result['unity_asset_root']}/Metadata/OriginalPrefabPreviews/"
            f"{Path(relative).with_suffix('.json').as_posix()}"
        )
        prefab_entries.append(
            {
                "source_relative_path": f"ExportedProject/Assets/{relative}",
                "asset_relative_path": f"Assets/{relative}",
                "unity_asset_path": f"{result['unity_asset_root']}/OriginalAssets/{relative}",
                "preview_relative_path": preview_unity_path,
                "particle_system_count": preview["particle_system_count"],
            }
        )

    dependency_entries = []
    for source in sorted(copied_assets - copied_prefabs, key=lambda item: item.as_posix().lower()):
        relative = source.relative_to(source_asset_root).as_posix()
        dependency_entries.append(
            {
                "source_relative_path": f"ExportedProject/Assets/{relative}",
                "asset_relative_path": f"Assets/{relative}",
                "unity_asset_path": f"{result['unity_asset_root']}/OriginalAssets/{relative}",
            }
        )

    result["prefab_count"] = len(prefab_entries)
    result["dependency_count"] = len(dependency_entries)
    result["copied_asset_count"] = len(copied_assets)
    result["prefabs"] = prefab_entries
    result["dependencies"] = dependency_entries
    metadata_dir.mkdir(parents=True, exist_ok=True)
    write_json_utf8(index_output, result)
    return result


def _read_assetripper_prefab_rows(index_path: Path) -> list[dict[str, str]]:
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _configured_study_path(study_root: Path, value: Any, fallback: Path) -> Path:
    relative = as_relative_path(str(value)) if value else fallback
    candidate = (study_root / relative).resolve(strict=False)
    if not ensure_inside_root(candidate, study_root):
        raise ValueError(f"configured VFX source path is outside study root: {candidate}")
    return candidate


def _selected_assetripper_vfx_prefabs(
    asset_export_root: Path,
    rows: list[dict[str, str]],
) -> set[Path]:
    selected: set[Path] = set()
    for row in rows:
        relative_path = str(row.get("relative_path") or "")
        if not relative_path.lower().endswith(".prefab"):
            continue
        particle_count = _safe_int(row.get("particle_count"))
        tags = str(row.get("scene_building_tags") or "")
        if particle_count <= 0 and "粒子特效" not in tags:
            continue
        source = _assetripper_relative_to_source_path(asset_export_root, relative_path)
        if source is not None:
            selected.add(source.resolve(strict=False))
    return selected


def _assetripper_relative_to_source_path(
    asset_export_root: Path,
    relative_path: str,
) -> Path | None:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if normalized.startswith("../") or WINDOWS_DRIVE_RE.match(normalized):
        return None
    candidates = [
        asset_export_root / normalized,
        asset_export_root / "ExportedProject" / normalized,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(strict=False)
    return candidates[0].resolve(strict=False)


def _infer_assetripper_assets_root(prefab_sources: set[Path]) -> Path | None:
    for source in sorted(prefab_sources, key=lambda item: item.as_posix().lower()):
        for parent in source.parents:
            if parent.name == "Assets" and parent.is_dir():
                return parent.resolve(strict=False)
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _build_unity_guid_asset_map(source_asset_root: Path) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for meta_path in source_asset_root.rglob("*.meta"):
        try:
            text = meta_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(r"^guid:\s*([0-9a-fA-F]{32})\s*$", text, re.MULTILINE)
        if not match:
            continue
        asset_path = Path(str(meta_path)[: -len(".meta")])
        if asset_path.is_file() and _path_is_inside(asset_path, source_asset_root):
            mapping[match.group(1).lower()] = asset_path.resolve(strict=False)
    return mapping


def _copy_unity_asset_with_meta(source: Path, source_asset_root: Path, target_asset_root: Path) -> None:
    relative = source.relative_to(source_asset_root)
    target = target_asset_root / relative
    if not _path_is_inside(target.resolve(strict=False), target_asset_root):
        raise ValueError(f"Unity VFX original asset target escapes export root: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    meta = Path(str(source) + ".meta")
    if meta.is_file():
        meta_target = Path(str(target) + ".meta")
        shutil.copy2(meta, meta_target)


def _unity_guids_in_asset(source: Path) -> set[str]:
    guids: set[str] = set()
    try:
        with source.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                guids.update(match.group(1).lower() for match in UNITY_GUID_RE.finditer(line))
    except OSError:
        return set()
    return guids


def _build_original_prefab_preview(source: Path, asset_relative_path: str) -> dict[str, Any]:
    text = source.read_text(encoding="utf-8", errors="ignore")
    sections = _unity_yaml_sections(text)
    game_objects: dict[str, str] = {}
    renderers_by_game_object: dict[str, list[dict[str, Any]]] = {}
    particle_systems: list[dict[str, Any]] = []

    for section in sections:
        if section["class_id"] == "1":
            name = _extract_yaml_value(section["body"], "m_Name")
            game_objects[section["file_id"]] = name or f"GameObject_{section['file_id']}"

    for section in sections:
        if section["class_id"] != "199":
            continue
        game_object_id = _extract_file_id_ref(section["body"], "m_GameObject")
        if not game_object_id:
            continue
        renderers_by_game_object.setdefault(game_object_id, []).append(
            {
                "file_id": section["file_id"],
                "materialGuids": sorted(_unity_guids_in_text(section["body"])),
                "enabled": _extract_yaml_bool(section["body"], "m_Enabled", True),
            }
        )

    for section in sections:
        if section["class_id"] != "198":
            continue
        body = section["body"]
        game_object_id = _extract_file_id_ref(body, "m_GameObject")
        renderer = (renderers_by_game_object.get(game_object_id or "") or [{}])[0]
        particle_systems.append(
            {
                "file_id": section["file_id"],
                "game_object_id": game_object_id or "",
                "game_object_name": game_objects.get(game_object_id or "", f"ParticleSystem_{section['file_id']}"),
                "main": {
                    "duration": _extract_yaml_number(body, "lengthInSec", 5.0),
                    "looping": _extract_yaml_bool(body, "looping", True),
                    "startLifetime": {"constant": _extract_module_scalar(body, "InitialModule", "startLifetime", 1.0)},
                    "startSpeed": {"constant": _extract_module_scalar(body, "InitialModule", "startSpeed", 1.0)},
                    "startSize": {"constant": _extract_module_scalar(body, "InitialModule", "startSize", 0.5)},
                },
                "emission": {
                    "enabled": _extract_module_bool(body, "EmissionModule", "enabled", True),
                    "rateOverTime": {"constant": _extract_module_scalar(body, "EmissionModule", "rateOverTime", 10.0)},
                },
                "shape": {
                    "enabled": _extract_module_bool(body, "ShapeModule", "enabled", True),
                    "shapeType": _shape_type_name(int(_extract_module_number(body, "ShapeModule", "type", 0))),
                    "angle": _extract_module_number(body, "ShapeModule", "angle", 25.0),
                    "radius": _extract_module_number(body, "ShapeModule", "radius", 1.0),
                },
                "renderer": renderer,
            }
        )

    return {
        "schema_version": 1,
        "engine": "unity",
        "kind": "original_prefab_preview",
        "name": source.stem,
        "source_relative_path": f"ExportedProject/Assets/{asset_relative_path}",
        "asset_relative_path": f"Assets/{asset_relative_path}",
        "particle_system_count": len(particle_systems),
        "particle_systems": particle_systems,
    }


def _unity_yaml_sections(text: str) -> list[dict[str, str]]:
    matches = list(UNITY_YAML_HEADER_RE.finditer(text))
    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            {
                "class_id": match.group("class_id"),
                "file_id": match.group("file_id"),
                "body": text[body_start:body_end],
            }
        )
    return sections


def _extract_yaml_value(body: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*(.*?)\s*$", body)
    if not match:
        return ""
    return match.group(1).strip().strip('"')


def _extract_file_id_ref(body: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}:\s*\{{fileID:\s*(-?\d+)", body)
    return match.group(1) if match else ""


def _extract_yaml_number(body: str, key: str, fallback: float) -> float:
    value = _extract_yaml_value(body, key)
    try:
        return float(value)
    except ValueError:
        return fallback


def _extract_yaml_bool(body: str, key: str, fallback: bool) -> bool:
    value = _extract_yaml_value(body, key)
    if value in {"0", "1"}:
        return value == "1"
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return fallback


def _module_block(body: str, module: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"{module}:":
            continue
        base_indent = len(line) - len(line.lstrip())
        collected = []
        for child in lines[index + 1 :]:
            if child.strip():
                indent = len(child) - len(child.lstrip())
                if indent <= base_indent:
                    break
            collected.append(child)
        return "\n".join(collected)
    return ""


def _nested_scalar(body: str, field: str, fallback: float) -> float:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"{field}:":
            continue
        base_indent = len(line) - len(line.lstrip())
        for child in lines[index + 1 :]:
            if child.strip():
                indent = len(child) - len(child.lstrip())
                if indent <= base_indent:
                    break
                if child.strip().startswith("scalar:"):
                    try:
                        return float(child.split(":", 1)[1].strip())
                    except ValueError:
                        return fallback
    return fallback


def _extract_module_scalar(body: str, module: str, field: str, fallback: float) -> float:
    return _nested_scalar(_module_block(body, module), field, fallback)


def _extract_module_number(body: str, module: str, key: str, fallback: float) -> float:
    return _extract_yaml_number(_module_block(body, module), key, fallback)


def _extract_module_bool(body: str, module: str, key: str, fallback: bool) -> bool:
    return _extract_yaml_bool(_module_block(body, module), key, fallback)


def _unity_guids_in_text(text: str) -> set[str]:
    return {
        match.group(1).lower()
        for match in UNITY_GUID_RE.finditer(text)
        if match.group(1).lower() != "00000000000000000000000000000000"
    }


def _shape_type_name(shape_type: int) -> str:
    names = {
        0: "Sphere",
        1: "Hemisphere",
        2: "Cone",
        3: "Box",
        4: "Cone",
        5: "Mesh",
        6: "Circle",
        7: "Edge",
        8: "Rectangle",
    }
    return names.get(shape_type, f"Shape{shape_type}")


def _path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument(
        "--original-prefabs-only",
        action="store_true",
        help="Refresh only the AssetRipper original VFX prefab supplement and leave VFX items unchanged.",
    )
    args = parser.parse_args(argv)
    if args.original_prefabs_only:
        result = export_original_prefabs_only(args.study_root, args.config, args.manifest)
    else:
        result = export_reconstruction(args.study_root, args.config, args.manifest)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

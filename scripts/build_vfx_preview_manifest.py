#!/usr/bin/env python3
"""Build an adapter-driven VFX preview manifest."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import shutil
import sys
import types
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


_common = load_skill_module("_game_analysis_vfx_preview_common", SKILL_SCRIPTS / "vfx_preview_common.py")

ensure_inside_root = _common.ensure_inside_root
load_vfx_config = _common.load_vfx_config
vfx_paths = _common.vfx_paths
write_json_utf8 = _common.write_json_utf8
write_jsonl_utf8 = _common.write_jsonl_utf8


REQUIRED_CSV_COLUMNS = {"source_file", "path_id", "asset_type", "asset_name", "export_path"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga"}


def safe_name(value: str, fallback: str = "vfx") -> str:
    value = value.strip() if value else ""
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._ ")
    return value[:120] or fallback


def read_rows(index_path: Path) -> list[dict[str, str]]:
    rows, _errors, _fatal = read_rows_with_errors(index_path)
    return rows


def read_rows_with_errors(index_path: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]], bool]:
    if not index_path.is_file():
        return [], [{"code": "missing_index", "path": str(index_path)}], False
    with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], [{"code": "missing_csv_header", "path": str(index_path)}], True
        missing = sorted(REQUIRED_CSV_COLUMNS - set(reader.fieldnames))
        if missing:
            return [], [{"code": "missing_required_columns", "missing": missing, "path": str(index_path)}], True
        rows: list[dict[str, str]] = []
        errors: list[dict[str, Any]] = []
        extra_columns_recorded = False
        for line_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                if not extra_columns_recorded:
                    errors.append({"code": "extra_csv_columns", "path": str(index_path), "line": line_number})
                    extra_columns_recorded = True
                raw_row.pop(None, None)
            rows.append({str(key): value or "" for key, value in raw_row.items()})
        return rows, errors, False


def keyword_tags(name: str, config: dict[str, Any]) -> list[str]:
    lower = name.lower()
    tags: list[str] = []
    for rule in config.get("name_keyword_rules", []):
        tag = str(rule.get("tag") or "")
        keywords = [str(item).lower() for item in rule.get("keywords", [])]
        if tag and any(keyword and keyword in lower for keyword in keywords):
            tags.append(tag)
    return tags


def load_metadata(unitypy_root: Path, export_path: str, max_depth: int) -> dict[str, Any]:
    if not export_path:
        return {}
    source = safe_child_path(unitypy_root, export_path)
    if source is None or not source.is_file() or source.suffix.lower() != ".json":
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return trim_json(payload, max_depth=max_depth)


def trim_json(value: Any, *, max_depth: int, depth: int = 0) -> Any:
    if depth >= max_depth:
        if isinstance(value, (dict, list)):
            return "<truncated>"
        return value
    if isinstance(value, dict):
        return {str(key): trim_json(item, max_depth=max_depth, depth=depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, list):
        return [trim_json(item, max_depth=max_depth, depth=depth + 1) for item in value[:80]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def vfx_rule_for(row: dict[str, str], config: dict[str, Any]) -> dict[str, Any] | None:
    asset_type = row.get("asset_type", "")
    rule = config.get("vfx_type_rules", {}).get(asset_type)
    if not isinstance(rule, dict):
        return None
    tags = keyword_tags(row.get("asset_name", ""), config)
    if rule.get("requires_keyword") and not tags:
        return None
    return rule


def copy_texture_if_available(
    *,
    row: dict[str, str],
    unitypy_root: Path,
    classified_vfx_dir: Path,
    tags: list[str],
) -> str:
    export_path = row.get("export_path", "")
    if not export_path:
        return ""
    source = safe_child_path(unitypy_root, export_path)
    if source is None or not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
        return ""
    group = safe_name((tags[0].split(":", 1)[-1] if tags else row.get("asset_name", "")), "未分组")
    destination = classified_vfx_dir / "粒子贴图" / group / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination)


def write_config_card(
    *,
    row: dict[str, str],
    metadata: dict[str, Any],
    classified_vfx_dir: Path,
    category: str,
) -> Path:
    card_path = config_card_path(
        row=row,
        classified_vfx_dir=classified_vfx_dir,
        category=category,
    )
    card = {
        "schema_version": 1,
        "asset_id": row.get("asset_id", ""),
        "asset_type": row.get("asset_type", "Unknown"),
        "asset_name": row.get("asset_name", ""),
        "source_file": row.get("source_file", ""),
        "path_id": row.get("path_id", ""),
        "source_export_path": row.get("export_path", ""),
        "metadata": metadata,
    }
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return card_path


def config_card_path(
    *,
    row: dict[str, str],
    classified_vfx_dir: Path,
    category: str,
) -> Path:
    name = safe_name(row.get("asset_name", "") or f"pathid_{row.get('path_id', '')}")
    card_dir = classified_category_path(classified_vfx_dir, category)
    path_id = safe_name(row.get("path_id", "0"), "0")
    return card_dir / f"{name}_{path_id}.json"


def classified_category_path(classified_vfx_dir: Path, category: str) -> Path:
    if category.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", category):
        raise ValueError(f"classified category is outside classified root: {category}")
    parts = re.split(r"[\\/]+", category)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"classified category is outside classified root: {category}")
    candidate = classified_vfx_dir.joinpath(*(safe_name(part, "category") for part in parts))
    try:
        candidate.resolve(strict=False).relative_to(classified_vfx_dir.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"classified category is outside classified root: {category}") from exc
    return candidate


def build_classified_vfx_outputs(
    *,
    rows: list[dict[str, str]],
    config: dict[str, Any],
    unitypy_root: Path,
    classified_vfx_dir: Path,
    resume_existing_cards: bool = False,
) -> None:
    classified_vfx_dir.mkdir(parents=True, exist_ok=True)
    max_depth = int(config["limits"]["max_config_depth"])
    for row in rows:
        rule = vfx_rule_for(row, config)
        if rule is None:
            continue
        tags = keyword_tags(row.get("asset_name", ""), config)
        category = str(rule["category"])
        preview_mode = str(rule["preview_mode"])
        if preview_mode == "texture_sheet":
            copy_texture_if_available(
                row=row,
                unitypy_root=unitypy_root,
                classified_vfx_dir=classified_vfx_dir,
                tags=tags,
            )
        else:
            if resume_existing_cards and config_card_path(
                row=row,
                classified_vfx_dir=classified_vfx_dir,
                category=category,
            ).is_file():
                continue
            metadata = load_metadata(unitypy_root, row.get("export_path", ""), max_depth=max_depth)
            write_config_card(
                row=row,
                metadata=metadata,
                classified_vfx_dir=classified_vfx_dir,
                category=category,
            )


def safe_child_path(root: Path, relative_value: str) -> Path | None:
    if relative_value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", relative_value):
        return None
    try:
        resolved_root = root.resolve(strict=False)
        candidate = (root / relative_value).resolve(strict=False)
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return candidate


def resolve_index_and_unitypy_root(study_root: Path, index_path: Path | None, default_index: Path, default_root: Path) -> tuple[Path, Path]:
    if index_path is None:
        return default_index, default_root
    resolved = index_path.resolve(strict=False)
    if not ensure_inside_root(resolved, study_root):
        raise ValueError(f"index path is outside study-root: {index_path}")
    return resolved, resolved.parent


def empty_manifest(config: dict[str, Any], engine: str, study_root: Path, source_index: Path) -> dict[str, Any]:
    return {
        "schema_version": config["manifest"]["schema_version"],
        "engine": engine,
        "study_root": str(study_root),
        "source_index": str(source_index),
        "item_count": 0,
        "items": [],
    }


def build_vfx_manifest(
    study_root: Path,
    config_path: Path | None = None,
    index_path: Path | None = None,
    *,
    resume_classified: bool = False,
) -> dict[str, Any]:
    study_root = study_root.resolve(strict=False)
    config = load_vfx_config(config_path)
    paths = vfx_paths(study_root, config)
    engine = config["adapter"]["default_engine"]
    index, unitypy_root = resolve_index_and_unitypy_root(study_root, index_path, paths.unitypy_index, paths.unitypy_root)
    rows, errors, fatal_csv_error = read_rows_with_errors(index)
    if fatal_csv_error:
        manifest = empty_manifest(config, engine, study_root, index)
        write_json_utf8(paths.manifest, manifest)
        write_jsonl_utf8(paths.errors_jsonl, errors)
        return manifest

    max_items = int(config["limits"]["max_items"])
    if max_items <= 0:
        raise ValueError(f"max_items must be greater than 0: {max_items}")

    build_classified_vfx_outputs(
        rows=rows,
        config=config,
        unitypy_root=unitypy_root,
        classified_vfx_dir=paths.classified_vfx_dir,
        resume_existing_cards=resume_classified,
    )
    adapter = get_adapter(engine, config)
    roots = adapter.discover_roots(rows, unitypy_root)
    rows_by_source: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        rows_by_source.setdefault(row.get("source_file", ""), []).append(row)
    if len(roots) > max_items:
        errors.append({"code": "max_items_reached", "limit": max_items, "total_roots": len(roots)})
    items = [
        adapter.build_manifest_item(
            root,
            rows_by_source.get(root.get("source_file", ""), []),
            unitypy_root,
            study_root,
        )
        for root in roots[:max_items]
    ]

    manifest = {
        "schema_version": config["manifest"]["schema_version"],
        "engine": engine,
        "study_root": str(study_root),
        "source_index": str(index),
        "item_count": len(items),
        "items": items,
    }
    write_json_utf8(paths.manifest, manifest)
    write_jsonl_utf8(paths.errors_jsonl, errors)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument(
        "--resume-classified",
        action="store_true",
        help="Preserve already existing configuration cards while rebuilding the manifest.",
    )
    args = parser.parse_args(argv)
    manifest = build_vfx_manifest(
        args.study_root,
        args.config,
        args.index,
        resume_classified=args.resume_classified,
    )
    print(json.dumps({"item_count": manifest["item_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

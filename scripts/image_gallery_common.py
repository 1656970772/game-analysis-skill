from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_RELATIVE_PATH = Path("references") / "image-gallery-viewer-config.json"
PATH_SEPARATOR_RE = re.compile(r"[\\/]+")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class GalleryPaths:
    report_dir: Path
    index_dir: Path
    manifest: Path
    errors_jsonl: Path
    thumbnails_dir: Path
    launcher: Path


def load_config(skill_root: Path) -> dict[str, Any]:
    config_path = skill_root / CONFIG_RELATIVE_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("image gallery config must be an object")
    required_top_level = [
        "tool",
        "launcher",
        "paths",
        "default_image_roots",
        "image_extensions",
        "thumbnail",
        "manifest",
        "ui",
    ]
    missing = [key for key in required_top_level if key not in config]
    if missing:
        raise ValueError(f"image gallery config missing keys: {', '.join(missing)}")
    validate_config(config)
    return config


def as_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("config-relative path must be a non-empty string")
    if value.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"invalid config-relative path {value!r}: absolute paths are not allowed")
    parts = PATH_SEPARATOR_RE.split(value)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid config-relative path {value!r}: traversal or empty parts are not allowed")
    return Path(*parts)


def validate_config(config: dict[str, Any]) -> None:
    tool = require_object(config, "tool")
    require_string(tool, "name", "tool.name")
    require_string(tool, "version", "tool.version")
    require_config_path(tool, "exe_relative_path", "tool.exe_relative_path")
    require_config_path(tool, "source_template_dir", "tool.source_template_dir")

    launcher = require_object(config, "launcher")
    require_filename_only(launcher, "bat_name", "launcher.bat_name")
    require_string(launcher, "window_title", "launcher.window_title")

    paths = require_object(config, "paths")
    for key in ["report_dir", "index_dir", "manifest_name", "errors_name", "thumbnails_dir"]:
        require_config_path(paths, key, f"paths.{key}")

    default_roots = require_list(config, "default_image_roots", "default_image_roots")
    for index, item in enumerate(default_roots):
        item_path = f"default_image_roots[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"image gallery config field {item_path} must be an object")
        require_string(item, "label", f"{item_path}.label")
        require_config_path(item, "relative_path", f"{item_path}.relative_path")

    extensions = require_list(config, "image_extensions", "image_extensions")
    for index, item in enumerate(extensions):
        if not isinstance(item, str):
            raise ValueError(f"image gallery config field image_extensions[{index}] must be a string")

    thumbnail = require_object(config, "thumbnail")
    require_int(thumbnail, "max_size", "thumbnail.max_size")
    require_string(thumbnail, "format", "thumbnail.format")
    require_string(thumbnail, "checker_dark", "thumbnail.checker_dark")
    require_string(thumbnail, "checker_light", "thumbnail.checker_light")
    require_int(thumbnail, "checker_cell", "thumbnail.checker_cell")

    manifest = require_object(config, "manifest")
    require_int(manifest, "schema_version", "manifest.schema_version")
    status_values = require_list(manifest, "status_values", "manifest.status_values")
    for index, item in enumerate(status_values):
        if not isinstance(item, str):
            raise ValueError(f"image gallery config field manifest.status_values[{index}] must be a string")
    require_int(manifest, "max_error_records", "manifest.max_error_records")

    ui = require_object(config, "ui")
    require_string(ui, "default_sort", "ui.default_sort")
    page_sizes = require_list(ui, "page_sizes", "ui.page_sizes")
    for index, item in enumerate(page_sizes):
        if type(item) is not int:
            raise ValueError(f"image gallery config field ui.page_sizes[{index}] must be an integer")
    require_int(ui, "default_page_size", "ui.default_page_size")


def require_field(container: dict[str, Any], key: str, field_path: str) -> Any:
    if key not in container:
        raise ValueError(f"image gallery config missing required field: {field_path}")
    return container[key]


def require_object(container: dict[str, Any], key: str, field_path: str | None = None) -> dict[str, Any]:
    path = field_path or key
    value = require_field(container, key, path)
    if not isinstance(value, dict):
        raise ValueError(f"image gallery config field {path} must be an object")
    return value


def require_list(container: dict[str, Any], key: str, field_path: str) -> list[Any]:
    value = require_field(container, key, field_path)
    if not isinstance(value, list):
        raise ValueError(f"image gallery config field {field_path} must be a list")
    return value


def require_string(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_field(container, key, field_path)
    if not isinstance(value, str):
        raise ValueError(f"image gallery config field {field_path} must be a string")
    return value


def require_filename_only(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_string(container, key, field_path)
    if not value:
        raise ValueError(f"image gallery config field {field_path} must be a filename-only value")
    if WINDOWS_DRIVE_RE.match(value) or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"image gallery config field {field_path} must be a filename-only value")
    return value


def require_int(container: dict[str, Any], key: str, field_path: str) -> int:
    value = require_field(container, key, field_path)
    if type(value) is not int:
        raise ValueError(f"image gallery config field {field_path} must be an integer")
    return value


def require_config_path(container: dict[str, Any], key: str, field_path: str) -> str:
    value = require_string(container, key, field_path)
    try:
        as_path(value)
    except ValueError as exc:
        raise ValueError(f"image gallery config field {field_path} must be a config-relative path: {exc}") from exc
    return value


def gallery_paths(study_root: Path, config: dict[str, Any]) -> GalleryPaths:
    paths = config["paths"]
    report_dir = study_root / as_path(paths["report_dir"])
    index_dir = study_root / as_path(paths["index_dir"])
    launcher_name = config["launcher"]["bat_name"]
    return GalleryPaths(
        report_dir=report_dir,
        index_dir=index_dir,
        manifest=index_dir / as_path(paths["manifest_name"]),
        errors_jsonl=index_dir / as_path(paths["errors_name"]),
        thumbnails_dir=index_dir / as_path(paths["thumbnails_dir"]),
        launcher=report_dir / launcher_name,
    )


def stable_id(source_label: str, relative_path: str, size_bytes: int) -> str:
    normalized_relative_path = relative_path.replace("\\", "/")
    payload = f"{source_label}\n{normalized_relative_path}\n{size_bytes}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def resolve_image_roots(
    study_root: Path,
    config: dict[str, Any],
    extra_roots: list[Path],
) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    for item in config["default_image_roots"]:
        candidate = study_root / as_path(item["relative_path"])
        if candidate.is_dir():
            roots.append((item["label"], candidate))
    for index, root in enumerate(extra_roots, start=1):
        resolved = root.resolve()
        if not resolved.is_dir():
            raise ValueError(f"extra image root does not exist or is not a directory: {resolved}")
        roots.append((f"自定义图片根{index}", resolved))
    return roots


def ensure_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

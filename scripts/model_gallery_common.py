from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


REQUIRED_CONFIG_KEYS = (
    "tool",
    "launcher",
    "formats",
    "manifest",
    "paths",
    "thumbnail",
    "ui",
    "limits",
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_VIEWER_CONFIG = SKILL_ROOT / "references" / "model-viewer-gallery-config.json"


def resolve_model_viewer_config_path(config_path: Path | None = None) -> Path:
    if config_path is None:
        return DEFAULT_MODEL_VIEWER_CONFIG
    return config_path.expanduser().resolve(strict=False)


def load_model_viewer_config(config_path: Path | None = None) -> dict[str, Any]:
    resolved_config = resolve_model_viewer_config_path(config_path)
    data = json.loads(resolved_config.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in data]
    if missing:
        raise ValueError(f"model viewer config missing keys: {', '.join(missing)}")
    return data


def resolve_existing(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def ensure_inside_root(path: Path, root: Path) -> Path:
    resolved_path = resolve_existing(path)
    resolved_root = resolve_existing(root)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path is outside study-root: {resolved_path}") from exc
    return resolved_path


def stable_model_id(source_path: Path, root: Path) -> str:
    resolved_root = resolve_existing(root)
    resolved_source = ensure_inside_root(source_path, resolved_root)
    relative_path = resolved_source.relative_to(resolved_root).as_posix().lower()
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def write_json_utf8(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

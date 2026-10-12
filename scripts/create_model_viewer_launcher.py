#!/usr/bin/env python3
"""Create the Windows launcher for GameModelViewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SCRIPT_ROOT / "references" / "model-viewer-gallery-config.json"


def win(path: Path) -> str:
    return str(path).replace("/", "\\")


def batch_literal(value: str) -> str:
    return value.replace("%", "%%")


def win_literal(path: Path) -> str:
    return batch_literal(win(path))


def _configured_path(root: Path, config: dict[str, Any], key: str) -> Path:
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else root / configured
    return ensure_inside_root(candidate, root)


def _configured_child(root: Path, parent: Path, config: dict[str, Any], key: str) -> Path:
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else parent / configured
    return ensure_inside_root(candidate, root)


def _tool_relative(config: dict[str, Any]) -> Path:
    return Path(config["tool"]["directory"]) / config["tool"]["exe_name"]


def _launcher_bat_names(launcher: dict[str, Any]) -> list[str]:
    names = [str(launcher["bat_name"])]
    names.extend(str(name) for name in launcher.get("alias_bat_names", []))
    deduped: list[str] = []
    for name in names:
        normalized = name.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or Path(normalized).is_absolute()
        ):
            raise ValueError(f"launcher BAT name must be a filename: {name}")
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _deprecated_launcher_bat_names(launcher: dict[str, Any]) -> list[str]:
    names = [str(name) for name in launcher.get("deprecated_alias_bat_names", [])]
    deduped: list[str] = []
    for name in names:
        normalized = name.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or Path(normalized).is_absolute()
        ):
            raise ValueError(f"deprecated launcher BAT name must be a filename: {name}")
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def create_launcher(study_root: Path, skill_root: Path, config: dict[str, Any]) -> Path:
    root_path = resolve_existing(Path(study_root))
    skill_root_path = resolve_existing(Path(skill_root))

    report_dir = _configured_path(root_path, config, "report_dir")
    report_dir.mkdir(parents=True, exist_ok=True)

    preview_index = _configured_path(root_path, config, "preview_index_dir")
    models_dir = _configured_path(root_path, config, "models_dir")
    manifest_path = _configured_child(root_path, preview_index, config, "manifest_name")
    thumbs_dir = _configured_child(root_path, preview_index, config, "thumbs_dir")
    preview_cache_dir = _configured_child(root_path, preview_index, config, "preview_cache_dir")

    launcher = config["launcher"]
    env_exe = launcher["environment_exe"]
    env_skill_root = launcher["environment_skill_root"]
    bat_names = _launcher_bat_names(launcher)
    bat_paths = [ensure_inside_root(report_dir / bat_name, root_path) for bat_name in bat_names]
    bat_path = bat_paths[0]

    tool_relative = _tool_relative(config)
    if tool_relative.is_absolute():
        fallback_exe = resolve_existing(tool_relative)
        env_skill_exe = win_literal(fallback_exe)
    else:
        fallback_exe = resolve_existing(skill_root_path / tool_relative)
        env_skill_exe = f"%{env_skill_root}%\\{win(tool_relative)}"

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        "",
        f'set "STUDY_ROOT={win_literal(root_path)}"',
        f'set "SKILL_ROOT={win_literal(skill_root_path)}"',
        'set "VIEWER_EXE="',
        f'if not "%{env_exe}%"=="" set "VIEWER_EXE=%{env_exe}%"',
        (
            f'if "%VIEWER_EXE%"=="" if not "%{env_skill_root}%"=="" '
            f'set "VIEWER_EXE={env_skill_exe}"'
        ),
        f'if "%VIEWER_EXE%"=="" set "VIEWER_EXE={win_literal(fallback_exe)}"',
        "",
        'if not exist "%VIEWER_EXE%" (',
        '  echo 错误：找不到模型预览器：%VIEWER_EXE%',
        f"  echo 请设置 {env_exe} 或 {env_skill_root}，或安装默认工具。",
        "  pause",
        "  exit /b 1",
        ")",
        "",
        'start "" "%VIEWER_EXE%" ^',
        '  --study-root "%STUDY_ROOT%" ^',
        f'  --models-dir "{win_literal(models_dir)}" ^',
        f'  --manifest "{win_literal(manifest_path)}" ^',
        f'  --thumbs-dir "{win_literal(thumbs_dir)}" ^',
        f'  --preview-cache-dir "{win_literal(preview_cache_dir)}"',
        "",
        "endlocal",
        "exit /b 0",
    ]

    content = "\r\n".join(lines) + "\r\n"
    for path in bat_paths:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    active_paths = {path.resolve() for path in bat_paths}
    for bat_name in _deprecated_launcher_bat_names(launcher):
        deprecated_path = ensure_inside_root(report_dir / bat_name, root_path)
        if deprecated_path.resolve() not in active_paths and deprecated_path.exists():
            deprecated_path.unlink()
    return bat_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create GameModelViewer launcher BAT.")
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_model_viewer_config(args.config)
    bat_path = create_launcher(args.study_root, args.skill_root, config)
    print(f"wrote {bat_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

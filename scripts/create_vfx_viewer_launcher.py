#!/usr/bin/env python3
"""Create the Windows launcher for GameVfxViewer."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "references" / "vfx-preview-config.json"


def load_skill_common() -> Any:
    common_path = SKILL_ROOT / "scripts" / "vfx_preview_common.py"
    spec = importlib.util.spec_from_file_location("_game_analysis_vfx_preview_common", common_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load VFX preview common module: {common_path}")
    common = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = common
    spec.loader.exec_module(common)
    return common


_COMMON = load_skill_common()
load_vfx_config = _COMMON.load_vfx_config
vfx_paths = _COMMON.vfx_paths


def win(path: Path) -> str:
    return str(path).replace("/", "\\")


def bat_value(value: Path | str) -> str:
    text = str(value)
    if '"' in text:
        raise ValueError("BAT value contains unsupported quote")
    if "!" in text:
        raise ValueError("BAT value contains unsupported exclamation mark")
    if any(ord(char) < 32 for char in text):
        raise ValueError("BAT value contains unsupported control character")
    return text.replace("^", "^^").replace("%", "%%")


def win_value(path: Path) -> str:
    return bat_value(win(path))


def tool_relative(config: dict[str, Any]) -> Path:
    return Path(config["tool"]["directory"]) / config["tool"]["exe_name"]


def create_launcher(study_root: Path, skill_root: Path, config_path: Path | None = None) -> Path:
    config = load_vfx_config(config_path or DEFAULT_CONFIG)
    root = Path(study_root).resolve(strict=False)
    skill = Path(skill_root).resolve(strict=False)
    paths = vfx_paths(root, config)

    launcher = config["launcher"]
    env_exe = launcher["environment_exe"]
    env_skill_root = launcher["environment_skill_root"]
    relative_exe = tool_relative(config)
    fallback_exe = (skill / relative_exe).resolve(strict=False)

    bat_values = [
        root,
        skill,
        fallback_exe,
        paths.manifest,
        paths.reconstructed_dir,
        paths.preview_cache_dir,
        paths.unity_export_dir,
    ]
    for value in bat_values:
        bat_value(win(value))

    paths.report_dir.mkdir(parents=True, exist_ok=True)
    env_skill_exe = f"!{env_skill_root}!\\{bat_value(win(relative_exe))}"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal EnableExtensions EnableDelayedExpansion",
        "",
        f'set "STUDY_ROOT={win_value(root)}"',
        f'set "SKILL_ROOT={win_value(skill)}"',
        f'set "GAME_ANALYSIS_VFX_MANIFEST={win_value(paths.manifest)}"',
        f'set "GAME_ANALYSIS_VFX_RECONSTRUCTED_DIR={win_value(paths.reconstructed_dir)}"',
        f'set "GAME_ANALYSIS_VFX_PREVIEW_CACHE_DIR={win_value(paths.preview_cache_dir)}"',
        f'set "GAME_ANALYSIS_VFX_UNITY_EXPORT_DIR={win_value(paths.unity_export_dir)}"',
        'set "VIEWER_EXE="',
        f'if defined {env_exe} set "VIEWER_EXE=!{env_exe}!"',
        (
            f'if not defined VIEWER_EXE if defined {env_skill_root} '
            f'set "VIEWER_EXE={env_skill_exe}"'
        ),
        f'if not defined VIEWER_EXE set "VIEWER_EXE={win_value(fallback_exe)}"',
        "",
        'if not exist "!VIEWER_EXE!" (',
        '  echo 错误：找不到特效预览器。',
        "  echo 请检查环境变量或默认工具路径。",
        '  if not "!GAME_ANALYSIS_VFX_LAUNCHER_NO_PAUSE!"=="1" pause',
        "  exit /b 1",
        ")",
        "",
        'start "" "!VIEWER_EXE!" ^',
        '  --study-root "!STUDY_ROOT!" ^',
        f'  --manifest "{win_value(paths.manifest)}" ^',
        f'  --reconstructed-dir "{win_value(paths.reconstructed_dir)}" ^',
        f'  --preview-cache-dir "{win_value(paths.preview_cache_dir)}" ^',
        f'  --unity-export-dir "{win_value(paths.unity_export_dir)}"',
        "",
        "endlocal",
        "exit /b 0",
    ]

    payload = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    if not paths.launcher.is_file() or paths.launcher.read_bytes() != payload:
        paths.launcher.write_bytes(payload)
    return paths.launcher


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create GameVfxViewer launcher BAT.")
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--skill-root", required=True, type=Path)
    parser.add_argument("--config", default=None, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bat_path = create_launcher(args.study_root, args.skill_root, args.config)
    print(json.dumps({"launcher": str(bat_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

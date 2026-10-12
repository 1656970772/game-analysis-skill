#!/usr/bin/env python3
"""Create the single character-first DST animation launcher for one study root."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
from typing import Any


DEFAULT_CONFIG_RELATIVE = Path("references") / "game-character-animation-viewer-config.json"
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LauncherError(RuntimeError):
    """Raised when the character viewer launcher cannot be created safely."""


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_existing_directory(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError(f"{label} does not exist: {path}") from exc
    if not resolved.is_dir():
        raise LauncherError(f"{label} is not a directory: {resolved}")
    return resolved


def resolve_existing_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LauncherError(f"{label} does not exist: {path}") from exc
    if not resolved.is_file():
        raise LauncherError(f"{label} is not a file: {resolved}")
    return resolved


def relative_parts(raw: str, label: str) -> tuple[str, ...]:
    if not raw or raw != raw.strip():
        raise LauncherError(f"{label} must be a non-empty exact relative path: {raw!r}")
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise LauncherError(f"{label} contains control characters: {raw!r}")
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if windows_path.drive or windows_path.root or windows_path.is_absolute() or posix_path.is_absolute():
        raise LauncherError(f"{label} must be relative: {raw!r}")
    parts = tuple(raw.replace("\\", "/").split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LauncherError(f"{label} contains an empty, dot, or parent segment: {raw!r}")
    return parts


def required_object(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise LauncherError(f"viewer config {name} must be an object")
    return value


def required_string(payload: dict[str, Any], name: str, context: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LauncherError(f"viewer config {context}.{name} must be a non-empty exact string")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise LauncherError(f"viewer config {context}.{name} contains control characters")
    return value


def load_config(path: Path) -> dict[str, Any]:
    resolved = resolve_existing_file(path, "character viewer config")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherError(f"character viewer config is not valid UTF-8 JSON: {resolved}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LauncherError("character viewer config schema_version must be 1")
    required_object(payload, "tool")
    required_object(payload, "launch")
    return payload


def configured_path(root: Path, raw: str, label: str) -> Path:
    candidate = root.joinpath(*relative_parts(raw, label))
    resolved = candidate.resolve(strict=False)
    if not is_within(resolved, root):
        raise LauncherError(f"{label} escapes its root: {candidate} -> {resolved}")
    return resolved


def validate_dst_root(value: str | Path) -> Path:
    root = resolve_existing_directory(value, "DST game root")
    data_root = root if root.name.casefold() == "data" else root / "data"
    required = ("anim", "bigportraits", "images", "minimap", "sound")
    missing = [name for name in required if not (data_root / name).is_dir()]
    if missing:
        raise LauncherError(
            f"DST game root is missing required data directories {missing}: {root}"
        )
    return root


def load_project_game_root(path: Path, study_root: Path) -> Path:
    resolved = resolve_existing_file(path, "character viewer project config")
    if not is_within(resolved, study_root):
        raise LauncherError(f"character viewer project config is outside study-root: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherError(f"character viewer project config is invalid: {resolved}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LauncherError("character viewer project config schema_version must be 1")
    game_root = payload.get("game_root")
    if not isinstance(game_root, str) or not game_root.strip():
        raise LauncherError("character viewer project config game_root must be a non-empty string")
    return validate_dst_root(game_root)


def batch_value(value: Path | str) -> str:
    text = str(value)
    if any(character in text for character in ('"', "\x00", "\r", "\n")):
        raise LauncherError(f"value cannot be represented safely in a BAT file: {text!r}")
    return text.replace("%", "%%")


def render_launcher(
    *,
    viewer: Path,
    game_root: Path,
    support_script: Path,
    launch_log: Path,
    environment_name: str,
) -> str:
    return (
        "@echo off\r\n"
        "setlocal DisableDelayedExpansion\r\n"
        "chcp 65001 >nul\r\n"
        "title DST character animation viewer launcher\r\n"
        f'set "GA_CHARACTER_VIEWER={batch_value(viewer)}"\r\n'
        f'set "GA_CHARACTER_LAUNCHER={batch_value(support_script)}"\r\n'
        f'set "GA_LAUNCH_LOG={batch_value(launch_log)}"\r\n'
        f'set "{environment_name}={batch_value(game_root)}"\r\n'
        'if not exist "%GA_CHARACTER_VIEWER%" goto :missing_viewer\r\n'
        'if not exist "%GA_CHARACTER_LAUNCHER%" goto :missing_support\r\n'
        f'if exist "%{environment_name}%\\data\\anim\\" goto :launch\r\n'
        f'if exist "%{environment_name}%\\anim\\" goto :launch\r\n'
        "goto :invalid_game_root\r\n"
        ":launch\r\n"
        "echo Starting DST character animation viewer. First indexing may take a moment...\r\n"
        "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File \"%GA_CHARACTER_LAUNCHER%\" "
        "-ViewerPath \"%GA_CHARACTER_VIEWER%\" "
        f'-GameRoot "%{environment_name}%" '
        '-LogPath "%GA_LAUNCH_LOG%"\r\n'
        'set "GA_EXIT_CODE=%ERRORLEVEL%"\r\n'
        'if "%GA_EXIT_CODE%"=="0" exit /b 0\r\n'
        "echo.\r\n"
        "echo The character animation viewer failed to start or exited unexpectedly.\r\n"
        'echo Log: "%GA_LAUNCH_LOG%"\r\n'
        "pause\r\n"
        "exit /b %GA_EXIT_CODE%\r\n"
        ":missing_viewer\r\n"
        'echo Character animation viewer is missing: "%GA_CHARACTER_VIEWER%"\r\n'
        "pause\r\n"
        "exit /b 2\r\n"
        ":missing_support\r\n"
        'echo Character animation launch helper is missing: "%GA_CHARACTER_LAUNCHER%"\r\n'
        "pause\r\n"
        "exit /b 3\r\n"
        ":invalid_game_root\r\n"
        f'echo Invalid DST game root: "%{environment_name}%"\r\n'
        "echo Expected data\\anim and the other installed game data directories.\r\n"
        "pause\r\n"
        "exit /b 4\r\n"
    )


def write_if_changed(path: Path, content: str) -> str:
    data = content.encode("utf-8-sig")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return "reused"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise LauncherError(f"cannot publish character viewer launcher: {path}: {exc}") from exc
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
    return "created"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--game-root")
    parser.add_argument("--project-config")
    parser.add_argument("--config")
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    study_root = resolve_existing_directory(arguments.study_root, "study-root")
    skill_root = resolve_existing_directory(arguments.skill_root, "skill-root")
    config_path = Path(arguments.config).expanduser() if arguments.config else skill_root / DEFAULT_CONFIG_RELATIVE
    config = load_config(config_path)
    tool = required_object(config, "tool")
    launch = required_object(config, "launch")

    install_relative = required_string(tool, "install_relative", "tool")
    executable_name = required_string(tool, "executable_name", "tool")
    viewer_dir = configured_path(skill_root, install_relative, "tool.install_relative")
    viewer = resolve_existing_file(viewer_dir / executable_name, "character animation viewer")
    if not is_within(viewer, skill_root):
        raise LauncherError(f"character animation viewer is outside skill-root: {viewer}")

    support_script = resolve_existing_file(
        configured_path(
            skill_root,
            required_string(launch, "support_script_relative", "launch"),
            "launch.support_script_relative",
        ),
        "character viewer launch helper",
    )
    environment_name = required_string(launch, "game_root_environment", "launch")
    if not ENVIRONMENT_NAME_PATTERN.fullmatch(environment_name):
        raise LauncherError(f"launch.game_root_environment is not a safe environment name: {environment_name!r}")

    default_project_config = configured_path(
        study_root,
        required_string(launch, "project_config_relative", "launch"),
        "launch.project_config_relative",
    )
    if arguments.project_config:
        project_config = Path(arguments.project_config).expanduser()
        if not project_config.is_absolute():
            project_config = study_root / project_config
    else:
        project_config = default_project_config

    if arguments.game_root:
        game_root = validate_dst_root(arguments.game_root)
        game_root_source = "explicit"
    elif project_config.is_file():
        game_root = load_project_game_root(project_config, study_root)
        game_root_source = str(project_config.resolve(strict=True))
    else:
        raise LauncherError(
            "DST game root is required: pass --game-root or create the configured project JSON with schema_version=1 and game_root"
        )

    launcher = configured_path(
        study_root,
        required_string(launch, "launcher_relative", "launch"),
        "launch.launcher_relative",
    )
    launch_log = configured_path(
        study_root,
        required_string(launch, "log_relative", "launch"),
        "launch.log_relative",
    )
    launch_log.parent.mkdir(parents=True, exist_ok=True)
    content = render_launcher(
        viewer=viewer,
        game_root=game_root,
        support_script=support_script,
        launch_log=launch_log,
        environment_name=environment_name,
    )
    status = write_if_changed(launcher, content)
    return {
        "status": status,
        "launcher": str(launcher),
        "viewer": str(viewer),
        "game_root": str(game_root),
        "game_root_source": game_root_source,
        "launch_log": str(launch_log),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run(arguments)
    except LauncherError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

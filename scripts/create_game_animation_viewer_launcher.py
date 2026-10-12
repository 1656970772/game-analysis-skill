#!/usr/bin/env python3
"""Create the single local GameAnimationViewer launcher for one study root."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys
import tempfile
from typing import Any


MANIFEST_RELATIVE = Path("4.临时目录") / "中间索引" / "游戏动画预览" / "manifest.json"
PREVIEW_CACHE_RELATIVE = (
    Path("4.临时目录") / "中间索引" / "游戏动画预览" / "preview-cache"
)
EXPORT_RELATIVE = Path("4.临时目录") / "动画预览导出"
LAUNCH_LOG_RELATIVE = (
    Path("4.临时目录") / "中间索引" / "游戏动画预览" / "viewer-launch.log"
)
LAUNCHER_RELATIVE = Path("2.报告") / "打开动画预览.bat"
VIEWER_RELATIVE = (
    Path("built-in-tools")
    / "GameAnimationViewer_1.0.1"
    / "GameAnimationViewer.exe"
)
DEFAULT_RENDERER_RELATIVE = Path("scripts") / "export_game_animation_asset.py"
DEFAULT_SKILL_PYTHON_RELATIVE = Path("tools") / "python-env" / "Scripts" / "python.exe"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class LauncherError(RuntimeError):
    """Raised when the launcher contract cannot be satisfied safely."""


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise LauncherError(f"Cannot inspect path boundary: {path}: {exc}") from exc
    attributes = int(getattr(info, "st_file_attributes", 0))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def resolve_existing_directory(value: str, label: str) -> Path:
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


def resolve_optional_file(value: str | None, default: Path, label: str) -> Path:
    candidate = Path(value).expanduser() if value else default
    if value and not candidate.is_absolute():
        raise LauncherError(f"{label} override must be absolute: {candidate}")
    return resolve_existing_file(candidate, label)


def load_manifest(path: Path, study_root: Path) -> dict[str, Any]:
    resolved = resolve_existing_file(path, "animation manifest")
    if not is_within(resolved, study_root):
        raise LauncherError(f"animation manifest is outside study-root: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherError(f"animation manifest is not valid UTF-8 JSON: {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LauncherError("animation manifest root must be an object")
    return payload


def relative_parts(raw: str) -> tuple[str, ...]:
    if not raw or raw != raw.strip():
        raise LauncherError(f"source_roots entry must be a non-empty exact relative path: {raw!r}")
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise LauncherError(f"source_roots entry contains control characters: {raw!r}")
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if windows_path.drive or windows_path.root or windows_path.is_absolute() or posix_path.is_absolute():
        raise LauncherError(f"source_roots entry must not be absolute or drive-relative: {raw!r}")
    normalized = raw.replace("\\", "/")
    parts = tuple(normalized.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LauncherError(f"source_roots entry must not contain empty, dot, or parent segments: {raw!r}")
    return parts


def validate_reparse_target(path: Path, study_root: Path) -> None:
    if not is_reparse_point(path):
        return
    try:
        target = path.resolve(strict=True)
    except OSError as exc:
        raise LauncherError(f"broken symlink/reparse point under asset root: {path}") from exc
    if not is_within(target, study_root):
        raise LauncherError(f"symlink/reparse point escapes study-root: {path} -> {target}")


def audit_nested_reparse_points(asset_root: Path, study_root: Path) -> None:
    validate_reparse_target(asset_root, study_root)
    for current, directory_names, file_names in os.walk(asset_root, followlinks=False):
        current_path = Path(current)
        for name in [*directory_names, *file_names]:
            entry = current_path / name
            if is_reparse_point(entry):
                validate_reparse_target(entry, study_root)


def validate_source_root_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise LauncherError("animation manifest source_roots keys must be strings")
    if not raw or raw != raw.strip():
        raise LauncherError(f"source_roots key must be a non-empty exact name: {raw!r}")
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise LauncherError(f"source_roots key contains control characters: {raw!r}")
    if raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise LauncherError(f"source_roots key must be a logical name, not a path: {raw!r}")
    return raw


def resolve_source_roots(payload: dict[str, Any], study_root: Path) -> list[Path]:
    raw_roots = payload.get("source_roots")
    if not isinstance(raw_roots, dict) or not raw_roots:
        raise LauncherError(
            "animation manifest source_roots must be a non-empty object of name to relative path"
        )

    validated: list[tuple[str, str]] = []
    for raw_name, raw_value in raw_roots.items():
        name = validate_source_root_name(raw_name)
        if not isinstance(raw_value, str):
            raise LauncherError(f"source_roots[{name!r}] must be a string")
        relative_parts(raw_value)
        validated.append((name, raw_value))

    # The builder emits a semantic object (for example, xml/atlas), but the
    # viewer CLI consumes repeated --asset-root arguments. Sort by normalized
    # object value so JSON key/insertion order cannot change the launcher.
    validated.sort(key=lambda item: (item[1].replace("\\", "/"), item[0]))

    resolved_roots: list[Path] = []
    seen: set[str] = set()
    for name, raw in validated:
        candidate = study_root.joinpath(*relative_parts(raw))
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise LauncherError(f"source_roots[{name!r}] does not exist: {candidate}") from exc
        if not resolved.is_dir():
            raise LauncherError(f"source_roots[{name!r}] is not a directory: {resolved}")
        if not is_within(resolved, study_root):
            raise LauncherError(
                f"source_roots[{name!r}] escapes study-root: {candidate} -> {resolved}"
            )
        audit_nested_reparse_points(candidate, study_root)
        identity = os.path.normcase(str(resolved))
        if identity in seen:
            raise LauncherError(
                f"source_roots contains a duplicate resolved root at {name!r}: {raw!r}"
            )
        seen.add(identity)
        resolved_roots.append(resolved)
    return resolved_roots


def safe_output_path(study_root: Path, relative: Path, label: str) -> Path:
    candidate = study_root / relative
    resolved = candidate.resolve(strict=False)
    if not is_within(resolved, study_root):
        raise LauncherError(f"{label} escapes study-root: {candidate} -> {resolved}")
    return resolved


def batch_quote(value: Path | str) -> str:
    text = str(value)
    if any(character in text for character in ('"', "\x00", "\r", "\n")):
        raise LauncherError(f"path cannot be represented safely in a BAT argument: {text!r}")
    return f'"{text.replace("%", "%%")}"'


def batch_value(value: Path | str) -> str:
    text = str(value)
    if any(character in text for character in ('"', "\x00", "\r", "\n")):
        raise LauncherError(f"path cannot be represented safely in a BAT value: {text!r}")
    return text.replace("%", "%%")


def render_launcher(
    viewer: Path,
    study_root: Path,
    manifest: Path,
    source_roots: list[Path],
    preview_cache: Path,
    export_dir: Path,
    python_exe: Path,
    renderer_script: Path,
) -> str:
    launch_log = safe_output_path(study_root, LAUNCH_LOG_RELATIVE, "viewer launch log")
    arguments: list[tuple[str, Path]] = [
        ("--study-root", study_root),
        ("--manifest", manifest),
    ]
    arguments.extend(("--asset-root", root) for root in source_roots)
    arguments.extend(
        [
            ("--preview-cache-dir", preview_cache),
            ("--export-dir", export_dir),
            ("--python-exe", python_exe),
            ("--renderer-script", renderer_script),
        ]
    )

    command_lines = [batch_quote(viewer)]
    command_lines.extend(f"  {flag} {batch_quote(value)}" for flag, value in arguments)
    joined = " ^\r\n".join(command_lines)
    return (
        "@echo off\r\n"
        "setlocal DisableDelayedExpansion\r\n"
        "title GameAnimationViewer launcher\r\n"
        f'set "GA_VIEWER={batch_value(viewer)}"\r\n'
        f'set "GA_MANIFEST={batch_value(manifest)}"\r\n'
        f'set "GA_LAUNCH_LOG={batch_value(launch_log)}"\r\n'
        'if not exist "%GA_VIEWER%" goto :missing_viewer\r\n'
        'if not exist "%GA_MANIFEST%" goto :missing_manifest\r\n'
        '>"%GA_LAUNCH_LOG%" echo [%DATE% %TIME%] Starting GameAnimationViewer...\r\n'
        "echo Starting GameAnimationViewer. Please wait...\r\n"
        f'{joined} >>"%GA_LAUNCH_LOG%" 2>&1\r\n'
        'set "GA_EXIT_CODE=%ERRORLEVEL%"\r\n'
        'if "%GA_EXIT_CODE%"=="0" exit /b 0\r\n'
        '>>"%GA_LAUNCH_LOG%" echo [%DATE% %TIME%] Viewer exited with code %GA_EXIT_CODE%.\r\n'
        "echo.\r\n"
        "echo GameAnimationViewer failed to start or exited unexpectedly.\r\n"
        'echo Log: "%GA_LAUNCH_LOG%"\r\n'
        "pause\r\n"
        "exit /b %GA_EXIT_CODE%\r\n"
        ":missing_viewer\r\n"
        'echo GameAnimationViewer is missing: "%GA_VIEWER%"\r\n'
        "pause\r\n"
        "exit /b 2\r\n"
        ":missing_manifest\r\n"
        'echo Animation manifest is missing: "%GA_MANIFEST%"\r\n'
        "pause\r\n"
        "exit /b 3\r\n"
    )


def write_if_changed(path: Path, content: str) -> str:
    data = content.encode("utf-8-sig")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_bytes() == data:
                return "reused"
        except OSError as exc:
            raise LauncherError(f"cannot read existing launcher: {path}: {exc}") from exc

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise LauncherError(f"cannot publish launcher: {path}: {exc}") from exc
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
    parser.add_argument("--manifest")
    parser.add_argument("--python-exe")
    parser.add_argument(
        "--renderer-script",
        "--exporter-script",
        dest="renderer_script",
        help="Animation renderer script; --exporter-script is a compatibility alias",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    study_root = resolve_existing_directory(arguments.study_root, "study-root")
    skill_root = resolve_existing_directory(arguments.skill_root, "skill-root")

    manifest_candidate = (
        Path(arguments.manifest).expanduser()
        if arguments.manifest
        else study_root / MANIFEST_RELATIVE
    )
    if arguments.manifest and not manifest_candidate.is_absolute():
        manifest_candidate = study_root / manifest_candidate
    payload = load_manifest(manifest_candidate, study_root)
    manifest = manifest_candidate.resolve(strict=True)
    source_roots = resolve_source_roots(payload, study_root)

    viewer = resolve_existing_file(skill_root / VIEWER_RELATIVE, "GameAnimationViewer")
    bundled_python = skill_root / DEFAULT_SKILL_PYTHON_RELATIVE
    default_python = bundled_python if bundled_python.is_file() else Path(sys.executable)
    python_exe = resolve_optional_file(arguments.python_exe, default_python, "Python executable")
    renderer_script = resolve_optional_file(
        arguments.renderer_script,
        skill_root / DEFAULT_RENDERER_RELATIVE,
        "animation renderer script",
    )

    preview_cache = safe_output_path(study_root, PREVIEW_CACHE_RELATIVE, "preview cache")
    export_dir = safe_output_path(study_root, EXPORT_RELATIVE, "animation export directory")
    launcher = safe_output_path(study_root, LAUNCHER_RELATIVE, "animation launcher")
    report_directory = launcher.parent
    report_directory.mkdir(parents=True, exist_ok=True)
    report_directory = report_directory.resolve(strict=True)
    if not is_within(report_directory, study_root):
        raise LauncherError(f"report directory escapes study-root: {report_directory}")

    content = render_launcher(
        viewer,
        study_root,
        manifest,
        source_roots,
        preview_cache,
        export_dir,
        python_exe,
        renderer_script,
    )
    status = write_if_changed(launcher, content)
    return {
        "status": status,
        "launcher": str(launcher),
        "manifest": str(manifest),
        "source_roots": [str(root) for root in source_roots],
        "viewer": str(viewer),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run(arguments)
    except LauncherError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

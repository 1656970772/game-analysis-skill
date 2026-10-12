from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import image_gallery_common as gallery_common
except ModuleNotFoundError:
    from scripts import image_gallery_common as gallery_common


DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]


def image_roots_from_manifest(manifest_path: Path) -> list[Path]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"image gallery manifest not found: {manifest_path}")

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"image gallery manifest must contain valid JSON: {manifest_path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"image gallery manifest must be a JSON object: {manifest_path}")

    source_roots = data.get("source_roots")
    if not isinstance(source_roots, list) or not source_roots:
        raise ValueError(
            f"image gallery manifest field source_roots must be a non-empty list: {manifest_path}"
        )

    image_roots: list[Path] = []
    for index, source_root in enumerate(source_roots):
        if not isinstance(source_root, dict):
            raise ValueError(f"image gallery manifest field source_roots[{index}] must be an object")
        path_value = source_root.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValueError(
                f"image gallery manifest field source_roots[{index}].path must be a non-empty string"
            )
        image_roots.append(Path(path_value))
    return image_roots


def bat_value(value: Path | str) -> str:
    text = str(value)
    if '"' in text or any(ord(char) < 32 for char in text):
        raise ValueError(f"BAT value contains unsupported characters: {text!r}")
    return text.replace("%", "%%")


def bat_text(
    *,
    study_root: Path,
    skill_root: Path,
    gallery_exe: Path,
    manifest_path: Path,
    image_roots: list[Path],
) -> str:
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal",
        f'set "STUDY_ROOT={bat_value(study_root)}"',
        f'set "SKILL_ROOT={bat_value(skill_root)}"',
        f'set "GALLERY_EXE={bat_value(gallery_exe)}"',
        f'set "MANIFEST_PATH={bat_value(manifest_path)}"',
    ]
    for index, image_root in enumerate(image_roots, start=1):
        lines.append(f'set "IMAGE_ROOT_{index}={bat_value(image_root)}"')

    image_root_args = " ".join(
        f'--images-root "%IMAGE_ROOT_{index}%"' for index in range(1, len(image_roots) + 1)
    )
    command_parts = [
        'start "" "%GALLERY_EXE%"',
        '--study-root "%STUDY_ROOT%"',
        '--manifest "%MANIFEST_PATH%"',
    ]
    if image_root_args:
        command_parts.append(image_root_args)

    lines.extend(
        [
            'if not exist "%GALLERY_EXE%" (',
            '  echo ERROR: GameImageGallery.exe not found: "%GALLERY_EXE%"',
            "  pause",
            "  exit /b 1",
            ")",
            'if not exist "%MANIFEST_PATH%" (',
            '  echo ERROR: image gallery manifest not found: "%MANIFEST_PATH%"',
            "  pause",
            "  exit /b 1",
            ")",
            " ".join(command_parts),
        ]
    )
    return "\r\n".join(lines) + "\r\n"


def create_launcher(study_root: Path, skill_root: Path) -> Path:
    study_root = Path(study_root).resolve()
    skill_root = Path(skill_root).resolve()
    config: dict[str, Any] = gallery_common.load_config(skill_root)
    paths = gallery_common.gallery_paths(study_root, config)
    manifest_path = paths.manifest.resolve()
    image_roots = image_roots_from_manifest(manifest_path)
    gallery_exe = (skill_root / gallery_common.as_path(config["tool"]["exe_relative_path"])).resolve()

    paths.report_dir.mkdir(parents=True, exist_ok=True)
    paths.launcher.write_text(
        bat_text(
            study_root=study_root,
            skill_root=skill_root,
            gallery_exe=gallery_exe,
            manifest_path=manifest_path,
            image_roots=image_roots,
        ),
        encoding="utf-8",
    )
    return paths.launcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the GameAnalysis image gallery BAT launcher.")
    parser.add_argument(
        "--study-root",
        required=True,
        type=Path,
        help="GameAnalysis study project root.",
    )
    parser.add_argument(
        "--skill-root",
        required=True,
        type=Path,
        help="Skill root containing references/image-gallery-viewer-config.json.",
    )
    args = parser.parse_args(argv)

    launcher = create_launcher(args.study_root, args.skill_root)
    # stdout is a machine interface. ASCII-escaped JSON stays valid UTF-8 even
    # when Windows starts Python with a legacy pipe encoding such as cp936.
    print(json.dumps({"launcher": str(launcher)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

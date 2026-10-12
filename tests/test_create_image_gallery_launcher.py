from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.create_image_gallery_launcher import (  # noqa: E402
    bat_value,
    bat_text,
    create_launcher,
    image_roots_from_manifest,
)
from scripts.image_gallery_common import gallery_paths, load_config  # noqa: E402


def write_manifest(study_root: Path, source_roots: list[Path] | None = None) -> Path:
    paths = gallery_paths(study_root, load_config(ROOT))
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "source_roots": [
            {
                "label": f"图片根{index}",
                "path": str(source_root),
                "item_count": 0,
            }
            for index, source_root in enumerate(source_roots or [], start=1)
        ],
        "items": [],
    }
    paths.manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return paths.manifest


def test_create_launcher_writes_quoted_bat_for_single_source_root(tmp_path: Path) -> None:
    study_root = tmp_path / "测试 项目"
    image_root = study_root / "1.分类资源" / "图片"
    manifest_path = write_manifest(study_root, [image_root])

    launcher = create_launcher(study_root, ROOT)

    config = load_config(ROOT)
    paths = gallery_paths(study_root, config)
    text = launcher.read_text(encoding="utf-8")
    assert launcher == paths.launcher
    assert 'chcp 65001 >nul' in text
    assert f'set "STUDY_ROOT={study_root.resolve()}"' in text
    assert f'set "SKILL_ROOT={ROOT.resolve()}"' in text
    assert f'set "GALLERY_EXE={(ROOT / "built-in-tools" / "GameImageGallery_1.0.0" / "GameImageGallery.exe").resolve()}"' in text
    assert f'set "MANIFEST_PATH={manifest_path.resolve()}"' in text
    assert f'set "IMAGE_ROOT_1={image_root}"' in text
    assert 'echo ERROR: GameImageGallery.exe not found: "%GALLERY_EXE%"' in text
    assert 'echo ERROR: image gallery manifest not found: "%MANIFEST_PATH%"' in text
    assert text.count("pause") == 2
    assert 'start "" "%GALLERY_EXE%" --study-root "%STUDY_ROOT%" --manifest "%MANIFEST_PATH%" --images-root "%IMAGE_ROOT_1%"' in text


def test_multiple_source_roots_generate_repeated_images_root_args(tmp_path: Path) -> None:
    image_roots = [
        tmp_path / "1.分类资源" / "图片",
        tmp_path / "4.临时目录" / "_ascii_work" / "额外 图片",
    ]
    write_manifest(tmp_path, image_roots)

    launcher = create_launcher(tmp_path, ROOT)
    text = launcher.read_text(encoding="utf-8")

    assert f'set "IMAGE_ROOT_1={image_roots[0]}"' in text
    assert f'set "IMAGE_ROOT_2={image_roots[1]}"' in text
    assert text.count("--images-root") == 2
    assert '--images-root "%IMAGE_ROOT_1%" --images-root "%IMAGE_ROOT_2%"' in text


def test_create_launcher_does_not_copy_manifest_into_report_dir(tmp_path: Path) -> None:
    write_manifest(tmp_path, [tmp_path / "1.分类资源" / "图片"])

    create_launcher(tmp_path, ROOT)

    assert not (tmp_path / "2.报告" / "manifest.json").exists()
    assert (tmp_path / "4.临时目录" / "中间索引" / "图片画廊" / "manifest.json").is_file()


@pytest.mark.parametrize(
    "manifest_data",
    [
        {},
        {"source_roots": []},
    ],
)
def test_image_roots_from_manifest_rejects_missing_or_empty_source_roots(
    tmp_path: Path,
    manifest_data: dict[str, object],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="source_roots"):
        image_roots_from_manifest(manifest_path)


@pytest.mark.parametrize(
    "source_root",
    [
        {},
        {"path": ""},
        {"path": 123},
    ],
)
def test_image_roots_from_manifest_rejects_invalid_source_root_path(
    tmp_path: Path,
    source_root: dict[str, object],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"source_roots": [source_root]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_roots\\[0\\]\\.path"):
        image_roots_from_manifest(manifest_path)


def test_image_roots_from_manifest_rejects_missing_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError, match="image gallery manifest not found"):
        image_roots_from_manifest(manifest_path)


def test_image_roots_from_manifest_accepts_utf8_bom(tmp_path: Path) -> None:
    image_root = tmp_path / "1.分类资源" / "图片"
    manifest_path = tmp_path / "manifest.json"
    payload = {"source_roots": [{"path": str(image_root)}]}
    manifest_path.write_text("\ufeff" + json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert image_roots_from_manifest(manifest_path) == [image_root]


def test_bat_text_escapes_percent_in_paths() -> None:
    text = bat_text(
        study_root=Path(r"C:\GA 100% 项目"),
        skill_root=Path(r"C:\Skill Root"),
        gallery_exe=Path(r"C:\Skill Root\built-in-tools\GameImageGallery_1.0.0\GameImageGallery.exe"),
        manifest_path=Path(r"C:\GA 100% 项目\4.临时目录\中间索引\图片画廊\manifest.json"),
        image_roots=[Path(r"C:\GA 100% 项目\1.分类资源\图片")],
    )

    assert 'set "STUDY_ROOT=C:\\GA 100%% 项目"' in text
    assert 'set "IMAGE_ROOT_1=C:\\GA 100%% 项目\\1.分类资源\\图片"' in text


@pytest.mark.parametrize("unsafe_value", ['bad"value', "bad\nvalue", "bad\rvalue", "bad\x1fvalue"])
def test_bat_value_rejects_quotes_and_control_characters(unsafe_value: str) -> None:
    with pytest.raises(ValueError, match="BAT value contains unsupported characters"):
        bat_value(unsafe_value)


def test_cli_generates_launcher_and_outputs_json(tmp_path: Path) -> None:
    write_manifest(tmp_path, [tmp_path / "1.分类资源" / "图片"])

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "create_image_gallery_launcher.py"),
            "--study-root",
            str(tmp_path),
            "--skill-root",
            str(ROOT),
        ],
        cwd=ROOT,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    launcher = Path(payload["launcher"])
    assert launcher == gallery_paths(tmp_path, load_config(ROOT)).launcher
    assert launcher.is_file()


def test_cli_stdout_bytes_are_strict_utf8_without_environment_override(tmp_path: Path) -> None:
    write_manifest(tmp_path, [tmp_path / "1.分类资源" / "图片"])

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "create_image_gallery_launcher.py"),
            "--study-root",
            str(tmp_path),
            "--skill-root",
            str(ROOT),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
    launcher = Path(payload["launcher"])
    assert launcher == gallery_paths(tmp_path, load_config(ROOT)).launcher
    assert launcher.is_file()

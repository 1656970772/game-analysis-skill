from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.image_gallery_common as image_gallery_common
from scripts.image_gallery_common import (
    as_path,
    ensure_inside,
    gallery_paths,
    load_config,
    resolve_image_roots,
    stable_id,
    write_json,
)


def test_config_declares_fixed_tool_and_launcher_names() -> None:
    config = load_config(ROOT)

    assert config["tool"]["name"] == "GameImageGallery"
    assert config["tool"]["version"] == "1.0.0"
    assert config["tool"]["exe_relative_path"] == "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe"
    assert config["launcher"]["bat_name"] == "打开图片画廊.bat"
    assert ".png" in config["image_extensions"]
    assert config["thumbnail"]["format"] == "PNG"
    assert config["ui"]["default_page_size"] == 480
    assert 480 in config["ui"]["page_sizes"]


def write_temp_config(tmp_path: Path, config: dict[str, object]) -> Path:
    config_path = tmp_path / "references" / "image-gallery-viewer-config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_as_path_splits_forward_and_backslash_config_paths_on_any_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_gallery_common, "Path", PurePosixPath)

    assert as_path("4.临时目录/中间索引\\图片画廊").parts == ("4.临时目录", "中间索引", "图片画廊")


@pytest.mark.parametrize(
    "value",
    [
        "/absolute/path",
        "C:/absolute/path",
        "../outside",
        "safe/../outside",
    ],
)
def test_as_path_rejects_absolute_or_traversing_config_paths(value: str) -> None:
    with pytest.raises(ValueError, match="config-relative path"):
        as_path(value)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config["tool"].pop("name"), "tool.name"),
        (lambda config: config["launcher"].__setitem__("bat_name", 123), "launcher.bat_name"),
        (lambda config: config["paths"].pop("index_dir"), "paths.index_dir"),
        (lambda config: config.__setitem__("default_image_roots", [{"label": "分类图片"}]), "default_image_roots[0].relative_path"),
        (lambda config: config["thumbnail"].__setitem__("max_size", "360"), "thumbnail.max_size"),
        (lambda config: config["manifest"].__setitem__("status_values", ["ready", 404]), "manifest.status_values[1]"),
        (lambda config: config["ui"].__setitem__("page_sizes", [120, "240"]), "ui.page_sizes[1]"),
    ],
)
def test_load_config_validates_nested_required_fields_and_types(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    config = copy.deepcopy(load_config(ROOT))
    mutate(config)
    temp_root = write_temp_config(tmp_path, config)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_config(temp_root)


@pytest.mark.parametrize(
    "bat_name",
    [
        "",
        "../escape.bat",
        "nested/escape.bat",
        r"nested\escape.bat",
        r"C:\escape.bat",
        "escape..bat",
    ],
)
def test_load_config_rejects_non_filename_launcher_bat_names(tmp_path: Path, bat_name: str) -> None:
    config = copy.deepcopy(load_config(ROOT))
    config["launcher"]["bat_name"] = bat_name
    temp_root = write_temp_config(tmp_path, config)

    with pytest.raises(ValueError, match=re.escape("launcher.bat_name")):
        load_config(temp_root)


def test_gallery_paths_are_inside_temp_index_and_report_dirs(tmp_path: Path) -> None:
    paths = gallery_paths(tmp_path, load_config(ROOT))

    assert paths.report_dir == tmp_path / "2.报告"
    assert paths.index_dir == tmp_path / "4.临时目录" / "中间索引" / "图片画廊"
    assert paths.manifest == paths.index_dir / "manifest.json"
    assert paths.errors_jsonl == paths.index_dir / "errors.jsonl"
    assert paths.thumbnails_dir == paths.index_dir / "thumbnails"
    assert paths.launcher == tmp_path / "2.报告" / "打开图片画廊.bat"


def test_gallery_paths_uses_separator_neutral_nested_path_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    config = copy.deepcopy(load_config(ROOT))
    config["paths"]["manifest_name"] = "nested\\manifest.json"
    config["paths"]["errors_name"] = "nested/errors.jsonl"
    config["paths"]["thumbnails_dir"] = "thumbs\\nested"
    monkeypatch.setattr(image_gallery_common, "Path", PurePosixPath)

    paths = gallery_paths(PurePosixPath("/study"), config)

    assert paths.manifest == PurePosixPath("/study/4.临时目录/中间索引/图片画廊/nested/manifest.json")
    assert paths.errors_jsonl == PurePosixPath("/study/4.临时目录/中间索引/图片画廊/nested/errors.jsonl")
    assert paths.thumbnails_dir == PurePosixPath("/study/4.临时目录/中间索引/图片画廊/thumbs/nested")


def test_stable_id_uses_relative_path_and_size() -> None:
    first = stable_id("分类图片", "UI/icon.png", 128)
    second = stable_id("分类图片", "UI/icon.png", 256)

    assert first != second
    assert len(first) == 16
    assert all(ch in "0123456789abcdef" for ch in first)


def test_stable_id_normalizes_relative_path_separators() -> None:
    assert stable_id("分类图片", "UI/icon.png", 128) == stable_id("分类图片", "UI\\icon.png", 128)


def test_resolve_image_roots_uses_existing_configured_candidates(tmp_path: Path) -> None:
    (tmp_path / "1.分类资源" / "图片" / "UI").mkdir(parents=True)
    config = load_config(ROOT)

    roots = resolve_image_roots(tmp_path, config, [])

    assert roots == [("分类图片", tmp_path / "1.分类资源" / "图片")]


def test_resolve_image_roots_allows_extra_roots(tmp_path: Path) -> None:
    extra = tmp_path / "4.临时目录" / "_ascii_work" / "GameMaker_UTMT_Exports"
    extra.mkdir(parents=True)
    config = load_config(ROOT)

    roots = resolve_image_roots(tmp_path, config, [extra])

    assert ("自定义图片根1", extra) in roots


def test_resolve_image_roots_rejects_missing_explicit_extra_roots(tmp_path: Path) -> None:
    missing = tmp_path / "missing-extra-root"
    config = load_config(ROOT)

    with pytest.raises(ValueError, match=re.escape(str(missing))):
        resolve_image_roots(tmp_path, config, [missing])


def test_ensure_inside_accepts_descendants_and_rejects_siblings(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "nested" / "image.png"
    sibling = tmp_path / "outside.png"

    assert ensure_inside(child, root) is True
    assert ensure_inside(sibling, root) is False


def test_write_json_creates_parent_dirs_and_preserves_chinese_text(tmp_path: Path) -> None:
    output = tmp_path / "4.临时目录" / "中间索引" / "图片画廊" / "manifest.json"

    write_json(output, {"title": "图片画廊", "items": []})

    assert json.loads(output.read_text(encoding="utf-8")) == {"title": "图片画廊", "items": []}

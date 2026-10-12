from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.build_image_gallery_manifest as manifest_builder
from scripts.image_gallery_common import gallery_paths, load_config


def write_image(path: Path, size: tuple[int, int] = (24, 16), mode: str = "RGBA") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (60, 140, 220, 180) if mode == "RGBA" else (60, 140, 220)
    Image.new(mode, size, color).save(path)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    content = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in content if line.strip()]


def test_safe_relative_path_falls_back_to_file_name_for_paths_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inside = root / "nested" / "icon.png"
    outside = tmp_path / "outside" / "escaped.png"

    assert manifest_builder.safe_relative_path(inside, root) == "nested/icon.png"
    assert manifest_builder.safe_relative_path(outside, root) == "escaped.png"


def test_safe_resolved_path_falls_back_to_string_when_resolve_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "bad-resolve.png"
    original_resolve = type(target).resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if str(self) == str(target):
            raise RuntimeError("simulated resolve failure")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "resolve", fake_resolve)

    assert manifest_builder.safe_resolved_path(target) == str(target)


def test_build_manifest_scans_default_chinese_image_and_writes_png_thumbnail(tmp_path: Path) -> None:
    image_path = tmp_path / "1.分类资源" / "图片" / "角色" / "UI" / "角色头像_测试.png"
    write_image(image_path, size=(32, 20))
    config = load_config(ROOT)
    paths = gallery_paths(tmp_path, config)

    manifest = manifest_builder.build_manifest(tmp_path, ROOT, [], "测试项目 图片画廊")

    assert paths.manifest.is_file()
    assert paths.errors_jsonl.is_file()
    assert read_jsonl(paths.errors_jsonl) == []
    saved_manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    assert saved_manifest == manifest
    assert manifest["schema_version"] == config["manifest"]["schema_version"]
    assert manifest["tool"] == config["tool"]
    assert manifest["study_root"] == str(tmp_path.resolve())
    assert manifest["title"] == "测试项目 图片画廊"
    assert len(manifest["items"]) == 1
    assert manifest["summary"]["item_count"] == 1
    assert manifest["summary"]["failed_count"] == 0
    assert manifest["summary"]["thumbnail_count_created"] == 1
    assert manifest["ui"]["default_page_size"] == 480

    item = manifest["items"][0]
    expected_fields = {
        "name",
        "source_path",
        "width",
        "height",
        "mode",
        "has_alpha",
        "size_bytes",
        "modified_time",
        "thumbnail_path",
        "status",
        "relative_path",
        "category",
        "source_label",
    }
    assert expected_fields <= set(item)
    assert item["source_label"] == "分类图片"
    assert item["name"] == "角色头像_测试.png"
    assert item["source_path"] == str(image_path.resolve())
    assert item["relative_path"] == "角色/UI/角色头像_测试.png"
    assert item["category"] == "角色"
    assert item["extension"] == ".png"
    assert item["width"] == 32
    assert item["height"] == 20
    assert item["mode"] == "RGBA"
    assert item["has_alpha"] is True
    assert item["size_bytes"] == image_path.stat().st_size
    assert isinstance(item["modified_time"], str)
    assert item["status"] == "ready"
    assert item["thumbnail_path"].endswith(".png")

    thumbnail_path = Path(item["thumbnail_path"])
    assert thumbnail_path.is_absolute()
    assert thumbnail_path.is_file()
    with Image.open(thumbnail_path) as thumbnail:
        assert thumbnail.format == "PNG"
        assert thumbnail.width <= config["thumbnail"]["max_size"]
        assert thumbnail.height <= config["thumbnail"]["max_size"]


def test_build_manifest_records_broken_image_error_and_keeps_good_image(tmp_path: Path) -> None:
    image_root = tmp_path / "1.分类资源" / "图片"
    write_image(image_root / "good.png")
    broken = image_root / "坏图.png"
    broken.write_bytes(b"not an image")
    paths = gallery_paths(tmp_path, load_config(ROOT))

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT)

    assert [item["file_name"] for item in manifest["items"]] == ["good.png"]
    errors = read_jsonl(paths.errors_jsonl)
    assert len(errors) == 1
    assert errors[0]["source_label"] == "分类图片"
    assert errors[0]["file_name"] == "坏图.png"
    assert errors[0]["relative_path"] == "坏图.png"
    assert "error" in errors[0]
    assert manifest["summary"]["total_items"] == 1
    assert manifest["summary"]["error_count"] == 1
    assert manifest["summary"]["item_count"] == 1
    assert manifest["summary"]["failed_count"] == 1
    assert manifest["summary"]["thumbnail_count_created"] == 1
    assert manifest["items"][0]["category"] == "未分类"


def test_build_manifest_records_error_when_relative_diagnostic_cannot_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_root = tmp_path / "1.分类资源" / "图片"
    good = image_root / "good.png"
    escaped = tmp_path / "escaped.png"
    write_image(good)
    write_image(escaped)
    paths = gallery_paths(tmp_path, load_config(ROOT))
    original_image_item = manifest_builder.image_item

    def fake_iter_image_files(root: Path, extensions: object) -> list[Path]:
        assert root == image_root
        return [good, escaped]

    def fake_image_item(
        image_path: Path,
        source_root: Path,
        source_label: str,
        study_root: Path,
        thumbnails_dir: Path,
        config: dict[str, object],
    ) -> dict[str, object]:
        if image_path == escaped:
            raise RuntimeError("simulated escaped path")
        return original_image_item(
            image_path,
            source_root,
            source_label,
            study_root,
            thumbnails_dir,
            config,
        )

    monkeypatch.setattr(manifest_builder, "iter_image_files", fake_iter_image_files)
    monkeypatch.setattr(manifest_builder, "image_item", fake_image_item)

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT)

    assert [item["file_name"] for item in manifest["items"]] == ["good.png"]
    errors = read_jsonl(paths.errors_jsonl)
    assert len(errors) == 1
    assert errors[0]["file_name"] == "escaped.png"
    assert errors[0]["relative_path"] == "escaped.png"
    assert "RuntimeError: simulated escaped path" == errors[0]["error"]
    assert manifest["summary"]["item_count"] == 1
    assert manifest["summary"]["failed_count"] == 1


def test_build_manifest_error_record_uses_fail_soft_resolved_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_root = tmp_path / "1.分类资源" / "图片"
    good = image_root / "good.png"
    bad_resolve = image_root / "bad-resolve.png"
    write_image(good)
    write_image(bad_resolve)
    paths = gallery_paths(tmp_path, load_config(ROOT))
    original_resolve = type(bad_resolve).resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if str(self) == str(bad_resolve):
            raise RuntimeError("simulated resolve failure")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(type(bad_resolve), "resolve", fake_resolve)

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT)

    assert [item["file_name"] for item in manifest["items"]] == ["good.png"]
    errors = read_jsonl(paths.errors_jsonl)
    assert len(errors) == 1
    assert errors[0]["source_path"] == str(image_root.resolve())
    assert errors[0]["path"] == str(bad_resolve)
    assert errors[0]["relative_path"] == "bad-resolve.png"
    assert errors[0]["file_name"] == "bad-resolve.png"
    assert errors[0]["error"] == "RuntimeError: simulated resolve failure"
    assert manifest["summary"]["item_count"] == 1
    assert manifest["summary"]["failed_count"] == 1


def test_build_manifest_records_traversal_error_and_keeps_later_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_root = tmp_path / "1.分类资源" / "图片"
    default_root.mkdir(parents=True)
    extra_root = tmp_path / "4.临时目录" / "_ascii_work" / "extra_images"
    extra_image = extra_root / "sprites" / "player.png"
    write_image(extra_image)
    paths = gallery_paths(tmp_path, load_config(ROOT))
    original_iter_image_files = manifest_builder.iter_image_files

    def fake_iter_image_files(root: Path, extensions: object) -> list[Path]:
        if root == default_root:
            raise OSError("simulated traversal failure")
        return original_iter_image_files(root, extensions)

    monkeypatch.setattr(manifest_builder, "iter_image_files", fake_iter_image_files)

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT, extra_roots=[extra_root])

    assert [item["file_name"] for item in manifest["items"]] == ["player.png"]
    assert manifest["source_roots"] == [
        {
            "label": "分类图片",
            "path": str(default_root.resolve()),
            "item_count": 0,
        },
        {
            "label": "自定义图片根1",
            "path": str(extra_root.resolve()),
            "item_count": 1,
        },
    ]
    errors = read_jsonl(paths.errors_jsonl)
    assert len(errors) == 1
    assert errors[0]["source_label"] == "分类图片"
    assert errors[0]["source_path"] == str(default_root.resolve())
    assert errors[0]["path"] == str(default_root.resolve())
    assert errors[0]["relative_path"] == ""
    assert errors[0]["file_name"] == ""
    assert errors[0]["error"] == "OSError: simulated traversal failure"
    assert manifest["summary"]["item_count"] == 1
    assert manifest["summary"]["failed_count"] == 1


def test_build_manifest_accepts_extra_image_root_with_custom_label(tmp_path: Path) -> None:
    extra = tmp_path / "4.临时目录" / "_ascii_work" / "GameMaker_UTMT_Exports"
    write_image(extra / "Sprites" / "spr_player.png")

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT, extra_roots=[extra])

    assert manifest["source_roots"] == [
        {
            "label": "自定义图片根1",
            "path": str(extra.resolve()),
            "item_count": 1,
        }
    ]
    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["source_label"] == "自定义图片根1"
    assert manifest["items"][0]["relative_path"] == "Sprites/spr_player.png"


def test_build_manifest_embeds_ui_config_from_gallery_config(tmp_path: Path) -> None:
    write_image(tmp_path / "1.分类资源" / "图片" / "icon.png")
    config = load_config(ROOT)

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT)

    assert manifest["ui"] == config["ui"]


def test_build_manifest_defaults_title_from_study_root_name(tmp_path: Path) -> None:
    write_image(tmp_path / "1.分类资源" / "图片" / "icon.png")

    manifest = manifest_builder.build_manifest(tmp_path, skill_root=ROOT)

    assert manifest["title"] == f"{tmp_path.name} 图片画廊"


def test_cli_accepts_plan_options_and_writes_manifest(tmp_path: Path) -> None:
    default_image = tmp_path / "1.分类资源" / "图片" / "UI" / "icon.png"
    extra_root = tmp_path / "额外图片"
    extra_image = extra_root / "sprites" / "player.png"
    write_image(default_image, mode="RGB")
    write_image(extra_image, size=(18, 18), mode="RGBA")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_image_gallery_manifest.py"),
            "--study-root",
            str(tmp_path),
            "--image-root",
            str(extra_root),
            "--title",
            "CLI 测试图片画廊",
        ],
        cwd=ROOT,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest_path = gallery_paths(tmp_path, load_config(ROOT)).manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["title"] == "CLI 测试图片画廊"
    assert manifest["source_roots"] == [
        {
            "label": "分类图片",
            "path": str((tmp_path / "1.分类资源" / "图片").resolve()),
            "item_count": 1,
        },
        {
            "label": "自定义图片根1",
            "path": str(extra_root.resolve()),
            "item_count": 1,
        },
    ]
    by_source = {item["source_path"]: item for item in manifest["items"]}
    assert set(by_source) == {str(default_image.resolve()), str(extra_image.resolve())}
    assert by_source[str(default_image.resolve())]["category"] == "UI"
    assert by_source[str(extra_image.resolve())]["source_label"] == "自定义图片根1"
    assert Path(by_source[str(extra_image.resolve())]["thumbnail_path"]).is_absolute()
    assert Path(by_source[str(extra_image.resolve())]["thumbnail_path"]).is_file()
    assert manifest["summary"]["item_count"] == 2
    assert manifest["summary"]["failed_count"] == 0
    assert manifest["summary"]["thumbnail_count_created"] == 2


def test_cli_accepts_extra_root_alias_multiple_times(tmp_path: Path) -> None:
    extra_root_one = tmp_path / "额外图片1"
    extra_root_two = tmp_path / "额外图片2"
    first_image = extra_root_one / "sprites" / "player.png"
    second_image = extra_root_two / "tiles" / "floor.png"
    write_image(first_image, mode="RGB")
    write_image(second_image, size=(18, 18), mode="RGBA")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_image_gallery_manifest.py"),
            "--study-root",
            str(tmp_path),
            "--extra-root",
            str(extra_root_one),
            "--extra-root",
            str(extra_root_two),
        ],
        cwd=ROOT,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest_path = gallery_paths(tmp_path, load_config(ROOT)).manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_roots"] == [
        {
            "label": "自定义图片根1",
            "path": str(extra_root_one.resolve()),
            "item_count": 1,
        },
        {
            "label": "自定义图片根2",
            "path": str(extra_root_two.resolve()),
            "item_count": 1,
        },
    ]
    by_source = {item["source_path"]: item for item in manifest["items"]}
    assert set(by_source) == {str(first_image.resolve()), str(second_image.resolve())}
    assert by_source[str(first_image.resolve())]["source_label"] == "自定义图片根1"
    assert by_source[str(second_image.resolve())]["source_label"] == "自定义图片根2"
    assert manifest["summary"]["item_count"] == 2
    assert manifest["summary"]["failed_count"] == 0

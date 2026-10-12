from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_game_animation_manifest.py"
EXPORT_SCRIPT = ROOT / "scripts" / "export_game_animation_asset.py"
CONFIG = ROOT / "references" / "game-animation-viewer-config.json"
FIXTURE = ROOT / "tests" / "fixtures" / "game_animation"
MANIFEST_REL = Path("4.临时目录/中间索引/游戏动画预览/manifest.json")


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_built_study(tmp_path: Path):
    study_root = tmp_path / "study"
    shutil.copytree(FIXTURE, study_root)
    atlas_root = study_root / "1.分类资源/图片/图集/KleiTEX/data/anim"
    sample = atlas_root / "sample_build"
    sample.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(sample / "atlas-0.png")
    Image.new("RGBA", (8, 8), (0, 255, 0, 128)).save(sample / "atlas-1.png")
    wave = atlas_root / "wave_shimmer"
    wave.mkdir(parents=True)
    Image.new("RGBA", (4, 4), (0, 180, 255, 180)).save(wave / "atlas-0.png")
    builder = load_script(BUILD_SCRIPT, f"test_animation_builder_{tmp_path.name}")
    manifest = builder.build_game_animation_manifest(study_root, CONFIG)
    sample_item = next(item for item in manifest["items"] if item["bank"]["name"] == "sample_bank")
    return study_root, study_root / MANIFEST_REL, sample_item


def make_wave_74_study(tmp_path: Path):
    study_root, manifest_path, _sample = make_built_study(tmp_path)
    anim_path = (
        study_root
        / "4.临时目录/中间索引/Klei动画XML/wave_shimmer/anim.xml"
    )
    tree = ET.parse(anim_path)
    animation = tree.getroot().find("anim")
    assert animation is not None
    template = animation.find("frame")
    assert template is not None
    for frame in list(animation.findall("frame")):
        animation.remove(frame)
    animation.attrib["numframes"] = "74"
    for _index in range(65):
        animation.append(copy.deepcopy(template))
    for _index in range(9):
        animation.append(ET.Element("frame", dict(template.attrib)))
    ET.indent(tree, space="  ")
    tree.write(anim_path, encoding="utf-8", xml_declaration=False)

    builder = load_script(BUILD_SCRIPT, f"test_animation_builder_wave74_{tmp_path.name}")
    manifest = builder.build_game_animation_manifest(study_root, CONFIG)
    wave = next(item for item in manifest["items"] if item["bank"]["name"] == "wave_shimmer")
    return study_root, manifest_path, wave


def test_affine_math_current_png_layer_order_and_transparency(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_affine")
    vertex = exporter.AlphaVertex(2, 3, 0, 0, 0)
    element = ET.fromstring(
        '<element m_a="0" m_b="1" m_c="-1" m_d="0" m_tx="4" m_ty="5"/>'
    )
    assert exporter.transform_point(vertex, element) == (1.0, 7.0)
    study_root, manifest_path, item = make_built_study(tmp_path)

    result = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector="idle",
        output_root=tmp_path / "out",
        export_format="current-frame-png",
        frame_index=0,
    )

    assert result["status"] == "written"
    with Image.open(result["outputs"][0]) as image:
        rgba = image.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        pixels = (
            rgba.get_flattened_data()
            if hasattr(rgba, "get_flattened_data")
            else rgba.getdata()
        )
        composite_pixels = [
            pixel
            for pixel in pixels
            if pixel[0] > 0 and pixel[1] > 0 and pixel[3] > 128
        ]
        assert composite_pixels
        assert any(green > red for red, green, _blue, _alpha in composite_pixels)


def test_png_sequence_apng_and_gif_preserve_frame_count_and_fps(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_animated")
    study_root, manifest_path, item = make_built_study(tmp_path)
    output_root = tmp_path / "out"

    sequence = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector="idle",
        output_root=output_root,
        export_format="png-sequence",
    )
    apng = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector="idle",
        output_root=output_root,
        export_format="apng",
    )
    gif = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector="idle",
        output_root=output_root,
        export_format="gif",
    )

    assert sequence["frame_count"] == apng["frame_count"] == gif["frame_count"] == 2
    assert sequence["fps"] == apng["fps"] == gif["fps"] == 10.0
    assert sequence["duration_ms_per_frame"] == 100
    sizes = []
    for path in sequence["outputs"]:
        with Image.open(path) as frame:
            sizes.append(frame.size)
    assert len(set(sizes)) == 1
    with Image.open(apng["outputs"][0]) as image:
        assert image.n_frames == 2
    with Image.open(gif["outputs"][0]) as image:
        assert image.n_frames == 2


def test_wave_74_legal_empty_tail_preserves_timeline_in_all_formats(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_wave74")
    study_root, manifest_path, item = make_wave_74_study(tmp_path)
    clip = item["clips"][0]
    assert item["preview_status"] == "ready"
    assert clip["preview_status"] == "ready"
    assert clip["frame_count"] == clip["declared_frame_count"] == 74
    assert "legal_empty_frames:9" in clip["reasons"]
    assert all(clip["capabilities"].values())

    output_root = tmp_path / "wave-74-output"
    current = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector=clip["id"],
        output_root=output_root,
        export_format="current-frame-png",
        frame_index=70,
    )
    sequence = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector=clip["id"],
        output_root=output_root,
        export_format="png-sequence",
    )
    apng = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector=clip["id"],
        output_root=output_root,
        export_format="apng",
    )
    gif = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector=clip["id"],
        output_root=output_root,
        export_format="gif",
    )
    bundle = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector=clip["id"],
        output_root=output_root,
        export_format="source-bundle",
    )

    assert current["input_frame_count"] == 74
    assert sequence["input_frame_count"] == sequence["frame_count"] == 74
    assert apng["input_frame_count"] == apng["frame_count"] == 74
    assert gif["input_frame_count"] == gif["frame_count"] == 74
    assert bundle["input_frame_count"] == 74
    assert len(sequence["outputs"]) == 74
    with Image.open(current["outputs"][0]) as image:
        assert image.convert("RGBA").getchannel("A").getextrema() == (0, 0)
    for index in (65, 73):
        with Image.open(sequence["outputs"][index]) as image:
            assert image.convert("RGBA").getchannel("A").getextrema() == (0, 0)
    with Image.open(apng["outputs"][0]) as image:
        assert image.n_frames == 74
        apng_duration = sum(
            image.seek(index) or int(image.info["duration"])
            for index in range(image.n_frames)
        )
        image.seek(73)
        assert image.convert("RGBA").getchannel("A").getextrema() == (0, 0)
    with Image.open(gif["outputs"][0]) as image:
        assert image.n_frames == 74
        gif_duration = sum(
            image.seek(index) or int(image.info["duration"])
            for index in range(image.n_frames)
        )
        image.seek(73)
        assert image.convert("RGBA").getchannel("A").getextrema() == (0, 0)
    assert apng_duration == apng["duration_ms_per_frame"] * 74
    expected_duration = 1000 / gif["fps"] * 74
    assert abs(gif_duration - expected_duration) <= 10 * 74
    with zipfile.ZipFile(bundle["outputs"][0]) as archive:
        assert "provenance.json" in archive.namelist()


def test_empty_frame_exception_does_not_hide_missing_atlas_symbol_or_degenerate_quad(
    tmp_path: Path,
) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_empty_fail_closed")
    study_root, manifest_path, item = make_built_study(tmp_path)
    candidate = next(
        value for value in item["build_candidates"] if value["id"] == item["default_build_id"]
    )
    build = exporter.parse_build(study_root / Path(candidate["build"]["source_path"]))
    atlas_images = [
        Image.open(study_root / Path(relative)).convert("RGBA")
        for relative in candidate["atlases"]
    ]
    try:
        empty = ET.fromstring('<frame w="0" h="0" x="0" y="0"/>')
        rendered = exporter.render_frame(build, atlas_images, empty)
        assert rendered.element_count == rendered.quad_count == 0
        assert rendered.image.getchannel("A").getextrema() == (0, 0)

        missing_symbol = ET.fromstring(
            '<frame><element name="absent" frame="0" z_index="0" '
            'm_a="1" m_b="0" m_c="0" m_d="1" m_tx="0" m_ty="0"/></frame>'
        )
        with pytest.raises(exporter.ExportError, match="has no symbol"):
            exporter.render_frame(build, atlas_images, missing_symbol)

        composite = ET.fromstring(
            '<frame w="10" h="10" x="0" y="0">'
            '<element name="BACK" frame="0" z_index="1" '
            'm_a="1" m_b="0" m_c="0" m_d="1" m_tx="0" m_ty="0"/>'
            '<element name="runtime_swap" frame="0" z_index="0" '
            'm_a="1" m_b="0" m_c="0" m_d="1" m_tx="0" m_ty="0"/>'
            '</frame>'
        )
        rendered_composite = exporter.render_frame(
            build, atlas_images, composite, missing_symbol_policy="skip"
        )
        assert rendered_composite.element_count == 1
        assert rendered_composite.quad_count > 0

        degenerate = ET.fromstring(
            '<frame><element name="back" frame="0" z_index="0" '
            'm_a="0" m_b="0" m_c="0" m_d="0" m_tx="0" m_ty="0"/></frame>'
        )
        with pytest.raises(exporter.ExportError, match="degenerate destination quad"):
            exporter.render_frame(build, atlas_images, degenerate)
    finally:
        for image in atlas_images:
            image.close()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_item = next(value for value in manifest["items"] if value["id"] == item["id"])
    selected = next(
        value
        for value in manifest_item["build_candidates"]
        if value["id"] == manifest_item["default_build_id"]
    )
    selected["atlases"][0] = "1.分类资源/图片/图集/KleiTEX/data/anim/missing.png"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((exporter.ExportError, FileNotFoundError)):
        exporter.export_animation(
            study_root=study_root,
            manifest_path=manifest_path,
            item_selector=item["id"],
            clip_selector="idle",
            output_root=tmp_path / "missing-atlas-output",
            export_format="current-frame-png",
        )


def test_source_bundle_has_xml_atlases_sha256_and_does_not_mutate_sources(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_bundle")
    study_root, manifest_path, item = make_built_study(tmp_path)
    candidate = item["build_candidates"][0]
    source_relatives = [
        item["clips"][0]["source_path"],
        candidate["build"]["source_path"],
        *candidate["atlases"],
    ]
    before = {
        relative: (
            exporter.sha256_file(study_root / Path(relative)),
            (study_root / Path(relative)).stat().st_mtime_ns,
        )
        for relative in source_relatives
    }

    result = exporter.export_animation(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        build_selector=candidate["id"],
        clip_selector="idle",
        output_root=tmp_path / "out",
        export_format="source-bundle",
    )

    after = {
        relative: (
            exporter.sha256_file(study_root / Path(relative)),
            (study_root / Path(relative)).stat().st_mtime_ns,
        )
        for relative in source_relatives
    }
    assert before == after
    with zipfile.ZipFile(result["outputs"][0]) as archive:
        names = archive.namelist()
        assert "provenance.json" in names
        assert sum(name.endswith(".xml") for name in names) == 2
        assert sum(name.endswith(".png") for name in names) == 2
        provenance = json.loads(archive.read("provenance.json"))
    assert {entry["path"] for entry in provenance["files"]} == set(source_relatives)
    assert all(len(entry["sha256"]) == 64 for entry in provenance["files"])


def test_export_is_idempotent_and_refuses_mismatched_existing_output(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_idempotent")
    study_root, manifest_path, item = make_built_study(tmp_path)
    arguments = dict(
        study_root=study_root,
        manifest_path=manifest_path,
        item_selector=item["id"],
        clip_selector="idle",
        output_root=tmp_path / "out",
        export_format="current-frame-png",
    )
    first = exporter.export_animation(**arguments)
    second = exporter.export_animation(**arguments)
    assert first["status"] == "written"
    assert second["status"] == "reused"
    Path(first["outputs"][0]).write_bytes(b"conflict")
    with pytest.raises(FileExistsError, match="different content"):
        exporter.export_animation(**arguments)


def test_export_cli_runs_current_frame_end_to_end(tmp_path: Path) -> None:
    study_root, manifest_path, item = make_built_study(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--study-root",
            str(study_root),
            "--manifest",
            str(manifest_path),
            "--item",
            item["id"],
            "--clip",
            "idle",
            "--output-root",
            str(tmp_path / "cli-output"),
            "--format",
            "current-frame-png",
            "--frame",
            "1",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["format"] == "current-frame-png"
    assert payload["fps"] == 10.0
    assert Path(payload["outputs"][0]).is_file()


def test_manifest_source_traversal_is_rejected(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_traversal")
    study_root, manifest_path, item = make_built_study(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_item = next(value for value in manifest["items"] if value["id"] == item["id"])
    manifest_item["clips"][0]["source_path"] = "../outside.xml"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(exporter.ExportError, match="Unsafe manifest source path"):
        exporter.export_animation(
            study_root=study_root,
            manifest_path=manifest_path,
            item_selector=item["id"],
            clip_selector="idle",
            output_root=tmp_path / "out",
            export_format="source-bundle",
        )
    assert not (tmp_path / "outside.xml").exists()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink is unavailable")
def test_output_reparse_root_is_rejected_when_symlink_creation_is_available(tmp_path: Path) -> None:
    exporter = load_script(EXPORT_SCRIPT, "test_animation_exporter_reparse")
    study_root, manifest_path, item = make_built_study(tmp_path)
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("current Windows account cannot create symlinks")

    with pytest.raises(exporter.ExportError, match="symlink/reparse"):
        exporter.export_animation(
            study_root=study_root,
            manifest_path=manifest_path,
            item_selector=item["id"],
            clip_selector="idle",
            output_root=linked_output,
            export_format="current-frame-png",
        )

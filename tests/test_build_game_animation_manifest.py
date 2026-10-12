from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_game_animation_manifest.py"
CONFIG = ROOT / "references" / "game-animation-viewer-config.json"
SCHEMA = ROOT / "references" / "game-animation-manifest-schema.json"
FIXTURE = ROOT / "tests" / "fixtures" / "game_animation"
MANIFEST_REL = Path("4.临时目录/中间索引/游戏动画预览/manifest.json")


def load_module():
    spec = importlib.util.spec_from_file_location("test_game_animation_manifest_builder", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_study_root(tmp_path: Path) -> Path:
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
    return study_root


def item_by_bank(manifest: dict, bank_name: str) -> dict:
    return next(item for item in manifest["items"] if item["bank"]["name"] == bank_name)


def add_player_runtime_override_fixture(study_root: Path) -> None:
    xml_root = study_root / "4.临时目录/中间索引/Klei动画XML"
    anim_source = (xml_root / "sample_bank_pkg/anim.xml").read_text(encoding="utf-8")
    anim_source = (
        anim_source.replace('name="idle"', 'name="ocean_idle"')
        .replace('root="sample_bank"', 'root="wilson"')
        .replace('name="back"', 'name="PLAYER_BASE"')
        .replace('name="front"', 'name="swap_object"')
    )
    player_package = xml_root / "player_runtime_override_test"
    player_package.mkdir()
    (player_package / "anim.xml").write_text(anim_source, encoding="utf-8")

    build_source = (xml_root / "sample_build/build.xml").read_text(encoding="utf-8")
    build_source = (
        build_source.replace('name="sample_visual_build"', 'name="wilson"')
        .replace('name="back"', 'name="player_base"')
        .replace('name="front"', 'name="unused_layer"')
    )
    build_package = xml_root / "wilson"
    build_package.mkdir()
    (build_package / "build.xml").write_text(build_source, encoding="utf-8")

    atlas_root = study_root / "1.分类资源/图片/图集/KleiTEX/data/anim"
    shutil.copytree(atlas_root / "sample_build", atlas_root / "wilson")


def test_build_manifest_preserves_bank_and_build_name_package_boundaries(tmp_path: Path) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)

    manifest = module.build_game_animation_manifest(study_root, CONFIG)

    assert manifest["schema_version"] == 1
    sample = item_by_bank(manifest, "sample_bank")
    assert sample["bank"]["name"] == "sample_bank"
    assert sample["bank"]["package"] == "sample_bank_pkg"
    assert sample["bank"]["source_path"].endswith("sample_bank_pkg/anim.xml")
    assert sample["preview_status"] == "ready"
    assert sample["default_build_id"]
    candidate = sample["build_candidates"][0]
    assert candidate["build"]["name"] == "sample_visual_build"
    assert candidate["build"]["package"] == "sample_build"
    assert candidate["reasons"] == ["symbol_overlap"]
    assert candidate["symbol_coverage"] == 1.0
    assert candidate["texture_names"] == ["atlas-0.tex", "atlas-1.tex"]
    assert candidate["preview_status"] == "ready"
    assert candidate["capabilities"]["png_sequence"]
    assert "structure_preflight_passed" in candidate["preview_reasons"]
    assert [Path(path).name for path in candidate["atlases"]] == [
        "atlas-0.png",
        "atlas-1.png",
    ]
    assert all(path.endswith(".png") for path in candidate["atlases"])
    assert all(not path.endswith(".xml") for path in candidate["atlases"])


def test_manifest_paths_are_relative_posix_and_ids_are_stable(tmp_path: Path) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)
    first = module.build_game_animation_manifest(study_root, CONFIG)
    second = module.build_game_animation_manifest(study_root, CONFIG)

    assert [item["id"] for item in first["items"]] == [item["id"] for item in second["items"]]
    for item in first["items"]:
        paths = [item["bank"]["source_path"]]
        paths.extend(asset["path"] for asset in item["source_assets"])
        paths.extend(clip["source_path"] for clip in item["clips"])
        for candidate in item["build_candidates"]:
            paths.append(candidate["build"]["source_path"])
            paths.extend(candidate["atlases"])
        for value in paths:
            posix = PurePosixPath(value)
            assert not posix.is_absolute()
            assert ".." not in posix.parts
            assert "\\" not in value
    persisted = json.loads((study_root / MANIFEST_REL).read_text(encoding="utf-8"))
    assert persisted == second
    assert json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["item"]


def test_missing_build_and_atlas_are_not_reported_ready(tmp_path: Path) -> None:
    module = load_module()
    manifest = module.build_game_animation_manifest(make_study_root(tmp_path), CONFIG)

    missing = item_by_bank(manifest, "missing_bank")
    assert missing["preview_status"] == "blocked"
    assert missing["default_build_id"] is None
    assert missing["errors"] == ["no_build_candidate"]
    assert missing["clips"][0]["preview_status"] == "blocked"
    assert "default_build_unavailable" in missing["clips"][0]["reasons"]
    atlasless = item_by_bank(manifest, "atlasless")
    assert atlasless["preview_status"] == "partial"
    assert atlasless["default_build_id"] is None
    assert "no_candidate_with_complete_atlas" in atlasless["errors"]
    assert not atlasless["capabilities"]["current_frame_png"]
    assert atlasless["clips"][0]["preview_status"] == "blocked"
    assert "atlas_incomplete" in atlasless["clips"][0]["reasons"]


def test_clip_fps_frame_count_and_water_boundary_category(tmp_path: Path) -> None:
    module = load_module()
    manifest = module.build_game_animation_manifest(make_study_root(tmp_path), CONFIG)

    clip = item_by_bank(manifest, "sample_bank")["clips"][0]
    assert clip["fps"] == 10.0
    assert clip["frame_count"] == 2
    assert clip["declared_frame_count"] == 2
    assert clip["preview_status"] == "ready"
    assert all(clip["capabilities"].values())
    assert "structure_preflight_passed" in clip["reasons"]
    wave = item_by_bank(manifest, "wave_shimmer")
    assert wave["category"] == "水面动画"
    assert "keyword:wave" in wave["reasons"]


def test_ready_requires_configured_full_symbol_coverage(tmp_path: Path) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)
    build_xml = study_root / "4.临时目录/中间索引/Klei动画XML/sample_build/build.xml"
    changed = build_xml.read_text(encoding="utf-8")
    changed = changed.replace(
        '<Build name="sample_visual_build">', '<Build name="sample_bank">'
    ).replace('<Symbol name="front">', '<Symbol name="unused">')
    build_xml.write_text(changed, encoding="utf-8")

    manifest = module.build_game_animation_manifest(study_root, CONFIG)

    sample = item_by_bank(manifest, "sample_bank")
    assert sample["build_candidates"][0]["symbol_coverage"] == 0.5
    assert sample["default_build_id"] is None
    assert sample["preview_status"] == "partial"
    assert "no_candidate_meets_ready_symbol_coverage" in sample["warnings"]


def test_player_runtime_override_build_is_discoverable_and_partially_previewable(
    tmp_path: Path,
) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)
    add_player_runtime_override_fixture(study_root)

    manifest = module.build_game_animation_manifest(study_root, CONFIG)
    player = next(
        item
        for item in manifest["items"]
        if item["bank"]["package"] == "player_runtime_override_test"
    )

    assert player["bank"]["name"] == "wilson"
    assert player["category"] == "人物动画"
    assert player["preview_status"] == "partial"
    assert player["default_build_id"]
    candidate = next(
        value
        for value in player["build_candidates"]
        if value["id"] == player["default_build_id"]
    )
    assert candidate["build"]["package"] == "wilson"
    assert candidate["symbol_coverage"] == 0.5
    assert candidate["missing_symbols"] == ["swap_object"]
    assert candidate["missing_symbol_policy"] == "skip"
    assert candidate["capabilities"]["png_sequence"]
    assert "render_policy:dst_player_runtime_overrides" in candidate["preview_reasons"]
    clip = player["clips"][0]
    assert clip["name"] == "ocean_idle"
    assert clip["preview_status"] == "partial"
    assert clip["capabilities"]["current_frame_png"]
    assert "runtime_override_symbols_skipped:1" in clip["reasons"]


def test_equal_ready_candidates_preserve_ambiguity_instead_of_guessing(tmp_path: Path) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)
    xml_root = study_root / "4.临时目录/中间索引/Klei动画XML"
    shutil.copytree(xml_root / "sample_build", xml_root / "sample_build_clone")
    atlas_root = study_root / "1.分类资源/图片/图集/KleiTEX/data/anim"
    shutil.copytree(atlas_root / "sample_build", atlas_root / "sample_build_clone")

    manifest = module.build_game_animation_manifest(study_root, CONFIG)

    sample = item_by_bank(manifest, "sample_bank")
    assert len(sample["build_candidates"]) >= 2
    assert sample["build_candidates"][0]["score"] == sample["build_candidates"][1]["score"]
    assert sample["default_build_id"] is None
    assert sample["preview_status"] == "partial"
    assert "ambiguous_build_candidates" in sample["warnings"]
    assert sample["clips"][0]["preview_status"] == "partial"
    assert "ambiguous_build_candidates" in sample["clips"][0]["reasons"]


def test_item_capabilities_are_aggregated_from_clip_preflight_not_default_build(
    tmp_path: Path,
) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)
    anim_path = (
        study_root
        / "4.临时目录/中间索引/Klei动画XML/sample_bank_pkg/anim.xml"
    )
    tree = ET.parse(anim_path)
    root = tree.getroot()
    invalid = ET.fromstring(
        '<anim name="invalid_fps" root="sample_bank" framerate="0" numframes="1">'
        '<frame w="10" h="10" x="0" y="0">'
        '<element name="back" frame="0" z_index="0" '
        'm_a="1" m_b="0" m_c="0" m_d="1" m_tx="0" m_ty="0"/>'
        '</frame></anim>'
    )
    root.append(invalid)
    ET.indent(tree, space="  ")
    tree.write(anim_path, encoding="utf-8", xml_declaration=False)

    sample = item_by_bank(
        module.build_game_animation_manifest(study_root, CONFIG), "sample_bank"
    )

    assert sample["default_build_id"]
    assert sample["preview_status"] == "partial"
    assert not sample["capabilities"]["current_frame_png"]
    invalid_clip = next(clip for clip in sample["clips"] if clip["name"] == "invalid_fps")
    assert invalid_clip["preview_status"] == "blocked"
    assert "invalid_fps" in invalid_clip["reasons"]


def test_cli_builds_manifest(tmp_path: Path) -> None:
    study_root = make_study_root(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(study_root),
            "--config",
            str(CONFIG),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_count"] == 4
    assert (study_root / MANIFEST_REL).is_file()


def test_manifest_output_traversal_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    study_root = make_study_root(tmp_path)

    try:
        module.build_game_animation_manifest(study_root, CONFIG, "../outside.json")
        assert False, "traversal output should be rejected"
    except ValueError as error:
        assert "relative" in str(error) or "escapes" in str(error)
    assert not (study_root.parent / "outside.json").exists()

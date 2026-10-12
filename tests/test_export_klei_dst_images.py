from __future__ import annotations

import csv
import io
import json
import random
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import export_klei_dst_images as exporter  # noqa: E402


def png_bytes(color: tuple[int, int, int, int] = (31, 127, 223, 255)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", (3, 2), color).save(stream, format="PNG")
    return stream.getvalue()


def write_manifest(path: Path, sources: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps({"version": 1, "sources": sources}, ensure_ascii=False), encoding="utf-8")
    return path


def source_entry(
    source_id: str,
    root: Path,
    output_prefix: str,
    kind: str = "game",
    terrain_prefix: str = "",
) -> dict[str, str]:
    return {
        "id": source_id,
        "root": str(root),
        "output_prefix": output_prefix,
        "terrain_prefix": terrain_prefix,
        "kind": kind,
        "display_name": source_id,
    }


def read_rows(study: Path) -> list[dict[str, str]]:
    path = (
        study
        / "4.临时目录"
        / "中间索引"
        / "KleiDST"
        / "图片补全"
        / "klei_dst_image_export_index.csv"
    )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_terrain_rows(study: Path) -> list[dict[str, str]]:
    path = (
        study
        / "4.临时目录"
        / "中间索引"
        / "KleiDST"
        / "图片补全"
        / "klei_dst_terrain_image_index.csv"
    )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def fake_stex_factory(calls: list[bytes], fail_first: bool = False):
    def fake_stex(_stex: Path, source: Path, output: Path) -> tuple[int, str]:
        payload = source.read_bytes()
        calls.append(payload)
        output.parent.mkdir(parents=True, exist_ok=True)
        if fail_first and len(calls) == 1:
            output.write_bytes(b"partial-not-a-png")
            return 1, "intentional partial failure"
        shade = sum(payload) % 255
        output.write_bytes(png_bytes((shade, 90, 180, 255)))
        return 0, "fake success"

    return fake_stex


def test_terrain_rules_cover_audited_p0_boundaries(tmp_path: Path) -> None:
    config = json.loads(
        (ROOT / "references" / "klei-dst-image-export-config.json").read_text(encoding="utf-8")
    )
    image_exporter = exporter.ImageExporter(tmp_path / "study", ROOT, config, False)

    positive_paths = [
        "data/levels/textures/snow.tex",
        "data/minimap/minimap_atlas.tex",
        "data/minimap/minimap_atlas1.tex",
        "data/minimap/minimap_atlas2.tex",
        "data/images/minimap_paper.tex",
        *(
            f"data/databundles/images/images/{name}.tex"
            for name in (
                "roadnoise",
                "roadedge",
                "roadcorner",
                "roadendcap",
                "pathnoise",
                "mini_pathnoise",
            )
        ),
    ]
    for logical_path in positive_paths:
        confidence, reason = image_exporter._terrain_match(logical_path)
        assert confidence == "high", logical_path
        assert reason, logical_path

    negative_paths = [
        "data/images/map_icons/snow.tex",
        "data/images/inventoryimages/turf_road.tex",
        "data/anim/ground_fx/atlas-0.tex",
    ]
    for logical_path in negative_paths:
        confidence, _ = image_exporter._terrain_match(logical_path)
        assert confidence != "high", logical_path


def test_writes_separate_terrain_index(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    (source / "data" / "levels" / "textures").mkdir(parents=True)
    (source / "data" / "images").mkdir(parents=True)
    (source / "data" / "ground").mkdir(parents=True)
    (source / "data" / "levels" / "textures" / "snow.tex").write_bytes(b"high-terrain")
    (source / "data" / "images" / "ordinary.tex").write_bytes(b"ordinary")
    (source / "data" / "ground" / "fx.tex").write_bytes(b"medium-ground-fx")
    (source / "data" / "levels" / "textures" / "snow.xml").write_text(
        "<Atlas/>", encoding="utf-8"
    )
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory([]))

    exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    unified_rows = read_rows(study)
    assert len(unified_rows) == 4
    terrain_rows = read_terrain_rows(study)
    assert [row["source_relative"] for row in terrain_rows] == [
        "data/levels/textures/snow.tex"
    ]
    terrain_row = terrain_rows[0]
    assert terrain_row["terrain_confidence"] == "high"
    assert terrain_row["origin_kind"] == "loose_tex"
    terrain_output = Path(terrain_row["terrain_output"])
    assert terrain_output.is_file()
    with Image.open(terrain_output) as image:
        image.verify()

    index_root = study / "4.临时目录" / "中间索引" / "KleiDST" / "图片补全"
    terrain_json = json.loads(
        (index_root / "klei_dst_terrain_image_index.json").read_text(encoding="utf-8")
    )
    assert [row["source_relative"] for row in terrain_json["rows"]] == [
        "data/levels/textures/snow.tex"
    ]
    assert terrain_json["rows"][0]["terrain_output"] == terrain_row["terrain_output"]


def test_terrain_output_uses_chinese_category_then_source_prefix_and_index_fields(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    fixtures = {
        "data/levels/tiles/grass.tex": "地图Tile图集PNG",
        "data/images/roadnoise.tex": "道路与噪声PNG",
        "data/minimap/minimap_atlas.tex": "小地图图集与底图PNG",
        "data/levels/textures/mini_beach.tex": "小地图地表纹理PNG",
        "data/levels/textures/forest_noise.tex": "地表噪声与材质PNG",
        "data/levels/textures/lava.tex": "地表噪声与材质PNG",
    }
    for relative in fixtures:
        texture = source.joinpath(*relative.split("/"))
        texture.parent.mkdir(parents=True, exist_ok=True)
        texture.write_bytes(relative.encode("utf-8"))
    manifest = write_manifest(
        tmp_path / "sources.json",
        [source_entry("base", source, "", terrain_prefix="")],
    )
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory([]))

    result = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    assert result["terrain_archived"] == len(fixtures)
    rows = {row["source_relative"]: row for row in read_terrain_rows(study)}
    assert set(rows) == set(fixtures)
    terrain_root = study / "1.分类资源" / "图片" / "地形贴图"
    for relative, category in fixtures.items():
        row = rows[relative]
        expected = terrain_root / category / f"{Path(relative).stem}.png"
        assert Path(row["terrain_output"]) == expected
        assert row["terrain_prefix"] == ""
        assert row["terrain_category"] == category
        assert expected.is_file()


def test_same_named_terrain_is_isolated_by_source_prefix_and_rerun_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    base = tmp_path / "base"
    mod = tmp_path / "mod"
    study = tmp_path / "study"
    for root, payload in ((base, b"base-grass"), (mod, b"mod-grass")):
        texture = root / "data" / "levels" / "tiles" / "grass.tex"
        texture.parent.mkdir(parents=True, exist_ok=True)
        texture.write_bytes(payload)
    manifest = write_manifest(
        tmp_path / "sources.json",
        [
            source_entry("base", base, "base", terrain_prefix=""),
            source_entry(
                "mod",
                mod,
                "mods/sample",
                kind="mod",
                terrain_prefix="模组/样例（123）",
            ),
        ],
    )
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))

    first = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )
    second = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    terrain_root = study / "1.分类资源" / "图片" / "地形贴图" / "地图Tile图集PNG"
    outputs = {
        terrain_root / "grass.png",
        terrain_root / "模组" / "样例（123）" / "grass.png",
    }
    assert all(path.is_file() for path in outputs)
    rows = read_terrain_rows(study)
    assert {Path(row["terrain_output"]) for row in rows} == outputs
    assert first["conflicts"] == 0
    assert second["conflicts"] == 0
    assert second["terrain_archived"] == 2
    assert {row["terrain_status"] for row in rows} == {"up_to_date"}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "terrain_prefix",
    [
        "/",
        "\\",
        "../逃逸",
        "/绝对路径",
        "C:/逃逸",
        "模组//空段",
        "模组/坏<名",
        "模组/坏>名",
        '模组/坏"名',
        "模组/坏|名",
        "模组/坏?名",
        "模组/坏*名",
        "模组/坏\x01名",
        "模组/尾随空格 ",
        "模组/尾随点.",
        "CON",
        "模组/PRN.txt",
        "模组/com1",
        "模组/Lpt9.dat",
    ],
)
def test_rejects_unsafe_terrain_prefix(tmp_path: Path, terrain_prefix: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = write_manifest(
        tmp_path / "sources.json",
        [source_entry("bad", source, "", terrain_prefix=terrain_prefix)],
    )

    with pytest.raises(exporter.ManifestError, match="terrain_prefix"):
        exporter.run_export(
            tmp_path / "study",
            manifest,
            ROOT,
            ROOT / "references" / "klei-dst-image-export-config.json",
            False,
        )


def test_exports_multiple_sources_loose_zip_nested_direct_xml_and_terrain(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base"
    mod = tmp_path / "mod"
    study = tmp_path / "study"
    (base / "data" / "levels" / "tiles").mkdir(parents=True)
    (mod / "packs").mkdir(parents=True)

    (base / "data" / "levels" / "tiles" / "grass.tex").write_bytes(b"KTEX-grass")
    (base / "data" / "levels" / "tiles" / "grass.xml").write_text("<Atlas/>", encoding="utf-8")
    (base / "preview.png").write_bytes(png_bytes())

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as inner:
        inner.writestr("deep/deep_texture.tex", b"KTEX-deep")
    with zipfile.ZipFile(mod / "packs" / "images.zip", "w") as archive:
        archive.writestr("atlas-0.tex", b"KTEX-atlas")
        archive.writestr("direct.png", png_bytes((220, 30, 40, 255)))
        archive.writestr("atlas.xml", "<Atlas><Element name='x'/></Atlas>")
        archive.writestr("../escape.tex", b"unsafe")
        archive.writestr("nested.zip", nested.getvalue())

    manifest = write_manifest(
        tmp_path / "sources.json",
        [source_entry("base", base, ""), source_entry("sample-mod", mod, "Mods/sample", "mod")],
    )
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))

    result = exporter.run_export(
        study_root=study,
        sources_manifest=manifest,
        skill_root=ROOT,
        config_path=ROOT / "references" / "klei-dst-image-export-config.json",
        force=False,
    )

    assert result["failed"] == 0
    assert len(calls) == 3
    assert (study / "0.原始导出" / "KleiDST" / "textures_ktex" / "data" / "levels" / "tiles" / "grass.tex").read_bytes() == b"KTEX-grass"
    assert (study / "0.原始导出" / "KleiDST" / "atlas_metadata" / "data" / "levels" / "tiles" / "grass.xml").exists()
    assert (study / "0.原始导出" / "KleiDST" / "packages" / "Mods" / "sample" / "packs" / "images.zip").exists()
    assert (study / "0.原始导出" / "KleiDST" / "packages_extracted" / "Mods" / "sample" / "packs" / "images" / "nested" / "deep" / "deep_texture.tex").exists()

    classified = study / "1.分类资源" / "图片" / "图集" / "KleiTEX"
    terrain = study / "1.分类资源" / "图片" / "地形贴图"
    expected_pngs = [
        classified / "textures_ktex" / "data" / "levels" / "tiles" / "grass.png",
        classified / "direct_images" / "preview.png",
        classified / "Mods" / "sample" / "packs" / "images" / "atlas-0.png",
        classified / "Mods" / "sample" / "packs" / "images" / "direct.png",
        classified / "Mods" / "sample" / "packs" / "images" / "nested" / "deep" / "deep_texture.png",
        terrain / "地图Tile图集PNG" / "grass.png",
    ]
    for image_path in expected_pngs:
        with Image.open(image_path) as image:
            image.verify()
    assert not any(path.suffix.lower() == ".xml" for path in classified.rglob("*"))

    rows = read_rows(study)
    assert {row["source_id"] for row in rows} == {"base", "sample-mod"}
    nested_row = next(row for row in rows if row["member"] == "deep/deep_texture.tex")
    assert nested_row["archive_chain"] == "packs/images.zip::nested.zip"
    assert nested_row["origin_kind"] == "archive_tex"
    unsafe_row = next(row for row in rows if row["member"] == "../escape.tex")
    assert unsafe_row["status"] == "unsafe_archive_member"
    xml_rows = [row for row in rows if row["origin_kind"].endswith("atlas_metadata")]
    assert xml_rows and all(not row["output"] for row in xml_rows)
    terrain_row = next(row for row in rows if row["source"].endswith("grass.tex"))
    assert terrain_row["terrain_confidence"] == "high"
    assert terrain_row["terrain_reason"]
    assert terrain_row["terrain_output"]


def test_mixed_loose_archive_nested_and_terrain_rerun_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    (source / "loose").mkdir(parents=True)
    (source / "packs").mkdir(parents=True)
    (source / "loose" / "plain.tex").write_bytes(b"KTEX-loose")
    (source / "loose" / "preview.png").write_bytes(png_bytes())
    (source / "loose" / "atlas.xml").write_text("<Atlas/>", encoding="utf-8")

    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as inner:
        inner.writestr("deep/deep_texture.tex", b"KTEX-nested")
    with zipfile.ZipFile(source / "packs" / "assets.zip", "w") as archive:
        archive.writestr("atlas-0.tex", b"KTEX-archive")
        archive.writestr("direct.png", png_bytes((220, 30, 40, 255)))
        archive.writestr("atlas.xml", "<Atlas/>")
        archive.writestr("nested.zip", nested.getvalue())
        archive.writestr("data/levels/tiles/terrain.tex", b"KTEX-terrain")

    manifest = write_manifest(
        tmp_path / "sources.json", [source_entry("fixture", source, "fixture")]
    )
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))

    first = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )
    assert first["total"] == 10
    assert first["conflicts"] == 0
    assert len(calls) == 4

    second = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )
    assert second["total"] == 10
    assert second["conflicts"] == 0
    assert second["unsafe"] == 0
    assert second["terrain_archived"] == 1
    assert second["status_counts"] == {
        "metadata_up_to_date": 2,
        "up_to_date": 6,
        "raw_up_to_date": 2,
    }
    assert len(calls) == 4

    rows = read_rows(study)
    image_rows = [
        row
        for row in rows
        if row["origin_kind"]
        in {"loose_tex", "loose_direct_image", "archive_tex", "archive_direct_image"}
    ]
    assert len(image_rows) == 6
    assert all(row["status"] == "up_to_date" for row in image_rows)
    terrain_rows = [row for row in image_rows if row["terrain_output"]]
    assert len(terrain_rows) == 1
    assert terrain_rows[0]["terrain_status"] == "up_to_date"
    assert {
        row["origin_kind"]: row["status"]
        for row in rows
        if row["origin_kind"] in {"archive", "nested_archive"}
    } == {"archive": "raw_up_to_date", "nested_archive": "raw_up_to_date"}


def test_default_zip_ratio_accepts_audited_range_but_rejects_over_limit(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    archive_root = source / "anim"
    archive_root.mkdir(parents=True)

    fixed_prefix = random.Random(0xD57).randbytes(4 * 1024)
    between_payload = b"KTEX" + fixed_prefix + (b"\0" * (2 * 1024 * 1024))
    over_limit_payload = b"KTEX" + (b"\0" * (2 * 1024 * 1024))
    fixtures = {
        "between_250_and_512.zip": between_payload,
        "over_512.zip": over_limit_payload,
    }
    measured_ratios: dict[str, float] = {}
    for archive_name, payload in fixtures.items():
        archive_path = archive_root / archive_name
        with zipfile.ZipFile(
            archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.writestr("atlas-0.tex", payload)
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo("atlas-0.tex")
            measured_ratios[archive_name] = info.file_size / max(info.compress_size, 1)

    assert 250 < measured_ratios["between_250_and_512.zip"] < 512
    assert measured_ratios["over_512.zip"] > 512

    manifest = write_manifest(
        tmp_path / "sources.json", [source_entry("ratio-fixture", source, "ratio-fixture")]
    )
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory([]))
    exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    rows = {
        row["archive"]: row
        for row in read_rows(study)
        if row["member"] == "atlas-0.tex"
    }
    assert rows["anim/between_250_and_512.zip"]["status"] == "converted"
    assert rows["anim/over_512.zip"]["status"] == "unsafe_archive_member"


def test_conflicting_sources_do_not_overwrite_first_owner(tmp_path: Path, monkeypatch) -> None:
    source_a = tmp_path / "a"
    source_b = tmp_path / "b"
    study = tmp_path / "study"
    (source_a / "images").mkdir(parents=True)
    (source_b / "images").mkdir(parents=True)
    (source_a / "images" / "shared.tex").write_bytes(b"from-A")
    (source_b / "images" / "shared.tex").write_bytes(b"from-B")
    manifest = write_manifest(
        tmp_path / "sources.json",
        [source_entry("a", source_a, "same"), source_entry("b", source_b, "same")],
    )
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory([]))

    result = exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)

    raw = study / "0.原始导出" / "KleiDST" / "textures_ktex" / "same" / "images" / "shared.tex"
    assert raw.read_bytes() == b"from-A"
    assert result["conflicts"] >= 1
    rows = read_rows(study)
    assert any(row["source_id"] == "b" and row["status"] == "conflict" for row in rows)


def test_new_raw_refreshes_preexisting_valid_tex_and_direct_outputs(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    source.mkdir()
    (source / "current.tex").write_bytes(b"CURRENT-KTEX-SOURCE")
    (source / "preview.png").write_bytes(png_bytes((20, 40, 220, 255)))
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])

    classified = study / "1.分类资源" / "图片" / "图集" / "KleiTEX"
    tex_output = classified / "textures_ktex" / "current.png"
    direct_output = classified / "direct_images" / "preview.png"
    wrong_png = png_bytes((230, 20, 20, 255))
    for output in (tex_output, direct_output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wrong_png)
    before_hashes = {output: exporter.sha256_file(output) for output in (tex_output, direct_output)}

    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))
    result = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    rows = {row["source_relative"]: row for row in read_rows(study)}
    assert len(calls) == 1
    assert result["updated"] == 2
    for relative, output in (("current.tex", tex_output), ("preview.png", direct_output)):
        assert rows[relative]["raw_status"] == "raw_copied"
        assert rows[relative]["status"] == "updated"
        assert exporter.sha256_file(output) != before_hashes[output]
        with Image.open(output) as image:
            image.verify()


def test_terrain_archive_failure_counts_each_asset_once(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    terrain = source / "data" / "levels" / "textures" / "snow.tex"
    terrain.parent.mkdir(parents=True)
    terrain.write_bytes(b"TERRAIN-KTEX")
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory([]))

    def fail_archive(
        self,
        output: Path,
        identity: str,
        terrain_prefix_parts: tuple[str, ...],
        category: str,
    ) -> tuple[str, str, str]:
        return str(self.terrain_root / "forced.png"), "failed", "injected terrain archive failure"

    monkeypatch.setattr(exporter.ImageExporter, "_archive_terrain", fail_archive)
    result = exporter.run_export(
        study,
        manifest,
        ROOT,
        ROOT / "references" / "klei-dst-image-export-config.json",
        False,
    )

    failed_rows = [row for row in read_rows(study) if row["status"] == "failed"]
    assert len(failed_rows) == 1
    assert failed_rows[0]["terrain_status"] == "failed"
    assert result["status_counts"]["failed"] == 1
    assert result["failed"] == 1


def test_terrain_copy_failure_removes_partial_temporary_png(tmp_path: Path, monkeypatch) -> None:
    config = json.loads(
        (ROOT / "references" / "klei-dst-image-export-config.json").read_text(encoding="utf-8")
    )
    image_exporter = exporter.ImageExporter(tmp_path / "study", ROOT, config, False)
    canonical = (
        image_exporter.classified_root
        / "textures_ktex"
        / "data"
        / "levels"
        / "textures"
        / "snow.png"
    )
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(png_bytes((10, 20, 30, 255)))
    canonical_hash = exporter.sha256_file(canonical)

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("force copy fallback")

    def partial_copy(_source: Path, destination: Path) -> None:
        Path(destination).write_bytes(b"PARTIAL-NOT-PNG")
        raise OSError("injected partial copy failure")

    monkeypatch.setattr(exporter.os, "link", fail_link)
    monkeypatch.setattr(exporter.shutil, "copyfile", partial_copy)
    destination, status, _ = image_exporter._archive_terrain(
        canonical, "probe", (), "地表噪声与材质PNG"
    )

    assert status == "failed"
    assert canonical.is_file()
    assert exporter.sha256_file(canonical) == canonical_hash
    assert not Path(destination).exists()
    assert not list(image_exporter.terrain_root.rglob("*.tmp.png"))


@pytest.mark.parametrize(
    ("summary", "expected_code"),
    [
        ({"failed": 1, "conflicts": 0, "unsafe": 0}, 1),
        ({"failed": 0, "conflicts": 1, "unsafe": 0}, 1),
        ({"failed": 0, "conflicts": 0, "unsafe": 1}, 1),
        ({"failed": 0, "conflicts": 0, "unsafe": 0}, 0),
    ],
)
def test_main_returns_nonzero_for_incomplete_summary(
    tmp_path: Path, monkeypatch, summary: dict[str, int], expected_code: int
) -> None:
    monkeypatch.setattr(exporter, "run_export", lambda *args, **kwargs: summary)
    code = exporter.main(
        [
            "--study-root",
            str(tmp_path / "study"),
            "--sources-manifest",
            str(tmp_path / "sources.json"),
            "--skill-root",
            str(ROOT),
        ]
    )
    assert code == expected_code


def test_partial_or_invalid_png_is_removed_and_retried(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    source.mkdir()
    (source / "bad.tex").write_bytes(b"KTEX-bad")
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls, fail_first=True))

    first = exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)
    final_png = study / "1.分类资源" / "图片" / "图集" / "KleiTEX" / "textures_ktex" / "bad.png"
    assert first["failed"] == 1
    assert not final_png.exists()

    second = exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)
    assert len(calls) == 2
    assert second["converted"] == 1
    with Image.open(final_png) as image:
        image.load()


def test_source_change_refreshes_raw_png_and_index(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    source.mkdir()
    texture = source / "changing.tex"
    texture.write_bytes(b"first")
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))

    exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)
    output = study / "1.分类资源" / "图片" / "图集" / "KleiTEX" / "textures_ktex" / "changing.png"
    first_bytes = output.read_bytes()
    texture.write_bytes(b"second-and-different")
    result = exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)

    assert len(calls) == 2
    assert result["updated"] == 1
    assert output.read_bytes() != first_bytes
    raw = study / "0.原始导出" / "KleiDST" / "textures_ktex" / "changing.tex"
    assert raw.read_bytes() == b"second-and-different"
    row = next(row for row in read_rows(study) if row["source"].endswith("changing.tex"))
    assert row["status"] == "updated"
    assert len(row["sha256"]) == 64
    assert row["width"] == "3" and row["height"] == "2"


def test_unchanged_rerun_is_idempotent_and_writes_csv_json_jsonl(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    study = tmp_path / "study"
    source.mkdir()
    (source / "stable.tex").write_bytes(b"stable")
    manifest = write_manifest(tmp_path / "sources.json", [source_entry("base", source, "")])
    calls: list[bytes] = []
    monkeypatch.setattr(exporter, "run_stex", fake_stex_factory(calls))

    exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)
    output = study / "1.分类资源" / "图片" / "图集" / "KleiTEX" / "textures_ktex" / "stable.png"
    first_output_sha = exporter.sha256_file(output)
    second = exporter.run_export(study, manifest, ROOT, ROOT / "references" / "klei-dst-image-export-config.json", False)

    assert len(calls) == 1
    assert second["up_to_date"] == 1
    assert exporter.sha256_file(output) == first_output_sha
    index_root = study / "4.临时目录" / "中间索引" / "KleiDST" / "图片补全"
    assert (index_root / "klei_dst_image_export_index.csv").is_file()
    json_payload = json.loads((index_root / "klei_dst_image_export_index.json").read_text(encoding="utf-8"))
    jsonl_rows = [json.loads(line) for line in (index_root / "klei_dst_image_export_index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert json_payload["rows"] == jsonl_rows
    assert set(("source_id", "origin_kind", "archive", "member", "source", "raw_output", "output", "sha256", "width", "height", "terrain_reason", "status")).issubset(jsonl_rows[0])


def test_rejects_unsafe_output_prefix_and_study_source_overlap(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    unsafe_manifest = write_manifest(
        tmp_path / "unsafe.json",
        [source_entry("bad", source, "../escape")],
    )
    with pytest.raises(exporter.ManifestError, match="output_prefix"):
        exporter.run_export(
            tmp_path / "study",
            unsafe_manifest,
            ROOT,
            ROOT / "references" / "klei-dst-image-export-config.json",
            False,
        )

    overlap_study = source / "study"
    overlap_manifest = write_manifest(tmp_path / "overlap.json", [source_entry("bad", source, "")])
    with pytest.raises(exporter.ManifestError, match="overlap"):
        exporter.run_export(
            overlap_study,
            overlap_manifest,
            ROOT,
            ROOT / "references" / "klei-dst-image-export-config.json",
            False,
        )


def test_skill_documents_multi_source_klei_image_export_contract() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / "references" / "klei-dst-workflow.md").read_text(encoding="utf-8")
    branch = (ROOT / "references" / "branch-completion-checks.md").read_text(encoding="utf-8")
    combined = "\n".join((skill, workflow, branch))

    for term in (
        "scripts/export_klei_dst_images.py",
        "references/klei-dst-image-export-config.json",
        "--sources-manifest",
        "output_prefix",
        "terrain_prefix",
        "terrain_category",
        "archive_chain",
        "地形贴图",
        "Pillow",
    ):
        assert term in combined

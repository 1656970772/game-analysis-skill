from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "build_unity_vfx_full_index.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_unity_vfx_full_index", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_manifest_is_not_compacted_while_cards_can_be_compacted(
    tmp_path: Path,
) -> None:
    module = load_module()
    payload = {
        "item_count": 40,
        "items": [
            {"id": index, "dependencies": [{"path_id": child} for child in range(40)]}
            for index in range(40)
        ],
    }

    manifest_path = tmp_path / "manifest.json"
    module.write_json(manifest_path, payload)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["item_count"] == 40
    assert len(manifest["items"]) == 40

    card_path = tmp_path / "card.json"
    module.write_json(card_path, payload["items"][0], compact_payload=True)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    assert len(card["dependencies"]) == 32


def test_assetripper_yaml_counter_streams_files_larger_than_25mb(
    tmp_path: Path,
) -> None:
    module = load_module()
    assets_root = tmp_path / "Assets"
    assets_root.mkdir()
    yaml_path = assets_root / "large.unity"
    with yaml_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("--- !u!198 &1\n")
        handle.write("--- !u!199 &2\n")
        handle.write("#" * 25_000_001)

    output = tmp_path / "reconciliation.csv"
    summary = module.scan_assetripper_yaml(assets_root, output)
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert summary["particle_yaml_file_count"] == 1
    assert summary["particle_system_count"] == 1
    assert summary["particle_system_renderer_count"] == 1
    assert rows[0]["particle_system_count"] == "1"
    assert rows[0]["particle_system_renderer_count"] == "1"


def test_reusable_script_contains_no_task_specific_counts_or_paths() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "T32",
        "KuaKeDownload",
        "32494",
        "32685",
        "65179",
    ):
        assert forbidden not in source


def test_full_paths_follow_project_config(tmp_path: Path) -> None:
    module = load_module()
    config = {
        "paths": {
            "unitypy_root": "0.原始导出/UnityPy/Recovered",
            "unitypy_index": "0.原始导出/UnityPy/Recovered/index.csv",
            "preview_index_dir": "4.临时目录/中间索引/VfxBranch/preview",
            "classified_vfx_dir": "1.分类资源/特效/VfxBranch",
            "full_structure_index_dir": "4.临时目录/中间索引/VfxBranch/structure",
            "assetripper_assets_root": "0.原始导出/AssetRipper/ExportedProject/Assets",
        }
    }

    paths = module.resolve_full_paths(tmp_path, config)

    assert paths["unitypy_root"] == tmp_path / "0.原始导出/UnityPy/Recovered"
    assert paths["index_path"] == tmp_path / "0.原始导出/UnityPy/Recovered/index.csv"
    assert paths["preview_dir"] == tmp_path / "4.临时目录/中间索引/VfxBranch/preview"
    assert paths["structure_dir"] == tmp_path / "4.临时目录/中间索引/VfxBranch/structure"
    assert paths["classified_root"] == tmp_path / "1.分类资源/特效/VfxBranch"
    assert paths["assets_root"] == tmp_path / "0.原始导出/AssetRipper/ExportedProject/Assets"


def test_pptr_audit_distinguishes_null_local_and_cross_bundle() -> None:
    module = load_module()
    payload = [
        {"m_FileID": 0, "m_PathID": 0},
        {"m_FileID": 0, "m_PathID": 12},
        {"m_FileID": 2, "m_PathID": 34},
    ]

    refs = module.all_pptrs(payload)

    assert refs == [
        ("0", "0", "null"),
        ("0", "12", "local"),
        ("2", "34", "cross_bundle"),
    ]


def test_writers_preserve_mtime_when_payload_is_unchanged(tmp_path: Path) -> None:
    module = load_module()
    output = tmp_path / "stable.json"
    module.write_json(output, {"ok": True})
    first_mtime = output.stat().st_mtime_ns

    module.write_json(output, {"ok": True})

    assert output.stat().st_mtime_ns == first_mtime


def test_existing_reconstruction_state_is_reused_only_for_same_structure(
    tmp_path: Path,
) -> None:
    module = load_module()
    preview = tmp_path / "preview"
    reconstructed = preview / "reconstructed" / "item.json"
    reconstructed.parent.mkdir(parents=True)
    reconstructed.write_text("{}", encoding="utf-8")
    old = {
        "items": [
            {
                "id": "same",
                "source_file": "bundle",
                "path_id": "1",
                "root_type": "ParticleSystem",
                "source_refs": [{"path_id": "1"}],
                "dependencies": {"materials": [{"path_id": "2"}]},
                "warnings": ["missing_texture"],
                "reconstruction_relative_path": "reconstructed/item.json",
                "preview_status": "partial",
                "reconstruction_status": "partial",
                "unity_export_status": "partial",
                "unity_export_path": "somewhere",
            }
        ]
    }
    module.write_json(preview / "manifest.json", old)
    new_item = {
        "id": "same",
        "source_file": "bundle",
        "path_id": "1",
        "root_type": "ParticleSystem",
        "source_refs": [{"path_id": "1"}],
        "dependencies": {"materials": [{"path_id": "2"}]},
        "warnings": ["missing_texture"],
        "reconstruction_relative_path": "reconstructed/item.json",
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }

    reused = module.merge_existing_reconstruction_state(
        preview / "manifest.json", preview, [new_item]
    )

    assert reused == 1
    assert new_item["reconstruction_status"] == "partial"
    assert new_item["unity_export_path"] == "somewhere"

    changed = dict(new_item)
    changed["dependencies"] = {"materials": []}
    changed["preview_status"] = "pending"
    changed["reconstruction_status"] = "pending"
    changed["unity_export_status"] = "pending"
    assert module.merge_existing_reconstruction_state(
        preview / "manifest.json", preview, [changed]
    ) == 0
    assert changed["reconstruction_status"] == "pending"


def test_long_texture_names_keep_image_extension_and_migrate_legacy_file(
    tmp_path: Path,
) -> None:
    module = load_module()
    unitypy = tmp_path / "unitypy"
    classified = tmp_path / "classified"
    relative = f"images/Texture2D/{'x' * 180}.png"
    source = unitypy / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    row = {
        "source_file": "bundle",
        "path_id": "1",
        "asset_type": "Texture2D",
        "asset_name": "fire",
        "container_path": "Effects/fire.png",
        "export_path": relative,
    }
    asset_id = module.stable_id("bundle", "1", "Texture2D", "fire")
    legacy = classified / "粒子贴图" / "fire" / f"{asset_id}_{module.safe_name(source.name)}"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(source.read_bytes())

    copied = module.copy_keyword_textures(
        {("bundle", "1"): row},
        unitypy,
        classified,
        {"name_keyword_rules": [{"tag": "vfx:fire", "keywords": ["fire"]}]},
    )
    target = Path(copied[0]["classified_path"])
    first_mtime = target.stat().st_mtime_ns
    module.copy_keyword_textures(
        {("bundle", "1"): row},
        unitypy,
        classified,
        {"name_keyword_rules": [{"tag": "vfx:fire", "keywords": ["fire"]}]},
    )

    assert target.suffix == ".png"
    assert target.read_bytes() == source.read_bytes()
    assert not legacy.exists()
    assert target.stat().st_mtime_ns == first_mtime

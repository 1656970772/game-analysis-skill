from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "references" / "vfx-preview-config.json"


def load_script_module(name: str) -> Any:
    script = ROOT / "scripts" / f"{name}.py"
    assert script.exists(), f"{name} script is missing"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def unitypy_root(study_root: Path) -> Path:
    return study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源"


def preview_index_dir(study_root: Path) -> Path:
    return study_root / "4.临时目录" / "中间索引" / "VFX预览"


def unity_export_asset_root(study_root: Path) -> Path:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return preview_index_dir(study_root) / "unity-export" / Path(config["unity_export"]["asset_root"])


def guid_from_meta(meta_path: Path) -> str:
    match = re.search(r"^guid: ([0-9a-f]{32})$", meta_path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"missing Unity guid in {meta_path}"
    guid = match.group(1)
    assert guid != "0" * 32
    return guid


def write_minimal_unitypy_fixture(study_root: Path) -> Path:
    root = unitypy_root(study_root)
    index = root / "all_resources_index.csv"
    rows = [
        {
            "asset_id": "ps_1",
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire_loop",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystem/fire_loop.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "renderer_1",
            "source_file": "resources.assets",
            "path_id": "101",
            "asset_type": "ParticleSystemRenderer",
            "asset_name": "fire_loop_renderer",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystemRenderer/fire_loop_renderer.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_1",
            "source_file": "resources.assets",
            "path_id": "102",
            "asset_type": "Texture2D",
            "asset_name": "fire_particle",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/fire_particle.png",
            "width": "128",
            "height": "128",
            "status": "exported",
            "remarks": "",
        },
    ]
    index.parent.mkdir(parents=True)
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    (root / "metadata" / "ParticleSystem").mkdir(parents=True)
    (root / "metadata" / "ParticleSystem" / "fire_loop.json").write_text(
        json.dumps(
            {
                "m_Name": "fire_loop",
                "m_Duration": 2.0,
                "m_Looping": True,
                "m_Texture": {"pathID": 102},
                "InitialModule": {
                    "startLifetime": {"constant": 1.25},
                    "startSpeed": {"constant": 3.0},
                    "startSize": {"constant": 0.4},
                    "maxParticles": 128,
                },
                "EmissionModule": {
                    "enabled": True,
                    "rateOverTime": {"constant": 16.0},
                },
                "ShapeModule": {
                    "enabled": True,
                    "shapeType": "Cone",
                    "angle": 20.0,
                    "radius": 0.5,
                },
                "ParticleSystemRenderer": {
                    "renderMode": "Billboard",
                    "sortingLayer": "Effects",
                    "sortingOrder": 3,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    renderer_metadata = root / "metadata" / "ParticleSystemRenderer" / "fire_loop_renderer.json"
    renderer_metadata.parent.mkdir(parents=True)
    renderer_metadata.write_text(
        json.dumps(
            {
                "m_Name": "fire_loop_renderer",
                "m_RenderMode": "Billboard",
                "m_Material": {"pathID": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    texture = root / "Texture2D" / "fire_particle.png"
    texture.parent.mkdir(parents=True)
    texture.write_bytes(b"fake-png")
    return index


def assert_outputs_stay_inside_study_contract(study_root: Path) -> None:
    allowed_top_levels = {"0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"}
    for path in study_root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(study_root)
            assert relative.parts[0] in allowed_top_levels, str(relative)


def test_unity_vfx_preview_workflow_builds_reconstruction_export_and_launcher(tmp_path: Path) -> None:
    manifest_builder = load_script_module("build_vfx_preview_manifest")
    reconstruction_exporter = load_script_module("export_unity_vfx_reconstruction")
    launcher_creator = load_script_module("create_vfx_viewer_launcher")
    study_root = tmp_path / "study"
    index = write_minimal_unitypy_fixture(study_root)

    manifest = manifest_builder.build_vfx_manifest(study_root, CONFIG, index)

    assert manifest["item_count"] == 1
    item = manifest["items"][0]
    assert item["preview_status"] == "pending"
    assert item["reconstruction_status"] == "pending"
    assert item["unity_export_status"] == "pending"
    assert item["dependencies"]["renderers"][0]["asset_type"] == "ParticleSystemRenderer"
    assert item["dependencies"]["textures"][0]["asset_type"] == "Texture2D"
    assert (study_root / "1.分类资源" / "特效" / "粒子贴图" / "fire" / "fire_particle.png").read_bytes() == b"fake-png"
    particle_card = study_root / "1.分类资源" / "特效" / "配置" / "ParticleSystem" / "fire_loop_100.json"
    renderer_card = study_root / "1.分类资源" / "特效" / "配置" / "ParticleSystemRenderer" / "fire_loop_renderer_101.json"
    assert json.loads(particle_card.read_text(encoding="utf-8"))["metadata"]["m_Texture"]["pathID"] == 102
    assert json.loads(renderer_card.read_text(encoding="utf-8"))["asset_type"] == "ParticleSystemRenderer"
    for directory_name in ["VFX材质", "VFX模型", "VFX动画"]:
        resource_dir = study_root / "1.分类资源" / "特效" / directory_name
        json_cards = list(resource_dir.rglob("*.json")) if resource_dir.exists() else []
        assert json_cards == []

    manifest_path = preview_index_dir(study_root) / "manifest.json"
    export_result = reconstruction_exporter.export_reconstruction(study_root, CONFIG, manifest_path)

    assert export_result["updated_count"] == 1
    assert export_result["failed_count"] == 0
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    updated_item = updated_manifest["items"][0]
    reconstructed_path = preview_index_dir(study_root) / updated_item["reconstruction_relative_path"]
    reconstructed = json.loads(reconstructed_path.read_text(encoding="utf-8"))
    assert reconstructed["id"] == updated_item["id"]
    assert reconstructed["modules"]["main"]["duration"] == 2.0
    assert reconstructed["modules"]["emission"]["rateOverTime"]["constant"] == 16.0
    assert reconstructed["modules"]["shape"]["shapeType"] == "Cone"
    assert reconstructed["modules"]["renderer"]["sortingLayer"] == "Effects"
    assert reconstructed["modules"]["renderer"]["textureRef"] == "Texture2D/fire_particle.png"
    assert updated_item["preview_status"] == "ready"
    assert updated_item["reconstruction_status"] == "full"
    assert updated_item["unity_export_status"] == "ready"
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected_prefab_relative = f"unity-export/{config['unity_export']['asset_root']}/Prefabs/fire_loop.prefab"
    assert updated_item["unity_export_relative_path"] == expected_prefab_relative

    export_root = unity_export_asset_root(study_root)
    prefab = export_root / "Prefabs" / "fire_loop.prefab"
    material = export_root / "Materials" / f"{updated_item['id']}.mat"
    metadata = export_root / "Metadata" / f"{updated_item['id']}.json"
    texture = export_root / "Textures" / "fire_particle.png"
    bootstrap = export_root / "Scripts" / "GameAnalysisVfxBootstrap.cs"
    prefab_meta = prefab.with_suffix(prefab.suffix + ".meta")
    material_meta = material.with_suffix(material.suffix + ".meta")
    metadata_meta = metadata.with_suffix(metadata.suffix + ".meta")
    texture_meta = texture.with_suffix(texture.suffix + ".meta")
    bootstrap_meta = bootstrap.with_suffix(bootstrap.suffix + ".meta")
    for path in [prefab, material, metadata, texture, bootstrap, prefab_meta, material_meta, metadata_meta, texture_meta, bootstrap_meta]:
        assert path.is_file()
    assert texture.read_bytes() == b"fake-png"
    material_guid = guid_from_meta(material_meta)
    metadata_guid = guid_from_meta(metadata_meta)
    texture_guid = guid_from_meta(texture_meta)
    bootstrap_guid = guid_from_meta(bootstrap_meta)
    material_text = material.read_text(encoding="utf-8")
    assert f"m_Texture: {{fileID: 2800000, guid: {texture_guid}, type: 3}}" in material_text
    prefab_text = prefab.read_text(encoding="utf-8")
    assert f"m_Script: {{fileID: 11500000, guid: {bootstrap_guid}, type: 3}}" in prefab_text
    assert f"- {{fileID: 2100000, guid: {material_guid}, type: 2}}" in prefab_text
    assert f"material: {{fileID: 2100000, guid: {material_guid}, type: 2}}" in prefab_text
    assert f"metadata: {{fileID: 4900000, guid: {metadata_guid}, type: 3}}" in prefab_text
    assert "m_Script: {fileID: 0}" not in prefab_text
    assert "material: {fileID: 0}" not in prefab_text
    assert "metadata: {fileID: 0}" not in prefab_text
    metadata_json = json.loads(metadata.read_text(encoding="utf-8"))
    assert metadata_json == reconstructed
    assert metadata_json["id"] == reconstructed["id"] == updated_item["id"]

    launcher = launcher_creator.create_launcher(study_root, ROOT, CONFIG)

    assert launcher == study_root / "2.报告" / "打开特效预览.bat"
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "--manifest" in launcher_text
    assert "--reconstructed-dir" in launcher_text
    assert "--preview-cache-dir" in launcher_text
    assert "--unity-export-dir" in launcher_text
    assert "--errors" not in launcher_text
    assert (preview_index_dir(study_root) / "preview-cache").is_dir()
    assert_outputs_stay_inside_study_contract(study_root)

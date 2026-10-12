from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_unity_vfx_reconstruction.py"
CONFIG = ROOT / "references" / "vfx-preview-config.json"
SCHEMA = ROOT / "references" / "vfx-unity-reconstruction-schema.json"


def load_module():
    assert SCRIPT.exists(), "Unity VFX reconstruction export script is missing"
    spec = importlib.util.spec_from_file_location("export_unity_vfx_reconstruction", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def preview_index_dir(study_root: Path) -> Path:
    return study_root / "4.临时目录" / "中间索引" / "VFX预览"


def unitypy_root(study_root: Path) -> Path:
    return study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源"


def unity_export_asset_root(study_root: Path) -> Path:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return preview_index_dir(study_root) / "unity-export" / Path(config["unity_export"]["asset_root"])


def validate_json_schema_subset(instance: object, schema: dict[str, object]) -> None:
    def resolve_ref(ref: str) -> dict[str, object]:
        current: object = schema
        for part in ref.lstrip("#/").split("/"):
            assert isinstance(current, dict), ref
            current = current[part]
        assert isinstance(current, dict), ref
        return current

    def validate(value: object, node: dict[str, object], path: str) -> None:
        if "$ref" in node:
            validate(value, resolve_ref(str(node["$ref"])), path)
        for child in node.get("allOf", []):
            assert isinstance(child, dict)
            validate(value, child, path)
        if "const" in node:
            assert value == node["const"], path
        if "enum" in node:
            assert value in node["enum"], path
        expected_type = node.get("type")
        if expected_type == "object":
            assert isinstance(value, dict), path
            required = node.get("required", [])
            assert isinstance(required, list)
            for key in required:
                assert key in value, f"{path}.{key}"
            properties = node.get("properties", {})
            assert isinstance(properties, dict)
            if node.get("additionalProperties") is False:
                assert set(value).issubset(properties), path
            for key, child in properties.items():
                if key in value:
                    assert isinstance(child, dict)
                    validate(value[key], child, f"{path}.{key}")
        elif expected_type == "array":
            assert isinstance(value, list), path
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    validate(item, item_schema, f"{path}[{index}]")
        elif expected_type == "string":
            assert isinstance(value, str), path
        elif expected_type == "integer":
            assert type(value) is int, path
        elif expected_type == "number":
            assert isinstance(value, (int, float)) and not isinstance(value, bool), path
        elif expected_type == "boolean":
            assert type(value) is bool, path

    validate(instance, schema, "$")


def write_particle_metadata(study_root: Path, export_path: str = "metadata/ParticleSystem/fire_loop.json") -> None:
    metadata_path = unitypy_root(study_root) / export_path
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "m_Name": "fire_loop",
                "m_Duration": 2.0,
                "m_Looping": True,
                "InitialModule": {
                    "startLifetime": {"constant": 1.25},
                    "startSpeed": {"constant": 3.0},
                    "startSize": {"constant": 0.4},
                    "startColor": {"color": [1.0, 0.4, 0.1, 1.0]},
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
        encoding="utf-8-sig",
    )


def write_texture_fixture(study_root: Path, relative_path: str = "Texture2D/fire_particle.png") -> Path:
    texture_path = unitypy_root(study_root) / relative_path
    texture_path.parent.mkdir(parents=True, exist_ok=True)
    texture_path.write_bytes(b"fake-png")
    return texture_path


def write_manifest(study_root: Path, *, export_path: str = "metadata/ParticleSystem/fire_loop.json") -> Path:
    index_dir = preview_index_dir(study_root)
    index = unitypy_root(study_root) / "all_resources_index.csv"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("source_file,path_id,asset_type,asset_name,export_path\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "engine": "unity",
        "study_root": str(study_root.resolve(strict=False)),
        "source_index": str(index.resolve(strict=False)),
        "item_count": 1,
        "items": [
            {
                "schema_version": 1,
                "id": "fire-loop",
                "engine": "unity",
                "name": "fire_loop",
                "kind": "particle_system",
                "root_type": "ParticleSystem",
                "source_file": "resources.assets",
                "path_id": "100",
                "asset_type": "ParticleSystem",
                "preview_status": "pending",
                "reconstruction_status": "pending",
                "unity_export_status": "pending",
                "reconstruction_relative_path": "reconstructed/fire_loop-100.json",
                "unity_export_path": "",
                "source_refs": [
                    {
                        "asset_type": "ParticleSystem",
                        "asset_name": "fire_loop",
                        "source_file": "resources.assets",
                        "path_id": "100",
                        "export_path": export_path,
                    }
                ],
                "dependencies": {
                    "renderers": [
                        {
                            "id": "101",
                            "type": "ParticleSystemRenderer",
                            "name": "fire_loop_renderer",
                            "path_id": "101",
                            "asset_type": "ParticleSystemRenderer",
                            "asset_name": "fire_loop_renderer",
                            "relative_path": "metadata/ParticleSystemRenderer/fire_loop_renderer.json",
                        }
                    ],
                    "materials": [
                        {
                            "id": "102",
                            "type": "Material",
                            "name": "fire_loop_mat",
                            "path_id": "102",
                            "asset_type": "Material",
                            "asset_name": "fire_loop_mat",
                            "relative_path": "metadata/Material/fire_loop_mat.json",
                        }
                    ],
                    "textures": [
                        {
                            "id": "103",
                            "type": "Texture2D",
                            "name": "fire_particle",
                            "path_id": "103",
                            "asset_type": "Texture2D",
                            "asset_name": "fire_particle",
                            "relative_path": "Texture2D/fire_particle.png",
                        }
                    ],
                    "meshes": [],
                    "animations": [],
                },
                "supported_modules": ["main", "emission", "shape", "renderer"],
                "unsupported_modules": [],
                "warnings": [],
            }
        ],
    }
    manifest_path = index_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def write_assetripper_vfx_prefab_fixture(study_root: Path) -> None:
    asset_root = study_root / "0.原始导出" / "AssetRipper" / "ExportedProject" / "Assets"
    prefab = asset_root / "Resources" / "effect" / "fire.prefab"
    material = asset_root / "Materials" / "fire.mat"
    texture = asset_root / "Textures" / "fire.png"
    prefab.parent.mkdir(parents=True, exist_ok=True)
    material.parent.mkdir(parents=True, exist_ok=True)
    texture.parent.mkdir(parents=True, exist_ok=True)
    material_guid = "11111111111111111111111111111111"
    texture_guid = "22222222222222222222222222222222"
    prefab.write_text(
        "%YAML 1.1\n"
        "--- !u!1 &100000\n"
        "GameObject:\n"
        "  m_Name: fire\n"
        "  m_Component:\n"
        "  - component: {fileID: 19800000}\n"
        "  - component: {fileID: 19900000}\n"
        "--- !u!198 &19800000\n"
        "ParticleSystem:\n"
        "  m_GameObject: {fileID: 100000}\n"
        "  lengthInSec: 5\n"
        "  looping: 1\n"
        "  InitialModule:\n"
        "    enabled: 1\n"
        "    startLifetime:\n"
        "      scalar: 1\n"
        "    startSpeed:\n"
        "      scalar: 0\n"
        "    startSize:\n"
        "      scalar: 0.67\n"
        "  EmissionModule:\n"
        "    enabled: 1\n"
        "    rateOverTime:\n"
        "      scalar: 10\n"
        "  ShapeModule:\n"
        "    enabled: 1\n"
        "    type: 4\n"
        "    angle: 25\n"
        "    radius: 1\n"
        "--- !u!199 &19900000\n"
        "ParticleSystemRenderer:\n"
        "  m_GameObject: {fileID: 100000}\n"
        f"  m_Materials:\n  - {{fileID: 2100000, guid: {material_guid}, type: 2}}\n",
        encoding="utf-8",
    )
    prefab.with_suffix(".prefab.meta").write_text(
        "fileFormatVersion: 2\n"
        "guid: 33333333333333333333333333333333\n",
        encoding="utf-8",
    )
    material.write_text(
        "%YAML 1.1\n"
        "--- !u!21 &2100000\n"
        "Material:\n"
        "  m_Name: fire\n"
        "  m_SavedProperties:\n"
        f"    m_TexEnvs:\n    - _MainTex:\n        m_Texture: {{fileID: 2800000, guid: {texture_guid}, type: 3}}\n",
        encoding="utf-8",
    )
    material.with_suffix(".mat.meta").write_text(
        "fileFormatVersion: 2\n"
        f"guid: {material_guid}\n",
        encoding="utf-8",
    )
    texture.write_bytes(b"real-png")
    texture.with_suffix(".png.meta").write_text(
        "fileFormatVersion: 2\n"
        f"guid: {texture_guid}\n",
        encoding="utf-8",
    )
    index = study_root / "4.临时目录" / "中间索引" / "AssetRipper分析" / "Prefab索引.csv"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        "relative_path,group,file_size,yaml_object_count,gameobject_count,transform_count,recttransform_count,"
        "spriterenderer_count,particle_count,animator_count,canvas_count,camera_count,collider2d_count,"
        "monobehaviour_count,tilemap_keyword_count,grid_keyword_count,sprite_guid_ref_count,"
        "cinemachine_keyword_count,timeline_keyword_count,root_names,sample_names,scene_building_tags\n"
        "ExportedProject/Assets/Resources/effect/fire.prefab,effect,100,2,1,1,0,0,2,0,0,0,0,0,0,0,0,0,0,fire,fire | flame,粒子特效\n",
        encoding="utf-8",
    )


def sys_path_contains(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    for entry in sys.path:
        if not entry:
            continue
        try:
            if Path(entry).resolve(strict=False) == resolved:
                return True
        except OSError:
            continue
    return False


def test_export_reconstruction_writes_schema_json_unity_export_and_ready_manifest(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    write_texture_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["updated_count"] == 1
    assert result["failed_count"] == 0
    reconstructed_path = preview_index_dir(tmp_path) / "reconstructed" / "fire_loop-100.json"
    payload = json.loads(reconstructed_path.read_text(encoding="utf-8"))
    assert payload["engine"] == "unity"
    assert payload["modules"]["main"]["duration"] == 2.0
    assert payload["modules"]["main"]["looping"] is True
    assert payload["modules"]["emission"]["rateOverTime"]["constant"] == 16.0
    assert payload["modules"]["shape"]["shapeType"] == "Cone"
    assert payload["modules"]["renderer"]["renderMode"] == "Billboard"
    assert payload["modules"]["main"]["startColor"]["color"] == "rgba(1, 0.4, 0.1, 1)"
    validate_json_schema_subset(
        payload,
        json.loads(SCHEMA.read_text(encoding="utf-8")),
    )
    assert "main" not in payload
    export_root = unity_export_asset_root(tmp_path)
    prefab = export_root / "Prefabs" / "fire_loop.prefab"
    metadata = export_root / "Metadata" / "fire-loop.json"
    bootstrap = export_root / "Scripts" / "GameAnalysisVfxBootstrap.cs"
    prefab_meta = export_root / "Prefabs" / "fire_loop.prefab.meta"
    bootstrap_meta = export_root / "Scripts" / "GameAnalysisVfxBootstrap.cs.meta"
    metadata_meta = export_root / "Metadata" / "fire-loop.json.meta"
    assert prefab.is_file()
    assert prefab_meta.is_file()
    assert metadata.is_file()
    assert bootstrap.is_file()
    assert bootstrap_meta.is_file()
    assert metadata_meta.is_file()
    material = export_root / "Materials" / "fire-loop.mat"
    material_meta = export_root / "Materials" / "fire-loop.mat.meta"
    texture = export_root / "Textures" / "fire_particle.png"
    texture_meta = export_root / "Textures" / "fire_particle.png.meta"
    assert material.is_file()
    assert material_meta.is_file()
    assert texture.is_file()
    assert texture.read_bytes() == b"fake-png"
    assert texture_meta.is_file()
    assert json.loads(metadata.read_text(encoding="utf-8")) == payload
    prefab_text = prefab.read_text(encoding="utf-8")
    assert "GameAnalysisVfxBootstrap" in prefab_text
    assert "00000000000000000000000000000000" not in prefab_text
    assert "material: {fileID: 0}" not in prefab_text
    assert re.search(r"material: \{fileID: 2100000, guid: [0-9a-f]{32}, type: 2\}", prefab_text)
    assert "materialAssetPath" in prefab_text
    assert 'textureAssetPath: "Assets/GameAnalysisVfxPreview/Textures/fire_particle.png"' in prefab_text
    assert 'sourceTextureRelativePath: "Texture2D/fire_particle.png"' in prefab_text
    assert "Texture2D/fire_particle.png" in metadata.read_text(encoding="utf-8")
    texture_guid = re.search(r"guid: ([0-9a-f]{32})", texture_meta.read_text(encoding="utf-8")).group(1)
    material_text = material.read_text(encoding="utf-8")
    assert "Texture2D/fire_particle.png" in material_text
    assert f"m_Texture: {{fileID: 2800000, guid: {texture_guid}, type: 3}}" in material_text
    script_guid = re.search(r"guid: ([0-9a-f]{32})", bootstrap_meta.read_text(encoding="utf-8")).group(1)
    assert script_guid in prefab_text
    bootstrap_text = bootstrap.read_text(encoding="utf-8")
    assert "JsonUtility.FromJson" in bootstrap_text
    assert "emission.rateOverTime" in bootstrap_text
    assert "ParticleSystemRenderer" in bootstrap_text
    assert "ApplyShape" in bootstrap_text
    assert "ps.shape" in bootstrap_text
    assert "angle" in bootstrap_text
    assert "radius" in bootstrap_text
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    item = updated_manifest["items"][0]
    assert item["preview_status"] == "ready"
    assert item["reconstruction_status"] == "full"
    assert item["unity_export_status"] == "ready"
    config_asset_root = json.loads(CONFIG.read_text(encoding="utf-8"))["unity_export"]["asset_root"]
    assert item["unity_export_relative_path"] == f"unity-export/{config_asset_root}/Prefabs/fire_loop.prefab"
    assert Path(item["unity_export_path"]).resolve(strict=False) == prefab.resolve(strict=False)


def test_export_reconstruction_creates_report_launcher_bat(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    write_texture_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    launcher = tmp_path / "2.报告" / "打开特效预览.bat"
    assert result["launcher_path"] == str(launcher.resolve(strict=False))
    assert launcher.is_file()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "--manifest" in launcher_text
    assert "--reconstructed-dir" in launcher_text
    assert "--preview-cache-dir" in launcher_text
    assert "--unity-export-dir" in launcher_text


def test_export_reconstruction_copies_real_assetripper_vfx_prefabs_and_dependencies(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    write_texture_fixture(tmp_path)
    write_assetripper_vfx_prefab_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["original_vfx_prefab_count"] == 1
    assert result["original_vfx_dependency_count"] == 2
    export_root = unity_export_asset_root(tmp_path)
    original_root = export_root / "OriginalAssets"
    copied_prefab = original_root / "Resources" / "effect" / "fire.prefab"
    copied_material = original_root / "Materials" / "fire.mat"
    copied_texture = original_root / "Textures" / "fire.png"
    copied_index = export_root / "Metadata" / "original-vfx-prefabs.json"
    copied_preview = export_root / "Metadata" / "OriginalPrefabPreviews" / "Resources" / "effect" / "fire.json"
    assert copied_prefab.read_text(encoding="utf-8") == (
        tmp_path / "0.原始导出" / "AssetRipper" / "ExportedProject" / "Assets" / "Resources" / "effect" / "fire.prefab"
    ).read_text(encoding="utf-8")
    assert copied_prefab.with_suffix(".prefab.meta").is_file()
    assert copied_material.is_file()
    assert copied_material.with_suffix(".mat.meta").is_file()
    assert copied_texture.read_bytes() == b"real-png"
    assert copied_texture.with_suffix(".png.meta").is_file()
    original_index = json.loads(copied_index.read_text(encoding="utf-8"))
    assert original_index["source"] == "AssetRipper Prefab索引.csv"
    assert original_index["prefab_count"] == 1
    assert original_index["dependency_count"] == 2
    assert original_index["prefabs"][0]["source_relative_path"] == "ExportedProject/Assets/Resources/effect/fire.prefab"
    assert original_index["prefabs"][0]["unity_asset_path"] == "Assets/GameAnalysisVfxPreview/OriginalAssets/Resources/effect/fire.prefab"
    assert original_index["prefabs"][0]["preview_relative_path"] == (
        "Assets/GameAnalysisVfxPreview/Metadata/OriginalPrefabPreviews/Resources/effect/fire.json"
    )
    preview = json.loads(copied_preview.read_text(encoding="utf-8"))
    assert preview["kind"] == "original_prefab_preview"
    assert preview["name"] == "fire"
    assert preview["particle_system_count"] == 1
    assert preview["particle_systems"][0]["game_object_name"] == "fire"
    assert preview["particle_systems"][0]["main"]["duration"] == 5.0
    assert preview["particle_systems"][0]["main"]["looping"] is True
    assert preview["particle_systems"][0]["main"]["startLifetime"]["constant"] == 1.0
    assert preview["particle_systems"][0]["main"]["startSpeed"]["constant"] == 0.0
    assert preview["particle_systems"][0]["main"]["startSize"]["constant"] == 0.67
    assert preview["particle_systems"][0]["emission"]["rateOverTime"]["constant"] == 10.0
    assert preview["particle_systems"][0]["shape"]["shapeType"] == "Cone"
    assert preview["particle_systems"][0]["shape"]["angle"] == 25.0
    assert preview["particle_systems"][0]["renderer"]["materialGuids"] == ["11111111111111111111111111111111"]


def test_unity_guids_in_asset_streams_files_larger_than_25mb(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "large-scene.unity"
    first_guid = "11111111111111111111111111111111"
    second_guid = "22222222222222222222222222222222"
    with source.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"m_First: {{fileID: 1, guid: {first_guid}, type: 2}}\n")
        handle.seek(25_000_128)
        handle.write(f"m_Second: {{fileID: 2, guid: {second_guid}, type: 3}}\n")

    assert source.stat().st_size > 25_000_000
    assert module._unity_guids_in_asset(source) == {first_guid, second_guid}


def test_export_reconstruction_uses_configured_assetripper_prefab_sources(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    write_texture_fixture(tmp_path)
    write_assetripper_vfx_prefab_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)

    default_index = tmp_path / "4.临时目录" / "中间索引" / "AssetRipper分析" / "Prefab索引.csv"
    configured_index = tmp_path / "4.临时目录" / "中间索引" / "解密AssetRipper分析" / "Prefab索引.csv"
    configured_index.parent.mkdir(parents=True, exist_ok=True)
    default_index.replace(configured_index)

    default_assets = tmp_path / "0.原始导出" / "AssetRipper" / "ExportedProject" / "Assets"
    configured_export_root = tmp_path / "0.原始导出" / "AssetRipper解密资源" / "Project"
    configured_assets = configured_export_root / "ExportedProject" / "Assets"
    configured_assets.parent.mkdir(parents=True, exist_ok=True)
    default_assets.replace(configured_assets)

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["paths"]["assetripper_prefab_index"] = (
        "4.临时目录/中间索引/解密AssetRipper分析/Prefab索引.csv"
    )
    config["paths"]["assetripper_export_root"] = (
        "0.原始导出/AssetRipper解密资源/Project"
    )
    configured_path = tmp_path / "configured-vfx.json"
    configured_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    result = module.export_reconstruction(tmp_path, configured_path, manifest_path)

    assert result["original_vfx_prefab_count"] == 1
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_export = updated_manifest["original_vfx_prefab_export"]
    assert Path(original_export["source_index"]).resolve(strict=False) == configured_index.resolve(strict=False)
    assert Path(original_export["source_assets_root"]).resolve(strict=False) == configured_assets.resolve(strict=False)


def test_original_prefabs_only_preserves_existing_vfx_items(tmp_path: Path) -> None:
    module = load_module()
    write_assetripper_vfx_prefab_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)
    before = json.loads(manifest_path.read_text(encoding="utf-8"))["items"]

    result = module.export_original_prefabs_only(tmp_path, CONFIG, manifest_path)

    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result["original_prefabs_only"] is True
    assert result["updated_count"] == 0
    assert result["original_vfx_prefab_count"] == 1
    assert after["items"] == before
    assert after["original_vfx_prefab_export"]["prefab_count"] == 1


def test_cli_can_run_script_directly_and_export_reconstruction(tmp_path: Path) -> None:
    write_particle_metadata(tmp_path)
    write_texture_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(tmp_path),
            "--config",
            str(CONFIG),
            "--manifest",
            str(manifest_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["updated_count"] == 1
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated_manifest["items"][0]["unity_export_status"] == "ready"


def test_cli_original_prefabs_only_preserves_existing_vfx_items(tmp_path: Path) -> None:
    write_assetripper_vfx_prefab_fixture(tmp_path)
    manifest_path = write_manifest(tmp_path)
    before = json.loads(manifest_path.read_text(encoding="utf-8"))["items"]

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(tmp_path),
            "--config",
            str(CONFIG),
            "--manifest",
            str(manifest_path),
            "--original-prefabs-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["original_prefabs_only"] is True
    assert payload["updated_count"] == 0
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["items"] == before


def test_metadata_path_traversal_is_rejected_without_ready_status(tmp_path: Path) -> None:
    module = load_module()
    outside = tmp_path / "outside.json"
    outside.write_text('{"m_Duration": 99.0}', encoding="utf-8")
    manifest_path = write_manifest(tmp_path, export_path="../outside.json")

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["updated_count"] == 0
    assert result["failed_count"] == 1
    assert not (preview_index_dir(tmp_path) / "reconstructed" / "fire_loop-100.json").exists()
    item = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
    assert item["preview_status"] == "failed"
    assert item["reconstruction_status"] == "failed"
    assert item["unity_export_status"] == "failed"
    assert item["unity_export_relative_path"] == ""


def test_export_project_fragment_without_required_modules_is_partial(tmp_path: Path) -> None:
    from scripts.vfx_adapters.unity import UnityVfxAdapter
    from scripts.vfx_preview_common import load_vfx_config

    adapter = UnityVfxAdapter(load_vfx_config(CONFIG))
    reconstruction = {
        "schema_version": 1,
        "engine": "unity",
        "id": "missing-shape",
        "name": "missing_shape",
        "kind": "particle_system",
        "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
        "root_type": "ParticleSystem",
        "modules": {
            "main": {"duration": 1.0},
            "emission": {"rateOverTime": {"constant": 8.0}},
            "renderer": {"renderMode": "Billboard"},
            "raw_unity": {},
        },
        "resources": {"textures": [], "materials": [], "meshes": [], "animations": []},
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }

    result = adapter.export_project_fragment(
        {"id": "missing-shape", "name": "missing_shape"},
        reconstruction,
        tmp_path / "unity-export",
    )

    assert result["ready"] is False
    assert result["unity_export_status"] == "partial"
    assert result["reconstruction_status"] == "partial"
    assert "missing required reconstruction modules" in result["reason"]


def test_export_reconstruction_with_texture_dependency_without_source_file_is_partial(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    manifest_path = write_manifest(tmp_path)

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["updated_count"] == 1
    assert result["failed_count"] == 0
    item = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
    assert item["unity_export_status"] == "partial"
    assert "Texture2D dependency is not copied or bound" in item["unity_export_reason"]
    export_root = unity_export_asset_root(tmp_path)
    material = export_root / "Materials" / "fire-loop.mat"
    assert "m_Texture: {fileID: 0}" in material.read_text(encoding="utf-8")
    assert not (export_root / "Textures" / "fire_particle.png").exists()


def test_export_reconstruction_marks_trail_and_line_roots_partial_not_ready(tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    manifest_path = write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest["items"][0]
    manifest["items"] = []
    for root_type, kind, path_id in [
        ("TrailRenderer", "trail_renderer", "200"),
        ("LineRenderer", "line_renderer", "300"),
    ]:
        item = json.loads(json.dumps(base))
        item["id"] = root_type.lower()
        item["name"] = root_type
        item["kind"] = kind
        item["root_type"] = root_type
        item["asset_type"] = root_type
        item["path_id"] = path_id
        item["source_refs"][0]["asset_type"] = root_type
        item["source_refs"][0]["path_id"] = path_id
        item["source_refs"][0]["asset_name"] = root_type
        item["source_refs"][0]["export_path"] = "metadata/ParticleSystem/fire_loop.json"
        item["dependencies"]["textures"] = []
        manifest["items"].append(item)
    manifest["item_count"] = 2
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["updated_count"] == 2
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in updated_manifest["items"]:
        assert item["root_type"] in {"TrailRenderer", "LineRenderer"}
        assert item["unity_export_status"] == "partial"
        assert item["unity_export_status"] != "ready"
        assert "ParticleSystem only" in item["unity_export_reason"]
        reconstructed = json.loads((preview_index_dir(tmp_path) / item["reconstruction_relative_path"]).read_text(encoding="utf-8"))
        assert reconstructed["unity_export_status"] == "partial"
        assert any("ParticleSystem only" in warning for warning in reconstructed["warnings"])


def test_export_project_fragment_quotes_yaml_strings_for_special_names(tmp_path: Path) -> None:
    from scripts.vfx_adapters.unity import UnityVfxAdapter
    from scripts.vfx_preview_common import load_vfx_config

    adapter = UnityVfxAdapter(load_vfx_config(CONFIG))
    reconstruction = {
        "schema_version": 1,
        "engine": "unity",
        "id": "spark#![id]",
        "name": "spark#![burst]",
        "kind": "particle_system",
        "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
        "root_type": "ParticleSystem",
        "modules": {
            "main": {"duration": 1.0},
            "emission": {"enabled": True, "rateOverTime": {"constant": 8.0}},
            "shape": {"enabled": True, "shapeType": "Cone", "angle": 12.0, "radius": 0.25},
            "renderer": {"renderMode": "Billboard", "sortingLayer": "FX#[front]"},
            "raw_unity": {},
        },
        "resources": {"textures": [], "materials": [], "meshes": [], "animations": []},
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }

    result = adapter.export_project_fragment(
        {"id": "spark#![id]", "name": "spark#![burst]"},
        reconstruction,
        tmp_path / "unity-export",
    )

    prefab = Path(result["unity_export_path"])
    text = prefab.read_text(encoding="utf-8")
    assert 'm_Name: "spark#![burst]"' in text
    config_asset_root = json.loads(CONFIG.read_text(encoding="utf-8"))["unity_export"]["asset_root"]
    assert f'metadataAssetPath: "{config_asset_root}/Metadata/spark#![id].json"' in text
    assert 'm_SortingLayer: "FX#[front]"' in text


def test_cleanup_export_fragment_refuses_to_delete_outside_export_root(tmp_path: Path) -> None:
    from scripts.vfx_adapters import unity

    export_root = tmp_path / "unity-export"
    export_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    unity._cleanup_export_fragment(export_root, outside)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_export_project_fragment_rejects_resolved_target_outside_export_root(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from scripts.vfx_adapters.unity import UnityVfxAdapter
    from scripts.vfx_preview_common import load_vfx_config

    adapter = UnityVfxAdapter(load_vfx_config(CONFIG))
    reconstruction = {
        "schema_version": 1,
        "engine": "unity",
        "id": "escape",
        "name": "escape",
        "kind": "particle_system",
        "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
        "root_type": "ParticleSystem",
        "modules": {
            "main": {"duration": 1.0},
            "emission": {"enabled": True, "rateOverTime": {"constant": 8.0}},
            "shape": {"enabled": True, "shapeType": "Cone", "angle": 12.0, "radius": 0.25},
            "renderer": {"renderMode": "Billboard"},
            "raw_unity": {},
        },
        "resources": {"textures": [], "materials": [], "meshes": [], "animations": []},
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }
    original_resolve = Path.resolve

    def resolve_outside_for_material(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path.name == "escape.mat":
            return tmp_path / "outside" / "escape.mat"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside_for_material)

    result = adapter.export_project_fragment(
        {"id": "escape", "name": "escape"},
        reconstruction,
        tmp_path / "unity-export",
    )

    assert result["status"] == "failed"
    assert result["unity_export_status"] == "failed"
    assert "outside Unity export root" in result["reason"]


def test_numeric_unity_enums_are_normalized_to_schema_strings(tmp_path: Path) -> None:
    from scripts.vfx_adapters.unity import UnityVfxAdapter
    from scripts.vfx_preview_common import load_vfx_config

    adapter = UnityVfxAdapter(load_vfx_config(CONFIG))
    item = {
        "id": "numeric-enums",
        "engine": "unity",
        "name": "numeric_enums",
        "kind": "particle_system",
        "root_type": "ParticleSystem",
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
        "source_refs": [
            {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"}
        ],
        "dependencies": {"textures": [], "materials": [], "meshes": [], "animations": []},
    }

    reconstruction = adapter.build_reconstruction(
        item,
        {
            "ShapeModule": {
                "enabled": True,
                "m_Type": 2,
                "m_Angle": 30.0,
                "m_Radius": 0.5,
            },
            "ParticleSystemRenderer": {"m_RenderMode": 4},
        },
    )

    assert reconstruction["modules"]["shape"]["shapeType"] == "Cone"
    assert reconstruction["modules"]["renderer"]["renderMode"] == "Mesh"
    assert isinstance(reconstruction["modules"]["shape"]["shapeType"], str)
    assert isinstance(reconstruction["modules"]["renderer"]["renderMode"], str)
    validate_json_schema_subset(
        reconstruction,
        json.loads(SCHEMA.read_text(encoding="utf-8")),
    )


def test_export_project_fragment_write_failure_does_not_leave_ready_metadata(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from scripts.vfx_adapters.unity import UnityVfxAdapter
    from scripts.vfx_preview_common import load_vfx_config

    adapter = UnityVfxAdapter(load_vfx_config(CONFIG))
    reconstruction = {
        "schema_version": 1,
        "engine": "unity",
        "id": "write-fail",
        "name": "write_fail",
        "kind": "particle_system",
        "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
        "root_type": "ParticleSystem",
        "modules": {
            "main": {"duration": 1.0},
            "emission": {"enabled": True, "rateOverTime": {"constant": 8.0}},
            "shape": {"enabled": True, "shapeType": "Cone", "angle": 12.0, "radius": 0.25},
            "renderer": {"renderMode": "Billboard"},
            "raw_unity": {},
        },
        "resources": {"textures": [], "materials": [], "meshes": [], "animations": []},
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
    }
    original_write_text = Path.write_text

    def fail_prefab_write(path: Path, *args: Any, **kwargs: Any) -> int:
        if path.suffix == ".prefab":
            raise OSError("simulated prefab write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_prefab_write)

    export_root = preview_index_dir(tmp_path) / "unity-export"
    result = adapter.export_project_fragment(
        {"id": "write-fail", "name": "write_fail"},
        reconstruction,
        export_root,
    )

    metadata = export_root / Path(json.loads(CONFIG.read_text(encoding="utf-8"))["unity_export"]["asset_root"]) / "Metadata" / "write-fail.json"
    assert result["status"] == "failed"
    assert result["unity_export_status"] == "failed"
    assert not metadata.exists() or json.loads(metadata.read_text(encoding="utf-8"))["unity_export_status"] != "ready"


def test_export_reconstruction_counts_unity_export_failed_result(monkeypatch: Any, tmp_path: Path) -> None:
    module = load_module()
    write_particle_metadata(tmp_path)
    manifest_path = write_manifest(tmp_path)

    class FailedExportAdapter:
        def build_reconstruction(self, item: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "engine": "unity",
                "id": item["id"],
                "name": item["name"],
                "kind": item["kind"],
                "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
                "root_type": item["root_type"],
                "modules": {
                    "main": {"duration": 1.0},
                    "emission": {"enabled": True},
                    "shape": {"enabled": True},
                    "renderer": {"renderMode": "Billboard"},
                    "raw_unity": metadata,
                },
                "resources": {"textures": [], "materials": [], "meshes": [], "animations": []},
                "preview_status": "pending",
                "reconstruction_status": "pending",
                "unity_export_status": "pending",
            }

        def export_project_fragment(
            self,
            item: dict[str, Any],
            reconstruction: dict[str, Any],
            export_root: Path,
        ) -> dict[str, Any]:
            return {
                "status": "failed",
                "viewer_runtime_supported": False,
                "reconstruction_status": "full",
                "unity_export_status": "failed",
                "unity_export_relative_path": "",
                "unity_export_path": "",
                "reason": "simulated export failure",
            }

    monkeypatch.setattr(module, "get_adapter", lambda engine, config: FailedExportAdapter())

    result = module.export_reconstruction(tmp_path, CONFIG, manifest_path)

    assert result["updated_count"] == 1
    assert result["failed_count"] == 1
    item = json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]
    assert item["preview_status"] == "partial"
    assert item["reconstruction_status"] == "full"
    assert item["unity_export_status"] == "failed"
    assert item["unity_export_reason"] == "simulated export failure"


def test_importlib_loading_ignores_shadow_scripts_package_and_exports(tmp_path: Path) -> None:
    shadow_scripts = tmp_path / "scripts"
    shadow_adapters = shadow_scripts / "vfx_adapters"
    shadow_adapters.mkdir(parents=True)
    (shadow_scripts / "__init__.py").write_text("", encoding="utf-8")
    (shadow_scripts / "vfx_preview_common.py").write_text(
        "def load_vfx_config(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n",
        encoding="utf-8",
    )
    (shadow_adapters / "__init__.py").write_text(
        "def get_adapter(*args, **kwargs):\n"
        "    raise AssertionError('shadow adapter imported')\n",
        encoding="utf-8",
    )
    study_root = tmp_path / "study"
    write_particle_metadata(study_root)
    write_texture_fixture(study_root)
    manifest_path = write_manifest(study_root)
    original_path = sys.path[:]
    original_modules = {
        name: sys.modules.get(name)
        for name in [
            "scripts",
            "scripts.vfx_adapters",
            "scripts.vfx_adapters.unity",
            "scripts.vfx_preview_common",
        ]
    }
    try:
        sys.path[:] = [str(tmp_path)] + [
            entry
            for entry in original_path
            if not entry or Path(entry).resolve(strict=False) != ROOT.resolve(strict=False)
        ]
        for name in original_modules:
            sys.modules.pop(name, None)

        module = load_module()
        result = module.export_reconstruction(study_root, CONFIG, manifest_path)

        assert result["updated_count"] == 1
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["items"][0]["unity_export_status"] == "ready"
        assert Path(module.load_vfx_config.__code__.co_filename).resolve(strict=False).is_relative_to(ROOT)
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_importlib_loading_ignores_preloaded_fake_adapter_module(tmp_path: Path) -> None:
    study_root = tmp_path / "study"
    write_particle_metadata(study_root)
    write_texture_fixture(study_root)
    manifest_path = write_manifest(study_root)
    fake_adapters = types.ModuleType("scripts.vfx_adapters")
    fake_adapters.__file__ = str(tmp_path / "outside" / "__init__.py")

    def fake_get_adapter(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("fake adapter imported")

    fake_adapters.get_adapter = fake_get_adapter
    original_modules = {
        name: sys.modules.get(name)
        for name in [
            "scripts",
            "scripts.vfx_adapters",
            "scripts.vfx_adapters.unity",
            "scripts.vfx_preview_common",
        ]
    }
    try:
        sys.modules["scripts.vfx_adapters"] = fake_adapters

        module = load_module()
        result = module.export_reconstruction(study_root, CONFIG, manifest_path)

        assert result["updated_count"] == 1
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_importlib_loading_does_not_prepend_skill_root_to_sys_path() -> None:
    import scripts.vfx_adapters  # noqa: F401
    import scripts.vfx_preview_common  # noqa: F401

    original = list(sys.path)
    try:
        sys.path[:] = [
            entry
            for entry in sys.path
            if entry and Path(entry).resolve(strict=False) != ROOT.resolve(strict=False)
        ]
        assert not sys_path_contains(ROOT)

        module = load_module()

        assert module.SKILL_ROOT == ROOT
        assert not sys_path_contains(ROOT)
    finally:
        sys.path[:] = original

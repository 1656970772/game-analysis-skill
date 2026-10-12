from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.vfx_preview_common import load_vfx_config, stable_vfx_id
from scripts.vfx_adapters import VfxDependencyRef, VfxExportResult, VfxManifestItem, VfxReconstruction, get_adapter
from scripts.vfx_adapters.unity import UnityVfxAdapter


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


def particle_rows() -> list[dict[str, str]]:
    return [
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
            "asset_name": "fire_loop_Renderer",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystemRenderer/fire_loop_Renderer.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "mat_1",
            "source_file": "resources.assets",
            "path_id": "102",
            "asset_type": "Material",
            "asset_name": "fire_loop_mat",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Material/fire_loop_mat.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_1",
            "source_file": "resources.assets",
            "path_id": "103",
            "asset_type": "Texture2D",
            "asset_name": "fire_loop_sheet",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/fire_loop_sheet.png",
            "width": "256",
            "height": "128",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "mesh_1",
            "source_file": "resources.assets",
            "path_id": "104",
            "asset_type": "Mesh",
            "asset_name": "fire_loop_mesh",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Mesh/fire_loop_mesh.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "anim_1",
            "source_file": "resources.assets",
            "path_id": "105",
            "asset_type": "AnimationClip",
            "asset_name": "fire_loop_anim",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/AnimationClip/fire_loop_anim.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "trail_1",
            "source_file": "resources.assets",
            "path_id": "106",
            "asset_type": "TrailRenderer",
            "asset_name": "fire_loop_trail",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/TrailRenderer/fire_loop_trail.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
    ]


def test_get_adapter_returns_unity_adapter_and_rejects_unknown() -> None:
    config = load_vfx_config(ROOT)

    adapter = get_adapter("unity", config)

    assert isinstance(adapter, UnityVfxAdapter)
    with pytest.raises(ValueError, match="Unsupported VFX engine"):
        get_adapter("unreal", config)


def test_adapter_contract_exports_typed_result_shapes() -> None:
    assert VfxDependencyRef.__name__ == "VfxDependencyRef"
    assert VfxManifestItem.__name__ == "VfxManifestItem"
    assert VfxReconstruction.__name__ == "VfxReconstruction"
    assert VfxExportResult.__name__ == "VfxExportResult"


def test_unity_adapter_discovers_particle_roots_and_dependency_chain(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()

    roots = adapter.discover_roots(rows, tmp_path)
    item = adapter.build_manifest_item(roots[0], rows, tmp_path, tmp_path)

    assert [root["asset_type"] for root in roots] == ["ParticleSystem", "TrailRenderer"]
    assert item["dependencies"]["renderers"][0]["asset_name"] == "fire_loop_Renderer"
    assert item["dependencies"]["materials"][0]["asset_name"] == "fire_loop_mat"
    assert item["dependencies"]["textures"][0]["relative_path"] == "Texture2D/fire_loop_sheet.png"
    assert item["dependencies"]["textures"][0]["match_reason"] == "name_prefix"
    assert item["dependencies"]["textures"][0]["confidence"] == "medium"
    assert item["dependencies"]["meshes"][0]["asset_name"] == "fire_loop_mesh"
    assert item["dependencies"]["animations"][0]["asset_name"] == "fire_loop_anim"


def test_unity_adapter_builds_manifest_item_with_pending_statuses(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    root = rows[0]

    item = adapter.build_manifest_item(root, rows, tmp_path, tmp_path)

    assert item["id"] == stable_vfx_id("resources.assets", "100", "ParticleSystem", "fire_loop")
    assert item["engine"] == "unity"
    assert item["kind"] == "particle_system"
    assert "vfx:fire" in item["tags"]
    assert "vfx:particle" in item["tags"]
    assert item["preview_status"] == "pending"
    assert item["reconstruction_status"] == "pending"
    assert item["unity_export_status"] == "pending"
    assert item["reconstruction_relative_path"] == "reconstructed/fire_loop-100.json"
    assert item["source_refs"] == [root]
    assert set(item["supported_modules"]) >= {"main", "emission", "shape", "renderer", "textureSheetAnimation"}
    assert item["unsupported_modules"] == []


def test_unity_adapter_does_not_read_metadata_outside_unitypy_root(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    unitypy_root = tmp_path / "unitypy"
    unitypy_root.mkdir()
    (tmp_path / "outside.json").write_text(
        '{"m_Texture": {"pathID": "999"}, "leaked": "outside"}',
        encoding="utf-8",
    )
    rows = [
        {
            "asset_id": "ps_unsafe",
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "../outside.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_outside_ref",
            "source_file": "resources.assets",
            "path_id": "999",
            "asset_type": "Texture2D",
            "asset_name": "spark_sheet",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/spark_sheet.png",
            "width": "64",
            "height": "64",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(rows[0], rows, unitypy_root, tmp_path)

    assert item["dependencies"]["textures"] == []
    assert "missing_texture" in item["warnings"]


def test_unity_adapter_uses_prefix_not_contains_for_dependency_names(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = [
        {
            "asset_id": "ps_fire",
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystem/fire.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_fire_loop",
            "source_file": "resources.assets",
            "path_id": "101",
            "asset_type": "Texture2D",
            "asset_name": "fire_loop_sheet",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/fire_loop_sheet.png",
            "width": "256",
            "height": "128",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_campfire_button",
            "source_file": "resources.assets",
            "path_id": "102",
            "asset_type": "Texture2D",
            "asset_name": "campfire_button",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/campfire_button.png",
            "width": "256",
            "height": "128",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)

    assert [texture["asset_name"] for texture in item["dependencies"]["textures"]] == ["fire_loop_sheet"]
    assert item["dependencies"]["textures"][0]["match_reason"] == "name_prefix"
    assert item["dependencies"]["textures"][0]["confidence"] == "medium"
    assert "missing_renderer" in item["warnings"]
    assert "missing_material" in item["warnings"]


def test_unity_adapter_marks_explicit_reference_dependencies_high_confidence(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    root = dict(particle_rows()[0])
    root["metadata"] = {"ParticleSystemRenderer": {"m_Material": {"pathID": "777"}}}
    rows = [
        root,
        {
            "asset_id": "mat_explicit",
            "source_file": "resources.assets",
            "path_id": "777",
            "asset_type": "Material",
            "asset_name": "unrelated_material",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Material/unrelated_material.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(root, rows, tmp_path, tmp_path)

    assert item["dependencies"]["materials"][0]["asset_name"] == "unrelated_material"
    assert item["dependencies"]["materials"][0]["match_reason"] == "explicit_ref"
    assert item["dependencies"]["materials"][0]["confidence"] == "high"


def test_unity_adapter_builds_reconstruction_with_renderer_texture_and_texture_sheet(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)
    metadata = {
        "m_Name": "fire_loop",
        "InitialModule": {
            "duration": 2.5,
            "looping": False,
            "startLifetime": {"constant": 1.2},
            "startSpeed": {"constant": 3.4},
            "startSize": {"constant": 0.75},
            "startColor": {"rgba": [1.0, 0.2, 0.1, 1.0]},
            "gravityModifier": {"constant": 0.1},
            "simulationSpace": "World",
            "maxParticles": 250,
        },
        "EmissionModule": {
            "enabled": True,
            "rateOverTime": {"constant": 24.0},
            "rateOverDistance": {"constant": 0.0},
            "bursts": [{"time": 0.1, "count": 8}],
        },
        "ShapeModule": {
            "enabled": True,
            "shapeType": "Cone",
            "angle": 30.0,
            "radius": 0.5,
            "box": [1.0, 2.0, 3.0],
            "meshRef": "metadata/Mesh/fire_loop_mesh.json",
        },
        "ParticleSystemRenderer": {
            "renderMode": "Mesh",
            "materialRef": "metadata/Material/fire_loop_mat.json",
            "meshRef": "metadata/Mesh/fire_loop_mesh.json",
            "sortingLayer": "Effects",
            "sortingOrder": 12,
            "alignment": "View",
        },
        "TextureSheetAnimationModule": {
            "enabled": True,
            "tiles": {"x": 4, "y": 2},
            "cycles": 3,
        },
    }

    reconstruction = adapter.build_reconstruction(item, metadata)

    assert reconstruction["schema_version"] == 1
    assert reconstruction["engine"] == "unity"
    assert reconstruction["id"] == item["id"]
    assert reconstruction["name"] == "fire_loop"
    assert reconstruction["kind"] == "particle_system"
    assert reconstruction["modules"]["main"]["duration"] == 2.5
    assert reconstruction["modules"]["main"]["looping"] is False
    assert reconstruction["modules"]["emission"]["bursts"] == [{"time": 0.1, "count": 8}]
    assert reconstruction["modules"]["shape"]["shapeType"] == "Cone"
    assert reconstruction["modules"]["renderer"]["renderMode"] == "Mesh"
    assert reconstruction["modules"]["renderer"]["materialRef"] == "metadata/Material/fire_loop_mat.json"
    assert reconstruction["modules"]["renderer"]["textureRef"] == "Texture2D/fire_loop_sheet.png"
    assert reconstruction["modules"]["textureSheetAnimation"] == {
        "enabled": True,
        "tiles": {"x": 4, "y": 2},
        "cycles": 3,
    }
    assert reconstruction["modules"]["raw_unity"] == metadata
    assert reconstruction["resources"]["textures"] == item["dependencies"]["textures"]
    assert reconstruction["preview_status"] == item["preview_status"]
    assert reconstruction["reconstruction_status"] == item["reconstruction_status"]
    assert reconstruction["unity_export_status"] == item["unity_export_status"]


def test_unity_adapter_reconstruction_matches_json_schema(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)
    reconstruction = adapter.build_reconstruction(
        item,
        {
            "m_Duration": 2.0,
            "m_Looping": False,
            "ParticleSystemRenderer": {"renderMode": "Billboard"},
        },
    )
    schema = json.loads((ROOT / "references" / "vfx-unity-reconstruction-schema.json").read_text(encoding="utf-8"))

    validate_json_schema_subset(reconstruction, schema)
    assert "main" not in reconstruction
    assert "raw_unity" not in reconstruction
    assert reconstruction["modules"]["raw_unity"]["m_Duration"] == 2.0


def test_unity_adapter_reconstruction_uses_top_level_main_fields_and_avoids_none(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = [particle_rows()[0]]
    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)

    reconstruction = adapter.build_reconstruction(item, {"m_Duration": 2.0, "m_Looping": False})

    assert reconstruction["modules"]["main"]["duration"] == 2.0
    assert reconstruction["modules"]["main"]["looping"] is False
    for module_name in ["main", "emission", "shape", "renderer", "textureSheetAnimation", "trail", "line"]:
        assert all(value is not None for value in reconstruction["modules"][module_name].values())
    assert reconstruction["modules"]["renderer"]["materialRef"] == ""
    assert reconstruction["modules"]["renderer"]["textureRef"] == ""
    assert reconstruction["modules"]["renderer"]["meshRef"] == ""


def test_unity_adapter_prefers_dependency_texture_over_renderer_metadata(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)
    item["dependencies"]["textures"][0]["relative_path"] = "Texture2D/spark_sheet.png"
    metadata = {
        "ParticleSystemRenderer": {
            "textureRef": "metadata_texture_should_not_win",
            "m_Texture": "metadata_texture_should_not_win",
        },
    }

    reconstruction = adapter.build_reconstruction(item, metadata)

    assert reconstruction["modules"]["renderer"]["textureRef"] == "Texture2D/spark_sheet.png"


def test_unity_adapter_does_not_match_explicit_refs_across_source_files(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    root = dict(particle_rows()[0])
    root["source_file"] = "a.assets"
    root["metadata"] = {"ParticleSystemRenderer": {"m_Material": {"pathID": "777"}}}
    rows = [
        root,
        {
            "asset_id": "mat_wrong_bundle",
            "source_file": "b.assets",
            "path_id": "777",
            "asset_type": "Material",
            "asset_name": "unrelated_material",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Material/unrelated_material.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(root, rows, tmp_path, tmp_path)

    assert item["dependencies"]["materials"] == []
    assert "missing_material" in item["warnings"]


@pytest.mark.parametrize(
    "export_path",
    [
        r"C:\outside.json",
        r"C:outside.json",
        r"\\server\share\outside.json",
    ],
)
def test_unity_adapter_rejects_windows_absolute_drive_relative_and_unc_metadata_paths(
    tmp_path: Path,
    export_path: str,
) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = [
        {
            "asset_id": "ps_unsafe",
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": export_path,
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_outside_ref",
            "source_file": "resources.assets",
            "path_id": "999",
            "asset_type": "Texture2D",
            "asset_name": "spark_sheet",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/spark_sheet.png",
            "width": "64",
            "height": "64",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(rows[0], rows, tmp_path / "unitypy", tmp_path)

    assert item["dependencies"]["textures"] == []
    assert "missing_texture" in item["warnings"]


def test_unity_adapter_ignores_invalid_utf8_metadata_without_crashing(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    unitypy_root = tmp_path / "unitypy"
    metadata_path = unitypy_root / "metadata" / "ParticleSystem" / "bad.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_bytes(b"\xff\xfe\xfa")
    rows = [
        {
            "asset_id": "ps_bad_utf8",
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystem/bad.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "tex_outside_ref",
            "source_file": "resources.assets",
            "path_id": "999",
            "asset_type": "Texture2D",
            "asset_name": "spark_sheet",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/spark_sheet.png",
            "width": "64",
            "height": "64",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(rows[0], rows, unitypy_root, tmp_path)

    assert item["dependencies"]["textures"] == []
    assert "missing_texture" in item["warnings"]


def test_unity_adapter_follows_explicit_dependency_chain_from_bom_metadata(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    unitypy_root = tmp_path / "unitypy"
    root_metadata = unitypy_root / "metadata" / "ParticleSystem" / "root.json"
    renderer_metadata = unitypy_root / "metadata" / "ParticleSystemRenderer" / "renderer.json"
    material_metadata = unitypy_root / "metadata" / "Material" / "material.json"
    root_metadata.parent.mkdir(parents=True)
    renderer_metadata.parent.mkdir(parents=True)
    material_metadata.parent.mkdir(parents=True)
    root_metadata.write_text(json.dumps({"renderer": {"pathID": "201"}}), encoding="utf-8-sig")
    renderer_metadata.write_text(json.dumps({"material": {"pathID": "202"}}), encoding="utf-8-sig")
    material_metadata.write_text(json.dumps({"texture": {"pathID": "203"}}), encoding="utf-8-sig")
    rows = [
        {
            "asset_id": "ps_bom",
            "source_file": "resources.assets",
            "path_id": "200",
            "asset_type": "ParticleSystem",
            "asset_name": "root_effect",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystem/root.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "renderer_bom",
            "source_file": "resources.assets",
            "path_id": "201",
            "asset_type": "ParticleSystemRenderer",
            "asset_name": "draw_node",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/ParticleSystemRenderer/renderer.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "material_bom",
            "source_file": "resources.assets",
            "path_id": "202",
            "asset_type": "Material",
            "asset_name": "surface",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Material/material.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "texture_bom",
            "source_file": "resources.assets",
            "path_id": "203",
            "asset_type": "Texture2D",
            "asset_name": "atlas",
            "container_path": "",
            "export_kind": "image",
            "export_path": "Texture2D/atlas.png",
            "width": "64",
            "height": "64",
            "status": "exported",
            "remarks": "",
        },
    ]

    item = adapter.build_manifest_item(rows[0], rows, unitypy_root, tmp_path)

    assert item["dependencies"]["textures"][0]["asset_name"] == "atlas"
    assert item["dependencies"]["textures"][0]["match_reason"] == "explicit_ref"
    assert item["dependencies"]["textures"][0]["confidence"] == "high"
    assert "missing_texture" not in item["warnings"]


def test_unity_adapter_marks_unsupported_modules_without_blocking_supported_modules(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    root = dict(rows[0])
    root["modules"] = ["InitialModule", "EmissionModule", "NoiseModule", "TextureSheetAnimationModule"]

    item = adapter.build_manifest_item(root, rows, tmp_path, tmp_path)

    assert {"main", "emission", "textureSheetAnimation"}.issubset(item["supported_modules"])
    assert item["unsupported_modules"] == ["NoiseModule"]


def test_export_project_fragment_marks_trail_and_line_roots_partial_not_ready(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    for root_type, kind in [
        ("TrailRenderer", "trail_renderer"),
        ("LineRenderer", "line_renderer"),
    ]:
        reconstruction = {
            "schema_version": 1,
            "engine": "unity",
            "id": root_type.lower(),
            "name": root_type,
            "kind": kind,
            "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": root_type},
            "root_type": root_type,
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
            "warnings": [],
        }

        result = adapter.export_project_fragment(
            {"id": root_type.lower(), "name": root_type, "root_type": root_type},
            reconstruction,
            tmp_path / f"{root_type}-unity-export",
        )

        assert result["status"] == "partial"
        assert result["ready"] is False
        assert result["unity_export_status"] == "partial"
        assert result["unity_export_status"] != "ready"
        assert "ParticleSystem only" in result["reason"]
        assert any("ParticleSystem only" in warning for warning in reconstruction["warnings"])


def test_export_project_fragment_with_uncopied_texture_dependency_is_partial(tmp_path: Path) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    reconstruction = {
        "schema_version": 1,
        "engine": "unity",
        "id": "missing-texture",
        "name": "missing_texture",
        "kind": "particle_system",
        "source_object": {"source_file": "resources.assets", "path_id": "100", "asset_type": "ParticleSystem"},
        "root_type": "ParticleSystem",
        "modules": {
            "main": {"duration": 1.0},
            "emission": {"enabled": True, "rateOverTime": {"constant": 8.0}},
            "shape": {"enabled": True, "shapeType": "Cone", "angle": 12.0, "radius": 0.25},
            "renderer": {"renderMode": "Billboard", "textureRef": "Texture2D/missing.png"},
            "raw_unity": {},
        },
        "resources": {
            "textures": [{"relative_path": "Texture2D/missing.png", "asset_type": "Texture2D"}],
            "materials": [],
            "meshes": [],
            "animations": [],
        },
        "preview_status": "pending",
        "reconstruction_status": "pending",
        "unity_export_status": "pending",
        "warnings": [],
    }

    result = adapter.export_project_fragment(
        {"id": "missing-texture", "name": "missing_texture", "__unitypy_root": str(tmp_path / "unitypy")},
        reconstruction,
        tmp_path / "unity-export",
    )

    assert result["status"] == "partial"
    assert result["ready"] is False
    assert result["unity_export_status"] == "partial"
    assert "Texture2D dependency is not copied or bound" in result["reason"]
    asset_root = tmp_path / "unity-export" / Path(config["unity_export"]["asset_root"])
    material = asset_root / "Materials" / "missing-texture.mat"
    assert "m_Texture: {fileID: 0}" in material.read_text(encoding="utf-8")
    assert not (asset_root / "Textures" / "missing.png").exists()


def test_unity_adapter_export_project_fragment_writes_ready_unity_fragment(
    tmp_path: Path,
) -> None:
    config = load_vfx_config(ROOT)
    adapter = UnityVfxAdapter(config)
    rows = particle_rows()
    item = adapter.build_manifest_item(rows[0], rows, tmp_path, tmp_path)
    (tmp_path / "Texture2D").mkdir()
    (tmp_path / "Texture2D" / "fire_loop_sheet.png").write_bytes(b"fake-png")
    item["__unitypy_root"] = str(tmp_path)
    reconstruction = adapter.build_reconstruction(item, {"m_Name": "fire_loop"})
    export_root = tmp_path / "unity-export"

    result = adapter.export_project_fragment(item, reconstruction, export_root)

    assert result["status"] == "ready"
    assert result["viewer_runtime_supported"] is True
    assert result["reconstruction_status"] == "full"
    assert result["unity_export_status"] == "ready"
    config_asset_root = Path(config["unity_export"]["asset_root"])
    assert result["unity_export_relative_path"] == f"unity-export/{config_asset_root.as_posix()}/Prefabs/fire_loop.prefab"
    assert result["ready"] is True
    asset_root = export_root / config_asset_root
    assert (asset_root / "Prefabs" / "fire_loop.prefab").is_file()
    assert (asset_root / "Scripts" / "GameAnalysisVfxBootstrap.cs").is_file()
    assert (asset_root / "Materials" / f"{item['id']}.mat").is_file()
    assert (asset_root / "Materials" / f"{item['id']}.mat.meta").is_file()
    texture = asset_root / "Textures" / "fire_loop_sheet.png"
    texture_meta = asset_root / "Textures" / "fire_loop_sheet.png.meta"
    assert texture.is_file()
    assert texture.read_bytes() == b"fake-png"
    texture_guid = next(
        line.split(": ", 1)[1]
        for line in texture_meta.read_text(encoding="utf-8").splitlines()
        if line.startswith("guid: ")
    )
    material_text = (asset_root / "Materials" / f"{item['id']}.mat").read_text(encoding="utf-8")
    assert f"m_Texture: {{fileID: 2800000, guid: {texture_guid}, type: 3}}" in material_text
    assert json.loads((asset_root / "Metadata" / f"{item['id']}.json").read_text(encoding="utf-8"))[
        "modules"
    ]["main"]["duration"] == 5.0

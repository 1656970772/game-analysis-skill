from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_model_gallery_manifest.py"
CONFIG = ROOT / "references" / "model-viewer-gallery-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("build_model_gallery_manifest", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_minimal_animated_gltf(path: Path) -> None:
    payload = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [{"mesh": 0, "name": "animated_root"}],
        "meshes": [{"name": "animated_mesh", "primitives": []}],
        "materials": [{"name": "mat"}],
        "buffers": [{"uri": "anim.bin", "byteLength": 0}],
        "bufferViews": [],
        "accessors": [{"min": [0.0], "max": [1.25], "count": 2, "type": "SCALAR"}],
        "animations": [
            {
                "name": "run",
                "samplers": [{"input": 0, "output": 0}],
                "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
            }
        ],
        "skins": [{"joints": [0]}],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    path.with_name("anim.bin").write_bytes(b"")


def write_minimal_animated_glb(path: Path) -> None:
    payload = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [{"name": "glb_root"}],
        "animations": [{"name": "idle", "samplers": [], "channels": []}],
    }
    json_chunk = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total_length = 12 + 8 + len(json_chunk)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
    )


def write_minimal_static_gltf(path: Path, mesh_name: str | None = None) -> None:
    payload = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [{"mesh": 0, "name": mesh_name or path.stem}],
        "meshes": [{"name": mesh_name or path.stem, "primitives": []}],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_build_manifest_records_gltf_animation_stats_and_tags(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "hero"
    model_dir.mkdir(parents=True)
    write_minimal_animated_gltf(model_dir / "hero.gltf")
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    item = manifest["items"][0]
    assert item["stats"]["mesh_count"] == 1
    assert item["stats"]["material_count"] == 1
    assert item["stats"]["skin_count"] == 1
    assert item["stats"]["animation_count"] == 1
    assert item["animations"] == [{"index": 0, "name": "run", "duration": 1.25}]
    assert item["animation_status"] == "with"
    assert "animation:with" in item["tags"]


def test_build_manifest_reads_glb_json_animation_count(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "hero_glb"
    model_dir.mkdir(parents=True)
    write_minimal_animated_glb(model_dir / "hero.glb")
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    item = manifest["items"][0]
    assert item["source_format"] == "glb"
    assert item["stats"]["animation_count"] == 1
    assert item["animations"] == [{"index": 0, "name": "idle"}]
    assert item["animation_status"] == "with"
    assert item["animation_origin"] == "embedded"


def test_build_manifest_marks_procedural_animation_preview_separately(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "Unity动作预览_AnimationClips"
    model_dir.mkdir(parents=True)
    model_path = model_dir / "Unity动作预览_AnimationClips.gltf"
    write_minimal_animated_gltf(model_path)
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    payload["extras"] = {
        "game_analysis": {
            "animation_origin": "procedural_preview",
            "is_generated_preview": True,
        }
    }
    model_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    item = manifest["items"][0]
    assert item["stats"]["animation_count"] == 1
    assert item["animation_status"] == "with"
    assert item["animation_origin"] == "procedural_preview"
    assert item["is_generated_animation_preview"] is True
    assert "animation-origin:procedural_preview" in item["tags"]


def test_build_manifest_preserves_non_procedural_game_analysis_animation_origin(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "dense_preview"
    model_dir.mkdir(parents=True)
    model_path = model_dir / "dense_preview.gltf"
    write_minimal_animated_gltf(model_path)
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    payload["extras"] = {
        "game_analysis": {
            "animation_origin": "mecanim_dense_preview",
            "is_generated_preview": False,
        }
    }
    model_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    item = manifest["items"][0]
    assert item["stats"]["animation_count"] == 1
    assert item["animation_origin"] == "mecanim_dense_preview"
    assert item["is_generated_animation_preview"] is False
    assert "animation-origin:mecanim_dense_preview" in item["tags"]


def test_build_manifest_adds_full_body_preview_to_outfit_anchor(tmp_path: Path) -> None:
    module = load_module()
    models = tmp_path / "1.分类资源" / "模型"
    for name in [
        "man003_body",
        "man003_head",
        "man003_10_clothes",
        "man003_10_trousers",
        "man003_10_Larms",
        "man003_10_Rarms",
    ]:
        model_dir = models / name
        model_dir.mkdir(parents=True)
        write_minimal_static_gltf(model_dir / f"{name}.gltf", name)
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    items_by_name = {item["name"]: item for item in manifest["items"]}
    clothes = items_by_name["man003_10_clothes"]
    preview = clothes["full_body_preview"]
    assert preview["name"] == "man003_10 全身预览"
    assert [part["role"] for part in preview["parts"]] == [
        "body",
        "head",
        "clothes",
        "trousers",
        "Larms",
        "Rarms",
    ]
    assert [part["name"] for part in preview["parts"]] == [
        "man003_body",
        "man003_head",
        "man003_10_clothes",
        "man003_10_trousers",
        "man003_10_Larms",
        "man003_10_Rarms",
    ]
    assert "full-body-preview:available" in clothes["tags"]
    assert "full_body_preview" not in items_by_name["man003_10_trousers"]


def test_build_manifest_merges_unitypy_binding_source_and_writes_sidecars(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "weapon_jian1"
    model_dir.mkdir(parents=True)
    texture_dir = model_dir / "textures" / "待验证_同名前缀"
    texture_dir.mkdir(parents=True)
    (texture_dir / "weapon_jian1.png").write_bytes(b"fake-png")
    model_path = model_dir / "weapon_jian1.gltf"
    payload = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "nodes": [{"mesh": 0, "name": "weapon_jian1"}],
        "meshes": [{"name": "weapon_jian1", "primitives": [{"attributes": {}, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "images": [{"uri": "textures/待验证_同名前缀/weapon_jian1.png"}],
        "textures": [{"source": 0}],
    }
    model_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    index_dir = tmp_path / "4.临时目录" / "中间索引" / "UnityPy模型导出"
    index_dir.mkdir(parents=True)
    (index_dir / "模型导出索引.csv").write_text(
        "\n".join(
            [
                "mesh_name,output_name,source_file,path_id,gltf_path,vertices,triangles,texture_candidates_copied,material_texture_name,material_texture_uri,material_binding_source,status,remarks",
                "weapon_jian1,weapon_jian1,resources.assets,42,1.分类资源\\模型\\weapon_jian1\\weapon_jian1.gltf,3,1,1,,textures/待验证_同名前缀/weapon_jian1.png,same_name_candidate,exported,候选贴图",
            ]
        ),
        encoding="utf-8",
    )
    config = module.load_model_viewer_config(CONFIG)

    manifest = module.build_manifest(tmp_path, config)

    item = manifest["items"][0]
    assert item["material_binding_source"] == "same_name_candidate"
    assert item["texture_status"] == "same_name_candidate"
    assert item["material_status"] == "unknown"
    assert "texture-source:same_name_candidate" in item["tags"]
    for sidecar_name in ["metadata.json", "export-log.json", "dependencies.json", "missing-resources.json"]:
        assert (model_dir / sidecar_name).is_file()
    dependencies = json.loads((model_dir / "dependencies.json").read_text(encoding="utf-8"))
    assert dependencies["textures"][0]["uri"] == "textures/待验证_同名前缀/weapon_jian1.png"


def test_build_manifest_reads_configured_unitypy_model_index(tmp_path: Path) -> None:
    module = load_module()
    model_dir = tmp_path / "1.分类资源" / "模型" / "YooAsset全量" / "mesh_a"
    model_dir.mkdir(parents=True)
    model_path = model_dir / "mesh_a.gltf"
    write_minimal_static_gltf(model_path)
    custom_index = tmp_path / "4.临时目录" / "中间索引" / "UnityPy模型导出YooAsset" / "模型导出索引.csv"
    custom_index.parent.mkdir(parents=True)
    custom_index.write_text(
        "\n".join(
            [
                "mesh_name,output_name,source_file,path_id,gltf_path,material_binding_source,status",
                "mesh_a,mesh_a,DefaultPackage/a.bundle,42,1.分类资源/模型/YooAsset全量/mesh_a/mesh_a.gltf,unity_material_configured_property,exported",
            ]
        ),
        encoding="utf-8",
    )
    config = module.load_model_viewer_config(CONFIG)
    config["paths"]["models_dir"] = "1.分类资源/模型/YooAsset全量"
    config["paths"]["preview_index_dir"] = "4.临时目录/中间索引/模型预览YooAsset"
    config["paths"]["unitypy_model_index"] = "4.临时目录/中间索引/UnityPy模型导出YooAsset/模型导出索引.csv"

    manifest = module.build_manifest(tmp_path, config)

    assert manifest["item_count"] == 1
    assert manifest["items"][0]["material_binding_source"] == "unity_material_configured_property"

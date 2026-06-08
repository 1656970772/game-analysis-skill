from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_umodel_slice_scripts_are_declared_and_generic():
    skill = read_text("SKILL.md")

    assert "scripts/export_unreal_umodel_slices.ps1" in skill
    assert "scripts/merge_unreal_umodel_slices.ps1" in skill
    assert "scripts/repair_umodel_gltf_materials.py" in skill
    assert "Wandering Sword" not in skill


def test_export_script_preserves_paths_and_uses_two_pass_umodel_flow():
    script = read_text("scripts/export_unreal_umodel_slices.ps1")

    required_terms = [
        "New-Item -ItemType Junction",
        'string]$GameTag = "ue4.26"',
        '"01_textures_materials"',
        '"02_full_models"',
        '"-nomesh"',
        '"-nostat"',
        '"-noanim"',
        '"-novert"',
        '"raw_umodel"',
        '"by_type"',
        '"Textures_PNG"',
        '"Models_GLTF"',
        "rg",
        "StaticMesh|SkeletalMesh|AnimSequence|AnimMontage|Skeleton",
        "converted_assets_index.csv",
    ]

    for term in required_terms:
        assert term in script

    assert "Wandering Sword" not in script


def test_merge_script_verifies_raw_outputs_and_renderable_formats():
    script = read_text("scripts/merge_unreal_umodel_slices.ps1")

    required_terms = [
        "missing_raw_files",
        "size_mismatch_files",
        "zero_byte_files",
        "png_bad_signatures",
        "gltf_missing_buffers",
        "Test-PngSignature",
        "Test-GltfBuffers",
        "verification_summary.json",
        "all_converted_assets_index.csv",
    ]

    for term in required_terms:
        assert term in script

    assert "Wandering Sword" not in script


def test_unreal_reference_documents_full_conversion_boundaries():
    reference = read_text("references/unreal-pak-workflow.md")

    required_terms = [
        "UModel 全量转换与分片导出",
        "repak unpack",
        "Cooked Unreal 项目结构",
        "-game=ue4.26",
        "Windows junction",
        "raw_umodel",
        "by_type",
        "PNG 文件头",
        "glTF JSON",
        "FBX",
        "repair_umodel_gltf_materials.py",
        "TextureParameterValues",
        "FullConverted_BlenderReady_GLTF",
        "baseColorTexture",
        "_textures",
    ]

    for term in required_terms:
        assert term in reference

    assert "Wandering Sword" not in reference

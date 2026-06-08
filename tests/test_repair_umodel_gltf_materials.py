import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_umodel_gltf_materials.py"


def test_repair_umodel_gltf_materials_links_props_texture(tmp_path):
    converted = tmp_path / "FullConverted"
    raw = converted / "09_Effects" / "raw_umodel"
    mesh_dir = raw / "Game" / "FX" / "Meshes"
    material_dir = raw / "Game" / "FX" / "Materials"
    texture_dir = raw / "Game" / "FX" / "Texture"
    mesh_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    texture_dir.mkdir(parents=True)

    (mesh_dir / "Stone_1.bin").write_bytes(b"\x00" * 16)
    (texture_dir / "T_SnowCliff.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (material_dir / "Mi_ce.props.txt").write_text(
        """
TextureParameterValues[1] =
{
    TextureParameterValues[0] =
    {
        ParameterInfo = { Name=Texture }
        ParameterValue = Texture2D'Game/FX/Texture/T_SnowCliff.T_SnowCliff'
    }
}
""".strip(),
        encoding="utf-8",
    )
    (mesh_dir / "Stone_1.gltf").write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "materials": [
                    {
                        "name": "Mi_ce",
                        "pbrMetallicRoughness": {
                            "baseColorFactor": [0.3, 0.3, 0.3, 1.0]
                        },
                    }
                ],
                "buffers": [{"uri": "Stone_1.bin", "byteLength": 16}],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "BlenderReady"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--converted-root",
            str(converted),
            "--output-root",
            str(output),
            "--overwrite",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    fixed_gltf = output / "09_Effects" / "Game" / "FX" / "Meshes" / "Stone_1.gltf"
    fixed = json.loads(fixed_gltf.read_text(encoding="utf-8"))
    material = fixed["materials"][0]

    assert "TOTAL_GLTF=1" in result.stdout
    assert "REPAIRED_MATERIALS=1" in result.stdout
    assert fixed["images"][0]["uri"].startswith("_textures/")
    assert (fixed_gltf.parent / fixed["images"][0]["uri"]).exists()
    assert material["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 0
    assert material["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
    assert (fixed_gltf.parent / "Stone_1.bin").exists()


def test_repair_umodel_gltf_materials_falls_back_for_dummy_materials(tmp_path):
    converted = tmp_path / "FullConverted"
    raw = converted / "09_Effects" / "raw_umodel"
    mesh_dir = raw / "Game" / "FX" / "Meshes"
    material_dir = raw / "Game" / "FX" / "Materials"
    texture_dir = raw / "Game" / "FX" / "Texture"
    mesh_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    texture_dir.mkdir(parents=True)

    (mesh_dir / "Kunai.bin").write_bytes(b"\x00" * 16)
    (texture_dir / "T_Noise.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (texture_dir / "T_Kunai_Mesh.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    (material_dir / "Mi_kunai_02.props.txt").write_text(
        """
TextureParameterValues[2] =
{
    TextureParameterValues[0] =
    {
        ParameterInfo = { Name=Param }
        ParameterValue = Texture2D'Game/FX/Texture/T_Noise.T_Noise'
    }
    TextureParameterValues[1] =
    {
        ParameterInfo = { Name=Param_1 }
        ParameterValue = Texture2D'Game/FX/Texture/T_Kunai_Mesh.T_Kunai_Mesh'
    }
}
""".strip(),
        encoding="utf-8",
    )
    (mesh_dir / "Kunai.gltf").write_text(
        json.dumps(
            {
                "asset": {"version": "2.0"},
                "materials": [
                    {
                        "name": "dummy_material_0",
                        "pbrMetallicRoughness": {
                            "baseColorFactor": [0.3, 0.3, 0.3, 1.0]
                        },
                    }
                ],
                "buffers": [{"uri": "Kunai.bin", "byteLength": 16}],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "BlenderReady"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--converted-root",
            str(converted),
            "--output-root",
            str(output),
            "--overwrite",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    fixed_gltf = output / "09_Effects" / "Game" / "FX" / "Meshes" / "Kunai.gltf"
    fixed = json.loads(fixed_gltf.read_text(encoding="utf-8"))
    material = fixed["materials"][0]
    image_uri = fixed["images"][0]["uri"]
    index_csv = output / "_material_repair_index" / "material_repair_index.csv"

    assert "REPAIRED_MATERIALS=1" in result.stdout
    assert len(fixed["images"]) == 1
    assert "T_Kunai_Mesh" in image_uri
    assert "T_Noise" not in image_uri
    assert material["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 0
    assert "normalTexture" not in material
    assert "fallback_props_by_mesh_name" in index_csv.read_text(encoding="utf-8-sig")

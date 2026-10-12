from __future__ import annotations

import importlib.util
import csv
import json
import tempfile
import time
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_unitypy_meshes_to_gltf.py"


def load_module():
    spec = importlib.util.spec_from_file_location("export_unitypy_meshes_to_gltf", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_obj_to_gltf_binds_base_color_texture(tmp_path: Path) -> None:
    module = load_module()
    obj_text = "\n".join(
        [
            "v 0 0 0",
            "v 1 0 0",
            "v 0 1 0",
            "vt 0 0",
            "vt 1 0",
            "vt 0 1",
            "vn 0 0 1",
            "f 1/1/1 2/2/1 3/3/1",
        ]
    )
    gltf_path = tmp_path / "triangle.gltf"

    module.obj_to_gltf(
        obj_text,
        "triangle",
        gltf_path,
        material_texture_uri="textures/diffuse.png",
    )

    gltf = json.loads(gltf_path.read_text(encoding="utf-8"))
    assert gltf["images"] == [{"uri": "textures/diffuse.png"}]
    assert gltf["textures"] == [{"source": 0}]
    assert gltf["materials"][0]["doubleSided"] is True
    assert gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0}
    assert gltf["meshes"][0]["primitives"][0]["material"] == 0


def test_copy_texture_candidates_with_uri_selects_copied_candidate(tmp_path: Path) -> None:
    module = load_module()
    texture_root = tmp_path / "images"
    texture_root.mkdir()
    source_texture = texture_root / "hero_body_diffuse.png"
    source_texture.write_bytes(b"fake-png")
    model_dir = tmp_path / "model"

    copied, uri = module.copy_texture_candidates_with_uri(texture_root, model_dir, "hero_body", 6)

    assert copied == 1
    assert uri == "textures/待验证_同名前缀/hero_body_diffuse.png"
    assert (model_dir / "textures" / "待验证_同名前缀" / "hero_body_diffuse.png").read_bytes() == b"fake-png"


def test_iter_unity_sources_accepts_trusted_magic_dat_and_rejects_random_dat(
    tmp_path: Path,
) -> None:
    module = load_module()
    (tmp_path / "valid.dat").write_bytes(b"UnityFS\x00synthetic")
    (tmp_path / "random.dat").write_bytes(b"not-a-unity-container")
    (tmp_path / "wrong-extension.bin").write_bytes(b"UnityWeb\x00synthetic")

    discovered = {path.name for path in module.iter_unity_sources(tmp_path)}

    assert discovered == {"valid.dat", "wrong-extension.bin"}


def test_iter_unity_sources_includes_extensionless_builtin_resource_containers(
    tmp_path: Path,
) -> None:
    module = load_module()
    resources = tmp_path / "Synthetic_Data" / "Resources"
    resources.mkdir(parents=True)
    (resources / "unity default resources").write_bytes(b"synthetic-default-resources")
    (resources / "unity_builtin_extra").write_bytes(b"synthetic-builtin-extra")

    discovered = {
        path.relative_to(tmp_path).as_posix()
        for path in module.iter_unity_sources(tmp_path)
    }

    assert discovered == {
        "Synthetic_Data/Resources/unity default resources",
        "Synthetic_Data/Resources/unity_builtin_extra",
    }


def test_output_name_allocator_avoids_legal_name_and_generated_suffix_collision(
    tmp_path: Path,
) -> None:
    module = load_module()
    mesh_names = ["平面_007", *(["平面"] * 7)]

    def allocate_all() -> list[str]:
        used_names: set[str] = set()
        suffix_counters: dict[str, int] = {}
        return [
            module.allocate_unique_output_name(name, used_names, suffix_counters)
            for name in mesh_names
        ]

    output_names = allocate_all()
    assert output_names == allocate_all()
    assert len({name.casefold() for name in output_names}) == len(output_names)
    assert output_names.count("平面_007") == 1

    gltf_paths = [tmp_path / name / f"{name}.gltf" for name in output_names]
    assert len({str(path).casefold() for path in gltf_paths}) == len(gltf_paths)
    for path in gltf_paths:
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
    assert all(path.is_file() for path in gltf_paths)


def test_classify_unitypy_mesh_export_false_as_no_geometry() -> None:
    module = load_module()

    status, reason = module.classify_mesh_export_result(False)

    assert status == "skipped_no_geometry"
    assert "no decoded vertex geometry" in reason


def test_detects_runtime_spine_mesh_placeholder() -> None:
    module = load_module()
    mesh = type(
        "FakeMesh",
        (),
        {
            "m_Name": 'Skeleton Prefab Mesh "Gun"',
            "m_SubMeshes": [],
        },
    )()

    assert module.is_runtime_generated_mesh_placeholder(mesh) is True


class FakeType:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeObj:
    def __init__(self, path_id: int, type_name: str, data: object) -> None:
        self.path_id = path_id
        self.type = FakeType(type_name)
        self._data = data

    def read(self) -> object:
        return self._data


class FakePtr:
    def __init__(self, path_id: int) -> None:
        self.path_id = path_id


class FakeRenderer:
    def __init__(self, mesh_id: int, material_id: int) -> None:
        self.m_Mesh = FakePtr(mesh_id)
        self.m_Materials = [FakePtr(material_id)]


class FakeTexEnv:
    def __init__(self, texture_id: int) -> None:
        self.m_Texture = FakePtr(texture_id)


class FakeSavedProperties:
    def __init__(self, texture_id: int) -> None:
        self.m_TexEnvs = [("_MainTex", FakeTexEnv(texture_id))]


class FakeSavedPropertiesWithBaseMap:
    def __init__(self, main_texture_id: int, base_map_id: int) -> None:
        self.m_TexEnvs = [
            ("_MainTex", FakeTexEnv(main_texture_id)),
            ("_BaseMap", FakeTexEnv(base_map_id)),
        ]


class FakeMaterial:
    def __init__(self, texture_id: int) -> None:
        self.m_SavedProperties = FakeSavedProperties(texture_id)


class FakeMaterialWithBaseMap:
    def __init__(self, main_texture_id: int, base_map_id: int) -> None:
        self.m_SavedProperties = FakeSavedPropertiesWithBaseMap(main_texture_id, base_map_id)


class FakeTexture:
    def __init__(self, name: str) -> None:
        self.m_Name = name


class FakeEnv:
    def __init__(self) -> None:
        self.objects = [
            FakeObj(10, "SkinnedMeshRenderer", FakeRenderer(20, 30)),
            FakeObj(30, "Material", FakeMaterial(40)),
            FakeObj(40, "Texture2D", FakeTexture("hero_diffuse")),
        ]


def test_build_mesh_texture_name_map_reads_renderer_material_main_texture() -> None:
    module = load_module()

    mapping = module.build_mesh_texture_name_map(FakeEnv())

    assert mapping == {20: "hero_diffuse"}


def test_build_mesh_texture_name_map_uses_configured_texture_property_priority() -> None:
    module = load_module()
    env = type("FakeEnv", (), {})()
    env.objects = [
        FakeObj(10, "MeshRenderer", FakeRenderer(20, 30)),
        FakeObj(30, "Material", FakeMaterialWithBaseMap(40, 41)),
        FakeObj(40, "Texture2D", FakeTexture("legacy_main")),
        FakeObj(41, "Texture2D", FakeTexture("urp_base")),
    ]
    config = {"texture_property_priority": ["_BaseMap", "_MainTex"]}

    mapping = module.build_mesh_texture_name_map(env, config)

    assert mapping == {20: "urp_base"}


class FakeMeshFilter:
    def __init__(self, game_object_id: int, mesh_id: int) -> None:
        self.m_GameObject = FakePtr(game_object_id)
        self.m_Mesh = FakePtr(mesh_id)


class FakeRendererViaGameObject:
    def __init__(self, game_object_id: int, material_id: int) -> None:
        self.m_GameObject = FakePtr(game_object_id)
        self.m_Materials = [FakePtr(material_id)]


def test_build_mesh_texture_binding_map_joins_meshfilter_and_meshrenderer() -> None:
    module = load_module()
    env = type("FakeEnv", (), {})()
    env.objects = [
        FakeObj(10, "MeshFilter", FakeMeshFilter(100, 20)),
        FakeObj(11, "MeshRenderer", FakeRendererViaGameObject(100, 30)),
        FakeObj(30, "Material", FakeMaterial(40)),
        FakeObj(40, "Texture2D", FakeTexture("joined_diffuse")),
    ]

    mapping = module.build_mesh_texture_binding_map(env)

    assert mapping[20]["texture_name"] == "joined_diffuse"
    assert mapping[20]["texture_property"] == "_MainTex"
    assert mapping[20]["renderer_path_id"] == 11
    assert mapping[20]["material_path_id"] == 30
    assert mapping[20]["binding_count"] == 1
    assert mapping[20]["ambiguous"] is False


def test_build_mesh_texture_binding_map_resolves_external_texture_name() -> None:
    module = load_module()
    env = type("FakeEnv", (), {})()
    env.objects = [
        FakeObj(10, "SkinnedMeshRenderer", FakeRenderer(20, 30)),
        FakeObj(30, "Material", FakeMaterial(40)),
    ]

    mapping = module.build_mesh_texture_binding_map(env, external_texture_names={40: "external_diffuse"})

    assert mapping[20]["texture_name"] == "external_diffuse"
    assert mapping[20]["texture_path_id"] == 40


def test_build_mesh_texture_binding_map_resolves_external_material_evidence() -> None:
    module = load_module()
    env = type("FakeEnv", (), {})()
    env.objects = [FakeObj(10, "SkinnedMeshRenderer", FakeRenderer(20, 30))]
    external_materials = {
        30: [
            {
                "material_path_id": 30,
                "texture_path_id": 40,
                "texture_name": "external_diffuse",
                "texture_property": "_BaseMap",
                "material_source_file": "DefaultPackage/material.bundle",
            }
        ]
    }

    mapping = module.build_mesh_texture_binding_map(env, external_material_bindings=external_materials)

    assert mapping[20]["texture_name"] == "external_diffuse"
    assert mapping[20]["texture_property"] == "_BaseMap"
    assert mapping[20]["material_source_file"] == "DefaultPackage/material.bundle"


def test_expected_mesh_index_filters_and_tracks_exact_mesh_keys(tmp_path: Path) -> None:
    module = load_module()
    index_path = tmp_path / "all_resources_index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_file", "path_id", "asset_type", "asset_name", "container_path"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "source_file": "DefaultPackage/a.bundle",
                "path_id": "42",
                "asset_type": "Mesh",
                "asset_name": "mesh_a",
                "container_path": "Assets/mesh_a.asset",
            }
        )
        writer.writerow(
            {
                "source_file": "DefaultPackage/a.bundle",
                "path_id": "43",
                "asset_type": "Texture2D",
                "asset_name": "texture_a",
                "container_path": "Assets/texture_a.png",
            }
        )

    entries = module.load_expected_mesh_entries(index_path)

    assert list(entries) == [("DefaultPackage/a.bundle", 42)]
    assert entries[("DefaultPackage/a.bundle", 42)]["asset_name"] == "mesh_a"


def test_safe_output_subdir_rejects_escape_and_accepts_nested_relative() -> None:
    module = load_module()

    assert module.safe_relative_subdir("YooAsset/full", allow_empty=False).as_posix() == "YooAsset/full"
    for invalid in ("", ".", "..", "../outside", "/absolute", "C:/absolute", "bad\\path"):
        try:
            module.safe_relative_subdir(invalid, allow_empty=False)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected unsafe subdir rejection: {invalid}")


def test_obj_to_gltf_preserves_mtime_when_content_is_unchanged(tmp_path: Path) -> None:
    module = load_module()
    obj_text = "\n".join(
        [
            "v 0 0 0",
            "v 1 0 0",
            "v 0 1 0",
            "f 1 2 3",
        ]
    )
    gltf_path = tmp_path / "triangle.gltf"

    module.obj_to_gltf(obj_text, "triangle", gltf_path)
    first = (gltf_path.stat().st_mtime_ns, gltf_path.with_suffix(".bin").stat().st_mtime_ns)
    time.sleep(0.01)
    module.obj_to_gltf(obj_text, "triangle", gltf_path)
    second = (gltf_path.stat().st_mtime_ns, gltf_path.with_suffix(".bin").stat().st_mtime_ns)

    assert second == first


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        test_obj_to_gltf_binds_base_color_texture(Path(tmp))
        test_copy_texture_candidates_with_uri_selects_copied_candidate(Path(tmp))
        test_build_mesh_texture_name_map_reads_renderer_material_main_texture()

from __future__ import annotations

import copy
import csv
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_vfx_preview_manifest.py"
CONFIG = ROOT / "references" / "vfx-preview-config.json"
REQUIRED_FIELDS = ["source_file", "path_id", "asset_type", "asset_name", "export_path"]


def load_module():
    assert SCRIPT.exists(), "VFX preview manifest script is missing"
    spec = importlib.util.spec_from_file_location("build_vfx_preview_manifest", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_index(path: Path) -> None:
    path.parent.mkdir(parents=True)
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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_particle_metadata(index: Path, texture_path_id: str = "102") -> None:
    unitypy_root = index.parent
    particle_dir = unitypy_root / "metadata" / "ParticleSystem"
    particle_dir.mkdir(parents=True)
    (particle_dir / "fire_loop.json").write_text(
        json.dumps(
            {
                "m_Name": "fire_loop",
                "m_Duration": 2.5,
                "m_Looping": True,
                "m_Texture": {"pathID": texture_path_id},
                "InitialModule": {"startLifetime": {"constant": 1.0}, "startSpeed": {"constant": 3.0}},
                "EmissionModule": {"enabled": True, "rateOverTime": {"constant": 12.0}},
                "ShapeModule": {"enabled": True, "type": "Cone", "angle": 25.0, "radius": 0.4},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def preview_index_dir(study_root: Path) -> Path:
    return study_root / "4.临时目录" / "中间索引" / "VFX预览"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_temp_config(tmp_path: Path, mutate: object | None = None) -> Path:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(config)
    config_path = tmp_path / "vfx-preview-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_path


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


def test_build_vfx_manifest_marks_unity_particle_as_dynamic_preview(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
    texture_source = index.parent / "Texture2D" / "fire_particle.png"
    texture_source.parent.mkdir(parents=True)
    texture_source.write_bytes(b"fake-png")

    manifest = module.build_vfx_manifest(tmp_path, CONFIG)

    assert manifest["schema_version"] == 1
    assert manifest["engine"] == "unity"
    assert manifest["study_root"] == str(tmp_path.resolve(strict=False))
    assert manifest["item_count"] == 1
    item = manifest["items"][0]
    assert item["engine"] == "unity"
    assert item["kind"] == "particle_system"
    assert item["preview_status"] == "pending"
    assert item["reconstruction_status"] == "pending"
    assert item["unity_export_status"] == "pending"
    assert item["source_refs"][0]["asset_type"] == "ParticleSystem"
    assert item["dependencies"]["renderers"][0]["asset_type"] == "ParticleSystemRenderer"
    assert item["dependencies"]["textures"][0]["asset_type"] == "Texture2D"
    assert not any(source["asset_type"] == "Texture2D" for source in item["source_refs"])
    assert "main" in item["supported_modules"]
    assert "emission" in item["supported_modules"]
    assert item["reconstruction_relative_path"] == "reconstructed/fire_loop-100.json"
    output_manifest = preview_index_dir(tmp_path) / "manifest.json"
    assert output_manifest.is_file()
    assert (preview_index_dir(tmp_path) / "errors.jsonl").read_text(encoding="utf-8") == ""
    classified_texture = tmp_path / "1.分类资源" / "特效" / "粒子贴图" / "fire" / "fire_particle.png"
    assert classified_texture.read_bytes() == b"fake-png"
    classified_card = tmp_path / "1.分类资源" / "特效" / "配置" / "ParticleSystem" / "fire_loop_100.json"
    assert json.loads(classified_card.read_text(encoding="utf-8"))["asset_type"] == "ParticleSystem"


def test_build_vfx_manifest_passes_only_same_source_cohort_to_each_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    index = tmp_path / "custom" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    rows = [
        {
            "source_file": source_file,
            "path_id": path_id,
            "asset_type": asset_type,
            "asset_name": asset_name,
            "export_path": export_path,
        }
        for source_file, path_id, asset_type, asset_name, export_path in [
            ("bundle_a", "1", "ParticleSystem", "fire", "metadata/a-root.json"),
            ("bundle_a", "2", "Texture2D", "fire_sheet", "Texture2D/a.png"),
            ("bundle_b", "3", "ParticleSystem", "smoke", "metadata/b-root.json"),
            ("bundle_b", "4", "Material", "smoke_mat", "metadata/b-mat.json"),
            ("bundle_c", "5", "Mesh", "unrelated", "metadata/c-mesh.json"),
        ]
    ]
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    calls: list[tuple[str, list[str]]] = []

    class RecordingAdapter:
        def discover_roots(self, candidate_rows: list[dict[str, str]], _unitypy_root: Path):
            return [row for row in candidate_rows if row["asset_type"] == "ParticleSystem"]

        def build_manifest_item(
            self,
            root: dict[str, str],
            candidate_rows: list[dict[str, str]],
            _unitypy_root: Path,
            _study_root: Path,
        ):
            calls.append((root["source_file"], [row["source_file"] for row in candidate_rows]))
            return {"source_file": root["source_file"], "path_id": root["path_id"]}

    monkeypatch.setattr(module, "get_adapter", lambda _engine, _config: RecordingAdapter())

    manifest = module.build_vfx_manifest(tmp_path, CONFIG, index)

    assert manifest["item_count"] == 2
    assert calls == [
        ("bundle_a", ["bundle_a", "bundle_a"]),
        ("bundle_b", ["bundle_b", "bundle_b"]),
    ]


def test_build_vfx_manifest_keeps_dependencies_in_same_source_cohort(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "custom" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    rows = [
        {
            "source_file": source_file,
            "path_id": path_id,
            "asset_type": asset_type,
            "asset_name": asset_name,
            "export_path": export_path,
        }
        for source_file, path_id, asset_type, asset_name, export_path in [
            ("bundle_a", "1", "ParticleSystem", "fire", "metadata/a-root.json"),
            ("bundle_a", "2", "Texture2D", "fire_sheet", "Texture2D/a.png"),
            ("bundle_b", "3", "ParticleSystem", "fire", "metadata/b-root.json"),
            ("bundle_b", "4", "Texture2D", "fire_sheet", "Texture2D/b.png"),
        ]
    ]
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    manifest = module.build_vfx_manifest(tmp_path, CONFIG, index)

    assert manifest["item_count"] == 2
    for item in manifest["items"]:
        dependencies = [
            dependency
            for bucket in item["dependencies"].values()
            for dependency in bucket
        ]
        assert dependencies
        assert {dependency["source_file"] for dependency in dependencies} == {item["source_file"]}


def test_resume_classified_preserves_existing_config_card(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
    card = tmp_path / "1.分类资源" / "特效" / "配置" / "ParticleSystem" / "fire_loop_100.json"
    card.parent.mkdir(parents=True)
    sentinel = b'{"resume_probe":"keep-existing-bytes"}\n'
    card.write_bytes(sentinel)

    module.build_vfx_manifest(tmp_path, CONFIG, index, resume_classified=True)

    assert card.read_bytes() == sentinel


def test_default_classification_rewrites_existing_config_card(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
    card = tmp_path / "1.分类资源" / "特效" / "配置" / "ParticleSystem" / "fire_loop_100.json"
    card.parent.mkdir(parents=True)
    card.write_text('{"resume_probe":"must-be-overwritten"}\n', encoding="utf-8")

    module.build_vfx_manifest(tmp_path, CONFIG, index)

    assert json.loads(card.read_text(encoding="utf-8"))["asset_type"] == "ParticleSystem"


def test_vfx_metadata_cards_for_material_mesh_and_animation_stay_in_config_bucket(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    rows = [
        {
            "asset_id": "mat_1",
            "source_file": "resources.assets",
            "path_id": "200",
            "asset_type": "Material",
            "asset_name": "fire_vfx_material",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Material/fire_vfx_material.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "mesh_1",
            "source_file": "resources.assets",
            "path_id": "201",
            "asset_type": "Mesh",
            "asset_name": "fire_vfx_mesh",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/Mesh/fire_vfx_mesh.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
        {
            "asset_id": "anim_1",
            "source_file": "resources.assets",
            "path_id": "202",
            "asset_type": "AnimationClip",
            "asset_name": "fire_vfx_animation",
            "container_path": "",
            "export_kind": "metadata",
            "export_path": "metadata/AnimationClip/fire_vfx_animation.json",
            "width": "",
            "height": "",
            "status": "exported",
            "remarks": "",
        },
    ]
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        metadata_path = index.parent / row["export_path"]
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps({"m_Name": row["asset_name"], "asset_type": row["asset_type"]}, ensure_ascii=False),
            encoding="utf-8",
        )

    module.build_vfx_manifest(tmp_path, CONFIG, index)

    classified_root = tmp_path / "1.分类资源" / "特效"
    expected_cards = {
        "配置/Material/fire_vfx_material_200.json": "Material",
        "配置/Mesh/fire_vfx_mesh_201.json": "Mesh",
        "配置/AnimationClip/fire_vfx_animation_202.json": "AnimationClip",
    }
    for relative_path, asset_type in expected_cards.items():
        card_path = classified_root / relative_path
        assert card_path.is_file()
        assert json.loads(card_path.read_text(encoding="utf-8"))["asset_type"] == asset_type

    for resource_dir in ["VFX材质", "VFX模型", "VFX动画"]:
        path = classified_root / resource_dir
        json_cards = list(path.rglob("*.json")) if path.exists() else []
        assert json_cards == []


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


def test_importlib_loading_ignores_shadow_scripts_dependencies(tmp_path: Path) -> None:
    shadow_scripts = tmp_path / "scripts"
    shadow_adapters = shadow_scripts / "vfx_adapters"
    shadow_adapters.mkdir(parents=True)
    (shadow_scripts / "__init__.py").write_text("", encoding="utf-8")
    (shadow_scripts / "vfx_preview_common.py").write_text(
        "def load_vfx_config(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n"
        "def vfx_paths(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n"
        "def ensure_inside_root(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n"
        "def write_json_utf8(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n"
        "def write_jsonl_utf8(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n",
        encoding="utf-8",
    )
    (shadow_adapters / "__init__.py").write_text(
        "def get_adapter(*args, **kwargs):\n"
        "    raise AssertionError('shadow adapter imported')\n",
        encoding="utf-8",
    )
    study_root = tmp_path / "study"
    index = study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
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
        manifest = module.build_vfx_manifest(study_root, CONFIG)

        assert manifest["item_count"] == 1
        assert Path(module.load_vfx_config.__code__.co_filename).resolve(strict=False).is_relative_to(ROOT)
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_importlib_loading_ignores_preloaded_fake_unity_adapter_module(tmp_path: Path) -> None:
    study_root = tmp_path / "study"
    index = study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
    fake_unity = types.ModuleType("scripts.vfx_adapters.unity")
    fake_unity.__file__ = str(tmp_path / "outside" / "unity.py")

    class FakeUnityVfxAdapter:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("fake unity adapter imported")

    fake_unity.UnityVfxAdapter = FakeUnityVfxAdapter
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
        sys.modules["scripts.vfx_adapters.unity"] = fake_unity

        module = load_module()
        manifest = module.build_vfx_manifest(study_root, CONFIG)

        assert manifest["item_count"] == 1
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_importlib_loading_preserves_existing_scripts_module(tmp_path: Path) -> None:
    external_scripts = types.ModuleType("scripts")
    external_scripts.__file__ = str(tmp_path / "external" / "__init__.py")
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
        for name in original_modules:
            sys.modules.pop(name, None)
        sys.modules["scripts"] = external_scripts

        load_module()

        assert sys.modules["scripts"] is external_scripts
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_cli_can_run_script_directly_and_write_dynamic_manifest(tmp_path: Path) -> None:
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(tmp_path),
            "--config",
            str(CONFIG),
            "--index",
            str(index),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_count"] == 1
    manifest = json.loads((preview_index_dir(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == "unity"
    assert manifest["source_index"] == str(index.resolve(strict=False))


def test_cli_resume_classified_preserves_existing_config_card(tmp_path: Path) -> None:
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    write_particle_metadata(index)
    card = tmp_path / "1.分类资源" / "特效" / "配置" / "ParticleSystem" / "fire_loop_100.json"
    card.parent.mkdir(parents=True)
    sentinel = b'{"cli_resume_probe":"keep-existing-bytes"}\n'
    card.write_bytes(sentinel)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(tmp_path),
            "--config",
            str(CONFIG),
            "--index",
            str(index),
            "--resume-classified",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_count"] == 1
    assert card.read_bytes() == sentinel


def test_config_card_sanitizes_path_id_without_nested_or_escaped_path(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    rows = list(csv.DictReader(index.read_text(encoding="utf-8").splitlines()))
    rows[0]["path_id"] = "../outside"
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    module.build_vfx_manifest(tmp_path, CONFIG)

    classified_root = tmp_path / "1.分类资源" / "特效"
    assert (classified_root / "配置" / "ParticleSystem" / "fire_loop_outside.json").is_file()
    assert not (classified_root / "配置" / "ParticleSystem" / "fire_loop_.." / "outside.json").exists()
    for output in classified_root.rglob("*"):
        assert output.resolve(strict=False).relative_to(classified_root.resolve(strict=False))


def test_malicious_config_category_is_rejected_without_writing_outside_classified_root(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    config = write_temp_config(
        tmp_path,
        lambda value: value["vfx_type_rules"]["ParticleSystem"].__setitem__("category", "../逃逸"),
    )

    try:
        module.build_vfx_manifest(tmp_path, config)
        assert False, "malicious category should be rejected"
    except ValueError as exc:
        assert "vfx_type_rules.ParticleSystem.category" in str(exc)
    assert not (tmp_path / "1.分类资源" / "逃逸").exists()


def test_missing_required_csv_columns_returns_empty_manifest_and_error(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_file", "path_id", "asset_type", "asset_name"])
        writer.writeheader()
        writer.writerow({
            "source_file": "resources.assets",
            "path_id": "100",
            "asset_type": "ParticleSystem",
            "asset_name": "fire_loop",
        })

    manifest = module.build_vfx_manifest(tmp_path, CONFIG)

    assert manifest["item_count"] == 0
    assert manifest["items"] == []
    errors = read_jsonl(preview_index_dir(tmp_path) / "errors.jsonl")
    assert errors[0]["code"] == "missing_required_columns"
    assert errors[0]["missing"] == ["export_path"]


def test_empty_csv_returns_empty_manifest_and_header_error(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    index.write_text("", encoding="utf-8")

    manifest = module.build_vfx_manifest(tmp_path, CONFIG)

    assert manifest["item_count"] == 0
    assert manifest["items"] == []
    errors = read_jsonl(preview_index_dir(tmp_path) / "errors.jsonl")
    assert errors[0]["code"] == "missing_csv_header"


def test_bom_csv_with_extra_columns_drops_none_key(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    index.parent.mkdir(parents=True)
    index.write_text(
        "\ufeffsource_file,path_id,asset_type,asset_name,export_path\n"
        "resources.assets,100,ParticleSystem,fire_loop,metadata/ParticleSystem/fire_loop.json,ignored-extra\n",
        encoding="utf-8",
    )
    write_particle_metadata(index, texture_path_id="")

    manifest = module.build_vfx_manifest(tmp_path, CONFIG)

    assert manifest["item_count"] == 1
    item = manifest["items"][0]
    assert None not in item["source_refs"][0]
    assert "null" not in json.loads((preview_index_dir(tmp_path) / "manifest.json").read_text(encoding="utf-8"))[
        "items"
    ][0]["source_refs"][0]
    errors = read_jsonl(preview_index_dir(tmp_path) / "errors.jsonl")
    assert errors[0]["code"] == "extra_csv_columns"


def test_custom_index_uses_index_parent_for_metadata_dependencies(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "custom" / "UnityPy全量可读资源" / "custom_index.csv"
    write_index(index)
    write_particle_metadata(index)

    manifest = module.build_vfx_manifest(tmp_path, CONFIG, index)

    assert manifest["source_index"] == str(index.resolve(strict=False))
    assert manifest["items"][0]["dependencies"]["textures"][0]["asset_type"] == "Texture2D"
    assert "missing_texture" not in manifest["items"][0]["warnings"]


def test_index_outside_study_root_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    outside_index = tmp_path.parent / f"{tmp_path.name}_outside.csv"
    outside_index.write_text(",".join(REQUIRED_FIELDS) + "\n", encoding="utf-8")

    try:
        try:
            module.build_vfx_manifest(tmp_path, CONFIG, outside_index)
            assert False, "outside index should be rejected"
        except ValueError as exc:
            assert "index path is outside study-root" in str(exc)
    finally:
        outside_index.unlink(missing_ok=True)


def test_max_items_truncation_is_recorded_in_errors_jsonl(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    rows = list(csv.DictReader(index.read_text(encoding="utf-8").splitlines()))
    second_root = copy.deepcopy(rows[0])
    second_root["asset_id"] = "ps_2"
    second_root["path_id"] = "200"
    second_root["asset_name"] = "spark_loop"
    second_root["export_path"] = "metadata/ParticleSystem/spark_loop.json"
    rows.append(second_root)
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    write_particle_metadata(index)
    spark_dir = index.parent / "metadata" / "ParticleSystem"
    (spark_dir / "spark_loop.json").write_text('{"m_Name": "spark_loop"}', encoding="utf-8")
    config = write_temp_config(tmp_path, lambda value: value["limits"].__setitem__("max_items", 1))

    manifest = module.build_vfx_manifest(tmp_path, config)

    assert manifest["item_count"] == 1
    assert manifest["items"][0]["name"] == "fire_loop"
    errors = read_jsonl(preview_index_dir(tmp_path) / "errors.jsonl")
    assert errors[0] == {"code": "max_items_reached", "limit": 1, "total_roots": 2}


def test_max_items_must_be_positive(tmp_path: Path) -> None:
    module = load_module()
    index = tmp_path / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源" / "all_resources_index.csv"
    write_index(index)
    config = write_temp_config(tmp_path, lambda value: value["limits"].__setitem__("max_items", 0))

    try:
        module.build_vfx_manifest(tmp_path, config)
        assert False, "max_items <= 0 should be rejected"
    except ValueError as exc:
        assert "max_items must be greater than 0" in str(exc)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config["limits"].pop("max_config_depth"), "max_config_depth"),
        (lambda config: config["vfx_type_rules"]["ParticleSystem"].pop("category"), "category"),
        (lambda config: config["vfx_type_rules"]["ParticleSystem"].pop("preview_mode"), "preview_mode"),
    ],
)
def test_classified_vfx_outputs_do_not_use_config_fallbacks(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    module = load_module()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    mutate(config)

    with pytest.raises(KeyError, match=message):
        module.build_classified_vfx_outputs(
            rows=[
                {
                    "asset_id": "ps_1",
                    "source_file": "resources.assets",
                    "path_id": "100",
                    "asset_type": "ParticleSystem",
                    "asset_name": "fire_loop",
                    "export_path": "metadata/ParticleSystem/fire_loop.json",
                }
            ],
            config=config,
            unitypy_root=tmp_path / "unitypy",
            classified_vfx_dir=tmp_path / "classified",
        )

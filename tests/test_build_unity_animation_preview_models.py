from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_unity_animation_preview_models.py"
CONFIG = ROOT / "references" / "model-viewer-gallery-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("build_unity_animation_preview_models", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_fake_unitypy_animation_export(study_root: Path) -> None:
    export_root = study_root / "0.原始导出" / "UnityPy" / "UnityPy全量可读资源"
    metadata_dir = export_root / "metadata" / "AnimationClip"
    metadata_dir.mkdir(parents=True)
    metadata_path = metadata_dir / "fake_1_run.json"
    metadata_path.write_text(
        json.dumps(
            {
                "type_tree": {
                    "m_Name": "hero_run",
                    "m_MuscleClip": {
                        "m_Clip": {
                            "m_StartTime": 0.0,
                            "m_StopTime": 1.5,
                        }
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with (export_root / "all_resources_index.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "asset_id",
                "source_file",
                "path_id",
                "asset_type",
                "asset_name",
                "container_path",
                "export_kind",
                "export_path",
                "width",
                "height",
                "status",
                "remarks",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "asset_id": "fake_1",
                "source_file": "globalgamemanagers.assets",
                "path_id": "1",
                "asset_type": "AnimationClip",
                "asset_name": "hero_run",
                "container_path": "",
                "export_kind": "metadata",
                "export_path": "metadata/AnimationClip/fake_1_run.json",
                "status": "exported",
            }
        )


def write_unitypy_animation_index(
    study_root: Path,
    export_name: str,
    clip_names: list[str],
) -> Path:
    export_root = (
        study_root
        / "0.原始导出"
        / export_name
        / "UnityPy全量可读资源"
    )
    metadata_dir = export_root / "metadata" / "AnimationClip"
    metadata_dir.mkdir(parents=True)
    fieldnames = [
        "asset_id",
        "source_file",
        "path_id",
        "asset_type",
        "asset_name",
        "container_path",
        "export_kind",
        "export_path",
        "width",
        "height",
        "status",
        "remarks",
    ]
    index_path = export_root / "all_resources_index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for path_id, clip_name in enumerate(clip_names, start=1):
            metadata_path = metadata_dir / f"fake_{path_id}_{clip_name}.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "type_tree": {
                            "m_Name": clip_name,
                            "m_StartTime": 0.0,
                            "m_StopTime": 1.0,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            writer.writerow(
                {
                    "asset_id": f"fake_{path_id}",
                    "source_file": f"{export_name}.assets",
                    "path_id": str(path_id),
                    "asset_type": "AnimationClip",
                    "asset_name": clip_name,
                    "export_kind": "metadata",
                    "export_path": metadata_path.relative_to(export_root).as_posix(),
                    "status": "exported",
                }
            )
    return index_path


def test_default_index_selection_prefers_candidate_with_animation_clips(
    tmp_path: Path,
) -> None:
    module = load_module()
    write_unitypy_animation_index(tmp_path, "UnityPy", [])
    richer_index = write_unitypy_animation_index(
        tmp_path,
        "UnityPy_GameFramework解密资源",
        ["hero_idle", "hero_run"],
    )
    config = module.load_model_viewer_config(CONFIG)

    summary = module.build_animation_preview(tmp_path, config)

    assert summary["status"] == "generated"
    assert summary["source_clip_count"] == 2
    assert Path(summary["source_index"]) == richer_index.resolve()


def test_explicit_index_override_is_authoritative(tmp_path: Path) -> None:
    module = load_module()
    legacy_index = write_unitypy_animation_index(
        tmp_path,
        "UnityPy",
        ["legacy_idle"],
    )
    write_unitypy_animation_index(
        tmp_path,
        "UnityPy_GameFramework解密资源",
        ["hero_idle", "hero_run"],
    )
    config = module.load_model_viewer_config(CONFIG)

    summary = module.build_animation_preview(tmp_path, config, legacy_index)

    assert summary["source_clip_count"] == 1
    assert summary["clips"][0]["name"] == "legacy_idle"
    assert Path(summary["source_index"]) == legacy_index.resolve()


def test_explicit_index_rejects_path_outside_study_root(tmp_path: Path) -> None:
    module = load_module()
    outside_index = tmp_path.parent / "outside-all_resources_index.csv"
    outside_index.write_text("asset_type,export_path\n", encoding="utf-8")
    config = module.load_model_viewer_config(CONFIG)

    with pytest.raises(ValueError, match="outside study-root"):
        module.build_animation_preview(tmp_path, config, outside_index)

    assert not (tmp_path / "1.分类资源" / "模型").exists()


def test_parse_args_accepts_index_override(tmp_path: Path) -> None:
    module = load_module()
    args = module.parse_args(
        [
            "--study-root",
            str(tmp_path),
            "--index",
            "0.原始导出/custom/all_resources_index.csv",
        ]
    )

    assert args.index == Path("0.原始导出/custom/all_resources_index.csv")


def test_build_animation_preview_generates_gltf_with_clip(tmp_path: Path) -> None:
    module = load_module()
    write_fake_unitypy_animation_export(tmp_path)
    config = module.load_model_viewer_config(CONFIG)

    summary = module.build_animation_preview(tmp_path, config)

    assert summary["status"] == "generated"
    assert summary["source_clip_count"] == 1
    assert summary["exported_clip_count"] == 1
    gltf = json.loads(Path(summary["model_path"]).read_text(encoding="utf-8"))
    assert gltf["animations"][0]["name"] == "hero_run"
    assert gltf["extras"]["preview_accuracy"].startswith("procedural learning preview")
    assert gltf["extras"]["game_analysis"]["animation_origin"] == "procedural_preview"
    assert gltf["extras"]["game_analysis"]["is_generated_preview"] is True

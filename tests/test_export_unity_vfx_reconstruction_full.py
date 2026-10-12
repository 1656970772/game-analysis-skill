from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "export_unity_vfx_reconstruction_full.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "export_unity_vfx_reconstruction_full",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_nonfinite_curves_are_normalized_without_project_constants() -> None:
    module = load_module()
    result = module.sanitize_and_normalize(
        {
            "scalar": math.inf,
            "minScalar": 5.0,
            "maxCurve": {"m_Curve": []},
        }
    )
    assert result["scalar"] is None
    assert result["constant"] == 5.0
    assert result["constantMin"] == 5.0
    assert result["constantMax"] == 5.0


def test_type_tree_is_unwrapped_and_renderer_metadata_is_attached(
    tmp_path: Path,
) -> None:
    module = load_module()
    particle = tmp_path / "metadata" / "ParticleSystem" / "particle.json"
    renderer = tmp_path / "metadata" / "ParticleSystemRenderer" / "renderer.json"
    particle.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    particle.write_text(
        json.dumps({"type_tree": {"lengthInSec": 3.5}}),
        encoding="utf-8",
    )
    renderer.write_text(
        json.dumps({"type_tree": {"m_RenderMode": 4}}),
        encoding="utf-8",
    )
    item = {
        "root_type": "ParticleSystem",
        "source_refs": [{"export_path": "metadata/ParticleSystem/particle.json"}],
        "dependencies": {
            "renderers": [
                {"export_path": "metadata/ParticleSystemRenderer/renderer.json"}
            ]
        },
    }

    result = module.metadata_for_item(item, tmp_path)
    assert result["duration"] == 3.5
    assert result["ParticleSystemRenderer"]["m_RenderMode"] == 4


def test_reusable_script_contains_no_task_specific_counts_or_paths() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "T32",
        "KuaKeDownload",
        "32494",
        "32685",
        "65179",
        "16853",
    ):
        assert forbidden not in source


def test_full_reconstruction_paths_follow_project_config(tmp_path: Path) -> None:
    module = load_module()

    class FakeExporter:
        @staticmethod
        def vfx_paths(study_root: Path, config: dict[str, Any]) -> Any:
            class Paths:
                preview_index_dir = study_root / "preview"
                manifest = preview_index_dir / "manifest.json"
                unitypy_root = study_root / "unitypy"
                classified_vfx_dir = study_root / "classified"
                unity_export_dir = preview_index_dir / "unity-export"

            return Paths()

        @staticmethod
        def _configured_study_path(
            study_root: Path, value: Any, fallback: Path
        ) -> Path:
            return study_root / Path(str(value)) if value else study_root / fallback

    config = {
        "paths": {
            "full_structure_index_dir": "branch/structure",
            "assetripper_assets_root": "branch/assetripper/Assets",
        }
    }

    paths = module.resolve_full_paths(FakeExporter(), tmp_path, config)

    assert paths["structure_dir"] == tmp_path / "branch/structure"
    assert paths["assets_root"] == tmp_path / "branch/assetripper/Assets"
    assert paths["preview_dir"] == tmp_path / "preview"


def test_copy_and_json_writes_preserve_mtime_for_equal_content(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    metadata = tmp_path / "metadata.json"
    source.write_bytes(b"stable")

    module.copy_if_changed(source, destination)
    module.write_json_if_changed(metadata, {"ok": True})
    first = (destination.stat().st_mtime_ns, metadata.stat().st_mtime_ns)
    module.copy_if_changed(source, destination)
    module.write_json_if_changed(metadata, {"ok": True})

    assert (destination.stat().st_mtime_ns, metadata.stat().st_mtime_ns) == first

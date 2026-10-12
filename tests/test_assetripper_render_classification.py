import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "organize_assetripper_render_resources.py"
CONFIG = ROOT / "references" / "assetripper-render-classification.json"


def test_game_model_viewer_hash_matches_tooling_manifest() -> None:
    manifest = json.loads(
        (ROOT / "references" / "tooling-manifest.json").read_text(encoding="utf-8")
    )
    tool = next(item for item in manifest["tools"] if item["name"] == "GameModelViewer")
    executable = ROOT / tool["local_path"]

    assert executable.is_file()
    actual_hash = hashlib.sha256(executable.read_bytes()).hexdigest().upper()
    assert actual_hash == tool["sha256"]


def test_render_classification_contract_is_configuration_driven() -> None:
    assert SCRIPT.is_file()
    assert CONFIG.is_file()

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    mappings = {
        extension: category["name"]
        for category in config["categories"]
        for extension in category["extensions"]
    }
    assert mappings == {
        ".mat": "Material",
        ".shader": "Shader",
        ".rendertexture": "RenderTexture",
    }


def test_render_resources_are_classified_with_relative_paths_and_idempotence(
    tmp_path: Path,
) -> None:
    study_root = tmp_path / "study"
    assets_root = (
        study_root / "0.原始导出" / "AssetRipper" / "ExportedProject" / "Assets"
    )
    fixtures = {
        "Art/Materials/Hero.mat": b"material",
        "Rendering/Shaders/Outline.shader": b"shader",
        "Scenes/Lighting/Reflection.rendertexture": b"rendertexture",
    }
    for relative_path, payload in fixtures.items():
        path = assets_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        Path(f"{path}.meta").write_text("metadata", encoding="utf-8")
    (assets_root / "ignored.json").write_text("{}", encoding="utf-8")

    command = [
        sys.executable,
        str(SCRIPT),
        "--study-root",
        str(study_root),
        "--config",
        str(CONFIG),
    ]
    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stderr

    summary_path = (
        study_root / "4.临时目录" / "中间索引" / "渲染分类" / "渲染分类摘要.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["source_candidate_count"] == 3
    assert summary["classified_file_count"] == 3
    assert summary["missing_count"] == 0
    assert summary["extra_count"] == 0
    assert summary["conflict_count"] == 0

    expected_destinations = {
        "Material/Art/Materials/Hero.mat": assets_root / "Art/Materials/Hero.mat",
        "Shader/Rendering/Shaders/Outline.shader": assets_root
        / "Rendering/Shaders/Outline.shader",
        "RenderTexture/Scenes/Lighting/Reflection.rendertexture": assets_root
        / "Scenes/Lighting/Reflection.rendertexture",
    }
    classified_root = study_root / "1.分类资源" / "渲染"
    for relative_path, source in expected_destinations.items():
        destination = classified_root / relative_path
        assert destination.read_bytes() == source.read_bytes()
        if summary["hardlinked_count"]:
            assert os.path.samefile(source, destination)

    unexpected = [
        path
        for path in classified_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() not in {".mat", ".shader", ".rendertexture"}
    ]
    assert unexpected == []

    second = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert second.returncode == 0, second.stderr
    rerun_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert rerun_summary["created_count"] == 0
    assert rerun_summary["reused_count"] == 3
    assert rerun_summary["classified_file_count"] == 3
    assert rerun_summary["conflict_count"] == 0

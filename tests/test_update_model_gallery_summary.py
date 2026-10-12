from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_model_gallery_summary.py"
CONFIG = ROOT / "references" / "model-viewer-gallery-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("update_model_gallery_summary", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summary_separates_real_animated_models_from_procedural_previews(tmp_path: Path) -> None:
    module = load_module()
    manifest_path = tmp_path / "4.临时目录" / "中间索引" / "模型预览" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "preview_status": "ready",
                        "stats": {"animation_count": 2},
                        "animation_origin": "embedded",
                    },
                    {
                        "preview_status": "ready",
                        "stats": {"animation_count": 48},
                        "animation_origin": "procedural_preview",
                        "is_generated_animation_preview": True,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = module.load_model_viewer_config(CONFIG)

    overview = module.update_summary(
        tmp_path,
        manifest_path,
        config,
        viewer_exists=True,
        launcher_exists=True,
    )

    text = overview.read_text(encoding="utf-8")
    assert "- 含真实导出动作模型：1" in text
    assert "- 程序化动作预览模型：1" in text
    assert "程序化动作预览不计入真实导出动作模型" in text

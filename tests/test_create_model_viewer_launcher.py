from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create_model_viewer_launcher.py"
CONFIG = ROOT / "references" / "model-viewer-gallery-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("create_model_viewer_launcher", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_create_launcher_writes_primary_bat_and_removes_deprecated_alias(tmp_path: Path) -> None:
    module = load_module()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    tool_dir = tmp_path / config["tool"]["directory"]
    tool_dir.mkdir(parents=True)
    (tool_dir / config["tool"]["exe_name"]).write_text("viewer", encoding="utf-8")
    report_dir = tmp_path / config["paths"]["report_dir"]
    alias = report_dir / "模型预览.bat"
    alias.parent.mkdir(parents=True)
    alias.write_text("deprecated", encoding="utf-8")

    primary = module.create_launcher(tmp_path, tmp_path, config)

    assert primary == report_dir / "打开模型预览.bat"
    assert primary.exists()
    assert not alias.exists()

from __future__ import annotations

from pathlib import Path


def test_electron_template_knows_report_gallery_and_standard_roots() -> None:
    main_js = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "electron-gallery-viewer"
        / "main.js"
    ).read_text(encoding="utf-8")

    assert "2.报告" in main_js
    assert "全量图片画廊.html" in main_js
    assert "1.分类资源" in main_js
    assert "4.临时目录" in main_js
    assert "_ascii_work" in main_js
    assert "process.exitCode = 1" in main_js


def test_gallery_exe_build_uses_npm_cmd_on_windows() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_gallery_exe.ps1"
    ).read_text(encoding="utf-8")

    assert "npm.cmd" in script
    assert "Invoke-Npm" in script

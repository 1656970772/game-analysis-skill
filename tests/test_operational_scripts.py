from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_run_skill_tests_uses_skill_python_env_and_pytest() -> None:
    script = read_script("run_skill_tests.ps1")

    assert "tools\\python-env\\Scripts\\python.exe" in script
    assert "tools\\python-env\\Lib\\site-packages" in script
    assert "UnityPy" in script
    assert "pytest" in script
    assert "PYTHONPATH" in script
    assert '$env:PYTHONUTF8 = "1"' in script


def test_verify_model_viewer_gui_uses_direct_cdp_and_quoted_argument_line() -> None:
    script = read_script("verify_model_viewer_gui.ps1")

    assert "WebSocket" in script
    assert "Runtime.evaluate" in script
    assert "Quote-ProcessArgument" in script
    assert "-ArgumentList $argumentLine" in script
    assert "Start-Process" in script
    assert "Stop-NewGameModelViewerProcesses" in script
    assert "animation-select" in script


def test_verify_model_viewer_local_cdp_requests_explicitly_bypass_http_proxy() -> None:
    script = read_script("verify_model_viewer_gui.ps1")

    assert '-Uri "http://127.0.0.1:$Port/json/list"' in script
    assert (
        '$response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/list" '
        "-UseBasicParsing -TimeoutSec 1 -NoProxy"
    ) in script
    assert "$PSVersionTable.PSVersion.Major -lt 7" in script


def test_verify_model_viewer_gui_can_check_full_body_preview_button() -> None:
    script = read_script("verify_model_viewer_gui.ps1")

    assert "[switch]$ExpectFullBodyPreview" in script
    assert "GAME_ANALYSIS_EXPECT_FULL_BODY_PREVIEW" in script
    assert "full-body-preview" in script
    assert "全身预览" in script
    assert "单件预览" in script


def test_model_viewer_build_uses_ascii_default_build_root() -> None:
    script = read_script("build_model_viewer_tool.ps1")

    assert "tools\\_build\\GameModelViewer_$ToolVersion" in script
    assert "4.临时目录\\工具构建" not in script


def test_assetripper_project_export_runs_headless_and_logs_api_steps() -> None:
    script = read_script("export_unity_assetripper_project.ps1")

    assert '"--headless"' in script
    assert "AsciiWorkRoot" in script
    assert "runtime_log_path" in script
    assert "/openapi.json" in script
    assert "/LoadFolder" in script
    assert "/Export/UnityProject" in script
    assert "assetripper-api-log.json" in script
    assert "-WindowStyle Hidden" in script


def test_assetripper_local_api_requests_explicitly_bypass_http_proxy() -> None:
    script = read_script("export_unity_assetripper_project.ps1")

    assert "function Invoke-LocalApiWebRequest" in script
    assert "Invoke-WebRequest @Request -NoProxy" in script
    assert script.count("Invoke-WebRequest") == 1


def test_assetripper_exports_into_raw_parent_and_reports_generated_project() -> None:
    script = read_script("export_unity_assetripper_project.ps1")

    assert "$exportRequestPath = $rawRoot" in script
    assert '$exportPath = Join-Path $exportRequestPath "ExportedProject"' in script
    assert "Ensure-Directory -Path $exportRequestPath" in script
    assert "Ensure-Directory -Path $exportPath" not in script
    assert script.count("@{ path = $exportRequestPath }") == 2
    assert "Test-Path -LiteralPath $exportPath -PathType Container" in script
    assert "export_path = $exportPath" in script

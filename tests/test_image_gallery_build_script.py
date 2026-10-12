from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_image_gallery_viewer_exe.ps1"
TOOLING_MANIFEST = ROOT / "references" / "tooling-manifest.json"


def test_build_script_targets_fixed_gallery_exe_and_template() -> None:
    assert BUILD_SCRIPT.exists(), "fixed image gallery build script is missing"
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '[string]$SkillRoot = ""' in script
    assert '[string]$Version = "1.0.0"' in script
    assert '$ErrorActionPreference = "Stop"' in script
    assert "assets\\image-gallery-viewer-src" in script
    assert "built-in-tools\\GameImageGallery_$Version" in script
    assert "tools\\_build\\GameImageGallery" in script
    assert "StudyRoot" not in script


def test_build_script_uses_npm_cmd_wrapper_and_required_copy_pipeline() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'Get-Command "npm.cmd"' in script
    assert "Get-Command \"npm\"" in script
    assert "function Invoke-Npm" in script
    assert "Invoke-Npm install" in script
    assert "Invoke-Npm run dist" in script
    assert "Get-ChildItem -LiteralPath $templateDir | Copy-Item -Destination $workDir -Recurse -Force" in script
    assert "Join-Path $templateDir" not in script


def test_build_script_packages_win_unpacked_without_console_subsystem_patch() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '$unpackedDir = Join-Path $workDir "dist\\win-unpacked"' in script
    assert "Get-ChildItem -LiteralPath $unpackedDir -Force | Copy-Item -Destination $targetDir -Recurse -Force" in script
    assert '$target = Join-Path $targetDir "GameImageGallery.exe"' in script
    assert "Set-PEConsoleSubsystem" not in script
    assert "[UInt16]3" not in script
    assert '-Filter "*.exe"' not in script
    assert "Sort-Object LastWriteTime" not in script


def test_tooling_manifest_declares_single_fixed_game_image_gallery_tool() -> None:
    manifest = json.loads(TOOLING_MANIFEST.read_text(encoding="utf-8"))
    entries = [tool for tool in manifest["tools"] if tool.get("name") == "GameImageGallery"]

    assert len(entries) == 1
    assert entries[0]["version"] == "1.0.0"
    assert entries[0]["local_path"] == "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe"
    assert entries[0]["source"] == "Local fixed Electron image gallery built from assets/image-gallery-viewer-src."
    assert (
        entries[0]["purpose"]
        == "Open GameAnalysis study-root image manifests through a fixed local Windows EXE. Each project generates only a BAT launcher, manifest, thumbnails, and error logs."
    )

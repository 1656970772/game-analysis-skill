from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "install_game_character_animation_viewer_tool.ps1"


def run_checked(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


@pytest.mark.skipif(shutil.which("pwsh") is None or shutil.which("git") is None, reason="pwsh and git are required")
def test_installs_versioned_assetarchive_artifact_with_provenance_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "AssetArchive-Dev"
    artifact = source / "src-tauri" / "target" / "release" / "Asset Archive.exe"
    artifact.parent.mkdir(parents=True)
    artifact_bytes = b"fixture tauri executable"
    artifact.write_bytes(artifact_bytes)
    (source / "LICENSE").write_text("MIT License\nfixture\n", encoding="utf-8")

    run_checked(["git", "init", "-q"], cwd=source)
    run_checked(["git", "config", "user.email", "test@example.invalid"], cwd=source)
    run_checked(["git", "config", "user.name", "GameAnalysis Test"], cwd=source)
    run_checked(["git", "add", "."], cwd=source)
    run_checked(["git", "commit", "-q", "-m", "fixture"], cwd=source)
    commit = run_checked(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()

    skill = tmp_path / "skill"
    references = skill / "references"
    references.mkdir(parents=True)
    config = {
        "schema_version": 1,
        "tool": {
            "name": "GameCharacterAnimationViewer",
            "version": "0.1.0",
            "executable_name": "GameCharacterAnimationViewer.exe",
            "install_relative": "built-in-tools/GameCharacterAnimationViewer_0.1.0",
            "license_name": "LICENSE.AssetArchive.txt",
            "origin_name": "ORIGIN.json",
            "version_name": "VERSION.txt",
        },
        "source_repository": {
            "default_path": str(source),
            "license_relative": "LICENSE",
            "artifact_candidates": ["src-tauri/target/release/Asset Archive.exe"],
        },
        "build": {
            "command": "npm.cmd",
            "working_directory_relative": ".",
            "arguments": ["run", "tauri", "build"],
            "process_environment": {"CMAKE_POLICY_VERSION_MINIMUM": "3.5"},
        },
        "launch": {},
    }
    config_path = references / "game-character-animation-viewer-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (references / "tooling-manifest.json").write_text(
        json.dumps({"distribution": "fixture", "auto_download": False, "tools": []}),
        encoding="utf-8",
    )

    result = run_checked(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-SkillRoot",
            str(skill),
            "-ConfigPath",
            str(config_path),
            "-SourceRoot",
            str(source),
            "-ArtifactPath",
            str(artifact),
        ]
    )

    payload = json.loads(result.stdout)
    target = (
        skill
        / "built-in-tools"
        / "GameCharacterAnimationViewer_0.1.0"
        / "GameCharacterAnimationViewer.exe"
    )
    expected_hash = hashlib.sha256(artifact_bytes).hexdigest().upper()
    assert payload["status"] == "installed"
    assert payload["sha256"] == expected_hash
    assert target.read_bytes() == artifact_bytes
    assert (target.parent / "LICENSE.AssetArchive.txt").is_file()
    assert (target.parent / "VERSION.txt").read_text(encoding="utf-8").strip() == "0.1.0"

    origin = json.loads((target.parent / "ORIGIN.json").read_text(encoding="utf-8"))
    assert origin["source_commit"] == commit
    assert origin["source_dirty"] is False
    assert origin["build_invoked_by_installer"] is False
    assert origin["build_process_environment"] == {"CMAKE_POLICY_VERSION_MINIMUM": "3.5"}
    assert origin["artifact_sha256"] == expected_hash
    assert origin["installed_executable_sha256"] == expected_hash

    tooling = json.loads((references / "tooling-manifest.json").read_text(encoding="utf-8"))
    entry = next(tool for tool in tooling["tools"] if tool["name"] == "GameCharacterAnimationViewer")
    assert entry["version"] == "0.1.0"
    assert entry["source_commit"] == commit
    assert entry["sha256"] == expected_hash
    assert entry["build_process_environment"] == {"CMAKE_POLICY_VERSION_MINIMUM": "3.5"}
    assert entry["local_path"] == (
        "built-in-tools/GameCharacterAnimationViewer_0.1.0/GameCharacterAnimationViewer.exe"
    )


@pytest.mark.skipif(shutil.which("pwsh") is None or shutil.which("git") is None, reason="pwsh and git are required")
def test_build_switch_scopes_cmake_compatibility_environment_to_build_process(tmp_path: Path) -> None:
    source = tmp_path / "AssetArchive-Dev"
    source.mkdir()
    artifact = source / "src-tauri" / "target" / "release" / "Asset Archive.exe"
    observed = source / "observed-cmake-policy.txt"
    build_fixture = source / "build-fixture.ps1"
    build_fixture.write_text(
        "param([string]$Artifact, [string]$Observed)\n"
        "New-Item -ItemType Directory -Path (Split-Path -Parent $Artifact) -Force | Out-Null\n"
        "[IO.File]::WriteAllBytes($Artifact, [Text.Encoding]::UTF8.GetBytes('built artifact'))\n"
        "[IO.File]::WriteAllText($Observed, $env:CMAKE_POLICY_VERSION_MINIMUM)\n",
        encoding="utf-8",
    )
    (source / "LICENSE").write_text("MIT License\nfixture\n", encoding="utf-8")
    run_checked(["git", "init", "-q"], cwd=source)
    run_checked(["git", "config", "user.email", "test@example.invalid"], cwd=source)
    run_checked(["git", "config", "user.name", "GameAnalysis Test"], cwd=source)
    run_checked(["git", "add", "."], cwd=source)
    run_checked(["git", "commit", "-q", "-m", "fixture"], cwd=source)

    skill = tmp_path / "skill"
    references = skill / "references"
    references.mkdir(parents=True)
    config = {
        "schema_version": 1,
        "tool": {
            "name": "GameCharacterAnimationViewer",
            "version": "0.1.0",
            "executable_name": "GameCharacterAnimationViewer.exe",
            "install_relative": "built-in-tools/GameCharacterAnimationViewer_0.1.0",
            "license_name": "LICENSE.AssetArchive.txt",
            "origin_name": "ORIGIN.json",
            "version_name": "VERSION.txt",
        },
        "source_repository": {
            "default_path": str(source),
            "license_relative": "LICENSE",
            "artifact_candidates": ["src-tauri/target/release/Asset Archive.exe"],
        },
        "build": {
            "command": "pwsh",
            "working_directory_relative": ".",
            "arguments": [
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(build_fixture),
                "-Artifact",
                str(artifact),
                "-Observed",
                str(observed),
            ],
            "process_environment": {"CMAKE_POLICY_VERSION_MINIMUM": "3.5"},
        },
        "launch": {},
    }
    config_path = references / "game-character-animation-viewer-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    (references / "tooling-manifest.json").write_text(
        json.dumps({"distribution": "fixture", "auto_download": False, "tools": []}),
        encoding="utf-8",
    )

    result = run_checked(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-SkillRoot",
            str(skill),
            "-ConfigPath",
            str(config_path),
            "-SourceRoot",
            str(source),
            "-Build",
        ]
    )

    payload = json.loads(result.stdout)
    target = (
        skill
        / "built-in-tools"
        / "GameCharacterAnimationViewer_0.1.0"
        / "GameCharacterAnimationViewer.exe"
    )
    assert payload["status"] == "installed"
    assert observed.read_text(encoding="utf-8") == "3.5"
    assert target.read_bytes() == b"built artifact"
    origin = json.loads((target.parent / "ORIGIN.json").read_text(encoding="utf-8"))
    assert origin["build_invoked_by_installer"] is True
    assert origin["build_process_environment"] == {"CMAKE_POLICY_VERSION_MINIMUM": "3.5"}

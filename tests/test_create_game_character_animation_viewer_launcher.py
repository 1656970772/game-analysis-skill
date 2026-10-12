from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "create_game_character_animation_viewer_launcher.py"
LAUNCHER_RELATIVE = Path("2.报告") / "打开动画预览.bat"


def create_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    study_root = tmp_path / "学习项目"
    skill_root = tmp_path / "skill"
    study_root.mkdir()
    config = {
        "schema_version": 1,
        "tool": {
            "name": "GameCharacterAnimationViewer",
            "version": "0.1.0",
            "executable_name": "GameCharacterAnimationViewer.exe",
            "install_relative": "built-in-tools/GameCharacterAnimationViewer_0.1.0",
        },
        "launch": {
            "game_root_environment": "GAME_ANALYSIS_DST_ROOT",
            "launcher_relative": "2.报告/打开动画预览.bat",
            "log_relative": "4.临时目录/中间索引/游戏动画预览/viewer-launch.log",
            "project_config_relative": (
                "4.临时目录/中间索引/游戏动画预览/character-viewer-project.json"
            ),
            "support_script_relative": "scripts/launch_game_character_animation_viewer.ps1",
        },
    }
    config_path = skill_root / "references" / "game-character-animation-viewer-config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    viewer = (
        skill_root
        / "built-in-tools"
        / "GameCharacterAnimationViewer_0.1.0"
        / "GameCharacterAnimationViewer.exe"
    )
    viewer.parent.mkdir(parents=True)
    viewer.write_bytes(b"character viewer")
    support = skill_root / "scripts" / "launch_game_character_animation_viewer.ps1"
    support.parent.mkdir(parents=True)
    support.write_text("# fixture\n", encoding="utf-8")

    game_root = tmp_path / "Steam Library" / "Don't Starve Together"
    for name in ("anim", "bigportraits", "images", "minimap", "sound"):
        (game_root / "data" / name).mkdir(parents=True, exist_ok=True)
    return study_root, skill_root, game_root, config_path


def run_launcher(
    study_root: Path,
    skill_root: Path,
    *,
    game_root: Path | None = None,
    project_config: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--study-root",
        str(study_root),
        "--skill-root",
        str(skill_root),
    ]
    if game_root is not None:
        command.extend(["--game-root", str(game_root)])
    if project_config is not None:
        command.extend(["--project-config", str(project_config)])
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def test_creates_character_first_launcher_without_legacy_manifest(tmp_path: Path) -> None:
    study_root, skill_root, game_root, _config = create_layout(tmp_path)

    first = run_launcher(study_root, skill_root, game_root=game_root)
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload["status"] == "created"
    assert payload["game_root"] == str(game_root.resolve())
    assert payload["game_root_source"] == "explicit"

    launcher = study_root / LAUNCHER_RELATIVE
    content = launcher.read_text(encoding="utf-8-sig")
    viewer = (
        skill_root
        / "built-in-tools"
        / "GameCharacterAnimationViewer_0.1.0"
        / "GameCharacterAnimationViewer.exe"
    ).resolve()
    support = (skill_root / "scripts" / "launch_game_character_animation_viewer.ps1").resolve()
    assert f'set "GA_CHARACTER_VIEWER={viewer}"' in content
    assert f'set "GA_CHARACTER_LAUNCHER={support}"' in content
    assert f'set "GAME_ANALYSIS_DST_ROOT={game_root.resolve()}"' in content
    assert "powershell.exe -NoLogo -NoProfile" in content
    assert "-GameRoot \"%GAME_ANALYSIS_DST_ROOT%\"" in content
    assert '>>"%GA_LAUNCH_LOG%"' not in content
    assert "GameAnimationViewer_1.0.1" not in content
    assert "manifest.json" not in content
    assert "--manifest" not in content
    assert "pause" in content

    before = launcher.stat().st_mtime_ns
    second = run_launcher(study_root, skill_root, game_root=game_root)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "reused"
    assert launcher.stat().st_mtime_ns == before


def test_reads_explicit_game_root_from_project_config(tmp_path: Path) -> None:
    study_root, skill_root, game_root, _config = create_layout(tmp_path)
    project_config = (
        study_root
        / "4.临时目录"
        / "中间索引"
        / "游戏动画预览"
        / "character-viewer-project.json"
    )
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        json.dumps({"schema_version": 1, "game_root": str(game_root)}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["game_root"] == str(game_root.resolve())
    assert payload["game_root_source"] == str(project_config.resolve())


def test_rejects_missing_or_invalid_dst_root_without_overwriting_launcher(tmp_path: Path) -> None:
    study_root, skill_root, _game_root, _config = create_layout(tmp_path)
    launcher = study_root / LAUNCHER_RELATIVE
    launcher.parent.mkdir(parents=True)
    launcher.write_text("existing launcher", encoding="utf-8")

    missing = run_launcher(study_root, skill_root)
    assert missing.returncode == 1
    assert "DST game root is required" in missing.stderr
    assert launcher.read_text(encoding="utf-8") == "existing launcher"

    invalid_root = tmp_path / "invalid game"
    invalid_root.mkdir()
    invalid = run_launcher(study_root, skill_root, game_root=invalid_root)
    assert invalid.returncode == 1
    assert "missing required data directories" in invalid.stderr
    assert launcher.read_text(encoding="utf-8") == "existing launcher"

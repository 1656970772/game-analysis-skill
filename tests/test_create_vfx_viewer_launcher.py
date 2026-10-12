from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create_vfx_viewer_launcher.py"
CONFIG = ROOT / "references" / "vfx-preview-config.json"


def load_module():
    spec = importlib.util.spec_from_file_location("create_vfx_viewer_launcher", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_config(tmp_path: Path, **launcher_overrides: str) -> dict[str, object]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["launcher"] = {**config["launcher"], **launcher_overrides}
    config_path = tmp_path / "vfx-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return {"config": config, "path": config_path}


def win(path: Path) -> str:
    return str(path).replace("/", "\\")


def bat_literal(path: Path) -> str:
    return win(path).replace("^", "^^").replace("%", "%%")


def run_bat(path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["cmd.exe", "/d", "/c", str(path)],
        cwd=path.parent,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_create_vfx_launcher_writes_bat_with_manifest_and_unity_export_args(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(
        tmp_path,
        environment_exe="GAME_ANALYSIS_VFX_VIEWER",
        environment_skill_root="GAME_ANALYSIS_SKILL_ROOT",
    )
    config = payload["config"]
    config_path = payload["path"]
    assert isinstance(config, dict)
    assert isinstance(config_path, Path)

    study_root = tmp_path / "测试 项目 100%"
    skill_root = tmp_path / "Skill Root 100%"
    tool_dir = skill_root / config["tool"]["directory"]
    tool_dir.mkdir(parents=True)
    (tool_dir / config["tool"]["exe_name"]).write_text("viewer", encoding="utf-8")
    manifest = study_root / config["paths"]["preview_index_dir"] / config["paths"]["manifest_name"]
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"items":[]}', encoding="utf-8")

    bat_path = module.create_launcher(study_root, skill_root, config_path)

    text = bat_path.read_text(encoding="utf-8")
    assert bat_path == study_root / config["paths"]["report_dir"] / config["launcher"]["bat_name"]
    assert f'set "STUDY_ROOT={bat_literal(study_root.resolve(strict=False))}"' in text
    assert f'set "SKILL_ROOT={bat_literal(skill_root.resolve(strict=False))}"' in text
    assert "GAME_ANALYSIS_VFX_VIEWER" in text
    assert "GAME_ANALYSIS_SKILL_ROOT" in text
    assert f'set "VIEWER_EXE={bat_literal(skill_root.resolve(strict=False) / config["tool"]["directory"] / config["tool"]["exe_name"])}"' in text
    assert f'--manifest "{bat_literal(manifest.resolve(strict=False))}"' in text
    assert f'--reconstructed-dir "{bat_literal(manifest.parent / config["paths"]["reconstructed_dir"])}"' in text
    assert f'--preview-cache-dir "{bat_literal(manifest.parent / config["paths"]["preview_cache_dir"])}"' in text
    assert f'--unity-export-dir "{bat_literal(manifest.parent / config["paths"]["unity_export_dir"])}"' in text
    assert f'set "GAME_ANALYSIS_VFX_MANIFEST={bat_literal(manifest.resolve(strict=False))}"' in text
    assert f'set "GAME_ANALYSIS_VFX_RECONSTRUCTED_DIR={bat_literal(manifest.parent / config["paths"]["reconstructed_dir"])}"' in text
    assert f'set "GAME_ANALYSIS_VFX_PREVIEW_CACHE_DIR={bat_literal(manifest.parent / config["paths"]["preview_cache_dir"])}"' in text
    assert f'set "GAME_ANALYSIS_VFX_UNITY_EXPORT_DIR={bat_literal(manifest.parent / config["paths"]["unity_export_dir"])}"' in text
    assert 'setlocal EnableExtensions EnableDelayedExpansion' in text
    assert '--study-root "!STUDY_ROOT!"' in text


def test_create_vfx_launcher_uses_configured_launcher_fields(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(
        tmp_path,
        bat_name="custom-vfx-launcher.bat",
        environment_exe="CUSTOM_VFX_VIEWER",
        environment_skill_root="CUSTOM_SKILL_ROOT",
    )

    bat_path = module.create_launcher(tmp_path / "study", tmp_path / "skill", payload["path"])
    text = bat_path.read_text(encoding="utf-8")

    assert bat_path.name == "custom-vfx-launcher.bat"
    assert "CUSTOM_VFX_VIEWER" in text
    assert "CUSTOM_SKILL_ROOT" in text
    assert "GAME_ANALYSIS_VFX_VIEWER" not in text


def test_cli_generates_launcher_and_outputs_ascii_json(tmp_path: Path) -> None:
    payload = write_config(
        tmp_path,
        environment_exe="GAME_ANALYSIS_VFX_VIEWER",
        environment_skill_root="GAME_ANALYSIS_SKILL_ROOT",
    )
    study_root = tmp_path / "study cli"
    skill_root = tmp_path / "skill cli"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(study_root),
            "--skill-root",
            str(skill_root),
            "--config",
            str(payload["path"]),
        ],
        cwd=ROOT,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    result.stdout.encode("ascii")
    output = json.loads(result.stdout)
    assert Path(output["launcher"]) == study_root / payload["config"]["paths"]["report_dir"] / payload["config"]["launcher"]["bat_name"]
    assert Path(output["launcher"]).is_file()


def test_create_launcher_uses_default_config_when_config_path_is_none(tmp_path: Path) -> None:
    module = load_module()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    launcher = module.create_launcher(tmp_path / "study default", ROOT, None)

    assert launcher == tmp_path / "study default" / config["paths"]["report_dir"] / config["launcher"]["bat_name"]
    assert launcher.is_file()


def test_cli_uses_default_config_when_config_arg_is_omitted(tmp_path: Path) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    study_root = tmp_path / "study default cli"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--study-root",
            str(study_root),
            "--skill-root",
            str(ROOT),
        ],
        cwd=ROOT,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    result.stdout.encode("ascii")
    output = json.loads(result.stdout)
    assert Path(output["launcher"]) == study_root / config["paths"]["report_dir"] / config["launcher"]["bat_name"]
    assert Path(output["launcher"]).is_file()


def test_bat_handles_cmd_metacharacters_without_echoing_dynamic_viewer_path(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(
        tmp_path,
        environment_exe="GAME_ANALYSIS_VFX_VIEWER",
        environment_skill_root="GAME_ANALYSIS_SKILL_ROOT",
    )

    bat_path = module.create_launcher(tmp_path / "study & ^ percent 100%", tmp_path / "skill & ^ |", payload["path"])
    text = bat_path.read_text(encoding="utf-8")

    assert 'echo 错误：找不到特效预览器。' in text
    assert 'echo 请检查环境变量或默认工具路径。' in text
    assert not any("%VIEWER_EXE%" in line for line in text.splitlines() if line.strip().startswith("echo "))
    assert "study & ^^ percent 100%%" in text
    assert "skill & ^^ |" in text


def test_bat_does_not_execute_malicious_runtime_env_var_values(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(tmp_path)
    bat_path = module.create_launcher(tmp_path / "study", tmp_path / "skill", payload["path"])
    marker = tmp_path / "pwned.txt"
    env = os.environ.copy()
    env["GAME_ANALYSIS_VFX_VIEWER"] = f'missing.exe" & echo PWNED > "{marker}" & rem'
    env["GAME_ANALYSIS_VFX_LAUNCHER_NO_PAUSE"] = "1"

    result = run_bat(bat_path, env)

    assert result.returncode == 1
    assert not marker.exists()
    assert "PWNED" not in result.stdout
    assert "PWNED" not in result.stderr


def test_bat_runtime_env_override_accepts_safe_exe_viewer(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(tmp_path)
    bat_path = module.create_launcher(tmp_path / "study", tmp_path / "skill", payload["path"])
    viewer = Path(r"C:\Windows\System32\where.exe")
    assert viewer.is_file()
    env = os.environ.copy()
    env["GAME_ANALYSIS_VFX_VIEWER"] = str(viewer)
    env["GAME_ANALYSIS_VFX_LAUNCHER_NO_PAUSE"] = "1"

    result = run_bat(bat_path, env)

    assert result.returncode == 0, result.stderr
    assert "找不到特效预览器" not in result.stdout


def test_create_vfx_launcher_rejects_bat_values_with_quotes(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(
        tmp_path,
        environment_exe="GAME_ANALYSIS_VFX_VIEWER",
        environment_skill_root="GAME_ANALYSIS_SKILL_ROOT",
    )

    with pytest.raises(ValueError, match="BAT value contains unsupported quote"):
        module.create_launcher(Path('C:/bad"study'), tmp_path / "skill", payload["path"])


@pytest.mark.parametrize(
    ("unsafe_path", "message"),
    [
        (Path(r"C:\tmp\study!SECRET!"), "unsupported exclamation mark"),
        (Path('C:/tmp/study"SECRET"'), "unsupported quote"),
        (Path("C:/tmp/study\nSECRET"), "unsupported control character"),
    ],
)
def test_bat_value_errors_do_not_leak_original_input(
    unsafe_path: Path,
    message: str,
) -> None:
    module = load_module()

    with pytest.raises(ValueError, match=message) as exc_info:
        module.bat_value(unsafe_path)

    error = str(exc_info.value)
    assert "SECRET" not in error
    assert "study" not in error
    assert "C:" not in error


def test_create_vfx_launcher_rejects_bang_in_bat_values(tmp_path: Path) -> None:
    module = load_module()
    payload = write_config(tmp_path)

    with pytest.raises(ValueError, match="BAT value contains unsupported exclamation mark"):
        module.create_launcher(tmp_path / "study!bang", tmp_path / "skill", payload["path"])


def test_importlib_load_does_not_mutate_sys_path() -> None:
    original = sys.path[:]
    root = ROOT.resolve()
    stripped = [
        entry
        for entry in original
        if not entry or Path(entry).resolve(strict=False) != root
    ]
    try:
        sys.path[:] = stripped
        load_module()
        assert sys.path == stripped
    finally:
        sys.path[:] = original


def test_importlib_load_ignores_shadow_scripts_package_on_sys_path(tmp_path: Path) -> None:
    shadow_scripts = tmp_path / "scripts"
    shadow_scripts.mkdir()
    (shadow_scripts / "__init__.py").write_text("", encoding="utf-8")
    (shadow_scripts / "vfx_preview_common.py").write_text(
        "def load_vfx_config(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n"
        "def vfx_paths(*args, **kwargs):\n"
        "    raise AssertionError('shadow common imported')\n",
        encoding="utf-8",
    )
    original_path = sys.path[:]
    original_modules = {
        name: sys.modules.get(name)
        for name in ["scripts", "scripts.vfx_preview_common"]
    }
    try:
        sys.path[:] = [str(tmp_path)] + [
            entry
            for entry in original_path
            if not entry or Path(entry).resolve(strict=False) != ROOT.resolve(strict=False)
        ]
        sys.modules.pop("scripts", None)
        sys.modules.pop("scripts.vfx_preview_common", None)

        module = load_module()
        launcher = module.create_launcher(tmp_path / "study", ROOT, CONFIG)

        assert launcher.is_file()
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

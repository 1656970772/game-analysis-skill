from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "create_game_animation_viewer_launcher.py"
MANIFEST_RELATIVE = Path("4.临时目录") / "中间索引" / "游戏动画预览" / "manifest.json"
LAUNCHER_RELATIVE = Path("2.报告") / "打开动画预览.bat"


def create_layout(tmp_path: Path, source_roots: object) -> tuple[Path, Path, Path]:
    study_root = tmp_path / "学习项目"
    skill_root = tmp_path / "skill"
    manifest = study_root / MANIFEST_RELATIVE
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"schema_version": 1, "source_roots": source_roots}, ensure_ascii=False),
        encoding="utf-8",
    )

    viewer = (
        skill_root
        / "built-in-tools"
        / "GameAnimationViewer_1.0.1"
        / "GameAnimationViewer.exe"
    )
    viewer.parent.mkdir(parents=True)
    viewer.write_bytes(b"test viewer")

    exporter = skill_root / "scripts" / "export_game_animation_asset.py"
    exporter.parent.mkdir(parents=True)
    exporter.write_text("# test exporter\n", encoding="utf-8")
    return study_root, skill_root, exporter


def run_launcher(
    study_root: Path,
    skill_root: Path,
    renderer_script: Path | None = None,
    *,
    compatibility_alias: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    arguments = [
        sys.executable,
        str(SCRIPT),
        "--study-root",
        str(study_root),
        "--skill-root",
        str(skill_root),
        "--python-exe",
        sys.executable,
    ]
    if renderer_script is not None:
        arguments.extend(
            [
                "--exporter-script" if compatibility_alias else "--renderer-script",
                str(renderer_script),
            ]
        )
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def test_creates_unique_launcher_with_exact_manifest_roots_and_is_idempotent(
    tmp_path: Path,
) -> None:
    # Match the core builder schema and deliberately insert keys in an order
    # different from their values' deterministic sort order.
    relative_roots = {
        "xml": "4.临时目录/中间索引/Klei动画XML",
        "atlas": "1.分类资源/图片/图集/KleiTEX/data/anim",
    }
    study_root, skill_root, exporter = create_layout(tmp_path, relative_roots)
    resolved_roots: list[Path] = []
    for relative in sorted(relative_roots.values()):
        root = study_root.joinpath(*relative.split("/"))
        root.mkdir(parents=True)
        resolved_roots.append(root.resolve())

    first = run_launcher(study_root, skill_root)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["status"] == "created"
    assert first_payload["source_roots"] == [str(path) for path in resolved_roots]

    launcher = study_root / LAUNCHER_RELATIVE
    assert launcher.is_file()
    before_mtime = launcher.stat().st_mtime_ns
    content = launcher.read_text(encoding="utf-8-sig")
    viewer = (
        skill_root
        / "built-in-tools"
        / "GameAnimationViewer_1.0.1"
        / "GameAnimationViewer.exe"
    ).resolve()
    manifest = (study_root / MANIFEST_RELATIVE).resolve()
    preview_cache = (
        study_root / "4.临时目录" / "中间索引" / "游戏动画预览" / "preview-cache"
    ).resolve()
    export_dir = (study_root / "4.临时目录" / "动画预览导出").resolve()

    assert f'"{viewer}"' in content
    assert f'--manifest "{manifest}"' in content
    assert content.count("--asset-root ") == 2
    for root in resolved_roots:
        assert f'--asset-root "{root}"' in content
    assert f'--preview-cache-dir "{preview_cache}"' in content
    assert f'--export-dir "{export_dir}"' in content
    assert f'--python-exe "{Path(sys.executable).resolve()}"' in content
    assert f'--renderer-script "{exporter.resolve()}"' in content
    assert "--exporter-script" not in content
    assert 'set "GA_LAUNCH_LOG=' in content
    assert "Starting GameAnimationViewer. Please wait..." in content
    assert "goto :missing_viewer" in content
    assert "goto :missing_manifest" in content
    assert '>>"%GA_LAUNCH_LOG%" 2>&1' in content
    assert "pause" in content
    assert list((study_root / "2.报告").glob("*.bat")) == [launcher]

    second = run_launcher(study_root, skill_root)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["status"] == "reused"
    assert launcher.stat().st_mtime_ns == before_mtime


def test_exporter_script_cli_alias_still_emits_renderer_script_flag(tmp_path: Path) -> None:
    study_root, skill_root, _default_renderer = create_layout(
        tmp_path,
        {"assets": "assets"},
    )
    (study_root / "assets").mkdir()
    compatibility_renderer = skill_root / "scripts" / "compatibility-renderer.py"
    compatibility_renderer.write_text("# compatibility renderer\n", encoding="utf-8")

    result = run_launcher(
        study_root,
        skill_root,
        compatibility_renderer,
        compatibility_alias=True,
    )

    assert result.returncode == 0, result.stderr
    content = (study_root / LAUNCHER_RELATIVE).read_text(encoding="utf-8-sig")
    assert f'--renderer-script "{compatibility_renderer.resolve()}"' in content
    assert "--exporter-script" not in content


@pytest.mark.parametrize(
    ("source_roots", "case_id"),
    [
        ({}, "empty-object"),
        (["assets"], "legacy-array"),
        ({"": "assets"}, "empty-key"),
        ({"../xml": "assets"}, "path-key"),
        ({"xml": 42}, "non-string-value"),
    ],
    ids=["empty-object", "legacy-array", "empty-key", "path-key", "non-string-value"],
)
def test_rejects_non_builder_source_roots_contract(
    tmp_path: Path,
    source_roots: object,
    case_id: str,
) -> None:
    study_root, skill_root, _exporter = create_layout(tmp_path, source_roots)
    (study_root / "assets").mkdir(exist_ok=True)

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 1, case_id
    assert "source_roots" in result.stderr
    assert not (study_root / LAUNCHER_RELATIVE).exists()


def test_rejects_duplicate_resolved_source_root_values(tmp_path: Path) -> None:
    study_root, skill_root, _exporter = create_layout(
        tmp_path,
        {"xml": "assets/shared", "atlas": "assets/shared"},
    )
    (study_root / "assets" / "shared").mkdir(parents=True)

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 1
    assert "duplicate resolved root" in result.stderr
    assert not (study_root / LAUNCHER_RELATIVE).exists()


@pytest.mark.parametrize("invalid_kind", ["traversal", "absolute"])
def test_rejects_traversal_and_absolute_source_roots(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    invalid = "../outside" if invalid_kind == "traversal" else str(outside.resolve())
    study_root, skill_root, _exporter = create_layout(tmp_path, {"assets": invalid})

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 1
    assert "source_roots" in result.stderr
    assert not (study_root / LAUNCHER_RELATIVE).exists()


def test_rejects_missing_source_root(tmp_path: Path) -> None:
    study_root, skill_root, _exporter = create_layout(
        tmp_path,
        {"images": "1.分类资源/图片/不存在"},
    )

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert not (study_root / LAUNCHER_RELATIVE).exists()


def test_rejects_symlink_or_reparse_escape(tmp_path: Path) -> None:
    study_root, skill_root, _exporter = create_layout(
        tmp_path,
        {"images": "1.分类资源/图片/外链"},
    )
    outside = tmp_path / "outside-assets"
    outside.mkdir()
    link = study_root / "1.分类资源" / "图片" / "外链"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = run_launcher(study_root, skill_root)

    assert result.returncode == 1
    assert "study-root" in result.stderr
    assert not (study_root / LAUNCHER_RELATIVE).exists()

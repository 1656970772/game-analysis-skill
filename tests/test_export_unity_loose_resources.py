import csv
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_unity_loose_resources.py"
CONFIG = ROOT / "references" / "unity-loose-resource-config.json"
TOP_LEVEL = ("0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录")


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = ROOT / "tests_runtime"
    path = parent / f"unity-loose-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def make_study(root: Path) -> Path:
    study_root = root / "中文学习目录"
    for name in TOP_LEVEL:
        (study_root / name).mkdir(parents=True, exist_ok=True)
    return study_root


def write_bytes(root: Path, relative: str, data: bytes) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_png(root: Path, relative: str, color: tuple[int, int, int] = (20, 80, 160)) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 2), color).save(path, format="PNG")
    return path


def run_export(source: Path, study_root: Path, config: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--source",
        str(source),
        "--study-root",
        str(study_root),
    ]
    if config is not None:
        command.extend(["--config", str(config)])
    return subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def read_index(study_root: Path) -> tuple[list[dict[str, str]], dict, list[dict]]:
    index_root = study_root / "4.临时目录/中间索引/UnityLooseResources"
    csv_path = index_root / "loose_resources_index.csv"
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads((index_root / "summary.json").read_text(encoding="utf-8"))
    error_text = (index_root / "errors.jsonl").read_text(encoding="utf-8")
    errors = [json.loads(line) for line in error_text.splitlines() if line]
    return rows, summary, errors


def test_exports_unicode_res_and_streaming_assets_with_configured_classification(
    workspace_tmp_path: Path,
):
    source = workspace_tmp_path / "授权游戏_中文"
    res = source / "测试游戏_Data" / "Res"
    streaming = source / "测试游戏_Data" / "StreamingAssets"
    write_bytes(res, "配置/数值.json", '{"标题":"觅长生","值":1}'.encode("utf-8"))
    image_cases = {
        "scene/forest.png": "场景",
        "avatar/hero.png": "角色立绘",
        "avater/legacy.png": "角色立绘",
        "ui/button.png": "UI",
        "item/sword.png": "道具",
        "misc/plain.png": "未分类_待复核",
    }
    for index, relative in enumerate(image_cases):
        write_png(res, relative, (20 + index, 80, 160))
    write_bytes(streaming, "电影/intro.mp4", b"synthetic-mp4")
    write_bytes(streaming, "说明/readme.txt", "合成文本".encode("utf-8"))
    write_bytes(streaming, "数据/payload.bin", b"\x00\x01\x02")
    write_bytes(res, "ignored.unsupported", b"not configured")
    study_root = make_study(workspace_tmp_path)

    result = run_export(source, study_root)

    assert result.returncode == 0, result.stdout + result.stderr
    raw_root = study_root / "0.原始导出/UnityLooseResources"
    assert (raw_root / "测试游戏_Data/Res/配置/数值.json").is_file()
    assert (raw_root / "测试游戏_Data/StreamingAssets/电影/intro.mp4").is_file()
    assert (raw_root / "测试游戏_Data/StreamingAssets/说明/readme.txt").is_file()
    assert (raw_root / "测试游戏_Data/StreamingAssets/数据/payload.bin").is_file()
    assert not (raw_root / "测试游戏_Data/Res/ignored.unsupported").exists()
    for relative, classification in image_cases.items():
        assert (
            study_root
            / "1.分类资源"
            / "图片"
            / classification
            / "测试游戏_Data"
            / "Res"
            / Path(relative)
        ).is_file()

    classified_files = [path for path in (study_root / "1.分类资源/图片").rglob("*") if path.is_file()]
    assert len(classified_files) == len(image_cases)
    assert {path.suffix.lower() for path in classified_files} == {".png"}

    rows, summary, errors = read_index(study_root)
    assert len(rows) == 10
    assert summary["discovered_count"] == 10
    assert summary["index_row_count"] == 10
    assert sum(summary["status_counts"].values()) == 10
    assert summary["kind_counts"] == {"binary": 1, "image": 6, "json": 1, "text": 1, "video": 1}
    assert summary["validation_counts"] == {"not_required": 3, "valid": 7}
    assert summary["reconciliation"] == {
        "index_rows_equal_discovered": True,
        "status_counts_equal_index_rows": True,
        "raw_status_counts_equal_index_rows": True,
    }
    assert summary["csv_encoding"] == "utf-8-sig (UTF-8 BOM)"
    assert errors == []
    assert {row["source_rel"] for row in rows} == {
        "测试游戏_Data/Res/配置/数值.json",
        *(f"测试游戏_Data/Res/{relative}" for relative in image_cases),
        "测试游戏_Data/StreamingAssets/电影/intro.mp4",
        "测试游戏_Data/StreamingAssets/说明/readme.txt",
        "测试游戏_Data/StreamingAssets/数据/payload.bin",
    }
    required_fields = {
        "source_rel",
        "output_rel",
        "extension",
        "size",
        "sha256",
        "kind",
        "classification",
        "status",
        "error",
    }
    assert required_fields <= set(rows[0])


def test_invalid_json_is_archived_unchanged_and_recorded(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    invalid = b'{"broken": '
    write_bytes(source / "Game_Data/Res", "config/broken.json", invalid)
    study_root = make_study(workspace_tmp_path)

    result = run_export(source, study_root)

    assert result.returncode == 2
    archived = study_root / "0.原始导出/UnityLooseResources/Game_Data/Res/config/broken.json"
    assert archived.read_bytes() == invalid
    assert not list((study_root / "1.分类资源/图片").rglob("*.json"))
    rows, summary, errors = read_index(study_root)
    assert rows[0]["validation"] == "invalid"
    assert rows[0]["status"] == "archived_invalid"
    assert summary["raw_status_counts"] == {"copied": 1}
    assert summary["validation_counts"] == {"invalid": 1}
    assert len(errors) == 1
    assert errors[0]["stage"] == "validation"
    assert "invalid_json" in errors[0]["error"]


def test_invalid_image_is_archived_but_not_classified(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    invalid = b"\x89PNG\r\n\x1a\nheader-only"
    write_bytes(source / "Game_Data/Res", "ui/broken.png", invalid)
    study_root = make_study(workspace_tmp_path)

    result = run_export(source, study_root)

    assert result.returncode == 2
    archived = study_root / "0.原始导出/UnityLooseResources/Game_Data/Res/ui/broken.png"
    assert archived.read_bytes() == invalid
    assert not (study_root / "1.分类资源/图片/UI/Game_Data/Res/ui/broken.png").exists()
    rows, _summary, errors = read_index(study_root)
    assert rows[0]["validation"] == "invalid"
    assert rows[0]["status"] == "archived_invalid"
    assert rows[0]["classification_status"] == "skipped"
    assert errors[0]["stage"] == "validation"
    assert "invalid_image" in errors[0]["error"]


def test_custom_config_controls_include_extensions_and_exclusions(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    res = source / "Game_Data/Res"
    write_bytes(res, "keep.json", b"{}")
    write_bytes(res, "skip.json", b"{}")
    write_bytes(res, "not-included.txt", b"text")
    study_root = make_study(workspace_tmp_path)
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["include_extensions"] = [
        item for item in payload["include_extensions"] if item["extension"] != ".txt"
    ]
    payload["exclude_rules"].append(
        {"id": "synthetic_skip", "patterns": ["(^|/)skip\\.json$"]}
    )
    custom_config = workspace_tmp_path / "custom-config.json"
    custom_config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run_export(source, study_root, custom_config)

    assert result.returncode == 0, result.stdout + result.stderr
    rows, summary, errors = read_index(study_root)
    assert [row["source_rel"] for row in rows] == ["Game_Data/Res/keep.json"]
    assert summary["excluded_count"] == 1
    assert not (study_root / "0.原始导出/UnityLooseResources/Game_Data/Res/skip.json").exists()
    assert not (study_root / "0.原始导出/UnityLooseResources/Game_Data/Res/not-included.txt").exists()
    assert errors == []


def test_rerun_is_idempotent_for_raw_and_classified_copies(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    res = source / "Game_Data/Res"
    write_png(res, "ui/button.png")
    write_bytes(res, "data.json", b'{"ok":true}')
    study_root = make_study(workspace_tmp_path)

    first = run_export(source, study_root)
    second = run_export(source, study_root)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    rows, summary, errors = read_index(study_root)
    assert {row["raw_status"] for row in rows} == {"reused"}
    image_row = next(row for row in rows if row["kind"] == "image")
    assert image_row["classification_status"] == "reused"
    assert summary["raw_status_counts"] == {"reused": 2}
    assert summary["classification_status_counts"] == {"not_applicable": 1, "reused": 1}
    assert errors == []


def test_raw_destination_conflict_is_not_overwritten(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    write_bytes(source / "Game_Data/StreamingAssets", "readme.txt", b"source-content")
    study_root = make_study(workspace_tmp_path)
    destination = study_root / "0.原始导出/UnityLooseResources/Game_Data/StreamingAssets/readme.txt"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"user-content")

    result = run_export(source, study_root)

    assert result.returncode == 2
    assert destination.read_bytes() == b"user-content"
    rows, summary, errors = read_index(study_root)
    assert rows[0]["status"] == "conflict"
    assert rows[0]["raw_status"] == "conflict"
    assert summary["status_counts"] == {"conflict": 1}
    assert any(error["stage"] == "raw_copy" for error in errors)


def test_classification_destination_conflict_preserves_user_file(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    write_png(source / "Game_Data/Res", "ui/button.png")
    study_root = make_study(workspace_tmp_path)
    destination = study_root / "1.分类资源/图片/UI/Game_Data/Res/ui/button.png"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"user-image-placeholder")

    result = run_export(source, study_root)

    assert result.returncode == 2
    assert destination.read_bytes() == b"user-image-placeholder"
    assert (study_root / "0.原始导出/UnityLooseResources/Game_Data/Res/ui/button.png").is_file()
    rows, _summary, errors = read_index(study_root)
    assert rows[0]["status"] == "conflict"
    assert rows[0]["raw_status"] == "copied"
    assert rows[0]["classification_status"] == "conflict"
    assert any(error["stage"] == "classification_copy" for error in errors)


@pytest.mark.parametrize("mutation", ["missing", "extra_directory", "extra_file"])
def test_rejects_invalid_study_root_top_level(workspace_tmp_path: Path, mutation: str):
    source = workspace_tmp_path / "game"
    write_bytes(source / "Game_Data/Res", "data.json", b"{}")
    study_root = make_study(workspace_tmp_path)
    if mutation == "missing":
        (study_root / "2.报告").rmdir()
    elif mutation == "extra_directory":
        (study_root / "其他").mkdir()
    else:
        (study_root / "unexpected.txt").write_text("x", encoding="utf-8")

    result = run_export(source, study_root)

    assert result.returncode == 2
    assert "顶层必须恰含 5 个编号目录" in result.stderr
    assert not (study_root / "0.原始导出/UnityLooseResources").exists()
    assert not (study_root / "4.临时目录/中间索引/UnityLooseResources").exists()


def test_rejects_traversal_in_discovery_config_before_output(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    write_bytes(source / "Game_Data/Res", "data.json", b"{}")
    study_root = make_study(workspace_tmp_path)
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload["discovery_roots"][0]["glob"] = "../outside"
    custom_config = workspace_tmp_path / "unsafe-config.json"
    custom_config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run_export(source, study_root, custom_config)

    assert result.returncode == 2
    assert "不是安全的相对路径模式" in result.stderr
    assert not (study_root / "0.原始导出/UnityLooseResources").exists()


def create_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "& { param([string]$Link, [string]$Target) "
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command, str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"当前环境无法创建 junction：{result.stdout}{result.stderr}")


def test_reparse_directory_in_source_is_not_followed(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    res = source / "Game_Data/Res"
    res.mkdir(parents=True)
    outside = workspace_tmp_path / "outside-source"
    write_bytes(outside, "secret.png", b"not-a-real-image")
    create_junction(res / "linked", outside)
    study_root = make_study(workspace_tmp_path)

    result = run_export(source, study_root)

    assert result.returncode == 2
    assert not list((study_root / "0.原始导出/UnityLooseResources").rglob("secret.png"))
    rows, summary, errors = read_index(study_root)
    assert rows == []
    assert summary["discovered_count"] == 0
    assert any(error["error"] == "symlink_or_reparse_rejected" for error in errors)


def test_reparse_output_root_escape_is_rejected(workspace_tmp_path: Path):
    source = workspace_tmp_path / "game"
    write_bytes(source / "Game_Data/Res", "data.json", b"{}")
    study_root = make_study(workspace_tmp_path)
    outside = workspace_tmp_path / "outside-output"
    create_junction(study_root / "0.原始导出/UnityLooseResources", outside)

    result = run_export(source, study_root)

    assert result.returncode == 2
    assert "reparse point" in result.stderr
    assert list(outside.iterdir()) == []
    assert not (study_root / "4.临时目录/中间索引/UnityLooseResources").exists()

import base64
import json
import os
import shutil
import subprocess
import sys
import uuid
import wave
import struct
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "organize_godot_project_export.py"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = ROOT / "tests_runtime"
    path = parent / f"organizer-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def write_file(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def create_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "& { param([string]$Link, [string]$Target) "
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null }"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            command,
            str(link),
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def crc_valid_png_with_empty_idat() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", b"")
        + png_chunk(b"IEND", b"")
    )


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8_000)
        handle.writeframes(b"\0\0" * 8)


def minimal_sfnt(signature: bytes = b"\x00\x01\x00\x00") -> bytes:
    table_data = b"\0" * 12
    return (
        signature
        + struct.pack(">HHHH", 1, 16, 0, 0)
        + b"head"
        + struct.pack(">III", 0, 28, len(table_data))
        + table_data
    )


def run_organizer(recovery_root: Path, study_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/organize_godot_project_export.py",
            "--recovery-root",
            str(recovery_root.relative_to(ROOT)),
            "--study-root",
            str(study_root.relative_to(ROOT)),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def make_study(tmp_path: Path) -> tuple[Path, Path]:
    study_root = tmp_path / "study"
    recovery_root = study_root / "0.原始导出" / "GDRETools恢复"
    recovery_root.mkdir(parents=True)
    return study_root, recovery_root


@pytest.mark.parametrize(
    "junction_relative,source_relative",
    [
        ("1.分类资源", "sprites/ui/button.png"),
        ("3.代码", "scripts/player.gd"),
    ],
)
def test_rejects_classification_root_junction_escape(
    workspace_tmp_path: Path,
    junction_relative: str,
    source_relative: str,
):
    study_root, recovery_root = make_study(workspace_tmp_path)
    source_data = PNG_1X1 if source_relative.endswith(".png") else b"extends Node\n"
    write_file(recovery_root, source_relative, source_data)
    outside = workspace_tmp_path / "outside-classification"
    create_junction(study_root / junction_relative, outside)

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    assert list(outside.rglob("*")) == []
    assert "outside StudyRoot" in (result.stdout + result.stderr)
    assert not (study_root / "4.临时目录").exists()


def test_rejects_index_root_junction_escape(workspace_tmp_path: Path):
    study_root, recovery_root = make_study(workspace_tmp_path)
    write_file(recovery_root, "sprites/ui/button.png", PNG_1X1)
    outside = workspace_tmp_path / "outside-index"
    create_junction(study_root / "4.临时目录/中间索引/Godot", outside)

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    assert list(outside.rglob("*")) == []
    assert "outside StudyRoot" in (result.stdout + result.stderr)
    assert not (study_root / "1.分类资源").exists()


def test_rejects_crc_valid_png_with_empty_or_undecodable_idat(
    workspace_tmp_path: Path,
):
    study_root, recovery_root = make_study(workspace_tmp_path)
    relative = "sprites/ui/empty-idat.png"
    write_file(recovery_root, relative, crc_valid_png_with_empty_idat())

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    assert not (study_root / "1.分类资源/图片/UI" / relative).exists()
    failures = (
        study_root / "4.临时目录/中间索引/Godot/organize_failures.jsonl"
    ).read_text(encoding="utf-8")
    assert "invalid_image_content" in failures


@pytest.mark.parametrize(
    "extension,data",
    [
        (".png", b"\x89PNG\r\n\x1a\n"),
        (".jpg", b"\xff\xd8\xff\xe0"),
        (".jpeg", b"\xff\xd8\xff\xe0"),
        (".bmp", b"BM"),
        (".gif", b"GIF89a"),
        (".webp", b"RIFF\x04\0\0\0WEBP"),
        (".tga", b"\0\0\x02" + (b"\0" * 15)),
    ],
)
def test_rejects_header_only_images_for_every_configured_extension(
    workspace_tmp_path: Path,
    extension: str,
    data: bytes,
):
    config = json.loads(
        (ROOT / "references/godot-resource-classification.json").read_text(
            encoding="utf-8"
        )
    )
    assert extension in {value.lower() for value in config["image_extensions"]}
    study_root, recovery_root = make_study(workspace_tmp_path)
    relative = f"sprites/ui/header-only{extension}"
    write_file(recovery_root, relative, data)

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    assert not (study_root / "1.分类资源/图片/UI" / relative).exists()
    failures = (
        study_root / "4.临时目录/中间索引/Godot/organize_failures.jsonl"
    ).read_text(encoding="utf-8")
    assert "invalid_image_content" in failures


def test_classifies_assets_and_preserves_script_relative_paths(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    study_root, recovery_root = make_study(tmp_path)
    for relative in [
        "sprites/ui/button.png",
        "sprites/backgrounds/level.png",
        "sprites/items/key.png",
        "sprites/spritesheets/player.png",
        "sprites/vfx/particles_smoke.png",
    ]:
        write_file(recovery_root, relative, PNG_1X1)
    write_wav(recovery_root / "audio" / "hit.wav")
    write_file(recovery_root, "fonts/main.ttf", minimal_sfnt())
    write_file(
        recovery_root,
        "fonts/main.fnt",
        b'info face="Test"\ncommon lineHeight=8\npage id=0 file="main.png"\nchars count=0\n',
    )
    write_file(recovery_root, "scripts/player.gd", b"extends Node\nfunc _ready():\n\tpass\n")

    result = run_organizer(recovery_root, study_root)

    assert result.returncode == 0, result.stderr
    assert (study_root / "1.分类资源/图片/UI/sprites/ui/button.png").is_file()
    assert (study_root / "1.分类资源/图片/场景/sprites/backgrounds/level.png").is_file()
    assert (study_root / "1.分类资源/图片/道具/sprites/items/key.png").is_file()
    assert (study_root / "1.分类资源/图片/图集/sprites/spritesheets/player.png").is_file()
    assert not (study_root / "1.分类资源/图片/序列帧/sprites/spritesheets/player.png").exists()
    assert (study_root / "1.分类资源/特效/粒子贴图/sprites/vfx/particles_smoke.png").is_file()
    assert (study_root / "1.分类资源/音频/audio/hit.wav").is_file()
    assert (study_root / "1.分类资源/渲染/其他/字体/fonts/main.ttf").is_file()
    assert (study_root / "1.分类资源/渲染/其他/字体/fonts/main.fnt").is_file()
    assert (study_root / "3.代码/脚本/scripts/player.gd").is_file()

    index_root = study_root / "4.临时目录" / "中间索引" / "Godot"
    assert (index_root / "organized_files.csv").is_file()
    assert (index_root / "organize_summary.json").is_file()
    forbidden_index_suffixes = {".csv", ".json", ".jsonl", ".md", ".txt"}
    offenders = [
        path
        for path in (study_root / "1.分类资源").rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_index_suffixes
    ]
    assert offenders == []


def test_invalid_audio_is_not_copied_and_is_recorded(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    study_root, recovery_root = make_study(tmp_path)
    write_file(recovery_root, "audio/fake.ogg", b"not-an-ogg")

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    assert not (study_root / "1.分类资源/音频/audio/fake.ogg").exists()
    failure_path = study_root / "4.临时目录/中间索引/Godot/organize_failures.jsonl"
    assert failure_path.is_file()
    assert "audio/fake.ogg" in failure_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative,data",
    [
        ("sprites/ui/signature.png", b"\x89PNG\r\n\x1a\n"),
        ("audio/signature.ogg", b"OggS"),
        ("fonts/signature.ttf", b"\x00\x01\x00\x00"),
        ("fonts/signature.otf", b"OTTO"),
        ("fonts/signature.ttc", b"ttcf"),
        ("fonts/signature.fnt", b"BMF"),
    ],
)
def test_rejects_truncated_signature_only_assets(
    workspace_tmp_path: Path,
    relative: str,
    data: bytes,
):
    study_root, recovery_root = make_study(workspace_tmp_path)
    write_file(recovery_root, relative, data)

    result = run_organizer(recovery_root, study_root)

    assert result.returncode != 0
    expected_destination = study_root / (
        "1.分类资源/图片/UI/" + relative
        if relative.endswith(".png")
        else "1.分类资源/音频/" + relative
        if relative.endswith(".ogg")
        else "1.分类资源/渲染/其他/字体/" + relative
    )
    assert not expected_destination.exists()
    failures = (
        study_root / "4.临时目录/中间索引/Godot/organize_failures.jsonl"
    ).read_text(encoding="utf-8")
    assert relative in failures


def test_rerun_is_idempotent_and_different_content_conflicts(workspace_tmp_path: Path):
    tmp_path = workspace_tmp_path
    study_root, recovery_root = make_study(tmp_path)
    write_file(recovery_root, "sprites/ui/button.png", PNG_1X1)

    first = run_organizer(recovery_root, study_root)
    second = run_organizer(recovery_root, study_root)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    summary_path = study_root / "4.临时目录/中间索引/Godot/organize_summary.json"
    second_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert second_summary["copied_count"] == 0
    assert second_summary["unchanged_count"] == 1
    assert second_summary["conflict_count"] == 0

    destination = study_root / "1.分类资源/图片/UI/sprites/ui/button.png"
    destination.write_bytes(b"user-content")
    third = run_organizer(recovery_root, study_root)

    assert third.returncode != 0
    assert destination.read_bytes() == b"user-content"
    third_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert third_summary["conflict_count"] == 1

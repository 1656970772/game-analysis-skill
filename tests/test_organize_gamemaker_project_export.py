from __future__ import annotations

import csv
import sys
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from organize_gamemaker_project_export import organize_gamemaker_project_export  # noqa: E402


def write_png(path: Path, size: tuple[int, int] = (8, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_creates_required_project_folders_and_canonical_protagonist_frames(tmp_path: Path) -> None:
    exports = tmp_path / "GameMaker_UTMT_Exports"
    normalized = tmp_path / "Normalized"
    project = tmp_path / "Katana_ZERO"

    write_png(exports / "Sprites" / "spr_attack" / "spr_attack_0.png")
    write_png(exports / "Sprites" / "spr_attack" / "spr_attack_1.png")
    write_png(exports / "Sprites" / "spr_dragon_wallslide" / "spr_dragon_wallslide_0.png")
    write_png(exports / "Sprites" / "bg_rooftop" / "bg_rooftop_0.png")
    write_png(exports / "EmbeddedTextures" / "0.png")
    (exports / "Sounds" / "audiogroup_default").mkdir(parents=True)
    (exports / "Sounds" / "audiogroup_default" / "snd_slash.wav").write_bytes(b"RIFF")
    (exports / "strings.json").write_text("[]", encoding="utf-8")

    normalized.mkdir()
    with (normalized / "gamemaker_sprite_frame_index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sprite_name", "frame_index", "relative_path", "width", "height", "has_alpha", "size_bytes"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "sprite_name": "spr_attack",
                    "frame_index": "0",
                    "relative_path": "Sprites/spr_attack/spr_attack_0.png",
                    "width": "8",
                    "height": "8",
                    "has_alpha": "true",
                    "size_bytes": "1",
                },
                {
                    "sprite_name": "spr_attack",
                    "frame_index": "1",
                    "relative_path": "Sprites/spr_attack/spr_attack_1.png",
                    "width": "8",
                    "height": "8",
                    "has_alpha": "true",
                    "size_bytes": "1",
                },
                {
                    "sprite_name": "spr_dragon_wallslide",
                    "frame_index": "0",
                    "relative_path": "Sprites/spr_dragon_wallslide/spr_dragon_wallslide_0.png",
                    "width": "8",
                    "height": "8",
                    "has_alpha": "true",
                    "size_bytes": "1",
                },
            ]
        )

    summary = organize_gamemaker_project_export(
        project_root=project,
        exports_root=exports,
        normalized_root=normalized,
        game_name="Katana ZERO",
        protagonist_patterns=["^spr_attack$", "^spr_dragon_"],
    )

    for folder in ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]:
        assert (project / folder).is_dir()

    assert (project / "1.分类资源" / "图片" / "序列帧" / "主角候选" / "attack" / "attack_01.png").is_file()
    assert (project / "1.分类资源" / "图片" / "序列帧" / "主角候选" / "attack" / "attack_02.png").is_file()
    assert (project / "1.分类资源" / "图片" / "序列帧" / "主角候选" / "wallslide" / "wallslide_01.png").is_file()
    assert (project / "1.分类资源" / "图片" / "场景资源" / "bg_rooftop" / "bg_rooftop_0.png").is_file()
    assert (project / "1.分类资源" / "音频" / "audiogroup_default" / "snd_slash.wav").is_file()

    canonical_rows = read_csv(project / "4.临时目录" / "中间索引" / "GameMaker组织" / "主角动画序列帧索引.csv")
    assert [row["output_name"] for row in canonical_rows if row["action_name"] == "attack"] == [
        "attack_01.png",
        "attack_02.png",
    ]
    assert summary["canonical_frame_count"] == 3


def test_prefixed_action_names_do_not_overwrite_base_actions(tmp_path: Path) -> None:
    exports = tmp_path / "GameMaker_UTMT_Exports"
    normalized = tmp_path / "Normalized"
    project = tmp_path / "Katana_ZERO"

    write_png(exports / "Sprites" / "spr_fall" / "spr_fall_0.png")
    write_png(exports / "Sprites" / "spr_dragon_fall" / "spr_dragon_fall_0.png")
    normalized.mkdir()
    with (normalized / "gamemaker_sprite_frame_index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sprite_name", "frame_index", "relative_path", "width", "height", "has_alpha", "size_bytes"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "sprite_name": "spr_dragon_fall",
                    "frame_index": "0",
                    "relative_path": "Sprites/spr_dragon_fall/spr_dragon_fall_0.png",
                    "width": "8",
                    "height": "8",
                    "has_alpha": "true",
                    "size_bytes": "1",
                },
                {
                    "sprite_name": "spr_fall",
                    "frame_index": "0",
                    "relative_path": "Sprites/spr_fall/spr_fall_0.png",
                    "width": "8",
                    "height": "8",
                    "has_alpha": "true",
                    "size_bytes": "1",
                },
            ]
        )

    organize_gamemaker_project_export(
        project_root=project,
        exports_root=exports,
        normalized_root=normalized,
        game_name="Katana ZERO",
        protagonist_patterns=["^spr_fall$", "^spr_dragon_"],
    )

    assert (project / "1.分类资源" / "图片" / "序列帧" / "主角候选" / "fall" / "fall_01.png").is_file()
    assert (project / "1.分类资源" / "图片" / "序列帧" / "主角候选" / "dragon_fall" / "dragon_fall_01.png").is_file()
    canonical_rows = read_csv(project / "4.临时目录" / "中间索引" / "GameMaker组织" / "主角动画序列帧索引.csv")
    assert sorted(row["output_relative_path"] for row in canonical_rows) == [
        "dragon_fall/dragon_fall_01.png",
        "fall/fall_01.png",
    ]

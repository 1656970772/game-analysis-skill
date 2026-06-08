#!/usr/bin/env python3
"""Organize GameMaker UTMT exports into a reusable art-study deliverable.

The script copies already-exported local files. It never opens, patches, or
saves the original GameMaker data file.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Iterable


REQUIRED_FOLDERS = [
    "0.原始导出",
    "1.分类资源",
    "2.报告",
    "3.代码",
    "4.临时目录",
]

DEFAULT_PROTAGONIST_PATTERNS = [
    r"^spr_(attack|idle|idle_to_run|idle_to_walk|run|run_to_idle|walk|jump|fall|roll|airdodge|crouch|postcrouch|precrouch|slash|wallgrab2?|wallslide2?|hurtfly_begin|hurtfly_loop|hurtground|hurtrecover|stairfall)$",
    r"^spr_dragon_",
    r"^spr_player_",
    r"^player_",
]

IMAGE_EXTENSIONS = {".png", ".bmp", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".ogg", ".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a"}


def sanitize_token(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "unnamed"


def action_name_from_sprite(sprite_name: str) -> str:
    name = sanitize_token(sprite_name)
    name = re.sub(r"_strip\d+$", "", name)
    for prefix in ("spr_player_nosword_", "spr_player_", "player_", "spr_dragon_", "spr_"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = re.sub(r"_strip\d+$", "", name)
    return sanitize_token(name)


def prefixed_action_name(sprite_name: str) -> str:
    name = sanitize_token(sprite_name)
    if name.startswith("spr_"):
        name = name[4:]
    return sanitize_token(re.sub(r"_strip\d+$", "", name))


def is_unprefixed_base_action(sprite_name: str, action_name: str) -> bool:
    name = sanitize_token(sprite_name)
    return name == action_name or name == f"spr_{action_name}"


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def is_protagonist_sprite(sprite_name: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(sprite_name) for pattern in patterns)


def classify_sprite(sprite_name: str, protagonist_patterns: list[re.Pattern[str]]) -> list[str]:
    name = sanitize_token(sprite_name)
    if is_protagonist_sprite(sprite_name, protagonist_patterns):
        return ["角色资源", "主角"]
    if name.startswith(("bg_", "spr_bg")) or re.search(
        r"(background|foreground|wall|floor|roof|room|apt|apartment|motel|bunker|factory|club|cathedral|mansion|sewer|warehouse|temple|prison|videostore|bar|street|city)",
        name,
    ):
        return ["场景资源"]
    if re.search(r"(hud|ui|menu|cursor|keyboard|button|icon|font|textbox|vcr|vhs|logo|title|save)", name):
        return ["UI"]
    if re.search(r"(fx|slash|blood|spark|explosion|smoke|fire|flame|glow|debris|trail|bullet|laser|impact|particle)", name):
        return ["特效"]
    if re.search(
        r"(player|dragon|girl|grunt|cop|gangster|boss|headhunter|psy|psych|v_|vet|neighbor|bartender|scientist|officer|killer|dream|npc|bouncer|robber|homeless|kissyface|leon|electrohead)",
        name,
    ):
        return ["角色资源"]
    if re.search(
        r"(item|prop|bottle|chair|door|table|sword|katana|gun|machine|key|card|tape|phone|tv|car|bike|glass|medal|note|paper|battery|chandelier|switch|button|elevator)",
        name,
    ):
        return ["道具"]
    return ["其他"]


def ensure_required_folders(project_root: Path) -> dict[str, Path]:
    project_root.mkdir(parents=True, exist_ok=True)
    folders = {name: project_root / name for name in REQUIRED_FOLDERS}
    for path in folders.values():
        path.mkdir(parents=True, exist_ok=True)
    return folders


def copy_file(src: Path, dst: Path, overwrite: bool = True) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite:
        return False
    shutil.copy2(src, dst)
    return True


def iter_files(root: Path, extensions: set[str] | None = None) -> Iterable[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_file() and (extensions is None or path.suffix.lower() in extensions):
            yield path


def read_frame_rows(normalized_root: Path) -> list[dict[str, str]]:
    path = normalized_root / "gamemaker_sprite_frame_index.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def copy_sprite_tree(
    exports_root: Path,
    classified_root: Path,
    protagonist_patterns: list[re.Pattern[str]],
    overwrite: bool,
) -> tuple[int, dict[str, int]]:
    copied = 0
    category_counts: dict[str, int] = defaultdict(int)
    sprites_root = exports_root / "Sprites"
    if not sprites_root.exists():
        return copied, dict(category_counts)

    for sprite_dir in sorted((p for p in sprites_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        category_parts = classify_sprite(sprite_dir.name, protagonist_patterns)
        category_counts["/".join(category_parts)] += 1
        dest_dir = classified_root.joinpath("图片", *category_parts, sprite_dir.name)
        for src in iter_files(sprite_dir, IMAGE_EXTENSIONS):
            if copy_file(src, dest_dir / src.name, overwrite=overwrite):
                copied += 1
    return copied, dict(category_counts)


def copy_tree_preserve(src_root: Path, dst_root: Path, extensions: set[str] | None, overwrite: bool) -> int:
    copied = 0
    if not src_root.exists():
        return copied
    for src in iter_files(src_root, extensions):
        rel = src.relative_to(src_root)
        if copy_file(src, dst_root / rel, overwrite=overwrite):
            copied += 1
    return copied


def copy_normalized_indexes(normalized_root: Path, final_root: Path, overwrite: bool) -> int:
    copied = 0
    index_root = final_root / "资源索引"
    if not normalized_root.exists():
        return copied
    for src in sorted(normalized_root.glob("gamemaker_*")):
        if src.is_file() and copy_file(src, index_root / src.name, overwrite=overwrite):
            copied += 1
    return copied


def write_code_export_note(code_root: Path, datawin_info: Path | None, overwrite: bool) -> None:
    note = code_root / "代码导出说明.md"
    if note.exists() and not overwrite:
        return
    info_line = f"- `gamemaker_datawin_info.txt`：{datawin_info}" if datawin_info else "- 未提供 `gamemaker_datawin_info.txt` 路径。"
    text = "\n".join(
        [
            "# 代码导出说明",
            "",
            "## 观察到的事实",
            "- 当前流程检测并处理的是 GameMaker `data.win` 资源导出。",
            info_line,
            "",
            "## 我的判断",
            "- 如果 `Is YYC - True`，GML 通常已编译进 exe，UTMT 无法从 `data.win` 导出完整 GML 方法体。",
            "- 本目录保留代码导出边界说明；如需 native 行为验证，应另行执行授权范围内的 exe 逆向分析。",
            "",
            "## 待验证假设",
            "- 是否需要进一步分析 `Katana ZERO.exe` 的 native 行为，取决于后续研究目标。",
            "",
        ]
    )
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")


def export_canonical_protagonist_frames(
    exports_root: Path,
    normalized_root: Path,
    frame_root: Path,
    index_root: Path,
    protagonist_patterns: list[re.Pattern[str]],
    overwrite: bool,
) -> tuple[int, int]:
    rows = read_frame_rows(normalized_root)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        sprite_name = row.get("sprite_name", "")
        if is_protagonist_sprite(sprite_name, protagonist_patterns):
            grouped[sprite_name].append(row)

    base_action_groups: dict[str, list[str]] = defaultdict(list)
    for sprite_name in grouped:
        base_action_groups[action_name_from_sprite(sprite_name)].append(sprite_name)

    action_sources: dict[str, str] = {}
    index_rows: list[dict[str, str]] = []
    frame_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)

    for sprite_name, sprite_rows in sorted(grouped.items()):
        sprite_rows.sort(key=lambda row: int(row.get("frame_index") or 0))
        base_action = action_name_from_sprite(sprite_name)
        if len(base_action_groups[base_action]) > 1 and not is_unprefixed_base_action(sprite_name, base_action):
            action_name = prefixed_action_name(sprite_name)
        else:
            action_name = base_action
        if action_name in action_sources and action_sources[action_name] != sprite_name:
            fallback = prefixed_action_name(sprite_name)
            action_name = fallback
            suffix = 2
            while action_name in action_sources and action_sources[action_name] != sprite_name:
                action_name = f"{fallback}_{suffix}"
                suffix += 1
        action_sources[action_name] = sprite_name
        digits = max(2, len(str(len(sprite_rows))))

        for ordinal, row in enumerate(sprite_rows, start=1):
            src = exports_root / row["relative_path"]
            output_name = f"{action_name}_{ordinal:0{digits}d}{src.suffix.lower()}"
            dst = frame_root / action_name / output_name
            if copy_file(src, dst, overwrite=overwrite):
                index_rows.append(
                    {
                        "source_sprite_name": sprite_name,
                        "action_name": action_name,
                        "frame_number": str(ordinal),
                        "output_name": output_name,
                        "output_relative_path": dst.relative_to(frame_root).as_posix(),
                        "source_relative_path": row["relative_path"],
                        "width": row.get("width", ""),
                        "height": row.get("height", ""),
                    }
                )

    write_csv(
        index_root / "主角动画序列帧索引.csv",
        index_rows,
        [
            "source_sprite_name",
            "action_name",
            "frame_number",
            "output_name",
            "output_relative_path",
            "source_relative_path",
            "width",
            "height",
        ],
    )
    return len(index_rows), len(grouped)


def organize_gamemaker_project_export(
    project_root: Path | str,
    exports_root: Path | str,
    normalized_root: Path | str,
    game_name: str,
    protagonist_patterns: list[str] | None = None,
    datawin_info: Path | str | None = None,
    overwrite: bool = True,
) -> dict[str, object]:
    project_root = Path(project_root).resolve()
    exports_root = Path(exports_root).resolve()
    normalized_root = Path(normalized_root).resolve()
    datawin_info_path = Path(datawin_info).resolve() if datawin_info else None
    compiled_patterns = compile_patterns(protagonist_patterns or DEFAULT_PROTAGONIST_PATTERNS)

    folders = ensure_required_folders(project_root)
    raw_root = folders["0.原始导出"]
    classified_root = folders["1.分类资源"]
    report_root = folders["2.报告"]
    code_root = folders["3.代码"]
    index_root = folders["4.临时目录"] / "中间索引" / "GameMaker组织"
    protagonist_frame_root = classified_root / "图片" / "序列帧" / "主角候选"

    sprite_file_count, sprite_category_counts = copy_sprite_tree(exports_root, classified_root, compiled_patterns, overwrite=overwrite)
    embedded_count = copy_tree_preserve(exports_root / "EmbeddedTextures", classified_root / "图片" / "嵌入纹理", IMAGE_EXTENSIONS, overwrite=overwrite)
    texture_count = copy_tree_preserve(exports_root / "TextureItems", classified_root / "图片" / "纹理页", IMAGE_EXTENSIONS, overwrite=overwrite)
    audio_count = copy_tree_preserve(exports_root / "Sounds", classified_root / "音频", AUDIO_EXTENSIONS, overwrite=overwrite)
    text_count = 0
    if (exports_root / "strings.json").exists() and copy_file(exports_root / "strings.json", classified_root / "文本资源" / "strings.json", overwrite=overwrite):
        text_count = 1

    normalized_count = copy_normalized_indexes(normalized_root, report_root, overwrite=overwrite)
    canonical_frame_count, protagonist_animation_count = export_canonical_protagonist_frames(
        exports_root,
        normalized_root,
        protagonist_frame_root,
        index_root,
        compiled_patterns,
        overwrite=overwrite,
    )
    write_code_export_note(code_root, datawin_info_path, overwrite=overwrite)

    summary: dict[str, object] = {
        "game_name": game_name,
        "project_root": str(project_root),
        "exports_root": str(exports_root),
        "normalized_root": str(normalized_root),
        "raw_root": str(raw_root),
        "classified_resource_root": str(classified_root),
        "report_root": str(report_root),
        "index_root": str(index_root),
        "required_folders": REQUIRED_FOLDERS,
        "sprite_file_count": sprite_file_count,
        "sprite_category_counts": sprite_category_counts,
        "embedded_texture_count": embedded_count,
        "texture_item_count": texture_count,
        "audio_count": audio_count,
        "text_resource_count": text_count,
        "normalized_index_count": normalized_count,
        "protagonist_animation_count": protagonist_animation_count,
        "canonical_frame_count": canonical_frame_count,
    }
    (report_root / "gamemaker_project_export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Organize GameMaker UTMT exports into the standard project deliverable folders.")
    parser.add_argument("--project-root", required=True, help="Game-name root directory that will contain the 0-4 deliverable folders.")
    parser.add_argument("--exports-root", required=True, help="UTMT GameMaker_UTMT_Exports directory.")
    parser.add_argument("--normalized-root", required=True, help="Directory containing gamemaker_* normalized indexes.")
    parser.add_argument("--game-name", required=True, help="Display name stored in summary JSON.")
    parser.add_argument("--datawin-info", help="Optional gamemaker_datawin_info.txt path for code export note.")
    parser.add_argument("--protagonist-pattern", action="append", dest="protagonist_patterns", help="Regex for protagonist sprite names. Can be repeated.")
    parser.add_argument("--no-overwrite", action="store_true", help="Do not replace existing copied files.")
    args = parser.parse_args()

    summary = organize_gamemaker_project_export(
        project_root=args.project_root,
        exports_root=args.exports_root,
        normalized_root=args.normalized_root,
        game_name=args.game_name,
        protagonist_patterns=args.protagonist_patterns,
        datawin_info=args.datawin_info,
        overwrite=not args.no_overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

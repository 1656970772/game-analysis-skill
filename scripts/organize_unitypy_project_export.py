#!/usr/bin/env python3
"""Organize UnityPy exports into the project deliverable directory layout.

The script copies only files already exported into the local study directory.
It keeps game-provided asset names, and only normalizes detected sequence
frames into action_01.png, action_02.png, ... inside action folders.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IMAGE_TYPES = {"Sprite", "Texture2D"}
MODEL_TYPES = {"Mesh", "MeshRenderer", "SkinnedMeshRenderer"}
CLASSIFIED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp"}
CLASSIFIED_AUDIO_EXTENSIONS = {".wav", ".ogg", ".mp3", ".bank"}
METADATA_EXTENSIONS = {".json", ".csv", ".txt", ".md"}
MAIN_CHARACTER_HINTS = ("hero", "onya", "player", "mainchar", "main_char", "uicharonya", "techillu_hero")
ACTION_HINTS = (
    "idle",
    "attack",
    "walk",
    "run",
    "wall_run",
    "wallrun",
    "dash",
    "jump",
    "fall",
    "flip",
    "kick",
    "hit",
    "hurt",
    "death",
    "dead",
    "throw",
    "slash",
    "roll",
    "standhit",
    "standkick",
    "floorkick",
)


def safe_name(value: str, fallback: str = "asset") -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[^\w.\- ]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._ ")
    return text[:120] or fallback


def normalize_sequence_group(value: str) -> str:
    text = safe_name(value, "sequence")
    text = re.sub(r"[- ]+", "_", text)
    text = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.lower() or "sequence"


def parse_sequence_name(asset_name: str) -> tuple[str, str, int] | None:
    name = asset_name.strip()
    patterns = [
        r"^(?P<subject>.+?)[-_ ]+(?P<action>[A-Za-z][A-Za-z0-9_-]*?)[-_ ]+(?P<frame>\d{1,4})$",
        r"^(?P<subject>.+?)[-_ ]+(?P<action>[A-Za-z][A-Za-z0-9_-]*?)(?P<frame>\d{1,4})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, name)
        if not match:
            continue
        subject = match.group("subject").strip("_- ")
        action = match.group("action").strip("_- ")
        frame = int(match.group("frame"))
        if not subject or not action:
            continue
        action_l = normalize_sequence_group(action)
        action_tokens = set(action_l.split("_"))
        if action_l in ACTION_HINTS or any(token in ACTION_HINTS for token in action_tokens):
            return subject, action, frame
    return None


def classify_asset(asset_name: str, asset_type: str) -> tuple[str, str]:
    lower = asset_name.lower()
    if asset_type in MODEL_TYPES:
        return "模型", safe_name(asset_type)
    if asset_type == "AudioClip":
        return "音频", group_from_name(asset_name)
    if asset_type in {"Shader", "Material", "RenderTexture"}:
        return "渲染", safe_name(asset_type)
    if asset_type in {"TextAsset", "MonoScript", "Font"}:
        return "字体与文本", safe_name(asset_type)
    if lower.startswith("ui_") or lower.startswith("uimask") or any(
        token in lower for token in ("button", "menu", "hud", "scroll", "dropdown", "inputfield", "checkmark")
    ):
        return "图片/UI", group_from_name(asset_name)
    if lower.startswith("uichar") or any(
        token in lower for token in ("hero", "onya", "ogai", "talgun", "shenlei", "thug", "npc", "character", "monkeyonya")
    ):
        return "图片/角色立绘", group_from_name(asset_name)
    if any(token in lower for token in ("smoke", "fog", "slash", "spark", "particle", "vfx", "effect", "falloff", "transition")):
        return "特效/粒子贴图", group_from_name(asset_name)
    if any(token in lower for token in ("weapon", "sword", "polearm", "hat", "mask", "item", "prop")):
        return "图片/道具", group_from_name(asset_name)
    if any(
        token in lower
        for token in (
            "bg",
            "background",
            "forest",
            "jungle",
            "waterfall",
            "gate",
            "village",
            "monastery",
            "garden",
            "rooftop",
            "tavern",
            "trunk",
            "foliage",
            "studyhall",
            "door",
            "capital_",
            "market",
        )
    ):
        return "图片/场景", group_from_name(asset_name)
    if asset_type == "Texture2D":
        return "图片/图集", group_from_name(asset_name)
    if asset_type == "Sprite":
        return "图片/图集", group_from_name(asset_name)
    return "未分类", safe_name(asset_type)


def group_from_name(asset_name: str) -> str:
    parsed = parse_sequence_name(asset_name)
    if parsed:
        return safe_name(parsed[0])
    name = safe_name(asset_name)
    for delimiter in ("_", "-"):
        if delimiter in name:
            return safe_name(name.split(delimiter)[0])
    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}__{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def read_rows(index_path: Path) -> list[dict[str, str]]:
    with index_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def is_main_character_candidate(asset_name: str) -> bool:
    lower = safe_name(asset_name).lower()
    return any(hint in lower for hint in MAIN_CHARACTER_HINTS)


def split_export_paths(export_path: str) -> list[str]:
    return [part for part in export_path.split(";") if part]


def is_metadata_export(row: dict[str, str], export_path: str) -> bool:
    return row.get("export_kind") == "metadata" or Path(export_path).suffix.lower() in METADATA_EXTENSIONS


def should_copy_to_classified(row: dict[str, str], export_path: str) -> bool:
    """Only copy player-facing media into 1.分类资源.

    UnityPy indexes many useful metadata JSON files for analysis, but those
    belong in 0.原始导出/4.临时目录 indexes, not in the deliverable resource
    tree. AssetRipper project files such as .mat/.shader are handled by the
    AssetRipper classification step instead of this UnityPy organizer.
    """
    if not export_path or is_metadata_export(row, export_path):
        return False
    suffix = Path(export_path).suffix.lower()
    asset_type = row.get("asset_type", "")
    if asset_type in IMAGE_TYPES:
        return suffix in CLASSIFIED_IMAGE_EXTENSIONS
    if asset_type == "AudioClip":
        return row.get("export_kind") == "audio" and suffix in CLASSIFIED_AUDIO_EXTENSIONS
    return False


def organize(index_path: Path, unitypy_root: Path, study_root: Path, project_name: str) -> dict[str, Any]:
    rows = read_rows(index_path)
    classified_root = study_root / "1.分类资源"
    main_root = classified_root / "图片" / "角色立绘" / "主角候选资源"
    index_root = study_root / "4.临时目录" / "中间索引" / "UnityPy组织"
    classified_root.mkdir(parents=True, exist_ok=True)
    main_root.mkdir(parents=True, exist_ok=True)
    index_root.mkdir(parents=True, exist_ok=True)

    exported_rows = [row for row in rows if row.get("export_path")]
    sequence_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    normal_rows: list[dict[str, str]] = []
    for row in exported_rows:
        parsed = parse_sequence_name(row.get("asset_name", ""))
        category, group = classify_asset(row.get("asset_name", ""), row.get("asset_type", ""))
        if parsed and row.get("asset_type") in IMAGE_TYPES:
            subject, action, _frame = parsed
            sequence_groups[("图片/序列帧", safe_name(subject), normalize_sequence_group(action))].append(row)
        else:
            normal_rows.append(row)

    org_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    main_rows: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    skipped_metadata_count = 0
    skipped_non_deliverable_count = 0

    def copy_and_record(row: dict[str, str], dest: Path, category: str, group: str, sequence_action: str = "", sequence_frame: str = "") -> Path | None:
        src = unitypy_root / row["export_path"]
        if not src.exists():
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        final_dest = unique_path(dest)
        shutil.copy2(src, final_dest)
        output_rel = final_dest.relative_to(study_root).as_posix()
        org_rows.append(
            {
                "original_asset_name": row.get("asset_name", ""),
                "asset_type": row.get("asset_type", ""),
                "category": category,
                "group": group,
                "sequence_action": sequence_action,
                "sequence_frame": sequence_frame,
                "source_export_path": row.get("export_path", ""),
                "source_file": row.get("source_file", ""),
                "width": row.get("width", ""),
                "height": row.get("height", ""),
                "output_relative_path": output_rel,
            }
        )
        counters[category] += 1
        if is_main_character_candidate(row.get("asset_name", "")) and row.get("asset_type") in IMAGE_TYPES:
            main_dest = unique_path(main_root / safe_name(category.split("/")[-1], "category") / group / final_dest.name)
            main_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_dest, main_dest)
            main_rows.append(
                {
                    "original_asset_name": row.get("asset_name", ""),
                    "asset_type": row.get("asset_type", ""),
                    "category": category,
                    "group": group,
                    "source_output_relative_path": output_rel,
                    "main_output_relative_path": main_dest.relative_to(study_root).as_posix(),
                }
            )
        return final_dest

    for row in normal_rows:
        category, group = classify_asset(row.get("asset_name", ""), row.get("asset_type", ""))
        export_paths = split_export_paths(row.get("export_path", ""))
        for export_path in export_paths:
            row_for_copy = {**row, "export_path": export_path}
            if not should_copy_to_classified(row_for_copy, export_path):
                if is_metadata_export(row_for_copy, export_path):
                    skipped_metadata_count += 1
                else:
                    skipped_non_deliverable_count += 1
                continue
            if row.get("asset_type") == "AudioClip":
                filename = safe_name(Path(export_path).name, row.get("asset_name", "audio"))
            else:
                suffix = Path(export_path).suffix or ".asset"
                filename = f"{safe_name(row.get('asset_name', ''), row.get('asset_type', 'asset'))}{suffix}"
            copy_and_record(row_for_copy, classified_root / category / group / filename, category, group)

    for (category, subject, action), group_rows in sorted(sequence_groups.items()):
        parsed_rows = []
        for row in group_rows:
            parsed = parse_sequence_name(row.get("asset_name", ""))
            if parsed is None:
                continue
            parsed_rows.append((parsed[2], row))
        parsed_rows.sort(key=lambda item: (item[0], item[1].get("asset_name", "")))
        for ordinal, (_source_frame, row) in enumerate(parsed_rows, start=1):
            if not should_copy_to_classified(row, row.get("export_path", "")):
                if is_metadata_export(row, row.get("export_path", "")):
                    skipped_metadata_count += 1
                else:
                    skipped_non_deliverable_count += 1
                continue
            dest = classified_root / category / subject / action / f"{action}_{ordinal:02d}{Path(row['export_path']).suffix}"
            copied = copy_and_record(row, dest, category, subject, action, str(ordinal))
            if copied is not None:
                sequence_rows.append(
                    {
                        "sequence_group": f"{category}/{subject}/{action}",
                        "action": action,
                        "frame_number": ordinal,
                        "original_asset_name": row.get("asset_name", ""),
                        "output_relative_path": copied.relative_to(study_root).as_posix(),
                        "source_export_path": row.get("export_path", ""),
                        "width": row.get("width", ""),
                        "height": row.get("height", ""),
                    }
                )

    write_csv(
        index_root / "资源组织索引.csv",
        org_rows,
        [
            "original_asset_name",
            "asset_type",
            "category",
            "group",
            "sequence_action",
            "sequence_frame",
            "source_export_path",
            "source_file",
            "width",
            "height",
            "output_relative_path",
        ],
    )
    write_csv(
        index_root / "动画序列帧索引.csv",
        sequence_rows,
        ["sequence_group", "action", "frame_number", "original_asset_name", "output_relative_path", "source_export_path", "width", "height"],
    )
    write_csv(
        index_root / "主角资源候选索引.csv",
        main_rows,
        ["original_asset_name", "asset_type", "category", "group", "source_output_relative_path", "main_output_relative_path"],
    )

    model_rows = [row for row in rows if row.get("asset_type") in MODEL_TYPES]
    model_note = study_root / "4.临时目录" / "模型工具需求.md"
    model_note.parent.mkdir(parents=True, exist_ok=True)
    if model_rows:
        model_note.write_text(
            "\n".join(
                [
                    "# 模型工具需求",
                    "",
                    f"- 观察到的事实：UnityPy 索引中存在 {len(model_rows)} 个模型/渲染器相关对象。",
                    "- 我的判断：Forestrike 当前未从 UnityPy 导出独立模型文件；如 AssetRipper 后出现 `.fbx`、`.obj`、`.gltf`、`.glb`，再把 Blender/Assimp 等模型查看或转换工具下载到 Skill 的 `tools/` 目录并记录来源。",
                    "- 待验证：AssetRipper GUI 完成后检查 `ExportedProject` 是否包含模型文件。",
                ]
            ),
            encoding="utf-8",
        )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        "source_index": str(index_path),
        "classified_resource_root": str(classified_root),
        "index_root": str(index_root),
        "organized_count": len(org_rows),
        "sequence_frame_count": len(sequence_rows),
        "main_character_candidate_count": len(main_rows),
        "skipped_metadata_count": skipped_metadata_count,
        "skipped_non_deliverable_count": skipped_non_deliverable_count,
        "category_counts": dict(counters),
        "model_related_object_count": len(model_rows),
        "notes": [
            "Filenames keep game-provided asset names.",
            "Detected sequence frames are normalized to action_01.png, action_02.png, ... inside action folders.",
            "Indexes preserve original asset names and source paths.",
        ],
    }
    (index_root / "资源组织摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, help="UnityPy all_resources_index.csv")
    parser.add_argument("--unitypy-root", required=True, help="UnityPy export root")
    parser.add_argument("--study-root", required=True, help="Project study root with 0-4 deliverable folders")
    parser.add_argument("--project-name", required=True)
    args = parser.parse_args()
    summary = organize(Path(args.index), Path(args.unitypy_root), Path(args.study_root), args.project_name)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

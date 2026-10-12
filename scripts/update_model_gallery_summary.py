from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.model_gallery_common import (
        DEFAULT_MODEL_VIEWER_CONFIG,
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    from model_gallery_common import (
        DEFAULT_MODEL_VIEWER_CONFIG,
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )


DEFAULT_CONFIG = DEFAULT_MODEL_VIEWER_CONFIG
SECTION = "## 模型预览"


def _section_heading_pattern(section_title: str) -> re.Pattern[str]:
    return re.compile(rf"(?m)^{re.escape(section_title)}[ \t]*$")


def _section_end(text: str, start_after_heading: int) -> int:
    next_heading = re.search(r"(?m)^## ", text[start_after_heading:])
    if next_heading is None:
        return len(text)
    return start_after_heading + next_heading.start()


def _remove_section_occurrences(text: str, section_title: str) -> str:
    pattern = _section_heading_pattern(section_title)
    while True:
        match = pattern.search(text)
        if match is None:
            return text
        end = _section_end(text, match.end())
        prefix = text[: match.start()].rstrip()
        suffix = text[end:].lstrip("\n")
        if prefix and suffix:
            text = f"{prefix}\n\n{suffix}"
        else:
            text = f"{prefix}{suffix}"


def replace_section(text: str, section_title: str, replacement: str) -> str:
    replacement = replacement.strip() + "\n"
    pattern = _section_heading_pattern(section_title)
    match = pattern.search(text)
    if match is None:
        base = text.rstrip()
        if not base:
            return replacement
        return f"{base}\n\n{replacement}"

    end = _section_end(text, match.end())
    prefix = text[: match.start()].rstrip()
    suffix = _remove_section_occurrences(text[end:].lstrip("\n"), section_title).rstrip()

    parts = [part for part in (prefix, replacement.rstrip(), suffix) if part]
    return "\n\n".join(parts) + "\n"


def summarize_manifest(manifest_path: Path) -> tuple[int, int, int, int, dict[str, int], dict[str, int]]:
    if not manifest_path.exists():
        return 0, 0, 0, 0, {}, {}

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("model viewer manifest root must be an object")
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise ValueError("model viewer manifest items must be a list")
    failed_count = 0
    real_animated_count = 0
    procedural_preview_count = 0
    role_counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("asset_role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        if item.get("preview_status") == "failed" or item.get("errors"):
            failed_count += 1
        stats = item.get("stats", {})
        if isinstance(stats, dict) and int(stats.get("animation_count") or 0) > 0:
            if item.get("is_generated_animation_preview") or item.get("animation_origin") == "procedural_preview":
                procedural_preview_count += 1
            else:
                real_animated_count += 1
    skipped_reasons = summarize_thumbnail_skips(manifest_path)
    return len(items), failed_count, real_animated_count, procedural_preview_count, skipped_reasons, role_counts


def summarize_thumbnail_skips(manifest_path: Path) -> dict[str, int]:
    errors_path = manifest_path.parent / "errors.jsonl"
    if not errors_path.exists():
        return {}

    skipped_reasons: dict[str, int] = {}
    for line in errors_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("stage") != "thumbnail" or record.get("status") != "skipped":
            continue
        reason = str(record.get("reason") or "unknown")
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
    return skipped_reasons


def _launcher_names(config: dict[str, Any]) -> list[str]:
    launcher = config["launcher"]
    names = [str(launcher["bat_name"])]
    names.extend(str(name) for name in launcher.get("alias_bat_names", []))
    deduped: list[str] = []
    for name in names:
        normalized = name.replace("\\", "/").strip("/")
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _entry_path(config: dict[str, Any]) -> str:
    report_dir = str(config["paths"]["report_dir"]).replace("\\", "/").strip("/")
    bat_names = _launcher_names(config)
    entries = [f"{report_dir}/{name}" if report_dir else name for name in bat_names]
    if len(entries) == 1:
        return entries[0]
    return f"{entries[0]}（兼容：{', '.join(entries[1:])}）"


def _render_section(
    total_count: int,
    failed_count: int,
    real_animated_count: int,
    procedural_preview_count: int,
    skipped_reasons: dict[str, int],
    role_counts: dict[str, int],
    config: dict[str, Any],
    viewer_exists: bool,
    launcher_exists: bool,
) -> str:
    launcher_status = "已生成" if launcher_exists else "未生成"
    viewer_status = "可用" if viewer_exists else "缺失"
    lines = [
        SECTION,
        "",
        f"- 入口：{_entry_path(config)}",
        f"- 模型数量：{total_count}",
        f"- 含真实导出动作模型：{real_animated_count}",
        f"- 程序化动作预览模型：{procedural_preview_count}",
        f"- 失败数量：{failed_count}",
    ]
    if role_counts:
        role_labels = {
            "textured_art_model": "已验证贴图美术模型",
            "map_structure_mesh": "地图结构/体块网格",
            "map_embedded_mesh": "地图内嵌网格",
            "embedded_mesh_candidate": "内嵌网格候选",
            "model_candidate": "模型候选",
            "unknown": "未分类",
        }
        role_text = "，".join(
            f"{role_labels.get(role, role)} {count}项"
            for role, count in sorted(role_counts.items(), key=lambda entry: (-entry[1], entry[0]))
        )
        lines.append(f"- 类型分布：{role_text}")
    if real_animated_count:
        lines.append("- 真实动作预览说明：含真实导出动作的 glTF/GLB 可在 GameModelViewer 中使用动作列表、播放/暂停、时间轴和速度控制。")
    if procedural_preview_count:
        lines.append("- 程序化动作预览说明：程序化动作预览不计入真实导出动作模型；它仅用于查看 AnimationClip metadata 的动作名、估算时长和节奏，不代表完整还原原始 skin/Avatar/bone weights。")
    if skipped_reasons:
        reason_text = "，".join(
            f"{reason} {count}项" for reason, count in sorted(skipped_reasons.items())
        )
        lines.append(f"- 缩略图跳过：{reason_text}")
    lines.extend(
        [
            f"- 启动器状态：{launcher_status}",
            f"- 工具状态：GameModelViewer {viewer_status}",
            "",
        ]
    )
    return "\n".join(lines)


def update_summary(
    study_root: Path,
    manifest_path: Path,
    config: dict[str, Any],
    viewer_exists: bool = True,
    launcher_exists: bool = True,
) -> Path:
    study_root = resolve_existing(Path(study_root))
    manifest_path = ensure_inside_root(Path(manifest_path), study_root)
    report_dir = ensure_inside_root(study_root / str(config["paths"]["report_dir"]), study_root)
    overview_path = ensure_inside_root(report_dir / str(config["paths"]["overview_name"]), study_root)
    report_dir.mkdir(parents=True, exist_ok=True)

    if overview_path.exists():
        text = overview_path.read_text(encoding="utf-8")
    else:
        text = "# 研究总览\n"

    total_count, failed_count, real_animated_count, procedural_preview_count, skipped_reasons, role_counts = summarize_manifest(manifest_path)
    replacement = _render_section(
        total_count,
        failed_count,
        real_animated_count,
        procedural_preview_count,
        skipped_reasons,
        role_counts,
        config,
        viewer_exists=viewer_exists,
        launcher_exists=launcher_exists,
    )
    overview_path.write_text(
        replace_section(text, SECTION, replacement),
        encoding="utf-8",
    )
    return overview_path


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新研究总览中的模型预览摘要")
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--viewer-exe", required=True, type=Path)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        type=Path,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_model_viewer_config(args.config)
    launcher_path = (
        args.study_root
        / str(config["paths"]["report_dir"])
        / str(config["launcher"]["bat_name"])
    )
    update_summary(
        args.study_root,
        args.manifest,
        config,
        viewer_exists=args.viewer_exe.exists(),
        launcher_exists=launcher_path.exists(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

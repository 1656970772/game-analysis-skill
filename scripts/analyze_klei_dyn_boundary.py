#!/usr/bin/env python3
"""Inventory opaque Klei .dyn payload boundaries without decoding them."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def iter_files(root: Path, suffix: str) -> Iterable[Path]:
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in {".git", "__pycache__", "node_modules"}]
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() == suffix:
                yield path


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return round(-sum((count / total) * math.log2(count / total) for count in counts.values()), 4)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def load_anim_dynamic_stems(study_root: Path) -> dict[str, str]:
    root = (
        study_root
        / "0.原始导出"
        / "KleiDST"
        / "packages_extracted"
        / "data"
        / "databundles"
        / "anim_dynamic"
    )
    stems: dict[str, str] = {}
    for path in iter_files(root, ".zip"):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        stems[path.stem.lower()] = rel
    return stems


def extract_relevant_strings(data: bytes) -> list[str]:
    hits: list[str] = []
    pattern = re.compile(rb"[ -~]{4,}")
    for match in pattern.finditer(data):
        text = match.group(0).decode("ascii", errors="ignore")
        lower = text.lower()
        if "dyn" in lower or "dynamic" in lower or "anim_dynamic" in lower:
            hits.append(text[:240])
    return sorted(set(hits))


def scan_exe_strings(game_root: Path | None, max_hits: int = 80) -> list[dict[str, str]]:
    if game_root is None or not game_root.exists():
        return []
    hits: list[dict[str, str]] = []
    candidates = list(iter_files(game_root / "bin", ".exe")) + list(iter_files(game_root / "bin64", ".exe"))
    for exe in candidates:
        try:
            data = exe.read_bytes()
        except OSError:
            continue
        try:
            rel = exe.relative_to(game_root).as_posix()
        except ValueError:
            rel = exe.name
        for text in extract_relevant_strings(data):
            hits.append({"exe": rel, "string": text})
            if len(hits) >= max_hits:
                return hits
    return hits


def build_markdown(summary: dict[str, object], sample_rows: list[dict[str, object]]) -> str:
    cluster_rows = summary.get("header_clusters", [])
    lines = [
        "# Klei .dyn 边界盘点",
        "",
        "## 观察到的事实",
        "",
        f"- `.dyn` 文件数：{summary['dyn_files']}。",
        f"- 可对照 `anim_dynamic` 同名 zip：{summary['matched_anim_dynamic_zip']}。",
        f"- EXE 动态资源字符串命中：{len(summary.get('exe_string_hits', []))}。",
        "",
        "## Header 聚类",
        "",
        "| header16 | count |",
        "|---|---:|",
    ]
    for item in cluster_rows[:20]:  # type: ignore[index]
        lines.append(f"| `{item['header16']}` | {item['count']} |")
    lines.extend(
        [
            "",
            "## 样本",
            "",
            "| source | size | entropy64 | matched zip |",
            "|---|---:|---:|---|",
        ]
    )
    for row in sample_rows[:20]:
        lines.append(
            f"| `{row['source']}` | {row['size']} | {row['entropy64']} | {row['matched_anim_dynamic_zip']} |"
        )
    lines.extend(
        [
            "",
            "## 我的判断",
            "",
            "- `.dyn` 不是普通 ZIP；本脚本只做边界盘点，不尝试解码或绕过访问控制。",
            "- 同名 `anim_dynamic/*.zip` 说明可以建立资源名对照，但不能证明 `.dyn` 的编码算法。",
            "",
            "## 下一步",
            "",
            "- 若要继续，优先找可信公开 Klei 工具或官方 modding 资料；无可信工具时再编写可审计解析器。",
        ]
    )
    return "\n".join(lines)


def analyze_dyn_boundary(study_root: Path, game_root: Path | None = None) -> dict[str, object]:
    study_root = study_root.resolve()
    dyn_root = study_root / "0.原始导出" / "KleiDST" / "dynamic_dyn"
    index_root = study_root / "4.临时目录" / "中间索引" / "KleiDST"
    failure_root = study_root / "4.临时目录" / "失败记录"
    anim_stems = load_anim_dynamic_stems(study_root)
    rows: list[dict[str, object]] = []
    header_counter: Counter[str] = Counter()
    entropy_values: list[float] = []
    matched = 0

    for path in sorted(iter_files(dyn_root, ".dyn")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        first64 = data[:64]
        header16 = first64[:16].hex()
        entropy = shannon_entropy(first64)
        try:
            rel = path.relative_to(dyn_root).as_posix()
        except ValueError:
            rel = path.name
        matched_zip = anim_stems.get(path.stem.lower(), "")
        if matched_zip:
            matched += 1
        header_counter[header16] += 1
        entropy_values.append(entropy)
        rows.append(
            {
                "source": rel,
                "size": len(data),
                "magic16": header16,
                "first64_hex": first64.hex(),
                "entropy64": entropy,
                "matched_anim_dynamic_zip": matched_zip,
                "status": "raw_copied_boundary_inventory_only",
            }
        )

    exe_hits = scan_exe_strings(game_root)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_root": str(study_root),
        "game_root": str(game_root.resolve()) if game_root else "",
        "dyn_files": len(rows),
        "anim_dynamic_zip_names": len(anim_stems),
        "matched_anim_dynamic_zip": matched,
        "unmatched_anim_dynamic_zip": len(rows) - matched,
        "entropy64_min": min(entropy_values) if entropy_values else 0,
        "entropy64_max": max(entropy_values) if entropy_values else 0,
        "entropy64_avg": round(sum(entropy_values) / len(entropy_values), 4) if entropy_values else 0,
        "header_clusters": [
            {"header16": header, "count": count} for header, count in header_counter.most_common()
        ],
        "exe_string_hits": exe_hits,
    }

    write_csv(
        index_root / "dyn_boundary_inventory.csv",
        rows,
        [
            "source",
            "size",
            "magic16",
            "first64_hex",
            "entropy64",
            "matched_anim_dynamic_zip",
            "status",
        ],
    )
    write_text(
        index_root / "dyn_boundary_inventory_summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2),
    )
    write_text(
        failure_root / "KleiDST_dyn_boundary_inventory.md",
        build_markdown(summary, rows),
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, help="GameAnalysis Klei/DST study root.")
    parser.add_argument("--game-root", help="Optional original game root for EXE string hints.")
    args = parser.parse_args()

    result = analyze_dyn_boundary(
        Path(args.study_root),
        game_root=Path(args.game_root) if args.game_root else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

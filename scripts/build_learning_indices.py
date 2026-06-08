#!/usr/bin/env python3
"""Build learning-focused indexes from a local game study directory."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDEX_ROOT = Path("4.临时目录") / "中间索引" / "学习输出"
IMAGE_TYPES = {"Sprite", "Texture2D"}
RENDER_TYPES = {"Shader", "Material", "RenderTexture", "SpriteAtlas"}
ANIMATION_TYPES = {"AnimationClip", "AnimatorController", "AnimatorOverrideController", "RuntimeAnimatorController"}
AUDIO_EXTENSIONS = {".wav", ".ogg", ".mp3", ".bank"}
RENDER_EXTENSIONS = {".shader", ".mat", ".rendertexture"}
ANIMATION_EXTENSIONS = {".anim", ".controller", ".overridecontroller", ".playable"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def find_unitypy_indexes(study_root: Path) -> list[Path]:
    candidates = sorted((study_root / "0.原始导出" / "UnityPy").rglob("all_resources_index.csv"))
    if candidates:
        return candidates
    fallback = study_root / "4.临时目录" / "中间索引" / "UnityPy组织" / "资源组织索引.csv"
    return [fallback] if fallback.exists() else []


def parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def size_bucket(width: int, height: int) -> str:
    longest = max(width, height)
    if longest <= 0:
        return "unknown"
    if longest <= 64:
        return "≤64px"
    if longest <= 256:
        return "65-256px"
    if longest <= 512:
        return "257-512px"
    if longest <= 1024:
        return "513-1024px"
    return "≥1025px"


def asset_prefix(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_\-]+", "_", name or "Unnamed")
    parts = re.split(r"[_\-]+", clean)
    return parts[0] if parts and parts[0] else "Unnamed"


def build_size_and_name_indexes(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    size_counter: dict[tuple[int, int, str], Counter[str]] = defaultdict(Counter)
    color_input_rows: list[dict[str, Any]] = []
    name_counter: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        asset_type = row.get("asset_type", "")
        asset_name = row.get("asset_name", "")
        name_counter[asset_prefix(asset_name)][asset_type or "Unknown"] += 1
        width = parse_int(row.get("width", ""))
        height = parse_int(row.get("height", ""))
        if asset_type in IMAGE_TYPES and width and height:
            bucket = size_bucket(width, height)
            size_counter[(width, height, bucket)][asset_type] += 1
            color_input_rows.append(
                {
                    "asset_name": asset_name,
                    "asset_type": asset_type,
                    "width": width,
                    "height": height,
                    "size_bucket": bucket,
                    "export_path": row.get("export_path", ""),
                    "source_file": row.get("source_file", ""),
                }
            )

    size_rows = [
        {
            "width": width,
            "height": height,
            "size_bucket": bucket,
            "count": sum(counter.values()),
            "asset_types": " | ".join(f"{asset_type}:{count}" for asset_type, count in sorted(counter.items())),
        }
        for (width, height, bucket), counter in sorted(size_counter.items(), key=lambda item: (-sum(item[1].values()), item[0]))
    ]
    name_rows = [
        {
            "prefix": prefix,
            "count": sum(counter.values()),
            "asset_types": " | ".join(f"{asset_type}:{count}" for asset_type, count in sorted(counter.items())),
        }
        for prefix, counter in sorted(name_counter.items(), key=lambda item: (-sum(item[1].values()), item[0].lower()))
    ]
    return size_rows, color_input_rows, name_rows


def rows_for_types(rows: list[dict[str, str]], target_types: set[str]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("asset_type", "") in target_types]


def file_index_rows(root: Path, extensions: set[str], category: str) -> list[dict[str, str]]:
    if not root.exists():
        return []
    out = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        out.append(
            {
                "asset_name": path.stem,
                "asset_type": category,
                "source_file": path.relative_to(root).as_posix(),
                "export_path": path.as_posix(),
                "status": "file",
            }
        )
    return out


def namespace_rows(il_root: Path) -> list[dict[str, Any]]:
    if not il_root.exists():
        return []
    namespace_counter: dict[str, Counter[str]] = defaultdict(Counter)
    namespace_file: dict[str, str] = {}
    namespace_re = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)\s*;?")
    type_re = re.compile(r"\b(class|struct|interface|enum)\s+([A-Za-z_][\w]*)")
    for path in sorted(il_root.rglob("*.cs")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        namespace = "<global>"
        for line in text.splitlines():
            match = namespace_re.match(line)
            if match:
                namespace = match.group(1)
                namespace_file.setdefault(namespace, path.relative_to(il_root).as_posix())
                break
        for kind, _name in type_re.findall(text):
            namespace_counter[namespace][kind] += 1
        namespace_file.setdefault(namespace, path.relative_to(il_root).as_posix())
    return [
        {
            "namespace": namespace,
            "class_count": counter.get("class", 0),
            "struct_count": counter.get("struct", 0),
            "interface_count": counter.get("interface", 0),
            "enum_count": counter.get("enum", 0),
            "sample_file": namespace_file.get(namespace, ""),
        }
        for namespace, counter in sorted(namespace_counter.items(), key=lambda item: (-sum(item[1].values()), item[0]))
    ]


def build_indices(study_root: Path) -> dict[str, Any]:
    study_root = study_root.resolve()
    output_root = study_root / INDEX_ROOT
    output_root.mkdir(parents=True, exist_ok=True)

    unitypy_indexes = find_unitypy_indexes(study_root)
    unitypy_rows: list[dict[str, str]] = []
    for index_path in unitypy_indexes:
        unitypy_rows.extend(read_csv(index_path))

    size_rows, color_input_rows, name_rows = build_size_and_name_indexes(unitypy_rows)
    render_rows = rows_for_types(unitypy_rows, RENDER_TYPES)
    audio_rows = rows_for_types(unitypy_rows, {"AudioClip"})
    animation_rows = rows_for_types(unitypy_rows, ANIMATION_TYPES)

    assetripper_root = study_root / "0.原始导出" / "AssetRipper"
    render_rows.extend(file_index_rows(assetripper_root, RENDER_EXTENSIONS, "RenderFile"))
    audio_rows.extend(file_index_rows(assetripper_root, AUDIO_EXTENSIONS, "AudioFile"))
    animation_rows.extend(file_index_rows(assetripper_root, ANIMATION_EXTENSIONS, "AnimationFile"))

    ns_rows = namespace_rows(study_root / "3.代码" / "IL导出")

    write_csv(output_root / "美术资源尺寸分布.csv", size_rows, ["width", "height", "size_bucket", "count", "asset_types"])
    write_csv(
        output_root / "色彩摘要输入.csv",
        color_input_rows,
        ["asset_name", "asset_type", "width", "height", "size_bucket", "export_path", "source_file"],
    )
    write_csv(
        output_root / "渲染资源索引.csv",
        render_rows,
        ["asset_id", "asset_name", "asset_type", "source_file", "export_kind", "export_path", "status", "remarks"],
    )
    write_csv(
        output_root / "音频资源索引.csv",
        audio_rows,
        ["asset_id", "asset_name", "asset_type", "source_file", "export_kind", "export_path", "status", "remarks"],
    )
    write_csv(
        output_root / "动画资源索引.csv",
        animation_rows,
        ["asset_id", "asset_name", "asset_type", "source_file", "export_kind", "export_path", "status", "remarks"],
    )
    write_csv(output_root / "命名规范统计.csv", name_rows, ["prefix", "count", "asset_types"])
    write_csv(
        output_root / "代码命名空间索引.csv",
        ns_rows,
        ["namespace", "class_count", "struct_count", "interface_count", "enum_count", "sample_file"],
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_root": str(study_root),
        "output_root": str(output_root),
        "unitypy_index_count": len(unitypy_indexes),
        "image_size_rows": len(size_rows),
        "render_rows": len(render_rows),
        "audio_rows": len(audio_rows),
        "animation_rows": len(animation_rows),
        "namespace_rows": len(ns_rows),
    }
    (output_root / "学习输出摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True)
    args = parser.parse_args()
    summary = build_indices(Path(args.study_root))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

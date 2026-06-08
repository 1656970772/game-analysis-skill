from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import UnityPy
except ImportError:  # pragma: no cover - environment-specific
    UnityPy = None


GUID_RE = re.compile(r"guid:\s*([0-9a-f]{32})")
ANIM_VALUE_RE = re.compile(r"value:\s*\{fileID:\s*21300000,\s*guid:\s*([0-9a-f]{32}),\s*type:\s*2\}")
MOTION_RE = re.compile(r"m_Motion:\s*\{fileID:\s*7400000,\s*guid:\s*([0-9a-f]{32}),\s*type:\s*2\}")
NAME_RE = re.compile(r"^\s*m_Name:\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class AssetRef:
    guid: str
    path: Path
    name: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def safe_slug(name: str) -> str:
    text = name.strip().lower()
    replacements = {
        "anticipate": "antic",
        "anticipe": "antic",
        "antici": "antic",
        "antcitape": "antic",
        "anticitape": "antic",
        "recover": "recover",
        "loop": "loop",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    text = re.sub(r"(?<=[a-z])(\d+)", lambda match: f"{int(match.group(1)):02d}", text)
    return text or "anim"


def parse_guid_from_meta(meta: Path) -> str | None:
    match = GUID_RE.search(read_text(meta))
    return match.group(1) if match else None


def parse_name_from_asset(asset: Path) -> str:
    match = NAME_RE.search(read_text(asset))
    return match.group(1).strip().strip('"') if match else asset.stem


def build_guid_index(root: Path, suffix: str) -> dict[str, AssetRef]:
    index: dict[str, AssetRef] = {}
    for meta in root.rglob(f"*{suffix}.meta"):
        guid = parse_guid_from_meta(meta)
        if not guid:
            continue
        asset = Path(str(meta)[: -len(".meta")])
        if not asset.exists():
            continue
        index[guid] = AssetRef(guid=guid, path=asset, name=parse_name_from_asset(asset))
    return index


def parse_controller_clip_guids(controller: Path) -> list[str]:
    guids = []
    seen = set()
    for guid in MOTION_RE.findall(read_text(controller)):
        if guid not in seen:
            seen.add(guid)
            guids.append(guid)
    return guids


def parse_animation_sprite_guids(anim: Path) -> list[str]:
    text = read_text(anim)
    if "attribute: m_Sprite" not in text:
        return []
    guids = ANIM_VALUE_RE.findall(text)
    return guids


def load_unitypy_sprites(bundle_paths: list[Path], wanted_names: set[str]):
    if UnityPy is None:
        raise SystemExit("UnityPy is required. Run scripts/setup_python_env.ps1 or install requirements.txt.")
    sprite_images = {}
    duplicate_names = Counter()
    scanned = Counter()
    for bundle_path in bundle_paths:
        env = UnityPy.load(str(bundle_path))
        for obj in env.objects:
            if obj.type.name != "Sprite":
                continue
            scanned["Sprite"] += 1
            try:
                data = obj.read()
                name = getattr(data, "m_Name", "") or getattr(data, "name", "")
                if name not in wanted_names:
                    continue
                duplicate_names[name] += 1
                if name in sprite_images:
                    continue
                image = data.image
                if image is not None:
                    sprite_images[name] = image
            except Exception as exc:
                scanned[f"error:{type(exc).__name__}"] += 1
    return sprite_images, duplicate_names, scanned


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def copy_or_save_image(image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export key-character animation sprite frames with UnityPy.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--source-bundle", action="append", required=True)
    parser.add_argument("--controller", required=True, help="AnimatorController path relative to the exported Unity project root.")
    parser.add_argument("--animation-dir", default="Assets/AnimationClip")
    parser.add_argument("--sprite-dir", default="Assets/Sprite")
    parser.add_argument("--character-name", default="关键角色")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overwrite", action="store_true", help="Delete output-dir before exporting.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    controller = project_root / args.controller
    output_dir = Path(args.output_dir).resolve()
    frames_dir = output_dir / "frames"
    anim_dir = project_root / args.animation_dir
    sprite_dir = project_root / args.sprite_dir
    bundle_paths = [Path(p).resolve() for p in args.source_bundle]

    if args.overwrite and output_dir.exists():
        shutil.rmtree(output_dir)
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    clip_index = build_guid_index(anim_dir, ".anim")
    sprite_index = build_guid_index(sprite_dir, ".asset")
    clip_guids = parse_controller_clip_guids(controller)
    clips = [clip_index[guid] for guid in clip_guids if guid in clip_index]
    missing_clips = [guid for guid in clip_guids if guid not in clip_index]

    animations = []
    wanted_sprite_names: set[str] = set()
    for clip in clips:
        sprite_guids = parse_animation_sprite_guids(clip.path)
        sprite_refs = [sprite_index[guid] for guid in sprite_guids if guid in sprite_index]
        missing_sprites = [guid for guid in sprite_guids if guid not in sprite_index]
        if not sprite_refs:
            continue
        for sprite in sprite_refs:
            wanted_sprite_names.add(sprite.name)
        animations.append(
            {
                "clip": clip,
                "sprite_refs": sprite_refs,
                "missing_sprite_guids": missing_sprites,
            }
        )

    sprite_images, duplicate_names, scanned = load_unitypy_sprites(bundle_paths, wanted_sprite_names)

    rows = []
    exported_count = 0
    animation_name_counts = Counter()
    for anim in animations:
        clip: AssetRef = anim["clip"]
        base = safe_slug(clip.name)
        animation_name_counts[base] += 1
        if animation_name_counts[base] > 1:
            base = f"{base}_{animation_name_counts[base]}"
        folder = frames_dir / base
        for index, sprite in enumerate(anim["sprite_refs"], start=1):
            image = sprite_images.get(sprite.name)
            out_name = f"{base}_{index}.png"
            out_path = folder / out_name
            status = "exported"
            width = height = ""
            if image is None:
                status = "missing_unitypy_sprite"
            else:
                width, height = image.size
                copy_or_save_image(image, out_path)
                exported_count += 1
            rows.append(
                {
                    "animation_name": clip.name,
                    "normalized_animation": base,
                    "frame_index": index,
                    "frame_file": str(out_path.relative_to(output_dir)).replace("\\", "/") if status == "exported" else "",
                    "export_name": out_name,
                    "sprite_name": sprite.name,
                    "sprite_asset": str(sprite.path.relative_to(project_root)).replace("\\", "/"),
                    "sprite_guid": sprite.guid,
                    "width": width,
                    "height": height,
                    "status": status,
                    "naming_rule": f"{base}_{{frame_index}}.png",
                }
            )

    write_csv(
        output_dir / "key_character_animation_frames.csv",
        rows,
        [
            "animation_name",
            "normalized_animation",
            "frame_index",
            "frame_file",
            "export_name",
            "sprite_name",
            "sprite_asset",
            "sprite_guid",
            "width",
            "height",
            "status",
            "naming_rule",
        ],
    )

    animation_summary_rows = []
    for anim_name, items in defaultdict(list, ((k, []) for k in [])).items():
        pass
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["normalized_animation"]].append(row)
    for normalized, items in sorted(grouped.items()):
        exported = sum(1 for item in items if item["status"] == "exported")
        animation_summary_rows.append(
            {
                "normalized_animation": normalized,
                "animation_name": items[0]["animation_name"],
                "frame_count": len(items),
                "exported_count": exported,
                "folder": f"frames/{normalized}",
                "naming_rule": items[0]["naming_rule"],
            }
        )
    write_csv(
        output_dir / "key_character_animation_summary.csv",
        animation_summary_rows,
        ["normalized_animation", "animation_name", "frame_count", "exported_count", "folder", "naming_rule"],
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "controller": str(controller),
        "source_bundles": [str(path) for path in bundle_paths],
        "clip_guid_count": len(clip_guids),
        "resolved_clip_count": len(clips),
        "animation_with_sprite_count": len(animations),
        "wanted_sprite_count": len(wanted_sprite_names),
        "unitypy_sprite_found_count": len(sprite_images),
        "exported_frame_count": exported_count,
        "missing_clip_guids": missing_clips,
        "duplicate_sprite_names": {name: count for name, count in duplicate_names.items() if count > 1},
        "unitypy_scanned": dict(scanned),
    }
    (output_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# {args.character_name}动画帧导出说明",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 控制器：`{args.controller}`",
        f"- 解析到控制器动画引用：`{len(clip_guids)}` 个 GUID，匹配到 `{len(clips)}` 个 AnimationClip。",
        f"- 含 Sprite 帧的动画：`{len(animations)}` 个。",
        f"- UnityPy 找到 Sprite：`{len(sprite_images)}` / `{len(wanted_sprite_names)}`。",
        f"- 已导出帧数：`{exported_count}`。",
        "",
        "## 命名规则",
        "",
        "- 动画名转小写。",
        "- 空格和符号转 `_`。",
        "- 常见状态词归一：`Anticipate/Anticitape/Antcitape` -> `antic`，`Recover` -> `recover`，`Loop` -> `loop`。",
        "- 动画名中的数字补到两位，例如 `Attack1` -> `attack01`、`Slash2` -> `slash02`。",
        "- 帧文件命名：`{normalized_animation}_{frame_index}.png`，帧号不补零，例如 `attack01_1.png`、`attack01_2.png`、`idle_1.png`。",
        "",
        "## 输出文件",
        "",
        "- `frames/`：按动画分组的 PNG 帧。",
        "- `key_character_animation_summary.csv`：动画级清单。",
        "- `key_character_animation_frames.csv`：逐帧对照表，包含原 Sprite 名、GUID、尺寸和导出状态。",
        "- `manifest.json`：脚本运行摘要。",
        "",
        "## 来源与边界",
        "",
        "- 观察到的事实：帧图像由 UnityPy 从用户授权的本地 Addressables bundle 中读取 `Sprite.data.image` 导出。",
        "- 观察到的事实：动画序列由本地还原工程的 AnimatorController 与 `.anim` YAML 中的 `m_Sprite` 引用解析得到。",
        "- 我的判断：这些帧适合本机学习角色动作拆分、帧间节奏、轮廓变化和命名规范；原创项目应重新绘制角色与动作表达。",
    ]
    (output_dir / "README.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

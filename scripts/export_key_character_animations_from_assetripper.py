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

from PIL import Image


GUID_RE = re.compile(r"guid:\s*([0-9a-f]{32})")
MOTION_RE = re.compile(r"m_Motion:\s*\{fileID:\s*7400000,\s*guid:\s*([0-9a-f]{32}),\s*type:\s*2\}")
SPRITE_VALUE_RE = re.compile(r"value:\s*\{fileID:\s*21300000,\s*guid:\s*([0-9a-f]{32}),\s*type:\s*2\}")
NAME_RE = re.compile(r"^\s*m_Name:\s*(.+?)\s*$", re.MULTILINE)
RECT_RE = re.compile(
    r"m_Rect:\s*\n"
    r"\s*serializedVersion:\s*\d+\s*\n"
    r"\s*x:\s*([-0-9.]+)\s*\n"
    r"\s*y:\s*([-0-9.]+)\s*\n"
    r"\s*width:\s*([-0-9.]+)\s*\n"
    r"\s*height:\s*([-0-9.]+)",
    re.MULTILINE,
)
RD_TEXTURE_RE = re.compile(r"m_RD:.*?texture:\s*\{fileID:\s*2800000,\s*guid:\s*([0-9a-f]{32}),\s*type:\s*3\}", re.S)


@dataclass
class AssetRef:
    guid: str
    path: Path
    name: str


@dataclass
class SpriteInfo:
    guid: str
    path: Path
    name: str
    texture_guid: str
    rect: tuple[int, int, int, int]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def safe_slug(name: str) -> str:
    text = name.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    text = re.sub(r"(?<=[a-z])(\d+)", lambda match: f"{int(match.group(1)):02d}", text)
    return text or "anim"


def parse_guid_from_meta(meta: Path) -> str | None:
    match = GUID_RE.search(read_text(meta))
    return match.group(1) if match else None


def parse_name(path: Path) -> str:
    match = NAME_RE.search(read_text(path))
    return match.group(1).strip().strip('"') if match else path.stem


def build_guid_index(root: Path) -> dict[str, AssetRef]:
    index: dict[str, AssetRef] = {}
    for meta in root.rglob("*.meta"):
        guid = parse_guid_from_meta(meta)
        if not guid:
            continue
        asset = Path(str(meta)[: -len(".meta")])
        if asset.exists():
            index[guid] = AssetRef(guid=guid, path=asset, name=parse_name(asset) if asset.suffix in {".anim", ".asset"} else asset.stem)
    return index


def parse_controller_clip_guids(controller: Path) -> list[str]:
    seen: set[str] = set()
    guids: list[str] = []
    for guid in MOTION_RE.findall(read_text(controller)):
        if guid not in seen:
            seen.add(guid)
            guids.append(guid)
    return guids


def parse_animation_sprite_guids(anim: Path) -> list[str]:
    text = read_text(anim)
    if "attribute: m_Sprite" not in text:
        return []
    return SPRITE_VALUE_RE.findall(text)


def parse_sprite(sprite: AssetRef) -> SpriteInfo | None:
    text = read_text(sprite.path)
    rect_match = RECT_RE.search(text)
    texture_match = RD_TEXTURE_RE.search(text)
    if not rect_match or not texture_match:
        return None
    x, y, width, height = (int(round(float(value))) for value in rect_match.groups())
    return SpriteInfo(
        guid=sprite.guid,
        path=sprite.path,
        name=parse_name(sprite.path),
        texture_guid=texture_match.group(1),
        rect=(x, y, width, height),
    )


def crop_sprite(texture_path: Path, rect: tuple[int, int, int, int], output_path: Path) -> tuple[int, int]:
    x, y, width, height = rect
    with Image.open(texture_path) as image:
        image = image.convert("RGBA")
        tex_w, tex_h = image.size
        left = max(0, x)
        upper = max(0, tex_h - y - height)
        right = min(tex_w, x + width)
        lower = min(tex_h, tex_h - y)
        cropped = image.crop((left, upper, right, lower))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(output_path)
        return cropped.size


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export key-character sprite slices from an AssetRipper Unity project.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--controller", required=True, help="AnimatorController path relative to project root.")
    parser.add_argument("--character-name", default="关键角色")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    controller = project_root / args.controller
    output_dir = Path(args.output_dir).resolve()
    frames_dir = output_dir / "frames"

    if args.overwrite and output_dir.exists():
        shutil.rmtree(output_dir)
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    guid_index = build_guid_index(project_root)
    clip_guids = parse_controller_clip_guids(controller)
    clips = [guid_index[guid] for guid in clip_guids if guid in guid_index and guid_index[guid].path.suffix == ".anim"]
    missing_clip_guids = [guid for guid in clip_guids if guid not in guid_index]

    rows: list[dict] = []
    summary_rows: list[dict] = []
    exported_count = 0
    animation_name_counts = Counter()
    texture_cache: dict[str, Path] = {}
    sprite_cache: dict[str, SpriteInfo | None] = {}

    for clip in clips:
        sprite_guids = parse_animation_sprite_guids(clip.path)
        if not sprite_guids:
            summary_rows.append(
                {
                    "normalized_animation": safe_slug(clip.name),
                    "animation_name": clip.name,
                    "frame_count": 0,
                    "exported_count": 0,
                    "folder": "",
                    "naming_rule": "",
                    "remarks": "no m_Sprite object-reference frames; likely transform/rig animation only",
                }
            )
            continue

        base = safe_slug(clip.name)
        animation_name_counts[base] += 1
        if animation_name_counts[base] > 1:
            base = f"{base}_{animation_name_counts[base]}"
        folder = frames_dir / base
        exported_for_clip = 0

        for index, sprite_guid in enumerate(sprite_guids, start=1):
            sprite_ref = guid_index.get(sprite_guid)
            status = "exported"
            remarks = ""
            width = height = ""
            source_texture = ""
            sprite_asset = ""
            rect_text = ""
            out_name = f"{base}_{index}.png"
            out_path = folder / out_name

            if not sprite_ref:
                status = "missing_sprite_guid"
            else:
                sprite_asset = str(sprite_ref.path.relative_to(project_root)).replace("\\", "/")
                if sprite_guid not in sprite_cache:
                    sprite_cache[sprite_guid] = parse_sprite(sprite_ref)
                sprite_info = sprite_cache[sprite_guid]
                if not sprite_info:
                    status = "unreadable_sprite_yaml"
                else:
                    rect_text = ",".join(str(value) for value in sprite_info.rect)
                    if sprite_info.texture_guid not in texture_cache:
                        texture_ref = guid_index.get(sprite_info.texture_guid)
                        if texture_ref and texture_ref.path.exists():
                            texture_cache[sprite_info.texture_guid] = texture_ref.path
                    texture_path = texture_cache.get(sprite_info.texture_guid)
                    if not texture_path:
                        status = "missing_texture_guid"
                    else:
                        source_texture = str(texture_path.relative_to(project_root)).replace("\\", "/")
                        try:
                            width, height = crop_sprite(texture_path, sprite_info.rect, out_path)
                            exported_count += 1
                            exported_for_clip += 1
                        except Exception as exc:  # pragma: no cover - data dependent
                            status = "crop_error"
                            remarks = f"{type(exc).__name__}: {exc}"

            rows.append(
                {
                    "animation_name": clip.name,
                    "normalized_animation": base,
                    "frame_index": index,
                    "frame_file": str(out_path.relative_to(output_dir)).replace("\\", "/") if status == "exported" else "",
                    "export_name": out_name,
                    "sprite_name": sprite_ref.name if sprite_ref else "",
                    "sprite_asset": sprite_asset,
                    "sprite_guid": sprite_guid,
                    "source_texture": source_texture,
                    "sprite_rect_xywh": rect_text,
                    "width": width,
                    "height": height,
                    "status": status,
                    "remarks": remarks,
                    "naming_rule": f"{base}_{{frame_index}}.png",
                }
            )

        summary_rows.append(
            {
                "normalized_animation": base,
                "animation_name": clip.name,
                "frame_count": len(sprite_guids),
                "exported_count": exported_for_clip,
                "folder": f"frames/{base}",
                "naming_rule": f"{base}_{{frame_index}}.png",
                "remarks": "sprite slices referenced by this animation; transform curves remain in the .anim file",
            }
        )

    frame_fields = [
        "animation_name",
        "normalized_animation",
        "frame_index",
        "frame_file",
        "export_name",
        "sprite_name",
        "sprite_asset",
        "sprite_guid",
        "source_texture",
        "sprite_rect_xywh",
        "width",
        "height",
        "status",
        "remarks",
        "naming_rule",
    ]
    write_csv(output_dir / "key_character_animation_frames.csv", rows, frame_fields)
    write_csv(
        output_dir / "key_character_animation_summary.csv",
        summary_rows,
        ["normalized_animation", "animation_name", "frame_count", "exported_count", "folder", "naming_rule", "remarks"],
    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "controller": str(controller),
        "character_name": args.character_name,
        "clip_guid_count": len(clip_guids),
        "resolved_clip_count": len(clips),
        "missing_clip_guids": missing_clip_guids,
        "animation_count": len(summary_rows),
        "sprite_reference_count": len(rows),
        "exported_slice_count": exported_count,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "notes": [
            "This route crops Sprite slices from AssetRipper-exported Texture2D PNG files.",
            "For rigged/cutout 2D animation, these are referenced parts, not fully composited rendered character frames.",
        ],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = [
        f"# {args.character_name}动画素材导出说明",
        "",
        f"- 生成时间：{manifest['generated_at']}",
        f"- 控制器：`{args.controller}`",
        f"- 控制器动画引用：`{len(clip_guids)}` 个 GUID，匹配到 `{len(clips)}` 个 AnimationClip。",
        f"- 动画条目：`{len(summary_rows)}`。",
        f"- Sprite 引用：`{len(rows)}`，成功切出 `{exported_count}` 张。",
        "",
        "## 命名规则",
        "",
        "- 动画名转小写，符号转 `_`。",
        "- 动画名中的数字补到两位，帧号不补零。",
        "- 帧文件命名：`{normalized_animation}_{frame_index}.png`。",
        "",
        "## 重要说明",
        "",
        "- 观察到的事实：该角色控制器包含大量 Transform/Rotation 曲线，说明角色动画是骨骼/切片式组合，而不是每个动作一张完整序列帧。",
        "- 观察到的事实：本目录导出的是 `.anim` 里 `m_Sprite` 引用到的 Sprite 切片；完整动作还需要结合 `.anim` 中的位移、旋转、缩放曲线理解。",
        "- 我的判断：这些切片适合学习角色部件拆分、局部替换和动作命名；原创项目应重新绘制角色部件并重新制作动画。",
    ]
    (output_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

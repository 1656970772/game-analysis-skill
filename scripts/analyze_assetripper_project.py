from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None


CLASS_NAMES = {
    1: "GameObject",
    4: "Transform",
    20: "Camera",
    23: "MeshRenderer",
    33: "MeshFilter",
    50: "Rigidbody2D",
    54: "Rigidbody",
    58: "CircleCollider2D",
    60: "PolygonCollider2D",
    61: "BoxCollider2D",
    95: "Animator",
    104: "RenderSettings",
    108: "Light",
    114: "MonoBehaviour",
    157: "LightmapSettings",
    198: "ParticleSystem",
    199: "ParticleSystemRenderer",
    212: "SpriteRenderer",
    222: "CanvasRenderer",
    223: "Canvas",
    224: "RectTransform",
    225: "CanvasGroup",
}

MARKER_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)", re.MULTILINE)
NAME_RE = re.compile(r"^\s*m_Name:\s*(.*)$", re.MULTILINE)
GAMEOBJECT_RE = re.compile(r"m_GameObject:\s*\{fileID:\s*(-?\d+)")
FATHER_RE = re.compile(r"m_Father:\s*\{fileID:\s*(-?\d+)")
COMPONENT_RE = re.compile(r"component:\s*\{fileID:\s*(-?\d+)")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def clean_name(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace("\x00", "").strip()


def iter_yaml_objects(text: str):
    matches = list(MARKER_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield int(match.group(1)), int(match.group(2)), text[match.start() : end]


def scene_group(path: Path, project_root: Path) -> str:
    parts = path.relative_to(project_root).parts
    try:
        idx = parts.index("Levels")
        return parts[idx + 1] if idx + 1 < len(parts) else "Levels"
    except ValueError:
        if len(parts) >= 3 and parts[0] == "Assets" and parts[1] == "Game" and parts[2] == "Scenes":
            return "ScenesRoot"
        return parts[0] if parts else "Unknown"


def guess_category(path: Path) -> str:
    ext = path.suffix.lower()
    rel_lower = path.as_posix().lower()
    if ext == ".unity":
        return "scene"
    if ext == ".prefab":
        return "prefab"
    if ext in {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".psd"}:
        if "/ui/" in rel_lower or "icon" in rel_lower:
            return "image_ui_icon"
        if "character" in rel_lower or "/characters/" in rel_lower:
            return "image_character"
        if "effect" in rel_lower or "vfx" in rel_lower:
            return "image_effect"
        if "scene" in rel_lower or "level" in rel_lower or "map" in rel_lower:
            return "image_scene"
        return "image"
    if ext in {".anim", ".controller", ".overridecontroller", ".playable"}:
        return "animation"
    if ext in {".mat", ".shader", ".rendertexture", ".physicmaterial", ".physicsmaterial2d"}:
        return "material_shader"
    if ext in {".ogg", ".wav", ".mp3", ".bank"}:
        return "audio"
    if ext in {".ttf", ".otf"}:
        return "font"
    if ext in {".cs", ".dll"}:
        return "code_assembly"
    if ext in {".asset", ".json", ".txt", ".xml"}:
        return "data_config"
    if ext in {".bundle", ".resources", ".ress"}:
        return "streaming_package"
    if ext == ".meta":
        return "unity_meta"
    return "other"


def analyze_yaml_asset(path: Path, project_root: Path) -> dict:
    text = read_text(path)
    class_counts: Counter[int] = Counter()
    gameobject_names: dict[int, str] = {}
    transform_to_gameobject: dict[int, int] = {}
    transform_father: dict[int, int] = {}
    component_refs: dict[int, list[int]] = {}

    for class_id, file_id, block in iter_yaml_objects(text):
        class_counts[class_id] += 1
        if class_id == 1:
            name_match = NAME_RE.search(block)
            gameobject_names[file_id] = clean_name(name_match.group(1) if name_match else "")
            component_refs[file_id] = [int(item) for item in COMPONENT_RE.findall(block)]
        elif class_id in {4, 224}:
            go_match = GAMEOBJECT_RE.search(block)
            father_match = FATHER_RE.search(block)
            if go_match:
                transform_to_gameobject[file_id] = int(go_match.group(1))
            if father_match:
                transform_father[file_id] = int(father_match.group(1))

    roots = []
    for transform_id, go_id in transform_to_gameobject.items():
        father = transform_father.get(transform_id, 0)
        if father == 0 and go_id in gameobject_names:
            roots.append(gameobject_names[go_id])

    top_names = [name for name, _ in Counter(n for n in roots if n).most_common(16)]
    sample_names = [name for name, _ in Counter(n for n in gameobject_names.values() if n).most_common(24)]
    keywords = {
        "tilemap": text.lower().count("tilemap"),
        "grid": len(re.findall(r"\bGrid\b", text)),
        "sorting_layer": text.count("m_SortingLayer"),
        "sprite_guid_ref": text.count("m_Sprite: {fileID:"),
        "cinemachine": text.lower().count("cinemachine"),
        "timeline": text.lower().count("timeline"),
    }

    collider_count = sum(class_counts.get(cid, 0) for cid in (58, 60, 61))
    tags = []
    if class_counts.get(212, 0) >= 20:
        tags.append("SpriteRenderer分层")
    if class_counts.get(198, 0) > 0:
        tags.append("粒子特效")
    if class_counts.get(223, 0) > 0 or class_counts.get(224, 0) >= 20:
        tags.append("UI/RectTransform")
    if class_counts.get(95, 0) > 0:
        tags.append("Animator")
    if collider_count > 0:
        tags.append("2D碰撞")
    if keywords["tilemap"] > 0:
        tags.append("Tilemap线索")
    if keywords["cinemachine"] > 0:
        tags.append("Cinemachine线索")
    if not tags:
        tags.append("轻量场景片段")

    return {
        "relative_path": rel(path, project_root),
        "group": scene_group(path, project_root) if path.suffix.lower() == ".unity" else prefab_group(path, project_root),
        "file_size": path.stat().st_size,
        "yaml_object_count": sum(class_counts.values()),
        "class_counts": dict(class_counts),
        "gameobject_count": class_counts.get(1, 0),
        "transform_count": class_counts.get(4, 0),
        "recttransform_count": class_counts.get(224, 0),
        "spriterenderer_count": class_counts.get(212, 0),
        "particle_count": class_counts.get(198, 0),
        "animator_count": class_counts.get(95, 0),
        "canvas_count": class_counts.get(223, 0),
        "camera_count": class_counts.get(20, 0),
        "collider2d_count": collider_count,
        "monobehaviour_count": class_counts.get(114, 0),
        "tilemap_keyword_count": keywords["tilemap"],
        "grid_keyword_count": keywords["grid"],
        "sprite_guid_ref_count": keywords["sprite_guid_ref"],
        "cinemachine_keyword_count": keywords["cinemachine"],
        "timeline_keyword_count": keywords["timeline"],
        "root_names": " | ".join(top_names),
        "sample_names": " | ".join(sample_names),
        "scene_building_tags": " | ".join(tags),
    }


def prefab_group(path: Path, project_root: Path) -> str:
    parts = path.relative_to(project_root).parts
    if "Prefabs" in parts:
        idx = parts.index("Prefabs")
        return parts[idx + 1] if idx + 1 < len(parts) else "Prefabs"
    if "Resources" in parts:
        idx = parts.index("Resources")
        return parts[idx + 1] if idx + 1 < len(parts) else "Resources"
    return parts[0] if parts else "Unknown"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_file_inventory(project_root: Path) -> tuple[list[dict], Counter[str], Counter[str]]:
    rows = []
    ext_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower() or "(no_ext)"
        category = guess_category(path.relative_to(project_root))
        ext_counts[ext] += 1
        category_counts[category] += 1
        rows.append(
            {
                "relative_path": rel(path, project_root),
                "extension": ext,
                "category": category,
                "size_bytes": path.stat().st_size,
            }
        )
    rows.sort(key=lambda row: row["relative_path"].lower())
    return rows, ext_counts, category_counts


def collect_image_index(project_root: Path) -> list[dict]:
    if Image is None:
        return []
    rows = []
    for path in project_root.rglob("*.png"):
        try:
            with Image.open(path) as img:
                mode = img.mode
                has_alpha = mode in {"RGBA", "LA"} or ("transparency" in img.info)
                rows.append(
                    {
                        "relative_path": rel(path, project_root),
                        "width": img.width,
                        "height": img.height,
                        "mode": mode,
                        "has_alpha": str(has_alpha).lower(),
                        "category": guess_category(path.relative_to(project_root)),
                        "size_bytes": path.stat().st_size,
                    }
                )
        except Exception as exc:
            rows.append(
                {
                    "relative_path": rel(path, project_root),
                    "width": "",
                    "height": "",
                    "mode": "",
                    "has_alpha": "",
                    "category": guess_category(path.relative_to(project_root)),
                    "size_bytes": path.stat().st_size,
                    "error": str(exc),
                }
            )
    rows.sort(key=lambda row: (str(row.get("category")), str(row.get("relative_path")).lower()))
    return rows


def make_markdown_table(rows: list[dict], columns: list[str], limit: int = 12) -> str:
    if not rows:
        return "暂无数据。\n"
    out = []
    out.append("| " + " | ".join(columns) + " |")
    out.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for row in rows[:limit]:
        values = []
        for col in columns:
            value = str(row.get(col, ""))
            if len(value) > 90:
                value = value[:87] + "..."
            values.append(value.replace("|", "/"))
        out.append("| " + " | ".join(values) + " |")
    return "\n".join(out) + "\n"


def write_report(
    output_path: Path,
    project_root: Path,
    project_title: str,
    file_rows: list[dict],
    ext_counts: Counter[str],
    category_counts: Counter[str],
    scene_rows: list[dict],
    prefab_rows: list[dict],
    image_rows: list[dict],
) -> None:
    scene_groups = Counter(row["group"] for row in scene_rows)
    prefab_groups = Counter(row["group"] for row in prefab_rows)
    total_size = sum(int(row["size_bytes"]) for row in file_rows)
    top_sprite_scenes = sorted(scene_rows, key=lambda row: int(row["spriterenderer_count"]), reverse=True)
    top_particle_scenes = sorted(scene_rows, key=lambda row: int(row["particle_count"]), reverse=True)
    top_canvas_scenes = sorted(scene_rows, key=lambda row: int(row["canvas_count"]), reverse=True)
    top_go_scenes = sorted(scene_rows, key=lambda row: int(row["gameobject_count"]), reverse=True)
    top_prefabs = sorted(prefab_rows, key=lambda row: int(row["gameobject_count"]), reverse=True)
    large_images = sorted(
        image_rows,
        key=lambda row: (int(row["width"] or 0) * int(row["height"] or 0), int(row["size_bytes"] or 0)),
        reverse=True,
    )

    ext_table_rows = [
        {"扩展名": ext, "数量": count}
        for ext, count in ext_counts.most_common(18)
    ]
    cat_table_rows = [
        {"类别": cat, "数量": count}
        for cat, count in category_counts.most_common(18)
    ]
    scene_group_rows = [
        {"场景分组": group, "数量": count}
        for group, count in scene_groups.most_common()
    ]
    prefab_group_rows = [
        {"Prefab分组": group, "数量": count}
        for group, count in prefab_groups.most_common(18)
    ]

    now = datetime.now(timezone.utc).isoformat()
    report = f"""# {project_title} 全量资源导出与场景搭建分析

生成时间：{now}

## 来源与边界

- 观察到的事实：本报告读取本地授权生成的 Unity 项目结构，并基于其中的 `.unity`、`.prefab`、`.png` 和 Unity YAML 文本做本地统计；具体目录见同级 `ExportedProject` 文件夹。
- 观察到的事实：全量资源导出目录内共有 `{len(file_rows)}` 个文件，合计约 `{total_size / 1024 / 1024:.2f}` MiB。
- 我的判断：这些结果适合在本机学习 2D Unity 项目的层级组织、场景拆分、Prefab 复用、SpriteRenderer 分层、粒子和 UI 结构；原创项目应重新制作对应视觉内容。

## 全量资源概览

{make_markdown_table(ext_table_rows, ["扩展名", "数量"], limit=18)}

{make_markdown_table(cat_table_rows, ["类别", "数量"], limit=18)}

关键索引：

- 全量文件清单：`导出资源文件清单.csv`
- 场景索引：`场景索引.csv`
- Prefab 索引：`Prefab索引.csv`
- 图片尺寸索引：`图片资源尺寸索引.csv`

## 场景分组

观察到的事实：导出的 `.unity` 场景共 `{len(scene_rows)}` 个，主要分布如下。

{make_markdown_table(scene_group_rows, ["场景分组", "数量"], limit=30)}

我的判断：`Assets/Game/Scenes/Levels/*` 的多分组命名说明关卡被拆成许多小场景片段；这类结构通常利于 Addressables/分段加载、区域复用、调试单个房间，以及控制横版探索游戏的内存占用。

## 场景搭建线索

SpriteRenderer 数量最高的场景：

{make_markdown_table(top_sprite_scenes, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "collider2d_count", "scene_building_tags"], limit=12)}

GameObject 数量最高的场景：

{make_markdown_table(top_go_scenes, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "canvas_count", "root_names"], limit=12)}

粒子系统数量最高的场景：

{make_markdown_table(top_particle_scenes, ["relative_path", "group", "gameobject_count", "particle_count", "spriterenderer_count", "root_names"], limit=12)}

Canvas/UI 数量最高的场景：

{make_markdown_table(top_canvas_scenes, ["relative_path", "group", "gameobject_count", "canvas_count", "recttransform_count", "root_names"], limit=12)}

我的判断：

- 场景主要不是单张背景图铺满，而是由多个 GameObject、SpriteRenderer、碰撞体、粒子和少量控制脚本组合出来。
- 学习时可以先看 SpriteRenderer 多、GameObject 多的场景，理解前景/中景/背景的层次拆法；再看 ParticleSystem 多的场景，理解氛围、攻击、机关或环境动效的挂载位置。
- UI 与关卡场景是分层存在的：带 Canvas/RectTransform 的场景更适合学习菜单、HUD、过场或交互面板，不应和关卡地形搭建混在一起分析。

## Prefab 组织

观察到的事实：导出的 `.prefab` 共 `{len(prefab_rows)}` 个，主要分组如下。

{make_markdown_table(prefab_group_rows, ["Prefab分组", "数量"], limit=18)}

GameObject 数量最高的 Prefab：

{make_markdown_table(top_prefabs, ["relative_path", "group", "gameobject_count", "spriterenderer_count", "particle_count", "animator_count", "root_names"], limit=14)}

我的判断：Prefab 更适合学习“可复用对象”的拆法，例如角色、敌人、机关、特效、管理器和 UI 单元。看 Prefab 时重点关注根节点命名、子节点职责、Animator/ParticleSystem 是否被拆成子对象，以及同类对象是否保持一致层级。

## 图片资源入口

观察到的事实：导出的 PNG 图片共 `{len(image_rows)}` 张。面积最大的图片如下，适合先用作美术结构入口，但仍只建议本地查看学习。

{make_markdown_table(large_images, ["relative_path", "width", "height", "mode", "has_alpha", "category"], limit=16)}

我的判断：学习场景搭建时，不要只看单张图；更关键的是回到 `.unity` 场景里观察这些图如何被缩放、排序、分组、加碰撞、加粒子和绑定脚本。

## 推荐学习路径

1. 从 `Assets/Game/Scenes/Levels` 选一个 GameObject 数量高的场景，观察根节点和层级命名。
2. 打开同场景中 SpriteRenderer 数量高的对象，记录排序层、缩放、透明度、重复图块和遮挡关系。
3. 对照 `Assets/Game/Prefabs` 与其他 Prefab 目录，找出场景中反复出现的对象，理解哪些是关卡静态装饰，哪些是可复用玩法对象。
4. 单独查看 ParticleSystem 数量高的场景，提炼原创项目可迁移的粒子职责：环境漂浮、攻击反馈、受击反馈、机关提示、传送/过场。
5. 单独查看 Canvas/RectTransform 多的场景，拆 UI 的字号、图标尺寸、按钮状态、面板边距和视觉层级。

## 给原创项目的可迁移规范

- 场景拆分：按区域或房间拆小场景，每个场景保持清晰根节点，如 `Background`、`Middleground`、`Foreground`、`Collision`、`Triggers`、`FX`、`Lighting`、`Camera`。
- 图层组织：SpriteRenderer 以排序层和 Z/局部层级双重控制，避免把美术层、碰撞层、触发层混在同一个节点树里。
- Prefab 规范：可交互对象、敌人、机关和特效都做成可复用 Prefab；视觉子节点、碰撞子节点、脚本根节点分离，方便替换原创素材。
- 动效规范：角色和敌人用 Animator 管状态，环境氛围优先用粒子/短循环动画，UI 使用独立 Canvas 层。
- 色彩规范：为原创项目建立主色、辅助色、危险/交互提示色和低饱和环境色的分层表；同一关卡内控制冷暖对比，避免每个对象都抢视觉焦点。
- 形状语言：把角色、敌人、机关、地形、UI 图标分别定义成不同轮廓节奏；例如角色更强调可读外轮廓，机关强调硬边和方向性，背景强调大块面和重复节奏。
- 学习边界：只抽象结构方法、比例关系、层级命名习惯和制作约束；新项目应使用自己的角色、场景、图标、UI、动画和叙事表达。

## 待验证事项

- 需要在 Unity 2023.2.22f1 或兼容版本中打开项目后，进一步确认排序层、物理层、Addressables 分组和运行时加载逻辑。
- 需要结合游戏内实际游玩画面，验证 YAML 统计推断出的前景/中景/背景关系是否与运行时镜头一致。
- 需要为原创项目重新制作色板、角色轮廓、UI 图标和关卡装饰，不应沿用本地学习样本的具体视觉表达。

## 文件说明

- `ExportedProject/Assets/Game/Scenes`：关卡与流程场景入口。
- `ExportedProject/Assets/Game/Prefabs`：项目内明确归档的游戏 Prefab。
- `ExportedProject/Assets`：还包含更多由工具还原出的资源、脚本、材质、字体、音频和数据文件。
- `ExportedProject/ProjectSettings`：Unity 项目设置，可辅助理解渲染、输入、标签层等配置。
- `ExportedProject/Packages`：包配置，可辅助判断项目依赖。

"""
    output_path.write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title", default="目标游戏")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    file_rows, ext_counts, category_counts = collect_file_inventory(project_root)
    scene_paths = sorted(project_root.rglob("*.unity"))
    prefab_paths = sorted(project_root.rglob("*.prefab"))
    scene_rows = [analyze_yaml_asset(path, project_root) for path in scene_paths]
    prefab_rows = [analyze_yaml_asset(path, project_root) for path in prefab_paths]
    image_rows = collect_image_index(project_root)

    inventory_fields = ["relative_path", "extension", "category", "size_bytes"]
    yaml_fields = [
        "relative_path",
        "group",
        "file_size",
        "yaml_object_count",
        "gameobject_count",
        "transform_count",
        "recttransform_count",
        "spriterenderer_count",
        "particle_count",
        "animator_count",
        "canvas_count",
        "camera_count",
        "collider2d_count",
        "monobehaviour_count",
        "tilemap_keyword_count",
        "grid_keyword_count",
        "sprite_guid_ref_count",
        "cinemachine_keyword_count",
        "timeline_keyword_count",
        "root_names",
        "sample_names",
        "scene_building_tags",
    ]
    image_fields = ["relative_path", "width", "height", "mode", "has_alpha", "category", "size_bytes", "error"]

    write_csv(output_dir / "导出资源文件清单.csv", file_rows, inventory_fields)
    write_csv(output_dir / "场景索引.csv", scene_rows, yaml_fields)
    write_csv(output_dir / "Prefab索引.csv", prefab_rows, yaml_fields)
    write_csv(output_dir / "图片资源尺寸索引.csv", image_rows, image_fields)
    write_report(
        output_dir / "场景搭建分析.md",
        project_root,
        args.title,
        file_rows,
        ext_counts,
        category_counts,
        scene_rows,
        prefab_rows,
        image_rows,
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "output_dir": str(output_dir),
        "file_count": len(file_rows),
        "scene_count": len(scene_rows),
        "prefab_count": len(prefab_rows),
        "png_count": len(image_rows),
        "top_extensions": ext_counts.most_common(30),
        "top_categories": category_counts.most_common(30),
    }
    (output_dir / "导出分析摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

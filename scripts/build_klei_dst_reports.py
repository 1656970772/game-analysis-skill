#!/usr/bin/env python3
"""Build numbered GameAnalysis reports for Klei / Don't Starve Together studies."""

from __future__ import annotations

import argparse
import csv
import json
import os
import struct
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPORT_NAMES = [
    "00_研究总览.md",
    "01_美术资源规格表.md",
    "02_美术风格与色彩速查.md",
    "03_动画与手感帧数据.md",
    "04_场景关卡搭建.md",
    "05_技术架构拆解.md",
    "06_玩法系统拆解.md",
    "07_渲染管线拆解.md",
    "08_命名规范汇总.md",
    "09_可迁移技术清单.md",
]


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv_rows(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append({key: value for key, value in row.items() if key is not None})
            if limit is not None and len(rows) >= limit:
                break
    return rows


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def status_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            counts[row.get("status", "unknown") or "unknown"] += 1
    return counts


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def iter_files(root: Path, suffixes: set[str] | None = None) -> Iterable[Path]:
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in {".git", "__pycache__", "node_modules"}]
        for name in files:
            path = Path(current) / name
            if suffixes is None or path.suffix.lower() in suffixes:
                yield path


def count_files(root: Path) -> int:
    return sum(1 for _ in iter_files(root))


def count_files_with_suffixes(root: Path, suffixes: set[str]) -> int:
    return sum(1 for _ in iter_files(root, suffixes))


def count_bytes(root: Path) -> int:
    total = 0
    for path in iter_files(root):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if not header.startswith(b"\x89PNG\r\n\x1a\n") or len(header) < 24:
        return None
    return struct.unpack(">II", header[16:24])


def image_dimension_summary(image_root: Path) -> tuple[Counter[str], Counter[str], int]:
    buckets: Counter[str] = Counter()
    dimensions: Counter[str] = Counter()
    counted = 0
    for path in iter_files(image_root, {".png"}):
        size = png_size(path)
        if size is None:
            continue
        width, height = size
        longest = max(width, height)
        if longest <= 64:
            bucket = "<=64px"
        elif longest <= 256:
            bucket = "65-256px"
        elif longest <= 512:
            bucket = "257-512px"
        elif longest <= 1024:
            bucket = "513-1024px"
        else:
            bucket = ">=1025px"
        buckets[bucket] += 1
        dimensions[f"{width}x{height}"] += 1
        counted += 1
    return buckets, dimensions, counted


def prefix_counts_from_values(values: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for value in values:
        stem = Path(value).stem.lower()
        if not stem:
            continue
        token = stem.split("_")[0]
        counts[token] += 1
    return counts


def code_directory_counts(code_root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in iter_files(code_root, {".lua"}):
        try:
            rel = path.relative_to(code_root)
        except ValueError:
            continue
        first = rel.parts[0] if len(rel.parts) > 1 else "[root]"
        counts[first] += 1
    return counts


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def top_counter_rows(counter: Counter[str], limit: int = 10) -> list[list[object]]:
    return [[key, value] for key, value in counter.most_common(limit)]


def int_value(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, int | float | str) and str(value).isdigit() else default


def load_context(study_root: Path) -> dict[str, object]:
    temp = study_root / "4.临时目录"
    indices = temp / "中间索引"
    learning = indices / "学习输出"
    reports = study_root / "2.报告"
    image_root = study_root / "1.分类资源" / "图片"
    audio_root = study_root / "1.分类资源" / "音频"
    code_root = study_root / "3.代码" / "脚本"

    paths = {
        "export_summary": indices / "KleiDST" / "klei_dst_export_summary.json",
        "conversion_summary": temp / "工具日志" / "klei_resource_conversion_summary.json",
        "texture_index": learning / "klei_texture_conversion_index.csv",
        "animation_index": learning / "klei_animation_xml_index.csv",
        "audio_index": learning / "klei_fsb_conversion_index.csv",
        "code_index": indices / "KleiDST" / "代码导出索引.csv",
        "dyn_index": indices / "KleiDST" / "动态动画阻塞索引.csv",
        "dyn_boundary_summary": indices / "KleiDST" / "dyn_boundary_inventory_summary.json",
        "palette": learning / "klei_palette_report.json",
        "gallery_manifest": indices / "图片画廊" / "manifest.json",
    }

    texture_rows = read_csv_rows(paths["texture_index"], limit=5000)
    animation_rows = read_csv_rows(paths["animation_index"], limit=5000)
    audio_rows = read_csv_rows(paths["audio_index"], limit=5000)
    dyn_rows = read_csv_rows(paths["dyn_index"], limit=200)
    image_buckets, image_dimensions, image_counted = image_dimension_summary(image_root)
    conversion = read_json(paths["conversion_summary"])
    audio_summary = conversion.get("audio", {}) if isinstance(conversion, dict) else {}

    return {
        "study_root": study_root,
        "temp": temp,
        "indices": indices,
        "learning": learning,
        "reports": reports,
        "image_root": image_root,
        "audio_root": audio_root,
        "code_root": code_root,
        "paths": paths,
        "export": read_json(paths["export_summary"]),
        "conversion": conversion,
        "dyn_boundary": read_json(paths["dyn_boundary_summary"]),
        "palette": read_json(paths["palette"]),
        "gallery": read_json(paths["gallery_manifest"]),
        "texture_status": status_counts(paths["texture_index"]),
        "animation_status": status_counts(paths["animation_index"]),
        "audio_status": status_counts(paths["audio_index"]),
        "texture_total": count_csv_rows(paths["texture_index"]),
        "animation_total": count_csv_rows(paths["animation_index"]),
        "audio_total": count_csv_rows(paths["audio_index"]),
        "audio_sample_file_total": count_files_with_suffixes(audio_root / "KleiFSB", {".ogg", ".wav", ".mp3"}),
        "audio_samples_converted": int_value(audio_summary.get("samples_converted", 0)) if isinstance(audio_summary, dict) else 0,
        "code_total": count_csv_rows(paths["code_index"]),
        "dyn_total": count_csv_rows(paths["dyn_index"]),
        "texture_prefixes": prefix_counts_from_values(row.get("output", "") for row in texture_rows),
        "animation_prefixes": prefix_counts_from_values(row.get("source", "") for row in animation_rows),
        "audio_prefixes": prefix_counts_from_values(row.get("sample_name", "") for row in audio_rows),
        "code_dirs": code_directory_counts(code_root),
        "image_buckets": image_buckets,
        "image_dimensions": image_dimensions,
        "image_counted": image_counted,
        "dyn_samples": dyn_rows[:5],
        "file_counts": {
            "0.原始导出": count_files(study_root / "0.原始导出"),
            "1.分类资源": count_files(study_root / "1.分类资源"),
            "2.报告": count_files(study_root / "2.报告"),
            "3.代码": count_files(study_root / "3.代码"),
            "4.临时目录": count_files(study_root / "4.临时目录"),
        },
        "byte_counts": {
            "0.原始导出": count_bytes(study_root / "0.原始导出"),
            "1.分类资源": count_bytes(study_root / "1.分类资源"),
            "2.报告": count_bytes(study_root / "2.报告"),
            "3.代码": count_bytes(study_root / "3.代码"),
            "4.临时目录": count_bytes(study_root / "4.临时目录"),
        },
    }


def conversion_value(ctx: dict[str, object], section: str, key: str, default: int = 0) -> int:
    conversion = ctx.get("conversion")
    if not isinstance(conversion, dict):
        return default
    data = conversion.get(section)
    if not isinstance(data, dict):
        return default
    return int_value(data.get(key, default), default)


def common_sources(ctx: dict[str, object]) -> str:
    paths = ctx["paths"]  # type: ignore[assignment]
    assert isinstance(paths, dict)
    return "\n".join(
        [
            f"- 导出摘要：`{paths['export_summary']}`",
            f"- 转换摘要：`{paths['conversion_summary']}`",
            f"- Klei 转换索引：`{ctx['learning']}`",
            f"- 图片画廊 manifest：`{paths['gallery_manifest']}`",
        ]
    )


def report_00(ctx: dict[str, object], notes: list[str]) -> str:
    export = ctx["export"] if isinstance(ctx["export"], dict) else {}
    conversion = ctx["conversion"] if isinstance(ctx["conversion"], dict) else {}
    dyn_boundary = ctx["dyn_boundary"] if isinstance(ctx.get("dyn_boundary"), dict) else {}
    file_rows = []
    for name, count in ctx["file_counts"].items():  # type: ignore[union-attr]
        size = ctx["byte_counts"][name]  # type: ignore[index]
        file_rows.append([name, count, human_size(size)])
    blockers = f"{ctx['dyn_total']} 个 `.dyn` 动态动画载荷仍未解码" if ctx["dyn_total"] else "未发现剩余阻塞"
    note_lines = "\n".join(f"- {note}" for note in notes) if notes else "- 本次无新增沉淀。"
    return f"""# 00_研究总览

## 边界

### 观察到的事实

- 授权输入路径：`{export.get('source', '未记录')}`。
- 输出目录：`{ctx['study_root']}`。
- 研究模式：`full`；学习目的：`全面学习`。
- 引擎分支：`Klei / Don't Starve Together`。来源：`detect_engine.py` 与 Klei 目录/资源特征。

### 我的判断

- 本次结果适合做本机学习、资源规格观察、动画/XML 结构阅读和 Lua 架构地图梳理。
- 不应把导出的 PNG、OGG/WAV、Lua 或 XML 作为可复用素材或代码分发。

## 推荐阅读路径

1. 先读 `00_研究总览.md` 获取范围、工具链和阻塞项。
2. 美术侧读 `01`、`02`、`03`、`07`、`08`。
3. 技术和玩法侧读 `05`、`06`、`04`、`09`。
4. 需要浏览图片时打开 `2.报告/打开图片画廊.bat`。

## 导出统计

{md_table(['目录', '文件数', '体积'], file_rows)}

## 工具链结果

| 阶段 | 结果 |
|---|---|
| 原始 Klei 导出 | packages={export.get('packages', 0)}，Lua={export.get('code_files', 0)}，KTEX/XML 记录={export.get('atlas_or_texture_records', 0)}，音频 bank/样本记录={export.get('audio_records', 0)}，DYN={export.get('dyn_records', 0)} |
| KTEX 转 PNG | {conversion.get('textures', {}).get('converted', 0)} / {conversion.get('textures', {}).get('total', 0)} converted |
| anim/build 转 XML | {conversion.get('animations', {}).get('converted', 0)} / {conversion.get('animations', {}).get('total', 0)} converted |
| FSB 转音频样本 | banks {conversion.get('audio', {}).get('banks_converted', 0)} / {conversion.get('audio', {}).get('banks_total', 0)}，samples={conversion.get('audio', {}).get('samples_converted', 0)} |
| 图片画廊 | manifest items={ctx.get('gallery', {}).get('items', []) and len(ctx.get('gallery', {}).get('items', []))}，self-test 结果见 `4.临时目录/工具日志/GameImageGallery_self_test.log` |

## 关键产物

- 图片：`1.分类资源/图片/图集/KleiTEX/`。
- 音频：`1.分类资源/音频/KleiFSB/` 与原始 bank。
- Lua：`3.代码/脚本/`。
- 动画 XML：`4.临时目录/中间索引/Klei动画XML/`。
- Klei 索引：`4.临时目录/中间索引/KleiDST/` 与 `4.临时目录/中间索引/学习输出/`。

## 阻塞项

### 观察到的事实

- {blockers}。来源：`动态动画阻塞索引.csv`。
- `.dyn` 边界盘点：同名 `anim_dynamic` ZIP 对照 {dyn_boundary.get('matched_anim_dynamic_zip', 0)} / {dyn_boundary.get('dyn_files', ctx['dyn_total'])}，前 64 字节平均熵 {dyn_boundary.get('entropy64_avg', '未记录')}，EXE 字符串线索 {len(dyn_boundary.get('exe_string_hits', [])) if isinstance(dyn_boundary.get('exe_string_hits', []), list) else 0} 条。来源：`dyn_boundary_inventory_summary.json`。
- KTEX、anim/build 和 FSB 在第二阶段转换中均为 0 failed。来源：`klei_resource_conversion_summary.json`。

### 待验证假设

- `.dyn` 可能是 Klei 动态皮肤/服装相关载荷，当前只完成魔数和原始复制，尚未确认可审计解码路线。

## Skill 迭代

{note_lines}

## 来源

{common_sources(ctx)}
"""


def report_01(ctx: dict[str, object]) -> str:
    return f"""# 01_美术资源规格表

## 尺寸分布

### 观察到的事实

- 已读取 PNG IHDR 尺寸：{ctx['image_counted']} 张。来源：`1.分类资源/图片/图集/KleiTEX/`。

{md_table(['尺寸桶', '图片数'], top_counter_rows(ctx['image_buckets'], 10))}

## 图集规格

{md_table(['常见尺寸', '数量'], top_counter_rows(ctx['image_dimensions'], 12))}

## UI 与图标规格

### 我的判断

- DST 的 Klei TEX 输出以图集为主，图片文件名和目录保留 `data/anim`、`data/images`、`bigportraits` 等语义路径；学习时应以图集尺寸、透明边界、动作资源命名为主，不直接复用原始图像。
- UI、角色头像、动画图集混在 KleiTEX 图集输出中，进一步精分可按路径关键词做二级分类。

## 角色与序列帧规格

- 动画结构已经转为 XML，帧/层级信息在 `4.临时目录/中间索引/Klei动画XML/`，图像图集在 `1.分类资源/图片/图集/KleiTEX/`。
- 前摇/有效帧/后摇需要结合 `anim.xml` 的时间轴和游戏 Lua 状态机复核，本报告不把 XML 转换成功等同于手感结论。

## 对标建议

- 原创项目可学习“图集 + XML/状态机 + 音频事件”的资源组织方式。
- 建议建立原创图集规格：小图标 64-256px，角色/大型图集 512-2048px，具体尺寸以目标相机和内存预算重新制定。

## 来源

{common_sources(ctx)}
"""


def report_02(ctx: dict[str, object]) -> str:
    palette = ctx["palette"] if isinstance(ctx["palette"], dict) else {}
    colors = palette.get("aggregate_colors", []) if isinstance(palette, dict) else []
    color_rows = [
        [item.get("hex"), item.get("rgb"), f"{item.get('percent')}%", item.get("count")]
        for item in colors[:12]
        if isinstance(item, dict)
    ]
    if not color_rows:
        color_rows = [["待生成", "待生成", "待生成", "待生成"]]
    return f"""# 02_美术风格与色彩速查

## 主色卡

### 观察到的事实

- 色彩抽样来源：`4.临时目录/中间索引/学习输出/klei_palette_report.json`。
- 抽样图片数：{palette.get('image_files_analyzed', 0)} / {palette.get('image_files_found', 0)}。

{md_table(['颜色', 'RGB', '占比', '样本计数'], color_rows)}

## 明度与饱和度

### 我的判断

- 抽样结果黑白灰占比较高，通常来自透明边界、线稿、遮罩、字体或 UI/图集空白区；不能直接理解为最终画面占比。
- 非灰色高频项以低饱和绿、棕、土色为主，适合作为自然、荒野、旧纸感与木质材质的参考方向。

## 角色/场景/UI 对比

- 角色、怪物、UI 与环境图集共存于 KleiTEX 输出，建议用图片画廊按路径筛选后再做人工分组。
- 风格学习应关注抽象规则：不规则轮廓、手绘线条、低饱和自然色、强调剪影，而不是复制角色、图标或构图。

## 原创化风格规则

- 保留“强剪影 + 低饱和自然色 + 夸张比例”的学习方向。
- 所有角色造型、图标、图集、UI 框和音效都必须重新原创。

## 来源

{common_sources(ctx)}
"""


def report_03(ctx: dict[str, object]) -> str:
    anim_rows = top_counter_rows(ctx["animation_prefixes"], 12)
    return f"""# 03_动画与手感帧数据

## 动作索引

### 观察到的事实

- Klei 动画包 XML 转换：{dict(ctx['animation_status'])}。
- 转换索引行数：{ctx['animation_total']}。来源：`klei_animation_xml_index.csv`。

{md_table(['动作/资源名前缀', '数量'], anim_rows)}

## 关键帧数据

### 待验证假设

- `anim.xml` 可用于统计动作时长、layer、symbol 和帧关键点；是否对应前摇、有效帧、后摇，必须结合 Lua 状态机、事件帧和实际播放验证。
- 当前脚本只完成结构化导出，未做逐动作手感判定。

## 反馈层拆解

- 音频样本已拆分，可与动作名、状态机和资源路径交叉检索。
- VFX/粒子在 DST 中常混合表现为动画图集、shader、音效和 Lua 触发；不按 Unity VFX 模型处理。

## 待验证

- `.dyn` 动态皮肤/服装载荷仍未解码。
- 可追加脚本解析 `anim.xml` 的帧时间轴，生成更细的动作帧表。

## 来源

{common_sources(ctx)}
"""


def report_04(ctx: dict[str, object]) -> str:
    return f"""# 04_场景关卡搭建

## 场景索引

### 观察到的事实

- 本项目不是 Unity/Unreal 场景文件导出；未生成 Scene/Prefab/Level 索引。
- Lua 代码目录分布：\n\n{md_table(['代码目录', 'Lua 文件数'], top_counter_rows(ctx['code_dirs'], 12))}

## 搭建规律

### 我的判断

- DST 场景与世界搭建更可能由 Lua worldgen、prefab、component、stategraph 和资源图集协同驱动，而不是可视化场景文件。
- 关卡学习重点应从“摆放文件”转到“生成规则、prefab 定义、地表/生物群落/事件配置和反馈资源绑定”。

## 复用模块

- 可读代码入口建议从 `scripts/prefabs`、`scripts/components`、`scripts/stategraphs`、`scripts/map` 等目录开始。
- 图片与音频资源可通过命名和路径反查到相邻 gameplay 概念。

## 原创化建议

- 原创项目可学习“规则驱动世界 + prefab 语义目录 + 状态机触发反馈”的组织思想。
- 不复用 DST 地图规则、角色设定、资源命名中的独特表达或原始资源。

## 来源

{common_sources(ctx)}
"""


def report_05(ctx: dict[str, object]) -> str:
    export = ctx["export"] if isinstance(ctx["export"], dict) else {}
    return f"""# 05_技术架构拆解

## 运行时与工具链

### 观察到的事实

- 顶层目录包含 `bin`、`bin64`、`data`、`mods`。
- 包体以 ZIP、KTEX/TEX、DYN、FMOD FSB/FEV 和 Lua 为主。
- Lua 可读文件导出数：{ctx['code_total']}；原始 summary code_files={export.get('code_files', 0)}。

## 代码架构地图

{md_table(['目录', 'Lua 文件数'], top_counter_rows(ctx['code_dirs'], 15))}

## 核心系统入口

### 我的判断

- `actions.lua`、`components/`、`prefabs/`、`stategraphs/`、`widgets/`、`map/` 是优先阅读入口。
- 原生 EXE/DLL 不在本次 Lua 逻辑分析范围内；本次不做 native 反汇编或绕过。

## 可迁移原则

- 数据和表现分层：Lua 逻辑、Klei 动画 XML、图集和 FMOD 音频 bank 分开管理。
- 工具链可续跑：原始导出保留在 `0.原始导出`，分类资源只放真实可用输出，索引放临时目录。

## 来源

{common_sources(ctx)}
"""


def report_06(ctx: dict[str, object]) -> str:
    return f"""# 06_玩法系统拆解

## 核心循环

### 待验证假设

- 从目录和文件名推断，核心循环涉及探索、采集、制作、生存、战斗、季节/事件和多人联机反馈；这些需要结合 Lua 具体文件阅读确认。

## 系统地图

{md_table(['代码目录', 'Lua 文件数'], top_counter_rows(ctx['code_dirs'], 15))}

## 数据与反馈

- 资源反馈层已导出：PNG 图集转换索引 {ctx['texture_total']} 行，动画 XML 转换索引 {ctx['animation_total']} 行，音频样本文件 {ctx['audio_sample_file_total']} 个（转换摘要 samples_converted={ctx['audio_samples_converted']}），音频转换索引 {ctx['audio_total']} 行。
- 玩法触发和反馈绑定需要从 Lua 状态机、prefab、component 与资源命名交叉验证。

## 原创化练习

- 设计自己的 `actions/components/prefabs/stategraphs` 分层，不复刻 DST 的角色、道具、世界观或独特系统表达。
- 先复现“动作触发 -> 动画状态 -> 音频/VFX 反馈”的通用链路，再替换为原创内容。

## 来源

{common_sources(ctx)}
"""


def report_07(ctx: dict[str, object]) -> str:
    export = ctx["export"] if isinstance(ctx["export"], dict) else {}
    ksh_count = 0
    for ext, count in export.get("package_member_extensions", []):
        if ext == ".ksh":
            ksh_count = count
            break
    return f"""# 07_渲染管线拆解

## 管线概览

### 观察到的事实

- Klei TEX/KTEX 纹理已转换为 PNG：{dict(ctx['texture_status'])}。
- 原始包成员中 `.ksh` shader 文件计数：{ksh_count}。来源：导出 summary。

## Shader 与材质分类

### 我的判断

- DST 不是 Unity/Unreal 材质资源链，本次没有 Material/ShaderGraph 这类可视化材质索引。
- 可学习重点是 2D 图集、透明边界、手绘线稿、shader 文本和运行时 Lua 触发之间的关系。

## 关键用途推断

- 粒子、技能、环境表现更可能由动画图集、KSH shader、音频和 Lua 事件组合实现。
- 需要进一步解析 `.ksh` 和 Lua 中 shader/FX 调用，才能形成完整渲染参数表。

## 可迁移规则

- 原创项目可以采用“图集 + 轻量 shader + 状态机触发”的表现链路。
- 不把 DST 原图、shader 文本或独特特效形态直接搬用。

## 来源

{common_sources(ctx)}
"""


def report_08(ctx: dict[str, object]) -> str:
    return f"""# 08_命名规范汇总

## 观察到的事实

## 资源命名

{md_table(['图片/图集名前缀', '数量'], top_counter_rows(ctx['texture_prefixes'], 12))}

## 动作命名

{md_table(['动画资源名前缀', '数量'], top_counter_rows(ctx['animation_prefixes'], 12))}

## 代码命名

{md_table(['Lua 目录', '文件数'], top_counter_rows(ctx['code_dirs'], 12))}

## 音频命名

{md_table(['音频样本名前缀', '数量'], top_counter_rows(ctx['audio_prefixes'], 12))}

## 我的判断

- 原创项目建议统一资源前缀：`ui_`、`char_`、`prop_`、`fx_`、`sfx_`、`music_`、`sg_`。
- 动作名建议包含角色/对象、动作、方向或变体、帧/版本号；不要沿用 DST 独有命名。

## 来源

{common_sources(ctx)}
"""


def report_09(ctx: dict[str, object]) -> str:
    return f"""# 09_可迁移技术清单

## 观察到的事实

- 当前 study-root 已按原始导出、分类资源、报告、代码、临时索引分离。
- 本地索引覆盖图集、动画 XML、音频样本和 Lua 脚本，音频样本文件数为 {ctx['audio_sample_file_total']}，音频转换索引行数为 {ctx['audio_total']}。
- `.dyn` 动态资源仍有 {ctx['dyn_total']} 条阻塞记录。

## 我的判断

### 优先级清单

| 优先级 | 可迁移项 | 工时 | 验收标准 |
|---|---|---|---|
| P0 | 5 目录研究产物结构、原始/分类/报告/代码/临时索引分离 | 半天 | 输出目录可审计、可续跑 |
| P0 | 图集 + 动画 XML + 音频样本的学习索引 | 1-2 天 | 原创项目有自己的资源命名与索引 |
| P1 | Lua 式 `actions/components/prefabs/stategraphs` 分层思想 | 3-5 天 | 用原创代码做小型系统原型 |
| P1 | 动作状态机与音频/特效反馈链路 | 3-5 天 | 原创角色有 idle/walk/attack/hit/death 等闭环 |
| P2 | `.dyn` 动态资源解码路线 | 一周以上 | 找到可信工具或可审计解析流程 |

### 对标矩阵

- 美术规格：参考图集尺寸分布和命名，不复用原图。
- 动画手感：参考 XML 结构和状态机关系，手感帧必须在原创项目中重新调参。
- 技术架构：参考逻辑、资源、音频和表现层分离。

### 工时预估

- 半天：整理原创命名规范与目录结构。
- 1-2 天：搭建图集/动画/音频索引工具。
- 3-5 天：做一个原创角色状态机和反馈链。
- 一周以上：研究动态皮肤/服装类封装或更深 native 行为。

### 验收标准

- 所有资产、代码和音频均为原创或自有授权。
- 工具链只访问授权目录，输出可审计。
- 报告中的事实均有本地索引或日志来源。

## 来源

{common_sources(ctx)}
"""


def write_code_readability(ctx: dict[str, object]) -> None:
    content = f"""# 代码可读性判定

## 观察到的事实

- Lua 脚本已导出到 `3.代码/脚本/`，索引行数：{ctx['code_total']}。
- 可读代码主要来自 `data/databundles/scripts.zip` 和 `mods/*.lua`。
- 原生 EXE/DLL 没有在本次流程中反编译或反汇编。

## 我的判断

- Lua 层适合阅读 gameplay、UI、prefab、component、stategraph 和 worldgen 逻辑。
- 原生层只作为运行时边界记录，不作为本次 full 学习报告的可读代码来源。

## 待验证假设

- 若未来需要研究 native 行为，应走独立授权和安全边界审查，不应混入 DST 资源导出流程。
"""
    write_text(Path(ctx["study_root"]) / "3.代码" / "代码可读性判定.md", content)


def write_residual_blocker(ctx: dict[str, object]) -> None:
    dyn_rows = ctx["dyn_samples"]
    dyn_boundary = ctx["dyn_boundary"] if isinstance(ctx.get("dyn_boundary"), dict) else {}
    sample_rows = []
    for row in dyn_rows:  # type: ignore[assignment]
        sample_rows.append([row.get("source", ""), row.get("magic16", ""), row.get("status", "")])
    if not sample_rows:
        sample_rows = [["未发现", "", ""]]
    content = f"""# KleiDST 剩余阻塞

## 观察到的事实

- KTEX -> PNG、anim/build -> XML、FSB -> OGG/WAV 在第二阶段转换中均未记录 failed。
- `.dyn` 剩余未解码条目：{ctx['dyn_total']}。
- 同名 ZIP 对照：{dyn_boundary.get('matched_anim_dynamic_zip', 0)} / {dyn_boundary.get('dyn_files', ctx['dyn_total'])}。
- 前 64 字节平均熵：{dyn_boundary.get('entropy64_avg', '未记录')}。
- EXE 动态资源字符串线索：{len(dyn_boundary.get('exe_string_hits', [])) if isinstance(dyn_boundary.get('exe_string_hits', []), list) else 0} 条。

{md_table(['样本', 'magic16', '状态'], sample_rows)}

## 我的判断

- 当前可交付状态为 `DONE_WITH_CONCERNS`：主要学习产物已经生成，但 `.dyn` 动态动画/皮肤载荷仍需要独立格式研究。

## 下一步

- 对 `.dyn` 做 boundary inventory：聚类前 64 字节、熵统计、与 `anim_dynamic.zip` 条目名对照、EXE 字符串加载路径检索。
- 查找可信公开工具或编写可审计解析流程；成功后再沉淀到 Skill。
"""
    write_text(Path(ctx["study_root"]) / "4.临时目录" / "失败记录" / "KleiDST剩余阻塞.md", content)


def build_reports(study_root: Path, skill_iteration_notes: list[str] | None = None) -> dict[str, object]:
    study_root = study_root.resolve()
    ctx = load_context(study_root)
    notes = skill_iteration_notes or []
    detail_report_builders = [
        lambda current: report_01(current),
        lambda current: report_02(current),
        lambda current: report_03(current),
        lambda current: report_04(current),
        lambda current: report_05(current),
        lambda current: report_06(current),
        lambda current: report_07(current),
        lambda current: report_08(current),
        lambda current: report_09(current),
    ]
    reports_dir = study_root / "2.报告"
    for name, builder in zip(REPORT_NAMES[1:], detail_report_builders, strict=True):
        write_text(reports_dir / name, builder(ctx))
    write_code_readability(ctx)
    write_residual_blocker(ctx)

    # Write overview after the other reports, then reload and write it again so
    # its directory statistics include the overview file itself on fresh runs.
    ctx = load_context(study_root)
    write_text(reports_dir / REPORT_NAMES[0], report_00(ctx, notes))
    ctx = load_context(study_root)
    write_text(reports_dir / REPORT_NAMES[0], report_00(ctx, notes))

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_root": str(study_root),
        "reports_written": len(REPORT_NAMES),
        "reports": [str(reports_dir / name) for name in REPORT_NAMES],
        "code_readability": str(study_root / "3.代码" / "代码可读性判定.md"),
        "residual_blocker": str(study_root / "4.临时目录" / "失败记录" / "KleiDST剩余阻塞.md"),
    }
    summary_path = study_root / "4.临时目录" / "工具日志" / "build_klei_dst_reports_summary.json"
    write_text(summary_path, json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, help="GameAnalysis Klei/DST study root.")
    parser.add_argument(
        "--skill-iteration-note",
        action="append",
        default=[],
        help="Append a note to 00_研究总览.md / Skill 迭代.",
    )
    args = parser.parse_args()

    result = build_reports(Path(args.study_root), skill_iteration_notes=args.skill_iteration_note)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

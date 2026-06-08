from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_NAME = "localRemoteRes"
BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
ASTC_MAGIC = bytes.fromhex("13ABA15C")


ROLE_PREFIXES = (
    ("spine/role/", "Spine角色动作"),
    ("spine/role_lunjf/", "Spine轮回角色"),
    ("spine/cuzhi/", "Spine粗制角色"),
    ("friendsImg/", "好友人物图"),
    ("monsterIcon/", "怪物角色图标"),
    ("cuzhi/", "粗制角色图"),
    ("roleImgRank/", "角色排行立绘"),
    ("gdRole/", "滚动/挂机角色"),
    ("tongRen/", "同人角色"),
)

SCENE_PREFIXES = (
    ("spine/fightScene/", "Spine战斗场景"),
    ("bg/", "背景图"),
    ("realm/", "境界/地图场景"),
    ("schoolBg/", "门派背景"),
    ("chapterIcon/", "章节场景图"),
    ("bgIcon/", "背景图标"),
    ("qiyu/", "奇遇场景图"),
    ("diGongIcon/", "地宫场景图"),
    ("acitivity/activityBg/", "活动背景"),
    ("acitivity/gaoefanliBg/", "活动背景"),
)


@dataclass(frozen=True)
class AstcHeader:
    block_x: int
    block_y: int
    block_z: int
    width: int
    height: int
    depth: int


@dataclass
class AssetRow:
    key: int
    bundle: str
    asset_path: str
    category: str
    subcategory: str
    type_name: str
    compact_uuid: str
    uuid: str
    native_path: Path
    native_exists: bool
    native_size: int = 0
    block_x: int = 0
    block_y: int = 0
    width: int = 0
    height: int = 0
    astc_copy_path: Path | None = None
    decoded_path: Path | None = None
    role_candidate_path: Path | None = None
    decode_status: str = "pending"
    decode_error: str = ""


def decode_cocos_uuid(value: str) -> str:
    """Expand Cocos Creator compressed UUIDs to canonical dashed UUIDs."""
    token = str(value).split("@", 1)[0].strip()
    token = token.replace("-", "").lower() if len(token) == 36 else token
    if len(token) == 32 and re.fullmatch(r"[0-9a-fA-F]{32}", token):
        hex_value = token.lower()
    elif len(token) == 22:
        hex_value = token[:2].lower()
        for offset in range(2, 22, 2):
            left = BASE64_ALPHABET.index(token[offset])
            right = BASE64_ALPHABET.index(token[offset + 1])
            hex_value += f"{left * 64 + right:03x}"
    else:
        raise ValueError(f"Unsupported Cocos UUID format: {value!r}")
    return f"{hex_value[:8]}-{hex_value[8:12]}-{hex_value[12:16]}-{hex_value[16:20]}-{hex_value[20:32]}"


def read_astc_header(path: Path | str) -> AstcHeader:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(16)
    if len(header) < 16 or header[:4] != ASTC_MAGIC:
        raise ValueError(f"Not an ASTC file: {path}")
    width = header[7] | (header[8] << 8) | (header[9] << 16)
    height = header[10] | (header[11] << 8) | (header[12] << 16)
    depth = header[13] | (header[14] << 8) | (header[15] << 16)
    return AstcHeader(header[4], header[5], header[6], width, height, depth)


def classify_asset(asset_path: str) -> tuple[str, str] | None:
    for prefix, subcategory in ROLE_PREFIXES:
        if asset_path.startswith(prefix):
            return "角色资源", subcategory
    for prefix, subcategory in SCENE_PREFIXES:
        if asset_path.startswith(prefix):
            return "场景资源", subcategory
    return None


def local_remote_config_path(source_root: Path | str) -> Path:
    return Path(source_root) / "assets" / "assets" / BUNDLE_NAME / "cc.config.json"


def native_path_for_uuid(source_root: Path | str, uuid: str) -> Path:
    native_dir = Path(source_root) / "assets" / "assets" / BUNDLE_NAME / "native" / uuid[:2]
    matches = sorted(native_dir.glob(f"{uuid}.*"))
    if matches:
        return matches[0]
    return native_dir / f"{uuid}.astc"


def collect_role_scene_assets(source_root: Path | str) -> list[AssetRow]:
    source_root = Path(source_root)
    with local_remote_config_path(source_root).open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    rows: list[AssetRow] = []
    types = config["types"]
    uuids = config["uuids"]
    for key_text, value in config["paths"].items():
        if not isinstance(value, list) or len(value) < 2:
            continue
        asset_path = value[0]
        type_name = types[int(value[1])]
        if type_name != "cc.ImageAsset":
            continue
        classified = classify_asset(asset_path)
        if not classified:
            continue
        key = int(key_text)
        compact_uuid = uuids[key]
        uuid = decode_cocos_uuid(compact_uuid)
        native_path = native_path_for_uuid(source_root, uuid)
        exists = native_path.exists()
        rows.append(
            AssetRow(
                key=key,
                bundle=BUNDLE_NAME,
                asset_path=asset_path,
                category=classified[0],
                subcategory=classified[1],
                type_name=type_name,
                compact_uuid=compact_uuid,
                uuid=uuid,
                native_path=native_path,
                native_exists=exists,
                native_size=native_path.stat().st_size if exists else 0,
            )
        )
    return sorted(rows, key=lambda row: (row.category, row.subcategory, row.asset_path))


def safe_filename(asset_path: str, uuid: str, suffix: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z._-]+", "__", asset_path.strip("/"))
    stem = stem.strip("._-") or uuid
    if len(stem) > 130:
        stem = stem[:130].rstrip("._-")
    return f"{stem}__{uuid[:8]}{suffix}"


def ensure_clean_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def prepare_output_paths(row: AssetRow, study_root: Path, force: bool = False, copy_original: bool = True) -> None:
    export_dir = study_root / "1.分类资源" / "图片" / row.category
    astc_dir = export_dir / "ASTC原始纹理" / row.subcategory
    png_dir = export_dir / "可预览_ASTC解码" / row.subcategory
    ensure_clean_dir(astc_dir)
    ensure_clean_dir(png_dir)
    row.astc_copy_path = astc_dir / safe_filename(row.asset_path, row.uuid, row.native_path.suffix or ".astc")
    row.decoded_path = png_dir / safe_filename(row.asset_path, row.uuid, ".png")
    if row.native_exists:
        header = read_astc_header(row.native_path)
        row.block_x = header.block_x
        row.block_y = header.block_y
        row.width = header.width
        row.height = header.height
        if copy_original and (force or not row.astc_copy_path.exists() or row.astc_copy_path.stat().st_size != row.native_size):
            shutil.copy2(row.native_path, row.astc_copy_path)


def find_astcenc(study_root: Path | None = None) -> Path | None:
    tool_roots = [
        SKILL_ROOT / "tools" / "astcenc_5.4.0_windows_x64",
        SKILL_ROOT / "tools",
    ]
    if study_root is not None:
        tool_roots.append(study_root / "4.临时目录" / "tools" / "astcenc")
    preferred = (
        "astcenc-sse4.1.exe",
        "astcenc-sse2.exe",
        "astcenc-avx2.exe",
    )
    for tool_root in tool_roots:
        if not tool_root.exists():
            continue
        for name in preferred:
            matches = sorted(tool_root.glob(f"**/{name}"))
            if matches:
                return matches[-1]
        matches = sorted(tool_root.glob("**/astcenc*.exe"))
        if matches:
            return matches[-1]
    return None


def decode_one(row: AssetRow, astcenc: Path, force: bool = False, timeout: int = 120) -> AssetRow:
    if not row.native_exists:
        row.decode_status = "missing_native"
        return row
    if row.native_path.suffix.lower() != ".astc":
        row.decode_status = f"skip_{row.native_path.suffix.lower().lstrip('.')}"
        return row
    if not row.decoded_path:
        row.decode_status = "missing_output_path"
        return row
    if row.decoded_path.exists() and row.decoded_path.stat().st_size > 0 and not force:
        row.decode_status = "cached"
        return row
    try:
        result = subprocess.run(
            [str(astcenc), "-dl", str(row.native_path), str(row.decoded_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive external tool wrapper
        row.decode_status = "failed"
        row.decode_error = str(exc)
        return row
    if result.returncode == 0 and row.decoded_path.exists() and row.decoded_path.stat().st_size > 0:
        row.decode_status = "decoded"
    else:
        row.decode_status = "failed"
        row.decode_error = (result.stderr or result.stdout).strip()[-800:]
    return row


def decode_rows(rows: list[AssetRow], astcenc: Path | None, force: bool, max_decode: int, workers: int) -> None:
    if not astcenc:
        for row in rows:
            row.decode_status = "missing_decoder"
        return
    candidates = [row for row in rows if row.native_exists and row.native_path.suffix.lower() == ".astc"]
    if max_decode > 0:
        candidates = candidates[:max_decode]
        limited = {id(row) for row in candidates}
        for row in rows:
            if id(row) not in limited:
                row.decode_status = "not_requested"
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(decode_one, row, astcenc, force) for row in candidates]
        for future in as_completed(futures):
            future.result()


def is_role_candidate(row: AssetRow) -> bool:
    path = row.asset_path
    return (
        path.startswith("spine/role/")
        or path.startswith("spine/role_lunjf/")
        or path.startswith("gdRole/")
        or path.startswith("roleImgRank/")
        or (path.startswith("friendsImg/") and ("_body" in path or "_head" in path))
        or path.startswith("tongRen/")
    )


def copy_role_candidates(rows: Iterable[AssetRow], study_root: Path, force: bool) -> None:
    target_root = study_root / "1.分类资源" / "图片" / "主角候选资源" / "可预览_ASTC解码"
    ensure_clean_dir(target_root)
    for row in rows:
        if not is_role_candidate(row):
            continue
        if row.decoded_path and row.decoded_path.exists() and row.decode_status in {"decoded", "cached"}:
            target = target_root / safe_filename(row.asset_path, row.uuid, ".png")
            if force or not target.exists() or target.stat().st_size != row.decoded_path.stat().st_size:
                shutil.copy2(row.decoded_path, target)
            row.role_candidate_path = target


def relative_for_html(path: Path, base: Path) -> str:
    rel = os.path.relpath(path, base)
    return rel.replace("\\", "/")


def rows_to_csv_dict(row: AssetRow, study_root: Path, source_root: Path) -> dict[str, object]:
    data = asdict(row)
    for key in ("native_path", "astc_copy_path", "decoded_path", "role_candidate_path"):
        value = data[key]
        data[key] = "" if value is None else str(value)
    data["source_relative"] = str(row.native_path.relative_to(source_root)) if row.native_path.is_absolute() and source_root in row.native_path.parents else str(row.native_path)
    data["study_relative"] = str(row.decoded_path.relative_to(study_root)) if row.decoded_path and row.decoded_path.exists() else ""
    return data


def write_indexes(rows: list[AssetRow], study_root: Path, source_root: Path) -> None:
    index_dir = study_root / "4.临时目录" / "中间索引" / "cocos_astc"
    ensure_clean_dir(index_dir)
    csv_path = index_dir / "角色场景资源映射.csv"
    json_path = index_dir / "角色场景资源映射.json"
    fieldnames = list(rows_to_csv_dict(rows[0], study_root, source_root).keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(rows_to_csv_dict(row, study_root, source_root))
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump([rows_to_csv_dict(row, study_root, source_root) for row in rows], handle, ensure_ascii=False, indent=2)


def gallery_items(rows: Iterable[AssetRow], gallery_dir: Path) -> list[dict[str, object]]:
    items = []
    for row in rows:
        if not row.decoded_path or not row.decoded_path.exists():
            continue
        if row.decode_status not in {"decoded", "cached"}:
            continue
        items.append(
            {
                "src": relative_for_html(row.decoded_path, gallery_dir),
                "assetPath": row.asset_path,
                "category": row.category,
                "subcategory": row.subcategory,
                "uuid": row.uuid,
                "width": row.width,
                "height": row.height,
                "bytes": row.decoded_path.stat().st_size,
                "nativeBytes": row.native_size,
            }
        )
    return items


def write_gallery(rows: list[AssetRow], gallery_dir: Path, title: str, description: str) -> None:
    ensure_clean_dir(gallery_dir)
    items = gallery_items(rows, gallery_dir)
    manifest_path = gallery_dir / "gallery_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump({"title": title, "items": items}, handle, ensure_ascii=False, indent=2)
    categories = Counter(item["category"] for item in items)
    subcategories = sorted({str(item["subcategory"]) for item in items})
    items_json = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #f7f5ef;
      --ink: #202124;
      --muted: #67645f;
      --line: #d8d2c5;
      --tile: #ffffff;
      --accent: #0f766e;
      --accent-2: #9a3412;
      --shadow: 0 10px 30px rgba(22, 20, 16, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--paper);
      color: var(--ink);
      letter-spacing: 0;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(247, 245, 239, 0.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(12px);
    }}
    .bar {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 18px 20px 14px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(22px, 3vw, 36px);
      line-height: 1.12;
      font-weight: 750;
    }}
    .desc {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }}
    .stat {{
      border: 1px solid var(--line);
      background: #fffaf0;
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 13px;
      color: var(--muted);
    }}
    .stat strong {{ color: var(--ink); }}
    .controls {{
      max-width: 1480px;
      margin: 16px auto;
      padding: 0 20px;
      display: grid;
      grid-template-columns: minmax(220px, 1.5fr) minmax(160px, 0.7fr) minmax(150px, 0.55fr);
      gap: 10px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 8px;
      min-height: 40px;
      padding: 0 12px;
      font-size: 14px;
    }}
    .tabs {{
      max-width: 1480px;
      margin: 0 auto 16px;
      padding: 0 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 8px;
      min-height: 38px;
      padding: 0 12px;
      font-size: 14px;
      cursor: pointer;
    }}
    button.active {{
      border-color: var(--accent);
      background: #e7f5f2;
      color: #0b4c47;
      font-weight: 700;
    }}
    main {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 0 20px 42px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 12px;
      align-items: start;
    }}
    figure {{
      margin: 0;
      background: var(--tile);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .thumb {{
      aspect-ratio: 1 / 1;
      display: grid;
      place-items: center;
      background-color: #eee8dc;
      background-image:
        linear-gradient(45deg, rgba(0,0,0,.045) 25%, transparent 25%),
        linear-gradient(-45deg, rgba(0,0,0,.045) 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, rgba(0,0,0,.045) 75%),
        linear-gradient(-45deg, transparent 75%, rgba(0,0,0,.045) 75%);
      background-size: 18px 18px;
      background-position: 0 0, 0 9px, 9px -9px, -9px 0;
    }}
    img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      image-rendering: auto;
    }}
    figcaption {{
      padding: 10px;
      display: grid;
      gap: 5px;
      min-height: 96px;
    }}
    .name {{
      font-size: 13px;
      font-weight: 700;
      overflow-wrap: anywhere;
      line-height: 1.35;
    }}
    .meta {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}
    .empty {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 28px;
      color: var(--muted);
      background: #fff;
    }}
    @media (max-width: 720px) {{
      .controls {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }}
      .bar {{ padding-inline: 14px; }}
      main, .controls, .tabs {{ padding-inline: 14px; }}
    }}
    @media print {{
      header {{ position: static; }}
      .controls, .tabs {{ display: none; }}
      figure {{ box-shadow: none; break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <h1>{title}</h1>
      <p class="desc">{description}</p>
      <div class="stats">
        <span class="stat"><strong id="visibleCount">0</strong> / {len(items)} 张可预览 PNG</span>
        <span class="stat">角色资源 <strong>{categories.get("角色资源", 0)}</strong></span>
        <span class="stat">场景资源 <strong>{categories.get("场景资源", 0)}</strong></span>
      </div>
    </div>
  </header>
  <section class="controls" aria-label="筛选">
    <input id="search" type="search" placeholder="搜索路径 / UUID / 子类">
    <select id="subcategory">
      <option value="">全部子类</option>
      {''.join(f'<option value="{name}">{name}</option>' for name in subcategories)}
    </select>
    <select id="sort">
      <option value="path">按路径</option>
      <option value="area">按尺寸</option>
      <option value="bytes">按 PNG 大小</option>
    </select>
  </section>
  <nav class="tabs" aria-label="资源分类">
    <button type="button" class="active" data-category="">全部</button>
    <button type="button" data-category="角色资源">角色资源</button>
    <button type="button" data-category="场景资源">场景资源</button>
  </nav>
  <main>
    <div id="grid" class="grid"></div>
    <div id="empty" class="empty" hidden>没有匹配项。</div>
  </main>
  <script>
    const ITEMS = {items_json};
    const state = {{ category: "", query: "", subcategory: "", sort: "path" }};
    const grid = document.querySelector("#grid");
    const empty = document.querySelector("#empty");
    const visibleCount = document.querySelector("#visibleCount");

    function formatBytes(n) {{
      if (!n) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let value = n;
      let index = 0;
      while (value >= 1024 && index < units.length - 1) {{
        value /= 1024;
        index += 1;
      }}
      return `${{value.toFixed(value >= 10 || index === 0 ? 0 : 1)}} ${{units[index]}}`;
    }}

    function matches(item) {{
      if (state.category && item.category !== state.category) return false;
      if (state.subcategory && item.subcategory !== state.subcategory) return false;
      if (!state.query) return true;
      const hay = `${{item.assetPath}} ${{item.uuid}} ${{item.subcategory}}`.toLowerCase();
      return hay.includes(state.query);
    }}

    function sorted(items) {{
      const arr = [...items];
      if (state.sort === "area") {{
        arr.sort((a, b) => (b.width * b.height) - (a.width * a.height));
      }} else if (state.sort === "bytes") {{
        arr.sort((a, b) => b.bytes - a.bytes);
      }} else {{
        arr.sort((a, b) => a.assetPath.localeCompare(b.assetPath, "zh-Hans-CN"));
      }}
      return arr;
    }}

    function render() {{
      const selected = sorted(ITEMS.filter(matches));
      visibleCount.textContent = selected.length;
      empty.hidden = selected.length !== 0;
      grid.innerHTML = selected.map((item) => `
        <figure>
          <div class="thumb"><img loading="lazy" src="${{item.src}}" alt="${{item.assetPath}}"></div>
          <figcaption>
            <div class="name">${{item.assetPath}}</div>
            <div class="meta">${{item.category}} / ${{item.subcategory}}</div>
            <div class="meta">${{item.width}} x ${{item.height}} · ${{formatBytes(item.bytes)}}</div>
            <div class="meta">${{item.uuid}}</div>
          </figcaption>
        </figure>
      `).join("");
    }}

    document.querySelector("#search").addEventListener("input", (event) => {{
      state.query = event.target.value.trim().toLowerCase();
      render();
    }});
    document.querySelector("#subcategory").addEventListener("change", (event) => {{
      state.subcategory = event.target.value;
      render();
    }});
    document.querySelector("#sort").addEventListener("change", (event) => {{
      state.sort = event.target.value;
      render();
    }});
    document.querySelectorAll(".tabs button").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelectorAll(".tabs button").forEach((item) => item.classList.toggle("active", item === button));
        state.category = button.dataset.category || "";
        render();
      }});
    }});
    render();
  </script>
</body>
</html>
"""
    (gallery_dir / "index.html").write_text(html, encoding="utf-8")


def command_text(args: list[str]) -> str:
    return " ".join(f'"{arg}"' if " " in arg else arg for arg in args)


def write_tool_record(study_root: Path, astcenc: Path | None) -> None:
    record_dir = study_root / "4.临时目录" / "工具验证记录"
    ensure_clean_dir(record_dir)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not astcenc:
        content = f"""# ASTC 解码工具验证

- 记录时间：{now}
- 状态：未找到 `astcenc`，本次只能生成映射，不能解码 PNG。
"""
    else:
        version = subprocess.run(
            [str(astcenc), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        exe_hash = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"(Get-FileHash -LiteralPath '{str(astcenc)}' -Algorithm SHA256).Hash"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        content = f"""# ASTC 解码工具验证

- 记录时间：{now}
- 工具路径：`{astcenc}`
- 工具输出：`{(version.stdout or version.stderr).strip()}`
- EXE SHA256：`{exe_hash.stdout.strip()}`
- 来源：ARM 官方 astc-encoder GitHub Release `5.4.0`，Windows x64 包。
- 解码命令模板：`{command_text([str(astcenc), "-dl", "<input.astc>", "<output.png>"])}`
"""
    (record_dir / "astcenc_验证记录.md").write_text(content, encoding="utf-8")


def write_report(rows: list[AssetRow], study_root: Path, source_root: Path, astcenc: Path | None) -> None:
    final_dir = study_root / "2.报告"
    ensure_clean_dir(final_dir)
    total = len(rows)
    decoded = sum(1 for row in rows if row.decode_status in {"decoded", "cached"} and row.decoded_path and row.decoded_path.exists())
    failed = sum(1 for row in rows if row.decode_status == "failed")
    missing = sum(1 for row in rows if row.decode_status == "missing_native")
    counts = Counter(row.category for row in rows)
    subcounts = Counter((row.category, row.subcategory) for row in rows)
    lines = [
        "# 角色场景资源专项修复",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 源目录：`{source_root}`",
        f"- Study 目录：`{study_root}`",
        f"- ASTC 解码工具：`{astcenc}`" if astcenc else "- ASTC 解码工具：未找到",
        "",
        "## 结论",
        "",
        "旧版画廊不是没有角色/场景资源，而是只按扩展名把 `.json/.atlas` 分到了角色/场景目录；Cocos Creator 的真实大图纹理在 `assets/assets/localRemoteRes/native/<uuid前两位>/<uuid>.astc`，因此之前被留在“未分类_待复核”。",
        "",
        "本次按 `localRemoteRes/cc.config.json` 的 `paths` 键值关系重新建立了语义路径到 UUID 的映射：`paths` 的对象键是 UUID 索引，条目第三个数字不是纹理 UUID 索引。",
        "",
        "## 输出",
        "",
        "- 专项画廊：`2.报告/角色场景资源画廊/index.html`",
        "- 角色候选画廊：`2.报告/主角候选资源画廊/index.html`",
        "- 映射索引：`4.临时目录/中间索引/cocos_astc/角色场景资源映射.csv`",
        "- 角色 PNG：`1.分类资源/图片/角色资源/可预览_ASTC解码/`",
        "- 场景 PNG：`1.分类资源/图片/场景资源/可预览_ASTC解码/`",
        "",
        "## 数量",
        "",
        f"- 识别到角色/场景 ImageAsset：{total}",
        f"- 已可预览 PNG：{decoded}",
        f"- 解码失败：{failed}",
        f"- 原始纹理缺失：{missing}",
        f"- 角色资源：{counts.get('角色资源', 0)}",
        f"- 场景资源：{counts.get('场景资源', 0)}",
        "",
        "## 子类分布",
        "",
    ]
    for (category, subcategory), count in sorted(subcounts.items(), key=lambda item: (item[0][0], item[0][1])):
        lines.append(f"- {category} / {subcategory}：{count}")
    lines += [
        "",
        "## 仍然保留的边界",
        "",
        "- 本次把 ASTC 纹理解码成可查看 PNG，并没有重建 Spine 动画运行时；`.json/.atlas/.bin` 骨骼与动画数据仍按原始资源保留。",
        "- DEX、IL2CPP/native 逻辑反编译仍属于更深层执行逻辑分析，不影响本次角色/场景美术资源可视化。",
        "",
        "## 来源",
        "",
        f"- 用户授权目录：`{source_root}`",
        "- 本地配置：`assets/assets/localRemoteRes/cc.config.json`",
        "- ARM 官方 ASTC Encoder Release：`https://github.com/ARM-software/astc-encoder/releases/tag/5.4.0`",
    ]
    (final_dir / "角色场景资源专项修复.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_status_append(rows: list[AssetRow], study_root: Path) -> None:
    status_path = study_root / "2.报告" / "验证日志.md"
    decoded = sum(1 for row in rows if row.decode_status in {"decoded", "cached"} and row.decoded_path and row.decoded_path.exists())
    block = f"""

## 2026-05-14 角色/场景资源专项补强

- 已按 `localRemoteRes/cc.config.json` 修复 Cocos UUID 到 ASTC 原始纹理的映射。
- 已生成角色/场景 ASTC 解码 PNG：{decoded} 张。
- 新增专项入口：`2.报告/角色场景资源画廊/index.html`。
- 新增角色候选入口：`2.报告/主角候选资源画廊/index.html`。
- 说明：之前“只有奇怪图标”的原因是旧画廊只收集 PNG/JPG/WEBP，未把语义路径下的 `.astc` 大图解码进角色/场景画廊。
"""
    if status_path.exists():
        existing = status_path.read_text(encoding="utf-8")
        marker = "## 2026-05-14 角色/场景资源专项补强"
        if marker in existing:
            existing = existing.split(marker)[0].rstrip() + "\n"
            status_path.write_text(existing + block, encoding="utf-8")
        else:
            status_path.write_text(existing.rstrip() + block, encoding="utf-8")
    else:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text("# 验证日志\n" + block, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair role and scene ASTC resources for the full study output.")
    parser.add_argument("--source-root", required=True, help="授权的 Cocos Creator APK 解包目录或项目目录")
    parser.add_argument("--study-root", required=True, help="GameAnalysis study-root 输出目录")
    parser.add_argument("--astcenc", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--mapping-only", action="store_true")
    parser.add_argument("--max-decode", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(2, min(6, (os.cpu_count() or 4) // 2)))
    args = parser.parse_args()

    source_root = Path(args.source_root)
    study_root = Path(args.study_root)
    astcenc = Path(args.astcenc) if args.astcenc else find_astcenc(study_root)

    rows = collect_role_scene_assets(source_root)
    for row in rows:
        prepare_output_paths(row, study_root, force=args.force, copy_original=not args.mapping_only)

    if args.mapping_only:
        for row in rows:
            row.decode_status = "mapping_only"
    else:
        decode_rows(rows, astcenc, force=args.force, max_decode=args.max_decode, workers=args.workers)
        copy_role_candidates(rows, study_root, force=args.force)

    write_indexes(rows, study_root, source_root)
    write_gallery(
        rows,
        study_root / "2.报告" / "角色场景资源画廊",
        "角色场景资源画廊",
        "从 localRemoteRes 的 Cocos 语义路径映射到 ASTC 原始纹理后生成。",
    )
    write_gallery(
        [row for row in rows if is_role_candidate(row)],
        study_root / "2.报告" / "主角候选资源画廊",
        "主角候选资源画廊",
        "按角色展示、排行立绘、好友人物图和同人角色筛选出的候选图。",
    )
    write_tool_record(study_root, astcenc)
    write_report(rows, study_root, source_root, astcenc)
    write_status_append(rows, study_root)

    summary = {
        "rows": len(rows),
        "decoded": sum(1 for row in rows if row.decode_status in {"decoded", "cached"} and row.decoded_path and row.decoded_path.exists()),
        "failed": sum(1 for row in rows if row.decode_status == "failed"),
        "missing_native": sum(1 for row in rows if row.decode_status == "missing_native"),
        "astcenc": str(astcenc) if astcenc else "",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

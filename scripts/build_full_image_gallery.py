from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def thumb_name(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]
    stem = Path(relative_path).stem
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:80]
    return f"{safe}_{digest}.jpg"


def checkerboard(size: tuple[int, int], cell: int = 16) -> Image.Image:
    img = Image.new("RGB", size, "#1a1a1a")
    draw = ImageDraw.Draw(img)
    colors = ("#1f1f1f", "#2b2b2b")
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=colors[((x // cell) + (y // cell)) % 2])
    return img


def make_thumbnail(src: Path, dst: Path, max_size: int = 320) -> bool:
    if dst.exists() and dst.stat().st_size > 0:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = img.convert("RGBA")
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        canvas = checkerboard((max_size, max_size))
        x = (max_size - img.width) // 2
        y = (max_size - img.height) // 2
        canvas.paste(img, (x, y), img)
        canvas.save(dst, "JPEG", quality=82, optimize=True)
    return True


def read_image_index(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_gallery(index_path: Path, items: list[dict], title: str) -> None:
    data_json = json.dumps(items, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #151515;
      --panel: #202020;
      --panel-2: #292929;
      --line: #3d3d3d;
      --text: #f2f2f2;
      --muted: #b8b8b8;
      --accent: #d09152;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
    }}
    body.preview-open {{
      overflow: hidden;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(21, 21, 21, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 18px clamp(16px, 4vw, 42px);
      backdrop-filter: blur(10px);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) 160px 150px 140px 120px;
      gap: 10px;
      margin-top: 16px;
      align-items: center;
    }}
    input, select, button {{
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
      letter-spacing: 0;
    }}
    button {{
      cursor: pointer;
      background: #3a2b20;
      border-color: #765231;
    }}
    main {{
      padding: 24px clamp(16px, 4vw, 42px) 46px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 14px;
    }}
    .card {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }}
    .card.active {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(208, 145, 82, 0.26);
    }}
    .thumb {{
      aspect-ratio: 1 / 1;
      background: #111;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 0;
      cursor: zoom-in;
      min-height: 0;
      padding: 0;
      width: 100%;
    }}
    .thumb img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }}
    .info {{
      padding: 10px;
      display: grid;
      gap: 6px;
      font-size: 13px;
      line-height: 1.35;
    }}
    .name {{
      font-size: 14px;
      font-weight: 700;
      overflow-wrap: anywhere;
      color: #fff;
    }}
    .path {{
      color: var(--muted);
      overflow-wrap: anywhere;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }}
    .chip {{
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--muted);
      border-radius: 999px;
      padding: 3px 7px;
      font-size: 12px;
    }}
    .pager {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      margin: 22px 0 0;
    }}
    .pager button {{
      width: auto;
      min-width: 96px;
      padding-inline: 14px;
    }}
    .empty {{
      color: var(--muted);
      padding: 26px;
      border: 1px dashed var(--line);
      border-radius: 8px;
    }}
    a {{
      color: #f0c08a;
      text-decoration: none;
    }}
    .preview-overlay {{
      position: fixed;
      inset: 0;
      z-index: 40;
      background: rgba(7, 7, 7, 0.88);
      display: grid;
      grid-template-rows: auto 1fr;
      padding: 18px;
      backdrop-filter: blur(12px);
    }}
    .preview-overlay[hidden] {{
      display: none;
    }}
    .preview-bar {{
      min-width: 0;
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(32, 32, 32, 0.92);
    }}
    .preview-title {{
      min-width: 0;
      font-size: 15px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .preview-meta {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .preview-actions {{
      display: flex;
      gap: 8px;
    }}
    .preview-actions button {{
      width: auto;
      min-width: 42px;
      padding-inline: 12px;
    }}
    .preview-stage {{
      min-height: 0;
      display: grid;
      place-items: center;
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background-color: #1a1a1a;
      background-image:
        linear-gradient(45deg, #222 25%, transparent 25%),
        linear-gradient(-45deg, #222 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #222 75%),
        linear-gradient(-45deg, transparent 75%, #222 75%);
      background-position: 0 0, 0 16px, 16px -16px, -16px 0;
      background-size: 32px 32px;
    }}
    .preview-image {{
      max-width: min(100%, 96vw);
      max-height: calc(100vh - 128px);
      width: auto;
      height: auto;
      object-fit: contain;
      cursor: zoom-out;
      image-rendering: auto;
    }}
    @media (max-width: 850px) {{
      .toolbar {{
        grid-template-columns: 1fr 1fr;
      }}
      .preview-bar {{
        grid-template-columns: 1fr auto;
      }}
      .preview-actions {{
        grid-column: 1 / -1;
      }}
    }}
    @media (max-width: 560px) {{
      .toolbar {{
        grid-template-columns: 1fr;
      }}
      .grid {{
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="meta">
      <span>索引数量：<strong id="total"></strong></span>
      <span>当前结果：<strong id="filtered"></strong></span>
      <span>本地学习预览；缩略图为降采样 JPEG，原始 PNG 保留在导出工程内。</span>
    </div>
    <div class="toolbar">
      <input id="search" type="search" placeholder="搜索文件名、路径、类别" />
      <select id="category"></select>
      <select id="sort">
        <option value="area_desc">面积从大到小</option>
        <option value="area_asc">面积从小到大</option>
        <option value="path_asc">路径 A-Z</option>
        <option value="size_desc">文件体积从大到小</option>
      </select>
      <select id="pageSize">
        <option value="120">每页 120</option>
        <option value="240">每页 240</option>
        <option value="480">每页 480</option>
      </select>
      <button id="reset">重置</button>
    </div>
  </header>
  <main>
    <div id="grid" class="grid"></div>
    <div id="empty" class="empty" hidden>没有匹配的图片。</div>
    <div class="pager">
      <button id="prev">上一页</button>
      <span id="pageInfo"></span>
      <button id="next">下一页</button>
    </div>
  </main>
  <div id="previewOverlay" class="preview-overlay" hidden>
    <div class="preview-bar">
      <div>
        <div id="previewTitle" class="preview-title"></div>
        <div id="previewMeta" class="preview-meta"></div>
      </div>
      <div class="preview-actions">
        <button id="previewFolder" type="button" hidden>打开目录</button>
        <button type="button" data-preview-close>关闭</button>
      </div>
    </div>
    <div class="preview-stage">
      <img id="previewImage" class="preview-image" alt="">
    </div>
  </div>
  <script>
    const DATA = {data_json};
    const state = {{ query: "", category: "all", sort: "area_desc", page: 1, pageSize: 120, previewIndex: null }};
    const $ = (id) => document.getElementById(id);
    const grid = $("grid");
    const overlay = $("previewOverlay");
    const previewImage = $("previewImage");
    const previewTitle = $("previewTitle");
    const previewMeta = $("previewMeta");
    const previewFolder = $("previewFolder");
    const categories = ["all", ...Array.from(new Set(DATA.map(item => item.category))).sort()];

    $("total").textContent = DATA.length.toLocaleString();
    $("category").innerHTML = categories.map(cat => `<option value="${{cat}}">${{cat === "all" ? "全部类别" : cat}}</option>`).join("");

    function normalize(text) {{
      return String(text || "").toLowerCase();
    }}

    function byIndex(index) {{
      return DATA.find(item => Number(item.index) === Number(index));
    }}

    function fileUrlFor(item) {{
      return new URL(item.original_href, window.location.href).href;
    }}

    function contextPayload(item) {{
      return {{
        index: item.index,
        name: item.name,
        relativePath: item.relative_path,
        fileUrl: fileUrlFor(item),
        width: item.width,
        height: item.height,
        category: item.category
      }};
    }}

    function notifyContextItem(item) {{
      if (window.galleryBridge && typeof window.galleryBridge.setContextItem === "function") {{
        window.galleryBridge.setContextItem(contextPayload(item));
      }}
      window.__galleryLastContextItem = contextPayload(item);
    }}

    function closePreview() {{
      state.previewIndex = null;
      overlay.hidden = true;
      previewImage.removeAttribute("src");
      document.body.classList.remove("preview-open");
      document.querySelectorAll(".card.active").forEach(card => card.classList.remove("active"));
    }}

    function openPreview(item) {{
      if (!item) return;
      if (!overlay.hidden && state.previewIndex === item.index) {{
        closePreview();
        return;
      }}
      state.previewIndex = item.index;
      previewTitle.textContent = item.name;
      previewMeta.textContent = `${{item.width}}×${{item.height}} · ${{item.category}} · ${{item.relative_path}}`;
      previewImage.src = item.original_href;
      previewImage.alt = item.name;
      previewFolder.hidden = !(window.galleryBridge && typeof window.galleryBridge.openInFolder === "function");
      overlay.hidden = false;
      document.body.classList.add("preview-open");
      document.querySelectorAll(".card.active").forEach(card => card.classList.remove("active"));
      document.querySelector(`[data-card-index="${{item.index}}"]`)?.classList.add("active");
      notifyContextItem(item);
    }}

    function filteredItems() {{
      const q = normalize(state.query);
      let rows = DATA.filter(item => {{
        const categoryOk = state.category === "all" || item.category === state.category;
        const queryOk = !q || normalize(item.relative_path + " " + item.category + " " + item.name).includes(q);
        return categoryOk && queryOk;
      }});
      rows.sort((a, b) => {{
        const areaA = Number(a.width || 0) * Number(a.height || 0);
        const areaB = Number(b.width || 0) * Number(b.height || 0);
        if (state.sort === "area_desc") return areaB - areaA || a.relative_path.localeCompare(b.relative_path);
        if (state.sort === "area_asc") return areaA - areaB || a.relative_path.localeCompare(b.relative_path);
        if (state.sort === "size_desc") return Number(b.size_bytes || 0) - Number(a.size_bytes || 0);
        return a.relative_path.localeCompare(b.relative_path);
      }});
      return rows;
    }}

    function render() {{
      const rows = filteredItems();
      const pageCount = Math.max(1, Math.ceil(rows.length / state.pageSize));
      state.page = Math.min(Math.max(1, state.page), pageCount);
      const start = (state.page - 1) * state.pageSize;
      const pageRows = rows.slice(start, start + state.pageSize);
      $("filtered").textContent = rows.length.toLocaleString();
      $("pageInfo").textContent = `${{state.page}} / ${{pageCount}}`;
      $("empty").hidden = rows.length !== 0;
      grid.innerHTML = pageRows.map(item => `
        <article class="card ${{state.previewIndex === item.index ? "active" : ""}}" data-card-index="${{item.index}}">
          <button class="thumb" type="button" data-index="${{item.index}}">
            <img loading="lazy" src="${{item.thumb}}" alt="${{item.name}}">
          </button>
          <div class="info">
            <div class="name">${{item.name}}</div>
            <div class="chips">
              <span class="chip">${{item.width}}×${{item.height}}</span>
              <span class="chip">${{item.category}}</span>
              <span class="chip">${{item.has_alpha === "true" ? "Alpha" : "Opaque"}}</span>
            </div>
            <div class="path">${{item.relative_path}}</div>
          </div>
        </article>
      `).join("");
      $("prev").disabled = state.page <= 1;
      $("next").disabled = state.page >= pageCount;
    }}

    grid.addEventListener("click", (event) => {{
      const trigger = event.target.closest("[data-index]");
      if (!trigger) return;
      const item = byIndex(trigger.dataset.index);
      openPreview(item);
    }});

    grid.addEventListener("pointerover", (event) => {{
      const trigger = event.target.closest("[data-index], [data-card-index]");
      if (!trigger) return;
      const item = byIndex(trigger.dataset.index || trigger.dataset.cardIndex);
      if (item) notifyContextItem(item);
    }});

    document.addEventListener("contextmenu", (event) => {{
      const indexed = event.target.closest("[data-index], [data-card-index]");
      const item = indexed ? byIndex(indexed.dataset.index || indexed.dataset.cardIndex) : byIndex(state.previewIndex);
      if (!item) return;
      notifyContextItem(item);
      if (window.galleryBridge && typeof window.galleryBridge.openInFolder === "function") {{
        event.preventDefault();
        window.galleryBridge.openInFolder(contextPayload(item));
      }}
    }});

    overlay.addEventListener("click", (event) => {{
      if (event.target === overlay || event.target === previewImage || event.target.closest("[data-preview-close]")) {{
        closePreview();
      }}
    }});

    previewFolder.addEventListener("click", () => {{
      const item = byIndex(state.previewIndex);
      if (item && window.galleryBridge && typeof window.galleryBridge.openInFolder === "function") {{
        window.galleryBridge.openInFolder(contextPayload(item));
      }}
    }});

    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape" && !overlay.hidden) closePreview();
    }});

    $("search").addEventListener("input", (event) => {{ state.query = event.target.value; state.page = 1; render(); }});
    $("category").addEventListener("change", (event) => {{ state.category = event.target.value; state.page = 1; render(); }});
    $("sort").addEventListener("change", (event) => {{ state.sort = event.target.value; state.page = 1; render(); }});
    $("pageSize").addEventListener("change", (event) => {{ state.pageSize = Number(event.target.value); state.page = 1; render(); }});
    $("reset").addEventListener("click", () => {{
      state.query = "";
      state.category = "all";
      state.sort = "area_desc";
      state.page = 1;
      $("search").value = "";
      $("category").value = "all";
      $("sort").value = "area_desc";
      render();
    }});
    $("prev").addEventListener("click", () => {{ state.page -= 1; closePreview(); render(); window.scrollTo({{ top: 0, behavior: "smooth" }}); }});
    $("next").addEventListener("click", () => {{ state.page += 1; closePreview(); render(); window.scrollTo({{ top: 0, behavior: "smooth" }}); }});
    render();
  </script>
</body>
</html>
"""
    index_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--image-index", required=True)
    parser.add_argument("--gallery-dir", help="Directory output mode: writes index.html, assets/, gallery_manifest.json.")
    parser.add_argument("--gallery-file", help="Report-file output mode: writes a named HTML file such as 2.报告/全量图片画廊.html.")
    parser.add_argument("--manifest", help="Optional manifest JSON path. Defaults next to the gallery output.")
    parser.add_argument("--max-size", type=int, default=320)
    parser.add_argument("--title", default="全量图片资源画廊")
    parser.add_argument("--original-prefix", default="../ExportedProject/")
    args = parser.parse_args()

    if bool(args.gallery_dir) == bool(args.gallery_file):
        parser.error("Pass exactly one of --gallery-dir or --gallery-file.")

    project_root = Path(args.project_root).resolve()
    image_index_path = Path(args.image_index).resolve()
    if args.gallery_file:
        gallery_path = Path(args.gallery_file).resolve()
        gallery_base = gallery_path.parent
        thumb_dir = gallery_base / f"{gallery_path.stem}_assets" / "thumbnails"
        manifest_path = Path(args.manifest).resolve() if args.manifest else gallery_base / f"{gallery_path.stem}_manifest.json"
    else:
        gallery_dir = Path(args.gallery_dir).resolve()
        gallery_path = gallery_dir / "index.html"
        gallery_base = gallery_dir
        thumb_dir = gallery_dir / "assets" / "thumbnails"
        manifest_path = Path(args.manifest).resolve() if args.manifest else gallery_dir / "gallery_manifest.json"
    gallery_base.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    rows = read_image_index(image_index_path)
    items = []
    created = 0
    failed = []
    for index, row in enumerate(rows):
        relative_path = row["relative_path"]
        src = project_root / Path(relative_path)
        name = Path(relative_path).name
        thumb = thumb_dir / thumb_name(relative_path)
        try:
            if make_thumbnail(src, thumb, args.max_size):
                created += 1
            items.append(
                {
                    "relative_path": relative_path,
                    "index": index,
                    "name": name,
                    "width": row.get("width", ""),
                    "height": row.get("height", ""),
                    "mode": row.get("mode", ""),
                    "has_alpha": row.get("has_alpha", ""),
                    "category": row.get("category", ""),
                    "size_bytes": row.get("size_bytes", ""),
                    "thumb": rel(thumb, gallery_base),
                    "original_href": args.original_prefix + relative_path,
                }
            )
        except Exception as exc:
            failed.append({"relative_path": relative_path, "error": str(exc)})

    write_gallery(gallery_path, items, args.title)
    manifest_path.write_text(
        json.dumps(
            {
                "item_count": len(items),
                "thumbnail_count_created": created,
                "failed_count": len(failed),
                "failed": failed[:100],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"item_count": len(items), "created": created, "failed": len(failed), "gallery": str(gallery_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

const state = {
  manifest: null,
  rows: [],
  visibleRows: [],
  query: "",
  category: "all",
  sort: "",
  page: 1,
  pageSize: 0,
  previewIndex: -1,
  previewZoom: 1,
  previewPanX: 0,
  previewPanY: 0,
  previewPointerId: null,
  previewDragStartX: 0,
  previewDragStartY: 0,
  previewDragOriginX: 0,
  previewDragOriginY: 0
};

const $ = (id) => document.getElementById(id);

function normalize(text) {
  return String(text || "").toLowerCase();
}

function area(item) {
  return Number(item.width || 0) * Number(item.height || 0);
}

function categories(rows) {
  return ["all", ...Array.from(new Set(rows.map((item) => item.category || "未分类"))).sort()];
}

function optionElement(value, label) {
  const option = document.createElement("option");
  option.value = String(value);
  option.textContent = String(label);
  return option;
}

function chipElement(label) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = String(label);
  return chip;
}

function filteredRows() {
  const query = normalize(state.query);
  const rows = state.rows.filter((item) => {
    const categoryOk = state.category === "all" || item.category === state.category;
    const text = `${item.name} ${item.relative_path} ${item.category} ${item.source_label}`;
    return categoryOk && (!query || normalize(text).includes(query));
  });

  rows.sort((a, b) => {
    const leftPath = a.relative_path || "";
    const rightPath = b.relative_path || "";
    if (state.sort === "area_desc") return area(b) - area(a) || leftPath.localeCompare(rightPath);
    if (state.sort === "area_asc") return area(a) - area(b) || leftPath.localeCompare(rightPath);
    if (state.sort === "size_desc") {
      return Number(b.size_bytes || 0) - Number(a.size_bytes || 0) || leftPath.localeCompare(rightPath);
    }
    return leftPath.localeCompare(rightPath);
  });
  return rows;
}

function configuredPageSizes(ui) {
  const sizes = Array.isArray(ui.page_sizes)
    ? ui.page_sizes.map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0)
    : [];
  return sizes.length ? sizes : [Math.max(1, state.rows.length || 1)];
}

function populatePageSizeOptions(ui) {
  const pageSizes = configuredPageSizes(ui);
  const defaultPageSize = Number(ui.default_page_size || pageSizes[0]);
  $("pageSize").replaceChildren(...pageSizes.map((size) => optionElement(size, `每页 ${size}`)));
  state.pageSize = pageSizes.includes(defaultPageSize) ? defaultPageSize : pageSizes[0];
  $("pageSize").value = String(state.pageSize);
}

function populateCategoryOptions() {
  $("category").replaceChildren(
    ...categories(state.rows).map((category) =>
      optionElement(category, category === "all" ? "全部分类" : category)
    )
  );
}

function itemById(id) {
  return state.rows.find((item) => item.id === id);
}

function cardById(id) {
  return document.querySelector(`[data-card-id="${CSS.escape(id)}"]`);
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function updatePreviewTransform() {
  $("previewImage").style.transform =
    `translate(${state.previewPanX}px, ${state.previewPanY}px) scale(${state.previewZoom})`;
  $("previewStage").classList.toggle("panning", state.previewPointerId !== null);
}

function resetPreviewTransform() {
  state.previewZoom = 1;
  state.previewPanX = 0;
  state.previewPanY = 0;
  state.previewPointerId = null;
  updatePreviewTransform();
}

function openPreview(id) {
  const item = itemById(id);
  if (!item) return;
  resetPreviewTransform();
  state.previewIndex = state.visibleRows.findIndex((row) => row.id === id);
  $("previewTitle").textContent = item.name || "";
  $("previewMeta").textContent = `${item.width || 0}x${item.height || 0} · ${item.category || "未分类"} · ${item.relative_path || ""}`;
  $("previewImage").src = item.source_url;
  $("previewImage").alt = item.name || "";
  $("previewOverlay").hidden = false;
  document.body.classList.add("preview-open");
  document.querySelectorAll(".card.active").forEach((card) => card.classList.remove("active"));
  cardById(item.id)?.classList.add("active");
}

function closePreview() {
  $("previewOverlay").hidden = true;
  $("previewImage").removeAttribute("src");
  resetPreviewTransform();
  document.body.classList.remove("preview-open");
  document.querySelectorAll(".card.active").forEach((card) => card.classList.remove("active"));
}

function currentPreviewItem() {
  return state.visibleRows[state.previewIndex] || null;
}

function openItemInFolder(item) {
  if (item) window.galleryBridge.openInFolder(item.id);
}

function cardElement(item) {
  const article = document.createElement("article");
  article.className = "card";
  article.dataset.cardId = item.id;

  const thumbButton = document.createElement("button");
  thumbButton.className = "thumb";
  thumbButton.type = "button";
  thumbButton.dataset.id = item.id;

  const image = document.createElement("img");
  image.loading = "lazy";
  image.src = item.thumb_url;
  image.alt = item.name || "";
  thumbButton.append(image);

  const info = document.createElement("div");
  info.className = "info";

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = item.name || "";

  const chips = document.createElement("div");
  chips.className = "chips";
  chips.append(
    chipElement(`${item.width || 0}x${item.height || 0}`),
    chipElement(item.category || "未分类"),
    chipElement(item.has_alpha ? "Alpha" : "Opaque")
  );

  const pathText = document.createElement("div");
  pathText.className = "path";
  pathText.textContent = item.relative_path || "";
  info.append(name, chips, pathText);
  article.append(thumbButton, info);
  return article;
}

function render() {
  const rows = filteredRows();
  state.visibleRows = rows;
  const pageCount = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(Math.max(1, state.page), pageCount);
  const start = (state.page - 1) * state.pageSize;
  const pageRows = rows.slice(start, start + state.pageSize);

  $("filtered").textContent = rows.length.toLocaleString();
  $("pageInfo").textContent = `${state.page} / ${pageCount}`;
  $("empty").hidden = rows.length > 0;
  $("prev").disabled = state.page <= 1;
  $("next").disabled = state.page >= pageCount;

  const fragment = document.createDocumentFragment();
  pageRows.forEach((item) => fragment.append(cardElement(item)));
  $("grid").replaceChildren(fragment);
}

async function init() {
  state.manifest = await window.galleryBridge.loadManifest();
  state.rows = state.manifest.items || [];
  const ui = state.manifest.ui || {};
  $("sort").value = ui.default_sort || $("sort").options[0]?.value || "";
  state.sort = $("sort").value;
  populatePageSizeOptions(ui);
  $("title").textContent = state.manifest.title || "图片画廊";
  $("total").textContent = state.rows.length.toLocaleString();
  populateCategoryOptions();
  render();
}

$("grid").addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-id]");
  if (trigger) openPreview(trigger.dataset.id);
});

$("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  state.page = 1;
  render();
});

$("category").addEventListener("change", (event) => {
  state.category = event.target.value;
  state.page = 1;
  render();
});

$("sort").addEventListener("change", (event) => {
  state.sort = event.target.value;
  state.page = 1;
  render();
});

$("pageSize").addEventListener("change", (event) => {
  state.pageSize = Number(event.target.value);
  state.page = 1;
  render();
});

$("prev").addEventListener("click", () => {
  state.page -= 1;
  closePreview();
  render();
});

$("next").addEventListener("click", () => {
  state.page += 1;
  closePreview();
  render();
});

$("closePreview").addEventListener("click", closePreview);

$("previewStage").addEventListener("wheel", (event) => {
  event.preventDefault();
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
  state.previewZoom = clamp(state.previewZoom * factor, 0.2, 12);
  updatePreviewTransform();
}, { passive: false });

$("previewStage").addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  state.previewPointerId = event.pointerId;
  state.previewDragStartX = event.clientX;
  state.previewDragStartY = event.clientY;
  state.previewDragOriginX = state.previewPanX;
  state.previewDragOriginY = state.previewPanY;
  $("previewStage").setPointerCapture(event.pointerId);
  updatePreviewTransform();
});

$("previewStage").addEventListener("pointermove", (event) => {
  if (state.previewPointerId !== event.pointerId) return;
  event.preventDefault();
  state.previewPanX = state.previewDragOriginX + event.clientX - state.previewDragStartX;
  state.previewPanY = state.previewDragOriginY + event.clientY - state.previewDragStartY;
  updatePreviewTransform();
});

function stopPreviewPan(event) {
  if (state.previewPointerId !== event.pointerId) return;
  if ($("previewStage").hasPointerCapture(event.pointerId)) {
    $("previewStage").releasePointerCapture(event.pointerId);
  }
  state.previewPointerId = null;
  updatePreviewTransform();
}

$("previewStage").addEventListener("pointerup", stopPreviewPan);
$("previewStage").addEventListener("pointercancel", stopPreviewPan);

$("openFolder").addEventListener("click", () => {
  openItemInFolder(currentPreviewItem());
});

$("copyPath").addEventListener("click", () => {
  const item = currentPreviewItem();
  if (item) window.galleryBridge.copyPath(item.id);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePreview();
});

init().catch((error) => {
  const message = document.createElement("div");
  message.className = "empty error";
  message.textContent = `加载失败：${error.message}`;
  $("grid").replaceChildren(message);
});

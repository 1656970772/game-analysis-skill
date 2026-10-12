import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const bridge = window.modelViewerBridge;
const state = {
  runtime: null,
  manifest: null,
  items: [],
  filteredItems: [],
  selectedIndex: -1,
  currentModel: null,
  animationMixer: null,
  animationClips: [],
  animationActions: [],
  activeAction: null,
  currentClipIndex: -1,
  isAnimationPlaying: false,
  isTimelineScrubbing: false,
  playbackSpeed: 1,
  previewMode: "single",
  loadToken: 0,
  assetBaseDir: "",
  gridMessage: "",
  filters: {
    text: "",
    format: "",
    texture: "",
    preview: "",
    tags: ""
  }
};

const viewport = document.getElementById("viewport");
const viewportError = document.getElementById("viewport-error");
const runtimeMeta = document.getElementById("runtime-meta");
const statusMeta = document.getElementById("status-meta");
const modelTitle = document.getElementById("model-title");
const modelMeta = document.getElementById("model-meta");
const modelErrors = document.getElementById("model-errors");
const fullBodyPreviewButton = document.getElementById("full-body-preview");
const modelGrid = document.getElementById("model-grid");
const previewOverlay = document.getElementById("preview-overlay");
const previewCloseButton = document.getElementById("preview-close");
const filterText = document.getElementById("filter-text");
const filterFormat = document.getElementById("filter-format");
const filterTexture = document.getElementById("filter-texture");
const filterPreview = document.getElementById("filter-preview");
const filterTags = document.getElementById("filter-tags");
const openFolderButton = document.getElementById("open-folder");
const openThumbsButton = document.getElementById("open-thumbs");
const copyPathButton = document.getElementById("copy-path");
const prevModelButton = document.getElementById("prev-model");
const nextModelButton = document.getElementById("next-model");
const animationPanel = document.getElementById("animation-panel");
const animationSidebar = document.getElementById("animation-sidebar");
const animationList = document.getElementById("animation-list");
const animationCount = document.getElementById("animation-count");
const animationSelect = document.getElementById("animation-select");
const animationToggle = document.getElementById("animation-toggle");
const animationTimeline = document.getElementById("animation-timeline");
const animationTime = document.getElementById("animation-time");
const animationSpeed = document.getElementById("animation-speed");
const animationStatus = document.getElementById("animation-status");
const animationCurrentName = document.getElementById("animation-current-name");

const MODEL_GRID_COLUMNS = 4;
const MODEL_GRID_ROW_HEIGHT = 388;
const MODEL_GRID_GAP = 14;
const MODEL_GRID_OVERSCAN_ROWS = 2;
const virtualGridState = {
  spacerEl: null,
  windowEl: null,
  startIndex: -1,
  endIndex: -1,
  selectedIndex: -1,
  frame: 0
};

let scene;
let camera;
let renderer;
let controls;
let loader;
let clock;
let resizeViewer = () => {};

function normalizeLocalPath(value) {
  return String(value || "").replace(/\\/g, "/");
}

function parentDir(value) {
  const normalized = normalizeLocalPath(value);
  const index = normalized.lastIndexOf("/");
  return index >= 0 ? normalized.slice(0, index) : "";
}

function isAbsoluteLocalPath(value) {
  return /^[a-zA-Z]:\//.test(value) || value.startsWith("/") || value.startsWith("//");
}

function joinLocalPath(baseDir, value) {
  const decoded = decodeURIComponent(String(value || "")).replace(/\\/g, "/");
  if (!decoded) return normalizeLocalPath(baseDir);
  if (isAbsoluteLocalPath(decoded)) return decoded;

  const segments = `${normalizeLocalPath(baseDir).replace(/\/+$/, "")}/${decoded}`
    .split("/")
    .filter(Boolean);
  const normalized = [];
  for (const segment of segments) {
    if (segment === ".") continue;
    if (segment === "..") {
      normalized.pop();
    } else {
      normalized.push(segment);
    }
  }

  const first = normalized[0] || "";
  const prefix = /^[a-zA-Z]:$/.test(first) ? "" : "/";
  return `${prefix}${normalized.join("/")}`;
}

function assetUrl(localPath) {
  const normalized = normalizeLocalPath(localPath);
  const root = normalizeLocalPath(state.runtime?.studyRoot || "");
  const rootWithSlash = root.endsWith("/") ? root : `${root}/`;
  const lowerPath = normalized.toLowerCase();
  const lowerRoot = root.toLowerCase();
  const lowerRootWithSlash = rootWithSlash.toLowerCase();
  let relative = normalized;

  if (lowerPath === lowerRoot) {
    relative = "";
  } else if (lowerPath.startsWith(lowerRootWithSlash)) {
    relative = normalized.slice(rootWithSlash.length);
  } else {
    throw new Error(`Path outside study-root: ${localPath}`);
  }

  return `app://asset/${relative
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/")}`;
}

function safeAssetUrl(localPath, fallback = "", options = {}) {
  try {
    return assetUrl(localPath);
  } catch (error) {
    if (!options.silent) {
      renderViewportError(error.message || "资源路径无效。");
    }
    return fallback;
  }
}

function thumbnailAssetUrl(item) {
  return item.thumbnail_path
    ? safeAssetUrl(item.thumbnail_path, placeholderThumb(item), { silent: true })
    : placeholderThumb(item);
}

function resolveAssetReference(url, baseDir = state.assetBaseDir) {
  const raw = String(url || "");
  if (!raw) return raw;

  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      throw new Error(`Blocked non-local asset URI: ${raw}`);
    }
    if (parsed.protocol === "data:" || parsed.protocol === "blob:" || parsed.protocol === "app:") {
      return raw;
    }
  } catch (error) {
    if (error.message && error.message.startsWith("Blocked non-local asset URI")) {
      throw error;
    }
  }

  return assetUrl(joinLocalPath(baseDir, url));
}

function renderViewportError(message) {
  viewportError.textContent = message || "";
  viewportError.hidden = false;
}

function clearViewportError() {
  viewportError.textContent = "";
  viewportError.hidden = true;
}

function placeholderThumb(item) {
  const label = (item?.source_format || "model").toUpperCase();
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="240" height="160" viewBox="0 0 240 160">
      <rect width="240" height="160" fill="#20242b"/>
      <path d="M70 110 120 42l50 68H70Z" fill="#6bc1ff" opacity=".75"/>
      <circle cx="164" cy="48" r="14" fill="#f3c969"/>
      <text x="120" y="138" text-anchor="middle" font-family="Arial" font-size="18" fill="#edf2f7">${label}</text>
    </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function setupThree() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x16191f);

  camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
  camera.position.set(4, 3, 5);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  viewport.appendChild(renderer.domElement);
  clock = new THREE.Clock();

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  scene.add(new THREE.GridHelper(10, 10, 0x5a6472, 0x303741));
  scene.add(new THREE.HemisphereLight(0xf2f7ff, 0x38404c, 1.7));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
  keyLight.position.set(5, 7, 4);
  scene.add(keyLight);

  const loadingManager = new THREE.LoadingManager();
  loadingManager.setURLModifier((url) => {
    try {
      return resolveAssetReference(url);
    } catch (error) {
      renderViewportError(error.message);
      throw error;
    }
  });
  loader = new GLTFLoader(loadingManager);

  const resize = () => {
    const rect = viewport.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();
    renderer.setSize(rect.width, rect.height, false);
  };
  new ResizeObserver(resize).observe(viewport);
  resizeViewer = resize;
  resize();

  const render = () => {
    requestAnimationFrame(render);
    const delta = clock.getDelta();
    if (state.animationMixer && state.isAnimationPlaying && !state.isTimelineScrubbing) {
      state.animationMixer.update(delta * state.playbackSpeed);
      syncAnimationTimeline();
    }
    controls.update();
    renderer.render(scene, camera);
  };
  render();
}

function createModelLoader(baseDir) {
  const loadingManager = new THREE.LoadingManager();
  loadingManager.setURLModifier((url) => {
    try {
      return resolveAssetReference(url, baseDir);
    } catch (error) {
      renderViewportError(error.message);
      throw error;
    }
  });
  return new GLTFLoader(loadingManager);
}

function loadGltf(modelPath, baseDir) {
  return new Promise((resolve, reject) => {
    let modelUrl = "";
    try {
      modelUrl = assetUrl(modelPath);
    } catch (error) {
      reject(error);
      return;
    }
    createModelLoader(baseDir).load(modelUrl, resolve, undefined, reject);
  });
}

function clearModel() {
  clearAnimationState();
  if (!state.currentModel) return;
  scene.remove(state.currentModel);
  disposeObject(state.currentModel);
  state.currentModel = null;
}

function clearAnimationState() {
  if (state.animationMixer) {
    state.animationMixer.stopAllAction();
  }
  state.animationMixer = null;
  state.animationClips = [];
  state.animationActions = [];
  state.activeAction = null;
  state.currentClipIndex = -1;
  state.isAnimationPlaying = false;
  state.isTimelineScrubbing = false;
  resetAnimationControls();
}

function resetAnimationControls() {
  if (!animationPanel) return;
  previewOverlay.classList.remove("has-animations");
  animationPanel.hidden = true;
  animationSidebar.hidden = true;
  animationList.innerHTML = "";
  animationCount.textContent = "0";
  animationSelect.innerHTML = "";
  animationSelect.disabled = true;
  animationToggle.disabled = true;
  animationToggle.textContent = "播放";
  animationTimeline.min = "0";
  animationTimeline.max = "0";
  animationTimeline.value = "0";
  animationTimeline.disabled = true;
  animationSpeed.value = String(state.playbackSpeed);
  animationSpeed.disabled = true;
  animationTime.textContent = "0.00 / 0.00s";
  animationCurrentName.textContent = "未选择动作";
  animationStatus.textContent = "";
}

function disposeObject(object) {
  if (!object || !object.traverse) return;
  object.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
    if (node.material) {
      const materials = Array.isArray(node.material) ? node.material : [node.material];
      for (const material of materials) material.dispose();
    }
  });
}

function fitCameraToObject(object) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 1);
  const distance = maxDim * 1.8;

  camera.near = Math.max(maxDim / 1000, 0.01);
  camera.far = Math.max(maxDim * 100, 1000);
  camera.position.set(center.x + distance, center.y + distance * 0.55, center.z + distance);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

function normalizeModelToOrigin(object) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;

  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(new THREE.Vector3(center.x, box.min.y, center.z));
  object.updateMatrixWorld(true);
}

function selectedItem() {
  return state.filteredItems[state.selectedIndex] || null;
}

function isPreviewOpen() {
  return Boolean(previewOverlay && !previewOverlay.hidden);
}

function hasFullBodyPreview(item) {
  return Array.isArray(item?.full_body_preview?.parts) && item.full_body_preview.parts.length > 1;
}

function isFullBodyPreviewActive(item) {
  return state.previewMode === "full_body" && hasFullBodyPreview(item);
}

function renderFullBodyPreviewAction(item) {
  const hasPreview = hasFullBodyPreview(item);
  previewOverlay.classList.toggle("has-full-body-preview", hasPreview);
  if (!fullBodyPreviewButton) return;
  fullBodyPreviewButton.hidden = !hasPreview;
  fullBodyPreviewButton.disabled = !hasPreview;
  fullBodyPreviewButton.textContent = isFullBodyPreviewActive(item) ? "单件预览" : "全身预览";
  fullBodyPreviewButton.title = hasPreview
    ? `${item.full_body_preview.name || "全身预览"}`
    : "";
}

function setRuntimeStatus() {
  if (!state.runtime) return;
  runtimeMeta.textContent = state.runtime.manifest;
}

function renderMeta(item) {
  const fullBodyActive = isFullBodyPreviewActive(item);
  modelTitle.textContent = fullBodyActive
    ? item.full_body_preview.name || "全身预览"
    : item
      ? item.name || item.id || "未命名模型"
      : "未选择模型";
  modelMeta.innerHTML = "";
  modelErrors.textContent = "";
  renderFullBodyPreviewAction(item);

  if (!item) return;

  const rows = [
    ["格式", item.source_format || "-"],
    ["贴图", item.texture_status || "-"],
    ["预览", item.preview_status || "-"],
    ["材质", item.material_status || "-"],
    ["类型", assetRoleLabel(item)],
    ["视觉来源", visualSourceLabel(item)],
    ["动作", renderAnimationSummary(item)],
    ["路径", item.source_path || item.preview_path || "-"],
    ["标签", renderTags(item)]
  ];

  for (const [label, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    if (value instanceof Node) {
      detail.appendChild(value);
    } else {
      detail.textContent = value;
    }
    modelMeta.append(term, detail);
  }

  if (Array.isArray(item.errors) && item.errors.length) {
    modelErrors.textContent = item.errors.join("；");
  }
}

function visibleTags(item) {
  const tags = Array.isArray(item?.tags) ? item.tags : [];
  const redundantTags = new Set(
    [
      item?.source_format,
      item?.texture_status ? `texture:${item.texture_status}` : "",
      item?.preview_status ? `preview:${item.preview_status}` : "",
      item?.material_status ? `material:${item.material_status}` : "",
      item?.animation_status ? `animation:${item.animation_status}` : ""
    ].filter(Boolean)
  );
  return tags.filter((tag) => !redundantTags.has(tag));
}

function renderTags(item) {
  const tags = visibleTags(item);
  if (!tags.length) return "-";

  const list = document.createElement("div");
  list.className = "tag-list";
  for (const tag of tags) {
    const pill = document.createElement("span");
    pill.className = "tag-pill";
    pill.textContent = tag;
    list.appendChild(pill);
  }
  return list;
}

function assetRoleLabel(item) {
  const role = item?.asset_role || "";
  const labels = {
    textured_art_model: "美术模型",
    map_structure_mesh: "地图结构",
    map_embedded_mesh: "地图内嵌网格",
    embedded_mesh_candidate: "内嵌网格候选",
    model_candidate: "模型候选"
  };
  return labels[role] || role || "-";
}

function visualSourceLabel(item) {
  const source = item?.visual_source || "";
  const labels = {
    model_material: "模型材质",
    tmx_tileset_layers: "TMX 瓦片层",
    embedded_mesh: "内嵌网格",
    model_file: "模型文件"
  };
  const label = labels[source] || source || "-";
  return item?.visual_source_path ? `${label}：${item.visual_source_path}` : label;
}

function renderAnimationSummary(item) {
  const count = Number(item?.stats?.animation_count || 0);
  if (!count) return item?.animation_status === "unknown" ? "未知" : "无";

  const names = Array.isArray(item.animations)
    ? item.animations
        .map((animation) => animation && animation.name)
        .filter(Boolean)
        .slice(0, 3)
    : [];
  const suffix = names.length ? `：${names.join("，")}${count > names.length ? "..." : ""}` : "";
  return `${animationOriginLabel(item)} ${count} 个${suffix}`;
}

function isProceduralAnimationPreview(item) {
  return Boolean(item?.is_generated_animation_preview || item?.animation_origin === "procedural_preview");
}

function animationOriginLabel(item) {
  if (isProceduralAnimationPreview(item)) return "程序化预览";
  if (item?.animation_origin === "embedded") return "真实导出";
  return "动作";
}

function formatSeconds(value) {
  const seconds = Number.isFinite(value) ? Math.max(0, value) : 0;
  return seconds.toFixed(2);
}

function renderStatus() {
  const total = state.items.length;
  const count = state.filteredItems.length;
  const current = state.selectedIndex >= 0 ? state.selectedIndex + 1 : 0;
  statusMeta.textContent = isPreviewOpen()
    ? `${current} / ${count}（总计 ${total}）`
    : `结果 ${count} / ${total}`;
}

function renderModelGrid() {
  modelGrid.innerHTML = "";
  resetVirtualGridState();
  if (!state.filteredItems.length) {
    const empty = document.createElement("div");
    empty.className = "grid-empty";
    empty.textContent = state.gridMessage || "本项目没有可预览模型";
    modelGrid.appendChild(empty);
    return;
  }

  const totalRows = Math.ceil(state.filteredItems.length / MODEL_GRID_COLUMNS);
  const spacer = document.createElement("div");
  spacer.className = "virtual-grid-spacer";
  spacer.style.height = `${virtualGridHeight(totalRows)}px`;

  const windowEl = document.createElement("div");
  windowEl.className = "virtual-grid-window";

  spacer.appendChild(windowEl);
  modelGrid.appendChild(spacer);
  virtualGridState.spacerEl = spacer;
  virtualGridState.windowEl = windowEl;
  renderVirtualModelGrid(true);
}

function resetVirtualGridState() {
  if (virtualGridState.frame) {
    cancelAnimationFrame(virtualGridState.frame);
  }
  virtualGridState.spacerEl = null;
  virtualGridState.windowEl = null;
  virtualGridState.startIndex = -1;
  virtualGridState.endIndex = -1;
  virtualGridState.selectedIndex = -1;
  virtualGridState.frame = 0;
}

function virtualRowPitch() {
  return MODEL_GRID_ROW_HEIGHT + MODEL_GRID_GAP;
}

function virtualGridHeight(totalRows) {
  if (!totalRows) return 0;
  return totalRows * MODEL_GRID_ROW_HEIGHT + (totalRows - 1) * MODEL_GRID_GAP;
}

function renderVirtualModelGrid(force = false) {
  if (!virtualGridState.windowEl) return;

  const total = state.filteredItems.length;
  const totalRows = Math.ceil(total / MODEL_GRID_COLUMNS);
  const rowPitch = virtualRowPitch();
  const startRow = Math.max(
    0,
    Math.floor(modelGrid.scrollTop / rowPitch) - MODEL_GRID_OVERSCAN_ROWS
  );
  const visibleRows =
    Math.ceil((modelGrid.clientHeight || rowPitch) / rowPitch) + MODEL_GRID_OVERSCAN_ROWS * 2;
  const endRow = Math.min(totalRows, startRow + visibleRows);
  const startIndex = startRow * MODEL_GRID_COLUMNS;
  const endIndex = Math.min(total, endRow * MODEL_GRID_COLUMNS);

  if (
    !force &&
    startIndex === virtualGridState.startIndex &&
    endIndex === virtualGridState.endIndex &&
    state.selectedIndex === virtualGridState.selectedIndex
  ) {
    return;
  }

  virtualGridState.startIndex = startIndex;
  virtualGridState.endIndex = endIndex;
  virtualGridState.selectedIndex = state.selectedIndex;
  virtualGridState.windowEl.style.transform = `translateY(${startRow * rowPitch}px)`;
  virtualGridState.windowEl.innerHTML = "";

  for (let index = startIndex; index < endIndex; index += 1) {
    virtualGridState.windowEl.appendChild(createModelCard(state.filteredItems[index], index));
  }
}

function scheduleVirtualModelGridRender() {
  if (virtualGridState.frame) return;
  virtualGridState.frame = requestAnimationFrame(() => {
    virtualGridState.frame = 0;
    renderVirtualModelGrid();
  });
}

function scrollGridToIndex(index) {
  if (index < 0 || !state.filteredItems.length) return;
  const row = Math.floor(index / MODEL_GRID_COLUMNS);
  modelGrid.scrollTop = row * virtualRowPitch();
  renderVirtualModelGrid(true);
}

function createModelCard(item, index) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `model-card${index === state.selectedIndex ? " is-active" : ""}`;
  button.dataset.index = String(index);
  button.title = item.source_path || item.preview_path || item.name || "";
  button.addEventListener("click", () => openPreview(index));

  const thumb = document.createElement("span");
  thumb.className = "model-card-thumb";
  const img = document.createElement("img");
  img.alt = item.name || item.id || "model";
  img.loading = "lazy";
  img.src = thumbnailAssetUrl(item);
  img.onerror = () => {
    img.src = placeholderThumb(item);
  };
  thumb.appendChild(img);

  const info = document.createElement("span");
  info.className = "model-card-info";
  const label = document.createElement("span");
  label.className = "model-card-name";
  label.textContent = item.name || item.id || "model";
  const chips = document.createElement("span");
  chips.className = "card-chips";
  for (const value of [
    item.source_format,
    item.texture_status,
    item.preview_status,
    item.material_status,
    assetRoleLabel(item),
    item.animation_status === "with" && isProceduralAnimationPreview(item)
      ? `程序化动作 ${item.stats?.animation_count || 0}`
      : item.animation_status === "with"
        ? `动作 ${item.stats?.animation_count || 0}`
        : item.animation_status
  ].filter(Boolean)) {
    chips.appendChild(createChip(value));
  }
  const path = document.createElement("span");
  path.className = "model-card-path";
  path.textContent = compactPath(item.source_path || item.preview_path || item.model_dir || "");
  info.append(label, chips, path);

  button.append(thumb, info);
  return button;
}

function createChip(value) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = value;
  return chip;
}

function compactPath(value) {
  const normalized = normalizeLocalPath(value);
  const root = normalizeLocalPath(state.runtime?.studyRoot || "");
  const rootWithSlash = root.endsWith("/") ? root : `${root}/`;
  if (root && normalized.toLowerCase().startsWith(rootWithSlash.toLowerCase())) {
    return normalized.slice(rootWithSlash.length);
  }
  return normalized;
}

function updateGridSelection() {
  modelGrid.querySelectorAll(".model-card.is-active").forEach((card) => {
    card.classList.remove("is-active");
  });
  const active = modelGrid.querySelector(`[data-index="${state.selectedIndex}"]`);
  if (active) active.classList.add("is-active");
}

function uniqueValues(selector) {
  return [...new Set(state.items.map(selector).filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b), "zh-CN")
  );
}

function updateSelectOptions(select, values, label) {
  const current = select.value;
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = label;
  select.appendChild(all);
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  select.value = values.includes(current) ? current : "";
}

function populateFilters() {
  updateSelectOptions(filterFormat, uniqueValues((item) => item.source_format), "全部格式");
  updateSelectOptions(filterTexture, uniqueValues((item) => item.texture_status), "全部贴图状态");
  updateSelectOptions(filterPreview, uniqueValues((item) => item.preview_status), "全部预览状态");
  updateSelectOptions(
    filterTags,
    [...new Set(state.items.flatMap((item) => (Array.isArray(item.tags) ? item.tags : [])))].sort(),
    "全部标签"
  );
}

function matchesText(item, text) {
  if (!text) return true;
  const haystack = [
    item.id,
    item.name,
    item.source_path,
    item.preview_path,
    item.model_dir,
    ...(Array.isArray(item.tags) ? item.tags : [])
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(text);
}

function applyFilters() {
  const activeItem = selectedItem();
  state.filters = {
    text: filterText.value.trim().toLowerCase(),
    format: filterFormat.value,
    texture: filterTexture.value,
    preview: filterPreview.value,
    tags: filterTags.value
  };

  state.filteredItems = state.items.filter((item) => {
    if (!matchesText(item, state.filters.text)) return false;
    if (state.filters.format && item.source_format !== state.filters.format) return false;
    if (state.filters.texture && item.texture_status !== state.filters.texture) return false;
    if (state.filters.preview && item.preview_status !== state.filters.preview) return false;
    if (
      state.filters.tags &&
      (!Array.isArray(item.tags) || !item.tags.includes(state.filters.tags))
    ) {
      return false;
    }
    return true;
  });

  state.gridMessage = "";
  state.selectedIndex = activeItem ? state.filteredItems.indexOf(activeItem) : -1;
  modelGrid.scrollTop = 0;
  renderStatus();
  renderModelGrid();
  if (isPreviewOpen() && state.selectedIndex >= 0) {
    selectItem(state.selectedIndex);
  } else if (isPreviewOpen()) {
    closePreview();
  } else {
    state.loadToken += 1;
    clearModel();
    renderMeta(null);
    clearViewportError();
  }
}

function setupAnimations(gltf, item) {
  clearAnimationState();
  const clips = Array.isArray(gltf?.animations) ? gltf.animations.filter(Boolean) : [];
  if (!clips.length) {
    if (Number(item?.stats?.animation_count || 0) > 0) {
      animationPanel.hidden = false;
      animationStatus.textContent = "manifest 记录了动作，但 glTF 运行时没有返回可播放 clip。";
    }
    return;
  }

  state.animationMixer = new THREE.AnimationMixer(state.currentModel);
  state.animationClips = clips;
  state.animationActions = clips.map((clip) => state.animationMixer.clipAction(clip));
  state.isAnimationPlaying = true;

  previewOverlay.classList.add("has-animations");
  animationPanel.hidden = false;
  animationSidebar.hidden = false;
  animationSelect.disabled = false;
  animationToggle.disabled = false;
  animationTimeline.disabled = false;
  animationSpeed.disabled = false;
  animationSelect.innerHTML = "";
  clips.forEach((clip, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `${clip.name || `Animation ${index + 1}`} (${formatSeconds(clip.duration)}s)`;
    animationSelect.appendChild(option);
  });
  renderAnimationList();
  animationCount.textContent = `${clips.length} 个`;
  animationStatus.textContent = isProceduralAnimationPreview(item)
    ? `${clips.length} 个程序化预览动作可播放`
    : `${clips.length} 个动作可播放`;
  selectAnimationClip(0, { restart: true });
}

function renderAnimationList() {
  animationList.innerHTML = "";
  state.animationClips.forEach((clip, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "animation-list-button";
    button.dataset.animationIndex = String(index);
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", index === state.currentClipIndex ? "true" : "false");
    button.addEventListener("click", () => selectAnimationClip(index, { restart: true }));

    const name = document.createElement("span");
    name.className = "animation-list-name";
    name.textContent = clip.name || `Animation ${index + 1}`;
    const duration = document.createElement("span");
    duration.className = "animation-list-duration";
    duration.textContent = `${formatSeconds(clip.duration)}s`;
    button.append(name, duration);
    animationList.appendChild(button);
  });
  updateAnimationListSelection();
}

function updateAnimationListSelection() {
  animationList.querySelectorAll(".animation-list-button").forEach((button) => {
    const isActive = Number(button.dataset.animationIndex) === state.currentClipIndex;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function selectAnimationClip(index, options = {}) {
  if (index < 0 || index >= state.animationClips.length || !state.animationMixer) return;
  if (state.activeAction) {
    state.activeAction.stop();
  }

  state.currentClipIndex = index;
  state.activeAction = state.animationActions[index];
  const clip = state.animationClips[index];
  const duration = Math.max(clip.duration || 0, 0);
  animationSelect.value = String(index);
  animationTimeline.max = String(duration);
  animationTimeline.value = "0";
  animationCurrentName.textContent = clip.name || `Animation ${index + 1}`;
  animationToggle.textContent = state.isAnimationPlaying ? "暂停" : "播放";
  updateAnimationTime(0, duration);
  updateAnimationListSelection();

  state.activeAction.reset();
  state.activeAction.enabled = true;
  state.activeAction.paused = false;
  state.activeAction.play();
  state.animationMixer.setTime(0);
}

function updateAnimationTime(current, duration = currentAnimationDuration()) {
  animationTime.textContent = `${formatSeconds(current)} / ${formatSeconds(duration)}s`;
}

function currentAnimationDuration() {
  const clip = state.animationClips[state.currentClipIndex];
  return Math.max(clip?.duration || 0, 0);
}

function syncAnimationTimeline() {
  if (!state.activeAction || state.currentClipIndex < 0) return;
  const duration = currentAnimationDuration();
  const time = duration > 0 ? state.activeAction.time % duration : 0;
  animationTimeline.value = String(time);
  updateAnimationTime(time, duration);
}

function pauseAnimationPlayback() {
  if (!state.activeAction) return;
  state.isAnimationPlaying = false;
  animationToggle.textContent = "播放";
}

function toggleAnimationPlayback() {
  if (!state.activeAction) return;
  state.isAnimationPlaying = !state.isAnimationPlaying;
  animationToggle.textContent = state.isAnimationPlaying ? "暂停" : "播放";
}

function scrubAnimation(value) {
  if (!state.animationMixer || !state.activeAction) return;
  pauseAnimationPlayback();
  const time = Math.max(0, Math.min(Number(value) || 0, currentAnimationDuration()));
  state.activeAction.time = time;
  state.animationMixer.setTime(time);
  updateAnimationTime(time);
}

async function loadSelectedModel(item) {
  clearModel();
  if (!item) return;

  const loadToken = ++state.loadToken;
  state.assetBaseDir = item.model_dir || parentDir(item.preview_path);
  const modelPath = item.preview_path || item.source_path;
  const extension = (modelPath || "").split(".").pop().toLowerCase();
  if (!modelPath || !["glb", "gltf"].includes(extension)) {
    renderViewportError("该条目暂无可直接加载的 GLB/glTF 预览。");
    return;
  }

  clearViewportError();
  try {
    const gltf = await loadGltf(modelPath, state.assetBaseDir);
    if (loadToken !== state.loadToken) {
      disposeObject(gltf.scene);
      return;
    }
    clearModel();
    state.currentModel = gltf.scene;
    normalizeModelToOrigin(state.currentModel);
    scene.add(state.currentModel);
    setupAnimations(gltf, item);
    fitCameraToObject(state.currentModel);
    clearViewportError();
  } catch (error) {
    renderViewportError(error.message || "模型路径无效。");
  }
}

async function loadFullBodyPreview(item) {
  clearModel();
  if (!hasFullBodyPreview(item)) return;

  const loadToken = ++state.loadToken;
  const preview = item.full_body_preview;
  const group = new THREE.Group();
  group.name = preview.name || `${item.name || item.id || "model"} full body preview`;
  const errors = [];
  let animationGltf = null;

  clearViewportError();
  for (const part of preview.parts) {
    const modelPath = part.preview_path || part.source_path;
    const extension = (modelPath || "").split(".").pop().toLowerCase();
    if (!modelPath || !["glb", "gltf"].includes(extension)) {
      errors.push(`${part.name || part.role || "part"} 暂无可直接加载的 GLB/glTF。`);
      continue;
    }

    try {
      const gltf = await loadGltf(modelPath, part.model_dir || parentDir(modelPath));
      if (loadToken !== state.loadToken) {
        disposeObject(gltf.scene);
        disposeObject(group);
        return;
      }
      gltf.scene.name = part.name || part.role || "part";
      group.add(gltf.scene);
      if (part.item_id === item.id || part.name === item.name) {
        animationGltf = gltf;
      }
    } catch (error) {
      errors.push(`${part.name || part.role || "part"} 加载失败：${error.message || error}`);
    }
  }

  if (loadToken !== state.loadToken) {
    disposeObject(group);
    return;
  }
  if (!group.children.length) {
    renderViewportError(errors.join("；") || "全身预览没有可加载分件。");
    disposeObject(group);
    return;
  }

  clearModel();
  state.currentModel = group;
  normalizeModelToOrigin(state.currentModel);
  scene.add(state.currentModel);
  setupAnimations(animationGltf, item);
  fitCameraToObject(state.currentModel);
  if (errors.length) {
    renderViewportError(errors.join("；"));
  } else {
    clearViewportError();
  }
}

function selectItem(index) {
  if (index < 0 || index >= state.filteredItems.length) return;
  state.selectedIndex = index;
  state.previewMode = "single";
  const item = selectedItem();
  renderMeta(item);
  renderStatus();
  updateGridSelection();
  loadSelectedModel(item);
}

function openFullBodyPreview() {
  const item = selectedItem();
  if (!hasFullBodyPreview(item)) return;
  state.previewMode = isFullBodyPreviewActive(item) ? "single" : "full_body";
  renderMeta(item);
  if (isFullBodyPreviewActive(item)) {
    loadFullBodyPreview(item);
  } else {
    loadSelectedModel(item);
  }
  requestAnimationFrame(() => {
    resizeViewer();
  });
}

function openPreview(index) {
  if (index < 0 || index >= state.filteredItems.length) return;
  previewOverlay.hidden = false;
  document.body.classList.add("preview-open");
  selectItem(index);
  requestAnimationFrame(() => {
    resizeViewer();
  });
}

function closePreview() {
  previewOverlay.hidden = true;
  document.body.classList.remove("preview-open");
  state.previewMode = "single";
  state.loadToken += 1;
  clearModel();
  clearViewportError();
  scrollGridToIndex(state.selectedIndex);
  renderStatus();
  updateGridSelection();
}

function moveSelection(delta) {
  if (!isPreviewOpen() || !state.filteredItems.length) return;
  const next =
    (state.selectedIndex + delta + state.filteredItems.length) % state.filteredItems.length;
  selectItem(next);
  requestAnimationFrame(() => {
    resizeViewer();
  });
}

async function openCurrentFolder() {
  const item = selectedItem();
  if (!item) return;
  await handleBridgeResult(
    bridge.openFolder({ path: item.source_path || item.preview_path || item.model_dir })
  );
}

async function copyCurrentPath() {
  const item = selectedItem();
  if (!item) return;
  await handleBridgeResult(
    bridge.copyPath({ path: item.source_path || item.preview_path || item.model_dir })
  );
}

async function openThumbsFolder() {
  if (!state.runtime?.thumbsDir) {
    renderViewportError("缩略图目录尚未加载。");
    return;
  }
  await handleBridgeResult(bridge.openFolder({ path: state.runtime.thumbsDir }));
}

async function handleBridgeResult(promise) {
  try {
    const result = await promise;
    if (!result || result.ok === false) {
      renderViewportError(result?.reason || result?.error || "操作失败。");
      return result;
    }
    clearViewportError();
    return result;
  } catch (error) {
    renderViewportError(error.message || "操作失败。");
    return { ok: false, reason: error.message };
  }
}

async function loadManifest() {
  state.runtime = await bridge.getRuntime();
  setRuntimeStatus();

  const result = await bridge.readManifest();
  if (!result.ok) {
    state.items = [];
    state.filteredItems = [];
    state.gridMessage = result.message || "读取 manifest 失败。";
    renderViewportError(result.message || "读取 manifest 失败。");
    renderStatus();
    renderModelGrid();
    return;
  }

  state.manifest = result.manifest;
  state.items = Array.isArray(result.manifest.items) ? result.manifest.items : [];
  populateFilters();
  applyFilters();
}

filterText.addEventListener("input", applyFilters);
filterFormat.addEventListener("change", applyFilters);
filterTexture.addEventListener("change", applyFilters);
filterPreview.addEventListener("change", applyFilters);
filterTags.addEventListener("change", applyFilters);
openFolderButton.addEventListener("click", openCurrentFolder);
openThumbsButton.addEventListener("click", openThumbsFolder);
copyPathButton.addEventListener("click", copyCurrentPath);
prevModelButton.addEventListener("click", () => moveSelection(-1));
nextModelButton.addEventListener("click", () => moveSelection(1));
previewCloseButton.addEventListener("click", closePreview);
modelGrid.addEventListener("scroll", scheduleVirtualModelGridRender, { passive: true });
animationSelect.addEventListener("change", () => {
  selectAnimationClip(Number(animationSelect.value || 0), { restart: true });
});
animationToggle.addEventListener("click", toggleAnimationPlayback);
animationTimeline.addEventListener("pointerdown", pauseAnimationPlayback);
animationTimeline.addEventListener("pointerdown", () => {
  state.isTimelineScrubbing = true;
});
animationTimeline.addEventListener("pointerup", () => {
  state.isTimelineScrubbing = false;
});
animationTimeline.addEventListener("input", () => {
  scrubAnimation(animationTimeline.value);
});
animationSpeed.addEventListener("input", () => {
  state.playbackSpeed = Number(animationSpeed.value) || 1;
});
fullBodyPreviewButton.addEventListener("click", openFullBodyPreview);
previewOverlay.addEventListener("click", (event) => {
  if (event.target === previewOverlay) closePreview();
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isPreviewOpen()) {
    event.preventDefault();
    closePreview();
    return;
  }
  if (isEditableTarget(event.target)) return;
  if (!isPreviewOpen()) return;
  if (event.key === "ArrowLeft") moveSelection(-1);
  if (event.key === "ArrowRight") moveSelection(1);
});

function isEditableTarget(target) {
  if (!target) return false;
  const tagName = String(target.tagName || "").toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}

setupThree();
loadManifest();

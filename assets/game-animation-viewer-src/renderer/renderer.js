"use strict";

const state = {
  manifest: null,
  items: [],
  visibleItems: [],
  selectedItemId: null,
  selectedBuildId: null,
  selectedClipId: null,
  query: "",
  category: "all",
  status: "all",
  preview: null,
  previewToken: 0,
  currentFrame: 0,
  playing: false,
  speed: 1,
  lastTick: 0,
  accumulatedMs: 0,
  animationHandle: null
};

const $ = (id) => document.getElementById(id);

function option(value, label) {
  const element = document.createElement("option");
  element.value = String(value);
  element.textContent = String(label);
  return element;
}

function selectedItem() {
  return state.items.find((item) => item.id === state.selectedItemId) || null;
}

function selectedBuild() {
  const item = selectedItem();
  return item?.build_candidates.find((candidate) => candidate.id === state.selectedBuildId) || null;
}

function selectedClip() {
  const item = selectedItem();
  return item?.clips.find((clip) => clip.id === state.selectedClipId) || null;
}

function normalize(value) {
  return String(value || "").toLocaleLowerCase("zh-CN");
}

function searchableText(item) {
  return [
    item.bank.name,
    item.bank.package,
    item.category,
    item.preview_status,
    ...(item.reasons || []),
    ...(item.clips || []).map((clip) => clip.name),
    ...(item.build_candidates || []).flatMap((candidate) => [
      candidate.build.name,
      candidate.build.package,
      ...(candidate.reasons || [])
    ])
  ].join(" ");
}

function filteredItems() {
  const query = normalize(state.query);
  return state.items.filter((item) => {
    if (state.category !== "all" && item.category !== state.category) return false;
    if (state.status !== "all" && item.preview_status !== state.status) return false;
    return !query || normalize(searchableText(item)).includes(query);
  });
}

function statusTag(status) {
  const tag = document.createElement("span");
  tag.className = `tag ${status || "blocked"}`;
  tag.textContent = status || "blocked";
  return tag;
}

function bankButton(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `bank-item${item.id === state.selectedItemId ? " active" : ""}`;
  button.dataset.itemId = item.id;
  button.setAttribute("role", "option");
  button.setAttribute("aria-selected", item.id === state.selectedItemId ? "true" : "false");

  const name = document.createElement("span");
  name.className = "bank-name";
  name.textContent = item.bank.name;
  const packageName = document.createElement("span");
  packageName.className = "subtle";
  packageName.textContent = item.bank.package;
  const meta = document.createElement("span");
  meta.className = "bank-meta";
  meta.append(statusTag(item.preview_status));
  const category = document.createElement("span");
  category.textContent = item.category || "未分类动画";
  meta.append(category);
  const clipCount = document.createElement("span");
  clipCount.textContent = `${item.clips.length} 动作`;
  meta.append(clipCount);
  button.append(name, packageName, meta);
  return button;
}

function renderItemList() {
  state.visibleItems = filteredItems();
  const fragment = document.createDocumentFragment();
  for (const item of state.visibleItems) fragment.append(bankButton(item));
  $("itemList").replaceChildren(fragment);
  $("emptyList").hidden = state.visibleItems.length > 0;
  $("resultCount").textContent = `${state.visibleItems.length.toLocaleString()} 项`;
}

function populateFilters() {
  const categories = Array.from(new Set(state.items.map((item) => item.category || "未分类动画"))).sort();
  $("category").replaceChildren(
    option("all", "全部分类"),
    ...categories.map((value) => option(value, value))
  );
  $("statusFilter").replaceChildren(
    option("all", "全部状态"),
    option("ready", "ready"),
    option("partial", "partial"),
    option("blocked", "blocked")
  );
}

function stopPlayback() {
  state.playing = false;
  state.lastTick = 0;
  state.accumulatedMs = 0;
  if (state.animationHandle !== null) cancelAnimationFrame(state.animationHandle);
  state.animationHandle = null;
  $("playPause").textContent = "播放";
}

function updateTransport() {
  const count = state.preview?.frame_urls.length || 0;
  const enabled = count > 0;
  $("playPause").disabled = !enabled;
  $("previousFrame").disabled = !enabled;
  $("nextFrame").disabled = !enabled;
  $("timeline").disabled = !enabled;
  $("timeline").max = String(Math.max(0, count - 1));
  $("timeline").value = String(Math.min(state.currentFrame, Math.max(0, count - 1)));
  $("frameReadout").textContent = enabled ? `${state.currentFrame + 1} / ${count}` : "0 / 0";
}

function showFrame(index) {
  const urls = state.preview?.frame_urls || [];
  if (!urls.length) return;
  state.currentFrame = Math.max(0, Math.min(index, urls.length - 1));
  $("frameImage").src = urls[state.currentFrame];
  $("frameImage").hidden = false;
  $("stageMessage").hidden = true;
  updateTransport();
}

function playbackTick(timestamp) {
  if (!state.playing || !state.preview) return;
  if (!state.lastTick) state.lastTick = timestamp;
  state.accumulatedMs += timestamp - state.lastTick;
  state.lastTick = timestamp;
  const interval = 1000 / (state.preview.fps * state.speed);
  if (Number.isFinite(interval) && interval > 0 && state.accumulatedMs >= interval) {
    const steps = Math.floor(state.accumulatedMs / interval);
    state.accumulatedMs -= steps * interval;
    let next = state.currentFrame + steps;
    if (next >= state.preview.frame_urls.length) {
      if ($("loop").checked) next %= state.preview.frame_urls.length;
      else {
        showFrame(state.preview.frame_urls.length - 1);
        stopPlayback();
        return;
      }
    }
    showFrame(next);
  }
  state.animationHandle = requestAnimationFrame(playbackTick);
}

function startPlayback() {
  if (!state.preview?.frame_urls.length || state.playing) return;
  if (state.currentFrame >= state.preview.frame_urls.length - 1 && !$("loop").checked) showFrame(0);
  state.playing = true;
  state.lastTick = 0;
  state.accumulatedMs = 0;
  $("playPause").textContent = "暂停";
  state.animationHandle = requestAnimationFrame(playbackTick);
}

function setStageMessage(message) {
  $("frameImage").removeAttribute("src");
  $("frameImage").hidden = true;
  $("stageMessage").textContent = message;
  $("stageMessage").hidden = false;
}

function renderCandidates(item) {
  const fragment = document.createDocumentFragment();
  if (!item.build_candidates.length) {
    const empty = document.createElement("div");
    empty.className = "diagnostic error";
    empty.textContent = "没有 Build 候选。";
    fragment.append(empty);
  }
  for (const candidate of item.build_candidates) {
    const card = document.createElement("div");
    card.className = `candidate-card${candidate.id === state.selectedBuildId ? " selected" : ""}`;
    const row = document.createElement("div");
    row.className = "candidate-row";
    const name = document.createElement("strong");
    name.textContent = candidate.build.name;
    const coverage = document.createElement("span");
    coverage.textContent = `${Math.round(Number(candidate.symbol_coverage || 0) * 100)}%`;
    row.append(name, coverage);
    const packageName = document.createElement("div");
    packageName.className = "subtle";
    packageName.textContent = candidate.build.package;
    const evidence = document.createElement("div");
    evidence.className = "evidence";
    evidence.textContent = [
      `score ${candidate.score}`,
      `${candidate.shared_symbol_count || 0}/${candidate.required_symbol_count || 0} symbols`,
      candidate.atlas_complete ? "atlas complete" : "atlas missing",
      ...(candidate.reasons || [])
    ].join(" · ");
    const choose = document.createElement("button");
    choose.type = "button";
    choose.dataset.chooseBuild = candidate.id;
    choose.textContent = candidate.id === state.selectedBuildId ? "已选择" : "选择此 Build";
    choose.disabled = candidate.id === state.selectedBuildId;
    card.append(row, packageName, evidence, choose);
    fragment.append(card);
  }
  $("candidateDetails").replaceChildren(fragment);
}

function renderDiagnostics(item) {
  const fragment = document.createDocumentFragment();
  const warnings = item.warnings || [];
  const errors = item.errors || [];
  if (!warnings.length && !errors.length) {
    const ok = document.createElement("div");
    ok.className = "diagnostic ok";
    ok.textContent = "未记录警告或错误。";
    fragment.append(ok);
  }
  for (const value of warnings) {
    const row = document.createElement("div");
    row.className = "diagnostic warning";
    row.textContent = value;
    fragment.append(row);
  }
  for (const value of errors) {
    const row = document.createElement("div");
    row.className = "diagnostic error";
    row.textContent = value;
    fragment.append(row);
  }
  $("diagnostics").replaceChildren(fragment);
}

function materialCard(resource) {
  const card = document.createElement("div");
  card.className = "material-card";
  if (resource.image_url) {
    const image = document.createElement("img");
    image.className = "material-preview";
    image.loading = "lazy";
    image.src = resource.image_url;
    image.alt = resource.display_path;
    image.draggable = false;
    card.append(image);
  }
  const kind = document.createElement("span");
  kind.className = "material-kind";
  kind.textContent = resource.kind;
  const pathText = document.createElement("div");
  pathText.className = "material-path";
  pathText.textContent = resource.display_path;
  const availability = document.createElement("div");
  availability.className = "subtle";
  availability.textContent = resource.available ? "可用" : "文件缺失";
  const actions = document.createElement("div");
  actions.className = "material-actions";
  const open = document.createElement("button");
  open.type = "button";
  open.dataset.openResource = resource.id;
  open.textContent = "Explorer 定位";
  open.disabled = !resource.available;
  const copy = document.createElement("button");
  copy.type = "button";
  copy.dataset.copyResource = resource.id;
  copy.textContent = "复制路径";
  copy.disabled = !resource.available;
  actions.append(open, copy);
  card.append(kind, pathText, availability, actions);
  return card;
}

function renderMaterials(item) {
  const resources = [item.bank.source];
  const build = selectedBuild();
  if (build) resources.push(build.build.source, ...build.atlases);
  const clip = selectedClip();
  if (clip) resources.push(clip.source);
  const seen = new Set();
  const fragment = document.createDocumentFragment();
  for (const resource of resources.filter(Boolean)) {
    if (seen.has(resource.id)) continue;
    seen.add(resource.id);
    fragment.append(materialCard(resource));
  }
  $("materialList").replaceChildren(fragment);
}

function renderSelectionDetails() {
  const item = selectedItem();
  if (!item) return;
  $("selectedCategory").textContent = `${item.category} · confidence ${Number(item.confidence || 0).toFixed(2)}`;
  $("selectedBank").textContent = item.bank.name;
  $("selectedPackage").textContent = `package: ${item.bank.package}`;
  $("previewStatus").textContent = item.preview_status;
  $("previewStatus").className = `status-pill ${item.preview_status}`;
  renderCandidates(item);
  renderDiagnostics(item);
  renderMaterials(item);
}

function populateSelectionControls(item) {
  const buildOptions = [];
  if (!item.default_build_id) buildOptions.push(option("", "选择 Build（存在歧义或不完整）"));
  for (const candidate of item.build_candidates) {
    const coverage = Math.round(Number(candidate.symbol_coverage || 0) * 100);
    buildOptions.push(option(candidate.id, `${candidate.build.name} · ${coverage}% · ${candidate.build.package}`));
  }
  $("buildSelect").replaceChildren(...buildOptions);
  $("buildSelect").disabled = !item.build_candidates.length;
  state.selectedBuildId = item.default_build_id || "";
  $("buildSelect").value = state.selectedBuildId;

  $("clipSelect").replaceChildren(
    ...item.clips.map((clip) => option(clip.id, `${clip.name} · ${clip.frame_count}f @ ${clip.fps}fps`))
  );
  $("clipSelect").disabled = !item.clips.length;
  state.selectedClipId = item.clips[0]?.id || "";
  $("clipSelect").value = state.selectedClipId;
}

async function preparePreview() {
  stopPlayback();
  state.preview = null;
  state.currentFrame = 0;
  updateTransport();
  $("previewError").textContent = "";
  const item = selectedItem();
  const build = selectedBuild();
  const clip = selectedClip();
  renderSelectionDetails();
  if (!item || !build || !clip) {
    setStageMessage(item && !build ? "请选择一个 Build；Viewer 不会替你消解歧义。" : "没有可预览的动作。 ");
    return;
  }
  if (!build.atlas_complete) {
    setStageMessage("所选 Build 的 atlas 不完整，无法生成真实帧。");
    return;
  }
  const token = ++state.previewToken;
  setStageMessage("正在调用动画 exporter 生成真实 PNG 序列…");
  try {
    const preview = await window.animationBridge.preparePreview(item.id, build.id, clip.id);
    if (token !== state.previewToken) return;
    if (!preview.frame_urls?.length) throw new Error("exporter 没有返回帧");
    state.preview = preview;
    state.currentFrame = 0;
    for (const url of preview.frame_urls.slice(1, 5)) {
      const image = new Image();
      image.src = url;
    }
    showFrame(0);
  } catch (error) {
    if (token !== state.previewToken) return;
    state.preview = null;
    setStageMessage("真实预览生成失败");
    $("previewError").textContent = error.message || String(error);
    updateTransport();
  }
}

function selectItem(itemId) {
  const item = state.items.find((value) => value.id === itemId);
  if (!item) return;
  state.previewToken += 1;
  state.selectedItemId = item.id;
  populateSelectionControls(item);
  renderItemList();
  renderSelectionDetails();
  preparePreview();
}

async function exportCurrent(format) {
  const item = selectedItem();
  const build = selectedBuild();
  const clip = selectedClip();
  if (!item || !build || !clip) {
    $("exportResult").textContent = "请先选择 Bank、Build 和动作。";
    return;
  }
  const buttons = document.querySelectorAll("[data-export-format]");
  buttons.forEach((button) => { button.disabled = true; });
  $("exportResult").textContent = `正在导出 ${format}…`;
  try {
    const result = await window.animationBridge.exportAnimation(
      item.id,
      build.id,
      clip.id,
      format,
      state.currentFrame
    );
    const fragment = document.createDocumentFragment();
    const status = document.createElement("span");
    status.textContent = `${result.status} · ${result.format} · `;
    fragment.append(status);
    result.outputs.forEach((output, index) => {
      if (index) fragment.append(document.createTextNode("，"));
      const link = document.createElement("button");
      link.type = "button";
      link.className = "export-link";
      link.dataset.openExport = output.id;
      link.textContent = output.display_path;
      fragment.append(link);
    });
    $("exportResult").replaceChildren(fragment);
  } catch (error) {
    $("exportResult").textContent = `导出失败：${error.message || String(error)}`;
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function init() {
  state.manifest = await window.animationBridge.loadManifest();
  state.items = state.manifest.items || [];
  $("totalCount").textContent = state.items.length.toLocaleString();
  populateFilters();
  renderItemList();
  if (state.items.length) selectItem(state.items[0].id);
}

$("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderItemList();
});
$("category").addEventListener("change", (event) => {
  state.category = event.target.value;
  renderItemList();
});
$("statusFilter").addEventListener("change", (event) => {
  state.status = event.target.value;
  renderItemList();
});
$("itemList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-item-id]");
  if (button) selectItem(button.dataset.itemId);
});
$("buildSelect").addEventListener("change", (event) => {
  state.selectedBuildId = event.target.value;
  renderSelectionDetails();
  preparePreview();
});
$("clipSelect").addEventListener("change", (event) => {
  state.selectedClipId = event.target.value;
  renderSelectionDetails();
  preparePreview();
});
$("playPause").addEventListener("click", () => {
  if (state.playing) stopPlayback(); else startPlayback();
});
$("previousFrame").addEventListener("click", () => {
  stopPlayback();
  const count = state.preview?.frame_urls.length || 0;
  if (count) showFrame((state.currentFrame - 1 + count) % count);
});
$("nextFrame").addEventListener("click", () => {
  stopPlayback();
  const count = state.preview?.frame_urls.length || 0;
  if (count) showFrame((state.currentFrame + 1) % count);
});
$("timeline").addEventListener("input", (event) => {
  stopPlayback();
  showFrame(Number(event.target.value));
});
$("speed").addEventListener("change", (event) => {
  state.speed = Number(event.target.value) || 1;
  state.lastTick = 0;
  state.accumulatedMs = 0;
});
$("candidateDetails").addEventListener("click", (event) => {
  const button = event.target.closest("[data-choose-build]");
  if (!button) return;
  state.selectedBuildId = button.dataset.chooseBuild;
  $("buildSelect").value = state.selectedBuildId;
  renderSelectionDetails();
  preparePreview();
});
$("materialList").addEventListener("click", (event) => {
  const open = event.target.closest("[data-open-resource]");
  const copy = event.target.closest("[data-copy-resource]");
  if (open) window.animationBridge.openAsset(open.dataset.openResource);
  if (copy) window.animationBridge.copyAssetPath(copy.dataset.copyResource);
});
document.querySelectorAll("[data-export-format]").forEach((button) => {
  button.addEventListener("click", () => exportCurrent(button.dataset.exportFormat));
});
$("exportResult").addEventListener("click", (event) => {
  const target = event.target.closest("[data-open-export]");
  if (target) window.animationBridge.openExport(target.dataset.openExport);
});

init().catch((error) => {
  $("emptyList").hidden = false;
  $("emptyList").textContent = `Viewer 初始化失败：${error.message || String(error)}`;
  setStageMessage("Manifest 加载失败");
});

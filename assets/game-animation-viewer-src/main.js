"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const { pathToFileURL } = require("url");

const isTestMode = process.env.GA_GAME_ANIMATION_VIEWER_TEST === "1";
let app;
let BrowserWindow;
let ipcMain;
let protocol;
let shell;
let clipboard;
let session;
let dialog;

if (!isTestMode) {
  ({ app, BrowserWindow, ipcMain, protocol, shell, clipboard, session, dialog } = require("electron"));
}

const EXPORT_FORMATS = new Set([
  "current-frame-png",
  "png-sequence",
  "apng",
  "gif",
  "source-bundle"
]);
const IMAGE_EXTENSIONS = new Set([".png", ".apng", ".gif"]);
const MAX_RENDERER_OUTPUT_BYTES = 2 * 1024 * 1024;

let mainWindow = null;
let runtime = null;

function argValues(name, argv = process.argv) {
  const values = [];
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === name && index + 1 < argv.length) values.push(argv[index + 1]);
  }
  return values;
}

function argValue(name, argv = process.argv) {
  const values = argValues(name, argv);
  return values.length ? values[0] : "";
}

function hasArg(name, argv = process.argv) {
  return argv.includes(name);
}

function lexicalAbsolute(value) {
  return path.resolve(String(value || ""));
}

function canonicalPath(value) {
  const resolved = lexicalAbsolute(value);
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

function normalizeForCompare(value) {
  const resolved = canonicalPath(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function isInsideRoot(localPath, root) {
  const candidate = normalizeForCompare(localPath);
  const allowed = normalizeForCompare(root);
  const prefix = allowed.endsWith(path.sep) ? allowed : `${allowed}${path.sep}`;
  return candidate === allowed || candidate.startsWith(prefix);
}

function isInsideAny(localPath, roots) {
  return roots.some((root) => isInsideRoot(localPath, root));
}

function rootsOverlap(left, right) {
  return isInsideRoot(left, right) || isInsideRoot(right, left);
}

function assertNoDuplicateRoots(roots, label) {
  const seen = new Set();
  for (const root of roots) {
    const normalized = normalizeForCompare(root);
    if (seen.has(normalized)) throw new Error(`${label} contains a duplicate: ${root}`);
    seen.add(normalized);
  }
}

function sameRootSet(left, right) {
  const a = left.map(normalizeForCompare).sort();
  const b = right.map(normalizeForCompare).sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function isLinkOrReparse(localPath) {
  if (!fs.existsSync(localPath)) return false;
  const value = fs.lstatSync(localPath);
  return value.isSymbolicLink();
}

function rejectLinkChain(candidate, boundary, label) {
  const lexicalBoundary = lexicalAbsolute(boundary);
  const lexicalCandidate = lexicalAbsolute(candidate);
  const relative = path.relative(lexicalBoundary, lexicalCandidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`${label} escapes allowed root: ${lexicalCandidate}`);
  }
  let current = lexicalBoundary;
  if (fs.existsSync(current) && isLinkOrReparse(current)) {
    throw new Error(`${label} root is a symlink/reparse point: ${current}`);
  }
  for (const part of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.existsSync(current) && isLinkOrReparse(current)) {
      throw new Error(`${label} uses a symlink/reparse point: ${current}`);
    }
  }
}

function ensureDirectorySafe(candidate, studyRoot, label) {
  const root = canonicalPath(studyRoot);
  const lexical = lexicalAbsolute(candidate);
  if (!isInsideRoot(lexical, root)) throw new Error(`${label} must be inside study-root`);
  const relative = path.relative(root, lexical);
  let current = root;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    if (fs.existsSync(current)) {
      if (isLinkOrReparse(current) || !fs.statSync(current).isDirectory()) {
        throw new Error(`${label} has an unsafe directory component: ${current}`);
      }
    } else {
      fs.mkdirSync(current);
    }
  }
  return canonicalPath(lexical);
}

function readJsonFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  return JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);
}

function safeManifestRelativePath(raw, label) {
  if (typeof raw !== "string" || raw.trim() === "") {
    throw new Error(`${label} must be a non-empty study-root relative POSIX path`);
  }
  if (raw.includes("\\") || raw.startsWith("/") || /^[A-Za-z]:/.test(raw)) {
    throw new Error(`${label} must be a study-root relative POSIX path`);
  }
  const parts = raw.split("/");
  if (parts.some((part) => part === ".." || part === "")) {
    throw new Error(`${label} contains traversal or empty path components`);
  }
  return parts;
}

function manifestAssetRoots(manifest, studyRoot) {
  const roots = manifest && manifest.source_roots;
  if (!roots || typeof roots !== "object" || Array.isArray(roots)) {
    throw new Error("manifest source_roots must be an object");
  }
  const entries = Object.entries(roots);
  if (!entries.length) throw new Error("manifest source_roots must not be empty");
  return entries.map(([key, value]) => {
    const parts = safeManifestRelativePath(value, `manifest source_roots.${key}`);
    return canonicalPath(path.join(studyRoot, ...parts));
  });
}

function stableResourceId(kind, value) {
  return `${kind}-${crypto.createHash("sha256").update(`${kind}\0${value}`).digest("hex").slice(0, 24)}`;
}

function mimeFor(localPath) {
  const extension = path.extname(localPath).toLowerCase();
  if (extension === ".gif") return "image/gif";
  return "image/png";
}

function resolveManifestAsset(relativePath, context, kind) {
  const parts = safeManifestRelativePath(relativePath, `manifest ${kind} path`);
  const lexical = path.join(context.studyRoot, ...parts);
  if (!isInsideAny(lexical, context.assetRoots)) {
    throw new Error(`manifest ${kind} path escapes CLI --asset-root set: ${relativePath}`);
  }
  const ownerRoot = context.assetRoots.find((root) => isInsideRoot(lexical, root));
  rejectLinkChain(lexical, ownerRoot, `manifest ${kind}`);
  const exists = fs.existsSync(lexical);
  if (exists && !fs.statSync(lexical).isFile()) {
    throw new Error(`manifest ${kind} path is not a file: ${relativePath}`);
  }
  const localPath = canonicalPath(lexical);
  if (!isInsideRoot(localPath, ownerRoot)) {
    throw new Error(`manifest ${kind} realpath escapes CLI --asset-root: ${relativePath}`);
  }
  const id = stableResourceId("asset", relativePath);
  const existing = context.resources.get(id);
  if (existing && normalizeForCompare(existing.localPath) !== normalizeForCompare(localPath)) {
    throw new Error(`resource id collision for manifest path: ${relativePath}`);
  }
  context.resources.set(id, {
    id,
    kind,
    localPath,
    displayPath: relativePath,
    exists,
    protocolReadable: exists && IMAGE_EXTENSIONS.has(path.extname(localPath).toLowerCase())
  });
  return id;
}

function validateManifest(manifest, context) {
  if (!manifest || manifest.schema_version !== 1) {
    throw new Error("manifest schema_version must be 1");
  }
  if (!Array.isArray(manifest.items)) throw new Error("manifest items must be an array");
  const itemIds = new Set();
  const itemsById = new Map();
  for (const [itemIndex, item] of manifest.items.entries()) {
    if (!item || typeof item !== "object" || typeof item.id !== "string" || !item.id) {
      throw new Error(`manifest items[${itemIndex}] requires a non-empty id`);
    }
    if (itemIds.has(item.id)) throw new Error(`duplicate manifest item id: ${item.id}`);
    itemIds.add(item.id);
    if (!item.bank || typeof item.bank.name !== "string" || typeof item.bank.package !== "string") {
      throw new Error(`manifest item ${item.id} requires bank.name and bank.package`);
    }
    const bankResourceId = resolveManifestAsset(item.bank.source_path, context, "anim_xml");
    const clipIds = new Set();
    const clipsById = new Map();
    for (const clip of item.clips || []) {
      if (!clip.id || clipIds.has(clip.id)) throw new Error(`invalid or duplicate clip id in ${item.id}`);
      clipIds.add(clip.id);
      const sourceResourceId = resolveManifestAsset(clip.source_path, context, "anim_xml");
      clipsById.set(clip.id, { ...clip, sourceResourceId });
    }
    const buildIds = new Set();
    const buildsById = new Map();
    for (const candidate of item.build_candidates || []) {
      if (!candidate.id || buildIds.has(candidate.id)) {
        throw new Error(`invalid or duplicate build candidate id in ${item.id}`);
      }
      buildIds.add(candidate.id);
      const buildResourceId = resolveManifestAsset(
        candidate.build && candidate.build.source_path,
        context,
        "build_xml"
      );
      const atlasResourceIds = (candidate.atlases || []).map((atlasPath) =>
        resolveManifestAsset(atlasPath, context, "atlas_png")
      );
      buildsById.set(candidate.id, { ...candidate, buildResourceId, atlasResourceIds });
    }
    const sourceAssetIds = (item.source_assets || []).map((asset) =>
      resolveManifestAsset(asset.path, context, asset.kind || "source")
    );
    if (item.default_build_id !== null && item.default_build_id !== undefined && !buildIds.has(item.default_build_id)) {
      throw new Error(`default_build_id is not a candidate for ${item.id}`);
    }
    itemsById.set(item.id, {
      ...item,
      bankResourceId,
      sourceAssetIds,
      clipsById,
      buildsById
    });
  }
  return itemsById;
}

function requireCliValue(argv, name) {
  const value = argValue(name, argv);
  if (!value) throw new Error(`Missing required ${name} argument`);
  return value;
}

function resolveRuntimeFromArgs(argv = process.argv) {
  const studyRootArg = requireCliValue(argv, "--study-root");
  const manifestArg = requireCliValue(argv, "--manifest");
  const assetRootArgs = argValues("--asset-root", argv);
  if (!assetRootArgs.length) throw new Error("Missing required --asset-root argument");
  const previewCacheArg = requireCliValue(argv, "--preview-cache-dir");
  const exportDirArg = requireCliValue(argv, "--export-dir");
  const pythonExeArg = requireCliValue(argv, "--python-exe");
  const rendererScriptArg = requireCliValue(argv, "--renderer-script");

  const studyRootLexical = lexicalAbsolute(studyRootArg);
  if (!fs.existsSync(studyRootLexical) || !fs.statSync(studyRootLexical).isDirectory()) {
    throw new Error(`study-root is not a directory: ${studyRootLexical}`);
  }
  if (isLinkOrReparse(studyRootLexical)) throw new Error("study-root cannot be a symlink/reparse point");
  const studyRoot = canonicalPath(studyRootLexical);

  const manifestLexical = lexicalAbsolute(manifestArg);
  if (!isInsideRoot(manifestLexical, studyRoot)) throw new Error("Manifest must be inside study-root");
  rejectLinkChain(manifestLexical, studyRoot, "manifest");
  if (!fs.existsSync(manifestLexical) || !fs.statSync(manifestLexical).isFile()) {
    throw new Error(`Cannot find manifest: ${manifestLexical}`);
  }
  const manifestPath = canonicalPath(manifestLexical);

  const assetRoots = assetRootArgs.map((value, index) => {
    const lexical = lexicalAbsolute(value);
    if (!isInsideRoot(lexical, studyRoot)) throw new Error(`--asset-root[${index}] must be inside study-root`);
    rejectLinkChain(lexical, studyRoot, `--asset-root[${index}]`);
    if (!fs.existsSync(lexical) || !fs.statSync(lexical).isDirectory()) {
      throw new Error(`--asset-root[${index}] is not a directory: ${lexical}`);
    }
    return canonicalPath(lexical);
  });
  assertNoDuplicateRoots(assetRoots, "CLI --asset-root set");

  const manifest = readJsonFile(manifestPath);
  const declaredAssetRoots = manifestAssetRoots(manifest, studyRoot);
  assertNoDuplicateRoots(declaredAssetRoots, "manifest source_roots");
  if (!sameRootSet(assetRoots, declaredAssetRoots)) {
    throw new Error("manifest asset roots do not exactly match CLI --asset-root values");
  }

  const previewLexical = lexicalAbsolute(previewCacheArg);
  const exportLexical = lexicalAbsolute(exportDirArg);
  for (const [label, target] of [["preview-cache-dir", previewLexical], ["export-dir", exportLexical]]) {
    if (!isInsideRoot(target, studyRoot)) throw new Error(`${label} must be inside study-root`);
    if (assetRoots.some((assetRoot) => rootsOverlap(target, assetRoot))) {
      throw new Error(`${label} must not overlap read-only asset roots`);
    }
  }
  if (rootsOverlap(previewLexical, exportLexical)) {
    throw new Error("preview-cache-dir and export-dir must not overlap");
  }
  const previewCacheDir = ensureDirectorySafe(previewLexical, studyRoot, "preview-cache-dir");
  const exportDir = ensureDirectorySafe(exportLexical, studyRoot, "export-dir");

  const pythonExe = canonicalPath(pythonExeArg);
  if (!fs.existsSync(pythonExe) || !fs.statSync(pythonExe).isFile() || isLinkOrReparse(pythonExe)) {
    throw new Error(`python-exe must be a real file: ${pythonExe}`);
  }
  const rendererScript = canonicalPath(rendererScriptArg);
  if (!fs.existsSync(rendererScript) || !fs.statSync(rendererScript).isFile() || isLinkOrReparse(rendererScript)) {
    throw new Error(`renderer-script must be a real file: ${rendererScript}`);
  }

  const context = { studyRoot, assetRoots, resources: new Map() };
  const itemsById = validateManifest(manifest, context);
  const selfTest = hasArg("--self-test", argv);
  const selfTestOutputArg = argValue("--self-test-output", argv);
  if (selfTest && !selfTestOutputArg) throw new Error("Missing required --self-test-output argument");
  let selfTestOutput = null;
  if (selfTestOutputArg) {
    const lexical = lexicalAbsolute(selfTestOutputArg);
    if (!isInsideRoot(lexical, studyRoot)) throw new Error("self-test-output must be inside study-root");
    if (assetRoots.some((assetRoot) => isInsideRoot(lexical, assetRoot))) {
      throw new Error("self-test-output must not be inside read-only asset roots");
    }
    rejectLinkChain(lexical, studyRoot, "self-test-output");
    selfTestOutput = lexical;
  }

  return {
    studyRoot,
    manifestPath,
    manifest,
    assetRoots,
    previewCacheDir,
    exportDir,
    pythonExe,
    rendererScript,
    selfTest,
    selfTestOutput,
    resources: context.resources,
    itemsById,
    exports: new Map()
  };
}

function publicResource(resourceId) {
  const resource = runtime.resources.get(resourceId);
  if (!resource) return null;
  return {
    id: resource.id,
    kind: resource.kind,
    display_path: resource.displayPath,
    available: resource.exists,
    image_url: resource.protocolReadable
      ? `ga-animation://resource/${encodeURIComponent(resource.id)}`
      : null
  };
}

function publicManifest() {
  const items = [];
  for (const item of runtime.itemsById.values()) {
    items.push({
      id: item.id,
      adapter: item.adapter,
      category: item.category,
      confidence: item.confidence,
      reasons: item.reasons || [],
      bank: {
        name: item.bank.name,
        package: item.bank.package,
        source: publicResource(item.bankResourceId)
      },
      build_candidates: Array.from(item.buildsById.values()).map((candidate) => ({
        id: candidate.id,
        build: {
          name: candidate.build.name,
          package: candidate.build.package,
          source: publicResource(candidate.buildResourceId)
        },
        score: candidate.score,
        symbol_coverage: candidate.symbol_coverage,
        shared_symbol_count: candidate.shared_symbol_count,
        required_symbol_count: candidate.required_symbol_count,
        atlas_complete: candidate.atlas_complete,
        reasons: candidate.reasons || [],
        atlases: candidate.atlasResourceIds.map(publicResource).filter(Boolean)
      })),
      default_build_id: item.default_build_id || null,
      clips: Array.from(item.clipsById.values()).map((clip) => ({
        id: clip.id,
        name: clip.name,
        fps: clip.fps,
        frame_count: clip.frame_count,
        declared_frame_count: clip.declared_frame_count,
        symbols: clip.symbols || [],
        source: publicResource(clip.sourceResourceId)
      })),
      source_assets: item.sourceAssetIds.map(publicResource).filter(Boolean),
      capabilities: item.capabilities || {},
      preview_status: item.preview_status,
      warnings: item.warnings || [],
      errors: item.errors || []
    });
  }
  return {
    schema_version: runtime.manifest.schema_version,
    adapter: runtime.manifest.adapter,
    item_count: items.length,
    items
  };
}

function rendererUrl() {
  return pathToFileURL(path.join(__dirname, "renderer", "index.html")).href;
}

function isTrustedIpcEvent(event) {
  if (!mainWindow || !event || event.sender !== mainWindow.webContents) return false;
  const frameUrl = event.senderFrame && event.senderFrame.url ? event.senderFrame.url : "";
  const senderUrl = event.sender && typeof event.sender.getURL === "function" ? event.sender.getURL() : "";
  return (frameUrl || senderUrl) === rendererUrl();
}

function requireTrustedIpcEvent(event) {
  if (!isTrustedIpcEvent(event)) throw new Error("Untrusted IPC sender");
}

function validateSelection(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("Animation selection payload must be an object");
  }
  const item = runtime.itemsById.get(payload.itemId);
  if (!item) throw new Error("Unknown animation item id");
  const build = item.buildsById.get(payload.buildId);
  if (!build) throw new Error("Unknown build candidate id for item");
  const clip = item.clipsById.get(payload.clipId);
  if (!clip) throw new Error("Unknown clip id for item");
  return { item, build, clip };
}

function runRenderer(argumentsList) {
  return new Promise((resolve, reject) => {
    const child = spawn(runtime.pythonExe, [runtime.rendererScript, ...argumentsList], {
      cwd: runtime.studyRoot,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let overflow = false;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (Buffer.byteLength(stdout, "utf8") > MAX_RENDERER_OUTPUT_BYTES) {
        overflow = true;
        child.kill();
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
      if (Buffer.byteLength(stderr, "utf8") > MAX_RENDERER_OUTPUT_BYTES) {
        overflow = true;
        child.kill();
      }
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (overflow) return reject(new Error("Renderer output exceeded the safety limit"));
      if (code !== 0) return reject(new Error(stderr.trim() || `Renderer exited with code ${code}`));
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      if (lines.length !== 1) return reject(new Error("Renderer must return exactly one JSON line"));
      try {
        resolve(JSON.parse(lines[0]));
      } catch {
        reject(new Error("Renderer returned invalid JSON"));
      }
    });
  });
}

function validateRendererOutputs(result, outputRoot, kind) {
  if (!result || !Array.isArray(result.outputs) || !result.outputs.length) {
    throw new Error("Renderer JSON does not contain outputs");
  }
  return result.outputs.map((rawPath) => {
    if (typeof rawPath !== "string" || !rawPath) throw new Error("Renderer output path is invalid");
    const lexical = lexicalAbsolute(rawPath);
    if (!isInsideRoot(lexical, outputRoot)) throw new Error("Renderer output escapes authorized output root");
    rejectLinkChain(lexical, outputRoot, "renderer output");
    if (!fs.existsSync(lexical) || !fs.statSync(lexical).isFile()) {
      throw new Error(`Renderer output file is missing: ${lexical}`);
    }
    const localPath = canonicalPath(lexical);
    if (!isInsideRoot(localPath, outputRoot)) throw new Error("Renderer output realpath escapes authorized root");
    const id = stableResourceId(kind, localPath);
    const descriptor = {
      id,
      kind,
      localPath,
      displayPath: path.relative(runtime.studyRoot, localPath).split(path.sep).join("/"),
      exists: true,
      protocolReadable: kind === "preview" && path.extname(localPath).toLowerCase() === ".png"
    };
    runtime.resources.set(id, descriptor);
    if (kind === "export") runtime.exports.set(id, descriptor);
    return descriptor;
  });
}

function selectionOutputRoot(baseRoot, itemId, buildId, clipId) {
  const directoryName = stableResourceId("selection", `${itemId}\0${buildId}\0${clipId}`);
  return ensureDirectorySafe(path.join(baseRoot, directoryName), runtime.studyRoot, "selection output root");
}

async function invokeExport(payload, format, outputRoot) {
  const { item, build, clip } = validateSelection(payload);
  if (!EXPORT_FORMATS.has(format)) throw new Error("Unsupported animation export format");
  const frame = Number.isInteger(payload.frame) && payload.frame >= 0 ? payload.frame : 0;
  const args = [
    "--study-root", runtime.studyRoot,
    "--manifest", runtime.manifestPath,
    "--item", item.id,
    "--build", build.id,
    "--clip", clip.id,
    "--output-root", outputRoot,
    "--format", format,
    "--frame", String(frame)
  ];
  const result = await runRenderer(args);
  const isPreview = format === "png-sequence" && isInsideRoot(outputRoot, runtime.previewCacheDir);
  return { result, descriptors: validateRendererOutputs(result, outputRoot, isPreview ? "preview" : "export") };
}

function registerProtocols() {
  protocol.handle("ga-animation", async (request) => {
    const url = new URL(request.url);
    if (url.hostname !== "resource") return new Response("unknown resource kind", { status: 404 });
    const id = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    const resource = runtime.resources.get(id);
    if (!resource || !resource.protocolReadable || !resource.exists) {
      return new Response("missing resource", { status: 404 });
    }
    const allowedRoots = resource.kind === "preview" ? [runtime.previewCacheDir] : runtime.assetRoots;
    if (!isInsideAny(resource.localPath, allowedRoots)) return new Response("forbidden", { status: 403 });
    return new Response(fs.readFileSync(resource.localPath), {
      headers: { "content-type": mimeFor(resource.localPath), "cache-control": "no-store" }
    });
  });
}

function installOfflineGuards() {
  session.defaultSession.webRequest.onBeforeRequest(
    { urls: ["http://*/*", "https://*/*", "ws://*/*", "wss://*/*"] },
    (_details, callback) => callback({ cancel: true })
  );
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
}

function registerIpcHandlers() {
  ipcMain.handle("animation:load-manifest", (event) => {
    requireTrustedIpcEvent(event);
    return publicManifest();
  });
  ipcMain.handle("animation:prepare-preview", async (event, payload) => {
    requireTrustedIpcEvent(event);
    const { item, build, clip } = validateSelection(payload);
    if (!build.atlas_complete) throw new Error("Selected build has incomplete atlas assets");
    const outputRoot = selectionOutputRoot(runtime.previewCacheDir, item.id, build.id, clip.id);
    const exported = await invokeExport({ ...payload, frame: 0 }, "png-sequence", outputRoot);
    return {
      item_id: item.id,
      build_id: build.id,
      clip_id: clip.id,
      fps: Number(exported.result.fps || clip.fps),
      frame_count: exported.descriptors.length,
      frame_urls: exported.descriptors.map((value) => `ga-animation://resource/${encodeURIComponent(value.id)}`)
    };
  });
  ipcMain.handle("animation:export", async (event, payload) => {
    requireTrustedIpcEvent(event);
    if (!payload || !EXPORT_FORMATS.has(payload.format)) throw new Error("Unsupported animation export format");
    const selection = validateSelection(payload);
    const outputRoot = selectionOutputRoot(
      runtime.exportDir,
      selection.item.id,
      selection.build.id,
      selection.clip.id
    );
    const exported = await invokeExport(payload, payload.format, outputRoot);
    return {
      status: exported.result.status,
      format: payload.format,
      outputs: exported.descriptors.map((value) => ({
        id: value.id,
        display_path: value.displayPath
      }))
    };
  });
  ipcMain.handle("animation:open-asset", (event, resourceId) => {
    requireTrustedIpcEvent(event);
    const resource = runtime.resources.get(resourceId);
    if (!resource || resource.kind === "preview" || resource.kind === "export" || !resource.exists) {
      return { ok: false };
    }
    if (!isInsideAny(resource.localPath, runtime.assetRoots)) return { ok: false };
    shell.showItemInFolder(resource.localPath);
    return { ok: true };
  });
  ipcMain.handle("animation:copy-asset-path", (event, resourceId) => {
    requireTrustedIpcEvent(event);
    const resource = runtime.resources.get(resourceId);
    if (!resource || resource.kind === "preview" || resource.kind === "export" || !resource.exists) {
      return { ok: false };
    }
    if (!isInsideAny(resource.localPath, runtime.assetRoots)) return { ok: false };
    clipboard.writeText(resource.localPath);
    return { ok: true };
  });
  ipcMain.handle("animation:open-export", (event, exportId) => {
    requireTrustedIpcEvent(event);
    const descriptor = runtime.exports.get(exportId);
    if (!descriptor || !isInsideRoot(descriptor.localPath, runtime.exportDir)) return { ok: false };
    shell.showItemInFolder(descriptor.localPath);
    return { ok: true };
  });
}

function focusMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  return true;
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1580,
    height: 980,
    minWidth: 1080,
    minHeight: 720,
    title: "GameAnimationViewer",
    backgroundColor: "#101416",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js")
    }
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url !== rendererUrl()) event.preventDefault();
  });
  mainWindow.webContents.on("will-attach-webview", (event) => event.preventDefault());
  mainWindow.once("ready-to-show", focusMainWindow);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  await mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

function selfTestSummary(activeRuntime) {
  return {
    ok: true,
    schema_version: activeRuntime.manifest.schema_version,
    item_count: activeRuntime.itemsById.size,
    asset_root_count: activeRuntime.assetRoots.length,
    offline: true,
    renderer_script: path.basename(activeRuntime.rendererScript)
  };
}

function writeSelfTestOutput(activeRuntime) {
  const summary = selfTestSummary(activeRuntime);
  const destination = activeRuntime.selfTestOutput;
  const parent = ensureDirectorySafe(path.dirname(destination), activeRuntime.studyRoot, "self-test-output parent");
  rejectLinkChain(destination, parent, "self-test-output");
  const payload = `${JSON.stringify(summary, null, 2)}${os.EOL}`;
  if (fs.existsSync(destination) && fs.readFileSync(destination, "utf8") !== payload) {
    throw new Error("self-test-output exists with different content");
  }
  if (!fs.existsSync(destination)) fs.writeFileSync(destination, payload, { encoding: "utf8", flag: "wx" });
  return summary;
}

function setRuntimeForTest(nextRuntime) {
  runtime = nextRuntime;
}

function setMainWindowForTest(nextWindow) {
  mainWindow = nextWindow;
}

function reportStartupError(error) {
  const message = error && error.message ? error.message : String(error);
  console.error(message);
  try {
    dialog.showErrorBox(
      "GameAnimationViewer 启动失败",
      `${message}\n\n请重新运行“打开动画预览.bat”并查看启动窗口中的日志路径。`
    );
  } finally {
    app.exit(1);
  }
}

function instanceScopeName(argv = process.argv) {
  const studyRootArg = argValue("--study-root", argv) || "missing-study-root";
  const identity = path.resolve(studyRootArg).toLowerCase();
  const digest = crypto.createHash("sha256").update(identity, "utf8").digest("hex").slice(0, 16);
  return `study-${digest}`;
}

function prepareInstanceScope(argv = process.argv) {
  const defaultUserData = app.getPath("userData");
  const scopedUserData = path.join(defaultUserData, instanceScopeName(argv));
  fs.mkdirSync(scopedUserData, { recursive: true });
  app.setPath("userData", scopedUserData);
}

function startApp() {
  try {
    prepareInstanceScope(process.argv);
  } catch (error) {
    app.whenReady().then(() => reportStartupError(error)).catch(reportStartupError);
    return;
  }
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on("second-instance", () => {
    focusMainWindow();
  });
  app.whenReady().then(async () => {
    try {
      runtime = resolveRuntimeFromArgs(process.argv);
      if (runtime.selfTest) {
        const summary = writeSelfTestOutput(runtime);
        console.log(JSON.stringify(summary));
        app.quit();
        return;
      }
      registerIpcHandlers();
      registerProtocols();
      installOfflineGuards();
      await createWindow();
    } catch (error) {
      reportStartupError(error);
    }
  }).catch(reportStartupError);
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}

if (isTestMode) {
  module.exports = {
    EXPORT_FORMATS,
    argValues,
    argValue,
    canonicalPath,
    isInsideRoot,
    sameRootSet,
    safeManifestRelativePath,
    resolveRuntimeFromArgs,
    validateSelection,
    validateRendererOutputs,
    selectionOutputRoot,
    publicManifest,
    rendererUrl,
    isTrustedIpcEvent,
    setRuntimeForTest,
    setMainWindowForTest,
    focusMainWindow,
    instanceScopeName,
    selfTestSummary,
    writeSelfTestOutput
  };
} else {
  startApp();
}

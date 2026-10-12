const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const isTestMode = process.env.GA_IMAGE_GALLERY_TEST === "1";
let app;
let BrowserWindow;
let ipcMain;
let protocol;
let shell;
let clipboard;
let session;

if (!isTestMode) {
  ({ app, BrowserWindow, ipcMain, protocol, shell, clipboard, session } = require("electron"));
}

let mainWindow = null;
let runtime = null;

function argValues(name) {
  const values = [];
  for (let index = 0; index < process.argv.length; index += 1) {
    if (process.argv[index] === name && index + 1 < process.argv.length) {
      values.push(process.argv[index + 1]);
    }
  }
  return values;
}

function argValue(name) {
  const values = argValues(name);
  return values.length ? values[0] : "";
}

function canonicalPath(value) {
  const resolved = path.resolve(String(value || ""));
  try {
    return fs.realpathSync.native(resolved);
  } catch {
    return resolved;
  }
}

function readJsonFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  return JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);
}

function normalizeForCompare(value) {
  const resolved = canonicalPath(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function validateCliImageRootArgs(cliImageRootArgs) {
  for (const [index, root] of cliImageRootArgs.entries()) {
    if (typeof root !== "string" || root.trim() === "") {
      throw new Error(`--images-root[${index}] must be a non-empty string`);
    }
  }
}

function manifestSourceRootPaths(manifest) {
  if (!Array.isArray(manifest.source_roots) || manifest.source_roots.length === 0) {
    throw new Error("manifest source_roots must be a non-empty array");
  }
  return manifest.source_roots.map((root, index) => {
    if (!root || typeof root !== "object" || Array.isArray(root)) {
      throw new Error(`manifest source_roots[${index}] must be an object with a path`);
    }
    if (typeof root.path !== "string" || root.path.trim() === "") {
      throw new Error(`manifest source_roots[${index}].path must be a non-empty string`);
    }
    return root.path;
  });
}

function assertNoDuplicateRoots(roots, message) {
  const seen = new Set();
  for (const root of roots) {
    const normalized = normalizeForCompare(root);
    if (seen.has(normalized)) {
      throw new Error(`${message}: ${root}`);
    }
    seen.add(normalized);
  }
}

function sameRootSet(left, right) {
  const leftRoots = left.map(normalizeForCompare).sort();
  const rightRoots = right.map(normalizeForCompare).sort();
  return leftRoots.length === rightRoots.length && leftRoots.every((item, index) => item === rightRoots[index]);
}

function isInsideRoot(localPath, root) {
  const resolved = normalizeForCompare(localPath);
  const allowed = normalizeForCompare(root);
  const allowedWithSep = allowed.endsWith(path.sep) ? allowed : allowed + path.sep;
  return resolved === allowed || resolved.startsWith(allowedWithSep);
}

function isInsideAny(localPath, roots) {
  return roots.some((root) => isInsideRoot(localPath, root));
}

function validateRequiredArgs(studyRootArg, manifestArg, cliImageRootArgs) {
  if (!studyRootArg) {
    throw new Error("Missing required --study-root argument");
  }
  if (!manifestArg) {
    throw new Error("Missing required --manifest argument");
  }
  if (!cliImageRootArgs.length) {
    throw new Error("Missing required --images-root argument");
  }
}

function validateManifestItems(manifest, imageRoots, thumbnailRoot) {
  for (const item of manifest.items || []) {
    if (item.source_path && !isInsideAny(item.source_path, imageRoots)) {
      throw new Error(`manifest item source_path escapes allowed image roots: ${item.id || item.source_path}`);
    }
    if (item.thumbnail_path && !isInsideRoot(item.thumbnail_path, thumbnailRoot)) {
      throw new Error(`manifest item thumbnail_path escapes gallery index root: ${item.id || item.thumbnail_path}`);
    }
  }
}

function itemById(id) {
  return (runtime.manifest.items || []).find((item) => item.id === id);
}

function isAllowedImage(localPath) {
  return isInsideAny(localPath, runtime.allowedImageRoots);
}

function isAllowedThumbnail(localPath) {
  return isInsideRoot(localPath, runtime.allowedThumbnailRoot);
}

function resolveRuntime() {
  const studyRootArg = argValue("--study-root");
  const manifestArg = argValue("--manifest");
  const cliImageRootArgs = argValues("--images-root");
  validateRequiredArgs(studyRootArg, manifestArg, cliImageRootArgs);
  validateCliImageRootArgs(cliImageRootArgs);

  const studyRoot = canonicalPath(studyRootArg);
  const manifestPath = canonicalPath(manifestArg);
  const cliImageRoots = cliImageRootArgs.map((root) => canonicalPath(root));
  assertNoDuplicateRoots(cliImageRoots, "duplicate --images-root");

  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Cannot find manifest: ${manifestPath}`);
  }
  if (!isInsideRoot(manifestPath, studyRoot)) {
    throw new Error(`Manifest must be inside study root: ${manifestPath}`);
  }

  const manifest = readJsonFile(manifestPath);
  const manifestImageRoots = manifestSourceRootPaths(manifest).map((root) => canonicalPath(root));
  assertNoDuplicateRoots(manifestImageRoots, "duplicate manifest source root");

  if (!sameRootSet(cliImageRoots, manifestImageRoots)) {
    throw new Error("manifest image roots do not match CLI --images-root values");
  }

  const galleryIndexRoot = canonicalPath(path.join(studyRoot, "4.临时目录", "中间索引", "图片画廊"));
  validateManifestItems(manifest, cliImageRoots, galleryIndexRoot);

  return {
    studyRoot,
    manifestPath,
    manifest,
    allowedImageRoots: cliImageRoots,
    allowedThumbnailRoot: galleryIndexRoot
  };
}

function mimeFor(localPath) {
  const extension = path.extname(localPath).toLowerCase();
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".webp") return "image/webp";
  if (extension === ".gif") return "image/gif";
  if (extension === ".bmp") return "image/bmp";
  return "image/png";
}

function pathForAsset(kind, id) {
  if (kind !== "thumb" && kind !== "source") return null;
  const item = itemById(id);
  if (!item) return null;
  const selected = kind === "thumb" ? item.thumbnail_path : item.source_path;
  if (!selected) return null;
  if (kind === "thumb" && !isAllowedThumbnail(selected)) return null;
  if (kind === "source" && !isAllowedImage(selected)) return null;
  return selected;
}

function registerProtocols() {
  protocol.handle("ga-image", async (request) => {
    const url = new URL(request.url);
    const kind = url.hostname;
    const id = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    const localPath = pathForAsset(kind, id);
    if (!localPath || !fs.existsSync(localPath)) {
      return new Response("missing image", { status: 404 });
    }
    return new Response(fs.readFileSync(localPath), {
      headers: { "content-type": mimeFor(localPath) }
    });
  });
}

function installNetworkBlocker() {
  session.defaultSession.webRequest.onBeforeRequest(
    { urls: ["http://*/*", "https://*/*"] },
    (_details, callback) => callback({ cancel: true })
  );
}

function rendererUrl() {
  return pathToFileURL(path.join(__dirname, "renderer", "index.html")).href;
}

function isTrustedIpcEvent(event) {
  if (!mainWindow || !event || event.sender !== mainWindow.webContents) return false;
  const frameUrl = event.senderFrame && event.senderFrame.url ? event.senderFrame.url : "";
  const senderUrl = event.sender && typeof event.sender.getURL === "function" ? event.sender.getURL() : "";
  return frameUrl ? frameUrl === rendererUrl() : senderUrl === rendererUrl();
}

function requireTrustedIpcEvent(event) {
  if (!isTrustedIpcEvent(event)) {
    throw new Error("Untrusted IPC sender");
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 980,
    minWidth: 920,
    minHeight: 640,
    title: runtime.manifest.title || "GameAnalysis 图片画廊",
    backgroundColor: "#171717",
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
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

function publicManifest() {
  return {
    ...runtime.manifest,
    items: (runtime.manifest.items || []).map((item) => ({
      ...item,
      thumb_url: `ga-image://thumb/${encodeURIComponent(item.id)}`,
      source_url: `ga-image://source/${encodeURIComponent(item.id)}`
    }))
  };
}

function registerIpcHandlers() {
  ipcMain.handle("load-manifest", (event) => {
    requireTrustedIpcEvent(event);
    return publicManifest();
  });

  ipcMain.handle("open-in-folder", (event, id) => {
    if (!isTrustedIpcEvent(event)) return { ok: false };
    const item = itemById(id);
    if (!item || !item.source_path || !isAllowedImage(item.source_path)) return { ok: false };
    shell.showItemInFolder(item.source_path);
    return { ok: true, path: item.source_path };
  });

  ipcMain.handle("copy-path", (event, id) => {
    if (!isTrustedIpcEvent(event)) return { ok: false };
    const item = itemById(id);
    if (!item || !item.source_path || !isAllowedImage(item.source_path)) return { ok: false };
    clipboard.writeText(item.source_path);
    return { ok: true, path: item.source_path };
  });
}

function setRuntimeForTest(nextRuntime) {
  runtime = nextRuntime;
}

function setMainWindowForTest(nextMainWindow) {
  mainWindow = nextMainWindow;
}

function startApp() {
  app.whenReady().then(() => {
    try {
      runtime = resolveRuntime();
      if (process.argv.includes("--self-test")) {
        console.log(JSON.stringify({ ok: true, item_count: (runtime.manifest.items || []).length }, null, 2));
        app.quit();
        return;
      }
      registerIpcHandlers();
      registerProtocols();
      installNetworkBlocker();
      createWindow();
    } catch (error) {
      console.error(error.message || String(error));
      app.exit(1);
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}

if (isTestMode) {
  module.exports = {
    argValues,
    argValue,
    canonicalPath,
    normalizeForCompare,
    sameRootSet,
    isInsideRoot,
    isInsideAny,
    resolveRuntime,
    pathForAsset,
    setRuntimeForTest,
    setMainWindowForTest,
    isTrustedIpcEvent,
    rendererUrl
  };
} else {
  startApp();
}

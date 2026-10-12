const {
  app,
  BrowserWindow,
  clipboard,
  ipcMain,
  net,
  protocol,
  shell
} = require("electron");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      standard: true,
      secure: true,
      corsEnabled: true,
      supportFetchAPI: true,
      stream: true
    }
  }
]);

const MODEL_EXTENSIONS = new Set([".fbx", ".glb", ".gltf"]);
const DEFAULT_PATHS = {
  modelsDir: path.join("1.分类资源", "模型"),
  previewIndexDir: path.join("4.临时目录", "中间索引", "模型预览"),
  manifestName: "manifest.json",
  thumbsDir: "thumbs",
  previewCacheDir: "preview-cache"
};

let mainWindow = null;
let runtime = null;

function getArgValue(argv, name) {
  const index = argv.indexOf(name);
  if (index < 0 || index + 1 >= argv.length) return null;
  return argv[index + 1];
}

function comparablePath(value) {
  const resolved = path.resolve(value);
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function resolveInsideRoot(studyRoot, candidate, label = "path") {
  const root = path.resolve(studyRoot);
  const resolved = path.resolve(root, candidate || ".");
  const compareRoot = comparablePath(root);
  const compareResolved = comparablePath(resolved);
  const rootWithSep = compareRoot.endsWith(path.sep) ? compareRoot : compareRoot + path.sep;
  if (compareResolved !== compareRoot && !compareResolved.startsWith(rootWithSep)) {
    throw new Error(`${label} must stay inside study-root: ${resolved}`);
  }
  return resolved;
}

function buildRuntime(argv = process.argv) {
  const studyArg =
    getArgValue(argv, "--study-root") ||
    process.env.GAME_ANALYSIS_STUDY_ROOT ||
    process.cwd();
  const studyRoot = path.resolve(studyArg);
  const previewIndexDir = resolveInsideRoot(studyRoot, DEFAULT_PATHS.previewIndexDir, "preview-index");
  const manifestArg =
    getArgValue(argv, "--manifest") ||
    path.join(previewIndexDir, DEFAULT_PATHS.manifestName);

  return {
    studyRoot,
    manifest: resolveInsideRoot(studyRoot, manifestArg, "manifest"),
    modelsDir: resolveInsideRoot(
      studyRoot,
      getArgValue(argv, "--models-dir") || DEFAULT_PATHS.modelsDir,
      "models-dir"
    ),
    thumbsDir: resolveInsideRoot(
      studyRoot,
      getArgValue(argv, "--thumbs-dir") || path.join(previewIndexDir, DEFAULT_PATHS.thumbsDir),
      "thumbs-dir"
    ),
    previewCacheDir: resolveInsideRoot(
      studyRoot,
      getArgValue(argv, "--preview-cache-dir") ||
        path.join(previewIndexDir, DEFAULT_PATHS.previewCacheDir),
      "preview-cache-dir"
    ),
    assetProtocol: "app://asset"
  };
}

function latestModelMtime(modelsDir) {
  let latest = 0;
  if (!fs.existsSync(modelsDir)) return latest;

  const pending = [modelsDir];
  while (pending.length) {
    const current = pending.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (_) {
      continue;
    }

    for (const entry of entries) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(entryPath);
      } else if (entry.isFile() && MODEL_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        try {
          latest = Math.max(latest, fs.statSync(entryPath).mtimeMs);
        } catch (_) {
          // Ignore files that disappear during a scan.
        }
      }
    }
  }

  return latest;
}

function readManifestFile() {
  if (!fs.existsSync(runtime.manifest)) {
    return {
      ok: false,
      code: "manifest-missing",
      message: `找不到 manifest：${runtime.manifest}`,
      latestModelMtime: latestModelMtime(runtime.modelsDir)
    };
  }

  const manifestStat = fs.statSync(runtime.manifest);
  const modelMtime = latestModelMtime(runtime.modelsDir);
  if (modelMtime > manifestStat.mtimeMs + 1000) {
    return {
      ok: false,
      code: "manifest-stale",
      message: "manifest 已过期：模型目录中存在更新的模型文件，请重新生成模型预览索引。",
      manifestMtime: manifestStat.mtimeMs,
      latestModelMtime: modelMtime
    };
  }

  try {
    const manifest = JSON.parse(fs.readFileSync(runtime.manifest, "utf8"));
    if (!manifest || typeof manifest !== "object" || !Array.isArray(manifest.items)) {
      return {
        ok: false,
        code: "manifest-invalid",
        message: "manifest 格式无效：根对象必须包含 items 数组。"
      };
    }
    return {
      ok: true,
      manifest,
      manifestMtime: manifestStat.mtimeMs,
      latestModelMtime: modelMtime
    };
  } catch (error) {
    return {
      ok: false,
      code: "manifest-read-error",
      message: `读取 manifest 失败：${error.message}`
    };
  }
}

function decodeAssetPath(pathname) {
  return pathname.replace(/^\/+/, "").split("/").map(decodeURIComponent).join(path.sep);
}

function registerAssetProtocol() {
  protocol.handle("app", async (request) => {
    const url = new URL(request.url);
    if (url.host !== "asset") {
      return new Response("Unsupported app protocol host", { status: 404 });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("app://asset is read-only", { status: 405 });
    }

    try {
      const relativePath = decodeAssetPath(url.pathname);
      const requestedPath = path.join(runtime.studyRoot, relativePath);
      const resolvedPath = resolveInsideRoot(runtime.studyRoot, requestedPath, "asset");
      if (!fs.existsSync(resolvedPath) || !fs.statSync(resolvedPath).isFile()) {
        return new Response("Asset not found", { status: 404 });
      }
      return net.fetch(pathToFileURL(resolvedPath).toString());
    } catch (error) {
      return new Response(error.message, { status: 403 });
    }
  });
}

function resolvePayloadPath(payload, label = "path") {
  const rawPath = typeof payload === "string" ? payload : payload && payload.path;
  if (!rawPath) {
    throw new Error(`${label} is required`);
  }
  return resolveInsideRoot(runtime.studyRoot, rawPath, label);
}

async function openFolder(payload) {
  try {
    const targetPath = resolvePayloadPath(payload, "open-folder");
    if (fs.existsSync(targetPath) && fs.statSync(targetPath).isFile()) {
      shell.showItemInFolder(targetPath);
      return { ok: true, path: targetPath, mode: "select" };
    }
    if (fs.existsSync(targetPath) && fs.statSync(targetPath).isDirectory()) {
      const error = await shell.openPath(targetPath);
      return { ok: !error, path: targetPath, mode: "folder", error };
    }

    const parent = path.dirname(targetPath);
    if (fs.existsSync(parent)) {
      const error = await shell.openPath(parent);
      return { ok: !error, path: parent, mode: "parent", error };
    }
    return { ok: false, path: targetPath, reason: "missing" };
  } catch (error) {
    return { ok: false, reason: error.message };
  }
}

function copyPath(payload) {
  try {
    const targetPath = resolvePayloadPath(payload, "copy-path");
    clipboard.writeText(targetPath);
    return { ok: true, path: targetPath };
  } catch (error) {
    return { ok: false, reason: error.message };
  }
}

function createWindow() {
  const indexPath = path.join(__dirname, "index.html");
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1080,
    minHeight: 720,
    title: "GameModelViewer",
    backgroundColor: "#121417",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js")
    }
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, targetUrl) => {
    if (targetUrl !== pathToFileURL(indexPath).toString()) {
      event.preventDefault();
    }
  });
  mainWindow.webContents.on("will-redirect", (event) => {
    event.preventDefault();
  });

  mainWindow.loadFile(indexPath);
}

async function runSelfTest() {
  const manifestResult = readManifestFile();
  const payload = {
    ok: manifestResult.ok,
    runtime,
    manifestExists: fs.existsSync(runtime.manifest),
    modelsDirExists: fs.existsSync(runtime.modelsDir),
    thumbsDirExists: fs.existsSync(runtime.thumbsDir),
    previewCacheDirExists: fs.existsSync(runtime.previewCacheDir),
    manifestMessage: manifestResult.message || "",
    latestModelMtime: manifestResult.latestModelMtime || 0
  };
  if (!manifestResult.ok) process.exitCode = 1;
  console.log(JSON.stringify(payload, null, 2));
  app.quit();
}

ipcMain.handle("get-runtime", () => runtime);
ipcMain.handle("read-manifest", () => readManifestFile());
ipcMain.handle("open-folder", (_event, payload) => openFolder(payload));
ipcMain.handle("copy-path", (_event, payload) => copyPath(payload));

app.whenReady().then(async () => {
  runtime = buildRuntime();
  registerAssetProtocol();

  if (process.argv.includes("--self-test")) {
    await runSelfTest();
    return;
  }

  createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

const { app, BrowserWindow, Menu, clipboard, ipcMain, shell } = require("electron");
const fs = require("fs");
const path = require("path");
const { fileURLToPath } = require("url");

let mainWindow = null;
let currentItem = null;

function existingPath(candidates) {
  return candidates.find((candidate) => candidate && fs.existsSync(candidate)) || null;
}

function appBaseCandidates() {
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR || "";
  return [
    process.env.GAME_ART_STUDY_ROOT,
    portableDir,
    path.dirname(process.execPath),
    path.resolve(__dirname, ".."),
    path.resolve(process.cwd(), ".."),
    process.cwd()
  ].filter(Boolean);
}

function locateProject() {
  const bases = appBaseCandidates();
  const galleryHtml = existingPath(
    bases.flatMap((base) => [
      path.resolve(base, "2.报告", "全量图片画廊.html"),
      path.resolve(base, "2.报告", "全量图片画廊", "index.html"),
      path.resolve(base, "2.报告", "角色场景资源画廊", "index.html"),
      path.resolve(base, "2.报告", "主角候选资源画廊", "index.html"),
      path.resolve(base, "全量图片画廊", "index.html"),
      path.resolve(base, "gallery", "index.html"),
      path.resolve(base, "FullImageGallery", "index.html")
    ])
  );
  const exportRoot = existingPath(
    bases.flatMap((base) => [
      path.resolve(base, "4.临时目录", "工具产物", "AssetRipper", "ExportedProject"),
      path.resolve(base, "4.临时目录", "工具产物", "AssetRipper", "ExportedProject", "ExportedProject"),
      path.resolve(base, "0.原始导出", "AssetRipper", "ExportedProject"),
      path.resolve(base, "0.原始导出", "UnityPy"),
      path.resolve(base, "0.原始导出", "PakUnpacked"),
      path.resolve(base, "1.分类资源"),
      path.resolve(base, "4.临时目录", "_ascii_work", "PakUnpacked"),
      path.resolve(base, "4.临时目录", "_ascii_work", "GameMaker_UTMT_Exports"),
      path.resolve(base, "4.临时目录", "工具产物", "GameMaker_UTMT_Exports"),
      path.resolve(base, "ExportedProject"),
      path.resolve(base, "exported-project"),
      path.resolve(base, "PakUnpacked"),
      path.resolve(base, "GameMaker_UTMT_Exports"),
      path.resolve(base, "..", "4.临时目录", "_ascii_work", "GameMaker_UTMT_Exports")
    ])
  );

  return {
    galleryHtml,
    exportRoot,
    bases
  };
}

function safeResolveAsset(payload) {
  const { exportRoot } = locateProject();
  if (!payload) return null;

  if (payload.relativePath && exportRoot) {
    const relative = String(payload.relativePath).replaceAll("/", path.sep);
    const candidate = path.resolve(exportRoot, relative);
    const rootWithSep = exportRoot.endsWith(path.sep) ? exportRoot : exportRoot + path.sep;
    if (candidate === exportRoot || candidate.startsWith(rootWithSep)) {
      return candidate;
    }
  }

  if (payload.fileUrl) {
    try {
      return fileURLToPath(payload.fileUrl);
    } catch (_) {
      return null;
    }
  }

  return null;
}

async function showInFolder(payload) {
  const assetPath = safeResolveAsset(payload || currentItem);
  if (!assetPath) {
    return { ok: false, reason: "no-current-asset" };
  }

  if (fs.existsSync(assetPath)) {
    shell.showItemInFolder(assetPath);
    return { ok: true, path: assetPath, mode: "select" };
  }

  const folder = path.dirname(assetPath);
  if (fs.existsSync(folder)) {
    await shell.openPath(folder);
    return { ok: true, path: folder, mode: "folder" };
  }

  return { ok: false, path: assetPath, reason: "missing" };
}

function copyAssetPath(payload) {
  const assetPath = safeResolveAsset(payload || currentItem);
  if (!assetPath) return { ok: false, reason: "no-current-asset" };
  clipboard.writeText(assetPath);
  return { ok: true, path: assetPath };
}

function createWindow() {
  const project = locateProject();
  if (!project.galleryHtml) {
    throw new Error(`Cannot find local gallery index.html. Checked: ${project.bases.join("; ")}`);
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 980,
    minWidth: 960,
    minHeight: 680,
    title: "本地全量素材画廊",
    backgroundColor: "#151515",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, "preload.js")
    }
  });

  mainWindow.loadFile(project.galleryHtml);

  mainWindow.webContents.on("context-menu", async () => {
    let pageItem = null;
    try {
      pageItem = await mainWindow.webContents.executeJavaScript("window.__galleryLastContextItem || null", true);
    } catch (_) {
      pageItem = null;
    }
    const item = pageItem || currentItem;
    if (item) currentItem = item;

    Menu.buildFromTemplate([
      {
        label: "在资源管理器中显示素材",
        enabled: Boolean(item),
        click: () => showInFolder(item)
      },
      {
        label: "复制素材绝对路径",
        enabled: Boolean(item),
        click: () => copyAssetPath(item)
      },
      { type: "separator" },
      {
        label: "打开导出根目录",
        enabled: Boolean(project.exportRoot),
        click: () => project.exportRoot && shell.openPath(project.exportRoot)
      },
      {
        label: "重新载入画廊",
        click: () => mainWindow.reload()
      }
    ]).popup({ window: mainWindow });
  });
}

ipcMain.handle("set-context-item", (_event, payload) => {
  currentItem = payload || null;
  return { ok: true };
});

ipcMain.handle("open-in-folder", (_event, payload) => showInFolder(payload));
ipcMain.handle("copy-path", (_event, payload) => copyAssetPath(payload));

async function runSelfTest() {
  const project = locateProject();
  if (!project.galleryHtml || !project.exportRoot) {
    process.exitCode = 1;
  }
  console.log(JSON.stringify({
    galleryHtml: project.galleryHtml,
    exportRoot: project.exportRoot,
    galleryExists: Boolean(project.galleryHtml && fs.existsSync(project.galleryHtml)),
    exportRootExists: Boolean(project.exportRoot && fs.existsSync(project.exportRoot)),
    checkedBases: project.bases
  }, null, 2));
  app.quit();
}

app.whenReady().then(() => {
  if (process.argv.includes("--self-test")) {
    return runSelfTest();
  }
  createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

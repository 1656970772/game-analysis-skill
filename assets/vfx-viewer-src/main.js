const { app, BrowserWindow, ipcMain, shell } = require('electron');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

function argValue(name) {
  const prefix = `${name}=`;
  for (let i = 0; i < process.argv.length; i += 1) {
    const value = process.argv[i];
    if (value === name) return process.argv[i + 1] || '';
    if (value.startsWith(prefix)) return value.slice(prefix.length);
  }
  return '';
}

function resolvePath(value) {
  return path.resolve(String(value || ''));
}

function resolveOptionalPath(value) {
  return value ? path.resolve(String(value)) : '';
}

function ensureInside(candidate, root) {
  const resolved = path.resolve(candidate);
  const resolvedRoot = path.resolve(root);
  return resolved === resolvedRoot || resolved.startsWith(`${resolvedRoot}${path.sep}`);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, ''));
}

function currentOptions() {
  return {
    selfTest: process.argv.includes('--self-test'),
    selfTestOutput: resolveOptionalPath(argValue('--self-test-output')),
    studyRoot: resolvePath(argValue('--study-root') || process.env.STUDY_ROOT || '.'),
    manifest: resolvePath(argValue('--manifest') || process.env.GAME_ANALYSIS_VFX_MANIFEST),
    reconstructedDir: resolvePath(argValue('--reconstructed-dir') || process.env.GAME_ANALYSIS_VFX_RECONSTRUCTED_DIR),
    previewCacheDir: resolvePath(argValue('--preview-cache-dir') || process.env.GAME_ANALYSIS_VFX_PREVIEW_CACHE_DIR),
    unityExportDir: resolvePath(argValue('--unity-export-dir') || process.env.GAME_ANALYSIS_VFX_UNITY_EXPORT_DIR),
  };
}

function writeSelfTestResult(outputPath, result) {
  if (!outputPath) return;
  const outputDir = path.dirname(outputPath);
  fs.mkdirSync(outputDir, { recursive: true });
  const temporaryPath = `${outputPath}.tmp-${process.pid || 'process'}-${Date.now()}`;
  try {
    fs.writeFileSync(temporaryPath, `${JSON.stringify(result)}\n`, 'utf8');
    fs.renameSync(temporaryPath, outputPath);
  } finally {
    if (fs.existsSync(temporaryPath)) fs.unlinkSync(temporaryPath);
  }
}

function validateOptions(options) {
  const requiredFiles = [['manifest', options.manifest]];
  const requiredDirs = [
    ['studyRoot', options.studyRoot],
    ['reconstructedDir', options.reconstructedDir],
    ['unityExportDir', options.unityExportDir],
  ];
  for (const [name, filePath] of requiredFiles) {
    if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      throw new Error(`${name} not found: ${filePath}`);
    }
  }
  for (const [name, dirPath] of requiredDirs) {
    if (!dirPath || !fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory()) {
      throw new Error(`${name} not found: ${dirPath}`);
    }
  }
}

function manifestItems(options) {
  const manifest = readJson(options.manifest);
  const items = Array.isArray(manifest.items) ? manifest.items : [];
  return {
    manifest: {
      schema_version: manifest.schema_version,
      engine: manifest.engine,
      item_count: manifest.item_count,
      source_index: manifest.source_index,
      original_vfx_prefab_export: manifest.original_vfx_prefab_export,
    },
    items,
  };
}

function unityPyRootFromManifest(options, manifest) {
  if (manifest.source_index) {
    return path.dirname(resolvePath(manifest.source_index));
  }
  return path.join(options.studyRoot, '0.原始导出', 'UnityPy', 'UnityPy全量可读资源');
}

function textureUrlFor(options, reconstruction, manifest) {
  const textureRef = reconstruction?.modules?.renderer?.textureRef;
  if (!textureRef || path.isAbsolute(textureRef) || textureRef.includes('..')) return '';
  const root = unityPyRootFromManifest(options, manifest);
  const candidate = path.resolve(root, textureRef);
  if (!ensureInside(candidate, root) || !fs.existsSync(candidate)) return '';
  return pathToFileURL(candidate).toString();
}

function reconstructionPath(options, relativePath) {
  const normalized = String(relativePath || '').replace(/\\/g, '/');
  const withoutPrefix = normalized.startsWith('reconstructed/')
    ? normalized.slice('reconstructed/'.length)
    : normalized;
  const candidate = path.resolve(options.reconstructedDir, withoutPrefix);
  if (!ensureInside(candidate, options.reconstructedDir)) {
    throw new Error(`reconstruction path escapes root: ${relativePath}`);
  }
  return candidate;
}

function unityExportPath(options, relativePath) {
  const normalized = String(relativePath || '').replace(/\\/g, '/');
  if (path.isAbsolute(normalized) || normalized.includes('..')) {
    throw new Error(`unity export path is invalid: ${relativePath}`);
  }
  const candidate = path.resolve(options.unityExportDir, normalized);
  if (!ensureInside(candidate, options.unityExportDir)) {
    throw new Error(`unity export path escapes root: ${relativePath}`);
  }
  return candidate;
}

async function runSelfTest(options) {
  validateOptions(options);
  const data = manifestItems(options);
  const ready = data.items.filter((item) => item.preview_status === 'ready').length;
  const firstReadable = data.items.find((item) => item.reconstruction_relative_path);
  if (firstReadable) {
    readJson(reconstructionPath(options, firstReadable.reconstruction_relative_path));
  }
  const result = {
    ok: true,
    item_count: data.items.length,
    ready_count: ready,
    manifest: options.manifest,
    reconstructed_dir: options.reconstructedDir,
    unity_export_dir: options.unityExportDir,
  };
  writeSelfTestResult(options.selfTestOutput, result);
  process.stdout.write(JSON.stringify(result));
}

function createWindow(options) {
  const win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#121417',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(path.join(__dirname, 'index.html'));
}

const options = currentOptions();

ipcMain.handle('vfx:get-initial-data', () => {
  validateOptions(options);
  const data = manifestItems(options);
  return {
    ...data,
    paths: {
      studyRoot: options.studyRoot,
      manifest: options.manifest,
      reconstructedDir: options.reconstructedDir,
      previewCacheDir: options.previewCacheDir,
      unityExportDir: options.unityExportDir,
    },
  };
});

ipcMain.handle('vfx:read-reconstruction', (_event, relativePath) => {
  const data = manifestItems(options);
  const filePath = reconstructionPath(options, relativePath);
  const reconstruction = readJson(filePath);
  return {
    reconstruction,
    textureUrl: textureUrlFor(options, reconstruction, data.manifest),
  };
});

ipcMain.handle('vfx:read-original-prefab-preview', (_event, relativePath) => {
  const filePath = unityExportPath(options, relativePath);
  return readJson(filePath);
});

ipcMain.handle('vfx:open-unity-export', async () => {
  return shell.openPath(options.unityExportDir);
});

app.whenReady().then(async () => {
  if (options.selfTest) {
    try {
      await runSelfTest(options);
      app.exit(0);
    } catch (error) {
      const errorText = String(error && error.stack ? error.stack : error);
      try {
        writeSelfTestResult(options.selfTestOutput, {
          ok: false,
          error: errorText,
        });
      } catch (outputError) {
        process.stderr.write(`${String(outputError && outputError.stack ? outputError.stack : outputError)}\n`);
      }
      process.stderr.write(errorText);
      app.exit(1);
    }
    return;
  }
  createWindow(options);
});

app.on('window-all-closed', () => {
  app.quit();
});

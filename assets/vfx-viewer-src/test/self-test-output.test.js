const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const mainPath = path.resolve(__dirname, '..', 'main.js');
const mainSource = fs.readFileSync(mainPath, 'utf8');

async function runMain(argv) {
  let readyPromise;
  let exitCode;
  let stdout = '';
  let stderr = '';
  const electron = {
    app: {
      whenReady() {
        return {
          then(callback) {
            readyPromise = Promise.resolve().then(callback);
            return readyPromise;
          },
        };
      },
      exit(code) {
        exitCode = code;
      },
      on() {},
      quit() {},
    },
    BrowserWindow: class {},
    ipcMain: { handle() {} },
    shell: { openPath: async () => '' },
  };
  const fakeProcess = {
    argv: ['node', mainPath, ...argv],
    env: {},
    stdout: { write(value) { stdout += String(value); } },
    stderr: { write(value) { stderr += String(value); } },
  };
  const sandbox = {
    __dirname: path.dirname(mainPath),
    Buffer,
    console,
    process: fakeProcess,
    require(name) {
      if (name === 'electron') return electron;
      return require(name);
    },
  };
  vm.runInNewContext(mainSource, sandbox, { filename: mainPath });
  await readyPromise;
  return { exitCode, stdout, stderr };
}

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'game-vfx-viewer-self-test-'));
  const reconstructedDir = path.join(root, 'reconstructed');
  const previewCacheDir = path.join(root, 'preview-cache');
  const unityExportDir = path.join(root, 'unity-export');
  fs.mkdirSync(reconstructedDir);
  fs.mkdirSync(previewCacheDir);
  fs.mkdirSync(unityExportDir);
  fs.writeFileSync(
    path.join(reconstructedDir, 'ready.json'),
    JSON.stringify({ schema_version: 1 }),
    'utf8',
  );
  const manifest = path.join(root, 'manifest.json');
  fs.writeFileSync(
    manifest,
    JSON.stringify({
      schema_version: 1,
      engine: 'Unity',
      item_count: 2,
      items: [
        {
          preview_status: 'ready',
          reconstruction_relative_path: 'reconstructed/ready.json',
        },
        { preview_status: 'partial' },
      ],
    }),
    'utf8',
  );
  return { root, manifest, reconstructedDir, previewCacheDir, unityExportDir };
}

function argumentsFor(paths, outputPath) {
  return [
    '--self-test',
    '--self-test-output', outputPath,
    '--study-root', paths.root,
    '--manifest', paths.manifest,
    '--reconstructed-dir', paths.reconstructedDir,
    '--preview-cache-dir', paths.previewCacheDir,
    '--unity-export-dir', paths.unityExportDir,
  ];
}

test('self-test atomically writes a UTF-8 JSON result to an explicit path', async (t) => {
  const paths = fixture();
  t.after(() => fs.rmSync(paths.root, { recursive: true, force: true }));
  const outputPath = path.join(paths.root, 'result.json');

  const observed = await runMain(argumentsFor(paths, outputPath));

  assert.equal(observed.exitCode, 0);
  assert.equal(fs.existsSync(outputPath), true);
  const result = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  assert.deepEqual(
    JSON.parse(JSON.stringify(result)),
    {
      ok: true,
      item_count: 2,
      ready_count: 1,
      manifest: paths.manifest,
      reconstructed_dir: paths.reconstructedDir,
      unity_export_dir: paths.unityExportDir,
    },
  );
  assert.deepEqual(
    fs.readdirSync(paths.root).filter((name) => name.startsWith('result.json.tmp-')),
    [],
  );
});

test('self-test writes diagnostic JSON and exits non-zero on failure', async (t) => {
  const paths = fixture();
  t.after(() => fs.rmSync(paths.root, { recursive: true, force: true }));
  paths.manifest = path.join(paths.root, 'missing-manifest.json');
  const outputPath = path.join(paths.root, 'failure.json');

  const observed = await runMain(argumentsFor(paths, outputPath));

  assert.equal(observed.exitCode, 1);
  assert.equal(fs.existsSync(outputPath), true);
  const result = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  assert.equal(result.ok, false);
  assert.match(result.error, /manifest not found/);
});

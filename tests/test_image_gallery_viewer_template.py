from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "assets" / "image-gallery-viewer-src"


def run_node_gallery_test(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GA_IMAGE_GALLERY_TEST"] = "1"
    env["MAIN_JS"] = str(VIEWER / "main.js")
    return subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_viewer_template_reads_manifest_argument_and_avoids_html_gallery() -> None:
    main_js = (VIEWER / "main.js").read_text(encoding="utf-8")

    assert "--manifest" in main_js
    assert "--study-root" in main_js
    assert "--images-root" in main_js
    assert "argValues" in main_js
    assert "manifest.source_roots" in main_js
    assert "Missing required --study-root argument" in main_js
    assert "Missing required --manifest argument" in main_js
    assert "Missing required --images-root argument" in main_js
    assert "Manifest must be inside study root" in main_js
    assert "manifest image roots do not match CLI --images-root values" in main_js
    assert "manifest item source_path escapes allowed image roots" in main_js
    assert "realpathSync.native" in main_js
    assert "protocol.handle" in main_js
    assert "setWindowOpenHandler" in main_js
    assert "onBeforeRequest" in main_js
    assert '"http://*/*"' in main_js
    assert '"https://*/*"' in main_js
    assert "全量图片画廊.html" not in main_js
    assert "build_full_image_gallery" not in main_js


def test_manifest_items_validate_sources_and_thumbnails() -> None:
    main_js = (VIEWER / "main.js").read_text(encoding="utf-8")
    start = main_js.index("function validateManifestItems")
    end = main_js.index("function itemById", start)
    validator = main_js[start:end]

    assert "source_path" in validator
    assert "thumbnail_path" in validator
    assert "isInsideAny(item.source_path" in validator
    assert "isInsideRoot(item.thumbnail_path" in validator
    assert "manifest item source_path escapes allowed image roots" in validator
    assert "manifest item thumbnail_path escapes gallery index root" in validator


def test_shell_and_clipboard_actions_require_allowed_image_guard() -> None:
    main_js = (VIEWER / "main.js").read_text(encoding="utf-8")

    show_index = main_js.index("shell.showItemInFolder")
    copy_index = main_js.index("clipboard.writeText")
    assert "isAllowedImage(item.source_path)" in main_js[show_index - 260 : show_index]
    assert "isAllowedImage(item.source_path)" in main_js[copy_index - 260 : copy_index]


def test_preload_exposes_only_expected_bridge_methods() -> None:
    preload = (VIEWER / "preload.js").read_text(encoding="utf-8")

    assert 'exposeInMainWorld("galleryBridge"' in preload
    assert "loadManifest" in preload
    assert "openInFolder" in preload
    assert "copyPath" in preload
    assert "ipcRenderer.invoke" in preload
    assert "shell" not in preload
    assert "clipboard" not in preload


def test_viewer_renderer_has_search_preview_and_explorer_actions() -> None:
    renderer = (VIEWER / "renderer" / "renderer.js").read_text(encoding="utf-8")
    html = (VIEWER / "renderer" / "index.html").read_text(encoding="utf-8")

    assert "galleryBridge.loadManifest" in renderer
    assert "state.manifest.ui" in renderer
    assert "populatePageSizeOptions" in renderer
    assert "openPreview" in renderer
    assert "openItemInFolder" in renderer
    assert 'addEventListener("contextmenu"' not in renderer
    assert '$("closePreview").addEventListener("click", closePreview)' in renderer
    assert '$("previewOverlay").addEventListener("click"' not in renderer
    assert "updatePreviewTransform" in renderer
    assert "resetPreviewTransform" in renderer
    assert 'addEventListener("wheel"' in renderer
    assert 'addEventListener("pointerdown"' in renderer
    assert 'addEventListener("pointermove"' in renderer
    assert 'addEventListener("pointerup"' in renderer
    assert "previewZoom" in renderer
    assert "previewPanX" in renderer
    assert "openInFolder" in renderer
    assert "copyPath" in renderer
    assert "innerHTML" not in renderer
    assert "Content-Security-Policy" in html
    assert 'id="search"' in html
    assert 'id="category"' in html
    assert 'id="sort"' in html
    assert 'id="previewOverlay"' in html
    assert 'id="closePreview" class="preview-close"' in html
    assert 'id="previewImage" class="preview-image" draggable="false"' in html
    assert '<select id="pageSize"></select>' in html
    assert '<option value="120"' not in html


def test_preview_surface_is_not_selectable_or_draggable() -> None:
    styles = (VIEWER / "renderer" / "styles.css").read_text(encoding="utf-8")

    assert ".preview-overlay" in styles
    assert "user-select: none;" in styles
    assert "-webkit-user-select: none;" in styles
    assert "-webkit-user-drag: none;" in styles
    assert "touch-action: none;" in styles
    assert "cursor: grab;" in styles
    assert ".preview-close::before" in styles
    assert ".preview-close::after" in styles


def test_renderer_csp_allows_only_packaged_and_ga_image_sources() -> None:
    html = (VIEWER / "renderer" / "index.html").read_text(encoding="utf-8")

    assert "Content-Security-Policy" in html
    assert "img-src 'self' ga-image:;" in html
    assert "img-src 'self' ga-image: data:;" not in html


def test_main_runtime_rejects_duplicate_or_malformed_roots_and_unknown_asset_kind() -> None:
    script = textwrap.dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const os = require("os");
        const path = require("path");
        const Module = require("module");

        let readyCalls = 0;
        const originalLoad = Module._load;
        Module._load = function(request, parent, isMain) {
          if (request === "electron") {
            return {
              app: {
                whenReady() {
                  readyCalls += 1;
                  return { then() {} };
                },
                on() {},
                quit() {},
                exit() {}
              },
              BrowserWindow: function BrowserWindow() {},
              ipcMain: { handle() {} },
              protocol: { handle() {} },
              shell: { showItemInFolder() {} },
              clipboard: { writeText() {} },
              session: { defaultSession: { webRequest: { onBeforeRequest() {} } } }
            };
          }
          return originalLoad.apply(this, arguments);
        };

        const api = require(process.env.MAIN_JS);
        assert.strictEqual(readyCalls, 0, "test import must not call app.whenReady");
        assert.strictEqual(typeof api.resolveRuntime, "function", "resolveRuntime must be exported in test mode");
        assert.strictEqual(typeof api.isInsideRoot, "function", "isInsideRoot must be exported in test mode");
        assert.strictEqual(typeof api.pathForAsset, "function", "pathForAsset must be exported in test mode");
        assert.strictEqual(typeof api.setRuntimeForTest, "function", "setRuntimeForTest must be exported in test mode");
        assert.strictEqual(typeof api.isTrustedIpcEvent, "function", "isTrustedIpcEvent must be exported in test mode");
        assert.strictEqual(typeof api.setMainWindowForTest, "function", "setMainWindowForTest must be exported in test mode");
        assert.strictEqual(typeof api.rendererUrl, "function", "rendererUrl must be exported in test mode");

        const base = fs.mkdtempSync(path.join(os.tmpdir(), "ga-gallery-viewer-"));
        const studyRoot = path.join(base, "study");
        const imageRoot = path.join(studyRoot, "images");
        const galleryRoot = path.join(studyRoot, "4.临时目录", "中间索引", "图片画廊");
        fs.mkdirSync(imageRoot, { recursive: true });
        fs.mkdirSync(galleryRoot, { recursive: true });
        const sourcePath = path.join(imageRoot, "asset.png");
        const thumbnailPath = path.join(galleryRoot, "asset-thumb.png");
        fs.writeFileSync(sourcePath, "source");
        fs.writeFileSync(thumbnailPath, "thumb");

        function writeManifest(name, sourceRoots, options = {}) {
          const manifestPath = path.join(studyRoot, name);
          const manifest = {
            title: "测试画廊",
            source_roots: sourceRoots,
            items: [Object.assign({
              id: "asset-1",
              source_path: sourcePath,
              thumbnail_path: thumbnailPath
            }, options.item || {})]
          };
          fs.writeFileSync(manifestPath, (options.bom ? "\uFEFF" : "") + JSON.stringify(manifest), "utf8");
          return manifestPath;
        }

        function withArgv(args, callback) {
          const previous = process.argv;
          process.argv = ["node", "main.js"].concat(args);
          try {
            return callback();
          } finally {
            process.argv = previous;
          }
        }

        function expectMessage(regex, callback) {
          assert.throws(callback, (error) => regex.test(String(error.message || error)));
        }

        expectMessage(
          /duplicate --images-root/i,
          () => withArgv([
            "--study-root", studyRoot,
            "--manifest", writeManifest("duplicate-cli.json", [{ path: imageRoot }]),
            "--images-root", imageRoot,
            "--images-root", imageRoot
          ], () => api.resolveRuntime())
        );

        expectMessage(
          /duplicate manifest source root/i,
          () => withArgv([
            "--study-root", studyRoot,
            "--manifest", writeManifest("duplicate-manifest.json", [{ path: imageRoot }, { path: imageRoot }]),
            "--images-root", imageRoot
          ], () => api.resolveRuntime())
        );

        expectMessage(
          /manifest source_roots\[0\]\.path.*non-empty string/i,
          () => withArgv([
            "--study-root", studyRoot,
            "--manifest", writeManifest("empty-path.json", [{ path: "" }]),
            "--images-root", imageRoot
          ], () => api.resolveRuntime())
        );

        assert.strictEqual(
          api.isInsideRoot(path.join(base, "foobar", "a.png"), path.join(base, "foo")),
          false,
          "sibling path prefix must not count as inside the root"
        );

        const runtime = withArgv([
          "--study-root", studyRoot,
          "--manifest", writeManifest("valid.json", [{ path: imageRoot }]),
          "--images-root", imageRoot
        ], () => api.resolveRuntime());
        api.setRuntimeForTest(runtime);
        assert.strictEqual(api.pathForAsset("other", "asset-1"), null);
        const trustedWebContents = { getURL() { return api.rendererUrl(); } };
        api.setMainWindowForTest({ webContents: trustedWebContents });
        assert.strictEqual(api.isTrustedIpcEvent({ sender: trustedWebContents, senderFrame: { url: api.rendererUrl() } }), true);
        assert.strictEqual(api.isTrustedIpcEvent({ sender: {}, senderFrame: { url: api.rendererUrl() } }), false);
        assert.strictEqual(api.isTrustedIpcEvent({ sender: trustedWebContents, senderFrame: { url: "https://example.invalid/" } }), false);

        const bomRuntime = withArgv([
          "--study-root", studyRoot,
          "--manifest", writeManifest("bom-valid.json", [{ path: imageRoot }], { bom: true }),
          "--images-root", imageRoot
        ], () => api.resolveRuntime());
        assert.strictEqual(bomRuntime.manifest.title, "测试画廊");

        const outsideSourcePath = path.join(base, "outside.png");
        fs.writeFileSync(outsideSourcePath, "outside");
        expectMessage(
          /manifest item source_path escapes allowed image roots/i,
          () => withArgv([
            "--study-root", studyRoot,
            "--manifest", writeManifest("bom-escape.json", [{ path: imageRoot }], {
              bom: true,
              item: { source_path: outsideSourcePath }
            }),
            "--images-root", imageRoot
          ], () => api.resolveRuntime())
        );
        """
    )

    result = run_node_gallery_test(script)

    assert result.returncode == 0, result.stderr + result.stdout


def test_viewer_package_builds_fixed_tool_name() -> None:
    package = (VIEWER / "package.json").read_text(encoding="utf-8")

    assert '"name": "game-analysis-image-gallery"' in package
    assert '"productName": "GameImageGallery"' in package
    assert '"artifactName": "GameImageGallery.${ext}"' in package
    assert '"start": "electron ."' in package
    assert '"self-test": "electron . --self-test --study-root . --manifest sample-manifest.json --images-root ."' in package
    assert '"dist": "electron-builder --win portable --x64"' in package
    assert '"electron"' in package
    assert '"electron-builder"' in package

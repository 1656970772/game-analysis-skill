from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "assets" / "game-animation-viewer-src"
BUILD_SCRIPT = ROOT / "scripts" / "build_game_animation_viewer_tool.ps1"
LAUNCHER_SCRIPT = ROOT / "scripts" / "create_game_animation_viewer_launcher.py"
RENDERER_SCRIPT = ROOT / "scripts" / "export_game_animation_asset.py"
FIXED_VIEWER = (
    ROOT
    / "built-in-tools"
    / "GameAnimationViewer_1.0.1"
    / "GameAnimationViewer.exe"
)


def run_node(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GA_GAME_ANIMATION_VIEWER_TEST"] = "1"
    env["ANIMATION_MAIN_JS"] = str(VIEWER / "main.js")
    return subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_template_contains_independent_electron_application() -> None:
    required = [
        VIEWER / "package.json",
        VIEWER / "main.js",
        VIEWER / "preload.js",
        VIEWER / "self-test.js",
        VIEWER / "renderer" / "index.html",
        VIEWER / "renderer" / "renderer.js",
        VIEWER / "renderer" / "styles.css",
    ]
    assert all(path.is_file() for path in required)
    package = json.loads((VIEWER / "package.json").read_text(encoding="utf-8"))
    assert package["main"] == "main.js"
    assert package["build"]["productName"] == "GameAnimationViewer"
    assert package["build"]["artifactName"] == "GameAnimationViewer.${ext}"
    assert package["scripts"]["self-test"] == "node self-test.js"
    assert "electron" in package["devDependencies"]
    assert "electron-builder" in package["devDependencies"]


def test_renderer_has_required_filters_player_materials_and_exports() -> None:
    html = (VIEWER / "renderer" / "index.html").read_text(encoding="utf-8")
    renderer = (VIEWER / "renderer" / "renderer.js").read_text(encoding="utf-8")
    styles = (VIEWER / "renderer" / "styles.css").read_text(encoding="utf-8")

    for element_id in [
        "search", "category", "statusFilter", "itemList", "buildSelect", "clipSelect",
        "playPause", "loop", "timeline", "frameReadout", "speed", "frameImage",
        "candidateDetails", "diagnostics", "materialList", "exportResult",
    ]:
        assert f'id="{element_id}"' in html
    for export_format in [
        "current-frame-png", "png-sequence", "apng", "gif", "source-bundle"
    ]:
        assert f'data-export-format="{export_format}"' in html
        assert f'"{export_format}"' in (VIEWER / "main.js").read_text(encoding="utf-8")
    assert "requestAnimationFrame(playbackTick)" in renderer
    assert "preview.frame_urls" in renderer
    assert "window.animationBridge.preparePreview" in renderer
    assert "window.animationBridge.exportAnimation" in renderer
    assert "new Image()" in renderer
    assert "<canvas" not in html
    assert "background-image:" in styles
    assert "linear-gradient(45deg" in styles
    assert "image-rendering: auto" in styles
    assert "innerHTML" not in renderer


def test_csp_browserwindow_protocol_and_ipc_are_locked_down() -> None:
    html = (VIEWER / "renderer" / "index.html").read_text(encoding="utf-8")
    main = (VIEWER / "main.js").read_text(encoding="utf-8")
    preload = (VIEWER / "preload.js").read_text(encoding="utf-8")

    assert "Content-Security-Policy" in html
    assert "img-src 'self' ga-animation:;" in html
    assert "connect-src 'none'" in html
    assert "object-src 'none'" in html
    assert "data:" not in html
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert "setWindowOpenHandler" in main
    assert 'on("will-navigate"' in main
    assert 'on("will-attach-webview"' in main
    assert "onBeforeRequest" in main
    assert '"http://*/*"' in main and '"https://*/*"' in main
    assert '"ws://*/*"' in main and '"wss://*/*"' in main
    assert "setPermissionRequestHandler" in main
    assert "requireTrustedIpcEvent(event)" in main
    assert 'protocol.handle("ga-animation"' in main
    assert 'url.hostname !== "resource"' in main
    assert "ga-animation://resource/${encodeURIComponent(resource.id)}" in main
    assert 'exposeInMainWorld("animationBridge"' in preload
    assert "ipcRenderer.invoke" in preload
    assert "require(\"fs\")" not in preload
    assert "shell" not in preload and "clipboard" not in preload


def test_main_enforces_cli_exact_roots_output_containment_and_renderer_contract() -> None:
    main = (VIEWER / "main.js").read_text(encoding="utf-8")
    for argument in [
        "--study-root", "--manifest", "--asset-root", "--preview-cache-dir",
        "--export-dir", "--python-exe", "--renderer-script", "--self-test",
        "--self-test-output",
    ]:
        assert argument in main
    assert "manifest asset roots do not exactly match CLI --asset-root values" in main
    assert "must not overlap read-only asset roots" in main
    assert "preview-cache-dir and export-dir must not overlap" in main
    assert "safeManifestRelativePath" in main
    assert "rejectLinkChain" in main
    assert "realpathSync.native" in main
    assert "spawn(runtime.pythonExe" in main
    assert "shell: false" in main
    assert '"--output-root", outputRoot' in main
    assert "Renderer must return exactly one JSON line" in main
    assert "Renderer output escapes authorized output root" in main
    assert "Unknown animation item id" in main
    assert "Unknown build candidate id for item" in main
    assert "Unknown clip id for item" in main
    assert "requestSingleInstanceLock" in main
    assert 'on("second-instance"' in main
    assert "showErrorBox(" in main
    assert 'once("ready-to-show", focusMainWindow)' in main


def test_launcher_bat_renderer_flag_matches_viewer_parse_args_contract() -> None:
    main = (VIEWER / "main.js").read_text(encoding="utf-8")
    launcher = LAUNCHER_SCRIPT.read_text(encoding="utf-8")
    viewer_match = re.search(
        r'rendererScriptArg\s*=\s*requireCliValue\(argv,\s*"(?P<flag>--[^"]+)"\)',
        main,
    )
    assert viewer_match is not None
    render_launcher_body = launcher.split("def render_launcher(", 1)[1].split(
        "def write_if_changed(", 1
    )[0]
    launcher_match = re.search(
        r'\("(?P<flag>--[^"]+)",\s*renderer_script\)',
        render_launcher_body,
    )
    assert launcher_match is not None
    assert launcher_match.group("flag") == viewer_match.group("flag") == "--renderer-script"
    assert "--exporter-script" not in render_launcher_body


def test_fixed_viewer_accepts_launcher_arguments_in_self_test(tmp_path: Path) -> None:
    if not FIXED_VIEWER.is_file():
        pytest.skip(f"fixed GameAnimationViewer is not built: {FIXED_VIEWER}")

    study_root = tmp_path / "动画学习项目"
    xml_root = study_root / "source" / "xml"
    atlas_root = study_root / "source" / "atlas"
    bank_directory = xml_root / "bank-package"
    build_directory = xml_root / "build-package"
    atlas_directory = atlas_root / "build-package"
    bank_directory.mkdir(parents=True)
    build_directory.mkdir(parents=True)
    atlas_directory.mkdir(parents=True)
    (bank_directory / "anim.xml").write_text("<Anims/>", encoding="utf-8")
    (build_directory / "build.xml").write_text("<Build/>", encoding="utf-8")
    (atlas_directory / "atlas-0.png").write_bytes(b"png-self-test")

    manifest = {
        "schema_version": 1,
        "adapter": "klei_dst",
        "study_root": ".",
        "source_roots": {"xml": "source/xml", "atlas": "source/atlas"},
        "item_count": 1,
        "items": [
            {
                "id": "item-1",
                "adapter": "klei_dst",
                "category": "特效",
                "confidence": 1,
                "reasons": [],
                "bank": {
                    "name": "bank",
                    "package": "bank-package",
                    "source_path": "source/xml/bank-package/anim.xml",
                },
                "build_candidates": [
                    {
                        "id": "build-1",
                        "build": {
                            "name": "build",
                            "package": "build-package",
                            "source_path": "source/xml/build-package/build.xml",
                        },
                        "score": 1,
                        "symbol_coverage": 1,
                        "shared_symbol_count": 1,
                        "required_symbol_count": 1,
                        "atlas_complete": True,
                        "reasons": ["same_package"],
                        "atlases": ["source/atlas/build-package/atlas-0.png"],
                    }
                ],
                "default_build_id": "build-1",
                "clips": [
                    {
                        "id": "clip-1",
                        "name": "idle",
                        "fps": 30,
                        "frame_count": 1,
                        "declared_frame_count": 1,
                        "source_path": "source/xml/bank-package/anim.xml",
                        "symbols": ["symbol"],
                    }
                ],
                "source_assets": [
                    {"kind": "anim_xml", "path": "source/xml/bank-package/anim.xml"},
                    {"kind": "build_xml", "path": "source/xml/build-package/build.xml"},
                    {"kind": "atlas_png", "path": "source/atlas/build-package/atlas-0.png"},
                ],
                "capabilities": {
                    "current_frame_png": True,
                    "png_sequence": True,
                    "apng": True,
                    "gif": True,
                    "source_bundle": True,
                },
                "preview_status": "ready",
                "warnings": [],
                "errors": [],
            }
        ],
    }
    manifest_path = (
        study_root / "4.临时目录" / "中间索引" / "游戏动画预览" / "manifest.json"
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    launcher_environment = os.environ.copy()
    launcher_environment["PYTHONIOENCODING"] = "utf-8"
    launcher_result = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER_SCRIPT),
            "--study-root",
            str(study_root),
            "--skill-root",
            str(ROOT),
            "--python-exe",
            sys.executable,
        ],
        cwd=ROOT,
        env=launcher_environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert launcher_result.returncode == 0, launcher_result.stderr + launcher_result.stdout

    asset_roots = [atlas_root.resolve(), xml_root.resolve()]
    preview_cache = (
        study_root / "4.临时目录" / "中间索引" / "游戏动画预览" / "preview-cache"
    ).resolve()
    export_dir = (study_root / "4.临时目录" / "动画预览导出").resolve()
    raw_arguments = [
        "--study-root",
        str(study_root.resolve()),
        "--manifest",
        str(manifest_path.resolve()),
    ]
    for root in asset_roots:
        raw_arguments.extend(["--asset-root", str(root)])
    raw_arguments.extend(
        [
            "--preview-cache-dir",
            str(preview_cache),
            "--export-dir",
            str(export_dir),
            "--python-exe",
            str(Path(sys.executable).resolve()),
            "--renderer-script",
            str(RENDERER_SCRIPT.resolve()),
        ]
    )

    launcher = study_root / "2.报告" / "打开动画预览.bat"
    launcher_content = launcher.read_text(encoding="utf-8-sig")
    for index in range(0, len(raw_arguments), 2):
        assert f'{raw_arguments[index]} "{raw_arguments[index + 1]}"' in launcher_content
    assert "--exporter-script" not in launcher_content

    self_test_output = study_root / "4.临时目录" / "GameAnimationViewer自检.json"
    result = subprocess.run(
        [
            str(FIXED_VIEWER),
            *raw_arguments,
            "--self-test",
            "--self-test-output",
            str(self_test_output),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=60,
    )
    process_output = (result.stderr + result.stdout).decode("utf-8", errors="replace")
    assert result.returncode == 0, process_output
    summary = json.loads(self_test_output.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert summary["asset_root_count"] == 2
    assert summary["renderer_script"] == RENDERER_SCRIPT.name


def test_node_runtime_contract_rejects_root_mismatch_traversal_and_untrusted_sender() -> None:
    script = textwrap.dedent(
        r"""
        const assert = require("assert");
        const fs = require("fs");
        const os = require("os");
        const path = require("path");
        const api = require(process.env.ANIMATION_MAIN_JS);
        const base = fs.mkdtempSync(path.join(os.tmpdir(), "ga-animation-template-test-"));
        try {
          const study = path.join(base, "study");
          const xmlRoot = path.join(study, "source", "xml");
          const atlasRoot = path.join(study, "source", "atlas");
          fs.mkdirSync(path.join(xmlRoot, "bankpkg"), { recursive: true });
          fs.mkdirSync(path.join(xmlRoot, "buildpkg"), { recursive: true });
          fs.mkdirSync(path.join(atlasRoot, "buildpkg"), { recursive: true });
          fs.writeFileSync(path.join(xmlRoot, "bankpkg", "anim.xml"), "<Anims/>");
          fs.writeFileSync(path.join(xmlRoot, "buildpkg", "build.xml"), "<Build/>");
          fs.writeFileSync(path.join(atlasRoot, "buildpkg", "atlas-0.png"), "png");
          const manifest = {
            schema_version: 1,
            adapter: "klei_dst",
            source_roots: { xml: "source/xml", atlas: "source/atlas" },
            items: [{
              id: "item", adapter: "klei_dst", category: "特效", confidence: 1, reasons: [],
              bank: { name: "bank", package: "bankpkg", source_path: "source/xml/bankpkg/anim.xml" },
              build_candidates: [{
                id: "build", build: { name: "build", package: "buildpkg", source_path: "source/xml/buildpkg/build.xml" },
                score: 1, symbol_coverage: 1, shared_symbol_count: 1, required_symbol_count: 1,
                atlas_complete: true, reasons: ["symbol_overlap"], atlases: ["source/atlas/buildpkg/atlas-0.png"]
              }],
              default_build_id: "build",
              clips: [{ id: "clip", name: "idle", fps: 10, frame_count: 1, declared_frame_count: 1, source_path: "source/xml/bankpkg/anim.xml", symbols: ["s"] }],
              source_assets: [], capabilities: {}, preview_status: "ready", warnings: [], errors: []
            }]
          };
          const manifestPath = path.join(study, "manifest.json");
          fs.writeFileSync(manifestPath, JSON.stringify(manifest));
          function args(assetRoots = [xmlRoot, atlasRoot], manifestFile = manifestPath) {
            const value = ["node", "main.js", "--study-root", study, "--manifest", manifestFile];
            for (const root of assetRoots) value.push("--asset-root", root);
            value.push(
              "--preview-cache-dir", path.join(study, "runtime", "preview"),
              "--export-dir", path.join(study, "runtime", "exports"),
              "--python-exe", process.execPath,
              "--renderer-script", process.env.ANIMATION_MAIN_JS
            );
            return value;
          }
          assert.throws(() => api.resolveRuntimeFromArgs(args([xmlRoot])), /exactly match/);
          assert.throws(() => api.resolveRuntimeFromArgs(args([xmlRoot, atlasRoot, atlasRoot])), /duplicate/i);
          const overlap = args();
          const previewIndex = overlap.indexOf("--preview-cache-dir") + 1;
          overlap[previewIndex] = path.join(xmlRoot, "bad-cache");
          assert.throws(() => api.resolveRuntimeFromArgs(overlap), /must not overlap read-only asset roots/);

          const valid = api.resolveRuntimeFromArgs(args());
          api.setRuntimeForTest(valid);
          const publicManifest = api.publicManifest();
          assert.strictEqual(publicManifest.items[0].bank.source.display_path, "source/xml/bankpkg/anim.xml");
          assert.strictEqual(JSON.stringify(publicManifest).includes(study), false, "renderer must not receive absolute source paths");
          assert.strictEqual(api.validateSelection({ itemId: "item", buildId: "build", clipId: "clip" }).clip.name, "idle");
          assert.throws(() => api.validateSelection({ itemId: "../item", buildId: "build", clipId: "clip" }), /Unknown animation item id/);
          assert.throws(() => api.safeManifestRelativePath("../outside.xml", "probe"), /traversal/);

          const trustedContents = { getURL() { return api.rendererUrl(); } };
          api.setMainWindowForTest({ webContents: trustedContents });
          assert.strictEqual(api.isTrustedIpcEvent({ sender: trustedContents, senderFrame: { url: api.rendererUrl() } }), true);
          assert.strictEqual(api.isTrustedIpcEvent({ sender: {}, senderFrame: { url: api.rendererUrl() } }), false);
          assert.strictEqual(api.isTrustedIpcEvent({ sender: trustedContents, senderFrame: { url: "https://example.invalid" } }), false);

          const focusCalls = [];
          api.setMainWindowForTest({
            isDestroyed() { return false; },
            isMinimized() { return true; },
            restore() { focusCalls.push("restore"); },
            show() { focusCalls.push("show"); },
            focus() { focusCalls.push("focus"); }
          });
          assert.strictEqual(api.focusMainWindow(), true);
          assert.deepStrictEqual(focusCalls, ["restore", "show", "focus"]);
          const scopeA = api.instanceScopeName(["node", "main.js", "--study-root", path.join(base, "study-a")]);
          const scopeB = api.instanceScopeName(["node", "main.js", "--study-root", path.join(base, "study-b")]);
          assert.match(scopeA, /^study-[0-9a-f]{16}$/);
          assert.notStrictEqual(scopeA, scopeB, "single-instance locks must be scoped per study root");

          manifest.items[0].bank.source_path = "../outside.xml";
          const maliciousManifest = path.join(study, "malicious.json");
          fs.writeFileSync(maliciousManifest, JSON.stringify(manifest));
          assert.throws(() => api.resolveRuntimeFromArgs(args([xmlRoot, atlasRoot], maliciousManifest)), /traversal/);
        } finally {
          fs.rmSync(base, { recursive: true, force: true });
        }
        """
    )
    result = run_node(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_build_script_targets_animation_template_and_fixed_exe_without_tooling_manifest() -> None:
    assert BUILD_SCRIPT.is_file()
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert '[string]$SkillRoot = ""' in script
    assert '[string]$Version = "1.0.1"' in script
    assert "assets\\game-animation-viewer-src" in script
    assert "GameAnimationViewer_$Version" in script
    assert '"GameAnimationViewer.exe"' in script
    assert "tools\\_build" in script
    assert "Assert-PathInside" in script
    assert "Invoke-Npm install" in script
    assert "Invoke-Npm run self-test" in script
    assert "Invoke-Npm run dist" in script
    assert "tooling-manifest" not in script


def test_javascript_sources_pass_node_syntax_check() -> None:
    for path in [
        VIEWER / "main.js",
        VIEWER / "preload.js",
        VIEWER / "self-test.js",
        VIEWER / "renderer" / "renderer.js",
    ]:
        result = subprocess.run(
            ["node", "--check", str(path)],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}{result.stdout}"


def test_npm_self_test_when_npm_is_available() -> None:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        pytest.skip("npm is unavailable")
    result = subprocess.run(
        [npm, "run", "self-test", "--silent"],
        cwd=VIEWER,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["ok"] is True
    assert summary["item_count"] == 1
    assert summary["asset_root_count"] == 2
    assert summary["offline"] is True

"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

process.env.GA_GAME_ANIMATION_VIEWER_TEST = "1";
const api = require("./main.js");

const base = fs.mkdtempSync(path.join(os.tmpdir(), "game-animation-viewer-self-test-"));
try {
  const studyRoot = path.join(base, "study");
  const xmlRoot = path.join(studyRoot, "assets", "xml");
  const atlasRoot = path.join(studyRoot, "assets", "atlas");
  const bankDir = path.join(xmlRoot, "bank-package");
  const buildDir = path.join(xmlRoot, "build-package");
  const atlasDir = path.join(atlasRoot, "build-package");
  fs.mkdirSync(bankDir, { recursive: true });
  fs.mkdirSync(buildDir, { recursive: true });
  fs.mkdirSync(atlasDir, { recursive: true });
  fs.writeFileSync(path.join(bankDir, "anim.xml"), "<Anims/>", "utf8");
  fs.writeFileSync(path.join(buildDir, "build.xml"), "<Build/>", "utf8");
  fs.writeFileSync(path.join(atlasDir, "atlas-0.png"), "png-self-test", "utf8");

  const manifest = {
    schema_version: 1,
    adapter: "klei_dst",
    study_root: ".",
    source_roots: { xml: "assets/xml", atlas: "assets/atlas" },
    item_count: 1,
    items: [{
      id: "item-1",
      adapter: "klei_dst",
      category: "特效",
      confidence: 1,
      reasons: ["self-test"],
      bank: { name: "bank", package: "bank-package", source_path: "assets/xml/bank-package/anim.xml" },
      build_candidates: [{
        id: "build-1",
        build: { name: "build", package: "build-package", source_path: "assets/xml/build-package/build.xml" },
        score: 1,
        symbol_coverage: 1,
        shared_symbol_count: 1,
        required_symbol_count: 1,
        atlas_complete: true,
        reasons: ["self-test"],
        atlases: ["assets/atlas/build-package/atlas-0.png"]
      }],
      default_build_id: "build-1",
      clips: [{
        id: "clip-1",
        name: "idle",
        fps: 10,
        frame_count: 2,
        declared_frame_count: 2,
        source_path: "assets/xml/bank-package/anim.xml",
        symbols: ["symbol"]
      }],
      source_assets: [
        { kind: "anim_xml", path: "assets/xml/bank-package/anim.xml" },
        { kind: "build_xml", path: "assets/xml/build-package/build.xml" },
        { kind: "atlas_png", path: "assets/atlas/build-package/atlas-0.png" }
      ],
      capabilities: { current_frame_png: true, png_sequence: true, apng: true, gif: true, source_bundle: true },
      preview_status: "ready",
      warnings: [],
      errors: []
    }]
  };
  const manifestPath = path.join(studyRoot, "manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest), "utf8");
  const selfTestOutput = path.join(studyRoot, "checks", "self-test.json");
  const argv = [
    "node", "main.js",
    "--self-test",
    "--study-root", studyRoot,
    "--manifest", manifestPath,
    "--asset-root", xmlRoot,
    "--asset-root", atlasRoot,
    "--preview-cache-dir", path.join(studyRoot, "runtime", "preview-cache"),
    "--export-dir", path.join(studyRoot, "runtime", "exports"),
    "--python-exe", process.execPath,
    "--renderer-script", path.join(__dirname, "main.js"),
    "--self-test-output", selfTestOutput
  ];

  const runtime = api.resolveRuntimeFromArgs(argv);
  api.setRuntimeForTest(runtime);
  const publicManifest = api.publicManifest();
  assert.strictEqual(publicManifest.item_count, 1);
  assert.strictEqual(publicManifest.items[0].bank.source.available, true);
  assert.strictEqual(JSON.stringify(publicManifest).includes(studyRoot), false);
  const summary = api.writeSelfTestOutput(runtime);
  assert.strictEqual(summary.ok, true);
  assert.strictEqual(summary.item_count, 1);
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(selfTestOutput, "utf8")), summary);
  console.log(JSON.stringify(summary));
} finally {
  fs.rmSync(base, { recursive: true, force: true });
}

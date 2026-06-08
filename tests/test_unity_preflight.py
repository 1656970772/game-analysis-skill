from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from unity_preflight import preflight, to_markdown  # noqa: E402


def test_detects_forestrike_style_il2cpp_layout(tmp_path: Path) -> None:
    root = tmp_path / "Forestrike"
    data = root / "Forestrike_Data"
    metadata = data / "il2cpp_data" / "Metadata"
    plugins = data / "Plugins" / "x86_64"
    streaming = data / "StreamingAssets"
    metadata.mkdir(parents=True)
    plugins.mkdir(parents=True)
    streaming.mkdir(parents=True)

    (root / "GameAssembly.dll").write_bytes(b"dll")
    (root / "UnityPlayer.dll").write_bytes(b"unity")
    (data / "app.info").write_text("Skeleton Crew Studio\nForestrike\n", encoding="utf-8")
    (data / "boot.config").write_text("gc-max-time-slice=3\n", encoding="utf-8")
    (data / "ScriptingAssemblies.json").write_text(
        json.dumps({"names": ["Assembly-CSharp.dll", "Cinemachine.dll"], "types": [16, 16]}),
        encoding="utf-8",
    )
    (metadata / "global-metadata.dat").write_bytes(struct.pack("<II", 0xFAB11BAF, 31) + b"\0" * 16)
    (data / "resources.assets").write_bytes(b"a" * 1024)
    (data / "resources.assets.resS").write_bytes(b"r" * 2048)
    (data / "resources.resource").write_bytes(b"x" * 4096)
    (data / "level0").write_bytes(b"level")
    (plugins / "steam_api64.dll").write_bytes(b"steam")
    (streaming / "Leaf_school_005.webm").write_bytes(b"webm")

    report = preflight(root)

    assert report["product"] == {"company": "Skeleton Crew Studio", "name": "Forestrike"}
    assert report["unity_data_dir"] == "Forestrike_Data"
    assert report["scripting_backend"] == "IL2CPP"
    assert report["il2cpp"]["metadata_version"] == 31
    assert report["il2cpp"]["cpp2il_2022_status"] == "unsupported-likely"
    assert "run_il2cpp_dumper_before_native_analysis" in report["workflow_flags"]
    assert "external_streaming_or_resource_payloads_present" in report["workflow_flags"]


def test_markdown_surfaces_next_workflow_steps(tmp_path: Path) -> None:
    root = tmp_path / "Game"
    data = root / "Game_Data" / "il2cpp_data" / "Metadata"
    data.mkdir(parents=True)
    (root / "GameAssembly.dll").write_bytes(b"dll")
    (root / "UnityPlayer.dll").write_bytes(b"unity")
    (data / "global-metadata.dat").write_bytes(struct.pack("<II", 0xFAB11BAF, 31))

    markdown = to_markdown(preflight(root))

    assert "# Unity Build Preflight" in markdown
    assert "GameAssembly.dll + global-metadata.dat" in markdown
    assert "Metadata version: `31`" in markdown
    assert "Il2CppDumper" in markdown

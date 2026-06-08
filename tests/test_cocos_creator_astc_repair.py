from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from cocos_creator_astc_repair import (  # noqa: E402
    collect_role_scene_assets,
    decode_cocos_uuid,
    find_astcenc,
    read_astc_header,
)


def test_decode_cocos_uuid_known_values() -> None:
    assert decode_cocos_uuid("d8NLgxLtdMlq5mkKXMCo5Z") == "d834b831-2ed7-4c96-ae66-90a5cc0a8e59"
    assert decode_cocos_uuid("07uKZxB21G3ZHt/ium7Psk") == "07b8a671-076d-46dd-91ed-fe2ba6ecfb24"
    assert decode_cocos_uuid("22YpWXPH9Mf4eYokc8sF18@6c48a") == "22629597-3c7f-4c7f-8798-a2473cb05d7c"


def test_read_astc_header_uses_24bit_little_endian_dimensions(tmp_path: Path) -> None:
    sample = tmp_path / "sample.astc"
    sample.write_bytes(bytes.fromhex("13ABA15C080801B00600621000010000") + b"\0" * 16)
    header = read_astc_header(sample)
    assert header.block_x == 8
    assert header.block_y == 8
    assert header.width == 1712
    assert header.height == 4194
    assert header.depth == 1


def test_collect_role_scene_assets_uses_paths_key_as_uuid_index(tmp_path: Path) -> None:
    bundle = tmp_path / "assets" / "assets" / "localRemoteRes"
    native = bundle / "native" / "22"
    native.mkdir(parents=True)
    (native / "22629597-3c7f-4c7f-8798-a2473cb05d7c.astc").write_bytes(
        bytes.fromhex("13ABA15C080801B00600621000010000") + b"\0" * 16
    )
    uuids = ["unused"] * 3733
    uuids[1] = "00XN9tR8pKEYztWhD9Hqen@6c48a"
    uuids[3732] = "22YpWXPH9Mf4eYokc8sF18"
    config = {
        "types": ["cc.ImageAsset", "cc.Texture2D"],
        "uuids": uuids,
        "paths": {
            "3732": ["bg/boXi", 0, 1],
            "1": ["spine/role/role01/texture", 1, 1],
        },
    }
    (bundle / "cc.config.json").write_text(json.dumps(config), encoding="utf-8")

    rows = collect_role_scene_assets(tmp_path)

    assert len(rows) == 1
    assert rows[0].asset_path == "bg/boXi"
    assert rows[0].uuid == "22629597-3c7f-4c7f-8798-a2473cb05d7c"
    assert rows[0].native_exists is True


def test_find_astcenc_prefers_skill_tool() -> None:
    astcenc = find_astcenc()
    assert astcenc is not None
    assert astcenc.name == "astcenc-sse4.1.exe"

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


class FakeType:
    name = "Texture2D"


class FakeAssetsFile:
    name = "serialized.assets"


class FakeTextureData:
    m_Name = "empty-texture"
    m_Width = 0
    m_Height = 0
    image_data = b""
    m_StreamData = SimpleNamespace(path="", offset=0, size=0)

    @property
    def image(self) -> Image.Image:
        raise AssertionError("an empty stream path must not reach UnityPy image decoding")


class FakeTextureObject:
    type = FakeType()
    assets_file = FakeAssetsFile()
    path_id = 77

    def __init__(self, data: Any) -> None:
        self._data = data

    def read(self) -> Any:
        return self._data


def test_empty_texture_payload_is_indexed_without_loading_an_empty_dependency_path(
    tmp_path: Path,
) -> None:
    row, did_export = extractor.export_object(
        FakeTextureObject(FakeTextureData()),
        "Game_Data/resources.assets",
        {},
        tmp_path,
        "metadata",
    )

    assert did_export is False
    assert row["status"] == "indexed_no_image_data"
    assert row["export_kind"] == "metadata"
    assert row["export_path"] == ""
    assert row["width"] == 0
    assert row["height"] == 0
    assert "external stream reference" in row["remarks"]
    assert not (tmp_path / "images").exists()


def test_nonempty_external_texture_stream_keeps_normal_png_export(
    tmp_path: Path,
) -> None:
    image = Image.new("RGBA", (2, 3), (1, 2, 3, 4))
    data = SimpleNamespace(
        m_Name="streamed-texture",
        m_Width=2,
        m_Height=3,
        image_data=b"",
        m_StreamData=SimpleNamespace(path="archive:/texture.resS", offset=8, size=24),
        image=image,
    )
    try:
        row, did_export = extractor.export_object(
            FakeTextureObject(data),
            "Game_Data/sharedassets0.assets",
            {},
            tmp_path,
            "metadata",
        )
    finally:
        image.close()

    assert did_export is True
    assert row["status"] == "exported"
    assert row["export_kind"] == "png"
    assert row["width"] == 2
    assert row["height"] == 3
    assert (tmp_path / row["export_path"]).is_file()


def test_nonempty_inline_texture_data_keeps_normal_png_export(
    tmp_path: Path,
) -> None:
    image = Image.new("RGBA", (4, 5), (5, 6, 7, 8))
    data = SimpleNamespace(
        m_Name="inline-texture",
        m_Width=4,
        m_Height=5,
        image_data=b"non-empty-inline-payload",
        m_StreamData=SimpleNamespace(path="", offset=0, size=0),
        image=image,
    )
    try:
        row, did_export = extractor.export_object(
            FakeTextureObject(data),
            "Game_Data/resources.assets",
            {},
            tmp_path,
            "metadata",
        )
    finally:
        image.close()

    assert did_export is True
    assert row["status"] == "exported"
    assert row["export_kind"] == "png"
    assert row["width"] == 4
    assert row["height"] == 5
    assert (tmp_path / row["export_path"]).is_file()

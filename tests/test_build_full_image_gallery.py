from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_full_image_gallery.py"


def test_gallery_accepts_custom_original_prefix(tmp_path: Path) -> None:
    project = tmp_path / "GameMaker_UTMT_Exports"
    image = project / "Sprites" / "spr_test" / "spr_test_0.png"
    image.parent.mkdir(parents=True)
    Image.new("RGBA", (12, 8), (255, 0, 0, 255)).save(image)

    index = tmp_path / "index.csv"
    with index.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["relative_path", "width", "height", "mode", "has_alpha", "category", "size_bytes"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "relative_path": "Sprites/spr_test/spr_test_0.png",
                "width": "12",
                "height": "8",
                "mode": "RGBA",
                "has_alpha": "true",
                "category": "sprite_frame",
                "size_bytes": str(image.stat().st_size),
            }
        )

    gallery = tmp_path / "gallery"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(project),
            "--image-index",
            str(index),
            "--gallery-dir",
            str(gallery),
            "--original-prefix",
            "../GameMaker_UTMT_Exports/",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((gallery / "gallery_manifest.json").read_text(encoding="utf-8"))
    assert manifest["item_count"] == 1
    html = (gallery / "index.html").read_text(encoding="utf-8")
    assert "../GameMaker_UTMT_Exports/Sprites/spr_test/spr_test_0.png" in html


def test_gallery_can_write_report_folder_html_file_and_manifest(tmp_path: Path) -> None:
    project = tmp_path / "ExportedProject"
    image = project / "Assets" / "Texture2D" / "icon.png"
    image.parent.mkdir(parents=True)
    Image.new("RGBA", (16, 16), (0, 128, 255, 255)).save(image)

    index = tmp_path / "image_index.csv"
    with index.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["relative_path", "width", "height", "mode", "has_alpha", "category", "size_bytes"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "relative_path": "Assets/Texture2D/icon.png",
                "width": "16",
                "height": "16",
                "mode": "RGBA",
                "has_alpha": "true",
                "category": "UI",
                "size_bytes": str(image.stat().st_size),
            }
        )

    report_html = tmp_path / "2.报告" / "全量图片画廊.html"
    manifest = tmp_path / "4.临时目录" / "中间索引" / "全量图片画廊_manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(project),
            "--image-index",
            str(index),
            "--gallery-file",
            str(report_html),
            "--manifest",
            str(manifest),
            "--original-prefix",
            "../0.原始导出/AssetRipper/ExportedProject/",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert report_html.is_file()
    assert manifest.is_file()
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_data["item_count"] == 1
    html = report_html.read_text(encoding="utf-8")
    assert "../0.原始导出/AssetRipper/ExportedProject/Assets/Texture2D/icon.png" in html

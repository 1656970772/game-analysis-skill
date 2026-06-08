from __future__ import annotations

import csv
import sys
import wave
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from normalize_gamemaker_exports import normalize_exports  # noqa: E402


def write_png(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_normalizes_gamemaker_images_audio_and_animation_index(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    out = tmp_path / "out"
    write_png(exports / "Sprites" / "spr_dragon_idle" / "spr_dragon_idle_0.png", (16, 24), (255, 0, 0, 255))
    write_png(exports / "Sprites" / "spr_dragon_idle" / "spr_dragon_idle_1.png", (16, 24), (255, 0, 0, 128))
    write_png(exports / "TextureItems" / "Backgrounds" / "bg_city_0.png", (80, 45), (0, 0, 255, 255))
    write_png(exports / "EmbeddedTextures" / "0.png", (128, 128), (0, 255, 0, 255))
    write_wav(exports / "Sounds" / "audiogroup_default" / "snd_hit.wav")

    summary = normalize_exports(exports, out, title="Fixture")

    images = read_csv(out / "gamemaker_image_index.csv")
    animations = read_csv(out / "gamemaker_animation_index.csv")
    audio = read_csv(out / "gamemaker_audio_index.csv")

    assert summary["image_count"] == 4
    assert summary["audio_count"] == 1
    assert {row["category"] for row in images} == {"sprite_frame", "background", "embedded_texture"}
    assert animations == [
        {
            "sprite_name": "spr_dragon_idle",
            "frame_count": "2",
            "first_frame": "Sprites/spr_dragon_idle/spr_dragon_idle_0.png",
            "width_min": "16",
            "width_max": "16",
            "height_min": "24",
            "height_max": "24",
            "total_area": "768",
        }
    ]
    assert audio[0]["relative_path"] == "Sounds/audiogroup_default/snd_hit.wav"
    assert audio[0]["extension"] == ".wav"


def test_animation_frames_sort_by_numeric_suffix(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    out = tmp_path / "out"
    write_png(exports / "Sprites" / "spr_order" / "spr_order_10.png", (10, 10), (0, 0, 0, 255))
    write_png(exports / "Sprites" / "spr_order" / "spr_order_2.png", (20, 20), (0, 0, 0, 255))

    normalize_exports(exports, out)

    frames = read_csv(out / "gamemaker_sprite_frame_index.csv")
    assert [row["frame_index"] for row in frames] == ["2", "10"]

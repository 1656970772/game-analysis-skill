from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from asset_inventory import categorize  # noqa: E402


def test_categorizes_gamemaker_data_files() -> None:
    assert categorize(Path("data.win")) == "game-engine-packages"
    assert categorize(Path("audiogroup1.dat")) == "game-engine-packages"

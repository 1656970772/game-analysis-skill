#!/usr/bin/env python3
"""Build the engine-neutral game animation preview manifest."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = SKILL_ROOT / "references" / "game-animation-viewer-config.json"


def _load_klei_adapter_class() -> type:
    path = SCRIPT_DIR / "animation_adapters" / "klei_dst.py"
    spec = importlib.util.spec_from_file_location("_game_animation_klei_dst_adapter", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Klei animation adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.KleiDSTAdapter


def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Animation viewer config schema_version must be 1")
    adapters = value.get("adapters")
    if not isinstance(adapters, dict) or "klei_dst" not in adapters:
        raise KeyError("Animation viewer config requires adapters.klei_dst")
    return value


def safe_relative_output(study_root: Path, raw: str | Path) -> Path:
    root = study_root.resolve(strict=True)
    value = str(raw)
    posix = PurePosixPath(value.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or Path(value).is_absolute():
        raise ValueError("Manifest output must be a study-root relative path")
    output = (root / Path(*posix.parts)).resolve(strict=False)
    try:
        output.relative_to(root)
    except ValueError as error:
        raise ValueError("Manifest output escapes study-root") from error
    return output


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_game_animation_manifest(
    study_root: Path,
    config_path: Path = DEFAULT_CONFIG,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    root = study_root.resolve(strict=True)
    config = load_config(config_path.resolve(strict=True))
    adapter_config = config["adapters"]["klei_dst"]
    adapter_class = _load_klei_adapter_class()
    manifest = adapter_class(root, adapter_config).build_manifest()
    requested_output = output_path or adapter_config["manifest_output"]
    destination = safe_relative_output(root, requested_output)
    write_json_atomic(destination, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output",
        help="Study-root relative manifest path; defaults to config manifest_output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_game_animation_manifest(
        args.study_root, args.config, args.output
    )
    print(
        json.dumps(
            {
                "adapter": manifest["adapter"],
                "item_count": manifest["item_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

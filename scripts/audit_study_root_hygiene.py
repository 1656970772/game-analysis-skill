#!/usr/bin/env python3
"""Audit and optionally clean temporary browser profiles in a study root."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BROWSER_NAME_TOKENS = ("chrome", "edge", "browser")
BROWSER_MARKERS = ("Default", "Local State", "Crashpad", "ShaderCache", "GrShaderCache")


def is_browser_profile(path: Path) -> bool:
    lower_name = path.name.lower()
    if any(token in lower_name for token in BROWSER_NAME_TOKENS) and path.is_dir():
        return True
    return any((path / marker).exists() for marker in BROWSER_MARKERS)


def safe_child(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def audit_study_root(study_root: Path, clean_browser_profiles: bool = False) -> dict[str, Any]:
    study_root = study_root.resolve()
    ascii_work = study_root / "4.临时目录" / "_ascii_work"
    log_dir = study_root / "4.临时目录" / "工具日志"
    log_dir.mkdir(parents=True, exist_ok=True)

    detected: list[Path] = []
    if ascii_work.exists():
        for child in sorted(ascii_work.iterdir()):
            if child.is_dir() and is_browser_profile(child):
                detected.append(child)

    deleted: list[str] = []
    skipped: list[str] = []
    if clean_browser_profiles:
        for profile in detected:
            if not safe_child(profile, ascii_work):
                skipped.append(str(profile))
                continue
            shutil.rmtree(profile)
            deleted.append(str(profile))

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study_root": str(study_root),
        "ascii_work": str(ascii_work),
        "clean_browser_profiles": clean_browser_profiles,
        "detected_browser_profiles": [str(path) for path in detected],
        "detected_browser_profile_count": len(detected),
        "deleted_profiles": deleted,
        "deleted_count": len(deleted),
        "skipped_profiles": skipped,
    }
    (log_dir / "临时目录卫生审计.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True)
    parser.add_argument("--clean-browser-profiles", action="store_true")
    args = parser.parse_args()
    result = audit_study_root(Path(args.study_root), clean_browser_profiles=args.clean_browser_profiles)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

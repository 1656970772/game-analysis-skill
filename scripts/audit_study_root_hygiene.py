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
REQUIRED_ROOT_DIRS = ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]
REPORT_ALLOWED_EXTENSIONS = {".md", ".bat", ".pdf"}
MODEL_SIDECAR_FILES = {"metadata.json", "export-log.json", "dependencies.json", "missing-resources.json"}


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


def _issue(code: str, message: str, path: Path | None = None, severity: str = "error") -> dict[str, str]:
    payload = {"code": code, "severity": severity, "message": message}
    if path is not None:
        payload["path"] = str(path)
    return payload


def audit_study_root_contract(study_root: Path, mode: str = "full", engine: str = "") -> dict[str, Any]:
    study_root = study_root.resolve()
    issues: list[dict[str, str]] = []

    root_entries = list(study_root.iterdir()) if study_root.exists() else []
    root_entry_names = {entry.name for entry in root_entries}
    missing_root_dirs = [name for name in REQUIRED_ROOT_DIRS if name not in root_entry_names]
    extra_root_entries = [
        entry for entry in root_entries
        if entry.name not in REQUIRED_ROOT_DIRS
    ]
    for name in missing_root_dirs:
        issues.append(_issue("missing_required_root_dir", f"缺少必需根目录：{name}", study_root / name))
    for entry in extra_root_entries:
        issues.append(_issue("extra_root_entry", "study-root 根目录只能包含 5 个编号目录。", entry))

    report_dir = study_root / "2.报告"
    illegal_report_entries: list[dict[str, str]] = []
    if report_dir.exists():
        for entry in sorted(report_dir.iterdir(), key=lambda item: item.name.lower()):
            if entry.is_file() and entry.suffix.lower() not in REPORT_ALLOWED_EXTENSIONS:
                record = {
                    "path": str(entry),
                    "reason": "2.报告 只允许编号 Markdown 报告、BAT 入口和可选 PDF。",
                }
                illegal_report_entries.append(record)
                issues.append(_issue("illegal_report_entry", record["reason"], entry))

    image_launcher = report_dir / "打开图片画廊.bat"
    if image_launcher.is_file():
        text = image_launcher.read_text(encoding="utf-8", errors="ignore")
        if "\\tools\\GameImageGallery_" in text or "/tools/GameImageGallery_" in text:
            issues.append(_issue(
                "legacy_image_gallery_tools_path",
                "图片画廊 BAT 仍指向旧 tools/GameImageGallery 路径，应改为 built-in-tools。",
                image_launcher,
            ))

    if mode == "full" and engine.lower() == "unity":
        assetripper_project = study_root / "0.原始导出" / "AssetRipper" / "ExportedProject"
        if not assetripper_project.exists():
            issues.append(_issue(
                "missing_assetripper_exported_project",
                "Unity full 模式缺少 AssetRipper ExportedProject；场景/Prefab 工程级还原必须标为阻塞或重跑。",
                assetripper_project,
            ))

    classified_metadata: list[dict[str, str]] = []
    classified_root = study_root / "1.分类资源"
    def is_allowed_classified_metadata(path: Path) -> bool:
        try:
            relative = path.relative_to(classified_root)
        except ValueError:
            return False
        parts = relative.parts
        if len(parts) >= 3 and parts[0] == "模型" and path.name in MODEL_SIDECAR_FILES:
            return True
        return (
            len(parts) >= 3
            and parts[0] == "特效"
            and parts[1] in {"配置", "VFX材质", "VFX模型", "VFX动画"}
            and path.suffix.lower() == ".json"
        )

    if classified_root.exists():
        for path in classified_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".json", ".csv", ".md", ".txt"}:
                if is_allowed_classified_metadata(path):
                    continue
                record = {
                    "path": str(path),
                    "reason": "1.分类资源 中不允许放索引、日志或说明文档。",
                }
                classified_metadata.append(record)
                issues.append(_issue("classified_metadata_file", record["reason"], path))

    return {
        "root_dirs": sorted(root_entry_names),
        "missing_root_dirs": missing_root_dirs,
        "extra_root_entries": [str(entry) for entry in extra_root_entries],
        "illegal_report_entries": illegal_report_entries,
        "classified_metadata_files": classified_metadata,
        "issues": issues,
        "error_count": sum(1 for issue in issues if issue.get("severity") == "error"),
    }


def audit_study_root(
    study_root: Path,
    clean_browser_profiles: bool = False,
    mode: str = "full",
    engine: str = "",
) -> dict[str, Any]:
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
        "contract": audit_study_root_contract(study_root, mode=mode, engine=engine),
    }
    (log_dir / "临时目录卫生审计.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True)
    parser.add_argument("--clean-browser-profiles", action="store_true")
    parser.add_argument("--mode", default="full", choices=["full", "art", "code"])
    parser.add_argument("--engine", default="")
    args = parser.parse_args()
    result = audit_study_root(
        Path(args.study_root),
        clean_browser_profiles=args.clean_browser_profiles,
        mode=args.mode,
        engine=args.engine,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

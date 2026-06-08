from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from audit_study_root_hygiene import audit_study_root  # noqa: E402


def test_audit_detects_browser_profiles_without_cleaning_by_default(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    browser_cache = study_root / "4.临时目录" / "_ascii_work" / "chrome-gallery-check" / "Default" / "Cache"
    browser_cache.mkdir(parents=True)
    (browser_cache / "cache.bin").write_bytes(b"cache")
    ghidra_input = study_root / "4.临时目录" / "_ascii_work" / "GhidraInput"
    ghidra_input.mkdir(parents=True)
    (ghidra_input / "GameAssembly.dll").write_bytes(b"dll")

    result = audit_study_root(study_root, clean_browser_profiles=False)

    assert result["detected_browser_profile_count"] == 1
    assert result["deleted_count"] == 0
    assert browser_cache.exists()
    assert ghidra_input.exists()


def test_audit_cleans_only_browser_profiles_inside_ascii_work(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    browser_profile = study_root / "4.临时目录" / "_ascii_work" / "edge-gallery-check"
    (browser_profile / "Default" / "Service Worker").mkdir(parents=True)
    (browser_profile / "Local State").write_text("{}", encoding="utf-8")
    ghidra_project = study_root / "4.临时目录" / "_ascii_work" / "GhidraProjects"
    ghidra_project.mkdir(parents=True)
    (ghidra_project / "analysis.gpr").write_text("keep", encoding="utf-8")

    result = audit_study_root(study_root, clean_browser_profiles=True)

    assert result["deleted_count"] == 1
    assert not browser_profile.exists()
    assert ghidra_project.exists()
    log_path = study_root / "4.临时目录" / "工具日志" / "临时目录卫生审计.json"
    assert log_path.exists()

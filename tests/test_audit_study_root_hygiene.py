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


def test_audit_flags_report_html_and_legacy_gallery_launcher(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    for name in ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]:
        (study_root / name).mkdir(parents=True)
    (study_root / "2.报告" / "全量图片画廊.html").write_text("<html></html>", encoding="utf-8")
    (study_root / "2.报告" / "打开图片画廊.bat").write_text(
        r'set "GALLERY_EXE=C:\Skill\tools\GameImageGallery_1.0.0\GameImageGallery.exe"',
        encoding="utf-8",
    )

    result = audit_study_root(study_root, clean_browser_profiles=False, mode="full", engine="Unity")

    assert any(item["path"].endswith("全量图片画廊.html") for item in result["contract"]["illegal_report_entries"])
    assert any(issue["code"] == "legacy_image_gallery_tools_path" for issue in result["contract"]["issues"])
    assert any(issue["code"] == "missing_assetripper_exported_project" for issue in result["contract"]["issues"])


def test_audit_allows_required_model_sidecars_but_flags_stray_metadata(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    for name in ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]:
        (study_root / name).mkdir(parents=True)
    model_dir = study_root / "1.分类资源" / "模型" / "角色A"
    model_dir.mkdir(parents=True)
    for name in ["metadata.json", "export-log.json", "dependencies.json", "missing-resources.json"]:
        (model_dir / name).write_text("{}", encoding="utf-8")
    (model_dir / "notes.json").write_text("{}", encoding="utf-8")

    result = audit_study_root(study_root, clean_browser_profiles=False, mode="art", engine="Unity")
    flagged = result["contract"]["classified_metadata_files"]

    assert len(flagged) == 1
    assert flagged[0]["path"].endswith("notes.json")


def test_audit_allows_vfx_preview_cards(tmp_path: Path) -> None:
    study_root = tmp_path / "Study"
    for name in ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]:
        (study_root / name).mkdir(parents=True)
    for bucket in ["配置", "VFX材质", "VFX模型", "VFX动画"]:
        target = study_root / "1.分类资源" / "特效" / bucket / "sample.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")

    result = audit_study_root(study_root, clean_browser_profiles=False, mode="art", engine="Unity")

    assert result["contract"]["classified_metadata_files"] == []

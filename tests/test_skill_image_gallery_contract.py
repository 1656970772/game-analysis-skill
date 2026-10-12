from __future__ import annotations

import json
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_json(relative_path: str) -> object:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8-sig"))


def assert_contains_all(relative_path: str, terms: list[str]) -> None:
    text = read_text(relative_path)
    missing = [term for term in terms if term not in text]
    assert missing == [], f"{relative_path} missing terms: {missing}"


def assert_not_contains_any(relative_path: str, terms: list[str]) -> None:
    text = read_text(relative_path)
    found = [term for term in terms if term in text]
    assert found == [], f"{relative_path} contains stale terms: {found}"


def extract_section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def test_skill_declares_fixed_exe_gallery_default_flow_contract() -> None:
    skill = read_text("SKILL.md")

    required_terms = [
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "打开图片画廊.bat",
        "build_image_gallery_manifest.py",
        "create_image_gallery_launcher.py",
        "--study-root",
        "--manifest",
        "--images-root",
        "self-test",
        "4.临时目录/中间索引/图片画廊/thumbnails",
        "errors.jsonl",
    ]

    for term in required_terms:
        assert term in skill


def test_skill_common_entrypoint_requires_gallery_manifest_then_launcher_flow() -> None:
    skill = read_text("SKILL.md")
    entrypoint = extract_section(skill, "## 通用执行入口")

    required_terms = [
        "1.分类资源/图片",
        "scripts/build_image_gallery_manifest.py --study-root <study-root> --skill-root <skill-root>",
        "scripts/create_image_gallery_launcher.py --study-root <study-root> --skill-root <skill-root>",
        "2.报告/打开图片画廊.bat",
        "4.临时目录/中间索引/图片画廊/manifest.json",
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "--images-root",
    ]

    for term in required_terms:
        assert term in entrypoint

    manifest_index = entrypoint.index("scripts/build_image_gallery_manifest.py")
    launcher_index = entrypoint.index("scripts/create_image_gallery_launcher.py")
    learning_index = entrypoint.index("scripts/build_learning_indices.py")

    assert manifest_index < launcher_index < learning_index


def test_skill_resource_navigation_no_longer_uses_legacy_gallery_wording() -> None:
    skill = read_text("SKILL.md")

    assert "HTML 画廊、EXE 壳" not in skill


def test_directory_spec_declares_fixed_exe_gallery_contract() -> None:
    directory_spec = read_text("references/directory-spec.md")

    required_terms = [
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--study-root",
        "--manifest",
        "--images-root",
    ]

    for term in required_terms:
        assert term in directory_spec

    assert (
        "不要在每个 study-root" in directory_spec
        or "不得每项目" in directory_spec
    )


def test_multi_agent_prompts_declare_gallery_bat_manifest_and_self_test_contract() -> None:
    prompts = read_text("references/multi-agent-prompts.md")

    required_terms = [
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--study-root",
        "--manifest",
        "--images-root",
        "2.报告/打开图片画廊.bat",
        "4.临时目录/中间索引/图片画廊/manifest.json",
        "BAT 包含",
    ]

    for term in required_terms:
        assert term in prompts

    assert "检查 BAT 包含 `--study-root`、`--manifest` 和重复 `--images-root`" in prompts


def test_gamemaker_workflow_declares_gallery_cli_arguments_and_self_test() -> None:
    assert_contains_all(
        "references/gamemaker-datawin-workflow.md",
        [
            "--image-root",
            "--skill-root",
            "self-test",
            "--images-root",
        ],
    )


def test_gamemaker_recommended_directory_uses_standard_numbered_reports() -> None:
    workflow = read_text("references/gamemaker-datawin-workflow.md")

    legacy_report_names = [
        "交付摘要.md",
        "整体美术素材分析文档.md",
        "场景美术分析.md",
        "角色美术分析.md",
        "主角美术分析.md",
        "代码导出说明.md",
        "01_资源资产清单.md",
    ]

    for report_name in legacy_report_names:
        assert report_name not in workflow

    assert "00_研究总览.md" in workflow
    assert "01_美术资源规格表.md" in workflow


def test_full_extraction_workflow_declares_fixed_exe_self_test_and_bat_arguments() -> None:
    assert_contains_all(
        "references/full-extraction-workflow.md",
        [
            "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
            "self-test",
            "--study-root",
            "--manifest",
            "--images-root",
            "2.报告/打开图片画廊.bat",
            "4.临时目录/中间索引/图片画廊/manifest.json",
        ],
    )


def test_deliverable_templates_use_fixed_exe_gallery_outputs() -> None:
    template = read_text("references/deliverable-templates.md")

    required_terms = [
        "2.报告/打开图片画廊.bat",
        "4.临时目录/中间索引/图片画廊/manifest.json",
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--study-root",
        "--manifest",
        "--images-root",
    ]
    banned_terms = [
        "全量图片画廊/index.html",
        "GameArtStudyGallery.exe",
        "全量图片画廊 HTML",
        "HTML/EXE",
    ]

    for term in required_terms:
        assert term in template
    for term in banned_terms:
        assert term not in template


def test_branch_completion_checks_use_bat_gallery_default() -> None:
    checks = read_text("references/branch-completion-checks.md")

    assert "打开图片画廊.bat" in checks
    assert "HTML 画廊" not in checks
    assert "manifest、缩略图、错误日志" in checks
    assert "CSV/JSON、验证日志、manifest" in checks


def test_cocos_workflow_uses_fixed_exe_gallery_default() -> None:
    workflow = read_text("references/cocos-creator-astc-workflow.md")

    required_terms = [
        "2.报告/打开图片画廊.bat",
        "4.临时目录/中间索引/图片画廊/manifest.json",
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--study-root",
        "--manifest",
        "--images-root",
    ]
    banned_terms = [
        "HTML 画廊必须可本地打开",
        "浏览器中 `figure` 数",
        "控制台 error 为 0",
        "画廊用 fetch 读取本地 JSON",
    ]

    for term in required_terms:
        assert term in workflow
    for term in banned_terms:
        assert term not in workflow


def test_local_export_preview_requirements_declare_fixed_exe_contract() -> None:
    requirements = read_text("references/local-export-preview-requirements.md")

    required_terms = [
        "2.报告/打开图片画廊.bat",
        "4.临时目录/中间索引/图片画廊/manifest.json",
        "4.临时目录/中间索引/图片画廊/thumbnails",
        "4.临时目录/中间索引/图片画廊/errors.jsonl",
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--study-root",
        "--manifest",
        "--images-root",
    ]
    banned_terms = [
        "可选 JSON/Markdown/HTML",
        "打包为离线桌面工具",
        "EXE 打包后",
    ]

    for term in required_terms:
        assert term in requirements
    for term in banned_terms:
        assert term not in requirements


def test_fixed_gallery_target_docs_do_not_use_stale_preview_ui_phrases() -> None:
    stale_terms = [
        "右键打开素材目录",
        "右键打开",
        "右键只打开",
        "右键定位",
        "右键菜单",
        "right-click opens",
        "right click opens",
        "right-click locate",
        "right click locate",
        "再次点击预览图",
        "点背景",
        "点图片",
        'event.target === $("previewOverlay")',
        'event.target === $("previewImage")',
    ]
    target_docs = [
        "references/full-extraction-workflow.md",
        "references/local-export-preview-requirements.md",
        "references/multi-agent-prompts.md",
        "references/gamemaker-datawin-workflow.md",
        "references/deliverable-templates.md",
        "docs/superpowers/specs/2026-06-09-图片预览客户端设计.md",
        "docs/superpowers/plans/2026-06-09-图片预览客户端实施计划.md",
    ]

    for relative_path in target_docs:
        assert_not_contains_any(relative_path, stale_terms)


def test_gallery_design_and_plan_docs_declare_current_preview_interaction_contract() -> None:
    required_terms = [
        "点击卡片打开预览",
        "鼠标滚轮缩放图片",
        "按住鼠标左键拖拽平移图片",
        "右上角 X/关闭按钮或 Esc 关闭",
        "`打开目录` 按钮",
        "系统文件管理器",
        "打开并定位当前图片",
        "右键不触发操作",
        "不属于默认工作流",
    ]
    target_docs = [
        "docs/superpowers/specs/2026-06-09-图片预览客户端设计.md",
        "docs/superpowers/plans/2026-06-09-图片预览客户端实施计划.md",
    ]

    for relative_path in target_docs:
        assert_contains_all(relative_path, required_terms)


def test_tooling_manifest_marks_legacy_html_gallery_template_as_compatibility() -> None:
    manifest = read_json("references/tooling-manifest.json")
    tools = manifest["tools"]
    legacy = next(item for item in tools if item["name"] == "Electron gallery viewer template")
    game_gallery = next(item for item in tools if item["name"] == "GameImageGallery")

    assert "兼容" in legacy["purpose"] or "legacy" in legacy["purpose"].lower()
    assert "not the default" in legacy["purpose"].lower() or "不作为默认" in legacy["purpose"]
    assert game_gallery["local_path"] == "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe"
    assert game_gallery["license"]
    assert len(game_gallery["sha256"]) == 64
    assert game_gallery["build_source"] == "assets/image-gallery-viewer-src"
    assert game_gallery["build_script"] == "scripts/build_image_gallery_viewer_exe.ps1"


def test_game_image_gallery_hash_matches_local_tool_when_present() -> None:
    manifest = read_json("references/tooling-manifest.json")
    game_gallery = next(item for item in manifest["tools"] if item["name"] == "GameImageGallery")
    exe_path = ROOT / game_gallery["local_path"]
    if not exe_path.is_file():
        return

    digest = hashlib.sha256(exe_path.read_bytes()).hexdigest().upper()
    assert digest == game_gallery["sha256"]


def test_gallery_design_docs_use_placeholders_not_local_machine_paths() -> None:
    banned_terms = [
        r"E:\AI_Projects\GameAnalysis\示例项目",
        r"C:\Users\Administrator\.codex\skills\game-analysis",
        r"E:\Program Files",
        r"C:\Games",
        r"D:\Games",
        "Karma Exorcist",
        "RimWorld",
        "SomeUnrealGame",
        "SomeUnityGame",
        "GameMakerExample",
    ]
    docs = [
        "docs/superpowers/specs/2026-06-09-图片预览客户端设计.md",
        "docs/superpowers/plans/2026-06-09-图片预览客户端实施计划.md",
        "references/full-extraction-workflow.md",
        "test-prompts.json",
    ]

    for relative_path in docs:
        text = read_text(relative_path)
        for term in banned_terms:
            assert term not in text, f"{relative_path} contains local/example-specific term: {term}"


def test_target_docs_do_not_restore_old_html_gallery_portable_exe_deliverable() -> None:
    banned = "2.报告/全量图片画廊.html 和 portable EXE"

    for relative_path in [
        "SKILL.md",
        "references/multi-agent-prompts.md",
        "references/gamemaker-datawin-workflow.md",
        "references/full-extraction-workflow.md",
        "references/deliverable-templates.md",
        "references/branch-completion-checks.md",
    ]:
        assert banned not in read_text(relative_path)


def test_test_prompts_json_declares_gallery_contract_without_portable_exe() -> None:
    prompts = read_json("test-prompts.json")
    expected_text = "\n".join(item["expected"] for item in prompts)

    required_terms = [
        "打开图片画廊.bat",
        "manifest.json",
        "built-in-tools/GameImageGallery_1.0.0/GameImageGallery.exe",
        "self-test",
        "--images-root",
    ]

    for term in required_terms:
        assert term in expected_text

    assert "2.报告/全量图片画廊.html 和 portable EXE" not in expected_text
    assert "portable EXE" not in expected_text


def test_legacy_gallery_scripts_are_marked_as_compatibility_tools() -> None:
    combined = "\n".join(
        [
            read_text("SKILL.md"),
            read_text("references/full-extraction-workflow.md"),
            read_text("references/gamemaker-datawin-workflow.md"),
        ]
    )

    for tool in ["build_full_image_gallery.py", "build_gallery_exe.ps1"]:
        index = combined.find(tool)
        assert index != -1
        nearby = combined[max(0, index - 40) : index + len(tool) + 80]
        assert "兼容工具" in nearby

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_skill_declares_full_project_study_contract():
    skill = read_text("SKILL.md")

    required_terms = [
        "研究模式",
        "学习目的",
        "美术规格",
        "代码逻辑",
        "美术风格",
        "动画手感",
        "全面学习",
        "需求确认",
        "交付目录",
        "未知引擎",
        "自扩展",
        "使用后迭代",
    ]

    for term in required_terms:
        assert term in skill


def test_deliverable_templates_cover_technical_and_learning_outputs():
    templates = read_text("references/deliverable-templates.md")

    required_terms = [
        "游戏项目研究总览",
        "技术拆解报告",
        "代码实现地图",
        "玩法系统拆解",
        "场景关卡搭建复盘",
        "动画与手感拆解",
        "原创化学习清单",
        "使用后复盘与 Skill 迭代记录",
    ]

    for term in required_terms:
        assert term in templates


def test_skill_declares_mandatory_output_directory_contract():
    skill = read_text("SKILL.md")

    required_terms = [
        "交付目录",
        "0.原始导出",
        "1.分类资源",
        "2.报告",
        "3.代码",
        "4.临时目录",
        "1.分类资源/音频",
        "1.分类资源/渲染",
        "禁止创建",
        "4.临时目录/_ascii_work",
    ]

    for term in required_terms:
        assert term in skill


def test_deliverable_templates_include_output_audit_template():
    templates = read_text("references/deliverable-templates.md")

    required_terms = [
        "验证日志",
        "交付核对表",
        "必做目录",
        "必填内容",
        "实际输出",
        "验收状态",
        "缺失或阻塞原因",
    ]

    for term in required_terms:
        assert term in templates


def test_skill_groups_engine_flows_with_their_completion_checks():
    skill = read_text("SKILL.md")

    required_terms = [
        "通用执行入口",
        "全局完成前检查",
        "references/branch-completion-checks.md",
        "references/full-extraction-workflow.md",
        "references/gamemaker-datawin-workflow.md",
        "references/unreal-pak-workflow.md",
    ]

    for term in required_terms:
        assert term in skill

    assert "\n## 执行流程\n" not in skill
    assert "\n## 完成前检查\n" not in skill


def test_skill_declares_dynamic_subagent_dispatch_contract():
    skill = read_text("SKILL.md")
    prompts = read_text("references/multi-agent-prompts.md")

    required_skill_terms = [
        "动态子 agent 调度",
        "任务图",
        "互不重叠的写入范围",
        "DONE_WITH_CONCERNS",
        "NEEDS_CONTEXT",
        "BLOCKED",
        "references/multi-agent-prompts.md",
    ]
    for term in required_skill_terms:
        assert term in skill

    required_prompt_terms = [
        "角色选择规则",
        "依赖顺序",
        "任务输出契约",
        "主 agent 汇总",
        "只写入自己的任务目录",
    ]
    for term in required_prompt_terms:
        assert term in prompts


def test_dynamic_subagent_references_are_stage_safe():
    prompts = read_text("references/multi-agent-prompts.md")
    directory_spec = read_text("references/directory-spec.md")

    required_terms = [
        "执行型角色不得重新运行 detect_engine.py 或 asset_inventory.py",
        "角色八A：Unity AssetRipper 导出",
        "角色八B：UnityPy 可读资源导出",
        "角色八C：AssetRipper 工程分析",
        "角色十一A：GameMaker UTMT 原始导出",
        "角色十一B：GameMaker 归一化索引",
        "角色十一C：GameMaker 分类与画廊",
        "两阶段审查",
        "回修后必须复审",
    ]
    for term in required_terms:
        assert term in prompts

    assert "## 角色八：全量导出执行" not in prompts
    assert "## 角色十一：GameMaker/data.win 全量导出执行" not in prompts
    assert "子任务产物/" in directory_spec


def test_il2cpp_workflow_uses_numbered_directories():
    workflow = read_text("references/il2cpp-native-code-workflow.md")

    forbidden_terms = [
        "<study-root>\\ExportedProject",
        "<study-root>\\IL2CPP_Dump",
        "<study-root>\\GhidraProjects",
        "\n  IL2CPP_Dump/",
        "\n  GhidraProjects/",
        "\n  IL2CPP代码分析/",
    ]
    for term in forbidden_terms:
        assert term not in workflow

    required_terms = [
        "<study-root>\\0.原始导出\\AssetRipper\\ExportedProject",
        "<study-root>\\3.代码\\IL2CPP_Dump",
        "<study-root>\\4.临时目录\\GhidraProjects",
        "<study-root>\\3.代码\\Native映射",
    ]
    for term in required_terms:
        assert term in workflow


def test_final_outputs_reference_per_document_templates():
    template_paths = [
        "references/templates/00_研究总览模板.md",
        "references/templates/01_美术资源规格表模板.md",
        "references/templates/02_美术风格与色彩速查模板.md",
        "references/templates/03_动画与手感帧数据模板.md",
        "references/templates/04_场景关卡搭建模板.md",
        "references/templates/05_技术架构拆解模板.md",
        "references/templates/06_玩法系统拆解模板.md",
        "references/templates/07_渲染管线拆解模板.md",
        "references/templates/08_命名规范汇总模板.md",
        "references/templates/09_可迁移技术清单模板.md",
    ]

    for template_path in template_paths:
        template = ROOT / template_path
        assert template.exists()
        content = template.read_text(encoding="utf-8")
        assert "必填分析细节大纲" in content
        assert "引擎分支" in content
        assert "引用来源" in content


def test_learning_output_contract_keeps_reports_reader_facing():
    directory_spec = read_text("references/directory-spec.md")
    workflow = read_text("references/full-extraction-workflow.md")
    checks = read_text("references/branch-completion-checks.md")

    required_terms = [
        "2.报告/00_研究总览.md",
        "CSV/JSON、验证日志、manifest、分类摘要统一写入 `4.临时目录`",
        "空目录不放 `_empty_reason.md`",
        "4.临时目录/中间索引/学习输出",
        "audit_study_root_hygiene.py",
        "build_learning_indices.py",
    ]

    combined = "\n".join([directory_spec, workflow, checks])
    for term in required_terms:
        assert term in combined


def test_unreal_blender_preview_mentions_quoted_python_launch():
    workflow = read_text("references/unreal-pak-workflow.md")
    launcher = ROOT / "scripts/launch_blender_python.cmd"

    assert launcher.exists()

    required_workflow_terms = [
        "--background --python",
        "Start-Process",
        "带空格的目录名",
        "从 `__file__` 推导 study-root",
        "T_Noise",
    ]
    for term in required_workflow_terms:
        assert term in workflow


def test_deprecated_output_paths_only_appear_as_forbidden_examples():
    allowed = {
        ROOT / "SKILL.md",
        ROOT / "references" / "directory-spec.md",
        ROOT / "references" / "common-pitfalls.md",
        Path(__file__).resolve(),
    }
    deprecated_terms = [
        "0.完整资源导出目录",
        "1.最终产出",
        "2.主角资源",
        "3.代码导出",
        "HTML_画廊入口",
        "交付状态清单.md",
        "__ascii_work",
    ]

    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if path in allowed or not path.is_file():
            continue
        if any(part in {".git", ".pytest_cache", "__pycache__", "tools"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".md", ".py", ".ps1", ".js", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [term for term in deprecated_terms if term in text]
        if hits:
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {', '.join(hits)}")

    assert offenders == []

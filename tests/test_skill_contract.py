import json
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


def test_klei_dst_workflow_is_integrated_with_reports():
    skill = read_text("SKILL.md")
    branch_checks = read_text("references/branch-completion-checks.md")
    workflow = read_text("references/klei-dst-workflow.md")

    combined = "\n".join([skill, branch_checks, workflow])
    required_terms = [
        "Klei / Don't Starve Together",
        "references/klei-dst-workflow.md",
        "scripts/export_klei_dst_project.py",
        "scripts/convert_klei_dst_resources.py",
        "scripts/analyze_klei_dyn_boundary.py",
        "scripts/analyze_klei_worldsim_binary.py",
        "scripts/build_klei_dst_reports.py",
        "KleiDST剩余阻塞.md",
        "dyn_boundary_inventory.csv",
        "dyn_boundary_inventory_summary.json",
        "worldsim_lua_bindings.csv",
        "ghidra_worldsim_targets_autogen.csv",
        "klei_texture_conversion_index.csv",
        "klei_animation_xml_index.csv",
        "klei_fsb_conversion_index.csv",
    ]
    for term in required_terms:
        assert term in combined


def test_klei_worldsim_native_tooling_is_declared():
    requirements = read_text("requirements.txt")
    tooling = json.loads(read_text("references/tooling-manifest.json"))
    tools = {tool["name"]: tool for tool in tooling["tools"]}

    assert "pefile==2024.8.26" in requirements
    assert "capstone==5.0.9" in requirements
    assert tools["pefile"]["local_path"] == "tools/python-env/Lib/site-packages/pefile.py"
    assert tools["capstone"]["local_path"] == "tools/python-env/Lib/site-packages/capstone"
    assert tools["Ghidra"]["archive_path"] == "tools/ghidra_12.0.4_PUBLIC_20260303.zip"
    assert "targeted decompile" in tools["Ghidra"]["purpose"]


def test_skill_declares_dynamic_subagent_dispatch_contract():
    skill = read_text("SKILL.md")
    prompts = read_text("references/multi-agent-prompts.md")

    required_skill_terms = [
        "动态子 agent 调度",
        "默认使用动态子 agent 调度",
        "除非用户明确要求不使用多 agent",
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
        "默认使用动态子 agent 调度",
        "除非用户明确要求不使用多 agent",
        "角色选择规则",
        "依赖顺序",
        "任务输出契约",
        "主 agent 汇总",
        "只写入自己的任务目录",
    ]
    for term in required_prompt_terms:
        assert term in prompts

    assert "任务可拆成互不重叠的阶段或产物时" not in skill


def test_unity_vfx_preview_dynamic_workflow_is_integrated():
    skill = read_text("SKILL.md")
    local_preview = read_text("references/local-export-preview-requirements.md")
    full_workflow = read_text("references/full-extraction-workflow.md")
    branch_checks = read_text("references/branch-completion-checks.md")
    directory_spec = read_text("references/directory-spec.md")
    image_preview_plan = read_text("docs/superpowers/plans/2026-06-09-图片预览客户端实施计划.md")
    tooling = json.loads(read_text("references/tooling-manifest.json"))

    combined = "\n".join([skill, local_preview, full_workflow, branch_checks, directory_spec, image_preview_plan])

    forbidden_terms = [
        "第一版 VFX 预览只做配置卡和资源关联，不声称还原 Unity runtime 粒子模拟",
        "第一版只提供配置卡、贴图/材质/mesh/animation 关联和 spritesheet/图片层预览入口，不还原 Unity runtime 粒子模拟",
        "VFX 分类与最小预览",
        "最小预览卡片",
        "最小预览而非 Unity runtime 模拟",
        "Mesh 配置卡",
        "在 VFX manifest 标注 preview_mode",
        "`2.报告/` 只放学习者阅读产物：编号报告、图片画廊 BAT 入口、可选 PDF",
        "`2.报告/` 只放编号报告、图片画廊 BAT 入口 `打开图片画廊.bat` 和可选 PDF",
        "`2.报告/` 没有 CSV、JSON、验证日志、manifest 或分类摘要；图片画廊入口仅保留 `打开图片画廊.bat`，模型预览入口仅保留 `打开模型预览.bat` 和配置化兼容入口（默认 `模型预览.bat`）",
        "`2.报告/` 只包含 `00_研究总览.md` 到 `09_可迁移技术清单.md`、`打开图片画廊.bat` 和可选 PDF",
    ]
    for term in forbidden_terms:
        assert term not in combined

    required_terms = [
        "GameVfxViewer",
        "built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe",
        "GAME_ANALYSIS_VFX_VIEWER",
        "GAME_ANALYSIS_SKILL_ROOT",
        "scripts/build_vfx_preview_manifest.py",
        "scripts/export_unity_vfx_reconstruction.py",
        "scripts/create_vfx_viewer_launcher.py",
        "references/vfx-unity-reconstruction-schema.json",
        "1.分类资源/特效/",
        "4.临时目录/中间索引/VFX预览/manifest.json",
        "reconstructed/",
        "unity-export/",
        "unity-export/Assets/GameAnalysisVfxPreview",
        "2.报告/打开特效预览.bat",
        "preview-cache-dir",
        "best-effort",
        "errors",
        "status",
        "ParticleSystem",
        "TrailRenderer",
        "LineRenderer",
        "Prefab",
        "Material",
        "metadata",
        "bootstrap",
    ]
    for term in required_terms:
        assert term in combined

    directory_required_terms = [
        "4.临时目录/中间索引/VFX预览/",
        "manifest.json",
        "errors.jsonl",
        "reconstructed/",
        "unity-export/Assets/GameAnalysisVfxPreview",
        "best-effort",
        "preview_status",
        "reconstruction_status",
        "unity_export_status",
    ]
    for term in directory_required_terms:
        assert term in directory_spec

    report_directory_required_by_doc = {
        "directory_spec": directory_spec,
        "branch_checks": branch_checks,
        "full_workflow": full_workflow,
    }
    required_report_terms = [
        "打开图片画廊.bat",
        "打开模型预览.bat",
        "模型预览.bat",
        "打开特效预览.bat",
        "可选 PDF",
        "CSV/JSON",
        "验证日志",
        "manifest",
        "缩略图",
        "分类摘要",
    ]
    for _name, content in report_directory_required_by_doc.items():
        for term in required_report_terms:
            assert term in content

    required_skill_final_check_terms = [
        "全局完成前检查",
        "图片画廊入口仅保留 `打开图片画廊.bat`",
        "模型预览入口仅保留 `打开模型预览.bat`，不得再生成 `模型预览.bat` 等重复别名",
        "特效预览入口仅保留 `打开特效预览.bat`",
    ]
    for term in required_skill_final_check_terms:
        assert term in skill

    vfx_viewer_entries = [
        tool for tool in tooling["tools"] if tool.get("name") == "GameVfxViewer"
    ]
    assert vfx_viewer_entries
    vfx_viewer = vfx_viewer_entries[0]
    assert vfx_viewer["local_path"] == "built-in-tools/GameVfxViewer_1.0.0/GameVfxViewer.exe"
    assert vfx_viewer["build_source"] == "assets/vfx-viewer-src"
    assert vfx_viewer["build_script"] == "scripts/build_vfx_viewer_tool.ps1"
    assert vfx_viewer["sha256"] == "19FDCB007F362FF3840C208B0809E02283CB4C56B04E78E76663C316F0B45846"
    assert "Unity" in vfx_viewer["purpose"]
    assert "best-effort" in vfx_viewer["purpose"]


def test_vfx_viewer_uses_semantic_tag_render_modes():
    renderer = read_text("assets/vfx-viewer-src/renderer/renderer.js")

    required_terms = [
        "semanticRenderMode",
        "preferredInitialVfxItem",
        "originalPrefabItems",
        "original_vfx_prefab_export",
        "original_prefab",
        "OriginalAssets",
        "Prefab",
        "readOriginalPrefabPreview",
        "drawOriginalPrefabParticlePreview",
        "prefabParticleSystems",
        "particle_systems",
        "drawSemanticParticleSystem",
        "drawFireEffect",
        "drawSmokeEffect",
        "drawSparkEffect",
        "drawSlashEffect",
        "drawMagicEffect",
        "itemTags(selected)",
        "vfx:fire",
        "vfx:smoke",
        "vfx:spark",
        "vfx:slash",
        "vfx:magic",
        "vfx:particle",
    ]

    for term in required_terms:
        assert term in renderer

    particle_branch = renderer.split("function drawFrame", 1)[1]
    assert "drawSemanticParticleSystem(dt, now)" in particle_branch


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
        "每一轮修复必须启动新的 Fixer agent",
        "复现步骤、临时 probe、测试用例、实际输出和期望输出",
        "先运行 Reviewer 提供的 probe",
        "转成 RED 测试",
        "目标测试、相关回归测试和必要的全量测试",
        "主 agent 独立检查 diff、运行关键命令并确认工作树状态",
    ]
    for term in required_terms:
        assert term in prompts

    assert "## 角色八：全量导出执行" not in prompts
    assert "## 角色十一：GameMaker/data.win 全量导出执行" not in prompts
    assert "交回原子任务或新修复子 agent" not in prompts
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
        "CSV/JSON、验证日志、manifest、缩略图、分类摘要统一写入 `4.临时目录`",
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


def test_godot_pck_full_workflow_is_integrated():
    skill = read_text("SKILL.md")
    checks = read_text("references/branch-completion-checks.md")
    workflow = read_text("references/godot-pck-workflow.md")
    pitfalls = read_text("references/common-pitfalls.md")
    extension = read_text("references/tool-extension-and-iteration.md")
    prompts = read_text("references/multi-agent-prompts.md")
    full_workflow = read_text("references/full-extraction-workflow.md")
    tooling = json.loads(read_text("references/tooling-manifest.json"))
    test_prompts = json.loads(read_text("test-prompts.json"))

    combined = "\n".join([skill, checks, workflow, pitfalls, extension, prompts, full_workflow])
    required_terms = [
        "Godot 3.x",
        "references/godot-pck-workflow.md",
        "scripts/analyze_godot_pck.py",
        "scripts/export_godot_pck_project.ps1",
        "scripts/organize_godot_project_export.py",
        "references/godot-resource-classification.json",
        "0.原始导出/GDRETools恢复",
        "4.临时目录/中间索引/Godot",
        "detect→PCK probe→GDRE list→scripts-only→full recover→organize",
        "tool",
        "EditorPlugin",
        "GDNative",
        "不运行主场景",
        "有损恢复",
        "rtk",
        "Start-Process",
        "角色十三A：Godot PCK 探测与 GDRETools 恢复",
        "角色十三B：Godot 恢复产物组织与静态验证",
    ]
    for term in required_terms:
        assert term in combined
    tools = {entry["name"]: entry for entry in tooling["tools"]}
    gdre = tools["GDRETools"]
    assert gdre["version"] == "2.6.0"
    assert gdre["local_path"] == "tools/GDRETools_2.6.0/gdre_tools.exe"
    assert gdre["archive_sha256"] == "1C459C2CD575DC06DA09757EF1375A451B9CF421603913CFB3B82D5ED0F3B06D"
    assert gdre["exe_sha256"] == "F7D25B387F3E67D3E0B65B6B480C01B09D0D7AF5DCE675CAF6ED105B8454BB7F"
    assert gdre["license"] == "MIT"

    godot = tools["Godot Engine 3.2.3"]
    assert godot["local_path"] == "tools/Godot_3.2.3_stable_win64/Godot_v3.2.3-stable_win64.exe"
    assert godot["archive_sha512"] == (
        "73E8376F2B1E6B884C4604BD6DF30A8AEA73975FA53D8A8D2F8D62C8CDA6D3"
        "B73D00BB2868A91635CD8C79A78F50CDE262FD3C325BDB9488CB16A7F1C231DD16"
    )
    assert godot["archive_sha256"] == "9C6232AAF1F34507DA75B286538A69E673B368D57A68BA0237B9EA9D6A764065"
    assert godot["exe_sha256"] == "E53B6EA7964F3A89108C3DFDFECD8E98B64E822EC0846C47CE541897EA2E8664"
    assert godot["license"] == "MIT"

    full_prompts = [item for item in test_prompts if item.get("id") == 15]
    assert len(full_prompts) == 1
    assert "Godot" in full_prompts[0]["prompt"]
    assert "PCK" in full_prompts[0]["expected"]
    assert "GDRETools" in full_prompts[0]["expected"]
    assert "从引擎探测与恢复日志确定版本" in full_prompts[0]["expected"]
    assert "本案探测为 3.2.3 时才使用" in full_prompts[0]["expected"]
    assert "固定使用精确 Godot 3.2.3" not in full_prompts[0]["expected"]


def test_loose_json_analysis_is_discoverable_and_has_completion_gates():
    skill = read_text("SKILL.md")
    workflow = read_text("references/full-extraction-workflow.md")
    checks = read_text("references/branch-completion-checks.md")

    for path in [
        "scripts/analyze_loose_json_tables.py",
        "references/loose-json-analysis-config.json",
    ]:
        assert (ROOT / path).is_file()
        assert path in skill

    required_terms = [
        "--expected-file-count",
        "--expected-total-bytes",
        "源前后快照",
        "确定性 SHA-256 抽样",
        "不复制字段值",
        "只是检索信号",
        "系统候选",
        "不冒充运行时边界或实体数",
    ]
    combined = "\n".join([workflow, checks])
    for term in required_terms:
        assert term in combined


def test_godot_force_bytecode_version_is_evidence_driven_not_hardcoded():
    workflow = read_text("references/godot-pck-workflow.md")
    scripts_only = workflow.split("### 4. scripts-only", 1)[1].split(
        "### 5. full recover", 1
    )[0]

    assert '-ForceBytecodeVersion "3.2.3"' not in scripts_only
    assert "PCK probe" in scripts_only
    assert "恢复日志" in scripts_only
    assert "精确版本证据" in scripts_only


def test_godot_pillow_runtime_dependency_is_fully_declared():
    tooling = json.loads(read_text("references/tooling-manifest.json"))
    pillow_entries = [entry for entry in tooling["tools"] if entry["name"] == "Pillow"]

    assert len(pillow_entries) == 1
    pillow = pillow_entries[0]
    assert pillow["version"] == "12.2.0"
    assert pillow["local_path"] == "tools/python-env/Lib/site-packages/PIL"
    assert pillow["source"] == "https://pypi.org/project/Pillow/"
    assert pillow["license"] == "MIT-CMU"
    assert "image" in pillow["purpose"].lower()
    assert "verify/load" in pillow["purpose"]


def test_assetripper_audio_classification_is_discoverable_and_gated():
    skill = read_text("SKILL.md")
    workflow = read_text("references/full-extraction-workflow.md")
    checks = read_text("references/branch-completion-checks.md")

    for path in [
        "scripts/organize_assetripper_audio_resources.py",
        "references/assetripper-audio-classification.json",
    ]:
        assert (ROOT / path).is_file()
        assert path in skill

    combined = "\n".join([skill, workflow, checks])
    required_terms = [
        "1.分类资源/音频/AssetRipper/",
        "4.临时目录/中间索引/AssetRipper音频分类/",
        "manifest SHA-256",
        "missing/extra/impure/conflict/failed",
        "源前后快照一致",
        "硬链接分类只读",
        "编辑前复制",
    ]
    for term in required_terms:
        assert term in combined


def test_unitypy_organizer_declares_exact_generation_success_handoff_contract():
    skill = read_text("SKILL.md")
    workflow = read_text("references/full-extraction-workflow.md")
    contract = json.loads(
        read_text("references/unitypy-resource-organization-contract.json")
    )
    policy = contract["control_input_policy"]

    assert policy["artifact_directory_lease"] == (
        "held_from_stage_through_commit_rollback_and_cleanup"
    )
    assert policy["artifact_rename_reconciliation"] == (
        "exact_generation_postcondition_after_success_or_exception"
    )
    assert policy["cleanup_status_success_handoff"] == (
        "explicit_close_or_context_after_consumption_cli_closes_after_serialization"
    )
    assert policy["owned_generation_cleanup"] == (
        "retire_exact_generation_then_handle_or_fd_delete"
    )
    assert policy["no_replace_publish_reconciliation"] == (
        "hardlink_and_copy_adopt_only_exact_published_generation"
    )

    combined = "\n".join([skill, workflow])
    for term in [
        "OrganizationResult",
        "显式 `close()`",
        "exact generation",
        "hardlink",
        "copy no-replace",
        "stage",
        "rollback",
        "handle/fd",
    ]:
        assert term in combined


def test_animation_vfx_dynamic_routing_and_viewer_contract_are_integrated():
    skill = read_text("SKILL.md")
    prompts = read_text("references/multi-agent-prompts.md")
    workflow = read_text("references/game-animation-preview-workflow.md")
    klei = read_text("references/klei-dst-workflow.md")
    branch = read_text("references/branch-completion-checks.md")
    local_requirements = read_text("references/local-export-preview-requirements.md")
    directory_spec = read_text("references/directory-spec.md")
    routing = json.loads(read_text("references/animation-vfx-routing-config.json"))
    combined = "\n".join(
        [
            skill,
            prompts,
            workflow,
            klei,
            branch,
            local_requirements,
            directory_spec,
        ]
    )

    for path in [
        "references/animation-vfx-routing-config.json",
        "references/game-animation-preview-workflow.md",
        "references/game-animation-manifest-schema.json",
        "scripts/build_game_animation_manifest.py",
        "scripts/export_game_animation_asset.py",
        "scripts/create_game_animation_viewer_launcher.py",
    ]:
        assert path in skill

    for term in [
        "N/B/G/U/E/C/R",
        "Discovery → Adapter → Exporter → Preview → Merger → Spec → Quality",
        "single-producer",
        "Spec 复审",
        "GameAnimationViewer_1.0.1/GameAnimationViewer.exe",
        "source_roots",
        "Klei/DST BILD + ANIM + atlas AnimState v1",
        "2796",
        "本地没有 shard CLI 不是门禁",
        "不得声称已有通用本地 Merger 程序",
        "material-only/unsupported",
    ]:
        assert term in skill

    for path in [
        "4.临时目录/中间索引/游戏动画预览/manifest.json",
        "4.临时目录/中间索引/游戏动画预览/preview-cache/",
        "4.临时目录/动画预览导出/",
        "2.报告/打开动画预览.bat",
    ]:
        assert path in combined

    for export_format in [
        "current-frame-png",
        "png-sequence",
        "apng",
        "gif",
        "source-bundle",
    ]:
        assert export_format in workflow

    assert "scripts/export_game_animation_media.py" not in combined
    assert "versioned generic IR" not in combined
    assert "4.临时目录/中间索引/动画预览/" not in combined

    expected_roles = {
        "Discovery",
        "Adapter",
        "Exporter",
        "Preview",
        "Merger",
        "Spec",
        "Fixer",
        "Quality",
    }
    assert set(routing["metrics"]) == {"N", "B", "G", "U", "E", "C", "R"}
    assert expected_roles <= set(routing["roles"])
    assert routing["engine_overrides"]["klei"]["conversion_single_producer"] is True
    assert "ready_independent_nodes_without_write_conflicts" in routing["concurrency"][
        "child_concurrency_formula"
    ]

    for heading in [
        "动画/特效 Discovery",
        "动画/特效 Adapter",
        "动画/特效 Exporter",
        "动画/特效 Preview",
        "动画/特效 Merger",
        "动画/特效 Spec Reviewer",
        "动画/特效 Fixer",
        "动画/特效 Quality Reviewer",
    ]:
        assert heading in prompts
    for term in [
        "4.临时目录/子任务产物/<task-id>/",
        "唯一写者",
        "Spec → 新 Fixer → Spec 复审 → Quality",
        "Klei package conversion",
        "一个 Merger 子 agent",
        "本地没有 shard CLI 不是门禁",
    ]:
        assert term in prompts

    for term in [
        "Bank.name",
        "Bank.package",
        "Build.name",
        "Build.package",
        "BILD + ANIM + atlas",
        "AnimState v1",
        "KSH shader",
        "candidate exact set",
        "analyze_klei_dyn_boundary.py",
        "DONE_WITH_CONCERNS",
        "不得进入 AnimState `ready` manifest",
        "未知格式先做只读 Discovery",
        "不得要求它符合当前 Klei/DST AnimState v1 schema",
    ]:
        assert term in workflow


def test_game_animation_manifest_requires_named_safe_source_roots_object():
    schema = json.loads(read_text("references/game-animation-manifest-schema.json"))

    assert "source_roots" in schema["required"]
    source_roots = schema["properties"]["source_roots"]
    assert source_roots["type"] == "object"
    assert source_roots["minProperties"] >= 1
    assert source_roots["propertyNames"]["minLength"] >= 1
    assert source_roots["additionalProperties"] == {"$ref": "#/$defs/relativePath"}

    relative_path = schema["$defs"]["relativePath"]
    assert relative_path["type"] == "string"
    assert relative_path["minLength"] >= 1
    rejected_patterns = relative_path["not"]["anyOf"]
    assert any("A-Za-z" in entry["pattern"] for entry in rejected_patterns)
    assert any("\\.\\." in entry["pattern"] for entry in rejected_patterns)


def test_custom_dst_character_requires_independent_visual_identity():
    workflow = read_text("references/game-character-animation-viewer-workflow.md")

    required_terms = [
        "自定义角色的视觉身份发行门禁",
        "headbase",
        "headbase_hat",
        "hair_hat",
        "torso_pelvis",
        "arm_upper_skin",
        "SWAP_ICON",
        "数据配置驱动",
        "只替换 `hair`/`hair_hat`、调色、加头饰或修改角色显示名",
        "Bank: wilson",
        "新角色自己的 `Build`",
        "真 RGBA",
        "Alpha 至少同时存在 0 与 255",
        "100% 常用缩放",
        "idle_loop",
        "一个移动动作",
    ]
    for term in required_terms:
        assert term in workflow

    assert "130/132 不是其他 Bank 的通用阈值" in workflow

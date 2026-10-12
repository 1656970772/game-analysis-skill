from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SUFFIXES = {".ps1", ".psm1", ".cmd", ".bat", ".py"}
DRIVE_ALIAS_MUTATORS = {
    "SUBST": re.compile(r"(?i)\bsubst(?:\.exe)?\b"),
    "New-PSDrive": re.compile(r"(?i)\bnew-psdrive\b"),
    "DefineDosDevice": re.compile(r"(?i)\bdefinedosdevice(?:a|w)?\b"),
    "net use": re.compile(r"(?i)\bnet(?:\.exe)?\s+use\b"),
}
WINDOWS_POWERSHELL = shutil.which("powershell")


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def heading_section(text: str, heading: str) -> str:
    start = text.index(heading)
    body_start = start + len(heading)
    match = re.search(r"(?m)^##\s+", text[body_start:])
    end = body_start + match.start() if match else len(text)
    return text[start:end]


def assert_terms(text: str, terms: list[str]) -> None:
    missing = [term for term in terms if term not in text]
    assert not missing, f"missing contract terms: {missing}"


def run_gamemaker_export_validation(
    game_root: Path, study_root: str | Path, skill_root: Path
) -> subprocess.CompletedProcess[str]:
    assert WINDOWS_POWERSHELL is not None
    return subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "export_gamemaker_datawin_utmt.ps1"),
            "-GameRoot",
            str(game_root),
            "-StudyRoot",
            str(study_root),
            "-SkillRoot",
            str(skill_root),
            "-SkipSounds",
            "-SkipStrings",
        ],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        cwd=ROOT,
    )


def require_windows_powershell_51() -> int:
    if os.name != "nt" or WINDOWS_POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")

    result = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "$PSVersionTable.PSVersion.Major",
        ],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"cannot probe Windows PowerShell version: {result.stderr.strip()}")

    try:
        major = int(result.stdout.strip())
    except ValueError:
        pytest.skip(f"unrecognized Windows PowerShell version: {result.stdout!r}")
    if major != 5:
        pytest.skip(f"requires Windows PowerShell 5.x, found major={major}")
    return major


def test_automation_scripts_do_not_mutate_windows_drive_aliases() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "scripts").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCRIPT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in DRIVE_ALIAS_MUTATORS.items():
            if pattern.search(content):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {name}")

    assert offenders == []


def test_skill_and_subagent_contracts_fail_closed_without_drive_aliases() -> None:
    skill_policy = heading_section(
        read_text("SKILL.md"), "## Windows 路径兼容性与盘符命名空间"
    )
    assert re.search(r"空格.{0,16}(?:不等于|不是).{0,16}非\s*ASCII", skill_policy)
    assert_terms(
        skill_policy,
        [
            "真实绝对路径",
            "参数数组",
            "正确引号",
            "SUBST",
            "New-PSDrive",
            "DefineDosDevice",
            "net use",
            "主 agent",
            "子 agent",
            "只读核对",
            "不得自动清理",
            "现有卷",
            "普通目录",
            "task-id",
            "BLOCKED",
            "fail closed",
        ],
    )

    prompts = read_text("references/multi-agent-prompts.md")
    main_flow = heading_section(prompts, "## 使用方式")
    common_contract = heading_section(prompts, "## 通用约束块")
    for section in (main_flow, common_contract):
        assert_terms(
            section,
            [
                "OS 全局盘符命名空间",
                "SUBST",
                "New-PSDrive",
                "DefineDosDevice",
                "net use",
                "现有卷",
                "普通目录",
                "task-id",
                "BLOCKED",
            ],
        )

    unreal_role = heading_section(prompts, "## 角色十二：Unreal/PAK 只读索引执行")
    assert_terms(unreal_role, ["UModel", "slice", "共享", "兼容根", "普通目录", "task-id"])
    gamemaker_role = heading_section(prompts, "## 角色十一A：GameMaker UTMT 原始导出")
    assert_terms(gamemaker_role, ["空格", "非 ASCII", "虚拟盘符", "BLOCKED"])


def test_compatible_root_contract_has_cross_run_and_per_task_ownership() -> None:
    skill_policy = heading_section(
        read_text("SKILL.md"), "## Windows 路径兼容性与盘符命名空间"
    )
    prompts = read_text("references/multi-agent-prompts.md")
    common_contract = heading_section(prompts, "## 通用约束块")
    pitfalls = heading_section(
        read_text("references/common-pitfalls.md"), "## Windows 路径兼容性"
    )
    gamemaker = heading_section(
        read_text("references/gamemaker-datawin-workflow.md"),
        "## Windows 路径兼容性",
    )
    unreal = heading_section(
        read_text("references/unreal-pak-workflow.md"), "## Windows 路径兼容性"
    )

    for contract in (skill_policy, common_contract, pitfalls):
        normalized = contract.replace("\\", "/")
        assert "<compat-root>/<owner-id>/<task-id>/" in normalized
        assert_terms(
            contract,
            [
                "owner-id",
                "一次分析运行唯一",
                "主 agent",
                "下发",
                "子 agent",
                "不得自选",
                "task-id",
                "无冲突",
            ],
        )

    normalized_gamemaker = gamemaker.replace("\\", "/")
    assert "<compat-root>/<owner-id>/<task-id>/gamemaker" in normalized_gamemaker
    assert_terms(gamemaker, ["owner-id", "task-id", "gamemaker", "主 agent", "下发"])

    normalized_unreal = unreal.replace("\\", "/")
    assert "<compat-root>/<owner-id>/<task-id>/<slice-id>" in normalized_unreal
    assert_terms(
        unreal,
        ["owner-id", "task-id", "slice-id", "主 agent", "下发", "无冲突"],
    )


def test_drive_alias_audit_preserves_fact_and_inference_boundary() -> None:
    skill_policy = heading_section(
        read_text("SKILL.md"), "## Windows 路径兼容性与盘符命名空间"
    )
    pitfalls = heading_section(
        read_text("references/common-pitfalls.md"), "## Windows 路径兼容性"
    )

    assert_terms(
        skill_policy,
        ["事实", "推断", "静态扫描", "外部命令", "不得归责", "自动清理"],
    )
    assert_terms(
        pitfalls,
        [
            "事实",
            "当前 `scripts/`",
            "静态扫描未发现",
            "映射 mutator",
            "历史映射",
            "agent 即时命令",
            "推断",
            "不能证明",
            "外部命令",
            "不得归责",
            "自动清理",
        ],
    )
    assert "“历史映射来自 agent 即时命令”只能作为推断" in pitfalls


def test_common_pitfalls_define_ascii_compatibility_without_mapped_drives() -> None:
    pitfalls = read_text("references/common-pitfalls.md")
    path_policy = heading_section(pitfalls, "## Windows 路径兼容性")

    assert re.search(r"空格.{0,16}(?:不等于|不是).{0,16}非\s*ASCII", path_policy)
    assert_terms(
        path_policy,
        [
            "完整绝对路径",
            "4.临时目录/_ascii_work/",
            "ASCII-compatible",
            "主 agent",
            "现有卷",
            "普通目录",
            "task-id",
            "0.原始导出",
            "1.分类资源",
            "2.报告",
            "3.代码",
            "4.临时目录",
            "盘符映射",
            "BLOCKED",
            "fail closed",
        ],
    )


def test_gamemaker_script_validates_before_creating_study_root() -> None:
    script = read_text("scripts/export_gamemaker_datawin_utmt.ps1")

    validation_call = "Assert-StudyRootPathCompatibility -PathValue $StudyRoot"
    creation_call = "New-Item -ItemType Directory -Force -Path $StudyRoot"
    assert validation_call in script
    assert creation_call in script
    assert script.index(validation_call) < script.index(creation_call)

    assert "F:\\MyTools" not in script
    assert_terms(
        script,
        [
            "Spaces are allowed",
            "real absolute path",
            "existing volume",
            "Virtual drive aliases are forbidden",
        ],
    )


@pytest.mark.skipif(
    os.name != "nt" or WINDOWS_POWERSHELL is None,
    reason="requires Windows PowerShell",
)
def test_gamemaker_validation_accepts_absolute_ascii_path_with_spaces_on_powershell_51(
    tmp_path: Path,
) -> None:
    require_windows_powershell_51()
    game_root = tmp_path / "Existing Game Root"
    game_root.mkdir()
    study_root = tmp_path / "ASCII Study Root"

    result = run_gamemaker_export_validation(
        game_root, study_root, tmp_path / "Missing Skill Root"
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "MethodNotFound" not in output
    assert "IsPathFullyQualified" not in output
    assert "Cannot find data.win" in output
    assert study_root.is_dir()


@pytest.mark.skipif(
    os.name != "nt" or WINDOWS_POWERSHELL is None,
    reason="requires Windows PowerShell",
)
def test_gamemaker_validation_rejects_non_ascii_before_directory_creation_on_powershell_51(
    tmp_path: Path,
) -> None:
    require_windows_powershell_51()
    game_root = tmp_path / "Existing Game Root"
    game_root.mkdir()
    study_root = tmp_path / "non-ascii-研究"

    result = run_gamemaker_export_validation(
        game_root, study_root, tmp_path / "Missing Skill Root"
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "StudyRoot must be ASCII-compatible" in output
    assert "MethodNotFound" not in output
    assert not study_root.exists()


def test_windows_powershell_behavior_tests_really_use_major_5() -> None:
    assert require_windows_powershell_51() == 5


@pytest.mark.parametrize("path_kind", ["drive-relative", "root-relative"])
def test_gamemaker_validation_rejects_non_absolute_windows_paths_before_side_effects_on_powershell_51(
    tmp_path: Path, path_kind: str
) -> None:
    require_windows_powershell_51()

    game_root = tmp_path / "Existing Game Root"
    game_root.mkdir()
    (game_root / "data.win").write_bytes(b"test sentinel; must not be opened by UTMT")
    unique_name = f"ga-path-policy-{path_kind}-{tmp_path.name}"
    if path_kind == "drive-relative":
        study_root_arg = f"{ROOT.drive}{unique_name}"
        resolved_candidate = ROOT / unique_name
    else:
        study_root_arg = f"\\{unique_name}"
        resolved_candidate = Path(ROOT.anchor) / unique_name

    assert not resolved_candidate.exists()
    result = run_gamemaker_export_validation(
        game_root, study_root_arg, tmp_path / "Missing Skill Root"
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "StudyRoot must be a real absolute path" in output
    assert "UndertaleModCli" not in output
    assert "data.win" not in output
    assert not resolved_candidate.exists()
    for pattern in DRIVE_ALIAS_MUTATORS.values():
        assert pattern.search(output) is None


def test_gamemaker_and_unreal_workflows_distinguish_spaces_from_non_ascii() -> None:
    gamemaker = read_text("references/gamemaker-datawin-workflow.md")
    unreal = read_text("references/unreal-pak-workflow.md")

    for workflow in (gamemaker, unreal):
        assert re.search(r"空格.{0,16}(?:不等于|不是).{0,16}非\s*ASCII", workflow)
        assert_terms(
            workflow,
            [
                "真实绝对路径",
                "直接传参",
                "参数数组",
                "现有卷",
                "普通目录",
                "task-id",
                "虚拟盘符",
                "BLOCKED",
                "fail closed",
            ],
        )

    assert_terms(gamemaker, ["完整绝对路径", "ASCII-compatible", "回填"])
    assert_terms(unreal, ["UModel", "slice", "共享", "兼容根"])

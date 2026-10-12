import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "export_godot_pck_project.ps1"


@pytest.fixture
def workspace_tmp_path() -> Path:
    parent = ROOT / "tests_runtime"
    path = parent / f"wrapper-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def write_manifest(skill_root: Path, tool: Path, declared_hash: object = None) -> None:
    record = {
        "name": "GDRETools",
        "version": "test",
        "local_path": tool.relative_to(skill_root).as_posix(),
    }
    if declared_hash is not None:
        record["exe_sha256"] = declared_hash
    manifest = {"schema_version": 1, "tools": [record]}
    references = skill_root / "references"
    references.mkdir(parents=True)
    (references / "tooling-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def create_junction(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    command = (
        "& { param([string]$Link, [string]$Target) "
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null }"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            command,
            str(link),
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def make_nonexecutable_tool_fixture(workspace_tmp_path: Path) -> tuple[Path, Path, Path]:
    study_root = workspace_tmp_path / "study"
    skill_root = workspace_tmp_path / "skill"
    tool = skill_root / "tools" / "gdre.invalid"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"not-an-executable")
    write_manifest(skill_root, tool, hashlib.sha256(tool.read_bytes()).hexdigest())
    source = workspace_tmp_path / "input.pck"
    source.write_bytes(b"GDPC")
    study_root.mkdir()
    return study_root, skill_root, source


def run_wrapper(
    study_root: Path,
    skill_root: Path,
    source: Path,
    timeout_seconds: int = 5,
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # The host Python process can carry PowerShell 7's module path into Windows
    # PowerShell 5.1, which disables normal inbox-module discovery. Let 5.1
    # rebuild its own default PSModulePath for this compatibility test.
    for key in list(environment):
        if key.lower() == "psmodulepath":
            del environment[key]
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-SourcePath",
            str(source),
            "-StudyRoot",
            str(study_root),
            "-SkillRoot",
            str(skill_root),
            "-TimeoutSeconds",
            str(timeout_seconds),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )


def test_wrapper_has_parameterized_safe_process_contract():
    text = WRAPPER.read_text(encoding="utf-8")

    required_terms = [
        "$SourcePath",
        "$StudyRoot",
        "$SkillRoot",
        "$TimeoutSeconds",
        "$ScriptsOnly",
        "$ForceBytecodeVersion",
        "references\\tooling-manifest.json",
        '"GDRETools"',
        "CreateProcess",
        "CREATE_SUSPENDED",
        "CREATE_NO_WINDOW",
        "AssignProcessToJobObject",
        "ResumeThread",
        "StartAssignedProcess",
        "WaitForExit",
        "Get-FileHash",
        "SHA256",
        "source_sha256_before",
        "source_sha256_after",
        "source_unchanged",
        "CreateJobObject",
        "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
        "cleanup_guaranteed",
        "cleanup_failure",
        "exit_code",
        "timed_out",
        "--scripts-only",
        "--force-bytecode-version=",
        "gdre_full_execution_summary.json",
        "gdre_scripts_execution_summary.json",
        "Assert-PathWithinRoot",
    ]
    for term in required_terms:
        assert term in text

    assert "rtk" not in text.lower()
    assert "SOULCHAIN" not in text
    assert "Start-Process -FilePath $source" not in text
    assert "$process = Start-Process" not in text
    create_call = text.index("if (!CreateProcess(")
    assign_call = text.index(
        "if (!AssignProcessToJobObject(handle, nativeProcess.hProcess))"
    )
    resume_call = text.index("if (ResumeThread(nativeProcess.hThread)")
    assert create_call < assign_call < resume_call
    assert "CREATE_SUSPENDED | CREATE_NO_WINDOW" in text


@pytest.mark.parametrize("declared_hash", [None, "", "ABC", "G" * 64, "0" * 63])
def test_wrapper_rejects_missing_empty_or_malformed_tool_hash_before_launch(
    workspace_tmp_path: Path,
    declared_hash: object,
):
    study_root = workspace_tmp_path / "study"
    skill_root = workspace_tmp_path / "skill"
    tool = skill_root / "tools" / "gdre.cmd"
    tool.parent.mkdir(parents=True)
    sentinel = tool.parent / "LAUNCHED.txt"
    tool.write_text('@echo launched>"%~dp0LAUNCHED.txt"\r\n', encoding="ascii")
    write_manifest(skill_root, tool, declared_hash)
    source = workspace_tmp_path / "input.pck"
    source.write_bytes(b"GDPC")
    study_root.mkdir()

    result = run_wrapper(study_root, skill_root, source)

    assert result.returncode != 0
    assert "64 hexadecimal" in (result.stdout + result.stderr)
    assert not sentinel.exists()


def test_wrapper_rejects_nonempty_output_before_launch(workspace_tmp_path: Path):
    study_root = workspace_tmp_path / "study"
    skill_root = workspace_tmp_path / "skill"
    tool = skill_root / "tools" / "gdre.cmd"
    tool.parent.mkdir(parents=True)
    sentinel = tool.parent / "LAUNCHED.txt"
    tool.write_text('@echo launched>"%~dp0LAUNCHED.txt"\r\n', encoding="ascii")
    write_manifest(skill_root, tool, hashlib.sha256(tool.read_bytes()).hexdigest())
    source = workspace_tmp_path / "input.pck"
    source.write_bytes(b"GDPC")
    output = study_root / "0.原始导出" / "GDRETools恢复"
    output.mkdir(parents=True)
    (output / "SENTINEL").write_text("keep", encoding="utf-8")

    result = run_wrapper(study_root, skill_root, source)

    assert result.returncode != 0
    assert "not empty" in (result.stdout + result.stderr)
    assert not sentinel.exists()


def test_wrapper_rejects_numbered_output_root_junction_before_launch(
    workspace_tmp_path: Path,
):
    study_root, skill_root, source = make_nonexecutable_tool_fixture(workspace_tmp_path)
    outside = workspace_tmp_path / "outside-output"
    create_junction(study_root / "0.原始导出", outside)

    result = run_wrapper(study_root, skill_root, source)

    assert result.returncode != 0
    assert list(outside.rglob("*")) == []
    assert "outside StudyRoot" in (result.stdout + result.stderr)
    assert not (study_root / "4.临时目录").exists()
    assert not (skill_root / "tools/LAUNCHED.txt").exists()


@pytest.mark.parametrize("junction_name", ["工具日志", "中间索引"])
def test_wrapper_rejects_log_or_index_root_junction_before_launch(
    workspace_tmp_path: Path,
    junction_name: str,
):
    study_root, skill_root, source = make_nonexecutable_tool_fixture(workspace_tmp_path)
    outside = workspace_tmp_path / f"outside-{junction_name}"
    create_junction(study_root / "4.临时目录" / junction_name, outside)

    result = run_wrapper(study_root, skill_root, source)

    assert result.returncode != 0
    assert list(outside.rglob("*")) == []
    assert "outside StudyRoot" in (result.stdout + result.stderr)
    assert not (study_root / "0.原始导出").exists()
    assert not (skill_root / "tools/LAUNCHED.txt").exists()


def test_wrapper_timeout_kills_child_process_and_records_guarantee(
    workspace_tmp_path: Path,
):
    study_root = workspace_tmp_path / "study"
    skill_root = workspace_tmp_path / "skill"
    tool = skill_root / "tools" / "gdre.cmd"
    tool.parent.mkdir(parents=True)
    tool.write_text(
        "@echo off\r\n"
        "start \"\" /b powershell.exe -NoLogo -NoProfile -Command \"$PID | Set-Content -Encoding ASCII -LiteralPath '%~dp0child.pid'; Start-Sleep -Seconds 30\"\r\n"
        "powershell.exe -NoLogo -NoProfile -Command \"Start-Sleep -Seconds 30\"\r\n",
        encoding="ascii",
    )
    write_manifest(skill_root, tool, hashlib.sha256(tool.read_bytes()).hexdigest())
    source = workspace_tmp_path / "input.pck"
    source.write_bytes(b"GDPC")
    study_root.mkdir()

    result = run_wrapper(study_root, skill_root, source, timeout_seconds=3)

    assert result.returncode != 0
    assert "timed out" in (result.stdout + result.stderr)
    pid_path = tool.parent / "child.pid"
    assert pid_path.is_file(), result.stdout + result.stderr
    child_pid = int(pid_path.read_text(encoding="ascii").strip())

    summary_path = (
        study_root
        / "4.临时目录"
        / "中间索引"
        / "Godot"
        / "gdre_full_execution_summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["timed_out"] is True
    assert summary["job_object_assigned"] is True
    assert summary["process_resumed"] is True
    assert summary["cleanup_guaranteed"] is True
    assert summary["cleanup_failure"] is None
    root_pid = int(summary["process_id"])

    for role, pid in [("root", root_pid), ("child", child_pid)]:
        probe = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 1 }}",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if probe.returncode != 0:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        assert probe.returncode == 0, f"{role} process {pid} survived timeout"


def test_wrapper_encodes_fixed_numbered_output_directories_without_utf8_literals():
    text = WRAPPER.read_text(encoding="utf-8")

    required_codepoints = [
        "0x539F",  # 原
        "0x59CB",  # 始
        "0x5BFC",  # 导
        "0x51FA",  # 出
        "0x6062",  # 恢
        "0x590D",  # 复
        "0x4E34",  # 临
        "0x65F6",  # 时
        "0x76EE",  # 目
        "0x5F55",  # 录
    ]
    for codepoint in required_codepoints:
        assert codepoint in text

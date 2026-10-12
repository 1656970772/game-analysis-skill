import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = SKILL_ROOT / "scripts" / "invoke_python_utf8.ps1"
DEFAULT_PYTHON = SKILL_ROOT / "tools" / "python-env" / "Scripts" / "python.exe"
PWSH = shutil.which("pwsh")


@pytest.fixture
def runtime_tmp_path() -> Path:
    parent = SKILL_ROOT / "tests_runtime"
    path = parent / f"utf8-wrapper-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


@pytest.fixture
def echo_script(runtime_tmp_path: Path) -> Path:
    script_dir = runtime_tmp_path / "脚本 目录"
    script_dir.mkdir()
    script = script_dir / "输出 参数.py"
    script.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "payload = {\n"
        "    'argv': sys.argv[1:],\n"
        "    'pythonioencoding': os.environ.get('PYTHONIOENCODING'),\n"
        "    'pythonutf8': os.environ.get('PYTHONUTF8'),\n"
        "    '中文路径': r'E:\\测试 目录\\资源.json',\n"
        "}\n"
        "print(json.dumps(payload, ensure_ascii=False))\n"
        "print('标准错误：中文完整', file=sys.stderr)\n"
        "if '--exit-code' in sys.argv:\n"
        "    index = sys.argv.index('--exit-code')\n"
        "    raise SystemExit(int(sys.argv[index + 1]))\n",
        encoding="utf-8",
    )
    return script


def run_wrapper(
    script: Path,
    *script_args: str,
    python_exe: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None, "测试要求 PowerShell 7 的 pwsh 可用"
    command = [PWSH, "-NoLogo", "-NoProfile", "-File", str(WRAPPER)]
    if python_exe is not None:
        command.extend(["-PythonExe", str(python_exe)])
    command.extend(["-Script", str(script), *script_args])
    return subprocess.run(
        command,
        cwd=SKILL_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=30,
    )


def parse_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert "�" not in result.stdout
    assert "�" not in result.stderr
    return json.loads(result.stdout.strip())


def test_default_python_preserves_utf8_spaces_and_flag_value(echo_script: Path):
    assert DEFAULT_PYTHON.is_file()
    result = run_wrapper(
        echo_script,
        r"E:\中文 路径\数据 文件.json",
        "--flag",
        "值 含空格",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = parse_stdout(result)
    assert payload["argv"] == [r"E:\中文 路径\数据 文件.json", "--flag", "值 含空格"]
    assert payload["中文路径"] == r"E:\测试 目录\资源.json"
    assert payload["pythonioencoding"] == "utf-8"
    assert payload["pythonutf8"] == "1"
    assert "标准错误：中文完整" in result.stderr


def test_explicit_python_forwards_stderr_and_custom_exit_code(echo_script: Path):
    result = run_wrapper(
        echo_script,
        "--flag",
        "非零退出",
        "--exit-code",
        "23",
        python_exe=Path(os.sys.executable),
    )

    assert result.returncode == 23
    payload = parse_stdout(result)
    assert payload["argv"] == ["--flag", "非零退出", "--exit-code", "23"]
    assert "标准错误：中文完整" in result.stderr


def test_missing_script_returns_clear_nonzero_error(runtime_tmp_path: Path):
    missing_script = runtime_tmp_path / "不存在 脚本.py"
    result = run_wrapper(missing_script, python_exe=Path(os.sys.executable))

    assert result.returncode == 2
    assert "Python script does not exist" in result.stderr
    assert str(missing_script) in result.stderr


def test_missing_python_returns_clear_nonzero_error(
    echo_script: Path, runtime_tmp_path: Path
):
    missing_python = runtime_tmp_path / "不存在 Python" / "python.exe"
    result = run_wrapper(echo_script, python_exe=missing_python)

    assert result.returncode == 127
    assert "Python executable does not exist" in result.stderr
    assert str(missing_python) in result.stderr


def test_parent_environment_is_not_changed(echo_script: Path):
    parent_io = os.environ.get("PYTHONIOENCODING")
    parent_utf8 = os.environ.get("PYTHONUTF8")
    child_environment = os.environ.copy()
    child_environment["PYTHONIOENCODING"] = "parent-sentinel"
    child_environment.pop("PYTHONUTF8", None)

    result = run_wrapper(echo_script, env=child_environment)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = parse_stdout(result)
    assert payload["pythonioencoding"] == "utf-8"
    assert payload["pythonutf8"] == "1"
    assert os.environ.get("PYTHONIOENCODING") == parent_io
    assert os.environ.get("PYTHONUTF8") == parent_utf8


def test_wrapper_restores_encoding_and_environment_in_finally():
    text = WRAPPER.read_text(encoding="utf-8")

    assert "finally" in text
    assert "$previousInputEncoding" in text
    assert "$previousOutputEncoding" in text
    assert "$previousPipelineEncoding" in text
    assert "$hadPythonIoEncoding" in text
    assert "$hadPythonUtf8" in text
    assert 'SetEnvironmentVariable("PYTHONIOENCODING", $null, "Process")' in text
    assert 'SetEnvironmentVariable("PYTHONUTF8", $null, "Process")' in text
    assert "Invoke-Expression" not in text

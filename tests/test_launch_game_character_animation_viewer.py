from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "launch_game_character_animation_viewer.ps1"
CSC_CANDIDATES = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "Microsoft.NET"
    / "Framework64"
    / "v4.0.30319"
    / "csc.exe",
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "Microsoft.NET"
    / "Framework"
    / "v4.0.30319"
    / "csc.exe",
)
POWERSHELL_HOSTS = tuple(
    host for host in ("powershell.exe", "pwsh.exe") if shutil.which(host) is not None
)


def compile_stub(tmp_path: Path) -> Path:
    compiler = next((candidate for candidate in CSC_CANDIDATES if candidate.is_file()), None)
    if compiler is None:
        pytest.skip(".NET Framework C# compiler is unavailable")
    source = tmp_path / "ViewerSmokeStub.cs"
    executable = tmp_path / "viewer-bin" / "ViewerSmokeStub.exe"
    executable.parent.mkdir()
    source.write_text(
        """
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

public static class ViewerSmokeStub
{
    public static int Main()
    {
        string root = Environment.GetEnvironmentVariable("GAME_ANALYSIS_DST_ROOT") ?? "";
        string output = Path.Combine(root, "stub-launches.txt");
        string line = Process.GetCurrentProcess().Id + "|" + Environment.CurrentDirectory + "|" + root;
        File.AppendAllText(output, line + Environment.NewLine, new UTF8Encoding(false));

        int sleepMs = 0;
        Int32.TryParse(Environment.GetEnvironmentVariable("GAME_ANALYSIS_STUB_SLEEP_MS"), out sleepMs);
        if (sleepMs > 0) Thread.Sleep(sleepMs);

        int exitCode = 0;
        Int32.TryParse(Environment.GetEnvironmentVariable("GAME_ANALYSIS_STUB_EXIT_CODE"), out exitCode);
        return exitCode;
    }
}
""".strip(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{executable}",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert executable.is_file()
    return executable


def invoke_helper(
    host: str,
    viewer: Path,
    game_root: Path,
    launch_log: Path,
    env: dict[str, str],
    *,
    redirect_like_generated_bat: bool = False,
):
    command = [
        host,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-ViewerPath",
        str(viewer),
        "-GameRoot",
        str(game_root),
        "-LogPath",
        str(launch_log),
    ]
    if redirect_like_generated_bat:
        launch_log.parent.mkdir(parents=True, exist_ok=True)
        command_line = subprocess.list2cmdline(command)
        command_line += " >>" + subprocess.list2cmdline([str(launch_log)]) + " 2>&1"
        command = ["cmd.exe", "/d", "/c", command_line]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        timeout=20,
    )


@pytest.mark.skipif(not POWERSHELL_HOSTS, reason="PowerShell is unavailable")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS)
def test_launches_real_process_with_environment_working_directory_exit_code_and_log(
    tmp_path: Path, host: str
) -> None:
    viewer = compile_stub(tmp_path)
    game_root = tmp_path / f"game-root-{Path(host).stem}"
    game_root.mkdir()
    launch_log = tmp_path / "logs" / f"{Path(host).stem}.log"
    environment = os.environ.copy()
    environment["GAME_ANALYSIS_STUB_EXIT_CODE"] = "23"
    environment["GAME_ANALYSIS_STUB_SLEEP_MS"] = "0"

    result = invoke_helper(
        host,
        viewer,
        game_root,
        launch_log,
        environment,
        redirect_like_generated_bat=True,
    )

    assert result.returncode == 23, result.stdout + result.stderr
    launches = (game_root / "stub-launches.txt").read_text(encoding="utf-8").splitlines()
    assert len(launches) == 1
    _pid, working_directory, inherited_root = launches[0].split("|", 2)
    assert Path(working_directory).resolve() == viewer.parent.resolve()
    assert Path(inherited_root).resolve() == game_root.resolve()
    log = launch_log.read_text(encoding="utf-8-sig")
    assert "Starting character viewer" in log
    assert "Viewer process exited with code 23" in log
    assert "Parameter set cannot be resolved" not in log


@pytest.mark.skipif(not POWERSHELL_HOSTS, reason="PowerShell is unavailable")
@pytest.mark.parametrize("host", POWERSHELL_HOSTS)
def test_existing_exact_executable_process_is_not_started_twice(tmp_path: Path, host: str) -> None:
    viewer = compile_stub(tmp_path)
    game_root = tmp_path / f"duplicate-root-{Path(host).stem}"
    game_root.mkdir()
    launches_path = game_root / "stub-launches.txt"
    launch_log = tmp_path / "logs" / f"duplicate-{Path(host).stem}.log"
    existing_environment = os.environ.copy()
    existing_environment["GAME_ANALYSIS_DST_ROOT"] = str(game_root.resolve())
    existing_environment["GAME_ANALYSIS_STUB_EXIT_CODE"] = "0"
    existing_environment["GAME_ANALYSIS_STUB_SLEEP_MS"] = "10000"
    existing = subprocess.Popen(
        [str(viewer)],
        cwd=viewer.parent,
        env=existing_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not launches_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert launches_path.is_file(), "stub process did not start"

        helper_environment = os.environ.copy()
        helper_environment["GAME_ANALYSIS_STUB_EXIT_CODE"] = "99"
        helper_environment["GAME_ANALYSIS_STUB_SLEEP_MS"] = "0"
        result = invoke_helper(host, viewer, game_root, launch_log, helper_environment)

        assert result.returncode == 0, result.stdout + result.stderr
        assert len(launches_path.read_text(encoding="utf-8").splitlines()) == 1
        log = launch_log.read_text(encoding="utf-8-sig")
        assert f"Viewer is already running (PID {existing.pid})" in log
        assert "focus requested:" in log
    finally:
        existing.terminate()
        try:
            existing.wait(timeout=5)
        except subprocess.TimeoutExpired:
            existing.kill()
            existing.wait(timeout=5)

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import export_wwise_audio as exporter  # noqa: E402


def test_bank_metadata_probe_is_explicitly_metadata_only(
    tmp_path: Path,
) -> None:
    bank = tmp_path / "Enemy.bnk"
    bank.write_bytes(b"BKHD")
    tool = tmp_path / "vgmstream-cli.exe"
    tool.write_bytes(b"fake")
    commands: list[list[str]] = []

    def fake_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "-I" in command and "-m" not in command:
            bank.with_suffix(bank.suffix + ".wav").write_bytes(b"unexpected write")
        payload = {
            "streamInfo": {"index": 0, "name": "123456", "total": 3},
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    result = exporter.probe_bank(tool, bank, runner=fake_runner)

    assert result["stream_count"] == 3
    assert commands == [[str(tool), "-m", "-I", str(bank)]]
    assert not bank.with_suffix(bank.suffix + ".wav").exists()


def test_decode_command_uses_an_explicit_output_path(tmp_path: Path) -> None:
    tool = tmp_path / "vgmstream-cli.exe"
    source = tmp_path / "1001.wem"
    output = tmp_path / "decoded" / "1001.wav"

    command = exporter.build_decode_command(tool, source, output)

    assert command == [
        str(tool),
        "-i",
        "-o",
        str(output),
        str(source),
    ]


def test_output_roots_cannot_overlap_the_read_only_input(tmp_path: Path) -> None:
    source = tmp_path / "game"
    source.mkdir()

    with pytest.raises(ValueError, match="input"):
        exporter.validate_roots(
            source,
            source / "decoded",
            tmp_path / "indices",
            tmp_path / "classified",
        )


def test_external_output_name_is_safe_traceable_and_bounded(tmp_path: Path) -> None:
    output_root = tmp_path / "raw"
    source_root = tmp_path / "game"
    source = source_root / "Windows" / "123456.wem"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wem")

    output = exporter.external_output_path(
        source=source,
        input_root=source_root,
        output_root=output_root,
        media_id="123456",
        semantic_name='Bad:<name>?* / 中文"voice".wav',
        language="Chinese",
    )

    output.relative_to(output_root.resolve())
    assert output.suffix == ".wav"
    assert output.name.startswith("123456__")
    assert not any(char in output.name for char in '<>:"/\\|?*')
    assert "中文" in output.name

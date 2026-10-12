from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


FIXED_MTIME_NS = 1_650_000_000_123_456_700


class FakeType:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeAssetsFile:
    name = "serialized.assets"


class FakeObject:
    assets_file = FakeAssetsFile()

    def __init__(
        self,
        asset_type: str,
        data: Any,
        *,
        path_id: int = 1,
        type_tree: Any = None,
    ) -> None:
        self.type = FakeType(asset_type)
        self.path_id = path_id
        self._data = data
        self._type_tree = type_tree

    def read(self) -> Any:
        return self._data

    def read_typetree(self) -> Any:
        return self._type_tree


def freeze_mtime(path: Path) -> int:
    path.touch(exist_ok=True)
    path.chmod(0o666)
    os.utime(path, ns=(FIXED_MTIME_NS, FIXED_MTIME_NS))
    return path.stat().st_mtime_ns


def assert_no_partial_files(root: Path) -> None:
    assert not [path for path in root.rglob("*") if path.name.endswith(".partial")]


def test_atomic_content_writer_reuses_exact_bytes_and_replaces_same_length_difference(
    tmp_path: Path,
) -> None:
    target = tmp_path / "payload.bin"

    changed = extractor.write_export_file_if_changed(
        target,
        lambda temp_path: temp_path.write_bytes(b"first"),
    )
    assert changed is True
    frozen = freeze_mtime(target)

    changed = extractor.write_export_file_if_changed(
        target,
        lambda temp_path: temp_path.write_bytes(b"first"),
    )
    assert changed is False
    assert target.stat().st_mtime_ns == frozen

    changed = extractor.write_export_file_if_changed(
        target,
        lambda temp_path: temp_path.write_bytes(b"other"),
    )
    assert changed is True
    assert target.read_bytes() == b"other"
    assert target.stat().st_mtime_ns != frozen
    assert_no_partial_files(tmp_path)


def test_atomic_content_writer_cleans_partial_on_base_exception_without_touching_target(
    tmp_path: Path,
) -> None:
    class SyntheticAbort(BaseException):
        pass

    target = tmp_path / "existing.bin"
    target.write_bytes(b"stable")
    frozen = freeze_mtime(target)

    def aborting_writer(temp_path: Path) -> None:
        temp_path.write_bytes(b"partial")
        raise SyntheticAbort("stop after a partial temporary write")

    with pytest.raises(SyntheticAbort, match="partial temporary write"):
        extractor.write_export_file_if_changed(target, aborting_writer)

    assert target.read_bytes() == b"stable"
    assert target.stat().st_mtime_ns == frozen
    assert_no_partial_files(tmp_path)


def test_png_export_reuses_identical_content_and_supports_unicode_long_target_names(
    tmp_path: Path,
) -> None:
    image = Image.new("RGBA", (3, 2), (12, 34, 56, 78))
    legacy_png = tmp_path / "legacy-direct-save.png"
    image.save(legacy_png)
    data = SimpleNamespace(
        m_Name="中文贴图_" + "long-name." * 9,
        m_Width=3,
        m_Height=2,
        image_data=b"inline-payload",
        m_StreamData=SimpleNamespace(path="", offset=0, size=0),
        image=image,
    )
    obj = FakeObject("Texture2D", data, path_id=7)
    try:
        first_row, first_exported = extractor.export_object(
            obj,
            "中文目录/资源.assets",
            {},
            tmp_path,
            "metadata",
        )
        target = tmp_path / first_row["export_path"]
        frozen = freeze_mtime(target)
        second_row, second_exported = extractor.export_object(
            obj,
            "中文目录/资源.assets",
            {},
            tmp_path,
            "metadata",
        )
    finally:
        image.close()

    assert first_exported is second_exported is True
    assert first_row == second_row
    assert target.stat().st_mtime_ns == frozen
    with Image.open(target) as exported:
        assert exported.format == "PNG"
        assert exported.size == (3, 2)
    assert target.read_bytes() == legacy_png.read_bytes()
    assert_no_partial_files(tmp_path)


@pytest.mark.parametrize(
    ("script", "suffix", "expected_bytes"),
    [
        ("line one\n中文", ".txt", f"line one{os.linesep}中文".encode("utf-8")),
        (b"utf-8 bytes", ".txt", b"utf-8 bytes"),
        (b"\xff\x00binary", ".bin", b"\xff\x00binary"),
    ],
)
def test_text_asset_string_and_bytes_outputs_reuse_identical_content(
    tmp_path: Path,
    script: str | bytes,
    suffix: str,
    expected_bytes: bytes,
) -> None:
    out_base = tmp_path / ("中文_" + "x" * 80)
    saved, remarks = extractor.write_text_asset(SimpleNamespace(m_Script=script), out_base)
    target = Path(saved)
    assert target.name.endswith(suffix)
    assert remarks == ""
    frozen = freeze_mtime(target)

    second_saved, second_remarks = extractor.write_text_asset(
        SimpleNamespace(m_Script=script),
        out_base,
    )

    assert Path(second_saved) == target
    assert second_remarks == ""
    assert target.read_bytes() == expected_bytes
    assert target.stat().st_mtime_ns == frozen
    assert_no_partial_files(tmp_path)


def test_text_asset_json_output_reuses_identical_content(tmp_path: Path) -> None:
    script = {"name": "中文", "items": [1, 2, 3]}
    out_base = tmp_path / "structured"
    saved, _ = extractor.write_text_asset(SimpleNamespace(m_Script=script), out_base)
    target = Path(saved)
    frozen = freeze_mtime(target)

    second_saved, _ = extractor.write_text_asset(SimpleNamespace(m_Script=script), out_base)

    assert Path(second_saved) == target
    assert json.loads(target.read_text(encoding="utf-8")) == script
    assert target.stat().st_mtime_ns == frozen
    assert_no_partial_files(tmp_path)


def test_metadata_json_export_reuses_identical_content(tmp_path: Path) -> None:
    obj = FakeObject(
        "Material",
        SimpleNamespace(m_Name="stable-material"),
        path_id=11,
        type_tree={"m_Name": "stable-material", "m_Custom": [1, 2]},
    )
    first_row, first_exported = extractor.export_object(
        obj,
        "Game_Data/resources.assets",
        {},
        tmp_path,
        "metadata",
    )
    target = tmp_path / first_row["export_path"]
    legacy_metadata = tmp_path / "legacy-metadata.json"
    legacy_metadata.write_text(
        json.dumps(
            {
                "source_file": "Game_Data/resources.assets",
                "path_id": 11,
                "asset_type": "Material",
                "asset_name": "stable-material",
                "container_path": "",
                "type_tree": {"m_Name": "stable-material", "m_Custom": [1, 2]},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    frozen = freeze_mtime(target)

    second_row, second_exported = extractor.export_object(
        obj,
        "Game_Data/resources.assets",
        {},
        tmp_path,
        "metadata",
    )

    assert first_exported is second_exported is True
    assert first_row == second_row
    assert target.stat().st_mtime_ns == frozen
    assert target.read_bytes() == legacy_metadata.read_bytes()
    assert json.loads(target.read_text(encoding="utf-8"))["type_tree"]["m_Custom"] == [1, 2]
    assert_no_partial_files(tmp_path)


def test_audio_sample_bytes_reuse_identical_content(tmp_path: Path) -> None:
    data = SimpleNamespace(
        m_Name="voice-bank",
        samples={"voice.ogg": b"OggS-sample", "中文.wav": b"RIFF-sample"},
    )
    obj = FakeObject("AudioClip", data, path_id=15)
    first_row, first_exported = extractor.export_object(
        obj,
        "Game_Data/resources.assets",
        {},
        tmp_path,
        "samples",
    )
    targets = [tmp_path / relative for relative in first_row["export_path"].split(";")]
    frozen = {target: freeze_mtime(target) for target in targets}

    second_row, second_exported = extractor.export_object(
        obj,
        "Game_Data/resources.assets",
        {},
        tmp_path,
        "samples",
    )

    assert first_exported is second_exported is True
    assert first_row == second_row
    assert [target.read_bytes() for target in targets] == [b"OggS-sample", b"RIFF-sample"]
    assert {target: target.stat().st_mtime_ns for target in targets} == frozen
    assert_no_partial_files(tmp_path)


def test_audio_sample_child_identity_uses_full_raw_key_hash_without_collision(
    tmp_path: Path,
) -> None:
    samples = {
        "目录甲.wav": b"first-sample",
        "目录乙.wav": b"second-sample",
        "CON.wav": b"reserved-name-sample",
        f"{'x' * 260}LEFT.ogg": b"long-left",
        f"{'x' * 260}RIGHT.ogg": b"long-right",
    }
    obj = FakeObject(
        "AudioClip",
        SimpleNamespace(m_Name="voice-bank", samples=samples),
        path_id=19,
    )

    row, exported = extractor.export_object(
        obj,
        "Game_Data/audio.assets",
        {},
        tmp_path,
        "samples",
    )

    paths = row["export_path"].split(";")
    assert exported is True
    assert len(paths) == len(samples)
    assert len({path.casefold() for path in paths}) == len(samples)
    assert all("_sha256_" in Path(path).name for path in paths)
    assert all(len(Path(path).name) <= 255 for path in paths)
    assert [Path(tmp_path / path).read_bytes() for path in paths] == list(samples.values())


def test_audio_sample_stage_failure_leaves_no_unindexed_child(tmp_path: Path) -> None:
    obj = FakeObject(
        "AudioClip",
        SimpleNamespace(
            m_Name="voice-bank",
            samples={"first.wav": b"first-sample", "second.wav": object()},
        ),
        path_id=21,
    )

    row, exported = extractor.export_object(
        obj,
        "Game_Data/audio.assets",
        {},
        tmp_path,
        "samples",
    )

    assert exported is False
    assert row["status"] == "export_error"
    assert row["export_path"] == ""
    assert not [path for path in (tmp_path / "audio").rglob("*") if path.is_file()]
    assert_no_partial_files(tmp_path)


def test_audio_sample_commit_failure_restores_complete_previous_set_and_mtimes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = FakeObject(
        "AudioClip",
        SimpleNamespace(
            m_Name="voice-bank",
            samples={"first.wav": b"first-old", "second.wav": b"second-old"},
        ),
        path_id=22,
    )
    first_row, first_exported = extractor.export_object(
        original,
        "Game_Data/audio.assets",
        {},
        tmp_path,
        "samples",
    )
    assert first_exported is True
    targets = [tmp_path / relative for relative in first_row["export_path"].split(";")]
    original_bytes = {path: path.read_bytes() for path in targets}
    original_mtimes = {path: freeze_mtime(path) for path in targets}
    real_rename_no_replace = extractor.OutputBoundary.rename_no_replace

    def fail_second_stage(
        boundary: extractor.OutputBoundary,
        temporary: Path,
        target: Path,
    ) -> None:
        if temporary.name.startswith(".ga-audio-stage-") and target.name.startswith(
            "second_sha256_"
        ):
            raise extractor.OutputBoundaryError("synthetic second sample commit failure")
        real_rename_no_replace(boundary, temporary, target)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "rename_no_replace",
        fail_second_stage,
    )
    changed = FakeObject(
        "AudioClip",
        SimpleNamespace(
            m_Name="voice-bank",
            samples={"first.wav": b"first-new", "second.wav": b"second-new"},
        ),
        path_id=22,
    )

    with pytest.raises(extractor.OutputBoundaryError, match="second sample commit failure"):
        extractor.export_object(
            changed,
            "Game_Data/audio.assets",
            {},
            tmp_path,
            "samples",
        )
    assert {path: path.read_bytes() for path in targets} == original_bytes
    assert {path: path.stat().st_mtime_ns for path in targets} == original_mtimes
    assert_no_partial_files(tmp_path)


def test_audio_sample_commit_failure_removes_every_new_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_rename_no_replace = extractor.OutputBoundary.rename_no_replace

    def fail_second_stage(
        boundary: extractor.OutputBoundary,
        temporary: Path,
        target: Path,
    ) -> None:
        if temporary.name.startswith(".ga-audio-stage-") and target.name.startswith(
            "second_sha256_"
        ):
            raise extractor.OutputBoundaryError("synthetic second new-sample commit failure")
        real_rename_no_replace(boundary, temporary, target)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "rename_no_replace",
        fail_second_stage,
    )
    obj = FakeObject(
        "AudioClip",
        SimpleNamespace(
            m_Name="voice-bank",
            samples={"first.wav": b"first-new", "second.wav": b"second-new"},
        ),
        path_id=23,
    )

    with pytest.raises(extractor.OutputBoundaryError, match="new-sample commit failure"):
        extractor.export_object(
            obj,
            "Game_Data/audio.assets",
            {},
            tmp_path,
            "samples",
        )

    assert not [path for path in (tmp_path / "audio").rglob("*") if path.is_file()]
    assert_no_partial_files(tmp_path)


@pytest.mark.parametrize("mode", ["reuse", "published"])
def test_group_revalidates_every_target_after_success_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    target = tmp_path / f"{mode}.bin"
    planned = b"planned generation"
    target.write_bytes(planned if mode == "reuse" else b"old generation")
    real_unlink = extractor.OutputBoundary.unlink_expected
    injected = False

    def drift_during_cleanup(
        current_boundary: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        nonlocal injected
        if not injected and path.name.startswith(".ga-group-retired-"):
            injected = True
            target.write_bytes(b"external generation")
        real_unlink(current_boundary, path, expected)

    monkeypatch.setattr(extractor.OutputBoundary, "unlink_expected", drift_during_cleanup)

    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.write_export_file_group_if_changed(
            [(target, planned)],
            output_boundary=boundary,
        )

    assert injected is True
    assert error.value.transaction_state == "committed_target_drift"
    assert target.read_bytes() == b"external generation"
    assert_no_partial_files(tmp_path)


def test_group_revalidates_reused_target_after_other_target_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    reused = tmp_path / "a-reused.bin"
    published = tmp_path / "b-published.bin"
    reused.write_bytes(b"stable")
    published.write_bytes(b"old")
    real_unlink = extractor.OutputBoundary.unlink_expected
    injected = False

    def drift_reused_target_during_published_cleanup(
        current_boundary: extractor.OutputBoundary,
        path: Path,
        expected: extractor.ExpectedFileGeneration,
    ) -> None:
        nonlocal injected
        if not injected and path.name.startswith(".ga-group-retired-"):
            injected = True
            reused.write_bytes(b"external")
        real_unlink(current_boundary, path, expected)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "unlink_expected",
        drift_reused_target_during_published_cleanup,
    )

    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.write_export_file_group_if_changed(
            [(reused, b"stable"), (published, b"new")],
            output_boundary=boundary,
        )

    assert injected is True
    assert error.value.transaction_state == "committed_target_drift"
    assert reused.read_bytes() == b"external"
    assert published.read_bytes() == b"new"
    assert_no_partial_files(tmp_path)


def test_group_reports_backup_mismatch_with_late_target_as_rollback_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = extractor.OutputBoundary(tmp_path).initialize()
    target = tmp_path / "target.bin"
    target.write_bytes(b"AAAA")
    real_rename = extractor.OutputBoundary.rename_no_replace
    injected = False

    def inject_late_generations(
        current_boundary: extractor.OutputBoundary,
        source: Path,
        destination: Path,
    ) -> None:
        nonlocal injected
        if (
            not injected
            and source == target
            and destination.name.startswith(".ga-audio-backup-")
        ):
            injected = True
            target.write_bytes(b"BBBB")
            real_rename(current_boundary, source, destination)
            target.write_bytes(b"CCCC")
            return
        real_rename(current_boundary, source, destination)

    monkeypatch.setattr(extractor.OutputBoundary, "rename_no_replace", inject_late_generations)

    with pytest.raises(extractor.GroupTransactionError) as error:
        extractor.write_export_file_group_if_changed(
            [(target, b"NNNN")],
            output_boundary=boundary,
        )

    assert injected is True
    assert error.value.transaction_state == "rollback_incomplete"
    assert target.read_bytes() == b"CCCC"
    assert len(error.value.recovery_files) == 1
    recovery = tmp_path / error.value.recovery_files[0]
    assert recovery.read_bytes() == b"BBBB"

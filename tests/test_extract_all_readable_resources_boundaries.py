from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


@pytest.mark.parametrize("relationship", ["equal", "output_under_source", "source_under_output"])
def test_output_boundary_rejects_source_export_overlap_before_new_output(
    tmp_path: Path,
    relationship: str,
) -> None:
    if relationship == "equal":
        source_root = tmp_path / "same"
        export_root = source_root
        source_root.mkdir()
    elif relationship == "output_under_source":
        source_root = tmp_path / "source"
        export_root = source_root / "new-export"
        source_root.mkdir()
    else:
        export_root = tmp_path / "export"
        source_root = export_root / "source"
        source_root.mkdir(parents=True)

    with pytest.raises(extractor.OutputBoundaryError, match="disjoint|overlap"):
        extractor.OutputBoundary(export_root, source_root).initialize()

    if relationship == "output_under_source":
        assert not export_root.exists()


def test_output_boundary_rejects_existing_child_reparse_simulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    images = export_root / "images"
    images.mkdir(parents=True)
    original = extractor.is_reparse_path
    monkeypatch.setattr(
        extractor,
        "is_reparse_path",
        lambda path: Path(path) == images or original(Path(path)),
    )

    with pytest.raises(extractor.OutputBoundaryError, match="reparse"):
        extractor.OutputBoundary(export_root).initialize()


def test_nested_directory_is_rechecked_after_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    boundary = extractor.OutputBoundary(export_root).initialize()
    nested = export_root / "metadata" / "Material"
    original = extractor.is_reparse_path
    monkeypatch.setattr(
        extractor,
        "is_reparse_path",
        lambda path: (Path(path) == nested and Path(path).exists()) or original(Path(path)),
    )

    with pytest.raises(extractor.OutputBoundaryError, match="reparse"):
        boundary.ensure_directory(nested)
    assert not (nested / "escaped.json").exists()


def test_v2_identity_uses_full_original_identity_hash_and_legacy_gate_rejects_collision() -> None:
    common = "a" * 110
    left = f"{common}left.bundle"
    right = f"{common}right.bundle"
    assert extractor.safe_name(left, "source") == extractor.safe_name(right, "source")

    with pytest.raises(extractor.ResumeStateError, match="collide"):
        extractor.validate_canonical_source_tokens(
            [left, right],
            extractor.LEGACY_ASSET_IDENTITY_SCHEME,
        )

    left_id = extractor.canonical_asset_id(
        left,
        "CAB-shared",
        17,
        extractor.ASSET_IDENTITY_SCHEME,
    )
    right_id = extractor.canonical_asset_id(
        right,
        "CAB-shared",
        17,
        extractor.ASSET_IDENTITY_SCHEME,
    )
    assert left_id != right_id
    assert left_id.endswith(hashlib.sha256(
        json.dumps([left, "CAB-shared", 17], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest())


def test_legacy_normalization_collision_is_rejected() -> None:
    with pytest.raises(extractor.ResumeStateError, match="collide"):
        extractor.validate_canonical_source_tokens(
            ["目录甲.bundle", "目录乙.bundle"],
            extractor.LEGACY_ASSET_IDENTITY_SCHEME,
        )


def test_fresh_checkpoint_persists_protocol_two_identity_scheme(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    boundary = extractor.OutputBoundary(export_root).initialize()
    checkpoint_path = export_root / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    identity = extractor.checkpoint_identity(
        tmp_path / "source",
        export_root,
        "metadata",
        [],
        [],
        [],
        protocol_version=extractor.RESUME_PROTOCOL_VERSION,
    )

    checkpoint, resumed = extractor.load_or_create_checkpoint(
        checkpoint_path,
        identity,
        protocol_version=extractor.RESUME_PROTOCOL_VERSION,
        output_boundary=boundary,
    )

    assert resumed is False
    assert checkpoint["protocol_version"] == 2
    assert checkpoint["identity"]["asset_identity_scheme"] == extractor.ASSET_IDENTITY_SCHEME
    assert checkpoint["identity"]["asset_identity_version"] == extractor.ASSET_IDENTITY_VERSION
    assert checkpoint["asset_identity"] == {
        "scheme": extractor.ASSET_IDENTITY_SCHEME,
        "version": extractor.ASSET_IDENTITY_VERSION,
    }
    assert checkpoint["identity"]["large_bundle_strategy_selector"] == "block_or_node_v1"


def test_protocol_two_checkpoint_migrates_missing_node_strategy_selector(
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "export"
    boundary = extractor.OutputBoundary(export_root).initialize()
    checkpoint_path = export_root / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    identity = extractor.checkpoint_identity(
        tmp_path / "source",
        export_root,
        "metadata",
        [],
        [],
        [],
        protocol_version=extractor.RESUME_PROTOCOL_VERSION,
    )
    old_identity = dict(identity)
    old_identity.pop("large_bundle_strategy_selector")
    extractor.atomic_write_json(
        checkpoint_path,
        {
            "protocol_version": extractor.RESUME_PROTOCOL_VERSION,
            "identity": old_identity,
            "asset_identity": {
                "scheme": extractor.ASSET_IDENTITY_SCHEME,
                "version": extractor.ASSET_IDENTITY_VERSION,
            },
            "resume_count": 0,
            "sources": {},
        },
        output_boundary=boundary,
    )

    checkpoint, resumed = extractor.load_or_create_checkpoint(
        checkpoint_path,
        identity,
        protocol_version=extractor.RESUME_PROTOCOL_VERSION,
        output_boundary=boundary,
    )

    assert resumed is True
    assert checkpoint["identity"] == identity


def test_legacy_checkpoint_resumes_without_identity_field_migration(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    boundary = extractor.OutputBoundary(export_root).initialize()
    checkpoint_path = export_root / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    identity = {
        "source_root": "source",
        "export_root": "output",
        "options": {"audio_mode": "metadata"},
        "source_count": 735,
        "source_list_fingerprint": "sources",
        "external_payload_fingerprint": "payloads",
        "addressables_catalog_fingerprint": "catalogs",
    }
    extractor.atomic_write_json(
        checkpoint_path,
        {
            "protocol_version": 1,
            "identity": identity,
            "resume_count": 0,
            "sources": {},
        },
        output_boundary=boundary,
    )

    assert extractor.read_checkpoint_protocol(checkpoint_path) == 1
    checkpoint, resumed = extractor.load_or_create_checkpoint(
        checkpoint_path,
        identity,
        protocol_version=extractor.LEGACY_RESUME_PROTOCOL_VERSION,
        output_boundary=boundary,
    )
    assert resumed is True
    assert checkpoint["protocol_version"] == 1
    assert "asset_identity" not in checkpoint
    assert "asset_identity_scheme" not in checkpoint["identity"]


def test_fragment_identity_metadata_prevents_protocol_mix() -> None:
    with pytest.raises(extractor.ResumeStateError, match="will not be mixed"):
        extractor.validate_fragment_identity_metadata({}, extractor.RESUME_PROTOCOL_VERSION)
    with pytest.raises(extractor.ResumeStateError, match="will not be mixed"):
        extractor.validate_fragment_identity_metadata(
            {
                "asset_identity_scheme": extractor.ASSET_IDENTITY_SCHEME,
                "asset_identity_version": extractor.ASSET_IDENTITY_VERSION,
            },
            extractor.LEGACY_RESUME_PROTOCOL_VERSION,
        )


def test_orphaned_resume_fragments_are_not_mixed_with_fresh_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    export_root = tmp_path / "export"
    orphan = export_root / extractor.RESUME_STATE_DIR_NAME / "fragments" / "old.csv"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(extractor, "UnityPy", object())

    with pytest.raises(extractor.ResumeStateError, match="will not be mixed"):
        extractor.export_all(source_root, export_root)
    assert orphan.read_text(encoding="utf-8") == "legacy"
    assert not (export_root / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json").exists()


def test_legacy_source_collision_fails_before_checkpoint_load_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    common = "a" * 110
    (source_root / f"{common}left.bundle").write_bytes(b"not loaded")
    (source_root / f"{common}right.bundle").write_bytes(b"not loaded")
    export_root = tmp_path / "export"
    boundary = extractor.OutputBoundary(export_root).initialize()
    checkpoint_path = export_root / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    extractor.atomic_write_json(
        checkpoint_path,
        {
            "protocol_version": 1,
            "identity": {},
            "resume_count": 9,
            "sources": {},
        },
        output_boundary=boundary,
    )
    before = checkpoint_path.read_bytes()
    monkeypatch.setattr(extractor, "UnityPy", object())

    with pytest.raises(extractor.ResumeStateError, match="collide"):
        extractor.export_all(source_root, export_root)

    assert checkpoint_path.read_bytes() == before


def test_completion_inventory_rejects_nested_export_partial(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    partial = export_root / "images" / "Sprite" / ".ga-export-deadbeef.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"uncommitted")

    inventory = extractor.scan_export_completion_inventory(export_root)
    assert inventory["partial_paths"] == ["images/Sprite/.ga-export-deadbeef.partial"]
    with pytest.raises(extractor.ResumeStateError, match="Uncommitted partial"):
        extractor.scan_export_completion_inventory(export_root, reject_partials=True)


def test_real_symlink_child_is_rejected_when_platform_allows_it(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    outside = tmp_path / "outside"
    export_root.mkdir()
    outside.mkdir()
    link = export_root / "images"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create a directory symlink.")

    with pytest.raises(extractor.OutputBoundaryError, match="reparse"):
        extractor.OutputBoundary(export_root).initialize()


def make_windows_junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"cannot create isolated Windows junction: {result.stderr or result.stdout}"
        )


def directory_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows stable-directory contract")
def test_parent_swap_during_replace_is_blocked_before_any_outside_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    parent = export_root / "metadata"
    parked = export_root / "metadata-parked"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    temporary = parent / "payload.partial"
    target = parent / "payload.json"
    temporary.write_bytes(b"inside-temporary")
    (outside / temporary.name).write_bytes(b"outside-controlled")
    (outside / target.name).write_bytes(b"outside-existing-target")
    (outside / "unrelated.bin").write_bytes(b"unchanged")
    outside_before = directory_bytes(outside)
    boundary = extractor.OutputBoundary(export_root).initialize()
    real_replace = extractor.os.replace

    def raced_replace(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
    ) -> None:
        os.rename(parent, parked)
        make_windows_junction(parent, outside)
        real_replace(source, destination)

    monkeypatch.setattr(extractor.os, "replace", raced_replace)
    try:
        with pytest.raises(extractor.OutputBoundaryError, match="Stable atomic replacement"):
            boundary.replace(temporary, target)

        assert directory_bytes(outside) == outside_before
        assert parent.is_dir()
        assert not parked.exists()
        assert temporary.read_bytes() == b"inside-temporary"
        assert not target.exists()
        assert not list(export_root.rglob(".ga-dirlease-*"))
    finally:
        if extractor.is_reparse_path(parent):
            os.rmdir(parent)
        if parked.exists():
            os.rename(parked, parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows stable-directory contract")
def test_parent_swap_during_cleanup_is_blocked_before_any_outside_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    parent = export_root / "metadata"
    parked = export_root / "metadata-parked"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    temporary = parent / "payload.partial"
    temporary.write_bytes(b"inside-temporary")
    (outside / temporary.name).write_bytes(b"outside-controlled")
    (outside / "unrelated.bin").write_bytes(b"unchanged")
    outside_before = directory_bytes(outside)
    boundary = extractor.OutputBoundary(export_root).initialize()
    real_unlink = extractor.os.unlink

    def raced_unlink(path: os.PathLike[str] | str, *args: object, **kwargs: object) -> None:
        os.rename(parent, parked)
        make_windows_junction(parent, outside)
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(extractor.os, "unlink", raced_unlink)
    try:
        with pytest.raises(extractor.OutputBoundaryError, match="Stable output cleanup"):
            boundary.unlink(temporary)

        assert directory_bytes(outside) == outside_before
        assert parent.is_dir()
        assert not parked.exists()
        assert temporary.read_bytes() == b"inside-temporary"
        assert not list(export_root.rglob(".ga-dirlease-*"))
    finally:
        if extractor.is_reparse_path(parent):
            os.rmdir(parent)
        if parked.exists():
            os.rename(parked, parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows stable-directory creation contract")
def test_new_directory_creation_is_relative_to_pinned_parent_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    parent = export_root / "metadata"
    parked = export_root / "metadata-parked"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    (outside / "sentinel.bin").write_bytes(b"outside-stable")
    outside_before = directory_bytes(outside)
    boundary = extractor.OutputBoundary(export_root).initialize()
    real_create = extractor.OutputBoundary._windows_create_child_directory
    rename_errors: list[int | None] = []

    def create_after_parent_swap_attempt(directory_handle: int, name: str) -> int:
        try:
            os.rename(parent, parked)
        except OSError as exc:
            rename_errors.append(exc.winerror)
        else:  # pragma: no cover - would violate the stable-parent contract
            make_windows_junction(parent, outside)
        return real_create(directory_handle, name)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "_windows_create_child_directory",
        staticmethod(create_after_parent_swap_attempt),
    )
    try:
        created = boundary.ensure_directory(parent / "new-category")
        assert rename_errors
        assert created == parent / "new-category"
        assert created.is_dir()
        assert not parked.exists()
        assert not extractor.is_reparse_path(parent)
        assert directory_bytes(outside) == outside_before
        assert not (outside / "new-category").exists()
        assert not list(export_root.rglob(".ga-dirlease-*"))
    finally:
        if extractor.is_reparse_path(parent):
            os.rmdir(parent)
        if parked.exists():
            os.rename(parked, parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows output-root creation contract")
def test_missing_output_root_chain_is_created_under_pinned_existing_ancestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorized_parent = tmp_path / "authorized-parent"
    parked = tmp_path / "authorized-parent-parked"
    outside = tmp_path / "outside"
    authorized_parent.mkdir()
    outside.mkdir()
    (outside / "sentinel.bin").write_bytes(b"outside-stable")
    outside_before = directory_bytes(outside)
    export_root = authorized_parent / "new-export" / "nested"
    real_create = extractor.OutputBoundary._windows_create_child_directory
    rename_errors: list[int | None] = []

    def create_after_ancestor_swap_attempt(directory_handle: int, name: str) -> int:
        if name == "new-export":
            try:
                os.rename(authorized_parent, parked)
            except OSError as exc:
                rename_errors.append(exc.winerror)
            else:  # pragma: no cover - would violate the stable-parent contract
                make_windows_junction(authorized_parent, outside)
        return real_create(directory_handle, name)

    monkeypatch.setattr(
        extractor.OutputBoundary,
        "_windows_create_child_directory",
        staticmethod(create_after_ancestor_swap_attempt),
    )
    try:
        boundary = extractor.OutputBoundary(export_root).initialize()
        assert boundary.root == export_root
        assert export_root.is_dir()
        assert rename_errors
        assert not parked.exists()
        assert not extractor.is_reparse_path(authorized_parent)
        assert directory_bytes(outside) == outside_before
        assert not (outside / "new-export").exists()
        assert not list(export_root.rglob(".ga-dirlease-*"))
    finally:
        if extractor.is_reparse_path(authorized_parent):
            os.rmdir(authorized_parent)
        if parked.exists():
            os.rename(parked, authorized_parent)

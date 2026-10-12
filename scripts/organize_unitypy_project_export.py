#!/usr/bin/env python3
"""Stream and safely organize UnityPy exports into classified resources."""

from __future__ import annotations

import argparse
import contextvars
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
from typing import Any, BinaryIO, Generator, TextIO
import unicodedata
import uuid


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "references" / "unitypy-resource-organization.json"
DEFAULT_CONTRACT = SKILL_ROOT / "references" / "unitypy-resource-organization-contract.json"
OFFICIAL_CATEGORY_ROOTS = frozenset({"图片", "模型", "特效", "音频", "渲染"})
REQUIRED_FORBIDDEN_EXTENSIONS = frozenset({".json", ".csv", ".txt", ".md"})
EXPECTED_COLLISION_POLICY = {
    "same_content": "reuse_without_touching_mtime",
    "different_content": "stable_identity_suffix_then_fail_closed",
    "incremental_suffixes_forbidden": True,
}
EXPECTED_SOURCE_POLICY = {
    "index_processing": "two_pass_streaming",
    "sequence_candidate_memory": "bounded_with_configured_sqlite_spill",
    "source_files": "read_only",
    "reject_path_traversal": True,
    "reject_symlink_or_reparse_escape": True,
    "classified_resource_mutations": "stable_directory_generation_handle_or_fd",
    "sequence_candidate_eligibility": "per_export_path_deliverable_existing_and_filter_allowed",
    "multi_export_path_semantics": "per_physical_path_context",
    "source_resource_reads": "stable_root_to_file_handle_or_fd_same_object_hash_and_transfer",
    "classified_target_ownership": "created_generation_bound_retire_then_handle_or_fd_delete_without_foreign_cleanup",
    "export_path_canonicalization": "strict_posix_relative_with_windows_alias_control",
    "duplicate_physical_sources": "deduplicate_exact_reject_windows_alias",
}
EXPECTED_ARTIFACT_POLICY = {
    "path_semantics": "windows_case_insensitive",
    "reject_file_parent_conflicts": True,
    "reject_input_aliases": ["index", "config", "contract"],
    "requirements_must_be_unique": True,
    "csv_fields": "unique_non_empty_strings",
    "filename_suffix_must_match_artifact_type": True,
}
EXPECTED_CONTROL_INPUT_POLICY = {
    "parse_and_provenance": "same_bytes_snapshot",
    "revalidate_before_first_write": True,
    "revalidate_before_artifact_commit": True,
    "revalidate_during_and_after_artifact_commit": True,
    "artifact_group_commit": "rollback_on_failure_with_summary_last",
    "drift_result": "nonzero_without_replacing_existing_artifacts",
    "source_index_pass_integrity": "each_pass_stream_digest_and_stable_identity",
    "artifact_destination_binding": "planned_and_staged_exact_generation_with_stable_parent_lease",
    "artifact_directory_lease": "held_from_stage_through_commit_rollback_and_cleanup",
    "artifact_rename_reconciliation": "exact_generation_postcondition_after_success_or_exception",
    "rollback_failure_state": "ok_false_rollback_incomplete_or_summary_absent",
    "cleanup_warning_state": "committed_ok_with_machine_readable_warnings",
    "cleanup_status_publish_failure": "ok_false_committed_cleanup_status_unknown_or_summary_absent",
    "cleanup_status_publish": "generation_bound_retire_then_no_replace",
    "cleanup_status_post_publish_validation": "exact_identity_and_sha256_through_caller_success_handoff",
    "cleanup_status_success_handoff": "explicit_close_or_context_after_consumption_cli_closes_after_serialization",
    "owned_generation_cleanup": "retire_exact_generation_then_handle_or_fd_delete",
    "no_replace_publish_reconciliation": "hardlink_and_copy_adopt_only_exact_published_generation",
    "abort_cleanup": "attempt_all_and_aggregate",
}


_ACTIVE_ARTIFACT_COMMIT: contextvars.ContextVar[
    tuple[StableDirectoryLease, "ArtifactPathState"] | None
] = contextvars.ContextVar("unitypy_artifact_commit", default=None)


class UnsafePathError(ValueError):
    """Raised when a source or destination crosses an authorized path boundary."""


class DestinationConflictError(FileExistsError):
    """Raised when a deterministic destination collision cannot be resolved."""


class InputChangedError(RuntimeError):
    """Raised when a snapshotted control input changes before output commit."""


class ClassifiedOutputBoundaryError(UnsafePathError):
    """Raised when a classified-resource mutation loses its pinned directory."""


class RecoveryRequiredError(RuntimeError):
    """Raised with exact, machine-readable generations that need reconciliation."""

    def __init__(
        self,
        message: str,
        recovery_files: Iterable[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.recovery_files = tuple(dict(item) for item in recovery_files)
        self.cleanup_required_paths = tuple(
            str(item["path"])
            for item in self.recovery_files
            if isinstance(item.get("path"), str)
        )


class ClassifiedMaterializationRecoveryError(RecoveryRequiredError):
    """A classified transfer failed and left an exact owned private generation."""


@dataclass(frozen=True)
class StableDirectoryLease:
    path: Path
    directory_fd: int | None = None
    windows_directory_handle: int | None = None


class OrganizationResult(dict[str, Any]):
    """A result whose success lease remains live until the caller consumes it.

    A plain ``dict`` cannot keep a mandatory filesystem lease alive across the
    callee-return boundary.  Cleanup-status publication therefore returns this
    compatible mapping.  Direct callers explicitly call ``close()`` (or use a
    context manager) after consuming success; the CLI holds it through
    serialization and releases it explicitly afterwards.
    """

    def __init__(self, *args: Any, success_handoff: Any | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._success_handoff = success_handoff
        self._sync_handoff_state()

    def _sync_handoff_state(self) -> None:
        handoff = self._success_handoff
        if handoff is None:
            self.setdefault("handoff_state", "not_required")
            self.setdefault("published_lease_state", "not_held")
            self.setdefault("release_errors", [])
            self.setdefault("release_required_paths", [])
            return
        self["handoff_state"] = str(getattr(handoff, "state", "open"))
        self["published_lease_state"] = (
            "released" if getattr(handoff, "state", "open") == "complete" else "held"
        )
        self["release_errors"] = list(getattr(handoff, "release_errors", ()))
        self["release_required_paths"] = list(
            getattr(handoff, "release_required_paths", ())
        )

    def consume_success(self) -> None:
        handoff = self._success_handoff
        if handoff is None:
            return
        try:
            handoff.complete()
        except BaseException:
            self._sync_handoff_state()
            raise
        if getattr(handoff, "state", "complete") == "complete":
            self._success_handoff = None
        self._sync_handoff_state()

    def close(self) -> None:
        self.consume_success()

    def take_success_handoff(self) -> Any | None:
        handoff = self._success_handoff
        self._success_handoff = None
        self._sync_handoff_state()
        return handoff

    def __enter__(self) -> "OrganizationResult":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.consume_success()
        except BaseException as release_error:
            if exc is None:
                raise
            if hasattr(exc, "add_note"):
                exc.add_note(
                    "organization success-handoff release also failed: "
                    f"{type(release_error).__name__}: {release_error}"
                )

    def __del__(self) -> None:
        handoff = getattr(self, "_success_handoff", None)
        if handoff is None:
            return
        try:
            handoff.complete()
        except BaseException:
            # Destructors cannot report a reliable transactional exception.
            # Explicit callers and the CLI always consume/release instead.
            pass
        finally:
            if getattr(handoff, "state", None) == "complete":
                self._success_handoff = None


@dataclass
class SourceFileLease:
    path: Path
    relative_path: PurePosixPath
    handle: BinaryIO
    opened_identity: tuple[int, int, int, int, int]
    parent_directory_fd: int | None = None
    windows_parent_handle: int | None = None
    cached_sha256: str | None = None

    @property
    def name(self) -> str:
        """Expose the diagnostic basename without reopening the lexical path."""
        return self.path.name


@dataclass(frozen=True)
class TransferResult:
    method: str
    generation: "ArtifactPathState"


@dataclass(frozen=True)
class InputSnapshot:
    path: Path
    data: bytes
    sha256: str
    stat_identity: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class FileDigestSnapshot:
    path: Path
    sha256: str
    stat_identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class IndexPassEvidence:
    sha256: str
    before_identity: tuple[int, int, int, int]
    after_handle_identity: tuple[int, int, int, int]
    after_path_identity: tuple[int, int, int, int]

    @property
    def stable(self) -> bool:
        return self.before_identity == self.after_handle_identity == self.after_path_identity


@dataclass(frozen=True)
class RuntimeRules:
    config: dict[str, Any]
    image_types: frozenset[str]
    audio_types: frozenset[str]
    model_types: frozenset[str]
    render_types: frozenset[str]
    metadata_extensions: frozenset[str]
    image_extensions: frozenset[str]
    audio_extensions: frozenset[str]
    category_roots: frozenset[str]
    classification_rules: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...]
    default_image_categories: dict[str, str]
    sequence_patterns: tuple[re.Pattern[str], ...]
    sequence_action_hints: frozenset[str]
    sequence_context_patterns: tuple[re.Pattern[str], ...]
    main_candidate_patterns: tuple[re.Pattern[str], ...]
    sequence_destination: str
    minimum_distinct_frames: int
    sequence_scope_mode: str
    sequence_scope_fields: tuple[str, ...]
    sequence_explicit_context_cross_scope: bool
    sequence_spill_threshold: int
    transfer_methods: tuple[str, ...]
    identity_hash_length: int
    hash_cache_entries: int
    export_path_canonical_format: str
    export_path_duplicate_semantics: str
    export_path_exact_duplicate: str
    export_path_alias_spelling: str


@dataclass(frozen=True)
class MaterializeResult:
    destination: Path
    operation_status: str
    transfer_method: str
    collision_status: str
    conflict: bool
    resolved_conflict: bool


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _stream_stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    # Windows path stat and handle fstat can report different st_ctime
    # semantics for the same file.  dev/inode/size/mtime are comparable across
    # both APIs; each pass digest supplies the stronger generation binding.
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _parse_json_snapshot(snapshot: InputSnapshot, label: str) -> dict[str, Any]:
    value = json.loads(snapshot.data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _capture_input_snapshot(path: Path, label: str) -> InputSnapshot:
    canonical = _canonical_existing_file(path, label)
    before = canonical.stat()
    with canonical.open("rb") as handle:
        data = handle.read()
    after = canonical.stat()
    before_identity = _stat_identity(before)
    after_identity = _stat_identity(after)
    if before_identity != after_identity:
        raise InputChangedError(f"{label} changed while its input snapshot was being read: {canonical}")
    return InputSnapshot(
        path=canonical,
        data=data,
        sha256=hashlib.sha256(data).hexdigest().upper(),
        stat_identity=after_identity,
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    """Compatibility helper for callers that only need a stable one-read JSON object."""
    return _parse_json_snapshot(_capture_input_snapshot(path, label), label)


def _assert_input_snapshot_current(snapshot: InputSnapshot, label: str) -> None:
    current = _capture_input_snapshot(snapshot.path, label)
    if (
        current.path != snapshot.path
        or current.stat_identity != snapshot.stat_identity
        or current.sha256 != snapshot.sha256
        or current.data != snapshot.data
    ):
        raise InputChangedError(f"{label} changed after parsing and before output commit: {snapshot.path}")


def _assert_control_inputs_current(config: InputSnapshot, contract: InputSnapshot) -> None:
    _assert_input_snapshot_current(config, "config")
    _assert_input_snapshot_current(contract, "contract")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _capture_file_digest_snapshot(path: Path, label: str) -> FileDigestSnapshot:
    canonical = _canonical_existing_file(path, label)
    before = canonical.stat()
    digest = hashlib.sha256()
    with canonical.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after_handle = os.fstat(handle.fileno())
    after_path = canonical.stat()
    identities = tuple(_stream_stat_identity(value) for value in (before, opened, after_handle, after_path))
    if len(set(identities)) != 1:
        raise InputChangedError(f"{label} changed while its digest snapshot was being read: {canonical}")
    return FileDigestSnapshot(
        path=canonical,
        sha256=digest.hexdigest().upper(),
        stat_identity=identities[0],
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_relative_to(left, right) or _is_relative_to(right, left)


def _is_reparse(path: Path) -> bool:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _assert_no_reparse_components(root: Path, target: Path, label: str) -> None:
    if not _is_relative_to(target, root):
        raise UnsafePathError(f"{label} escapes root: {target}")
    current = root
    if current.exists() and _is_reparse(current):
        raise UnsafePathError(f"{label} root is a reparse point: {current}")
    for part in target.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise UnsafePathError(f"{label} crosses a reparse point: {current}")


def _assert_no_reparse_ancestors(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise UnsafePathError(f"{label} crosses a reparse point: {current}")


def _canonical_existing_file(path: Path, label: str) -> Path:
    lexical = Path(os.path.abspath(path))
    _assert_no_reparse_ancestors(lexical, label)
    if not lexical.is_file() or _is_reparse(lexical):
        raise FileNotFoundError(f"{label} is not a regular non-reparse file: {lexical}")
    return lexical.resolve(strict=True)


def _canonical_existing_dir(path: Path, label: str) -> Path:
    lexical = Path(os.path.abspath(path))
    _assert_no_reparse_ancestors(lexical, label)
    if not lexical.is_dir() or _is_reparse(lexical):
        raise FileNotFoundError(f"{label} is not a regular non-reparse directory: {lexical}")
    resolved = lexical.resolve(strict=True)
    _assert_no_reparse_components(resolved, resolved, label)
    return resolved


def _plan_study_root(path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    existing = lexical
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if existing.exists() and _is_reparse(existing):
        raise UnsafePathError(f"study-root ancestor is a reparse point: {existing}")
    _assert_no_reparse_ancestors(existing, "study-root")
    if lexical.exists() and (not lexical.is_dir() or _is_reparse(lexical)):
        raise UnsafePathError(f"study-root must be a regular directory: {lexical}")
    return lexical.resolve(strict=False)


class ClassifiedOutputBoundary:
    """Bind classified-resource mutations to verified directory generations."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))
        self.canonical_root: Path | None = None
        self._initialized = False
        self._verify_existing_components(self.root)

    @staticmethod
    def _component_chain(path: Path) -> list[Path]:
        chain: list[Path] = []
        current = path
        while True:
            chain.append(current)
            if current.parent == current:
                break
            current = current.parent
        chain.reverse()
        return chain

    def _verify_existing_components(self, path: Path) -> None:
        for component in self._component_chain(Path(os.path.abspath(path))):
            if not os.path.lexists(component):
                continue
            if _is_reparse(component):
                raise ClassifiedOutputBoundaryError(
                    f"classified output contains a symlink/junction/reparse component: {component}"
                )

    def _assert_inside(self, path: Path) -> Path:
        candidate = Path(os.path.abspath(path))
        root_key = os.path.normcase(os.path.normpath(os.fspath(self.root)))
        candidate_key = os.path.normcase(os.path.normpath(os.fspath(candidate)))
        try:
            inside = os.path.commonpath([root_key, candidate_key]) == root_key
        except ValueError:
            inside = False
        if not inside:
            raise ClassifiedOutputBoundaryError(
                f"classified output path escapes its authorized root: {candidate}"
            )
        return candidate

    def _assert_canonical_inside(self, path: Path) -> None:
        if self.canonical_root is None:
            raise ClassifiedOutputBoundaryError("classified output boundary is not initialized")
        if not _is_relative_to(path.resolve(strict=False), self.canonical_root):
            raise ClassifiedOutputBoundaryError(
                f"classified output resolves outside its authorized root: {path}"
            )

    @staticmethod
    def _windows_directory_handles(directory_chain: list[Path]) -> tuple[list[int], Any]:
        import ctypes
        from ctypes import wintypes

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ByHandleFileInformation),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        invalid_handle = ctypes.c_void_p(-1).value
        handles: list[int] = []
        try:
            for directory in directory_chain:
                handle = kernel32.CreateFileW(
                    str(directory),
                    0x0080,
                    0x00000001 | 0x00000002,
                    None,
                    3,
                    0x02000000 | 0x00200000,
                    None,
                )
                if handle in (None, 0, invalid_handle):
                    raise ClassifiedOutputBoundaryError(
                        f"cannot pin classified directory {directory}: winerror={ctypes.get_last_error()}"
                    )
                handles.append(int(handle))
                information = ByHandleFileInformation()
                if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
                    raise ClassifiedOutputBoundaryError(
                        f"cannot verify classified directory {directory}: winerror={ctypes.get_last_error()}"
                    )
                if int(information.dwFileAttributes) & 0x00000400:
                    raise ClassifiedOutputBoundaryError(
                        f"classified directory handle resolved to a reparse point: {directory}"
                    )
            return handles, kernel32
        except BaseException:
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)
            raise

    @staticmethod
    def _windows_create_directory_anchor(directory_handle: int) -> int:
        import ctypes
        from ctypes import wintypes

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.ULONG),
                ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UnicodeString)),
                ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", wintypes.LPVOID),
                ("SecurityQualityOfService", wintypes.LPVOID),
            ]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        ]
        ntdll.NtCreateFile.restype = ctypes.c_long
        anchor_name = f".ga-dirlease-{os.getpid()}-{os.urandom(16).hex()}.tmp"
        name_buffer = ctypes.create_unicode_buffer(anchor_name)
        name_bytes = anchor_name.encode("utf-16-le")
        unicode_name = UnicodeString(
            len(name_bytes),
            len(name_bytes) + 2,
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            directory_handle,
            ctypes.pointer(unicode_name),
            0x00000040,
            None,
            None,
        )
        io_status = IoStatusBlock()
        anchor_handle = wintypes.HANDLE()
        status = ntdll.NtCreateFile(
            ctypes.byref(anchor_handle),
            0x00010000 | 0x00000080 | 0x00100000,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x00000002,
            0x00000001 | 0x00000002,
            2,
            0x00000040 | 0x00000020 | 0x00001000,
            None,
            0,
        )
        if status != 0 or not anchor_handle.value:
            raise ClassifiedOutputBoundaryError(
                f"cannot acquire classified directory anchor: ntstatus=0x{status & 0xFFFFFFFF:08x}"
            )
        return int(anchor_handle.value)

    @staticmethod
    def _windows_create_child_directory(directory_handle: int, name: str) -> int:
        import ctypes
        from ctypes import wintypes

        if not name or name in {".", ".."} or "\\" in name or "/" in name:
            raise ClassifiedOutputBoundaryError(f"unsafe classified directory component: {name!r}")

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.ULONG),
                ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UnicodeString)),
                ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", wintypes.LPVOID),
                ("SecurityQualityOfService", wintypes.LPVOID),
            ]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        ]
        ntdll.NtCreateFile.restype = ctypes.c_long
        name_buffer = ctypes.create_unicode_buffer(name)
        name_bytes = name.encode("utf-16-le")
        unicode_name = UnicodeString(
            len(name_bytes),
            len(name_bytes) + 2,
            ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            directory_handle,
            ctypes.pointer(unicode_name),
            0x00000040,
            None,
            None,
        )
        io_status = IoStatusBlock()
        child_handle = wintypes.HANDLE()
        status = ntdll.NtCreateFile(
            ctypes.byref(child_handle),
            0x00000001 | 0x00000002 | 0x00000004 | 0x00000080 | 0x00100000,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x00000010,
            0x00000001 | 0x00000002,
            2,
            0x00000001 | 0x00000020 | 0x00200000,
            None,
            0,
        )
        if status != 0 or not child_handle.value:
            raise ClassifiedOutputBoundaryError(
                f"cannot create classified directory component {name!r}: ntstatus=0x{status & 0xFFFFFFFF:08x}"
            )
        return int(child_handle.value)

    def _create_directory_chain(self, directory: Path) -> None:
        directory = self._assert_inside(directory)
        missing: list[Path] = []
        current = directory
        while not os.path.lexists(current):
            missing.append(current)
            current = current.parent
        self._verify_existing_components(current)
        if not current.is_dir():
            raise ClassifiedOutputBoundaryError(
                f"classified parent component is not a directory: {current}"
            )
        missing.reverse()
        if not missing:
            return
        if os.name == "nt":
            handles, kernel32 = self._windows_directory_handles(self._component_chain(current))
            anchors: list[int] = []
            try:
                anchors.append(self._windows_create_directory_anchor(handles[-1]))
                parent_handle = handles[-1]
                for component in missing:
                    child_handle = self._windows_create_child_directory(parent_handle, component.name)
                    handles.append(child_handle)
                    anchors.append(self._windows_create_directory_anchor(child_handle))
                    parent_handle = child_handle
            finally:
                for anchor in reversed(anchors):
                    kernel32.CloseHandle(anchor)
                for handle in reversed(handles):
                    kernel32.CloseHandle(handle)
        else:
            flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | int(
                getattr(os, "O_NOFOLLOW", 0)
            )
            descriptors: list[int] = []
            try:
                parent_fd = os.open(current, flags)
                descriptors.append(parent_fd)
                for component in missing:
                    try:
                        os.mkdir(component.name, dir_fd=parent_fd)
                    except FileExistsError as exc:
                        raise ClassifiedOutputBoundaryError(
                            f"concurrent classified directory creation was rejected: {component}"
                        ) from exc
                    child_fd = os.open(component.name, flags, dir_fd=parent_fd)
                    descriptors.append(child_fd)
                    parent_fd = child_fd
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
        self._verify_existing_components(directory)

    def initialize(self) -> "ClassifiedOutputBoundary":
        if not os.path.lexists(self.root):
            self._create_directory_chain(self.root)
        self._verify_existing_components(self.root)
        if not self.root.is_dir():
            raise ClassifiedOutputBoundaryError(
                f"classified output root is not a directory: {self.root}"
            )
        self.canonical_root = self.root.resolve(strict=True)
        self._initialized = True
        return self

    def ensure_directory(self, path: Path) -> Path:
        if not self._initialized:
            self.initialize()
        directory = self._assert_inside(path)
        self._verify_existing_components(directory)
        if not os.path.lexists(directory):
            self._create_directory_chain(directory)
        self._verify_existing_components(directory)
        if not directory.is_dir():
            raise ClassifiedOutputBoundaryError(
                f"classified output path is not a directory: {directory}"
            )
        self._assert_canonical_inside(directory)
        return directory

    def _directory_chain_inside_root(self, directory: Path) -> list[Path]:
        directory = self._assert_inside(directory)
        relative = directory.relative_to(self.root)
        chain = [self.root]
        current = self.root
        for part in relative.parts:
            current = current / part
            chain.append(current)
        return chain

    @contextmanager
    def stable_directory(self, directory: Path) -> Iterator[StableDirectoryLease]:
        directory = self.ensure_directory(directory)
        self._verify_existing_components(directory)
        self._assert_canonical_inside(directory)
        if os.name == "nt":
            handles, kernel32 = self._windows_directory_handles(
                self._directory_chain_inside_root(directory)
            )
            anchor: int | None = None
            # A directory's size/timestamps legitimately change while entries are
            # created, retired, or removed.  The generation check must therefore
            # bind only to the filesystem object identity; the open handles and
            # delete-denying anchor prevent that object from being renamed away.
            planned_stat = directory.stat()
            planned_identity = (planned_stat.st_dev, planned_stat.st_ino)
            try:
                anchor = self._windows_create_directory_anchor(handles[-1])
                self._verify_existing_components(directory)
                self._assert_canonical_inside(directory)
                yield StableDirectoryLease(
                    directory,
                    windows_directory_handle=handles[-1],
                )
                self._verify_existing_components(directory)
                self._assert_canonical_inside(directory)
                current_stat = directory.stat()
                if (current_stat.st_dev, current_stat.st_ino) != planned_identity:
                    raise ClassifiedOutputBoundaryError(
                        f"classified directory identity changed during mutation: {directory}"
                    )
            finally:
                if anchor is not None:
                    kernel32.CloseHandle(anchor)
                for handle in reversed(handles):
                    kernel32.CloseHandle(handle)
            return

        flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | int(
            getattr(os, "O_NOFOLLOW", 0)
        )
        directory_fd = os.open(directory, flags)
        try:
            descriptor_stat = os.fstat(directory_fd)
            path_stat = os.stat(directory, follow_symlinks=False)
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                path_stat.st_dev,
                path_stat.st_ino,
            ):
                raise ClassifiedOutputBoundaryError(
                    f"classified parent changed while acquiring its descriptor: {directory}"
                )
            yield StableDirectoryLease(directory, directory_fd=directory_fd)
            current_path_stat = os.stat(directory, follow_symlinks=False)
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                current_path_stat.st_dev,
                current_path_stat.st_ino,
            ):
                raise ClassifiedOutputBoundaryError(
                    f"classified parent changed during its descriptor-relative mutation: {directory}"
                )
        finally:
            os.close(directory_fd)


class OwnedStableDirectoryLease:
    """Own one stable output-directory lease across stage/commit/cleanup."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(os.path.abspath(directory))
        self.boundary = ClassifiedOutputBoundary(self.directory).initialize()
        self.context = self.boundary.stable_directory(self.directory)
        self.lease = self.context.__enter__()
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.context.__exit__(None, None, None)

    def __enter__(self) -> StableDirectoryLease:
        return self.lease

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.closed:
            return
        self.closed = True
        self.context.__exit__(exc_type, exc, traceback)


def _windows_nt_open_relative(
    parent_handle: int,
    name: str,
    *,
    directory: bool,
    share_access: int,
    desired_access: int | None = None,
) -> int:
    """Open one non-reparse child relative to an already pinned NT directory."""

    import ctypes
    from ctypes import wintypes

    if not name or name in {".", ".."} or "\\" in name or "/" in name:
        raise UnsafePathError(f"unsafe relative filesystem component: {name!r}")

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    ]
    ntdll.NtCreateFile.restype = ctypes.c_long
    name_buffer = ctypes.create_unicode_buffer(name)
    name_bytes = name.encode("utf-16-le")
    unicode_name = UnicodeString(
        len(name_bytes),
        len(name_bytes) + 2,
        ctypes.cast(name_buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent_handle,
        ctypes.pointer(unicode_name),
        0x00000040,
        None,
        None,
    )
    io_status = IoStatusBlock()
    child_handle = wintypes.HANDLE()
    active_access = (
        desired_access
        if desired_access is not None
        else 0x00000001 | 0x00000080 | 0x00100000
    )
    options = 0x00000020 | 0x00200000 | (0x00000001 if directory else 0x00000040)
    status = ntdll.NtCreateFile(
        ctypes.byref(child_handle),
        active_access,
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        0,
        share_access,
        1,
        options,
        None,
        0,
    )
    if status != 0 or not child_handle.value:
        raise FileNotFoundError(
            f"cannot open stable relative {'directory' if directory else 'file'} {name!r}: "
            f"ntstatus=0x{status & 0xFFFFFFFF:08x}"
        )

    handle = int(child_handle.value)
    try:
        attributes_value = _windows_handle_attributes(handle)
        if attributes_value & 0x00000400:
            raise UnsafePathError(f"stable relative child is a reparse point: {name!r}")
        is_directory = bool(attributes_value & 0x00000010)
        if is_directory != directory:
            raise UnsafePathError(
                f"stable relative child has the wrong filesystem type: {name!r}"
            )
        return handle
    except BaseException:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise


def _windows_handle_attributes(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    information = ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise OSError(
            ctypes.get_last_error(),
            "cannot inspect stable Windows file handle",
        )
    return int(information.dwFileAttributes)


def _windows_handle_to_binary_file(handle: int) -> BinaryIO:
    import ctypes
    import msvcrt

    try:
        descriptor = msvcrt.open_osfhandle(
            handle,
            os.O_RDONLY | int(getattr(os, "O_BINARY", 0)),
        )
    except BaseException:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb", buffering=0)


def _windows_binary_handle_path(handle: BinaryIO) -> Path:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    native = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    required = int(kernel32.GetFinalPathNameByHandleW(native, None, 0, 0))
    if required <= 0:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = int(kernel32.GetFinalPathNameByHandleW(native, buffer, len(buffer), 0))
    if written <= 0 or written >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


def _windows_rename_binary_handle(
    handle: BinaryIO,
    root_handle: int,
    target_name: str,
) -> None:
    """No-replace rename one exact open file object relative to a pinned parent."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    if not target_name or Path(target_name).name != target_name:
        raise UnsafePathError(f"rename target must be a basename: {target_name!r}")

    class FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BYTE),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(target_name) + 1)),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

    information = FileRenameInformation()
    information.ReplaceIfExists = 0
    information.RootDirectory = wintypes.HANDLE(root_handle)
    information.FileNameLength = len(target_name.encode("utf-16-le"))
    information.FileName = target_name
    size = FileRenameInformation.FileName.offset + information.FileNameLength
    io_status = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    native = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    status = int(
        ntdll.NtSetInformationFile(
            native,
            ctypes.byref(io_status),
            ctypes.byref(information),
            size,
            10,
        )
    )
    if status < 0:
        raise OSError(
            status & 0xFFFFFFFF,
            f"cannot no-replace rename exact Windows generation to {target_name!r}",
        )


def _windows_delete_binary_handle(handle: BinaryIO) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileDispositionInformation(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    native = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
    information = FileDispositionInformation(True)
    if not kernel32.SetFileInformationByHandle(
        native,
        4,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


class SourceInputBoundary:
    """Open source files relative to a pinned root and consume one exact file object."""

    def __init__(self, root: Path) -> None:
        self.root = _canonical_existing_dir(root, "unitypy_root")
        root_stat = self.root.stat()
        self.root_identity = (root_stat.st_dev, root_stat.st_ino)

    def _assert_root_current(self) -> None:
        if _is_reparse(self.root):
            raise UnsafePathError(f"unitypy_root became a reparse point: {self.root}")
        current = self.root.stat()
        if (current.st_dev, current.st_ino) != self.root_identity:
            raise InputChangedError(f"unitypy_root generation changed: {self.root}")

    @contextmanager
    def stable_file(self, export_path: str) -> Iterator[SourceFileLease]:
        relative = _safe_relative_path(export_path, "export_path")
        lexical = self.root.joinpath(*relative.parts)
        self._assert_root_current()
        _assert_no_reparse_components(self.root, lexical, "export_path")
        if os.name == "nt":
            handles, kernel32 = ClassifiedOutputBoundary._windows_directory_handles(
                [self.root]
            )
            source_handle: BinaryIO | None = None
            try:
                # The root handle denies delete/rename sharing.  Rechecking the
                # pathname identity after acquiring it closes the check-to-open
                # window: if another ordinary directory was substituted before
                # the handle open, it cannot be swapped back before this check.
                self._assert_root_current()
                parent_handle = handles[-1]
                for component in relative.parts[:-1]:
                    child_handle = _windows_nt_open_relative(
                        parent_handle,
                        component,
                        directory=True,
                        share_access=0x00000001 | 0x00000002,
                    )
                    handles.append(child_handle)
                    parent_handle = child_handle
                raw_file_handle = _windows_nt_open_relative(
                    parent_handle,
                    relative.name,
                    directory=False,
                    share_access=0x00000001,
                    desired_access=0x00000001 | 0x00000080 | 0x00010000 | 0x00100000,
                )
                source_handle = _windows_handle_to_binary_file(raw_file_handle)
                opened = os.fstat(source_handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise UnsafePathError(f"source resource is not a regular file: {lexical}")
                opened_identity = _stat_identity(opened)
                yield SourceFileLease(
                    lexical,
                    relative,
                    source_handle,
                    opened_identity,
                    windows_parent_handle=parent_handle,
                )
                if _stat_identity(os.fstat(source_handle.fileno()))[:4] != opened_identity[:4]:
                    raise InputChangedError(
                        f"source resource changed while its stable lease was held: {lexical}"
                    )
            finally:
                if source_handle is not None:
                    source_handle.close()
                for handle in reversed(handles):
                    kernel32.CloseHandle(handle)
            return

        flags = (
            os.O_RDONLY
            | int(getattr(os, "O_DIRECTORY", 0))
            | int(getattr(os, "O_NOFOLLOW", 0))
        )
        descriptors: list[int] = []
        source_handle: BinaryIO | None = None
        try:
            parent_fd = os.open(self.root, flags)
            descriptors.append(parent_fd)
            root_opened = os.fstat(parent_fd)
            if (root_opened.st_dev, root_opened.st_ino) != self.root_identity:
                raise InputChangedError(f"unitypy_root changed while acquiring its descriptor: {self.root}")
            for component in relative.parts[:-1]:
                child_fd = os.open(component, flags, dir_fd=parent_fd)
                descriptors.append(child_fd)
                parent_fd = child_fd
            file_flags = (
                os.O_RDONLY
                | int(getattr(os, "O_NOFOLLOW", 0))
                | int(getattr(os, "O_BINARY", 0))
            )
            source_fd = os.open(relative.name, file_flags, dir_fd=parent_fd)
            source_handle = os.fdopen(source_fd, "rb", buffering=0)
            opened = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise UnsafePathError(f"source resource is not a regular file: {lexical}")
            opened_identity = _stat_identity(opened)
            yield SourceFileLease(
                lexical,
                relative,
                source_handle,
                opened_identity,
                parent_directory_fd=parent_fd,
            )
            if _stat_identity(os.fstat(source_handle.fileno()))[:4] != opened_identity[:4]:
                raise InputChangedError(
                    f"source resource changed while its stable lease was held: {lexical}"
                )
        finally:
            if source_handle is not None:
                source_handle.close()
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def _prepare_study_root(path: Path) -> Path:
    planned = _plan_study_root(path)
    lexical = Path(os.path.abspath(path))
    lexical.mkdir(parents=True, exist_ok=True)
    if not lexical.is_dir() or _is_reparse(lexical):
        raise UnsafePathError(f"study-root must be a regular directory: {lexical}")
    prepared = lexical.resolve(strict=True)
    if _windows_path_key(prepared) != _windows_path_key(planned):
        raise UnsafePathError("study-root identity changed while it was being prepared")
    return prepared


WINDOWS_RESERVED_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def _windows_component_key(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if any(character in '<>"|?*' or ord(character) < 32 for character in normalized):
        raise UnsafePathError(f"{label} contains a Windows-invalid path component: {value!r}")
    trimmed = normalized.rstrip(" .")
    if not trimmed or trimmed != normalized:
        raise UnsafePathError(f"{label} contains a Windows-ambiguous path component: {value!r}")
    if trimmed.split(".", 1)[0].casefold() in WINDOWS_RESERVED_BASENAMES:
        raise UnsafePathError(f"{label} contains a Windows reserved name: {value!r}")
    return trimmed.casefold()


def _windows_relative_key(path: PurePosixPath, label: str) -> tuple[str, ...]:
    return tuple(_windows_component_key(part, label) for part in path.parts)


def _windows_path_key(path: Path) -> tuple[str, ...]:
    absolute = Path(os.path.abspath(path)).resolve(strict=False)
    return tuple(unicodedata.normalize("NFC", part).rstrip(" .").casefold() for part in absolute.parts)


def _key_is_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and left == right[: len(left)]


def _safe_relative_path(value: object, label: str) -> PurePosixPath:
    text = str(value)
    if (
        not text
        or text != text.strip()
        or text != unicodedata.normalize("NFC", text)
        or "\\" in text
        or ":" in text
        or "\x00" in text
    ):
        raise UnsafePathError(f"{label} must be a non-empty POSIX relative path: {value!r}")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        raise UnsafePathError(
            f"{label} must be a canonical POSIX relative path without dot segments or repeated separators: {value!r}"
        )
    _windows_relative_key(path, label)
    return path


def _resolve_study_path(study_root: Path, value: object, label: str) -> Path:
    relative = _safe_relative_path(value, label)
    lexical = study_root.joinpath(*relative.parts)
    _assert_no_reparse_components(study_root, lexical, label)
    resolved = lexical.resolve(strict=False)
    if not _is_relative_to(resolved, study_root):
        raise UnsafePathError(f"{label} resolves outside study-root: {value!r}")
    return lexical


def _resolve_requirements_path(study_root: Path, value: Path) -> Path:
    lexical = Path(os.path.abspath(value))
    temporary_root = study_root / "4.临时目录"
    if not _is_relative_to(lexical, temporary_root):
        raise UnsafePathError("requirements_output must stay under 4.临时目录")
    _assert_no_reparse_components(study_root, lexical, "requirements_output")
    return lexical


def _contract_filename(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(f"{label} must be a non-empty string without surrounding whitespace")
    relative = _safe_relative_path(value, label)
    _windows_relative_key(relative, label)
    return relative


def _validated_contract_filenames(contract: dict[str, Any]) -> dict[str, PurePosixPath]:
    outputs = contract.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("contract outputs must be an object")
    expected_suffixes = {
        "resource_index": ".csv",
        "sequence_index": ".csv",
        "main_candidate_index": ".csv",
        "errors": ".jsonl",
        "summary": ".json",
    }
    if set(outputs) != set(expected_suffixes):
        missing = sorted(set(expected_suffixes) - set(outputs))
        extra = sorted(set(outputs) - set(expected_suffixes))
        raise ValueError(f"contract outputs mismatch; missing={missing}, extra={extra}")
    filenames: dict[str, PurePosixPath] = {}
    semantic: dict[str, tuple[str, ...]] = {}
    for key, suffix in expected_suffixes.items():
        spec = outputs.get(key)
        if not isinstance(spec, dict):
            raise ValueError(f"contract output {key} must be an object")
        relative = _contract_filename(spec.get("filename"), f"contract outputs.{key}.filename")
        if relative.suffix.casefold() != suffix:
            raise ValueError(f"contract output {key} filename must use the {suffix} suffix")
        filenames[key] = relative
        semantic[key] = _windows_relative_key(relative, f"contract outputs.{key}.filename")
    items = list(semantic.items())
    for index, (left_name, left_key) in enumerate(items):
        for right_name, right_key in items[index + 1 :]:
            if _key_is_prefix(left_key, right_key) or _key_is_prefix(right_key, left_key):
                raise ValueError(
                    "contract output filenames must be Windows-semantically unique and must not have "
                    f"file/parent conflicts: {left_name}, {right_name}"
                )
    return filenames


def _resolve_contract_artifacts(index_root: Path, contract: dict[str, Any], study_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for key, relative in _validated_contract_filenames(contract).items():
        lexical = index_root.joinpath(*relative.parts)
        _assert_no_reparse_components(study_root, lexical, f"contract output {key}")
        resolved = lexical.resolve(strict=False)
        if not _is_relative_to(resolved, index_root.resolve(strict=False)):
            raise UnsafePathError(f"contract output {key} resolves outside index_root")
        result[key] = lexical
    return result


def _path_exists_for_identity(path: Path, label: str) -> bool:
    try:
        os.lstat(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise UnsafePathError(f"could not verify filesystem identity for {label} {path}: {exc}") from exc


def _existing_paths_alias(left: Path, right: Path) -> bool:
    left_exists = _path_exists_for_identity(left, "left path")
    right_exists = _path_exists_for_identity(right, "right path")
    if not left_exists or not right_exists:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError as exc:
        raise UnsafePathError(f"could not compare filesystem identity for {left} and {right}: {exc}") from exc


def _assert_artifact_target_ready(study_root: Path, path: Path, label: str) -> None:
    _assert_no_reparse_components(study_root, path, label)
    current = study_root
    for part in path.relative_to(study_root).parts[:-1]:
        current = current / part
        if current.exists() and not current.is_dir():
            raise UnsafePathError(f"{label} has a file where a parent directory is required: {current}")
    if path.exists() and (_is_reparse(path) or not path.is_file()):
        raise UnsafePathError(f"{label} must be a regular non-reparse file when it already exists: {path}")


def _validate_artifact_plan(
    study_root: Path,
    artifacts: dict[str, Path],
    inputs: dict[str, Path],
) -> None:
    artifact_items = list(artifacts.items())
    for name, path in artifact_items:
        _assert_artifact_target_ready(study_root, path, f"output artifact {name}")
    for index, (left_name, left_path) in enumerate(artifact_items):
        left_key = _windows_path_key(left_path)
        for right_name, right_path in artifact_items[index + 1 :]:
            right_key = _windows_path_key(right_path)
            if (
                _key_is_prefix(left_key, right_key)
                or _key_is_prefix(right_key, left_key)
                or _existing_paths_alias(left_path, right_path)
            ):
                raise UnsafePathError(
                    "output artifacts must be Windows-semantically unique and must not have file/parent "
                    f"aliases: {left_name}, {right_name}"
                )
    for artifact_name, artifact_path in artifact_items:
        artifact_key = _windows_path_key(artifact_path)
        for input_name, input_path in inputs.items():
            input_key = _windows_path_key(input_path)
            if (
                _key_is_prefix(artifact_key, input_key)
                or _key_is_prefix(input_key, artifact_key)
                or _existing_paths_alias(artifact_path, input_path)
            ):
                raise UnsafePathError(f"output artifact {artifact_name} aliases input {input_name}")


def _compile_patterns(values: object, label: str) -> tuple[re.Pattern[str], ...]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty array")
    compiled: list[re.Pattern[str]] = []
    for value in values:
        try:
            compiled.append(re.compile(str(value), re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"invalid regex in {label}: {value!r}: {exc}") from exc
    return tuple(compiled)


def _validate_extensions(values: object, label: str) -> frozenset[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty array")
    result: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value.startswith(".") or len(value) < 2 or "/" in value or "\\" in value or value in result:
            raise ValueError(f"invalid or duplicate extension in {label}: {raw!r}")
        result.add(value)
    return frozenset(result)


def _validate_category(category: object, roots: frozenset[str], label: str) -> str:
    relative = _safe_relative_path(category, label)
    if relative.parts[0] not in roots:
        raise ValueError(f"{label} has unsupported classified root: {category!r}")
    return relative.as_posix()


def load_runtime_rules(config_path: Path | InputSnapshot) -> RuntimeRules:
    snapshot = config_path if isinstance(config_path, InputSnapshot) else _capture_input_snapshot(config_path, "config")
    config = _parse_json_snapshot(snapshot, "config")
    if config.get("schema_version") != 2:
        raise ValueError("unsupported UnityPy organization config schema_version")
    roots = frozenset(str(item) for item in config.get("classified_category_roots", []))
    if roots != OFFICIAL_CATEGORY_ROOTS or any(len(_safe_relative_path(item, "classified_category_roots").parts) != 1 for item in roots):
        raise ValueError("classified_category_roots must exactly match the five directory-spec resource roots")

    classification_rules: list[tuple[str, tuple[re.Pattern[str], ...]]] = []
    seen_rule_ids: set[str] = set()
    raw_rules = config.get("image_classification_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("image_classification_rules must be a non-empty array")
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise ValueError(f"image_classification_rules[{index}] must be an object")
        rule_id = str(rule.get("id", "")).strip()
        if not rule_id or rule_id in seen_rule_ids:
            raise ValueError(f"invalid or duplicate classification rule id: {rule_id!r}")
        seen_rule_ids.add(rule_id)
        category = _validate_category(rule.get("category"), roots, f"classification rule {rule_id}")
        classification_rules.append((category, _compile_patterns(rule.get("patterns"), f"classification rule {rule_id}")))

    defaults_raw = config.get("default_image_categories")
    if not isinstance(defaults_raw, dict):
        raise ValueError("default_image_categories must be an object")
    defaults = {
        str(asset_type): _validate_category(category, roots, f"default_image_categories.{asset_type}")
        for asset_type, category in defaults_raw.items()
    }
    sequence = config.get("sequence")
    if not isinstance(sequence, dict):
        raise ValueError("sequence must be an object")
    minimum_frames = int(sequence.get("minimum_distinct_frames", 0))
    if minimum_frames < 1:
        raise ValueError("sequence.minimum_distinct_frames must be at least 1")
    group_scope = sequence.get("group_scope")
    if not isinstance(group_scope, dict):
        raise ValueError("sequence.group_scope must be an object")
    sequence_scope_mode = str(group_scope.get("mode", "")).strip()
    if sequence_scope_mode not in {"source_context", "global"}:
        raise ValueError("sequence.group_scope.mode must be source_context or global")
    raw_scope_fields = group_scope.get("fields")
    allowed_scope_fields = {"source_file", "container_path", "export_parent"}
    if (
        not isinstance(raw_scope_fields, list)
        or not raw_scope_fields
        or any(not isinstance(value, str) or value not in allowed_scope_fields for value in raw_scope_fields)
        or len(set(raw_scope_fields)) != len(raw_scope_fields)
    ):
        raise ValueError(
            "sequence.group_scope.fields must contain unique source_file/container_path/export_parent values"
        )
    explicit_cross_scope = group_scope.get("explicit_context_cross_scope")
    if not isinstance(explicit_cross_scope, bool):
        raise ValueError("sequence.group_scope.explicit_context_cross_scope must be boolean")
    candidate_store = sequence.get("candidate_store")
    if not isinstance(candidate_store, dict):
        raise ValueError("sequence.candidate_store must be an object")
    sequence_spill_threshold = int(candidate_store.get("spill_threshold", 0))
    if sequence_spill_threshold < 1 or sequence_spill_threshold > 10_000_000:
        raise ValueError("sequence.candidate_store.spill_threshold must be between 1 and 10000000")
    actions = frozenset(normalize_sequence_group(str(item)) for item in sequence.get("action_hints", []))
    if not actions:
        raise ValueError("sequence.action_hints must not be empty")
    methods = tuple(str(item).strip().lower() for item in config.get("transfer_methods", []))
    if not methods or len(set(methods)) != len(methods) or any(item not in {"hardlink", "copy"} for item in methods):
        raise ValueError("transfer_methods must contain unique hardlink/copy entries")
    identity_hash_length = int(config.get("collision", {}).get("identity_hash_length", 0))
    if identity_hash_length < 8 or identity_hash_length > 64:
        raise ValueError("collision.identity_hash_length must be between 8 and 64")
    hash_cache_entries = int(config.get("hash_cache_entries", 0))
    if hash_cache_entries < 1 or hash_cache_entries > 100000:
        raise ValueError("hash_cache_entries must be between 1 and 100000")
    export_path_policy = config.get("export_path_policy")
    expected_export_path_policy = {
        "canonical_format": "strict_posix_relative",
        "duplicate_semantics": "windows_case_insensitive",
        "exact_duplicate": "deduplicate",
        "alias_spelling": "reject",
    }
    if export_path_policy != expected_export_path_policy:
        raise ValueError(
            "export_path_policy must declare strict canonical POSIX paths, Windows-semantic aliases, "
            "exact deduplication, and alias-spelling rejection"
        )
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("paths must be an object")
    classified_root = _safe_relative_path(paths.get("classified_root"), "paths.classified_root")
    if classified_root != PurePosixPath("1.分类资源"):
        raise ValueError("paths.classified_root must follow the directory-spec contract")
    index_root = _safe_relative_path(paths.get("index_root"), "paths.index_root")
    if len(index_root.parts) < 3 or index_root.parts[:2] != ("4.临时目录", "中间索引"):
        raise ValueError(
            "paths.index_root must be a non-empty safe subtree under 4.临时目录/中间索引"
        )
    if not str(paths.get("requirements_output", "")).startswith("4.临时目录/"):
        raise ValueError("paths.requirements_output must stay under 4.临时目录")
    main_candidate = _safe_relative_path(paths.get("main_candidate_subdir"), "paths.main_candidate_subdir")
    if main_candidate.parts[0] not in roots:
        raise ValueError("paths.main_candidate_subdir must start with a classified category root")
    metadata_extensions = _validate_extensions(config.get("metadata_extensions"), "metadata_extensions")
    if not REQUIRED_FORBIDDEN_EXTENSIONS.issubset(metadata_extensions):
        raise ValueError("metadata_extensions must include JSON/CSV/TXT/MD")
    return RuntimeRules(
        config=config,
        image_types=frozenset(str(item) for item in config.get("image_asset_types", [])),
        audio_types=frozenset(str(item) for item in config.get("audio_asset_types", [])),
        model_types=frozenset(str(item) for item in config.get("model_related_asset_types", [])),
        render_types=frozenset(str(item) for item in config.get("render_asset_types", [])),
        metadata_extensions=metadata_extensions,
        image_extensions=_validate_extensions(config.get("image_extensions"), "image_extensions"),
        audio_extensions=_validate_extensions(config.get("audio_extensions"), "audio_extensions"),
        category_roots=roots,
        classification_rules=tuple(classification_rules),
        default_image_categories=defaults,
        sequence_patterns=_compile_patterns(sequence.get("name_patterns"), "sequence.name_patterns"),
        sequence_action_hints=actions,
        sequence_context_patterns=_compile_patterns(sequence.get("explicit_context_patterns"), "sequence.explicit_context_patterns"),
        main_candidate_patterns=_compile_patterns(config.get("main_character_candidate_patterns"), "main_character_candidate_patterns"),
        sequence_destination=_validate_category(sequence.get("destination"), roots, "sequence.destination"),
        minimum_distinct_frames=minimum_frames,
        sequence_scope_mode=sequence_scope_mode,
        sequence_scope_fields=tuple(raw_scope_fields),
        sequence_explicit_context_cross_scope=explicit_cross_scope,
        sequence_spill_threshold=sequence_spill_threshold,
        transfer_methods=methods,
        identity_hash_length=identity_hash_length,
        hash_cache_entries=hash_cache_entries,
        export_path_canonical_format=str(export_path_policy["canonical_format"]),
        export_path_duplicate_semantics=str(export_path_policy["duplicate_semantics"]),
        export_path_exact_duplicate=str(export_path_policy["exact_duplicate"]),
        export_path_alias_spelling=str(export_path_policy["alias_spelling"]),
    )


def load_contract(contract_path: Path | InputSnapshot) -> dict[str, Any]:
    snapshot = contract_path if isinstance(contract_path, InputSnapshot) else _capture_input_snapshot(contract_path, "contract")
    contract = _parse_json_snapshot(snapshot, "contract")
    if contract.get("schema_version") != 1 or contract.get("contract_id") != "unitypy-resource-organization":
        raise ValueError("unsupported UnityPy organization contract")
    if contract.get("config_schema_version") != 2:
        raise ValueError("contract config_schema_version is unsupported")
    required = contract.get("required_index_columns")
    outputs = contract.get("outputs")
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError("contract required_index_columns must contain unique non-empty strings")
    _validated_contract_filenames(contract)
    for key in ("resource_index", "sequence_index", "main_candidate_index"):
        spec = outputs[key]
        fields = spec.get("fields")
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in fields)
            or len(set(fields)) != len(fields)
        ):
            raise ValueError(f"contract output {key} fields must contain unique non-empty strings")
    summary_keys = outputs["summary"].get("required_keys")
    if (
        not isinstance(summary_keys, list)
        or not summary_keys
        or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in summary_keys)
        or len(set(summary_keys)) != len(summary_keys)
    ):
        raise ValueError("contract summary.required_keys must contain unique non-empty strings")
    serialization = contract.get("deterministic_serialization")
    expected_serialization = {
        "json_sort_keys": True,
        "json_allow_nan": False,
        "json_indent": 2,
        "json_trailing_newline": True,
        "csv_encoding": "utf-8-sig",
        "csv_lineterminator": "\\n",
    }
    if serialization != expected_serialization:
        raise ValueError("contract deterministic_serialization does not match the implemented strict profile")
    forbidden = frozenset(str(item).lower() for item in contract.get("classified_forbidden_extensions", []))
    if not REQUIRED_FORBIDDEN_EXTENSIONS.issubset(forbidden):
        raise ValueError("contract classified_forbidden_extensions must include JSON/CSV/TXT/MD")
    if contract.get("collision_policy") != EXPECTED_COLLISION_POLICY:
        raise ValueError("contract collision_policy does not match the implemented fail-closed profile")
    if contract.get("source_policy") != EXPECTED_SOURCE_POLICY:
        raise ValueError("contract source_policy does not match the implemented streaming safety profile")
    if contract.get("artifact_policy") != EXPECTED_ARTIFACT_POLICY:
        raise ValueError("contract artifact_policy does not match the implemented alias safety profile")
    if contract.get("control_input_policy") != EXPECTED_CONTROL_INPUT_POLICY:
        raise ValueError("contract control_input_policy does not match the implemented snapshot safety profile")
    return contract


@lru_cache(maxsize=1)
def _default_rules() -> RuntimeRules:
    return load_runtime_rules(DEFAULT_CONFIG)


def safe_name(value: str, fallback: str = "asset") -> str:
    text = (value or "").strip() or fallback
    text = re.sub(r"[^\w.\- ]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._ ")
    return text[:120] or fallback


def normalize_sequence_group(value: str) -> str:
    text = safe_name(value, "sequence")
    text = re.sub(r"[- ]+", "_", text)
    text = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.lower() or "sequence"


def parse_sequence_name(asset_name: str, rules: RuntimeRules | None = None) -> tuple[str, str, int] | None:
    active = rules or _default_rules()
    name = asset_name.strip()
    for pattern in active.sequence_patterns:
        match = pattern.fullmatch(name)
        if not match:
            continue
        subject = match.group("subject").strip("_- ")
        action = match.group("action").strip("_- ")
        if not subject or not action:
            continue
        action_key = normalize_sequence_group(action)
        action_tokens = set(action_key.split("_"))
        if action_key in active.sequence_action_hints or active.sequence_action_hints.intersection(action_tokens):
            return subject, action, int(match.group("frame"))
    return None


def group_from_name(asset_name: str, rules: RuntimeRules | None = None) -> str:
    parsed = parse_sequence_name(asset_name, rules)
    if parsed:
        return safe_name(parsed[0])
    name = safe_name(asset_name)
    for delimiter in ("_", "-"):
        if delimiter in name:
            return safe_name(name.split(delimiter)[0])
    return name


def classify_asset(asset_name: str, asset_type: str, rules: RuntimeRules | None = None, context: str = "") -> tuple[str, str]:
    active = rules or _default_rules()
    if asset_type in active.model_types:
        return "模型", safe_name(asset_type)
    if asset_type in active.audio_types:
        return "音频", group_from_name(asset_name, active)
    if asset_type in active.render_types:
        return "渲染", safe_name(asset_type)
    if asset_type in active.image_types:
        searchable = f"{asset_name}/{context}".replace("\\", "/")
        for category, patterns in active.classification_rules:
            if any(pattern.search(searchable) for pattern in patterns):
                return category, group_from_name(asset_name, active)
        return active.default_image_categories.get(asset_type, "图片/图集"), group_from_name(asset_name, active)
    return "未分类", safe_name(asset_type)


def is_main_character_candidate(asset_name: str, rules: RuntimeRules | None = None, context: str = "") -> bool:
    active = rules or _default_rules()
    searchable = f"{asset_name}/{context}".replace("\\", "/")
    return any(pattern.search(searchable) for pattern in active.main_candidate_patterns)


def split_export_paths(export_path: str) -> list[str]:
    if export_path == "":
        return []
    parts = export_path.split(";")
    if any(part == "" for part in parts):
        raise UnsafePathError(
            f"export_path list must not contain empty semicolon components: {export_path!r}"
        )
    return [
        _safe_relative_path(part, "export_path").as_posix()
        for part in parts
    ]


class CanonicalExportPathStore:
    """Bound global physical-path deduplication to Windows contract semantics."""

    def __init__(self, directory: Path, pass_name: str) -> None:
        self.directory = directory
        self.path = directory / (
            f".export-paths-{safe_name(pass_name)}.ga-unitypy-{os.getpid()}-"
            f"{uuid.uuid4().hex}.sqlite.partial"
        )
        _assert_no_reparse_components(directory, self.path, "export path dedup store")
        self.connection: sqlite3.Connection | None = sqlite3.connect(self.path)
        connection = self.connection
        assert connection is not None
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute(
            "CREATE TABLE seen (semantic_key TEXT PRIMARY KEY, spelling TEXT NOT NULL) WITHOUT ROWID"
        )
        self.accepted_count = 0
        self.duplicate_count = 0

    @staticmethod
    def _semantic_key(relative: PurePosixPath) -> str:
        return json.dumps(
            _windows_relative_key(relative, "export_path"),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    def accept(self, export_path: str) -> bool:
        if self.connection is None:
            raise RuntimeError("export path dedup store is closed")
        relative = _safe_relative_path(export_path, "export_path")
        spelling = relative.as_posix()
        key = self._semantic_key(relative)
        existing = self.connection.execute(
            "SELECT spelling FROM seen WHERE semantic_key=?",
            (key,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != spelling:
                raise UnsafePathError(
                    "distinct export_path spellings alias under Windows semantics: "
                    f"{existing[0]!r}, {spelling!r}"
                )
            self.duplicate_count += 1
            return False
        self.connection.execute(
            "INSERT INTO seen(semantic_key, spelling) VALUES (?, ?)",
            (key, spelling),
        )
        self.accepted_count += 1
        return True

    def close(self) -> None:
        failures: list[str] = []
        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                connection.close()
            except BaseException as exc:
                failures.append(f"connection close: {type(exc).__name__}: {exc}")
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(f"{self.path}{suffix}")
            try:
                if _path_exists_for_identity(path, "export path dedup sidecar"):
                    if _is_reparse(path) or not path.is_file():
                        raise UnsafePathError(
                            f"export path dedup sidecar is not a regular file: {path}"
                        )
                    path.unlink()
            except FileNotFoundError:
                pass
            except BaseException as exc:
                failures.append(f"{path}: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("export path dedup store close failed: " + "; ".join(failures))


def is_metadata_export(row: dict[str, str], export_path: str, rules: RuntimeRules | None = None) -> bool:
    active = rules or _default_rules()
    return row.get("export_kind") == "metadata" or PurePosixPath(export_path.replace("\\", "/")).suffix.lower() in active.metadata_extensions


def should_copy_to_classified(row: dict[str, str], export_path: str, rules: RuntimeRules | None = None) -> bool:
    active = rules or _default_rules()
    if not export_path or is_metadata_export(row, export_path, active):
        return False
    suffix = PurePosixPath(export_path.replace("\\", "/")).suffix.lower()
    asset_type = row.get("asset_type", "")
    if asset_type in active.image_types:
        return suffix in active.image_extensions
    if asset_type in active.audio_types:
        return row.get("export_kind") == "audio" and suffix in active.audio_extensions
    return False


def _path_context(row: dict[str, str], export_path: str) -> str:
    return "/".join(
        (row.get("source_file", ""), row.get("container_path", ""), export_path)
    ).replace("\\", "/")


def _is_deliverable_sequence_path(
    row: dict[str, str],
    export_path: str,
    source_boundary: SourceInputBoundary,
    rules: RuntimeRules,
    include_roots: frozenset[str],
    exclude_roots: frozenset[str],
) -> bool:
    if row.get("status") != "exported":
        return False
    if not should_copy_to_classified(row, export_path, rules):
        return False
    if not category_is_allowed(rules.sequence_destination, include_roots, exclude_roots):
        return False
    try:
        with source_boundary.stable_file(export_path) as source:
            _source_lease_stat(source)
    except (FileNotFoundError, UnsafePathError, InputChangedError, OSError, ValueError):
        return False
    return True


def normalize_category_roots(values: Iterable[str] | None, option_name: str, rules: RuntimeRules | None = None) -> frozenset[str]:
    active = rules or _default_rules()
    roots: set[str] = set()
    for value in values or ():
        root = value.strip()
        if root not in active.category_roots:
            allowed = ", ".join(sorted(active.category_roots))
            raise ValueError(f"{option_name} contains invalid category root {value!r}; allowed roots: {allowed}")
        roots.add(root)
    return frozenset(roots)


def category_is_allowed(category: str, include_roots: frozenset[str], exclude_roots: frozenset[str]) -> bool:
    category_root = category.split("/", 1)[0]
    if include_roots and category_root not in include_roots:
        return False
    return category_root not in exclude_roots


class _DigestingRawReader(io.RawIOBase):
    """Hash the exact byte stream consumed by TextIOWrapper/csv."""

    def __init__(self, raw: io.RawIOBase, digest: Any) -> None:
        super().__init__()
        self.raw = raw
        self.digest = digest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        count = self.raw.readinto(buffer)
        if count:
            self.digest.update(memoryview(buffer)[:count])
        return int(count or 0)

    def fileno(self) -> int:
        return self.raw.fileno()

    def close(self) -> None:
        try:
            self.raw.close()
        finally:
            super().close()


def _iter_rows(
    index_path: Path,
    required_fields: list[str],
) -> Generator[dict[str, str], None, IndexPassEvidence]:
    before_path = index_path.stat()
    digest = hashlib.sha256()
    raw = index_path.open("rb", buffering=0)
    try:
        before_handle = os.fstat(raw.fileno())
        if _stream_stat_identity(before_path) != _stream_stat_identity(before_handle):
            raise InputChangedError(f"source index identity changed before streaming pass: {index_path}")
        digesting = _DigestingRawReader(raw, digest)
        buffered = io.BufferedReader(digesting)
        handle = io.TextIOWrapper(buffered, encoding="utf-8-sig", newline="")
        try:
            reader = csv.DictReader(handle)
            actual = reader.fieldnames or []
            missing = [field for field in required_fields if field not in actual]
            if missing:
                raise ValueError(f"UnityPy index is missing required columns: {', '.join(missing)}")
            for row in reader:
                yield {key: value or "" for key, value in row.items() if key is not None}
            # Force the text/binary buffers to consume through EOF before evidence is captured.
            handle.read()
            after_handle = os.fstat(raw.fileno())
            after_path = index_path.stat()
            return IndexPassEvidence(
                sha256=digest.hexdigest().upper(),
                before_identity=_stream_stat_identity(before_handle),
                after_handle_identity=_stream_stat_identity(after_handle),
                after_path_identity=_stream_stat_identity(after_path),
            )
        finally:
            handle.close()
    finally:
        if not raw.closed:
            raw.close()


class _IndexPassCapture:
    def __init__(self, iterator: Iterator[dict[str, str]]) -> None:
        self.iterator = iter(iterator)
        self.evidence: IndexPassEvidence | None = None

    def __iter__(self) -> _IndexPassCapture:
        return self

    def __next__(self) -> dict[str, str]:
        try:
            return next(self.iterator)
        except StopIteration as completed:
            value = completed.value
            self.evidence = value if isinstance(value, IndexPassEvidence) else None
            raise


def _index_pass_matches_snapshot(evidence: IndexPassEvidence | None, snapshot: FileDigestSnapshot) -> bool:
    return bool(
        evidence is not None
        and evidence.stable
        and evidence.before_identity == snapshot.stat_identity
        and evidence.sha256 == snapshot.sha256
    )


def _has_explicit_sequence_context(row: dict[str, str], rules: RuntimeRules) -> bool:
    context = "/".join((row.get("source_file", ""), row.get("container_path", ""), row.get("export_path", ""))).replace("\\", "/")
    return any(pattern.search(context) for pattern in rules.sequence_context_patterns)


SequenceKey = tuple[str, str, str, str]


def _normalize_sequence_scope_component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip()).replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    return "/".join(parts).casefold() or "-"


def _sequence_scope(row: dict[str, str], rules: RuntimeRules, explicit_context: bool) -> str:
    if rules.sequence_scope_mode == "global" or (
        explicit_context and rules.sequence_explicit_context_cross_scope
    ):
        return "global"
    values: list[tuple[str, str]] = []
    for field in rules.sequence_scope_fields:
        if field == "export_parent":
            export_path = row.get("export_path", "").replace("\\", "/")
            value = PurePosixPath(export_path).parent.as_posix() if export_path else ""
            if value == ".":
                value = ""
        else:
            value = row.get(field, "")
        values.append((field, _normalize_sequence_scope_component(value)))
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sequence_key(parsed: tuple[str, str, int], row: dict[str, str], rules: RuntimeRules) -> SequenceKey:
    subject, action, _ = parsed
    explicit_context = _has_explicit_sequence_context(row, rules)
    return (
        rules.sequence_destination,
        safe_name(subject),
        normalize_sequence_group(action),
        _sequence_scope(row, rules, explicit_context),
    )


def _sequence_scope_id(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]


class SequenceCandidateStore:
    """Bound sequence-candidate memory and spill deterministically to isolated SQLite."""

    def __init__(self, spill_dir: Path, spill_threshold: int) -> None:
        self.spill_dir = spill_dir
        self.spill_threshold = spill_threshold
        self.frames: dict[SequenceKey, set[int]] = defaultdict(set)
        self.explicit_groups: set[SequenceKey] = set()
        self.unique_frame_count = 0
        self.connection: sqlite3.Connection | None = None
        self.spill_path: Path | None = None
        self.confirmed_memory: set[SequenceKey] = set()
        self.ordinals_memory: dict[SequenceKey, dict[int, int]] = {}
        self.multi_scope_memory: set[tuple[str, str, str]] = set()
        self.candidate_group_count = 0
        self.confirmed_group_count = 0
        self.explicit_group_count = 0
        self.finalized = False

    @property
    def spilled(self) -> bool:
        return self.connection is not None

    def _spill_candidates(self) -> None:
        if self.connection is not None:
            return
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        self.spill_path = self.spill_dir / (
            f".sequence-candidates.ga-unitypy-{os.getpid()}-{uuid.uuid4().hex}.sqlite.partial"
        )
        _assert_no_reparse_components(self.spill_dir, self.spill_path, "sequence candidate spill")
        connection = sqlite3.connect(self.spill_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA cache_size=-4096")
            connection.execute(
                "CREATE TABLE frames (destination TEXT, subject TEXT, action TEXT, scope TEXT, "
                "frame INTEGER, PRIMARY KEY(destination, subject, action, scope, frame)) WITHOUT ROWID"
            )
            connection.execute(
                "CREATE TABLE explicit_groups (destination TEXT, subject TEXT, action TEXT, scope TEXT, "
                "PRIMARY KEY(destination, subject, action, scope)) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT OR IGNORE INTO frames VALUES (?, ?, ?, ?, ?)",
                (
                    (destination, subject, action, scope, frame)
                    for (destination, subject, action, scope), frames in self.frames.items()
                    for frame in frames
                ),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO explicit_groups VALUES (?, ?, ?, ?)",
                iter(self.explicit_groups),
            )
            self.frames.clear()
            self.explicit_groups.clear()
            self.connection = connection
        except BaseException as failure:
            cleanup_failures: list[str] = []
            try:
                connection.close()
            except BaseException as exc:
                cleanup_failures.append(f"connection close: {type(exc).__name__}: {exc}")
            try:
                self._remove_spill_files()
            except BaseException as exc:
                cleanup_failures.append(f"file cleanup: {type(exc).__name__}: {exc}")
            if cleanup_failures:
                raise RuntimeError(
                    f"sequence spill initialization failed ({type(failure).__name__}: {failure}); cleanup also failed: "
                    + "; ".join(cleanup_failures)
                ) from failure
            raise

    def add(self, key: SequenceKey, frame: int, explicit_context: bool) -> None:
        if self.finalized:
            raise RuntimeError("sequence candidate store is already finalized")
        if self.connection is None:
            frames = self.frames[key]
            before = len(frames)
            frames.add(frame)
            if len(frames) != before:
                self.unique_frame_count += 1
            if explicit_context:
                self.explicit_groups.add(key)
            if self.unique_frame_count > self.spill_threshold:
                self._spill_candidates()
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO frames VALUES (?, ?, ?, ?, ?)",
            (*key, frame),
        )
        if explicit_context:
            self.connection.execute("INSERT OR IGNORE INTO explicit_groups VALUES (?, ?, ?, ?)", key)

    def finalize(self, minimum_distinct_frames: int) -> None:
        if self.finalized:
            raise RuntimeError("sequence candidate store was finalized twice")
        if self.connection is None:
            self.candidate_group_count = len(self.frames)
            self.explicit_group_count = len(self.explicit_groups)
            self.confirmed_memory = {
                key
                for key, frames in self.frames.items()
                if len(frames) >= minimum_distinct_frames or key in self.explicit_groups
            }
            self.confirmed_group_count = len(self.confirmed_memory)
            self.ordinals_memory = {
                key: {frame: ordinal for ordinal, frame in enumerate(sorted(self.frames[key]), start=1)}
                for key in self.confirmed_memory
            }
            base_counts = Counter(key[:3] for key in self.confirmed_memory)
            self.multi_scope_memory = {base for base, count in base_counts.items() if count > 1}
        else:
            connection = self.connection
            connection.commit()
            self.candidate_group_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM frames GROUP BY destination, subject, action, scope)"
                ).fetchone()[0]
            )
            self.explicit_group_count = int(connection.execute("SELECT COUNT(*) FROM explicit_groups").fetchone()[0])
            connection.execute(
                "CREATE TABLE confirmed (destination TEXT, subject TEXT, action TEXT, scope TEXT, "
                "PRIMARY KEY(destination, subject, action, scope)) WITHOUT ROWID"
            )
            connection.execute(
                "INSERT INTO confirmed "
                "SELECT f.destination, f.subject, f.action, f.scope FROM frames AS f "
                "LEFT JOIN explicit_groups AS e ON e.destination=f.destination AND e.subject=f.subject "
                "AND e.action=f.action AND e.scope=f.scope "
                "GROUP BY f.destination, f.subject, f.action, f.scope "
                "HAVING COUNT(*) >= ? OR MAX(CASE WHEN e.destination IS NULL THEN 0 ELSE 1 END)=1",
                (minimum_distinct_frames,),
            )
            self.confirmed_group_count = int(connection.execute("SELECT COUNT(*) FROM confirmed").fetchone()[0])
            connection.execute(
                "CREATE TABLE ordinals (destination TEXT, subject TEXT, action TEXT, scope TEXT, "
                "frame INTEGER, ordinal INTEGER, "
                "PRIMARY KEY(destination, subject, action, scope, frame)) WITHOUT ROWID"
            )
            read_cursor = connection.execute(
                "SELECT f.destination, f.subject, f.action, f.scope, f.frame "
                "FROM frames AS f JOIN confirmed AS c ON c.destination=f.destination AND c.subject=f.subject "
                "AND c.action=f.action AND c.scope=f.scope "
                "ORDER BY f.destination, f.subject, f.action, f.scope, f.frame"
            )
            ordinal_batch: list[tuple[str, str, str, str, int, int]] = []
            previous_key: SequenceKey | None = None
            ordinal = 0
            for destination, subject, action, scope, frame in read_cursor:
                key = (str(destination), str(subject), str(action), str(scope))
                if key != previous_key:
                    previous_key = key
                    ordinal = 1
                else:
                    ordinal += 1
                ordinal_batch.append((*key, int(frame), ordinal))
                if len(ordinal_batch) >= 1000:
                    connection.executemany("INSERT INTO ordinals VALUES (?, ?, ?, ?, ?, ?)", ordinal_batch)
                    ordinal_batch.clear()
            if ordinal_batch:
                connection.executemany("INSERT INTO ordinals VALUES (?, ?, ?, ?, ?, ?)", ordinal_batch)
            connection.execute(
                "CREATE TABLE multi_scopes (destination TEXT, subject TEXT, action TEXT, "
                "PRIMARY KEY(destination, subject, action)) WITHOUT ROWID"
            )
            connection.execute(
                "INSERT INTO multi_scopes SELECT destination, subject, action FROM confirmed "
                "GROUP BY destination, subject, action HAVING COUNT(*) > 1"
            )
            connection.commit()
        self.finalized = True

    def is_confirmed(self, key: SequenceKey) -> bool:
        if not self.finalized:
            raise RuntimeError("sequence candidate store is not finalized")
        if self.connection is None:
            return key in self.confirmed_memory
        return self.connection.execute(
            "SELECT 1 FROM confirmed WHERE destination=? AND subject=? AND action=? AND scope=?",
            key,
        ).fetchone() is not None

    def ordinal(self, key: SequenceKey, frame: int) -> int:
        if self.connection is None:
            return self.ordinals_memory[key][frame]
        row = self.connection.execute(
            "SELECT ordinal FROM ordinals WHERE destination=? AND subject=? AND action=? AND scope=? AND frame=?",
            (*key, frame),
        ).fetchone()
        if row is None:
            raise KeyError((key, frame))
        return int(row[0])

    def has_multiple_scopes(self, key: SequenceKey) -> bool:
        base = key[:3]
        if self.connection is None:
            return base in self.multi_scope_memory
        return self.connection.execute(
            "SELECT 1 FROM multi_scopes WHERE destination=? AND subject=? AND action=?",
            base,
        ).fetchone() is not None

    def _remove_spill_files(self) -> None:
        if self.spill_path is None:
            return
        failures: list[str] = []
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(f"{self.spill_path}{suffix}")
            try:
                if _path_exists_for_identity(path, "sequence spill sidecar"):
                    if _is_reparse(path) or not path.is_file():
                        raise UnsafePathError(f"sequence spill sidecar is not a regular file: {path}")
                    path.unlink()
            except FileNotFoundError:
                pass
            except BaseException as exc:
                failures.append(f"{path}: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("sequence spill cleanup failed: " + "; ".join(failures))

    def close(self) -> None:
        failures: list[str] = []
        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                connection.close()
            except BaseException as exc:
                failures.append(f"connection close: {type(exc).__name__}: {exc}")
        try:
            self._remove_spill_files()
        except BaseException as exc:
            failures.append(f"file cleanup: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("sequence candidate store close failed: " + "; ".join(failures))


def _source_path(root: Path, export_path: str) -> Path:
    relative = _safe_relative_path(export_path, "export_path")
    lexical = root.joinpath(*relative.parts)
    _assert_no_reparse_components(root, lexical, "export_path")
    resolved = lexical.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise UnsafePathError(f"export_path resolves outside UnityPy root: {export_path!r}")
    return lexical


class HashCache:
    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        self.values: OrderedDict[tuple[object, ...], str] = OrderedDict()

    def get(self, source: SourceFileLease | Path) -> str:
        if isinstance(source, SourceFileLease):
            file_stat = os.fstat(source.handle.fileno())
            key: tuple[object, ...] = (
                "stable-source",
                file_stat.st_dev,
                file_stat.st_ino,
                file_stat.st_size,
                file_stat.st_mtime_ns,
            )
        else:
            file_stat = source.stat()
            key = (str(source), file_stat.st_size, file_stat.st_mtime_ns)
        cached = self.values.pop(key, None)
        if cached is None:
            cached = (
                _source_lease_sha256(source)
                if isinstance(source, SourceFileLease)
                else _sha256(source)
            )
        if isinstance(source, SourceFileLease):
            source.cached_sha256 = cached
        self.values[key] = cached
        while len(self.values) > self.max_entries:
            self.values.popitem(last=False)
        return cached


def _source_lease_stat(source: SourceFileLease) -> os.stat_result:
    current = os.fstat(source.handle.fileno())
    if _stat_identity(current)[:4] != source.opened_identity[:4]:
        raise InputChangedError(
            f"source resource changed after its stable lease was acquired: {source.path}"
        )
    return current


def _source_lease_sha256(source: SourceFileLease) -> str:
    before = _source_lease_stat(source)
    digest = hashlib.sha256()
    source.handle.seek(0)
    for chunk in iter(lambda: source.handle.read(1024 * 1024), b""):
        digest.update(chunk)
    source.handle.seek(0)
    after = _source_lease_stat(source)
    if _stat_identity(before)[:4] != _stat_identity(after)[:4]:
        raise InputChangedError(
            f"source resource changed while it was hashed: {source.path}"
        )
    return digest.hexdigest().upper()


def _source_size(source: SourceFileLease | Path) -> int:
    return (
        int(_source_lease_stat(source).st_size)
        if isinstance(source, SourceFileLease)
        else int(source.stat().st_size)
    )


def _lease_lstat(lease: StableDirectoryLease, name: str) -> os.stat_result:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ClassifiedOutputBoundaryError(f"classified filename must be a basename: {name!r}")
    if os.name == "nt":
        return (lease.path / name).lstat()
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError("stable POSIX classified directory descriptor is unavailable")
    return os.stat(name, dir_fd=lease.directory_fd, follow_symlinks=False)


def _lease_entry_state(lease: StableDirectoryLease, name: str) -> os.stat_result | None:
    try:
        value = _lease_lstat(lease, name)
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(value.st_mode)
        or (os.name == "nt" and _is_reparse(lease.path / name))
    ):
        raise ClassifiedOutputBoundaryError(
            f"classified destination is not a regular non-reparse file: {lease.path / name}"
        )
    return value


def _lease_open_read(lease: StableDirectoryLease, name: str) -> Any:
    if os.name == "nt":
        if lease.windows_directory_handle is None:
            raise ClassifiedOutputBoundaryError(
                "stable Windows classified directory handle is unavailable"
            )
        handle = _windows_nt_open_relative(
            lease.windows_directory_handle,
            name,
            directory=False,
            share_access=0x00000001,
        )
        return _windows_handle_to_binary_file(handle)
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError("stable POSIX classified directory descriptor is unavailable")
    flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0)) | int(getattr(os, "O_BINARY", 0))
    descriptor = os.open(name, flags, dir_fd=lease.directory_fd)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise ClassifiedOutputBoundaryError(
            f"classified destination is not a regular file: {lease.path / name}"
        )
    return os.fdopen(descriptor, "rb", buffering=0)


def _lease_open_mutable_generation(
    lease: StableDirectoryLease,
    name: str,
) -> BinaryIO:
    """Open one exact generation for handle-relative retirement/deletion."""

    if os.name != "nt":
        raise ClassifiedOutputBoundaryError(
            "mandatory exact-generation mutation is unavailable on this POSIX runtime"
        )
    if lease.windows_directory_handle is None:
        raise ClassifiedOutputBoundaryError(
            "stable Windows output directory handle is unavailable"
        )
    handle = _windows_nt_open_relative(
        lease.windows_directory_handle,
        name,
        directory=False,
        share_access=0x00000001,
        desired_access=0x00000001 | 0x00000080 | 0x00010000 | 0x00100000,
    )
    return _windows_handle_to_binary_file(handle)


def _lease_sha256(lease: StableDirectoryLease, name: str) -> str:
    digest = hashlib.sha256()
    with _lease_open_read(lease, name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _lease_same_content(
    source: SourceFileLease | Path,
    lease: StableDirectoryLease,
    name: str,
    source_hash: str,
) -> bool:
    destination_stat = _lease_entry_state(lease, name)
    if destination_stat is None or _source_size(source) != destination_stat.st_size:
        return False
    return source_hash == _lease_sha256(lease, name)


def _lease_samefile(source: SourceFileLease | Path, lease: StableDirectoryLease, name: str) -> bool:
    destination_stat = _lease_entry_state(lease, name)
    if destination_stat is None:
        return False
    source_stat = (
        _source_lease_stat(source)
        if isinstance(source, SourceFileLease)
        else source.stat()
    )
    return (source_stat.st_dev, source_stat.st_ino) == (
        destination_stat.st_dev,
        destination_stat.st_ino,
    )


def _lease_unlink(lease: StableDirectoryLease, name: str, *, missing_ok: bool = False) -> None:
    try:
        state = _lease_entry_state(lease, name)
        if state is None:
            if missing_ok:
                return
            raise FileNotFoundError(lease.path / name)
        if os.name == "nt":
            os.unlink(lease.path / name)
        else:
            if lease.directory_fd is None:
                raise ClassifiedOutputBoundaryError(
                    "stable POSIX classified directory descriptor is unavailable"
                )
            os.unlink(name, dir_fd=lease.directory_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def _lease_publish_no_replace(
    lease: StableDirectoryLease,
    temporary_name: str,
    destination_name: str,
) -> None:
    if _lease_entry_state(lease, destination_name) is not None:
        raise FileExistsError(lease.path / destination_name)
    if os.name == "nt":
        os.rename(lease.path / temporary_name, lease.path / destination_name)
        return
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError("stable POSIX classified directory descriptor is unavailable")
    os.link(
        temporary_name,
        destination_name,
        src_dir_fd=lease.directory_fd,
        dst_dir_fd=lease.directory_fd,
        follow_symlinks=False,
    )
    try:
        os.unlink(temporary_name, dir_fd=lease.directory_fd)
    except BaseException:
        os.unlink(destination_name, dir_fd=lease.directory_fd)
        raise


def _lease_artifact_state(lease: StableDirectoryLease, name: str) -> ArtifactPathState:
    opened = _lease_entry_state(lease, name)
    if opened is None:
        return ArtifactPathState(False, None, None)
    return ArtifactPathState(True, _stat_identity(opened), _lease_sha256(lease, name))


def _artifact_generation_matches(
    actual: ArtifactPathState,
    expected: ArtifactPathState,
) -> bool:
    if not actual.exists or not expected.exists:
        return actual == expected
    if actual.identity is None or expected.identity is None:
        return False
    # Creating the retirement hardlink on POSIX legitimately updates ctime.
    # dev/inode/size/mtime plus the digest still bind the exact file object and
    # bytes without rejecting that metadata-only consequence of retirement.
    return bool(
        actual.identity[:4] == expected.identity[:4]
        and (expected.sha256 is None or actual.sha256 == expected.sha256)
    )


def _recovery_file_record(
    path: Path,
    generation: "ArtifactPathState",
    *,
    kind: str,
) -> dict[str, Any]:
    if not generation.exists or generation.identity is None or generation.sha256 is None:
        raise InputChangedError(
            f"cannot record a missing or unidentified recovery generation: {path}"
        )
    return {
        "kind": kind,
        "path": str(path),
        "identity": list(generation.identity),
        "sha256": generation.sha256,
    }


def _lease_probe_exact_windows_candidate(
    lease: StableDirectoryLease,
    expected: "ArtifactPathState",
    allowed_names: Iterable[str],
) -> str:
    """Resolve an exact open generation without pathname or handle-name helpers.

    This is the independent coordinator fallback used when
    ``GetFinalPathNameByHandleW`` or the ordinary reconciliation helper itself
    fails after a rename syscall.  Each candidate is reopened relative to the
    already pinned NT ``RootDirectory`` and compared by exact generation.
    """

    if os.name != "nt" or lease.windows_directory_handle is None:
        raise ClassifiedOutputBoundaryError(
            "direct exact-candidate probing requires a stable Windows directory handle"
        )
    matches: list[str] = []
    probe_errors: list[str] = []
    seen: set[str] = set()
    for name in allowed_names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        reopened: BinaryIO | None = None
        try:
            native = _windows_nt_open_relative(
                lease.windows_directory_handle,
                name,
                directory=False,
                share_access=0x00000001 | 0x00000004,
                desired_access=0x00000001 | 0x00000080 | 0x00100000,
            )
            reopened = _windows_handle_to_binary_file(native)
            actual = _opened_file_artifact_state(reopened)
            if _artifact_generation_matches(actual, expected):
                matches.append(name)
        except FileNotFoundError:
            continue
        except BaseException as exc:
            probe_errors.append(f"{name}: {type(exc).__name__}: {exc}")
        finally:
            if reopened is not None:
                reopened.close()
    if len(matches) != 1:
        detail = f"; probe errors: {'; '.join(probe_errors)}" if probe_errors else ""
        raise InputChangedError(
            "direct candidate probing could not uniquely locate the exact generation: "
            f"matches={matches}{detail}"
        )
    return matches[0]


def _lease_reconcile_open_windows_name(
    lease: StableDirectoryLease,
    opened: BinaryIO,
    expected: ArtifactPathState,
    allowed_names: Iterable[str],
) -> str:
    current_name = _windows_binary_handle_path(opened).name
    allowed = {name.casefold() for name in allowed_names}
    if current_name.casefold() not in allowed:
        raise InputChangedError(
            f"exact Windows generation moved to an unexpected basename: {current_name!r}"
        )
    if lease.windows_directory_handle is None:
        raise ClassifiedOutputBoundaryError(
            "stable Windows output directory handle is unavailable"
        )
    reopened_native = _windows_nt_open_relative(
        lease.windows_directory_handle,
        current_name,
        directory=False,
        share_access=0x00000001 | 0x00000004,
        desired_access=0x00000001 | 0x00000080 | 0x00100000,
    )
    reopened = _windows_handle_to_binary_file(reopened_native)
    try:
        current = _opened_file_artifact_state(reopened)
    finally:
        reopened.close()
    opened_state = _opened_file_artifact_state(opened)
    if not _artifact_generation_matches(opened_state, expected) or not _artifact_generation_matches(
        current, expected
    ):
        raise InputChangedError(
            f"handle-reported basename does not resolve to the exact expected generation: {current_name!r}"
        )
    return current_name


def _lease_move_no_replace_reconciled(
    lease: StableDirectoryLease,
    source_name: str,
    destination_name: str,
    expected: ArtifactPathState,
) -> tuple[bool, BaseException | None]:
    """Move an owned generation and reconcile kernel state even after exceptions."""

    if _lease_entry_state(lease, destination_name) is not None:
        raise FileExistsError(lease.path / destination_name)
    current_source = _lease_artifact_state(lease, source_name)
    if not _artifact_generation_matches(current_source, expected):
        raise InputChangedError(
            f"rename source is not the expected generation: {lease.path / source_name}"
        )
    if os.name == "nt":
        opened = _lease_open_mutable_generation(lease, source_name)
        try:
            opened_state = _opened_file_artifact_state(opened)
            if not _artifact_generation_matches(opened_state, expected):
                raise InputChangedError(
                    f"rename source changed while acquiring its exact handle: {lease.path / source_name}"
                )
            failure: BaseException | None = None
            try:
                if lease.windows_directory_handle is None:
                    raise ClassifiedOutputBoundaryError(
                        "stable Windows output directory handle is unavailable"
                    )
                _windows_rename_binary_handle(
                    opened,
                    lease.windows_directory_handle,
                    destination_name,
                )
            except BaseException as exc:
                failure = exc
            reconciliation_failure: BaseException | None = None
            try:
                current_name = _lease_reconcile_open_windows_name(
                    lease,
                    opened,
                    expected,
                    (source_name, destination_name),
                )
            except BaseException as exc:
                reconciliation_failure = exc
                current_name = _lease_probe_exact_windows_candidate(
                    lease,
                    expected,
                    (source_name, destination_name),
                )
            return (
                current_name.casefold() == destination_name.casefold(),
                failure if failure is not None else reconciliation_failure,
            )
        finally:
            opened.close()

    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError(
            "stable POSIX output directory descriptor is unavailable"
        )
    failure = None
    try:
        _lease_publish_no_replace(lease, source_name, destination_name)
    except BaseException as exc:
        failure = exc
    source_after = _lease_artifact_state(lease, source_name)
    destination_after = _lease_artifact_state(lease, destination_name)
    moved = (
        not source_after.exists
        and _artifact_generation_matches(destination_after, expected)
    )
    unmoved = (
        _artifact_generation_matches(source_after, expected)
        and not destination_after.exists
    )
    if not moved and not unmoved:
        raise InputChangedError(
            f"could not reconcile descriptor-relative rename {source_name!r} -> {destination_name!r}"
        ) from failure
    return moved, failure


def _lease_write_bytes_exclusive(
    lease: StableDirectoryLease,
    name: str,
    data: bytes,
) -> None:
    if os.name == "nt":
        path = lease.path / name
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError("stable POSIX classified directory descriptor is unavailable")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | int(getattr(os, "O_NOFOLLOW", 0))
        | int(getattr(os, "O_BINARY", 0))
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=lease.directory_fd)
    with os.fdopen(descriptor, "wb", buffering=0) as handle:
        handle.write(data)
        os.fsync(handle.fileno())


def _lease_retire_expected(
    lease: StableDirectoryLease,
    source_name: str,
    retired_name: str,
    expected: ArtifactPathState,
) -> None:
    if not expected.exists or expected.identity is None or expected.sha256 is None:
        raise InputChangedError("summary retirement requires an existing planned generation")
    if _lease_entry_state(lease, retired_name) is not None:
        raise FileExistsError(lease.path / retired_name)
    if os.name == "nt":
        os.rename(lease.path / source_name, lease.path / retired_name)
        retired = _lease_artifact_state(lease, retired_name)
        if retired != expected:
            if _lease_entry_state(lease, source_name) is None:
                _lease_publish_no_replace(lease, retired_name, source_name)
            raise InputChangedError("committed summary generation changed during retirement")
        return
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError("stable POSIX classified directory descriptor is unavailable")
    os.link(
        source_name,
        retired_name,
        src_dir_fd=lease.directory_fd,
        dst_dir_fd=lease.directory_fd,
        follow_symlinks=False,
    )
    retired = _lease_artifact_state(lease, retired_name)
    current = _lease_artifact_state(lease, source_name)
    if (
        not _artifact_generation_matches(retired, expected)
        or not _artifact_generation_matches(current, expected)
        or current.identity is None
        or retired.identity is None
        or current.identity[:2] != retired.identity[:2]
    ):
        _lease_unlink(lease, retired_name, missing_ok=True)
        raise InputChangedError("committed summary generation changed during retirement")
    os.unlink(source_name, dir_fd=lease.directory_fd)


def _lease_validate_summary(
    lease: StableDirectoryLease,
    name: str,
    required_keys: list[str],
) -> None:
    with _lease_open_read(lease, name) as handle:
        value = json.loads(handle.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("summary root must be an object")
    missing = [key for key in required_keys if key not in value]
    if missing:
        raise ValueError(f"summary is missing required keys: {', '.join(missing)}")


def _windows_link_source_handle_to_lease(
    source: SourceFileLease,
    lease: StableDirectoryLease,
    destination_name: str,
) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    if lease.windows_directory_handle is None:
        raise ClassifiedOutputBoundaryError(
            "stable Windows classified directory handle is unavailable"
        )

    class FileLinkInfo(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    encoded = destination_name.encode("utf-16-le")
    size = FileLinkInfo.FileName.offset + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    information = ctypes.cast(buffer, ctypes.POINTER(FileLinkInfo)).contents
    information.ReplaceIfExists = False
    information.RootDirectory = lease.windows_directory_handle
    information.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + FileLinkInfo.FileName.offset,
        encoded,
        len(encoded),
    )
    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_ssize_t), ("Information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    source_handle = msvcrt.get_osfhandle(source.handle.fileno())
    io_status = IoStatusBlock()
    status = ntdll.NtSetInformationFile(
        source_handle,
        ctypes.byref(io_status),
        buffer,
        size,
        11,
    )
    if status != 0:
        raise OSError(
            status & 0xFFFFFFFF,
            "cannot create exact-source Windows hardlink with NtSetInformationFile",
        )


def _posix_link_source_handle_to_lease(
    source: SourceFileLease,
    lease: StableDirectoryLease,
    destination_name: str,
) -> None:
    import ctypes

    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError(
            "stable POSIX classified directory descriptor is unavailable"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    if linkat(
        source.handle.fileno(),
        b"",
        lease.directory_fd,
        os.fsencode(destination_name),
        0x1000,
    ) != 0:
        error = ctypes.get_errno()
        if error == 17:
            raise FileExistsError(lease.path / destination_name)
        raise OSError(error, "exact-source linkat(AT_EMPTY_PATH) unavailable")


def _lease_open_write_exclusive(
    lease: StableDirectoryLease,
    name: str,
) -> BinaryIO:
    if os.name == "nt":
        return (lease.path / name).open("xb", buffering=0)
    if lease.directory_fd is None:
        raise ClassifiedOutputBoundaryError(
            "stable POSIX classified directory descriptor is unavailable"
        )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | int(getattr(os, "O_NOFOLLOW", 0))
        | int(getattr(os, "O_BINARY", 0))
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=lease.directory_fd)
    return os.fdopen(descriptor, "wb", buffering=0)


def _lease_unlink_owned_generation(
    lease: StableDirectoryLease,
    name: str,
    expected: ArtifactPathState | None,
) -> None:
    current = _lease_artifact_state(lease, name)
    if not current.exists:
        return
    if expected is None or not _artifact_generation_matches(current, expected):
        raise InputChangedError(
            f"refused to clean a file generation not owned by this transaction: {lease.path / name}"
        )
    retired_name = (
        f".{name}.ga-owned-retired-{os.getpid()}-{uuid.uuid4().hex}.partial"
    )
    moved, rename_failure = _lease_move_no_replace_reconciled(
        lease,
        name,
        retired_name,
        expected,
    )
    if not moved:
        assert rename_failure is not None
        raise rename_failure
    if os.name != "nt":
        retired = _lease_artifact_state(lease, retired_name)
        if not _artifact_generation_matches(retired, expected):
            raise InputChangedError(
                f"descriptor-relative retirement did not move the expected generation: {retired_name}"
            )
        _lease_unlink(lease, retired_name)
        return
    opened = _lease_open_mutable_generation(lease, retired_name)
    delete_failure: BaseException | None = None
    recovery_failure: BaseException | None = None
    try:
        opened_state = _opened_file_artifact_state(opened)
        if not _artifact_generation_matches(opened_state, expected):
            raise InputChangedError(
                f"retired cleanup name no longer owns the expected generation: {retired_name}"
            )
        current_name = _lease_reconcile_open_windows_name(
            lease,
            opened,
            expected,
            (retired_name,),
        )
        if current_name.casefold() != retired_name.casefold():
            raise InputChangedError(
                f"retired cleanup generation moved unexpectedly: {current_name!r}"
            )
        try:
            _windows_delete_binary_handle(opened)
        except BaseException as exc:
            delete_failure = exc
            # A pre-syscall denial must leave the owned generation under its
            # recorded name so the cleanup ledger remains actionable.  A
            # wrapper may also raise after the delete disposition succeeded;
            # that case is reconciled after closing the exact handle below.
            try:
                assert lease.windows_directory_handle is not None
                _windows_rename_binary_handle(
                    opened,
                    lease.windows_directory_handle,
                    name,
                )
            except BaseException as recovery_exc:
                recovery_failure = recovery_exc
    finally:
        opened.close()

    restored = _lease_artifact_state(lease, name)
    retired_after = _lease_artifact_state(lease, retired_name)
    if delete_failure is not None:
        if not restored.exists and not retired_after.exists:
            # The kernel delete completed and only its wrapper failed.
            return
        if _artifact_generation_matches(restored, expected) and not retired_after.exists:
            raise delete_failure
        if _artifact_generation_matches(retired_after, expected) and not restored.exists:
            detail = (
                f"; recovery rename failed: {type(recovery_failure).__name__}: "
                f"{recovery_failure}"
                if recovery_failure is not None
                else ""
            )
            recovery_path = lease.path / retired_name
            raise RecoveryRequiredError(
                "exact-generation cleanup failed and remained privately retired: "
                f"{recovery_path}{detail}",
                [
                    _recovery_file_record(
                        recovery_path,
                        retired_after,
                        kind="owned_private_generation",
                    )
                ],
            ) from delete_failure
        raise InputChangedError(
            f"could not reconcile exact-generation cleanup failure: {lease.path / name}"
        ) from delete_failure
    if retired_after.exists:
        raise InputChangedError(
            f"exact-generation cleanup did not remove retired name: {retired_name}"
        )


def _lease_create_hardlink_reconciled(
    source: SourceFileLease,
    lease: StableDirectoryLease,
    destination_name: str,
) -> ArtifactPathState:
    if _lease_entry_state(lease, destination_name) is not None:
        raise FileExistsError(lease.path / destination_name)
    source_state = ArtifactPathState(
        True,
        _stat_identity(_source_lease_stat(source)),
        source.cached_sha256 or _source_lease_sha256(source),
    )
    failure: BaseException | None = None
    try:
        if os.name == "nt":
            _windows_link_source_handle_to_lease(source, lease, destination_name)
        else:
            _posix_link_source_handle_to_lease(source, lease, destination_name)
    except BaseException as exc:
        failure = exc
    destination = _lease_artifact_state(lease, destination_name)
    if destination.exists:
        if (
            destination.identity is not None
            and source_state.identity is not None
            and destination.identity[:2] == source_state.identity[:2]
            and destination.sha256 == source_state.sha256
        ):
            return destination
        raise InputChangedError(
            f"hardlink syscall left a foreign destination generation: {lease.path / destination_name}"
        ) from failure
    if failure is not None:
        raise failure
    raise InputChangedError(
        f"hardlink syscall returned success without publishing a destination: {lease.path / destination_name}"
    )


def _transfer_new(
    source: SourceFileLease,
    destination: Path,
    methods: tuple[str, ...],
    lease: StableDirectoryLease,
) -> TransferResult:
    if destination.parent != lease.path:
        raise ClassifiedOutputBoundaryError("classified transfer crossed its stable parent directory")
    errors: list[str] = []
    for method in methods:
        try:
            if method == "hardlink":
                return TransferResult(
                    "hardlink",
                    _lease_create_hardlink_reconciled(
                        source,
                        lease,
                        destination.name,
                    ),
                )
            temporary = destination.with_name(f".{destination.name}.ga-unitypy-{os.getpid()}-{uuid.uuid4().hex}.partial")
            temporary_generation: ArtifactPathState | None = None
            try:
                source.handle.seek(0)
                with _lease_open_write_exclusive(lease, temporary.name) as target:
                    shutil.copyfileobj(source.handle, target, length=1024 * 1024)
                    os.fsync(target.fileno())
                source.handle.seek(0)
                source_stat = _source_lease_stat(source)
                if os.name != "nt" and lease.directory_fd is not None:
                    os.chmod(
                        temporary.name,
                        stat.S_IMODE(source_stat.st_mode),
                        dir_fd=lease.directory_fd,
                        follow_symlinks=False,
                    )
                    os.utime(
                        temporary.name,
                        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                        dir_fd=lease.directory_fd,
                        follow_symlinks=False,
                    )
                temporary_generation = _lease_artifact_state(lease, temporary.name)
                if (
                    temporary_generation.sha256 is None
                    or temporary_generation.sha256
                    != (source.cached_sha256 or _source_lease_sha256(source))
                ):
                    raise InputChangedError(
                        f"copied temporary does not match its stable source object: {temporary}"
                    )
                moved, publish_failure = _lease_move_no_replace_reconciled(
                    lease,
                    temporary.name,
                    destination.name,
                    temporary_generation,
                )
                if not moved:
                    assert publish_failure is not None
                    raise publish_failure
                published = _lease_artifact_state(lease, destination.name)
                if not _artifact_generation_matches(published, temporary_generation):
                    raise InputChangedError(
                        f"copy publish did not preserve the staged generation: {destination}"
                    )
                # The exact generation proves that the kernel operation
                # completed.  A wrapper fault after that point is reconciled as
                # success instead of leaving an unowned ghost destination.
                return TransferResult("copy", published)
            finally:
                if _lease_entry_state(lease, temporary.name) is not None:
                    _lease_unlink_owned_generation(
                        lease,
                        temporary.name,
                        temporary_generation,
                    )
        except OSError as exc:
            errors.append(f"{method}: {exc}")
            if _lease_entry_state(lease, destination.name) is not None:
                break
    raise OSError("; ".join(errors))


def _stable_identity(row: dict[str, str], export_path: str, purpose: str) -> str:
    fields = {
        "asset_id": row.get("asset_id", ""),
        "source_file": row.get("source_file", ""),
        "path_id": row.get("path_id", ""),
        "asset_type": row.get("asset_type", ""),
        "asset_name": row.get("asset_name", ""),
        "export_path": export_path,
        "purpose": purpose,
    }
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _stable_collision_path(destination: Path, identity_sha256: str, length: int) -> Path:
    suffix = identity_sha256[:length].lower()
    stem = destination.stem[:96]
    return destination.with_name(f"{stem}__{suffix}{destination.suffix}")


def _opened_file_artifact_state(handle: BinaryIO) -> ArtifactPathState:
    before = os.fstat(handle.fileno())
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    after = os.fstat(handle.fileno())
    before_identity = _stat_identity(before)
    after_identity = _stat_identity(after)
    if before_identity[:4] != after_identity[:4]:
        raise InputChangedError("opened file generation changed while it was validated")
    return ArtifactPathState(True, after_identity, digest.hexdigest().upper())


def _verify_owned_destination(
    source: SourceFileLease,
    lease: StableDirectoryLease,
    name: str,
    source_hash: str,
    expected: ArtifactPathState,
) -> None:
    with _lease_open_read(lease, name) as owned_handle:
        opened = _opened_file_artifact_state(owned_handle)
        if not _artifact_generation_matches(opened, expected):
            raise InputChangedError(
                f"classified destination generation changed before verification: {lease.path / name}"
            )
        if not _lease_same_content(source, lease, name, source_hash):
            raise OSError(
                f"transferred classified destination failed hash verification: {lease.path / name}"
            )
        current = _lease_artifact_state(lease, name)
        if not _artifact_generation_matches(current, expected):
            raise InputChangedError(
                f"classified destination generation changed during verification: {lease.path / name}"
            )
        reopened = _opened_file_artifact_state(owned_handle)
        if not _artifact_generation_matches(reopened, expected):
            raise InputChangedError(
                f"classified destination object changed during verification: {lease.path / name}"
            )


def _verify_or_cleanup_created_destination(
    source: SourceFileLease,
    lease: StableDirectoryLease,
    name: str,
    source_hash: str,
    expected: ArtifactPathState,
) -> None:
    try:
        _verify_owned_destination(source, lease, name, source_hash, expected)
    except BaseException as primary:
        try:
            _lease_unlink_owned_generation(lease, name, expected)
        except RecoveryRequiredError as cleanup_error:
            raise ClassifiedMaterializationRecoveryError(
                "classified destination verification failed "
                f"({type(primary).__name__}: {primary}); exact cleanup requires recovery: "
                f"{cleanup_error}",
                cleanup_error.recovery_files,
            ) from primary
        except BaseException as cleanup_error:
            raise RuntimeError(
                "classified destination verification failed "
                f"({type(primary).__name__}: {primary}); cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            ) from primary
        raise


@contextmanager
def _coerce_source_file_lease(
    source: SourceFileLease | Path,
) -> Iterator[SourceFileLease]:
    if isinstance(source, SourceFileLease):
        yield source
        return
    canonical = _canonical_existing_file(source, "source resource")
    boundary = SourceInputBoundary(canonical.parent)
    with boundary.stable_file(canonical.name) as leased:
        yield leased


def _materialize(
    source: SourceFileLease | Path,
    destination: Path,
    source_hash: str,
    identity_sha256: str,
    output_root: Path,
    rules: RuntimeRules,
    boundary: ClassifiedOutputBoundary | None = None,
) -> MaterializeResult:
    with _coerce_source_file_lease(source) as leased_source:
        if leased_source.cached_sha256 is None:
            leased_source.cached_sha256 = source_hash
        return _materialize_leased(
            leased_source,
            destination,
            source_hash,
            identity_sha256,
            output_root,
            rules,
            boundary,
        )


def _materialize_leased(
    source: SourceFileLease,
    destination: Path,
    source_hash: str,
    identity_sha256: str,
    output_root: Path,
    rules: RuntimeRules,
    boundary: ClassifiedOutputBoundary | None = None,
) -> MaterializeResult:
    active_boundary = boundary or ClassifiedOutputBoundary(output_root).initialize()
    parent = active_boundary.ensure_directory(destination.parent)
    with active_boundary.stable_directory(parent) as lease:
        destination_state = _lease_entry_state(lease, destination.name)
        if destination_state is not None:
            if _lease_same_content(source, lease, destination.name, source_hash):
                method = "hardlink" if _lease_samefile(source, lease, destination.name) else "copy"
                return MaterializeResult(destination, "reused", method, "none", False, False)
            candidate = _stable_collision_path(destination, identity_sha256, rules.identity_hash_length)
            if candidate.parent != parent:
                raise ClassifiedOutputBoundaryError("classified collision crossed its stable parent")
            candidate_state = _lease_entry_state(lease, candidate.name)
            if candidate_state is not None:
                if not _lease_same_content(source, lease, candidate.name, source_hash):
                    raise DestinationConflictError(
                        f"stable collision destination has different content: {candidate}"
                    )
                method = "hardlink" if _lease_samefile(source, lease, candidate.name) else "copy"
                return MaterializeResult(
                    candidate,
                    "reused",
                    method,
                    "resolved_stable_suffix",
                    True,
                    True,
                )
            try:
                transfer = _transfer_new(source, candidate, rules.transfer_methods, lease)
            except OSError as exc:
                raise DestinationConflictError(
                    f"could not materialize stable collision destination {candidate}: {exc}"
                ) from exc
            _verify_or_cleanup_created_destination(
                source,
                lease,
                candidate.name,
                source_hash,
                transfer.generation,
            )
            return MaterializeResult(
                candidate,
                "created",
                transfer.method,
                "resolved_stable_suffix",
                True,
                True,
            )
        transfer = _transfer_new(source, destination, rules.transfer_methods, lease)
        _verify_or_cleanup_created_destination(
            source,
            lease,
            destination.name,
            source_hash,
            transfer.generation,
        )
        return MaterializeResult(
            destination,
            "created",
            transfer.method,
            "none",
            False,
            False,
        )


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.ga-unitypy-{os.getpid()}-{uuid.uuid4().hex}.partial")


def _files_identical(left: Path, right: Path) -> bool:
    return left.stat().st_size == right.stat().st_size and _sha256(left) == _sha256(right)


def _commit_temporary(temporary: Path, destination: Path) -> bool:
    active = _ACTIVE_ARTIFACT_COMMIT.get()
    if active is not None:
        lease, expected = active
        if destination.parent != lease.path or temporary.parent != lease.path:
            raise ClassifiedOutputBoundaryError(
                "artifact commit crossed its stable output parent"
            )
        destination_state = _lease_artifact_state(lease, destination.name)
        if destination_state.exists:
            if destination_state.sha256 == expected.sha256:
                _lease_unlink_owned_generation(lease, temporary.name, expected)
                return False
            raise InputChangedError(
                f"output artifact appeared or changed after transaction planning: {destination}"
            )
        moved, failure = _lease_move_no_replace_reconciled(
            lease,
            temporary.name,
            destination.name,
            expected,
        )
        if not moved:
            if failure is not None:
                raise failure
            raise InputChangedError(
                f"artifact stage did not publish to its planned destination: {destination}"
            )
        published = _lease_artifact_state(lease, destination.name)
        if not _artifact_generation_matches(published, expected):
            raise InputChangedError(
                f"artifact publish did not preserve the exact staged generation: {destination}"
            )
        # A wrapper fault after a successful no-replace syscall is reconciled
        # by the exact published generation rather than producing a ghost.
        return True
    if destination.exists():
        if _is_reparse(destination) or not destination.is_file():
            raise UnsafePathError(f"output artifact is not a regular file: {destination}")
        if _files_identical(temporary, destination):
            temporary.unlink()
            return False
        raise InputChangedError(f"output artifact appeared or changed after transaction planning: {destination}")
    try:
        # On Windows rename is atomic and refuses an existing destination.  The
        # explicit existence check above also gives injected/concurrent writers
        # a deterministic fail-closed boundary before any overwrite.
        os.rename(temporary, destination)
    except FileExistsError as exc:
        raise InputChangedError(f"output artifact appeared during transaction commit: {destination}") from exc
    return True


def _attempt_cleanup_actions(
    actions: Iterable[tuple[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for label, action in actions:
        try:
            action()
        except BaseException as exc:
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
    return failures


def _raise_primary_with_cleanup(
    primary: BaseException,
    cleanup_failures: list[str],
    label: str,
) -> None:
    if cleanup_failures:
        raise RuntimeError(
            f"{label} failed ({type(primary).__name__}: {primary}); cleanup also failed: "
            + "; ".join(cleanup_failures)
        ) from primary
    raise primary.with_traceback(primary.__traceback__)


def _remove_owned_partial(path: Path, label: str) -> None:
    if not os.path.lexists(path):
        return
    if _is_reparse(path) or not path.is_file():
        raise UnsafePathError(f"{label} is not a regular non-reparse file: {path}")
    path.unlink()


class AtomicCsvWriter:
    def __init__(self, path: Path, fields: list[str]) -> None:
        self.path = path
        self.fields = fields
        self.row_count = 0
        self.prepared = False
        self.lease_owner = OwnedStableDirectoryLease(path.parent)
        self.lease = self.lease_owner.lease
        self.temporary = _temporary_path(path)
        self.handle: TextIO | None = None
        self.temporary_generation: ArtifactPathState | None = None
        try:
            binary = _lease_open_write_exclusive(self.lease, self.temporary.name)
            self.handle = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            self.writer = csv.DictWriter(self.handle, fieldnames=fields, lineterminator="\n", extrasaction="raise")
            self.writer.writeheader()
        except BaseException as failure:
            if self.temporary_generation is None:
                if self.handle is not None and not self.handle.closed:
                    self.temporary_generation = ArtifactPathState(
                        True,
                        _stat_identity(os.fstat(self.handle.fileno())),
                        None,
                    )
                else:
                    current = _lease_artifact_state(self.lease, self.temporary.name)
                    if current.exists:
                        self.temporary_generation = current
            cleanup_failures = self._cleanup_partial()
            try:
                self.lease_owner.close()
            except BaseException as exc:
                cleanup_failures.append(
                    f"close stable CSV directory lease: {type(exc).__name__}: {exc}"
                )
            _raise_primary_with_cleanup(failure, cleanup_failures, "CSV writer construction")

    def write(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row)
        self.row_count += 1

    def prepare(self) -> dict[str, Any]:
        if self.handle is None:
            raise RuntimeError(f"CSV artifact handle is unavailable: {self.path}")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.temporary_generation = ArtifactPathState(
            True,
            _stat_identity(os.fstat(self.handle.fileno())),
            None,
        )
        self.handle.close()
        with _lease_open_read(self.lease, self.temporary.name) as binary:
            handle = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(handle)
            if reader.fieldnames != self.fields:
                raise ValueError(f"CSV reopen fields mismatch: {self.path.name}")
            reopened_count = sum(1 for _ in reader)
        if reopened_count != self.row_count:
            raise ValueError(f"CSV reopen row count mismatch: {self.path.name}")
        self.prepared = True
        self.temporary_generation = _lease_artifact_state(
            self.lease,
            self.temporary.name,
        )
        return {
            "ok": True,
            "row_count": self.row_count,
            "sha256": self.temporary_generation.sha256,
        }

    def commit(self) -> dict[str, Any]:
        if not self.prepared:
            raise RuntimeError(f"CSV artifact was not prepared: {self.path}")
        changed = _commit_temporary(self.temporary, self.path)
        return {
            "ok": True,
            "row_count": self.row_count,
            "sha256": _lease_sha256(self.lease, self.path.name),
            "changed": changed,
        }

    def _cleanup_partial(self) -> list[str]:
        actions: list[tuple[str, Any]] = []
        if self.handle is not None and not self.handle.closed:
            try:
                self.handle.flush()
            except BaseException as exc:
                actions.append((
                    f"flush {self.temporary}",
                    lambda exc=exc: (_ for _ in ()).throw(exc),
                ))
            self.temporary_generation = ArtifactPathState(
                True,
                _stat_identity(os.fstat(self.handle.fileno())),
                None,
            )
            actions.append((f"close {self.temporary}", self.handle.close))
        actions.append((
            f"remove {self.temporary}",
            lambda: _lease_unlink_owned_generation(
                self.lease,
                self.temporary.name,
                self.temporary_generation,
            ),
        ))
        return _attempt_cleanup_actions(actions)

    def abort(self) -> None:
        failures = self._cleanup_partial()
        try:
            self.lease_owner.close()
        except BaseException as exc:
            failures.append(f"close stable CSV directory lease: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("CSV writer abort failed: " + "; ".join(failures))


class AtomicJsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.row_count = 0
        self.prepared = False
        self.lease_owner = OwnedStableDirectoryLease(path.parent)
        self.lease = self.lease_owner.lease
        self.temporary = _temporary_path(path)
        self.handle: TextIO | None = None
        self.temporary_generation: ArtifactPathState | None = None
        try:
            binary = _lease_open_write_exclusive(self.lease, self.temporary.name)
            self.handle = io.TextIOWrapper(binary, encoding="utf-8", newline="")
        except BaseException as failure:
            if self.temporary_generation is None:
                if self.handle is not None and not self.handle.closed:
                    self.temporary_generation = ArtifactPathState(
                        True,
                        _stat_identity(os.fstat(self.handle.fileno())),
                        None,
                    )
                else:
                    current = _lease_artifact_state(self.lease, self.temporary.name)
                    if current.exists:
                        self.temporary_generation = current
            cleanup_failures = self._cleanup_partial()
            try:
                self.lease_owner.close()
            except BaseException as exc:
                cleanup_failures.append(
                    f"close stable JSONL directory lease: {type(exc).__name__}: {exc}"
                )
            _raise_primary_with_cleanup(failure, cleanup_failures, "JSONL writer construction")

    def write(self, value: dict[str, Any]) -> None:
        if self.handle is None:
            raise RuntimeError(f"JSONL artifact handle is unavailable: {self.path}")
        self.handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        self.row_count += 1

    def prepare(self) -> dict[str, Any]:
        if self.handle is None:
            raise RuntimeError(f"JSONL artifact handle is unavailable: {self.path}")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.temporary_generation = ArtifactPathState(
            True,
            _stat_identity(os.fstat(self.handle.fileno())),
            None,
        )
        self.handle.close()
        reopened_count = 0
        with _lease_open_read(self.lease, self.temporary.name) as binary:
            handle = io.TextIOWrapper(binary, encoding="utf-8")
            for line in handle:
                json.loads(line)
                reopened_count += 1
        if reopened_count != self.row_count:
            raise ValueError("JSONL reopen row count mismatch")
        self.prepared = True
        self.temporary_generation = _lease_artifact_state(
            self.lease,
            self.temporary.name,
        )
        return {
            "ok": True,
            "row_count": self.row_count,
            "sha256": self.temporary_generation.sha256,
        }

    def commit(self) -> dict[str, Any]:
        if not self.prepared:
            raise RuntimeError(f"JSONL artifact was not prepared: {self.path}")
        changed = _commit_temporary(self.temporary, self.path)
        return {
            "ok": True,
            "row_count": self.row_count,
            "sha256": _lease_sha256(self.lease, self.path.name),
            "changed": changed,
        }

    def _cleanup_partial(self) -> list[str]:
        actions: list[tuple[str, Any]] = []
        if self.handle is not None and not self.handle.closed:
            try:
                self.handle.flush()
            except BaseException as exc:
                actions.append((
                    f"flush {self.temporary}",
                    lambda exc=exc: (_ for _ in ()).throw(exc),
                ))
            self.temporary_generation = ArtifactPathState(
                True,
                _stat_identity(os.fstat(self.handle.fileno())),
                None,
            )
            actions.append((f"close {self.temporary}", self.handle.close))
        actions.append((
            f"remove {self.temporary}",
            lambda: _lease_unlink_owned_generation(
                self.lease,
                self.temporary.name,
                self.temporary_generation,
            ),
        ))
        return _attempt_cleanup_actions(actions)

    def abort(self) -> None:
        failures = self._cleanup_partial()
        try:
            self.lease_owner.close()
        except BaseException as exc:
            failures.append(f"close stable JSONL directory lease: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("JSONL writer abort failed: " + "; ".join(failures))


@dataclass
class PendingTextArtifact:
    path: Path
    temporary: Path
    validator: Any
    lease_owner: OwnedStableDirectoryLease
    temporary_generation: ArtifactPathState

    @property
    def lease(self) -> StableDirectoryLease:
        return self.lease_owner.lease

    def validation(self) -> dict[str, Any]:
        return {"ok": True, "sha256": _lease_sha256(self.lease, self.temporary.name)}

    def commit(self) -> dict[str, Any]:
        changed = _commit_temporary(self.temporary, self.path)
        self.validator(self.path)
        return {
            "ok": True,
            "sha256": _lease_sha256(self.lease, self.path.name),
            "changed": changed,
        }

    def abort(self) -> None:
        failures = _attempt_cleanup_actions([(
            f"remove {self.temporary}",
            lambda: _lease_unlink_owned_generation(
                self.lease,
                self.temporary.name,
                self.temporary_generation,
            ),
        )])
        try:
            self.lease_owner.close()
        except BaseException as exc:
            failures.append(f"close stable text directory lease: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError("pending text abort failed: " + "; ".join(failures))


def _prepare_atomic_text(path: Path, text: str, validator: Any) -> PendingTextArtifact:
    lease_owner = OwnedStableDirectoryLease(path.parent)
    lease = lease_owner.lease
    temporary = _temporary_path(path)
    temporary_generation: ArtifactPathState | None = None
    try:
        with _lease_open_write_exclusive(lease, temporary.name) as binary:
            handle = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_generation = _lease_artifact_state(lease, temporary.name)
        validator(temporary)
        return PendingTextArtifact(
            path=path,
            temporary=temporary,
            validator=validator,
            lease_owner=lease_owner,
            temporary_generation=temporary_generation,
        )
    except BaseException as failure:
        cleanup_failures = _attempt_cleanup_actions([(
            f"remove {temporary}",
            lambda: _lease_unlink_owned_generation(
                lease,
                temporary.name,
                temporary_generation,
            ),
        )])
        try:
            lease_owner.close()
        except BaseException as exc:
            cleanup_failures.append(
                f"close stable text directory lease: {type(exc).__name__}: {exc}"
            )
        _raise_primary_with_cleanup(failure, cleanup_failures, "text artifact preparation")


def _validate_text(path: Path) -> None:
    path.read_text(encoding="utf-8")


def _validate_summary(path: Path, required_keys: list[str]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("summary root must be an object")
    missing = [key for key in required_keys if key not in value]
    if missing:
        raise ValueError(f"summary is missing required keys: {', '.join(missing)}")


@dataclass(frozen=True)
class ArtifactGroupItem:
    name: str
    destination: Path
    temporary: Path
    expected_sha256: str
    commit: Any
    lease: StableDirectoryLease


@dataclass(frozen=True)
class ArtifactPathState:
    exists: bool
    identity: tuple[int, int, int, int, int] | None
    sha256: str | None


@dataclass
class ArtifactRollbackEntry:
    name: str
    destination: Path
    changed: bool
    backup: Path | None
    published_identity: tuple[int, int, int, int, int] | None = None
    published_generation: ArtifactPathState | None = None
    lease: StableDirectoryLease | None = None
    backup_generation: ArtifactPathState | None = None
    recovery_private: Path | None = None


@dataclass(frozen=True)
class ArtifactGroupCommitResult:
    results: dict[str, dict[str, Any]]
    cleanup_warnings: tuple[dict[str, str], ...]
    published_generations: dict[str, ArtifactPathState]


def _capture_artifact_path_state(path: Path, label: str) -> ArtifactPathState:
    if not _path_exists_for_identity(path, label):
        return ArtifactPathState(False, None, None)
    if _is_reparse(path) or not path.is_file():
        raise UnsafePathError(f"{label} is not a regular non-reparse file: {path}")
    before = path.stat()
    digest = _sha256(path)
    after = path.stat()
    before_identity = _stat_identity(before)
    after_identity = _stat_identity(after)
    if before_identity != after_identity:
        raise InputChangedError(f"{label} changed while its transaction state was captured: {path}")
    return ArtifactPathState(True, after_identity, digest)


def _assert_artifact_path_state_current(path: Path, expected: ArtifactPathState, label: str) -> None:
    current = _capture_artifact_path_state(path, label)
    if current != expected:
        raise InputChangedError(f"{label} changed after transaction planning: {path}")


def _assert_lease_artifact_state_current(
    lease: StableDirectoryLease,
    name: str,
    expected: ArtifactPathState,
    label: str,
) -> None:
    current = _lease_artifact_state(lease, name)
    if current != expected:
        raise InputChangedError(
            f"{label} changed after transaction planning: {lease.path / name}"
        )


def _rollback_backup_path(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.name}.ga-rollback-{os.getpid()}-{uuid.uuid4().hex}.bak"
    )


def _restore_artifact_entry(entry: ArtifactRollbackEntry) -> None:
    if not entry.changed:
        return
    if entry.lease is not None:
        lease = entry.lease
        published = entry.published_generation
        if published is None and entry.published_identity is not None:
            current = _lease_artifact_state(lease, entry.destination.name)
            if current.exists and current.identity == entry.published_identity:
                published = current
        private_name: str | None = None
        private_generation: ArtifactPathState | None = None
        if published is not None:
            private_name = (
                f".{entry.destination.name}.ga-rollback-private-{os.getpid()}-"
                f"{uuid.uuid4().hex}.partial"
            )
            moved, move_failure = _lease_move_no_replace_reconciled(
                lease,
                entry.destination.name,
                private_name,
                published,
            )
            if not moved:
                assert move_failure is not None
                raise move_failure
            entry.recovery_private = entry.destination.parent / private_name
            private_generation = _lease_artifact_state(lease, private_name)

        if entry.backup is not None:
            backup_name = entry.backup.name
            backup_generation = entry.backup_generation
            if backup_generation is None:
                backup_generation = _lease_artifact_state(lease, backup_name)
            if not backup_generation.exists:
                raise FileNotFoundError(f"rollback backup is missing: {entry.backup}")
            moved, restore_failure = _lease_move_no_replace_reconciled(
                lease,
                backup_name,
                entry.destination.name,
                backup_generation,
            )
            if not moved:
                assert restore_failure is not None
                raise restore_failure
            entry.backup = None
            entry.backup_generation = None
            if restore_failure is not None:
                # Kernel state proves the old generation is restored; do not
                # misreport rollback failure solely because a wrapper raised
                # after the successful syscall.
                restored = _lease_artifact_state(lease, entry.destination.name)
                if not _artifact_generation_matches(restored, backup_generation):
                    raise restore_failure

        if private_name is not None and private_generation is not None:
            _lease_unlink_owned_generation(
                lease,
                private_name,
                private_generation,
            )
            entry.recovery_private = None
        return
    if entry.backup is not None:
        if not _path_exists_for_identity(entry.backup, f"rollback backup for {entry.name}"):
            raise FileNotFoundError(f"rollback backup is missing: {entry.backup}")
        if _is_reparse(entry.backup) or not entry.backup.is_file():
            raise UnsafePathError(f"rollback backup is not a regular file: {entry.backup}")
        if entry.published_identity is not None:
            current = _capture_artifact_path_state(entry.destination, f"rollback destination {entry.name}")
            if not current.exists or current.identity != entry.published_identity:
                raise InputChangedError(
                    f"rollback destination no longer belongs to this transaction: {entry.destination}"
                )
        elif _path_exists_for_identity(entry.destination, f"rollback destination {entry.name}"):
            raise InputChangedError(
                f"rollback destination appeared before this transaction published it: {entry.destination}"
            )
        # A single atomic replace restores the backup over the transaction's
        # confirmed entity.  Do not unlink the destination first.
        os.replace(entry.backup, entry.destination)
        return
    if entry.published_identity is None:
        return
    current = _capture_artifact_path_state(entry.destination, f"rollback-created destination {entry.name}")
    if not current.exists or current.identity != entry.published_identity:
        raise InputChangedError(
            f"rollback refused to remove an entity not created by this transaction: {entry.destination}"
        )
    entry.destination.unlink()


def _publish_rollback_failure_marker(
    summary_destination: Path,
    failures: list[Any],
    entries: list[ArtifactRollbackEntry],
    *,
    transaction_state: str = "rollback_incomplete",
    cleanup_state: str = "recovery_required",
    expected_current: ArtifactPathState | None = None,
) -> None:
    marker: dict[str, Any] = {}
    summary_entry = next((entry for entry in entries if entry.name == "summary"), None)
    candidates = [summary_destination]
    if summary_entry is not None and summary_entry.backup is not None:
        candidates.append(summary_entry.backup)
    for candidate in candidates:
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                marker = value
                break
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    marker.update({
        "schema_version": 1,
        "ok": False,
        "transaction_state": transaction_state,
        "cleanup_state": cleanup_state,
        "cleanup_warnings": failures,
        "recovery_backups": [
            str(entry.backup)
            for entry in entries
            if entry.backup is not None and entry.backup.exists()
        ] + [
            str(entry.recovery_private)
            for entry in entries
            if entry.recovery_private is not None and entry.recovery_private.exists()
        ],
    })
    if expected_current is not None:
        boundary = ClassifiedOutputBoundary(summary_destination.parent).initialize()
        temporary_name = _temporary_path(summary_destination).name
        retired_name: str | None = None
        if expected_current.exists:
            retired_name = (
                f".{summary_destination.name}.ga-invalid-{os.getpid()}-{uuid.uuid4().hex}.bak"
            )
            marker["recovery_backups"].append(str(summary_destination.parent / retired_name))
        marker_bytes = (
            json.dumps(marker, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            + "\n"
        ).encode("utf-8")
        with boundary.stable_directory(summary_destination.parent) as lease:
            current = _lease_artifact_state(lease, summary_destination.name)
            if current != expected_current:
                raise InputChangedError(
                    "summary changed before generation-bound failure marker publication"
                )
            try:
                _lease_write_bytes_exclusive(lease, temporary_name, marker_bytes)
                _lease_validate_summary(
                    lease,
                    temporary_name,
                    ["schema_version", "ok", "transaction_state", "cleanup_state"],
                )
                if _lease_artifact_state(lease, summary_destination.name) != expected_current:
                    raise InputChangedError(
                        "summary changed before failure marker retirement"
                    )
                if retired_name is not None:
                    _lease_retire_expected(
                        lease,
                        summary_destination.name,
                        retired_name,
                        expected_current,
                    )
                try:
                    _lease_publish_no_replace(
                        lease,
                        temporary_name,
                        summary_destination.name,
                    )
                except BaseException:
                    if (
                        retired_name is not None
                        and _lease_entry_state(lease, summary_destination.name) is None
                    ):
                        _lease_publish_no_replace(
                            lease,
                            retired_name,
                            summary_destination.name,
                        )
                    raise
                _lease_validate_summary(
                    lease,
                    summary_destination.name,
                    ["schema_version", "ok", "transaction_state", "cleanup_state"],
                )
            finally:
                cleanup_failures = _attempt_cleanup_actions([(
                    f"remove failure-marker temporary {temporary_name}",
                    lambda: _lease_unlink(lease, temporary_name, missing_ok=True),
                )])
                if cleanup_failures:
                    active_failure = sys.exc_info()[1]
                    if active_failure is not None:
                        _raise_primary_with_cleanup(
                            active_failure,
                            cleanup_failures,
                            "generation-bound failure marker publication",
                        )
                    raise RuntimeError(
                        "failure-marker temporary cleanup failed: "
                        + "; ".join(cleanup_failures)
                    )
        return
    temporary = _temporary_path(summary_destination)
    retired_summary: Path | None = None
    try:
        summary_destination.parent.mkdir(parents=True, exist_ok=True)
        if _path_exists_for_identity(summary_destination, "summary before failure marker"):
            if _is_reparse(summary_destination) or not summary_destination.is_file():
                raise UnsafePathError(f"summary before failure marker is not a regular file: {summary_destination}")
            retired_summary = summary_destination.with_name(
                f".{summary_destination.name}.ga-invalid-{os.getpid()}-{uuid.uuid4().hex}.bak"
            )
            os.replace(summary_destination, retired_summary)
            marker["recovery_backups"].append(str(retired_summary))
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(marker, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_summary(temporary, ["schema_version", "ok", "transaction_state", "cleanup_state"])
        os.replace(temporary, summary_destination)
    except BaseException:
        try:
            if temporary.exists() and not _is_reparse(temporary):
                temporary.unlink()
        except BaseException:
            pass
        # The prior fixed summary was retired before marker construction.  If
        # a partial marker nevertheless reached the fixed pathname, make one
        # final best-effort quarantine so a malformed success cannot be served.
        try:
            if summary_destination.exists() and not _is_reparse(summary_destination) and summary_destination.is_file():
                quarantine = summary_destination.with_name(
                    f".{summary_destination.name}.ga-invalid-{os.getpid()}-{uuid.uuid4().hex}.bak"
                )
                os.replace(summary_destination, quarantine)
        except BaseException:
            pass
        raise


def _rollback_artifact_group(
    entries: list[ArtifactRollbackEntry],
    summary_destination: Path,
) -> None:
    failures: list[str] = []
    summary_entries = [entry for entry in entries if entry.name == "summary"]
    non_summary_entries = [entry for entry in entries if entry.name != "summary"]
    for entry in reversed(non_summary_entries):
        try:
            _restore_artifact_entry(entry)
        except BaseException as exc:
            failures.append(f"{entry.destination}: {type(exc).__name__}: {exc}")

    summary_entry = summary_entries[0] if summary_entries else None
    if not failures and summary_entry is not None:
        try:
            _restore_artifact_entry(summary_entry)
        except BaseException as exc:
            failures.append(f"{summary_entry.destination}: {type(exc).__name__}: {exc}")

    if failures:
        try:
            _publish_rollback_failure_marker(summary_destination, failures, entries)
        except BaseException as marker_failure:
            failures.append(
                f"summary failure marker: {type(marker_failure).__name__}: {marker_failure}"
            )
        raise RuntimeError("artifact group rollback failed: " + "; ".join(failures))


def _cleanup_committed_backups(entries: list[ArtifactRollbackEntry]) -> tuple[dict[str, str], ...]:
    warnings: list[dict[str, str]] = []
    for entry in entries:
        backup = entry.backup
        if backup is None:
            continue
        if entry.lease is not None:
            lease = entry.lease
            expected = entry.backup_generation
            try:
                current = _lease_artifact_state(lease, backup.name)
                if not current.exists:
                    continue
                if expected is None or not _artifact_generation_matches(current, expected):
                    raise InputChangedError(
                        f"committed backup generation drifted: {backup}"
                    )
                cleanup_name = (
                    f".{entry.destination.name}.ga-cleanup-orphan-{os.getpid()}-"
                    f"{uuid.uuid4().hex}.bak"
                )
                moved, quarantine_failure = _lease_move_no_replace_reconciled(
                    lease,
                    backup.name,
                    cleanup_name,
                    expected,
                )
                if not moved:
                    assert quarantine_failure is not None
                    raise quarantine_failure
                cleanup_path = entry.destination.parent / cleanup_name
                entry.backup = cleanup_path
                entry.backup_generation = _lease_artifact_state(lease, cleanup_name)
                if quarantine_failure is not None:
                    raise quarantine_failure
                _lease_unlink_owned_generation(
                    lease,
                    cleanup_name,
                    entry.backup_generation,
                )
                entry.backup = None
                entry.backup_generation = None
            except BaseException as exc:
                owned_orphan = ""
                for candidate in (entry.backup, backup):
                    if candidate is None:
                        continue
                    try:
                        candidate_state = _lease_artifact_state(
                            lease,
                            candidate.name,
                        )
                    except BaseException:
                        continue
                    if expected is not None and _artifact_generation_matches(
                        candidate_state,
                        expected,
                    ):
                        owned_orphan = str(candidate)
                        entry.backup = candidate
                        entry.backup_generation = candidate_state
                        break
                warnings.append({
                    "code": "backup_cleanup_failed",
                    "artifact_name": entry.name,
                    "destination": str(entry.destination),
                    "orphan_path": owned_orphan,
                    "message": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            exists = _path_exists_for_identity(backup, f"committed backup for {entry.name}")
        except BaseException as exc:
            warnings.append({
                "code": "backup_identity_check_failed",
                "artifact_name": entry.name,
                "destination": str(entry.destination),
                "orphan_path": str(backup),
                "message": f"{type(exc).__name__}: {exc}",
            })
            continue
        if not exists:
            continue
        cleanup_orphan = entry.destination.with_name(
            f".{entry.destination.name}.ga-cleanup-orphan-{os.getpid()}-{uuid.uuid4().hex}.bak"
        )
        try:
            os.replace(backup, cleanup_orphan)
            entry.backup = cleanup_orphan
        except BaseException as exc:
            warnings.append({
                "code": "backup_quarantine_failed",
                "artifact_name": entry.name,
                "destination": str(entry.destination),
                "orphan_path": str(backup),
                "message": f"{type(exc).__name__}: {exc}",
            })
            continue
        try:
            cleanup_orphan.unlink()
        except BaseException as exc:
            warnings.append({
                "code": "backup_cleanup_failed",
                "artifact_name": entry.name,
                "destination": str(entry.destination),
                "orphan_path": str(cleanup_orphan),
                "message": f"{type(exc).__name__}: {exc}",
            })
    return tuple(warnings)


def _reconcile_owned_cleanup_orphans(artifacts: dict[str, Path]) -> list[str]:
    """Remove only quarantined or explicitly recorded committed-generation backups."""

    cleaned: list[str] = []
    seen: set[Path] = set()
    for destination in artifacts.values():
        parent = destination.parent
        if not parent.is_dir() or _is_reparse(parent):
            continue
        pattern = f".{destination.name}.ga-cleanup-orphan-*.bak"
        for orphan in parent.glob(pattern):
            if orphan in seen:
                continue
            seen.add(orphan)
            if _is_reparse(orphan) or not orphan.is_file():
                raise UnsafePathError(f"owned cleanup orphan is not a regular file: {orphan}")
            owner = OwnedStableDirectoryLease(orphan.parent)
            try:
                expected = _lease_artifact_state(owner.lease, orphan.name)
                _lease_unlink_owned_generation(owner.lease, orphan.name, expected)
            finally:
                owner.close()
            cleaned.append(str(orphan))

    summary_path = artifacts.get("summary")
    if summary_path is None or not _path_exists_for_identity(summary_path, "cleanup-state summary"):
        return cleaned
    if _is_reparse(summary_path) or not summary_path.is_file():
        raise UnsafePathError(f"cleanup-state summary is not a regular file: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return cleaned
    if not isinstance(summary, dict):
        return cleaned
    if summary.get("ok") is not True or summary.get("transaction_state") != "committed":
        return cleaned
    warnings = summary.get("cleanup_warnings")
    output_validation = summary.get("output_validation")
    if not isinstance(warnings, list) or not isinstance(output_validation, dict):
        return cleaned
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        artifact_name = warning.get("artifact_name")
        destination = artifacts.get(artifact_name) if isinstance(artifact_name, str) else None
        if destination is None or not isinstance(warning.get("destination"), str):
            continue
        if _windows_path_key(Path(warning["destination"])) != _windows_path_key(destination):
            continue
        orphan_text = warning.get("orphan_path")
        if not isinstance(orphan_text, str):
            continue
        orphan = Path(orphan_text)
        if not orphan.is_absolute() or _windows_path_key(orphan.parent) != _windows_path_key(destination.parent):
            continue
        allowed_prefixes = (
            f".{destination.name}.ga-cleanup-orphan-",
            f".{destination.name}.ga-rollback-",
        )
        if not orphan.name.endswith(".bak") or not orphan.name.startswith(allowed_prefixes):
            continue
        if orphan in seen or not _path_exists_for_identity(orphan, f"recorded cleanup orphan for {artifact_name}"):
            continue
        if _is_reparse(orphan) or not orphan.is_file():
            raise UnsafePathError(f"recorded cleanup orphan is not a regular file: {orphan}")
        if artifact_name != "summary":
            validation = output_validation.get(artifact_name)
            if not isinstance(validation, dict) or not isinstance(validation.get("sha256"), str):
                continue
            current = _capture_artifact_path_state(destination, f"committed destination for {artifact_name}")
            if not current.exists or current.sha256 != validation["sha256"]:
                continue
        owner = OwnedStableDirectoryLease(orphan.parent)
        try:
            expected = _lease_artifact_state(owner.lease, orphan.name)
            _lease_unlink_owned_generation(owner.lease, orphan.name, expected)
        finally:
            owner.close()
        seen.add(orphan)
        cleaned.append(str(orphan))
    return cleaned


def _reconcile_classified_recovery_records(
    summary_path: Path,
    classified_root: Path,
) -> list[str]:
    """Retry only exact classified private generations named by prior summary."""

    if not _path_exists_for_identity(summary_path, "classified recovery summary"):
        return []
    if _is_reparse(summary_path) or not summary_path.is_file():
        raise UnsafePathError(
            f"classified recovery summary is not a regular file: {summary_path}"
        )
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(summary, dict):
        return []
    records = summary.get("recovery_files")
    if not isinstance(records, list):
        return []
    cleaned: list[str] = []
    root_key = _windows_path_key(classified_root)
    for record in records:
        if not isinstance(record, dict) or record.get("kind") not in {
            "owned_private_generation",
            "classified_private_generation",
        }:
            continue
        path_text = record.get("path")
        identity_value = record.get("identity")
        sha256 = record.get("sha256")
        if (
            not isinstance(path_text, str)
            or not isinstance(identity_value, list)
            or len(identity_value) != 5
            or not all(isinstance(value, int) for value in identity_value)
            or not isinstance(sha256, str)
        ):
            continue
        candidate = Path(path_text)
        candidate_key = _windows_path_key(candidate)
        if (
            not candidate.is_absolute()
            or not _key_is_prefix(root_key, candidate_key)
            or ".ga-owned-retired-" not in candidate.name
            or not candidate.name.endswith(".partial")
        ):
            continue
        if not os.path.lexists(candidate):
            continue
        if _is_reparse(candidate) or not candidate.is_file():
            raise UnsafePathError(
                f"recorded classified recovery path is not a regular file: {candidate}"
            )
        owner = OwnedStableDirectoryLease(candidate.parent)
        try:
            current = _lease_artifact_state(owner.lease, candidate.name)
            expected = ArtifactPathState(True, tuple(identity_value), sha256)
            if not _artifact_generation_matches(current, expected):
                continue
            _lease_unlink_owned_generation(owner.lease, candidate.name, expected)
        finally:
            owner.close()
        cleaned.append(str(candidate))
    return cleaned


def _close_after_fact(resource: Any) -> None:
    """Close one resource and accept a wrapper error only after exact close."""

    try:
        resource.close()
    except BaseException:
        if bool(getattr(resource, "closed", False)):
            return
        raise


class CleanupSummarySuccessHandoff:
    """Keep the published cleanup summary pinned until caller consumption.

    The state is retryable.  No irreversible retirement cleanup occurs until
    every identity/structure check that can fail has completed.  A wrapper
    exception after an underlying close/delete is reconciled from post-state,
    not treated as a reason to discard this owner prematurely.
    """

    def __init__(
        self,
        owner: OwnedStableDirectoryLease,
        handle: BinaryIO,
        path_name: str,
        retired_name: str,
        published: ArtifactPathState,
        retired: ArtifactPathState,
        intended_sha256: str,
        required_keys: list[str],
    ) -> None:
        self.owner = owner
        self.handle = handle
        self.path_name = path_name
        self.retired_name = retired_name
        self.published = published
        self.retired = retired
        self.intended_sha256 = intended_sha256
        self.required_keys = required_keys
        self.state = "open"
        self.release_errors: list[dict[str, str]] = []
        self.release_required_paths: list[str] = []
        self._validated = False
        self._retired_removed = False
        self._handle_closed = False
        self._owner_closed = False

    @property
    def completed(self) -> bool:
        return self.state == "complete"

    def _record_failure(self, operation: str, exc: BaseException) -> None:
        self.release_errors.append(
            {
                "artifact": str(self.owner.directory / self.path_name),
                "operation": operation,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        candidates: list[str] = []
        if not self._retired_removed:
            try:
                current = _lease_artifact_state(self.owner.lease, self.retired_name)
                if _artifact_generation_matches(current, self.retired):
                    candidates.append(str(self.owner.directory / self.retired_name))
            except BaseException:
                pass
        self.release_required_paths = sorted(set(candidates))

    def complete(self) -> None:
        if self.state == "complete":
            return
        if self.state == "completing":
            raise RuntimeError("cleanup-summary handoff completion is already in progress")
        self.state = "completing"
        lease = self.owner.lease
        try:
            if not self._validated:
                opened = _opened_file_artifact_state(self.handle)
                current = _lease_artifact_state(lease, self.path_name)
                retired_current = _lease_artifact_state(lease, self.retired_name)
                if (
                    not _artifact_generation_matches(opened, self.published)
                    or not _artifact_generation_matches(current, self.published)
                    or opened.sha256 != self.intended_sha256
                    or not _artifact_generation_matches(retired_current, self.retired)
                ):
                    raise InputChangedError(
                        "cleanup-status summary or its retirement generation changed "
                        "before caller consumed success"
                    )
                _lease_validate_summary(lease, self.path_name, self.required_keys)
                self._validated = True
            if not self._retired_removed:
                _lease_unlink_owned_generation(
                    lease,
                    self.retired_name,
                    self.retired,
                )
                self._retired_removed = True
            if not self._handle_closed:
                _close_after_fact(self.handle)
                self._handle_closed = True
            if not self._owner_closed:
                _close_after_fact(self.owner)
                self._owner_closed = True
        except BaseException as exc:
            self.state = "recovery_required"
            self._record_failure("complete-cleanup-summary-handoff", exc)
            raise
        self.release_errors = []
        self.release_required_paths = []
        self.state = "complete"


@dataclass
class ArtifactLeaseMember:
    name: str
    path: Path
    owner: OwnedStableDirectoryLease
    handle: BinaryIO
    expected: ArtifactPathState
    handle_closed: bool = False


class ArtifactSetSuccessHandoff:
    """Own the entire committed artifact set through the caller boundary."""

    def __init__(
        self,
        members: list[ArtifactLeaseMember],
        owners: list[OwnedStableDirectoryLease],
        nested_cleanup: CleanupSummarySuccessHandoff | None = None,
    ) -> None:
        self.members = members
        self.owners = owners
        self.nested_cleanup = nested_cleanup
        self.state = "open"
        self.release_errors: list[dict[str, str]] = []
        self.release_required_paths: list[str] = []
        self._validated = False
        self._nested_completed = nested_cleanup is None
        self._closed_owner_ids: set[int] = set()

    @property
    def completed(self) -> bool:
        return self.state == "complete"

    def _error(self, operation: str, artifact: str, exc: BaseException) -> None:
        self.release_errors.append(
            {
                "artifact": artifact,
                "operation": operation,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    def _validate_all(self) -> None:
        for member in self.members:
            opened = _opened_file_artifact_state(member.handle)
            current = _lease_artifact_state(member.owner.lease, member.path.name)
            if (
                not _artifact_generation_matches(opened, member.expected)
                or not _artifact_generation_matches(current, member.expected)
            ):
                raise InputChangedError(
                    f"committed artifact changed before caller consumed success: {member.path}"
                )

    def complete(self) -> None:
        if self.state == "complete":
            return
        if self.state == "completing":
            raise RuntimeError("artifact-set handoff completion is already in progress")
        self.state = "completing"
        self.release_errors = []
        try:
            if not self._validated:
                self._validate_all()
                self._validated = True
            if not self._nested_completed and self.nested_cleanup is not None:
                self.nested_cleanup.complete()
                self._nested_completed = True

            for member in self.members:
                if member.handle_closed:
                    continue
                try:
                    _close_after_fact(member.handle)
                    member.handle_closed = True
                except BaseException as exc:
                    self._error("close-published-artifact-handle", str(member.path), exc)

            for owner in self.owners:
                if id(owner) in self._closed_owner_ids:
                    continue
                try:
                    _close_after_fact(owner)
                    self._closed_owner_ids.add(id(owner))
                except BaseException as exc:
                    self._error("close-published-artifact-parent", str(owner.directory), exc)
        except BaseException as exc:
            self._error("complete-artifact-set-handoff", "artifact-set", exc)

        if self.release_errors:
            self.release_required_paths = sorted(
                str(member.path) for member in self.members
            )
            self.state = "recovery_required"
            raise RuntimeError(
                "artifact-set success handoff release failed: "
                + json.dumps(self.release_errors, ensure_ascii=False, sort_keys=True)
            )
        self.release_required_paths = []
        self.state = "complete"


def _acquire_artifact_set_success_handoff(
    artifact_paths: dict[str, Path],
    expected_generations: dict[str, ArtifactPathState],
    nested_cleanup: CleanupSummarySuccessHandoff | None = None,
) -> ArtifactSetSuccessHandoff:
    owners_by_parent: dict[tuple[str, ...], OwnedStableDirectoryLease] = {}
    owners: list[OwnedStableDirectoryLease] = []
    members: list[ArtifactLeaseMember] = []
    try:
        for name, path in artifact_paths.items():
            expected = expected_generations.get(name)
            if expected is None or not expected.exists:
                raise InputChangedError(
                    f"committed artifact generation is unavailable for handoff: {name}"
                )
            parent_key = _windows_path_key(path.parent)
            owner = owners_by_parent.get(parent_key)
            if owner is None:
                owner = OwnedStableDirectoryLease(path.parent)
                owners_by_parent[parent_key] = owner
                owners.append(owner)
            handle = _lease_open_read(owner.lease, path.name)
            member = ArtifactLeaseMember(name, path, owner, handle, expected)
            members.append(member)
            opened = _opened_file_artifact_state(handle)
            current = _lease_artifact_state(owner.lease, path.name)
            if (
                not _artifact_generation_matches(opened, expected)
                or not _artifact_generation_matches(current, expected)
            ):
                raise InputChangedError(
                    f"committed artifact changed while acquiring success handoff: {path}"
                )
        return ArtifactSetSuccessHandoff(members, owners, nested_cleanup)
    except BaseException as failure:
        cleanup_failures: list[str] = []
        for member in members:
            try:
                _close_after_fact(member.handle)
            except BaseException as exc:
                cleanup_failures.append(
                    f"close handoff member {member.path}: {type(exc).__name__}: {exc}"
                )
        for owner in owners:
            try:
                _close_after_fact(owner)
            except BaseException as exc:
                cleanup_failures.append(
                    f"close handoff parent {owner.directory}: {type(exc).__name__}: {exc}"
                )
        _raise_primary_with_cleanup(
            failure,
            cleanup_failures,
            "artifact-set success handoff acquisition",
        )


def _rewrite_committed_summary_cleanup_state(
    path: Path,
    summary: dict[str, Any],
    required_keys: list[str],
    expected_current_sha256: str,
) -> OrganizationResult:
    owner = OwnedStableDirectoryLease(path.parent)
    lease = owner.lease
    temporary_name = _temporary_path(path).name
    retired_name = (
        f".{path.name}.ga-cleanup-status-retired-{os.getpid()}-{uuid.uuid4().hex}.bak"
    )
    text = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        indent=2,
    ) + "\n"
    intended_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
    handoff: CleanupSummarySuccessHandoff | None = None
    intended_generation: ArtifactPathState | None = None
    try:
        current = _lease_artifact_state(lease, path.name)
        if not current.exists or current.sha256 != expected_current_sha256:
            raise InputChangedError("committed summary changed before cleanup-status update")
        _lease_write_bytes_exclusive(lease, temporary_name, text.encode("utf-8"))
        _lease_validate_summary(lease, temporary_name, required_keys)
        intended_generation = _lease_artifact_state(lease, temporary_name)
        if not intended_generation.exists or intended_generation.sha256 != intended_sha256:
            raise InputChangedError(
                "cleanup-status temporary generation changed before publication"
            )
        if _lease_artifact_state(lease, path.name) != current:
            raise InputChangedError(
                "committed summary changed before cleanup-status retirement"
            )
        moved, retirement_failure = _lease_move_no_replace_reconciled(
            lease,
            path.name,
            retired_name,
            current,
        )
        retired = _lease_artifact_state(lease, retired_name)
        if not moved or not _artifact_generation_matches(retired, current):
            if retirement_failure is not None:
                raise retirement_failure
            raise InputChangedError("cleanup-status retirement did not move the planned summary")
        moved, publish_failure = _lease_move_no_replace_reconciled(
            lease,
            temporary_name,
            path.name,
            intended_generation,
        )
        published = _lease_artifact_state(lease, path.name)
        if not moved or not _artifact_generation_matches(published, intended_generation):
            if publish_failure is not None:
                raise publish_failure
            raise InputChangedError(
                "cleanup-status summary is not the exact temporary generation published by this transaction"
            )
        published_handle = _lease_open_read(lease, path.name)
        try:
            opened = _opened_file_artifact_state(published_handle)
            if not _artifact_generation_matches(opened, published):
                raise InputChangedError(
                    "cleanup-status summary generation changed before final validation"
                )
            _lease_validate_summary(lease, path.name, required_keys)
            handoff = CleanupSummarySuccessHandoff(
                owner,
                published_handle,
                path.name,
                retired_name,
                published,
                retired,
                intended_sha256,
                required_keys,
            )
        except BaseException:
            published_handle.close()
            raise
        return OrganizationResult(
            {
                "ok": True,
                "sha256": intended_sha256,
                "changed": True,
            },
            success_handoff=handoff,
        )
    finally:
        cleanup_failures = _attempt_cleanup_actions([(
            f"remove cleanup-status temporary {temporary_name}",
            lambda: _lease_unlink_owned_generation(
                lease,
                temporary_name,
                intended_generation,
            ),
        )])
        if handoff is None:
            try:
                owner.close()
            except BaseException as exc:
                cleanup_failures.append(
                    f"close cleanup-status directory lease: {type(exc).__name__}: {exc}"
                )
        if cleanup_failures:
            active_failure = sys.exc_info()[1]
            if active_failure is not None:
                _raise_primary_with_cleanup(
                    active_failure,
                    cleanup_failures,
                    "cleanup-status summary publication",
                )
            raise RuntimeError(
                "cleanup-status summary temporary cleanup failed: "
                + "; ".join(cleanup_failures)
            )


def _commit_artifact_group(
    items: list[ArtifactGroupItem],
    config_snapshot: InputSnapshot,
    contract_snapshot: InputSnapshot,
    planned_states: dict[str, ArtifactPathState],
) -> ArtifactGroupCommitResult:
    """Publish the fixed artifact set as one rollback-capable generation."""

    if not items or items[-1].name != "summary":
        raise ValueError("artifact group must be non-empty and commit summary last")
    staged_states: dict[str, ArtifactPathState] = {}
    for item in items:
        staged = _lease_artifact_state(item.lease, item.temporary.name)
        if not staged.exists:
            raise UnsafePathError(f"staged artifact is not a regular file: {item.temporary}")
        if staged.sha256 != item.expected_sha256:
            raise InputChangedError(f"staged artifact changed before group commit: {item.temporary}")
        staged_states[item.name] = staged

    if set(planned_states) != {item.name for item in items}:
        raise ValueError("artifact transaction states do not match the fixed artifact group")
    unchanged = {
        item.name: bool(
            planned_states[item.name].exists
            and planned_states[item.name].sha256 == item.expected_sha256
        )
        for item in items
    }
    if unchanged["summary"] and any(not unchanged[item.name] for item in items[:-1]):
        raise RuntimeError("a changed artifact generation cannot reuse an unchanged successful summary")

    entries: list[ArtifactRollbackEntry] = []
    results: dict[str, dict[str, Any]] = {}
    summary_item = items[-1]
    try:
        _assert_control_inputs_current(config_snapshot, contract_snapshot)
        for item in items:
            _assert_lease_artifact_state_current(
                item.lease,
                item.destination.name,
                planned_states[item.name],
                f"artifact {item.name}",
            )

        # Retire a changing prior success summary before touching any index.
        # On an unrecoverable rollback, downstream readers see no ok=true at
        # the fixed pathname (or an explicit ok=false recovery marker).
        summary_backup: Path | None = None
        summary_entry = ArtifactRollbackEntry(
            name="summary",
            destination=summary_item.destination,
            changed=not unchanged["summary"],
            backup=None,
            lease=summary_item.lease,
        )
        entries.append(summary_entry)
        if not unchanged["summary"] and planned_states["summary"].exists:
            summary_backup = _rollback_backup_path(summary_item.destination)
            summary_entry.backup = summary_backup
            moved, backup_failure = _lease_move_no_replace_reconciled(
                summary_item.lease,
                summary_item.destination.name,
                summary_backup.name,
                planned_states["summary"],
            )
            if moved:
                summary_entry.backup_generation = _lease_artifact_state(
                    summary_item.lease,
                    summary_backup.name,
                )
            if backup_failure is not None:
                raise backup_failure
            if not moved:
                raise InputChangedError("summary retirement did not move the planned generation")

        for item in items:
            _assert_control_inputs_current(config_snapshot, contract_snapshot)
            if item.name == "summary":
                entry = summary_entry
                if unchanged[item.name]:
                    _assert_lease_artifact_state_current(
                        item.lease,
                        item.destination.name,
                        planned_states[item.name],
                        f"artifact {item.name}",
                    )
            else:
                _assert_lease_artifact_state_current(
                    item.lease,
                    item.destination.name,
                    planned_states[item.name],
                    f"artifact {item.name}",
                )
                backup: Path | None = None
                entry = ArtifactRollbackEntry(
                    name=item.name,
                    destination=item.destination,
                    changed=not unchanged[item.name],
                    backup=None,
                    lease=item.lease,
                )
                entries.append(entry)
                if not unchanged[item.name] and planned_states[item.name].exists:
                    backup = _rollback_backup_path(item.destination)
                    entry.backup = backup
                    moved, backup_failure = _lease_move_no_replace_reconciled(
                        item.lease,
                        item.destination.name,
                        backup.name,
                        planned_states[item.name],
                    )
                    if moved:
                        entry.backup_generation = _lease_artifact_state(
                            item.lease,
                            backup.name,
                        )
                    if backup_failure is not None:
                        raise backup_failure
                    if not moved:
                        raise InputChangedError(
                            f"artifact retirement did not move the planned generation: {item.name}"
                        )
            commit_token = _ACTIVE_ARTIFACT_COMMIT.set(
                (item.lease, staged_states[item.name])
            )
            try:
                result = item.commit()
            except BaseException:
                # A validator can fail after the atomic publish but before the
                # commit callback returns.  Adopt only an entity whose digest
                # exactly matches this transaction's staged artifact so
                # rollback can restore/remove it without claiming a late file.
                if entry.changed:
                    current_after_error = _lease_artifact_state(
                        item.lease,
                        item.destination.name,
                    )
                    if _artifact_generation_matches(
                        current_after_error,
                        staged_states[item.name],
                    ):
                        entry.published_identity = current_after_error.identity
                        entry.published_generation = current_after_error
                raise
            finally:
                _ACTIVE_ARTIFACT_COMMIT.reset(commit_token)
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError(f"artifact commit did not return a valid result: {item.name}")
            if entry.changed and not result.get("changed"):
                raise InputChangedError(
                    f"artifact destination state changed after planning: {item.destination}"
                )
            committed_state = _lease_artifact_state(item.lease, item.destination.name)
            if not committed_state.exists:
                raise UnsafePathError(f"committed artifact is not a regular file: {item.destination}")
            actual_sha256 = committed_state.sha256
            if actual_sha256 != item.expected_sha256 or result.get("sha256") != item.expected_sha256:
                raise RuntimeError(f"committed artifact hash mismatch: {item.name}")
            if entry.changed:
                if not _artifact_generation_matches(
                    committed_state,
                    staged_states[item.name],
                ):
                    raise InputChangedError(
                        f"committed artifact is not the exact staged generation: {item.name}"
                    )
                entry.published_identity = committed_state.identity
                entry.published_generation = committed_state
            results[item.name] = result
            _assert_control_inputs_current(config_snapshot, contract_snapshot)

        for item in items:
            if _lease_sha256(item.lease, item.destination.name) != item.expected_sha256:
                raise RuntimeError(f"artifact group changed during final validation: {item.name}")
        _assert_control_inputs_current(config_snapshot, contract_snapshot)
    except BaseException as failure:
        try:
            _rollback_artifact_group(entries, summary_item.destination)
        except BaseException as rollback_failure:
            raise RuntimeError(
                f"artifact group commit failed ({type(failure).__name__}: {failure}) and rollback failed "
                f"({type(rollback_failure).__name__}: {rollback_failure})"
            ) from rollback_failure
        raise

    published_generations = {
        item.name: _lease_artifact_state(item.lease, item.destination.name)
        for item in items
    }
    cleanup_warnings = _cleanup_committed_backups(entries)
    return ArtifactGroupCommitResult(
        results=results,
        cleanup_warnings=cleanup_warnings,
        published_generations=published_generations,
    )


def _record_transfer(counters: Counter[str], result: MaterializeResult) -> None:
    counters[result.operation_status] += 1
    counters["hardlinked" if result.transfer_method == "hardlink" else "copied"] += 1
    if result.conflict:
        counters["conflict"] += 1
    if result.resolved_conflict:
        counters["resolved_conflict"] += 1


def _cleanup_organize_objects(
    *,
    writers: Iterable[Any] = (),
    pending_texts: Iterable[PendingTextArtifact] = (),
    sequence_store: SequenceCandidateStore | None = None,
    export_path_stores: Iterable[CanonicalExportPathStore] = (),
) -> list[str]:
    actions: list[tuple[str, Any]] = []
    for index, writer in enumerate(writers, start=1):
        actions.append((f"abort writer {index}", writer.abort))
    for index, pending in enumerate(pending_texts, start=1):
        actions.append((f"abort pending text {index}", pending.abort))
    if sequence_store is not None:
        actions.append(("close sequence candidate store", sequence_store.close))
    for index, store in enumerate(export_path_stores, start=1):
        actions.append((f"close export path store {index}", store.close))
    return _attempt_cleanup_actions(actions)


def organize(
    index_path: Path,
    unitypy_root: Path,
    study_root: Path,
    project_name: str,
    requirements_output: Path | None = None,
    include_category_roots: Iterable[str] | None = None,
    exclude_category_roots: Iterable[str] | None = None,
    config_path: Path = DEFAULT_CONFIG,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    source_root = _canonical_existing_dir(unitypy_root, "unitypy_root")
    source_boundary = SourceInputBoundary(source_root)
    source_index = _canonical_existing_file(index_path, "index")
    if not _is_relative_to(source_index, source_root):
        raise UnsafePathError("index must stay under unitypy_root")
    planned_study_root = _plan_study_root(study_root)
    config_snapshot = _capture_input_snapshot(config_path, "config")
    contract_snapshot = _capture_input_snapshot(contract_path, "contract")
    config_file = config_snapshot.path
    contract_file = contract_snapshot.path
    rules = load_runtime_rules(config_snapshot)
    contract = load_contract(contract_snapshot)
    include_roots = normalize_category_roots(include_category_roots, "include_category_roots", rules)
    exclude_roots = normalize_category_roots(exclude_category_roots, "exclude_category_roots", rules)
    overlap = include_roots & exclude_roots
    if overlap:
        raise ValueError(f"category roots cannot be both included and excluded: {', '.join(sorted(overlap))}")

    paths = rules.config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("config paths must be an object")
    classified_root = _resolve_study_path(planned_study_root, paths.get("classified_root"), "paths.classified_root")
    index_root = _resolve_study_path(planned_study_root, paths.get("index_root"), "paths.index_root")
    main_relative = _safe_relative_path(paths.get("main_candidate_subdir"), "paths.main_candidate_subdir")
    main_root = classified_root.joinpath(*main_relative.parts)
    if requirements_output is None:
        model_note = _resolve_study_path(planned_study_root, paths.get("requirements_output"), "paths.requirements_output")
    else:
        model_note = _resolve_requirements_path(planned_study_root, requirements_output)
    if _is_relative_to(classified_root, index_root) or _is_relative_to(index_root, classified_root):
        raise UnsafePathError("classified_root and index_root must not overlap")
    if _paths_overlap(source_root, classified_root) or _paths_overlap(source_root, index_root):
        raise UnsafePathError("unitypy_root must not overlap classified_root or index_root")

    outputs = contract["outputs"]
    resource_spec = outputs["resource_index"]
    sequence_spec = outputs["sequence_index"]
    main_spec = outputs["main_candidate_index"]
    artifact_paths = _resolve_contract_artifacts(index_root, contract, planned_study_root)
    artifact_paths["requirements_note"] = model_note
    _validate_artifact_plan(
        planned_study_root,
        artifact_paths,
        {"index": source_index, "config": config_file, "contract": contract_file},
    )
    reconciled_cleanup_orphans = _reconcile_owned_cleanup_orphans(artifact_paths)
    reconciled_cleanup_orphans.extend(
        _reconcile_classified_recovery_records(
            artifact_paths["summary"],
            classified_root,
        )
    )
    artifact_plan_states = {
        name: _capture_artifact_path_state(path, f"initial artifact plan {name}")
        for name, path in artifact_paths.items()
    }

    required_fields = [str(item) for item in contract["required_index_columns"]]
    source_index_snapshot = _capture_file_digest_snapshot(source_index, "source index")
    source_index_sha256 = source_index_snapshot.sha256
    config_sha256 = config_snapshot.sha256
    contract_sha256 = contract_snapshot.sha256

    _assert_control_inputs_current(config_snapshot, contract_snapshot)
    study_root = _prepare_study_root(planned_study_root)
    index_root.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_components(study_root, index_root, "index_root")
    _assert_control_inputs_current(config_snapshot, contract_snapshot)

    sequence_store = SequenceCandidateStore(index_root, rules.sequence_spill_threshold)
    first_export_path_store = CanonicalExportPathStore(index_root, "first-pass")
    source_row_count = 0
    export_path_count = 0
    model_related_object_count = 0
    try:
        first_pass = _IndexPassCapture(_iter_rows(source_index, required_fields))
        for row in first_pass:
            source_row_count += 1
            row_export_paths = [
                export_path
                for export_path in split_export_paths(row.get("export_path", ""))
                if first_export_path_store.accept(export_path)
            ]
            export_path_count += len(row_export_paths)
            if row.get("asset_type") in rules.model_types:
                model_related_object_count += 1
            if row.get("asset_type") not in rules.image_types:
                continue
            parsed = parse_sequence_name(row.get("asset_name", ""), rules)
            if parsed is None:
                continue
            for export_path in row_export_paths:
                row_for_path = dict(row)
                row_for_path["export_path"] = export_path
                if not _is_deliverable_sequence_path(
                    row_for_path,
                    export_path,
                    source_boundary,
                    rules,
                    include_roots,
                    exclude_roots,
                ):
                    continue
                key = _sequence_key(parsed, row_for_path, rules)
                sequence_store.add(
                    key,
                    parsed[2],
                    _has_explicit_sequence_context(row_for_path, rules),
                )
        sequence_store.finalize(rules.minimum_distinct_frames)
        first_export_path_duplicate_count = first_export_path_store.duplicate_count
        first_export_path_store.close()
        first_export_path_store = None
        first_pass_evidence = first_pass.evidence
        if not _index_pass_matches_snapshot(first_pass_evidence, source_index_snapshot):
            raise InputChangedError(
                "source index first pass did not consume the snapshotted generation"
            )
        sequence_candidate_group_count = sequence_store.candidate_group_count
        confirmed_sequence_group_count = sequence_store.confirmed_group_count
        explicit_sequence_group_count = sequence_store.explicit_group_count
        sequence_store_spilled = sequence_store.spilled
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            sequence_store=sequence_store,
            export_path_stores=(
                [first_export_path_store]
                if first_export_path_store is not None
                else []
            ),
        )
        _raise_primary_with_cleanup(failure, cleanup_failures, "sequence candidate pass")

    _assert_control_inputs_current(config_snapshot, contract_snapshot)
    classified_boundary = ClassifiedOutputBoundary(classified_root).initialize()
    classified_boundary.ensure_directory(classified_root)
    _assert_no_reparse_components(classified_root, main_root, "main candidate root")
    _assert_control_inputs_current(config_snapshot, contract_snapshot)

    initialized_writers: list[Any] = []
    try:
        resource_writer = AtomicCsvWriter(artifact_paths["resource_index"], list(resource_spec["fields"]))
        initialized_writers.append(resource_writer)
        sequence_writer = AtomicCsvWriter(artifact_paths["sequence_index"], list(sequence_spec["fields"]))
        initialized_writers.append(sequence_writer)
        main_writer = AtomicCsvWriter(artifact_paths["main_candidate_index"], list(main_spec["fields"]))
        initialized_writers.append(main_writer)
        errors_writer = AtomicJsonlWriter(artifact_paths["errors"])
        initialized_writers.append(errors_writer)
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            writers=initialized_writers,
            sequence_store=sequence_store,
        )
        _raise_primary_with_cleanup(failure, cleanup_failures, "artifact writer initialization")
    writers = (resource_writer, sequence_writer, main_writer, errors_writer)

    transfer_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    filtered_category_counts: Counter[str] = Counter()
    skipped_counts: Counter[str] = Counter()
    organized_count = 0
    sequence_frame_count = 0
    main_candidate_count = 0
    filtered_category_count = 0
    failed_count = 0
    forbidden_output_count = 0
    hash_cache = HashCache(rules.hash_cache_entries)
    try:
        second_export_path_store = CanonicalExportPathStore(index_root, "second-pass")
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            writers=writers,
            sequence_store=sequence_store,
        )
        _raise_primary_with_cleanup(
            failure,
            cleanup_failures,
            "second-pass export path store initialization",
        )

    try:
        second_pass = _IndexPassCapture(_iter_rows(source_index, required_fields))
        for row in second_pass:
            parsed = parse_sequence_name(row.get("asset_name", ""), rules) if row.get("asset_type") in rules.image_types else None
            declared_export_paths = split_export_paths(row.get("export_path", ""))
            if not declared_export_paths:
                skipped_counts["no_export_path"] += 1
                continue
            export_paths = [
                export_path
                for export_path in declared_export_paths
                if second_export_path_store.accept(export_path)
            ]
            if not export_paths:
                skipped_counts["duplicate_export_path"] += len(declared_export_paths)
                continue
            for export_path in export_paths:
                row_for_path = dict(row)
                row_for_path["export_path"] = export_path
                context = _path_context(row_for_path, export_path)
                sequence_key = _sequence_key(parsed, row_for_path, rules) if parsed else None
                sequence_path_is_deliverable = bool(
                    sequence_key
                    and _is_deliverable_sequence_path(
                        row_for_path,
                        export_path,
                        source_boundary,
                        rules,
                        include_roots,
                        exclude_roots,
                    )
                )
                is_sequence = bool(
                    sequence_key
                    and sequence_path_is_deliverable
                    and sequence_store.is_confirmed(sequence_key)
                )
                if not should_copy_to_classified(row_for_path, export_path, rules):
                    skipped_counts["metadata" if is_metadata_export(row_for_path, export_path, rules) else "non_deliverable"] += 1
                    continue
                if is_sequence and parsed and sequence_key:
                    category, subject, action, sequence_scope = sequence_key
                    group = subject
                    source_frame = parsed[2]
                    ordinal = sequence_store.ordinal(sequence_key, source_frame)
                    suffix = PurePosixPath(export_path).suffix.lower()
                    filename = f"{action}_{ordinal:02d}{suffix}"
                    action_directory = (
                        f"{action}__scope_{_sequence_scope_id(sequence_scope)}"
                        if sequence_store.has_multiple_scopes(sequence_key)
                        else action
                    )
                    desired = classified_root.joinpath(
                        *PurePosixPath(category).parts,
                        subject,
                        action_directory,
                        filename,
                    )
                    sequence_action = action
                    sequence_frame = str(ordinal)
                else:
                    category, group = classify_asset(
                        row_for_path.get("asset_name", ""),
                        row_for_path.get("asset_type", ""),
                        rules,
                        context,
                    )
                    suffix = PurePosixPath(export_path).suffix.lower()
                    filename = safe_name(PurePosixPath(export_path).name, row.get("asset_name", "audio")) if row.get("asset_type") in rules.audio_types else f"{safe_name(row.get('asset_name', ''), row.get('asset_type', 'asset'))}{suffix}"
                    desired = classified_root.joinpath(*PurePosixPath(category).parts, safe_name(group), filename)
                    sequence_action = ""
                    sequence_frame = ""
                    sequence_scope = ""
                if not category_is_allowed(category, include_roots, exclude_roots):
                    filtered_category_count += 1
                    filtered_category_counts[category] += 1
                    continue
                if desired.suffix.lower() in {str(item).lower() for item in contract.get("classified_forbidden_extensions", [])}:
                    forbidden_output_count += 1
                    failed_count += 1
                    errors_writer.write({"code": "forbidden_classified_extension", "export_path": export_path, "destination": desired.relative_to(study_root).as_posix()})
                    continue

                identity_sha256 = _stable_identity(row, export_path, "classified")
                source_hash = ""
                source_size = ""
                output_relative = desired.relative_to(study_root).as_posix()
                operation_status = "failed"
                transfer_method = ""
                collision_status = "unresolved"
                result: MaterializeResult | None = None
                try:
                    with source_boundary.stable_file(export_path) as source:
                        source_size = str(_source_lease_stat(source).st_size)
                        source_hash = hash_cache.get(source)
                        result = _materialize(
                            source,
                            desired,
                            source_hash,
                            identity_sha256,
                            classified_root,
                            rules,
                            classified_boundary,
                        )
                    _record_transfer(transfer_counts, result)
                    operation_status = result.operation_status
                    transfer_method = result.transfer_method
                    collision_status = result.collision_status
                    output_relative = result.destination.relative_to(study_root).as_posix()
                    organized_count += 1
                    category_counts[category] += 1
                except DestinationConflictError as exc:
                    failed_count += 1
                    transfer_counts["failed"] += 1
                    transfer_counts["conflict"] += 1
                    errors_writer.write({
                        "code": "destination_conflict_unresolved",
                        "asset_id": row.get("asset_id", ""),
                        "source_file": row.get("source_file", ""),
                        "export_path": export_path,
                        "destination": output_relative,
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                except Exception as exc:
                    failed_count += 1
                    transfer_counts["failed"] += 1
                    errors_writer.write({
                        "code": "resource_materialization_failed",
                        "asset_id": row.get("asset_id", ""),
                        "source_file": row.get("source_file", ""),
                        "export_path": export_path,
                        "destination": output_relative,
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                resource_writer.write({
                    "original_asset_name": row.get("asset_name", ""),
                    "asset_type": row.get("asset_type", ""),
                    "category": category,
                    "group": group,
                    "sequence_action": sequence_action,
                    "sequence_frame": sequence_frame,
                    "source_export_path": export_path,
                    "source_file": row.get("source_file", ""),
                    "source_size": source_size,
                    "source_sha256": source_hash,
                    "width": row.get("width", ""),
                    "height": row.get("height", ""),
                    "output_relative_path": output_relative,
                    "operation_status": operation_status,
                    "transfer_method": transfer_method,
                    "collision_status": collision_status,
                    "identity_sha256": identity_sha256,
                })
                if result is None:
                    continue
                if is_sequence and parsed and sequence_key:
                    sequence_frame_count += 1
                    sequence_writer.write({
                        "sequence_group": (
                            f"{sequence_key[0]}/{sequence_key[1]}/{sequence_key[2]}"
                            f"@scope_{_sequence_scope_id(sequence_key[3])}"
                        ),
                        "sequence_scope": sequence_key[3],
                        "action": sequence_key[2],
                        "source_frame_number": parsed[2],
                        "normalized_frame_number": sequence_store.ordinal(sequence_key, parsed[2]),
                        "original_asset_name": row.get("asset_name", ""),
                        "output_relative_path": output_relative,
                        "source_export_path": export_path,
                        "source_sha256": source_hash,
                        "width": row.get("width", ""),
                        "height": row.get("height", ""),
                    })
                if row.get("asset_type") in rules.image_types and is_main_character_candidate(row.get("asset_name", ""), rules, context):
                    main_candidate_category = main_relative.as_posix()
                    if not category_is_allowed(main_candidate_category, include_roots, exclude_roots):
                        filtered_category_count += 1
                        filtered_category_counts[main_candidate_category] += 1
                        continue
                    main_identity = _stable_identity(row, export_path, "main_candidate")
                    main_desired = main_root / safe_name(category.split("/")[-1], "category") / safe_name(group) / result.destination.name
                    main_operation = "failed"
                    main_method = ""
                    main_collision = "unresolved"
                    main_output_relative = main_desired.relative_to(study_root).as_posix()
                    try:
                        with source_boundary.stable_file(export_path) as source:
                            source.cached_sha256 = source_hash
                            main_result = _materialize(
                                source,
                                main_desired,
                                source_hash,
                                main_identity,
                                classified_root,
                                rules,
                                classified_boundary,
                            )
                        _record_transfer(transfer_counts, main_result)
                        main_operation = main_result.operation_status
                        main_method = main_result.transfer_method
                        main_collision = main_result.collision_status
                        main_output_relative = main_result.destination.relative_to(study_root).as_posix()
                        main_candidate_count += 1
                    except DestinationConflictError as exc:
                        failed_count += 1
                        transfer_counts["failed"] += 1
                        transfer_counts["conflict"] += 1
                        errors_writer.write({
                            "code": "main_candidate_destination_conflict_unresolved",
                            "asset_id": row.get("asset_id", ""),
                            "export_path": export_path,
                            "destination": main_output_relative,
                            "message": f"{type(exc).__name__}: {exc}",
                        })
                    except Exception as exc:
                        failed_count += 1
                        transfer_counts["failed"] += 1
                        errors_writer.write({
                            "code": "main_candidate_materialization_failed",
                            "asset_id": row.get("asset_id", ""),
                            "export_path": export_path,
                            "destination": main_output_relative,
                            "message": f"{type(exc).__name__}: {exc}",
                        })
                    main_writer.write({
                        "original_asset_name": row.get("asset_name", ""),
                        "asset_type": row.get("asset_type", ""),
                        "category": category,
                        "group": group,
                        "source_output_relative_path": output_relative,
                        "main_output_relative_path": main_output_relative,
                        "source_sha256": source_hash,
                        "operation_status": main_operation,
                        "transfer_method": main_method,
                        "collision_status": main_collision,
                    })

        second_pass_evidence = second_pass.evidence
        second_export_path_duplicate_count = second_export_path_store.duplicate_count
        second_export_path_store.close()
        second_export_path_store = None
        try:
            source_index_after_snapshot = _capture_file_digest_snapshot(source_index, "source index after second pass")
            source_index_after_sha256 = source_index_after_snapshot.sha256
            final_snapshot_matches = source_index_after_snapshot == source_index_snapshot
        except InputChangedError:
            source_index_after_snapshot = None
            source_index_after_sha256 = "unavailable_due_to_drift"
            final_snapshot_matches = False
        source_index_unchanged = bool(
            _index_pass_matches_snapshot(first_pass_evidence, source_index_snapshot)
            and _index_pass_matches_snapshot(second_pass_evidence, source_index_snapshot)
            and first_pass_evidence is not None
            and second_pass_evidence is not None
            and first_pass_evidence.sha256 == second_pass_evidence.sha256
            and final_snapshot_matches
            and first_export_path_duplicate_count
            == second_export_path_duplicate_count
        )
        if not source_index_unchanged:
            failed_count += 1
            errors_writer.write({
                "code": "source_index_generation_mismatch",
                "before_sha256": source_index_sha256,
                "first_pass_sha256": first_pass_evidence.sha256 if first_pass_evidence else "unavailable",
                "first_pass_stable": first_pass_evidence.stable if first_pass_evidence else False,
                "second_pass_sha256": second_pass_evidence.sha256 if second_pass_evidence else "unavailable",
                "second_pass_stable": second_pass_evidence.stable if second_pass_evidence else False,
                "after_sha256": source_index_after_sha256,
            })

        resource_validation = resource_writer.prepare()
        sequence_validation = sequence_writer.prepare()
        main_validation = main_writer.prepare()
        errors_validation = errors_writer.prepare()
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            writers=writers,
            sequence_store=sequence_store,
            export_path_stores=(
                [second_export_path_store]
                if second_export_path_store is not None
                else []
            ),
        )
        _raise_primary_with_cleanup(failure, cleanup_failures, "resource organization")

    try:
        sequence_store.close()
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            writers=writers,
            sequence_store=sequence_store,
        )
        _raise_primary_with_cleanup(failure, cleanup_failures, "sequence candidate close")

    pending_texts: list[PendingTextArtifact] = []
    try:
        _assert_control_inputs_current(config_snapshot, contract_snapshot)
        project_display = " ".join(project_name.split()) or "未命名项目"
        model_note_text = "\n".join((
            "# 模型工具需求",
            "",
            f"- 观察到的事实：UnityPy 索引中存在 {model_related_object_count} 个模型/渲染器相关对象。",
            f"- 我的判断：{project_display} 的 UnityPy 资源组织步骤只整理已导出的图片和真实音频；模型对象应继续交给 UnityPy Mesh glTF 导出与模型预览链。",
            "- 待验证：检查 Mesh 几何、Renderer → Material → Texture 绑定、动画与 Blender 导入结果。",
            "",
        ))
        model_note_pending = _prepare_atomic_text(model_note, model_note_text, _validate_text)
        pending_texts.append(model_note_pending)
        model_note_validation = model_note_pending.validation()
        skipped_metadata_count = skipped_counts["metadata"]
        skipped_non_deliverable_count = skipped_counts["non_deliverable"]
        skipped_count = sum(skipped_counts.values())
        output_validation = {
            key: {field: value for field, value in validation.items() if field != "changed"}
            for key, validation in {
                "resource_index": resource_validation,
                "sequence_index": sequence_validation,
                "main_candidate_index": main_validation,
                "errors": errors_validation,
                "requirements_note": model_note_validation,
            }.items()
        }
        summary = {
            "schema_version": 1,
            "ok": source_index_unchanged and failed_count == 0 and forbidden_output_count == 0 and all(value.get("ok") for value in output_validation.values()),
            "transaction_state": "committed",
            "cleanup_state": "clean",
            "cleanup_warnings": [],
            "project_name": project_name,
            "source_index": str(source_index),
            "source_index_sha256": source_index_sha256,
            "source_index_unchanged": source_index_unchanged,
            "source_index_passes": {
                "initial_identity": list(source_index_snapshot.stat_identity),
                "first": {
                    "sha256": first_pass_evidence.sha256 if first_pass_evidence else "unavailable",
                    "stable_identity": first_pass_evidence.stable if first_pass_evidence else False,
                },
                "second": {
                    "sha256": second_pass_evidence.sha256 if second_pass_evidence else "unavailable",
                    "stable_identity": second_pass_evidence.stable if second_pass_evidence else False,
                },
                "final_sha256": source_index_after_sha256,
                "all_generations_match": source_index_unchanged,
            },
            "config_path": str(config_file),
            "config_sha256": config_sha256,
            "contract_path": str(contract_file),
            "contract_sha256": contract_sha256,
            "classified_resource_root": str(classified_root),
            "index_root": str(index_root),
            "source_row_count": source_row_count,
            "export_path_count": export_path_count,
            "duplicate_export_path_count": first_export_path_duplicate_count,
            "export_path_policy": {
                "canonical_format": rules.export_path_canonical_format,
                "duplicate_semantics": rules.export_path_duplicate_semantics,
                "exact_duplicate": rules.export_path_exact_duplicate,
                "alias_spelling": rules.export_path_alias_spelling,
                "passes_match": first_export_path_duplicate_count
                == second_export_path_duplicate_count,
            },
            "organized_count": organized_count,
            "sequence_frame_count": sequence_frame_count,
            "main_character_candidate_count": main_candidate_count,
            "model_related_object_count": model_related_object_count,
            "created_count": transfer_counts["created"],
            "reused_count": transfer_counts["reused"],
            "hardlinked_count": transfer_counts["hardlinked"],
            "copied_count": transfer_counts["copied"],
            "conflict_count": transfer_counts["conflict"],
            "resolved_conflict_count": transfer_counts["resolved_conflict"],
            "failed_count": failed_count,
            "filtered_category_count": filtered_category_count,
            "filtered_category_counts": dict(sorted(filtered_category_counts.items())),
            "skipped_count": skipped_count,
            "skipped_metadata_count": skipped_metadata_count,
            "skipped_non_deliverable_count": skipped_non_deliverable_count,
            "skipped_counts": dict(sorted(skipped_counts.items())),
            "forbidden_output_count": forbidden_output_count,
            "category_counts": dict(sorted(category_counts.items())),
            "category_filter": {"include_roots": sorted(include_roots), "exclude_roots": sorted(exclude_roots)},
            "sequence_detection": {
                "candidate_group_count": sequence_candidate_group_count,
                "confirmed_group_count": confirmed_sequence_group_count,
                "explicit_context_group_count": explicit_sequence_group_count,
                "minimum_distinct_frames": rules.minimum_distinct_frames,
                "group_scope_mode": rules.sequence_scope_mode,
                "group_scope_fields": list(rules.sequence_scope_fields),
                "explicit_context_cross_scope": rules.sequence_explicit_context_cross_scope,
                "candidate_spill_threshold": rules.sequence_spill_threshold,
                "candidate_store_spilled_to_sqlite": sequence_store_spilled,
            },
            "output_validation": output_validation,
            "reconciled_cleanup_orphan_count": len(reconciled_cleanup_orphans),
            "notes": [
                "Project/domain keywords and sequence actions are loaded from the reference config.",
                "Normal resource names are preserved; confirmed sequence frames use stable action ordinals derived from sorted source frame numbers.",
                "Transfer counters include both primary classified files and optional main-character candidate hardlinks/copies.",
                "A different-content base collision uses a stable source-identity suffix; a mismatch at that stable path fails closed.",
            ],
        }
        summary_spec = outputs["summary"]
        required_summary_keys = list(summary_spec["required_keys"])
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n"
        summary_pending = _prepare_atomic_text(
            artifact_paths["summary"],
            summary_text,
            lambda path: _validate_summary(path, required_summary_keys),
        )
        pending_texts.append(summary_pending)
        summary_pre_validation = summary_pending.validation()

        group_commit = _commit_artifact_group(
            [
                ArtifactGroupItem(
                    "resource_index",
                    resource_writer.path,
                    resource_writer.temporary,
                    str(resource_validation["sha256"]),
                    resource_writer.commit,
                    resource_writer.lease,
                ),
                ArtifactGroupItem(
                    "sequence_index",
                    sequence_writer.path,
                    sequence_writer.temporary,
                    str(sequence_validation["sha256"]),
                    sequence_writer.commit,
                    sequence_writer.lease,
                ),
                ArtifactGroupItem(
                    "main_candidate_index",
                    main_writer.path,
                    main_writer.temporary,
                    str(main_validation["sha256"]),
                    main_writer.commit,
                    main_writer.lease,
                ),
                ArtifactGroupItem(
                    "errors",
                    errors_writer.path,
                    errors_writer.temporary,
                    str(errors_validation["sha256"]),
                    errors_writer.commit,
                    errors_writer.lease,
                ),
                ArtifactGroupItem(
                    "requirements_note",
                    model_note_pending.path,
                    model_note_pending.temporary,
                    str(model_note_validation["sha256"]),
                    model_note_pending.commit,
                    model_note_pending.lease,
                ),
                ArtifactGroupItem(
                    "summary",
                    summary_pending.path,
                    summary_pending.temporary,
                    str(summary_pre_validation["sha256"]),
                    summary_pending.commit,
                    summary_pending.lease,
                ),
            ],
            config_snapshot,
            contract_snapshot,
            artifact_plan_states,
        )
        summary_validation = group_commit.results["summary"]
        if group_commit.cleanup_warnings:
            summary["cleanup_state"] = "warnings"
            summary["cleanup_warnings"] = list(group_commit.cleanup_warnings)
            try:
                summary_validation = _rewrite_committed_summary_cleanup_state(
                    artifact_paths["summary"],
                    summary,
                    required_summary_keys,
                    str(summary_validation["sha256"]),
                )
            except BaseException as cleanup_status_failure:
                cleanup_failures: list[Any] = [*group_commit.cleanup_warnings, {
                    "code": "cleanup_status_publish_failed",
                    "artifact_name": "summary",
                    "destination": str(artifact_paths["summary"]),
                    "orphan_path": "",
                    "message": f"{type(cleanup_status_failure).__name__}: {cleanup_status_failure}",
                }]
                current_summary = _capture_artifact_path_state(
                    artifact_paths["summary"],
                    "summary after cleanup-status publish failure",
                )
                # A different generation that appeared during the no-replace
                # publication belongs to another writer.  Preserve it at the
                # fixed path and fail closed instead of replacing it with our
                # recovery marker.
                if (
                    not current_summary.exists
                    or current_summary.sha256 == str(group_commit.results["summary"]["sha256"])
                ):
                    try:
                        _publish_rollback_failure_marker(
                            artifact_paths["summary"],
                            cleanup_failures,
                            [],
                            transaction_state="committed_cleanup_status_unknown",
                            cleanup_state="recovery_required",
                            expected_current=current_summary,
                        )
                    except BaseException as marker_failure:
                        cleanup_failures.append({
                            "code": "cleanup_failure_marker_publish_failed",
                            "artifact_name": "summary",
                            "destination": str(artifact_paths["summary"]),
                            "orphan_path": "",
                            "message": f"{type(marker_failure).__name__}: {marker_failure}",
                        })
                raise RuntimeError(
                    "committed artifacts could not publish an authoritative cleanup state: "
                    + json.dumps(cleanup_failures, ensure_ascii=False, sort_keys=True)
                ) from cleanup_status_failure
        success_handoff = (
            summary_validation.take_success_handoff()
            if isinstance(summary_validation, OrganizationResult)
            else None
        )
        summary["summary_artifact"] = dict(summary_validation)
        release_failures = _cleanup_organize_objects(
            writers=writers,
            pending_texts=pending_texts,
        )
        if release_failures:
            if success_handoff is not None:
                try:
                    success_handoff.complete()
                except BaseException as exc:
                    release_failures.append(
                        f"complete cleanup-summary handoff: {type(exc).__name__}: {exc}"
                    )
            raise RuntimeError(
                "artifact success lease release failed: " + "; ".join(release_failures)
            )
        return OrganizationResult(summary, success_handoff=success_handoff)
    except BaseException as failure:
        cleanup_failures = _cleanup_organize_objects(
            writers=writers,
            pending_texts=pending_texts,
        )
        _raise_primary_with_cleanup(failure, cleanup_failures, "artifact finalization")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path, help="UnityPy all_resources_index.csv under --unitypy-root")
    parser.add_argument("--unitypy-root", required=True, type=Path, help="Read-only UnityPy export root")
    parser.add_argument("--study-root", required=True, type=Path, help="Project study root with 0-4 deliverable folders")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--requirements-output", type=Path, help="Optional path under study-root/4.临时目录")
    parser.add_argument("--include-category-root", action="append", help="Repeatable classified-resource root allowlist")
    parser.add_argument("--exclude-category-root", action="append", help="Repeatable classified-resource root denylist")
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    try:
        summary = organize(
            index_path=args.index,
            unitypy_root=args.unitypy_root,
            study_root=args.study_root,
            project_name=args.project_name,
            requirements_output=args.requirements_output,
            include_category_roots=args.include_category_root,
            exclude_category_roots=args.exclude_category_root,
            config_path=args.config,
            contract_path=args.contract,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
        exit_code = 0 if summary["ok"] else 1
        summary.close()
        return exit_code
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

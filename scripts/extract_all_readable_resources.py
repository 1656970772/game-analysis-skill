from __future__ import annotations

import argparse
import csv
import errno
import gc
import hashlib
import json
import lzma
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

try:
    import UnityPy
except ImportError:  # pragma: no cover - environment-specific
    UnityPy = None


UNITY_CONTAINER_SUFFIXES = {
    ".assets",
    ".bundle",
    ".unity3d",
    ".sharedassets",
}
UNITY_CONTAINER_NAMES = {
    "globalgamemanagers",
    "maindata",
    "resources.assets",
    "unity default resources",
    "unity_builtin_extra",
}
UNITY_BUNDLE_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")
UNITY_EXTERNAL_PAYLOAD_SUFFIXES = {".ress", ".resource"}
UNITY_LEVEL_NAME = re.compile(r"^level\d+$", flags=re.IGNORECASE)
ADDRESSABLE_HINTS = ("StreamingAssets", "aa")

INDEX_FIELDS = [
    "asset_id",
    "source_file",
    "path_id",
    "asset_type",
    "asset_name",
    "container_path",
    "export_kind",
    "export_path",
    "width",
    "height",
    "status",
    "remarks",
]
LOG_FIELDS = ["source", "stage", "status", "message"]
LEGACY_RESUME_PROTOCOL_VERSION = 1
RESUME_PROTOCOL_VERSION = 2
SUPPORTED_RESUME_PROTOCOL_VERSIONS = {
    LEGACY_RESUME_PROTOCOL_VERSION,
    RESUME_PROTOCOL_VERSION,
}
LEGACY_ASSET_IDENTITY_SCHEME = "legacy_safe_name_v1"
ASSET_IDENTITY_SCHEME = "readable_prefix_full_identity_sha256_v2"
ASSET_IDENTITY_VERSION = 2
RESUME_STATE_DIR_NAME = ".unitypy_resume"
PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA = "protocol_one_group_transaction_v2"
PROTOCOL_ONE_TRANSACTION_SCHEMA = "protocol_one_group_transaction_v3"
PROTOCOL_ONE_TRANSACTION_STATE = "prepared_commit_record"
PROTOCOL_ONE_HISTORY_SCHEMA = "protocol_one_group_transaction_history_v1"
PROTOCOL_ONE_HISTORY_GENESIS_SHA256 = hashlib.sha256(
    b"game-analysis/protocol-one-transaction-history/genesis/v1\n"
).hexdigest()
LARGE_BUNDLE_STRATEGY_VERSION = 1
LARGE_BUNDLE_DEFAULT_THRESHOLD_BYTES = 512 * 1024 * 1024
LARGE_BUNDLE_DEFAULT_NODE_THRESHOLD_BYTES = 512 * 1024 * 1024
LARGE_BUNDLE_DEFAULT_IO_CHUNK_BYTES = 4 * 1024 * 1024
LARGE_BUNDLE_DEFAULT_BATCH_SIZE = 10_000
LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES = 256 * 1024 * 1024
LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES = 8
WINDOWS_PROCESS_TREE_MEMORY_METHOD = (
    "windows_toolhelp32_process_tree_GetProcessMemoryInfo_polled_v1"
)
WINDOWS_PROCESS_TREE_PEAK_SEMANTICS = (
    "max_observed_single_process_in_launch_tree_not_sum"
)
LARGE_BUNDLE_MAX_BLOCK_INFO_BYTES = 64 * 1024 * 1024
LARGE_BUNDLE_MAX_LZMA_DICTIONARY_BYTES = 256 * 1024 * 1024
UNITYFS_COMPRESSION_MASK = 0x3F
UNITYFS_COMPRESSION_NONE = 0
UNITYFS_COMPRESSION_LZMA = 1
UNITYFS_COMPRESSION_LZ4 = 2
UNITYFS_COMPRESSION_LZ4HC = 3
UNITYFS_BLOCKS_INFO_AT_END = 0x80
UNITYFS_OLD_ENCRYPTION_FLAG = 0x200
UNITYFS_BLOCK_ENCRYPTION_FLAG = 0x100
UNITYFS_SERIALIZED_NODE_FLAG = 0x04


class ResumeStateError(RuntimeError):
    """Raised when an existing checkpoint cannot safely be reused."""


class LargeBundleError(RuntimeError):
    """Raised when the bounded-memory UnityFS route cannot proceed safely."""


class LargeBundleWorkerError(LargeBundleError):
    """Raised when a short-lived large-bundle export worker fails."""


class OutputBoundaryError(RuntimeError):
    """Raised before an exporter write can escape its authorized output root."""


class SourceBoundaryError(RuntimeError):
    """Raised before exporter discovery/read can escape its authorized source root."""


class GroupTransactionError(OutputBoundaryError):
    """Expose a fail-closed grouped-write recovery state to callers and validators."""

    def __init__(
        self,
        message: str,
        *,
        transaction_id: str,
        transaction_state: str,
        cleanup_warnings: list[str] | None = None,
        cleanup_required_paths: list[str] | None = None,
        recovery_files: list[str] | None = None,
    ) -> None:
        self.transaction_id = transaction_id
        self.transaction_state = transaction_state
        self.cleanup_warnings = list(cleanup_warnings or [])
        self.cleanup_required_paths = sorted(set(cleanup_required_paths or []))
        self.recovery_files = sorted(set(recovery_files or []))
        details = [f"transaction_state={transaction_state}"]
        if self.cleanup_required_paths:
            details.append(
                "cleanup_required_paths=" + ",".join(self.cleanup_required_paths)
            )
        if self.recovery_files:
            details.append("recovery_files=" + ",".join(self.recovery_files))
        super().__init__(f"{message} ({'; '.join(details)})")

    def evidence(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "transaction_state": self.transaction_state,
            "cleanup_state": (
                "warnings" if self.cleanup_warnings or self.cleanup_required_paths else "clean"
            ),
            "cleanup_warnings": self.cleanup_warnings,
            "cleanup_required_paths": self.cleanup_required_paths,
            "recovery_files": self.recovery_files,
        }


class GroupCommitResult(list[str]):
    """List-compatible cleanup warnings plus machine-readable commit evidence."""

    def __init__(
        self,
        cleanup_warnings: list[str],
        *,
        transaction_id: str,
        cleanup_required_paths: list[str],
        target_generations: list[dict[str, Any]],
        target_leases: list[StableReadLease] | None = None,
    ) -> None:
        super().__init__(cleanup_warnings)
        self.transaction_id = transaction_id
        self.transaction_state = "committed"
        self.cleanup_state = "warnings" if cleanup_warnings else "clean"
        self.cleanup_required_paths = sorted(set(cleanup_required_paths))
        self.target_generations = target_generations
        self._target_leases = list(target_leases or [])
        self._closed = False

    def close(self) -> None:
        """Release the exact committed-target handoff leases."""
        if self._closed:
            return
        self._closed = True
        for lease in reversed(self._target_leases):
            lease.close()
        self._target_leases.clear()

    def verify_handoff(self) -> None:
        """Prove every committed target still names its leased generation."""
        if self._closed:
            raise ResumeStateError("Grouped commit target handoff has already been closed.")
        for lease in self._target_leases:
            verify_stable_read_lease(lease)

    def __enter__(self) -> "GroupCommitResult":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive caller cleanup
        self.close()

    def evidence(self) -> dict[str, Any]:
        self.verify_handoff()
        return {
            "transaction_id": self.transaction_id,
            "transaction_state": self.transaction_state,
            "cleanup_state": self.cleanup_state,
            "cleanup_warnings": list(self),
            "cleanup_required_paths": self.cleanup_required_paths,
            "recovery_files": self.cleanup_required_paths,
            "target_generations": self.target_generations,
        }


class ProtocolOneRecoveryResult(dict[str, Any]):
    """Recovery evidence whose target leases remain live until caller handoff."""

    def __init__(self, evidence: dict[str, Any], target_leases: list[StableReadLease]) -> None:
        super().__init__(evidence)
        self._target_leases = target_leases
        self._closed = False

    def verify_handoff(self) -> None:
        if self._closed:
            raise ResumeStateError("Protocol-1 recovery handoff has already been closed.")
        for lease in self._target_leases:
            verify_stable_read_lease(lease)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for lease in reversed(self._target_leases):
            lease.close()
        self._target_leases.clear()

    def __enter__(self) -> "ProtocolOneRecoveryResult":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - defensive caller cleanup
        self.close()


class ProtocolOneTransactionHistory(list[Path]):
    """Ordered immutable protocol-1 sidecars plus their authoritative head."""

    def __init__(
        self,
        paths: list[Path],
        *,
        anchor: dict[str, Any] | None,
        head_sidecar_sha256: str | None,
        records: dict[str, dict[str, Any]],
    ) -> None:
        super().__init__(paths)
        self.anchor = dict(anchor) if anchor is not None else None
        self.head_sidecar_sha256 = head_sidecar_sha256
        self.records = dict(records)


class StableDirectoryLease:
    """Keep one verified output parent stable for a complete mutation.

    Windows leases retain directory handles opened without FILE_SHARE_DELETE, so
    the directory and every in-root ancestor cannot be renamed, removed, or
    replaced by a junction while a path-based writer is using it. POSIX leases
    retain an O_DIRECTORY/O_NOFOLLOW descriptor used by replace/remove.
    """

    def __init__(
        self,
        path: Path,
        *,
        directory_fd: int | None = None,
        windows_directory_handle: int | None = None,
    ) -> None:
        self.path = path
        self.directory_fd = directory_fd
        self.windows_directory_handle = windows_directory_handle


@dataclass(frozen=True)
class ExpectedFileGeneration:
    """One observable regular-file generation used by conditional group commits."""

    exists: bool
    identity: tuple[int, ...] | None = None
    byte_size: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.exists:
            if self.identity is None or self.byte_size is None or self.sha256 is None:
                raise ValueError("Existing file generations require identity, size, and SHA-256.")
            if self.byte_size < 0 or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
                raise ValueError("Existing file generation metadata is invalid.")
            return
        if self.identity is not None or self.byte_size is not None or self.sha256 is not None:
            raise ValueError("Absent file generations cannot carry file metadata.")


class ConditionalExportFilePlans(list[tuple[Path, bytes]]):
    """List-compatible group plan carrying audited destination generations."""

    def __init__(
        self,
        plans: list[tuple[Path, bytes]],
        expected_generations: Mapping[Path, ExpectedFileGeneration],
        transaction_token: str | None = None,
    ) -> None:
        super().__init__(plans)
        self.expected_generations = dict(expected_generations)
        self.transaction_token = transaction_token


class StableReadLease:
    """Keep one regular file identity unavailable for replacement while audited."""

    def __init__(
        self,
        path: Path,
        identity: tuple[int, ...],
        *,
        file_descriptor: int | None = None,
        windows_handle: int | None = None,
        windows_kernel32: Any = None,
    ) -> None:
        self.path = path
        self.identity = identity
        self.file_descriptor = file_descriptor
        self.windows_handle = windows_handle
        self.windows_kernel32 = windows_kernel32
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.windows_handle is not None:
            self.windows_kernel32.CloseHandle(self.windows_handle)
            self.windows_handle = None
        if self.file_descriptor is not None:
            os.close(self.file_descriptor)
            self.file_descriptor = None

    def __del__(self) -> None:  # pragma: no cover - defensive exception cleanup
        self.close()


class LegacyNoWorkPreflight(dict[str, Any]):
    """Public evidence plus live file leases for one protocol-1 no-work run."""

    def __init__(
        self,
        evidence: dict[str, Any],
        checkpoint: dict[str, Any],
        checkpoint_lease: StableReadLease,
        fragment_leases: list[StableReadLease],
        snapshot_binding: dict[str, Any],
        checkpoint_generation: ExpectedFileGeneration,
    ) -> None:
        super().__init__(evidence)
        self.checkpoint = checkpoint
        self.checkpoint_lease = checkpoint_lease
        self.fragment_leases = fragment_leases
        self.snapshot_binding = snapshot_binding
        self.checkpoint_generation = checkpoint_generation

    def release_checkpoint(self) -> None:
        self.checkpoint_lease.close()

    def close(self) -> None:
        self.checkpoint_lease.close()
        for lease in reversed(self.fragment_leases):
            lease.close()

    def __del__(self) -> None:  # pragma: no cover - defensive exception cleanup
        self.close()


def lexical_absolute(path: Path) -> Path:
    """Return an absolute path without resolving symlink/reparse components."""
    return Path(os.path.abspath(os.fspath(path)))


def paths_overlap(left: Path, right: Path) -> bool:
    """Return whether two absolute paths are equal or one contains the other."""
    left_text = os.path.normcase(os.path.normpath(os.fspath(left)))
    right_text = os.path.normcase(os.path.normpath(os.fspath(right)))
    try:
        common = os.path.commonpath([left_text, right_text])
    except ValueError:
        return False
    return common in {left_text, right_text}


def is_reparse_path(path: Path) -> bool:
    """Detect symlinks and Windows reparse points without following them."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


class AuthorizedSourceSnapshot:
    """Hold stable leases for every discovered source file until the run ends."""

    def __init__(
        self,
        boundary: "SourceBoundary",
        files: list[Path],
        leases: dict[str, StableReadLease],
    ) -> None:
        self.boundary = boundary
        self.files = list(files)
        self.leases = leases
        self._closed = False

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(os.path.normpath(os.fspath(lexical_absolute(path))))

    def verify(self, path: Path) -> None:
        authorized = self.boundary.require_authorized_file(path)
        lease = self.leases.get(self._key(authorized))
        if lease is None:
            raise SourceBoundaryError(
                f"Source file was not part of the stable authorized snapshot: {authorized}."
            )
        try:
            verify_stable_read_lease(lease)
        except ResumeStateError as exc:
            raise SourceBoundaryError(str(exc)) from exc

    def verify_all(self) -> None:
        for path in self.files:
            self.verify(path)
        self.boundary.verify_root_identity()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for lease in reversed(list(self.leases.values())):
            lease.close()

    def __enter__(self) -> "AuthorizedSourceSnapshot":
        return self

    def __exit__(self, *_error: Any) -> None:
        self.close()


class SourceBoundary:
    """Fail closed on source-root escape, reparse traversal, and file drift.

    Discovery walks one lexical tree without following directory links. Every
    component from the authorized root to a candidate must be non-reparse, and
    the candidate's canonical path must remain under the canonical root. A run
    then leases every discovered regular file; Windows leases deny write/delete
    replacement, while POSIX retains the same descriptor/advisory-lock boundary
    documented for protocol-1 inputs.
    """

    def __init__(self, source_root: Path) -> None:
        self.root = lexical_absolute(source_root)
        self.canonical_root: Path | None = None
        self.root_identity: tuple[int, int] | None = None

    @staticmethod
    def _component_chain(path: Path) -> list[Path]:
        chain: list[Path] = []
        current = lexical_absolute(path)
        while True:
            chain.append(current)
            if current.parent == current:
                break
            current = current.parent
        chain.reverse()
        return chain

    def _reject_reparse_components(self, path: Path) -> None:
        for component in self._component_chain(path):
            if os.path.lexists(component) and is_reparse_path(component):
                raise SourceBoundaryError(
                    "Source path contains a symlink/junction/reparse component: "
                    f"{component}."
                )

    def initialize(self) -> "SourceBoundary":
        self._reject_reparse_components(self.root)
        if not self.root.is_dir():
            raise SourceBoundaryError(
                f"Authorized source root is not an existing directory: {self.root}."
            )
        self.canonical_root = self.root.resolve(strict=True)
        metadata = self.root.stat()
        self.root_identity = (int(metadata.st_dev), int(metadata.st_ino))
        self.verify_root_identity()
        return self

    def verify_root_identity(self) -> None:
        if self.canonical_root is None or self.root_identity is None:
            raise SourceBoundaryError("Source boundary has not been initialized.")
        self._reject_reparse_components(self.root)
        try:
            metadata = self.root.stat()
            canonical = self.root.resolve(strict=True)
        except OSError as exc:
            raise SourceBoundaryError(
                f"Authorized source root is no longer stable: {self.root}."
            ) from exc
        if (
            (int(metadata.st_dev), int(metadata.st_ino)) != self.root_identity
            or canonical != self.canonical_root
        ):
            raise SourceBoundaryError(
                f"Authorized source root identity changed during the run: {self.root}."
            )

    def require_authorized_file(self, path: Path) -> Path:
        self.verify_root_identity()
        candidate = lexical_absolute(path)
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise SourceBoundaryError(
                f"Source candidate escapes the authorized lexical root: {candidate}."
            ) from exc
        current = self.root
        for part in relative.parts:
            current = current / part
            if not os.path.lexists(current):
                raise SourceBoundaryError(
                    f"Source candidate disappeared during authorization: {current}."
                )
            if is_reparse_path(current):
                raise SourceBoundaryError(
                    "Source candidate crosses a symlink/junction/reparse component: "
                    f"{current}."
                )
        assert self.canonical_root is not None
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SourceBoundaryError(
                f"Source candidate cannot be resolved safely: {candidate}."
            ) from exc
        if not resolved.is_relative_to(self.canonical_root):
            raise SourceBoundaryError(
                f"Source candidate resolves outside the authorized root: {candidate} -> {resolved}."
            )
        if not candidate.is_file():
            raise SourceBoundaryError(
                f"Source candidate is not a regular file: {candidate}."
            )
        return candidate

    def discover_files(self) -> list[Path]:
        self.verify_root_identity()
        files: list[Path] = []

        def walk_error(error: OSError) -> None:
            raise SourceBoundaryError(f"Cannot inspect authorized source tree: {error}")

        for current, directories, names in os.walk(
            self.root,
            topdown=True,
            followlinks=False,
            onerror=walk_error,
        ):
            current_path = lexical_absolute(Path(current))
            self._reject_reparse_components(current_path)
            assert self.canonical_root is not None
            resolved_current = current_path.resolve(strict=True)
            if not resolved_current.is_relative_to(self.canonical_root):
                raise SourceBoundaryError(
                    f"Source traversal escaped its canonical root: {current_path}."
                )
            directories.sort(key=str.casefold)
            names.sort(key=str.casefold)
            for name in directories:
                directory = current_path / name
                if is_reparse_path(directory):
                    raise SourceBoundaryError(
                        "Source tree contains a symlink/junction/reparse directory: "
                        f"{directory}."
                    )
                resolved = directory.resolve(strict=True)
                if not resolved.is_relative_to(self.canonical_root):
                    raise SourceBoundaryError(
                        f"Source directory resolves outside the authorized root: {directory}."
                    )
            for name in names:
                files.append(self.require_authorized_file(current_path / name))
        self.verify_root_identity()
        return sorted(
            files,
            key=lambda path: path.relative_to(self.root).as_posix().casefold(),
        )

    def acquire_snapshot(self, files: list[Path]) -> AuthorizedSourceSnapshot:
        leases: dict[str, StableReadLease] = {}
        try:
            for path in files:
                authorized = self.require_authorized_file(path)
                key = AuthorizedSourceSnapshot._key(authorized)
                if key in leases:
                    raise SourceBoundaryError(
                        f"Duplicate source path in authorized snapshot: {authorized}."
                    )
                try:
                    leases[key] = acquire_stable_read_lease(authorized)
                except ResumeStateError as exc:
                    raise SourceBoundaryError(str(exc)) from exc
            snapshot = AuthorizedSourceSnapshot(self, files, leases)
            snapshot.verify_all()
            return snapshot
        except BaseException:
            for lease in reversed(list(leases.values())):
                lease.close()
            raise


class OutputBoundary:
    """Fail-closed writer boundary for every exporter-managed output path.

    The lexical root is fixed before creation. Existing ancestors, the root, and
    the existing output tree are rejected if any component is a symlink, junction,
    or other reparse point. Every nested parent is then created one component at a
    time with before/after verification, and every file target is checked again
    immediately before use.
    """

    def __init__(self, export_root: Path, source_root: Path | None = None) -> None:
        self.root = lexical_absolute(export_root)
        self.source_root = lexical_absolute(source_root) if source_root is not None else None
        self.canonical_root: Path | None = None
        self._initialized = False
        self._reject_root_overlap()
        self._verify_existing_components(self.root)

    def _reject_root_overlap(self) -> None:
        if self.source_root is None:
            return
        if paths_overlap(self.source_root, self.root):
            raise OutputBoundaryError(
                "Source and export roots must be disjoint; equal or nested roots are forbidden."
            )
        canonical_source = self.source_root.resolve(strict=False)
        canonical_export = self.root.resolve(strict=False)
        if paths_overlap(canonical_source, canonical_export):
            raise OutputBoundaryError(
                "Source and export roots overlap after canonical resolution."
            )

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
        for component in self._component_chain(lexical_absolute(path)):
            if not os.path.lexists(component):
                continue
            if is_reparse_path(component):
                raise OutputBoundaryError(
                    f"Output path contains a symlink/junction/reparse component: {component}"
                )

    def _assert_lexically_inside(self, path: Path) -> Path:
        candidate = lexical_absolute(path)
        root_text = os.path.normcase(os.path.normpath(os.fspath(self.root)))
        candidate_text = os.path.normcase(os.path.normpath(os.fspath(candidate)))
        try:
            inside = os.path.commonpath([root_text, candidate_text]) == root_text
        except ValueError:
            inside = False
        if not inside:
            raise OutputBoundaryError(f"Output path escapes the authorized root: {candidate}")
        return candidate

    def _assert_canonically_inside(self, path: Path) -> None:
        if self.canonical_root is None:
            raise OutputBoundaryError("Output boundary has not been initialized.")
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.canonical_root):
            raise OutputBoundaryError(
                f"Output path resolves outside the authorized root: {path} -> {resolved}"
            )

    def _create_directory_chain(self, directory: Path) -> None:
        directory = self._assert_lexically_inside(directory)
        missing: list[Path] = []
        current = directory
        while not os.path.lexists(current):
            missing.append(current)
            current = current.parent
        self._verify_existing_components(current)
        if os.path.lexists(current) and not current.is_dir():
            raise OutputBoundaryError(f"Output parent component is not a directory: {current}")
        ordered_missing = list(reversed(missing))
        if not ordered_missing:
            return
        if os.name == "nt":
            self._create_windows_directory_chain(current, ordered_missing)
        else:
            self._create_posix_directory_chain(current, ordered_missing)
        for component in ordered_missing:
            self._verify_existing_components(component)
            if not component.is_dir():
                raise OutputBoundaryError(
                    f"Created output component is not a directory: {component}"
                )
            if self.canonical_root is not None:
                self._assert_canonically_inside(component)

    @staticmethod
    def _windows_create_child_directory(directory_handle: int, name: str) -> int:
        """Create exactly one directory relative to an already verified handle."""
        import ctypes
        from ctypes import wintypes

        if not name or name in {".", ".."} or "\\" in name or "/" in name:
            raise OutputBoundaryError(f"Unsafe output directory component: {name!r}.")

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
            _fields_ = [
                ("Status", ctypes.c_ssize_t),
                ("Information", ctypes.c_size_t),
            ]

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
            0x00000040,  # OBJ_CASE_INSENSITIVE
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
            0x00000010,  # FILE_ATTRIBUTE_DIRECTORY
            0x00000001 | 0x00000002,  # no delete sharing
            2,  # FILE_CREATE; concurrent aliases fail closed
            0x00000001 | 0x00000020 | 0x00200000,
            None,
            0,
        )
        if status != 0 or not child_handle.value:
            raise OutputBoundaryError(
                "Stable handle-relative directory creation failed for "
                f"{name!r}: ntstatus=0x{status & 0xFFFFFFFF:08x}."
            )
        return int(child_handle.value)

    def _create_windows_directory_chain(
        self,
        existing_parent: Path,
        missing: list[Path],
    ) -> None:
        handles, kernel32 = self._windows_directory_handles(
            self._component_chain(existing_parent)
        )
        anchors: list[int] = []
        try:
            anchors.append(self._windows_create_directory_anchor(handles[-1]))
            parent_handle = handles[-1]
            for component in missing:
                child_handle = self._windows_create_child_directory(
                    parent_handle,
                    component.name,
                )
                handles.append(child_handle)
                # Pin every newly-created identity before releasing its parent.
                anchors.append(self._windows_create_directory_anchor(child_handle))
                parent_handle = child_handle
                self._verify_existing_components(component)
        finally:
            for anchor in reversed(anchors):
                kernel32.CloseHandle(anchor)
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)

    def _create_posix_directory_chain(
        self,
        existing_parent: Path,
        missing: list[Path],
    ) -> None:
        open_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            open_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW
        descriptors: list[int] = []
        try:
            parent_fd = os.open(existing_parent, open_flags)
            descriptors.append(parent_fd)
            for component in missing:
                try:
                    os.mkdir(component.name, dir_fd=parent_fd)
                except FileExistsError as exc:
                    raise OutputBoundaryError(
                        "Concurrent output directory creation was rejected: "
                        f"{component}."
                    ) from exc
                child_fd = os.open(component.name, open_flags, dir_fd=parent_fd)
                descriptors.append(child_fd)
                parent_fd = child_fd
                descriptor_stat = os.fstat(child_fd)
                path_stat = os.stat(component, follow_symlinks=False)
                if (
                    descriptor_stat.st_dev,
                    descriptor_stat.st_ino,
                ) != (
                    path_stat.st_dev,
                    path_stat.st_ino,
                ):
                    raise OutputBoundaryError(
                        f"Created output directory identity changed: {component}."
                    )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _scan_existing_tree_no_reparse(self) -> None:
        stack = [self.root]
        while stack:
            directory = stack.pop()
            self._verify_existing_components(directory)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        child = Path(entry.path)
                        if is_reparse_path(child):
                            raise OutputBoundaryError(
                                "Existing output tree contains a "
                                f"symlink/junction/reparse entry: {child}"
                            )
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(child)
            except OSError as exc:
                raise OutputBoundaryError(
                    f"Cannot inspect existing output tree component {directory}: {exc}"
                ) from exc

    def initialize(self, *, scan_existing_tree: bool = True) -> "OutputBoundary":
        self._reject_root_overlap()
        self._verify_existing_components(self.root)
        if not os.path.lexists(self.root):
            self._create_directory_chain(self.root)
        if not self.root.is_dir():
            raise OutputBoundaryError(f"Export root is not a directory: {self.root}")
        self.canonical_root = self.root.resolve(strict=True)
        self._reject_root_overlap()
        self._initialized = True
        if scan_existing_tree:
            self._scan_existing_tree_no_reparse()
        return self

    def ensure_directory(self, path: Path) -> Path:
        if not self._initialized:
            self.initialize(scan_existing_tree=False)
        directory = self._assert_lexically_inside(path)
        self._verify_existing_components(directory)
        if not os.path.lexists(directory):
            self._create_directory_chain(directory)
        self._verify_existing_components(directory)
        if not directory.is_dir():
            raise OutputBoundaryError(f"Output path is not a directory: {directory}")
        self._assert_canonically_inside(directory)
        return directory

    def prepare_file(self, path: Path) -> Path:
        if not self._initialized:
            self.initialize(scan_existing_tree=False)
        target = self._assert_lexically_inside(path)
        self.ensure_directory(target.parent)
        self._verify_existing_components(target)
        if os.path.lexists(target) and target.is_dir():
            raise OutputBoundaryError(f"Output file target is an existing directory: {target}")
        self._assert_canonically_inside(target.parent)
        if os.path.lexists(target):
            self._assert_canonically_inside(target)
        return target

    @staticmethod
    def _windows_directory_handles(directory_chain: list[Path]) -> tuple[list[int], Any]:
        """Open normal directories without delete sharing and return stable handles."""
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

        file_read_attributes = 0x0080
        file_share_read = 0x00000001
        file_share_write = 0x00000002
        open_existing = 3
        file_flag_backup_semantics = 0x02000000
        file_flag_open_reparse_point = 0x00200000
        file_attribute_reparse_point = 0x00000400
        invalid_handle = ctypes.c_void_p(-1).value
        handles: list[int] = []
        try:
            for directory in directory_chain:
                handle = kernel32.CreateFileW(
                    str(directory),
                    file_read_attributes,
                    # Intentionally omit FILE_SHARE_DELETE. This is the Windows
                    # kernel-enforced part of the stable-parent contract.
                    file_share_read | file_share_write,
                    None,
                    open_existing,
                    file_flag_backup_semantics | file_flag_open_reparse_point,
                    None,
                )
                if handle in (None, 0, invalid_handle):
                    error = ctypes.get_last_error()
                    raise OutputBoundaryError(
                        "Cannot acquire a stable Windows output-directory handle "
                        f"for {directory}: winerror={error}."
                    )
                handles.append(int(handle))
                information = ByHandleFileInformation()
                if not kernel32.GetFileInformationByHandle(
                    handle,
                    ctypes.byref(information),
                ):
                    error = ctypes.get_last_error()
                    raise OutputBoundaryError(
                        "Cannot verify a stable Windows output-directory handle "
                        f"for {directory}: winerror={error}."
                    )
                if int(information.dwFileAttributes) & file_attribute_reparse_point:
                    raise OutputBoundaryError(
                        "Stable Windows output-directory handle resolved to a "
                        f"symlink/junction/reparse point: {directory}"
                    )
            return handles, kernel32
        except BaseException:
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)
            raise

    @staticmethod
    def _windows_create_directory_anchor(directory_handle: int) -> int:
        """Create a delete-on-close child by stable handle to pin a directory identity.

        Modern Windows can rename a directory even while a metadata-only directory
        handle omits ``FILE_SHARE_DELETE``.  An open child that itself omits delete
        sharing is the kernel-enforced lease: ancestor rename/delete attempts fail,
        and handle-relative creation means a path swap cannot redirect the lease
        file into an attacker-controlled directory.
        """
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
            _fields_ = [
                ("Status", ctypes.c_ssize_t),
                ("Information", ctypes.c_size_t),
            ]

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
        object_attributes = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes),
            directory_handle,
            ctypes.pointer(unicode_name),
            0x00000040,  # OBJ_CASE_INSENSITIVE
            None,
            None,
        )
        io_status = IoStatusBlock()
        anchor_handle = wintypes.HANDLE()
        status = ntdll.NtCreateFile(
            ctypes.byref(anchor_handle),
            0x00010000 | 0x00000080 | 0x00100000,  # DELETE, attributes, synchronize
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            0x00000002,  # FILE_ATTRIBUTE_HIDDEN
            0x00000001 | 0x00000002,  # share read/write; intentionally no delete
            2,  # FILE_CREATE: never open or overwrite a colliding entry
            0x00000040 | 0x00000020 | 0x00001000,  # file, sync, delete-on-close
            None,
            0,
        )
        if status != 0 or not anchor_handle.value:
            raise OutputBoundaryError(
                "Cannot acquire the Windows handle-relative output-directory "
                f"lease anchor: ntstatus=0x{status & 0xFFFFFFFF:08x}."
            )
        return int(anchor_handle.value)

    def _directory_chain_inside_root(self, directory: Path) -> list[Path]:
        directory = self._assert_lexically_inside(directory)
        try:
            relative_parts = directory.relative_to(self.root).parts
        except ValueError as exc:  # defensive; lexical containment already checked
            raise OutputBoundaryError(
                f"Output directory escapes the authorized root: {directory}"
            ) from exc
        chain = [self.root]
        current = self.root
        for part in relative_parts:
            current = current / part
            chain.append(current)
        return chain

    @contextmanager
    def stable_directory(self, directory: Path) -> Iterator[StableDirectoryLease]:
        """Hold a stable parent identity for every path-based mutation.

        Windows opens the full in-root directory chain, then creates an open
        delete-on-close child anchor relative to the verified final-directory
        handle. The anchor omits delete sharing and pins that directory plus its
        ancestors against rename/delete for the complete write/commit/cleanup
        window. On POSIX, the fallback retains an O_DIRECTORY/O_NOFOLLOW file
        descriptor and replace/remove use descriptor-relative APIs. The path-based
        writer callback remains a cooperative-writer boundary on non-Windows
        platforms; this limitation is explicit rather than being confused with
        the stronger Windows lease.
        """
        directory = self.ensure_directory(directory)
        chain = self._directory_chain_inside_root(directory)
        self._verify_existing_components(directory)
        self._assert_canonically_inside(directory)
        if os.name == "nt":
            handles, kernel32 = self._windows_directory_handles(chain)
            anchor_handle: int | None = None
            try:
                self._verify_existing_components(directory)
                self._assert_canonically_inside(directory)
                anchor_handle = self._windows_create_directory_anchor(handles[-1])
                self._verify_existing_components(directory)
                self._assert_canonically_inside(directory)
                yield StableDirectoryLease(
                    directory,
                    windows_directory_handle=handles[-1],
                )
                self._verify_existing_components(directory)
                self._assert_canonically_inside(directory)
            finally:
                if anchor_handle is not None:
                    kernel32.CloseHandle(anchor_handle)
                for handle in reversed(handles):
                    kernel32.CloseHandle(handle)
            return

        open_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            open_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW
        try:
            directory_fd = os.open(directory, open_flags)
        except OSError as exc:
            raise OutputBoundaryError(
                f"Cannot acquire a stable POSIX output-directory descriptor: {directory}"
            ) from exc
        try:
            descriptor_stat = os.fstat(directory_fd)
            path_stat = os.stat(directory, follow_symlinks=False)
            if (
                descriptor_stat.st_dev,
                descriptor_stat.st_ino,
            ) != (
                path_stat.st_dev,
                path_stat.st_ino,
            ):
                raise OutputBoundaryError(
                    f"Output parent changed while acquiring its stable descriptor: {directory}"
                )
            yield StableDirectoryLease(directory, directory_fd=directory_fd)
        finally:
            os.close(directory_fd)

    def replace(self, temporary: Path, target: Path) -> None:
        temporary = self.prepare_file(temporary)
        target = self.prepare_file(target)
        if temporary.parent != target.parent:
            raise OutputBoundaryError("Atomic replacement must use a same-directory temporary file.")
        with self.stable_directory(target.parent) as lease:
            temporary = self.prepare_file(temporary)
            target = self.prepare_file(target)
            if not temporary.is_file() or is_reparse_path(temporary):
                raise OutputBoundaryError(
                    f"Atomic replacement source is not a regular in-root file: {temporary}"
                )
            self._assert_canonically_inside(temporary)
            self._assert_canonically_inside(target.parent)
            try:
                if os.name == "nt":
                    if lease.windows_directory_handle is None:
                        raise OutputBoundaryError(
                            "Stable Windows directory handle is unavailable for replace."
                        )
                    # The handle-relative child anchor held by stable_directory
                    # omits FILE_SHARE_DELETE. Windows therefore rejects a
                    # concurrent parent/ancestor rename or delete before this
                    # path-based replacement can be retargeted.
                    os.replace(temporary, target)
                else:
                    if lease.directory_fd is None:
                        raise OutputBoundaryError(
                            "Stable POSIX directory descriptor is unavailable for replace."
                        )
                    os.replace(
                        temporary.name,
                        target.name,
                        src_dir_fd=lease.directory_fd,
                        dst_dir_fd=lease.directory_fd,
                    )
            except OutputBoundaryError:
                raise
            except OSError as exc:
                raise OutputBoundaryError(
                    f"Stable atomic replacement failed for {target}: {exc}"
                ) from exc
            self._verify_existing_components(target)
            self._assert_canonically_inside(target)

    def rename_no_replace(self, source: Path, target: Path) -> None:
        """Atomically rename one regular file only when ``target`` is absent.

        Group transactions use this primitive for both publication and recovery,
        so a late external generation is never silently overwritten. Windows
        ``os.rename`` is no-replace. Linux uses ``renameat2(RENAME_NOREPLACE)``;
        unsupported POSIX platforms fail closed instead of emulating the move
        with an unlink race.
        """
        source = self.prepare_file(source)
        target = self.prepare_file(target)
        if source.parent != target.parent:
            raise OutputBoundaryError(
                "No-replace rename must use one stable output directory."
            )
        with self.stable_directory(target.parent) as lease:
            source = self.prepare_file(source)
            target = self.prepare_file(target)
            if not source.is_file() or is_reparse_path(source):
                raise OutputBoundaryError(
                    f"No-replace source is not a regular in-root file: {source}."
                )
            if os.path.lexists(target):
                raise OutputBoundaryError(
                    f"No-replace target already exists: {target}."
                )
            try:
                if os.name == "nt":
                    os.rename(source, target)
                else:
                    if lease.directory_fd is None:
                        raise OutputBoundaryError(
                            "Stable POSIX directory descriptor is unavailable for rename."
                        )
                    import ctypes

                    libc = ctypes.CDLL(None, use_errno=True)
                    renameat2 = getattr(libc, "renameat2", None)
                    if renameat2 is None:
                        raise OutputBoundaryError(
                            "Atomic no-replace rename is unavailable on this POSIX platform."
                        )
                    renameat2.argtypes = [
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_uint,
                    ]
                    renameat2.restype = ctypes.c_int
                    result = renameat2(
                        lease.directory_fd,
                        os.fsencode(source.name),
                        lease.directory_fd,
                        os.fsencode(target.name),
                        1,  # RENAME_NOREPLACE
                    )
                    if result != 0:
                        error = ctypes.get_errno()
                        if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
                            raise OutputBoundaryError(
                                "Atomic no-replace rename is unavailable on this POSIX filesystem."
                            )
                        raise OSError(error, os.strerror(error), os.fspath(target))
            except OutputBoundaryError:
                raise
            except OSError as exc:
                raise OutputBoundaryError(
                    f"Stable no-replace rename failed for {target}: {exc}"
                ) from exc
            if os.path.lexists(source):
                raise OutputBoundaryError(
                    f"No-replace rename left the source path present: {source}."
                )
            self._verify_existing_components(target)
            self._assert_canonically_inside(target)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        """Remove one regular in-root file without re-traversing a mutable parent."""
        path = self.prepare_file(path)
        with self.stable_directory(path.parent) as lease:
            path = self.prepare_file(path)
            if not os.path.lexists(path):
                if missing_ok:
                    return
                raise FileNotFoundError(path)
            if is_reparse_path(path) or not path.is_file():
                raise OutputBoundaryError(
                    f"Refusing to remove a non-regular or reparse output file: {path}"
                )
            try:
                if os.name == "nt":
                    if lease.windows_directory_handle is None:
                        raise OutputBoundaryError(
                            "Stable Windows directory handle is unavailable for unlink."
                        )
                    os.unlink(path)
                else:
                    if lease.directory_fd is None:
                        raise OutputBoundaryError(
                            "Stable POSIX directory descriptor is unavailable for unlink."
                        )
                    os.unlink(path.name, dir_fd=lease.directory_fd)
            except FileNotFoundError:
                if not missing_ok:
                    raise
            except OutputBoundaryError:
                raise
            except OSError as exc:
                raise OutputBoundaryError(
                    f"Stable output cleanup failed for {path}: {exc}"
                ) from exc

    def unlink_expected(
        self,
        path: Path,
        expected: ExpectedFileGeneration,
    ) -> None:
        """Delete the exact expected regular-file object, never a late alias."""
        if not expected.exists:
            raise OutputBoundaryError("Exact-generation unlink requires an existing file.")
        path = self.prepare_file(path)
        with self.stable_directory(path.parent) as lease:
            path = self.prepare_file(path)
            if os.name == "nt":
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

                class FileDispositionInformation(ctypes.Structure):
                    _fields_ = [("DeleteFile", wintypes.BOOL)]

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
                kernel32.SetFileInformationByHandle.argtypes = [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                ]
                kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                invalid = ctypes.c_void_p(-1).value
                handle = kernel32.CreateFileW(
                    str(path),
                    0x80000000 | 0x00010000,  # GENERIC_READ | DELETE
                    0x00000001,  # FILE_SHARE_READ; deny write/delete/replace
                    None,
                    3,
                    0x00200000 | 0x08000000,
                    None,
                )
                if handle in (None, 0, invalid):
                    raise OutputBoundaryError(
                        f"Cannot open exact cleanup generation {path}: "
                        f"winerror={ctypes.get_last_error()}."
                    )
                try:
                    information = ByHandleFileInformation()
                    if not kernel32.GetFileInformationByHandle(
                        handle,
                        ctypes.byref(information),
                    ):
                        raise OutputBoundaryError(
                            f"Cannot identify exact cleanup generation {path}: "
                            f"winerror={ctypes.get_last_error()}."
                        )
                    observed_identity = (
                        int(information.dwVolumeSerialNumber),
                        (int(information.nFileIndexHigh) << 32)
                        | int(information.nFileIndexLow),
                        (int(information.nFileSizeHigh) << 32)
                        | int(information.nFileSizeLow),
                        (int(information.ftLastWriteTime.dwHighDateTime) << 32)
                        | int(information.ftLastWriteTime.dwLowDateTime),
                    )
                    if observed_identity != expected.identity:
                        raise ResumeStateError(
                            f"Exact cleanup generation changed before handle delete: {path}."
                        )
                    disposition = FileDispositionInformation(DeleteFile=True)
                    if not kernel32.SetFileInformationByHandle(
                        handle,
                        4,  # FileDispositionInfo
                        ctypes.byref(disposition),
                        ctypes.sizeof(disposition),
                    ):
                        raise OutputBoundaryError(
                            f"Exact cleanup handle delete failed for {path}: "
                            f"winerror={ctypes.get_last_error()}."
                        )
                finally:
                    kernel32.CloseHandle(handle)
            else:
                if lease.directory_fd is None:
                    raise OutputBoundaryError(
                        "Stable POSIX directory descriptor is unavailable for exact unlink."
                    )
                flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
                descriptor = os.open(path.name, flags, dir_fd=lease.directory_fd)
                try:
                    try:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except ImportError:  # pragma: no cover
                        pass
                    metadata = os.fstat(descriptor)
                    identity = (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                    )
                    if identity != expected.identity:
                        raise ResumeStateError(
                            f"Exact cleanup generation changed before descriptor unlink: {path}."
                        )
                    named = os.stat(
                        path.name,
                        dir_fd=lease.directory_fd,
                        follow_symlinks=False,
                    )
                    if (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ResumeStateError(
                            f"Exact cleanup basename changed before descriptor unlink: {path}."
                        )
                    os.unlink(path.name, dir_fd=lease.directory_fd)
                finally:
                    os.close(descriptor)
        if os.path.lexists(path):
            raise OutputBoundaryError(
                f"Exact cleanup generation still has its canonical pathname: {path}."
            )


def boundary_for_parent(path: Path) -> OutputBoundary:
    """Compatibility boundary for direct helper calls outside a full export run."""
    return OutputBoundary(lexical_absolute(path).parent).initialize(scan_existing_tree=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_root(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def manifest_entries(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in paths:
        stat = path.stat()
        entries.append(
            {
                "path": rel(path, root),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return entries


def manifest_fingerprint(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_stable_read_lease(path: Path) -> StableReadLease:
    """Open one regular file with a stable identity for a read/commit window.

    Windows denies concurrent writes and replacement while the lease is live.
    POSIX retains a no-follow descriptor and takes an advisory exclusive lock;
    callers still verify bytes because POSIX locks require cooperating writers.
    """
    path = lexical_absolute(path)
    if os.name == "nt":
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
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000,  # GENERIC_READ
            0x00000001,  # FILE_SHARE_READ only: deny write/delete/replacement
            None,
            3,  # OPEN_EXISTING
            0x00200000 | 0x08000000,  # open reparse point, sequential scan
            None,
        )
        if handle in (None, 0, invalid_handle):
            error = ctypes.get_last_error()
            raise ResumeStateError(
                f"Cannot acquire stable read lease for {path}: winerror={error}."
            )
        information = ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ResumeStateError(
                f"Cannot identify stable read lease for {path}: winerror={error}."
            )
        if int(information.dwFileAttributes) & 0x00000400:
            kernel32.CloseHandle(handle)
            raise ResumeStateError(f"Stable read lease target is a reparse point: {path}.")
        identity = (
            int(information.dwVolumeSerialNumber),
            (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
            (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow),
            (int(information.ftLastWriteTime.dwHighDateTime) << 32)
            | int(information.ftLastWriteTime.dwLowDateTime),
        )
        return StableReadLease(
            path,
            identity,
            windows_handle=int(handle),
            windows_kernel32=kernel32,
        )

    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    descriptor = os.open(path, open_flags)
    try:
        try:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # pragma: no cover - non-POSIX fallback
            pass
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ResumeStateError(f"Stable read lease target is not a regular file: {path}.")
        return StableReadLease(
            path,
            (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns),
            file_descriptor=descriptor,
        )
    except BaseException:
        os.close(descriptor)
        raise


def verify_stable_read_lease(lease: StableReadLease) -> None:
    """Verify a leased file still names the same size/mtime identity."""
    try:
        metadata = lease.path.stat()
    except OSError as exc:
        raise ResumeStateError(
            f"Leased protocol-1 input is no longer reachable: {lease.path}."
        ) from exc
    if os.name == "nt":
        current = acquire_stable_read_lease(lease.path)
        try:
            if current.identity != lease.identity:
                raise ResumeStateError(
                    f"Leased protocol-1 input identity changed: {lease.path}."
                )
        finally:
            current.close()
        return
    identity = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
    if identity != lease.identity:
        raise ResumeStateError(
            f"Leased protocol-1 input identity changed: {lease.path}."
        )


def capture_expected_file_generation(path: Path) -> ExpectedFileGeneration:
    """Capture one regular target's existence, stable identity, size, and bytes.

    Windows holds a read lease that denies writes and replacement while hashing.
    POSIX retains the descriptor/advisory-lock boundary used by protocol-1 and
    verifies the path identity again after hashing.
    """
    path = lexical_absolute(path)
    if not os.path.lexists(path):
        return ExpectedFileGeneration(False)
    if is_reparse_path(path) or not path.is_file():
        raise OutputBoundaryError(
            f"Conditional commit target is not a regular non-reparse file: {path}."
        )
    lease = acquire_stable_read_lease(path)
    try:
        byte_size = path.stat().st_size
        digest = sha256_file(path)
        verify_stable_read_lease(lease)
        return ExpectedFileGeneration(
            True,
            identity=lease.identity,
            byte_size=byte_size,
            sha256=digest,
        )
    finally:
        lease.close()


def require_expected_file_generation(
    path: Path,
    expected: ExpectedFileGeneration,
    *,
    context: str,
) -> None:
    """Fail closed unless ``path`` still exposes exactly ``expected``."""
    current = capture_expected_file_generation(path)
    if current != expected:
        raise ResumeStateError(
            f"Conditional group commit target generation changed during {context}: "
            f"{path}."
        )


def acquire_expected_generation_lease(
    path: Path,
    expected: ExpectedFileGeneration,
    *,
    context: str,
) -> StableReadLease:
    """Lease one exact existing generation through a caller-visible handoff.

    Windows denies write/delete/replacement while the lease is live. POSIX keeps
    the descriptor and advisory lock, then revalidates the pathname at every
    handoff boundary; non-cooperating POSIX writers remain an explicit platform
    limitation rather than being mistaken for mandatory exclusion.
    """
    if not expected.exists:
        raise ResumeStateError(f"Cannot lease an absent generation during {context}: {path}.")
    lease = acquire_stable_read_lease(path)
    try:
        observed = ExpectedFileGeneration(
            True,
            identity=lease.identity,
            byte_size=path.stat().st_size,
            sha256=sha256_file(path),
        )
        verify_stable_read_lease(lease)
        if observed != expected:
            raise ResumeStateError(
                f"Conditional target generation changed while acquiring the {context} "
                f"handoff lease: {path}."
            )
        return lease
    except BaseException:
        lease.close()
        raise


def acquire_expected_generation_leases(
    targets: list[tuple[Path, ExpectedFileGeneration]],
    *,
    context: str,
) -> list[StableReadLease]:
    leases: list[StableReadLease] = []
    try:
        for path, expected in targets:
            leases.append(
                acquire_expected_generation_lease(path, expected, context=context)
            )
        for lease in leases:
            verify_stable_read_lease(lease)
        return leases
    except BaseException:
        for lease in reversed(leases):
            lease.close()
        raise


def remove_expected_generation_reconciled(
    path: Path,
    expected: ExpectedFileGeneration,
    *,
    output_boundary: OutputBoundary,
    context: str,
) -> None:
    """Remove one exact observed generation and coordinate late wrapper errors."""
    require_expected_file_generation(path, expected, context=context)
    try:
        output_boundary.unlink_expected(path, expected)
    except BaseException:
        observed = capture_expected_file_generation(path)
        if not observed.exists:
            return
        if observed == expected:
            raise
        raise ResumeStateError(
            f"Owned cleanup generation drifted during {context}: {path}."
        ) from sys.exc_info()[1]
    observed = capture_expected_file_generation(path)
    if observed.exists:
        state = "still names the owned generation" if observed == expected else "drifted"
        raise ResumeStateError(
            f"Owned cleanup path {state} after {context}: {path}."
        )


def flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def files_have_identical_bytes(left: Path, right: Path, chunk_size: int = 1024 * 1024) -> bool:
    """Compare regular files exactly, using size only as an early rejection."""
    if chunk_size <= 0:
        raise ValueError("File comparison chunk size must be positive.")
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(chunk_size)
                right_chunk = right_handle.read(chunk_size)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except (FileNotFoundError, IsADirectoryError):
        return False


def write_export_file_if_changed(
    path: Path,
    write_temp: Callable[[Path], Any],
    *,
    output_boundary: OutputBoundary | None = None,
) -> bool:
    """Atomically replace an exported file only when its exact bytes differ.

    The writer always receives a unique temporary file in the target directory.
    Existing regular targets are compared byte-for-byte, so a content match keeps
    the original file and its mtime. Symlinks are never followed for reuse; a
    differing output replaces the directory entry atomically with ``os.replace``.

    Returns ``True`` when the target was created or replaced, otherwise ``False``.
    """
    boundary = output_boundary or boundary_for_parent(path)
    path = boundary.prepare_file(path)
    with boundary.stable_directory(path.parent):
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".ga-export-",
            suffix=".partial",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        descriptor_open = True
        try:
            os.close(descriptor)
            descriptor_open = False
            temp_path = boundary.prepare_file(temp_path)
            write_temp(temp_path)
            temp_path = boundary.prepare_file(temp_path)
            if not temp_path.is_file() or is_reparse_path(temp_path):
                raise RuntimeError(
                    f"Export writer did not produce a regular temporary file: {temp_path}"
                )
            with temp_path.open("r+b") as handle:
                flush_and_sync(handle)
            path = boundary.prepare_file(path)
            if (
                path.is_file()
                and not is_reparse_path(path)
                and files_have_identical_bytes(temp_path, path)
            ):
                return False
            boundary.replace(temp_path, path)
            return True
        finally:
            active_exception = sys.exc_info()[0] is not None
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    if not active_exception:
                        raise
            try:
                boundary.unlink(temp_path, missing_ok=True)
            except Exception:
                if not active_exception:
                    raise


def atomic_write_json(
    path: Path,
    value: dict[str, Any],
    *,
    output_boundary: OutputBoundary | None = None,
) -> None:
    boundary = output_boundary or boundary_for_parent(path)
    path = boundary.prepare_file(path)
    with boundary.stable_directory(path.parent):
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{safe_name(path.name, 'json')}-",
            suffix=".partial",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        descriptor_open = True
        try:
            os.close(descriptor)
            descriptor_open = False
            temp_path = boundary.prepare_file(temp_path)
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                flush_and_sync(handle)
            boundary.replace(temp_path, path)
        finally:
            active_exception = sys.exc_info()[0] is not None
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    if not active_exception:
                        raise
            try:
                boundary.unlink(temp_path, missing_ok=True)
            except Exception:
                if not active_exception:
                    raise


def scan_export_completion_inventory(
    export_root: Path,
    *,
    reject_partials: bool = False,
) -> dict[str, Any]:
    """Inventory one output tree pass and include every nested partial file."""
    boundary = OutputBoundary(export_root).initialize(scan_existing_tree=False)
    file_count = 0
    total_bytes = 0
    partial_paths: list[str] = []
    fingerprint = hashlib.sha256()

    def fail_inventory_walk(error: OSError) -> None:
        raise OutputBoundaryError(f"Cannot inventory export tree: {error}")

    for current, directories, files in os.walk(
        boundary.root,
        followlinks=False,
        onerror=fail_inventory_walk,
    ):
        current_path = Path(current)
        boundary.ensure_directory(current_path)
        directories.sort()
        files.sort()
        for name in directories:
            directory = current_path / name
            if is_reparse_path(directory):
                raise OutputBoundaryError(
                    f"Export inventory contains a symlink/junction/reparse entry: {directory}"
                )
        for name in files:
            path = current_path / name
            if is_reparse_path(path):
                raise OutputBoundaryError(
                    f"Export inventory contains a symlink/junction/reparse entry: {path}"
                )
            metadata = path.stat()
            relative = path.relative_to(boundary.root).as_posix()
            file_count += 1
            total_bytes += metadata.st_size
            fingerprint.update(relative.encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(str(metadata.st_size).encode("ascii"))
            fingerprint.update(b"\0")
            if name.endswith(".partial"):
                partial_paths.append(relative)
    partial_paths.sort()
    if reject_partials and partial_paths:
        raise ResumeStateError(
            "Uncommitted partial files remain anywhere below the export root: "
            + ", ".join(partial_paths[:10])
        )
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "inventory_fingerprint_sha256": fingerprint.hexdigest(),
        "partial_file_count": len(partial_paths),
        "partial_paths": partial_paths,
    }


def _read_exact(handle: Any, size: int, label: str) -> bytes:
    if size < 0:
        raise LargeBundleError(f"Invalid negative byte count for {label}: {size}")
    data = handle.read(size)
    if len(data) != size:
        raise LargeBundleError(
            f"Truncated UnityFS {label}: expected {size} bytes, read {len(data)}."
        )
    return data


def _read_c_string(handle: Any, label: str, max_bytes: int = 4096) -> str:
    value = bytearray()
    while len(value) < max_bytes:
        byte = _read_exact(handle, 1, label)
        if byte == b"\0":
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LargeBundleError(f"Invalid UTF-8 in UnityFS {label}.") from exc
        value.extend(byte)
    raise LargeBundleError(f"UnityFS {label} exceeds {max_bytes} bytes.")


def _parse_engine_version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _uses_old_archive_flags(engine_version: tuple[int, int, int]) -> bool:
    major, minor, patch = engine_version
    if major < 2020:
        return True
    if major == 2020:
        return (major, minor, patch) < (2020, 3, 34)
    if major == 2021:
        return (major, minor, patch) < (2021, 3, 2)
    if major == 2022:
        return (major, minor, patch) < (2022, 1, 1)
    return False


def _unity_lzma_filters(header: bytes) -> list[dict[str, int]]:
    if len(header) != 5:
        raise LargeBundleError("Unity LZMA header must contain exactly five bytes.")
    props, dict_size = struct.unpack("<BI", header)
    if (
        props >= 9 * 5 * 5
        or dict_size <= 0
        or dict_size > LARGE_BUNDLE_MAX_LZMA_DICTIONARY_BYTES
    ):
        raise LargeBundleError(
            f"Invalid Unity LZMA properties: props={props}, dict_size={dict_size}."
        )
    lc = props % 9
    remainder = props // 9
    pb = remainder // 5
    lp = remainder % 5
    return [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": dict_size,
            "lc": lc,
            "lp": lp,
            "pb": pb,
        }
    ]


def _decompress_small_unityfs_payload(
    compressed: bytes,
    expected_size: int,
    flags: int,
) -> bytes:
    compression = flags & UNITYFS_COMPRESSION_MASK
    if compression == UNITYFS_COMPRESSION_NONE:
        result = compressed
    elif compression == UNITYFS_COMPRESSION_LZMA:
        if len(compressed) < 5:
            raise LargeBundleError("Truncated UnityFS LZMA payload header.")
        decoder = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW,
            filters=_unity_lzma_filters(compressed[:5]),
        )
        result = decoder.decompress(compressed[5:])
    elif compression in {UNITYFS_COMPRESSION_LZ4, UNITYFS_COMPRESSION_LZ4HC}:
        try:
            import lz4.block  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - UnityPy environment supplies lz4
            raise LargeBundleError("LZ4 is required to read UnityFS block metadata.") from exc
        result = lz4.block.decompress(compressed, expected_size)
    else:
        raise LargeBundleError(
            f"Unsupported UnityFS compression {compression} for block metadata."
        )
    if len(result) != expected_size:
        raise LargeBundleError(
            "UnityFS block metadata size mismatch: "
            f"expected {expected_size}, got {len(result)}."
        )
    return result


def _read_be_int(buffer: memoryview, offset: int, fmt: str, label: str) -> tuple[int, int]:
    size = struct.calcsize(">" + fmt)
    if offset + size > len(buffer):
        raise LargeBundleError(f"Truncated UnityFS directory while reading {label}.")
    return int(struct.unpack_from(">" + fmt, buffer, offset)[0]), offset + size


def _read_buffer_c_string(
    buffer: memoryview,
    offset: int,
    label: str,
) -> tuple[str, int]:
    end = bytes(buffer[offset:]).find(b"\0")
    if end < 0:
        raise LargeBundleError(f"Unterminated UnityFS directory string for {label}.")
    raw = bytes(buffer[offset : offset + end])
    try:
        return raw.decode("utf-8"), offset + end + 1
    except UnicodeDecodeError as exc:
        raise LargeBundleError(f"Invalid UTF-8 in UnityFS directory path for {label}.") from exc


def probe_unityfs_layout(path: Path) -> dict[str, Any] | None:
    """Parse UnityFS headers and directory metadata without loading data blocks."""
    path = path.resolve()
    with path.open("rb") as handle:
        if handle.read(8) != b"UnityFS\0":
            return None
        format_version = struct.unpack(">I", _read_exact(handle, 4, "format version"))[0]
        player_version = _read_c_string(handle, "player version")
        engine_version = _read_c_string(handle, "engine version")
        declared_size = struct.unpack(">q", _read_exact(handle, 8, "declared size"))[0]
        compressed_info_size = struct.unpack(
            ">I", _read_exact(handle, 4, "compressed block-info size")
        )[0]
        uncompressed_info_size = struct.unpack(
            ">I", _read_exact(handle, 4, "uncompressed block-info size")
        )[0]
        data_flags = struct.unpack(">I", _read_exact(handle, 4, "archive flags"))[0]
        if compressed_info_size > LARGE_BUNDLE_MAX_BLOCK_INFO_BYTES:
            raise LargeBundleError(
                "UnityFS compressed block metadata exceeds the safety limit: "
                f"{compressed_info_size} bytes."
            )
        if uncompressed_info_size > LARGE_BUNDLE_MAX_BLOCK_INFO_BYTES:
            raise LargeBundleError(
                "UnityFS uncompressed block metadata exceeds the safety limit: "
                f"{uncompressed_info_size} bytes."
            )
        parsed_engine = _parse_engine_version(engine_version)
        if _uses_old_archive_flags(parsed_engine) and data_flags & UNITYFS_OLD_ENCRYPTION_FLAG:
            raise LargeBundleError(
                "Encrypted UnityFS archives are not supported by the bounded-memory route."
            )
        if format_version >= 7 or (
            parsed_engine[0] == 2019 and parsed_engine >= (2019, 4, 15)
        ):
            handle.seek((handle.tell() + 15) & ~15)
        header_end = handle.tell()
        actual_size = path.stat().st_size
        if declared_size < 0 or declared_size > actual_size:
            raise LargeBundleError(
                f"UnityFS declared size {declared_size} exceeds file size {actual_size}."
            )
        if data_flags & UNITYFS_BLOCKS_INFO_AT_END:
            info_offset = actual_size - compressed_info_size
            if info_offset < header_end:
                raise LargeBundleError("UnityFS block metadata overlaps the header.")
            handle.seek(info_offset)
            compressed_info = _read_exact(handle, compressed_info_size, "block metadata")
            data_start = header_end
        else:
            compressed_info = _read_exact(handle, compressed_info_size, "block metadata")
            data_start = handle.tell()
        if not _uses_old_archive_flags(parsed_engine) and data_flags & 0x200:
            data_start = (data_start + 15) & ~15

    info = memoryview(
        _decompress_small_unityfs_payload(
            compressed_info,
            uncompressed_info_size,
            data_flags,
        )
    )
    offset = 0
    if len(info) < 16:
        raise LargeBundleError("UnityFS block metadata is missing its hash field.")
    info_hash = bytes(info[:16]).hex()
    offset = 16
    block_count, offset = _read_be_int(info, offset, "i", "block count")
    if block_count < 0 or block_count > 10_000_000:
        raise LargeBundleError(f"Unsafe UnityFS block count: {block_count}.")
    blocks: list[dict[str, int]] = []
    compressed_offset = data_start
    uncompressed_offset = 0
    for block_index in range(block_count):
        uncompressed_size, offset = _read_be_int(
            info, offset, "I", f"block {block_index} uncompressed size"
        )
        compressed_size, offset = _read_be_int(
            info, offset, "I", f"block {block_index} compressed size"
        )
        flags, offset = _read_be_int(info, offset, "H", f"block {block_index} flags")
        if flags & UNITYFS_BLOCK_ENCRYPTION_FLAG:
            raise LargeBundleError(
                f"Encrypted UnityFS data block {block_index} is unsupported."
            )
        blocks.append(
            {
                "index": block_index,
                "uncompressed_size": uncompressed_size,
                "compressed_size": compressed_size,
                "flags": flags,
                "compressed_offset": compressed_offset,
                "uncompressed_offset": uncompressed_offset,
            }
        )
        compressed_offset += compressed_size
        uncompressed_offset += uncompressed_size
    node_count, offset = _read_be_int(info, offset, "i", "node count")
    if node_count < 0 or node_count > 10_000_000:
        raise LargeBundleError(f"Unsafe UnityFS node count: {node_count}.")
    nodes: list[dict[str, Any]] = []
    for node_index in range(node_count):
        node_offset, offset = _read_be_int(info, offset, "q", f"node {node_index} offset")
        node_size, offset = _read_be_int(info, offset, "q", f"node {node_index} size")
        node_flags, offset = _read_be_int(info, offset, "I", f"node {node_index} flags")
        node_path, offset = _read_buffer_c_string(info, offset, f"node {node_index}")
        nodes.append(
            {
                "index": node_index,
                "offset": node_offset,
                "size": node_size,
                "flags": node_flags,
                "path": node_path,
            }
        )
    data_end = compressed_offset
    info_offset = actual_size - compressed_info_size if data_flags & UNITYFS_BLOCKS_INFO_AT_END else None
    if data_end > (info_offset if info_offset is not None else actual_size):
        raise LargeBundleError("UnityFS compressed data blocks exceed the file boundary.")
    layout = {
        "signature": "UnityFS",
        "format_version": format_version,
        "player_version": player_version,
        "engine_version": engine_version,
        "declared_bundle_size": declared_size,
        "actual_file_size": actual_size,
        "data_flags": data_flags,
        "header_end": header_end,
        "data_start": data_start,
        "blocks_info_at_end": bool(data_flags & UNITYFS_BLOCKS_INFO_AT_END),
        "blocks_info_sha256": hashlib.sha256(bytes(info)).hexdigest(),
        "uncompressed_data_hash": info_hash,
        "blocks": blocks,
        "nodes": nodes,
        "total_uncompressed_block_bytes": uncompressed_offset,
        "total_compressed_block_bytes": sum(block["compressed_size"] for block in blocks),
    }
    validate_unityfs_nodes(nodes, uncompressed_offset)
    return layout


def validate_unityfs_nodes(nodes: list[dict[str, Any]], total_uncompressed: int) -> None:
    normalized_paths: set[str] = set()
    normalized_basenames: set[str] = set()
    ranges: list[tuple[int, int, int]] = []
    for expected_index, node in enumerate(nodes):
        index = int(node.get("index", expected_index))
        raw_path = str(node.get("path", ""))
        normalized = raw_path.replace("\\", "/")
        pure_path = PurePosixPath(normalized)
        if (
            not normalized
            or pure_path.is_absolute()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
            or ":" in normalized
            or "\0" in normalized
        ):
            raise LargeBundleError(f"Unsafe UnityFS node path: {raw_path!r}.")
        path_key = normalized.casefold()
        basename_key = pure_path.name.casefold()
        if path_key in normalized_paths:
            raise LargeBundleError(f"Duplicate UnityFS node path: {raw_path!r}.")
        if basename_key in normalized_basenames:
            raise LargeBundleError(
                "UnityFS node basename collision is unsafe for external payload resolution: "
                f"{pure_path.name!r}."
            )
        normalized_paths.add(path_key)
        normalized_basenames.add(basename_key)
        offset = int(node.get("offset", -1))
        size = int(node.get("size", -1))
        if offset < 0 or size < 0 or offset + size > total_uncompressed:
            raise LargeBundleError(
                f"UnityFS node {index} range is outside the decompressed stream: "
                f"offset={offset}, size={size}, total={total_uncompressed}."
            )
        ranges.append((offset, offset + size, index))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            raise LargeBundleError(
                f"UnityFS nodes {previous[2]} and {current[2]} overlap."
            )


def large_bundle_layout_fingerprint(layout: dict[str, Any]) -> str:
    stable = {
        "format_version": layout["format_version"],
        "engine_version": layout["engine_version"],
        "data_flags": layout["data_flags"],
        "data_start": layout["data_start"],
        "blocks_info_sha256": layout["blocks_info_sha256"],
        "blocks": [
            {
                "index": block["index"],
                "uncompressed_size": block["uncompressed_size"],
                "compressed_size": block["compressed_size"],
                "flags": block["flags"],
                "compressed_offset": block["compressed_offset"],
                "uncompressed_offset": block["uncompressed_offset"],
            }
            for block in layout["blocks"]
        ],
        "nodes": [
            {
                "index": node["index"],
                "offset": node["offset"],
                "size": node["size"],
                "flags": node["flags"],
                "path": node["path"],
            }
            for node in layout["nodes"]
        ],
    }
    payload = json.dumps(stable, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def should_use_large_bundle_strategy(
    layout: dict[str, Any] | None,
    block_threshold_bytes: int,
    node_threshold_bytes: int | None = None,
) -> bool:
    if layout is None:
        return False
    if block_threshold_bytes <= 0:
        raise ValueError("large bundle block threshold must be positive")
    if node_threshold_bytes is None:
        node_threshold_bytes = block_threshold_bytes
    if node_threshold_bytes <= 0:
        raise ValueError("large bundle node threshold must be positive")
    return any(
        int(block["uncompressed_size"]) >= block_threshold_bytes
        for block in layout["blocks"]
    ) or any(int(node["size"]) >= node_threshold_bytes for node in layout["nodes"])


def iter_unityfs_block_chunks(
    handle: Any,
    block: dict[str, Any],
    *,
    input_chunk_bytes: int,
    output_chunk_bytes: int,
    full_block_limit_bytes: int,
) -> Iterator[bytes]:
    """Yield bounded decompressed chunks; the LZMA route never returns a full block."""
    if input_chunk_bytes <= 0 or output_chunk_bytes <= 0:
        raise ValueError("UnityFS stream chunk sizes must be positive")
    compressed_size = int(block["compressed_size"])
    expected_size = int(block["uncompressed_size"])
    compression = int(block["flags"]) & UNITYFS_COMPRESSION_MASK
    if int(block["flags"]) & UNITYFS_BLOCK_ENCRYPTION_FLAG:
        raise LargeBundleError(f"Encrypted UnityFS block {block['index']} is unsupported.")
    handle.seek(int(block["compressed_offset"]))
    produced = 0
    if compression == UNITYFS_COMPRESSION_NONE:
        if compressed_size != expected_size:
            raise LargeBundleError(
                f"Uncompressed UnityFS block {block['index']} has mismatched sizes."
            )
        remaining = compressed_size
        while remaining:
            chunk = _read_exact(
                handle,
                min(input_chunk_bytes, remaining),
                f"uncompressed block {block['index']}",
            )
            remaining -= len(chunk)
            produced += len(chunk)
            yield chunk
    elif compression == UNITYFS_COMPRESSION_LZMA:
        if compressed_size < 5:
            raise LargeBundleError(f"UnityFS LZMA block {block['index']} is truncated.")
        header = _read_exact(handle, 5, f"LZMA block {block['index']} header")
        remaining = compressed_size - 5
        decoder = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW,
            filters=_unity_lzma_filters(header),
        )
        while remaining:
            compressed_chunk = _read_exact(
                handle,
                min(input_chunk_bytes, remaining),
                f"LZMA block {block['index']} data",
            )
            remaining -= len(compressed_chunk)
            pending = compressed_chunk
            while True:
                if decoder.eof:
                    if remaining or decoder.unused_data:
                        raise LargeBundleError(
                            f"UnityFS LZMA block {block['index']} ended before its compressed boundary."
                        )
                    break
                output = decoder.decompress(pending, max_length=output_chunk_bytes)
                pending = b""
                if output:
                    produced += len(output)
                    if len(output) > output_chunk_bytes:
                        raise LargeBundleError("LZMA decoder exceeded the configured output chunk.")
                    yield output
                if decoder.needs_input:
                    break
        while not decoder.needs_input and not decoder.eof:
            output = decoder.decompress(b"", max_length=output_chunk_bytes)
            if not output:
                break
            produced += len(output)
            yield output
    elif compression in {UNITYFS_COMPRESSION_LZ4, UNITYFS_COMPRESSION_LZ4HC}:
        if expected_size >= full_block_limit_bytes:
            raise LargeBundleError(
                f"UnityFS block {block['index']} uses non-streamable LZ4 compression "
                f"for {expected_size} uncompressed bytes; refusing normal UnityPy fallback."
            )
        compressed = _read_exact(handle, compressed_size, f"LZ4 block {block['index']}")
        try:
            import lz4.block  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - UnityPy environment supplies lz4
            raise LargeBundleError("LZ4 is required to spool this UnityFS bundle.") from exc
        decompressed = lz4.block.decompress(compressed, expected_size)
        produced = len(decompressed)
        for start in range(0, len(decompressed), output_chunk_bytes):
            yield decompressed[start : start + output_chunk_bytes]
    else:
        raise LargeBundleError(
            f"UnityFS block {block['index']} uses unsupported compression {compression}; "
            "refusing normal UnityPy fallback."
        )
    if produced != expected_size:
        raise LargeBundleError(
            f"UnityFS block {block['index']} size mismatch after decompression: "
            f"expected {expected_size}, got {produced}."
        )


def _large_bundle_spool_identity(
    source_path: Path,
    layout: dict[str, Any],
    io_chunk_bytes: int,
    full_block_limit_bytes: int,
) -> dict[str, Any]:
    stat = source_path.stat()
    return {
        "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
        "source_path": normalize_root(source_path),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "layout_fingerprint": large_bundle_layout_fingerprint(layout),
        "io_chunk_bytes": io_chunk_bytes,
        "full_block_limit_bytes": full_block_limit_bytes,
    }


def _spool_node_relative_path(node: dict[str, Any]) -> str:
    original_name = PurePosixPath(str(node["path"]).replace("\\", "/")).name
    safe_basename = safe_name(original_name, f"node_{int(node['index']):06d}")
    return f"nodes/{int(node['index']):06d}_{safe_basename}"


def validate_spooled_node(
    spool_root: Path,
    node_record: dict[str, Any],
    *,
    verify_hash: bool = True,
) -> Path:
    relative = node_record.get("spool_path")
    expected_hash = node_record.get("sha256")
    expected_size = int(node_record.get("size", -1))
    if not isinstance(relative, str) or not relative or not isinstance(expected_hash, str):
        raise LargeBundleError("Completed UnityFS spool node has incomplete metadata.")
    node_root = (spool_root / "nodes").resolve()
    node_path = (spool_root / relative).resolve()
    if not node_path.is_relative_to(node_root):
        raise LargeBundleError(f"Spool node escapes its root: {relative!r}.")
    if not node_path.is_file() or node_path.stat().st_size != expected_size:
        raise LargeBundleError(f"Completed UnityFS spool node is missing or has wrong size: {node_path}")
    if verify_hash and sha256_file(node_path) != expected_hash:
        raise LargeBundleError(f"Completed UnityFS spool node hash mismatch: {node_path}")
    return node_path


def spool_large_unityfs_bundle(
    source_path: Path,
    layout: dict[str, Any],
    spool_root: Path,
    *,
    io_chunk_bytes: int,
    full_block_limit_bytes: int,
    on_node_committed: Callable[[dict[str, Any]], None] | None = None,
    output_boundary: OutputBoundary | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Route a UnityFS decompressed stream directly into atomic per-node files."""
    source_path = source_path.resolve()
    spool_root = lexical_absolute(spool_root)
    boundary = output_boundary or OutputBoundary(spool_root).initialize()
    spool_root = boundary.ensure_directory(spool_root)
    boundary.ensure_directory(spool_root / "nodes")
    manifest_path = spool_root / "manifest.json"
    identity = _large_bundle_spool_identity(
        source_path,
        layout,
        io_chunk_bytes,
        full_block_limit_bytes,
    )
    validate_unityfs_nodes(layout["nodes"], int(layout["total_uncompressed_block_bytes"]))
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LargeBundleError(f"Cannot read UnityFS spool manifest: {exc}") from exc
        if manifest.get("identity") != identity:
            raise LargeBundleError(
                "UnityFS spool identity mismatch; existing spool was preserved and not reused."
            )
    else:
        manifest = {
            "strategy": "unityfs_streamed_nodes_then_batched_workers",
            "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
            "identity": identity,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "status": "spooling",
            "nodes": [
                {
                    "index": int(node["index"]),
                    "path": str(node["path"]),
                    "offset": int(node["offset"]),
                    "size": int(node["size"]),
                    "flags": int(node["flags"]),
                    "spool_path": _spool_node_relative_path(node),
                    "status": "pending",
                }
                for node in layout["nodes"]
            ],
            "batches": {},
        }
        atomic_write_json(manifest_path, manifest, output_boundary=boundary)

    records_by_index = {int(record["index"]): record for record in manifest["nodes"]}
    if set(records_by_index) != {int(node["index"]) for node in layout["nodes"]}:
        raise LargeBundleError("UnityFS spool manifest node set no longer matches the bundle layout.")
    for node in layout["nodes"]:
        record = records_by_index[int(node["index"])]
        expected_fields = {
            "path": str(node["path"]),
            "offset": int(node["offset"]),
            "size": int(node["size"]),
            "flags": int(node["flags"]),
            "spool_path": _spool_node_relative_path(node),
        }
        mismatched = [
            field for field, expected in expected_fields.items() if record.get(field) != expected
        ]
        if mismatched:
            raise LargeBundleError(
                "UnityFS spool node metadata mismatch for "
                f"node {node['index']}: {', '.join(mismatched)}."
            )
    reused_nodes = 0
    rebuilt_nodes = 0
    for record in manifest["nodes"]:
        if record.get("status") != "completed":
            continue
        try:
            validate_spooled_node(spool_root, record)
            reused_nodes += 1
        except LargeBundleError:
            record["status"] = "pending"
            record.pop("sha256", None)
            record.pop("completed_at", None)
    manifest["status"] = "spooling"
    manifest["updated_at"] = utc_now()
    atomic_write_json(manifest_path, manifest, output_boundary=boundary)

    if all(record.get("status") == "completed" for record in manifest["nodes"]):
        manifest["status"] = "complete"
        manifest["updated_at"] = utc_now()
        manifest["spool_evidence"] = {
            "node_count": len(manifest["nodes"]),
            "reused_node_count_this_run": reused_nodes,
            "rebuilt_node_count_this_run": 0,
            "total_node_bytes": sum(int(record["size"]) for record in manifest["nodes"]),
            "io_chunk_bytes": io_chunk_bytes,
            "streaming_lzma": any(
                int(block["flags"]) & UNITYFS_COMPRESSION_MASK == UNITYFS_COMPRESSION_LZMA
                for block in layout["blocks"]
            ),
            "decompression_skipped_all_nodes_reused": True,
        }
        atomic_write_json(manifest_path, manifest, output_boundary=boundary)
        return manifest, manifest["spool_evidence"]

    sorted_nodes = sorted(layout["nodes"], key=lambda item: (int(item["offset"]), int(item["index"])))
    open_handles: dict[int, Any] = {}
    hashers: dict[int, Any] = {}
    written: Counter[int] = Counter()

    def finalize_node(node: dict[str, Any]) -> None:
        nonlocal rebuilt_nodes
        index = int(node["index"])
        record = records_by_index[index]
        if record.get("status") == "completed":
            return
        final_path = boundary.prepare_file(spool_root / record["spool_path"])
        partial_path = final_path.with_name(f"{final_path.name}.partial")
        handle = open_handles.pop(index, None)
        if handle is None:
            boundary.ensure_directory(final_path.parent)
            partial_path = boundary.prepare_file(partial_path)
            handle = partial_path.open("wb")
            hashers[index] = hashlib.sha256()
        flush_and_sync(handle)
        handle.close()
        if written[index] != int(node["size"]):
            raise LargeBundleError(
                f"UnityFS node {index} size mismatch: expected {node['size']}, got {written[index]}."
            )
        boundary.replace(partial_path, final_path)
        record.update(
            {
                "status": "completed",
                "sha256": hashers.pop(index).hexdigest(),
                "completed_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        rebuilt_nodes += 1
        manifest["updated_at"] = utc_now()
        atomic_write_json(manifest_path, manifest, output_boundary=boundary)
        if on_node_committed is not None:
            on_node_committed(dict(record))

    node_directory_lease = boundary.stable_directory(spool_root / "nodes")
    node_directory_lease.__enter__()
    try:
        for node in sorted_nodes:
            if int(node["size"]) == 0 and records_by_index[int(node["index"])].get("status") != "completed":
                finalize_node(node)
        with source_path.open("rb") as source_handle:
            absolute_position = 0
            node_cursor = 0
            for block in layout["blocks"]:
                if int(block["uncompressed_offset"]) != absolute_position:
                    raise LargeBundleError("UnityFS block offsets are not contiguous.")
                for chunk in iter_unityfs_block_chunks(
                    source_handle,
                    block,
                    input_chunk_bytes=io_chunk_bytes,
                    output_chunk_bytes=io_chunk_bytes,
                    full_block_limit_bytes=full_block_limit_bytes,
                ):
                    chunk_start = absolute_position
                    chunk_end = chunk_start + len(chunk)
                    while node_cursor < len(sorted_nodes) and int(sorted_nodes[node_cursor]["offset"]) + int(sorted_nodes[node_cursor]["size"]) <= chunk_start:
                        node_cursor += 1
                    candidate_index = node_cursor
                    while candidate_index < len(sorted_nodes):
                        node = sorted_nodes[candidate_index]
                        node_start = int(node["offset"])
                        node_end = node_start + int(node["size"])
                        if node_start >= chunk_end:
                            break
                        overlap_start = max(chunk_start, node_start)
                        overlap_end = min(chunk_end, node_end)
                        if overlap_start < overlap_end:
                            record = records_by_index[int(node["index"])]
                            if record.get("status") != "completed":
                                index = int(node["index"])
                                if index not in open_handles:
                                    final_path = boundary.prepare_file(
                                        spool_root / record["spool_path"]
                                    )
                                    partial_path = final_path.with_name(f"{final_path.name}.partial")
                                    boundary.ensure_directory(final_path.parent)
                                    partial_path = boundary.prepare_file(partial_path)
                                    open_handles[index] = partial_path.open("wb")
                                    hashers[index] = hashlib.sha256()
                                piece = chunk[overlap_start - chunk_start : overlap_end - chunk_start]
                                open_handles[index].write(piece)
                                hashers[index].update(piece)
                                written[index] += len(piece)
                                if overlap_end == node_end:
                                    finalize_node(node)
                        candidate_index += 1
                    absolute_position = chunk_end
            if absolute_position != int(layout["total_uncompressed_block_bytes"]):
                raise LargeBundleError(
                    "UnityFS total decompressed size mismatch while routing spool nodes."
                )
    finally:
        active_exception = sys.exc_info()
        for handle in open_handles.values():
            try:
                flush_and_sync(handle)
            except Exception:
                pass
            handle.close()
        node_directory_lease.__exit__(*active_exception)

    for record in manifest["nodes"]:
        if record.get("status") != "completed":
            raise LargeBundleError(f"UnityFS spool node was not completed: {record['path']}")
        validate_spooled_node(spool_root, record)
    final_stat = source_path.stat()
    if final_stat.st_size != identity["source_size"] or final_stat.st_mtime_ns != identity["source_mtime_ns"]:
        raise LargeBundleError("UnityFS source changed while it was being spooled.")
    manifest["status"] = "complete"
    manifest["updated_at"] = utc_now()
    manifest["spool_evidence"] = {
        "node_count": len(manifest["nodes"]),
        "reused_node_count_this_run": reused_nodes,
        "rebuilt_node_count_this_run": rebuilt_nodes,
        "total_node_bytes": sum(int(record["size"]) for record in manifest["nodes"]),
        "io_chunk_bytes": io_chunk_bytes,
        "streaming_lzma": any(
            int(block["flags"]) & UNITYFS_COMPRESSION_MASK == UNITYFS_COMPRESSION_LZMA
            for block in layout["blocks"]
        ),
        "decompression_skipped_all_nodes_reused": False,
    }
    atomic_write_json(manifest_path, manifest, output_boundary=boundary)
    return manifest, manifest["spool_evidence"]


def _iter_serialized_file_objects(serialized_file: Any) -> Iterator[Any]:
    objects = getattr(serialized_file, "objects", None)
    if isinstance(objects, dict):
        yield from objects.values()
        return
    if objects is not None:
        yield from objects


def _iter_sprite_texture_caches(env: Any, serialized_file: Any) -> Iterator[dict[Any, Any]]:
    """Yield each SerializedFile cache once, including a target omitted from env.cabs."""
    seen: set[int] = set()
    candidates = [serialized_file, *getattr(env, "cabs", {}).values()]
    for candidate in candidates:
        cache = getattr(candidate, "_cache", None)
        if not isinstance(cache, dict) or id(cache) in seen:
            continue
        seen.add(id(cache))
        yield cache


def estimate_sprite_cache_image_bytes(value: Any) -> int | None:
    """Conservatively estimate decoded pixel storage without copying image bytes."""
    size = getattr(value, "size", None)
    if (
        not isinstance(size, tuple)
        or len(size) != 2
        or not all(isinstance(dimension, int) and dimension >= 0 for dimension in size)
        or not callable(getattr(value, "close", None))
    ):
        return None
    try:
        bands = tuple(value.getbands())
    except Exception:
        return None
    mode = str(getattr(value, "mode", ""))
    bytes_per_band = 4 if mode in {"F", "I"} or "32" in mode else 2 if "16" in mode else 1
    # Pillow commonly stores decoded Unity textures as RGB/RGBA. Four bytes per
    # pixel is a conservative floor for those modes and covers row padding with
    # a small fixed allowance without materializing value.tobytes().
    bytes_per_pixel = max(4, max(1, len(bands)) * bytes_per_band)
    width, height = size
    return width * height * bytes_per_pixel + 4096


class SpriteTextureCacheBudget:
    """Bound UnityPy's per-SerializedFile decoded Sprite texture cache.

    UnityPy intentionally keeps full source textures in ``SerializedFile._cache``
    so adjacent Sprites sharing an atlas are cheap. This controller preserves that
    local reuse, but evicts oldest inserted image entries after every exported
    object when either the decoded-pixel budget or entry window is exceeded.
    """

    def __init__(self, max_bytes: int, max_entries: int) -> None:
        if max_bytes < 0 or max_entries < 0:
            raise ValueError("Sprite cache byte and entry limits must be non-negative")
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._entries: dict[
            tuple[int, Any],
            tuple[dict[Any, Any], Any, Any, int],
        ] = {}
        self.evicted_entries = 0
        self.evicted_estimated_bytes = 0
        self.shutdown_cleared_entries = 0
        self.shutdown_cleared_estimated_bytes = 0
        self.close_errors = 0
        self.peak_observed_entries = 0
        self.peak_observed_estimated_bytes = 0
        self.peak_retained_entries = 0
        self.peak_retained_estimated_bytes = 0

    def _sync(self, caches: list[dict[Any, Any]]) -> tuple[int, int]:
        current_tokens: set[tuple[int, Any]] = set()
        for cache in caches:
            for key, value in list(cache.items()):
                estimated_bytes = estimate_sprite_cache_image_bytes(value)
                if estimated_bytes is None:
                    continue
                token = (id(cache), key)
                current_tokens.add(token)
                prior = self._entries.get(token)
                if prior is None or prior[2] is not value:
                    self._entries.pop(token, None)
                    self._entries[token] = (cache, key, value, estimated_bytes)
        for token in list(self._entries):
            if token not in current_tokens:
                self._entries.pop(token, None)
        total_bytes = sum(entry[3] for entry in self._entries.values())
        return len(self._entries), total_bytes

    def _close_if_untracked(self, value: Any) -> None:
        if any(entry[2] is value for entry in self._entries.values()):
            return
        try:
            value.close()
        except Exception:
            self.close_errors += 1

    def _evict_oldest(self) -> int:
        token = next(iter(self._entries))
        cache, key, value, estimated_bytes = self._entries.pop(token)
        if cache.get(key) is value:
            cache.pop(key, None)
        self._close_if_untracked(value)
        return estimated_bytes

    def enforce(self, caches: Iterator[dict[Any, Any]] | list[dict[Any, Any]]) -> None:
        cache_list = list(caches)
        entry_count, estimated_bytes = self._sync(cache_list)
        self.peak_observed_entries = max(self.peak_observed_entries, entry_count)
        self.peak_observed_estimated_bytes = max(
            self.peak_observed_estimated_bytes,
            estimated_bytes,
        )
        while self._entries and (
            len(self._entries) > self.max_entries
            or sum(entry[3] for entry in self._entries.values()) > self.max_bytes
        ):
            released_bytes = self._evict_oldest()
            self.evicted_entries += 1
            self.evicted_estimated_bytes += released_bytes
        retained_bytes = sum(entry[3] for entry in self._entries.values())
        self.peak_retained_entries = max(self.peak_retained_entries, len(self._entries))
        self.peak_retained_estimated_bytes = max(
            self.peak_retained_estimated_bytes,
            retained_bytes,
        )

    def clear_all(self, caches: Iterator[dict[Any, Any]] | list[dict[Any, Any]]) -> None:
        cache_list = list(caches)
        self._sync(cache_list)
        while self._entries:
            released_bytes = self._evict_oldest()
            self.shutdown_cleared_entries += 1
            self.shutdown_cleared_estimated_bytes += released_bytes

    def evidence(self) -> dict[str, Any]:
        return {
            "policy": "oldest_inserted_decoded_image_window",
            "max_estimated_decoded_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "peak_observed_entries_before_eviction": self.peak_observed_entries,
            "peak_observed_estimated_bytes_before_eviction": self.peak_observed_estimated_bytes,
            "peak_retained_entries_after_eviction": self.peak_retained_entries,
            "peak_retained_estimated_bytes_after_eviction": self.peak_retained_estimated_bytes,
            "evicted_entries": self.evicted_entries,
            "evicted_estimated_bytes": self.evicted_estimated_bytes,
            "shutdown_cleared_entries": self.shutdown_cleared_entries,
            "shutdown_cleared_estimated_bytes": self.shutdown_cleared_estimated_bytes,
            "close_errors": self.close_errors,
            "retained_entries_at_commit": len(self._entries),
            "retained_estimated_bytes_at_commit": sum(
                entry[3] for entry in self._entries.values()
            ),
        }


def load_spooled_serialized_environment(
    spool_root: Path,
    spool_manifest: dict[str, Any],
    serialized_node_index: int,
) -> tuple[Any, Any]:
    if UnityPy is None:
        raise LargeBundleWorkerError("UnityPy is unavailable in the large-bundle worker.")
    nodes = list(spool_manifest.get("nodes", []))
    target_record = next(
        (record for record in nodes if int(record.get("index", -1)) == serialized_node_index),
        None,
    )
    if target_record is None or not int(target_record.get("flags", 0)) & UNITYFS_SERIALIZED_NODE_FLAG:
        raise LargeBundleWorkerError(
            f"UnityFS node {serialized_node_index} is not a serialized node."
        )
    env = UnityPy.Environment(path=str(spool_root))
    open_handles: list[Any] = []
    serialized_file = None
    try:
        for record in nodes:
            node_path = validate_spooled_node(spool_root, record, verify_hash=False)
            node_handle = node_path.open("rb")
            open_handles.append(node_handle)
            parsed_file = env.load_file(
                node_handle,
                name=str(record["path"]),
                is_dependency=int(record["index"]) != serialized_node_index,
            )
            if int(record["index"]) == serialized_node_index:
                serialized_file = parsed_file
    except Exception:
        for handle in open_handles:
            handle.close()
        raise
    # EndianBinaryReader instances retain these streams. Keeping one explicit owner
    # also makes their lifetime unambiguous until the short-lived worker exits.
    setattr(env, "_game_analysis_spool_handles", open_handles)
    if serialized_file is None or not hasattr(serialized_file, "objects"):
        raise LargeBundleWorkerError(
            f"Spool node {target_record['path']!r} did not parse as a SerializedFile."
        )
    return env, serialized_file


def large_bundle_worker_export_batch(
    *,
    spool_manifest_path: Path,
    serialized_node_index: int,
    start: int,
    batch_size: int,
    source_label: str,
    export_root: Path,
    audio_mode: str,
    batch_csv_path: Path,
    batch_result_path: Path,
    asset_identity_scheme: str = ASSET_IDENTITY_SCHEME,
    sprite_cache_max_bytes: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
    sprite_cache_max_entries: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
) -> dict[str, Any]:
    """Export one object range and atomically commit its headerless CSV plus sidecar."""
    if start < 0 or batch_size <= 0:
        raise LargeBundleWorkerError("Worker start must be non-negative and batch size positive.")
    if sprite_cache_max_bytes < 0 or sprite_cache_max_entries < 0:
        raise LargeBundleWorkerError("Sprite cache byte and entry limits must be non-negative.")
    spool_manifest_path = spool_manifest_path.resolve()
    spool_root = spool_manifest_path.parent
    export_root = lexical_absolute(export_root)
    boundary = OutputBoundary(export_root).initialize(scan_existing_tree=False)
    batch_csv_path = lexical_absolute(batch_csv_path)
    batch_result_path = lexical_absolute(batch_result_path)
    batch_root = lexical_absolute(spool_root / "batches")
    batch_root_text = os.path.normcase(os.path.normpath(os.fspath(batch_root)))
    csv_parent_text = os.path.normcase(os.path.normpath(os.fspath(batch_csv_path.parent)))
    result_parent_text = os.path.normcase(
        os.path.normpath(os.fspath(batch_result_path.parent))
    )
    if (
        os.path.commonpath([batch_root_text, csv_parent_text]) != batch_root_text
        or os.path.commonpath([batch_root_text, result_parent_text]) != batch_root_text
    ):
        raise LargeBundleWorkerError("Large-bundle worker output escapes the spool batch directory.")
    manifest = json.loads(spool_manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise LargeBundleWorkerError("Large-bundle spool must be complete before worker export.")
    target_record = next(
        record
        for record in manifest["nodes"]
        if int(record["index"]) == serialized_node_index
    )
    identity = {
        "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
        "spool_identity": manifest["identity"],
        "serialized_node_index": serialized_node_index,
        "serialized_node_sha256": target_record["sha256"],
        "start": start,
        "batch_size": batch_size,
        "source_label": source_label,
        "export_root": normalize_root(export_root),
        "audio_mode": audio_mode,
        "sprite_cache_max_bytes": sprite_cache_max_bytes,
        "sprite_cache_max_entries": sprite_cache_max_entries,
    }
    if asset_identity_scheme == ASSET_IDENTITY_SCHEME:
        identity["asset_identity_scheme"] = asset_identity_scheme
    elif asset_identity_scheme != LEGACY_ASSET_IDENTITY_SCHEME:
        raise LargeBundleWorkerError(
            f"Unsupported asset identity scheme: {asset_identity_scheme!r}."
        )
    batch_csv_path = boundary.prepare_file(batch_csv_path)
    batch_result_path = boundary.prepare_file(batch_result_path)
    csv_temp = batch_csv_path.with_name(f"{batch_csv_path.name}.partial")
    result_temp = batch_result_path.with_name(f"{batch_result_path.name}.partial")
    env = None
    serialized_file = None
    container_paths = None
    obj = None
    row = None
    type_counts: Counter[str] = Counter()
    exported_counts: Counter[str] = Counter()
    row_count = 0
    has_more = False
    sprite_caches: list[dict[Any, Any]] = []
    sprite_cache_budget = SpriteTextureCacheBudget(
        sprite_cache_max_bytes,
        sprite_cache_max_entries,
    )
    try:
        env, serialized_file = load_spooled_serialized_environment(
            spool_root,
            manifest,
            serialized_node_index,
        )
        container_paths = collect_container_paths_including_dependencies(env)
        sprite_caches = list(_iter_sprite_texture_caches(env, serialized_file))
        sprite_cache_budget.enforce(sprite_caches)
        csv_temp = boundary.prepare_file(csv_temp)
        with boundary.stable_directory(csv_temp.parent):
            with csv_temp.open("w", newline="", encoding="utf-8") as csv_handle:
                writer = csv.DictWriter(csv_handle, fieldnames=INDEX_FIELDS)
                for object_index, obj in enumerate(
                    _iter_serialized_file_objects(serialized_file)
                ):
                    if object_index < start:
                        continue
                    if object_index >= start + batch_size:
                        has_more = True
                        break
                    asset_type = obj.type.name
                    type_counts[asset_type] += 1
                    try:
                        row, did_export = export_object(
                            obj,
                            source_label,
                            container_paths,
                            export_root,
                            audio_mode,
                            output_boundary=boundary,
                            asset_identity_scheme=asset_identity_scheme,
                        )
                    finally:
                        # SpriteHelper stores full decoded source textures in
                        # SerializedFile._cache. Enforce the configured reuse window
                        # even when export_object raises KeyboardInterrupt/SystemExit.
                        sprite_cache_budget.enforce(sprite_caches)
                    writer.writerow(row)
                    row_count += 1
                    if did_export:
                        exported_counts[asset_type] += 1
                    row = None
                flush_and_sync(csv_handle)
            # Nothing can benefit from the cache after this batch. Release it before
            # committing evidence, and repeat defensively in the outer finally path.
            sprite_cache_budget.clear_all(sprite_caches)
            boundary.replace(csv_temp, batch_csv_path)
        result = {
            "status": "completed",
            "completed_at": utc_now(),
            "identity": identity,
            "start": start,
            "end": start + row_count,
            "row_count": row_count,
            "has_more": has_more,
            "type_counts": dict(type_counts),
            "exported_counts": dict(exported_counts),
            "csv_path": str(batch_csv_path.relative_to(spool_root).as_posix()),
            "csv_sha256": sha256_file(batch_csv_path),
            "sprite_texture_cache": sprite_cache_budget.evidence(),
        }
        result_temp = boundary.prepare_file(result_temp)
        with boundary.stable_directory(result_temp.parent):
            with result_temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                flush_and_sync(handle)
            boundary.replace(result_temp, batch_result_path)
        return result
    finally:
        row = None
        obj = None
        container_paths = None
        sprite_cache_budget.clear_all(sprite_caches)
        sprite_caches = []
        serialized_file = None
        for handle in getattr(env, "_game_analysis_spool_handles", []) if env is not None else []:
            try:
                handle.close()
            except Exception:
                pass
        env = None
        del row, obj, container_paths, serialized_file, env
        gc.collect()


def validate_large_bundle_batch_result(
    request: dict[str, Any],
    *,
    require_worker_evidence: bool = False,
) -> dict[str, Any]:
    result_path = Path(request["batch_result_path"]).resolve()
    csv_path = Path(request["batch_csv_path"]).resolve()
    spool_manifest_path = Path(request["spool_manifest_path"]).resolve()
    spool_root = spool_manifest_path.parent
    manifest = json.loads(spool_manifest_path.read_text(encoding="utf-8"))
    target_record = next(
        record
        for record in manifest["nodes"]
        if int(record["index"]) == int(request["serialized_node_index"])
    )
    expected_identity = {
        "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
        "spool_identity": manifest["identity"],
        "serialized_node_index": int(request["serialized_node_index"]),
        "serialized_node_sha256": target_record["sha256"],
        "start": int(request["start"]),
        "batch_size": int(request["batch_size"]),
        "source_label": str(request["source_label"]),
        "export_root": normalize_root(Path(request["export_root"])),
        "audio_mode": str(request["audio_mode"]),
        "sprite_cache_max_bytes": int(request["sprite_cache_max_bytes"]),
        "sprite_cache_max_entries": int(request["sprite_cache_max_entries"]),
    }
    asset_identity_scheme = str(
        request.get("asset_identity_scheme", ASSET_IDENTITY_SCHEME)
    )
    if asset_identity_scheme == ASSET_IDENTITY_SCHEME:
        expected_identity["asset_identity_scheme"] = asset_identity_scheme
    elif asset_identity_scheme != LEGACY_ASSET_IDENTITY_SCHEME:
        raise LargeBundleWorkerError(
            f"Unsupported asset identity scheme: {asset_identity_scheme!r}."
        )
    if not result_path.is_file() or not csv_path.is_file():
        raise LargeBundleWorkerError("Large-bundle batch result or CSV is missing.")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LargeBundleWorkerError(f"Cannot parse large-bundle batch result: {exc}") from exc
    if result.get("status") != "completed" or result.get("identity") != expected_identity:
        raise LargeBundleWorkerError("Large-bundle batch result identity mismatch.")
    expected_csv_rel = csv_path.relative_to(spool_root).as_posix()
    if result.get("csv_path") != expected_csv_rel:
        raise LargeBundleWorkerError("Large-bundle batch CSV path mismatch.")
    if sha256_file(csv_path) != result.get("csv_sha256"):
        raise LargeBundleWorkerError("Large-bundle batch CSV hash mismatch.")
    start = int(result.get("start", -1))
    end = int(result.get("end", -1))
    row_count = int(result.get("row_count", -1))
    if start != int(request["start"]) or end - start != row_count or row_count < 0:
        raise LargeBundleWorkerError("Large-bundle batch range metadata is invalid.")
    if bool(result.get("has_more")) and row_count == 0:
        raise LargeBundleWorkerError("Large-bundle worker reported more objects after an empty batch.")
    cache_evidence = result.get("sprite_texture_cache")
    if not isinstance(cache_evidence, dict):
        raise LargeBundleWorkerError("Large-bundle worker omitted Sprite texture cache evidence.")
    max_bytes = int(request["sprite_cache_max_bytes"])
    max_entries = int(request["sprite_cache_max_entries"])
    try:
        retained_peak_bytes = int(
            cache_evidence["peak_retained_estimated_bytes_after_eviction"]
        )
        retained_peak_entries = int(cache_evidence["peak_retained_entries_after_eviction"])
        retained_commit_entries = int(cache_evidence["retained_entries_at_commit"])
        retained_commit_bytes = int(cache_evidence["retained_estimated_bytes_at_commit"])
        close_errors = int(cache_evidence["close_errors"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LargeBundleWorkerError(
            "Large-bundle worker Sprite texture cache evidence is incomplete."
        ) from exc
    if (
        cache_evidence.get("policy") != "oldest_inserted_decoded_image_window"
        or int(cache_evidence.get("max_estimated_decoded_bytes", -1)) != max_bytes
        or int(cache_evidence.get("max_entries", -1)) != max_entries
        or not 0 <= retained_peak_bytes <= max_bytes
        or not 0 <= retained_peak_entries <= max_entries
        or retained_commit_entries != 0
        or retained_commit_bytes != 0
        or close_errors != 0
    ):
        raise LargeBundleWorkerError(
            "Large-bundle worker Sprite texture cache evidence violates its configured boundary."
        )
    worker_evidence = result.get("worker_evidence")
    if worker_evidence is None and require_worker_evidence:
        raise LargeBundleWorkerError(
            "Large-bundle batch is not parent-committed with worker memory evidence."
        )
    if worker_evidence is not None:
        validate_worker_memory_evidence(worker_evidence)
    return result


def _sample_windows_process_memory(pid: int) -> dict[str, int] | None:
    """Return one Windows process identity plus its current memory counters.

    Creation time is part of the result so callers can reject PID reuse instead
    of silently attaching a later, unrelated process to an existing worker run.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
        if not handle:
            return None
        try:
            creation_time = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            counters = ProcessMemoryCountersEx()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            ):
                return None
            return {
                "creation_time_100ns": (
                    int(creation_time.dwHighDateTime) << 32
                ) | int(creation_time.dwLowDateTime),
                "working_set_bytes": int(counters.WorkingSetSize),
                "private_bytes": int(counters.PrivateUsage),
            }
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _windows_process_snapshot() -> list[dict[str, Any]] | None:
    """Take a Toolhelp process snapshot without querying command lines."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32W),
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32W),
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot in (None, 0, invalid_handle):
            return None
        entries: list[dict[str, Any]] = []
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_entry:
                pid = int(entry.th32ProcessID)
                if pid > 0:
                    entries.append(
                        {
                            "pid": pid,
                            "parent_pid": int(entry.th32ParentProcessID),
                            "image_name": str(entry.szExeFile),
                        }
                    )
                entry.dwSize = ctypes.sizeof(entry)
                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        return entries
    except Exception:
        return None


class WindowsProcessTreeMemorySampler:
    """Audit a launcher and its live descendants using stable process identities.

    Peak working set and private bytes are the maximum current value observed for
    any *single* verified process in the launch tree. They are intentionally not
    summed: a venv launcher and the real interpreter are separate processes, and
    an aggregate would make the evidence incomparable with the historical
    single-worker safety threshold.
    """

    def __init__(self, root_pid: int) -> None:
        self.root_pid = int(root_pid)
        self.root_creation_time_100ns: int | None = None
        self.root_identity_verified = False
        self.poll_count = 0
        self.successful_snapshot_count = 0
        self.root_snapshot_verification_count = 0
        self.snapshot_failure_count = 0
        self.process_query_failure_count = 0
        self.pid_reuse_rejection_count = 0
        self._seen_sequence = 0
        self._records: dict[tuple[int, int], dict[str, Any]] = {}
        initial = _sample_windows_process_memory(self.root_pid)
        if initial is None:
            self.process_query_failure_count += 1
            return
        self.root_creation_time_100ns = int(initial["creation_time_100ns"])
        self.root_identity_verified = True
        self._observe(
            pid=self.root_pid,
            parent_pid=None,
            parent_creation_time_100ns=None,
            image_name=None,
            sample=initial,
            is_root=True,
        )

    def _observe(
        self,
        *,
        pid: int,
        parent_pid: int | None,
        parent_creation_time_100ns: int | None,
        image_name: str | None,
        sample: dict[str, int],
        is_root: bool,
    ) -> None:
        creation_time = int(sample["creation_time_100ns"])
        identity = (int(pid), creation_time)
        if is_root:
            parent_pid = None
            parent_creation_time_100ns = None
        elif parent_pid is None or parent_creation_time_100ns is None:
            raise LargeBundleWorkerError(
                "Worker descendant observation omitted its verified parent identity."
            )
        record = self._records.get(identity)
        if record is None:
            if any(existing_pid == int(pid) for existing_pid, _ in self._records):
                raise LargeBundleWorkerError(
                    "Worker sampler cannot persist multiple creation identities for one PID."
                )
            self._seen_sequence += 1
            record = {
                "pid": int(pid),
                "parent_pid": int(parent_pid) if parent_pid is not None else None,
                "parent_creation_time_100ns": (
                    int(parent_creation_time_100ns)
                    if parent_creation_time_100ns is not None
                    else None
                ),
                "creation_time_100ns": creation_time,
                "image_name": image_name,
                "role": "launch_root" if is_root else "descendant",
                "first_seen_sequence": self._seen_sequence,
                "last_seen_poll": self.poll_count,
                "sample_count": 0,
                "peak_working_set_bytes": 0,
                "peak_private_bytes": 0,
            }
            self._records[identity] = record
        else:
            if record.get("image_name") is None and image_name:
                record["image_name"] = image_name
            expected_parent_identity = (
                record.get("parent_pid"),
                record.get("parent_creation_time_100ns"),
            )
            observed_parent_identity = (parent_pid, parent_creation_time_100ns)
            if expected_parent_identity != observed_parent_identity:
                raise LargeBundleWorkerError(
                    "Worker process identity was observed with a different parent identity."
                )
        record["last_seen_poll"] = self.poll_count
        record["sample_count"] = int(record["sample_count"]) + 1
        record["peak_working_set_bytes"] = max(
            int(record["peak_working_set_bytes"]),
            int(sample["working_set_bytes"]),
        )
        record["peak_private_bytes"] = max(
            int(record["peak_private_bytes"]),
            int(sample["private_bytes"]),
        )

    def sample(self) -> None:
        """Sample verified live identities and discover descendants breadth-first."""
        self.poll_count += 1
        if not self.root_identity_verified:
            return
        snapshot = _windows_process_snapshot()
        if snapshot is None:
            self.snapshot_failure_count += 1
            return
        self.successful_snapshot_count += 1
        entries_by_pid = {
            int(entry["pid"]): entry
            for entry in snapshot
            if int(entry.get("pid", 0)) > 0
        }
        children_by_parent: dict[int, list[dict[str, Any]]] = {}
        for entry in entries_by_pid.values():
            children_by_parent.setdefault(int(entry.get("parent_pid", 0)), []).append(entry)

        confirmed: dict[int, int] = {}
        known_by_pid: dict[int, set[int]] = {}
        for pid, creation_time in self._records:
            known_by_pid.setdefault(pid, set()).add(creation_time)

        # First verify identities we have already observed. A mismatched creation
        # time is PID reuse and must never inherit old peak evidence or ancestry.
        for pid, expected_creation_times in known_by_pid.items():
            if pid not in entries_by_pid:
                continue
            process_sample = _sample_windows_process_memory(pid)
            if process_sample is None:
                self.process_query_failure_count += 1
                continue
            creation_time = int(process_sample["creation_time_100ns"])
            if creation_time not in expected_creation_times:
                self.pid_reuse_rejection_count += 1
                continue
            record = self._records[(pid, creation_time)]
            self._observe(
                pid=pid,
                parent_pid=record.get("parent_pid"),
                parent_creation_time_100ns=record.get(
                    "parent_creation_time_100ns"
                ),
                image_name=entries_by_pid[pid].get("image_name"),
                sample=process_sample,
                is_root=pid == self.root_pid and creation_time == self.root_creation_time_100ns,
            )
            confirmed[pid] = creation_time
            if pid == self.root_pid and creation_time == self.root_creation_time_100ns:
                self.root_snapshot_verification_count += 1

        # Only a currently verified parent can introduce a new child identity.
        # This prevents a reused, now-unrelated PID from grafting processes onto
        # a tree whose original parent has already exited.
        queue = list(confirmed)
        while queue:
            parent_pid = queue.pop(0)
            parent_creation_time = confirmed[parent_pid]
            for entry in children_by_parent.get(parent_pid, []):
                child_pid = int(entry["pid"])
                if child_pid in confirmed or child_pid in known_by_pid:
                    continue
                process_sample = _sample_windows_process_memory(child_pid)
                if process_sample is None:
                    self.process_query_failure_count += 1
                    continue
                child_creation_time = int(process_sample["creation_time_100ns"])
                if child_creation_time < parent_creation_time:
                    self.pid_reuse_rejection_count += 1
                    continue
                self._observe(
                    pid=child_pid,
                    parent_pid=parent_pid,
                    parent_creation_time_100ns=parent_creation_time,
                    image_name=entry.get("image_name"),
                    sample=process_sample,
                    is_root=False,
                )
                confirmed[child_pid] = child_creation_time
                queue.append(child_pid)

    def evidence(self) -> dict[str, Any]:
        records = sorted(
            self._records.values(),
            key=lambda record: (
                int(record["first_seen_sequence"]),
                int(record["pid"]),
                int(record["creation_time_100ns"]),
            ),
        )
        successful_sample_count = sum(int(record["sample_count"]) for record in records)
        unavailable_reason = None
        if not self.root_identity_verified:
            unavailable_reason = "root_process_identity_unavailable"
        elif self.successful_snapshot_count == 0:
            unavailable_reason = "process_snapshot_unavailable"
        elif self.root_snapshot_verification_count == 0:
            unavailable_reason = "launch_root_not_present_in_snapshot"
        if unavailable_reason is not None:
            return {
                "memory_method": "unavailable",
                "memory_sampling_status": "unavailable",
                "memory_unavailable_reason": unavailable_reason,
                "memory_peak_semantics": WINDOWS_PROCESS_TREE_PEAK_SEMANTICS,
                "root_pid": self.root_pid,
                "root_creation_time_100ns": self.root_creation_time_100ns,
                "root_identity_verified": self.root_identity_verified,
                "poll_count": self.poll_count,
                "successful_process_sample_count": successful_sample_count,
                "successful_snapshot_count": self.successful_snapshot_count,
                "root_snapshot_verification_count": self.root_snapshot_verification_count,
                "snapshot_failure_count": self.snapshot_failure_count,
                "process_query_failure_count": self.process_query_failure_count,
                "pid_reuse_rejection_count": self.pid_reuse_rejection_count,
                "observed_processes": records,
            }

        peak_working_record = max(
            records,
            key=lambda record: int(record["peak_working_set_bytes"]),
        )
        peak_private_record = max(
            records,
            key=lambda record: int(record["peak_private_bytes"]),
        )
        sampling_status = (
            "partial"
            if self.snapshot_failure_count or self.process_query_failure_count
            else "available"
        )
        return {
            "memory_method": WINDOWS_PROCESS_TREE_MEMORY_METHOD,
            "memory_sampling_status": sampling_status,
            "memory_sampling_completeness": (
                "partial_due_to_sampling_failures"
                if sampling_status == "partial"
                else "complete_for_successful_polls"
            ),
            "memory_peak_semantics": WINDOWS_PROCESS_TREE_PEAK_SEMANTICS,
            "root_pid": self.root_pid,
            "root_creation_time_100ns": self.root_creation_time_100ns,
            "root_identity_verified": self.root_identity_verified,
            "poll_count": self.poll_count,
            "successful_process_sample_count": successful_sample_count,
            "successful_snapshot_count": self.successful_snapshot_count,
            "root_snapshot_verification_count": self.root_snapshot_verification_count,
            "snapshot_failure_count": self.snapshot_failure_count,
            "process_query_failure_count": self.process_query_failure_count,
            "pid_reuse_rejection_count": self.pid_reuse_rejection_count,
            "observed_process_count": len(records),
            "observed_processes": records,
            "peak_working_set_bytes": int(peak_working_record["peak_working_set_bytes"]),
            "peak_working_set_process": {
                "pid": int(peak_working_record["pid"]),
                "creation_time_100ns": int(peak_working_record["creation_time_100ns"]),
            },
            "private_bytes": int(peak_private_record["peak_private_bytes"]),
            "peak_private_process": {
                "pid": int(peak_private_record["pid"]),
                "creation_time_100ns": int(peak_private_record["creation_time_100ns"]),
            },
        }


def validate_worker_memory_evidence(evidence: Any) -> dict[str, Any]:
    """Validate persisted worker memory evidence without re-sampling processes."""
    if not isinstance(evidence, dict):
        raise LargeBundleWorkerError("Worker memory evidence must be a JSON object.")
    method = evidence.get("memory_method")
    status = evidence.get("memory_sampling_status")
    if method == "unavailable":
        reason = evidence.get("memory_unavailable_reason")
        root_verified = evidence.get("root_identity_verified") is True
        try:
            successful_snapshot_count = int(
                evidence.get("successful_snapshot_count", 0) or 0
            )
            root_snapshot_verification_count = int(
                evidence.get("root_snapshot_verification_count", 0) or 0
            )
            snapshot_failure_count = int(evidence.get("snapshot_failure_count", 0) or 0)
            process_query_failure_count = int(
                evidence.get("process_query_failure_count", 0) or 0
            )
            claimed_working_set = int(evidence.get("peak_working_set_bytes", 0) or 0)
            claimed_private = int(evidence.get("private_bytes", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise LargeBundleWorkerError(
                "Unavailable worker memory evidence has invalid counters."
            ) from exc
        reason_matches_state = (
            (
                reason == "root_process_identity_unavailable"
                and not root_verified
                and process_query_failure_count > 0
            )
            or (
                reason == "process_snapshot_unavailable"
                and root_verified
                and successful_snapshot_count == 0
                and snapshot_failure_count > 0
            )
            or (
                reason == "launch_root_not_present_in_snapshot"
                and root_verified
                and successful_snapshot_count > 0
                and root_snapshot_verification_count == 0
            )
        )
        if (
            status != "unavailable"
            or not reason_matches_state
            or claimed_working_set != 0
            or claimed_private != 0
        ):
            raise LargeBundleWorkerError(
                "Unavailable worker memory evidence is incomplete or claims a peak."
            )
        return evidence
    if method != WINDOWS_PROCESS_TREE_MEMORY_METHOD:
        raise LargeBundleWorkerError(
            "Worker memory evidence uses an unverified single-PID or unknown sampling method."
        )
    if (
        status not in {"available", "partial"}
        or evidence.get("memory_peak_semantics") != WINDOWS_PROCESS_TREE_PEAK_SEMANTICS
        or evidence.get("root_identity_verified") is not True
    ):
        raise LargeBundleWorkerError("Worker process-tree memory evidence contract mismatch.")
    try:
        root_pid = int(evidence["root_pid"])
        compatibility_pid = int(evidence.get("pid", root_pid))
        root_creation_time = int(evidence["root_creation_time_100ns"])
        observed_process_count = int(evidence["observed_process_count"])
        successful_sample_count = int(evidence["successful_process_sample_count"])
        successful_snapshot_count = int(evidence["successful_snapshot_count"])
        root_snapshot_verification_count = int(
            evidence["root_snapshot_verification_count"]
        )
        peak_working_set = int(evidence["peak_working_set_bytes"])
        peak_private = int(evidence["private_bytes"])
        snapshot_failure_count = int(evidence["snapshot_failure_count"])
        process_query_failure_count = int(evidence["process_query_failure_count"])
        working_owner = evidence["peak_working_set_process"]
        private_owner = evidence["peak_private_process"]
        records = evidence["observed_processes"]
    except (KeyError, TypeError, ValueError) as exc:
        raise LargeBundleWorkerError(
            "Worker process-tree memory evidence is incomplete."
        ) from exc
    if (
        root_pid <= 0
        or compatibility_pid != root_pid
        or root_creation_time <= 0
        or not isinstance(records, list)
        or observed_process_count != len(records)
        or observed_process_count <= 0
        or successful_sample_count <= 0
        or successful_snapshot_count <= 0
        or root_snapshot_verification_count <= 0
        or peak_working_set <= 0
        or peak_private <= 0
        or snapshot_failure_count < 0
        or process_query_failure_count < 0
    ):
        raise LargeBundleWorkerError("Worker process-tree memory evidence has invalid totals.")
    has_sampling_failures = bool(snapshot_failure_count or process_query_failure_count)
    expected_completeness = (
        "partial_due_to_sampling_failures"
        if has_sampling_failures
        else "complete_for_successful_polls"
    )
    if (
        (status == "partial") != has_sampling_failures
        or evidence.get("memory_sampling_completeness") != expected_completeness
    ):
        raise LargeBundleWorkerError("Worker memory sampling completeness is inconsistent.")

    identities: set[tuple[int, int]] = set()
    identity_by_pid: dict[int, tuple[int, int]] = {}
    record_by_identity: dict[tuple[int, int], dict[str, Any]] = {}
    parent_by_identity: dict[tuple[int, int], tuple[int, int]] = {}
    sample_count_total = 0
    for record in records:
        if not isinstance(record, dict):
            raise LargeBundleWorkerError("Worker process record must be a JSON object.")
        try:
            pid = int(record["pid"])
            creation_time = int(record["creation_time_100ns"])
            sample_count = int(record["sample_count"])
            record_working_set = int(record["peak_working_set_bytes"])
            record_private = int(record["peak_private_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LargeBundleWorkerError("Worker process record is incomplete.") from exc
        identity = (pid, creation_time)
        if (
            pid <= 0
            or creation_time <= 0
            or sample_count <= 0
            or record_working_set < 0
            or record_private < 0
            or identity in identities
        ):
            raise LargeBundleWorkerError("Worker process record has an invalid identity or peak.")
        if pid in identity_by_pid and identity_by_pid[pid] != identity:
            raise LargeBundleWorkerError(
                "Worker evidence contains ambiguous reused PID identities."
            )
        identities.add(identity)
        identity_by_pid[pid] = identity
        record_by_identity[identity] = record
        sample_count_total += sample_count

    root_identity = (root_pid, root_creation_time)
    root_record = record_by_identity.get(root_identity)
    if root_record is None or root_record.get("role") != "launch_root":
        raise LargeBundleWorkerError("Worker evidence does not contain its verified launch root.")
    launch_roots = [record for record in records if record.get("role") == "launch_root"]
    if len(launch_roots) != 1:
        raise LargeBundleWorkerError("Worker evidence must contain exactly one launch root.")
    for record in records:
        identity = (int(record["pid"]), int(record["creation_time_100ns"]))
        if record.get("role") == "launch_root":
            if (
                identity != root_identity
                or record.get("parent_pid") is not None
                or record.get("parent_creation_time_100ns") is not None
            ):
                raise LargeBundleWorkerError(
                    "Worker launch root identity or parent boundary is invalid."
                )
            continue
        if record.get("role") != "descendant":
            raise LargeBundleWorkerError("Worker process record has an unknown role.")
        try:
            parent_pid = int(record["parent_pid"])
            parent_creation_time = int(record["parent_creation_time_100ns"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LargeBundleWorkerError(
                "Worker descendant omitted its verified parent identity."
            ) from exc
        parent_identity = (parent_pid, parent_creation_time)
        if parent_identity not in record_by_identity:
            raise LargeBundleWorkerError(
                "Worker descendant parent identity is absent from the observed tree."
            )
        if identity[1] < parent_identity[1]:
            raise LargeBundleWorkerError(
                "Worker descendant was created before its verified parent identity."
            )
        parent_by_identity[identity] = parent_identity

    for identity in record_by_identity:
        if identity == root_identity:
            continue
        path: set[tuple[int, int]] = set()
        current = identity
        while current != root_identity:
            if current in path:
                raise LargeBundleWorkerError("Worker process identity graph contains a cycle.")
            path.add(current)
            parent_identity = parent_by_identity.get(current)
            if parent_identity is None:
                raise LargeBundleWorkerError(
                    "Worker process identity graph does not reach its launch root."
                )
            current = parent_identity

    expected_working_set = max(int(record["peak_working_set_bytes"]) for record in records)
    expected_private = max(int(record["peak_private_bytes"]) for record in records)
    if (
        sample_count_total != successful_sample_count
        or expected_working_set != peak_working_set
        or expected_private != peak_private
    ):
        raise LargeBundleWorkerError("Worker memory peaks do not reconcile to process records.")
    try:
        working_identity = (
            int(working_owner["pid"]),
            int(working_owner["creation_time_100ns"]),
        )
        private_identity = (
            int(private_owner["pid"]),
            int(private_owner["creation_time_100ns"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LargeBundleWorkerError("Worker peak owner identity is incomplete.") from exc
    if (
        working_identity not in record_by_identity
        or private_identity not in record_by_identity
        or int(record_by_identity[working_identity]["peak_working_set_bytes"])
        != peak_working_set
        or int(record_by_identity[private_identity]["peak_private_bytes"]) != peak_private
    ):
        raise LargeBundleWorkerError("Worker memory peak owner does not match its process record.")
    return evidence


def run_large_bundle_worker_subprocess(request: dict[str, Any]) -> dict[str, Any]:
    boundary = OutputBoundary(Path(request["export_root"])).initialize(
        scan_existing_tree=False
    )
    result_path = boundary.prepare_file(Path(request["batch_result_path"]))
    log_stem = result_path.with_suffix("")
    stdout_path = log_stem.with_name(f"{log_stem.name}.stdout.log")
    stderr_path = log_stem.with_name(f"{log_stem.name}.stderr.log")
    stdout_temp = stdout_path.with_name(f"{stdout_path.name}.partial")
    stderr_temp = stderr_path.with_name(f"{stderr_path.name}.partial")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--large-bundle-worker",
        "--spool-manifest",
        str(request["spool_manifest_path"]),
        "--serialized-node-index",
        str(request["serialized_node_index"]),
        "--batch-start",
        str(request["start"]),
        "--large-bundle-batch-size",
        str(request["batch_size"]),
        "--source-label",
        str(request["source_label"]),
        "--output",
        str(request["export_root"]),
        "--audio-mode",
        str(request["audio_mode"]),
        "--asset-identity-scheme",
        str(request.get("asset_identity_scheme", ASSET_IDENTITY_SCHEME)),
        "--batch-csv",
        str(request["batch_csv_path"]),
        "--batch-result",
        str(request["batch_result_path"]),
        "--large-bundle-sprite-cache-bytes",
        str(request["sprite_cache_max_bytes"]),
        "--large-bundle-sprite-cache-max-entries",
        str(request["sprite_cache_max_entries"]),
    ]
    started = time.monotonic()
    worker_environment = os.environ.copy()
    worker_environment["PYTHONUTF8"] = "1"
    worker_environment["PYTHONIOENCODING"] = "utf-8"
    stdout_path = boundary.prepare_file(stdout_path)
    stderr_path = boundary.prepare_file(stderr_path)
    stdout_temp = boundary.prepare_file(stdout_temp)
    stderr_temp = boundary.prepare_file(stderr_temp)
    with boundary.stable_directory(stdout_temp.parent):
        with stdout_temp.open(
            "w", encoding="utf-8", newline="\n"
        ) as stdout_handle, stderr_temp.open(
            "w", encoding="utf-8", newline="\n"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                env=worker_environment,
            )
            memory_sampler = WindowsProcessTreeMemorySampler(process.pid)
            while True:
                # Sample immediately after launch and once more after observing exit;
                # this gives short-lived direct workers and late-starting descendants
                # the widest practical audit window without changing worker lifetime.
                memory_sampler.sample()
                polled_return_code = process.poll()
                if polled_return_code is not None:
                    memory_sampler.sample()
                    break
                time.sleep(0.1)
            return_code = process.wait()
            flush_and_sync(stdout_handle)
            flush_and_sync(stderr_handle)
        boundary.replace(stdout_temp, stdout_path)
        boundary.replace(stderr_temp, stderr_path)
    memory_evidence = memory_sampler.evidence()
    evidence = {
        "pid": process.pid,
        "exit_code": return_code,
        "duration_seconds": round(time.monotonic() - started, 3),
        **memory_evidence,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    if return_code != 0:
        try:
            error_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            error_tail = ""
        raise LargeBundleWorkerError(
            f"Large-bundle worker failed with exit code {return_code}: {error_tail}"
        )
    validate_worker_memory_evidence(evidence)
    result = validate_large_bundle_batch_result(request)
    result["worker_evidence"] = evidence
    atomic_write_json(result_path, result, output_boundary=boundary)
    return validate_large_bundle_batch_result(request, require_worker_evidence=True)


def large_bundle_batch_request(
    *,
    spool_manifest_path: Path,
    serialized_node_index: int,
    start: int,
    batch_size: int,
    source_label: str,
    export_root: Path,
    audio_mode: str,
    asset_identity_scheme: str = ASSET_IDENTITY_SCHEME,
    sprite_cache_max_bytes: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
    sprite_cache_max_entries: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
) -> dict[str, Any]:
    batch_dir = (
        spool_manifest_path.parent
        / "batches"
        / f"node_{serialized_node_index:06d}"
    )
    return {
        "spool_manifest_path": str(spool_manifest_path.resolve()),
        "serialized_node_index": serialized_node_index,
        "start": start,
        "batch_size": batch_size,
        "source_label": source_label,
        "export_root": str(export_root.resolve()),
        "audio_mode": audio_mode,
        "asset_identity_scheme": asset_identity_scheme,
        "sprite_cache_max_bytes": sprite_cache_max_bytes,
        "sprite_cache_max_entries": sprite_cache_max_entries,
        "batch_csv_path": str((batch_dir / f"batch_{start:012d}.csv").resolve()),
        "batch_result_path": str((batch_dir / f"batch_{start:012d}.json").resolve()),
    }


def export_large_bundle_source(
    *,
    source_path: Path,
    source_label: str,
    export_root: Path,
    state_dir: Path,
    layout: dict[str, Any],
    fragment_temp: Path,
    fragment_path: Path,
    audio_mode: str,
    threshold_bytes: int,
    io_chunk_bytes: int,
    batch_size: int,
    sprite_cache_max_bytes: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
    sprite_cache_max_entries: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
    worker_runner: Callable[[dict[str, Any]], dict[str, Any]] = run_large_bundle_worker_subprocess,
    output_boundary: OutputBoundary | None = None,
    asset_identity_scheme: str = ASSET_IDENTITY_SCHEME,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("large bundle batch size must be positive")
    if sprite_cache_max_bytes < 0 or sprite_cache_max_entries < 0:
        raise ValueError("Sprite cache byte and entry limits must be non-negative")
    source_hash = hashlib.sha256(source_label.encode("utf-8")).hexdigest()[:24]
    boundary = output_boundary or OutputBoundary(export_root).initialize()
    spool_root = state_dir / "large_bundle_spool" / source_hash
    spool_manifest, spool_evidence = spool_large_unityfs_bundle(
        source_path,
        layout,
        spool_root,
        io_chunk_bytes=io_chunk_bytes,
        full_block_limit_bytes=threshold_bytes,
        output_boundary=boundary,
    )
    spool_manifest_path = spool_root / "manifest.json"
    serialized_nodes = [
        record
        for record in spool_manifest["nodes"]
        if int(record["flags"]) & UNITYFS_SERIALIZED_NODE_FLAG
    ]
    if not serialized_nodes:
        raise LargeBundleError("Large UnityFS bundle contains no serialized node (flags & 4).")
    all_batches: list[dict[str, Any]] = []
    worker_runs = 0
    reused_batches = 0
    worker_peak_working_set = 0
    worker_peak_private = 0
    worker_peak_working_set_process: dict[str, Any] | None = None
    worker_peak_private_process: dict[str, Any] | None = None
    worker_memory_status_counts: Counter[str] = Counter()
    sprite_cache_peak_observed = 0
    sprite_cache_peak_retained = 0
    sprite_cache_evicted_entries = 0
    for serialized_node in serialized_nodes:
        start = 0
        while True:
            request = large_bundle_batch_request(
                spool_manifest_path=spool_manifest_path,
                serialized_node_index=int(serialized_node["index"]),
                start=start,
                batch_size=batch_size,
                source_label=source_label,
                export_root=export_root,
                audio_mode=audio_mode,
                asset_identity_scheme=asset_identity_scheme,
                sprite_cache_max_bytes=sprite_cache_max_bytes,
                sprite_cache_max_entries=sprite_cache_max_entries,
            )
            try:
                result = validate_large_bundle_batch_result(
                    request,
                    require_worker_evidence=True,
                )
                reused_batches += 1
            except LargeBundleWorkerError:
                result = worker_runner(request)
                result = validate_large_bundle_batch_result(
                    request,
                    require_worker_evidence=True,
                )
                worker_runs += 1
            evidence = result.get("worker_evidence", {})
            memory_status = str(evidence.get("memory_sampling_status") or "missing")
            worker_memory_status_counts[memory_status] += 1
            evidence_working_set = int(evidence.get("peak_working_set_bytes", 0) or 0)
            if evidence_working_set > worker_peak_working_set:
                worker_peak_working_set = evidence_working_set
                owner = evidence.get("peak_working_set_process")
                worker_peak_working_set_process = dict(owner) if isinstance(owner, dict) else None
            evidence_private = int(evidence.get("private_bytes", 0) or 0)
            if evidence_private > worker_peak_private:
                worker_peak_private = evidence_private
                owner = evidence.get("peak_private_process")
                worker_peak_private_process = dict(owner) if isinstance(owner, dict) else None
            cache_evidence = result.get("sprite_texture_cache", {})
            sprite_cache_peak_observed = max(
                sprite_cache_peak_observed,
                int(cache_evidence.get("peak_observed_estimated_bytes_before_eviction", 0) or 0),
            )
            sprite_cache_peak_retained = max(
                sprite_cache_peak_retained,
                int(cache_evidence.get("peak_retained_estimated_bytes_after_eviction", 0) or 0),
            )
            sprite_cache_evicted_entries += int(cache_evidence.get("evicted_entries", 0) or 0)
            all_batches.append(result)
            end = int(result["end"])
            if not result.get("has_more"):
                break
            if end <= start:
                raise LargeBundleWorkerError("Large-bundle batch did not advance its range.")
            start = end

    final_source_stat = source_path.stat()
    spool_identity = spool_manifest["identity"]
    if (
        final_source_stat.st_size != int(spool_identity["source_size"])
        or final_source_stat.st_mtime_ns != int(spool_identity["source_mtime_ns"])
    ):
        raise LargeBundleError(
            "UnityFS source changed while batch workers were exporting; "
            "the source fragment was not committed."
        )
    # Workers use size/path checks to avoid re-hashing a multi-gigabyte payload on
    # every batch. Re-verify every node once here before the source-level commit.
    for node_record in spool_manifest["nodes"]:
        validate_spooled_node(spool_root, node_record)

    type_counts: Counter[str] = Counter()
    exported_counts: Counter[str] = Counter()
    row_count = 0
    fragment_temp = boundary.prepare_file(fragment_temp)
    fragment_path = boundary.prepare_file(fragment_path)
    with boundary.stable_directory(fragment_temp.parent):
        with fragment_temp.open("w", newline="", encoding="utf-8") as fragment_handle:
            for result in all_batches:
                batch_csv = (spool_root / result["csv_path"]).resolve()
                if not batch_csv.is_relative_to((spool_root / "batches").resolve()):
                    raise LargeBundleWorkerError(
                        "Large-bundle batch CSV escapes its spool root."
                    )
                with batch_csv.open("r", encoding="utf-8", newline="") as batch_handle:
                    shutil.copyfileobj(batch_handle, fragment_handle, length=1024 * 1024)
                row_count += int(result["row_count"])
                type_counts.update(result.get("type_counts", {}))
                exported_counts.update(result.get("exported_counts", {}))
            flush_and_sync(fragment_handle)
        boundary.replace(fragment_temp, fragment_path)
    batch_summary = {
        "batch_size": batch_size,
        "batch_count": len(all_batches),
        "worker_runs_this_attempt": worker_runs,
        "reused_batch_count_this_attempt": reused_batches,
        "worker_memory_method": (
            WINDOWS_PROCESS_TREE_MEMORY_METHOD
            if (
                worker_memory_status_counts.get("available", 0)
                or worker_memory_status_counts.get("partial", 0)
            )
            else "unavailable"
        ),
        "worker_memory_peak_semantics": (
            WINDOWS_PROCESS_TREE_PEAK_SEMANTICS
            if (
                worker_memory_status_counts.get("available", 0)
                or worker_memory_status_counts.get("partial", 0)
            )
            else None
        ),
        "worker_memory_status_counts": dict(worker_memory_status_counts),
        "peak_worker_working_set_bytes": worker_peak_working_set or None,
        "peak_worker_working_set_process": worker_peak_working_set_process,
        "peak_worker_private_bytes": worker_peak_private or None,
        "peak_worker_private_process": worker_peak_private_process,
        "sprite_texture_cache": {
            "max_estimated_decoded_bytes": sprite_cache_max_bytes,
            "max_entries": sprite_cache_max_entries,
            "peak_observed_estimated_bytes_before_eviction": sprite_cache_peak_observed,
            "peak_retained_estimated_bytes_after_eviction": sprite_cache_peak_retained,
            "evicted_entries": sprite_cache_evicted_entries,
        },
    }
    spool_manifest["batches"][source_label] = batch_summary
    spool_manifest["updated_at"] = utc_now()
    atomic_write_json(
        spool_manifest_path,
        spool_manifest,
        output_boundary=boundary,
    )
    return {
        "row_count": row_count,
        "type_counts": dict(type_counts),
        "exported_counts": dict(exported_counts),
        "fragment_sha256": sha256_file(fragment_path),
        "large_bundle": {
            "strategy": "unityfs_streamed_nodes_then_batched_workers",
            "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
            "layout_fingerprint": large_bundle_layout_fingerprint(layout),
            "spool_manifest": rel(spool_manifest_path, export_root),
            "spool": spool_evidence,
            "batches": batch_summary,
            "serialized_node_count": len(serialized_nodes),
        },
    }


def sample_process_memory() -> dict[str, Any] | None:
    try:
        import psutil  # type: ignore[import-not-found]

        info = psutil.Process(os.getpid()).memory_full_info()
        values = info._asdict()
        working_set = values.get("peak_wset") or values.get("rss")
        private_bytes = values.get("private") or values.get("private_bytes")
        return {
            "method": "psutil_process_memory_info_sampled_at_source_boundaries",
            "working_set_bytes": int(working_set) if working_set is not None else None,
            "private_bytes": int(private_bytes) if private_bytes is not None else None,
        }
    except Exception:
        return None


def update_peak_memory(checkpoint: dict[str, Any]) -> None:
    sample = sample_process_memory()
    if sample is None:
        checkpoint.setdefault(
            "peak_memory",
            {
                "method": "unavailable",
                "peak_working_set_bytes": None,
                "peak_private_bytes": None,
            },
        )
        return
    peak = checkpoint.setdefault(
        "peak_memory",
        {
            "method": sample["method"],
            "peak_working_set_bytes": None,
            "peak_private_bytes": None,
        },
    )
    peak["method"] = sample["method"]
    for sample_key, peak_key in (
        ("working_set_bytes", "peak_working_set_bytes"),
        ("private_bytes", "peak_private_bytes"),
    ):
        value = sample.get(sample_key)
        prior = peak.get(peak_key)
        if value is not None and (prior is None or value > prior):
            peak[peak_key] = value


def safe_name(value: str, fallback: str) -> str:
    text = value.strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text[:96].strip("._") or fallback


WINDOWS_RESERVED_COMPONENTS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def canonical_physical_export_path(value: str) -> str:
    """Require one exact canonical relative POSIX path for a physical output.

    Rejecting aliases is intentional: ``.`` segments, repeated separators and
    backslashes must never receive different uniqueness keys for one file.
    """
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise ResumeStateError(f"Non-canonical physical export_path: {value!r}.")
    if value.startswith("/") or ":" in value:
        raise ResumeStateError(f"Unsafe physical export_path: {value!r}.")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ResumeStateError(f"Non-canonical physical export_path: {value!r}.")
    canonical = PurePosixPath(*raw_parts).as_posix()
    if canonical != value:
        raise ResumeStateError(f"Non-canonical physical export_path: {value!r}.")
    for component in raw_parts:
        if component.rstrip(" .") != component:
            raise ResumeStateError(
                "Non-canonical physical export_path contains a Windows-aliased "
                f"component: {component!r}."
            )
        reserved_stem = component.split(".", 1)[0].casefold()
        if reserved_stem in WINDOWS_RESERVED_COMPONENTS:
            raise ResumeStateError(
                "Non-canonical physical export_path contains a Windows-reserved "
                f"component: {component!r}."
            )
    return canonical


def canonical_audio_sample_filename(sample_key: Any) -> str:
    """Map one raw AudioClip sample key to a Windows-safe unique component."""
    raw_key = sample_key if isinstance(sample_key, str) else str(sample_key)
    canonical = json.dumps(
        [
            "AudioClip.sample_key.v1",
            f"{type(sample_key).__module__}.{type(sample_key).__qualname__}",
            raw_key,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    full_key_sha256 = hashlib.sha256(canonical).hexdigest()
    raw_basename = PurePosixPath(raw_key.replace("\\", "/")).name or "sample"
    suffix_match = re.search(r"(\.[A-Za-z0-9]{1,16})$", raw_basename)
    suffix = suffix_match.group(1) if suffix_match else ""
    raw_stem = raw_basename[: -len(suffix)] if suffix else raw_basename
    readable_stem = safe_name(raw_stem, "sample")[:48].rstrip(" .") or "sample"
    if readable_stem.casefold() in WINDOWS_RESERVED_COMPONENTS:
        readable_stem = f"_{readable_stem}"
    filename = f"{readable_stem}_sha256_{full_key_sha256}{suffix}"
    if (
        len(filename) > 255
        or filename.rstrip(" .") != filename
        or filename.split(".", 1)[0].casefold() in WINDOWS_RESERVED_COMPONENTS
    ):
        raise OutputBoundaryError(
            f"AudioClip sample key could not be mapped to a safe filename: {raw_key!r}."
        )
    return filename


def asset_identity_scheme_for_protocol(protocol_version: int) -> str:
    if protocol_version == LEGACY_RESUME_PROTOCOL_VERSION:
        return LEGACY_ASSET_IDENTITY_SCHEME
    if protocol_version == RESUME_PROTOCOL_VERSION:
        return ASSET_IDENTITY_SCHEME
    raise ResumeStateError(
        f"Unsupported UnityPy resume protocol version: {protocol_version!r}."
    )


def canonical_asset_id(
    source_label: str,
    serialized_file: str,
    path_id: int,
    asset_identity_scheme: str,
) -> str:
    """Build a stable output identity without lossy-normalization collisions."""
    if asset_identity_scheme == LEGACY_ASSET_IDENTITY_SCHEME:
        return (
            f"{safe_name(source_label, 'source')}_"
            f"{safe_name(serialized_file, 'serialized')}_{path_id}"
        )
    if asset_identity_scheme != ASSET_IDENTITY_SCHEME:
        raise ResumeStateError(
            f"Unsupported asset identity scheme: {asset_identity_scheme!r}."
        )
    canonical = json.dumps(
        [source_label, serialized_file, int(path_id)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    full_identity_sha256 = hashlib.sha256(canonical).hexdigest()
    # Keep the readable part short enough that a 96-character asset name plus
    # extension remains below the common 255-character component limit.
    source_prefix = safe_name(source_label, "source")[:20]
    serialized_prefix = safe_name(serialized_file, "serialized")[:32]
    return (
        f"{source_prefix}_{serialized_prefix}_{path_id}_"
        f"sha256_{full_identity_sha256}"
    )


def validate_canonical_source_tokens(
    source_labels: list[str],
    asset_identity_scheme: str,
) -> None:
    """Fail before checkpoint load when distinct labels share a source token.

    Protocol 1 cannot change its historical asset IDs, so its safe-name token is
    checked globally. Protocol 2 tokens include the complete label SHA-256; the
    same gate remains as a defensive invariant and documents the active scheme.
    """
    token_owner: dict[str, str] = {}
    for source_label in source_labels:
        if asset_identity_scheme == LEGACY_ASSET_IDENTITY_SCHEME:
            token = safe_name(source_label, "source")
        elif asset_identity_scheme == ASSET_IDENTITY_SCHEME:
            token = hashlib.sha256(source_label.encode("utf-8")).hexdigest()
        else:
            raise ResumeStateError(
                f"Unsupported asset identity scheme: {asset_identity_scheme!r}."
            )
        previous = token_owner.setdefault(token, source_label)
        if previous != source_label:
            raise ResumeStateError(
                "Distinct source labels collide under the active canonical source "
                f"token scheme {asset_identity_scheme!r}: {previous!r} and "
                f"{source_label!r}. Checkpoint and outputs were not modified."
            )


def category_dir(asset_type: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", asset_type) or "Unknown"


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 7:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes_len": len(value), "preview_hex": value[:32].hex()}
    if isinstance(value, (list, tuple)):
        if len(value) > 240:
            return [to_jsonable(v, depth + 1) for v in value[:240]] + [{"truncated_items": len(value) - 240}]
        return [to_jsonable(v, depth + 1) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 240:
                out["<truncated_keys>"] = len(value) - 240
                break
            out[str(k)] = to_jsonable(v, depth + 1)
        return out
    simple = {}
    for attr in ("m_FileID", "m_PathID", "path_id", "type", "m_Name"):
        if hasattr(value, attr):
            try:
                simple[attr] = to_jsonable(getattr(value, attr), depth + 1)
            except Exception:
                pass
    if simple:
        return simple
    return repr(value)


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def serialized_file_name(obj: Any) -> str:
    assets_file = getattr(obj, "assets_file", None)
    return str(getattr(assets_file, "name", "") or "serialized")


def texture2d_has_image_payload(data: Any) -> tuple[bool, str]:
    """Reject an empty Texture2D payload before UnityPy resolves dependencies.

    UnityPy represents ``m_StreamData`` as a truthy object even when its path and
    size are both empty. Asking for ``data.image`` in that state makes its
    dependency loader resolve the empty path to the environment directory and
    attempt to open that directory as a file. An empty inline buffer plus an
    empty/zero-length stream is an indexed texture placeholder, not a readable
    image payload.
    """
    inline_data = getattr(data, "image_data", None)
    try:
        inline_size = len(inline_data) if inline_data is not None else 0
    except (TypeError, ValueError):
        inline_size = None
    if inline_size is not None and inline_size > 0:
        return True, "inline_image_data"
    if inline_size is None:
        try:
            if bool(inline_data):
                return True, "inline_image_data"
        except Exception:
            pass

    stream_data = getattr(data, "m_StreamData", None)
    stream_path = str(getattr(stream_data, "path", "") or "").strip()
    try:
        stream_size = int(getattr(stream_data, "size", 0) or 0)
    except (TypeError, ValueError):
        stream_size = 0
    if stream_path and stream_size > 0:
        return True, "external_stream"
    return (
        False,
        "Texture2D has neither inline image bytes nor a non-empty external stream "
        "reference; image decoding and empty-path dependency lookup were skipped.",
    )


def write_text_asset(
    data: Any,
    out_path: Path,
    *,
    output_boundary: OutputBoundary | None = None,
) -> tuple[str, str]:
    script = getattr(data, "m_Script", b"")
    if isinstance(script, str):
        out_path = out_path.with_name(f"{out_path.name}.txt")
        write_export_file_if_changed(
            out_path,
            lambda temp_path: temp_path.write_text(script, encoding="utf-8", errors="replace"),
            output_boundary=output_boundary,
        )
        return str(out_path), ""
    if isinstance(script, bytes):
        try:
            text = script.decode("utf-8")
            out_path = out_path.with_name(f"{out_path.name}.txt")
            write_export_file_if_changed(
                out_path,
                lambda temp_path: temp_path.write_text(text, encoding="utf-8", errors="replace"),
                output_boundary=output_boundary,
            )
        except UnicodeDecodeError:
            out_path = out_path.with_name(f"{out_path.name}.bin")
            write_export_file_if_changed(
                out_path,
                lambda temp_path: temp_path.write_bytes(script),
                output_boundary=output_boundary,
            )
        return str(out_path), ""
    out_path = out_path.with_name(f"{out_path.name}.json")
    json_text = json.dumps(to_jsonable(script), ensure_ascii=False, indent=2)
    write_export_file_if_changed(
        out_path,
        lambda temp_path: temp_path.write_text(json_text, encoding="utf-8"),
        output_boundary=output_boundary,
    )
    return str(out_path), ""


def collect_container_paths(env: Any) -> dict[int, str]:
    mapping: dict[int, str] = {}
    try:
        for path, ptr in env.container.items():
            pid = getattr(ptr, "m_PathID", None)
            if isinstance(pid, int):
                mapping[pid] = path
    except Exception:
        pass
    return mapping


def collect_container_paths_including_dependencies(env: Any) -> dict[int, str]:
    """Mirror a bundle-wide container map when sibling serialized nodes are workers' dependencies."""
    mapping: dict[int, str] = {}
    for serialized_file in getattr(env, "cabs", {}).values():
        try:
            items = serialized_file.container.items()
        except Exception:
            continue
        for path, ptr in items:
            pid = getattr(ptr, "m_PathID", None)
            if isinstance(pid, int):
                mapping[pid] = path
    return mapping


def find_unity_data_dirs(
    source_dir: Path,
    authorized_files: list[Path] | None = None,
) -> list[Path]:
    if authorized_files is None:
        boundary = SourceBoundary(source_dir).initialize()
        authorized_files = boundary.discover_files()
        with boundary.acquire_snapshot(authorized_files) as snapshot:
            snapshot.verify_all()
    data_dirs = {
        ancestor
        for path in authorized_files
        for ancestor in path.parents
        if ancestor != source_dir and ancestor.name.lower().endswith("_data")
    }
    if source_dir.name.endswith("_Data"):
        data_dirs.add(source_dir)
    return sorted(data_dirs, key=lambda path: str(path).lower())


def has_unity_bundle_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(8).startswith(UNITY_BUNDLE_MAGICS)
    except OSError:
        return False


def is_in_unity_data_dir(path: Path, source_dir: Path) -> bool:
    try:
        relative_parts = path.relative_to(source_dir).parts[:-1]
    except ValueError:
        relative_parts = path.parts[:-1]
    return source_dir.name.lower().endswith("_data") or any(
        part.lower().endswith("_data") for part in relative_parts
    )


def looks_like_unity_container(path: Path, source_dir: Path) -> bool:
    if not path.is_file():
        return False
    lower_name = path.name.lower()
    lower_suffix = path.suffix.lower()
    if lower_suffix in UNITY_EXTERNAL_PAYLOAD_SUFFIXES:
        return False
    if lower_suffix in UNITY_CONTAINER_SUFFIXES:
        return True
    if lower_name in UNITY_CONTAINER_NAMES or lower_name.startswith("sharedassets"):
        return True
    if UNITY_LEVEL_NAME.fullmatch(lower_name) and is_in_unity_data_dir(path, source_dir):
        return True
    try:
        parts = {part.lower() for part in path.relative_to(source_dir).parts}
    except ValueError:
        parts = {part.lower() for part in path.parts}
    if (
        "assetbundles" in parts
        and lower_name.startswith("resources_")
        and lower_suffix not in {".manifest", ".json", ".txt"}
    ):
        return True
    if {"streamingassets", "aa"}.issubset(parts) and lower_suffix not in {".json", ".hash"}:
        return True
    return has_unity_bundle_magic(path)


def discover_unity_sources(
    source_dir: Path,
    *,
    source_boundary: SourceBoundary | None = None,
    authorized_files: list[Path] | None = None,
) -> list[Path]:
    boundary = source_boundary or SourceBoundary(source_dir).initialize()
    owned_snapshot: AuthorizedSourceSnapshot | None = None
    files = authorized_files
    if files is None:
        files = boundary.discover_files()
        owned_snapshot = boundary.acquire_snapshot(files)
    try:
        sources = [
            boundary.require_authorized_file(path)
            for path in files
            if looks_like_unity_container(path, boundary.root)
        ]
        if owned_snapshot is not None:
            owned_snapshot.verify_all()
    finally:
        if owned_snapshot is not None:
            owned_snapshot.close()
    return sorted(
        set(sources),
        key=lambda path: path.relative_to(boundary.root).as_posix().casefold(),
    )


def discover_unity_external_payloads(
    source_dir: Path,
    *,
    source_boundary: SourceBoundary | None = None,
    authorized_files: list[Path] | None = None,
) -> list[Path]:
    boundary = source_boundary or SourceBoundary(source_dir).initialize()
    owned_snapshot: AuthorizedSourceSnapshot | None = None
    files = authorized_files
    if files is None:
        files = boundary.discover_files()
        owned_snapshot = boundary.acquire_snapshot(files)
    try:
        payloads = [
            boundary.require_authorized_file(path)
            for path in files
            if path.suffix.lower() in UNITY_EXTERNAL_PAYLOAD_SUFFIXES
        ]
        if owned_snapshot is not None:
            owned_snapshot.verify_all()
    finally:
        if owned_snapshot is not None:
            owned_snapshot.close()
    return sorted(payloads, key=lambda path: path.relative_to(boundary.root).as_posix().casefold())


def discover_addressables_catalogs(
    source_dir: Path,
    *,
    source_boundary: SourceBoundary | None = None,
    authorized_files: list[Path] | None = None,
) -> list[Path]:
    boundary = source_boundary or SourceBoundary(source_dir).initialize()
    owned_snapshot: AuthorizedSourceSnapshot | None = None
    files = authorized_files
    if files is None:
        files = boundary.discover_files()
        owned_snapshot = boundary.acquire_snapshot(files)
    try:
        catalogs = [
            boundary.require_authorized_file(path)
            for path in files
            if path.name.casefold().startswith("catalog")
            and path.suffix.casefold() == ".json"
        ]
        if owned_snapshot is not None:
            owned_snapshot.verify_all()
    finally:
        if owned_snapshot is not None:
            owned_snapshot.close()
    return sorted(catalogs, key=lambda path: path.relative_to(boundary.root).as_posix().casefold())


def parse_addressables_catalogs(
    source_dir: Path,
    export_root: Path,
    *,
    output_boundary: OutputBoundary | None = None,
    catalogs: list[Path] | None = None,
) -> dict[str, Any]:
    owned_snapshot: AuthorizedSourceSnapshot | None = None
    if catalogs is None:
        source_boundary = SourceBoundary(source_dir).initialize()
        authorized_files = source_boundary.discover_files()
        owned_snapshot = source_boundary.acquire_snapshot(authorized_files)
        catalogs = discover_addressables_catalogs(
            source_dir,
            source_boundary=source_boundary,
            authorized_files=authorized_files,
        )
    try:
        boundary = output_boundary or OutputBoundary(export_root).initialize()
        out_dir = export_root / "scene_catalog"
        boundary.ensure_directory(out_dir)
        if not catalogs:
            return {"exists": False, "scene_count": 0, "catalog_count": 0}
        scene_paths: list[str] = []
        asset_paths: list[str] = []
        catalog_rows: list[dict[str, str]] = []
        for catalog in catalogs:
            try:
                data = json.loads(catalog.read_text(encoding="utf-8"))
            except Exception as exc:
                catalog_rows.append({"catalog": rel(catalog, source_dir), "status": "read_error", "message": f"{type(exc).__name__}: {exc}"})
                continue
            internal_ids = data.get("m_InternalIds", [])
            for item in internal_ids:
                if not isinstance(item, str):
                    continue
                if item.startswith("Assets/"):
                    asset_paths.append(item)
                    if item.endswith(".unity") or "/Scenes/" in item:
                        scene_paths.append(item)
            catalog_rows.append({"catalog": rel(catalog, source_dir), "status": "parsed", "message": str(len(internal_ids))})
        write_export_file_if_changed(
            out_dir / "scene_paths.txt",
            lambda temp_path: temp_path.write_text("\n".join(scene_paths), encoding="utf-8"),
            output_boundary=boundary,
        )
        write_export_file_if_changed(
            out_dir / "asset_paths.txt",
            lambda temp_path: temp_path.write_text("\n".join(asset_paths), encoding="utf-8"),
            output_boundary=boundary,
        )

        def write_catalog_csv(temp_path: Path) -> None:
            with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=["catalog", "status", "message"])
                writer.writeheader()
                writer.writerows(catalog_rows)

        write_export_file_if_changed(
            out_dir / "catalogs.csv",
            write_catalog_csv,
            output_boundary=boundary,
        )
        if owned_snapshot is not None:
            owned_snapshot.verify_all()
        return {
            "exists": True,
            "catalog_count": len(catalogs),
            "scene_count": len(scene_paths),
            "asset_path_count": len(asset_paths),
            "scene_paths_file": "scene_catalog/scene_paths.txt",
            "asset_paths_file": "scene_catalog/asset_paths.txt",
            "catalogs_file": "scene_catalog/catalogs.csv",
        }
    finally:
        if owned_snapshot is not None:
            owned_snapshot.close()


def checkpoint_identity(
    source_dir: Path,
    export_root: Path,
    audio_mode: str,
    source_entries: list[dict[str, Any]],
    external_entries: list[dict[str, Any]],
    catalog_entries: list[dict[str, Any]],
    *,
    protocol_version: int = RESUME_PROTOCOL_VERSION,
) -> dict[str, Any]:
    identity = {
        "source_root": normalize_root(source_dir),
        "export_root": normalize_root(export_root),
        "options": {"audio_mode": audio_mode},
        "source_count": len(source_entries),
        "source_list_fingerprint": manifest_fingerprint(source_entries),
        "external_payload_fingerprint": manifest_fingerprint(external_entries),
        "addressables_catalog_fingerprint": manifest_fingerprint(catalog_entries),
    }
    if protocol_version == LEGACY_RESUME_PROTOCOL_VERSION:
        return identity
    if protocol_version != RESUME_PROTOCOL_VERSION:
        raise ResumeStateError(
            f"Unsupported UnityPy resume protocol version: {protocol_version!r}."
        )
    identity.update(
        {
            "asset_identity_scheme": ASSET_IDENTITY_SCHEME,
            "asset_identity_version": ASSET_IDENTITY_VERSION,
            "large_bundle_strategy_selector": "block_or_node_v1",
        }
    )
    return identity


def read_checkpoint_protocol(checkpoint_path: Path) -> int | None:
    """Read only the protocol selector; never mutate checkpoint state."""
    if not checkpoint_path.exists():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        protocol_version = checkpoint["protocol_version"]
        if isinstance(protocol_version, bool) or not isinstance(protocol_version, int):
            raise TypeError("protocol_version must be an integer")
    except Exception as exc:
        raise ResumeStateError(
            f"Cannot read UnityPy resume checkpoint {checkpoint_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if protocol_version not in SUPPORTED_RESUME_PROTOCOL_VERSIONS:
        raise ResumeStateError(
            "UnityPy resume checkpoint protocol mismatch: "
            f"supported={sorted(SUPPORTED_RESUME_PROTOCOL_VERSIONS)}, "
            f"found={protocol_version!r}. Existing outputs were not modified."
        )
    return protocol_version


def validate_reprocess_source_requests(
    requested_sources: list[str] | None,
    discovered_source_labels: list[str],
) -> list[str]:
    """Return stable, de-duplicated exact source labels or fail closed.

    This validation must run before loading a resume checkpoint because loading a
    matching checkpoint records a new run. Reprocessing is a run action, not part
    of protocol-v1 identity, so only labels discovered for the current source
    manifest are accepted.
    """
    if requested_sources is None:
        return []
    invalid_values = [
        value
        for value in requested_sources
        if not isinstance(value, str) or not value.strip()
    ]
    if invalid_values:
        raise ResumeStateError(
            "Every requested reprocess source must be a non-empty exact relative source label."
        )
    requested_set = set(requested_sources)
    discovered_set = set(discovered_source_labels)
    unknown_sources = sorted(requested_set - discovered_set)
    if unknown_sources:
        raise ResumeStateError(
            "Unknown reprocess source label(s); checkpoint and fragments were not modified: "
            + ", ".join(repr(value) for value in unknown_sources)
        )
    return [
        source_label
        for source_label in discovered_source_labels
        if source_label in requested_set
    ]


def load_or_create_checkpoint(
    checkpoint_path: Path,
    identity: dict[str, Any],
    *,
    protocol_version: int = RESUME_PROTOCOL_VERSION,
    output_boundary: OutputBoundary | None = None,
) -> tuple[dict[str, Any], bool]:
    asset_identity_scheme = asset_identity_scheme_for_protocol(protocol_version)
    if protocol_version == RESUME_PROTOCOL_VERSION:
        if (
            identity.get("asset_identity_scheme") != ASSET_IDENTITY_SCHEME
            or identity.get("asset_identity_version") != ASSET_IDENTITY_VERSION
        ):
            raise ResumeStateError(
                "Protocol 2 checkpoint identity must persist the current asset "
                "identity scheme and version."
            )
    elif "asset_identity_scheme" in identity or "asset_identity_version" in identity:
        raise ResumeStateError(
            "Protocol 1 identity must retain its exact historical field set."
        )
    if not checkpoint_path.exists():
        checkpoint = {
            "protocol_version": protocol_version,
            "identity": identity,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "status": "running",
            "resume_count": 0,
            "sources": {},
        }
        if protocol_version == RESUME_PROTOCOL_VERSION:
            checkpoint["asset_identity"] = {
                "scheme": asset_identity_scheme,
                "version": ASSET_IDENTITY_VERSION,
            }
        update_peak_memory(checkpoint)
        atomic_write_json(
            checkpoint_path,
            checkpoint,
            output_boundary=output_boundary,
        )
        return checkpoint, False

    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResumeStateError(
            f"Cannot read UnityPy resume checkpoint {checkpoint_path}: {type(exc).__name__}: {exc}"
        ) from exc
    if checkpoint.get("protocol_version") != protocol_version:
        raise ResumeStateError(
            "UnityPy resume checkpoint protocol mismatch: "
            f"expected {protocol_version}, found {checkpoint.get('protocol_version')!r}. "
            "Use a different output directory or explicitly archive the incompatible resume state."
        )
    if protocol_version == RESUME_PROTOCOL_VERSION and checkpoint.get("asset_identity") != {
        "scheme": ASSET_IDENTITY_SCHEME,
        "version": ASSET_IDENTITY_VERSION,
    }:
        raise ResumeStateError(
            "Protocol 2 checkpoint asset identity scheme/version is missing or mismatched. "
            "Existing outputs were not modified."
        )
    previous_identity = checkpoint.get("identity")
    if (
        protocol_version == RESUME_PROTOCOL_VERSION
        and isinstance(previous_identity, dict)
        and "large_bundle_strategy_selector" not in previous_identity
        and identity.get("large_bundle_strategy_selector") == "block_or_node_v1"
    ):
        # Protocol 2 checkpoints created before node-size routing can migrate in
        # place: completed fragment identity is unchanged, while only pending or
        # retried sources use the expanded bounded-memory selector.
        migrated_identity = dict(previous_identity)
        migrated_identity["large_bundle_strategy_selector"] = "block_or_node_v1"
        checkpoint["identity"] = migrated_identity
        previous_identity = migrated_identity
    identity_keys = set(identity)
    previous_keys = set(previous_identity) if isinstance(previous_identity, dict) else set()
    mismatch_fields = sorted(
        key
        for key in identity_keys | previous_keys
        if not isinstance(previous_identity, dict)
        or key not in identity
        or previous_identity.get(key) != identity.get(key)
    )
    if mismatch_fields:
        raise ResumeStateError(
            "UnityPy resume checkpoint identity mismatch for: "
            f"{', '.join(mismatch_fields)}. Existing outputs were not cleared or modified."
        )
    if not isinstance(checkpoint.get("sources"), dict):
        raise ResumeStateError("UnityPy resume checkpoint has an invalid sources map.")
    checkpoint["resume_count"] = int(checkpoint.get("resume_count", 0)) + 1
    checkpoint["updated_at"] = utc_now()
    checkpoint["status"] = "running"
    update_peak_memory(checkpoint)
    atomic_write_json(
        checkpoint_path,
        checkpoint,
        output_boundary=output_boundary,
    )
    return checkpoint, True


def source_fragment_name(source_index: int, source_rel: str) -> str:
    source_hash = hashlib.sha256(source_rel.encode("utf-8")).hexdigest()[:16]
    return f"{source_index:06d}_{source_hash}.csv"


def validate_fragment_identity_metadata(
    record: dict[str, Any],
    protocol_version: int,
) -> None:
    """Prevent checkpoint records from silently mixing v1/v2 fragments."""
    scheme = record.get("asset_identity_scheme")
    version = record.get("asset_identity_version")
    if protocol_version == RESUME_PROTOCOL_VERSION:
        if scheme != ASSET_IDENTITY_SCHEME or version != ASSET_IDENTITY_VERSION:
            raise ResumeStateError(
                "Protocol 2 source fragment is missing its persisted asset identity "
                "scheme/version; old and new fragments will not be mixed."
            )
        return
    if protocol_version == LEGACY_RESUME_PROTOCOL_VERSION:
        if scheme not in {None, LEGACY_ASSET_IDENTITY_SCHEME} or version not in {None, 1}:
            raise ResumeStateError(
                "Protocol 1 source record contains incompatible asset identity "
                "metadata; old and new fragments will not be mixed."
            )
        return
    raise ResumeStateError(
        f"Unsupported UnityPy resume protocol version: {protocol_version!r}."
    )


def validate_completed_fragment(
    export_root: Path,
    record: dict[str, Any],
    *,
    output_boundary: OutputBoundary | None = None,
) -> Path:
    fragment_rel = record.get("fragment")
    expected_hash = record.get("fragment_sha256")
    if not isinstance(fragment_rel, str) or not fragment_rel or not isinstance(expected_hash, str):
        raise ResumeStateError("Completed UnityPy source has incomplete fragment metadata.")
    fragment_root = (export_root / RESUME_STATE_DIR_NAME / "fragments").resolve()
    fragment_path = (export_root / Path(fragment_rel)).resolve()
    if not fragment_path.is_relative_to(fragment_root):
        raise ResumeStateError(
            f"Completed UnityPy source fragment escapes the resume directory: {fragment_rel}"
        )
    if output_boundary is not None:
        output_boundary.prepare_file(fragment_path)
    if not fragment_path.is_file():
        raise ResumeStateError(f"Completed UnityPy source fragment is missing: {fragment_path}")
    actual_hash = sha256_file(fragment_path)
    if actual_hash != expected_hash:
        raise ResumeStateError(
            f"Completed UnityPy source fragment hash mismatch: {fragment_path}. "
            "Existing outputs were not cleared or modified."
        )
    return fragment_path


def _legacy_uniqueness_digest(namespace: str, value: str) -> bytes:
    """Return a compact fail-closed key for multi-million-row legacy checks."""
    return hashlib.sha256(
        namespace.encode("ascii") + b"\0" + value.casefold().encode("utf-8")
    ).digest()


def _validate_legacy_protocol_one_no_work_body(
    export_root: Path,
    checkpoint_path: Path,
    source_labels: list[str],
    expected_identity: dict[str, Any],
    requested_reprocess_sources: list[str],
) -> dict[str, Any]:
    """Allow protocol 1 only as a fully validated, collision-free no-work run.

    Legacy asset IDs cannot be made safe for new objects without changing their
    historical output paths. Therefore every discovered source must already be
    completed, every canonical fragment must hash-match, and all asset IDs plus
    every nonempty physical export path must be globally unique under Windows
    case-insensitive semantics. This function is read-only and must run before
    resume_count, attempts, fragments, index, resources, or summary can change.
    """
    if requested_reprocess_sources:
        raise ResumeStateError(
            "Protocol 1 is restricted to validated pure no-work resumes; targeted "
            "reprocess requires a fresh protocol-2 output directory. Existing "
            "checkpoint and outputs were not modified."
        )
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResumeStateError(
            f"Cannot read legacy UnityPy checkpoint before no-work gate: {exc}"
        ) from exc
    if checkpoint.get("protocol_version") != LEGACY_RESUME_PROTOCOL_VERSION:
        raise ResumeStateError("Legacy no-work gate received a non-protocol-1 checkpoint.")
    if checkpoint.get("identity") != expected_identity:
        raise ResumeStateError(
            "Protocol-1 checkpoint identity differs from current discovery; "
            "existing outputs were not modified."
        )
    records = checkpoint.get("sources")
    if not isinstance(records, dict) or list(records) != source_labels:
        raise ResumeStateError(
            "Protocol 1 is restricted to a complete checkpoint whose ordered source "
            "set exactly matches current discovery. Use a fresh protocol-2 output "
            "directory for pending/retry work."
        )

    fragment_root = lexical_absolute(
        export_root / RESUME_STATE_DIR_NAME / "fragments"
    )
    asset_keys: set[bytes] = set()
    export_path_keys: set[bytes] = set()
    total_rows = 0
    claimed_export_paths = 0
    for source_index, source_label in enumerate(source_labels):
        record = records.get(source_label)
        if not isinstance(record, dict) or record.get("status") != "completed":
            raise ResumeStateError(
                "Protocol 1 is restricted to validated pure no-work: every source "
                "must already be completed. Use a fresh protocol-2 output directory "
                "for pending/retry work."
            )
        validate_fragment_identity_metadata(
            record,
            LEGACY_RESUME_PROTOCOL_VERSION,
        )
        expected_fragment = (
            f"{RESUME_STATE_DIR_NAME}/fragments/"
            f"{source_fragment_name(source_index, source_label)}"
        )
        if record.get("fragment") != expected_fragment:
            raise ResumeStateError(
                f"Protocol-1 fragment path is not canonical for {source_label!r}."
            )
        fragment_path = lexical_absolute(export_root / expected_fragment)
        try:
            fragment_path.relative_to(fragment_root)
        except ValueError as exc:
            raise ResumeStateError(
                f"Protocol-1 fragment escapes its canonical root: {expected_fragment!r}."
            ) from exc
        if (
            is_reparse_path(fragment_path)
            or not fragment_path.is_file()
            or not isinstance(record.get("fragment_sha256"), str)
        ):
            raise ResumeStateError(
                f"Protocol-1 completed fragment is missing or unsafe: {fragment_path}."
            )
        if sha256_file(fragment_path) != record["fragment_sha256"]:
            raise ResumeStateError(
                f"Protocol-1 completed fragment hash mismatch: {fragment_path}."
            )

        source_rows = 0
        with fragment_path.open("r", encoding="utf-8", newline="") as handle:
            for row_number, values in enumerate(csv.reader(handle), 1):
                if len(values) != len(INDEX_FIELDS):
                    raise ResumeStateError(
                        "Protocol-1 fragment row has an invalid field count: "
                        f"{fragment_path}:{row_number}."
                    )
                row = dict(zip(INDEX_FIELDS, values, strict=True))
                if row["source_file"] != source_label or not row["asset_id"]:
                    raise ResumeStateError(
                        "Protocol-1 fragment source/asset identity mismatch: "
                        f"{fragment_path}:{row_number}."
                    )
                asset_key = _legacy_uniqueness_digest("asset_id", row["asset_id"])
                if asset_key in asset_keys:
                    raise ResumeStateError(
                        "Protocol-1 fragments contain a global asset_id collision; "
                        "pure no-work was rejected before persistent state changed."
                    )
                asset_keys.add(asset_key)
                for export_value in filter(None, row["export_path"].split(";")):
                    normalized = canonical_physical_export_path(export_value)
                    export_key = _legacy_uniqueness_digest(
                        "export_path",
                        normalized,
                    )
                    if export_key in export_path_keys:
                        raise ResumeStateError(
                            "Protocol-1 fragments contain a global export_path collision; "
                            "pure no-work was rejected before persistent state changed."
                        )
                    export_path_keys.add(export_key)
                    claimed_export_paths += 1
                source_rows += 1
                total_rows += 1
        if source_rows != int(record.get("row_count", -1)):
            raise ResumeStateError(
                f"Protocol-1 fragment row count mismatch: {source_label!r}."
            )
    return {
        "mode": "validated_pure_no_work_only",
        "completed_source_count": len(source_labels),
        "fragment_count": len(source_labels),
        "globally_unique_asset_id_count": len(asset_keys),
        "globally_unique_export_path_count": len(export_path_keys),
        "row_count": total_rows,
        "claimed_export_path_count": claimed_export_paths,
        "windows_case_insensitive_uniqueness": True,
    }


def validate_legacy_protocol_one_no_work(
    export_root: Path,
    checkpoint_path: Path,
    source_labels: list[str],
    expected_identity: dict[str, Any],
    requested_reprocess_sources: list[str],
) -> LegacyNoWorkPreflight:
    """Bind protocol-1 preflight and its no-work commit to leased file identities."""
    if requested_reprocess_sources:
        raise ResumeStateError(
            "Protocol 1 is restricted to validated pure no-work resumes; targeted "
            "reprocess requires a fresh protocol-2 output directory. Existing "
            "checkpoint and outputs were not modified."
        )
    checkpoint_lease = acquire_stable_read_lease(checkpoint_path)
    fragment_leases: list[StableReadLease] = []
    try:
        checkpoint_bytes = checkpoint_path.read_bytes()
        checkpoint = json.loads(checkpoint_bytes.decode("utf-8"))
        records = checkpoint.get("sources")
        if not isinstance(records, dict) or list(records) != source_labels:
            raise ResumeStateError(
                "Protocol 1 is restricted to a complete checkpoint whose ordered source "
                "set exactly matches current discovery. Use a fresh protocol-2 output "
                "directory for pending/retry work."
            )
        for source_index, source_label in enumerate(source_labels):
            record = records.get(source_label)
            if not isinstance(record, dict) or record.get("status") != "completed":
                raise ResumeStateError(
                    "Protocol 1 is restricted to validated pure no-work: every source "
                    "must already be completed. Use a fresh protocol-2 output directory "
                    "for pending/retry work."
                )
            fragment_path = lexical_absolute(
                export_root
                / RESUME_STATE_DIR_NAME
                / "fragments"
                / source_fragment_name(source_index, source_label)
            )
            fragment_leases.append(acquire_stable_read_lease(fragment_path))

        evidence = _validate_legacy_protocol_one_no_work_body(
            export_root,
            checkpoint_path,
            source_labels,
            expected_identity,
            requested_reprocess_sources,
        )
        verify_stable_read_lease(checkpoint_lease)
        for lease in fragment_leases:
            verify_stable_read_lease(lease)
        fragment_binding = hashlib.sha256()
        for lease in fragment_leases:
            fragment_binding.update(lease.path.as_posix().encode("utf-8"))
            fragment_binding.update(b"\0")
            fragment_binding.update(repr(lease.identity).encode("ascii"))
            fragment_binding.update(b"\0")
        snapshot_binding = {
            "checkpoint_sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
            "checkpoint_identity": list(checkpoint_lease.identity),
            "fragment_identity_set_sha256": fragment_binding.hexdigest(),
            "leased_fragment_count": len(fragment_leases),
            "windows_write_delete_denied_while_committing": os.name == "nt",
            "posix_descriptor_and_advisory_lock": os.name != "nt",
        }
        checkpoint_generation = ExpectedFileGeneration(
            True,
            identity=checkpoint_lease.identity,
            byte_size=len(checkpoint_bytes),
            sha256=snapshot_binding["checkpoint_sha256"],
        )
        return LegacyNoWorkPreflight(
            evidence,
            checkpoint,
            checkpoint_lease,
            fragment_leases,
            snapshot_binding,
            checkpoint_generation,
        )
    except BaseException:
        checkpoint_lease.close()
        for lease in reversed(fragment_leases):
            lease.close()
        raise


def expected_generation_evidence(
    path: Path,
    generation: ExpectedFileGeneration,
    export_root: Path,
) -> dict[str, Any]:
    return {
        "path": rel(path, export_root),
        "exists": generation.exists,
        "identity": list(generation.identity) if generation.identity is not None else None,
        "byte_size": generation.byte_size,
        "sha256": generation.sha256,
    }


def _canonical_history_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def protocol_one_history_anchor_from_link(link: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
        "head_transaction_id": link["head_transaction_id"],
        "count": link["count"],
        "chain_sha256": link["chain_sha256"],
    }


def protocol_one_history_link(
    transaction_id: str,
    previous_anchor: Mapping[str, Any] | None,
    previous_sidecar_sha256: str | None,
) -> dict[str, Any]:
    """Create a non-cyclic append-only link for one immutable sidecar.

    The link digest excludes the planned control payload digests. Controls can
    therefore anchor this link before the sidecar binds their complete bytes,
    retaining the v2 pre-commit plan without a hash fixed-point.
    """
    if previous_anchor is None:
        previous_head: str | None = None
        previous_count = 0
        previous_chain = PROTOCOL_ONE_HISTORY_GENESIS_SHA256
        if previous_sidecar_sha256 is not None:
            raise ResumeStateError("Protocol-1 genesis cannot name a previous sidecar.")
    else:
        previous_head = previous_anchor.get("head_transaction_id")
        previous_count = previous_anchor.get("count")
        previous_chain = previous_anchor.get("chain_sha256")
        if (
            previous_anchor.get("schema") != PROTOCOL_ONE_HISTORY_SCHEMA
            or re.fullmatch(r"[0-9a-f]{32}", str(previous_head)) is None
            or not isinstance(previous_count, int)
            or previous_count < 1
            or re.fullmatch(r"[0-9a-f]{64}", str(previous_chain)) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(previous_sidecar_sha256)) is None
        ):
            raise ResumeStateError("Protocol-1 previous history anchor is invalid.")
    digest_input = {
        "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
        "previous_head_transaction_id": previous_head,
        "previous_sidecar_sha256": previous_sidecar_sha256,
        "previous_count": previous_count,
        "previous_chain_sha256": previous_chain,
        "head_transaction_id": transaction_id,
        "count": previous_count + 1,
    }
    return {
        **digest_input,
        "chain_sha256": hashlib.sha256(
            _canonical_history_bytes(digest_input)
        ).hexdigest(),
    }


def protocol_one_legacy_v2_anchor(
    transaction_id: str,
    sidecar_sha256: str,
) -> dict[str, Any]:
    """Derive the sole allowed legacy boundary: one control-anchored v2 record."""
    digest_input = {
        "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
        "legacy_boundary": PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA,
        "head_transaction_id": transaction_id,
        "sidecar_sha256": sidecar_sha256,
        "count": 1,
    }
    return {
        "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
        "head_transaction_id": transaction_id,
        "count": 1,
        "chain_sha256": hashlib.sha256(
            _canonical_history_bytes(digest_input)
        ).hexdigest(),
    }


def protocol_one_transaction_reference(
    transaction_id: str,
    status_path: Path,
    export_root: Path,
    history_link: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "status_path": rel(status_path, export_root),
        "status_sidecar_authoritative": True,
        "history": protocol_one_history_anchor_from_link(history_link),
    }


def _generation_from_evidence(item: Mapping[str, Any]) -> ExpectedFileGeneration:
    exists = item.get("exists")
    identity = item.get("identity")
    byte_size = item.get("byte_size")
    digest = item.get("sha256")
    if exists is False:
        if identity is not None or byte_size is not None or digest is not None:
            raise ResumeStateError("Absent protocol-1 target evidence carries metadata.")
        return ExpectedFileGeneration(False)
    if (
        exists is not True
        or not isinstance(identity, list)
        or not identity
        or not all(isinstance(value, int) for value in identity)
        or not isinstance(byte_size, int)
        or byte_size < 0
        or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
    ):
        raise ResumeStateError("Protocol-1 target generation evidence is invalid.")
    return ExpectedFileGeneration(
        True,
        identity=tuple(identity),
        byte_size=byte_size,
        sha256=str(digest),
    )


def _validate_protocol_one_target_evidence_set(
    evidence: Any,
    *,
    label: str,
) -> dict[str, ExpectedFileGeneration]:
    expected_paths = {
        "summary.json",
        f"{RESUME_STATE_DIR_NAME}/checkpoint.json",
    }
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise ResumeStateError(f"Protocol-1 {label} target set is invalid.")
    result: dict[str, ExpectedFileGeneration] = {}
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "exists",
            "identity",
            "byte_size",
            "sha256",
        }:
            raise ResumeStateError(f"Protocol-1 {label} target evidence is invalid.")
        relative = item.get("path")
        if relative not in expected_paths or relative in result:
            raise ResumeStateError(f"Protocol-1 {label} target paths are invalid.")
        result[str(relative)] = _generation_from_evidence(item)
    if set(result) != expected_paths:
        raise ResumeStateError(f"Protocol-1 {label} target paths are incomplete.")
    return result


def _validate_protocol_one_plan_set(plans: Any) -> dict[str, dict[str, Any]]:
    expected_paths = {
        "summary.json",
        f"{RESUME_STATE_DIR_NAME}/checkpoint.json",
    }
    if not isinstance(plans, list) or len(plans) != 2:
        raise ResumeStateError("Protocol-1 transaction planned target set is invalid.")
    result: dict[str, dict[str, Any]] = {}
    for item in plans:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise ResumeStateError("Protocol-1 transaction planned target is invalid.")
        relative = item.get("path")
        if (
            relative not in expected_paths
            or relative in result
            or not isinstance(item.get("byte_size"), int)
            or int(item["byte_size"]) < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))) is None
        ):
            raise ResumeStateError("Protocol-1 transaction planned target is invalid.")
        result[str(relative)] = dict(item)
    if set(result) != expected_paths:
        raise ResumeStateError("Protocol-1 transaction planned target paths are incomplete.")
    return result


def _validate_protocol_one_v3_record(
    record: Any,
    transaction_id: str,
) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "transaction_id",
        "transaction_state",
        "protocol_version",
        "recorded_at",
        "history",
        "previous_target_generations",
        "planned_target_contents",
        "planned_control_anchor",
        "cleanup_ownership",
    }:
        raise ResumeStateError("Protocol-1 v3 transaction schema is not exact.")
    if (
        record.get("schema") != PROTOCOL_ONE_TRANSACTION_SCHEMA
        or record.get("transaction_id") != transaction_id
        or record.get("transaction_state") != PROTOCOL_ONE_TRANSACTION_STATE
        or record.get("protocol_version") != LEGACY_RESUME_PROTOCOL_VERSION
        or not isinstance(record.get("recorded_at"), str)
        or not record["recorded_at"]
    ):
        raise ResumeStateError("Protocol-1 v3 transaction identity/state is invalid.")
    history = record.get("history")
    if not isinstance(history, dict) or set(history) != {
        "schema",
        "previous_head_transaction_id",
        "previous_sidecar_sha256",
        "previous_count",
        "previous_chain_sha256",
        "head_transaction_id",
        "count",
        "chain_sha256",
    }:
        raise ResumeStateError("Protocol-1 transaction history link is not exact.")
    previous_count = history.get("previous_count")
    previous_head = history.get("previous_head_transaction_id")
    previous_digest = history.get("previous_sidecar_sha256")
    previous_chain = history.get("previous_chain_sha256")
    if (
        history.get("schema") != PROTOCOL_ONE_HISTORY_SCHEMA
        or history.get("head_transaction_id") != transaction_id
        or not isinstance(previous_count, int)
        or previous_count < 0
        or history.get("count") != previous_count + 1
        or re.fullmatch(r"[0-9a-f]{64}", str(previous_chain)) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(history.get("chain_sha256"))) is None
    ):
        raise ResumeStateError("Protocol-1 transaction history link is invalid.")
    if previous_count == 0:
        if (
            previous_head is not None
            or previous_digest is not None
            or previous_chain != PROTOCOL_ONE_HISTORY_GENESIS_SHA256
        ):
            raise ResumeStateError("Protocol-1 transaction genesis link is invalid.")
    elif (
        re.fullmatch(r"[0-9a-f]{32}", str(previous_head)) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(previous_digest)) is None
    ):
        raise ResumeStateError("Protocol-1 transaction predecessor link is invalid.")
    digest_input = {key: history[key] for key in history if key != "chain_sha256"}
    expected_chain = hashlib.sha256(_canonical_history_bytes(digest_input)).hexdigest()
    if history.get("chain_sha256") != expected_chain:
        raise ResumeStateError("Protocol-1 transaction history chain digest is invalid.")
    _validate_protocol_one_target_evidence_set(
        record.get("previous_target_generations"),
        label="previous",
    )
    _validate_protocol_one_plan_set(record.get("planned_target_contents"))
    planned_anchor = record.get("planned_control_anchor")
    if (
        not isinstance(planned_anchor, dict)
        or set(planned_anchor) != {"summary", "checkpoint"}
        or planned_anchor.get("summary") != planned_anchor.get("checkpoint")
        or not isinstance(planned_anchor.get("summary"), dict)
    ):
        raise ResumeStateError("Protocol-1 planned control anchor is invalid.")
    reference = planned_anchor["summary"]
    if (
        reference.get("transaction_id") != transaction_id
        or reference.get("status_sidecar_authoritative") is not True
        or reference.get("history") != protocol_one_history_anchor_from_link(history)
        or reference.get("status_path")
        != f"{RESUME_STATE_DIR_NAME}/group_transactions/{transaction_id}.json"
    ):
        raise ResumeStateError("Protocol-1 planned control anchor does not match its history link.")
    ownership = record.get("cleanup_ownership")
    if (
        not isinstance(ownership, dict)
        or set(ownership) != {"token", "exact_filename_grammar"}
        or ownership.get("token") != transaction_id
        or not isinstance(ownership.get("exact_filename_grammar"), str)
    ):
        raise ResumeStateError("Protocol-1 transaction cleanup ownership is invalid.")
    return dict(history)


def write_group_transaction_status_no_replace(
    status_path: Path,
    evidence: dict[str, Any],
    *,
    output_boundary: OutputBoundary,
) -> StableReadLease:
    """Publish one immutable pre-commit record without overwriting.

    The record is published before any canonical control file can reference it.
    A wrapper error after the no-replace rename is reconciled from the exact
    destination bytes, so a completed syscall is not misreported as absence.
    """
    status_path = output_boundary.prepare_file(status_path)
    payload = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with output_boundary.stable_directory(status_path.parent):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".ga-group-status-{evidence.get('transaction_id', 'invalid')}-",
            suffix=".partial",
            dir=str(status_path.parent),
        )
        temporary = Path(temporary_name)
        temporary_generation: ExpectedFileGeneration | None = None
        publication_error: BaseException | None = None
        cleanup_errors: list[str] = []
        publication_succeeded = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                flush_and_sync(handle)
            temporary_generation = capture_expected_file_generation(temporary)
            try:
                output_boundary.rename_no_replace(temporary, status_path)
            except BaseException as exc:
                if (
                    not os.path.lexists(temporary)
                    and status_path.is_file()
                    and not is_reparse_path(status_path)
                    and status_path.read_bytes() == payload
                ):
                    publication_succeeded = True
                else:
                    publication_error = exc
            else:
                publication_succeeded = True
        finally:
            try:
                if os.path.lexists(temporary):
                    if temporary_generation is None:
                        temporary_generation = capture_expected_file_generation(temporary)
                    remove_expected_generation_reconciled(
                        temporary,
                        temporary_generation,
                        output_boundary=output_boundary,
                        context="transaction sidecar stage cleanup",
                    )
            except BaseException as exc:
                if os.path.lexists(temporary):
                    cleanup_errors.append(
                        f"{rel(temporary, output_boundary.root)}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                elif publication_error is None:
                    # The underlying unlink completed; the wrapper error does
                    # not turn an absent temporary into a recovery file.
                    pass
        if publication_error is not None or cleanup_errors:
            recovery = [
                rel(temporary, output_boundary.root)
                for _ in [0]
                if os.path.lexists(temporary)
            ]
            raise GroupTransactionError(
                "Protocol-1 transaction record publication was incomplete",
                transaction_id=str(evidence.get("transaction_id") or "unknown"),
                transaction_state="sidecar_publication_incomplete",
                cleanup_warnings=(
                    [
                        f"{type(publication_error).__name__}: {publication_error}"
                    ]
                    if publication_error is not None
                    else []
                )
                + cleanup_errors,
                cleanup_required_paths=recovery,
                recovery_files=recovery,
            ) from publication_error
        if not publication_succeeded:
            raise GroupTransactionError(
                "Protocol-1 transaction record publication has no proven outcome",
                transaction_id=str(evidence.get("transaction_id") or "unknown"),
                transaction_state="sidecar_publication_incomplete",
            )
        expected = capture_expected_file_generation(status_path)
        if (
            not expected.exists
            or expected.byte_size != len(payload)
            or expected.sha256 != hashlib.sha256(payload).hexdigest()
        ):
            raise GroupTransactionError(
                "Protocol-1 transaction record changed before lease handoff",
                transaction_id=str(evidence.get("transaction_id") or "unknown"),
                transaction_state="sidecar_publication_incomplete",
                recovery_files=[rel(status_path, output_boundary.root)],
            )
        return acquire_expected_generation_lease(
            status_path,
            expected,
            context="protocol-1 transaction record publication",
        )


def protocol_one_transaction_record(
    transaction_id: str,
    plans: list[tuple[Path, bytes]],
    export_root: Path,
    *,
    history_link: Mapping[str, Any] | None = None,
    previous_generations: Mapping[Path, ExpectedFileGeneration] | None = None,
) -> dict[str, Any]:
    """Build the immutable record that is published before referenced controls."""
    if history_link is None:
        history_link = protocol_one_history_link(transaction_id, None, None)
    targets = []
    control_anchors: dict[str, Any] = {}
    for path, payload in plans:
        relative = rel(path, export_root)
        targets.append(
            {
                "path": relative,
                "byte_size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        try:
            document = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise ResumeStateError(
                f"Protocol-1 planned control payload is not UTF-8 JSON: {path}."
            ) from exc
        if relative == "summary.json":
            control_anchors["summary"] = document.get("resume", {}).get(
                "group_transaction"
            )
        elif relative == f"{RESUME_STATE_DIR_NAME}/checkpoint.json":
            control_anchors["checkpoint"] = document.get("last_run", {}).get(
                "group_transaction"
            )
    previous: list[dict[str, Any]] = []
    for path in (
        export_root / "summary.json",
        export_root / RESUME_STATE_DIR_NAME / "checkpoint.json",
    ):
        generation = (
            previous_generations.get(path)
            if previous_generations is not None and path in previous_generations
            else capture_expected_file_generation(path)
        )
        if generation is None:
            raise ResumeStateError(
                f"Protocol-1 previous generation is missing for {path}."
            )
        previous.append(expected_generation_evidence(path, generation, export_root))
    return {
        "schema": PROTOCOL_ONE_TRANSACTION_SCHEMA,
        "transaction_id": transaction_id,
        "transaction_state": PROTOCOL_ONE_TRANSACTION_STATE,
        "protocol_version": LEGACY_RESUME_PROTOCOL_VERSION,
        "recorded_at": utc_now(),
        "history": dict(history_link),
        "previous_target_generations": previous,
        "planned_target_contents": targets,
        "planned_control_anchor": control_anchors,
        "cleanup_ownership": {
            "token": transaction_id,
            "exact_filename_grammar": (
                ".ga-(group-retired|audio-stage|audio-backup|group-rollback-new)-"
                "<transaction-id>-<six-decimal-ordinal>.partial"
            ),
        },
    }


def validate_transaction_token(transaction_token: str | None) -> str:
    token = transaction_token or os.urandom(16).hex()
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        raise ValueError("Group transaction token must be 32 lowercase hexadecimal characters.")
    return token


def discover_transaction_owned_partials(
    export_root: Path,
    transaction_id: str,
    *,
    output_boundary: OutputBoundary,
) -> list[Path]:
    """Recursively enumerate only canonical partial names owned by one token."""
    name_pattern = re.compile(
        r"\.ga-(?:group-retired|audio-stage|audio-backup|group-rollback-new)-"
        + re.escape(transaction_id)
        + r"-[0-9]{6}\.partial"
    )
    matches: list[Path] = []

    def fail_walk(error: OSError) -> None:
        raise OutputBoundaryError(f"Cannot scan protocol-1 transaction residue: {error}")

    for current, directories, files in os.walk(
        export_root,
        followlinks=False,
        onerror=fail_walk,
    ):
        current_path = Path(current)
        output_boundary.ensure_directory(current_path)
        directories.sort()
        files.sort()
        for name in directories:
            directory = current_path / name
            if is_reparse_path(directory):
                raise ResumeStateError(
                    f"Protocol-1 transaction scan found a reparse directory: {directory}."
                )
        for name in files:
            path = current_path / name
            if is_reparse_path(path):
                raise ResumeStateError(
                    f"Protocol-1 transaction scan found a reparse file: {path}."
                )
            if name_pattern.fullmatch(name):
                if not path.is_file():
                    raise ResumeStateError(
                        f"Protocol-1 transaction residue is not a regular file: {path}."
                    )
                matches.append(path)
    return matches


def _planned_transaction_target_leases(
    status: dict[str, Any],
    export_root: Path,
) -> list[StableReadLease]:
    plans = status.get("planned_target_contents")
    if not isinstance(plans, list) or len(plans) != 2:
        raise ResumeStateError("Protocol-1 transaction planned target set is invalid.")
    expected_paths = {
        "summary.json",
        f"{RESUME_STATE_DIR_NAME}/checkpoint.json",
    }
    target_pairs: list[tuple[Path, ExpectedFileGeneration]] = []
    observed_paths: set[str] = set()
    for item in plans:
        if not isinstance(item, dict):
            raise ResumeStateError("Protocol-1 transaction planned target is invalid.")
        relative = item.get("path")
        digest = item.get("sha256")
        byte_size = item.get("byte_size")
        if (
            not isinstance(relative, str)
            or relative not in expected_paths
            or relative in observed_paths
            or not isinstance(byte_size, int)
            or byte_size < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
        ):
            raise ResumeStateError("Protocol-1 transaction planned target evidence is invalid.")
        target = lexical_absolute(export_root / PurePosixPath(relative))
        observed = capture_expected_file_generation(target)
        if (
            not observed.exists
            or observed.byte_size != byte_size
            or observed.sha256 != digest
        ):
            raise ResumeStateError(
                f"Protocol-1 committed target differs from its immutable plan: {target}."
            )
        observed_paths.add(relative)
        target_pairs.append((target, observed))
    if observed_paths != expected_paths:
        raise ResumeStateError("Protocol-1 transaction planned target paths are incomplete.")
    return acquire_expected_generation_leases(
        target_pairs,
        context="protocol-1 cleanup recovery",
    )


def reconcile_protocol_one_transaction_records(
    export_root: Path,
    *,
    output_boundary: OutputBoundary,
) -> ProtocolOneTransactionHistory:
    """Rebuild the unique anchored chain and retire one proven interrupted tail."""
    root = export_root / RESUME_STATE_DIR_NAME / "group_transactions"
    checkpoint_path = export_root / RESUME_STATE_DIR_NAME / "checkpoint.json"
    summary_path = export_root / "summary.json"
    if not checkpoint_path.is_file() or not summary_path.is_file():
        if root.exists():
            with os.scandir(root) as entries:
                if next(entries, None) is not None:
                    raise ResumeStateError(
                        "Protocol-1 transaction evidence exists without both control files."
                    )
        return ProtocolOneTransactionHistory(
            [], anchor=None, head_sidecar_sha256=None, records={}
        )

    control_pairs = [
        (summary_path, capture_expected_file_generation(summary_path)),
        (checkpoint_path, capture_expected_file_generation(checkpoint_path)),
    ]
    control_leases = acquire_expected_generation_leases(
        control_pairs,
        context="protocol-1 history reconciliation",
    )
    try:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ResumeStateError(
                f"Cannot read protocol-1 transaction controls: {exc}"
            ) from exc
        summary_reference = summary.get("resume", {}).get("group_transaction")
        checkpoint_reference = checkpoint.get("last_run", {}).get("group_transaction")
        if summary_reference != checkpoint_reference:
            raise ResumeStateError(
                "Protocol-1 summary/checkpoint history references differ."
            )
        reference = summary_reference
        if reference is not None and not isinstance(reference, dict):
            raise ResumeStateError("Protocol-1 history reference is invalid.")

        if not root.exists():
            if reference is not None:
                raise ResumeStateError(
                    "Protocol-1 controls reference a missing transaction history."
                )
            return ProtocolOneTransactionHistory(
                [], anchor=None, head_sidecar_sha256=None, records={}
            )
        output_boundary.ensure_directory(root)
        canonical_pattern = re.compile(r"([0-9a-f]{32})\.json")
        staged_pattern = re.compile(r"\.ga-group-status-([0-9a-f]{32})-.+\.partial")
        canonical: dict[str, Path] = {}
        staged: list[tuple[str, Path]] = []
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                if entry.is_symlink() or is_reparse_path(path):
                    raise ResumeStateError(
                        f"Protocol-1 transaction history contains a reparse entry: {path}."
                    )
                if entry.is_dir(follow_symlinks=False):
                    raise ResumeStateError(
                        f"Protocol-1 transaction history contains a nested directory: {path}."
                    )
                match = canonical_pattern.fullmatch(entry.name)
                if match:
                    canonical[match.group(1)] = path
                    continue
                match = staged_pattern.fullmatch(entry.name)
                if match:
                    staged.append((match.group(1), path))
                    continue
                raise ResumeStateError(
                    f"Protocol-1 transaction history contains an illegal filename: {path}."
                )

        records: dict[str, dict[str, Any]] = {}
        sidecar_sha256: dict[str, str] = {}
        for token, path in sorted(canonical.items()):
            try:
                payload = path.read_bytes()
                record = json.loads(payload.decode("utf-8"))
            except Exception as exc:
                raise ResumeStateError(
                    f"Cannot read protocol-1 transaction history record {path}: {exc}"
                ) from exc
            if record.get("transaction_id") != token:
                raise ResumeStateError(
                    f"Protocol-1 transaction filename/content identity mismatch: {path}."
                )
            if record.get("schema") == PROTOCOL_ONE_TRANSACTION_SCHEMA:
                _validate_protocol_one_v3_record(record, token)
            elif record.get("schema") == PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA:
                if (
                    record.get("transaction_state") != PROTOCOL_ONE_TRANSACTION_STATE
                    or record.get("protocol_version") != LEGACY_RESUME_PROTOCOL_VERSION
                ):
                    raise ResumeStateError(
                        f"Legacy v2 protocol-1 transaction state is invalid: {path}."
                    )
                _validate_protocol_one_plan_set(record.get("planned_target_contents"))
            else:
                raise ResumeStateError(
                    f"Unsupported protocol-1 historical transaction schema: {path}."
                )
            records[token] = record
            sidecar_sha256[token] = hashlib.sha256(payload).hexdigest()

        ordered_ids: list[str] = []
        anchor: dict[str, Any] | None = None
        head_sha256: str | None = None
        if reference is not None:
            transaction_id = reference.get("transaction_id")
            expected_path = (
                f"{RESUME_STATE_DIR_NAME}/group_transactions/{transaction_id}.json"
            )
            if (
                re.fullmatch(r"[0-9a-f]{32}", str(transaction_id)) is None
                or reference.get("status_path") != expected_path
                or reference.get("status_sidecar_authoritative") is not True
                or transaction_id not in records
            ):
                raise ResumeStateError("Protocol-1 history head reference is invalid.")
            head = str(transaction_id)
            head_record = records[head]
            head_sha256 = sidecar_sha256[head]
            if head_record.get("schema") == PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA:
                if set(reference) != {
                    "transaction_id",
                    "status_path",
                    "status_sidecar_authoritative",
                }:
                    raise ResumeStateError("Legacy v2 protocol-1 reference is not exact.")
                anchor = protocol_one_legacy_v2_anchor(head, head_sha256)
                ordered_ids = [head]
            else:
                if set(reference) != {
                    "transaction_id",
                    "status_path",
                    "status_sidecar_authoritative",
                    "history",
                }:
                    raise ResumeStateError("Protocol-1 v3 history reference is not exact.")
                current_id = head
                expected_anchor = reference.get("history")
                seen: set[str] = set()
                reverse_ids: list[str] = []
                while True:
                    if current_id in seen:
                        raise ResumeStateError("Protocol-1 transaction history contains a cycle.")
                    seen.add(current_id)
                    current_record = records.get(current_id)
                    if current_record is None:
                        raise ResumeStateError(
                            f"Protocol-1 transaction history predecessor is missing: {current_id}."
                        )
                    if current_record.get("schema") != PROTOCOL_ONE_TRANSACTION_SCHEMA:
                        raise ResumeStateError(
                            "Protocol-1 legacy boundary may only appear as the final predecessor."
                        )
                    link = _validate_protocol_one_v3_record(current_record, current_id)
                    if protocol_one_history_anchor_from_link(link) != expected_anchor:
                        raise ResumeStateError(
                            "Protocol-1 transaction history head/count/chain anchor differs."
                        )
                    reverse_ids.append(current_id)
                    previous_count = int(link["previous_count"])
                    if previous_count == 0:
                        break
                    previous_id = str(link["previous_head_transaction_id"])
                    if previous_id in seen:
                        raise ResumeStateError("Protocol-1 transaction history contains a cycle.")
                    previous_record = records.get(previous_id)
                    if previous_record is None:
                        raise ResumeStateError(
                            f"Protocol-1 transaction history predecessor is missing: {previous_id}."
                        )
                    if sidecar_sha256[previous_id] != link["previous_sidecar_sha256"]:
                        raise ResumeStateError(
                            "Protocol-1 transaction predecessor digest was tampered."
                        )
                    if previous_record.get("schema") == PROTOCOL_ONE_TRANSACTION_SCHEMA:
                        previous_link = _validate_protocol_one_v3_record(
                            previous_record, previous_id
                        )
                        previous_anchor = protocol_one_history_anchor_from_link(previous_link)
                    elif (
                        previous_record.get("schema")
                        == PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA
                        and previous_count == 1
                    ):
                        previous_anchor = protocol_one_legacy_v2_anchor(
                            previous_id, sidecar_sha256[previous_id]
                        )
                        reverse_ids.append(previous_id)
                        if previous_anchor != {
                            "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
                            "head_transaction_id": previous_id,
                            "count": link["previous_count"],
                            "chain_sha256": link["previous_chain_sha256"],
                        }:
                            raise ResumeStateError(
                                "Protocol-1 legacy v2 migration boundary is invalid."
                            )
                        break
                    else:
                        raise ResumeStateError(
                            "Protocol-1 transaction history has an unsupported legacy boundary."
                        )
                    if previous_anchor != {
                        "schema": PROTOCOL_ONE_HISTORY_SCHEMA,
                        "head_transaction_id": previous_id,
                        "count": link["previous_count"],
                        "chain_sha256": link["previous_chain_sha256"],
                    }:
                        raise ResumeStateError(
                            "Protocol-1 transaction predecessor anchor differs."
                        )
                    current_id = previous_id
                    expected_anchor = previous_anchor
                ordered_ids = list(reversed(reverse_ids))
                anchor = dict(reference["history"])

            plans = _validate_protocol_one_plan_set(
                head_record.get("planned_target_contents")
            )
            for path, generation in control_pairs:
                relative = rel(path, export_root)
                plan = plans[relative]
                if (
                    not generation.exists
                    or generation.byte_size != plan["byte_size"]
                    or generation.sha256 != plan["sha256"]
                ):
                    raise ResumeStateError(
                        f"Protocol-1 history head does not bind current control: {path}."
                    )

        chain_ids = set(ordered_ids)
        extras = sorted(set(records) - chain_ids)

        def link_extends_current(record: dict[str, Any], token: str) -> bool:
            if record.get("schema") != PROTOCOL_ONE_TRANSACTION_SCHEMA:
                return False
            link = _validate_protocol_one_v3_record(record, token)
            expected_previous_head = (
                anchor.get("head_transaction_id") if anchor is not None else None
            )
            expected_previous_count = anchor.get("count") if anchor is not None else 0
            expected_previous_chain = (
                anchor.get("chain_sha256")
                if anchor is not None
                else PROTOCOL_ONE_HISTORY_GENESIS_SHA256
            )
            if (
                link.get("previous_head_transaction_id") != expected_previous_head
                or link.get("previous_count") != expected_previous_count
                or link.get("previous_chain_sha256") != expected_previous_chain
                or link.get("previous_sidecar_sha256") != head_sha256
            ):
                return False
            previous = _validate_protocol_one_target_evidence_set(
                record.get("previous_target_generations"),
                label="interrupted-tail previous",
            )
            return all(previous[rel(path, export_root)] == generation for path, generation in control_pairs)

        if len(extras) > 1:
            raise ResumeStateError(
                "Protocol-1 transaction history contains an extra/forked sidecar set."
            )
        if extras:
            tail_id = extras[0]
            tail = canonical[tail_id]
            if not link_extends_current(records[tail_id], tail_id):
                raise ResumeStateError(
                    "Protocol-1 transaction history contains an unanchored extra sidecar."
                )
            tail_generation = capture_expected_file_generation(tail)
            remove_expected_generation_reconciled(
                tail,
                tail_generation,
                output_boundary=output_boundary,
                context="interrupted protocol-1 sidecar tail cleanup",
            )
            records.pop(tail_id)
            sidecar_sha256.pop(tail_id)

        for token, temporary in staged:
            try:
                payload = temporary.read_bytes()
                record = json.loads(payload.decode("utf-8"))
            except Exception as exc:
                raise ResumeStateError(
                    f"Cannot read staged protocol-1 transaction record {temporary}: {exc}"
                ) from exc
            _validate_protocol_one_v3_record(record, token)
            existing = canonical.get(token)
            if existing is not None:
                if existing.read_bytes() != payload:
                    raise ResumeStateError(
                        f"Protocol-1 staged/canonical sidecar conflict for transaction {token}."
                    )
            elif not link_extends_current(record, token):
                raise ResumeStateError(
                    "Protocol-1 staged sidecar is not a proven interrupted next tail."
                )
            generation = capture_expected_file_generation(temporary)
            remove_expected_generation_reconciled(
                temporary,
                generation,
                output_boundary=output_boundary,
                context="interrupted protocol-1 sidecar stage cleanup",
            )

        for lease in control_leases:
            verify_stable_read_lease(lease)
        return ProtocolOneTransactionHistory(
            [canonical[token] for token in ordered_ids],
            anchor=anchor,
            head_sidecar_sha256=head_sha256,
            records={token: records[token] for token in ordered_ids},
        )
    finally:
        for lease in reversed(control_leases):
            lease.close()


def recover_protocol_one_group_cleanup(
    export_root: Path,
    checkpoint_path: Path,
    *,
    output_boundary: OutputBoundary,
) -> ProtocolOneRecoveryResult | None:
    """Safely retire declared leftovers from a previously committed transaction.

    The immutable status sidecar is authoritative. Cleanup is allowed only when
    every committed target still matches its exact recorded generation and every
    candidate is a transaction-owned, in-root regular ``*.partial`` path.
    """
    if not checkpoint_path.is_file():
        return None
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResumeStateError(
            f"Cannot inspect protocol-1 cleanup recovery checkpoint: {exc}"
        ) from exc
    reference = checkpoint.get("last_run", {}).get("group_transaction")
    if not isinstance(reference, dict):
        return None
    relative_status = reference.get("status_path")
    transaction_id = reference.get("transaction_id")
    if (
        not isinstance(relative_status, str)
        or re.fullmatch(r"[0-9a-f]{32}", str(transaction_id)) is None
    ):
        raise ResumeStateError("Protocol-1 group transaction reference is invalid.")
    expected_prefix = f"{RESUME_STATE_DIR_NAME}/group_transactions/"
    if (
        not relative_status.startswith(expected_prefix)
        or relative_status != f"{expected_prefix}{transaction_id}.json"
    ):
        raise ResumeStateError("Protocol-1 group transaction status path is not canonical.")
    status_path = lexical_absolute(export_root / PurePosixPath(relative_status))
    try:
        status_path.relative_to(lexical_absolute(export_root))
    except ValueError as exc:
        raise ResumeStateError("Protocol-1 group transaction status escapes export root.") from exc
    if not status_path.is_file() or is_reparse_path(status_path):
        raise ResumeStateError(
            f"Protocol-1 group transaction status is missing or unsafe: {status_path}."
        )
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResumeStateError(
            f"Cannot read protocol-1 group transaction status: {exc}"
        ) from exc
    if status.get("transaction_id") != transaction_id:
        raise ResumeStateError("Protocol-1 group transaction sidecar identity mismatch.")
    schema = status.get("schema")
    status_generation = capture_expected_file_generation(status_path)
    target_leases: list[StableReadLease] = [
        acquire_expected_generation_lease(
            status_path,
            status_generation,
            context="protocol-1 transaction sidecar recovery",
        )
    ]
    try:
        if schema in {
            PROTOCOL_ONE_TRANSACTION_SCHEMA,
            PROTOCOL_ONE_LEGACY_TRANSACTION_SCHEMA,
        }:
            if status.get("transaction_state") != PROTOCOL_ONE_TRANSACTION_STATE:
                raise ResumeStateError("Protocol-1 immutable transaction state is invalid.")
            target_leases.extend(
                _planned_transaction_target_leases(status, export_root)
            )
            declared: list[str] = []
        else:
            # Backward-compatible reader for the first transaction-sidecar
            # schema. New writes never create this layout.
            declared = status.get("cleanup_required_paths", [])
            targets = status.get("target_generations", [])
            if not isinstance(declared, list) or not all(
                isinstance(item, str) for item in declared
            ):
                raise ResumeStateError("Protocol-1 cleanup required-path list is invalid.")
            if status.get("transaction_state") != "committed":
                raise GroupTransactionError(
                    "Protocol-1 prior group transaction is not safely recoverable",
                    transaction_id=str(transaction_id),
                    transaction_state=str(status.get("transaction_state") or "unknown"),
                    cleanup_warnings=list(status.get("cleanup_warnings") or []),
                    cleanup_required_paths=declared,
                    recovery_files=list(status.get("recovery_files") or []),
                )
            target_pairs: list[tuple[Path, ExpectedFileGeneration]] = []
            for item in targets:
                if not isinstance(item, dict) or item.get("exists") is not True:
                    raise ResumeStateError("Protocol-1 committed target generation is invalid.")
                target_relative = item.get("path")
                identity = item.get("identity")
                if (
                    not isinstance(target_relative, str)
                    or not isinstance(identity, list)
                    or not all(isinstance(value, int) for value in identity)
                ):
                    raise ResumeStateError("Protocol-1 committed target evidence is invalid.")
                target_pairs.append(
                    (
                        lexical_absolute(export_root / PurePosixPath(target_relative)),
                        ExpectedFileGeneration(
                            True,
                            identity=tuple(identity),
                            byte_size=item.get("byte_size"),
                            sha256=item.get("sha256"),
                        ),
                    )
                )
            if declared and not target_pairs:
                raise ResumeStateError(
                    "Protocol-1 committed cleanup cannot proceed without exact target generations."
                )
            if target_pairs:
                target_leases.extend(
                    acquire_expected_generation_leases(
                        target_pairs,
                        context="legacy protocol-1 cleanup recovery",
                    )
                )

        discovered = discover_transaction_owned_partials(
            export_root,
            str(transaction_id),
            output_boundary=output_boundary,
        )
        candidates: dict[str, Path] = {
            rel(path, export_root): path for path in discovered
        }
        for relative in declared:
            candidate = lexical_absolute(export_root / PurePosixPath(relative))
            if os.path.lexists(candidate):
                candidates[relative] = candidate

        cleanup_errors: list[str] = []
        for relative, candidate in sorted(candidates.items()):
            try:
                candidate.relative_to(lexical_absolute(export_root))
            except ValueError as exc:
                raise ResumeStateError(
                    f"Protocol-1 cleanup path escapes export root: {relative!r}."
                ) from exc
            if is_reparse_path(candidate) or not candidate.is_file():
                raise ResumeStateError(
                    f"Protocol-1 cleanup candidate is not a regular file: {candidate}."
                )
            owned_generation = capture_expected_file_generation(candidate)
            try:
                remove_expected_generation_reconciled(
                    candidate,
                    owned_generation,
                    output_boundary=output_boundary,
                    context="protocol-1 exact-generation cleanup recovery",
                )
            except BaseException as exc:
                cleanup_errors.append(f"{relative}: {type(exc).__name__}: {exc}")

        for lease in target_leases:
            verify_stable_read_lease(lease)
        remaining = [
            rel(path, export_root)
            for path in discover_transaction_owned_partials(
                export_root,
                str(transaction_id),
                output_boundary=output_boundary,
            )
        ]
        if cleanup_errors or remaining:
            raise GroupTransactionError(
                "Protocol-1 committed cleanup recovery remains incomplete",
                transaction_id=str(transaction_id),
                transaction_state="committed_cleanup_required",
                cleanup_warnings=cleanup_errors,
                cleanup_required_paths=remaining,
                recovery_files=remaining,
            )
        evidence = dict(status)
        evidence.update(
            {
                "derived_commit_state": "committed",
                "cleanup_state": "clean",
                "cleanup_warnings": [],
                "cleanup_required_paths": [],
                "recovery_files": [],
            }
        )
        result = ProtocolOneRecoveryResult(evidence, target_leases)
        target_leases = []
        result.verify_handoff()
        return result
    finally:
        for lease in reversed(target_leases):
            lease.close()


def write_export_file_group_if_changed(
    plans: list[tuple[Path, bytes]],
    *,
    output_boundary: OutputBoundary,
    expected_generations: Mapping[Path, ExpectedFileGeneration] | None = None,
    transaction_token: str | None = None,
) -> GroupCommitResult:
    """Conditionally publish a stable-parent set with cross-directory rollback.

    Every target is bound to an existence/identity/size/SHA-256 generation before
    staging. Callers may supply an older audited generation (protocol-1 uses its
    preflight checkpoint snapshot). Each target is revalidated before every first
    mutation. Old and new entries move only through no-replace renames, and the
    generation captured at the backup path is checked before publication. Thus a
    late writer is either observed and preserved or causes a fail-closed recovery;
    it is never accepted as the target that this transaction was authorized to
    overwrite.
    """
    if transaction_token is None and isinstance(plans, ConditionalExportFilePlans):
        transaction_token = plans.transaction_token
    transaction_token = validate_transaction_token(transaction_token)
    if not plans:
        return GroupCommitResult(
            [],
            transaction_id=transaction_token,
            cleanup_required_paths=[],
            target_generations=[],
        )
    if expected_generations is None and isinstance(plans, ConditionalExportFilePlans):
        expected_generations = plans.expected_generations
    prepared_plans = [
        (output_boundary.prepare_file(path), payload) for path, payload in plans
    ]
    parents = sorted(
        {path.parent for path, _ in prepared_plans},
        key=lambda path: os.path.normcase(os.fspath(path)),
    )
    target_keys: set[str] = set()
    for path, payload in prepared_plans:
        if not isinstance(payload, bytes):
            raise TypeError(f"Grouped export payload must be bytes: {path}.")
        key = os.path.normcase(os.path.normpath(os.fspath(path)))
        if key in target_keys:
            raise OutputBoundaryError(
                f"Grouped export contains a duplicate Windows target: {path.name!r}."
            )
        target_keys.add(key)

    supplied_generations: dict[str, ExpectedFileGeneration] = {}
    for supplied_path, generation in (expected_generations or {}).items():
        prepared_path = output_boundary.prepare_file(Path(supplied_path))
        key = os.path.normcase(os.path.normpath(os.fspath(prepared_path)))
        if key not in target_keys:
            raise OutputBoundaryError(
                f"Conditional generation was supplied for a non-target: {prepared_path}."
            )
        if key in supplied_generations:
            raise OutputBoundaryError(
                f"Duplicate conditional generation target: {prepared_path}."
            )
        if not isinstance(generation, ExpectedFileGeneration):
            raise TypeError(
                f"Conditional generation must be ExpectedFileGeneration: {prepared_path}."
            )
        supplied_generations[key] = generation

    states: list[dict[str, Any]] = []
    for target, payload in prepared_plans:
        key = os.path.normcase(os.path.normpath(os.fspath(target)))
        expected = supplied_generations.get(key)
        if expected is None:
            expected = capture_expected_file_generation(target)
        states.append(
            {
                "target": target,
                "payload": payload,
                "expected": expected,
                "temporary": None,
                "temporary_generation": None,
                "reuse": False,
                "backup": None,
                "backup_generation": None,
                "published_generation": None,
            }
        )

    cleanup_errors: list[str] = []
    recovery_files: set[str] = set()
    with ExitStack() as directory_leases:
        for parent in parents:
            directory_leases.enter_context(output_boundary.stable_directory(parent))

        def require_current_generations(context: str) -> None:
            for state in states:
                target = state["target"]
                if state["published_generation"] is not None:
                    expected = state["published_generation"]
                elif state["backup_generation"] is not None:
                    expected = ExpectedFileGeneration(False)
                else:
                    expected = state["expected"]
                require_expected_file_generation(
                    target,
                    expected,
                    context=context,
                )

        def move_generation_no_replace(
            source: Path,
            target: Path,
            expected: ExpectedFileGeneration,
            *,
            context: str,
        ) -> ExpectedFileGeneration:
            require_expected_file_generation(source, expected, context=context)
            require_expected_file_generation(
                target,
                ExpectedFileGeneration(False),
                context=context,
            )
            try:
                output_boundary.rename_no_replace(source, target)
            except BaseException:
                # A wrapper, tracing hook, KeyboardInterrupt, or asynchronous
                # exception may escape after the kernel already completed the
                # rename. Coordinate the observed syscall postcondition before
                # propagating so the transaction never loses the real path of
                # its exact owned generation.
                observed_source = capture_expected_file_generation(source)
                observed_target = capture_expected_file_generation(target)
                if not observed_source.exists and observed_target == expected:
                    return observed_target
                if observed_source == expected and not observed_target.exists:
                    raise
                raise ResumeStateError(
                    "Conditional no-replace rename outcome could not be reconciled "
                    f"during {context}: {source} -> {target}."
                ) from sys.exc_info()[1]
            moved = capture_expected_file_generation(target)
            if moved != expected:
                # The no-replace move safely captured a concurrent source
                # generation. Put that exact entry back when the source name is
                # still free; never replace another late generation.
                if not os.path.lexists(source):
                    try:
                        output_boundary.rename_no_replace(target, source)
                    except Exception as restore_exc:
                        cleanup_errors.append(
                            "conditional move restore "
                            f"{source}: {type(restore_exc).__name__}: {restore_exc}"
                        )
                        recovery_files.add(rel(target, output_boundary.root))
                else:
                    cleanup_errors.append(
                        "conditional move restore blocked by a late canonical target; "
                        f"preserved recovery file {target}"
                    )
                    recovery_files.add(rel(target, output_boundary.root))
                raise ResumeStateError(
                    f"Conditional group commit source generation changed during "
                    f"{context}: {source}."
                )
            return moved

        def remove_generation_exact(
            path: Path,
            generation: ExpectedFileGeneration,
            *,
            context: str,
        ) -> None:
            """Remove only the observed owned generation and reconcile late errors."""
            remove_expected_generation_reconciled(
                path,
                generation,
                output_boundary=output_boundary,
                context=context,
            )

        def retire_owned_generation(
            path: Path,
            generation: ExpectedFileGeneration,
            *,
            token: str,
            ordinal: int,
            context: str,
        ) -> Path | None:
            if not os.path.lexists(path):
                return None
            retired = path.with_name(
                f".ga-group-retired-{token}-{ordinal:06d}.partial"
            )
            move_generation_no_replace(
                path,
                retired,
                generation,
                context=context,
            )
            try:
                remove_generation_exact(
                    retired,
                    generation,
                    context=f"{context} unlink",
                )
            except BaseException:
                if os.path.lexists(retired):
                    recovery_files.add(rel(retired, output_boundary.root))
                raise
            return retired

        try:
            # An explicitly supplied generation can predate this function call.
            # Validate it before even creating transaction-owned stage files.
            require_current_generations("pre-stage validation")
            for state in states:
                target = state["target"]
                payload = state["payload"]
                target = output_boundary.prepare_file(target)
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=".ga-audio-stage-",
                    suffix=".partial",
                    dir=str(target.parent),
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(payload)
                        flush_and_sync(handle)
                except BaseException:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    try:
                        if os.path.lexists(temporary):
                            failed_generation = capture_expected_file_generation(temporary)
                            remove_expected_generation_reconciled(
                                temporary,
                                failed_generation,
                                output_boundary=output_boundary,
                                context="failed grouped stage cleanup",
                            )
                    except Exception as cleanup_exc:
                        cleanup_errors.append(
                            "failed-stage cleanup "
                            f"{temporary}: {type(cleanup_exc).__name__}: {cleanup_exc}"
                        )
                    raise
                temporary = output_boundary.prepare_file(temporary)
                state["temporary"] = temporary
                state["temporary_generation"] = capture_expected_file_generation(
                    temporary
                )
                state["reuse"] = (
                    target.is_file()
                    and not is_reparse_path(target)
                    and files_have_identical_bytes(temporary, target)
                )

            require_current_generations("post-stage validation")
            for index, state in enumerate(states):
                target = state["target"]
                temporary = state["temporary"]
                expected = state["expected"]
                if state["reuse"]:
                    continue
                require_current_generations(
                    f"before target {index + 1} of {len(states)}"
                )
                if expected.exists:
                    backup = target.with_name(
                        f".ga-audio-backup-{transaction_token}-{index:06d}.partial"
                    )
                    state["backup"] = backup
                    state["backup_generation"] = move_generation_no_replace(
                        target,
                        backup,
                        expected,
                        context=f"backup target {index + 1} of {len(states)}",
                    )
                else:
                    require_expected_file_generation(
                        target,
                        ExpectedFileGeneration(False),
                        context=f"publish target {index + 1} of {len(states)}",
                    )
                published = move_generation_no_replace(
                    temporary,
                    target,
                    state["temporary_generation"],
                    context=f"publish target {index + 1} of {len(states)}",
                )
                if (
                    not published.exists
                    or published.byte_size != len(state["payload"])
                    or published.sha256
                    != hashlib.sha256(state["payload"]).hexdigest()
                ):
                    raise ResumeStateError(
                        f"Grouped export published an unexpected generation: {target}."
                    )
                state["published_generation"] = published
                require_current_generations(
                    f"after target {index + 1} of {len(states)}"
                )
            require_current_generations("final committed-generation validation")
        except BaseException:
            active = sys.exc_info()
            for reverse_index, state in enumerate(reversed(states)):
                target = state["target"]
                backup = state["backup"]
                published = state["published_generation"]
                backup_generation = state["backup_generation"]
                try:
                    if published is not None:
                        retired = target.with_name(
                            ".ga-group-rollback-new-"
                            f"{transaction_token}-{reverse_index:06d}.partial"
                        )
                        move_generation_no_replace(
                            target,
                            retired,
                            published,
                            context="rollback retire published generation",
                        )
                        if backup is not None and backup_generation is not None:
                            move_generation_no_replace(
                                backup,
                                target,
                                backup_generation,
                                context="rollback restore prior generation",
                            )
                            state["backup_generation"] = None
                        retire_owned_generation(
                            retired,
                            published,
                            token=transaction_token,
                            ordinal=len(states) + reverse_index,
                            context="rollback remove published generation",
                        )
                        state["published_generation"] = None
                    elif backup is not None and backup_generation is not None:
                        move_generation_no_replace(
                            backup,
                            target,
                            backup_generation,
                            context="rollback restore detached generation",
                        )
                        state["backup_generation"] = None
                except Exception as rollback_exc:
                    cleanup_errors.append(
                        f"rollback {target}: {type(rollback_exc).__name__}: {rollback_exc}"
                    )
            for index, state in enumerate(states):
                temporary = state["temporary"]
                temporary_generation = state["temporary_generation"]
                if temporary is None or temporary_generation is None:
                    continue
                try:
                    retire_owned_generation(
                        temporary,
                        temporary_generation,
                        token=transaction_token,
                        ordinal=(len(states) * 2) + index,
                        context="failed transaction stage cleanup",
                    )
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"stage cleanup {temporary}: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            if cleanup_errors:
                raise GroupTransactionError(
                    "Grouped export rollback was incomplete: " + "; ".join(cleanup_errors),
                    transaction_id=transaction_token,
                    transaction_state="rollback_incomplete",
                    cleanup_warnings=cleanup_errors,
                    cleanup_required_paths=sorted(recovery_files),
                    recovery_files=sorted(recovery_files),
                ) from active[1]
            raise
        else:
            for index, state in enumerate(states):
                temporary = state["temporary"]
                temporary_generation = state["temporary_generation"]
                if temporary is None or temporary_generation is None:
                    continue
                try:
                    retire_owned_generation(
                        temporary,
                        temporary_generation,
                        token=transaction_token,
                        ordinal=(len(states) * 3) + index,
                        context="successful transaction stage cleanup",
                    )
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"stage cleanup {temporary}: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            for index, state in enumerate(states):
                backup = state["backup"]
                backup_generation = state["backup_generation"]
                if backup is None or backup_generation is None:
                    continue
                try:
                    retire_owned_generation(
                        backup,
                        backup_generation,
                        token=transaction_token,
                        ordinal=(len(states) * 4) + index,
                        context="successful transaction backup cleanup",
                    )
                    state["backup_generation"] = None
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"backup cleanup {backup}: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            try:
                # Cleanup remains part of the success window. Reuse targets and
                # newly-published targets must still expose the exact authorized
                # generation immediately before success returns.
                require_current_generations("post-cleanup success validation")
            except BaseException as drift_exc:
                current_recovery = sorted(recovery_files)
                raise GroupTransactionError(
                    "Grouped export committed, but a target generation drifted before success return",
                    transaction_id=transaction_token,
                    transaction_state="committed_target_drift",
                    cleanup_warnings=cleanup_errors,
                    cleanup_required_paths=current_recovery,
                    recovery_files=current_recovery,
                ) from drift_exc
            required_paths = sorted(recovery_files)
            committed_targets = [
                (state["target"], state["published_generation"] or state["expected"])
                for state in states
            ]
            target_leases: list[StableReadLease] = []
            try:
                # The final evidence is not merely a path-time snapshot. Keep
                # every exact committed generation leased through construction
                # and transfer those leases to GroupCommitResult. The caller is
                # responsible for close()/context-manager handoff.
                target_leases = acquire_expected_generation_leases(
                    committed_targets,
                    context="grouped commit result",
                )
                target_generations = [
                    expected_generation_evidence(
                        target,
                        generation,
                        output_boundary.root,
                    )
                    for target, generation in committed_targets
                ]
                for lease in target_leases:
                    verify_stable_read_lease(lease)
                return GroupCommitResult(
                    cleanup_errors,
                    transaction_id=transaction_token,
                    cleanup_required_paths=required_paths,
                    target_generations=target_generations,
                    target_leases=target_leases,
                )
            except BaseException as drift_exc:
                for lease in reversed(target_leases):
                    lease.close()
                raise GroupTransactionError(
                    "Grouped export committed, but the result handoff could not retain "
                    "the exact target generations",
                    transaction_id=transaction_token,
                    transaction_state="committed_target_drift",
                    cleanup_warnings=cleanup_errors,
                    cleanup_required_paths=required_paths,
                    recovery_files=required_paths,
                ) from drift_exc


def export_object(
    obj: Any,
    source_label: str,
    container_paths: dict[int, str],
    export_root: Path,
    audio_mode: str,
    *,
    output_boundary: OutputBoundary | None = None,
    asset_identity_scheme: str = ASSET_IDENTITY_SCHEME,
) -> tuple[dict[str, Any], bool]:
    data = None
    tree = None
    img = None
    asset_type = obj.type.name
    serialized_file = serialized_file_name(obj)
    boundary = output_boundary or OutputBoundary(export_root).initialize()
    asset_id = canonical_asset_id(
        source_label,
        serialized_file,
        obj.path_id,
        asset_identity_scheme,
    )
    name = f"pathid_{obj.path_id}"
    container_path = container_paths.get(obj.path_id, "")
    width: int | str = ""
    height: int | str = ""
    export_kind = "metadata"
    export_path = ""
    status = "indexed"
    remarks = ""
    did_export = False
    try:
        try:
            data = obj.read()
            name = getattr(data, "m_Name", "") or getattr(data, "name", "") or name
        except Exception as exc:
            return (
                {
                    "asset_id": asset_id,
                    "source_file": source_label,
                    "path_id": obj.path_id,
                    "asset_type": asset_type,
                    "asset_name": name,
                    "container_path": container_path,
                    "export_kind": "",
                    "export_path": "",
                    "width": "",
                    "height": "",
                    "status": "read_error",
                    "remarks": f"{type(exc).__name__}: {exc}",
                },
                False,
            )

        base_name = f"{asset_id}_{safe_name(name, asset_type)}"
        try:
            if asset_type in {"Texture2D", "Sprite"}:
                has_image_payload = True
                if asset_type == "Texture2D":
                    has_image_payload, payload_state = texture2d_has_image_payload(data)
                    if not has_image_payload:
                        width = getattr(data, "m_Width", "")
                        height = getattr(data, "m_Height", "")
                        status = "indexed_no_image_data"
                        remarks = payload_state
                if has_image_payload:
                    img = data.image
                    if img is not None:
                        width, height = img.size
                        out_file = export_root / "images" / category_dir(asset_type) / f"{base_name}.png"
                        write_export_file_if_changed(
                            out_file,
                            lambda temp_path: img.save(temp_path, format="PNG"),
                            output_boundary=boundary,
                        )
                        export_kind = "png"
                        export_path = rel(out_file, export_root)
                        status = "exported"
                        did_export = True
            elif asset_type == "TextAsset":
                out_base = export_root / "text_assets" / base_name
                saved, remarks = write_text_asset(
                    data,
                    out_base,
                    output_boundary=boundary,
                )
                export_kind = "text_or_binary"
                export_path = rel(Path(saved), export_root)
                status = "exported"
                did_export = True
            elif asset_type == "AudioClip" and audio_mode == "samples":
                samples = getattr(data, "samples", {}) or {}
                if samples:
                    audio_dir = export_root / "audio" / base_name
                    sample_plan: list[tuple[str, bytes]] = []
                    planned_names: set[str] = set()
                    for sample_key, sample_bytes in samples.items():
                        sample_filename = canonical_audio_sample_filename(sample_key)
                        filename_key = sample_filename.casefold()
                        if filename_key in planned_names:
                            raise OutputBoundaryError(
                                "Distinct AudioClip sample keys produced a duplicate "
                                "Windows output filename before any sample was written."
                            )
                        planned_names.add(filename_key)
                        sample_plan.append((sample_filename, sample_bytes))
                    boundary.ensure_directory(audio_dir)
                    output_plan = [
                        (audio_dir / sample_filename, sample_bytes)
                        for sample_filename, sample_bytes in sample_plan
                    ]
                    group_cleanup_warnings = write_export_file_group_if_changed(
                        output_plan,
                        output_boundary=boundary,
                    )
                    try:
                        group_cleanup_warnings.verify_handoff()
                        saved_files = [rel(path, export_root) for path, _ in output_plan]
                        export_kind = "audio"
                        export_path = ";".join(saved_files)
                        status = "exported"
                        if group_cleanup_warnings:
                            remarks = "Grouped AudioClip cleanup warnings: " + "; ".join(
                                group_cleanup_warnings
                            )
                        did_export = True
                    finally:
                        group_cleanup_warnings.close()
            elif asset_type == "AudioClip":
                export_kind = "metadata"
                status = "indexed_audio_metadata"
                remarks = (
                    "Audio sample export skipped by default to avoid native FMOD decoder crashes; "
                    "rerun with --audio-mode samples if needed."
                )
            else:
                try:
                    tree = obj.read_typetree()
                except Exception:
                    tree = {"repr": repr(data)}
                out_file = export_root / "metadata" / category_dir(asset_type) / f"{base_name}.json"
                payload = {
                    "source_file": source_label,
                    "path_id": obj.path_id,
                    "asset_type": asset_type,
                    "asset_name": name,
                    "container_path": container_path,
                    "type_tree": to_jsonable(tree),
                }
                metadata_text = json.dumps(payload, ensure_ascii=False, indent=2)
                write_export_file_if_changed(
                    out_file,
                    lambda temp_path: temp_path.write_text(metadata_text, encoding="utf-8"),
                    output_boundary=boundary,
                )
                export_path = rel(out_file, export_root)
                status = "exported"
                did_export = True
        except OutputBoundaryError:
            raise
        except Exception as exc:
            status = "export_error"
            remarks = f"{type(exc).__name__}: {exc}"

        return (
            {
                "asset_id": asset_id,
                "source_file": source_label,
                "path_id": obj.path_id,
                "asset_type": asset_type,
                "asset_name": name,
                "container_path": container_path,
                "export_kind": export_kind,
                "export_path": export_path,
                "width": width,
                "height": height,
                "status": status,
                "remarks": remarks,
            },
            did_export,
        )
    finally:
        data = None
        tree = None
        img = None
        del data, tree, img


def assemble_index(
    export_root: Path,
    completed_records: list[dict[str, Any]],
    *,
    output_boundary: OutputBoundary | None = None,
) -> None:
    boundary = output_boundary or OutputBoundary(export_root).initialize()
    index_path = export_root / "all_resources_index.csv"
    temp_path = index_path.with_name(f"{index_path.name}.partial")
    index_path = boundary.prepare_file(index_path)
    temp_path = boundary.prepare_file(temp_path)
    with boundary.stable_directory(temp_path.parent):
        with temp_path.open("w", newline="", encoding="utf-8-sig") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=INDEX_FIELDS)
            writer.writeheader()
            for record in completed_records:
                fragment_path = validate_completed_fragment(
                    export_root,
                    record,
                    output_boundary=boundary,
                )
                with fragment_path.open(
                    "r", encoding="utf-8", newline=""
                ) as fragment_handle:
                    shutil.copyfileobj(fragment_handle, output_handle, length=1024 * 1024)
            flush_and_sync(output_handle)
        boundary.replace(temp_path, index_path)


def assemble_export_log(
    export_root: Path,
    sources: list[Path],
    source_dir: Path,
    records: dict[str, dict[str, Any]],
    *,
    output_boundary: OutputBoundary | None = None,
) -> None:
    boundary = output_boundary or OutputBoundary(export_root).initialize()
    log_path = export_root / "export_log.csv"
    temp_path = log_path.with_name(f"{log_path.name}.partial")
    log_path = boundary.prepare_file(log_path)
    temp_path = boundary.prepare_file(temp_path)
    with boundary.stable_directory(temp_path.parent):
        with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS)
            writer.writeheader()
            for source_path in sources:
                source_label = rel(source_path, source_dir)
                record = records.get(source_label, {})
                status = record.get("status", "pending")
                if status == "completed":
                    message = (
                        f"rows={record.get('row_count', 0)}; "
                        f"attempts={record.get('attempts', 0)}"
                    )
                    stage = "export"
                else:
                    message = str(record.get("message", "source was not completed"))
                    stage = str(record.get("stage", "export"))
                writer.writerow(
                    {
                        "source": source_label,
                        "stage": stage,
                        "status": status,
                        "message": message,
                    }
                )
            flush_and_sync(handle)
        boundary.replace(temp_path, log_path)


def _export_all_authorized(
    source_dir: Path,
    export_root: Path,
    audio_mode: str = "metadata",
    *,
    large_bundle_threshold_bytes: int = LARGE_BUNDLE_DEFAULT_THRESHOLD_BYTES,
    large_bundle_node_threshold_bytes: int = LARGE_BUNDLE_DEFAULT_NODE_THRESHOLD_BYTES,
    large_bundle_io_chunk_bytes: int = LARGE_BUNDLE_DEFAULT_IO_CHUNK_BYTES,
    large_bundle_batch_size: int = LARGE_BUNDLE_DEFAULT_BATCH_SIZE,
    large_bundle_sprite_cache_bytes: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
    large_bundle_sprite_cache_entries: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
    reprocess_sources: list[str] | None = None,
    source_boundary: SourceBoundary,
    authorized_files: list[Path],
    source_snapshot: AuthorizedSourceSnapshot,
) -> dict[str, Any]:
    if UnityPy is None:
        raise SystemExit("UnityPy is required. Run scripts/setup_python_env.ps1 or install requirements.txt.")
    if audio_mode not in {"metadata", "samples"}:
        raise ValueError(f"Unsupported audio mode: {audio_mode}")
    if large_bundle_threshold_bytes <= 0:
        raise ValueError("large bundle threshold must be positive")
    if large_bundle_node_threshold_bytes <= 0:
        raise ValueError("large bundle node threshold must be positive")
    if large_bundle_io_chunk_bytes <= 0:
        raise ValueError("large bundle I/O chunk size must be positive")
    if large_bundle_batch_size <= 0:
        raise ValueError("large bundle batch size must be positive")
    if large_bundle_sprite_cache_bytes < 0 or large_bundle_sprite_cache_entries < 0:
        raise ValueError("Sprite cache byte and entry limits must be non-negative")
    source_dir = source_boundary.root
    source_snapshot.verify_all()
    export_root = lexical_absolute(export_root)
    output_boundary = OutputBoundary(export_root, source_dir).initialize()
    sources = discover_unity_sources(
        source_dir,
        source_boundary=source_boundary,
        authorized_files=authorized_files,
    )
    source_labels = [rel(path, source_dir) for path in sources]
    state_dir = export_root / RESUME_STATE_DIR_NAME
    fragments_dir = state_dir / "fragments"
    checkpoint_path = state_dir / "checkpoint.json"
    recovery_handoff: ProtocolOneRecoveryResult | None = None
    checkpoint_protocol = read_checkpoint_protocol(checkpoint_path)
    if checkpoint_protocol is None:
        state_has_entries = False
        if state_dir.exists():
            with os.scandir(state_dir) as entries:
                state_has_entries = next(entries, None) is not None
        if state_has_entries:
            raise ResumeStateError(
                "Resume state exists without checkpoint.json; protocol 1/2 fragments "
                "will not be mixed silently. Use a fresh output directory or archive "
                "the orphaned state."
            )
        checkpoint_protocol = RESUME_PROTOCOL_VERSION
    elif checkpoint_protocol == LEGACY_RESUME_PROTOCOL_VERSION:
        reconcile_protocol_one_transaction_records(
            export_root,
            output_boundary=output_boundary,
        )
        recovery_handoff = recover_protocol_one_group_cleanup(
            export_root,
            checkpoint_path,
            output_boundary=output_boundary,
        )
    asset_identity_scheme = asset_identity_scheme_for_protocol(checkpoint_protocol)
    validate_canonical_source_tokens(source_labels, asset_identity_scheme)
    requested_reprocess_sources = validate_reprocess_source_requests(
        reprocess_sources,
        source_labels,
    )
    requested_reprocess_set = set(requested_reprocess_sources)
    external_payloads = discover_unity_external_payloads(
        source_dir,
        source_boundary=source_boundary,
        authorized_files=authorized_files,
    )
    catalogs = discover_addressables_catalogs(
        source_dir,
        source_boundary=source_boundary,
        authorized_files=authorized_files,
    )
    source_entries = manifest_entries(sources, source_dir)
    external_entries = manifest_entries(external_payloads, source_dir)
    catalog_entries = manifest_entries(catalogs, source_dir)
    identity = checkpoint_identity(
        source_dir,
        export_root,
        audio_mode,
        source_entries,
        external_entries,
        catalog_entries,
        protocol_version=checkpoint_protocol,
    )
    legacy_no_work_preflight: LegacyNoWorkPreflight | None = None
    if checkpoint_protocol == LEGACY_RESUME_PROTOCOL_VERSION:
        try:
            legacy_no_work_preflight = validate_legacy_protocol_one_no_work(
                export_root,
                checkpoint_path,
                source_labels,
                identity,
                requested_reprocess_sources,
            )
            if recovery_handoff is not None:
                recovery_handoff.verify_handoff()
        finally:
            if recovery_handoff is not None:
                recovery_handoff.close()

    output_boundary.ensure_directory(fragments_dir)
    if legacy_no_work_preflight is not None:
        # Never re-read protocol-1 checkpoint state after the leased preflight.
        # Fragment leases remain live until index/summary/checkpoint publication.
        checkpoint = json.loads(
            json.dumps(legacy_no_work_preflight.checkpoint, ensure_ascii=False)
        )
        verify_stable_read_lease(legacy_no_work_preflight.checkpoint_lease)
        for lease in legacy_no_work_preflight.fragment_leases:
            verify_stable_read_lease(lease)
        checkpoint["resume_count"] = int(checkpoint.get("resume_count", 0)) + 1
        checkpoint["updated_at"] = utc_now()
        checkpoint["status"] = "running"
        update_peak_memory(checkpoint)
        resumed = True
    else:
        checkpoint, resumed = load_or_create_checkpoint(
            checkpoint_path,
            identity,
            protocol_version=checkpoint_protocol,
            output_boundary=output_boundary,
        )
    records: dict[str, dict[str, Any]] = checkpoint["sources"]
    run_started_at = utc_now()
    reprocessed_sources: list[str] = []
    checkpoint["last_run"] = {
        "started_at": run_started_at,
        "large_bundle_options": {
            "strategy_selector": "block_or_node_v1",
            "block_threshold_bytes": large_bundle_threshold_bytes,
            "node_threshold_bytes": large_bundle_node_threshold_bytes,
            "io_chunk_bytes": large_bundle_io_chunk_bytes,
            "batch_size": large_bundle_batch_size,
            "sprite_cache_max_estimated_decoded_bytes": large_bundle_sprite_cache_bytes,
            "sprite_cache_max_entries": large_bundle_sprite_cache_entries,
        },
        "requested_reprocess_sources": requested_reprocess_sources,
        "requested_reprocess_source_count": len(requested_reprocess_sources),
        "reprocessed_sources": reprocessed_sources,
        "reprocessed_source_count": 0,
    }
    checkpoint["updated_at"] = utc_now()
    if legacy_no_work_preflight is None:
        atomic_write_json(
            checkpoint_path,
            checkpoint,
            output_boundary=output_boundary,
        )
    catalog_info = parse_addressables_catalogs(
        source_dir,
        export_root,
        output_boundary=output_boundary,
        catalogs=catalogs,
    )
    skipped_source_count = 0
    retried_source_count = 0

    for source_index, source_path in enumerate(sources):
        source_snapshot.verify(source_path)
        source_rel = rel(source_path, source_dir)
        source_label = source_rel
        prior_record = records.get(source_label, {})
        prior_status = prior_record.get("status")
        if prior_status == "completed":
            if source_label not in requested_reprocess_set:
                validate_fragment_identity_metadata(
                    prior_record,
                    checkpoint_protocol,
                )
                validate_completed_fragment(
                    export_root,
                    prior_record,
                    output_boundary=output_boundary,
                )
                skipped_source_count += 1
                continue
        if prior_status in {"processing", "failed"}:
            retried_source_count += 1
        is_completed_source_reprocess = (
            prior_status == "completed" and source_label in requested_reprocess_set
        )

        fragment_name = source_fragment_name(source_index, source_label)
        fragment_path = fragments_dir / fragment_name
        fragment_temp = fragment_path.with_name(f"{fragment_path.name}.partial")
        attempts = int(prior_record.get("attempts", 0)) + 1
        record: dict[str, Any] = {
            "status": "processing",
            "stage": "export",
            "attempts": attempts,
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "fragment": rel(fragment_path, export_root),
        }
        if checkpoint_protocol == RESUME_PROTOCOL_VERSION:
            record.update(
                {
                    "asset_identity_scheme": ASSET_IDENTITY_SCHEME,
                    "asset_identity_version": ASSET_IDENTITY_VERSION,
                }
            )
        records[source_label] = record
        checkpoint["updated_at"] = utc_now()
        atomic_write_json(
            checkpoint_path,
            checkpoint,
            output_boundary=output_boundary,
        )

        env = None
        data = None
        tree = None
        img = None
        container_paths = None
        obj = None
        try:
            if not source_path.exists():
                record.update(
                    {
                        "status": "failed",
                        "stage": "load",
                        "message": "source disappeared after discovery",
                        "updated_at": utc_now(),
                    }
                )
                continue
            unityfs_layout = probe_unityfs_layout(source_path)
            if should_use_large_bundle_strategy(
                unityfs_layout,
                large_bundle_threshold_bytes,
                large_bundle_node_threshold_bytes,
            ):
                assert unityfs_layout is not None
                try:
                    large_result = export_large_bundle_source(
                        source_path=source_path,
                        source_label=source_label,
                        export_root=export_root,
                        state_dir=state_dir,
                        layout=unityfs_layout,
                        fragment_temp=fragment_temp,
                        fragment_path=fragment_path,
                        audio_mode=audio_mode,
                        threshold_bytes=large_bundle_threshold_bytes,
                        io_chunk_bytes=large_bundle_io_chunk_bytes,
                        batch_size=large_bundle_batch_size,
                        sprite_cache_max_bytes=large_bundle_sprite_cache_bytes,
                        sprite_cache_max_entries=large_bundle_sprite_cache_entries,
                        output_boundary=output_boundary,
                        asset_identity_scheme=asset_identity_scheme,
                    )
                except Exception as exc:
                    record.update(
                        {
                            "status": "processing",
                            "stage": "large_bundle",
                            "message": f"{type(exc).__name__}: {exc}",
                            "updated_at": utc_now(),
                        }
                    )
                    raise
                record.update(
                    {
                        "status": "completed",
                        "stage": "large_bundle",
                        "completed_at": utc_now(),
                        "updated_at": utc_now(),
                        **large_result,
                    }
                )
                if is_completed_source_reprocess:
                    reprocessed_sources.append(source_label)
                    checkpoint["last_run"]["reprocessed_sources"] = list(
                        reprocessed_sources
                    )
                    checkpoint["last_run"]["reprocessed_source_count"] = len(
                        reprocessed_sources
                    )
                continue
            try:
                env = UnityPy.load(str(source_path))
            except Exception as exc:
                record.update(
                    {
                        "status": "failed",
                        "stage": "load",
                        "message": f"{type(exc).__name__}: {exc}",
                        "updated_at": utc_now(),
                    }
                )
                continue

            container_paths = collect_container_paths(env)
            source_counts: Counter[str] = Counter()
            source_exported: Counter[str] = Counter()
            row_count = 0
            fragment_temp = output_boundary.prepare_file(fragment_temp)
            fragment_path = output_boundary.prepare_file(fragment_path)
            with output_boundary.stable_directory(fragment_temp.parent):
                with fragment_temp.open(
                    "w", newline="", encoding="utf-8"
                ) as fragment_handle:
                    fragment_writer = csv.DictWriter(
                        fragment_handle,
                        fieldnames=INDEX_FIELDS,
                    )
                    for obj in env.objects:
                        asset_type = obj.type.name
                        source_counts[asset_type] += 1
                        row, did_export = export_object(
                            obj,
                            source_label,
                            container_paths,
                            export_root,
                            audio_mode,
                            output_boundary=output_boundary,
                            asset_identity_scheme=asset_identity_scheme,
                        )
                        fragment_writer.writerow(row)
                        row_count += 1
                        if did_export:
                            source_exported[asset_type] += 1
                        row = None
                    flush_and_sync(fragment_handle)
                output_boundary.replace(fragment_temp, fragment_path)
            record.update(
                {
                    "status": "completed",
                    "stage": "export",
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                    "row_count": row_count,
                    "type_counts": dict(source_counts),
                    "exported_counts": dict(source_exported),
                    "fragment_sha256": sha256_file(fragment_path),
                }
            )
            if is_completed_source_reprocess:
                reprocessed_sources.append(source_label)
                checkpoint["last_run"]["reprocessed_sources"] = list(
                    reprocessed_sources
                )
                checkpoint["last_run"]["reprocessed_source_count"] = len(
                    reprocessed_sources
                )
        finally:
            source_snapshot.verify(source_path)
            obj = None
            env = None
            data = None
            tree = None
            img = None
            container_paths = None
            del obj, env, data, tree, img, container_paths
            gc.collect()
            update_peak_memory(checkpoint)
            checkpoint["updated_at"] = utc_now()
            atomic_write_json(
                checkpoint_path,
                checkpoint,
                output_boundary=output_boundary,
            )

    counts: Counter[str] = Counter()
    exported: Counter[str] = Counter()
    completed_sources: list[str] = []
    failed_sources: list[str] = []
    failed_source_details: list[dict[str, str]] = []
    completed_records: list[dict[str, Any]] = []
    object_count = 0
    for source_path in sources:
        source_label = rel(source_path, source_dir)
        record = records.get(source_label, {})
        if record.get("status") == "completed":
            completed_sources.append(source_label)
            completed_records.append(record)
            object_count += int(record.get("row_count", 0))
            counts.update(record.get("type_counts", {}))
            exported.update(record.get("exported_counts", {}))
        else:
            failed_sources.append(source_label)
            failed_source_details.append(
                {
                    "source": source_label,
                    "stage": str(record.get("stage", "export")),
                    "status": str(record.get("status", "pending")),
                    "message": str(record.get("message", "source was not completed")),
                }
            )

    assemble_index(
        export_root,
        completed_records,
        output_boundary=output_boundary,
    )
    assemble_export_log(
        export_root,
        sources,
        source_dir,
        records,
        output_boundary=output_boundary,
    )
    update_peak_memory(checkpoint)
    source_snapshot.verify_all()

    summary = {
        "generated_at": utc_now(),
        "source_root": str(source_dir),
        "sources": [rel(path, source_dir) for path in sources],
        "external_payloads": [rel(path, source_dir) for path in external_payloads],
        "external_payload_count": len(external_payloads),
        "parsed_sources": completed_sources,
        "completed_sources": completed_sources,
        "failed_sources": failed_sources,
        "failed_source_details": failed_source_details,
        "source_count": len(sources),
        "completed_source_count": len(completed_sources),
        "failed_source_count": len(failed_sources),
        "object_count": object_count,
        "type_counts": dict(counts),
        "exported_counts": dict(exported),
        "catalog": catalog_info,
        "resume": {
            "protocol_version": checkpoint_protocol,
            "asset_identity_scheme": asset_identity_scheme,
            "asset_identity_version": (
                ASSET_IDENTITY_VERSION
                if checkpoint_protocol == RESUME_PROTOCOL_VERSION
                else 1
            ),
            "checkpoint": rel(checkpoint_path, export_root),
            "source_list_fingerprint": identity["source_list_fingerprint"],
            "resumed": resumed,
            "resume_count": checkpoint["resume_count"],
            "skipped_source_count_this_run": skipped_source_count,
            "retried_source_count_this_run": retried_source_count,
            "requested_reprocess_sources": requested_reprocess_sources,
            "requested_reprocess_source_count": len(requested_reprocess_sources),
            "reprocessed_sources": reprocessed_sources,
            "reprocessed_source_count_this_run": len(reprocessed_sources),
            "completed_source_count": len(completed_sources),
            "failed_source_count": len(failed_sources),
        },
        "peak_memory": checkpoint.get("peak_memory"),
        "large_bundle": {
            "strategy_version": LARGE_BUNDLE_STRATEGY_VERSION,
            "threshold_bytes": large_bundle_threshold_bytes,
            "block_threshold_bytes": large_bundle_threshold_bytes,
            "node_threshold_bytes": large_bundle_node_threshold_bytes,
            "io_chunk_bytes": large_bundle_io_chunk_bytes,
            "batch_size": large_bundle_batch_size,
            "sprite_cache_max_estimated_decoded_bytes": large_bundle_sprite_cache_bytes,
            "sprite_cache_max_entries": large_bundle_sprite_cache_entries,
            "source_count": sum(
                1
                for record in completed_records
                if isinstance(record.get("large_bundle"), dict)
            ),
            "sources": [
                {
                    "source": source_label,
                    **records[source_label]["large_bundle"],
                }
                for source_label in completed_sources
                if isinstance(records[source_label].get("large_bundle"), dict)
            ],
        },
        "notes": [
            "UnityPy route scans likely Unity containers and exports readable objects only.",
            ".resS and .resource files are recorded as external payloads, not loaded as independent object containers; UnityPy resolves them through serialized companion references.",
            "The all-resources index is assembled from atomically committed per-source fragments; completed sources are skipped on a matching resume checkpoint.",
            "Peak memory values are observed at source boundaries when psutil process metrics are available; they may be lower than an intra-source transient peak.",
            "Oversized UnityFS data blocks use a bounded-memory node spool and short-lived object-batch workers. Unsupported oversized compression or encryption fails explicitly instead of falling back to an unbounded UnityPy bundle load.",
            "Large-bundle workers bound UnityPy's decoded Sprite source-texture cache by estimated pixel bytes and an entry window, then clear it before each atomic batch commit; this cache budget is not a claim about the worker's total transient memory peak.",
            "For full Unity project reconstruction, pair this with AssetRipper export and analyze_assetripper_project.py.",
            "AudioClip sample export defaults to metadata-only because UnityPy may call native FMOD decoders that can crash the Python process on some games.",
        ],
    }
    if legacy_no_work_preflight is not None:
        summary["resume"]["legacy_no_work_preflight"] = legacy_no_work_preflight
        summary["resume"]["legacy_no_work_snapshot_binding"] = (
            legacy_no_work_preflight.snapshot_binding
        )
    checkpoint["status"] = "complete_with_errors" if failed_sources else "complete"
    checkpoint["completed_source_count"] = len(completed_sources)
    checkpoint["failed_source_count"] = len(failed_sources)
    checkpoint["last_run"].update(
        {
            "completed_at": utc_now(),
            "skipped_source_count": skipped_source_count,
            "retried_source_count": retried_source_count,
            "reprocessed_sources": list(reprocessed_sources),
            "reprocessed_source_count": len(reprocessed_sources),
        }
    )
    checkpoint["updated_at"] = utc_now()
    if legacy_no_work_preflight is not None:
        transaction_token = validate_transaction_token(None)
        transaction_status_path = (
            export_root
            / RESUME_STATE_DIR_NAME
            / "group_transactions"
            / f"{transaction_token}.json"
        )
        history = reconcile_protocol_one_transaction_records(
            export_root,
            output_boundary=output_boundary,
        )
        history_link = protocol_one_history_link(
            transaction_token,
            history.anchor,
            history.head_sidecar_sha256,
        )
        transaction_reference = protocol_one_transaction_reference(
            transaction_token,
            transaction_status_path,
            export_root,
            history_link,
        )
        summary["resume"]["group_transaction"] = transaction_reference
        checkpoint["last_run"]["group_transaction"] = transaction_reference
        try:
            verify_stable_read_lease(legacy_no_work_preflight.checkpoint_lease)
            for lease in legacy_no_work_preflight.fragment_leases:
                verify_stable_read_lease(lease)
            summary_path = export_root / "summary.json"
            summary_generation = capture_expected_file_generation(summary_path)
            if not summary_generation.exists:
                raise ResumeStateError(
                    "Protocol-1 history genesis requires an existing summary control."
                )
            legacy_no_work_preflight.release_checkpoint()
            json_documents = ConditionalExportFilePlans(
                [
                    (
                        export_root / "summary.json",
                        (
                            json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
                        ).encode("utf-8"),
                    ),
                    (
                        checkpoint_path,
                        (
                            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n"
                        ).encode("utf-8"),
                    ),
                ],
                expected_generations={
                    summary_path: summary_generation,
                    checkpoint_path: legacy_no_work_preflight.checkpoint_generation,
                },
                transaction_token=transaction_token,
            )
            # Publish the immutable transaction record *before* either
            # canonical control can reference it. Its planned content digests
            # let recovery prove a completed control commit without relying on
            # a mutable/post-hoc sidecar update.
            evidence = protocol_one_transaction_record(
                transaction_token,
                list(json_documents),
                export_root,
                history_link=history_link,
                previous_generations=json_documents.expected_generations,
            )
            status_lease = write_group_transaction_status_no_replace(
                transaction_status_path,
                evidence,
                output_boundary=output_boundary,
            )
            try:
                group_result = write_export_file_group_if_changed(
                    json_documents,
                    output_boundary=output_boundary,
                )
                try:
                    verify_stable_read_lease(status_lease)
                    group_result.verify_handoff()
                    if group_result.cleanup_state != "clean":
                        raise GroupTransactionError(
                            "Protocol-1 evidence committed with cleanup still required",
                            transaction_id=transaction_token,
                            transaction_state="committed_cleanup_required",
                            cleanup_warnings=list(group_result),
                            cleanup_required_paths=group_result.cleanup_required_paths,
                            recovery_files=group_result.cleanup_required_paths,
                        )
                finally:
                    group_result.close()
            finally:
                status_lease.close()
        finally:
            legacy_no_work_preflight.close()
    else:
        atomic_write_json(
            export_root / "summary.json",
            summary,
            output_boundary=output_boundary,
        )
        atomic_write_json(
            checkpoint_path,
            checkpoint,
            output_boundary=output_boundary,
        )
    return summary


def export_all(
    source_dir: Path,
    export_root: Path,
    audio_mode: str = "metadata",
    *,
    large_bundle_threshold_bytes: int = LARGE_BUNDLE_DEFAULT_THRESHOLD_BYTES,
    large_bundle_node_threshold_bytes: int = LARGE_BUNDLE_DEFAULT_NODE_THRESHOLD_BYTES,
    large_bundle_io_chunk_bytes: int = LARGE_BUNDLE_DEFAULT_IO_CHUNK_BYTES,
    large_bundle_batch_size: int = LARGE_BUNDLE_DEFAULT_BATCH_SIZE,
    large_bundle_sprite_cache_bytes: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
    large_bundle_sprite_cache_entries: int = LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
    reprocess_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Run one export against a reparse-free, leased source-tree snapshot."""
    boundary = SourceBoundary(source_dir).initialize()
    authorized_files = boundary.discover_files()
    with boundary.acquire_snapshot(authorized_files) as snapshot:
        return _export_all_authorized(
            boundary.root,
            export_root,
            audio_mode=audio_mode,
            large_bundle_threshold_bytes=large_bundle_threshold_bytes,
            large_bundle_node_threshold_bytes=large_bundle_node_threshold_bytes,
            large_bundle_io_chunk_bytes=large_bundle_io_chunk_bytes,
            large_bundle_batch_size=large_bundle_batch_size,
            large_bundle_sprite_cache_bytes=large_bundle_sprite_cache_bytes,
            large_bundle_sprite_cache_entries=large_bundle_sprite_cache_entries,
            reprocess_sources=reprocess_sources,
            source_boundary=boundary,
            authorized_files=authorized_files,
            source_snapshot=snapshot,
        )


def main() -> int:
    if "--large-bundle-worker" in sys.argv[1:]:
        worker_parser = argparse.ArgumentParser()
        worker_parser.add_argument("--large-bundle-worker", action="store_true")
        worker_parser.add_argument("--spool-manifest", required=True)
        worker_parser.add_argument("--serialized-node-index", required=True, type=int)
        worker_parser.add_argument("--batch-start", required=True, type=int)
        worker_parser.add_argument("--large-bundle-batch-size", required=True, type=int)
        worker_parser.add_argument("--source-label", required=True)
        worker_parser.add_argument("--output", required=True)
        worker_parser.add_argument("--audio-mode", choices=["metadata", "samples"], default="metadata")
        worker_parser.add_argument(
            "--asset-identity-scheme",
            choices=[LEGACY_ASSET_IDENTITY_SCHEME, ASSET_IDENTITY_SCHEME],
            default=ASSET_IDENTITY_SCHEME,
        )
        worker_parser.add_argument("--batch-csv", required=True)
        worker_parser.add_argument("--batch-result", required=True)
        worker_parser.add_argument(
            "--large-bundle-sprite-cache-bytes",
            type=int,
            default=LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES,
        )
        worker_parser.add_argument(
            "--large-bundle-sprite-cache-max-entries",
            type=int,
            default=LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
        )
        worker_args = worker_parser.parse_args()
        result = large_bundle_worker_export_batch(
            spool_manifest_path=Path(worker_args.spool_manifest),
            serialized_node_index=worker_args.serialized_node_index,
            start=worker_args.batch_start,
            batch_size=worker_args.large_bundle_batch_size,
            source_label=worker_args.source_label,
            export_root=Path(worker_args.output),
            audio_mode=worker_args.audio_mode,
            batch_csv_path=Path(worker_args.batch_csv),
            batch_result_path=Path(worker_args.batch_result),
            asset_identity_scheme=worker_args.asset_identity_scheme,
            sprite_cache_max_bytes=worker_args.large_bundle_sprite_cache_bytes,
            sprite_cache_max_entries=worker_args.large_bundle_sprite_cache_max_entries,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--audio-mode",
        choices=["metadata", "samples"],
        default="metadata",
        help="Default metadata avoids UnityPy native FMOD sample decoding crashes; use samples to export audio bytes.",
    )
    parser.add_argument(
        "--reprocess-source",
        action="append",
        default=None,
        help=(
            "Reprocess one exact discovered relative source label even when its "
            "checkpoint record is completed. Repeat the option for multiple sources; "
            "unknown or empty labels fail before checkpoint mutation."
        ),
    )
    parser.add_argument(
        "--large-bundle-threshold-mib",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_THRESHOLD_BYTES // (1024 * 1024),
        help="Use bounded-memory UnityFS spooling when any data block reaches this uncompressed size.",
    )
    parser.add_argument(
        "--large-bundle-node-threshold-mib",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_NODE_THRESHOLD_BYTES // (1024 * 1024),
        help=(
            "Use bounded-memory UnityFS spooling when any node reaches this uncompressed size, "
            "even when the bundle consists only of smaller data blocks."
        ),
    )
    parser.add_argument(
        "--large-bundle-io-chunk-mib",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_IO_CHUNK_BYTES // (1024 * 1024),
        help="Compressed-input and decompressed-output chunk size for streamed UnityFS spooling.",
    )
    parser.add_argument(
        "--large-bundle-batch-size",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_BATCH_SIZE,
        help="Maximum objects exported by each short-lived large-bundle worker.",
    )
    parser.add_argument(
        "--large-bundle-sprite-cache-mib",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_BYTES // (1024 * 1024),
        help=(
            "Maximum estimated decoded Sprite source-texture bytes retained by a "
            "large-bundle worker; zero disables cross-object texture retention."
        ),
    )
    parser.add_argument(
        "--large-bundle-sprite-cache-max-entries",
        type=int,
        default=LARGE_BUNDLE_DEFAULT_SPRITE_CACHE_ENTRIES,
        help="Maximum decoded Sprite source textures retained between objects in one worker.",
    )
    args = parser.parse_args()
    summary = export_all(
        Path(args.source),
        Path(args.output),
        audio_mode=args.audio_mode,
        large_bundle_threshold_bytes=args.large_bundle_threshold_mib * 1024 * 1024,
        large_bundle_node_threshold_bytes=(
            args.large_bundle_node_threshold_mib * 1024 * 1024
        ),
        large_bundle_io_chunk_bytes=args.large_bundle_io_chunk_mib * 1024 * 1024,
        large_bundle_batch_size=args.large_bundle_batch_size,
        large_bundle_sprite_cache_bytes=args.large_bundle_sprite_cache_mib * 1024 * 1024,
        large_bundle_sprite_cache_entries=args.large_bundle_sprite_cache_max_entries,
        reprocess_sources=args.reprocess_source,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

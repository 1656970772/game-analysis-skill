#!/usr/bin/env python3
"""Probe and recover Unity Game Framework package resources.

This script handles the stock Game Framework package layout used by
UnityGameFramework:

- ``GameFrameworkVersion.dat`` with the ``GFP`` package-version-list header
  (currently version 2).
- optional virtual file-system containers with the ``GFF`` header.
- resource payload encryption selected by Game Framework ``LoadType``:
  quick XOR (first 220 bytes) or full XOR, keyed by the little-endian
  resource ``hashCode``.

It never modifies the source tree. Extraction preserves logical resource
paths below an explicit output directory and rejects unsafe paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator


GFP_HEADER = b"GFP"
GFF_HEADER = b"GFF"
PACKAGE_VERSION = 2
DEFAULT_EXTENSION = "dat"
GFF_HEADER_SIZE = 20
GFF_BLOCK_SIZE = 12
GFF_STRING_SIZE = 256
GFF_CLUSTER_SIZE = 4096
QUICK_XOR_LENGTH = 220
LOAD_TYPE_NAMES = {
    0: "LoadFromFile",
    1: "LoadFromMemory",
    2: "LoadFromMemoryAndQuickDecrypt",
    3: "LoadFromMemoryAndDecrypt",
    4: "LoadFromBinary",
    5: "LoadFromBinaryAndQuickDecrypt",
    6: "LoadFromBinaryAndDecrypt",
}
NO_DECRYPT_LOAD_TYPES = {0, 1, 4}
QUICK_DECRYPT_LOAD_TYPES = {2, 5}
FULL_DECRYPT_LOAD_TYPES = {3, 6}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class FormatError(ValueError):
    """Raised when a package violates the supported binary contract."""


@dataclass
class AssetEntry:
    index: int
    name: str
    dependency_indexes: list[int]


@dataclass
class ResourceEntry:
    index: int
    name: str
    variant: str | None
    extension: str
    load_type: int
    length: int
    hash_code: int
    asset_indexes: list[int]

    @property
    def full_name(self) -> str:
        if self.variant:
            return f"{self.name}.{self.variant}.{self.extension}"
        return f"{self.name}.{self.extension}"


@dataclass
class FileSystemEntry:
    name: str
    resource_indexes: list[int]


@dataclass
class ResourceGroupEntry:
    name: str
    resource_indexes: list[int]


@dataclass
class PackageVersionList:
    applicable_game_version: str | None
    internal_resource_version: int
    assets: list[AssetEntry]
    resources: list[ResourceEntry]
    file_systems: list[FileSystemEntry]
    resource_groups: list[ResourceGroupEntry]


@dataclass
class GffPayload:
    name: str
    offset: int
    length: int
    block_index: int


@dataclass
class GffIndex:
    source: Path
    key_hex: str
    max_file_count: int
    max_block_count: int
    block_count: int
    payloads: dict[str, GffPayload]


@dataclass
class SourceSlice:
    path: Path
    offset: int
    length: int
    source_kind: str
    file_system: str | None


def read_exact(stream: BinaryIO, length: int, label: str) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise FormatError(f"{label} truncated: need {length}, got {len(data)}")
    return data


def read_byte(stream: BinaryIO, label: str) -> int:
    return read_exact(stream, 1, label)[0]


def read_i32(stream: BinaryIO, label: str) -> int:
    return struct.unpack("<i", read_exact(stream, 4, label))[0]


def read_u32(stream: BinaryIO, label: str) -> int:
    return struct.unpack("<I", read_exact(stream, 4, label))[0]


def read_7bit_int32(stream: BinaryIO, label: str) -> int:
    value = 0
    shift = 0
    for _ in range(5):
        current = read_byte(stream, label)
        value |= (current & 0x7F) << shift
        if (current & 0x80) == 0:
            if value > 0x7FFFFFFF:
                raise FormatError(f"{label} exceeds signed Int32: {value}")
            return value
        shift += 7
    raise FormatError(f"{label} has an invalid 7-bit encoded Int32")


def xor_bytes(data: bytes, key: bytes, limit: int | None = None) -> bytes:
    if not key:
        raise FormatError("XOR key is empty")
    result = bytearray(data)
    count = len(result) if limit is None else min(len(result), limit)
    for index in range(count):
        result[index] ^= key[index % len(key)]
    return bytes(result)


def read_encrypted_string(
    stream: BinaryIO, key: bytes, label: str
) -> str | None:
    length = read_byte(stream, f"{label}.length")
    if length == 0:
        return None
    raw = read_exact(stream, length, label)
    try:
        return xor_bytes(raw, key).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FormatError(f"{label} is not valid UTF-8 after XOR") from exc


def validate_count(value: int, label: str, upper: int = 2_000_000) -> int:
    if value < 0 or value > upper:
        raise FormatError(f"{label} out of range: {value}")
    return value


def parse_package_version_list(path: Path) -> PackageVersionList:
    with path.open("rb") as stream:
        if read_exact(stream, 3, "package header") != GFP_HEADER:
            raise FormatError(f"{path} is not a GFP package version list")
        version = read_byte(stream, "package version")
        if version != PACKAGE_VERSION:
            raise FormatError(
                f"unsupported GFP version {version}; supported: {PACKAGE_VERSION}"
            )
        key = read_exact(stream, 4, "package string XOR key")
        applicable_game_version = read_encrypted_string(
            stream, key, "applicable game version"
        )
        internal_resource_version = read_7bit_int32(
            stream, "internal resource version"
        )

        asset_count = validate_count(
            read_7bit_int32(stream, "asset count"), "asset count"
        )
        assets: list[AssetEntry] = []
        for asset_index in range(asset_count):
            name = read_encrypted_string(stream, key, f"asset[{asset_index}].name")
            if not name:
                raise FormatError(f"asset[{asset_index}] has an empty name")
            dependency_count = validate_count(
                read_7bit_int32(
                    stream, f"asset[{asset_index}].dependency count"
                ),
                f"asset[{asset_index}].dependency count",
            )
            dependencies = [
                read_7bit_int32(
                    stream, f"asset[{asset_index}].dependency[{dependency_index}]"
                )
                for dependency_index in range(dependency_count)
            ]
            assets.append(AssetEntry(asset_index, name, dependencies))

        resource_count = validate_count(
            read_7bit_int32(stream, "resource count"), "resource count"
        )
        resources: list[ResourceEntry] = []
        for resource_index in range(resource_count):
            name = read_encrypted_string(
                stream, key, f"resource[{resource_index}].name"
            )
            if not name:
                raise FormatError(f"resource[{resource_index}] has an empty name")
            variant = read_encrypted_string(
                stream, key, f"resource[{resource_index}].variant"
            )
            extension = (
                read_encrypted_string(
                    stream, key, f"resource[{resource_index}].extension"
                )
                or DEFAULT_EXTENSION
            )
            load_type = read_byte(stream, f"resource[{resource_index}].load type")
            if load_type not in LOAD_TYPE_NAMES:
                raise FormatError(
                    f"resource[{resource_index}] has unsupported load type {load_type}"
                )
            length = read_7bit_int32(
                stream, f"resource[{resource_index}].length"
            )
            hash_code = read_i32(
                stream, f"resource[{resource_index}].hash code"
            )
            asset_index_count = validate_count(
                read_7bit_int32(
                    stream, f"resource[{resource_index}].asset index count"
                ),
                f"resource[{resource_index}].asset index count",
            )
            asset_indexes = [
                read_7bit_int32(
                    stream,
                    f"resource[{resource_index}].asset index[{asset_list_index}]",
                )
                for asset_list_index in range(asset_index_count)
            ]
            resources.append(
                ResourceEntry(
                    resource_index,
                    name,
                    variant,
                    extension,
                    load_type,
                    length,
                    hash_code,
                    asset_indexes,
                )
            )

        file_system_count = validate_count(
            read_7bit_int32(stream, "file system count"), "file system count"
        )
        file_systems: list[FileSystemEntry] = []
        for file_system_index in range(file_system_count):
            name = read_encrypted_string(
                stream, key, f"file system[{file_system_index}].name"
            )
            if not name:
                raise FormatError(
                    f"file system[{file_system_index}] has an empty name"
                )
            index_count = validate_count(
                read_7bit_int32(
                    stream, f"file system[{file_system_index}].resource count"
                ),
                f"file system[{file_system_index}].resource count",
            )
            indexes = [
                read_7bit_int32(
                    stream,
                    (
                        f"file system[{file_system_index}]"
                        f".resource[{list_index}]"
                    ),
                )
                for list_index in range(index_count)
            ]
            file_systems.append(FileSystemEntry(name, indexes))

        resource_group_count = validate_count(
            read_7bit_int32(stream, "resource group count"),
            "resource group count",
        )
        resource_groups: list[ResourceGroupEntry] = []
        for group_index in range(resource_group_count):
            name = read_encrypted_string(
                stream, key, f"resource group[{group_index}].name"
            )
            if not name:
                raise FormatError(
                    f"resource group[{group_index}] has an empty name"
                )
            index_count = validate_count(
                read_7bit_int32(
                    stream, f"resource group[{group_index}].resource count"
                ),
                f"resource group[{group_index}].resource count",
            )
            indexes = [
                read_7bit_int32(
                    stream, f"resource group[{group_index}].resource[{item_index}]"
                )
                for item_index in range(index_count)
            ]
            resource_groups.append(ResourceGroupEntry(name, indexes))

        if stream.read(1):
            raise FormatError("GFP package version list has trailing bytes")

    _validate_version_references(assets, resources, file_systems, resource_groups)
    return PackageVersionList(
        applicable_game_version,
        internal_resource_version,
        assets,
        resources,
        file_systems,
        resource_groups,
    )


def _validate_version_references(
    assets: list[AssetEntry],
    resources: list[ResourceEntry],
    file_systems: list[FileSystemEntry],
    resource_groups: list[ResourceGroupEntry],
) -> None:
    for asset in assets:
        for dependency in asset.dependency_indexes:
            if dependency < 0 or dependency >= len(assets):
                raise FormatError(
                    f"asset[{asset.index}] dependency out of range: {dependency}"
                )
    for resource in resources:
        for asset_index in resource.asset_indexes:
            if asset_index < 0 or asset_index >= len(assets):
                raise FormatError(
                    f"resource[{resource.index}] asset out of range: {asset_index}"
                )
    seen_resource_indexes: set[int] = set()
    for owner, entries in (
        ("file system", file_systems),
        ("resource group", resource_groups),
    ):
        for entry in entries:
            for resource_index in entry.resource_indexes:
                if resource_index < 0 or resource_index >= len(resources):
                    raise FormatError(
                        f"{owner} {entry.name!r} resource out of range: "
                        f"{resource_index}"
                    )
                if owner == "file system":
                    if resource_index in seen_resource_indexes:
                        raise FormatError(
                            f"resource[{resource_index}] belongs to multiple file systems"
                        )
                    seen_resource_indexes.add(resource_index)


def align_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def parse_gff(path: Path) -> GffIndex:
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        if read_exact(stream, 3, "GFF header") != GFF_HEADER:
            raise FormatError(f"{path} is not a GFF file system")
        version = read_byte(stream, "GFF version")
        if version != 0:
            raise FormatError(f"unsupported GFF version {version}")
        key = read_exact(stream, 4, "GFF string XOR key")
        max_file_count = read_i32(stream, "GFF max file count")
        max_block_count = read_i32(stream, "GFF max block count")
        block_count = read_i32(stream, "GFF block count")
        if (
            max_file_count <= 0
            or max_block_count <= 0
            or max_file_count > max_block_count
            or block_count <= 0
            or block_count > max_block_count
        ):
            raise FormatError(
                "invalid GFF counts: "
                f"files={max_file_count}, blocks={max_block_count}, used={block_count}"
            )
        string_offset = GFF_HEADER_SIZE + GFF_BLOCK_SIZE * max_block_count
        file_data_offset = align_up(
            string_offset + GFF_STRING_SIZE * max_file_count,
            GFF_CLUSTER_SIZE,
        )
        if file_data_offset > file_size:
            raise FormatError(
                f"GFF metadata exceeds file: {file_data_offset} > {file_size}"
            )

        blocks: list[tuple[int, int, int]] = []
        for block_index in range(block_count):
            string_index, cluster_index, length = struct.unpack(
                "<iii", read_exact(stream, GFF_BLOCK_SIZE, f"GFF block[{block_index}]")
            )
            blocks.append((string_index, cluster_index, length))

        payloads: dict[str, GffPayload] = {}
        occupied_ranges: list[tuple[int, int, str]] = []
        for block_index, (string_index, cluster_index, length) in enumerate(blocks):
            if string_index < 0:
                continue
            if string_index >= max_file_count:
                raise FormatError(
                    f"GFF block[{block_index}] string index out of range: "
                    f"{string_index}"
                )
            if cluster_index < 0 or length < 0:
                raise FormatError(
                    f"GFF block[{block_index}] has negative offset/length"
                )
            stream.seek(string_offset + GFF_STRING_SIZE * string_index)
            name_length = read_byte(
                stream, f"GFF string[{string_index}].length"
            )
            encrypted_name = read_exact(
                stream, name_length, f"GFF string[{string_index}]"
            )
            try:
                name = xor_bytes(encrypted_name, key).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FormatError(
                    f"GFF string[{string_index}] is not valid UTF-8"
                ) from exc
            if not name:
                raise FormatError(f"GFF block[{block_index}] has an empty name")
            if name in payloads:
                raise FormatError(f"duplicate GFF payload name: {name}")
            offset = cluster_index * GFF_CLUSTER_SIZE
            end = offset + length
            if offset < file_data_offset or end > file_size:
                raise FormatError(
                    f"GFF payload {name!r} out of bounds: "
                    f"{offset}+{length}, file={file_size}"
                )
            payloads[name] = GffPayload(name, offset, length, block_index)
            occupied_ranges.append((offset, end, name))

        occupied_ranges.sort()
        for previous, current in zip(occupied_ranges, occupied_ranges[1:]):
            if current[0] < previous[1]:
                raise FormatError(
                    f"GFF payload overlap: {previous[2]!r} and {current[2]!r}"
                )

    return GffIndex(
        path,
        key.hex().upper(),
        max_file_count,
        max_block_count,
        block_count,
        payloads,
    )


def validate_relative_resource_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or not relative.parts:
        raise FormatError(f"unsafe resource path: {value!r}")
    for component in relative.parts:
        if component in {"", ".", ".."}:
            raise FormatError(f"unsafe resource path component: {value!r}")
        if any(ord(character) < 32 for character in component):
            raise FormatError(f"control character in resource path: {value!r}")
        if any(character in '<>:"|?*' for character in component):
            raise FormatError(f"Windows-unsafe resource path: {value!r}")
        if component.endswith((" ", ".")):
            raise FormatError(f"Windows-unsafe resource path suffix: {value!r}")
        stem = component.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise FormatError(f"Windows reserved resource path: {value!r}")
    return relative


def resolve_output_path(output_root: Path, logical_name: str) -> Path:
    relative = validate_relative_resource_path(logical_name)
    candidate = output_root.joinpath(*relative.parts)
    resolved_root = output_root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise FormatError(f"resource escapes output root: {logical_name!r}") from exc
    return candidate


def build_resource_sources(
    source_root: Path,
    package: PackageVersionList,
) -> tuple[dict[int, SourceSlice], dict[str, GffIndex]]:
    resource_to_file_system: dict[int, str] = {}
    for file_system in package.file_systems:
        for resource_index in file_system.resource_indexes:
            resource_to_file_system[resource_index] = file_system.name

    gff_indexes: dict[str, GffIndex] = {}
    sources: dict[int, SourceSlice] = {}
    for resource in package.resources:
        file_system_name = resource_to_file_system.get(resource.index)
        if file_system_name is None:
            source_path = source_root.joinpath(
                *validate_relative_resource_path(resource.full_name).parts
            )
            if not source_path.is_file():
                raise FormatError(
                    f"resource source is missing: {resource.full_name!r} -> "
                    f"{source_path}"
                )
            sources[resource.index] = SourceSlice(
                source_path, 0, source_path.stat().st_size, "loose", None
            )
            continue

        gff = gff_indexes.get(file_system_name)
        if gff is None:
            gff_path = source_root / f"{file_system_name}.dat"
            if not gff_path.is_file():
                raise FormatError(
                    f"GFF source is missing for {file_system_name!r}: {gff_path}"
                )
            gff = parse_gff(gff_path)
            gff_indexes[file_system_name] = gff
        payload = gff.payloads.get(resource.full_name)
        if payload is None:
            raise FormatError(
                f"GFF {file_system_name!r} lacks resource {resource.full_name!r}"
            )
        sources[resource.index] = SourceSlice(
            gff.source,
            payload.offset,
            payload.length,
            "gff",
            file_system_name,
        )

    return sources, gff_indexes


def resource_xor_key(resource: ResourceEntry) -> bytes | None:
    if resource.load_type in NO_DECRYPT_LOAD_TYPES:
        return None
    if resource.load_type in QUICK_DECRYPT_LOAD_TYPES | FULL_DECRYPT_LOAD_TYPES:
        return struct.pack("<i", resource.hash_code)
    raise FormatError(f"unsupported load type {resource.load_type}")


def decrypt_prefix(resource: ResourceEntry, data: bytes) -> bytes:
    key = resource_xor_key(resource)
    if key is None:
        return data
    if resource.load_type in QUICK_DECRYPT_LOAD_TYPES:
        return xor_bytes(data, key, QUICK_XOR_LENGTH)
    return xor_bytes(data, key)


def identify_magic(data: bytes) -> str:
    signatures = (
        (b"UnityFS\x00", "UnityFS"),
        (b"UnityWeb", "UnityWeb"),
        (b"UnityRaw", "UnityRaw"),
        (b"OggS", "Ogg"),
        (b"RIFF", "RIFF"),
        (b"FSB5", "FSB5"),
        (b"BKHD", "WwiseBank"),
        (b"\x89PNG\r\n\x1a\n", "PNG"),
        (b"PK\x03\x04", "ZIP"),
    )
    for signature, label in signatures:
        if data.startswith(signature):
            return label
    if data and all(
        byte in b"\t\r\n" or 32 <= byte <= 126 for byte in data[:64]
    ):
        return "text-like"
    return "unknown"


def read_source_prefix(source: SourceSlice, length: int = 64) -> bytes:
    with source.path.open("rb") as stream:
        stream.seek(source.offset)
        return read_exact(stream, min(source.length, length), "resource prefix")


def iter_source_chunks(
    source: SourceSlice, chunk_size: int = 1024 * 1024
) -> Iterator[bytes]:
    remaining = source.length
    with source.path.open("rb") as stream:
        stream.seek(source.offset)
        while remaining:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                raise FormatError(
                    f"resource source truncated: {source.path} at "
                    f"{source.length - remaining}/{source.length}"
                )
            remaining -= len(chunk)
            yield chunk


def write_decrypted_resource(
    resource: ResourceEntry,
    source: SourceSlice,
    destination: Path,
    overwrite: bool,
) -> tuple[str, str, int]:
    if destination.exists() and not overwrite:
        raise FormatError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        temporary.unlink()

    source_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    key = resource_xor_key(resource)
    processed = 0
    try:
        with temporary.open("wb") as output:
            for chunk in iter_source_chunks(source):
                source_hash.update(chunk)
                decoded = bytearray(chunk)
                if key is not None:
                    if resource.load_type in QUICK_DECRYPT_LOAD_TYPES:
                        xor_count = max(
                            0,
                            min(
                                len(decoded),
                                QUICK_XOR_LENGTH - processed,
                            ),
                        )
                    else:
                        xor_count = len(decoded)
                    for index in range(xor_count):
                        decoded[index] ^= key[(processed + index) % len(key)]
                output.write(decoded)
                output_hash.update(decoded)
                processed += len(chunk)
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return source_hash.hexdigest().upper(), output_hash.hexdigest().upper(), processed


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_indexes(
    index_dir: Path,
    version_path: Path,
    package: PackageVersionList,
    gff_indexes: dict[str, GffIndex],
    resource_rows: list[dict],
    errors: list[dict],
    mode: str,
) -> dict:
    index_dir.mkdir(parents=True, exist_ok=True)
    asset_rows = [
        {
            "index": asset.index,
            "name": asset.name,
            "dependency_count": len(asset.dependency_indexes),
            "dependency_indexes": "|".join(map(str, asset.dependency_indexes)),
        }
        for asset in package.assets
    ]
    file_system_rows = [
        {
            "name": file_system.name,
            "resource_count": len(file_system.resource_indexes),
            "resource_indexes": "|".join(map(str, file_system.resource_indexes)),
            "gff_source": str(gff_indexes[file_system.name].source)
            if file_system.name in gff_indexes
            else "",
            "gff_payload_count": len(gff_indexes[file_system.name].payloads)
            if file_system.name in gff_indexes
            else 0,
        }
        for file_system in package.file_systems
    ]
    write_csv(
        index_dir / "gameframework_asset_index.csv",
        ["index", "name", "dependency_count", "dependency_indexes"],
        asset_rows,
    )
    resource_fields = [
        "index",
        "logical_name",
        "load_type",
        "load_type_name",
        "declared_length",
        "source_length",
        "length_match",
        "hash_code_signed",
        "hash_code_unsigned",
        "asset_count",
        "source_kind",
        "file_system",
        "source_path",
        "source_offset",
        "encrypted_prefix_hex",
        "decoded_prefix_hex",
        "decoded_magic",
        "output_path",
        "source_sha256",
        "output_sha256",
        "status",
        "error",
    ]
    write_csv(
        index_dir / "gameframework_resource_index.csv",
        resource_fields,
        resource_rows,
    )
    write_csv(
        index_dir / "gameframework_file_system_index.csv",
        [
            "name",
            "resource_count",
            "resource_indexes",
            "gff_source",
            "gff_payload_count",
        ],
        file_system_rows,
    )
    with (index_dir / "errors.jsonl").open("w", encoding="utf-8") as stream:
        for error in errors:
            stream.write(json.dumps(error, ensure_ascii=False) + "\n")

    status_counts: dict[str, int] = {}
    magic_counts: dict[str, int] = {}
    load_type_counts: dict[str, int] = {}
    for row in resource_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        magic_counts[row["decoded_magic"]] = (
            magic_counts.get(row["decoded_magic"], 0) + 1
        )
        load_type_counts[row["load_type_name"]] = (
            load_type_counts.get(row["load_type_name"], 0) + 1
        )
    summary = {
        "format": "Unity Game Framework GFP v2 + GFF v0",
        "mode": mode,
        "version_list": str(version_path),
        "applicable_game_version": package.applicable_game_version,
        "internal_resource_version": package.internal_resource_version,
        "asset_count": len(package.assets),
        "resource_count": len(package.resources),
        "file_system_count": len(package.file_systems),
        "resource_group_count": len(package.resource_groups),
        "status_counts": status_counts,
        "decoded_magic_counts": magic_counts,
        "load_type_counts": load_type_counts,
        "error_count": len(errors),
    }
    (index_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_lines = [
        "# Unity Game Framework 资源恢复摘要",
        "",
        f"- 模式：`{mode}`",
        f"- GFP：`{version_path}`",
        f"- 游戏版本：`{package.applicable_game_version}`",
        f"- 内部资源版本：`{package.internal_resource_version}`",
        f"- Asset：`{len(package.assets)}`",
        f"- Resource：`{len(package.resources)}`",
        f"- GFF 文件系统：`{len(package.file_systems)}`",
        f"- 错误：`{len(errors)}`",
        "",
        "## 状态",
        "",
    ]
    summary_lines.extend(
        f"- `{name}`：{count}" for name, count in sorted(status_counts.items())
    )
    summary_lines.extend(["", "## 解密后魔数", ""])
    summary_lines.extend(
        f"- `{name}`：{count}" for name, count in sorted(magic_counts.items())
    )
    (index_dir / "summary.md").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )
    return summary


def execute(args: argparse.Namespace) -> int:
    source_root = args.source_root.resolve()
    version_path = (
        args.version_list.resolve()
        if args.version_list
        else source_root / "GameFrameworkVersion.dat"
    )
    index_dir = args.index_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else None
    if not source_root.is_dir():
        raise FormatError(f"source root is not a directory: {source_root}")
    if not version_path.is_file():
        raise FormatError(f"version list is missing: {version_path}")
    if args.mode == "extract" and output_dir is None:
        raise FormatError("--output-dir is required in extract mode")

    package = parse_package_version_list(version_path)
    sources, gff_indexes = build_resource_sources(source_root, package)
    rows: list[dict] = []
    errors: list[dict] = []

    for resource in package.resources:
        source = sources[resource.index]
        row = {
            "index": resource.index,
            "logical_name": resource.full_name,
            "load_type": resource.load_type,
            "load_type_name": LOAD_TYPE_NAMES[resource.load_type],
            "declared_length": resource.length,
            "source_length": source.length,
            "length_match": source.length == resource.length,
            "hash_code_signed": resource.hash_code,
            "hash_code_unsigned": resource.hash_code & 0xFFFFFFFF,
            "asset_count": len(resource.asset_indexes),
            "source_kind": source.source_kind,
            "file_system": source.file_system or "",
            "source_path": str(source.path),
            "source_offset": source.offset,
            "encrypted_prefix_hex": "",
            "decoded_prefix_hex": "",
            "decoded_magic": "",
            "output_path": "",
            "source_sha256": "",
            "output_sha256": "",
            "status": "pending",
            "error": "",
        }
        try:
            if source.length != resource.length:
                raise FormatError(
                    f"length mismatch for {resource.full_name!r}: "
                    f"version={resource.length}, source={source.length}"
                )
            encrypted_prefix = read_source_prefix(source)
            decoded_prefix = decrypt_prefix(resource, encrypted_prefix)
            row["encrypted_prefix_hex"] = encrypted_prefix.hex().upper()
            row["decoded_prefix_hex"] = decoded_prefix.hex().upper()
            row["decoded_magic"] = identify_magic(decoded_prefix)
            if args.mode == "extract":
                assert output_dir is not None
                destination = resolve_output_path(output_dir, resource.full_name)
                source_sha256, output_sha256, written = write_decrypted_resource(
                    resource, source, destination, args.overwrite
                )
                if written != resource.length:
                    raise FormatError(
                        f"write length mismatch for {resource.full_name!r}: "
                        f"{written} != {resource.length}"
                    )
                row["output_path"] = str(destination)
                row["source_sha256"] = source_sha256
                row["output_sha256"] = output_sha256
                row["status"] = "extracted"
            else:
                row["status"] = "probed"
        except Exception as exc:  # continue to produce a complete boundary inventory
            row["status"] = "failed"
            row["error"] = str(exc)
            errors.append(
                {
                    "resource_index": resource.index,
                    "logical_name": resource.full_name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        rows.append(row)

    summary = write_indexes(
        index_dir,
        version_path,
        package,
        gff_indexes,
        rows,
        errors,
        args.mode,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors or args.allow_errors else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe or extract Unity Game Framework GFP/GFF resources without "
            "modifying the source tree."
        )
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="StreamingAssets root containing GameFrameworkVersion.dat.",
    )
    parser.add_argument(
        "--version-list",
        type=Path,
        help="Optional GFP path; defaults to <source-root>/GameFrameworkVersion.dat.",
    )
    parser.add_argument(
        "--mode",
        choices=("probe", "extract"),
        default="probe",
        help="Probe headers only or export every decoded resource.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Decoded resource root, required for extract mode.",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        required=True,
        help="Directory for CSV/JSON/Markdown indexes and errors.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing decoded resource files.",
    )
    parser.add_argument(
        "--allow-errors",
        action="store_true",
        help="Return success after recording per-resource failures.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return execute(args)
    except (FormatError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

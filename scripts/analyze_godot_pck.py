#!/usr/bin/env python3
"""Read and validate a standalone Godot PCK format 0/1 directory.

The tool never extracts package content. It validates the directory, entry
ranges, non-overlap, safe ``res://`` paths, and each entry MD5 before writing
JSON/CSV/Markdown indexes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Sequence


MAGIC = b"GDPC"
SUPPORTED_FORMATS = {0, 1}
RESERVED_FIELD_COUNT = 16
MAX_ENTRY_COUNT = 1_000_000
MAX_PATH_BYTES = 64 * 1024
COPY_CHUNK_SIZE = 1024 * 1024
WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
    | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
)


class PckValidationError(ValueError):
    """Raised when a package violates the standalone PCK contract."""


@dataclass
class Entry:
    index: int
    path: str
    path_field_bytes: int
    offset: int
    size: int
    end: int
    md5_expected: str
    md5_actual: str = ""
    md5_valid: bool = False


def read_exact(handle: BinaryIO, size: int, label: str) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise PckValidationError(f"PCK 在读取{label}时截断：需要 {size} 字节，实际 {len(data)} 字节")
    return data


def read_u32(handle: BinaryIO, label: str) -> int:
    return struct.unpack("<I", read_exact(handle, 4, label))[0]


def read_u64(handle: BinaryIO, label: str) -> int:
    return struct.unpack("<Q", read_exact(handle, 8, label))[0]


def decode_safe_path(raw_field: bytes, index: int) -> str:
    raw_path = raw_field.rstrip(b"\0")
    if not raw_path:
        raise PckValidationError(f"条目 {index} 的路径为空")
    if b"\0" in raw_path:
        raise PckValidationError(f"条目 {index} 的路径包含内嵌 NUL")
    try:
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PckValidationError(f"条目 {index} 的路径不是有效 UTF-8：{exc}") from exc

    if "\\" in path:
        raise PckValidationError(f"条目 {index} 的路径使用反斜杠：{path!r}")
    if not path.startswith("res://"):
        raise PckValidationError(f"条目 {index} 的路径不是 res:// 相对路径：{path!r}")
    relative = path[len("res://") :]
    if not relative:
        raise PckValidationError(f"条目 {index} 的路径缺少 res:// 后的文件名")
    if relative.startswith(("/", "//")):
        raise PckValidationError(f"条目 {index} 的路径是绝对或 UNC 路径：{path!r}")
    if re.match(r"^[A-Za-z]:", relative):
        raise PckValidationError(f"条目 {index} 的路径包含盘符：{path!r}")

    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PckValidationError(f"条目 {index} 的路径包含空段、. 或 ..：{path!r}")
    for part in parts:
        if part.endswith((".", " ")):
            raise PckValidationError(
                f"条目 {index} 的路径含 Windows 不允许的段尾点或空格：{path!r}"
            )
        if any(character in WINDOWS_INVALID_FILENAME_CHARS for character in part):
            raise PckValidationError(
                f"条目 {index} 的路径含 Windows 非法字符或 NTFS ADS 分隔符：{path!r}"
            )
        if any(ord(character) < 32 for character in part):
            raise PckValidationError(f"条目 {index} 的路径含 Windows 控制字符：{path!r}")
        device_stem = part.split(".", 1)[0].rstrip(". ").upper()
        if device_stem in WINDOWS_DEVICE_NAMES:
            raise PckValidationError(
                f"条目 {index} 的路径使用 Windows 设备名（含扩展名仍保留）：{path!r}"
            )
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != relative:
        raise PckValidationError(f"条目 {index} 的路径不是规范 res:// 路径：{path!r}")
    return f"res://{normalized}"


def windows_collision_key(path: str) -> str:
    """Return a conservative Windows extraction collision key.

    NFC catches canonically equivalent Unicode spellings and casefold catches
    case-insensitive collisions before an external extractor sees the paths.
    """

    relative = path[len("res://") :]
    return "/".join(
        unicodedata.normalize("NFC", part).casefold() for part in relative.split("/")
    )


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def hash_range(handle: BinaryIO, offset: int, size: int) -> str:
    digest = hashlib.md5()
    handle.seek(offset)
    remaining = size
    while remaining:
        chunk = handle.read(min(remaining, COPY_CHUNK_SIZE))
        if not chunk:
            raise PckValidationError(f"数据区在 offset={offset}、size={size} 的 MD5 校验期间截断")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest().upper()


def validate_ranges(entries: list[Entry], directory_end: int, package_size: int) -> None:
    occupied: list[Entry] = []
    for entry in entries:
        if entry.end > package_size:
            raise PckValidationError(
                f"条目 {entry.index} 数据越界：offset={entry.offset} size={entry.size} "
                f"end={entry.end} package_size={package_size}"
            )
        if entry.size and entry.offset < directory_end:
            raise PckValidationError(
                f"条目 {entry.index} 数据范围与 PCK 头/目录重叠："
                f"offset={entry.offset} directory_end={directory_end}"
            )
        if entry.size:
            occupied.append(entry)

    occupied.sort(key=lambda item: (item.offset, item.end, item.index))
    for previous, current in zip(occupied, occupied[1:], strict=False):
        if current.offset < previous.end:
            raise PckValidationError(
                f"条目数据范围重叠：{previous.index}:{previous.path} "
                f"[{previous.offset},{previous.end}) 与 {current.index}:{current.path} "
                f"[{current.offset},{current.end})"
            )


def analyze(source: Path, max_entries: int = MAX_ENTRY_COUNT) -> dict:
    if max_entries < 1 or max_entries > MAX_ENTRY_COUNT:
        raise PckValidationError(f"--max-entries 必须在 1..{MAX_ENTRY_COUNT} 之间")
    if not source.is_file():
        raise PckValidationError(f"输入 PCK 不存在或不是文件：{source}")

    package_size = source.stat().st_size
    entries: list[Entry] = []
    seen_paths: set[str] = set()
    seen_windows_paths: dict[str, str] = {}
    with source.open("rb") as handle:
        magic = read_exact(handle, 4, "GDPC magic")
        if magic != MAGIC:
            raise PckValidationError(f"magic 不是 GDPC：{magic!r}；本脚本只处理独立 PCK")

        format_version = read_u32(handle, "pack format")
        if format_version not in SUPPORTED_FORMATS:
            raise PckValidationError(
                f"不支持的 PCK format {format_version}；仅支持独立 PCK format 0/1"
            )
        engine_major = read_u32(handle, "engine major")
        engine_minor = read_u32(handle, "engine minor")
        engine_patch = read_u32(handle, "engine patch")
        reserved = [read_u32(handle, f"保留字段 {index}") for index in range(RESERVED_FIELD_COUNT)]
        nonzero_reserved = [index for index, value in enumerate(reserved) if value != 0]
        if nonzero_reserved:
            raise PckValidationError(f"PCK 保留字段必须为 0，非零索引：{nonzero_reserved}")

        entry_count = read_u32(handle, "条目数")
        if entry_count > max_entries:
            raise PckValidationError(f"PCK 条目数 {entry_count} 超过上限 {max_entries}")

        for index in range(entry_count):
            path_size = read_u32(handle, f"条目 {index} 路径长度")
            if path_size < 1 or path_size > MAX_PATH_BYTES:
                raise PckValidationError(
                    f"条目 {index} 路径长度 {path_size} 超出 1..{MAX_PATH_BYTES}"
                )
            raw_path = read_exact(handle, path_size, f"条目 {index} 路径")
            safe_path = decode_safe_path(raw_path, index)
            if safe_path in seen_paths:
                raise PckValidationError(f"条目 {index} 路径重复：{safe_path}")
            seen_paths.add(safe_path)
            collision_key = windows_collision_key(safe_path)
            if collision_key in seen_windows_paths:
                raise PckValidationError(
                    f"条目 {index} 的路径与既有路径发生 Unicode/Windows 大小写折叠冲突："
                    f"{seen_windows_paths[collision_key]!r} 与 {safe_path!r}"
                )
            seen_windows_paths[collision_key] = safe_path

            offset = read_u64(handle, f"条目 {index} offset")
            size = read_u64(handle, f"条目 {index} size")
            expected_md5 = read_exact(handle, 16, f"条目 {index} MD5").hex().upper()
            end = offset + size
            if end > 0xFFFFFFFFFFFFFFFF:
                raise PckValidationError(f"条目 {index} offset+size 发生 uint64 溢出")
            entries.append(
                Entry(
                    index=index,
                    path=safe_path,
                    path_field_bytes=path_size,
                    offset=offset,
                    size=size,
                    end=end,
                    md5_expected=expected_md5,
                )
            )

        directory_end = handle.tell()
        validate_ranges(entries, directory_end, package_size)
        for entry in entries:
            entry.md5_actual = hash_range(handle, entry.offset, entry.size)
            entry.md5_valid = entry.md5_actual == entry.md5_expected
            if not entry.md5_valid:
                raise PckValidationError(
                    f"条目 {entry.index} MD5 不匹配：{entry.path} "
                    f"expected={entry.md5_expected} actual={entry.md5_actual}"
                )

    return {
        "schema_version": 1,
        "mode": "read_only_index",
        "source": str(source.resolve()),
        "source_size": package_size,
        "source_sha256": hash_file(source),
        "format_version": format_version,
        "engine_version": f"{engine_major}.{engine_minor}.{engine_patch}",
        "reserved_fields": reserved,
        "entry_count": entry_count,
        "directory_end": directory_end,
        "entries": [asdict(entry) for entry in entries],
    }


def write_outputs(output_dir: Path, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "godot_pck_index.json"
    csv_path = output_dir / "godot_pck_index.csv"
    summary_path = output_dir / "godot_pck_summary.md"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = [
        "index",
        "path",
        "path_field_bytes",
        "offset",
        "size",
        "end",
        "md5_expected",
        "md5_actual",
        "md5_valid",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["entries"])

    summary_path.write_text(
        "\n".join(
            [
                "# Godot PCK 只读探测摘要",
                "",
                f"- 输入：`{report['source']}`",
                f"- 文件大小：{report['source_size']} bytes",
                f"- SHA-256：`{report['source_sha256']}`",
                f"- PCK format：{report['format_version']}",
                f"- 引擎版本：{report['engine_version']}",
                f"- 目录条目：{report['entry_count']}",
                f"- 目录结束偏移：{report['directory_end']}",
                "- 路径、范围、重叠与 MD5：全部通过。",
                "- 边界：本脚本只读列包，不提取、反编译或转换任何内容。",
                "",
            ]
        ),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读校验并索引独立 Godot PCK format 0/1")
    parser.add_argument("source", type=Path, help="独立 .pck 文件")
    parser.add_argument("--output-dir", required=True, type=Path, help="JSON/CSV/摘要输出目录")
    parser.add_argument(
        "--max-entries",
        type=int,
        default=MAX_ENTRY_COUNT,
        help=f"目录条目上限（默认/硬上限 {MAX_ENTRY_COUNT}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyze(args.source, max_entries=args.max_entries)
        write_outputs(args.output_dir, report)
    except (OSError, PckValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: report[key] for key in ("source", "format_version", "engine_version", "entry_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

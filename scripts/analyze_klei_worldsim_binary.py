#!/usr/bin/env python3
"""Map Klei/DST WorldSim Lua bindings to native PE functions."""

from __future__ import annotations

import argparse
import csv
import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_ANCHOR_NAMES = [
    "WorldGen_InitializeNodePoints",
    "WorldGen_VoronoiPass",
    "WorldGen_Commit",
    "ConvertToTileMap",
    "SeparateIslands",
    "ForceConnectivity",
    "DrawRoads",
    "GetEncodedMap",
    "RunMaze",
    "GetPointsForSite",
]

KEY_METHODS = {
    "WorldGen_InitializeNodePoints",
    "WorldGen_AddNewPositions",
    "WorldGen_VoronoiPass",
    "WorldGen_Commit",
    "ConvertToTileMap",
    "SeparateIslands",
    "ForceConnectivity",
    "DrawRoads",
    "SetRoadParameters",
    "CreateNodeIdTileMap",
    "GetPointsForSite",
    "GetRandomPointsForSite",
    "GetEncodedMap",
    "RunMaze",
    "GetPointsForMetaMaze",
    "ReplaceSingleNonLandTiles",
    "DrawGroundLine",
    "DrawCellLine",
}

DEFAULT_EXCLUDED_CALL_RVAS = {
    0x0BA040,
    0x0BA270,
    0x13FD50,
    0x140040,
    0x149070,
    0x483E10,
    0x483E20,
    0x484030,
    0x484220,
    0x484260,
    0x4842C0,
    0x4844F0,
    0x484510,
    0x484530,
    0x4845C0,
    0x4847A0,
    0x484930,
    0x484A80,
    0x485100,
    0x4861D0,
    0x486360,
    0x486430,
    0x486480,
}


@dataclass(frozen=True)
class RegistrationEntry:
    index: int
    method: str
    name_va: int
    function_va: int


@dataclass(frozen=True)
class FunctionSummary:
    function_va: int
    function_end_va: int | None
    log_strings: list[str]
    direct_calls: list[int]
    candidate_calls: list[int]
    continuation_targets: list[int] = field(default_factory=list)


def format_rva(va: int, image_base: int) -> str:
    return f"0x{va - image_base:X}"


def format_va(va: int) -> str:
    return f"0x{va:X}"


def scan_registration_entries_from_bytes(
    blob: bytes,
    *,
    table_va: int,
    image_base: int,
    read_c_string: Callable[[int], str | None],
    section_for_va: Callable[[int], str | None],
    max_entries: int = 128,
) -> list[RegistrationEntry]:
    entries: list[RegistrationEntry] = []
    for index in range(max_entries):
        offset = index * 24
        if offset + 24 > len(blob):
            break
        name_va, function_va, terminator = struct.unpack_from("<QQQ", blob, offset)
        name = read_c_string(name_va)
        valid = (
            terminator == 0
            and bool(name)
            and section_for_va(name_va) == ".rdata"
            and section_for_va(function_va) == ".text"
        )
        if not valid:
            if entries:
                break
            continue
        entries.append(
            RegistrationEntry(
                index=index,
                method=name or "",
                name_va=name_va,
                function_va=function_va,
            )
        )
    return entries


def select_candidate_calls(direct_calls: Iterable[int], excluded_vas: set[int]) -> list[int]:
    candidates: list[int] = []
    seen: set[int] = set()
    for call in direct_calls:
        if call in excluded_vas or call in seen:
            continue
        seen.add(call)
        candidates.append(call)
    return candidates


def unique_in_order(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def unique_text_in_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def merge_function_summaries(
    primary: FunctionSummary,
    continuations: Iterable[FunctionSummary],
) -> FunctionSummary:
    summaries = [primary, *list(continuations)]
    function_end_va = summaries[-1].function_end_va or primary.function_end_va
    return FunctionSummary(
        function_va=primary.function_va,
        function_end_va=function_end_va,
        log_strings=unique_text_in_order(log for item in summaries for log in item.log_strings),
        direct_calls=unique_in_order(call for item in summaries for call in item.direct_calls),
        candidate_calls=unique_in_order(call for item in summaries for call in item.candidate_calls),
        continuation_targets=unique_in_order(primary.continuation_targets),
    )


def build_summary_markdown(
    *,
    exe_path: Path,
    image_base: int,
    entries: list[RegistrationEntry],
    summaries: dict[str, FunctionSummary],
) -> str:
    key_entries = [entry for entry in entries if entry.method in KEY_METHODS]
    lines = [
        "# Klei/DST WorldSim native 入口索引",
        "",
        "## 观察到的事实",
        "",
        f"- EXE：`{exe_path}`。",
        f"- ImageBase：`{format_va(image_base)}`。",
        f"- Lua 注册表命中条目：{len(entries)}。",
        "- 注册表形态为 64-bit 三元组：`method_name_ptr -> wrapper_function_ptr -> 0`。",
        "",
        "## 学习边界",
        "",
        "- 本索引只输出地址、调用链和行为摘要线索；不要分发反编译源码或第三方原始资源。",
        "- `candidate_calls` 是静态启发式结果：已排除日志、Lua API、常见分配/析构调用，但仍需用 Ghidra/IDA 人工复核。",
        "",
        "## 关键 Lua 绑定",
        "",
        "| Lua 方法 | wrapper RVA | 日志字符串 | 候选 native call RVA |",
        "|---|---:|---|---|",
    ]
    for entry in key_entries:
        summary = summaries.get(entry.method)
        if summary is None:
            continue
        logs = "<br>".join(f"`{item}`" for item in summary.log_strings[:4]) or ""
        candidates = ", ".join(format_rva(call, image_base) for call in summary.candidate_calls[:8])
        lines.append(
            f"| `{entry.method}` | `{format_rva(entry.function_va, image_base)}` | {logs} | `{candidates}` |"
        )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "- 用生成的 `ghidra_worldsim_targets_autogen.csv` 配合 `scripts/ghidra_decompile_targets_csv.py` 做定点反编译。",
            "- 把反编译结果转成自己的伪代码和流程图，不把原始函数体当作交付物发布。",
        ]
    )
    return "\n".join(lines)


class PeImage:
    def __init__(self, path: Path) -> None:
        try:
            import pefile  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised in real env only
            raise SystemExit("缺少 pefile；先运行 scripts/setup_python_env.ps1 或安装 pefile。") from exc

        self.path = path
        self.data = path.read_bytes()
        self.pe = pefile.PE(str(path), fast_load=True)
        self.image_base = int(self.pe.OPTIONAL_HEADER.ImageBase)
        self.sections: list[tuple[str, int, int, int, int]] = []
        for section in self.pe.sections:
            name = section.Name.rstrip(b"\x00").decode("ascii", errors="replace")
            self.sections.append(
                (
                    name,
                    int(section.VirtualAddress),
                    int(section.Misc_VirtualSize),
                    int(section.PointerToRawData),
                    int(section.SizeOfRawData),
                )
            )
        self.runtime_functions = self._load_runtime_functions()

    def section_for_va(self, va: int) -> str | None:
        rva = va - self.image_base
        for name, section_rva, virtual_size, raw_offset, raw_size in self.sections:
            if section_rva <= rva < section_rva + max(virtual_size, raw_size):
                return name
        return None

    def rva_to_offset(self, rva: int) -> int | None:
        for _name, section_rva, virtual_size, raw_offset, raw_size in self.sections:
            if section_rva <= rva < section_rva + max(virtual_size, raw_size):
                offset = raw_offset + (rva - section_rva)
                if 0 <= offset < len(self.data):
                    return offset
        return None

    def va_to_offset(self, va: int) -> int | None:
        return self.rva_to_offset(va - self.image_base)

    def offset_to_va(self, offset: int) -> int | None:
        for _name, section_rva, _virtual_size, raw_offset, raw_size in self.sections:
            if raw_offset <= offset < raw_offset + raw_size:
                return self.image_base + section_rva + (offset - raw_offset)
        return None

    def read_c_string(self, va: int, max_len: int = 512) -> str | None:
        if self.section_for_va(va) not in {".rdata", ".data"}:
            return None
        offset = self.va_to_offset(va)
        if offset is None:
            return None
        end = self.data.find(b"\x00", offset, min(len(self.data), offset + max_len))
        if end < 0:
            return None
        raw = self.data[offset:end]
        if not raw:
            return ""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in text):
            return None
        return text

    def find_ascii_strings(self, text: str) -> list[tuple[int, int]]:
        hits: list[tuple[int, int]] = []
        needle = text.encode("utf-8")
        start = 0
        while True:
            offset = self.data.find(needle, start)
            if offset < 0:
                break
            va = self.offset_to_va(offset)
            if va is not None:
                hits.append((offset, va))
            start = offset + 1
        return hits

    def find_qword_refs(self, target_va: int) -> list[tuple[int, int]]:
        hits: list[tuple[int, int]] = []
        needle = struct.pack("<Q", target_va)
        start = 0
        while True:
            offset = self.data.find(needle, start)
            if offset < 0:
                break
            va = self.offset_to_va(offset)
            if va is not None:
                hits.append((offset, va))
            start = offset + 1
        return hits

    def _load_runtime_functions(self) -> list[tuple[int, int]]:
        functions: list[tuple[int, int]] = []
        pdata = next((section for section in self.sections if section[0] == ".pdata"), None)
        if pdata is None:
            return functions
        _name, _rva, _virtual_size, raw_offset, raw_size = pdata
        for offset in range(raw_offset, raw_offset + raw_size, 12):
            if offset + 12 > len(self.data):
                break
            begin_rva, end_rva, _unwind_rva = struct.unpack_from("<III", self.data, offset)
            if begin_rva and end_rva and begin_rva < end_rva:
                functions.append((self.image_base + begin_rva, self.image_base + end_rva))
        return sorted(functions)

    def function_bounds(self, va: int) -> tuple[int, int] | None:
        for start, end in self.runtime_functions:
            if start <= va < end:
                return start, end
        return None

    def disassemble_function(self, va: int, max_bytes: int = 0x1000):
        try:
            from capstone import CS_ARCH_X86, CS_MODE_64, Cs  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised in real env only
            raise SystemExit("缺少 capstone；先运行 scripts/setup_python_env.ps1 或安装 capstone。") from exc

        bounds = self.function_bounds(va) or (va, va + 0x200)
        start, end = bounds
        offset = self.va_to_offset(start)
        if offset is None:
            return []
        size = min(end - start, max_bytes)
        md = Cs(CS_ARCH_X86, CS_MODE_64)
        md.detail = True
        return list(md.disasm(self.data[offset : offset + size], start))


def find_registration_table(image: PeImage, anchor_names: list[str]) -> list[RegistrationEntry]:
    best: list[RegistrationEntry] = []
    for anchor in anchor_names:
        for _string_offset, string_va in image.find_ascii_strings(anchor):
            for ref_offset, _ref_va in image.find_qword_refs(string_va):
                for back in range(0, 32):
                    table_offset = ref_offset - back * 24
                    if table_offset < 0:
                        continue
                    table_va = image.offset_to_va(table_offset)
                    if table_va is None:
                        continue
                    blob = image.data[table_offset : table_offset + 24 * 96]
                    entries = scan_registration_entries_from_bytes(
                        blob,
                        table_va=table_va,
                        image_base=image.image_base,
                        read_c_string=image.read_c_string,
                        section_for_va=image.section_for_va,
                        max_entries=96,
                    )
                    if any(entry.method == anchor for entry in entries) and len(entries) > len(best):
                        best = entries
    return best


def collect_function_summary(
    image: PeImage,
    function_va: int,
    excluded_vas: set[int],
) -> FunctionSummary:
    try:
        from capstone.x86 import X86_OP_IMM, X86_OP_MEM  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised in real env only
        raise SystemExit("缺少 capstone；先运行 scripts/setup_python_env.ps1 或安装 capstone。") from exc

    bounds = image.function_bounds(function_va)
    direct_calls: list[int] = []
    log_strings: list[str] = []
    continuation_targets: list[int] = []
    seen_logs: set[str] = set()
    for insn in image.disassemble_function(function_va):
        for operand in insn.operands:
            if insn.mnemonic.startswith("call") and operand.type == X86_OP_IMM:
                direct_calls.append(int(operand.imm))
            elif (
                bounds
                and insn.mnemonic.startswith("j")
                and operand.type == X86_OP_IMM
                and int(operand.imm) == bounds[1]
            ):
                continuation_targets.append(int(operand.imm))
            elif operand.type == X86_OP_MEM and insn.reg_name(operand.mem.base) == "rip":
                target_va = int(insn.address + insn.size + operand.mem.disp)
                text = image.read_c_string(target_va)
                if not text:
                    continue
                if "WorldSimActual" in text or "WorldGen" in text or "DrawRoads" in text:
                    if text not in seen_logs:
                        seen_logs.add(text)
                        log_strings.append(text)
    return FunctionSummary(
        function_va=function_va,
        function_end_va=bounds[1] if bounds else None,
        log_strings=log_strings,
        direct_calls=direct_calls,
        candidate_calls=select_candidate_calls(direct_calls, excluded_vas),
        continuation_targets=unique_in_order(continuation_targets),
    )


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def choose_exe(game_root: Path) -> Path:
    candidates = [
        game_root / "bin64" / "dontstarve_dedicated_server_nullrenderer_x64.exe",
        game_root / "bin64" / "dontstarve_steam_x64.exe",
        game_root / "bin" / "dontstarve_dedicated_server_nullrenderer.exe",
        game_root / "bin" / "dontstarve_steam.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit(f"未找到 Klei/DST 可执行文件：{game_root}")


def analyze_worldsim_binary(
    *,
    exe_path: Path,
    output_dir: Path,
    anchor_names: list[str] | None = None,
) -> dict[str, object]:
    image = PeImage(exe_path)
    anchors = anchor_names or DEFAULT_ANCHOR_NAMES
    entries = find_registration_table(image, anchors)
    if not entries:
        raise SystemExit("未能定位 WorldSim Lua 注册表；请检查 EXE 版本或 anchor names。")

    excluded_vas = {image.image_base + rva for rva in DEFAULT_EXCLUDED_CALL_RVAS}
    summaries: dict[str, FunctionSummary] = {}
    for entry in entries:
        primary = collect_function_summary(image, entry.function_va, excluded_vas)
        continuations = [
            collect_function_summary(image, target, excluded_vas)
            for target in primary.continuation_targets
            if image.section_for_va(target) == ".text"
        ]
        summaries[entry.method] = merge_function_summaries(primary, continuations)

    binding_rows: list[dict[str, object]] = []
    ghidra_rows: list[dict[str, object]] = []
    for entry in entries:
        summary = summaries[entry.method]
        binding_rows.append(
            {
                "index": entry.index,
                "method": entry.method,
                "name_va": format_va(entry.name_va),
                "name_rva": format_rva(entry.name_va, image.image_base),
                "wrapper_va": format_va(entry.function_va),
                "wrapper_rva": format_rva(entry.function_va, image.image_base),
                "function_end_rva": format_rva(summary.function_end_va, image.image_base)
                if summary.function_end_va
                else "",
                "log_strings": " | ".join(summary.log_strings),
                "direct_call_rvas": " ".join(format_rva(call, image.image_base) for call in summary.direct_calls),
                "candidate_call_rvas": " ".join(
                    format_rva(call, image.image_base) for call in summary.candidate_calls
                ),
            }
        )
        ghidra_rows.append({"name": f"{entry.method}_wrapper", "rva": format_rva(entry.function_va, image.image_base)})
        for call in summary.candidate_calls:
            ghidra_rows.append(
                {
                    "name": f"{entry.method}_candidate_{call - image.image_base:X}",
                    "rva": format_rva(call, image.image_base),
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "worldsim_lua_bindings.csv",
        binding_rows,
        [
            "index",
            "method",
            "name_va",
            "name_rva",
            "wrapper_va",
            "wrapper_rva",
            "function_end_rva",
            "log_strings",
            "direct_call_rvas",
            "candidate_call_rvas",
        ],
    )
    write_csv(output_dir / "ghidra_worldsim_targets_autogen.csv", ghidra_rows, ["name", "rva"])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exe_path": str(exe_path),
        "image_base": format_va(image.image_base),
        "entry_count": len(entries),
        "key_methods_found": [entry.method for entry in entries if entry.method in KEY_METHODS],
        "outputs": {
            "bindings_csv": str(output_dir / "worldsim_lua_bindings.csv"),
            "ghidra_targets_csv": str(output_dir / "ghidra_worldsim_targets_autogen.csv"),
            "markdown": str(output_dir / "worldsim_function_summary.md"),
        },
    }
    write_text(output_dir / "worldsim_binary_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    write_text(
        output_dir / "worldsim_function_summary.md",
        build_summary_markdown(
            exe_path=exe_path,
            image_base=image.image_base,
            entries=entries,
            summaries=summaries,
        ),
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", required=True, help="GameAnalysis study root.")
    parser.add_argument("--game-root", help="Original Klei/DST game root. Used to choose a default EXE.")
    parser.add_argument("--exe", help="Explicit PE file to analyze.")
    parser.add_argument("--output-dir", help="Output directory. Defaults to study-root KleiDST worldsim index.")
    parser.add_argument("--anchor-name", action="append", help="Additional Lua binding anchor name.")
    args = parser.parse_args()

    study_root = Path(args.study_root)
    if args.exe:
        exe_path = Path(args.exe)
    elif args.game_root:
        exe_path = choose_exe(Path(args.game_root))
    else:
        raise SystemExit("必须提供 --exe 或 --game-root。")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else study_root / "4.临时目录" / "中间索引" / "KleiDST" / "worldsim_native"
    )
    anchors = DEFAULT_ANCHOR_NAMES + (args.anchor_name or [])
    result = analyze_worldsim_binary(exe_path=exe_path, output_dir=output_dir, anchor_names=anchors)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

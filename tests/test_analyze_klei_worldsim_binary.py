from __future__ import annotations

import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_klei_worldsim_binary import (  # noqa: E402
    FunctionSummary,
    RegistrationEntry,
    build_summary_markdown,
    format_rva,
    merge_function_summaries,
    scan_registration_entries_from_bytes,
    select_candidate_calls,
)


def test_scans_lua_registration_triples_from_bytes() -> None:
    image_base = 0x180000000
    blob = b"".join(
        [
            struct.pack("<QQQ", image_base + 0x1000, image_base + 0x2000, 0),
            struct.pack("<QQQ", image_base + 0x1020, image_base + 0x2100, 0),
            struct.pack("<QQQ", image_base + 0x1040, image_base + 0x3000, 1),
        ]
    )
    names = {
        image_base + 0x1000: "WorldGen_VoronoiPass",
        image_base + 0x1020: "ConvertToTileMap",
        image_base + 0x1040: "InvalidTerminator",
    }

    def section_for_va(va: int) -> str | None:
        if va in names:
            return ".rdata"
        if image_base + 0x2000 <= va < image_base + 0x3000:
            return ".text"
        return None

    entries = scan_registration_entries_from_bytes(
        blob,
        table_va=image_base + 0x5000,
        image_base=image_base,
        read_c_string=lambda va: names.get(va),
        section_for_va=section_for_va,
    )

    assert entries == [
        RegistrationEntry(0, "WorldGen_VoronoiPass", image_base + 0x1000, image_base + 0x2000),
        RegistrationEntry(1, "ConvertToTileMap", image_base + 0x1020, image_base + 0x2100),
    ]


def test_select_candidate_calls_keeps_order_and_removes_known_noise() -> None:
    assert select_candidate_calls(
        [0x140149070, 0x140486480, 0x14046BF20, 0x14046BF20, 0x140149070],
        excluded_vas={0x140149070, 0x140486480},
    ) == [0x14046BF20]


def test_format_rva_uses_image_base() -> None:
    assert format_rva(0x14012B9D0, 0x140000000) == "0x12B9D0"


def test_merge_function_summaries_keeps_unique_calls_and_logs() -> None:
    first = FunctionSummary(
        function_va=0x14012FAD0,
        function_end_va=0x14012FB20,
        log_strings=[],
        direct_calls=[0x1404861D0],
        candidate_calls=[],
        continuation_targets=[0x14012FB20],
    )
    second = FunctionSummary(
        function_va=0x14012FB20,
        function_end_va=0x140130000,
        log_strings=["[GetEncodedMap] continuation"],
        direct_calls=[0x14012BC10, 0x1400B9F90],
        candidate_calls=[0x14012BC10, 0x1400B9F90],
    )

    merged = merge_function_summaries(first, [second])

    assert merged.function_va == first.function_va
    assert merged.function_end_va == second.function_end_va
    assert merged.log_strings == ["[GetEncodedMap] continuation"]
    assert merged.candidate_calls == [0x14012BC10, 0x1400B9F90]
    assert merged.continuation_targets == [0x14012FB20]


def test_summary_markdown_preserves_learning_boundary() -> None:
    markdown = build_summary_markdown(
        exe_path=Path("dontstarve_dedicated_server_nullrenderer_x64.exe"),
        image_base=0x140000000,
        entries=[
            RegistrationEntry(0, "WorldGen_VoronoiPass", 0x1404C6B90, 0x14012B9D0),
        ],
        summaries={
            "WorldGen_VoronoiPass": FunctionSummary(
                function_va=0x14012B9D0,
                function_end_va=0x14012BA2C,
                log_strings=["[WorldSimActual::WorldGen_VoronoiPass]"],
                direct_calls=[0x140149070, 0x14046C6C0],
                candidate_calls=[0x14046C6C0],
            )
        },
    )

    assert "WorldGen_VoronoiPass" in markdown
    assert "0x12B9D0" in markdown
    assert "0x46C6C0" in markdown
    assert "不要分发反编译源码" in markdown

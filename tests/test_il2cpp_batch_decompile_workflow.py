from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from build_il2cpp_ghidra_targets import build_targets  # noqa: E402
from merge_ghidra_decompile_summaries import merge_coverage  # noqa: E402


def test_build_targets_extracts_full_method_rva_list_with_filters_and_batches(tmp_path: Path) -> None:
    dump_cs = tmp_path / "dump.cs"
    dump_cs.write_text(
        """
namespace Forestrike.Characters
{
    public class WeaponSystem
    {
        // RVA: 0x73D600 Offset: 0x73BE00 VA: 0x18073D600
        public bool TryEquipWeapon(Weapon newWeapon) { }

        // RVA: 0x73D2C0 Offset: 0x73BAC0 VA: 0x18073D2C0
        public void ThrowWeapon(Weapon weapon) { }

        // RVA: 0x0 Offset: 0x0 VA: 0x0
        public abstract void AbstractOnly();
    }
}

namespace SCS.Juice
{
    public class CameraZoomer
    {
        // RVA: 0x775980 Offset: 0x774180 VA: 0x180775980
        public void ZoomInternal(float value) { }
    }
}
""",
        encoding="utf-8",
    )
    output = tmp_path / "targets.csv"
    batch_dir = tmp_path / "batches"

    summary = build_targets(
        dump_cs=dump_cs,
        output=output,
        batch_dir=batch_dir,
        batch_size=2,
        include_regexes=["Weapon|Camera"],
        exclude_regexes=["AbstractOnly"],
    )

    rows = list(csv.DictReader(output.open("r", encoding="utf-8")))
    assert [row["name"] for row in rows] == [
        "Forestrike.Characters.WeaponSystem.TryEquipWeapon",
        "Forestrike.Characters.WeaponSystem.ThrowWeapon",
        "SCS.Juice.CameraZoomer.ZoomInternal",
    ]
    assert [row["rva"] for row in rows] == ["0x73D600", "0x73D2C0", "0x775980"]
    assert summary["target_count"] == 3
    assert summary["skipped_zero_rva_count"] == 1
    assert (batch_dir / "targets_0001.csv").exists()
    assert (batch_dir / "targets_0002.csv").exists()


def test_merge_coverage_reports_ok_failed_and_missing_targets(tmp_path: Path) -> None:
    targets = tmp_path / "targets.csv"
    targets.write_text(
        "name,rva\nA.Foo,0x100\nB.Bar,0x200\nC.Baz,0x300\n",
        encoding="utf-8",
    )
    summary_dir = tmp_path / "batch1"
    summary_dir.mkdir()
    (summary_dir / "decompile_summary.csv").write_text(
        "name,rva,status,output\nA.Foo,0x100,OK,A.Foo.c\nB.Bar,0x200,FAILED,error\n",
        encoding="utf-8",
    )
    output_csv = tmp_path / "coverage.csv"
    output_json = tmp_path / "coverage.json"

    summary = merge_coverage(
        targets_csv=targets,
        summary_paths=[summary_dir / "decompile_summary.csv"],
        output_csv=output_csv,
        output_json=output_json,
    )

    assert summary["target_count"] == 3
    assert summary["ok_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["missing_count"] == 1
    assert json.loads(output_json.read_text(encoding="utf-8"))["coverage_percent"] == 33.33
    rows = list(csv.DictReader(output_csv.open("r", encoding="utf-8-sig")))
    assert [row["status"] for row in rows] == ["OK", "FAILED", "MISSING"]

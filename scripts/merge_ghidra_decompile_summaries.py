#!/usr/bin/env python3
"""Merge Ghidra batch decompile summaries into coverage reports."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def target_key(row: dict[str, str]) -> tuple[str, str]:
    return ((row.get("name") or row.get("Name") or "").strip(), (row.get("rva") or row.get("RVA") or "").strip().lower())


def discover_summary_paths(summary_roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in summary_roots:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(sorted(root.rglob("decompile_summary.csv")))
    return paths


def merge_coverage(
    targets_csv: Path,
    summary_paths: list[Path],
    output_csv: Path,
    output_json: Path,
) -> dict[str, object]:
    target_rows = read_csv_rows(targets_csv)
    results: dict[tuple[str, str], dict[str, str]] = {}
    duplicate_result_count = 0

    for summary_path in summary_paths:
        for row in read_csv_rows(summary_path):
            key = target_key(row)
            if not key[0] or not key[1]:
                continue
            normalized = dict(row)
            normalized["summary_path"] = str(summary_path)
            if key in results:
                duplicate_result_count += 1
            results[key] = normalized

    coverage_rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for target in target_rows:
        key = target_key(target)
        result = results.get(key)
        status = (result or {}).get("status", "MISSING")
        counts[status] = counts.get(status, 0) + 1
        coverage_rows.append(
            {
                "name": key[0],
                "rva": target.get("rva") or target.get("RVA") or "",
                "status": status,
                "output": (result or {}).get("output", ""),
                "summary_path": (result or {}).get("summary_path", ""),
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "rva", "status", "output", "summary_path"])
        writer.writeheader()
        writer.writerows(coverage_rows)

    target_count = len(target_rows)
    ok_count = counts.get("OK", 0)
    summary: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "targets_csv": str(targets_csv),
        "summary_paths": [str(path) for path in summary_paths],
        "output_csv": str(output_csv),
        "target_count": target_count,
        "ok_count": ok_count,
        "failed_count": sum(count for status, count in counts.items() if status not in {"OK", "MISSING"}),
        "missing_count": counts.get("MISSING", 0),
        "duplicate_result_count": duplicate_result_count,
        "status_counts": counts,
        "coverage_percent": round((ok_count / target_count * 100), 2) if target_count else 0,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, help="Original target CSV")
    parser.add_argument("--summary", action="append", default=[], help="decompile_summary.csv path; repeatable")
    parser.add_argument("--summary-root", action="append", default=[], help="Directory to search for decompile_summary.csv")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    summary_paths = discover_summary_paths([Path(value) for value in args.summary + args.summary_root])
    summary = merge_coverage(
        targets_csv=Path(args.targets),
        summary_paths=summary_paths,
        output_csv=Path(args.output_csv),
        output_json=Path(args.output_json),
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

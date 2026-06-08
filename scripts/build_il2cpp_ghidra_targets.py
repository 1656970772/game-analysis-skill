#!/usr/bin/env python3
"""Build Ghidra decompile target CSVs from Il2CppDumper dump.cs.

This is the default full-code route for Unity IL2CPP builds: use
Il2CppDumper's method names and RVAs to create no-BOM `name,rva` files, then
feed those files to `ghidra_decompile_targets_csv.py` in batches.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


RVA_RE = re.compile(r"//\s*RVA:\s*(0x[0-9A-Fa-f]+)")
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)")
TYPE_RE = re.compile(
    r"^\s*(?:public|private|protected|internal)?\s*"
    r"(?:(?:abstract|sealed|static|partial|unsafe|readonly)\s+)*"
    r"(?:class|struct|interface)\s+([A-Za-z_][\w.`]*)"
)
CONTROL_PREFIXES = ("if ", "for ", "foreach ", "while ", "switch ", "catch ", "using ", "lock ")


@dataclass(frozen=True)
class Target:
    name: str
    rva: str
    type_name: str
    method_name: str
    signature: str
    line: int


def strip_generic_suffix(name: str) -> str:
    return re.sub(r"<.*>$", "", name)


def extract_method_name(signature: str) -> str | None:
    text = signature.strip()
    if not text or text.startswith("//") or "(" not in text:
        return None
    if text.startswith(CONTROL_PREFIXES):
        return None
    before_args = text.split("(", 1)[0].strip()
    if not before_args:
        return None
    token = before_args.split()[-1].strip()
    token = strip_generic_suffix(token)
    token = token.strip("*&")
    if not token or token in {"operator"}:
        return None
    return token


def iter_targets(dump_cs: Path) -> tuple[list[Target], int]:
    targets: list[Target] = []
    namespace = ""
    current_type = ""
    pending_rva: tuple[str, int] | None = None
    skipped_zero_rva = 0

    with dump_cs.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            namespace_match = NAMESPACE_RE.match(line)
            if namespace_match:
                namespace = namespace_match.group(1)

            type_match = TYPE_RE.match(line)
            if type_match:
                current_type = type_match.group(1).replace("`", "_")

            rva_match = RVA_RE.search(line)
            if rva_match:
                pending_rva = (rva_match.group(1).upper().replace("X", "x"), line_no)
                continue

            if pending_rva is None:
                continue

            method_name = extract_method_name(line)
            if method_name is None:
                if line.strip() and not line.lstrip().startswith("["):
                    pending_rva = None
                continue

            rva, rva_line = pending_rva
            pending_rva = None
            if int(rva, 16) == 0:
                skipped_zero_rva += 1
                continue

            type_name = current_type or "Global"
            qualified_type = f"{namespace}.{type_name}" if namespace else type_name
            targets.append(
                Target(
                    name=f"{qualified_type}.{method_name}",
                    rva=rva,
                    type_name=qualified_type,
                    method_name=method_name,
                    signature=line.strip(),
                    line=rva_line,
                )
            )

    return targets, skipped_zero_rva


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns]


def keep_target(target: Target, includes: list[re.Pattern[str]], excludes: list[re.Pattern[str]]) -> bool:
    haystack = "\n".join([target.name, target.type_name, target.method_name, target.signature])
    if includes and not any(pattern.search(haystack) for pattern in includes):
        return False
    if excludes and any(pattern.search(haystack) for pattern in excludes):
        return False
    return True


def write_targets_csv(path: Path, targets: list[Target]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "rva"])
        writer.writeheader()
        for target in targets:
            writer.writerow({"name": target.name, "rva": target.rva})


def write_manifest(path: Path, targets: list[Target], summary: dict[str, object]) -> None:
    manifest = {
        "summary": summary,
        "targets": [asdict(target) for target in targets],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_batches(batch_dir: Path, targets: list[Target], batch_size: int) -> list[str]:
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_paths: list[str] = []
    for index, start in enumerate(range(0, len(targets), batch_size), start=1):
        batch = targets[start : start + batch_size]
        batch_path = batch_dir / f"targets_{index:04d}.csv"
        write_targets_csv(batch_path, batch)
        batch_paths.append(str(batch_path))
    return batch_paths


def build_targets(
    dump_cs: Path,
    output: Path,
    batch_dir: Path | None = None,
    batch_size: int = 500,
    include_regexes: list[str] | None = None,
    exclude_regexes: list[str] | None = None,
    manifest: Path | None = None,
) -> dict[str, object]:
    all_targets, skipped_zero_rva = iter_targets(dump_cs)
    includes = compile_patterns(include_regexes or [])
    excludes = compile_patterns(exclude_regexes or [])
    targets = [target for target in all_targets if keep_target(target, includes, excludes)]
    write_targets_csv(output, targets)

    batch_paths: list[str] = []
    if batch_dir is not None:
        batch_paths = write_batches(batch_dir, targets, batch_size)

    summary: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dump_cs": str(dump_cs),
        "target_csv": str(output),
        "total_method_rva_count": len(all_targets),
        "target_count": len(targets),
        "skipped_zero_rva_count": skipped_zero_rva,
        "batch_size": batch_size,
        "batch_count": len(batch_paths),
        "batch_paths": batch_paths,
        "include_regexes": include_regexes or [],
        "exclude_regexes": exclude_regexes or [],
    }

    if manifest is not None:
        write_manifest(manifest, targets, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-cs", required=True, help="Il2CppDumper dump.cs path")
    parser.add_argument("--output", required=True, help="Output no-BOM name,rva CSV")
    parser.add_argument("--batch-dir", help="Optional directory for targets_0001.csv batches")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--include-regex", action="append", default=[], help="Keep methods matching this regex; repeatable")
    parser.add_argument("--exclude-regex", action="append", default=[], help="Drop methods matching this regex; repeatable")
    parser.add_argument("--manifest", help="Optional detailed JSON manifest")
    args = parser.parse_args()

    summary = build_targets(
        dump_cs=Path(args.dump_cs),
        output=Path(args.output),
        batch_dir=Path(args.batch_dir) if args.batch_dir else None,
        batch_size=args.batch_size,
        include_regexes=args.include_regex,
        exclude_regexes=args.exclude_regex,
        manifest=Path(args.manifest) if args.manifest else None,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

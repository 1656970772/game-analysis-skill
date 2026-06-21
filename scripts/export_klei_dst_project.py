#!/usr/bin/env python3
"""Export and index Don't Starve Together/Klei package layouts.

This script intentionally avoids converting proprietary Klei KTEX/DYN/FSB
payloads unless a trusted converter is configured. It extracts readable ZIP
packages and Lua scripts, copies original package structures into the study
root, and writes conversion blockers for unknown/proprietary formats.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


PACKAGE_EXTS = {".zip"}
TEXT_CODE_EXTS = {".lua"}
IMAGE_METADATA_EXTS = {".xml"}
KLEI_TEXTURE_EXTS = {".tex"}
KLEI_DYNAMIC_EXTS = {".dyn"}
AUDIO_EXTS = {".wav", ".fsb", ".fev"}
RENDER_EXTS = {".fx", ".shader"}


@dataclass
class FileRecord:
    source: str
    output: str
    category: str
    size: int
    sha256: str
    status: str
    note: str


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def safe_join(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*Path(relative.replace("\\", "/")).parts)
    resolved = candidate.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"unsafe archive member path: {relative}") from exc
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def classify_image_metadata(path: str) -> str:
    lower = path.lower()
    if "inventory" in lower or "cookbook" in lower or "servericon" in lower:
        return "UI"
    if "portrait" in lower or "names_" in lower or "bigportrait" in lower:
        return "角色立绘"
    if "bg_" in lower or "loading" in lower or "map" in lower:
        return "场景"
    return "图集"


def extract_zip(src: Path, dst: Path, records: list[FileRecord], source_root: Path, tag: str) -> Counter:
    counts: Counter = Counter()
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            out = safe_join(dst, info.filename)
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as rf, out.open("wb") as wf:
                shutil.copyfileobj(rf, wf)
            ext = Path(info.filename).suffix.lower()
            counts[ext or "[none]"] += 1
            records.append(
                FileRecord(
                    source=f"{relpath(src, source_root)}::{info.filename}",
                    output=str(out),
                    category=tag,
                    size=info.file_size,
                    sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                    status="extracted",
                    note="zip member",
                )
            )
    return counts


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--project-name", default="DontStarveTogether")
    parser.add_argument("--max-audio-samples", type=int, default=200)
    args = parser.parse_args(argv)

    source = args.source.resolve()
    study = args.study_root.resolve()
    if not source.is_dir():
        raise SystemExit(f"source does not exist: {source}")

    raw_root = study / "0.原始导出" / "KleiDST"
    asset_root = study / "1.分类资源"
    code_root = study / "3.代码"
    temp_root = study / "4.临时目录"
    index_root = temp_root / "中间索引" / "KleiDST"
    failure_root = temp_root / "失败记录"
    log_root = temp_root / "工具日志" / "KleiDST"
    for path in [raw_root, index_root, failure_root, log_root]:
        path.mkdir(parents=True, exist_ok=True)

    records: list[FileRecord] = []
    package_rows: list[dict] = []
    format_rows: list[dict] = []
    code_rows: list[dict] = []
    atlas_rows: list[dict] = []
    audio_rows: list[dict] = []
    dyn_rows: list[dict] = []

    top_counter: Counter = Counter()
    package_member_counter: Counter = Counter()
    package_name_counter: Counter = Counter()

    for src in iter_files(source):
        rel = relpath(src, source)
        ext = src.suffix.lower()
        top_counter[ext or "[none]"] += 1
        size = src.stat().st_size

        if ext in PACKAGE_EXTS:
            package_name_counter[src.parent.name] += 1
            raw_dst = raw_root / "packages" / rel
            copy_file(src, raw_dst)
            status = "copied"
            note = "original zip copied"
            member_count = 0
            member_exts: Counter = Counter()
            try:
                extract_dst = raw_root / "packages_extracted" / rel.removesuffix(".zip")
                member_exts = extract_zip(src, extract_dst, records, source, "zip_member")
                package_member_counter.update(member_exts)
                member_count = sum(member_exts.values())
                status = "copied_and_extracted"
            except Exception as exc:  # noqa: BLE001 - record package-level failures.
                note = f"zip extract failed: {exc}"
            package_rows.append(
                {
                    "source": rel,
                    "size": size,
                    "sha256": sha256_file(src),
                    "status": status,
                    "member_count": member_count,
                    "top_member_extensions": json.dumps(member_exts.most_common(12), ensure_ascii=False),
                    "note": note,
                }
            )
            records.append(FileRecord(rel, str(raw_dst), "package", size, sha256_file(src), status, note))
            continue

        if ext in TEXT_CODE_EXTS:
            dst = code_root / "脚本" / rel
            copy_file(src, dst)
            code_rows.append({"source": rel, "output": str(dst), "size": size, "status": "copied"})
            records.append(FileRecord(rel, str(dst), "code", size, sha256_file(src), "copied", "loose Lua script"))
            continue

        if ext in AUDIO_EXTS:
            # WAV files are directly usable samples; FSB/FEV are copied as real audio bank assets.
            bucket = "WAV样本" if ext == ".wav" else "FMOD"
            dst = asset_root / "音频" / bucket / rel
            copy_file(src, dst)
            audio_rows.append(
                {"source": rel, "output": str(dst), "extension": ext, "size": size, "status": "copied"}
            )
            records.append(FileRecord(rel, str(dst), "audio", size, sha256_file(src), "copied", bucket))
            continue

        if ext in KLEI_TEXTURE_EXTS:
            dst = raw_root / "textures_ktex" / rel
            copy_file(src, dst)
            with src.open("rb") as fh:
                magic = fh.read(16).hex()
            atlas_rows.append(
                {
                    "source": rel,
                    "raw_output": str(dst),
                    "category_hint": classify_image_metadata(rel),
                    "size": size,
                    "magic16": magic,
                    "status": "raw_copied_conversion_blocked",
                    "note": "KTEX requires trusted ktech/TexTools or equivalent converter for PNG output",
                }
            )
            records.append(FileRecord(rel, str(dst), "ktex", size, sha256_file(src), "raw_copied", "conversion blocked"))
            continue

        if ext in KLEI_DYNAMIC_EXTS:
            dst = raw_root / "dynamic_dyn" / rel
            copy_file(src, dst)
            with src.open("rb") as fh:
                magic = fh.read(16).hex()
            dyn_rows.append(
                {
                    "source": rel,
                    "raw_output": str(dst),
                    "size": size,
                    "magic16": magic,
                    "status": "raw_copied_conversion_blocked",
                    "note": "DLC dynamic animation payload; not a plain ZIP in this sample",
                }
            )
            records.append(FileRecord(rel, str(dst), "dyn", size, sha256_file(src), "raw_copied", "conversion blocked"))
            continue

        if ext in IMAGE_METADATA_EXTS:
            dst = raw_root / "metadata_xml" / rel
            copy_file(src, dst)
            atlas_rows.append(
                {
                    "source": rel,
                    "raw_output": str(dst),
                    "category_hint": classify_image_metadata(rel),
                    "size": size,
                    "magic16": "",
                    "status": "metadata_copied",
                    "note": "atlas/xml metadata; texture conversion handled separately",
                }
            )
            records.append(FileRecord(rel, str(dst), "xml", size, sha256_file(src), "copied", "atlas metadata"))
            continue

    # Promote extracted readable Lua from packages into code output.
    package_extract_root = raw_root / "packages_extracted"
    if package_extract_root.is_dir():
        for lua in package_extract_root.rglob("*.lua"):
            try:
                rel = lua.relative_to(package_extract_root).as_posix()
            except ValueError:
                rel = lua.name
            dst = code_root / "脚本" / "packages" / rel
            copy_file(lua, dst)
            code_rows.append(
                {"source": f"0.原始导出/KleiDST/packages_extracted/{rel}", "output": str(dst), "size": lua.stat().st_size, "status": "copied_from_package"}
            )

        for xml in package_extract_root.rglob("*.xml"):
            rel = xml.relative_to(package_extract_root).as_posix()
            bucket = classify_image_metadata(rel)
            atlas_rows.append(
                {
                    "source": f"0.原始导出/KleiDST/packages_extracted/{rel}",
                    "raw_output": str(xml),
                    "category_hint": bucket,
                    "size": xml.stat().st_size,
                    "magic16": "",
                    "status": "atlas_xml_indexed",
                    "note": "XML atlas metadata remains in raw export; classification awaits KTEX -> PNG conversion",
                }
            )

        for tex in package_extract_root.rglob("*.tex"):
            rel = tex.relative_to(package_extract_root).as_posix()
            with tex.open("rb") as fh:
                magic = fh.read(16).hex()
            atlas_rows.append(
                {
                    "source": f"0.原始导出/KleiDST/packages_extracted/{rel}",
                    "raw_output": str(tex),
                    "category_hint": classify_image_metadata(rel),
                    "size": tex.stat().st_size,
                    "magic16": magic,
                    "status": "ktex_indexed_conversion_blocked",
                    "note": "KTEX remains in raw export; requires trusted ktech/TexTools or equivalent converter for PNG output",
                }
            )

    write_csv(index_root / "包体索引.csv", package_rows, ["source", "size", "sha256", "status", "member_count", "top_member_extensions", "note"])
    write_csv(index_root / "代码导出索引.csv", code_rows, ["source", "output", "size", "status"])
    write_csv(index_root / "图片纹理索引.csv", atlas_rows, ["source", "raw_output", "category_hint", "size", "magic16", "status", "note"])
    write_csv(index_root / "音频索引.csv", audio_rows, ["source", "output", "extension", "size", "status"])
    write_csv(index_root / "动态动画阻塞索引.csv", dyn_rows, ["source", "raw_output", "size", "magic16", "status", "note"])
    write_csv(index_root / "文件导出明细.csv", [asdict(r) for r in records], ["source", "output", "category", "size", "sha256", "status", "note"])

    summary = {
        "source": str(source),
        "study_root": str(study),
        "project_name": args.project_name,
        "top_extensions": top_counter.most_common(),
        "package_parent_counts": package_name_counter.most_common(),
        "package_member_extensions": package_member_counter.most_common(),
        "packages": len(package_rows),
        "code_files": len(code_rows),
        "atlas_or_texture_records": len(atlas_rows),
        "audio_records": len(audio_rows),
        "dyn_records": len(dyn_rows),
        "status": "DONE_WITH_CONCERNS",
        "concerns": [
            "KTEX textures were copied and indexed but not converted to PNG because no trusted local ktech/TexTools converter is configured.",
            "DYN animation payloads were copied and indexed but not decoded; sampled files were not plain ZIP archives.",
            "FSB/FEV FMOD banks were copied as real audio bank assets but not decoded into per-event samples.",
        ],
    }
    write_json(index_root / "klei_dst_export_summary.json", summary)

    blocker = """# Klei DST 转换阻塞记录

## 观察到的事实

- 源目录包含 `KTEX` 魔数的 `.tex` 纹理文件，原始文件已复制到 `0.原始导出/KleiDST/textures_ktex/`，索引见 `4.临时目录/中间索引/KleiDST/图片纹理索引.csv`。
- 源目录包含 `.dyn` 动态动画文件，小样本魔数不是 `PK` ZIP，本次只复制原始文件并索引，见 `4.临时目录/中间索引/KleiDST/动态动画阻塞索引.csv`。
- 源目录包含 FMOD `.fsb/.fev`，本次复制为真实音频 bank 资产，未拆分事件样本。

## 我的判断

- 需要接入可信 Klei 工具链：`ktech` 用于 KTEX -> PNG，`krane` 用于 `anim.bin/build.bin/atlas-0.tex` -> Spriter/SCML。
- 需要接入可信 FMOD/vgmstream 路线后，才能把 `.fsb` 进一步拆成单条可听样本。

## 待验证假设

- 部分 `.dyn` 是 DLC/皮肤动态动画加密或专有封装，不能按普通 ZIP 解包。
- `images.zip` 与 `data/anim/*.zip` 中的 XML/KTEX 可以在接入 `ktech` 后切成 PNG 图集或序列帧。
"""
    (failure_root / "KleiDST转换阻塞.md").write_text(blocker, encoding="utf-8")
    write_json(log_root / "执行摘要.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

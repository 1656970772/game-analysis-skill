#!/usr/bin/env python3
"""Convert Don't Starve Together resources after package extraction.

Outputs obey the GameAnalysis study-root directory contract:
- PNG textures go to 1.分类资源/图片/图集/KleiTEX
- rebuilt FSB samples go to 1.分类资源/音频/KleiFSB
- animation XML decompilation goes to 4.临时目录/中间索引/Klei动画XML
- CSV/JSONL logs stay under 4.临时目录
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name)
    return cleaned.strip(" .") or "unnamed"


def rel_no_suffix(path: Path, root: Path) -> Path:
    rel = path.relative_to(root)
    return rel.with_suffix("")


def ensure_dirs(study_root: Path) -> dict[str, Path]:
    dirs = {
        "tex_out": study_root / "1.分类资源" / "图片" / "图集" / "KleiTEX",
        "audio_out": study_root / "1.分类资源" / "音频" / "KleiFSB",
        "anim_xml": study_root / "4.临时目录" / "中间索引" / "Klei动画XML",
        "indices": study_root / "4.临时目录" / "中间索引" / "学习输出",
        "logs": study_root / "4.临时目录" / "工具日志",
        "failures": study_root / "4.临时目录" / "失败记录",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def run_process(args: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def convert_textures(study_root: Path, stex: Path, limit: int, force: bool, dirs: dict[str, Path]) -> dict[str, int]:
    raw_root = study_root / "0.原始导出" / "KleiDST" / "packages_extracted"
    tex_files = sorted(raw_root.rglob("*.tex")) if raw_root.exists() else []
    if limit > 0:
        tex_files = tex_files[:limit]

    rows = []
    errors_path = dirs["failures"] / "klei_texture_conversion_errors.jsonl"
    converted = skipped = failed = 0

    with errors_path.open("w", encoding="utf-8") as err:
        for src in tex_files:
            rel = rel_no_suffix(src, raw_root)
            dst = dirs["tex_out"] / rel.with_suffix(".png")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and not force:
                skipped += 1
                status = "skipped"
                message = ""
            else:
                code, out = run_process([str(stex), "decompress", "-i", str(src), "-o", str(dst)], timeout=180)
                status = "converted" if code == 0 and dst.exists() else "failed"
                message = out.strip().splitlines()[-1] if out.strip() else ""
                if status == "converted":
                    converted += 1
                else:
                    failed += 1
                    err.write(json.dumps({"source": str(src), "output": str(dst), "exit": code, "log": out}, ensure_ascii=False) + "\n")
            rows.append({"source": str(src), "output": str(dst), "status": status, "message": message})

    index_path = dirs["indices"] / "klei_texture_conversion_index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "output", "status", "message"])
        writer.writeheader()
        writer.writerows(rows)

    return {"total": len(tex_files), "converted": converted, "skipped": skipped, "failed": failed}


def convert_animations(study_root: Path, tool_dir: Path, limit: int, force: bool, dirs: dict[str, Path]) -> dict[str, int]:
    anim_root = study_root / "0.原始导出" / "KleiDST" / "packages_extracted" / "data" / "anim"
    build_script = tool_dir / "build_decompiler.py"
    anim_script = tool_dir / "anim_decompiler.py"
    candidates = []
    if anim_root.exists():
        for folder in sorted({p.parent for p in anim_root.rglob("*.bin")}):
            if (folder / "build.bin").exists() or (folder / "anim.bin").exists():
                candidates.append(folder)
    if limit > 0:
        candidates = candidates[:limit]

    rows = []
    errors_path = dirs["failures"] / "klei_animation_xml_errors.jsonl"
    converted = skipped = failed = 0

    with errors_path.open("w", encoding="utf-8") as err:
        for folder in candidates:
            rel = folder.relative_to(anim_root)
            out_dir = dirs["anim_xml"] / rel
            expected = []
            if (folder / "build.bin").exists():
                expected.append(out_dir / "build.xml")
            if (folder / "anim.bin").exists():
                expected.append(out_dir / "anim.xml")
            if expected and all(p.exists() for p in expected) and not force:
                skipped += 1
                rows.append({"source": str(folder), "output": str(out_dir), "status": "skipped", "message": ""})
                continue

            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("build.bin", "anim.bin"):
                src = folder / name
                if src.exists():
                    shutil.copy2(src, out_dir / name)
            messages = []
            ok = True
            if (out_dir / "build.bin").exists():
                code, out = run_process([sys.executable, str(build_script), str(out_dir)], timeout=120)
                ok = ok and code == 0 and (out_dir / "build.xml").exists()
                messages.append(out.strip().splitlines()[-1] if out.strip() else f"build_exit={code}")
            if (out_dir / "anim.bin").exists():
                code, out = run_process([sys.executable, str(anim_script), str(out_dir)], timeout=120)
                ok = ok and code == 0 and (out_dir / "anim.xml").exists()
                messages.append(out.strip().splitlines()[-1] if out.strip() else f"anim_exit={code}")
            if ok:
                converted += 1
                status = "converted"
            else:
                failed += 1
                status = "failed"
                err.write(json.dumps({"source": str(folder), "output": str(out_dir), "messages": messages}, ensure_ascii=False) + "\n")
            rows.append({"source": str(folder), "output": str(out_dir), "status": status, "message": " | ".join(messages)})

    index_path = dirs["indices"] / "klei_animation_xml_index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "output", "status", "message"])
        writer.writeheader()
        writer.writerows(rows)

    return {"total": len(candidates), "converted": converted, "skipped": skipped, "failed": failed}


def load_fsb5(fsb_pkg: Path):
    os.chdir(fsb_pkg)
    sys.path.insert(0, str(fsb_pkg))
    import fsb5  # type: ignore

    return fsb5


def patch_file_once(path: Path, replacements: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text
    for old, new in replacements:
        updated = updated.replace(old, new)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def ensure_dst_anim_tool_py3(tool_dir: Path) -> None:
    build_script = tool_dir / "build_decompiler.py"
    anim_script = tool_dir / "anim_decompiler.py"
    helper = (
        "\n\n"
        "def xml_text(value):\n"
        "    if isinstance(value, bytes):\n"
        "        return value.decode(\"utf-8\", errors=\"replace\")\n"
        "    return str(value)\n\n"
    )
    for script in (build_script, anim_script):
        text = script.read_text(encoding="utf-8")
        if "def xml_text(value):" not in text:
            marker = "\n\ndef Decompile"
            text = text.replace(marker, helper + marker.lstrip("\n"), 1)
            script.write_text(text, encoding="utf-8")

    patch_file_once(
        build_script,
        [
            ('hashcollection[str(hash_id)] = hash_str', 'hashcollection[str(hash_id)] = xml_text(hash_str)'),
            ('node.setAttribute("name", hashcollection[hash_id])', 'node.setAttribute("name", xml_text(hashcollection[hash_id]))'),
            ('open(os.path.join(workspace, "build.xml"), "wb")', 'open(os.path.join(workspace, "build.xml"), "w", encoding="utf-8")'),
            ('open(os.path.join(workspace, texture_node.getAttribute("filename")[:-4] + ".xml"), "wb")', 'open(os.path.join(workspace, texture_node.getAttribute("filename")[:-4] + ".xml"), "w", encoding="utf-8")'),
        ],
    )
    patch_file_once(
        anim_script,
        [
            ("anim_node.setAttribute('name', str(anim_name) + dir[facingbyte])", "facing_suffix = dir.get(facingbyte, \"_facing_%02x\" % facingbyte)\n        anim_node.setAttribute('name', xml_text(anim_name) + facing_suffix)"),
            ("anim_node.setAttribute('name', xml_text(anim_name) + dir[facingbyte])", "facing_suffix = dir.get(facingbyte, \"_facing_%02x\" % facingbyte)\n        anim_node.setAttribute('name', xml_text(anim_name) + facing_suffix)"),
            ('anim_node.setAttribute("root", hash)', 'anim_node.setAttribute("root", str(hash))'),
            ('frame_event_node.setAttribute("name", frame_event_name_hash)', 'frame_event_node.setAttribute("name", str(frame_event_name_hash))'),
            ('elements_node.setAttribute("name", element_name_hash)', 'elements_node.setAttribute("name", str(element_name_hash))'),
            ('elements_node.setAttribute("layername", layernamehash)', 'elements_node.setAttribute("layername", str(layernamehash))'),
            ('hashcollection[hashid] = hashstr', 'hashcollection[str(hashid)] = xml_text(hashstr)'),
            ('open(os.path.join(workspace, "anim.xml"), "wb")', 'open(os.path.join(workspace, "anim.xml"), "w", encoding="utf-8")'),
        ],
    )


def decode_bank_with_vgmstream(vgmstream: Path, bank_path: Path, out_dir: Path, force: bool) -> tuple[bool, int, int, str]:
    if out_dir.exists() and force:
        for child in out_dir.glob("*.wav"):
            child.unlink()
    before = {p.name for p in out_dir.glob("*.wav")}
    code, out = run_process(
        [str(vgmstream), "-i", "-S", "0", "-o", str(out_dir / "?s_?n.wav"), str(bank_path)],
        cwd=vgmstream.parent,
        timeout=300,
    )
    after = {p.name for p in out_dir.glob("*.wav")}
    created = len(after - before)
    return code == 0 and bool(after), created, len(after), out


def convert_audio(game_root: Path, fsb_pkg: Path, vgmstream: Path | None, limit_banks: int, limit_samples: int, force: bool, dirs: dict[str, Path]) -> dict[str, int]:
    sound_root = game_root / "data" / "sound"
    fsb_files = sorted(sound_root.glob("*.fsb")) if sound_root.exists() else []
    if limit_banks > 0:
        fsb_files = fsb_files[:limit_banks]
    fsb5 = load_fsb5(fsb_pkg)

    rows = []
    errors_path = dirs["failures"] / "klei_fsb_conversion_errors.jsonl"
    banks_done = banks_failed = samples_written = samples_skipped = samples_failed = 0

    with errors_path.open("w", encoding="utf-8") as err:
        for bank_path in fsb_files:
            bank_name = safe_name(bank_path.stem)
            out_dir = dirs["audio_out"] / bank_name
            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                bank = fsb5.load(bank_path.read_bytes())
                ext = bank.get_sample_extension()
                sample_list = bank.samples[:limit_samples] if limit_samples > 0 else bank.samples
                for idx, sample in enumerate(sample_list):
                    sample_name = safe_name(sample.name or f"{idx:04d}")
                    out_path = out_dir / f"{idx:04d}_{sample_name}.{ext}"
                    if out_path.exists() and not force:
                        samples_skipped += 1
                        status = "skipped"
                    else:
                        try:
                            out_path.write_bytes(bank.rebuild_sample(sample))
                            samples_written += 1
                            status = "converted"
                        except Exception as exc:  # keep batch going; record exact failing sample.
                            samples_failed += 1
                            status = "failed"
                            err.write(json.dumps({"bank": str(bank_path), "sample": idx, "name": sample.name, "error": str(exc)}, ensure_ascii=False) + "\n")
                    rows.append({"bank": str(bank_path), "sample_index": idx, "sample_name": sample.name, "output": str(out_path), "status": status})
                banks_done += 1
            except Exception as exc:
                if vgmstream and vgmstream.exists() and limit_samples == 0:
                    ok, created, total_wavs, log = decode_bank_with_vgmstream(vgmstream, bank_path, out_dir, force)
                    if ok:
                        banks_done += 1
                        samples_written += created
                        samples_skipped += max(0, total_wavs - created)
                        rows.append({"bank": str(bank_path), "sample_index": "", "sample_name": "vgmstream fallback", "output": str(out_dir), "status": "converted"})
                    else:
                        banks_failed += 1
                        err.write(json.dumps({"bank": str(bank_path), "error": str(exc), "fallback": log}, ensure_ascii=False) + "\n")
                else:
                    banks_failed += 1
                    err.write(json.dumps({"bank": str(bank_path), "error": str(exc)}, ensure_ascii=False) + "\n")

    index_path = dirs["indices"] / "klei_fsb_conversion_index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["bank", "sample_index", "sample_name", "output", "status"])
        writer.writeheader()
        writer.writerows(rows)

    return {
        "banks_total": len(fsb_files),
        "banks_converted": banks_done,
        "banks_failed": banks_failed,
        "samples_converted": samples_written,
        "samples_skipped": samples_skipped,
        "samples_failed": samples_failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study-root", required=True, type=Path)
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--skill-root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--max-tex", type=int, default=0, help="0 means all")
    parser.add_argument("--max-animations", type=int, default=0, help="0 means all")
    parser.add_argument("--max-fsb-banks", type=int, default=0, help="0 means all")
    parser.add_argument("--max-fsb-samples-per-bank", type=int, default=0, help="0 means all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    study_root = args.study_root.resolve()
    skill_root = args.skill_root.resolve()
    dirs = ensure_dirs(study_root)

    stex = skill_root / "tools" / "Stex_v0.6_Windows_Static_x64" / "bin" / "Stex.exe"
    anim_tool = skill_root / "tools" / "DstAnimTool_main"
    fsb_pkg = skill_root / "tools" / "python-fsb5_win64" / "python-fsb5"
    vgmstream = skill_root / "tools" / "vgmstream-win64-r2117" / "vgmstream-cli.exe"
    missing = [str(p) for p in (stex, anim_tool / "build_decompiler.py", anim_tool / "anim_decompiler.py", fsb_pkg / "fsb5" / "__init__.py") if not p.exists()]
    if missing:
        raise SystemExit("Missing conversion tools: " + "; ".join(missing))
    ensure_dst_anim_tool_py3(anim_tool)

    summary = {
        "textures": convert_textures(study_root, stex, args.max_tex, args.force, dirs),
        "animations": convert_animations(study_root, anim_tool, args.max_animations, args.force, dirs),
        "audio": convert_audio(args.game_root.resolve(), fsb_pkg, vgmstream, args.max_fsb_banks, args.max_fsb_samples_per_bank, args.force, dirs),
    }
    summary_path = dirs["logs"] / "klei_resource_conversion_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

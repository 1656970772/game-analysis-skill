#!/usr/bin/env python3
"""Detect likely game engines from local filenames and directory markers.

This script is read-only. It does not download, unpack, extract, or parse
packaged game resources.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "Library",
    "Temp",
    "obj",
    "bin",
}


@dataclass(frozen=True)
class Marker:
    pattern: str
    weight: int
    note: str


@dataclass
class EngineScore:
    engine: str
    score: int = 0
    markers: list[dict[str, object]] = field(default_factory=list)

    def add(self, marker: Marker, relpath: str) -> None:
        self.score += marker.weight
        self.markers.append(
            {
                "path": relpath,
                "pattern": marker.pattern,
                "weight": marker.weight,
                "note": marker.note,
            }
        )


ENGINE_MARKERS: dict[str, list[Marker]] = {
    "Unity": [
        Marker("ProjectSettings/ProjectVersion.txt", 35, "Unity project version file"),
        Marker("Packages/manifest.json", 20, "Unity package manifest"),
        Marker("Assets/*.unity", 18, "Unity scene file"),
        Marker("Assets/**/*.prefab", 14, "Unity prefab"),
        Marker("*_Data/Managed/Assembly-CSharp.dll", 30, "Unity Windows build managed assembly"),
        Marker("*_Data/globalgamemanagers", 25, "Unity player data marker"),
        Marker("UnityPlayer.dll", 22, "Unity player runtime"),
        Marker("*.asset", 6, "Unity-style asset extension"),
    ],
    "Unreal Engine": [
        Marker("*.uproject", 40, "Unreal project descriptor"),
        Marker("Config/DefaultEngine.ini", 25, "Unreal engine config"),
        Marker("*/Config/DefaultEngine.ini", 25, "Nested Unreal engine config"),
        Marker("Content/**/*.uasset", 18, "Unreal asset file"),
        Marker("Content/**/*.umap", 18, "Unreal map file"),
        Marker("Content/Paks/*.pak", 28, "Unreal packaged content marker"),
        Marker("*/Content/Paks/*.pak", 30, "Nested Unreal packaged content marker"),
        Marker("Binaries/Win64/*.exe", 8, "Unreal Windows binary layout"),
        Marker("*/Binaries/Win64/*-Win64-Shipping.exe", 18, "Nested Unreal Windows shipping binary"),
        Marker("Engine/Extras/Redist/en-us/UE4PrereqSetup_x64.exe", 18, "Unreal Engine 4 prerequisite installer"),
    ],
    "Godot": [
        Marker("project.godot", 45, "Godot project file"),
        Marker("export_presets.cfg", 20, "Godot export presets"),
        Marker("*.tscn", 14, "Godot text scene"),
        Marker("*.tres", 12, "Godot text resource"),
        Marker("*.gd", 10, "Godot script"),
        Marker("data.pck", 25, "Godot exported PCK marker"),
        Marker("*.pck", 18, "Godot resource pack marker"),
    ],
    "Ren'Py": [
        Marker("game/*.rpy", 35, "Ren'Py script"),
        Marker("game/*.rpyc", 18, "Ren'Py compiled script"),
        Marker("game/*.rpa", 24, "Ren'Py archive marker"),
        Marker("renpy/**", 22, "Ren'Py engine directory"),
        Marker("*.rpy", 18, "Ren'Py script outside standard layout"),
    ],
    "GameMaker": [
        Marker("*.yyp", 42, "GameMaker project file"),
        Marker("options/main/options_main.yy", 25, "GameMaker options file"),
        Marker("objects/**/*.yy", 15, "GameMaker object resource"),
        Marker("data.win", 25, "GameMaker Windows data marker"),
    ],
    "RPG Maker MV/MZ": [
        Marker("Game.rpgproject", 42, "RPG Maker project marker"),
        Marker("www/js/rpg_core.js", 30, "RPG Maker MV runtime"),
        Marker("www/js/rmmz_core.js", 32, "RPG Maker MZ runtime"),
        Marker("www/data/System.json", 24, "RPG Maker data layout"),
        Marker("img/characters/**", 10, "RPG Maker character asset layout"),
    ],
    "LOVE": [
        Marker("main.lua", 30, "LOVE main script"),
        Marker("conf.lua", 20, "LOVE config script"),
        Marker("*.love", 35, "LOVE packaged game marker"),
    ],
    "Defold": [
        Marker("game.project", 42, "Defold project file"),
        Marker("*.collection", 18, "Defold collection"),
        Marker("*.go", 12, "Defold game object"),
        Marker("*.script", 10, "Defold script"),
    ],
    "Construct": [
        Marker("*.c3proj", 42, "Construct 3 project file"),
        Marker("c2runtime.js", 24, "Construct 2 runtime"),
        Marker("data.js", 8, "Construct-style exported data marker"),
    ],
    "MonoGame/FNA": [
        Marker("Content.mgcb", 36, "MonoGame content builder file"),
        Marker("*.xnb", 18, "XNA/MonoGame compiled content"),
        Marker("MonoGame.Framework.dll", 28, "MonoGame runtime"),
        Marker("FNA.dll", 28, "FNA runtime"),
    ],
}


def normalize_rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel.strip("/")


def pattern_matches(pattern: str, relpath: str) -> bool:
    pattern_l = pattern.lower()
    rel_l = relpath.lower()
    if fnmatch.fnmatchcase(rel_l, pattern_l):
        return True
    if "**/" in pattern_l:
        return fnmatch.fnmatchcase(rel_l, pattern_l.replace("**/", ""))
    return False


def iter_local_paths(root: Path, max_depth: int, max_files: int) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    yielded = 0
    root_parts = len(root.parts)
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        depth = len(current_path.parts) - root_parts
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and depth < max_depth]
        for name in files:
            yield current_path / name
            yielded += 1
            if yielded >= max_files:
                return


def detect(root: Path, max_depth: int, max_files: int) -> dict[str, object]:
    root = root.resolve()
    scores = {engine: EngineScore(engine=engine) for engine in ENGINE_MARKERS}
    scanned = 0

    for path in iter_local_paths(root, max_depth=max_depth, max_files=max_files):
        scanned += 1
        try:
            relpath = normalize_rel(path.resolve(), root if root.is_dir() else root.parent)
        except ValueError:
            relpath = path.name
        for engine, markers in ENGINE_MARKERS.items():
            for marker in markers:
                if pattern_matches(marker.pattern, relpath):
                    scores[engine].add(marker, relpath)

    ranked = sorted(
        (score for score in scores.values() if score.score > 0),
        key=lambda item: item.score,
        reverse=True,
    )
    top_score = ranked[0].score if ranked else 0
    results = []
    for item in ranked:
        confidence = round(item.score / top_score, 3) if top_score else 0
        results.append(
            {
                "engine": item.engine,
                "score": item.score,
                "relative_confidence": confidence,
                "markers": item.markers[:20],
            }
        )

    return {
        "root": str(root),
        "scanned_files": scanned,
        "max_depth": max_depth,
        "max_files": max_files,
        "results": results,
        "notice": "Read-only filename/layout heuristic; packaged files are not opened or extracted.",
    }


def to_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Engine Detection Report",
        "",
        f"- Root: `{report['root']}`",
        f"- Scanned files: {report['scanned_files']}",
        f"- Notice: {report['notice']}",
        "",
    ]
    results = report["results"]
    if not results:
        lines.append("No engine markers found.")
        return "\n".join(lines)

    lines.extend(["| Engine | Score | Relative confidence | Top markers |", "|---|---:|---:|---|"])
    for item in results:  # type: ignore[assignment]
        markers = item["markers"][:5]
        marker_text = "<br>".join(
            f"`{m['path']}` ({m['note']}, +{m['weight']})" for m in markers
        )
        lines.append(
            f"| {item['engine']} | {item['score']} | {item['relative_confidence']} | {marker_text} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Local file or folder to inspect.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-files", type=int, default=20000)
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        parser.error(f"path does not exist: {root}")

    report = detect(root, max_depth=args.max_depth, max_files=args.max_files)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

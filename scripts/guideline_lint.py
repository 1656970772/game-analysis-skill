#!/usr/bin/env python3
"""Lint art-direction guidelines for source and style-boundary issues."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


RISK_PATTERNS = [
    (re.compile(r"\b(extract|extracted|extraction|rip|ripped|ripping|decompile|unpack)\b", re.I), "resource-extraction"),
    (re.compile(r"\b(asset\s*ripper|uabe|umodel|quickbms)\b", re.I), "extraction-tool"),
    (re.compile(r"\b(copy|clone|duplicate)\b.{0,30}\b(exactly|exact|1:1|one-to-one)\b", re.I), "copying"),
    (re.compile(r"\buse\b.{0,20}\b(original|ripped|extracted)\b.{0,20}\b(asset|sprite|texture|sound|ui)\b", re.I), "asset-reuse"),
    (re.compile(r"(提取|解包|反编译|扒取|扒图|扒素材|拆包)"), "zh-extraction-term"),
    (re.compile(r"(直接使用|原封不动|一比一|复刻|照抄)"), "zh-copying-term"),
]

NEGATION_HINTS = [
    "do not",
    "don't",
    "never",
    "avoid",
    "forbid",
    "prohibit",
    "禁止",
    "不要",
    "不得",
    "不应",
    "避免",
    "不能",
    "不可",
    "未要求",
    "没有",
    "禁止",
    "不下载",
    "不解包",
    "不提取",
    "不复用",
    "风险表达",
    "风险措辞",
    "检查",
]

AUTHORIZED_LOCAL_HINTS = [
    "user-authorized",
    "authorized local",
    "local-only",
    "本机学习",
    "本地授权",
    "用户授权",
    "授权路径",
    "授权目录",
    "只读",
    "学习目录",
    "本地路径",
]

REQUIRED_THEMES = {
    "sources": ["source", "sources", "来源", "引用"],
    "boundary": ["boundary", "copyright", "trademark", "授权", "边界", "不可复制"],
    "originality": ["original", "原创", "原创化", "可迁移"],
    "palette": ["palette", "color", "色彩", "调色"],
    "shape": ["shape", "silhouette", "形状", "轮廓"],
    "readability": ["readability", "ui", "hud", "可读", "界面"],
    "constraints": ["constraint", "production", "规格", "约束", "制作"],
}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def is_negated(line: str) -> bool:
    lower = line.lower()
    return any(hint in lower for hint in NEGATION_HINTS)


def is_authorized_local_context(line: str) -> bool:
    lower = line.lower()
    return any(hint.lower() in lower for hint in AUTHORIZED_LOCAL_HINTS)


def lint_text(text: str) -> dict[str, object]:
    issues = []
    warnings = []
    info = []
    lines = text.splitlines()
    lower_text = text.lower()
    authorized_document = (
        ("用户明确授权" in text or "user-authorized" in lower_text or "授权路径" in text)
        and ("本机学习" in text or "local" in lower_text or "本地" in text)
    )

    for index, line in enumerate(lines, start=1):
        for pattern, code in RISK_PATTERNS:
            if pattern.search(line):
                item = {
                    "line": index,
                    "code": code,
                    "text": line.strip()[:240],
                    "message": "Risk wording found. Rewrite as abstract, original art-direction guidance.",
                }
                if is_negated(line):
                    item["message"] = "Boundary wording found in a negated/prohibitive context."
                    info.append(item)
                elif code in {"resource-extraction", "extraction-tool", "zh-extraction-term"} and (
                    authorized_document or is_authorized_local_context(line)
                ):
                    item["message"] = "Authorized local learning/export wording found."
                    info.append(item)
                else:
                    issues.append(item)

    for theme, terms in REQUIRED_THEMES.items():
        if not any(term.lower() in lower_text for term in terms):
            warnings.append(
                {
                    "code": f"missing-{theme}",
                    "message": f"Expected an art-direction guideline to mention {theme}.",
                }
            )

    if "我的判断" not in text and "my judgment" not in lower_text:
        warnings.append(
            {
                "code": "missing-judgment-label",
                "message": "Label analysis as '我的判断' or 'my judgment' when making recommendations.",
            }
        )
    if "待验证" not in text and "to verify" not in lower_text:
        warnings.append(
            {
                "code": "missing-unknowns",
                "message": "Add a '待验证' section for assumptions, permissions, or art tests.",
            }
        )

    return {"issues": issues, "warnings": warnings, "info": info}


def lint_file(path: Path) -> dict[str, object]:
    text = read_text(path)
    result = lint_text(text)
    result.update({"path": str(path.resolve()), "line_count": len(text.splitlines())})
    return result


def to_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Guideline Lint Report",
        "",
        f"- Path: `{report['path']}`",
        f"- Lines: {report['line_count']}",
        f"- Issues: {len(report['issues'])}",
        f"- Warnings: {len(report['warnings'])}",
        f"- Boundary mentions: {len(report['info'])}",
        "",
    ]
    if report["issues"]:
        lines.extend(["## Issues", "", "| Line | Code | Message | Text |", "|---:|---|---|---|"])
        for item in report["issues"]:  # type: ignore[index]
            lines.append(f"| {item['line']} | `{item['code']}` | {item['message']} | {item['text']} |")
    if report["warnings"]:
        lines.extend(["", "## Warnings", "", "| Code | Message |", "|---|---|"])
        for item in report["warnings"]:  # type: ignore[index]
            lines.append(f"| `{item['code']}` | {item['message']} |")
    if report["info"]:
        lines.extend(["", "## Boundary Mentions", "", "| Line | Code | Text |", "|---:|---|---|"])
        for item in report["info"][:20]:  # type: ignore[index]
            lines.append(f"| {item['line']} | `{item['code']}` | {item['text']} |")
    if not report["issues"] and not report["warnings"]:
        lines.append("No issues or warnings found.")
    return "\n".join(lines)


def exit_code(report: dict[str, object], fail_on: str) -> int:
    if fail_on == "none":
        return 0
    if fail_on == "issue":
        return 1 if report["issues"] else 0
    if fail_on == "warning":
        return 1 if report["issues"] or report["warnings"] else 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Markdown, text, or JSON guideline file.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--fail-on", choices=["none", "issue", "warning"], default="none")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists() or not path.is_file():
        parser.error(f"file does not exist: {path}")

    report = lint_file(path)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return exit_code(report, args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())

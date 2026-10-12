#!/usr/bin/env python3
"""Render model thumbnails and Blender-backed preview conversions."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.model_gallery_common import (
        ensure_inside_root,
        load_model_viewer_config,
        resolve_existing,
    )


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SCRIPT_ROOT / "references" / "model-viewer-gallery-config.json"
JOB_FILE_NAME = "blender-jobs.json"
RESULT_FILE_NAME = "blender-results.json"
STAGE = "blender_preview"


def _write_json_utf8(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json_utf8(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_format(item: dict[str, Any]) -> str:
    value = item.get("source_format")
    if value:
        return str(value).lower()
    return Path(str(item["source_path"])).suffix.lower().lstrip(".")


def plan_blender_jobs(manifest: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    blender_formats = {str(fmt).lower() for fmt in config["formats"]["blender_preview"]}
    jobs: list[dict[str, Any]] = []

    for item in manifest.get("items", []):
        source_format = _source_format(item)
        jobs.append(
            {
                "id": str(item["id"]),
                "source_path": str(item["source_path"]),
                "thumbnail_path": str(item["thumbnail_path"]),
                "preview_path": str(item["preview_path"]),
                "export_glb": source_format in blender_formats,
            }
        )

    return jobs


def append_error_jsonl(errors_path: Path, record: dict[str, Any]) -> None:
    errors_path = Path(errors_path)
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    with errors_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _preview_index_dir(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    configured = manifest.get("preview_index_dir")
    if configured:
        return Path(str(configured))
    return Path(manifest_path).parent


def _study_root(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    configured = manifest.get("study_root")
    if configured:
        return resolve_existing(Path(str(configured)))
    return resolve_existing(Path(manifest_path).parent)


def _configured_child(root: Path, parent: Path, config: dict[str, Any], key: str) -> Path:
    configured = Path(config["paths"][key])
    candidate = configured if configured.is_absolute() else parent / configured
    return ensure_inside_root(candidate, root)


def _normalize_results(results: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(results, dict):
        value = results.get("results", [])
    else:
        value = results
    if not isinstance(value, list):
        raise ValueError("Blender results must be a list")
    return [result for result in value if isinstance(result, dict)]


def _refresh_preview_tag(item: dict[str, Any], config: dict[str, Any]) -> None:
    prefix = config.get("manifest", {}).get("tag_rules", {}).get("preview_prefix")
    if not prefix or not isinstance(item.get("tags"), list):
        return

    tags = [tag for tag in item["tags"] if not str(tag).startswith(prefix)]
    preview_status = item.get("preview_status")
    if preview_status:
        tags.append(f"{prefix}{preview_status}")
    item["tags"] = tags


def update_manifest_after_jobs(
    manifest_path: Path,
    results: list[dict[str, Any]] | dict[str, Any],
    config: dict[str, Any],
) -> None:
    manifest_path = Path(manifest_path)
    manifest = _read_json_utf8(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("model viewer manifest root must be an object")

    items = manifest.get("items")
    if not isinstance(items, list):
        raise ValueError("model viewer manifest items must be a list")

    preview_index = _preview_index_dir(manifest_path, manifest)
    root = _study_root(manifest_path, manifest)
    preview_index = ensure_inside_root(preview_index, root)
    errors_path = _configured_child(root, preview_index, config, "errors_name")
    errors_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.write_text("", encoding="utf-8")

    result_by_id = {str(result.get("id")): result for result in _normalize_results(results)}
    blender_formats = {str(fmt).lower() for fmt in config["formats"]["blender_preview"]}

    for item in items:
        item_id = str(item.get("id"))
        result = result_by_id.get(item_id)
        if result is None:
            continue

        if result.get("success"):
            if _source_format(item) in blender_formats:
                item["preview_status"] = "converted"
            else:
                item["preview_status"] = "ready"
            item["errors"] = []
            for key in ("thumbnail_path", "preview_path"):
                if result.get(key):
                    item[key] = str(result[key])
            _refresh_preview_tag(item, config)
            continue

        error = str(result.get("error") or "Blender preview failed")
        if _source_format(item) in blender_formats:
            item["preview_status"] = "failed"
        if not isinstance(item.get("errors"), list):
            item["errors"] = []
        item["errors"].append(error)
        _refresh_preview_tag(item, config)
        append_error_jsonl(
            errors_path,
            {
                "id": item_id,
                "source_path": str(item.get("source_path", "")),
                "stage": STAGE,
                "error": error,
            },
        )

    _write_json_utf8(manifest_path, manifest)


def run_blender(blender: Path, worker: Path, job_file: Path, result_file: Path) -> int:
    command = [
        str(blender),
        "--background",
        "--python",
        str(worker),
        "--",
        "--jobs",
        str(job_file),
        "--results",
        str(result_file),
    ]
    try:
        return subprocess.run(command).returncode
    except OSError as exc:
        raise RuntimeError(f"Blender failed to start: {exc}") from exc


def _load_results(result_file: Path) -> list[dict[str, Any]]:
    return _normalize_results(_read_json_utf8(result_file))


def _failed_results(jobs: list[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    return [
        {
            "id": job["id"],
            "success": False,
            "error": message,
        }
        for job in jobs
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render model thumbnails with Blender.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_model_viewer_config(args.config)
    manifest_path = Path(args.manifest)
    manifest = _read_json_utf8(manifest_path)
    jobs = plan_blender_jobs(manifest, config)

    preview_index = _preview_index_dir(manifest_path, manifest)
    root = _study_root(manifest_path, manifest)
    preview_index = ensure_inside_root(preview_index, root)
    job_file = preview_index / JOB_FILE_NAME
    result_file = preview_index / RESULT_FILE_NAME
    worker = Path(__file__).resolve().with_name("blender_model_preview_worker.py")

    if result_file.exists():
        result_file.unlink()
    for job in jobs:
        ensure_inside_root(Path(job["thumbnail_path"]), root)
        if job.get("export_glb"):
            ensure_inside_root(Path(job["preview_path"]), root)
    _write_json_utf8(job_file, {"study_root": str(root), "thumbnail": config["thumbnail"], "jobs": jobs})

    try:
        exit_code = run_blender(args.blender, worker, job_file, result_file)
        start_error = ""
    except RuntimeError as exc:
        exit_code = 1
        start_error = str(exc)
    if result_file.exists():
        results = _load_results(result_file)
    elif start_error:
        results = _failed_results(jobs, start_error)
    else:
        message = f"Blender worker exited with code {exit_code} before writing results"
        results = _failed_results(jobs, message)

    update_manifest_after_jobs(manifest_path, results, config)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict


class VfxDependencyRef(TypedDict, total=False):
    asset_id: str
    source_file: str
    path_id: str
    asset_type: str
    asset_name: str
    export_path: str
    relative_path: str
    match_reason: str
    confidence: str


class VfxManifestItem(TypedDict, total=False):
    schema_version: int
    engine: str
    id: str
    name: str
    kind: str
    root_type: str
    source_file: str
    path_id: str
    asset_type: str
    reconstruction_relative_path: str
    preview_status: str
    reconstruction_status: str
    unity_export_status: str
    source_refs: list[dict[str, str]]
    supported_modules: list[str]
    unsupported_modules: list[str]
    tags: list[str]
    dependencies: dict[str, list[VfxDependencyRef]]
    warnings: NotRequired[list[str]]


class VfxReconstruction(TypedDict, total=False):
    schema_version: int
    engine: str
    id: str
    name: str
    kind: str
    source_object: dict[str, str]
    root_type: str
    modules: dict[str, dict[str, Any]]
    resources: dict[str, list[VfxDependencyRef]]
    preview_status: str
    reconstruction_status: str
    unity_export_status: str
    warnings: NotRequired[list[str]]


class VfxExportResult(TypedDict, total=False):
    engine: str
    id: str
    name: str
    status: str
    viewer_runtime_supported: bool
    reconstruction_status: str
    unity_export_status: str
    unity_export_relative_path: str
    unity_export_path: str
    ready: bool
    export_root: str
    reason: str


class EngineAdapter(Protocol):
    def discover_roots(self, rows: list[dict[str, str]], unitypy_root: Path) -> list[dict[str, str]]:
        ...

    def build_manifest_item(
        self,
        root: dict[str, str],
        rows: list[dict[str, str]],
        unitypy_root: Path,
        study_root: Path,
    ) -> VfxManifestItem:
        ...

    def build_reconstruction(self, item: VfxManifestItem, metadata: dict[str, Any]) -> VfxReconstruction:
        ...

    def export_project_fragment(
        self,
        item: VfxManifestItem,
        reconstruction: VfxReconstruction,
        export_root: Path,
    ) -> VfxExportResult:
        ...


def get_adapter(engine: str, config: dict[str, Any]) -> EngineAdapter:
    if engine.lower() == "unity":
        from scripts.vfx_adapters.unity import UnityVfxAdapter

        return UnityVfxAdapter(config)
    raise ValueError(f"Unsupported VFX engine: {engine}")

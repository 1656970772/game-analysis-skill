from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from scripts.vfx_preview_common import as_relative_path, stable_vfx_id
from scripts.vfx_adapters import VfxExportResult, VfxManifestItem, VfxReconstruction


UNITY_KIND_BY_TYPE = {
    "ParticleSystem": "particle_system",
    "TrailRenderer": "trail_renderer",
    "LineRenderer": "line_renderer",
}

DEPENDENCY_BUCKETS = {
    "ParticleSystemRenderer": "renderers",
    "Material": "materials",
    "Texture2D": "textures",
    "Sprite": "textures",
    "Mesh": "meshes",
    "AnimationClip": "animations",
}

REQUIRED_DEPENDENCY_BUCKETS = ["renderers", "materials", "textures", "meshes", "animations"]
SHAPE_TYPE_BY_ENUM = {
    0: "Sphere",
    1: "Hemisphere",
    2: "Cone",
    3: "Box",
    4: "Mesh",
    5: "ConeVolume",
    6: "Circle",
    7: "SingleSidedEdge",
    8: "MeshRenderer",
    9: "SkinnedMeshRenderer",
    10: "BoxShell",
    11: "BoxEdge",
    12: "Donut",
    13: "Rectangle",
    14: "Sprite",
    15: "SpriteRenderer",
}
RENDER_MODE_BY_ENUM = {
    0: "Billboard",
    1: "Stretch",
    2: "HorizontalBillboard",
    3: "VerticalBillboard",
    4: "Mesh",
    5: "None",
}
SAFE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
NAME_TOKEN_RE = re.compile(r"[^a-z0-9]+")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
REQUIRED_RECONSTRUCTION_MODULES = ["main", "emission", "shape", "renderer"]
BOOTSTRAP_SCRIPT_GUID = "8f5fbc77e4f1459da2f7796af315d74f"
BOOTSTRAP_CS = """using UnityEngine;

#if UNITY_EDITOR
using UnityEditor;
#endif

public sealed class GameAnalysisVfxBootstrap : MonoBehaviour
{
    public TextAsset metadata;
    public string metadataAssetPath;
    public Material material;
    public string materialAssetPath;
    public string textureAssetPath;
    public string sourceTextureRelativePath;

    private void Awake()
    {
        Apply();
    }

    public void Apply()
    {
        var source = ResolveMetadata();
        if (source == null) return;

        var reconstruction = JsonUtility.FromJson<GameAnalysisVfxReconstruction>(source.text);
        if (reconstruction == null || reconstruction.modules == null) return;

        var ps = GetComponent<ParticleSystem>();
        if (ps == null)
        {
            ps = gameObject.AddComponent<ParticleSystem>();
        }

        ApplyMain(ps, reconstruction.modules.main);
        ApplyEmission(ps, reconstruction.modules.emission);
        ApplyShape(ps, reconstruction.modules.shape);
        ApplyRenderer(reconstruction.modules.renderer);

        if (!ps.isPlaying)
        {
            ps.Play();
        }
    }

    private TextAsset ResolveMetadata()
    {
        if (metadata != null) return metadata;
#if UNITY_EDITOR
        if (!string.IsNullOrEmpty(metadataAssetPath))
        {
            metadata = AssetDatabase.LoadAssetAtPath<TextAsset>(metadataAssetPath);
        }
#endif
        return metadata;
    }

    private void ApplyMain(ParticleSystem ps, GameAnalysisVfxMain mainData)
    {
        if (mainData == null) return;

        var main = ps.main;
        main.duration = Mathf.Max(0.01f, mainData.duration);
        main.loop = mainData.looping || mainData.loop;
        main.startLifetime = CurveValue(mainData.startLifetime, 1f);
        main.startSpeed = CurveValue(mainData.startSpeed, 1f);
        main.startSize = CurveValue(mainData.startSize, 1f);
        main.gravityModifier = CurveValue(mainData.gravityModifier, 0f);
        if (mainData.maxParticles > 0)
        {
            main.maxParticles = mainData.maxParticles;
        }
    }

    private void ApplyEmission(ParticleSystem ps, GameAnalysisVfxEmission emissionData)
    {
        if (emissionData == null) return;

        var emission = ps.emission;
        emission.enabled = emissionData.enabled;
        emission.rateOverTime = CurveValue(emissionData.rateOverTime, 10f);
        emission.rateOverDistance = CurveValue(emissionData.rateOverDistance, 0f);
    }

    private void ApplyShape(ParticleSystem ps, GameAnalysisVfxShape shapeData)
    {
        if (shapeData == null) return;

        var shape = ps.shape;
        shape.enabled = shapeData.enabled;
        shape.shapeType = ShapeType(shapeData.shapeType);
        shape.angle = shapeData.angle;
        shape.radius = shapeData.radius;
    }

    private void ApplyRenderer(GameAnalysisVfxRenderer rendererData)
    {
        var psRenderer = GetComponent<ParticleSystemRenderer>();
        if (psRenderer == null)
        {
            psRenderer = gameObject.AddComponent<ParticleSystemRenderer>();
        }

        if (material != null)
        {
            psRenderer.sharedMaterial = material;
        }
#if UNITY_EDITOR
        else if (!string.IsNullOrEmpty(materialAssetPath))
        {
            material = AssetDatabase.LoadAssetAtPath<Material>(materialAssetPath);
            if (material != null)
            {
                psRenderer.sharedMaterial = material;
            }
        }
#endif

        if (rendererData == null) return;
        psRenderer.sortingLayerName = rendererData.sortingLayer;
        psRenderer.sortingOrder = rendererData.sortingOrder;
    }

    private static float CurveValue(GameAnalysisVfxCurve curve, float fallback)
    {
        if (curve == null) return fallback;
        if (curve.constantMax != 0f || curve.constantMin != 0f)
        {
            return (curve.constantMin + curve.constantMax) * 0.5f;
        }
        return curve.constant != 0f ? curve.constant : fallback;
    }

    private static ParticleSystemShapeType ShapeType(string value)
    {
        switch (value)
        {
            case "Cone": return ParticleSystemShapeType.Cone;
            case "Sphere": return ParticleSystemShapeType.Sphere;
            case "Box": return ParticleSystemShapeType.Box;
            case "Circle": return ParticleSystemShapeType.Circle;
            default: return ParticleSystemShapeType.Cone;
        }
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxReconstruction
    {
        public GameAnalysisVfxModules modules;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxModules
    {
        public GameAnalysisVfxMain main;
        public GameAnalysisVfxEmission emission;
        public GameAnalysisVfxShape shape;
        public GameAnalysisVfxRenderer renderer;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxMain
    {
        public float duration = 1f;
        public bool loop;
        public bool looping = true;
        public int maxParticles = 1000;
        public GameAnalysisVfxCurve startLifetime;
        public GameAnalysisVfxCurve startSpeed;
        public GameAnalysisVfxCurve startSize;
        public GameAnalysisVfxCurve gravityModifier;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxEmission
    {
        public bool enabled = true;
        public GameAnalysisVfxCurve rateOverTime;
        public GameAnalysisVfxCurve rateOverDistance;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxShape
    {
        public bool enabled = true;
        public string shapeType = "Cone";
        public float angle = 25f;
        public float radius = 1f;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxRenderer
    {
        public string renderMode;
        public string sortingLayer;
        public int sortingOrder;
    }

    [System.Serializable]
    private sealed class GameAnalysisVfxCurve
    {
        public float constant;
        public float constantMin;
        public float constantMax;
    }
}
"""


class UnityVfxAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.unity_config = config["adapter"]["unity"]
        self.root_types = set(self.unity_config["root_types"])
        self.supported_modules = list(self.unity_config["supported_modules"])
        self.supported_module_set = set(self.supported_modules)
        self.module_aliases = dict(self.unity_config.get("module_aliases", {}))
        self.defaults = copy.deepcopy(self.unity_config.get("defaults", {}))
        self.configured_unsupported_modules = set(self.unity_config.get("unsupported_modules", []))
        unity_export = config["unity_export"]
        self.export_asset_root = as_relative_path(unity_export["asset_root"])
        self.export_metadata_dir = as_relative_path(unity_export["metadata_dir"])
        self.export_prefabs_dir = as_relative_path(unity_export["prefabs_dir"])
        self.export_materials_dir = as_relative_path(unity_export["materials_dir"])
        self.export_textures_dir = as_relative_path(unity_export["textures_dir"])
        self.export_scripts_dir = as_relative_path(unity_export["scripts_dir"])
        self.bootstrap_script_name = str(unity_export["bootstrap_script_name"])

    def discover_roots(self, rows: list[dict[str, str]], unitypy_root: Path) -> list[dict[str, str]]:
        return [dict(row) for row in rows if row.get("asset_type") in self.root_types]

    def build_manifest_item(
        self,
        root: dict[str, str],
        rows: list[dict[str, str]],
        unitypy_root: Path,
        study_root: Path,
    ) -> VfxManifestItem:
        root_copy = dict(root)
        name = _asset_name(root_copy)
        asset_type = root_copy.get("asset_type", "")
        source_file = root_copy.get("source_file", "")
        path_id = str(root_copy.get("path_id", ""))
        initial = self.config["state_machine"]["manifest_initial"]
        dependencies = self._dependencies_for(root_copy, rows, unitypy_root)
        supported, unsupported = self._classify_modules(root_copy)
        reconstruction_dir = self.config["paths"].get("reconstructed_dir", "reconstructed").replace("\\", "/")
        warnings = _dependency_warnings(dependencies)
        tags = self._keyword_tags(root_copy, dependencies)

        return {
            "schema_version": self.config["manifest"]["schema_version"],
            "engine": "unity",
            "id": stable_vfx_id(source_file, path_id, asset_type, name),
            "name": name,
            "kind": UNITY_KIND_BY_TYPE.get(asset_type, "unsupported"),
            "root_type": asset_type,
            "source_file": source_file,
            "path_id": path_id,
            "asset_type": asset_type,
            "reconstruction_relative_path": f"{reconstruction_dir}/{_safe_name(name)}-{_safe_name(path_id)}.json",
            "preview_status": initial["preview_status"],
            "reconstruction_status": initial["reconstruction_status"],
            "unity_export_status": initial["unity_export_status"],
            "source_refs": [root_copy],
            "supported_modules": supported,
            "unsupported_modules": unsupported,
            "tags": tags,
            "dependencies": dependencies,
            "warnings": warnings,
        }

    def build_reconstruction(self, item: VfxManifestItem, metadata: dict[str, Any]) -> VfxReconstruction:
        main = self._main_module(metadata)
        emission = self._emission_module(metadata)
        shape = self._shape_module(metadata)
        renderer = self._renderer_module(item, metadata)
        texture_sheet = self._texture_sheet_animation_module(metadata)
        trail = self._passthrough_module("trail", metadata)
        line = self._passthrough_module("line", metadata)

        return {
            "schema_version": 1,
            "engine": "unity",
            "id": item["id"],
            "name": item["name"],
            "kind": item["kind"],
            "source_object": _source_object(item),
            "root_type": item["root_type"],
            "modules": {
                "main": main,
                "emission": emission,
                "shape": shape,
                "renderer": renderer,
                "textureSheetAnimation": texture_sheet,
                "trail": trail,
                "line": line,
                "raw_unity": metadata,
            },
            "resources": _resources_for_schema(item.get("dependencies", {})),
            "preview_status": item["preview_status"],
            "reconstruction_status": item["reconstruction_status"],
            "unity_export_status": item["unity_export_status"],
            "warnings": list(item.get("warnings", [])),
        }

    def export_project_fragment(
        self,
        item: VfxManifestItem,
        reconstruction: VfxReconstruction,
        export_root: Path,
    ) -> VfxExportResult:
        item_id = str(item.get("id") or reconstruction.get("id") or "vfx")
        item_name = str(item.get("name") or reconstruction.get("name") or item_id)
        safe_id = _safe_name(item_id)
        safe_name = _safe_name(item_name)
        asset_root = export_root / self.export_asset_root
        metadata_path = asset_root / self.export_metadata_dir / f"{safe_id}.json"
        material_path = asset_root / self.export_materials_dir / f"{safe_id}.mat"
        prefab_path = asset_root / self.export_prefabs_dir / f"{safe_name}.prefab"
        bootstrap_path = asset_root / self.export_scripts_dir / self.bootstrap_script_name
        prefab_guid = _stable_guid("prefab", item_id)
        metadata_guid = _stable_guid("metadata", item_id)
        material_guid = _stable_guid("material", item_id)
        metadata_asset_path = (self.export_asset_root / self.export_metadata_dir / f"{safe_id}.json").as_posix()
        material_asset_path = (self.export_asset_root / self.export_materials_dir / f"{safe_id}.mat").as_posix()
        source_texture_relative_path = _first_texture_relative_path(reconstruction)
        texture_guid = _stable_guid("texture", item_id, source_texture_relative_path)
        texture_asset_path = ""
        texture_path: Path | None = None
        texture_warning = ""
        prefab_relative_path = _relative_unity_export_path(prefab_path, export_root)
        missing_modules = _missing_reconstruction_modules(reconstruction)
        root_type = str(reconstruction.get("root_type") or item.get("root_type") or item.get("asset_type") or "")

        if root_type != "ParticleSystem":
            warning = (
                "Unity prefab/bootstrap export currently supports ParticleSystem only; "
                f"{root_type or 'unknown'} is retained as reconstructed JSON/metadata."
            )
            _append_warning(reconstruction, warning)
            reconstruction["preview_status"] = "partial"
            reconstruction["reconstruction_status"] = "partial"
            reconstruction["unity_export_status"] = "partial"
            metadata_relative_path = _relative_unity_export_path(metadata_path, export_root)
            try:
                _ensure_export_targets_inside_root(export_root, metadata_path)
                metadata_path.parent.mkdir(parents=True, exist_ok=True)
                metadata_path.with_suffix(metadata_path.suffix + ".meta").write_text(
                    _text_asset_meta(metadata_guid),
                    encoding="utf-8",
                )
                metadata_path.write_text(
                    json.dumps(reconstruction, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except (OSError, ValueError) as exc:
                _cleanup_export_fragment(export_root, metadata_path)
                return {
                    "engine": "unity",
                    "id": item_id,
                    "name": item_name,
                    "status": "failed",
                    "viewer_runtime_supported": False,
                    "reconstruction_status": "failed",
                    "unity_export_status": "failed",
                    "unity_export_relative_path": "",
                    "unity_export_path": "",
                    "ready": False,
                    "export_root": str(export_root),
                    "reason": f"failed to write Unity export metadata: {exc}",
                }
            return {
                "engine": "unity",
                "id": item_id,
                "name": item_name,
                "status": "partial",
                "viewer_runtime_supported": False,
                "reconstruction_status": "partial",
                "unity_export_status": "partial",
                "unity_export_relative_path": metadata_relative_path,
                "unity_export_path": str(metadata_path),
                "ready": False,
                "export_root": str(export_root),
                "reason": warning,
                "warnings": [warning],
            }

        if source_texture_relative_path:
            texture_path, texture_asset_path, texture_warning = _resolve_texture_export(
                item=item,
                source_texture_relative_path=source_texture_relative_path,
                export_root=export_root,
                asset_root=asset_root,
                export_asset_root=self.export_asset_root,
                export_textures_dir=self.export_textures_dir,
            )
            if texture_warning:
                _append_warning(reconstruction, texture_warning)

        reconstruction["preview_status"] = "partial" if missing_modules else "ready"
        reconstruction["reconstruction_status"] = "partial" if missing_modules else "full"
        reconstruction["unity_export_status"] = "partial" if missing_modules or texture_warning else "ready"

        try:
            _ensure_export_targets_inside_root(
                export_root,
                metadata_path,
                material_path,
                prefab_path,
                bootstrap_path,
                *([texture_path] if texture_path is not None else []),
            )
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            material_path.parent.mkdir(parents=True, exist_ok=True)
            prefab_path.parent.mkdir(parents=True, exist_ok=True)
            bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
            if texture_path is not None:
                source_root = Path(str(item["__unitypy_root"]))
                source_texture_path = _safe_export_candidate(source_root, source_texture_relative_path)
                if source_texture_path is None or not source_texture_path.is_file():
                    raise OSError(f"Texture2D source is missing: {source_texture_relative_path}")
                texture_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_texture_path, texture_path)
                texture_path.with_suffix(texture_path.suffix + ".meta").write_text(
                    _texture_meta(texture_guid),
                    encoding="utf-8",
                )
            bootstrap_path.write_text(BOOTSTRAP_CS, encoding="utf-8")
            bootstrap_path.with_suffix(bootstrap_path.suffix + ".meta").write_text(
                _mono_script_meta(BOOTSTRAP_SCRIPT_GUID),
                encoding="utf-8",
            )
            material_path.write_text(
                _material_yaml(safe_name, source_texture_relative_path, texture_guid if texture_path is not None else ""),
                encoding="utf-8",
            )
            material_path.with_suffix(material_path.suffix + ".meta").write_text(
                _material_meta(material_guid),
                encoding="utf-8",
            )
            prefab_path.write_text(
                _prefab_yaml(
                    safe_name,
                    metadata_asset_path,
                    metadata_guid,
                    material_asset_path,
                    material_guid,
                    texture_asset_path,
                    source_texture_relative_path,
                    reconstruction,
                    BOOTSTRAP_SCRIPT_GUID,
                ),
                encoding="utf-8",
            )
            prefab_path.with_suffix(prefab_path.suffix + ".meta").write_text(
                _prefab_meta(prefab_guid),
                encoding="utf-8",
            )
            metadata_path.with_suffix(metadata_path.suffix + ".meta").write_text(
                _text_asset_meta(metadata_guid),
                encoding="utf-8",
            )
            metadata_path.write_text(
                json.dumps(reconstruction, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if _ready_export_missing(
                prefab_path=prefab_path,
                metadata_path=metadata_path,
                bootstrap_path=bootstrap_path,
                material_path=material_path,
                texture_path=texture_path,
                material_text=material_path.read_text(encoding="utf-8"),
                prefab_text=prefab_path.read_text(encoding="utf-8"),
                needs_texture_reference=source_texture_relative_path != "" and not texture_warning,
                source_texture_relative_path=source_texture_relative_path,
                texture_guid=texture_guid if texture_path is not None else "",
            ):
                raise OSError("Unity export fragment is missing required asset references")
        except (OSError, ValueError) as exc:
            _cleanup_export_fragment(
                export_root,
                metadata_path,
                material_path,
                prefab_path,
                bootstrap_path,
                *([texture_path] if texture_path is not None else []),
            )
            return {
                "engine": "unity",
                "id": item_id,
                "name": item_name,
                "status": "failed",
                "viewer_runtime_supported": False,
                "reconstruction_status": "failed",
                "unity_export_status": "failed",
                "unity_export_relative_path": "",
                "unity_export_path": "",
                "ready": False,
                "export_root": str(export_root),
                "reason": f"failed to write Unity export fragment: {exc}",
            }

        if missing_modules or texture_warning:
            reason = f"missing required reconstruction modules: {', '.join(missing_modules)}" if missing_modules else texture_warning
            return {
                "engine": "unity",
                "id": item_id,
                "name": item_name,
                "status": "partial",
                "viewer_runtime_supported": False,
                "reconstruction_status": "partial",
                "unity_export_status": "partial",
                "unity_export_relative_path": prefab_relative_path,
                "unity_export_path": str(prefab_path),
                "ready": False,
                "export_root": str(export_root),
                "reason": reason,
                "warnings": [texture_warning] if texture_warning else [],
            }

        return {
            "engine": "unity",
            "id": item_id,
            "name": item_name,
            "status": "ready",
            "viewer_runtime_supported": True,
            "reconstruction_status": "full",
            "unity_export_status": "ready",
            "unity_export_relative_path": prefab_relative_path,
            "unity_export_path": str(prefab_path),
            "ready": True,
            "export_root": str(export_root),
        }

    def _classify_modules(self, root: dict[str, Any]) -> tuple[list[str], list[str]]:
        raw_modules = _raw_modules(root)
        if not raw_modules:
            return list(self.supported_modules), []

        supported: list[str] = []
        unsupported: list[str] = []
        for module in raw_modules:
            canonical = self.module_aliases.get(module, module)
            if canonical in self.supported_module_set and canonical not in supported:
                supported.append(canonical)
            elif module not in unsupported:
                unsupported.append(module)

        for module in self.configured_unsupported_modules:
            if module in raw_modules and module not in unsupported:
                unsupported.append(module)
        return supported, unsupported

    def _keyword_tags(
        self,
        root: dict[str, str],
        dependencies: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        haystack_parts = [
            root.get("asset_name", ""),
            root.get("source_file", ""),
            root.get("export_path", ""),
            root.get("container_path", ""),
        ]
        for refs in dependencies.values():
            for ref in refs:
                haystack_parts.extend(
                    [
                        str(ref.get("asset_name", "")),
                        str(ref.get("relative_path", "")),
                        str(ref.get("export_path", "")),
                        str(ref.get("source_file", "")),
                    ]
                )
        haystack = " ".join(part for part in haystack_parts if part).lower()
        tags: list[str] = []
        for rule in self.config.get("name_keyword_rules", []):
            tag = str(rule.get("tag") or "")
            keywords = [str(keyword).lower() for keyword in rule.get("keywords", [])]
            if tag and any(keyword and keyword in haystack for keyword in keywords):
                tags.append(tag)
        return tags

    def _dependencies_for(
        self,
        root: dict[str, Any],
        rows: list[dict[str, str]],
        unitypy_root: Path,
    ) -> dict[str, list[dict[str, Any]]]:
        dependencies: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in REQUIRED_DEPENDENCY_BUCKETS}
        root_name = _asset_name(root)
        root_source = root.get("source_file", "")
        metadata_by_row: dict[str, Any] = {}
        reference_ids = _collect_reference_ids(root)
        root_metadata = self._row_metadata(root, unitypy_root, metadata_by_row)
        reference_ids.update(_collect_reference_ids(root_metadata))

        changed = True
        while changed:
            changed = False
            for row in rows:
                asset_type = row.get("asset_type", "")
                bucket = DEPENDENCY_BUCKETS.get(asset_type)
                if bucket is None:
                    continue
                if _already_has(dependencies[bucket], row):
                    continue
                match = self._dependency_match(row, root_name, root_source, reference_ids)
                if match is None:
                    continue

                dependency = _resource_ref(row, match_reason=match[0], confidence=match[1])
                dependencies[bucket].append(dependency)
                row_metadata = self._row_metadata(row, unitypy_root, metadata_by_row)
                before = len(reference_ids)
                reference_ids.update(_collect_reference_ids(row))
                reference_ids.update(_collect_reference_ids(row_metadata))
                changed = changed or len(reference_ids) != before

        return dependencies

    def _dependency_match(
        self,
        row: dict[str, Any],
        root_name: str,
        root_source: str,
        reference_ids: set[str],
    ) -> tuple[str, str] | None:
        if row.get("source_file") != root_source:
            return None
        path_id = str(row.get("path_id", ""))
        if path_id and path_id in reference_ids:
            return ("explicit_ref", "high")
        if _names_related(root_name, _asset_name(row)):
            return ("name_prefix", "medium")
        return None

    def _row_metadata(self, row: dict[str, Any], unitypy_root: Path, cache: dict[str, Any]) -> Any:
        cache_key = f"{row.get('source_file', '')}:{row.get('path_id', '')}"
        if cache_key in cache:
            return cache[cache_key]

        metadata = row.get("metadata")
        if metadata is None:
            export_path = row.get("export_path", "")
            candidate = _safe_export_candidate(unitypy_root, export_path) if export_path else None
            if candidate is not None and candidate.is_file():
                try:
                    metadata = json.loads(candidate.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    metadata = {}
            else:
                metadata = {}
        cache[cache_key] = metadata
        return metadata

    def _main_module(self, metadata: dict[str, Any]) -> dict[str, Any]:
        defaults = self._module_defaults("main")
        payload = self._module_payload("main", metadata)
        main = {
            "duration": _first(
                payload,
                "duration",
                "m_Duration",
                default=_first(metadata, "duration", "m_Duration", default=defaults.get("duration")),
            ),
            "looping": _first(
                payload,
                "looping",
                "loop",
                "m_Looping",
                default=_first(metadata, "looping", "loop", "m_Looping", default=defaults.get("loop", False)),
            ),
            "startLifetime": _first(payload, "startLifetime", "m_StartLifetime", default=defaults.get("startLifetime", {})),
            "startSpeed": _first(payload, "startSpeed", "m_StartSpeed", default=defaults.get("startSpeed", {})),
            "startSize": _first(payload, "startSize", "m_StartSize", default=defaults.get("startSize", {})),
            "startColor": _schema_gradient(
                _first(payload, "startColor", "m_StartColor", default=defaults.get("startColor", {}))
            ),
            "gravityModifier": _first(
                payload,
                "gravityModifier",
                "m_GravityModifier",
                default=defaults.get("gravityModifier", {}),
            ),
            "simulationSpace": _first(
                payload,
                "simulationSpace",
                "m_SimulationSpace",
                default=defaults.get("simulationSpace", ""),
            ),
            "maxParticles": _first(payload, "maxParticles", "m_MaxParticles", default=defaults.get("maxParticles", 0)),
        }
        return main

    def _emission_module(self, metadata: dict[str, Any]) -> dict[str, Any]:
        defaults = self._module_defaults("emission")
        payload = self._module_payload("emission", metadata)
        return {
            "enabled": _first(payload, "enabled", "m_Enabled", default=defaults.get("enabled", False)),
            "rateOverTime": _first(payload, "rateOverTime", "m_RateOverTime", default=defaults.get("rateOverTime")),
            "rateOverDistance": _first(
                payload,
                "rateOverDistance",
                "m_RateOverDistance",
                default=defaults.get("rateOverDistance", {}),
            ),
            "bursts": _first(payload, "bursts", "m_Bursts", default=defaults.get("bursts", [])),
        }

    def _shape_module(self, metadata: dict[str, Any]) -> dict[str, Any]:
        defaults = self._module_defaults("shape")
        payload = self._module_payload("shape", metadata)
        return {
            "enabled": _first(payload, "enabled", "m_Enabled", default=defaults.get("enabled", False)),
            "shapeType": _shape_type_name(
                _first(payload, "shapeType", "type", "m_Type", default=defaults.get("shapeType", ""))
            ),
            "angle": _first(payload, "angle", "m_Angle", default=defaults.get("angle", 0.0)),
            "radius": _first(payload, "radius", "m_Radius", default=defaults.get("radius", 0.0)),
            "box": _first(payload, "box", "m_Box", default=defaults.get("box", [])),
            "meshRef": _first(payload, "meshRef", "m_Mesh", default=defaults.get("meshRef", "")),
        }

    def _renderer_module(self, item: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        defaults = self._module_defaults("renderer")
        payload = self._module_payload("renderer", metadata)
        dependencies = item.get("dependencies", {})
        textures = dependencies.get("textures", [])
        materials = dependencies.get("materials", [])
        meshes = dependencies.get("meshes", [])
        dependency_texture = textures[0].get("relative_path") if textures else None
        return {
            "renderMode": _render_mode_name(
                _first(payload, "renderMode", "m_RenderMode", default=defaults.get("renderMode", ""))
            ),
            "materialRef": _first(
                payload,
                "materialRef",
                "m_Material",
                default=_first_resource_path(materials) or "",
            ),
            "textureRef": dependency_texture
            or _first(payload, "textureRef", "m_Texture", default=_first_resource_path(textures) or ""),
            "meshRef": _first(payload, "meshRef", "m_Mesh", default=_first_resource_path(meshes) or ""),
            "sortingLayer": _first(payload, "sortingLayer", "m_SortingLayer", default=defaults.get("sortingLayer", "")),
            "sortingOrder": _first(payload, "sortingOrder", "m_SortingOrder", default=defaults.get("sortingOrder", 0)),
            "alignment": _first(payload, "alignment", "m_Alignment", default=defaults.get("alignment", "")),
        }

    def _texture_sheet_animation_module(self, metadata: dict[str, Any]) -> dict[str, Any]:
        defaults = self._module_defaults("textureSheetAnimation")
        payload = self._module_payload("textureSheetAnimation", metadata)
        if not payload and isinstance(metadata.get("UVModule"), dict):
            payload = metadata["UVModule"]
        tiles = _first(payload, "tiles", default=defaults.get("tiles"))
        if tiles is None:
            x = _first(payload, "numTilesX", "m_NumTilesX", default=defaults.get("numTilesX"))
            y = _first(payload, "numTilesY", "m_NumTilesY", default=defaults.get("numTilesY"))
            tiles = {"x": x, "y": y} if x is not None or y is not None else None
        if tiles is None:
            tiles = {}
        return {
            "enabled": _first(payload, "enabled", "m_Enabled", default=defaults.get("enabled", False)),
            "tiles": tiles,
            "cycles": _first(payload, "cycles", "m_Cycles", default=defaults.get("cycles", 0)),
        }

    def _passthrough_module(self, canonical: str, metadata: dict[str, Any]) -> dict[str, Any]:
        module = self._module_defaults(canonical)
        module.update(copy.deepcopy(self._module_payload(canonical, metadata)))
        return module

    def _module_defaults(self, canonical: str) -> dict[str, Any]:
        return copy.deepcopy(self.defaults.get(canonical, {}))

    def _module_payload(self, canonical: str, metadata: dict[str, Any]) -> dict[str, Any]:
        candidates = [canonical, f"m_{canonical[0].upper()}{canonical[1:]}"]
        candidates.extend(alias for alias, mapped in self.module_aliases.items() if mapped == canonical)
        for key in candidates:
            value = metadata.get(key)
            if isinstance(value, dict):
                return value
        return {}


def _asset_name(row: dict[str, Any]) -> str:
    return str(row.get("asset_name") or row.get("name") or row.get("m_Name") or "")


def _safe_name(value: str) -> str:
    safe = SAFE_NAME_RE.sub("_", str(value)).strip(" ._")
    return safe or "vfx"


def _raw_modules(row: dict[str, Any]) -> list[str]:
    modules = row.get("modules", [])
    if isinstance(modules, str):
        modules = [part.strip() for part in re.split(r"[,;|]", modules) if part.strip()]
    if isinstance(modules, list):
        return [str(module) for module in modules if str(module)]
    return []


def _dependency_warnings(dependencies: dict[str, list[dict[str, Any]]]) -> list[str]:
    warnings: list[str] = []
    for bucket, warning in [
        ("renderers", "missing_renderer"),
        ("materials", "missing_material"),
        ("textures", "missing_texture"),
    ]:
        if not dependencies.get(bucket):
            warnings.append(warning)
    return warnings


def _safe_export_candidate(unitypy_root: Path, export_path: str) -> Path | None:
    if _is_absolute_or_drive_relative(export_path):
        return None
    try:
        root = unitypy_root.resolve(strict=False)
        candidate = (unitypy_root / export_path).resolve(strict=False)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


def _is_absolute_or_drive_relative(path_value: str) -> bool:
    return path_value.startswith(("/", "\\")) or WINDOWS_DRIVE_RE.match(path_value) is not None


def _resource_ref(row: dict[str, Any], *, match_reason: str, confidence: str) -> dict[str, Any]:
    ref = dict(row)
    export_path = str(ref.get("export_path") or "").replace("\\", "/")
    ref["relative_path"] = export_path
    ref["id"] = str(ref.get("path_id", ""))
    ref["type"] = str(ref.get("asset_type", ""))
    ref["name"] = str(ref.get("asset_name", ""))
    ref["match_reason"] = match_reason
    ref["confidence"] = confidence
    return ref


def _source_object(item: dict[str, Any]) -> dict[str, str]:
    source_refs = item.get("source_refs", [])
    source = source_refs[0] if source_refs else item
    return {
        key: str(source.get(key, ""))
        for key in ["source_file", "path_id", "asset_type", "asset_name", "export_path", "container_path"]
        if key in source or key in {"source_file", "path_id", "asset_type"}
    }


def _resources_for_schema(dependencies: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        bucket: list(dependencies.get(bucket, []))
        for bucket in ["textures", "materials", "meshes", "animations"]
    }


def _first_resource_path(resources: list[dict[str, Any]]) -> str | None:
    if not resources:
        return None
    first = resources[0]
    return first.get("relative_path") or first.get("export_path")


def _already_has(resources: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    path_id = str(row.get("path_id", ""))
    source_file = row.get("source_file", "")
    return any(str(resource.get("path_id", "")) == path_id and resource.get("source_file", "") == source_file for resource in resources)


def _names_related(root_name: str, candidate_name: str) -> bool:
    root = _normalized_name(root_name)
    candidate = _normalized_name(candidate_name)
    if not root or not candidate:
        return False
    return candidate == root or candidate.startswith(f"{root}_")


def _normalized_name(value: str) -> str:
    return NAME_TOKEN_RE.sub("_", value.lower()).strip("_")


def _collect_reference_ids(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"pathID", "path_id", "m_PathID"} and child not in {None, "", 0}:
                refs.add(str(child))
            refs.update(_collect_reference_ids(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_collect_reference_ids(child))
    return refs


def _first(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return copy.deepcopy(default)


def _missing_reconstruction_modules(reconstruction: dict[str, Any]) -> list[str]:
    modules = reconstruction.get("modules")
    if not isinstance(modules, dict):
        return list(REQUIRED_RECONSTRUCTION_MODULES)
    return [
        module
        for module in REQUIRED_RECONSTRUCTION_MODULES
        if not isinstance(modules.get(module), dict)
    ]


def _relative_unity_export_path(path: Path, export_root: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(export_root.resolve(strict=False)).as_posix()
        return f"{export_root.name}/{relative}" if export_root.name else relative
    except ValueError:
        return path.as_posix()


def _first_texture_relative_path(reconstruction: dict[str, Any]) -> str:
    modules = reconstruction.get("modules", {})
    if isinstance(modules, dict):
        renderer = modules.get("renderer", {})
        if isinstance(renderer, dict) and renderer.get("textureRef"):
            return str(renderer["textureRef"])
    resources = reconstruction.get("resources", {})
    textures = resources.get("textures", []) if isinstance(resources, dict) else []
    if isinstance(textures, list) and textures:
        first = textures[0]
        if isinstance(first, dict):
            return str(first.get("relative_path") or first.get("export_path") or "")
    return ""


def _ensure_export_targets_inside_root(export_root: Path, *paths: Path) -> None:
    root = export_root.resolve(strict=False)
    for path in paths:
        for candidate in [path, path.with_suffix(path.suffix + ".meta")]:
            try:
                candidate.resolve(strict=False).relative_to(root)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Unity export target is outside Unity export root: {candidate}") from exc


def _ready_export_missing(
    *,
    prefab_path: Path,
    metadata_path: Path,
    bootstrap_path: Path,
    material_path: Path,
    texture_path: Path | None,
    material_text: str,
    prefab_text: str,
    needs_texture_reference: bool,
    source_texture_relative_path: str,
    texture_guid: str,
) -> bool:
    required = [
        prefab_path,
        prefab_path.with_suffix(prefab_path.suffix + ".meta"),
        metadata_path,
        metadata_path.with_suffix(metadata_path.suffix + ".meta"),
        bootstrap_path,
        bootstrap_path.with_suffix(bootstrap_path.suffix + ".meta"),
        material_path,
        material_path.with_suffix(material_path.suffix + ".meta"),
    ]
    if texture_path is not None:
        required.extend([texture_path, texture_path.with_suffix(texture_path.suffix + ".meta")])
    if any(not path.is_file() for path in required):
        return True
    if "m_Script: {fileID: 0}" in prefab_text or "material: {fileID: 0}" in prefab_text:
        return True
    if needs_texture_reference and source_texture_relative_path not in prefab_text:
        return True
    if needs_texture_reference and (not texture_guid or f"guid: {texture_guid}" not in material_text):
        return True
    return False


def _resolve_texture_export(
    *,
    item: dict[str, Any],
    source_texture_relative_path: str,
    export_root: Path,
    asset_root: Path,
    export_asset_root: Path,
    export_textures_dir: Path,
) -> tuple[Path | None, str, str]:
    source_root_text = str(item.get("__unitypy_root") or "")
    if not source_root_text:
        return None, "", "Texture2D dependency is not copied or bound: missing UnityPy source root"
    source_root = Path(source_root_text)
    source_texture_path = _safe_export_candidate(source_root, source_texture_relative_path)
    if source_texture_path is None or not source_texture_path.is_file():
        return None, "", f"Texture2D dependency is not copied or bound: {source_texture_relative_path}"
    source_name = Path(source_texture_relative_path.replace("\\", "/")).name
    destination_name = _safe_name(source_name)
    if not Path(destination_name).suffix:
        destination_name = f"{destination_name}.png"
    texture_path = asset_root / export_textures_dir / destination_name
    texture_asset_path = (export_asset_root / export_textures_dir / destination_name).as_posix()
    try:
        texture_path.resolve(strict=False).relative_to(export_root.resolve(strict=False))
    except (OSError, ValueError):
        return None, "", f"Texture2D dependency is not copied or bound: {source_texture_relative_path}"
    return texture_path, texture_asset_path, ""


def _append_warning(reconstruction: dict[str, Any], warning: str) -> None:
    warnings = reconstruction.setdefault("warnings", [])
    if isinstance(warnings, list) and warning not in warnings:
        warnings.append(warning)


def _prefab_yaml(
    name: str,
    metadata_asset_path: str,
    metadata_guid: str,
    material_asset_path: str,
    material_guid: str,
    texture_asset_path: str,
    source_texture_relative_path: str,
    reconstruction: dict[str, Any],
    script_guid: str,
) -> str:
    modules = reconstruction.get("modules", {})
    main = modules.get("main", {}) if isinstance(modules, dict) else {}
    emission = modules.get("emission", {}) if isinstance(modules, dict) else {}
    renderer = modules.get("renderer", {}) if isinstance(modules, dict) else {}
    duration = _yaml_scalar(main.get("duration", 1.0) if isinstance(main, dict) else 1.0)
    looping = _yaml_bool(_first_bool(main, "looping", "loop", default=True))
    rate_over_time = _yaml_scalar(_curve_constant(emission.get("rateOverTime"), 10.0) if isinstance(emission, dict) else 10.0)
    sorting_order = _yaml_scalar(renderer.get("sortingOrder", 0) if isinstance(renderer, dict) else 0)
    sorting_layer = _yaml_scalar(renderer.get("sortingLayer", "") if isinstance(renderer, dict) else "")
    yaml_name = _yaml_scalar(name)
    yaml_metadata_asset_path = _yaml_scalar(metadata_asset_path)
    yaml_material_asset_path = _yaml_scalar(material_asset_path)
    yaml_texture_asset_path = _yaml_scalar(texture_asset_path)
    yaml_source_texture_relative_path = _yaml_scalar(source_texture_relative_path)
    yaml_bootstrap_name = _yaml_scalar("GameAnalysisVfxBootstrap")

    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100000
GameObject:
  m_ObjectHideFlags: 0
  m_Component:
  - component: {{fileID: 100001}}
  - component: {{fileID: 100002}}
  - component: {{fileID: 100003}}
  - component: {{fileID: 100004}}
  m_Layer: 0
  m_Name: {yaml_name}
  m_TagString: Untagged
  m_IsActive: 1
--- !u!4 &100001
Transform:
  m_GameObject: {{fileID: 100000}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children: []
  m_Father: {{fileID: 0}}
--- !u!198 &100002
ParticleSystem:
  m_GameObject: {{fileID: 100000}}
  lengthInSec: {duration}
  looping: {looping}
  moveWithTransform: 1
  InitialModule:
    enabled: 1
  EmissionModule:
    enabled: 1
    rateOverTime: {rate_over_time}
--- !u!199 &100003
ParticleSystemRenderer:
  m_GameObject: {{fileID: 100000}}
  m_Enabled: 1
  m_RenderMode: 0
  m_Materials:
  - {{fileID: 2100000, guid: {material_guid}, type: 2}}
  m_SortingLayer: {sorting_layer}
  m_SortingOrder: {sorting_order}
--- !u!114 &100004
MonoBehaviour:
  m_GameObject: {{fileID: 100000}}
  m_Enabled: 1
  m_Script: {{fileID: 11500000, guid: {script_guid}, type: 3}}
  m_Name: {yaml_bootstrap_name}
  metadata: {{fileID: 4900000, guid: {metadata_guid}, type: 3}}
  metadataAssetPath: {yaml_metadata_asset_path}
  material: {{fileID: 2100000, guid: {material_guid}, type: 2}}
  materialAssetPath: {yaml_material_asset_path}
  textureAssetPath: {yaml_texture_asset_path}
  sourceTextureRelativePath: {yaml_source_texture_relative_path}
"""


def _material_yaml(name: str, source_texture_relative_path: str, texture_guid: str) -> str:
    yaml_name = _yaml_scalar(name)
    yaml_source_texture_relative_path = _yaml_scalar(source_texture_relative_path)
    texture_ref = f"{{fileID: 2800000, guid: {texture_guid}, type: 3}}" if texture_guid else "{fileID: 0}"
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {yaml_name}
  m_Shader: {{fileID: 10755, guid: 0000000000000000f000000000000000, type: 0}}
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {texture_ref}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
    m_Floats: []
    m_Colors: []
  m_CustomRenderQueue: -1
  stringTagMap: {{}}
  disabledShaderPasses: []
  # sourceTextureRelativePath: {yaml_source_texture_relative_path}
"""


def _curve_constant(value: Any, default: float) -> float:
    if isinstance(value, dict):
        for key in ["constant", "constantMax", "constantMin"]:
            candidate = value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _shape_type_name(value: Any) -> str:
    return _enum_name(value, SHAPE_TYPE_BY_ENUM)


def _render_mode_name(value: Any) -> str:
    return _enum_name(value, RENDER_MODE_BY_ENUM)


def _enum_name(value: Any, mapping: dict[int, str]) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return mapping.get(value, f"Unknown({value})")
    if isinstance(value, float) and value.is_integer():
        numeric = int(value)
        return mapping.get(numeric, f"Unknown({numeric})")
    return str(value)


def _schema_gradient(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = copy.deepcopy(value)
    for key in ["color", "colorMin", "colorMax"]:
        if key in normalized:
            normalized[key] = _schema_color_value(normalized[key])
    return normalized


def _schema_color_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return f"rgba({', '.join(_compact_number(item) for item in value)})"
    if isinstance(value, dict):
        channels = [
            value.get("r"),
            value.get("g"),
            value.get("b"),
            value.get("a", 1),
        ]
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in channels):
            return f"rgba({', '.join(_compact_number(item) for item in channels)})"
    return str(value)


def _compact_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _stable_guid(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def _cleanup_export_fragment(export_root: Path, *paths: Path) -> None:
    root = export_root.resolve(strict=False)
    for path in paths:
        for candidate in [path, path.with_suffix(path.suffix + ".meta")]:
            try:
                candidate.resolve(strict=False).relative_to(root)
            except (OSError, ValueError):
                continue
            try:
                if candidate.is_file():
                    candidate.unlink()
            except OSError:
                pass


def _mono_script_meta(guid: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
MonoImporter:
  externalObjects: {{}}
  serializedVersion: 2
  defaultReferences: []
  executionOrder: 0
  icon: {{instanceID: 0}}
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def _text_asset_meta(guid: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
TextScriptImporter:
  externalObjects: {{}}
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def _material_meta(guid: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
NativeFormatImporter:
  externalObjects: {{}}
  mainObjectFileID: 2100000
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def _texture_meta(guid: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 0
  isReadable: 0
  textureType: 0
  textureShape: 1
  sRGBTexture: 1
  alphaSource: 1
  alphaIsTransparency: 1
  maxTextureSize: 2048
  textureCompression: 1
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def _prefab_meta(guid: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid}
PrefabImporter:
  externalObjects: {{}}
  userData:
  assetBundleName:
  assetBundleVariant:
"""


def _first_bool(payload: dict[str, Any], *keys: str, default: bool) -> bool:
    for key in keys:
        value = payload.get(key)
        if type(value) is bool:
            return value
    return default


def _yaml_bool(value: bool) -> int:
    return 1 if value else 0


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{text}"'

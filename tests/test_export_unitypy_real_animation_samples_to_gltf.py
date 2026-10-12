from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_unitypy_real_animation_samples_to_gltf.py"


def load_module():
    assert SCRIPT.exists(), "real animation sample exporter script is missing"
    spec = importlib.util.spec_from_file_location("export_unitypy_real_animation_samples_to_gltf", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_write_skinned_animation_gltf_contains_skin_weights_and_embedded_origin(tmp_path: Path) -> None:
    module = load_module()
    out = tmp_path / "hero.gltf"
    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]

    module.write_skinned_animation_gltf(
        gltf_path=out,
        model_name="hero",
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        normals=[(0.0, 0.0, 1.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        joints=[(0, 0, 0, 0), (0, 1, 0, 0), (1, 0, 0, 0)],
        weights=[(1.0, 0.0, 0.0, 0.0), (0.5, 0.5, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)],
        indices=[0, 1, 2],
        skeleton_nodes=[
            {
                "name": "root",
                "translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
                "children": [1],
            },
            {
                "name": "child",
                "translation": [0.0, 1.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0],
                "children": [],
            },
        ],
        joint_node_indices=[0, 1],
        skeleton_root_node=0,
        inverse_bind_matrices=[identity, identity],
        animations=[
            {
                "name": "Run",
                "channels": [
                    {
                        "node": 1,
                        "path": "rotation",
                        "times": [0.0, 1.0],
                        "values": [(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.7071068, 0.7071068)],
                    }
                ],
            }
        ],
        animation_origin="embedded",
        extras={"source_clip": "Run"},
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    primitive = payload["meshes"][0]["primitives"][0]
    assert "JOINTS_0" in primitive["attributes"]
    assert "WEIGHTS_0" in primitive["attributes"]
    assert payload["skins"][0]["joints"] == [0, 1]
    assert payload["nodes"][-1]["mesh"] == 0
    assert payload["nodes"][-1]["skin"] == 0
    assert payload["animations"][0]["name"] == "Run"
    assert payload["extras"]["game_analysis"]["animation_origin"] == "embedded"
    assert payload["extras"]["game_analysis"]["source_clip"] == "Run"
    assert out.with_suffix(".bin").exists()


def test_grouped_sample_helpers_keep_primary_model_identity_and_mixed_origin() -> None:
    module = load_module()

    assert module.grouped_sample_output_name("man003_10_clothes") == "man003_10_clothes"
    assert module.grouped_animation_origin(["embedded", "mecanim_dense_preview"]) == "mixed"
    assert module.grouped_animation_origin(["embedded", "embedded"]) == "embedded"


def test_iter_unity_sources_accepts_trusted_magic_dat_and_rejects_random_dat(
    tmp_path: Path,
) -> None:
    module = load_module()
    (tmp_path / "valid.dat").write_bytes(b"UnityRaw\x00synthetic")
    (tmp_path / "random.dat").write_bytes(b"not-a-unity-container")

    discovered = {path.name for path in module.iter_unity_sources(tmp_path)}

    assert discovered == {"valid.dat"}


class FakeCurve:
    def __init__(self, path: str) -> None:
        self.path = path


class FakeClip:
    def __init__(self) -> None:
        self.m_Name = "Run"
        self.m_RotationCurves = [FakeCurve("Root/Hips/Leg"), FakeCurve("Root/Hips/Arm")]
        self.m_PositionCurves = [FakeCurve("Root/Hips")]
        self.m_ScaleCurves = [FakeCurve("Root/Unmatched")]


def test_score_direct_clip_against_skeleton_reports_match_ratio() -> None:
    module = load_module()

    score = module.score_direct_clip_against_skeleton(FakeClip(), {"Root/Hips": 0, "Root/Hips/Leg": 1})

    assert score["clip_name"] == "Run"
    assert score["clip_channel_count"] == 4
    assert score["matched_channel_count"] == 2
    assert score["match_ratio"] == 0.5
    assert score["failure_reason"] == ""

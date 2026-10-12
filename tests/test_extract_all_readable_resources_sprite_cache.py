from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from UnityPy.export import SpriteHelper

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


class FakeCacheOwner:
    def __init__(self) -> None:
        self._cache: dict[Any, Any] = {}


class FakeSprite:
    def __init__(self, owner: FakeCacheOwner) -> None:
        self.assets_file = owner


class FakeTexturePointer:
    def __init__(self, path_id: int) -> None:
        self.path_id = path_id

    def deref_parse_as_object(self) -> object:
        return object()


def test_real_unitypy_spritehelper_cache_is_reused_then_evicted_by_decoded_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = FakeCacheOwner()
    sprite = FakeSprite(owner)
    texture = FakeTexturePointer(91)
    decode_calls: list[int] = []

    def decode_texture(_: object, __: bool) -> Image.Image:
        decode_calls.append(1)
        return Image.new("RGBA", (64, 64), (1, 2, 3, 4))

    monkeypatch.setattr(SpriteHelper, "get_image_from_texture2d", decode_texture)

    first = SpriteHelper.get_image(sprite, texture, None)  # type: ignore[arg-type]
    second = SpriteHelper.get_image(sprite, texture, None)  # type: ignore[arg-type]

    assert first is second
    assert decode_calls == [1]
    estimated = extractor.estimate_sprite_cache_image_bytes(first)
    assert isinstance(estimated, int) and estimated > 0
    budget = extractor.SpriteTextureCacheBudget(estimated - 1, 8)
    budget.enforce([owner._cache])

    assert owner._cache == {}
    evidence = budget.evidence()
    assert evidence["peak_observed_entries_before_eviction"] == 1
    assert evidence["peak_observed_estimated_bytes_before_eviction"] == estimated
    assert evidence["peak_retained_entries_after_eviction"] == 0
    assert evidence["evicted_entries"] == 1
    with pytest.raises(ValueError):
        first.getpixel((0, 0))


def test_sprite_cache_entry_window_keeps_recent_atlases_and_closes_oldest() -> None:
    cache: dict[Any, Any] = {}
    budget = extractor.SpriteTextureCacheBudget(1024 * 1024, 2)
    images = [Image.new("RGBA", (8, 8), index) for index in range(3)]

    cache[1] = images[0]
    budget.enforce([cache])
    cache[2] = images[1]
    budget.enforce([cache])
    # Re-observing the same two entries retains both for adjacent Sprite reuse.
    budget.enforce([cache])
    assert list(cache) == [1, 2]

    cache[3] = images[2]
    budget.enforce([cache])

    assert list(cache) == [2, 3]
    assert budget.evidence()["peak_retained_entries_after_eviction"] == 2
    with pytest.raises(ValueError):
        images[0].getpixel((0, 0))
    budget.clear_all([cache])
    assert cache == {}


class FakeType:
    name = "Sprite"


class FakeObject:
    type = FakeType()

    def __init__(self, path_id: int) -> None:
        self.path_id = path_id


class FakeSerializedFile(FakeCacheOwner):
    def __init__(self, objects: list[FakeObject]) -> None:
        super().__init__()
        self.objects = {obj.path_id: obj for obj in objects}


class FakeEnv:
    container: dict[str, Any] = {}


def make_worker_manifest(root: Path) -> Path:
    manifest_path = root / "manifest.json"
    extractor.atomic_write_json(
        manifest_path,
        {
            "status": "complete",
            "identity": {"strategy_version": 1, "source_path": "synthetic"},
            "nodes": [
                {
                    "index": 0,
                    "flags": 4,
                    "path": "CAB-sprite-cache",
                    "sha256": "synthetic-node-hash",
                }
            ],
        },
    )
    return manifest_path


def make_row(obj: FakeObject, source_label: str) -> dict[str, Any]:
    row = {field: "" for field in extractor.INDEX_FIELDS}
    row.update(
        {
            "asset_id": f"sprite-{obj.path_id}",
            "source_file": source_label,
            "path_id": obj.path_id,
            "asset_type": "Sprite",
            "asset_name": f"sprite-{obj.path_id}",
            "export_kind": "png",
            "export_path": f"images/Sprite/{obj.path_id}.png",
            "width": 32,
            "height": 32,
            "status": "exported",
        }
    )
    return row


def test_worker_preserves_local_reuse_but_commits_with_zero_cached_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    spool_root = export_root / extractor.RESUME_STATE_DIR_NAME / "spool"
    manifest_path = make_worker_manifest(spool_root)
    serialized = FakeSerializedFile([FakeObject(1), FakeObject(2), FakeObject(3)])
    monkeypatch.setattr(
        extractor,
        "load_spooled_serialized_environment",
        lambda *_: (FakeEnv(), serialized),
    )
    cache_seen_at_entry: list[list[int]] = []
    created_images: list[Image.Image] = []

    def fake_export(
        obj: FakeObject,
        source_label: str,
        _container_paths: dict[int, str],
        _export_root: Path,
        _audio_mode: str,
        **_: Any,
    ) -> tuple[dict[str, Any], bool]:
        cache_seen_at_entry.append(list(serialized._cache))
        texture_key = 10 if obj.path_id < 3 else 20
        if texture_key not in serialized._cache:
            image = Image.new("RGBA", (32, 32), obj.path_id)
            created_images.append(image)
            serialized._cache[texture_key] = image
        return make_row(obj, source_label), True

    monkeypatch.setattr(extractor, "export_object", fake_export)
    budget_probe = Image.new("RGBA", (32, 32))
    one_image_budget = extractor.estimate_sprite_cache_image_bytes(budget_probe)
    budget_probe.close()
    assert isinstance(one_image_budget, int)
    request = extractor.large_bundle_batch_request(
        spool_manifest_path=manifest_path,
        serialized_node_index=0,
        start=0,
        batch_size=3,
        source_label="source.bundle",
        export_root=export_root,
        audio_mode="metadata",
        sprite_cache_max_bytes=one_image_budget,
        sprite_cache_max_entries=1,
    )
    batch_csv = Path(request["batch_csv_path"])
    batch_result = Path(request["batch_result_path"])
    result = extractor.large_bundle_worker_export_batch(
        spool_manifest_path=manifest_path,
        serialized_node_index=0,
        start=0,
        batch_size=3,
        source_label="source.bundle",
        export_root=export_root,
        audio_mode="metadata",
        batch_csv_path=batch_csv,
        batch_result_path=batch_result,
        sprite_cache_max_bytes=one_image_budget,
        sprite_cache_max_entries=1,
    )

    assert cache_seen_at_entry == [[], [10], [10]]
    assert serialized._cache == {}
    evidence = result["sprite_texture_cache"]
    assert evidence["peak_observed_entries_before_eviction"] == 2
    assert evidence["peak_retained_entries_after_eviction"] == 1
    assert evidence["evicted_entries"] == 1
    assert evidence["shutdown_cleared_entries"] == 1
    assert evidence["retained_entries_at_commit"] == 0
    rows = list(csv.DictReader(batch_csv.open(encoding="utf-8"), fieldnames=extractor.INDEX_FIELDS))
    assert [row["asset_id"] for row in rows] == ["sprite-1", "sprite-2", "sprite-3"]
    assert extractor.validate_large_bundle_batch_result(request)["row_count"] == 3
    with pytest.raises(extractor.LargeBundleWorkerError, match="not parent-committed"):
        extractor.validate_large_bundle_batch_result(
            request,
            require_worker_evidence=True,
        )

    tampered = json.loads(batch_result.read_text(encoding="utf-8"))
    tampered["sprite_texture_cache"][
        "peak_retained_estimated_bytes_after_eviction"
    ] = one_image_budget + 1
    extractor.atomic_write_json(batch_result, tampered)
    with pytest.raises(extractor.LargeBundleWorkerError, match="configured boundary"):
        extractor.validate_large_bundle_batch_result(request)


def test_worker_interrupt_clears_new_sprite_cache_entry_without_committing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    spool_root = export_root / extractor.RESUME_STATE_DIR_NAME / "spool"
    manifest_path = make_worker_manifest(spool_root)
    serialized = FakeSerializedFile([FakeObject(1)])
    monkeypatch.setattr(
        extractor,
        "load_spooled_serialized_environment",
        lambda *_: (FakeEnv(), serialized),
    )
    cached_image = Image.new("RGBA", (64, 64), 7)

    def interrupting_export(*_: Any, **__: Any) -> tuple[dict[str, Any], bool]:
        serialized._cache[77] = cached_image
        raise KeyboardInterrupt("synthetic Sprite export interruption")

    monkeypatch.setattr(extractor, "export_object", interrupting_export)
    batch_csv = spool_root / "batches" / "node_000000" / "batch_0.csv"
    batch_result = batch_csv.with_suffix(".json")

    with pytest.raises(KeyboardInterrupt, match="synthetic Sprite export interruption"):
        extractor.large_bundle_worker_export_batch(
            spool_manifest_path=manifest_path,
            serialized_node_index=0,
            start=0,
            batch_size=10,
            source_label="source.bundle",
            export_root=export_root,
            audio_mode="metadata",
            batch_csv_path=batch_csv,
            batch_result_path=batch_result,
            sprite_cache_max_bytes=1024 * 1024,
            sprite_cache_max_entries=8,
        )

    assert serialized._cache == {}
    assert not batch_csv.exists()
    assert not batch_result.exists()
    with pytest.raises(ValueError):
        cached_image.getpixel((0, 0))

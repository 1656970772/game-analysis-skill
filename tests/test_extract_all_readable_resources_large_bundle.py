from __future__ import annotations

import csv
import hashlib
import io
import json
import lzma
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import extract_all_readable_resources as extractor  # noqa: E402


def unity_lzma(payload: bytes, *, dictionary_size: int = 1 << 20) -> bytes:
    filters = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": dictionary_size,
            "lc": 3,
            "lp": 0,
            "pb": 2,
        }
    ]
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filters)
    return struct.pack("<BI", 0x5D, dictionary_size) + compressor.compress(payload) + compressor.flush()


def make_unityfs(
    path: Path,
    node_payloads: list[tuple[str, int, bytes]],
) -> tuple[dict[str, Any], bytes]:
    payload = b"".join(node_payload for _, _, node_payload in node_payloads)
    compressed = unity_lzma(payload)
    info = bytearray(b"\0" * 16)
    info.extend(struct.pack(">iIIH", 1, len(payload), len(compressed), 65))
    info.extend(struct.pack(">i", len(node_payloads)))
    offset = 0
    for name, flags, node_payload in node_payloads:
        info.extend(struct.pack(">qqI", offset, len(node_payload), flags))
        info.extend(name.encode("utf-8") + b"\0")
        offset += len(node_payload)
    prefix = (
        b"UnityFS\0"
        + struct.pack(">I", 6)
        + b"5.x.x\0"
        + b"2018.4.36f1\0"
    )
    provisional_header = prefix + struct.pack(">qIII", 0, len(info), len(info), 64)
    total_size = len(provisional_header) + len(info) + len(compressed)
    header = prefix + struct.pack(">qIII", total_size, len(info), len(info), 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + info + compressed)
    layout = extractor.probe_unityfs_layout(path)
    assert layout is not None
    return layout, payload


class TrackingReader(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_requests.append(size)
        return super().read(size)


def test_streamed_lzma_never_reads_or_returns_the_full_block() -> None:
    payload = (b"bounded-memory-unityfs-" * 200) + b"tail"
    compressed = unity_lzma(payload)
    reader = TrackingReader(compressed)
    block = {
        "index": 0,
        "compressed_offset": 0,
        "compressed_size": len(compressed),
        "uncompressed_size": len(payload),
        "flags": 65,
    }

    chunks = list(
        extractor.iter_unityfs_block_chunks(
            reader,
            block,
            input_chunk_bytes=17,
            output_chunk_bytes=23,
            full_block_limit_bytes=100,
        )
    )

    assert b"".join(chunks) == payload
    assert max(len(chunk) for chunk in chunks) <= 23
    assert max(size for size in reader.read_requests if size >= 0) <= 17
    assert len(payload) not in reader.read_requests


def test_many_small_blocks_with_one_large_node_uses_large_bundle_strategy() -> None:
    layout = {
        "blocks": [
            {"uncompressed_size": 128 * 1024},
            {"uncompressed_size": 128 * 1024},
        ],
        "nodes": [{"size": 34 * 1024 * 1024}],
    }

    assert extractor.should_use_large_bundle_strategy(
        layout,
        block_threshold_bytes=4 * 1024 * 1024,
        node_threshold_bytes=16 * 1024 * 1024,
    )


def test_bundle_below_both_block_and_node_thresholds_uses_normal_strategy() -> None:
    layout = {
        "blocks": [{"uncompressed_size": 128 * 1024}],
        "nodes": [{"size": 8 * 1024 * 1024}],
    }

    assert not extractor.should_use_large_bundle_strategy(
        layout,
        block_threshold_bytes=4 * 1024 * 1024,
        node_threshold_bytes=16 * 1024 * 1024,
    )


def test_spool_routes_chunks_across_node_boundaries_and_supports_chinese_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "中文来源" / "特效包"
    nodes = [
        ("CAB-safe", 4, b"1234567"),
        ("CAB-safe.resS", 0, b"abcdefghijk"),
        ("CAB-safe.resource", 0, "尾部".encode("utf-8")),
    ]
    layout, _ = make_unityfs(source, nodes)
    spool_root = tmp_path / "中文导出" / ".unitypy_resume" / "large_bundle_spool" / "x"

    manifest, evidence = extractor.spool_large_unityfs_bundle(
        source,
        layout,
        spool_root,
        io_chunk_bytes=5,
        full_block_limit_bytes=16,
    )

    assert manifest["status"] == "complete"
    assert evidence["streaming_lzma"] is True
    for record, (_, _, expected) in zip(manifest["nodes"], nodes):
        assert (spool_root / record["spool_path"]).read_bytes() == expected


@pytest.mark.parametrize(
    "nodes,match",
    [
        ([{"index": 0, "path": "../escape", "offset": 0, "size": 1}], "Unsafe"),
        (
            [
                {"index": 0, "path": "a/CAB.resS", "offset": 0, "size": 1},
                {"index": 1, "path": "b/cab.ress", "offset": 1, "size": 1},
            ],
            "basename collision",
        ),
    ],
)
def test_spool_rejects_path_escape_and_dependency_collisions(
    nodes: list[dict[str, Any]],
    match: str,
) -> None:
    with pytest.raises(extractor.LargeBundleError, match=match):
        extractor.validate_unityfs_nodes(nodes, 2)


def test_spool_interruption_reuses_hashed_nodes_and_rebuilds_corruption(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "large.bundle"
    nodes = [
        ("CAB-resume", 4, b"first-node"),
        ("CAB-resume.resS", 0, b"second-node-payload"),
    ]
    layout, _ = make_unityfs(source, nodes)
    spool_root = tmp_path / "out" / ".unitypy_resume" / "large_bundle_spool" / "resume"
    commits: list[int] = []

    def interrupt_after_first(record: dict[str, Any]) -> None:
        commits.append(int(record["index"]))
        if len(commits) == 1:
            raise RuntimeError("synthetic spool interruption")

    with pytest.raises(RuntimeError, match="synthetic spool interruption"):
        extractor.spool_large_unityfs_bundle(
            source,
            layout,
            spool_root,
            io_chunk_bytes=4,
            full_block_limit_bytes=8,
            on_node_committed=interrupt_after_first,
        )

    interrupted = json.loads((spool_root / "manifest.json").read_text(encoding="utf-8"))
    assert interrupted["nodes"][0]["status"] == "completed"
    manifest, evidence = extractor.spool_large_unityfs_bundle(
        source,
        layout,
        spool_root,
        io_chunk_bytes=4,
        full_block_limit_bytes=8,
    )
    assert evidence["reused_node_count_this_run"] == 1
    first_path = spool_root / manifest["nodes"][0]["spool_path"]
    first_path.write_bytes(b"corrupt")

    repaired, repair_evidence = extractor.spool_large_unityfs_bundle(
        source,
        layout,
        spool_root,
        io_chunk_bytes=4,
        full_block_limit_bytes=8,
    )

    assert first_path.read_bytes() == nodes[0][2]
    assert repaired["nodes"][0]["sha256"] == hashlib.sha256(nodes[0][2]).hexdigest()
    assert repair_evidence["reused_node_count_this_run"] == 1


def _write_fake_batch(request: dict[str, Any], total_rows: int) -> dict[str, Any]:
    start = int(request["start"])
    batch_size = int(request["batch_size"])
    end = min(start + batch_size, total_rows)
    csv_path = Path(request["batch_csv_path"])
    result_path = Path(request["batch_result_path"])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=extractor.INDEX_FIELDS)
        for index in range(start, end):
            writer.writerow(
                {
                    "asset_id": f"asset-{index}",
                    "source_file": request["source_label"],
                    "path_id": index,
                    "asset_type": "Material",
                    "asset_name": f"row-{index}",
                    "container_path": "",
                    "export_kind": "metadata",
                    "export_path": f"metadata/Material/{index}.json",
                    "width": "",
                    "height": "",
                    "status": "exported",
                    "remarks": "",
                }
            )
    manifest_path = Path(request["spool_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    node = next(
        record
        for record in manifest["nodes"]
        if int(record["index"]) == int(request["serialized_node_index"])
    )
    batch_identity = {
        "strategy_version": extractor.LARGE_BUNDLE_STRATEGY_VERSION,
        "spool_identity": manifest["identity"],
        "serialized_node_index": int(request["serialized_node_index"]),
        "serialized_node_sha256": node["sha256"],
        "start": start,
        "batch_size": batch_size,
        "source_label": request["source_label"],
        "export_root": extractor.normalize_root(Path(request["export_root"])),
        "audio_mode": request["audio_mode"],
        "sprite_cache_max_bytes": request["sprite_cache_max_bytes"],
        "sprite_cache_max_entries": request["sprite_cache_max_entries"],
    }
    if request.get("asset_identity_scheme") == extractor.ASSET_IDENTITY_SCHEME:
        batch_identity["asset_identity_scheme"] = extractor.ASSET_IDENTITY_SCHEME
    result = {
        "status": "completed",
        "identity": batch_identity,
        "start": start,
        "end": end,
        "row_count": end - start,
        "has_more": end < total_rows,
        "type_counts": {"Material": end - start},
        "exported_counts": {"Material": end - start},
        "csv_path": csv_path.relative_to(manifest_path.parent).as_posix(),
        "csv_sha256": extractor.sha256_file(csv_path),
        "sprite_texture_cache": {
            "policy": "oldest_inserted_decoded_image_window",
            "max_estimated_decoded_bytes": request["sprite_cache_max_bytes"],
            "max_entries": request["sprite_cache_max_entries"],
            "peak_observed_estimated_bytes_before_eviction": 0,
            "peak_retained_estimated_bytes_after_eviction": 0,
            "peak_retained_entries_after_eviction": 0,
            "evicted_entries": 0,
            "close_errors": 0,
            "retained_entries_at_commit": 0,
            "retained_estimated_bytes_at_commit": 0,
        },
        "worker_evidence": {
            "memory_method": "unavailable",
            "memory_sampling_status": "unavailable",
            "memory_unavailable_reason": "root_process_identity_unavailable",
            "memory_peak_semantics": extractor.WINDOWS_PROCESS_TREE_PEAK_SEMANTICS,
            "root_pid": 999,
            "root_creation_time_100ns": None,
            "root_identity_verified": False,
            "poll_count": 0,
            "successful_process_sample_count": 0,
            "successful_snapshot_count": 0,
            "root_snapshot_verification_count": 0,
            "snapshot_failure_count": 0,
            "process_query_failure_count": 1,
            "pid_reuse_rejection_count": 0,
            "observed_processes": [],
        },
    }
    extractor.atomic_write_json(result_path, result)
    return result


def test_batch_interruption_resume_preserves_order_and_never_commits_partial_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "large.bundle"
    layout, _ = make_unityfs(source, [("CAB-batch", 4, b"synthetic serialized bytes")])
    export_root = tmp_path / "export"
    state_dir = export_root / extractor.RESUME_STATE_DIR_NAME
    fragments = state_dir / "fragments"
    fragments.mkdir(parents=True)
    fragment = fragments / "large.csv"
    partial = fragments / "large.csv.partial"
    calls: list[int] = []
    fail_once = {2}

    def runner(request: dict[str, Any]) -> dict[str, Any]:
        start = int(request["start"])
        calls.append(start)
        if start in fail_once:
            fail_once.remove(start)
            raise extractor.LargeBundleWorkerError("synthetic worker failure")
        return _write_fake_batch(request, total_rows=5)

    with pytest.raises(extractor.LargeBundleWorkerError, match="synthetic worker failure"):
        extractor.export_large_bundle_source(
            source_path=source,
            source_label="中文_Data/StreamingAssets/large.bundle",
            export_root=export_root,
            state_dir=state_dir,
            layout=layout,
            fragment_temp=partial,
            fragment_path=fragment,
            audio_mode="metadata",
            threshold_bytes=8,
            io_chunk_bytes=4,
            batch_size=2,
            worker_runner=runner,
        )
    assert not fragment.exists()
    assert calls == [0, 2]

    calls.clear()
    result = extractor.export_large_bundle_source(
        source_path=source,
        source_label="中文_Data/StreamingAssets/large.bundle",
        export_root=export_root,
        state_dir=state_dir,
        layout=layout,
        fragment_temp=partial,
        fragment_path=fragment,
        audio_mode="metadata",
        threshold_bytes=8,
        io_chunk_bytes=4,
        batch_size=2,
        worker_runner=runner,
    )

    assert calls == [2, 4]
    assert result["row_count"] == 5
    assert result["large_bundle"]["batches"]["reused_batch_count_this_attempt"] == 1
    rows = list(csv.DictReader(fragment.open(encoding="utf-8"), fieldnames=extractor.INDEX_FIELDS))
    assert [row["asset_id"] for row in rows] == [f"asset-{index}" for index in range(5)]
    assert len({row["asset_id"] for row in rows}) == 5


def test_protocol_one_checkpoint_with_732_completed_sources_is_preserved(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "out" / extractor.RESUME_STATE_DIR_NAME / "checkpoint.json"
    identity = {
        "source_root": "source",
        "export_root": "output",
        "options": {"audio_mode": "metadata"},
        "source_count": 735,
        "source_list_fingerprint": "sources",
        "external_payload_fingerprint": "payloads",
        "addressables_catalog_fingerprint": "catalogs",
    }
    sources = {
        f"Game_Data/level{index}": {"status": "completed", "fragment": f"fragment-{index}"}
        for index in range(732)
    }
    extractor.atomic_write_json(
        checkpoint_path,
        {
            "protocol_version": 1,
            "identity": identity,
            "resume_count": 0,
            "sources": sources,
        },
    )

    checkpoint, resumed = extractor.load_or_create_checkpoint(
        checkpoint_path,
        identity,
        protocol_version=extractor.LEGACY_RESUME_PROTOCOL_VERSION,
    )

    assert resumed is True
    assert checkpoint["protocol_version"] == extractor.LEGACY_RESUME_PROTOCOL_VERSION
    assert extractor.RESUME_PROTOCOL_VERSION == 2
    assert len(checkpoint["sources"]) == 732
    assert all(record["status"] == "completed" for record in checkpoint["sources"].values())


class _WorkerType:
    name = "Material"


class _WorkerAssetsFile:
    name = "CAB-worker"


class _WorkerData:
    m_Name = "worker-material"


class _WorkerObject:
    type = _WorkerType()
    assets_file = _WorkerAssetsFile()

    def __init__(self, path_id: int, *, interrupt: bool = False) -> None:
        self.path_id = path_id
        self.interrupt = interrupt

    def read(self) -> _WorkerData:
        if self.interrupt:
            raise KeyboardInterrupt("synthetic worker interruption")
        return _WorkerData()

    def read_typetree(self) -> dict[str, str]:
        return {"name": "worker-material"}


class _WorkerEnv:
    container: dict[str, Any] = {}


class _WorkerSerialized:
    def __init__(self, objects: list[_WorkerObject]) -> None:
        self.objects = {obj.path_id: obj for obj in objects}


def _make_worker_manifest(root: Path) -> Path:
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
                    "path": "CAB-worker",
                    "sha256": "synthetic-node-hash",
                }
            ],
        },
    )
    return manifest_path


def test_worker_commits_one_batch_with_normal_asset_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "导出"
    spool_root = export_root / extractor.RESUME_STATE_DIR_NAME / "中文spool"
    manifest_path = _make_worker_manifest(spool_root)
    monkeypatch.setattr(
        extractor,
        "load_spooled_serialized_environment",
        lambda *_: (_WorkerEnv(), _WorkerSerialized([_WorkerObject(7), _WorkerObject(8)])),
    )
    batch_csv = spool_root / "batches" / "node_000000" / "batch_0.csv"
    batch_result = batch_csv.with_suffix(".json")

    result = extractor.large_bundle_worker_export_batch(
        spool_manifest_path=manifest_path,
        serialized_node_index=0,
        start=0,
        batch_size=1,
        source_label="中文_Data/StreamingAssets/large.bundle",
        export_root=export_root,
        audio_mode="metadata",
        batch_csv_path=batch_csv,
        batch_result_path=batch_result,
    )

    rows = list(csv.DictReader(batch_csv.open(encoding="utf-8"), fieldnames=extractor.INDEX_FIELDS))
    assert result["row_count"] == 1
    assert result["has_more"] is True
    assert "_CAB-worker_7_sha256_" in rows[0]["asset_id"]
    assert len(rows[0]["asset_id"].rsplit("_", 1)[-1]) == 64
    assert rows[0]["source_file"] == "中文_Data/StreamingAssets/large.bundle"


def test_worker_interruption_does_not_commit_csv_or_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    spool_root = export_root / extractor.RESUME_STATE_DIR_NAME / "spool"
    manifest_path = _make_worker_manifest(spool_root)
    monkeypatch.setattr(
        extractor,
        "load_spooled_serialized_environment",
        lambda *_: (_WorkerEnv(), _WorkerSerialized([_WorkerObject(7, interrupt=True)])),
    )
    batch_csv = spool_root / "batches" / "node_000000" / "batch_0.csv"
    batch_result = batch_csv.with_suffix(".json")

    with pytest.raises(KeyboardInterrupt, match="synthetic worker interruption"):
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
        )

    assert not batch_csv.exists()
    assert not batch_result.exists()

"""F6-2C-O5-C-P1 正式 Chroma 指纹只读溯源审计。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(r"E:\3\posgresql\1")
RUN_ROOT = Path(r"E:\3\_training_backups\f6-2c-o5-c-p1-20260723-104248")
FORMAL = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
SEARCH_ROOTS = (
    Path(r"E:\3\_training_backups"),
    Path(r"E:\3\_evidence_worktrees"),
)
FROZEN_SHA = "d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992"
CURRENT_SHA = "0f163f373d1336e4c34522fb385d3355f1663a75d47184dbd395671f1026144c"
CURRENT_TREE_SHA = "1a55902ef0f9e42e7a0d20cfb8e0d83991f614f7a3ded1639ed477d0bc838471"
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".py"}

sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.storage_snapshot import (  # noqa: E402
    build_directory_manifest,
    create_verified_copy,
)
from tools.refresh_metadata_index import directory_tree_sha256  # noqa: E402


def write_json(name: str, value: Any) -> None:
    path = RUN_ROOT / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_count(path: Path) -> int:
    uri = path.joinpath("chroma.sqlite3").as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        return int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


def manifest_with_timestamps(path: Path) -> dict[str, Any]:
    manifest = build_directory_manifest(path)
    timestamps = {}
    for item in manifest.files:
        stat = path.joinpath(*item.path.split("/")).stat()
        timestamps[item.path] = {
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
        }
    return {
        "manifest": manifest.to_dict(),
        "timestamps": timestamps,
        "timestamp_evidence_trust": "LOW",
    }


def find_chroma_directories() -> list[Path]:
    found: set[Path] = set()
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for sqlite_file in root.rglob("chroma.sqlite3"):
            if sqlite_file.is_file():
                found.add(sqlite_file.parent.resolve())
    return sorted(found, key=lambda item: str(item).lower())


def walk_values(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def find_historical_evidence() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > 20 * 1024 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if FROZEN_SHA not in text:
                continue
            direct_manifest = False
            manifest_total_bytes = None
            manifest_file_count = None
            if path.suffix.lower() == ".json":
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if parsed is not None:
                    for value in walk_values(parsed):
                        if (
                            isinstance(value, dict)
                            and value.get("content_sha256") == FROZEN_SHA
                            and isinstance(value.get("files"), list)
                        ):
                            direct_manifest = True
                            manifest_total_bytes = value.get("total_bytes")
                            manifest_file_count = value.get("file_count")
                            break
            output.append(
                {
                    "path": str(path),
                    "direct_manifest_evidence": direct_manifest,
                    "manifest_file_count": manifest_file_count,
                    "manifest_total_bytes": manifest_total_bytes,
                }
            )
    return output


def physical_diff(
    old_root: Path,
    old_payload: dict[str, Any],
    current_root: Path,
    current_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    old_files = {item["path"]: item for item in old_payload["manifest"]["files"]}
    current_files = {
        item["path"]: item for item in current_payload["manifest"]["files"]
    }
    paths = sorted(set(old_files) | set(current_files))
    output = []
    for path in paths:
        old = old_files.get(path)
        current = current_files.get(path)
        old_time = old_payload["timestamps"].get(path)
        current_time = current_payload["timestamps"].get(path)
        output.append(
            {
                "path": path,
                "old_size": None if old is None else old["size"],
                "current_size": None if current is None else current["size"],
                "size_changed": old is None
                or current is None
                or old["size"] != current["size"],
                "old_sha256": None if old is None else old["sha256"],
                "current_sha256": None if current is None else current["sha256"],
                "content_changed": old is None
                or current is None
                or old["sha256"] != current["sha256"],
                "old_timestamp": old_time,
                "current_timestamp": current_time,
                "timestamp_evidence_trust": "LOW",
                "old_source_root": str(old_root),
                "current_source_root": str(current_root),
            }
        )
    return output


def normalized_logic_inventory(copy_root: Path) -> dict[str, Any]:
    import chromadb

    client = chromadb.PersistentClient(path=str(copy_root))
    records: list[dict[str, Any]] = []
    collection_counts: dict[str, int] = {}
    for collection_stub in sorted(client.list_collections(), key=lambda item: item.name):
        collection = client.get_collection(collection_stub.name)
        payload = collection.get(include=["documents", "metadatas"])
        ids = list(payload.get("ids") or [])
        documents = list(payload.get("documents") or [])
        metadatas = list(payload.get("metadatas") or [])
        collection_counts[collection.name] = len(ids)
        for index, record_id in enumerate(ids):
            document = documents[index] if index < len(documents) else None
            metadata = metadatas[index] if index < len(metadatas) else None
            normalized_metadata = metadata if isinstance(metadata, dict) else {}
            records.append(
                {
                    "collection": collection.name,
                    "record_id": str(record_id),
                    "document_sha256": hashlib.sha256(
                        ("" if document is None else str(document)).encode("utf-8")
                    ).hexdigest(),
                    "metadata_sha256": canonical_sha256(normalized_metadata),
                    "tool_name": normalized_metadata.get("tool_name"),
                    "sample_id": normalized_metadata.get("sample_id"),
                    "training_level": normalized_metadata.get("training_level"),
                    "classification": normalized_metadata.get("classification"),
                }
            )
    records.sort(key=lambda item: (item["collection"], item["record_id"]))
    return {
        "record_count": len(records),
        "collection_counts": collection_counts,
        "records": records,
        "logical_total_sha256": canonical_sha256(records),
        "embeddings_exported": False,
    }


def compare_logic(current: dict[str, Any], frozen: dict[str, Any]) -> dict[str, Any]:
    current_map = {
        (item["collection"], item["record_id"]): item for item in current["records"]
    }
    frozen_map = {
        (item["collection"], item["record_id"]): item for item in frozen["records"]
    }
    current_ids = set(current_map)
    frozen_ids = set(frozen_map)
    current_document_multiset = Counter(
        item["document_sha256"] for item in current["records"]
    )
    frozen_document_multiset = Counter(
        item["document_sha256"] for item in frozen["records"]
    )
    current_metadata_multiset = Counter(
        item["metadata_sha256"] for item in current["records"]
    )
    frozen_metadata_multiset = Counter(
        item["metadata_sha256"] for item in frozen["records"]
    )
    shared = sorted(current_ids & frozen_ids)
    document_differences = [
        {"collection": key[0], "record_id": key[1]}
        for key in shared
        if current_map[key]["document_sha256"] != frozen_map[key]["document_sha256"]
    ]
    metadata_differences = [
        {"collection": key[0], "record_id": key[1]}
        for key in shared
        if current_map[key]["metadata_sha256"] != frozen_map[key]["metadata_sha256"]
    ]
    return {
        "record_ids_equal": current_ids == frozen_ids,
        "document_multiset_equal": (
            current_document_multiset == frozen_document_multiset
        ),
        "metadata_multiset_equal": (
            current_metadata_multiset == frozen_metadata_multiset
        ),
        "missing_from_current": [
            {"collection": key[0], "record_id": key[1]}
            for key in sorted(frozen_ids - current_ids)
        ],
        "added_in_current": [
            {"collection": key[0], "record_id": key[1]}
            for key in sorted(current_ids - frozen_ids)
        ],
        "documents_equal": not document_differences,
        "document_differences": document_differences,
        "metadata_equal": not metadata_differences,
        "metadata_differences": metadata_differences,
        "logical_total_sha256_current": current["logical_total_sha256"],
        "logical_total_sha256_d8eb": frozen["logical_total_sha256"],
    }


def main() -> None:
    current_preflight = manifest_with_timestamps(FORMAL)
    current_records = record_count(FORMAL)
    current_tree = directory_tree_sha256(FORMAL)
    write_json(
        "formal-asset-preflight.json",
        {
            "metadata_sha256": hashlib.sha256(
                PROJECT_ROOT.joinpath(
                    "agent_data", "column_metadata_index.json"
                ).read_bytes()
            ).hexdigest(),
            "record_count": current_records,
            "manifest_content_sha256": current_preflight["manifest"][
                "content_sha256"
            ],
            "tree_sha256": current_tree,
            "formal_chroma_opened_by_chroma_api": False,
        },
    )
    write_json("current-formal-manifest.json", current_preflight)

    candidate_path = RUN_ROOT / "backup-candidate-inventory.json"
    if candidate_path.is_file():
        candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidates = candidate_payload["candidates"]
        exact_sources = [
            Path(item["path"])
            for item in candidates
            if item.get("exact_d8eb_match") is True
        ]
    else:
        candidates = []
        exact_sources = []
        for path in find_chroma_directories():
            item: dict[str, Any] = {"path": str(path)}
            try:
                manifest = build_directory_manifest(path)
                item.update(
                    {
                        "record_count": record_count(path),
                        "file_count": manifest.file_count,
                        "total_bytes": manifest.total_bytes,
                        "manifest_content_sha256": manifest.content_sha256,
                        "exact_d8eb_match": manifest.content_sha256 == FROZEN_SHA,
                        "source_stage_or_backup_name": path.relative_to(
                            next(
                                root
                                for root in SEARCH_ROOTS
                                if path.is_relative_to(root)
                            )
                        ).parts[0],
                    }
                )
                if item["exact_d8eb_match"]:
                    exact_sources.append(path)
            except Exception as error:
                item["error"] = f"{type(error).__name__}: {error}"
                item["exact_d8eb_match"] = False
            candidates.append(item)
        write_json(
            "backup-candidate-inventory.json",
            {
                "candidate_count": len(candidates),
                "exact_d8eb_source_count": len(exact_sources),
                "candidates": candidates,
            },
        )

    historical_path = RUN_ROOT / "historical-d8eb-evidence.json"
    if historical_path.is_file():
        historical_payload = json.loads(historical_path.read_text(encoding="utf-8"))
        historical = historical_payload["evidence"]
    else:
        historical = find_historical_evidence()
        write_json(
            "historical-d8eb-evidence.json",
            {
                "evidence_file_count": len(historical),
                "exact_manifest_evidence_found": any(
                    item["direct_manifest_evidence"] for item in historical
                ),
                "evidence": historical,
            },
        )

    if not exact_sources:
        (RUN_ROOT / "NOT_FOUND.txt").write_text(
            "No physical Chroma directory with exact d8eb manifest was found.\n",
            encoding="utf-8",
        )
        (RUN_ROOT / "NOT_AVAILABLE.txt").write_text(
            "Physical and logical comparison were not available because no exact d8eb source was found.\n",
            encoding="utf-8",
        )
        write_json(
            "audit-result.json",
            {
                "exact_d8eb_source_found": False,
                "exact_d8eb_manifest_evidence_found": any(
                    item["direct_manifest_evidence"] for item in historical
                ),
                "classification": "D8EB_SOURCE_NOT_FOUND",
            },
        )
    else:
        frozen_source = exact_sources[0]
        frozen_manifest = manifest_with_timestamps(frozen_source)
        write_json(
            "d8eb-manifest.json",
            {
                "source_path": str(frozen_source),
                "record_count": record_count(frozen_source),
                **frozen_manifest,
            },
        )
        diff = physical_diff(
            frozen_source, frozen_manifest, FORMAL, current_preflight
        )
        write_json(
            "d8eb-vs-current-file-diff.json",
            {
                "timestamp_evidence_trust": "LOW",
                "changed_physical_files": sum(
                    int(item["content_changed"]) for item in diff
                ),
                "files": diff,
            },
        )

        current_copy = RUN_ROOT / "current_formal_copy"
        frozen_copy = RUN_ROOT / "d8eb_source_copy"
        current_copy_result = create_verified_copy(
            FORMAL, current_copy, PROJECT_ROOT
        )
        frozen_copy_result = create_verified_copy(
            frozen_source, frozen_copy, PROJECT_ROOT
        )
        current_copy_pre_open = build_directory_manifest(current_copy).to_dict()
        frozen_copy_pre_open = build_directory_manifest(frozen_copy).to_dict()
        current_logic = normalized_logic_inventory(current_copy)
        frozen_logic = normalized_logic_inventory(frozen_copy)
        current_copy_post_open = build_directory_manifest(current_copy).to_dict()
        frozen_copy_post_open = build_directory_manifest(frozen_copy).to_dict()

        write_json("logical-inventory-current.json", current_logic)
        write_json("logical-inventory-d8eb.json", frozen_logic)
        comparison = compare_logic(current_logic, frozen_logic)
        comparison.update(
            {
                "current_copy_source_manifest": current_copy_result.source_before.to_dict(),
                "d8eb_copy_source_manifest": frozen_copy_result.source_before.to_dict(),
                "current_copy_pre_open_manifest": current_copy_pre_open,
                "current_copy_post_open_manifest": current_copy_post_open,
                "d8eb_copy_pre_open_manifest": frozen_copy_pre_open,
                "d8eb_copy_post_open_manifest": frozen_copy_post_open,
                "current_copy_physical_change_on_open": (
                    current_copy_pre_open["content_sha256"]
                    != current_copy_post_open["content_sha256"]
                ),
                "d8eb_copy_physical_change_on_open": (
                    frozen_copy_pre_open["content_sha256"]
                    != frozen_copy_post_open["content_sha256"]
                ),
            }
        )
        write_json("logical-inventory-comparison.json", comparison)
        logical_equal = (
            comparison["record_ids_equal"]
            and comparison["document_multiset_equal"]
            and comparison["metadata_multiset_equal"]
        )
        write_json(
            "audit-result.json",
            {
                "exact_d8eb_source_found": True,
                "exact_d8eb_source_paths": [str(item) for item in exact_sources],
                "selected_d8eb_source_path": str(frozen_source),
                "exact_d8eb_manifest_evidence_found": any(
                    item["direct_manifest_evidence"] for item in historical
                ),
                "logical_comparison_performed": True,
                "classification": (
                    "PHYSICAL_DRIFT_LOGICAL_CONTENT_IDENTICAL"
                    if logical_equal
                    else "LOGICAL_CONTENT_DIFFERENT"
                ),
                **comparison,
            },
        )

    current_postflight = manifest_with_timestamps(FORMAL)
    post_records = record_count(FORMAL)
    post_tree = directory_tree_sha256(FORMAL)
    write_json(
        "formal-asset-postflight.json",
        {
            "metadata_sha256": hashlib.sha256(
                PROJECT_ROOT.joinpath(
                    "agent_data", "column_metadata_index.json"
                ).read_bytes()
            ).hexdigest(),
            "record_count": post_records,
            "manifest_content_sha256": current_postflight["manifest"][
                "content_sha256"
            ],
            "tree_sha256": post_tree,
            "formal_chroma_opened_by_chroma_api": False,
            "formal_asset_modified": (
                current_preflight["manifest"]["content_sha256"]
                != current_postflight["manifest"]["content_sha256"]
                or current_tree != post_tree
                or current_records != post_records
            ),
        },
    )


if __name__ == "__main__":
    main()

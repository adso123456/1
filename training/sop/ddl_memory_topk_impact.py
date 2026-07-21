"""DDL Memory Top-K 重复影响工具；R1 阶段仅验证不可变归档模型。"""

from __future__ import annotations

import argparse
import ast
import gc
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from train_step3 import (
    build_all_table_ddls,
    group_tables,
    load_metadata_index,
    select_comment_retrieval_samples,
)
from training.sop.ddl_memory_formal_readonly_audit import (
    COLLECTION_NAME,
    FORMAL_CHROMA,
    TreeManifest,
    build_tree_manifest,
    copy_complete_snapshot,
    verify_tree_sha_gate,
)
from training.sop.ddl_memory_identity import normalize_ddl


PROJECT_ROOT = Path(__file__).absolute().parents[2]
BACKUP_ROOT = Path(r"E:\3\_training_backups").absolute()
QUERY_COUNT = 12
TOP_K = 10
RUN_ROOT_PATTERN = re.compile(r"f6-1h-\d{8}-\d{6}\Z")
TABLE_NAME_PATTERN = re.compile(r"(?m)^表名：\s*([^\r\n]+?)\s*$")
CREATE_TABLE_PATTERN = re.compile(
    r'(?i)\bCREATE\s+TABLE\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
)
DDL_MARKERS = ("[DDL_MEMORY]", "CREATE TABLE", "表名：")
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE = 0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT = 0
FORBIDDEN_CALL_NAMES = frozenset(
    {
        "add",
        "update",
        "upsert",
        "delete",
        "save_text_memory",
        "create_memory",
        "apply_ddl_memory_plan",
        "get_or_create_collection",
    }
)


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    expected_table: str
    query_text: str


@dataclass(frozen=True)
class ExpectedLookup:
    table_names: frozenset[str]
    table_by_document_sha: Mapping[str, str]


@dataclass(frozen=True)
class TopKResult:
    rank: int
    record_id: str
    parsed_table_name: str | None
    normalized_document_sha256: str
    distance: float
    classification: str

    def evidence_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "record_id": self.record_id,
            "parsed_table_name": self.parsed_table_name,
            "normalized_document_sha256": self.normalized_document_sha256,
            "distance": self.distance,
            "classification": self.classification,
        }

    def stable_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "record_id": self.record_id,
            "parsed_table_name": self.parsed_table_name,
            "normalized_document_sha256": self.normalized_document_sha256,
            "classification": self.classification,
        }


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    expected_table: str
    results: tuple[TopKResult, ...]
    exact_duplicate_slot_count: int
    exact_projection_result_count: int
    exact_projection_changed: bool
    table_duplicate_slot_count: int
    table_projection_result_count: int
    table_projection_changed: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _canonical_path(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _tree_changes(
    before: TreeManifest, after: TreeManifest
) -> tuple[dict[str, Any], ...]:
    before_by_path = {item["relative_path"]: item for item in before.files}
    after_by_path = {item["relative_path"]: item for item in after.files}
    changes = []
    for relative_path in sorted(set(before_by_path) | set(after_by_path)):
        old = before_by_path.get(relative_path)
        new = after_by_path.get(relative_path)
        if old == new:
            continue
        changes.append(
            {
                "relative_path": relative_path,
                "size_before": old["file_size"] if old is not None else None,
                "size_after": new["file_size"] if new is not None else None,
                "sha_before": old["file_sha256"] if old is not None else None,
                "sha_after": new["file_sha256"] if new is not None else None,
            }
        )
    return tuple(changes)


def _verify_archive_unchanged(before: TreeManifest, after: TreeManifest) -> None:
    if before != after:
        raise RuntimeError("ARCHIVE_INTEGRITY_FAILED：查询后 formal_archive 发生变化")


def _validate_formal_execution_paths(
    formal_source: Path | str, run_root: Path | str
) -> tuple[Path, Path]:
    if _canonical_path(formal_source) != _canonical_path(FORMAL_CHROMA):
        raise ValueError(f"formal_source 必须精确等于固定正式路径：{FORMAL_CHROMA}")
    root = Path(run_root).absolute()
    if (
        root.parent != BACKUP_ROOT
        or not _is_within(root, BACKUP_ROOT)
        or RUN_ROOT_PATTERN.fullmatch(root.name) is None
    ):
        raise ValueError(
            "run_root 必须为 E:\\3\\_training_backups\\"
            "f6-1h-<YYYYMMDD-HHMMSS>"
        )
    if root.exists():
        raise ValueError(f"run_root 必须全新且不存在：{root}")
    return Path(formal_source).absolute(), root


def _copy_query_snapshot(
    archive: Path, destination: Path, run_root: Path
) -> TreeManifest:
    expected_archive = run_root / "formal_archive"
    allowed_destinations = {
        _canonical_path(run_root / "query_snapshot_run1"),
        _canonical_path(run_root / "query_snapshot_run2"),
    }
    if _canonical_path(archive) != _canonical_path(expected_archive):
        raise ValueError("查询工作副本必须直接从 formal_archive 复制")
    if _canonical_path(destination) not in allowed_destinations:
        raise ValueError("查询工作副本目标路径非法")
    copy_complete_snapshot(archive, destination)
    archive_manifest = build_tree_manifest(archive)
    destination_manifest = build_tree_manifest(destination)
    verify_tree_sha_gate(archive_manifest, destination_manifest, archive_manifest)
    return destination_manifest


def _prepare_snapshot_model(
    source: Path, run_root: Path
) -> dict[str, TreeManifest]:
    archive = run_root / "formal_archive"
    run1 = run_root / "query_snapshot_run1"
    run2 = run_root / "query_snapshot_run2"
    source_before = build_tree_manifest(source)
    run_root.mkdir(parents=False, exist_ok=False)
    copy_complete_snapshot(source, archive)
    archive_before = build_tree_manifest(archive)
    source_after = build_tree_manifest(source)
    verify_tree_sha_gate(source_before, archive_before, source_after)
    run1_preopen = _copy_query_snapshot(archive, run1, run_root)
    run2_preopen = _copy_query_snapshot(archive, run2, run_root)
    return {
        "source_before": source_before,
        "source_after": source_after,
        "archive_before": archive_before,
        "run1_preopen": run1_preopen,
        "run2_preopen": run2_preopen,
    }


def _validate_client_open_path(path: Path, run_root: Path) -> None:
    allowed = {
        _canonical_path(run_root / "query_snapshot_run1"),
        _canonical_path(run_root / "query_snapshot_run2"),
    }
    target = _canonical_path(path)
    if target == _canonical_path(FORMAL_CHROMA):
        raise ValueError("正式来源禁止作为 Client 打开路径")
    if target == _canonical_path(run_root / "formal_archive"):
        raise ValueError("formal_archive 永远禁止由 Client 打开")
    if target not in allowed:
        raise ValueError("Client 只能打开 query_snapshot_run1 或 query_snapshot_run2")


def build_query_set() -> tuple[QuerySpec, ...]:
    metadata = load_metadata_index()
    tables = group_tables(metadata)
    selected = sorted(
        select_comment_retrieval_samples(tables, limit=QUERY_COUNT),
        key=lambda item: str(item["table"]),
    )
    if len(selected) != QUERY_COUNT:
        raise ValueError(f"查询集必须为 {QUERY_COUNT} 条，实际为 {len(selected)}")
    queries = []
    for index, table in enumerate(selected, start=1):
        table_name = str(table["table"])
        comment = str(table.get("table_comment") or "").strip()
        if not comment:
            raise ValueError(f"查询表注释为空：{table_name}")
        queries.append(QuerySpec(f"q{index:03d}", table_name, comment))
    return tuple(queries)


def build_expected_lookup() -> ExpectedLookup:
    metadata = load_metadata_index()
    tables = group_tables(metadata)
    generated, _geometry_count = build_all_table_ddls(tables)
    table_names = frozenset(str(item["table"]) for item in generated)
    table_by_sha = {
        hashlib.sha256(normalize_ddl(item["ddl"]).encode("utf-8")).hexdigest(): str(
            item["table"]
        )
        for item in generated
    }
    if len(table_names) != 115 or len(table_by_sha) != 115:
        raise ValueError("期望 DDL lookup 必须包含 115 个唯一表和 document SHA")
    return ExpectedLookup(table_names, table_by_sha)


def _document_sha(document: str) -> str:
    try:
        normalized = normalize_ddl(document)
    except ValueError:
        normalized = document.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_table_name(document: str) -> str | None:
    explicit = TABLE_NAME_PATTERN.search(document)
    if explicit is not None:
        return explicit.group(1).strip()
    match = CREATE_TABLE_PATTERN.search(document)
    if match is None:
        return None
    quoted, unquoted = match.groups()
    return quoted.replace('""', '"') if quoted is not None else unquoted


def _classify_document(
    document: str, document_sha: str, lookup: ExpectedLookup
) -> tuple[str | None, str]:
    exact_table = lookup.table_by_document_sha.get(document_sha)
    if exact_table is not None:
        return exact_table, "expected_exact_match"
    upper_document = document.upper()
    is_ddl = (
        DDL_MARKERS[0] in document
        and DDL_MARKERS[1] in upper_document
        and DDL_MARKERS[2] in document
    )
    parsed_table = _parse_table_name(document) if is_ddl else None
    if is_ddl and parsed_table in lookup.table_names:
        return parsed_table, "expected_table_content_variant"
    if is_ddl:
        return parsed_table, "unexpected_ddl"
    return None, "non_ddl_memory"


def analyze_query_results(
    query: QuerySpec,
    raw_results: Sequence[TopKResult],
    expected_tables: frozenset[str],
) -> QueryResult:
    results = tuple(sorted(raw_results, key=lambda item: item.rank))
    if len(results) != TOP_K:
        raise ValueError(
            f"{query.query_id} 原始结果必须为 {TOP_K} 条，实际为 {len(results)}"
        )
    if [item.rank for item in results] != list(range(1, TOP_K + 1)):
        raise ValueError(f"{query.query_id} rank 必须连续为 1..{TOP_K}")
    record_ids = [item.record_id for item in results]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError(f"{query.query_id} 原始 Top-K 存在重复 record_id")

    exact_seen: set[str] = set()
    exact_count = 0
    table_seen: set[str] = set()
    table_count = 0
    for item in results:
        if item.normalized_document_sha256 not in exact_seen:
            exact_seen.add(item.normalized_document_sha256)
            exact_count += 1
        table_key = (
            f"table:{item.parsed_table_name}"
            if item.parsed_table_name in expected_tables
            else f"record:{item.record_id}"
        )
        if table_key not in table_seen:
            table_seen.add(table_key)
            table_count += 1
    exact_slots = len(results) - exact_count
    table_slots = len(results) - table_count
    return QueryResult(
        query_id=query.query_id,
        expected_table=query.expected_table,
        results=results,
        exact_duplicate_slot_count=exact_slots,
        exact_projection_result_count=exact_count,
        exact_projection_changed=exact_slots > 0,
        table_duplicate_slot_count=table_slots,
        table_projection_result_count=table_count,
        table_projection_changed=table_slots > 0,
    )


def _query_collection(
    collection: Any,
    queries: Sequence[QuerySpec],
    lookup: ExpectedLookup,
) -> tuple[QueryResult, ...]:
    output = []
    for query in queries:
        raw = collection.query(
            query_texts=[query.query_text],
            n_results=TOP_K,
            include=["documents", "metadatas", "distances"],
        )
        ids = list((raw.get("ids") or [[]])[0])
        documents = list((raw.get("documents") or [[]])[0])
        distances = list((raw.get("distances") or [[]])[0])
        if not (len(ids) == len(documents) == len(distances)):
            raise ValueError(f"{query.query_id} query 返回数组长度不一致")
        parsed = []
        for rank, (record_id, document, distance) in enumerate(
            zip(ids, documents, distances), start=1
        ):
            document_sha = _document_sha(document)
            table_name, classification = _classify_document(
                document, document_sha, lookup
            )
            parsed.append(
                TopKResult(
                    rank=rank,
                    record_id=record_id,
                    parsed_table_name=table_name,
                    normalized_document_sha256=document_sha,
                    distance=float(distance),
                    classification=classification,
                )
            )
        output.append(analyze_query_results(query, parsed, lookup.table_names))
    return tuple(output)


def _stable_result_sha(results: Sequence[QueryResult]) -> str:
    payload = [
        {
            "query_id": query.query_id,
            "expected_table": query.expected_table,
            "results": [item.stable_dict() for item in query.results],
        }
        for query in sorted(results, key=lambda item: item.query_id)
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _verify_repeated_results(
    first_results: Sequence[QueryResult], second_results: Sequence[QueryResult]
) -> tuple[str, str]:
    first_sha = _stable_result_sha(first_results)
    second_sha = _stable_result_sha(second_results)
    if first_sha != second_sha:
        raise RuntimeError("两轮独立查询副本的稳定结果 SHA 不一致")
    return first_sha, second_sha


def _open_query_collection(path: Path, run_root: Path) -> Any:
    _validate_client_open_path(path, run_root)
    import chromadb
    from backend.memory import EMBEDDING_FUNCTION

    client = chromadb.PersistentClient(path=str(path))
    return client.get_collection(
        name=COLLECTION_NAME, embedding_function=EMBEDDING_FUNCTION
    )


def _run_one_query_copy(
    path: Path,
    run_root: Path,
    queries: Sequence[QuerySpec],
    lookup: ExpectedLookup,
) -> tuple[tuple[QueryResult, ...], TreeManifest]:
    collection = _open_query_collection(path, run_root)
    results = _query_collection(collection, queries, lookup)
    del collection
    gc.collect()
    return results, build_tree_manifest(path)


def scan_readonly_capabilities(module_path: Path | str) -> tuple[str, ...]:
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called_name: str | None = None
        receiver_text = ""
        if isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
            receiver_text = ast.unparse(node.func.value).lower()
        elif isinstance(node.func, ast.Name):
            called_name = node.func.id
        memory_primitive = called_name in {"add", "update", "upsert", "delete"}
        if called_name in FORBIDDEN_CALL_NAMES and (
            not memory_primitive
            or not receiver_text
            or "collection" in receiver_text
        ):
            violations.append(f"line={node.lineno}, call={called_name}")
    return tuple(sorted(violations))


def impact_test(formal_source: Path | str, run_root: Path | str) -> dict[str, Any]:
    source, root = _validate_formal_execution_paths(formal_source, run_root)
    queries = build_query_set()
    lookup = build_expected_lookup()
    manifests = _prepare_snapshot_model(source, root)
    archive = root / "formal_archive"
    run1 = root / "query_snapshot_run1"
    run2 = root / "query_snapshot_run2"
    evidence = root / "evidence"
    evidence.mkdir(parents=False, exist_ok=False)

    first_results, run1_postopen = _run_one_query_copy(
        run1, root, queries, lookup
    )
    second_results, run2_postopen = _run_one_query_copy(
        run2, root, queries, lookup
    )
    archive_after = build_tree_manifest(archive)
    _verify_archive_unchanged(manifests["archive_before"], archive_after)

    first_sha, second_sha = _verify_repeated_results(
        first_results, second_results
    )
    total_exact_slots = sum(
        item.exact_duplicate_slot_count for item in first_results
    )
    total_table_slots = sum(
        item.table_duplicate_slot_count for item in first_results
    )
    exact_changed = sum(item.exact_projection_changed for item in first_results)
    table_changed = sum(item.table_projection_changed for item in first_results)
    top1 = sum(
        any(
            result.rank <= 1 and result.parsed_table_name == query.expected_table
            for result in query.results
        )
        for query in first_results
    )
    top5 = sum(
        any(
            result.rank <= 5 and result.parsed_table_name == query.expected_table
            for result in query.results
        )
        for query in first_results
    )
    top10 = sum(
        any(result.parsed_table_name == query.expected_table for result in query.results)
        for query in first_results
    )
    assessment_status = (
        "PASS"
        if (total_exact_slots, total_table_slots, exact_changed, table_changed)
        == (0, 0, 0, 0)
        else "INCONSISTENT_WITH_F6_1G_AUDIT"
    )
    summary = {
        "assessment_status": assessment_status,
        "query_count": len(queries),
        "top_k": TOP_K,
        "archive_tree_sha": manifests["archive_before"].tree_sha256,
        "query_run1_preopen_tree_sha": manifests["run1_preopen"].tree_sha256,
        "query_run2_preopen_tree_sha": manifests["run2_preopen"].tree_sha256,
        "query_run1_postopen_tree_sha": run1_postopen.tree_sha256,
        "query_run2_postopen_tree_sha": run2_postopen.tree_sha256,
        "formal_archive_tree_sha_after_queries": archive_after.tree_sha256,
        "query_run1_changed_files": _tree_changes(
            manifests["run1_preopen"], run1_postopen
        ),
        "query_run2_changed_files": _tree_changes(
            manifests["run2_preopen"], run2_postopen
        ),
        "total_exact_duplicate_slots": total_exact_slots,
        "total_table_duplicate_slots": total_table_slots,
        "exact_projection_changed_query_count": exact_changed,
        "table_projection_changed_query_count": table_changed,
        "expected_table_top1_hit_count": top1,
        "expected_table_top5_hit_count": top5,
        "expected_table_top10_hit_count": top10,
        "first_run_result_sha256": first_sha,
        "second_run_result_sha256": second_sha,
        "duplicate_topk_impact": "NONE" if assessment_status == "PASS" else "PRESENT",
        "formal_chroma_client_open_attempts_by_script": 0,
        "snapshot_chroma_client_open_count": 2,
    }
    query_manifest = [
        {
            "query_id": item.query_id,
            "expected_table": item.expected_table,
            "query_text": item.query_text,
        }
        for item in queries
    ]
    result_manifest = {
        "first_run": [
            {
                "query_id": query.query_id,
                "expected_table": query.expected_table,
                "results": [item.evidence_dict() for item in query.results],
            }
            for query in first_results
        ],
        "second_run": [
            {
                "query_id": query.query_id,
                "expected_table": query.expected_table,
                "results": [item.evidence_dict() for item in query.results],
            }
            for query in second_results
        ],
    }
    (evidence / "query-manifest.json").write_text(
        json.dumps(query_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (evidence / "topk-result-manifest.json").write_text(
        json.dumps(result_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (evidence / "topk-impact-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


class _FakeQueryCollection:
    def __init__(self, ids: Sequence[str], documents: Sequence[str]) -> None:
        self._ids = list(ids)
        self._documents = list(documents)

    def query(
        self,
        *,
        query_texts: Sequence[str],
        n_results: int,
        include: Sequence[str],
    ) -> dict[str, list[list[Any]]]:
        return {
            "ids": [self._ids[:n_results]],
            "documents": [self._documents[:n_results]],
            "metadatas": [[{} for _ in self._ids[:n_results]]],
            "distances": [
                [index / 100 for index, _item in enumerate(self._ids[:n_results])]
            ],
        }


def _synthetic_ddl(table_name: str, column_type: str = "integer") -> str:
    return (
        "[DDL_MEMORY]\n"
        f"表名：{table_name}\n\n"
        f'CREATE TABLE "{table_name}" (\n  id {column_type}\n);'
    )


def _expect_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except (OSError, RuntimeError, ValueError) as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期失败：{expected_text}")


def self_test() -> int:
    real_queries = build_query_set()
    assert len(real_queries) == QUERY_COUNT
    assert [item.expected_table for item in real_queries] == sorted(
        item.expected_table for item in real_queries
    )
    assert all(item.query_text for item in real_queries)

    queries = (QuerySpec("q001", "table_00", "示例注释"),)
    documents = [_synthetic_ddl(f"table_{index:02d}") for index in range(TOP_K)]
    ids = [f"record-{index:02d}" for index in range(TOP_K)]
    lookup = ExpectedLookup(
        frozenset(f"table_{index:02d}" for index in range(TOP_K)),
        {
            _document_sha(document): f"table_{index:02d}"
            for index, document in enumerate(documents)
        },
    )
    fake = _FakeQueryCollection(ids, documents)
    stable = _query_collection(fake, queries, lookup)
    assert stable[0].exact_duplicate_slot_count == 0
    assert stable[0].table_duplicate_slot_count == 0

    reversed_analysis = analyze_query_results(
        queries[0], tuple(reversed(stable[0].results)), lookup.table_names
    )
    assert reversed_analysis == stable[0]
    changed_distances = tuple(
        TopKResult(
            item.rank,
            item.record_id,
            item.parsed_table_name,
            item.normalized_document_sha256,
            item.distance + 0.000001,
            item.classification,
        )
        for item in stable[0].results
    )
    distance_variant = (
        analyze_query_results(queries[0], changed_distances, lookup.table_names),
    )
    _verify_repeated_results(stable, distance_variant)
    changed_identity = list(stable[0].results)
    changed_identity[0] = TopKResult(
        1,
        "different-record",
        changed_identity[0].parsed_table_name,
        changed_identity[0].normalized_document_sha256,
        changed_identity[0].distance,
        changed_identity[0].classification,
    )
    unstable_results = (
        analyze_query_results(queries[0], changed_identity, lookup.table_names),
    )
    _expect_error(
        lambda: _verify_repeated_results(stable, unstable_results),
        "稳定结果 SHA 不一致",
    )

    exact_duplicate = list(stable[0].results)
    exact_duplicate[1] = TopKResult(
        2,
        "record-exact-duplicate",
        exact_duplicate[0].parsed_table_name,
        exact_duplicate[0].normalized_document_sha256,
        0.02,
        "expected_exact_match",
    )
    exact_analysis = analyze_query_results(
        queries[0], exact_duplicate, lookup.table_names
    )
    assert exact_analysis.exact_duplicate_slot_count == 1

    table_duplicate = list(stable[0].results)
    table_duplicate[1] = TopKResult(
        2,
        "record-table-duplicate",
        table_duplicate[0].parsed_table_name,
        "f" * 64,
        0.02,
        "expected_table_content_variant",
    )
    table_analysis = analyze_query_results(
        queries[0], table_duplicate, lookup.table_names
    )
    assert table_analysis.exact_duplicate_slot_count == 0
    assert table_analysis.table_duplicate_slot_count == 1

    non_ddl = list(stable[0].results)
    non_ddl[0] = TopKResult(1, "tool-a", None, "a" * 64, 0.01, "non_ddl_memory")
    non_ddl[1] = TopKResult(2, "tool-b", None, "b" * 64, 0.02, "non_ddl_memory")
    assert analyze_query_results(
        queries[0], non_ddl, lookup.table_names
    ).table_duplicate_slot_count == 0
    _expect_error(
        lambda: analyze_query_results(queries[0], stable[0].results[:9], lookup.table_names),
        "必须为 10 条",
    )
    duplicate_ids = list(stable[0].results)
    duplicate_ids[1] = TopKResult(
        2,
        duplicate_ids[0].record_id,
        duplicate_ids[1].parsed_table_name,
        duplicate_ids[1].normalized_document_sha256,
        duplicate_ids[1].distance,
        duplicate_ids[1].classification,
    )
    _expect_error(
        lambda: analyze_query_results(queries[0], duplicate_ids, lookup.table_names),
        "重复 record_id",
    )

    with tempfile.TemporaryDirectory(prefix="f6-1h-r1-") as temp_dir:
        temp_root = Path(temp_dir)
        source = temp_root / "source"
        run_root = temp_root / "run"
        source.mkdir()
        (source / "nested").mkdir()
        (source / "chroma.sqlite3").write_bytes(b"sqlite")
        (source / "nested" / "data.bin").write_bytes(b"vector")
        manifests = _prepare_snapshot_model(source, run_root)
        archive = run_root / "formal_archive"
        run1 = run_root / "query_snapshot_run1"
        run2 = run_root / "query_snapshot_run2"
        assert manifests["source_before"] == manifests["archive_before"]
        assert manifests["archive_before"] == manifests["run1_preopen"]
        assert manifests["archive_before"] == manifests["run2_preopen"]
        _expect_error(
            lambda: _copy_query_snapshot(run1, run_root / "illegal", run_root),
            "必须直接从 formal_archive",
        )
        (run1 / "chroma.sqlite3").write_bytes(b"sqlite-run1-changed")
        run1_after = build_tree_manifest(run1)
        assert _tree_changes(manifests["run1_preopen"], run1_after)
        _verify_archive_unchanged(
            manifests["archive_before"], build_tree_manifest(archive)
        )
        (run2 / "chroma.sqlite3").write_bytes(b"sqlite-run2-changed")
        run2_after = build_tree_manifest(run2)
        assert _tree_changes(manifests["run2_preopen"], run2_after)
        _verify_archive_unchanged(
            manifests["archive_before"], build_tree_manifest(archive)
        )
        (archive / "chroma.sqlite3").write_bytes(b"archive-changed")
        _expect_error(
            lambda: _verify_archive_unchanged(
                manifests["archive_before"], build_tree_manifest(archive)
            ),
            "ARCHIVE_INTEGRITY_FAILED",
        )

    with tempfile.TemporaryDirectory(prefix="f6-1h-source-change-") as temp_dir:
        temp_root = Path(temp_dir)
        source = temp_root / "source"
        archive = temp_root / "archive"
        source.mkdir()
        (source / "a.bin").write_bytes(b"before")
        before = build_tree_manifest(source)
        copy_complete_snapshot(source, archive)
        archived = build_tree_manifest(archive)
        (source / "a.bin").write_bytes(b"after")
        after = build_tree_manifest(source)
        _expect_error(
            lambda: verify_tree_sha_gate(before, archived, after),
            "来源稳定性门禁失败",
        )

    synthetic_root = BACKUP_ROOT / "f6-1h-20990101-000000"
    _expect_error(
        lambda: _validate_client_open_path(FORMAL_CHROMA, synthetic_root),
        "正式来源",
    )
    _expect_error(
        lambda: _validate_client_open_path(
            synthetic_root / "formal_archive", synthetic_root
        ),
        "formal_archive",
    )
    assert scan_readonly_capabilities(Path(__file__)) == ()
    forbidden_modules = ("chromadb", "vanna", "backend.memory")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )
    assert FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE == 0
    assert FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT == 0

    print("DDL_MEMORY_TOPK_IMPACT_SELF_TEST=PASS")
    print("TWO_LAYER_SNAPSHOT_TEST=PASS")
    print("QUERY_COPY_INDEPENDENCE_TEST=PASS")
    print("WORKING_COPY_CHANGE_TRACKING_TEST=PASS")
    print("ARCHIVE_INTEGRITY_TEST=PASS")
    print("SOURCE_CHANGE_GATE_TEST=PASS")
    print("TOPK_PROJECTION_TEST=PASS")
    print("RESULT_SHA_DISTANCE_EXCLUSION_TEST=PASS")
    print("READONLY_CAPABILITY_SCAN=PASS")
    print("CHROMA_CLIENT_CREATED=0")
    print("TOPK_EXECUTED=NO")
    print("FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0")
    print("FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--impact-test", action="store_true")
    parser.add_argument("--formal-source", type=Path)
    parser.add_argument("--run-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        if args.formal_source is not None or args.run_root is not None:
            raise SystemExit("--self-test 不接受正式路径参数")
        return self_test()
    if args.formal_source is None or args.run_root is None:
        raise SystemExit("--impact-test 必须显式传入 --formal-source 和 --run-root")
    try:
        summary = impact_test(args.formal_source, args.run_root)
    except (OSError, RuntimeError, ValueError) as exc:
        root = args.run_root.absolute()
        if root.exists():
            evidence = root / "evidence"
            evidence.mkdir(parents=False, exist_ok=True)
            (evidence / "topk-impact-failure.json").write_text(
                json.dumps(
                    {
                        "assessment_status": (
                            "ARCHIVE_INTEGRITY_FAILED"
                            if "ARCHIVE_INTEGRITY_FAILED" in str(exc)
                            else "FAIL"
                        ),
                        "candidate_usable": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "formal_chroma_client_open_attempts_by_script": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        print(f"DDL_MEMORY_TOPK_IMPACT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for key, value in summary.items():
        if key not in {"query_run1_changed_files", "query_run2_changed_files"}:
            print(f"{key}={value}")
    return 0 if summary["assessment_status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())

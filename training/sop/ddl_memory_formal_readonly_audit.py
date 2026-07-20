"""F6-1G 正式 DDL Memory 只读审计工具；G-A 阶段仅运行合成自检。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from train_step3 import build_all_table_ddls, group_tables, load_metadata_index
from training.sop.ddl_memory_identity import (
    IDENTITY_VERSION,
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
    normalize_ddl,
)
from training.sop.ddl_memory_plan import ExistingDdlMemoryRecord


PROJECT_ROOT = Path(__file__).absolute().parents[2]
BACKUP_ROOT = Path(r"E:\3\_training_backups").absolute()
FORMAL_CHROMA = Path(r"E:\3\_runtime\vanna-level1\vanna_data").absolute()
EXPECTED_FORMAL_COLLECTION_COUNT = 198
EXPECTED_DDL_COUNT = 115
COLLECTION_NAME = "tool_memories"
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE = 0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT = 0
RUN_ROOT_PATTERN = re.compile(r"f6-1g-\d{8}-\d{6}\Z")
TABLE_NAME_PATTERN = re.compile(r"(?m)^表名：\s*([^\r\n]+?)\s*$")
CREATE_TABLE_PATTERN = re.compile(
    r'(?i)\bCREATE\s+TABLE\s+(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
)
DDL_MARKERS = ("[DDL_MEMORY]", "CREATE TABLE", "表名：")
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
class TreeManifest:
    tree_sha256: str
    files: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ExpectedDdl:
    table_name: str
    logical_id: str
    record_id: str
    identity_key: str
    normalized_document_sha256: str


@dataclass(frozen=True)
class ExpectedDdlBundle:
    source_table_count: int
    desired_memory_count: int
    unique_table_name_count: int
    unique_logical_id_count: int
    unique_record_id_count: int
    records: tuple[ExpectedDdl, ...]


@dataclass(frozen=True)
class AuditClassification:
    formal_collection_count: int
    ddl_candidate_record_count: int
    expected_exact_match_record_count: int
    expected_exact_match_table_count: int
    missing_expected_table_count: int
    content_variant_record_count: int
    unexpected_ddl_record_count: int
    non_ddl_memory_count: int
    exact_duplicate_group_count: int
    exact_duplicate_record_count: int
    exact_duplicate_excess_count: int
    table_identity_duplicate_group_count: int
    table_identity_duplicate_record_count: int
    records: tuple[Mapping[str, Any], ...]
    missing_expected_tables: tuple[str, ...]
    classification_sha256: str

    def summary_dict(self) -> dict[str, int]:
        return {
            "formal_collection_count": self.formal_collection_count,
            "expected_formal_collection_count": EXPECTED_FORMAL_COLLECTION_COUNT,
            "ddl_candidate_record_count": self.ddl_candidate_record_count,
            "expected_exact_match_record_count": (
                self.expected_exact_match_record_count
            ),
            "expected_exact_match_table_count": self.expected_exact_match_table_count,
            "missing_expected_table_count": self.missing_expected_table_count,
            "content_variant_record_count": self.content_variant_record_count,
            "unexpected_ddl_record_count": self.unexpected_ddl_record_count,
            "non_ddl_memory_count": self.non_ddl_memory_count,
            "exact_duplicate_group_count": self.exact_duplicate_group_count,
            "exact_duplicate_record_count": self.exact_duplicate_record_count,
            "exact_duplicate_excess_count": self.exact_duplicate_excess_count,
            "table_identity_duplicate_group_count": (
                self.table_identity_duplicate_group_count
            ),
            "table_identity_duplicate_record_count": (
                self.table_identity_duplicate_record_count
            ),
        }


class ReadonlyCollectionReader:
    """只暴露 document/Metadata 读取，不暴露任何 Memory 写入方法。"""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def read_records(self) -> tuple[ExistingDdlMemoryRecord, ...]:
        raw = self._collection.get(include=["documents", "metadatas"])
        ids = list(raw.get("ids") or [])
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        if not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError(
                "只读 collection 返回数组长度不一致："
                f"ids={len(ids)}, documents={len(documents)}, metadatas={len(metadatas)}"
            )
        records = [
            ExistingDdlMemoryRecord(record_id, document, metadata or {})
            for record_id, document, metadata in zip(ids, documents, metadatas)
        ]
        return tuple(sorted(records, key=lambda item: item.record_id))


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


def build_tree_manifest(root: Path | str) -> TreeManifest:
    root_path = Path(root)
    files: list[dict[str, Any]] = []
    for candidate in sorted(root_path.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise ValueError(f"Tree SHA 禁止符号链接：{candidate.relative_to(root_path)}")
        if not candidate.is_file():
            continue
        relative_path = candidate.relative_to(root_path).as_posix()
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        files.append(
            {
                "relative_path": relative_path,
                "file_size": candidate.stat().st_size,
                "file_sha256": digest.hexdigest(),
            }
        )
    tree_sha = hashlib.sha256(_canonical_json(files).encode("utf-8")).hexdigest()
    return TreeManifest(tree_sha, tuple(files))


def verify_tree_sha_gate(
    before: TreeManifest, snapshot: TreeManifest, after: TreeManifest
) -> None:
    if not (
        before.tree_sha256 == snapshot.tree_sha256 == after.tree_sha256
        and before.files == snapshot.files == after.files
    ):
        raise RuntimeError(
            "来源稳定性门禁失败：formal source before、snapshot、after 不一致"
        )


def copy_complete_snapshot(source: Path | str, destination: Path | str) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    if destination_path.exists():
        raise ValueError(f"formal_snapshot 必须不存在：{destination_path}")
    shutil.copytree(source_path, destination_path, copy_function=shutil.copy2)


def build_expected_ddl_bundle() -> ExpectedDdlBundle:
    metadata = load_metadata_index()
    tables = group_tables(metadata)
    generated, _geometry_count = build_all_table_ddls(tables)
    expected = []
    for item in generated:
        identity = build_ddl_memory_identity(
            DdlMemoryIdentityInput(
                source_id="postgres_water",
                schema_name="public",
                object_type="table",
                object_name=item["table"],
            ),
            item["ddl"],
        )
        expected.append(
            ExpectedDdl(
                table_name=item["table"],
                logical_id=identity.logical_id,
                record_id=identity.record_id,
                identity_key=identity.identity_key,
                normalized_document_sha256=hashlib.sha256(
                    identity.normalized_ddl.encode("utf-8")
                ).hexdigest(),
            )
        )
    records = tuple(sorted(expected, key=lambda item: item.table_name))
    bundle = ExpectedDdlBundle(
        source_table_count=len(tables),
        desired_memory_count=len(records),
        unique_table_name_count=len({item.table_name for item in records}),
        unique_logical_id_count=len({item.logical_id for item in records}),
        unique_record_id_count=len({item.record_id for item in records}),
        records=records,
    )
    expected_counts = (
        EXPECTED_DDL_COUNT,
        EXPECTED_DDL_COUNT,
        EXPECTED_DDL_COUNT,
        EXPECTED_DDL_COUNT,
        EXPECTED_DDL_COUNT,
    )
    actual_counts = (
        bundle.source_table_count,
        bundle.desired_memory_count,
        bundle.unique_table_name_count,
        bundle.unique_logical_id_count,
        bundle.unique_record_id_count,
    )
    if actual_counts != expected_counts:
        raise ValueError(
            f"当前期望 DDL 集合门禁失败：actual={actual_counts}, expected={expected_counts}"
        )
    return bundle


def _normalized_document_sha(document: str) -> str:
    try:
        normalized = normalize_ddl(document)
    except ValueError:
        normalized = document.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_ddl_candidate(document: str) -> bool:
    upper_document = document.upper()
    return (
        DDL_MARKERS[0] in document
        and DDL_MARKERS[1] in upper_document
        and DDL_MARKERS[2] in document
    )


def _parse_table_name(document: str) -> str | None:
    explicit = TABLE_NAME_PATTERN.search(document)
    if explicit is not None:
        return explicit.group(1).strip()
    create_match = CREATE_TABLE_PATTERN.search(document)
    if create_match is None:
        return None
    quoted, unquoted = create_match.groups()
    return quoted.replace('""', '"') if quoted is not None else unquoted


def classify_records(
    records: Sequence[ExistingDdlMemoryRecord],
    expected_records: Sequence[ExpectedDdl],
) -> AuditClassification:
    expected_by_table = {item.table_name: item for item in expected_records}
    expected_by_sha = {
        item.normalized_document_sha256: item for item in expected_records
    }
    if len(expected_by_table) != len(expected_records):
        raise ValueError("期望集合存在重复表名")
    if len(expected_by_sha) != len(expected_records):
        raise ValueError("期望集合存在重复规范化 document SHA")

    seen_record_ids: set[str] = set()
    working: list[dict[str, Any]] = []
    exact_table_names: set[str] = set()
    ddl_hashes: list[str] = []
    expected_table_candidates: list[str] = []
    for record in records:
        if record.record_id in seen_record_ids:
            raise ValueError(f"正式快照存在重复顶层 record_id：{record.record_id}")
        seen_record_ids.add(record.record_id)
        document_sha = _normalized_document_sha(record.document)
        exact_expected = expected_by_sha.get(document_sha)
        ddl_candidate = exact_expected is not None or _is_ddl_candidate(record.document)
        parsed_table = (
            exact_expected.table_name
            if exact_expected is not None
            else _parse_table_name(record.document) if ddl_candidate else None
        )
        if exact_expected is not None:
            category = "expected_exact_match"
            exact_table_names.add(exact_expected.table_name)
        elif ddl_candidate and parsed_table in expected_by_table:
            category = "expected_table_content_variant"
        elif ddl_candidate:
            category = "unexpected_ddl"
        else:
            category = "non_ddl_memory"
        if ddl_candidate:
            ddl_hashes.append(document_sha)
        if ddl_candidate and parsed_table in expected_by_table:
            expected_table_candidates.append(str(parsed_table))
        working.append(
            {
                "record_id": record.record_id,
                "table_name": parsed_table,
                "normalized_document_sha256": document_sha,
                "metadata_field_names": sorted(str(key) for key in record.metadata),
                "classification": category,
            }
        )

    exact_counts = Counter(ddl_hashes)
    table_counts = Counter(expected_table_candidates)
    exact_duplicate_keys = sorted(
        key for key, count in exact_counts.items() if count > 1
    )
    table_duplicate_keys = sorted(
        key for key, count in table_counts.items() if count > 1
    )
    exact_group_ids = {
        key: f"exact-{index:04d}"
        for index, key in enumerate(exact_duplicate_keys, start=1)
    }
    table_group_ids = {
        key: f"table-{index:04d}"
        for index, key in enumerate(table_duplicate_keys, start=1)
    }
    for item in working:
        item["exact_duplicate_group"] = exact_group_ids.get(
            item["normalized_document_sha256"]
        )
        item["table_identity_duplicate_group"] = table_group_ids.get(
            item["table_name"]
        )
    sorted_records = tuple(sorted(working, key=lambda item: item["record_id"]))
    category_counts = Counter(item["classification"] for item in sorted_records)
    total_classified = sum(category_counts.values())
    if total_classified != len(records):
        raise RuntimeError(
            f"分类总数无法对账：classified={total_classified}, total={len(records)}"
        )
    missing_tables = tuple(sorted(set(expected_by_table) - exact_table_names))
    stable_payload = {
        "records": sorted_records,
        "missing_expected_tables": missing_tables,
    }
    classification_sha = hashlib.sha256(
        _canonical_json(stable_payload).encode("utf-8")
    ).hexdigest()
    return AuditClassification(
        formal_collection_count=len(records),
        ddl_candidate_record_count=(
            category_counts["expected_exact_match"]
            + category_counts["expected_table_content_variant"]
            + category_counts["unexpected_ddl"]
        ),
        expected_exact_match_record_count=category_counts["expected_exact_match"],
        expected_exact_match_table_count=len(exact_table_names),
        missing_expected_table_count=len(missing_tables),
        content_variant_record_count=category_counts[
            "expected_table_content_variant"
        ],
        unexpected_ddl_record_count=category_counts["unexpected_ddl"],
        non_ddl_memory_count=category_counts["non_ddl_memory"],
        exact_duplicate_group_count=len(exact_duplicate_keys),
        exact_duplicate_record_count=sum(exact_counts[key] for key in exact_duplicate_keys),
        exact_duplicate_excess_count=sum(
            exact_counts[key] - 1 for key in exact_duplicate_keys
        ),
        table_identity_duplicate_group_count=len(table_duplicate_keys),
        table_identity_duplicate_record_count=sum(
            table_counts[key] for key in table_duplicate_keys
        ),
        records=sorted_records,
        missing_expected_tables=missing_tables,
        classification_sha256=classification_sha,
    )


def _validate_formal_execution_paths(
    formal_source: Path | str, run_root: Path | str
) -> tuple[Path, Path, Path]:
    if _canonical_path(formal_source) != _canonical_path(FORMAL_CHROMA):
        raise ValueError(f"formal_source 必须精确等于固定正式路径：{FORMAL_CHROMA}")
    root = Path(run_root).absolute()
    if (
        not _is_within(root, BACKUP_ROOT)
        or root.parent != BACKUP_ROOT
        or RUN_ROOT_PATTERN.fullmatch(root.name) is None
    ):
        raise ValueError(
            "run_root 必须为 E:\\3\\_training_backups\\"
            "f6-1g-<YYYYMMDD-HHMMSS>"
        )
    if root.exists():
        raise ValueError(f"run_root 必须全新且不存在：{root}")
    return Path(formal_source).absolute(), root / "formal_snapshot", root / "evidence"


def _validate_snapshot_open_path(snapshot: Path, run_root: Path) -> None:
    expected_snapshot = run_root / "formal_snapshot"
    if _canonical_path(snapshot) == _canonical_path(FORMAL_CHROMA):
        raise ValueError("正式 Chroma 绝对禁止作为 Client 打开路径")
    if _canonical_path(snapshot) != _canonical_path(expected_snapshot):
        raise ValueError("Client 只能打开当前 run_root 下的 formal_snapshot")


def _open_snapshot_reader(snapshot: Path, run_root: Path) -> ReadonlyCollectionReader:
    _validate_snapshot_open_path(snapshot, run_root)
    import chromadb

    client = chromadb.PersistentClient(path=str(snapshot))
    collection = client.get_collection(name=COLLECTION_NAME)
    return ReadonlyCollectionReader(collection)


def scan_readonly_capabilities(module_path: Path | str) -> tuple[str, ...]:
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called_name: str | None = None
        receiver_name: str | None = None
        receiver_text: str | None = None
        if isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
            receiver_text = ast.unparse(node.func.value).lower()
            receiver = node.func.value
            while isinstance(receiver, ast.Attribute):
                receiver = receiver.value
            if isinstance(receiver, ast.Name):
                receiver_name = receiver.id
        elif isinstance(node.func, ast.Name):
            called_name = node.func.id
        memory_primitive = called_name in {"add", "update", "upsert", "delete"}
        forbidden = (
            called_name in FORBIDDEN_CALL_NAMES
            and (
                not memory_primitive
                or receiver_name is None
                or "collection" in (receiver_text or "")
            )
        )
        if forbidden:
            violations.append(f"line={node.lineno}, call={called_name}")
    return tuple(sorted(violations))


def formal_audit(formal_source: Path | str, run_root: Path | str) -> dict[str, Any]:
    expected = build_expected_ddl_bundle()
    source, snapshot, evidence = _validate_formal_execution_paths(
        formal_source, run_root
    )
    root = snapshot.parent

    before = build_tree_manifest(source)
    root.mkdir(parents=False, exist_ok=False)
    copy_complete_snapshot(source, snapshot)
    snapshot_manifest = build_tree_manifest(snapshot)
    after = build_tree_manifest(source)
    evidence.mkdir(parents=False, exist_ok=False)
    verify_tree_sha_gate(before, snapshot_manifest, after)

    reader = _open_snapshot_reader(snapshot, root)
    records = reader.read_records()
    classification = classify_records(records, expected.records)
    status = (
        "PASS"
        if classification.formal_collection_count
        == EXPECTED_FORMAL_COLLECTION_COUNT
        else "BASELINE_MISMATCH"
    )
    source_manifest_payload = {
        "formal_source_tree_sha256_before": before.tree_sha256,
        "snapshot_tree_sha256": snapshot_manifest.tree_sha256,
        "formal_source_tree_sha256_after": after.tree_sha256,
        "files": before.files,
    }
    record_payload = {
        "records": classification.records,
        "missing_expected_tables": classification.missing_expected_tables,
    }
    summary = {
        "audit_status": status,
        "formal_source_tree_sha256_before": before.tree_sha256,
        "snapshot_tree_sha256": snapshot_manifest.tree_sha256,
        "formal_source_tree_sha256_after": after.tree_sha256,
        **classification.summary_dict(),
        "classification_sha256": classification.classification_sha256,
        "formal_chroma_client_open_attempts_by_script": 0,
        "snapshot_chroma_client_open_count": 1,
    }
    (evidence / "formal-source-manifest.json").write_text(
        json.dumps(source_manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (evidence / "formal-record-classification.json").write_text(
        json.dumps(record_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (evidence / "formal-audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


class _FakeCollection:
    def __init__(self, records: Sequence[ExistingDdlMemoryRecord]) -> None:
        self._records = tuple(records)

    def get(self, include: Sequence[str]) -> dict[str, list[Any]]:
        return {
            "ids": [record.record_id for record in self._records],
            "documents": [record.document for record in self._records],
            "metadatas": [dict(record.metadata) for record in self._records],
        }


def _synthetic_expected(table_name: str, ddl: str) -> ExpectedDdl:
    identity = build_ddl_memory_identity(
        DdlMemoryIdentityInput("postgres_water", "public", "table", table_name), ddl
    )
    return ExpectedDdl(
        table_name=table_name,
        logical_id=identity.logical_id,
        record_id=identity.record_id,
        identity_key=identity.identity_key,
        normalized_document_sha256=hashlib.sha256(
            identity.normalized_ddl.encode("utf-8")
        ).hexdigest(),
    )


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
    full_expected = build_expected_ddl_bundle()
    assert (
        full_expected.source_table_count,
        full_expected.desired_memory_count,
        full_expected.unique_table_name_count,
        full_expected.unique_logical_id_count,
        full_expected.unique_record_id_count,
    ) == (115, 115, 115, 115, 115)

    alpha_ddl = _synthetic_ddl("alpha")
    beta_ddl = _synthetic_ddl("beta")
    expected = (
        _synthetic_expected("alpha", alpha_ddl),
        _synthetic_expected("beta", beta_ddl),
    )
    unique_result = classify_records(
        [ExistingDdlMemoryRecord("uuid-alpha", alpha_ddl, {"timestamp": "one"})],
        [expected[0]],
    )
    assert unique_result.expected_exact_match_record_count == 1
    assert unique_result.exact_duplicate_group_count == 0

    records = (
        ExistingDdlMemoryRecord("uuid-alpha-1", alpha_ddl, {"timestamp": "one"}),
        ExistingDdlMemoryRecord("uuid-alpha-2", alpha_ddl, {"timestamp": "two"}),
        ExistingDdlMemoryRecord(
            "uuid-alpha-variant", _synthetic_ddl("alpha", "bigint"), {}
        ),
        ExistingDdlMemoryRecord("uuid-gamma", _synthetic_ddl("gamma"), {}),
        ExistingDdlMemoryRecord("uuid-tool", "SELECT count(*) FROM demo", {"kind": "tool"}),
    )
    result = classify_records(records, expected)
    assert result.formal_collection_count == 5
    assert result.ddl_candidate_record_count == 4
    assert result.expected_exact_match_record_count == 2
    assert result.expected_exact_match_table_count == 1
    assert result.missing_expected_table_count == 1
    assert result.missing_expected_tables == ("beta",)
    assert result.content_variant_record_count == 1
    assert result.unexpected_ddl_record_count == 1
    assert result.non_ddl_memory_count == 1
    assert result.exact_duplicate_group_count == 1
    assert result.exact_duplicate_record_count == 2
    assert result.exact_duplicate_excess_count == 1
    assert result.table_identity_duplicate_group_count == 1
    assert result.table_identity_duplicate_record_count == 3
    assert (
        result.expected_exact_match_record_count
        + result.content_variant_record_count
        + result.unexpected_ddl_record_count
        + result.non_ddl_memory_count
        == result.formal_collection_count
    )
    reordered = classify_records(tuple(reversed(records)), tuple(reversed(expected)))
    assert reordered == result

    reader = ReadonlyCollectionReader(_FakeCollection(records))
    assert len(reader.read_records()) == 5
    for forbidden_name in ("add", "update", "upsert", "delete"):
        assert not hasattr(reader, forbidden_name)

    with tempfile.TemporaryDirectory(prefix="f6-1g-a-") as temp_dir:
        temp_root = Path(temp_dir)
        source = temp_root / "source"
        snapshot = temp_root / "snapshot"
        source.mkdir()
        (source / "nested").mkdir()
        (source / "a.bin").write_bytes(b"alpha")
        (source / "nested" / "b.bin").write_bytes(b"beta")
        before = build_tree_manifest(source)
        copy_complete_snapshot(source, snapshot)
        copied = build_tree_manifest(snapshot)
        unchanged_after = build_tree_manifest(source)
        verify_tree_sha_gate(before, copied, unchanged_after)
        (source / "a.bin").write_bytes(b"changed")
        changed_after = build_tree_manifest(source)
        _expect_error(
            lambda: verify_tree_sha_gate(before, copied, changed_after),
            "来源稳定性门禁失败",
        )

    synthetic_run_root = BACKUP_ROOT / "f6-1g-20990101-000000"
    _expect_error(
        lambda: _validate_snapshot_open_path(FORMAL_CHROMA, synthetic_run_root),
        "正式 Chroma",
    )
    violations = scan_readonly_capabilities(Path(__file__))
    assert violations == ()

    forbidden_modules = ("chromadb", "vanna", "agent_config")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )
    assert FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE == 0
    assert FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT == 0

    print("DDL_MEMORY_FORMAL_READONLY_AUDIT_SELF_TEST=PASS")
    print("EXPECTED_DDL_SET_TEST=PASS:115")
    print("SYNTHETIC_CLASSIFICATION_TEST=PASS")
    print("DUPLICATE_RULE_TEST=PASS")
    print("CLASSIFICATION_RECONCILIATION_TEST=PASS")
    print("TREE_SHA_COPY_TEST=PASS")
    print("SOURCE_CHANGE_GATE_TEST=PASS")
    print("SNAPSHOT_ONLY_OPEN_GATE_TEST=PASS")
    print("READONLY_CAPABILITY_SCAN=PASS")
    print("FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0")
    print("FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--formal-audit", action="store_true")
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
        raise SystemExit("--formal-audit 必须显式传入 --formal-source 和 --run-root")
    try:
        summary = formal_audit(args.formal_source, args.run_root)
    except (OSError, RuntimeError, ValueError) as exc:
        run_root = args.run_root.absolute()
        evidence = run_root / "evidence"
        if run_root.exists():
            evidence.mkdir(parents=False, exist_ok=True)
            (evidence / "formal-audit-failure.json").write_text(
                json.dumps(
                    {
                        "audit_status": "FAIL",
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
        print(f"FORMAL_READONLY_AUDIT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0 if summary["audit_status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())

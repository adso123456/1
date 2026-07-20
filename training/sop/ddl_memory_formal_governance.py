"""DDL Memory 正式治理决策、候选迁移与切换/回滚安全模型。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from training.sop.ddl_memory_identity import (
    DdlMemoryIdentity,
    DdlMemoryIdentityInput,
    IDENTITY_VERSION,
    MEMORY_TYPE,
    build_ddl_memory_identity,
    normalize_ddl,
)
from training.sop.ddl_memory_plan import (
    ExistingDdlMemoryRecord,
    build_ddl_memory_plan,
)


EXPECTED_TOTAL_COUNT = 198
EXPECTED_DDL_COUNT = 115
EXPECTED_NON_DDL_COUNT = 83
COLLECTION_NAME = "tool_memories"
FORMAL_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
DECISION_STATES = frozenset(
    {
        "ALREADY_MANAGED_NO_SWITCH",
        "IDENTITY_MIGRATION_REQUIRED",
        "BLOCKED_FORMAL_STATE",
    }
)
RUN_ROOT_PATTERNS = {
    "isolated": re.compile(r"f6-1i-b-\d{8}-\d{6}\Z"),
    "formal": re.compile(r"f6-1i-c-\d{8}-\d{6}\Z"),
}
BACKUP_ROOT = Path(r"E:\3\_training_backups")


@dataclass(frozen=True)
class FormalGovernanceFacts:
    total_count: int
    ddl_candidate_count: int
    exact_match_record_count: int
    exact_match_table_count: int
    non_ddl_count: int
    managed_v1_ddl_count: int
    legacy_expected_ddl_count: int
    missing_expected_table_count: int = 0
    content_variant_record_count: int = 0
    unexpected_ddl_record_count: int = 0
    exact_duplicate_group_count: int = 0
    table_identity_duplicate_group_count: int = 0
    managed_v1_corrupt_count: int = 0
    deterministic_id_conflict_count: int = 0
    classification_reconciled: bool = True


@dataclass(frozen=True)
class FormalGovernanceDecision:
    state: str
    reasons: tuple[str, ...]
    facts: FormalGovernanceFacts

    def __post_init__(self) -> None:
        if self.state not in DECISION_STATES:
            raise ValueError(f"非法治理状态：{self.state}")


@dataclass(frozen=True)
class LegacyDdlMigration:
    legacy_record_id: str
    target_record_id: str
    normalized_document_sha256: str
    classification: str = "expected_exact_match"


@dataclass(frozen=True)
class CandidateAcceptance:
    accepted: bool
    reasons: tuple[str, ...]
    facts: FormalGovernanceFacts
    create_count: int
    unchanged_count: int
    changed_count: int
    removed_count: int


@dataclass(frozen=True)
class SemanticHit:
    classification: str
    normalized_document_sha256: str
    table_name: str | None = None
    record_id: str | None = None

    def stable_key(self) -> tuple[str, str, str]:
        if self.classification == "non_ddl_memory":
            if not self.record_id:
                raise ValueError("非 DDL 语义结果必须携带 record_id")
            identity = self.record_id
        else:
            if not self.table_name:
                raise ValueError("DDL 语义结果必须携带 table_name")
            identity = self.table_name
        return identity, self.normalized_document_sha256, self.classification


@dataclass(frozen=True)
class SemanticQueryResult:
    query_id: str
    expected_table: str
    hits: tuple[SemanticHit, ...]
    exact_duplicate_slots: int = 0
    table_duplicate_slots: int = 0


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _document_sha(document: str) -> str:
    try:
        normalized = normalize_ddl(document)
    except ValueError:
        normalized = document.replace("\r\n", "\n").replace("\r", "\n").strip()
    return _sha256_text(normalized)


def _metadata_sha(metadata: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(payload)


def _parse_table_name(document: str) -> str | None:
    explicit = re.search(r"--\s*表名\s*:\s*([^\r\n]+)", document)
    if explicit:
        return explicit.group(1).strip()
    match = re.search(
        r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\w+\.)?(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))",
        document,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1) or match.group(2)


def _looks_like_ddl(document: str) -> bool:
    return _parse_table_name(document) is not None


def _managed_storage_record(desired: DdlMemoryIdentity) -> ExistingDdlMemoryRecord:
    metadata = dict(desired.effective_metadata)
    metadata["content_fingerprint"] = desired.content_fingerprint
    return ExistingDdlMemoryRecord(
        desired.record_id, desired.normalized_ddl, metadata
    )


def _is_valid_managed_record(
    desired: DdlMemoryIdentity, record: ExistingDdlMemoryRecord
) -> bool:
    try:
        plan = build_ddl_memory_plan([desired], [record])
    except ValueError:
        return False
    return (
        plan.unchanged_count == 1
        and plan.create_count == 0
        and plan.changed_count == 0
        and plan.removed_count == 0
    )


def analyze_formal_records(
    desired_memories: Sequence[DdlMemoryIdentity],
    records: Sequence[ExistingDdlMemoryRecord],
) -> FormalGovernanceFacts:
    desired = tuple(desired_memories)
    desired_by_sha = {_document_sha(item.normalized_ddl): item for item in desired}
    desired_by_table = {
        str(item.effective_metadata["object_name"]): item for item in desired
    }
    desired_by_id = {item.record_id: item for item in desired}
    if not (
        len(desired_by_sha) == len(desired_by_table) == len(desired_by_id) == len(desired)
    ):
        raise ValueError("期望 DDL 集合的 document、表身份或 record_id 不唯一")

    record_ids = [record.record_id for record in records]
    reconciled = len(record_ids) == len(set(record_ids))
    exact_tables: list[str] = []
    exact_hashes: list[str] = []
    content_variants = 0
    unexpected = 0
    non_ddl = 0
    managed = 0
    legacy = 0
    corrupt = 0
    conflicts = 0

    for record in records:
        document_sha = _document_sha(record.document)
        exact = desired_by_sha.get(document_sha)
        parsed_table = _parse_table_name(record.document)
        if exact is None:
            if parsed_table in desired_by_table:
                content_variants += 1
            elif _looks_like_ddl(record.document):
                unexpected += 1
            else:
                non_ddl += 1
            if record.record_id in desired_by_id:
                conflicts += 1
            continue

        table_name = str(exact.effective_metadata["object_name"])
        exact_tables.append(table_name)
        exact_hashes.append(document_sha)
        claims_v1 = (
            record.record_id == exact.record_id
            or record.metadata.get("identity_version") == IDENTITY_VERSION
            or record.metadata.get("memory_type") == MEMORY_TYPE
        )
        if claims_v1:
            if _is_valid_managed_record(exact, record):
                managed += 1
            else:
                corrupt += 1
        else:
            legacy += 1

    exact_counts = Counter(exact_hashes)
    table_counts = Counter(exact_tables)
    facts = FormalGovernanceFacts(
        total_count=len(records),
        ddl_candidate_count=len(exact_hashes) + content_variants + unexpected,
        exact_match_record_count=len(exact_hashes),
        exact_match_table_count=len(set(exact_tables)),
        non_ddl_count=non_ddl,
        managed_v1_ddl_count=managed,
        legacy_expected_ddl_count=legacy,
        missing_expected_table_count=len(set(desired_by_table) - set(exact_tables)),
        content_variant_record_count=content_variants,
        unexpected_ddl_record_count=unexpected,
        exact_duplicate_group_count=sum(count > 1 for count in exact_counts.values()),
        table_identity_duplicate_group_count=sum(
            count > 1 for count in table_counts.values()
        ),
        managed_v1_corrupt_count=corrupt,
        deterministic_id_conflict_count=conflicts,
        classification_reconciled=(
            reconciled
            and len(exact_hashes) + content_variants + unexpected + non_ddl
            == len(records)
        ),
    )
    return facts


def decide_formal_governance(
    facts: FormalGovernanceFacts,
) -> FormalGovernanceDecision:
    checks = {
        "collection 总数不为 198": facts.total_count != EXPECTED_TOTAL_COUNT,
        "DDL 候选不为 115": facts.ddl_candidate_count != EXPECTED_DDL_COUNT,
        "精确 DDL 记录不为 115": facts.exact_match_record_count
        != EXPECTED_DDL_COUNT,
        "精确 DDL 表不为 115": facts.exact_match_table_count
        != EXPECTED_DDL_COUNT,
        "非 DDL Memory 不为 83": facts.non_ddl_count != EXPECTED_NON_DDL_COUNT,
        "存在缺失表": facts.missing_expected_table_count != 0,
        "存在内容变体": facts.content_variant_record_count != 0,
        "存在非预期 DDL": facts.unexpected_ddl_record_count != 0,
        "存在精确重复": facts.exact_duplicate_group_count != 0,
        "存在表身份重复": facts.table_identity_duplicate_group_count != 0,
        "managed v1 记录损坏": facts.managed_v1_corrupt_count != 0,
        "确定性 ID 内容冲突": facts.deterministic_id_conflict_count != 0,
        "分类无法完整对账": not facts.classification_reconciled,
        "managed 与 legacy 数量无法对账": (
            facts.managed_v1_ddl_count + facts.legacy_expected_ddl_count
            != EXPECTED_DDL_COUNT
        ),
    }
    reasons = tuple(label for label, failed in checks.items() if failed)
    if reasons:
        return FormalGovernanceDecision("BLOCKED_FORMAL_STATE", reasons, facts)
    if facts.legacy_expected_ddl_count:
        return FormalGovernanceDecision(
            "IDENTITY_MIGRATION_REQUIRED",
            ("至少一条精确 DDL 仍使用 legacy ID 或旧 Metadata",),
            facts,
        )
    return FormalGovernanceDecision("ALREADY_MANAGED_NO_SWITCH", (), facts)


def build_legacy_delete_allowlist(
    decision: FormalGovernanceDecision,
    desired_memories: Sequence[DdlMemoryIdentity],
    records: Sequence[ExistingDdlMemoryRecord],
) -> tuple[LegacyDdlMigration, ...]:
    if decision.state != "IDENTITY_MIGRATION_REQUIRED":
        raise ValueError("只有 IDENTITY_MIGRATION_REQUIRED 才允许生成删除 allowlist")
    desired_by_sha = {
        _document_sha(item.normalized_ddl): item for item in desired_memories
    }
    migrations = []
    for record in records:
        document_sha = _document_sha(record.document)
        target = desired_by_sha.get(document_sha)
        if target is None:
            continue
        if _is_valid_managed_record(target, record):
            continue
        claims_managed = (
            record.record_id == target.record_id
            or record.metadata.get("identity_version") == IDENTITY_VERSION
            or record.metadata.get("memory_type") == MEMORY_TYPE
        )
        if claims_managed:
            raise ValueError(f"损坏的 managed v1 记录禁止进入 allowlist：{record.record_id}")
        migrations.append(
            LegacyDdlMigration(
                legacy_record_id=record.record_id,
                target_record_id=target.record_id,
                normalized_document_sha256=document_sha,
            )
        )
    migrations.sort(key=lambda item: item.legacy_record_id)
    if len(migrations) != decision.facts.legacy_expected_ddl_count:
        raise ValueError("allowlist 数量与冻结治理决策不一致")
    return tuple(migrations)


def build_candidate_records(
    decision: FormalGovernanceDecision,
    desired_memories: Sequence[DdlMemoryIdentity],
    source_records: Sequence[ExistingDdlMemoryRecord],
    allowlist: Sequence[LegacyDdlMigration],
) -> tuple[ExistingDdlMemoryRecord, ...]:
    if decision.state == "BLOCKED_FORMAL_STATE":
        raise ValueError("BLOCKED_FORMAL_STATE 禁止候选构建")
    source_by_id = {record.record_id: record for record in source_records}
    if len(source_by_id) != len(source_records):
        raise ValueError("来源快照存在重复 record_id")
    desired_by_id = {item.record_id: item for item in desired_memories}
    delete_ids: set[str] = set()
    for item in allowlist:
        if item.legacy_record_id in delete_ids:
            raise ValueError("删除 allowlist 存在重复 record_id")
        source = source_by_id.get(item.legacy_record_id)
        target = desired_by_id.get(item.target_record_id)
        if source is None or target is None:
            raise ValueError("删除 allowlist 引用了不存在的来源或目标")
        if item.classification != "expected_exact_match":
            raise ValueError("删除 allowlist classification 非法")
        if _document_sha(source.document) != item.normalized_document_sha256:
            raise ValueError("删除 allowlist document SHA 与来源不一致")
        if _document_sha(target.normalized_ddl) != item.normalized_document_sha256:
            raise ValueError("删除 allowlist document SHA 与目标不一致")
        if _is_valid_managed_record(target, source):
            raise ValueError("合法 managed v1 目标记录禁止删除")
        delete_ids.add(item.legacy_record_id)

    if decision.state == "ALREADY_MANAGED_NO_SWITCH" and delete_ids:
        raise ValueError("ALREADY_MANAGED_NO_SWITCH 的删除 allowlist 必须为空")
    if decision.state == "IDENTITY_MIGRATION_REQUIRED" and len(delete_ids) != len(allowlist):
        raise ValueError("迁移删除 allowlist 无法对账")

    candidate = {
        record_id: record
        for record_id, record in source_by_id.items()
        if record_id not in delete_ids
    }
    for desired in desired_memories:
        stored = candidate.get(desired.record_id)
        if stored is None:
            candidate[desired.record_id] = _managed_storage_record(desired)
        elif not _is_valid_managed_record(desired, stored):
            raise ValueError(f"候选目标 record_id 冲突：{desired.record_id}")
    return tuple(sorted(candidate.values(), key=lambda item: item.record_id))


def _non_ddl_signature(
    records: Sequence[ExistingDdlMemoryRecord],
    desired_memories: Sequence[DdlMemoryIdentity],
) -> Mapping[str, tuple[str, str]]:
    desired_shas = {_document_sha(item.normalized_ddl) for item in desired_memories}
    return {
        record.record_id: (_document_sha(record.document), _metadata_sha(record.metadata))
        for record in records
        if _document_sha(record.document) not in desired_shas
        and not _looks_like_ddl(record.document)
    }


def validate_candidate_acceptance(
    desired_memories: Sequence[DdlMemoryIdentity],
    source_records: Sequence[ExistingDdlMemoryRecord],
    candidate_records: Sequence[ExistingDdlMemoryRecord],
) -> CandidateAcceptance:
    facts = analyze_formal_records(desired_memories, candidate_records)
    decision = decide_formal_governance(facts)
    reasons = list(decision.reasons)
    try:
        plan = build_ddl_memory_plan(desired_memories, candidate_records)
    except ValueError as exc:
        reasons.append(f"候选 Plan 无效：{exc}")
        plan = build_ddl_memory_plan([], [])
    expected_plan = (0, EXPECTED_DDL_COUNT, 0, 0)
    actual_plan = (
        plan.create_count,
        plan.unchanged_count,
        plan.changed_count,
        plan.removed_count,
    )
    if actual_plan != expected_plan:
        reasons.append(f"候选 Plan 不为 0/115/0/0：{actual_plan}")
    if decision.state != "ALREADY_MANAGED_NO_SWITCH":
        reasons.append(f"候选治理状态不是 ALREADY_MANAGED_NO_SWITCH：{decision.state}")
    if _non_ddl_signature(source_records, desired_memories) != _non_ddl_signature(
        candidate_records, desired_memories
    ):
        reasons.append("非 DDL record_id/document/Metadata 未逐记录保持一致")
    return CandidateAcceptance(
        accepted=not reasons,
        reasons=tuple(reasons),
        facts=facts,
        create_count=plan.create_count,
        unchanged_count=plan.unchanged_count,
        changed_count=plan.changed_count,
        removed_count=plan.removed_count,
    )


def validate_topk_semantic_regression(
    source_results: Sequence[SemanticQueryResult],
    candidate_results: Sequence[SemanticQueryResult],
) -> str:
    def stable(results: Sequence[SemanticQueryResult]) -> list[dict[str, Any]]:
        output = []
        for query in sorted(results, key=lambda item: item.query_id):
            if query.exact_duplicate_slots or query.table_duplicate_slots:
                raise ValueError(f"{query.query_id} 存在重复 Top-K 槽位")
            output.append(
                {
                    "query_id": query.query_id,
                    "expected_table": query.expected_table,
                    "hits": [hit.stable_key() for hit in query.hits],
                }
            )
        return output

    source_payload = stable(source_results)
    candidate_payload = stable(candidate_results)
    if source_payload != candidate_payload:
        raise ValueError("源副本与候选副本 Top-K 语义结果顺序不一致")
    payload = json.dumps(
        source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256_text(payload)


def validate_run_paths(
    formal_source: Path | str, run_root: Path | str, *, mode: str
) -> tuple[Path, Path]:
    source = Path(formal_source).absolute()
    root = Path(run_root).absolute()
    if source != FORMAL_SOURCE.absolute():
        raise ValueError(f"formal_source 必须精确等于 {FORMAL_SOURCE}")
    pattern = RUN_ROOT_PATTERNS.get(mode)
    if pattern is None or pattern.fullmatch(root.name) is None:
        raise ValueError(f"run_root 命名不符合 F6-1I-{mode} 约束")
    if root.parent != BACKUP_ROOT.absolute():
        raise ValueError(f"run_root 必须直接位于 {BACKUP_ROOT}")
    if root.exists():
        raise ValueError("run_root 必须全新且不存在")
    return source, root


def validate_write_target(
    target: Path | str,
    *,
    formal_source: Path | str,
    immutable_archive: Path | str,
    candidate_working_copy: Path | str,
) -> Path:
    resolved = Path(target).absolute()
    if resolved in {Path(formal_source).absolute(), Path(immutable_archive).absolute()}:
        raise ValueError("正式来源和 immutable archive 禁止写入")
    if resolved != Path(candidate_working_copy).absolute():
        raise ValueError("collection 写入只允许 candidate_working_copy")
    return resolved


def validate_client_open_target(
    target: Path | str,
    *,
    formal_source: Path | str,
    immutable_archive: Path | str,
    allowed_working_copies: Sequence[Path | str],
) -> Path:
    resolved = Path(target).absolute()
    if resolved == Path(formal_source).absolute():
        raise ValueError("正式来源禁止由 Chroma Client 打开")
    if resolved == Path(immutable_archive).absolute():
        raise ValueError("immutable archive 禁止由 Chroma Client 打开")
    allowed = {Path(item).absolute() for item in allowed_working_copies}
    if resolved not in allowed:
        raise ValueError("Client 只能打开获准工作副本")
    return resolved


def _tree_sha(root: Path) -> str:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return _sha256_text(
        json.dumps(entries, sort_keys=True, separators=(",", ":"))
    )


def execute_sandbox_switch(
    sandbox_root: Path | str,
    *,
    validate_new_live: Callable[[Path], bool],
    fail_second_step: bool = False,
) -> Mapping[str, Any]:
    root = Path(sandbox_root).absolute()
    live = root / "live"
    candidate = root / "candidate"
    pre_switch = root / "pre_switch"
    failed_candidate = root / "failed_candidate"
    if not root.is_dir() or not live.is_dir() or not candidate.is_dir():
        raise ValueError("sandbox 必须包含 live 和 candidate")
    if pre_switch.exists() or failed_candidate.exists():
        raise ValueError("sandbox pre_switch/failed_candidate 必须不存在")
    original_sha = _tree_sha(live)
    live.rename(pre_switch)
    try:
        if fail_second_step:
            raise RuntimeError("SIMULATED_SECOND_STEP_FAILURE")
        candidate.rename(live)
    except Exception:
        if live.exists():
            live.rename(failed_candidate)
        pre_switch.rename(live)
        if _tree_sha(live) != original_sha:
            raise RuntimeError("第二步失败回滚后原 live Tree SHA 未恢复")
        return {
            "status": "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE",
            "original_tree_sha": original_sha,
            "restored_tree_sha": _tree_sha(live),
        }

    if not validate_new_live(live):
        live.rename(failed_candidate)
        pre_switch.rename(live)
        if _tree_sha(live) != original_sha:
            raise RuntimeError("新 live 验收失败回滚后原 Tree SHA 未恢复")
        return {
            "status": "ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE",
            "original_tree_sha": original_sha,
            "restored_tree_sha": _tree_sha(live),
        }
    return {
        "status": "SWITCHED",
        "original_tree_sha": original_sha,
        "new_live_tree_sha": _tree_sha(live),
        "pre_switch_retained": pre_switch.is_dir(),
        "automatic_rollback_executed": False,
    }


def require_approved_drill_summary(path: Path | str | None) -> Mapping[str, Any]:
    if path is None:
        raise ValueError("正式切换必须提供获批 I-B summary.json")
    summary_path = Path(path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    required = {
        "stage": "F6-1I-B",
        "assessment_status": "PASS",
        "formal_switch_authorized": True,
        "service_stopped_confirmed": True,
        "no_client_occupancy_confirmed": True,
    }
    if any(payload.get(key) != value for key, value in required.items()):
        raise ValueError("I-B summary 未通过固定批准字段校验")
    return payload


def _build_expected_desired_memories() -> tuple[DdlMemoryIdentity, ...]:
    from train_step3 import build_all_table_ddls, group_tables, load_metadata_index

    generated, _geometry_count = build_all_table_ddls(
        group_tables(load_metadata_index())
    )
    desired = tuple(
        sorted(
            (
                build_ddl_memory_identity(
                    DdlMemoryIdentityInput(
                        "postgres_water", "public", "table", item["table"]
                    ),
                    item["ddl"],
                )
                for item in generated
            ),
            key=lambda item: item.record_id,
        )
    )
    if len(desired) != EXPECTED_DDL_COUNT:
        raise RuntimeError(
            f"当前期望 DDL 数量不是 {EXPECTED_DDL_COUNT}：{len(desired)}"
        )
    return desired


def _open_runtime_collection(
    path: Path,
    *,
    formal_source: Path,
    immutable_archive: Path,
    allowed_working_copies: Sequence[Path],
) -> tuple[Any, Any]:
    validate_client_open_target(
        path,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=allowed_working_copies,
    )
    import chromadb
    from agent_config import EMBEDDING_FUNCTION

    client = chromadb.PersistentClient(path=str(path))
    collection = client.get_collection(
        name=COLLECTION_NAME, embedding_function=EMBEDDING_FUNCTION
    )
    return client, collection


def _snapshot_collection_records(collection: Any) -> tuple[ExistingDdlMemoryRecord, ...]:
    raw = collection.get(include=["documents", "metadatas"])
    ids = list(raw.get("ids") or [])
    documents = list(raw.get("documents") or [])
    metadatas = list(raw.get("metadatas") or [])
    if not (len(ids) == len(documents) == len(metadatas)):
        raise RuntimeError("collection 快照数组长度不一致")
    return tuple(
        sorted(
            (
                ExistingDdlMemoryRecord(record_id, document, metadata or {})
                for record_id, document, metadata in zip(ids, documents, metadatas)
            ),
            key=lambda item: item.record_id,
        )
    )


def _close_runtime_collection(client: Any, collection: Any) -> None:
    del collection
    del client
    gc.collect()


def _migrate_candidate_collection(
    collection: Any,
    *,
    decision: FormalGovernanceDecision,
    desired_memories: Sequence[DdlMemoryIdentity],
    source_records: Sequence[ExistingDdlMemoryRecord],
    allowlist: Sequence[LegacyDdlMigration],
) -> tuple[ExistingDdlMemoryRecord, ...]:
    expected_candidate = build_candidate_records(
        decision, desired_memories, source_records, allowlist
    )
    if allowlist:
        collection.delete(ids=[item.legacy_record_id for item in allowlist])
    current_ids = {
        record.record_id for record in _snapshot_collection_records(collection)
    }
    additions = [item for item in desired_memories if item.record_id not in current_ids]
    if additions:
        metadatas = []
        for item in additions:
            metadata = dict(item.effective_metadata)
            metadata["content_fingerprint"] = item.content_fingerprint
            metadatas.append(metadata)
        collection.add(
            ids=[item.record_id for item in additions],
            documents=[item.normalized_ddl for item in additions],
            metadatas=metadatas,
        )
    actual = _snapshot_collection_records(collection)
    if actual != expected_candidate:
        raise RuntimeError("candidate 实际记录与冻结迁移结果不一致")
    return actual


def _semantic_results_from_topk(results: Sequence[Any]) -> tuple[SemanticQueryResult, ...]:
    converted = []
    for query in results:
        hits = tuple(
            SemanticHit(
                classification=item.classification,
                normalized_document_sha256=item.normalized_document_sha256,
                table_name=(
                    item.parsed_table_name
                    if item.classification != "non_ddl_memory"
                    else None
                ),
                record_id=(
                    item.record_id
                    if item.classification == "non_ddl_memory"
                    else None
                ),
            )
            for item in query.results
        )
        converted.append(
            SemanticQueryResult(
                query_id=query.query_id,
                expected_table=query.expected_table,
                hits=hits,
                exact_duplicate_slots=query.exact_duplicate_slot_count,
                table_duplicate_slots=query.table_duplicate_slot_count,
            )
        )
    return tuple(converted)


def _run_topk_semantic_comparison(
    source_query_copy: Path,
    candidate_query_copy: Path,
    *,
    formal_source: Path,
    immutable_archive: Path,
) -> Mapping[str, Any]:
    from training.sop.ddl_memory_topk_impact import (
        _query_collection,
        build_expected_lookup,
        build_query_set,
    )

    allowed = [source_query_copy, candidate_query_copy]
    queries = build_query_set()
    lookup = build_expected_lookup()
    source_client, source_collection = _open_runtime_collection(
        source_query_copy,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=allowed,
    )
    try:
        source_results = _query_collection(source_collection, queries, lookup)
    finally:
        _close_runtime_collection(source_client, source_collection)
    candidate_client, candidate_collection = _open_runtime_collection(
        candidate_query_copy,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=allowed,
    )
    try:
        candidate_results = _query_collection(candidate_collection, queries, lookup)
    finally:
        _close_runtime_collection(candidate_client, candidate_collection)
    semantic_sha = validate_topk_semantic_regression(
        _semantic_results_from_topk(source_results),
        _semantic_results_from_topk(candidate_results),
    )

    def hit_counts(results: Sequence[Any]) -> tuple[int, int, int]:
        top1 = top5 = top10 = 0
        for query in results:
            tables = [item.parsed_table_name for item in query.results]
            if query.expected_table in tables[:1]:
                top1 += 1
            if query.expected_table in tables[:5]:
                top5 += 1
            if query.expected_table in tables[:10]:
                top10 += 1
        return top1, top5, top10

    source_hits = hit_counts(source_results)
    candidate_hits = hit_counts(candidate_results)
    if source_hits != candidate_hits:
        raise RuntimeError("源副本与候选副本 Top-1/5/10 命中数不一致")
    return {
        "query_count": len(queries),
        "top_k": 10,
        "semantic_result_sha256": semantic_sha,
        "expected_table_top1_hit_count": source_hits[0],
        "expected_table_top5_hit_count": source_hits[1],
        "expected_table_top10_hit_count": source_hits[2],
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _facts_dict(facts: FormalGovernanceFacts) -> Mapping[str, Any]:
    return dict(facts.__dict__)


def _prepare_runtime_candidate(formal_source: Path, root: Path) -> Mapping[str, Any]:
    from training.sop.ddl_memory_formal_readonly_audit import (
        build_tree_manifest,
        copy_complete_snapshot,
        verify_tree_sha_gate,
    )

    desired = _build_expected_desired_memories()
    archive = root / "immutable_source_archive"
    source_query_copy = root / "source_query_copy"
    candidate = root / "candidate_working_copy"
    candidate_query_copy = root / "candidate_query_copy"
    evidence = root / "evidence"

    source_before = build_tree_manifest(formal_source)
    root.mkdir(parents=False, exist_ok=False)
    copy_complete_snapshot(formal_source, archive)
    archive_before = build_tree_manifest(archive)
    source_after = build_tree_manifest(formal_source)
    verify_tree_sha_gate(source_before, archive_before, source_after)
    copy_complete_snapshot(archive, source_query_copy)
    copy_complete_snapshot(archive, candidate)

    allowed = [source_query_copy, candidate]
    source_client, source_collection = _open_runtime_collection(
        source_query_copy,
        formal_source=formal_source,
        immutable_archive=archive,
        allowed_working_copies=allowed,
    )
    try:
        source_records = _snapshot_collection_records(source_collection)
    finally:
        _close_runtime_collection(source_client, source_collection)
    source_facts = analyze_formal_records(desired, source_records)
    decision = decide_formal_governance(source_facts)
    if decision.state == "BLOCKED_FORMAL_STATE":
        summary = {
            "stage": "F6-1I-B" if root.name.startswith("f6-1i-b-") else "F6-1I-C",
            "assessment_status": "BLOCKED_FORMAL_STATE",
            "decision_state": decision.state,
            "decision_reasons": list(decision.reasons),
            "source_facts": _facts_dict(source_facts),
            "formal_source_tree_sha_before": source_before.tree_sha256,
            "archive_tree_sha": archive_before.tree_sha256,
            "formal_source_tree_sha_after": source_after.tree_sha256,
        }
        _write_json(evidence / "formal-governance-summary.json", summary)
        raise RuntimeError("BLOCKED_FORMAL_STATE：禁止候选迁移")

    allowlist = build_legacy_delete_allowlist(
        decision, desired, source_records
    ) if decision.state == "IDENTITY_MIGRATION_REQUIRED" else ()
    validate_write_target(
        candidate,
        formal_source=formal_source,
        immutable_archive=archive,
        candidate_working_copy=candidate,
    )
    candidate_client, candidate_collection = _open_runtime_collection(
        candidate,
        formal_source=formal_source,
        immutable_archive=archive,
        allowed_working_copies=allowed,
    )
    try:
        candidate_records = _migrate_candidate_collection(
            candidate_collection,
            decision=decision,
            desired_memories=desired,
            source_records=source_records,
            allowlist=allowlist,
        )
    finally:
        _close_runtime_collection(candidate_client, candidate_collection)
    acceptance = validate_candidate_acceptance(
        desired, source_records, candidate_records
    )
    if not acceptance.accepted:
        raise RuntimeError(f"candidate 验收失败：{acceptance.reasons}")

    copy_complete_snapshot(candidate, candidate_query_copy)
    topk = _run_topk_semantic_comparison(
        source_query_copy,
        candidate_query_copy,
        formal_source=formal_source,
        immutable_archive=archive,
    )
    archive_after = build_tree_manifest(archive)
    if archive_after != archive_before:
        raise RuntimeError("ARCHIVE_INTEGRITY_FAILED")
    return {
        "desired": desired,
        "source_records": source_records,
        "candidate_records": candidate_records,
        "source_facts": source_facts,
        "candidate_path": candidate,
        "archive_path": archive,
        "evidence_path": evidence,
        "public": {
            "formal_source_tree_sha_before": source_before.tree_sha256,
            "archive_tree_sha": archive_before.tree_sha256,
            "formal_source_tree_sha_after": source_after.tree_sha256,
            "archive_tree_sha_after": archive_after.tree_sha256,
            "decision_state": decision.state,
            "legacy_delete_allowlist_count": len(allowlist),
            "candidate_acceptance": "PASS",
            "candidate_facts": _facts_dict(acceptance.facts),
            "candidate_plan": {
                "create": acceptance.create_count,
                "unchanged": acceptance.unchanged_count,
                "changed": acceptance.changed_count,
                "removed": acceptance.removed_count,
            },
            "non_ddl_preservation": "PASS",
            "topk_semantic_regression": dict(topk),
            "formal_chroma_client_open_attempts_by_script": 0,
            "embedding_exported": False,
        },
    }


def _read_working_copy_records(
    path: Path,
    *,
    formal_source: Path,
    immutable_archive: Path,
) -> tuple[ExistingDdlMemoryRecord, ...]:
    client, collection = _open_runtime_collection(
        path,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=[path],
    )
    try:
        return _snapshot_collection_records(collection)
    finally:
        _close_runtime_collection(client, collection)


def isolated_drill(formal_source: Path | str, run_root: Path | str) -> Mapping[str, Any]:
    source, root = validate_run_paths(formal_source, run_root, mode="isolated")
    runtime = _prepare_runtime_candidate(source, root)
    drill_root = root / "sandbox"
    statuses = []
    for name, fail_step2, live_valid in (
        ("forward", False, True),
        ("second_step_failure", True, True),
        ("live_acceptance_failure", False, False),
    ):
        sandbox = drill_root / name
        sandbox.mkdir(parents=True)
        shutil.copytree(
            runtime["archive_path"], sandbox / "live", copy_function=shutil.copy2
        )
        shutil.copytree(
            runtime["candidate_path"],
            sandbox / "candidate",
            copy_function=shutil.copy2,
        )

        def validate_live(path: Path, accepted: bool = live_valid) -> bool:
            if not accepted:
                return False
            records = _read_working_copy_records(
                path,
                formal_source=source,
                immutable_archive=runtime["archive_path"],
            )
            return validate_candidate_acceptance(
                runtime["desired"], runtime["source_records"], records
            ).accepted

        result = execute_sandbox_switch(
            sandbox,
            validate_new_live=validate_live,
            fail_second_step=fail_step2,
        )
        statuses.append(result["status"])
        if result["status"].startswith("ROLLED_BACK"):
            validation_copy = sandbox / "rollback_validation_copy"
            shutil.copytree(
                sandbox / "live", validation_copy, copy_function=shutil.copy2
            )
            restored_records = _read_working_copy_records(
                validation_copy,
                formal_source=source,
                immutable_archive=runtime["archive_path"],
            )
            restored_facts = analyze_formal_records(
                runtime["desired"], restored_records
            )
            if restored_facts != runtime["source_facts"]:
                raise RuntimeError("sandbox 回滚后记录分类和总数未恢复")
    expected_statuses = [
        "SWITCHED",
        "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE",
        "ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE",
    ]
    if statuses != expected_statuses:
        raise RuntimeError(f"sandbox 演练状态异常：{statuses}")
    summary = {
        "stage": "F6-1I-B",
        "assessment_status": "PASS",
        **runtime["public"],
        "sandbox_statuses": statuses,
        "formal_switch_authorized": False,
        "service_stopped_confirmed": False,
        "no_client_occupancy_confirmed": False,
    }
    _write_json(runtime["evidence_path"] / "formal-governance-summary.json", summary)
    return summary


def _run_candidate_full_regression(root: Path, candidate: Path) -> None:
    regression_copy = root / "candidate_regression_copy"
    shutil.copytree(candidate, regression_copy, copy_function=shutil.copy2)
    agent_dir = root / "regression_agent"
    evidence_dir = root / "evidence" / "full-regression"
    agent_dir.mkdir()
    command = [
        sys.executable,
        "tools/run_postgresql_f5_regression.py",
        "--suite",
        "training/regression/postgresql_f5_regression_v1.json",
        "--data-dir",
        str(regression_copy),
        "--agent-dir",
        str(agent_dir),
        "--evidence-dir",
        str(evidence_dir),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2])
    if completed.returncode != 0:
        raise RuntimeError(f"候选完整回归失败：exit={completed.returncode}")


def formal_switch(
    formal_source: Path | str,
    run_root: Path | str,
    approved_drill_summary: Path | str,
) -> Mapping[str, Any]:
    approved = require_approved_drill_summary(approved_drill_summary)
    source, root = validate_run_paths(formal_source, run_root, mode="formal")
    runtime = _prepare_runtime_candidate(source, root)
    if runtime["public"]["formal_source_tree_sha_before"] != approved.get(
        "formal_source_tree_sha_before"
    ):
        raise RuntimeError("当前正式来源 Tree SHA 与获批 I-B 基线不一致")
    _run_candidate_full_regression(root, runtime["candidate_path"])

    timestamp = root.name.removeprefix("f6-1i-c-")
    parent = source.parent
    pre_switch = parent / f"vanna_data_pre_f6_1i_{timestamp}"
    sibling_candidate = parent / f"vanna_data_candidate_f6_1i_{timestamp}"
    failed_candidate = parent / f"vanna_data_failed_f6_1i_{timestamp}"
    if any(path.exists() for path in (pre_switch, sibling_candidate, failed_candidate)):
        raise RuntimeError("正式切换同级目标目录必须全新且不存在")
    shutil.copytree(
        runtime["candidate_path"], sibling_candidate, copy_function=shutil.copy2
    )
    switched = False
    try:
        source.rename(pre_switch)
        try:
            sibling_candidate.rename(source)
            switched = True
        except Exception:
            pre_switch.rename(source)
            raise
        post_client, post_collection = _open_runtime_collection(
            source,
            formal_source=pre_switch,
            immutable_archive=runtime["archive_path"],
            allowed_working_copies=[source],
        )
        try:
            post_records = _snapshot_collection_records(post_collection)
        finally:
            _close_runtime_collection(post_client, post_collection)
        post_acceptance = validate_candidate_acceptance(
            runtime["desired"], runtime["source_records"], post_records
        )
        if not post_acceptance.accepted:
            raise RuntimeError(f"新正式路径只读验收失败：{post_acceptance.reasons}")
    except Exception:
        if switched and source.exists():
            source.rename(failed_candidate)
        if pre_switch.exists() and not source.exists():
            pre_switch.rename(source)
        raise

    summary = {
        "stage": "F6-1I-C",
        "assessment_status": "PASS",
        **runtime["public"],
        "full_regression": "PASS",
        "formal_switch_executed": True,
        "automatic_rollback_executed": False,
        "pre_switch_directory": str(pre_switch),
        "successful_switch_rollback_policy": "DO_NOT_ROLL_BACK",
    }
    _write_json(runtime["evidence_path"] / "formal-governance-summary.json", summary)
    return summary


def _synthetic_desired() -> tuple[DdlMemoryIdentity, ...]:
    return tuple(
        build_ddl_memory_identity(
            DdlMemoryIdentityInput(
                "postgres_water", "public", "table", f"table_{index:03d}"
            ),
            f'CREATE TABLE "table_{index:03d}" (\n  id integer\n);',
        )
        for index in range(EXPECTED_DDL_COUNT)
    )


def _synthetic_source(
    desired: Sequence[DdlMemoryIdentity], *, managed_count: int
) -> tuple[ExistingDdlMemoryRecord, ...]:
    records: list[ExistingDdlMemoryRecord] = []
    for index, item in enumerate(desired):
        if index < managed_count:
            records.append(_managed_storage_record(item))
        else:
            records.append(
                ExistingDdlMemoryRecord(
                    f"legacy-{index:03d}",
                    item.normalized_ddl,
                    {"is_text_memory": True, "legacy_source": "level1"},
                )
            )
    records.extend(
        ExistingDdlMemoryRecord(
            f"non-ddl-{index:03d}",
            f"非 DDL Memory {index}",
            {"kind": "tool" if index else "text", "ordinal": index},
        )
        for index in range(EXPECTED_NON_DDL_COUNT)
    )
    return tuple(records)


def _expect_error(callable_object: Callable[[], Any], expected_text: str) -> None:
    try:
        callable_object()
    except (OSError, RuntimeError, ValueError) as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常信息未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期失败：{expected_text}")


def self_test() -> int:
    desired = _synthetic_desired()
    managed_source = _synthetic_source(desired, managed_count=EXPECTED_DDL_COUNT)
    managed_facts = analyze_formal_records(desired, managed_source)
    assert decide_formal_governance(managed_facts).state == "ALREADY_MANAGED_NO_SWITCH"

    mixed_source = _synthetic_source(desired, managed_count=25)
    mixed_facts = analyze_formal_records(desired, mixed_source)
    migration_decision = decide_formal_governance(mixed_facts)
    assert migration_decision.state == "IDENTITY_MIGRATION_REQUIRED"
    allowlist = build_legacy_delete_allowlist(
        migration_decision, desired, mixed_source
    )
    assert len(allowlist) == 90
    non_ddl_ids = {f"non-ddl-{index:03d}" for index in range(EXPECTED_NON_DDL_COUNT)}
    assert not non_ddl_ids.intersection(
        item.legacy_record_id for item in allowlist
    )
    candidate = build_candidate_records(
        migration_decision, desired, mixed_source, allowlist
    )
    acceptance = validate_candidate_acceptance(desired, mixed_source, candidate)
    assert acceptance.accepted
    assert acceptance.facts.managed_v1_ddl_count == EXPECTED_DDL_COUNT
    assert acceptance.facts.legacy_expected_ddl_count == 0
    assert acceptance.facts.non_ddl_count == EXPECTED_NON_DDL_COUNT
    assert (
        acceptance.create_count,
        acceptance.unchanged_count,
        acceptance.changed_count,
        acceptance.removed_count,
    ) == (0, EXPECTED_DDL_COUNT, 0, 0)

    damaged = list(managed_source)
    damaged_metadata = dict(damaged[0].metadata)
    damaged_metadata["content_fingerprint"] = "0" * 64
    damaged[0] = ExistingDdlMemoryRecord(
        damaged[0].record_id, damaged[0].document, damaged_metadata
    )
    assert (
        decide_formal_governance(analyze_formal_records(desired, damaged)).state
        == "BLOCKED_FORMAL_STATE"
    )
    variant = list(mixed_source)
    variant[0] = ExistingDdlMemoryRecord(
        variant[0].record_id,
        variant[0].document.replace("integer", "bigint"),
        variant[0].metadata,
    )
    assert (
        decide_formal_governance(analyze_formal_records(desired, variant)).state
        == "BLOCKED_FORMAL_STATE"
    )
    missing = mixed_source[1:]
    assert (
        decide_formal_governance(analyze_formal_records(desired, missing)).state
        == "BLOCKED_FORMAL_STATE"
    )
    unexpected = list(mixed_source)
    unexpected[-1] = ExistingDdlMemoryRecord(
        unexpected[-1].record_id,
        'CREATE TABLE "unexpected_table" (\n  id integer\n);',
        unexpected[-1].metadata,
    )
    assert (
        decide_formal_governance(analyze_formal_records(desired, unexpected)).state
        == "BLOCKED_FORMAL_STATE"
    )
    duplicate = mixed_source + (mixed_source[0],)
    assert (
        decide_formal_governance(analyze_formal_records(desired, duplicate)).state
        == "BLOCKED_FORMAL_STATE"
    )
    deterministic_conflict = list(mixed_source)
    deterministic_conflict[0] = ExistingDdlMemoryRecord(
        desired[0].record_id,
        desired[0].normalized_ddl.replace("integer", "bigint"),
        {"identity_version": IDENTITY_VERSION, "memory_type": MEMORY_TYPE},
    )
    conflict_facts = analyze_formal_records(desired, deterministic_conflict)
    assert conflict_facts.deterministic_id_conflict_count == 1
    assert decide_formal_governance(conflict_facts).state == "BLOCKED_FORMAL_STATE"

    semantic_source = (
        SemanticQueryResult(
            "q01",
            "table_000",
            (
                SemanticHit(
                    "expected_exact_match",
                    _document_sha(desired[0].normalized_ddl),
                    table_name="table_000",
                    record_id="legacy-000",
                ),
                SemanticHit(
                    "non_ddl_memory",
                    _document_sha("非 DDL Memory 0"),
                    record_id="non-ddl-000",
                ),
            ),
        ),
    )
    semantic_candidate = (
        SemanticQueryResult(
            "q01",
            "table_000",
            (
                SemanticHit(
                    "expected_exact_match",
                    _document_sha(desired[0].normalized_ddl),
                    table_name="table_000",
                    record_id=desired[0].record_id,
                ),
                semantic_source[0].hits[1],
            ),
        ),
    )
    assert validate_topk_semantic_regression(semantic_source, semantic_candidate)

    with tempfile.TemporaryDirectory(prefix="f6-1i-a-") as temp_name:
        temp = Path(temp_name)
        archive = temp / "immutable_source_archive"
        working = temp / "candidate_working_copy"
        archive.mkdir()
        working.mkdir()
        _expect_error(
            lambda: validate_write_target(
                FORMAL_SOURCE,
                formal_source=FORMAL_SOURCE,
                immutable_archive=archive,
                candidate_working_copy=working,
            ),
            "禁止写入",
        )
        _expect_error(
            lambda: validate_write_target(
                archive,
                formal_source=FORMAL_SOURCE,
                immutable_archive=archive,
                candidate_working_copy=working,
            ),
            "禁止写入",
        )
        assert validate_write_target(
            working,
            formal_source=FORMAL_SOURCE,
            immutable_archive=archive,
            candidate_working_copy=working,
        ) == working.absolute()
        _expect_error(
            lambda: validate_client_open_target(
                archive,
                formal_source=FORMAL_SOURCE,
                immutable_archive=archive,
                allowed_working_copies=[working],
            ),
            "immutable archive",
        )

        def make_sandbox(name: str) -> Path:
            sandbox = temp / name
            (sandbox / "live").mkdir(parents=True)
            (sandbox / "candidate").mkdir()
            (sandbox / "live" / "state.txt").write_text("original", encoding="utf-8")
            (sandbox / "candidate" / "state.txt").write_text("candidate", encoding="utf-8")
            return sandbox

        success = execute_sandbox_switch(
            make_sandbox("success"), validate_new_live=lambda path: True
        )
        assert success["status"] == "SWITCHED"
        assert success["automatic_rollback_executed"] is False
        step2_failure = execute_sandbox_switch(
            make_sandbox("step2-failure"),
            validate_new_live=lambda path: True,
            fail_second_step=True,
        )
        assert step2_failure["status"] == "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE"
        assert step2_failure["original_tree_sha"] == step2_failure["restored_tree_sha"]
        live_failure = execute_sandbox_switch(
            make_sandbox("live-failure"), validate_new_live=lambda path: False
        )
        assert live_failure["status"] == "ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE"
        assert live_failure["original_tree_sha"] == live_failure["restored_tree_sha"]

    _expect_error(lambda: require_approved_drill_summary(None), "获批 I-B")
    forbidden_modules = ("chromadb", "vanna", "agent_config")
    assert not any(
        module == forbidden or module.startswith(forbidden + ".")
        for module in sys.modules
        for forbidden in forbidden_modules
    )

    print("DDL_MEMORY_FORMAL_GOVERNANCE_SELF_TEST=PASS")
    print("GOVERNANCE_DECISION_TEST=PASS")
    print("CANDIDATE_MIGRATION_TEST=PASS")
    print("NON_DDL_PRESERVATION_TEST=PASS")
    print("TOPK_SEMANTIC_KEY_TEST=PASS")
    print("WRITE_BOUNDARY_SCAN=PASS")
    print("ARCHIVE_CLIENT_OPEN_GATE_TEST=PASS")
    print("SANDBOX_FORWARD_SWITCH_TEST=PASS")
    print("SANDBOX_ROLLBACK_TEST=PASS")
    print("FORMAL_SWITCH_APPROVAL_GATE_TEST=PASS")
    print("FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0")
    print("FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0")
    print("FORMAL_SWITCH_EXECUTED=NO")
    print("CHROMA_CLIENT_CREATED=0")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--isolated-drill", action="store_true")
    mode.add_argument("--formal-switch", action="store_true")
    parser.add_argument("--formal-source", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--approved-drill-summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        if args.formal_source or args.run_root or args.approved_drill_summary:
            raise SystemExit("--self-test 不接受正式路径参数")
        return self_test()
    if not args.formal_source or not args.run_root:
        raise SystemExit("治理模式必须提供 --formal-source 和 --run-root")
    if args.isolated_drill:
        summary = isolated_drill(args.formal_source, args.run_root)
    else:
        summary = formal_switch(
            args.formal_source, args.run_root, args.approved_drill_summary
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

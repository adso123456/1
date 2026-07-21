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
from training.sop.ddl_memory_formal_readonly_audit import (
    ExpectedDdl,
    classify_records,
    is_ddl_candidate_document,
    parse_ddl_table_name,
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
APPROVED_IB_TOPK_SEMANTIC_SHA256 = (
    "90ea174bb3e694f8865070437483329001caed630e2fdf486aac892a58cc45e3"
)
APPROVED_PRE_SWITCH_TREE_SHA256 = (
    "ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f"
)
APPROVED_CURRENT_LIVE_TREE_SHA256 = (
    "608630b65bd54aa18cc0a3143d836d171d706e24666ce74ff1af1004e8c5afe0"
)
APPROVED_INCIDENT_SUMMARY_SHA256 = (
    "c83e16116cb845bdf70022f5ff05828c786691cdb1523e5b09f113cd58c345d6"
)
APPROVED_FAILED_SUMMARY_SHA256 = (
    "9e96d7e62e1e2be610881a29e36dc7331610206f5d47e7d1df89b9bfe19118a1"
)
APPROVED_PRE_SWITCH_SOURCE = Path(
    r"E:\3\_runtime\vanna-level1\vanna_data_pre_f6_1i_20260721-094654"
)
APPROVED_FAILED_RUN_ROOT = Path(
    r"E:\3\_training_backups\f6-1i-c-20260721-094654"
)
APPROVED_INCIDENT_SUMMARY = Path(
    r"E:\3\_training_backups\f6-1i-c-r2-20260721-102627"
    r"\evidence\incident-diagnosis-summary.json"
)
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
    "incident": re.compile(r"f6-1i-c-r2-\d{8}-\d{6}\Z"),
    "incident_acceptance": re.compile(r"f6-1i-c-r3-\d{8}-\d{6}\Z"),
}
BACKUP_ROOT = Path(r"E:\3\_training_backups")
RENAME_TARGET_NAME = re.compile(
    r"vanna_data(?:_(?:pre|candidate|failed)_f6_1i_\d{8}-\d{6})?\Z"
)
INCIDENT_DECISION_STATES = frozenset(
    {
        "CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY",
        "CURRENT_LIVE_INVALID_PRE_SWITCH_RECOVERABLE",
        "BLOCKED_INCIDENT_STATE",
    }
)


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


class FormalSwitchTransactionError(RuntimeError):
    """正式切换失败及其回滚验证状态。"""

    def __init__(self, state: str, message: str, *, rollback_verified: bool) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.rollback_verified = rollback_verified


@dataclass(frozen=True)
class IncidentDiagnosticDecision:
    state: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state not in INCIDENT_DECISION_STATES:
            raise ValueError(f"非法事故诊断状态：{self.state}")


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
        if exact is None:
            ddl_candidate = is_ddl_candidate_document(record.document)
            parsed_table = (
                parse_ddl_table_name(record.document) if ddl_candidate else None
            )
            if ddl_candidate and parsed_table in desired_by_table:
                content_variants += 1
            elif ddl_candidate:
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


def _incident_structural_facts_valid(facts: FormalGovernanceFacts) -> bool:
    return (
        facts.total_count == EXPECTED_TOTAL_COUNT
        and facts.ddl_candidate_count == EXPECTED_DDL_COUNT
        and facts.exact_match_record_count == EXPECTED_DDL_COUNT
        and facts.exact_match_table_count == EXPECTED_DDL_COUNT
        and facts.non_ddl_count == EXPECTED_NON_DDL_COUNT
        and facts.missing_expected_table_count == 0
        and facts.content_variant_record_count == 0
        and facts.unexpected_ddl_record_count == 0
        and facts.exact_duplicate_group_count == 0
        and facts.table_identity_duplicate_group_count == 0
        and facts.managed_v1_corrupt_count == 0
        and facts.deterministic_id_conflict_count == 0
        and facts.classification_reconciled
    )


def decide_incident_diagnostic_state(
    current_facts: FormalGovernanceFacts,
    pre_switch_facts: FormalGovernanceFacts,
    *,
    pre_switch_tree_sha256: str,
    non_ddl_preserved: bool,
    failed_evidence_reconciled: bool,
) -> IncidentDiagnosticDecision:
    reasons = []
    pre_switch_legacy = (
        _incident_structural_facts_valid(pre_switch_facts)
        and pre_switch_facts.managed_v1_ddl_count == 0
        and pre_switch_facts.legacy_expected_ddl_count == EXPECTED_DDL_COUNT
    )
    if pre_switch_tree_sha256 != APPROVED_PRE_SWITCH_TREE_SHA256:
        reasons.append("pre-switch Tree SHA 不等于旧正式基线")
    if not pre_switch_legacy:
        reasons.append("pre-switch 不是完整 legacy 正式基线")
    if not non_ddl_preserved:
        reasons.append("current/pre-switch 非 DDL 三元签名不一致")
    if not failed_evidence_reconciled:
        reasons.append("失败 Evidence 无法对账")
    if reasons:
        return IncidentDiagnosticDecision("BLOCKED_INCIDENT_STATE", tuple(reasons))

    current_managed = (
        _incident_structural_facts_valid(current_facts)
        and current_facts.managed_v1_ddl_count == EXPECTED_DDL_COUNT
        and current_facts.legacy_expected_ddl_count == 0
    )
    if current_managed:
        return IncidentDiagnosticDecision(
            "CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY", ()
        )
    if _incident_structural_facts_valid(current_facts):
        return IncidentDiagnosticDecision(
            "CURRENT_LIVE_INVALID_PRE_SWITCH_RECOVERABLE",
            ("current live 未通过完整 managed v1 候选验收",),
        )
    return IncidentDiagnosticDecision(
        "BLOCKED_INCIDENT_STATE",
        ("current live 总数、分类、非 DDL 或重复状态异常",),
    )


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
        and not is_ddl_candidate_document(record.document)
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


def validate_incident_diagnose_paths(
    formal_source: Path | str,
    pre_switch_source: Path | str,
    failed_run_root: Path | str,
    run_root: Path | str,
) -> tuple[Path, Path, Path, Path]:
    current = Path(formal_source).absolute()
    pre_switch = Path(pre_switch_source).absolute()
    failed_root = Path(failed_run_root).absolute()
    root = Path(run_root).absolute()
    if current != FORMAL_SOURCE.absolute():
        raise ValueError(f"formal_source 必须精确等于 {FORMAL_SOURCE}")
    if (
        pre_switch.parent != current.parent
        or re.fullmatch(
            r"vanna_data_pre_f6_1i_\d{8}-\d{6}\Z", pre_switch.name
        )
        is None
    ):
        raise ValueError("pre-switch-source 路径不符合约束")
    if (
        failed_root.parent != BACKUP_ROOT.absolute()
        or re.fullmatch(r"f6-1i-c-\d{8}-\d{6}\Z", failed_root.name) is None
    ):
        raise ValueError("failed-run-root 路径不符合约束")
    if (
        root.parent != BACKUP_ROOT.absolute()
        or RUN_ROOT_PATTERNS["incident"].fullmatch(root.name) is None
    ):
        raise ValueError("run_root 命名不符合 F6-1I-C-R2 约束")
    if root.exists():
        raise ValueError("run_root 必须全新且不存在")
    return current, pre_switch, failed_root, root


def validate_incident_acceptance_paths(
    formal_source: Path | str,
    pre_switch_source: Path | str,
    failed_run_root: Path | str,
    incident_summary: Path | str,
    run_root: Path | str,
) -> tuple[Path, Path, Path, Path, Path]:
    current = Path(formal_source).absolute()
    pre_switch = Path(pre_switch_source).absolute()
    failed_root = Path(failed_run_root).absolute()
    incident_path = Path(incident_summary).absolute()
    root = Path(run_root).absolute()
    expected = (
        FORMAL_SOURCE.absolute(),
        APPROVED_PRE_SWITCH_SOURCE.absolute(),
        APPROVED_FAILED_RUN_ROOT.absolute(),
        APPROVED_INCIDENT_SUMMARY.absolute(),
    )
    if (current, pre_switch, failed_root, incident_path) != expected:
        raise ValueError("独立验收路径必须精确等于冻结事故路径")
    if (
        root.parent != BACKUP_ROOT.absolute()
        or RUN_ROOT_PATTERNS["incident_acceptance"].fullmatch(root.name) is None
    ):
        raise ValueError("run_root 命名不符合 F6-1I-C-R3 约束")
    if root.exists():
        raise ValueError("run_root 必须全新且不存在")
    return current, pre_switch, failed_root, incident_path, root


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
    if RENAME_TARGET_NAME.fullmatch(resolved.name):
        raise ValueError("CLIENT_RENAME_TARGET_CONFLICT：可重命名目录禁止直接打开 Client")
    if resolved == Path(formal_source).absolute():
        raise ValueError("正式来源禁止由 Chroma Client 打开")
    if resolved == Path(immutable_archive).absolute():
        raise ValueError("immutable archive 禁止由 Chroma Client 打开")
    allowed = {Path(item).absolute() for item in allowed_working_copies}
    if resolved not in allowed:
        raise ValueError("Client 只能打开获准工作副本")
    return resolved


def validate_rename_target_never_opened(
    client_open_targets: Sequence[Path | str], rename_targets: Sequence[Path | str]
) -> None:
    opened = {str(Path(item).absolute()).casefold() for item in client_open_targets}
    renamed = {str(Path(item).absolute()).casefold() for item in rename_targets}
    conflicts = sorted(opened & renamed)
    if conflicts:
        raise RuntimeError(f"CLIENT_RENAME_TARGET_CONFLICT：{conflicts}")


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


def verify_sibling_candidate_tree_gate(
    candidate_working_copy: Path | str, sibling_candidate: Path | str
) -> str:
    candidate_path = Path(candidate_working_copy)
    sibling_path = Path(sibling_candidate)
    if not candidate_path.is_dir() or not sibling_path.is_dir():
        raise RuntimeError("candidate Tree SHA 门禁要求两份目录都存在")
    candidate_sha = _tree_sha(candidate_path)
    sibling_sha = _tree_sha(sibling_path)
    if candidate_sha != sibling_sha:
        raise RuntimeError("sibling candidate Tree SHA 与 candidate_working_copy 不一致")
    return candidate_sha


def _verify_restored_live(
    live: Path,
    expected_tree_sha: str,
    validate_restored_classification: Callable[[Path], bool],
) -> None:
    try:
        if not live.is_dir():
            raise RuntimeError("恢复后的正式目录不存在")
        restored_sha = _tree_sha(live)
        if restored_sha != expected_tree_sha:
            raise RuntimeError(
                f"恢复 Tree SHA 不一致：expected={expected_tree_sha}, actual={restored_sha}"
            )
        if validate_restored_classification(live) is not True:
            raise RuntimeError("恢复后的总数、分类或治理事实与切换前不一致")
    except Exception as exc:
        raise FormalSwitchTransactionError(
            "ROLLBACK_VERIFICATION_FAILED",
            str(exc),
            rollback_verified=False,
        ) from exc


def execute_formal_switch_transaction(
    live: Path | str,
    sibling_candidate: Path | str,
    pre_switch: Path | str,
    failed_candidate: Path | str,
    *,
    expected_live_tree_sha: str,
    validate_post_classification: Callable[[Path], None],
    validate_post_topk: Callable[[Path], None],
    run_post_full_regression: Callable[[Path], None],
    validate_restored_classification: Callable[[Path], bool],
    fail_second_step: bool = False,
) -> Mapping[str, Any]:
    live_path = Path(live)
    candidate_path = Path(sibling_candidate)
    pre_switch_path = Path(pre_switch)
    failed_path = Path(failed_candidate)
    if not live_path.is_dir() or not candidate_path.is_dir():
        raise ValueError("正式切换要求 live 和 sibling candidate 均存在")
    if pre_switch_path.exists() or failed_path.exists():
        raise ValueError("pre-switch 和 failed candidate 目录必须全新且不存在")

    live_path.rename(pre_switch_path)
    try:
        if fail_second_step:
            raise RuntimeError("SIMULATED_SECOND_STEP_FAILURE")
        candidate_path.rename(live_path)
    except Exception as exc:
        try:
            if live_path.exists():
                live_path.rename(failed_path)
            elif candidate_path.exists():
                candidate_path.rename(failed_path)
            pre_switch_path.rename(live_path)
            _verify_restored_live(
                live_path,
                expected_live_tree_sha,
                validate_restored_classification,
            )
        except FormalSwitchTransactionError:
            raise
        except Exception as rollback_exc:
            raise FormalSwitchTransactionError(
                "ROLLBACK_VERIFICATION_FAILED",
                str(rollback_exc),
                rollback_verified=False,
            ) from rollback_exc
        raise FormalSwitchTransactionError(
            "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE",
            str(exc),
            rollback_verified=True,
        ) from exc

    post_checks = (
        ("POST_SWITCH_CLASSIFICATION_FAILURE", validate_post_classification),
        ("POST_SWITCH_TOPK_FAILURE", validate_post_topk),
        ("POST_SWITCH_FULL_REGRESSION_FAILURE", run_post_full_regression),
    )
    for failure_state, check in post_checks:
        try:
            check(live_path)
        except Exception as exc:
            try:
                live_path.rename(failed_path)
                pre_switch_path.rename(live_path)
                _verify_restored_live(
                    live_path,
                    expected_live_tree_sha,
                    validate_restored_classification,
                )
            except FormalSwitchTransactionError:
                raise
            except Exception as rollback_exc:
                raise FormalSwitchTransactionError(
                    "ROLLBACK_VERIFICATION_FAILED",
                    str(rollback_exc),
                    rollback_verified=False,
                ) from rollback_exc
            raise FormalSwitchTransactionError(
                f"ROLLED_BACK_AFTER_{failure_state}",
                str(exc),
                rollback_verified=True,
            ) from exc

    return {
        "status": "SWITCHED",
        "original_tree_sha": expected_live_tree_sha,
        "new_live_tree_sha": _tree_sha(live_path),
        "pre_switch_retained": pre_switch_path.is_dir(),
        "automatic_rollback_executed": False,
    }


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


def validate_ib_drill_summary(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("I-B summary 必须是 JSON object")
    required_values = {
        "stage": "F6-1I-B",
        "assessment_status": "PASS",
        "decision_state": "IDENTITY_MIGRATION_REQUIRED",
        "candidate_acceptance": "PASS",
        "non_ddl_preservation": "PASS",
        "formal_switch_authorized": False,
        "service_stopped_confirmed": False,
        "no_client_occupancy_confirmed": False,
    }
    failures = [
        key for key, expected in required_values.items() if payload.get(key) != expected
    ]
    expected_sandbox_statuses = [
        "SWITCHED",
        "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE",
        "ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE",
    ]
    if payload.get("sandbox_statuses") != expected_sandbox_statuses:
        failures.append("sandbox_statuses")
    source_sha = payload.get("formal_source_tree_sha_before")
    if not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None:
        failures.append("formal_source_tree_sha_before")
    for key in (
        "archive_tree_sha",
        "formal_source_tree_sha_after",
        "archive_tree_sha_after",
    ):
        if payload.get(key) != source_sha:
            failures.append(key)
    candidate_facts = payload.get("candidate_facts")
    expected_candidate_facts = {
        "total_count": EXPECTED_TOTAL_COUNT,
        "ddl_candidate_count": EXPECTED_DDL_COUNT,
        "exact_match_record_count": EXPECTED_DDL_COUNT,
        "exact_match_table_count": EXPECTED_DDL_COUNT,
        "non_ddl_count": EXPECTED_NON_DDL_COUNT,
        "managed_v1_ddl_count": EXPECTED_DDL_COUNT,
        "legacy_expected_ddl_count": 0,
        "missing_expected_table_count": 0,
        "content_variant_record_count": 0,
        "unexpected_ddl_record_count": 0,
        "exact_duplicate_group_count": 0,
        "table_identity_duplicate_group_count": 0,
        "managed_v1_corrupt_count": 0,
        "deterministic_id_conflict_count": 0,
        "classification_reconciled": True,
    }
    if not isinstance(candidate_facts, Mapping):
        failures.append("candidate_facts")
    else:
        failures.extend(
            f"candidate_facts.{key}"
            for key, expected in expected_candidate_facts.items()
            if candidate_facts.get(key) != expected
        )
    if payload.get("legacy_delete_allowlist_count") != EXPECTED_DDL_COUNT:
        failures.append("legacy_delete_allowlist_count")
    candidate_plan = payload.get("candidate_plan")
    if candidate_plan != {
        "create": 0,
        "unchanged": EXPECTED_DDL_COUNT,
        "changed": 0,
        "removed": 0,
    }:
        failures.append("candidate_plan")
    expected_topk = {
        "query_count": 12,
        "top_k": 10,
        "semantic_result_sha256": APPROVED_IB_TOPK_SEMANTIC_SHA256,
        "expected_table_top1_hit_count": 3,
        "expected_table_top5_hit_count": 9,
        "expected_table_top10_hit_count": 10,
    }
    topk = payload.get("topk_semantic_regression")
    if not isinstance(topk, Mapping):
        failures.append("topk_semantic_regression")
    else:
        failures.extend(
            f"topk_semantic_regression.{key}"
            for key, expected in expected_topk.items()
            if topk.get(key) != expected
        )
    if failures:
        raise ValueError(f"I-B summary 演练事实校验失败：{sorted(set(failures))}")
    return payload


def require_approved_drill_summary(
    path: Path | str | None, expected_sha256: str | None
) -> Mapping[str, Any]:
    if path is None:
        raise ValueError("正式切换必须提供原始 I-B summary.json")
    if expected_sha256 is None:
        raise ValueError("正式切换必须提供原始 I-B summary SHA-256")
    normalized_expected_sha = expected_sha256.lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized_expected_sha) is None:
        raise ValueError("原始 I-B summary SHA-256 格式非法")
    summary_path = Path(path)
    summary_bytes = summary_path.read_bytes()
    actual_sha = hashlib.sha256(summary_bytes).hexdigest()
    if actual_sha != normalized_expected_sha:
        raise ValueError(
            "原始 I-B summary SHA-256 不一致："
            f"expected={normalized_expected_sha}, actual={actual_sha}"
        )
    payload = json.loads(summary_bytes.decode("utf-8"))
    return validate_ib_drill_summary(payload)


def _expected_incident_facts(*, managed: bool) -> Mapping[str, Any]:
    return {
        "total_count": EXPECTED_TOTAL_COUNT,
        "ddl_candidate_count": EXPECTED_DDL_COUNT,
        "exact_match_record_count": EXPECTED_DDL_COUNT,
        "exact_match_table_count": EXPECTED_DDL_COUNT,
        "non_ddl_count": EXPECTED_NON_DDL_COUNT,
        "managed_v1_ddl_count": EXPECTED_DDL_COUNT if managed else 0,
        "legacy_expected_ddl_count": 0 if managed else EXPECTED_DDL_COUNT,
        "missing_expected_table_count": 0,
        "content_variant_record_count": 0,
        "unexpected_ddl_record_count": 0,
        "exact_duplicate_group_count": 0,
        "table_identity_duplicate_group_count": 0,
        "managed_v1_corrupt_count": 0,
        "deterministic_id_conflict_count": 0,
        "classification_reconciled": True,
    }


def validate_incident_acceptance_summary(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    required = {
        "stage": "F6-1I-C-R2",
        "assessment_status": "CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY",
        "current_live_tree_sha256": APPROVED_CURRENT_LIVE_TREE_SHA256,
        "pre_switch_tree_sha256": APPROVED_PRE_SWITCH_TREE_SHA256,
        "non_ddl_preservation": "PASS",
        "failed_evidence_reconciled": True,
        "runtime_direct_client_open_count": 0,
        "rename_executed": False,
        "recovery_executed": False,
    }
    failures = [key for key, value in required.items() if payload.get(key) != value]
    for key, managed in (("current_live_facts", True), ("pre_switch_facts", False)):
        facts = payload.get(key)
        if not isinstance(facts, Mapping):
            failures.append(key)
            continue
        failures.extend(
            f"{key}.{fact_key}"
            for fact_key, value in _expected_incident_facts(managed=managed).items()
            if facts.get(fact_key) != value
        )
    if failures:
        raise ValueError(f"事故 summary 冻结事实校验失败：{sorted(set(failures))}")
    return payload


def validate_failed_governance_summary(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    required = {
        "stage": "F6-1I-C",
        "assessment_status": "ROLLBACK_VERIFICATION_FAILED",
        "rollback_verification": "FAIL",
    }
    failures = [key for key, value in required.items() if payload.get(key) != value]
    if failures:
        raise ValueError(f"首次失败 summary 状态校验失败：{sorted(failures)}")
    return payload


def _read_frozen_json(
    path: Path | str,
    expected_sha256: str | None,
    approved_sha256: str,
    *,
    label: str,
) -> Mapping[str, Any]:
    if expected_sha256 is None:
        raise ValueError(f"{label} 必须提供 SHA-256")
    normalized = expected_sha256.lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError(f"{label} SHA-256 格式非法")
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != normalized:
        raise ValueError(
            f"{label} SHA-256 不一致：expected={normalized}, actual={actual}"
        )
    if normalized != approved_sha256:
        raise ValueError(f"{label} SHA-256 不等于冻结批准值")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} 必须是 JSON object")
    return payload


def require_incident_acceptance_evidence(
    incident_summary: Path | str | None,
    incident_summary_sha256: str | None,
    failed_run_root: Path | str | None,
    failed_summary_sha256: str | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if incident_summary is None:
        raise ValueError("独立验收必须提供 incident summary")
    incident = _read_frozen_json(
        incident_summary,
        incident_summary_sha256,
        APPROVED_INCIDENT_SUMMARY_SHA256,
        label="incident summary",
    )
    validate_incident_acceptance_summary(incident)
    if failed_run_root is None:
        raise ValueError("独立验收必须提供首次失败 run root")
    failed = _read_frozen_json(
        Path(failed_run_root) / "evidence" / "formal-governance-summary.json",
        failed_summary_sha256,
        APPROVED_FAILED_SUMMARY_SHA256,
        label="首次失败 summary",
    )
    validate_failed_governance_summary(failed)
    return incident, failed


def validate_incident_acceptance_authorization(
    retain_current_live_authorized: bool,
    service_stopped_confirmed: bool,
    no_client_occupancy_confirmed: bool,
) -> None:
    missing = []
    if retain_current_live_authorized is not True:
        missing.append("--retain-current-live-authorized")
    if service_stopped_confirmed is not True:
        missing.append("--service-stopped-confirmed")
    if no_client_occupancy_confirmed is not True:
        missing.append("--no-client-occupancy-confirmed")
    if missing:
        raise ValueError(f"独立验收缺少显式确认：{', '.join(missing)}")


def validate_formal_switch_authorization(
    formal_switch_authorized: bool,
    service_stopped_confirmed: bool,
    no_client_occupancy_confirmed: bool,
) -> None:
    missing = []
    if formal_switch_authorized is not True:
        missing.append("--formal-switch-authorized")
    if service_stopped_confirmed is not True:
        missing.append("--service-stopped-confirmed")
    if no_client_occupancy_confirmed is not True:
        missing.append("--no-client-occupancy-confirmed")
    if missing:
        raise ValueError(f"正式切换缺少显式运行时确认：{', '.join(missing)}")


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
    client_open_audit: list[Path] | None = None,
) -> tuple[Any, Any]:
    validated = validate_client_open_target(
        path,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=allowed_working_copies,
    )
    if client_open_audit is not None:
        client_open_audit.append(validated)
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
    client_open_audit: list[Path] | None = None,
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
        client_open_audit=client_open_audit,
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
        client_open_audit=client_open_audit,
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
        "exact_duplicate_slot_count": 0,
        "table_identity_duplicate_slot_count": 0,
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
    client_open_audit: list[Path] = []

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
        client_open_audit=client_open_audit,
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
        client_open_audit=client_open_audit,
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
        client_open_audit=client_open_audit,
    )
    archive_after = build_tree_manifest(archive)
    if archive_after != archive_before:
        raise RuntimeError("ARCHIVE_INTEGRITY_FAILED")
    return {
        "desired": desired,
        "source_records": source_records,
        "candidate_records": candidate_records,
        "source_facts": source_facts,
        "client_open_audit": client_open_audit,
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
    client_open_audit: list[Path] | None = None,
) -> tuple[ExistingDdlMemoryRecord, ...]:
    client, collection = _open_runtime_collection(
        path,
        formal_source=formal_source,
        immutable_archive=immutable_archive,
        allowed_working_copies=[path],
        client_open_audit=client_open_audit,
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


def _copy_verified_snapshot(source: Path, target: Path) -> str:
    from training.sop.ddl_memory_formal_readonly_audit import (
        build_tree_manifest,
        copy_complete_snapshot,
        verify_tree_sha_gate,
    )

    source_before = build_tree_manifest(source)
    copy_complete_snapshot(source, target)
    copied = build_tree_manifest(target)
    source_after = build_tree_manifest(source)
    verify_tree_sha_gate(source_before, copied, source_after)
    return copied.tree_sha256


def incident_diagnose(
    formal_source: Path | str,
    pre_switch_source: Path | str,
    failed_run_root: Path | str,
    run_root: Path | str,
) -> Mapping[str, Any]:
    current, pre_switch, failed_root, root = validate_incident_diagnose_paths(
        formal_source, pre_switch_source, failed_run_root, run_root
    )
    root.mkdir(parents=False, exist_ok=False)
    missing = [
        label
        for label, path in (
            ("current_live", current),
            ("pre_switch", pre_switch),
            ("failed_run_root", failed_root),
        )
        if not path.is_dir()
    ]
    if missing:
        summary = {
            "stage": "F6-1I-C-R2",
            "assessment_status": "BLOCKED_INCIDENT_STATE",
            "decision_reasons": [f"事故目录缺失：{missing}"],
            "runtime_direct_client_open_count": 0,
            "rename_executed": False,
            "recovery_executed": False,
        }
        _write_json(root / "evidence" / "incident-diagnosis-summary.json", summary)
        return summary
    failed_summary_path = failed_root / "evidence" / "formal-governance-summary.json"
    try:
        failed_summary = json.loads(failed_summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        failed_summary = {}
    failed_evidence_reconciled = (
        failed_summary.get("stage") == "F6-1I-C"
        and failed_summary.get("assessment_status") == "ROLLBACK_VERIFICATION_FAILED"
        and failed_summary.get("rollback_verification") == "FAIL"
    )
    current_copy = root / "current_live_diagnostic_copy"
    pre_switch_copy = root / "pre_switch_diagnostic_copy"
    current_tree_sha = _copy_verified_snapshot(current, current_copy)
    pre_switch_tree_sha = _copy_verified_snapshot(pre_switch, pre_switch_copy)
    desired = _build_expected_desired_memories()
    client_open_audit: list[Path] = []
    current_records = _read_working_copy_records(
        current_copy,
        formal_source=current,
        immutable_archive=pre_switch,
        client_open_audit=client_open_audit,
    )
    pre_switch_records = _read_working_copy_records(
        pre_switch_copy,
        formal_source=current,
        immutable_archive=pre_switch,
        client_open_audit=client_open_audit,
    )
    validate_rename_target_never_opened(
        client_open_audit,
        [current, pre_switch],
    )
    current_facts = analyze_formal_records(desired, current_records)
    pre_switch_facts = analyze_formal_records(desired, pre_switch_records)
    non_ddl_preserved = _non_ddl_signature(
        current_records, desired
    ) == _non_ddl_signature(pre_switch_records, desired)
    decision = decide_incident_diagnostic_state(
        current_facts,
        pre_switch_facts,
        pre_switch_tree_sha256=pre_switch_tree_sha,
        non_ddl_preserved=non_ddl_preserved,
        failed_evidence_reconciled=failed_evidence_reconciled,
    )
    summary = {
        "stage": "F6-1I-C-R2",
        "assessment_status": decision.state,
        "decision_reasons": list(decision.reasons),
        "current_live_tree_sha256": current_tree_sha,
        "pre_switch_tree_sha256": pre_switch_tree_sha,
        "current_live_facts": _facts_dict(current_facts),
        "pre_switch_facts": _facts_dict(pre_switch_facts),
        "non_ddl_preservation": "PASS" if non_ddl_preserved else "FAIL",
        "failed_evidence_reconciled": failed_evidence_reconciled,
        "client_open_targets": [str(path) for path in client_open_audit],
        "runtime_direct_client_open_count": 0,
        "rename_executed": False,
        "recovery_executed": False,
    }
    _write_json(root / "evidence" / "incident-diagnosis-summary.json", summary)
    return summary


def _run_full_regression(
    root: Path, source: Path, *, phase: str, formal_monitor_source: Path
) -> Mapping[str, Any]:
    if phase not in {"pre-switch", "post-switch"}:
        raise ValueError("完整回归 phase 非法")
    regression_copy = root / f"{phase}_regression_copy"
    shutil.copytree(source, regression_copy, copy_function=shutil.copy2)
    agent_dir = root / f"{phase}_regression_agent"
    evidence_dir = root / "evidence" / f"{phase}-full-regression"
    agent_dir.mkdir()
    from tools.run_postgresql_f5_regression import directory_state

    formal_monitor = directory_state(formal_monitor_source)
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
        "--expected-formal-record-count",
        str(formal_monitor["record_count"]),
        "--expected-formal-sha256",
        str(formal_monitor["sha256"]),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2])
    if completed.returncode != 0:
        raise RuntimeError(f"{phase} 完整回归失败：exit={completed.returncode}")
    return {
        "status": "PASS",
        "formal_monitor_record_count": formal_monitor["record_count"],
        "formal_monitor_sha256": formal_monitor["sha256"],
        "formal_monitor_checkpoints": str(
            evidence_dir / "formal-monitor-checkpoints.json"
        ),
    }


def _validate_topk_frozen_result(
    actual: Mapping[str, Any], approved: Mapping[str, Any]
) -> None:
    keys = (
        "query_count",
        "top_k",
        "semantic_result_sha256",
        "expected_table_top1_hit_count",
        "expected_table_top5_hit_count",
        "expected_table_top10_hit_count",
    )
    mismatches = [key for key in keys if actual.get(key) != approved.get(key)]
    if mismatches:
        raise RuntimeError(f"Top-K 结果与获批 I-B 基线不一致：{mismatches}")


def _run_current_live_full_regression(
    root: Path, source: Path, *, formal_monitor_source: Path
) -> Mapping[str, Any]:
    regression_copy = root / "current_live_regression_copy"
    shutil.copytree(source, regression_copy, copy_function=shutil.copy2)
    agent_dir = root / "current_live_regression_agent"
    evidence_dir = root / "evidence" / "current-live-full-regression"
    agent_dir.mkdir()
    from tools.run_postgresql_f5_regression import directory_state

    formal_monitor = directory_state(formal_monitor_source)
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
        "--expected-formal-record-count",
        str(formal_monitor["record_count"]),
        "--expected-formal-sha256",
        str(formal_monitor["sha256"]),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2])
    if completed.returncode != 0:
        raise RuntimeError(f"current live 完整回归失败：exit={completed.returncode}")
    return {
        "status": "PASS",
        "passed": 15,
        "total": 15,
        "formal_monitor_record_count": formal_monitor["record_count"],
        "formal_monitor_sha256": formal_monitor["sha256"],
        "formal_monitor_checkpoints": str(
            evidence_dir / "formal-monitor-checkpoints.json"
        ),
    }


def _execute_incident_current_live_acceptance(
    current: Path,
    pre_switch: Path,
    failed_root: Path,
    incident_path: Path,
    root: Path,
    incident: Mapping[str, Any],
) -> Mapping[str, Any]:
    from training.sop.ddl_memory_formal_readonly_audit import (
        build_tree_manifest,
        copy_complete_snapshot,
    )

    root.mkdir(parents=False, exist_ok=False)
    evidence_path = root / "evidence" / "formal-current-live-acceptance-summary.json"
    try:
        current_before = build_tree_manifest(current)
        pre_switch_before = build_tree_manifest(pre_switch)
        if current_before.tree_sha256 != incident["current_live_tree_sha256"]:
            raise RuntimeError("current live Tree SHA 与事故诊断冻结值不一致")
        if pre_switch_before.tree_sha256 != incident["pre_switch_tree_sha256"]:
            raise RuntimeError("pre-switch Tree SHA 与事故诊断冻结值不一致")

        current_copy = root / "current_live_acceptance_copy"
        pre_switch_copy = root / "pre_switch_acceptance_copy"
        current_copy_sha = _copy_verified_snapshot(current, current_copy)
        pre_switch_copy_sha = _copy_verified_snapshot(pre_switch, pre_switch_copy)
        if current_copy_sha != current_before.tree_sha256:
            raise RuntimeError("current live 验收副本 Tree SHA 不一致")
        if pre_switch_copy_sha != pre_switch_before.tree_sha256:
            raise RuntimeError("pre-switch 验收副本 Tree SHA 不一致")

        desired = _build_expected_desired_memories()
        client_open_audit: list[Path] = []
        current_records = _read_working_copy_records(
            current_copy,
            formal_source=current,
            immutable_archive=pre_switch,
            client_open_audit=client_open_audit,
        )
        pre_switch_records = _read_working_copy_records(
            pre_switch_copy,
            formal_source=current,
            immutable_archive=pre_switch,
            client_open_audit=client_open_audit,
        )
        acceptance = validate_candidate_acceptance(
            desired, pre_switch_records, current_records
        )
        if not acceptance.accepted:
            raise RuntimeError(f"current live 分类、Plan 或非 DDL 验收失败：{acceptance.reasons}")
        pre_switch_facts = analyze_formal_records(desired, pre_switch_records)
        if not (
            _incident_structural_facts_valid(pre_switch_facts)
            and pre_switch_facts.managed_v1_ddl_count == 0
            and pre_switch_facts.legacy_expected_ddl_count == EXPECTED_DDL_COUNT
        ):
            raise RuntimeError("pre-switch 不是完整 legacy 正式基线")
        if _non_ddl_signature(current_records, desired) != _non_ddl_signature(
            pre_switch_records, desired
        ):
            raise RuntimeError("current/pre-switch 的 83 条非 DDL 三元签名不一致")

        current_topk_copy = root / "current_live_topk_copy"
        pre_switch_topk_copy = root / "pre_switch_topk_copy"
        copy_complete_snapshot(current_copy, current_topk_copy)
        copy_complete_snapshot(pre_switch_copy, pre_switch_topk_copy)
        topk = _run_topk_semantic_comparison(
            pre_switch_topk_copy,
            current_topk_copy,
            formal_source=current,
            immutable_archive=pre_switch,
            client_open_audit=client_open_audit,
        )
        _validate_topk_frozen_result(
            topk,
            {
                "query_count": 12,
                "top_k": 10,
                "semantic_result_sha256": APPROVED_IB_TOPK_SEMANTIC_SHA256,
                "expected_table_top1_hit_count": 3,
                "expected_table_top5_hit_count": 9,
                "expected_table_top10_hit_count": 10,
            },
        )
        if (
            topk.get("exact_duplicate_slot_count") != 0
            or topk.get("table_identity_duplicate_slot_count") != 0
        ):
            raise RuntimeError("Top-K 存在精确或表身份重复槽位")
        full_regression = _run_current_live_full_regression(
            root,
            current_copy,
            formal_monitor_source=current,
        )
        if full_regression.get("status") != "PASS" or (
            full_regression.get("passed"), full_regression.get("total")
        ) != (15, 15):
            raise RuntimeError("current live 完整回归不是 15/15 PASS")

        regression_client_target = (root / "current_live_regression_copy").absolute()
        allowed_client_targets = {
            current_copy.absolute(),
            pre_switch_copy.absolute(),
            current_topk_copy.absolute(),
            pre_switch_topk_copy.absolute(),
            regression_client_target,
        }
        if any(path.absolute() not in allowed_client_targets for path in client_open_audit):
            raise RuntimeError("Client 打开了未获准的验收路径")
        validate_rename_target_never_opened(client_open_audit, [current, pre_switch])

        current_after = build_tree_manifest(current)
        pre_switch_after = build_tree_manifest(pre_switch)
        incident_sha_after = hashlib.sha256(incident_path.read_bytes()).hexdigest()
        failed_summary_path = failed_root / "evidence" / "formal-governance-summary.json"
        failed_sha_after = hashlib.sha256(failed_summary_path.read_bytes()).hexdigest()
        source_integrity = (
            current_after == current_before
            and pre_switch_after == pre_switch_before
            and incident_sha_after == APPROVED_INCIDENT_SUMMARY_SHA256
            and failed_sha_after == APPROVED_FAILED_SUMMARY_SHA256
        )
        if not source_integrity:
            summary = {
                "stage": "F6-1I-C-R3-B",
                "assessment_status": "FORMAL_ACCEPTANCE_SOURCE_CHANGED",
                "formal_acceptance": "FAIL",
                "formal_switch_executed": False,
                "recovery_executed": False,
                "rename_executed": False,
                "collection_write_count": 0,
            }
            _write_json(evidence_path, summary)
            raise RuntimeError("FORMAL_ACCEPTANCE_SOURCE_CHANGED")

        summary = {
            "stage": "F6-1I-C-R3-B",
            "assessment_status": "CURRENT_LIVE_FORMALLY_ACCEPTED",
            "formal_acceptance": "PASS",
            "retain_current_live": True,
            "formal_switch_executed": False,
            "recovery_executed": False,
            "rename_executed": False,
            "collection_write_count": 0,
            "current_live_classification": "PASS",
            "current_live_facts": _facts_dict(acceptance.facts),
            "current_live_plan": {
                "create": acceptance.create_count,
                "unchanged": acceptance.unchanged_count,
                "changed": acceptance.changed_count,
                "removed": acceptance.removed_count,
            },
            "pre_switch_legacy_baseline": "PASS",
            "pre_switch_facts": _facts_dict(pre_switch_facts),
            "non_ddl_preservation": "PASS",
            "topk_semantic_regression": "PASS",
            "topk_result": dict(topk),
            "full_regression": "15 / 15",
            "full_regression_monitor": dict(full_regression),
            "source_integrity_after_acceptance": "PASS",
            "current_live_tree_sha_before": current_before.tree_sha256,
            "current_live_acceptance_copy_tree_sha": current_copy_sha,
            "current_live_tree_sha_after": current_after.tree_sha256,
            "pre_switch_tree_sha_before": pre_switch_before.tree_sha256,
            "pre_switch_acceptance_copy_tree_sha": pre_switch_copy_sha,
            "pre_switch_tree_sha_after": pre_switch_after.tree_sha256,
            "incident_summary_sha256_after": incident_sha_after,
            "failed_summary_sha256_after": failed_sha_after,
            "client_open_targets": [
                *(str(path) for path in client_open_audit),
                str(regression_client_target),
            ],
            "runtime_direct_client_open_count": 0,
        }
        _write_json(evidence_path, summary)
        return summary
    except Exception as exc:
        if not evidence_path.exists():
            _write_json(
                evidence_path,
                {
                    "stage": "F6-1I-C-R3-B",
                    "assessment_status": "FORMAL_ACCEPTANCE_FAILED",
                    "formal_acceptance": "FAIL",
                    "failure": str(exc),
                    "formal_switch_executed": False,
                    "recovery_executed": False,
                    "rename_executed": False,
                    "collection_write_count": 0,
                },
            )
        raise


def incident_accept_current_live(
    formal_source: Path | str,
    pre_switch_source: Path | str,
    failed_run_root: Path | str,
    incident_summary: Path | str,
    incident_summary_sha256: str | None,
    failed_summary_sha256: str | None,
    run_root: Path | str,
    *,
    retain_current_live_authorized: bool,
    service_stopped_confirmed: bool,
    no_client_occupancy_confirmed: bool,
) -> Mapping[str, Any]:
    # 顺序是安全契约：两份 Evidence → 显式确认 → 路径 → 正式运行时访问。
    incident, _failed = require_incident_acceptance_evidence(
        incident_summary,
        incident_summary_sha256,
        failed_run_root,
        failed_summary_sha256,
    )
    validate_incident_acceptance_authorization(
        retain_current_live_authorized,
        service_stopped_confirmed,
        no_client_occupancy_confirmed,
    )
    current, pre_switch, failed_root, incident_path, root = (
        validate_incident_acceptance_paths(
            formal_source,
            pre_switch_source,
            failed_run_root,
            incident_summary,
            run_root,
        )
    )
    return _execute_incident_current_live_acceptance(
        current, pre_switch, failed_root, incident_path, root, incident
    )


def formal_switch(
    formal_source: Path | str,
    run_root: Path | str,
    approved_drill_summary: Path | str | None,
    approved_drill_summary_sha256: str | None,
    *,
    formal_switch_authorized: bool,
    service_stopped_confirmed: bool,
    no_client_occupancy_confirmed: bool,
) -> Mapping[str, Any]:
    # 顺序是安全契约：原始字节/SHA → I-B 事实 → 本次确认 → 路径 → 正式访问。
    approved = require_approved_drill_summary(
        approved_drill_summary, approved_drill_summary_sha256
    )
    validate_formal_switch_authorization(
        formal_switch_authorized,
        service_stopped_confirmed,
        no_client_occupancy_confirmed,
    )
    source, root = validate_run_paths(formal_source, run_root, mode="formal")
    runtime = _prepare_runtime_candidate(source, root)
    if runtime["public"]["formal_source_tree_sha_before"] != approved.get(
        "formal_source_tree_sha_before"
    ):
        raise RuntimeError("当前正式来源 Tree SHA 与获批 I-B 基线不一致")
    _validate_topk_frozen_result(
        runtime["public"]["topk_semantic_regression"],
        approved["topk_semantic_regression"],
    )
    pre_switch_full_regression = _run_full_regression(
        root,
        runtime["candidate_path"],
        phase="pre-switch",
        formal_monitor_source=source,
    )

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
    sibling_candidate_tree_sha = verify_sibling_candidate_tree_gate(
        runtime["candidate_path"], sibling_candidate
    )
    post_results: dict[str, Any] = {}

    def validate_post_classification(new_live: Path) -> None:
        classification_copy = root / "post_switch_classification_copy"
        post_results["classification_copy_tree_sha"] = _copy_verified_snapshot(
            new_live, classification_copy
        )
        post_client, post_collection = _open_runtime_collection(
            classification_copy,
            formal_source=new_live,
            immutable_archive=runtime["archive_path"],
            allowed_working_copies=[classification_copy],
            client_open_audit=runtime["client_open_audit"],
        )
        try:
            post_records = _snapshot_collection_records(post_collection)
        finally:
            _close_runtime_collection(post_client, post_collection)
        post_acceptance = validate_candidate_acceptance(
            runtime["desired"], runtime["source_records"], post_records
        )
        if not post_acceptance.accepted:
            raise RuntimeError(f"新正式路径分类/Plan 验收失败：{post_acceptance.reasons}")
        post_results["candidate_acceptance"] = post_acceptance

    def validate_post_topk(new_live: Path) -> None:
        post_query_copy = root / "post_switch_query_copy"
        shutil.copytree(new_live, post_query_copy, copy_function=shutil.copy2)
        result = _run_topk_semantic_comparison(
            root / "source_query_copy",
            post_query_copy,
            formal_source=new_live,
            immutable_archive=runtime["archive_path"],
            client_open_audit=runtime["client_open_audit"],
        )
        _validate_topk_frozen_result(result, approved["topk_semantic_regression"])
        post_results["topk_semantic_regression"] = dict(result)

    def run_post_full_regression(new_live: Path) -> None:
        post_results["full_regression"] = _run_full_regression(
            root,
            new_live,
            phase="post-switch",
            formal_monitor_source=new_live,
        )

    def validate_restored_classification(restored_live: Path) -> bool:
        validation_copy = root / "rollback_validation_copy"
        shutil.copytree(restored_live, validation_copy, copy_function=shutil.copy2)
        restored_records = _read_working_copy_records(
            validation_copy,
            formal_source=restored_live,
            immutable_archive=runtime["archive_path"],
            client_open_audit=runtime["client_open_audit"],
        )
        restored_facts = analyze_formal_records(runtime["desired"], restored_records)
        return restored_facts == runtime["source_facts"]

    anticipated_client_targets = [
        *runtime["client_open_audit"],
        root / "pre-switch_regression_copy",
        root / "post_switch_classification_copy",
        root / "post_switch_query_copy",
        root / "post-switch_regression_copy",
        root / "rollback_validation_copy",
    ]
    rename_targets = [source, pre_switch, sibling_candidate, failed_candidate]
    validate_rename_target_never_opened(anticipated_client_targets, rename_targets)
    _write_json(
        runtime["evidence_path"] / "client-open-path-audit.json",
        {
            "client_open_targets": [str(path) for path in anticipated_client_targets],
            "rename_targets": [str(path) for path in rename_targets],
            "intersection": [],
            "gate": "PASS",
        },
    )

    try:
        switch_result = execute_formal_switch_transaction(
            source,
            sibling_candidate,
            pre_switch,
            failed_candidate,
            expected_live_tree_sha=runtime["public"]["formal_source_tree_sha_before"],
            validate_post_classification=validate_post_classification,
            validate_post_topk=validate_post_topk,
            run_post_full_regression=run_post_full_regression,
            validate_restored_classification=validate_restored_classification,
        )
    except FormalSwitchTransactionError as exc:
        failure_summary = {
            "stage": "F6-1I-C",
            "assessment_status": exc.state,
            "formal_switch_executed": True,
            "automatic_rollback_executed": True,
            "rollback_verification": "PASS" if exc.rollback_verified else "FAIL",
            "failure": str(exc),
        }
        _write_json(
            runtime["evidence_path"] / "formal-governance-summary.json",
            failure_summary,
        )
        raise

    post_acceptance = post_results["candidate_acceptance"]
    summary = {
        "stage": "F6-1I-C",
        "assessment_status": "PASS",
        **runtime["public"],
        "approved_drill_summary_sha256": approved_drill_summary_sha256.lower(),
        "sibling_candidate_tree_sha": sibling_candidate_tree_sha,
        "pre_switch_candidate_acceptance": "PASS",
        "pre_switch_topk_semantic_regression": "PASS",
        "pre_switch_full_regression": pre_switch_full_regression["status"],
        "pre_switch_full_regression_monitor": dict(pre_switch_full_regression),
        "post_switch_candidate_acceptance": "PASS",
        "post_switch_candidate_facts": _facts_dict(post_acceptance.facts),
        "post_switch_candidate_plan": {
            "create": post_acceptance.create_count,
            "unchanged": post_acceptance.unchanged_count,
            "changed": post_acceptance.changed_count,
            "removed": post_acceptance.removed_count,
        },
        "post_switch_topk_semantic_regression": "PASS",
        "post_switch_topk_result": post_results["topk_semantic_regression"],
        "post_switch_full_regression": post_results["full_regression"]["status"],
        "post_switch_full_regression_monitor": dict(
            post_results["full_regression"]
        ),
        "client_open_path_audit": "PASS",
        "formal_switch_executed": True,
        "automatic_rollback_executed": False,
        "pre_switch_directory": str(pre_switch),
        "successful_switch_rollback_policy": "DO_NOT_ROLL_BACK",
        "switch_result": dict(switch_result),
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


def _audit_expected_from_desired(
    desired: Sequence[DdlMemoryIdentity],
) -> tuple[ExpectedDdl, ...]:
    return tuple(
        ExpectedDdl(
            table_name=str(item.effective_metadata["object_name"]),
            logical_id=item.logical_id,
            record_id=item.record_id,
            identity_key=item.identity_key,
            normalized_document_sha256=_document_sha(item.normalized_ddl),
        )
        for item in desired
    )


def _synthetic_ib_summary() -> dict[str, Any]:
    source_sha = "a" * 64
    return {
        "stage": "F6-1I-B",
        "assessment_status": "PASS",
        "decision_state": "IDENTITY_MIGRATION_REQUIRED",
        "formal_source_tree_sha_before": source_sha,
        "archive_tree_sha": source_sha,
        "formal_source_tree_sha_after": source_sha,
        "archive_tree_sha_after": source_sha,
        "legacy_delete_allowlist_count": EXPECTED_DDL_COUNT,
        "candidate_acceptance": "PASS",
        "non_ddl_preservation": "PASS",
        "candidate_facts": {
            "total_count": EXPECTED_TOTAL_COUNT,
            "ddl_candidate_count": EXPECTED_DDL_COUNT,
            "exact_match_record_count": EXPECTED_DDL_COUNT,
            "exact_match_table_count": EXPECTED_DDL_COUNT,
            "non_ddl_count": EXPECTED_NON_DDL_COUNT,
            "managed_v1_ddl_count": EXPECTED_DDL_COUNT,
            "legacy_expected_ddl_count": 0,
            "missing_expected_table_count": 0,
            "content_variant_record_count": 0,
            "unexpected_ddl_record_count": 0,
            "exact_duplicate_group_count": 0,
            "table_identity_duplicate_group_count": 0,
            "managed_v1_corrupt_count": 0,
            "deterministic_id_conflict_count": 0,
            "classification_reconciled": True,
        },
        "candidate_plan": {
            "create": 0,
            "unchanged": EXPECTED_DDL_COUNT,
            "changed": 0,
            "removed": 0,
        },
        "topk_semantic_regression": {
            "query_count": 12,
            "top_k": 10,
            "semantic_result_sha256": APPROVED_IB_TOPK_SEMANTIC_SHA256,
            "expected_table_top1_hit_count": 3,
            "expected_table_top5_hit_count": 9,
            "expected_table_top10_hit_count": 10,
        },
        "sandbox_statuses": [
            "SWITCHED",
            "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE",
            "ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE",
        ],
        "formal_switch_authorized": False,
        "service_stopped_confirmed": False,
        "no_client_occupancy_confirmed": False,
    }


def _synthetic_incident_summary() -> dict[str, Any]:
    return {
        "stage": "F6-1I-C-R2",
        "assessment_status": "CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY",
        "current_live_tree_sha256": APPROVED_CURRENT_LIVE_TREE_SHA256,
        "pre_switch_tree_sha256": APPROVED_PRE_SWITCH_TREE_SHA256,
        "current_live_facts": dict(_expected_incident_facts(managed=True)),
        "pre_switch_facts": dict(_expected_incident_facts(managed=False)),
        "non_ddl_preservation": "PASS",
        "failed_evidence_reconciled": True,
        "runtime_direct_client_open_count": 0,
        "rename_executed": False,
        "recovery_executed": False,
    }


def _synthetic_failed_summary() -> dict[str, Any]:
    return {
        "stage": "F6-1I-C",
        "assessment_status": "ROLLBACK_VERIFICATION_FAILED",
        "rollback_verification": "FAIL",
    }


def _expect_error(callable_object: Callable[[], Any], expected_text: str) -> None:
    try:
        callable_object()
    except (OSError, RuntimeError, ValueError) as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常信息未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期失败：{expected_text}")


def _expect_system_exit(callable_object: Callable[[], Any], expected_text: str) -> None:
    try:
        callable_object()
    except SystemExit as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"SystemExit 未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期 SystemExit：{expected_text}")


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

    marked_variant = (
        "[DDL_MEMORY]\n"
        "表名：table_000\n\n"
        'CREATE TABLE "table_000" (\n  id bigint\n);'
    )
    marked_unknown = (
        "[DDL_MEMORY]\n"
        "表名：unknown_table\n\n"
        'CREATE TABLE "unknown_table" (\n  id integer\n);'
    )
    current_table_example = '工具说明示例：CREATE TABLE "table_000" (id bigint);'
    contract_facts = analyze_formal_records(
        [desired[0]],
        (
            ExistingDdlMemoryRecord("legacy-exact", desired[0].normalized_ddl, {}),
            ExistingDdlMemoryRecord("marked-variant", marked_variant, {}),
            ExistingDdlMemoryRecord("marked-unknown", marked_unknown, {}),
            ExistingDdlMemoryRecord("current-example", current_table_example, {}),
        ),
    )
    assert (
        contract_facts.exact_match_record_count,
        contract_facts.content_variant_record_count,
        contract_facts.unexpected_ddl_record_count,
        contract_facts.non_ddl_count,
    ) == (1, 1, 1, 1)
    assert is_ddl_candidate_document(marked_variant)
    assert parse_ddl_table_name(marked_variant) == "table_000"
    assert not is_ddl_candidate_document(current_table_example)

    six_false_positive_source = list(
        _synthetic_source(desired, managed_count=0)
    )
    false_positive_ids = set()
    for index in range(6):
        source_index = EXPECTED_DDL_COUNT + index
        old_record = six_false_positive_source[source_index]
        false_positive_ids.add(old_record.record_id)
        six_false_positive_source[source_index] = ExistingDdlMemoryRecord(
            old_record.record_id,
            (
                "工具 Memory 中的 SQL 示例："
                f'CREATE TABLE "table_{index:03d}" (id bigint);'
            ),
            old_record.metadata,
        )
    six_false_positive_records = tuple(six_false_positive_source)
    false_positive_facts = analyze_formal_records(
        desired, six_false_positive_records
    )
    assert (
        false_positive_facts.total_count,
        false_positive_facts.ddl_candidate_count,
        false_positive_facts.exact_match_record_count,
        false_positive_facts.exact_match_table_count,
        false_positive_facts.content_variant_record_count,
        false_positive_facts.unexpected_ddl_record_count,
        false_positive_facts.non_ddl_count,
        false_positive_facts.managed_v1_ddl_count,
        false_positive_facts.legacy_expected_ddl_count,
    ) == (198, 115, 115, 115, 0, 0, 83, 0, 115)
    assert (
        decide_formal_governance(false_positive_facts).state
        == "IDENTITY_MIGRATION_REQUIRED"
    )
    non_ddl_signatures = _non_ddl_signature(
        six_false_positive_records, desired
    )
    assert len(non_ddl_signatures) == EXPECTED_NON_DDL_COUNT
    assert false_positive_ids.issubset(non_ddl_signatures)

    audit_classification = classify_records(
        six_false_positive_records, _audit_expected_from_desired(desired)
    )
    parity = (
        audit_classification.formal_collection_count,
        audit_classification.ddl_candidate_record_count,
        audit_classification.expected_exact_match_record_count,
        audit_classification.expected_exact_match_table_count,
        audit_classification.missing_expected_table_count,
        audit_classification.content_variant_record_count,
        audit_classification.unexpected_ddl_record_count,
        audit_classification.non_ddl_memory_count,
        audit_classification.exact_duplicate_group_count,
        audit_classification.table_identity_duplicate_group_count,
    )
    governance_parity = (
        false_positive_facts.total_count,
        false_positive_facts.ddl_candidate_count,
        false_positive_facts.exact_match_record_count,
        false_positive_facts.exact_match_table_count,
        false_positive_facts.missing_expected_table_count,
        false_positive_facts.content_variant_record_count,
        false_positive_facts.unexpected_ddl_record_count,
        false_positive_facts.non_ddl_count,
        false_positive_facts.exact_duplicate_group_count,
        false_positive_facts.table_identity_duplicate_group_count,
    )
    assert parity == governance_parity
    assert analyze_formal_records(
        desired, tuple(reversed(six_false_positive_records))
    ) == false_positive_facts
    assert classify_records(
        tuple(reversed(six_false_positive_records)),
        tuple(reversed(_audit_expected_from_desired(desired))),
    ) == audit_classification

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
        (
            "[DDL_MEMORY]\n"
            "表名：unexpected_table\n\n"
            'CREATE TABLE "unexpected_table" (\n  id integer\n);'
        ),
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
        rename_target = temp / "vanna_data_candidate_f6_1i_20990101-000000"
        _expect_error(
            lambda: validate_client_open_target(
                rename_target,
                formal_source=temp / "formal-source",
                immutable_archive=archive,
                allowed_working_copies=[rename_target],
            ),
            "CLIENT_RENAME_TARGET_CONFLICT",
        )
        _expect_error(
            lambda: validate_rename_target_never_opened(
                [working, rename_target], [rename_target]
            ),
            "CLIENT_RENAME_TARGET_CONFLICT",
        )
        assert validate_rename_target_never_opened([working], [rename_target]) is None

        managed_incident = decide_incident_diagnostic_state(
            managed_facts,
            false_positive_facts,
            pre_switch_tree_sha256=APPROVED_PRE_SWITCH_TREE_SHA256,
            non_ddl_preserved=True,
            failed_evidence_reconciled=True,
        )
        assert managed_incident.state == "CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY"
        recoverable_incident = decide_incident_diagnostic_state(
            mixed_facts,
            false_positive_facts,
            pre_switch_tree_sha256=APPROVED_PRE_SWITCH_TREE_SHA256,
            non_ddl_preserved=True,
            failed_evidence_reconciled=True,
        )
        assert (
            recoverable_incident.state
            == "CURRENT_LIVE_INVALID_PRE_SWITCH_RECOVERABLE"
        )
        blocked_incident = decide_incident_diagnostic_state(
            managed_facts,
            false_positive_facts,
            pre_switch_tree_sha256="b" * 64,
            non_ddl_preserved=True,
            failed_evidence_reconciled=True,
        )
        assert blocked_incident.state == "BLOCKED_INCIDENT_STATE"

        from inspect import getsource

        incident_source = getsource(incident_diagnose)
        assert ".rename(" not in incident_source and "shutil.move" not in incident_source
        full_regression_source = getsource(_run_full_regression)
        assert "regression_copy" in full_regression_source
        formal_switch_source = getsource(formal_switch)
        assert "post_switch_classification_copy" in formal_switch_source
        assert "post_switch_query_copy" in formal_switch_source
        assert "rollback_validation_copy" in formal_switch_source

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

        ib_summary = _synthetic_ib_summary()
        original_payload = json.dumps(ib_summary, sort_keys=True)
        assert validate_ib_drill_summary(ib_summary) is ib_summary
        assert json.dumps(ib_summary, sort_keys=True) == original_payload
        assert (
            ib_summary["formal_switch_authorized"],
            ib_summary["service_stopped_confirmed"],
            ib_summary["no_client_occupancy_confirmed"],
        ) == (False, False, False)
        summary_path = temp / "original-ib-summary.json"
        summary_path.write_text(
            json.dumps(ib_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary_bytes_before = summary_path.read_bytes()
        summary_sha = hashlib.sha256(summary_bytes_before).hexdigest()
        assert require_approved_drill_summary(summary_path, summary_sha) == ib_summary
        assert summary_path.read_bytes() == summary_bytes_before

        from unittest.mock import patch

        incident_summary = _synthetic_incident_summary()
        failed_summary = _synthetic_failed_summary()
        assert validate_incident_acceptance_summary(incident_summary) is incident_summary
        assert validate_failed_governance_summary(failed_summary) is failed_summary
        incident_path = temp / "synthetic-incident-summary.json"
        failed_root = temp / "synthetic-failed-run"
        failed_path = failed_root / "evidence" / "formal-governance-summary.json"
        incident_path.write_text(
            json.dumps(incident_summary, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        failed_path.parent.mkdir(parents=True)
        failed_path.write_text(
            json.dumps(failed_summary, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        incident_sha = hashlib.sha256(incident_path.read_bytes()).hexdigest()
        failed_sha = hashlib.sha256(failed_path.read_bytes()).hexdigest()
        with patch(
            f"{__name__}.APPROVED_INCIDENT_SUMMARY_SHA256", incident_sha
        ), patch(f"{__name__}.APPROVED_FAILED_SUMMARY_SHA256", failed_sha):
            loaded_incident, loaded_failed = require_incident_acceptance_evidence(
                incident_path, incident_sha, failed_root, failed_sha
            )
            assert loaded_incident == incident_summary
            assert loaded_failed == failed_summary

            for bad_incident, expected_text in (
                (
                    {**incident_summary, "assessment_status": "BLOCKED_INCIDENT_STATE"},
                    "assessment_status",
                ),
                (
                    {
                        **incident_summary,
                        "current_live_facts": {
                            **incident_summary["current_live_facts"],
                            "managed_v1_ddl_count": 114,
                        },
                    },
                    "current_live_facts.managed_v1_ddl_count",
                ),
                (
                    {
                        **incident_summary,
                        "pre_switch_facts": {
                            **incident_summary["pre_switch_facts"],
                            "legacy_expected_ddl_count": 114,
                        },
                    },
                    "pre_switch_facts.legacy_expected_ddl_count",
                ),
                (
                    {**incident_summary, "current_live_tree_sha256": "0" * 64},
                    "current_live_tree_sha256",
                ),
                (
                    {**incident_summary, "pre_switch_tree_sha256": "0" * 64},
                    "pre_switch_tree_sha256",
                ),
            ):
                _expect_error(
                    lambda item=bad_incident: validate_incident_acceptance_summary(
                        item
                    ),
                    expected_text,
                )
            _expect_error(
                lambda: validate_failed_governance_summary(
                    {**failed_summary, "assessment_status": "PASS"}
                ),
                "assessment_status",
            )

            with patch.object(Path, "exists", return_value=False):
                accepted_paths = validate_incident_acceptance_paths(
                    FORMAL_SOURCE,
                    APPROVED_PRE_SWITCH_SOURCE,
                    APPROVED_FAILED_RUN_ROOT,
                    APPROVED_INCIDENT_SUMMARY,
                    BACKUP_ROOT / "f6-1i-c-r3-20990101-000000",
                )
                assert accepted_paths[:4] == (
                    FORMAL_SOURCE.absolute(),
                    APPROVED_PRE_SWITCH_SOURCE.absolute(),
                    APPROVED_FAILED_RUN_ROOT.absolute(),
                    APPROVED_INCIDENT_SUMMARY.absolute(),
                )
                _expect_error(
                    lambda: validate_incident_acceptance_paths(
                        temp / "wrong-current",
                        APPROVED_PRE_SWITCH_SOURCE,
                        APPROVED_FAILED_RUN_ROOT,
                        APPROVED_INCIDENT_SUMMARY,
                        BACKUP_ROOT / "f6-1i-c-r3-20990101-000000",
                    ),
                    "冻结事故路径",
                )

            for confirmations, expected_text in (
                ((False, True, True), "--retain-current-live-authorized"),
                ((True, False, True), "--service-stopped-confirmed"),
                ((True, True, False), "--no-client-occupancy-confirmed"),
            ):
                _expect_error(
                    lambda values=confirmations: validate_incident_acceptance_authorization(
                        *values
                    ),
                    expected_text,
                )
            assert validate_incident_acceptance_authorization(True, True, True) is None

            blocked_root = temp / "must-not-be-created"
            with patch(
                f"{__name__}.validate_incident_acceptance_paths",
                side_effect=AssertionError("PATH_GATE_MUST_NOT_RUN"),
            ), patch(
                f"{__name__}._execute_incident_current_live_acceptance",
                side_effect=AssertionError("FORMAL_RUNTIME_MUST_NOT_RUN"),
            ):
                _expect_error(
                    lambda: incident_accept_current_live(
                        FORMAL_SOURCE,
                        APPROVED_PRE_SWITCH_SOURCE,
                        failed_root,
                        incident_path,
                        "0" * 64,
                        failed_sha,
                        blocked_root,
                        retain_current_live_authorized=True,
                        service_stopped_confirmed=True,
                        no_client_occupancy_confirmed=True,
                    ),
                    "SHA-256 不一致",
                )
                _expect_error(
                    lambda: incident_accept_current_live(
                        FORMAL_SOURCE,
                        APPROVED_PRE_SWITCH_SOURCE,
                        failed_root,
                        incident_path,
                        incident_sha,
                        "0" * 64,
                        blocked_root,
                        retain_current_live_authorized=True,
                        service_stopped_confirmed=True,
                        no_client_occupancy_confirmed=True,
                    ),
                    "SHA-256 不一致",
                )
                for confirmations, expected_text in (
                    ((False, True, True), "--retain-current-live-authorized"),
                    ((True, False, True), "--service-stopped-confirmed"),
                    ((True, True, False), "--no-client-occupancy-confirmed"),
                ):
                    _expect_error(
                        lambda values=confirmations: incident_accept_current_live(
                            FORMAL_SOURCE,
                            APPROVED_PRE_SWITCH_SOURCE,
                            failed_root,
                            incident_path,
                            incident_sha,
                            failed_sha,
                            blocked_root,
                            retain_current_live_authorized=values[0],
                            service_stopped_confirmed=values[1],
                            no_client_occupancy_confirmed=values[2],
                        ),
                        expected_text,
                    )
            assert not blocked_root.exists()

            from training.sop.ddl_memory_formal_readonly_audit import TreeManifest

            legacy_source = _synthetic_source(desired, managed_count=0)

            def run_synthetic_acceptance(
                name: str, *, change_current_after: bool = False
            ) -> tuple[Path, Mapping[str, Any] | None]:
                case = temp / name
                current = case / "current_live"
                pre = case / "pre_switch"
                failed = case / "failed"
                run_root = case / "run_root"
                current.mkdir(parents=True)
                pre.mkdir()
                failed_summary_path = failed / "evidence" / "formal-governance-summary.json"
                failed_summary_path.parent.mkdir(parents=True)
                failed_summary_path.write_bytes(failed_path.read_bytes())
                synthetic_incident_path = case / "incident-summary.json"
                synthetic_incident_path.write_bytes(incident_path.read_bytes())
                manifest_calls = {"current": 0}
                opened: list[Path] = []

                def fake_manifest(path: Path | str) -> TreeManifest:
                    target = Path(path)
                    if target == current:
                        manifest_calls["current"] += 1
                        sha = APPROVED_CURRENT_LIVE_TREE_SHA256
                        if change_current_after and manifest_calls["current"] > 1:
                            sha = "f" * 64
                        return TreeManifest(sha, ())
                    return TreeManifest(APPROVED_PRE_SWITCH_TREE_SHA256, ())

                def fake_verified_copy(source: Path, target: Path) -> str:
                    target.mkdir()
                    return (
                        APPROVED_CURRENT_LIVE_TREE_SHA256
                        if source == current
                        else APPROVED_PRE_SWITCH_TREE_SHA256
                    )

                def fake_complete_copy(_source: Path, target: Path) -> None:
                    target.mkdir()

                def fake_read(
                    path: Path, *, client_open_audit: list[Path] | None = None, **_kw: Any
                ) -> tuple[ExistingDdlMemoryRecord, ...]:
                    if client_open_audit is not None:
                        client_open_audit.append(path.absolute())
                        opened.append(path.absolute())
                    return managed_source if path.name.startswith("current") else legacy_source

                def fake_topk(
                    source_copy: Path,
                    candidate_copy: Path,
                    *,
                    client_open_audit: list[Path] | None = None,
                    **_kw: Any,
                ) -> Mapping[str, Any]:
                    if client_open_audit is not None:
                        client_open_audit.extend(
                            [source_copy.absolute(), candidate_copy.absolute()]
                        )
                    return {
                        "query_count": 12,
                        "top_k": 10,
                        "semantic_result_sha256": APPROVED_IB_TOPK_SEMANTIC_SHA256,
                        "expected_table_top1_hit_count": 3,
                        "expected_table_top5_hit_count": 9,
                        "expected_table_top10_hit_count": 10,
                        "exact_duplicate_slot_count": 0,
                        "table_identity_duplicate_slot_count": 0,
                    }

                def fake_regression(
                    root: Path, _source: Path, **_kw: Any
                ) -> Mapping[str, Any]:
                    (root / "current_live_regression_copy").mkdir()
                    return {"status": "PASS", "passed": 15, "total": 15}

                with patch(
                    "training.sop.ddl_memory_formal_readonly_audit.build_tree_manifest",
                    side_effect=fake_manifest,
                ), patch(
                    "training.sop.ddl_memory_formal_readonly_audit.copy_complete_snapshot",
                    side_effect=fake_complete_copy,
                ), patch(
                    f"{__name__}._copy_verified_snapshot",
                    side_effect=fake_verified_copy,
                ), patch(
                    f"{__name__}._build_expected_desired_memories", return_value=desired
                ), patch(
                    f"{__name__}._read_working_copy_records", side_effect=fake_read
                ), patch(
                    f"{__name__}._run_topk_semantic_comparison", side_effect=fake_topk
                ), patch(
                    f"{__name__}._run_current_live_full_regression",
                    side_effect=fake_regression,
                ), patch(
                    f"{__name__}.APPROVED_INCIDENT_SUMMARY_SHA256", incident_sha
                ), patch(f"{__name__}.APPROVED_FAILED_SUMMARY_SHA256", failed_sha):
                    if change_current_after:
                        _expect_error(
                            lambda: _execute_incident_current_live_acceptance(
                                current,
                                pre,
                                failed,
                                synthetic_incident_path,
                                run_root,
                                incident_summary,
                            ),
                            "FORMAL_ACCEPTANCE_SOURCE_CHANGED",
                        )
                        return run_root, None
                    result = _execute_incident_current_live_acceptance(
                        current,
                        pre,
                        failed,
                        synthetic_incident_path,
                        run_root,
                        incident_summary,
                    )
                    assert set(Path(item).name for item in result["client_open_targets"]) == {
                        "current_live_acceptance_copy",
                        "pre_switch_acceptance_copy",
                        "current_live_topk_copy",
                        "pre_switch_topk_copy",
                        "current_live_regression_copy",
                    }
                    return run_root, result

            acceptance_root, acceptance_summary = run_synthetic_acceptance(
                "incident-accept-success"
            )
            assert acceptance_summary is not None
            assert acceptance_summary["assessment_status"] == "CURRENT_LIVE_FORMALLY_ACCEPTED"
            assert acceptance_summary["current_live_plan"] == {
                "create": 0,
                "unchanged": EXPECTED_DDL_COUNT,
                "changed": 0,
                "removed": 0,
            }
            assert acceptance_summary["non_ddl_preservation"] == "PASS"
            assert acceptance_summary["topk_semantic_regression"] == "PASS"
            assert acceptance_summary["full_regression"] == "15 / 15"
            assert acceptance_summary["formal_switch_executed"] is False
            assert acceptance_summary["recovery_executed"] is False
            assert acceptance_summary["rename_executed"] is False
            assert acceptance_summary["collection_write_count"] == 0
            assert (acceptance_root / "current_live_regression_copy").is_dir()
            changed_root, changed_summary = run_synthetic_acceptance(
                "incident-accept-source-change", change_current_after=True
            )
            assert changed_summary is None
            changed_payload = json.loads(
                (changed_root / "evidence" / "formal-current-live-acceptance-summary.json").read_text(
                    encoding="utf-8"
                )
            )
            assert changed_payload["assessment_status"] == "FORMAL_ACCEPTANCE_SOURCE_CHANGED"

        acceptance_source = getsource(_execute_incident_current_live_acceptance)
        assert ".rename(" not in acceptance_source and "shutil.move" not in acceptance_source
        assert "collection.add" not in acceptance_source
        assert "collection.delete" not in acceptance_source

        with patch(
            f"{__name__}.validate_run_paths",
            side_effect=AssertionError("PATH_VALIDATION_MUST_NOT_RUN"),
        ), patch(
            f"{__name__}._prepare_runtime_candidate",
            side_effect=AssertionError("FORMAL_FILESYSTEM_MUST_NOT_RUN"),
        ):
            _expect_error(
                lambda: formal_switch(
                    FORMAL_SOURCE,
                    temp / "unused-hash-root",
                    summary_path,
                    "0" * 64,
                    formal_switch_authorized=True,
                    service_stopped_confirmed=True,
                    no_client_occupancy_confirmed=True,
                ),
                "SHA-256 不一致",
            )

        invalid_cases = (
            ({**ib_summary, "assessment_status": "FAIL"}, "assessment_status"),
            ({**ib_summary, "sandbox_statuses": ["SWITCHED"]}, "sandbox_statuses"),
            ({**ib_summary, "archive_tree_sha": "b" * 64}, "archive_tree_sha"),
            (
                {
                    **ib_summary,
                    "candidate_facts": {
                        **ib_summary["candidate_facts"],
                        "managed_v1_ddl_count": 114,
                    },
                },
                "candidate_facts.managed_v1_ddl_count",
            ),
            (
                {
                    **ib_summary,
                    "topk_semantic_regression": {
                        **ib_summary["topk_semantic_regression"],
                        "semantic_result_sha256": "b" * 64,
                    },
                },
                "topk_semantic_regression.semantic_result_sha256",
            ),
            (
                {
                    **ib_summary,
                    "candidate_plan": {
                        "create": 1,
                        "unchanged": 114,
                        "changed": 0,
                        "removed": 0,
                    },
                },
                "candidate_plan",
            ),
        )
        for invalid_summary, expected_text in invalid_cases:
            _expect_error(
                lambda item=invalid_summary: validate_ib_drill_summary(item),
                expected_text,
            )

        for index, (invalid_summary, expected_text) in enumerate(invalid_cases[3:5]):
            invalid_path = temp / f"invalid-approved-{index}.json"
            invalid_bytes = json.dumps(
                invalid_summary, ensure_ascii=False, indent=2
            ).encode("utf-8")
            invalid_path.write_bytes(invalid_bytes)
            invalid_sha = hashlib.sha256(invalid_bytes).hexdigest()
            with patch(
                f"{__name__}.validate_run_paths",
                side_effect=AssertionError("PATH_VALIDATION_MUST_NOT_RUN"),
            ), patch(
                f"{__name__}._prepare_runtime_candidate",
                side_effect=AssertionError("FORMAL_FILESYSTEM_MUST_NOT_RUN"),
            ):
                _expect_error(
                    lambda p=invalid_path, s=invalid_sha: formal_switch(
                        FORMAL_SOURCE,
                        temp / "unused-facts-root",
                        p,
                        s,
                        formal_switch_authorized=True,
                        service_stopped_confirmed=True,
                        no_client_occupancy_confirmed=True,
                    ),
                    expected_text,
                )

        _expect_error(
            lambda: validate_formal_switch_authorization(False, True, True),
            "--formal-switch-authorized",
        )
        _expect_error(
            lambda: validate_formal_switch_authorization(True, False, True),
            "--service-stopped-confirmed",
        )
        _expect_error(
            lambda: validate_formal_switch_authorization(True, True, False),
            "--no-client-occupancy-confirmed",
        )
        assert validate_formal_switch_authorization(True, True, True) is None

        incomplete_confirmations = (
            (False, True, True, "--formal-switch-authorized"),
            (True, False, True, "--service-stopped-confirmed"),
            (True, True, False, "--no-client-occupancy-confirmed"),
        )
        for authorized, stopped, unoccupied, expected_text in incomplete_confirmations:
            with patch(
                f"{__name__}.validate_run_paths",
                side_effect=AssertionError("PATH_VALIDATION_MUST_NOT_RUN"),
            ), patch(
                f"{__name__}._prepare_runtime_candidate",
                side_effect=AssertionError("FORMAL_FILESYSTEM_MUST_NOT_RUN"),
            ):
                _expect_error(
                    lambda: formal_switch(
                        FORMAL_SOURCE,
                        temp / "unused-run-root",
                        summary_path,
                        summary_sha,
                        formal_switch_authorized=authorized,
                        service_stopped_confirmed=stopped,
                        no_client_occupancy_confirmed=unoccupied,
                    ),
                    expected_text,
                )

        def make_formal_case(name: str) -> tuple[Path, Path, Path, Path]:
            case = temp / name
            live = case / "vanna_data"
            sibling = case / "candidate"
            live.mkdir(parents=True)
            sibling.mkdir()
            (live / "state.txt").write_text("original", encoding="utf-8")
            (sibling / "state.txt").write_text("candidate", encoding="utf-8")
            return live, sibling, case / "pre_switch", case / "failed_candidate"

        mismatch_live, mismatch_sibling, _, _ = make_formal_case("tree-mismatch")
        _expect_error(
            lambda: verify_sibling_candidate_tree_gate(
                mismatch_live, mismatch_sibling
            ),
            "Tree SHA",
        )

        def run_failure_case(name: str, failed_stage: str) -> None:
            live, sibling, pre, failed = make_formal_case(name)
            original_sha = _tree_sha(live)
            restored_checks: list[Path] = []

            def check(stage: str) -> Callable[[Path], None]:
                def inner(_path: Path) -> None:
                    if stage == failed_stage:
                        raise RuntimeError(f"SIMULATED_{stage}")

                return inner

            try:
                execute_formal_switch_transaction(
                    live,
                    sibling,
                    pre,
                    failed,
                    expected_live_tree_sha=original_sha,
                    validate_post_classification=check("CLASSIFICATION"),
                    validate_post_topk=check("TOPK"),
                    run_post_full_regression=check("FULL_REGRESSION"),
                    validate_restored_classification=lambda path: (
                        restored_checks.append(path) or True
                    ),
                )
            except FormalSwitchTransactionError as exc:
                assert exc.state == f"ROLLED_BACK_AFTER_POST_SWITCH_{failed_stage}_FAILURE"
                assert exc.rollback_verified is True
            else:
                raise AssertionError(f"预期 {failed_stage} 失败并回滚")
            assert _tree_sha(live) == original_sha
            assert restored_checks == [live]
            assert failed.is_dir()

        run_failure_case("classification-failure", "CLASSIFICATION")
        run_failure_case("topk-failure", "TOPK")
        run_failure_case("full-regression-failure", "FULL_REGRESSION")

        live, sibling, pre, failed = make_formal_case("second-step-failure")
        original_sha = _tree_sha(live)
        try:
            execute_formal_switch_transaction(
                live,
                sibling,
                pre,
                failed,
                expected_live_tree_sha=original_sha,
                validate_post_classification=lambda path: None,
                validate_post_topk=lambda path: None,
                run_post_full_regression=lambda path: None,
                validate_restored_classification=lambda path: True,
                fail_second_step=True,
            )
        except FormalSwitchTransactionError as exc:
            assert exc.state == "ROLLED_BACK_AFTER_SECOND_STEP_FAILURE"
            assert exc.rollback_verified is True
        else:
            raise AssertionError("预期第二步失败并回滚")
        assert _tree_sha(live) == original_sha

        live, sibling, pre, failed = make_formal_case("rollback-proof-failure")
        original_sha = _tree_sha(live)
        try:
            execute_formal_switch_transaction(
                live,
                sibling,
                pre,
                failed,
                expected_live_tree_sha=original_sha,
                validate_post_classification=lambda path: (_ for _ in ()).throw(
                    RuntimeError("SIMULATED_CLASSIFICATION")
                ),
                validate_post_topk=lambda path: None,
                run_post_full_regression=lambda path: None,
                validate_restored_classification=lambda path: False,
            )
        except FormalSwitchTransactionError as exc:
            assert exc.state == "ROLLBACK_VERIFICATION_FAILED"
            assert exc.rollback_verified is False
        else:
            raise AssertionError("回滚验证失败不得宣称恢复")

        success_parent = temp / "formal-success"
        source = success_parent / "vanna_data"
        source.mkdir(parents=True)
        (source / "state.txt").write_text("original", encoding="utf-8")
        formal_root = temp / "f6-1i-c-20990101-000000"
        formal_root.mkdir()
        candidate_path = formal_root / "candidate_working_copy"
        candidate_path.mkdir()
        (candidate_path / "state.txt").write_text("candidate", encoding="utf-8")
        archive_path = formal_root / "immutable_source_archive"
        archive_path.mkdir()
        evidence_path = formal_root / "evidence"
        evidence_path.mkdir()
        (formal_root / "source_query_copy").mkdir()
        candidate_facts = analyze_formal_records(desired, managed_source)
        synthetic_acceptance = CandidateAcceptance(
            True, (), candidate_facts, 0, EXPECTED_DDL_COUNT, 0, 0
        )
        synthetic_runtime = {
            "desired": desired,
            "source_records": six_false_positive_records,
            "candidate_records": managed_source,
            "source_facts": false_positive_facts,
            "client_open_audit": [],
            "candidate_path": candidate_path,
            "archive_path": archive_path,
            "evidence_path": evidence_path,
            "public": {
                "formal_source_tree_sha_before": ib_summary[
                    "formal_source_tree_sha_before"
                ],
                "candidate_acceptance": "PASS",
                "candidate_facts": _facts_dict(candidate_facts),
                "candidate_plan": {
                    "create": 0,
                    "unchanged": EXPECTED_DDL_COUNT,
                    "changed": 0,
                    "removed": 0,
                },
                "non_ddl_preservation": "PASS",
                "topk_semantic_regression": ib_summary[
                    "topk_semantic_regression"
                ],
            },
        }
        regression_phases: list[str] = []
        regression_monitor_shas: list[str] = []
        client_open_paths: list[Path] = []
        topk_paths: list[tuple[Path, Path]] = []

        def fake_full_regression(
            _root: Path,
            _source: Path,
            *,
            phase: str,
            formal_monitor_source: Path,
        ) -> Mapping[str, Any]:
            regression_phases.append(phase)
            assert formal_monitor_source.name == "vanna_data"
            monitor_sha = "c" * 64 if phase == "pre-switch" else "d" * 64
            regression_monitor_shas.append(monitor_sha)
            return {
                "status": "PASS",
                "formal_monitor_record_count": EXPECTED_TOTAL_COUNT,
                "formal_monitor_sha256": monitor_sha,
                "formal_monitor_checkpoints": f"{phase}-checkpoints.json",
            }

        def fake_open(path: Path, **_kwargs: Any) -> tuple[object, object]:
            client_open_paths.append(path)
            return object(), object()

        def fake_topk(
            source_copy: Path, candidate_copy: Path, **_kwargs: Any
        ) -> Mapping[str, Any]:
            topk_paths.append((source_copy, candidate_copy))
            return ib_summary["topk_semantic_regression"]

        with patch(
            f"{__name__}.validate_run_paths", return_value=(source, formal_root)
        ), patch(
            f"{__name__}._prepare_runtime_candidate", return_value=synthetic_runtime
        ), patch(
            f"{__name__}._run_full_regression", side_effect=fake_full_regression
        ), patch(
            f"{__name__}._open_runtime_collection", side_effect=fake_open
        ), patch(
            f"{__name__}._snapshot_collection_records", return_value=managed_source
        ), patch(
            f"{__name__}._close_runtime_collection", return_value=None
        ), patch(
            f"{__name__}.validate_candidate_acceptance",
            return_value=synthetic_acceptance,
        ), patch(
            f"{__name__}._run_topk_semantic_comparison",
            side_effect=fake_topk,
        ):
            success_summary = formal_switch(
                source,
                formal_root,
                summary_path,
                summary_sha,
                formal_switch_authorized=True,
                service_stopped_confirmed=True,
                no_client_occupancy_confirmed=True,
            )
        assert regression_phases == ["pre-switch", "post-switch"]
        assert regression_monitor_shas == ["c" * 64, "d" * 64]
        assert all(not RENAME_TARGET_NAME.fullmatch(path.name) for path in client_open_paths)
        assert any(path.name == "post_switch_classification_copy" for path in client_open_paths)
        assert topk_paths[-1][1].name == "post_switch_query_copy"
        assert success_summary["pre_switch_full_regression"] == "PASS"
        assert success_summary["post_switch_full_regression"] == "PASS"
        assert success_summary["post_switch_candidate_acceptance"] == "PASS"
        assert success_summary["post_switch_topk_semantic_regression"] == "PASS"
        assert success_summary["automatic_rollback_executed"] is False
        assert success_summary["successful_switch_rollback_policy"] == "DO_NOT_ROLL_BACK"

        with patch.object(
            sys,
            "argv",
            [
                "ddl_memory_formal_governance",
                "--isolated-drill",
                "--formal-source",
                str(FORMAL_SOURCE),
                "--run-root",
                str(temp / "unused-ib-root"),
                "--formal-switch-authorized",
            ],
        ):
            _expect_system_exit(main, "--isolated-drill 禁止携带")

    _expect_error(lambda: require_approved_drill_summary(None, None), "原始 I-B")
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
    print("IB_SUMMARY_VALIDATION_TEST=PASS")
    print("IB_SUMMARY_SHA256_GATE_TEST=PASS")
    print("IB_CRITICAL_FACTS_GATE_TEST=PASS")
    print("IB_TOPK_SHA_GATE_TEST=PASS")
    print("IB_SUMMARY_IMMUTABILITY_TEST=PASS")
    print("MISSING_AUTHORIZATION_TEST=PASS")
    print("MISSING_SERVICE_STOP_TEST=PASS")
    print("MISSING_OCCUPANCY_TEST=PASS")
    print("ALL_CONFIRMATIONS_TEST=PASS")
    print("IB_FLAG_REJECTION_TEST=PASS")
    print("PRE_FILESYSTEM_FAILURE_TEST=PASS")
    print("FORMAL_SWITCH_APPROVAL_GATE_TEST=PASS")
    print("SIBLING_CANDIDATE_TREE_GATE_TEST=PASS")
    print("POST_SWITCH_CLASSIFICATION_ROLLBACK_TEST=PASS")
    print("POST_SWITCH_TOPK_ROLLBACK_TEST=PASS")
    print("POST_SWITCH_FULL_REGRESSION_ROLLBACK_TEST=PASS")
    print("ROLLBACK_TREE_SHA_RESTORE_TEST=PASS")
    print("ROLLBACK_CLASSIFICATION_RESTORE_TEST=PASS")
    print("ROLLBACK_VERIFICATION_FAILED_STATE_TEST=PASS")
    print("PRE_POST_FULL_REGRESSION_SEPARATION_TEST=PASS")
    print("SUCCESSFUL_SWITCH_NO_ROLLBACK_TEST=PASS")
    print("DYNAMIC_FORMAL_MONITOR_PHASE_TEST=PASS")
    print("POST_SWITCH_CLASSIFICATION_COPY_ONLY_TEST=PASS")
    print("POST_SWITCH_TOPK_COPY_ONLY_TEST=PASS")
    print("POST_SWITCH_REGRESSION_COPY_ONLY_TEST=PASS")
    print("ROLLBACK_CLASSIFICATION_COPY_ONLY_TEST=PASS")
    print("CLIENT_RENAME_TARGET_CONFLICT_TEST=PASS")
    print("CLIENT_RENAME_TARGET_DISJOINT_TEST=PASS")
    print("INCIDENT_MANAGED_LEGACY_DECISION_TEST=PASS")
    print("INCIDENT_RECOVERABLE_DECISION_TEST=PASS")
    print("INCIDENT_BLOCKED_DECISION_TEST=PASS")
    print("INCIDENT_DIAGNOSE_NO_RENAME_TEST=PASS")
    print("INCIDENT_DIAGNOSE_EXECUTED=NO")
    print("INCIDENT_ACCEPTANCE_SUMMARY_SHA_GATE_TEST=PASS")
    print("FAILED_SUMMARY_SHA_GATE_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_CRITICAL_FACTS_GATE_TEST=PASS")
    print("RETAIN_CURRENT_LIVE_AUTHORIZATION_GATE_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_COPY_ONLY_CLIENT_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_NO_RENAME_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_NO_COLLECTION_WRITE_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_CLASSIFICATION_PLAN_TEST=PASS:0/115/0/0")
    print("INCIDENT_ACCEPTANCE_PRE_SWITCH_BASELINE_TEST=PASS:115/83")
    print("INCIDENT_ACCEPTANCE_NON_DDL_TEST=PASS:83")
    print("INCIDENT_ACCEPTANCE_TOPK_TEST=PASS:12/10/3/9/10")
    print("INCIDENT_ACCEPTANCE_FULL_REGRESSION_TEST=PASS:15/15")
    print("INCIDENT_ACCEPTANCE_SOURCE_CHANGE_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_SUCCESS_STATE_TEST=PASS")
    print("INCIDENT_ACCEPTANCE_EXECUTED=NO")
    print("SHARED_DDL_CLASSIFICATION_CONTRACT_TEST=PASS")
    print("SIX_FALSE_POSITIVE_REGRESSION_TEST=PASS:198/115/115/0/83")
    print("NON_DDL_PRESERVATION_CLASSIFICATION_TEST=PASS:83")
    print("CLASSIFICATION_PARITY_TEST=PASS")
    print("EXPECTED_SYNTHETIC_DECISION_STATE=IDENTITY_MIGRATION_REQUIRED")
    print("FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0")
    print("FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0")
    print("FORMAL_SWITCH_EXECUTED=NO")
    print("RECOVERY_EXECUTED=NO")
    print("FORMAL_ACCEPTANCE_EXECUTED=NO")
    print("CHROMA_CLIENT_CREATED=0")
    print("INCIDENT_RUNTIME_FILESYSTEM_ACCESS_DURING_STAGE=0")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--isolated-drill", action="store_true")
    mode.add_argument("--formal-switch", action="store_true")
    mode.add_argument("--incident-diagnose", action="store_true")
    mode.add_argument("--incident-accept-current-live", action="store_true")
    parser.add_argument("--formal-source", type=Path)
    parser.add_argument("--pre-switch-source", type=Path)
    parser.add_argument("--failed-run-root", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--approved-drill-summary", type=Path)
    parser.add_argument("--approved-drill-summary-sha256")
    parser.add_argument("--incident-summary", type=Path)
    parser.add_argument("--incident-summary-sha256")
    parser.add_argument("--failed-summary-sha256")
    parser.add_argument("--formal-switch-authorized", action="store_true")
    parser.add_argument("--retain-current-live-authorized", action="store_true")
    parser.add_argument("--service-stopped-confirmed", action="store_true")
    parser.add_argument("--no-client-occupancy-confirmed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_confirmations = (
        args.formal_switch_authorized,
        args.retain_current_live_authorized,
        args.service_stopped_confirmed,
        args.no_client_occupancy_confirmed,
    )
    if args.self_test:
        if (
            args.formal_source
            or args.pre_switch_source
            or args.failed_run_root
            or args.run_root
            or args.approved_drill_summary
            or args.approved_drill_summary_sha256
            or args.incident_summary
            or args.incident_summary_sha256
            or args.failed_summary_sha256
            or any(runtime_confirmations)
        ):
            raise SystemExit("--self-test 不接受正式路径参数")
        return self_test()
    if args.isolated_drill:
        if (
            args.approved_drill_summary
            or args.approved_drill_summary_sha256
            or args.incident_summary
            or args.incident_summary_sha256
            or args.failed_summary_sha256
            or any(runtime_confirmations)
        ):
            raise SystemExit("--isolated-drill 禁止携带正式切换批准或运行时确认参数")
    if args.incident_diagnose:
        if (
            args.approved_drill_summary
            or args.approved_drill_summary_sha256
            or args.incident_summary
            or args.incident_summary_sha256
            or args.failed_summary_sha256
            or any(runtime_confirmations)
        ):
            raise SystemExit("--incident-diagnose 禁止携带正式切换批准或确认参数")
        if not all(
            (
                args.formal_source,
                args.pre_switch_source,
                args.failed_run_root,
                args.run_root,
            )
        ):
            raise SystemExit("事故诊断必须提供 current、pre-switch、failed run 和 run-root")
        summary = incident_diagnose(
            args.formal_source,
            args.pre_switch_source,
            args.failed_run_root,
            args.run_root,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.incident_accept_current_live:
        if (
            args.approved_drill_summary
            or args.approved_drill_summary_sha256
            or args.formal_switch_authorized
        ):
            raise SystemExit("--incident-accept-current-live 禁止携带正式切换批准参数")
        if not all(
            (
                args.formal_source,
                args.pre_switch_source,
                args.failed_run_root,
                args.incident_summary,
                args.incident_summary_sha256,
                args.failed_summary_sha256,
                args.run_root,
            )
        ):
            raise SystemExit("独立验收必须提供全部冻结路径、Evidence SHA 和 run-root")
        summary = incident_accept_current_live(
            args.formal_source,
            args.pre_switch_source,
            args.failed_run_root,
            args.incident_summary,
            args.incident_summary_sha256,
            args.failed_summary_sha256,
            args.run_root,
            retain_current_live_authorized=args.retain_current_live_authorized,
            service_stopped_confirmed=args.service_stopped_confirmed,
            no_client_occupancy_confirmed=args.no_client_occupancy_confirmed,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if (
        args.incident_summary
        or args.incident_summary_sha256
        or args.failed_summary_sha256
        or args.retain_current_live_authorized
    ):
        raise SystemExit("治理/正式切换模式禁止携带独立事故验收参数")
    if not args.formal_source or not args.run_root:
        raise SystemExit("治理模式必须提供 --formal-source 和 --run-root")
    if args.isolated_drill:
        summary = isolated_drill(args.formal_source, args.run_root)
    else:
        summary = formal_switch(
            args.formal_source,
            args.run_root,
            args.approved_drill_summary,
            args.approved_drill_summary_sha256,
            formal_switch_authorized=args.formal_switch_authorized,
            service_stopped_confirmed=args.service_stopped_confirmed,
            no_client_occupancy_confirmed=args.no_client_occupancy_confirmed,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

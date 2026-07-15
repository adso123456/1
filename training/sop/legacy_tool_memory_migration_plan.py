"""旧 UUID run_sql Tool Memory 的 2.0 纯逻辑迁移状态契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from training.sop.memory_write_plan import (
    MEMORY_KIND,
    RECORD_SCHEMA_VERSION,
    build_memory_identity_from_canonical_content,
)


MIGRATION_SCHEMA_VERSION = "2.0"
APPROVED_FORMAL_SOURCE_SHA256 = (
    "4fd753c4d4c0d22119b6349856f195fe4ed7e23466120f6786edb979606646d8"
)
APPROVED_VERIFIED_BACKUP_SHA256 = APPROVED_FORMAL_SOURCE_SHA256
DEFAULT_EXPECTED_LEGACY_COUNT = 64
DEFAULT_TEXT_MEMORY_COUNT = 8
MIGRATION_SAMPLE_PREFIX = "LEGACY_TOOL_MIGRATION_"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_COMPATIBILITY_FIELDS = (
    "training_level",
    "train_decision",
    "expected_tables",
)
ENHANCER_REQUIRED_FIELDS = ("sample_id",)
HISTORICAL_OPTIONAL_FIELDS = (
    "review_reason",
    "source",
    "expected_behavior",
)
CHECKED_COMPATIBILITY_FIELDS = tuple(
    sorted(
        set(REQUIRED_COMPATIBILITY_FIELDS)
        | set(ENHANCER_REQUIRED_FIELDS)
        | set(HISTORICAL_OPTIONAL_FIELDS)
    )
)
MIGRATION_COMPATIBILITY_FIELDS = (
    "legacy_storage_id",
    "legacy_document_sha256",
    "legacy_metadata_sha256",
    "migration_batch_id",
    "migration_source_content_sha256",
    "migration_item_sha256",
    "memory_content_sha256",
    "legacy_missing_fields",
    "legacy_invalid_fields",
    "training_batch_id",
    "batch_content_sha256",
)

PhaseAAction = Literal[
    "create_target",
    "resume_target_created",
    "block_preexisting_target",
    "block_content_conflict",
]
MigrationState = Literal[
    "PLAN_BLOCKED",
    "PHASE_A_READY",
    "PHASE_A_ROLLBACK_REQUIRED",
    "PHASE_A_EXECUTED_PENDING_VERIFY",
    "PHASE_A_VERIFICATION_FAILED",
    "PHASE_A_VERIFIED_AWAITING_APPROVAL",
    "PHASE_B_APPROVAL_INVALID",
    "PHASE_B_REVALIDATION_REQUIRED",
    "PHASE_B_PREDELETE_FAILED",
    "PHASE_B_READY",
    "PHASE_B_RECOVERY_REQUIRED",
    "PHASE_B_EXECUTED_PENDING_VERIFY",
    "POST_B_VERIFICATION_FAILED",
    "COMPLETED",
]
ALLOWED_TOOL_CLASSIFICATIONS = {
    "legacy_tool_record",
    "controlled_tool_record",
    "malformed_record",
    "unknown_record",
}


@dataclass(frozen=True)
class LegacyToolRecordSnapshot:
    legacy_storage_id: str
    document: str
    raw_metadata: Mapping[str, Any]
    canonical_content: Mapping[str, Any]
    memory_content_sha256: str
    target_record_id: str
    compatibility_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ExistingMigrationTargetSnapshot:
    record_id: str
    canonical_content: Mapping[str, Any]
    memory_content_sha256: str
    top_level_metadata: Mapping[str, Any]
    compatibility_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class TextMemoryBaselineRecord:
    storage_id: str
    document_sha256: str
    metadata_sha256: str


@dataclass(frozen=True)
class MigrationContractIssue:
    code: str
    legacy_storage_id: str = ""
    target_record_id: str = ""
    message: str = ""


@dataclass(frozen=True)
class LegacyToolMemoryMigrationItem:
    migration_sample_id: str
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    legacy_document_sha256: str
    legacy_metadata_sha256: str
    target_document_sha256: str
    target_top_level_metadata_sha256: str
    target_compatibility_metadata_sha256: str
    migration_item_sha256: str
    target_governance_metadata: Mapping[str, Any]
    target_compatibility_metadata: Mapping[str, Any]
    phase_a_action: PhaseAAction
    issues: tuple[MigrationContractIssue, ...]
    executable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_sample_id": self.migration_sample_id,
            "legacy_storage_id": self.legacy_storage_id,
            "target_record_id": self.target_record_id,
            "memory_content_sha256": self.memory_content_sha256,
            "legacy_document_sha256": self.legacy_document_sha256,
            "legacy_metadata_sha256": self.legacy_metadata_sha256,
            "target_document_sha256": self.target_document_sha256,
            "target_top_level_metadata_sha256": (
                self.target_top_level_metadata_sha256
            ),
            "target_compatibility_metadata_sha256": (
                self.target_compatibility_metadata_sha256
            ),
            "migration_item_sha256": self.migration_item_sha256,
            "target_governance_metadata": dict(self.target_governance_metadata),
            "target_compatibility_metadata": dict(
                self.target_compatibility_metadata
            ),
            "phase_a_action": self.phase_a_action,
            "issues": [asdict(issue) for issue in self.issues],
            "executable": self.executable,
        }


@dataclass(frozen=True)
class LegacyToolMemoryMigrationContract:
    migration_schema_version: str
    migration_batch_id: str
    approved_formal_source_sha256: str
    approved_verified_backup_sha256: str
    expected_legacy_count: int
    text_memory_count: int
    migration_source_content_sha256: str
    migration_contract_sha256: str
    text_memory_baseline: tuple[TextMemoryBaselineRecord, ...]
    text_memory_baseline_sha256: str
    items: tuple[LegacyToolMemoryMigrationItem, ...]
    phase_a_create_target_ids: tuple[str, ...]
    phase_a_resume_target_ids: tuple[str, ...]
    phase_a_rollback_candidate_ids: tuple[str, ...]
    proposed_legacy_delete_ids: tuple[str, ...]
    expected_phase_a_store_count: int
    expected_final_store_count: int
    expected_transition_duplicate_group_count: int
    expected_transition_duplicate_storage_record_count: int
    expected_legacy_id_mismatch_count: int
    issues: tuple[MigrationContractIssue, ...]
    executable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_schema_version": self.migration_schema_version,
            "migration_batch_id": self.migration_batch_id,
            "approved_formal_source_sha256": self.approved_formal_source_sha256,
            "approved_verified_backup_sha256": (
                self.approved_verified_backup_sha256
            ),
            "expected_legacy_count": self.expected_legacy_count,
            "text_memory_count": self.text_memory_count,
            "migration_source_content_sha256": (
                self.migration_source_content_sha256
            ),
            "migration_contract_sha256": self.migration_contract_sha256,
            "text_memory_baseline": [
                asdict(record) for record in self.text_memory_baseline
            ],
            "text_memory_baseline_sha256": self.text_memory_baseline_sha256,
            "items": [item.to_dict() for item in self.items],
            "phase_a_create_target_ids": list(self.phase_a_create_target_ids),
            "phase_a_resume_target_ids": list(self.phase_a_resume_target_ids),
            "phase_a_rollback_candidate_ids": list(
                self.phase_a_rollback_candidate_ids
            ),
            "proposed_legacy_delete_ids": list(
                self.proposed_legacy_delete_ids
            ),
            "expected_phase_a_store_count": self.expected_phase_a_store_count,
            "expected_final_store_count": self.expected_final_store_count,
            "expected_transition_duplicate_group_count": (
                self.expected_transition_duplicate_group_count
            ),
            "expected_transition_duplicate_storage_record_count": (
                self.expected_transition_duplicate_storage_record_count
            ),
            "expected_legacy_id_mismatch_count": (
                self.expected_legacy_id_mismatch_count
            ),
            "issues": [asdict(issue) for issue in self.issues],
            "executable": self.executable,
        }


@dataclass(frozen=True)
class ObservedToolRecordEvidence:
    storage_id: str
    classification: str
    derived_record_id: str
    memory_content_sha256: str
    document_sha256: str
    metadata_sha256: str
    compatibility_metadata_sha256: str


@dataclass(frozen=True)
class ObservedTextMemoryEvidence:
    storage_id: str
    document_sha256: str
    metadata_sha256: str


@dataclass(frozen=True)
class StoreStateSnapshot:
    migration_contract_sha256: str
    source_inventory_sha256: str
    formal_source_before_sha256: str
    verified_backup_sha256: str
    tool_records: tuple[ObservedToolRecordEvidence, ...]
    text_memories: tuple[ObservedTextMemoryEvidence, ...]


@dataclass(frozen=True)
class PhaseAExecutionSnapshot:
    migration_contract_sha256: str
    attempted_create_target_ids: tuple[str, ...]
    created_target_ids: tuple[str, ...]
    resumed_target_ids: tuple[str, ...]
    failed_target_ids: tuple[str, ...]
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class PhaseAStoreVerificationResult:
    valid: bool
    issues: tuple[MigrationContractIssue, ...]
    logical_store_state_sha256: str
    phase_a_verification_sha256: str
    controlled_count: int
    legacy_count: int
    text_memory_count: int
    expected_transition_duplicate_group_count: int
    unexpected_duplicate_group_count: int
    legacy_id_mismatch_count: int


@dataclass(frozen=True)
class PhaseBApprovalSnapshot:
    approved: bool
    migration_contract_sha256: str
    phase_a_verification_sha256: str
    proposed_legacy_delete_ids_sha256: str


@dataclass(frozen=True)
class PhaseBExecutionSnapshot:
    migration_contract_sha256: str
    phase_a_verification_sha256: str
    phase_b_approval_sha256: str
    predelete_verification_sha256: str
    attempted_delete_ids: tuple[str, ...]
    deleted_ids: tuple[str, ...]
    failed_delete_ids: tuple[str, ...]
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class MigrationEvaluation:
    state: MigrationState
    issues: tuple[MigrationContractIssue, ...]
    migration_evaluation_sha256: str
    phase_a_executable_create_ids: tuple[str, ...]
    phase_a_executable_rollback_ids: tuple[str, ...]
    phase_b_executable_delete_ids: tuple[str, ...]
    phase_a_verification: PhaseAStoreVerificationResult | None
    predelete_verification: PhaseAStoreVerificationResult | None
    post_b_verification: PhaseAStoreVerificationResult | None
    phase_b_approval_sha256: str
    phase_a_execution_sha256: str
    phase_b_execution_sha256: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def _issue(
    code: str,
    message: str,
    *,
    legacy_storage_id: str = "",
    target_record_id: str = "",
) -> MigrationContractIssue:
    return MigrationContractIssue(
        code=code,
        legacy_storage_id=legacy_storage_id,
        target_record_id=target_record_id,
        message=message,
    )


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_expected_tables(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_is_nonempty_string(item) for item in value)
    )


def _compatibility_field_lists(
    metadata: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    missing = tuple(
        field for field in CHECKED_COMPATIBILITY_FIELDS if field not in metadata
    )
    invalid = tuple(
        field
        for field in CHECKED_COMPATIBILITY_FIELDS
        if field in metadata
        and not (
            _valid_expected_tables(metadata[field])
            if field == "expected_tables"
            else _is_nonempty_string(metadata[field])
        )
    )
    return missing, invalid


def analyze_legacy_metadata_coverage(
    snapshots: Sequence[LegacyToolRecordSnapshot],
) -> dict[str, Any]:
    coverage = {
        field: {
            "present_count": 0,
            "type_valid_count": 0,
            "nonempty_valid_count": 0,
            "total": len(snapshots),
        }
        for field in CHECKED_COMPATIBILITY_FIELDS
    }
    for snapshot in snapshots:
        for field in CHECKED_COMPATIBILITY_FIELDS:
            if field not in snapshot.compatibility_metadata:
                continue
            value = snapshot.compatibility_metadata[field]
            coverage[field]["present_count"] += 1
            type_valid = (
                isinstance(value, list)
                if field == "expected_tables"
                else isinstance(value, str)
            )
            if type_valid:
                coverage[field]["type_valid_count"] += 1
            valid = (
                _valid_expected_tables(value)
                if field == "expected_tables"
                else _is_nonempty_string(value)
            )
            if valid:
                coverage[field]["nonempty_valid_count"] += 1
    return {"legacy_record_count": len(snapshots), "fields": coverage}


def _sorted_unique_ids(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _ids_exact(values: Sequence[str], expected: Sequence[str]) -> bool:
    return (
        all(isinstance(value, str) for value in values)
        and len(values) == len(set(values))
        and tuple(sorted(values)) == tuple(sorted(expected))
    )


def _migration_item_sha256(
    *,
    migration_batch_id: str,
    migration_sample_id: str,
    legacy_storage_id: str,
    target_record_id: str,
    memory_content_sha256: str,
    legacy_document_sha256: str,
    legacy_metadata_sha256: str,
) -> str:
    return _sha256_json(
        {
            "migration_schema_version": MIGRATION_SCHEMA_VERSION,
            "migration_batch_id": migration_batch_id,
            "migration_sample_id": migration_sample_id,
            "legacy_storage_id": legacy_storage_id,
            "target_record_id": target_record_id,
            "memory_content_sha256": memory_content_sha256,
            "legacy_document_sha256": legacy_document_sha256,
            "legacy_metadata_sha256": legacy_metadata_sha256,
        }
    )


def _snapshot_issues(
    snapshot: LegacyToolRecordSnapshot,
) -> tuple[MigrationContractIssue, ...]:
    issues: list[MigrationContractIssue] = []
    identity = build_memory_identity_from_canonical_content(
        snapshot.canonical_content
    )
    common = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "target_record_id": snapshot.target_record_id,
    }
    if not _is_nonempty_string(snapshot.legacy_storage_id):
        issues.append(_issue("INVALID_LEGACY_STORAGE_ID", "旧存储 ID 为空", **common))
    if identity.record_id != snapshot.target_record_id:
        issues.append(_issue("TARGET_RECORD_ID_MISMATCH", "target ID 重算不一致", **common))
    if identity.memory_content_sha256 != snapshot.memory_content_sha256:
        issues.append(_issue("TARGET_CONTENT_DIGEST_MISMATCH", "内容摘要重算不一致", **common))
    if not isinstance(snapshot.document, str):
        issues.append(_issue("INVALID_LEGACY_DOCUMENT", "旧 document 必须是字符串", **common))
    required_raw = ("question", "tool_name", "args_json", "success", "metadata_json")
    if any(field not in snapshot.raw_metadata for field in required_raw):
        issues.append(_issue("INVALID_LEGACY_RAW_METADATA", "旧 metadata 字段不完整", **common))
    conflicts = sorted(
        field
        for field in MIGRATION_COMPATIBILITY_FIELDS
        if field in snapshot.compatibility_metadata
    )
    if conflicts:
        issues.append(
            _issue(
                "MIGRATION_METADATA_FIELD_CONFLICT",
                "旧 compatibility metadata 占用迁移治理字段：" + ",".join(conflicts),
                **common,
            )
        )
    for field in REQUIRED_COMPATIBILITY_FIELDS:
        value = snapshot.compatibility_metadata.get(field)
        valid = _valid_expected_tables(value) if field == "expected_tables" else _is_nonempty_string(value)
        if not valid:
            issues.append(_issue("REQUIRED_COMPATIBILITY_FIELD_INVALID", f"迁移阻断字段无效：{field}", **common))
    for field in ENHANCER_REQUIRED_FIELDS:
        if not _is_nonempty_string(snapshot.compatibility_metadata.get(field)):
            issues.append(_issue("ENHANCER_REQUIRED_FIELD_INVALID", f"增强器依赖字段无效：{field}", **common))
    return tuple(issues)


def _text_baseline_issues(
    records: Sequence[TextMemoryBaselineRecord], expected_count: int
) -> tuple[MigrationContractIssue, ...]:
    issues: list[MigrationContractIssue] = []
    if len(records) != expected_count:
        issues.append(_issue("TEXT_MEMORY_COUNT_MISMATCH", "Text Memory 基线数量不匹配"))
    ids = [record.storage_id for record in records]
    if len(ids) != len(set(ids)) or any(not _is_nonempty_string(value) for value in ids):
        issues.append(_issue("TEXT_MEMORY_ID_INVALID", "Text Memory storage ID 必须非空且唯一"))
    if any(not _valid_sha256(record.document_sha256) or not _valid_sha256(record.metadata_sha256) for record in records):
        issues.append(_issue("TEXT_MEMORY_DIGEST_INVALID", "Text Memory 基线摘要格式无效"))
    return tuple(issues)


def _target_metadata(
    snapshot: LegacyToolRecordSnapshot,
    *,
    migration_batch_id: str,
    migration_sample_id: str,
    migration_item_sha256: str,
    legacy_document_sha256: str,
    legacy_metadata_sha256: str,
    migration_source_content_sha256: str,
    missing_fields: Sequence[str],
    invalid_fields: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    compatibility = dict(snapshot.compatibility_metadata)
    additions = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "legacy_document_sha256": legacy_document_sha256,
        "legacy_metadata_sha256": legacy_metadata_sha256,
        "migration_batch_id": migration_batch_id,
        "migration_source_content_sha256": migration_source_content_sha256,
        "migration_item_sha256": migration_item_sha256,
        "memory_content_sha256": snapshot.memory_content_sha256,
        "legacy_missing_fields": list(missing_fields),
        "legacy_invalid_fields": list(invalid_fields),
        "training_batch_id": migration_batch_id,
        "batch_content_sha256": migration_source_content_sha256,
    }
    for field, value in additions.items():
        if field not in compatibility:
            compatibility[field] = value

    canonical = snapshot.canonical_content
    created_from_sample_id = snapshot.compatibility_metadata.get("sample_id", "")
    top_level = {
        "question": canonical.get("question", ""),
        "tool_name": canonical.get("tool_name", ""),
        "args_json": _canonical_json(canonical.get("args", {})).decode("utf-8"),
        "success": canonical.get("success") is True,
        "metadata_json": _canonical_json(compatibility).decode("utf-8"),
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "memory_kind": MEMORY_KIND,
        "memory_content_sha256": snapshot.memory_content_sha256,
        "created_by_training_batch_id": migration_batch_id,
        "created_by_batch_content_sha256": migration_source_content_sha256,
        "created_from_sample_id": created_from_sample_id,
        "training_level": snapshot.compatibility_metadata.get("training_level", ""),
        "train_decision": snapshot.compatibility_metadata.get("train_decision", ""),
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migrated_from_legacy_storage_id": snapshot.legacy_storage_id,
        "legacy_document_sha256": legacy_document_sha256,
        "legacy_metadata_sha256": legacy_metadata_sha256,
        "migration_source_content_sha256": migration_source_content_sha256,
        "migration_item_sha256": migration_item_sha256,
        "migration_sample_id": migration_sample_id,
    }
    return top_level, compatibility


def _existing_target_action(
    existing: ExistingMigrationTargetSnapshot | None,
    snapshot: LegacyToolRecordSnapshot,
    desired_top_level: Mapping[str, Any],
    desired_compatibility: Mapping[str, Any],
) -> tuple[PhaseAAction, tuple[MigrationContractIssue, ...]]:
    if existing is None:
        return "create_target", ()
    common = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "target_record_id": snapshot.target_record_id,
    }
    identity_equal = (
        existing.record_id == snapshot.target_record_id
        and existing.memory_content_sha256 == snapshot.memory_content_sha256
        and _canonical_json(existing.canonical_content) == _canonical_json(snapshot.canonical_content)
    )
    if not identity_equal:
        return "block_content_conflict", (_issue("TARGET_PREEXISTING_CONTENT_CONFLICT", "已存在 target 内容身份冲突", **common),)
    if (
        _canonical_json(existing.top_level_metadata) == _canonical_json(desired_top_level)
        and _canonical_json(existing.compatibility_metadata) == _canonical_json(desired_compatibility)
    ):
        return "resume_target_created", ()
    return "block_preexisting_target", (_issue("TARGET_PREEXISTING_OTHER_OWNER", "已存在 target 的迁移归属不同", **common),)


def build_legacy_tool_memory_migration_contract(
    snapshots: Sequence[LegacyToolRecordSnapshot],
    text_memory_baseline: Sequence[TextMemoryBaselineRecord],
    *,
    migration_batch_id: str,
    approved_formal_source_sha256: str,
    approved_verified_backup_sha256: str,
    expected_legacy_count: int = DEFAULT_EXPECTED_LEGACY_COUNT,
    text_memory_count: int = DEFAULT_TEXT_MEMORY_COUNT,
    observed_malformed_count: int = 0,
    observed_unknown_count: int = 0,
    observed_content_address_conflict_count: int = 0,
    existing_targets: Mapping[str, ExistingMigrationTargetSnapshot] | None = None,
) -> LegacyToolMemoryMigrationContract:
    snapshot_list = list(snapshots)
    text_records = tuple(sorted(text_memory_baseline, key=lambda record: record.storage_id))
    global_issues: list[MigrationContractIssue] = list(
        _text_baseline_issues(text_records, text_memory_count)
    )
    if not _is_nonempty_string(migration_batch_id):
        global_issues.append(_issue("INVALID_MIGRATION_BATCH_ID", "migration_batch_id 为空"))
    if approved_formal_source_sha256 != APPROVED_FORMAL_SOURCE_SHA256:
        global_issues.append(_issue("FORMAL_SOURCE_DIGEST_MISMATCH", "正式源摘要不是批准基线"))
    if approved_verified_backup_sha256 != APPROVED_VERIFIED_BACKUP_SHA256:
        global_issues.append(_issue("VERIFIED_BACKUP_DIGEST_MISMATCH", "验证备份摘要不是批准基线"))
    if len(snapshot_list) != expected_legacy_count:
        global_issues.append(_issue("LEGACY_RECORD_COUNT_MISMATCH", "legacy 数量不匹配"))
    if observed_malformed_count:
        global_issues.append(_issue("MALFORMED_RECORDS_PRESENT", "源清点存在损坏记录"))
    if observed_unknown_count:
        global_issues.append(_issue("UNKNOWN_RECORDS_PRESENT", "源清点存在未知记录"))
    if observed_content_address_conflict_count:
        global_issues.append(_issue("CONTENT_ADDRESS_CONFLICTS_PRESENT", "源清点存在内容寻址冲突"))

    legacy_ids = [snapshot.legacy_storage_id for snapshot in snapshot_list]
    target_ids = [snapshot.target_record_id for snapshot in snapshot_list]
    for value in sorted(set(legacy_ids)):
        if legacy_ids.count(value) > 1:
            global_issues.append(_issue("DUPLICATE_LEGACY_STORAGE_ID", "legacy storage ID 重复", legacy_storage_id=value))
    for value in sorted(set(target_ids)):
        if target_ids.count(value) > 1:
            global_issues.append(_issue("DUPLICATE_TARGET_RECORD_ID", "target record ID 重复", target_record_id=value))

    ordered = sorted(snapshot_list, key=lambda snapshot: (snapshot.target_record_id, snapshot.legacy_storage_id))
    rows: list[dict[str, Any]] = []
    for index, snapshot in enumerate(ordered, start=1):
        sample_id = f"{MIGRATION_SAMPLE_PREFIX}{index:03d}"
        legacy_document_sha256 = _sha256_text(snapshot.document)
        legacy_metadata_sha256 = _sha256_json(dict(snapshot.raw_metadata))
        missing_fields, invalid_fields = _compatibility_field_lists(snapshot.compatibility_metadata)
        item_sha = _migration_item_sha256(
            migration_batch_id=migration_batch_id,
            migration_sample_id=sample_id,
            legacy_storage_id=snapshot.legacy_storage_id,
            target_record_id=snapshot.target_record_id,
            memory_content_sha256=snapshot.memory_content_sha256,
            legacy_document_sha256=legacy_document_sha256,
            legacy_metadata_sha256=legacy_metadata_sha256,
        )
        rows.append({
            "snapshot": snapshot,
            "migration_sample_id": sample_id,
            "legacy_document_sha256": legacy_document_sha256,
            "legacy_metadata_sha256": legacy_metadata_sha256,
            "missing_fields": missing_fields,
            "invalid_fields": invalid_fields,
            "migration_item_sha256": item_sha,
            "issues": list(_snapshot_issues(snapshot)),
        })

    text_baseline_material = [asdict(record) for record in text_records]
    text_memory_baseline_sha256 = _sha256_json(text_baseline_material)
    source_material = {
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_batch_id": migration_batch_id,
        "approved_formal_source_sha256": approved_formal_source_sha256,
        "approved_verified_backup_sha256": approved_verified_backup_sha256,
        "expected_legacy_count": expected_legacy_count,
        "text_memory_count": text_memory_count,
        "text_memory_baseline": text_baseline_material,
        "items": [{
            "migration_sample_id": row["migration_sample_id"],
            "legacy_storage_id": row["snapshot"].legacy_storage_id,
            "target_record_id": row["snapshot"].target_record_id,
            "memory_content_sha256": row["snapshot"].memory_content_sha256,
            "legacy_document_sha256": row["legacy_document_sha256"],
            "legacy_metadata_sha256": row["legacy_metadata_sha256"],
            "migration_item_sha256": row["migration_item_sha256"],
            "canonical_content": dict(row["snapshot"].canonical_content),
            "legacy_compatibility_metadata": dict(row["snapshot"].compatibility_metadata),
            "legacy_missing_fields": list(row["missing_fields"]),
        } for row in rows],
    }
    migration_source_content_sha256 = _sha256_json(source_material)

    targets = existing_targets or {}
    items: list[LegacyToolMemoryMigrationItem] = []
    for row in rows:
        snapshot = row["snapshot"]
        top_level, compatibility = _target_metadata(
            snapshot,
            migration_batch_id=migration_batch_id,
            migration_sample_id=row["migration_sample_id"],
            migration_item_sha256=row["migration_item_sha256"],
            legacy_document_sha256=row["legacy_document_sha256"],
            legacy_metadata_sha256=row["legacy_metadata_sha256"],
            migration_source_content_sha256=migration_source_content_sha256,
            missing_fields=row["missing_fields"],
            invalid_fields=row["invalid_fields"],
        )
        action, target_issues = _existing_target_action(
            targets.get(snapshot.target_record_id), snapshot, top_level, compatibility
        )
        item_issues = tuple(row["issues"]) + target_issues
        items.append(LegacyToolMemoryMigrationItem(
            migration_sample_id=row["migration_sample_id"],
            legacy_storage_id=snapshot.legacy_storage_id,
            target_record_id=snapshot.target_record_id,
            memory_content_sha256=snapshot.memory_content_sha256,
            legacy_document_sha256=row["legacy_document_sha256"],
            legacy_metadata_sha256=row["legacy_metadata_sha256"],
            target_document_sha256=_sha256_text(str(top_level["question"])),
            target_top_level_metadata_sha256=_sha256_json(top_level),
            target_compatibility_metadata_sha256=_sha256_json(compatibility),
            migration_item_sha256=row["migration_item_sha256"],
            target_governance_metadata=top_level,
            target_compatibility_metadata=compatibility,
            phase_a_action=action,
            issues=item_issues,
            executable=not item_issues,
        ))

    all_issues = tuple(sorted(
        global_issues + [issue for item in items for issue in item.issues],
        key=lambda issue: (issue.legacy_storage_id, issue.target_record_id, issue.code),
    ))
    create_ids = tuple(sorted(item.target_record_id for item in items if item.phase_a_action == "create_target"))
    resume_ids = tuple(sorted(item.target_record_id for item in items if item.phase_a_action == "resume_target_created"))
    proposed_deletes = tuple(sorted(legacy_ids))
    expected_phase_a_store_count = expected_legacy_count * 2 + text_memory_count
    expected_final_store_count = expected_legacy_count + text_memory_count
    contract_material = {
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_source_content_sha256": migration_source_content_sha256,
        "items": [item.to_dict() for item in items],
        "text_memory_baseline": text_baseline_material,
        "text_memory_baseline_sha256": text_memory_baseline_sha256,
        "phase_a_create_target_ids": list(create_ids),
        "phase_a_resume_target_ids": list(resume_ids),
        "phase_a_rollback_candidate_ids": list(create_ids),
        "proposed_legacy_delete_ids": list(proposed_deletes),
        "expected_phase_a_store_count": expected_phase_a_store_count,
        "expected_final_store_count": expected_final_store_count,
        "expected_transition_duplicate_group_count": expected_legacy_count,
        "expected_transition_duplicate_storage_record_count": expected_legacy_count * 2,
        "expected_legacy_id_mismatch_count": expected_legacy_count,
        "issues": [asdict(issue) for issue in all_issues],
        "executable": not all_issues,
    }
    migration_contract_sha256 = _sha256_json(contract_material)
    return LegacyToolMemoryMigrationContract(
        migration_schema_version=MIGRATION_SCHEMA_VERSION,
        migration_batch_id=migration_batch_id,
        approved_formal_source_sha256=approved_formal_source_sha256,
        approved_verified_backup_sha256=approved_verified_backup_sha256,
        expected_legacy_count=expected_legacy_count,
        text_memory_count=text_memory_count,
        migration_source_content_sha256=migration_source_content_sha256,
        migration_contract_sha256=migration_contract_sha256,
        text_memory_baseline=text_records,
        text_memory_baseline_sha256=text_memory_baseline_sha256,
        items=tuple(items),
        phase_a_create_target_ids=create_ids,
        phase_a_resume_target_ids=resume_ids,
        phase_a_rollback_candidate_ids=create_ids,
        proposed_legacy_delete_ids=proposed_deletes,
        expected_phase_a_store_count=expected_phase_a_store_count,
        expected_final_store_count=expected_final_store_count,
        expected_transition_duplicate_group_count=expected_legacy_count,
        expected_transition_duplicate_storage_record_count=expected_legacy_count * 2,
        expected_legacy_id_mismatch_count=expected_legacy_count,
        issues=all_issues,
        executable=not all_issues,
    )


def proposed_legacy_delete_ids_sha256(
    contract: LegacyToolMemoryMigrationContract,
) -> str:
    return _sha256_json(list(sorted(contract.proposed_legacy_delete_ids)))


def _execution_digest(execution: PhaseAExecutionSnapshot) -> str:
    material = asdict(execution)
    for field in ("attempted_create_target_ids", "created_target_ids", "resumed_target_ids", "failed_target_ids", "error_codes"):
        material[field] = sorted(material[field])
    return _sha256_json(material)


def _store_snapshot_material(snapshot: StoreStateSnapshot) -> dict[str, Any]:
    return {
        "migration_contract_sha256": snapshot.migration_contract_sha256,
        "source_inventory_sha256": snapshot.source_inventory_sha256,
        "formal_source_before_sha256": snapshot.formal_source_before_sha256,
        "verified_backup_sha256": snapshot.verified_backup_sha256,
        "tool_records": [asdict(record) for record in sorted(snapshot.tool_records, key=lambda record: record.storage_id)],
        "text_memories": [asdict(record) for record in sorted(snapshot.text_memories, key=lambda record: record.storage_id)],
    }


def logical_store_state_sha256(snapshot: StoreStateSnapshot) -> str:
    return _sha256_json({
        "tool_records": [asdict(record) for record in sorted(snapshot.tool_records, key=lambda record: record.storage_id)],
        "text_memories": [asdict(record) for record in sorted(snapshot.text_memories, key=lambda record: record.storage_id)],
    })


def _base_store_issues(
    contract: LegacyToolMemoryMigrationContract,
    snapshot: StoreStateSnapshot,
) -> list[MigrationContractIssue]:
    issues: list[MigrationContractIssue] = []
    if snapshot.migration_contract_sha256 != contract.migration_contract_sha256:
        issues.append(_issue("STORE_CONTRACT_DIGEST_MISMATCH", "存储快照未绑定当前迁移契约"))
    if snapshot.formal_source_before_sha256 != contract.approved_formal_source_sha256:
        issues.append(_issue("STORE_FORMAL_SOURCE_MISMATCH", "存储快照正式源摘要不匹配"))
    if snapshot.verified_backup_sha256 != contract.approved_verified_backup_sha256:
        issues.append(_issue("STORE_VERIFIED_BACKUP_MISMATCH", "存储快照验证备份摘要不匹配"))
    if not _valid_sha256(snapshot.source_inventory_sha256):
        issues.append(_issue("STORE_INVENTORY_SHA256_INVALID", "底层清点摘要格式无效"))
    tool_ids = [record.storage_id for record in snapshot.tool_records]
    text_ids = [record.storage_id for record in snapshot.text_memories]
    if len(tool_ids) != len(set(tool_ids)) or len(text_ids) != len(set(text_ids)):
        issues.append(_issue("STORE_DUPLICATE_STORAGE_ID", "证据行 storage ID 重复"))
    if any(record.classification not in ALLOWED_TOOL_CLASSIFICATIONS for record in snapshot.tool_records):
        issues.append(_issue("STORE_CLASSIFICATION_INVALID", "Tool Memory 分类值无效"))
    evidence_hashes = [
        value
        for record in snapshot.tool_records
        for value in (
            record.memory_content_sha256,
            record.document_sha256,
            record.metadata_sha256,
            record.compatibility_metadata_sha256,
        )
    ] + [
        value
        for record in snapshot.text_memories
        for value in (record.document_sha256, record.metadata_sha256)
    ]
    if any(not _valid_sha256(value) for value in evidence_hashes):
        issues.append(_issue("STORE_EVIDENCE_SHA256_INVALID", "逐条证据摘要格式无效"))
    return issues


def verify_phase_a_store_state(
    contract: LegacyToolMemoryMigrationContract,
    execution: PhaseAExecutionSnapshot,
    snapshot: StoreStateSnapshot,
    *,
    purpose: Literal["phase_a", "predelete"] = "phase_a",
) -> PhaseAStoreVerificationResult:
    issues = _base_store_issues(contract, snapshot)
    by_storage = {record.storage_id: record for record in snapshot.tool_records}
    controlled_count = sum(record.classification == "controlled_tool_record" for record in snapshot.tool_records)
    legacy_count = sum(record.classification == "legacy_tool_record" for record in snapshot.tool_records)
    malformed_count = sum(record.classification == "malformed_record" for record in snapshot.tool_records)
    unknown_count = sum(record.classification == "unknown_record" for record in snapshot.tool_records)
    if (
        controlled_count != contract.expected_legacy_count
        or legacy_count != contract.expected_legacy_count
        or len(snapshot.text_memories) != contract.text_memory_count
        or len(snapshot.tool_records) + len(snapshot.text_memories) != contract.expected_phase_a_store_count
    ):
        issues.append(_issue("PHASE_A_STORE_COUNT_MISMATCH", "阶段 A 存储数量不变量不满足"))
    if malformed_count or unknown_count:
        issues.append(_issue("PHASE_A_INVALID_RECORDS_PRESENT", "阶段 A 存在损坏或未知记录"))

    expected_groups = 0
    unexpected_groups = 0
    grouped: dict[str, list[ObservedToolRecordEvidence]] = {}
    for record in snapshot.tool_records:
        grouped.setdefault(record.derived_record_id, []).append(record)
    expected_pairs = {
        item.target_record_id: {item.legacy_storage_id, item.target_record_id}
        for item in contract.items
    }
    for derived_id, records in grouped.items():
        if len(records) <= 1:
            continue
        storage_ids = {record.storage_id for record in records}
        classifications = {record.classification for record in records}
        if (
            derived_id in expected_pairs
            and len(records) == 2
            and storage_ids == expected_pairs[derived_id]
            and classifications == {"legacy_tool_record", "controlled_tool_record"}
        ):
            expected_groups += 1
        else:
            unexpected_groups += 1
    if expected_groups != contract.expected_transition_duplicate_group_count:
        issues.append(_issue("PHASE_A_TRANSITION_DUPLICATE_GROUP_MISMATCH", "预期过渡重复组不完整"))
    if unexpected_groups:
        issues.append(_issue("PHASE_A_UNEXPECTED_DUPLICATE_GROUPS", "存在意外重复内容组"))

    legacy_mismatches = {
        record.storage_id
        for record in snapshot.tool_records
        if record.classification == "legacy_tool_record" and record.storage_id != record.derived_record_id
    }
    if legacy_mismatches != set(contract.proposed_legacy_delete_ids):
        issues.append(_issue("PHASE_A_LEGACY_MISMATCH_SET_INVALID", "legacy ID mismatch 集合不精确"))

    for item in contract.items:
        target = by_storage.get(item.target_record_id)
        if target is None or not (
            target.classification == "controlled_tool_record"
            and target.derived_record_id == item.target_record_id
            and target.memory_content_sha256 == item.memory_content_sha256
            and target.document_sha256 == item.target_document_sha256
            and target.metadata_sha256 == item.target_top_level_metadata_sha256
            and target.compatibility_metadata_sha256 == item.target_compatibility_metadata_sha256
        ):
            issues.append(_issue("PHASE_A_TARGET_EVIDENCE_MISMATCH", "controlled target 逐条证据不匹配", target_record_id=item.target_record_id))
        legacy = by_storage.get(item.legacy_storage_id)
        if legacy is None or not (
            legacy.classification == "legacy_tool_record"
            and legacy.derived_record_id == item.target_record_id
            and legacy.memory_content_sha256 == item.memory_content_sha256
            and legacy.document_sha256 == item.legacy_document_sha256
            and legacy.metadata_sha256 == item.legacy_metadata_sha256
        ):
            issues.append(_issue("PHASE_A_LEGACY_EVIDENCE_MISMATCH", "legacy 逐条保留证据不匹配", legacy_storage_id=item.legacy_storage_id))

    observed_text = tuple(sorted((record.storage_id, record.document_sha256, record.metadata_sha256) for record in snapshot.text_memories))
    expected_text = tuple(sorted((record.storage_id, record.document_sha256, record.metadata_sha256) for record in contract.text_memory_baseline))
    if observed_text != expected_text:
        issues.append(_issue("TEXT_MEMORY_BASELINE_MISMATCH", "Text Memory 逐条证据发生变化"))

    logical_sha = logical_store_state_sha256(snapshot)
    normalized_issues = tuple(sorted(issues, key=lambda issue: (issue.legacy_storage_id, issue.target_record_id, issue.code)))
    verification_sha = _sha256_json({
        "purpose": purpose,
        "migration_contract_sha256": contract.migration_contract_sha256,
        "phase_a_execution_sha256": _execution_digest(execution),
        "store_state_snapshot": _store_snapshot_material(snapshot),
        "logical_store_state_sha256": logical_sha,
        "valid": not normalized_issues,
        "issue_codes": [issue.code for issue in normalized_issues],
        "controlled_count": controlled_count,
        "legacy_count": legacy_count,
        "text_memory_count": len(snapshot.text_memories),
        "expected_transition_duplicate_group_count": expected_groups,
        "unexpected_duplicate_group_count": unexpected_groups,
        "legacy_id_mismatch_count": len(legacy_mismatches),
    })
    return PhaseAStoreVerificationResult(
        valid=not normalized_issues,
        issues=normalized_issues,
        logical_store_state_sha256=logical_sha,
        phase_a_verification_sha256=verification_sha,
        controlled_count=controlled_count,
        legacy_count=legacy_count,
        text_memory_count=len(snapshot.text_memories),
        expected_transition_duplicate_group_count=expected_groups,
        unexpected_duplicate_group_count=unexpected_groups,
        legacy_id_mismatch_count=len(legacy_mismatches),
    )


def verify_post_b_store_state(
    contract: LegacyToolMemoryMigrationContract,
    execution: PhaseBExecutionSnapshot,
    snapshot: StoreStateSnapshot,
) -> PhaseAStoreVerificationResult:
    issues = _base_store_issues(contract, snapshot)
    by_storage = {record.storage_id: record for record in snapshot.tool_records}
    controlled_count = sum(record.classification == "controlled_tool_record" for record in snapshot.tool_records)
    legacy_count = sum(record.classification == "legacy_tool_record" for record in snapshot.tool_records)
    malformed_count = sum(record.classification == "malformed_record" for record in snapshot.tool_records)
    unknown_count = sum(record.classification == "unknown_record" for record in snapshot.tool_records)
    grouped: dict[str, int] = {}
    for record in snapshot.tool_records:
        grouped[record.derived_record_id] = grouped.get(record.derived_record_id, 0) + 1
    duplicate_groups = sum(count > 1 for count in grouped.values())
    legacy_mismatches = sum(
        record.classification == "legacy_tool_record" and record.storage_id != record.derived_record_id
        for record in snapshot.tool_records
    )
    if (
        controlled_count != contract.expected_legacy_count
        or legacy_count != 0
        or len(snapshot.text_memories) != contract.text_memory_count
        or len(snapshot.tool_records) + len(snapshot.text_memories) != contract.expected_final_store_count
    ):
        issues.append(_issue("POST_B_STORE_COUNT_MISMATCH", "阶段 B 后存储数量不变量不满足"))
    if malformed_count or unknown_count:
        issues.append(_issue("POST_B_INVALID_RECORDS_PRESENT", "阶段 B 后存在损坏或未知记录"))
    if duplicate_groups or legacy_mismatches:
        issues.append(_issue("POST_B_DUPLICATE_OR_LEGACY_PRESENT", "阶段 B 后仍有重复组或 legacy mismatch"))
    for item in contract.items:
        target = by_storage.get(item.target_record_id)
        if target is None or not (
            target.classification == "controlled_tool_record"
            and target.derived_record_id == item.target_record_id
            and target.memory_content_sha256 == item.memory_content_sha256
            and target.document_sha256 == item.target_document_sha256
            and target.metadata_sha256 == item.target_top_level_metadata_sha256
            and target.compatibility_metadata_sha256 == item.target_compatibility_metadata_sha256
        ):
            issues.append(_issue("POST_B_TARGET_EVIDENCE_MISMATCH", "最终 target 逐条证据不匹配", target_record_id=item.target_record_id))
        if item.legacy_storage_id in by_storage:
            issues.append(_issue("POST_B_LEGACY_STILL_PRESENT", "最终状态仍存在 legacy UUID", legacy_storage_id=item.legacy_storage_id))
    observed_text = tuple(sorted((record.storage_id, record.document_sha256, record.metadata_sha256) for record in snapshot.text_memories))
    expected_text = tuple(sorted((record.storage_id, record.document_sha256, record.metadata_sha256) for record in contract.text_memory_baseline))
    if observed_text != expected_text:
        issues.append(_issue("TEXT_MEMORY_BASELINE_MISMATCH", "最终 Text Memory 逐条证据发生变化"))
    logical_sha = logical_store_state_sha256(snapshot)
    normalized_issues = tuple(sorted(issues, key=lambda issue: (issue.legacy_storage_id, issue.target_record_id, issue.code)))
    verification_sha = _sha256_json({
        "purpose": "post_b",
        "migration_contract_sha256": contract.migration_contract_sha256,
        "phase_b_execution": asdict(execution),
        "store_state_snapshot": _store_snapshot_material(snapshot),
        "logical_store_state_sha256": logical_sha,
        "valid": not normalized_issues,
        "issue_codes": [issue.code for issue in normalized_issues],
        "controlled_count": controlled_count,
        "legacy_count": legacy_count,
        "text_memory_count": len(snapshot.text_memories),
        "duplicate_group_count": duplicate_groups,
        "legacy_id_mismatch_count": legacy_mismatches,
    })
    return PhaseAStoreVerificationResult(
        valid=not normalized_issues,
        issues=normalized_issues,
        logical_store_state_sha256=logical_sha,
        phase_a_verification_sha256=verification_sha,
        controlled_count=controlled_count,
        legacy_count=legacy_count,
        text_memory_count=len(snapshot.text_memories),
        expected_transition_duplicate_group_count=0,
        unexpected_duplicate_group_count=duplicate_groups,
        legacy_id_mismatch_count=legacy_mismatches,
    )


def _phase_a_execution_issues(
    contract: LegacyToolMemoryMigrationContract,
    execution: PhaseAExecutionSnapshot,
) -> tuple[MigrationContractIssue, ...]:
    issues: list[MigrationContractIssue] = []
    if execution.migration_contract_sha256 != contract.migration_contract_sha256:
        issues.append(_issue("PHASE_A_EXECUTION_CONTRACT_MISMATCH", "阶段 A 执行结果未绑定当前契约"))
    if not _ids_exact(execution.attempted_create_target_ids, contract.phase_a_create_target_ids):
        issues.append(_issue("PHASE_A_ATTEMPTED_CREATE_SET_MISMATCH", "阶段 A 尝试创建集合不精确"))
    if not _ids_exact(execution.resumed_target_ids, contract.phase_a_resume_target_ids):
        issues.append(_issue("PHASE_A_RESUME_SET_MISMATCH", "阶段 A resume 集合不精确"))
    attempted = set(execution.attempted_create_target_ids)
    created = set(execution.created_target_ids)
    failed = set(execution.failed_target_ids)
    if (
        len(execution.created_target_ids) != len(created)
        or len(execution.failed_target_ids) != len(failed)
        or not created.issubset(attempted)
        or not failed.issubset(attempted)
        or created & failed
        or created | failed != attempted
    ):
        issues.append(_issue("PHASE_A_EXECUTION_PARTITION_INVALID", "阶段 A 创建结果不是尝试集合的精确分区"))
    return tuple(issues)


def _approval_sha256(approval: PhaseBApprovalSnapshot) -> str:
    return _sha256_json(asdict(approval))


def _phase_b_execution_digest(execution: PhaseBExecutionSnapshot) -> str:
    material = asdict(execution)
    for field in (
        "attempted_delete_ids",
        "deleted_ids",
        "failed_delete_ids",
        "error_codes",
    ):
        material[field] = sorted(material[field])
    return _sha256_json(material)


def _approval_issues(
    contract: LegacyToolMemoryMigrationContract,
    verification: PhaseAStoreVerificationResult,
    approval: PhaseBApprovalSnapshot,
) -> tuple[MigrationContractIssue, ...]:
    issues: list[MigrationContractIssue] = []
    if approval.approved is not True:
        issues.append(_issue("PHASE_B_NOT_APPROVED", "阶段 B 未获批准"))
    if approval.migration_contract_sha256 != contract.migration_contract_sha256:
        issues.append(_issue("PHASE_B_APPROVAL_CONTRACT_MISMATCH", "批准未绑定当前契约"))
    if approval.phase_a_verification_sha256 != verification.phase_a_verification_sha256:
        issues.append(_issue("PHASE_B_APPROVAL_VERIFICATION_MISMATCH", "批准未绑定阶段 A 验证"))
    if approval.proposed_legacy_delete_ids_sha256 != proposed_legacy_delete_ids_sha256(contract):
        issues.append(_issue("PHASE_B_APPROVAL_DELETE_SET_MISMATCH", "批准未绑定精确删除集合"))
    return tuple(issues)


def _phase_b_execution_issues(
    contract: LegacyToolMemoryMigrationContract,
    phase_a_verification: PhaseAStoreVerificationResult,
    approval_sha256: str,
    predelete_verification: PhaseAStoreVerificationResult,
    execution: PhaseBExecutionSnapshot,
) -> tuple[MigrationContractIssue, ...]:
    issues: list[MigrationContractIssue] = []
    if execution.migration_contract_sha256 != contract.migration_contract_sha256:
        issues.append(_issue("PHASE_B_EXECUTION_CONTRACT_MISMATCH", "阶段 B 执行未绑定当前契约"))
    if execution.phase_a_verification_sha256 != phase_a_verification.phase_a_verification_sha256:
        issues.append(_issue("PHASE_B_EXECUTION_PHASE_A_MISMATCH", "阶段 B 执行未绑定阶段 A 验证"))
    if execution.phase_b_approval_sha256 != approval_sha256:
        issues.append(_issue("PHASE_B_EXECUTION_APPROVAL_MISMATCH", "阶段 B 执行未绑定批准"))
    if execution.predelete_verification_sha256 != predelete_verification.phase_a_verification_sha256:
        issues.append(_issue("PHASE_B_EXECUTION_PREDELETE_MISMATCH", "阶段 B 执行未绑定删除前重验"))
    if not _ids_exact(execution.attempted_delete_ids, contract.proposed_legacy_delete_ids):
        issues.append(_issue("PHASE_B_ATTEMPTED_DELETE_SET_MISMATCH", "阶段 B 尝试删除集合不精确"))
    attempted = set(execution.attempted_delete_ids)
    deleted = set(execution.deleted_ids)
    failed = set(execution.failed_delete_ids)
    if (
        len(execution.deleted_ids) != len(deleted)
        or len(execution.failed_delete_ids) != len(failed)
        or deleted & failed
        or deleted | failed != attempted
    ):
        issues.append(_issue("PHASE_B_EXECUTION_PARTITION_INVALID", "阶段 B 删除结果不是尝试集合的精确分区"))
    return tuple(issues)


def _illegal_evidence_order(
    contract: LegacyToolMemoryMigrationContract,
    phase_a_execution: PhaseAExecutionSnapshot | None,
    phase_a_store_state: StoreStateSnapshot | None,
    phase_b_approval: PhaseBApprovalSnapshot | None,
    predelete_store_state: StoreStateSnapshot | None,
    phase_b_execution: PhaseBExecutionSnapshot | None,
    final_store_state: StoreStateSnapshot | None,
) -> bool:
    any_evidence = any(value is not None for value in (phase_a_execution, phase_a_store_state, phase_b_approval, predelete_store_state, phase_b_execution, final_store_state))
    if not contract.executable and any_evidence:
        return True
    if phase_a_store_state is not None and phase_a_execution is None:
        return True
    if phase_b_approval is not None and phase_a_store_state is None:
        return True
    if predelete_store_state is not None and phase_b_approval is None:
        return True
    if phase_b_execution is not None and predelete_store_state is None:
        return True
    if final_store_state is not None and phase_b_execution is None:
        return True
    if final_store_state is not None and phase_b_execution is not None and phase_b_execution.failed_delete_ids:
        return True
    return False


def _evaluation(
    contract: LegacyToolMemoryMigrationContract,
    *,
    state: MigrationState,
    issues: Sequence[MigrationContractIssue] = (),
    create_ids: Sequence[str] = (),
    rollback_ids: Sequence[str] = (),
    delete_ids: Sequence[str] = (),
    phase_a_verification: PhaseAStoreVerificationResult | None = None,
    predelete_verification: PhaseAStoreVerificationResult | None = None,
    post_b_verification: PhaseAStoreVerificationResult | None = None,
    approval_sha256: str = "",
    phase_a_execution_sha256: str = "",
    phase_b_execution_sha256: str = "",
) -> MigrationEvaluation:
    normalized_issues = tuple(sorted(issues, key=lambda issue: (issue.legacy_storage_id, issue.target_record_id, issue.code)))
    material = {
        "migration_contract_sha256": contract.migration_contract_sha256,
        "state": state,
        "issue_codes": [issue.code for issue in normalized_issues],
        "phase_a_executable_create_ids": sorted(create_ids),
        "phase_a_executable_rollback_ids": sorted(rollback_ids),
        "phase_b_executable_delete_ids": sorted(delete_ids),
        "phase_a_verification_sha256": phase_a_verification.phase_a_verification_sha256 if phase_a_verification else "",
        "predelete_verification_sha256": predelete_verification.phase_a_verification_sha256 if predelete_verification else "",
        "post_b_verification_sha256": post_b_verification.phase_a_verification_sha256 if post_b_verification else "",
        "phase_b_approval_sha256": approval_sha256,
        "phase_a_execution_sha256": phase_a_execution_sha256,
        "phase_b_execution_sha256": phase_b_execution_sha256,
    }
    return MigrationEvaluation(
        state=state,
        issues=normalized_issues,
        migration_evaluation_sha256=_sha256_json(material),
        phase_a_executable_create_ids=tuple(sorted(create_ids)),
        phase_a_executable_rollback_ids=tuple(sorted(rollback_ids)),
        phase_b_executable_delete_ids=tuple(sorted(delete_ids)),
        phase_a_verification=phase_a_verification,
        predelete_verification=predelete_verification,
        post_b_verification=post_b_verification,
        phase_b_approval_sha256=approval_sha256,
        phase_a_execution_sha256=phase_a_execution_sha256,
        phase_b_execution_sha256=phase_b_execution_sha256,
    )


def evaluate_legacy_tool_memory_migration(
    contract: LegacyToolMemoryMigrationContract,
    *,
    phase_a_execution: PhaseAExecutionSnapshot | None = None,
    phase_a_store_state: StoreStateSnapshot | None = None,
    phase_b_approval: PhaseBApprovalSnapshot | None = None,
    predelete_store_state: StoreStateSnapshot | None = None,
    phase_b_execution: PhaseBExecutionSnapshot | None = None,
    final_store_state: StoreStateSnapshot | None = None,
) -> MigrationEvaluation:
    phase_a_execution_sha = (
        _execution_digest(phase_a_execution) if phase_a_execution else ""
    )
    phase_b_execution_sha = (
        _phase_b_execution_digest(phase_b_execution) if phase_b_execution else ""
    )

    def finish(**values: Any) -> MigrationEvaluation:
        return _evaluation(
            contract,
            phase_a_execution_sha256=phase_a_execution_sha,
            phase_b_execution_sha256=phase_b_execution_sha,
            **values,
        )

    if _illegal_evidence_order(contract, phase_a_execution, phase_a_store_state, phase_b_approval, predelete_store_state, phase_b_execution, final_store_state):
        return finish(state="PLAN_BLOCKED", issues=(_issue("ILLEGAL_STATE_EVIDENCE_ORDER", "执行或验证证据顺序非法"),))
    if not contract.executable:
        return finish(state="PLAN_BLOCKED", issues=contract.issues)
    if phase_a_execution is None:
        return finish(state="PHASE_A_READY", create_ids=contract.phase_a_create_target_ids)
    execution_issues = _phase_a_execution_issues(contract, phase_a_execution)
    if execution_issues or phase_a_execution.failed_target_ids:
        return finish(state="PHASE_A_ROLLBACK_REQUIRED", issues=execution_issues, rollback_ids=phase_a_execution.created_target_ids)
    if phase_a_store_state is None:
        return finish(state="PHASE_A_EXECUTED_PENDING_VERIFY")
    phase_a_verification = verify_phase_a_store_state(contract, phase_a_execution, phase_a_store_state)
    if not phase_a_verification.valid:
        return finish(state="PHASE_A_VERIFICATION_FAILED", issues=phase_a_verification.issues, phase_a_verification=phase_a_verification)
    if phase_b_approval is None:
        return finish(state="PHASE_A_VERIFIED_AWAITING_APPROVAL", phase_a_verification=phase_a_verification)
    approval_issues = _approval_issues(contract, phase_a_verification, phase_b_approval)
    approval_sha = _approval_sha256(phase_b_approval)
    if approval_issues:
        return finish(state="PHASE_B_APPROVAL_INVALID", issues=approval_issues, phase_a_verification=phase_a_verification, approval_sha256=approval_sha)
    if predelete_store_state is None:
        return finish(state="PHASE_B_REVALIDATION_REQUIRED", phase_a_verification=phase_a_verification, approval_sha256=approval_sha)
    predelete = verify_phase_a_store_state(contract, phase_a_execution, predelete_store_state, purpose="predelete")
    if not predelete.valid or predelete.logical_store_state_sha256 != phase_a_verification.logical_store_state_sha256:
        issues = list(predelete.issues)
        if predelete.logical_store_state_sha256 != phase_a_verification.logical_store_state_sha256:
            issues.append(_issue("PHASE_B_PREDELETE_LOGICAL_STATE_CHANGED", "删除前逻辑存储状态已变化"))
        return finish(state="PHASE_B_PREDELETE_FAILED", issues=issues, phase_a_verification=phase_a_verification, predelete_verification=predelete, approval_sha256=approval_sha)
    if phase_b_execution is None:
        return finish(state="PHASE_B_READY", delete_ids=contract.proposed_legacy_delete_ids, phase_a_verification=phase_a_verification, predelete_verification=predelete, approval_sha256=approval_sha)
    phase_b_issues = _phase_b_execution_issues(contract, phase_a_verification, approval_sha, predelete, phase_b_execution)
    if phase_b_issues or phase_b_execution.failed_delete_ids:
        return finish(state="PHASE_B_RECOVERY_REQUIRED", issues=phase_b_issues, phase_a_verification=phase_a_verification, predelete_verification=predelete, approval_sha256=approval_sha)
    if final_store_state is None:
        return finish(state="PHASE_B_EXECUTED_PENDING_VERIFY", phase_a_verification=phase_a_verification, predelete_verification=predelete, approval_sha256=approval_sha)
    final_verification = verify_post_b_store_state(contract, phase_b_execution, final_store_state)
    if not final_verification.valid:
        return finish(state="POST_B_VERIFICATION_FAILED", issues=final_verification.issues, phase_a_verification=phase_a_verification, predelete_verification=predelete, post_b_verification=final_verification, approval_sha256=approval_sha)
    return finish(state="COMPLETED", phase_a_verification=phase_a_verification, predelete_verification=predelete, post_b_verification=final_verification, approval_sha256=approval_sha)


def build_public_migration_evidence(
    contract: LegacyToolMemoryMigrationContract,
    evaluation: MigrationEvaluation | None = None,
) -> dict[str, Any]:
    items = [{
        "migration_sample_id": item.migration_sample_id,
        "legacy_storage_id": item.legacy_storage_id,
        "target_record_id": item.target_record_id,
        "memory_content_sha256": item.memory_content_sha256,
        "legacy_document_sha256": item.legacy_document_sha256,
        "legacy_metadata_sha256": item.legacy_metadata_sha256,
        "target_document_sha256": item.target_document_sha256,
        "target_top_level_metadata_sha256": item.target_top_level_metadata_sha256,
        "target_compatibility_metadata_sha256": item.target_compatibility_metadata_sha256,
        "missing_fields": list(item.target_compatibility_metadata.get("legacy_missing_fields", [])),
        "invalid_fields": list(item.target_compatibility_metadata.get("legacy_invalid_fields", [])),
        "phase_a_action": item.phase_a_action,
        "issue_codes": sorted(issue.code for issue in item.issues),
    } for item in contract.items]
    result: dict[str, Any] = {
        "migration_schema_version": contract.migration_schema_version,
        "migration_source_content_sha256": contract.migration_source_content_sha256,
        "migration_contract_sha256": contract.migration_contract_sha256,
        "current_state": evaluation.state if evaluation else ("PHASE_A_READY" if contract.executable else "PLAN_BLOCKED"),
        "issue_codes": sorted({issue.code for issue in (evaluation.issues if evaluation else contract.issues)}),
        "items": items,
    }
    if evaluation:
        result.update({
            "migration_evaluation_sha256": evaluation.migration_evaluation_sha256,
            "phase_a_execution_sha256": evaluation.phase_a_execution_sha256,
            "phase_b_execution_sha256": evaluation.phase_b_execution_sha256,
            "phase_a_verification_sha256": evaluation.phase_a_verification.phase_a_verification_sha256 if evaluation.phase_a_verification else "",
            "phase_b_approval_sha256": evaluation.phase_b_approval_sha256,
            "predelete_verification_sha256": evaluation.predelete_verification.phase_a_verification_sha256 if evaluation.predelete_verification else "",
            "post_b_verification_sha256": evaluation.post_b_verification.phase_a_verification_sha256 if evaluation.post_b_verification else "",
            "phase_a_create_count": len(contract.phase_a_create_target_ids),
            "phase_a_resume_count": len(contract.phase_a_resume_target_ids),
            "proposed_delete_count": len(contract.proposed_legacy_delete_ids),
        })
    return result


__all__ = [
    "APPROVED_FORMAL_SOURCE_SHA256",
    "APPROVED_VERIFIED_BACKUP_SHA256",
    "ExistingMigrationTargetSnapshot",
    "LegacyToolMemoryMigrationContract",
    "LegacyToolMemoryMigrationItem",
    "LegacyToolRecordSnapshot",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationContractIssue",
    "MigrationEvaluation",
    "ObservedTextMemoryEvidence",
    "ObservedToolRecordEvidence",
    "PhaseAExecutionSnapshot",
    "PhaseAStoreVerificationResult",
    "PhaseBApprovalSnapshot",
    "PhaseBExecutionSnapshot",
    "StoreStateSnapshot",
    "TextMemoryBaselineRecord",
    "analyze_legacy_metadata_coverage",
    "build_legacy_tool_memory_migration_contract",
    "build_public_migration_evidence",
    "evaluate_legacy_tool_memory_migration",
    "logical_store_state_sha256",
    "proposed_legacy_delete_ids_sha256",
    "verify_phase_a_store_state",
    "verify_post_b_store_state",
]

"""旧 UUID run_sql Tool Memory 的纯逻辑确定性迁移计划。"""

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


MIGRATION_SCHEMA_VERSION = "1.0"
APPROVED_FORMAL_SOURCE_SHA256 = (
    "4fd753c4d4c0d22119b6349856f195fe4ed7e23466120f6786edb979606646d8"
)
APPROVED_VERIFIED_BACKUP_SHA256 = APPROVED_FORMAL_SOURCE_SHA256
DEFAULT_EXPECTED_LEGACY_COUNT = 64
DEFAULT_TEXT_MEMORY_COUNT = 8
MIGRATION_SAMPLE_PREFIX = "LEGACY_TOOL_MIGRATION_"
REQUIRED_COMPATIBILITY_FIELDS = (
    "training_level",
    "train_decision",
    "expected_tables",
)
ENHANCER_REQUIRED_FIELDS = ("sample_id",)
HISTORICAL_OPTIONAL_FIELDS = (
    "sample_id",
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
    "legacy_metadata_sha256",
    "migration_batch_id",
    "migration_plan_content_sha256",
    "migration_item_sha256",
    "memory_content_sha256",
    "legacy_missing_fields",
    "legacy_invalid_fields",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

PhaseAAction = Literal[
    "create_target",
    "resume_target_created",
    "block_preexisting_target",
    "block_content_conflict",
]


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
class PhaseAVerificationSnapshot:
    migration_plan_content_sha256: str
    verified_target_record_ids: tuple[str, ...]
    retained_legacy_storage_ids: tuple[str, ...]
    store_count_after_phase_a: int
    controlled_tool_record_count: int
    legacy_tool_record_count: int
    text_memory_count: int
    malformed_record_count: int
    unknown_record_count: int
    duplicate_content_group_count: int
    content_address_conflict_count: int
    inventory_sha256: str


@dataclass(frozen=True)
class MigrationPlanIssue:
    code: str
    legacy_storage_id: str
    target_record_id: str
    message: str


@dataclass(frozen=True)
class LegacyToolMemoryMigrationItem:
    migration_sample_id: str
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    legacy_metadata_sha256: str
    migration_item_sha256: str
    target_governance_metadata: Mapping[str, Any]
    target_compatibility_metadata: Mapping[str, Any]
    phase_a_action: PhaseAAction
    phase_b_action: str
    issues: tuple[MigrationPlanIssue, ...]
    executable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_sample_id": self.migration_sample_id,
            "legacy_storage_id": self.legacy_storage_id,
            "target_record_id": self.target_record_id,
            "memory_content_sha256": self.memory_content_sha256,
            "legacy_metadata_sha256": self.legacy_metadata_sha256,
            "migration_item_sha256": self.migration_item_sha256,
            "target_governance_metadata": dict(self.target_governance_metadata),
            "target_compatibility_metadata": dict(
                self.target_compatibility_metadata
            ),
            "phase_a_action": self.phase_a_action,
            "phase_b_action": self.phase_b_action,
            "issues": [asdict(issue) for issue in self.issues],
            "executable": self.executable,
        }


@dataclass(frozen=True)
class LegacyToolMemoryMigrationPlan:
    migration_schema_version: str
    migration_batch_id: str
    approved_formal_source_sha256: str
    approved_verified_backup_sha256: str
    expected_legacy_count: int
    expected_target_create_count: int
    expected_phase_a_store_count: int
    expected_final_store_count: int
    text_memory_excluded_count: int
    migration_plan_content_sha256: str
    items: tuple[LegacyToolMemoryMigrationItem, ...]
    phase_a_possible_rollback_ids: tuple[str, ...]
    phase_a_verification_provided: bool
    phase_a_verified: bool
    phase_a_verification_issues: tuple[MigrationPlanIssue, ...]
    phase_b_approved: bool
    proposed_legacy_delete_ids: tuple[str, ...]
    phase_b_executable_delete_ids: tuple[str, ...]
    issues: tuple[MigrationPlanIssue, ...]
    executable: bool
    migration_plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_schema_version": self.migration_schema_version,
            "migration_batch_id": self.migration_batch_id,
            "approved_formal_source_sha256": self.approved_formal_source_sha256,
            "approved_verified_backup_sha256": (
                self.approved_verified_backup_sha256
            ),
            "expected_legacy_count": self.expected_legacy_count,
            "expected_target_create_count": self.expected_target_create_count,
            "expected_phase_a_store_count": self.expected_phase_a_store_count,
            "expected_final_store_count": self.expected_final_store_count,
            "text_memory_excluded_count": self.text_memory_excluded_count,
            "migration_plan_content_sha256": (
                self.migration_plan_content_sha256
            ),
            "items": [item.to_dict() for item in self.items],
            "phase_a_possible_rollback_ids": list(
                self.phase_a_possible_rollback_ids
            ),
            "phase_a_verification_provided": (
                self.phase_a_verification_provided
            ),
            "phase_a_verified": self.phase_a_verified,
            "phase_a_verification_issues": [
                asdict(issue) for issue in self.phase_a_verification_issues
            ],
            "phase_b_approved": self.phase_b_approved,
            "proposed_legacy_delete_ids": list(
                self.proposed_legacy_delete_ids
            ),
            "phase_b_executable_delete_ids": list(
                self.phase_b_executable_delete_ids
            ),
            "issues": [asdict(issue) for issue in self.issues],
            "executable": self.executable,
            "migration_plan_sha256": self.migration_plan_sha256,
        }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _issue(
    code: str,
    *,
    legacy_storage_id: str = "",
    target_record_id: str = "",
    message: str,
) -> MigrationPlanIssue:
    return MigrationPlanIssue(
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


def _legacy_metadata_sha256(snapshot: LegacyToolRecordSnapshot) -> str:
    return _sha256(dict(snapshot.raw_metadata))


def _migration_item_sha256(
    *,
    migration_batch_id: str,
    migration_sample_id: str,
    legacy_storage_id: str,
    target_record_id: str,
    memory_content_sha256: str,
    legacy_metadata_sha256: str,
) -> str:
    return _sha256(
        {
            "migration_schema_version": MIGRATION_SCHEMA_VERSION,
            "migration_batch_id": migration_batch_id,
            "migration_sample_id": migration_sample_id,
            "legacy_storage_id": legacy_storage_id,
            "target_record_id": target_record_id,
            "memory_content_sha256": memory_content_sha256,
            "legacy_metadata_sha256": legacy_metadata_sha256,
        }
    )


def _phase_a_verification_material(
    verification: PhaseAVerificationSnapshot | None,
) -> dict[str, Any] | None:
    if verification is None:
        return None
    return {
        "migration_plan_content_sha256": (
            verification.migration_plan_content_sha256
        ),
        "verified_target_record_ids": sorted(
            verification.verified_target_record_ids
        ),
        "retained_legacy_storage_ids": sorted(
            verification.retained_legacy_storage_ids
        ),
        "store_count_after_phase_a": verification.store_count_after_phase_a,
        "controlled_tool_record_count": (
            verification.controlled_tool_record_count
        ),
        "legacy_tool_record_count": verification.legacy_tool_record_count,
        "text_memory_count": verification.text_memory_count,
        "malformed_record_count": verification.malformed_record_count,
        "unknown_record_count": verification.unknown_record_count,
        "duplicate_content_group_count": (
            verification.duplicate_content_group_count
        ),
        "content_address_conflict_count": (
            verification.content_address_conflict_count
        ),
        "inventory_sha256": verification.inventory_sha256,
    }


def _validate_phase_a_verification(
    plan_content_sha256: str,
    expected_target_ids: Sequence[str],
    expected_legacy_ids: Sequence[str],
    verification: PhaseAVerificationSnapshot | None,
) -> tuple[MigrationPlanIssue, ...]:
    if verification is None:
        return (
            _issue(
                "PHASE_A_VERIFICATION_NOT_PROVIDED",
                message="未提供阶段 A 匿名验证快照",
            ),
        )
    if not isinstance(verification, PhaseAVerificationSnapshot):
        raise TypeError("phase_a_verification 类型无效")

    issues: list[MigrationPlanIssue] = []
    if verification.migration_plan_content_sha256 != plan_content_sha256:
        issues.append(
            _issue(
                "PHASE_A_PLAN_DIGEST_MISMATCH",
                message="阶段 A 验证快照与当前计划摘要不一致",
            )
        )

    expected_targets = tuple(sorted(expected_target_ids))
    verified_targets = verification.verified_target_record_ids
    if (
        any(not isinstance(value, str) for value in verified_targets)
        or len(verified_targets) != len(set(verified_targets))
        or tuple(sorted(verified_targets)) != expected_targets
    ):
        issues.append(
            _issue(
                "PHASE_A_TARGET_SET_MISMATCH",
                message="阶段 A 已验证 target 集合不精确",
            )
        )

    expected_legacy = tuple(sorted(expected_legacy_ids))
    retained_legacy = verification.retained_legacy_storage_ids
    if (
        any(not isinstance(value, str) for value in retained_legacy)
        or len(retained_legacy) != len(set(retained_legacy))
        or tuple(sorted(retained_legacy)) != expected_legacy
    ):
        issues.append(
            _issue(
                "PHASE_A_LEGACY_SET_MISMATCH",
                message="阶段 A 保留 legacy 集合不精确",
            )
        )

    if verification.store_count_after_phase_a != 136:
        issues.append(
            _issue(
                "PHASE_A_STORE_COUNT_MISMATCH",
                message="阶段 A 后存储总数不符合 136 条不变量",
            )
        )
    if (
        verification.controlled_tool_record_count != 64
        or verification.legacy_tool_record_count != 64
        or verification.text_memory_count != 8
    ):
        issues.append(
            _issue(
                "PHASE_A_CLASSIFICATION_COUNT_MISMATCH",
                message="阶段 A 后分类数量不变量不满足",
            )
        )
    if any(
        count != 0
        for count in (
            verification.malformed_record_count,
            verification.unknown_record_count,
            verification.duplicate_content_group_count,
            verification.content_address_conflict_count,
        )
    ):
        issues.append(
            _issue(
                "PHASE_A_INVALID_RECORDS_PRESENT",
                message="阶段 A 验证发现无效、未知、重复或冲突记录",
            )
        )
    if not isinstance(
        verification.inventory_sha256, str
    ) or not SHA256_PATTERN.fullmatch(verification.inventory_sha256):
        issues.append(
            _issue(
                "PHASE_A_INVENTORY_SHA256_INVALID",
                message="阶段 A 清点摘要格式无效",
            )
        )
    return tuple(issues)


def analyze_legacy_metadata_coverage(
    snapshots: Sequence[LegacyToolRecordSnapshot],
) -> dict[str, Any]:
    """统计兼容 metadata 的存在、类型正确和有效非空覆盖率。"""

    fields = (
        "sample_id",
        "training_level",
        "train_decision",
        "review_reason",
        "source",
        "expected_behavior",
        "expected_tables",
    )
    coverage = {
        field: {
            "present_count": 0,
            "type_valid_count": 0,
            "nonempty_valid_count": 0,
            "total": len(snapshots),
        }
        for field in fields
    }
    for snapshot in snapshots:
        metadata = snapshot.compatibility_metadata
        for field in fields:
            if field not in metadata:
                continue
            coverage[field]["present_count"] += 1
            value = metadata[field]
            type_valid = (
                isinstance(value, list)
                if field == "expected_tables"
                else isinstance(value, str)
            )
            if type_valid:
                coverage[field]["type_valid_count"] += 1
            nonempty_valid = (
                _valid_expected_tables(value)
                if field == "expected_tables"
                else _is_nonempty_string(value)
            )
            if nonempty_valid:
                coverage[field]["nonempty_valid_count"] += 1
    return {"legacy_record_count": len(snapshots), "fields": coverage}


def _snapshot_issues(
    snapshot: LegacyToolRecordSnapshot,
) -> tuple[MigrationPlanIssue, ...]:
    issues: list[MigrationPlanIssue] = []
    identity = build_memory_identity_from_canonical_content(
        snapshot.canonical_content
    )
    common = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "target_record_id": snapshot.target_record_id,
    }
    if not _is_nonempty_string(snapshot.legacy_storage_id):
        issues.append(
            _issue("INVALID_LEGACY_STORAGE_ID", message="旧存储 ID 为空", **common)
        )
    if identity.record_id != snapshot.target_record_id:
        issues.append(
            _issue(
                "TARGET_RECORD_ID_MISMATCH",
                message="target_record_id 与内容身份重算结果不一致",
                **common,
            )
        )
    if identity.memory_content_sha256 != snapshot.memory_content_sha256:
        issues.append(
            _issue(
                "TARGET_CONTENT_DIGEST_MISMATCH",
                message="memory_content_sha256 与内容身份重算结果不一致",
                **common,
            )
        )
    raw = snapshot.raw_metadata
    required_raw = (
        "question",
        "tool_name",
        "args_json",
        "success",
        "metadata_json",
    )
    if any(field not in raw for field in required_raw):
        issues.append(
            _issue(
                "INVALID_LEGACY_RAW_METADATA",
                message="旧顶层 metadata 缺少必要字段",
                **common,
            )
        )
    conflicts = sorted(
        field
        for field in MIGRATION_COMPATIBILITY_FIELDS
        if field in snapshot.compatibility_metadata
    )
    if conflicts:
        issues.append(
            _issue(
                "MIGRATION_METADATA_FIELD_CONFLICT",
                message="旧 compatibility metadata 占用迁移治理字段："
                + ",".join(conflicts),
                **common,
            )
        )
    metadata = snapshot.compatibility_metadata
    for field in REQUIRED_COMPATIBILITY_FIELDS:
        value = metadata.get(field)
        valid = (
            _valid_expected_tables(value)
            if field == "expected_tables"
            else _is_nonempty_string(value)
        )
        if not valid:
            issues.append(
                _issue(
                    "REQUIRED_COMPATIBILITY_FIELD_INVALID",
                    message=f"迁移阻断字段无效：{field}",
                    **common,
                )
            )
    for field in ENHANCER_REQUIRED_FIELDS:
        if not _is_nonempty_string(metadata.get(field)):
            issues.append(
                _issue(
                    "ENHANCER_REQUIRED_FIELD_INVALID",
                    message=f"当前增强器依赖字段无效：{field}",
                    **common,
                )
            )
    return tuple(issues)


def _content_material(
    *,
    migration_batch_id: str,
    approved_formal_source_sha256: str,
    approved_verified_backup_sha256: str,
    expected_legacy_count: int,
    text_memory_count: int,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_batch_id": migration_batch_id,
        "approved_formal_source_sha256": approved_formal_source_sha256,
        "approved_verified_backup_sha256": approved_verified_backup_sha256,
        "expected_legacy_count": expected_legacy_count,
        "text_memory_excluded_count": text_memory_count,
        "items": [
            {
                "migration_sample_id": row["migration_sample_id"],
                "legacy_storage_id": row["snapshot"].legacy_storage_id,
                "target_record_id": row["snapshot"].target_record_id,
                "memory_content_sha256": row[
                    "snapshot"
                ].memory_content_sha256,
                "legacy_metadata_sha256": row["legacy_metadata_sha256"],
                "migration_item_sha256": row["migration_item_sha256"],
                "canonical_content": dict(row["snapshot"].canonical_content),
                "legacy_compatibility_metadata": dict(
                    row["snapshot"].compatibility_metadata
                ),
                "legacy_missing_fields": list(row["legacy_missing_fields"]),
                "phase_a_action": "create_target",
                "phase_b_action": "retain_legacy",
            }
            for row in rows
        ],
    }


def _target_metadata(
    *,
    snapshot: LegacyToolRecordSnapshot,
    migration_batch_id: str,
    migration_sample_id: str,
    migration_item_sha256: str,
    legacy_metadata_sha256: str,
    migration_plan_content_sha256: str,
    legacy_missing_fields: Sequence[str],
    legacy_invalid_fields: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    compatibility = dict(snapshot.compatibility_metadata)
    migration_metadata = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "legacy_metadata_sha256": legacy_metadata_sha256,
        "migration_batch_id": migration_batch_id,
        "migration_plan_content_sha256": migration_plan_content_sha256,
        "migration_item_sha256": migration_item_sha256,
        "memory_content_sha256": snapshot.memory_content_sha256,
        "legacy_missing_fields": list(legacy_missing_fields),
        "legacy_invalid_fields": list(legacy_invalid_fields),
    }
    for field, value in migration_metadata.items():
        if field not in compatibility:
            compatibility[field] = value

    canonical = snapshot.canonical_content
    governance = {
        "question": canonical.get("question", ""),
        "tool_name": canonical.get("tool_name", ""),
        "args_json": _canonical_json(canonical.get("args", {})).decode("utf-8"),
        "success": canonical.get("success") is True,
        "metadata_json": _canonical_json(compatibility).decode("utf-8"),
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "memory_kind": MEMORY_KIND,
        "memory_content_sha256": snapshot.memory_content_sha256,
        "created_by_training_batch_id": migration_batch_id,
        "created_by_batch_content_sha256": migration_plan_content_sha256,
        "created_from_sample_id": migration_sample_id,
        "training_level": snapshot.compatibility_metadata.get(
            "training_level", ""
        ),
        "train_decision": snapshot.compatibility_metadata.get(
            "train_decision", ""
        ),
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migrated_from_legacy_storage_id": snapshot.legacy_storage_id,
        "legacy_metadata_sha256": legacy_metadata_sha256,
        "migration_plan_content_sha256": migration_plan_content_sha256,
        "migration_item_sha256": migration_item_sha256,
    }
    return governance, compatibility


def _existing_target_action(
    *,
    existing: ExistingMigrationTargetSnapshot | None,
    snapshot: LegacyToolRecordSnapshot,
    desired_governance: Mapping[str, Any],
    desired_compatibility: Mapping[str, Any],
) -> tuple[PhaseAAction, tuple[MigrationPlanIssue, ...]]:
    if existing is None:
        return "create_target", ()
    common = {
        "legacy_storage_id": snapshot.legacy_storage_id,
        "target_record_id": snapshot.target_record_id,
    }
    identity_equal = (
        existing.record_id == snapshot.target_record_id
        and existing.memory_content_sha256 == snapshot.memory_content_sha256
        and _canonical_json(existing.canonical_content)
        == _canonical_json(snapshot.canonical_content)
    )
    if not identity_equal:
        return (
            "block_content_conflict",
            (
                _issue(
                    "TARGET_PREEXISTING_CONTENT_CONFLICT",
                    message="已存在 target 的内容身份不一致",
                    **common,
                ),
            ),
        )
    if (
        _canonical_json(existing.top_level_metadata)
        == _canonical_json(desired_governance)
        and _canonical_json(existing.compatibility_metadata)
        == _canonical_json(desired_compatibility)
    ):
        return "resume_target_created", ()
    return (
        "block_preexisting_target",
        (
            _issue(
                "TARGET_PREEXISTING_OTHER_OWNER",
                message="已存在 target 内容相同但迁移归属不同",
                **common,
            ),
        ),
    )


def build_legacy_tool_memory_migration_plan(
    snapshots: Sequence[LegacyToolRecordSnapshot],
    *,
    migration_batch_id: str,
    approved_formal_source_sha256: str,
    approved_verified_backup_sha256: str,
    expected_legacy_count: int = DEFAULT_EXPECTED_LEGACY_COUNT,
    text_memory_count: int = DEFAULT_TEXT_MEMORY_COUNT,
    observed_malformed_count: int = 0,
    observed_unknown_count: int = 0,
    observed_content_address_conflict_count: int = 0,
    existing_targets: Mapping[
        str, ExistingMigrationTargetSnapshot
    ] | None = None,
    phase_a_verification: PhaseAVerificationSnapshot | None = None,
    phase_b_approved: bool = False,
) -> LegacyToolMemoryMigrationPlan:
    """生成不访问任何存储的两阶段确定性迁移计划。"""

    if not isinstance(phase_b_approved, bool):
        raise TypeError("phase_b_approved 必须是布尔值")
    snapshot_list = list(snapshots)
    global_issues: list[MigrationPlanIssue] = []
    if not _is_nonempty_string(migration_batch_id):
        global_issues.append(
            _issue("INVALID_MIGRATION_BATCH_ID", message="migration_batch_id 为空")
        )
    if approved_formal_source_sha256 != APPROVED_FORMAL_SOURCE_SHA256:
        global_issues.append(
            _issue(
                "FORMAL_SOURCE_DIGEST_MISMATCH",
                message="正式源摘要不是批准基线",
            )
        )
    if approved_verified_backup_sha256 != APPROVED_VERIFIED_BACKUP_SHA256:
        global_issues.append(
            _issue(
                "VERIFIED_BACKUP_DIGEST_MISMATCH",
                message="验证备份摘要不是批准基线",
            )
        )
    if len(snapshot_list) != expected_legacy_count:
        global_issues.append(
            _issue(
                "LEGACY_RECORD_COUNT_MISMATCH",
                message=f"legacy 数量为 {len(snapshot_list)}，预期 {expected_legacy_count}",
            )
        )
    if observed_malformed_count:
        global_issues.append(
            _issue(
                "MALFORMED_RECORDS_PRESENT",
                message=f"存在 {observed_malformed_count} 条损坏记录",
            )
        )
    if observed_unknown_count:
        global_issues.append(
            _issue(
                "UNKNOWN_RECORDS_PRESENT",
                message=f"存在 {observed_unknown_count} 条未知记录",
            )
        )
    if observed_content_address_conflict_count:
        global_issues.append(
            _issue(
                "CONTENT_ADDRESS_CONFLICTS_PRESENT",
                message=(
                    "存在 "
                    f"{observed_content_address_conflict_count} 条内容寻址冲突"
                ),
            )
        )

    legacy_ids: dict[str, int] = {}
    target_ids: dict[str, int] = {}
    for snapshot in snapshot_list:
        legacy_ids[snapshot.legacy_storage_id] = (
            legacy_ids.get(snapshot.legacy_storage_id, 0) + 1
        )
        target_ids[snapshot.target_record_id] = (
            target_ids.get(snapshot.target_record_id, 0) + 1
        )
    for legacy_id, count in sorted(legacy_ids.items()):
        if count > 1:
            global_issues.append(
                _issue(
                    "DUPLICATE_LEGACY_STORAGE_ID",
                    legacy_storage_id=legacy_id,
                    message="legacy_storage_id 重复",
                )
            )
    for target_id, count in sorted(target_ids.items()):
        if count > 1:
            global_issues.append(
                _issue(
                    "DUPLICATE_TARGET_RECORD_ID",
                    target_record_id=target_id,
                    message="多个旧记录映射到同一 target_record_id",
                )
            )

    ordered = sorted(
        snapshot_list,
        key=lambda snapshot: (
            snapshot.target_record_id,
            snapshot.legacy_storage_id,
        ),
    )
    rows: list[dict[str, Any]] = []
    for index, snapshot in enumerate(ordered, start=1):
        migration_sample_id = f"{MIGRATION_SAMPLE_PREFIX}{index:03d}"
        legacy_digest = _legacy_metadata_sha256(snapshot)
        missing_fields, invalid_fields = _compatibility_field_lists(
            snapshot.compatibility_metadata
        )
        item_digest = _migration_item_sha256(
            migration_batch_id=migration_batch_id,
            migration_sample_id=migration_sample_id,
            legacy_storage_id=snapshot.legacy_storage_id,
            target_record_id=snapshot.target_record_id,
            memory_content_sha256=snapshot.memory_content_sha256,
            legacy_metadata_sha256=legacy_digest,
        )
        rows.append(
            {
                "snapshot": snapshot,
                "migration_sample_id": migration_sample_id,
                "legacy_metadata_sha256": legacy_digest,
                "migration_item_sha256": item_digest,
                "legacy_missing_fields": missing_fields,
                "legacy_invalid_fields": invalid_fields,
                "issues": list(_snapshot_issues(snapshot)),
            }
        )

    content_material = _content_material(
        migration_batch_id=migration_batch_id,
        approved_formal_source_sha256=approved_formal_source_sha256,
        approved_verified_backup_sha256=approved_verified_backup_sha256,
        expected_legacy_count=expected_legacy_count,
        text_memory_count=text_memory_count,
        rows=rows,
    )
    migration_plan_content_sha256 = _sha256(content_material)
    targets = existing_targets or {}
    items: list[LegacyToolMemoryMigrationItem] = []
    for row in rows:
        snapshot = row["snapshot"]
        governance, compatibility = _target_metadata(
            snapshot=snapshot,
            migration_batch_id=migration_batch_id,
            migration_sample_id=row["migration_sample_id"],
            migration_item_sha256=row["migration_item_sha256"],
            legacy_metadata_sha256=row["legacy_metadata_sha256"],
            migration_plan_content_sha256=migration_plan_content_sha256,
            legacy_missing_fields=row["legacy_missing_fields"],
            legacy_invalid_fields=row["legacy_invalid_fields"],
        )
        action, existing_issues = _existing_target_action(
            existing=targets.get(snapshot.target_record_id),
            snapshot=snapshot,
            desired_governance=governance,
            desired_compatibility=compatibility,
        )
        item_issues = tuple(row["issues"]) + existing_issues
        items.append(
            LegacyToolMemoryMigrationItem(
                migration_sample_id=row["migration_sample_id"],
                legacy_storage_id=snapshot.legacy_storage_id,
                target_record_id=snapshot.target_record_id,
                memory_content_sha256=snapshot.memory_content_sha256,
                legacy_metadata_sha256=row["legacy_metadata_sha256"],
                migration_item_sha256=row["migration_item_sha256"],
                target_governance_metadata=governance,
                target_compatibility_metadata=compatibility,
                phase_a_action=action,
                phase_b_action="retain_legacy",
                issues=item_issues,
                executable=not item_issues,
            )
        )

    all_issues = tuple(
        sorted(
            global_issues
            + [issue for item in items for issue in item.issues],
            key=lambda issue: (
                issue.legacy_storage_id,
                issue.target_record_id,
                issue.code,
            ),
        )
    )
    executable = not all_issues
    proposed_deletes = tuple(sorted(legacy_ids))
    phase_a_verification_issues = _validate_phase_a_verification(
        migration_plan_content_sha256,
        tuple(item.target_record_id for item in items),
        proposed_deletes,
        phase_a_verification,
    )
    phase_a_verification_provided = phase_a_verification is not None
    phase_a_verified = (
        phase_a_verification_provided and not phase_a_verification_issues
    )
    executable_deletes = (
        proposed_deletes
        if phase_b_approved and phase_a_verified and executable
        else ()
    )
    rollback_ids = tuple(sorted(target_ids))
    create_count = sum(item.phase_a_action == "create_target" for item in items)
    final_material = {
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_batch_id": migration_batch_id,
        "approved_formal_source_sha256": approved_formal_source_sha256,
        "approved_verified_backup_sha256": approved_verified_backup_sha256,
        "expected_legacy_count": expected_legacy_count,
        "expected_target_create_count": create_count,
        "expected_phase_a_store_count": (
            expected_legacy_count + expected_legacy_count + text_memory_count
        ),
        "expected_final_store_count": expected_legacy_count + text_memory_count,
        "text_memory_excluded_count": text_memory_count,
        "migration_plan_content_sha256": migration_plan_content_sha256,
        "items": [item.to_dict() for item in items],
        "phase_a_possible_rollback_ids": list(rollback_ids),
        "phase_a_verification_provided": phase_a_verification_provided,
        "phase_a_verified": phase_a_verified,
        "phase_a_verification": _phase_a_verification_material(
            phase_a_verification
        ),
        "phase_a_verification_issues": [
            asdict(issue) for issue in phase_a_verification_issues
        ],
        "phase_b_approved": phase_b_approved,
        "proposed_legacy_delete_ids": list(proposed_deletes),
        "phase_b_executable_delete_ids": list(executable_deletes),
        "issues": [asdict(issue) for issue in all_issues],
        "executable": executable,
    }
    migration_plan_sha256 = _sha256(final_material)
    return LegacyToolMemoryMigrationPlan(
        migration_schema_version=MIGRATION_SCHEMA_VERSION,
        migration_batch_id=migration_batch_id,
        approved_formal_source_sha256=approved_formal_source_sha256,
        approved_verified_backup_sha256=approved_verified_backup_sha256,
        expected_legacy_count=expected_legacy_count,
        expected_target_create_count=create_count,
        expected_phase_a_store_count=(
            expected_legacy_count + expected_legacy_count + text_memory_count
        ),
        expected_final_store_count=expected_legacy_count + text_memory_count,
        text_memory_excluded_count=text_memory_count,
        migration_plan_content_sha256=migration_plan_content_sha256,
        items=tuple(items),
        phase_a_possible_rollback_ids=rollback_ids,
        phase_a_verification_provided=phase_a_verification_provided,
        phase_a_verified=phase_a_verified,
        phase_a_verification_issues=phase_a_verification_issues,
        phase_b_approved=phase_b_approved,
        proposed_legacy_delete_ids=proposed_deletes,
        phase_b_executable_delete_ids=executable_deletes,
        issues=all_issues,
        executable=executable,
        migration_plan_sha256=migration_plan_sha256,
    )


def build_public_migration_evidence(
    plan: LegacyToolMemoryMigrationPlan,
) -> dict[str, Any]:
    """生成不包含问题、SQL 或原始 JSON 正文的公开证据视图。"""

    allowed_item_fields = {
        "migration_sample_id",
        "legacy_storage_id",
        "target_record_id",
        "memory_content_sha256",
        "legacy_metadata_sha256",
        "migration_item_sha256",
        "metadata_field_names",
        "missing_fields",
        "invalid_fields",
        "issue_codes",
        "phase_a_action",
        "phase_b_action",
        "executable",
    }
    items = []
    for item in plan.items:
        public_item = {
            "migration_sample_id": item.migration_sample_id,
            "legacy_storage_id": item.legacy_storage_id,
            "target_record_id": item.target_record_id,
            "memory_content_sha256": item.memory_content_sha256,
            "legacy_metadata_sha256": item.legacy_metadata_sha256,
            "migration_item_sha256": item.migration_item_sha256,
            "metadata_field_names": sorted(
                item.target_compatibility_metadata
            ),
            "missing_fields": list(
                item.target_compatibility_metadata.get(
                    "legacy_missing_fields", []
                )
            ),
            "invalid_fields": list(
                item.target_compatibility_metadata.get(
                    "legacy_invalid_fields", []
                )
            ),
            "issue_codes": sorted(issue.code for issue in item.issues),
            "phase_a_action": item.phase_a_action,
            "phase_b_action": item.phase_b_action,
            "executable": item.executable,
        }
        if set(public_item) != allowed_item_fields:
            raise AssertionError("公开迁移项字段集合发生变化")
        items.append(public_item)
    return {
        "migration_schema_version": plan.migration_schema_version,
        "migration_batch_id": plan.migration_batch_id,
        "migration_plan_content_sha256": plan.migration_plan_content_sha256,
        "migration_plan_sha256": plan.migration_plan_sha256,
        "executable": plan.executable,
        "issue_codes": sorted({issue.code for issue in plan.issues}),
        "items": items,
    }


__all__ = [
    "APPROVED_FORMAL_SOURCE_SHA256",
    "APPROVED_VERIFIED_BACKUP_SHA256",
    "ExistingMigrationTargetSnapshot",
    "LegacyToolMemoryMigrationItem",
    "LegacyToolMemoryMigrationPlan",
    "LegacyToolRecordSnapshot",
    "MIGRATION_SCHEMA_VERSION",
    "MigrationPlanIssue",
    "PhaseAVerificationSnapshot",
    "analyze_legacy_metadata_coverage",
    "build_legacy_tool_memory_migration_plan",
    "build_public_migration_evidence",
]

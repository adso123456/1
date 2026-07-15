"""旧 Tool Memory expected_tables 的只读恢复提案契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from training.sop.batch_validator import (
    _normalize_sql,
    _normalized_tables,
    _statement_count,
)


RECOVERY_SCHEMA_VERSION = "1.1"
RECOVERY_STRATEGY_VERSION = "sqlguard-used-tables-v1"
APPROVED_RECOVERY_BASE_COMMIT = "c5b88b1ae491850f18536700d29635aa255b6e2a"
APPROVED_FORMAL_SOURCE_SHA256 = (
    "4fd753c4d4c0d22119b6349856f195fe4ed7e23466120f6786edb979606646d8"
)
APPROVED_VERIFIED_BACKUP_SHA256 = APPROVED_FORMAL_SOURCE_SHA256
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TARGET_RECORD_ID_PATTERN = re.compile(r"^toolmem-v1-[0-9a-f]{64}$")

RecoveryState = Literal[
    "SOURCE_BLOCKED",
    "CALIBRATION_FAILED",
    "RECOVERY_BLOCKED",
    "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL",
]
ExpectedTablesState = Literal["valid", "missing", "invalid"]


@dataclass(frozen=True)
class LegacyExpectedTablesRecord:
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    question: str
    sql: str
    stored_expected_tables: tuple[str, ...] | None
    expected_tables_state: ExpectedTablesState
    tool_name: str = "run_sql"
    success: bool = True
    canonical_sql_present: bool = True


@dataclass(frozen=True)
class SqlAnalysisEvidence:
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    sql_sha256: str
    normalized_sql_sha256: str
    statement_count: int
    guard_passed: bool
    guard_severity: str
    used_tables: tuple[str, ...]
    unknown_tables: tuple[str, ...]
    unknown_columns: tuple[str, ...]
    forbidden_operations: tuple[str, ...]
    candidate_mismatch: tuple[str, ...]
    analysis_status: Literal["ready", "blocked"]
    issue_codes: tuple[str, ...]
    analysis_item_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationItem:
    legacy_storage_id: str
    target_record_id: str
    sql_sha256: str
    stored_expected_tables: tuple[str, ...]
    derived_expected_tables: tuple[str, ...]
    matched: bool
    issue_codes: tuple[str, ...]
    analysis_item_sha256: str
    calibration_item_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryProposalItem:
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    sql_sha256: str
    normalized_sql_sha256: str
    proposed_expected_tables: tuple[str, ...]
    issue_codes: tuple[str, ...]
    analysis_item_sha256: str
    recovery_item_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoverySourceFacts:
    formal_source_sha256_before: str
    formal_source_sha256_after: str
    verified_backup_sha256_before: str
    verified_backup_sha256_after: str
    audit_copy_sha256_before_open: str
    audit_copy_sha256_after_open: str
    audit_inventory_sha256: str
    metadata_index_sha256_before: str
    metadata_index_sha256_after: str
    sql_guard_source_sha256: str
    batch_validator_source_sha256: str
    recovery_module_source_sha256: str
    audit_entry_source_sha256: str
    store_count: int
    legacy_record_count: int
    text_memory_count: int
    controlled_record_count: int
    malformed_record_count: int
    unknown_record_count: int
    duplicate_group_count: int
    content_conflict_count: int
    legacy_id_mismatch_count: int


@dataclass(frozen=True)
class RecoveryEnvironment:
    recovery_schema_version: str
    recovery_strategy_version: str
    base_commit: str
    approved_formal_source_sha256: str
    approved_verified_backup_sha256: str
    audit_inventory_sha256: str
    metadata_index_sha256: str
    sql_guard_source_sha256: str
    batch_validator_source_sha256: str
    recovery_module_source_sha256: str
    audit_entry_source_sha256: str
    expected_store_count: int
    expected_legacy_count: int
    expected_text_memory_count: int
    expected_calibration_count: int
    expected_recovery_count: int
    recovery_environment_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExpectedTablesRecoveryResult:
    state: RecoveryState
    calibration_ready: bool
    proposal_ready: bool
    issue_codes: tuple[str, ...]
    calibration_items: tuple[CalibrationItem, ...]
    recovery_items: tuple[RecoveryProposalItem, ...]
    calibration_record_count: int
    calibration_match_count: int
    calibration_mismatch_count: int
    calibration_blocked_count: int
    recovery_candidate_count: int
    recovery_proposed_count: int
    recovery_blocked_count: int
    recovery_environment_sha256: str
    recovery_proposal_sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "calibration_ready": self.calibration_ready,
            "proposal_ready": self.proposal_ready,
            "issue_codes": list(self.issue_codes),
            "calibration_items": [
                item.to_public_dict() for item in self.calibration_items
            ],
            "recovery_items": [
                item.to_public_dict() for item in self.recovery_items
            ],
            "calibration_record_count": self.calibration_record_count,
            "calibration_match_count": self.calibration_match_count,
            "calibration_mismatch_count": self.calibration_mismatch_count,
            "calibration_blocked_count": self.calibration_blocked_count,
            "recovery_candidate_count": self.recovery_candidate_count,
            "recovery_proposed_count": self.recovery_proposed_count,
            "recovery_blocked_count": self.recovery_blocked_count,
            "recovery_environment_sha256": self.recovery_environment_sha256,
            "recovery_proposal_sha256": self.recovery_proposal_sha256,
        }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def recompute_sql_analysis_item_sha256(analysis: SqlAnalysisEvidence) -> str:
    material = analysis.to_public_dict()
    material.pop("analysis_item_sha256", None)
    return _sha256_json(material)


def recompute_recovery_environment_sha256(
    environment: RecoveryEnvironment,
) -> str:
    material = environment.to_public_dict()
    material.pop("recovery_environment_sha256", None)
    return _sha256_json(material)


def classify_expected_tables(
    value: Any,
    *,
    field_present: bool,
) -> tuple[ExpectedTablesState, tuple[str, ...] | None]:
    if not field_present:
        return "missing", None
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        return "invalid", None
    normalized = tuple(_normalized_tables(value))
    if not normalized or len(normalized) != len(value):
        return "invalid", None
    return "valid", tuple(value)


def analyze_record_sql(
    record: LegacyExpectedTablesRecord,
    guard: Any,
) -> SqlAnalysisEvidence:
    sql = record.sql if isinstance(record.sql, str) else ""
    normalized_sql = _normalize_sql(sql)
    issues: list[str] = []
    statement_count = _statement_count(sql)
    if not sql.strip():
        issues.append("SQL_EMPTY")
    if statement_count != 1:
        issues.append("SQL_STATEMENT_COUNT_INVALID")

    try:
        result = guard.validate(
            sql=sql,
            query="",
            deterministic_candidate_tables=[],
        )
        guard_passed = result.passed is True
        guard_severity = str(result.severity)
        used_tables = tuple(_normalized_tables(list(result.used_tables)))
        unknown_tables = tuple(sorted(set(result.unknown_tables)))
        unknown_columns = tuple(sorted(set(result.unknown_columns)))
        forbidden_operations = tuple(sorted(set(result.forbidden_operations)))
        candidate_mismatch = tuple(sorted(set(result.candidate_mismatch)))
    except Exception:  # noqa: BLE001 - 公开证据不得保存异常正文
        guard_passed = False
        guard_severity = "exception"
        used_tables = ()
        unknown_tables = ()
        unknown_columns = ()
        forbidden_operations = ()
        candidate_mismatch = ()
        issues.append("SQL_GUARD_EXCEPTION")

    if not guard_passed:
        issues.append("SQL_GUARD_REJECTED")
    if guard_severity != "ok":
        issues.append("GUARD_SEVERITY_NOT_OK")
    if unknown_tables:
        issues.append("UNKNOWN_TABLES_PRESENT")
    if unknown_columns:
        issues.append("UNKNOWN_COLUMNS_PRESENT")
    if forbidden_operations:
        issues.append("FORBIDDEN_OPERATIONS_PRESENT")
    if candidate_mismatch:
        issues.append("CANDIDATE_MISMATCH_PRESENT")
    if not used_tables:
        issues.append("USED_TABLES_EMPTY")

    issue_codes = tuple(sorted(set(issues)))
    material = {
        "legacy_storage_id": record.legacy_storage_id,
        "target_record_id": record.target_record_id,
        "memory_content_sha256": record.memory_content_sha256,
        "sql_sha256": _sha256_text(sql),
        "normalized_sql_sha256": _sha256_text(normalized_sql),
        "statement_count": statement_count,
        "guard_passed": guard_passed,
        "guard_severity": guard_severity,
        "used_tables": list(used_tables),
        "unknown_tables": list(unknown_tables),
        "unknown_columns": list(unknown_columns),
        "forbidden_operations": list(forbidden_operations),
        "candidate_mismatch": list(candidate_mismatch),
        "analysis_status": "blocked" if issue_codes else "ready",
        "issue_codes": list(issue_codes),
    }
    return SqlAnalysisEvidence(
        legacy_storage_id=record.legacy_storage_id,
        target_record_id=record.target_record_id,
        memory_content_sha256=record.memory_content_sha256,
        sql_sha256=material["sql_sha256"],
        normalized_sql_sha256=material["normalized_sql_sha256"],
        statement_count=statement_count,
        guard_passed=guard_passed,
        guard_severity=guard_severity,
        used_tables=used_tables,
        unknown_tables=unknown_tables,
        unknown_columns=unknown_columns,
        forbidden_operations=forbidden_operations,
        candidate_mismatch=candidate_mismatch,
        analysis_status=material["analysis_status"],
        issue_codes=issue_codes,
        analysis_item_sha256=_sha256_json(material),
    )


def build_recovery_environment(
    *,
    base_commit: str,
    audit_inventory_sha256: str,
    metadata_index_sha256: str,
    sql_guard_source_sha256: str,
    batch_validator_source_sha256: str,
    recovery_module_source_sha256: str,
    audit_entry_source_sha256: str,
) -> RecoveryEnvironment:
    material = {
        "recovery_schema_version": RECOVERY_SCHEMA_VERSION,
        "recovery_strategy_version": RECOVERY_STRATEGY_VERSION,
        "base_commit": base_commit,
        "approved_formal_source_sha256": APPROVED_FORMAL_SOURCE_SHA256,
        "approved_verified_backup_sha256": APPROVED_VERIFIED_BACKUP_SHA256,
        "audit_inventory_sha256": audit_inventory_sha256,
        "metadata_index_sha256": metadata_index_sha256,
        "sql_guard_source_sha256": sql_guard_source_sha256,
        "batch_validator_source_sha256": batch_validator_source_sha256,
        "recovery_module_source_sha256": recovery_module_source_sha256,
        "audit_entry_source_sha256": audit_entry_source_sha256,
        "expected_store_count": 72,
        "expected_legacy_count": 64,
        "expected_text_memory_count": 8,
        "expected_calibration_count": 48,
        "expected_recovery_count": 16,
    }
    return RecoveryEnvironment(
        **material,
        recovery_environment_sha256=_sha256_json(material),
    )


def _source_issue_codes(
    records: Sequence[LegacyExpectedTablesRecord],
    facts: RecoverySourceFacts,
    environment: RecoveryEnvironment,
) -> tuple[str, ...]:
    issues: list[str] = []
    expected_facts = {
        "formal_source_sha256_before": APPROVED_FORMAL_SOURCE_SHA256,
        "formal_source_sha256_after": APPROVED_FORMAL_SOURCE_SHA256,
        "verified_backup_sha256_before": APPROVED_VERIFIED_BACKUP_SHA256,
        "verified_backup_sha256_after": APPROVED_VERIFIED_BACKUP_SHA256,
        "audit_copy_sha256_before_open": APPROVED_VERIFIED_BACKUP_SHA256,
        "store_count": 72,
        "legacy_record_count": 64,
        "text_memory_count": 8,
        "controlled_record_count": 0,
        "malformed_record_count": 0,
        "unknown_record_count": 0,
        "duplicate_group_count": 0,
        "content_conflict_count": 0,
        "legacy_id_mismatch_count": 64,
    }
    facts_dict = asdict(facts)
    for field, expected in expected_facts.items():
        if facts_dict[field] != expected:
            issues.append(f"SOURCE_{field.upper()}_MISMATCH")
    if not _valid_sha256(facts.audit_copy_sha256_after_open):
        issues.append("SOURCE_AUDIT_COPY_SHA256_AFTER_OPEN_INVALID")
    if facts.metadata_index_sha256_before != facts.metadata_index_sha256_after:
        issues.append("METADATA_INDEX_CHANGED")
    if len(records) != 64:
        issues.append("LEGACY_INPUT_COUNT_MISMATCH")
    legacy_ids = [record.legacy_storage_id for record in records]
    target_ids = [record.target_record_id for record in records]
    if len(legacy_ids) != len(set(legacy_ids)):
        issues.append("DUPLICATE_LEGACY_STORAGE_ID")
    if len(target_ids) != len(set(target_ids)):
        issues.append("DUPLICATE_TARGET_RECORD_ID")
    if any(record.tool_name != "run_sql" for record in records):
        issues.append("NON_RUN_SQL_RECORD")
    if any(record.success is not True for record in records):
        issues.append("UNSUCCESSFUL_LEGACY_RECORD")
    if any(
        not record.canonical_sql_present or not isinstance(record.sql, str)
        for record in records
    ):
        issues.append("CANONICAL_SQL_MISSING")
    if any(not record.legacy_storage_id for record in records):
        issues.append("EMPTY_LEGACY_STORAGE_ID")
    if any(
        not TARGET_RECORD_ID_PATTERN.fullmatch(record.target_record_id)
        for record in records
    ):
        issues.append("INVALID_TARGET_RECORD_ID")
    if any(not _valid_sha256(record.memory_content_sha256) for record in records):
        issues.append("INVALID_MEMORY_CONTENT_SHA256")
    state_counts = {
        state: sum(record.expected_tables_state == state for record in records)
        for state in ("valid", "missing", "invalid")
    }
    if state_counts != {"valid": 48, "missing": 16, "invalid": 0}:
        issues.append("EXPECTED_TABLES_DISTRIBUTION_MISMATCH")
    for record in records:
        if record.expected_tables_state == "missing":
            consistent = record.stored_expected_tables is None
        elif record.expected_tables_state == "valid":
            state, _ = classify_expected_tables(
                list(record.stored_expected_tables or ()), field_present=True
            )
            consistent = record.stored_expected_tables is not None and state == "valid"
        else:
            consistent = False
        if not consistent:
            issues.append("EXPECTED_TABLES_STATE_VALUE_MISMATCH")
            break

    environment_checks = (
        (environment.base_commit == APPROVED_RECOVERY_BASE_COMMIT, "RECOVERY_BASE_COMMIT_MISMATCH"),
        (recompute_recovery_environment_sha256(environment) == environment.recovery_environment_sha256, "RECOVERY_ENVIRONMENT_SHA256_MISMATCH"),
        (environment.recovery_schema_version == RECOVERY_SCHEMA_VERSION, "RECOVERY_ENVIRONMENT_SCHEMA_MISMATCH"),
        (environment.recovery_strategy_version == RECOVERY_STRATEGY_VERSION, "RECOVERY_ENVIRONMENT_STRATEGY_MISMATCH"),
        (environment.audit_inventory_sha256 == facts.audit_inventory_sha256, "RECOVERY_ENVIRONMENT_INVENTORY_MISMATCH"),
        (environment.metadata_index_sha256 == facts.metadata_index_sha256_before == facts.metadata_index_sha256_after, "RECOVERY_ENVIRONMENT_METADATA_INDEX_MISMATCH"),
        (environment.sql_guard_source_sha256 == facts.sql_guard_source_sha256, "RECOVERY_ENVIRONMENT_SQL_GUARD_SOURCE_MISMATCH"),
        (environment.batch_validator_source_sha256 == facts.batch_validator_source_sha256, "RECOVERY_ENVIRONMENT_BATCH_VALIDATOR_SOURCE_MISMATCH"),
        (environment.recovery_module_source_sha256 == facts.recovery_module_source_sha256, "RECOVERY_ENVIRONMENT_MODULE_SOURCE_MISMATCH"),
        (environment.audit_entry_source_sha256 == facts.audit_entry_source_sha256, "RECOVERY_ENVIRONMENT_AUDIT_SOURCE_MISMATCH"),
        (environment.approved_formal_source_sha256 == APPROVED_FORMAL_SOURCE_SHA256 and environment.approved_verified_backup_sha256 == APPROVED_VERIFIED_BACKUP_SHA256, "RECOVERY_ENVIRONMENT_SOURCE_APPROVAL_MISMATCH"),
        ((environment.expected_store_count, environment.expected_legacy_count, environment.expected_text_memory_count, environment.expected_calibration_count, environment.expected_recovery_count) == (72, 64, 8, 48, 16), "RECOVERY_ENVIRONMENT_COUNT_MISMATCH"),
    )
    issues.extend(code for passed, code in environment_checks if not passed)
    return tuple(sorted(set(issues)))


def _calibration_item(
    record: LegacyExpectedTablesRecord,
    analysis: SqlAnalysisEvidence,
) -> CalibrationItem:
    stored = tuple(_normalized_tables(list(record.stored_expected_tables or ())))
    derived = analysis.used_tables
    issues = list(_analysis_binding_issue_codes(record, analysis))
    if stored != derived:
        issues.append("CALIBRATION_TABLES_MISMATCH")
    issue_codes = tuple(sorted(set(issues)))
    material = {
        "legacy_storage_id": record.legacy_storage_id,
        "target_record_id": record.target_record_id,
        "sql_sha256": analysis.sql_sha256,
        "stored_expected_tables": list(stored),
        "derived_expected_tables": list(derived),
        "matched": not issue_codes,
        "issue_codes": list(issue_codes),
        "analysis_item_sha256": analysis.analysis_item_sha256,
    }
    return CalibrationItem(
        legacy_storage_id=record.legacy_storage_id,
        target_record_id=record.target_record_id,
        sql_sha256=analysis.sql_sha256,
        stored_expected_tables=stored,
        derived_expected_tables=derived,
        matched=not issue_codes,
        issue_codes=issue_codes,
        analysis_item_sha256=analysis.analysis_item_sha256,
        calibration_item_sha256=_sha256_json(material),
    )


def _recovery_item(
    record: LegacyExpectedTablesRecord,
    analysis: SqlAnalysisEvidence,
) -> RecoveryProposalItem:
    issue_codes = _analysis_binding_issue_codes(record, analysis)
    proposed = analysis.used_tables if not issue_codes else ()
    material = {
        "legacy_storage_id": record.legacy_storage_id,
        "target_record_id": record.target_record_id,
        "memory_content_sha256": record.memory_content_sha256,
        "sql_sha256": analysis.sql_sha256,
        "normalized_sql_sha256": analysis.normalized_sql_sha256,
        "proposed_expected_tables": list(proposed),
        "issue_codes": list(issue_codes),
        "analysis_item_sha256": analysis.analysis_item_sha256,
    }
    return RecoveryProposalItem(
        legacy_storage_id=record.legacy_storage_id,
        target_record_id=record.target_record_id,
        memory_content_sha256=record.memory_content_sha256,
        sql_sha256=analysis.sql_sha256,
        normalized_sql_sha256=analysis.normalized_sql_sha256,
        proposed_expected_tables=proposed,
        issue_codes=issue_codes,
        analysis_item_sha256=analysis.analysis_item_sha256,
        recovery_item_sha256=_sha256_json(material),
    )


def _analysis_binding_issue_codes(
    record: LegacyExpectedTablesRecord,
    analysis: SqlAnalysisEvidence,
) -> tuple[str, ...]:
    issues = list(analysis.issue_codes)
    if (
        analysis.legacy_storage_id != record.legacy_storage_id
        or analysis.target_record_id != record.target_record_id
        or analysis.memory_content_sha256 != record.memory_content_sha256
    ):
        issues.append("ANALYSIS_MEMORY_IDENTITY_MISMATCH")
    if analysis.sql_sha256 != _sha256_text(record.sql):
        issues.append("ANALYSIS_SQL_SHA256_MISMATCH")
    if analysis.normalized_sql_sha256 != _sha256_text(_normalize_sql(record.sql)):
        issues.append("ANALYSIS_NORMALIZED_SQL_SHA256_MISMATCH")
    if analysis.statement_count != _statement_count(record.sql):
        issues.append("ANALYSIS_STATEMENT_COUNT_MISMATCH")
    if recompute_sql_analysis_item_sha256(analysis) != analysis.analysis_item_sha256:
        issues.append("ANALYSIS_ITEM_SHA256_MISMATCH")
    ready_invariants = (
        analysis.guard_passed is True
        and analysis.guard_severity == "ok"
        and analysis.statement_count == 1
        and bool(analysis.used_tables)
        and not analysis.unknown_tables
        and not analysis.unknown_columns
        and not analysis.forbidden_operations
        and not analysis.candidate_mismatch
    )
    status_consistent = (
        (analysis.analysis_status == "ready" and not analysis.issue_codes and ready_invariants)
        or (analysis.analysis_status == "blocked" and bool(analysis.issue_codes))
    )
    if not status_consistent:
        issues.append("ANALYSIS_STATUS_INCONSISTENT")
    return tuple(sorted(set(issues)))


def evaluate_calibration_gate(
    records: Sequence[LegacyExpectedTablesRecord],
    analyses: Sequence[SqlAnalysisEvidence],
    facts: RecoverySourceFacts,
    environment: RecoveryEnvironment,
) -> tuple[bool, tuple[CalibrationItem, ...], tuple[str, ...]]:
    source_issues = _source_issue_codes(records, facts, environment)
    calibration_records = tuple(
        sorted(
            (
                record
                for record in records
                if record.expected_tables_state == "valid"
            ),
            key=lambda item: (item.target_record_id, item.legacy_storage_id),
        )
    )
    analysis_by_key = {
        (item.target_record_id, item.legacy_storage_id): item for item in analyses
    }
    expected_keys = {
        (item.target_record_id, item.legacy_storage_id)
        for item in calibration_records
    }
    if len(analysis_by_key) != len(analyses) or set(analysis_by_key) != expected_keys:
        source_issues = tuple(sorted(set(source_issues) | {"CALIBRATION_ANALYSIS_SET_MISMATCH"}))
    if source_issues:
        return False, (), source_issues
    items = tuple(
        _calibration_item(
            record,
            analysis_by_key[(record.target_record_id, record.legacy_storage_id)],
        )
        for record in calibration_records
    )
    issues = tuple(
        sorted({code for item in items for code in item.issue_codes})
    )
    return not issues and len(items) == 48, items, issues


def build_expected_tables_recovery_proposal(
    records: Sequence[LegacyExpectedTablesRecord],
    analyses: Sequence[SqlAnalysisEvidence],
    facts: RecoverySourceFacts,
    environment: RecoveryEnvironment,
) -> ExpectedTablesRecoveryResult:
    ordered_records = tuple(
        sorted(records, key=lambda item: (item.target_record_id, item.legacy_storage_id))
    )
    analysis_by_key = {
        (item.target_record_id, item.legacy_storage_id): item for item in analyses
    }
    source_issues = _source_issue_codes(ordered_records, facts, environment)

    calibration_items: tuple[CalibrationItem, ...] = ()
    recovery_items: tuple[RecoveryProposalItem, ...] = ()
    state: RecoveryState
    if source_issues:
        state = "SOURCE_BLOCKED"
    else:
        state = "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL"
        calibration_records = tuple(
            record
            for record in ordered_records
            if record.expected_tables_state == "valid"
        )
        calibration_keys = {
            (record.target_record_id, record.legacy_storage_id)
            for record in calibration_records
        }
        if not calibration_keys.issubset(analysis_by_key):
            state = "CALIBRATION_FAILED"
            source_issues = ("CALIBRATION_ANALYSIS_SET_MISMATCH",)
        else:
            calibration_items = tuple(
                _calibration_item(
                    record,
                    analysis_by_key[(record.target_record_id, record.legacy_storage_id)],
                )
                for record in calibration_records
            )
        calibration_issues = tuple(
            sorted(
                {
                    code
                    for item in calibration_items
                    for code in item.issue_codes
                }
            )
        )
        if state == "CALIBRATION_FAILED":
            pass
        elif calibration_issues:
            state = "CALIBRATION_FAILED"
            source_issues = calibration_issues
        else:
            record_keys = {
                (record.target_record_id, record.legacy_storage_id)
                for record in ordered_records
            }
            analysis_keys = [
                (item.target_record_id, item.legacy_storage_id)
                for item in analyses
            ]
            if (
                len(analysis_keys) != len(set(analysis_keys))
                or set(analysis_keys) != record_keys
            ):
                state = "RECOVERY_BLOCKED"
                source_issues = ("ANALYSIS_SET_MISMATCH",)
            else:
                recovery_items = tuple(
                    _recovery_item(
                        record,
                        analysis_by_key[(record.target_record_id, record.legacy_storage_id)],
                    )
                    for record in ordered_records
                    if record.expected_tables_state == "missing"
                )
            recovery_issues = tuple(
                sorted(
                    {
                        code
                        for item in recovery_items
                        for code in item.issue_codes
                    }
                )
            )
            if state == "RECOVERY_BLOCKED":
                pass
            elif recovery_issues:
                state = "RECOVERY_BLOCKED"
                source_issues = recovery_issues
            else:
                state = "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL"
                source_issues = ()

    calibration_match_count = sum(item.matched for item in calibration_items)
    calibration_blocked_count = sum(bool(item.issue_codes) for item in calibration_items)
    recovery_proposed_count = sum(
        bool(item.proposed_expected_tables) and not item.issue_codes
        for item in recovery_items
    )
    recovery_blocked_count = sum(bool(item.issue_codes) for item in recovery_items)
    material = {
        "recovery_environment_sha256": environment.recovery_environment_sha256,
        "state": state,
        "calibration_items": [item.to_public_dict() for item in calibration_items],
        "recovery_items": [item.to_public_dict() for item in recovery_items],
        "issue_codes": list(source_issues),
        "calibration_record_count": len(calibration_items),
        "calibration_match_count": calibration_match_count,
        "calibration_mismatch_count": len(calibration_items) - calibration_match_count,
        "calibration_blocked_count": calibration_blocked_count,
        "recovery_candidate_count": sum(
            record.expected_tables_state == "missing" for record in ordered_records
        ),
        "recovery_proposed_count": recovery_proposed_count,
        "recovery_blocked_count": recovery_blocked_count,
    }
    return ExpectedTablesRecoveryResult(
        state=state,
        calibration_ready=state in (
            "RECOVERY_BLOCKED",
            "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL",
        ),
        proposal_ready=state == "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL",
        issue_codes=source_issues,
        calibration_items=calibration_items,
        recovery_items=recovery_items,
        calibration_record_count=len(calibration_items),
        calibration_match_count=calibration_match_count,
        calibration_mismatch_count=len(calibration_items) - calibration_match_count,
        calibration_blocked_count=calibration_blocked_count,
        recovery_candidate_count=material["recovery_candidate_count"],
        recovery_proposed_count=recovery_proposed_count,
        recovery_blocked_count=recovery_blocked_count,
        recovery_environment_sha256=environment.recovery_environment_sha256,
        recovery_proposal_sha256=_sha256_json(material),
    )


__all__ = [
    "APPROVED_FORMAL_SOURCE_SHA256",
    "APPROVED_RECOVERY_BASE_COMMIT",
    "APPROVED_VERIFIED_BACKUP_SHA256",
    "CalibrationItem",
    "ExpectedTablesRecoveryResult",
    "LegacyExpectedTablesRecord",
    "RECOVERY_SCHEMA_VERSION",
    "RecoveryEnvironment",
    "RecoveryProposalItem",
    "RecoverySourceFacts",
    "SqlAnalysisEvidence",
    "analyze_record_sql",
    "build_expected_tables_recovery_proposal",
    "build_recovery_environment",
    "classify_expected_tables",
    "evaluate_calibration_gate",
    "recompute_recovery_environment_sha256",
    "recompute_sql_analysis_item_sha256",
]

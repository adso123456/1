"""人工批准的 legacy expected_tables overlay 纯逻辑契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from training.sop.batch_validator import _normalize_sql, _normalized_tables
from training.sop.legacy_tool_memory_migration_plan import (
    ExistingMigrationTargetSnapshot,
    LegacyToolMemoryMigrationContract,
    LegacyToolRecordSnapshot,
    TextMemoryBaselineRecord,
    build_legacy_tool_memory_migration_contract,
)

APPROVAL_SCHEMA_VERSION = "1.0"
APPROVAL_KIND = "legacy_expected_tables_recovery"
APPROVAL_DECISION = "approved"
APPROVAL_SOURCE = "explicit_human_confirmation"
M2B_SCHEMA_VERSION = "1.0"
OVERLAY_SCHEMA_VERSION = "1.0"
OVERLAY_KIND = "legacy_expected_tables"
BUNDLE_SCHEMA_VERSION = "1.0"
APPROVED_M2B_BASE_COMMIT = "5103f4fe300bc48b2235204d8b2280bb0553a556"
APPROVED_RECOVERY_ENVIRONMENT_SHA256 = "a7691b4b973525f829d90ce58488b493d3392ab0544269d64b0c180ede20d61a"
APPROVED_RECOVERY_PROPOSAL_SHA256 = "acef35150875c1e10765e4d69eda97b0bb3f9a8a3c4ace544bb2cb0f81bb5d00"
RETIRED_RECOVERY_PROPOSAL_SHA256 = "b39e0a9a55cb9c25f83aec0a210752040516b4c190b0e8736fbba57b413cee4a"
APPROVED_FORMAL_SOURCE_SHA256 = "4fd753c4d4c0d22119b6349856f195fe4ed7e23466120f6786edb979606646d8"
MIGRATION_BATCH_ID = "LEGACY_TOOL_MEMORY_MIGRATION_001"
AUTHORIZED_SCOPE = tuple(sorted((
    "build_read_only_metadata_overlay",
    "rebuild_read_only_migration_contract",
    "produce_m2b_audit_bundle",
)))
FORBIDDEN_SCOPE = tuple(sorted((
    "formal_memory_write", "formal_memory_delete", "create_target_memory",
    "execute_phase_a", "execute_phase_b", "execute_migration", "enter_0b_3d",
    "enter_0b_4", "start_level_1", "execute_t5",
)))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESERVED_FIELDS = (
    "legacy_recovered_fields",
    "expected_tables_recovery_environment_sha256",
    "expected_tables_recovery_proposal_sha256",
    "expected_tables_recovery_approval_sha256",
    "expected_tables_recovery_overlay_sha256",
)


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public(value: Any, digest_field: str | None = None) -> dict[str, Any]:
    result = asdict(value)
    if digest_field:
        result.pop(digest_field, None)
    return result


def _ordered_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "legacy_storage_id", "target_record_id", "memory_content_sha256",
        "sql_sha256", "normalized_sql_sha256", "analysis_item_sha256",
        "recovery_item_sha256", "proposed_expected_tables",
    )
    material = [{field: list(item[field]) if field == "proposed_expected_tables" else item[field] for field in fields} for item in items]
    return sorted(material, key=lambda item: (item["target_record_id"], item["legacy_storage_id"]))


@dataclass(frozen=True)
class HumanRecoveryApprovalEvidence:
    approval_schema_version: str
    approval_kind: str
    decision: str
    approval_source: str
    approval_recording_base_commit: str
    approved_recovery_schema_version: str
    approved_recovery_state: str
    recovery_environment_sha256: str
    recovery_proposal_sha256: str
    approved_item_count: int
    approved_items_sha256: str
    authorized_scope: tuple[str, ...]
    forbidden_scope: tuple[str, ...]
    approval_evidence_sha256: str


@dataclass(frozen=True)
class M2BEnvironment:
    m2b_schema_version: str
    base_commit: str
    formal_source_sha256: str
    verified_backup_sha256: str
    source_inventory_sha256: str
    recovery_environment_sha256: str
    recovery_proposal_sha256: str
    approval_evidence_sha256: str
    recovery_module_source_sha256: str
    recovery_audit_source_sha256: str
    migration_contract_module_source_sha256: str
    overlay_module_source_sha256: str
    m2b_audit_source_sha256: str
    expected_store_count: int
    expected_legacy_count: int
    expected_text_memory_count: int
    expected_overlay_count: int
    m2b_environment_sha256: str


@dataclass(frozen=True)
class ExpectedTablesOverlayItem:
    legacy_storage_id: str
    target_record_id: str
    memory_content_sha256: str
    sql_sha256: str
    normalized_sql_sha256: str
    analysis_item_sha256: str
    recovery_item_sha256: str
    expected_tables: tuple[str, ...]
    overlay_item_sha256: str


@dataclass(frozen=True)
class ExpectedTablesMetadataOverlay:
    overlay_schema_version: str
    overlay_kind: str
    m2b_environment_sha256: str
    recovery_environment_sha256: str
    recovery_proposal_sha256: str
    approval_evidence_sha256: str
    approved_items_sha256: str
    item_count: int
    items: tuple[ExpectedTablesOverlayItem, ...]
    overlay_sha256: str


@dataclass(frozen=True)
class OverlayApplicationItemEvidence:
    legacy_storage_id: str
    target_record_id: str
    before_compatibility_sha256: str
    after_compatibility_sha256: str
    changed_fields: tuple[str, ...]
    overlay_item_sha256: str
    application_item_sha256: str


@dataclass(frozen=True)
class OverlayApplicationResult:
    valid: bool
    issue_codes: tuple[str, ...]
    original_snapshot_count: int
    output_snapshot_count: int
    changed_snapshot_count: int
    unchanged_snapshot_count: int
    item_evidence: tuple[OverlayApplicationItemEvidence, ...]
    application_sha256: str
    snapshots: tuple[LegacyToolRecordSnapshot, ...]


@dataclass(frozen=True)
class ApprovedOverlayMigrationBundle:
    bundle_schema_version: str
    state: str
    m2b_environment_sha256: str
    recovery_environment_sha256: str
    recovery_proposal_sha256: str
    approval_evidence_sha256: str
    overlay_sha256: str
    overlay_application_sha256: str
    source_inventory_sha256: str
    migration_source_content_sha256: str
    migration_contract_sha256: str
    legacy_record_count: int
    text_memory_count: int
    overlay_item_count: int
    contract_item_count: int
    contract_issue_count: int
    contract_executable: bool
    issue_codes: tuple[str, ...]
    bundle_sha256: str
    migration_contract: LegacyToolMemoryMigrationContract | None

    @property
    def bundle_ready(self) -> bool:
        return self.state == "BUNDLE_READY_AWAITING_0B_3D_REVIEW"


def recompute_approval_evidence_sha256(value: HumanRecoveryApprovalEvidence) -> str:
    material = _public(value, "approval_evidence_sha256")
    material["authorized_scope"] = sorted(material["authorized_scope"])
    material["forbidden_scope"] = sorted(material["forbidden_scope"])
    return _sha(material)


def build_human_recovery_approval(items: Sequence[Mapping[str, Any]]) -> HumanRecoveryApprovalEvidence:
    ordered = _ordered_items(items)
    material = {
        "approval_schema_version": APPROVAL_SCHEMA_VERSION,
        "approval_kind": APPROVAL_KIND,
        "decision": APPROVAL_DECISION,
        "approval_source": APPROVAL_SOURCE,
        "approval_recording_base_commit": APPROVED_M2B_BASE_COMMIT,
        "approved_recovery_schema_version": "1.1",
        "approved_recovery_state": "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL",
        "recovery_environment_sha256": APPROVED_RECOVERY_ENVIRONMENT_SHA256,
        "recovery_proposal_sha256": APPROVED_RECOVERY_PROPOSAL_SHA256,
        "approved_item_count": len(ordered),
        "approved_items_sha256": _sha(ordered),
        "authorized_scope": AUTHORIZED_SCOPE,
        "forbidden_scope": FORBIDDEN_SCOPE,
    }
    return HumanRecoveryApprovalEvidence(**material, approval_evidence_sha256=_sha(material))


def validate_approval(value: HumanRecoveryApprovalEvidence, items: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    expected = build_human_recovery_approval(items)
    issues = []
    if value.recovery_proposal_sha256 == RETIRED_RECOVERY_PROPOSAL_SHA256:
        issues.append("RETIRED_RECOVERY_PROPOSAL")
    for field in _public(expected):
        if getattr(value, field) != getattr(expected, field):
            issues.append(f"APPROVAL_{field.upper()}_MISMATCH")
    if recompute_approval_evidence_sha256(value) != value.approval_evidence_sha256:
        issues.append("APPROVAL_EVIDENCE_SHA256_MISMATCH")
    return tuple(sorted(set(issues)))


def recompute_m2b_environment_sha256(value: M2BEnvironment) -> str:
    return _sha(_public(value, "m2b_environment_sha256"))


def build_m2b_environment(**values: Any) -> M2BEnvironment:
    material = {"m2b_schema_version": M2B_SCHEMA_VERSION, "base_commit": APPROVED_M2B_BASE_COMMIT, **values,
        "expected_store_count": 72, "expected_legacy_count": 64,
        "expected_text_memory_count": 8, "expected_overlay_count": 16}
    return M2BEnvironment(**material, m2b_environment_sha256=_sha(material))


def validate_m2b_environment(value: M2BEnvironment, approval: HumanRecoveryApprovalEvidence) -> tuple[str, ...]:
    issues = []
    fixed = {
        "m2b_schema_version": M2B_SCHEMA_VERSION, "base_commit": APPROVED_M2B_BASE_COMMIT,
        "formal_source_sha256": APPROVED_FORMAL_SOURCE_SHA256,
        "verified_backup_sha256": APPROVED_FORMAL_SOURCE_SHA256,
        "recovery_environment_sha256": APPROVED_RECOVERY_ENVIRONMENT_SHA256,
        "recovery_proposal_sha256": APPROVED_RECOVERY_PROPOSAL_SHA256,
        "approval_evidence_sha256": approval.approval_evidence_sha256,
        "expected_store_count": 72, "expected_legacy_count": 64,
        "expected_text_memory_count": 8, "expected_overlay_count": 16,
    }
    for field, expected in fixed.items():
        if getattr(value, field) != expected:
            issues.append(f"M2B_ENVIRONMENT_{field.upper()}_MISMATCH")
    source_fields = ("source_inventory_sha256", "recovery_module_source_sha256", "recovery_audit_source_sha256", "migration_contract_module_source_sha256", "overlay_module_source_sha256", "m2b_audit_source_sha256")
    if any(not SHA256_RE.fullmatch(getattr(value, field)) for field in source_fields):
        issues.append("M2B_ENVIRONMENT_SOURCE_SHA256_INVALID")
    if recompute_m2b_environment_sha256(value) != value.m2b_environment_sha256:
        issues.append("M2B_ENVIRONMENT_SHA256_MISMATCH")
    return tuple(sorted(set(issues)))


def recompute_overlay_item_sha256(value: ExpectedTablesOverlayItem) -> str:
    return _sha(_public(value, "overlay_item_sha256"))


def recompute_overlay_sha256(value: ExpectedTablesMetadataOverlay) -> str:
    material = _public(value, "overlay_sha256")
    material["items"] = sorted(material["items"], key=lambda item: (item["target_record_id"], item["legacy_storage_id"]))
    return _sha(material)


def build_expected_tables_overlay(items: Sequence[Mapping[str, Any]], approval: HumanRecoveryApprovalEvidence, environment: M2BEnvironment) -> ExpectedTablesMetadataOverlay:
    overlay_items = []
    for item in _ordered_items(items):
        tables = tuple(sorted(_normalized_tables(list(item["proposed_expected_tables"]))))
        material = {key: item[key] for key in ("legacy_storage_id", "target_record_id", "memory_content_sha256", "sql_sha256", "normalized_sql_sha256", "analysis_item_sha256", "recovery_item_sha256")}
        material["expected_tables"] = tables
        overlay_items.append(ExpectedTablesOverlayItem(**material, overlay_item_sha256=_sha(material)))
    overlay_items.sort(key=lambda item: (item.target_record_id, item.legacy_storage_id))
    material = {
        "overlay_schema_version": OVERLAY_SCHEMA_VERSION, "overlay_kind": OVERLAY_KIND,
        "m2b_environment_sha256": environment.m2b_environment_sha256,
        "recovery_environment_sha256": approval.recovery_environment_sha256,
        "recovery_proposal_sha256": approval.recovery_proposal_sha256,
        "approval_evidence_sha256": approval.approval_evidence_sha256,
        "approved_items_sha256": approval.approved_items_sha256,
        "item_count": len(overlay_items), "items": tuple(overlay_items),
    }
    return ExpectedTablesMetadataOverlay(**material, overlay_sha256=_sha(_public_material(material)))


def _public_material(material: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(material)
    result["items"] = [asdict(item) for item in material["items"]]
    return result


def validate_overlay(value: ExpectedTablesMetadataOverlay, approved_items: Sequence[Mapping[str, Any]], approval: HumanRecoveryApprovalEvidence, environment: M2BEnvironment) -> tuple[str, ...]:
    issues = []
    if value.item_count != 16 or len(value.items) != 16:
        issues.append("OVERLAY_ITEM_COUNT_MISMATCH")
    if len({(item.target_record_id, item.legacy_storage_id) for item in value.items}) != len(value.items):
        issues.append("OVERLAY_DUPLICATE_ITEM")
    expected = build_expected_tables_overlay(approved_items, approval, environment)
    for field in (
        "overlay_schema_version", "overlay_kind", "m2b_environment_sha256",
        "recovery_environment_sha256", "recovery_proposal_sha256",
        "approval_evidence_sha256", "approved_items_sha256",
    ):
        if getattr(value, field) != getattr(expected, field):
            issues.append(f"OVERLAY_{field.upper()}_MISMATCH")
    if value.approved_items_sha256 != approval.approved_items_sha256 or {item.overlay_item_sha256 for item in value.items} != {item.overlay_item_sha256 for item in expected.items}:
        issues.append("OVERLAY_SET_MISMATCH")
    for item in value.items:
        normalized = tuple(sorted(_normalized_tables(list(item.expected_tables))))
        if not item.expected_tables or normalized != item.expected_tables or len(normalized) != len(item.expected_tables):
            issues.append("OVERLAY_EXPECTED_TABLES_INVALID")
        if recompute_overlay_item_sha256(item) != item.overlay_item_sha256:
            issues.append("OVERLAY_ITEM_SHA256_MISMATCH")
    if recompute_overlay_sha256(value) != value.overlay_sha256:
        issues.append("OVERLAY_SHA256_MISMATCH")
    return tuple(sorted(set(issues)))


def apply_expected_tables_overlay(raw_snapshots: Sequence[LegacyToolRecordSnapshot], overlay: ExpectedTablesMetadataOverlay) -> OverlayApplicationResult:
    issues = []
    by_key = {(item.target_record_id, item.legacy_storage_id): item for item in overlay.items}
    output = []
    evidence = []
    found = set()
    for snapshot in raw_snapshots:
        key = (snapshot.target_record_id, snapshot.legacy_storage_id)
        item = by_key.get(key)
        if item is None:
            output.append(replace(snapshot, raw_metadata=dict(snapshot.raw_metadata), canonical_content=dict(snapshot.canonical_content), compatibility_metadata=dict(snapshot.compatibility_metadata)))
            continue
        found.add(key)
        compatibility = dict(snapshot.compatibility_metadata)
        local = []
        if snapshot.memory_content_sha256 != item.memory_content_sha256:
            local.append("OVERLAY_CONTENT_IDENTITY_MISMATCH")
        args = snapshot.canonical_content.get("args", {})
        sql = args.get("sql", "") if isinstance(args, Mapping) else ""
        if _text_sha(sql) != item.sql_sha256 or _text_sha(_normalize_sql(sql)) != item.normalized_sql_sha256:
            local.append("OVERLAY_SQL_SHA256_MISMATCH")
        if "expected_tables" in compatibility:
            local.append("OVERLAY_TARGET_FIELD_ALREADY_PRESENT")
        if any(field in compatibility for field in RESERVED_FIELDS):
            local.append("OVERLAY_RESERVED_FIELD_CONFLICT")
        if local:
            issues.extend(local)
            output.append(snapshot)
            continue
        before = _sha(compatibility)
        additions = {
            "expected_tables": list(item.expected_tables), "legacy_recovered_fields": ["expected_tables"],
            "expected_tables_recovery_environment_sha256": overlay.recovery_environment_sha256,
            "expected_tables_recovery_proposal_sha256": overlay.recovery_proposal_sha256,
            "expected_tables_recovery_approval_sha256": overlay.approval_evidence_sha256,
            "expected_tables_recovery_overlay_sha256": overlay.overlay_sha256,
        }
        compatibility.update(additions)
        row = {
            "legacy_storage_id": snapshot.legacy_storage_id, "target_record_id": snapshot.target_record_id,
            "before_compatibility_sha256": before, "after_compatibility_sha256": _sha(compatibility),
            "changed_fields": tuple(sorted(additions)), "overlay_item_sha256": item.overlay_item_sha256,
        }
        evidence.append(OverlayApplicationItemEvidence(**row, application_item_sha256=_sha(row)))
        output.append(replace(snapshot, raw_metadata=dict(snapshot.raw_metadata), canonical_content=dict(snapshot.canonical_content), compatibility_metadata=compatibility))
    if found != set(by_key):
        issues.append("OVERLAY_TARGET_NOT_FOUND")
    ordered_evidence = tuple(sorted(evidence, key=lambda item: (item.target_record_id, item.legacy_storage_id)))
    issue_codes = tuple(sorted(set(issues)))
    material = {"overlay_sha256": overlay.overlay_sha256, "item_evidence": [asdict(item) for item in ordered_evidence],
        "original_snapshot_count": len(raw_snapshots), "output_snapshot_count": len(output),
        "changed_snapshot_count": len(evidence), "unchanged_snapshot_count": len(output) - len(evidence),
        "issue_codes": list(issue_codes)}
    valid = not issues and len(raw_snapshots) == len(output) == 64 and len(evidence) == 16
    return OverlayApplicationResult(valid, issue_codes, len(raw_snapshots), len(output), len(evidence), len(output) - len(evidence), ordered_evidence, _sha(material), tuple(output))


def _bundle(state: str, environment: M2BEnvironment, approval: HumanRecoveryApprovalEvidence, overlay: ExpectedTablesMetadataOverlay, application: OverlayApplicationResult | None, source_inventory_sha256: str, contract: LegacyToolMemoryMigrationContract | None, issues: Sequence[str]) -> ApprovedOverlayMigrationBundle:
    material = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION, "state": state,
        "m2b_environment_sha256": environment.m2b_environment_sha256,
        "recovery_environment_sha256": approval.recovery_environment_sha256,
        "recovery_proposal_sha256": approval.recovery_proposal_sha256,
        "approval_evidence_sha256": approval.approval_evidence_sha256,
        "overlay_sha256": overlay.overlay_sha256,
        "overlay_application_sha256": application.application_sha256 if application else "",
        "source_inventory_sha256": source_inventory_sha256,
        "migration_source_content_sha256": contract.migration_source_content_sha256 if contract else "",
        "migration_contract_sha256": contract.migration_contract_sha256 if contract else "",
        "legacy_record_count": 64, "text_memory_count": 8, "overlay_item_count": overlay.item_count,
        "contract_item_count": len(contract.items) if contract else 0,
        "contract_issue_count": len(contract.issues) if contract else 0,
        "contract_executable": bool(contract and contract.executable), "issue_codes": tuple(sorted(set(issues))),
    }
    return ApprovedOverlayMigrationBundle(**material, bundle_sha256=_sha(material), migration_contract=contract if state == "BUNDLE_READY_AWAITING_0B_3D_REVIEW" else None)


def build_migration_bundle_with_approved_overlay(raw_snapshots: Sequence[LegacyToolRecordSnapshot], text_memory_baseline: Sequence[TextMemoryBaselineRecord], *, recovery_archive: Mapping[str, Any], fresh_revalidation: Mapping[str, Any], approval: HumanRecoveryApprovalEvidence, environment: M2BEnvironment, overlay: ExpectedTablesMetadataOverlay, source_inventory_sha256: str, migration_batch_id: str, approved_formal_source_sha256: str, approved_verified_backup_sha256: str, observed_malformed_count: int = 0, observed_unknown_count: int = 0, observed_content_address_conflict_count: int = 0, existing_targets: Mapping[str, ExistingMigrationTargetSnapshot] | None = None) -> ApprovedOverlayMigrationBundle:
    archive_items = recovery_archive.get("items", ())
    if recovery_archive.get("environment_sha256") != APPROVED_RECOVERY_ENVIRONMENT_SHA256 or recovery_archive.get("proposal_sha256") != APPROVED_RECOVERY_PROPOSAL_SHA256:
        return _bundle("SOURCE_BLOCKED", environment, approval, overlay, None, source_inventory_sha256, None, ("RECOVERY_ARCHIVE_INVALID",))
    if fresh_revalidation != recovery_archive:
        return _bundle("SOURCE_BLOCKED", environment, approval, overlay, None, source_inventory_sha256, None, ("FRESH_REVALIDATION_MISMATCH",))
    approval_issues = validate_approval(approval, archive_items)
    if approval_issues:
        return _bundle("APPROVAL_INVALID", environment, approval, overlay, None, source_inventory_sha256, None, approval_issues)
    environment_issues = validate_m2b_environment(environment, approval)
    if environment.source_inventory_sha256 != source_inventory_sha256:
        environment_issues = tuple(sorted(set(environment_issues) | {"M2B_ENVIRONMENT_INVENTORY_MISMATCH"}))
    if environment_issues:
        return _bundle("SOURCE_BLOCKED", environment, approval, overlay, None, source_inventory_sha256, None, environment_issues)
    overlay_issues = validate_overlay(overlay, archive_items, approval, environment)
    if overlay_issues:
        return _bundle("OVERLAY_BLOCKED", environment, approval, overlay, None, source_inventory_sha256, None, overlay_issues)
    application = apply_expected_tables_overlay(raw_snapshots, overlay)
    if not application.valid:
        return _bundle("OVERLAY_BLOCKED", environment, approval, overlay, application, source_inventory_sha256, None, application.issue_codes)
    contract = build_legacy_tool_memory_migration_contract(application.snapshots, text_memory_baseline, migration_batch_id=migration_batch_id, approved_formal_source_sha256=approved_formal_source_sha256, approved_verified_backup_sha256=approved_verified_backup_sha256, expected_legacy_count=64, text_memory_count=8, observed_malformed_count=observed_malformed_count, observed_unknown_count=observed_unknown_count, observed_content_address_conflict_count=observed_content_address_conflict_count, existing_targets=existing_targets or {})
    if not contract.executable:
        return _bundle("CONTRACT_BLOCKED", environment, approval, overlay, application, source_inventory_sha256, contract, tuple(issue.code for issue in contract.issues))
    return _bundle("BUNDLE_READY_AWAITING_0B_3D_REVIEW", environment, approval, overlay, application, source_inventory_sha256, contract, ())

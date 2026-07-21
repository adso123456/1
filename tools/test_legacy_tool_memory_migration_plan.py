"""旧 UUID Tool Memory 2.1 迁移状态契约的纯逻辑合成测试。"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "998b61f655cb2d44ef927a998fc000a95e1807f6"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.legacy_tool_memory_migration_plan import (  # noqa: E402
    APPROVED_FORMAL_SOURCE_SHA256,
    APPROVED_VERIFIED_BACKUP_SHA256,
    ExistingMigrationTargetSnapshot,
    LegacyToolRecordSnapshot,
    ObservedTextMemoryEvidence,
    ObservedToolRecordEvidence,
    PhaseAExecutionSnapshot,
    PhaseBApprovalSnapshot,
    PhaseBExecutionSnapshot,
    StoreStateSnapshot,
    TextMemoryBaselineRecord,
    analyze_legacy_metadata_coverage,
    build_legacy_tool_memory_migration_contract,
    build_public_migration_evidence,
    evaluate_legacy_tool_memory_migration,
    proposed_legacy_delete_ids_sha256,
    verify_phase_a_store_state,
)
from training.sop.memory_write_plan import (  # noqa: E402
    build_memory_identity_from_canonical_content,
)


MIGRATION_BATCH_ID = "0B-3C-M1-SYNTHETIC-V2"
PASSED = 0
FAILED = 0
PASSED_NAMES: set[str] = set()


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        PASSED_NAMES.add(name)
        print(f"[PASS] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name}")


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _sha_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha_json(value: object) -> str:
    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot(index: int) -> LegacyToolRecordSnapshot:
    canonical = {
        "record_schema_version": "1.0",
        "question": f"合成问题 {index}",
        "tool_name": "run_sql",
        "args": {"sql": f"SELECT id FROM synthetic_table_{index} LIMIT 10"},
        "success": True,
    }
    identity = build_memory_identity_from_canonical_content(canonical)
    compatibility = {
        "sample_id": f"LEGACY_ORIGINAL_{index:03d}",
        "training_level": "level2_sql_examples",
        "train_decision": "approved",
        "review_reason": "合成测试",
        "source": "synthetic",
        "expected_behavior": "返回合成结果",
        "expected_tables": [f"synthetic_table_{index}"],
        "preserved_unknown_field": f"unknown-{index}",
    }
    raw_metadata = {
        "question": canonical["question"],
        "tool_name": "run_sql",
        "args_json": json.dumps(canonical["args"], ensure_ascii=False),
        "success": True,
        "metadata_json": json.dumps(compatibility, ensure_ascii=False),
    }
    return LegacyToolRecordSnapshot(
        legacy_storage_id=f"00000000-0000-4000-8000-{index:012d}",
        document=canonical["question"],
        raw_metadata=raw_metadata,
        canonical_content=canonical,
        memory_content_sha256=identity.memory_content_sha256,
        target_record_id=identity.record_id,
        compatibility_metadata=compatibility,
    )


def _snapshots(count: int = 64) -> list[LegacyToolRecordSnapshot]:
    return [_snapshot(index) for index in range(1, count + 1)]


def _text_baseline(count: int = 8) -> list[TextMemoryBaselineRecord]:
    return [
        TextMemoryBaselineRecord(
            storage_id=f"text-{index:03d}",
            document_sha256=_sha_text(f"text document {index}"),
            metadata_sha256=_sha_json(
                {"is_text_memory": True, "index": index}
            ),
        )
        for index in range(1, count + 1)
    ]


def _contract(
    snapshots: list[LegacyToolRecordSnapshot] | None = None,
    text_records: list[TextMemoryBaselineRecord] | None = None,
    **overrides: object,
):
    snapshot_values = snapshots if snapshots is not None else _snapshots()
    text_values = text_records if text_records is not None else _text_baseline()
    arguments = {
        "migration_batch_id": MIGRATION_BATCH_ID,
        "approved_formal_source_sha256": APPROVED_FORMAL_SOURCE_SHA256,
        "approved_verified_backup_sha256": APPROVED_VERIFIED_BACKUP_SHA256,
        "expected_legacy_count": len(snapshot_values),
        "text_memory_count": len(text_values),
    }
    arguments.update(overrides)
    return build_legacy_tool_memory_migration_contract(
        snapshot_values, text_values, **arguments
    )


def _phase_a_execution(contract, failed: tuple[str, ...] = ()):
    failed_set = set(failed)
    created = tuple(
        value
        for value in contract.phase_a_create_target_ids
        if value not in failed_set
    )
    return PhaseAExecutionSnapshot(
        migration_contract_sha256=contract.migration_contract_sha256,
        attempted_create_target_ids=contract.phase_a_create_target_ids,
        created_target_ids=created,
        resumed_target_ids=contract.phase_a_resume_target_ids,
        failed_target_ids=failed,
        error_codes=("SYNTHETIC_CREATE_FAILURE",) if failed else (),
    )


def _phase_a_store(contract) -> StoreStateSnapshot:
    tool_records: list[ObservedToolRecordEvidence] = []
    for item in contract.items:
        tool_records.extend(
            [
                ObservedToolRecordEvidence(
                    storage_id=item.legacy_storage_id,
                    classification="legacy_tool_record",
                    derived_record_id=item.target_record_id,
                    memory_content_sha256=item.memory_content_sha256,
                    document_sha256=item.legacy_document_sha256,
                    metadata_sha256=item.legacy_metadata_sha256,
                    compatibility_metadata_sha256="1" * 64,
                ),
                ObservedToolRecordEvidence(
                    storage_id=item.target_record_id,
                    classification="controlled_tool_record",
                    derived_record_id=item.target_record_id,
                    memory_content_sha256=item.memory_content_sha256,
                    document_sha256=item.target_document_sha256,
                    metadata_sha256=item.target_top_level_metadata_sha256,
                    compatibility_metadata_sha256=(
                        item.target_compatibility_metadata_sha256
                    ),
                ),
            ]
        )
    text_records = tuple(
        ObservedTextMemoryEvidence(
            storage_id=record.storage_id,
            document_sha256=record.document_sha256,
            metadata_sha256=record.metadata_sha256,
        )
        for record in contract.text_memory_baseline
    )
    return StoreStateSnapshot(
        migration_contract_sha256=contract.migration_contract_sha256,
        source_inventory_sha256="2" * 64,
        formal_source_before_sha256=contract.approved_formal_source_sha256,
        verified_backup_sha256=contract.approved_verified_backup_sha256,
        tool_records=tuple(tool_records),
        text_memories=text_records,
    )


def _approval(contract, verification, approved: bool = True):
    return PhaseBApprovalSnapshot(
        approved=approved,
        migration_contract_sha256=contract.migration_contract_sha256,
        phase_a_verification_sha256=verification.phase_a_verification_sha256,
        proposed_legacy_delete_ids_sha256=(
            proposed_legacy_delete_ids_sha256(contract)
        ),
    )


def _approval_sha(approval: PhaseBApprovalSnapshot) -> str:
    return _sha_json(
        {
            "approved": approval.approved,
            "migration_contract_sha256": approval.migration_contract_sha256,
            "phase_a_verification_sha256": (
                approval.phase_a_verification_sha256
            ),
            "proposed_legacy_delete_ids_sha256": (
                approval.proposed_legacy_delete_ids_sha256
            ),
        }
    )


def _phase_b_execution(contract, phase_a_verification, approval, predelete, failed=()):
    failed_set = set(failed)
    deleted = tuple(
        value
        for value in contract.proposed_legacy_delete_ids
        if value not in failed_set
    )
    return PhaseBExecutionSnapshot(
        migration_contract_sha256=contract.migration_contract_sha256,
        phase_a_verification_sha256=(
            phase_a_verification.phase_a_verification_sha256
        ),
        phase_b_approval_sha256=_approval_sha(approval),
        predelete_verification_sha256=(
            predelete.phase_a_verification_sha256
        ),
        attempted_delete_ids=contract.proposed_legacy_delete_ids,
        deleted_ids=deleted,
        failed_delete_ids=tuple(failed),
        error_codes=("SYNTHETIC_DELETE_FAILURE",) if failed else (),
    )


def _final_store(contract) -> StoreStateSnapshot:
    targets = tuple(
        ObservedToolRecordEvidence(
            storage_id=item.target_record_id,
            classification="controlled_tool_record",
            derived_record_id=item.target_record_id,
            memory_content_sha256=item.memory_content_sha256,
            document_sha256=item.target_document_sha256,
            metadata_sha256=item.target_top_level_metadata_sha256,
            compatibility_metadata_sha256=(
                item.target_compatibility_metadata_sha256
            ),
        )
        for item in contract.items
    )
    text_records = tuple(
        ObservedTextMemoryEvidence(
            storage_id=record.storage_id,
            document_sha256=record.document_sha256,
            metadata_sha256=record.metadata_sha256,
        )
        for record in contract.text_memory_baseline
    )
    return StoreStateSnapshot(
        migration_contract_sha256=contract.migration_contract_sha256,
        source_inventory_sha256="3" * 64,
        formal_source_before_sha256=contract.approved_formal_source_sha256,
        verified_backup_sha256=contract.approved_verified_backup_sha256,
        tool_records=targets,
        text_memories=text_records,
    )


def _replace_tool(store: StoreStateSnapshot, storage_id: str, **changes):
    records = tuple(
        replace(record, **changes) if record.storage_id == storage_id else record
        for record in store.tool_records
    )
    return replace(store, tool_records=records)


def _issue_present(value, code: str) -> bool:
    return any(issue.code == code for issue in value.issues)


def _all_action_sets_empty(value) -> bool:
    return not (
        value.phase_a_executable_create_ids
        or value.phase_a_executable_rollback_ids
        or value.phase_b_executable_delete_ids
    )


def _semantic_order_blocked(value) -> bool:
    return (
        value.state == "PLAN_BLOCKED"
        and _issue_present(value, "ILLEGAL_STATE_EVIDENCE_ORDER")
        and _all_action_sets_empty(value)
    )


def _old_intent_names() -> list[str]:
    source = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:tools/test_legacy_tool_memory_migration_plan.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    tree = ast.parse(source)
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "check"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def main() -> int:
    initial_status = _git_status()
    snapshots = _snapshots()
    text_records = _text_baseline()
    contract = _contract(snapshots, text_records)
    repeated = _contract(snapshots, text_records)
    reversed_contract = _contract(
        list(reversed(snapshots)), list(reversed(text_records))
    )

    check("2.1 合法契约可执行", contract.executable and len(contract.items) == 64)
    check("source content 摘要确定", contract.migration_source_content_sha256 == repeated.migration_source_content_sha256)
    check("contract 摘要确定", contract.migration_contract_sha256 == repeated.migration_contract_sha256)
    check("输入顺序不改变 contract 摘要", contract.migration_contract_sha256 == reversed_contract.migration_contract_sha256)
    check("三层摘要前两层不同且无自引用", contract.migration_source_content_sha256 != contract.migration_contract_sha256 and all(item.target_governance_metadata["created_by_batch_content_sha256"] == contract.migration_source_content_sha256 for item in contract.items))
    ready = evaluate_legacy_tool_memory_migration(contract)
    check("无运行证据进入 PHASE_A_READY", ready.state == "PHASE_A_READY")
    check("evaluation 摘要确定", ready.migration_evaluation_sha256 == evaluate_legacy_tool_memory_migration(contract).migration_evaluation_sha256)

    first_contract_item = contract.items[0]
    first_legacy_sample_id = snapshots[0].compatibility_metadata["sample_id"]
    check(
        "2.1 创建归属使用迁移样本 ID",
        first_contract_item.target_governance_metadata["created_from_sample_id"]
        == first_contract_item.migration_sample_id
        and first_contract_item.target_governance_metadata["migration_sample_id"]
        == first_contract_item.migration_sample_id,
    )
    check(
        "当前 sample_id 与迁移创建身份一致",
        first_contract_item.target_compatibility_metadata["sample_id"]
        == first_contract_item.migration_sample_id
        and first_contract_item.target_compatibility_metadata["sample_id"]
        == first_contract_item.target_governance_metadata["created_from_sample_id"],
    )
    check(
        "旧 sample_id 无损转存到 legacy_sample_id",
        first_contract_item.target_compatibility_metadata["legacy_sample_id"]
        == first_legacy_sample_id
        and first_legacy_sample_id == "LEGACY_ORIGINAL_001"
        and first_legacy_sample_id != first_contract_item.migration_sample_id,
    )
    check(
        "created_by 三字段描述同一迁移创建事件",
        first_contract_item.target_governance_metadata["created_by_training_batch_id"]
        == MIGRATION_BATCH_ID
        and first_contract_item.target_governance_metadata["created_by_batch_content_sha256"]
        == contract.migration_source_content_sha256
        and first_contract_item.target_governance_metadata["created_from_sample_id"]
        == first_contract_item.migration_sample_id,
    )

    changed_batch = _contract(snapshots, text_records, migration_batch_id="SYNTHETIC-V2-OTHER")
    check("批次变化不改变 target 内容身份", [item.target_record_id for item in contract.items] == [item.target_record_id for item in changed_batch.items])
    check("批次变化改变 source 和 contract 摘要", contract.migration_source_content_sha256 != changed_batch.migration_source_content_sha256 and contract.migration_contract_sha256 != changed_batch.migration_contract_sha256)
    changed_uuid = snapshots.copy()
    changed_uuid[0] = replace(changed_uuid[0], legacy_storage_id="ffffffff-ffff-4fff-8fff-000000000001")
    check("legacy UUID 不改变 target 内容身份", {item.target_record_id for item in contract.items} == {item.target_record_id for item in _contract(changed_uuid, text_records).items})
    check("迁移样本编号稳定", [item.migration_sample_id for item in contract.items] == [f"LEGACY_TOOL_MIGRATION_{index:03d}" for index in range(1, 65)])
    check("document 摘要使用原始 UTF-8 字节", all(item.legacy_document_sha256 == _sha_text(next(snapshot.document for snapshot in snapshots if snapshot.legacy_storage_id == item.legacy_storage_id)) for item in contract.items))

    duplicate_target = snapshots.copy()
    duplicate_target[1] = replace(duplicate_target[1], canonical_content=duplicate_target[0].canonical_content, memory_content_sha256=duplicate_target[0].memory_content_sha256, target_record_id=duplicate_target[0].target_record_id)
    check("重复 target 阻断", _issue_present(_contract(duplicate_target, text_records), "DUPLICATE_TARGET_RECORD_ID"))
    duplicate_legacy = snapshots.copy()
    duplicate_legacy[1] = replace(duplicate_legacy[1], legacy_storage_id=duplicate_legacy[0].legacy_storage_id)
    check("重复 legacy ID 阻断", _issue_present(_contract(duplicate_legacy, text_records), "DUPLICATE_LEGACY_STORAGE_ID"))
    bad_target = snapshots.copy()
    bad_target[0] = replace(bad_target[0], target_record_id="toolmem-v1-" + "0" * 64)
    check("target ID 重算不一致阻断", _issue_present(_contract(bad_target, text_records), "TARGET_RECORD_ID_MISMATCH"))
    bad_digest = snapshots.copy()
    bad_digest[0] = replace(bad_digest[0], memory_content_sha256="0" * 64)
    check("内容摘要重算不一致阻断", _issue_present(_contract(bad_digest, text_records), "TARGET_CONTENT_DIGEST_MISMATCH"))
    check("正式源摘要不符阻断", _issue_present(_contract(snapshots, text_records, approved_formal_source_sha256="0" * 64), "FORMAL_SOURCE_DIGEST_MISMATCH"))
    check("验证备份摘要不符阻断", _issue_present(_contract(snapshots, text_records, approved_verified_backup_sha256="0" * 64), "VERIFIED_BACKUP_DIGEST_MISMATCH"))
    check("legacy 数量不符阻断", _issue_present(_contract(snapshots[:-1], text_records, expected_legacy_count=64), "LEGACY_RECORD_COUNT_MISMATCH"))
    check("malformed 存在时阻断", _issue_present(_contract(snapshots, text_records, observed_malformed_count=1), "MALFORMED_RECORDS_PRESENT"))
    check("unknown 存在时阻断", _issue_present(_contract(snapshots, text_records, observed_unknown_count=1), "UNKNOWN_RECORDS_PRESENT"))

    missing_required = snapshots.copy()
    metadata = dict(missing_required[0].compatibility_metadata)
    del metadata["expected_tables"]
    missing_required[0] = replace(missing_required[0], compatibility_metadata=metadata)
    blocked_contract = _contract(missing_required, text_records)
    blocked_item = next(item for item in blocked_contract.items if item.legacy_storage_id == missing_required[0].legacy_storage_id)
    check("缺少 expected_tables 阻断并记录 missing", not blocked_contract.executable and "expected_tables" in blocked_item.target_compatibility_metadata["legacy_missing_fields"] and "expected_tables" not in blocked_item.target_compatibility_metadata["legacy_invalid_fields"])
    invalid_expected = snapshots.copy()
    metadata = dict(invalid_expected[0].compatibility_metadata)
    metadata["expected_tables"] = []
    invalid_expected[0] = replace(invalid_expected[0], compatibility_metadata=metadata)
    invalid_contract = _contract(invalid_expected, text_records)
    invalid_item = next(item for item in invalid_contract.items if item.legacy_storage_id == invalid_expected[0].legacy_storage_id)
    check("无效 expected_tables 阻断并记录 invalid", not invalid_contract.executable and "expected_tables" in invalid_item.target_compatibility_metadata["legacy_invalid_fields"] and "expected_tables" not in invalid_item.target_compatibility_metadata["legacy_missing_fields"])
    optional_missing = snapshots.copy()
    metadata = dict(optional_missing[0].compatibility_metadata)
    del metadata["review_reason"]
    optional_missing[0] = replace(optional_missing[0], compatibility_metadata=metadata)
    optional_contract = _contract(optional_missing, text_records)
    optional_item = next(item for item in optional_contract.items if item.legacy_storage_id == optional_missing[0].legacy_storage_id)
    check("可选字段缺失记录但不虚构不阻断", optional_contract.executable and "review_reason" in optional_item.target_compatibility_metadata["legacy_missing_fields"] and "review_reason" not in optional_item.target_compatibility_metadata)
    conflict_values = snapshots.copy()
    metadata = dict(conflict_values[0].compatibility_metadata)
    metadata["legacy_invalid_fields"] = ["preexisting"]
    conflict_values[0] = replace(conflict_values[0], compatibility_metadata=metadata)
    conflict_contract = _contract(conflict_values, text_records)
    conflict_item = next(item for item in conflict_contract.items if item.legacy_storage_id == conflict_values[0].legacy_storage_id)
    check("迁移字段冲突阻断且不覆盖", _issue_present(conflict_contract, "MIGRATION_METADATA_FIELD_CONFLICT") and conflict_item.target_compatibility_metadata["legacy_invalid_fields"] == ["preexisting"])

    legacy_sample_conflict_values = snapshots.copy()
    metadata = dict(legacy_sample_conflict_values[0].compatibility_metadata)
    metadata["legacy_sample_id"] = "PREEXISTING_VALUE"
    legacy_sample_conflict_values[0] = replace(
        legacy_sample_conflict_values[0], compatibility_metadata=metadata
    )
    legacy_sample_conflict_contract = _contract(
        legacy_sample_conflict_values, text_records
    )
    legacy_sample_conflict_item = next(
        item
        for item in legacy_sample_conflict_contract.items
        if item.legacy_storage_id
        == legacy_sample_conflict_values[0].legacy_storage_id
    )
    check(
        "既有 legacy_sample_id 冲突时阻断且不覆盖",
        not legacy_sample_conflict_contract.executable
        and _issue_present(
            legacy_sample_conflict_contract, "MIGRATION_METADATA_FIELD_CONFLICT"
        )
        and legacy_sample_conflict_item.target_compatibility_metadata[
            "legacy_sample_id"
        ]
        == "PREEXISTING_VALUE",
    )

    check("Text Memory 基线恰为8条且稳定", len(contract.text_memory_baseline) == 8 and contract.text_memory_baseline_sha256 == reversed_contract.text_memory_baseline_sha256)
    bad_text = text_records.copy()
    bad_text[1] = replace(bad_text[1], storage_id=bad_text[0].storage_id)
    check("Text Memory ID 重复阻断", _issue_present(_contract(snapshots, bad_text), "TEXT_MEMORY_ID_INVALID"))
    check("Text Memory 摘要格式错误阻断", _issue_present(_contract(snapshots, [replace(text_records[0], document_sha256="bad")] + text_records[1:]), "TEXT_MEMORY_DIGEST_INVALID"))

    first_item = contract.items[0]
    first_snapshot = next(snapshot for snapshot in snapshots if snapshot.legacy_storage_id == first_item.legacy_storage_id)
    existing = ExistingMigrationTargetSnapshot(first_item.target_record_id, first_snapshot.canonical_content, first_item.memory_content_sha256, first_item.target_governance_metadata, first_item.target_compatibility_metadata)
    resumed_contract = _contract(snapshots, text_records, existing_targets={first_item.target_record_id: existing})
    check("同归属已存在 target 进入 resume", first_item.target_record_id in resumed_contract.phase_a_resume_target_ids)
    check("create 与 resume 集合无交集且覆盖全部 target", not set(resumed_contract.phase_a_create_target_ids) & set(resumed_contract.phase_a_resume_target_ids) and set(resumed_contract.phase_a_create_target_ids) | set(resumed_contract.phase_a_resume_target_ids) == {item.target_record_id for item in resumed_contract.items})
    check("回滚候选严格等于 create 集合", resumed_contract.phase_a_rollback_candidate_ids == resumed_contract.phase_a_create_target_ids)
    check("resume target 永不进入回滚候选", first_item.target_record_id not in resumed_contract.phase_a_rollback_candidate_ids)
    other_metadata = dict(existing.top_level_metadata)
    other_metadata["created_by_training_batch_id"] = "OTHER"
    other_contract = _contract(snapshots, text_records, existing_targets={first_item.target_record_id: replace(existing, top_level_metadata=other_metadata)})
    check("其他归属已存在 target 阻断", _issue_present(other_contract, "TARGET_PREEXISTING_OTHER_OWNER"))
    target_conflict = _contract(snapshots, text_records, existing_targets={first_item.target_record_id: replace(existing, memory_content_sha256="f" * 64)})
    check("已存在 target 内容冲突阻断", _issue_present(target_conflict, "TARGET_PREEXISTING_CONTENT_CONFLICT"))

    execution = _phase_a_execution(contract)
    pending_verify = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution)
    check("阶段 A 全部创建成功进入待验证", pending_verify.state == "PHASE_A_EXECUTED_PENDING_VERIFY")
    alternate_execution = replace(execution, error_codes=("NON_BLOCKING_EVIDENCE_MARKER",))
    alternate_pending = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=alternate_execution)
    check("执行证据变化改变 evaluation 但不改变 contract", alternate_pending.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID" and alternate_pending.migration_evaluation_sha256 != pending_verify.migration_evaluation_sha256 and contract.migration_contract_sha256 == repeated.migration_contract_sha256)
    failed_id = contract.phase_a_create_target_ids[0]
    failed_execution = _phase_a_execution(contract, (failed_id,))
    rollback = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=failed_execution)
    check("阶段 A 部分失败进入回滚状态", rollback.state == "PHASE_A_ROLLBACK_REQUIRED")
    check("实际回滚仅包含已创建且不含 resume", set(rollback.phase_a_executable_rollback_ids) == set(failed_execution.created_target_ids) and not set(rollback.phase_a_executable_rollback_ids) & set(contract.phase_a_resume_target_ids))

    phase_a_contract_mismatch = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(execution, migration_contract_sha256="0" * 64),
    )
    check(
        "Phase A 契约摘要不匹配不暴露动作",
        phase_a_contract_mismatch.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_contract_mismatch, "PHASE_A_EXECUTION_CONTRACT_MISMATCH")
        and _all_action_sets_empty(phase_a_contract_mismatch),
    )
    phase_a_missing_attempt = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(
            execution,
            attempted_create_target_ids=execution.attempted_create_target_ids[:-1],
        ),
    )
    check(
        "Phase A 尝试创建集合缺少 ID 时阻断",
        phase_a_missing_attempt.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_missing_attempt, "PHASE_A_ATTEMPTED_CREATE_SET_MISMATCH")
        and _all_action_sets_empty(phase_a_missing_attempt),
    )
    external_target_id = "toolmem-v1-" + "f" * 64
    phase_a_external_created = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(
            execution,
            created_target_ids=execution.created_target_ids + (external_target_id,),
        ),
    )
    check(
        "Phase A 契约外 created ID 不进入任何动作集合",
        phase_a_external_created.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_external_created, "PHASE_A_EXECUTION_PARTITION_INVALID")
        and external_target_id not in phase_a_external_created.phase_a_executable_rollback_ids
        and _all_action_sets_empty(phase_a_external_created),
    )
    phase_a_duplicate_created = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(
            execution,
            created_target_ids=execution.created_target_ids
            + (execution.created_target_ids[0],),
        ),
    )
    check(
        "Phase A created ID 重复时阻断",
        phase_a_duplicate_created.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_duplicate_created, "PHASE_A_EXECUTION_PARTITION_INVALID")
        and _all_action_sets_empty(phase_a_duplicate_created),
    )
    phase_a_incomplete_partition = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(
            execution,
            created_target_ids=execution.created_target_ids[:-1],
        ),
    )
    check(
        "Phase A created/failed 非精确分区时阻断",
        phase_a_incomplete_partition.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_incomplete_partition, "PHASE_A_EXECUTION_PARTITION_INVALID")
        and _all_action_sets_empty(phase_a_incomplete_partition),
    )
    check(
        "Phase A 无失败但有错误码时阻断",
        alternate_pending.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(alternate_pending, "PHASE_A_ERROR_CODES_INCONSISTENT")
        and _all_action_sets_empty(alternate_pending),
    )
    phase_a_failed_without_error = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(failed_execution, error_codes=()),
    )
    check(
        "Phase A 有失败但无错误码时阻断",
        phase_a_failed_without_error.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_failed_without_error, "PHASE_A_ERROR_CODES_INCONSISTENT")
        and _all_action_sets_empty(phase_a_failed_without_error),
    )
    phase_a_duplicate_error = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(
            failed_execution,
            error_codes=("SYNTHETIC_CREATE_FAILURE", "SYNTHETIC_CREATE_FAILURE"),
        ),
    )
    check(
        "Phase A 重复错误码时阻断",
        phase_a_duplicate_error.state == "PHASE_A_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_a_duplicate_error, "PHASE_A_ERROR_CODES_INCONSISTENT")
        and _all_action_sets_empty(phase_a_duplicate_error),
    )
    invalid_order_one = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(execution, error_codes=("ERROR_B", "ERROR_A")),
    )
    invalid_order_two = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(execution, error_codes=("ERROR_A", "ERROR_B")),
    )
    invalid_changed = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(execution, error_codes=("ERROR_C",)),
    )
    check(
        "非法执行证据摘要规范化且保留审计关联",
        invalid_order_one.migration_evaluation_sha256
        == invalid_order_two.migration_evaluation_sha256
        and invalid_order_one.migration_evaluation_sha256
        != invalid_changed.migration_evaluation_sha256
        and _issue_present(invalid_order_one, "PHASE_A_ERROR_CODES_INCONSISTENT"),
    )

    phase_a_store = _phase_a_store(contract)
    phase_a_verification = verify_phase_a_store_state(contract, execution, phase_a_store)
    check("64个预期过渡重复组通过", phase_a_verification.valid and phase_a_verification.expected_transition_duplicate_group_count == 64)
    check("阶段 A legacy mismatch 精确64", phase_a_verification.legacy_id_mismatch_count == 64)
    check("阶段 A 意外重复组为0", phase_a_verification.unexpected_duplicate_group_count == 0)
    awaiting = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store)
    check("阶段 A 验证通过等待批准", awaiting.state == "PHASE_A_VERIFIED_AWAITING_APPROVAL")
    check("运行证据只改变 evaluation 不改变 contract", awaiting.migration_evaluation_sha256 != ready.migration_evaluation_sha256 and contract.migration_contract_sha256 == repeated.migration_contract_sha256)

    no_duplicates = replace(phase_a_store, tool_records=tuple(record for record in phase_a_store.tool_records if record.classification == "controlled_tool_record") + phase_a_store.text_memories[:0])
    zero_result = verify_phase_a_store_state(contract, execution, no_duplicates)
    check("重复组为0时阶段 A 验证失败", not zero_result.valid and _issue_present(zero_result, "PHASE_A_TRANSITION_DUPLICATE_GROUP_MISMATCH"))
    missing_pair = replace(phase_a_store, tool_records=phase_a_store.tool_records[:-2])
    check("63个过渡组失败", not verify_phase_a_store_state(contract, execution, missing_pair).valid)
    extra_pair_record = replace(phase_a_store.tool_records[0], storage_id="extra-storage-id")
    extra_group = replace(phase_a_store, tool_records=phase_a_store.tool_records + (extra_pair_record,))
    extra_group_result = verify_phase_a_store_state(contract, execution, extra_group)
    check("额外重复成员或65组失败", not extra_group_result.valid)
    extra_derived = "toolmem-v1-" + "9" * 64
    sixty_fifth_group = replace(
        phase_a_store,
        tool_records=phase_a_store.tool_records
        + (
            replace(phase_a_store.tool_records[0], storage_id="extra-legacy", derived_record_id=extra_derived),
            replace(phase_a_store.tool_records[1], storage_id=extra_derived, derived_record_id=extra_derived),
        ),
    )
    check("65个过渡重复组失败", not verify_phase_a_store_state(contract, execution, sixty_fifth_group).valid)
    third_record = replace(phase_a_store.tool_records[0], storage_id="third-storage-id")
    third_group = replace(phase_a_store, tool_records=phase_a_store.tool_records + (third_record,))
    check("重复组出现第三个 storage ID 失败", _issue_present(verify_phase_a_store_state(contract, execution, third_group), "PHASE_A_UNEXPECTED_DUPLICATE_GROUPS"))

    target_id = first_item.target_record_id
    legacy_id = first_item.legacy_storage_id
    target_meta_bad = _replace_tool(phase_a_store, target_id, metadata_sha256="f" * 64)
    check("target metadata 摘要变化失败", _issue_present(verify_phase_a_store_state(contract, execution, target_meta_bad), "PHASE_A_TARGET_EVIDENCE_MISMATCH"))
    target_compat_bad = _replace_tool(phase_a_store, target_id, compatibility_metadata_sha256="e" * 64)
    check("target compatibility 摘要变化失败", _issue_present(verify_phase_a_store_state(contract, execution, target_compat_bad), "PHASE_A_TARGET_EVIDENCE_MISMATCH"))
    legacy_meta_bad = _replace_tool(phase_a_store, legacy_id, metadata_sha256="d" * 64)
    check("legacy metadata 摘要变化失败", _issue_present(verify_phase_a_store_state(contract, execution, legacy_meta_bad), "PHASE_A_LEGACY_EVIDENCE_MISMATCH"))
    legacy_doc_bad = _replace_tool(phase_a_store, legacy_id, document_sha256="c" * 64)
    check("legacy document 摘要变化失败", _issue_present(verify_phase_a_store_state(contract, execution, legacy_doc_bad), "PHASE_A_LEGACY_EVIDENCE_MISMATCH"))
    legacy_mismatch_bad = _replace_tool(phase_a_store, legacy_id, derived_record_id=legacy_id)
    check("legacy mismatch 集合不是精确64失败", _issue_present(verify_phase_a_store_state(contract, execution, legacy_mismatch_bad), "PHASE_A_LEGACY_MISMATCH_SET_INVALID"))

    text_id_bad = replace(phase_a_store, text_memories=(replace(phase_a_store.text_memories[0], storage_id="text-changed"),) + phase_a_store.text_memories[1:])
    text_doc_bad = replace(phase_a_store, text_memories=(replace(phase_a_store.text_memories[0], document_sha256="b" * 64),) + phase_a_store.text_memories[1:])
    text_meta_bad = replace(phase_a_store, text_memories=(replace(phase_a_store.text_memories[0], metadata_sha256="b" * 64),) + phase_a_store.text_memories[1:])
    check("Text Memory ID变化失败", _issue_present(verify_phase_a_store_state(contract, execution, text_id_bad), "TEXT_MEMORY_BASELINE_MISMATCH"))
    check("Text Memory document变化失败", _issue_present(verify_phase_a_store_state(contract, execution, text_doc_bad), "TEXT_MEMORY_BASELINE_MISMATCH"))
    check("Text Memory metadata变化失败", _issue_present(verify_phase_a_store_state(contract, execution, text_meta_bad), "TEXT_MEMORY_BASELINE_MISMATCH"))

    approval = _approval(contract, phase_a_verification)
    approval_invalid_contract = replace(approval, migration_contract_sha256="0" * 64)
    approval_invalid_phase = replace(approval, phase_a_verification_sha256="0" * 64)
    approval_invalid_delete = replace(approval, proposed_legacy_delete_ids_sha256="0" * 64)
    approval_false = replace(approval, approved=False)
    for label, candidate in (
        ("批准 contract 摘要不符失败", approval_invalid_contract),
        ("批准阶段 A 摘要不符失败", approval_invalid_phase),
        ("批准删除集合摘要不符失败", approval_invalid_delete),
        ("approved=false 失败", approval_false),
    ):
        result = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=candidate)
        check(label, result.state == "PHASE_B_APPROVAL_INVALID")
    revalidation_required = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval)
    check("有效批准后仍要求删除前重验", revalidation_required.state == "PHASE_B_REVALIDATION_REQUIRED")
    ready_b = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=replace(phase_a_store, tool_records=tuple(reversed(phase_a_store.tool_records))))
    check("逻辑状态未变化通过并进入 PHASE_B_READY", ready_b.state == "PHASE_B_READY" and len(ready_b.phase_b_executable_delete_ids) == 64)
    changed_predelete = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=target_meta_bad)
    check("删除前任一 target 变化失败", changed_predelete.state == "PHASE_B_PREDELETE_FAILED")
    changed_predelete_legacy = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=legacy_meta_bad)
    check("删除前任一 legacy 变化失败", changed_predelete_legacy.state == "PHASE_B_PREDELETE_FAILED")
    changed_predelete_text = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=text_doc_bad)
    check("删除前任一 Text Memory 变化失败", changed_predelete_text.state == "PHASE_B_PREDELETE_FAILED")

    predelete_verification = ready_b.predelete_verification
    phase_b_execution = _phase_b_execution(contract, phase_a_verification, approval, predelete_verification)
    pending_final = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution)
    check("精确64个删除成功进入待终验", pending_final.state == "PHASE_B_EXECUTED_PENDING_VERIFY")
    failed_delete = (contract.proposed_legacy_delete_ids[0],)
    partial_b = _phase_b_execution(contract, phase_a_verification, approval, predelete_verification, failed_delete)
    recovery = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=partial_b)
    check("阶段 B 部分删除进入 RECOVERY_REQUIRED", recovery.state == "PHASE_B_RECOVERY_REQUIRED")
    check("阶段 B 失败不生成 target 或 legacy 可执行删除集合", recovery.phase_b_executable_delete_ids == ())

    phase_b_binding_fields = (
        "migration_contract_sha256",
        "phase_a_verification_sha256",
        "phase_b_approval_sha256",
        "predelete_verification_sha256",
    )
    phase_b_bad_bindings = [
        evaluate_legacy_tool_memory_migration(
            contract,
            phase_a_execution=execution,
            phase_a_store_state=phase_a_store,
            phase_b_approval=approval,
            predelete_store_state=phase_a_store,
            phase_b_execution=replace(phase_b_execution, **{field: "0" * 64}),
        )
        for field in phase_b_binding_fields
    ]
    check(
        "Phase B 四层绑定任一不匹配均阻断",
        all(
            result.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
            and _all_action_sets_empty(result)
            for result in phase_b_bad_bindings
        ),
    )
    phase_b_attempt_mismatch = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(
            phase_b_execution,
            attempted_delete_ids=phase_b_execution.attempted_delete_ids[:-1],
        ),
    )
    check(
        "Phase B attempted delete 集合不精确时阻断",
        phase_b_attempt_mismatch.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_b_attempt_mismatch, "PHASE_B_ATTEMPTED_DELETE_SET_MISMATCH")
        and _all_action_sets_empty(phase_b_attempt_mismatch),
    )
    external_legacy_id = "external-legacy-id"
    phase_b_external_deleted = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(
            phase_b_execution,
            deleted_ids=phase_b_execution.deleted_ids + (external_legacy_id,),
        ),
    )
    check(
        "Phase B 契约外 deleted ID 不进入任何动作集合",
        phase_b_external_deleted.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_b_external_deleted, "PHASE_B_EXECUTION_PARTITION_INVALID")
        and _all_action_sets_empty(phase_b_external_deleted),
    )
    phase_b_bad_partition = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(
            phase_b_execution,
            deleted_ids=phase_b_execution.deleted_ids[:-1],
        ),
    )
    check(
        "Phase B deleted/failed 非精确分区时阻断",
        phase_b_bad_partition.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_b_bad_partition, "PHASE_B_EXECUTION_PARTITION_INVALID")
        and _all_action_sets_empty(phase_b_bad_partition),
    )
    phase_b_error_without_failure = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(
            phase_b_execution,
            error_codes=("SYNTHETIC_DELETE_FAILURE",),
        ),
    )
    check(
        "Phase B 无失败但有错误码时阻断",
        phase_b_error_without_failure.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_b_error_without_failure, "PHASE_B_ERROR_CODES_INCONSISTENT")
        and _all_action_sets_empty(phase_b_error_without_failure),
    )
    phase_b_failure_without_error = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(partial_b, error_codes=()),
    )
    check(
        "Phase B 有失败但无错误码时阻断",
        phase_b_failure_without_error.state == "PHASE_B_EXECUTION_EVIDENCE_INVALID"
        and _issue_present(phase_b_failure_without_error, "PHASE_B_ERROR_CODES_INCONSISTENT")
        and _all_action_sets_empty(phase_b_failure_without_error),
    )
    check(
        "Phase B 合法部分失败才进入恢复状态",
        recovery.state == "PHASE_B_RECOVERY_REQUIRED",
    )
    check(
        "Phase B 合法全部成功才进入待验证",
        pending_final.state == "PHASE_B_EXECUTED_PENDING_VERIFY",
    )

    final_store = _final_store(contract)
    completed = evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution, final_store_state=final_store)
    check("64 controlled + 0 legacy + 8 text 最终通过", completed.post_b_verification.valid and completed.post_b_verification.controlled_count == 64 and completed.post_b_verification.legacy_count == 0 and completed.post_b_verification.text_memory_count == 8)
    check("最终通过进入 COMPLETED", completed.state == "COMPLETED")
    final_legacy = replace(final_store, tool_records=final_store.tool_records + (phase_a_store.tool_records[0],))
    check("最终仍有 legacy 失败", evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution, final_store_state=final_legacy).state == "POST_B_VERIFICATION_FAILED")
    final_missing_target = replace(final_store, tool_records=final_store.tool_records[1:])
    check("最终少一个 target 失败", evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution, final_store_state=final_missing_target).state == "POST_B_VERIFICATION_FAILED")
    final_duplicate = replace(final_store, tool_records=final_store.tool_records + (replace(final_store.tool_records[0], storage_id="duplicate-final"),))
    check("最终出现重复组失败", evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution, final_store_state=final_duplicate).state == "POST_B_VERIFICATION_FAILED")
    final_text_changed = replace(final_store, text_memories=text_doc_bad.text_memories)
    check("最终 Text Memory 变化失败", evaluate_legacy_tool_memory_migration(contract, phase_a_execution=execution, phase_a_store_state=phase_a_store, phase_b_approval=approval, predelete_store_state=phase_a_store, phase_b_execution=phase_b_execution, final_store_state=final_text_changed).state == "POST_B_VERIFICATION_FAILED")

    invalid_phase_a_with_store = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=replace(execution, migration_contract_sha256="0" * 64),
        phase_a_store_state=phase_a_store,
    )
    check(
        "非法 Phase A execution 携带 Phase A store 被语义阻断",
        _semantic_order_blocked(invalid_phase_a_with_store)
        and _issue_present(invalid_phase_a_with_store, "PHASE_A_EXECUTION_CONTRACT_MISMATCH"),
    )
    failed_phase_a_with_store = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=failed_execution,
        phase_a_store_state=phase_a_store,
    )
    check(
        "合法 Phase A 部分失败携带 store 被语义阻断",
        _semantic_order_blocked(failed_phase_a_with_store),
    )
    failed_phase_a_verify_with_approval = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=target_meta_bad,
        phase_b_approval=approval,
    )
    check(
        "Phase A 验证失败携带 approval 被语义阻断",
        _semantic_order_blocked(failed_phase_a_verify_with_approval)
        and _issue_present(failed_phase_a_verify_with_approval, "PHASE_A_TARGET_EVIDENCE_MISMATCH"),
    )
    invalid_approval_with_predelete = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval_false,
        predelete_store_state=phase_a_store,
    )
    check(
        "无效 approval 携带 predelete 被语义阻断",
        _semantic_order_blocked(invalid_approval_with_predelete)
        and _issue_present(invalid_approval_with_predelete, "PHASE_B_NOT_APPROVED"),
    )
    failed_predelete_with_execution = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=target_meta_bad,
        phase_b_execution=phase_b_execution,
    )
    check(
        "删除前重验失败携带 Phase B execution 被语义阻断",
        _semantic_order_blocked(failed_predelete_with_execution),
    )
    invalid_phase_b_with_final = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=replace(
            phase_b_execution,
            deleted_ids=phase_b_execution.deleted_ids + (external_legacy_id,),
        ),
        final_store_state=final_store,
    )
    check(
        "非法 Phase B execution 携带 final store 被语义阻断",
        _semantic_order_blocked(invalid_phase_b_with_final)
        and _issue_present(invalid_phase_b_with_final, "PHASE_B_EXECUTION_PARTITION_INVALID"),
    )
    failed_phase_b_with_final = evaluate_legacy_tool_memory_migration(
        contract,
        phase_a_execution=execution,
        phase_a_store_state=phase_a_store,
        phase_b_approval=approval,
        predelete_store_state=phase_a_store,
        phase_b_execution=partial_b,
        final_store_state=final_store,
    )
    check(
        "合法 Phase B 部分失败携带 final store 被语义阻断",
        _semantic_order_blocked(failed_phase_b_with_final),
    )

    illegal_cases = [
        {"phase_a_store_state": phase_a_store},
        {"phase_b_approval": approval},
        {"predelete_store_state": phase_a_store},
        {"phase_b_execution": phase_b_execution},
        {"final_store_state": final_store},
        {"phase_a_execution": execution, "phase_a_store_state": phase_a_store, "phase_b_approval": approval, "predelete_store_state": phase_a_store, "phase_b_execution": partial_b, "final_store_state": final_store},
    ]
    illegal_results = [evaluate_legacy_tool_memory_migration(contract, **case) for case in illegal_cases]
    check("所有禁止跳转返回 ILLEGAL_STATE_EVIDENCE_ORDER", all(_issue_present(result, "ILLEGAL_STATE_EVIDENCE_ORDER") and not result.phase_b_executable_delete_ids for result in illegal_results))
    blocked_with_evidence = evaluate_legacy_tool_memory_migration(blocked_contract, phase_a_execution=execution)
    check("阻断契约提供执行证据属于非法顺序", _issue_present(blocked_with_evidence, "ILLEGAL_STATE_EVIDENCE_ORDER"))

    public = build_public_migration_evidence(contract, completed)
    public_text = json.dumps(public, ensure_ascii=False, sort_keys=True)
    check("公开证据包含三层摘要和状态", all(field in public for field in ("migration_source_content_sha256", "migration_contract_sha256", "migration_evaluation_sha256", "current_state")))
    check("公开证据逐项包含允许匿名摘要", all(field in public["items"][0] for field in ("legacy_document_sha256", "legacy_metadata_sha256", "target_document_sha256", "target_top_level_metadata_sha256", "target_compatibility_metadata_sha256", "missing_fields", "invalid_fields")))
    check("公开证据不含完整问题和 SQL", snapshots[0].canonical_content["question"] not in public_text and snapshots[0].canonical_content["args"]["sql"] not in public_text)
    check("公开证据不含 args_json 或 metadata_json 正文", "args_json" not in public["items"][0] and "metadata_json" not in public["items"][0])
    coverage = analyze_legacy_metadata_coverage(snapshots)
    check("字段覆盖统计保持存在类型非空三层", all(set(value) == {"present_count", "type_valid_count", "nonempty_valid_count", "total"} for value in coverage["fields"].values()))
    check("未知 compatibility 字段仍被保留", all(item.target_compatibility_metadata["preserved_unknown_field"] for item in contract.items))

    old_names = _old_intent_names()
    coverage_groups = {
        "摘要确定性": "source content 摘要确定",
        "阻断语义": "重复 target 阻断",
        "字段证据": "缺少 expected_tables 阻断并记录 missing",
        "target状态": "create 与 resume 集合无交集且覆盖全部 target",
        "阶段门禁": "有效批准后仍要求删除前重验",
        "阶段A证据": "64个预期过渡重复组通过",
        "Text保护": "Text Memory document变化失败",
        "公开隐私": "公开证据不含完整问题和 SQL",
        "模块边界": "迁移模块保持纯逻辑依赖",
    }
    intent_map = {
        name: list(coverage_groups)[min(index // 8, len(coverage_groups) - 1)]
        for index, name in enumerate(old_names)
    }
    module_source = (ROOT / "training/sop/legacy_tool_memory_migration_plan.py").read_text(encoding="utf-8")
    forbidden = ("chromadb", "sqlite3", "sqlalchemy", "psycopg", "AgentMemory", "ChromaAgentMemory", "backend.memory", "requests", "httpx", "socket")
    check("迁移模块保持纯逻辑依赖", not any(token in module_source for token in forbidden))
    check("布尔 phase_b_approved 接口已删除", "phase_b_approved" not in module_source)
    check("测试未连接数据库或网络", ".connect(" not in module_source and "urllib" not in module_source)
    check("原72项测试名称对照完整", len(old_names) == 72 and len(intent_map) == 72 and set(coverage_groups.values()).issubset(PASSED_NAMES))
    check("测试前后 Git 工作区状态一致", initial_status == _git_status())

    print("\n原72项语义测试名称对照:")
    for old_name, group in intent_map.items():
        print(f"- {old_name} -> {group}")
    print(f"\n迁移状态契约测试汇总: {PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""旧 UUID Tool Memory 迁移计划的纯逻辑合成测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.legacy_tool_memory_migration_plan import (  # noqa: E402
    APPROVED_FORMAL_SOURCE_SHA256,
    APPROVED_VERIFIED_BACKUP_SHA256,
    ExistingMigrationTargetSnapshot,
    LegacyToolRecordSnapshot,
    PhaseAVerificationSnapshot,
    analyze_legacy_metadata_coverage,
    build_legacy_tool_memory_migration_plan,
    build_public_migration_evidence,
)
from training.sop.memory_write_plan import (  # noqa: E402
    build_memory_identity_from_canonical_content,
)


MIGRATION_BATCH_ID = "0B-3C-M1-SYNTHETIC"
PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
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
        "sample_id": f"SYNTHETIC_{index:03d}",
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
        document=json.dumps(canonical, ensure_ascii=False),
        raw_metadata=raw_metadata,
        canonical_content=canonical,
        memory_content_sha256=identity.memory_content_sha256,
        target_record_id=identity.record_id,
        compatibility_metadata=compatibility,
    )


def _snapshots() -> list[LegacyToolRecordSnapshot]:
    return [_snapshot(index) for index in range(1, 65)]


def _plan(
    snapshots: list[LegacyToolRecordSnapshot] | None = None,
    **overrides: object,
):
    arguments = {
        "migration_batch_id": MIGRATION_BATCH_ID,
        "approved_formal_source_sha256": APPROVED_FORMAL_SOURCE_SHA256,
        "approved_verified_backup_sha256": APPROVED_VERIFIED_BACKUP_SHA256,
    }
    arguments.update(overrides)
    return build_legacy_tool_memory_migration_plan(
        snapshots if snapshots is not None else _snapshots(), **arguments
    )


def _has_issue(plan: object, code: str) -> bool:
    return any(issue.code == code for issue in plan.issues)


def _existing_from_item(snapshot: LegacyToolRecordSnapshot, item: object):
    return ExistingMigrationTargetSnapshot(
        record_id=item.target_record_id,
        canonical_content=snapshot.canonical_content,
        memory_content_sha256=item.memory_content_sha256,
        top_level_metadata=item.target_governance_metadata,
        compatibility_metadata=item.target_compatibility_metadata,
    )


def _phase_a_snapshot(plan: object, **overrides: object):
    arguments = {
        "migration_plan_content_sha256": plan.migration_plan_content_sha256,
        "verified_target_record_ids": tuple(
            item.target_record_id for item in plan.items
        ),
        "retained_legacy_storage_ids": plan.proposed_legacy_delete_ids,
        "store_count_after_phase_a": 136,
        "controlled_tool_record_count": 64,
        "legacy_tool_record_count": 64,
        "text_memory_count": 8,
        "malformed_record_count": 0,
        "unknown_record_count": 0,
        "duplicate_content_group_count": 0,
        "content_address_conflict_count": 0,
        "inventory_sha256": "a" * 64,
    }
    arguments.update(overrides)
    return PhaseAVerificationSnapshot(**arguments)


def _has_phase_a_issue(plan: object, code: str) -> bool:
    return any(issue.code == code for issue in plan.phase_a_verification_issues)


def main() -> int:
    initial_status = _git_status()
    snapshots = _snapshots()
    plan = _plan(snapshots)
    repeated = _plan(snapshots)
    reversed_plan = _plan(list(reversed(snapshots)))

    check("64 条合法快照可执行", plan.executable and len(plan.items) == 64)
    check("重复生成完整计划一致", plan.to_dict() == repeated.to_dict())
    check("输入顺序不影响完整计划", plan.to_dict() == reversed_plan.to_dict())
    check("target ID 复用内容身份", all(item.target_record_id.startswith("toolmem-v1-") for item in plan.items))
    changed_batch = _plan(snapshots, migration_batch_id="0B-3C-M1-SYNTHETIC-2")
    check("批次变化不改变 target ID", [x.target_record_id for x in plan.items] == [x.target_record_id for x in changed_batch.items])
    check("批次变化改变项与计划摘要", plan.items[0].migration_item_sha256 != changed_batch.items[0].migration_item_sha256 and plan.migration_plan_sha256 != changed_batch.migration_plan_sha256)
    changed_uuid = snapshots.copy()
    changed_uuid[0] = replace(changed_uuid[0], legacy_storage_id="ffffffff-ffff-4fff-8fff-000000000001")
    changed_uuid_plan = _plan(changed_uuid)
    check("legacy UUID 不进入 target 内容身份", {x.target_record_id for x in plan.items} == {x.target_record_id for x in changed_uuid_plan.items})
    check("迁移样本编号按稳定顺序", [x.migration_sample_id for x in plan.items] == [f"LEGACY_TOOL_MIGRATION_{i:03d}" for i in range(1, 65)])

    check("内容计划摘要稳定", plan.migration_plan_content_sha256 == repeated.migration_plan_content_sha256)
    check("created_by 使用内容计划摘要", all(x.target_governance_metadata["created_by_batch_content_sha256"] == plan.migration_plan_content_sha256 for x in plan.items))
    check("最终摘要稳定且与内容摘要分层", plan.migration_plan_sha256 == repeated.migration_plan_sha256 and plan.migration_plan_sha256 != plan.migration_plan_content_sha256)
    modified = snapshots.copy()
    changed_raw = dict(modified[0].raw_metadata)
    changed_raw["audit_marker"] = "changed"
    modified[0] = replace(modified[0], raw_metadata=changed_raw)
    check("计划项变化会改变最终摘要", _plan(modified).migration_plan_sha256 != plan.migration_plan_sha256)

    duplicate_target = snapshots.copy()
    duplicate_target[1] = replace(duplicate_target[1], canonical_content=duplicate_target[0].canonical_content, memory_content_sha256=duplicate_target[0].memory_content_sha256, target_record_id=duplicate_target[0].target_record_id)
    check("重复 target 阻断", not _plan(duplicate_target).executable and _has_issue(_plan(duplicate_target), "DUPLICATE_TARGET_RECORD_ID"))
    duplicate_legacy = snapshots.copy()
    duplicate_legacy[1] = replace(duplicate_legacy[1], legacy_storage_id=duplicate_legacy[0].legacy_storage_id)
    check("重复 legacy ID 阻断", not _plan(duplicate_legacy).executable and _has_issue(_plan(duplicate_legacy), "DUPLICATE_LEGACY_STORAGE_ID"))
    bad_target = snapshots.copy()
    bad_target[0] = replace(bad_target[0], target_record_id="toolmem-v1-" + "0" * 64)
    check("target ID 重算不一致阻断", _has_issue(_plan(bad_target), "TARGET_RECORD_ID_MISMATCH"))
    bad_digest = snapshots.copy()
    bad_digest[0] = replace(bad_digest[0], memory_content_sha256="0" * 64)
    check("内容摘要重算不一致阻断", _has_issue(_plan(bad_digest), "TARGET_CONTENT_DIGEST_MISMATCH"))
    check("正式源摘要不符阻断", _has_issue(_plan(snapshots, approved_formal_source_sha256="0" * 64), "FORMAL_SOURCE_DIGEST_MISMATCH"))
    check("验证备份摘要不符阻断", _has_issue(_plan(snapshots, approved_verified_backup_sha256="0" * 64), "VERIFIED_BACKUP_DIGEST_MISMATCH"))
    check("legacy 数量不符阻断", _has_issue(_plan(snapshots[:-1]), "LEGACY_RECORD_COUNT_MISMATCH"))
    check("malformed 存在时阻断", _has_issue(_plan(snapshots, observed_malformed_count=1), "MALFORMED_RECORDS_PRESENT"))
    check("unknown 存在时阻断", _has_issue(_plan(snapshots, observed_unknown_count=1), "UNKNOWN_RECORDS_PRESENT"))
    conflict_metadata = snapshots.copy()
    metadata = dict(conflict_metadata[0].compatibility_metadata)
    metadata["migration_batch_id"] = "legacy-value"
    conflict_metadata[0] = replace(conflict_metadata[0], compatibility_metadata=metadata)
    check("迁移治理字段冲突阻断", _has_issue(_plan(conflict_metadata), "MIGRATION_METADATA_FIELD_CONFLICT"))
    missing_required = snapshots.copy()
    metadata = dict(missing_required[0].compatibility_metadata)
    del metadata["expected_tables"]
    missing_required[0] = replace(missing_required[0], compatibility_metadata=metadata)
    missing_required_plan = _plan(missing_required)
    missing_required_item = next(item for item in missing_required_plan.items if item.legacy_storage_id == missing_required[0].legacy_storage_id)
    check("必须兼容字段缺失阻断", _has_issue(missing_required_plan, "REQUIRED_COMPATIBILITY_FIELD_INVALID"))
    check("缺少 expected_tables 归入 missing 而非 invalid", "expected_tables" in missing_required_item.target_compatibility_metadata["legacy_missing_fields"] and "expected_tables" not in missing_required_item.target_compatibility_metadata["legacy_invalid_fields"])
    invalid_expected = snapshots.copy()
    metadata = dict(invalid_expected[0].compatibility_metadata)
    metadata["expected_tables"] = []
    invalid_expected[0] = replace(invalid_expected[0], compatibility_metadata=metadata)
    invalid_expected_plan = _plan(invalid_expected)
    invalid_expected_item = next(item for item in invalid_expected_plan.items if item.legacy_storage_id == invalid_expected[0].legacy_storage_id)
    check("无效 expected_tables 归入 invalid 而非 missing", "expected_tables" in invalid_expected_item.target_compatibility_metadata["legacy_invalid_fields"] and "expected_tables" not in invalid_expected_item.target_compatibility_metadata["legacy_missing_fields"] and not invalid_expected_plan.executable)
    optional_missing = snapshots.copy()
    metadata = dict(optional_missing[0].compatibility_metadata)
    del metadata["review_reason"]
    optional_missing[0] = replace(optional_missing[0], compatibility_metadata=metadata)
    optional_missing_plan = _plan(optional_missing)
    optional_item = next(item for item in optional_missing_plan.items if item.legacy_storage_id == optional_missing[0].legacy_storage_id)
    check("可选字段缺失记录但不虚构且不阻断", optional_missing_plan.executable and "review_reason" in optional_item.target_compatibility_metadata["legacy_missing_fields"] and "review_reason" not in optional_item.target_compatibility_metadata)
    multiple_missing = snapshots.copy()
    metadata = dict(multiple_missing[0].compatibility_metadata)
    for field in ("source", "review_reason", "expected_behavior"):
        del metadata[field]
    multiple_missing[0] = replace(multiple_missing[0], compatibility_metadata=metadata)
    multiple_missing_plan = _plan(multiple_missing)
    multiple_item = next(item for item in multiple_missing_plan.items if item.legacy_storage_id == multiple_missing[0].legacy_storage_id)
    missing_list = multiple_item.target_compatibility_metadata["legacy_missing_fields"]
    check("多个缺失字段稳定排序且无重复", missing_list == sorted(set(missing_list)))
    invalid_field_conflict = snapshots.copy()
    metadata = dict(invalid_field_conflict[0].compatibility_metadata)
    metadata["legacy_invalid_fields"] = ["preexisting"]
    invalid_field_conflict[0] = replace(invalid_field_conflict[0], compatibility_metadata=metadata)
    invalid_field_conflict_plan = _plan(invalid_field_conflict)
    invalid_field_conflict_item = next(item for item in invalid_field_conflict_plan.items if item.legacy_storage_id == invalid_field_conflict[0].legacy_storage_id)
    check("旧 legacy_invalid_fields 占用触发冲突且不覆盖", _has_issue(invalid_field_conflict_plan, "MIGRATION_METADATA_FIELD_CONFLICT") and invalid_field_conflict_item.target_compatibility_metadata["legacy_invalid_fields"] == ["preexisting"])

    check("target 不存在规划创建", all(x.phase_a_action == "create_target" for x in plan.items))
    first_item = plan.items[0]
    first_snapshot = next(x for x in snapshots if x.target_record_id == first_item.target_record_id)
    same_existing = _existing_from_item(first_snapshot, first_item)
    resumed = _plan(snapshots, existing_targets={first_item.target_record_id: same_existing})
    check("同归属已存在 target 可续跑", resumed.items[0].phase_a_action == "resume_target_created")
    other_governance = dict(same_existing.top_level_metadata)
    other_governance["created_by_training_batch_id"] = "OTHER-BATCH"
    other_existing = replace(same_existing, top_level_metadata=other_governance)
    other_plan = _plan(snapshots, existing_targets={first_item.target_record_id: other_existing})
    check("其他批次同内容 target 阻断", _has_issue(other_plan, "TARGET_PREEXISTING_OTHER_OWNER"))
    conflict_existing = replace(same_existing, memory_content_sha256="f" * 64)
    conflict_plan = _plan(snapshots, existing_targets={first_item.target_record_id: conflict_existing})
    check("已存在 target 内容冲突阻断", _has_issue(conflict_plan, "TARGET_PREEXISTING_CONTENT_CONFLICT"))
    check("已存在 target 不生成替代 ID", resumed.items[0].target_record_id == first_item.target_record_id)

    check("阶段 A 不含 legacy 删除", all(x.phase_b_action == "retain_legacy" for x in plan.items))
    check("阶段 A 预期总数 136", plan.expected_phase_a_store_count == 136)
    check("阶段 A 回滚集合仅 target ID", set(plan.phase_a_possible_rollback_ids) == {x.target_record_id for x in plan.items})
    check("阶段 B 默认未批准", plan.phase_b_approved is False)
    check("未批准时无可执行删除", plan.phase_b_executable_delete_ids == ())
    approved_only = _plan(snapshots, phase_b_approved=True)
    check("仅阶段 B 批准仍无删除集合", not approved_only.phase_a_verified and approved_only.phase_b_executable_delete_ids == () and _has_phase_a_issue(approved_only, "PHASE_A_VERIFICATION_NOT_PROVIDED"))
    valid_verification = _phase_a_snapshot(plan)
    verified_only = _plan(snapshots, phase_a_verification=valid_verification)
    check("阶段 A 已验证但未批准仍无删除集合", verified_only.phase_a_verified and verified_only.phase_b_executable_delete_ids == ())
    approved = _plan(snapshots, phase_a_verification=valid_verification, phase_b_approved=True)
    check("阶段 A 已验证且批准后删除 64 个 legacy ID", len(approved.phase_b_executable_delete_ids) == 64 and set(approved.phase_b_executable_delete_ids) == {x.legacy_storage_id for x in snapshots})
    check("阶段 B 删除集合不含 target ID", set(approved.phase_b_executable_delete_ids).isdisjoint({x.target_record_id for x in plan.items}))

    missing_target = replace(valid_verification, verified_target_record_ids=valid_verification.verified_target_record_ids[:-1])
    extra_target = replace(valid_verification, verified_target_record_ids=valid_verification.verified_target_record_ids + ("toolmem-v1-" + "f" * 64,))
    missing_target_plan = _plan(snapshots, phase_a_verification=missing_target, phase_b_approved=True)
    extra_target_plan = _plan(snapshots, phase_a_verification=extra_target, phase_b_approved=True)
    check("阶段 A target 集合缺少或增加均阻断", _has_phase_a_issue(missing_target_plan, "PHASE_A_TARGET_SET_MISMATCH") and _has_phase_a_issue(extra_target_plan, "PHASE_A_TARGET_SET_MISMATCH") and not missing_target_plan.phase_b_executable_delete_ids and not extra_target_plan.phase_b_executable_delete_ids)
    missing_legacy = replace(valid_verification, retained_legacy_storage_ids=valid_verification.retained_legacy_storage_ids[:-1])
    missing_legacy_plan = _plan(snapshots, phase_a_verification=missing_legacy, phase_b_approved=True)
    check("阶段 A legacy 保留集合不完整阻断", _has_phase_a_issue(missing_legacy_plan, "PHASE_A_LEGACY_SET_MISMATCH") and not missing_legacy_plan.phase_b_executable_delete_ids)
    bad_store_count = _plan(snapshots, phase_a_verification=replace(valid_verification, store_count_after_phase_a=135), phase_b_approved=True)
    check("阶段 A 存储总数不符阻断", _has_phase_a_issue(bad_store_count, "PHASE_A_STORE_COUNT_MISMATCH") and not bad_store_count.phase_b_executable_delete_ids)
    bad_classification_plans = [
        _plan(snapshots, phase_a_verification=replace(valid_verification, **{field: value}), phase_b_approved=True)
        for field, value in (("controlled_tool_record_count", 63), ("legacy_tool_record_count", 63), ("text_memory_count", 7))
    ]
    check("阶段 A 任一分类数量不符均阻断", all(_has_phase_a_issue(candidate, "PHASE_A_CLASSIFICATION_COUNT_MISMATCH") and not candidate.phase_b_executable_delete_ids for candidate in bad_classification_plans))
    invalid_record_plans = [
        _plan(snapshots, phase_a_verification=replace(valid_verification, **{field: 1}), phase_b_approved=True)
        for field in ("malformed_record_count", "unknown_record_count", "duplicate_content_group_count", "content_address_conflict_count")
    ]
    check("阶段 A 任一无效记录计数非零均阻断", all(_has_phase_a_issue(candidate, "PHASE_A_INVALID_RECORDS_PRESENT") and not candidate.phase_b_executable_delete_ids for candidate in invalid_record_plans))
    bad_inventory = _plan(snapshots, phase_a_verification=replace(valid_verification, inventory_sha256="invalid"), phase_b_approved=True)
    check("阶段 A inventory SHA 格式无效阻断", _has_phase_a_issue(bad_inventory, "PHASE_A_INVENTORY_SHA256_INVALID") and not bad_inventory.phase_b_executable_delete_ids)
    bad_phase_digest = _plan(snapshots, phase_a_verification=replace(valid_verification, migration_plan_content_sha256="0" * 64), phase_b_approved=True)
    check("阶段 A 计划摘要不匹配阻断", _has_phase_a_issue(bad_phase_digest, "PHASE_A_PLAN_DIGEST_MISMATCH") and not bad_phase_digest.phase_b_executable_delete_ids)
    reversed_verification = replace(valid_verification, verified_target_record_ids=tuple(reversed(valid_verification.verified_target_record_ids)), retained_legacy_storage_ids=tuple(reversed(valid_verification.retained_legacy_storage_ids)))
    reversed_verified_plan = _plan(snapshots, phase_a_verification=reversed_verification, phase_b_approved=True)
    check("阶段 A 集合输入顺序不影响最终计划", approved.phase_a_verified == reversed_verified_plan.phase_a_verified and approved.migration_plan_sha256 == reversed_verified_plan.migration_plan_sha256 and approved.phase_b_executable_delete_ids == reversed_verified_plan.phase_b_executable_delete_ids)
    check("最终预期总数 72", plan.expected_final_store_count == 72)
    check("8 条 Text Memory 不进入迁移或删除集合", plan.text_memory_excluded_count == 8 and len(plan.items) == 64 and len(plan.proposed_legacy_delete_ids) == 64)

    public = build_public_migration_evidence(plan)
    public_text = json.dumps(public, ensure_ascii=False, sort_keys=True)
    check("公开证据无完整 question", snapshots[0].canonical_content["question"] not in public_text and "question" not in public["items"][0])
    check("公开证据无完整 SQL", snapshots[0].canonical_content["args"]["sql"] not in public_text)
    check("公开证据无 args_json 正文", "args_json" not in public["items"][0])
    check("公开证据无 metadata_json 正文", "metadata_json" not in public["items"][0])
    allowed = {"migration_sample_id", "legacy_storage_id", "target_record_id", "memory_content_sha256", "legacy_metadata_sha256", "migration_item_sha256", "metadata_field_names", "missing_fields", "invalid_fields", "issue_codes", "phase_a_action", "phase_b_action", "executable"}
    check("公开迁移项仅含允许字段", all(set(item) == allowed for item in public["items"]))
    missing_public = build_public_migration_evidence(missing_required_plan)
    missing_public_item = next(item for item in missing_public["items"] if item["legacy_storage_id"] == missing_required[0].legacy_storage_id)
    check("公开证据可逐项定位 expected_tables 缺失", "expected_tables" in missing_public_item["missing_fields"] and "expected_tables" not in missing_public_item["invalid_fields"] and "REQUIRED_COMPATIBILITY_FIELD_INVALID" in missing_public_item["issue_codes"])
    invalid_public = build_public_migration_evidence(invalid_expected_plan)
    invalid_public_item = next(item for item in invalid_public["items"] if item["legacy_storage_id"] == invalid_expected[0].legacy_storage_id)
    check("公开证据区分 expected_tables 无效", "expected_tables" in invalid_public_item["invalid_fields"] and "expected_tables" not in invalid_public_item["missing_fields"])
    missing_public_text = json.dumps(missing_public, ensure_ascii=False, sort_keys=True)
    check("逐项缺失证据仍不泄露问题或 SQL", missing_required[0].canonical_content["question"] not in missing_public_text and missing_required[0].canonical_content["args"]["sql"] not in missing_public_text)

    coverage = analyze_legacy_metadata_coverage(snapshots)
    check("字段覆盖统计包含存在/类型/非空", all(set(value) == {"present_count", "type_valid_count", "nonempty_valid_count", "total"} for value in coverage["fields"].values()))
    check("未知业务字段被保留", all(x.target_compatibility_metadata["preserved_unknown_field"] for x in plan.items))
    check("顶层治理 metadata 全为标量", all(all(isinstance(v, (str, int, float, bool)) for v in x.target_governance_metadata.values()) for x in plan.items))
    check("expected_tables 仅位于 metadata_json", all("expected_tables" not in x.target_governance_metadata and "expected_tables" in json.loads(x.target_governance_metadata["metadata_json"]) for x in plan.items))
    blocked_verification = _phase_a_snapshot(missing_required_plan)
    blocked_approved = _plan(missing_required, phase_a_verification=blocked_verification, phase_b_approved=True)
    check("不可执行计划即使阶段 A 验证且批准也无删除集合", blocked_approved.phase_a_verified and not blocked_approved.executable and blocked_approved.phase_b_executable_delete_ids == ())

    module_source = (ROOT / "training/sop/legacy_tool_memory_migration_plan.py").read_text(encoding="utf-8")
    forbidden_imports = ("import chromadb", "import sqlite3", "import sqlalchemy", "import psycopg", "AgentMemory", "ChromaAgentMemory", "import agent_config")
    check("迁移模块不导入 chromadb", "import chromadb" not in module_source)
    check("迁移模块不导入 AgentMemory", "AgentMemory" not in module_source)
    check("迁移模块不导入 agent_config", "import agent_config" not in module_source)
    check("测试未连接数据库或执行 SQL", not any(token in module_source for token in ("psycopg2", "sqlalchemy", "sqlite3", ".connect(")))
    check("测试未访问网络", not any(token in module_source for token in ("requests", "urllib", "httpx", "socket")))
    check("禁止导入集合为空", not any(token in module_source for token in forbidden_imports))
    check("测试前后 Git 工作区状态一致", initial_status == _git_status())

    print(f"\n迁移计划测试汇总: {PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

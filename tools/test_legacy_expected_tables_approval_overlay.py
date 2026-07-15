"""M2B 人工批准与 expected_tables overlay 契约测试。"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.legacy_expected_tables_approval_overlay import *  # noqa: E402,F403
from training.sop.legacy_tool_memory_migration_plan import (  # noqa: E402
    LegacyToolRecordSnapshot, TextMemoryBaselineRecord,
    build_legacy_tool_memory_migration_contract,
)
from training.sop.memory_write_plan import build_memory_identity_from_canonical_content  # noqa: E402

PASSED = FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1; print(f"[PASS] {name}")
    else:
        FAILED += 1; print(f"[FAIL] {name}")


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha_json(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fixtures():
    snapshots = []
    recovery = []
    for index in range(1, 65):
        sql = f"SELECT id FROM table_{index} LIMIT 10"
        canonical = {"record_schema_version": "1.0", "question": f"PRIVATE QUESTION {index}", "tool_name": "run_sql", "args": {"sql": sql}, "success": True}
        identity = build_memory_identity_from_canonical_content(canonical)
        compatibility = {"sample_id": f"LEGACY_{index:03d}", "training_level": "level2_sql_examples", "train_decision": "approved", "review_reason": "synthetic", "source": "synthetic", "expected_behavior": "synthetic"}
        if index <= 48:
            compatibility["expected_tables"] = [f"table_{index}"]
        raw = {"question": canonical["question"], "tool_name": "run_sql", "args_json": json.dumps(canonical["args"]), "success": True, "metadata_json": json.dumps(compatibility)}
        snapshot = LegacyToolRecordSnapshot(f"00000000-0000-4000-8000-{index:012d}", canonical["question"], raw, canonical, identity.memory_content_sha256, identity.record_id, compatibility)
        snapshots.append(snapshot)
        if index > 48:
            base = {"legacy_storage_id": snapshot.legacy_storage_id, "target_record_id": snapshot.target_record_id, "memory_content_sha256": snapshot.memory_content_sha256, "sql_sha256": sha_text(sql), "normalized_sql_sha256": sha_text(sql), "analysis_item_sha256": f"{index:064x}", "proposed_expected_tables": [f"table_{index}"]}
            base["recovery_item_sha256"] = sha_json({**base, "issue_codes": []})
            recovery.append(base)
    texts = [TextMemoryBaselineRecord(f"text-{i}", sha_text(f"doc-{i}"), sha_json({"i": i})) for i in range(8)]
    return snapshots, texts, recovery


def environment(approval):
    return build_m2b_environment(formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256, source_inventory_sha256="1" * 64, recovery_environment_sha256=APPROVED_RECOVERY_ENVIRONMENT_SHA256, recovery_proposal_sha256=APPROVED_RECOVERY_PROPOSAL_SHA256, approval_evidence_sha256=approval.approval_evidence_sha256, recovery_module_source_sha256="2" * 64, recovery_audit_source_sha256="3" * 64, migration_contract_module_source_sha256="4" * 64, overlay_module_source_sha256="5" * 64, m2b_audit_source_sha256="6" * 64)


def main() -> int:
    initial = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout
    snapshots, texts, items = fixtures()
    approval = build_human_recovery_approval(items)
    env = environment(approval)
    overlay = build_expected_tables_overlay(items, approval, env)
    application = apply_expected_tables_overlay(snapshots, overlay)
    archive = {"environment_sha256": APPROVED_RECOVERY_ENVIRONMENT_SHA256, "proposal_sha256": APPROVED_RECOVERY_PROPOSAL_SHA256, "items": items}
    bundle = build_migration_bundle_with_approved_overlay(snapshots, texts, recovery_archive=archive, fresh_revalidation=archive, approval=approval, environment=env, overlay=overlay, source_inventory_sha256="1" * 64, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256)
    direct = build_legacy_tool_memory_migration_contract(snapshots, texts, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256, expected_legacy_count=64, text_memory_count=8, existing_targets={})

    check("正确批准证据通过", not validate_approval(approval, items))
    check("批准摘要可重算", recompute_approval_evidence_sha256(approval) == approval.approval_evidence_sha256)
    approval_cases = (
        (replace(approval, recovery_environment_sha256="f" * 64), "错误environment阻断"),
        (replace(approval, recovery_proposal_sha256="f" * 64), "错误proposal阻断"),
        (replace(approval, recovery_proposal_sha256=RETIRED_RECOVERY_PROPOSAL_SHA256), "废止proposal阻断"),
        (replace(approval, approved_recovery_state="bad"), "错误state阻断"),
        (replace(approval, approved_item_count=15), "错误item count阻断"),
        (replace(approval, authorized_scope=approval.authorized_scope[:-1]), "授权范围变化阻断"),
        (replace(approval, forbidden_scope=approval.forbidden_scope[:-1]), "禁止范围变化阻断"),
        (replace(approval, approval_evidence_sha256="f" * 64), "批准摘要篡改阻断"),
    )
    for value, name in approval_cases: check(name, bool(validate_approval(value, items)))
    check("缺少批准项阻断", bool(validate_approval(approval, items[:-1])))
    check("增加批准项阻断", bool(validate_approval(approval, items + [items[0]])))
    changed_items = [dict(item) for item in items]; changed_items[0]["recovery_item_sha256"] = "f" * 64
    check("恢复项摘要变化阻断", bool(validate_approval(approval, changed_items)))
    check("批准输入顺序无关", build_human_recovery_approval(list(reversed(items))).approval_evidence_sha256 == approval.approval_evidence_sha256)

    check("M2B环境通过", not validate_m2b_environment(env, approval))
    check("M2B环境摘要可重算", recompute_m2b_environment_sha256(env) == env.m2b_environment_sha256)
    check("M2B环境篡改阻断", bool(validate_m2b_environment(replace(env, source_inventory_sha256="f" * 64), approval)))
    check("16条overlay通过", not validate_overlay(overlay, items, approval, env) and overlay.item_count == 16)
    check("overlay item摘要可重算", all(recompute_overlay_item_sha256(item) == item.overlay_item_sha256 for item in overlay.items))
    check("overlay总摘要可重算", recompute_overlay_sha256(overlay) == overlay.overlay_sha256)
    check("overlay顺序无关", recompute_overlay_sha256(replace(overlay, items=tuple(reversed(overlay.items)))) == overlay.overlay_sha256)
    check("overlay缺项阻断", bool(validate_overlay(replace(overlay, items=overlay.items[:-1], item_count=15), items, approval, env)))
    check("overlay重复项阻断", bool(validate_overlay(replace(overlay, items=overlay.items[:-1] + (overlay.items[0],)), items, approval, env)))
    bad_item = replace(overlay.items[0], expected_tables=())
    check("overlay空表阻断", bool(validate_overlay(replace(overlay, items=(bad_item,) + overlay.items[1:]), items, approval, env)))
    bad_item = replace(overlay.items[0], expected_tables=("public.table_49",))
    check("overlay未规范化表阻断", bool(validate_overlay(replace(overlay, items=(bad_item,) + overlay.items[1:]), items, approval, env)))
    bad_item = replace(overlay.items[0], overlay_item_sha256="f" * 64)
    check("overlay item摘要篡改阻断", bool(validate_overlay(replace(overlay, items=(bad_item,) + overlay.items[1:]), items, approval, env)))
    check("overlay总摘要篡改阻断", bool(validate_overlay(replace(overlay, overlay_sha256="f" * 64), items, approval, env)))

    check("overlay只修改16条副本", application.valid and application.changed_snapshot_count == 16 and application.unchanged_snapshot_count == 48)
    check("原始snapshot不变", all("expected_tables" not in snapshots[i].compatibility_metadata for i in range(48, 64)))
    check("48条已有记录完全不变", all(application.snapshots[i].compatibility_metadata == snapshots[i].compatibility_metadata for i in range(48)))
    check("raw metadata不变", all(a.raw_metadata == b.raw_metadata for a, b in zip(snapshots, application.snapshots)))
    check("canonical content不变", all(a.canonical_content == b.canonical_content for a, b in zip(snapshots, application.snapshots)))
    check("document不变", all(a.document == b.document for a, b in zip(snapshots, application.snapshots)))
    check("内容身份与target不变", all((a.memory_content_sha256, a.target_record_id) == (b.memory_content_sha256, b.target_record_id) for a, b in zip(snapshots, application.snapshots)))
    check("新增字段集合精确", all(set(b.compatibility_metadata) - set(a.compatibility_metadata) == {"expected_tables", *RESERVED_FIELDS} for a, b in zip(snapshots[48:], application.snapshots[48:])))
    existing = list(snapshots); existing[48] = replace(existing[48], compatibility_metadata={**existing[48].compatibility_metadata, "expected_tables": ["table_49"]})
    check("已有expected_tables拒绝覆盖", "OVERLAY_TARGET_FIELD_ALREADY_PRESENT" in apply_expected_tables_overlay(existing, overlay).issue_codes)
    reserved = list(snapshots); reserved[48] = replace(reserved[48], compatibility_metadata={**reserved[48].compatibility_metadata, RESERVED_FIELDS[0]: []})
    check("保留字段冲突阻断", "OVERLAY_RESERVED_FIELD_CONFLICT" in apply_expected_tables_overlay(reserved, overlay).issue_codes)
    check("找不到snapshot阻断", "OVERLAY_TARGET_NOT_FOUND" in apply_expected_tables_overlay(snapshots[:-1], overlay).issue_codes)
    wrong_sql = list(snapshots); wrong_sql[48] = replace(wrong_sql[48], canonical_content={**wrong_sql[48].canonical_content, "args": {"sql": "SELECT 1"}})
    check("SQL摘要不匹配阻断", "OVERLAY_SQL_SHA256_MISMATCH" in apply_expected_tables_overlay(wrong_sql, overlay).issue_codes)
    check("application摘要确定", apply_expected_tables_overlay(list(reversed(snapshots)), overlay).application_sha256 == application.application_sha256)

    check("原始2.1契约被16条缺失阻断", not direct.executable and len(direct.issues) >= 16)
    check("批准overlay契约可执行", bundle.bundle_ready and bundle.contract_executable and bundle.contract_issue_count == 0)
    contract = bundle.migration_contract
    check("迁移数量不变量", len(contract.items) == 64 and len(contract.phase_a_create_target_ids) == 64 and not contract.phase_a_resume_target_ids and len(contract.phase_a_rollback_candidate_ids) == 64 and len(contract.proposed_legacy_delete_ids) == 64)
    check("存储数量不变量", contract.expected_phase_a_store_count == 136 and contract.expected_final_store_count == 72)
    check("16条target含恢复来源", sum("expected_tables_recovery_overlay_sha256" in item.target_compatibility_metadata for item in contract.items) == 16)
    check("48条target无恢复来源", sum("expected_tables_recovery_overlay_sha256" not in item.target_compatibility_metadata for item in contract.items) == 48)
    check("bundle摘要确定", bundle.bundle_sha256 == build_migration_bundle_with_approved_overlay(snapshots, texts, recovery_archive=archive, fresh_revalidation=archive, approval=approval, environment=env, overlay=overlay, source_inventory_sha256="1" * 64, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256).bundle_sha256)
    check("归档错误进入SOURCE_BLOCKED", build_migration_bundle_with_approved_overlay(snapshots, texts, recovery_archive={**archive, "proposal_sha256": "f" * 64}, fresh_revalidation=archive, approval=approval, environment=env, overlay=overlay, source_inventory_sha256="1" * 64, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256).state == "SOURCE_BLOCKED")
    check("fresh变化进入SOURCE_BLOCKED", build_migration_bundle_with_approved_overlay(snapshots, texts, recovery_archive=archive, fresh_revalidation={**archive, "items": items[:-1]}, approval=approval, environment=env, overlay=overlay, source_inventory_sha256="1" * 64, migration_batch_id=MIGRATION_BATCH_ID, approved_formal_source_sha256=APPROVED_FORMAL_SOURCE_SHA256, approved_verified_backup_sha256=APPROVED_FORMAL_SOURCE_SHA256).state == "SOURCE_BLOCKED")

    source = (ROOT / "tools/audit_legacy_expected_tables_approval_overlay.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"add", "upsert", "update", "delete", "query", "peek", "modify", "reset"}
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "collection"}
    check("audit静态只读边界", not calls & forbidden and "save_tool_usage" not in source and "save_text_memory" not in source)
    pure = (ROOT / "training/sop/legacy_expected_tables_approval_overlay.py").read_text(encoding="utf-8")
    check("纯逻辑模块边界", not any(name in pure for name in ("chromadb", "sqlite3", "AgentMemory", "pathlib", "subprocess", "import os")))
    check("完整runner依赖注入与finally清理", "dependencies" in source and "finally:" in source and "migration_copy.exists()" in source)
    public = json.dumps({"approval": approval.__dict__, "overlay": overlay.__dict__, "bundle": {k: v for k, v in bundle.__dict__.items() if k != "migration_contract"}}, default=lambda value: value.__dict__, ensure_ascii=False)
    check("公开证据隐私", "PRIVATE QUESTION" not in public and "SELECT id" not in public and "args_json" not in public and "metadata_json" not in public)
    check("Git状态不变", initial == subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout)
    print(f"M2B approval overlay测试汇总: {PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

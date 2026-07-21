"""legacy expected_tables 只读恢复契约测试。"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.sop.legacy_expected_tables_recovery import (  # noqa: E402
    APPROVED_FORMAL_SOURCE_SHA256,
    APPROVED_RECOVERY_BASE_COMMIT,
    LegacyExpectedTablesRecord,
    RecoverySourceFacts,
    analyze_record_sql,
    build_expected_tables_recovery_proposal,
    build_recovery_environment,
    classify_expected_tables,
    recompute_recovery_environment_sha256,
    recompute_sql_analysis_item_sha256,
)


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


def _records() -> list[LegacyExpectedTablesRecord]:
    records = []
    for index in range(1, 65):
        table = f"table_{index}"
        records.append(
            LegacyExpectedTablesRecord(
                legacy_storage_id=f"legacy-{index:03d}",
                target_record_id=f"toolmem-v1-{index:064x}"[-75:],
                memory_content_sha256=f"{index:064x}"[-64:],
                question=f"PRIVATE QUESTION {index}",
                sql=f"SELECT id FROM {table}",
                stored_expected_tables=(table,) if index <= 48 else None,
                expected_tables_state="valid" if index <= 48 else "missing",
            )
        )
    return records


def _guard_result(
    used_tables: list[str],
    *,
    passed: bool = True,
    severity: str = "ok",
    unknown_tables: list[str] | None = None,
    unknown_columns: list[str] | None = None,
    forbidden_operations: list[str] | None = None,
    candidate_mismatch: list[str] | None = None,
):
    return SimpleNamespace(
        passed=passed,
        severity=severity,
        used_tables=used_tables,
        unknown_tables=unknown_tables or [],
        unknown_columns=unknown_columns or [],
        forbidden_operations=forbidden_operations or [],
        candidate_mismatch=candidate_mismatch or [],
    )


class FakeGuard:
    def __init__(self, mapping, *, raises: set[str] | None = None):
        self.mapping = mapping
        self.raises = raises or set()

    def validate(self, *, sql, query, deterministic_candidate_tables):
        assert query == ""
        assert deterministic_candidate_tables == []
        if sql in self.raises:
            raise RuntimeError("synthetic guard failure")
        return self.mapping[sql]


def _analyses(records, overrides=None, raises=None):
    overrides = overrides or {}
    mapping = {
        record.sql: overrides.get(
            record.legacy_storage_id,
            _guard_result([f"table_{int(record.legacy_storage_id[-3:])}"]),
        )
        for record in records
    }
    guard = FakeGuard(mapping, raises=raises)
    return [analyze_record_sql(record, guard) for record in records]


def _facts(**changes):
    values = {
        "formal_source_sha256_before": APPROVED_FORMAL_SOURCE_SHA256,
        "formal_source_sha256_after": APPROVED_FORMAL_SOURCE_SHA256,
        "verified_backup_sha256_before": APPROVED_FORMAL_SOURCE_SHA256,
        "verified_backup_sha256_after": APPROVED_FORMAL_SOURCE_SHA256,
        "audit_copy_sha256_before_open": APPROVED_FORMAL_SOURCE_SHA256,
        "audit_copy_sha256_after_open": APPROVED_FORMAL_SOURCE_SHA256,
        "audit_inventory_sha256": "1" * 64,
        "metadata_index_sha256_before": "2" * 64,
        "metadata_index_sha256_after": "2" * 64,
        "sql_guard_source_sha256": "3" * 64,
        "batch_validator_source_sha256": "4" * 64,
        "recovery_module_source_sha256": "5" * 64,
        "audit_entry_source_sha256": "6" * 64,
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
    values.update(changes)
    return RecoverySourceFacts(**values)


def _environment(**changes):
    values = {
        "base_commit": APPROVED_RECOVERY_BASE_COMMIT,
        "audit_inventory_sha256": "1" * 64,
        "metadata_index_sha256": "2" * 64,
        "sql_guard_source_sha256": "3" * 64,
        "batch_validator_source_sha256": "4" * 64,
        "recovery_module_source_sha256": "5" * 64,
        "audit_entry_source_sha256": "6" * 64,
    }
    values.update(changes)
    return build_recovery_environment(**values)


def _proposal(records=None, analyses=None, facts=None, environment=None):
    records = records or _records()
    return build_expected_tables_recovery_proposal(
        records,
        analyses or _analyses(records),
        facts or _facts(),
        environment or _environment(),
    )


def main() -> int:
    initial_status = _git_status()
    records = _records()
    analyses = _analyses(records)
    ready = _proposal(records, analyses)
    check(
        "48有效+16缺失进入人工审批等待状态",
        ready.state == "PROPOSAL_READY_AWAITING_HUMAN_APPROVAL"
        and ready.calibration_match_count == 48
        and ready.recovery_proposed_count == 16
        and ready.recovery_blocked_count == 0,
    )
    check("提案状态不包含自动批准", "approved" not in json.dumps(ready.to_public_dict()))

    source_cases = [
        ("总记录不是64", records[:-1], _facts(), "SOURCE_BLOCKED"),
        (
            "有效字段不是48",
            [replace(records[0], expected_tables_state="missing", stored_expected_tables=None)] + records[1:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "缺失字段不是16",
            records[:48] + [replace(records[48], expected_tables_state="valid", stored_expected_tables=("table_49",))] + records[49:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "存在invalid expected_tables",
            [replace(records[0], expected_tables_state="invalid", stored_expected_tables=None)] + records[1:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "legacy ID重复",
            [records[0], replace(records[1], legacy_storage_id=records[0].legacy_storage_id)] + records[2:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "target ID重复",
            [records[0], replace(records[1], target_record_id=records[0].target_record_id)] + records[2:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "非run_sql记录",
            [replace(records[0], tool_name="other")] + records[1:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
        (
            "success不是true",
            [replace(records[0], success=False)] + records[1:],
            _facts(),
            "SOURCE_BLOCKED",
        ),
    ]
    for name, changed, facts, expected_state in source_cases:
        result = _proposal(changed, _analyses(changed), facts)
        check(name, result.state == expected_state and not result.recovery_items)

    mismatch_records = records.copy()
    mismatch_records[0] = replace(
        mismatch_records[0], stored_expected_tables=("different_table",)
    )
    mismatch = _proposal(mismatch_records, _analyses(mismatch_records))
    check(
        "任一校准表集合不同则整体校准失败",
        mismatch.state == "CALIBRATION_FAILED"
        and mismatch.calibration_match_count == 47
        and not mismatch.recovery_items,
    )
    ordered_records = records.copy()
    ordered_records[0] = replace(
        ordered_records[0], stored_expected_tables=("table_1",)
    )
    check("校准表顺序规范化", _proposal(ordered_records).state == ready.state)
    schema_records = records.copy()
    schema_records[0] = replace(
        schema_records[0], stored_expected_tables=('"public"."table_1"',)
    )
    check("schema前缀和引号按批次规则规范化", _proposal(schema_records).state == ready.state)
    duplicate_state, duplicate_value = classify_expected_tables(
        ["table_1", "public.table_1"], field_present=True
    )
    check("stored expected_tables重复被源阻断", duplicate_state == "invalid" and duplicate_value is None)

    calibration_block_cases = {
        "校准SQLGuard拒绝": _guard_result(["table_1"], passed=False),
        "校准used_tables为空": _guard_result([]),
        "校准存在未知表": _guard_result(["table_1"], unknown_tables=["unknown"]),
        "校准存在未知字段": _guard_result(["table_1"], unknown_columns=["bad"]),
    }
    for name, guard_result in calibration_block_cases.items():
        result = _proposal(
            records,
            _analyses(records, {"legacy-001": guard_result}),
        )
        check(name, result.state == "CALIBRATION_FAILED" and not result.proposal_ready)

    candidate = records[48]
    recovery_cases = {
        "候选SQL为空": (replace(candidate, sql=""), None, None),
        "候选多语句": (replace(candidate, sql=candidate.sql + "; SELECT 1"), None, None),
        "候选非SELECT": (replace(candidate, sql="DELETE FROM table_49"), _guard_result(["table_49"], passed=False, forbidden_operations=["delete"]), None),
        "候选SQLGuard异常": (candidate, None, {candidate.sql}),
        "候选severity warning": (candidate, _guard_result(["table_49"], severity="warning"), None),
        "候选unknown_tables": (candidate, _guard_result(["table_49"], unknown_tables=["unknown"]), None),
        "候选unknown_columns": (candidate, _guard_result(["table_49"], unknown_columns=["bad"]), None),
        "候选forbidden_operations": (candidate, _guard_result(["table_49"], forbidden_operations=["update"]), None),
        "候选candidate_mismatch": (candidate, _guard_result(["table_49"], candidate_mismatch=["other"]), None),
        "候选used_tables为空": (candidate, _guard_result([]), None),
    }
    for name, (changed_candidate, override, raises) in recovery_cases.items():
        changed_records = records.copy()
        changed_records[48] = changed_candidate
        overrides = {"legacy-049": override} if override is not None else {}
        changed_analyses = _analyses(changed_records, overrides, raises)
        result = _proposal(changed_records, changed_analyses)
        check(
            name,
            result.state == "RECOVERY_BLOCKED"
            and not result.proposal_ready
            and result.recovery_proposed_count < 16,
        )
    one_failed = _proposal(
        records,
        _analyses(records, {"legacy-049": _guard_result([])}),
    )
    check("单条候选失败不产生15条可批准状态", one_failed.state == "RECOVERY_BLOCKED" and not one_failed.proposal_ready)

    repeated = _proposal(records, analyses)
    reversed_result = _proposal(list(reversed(records)), list(reversed(analyses)))
    check("相同输入摘要一致", ready.recovery_proposal_sha256 == repeated.recovery_proposal_sha256)
    check("输入顺序不影响摘要", ready.recovery_proposal_sha256 == reversed_result.recovery_proposal_sha256)
    changed_sql_records = records.copy()
    changed_sql_records[48] = replace(records[48], sql=records[48].sql + " ")
    changed_sql = _proposal(changed_sql_records, _analyses(changed_sql_records))
    check("SQL变化改变项和提案摘要", changed_sql.recovery_items[0].recovery_item_sha256 != ready.recovery_items[0].recovery_item_sha256 and changed_sql.recovery_proposal_sha256 != ready.recovery_proposal_sha256)
    proposed_change = _proposal(records, _analyses(records, {"legacy-049": _guard_result(["alternate_table"])}))
    check("proposed table变化改变摘要", proposed_change.recovery_items[0].recovery_item_sha256 != ready.recovery_items[0].recovery_item_sha256)
    metadata_environment = _environment(metadata_index_sha256="5" * 64)
    metadata_result = _proposal(records, analyses, environment=metadata_environment)
    check("metadata index摘要改变环境和提案摘要", metadata_environment.recovery_environment_sha256 != _environment().recovery_environment_sha256 and metadata_result.recovery_proposal_sha256 != ready.recovery_proposal_sha256)
    check("SQLGuard源码摘要改变环境", _environment(sql_guard_source_sha256="6" * 64).recovery_environment_sha256 != _environment().recovery_environment_sha256)
    check("batch validator源码摘要改变环境", _environment(batch_validator_source_sha256="7" * 64).recovery_environment_sha256 != _environment().recovery_environment_sha256)
    check("路径和时间不进入摘要", "path" not in json.dumps(_environment().to_public_dict()).lower() and "time" not in json.dumps(_environment().to_public_dict()).lower())
    check("校准失败与成功状态摘要不同", mismatch.recovery_proposal_sha256 != ready.recovery_proposal_sha256)

    def rehash_analysis(value):
        return replace(value, analysis_item_sha256=recompute_sql_analysis_item_sha256(value))

    def rehash_environment(value):
        return replace(value, recovery_environment_sha256=recompute_recovery_environment_sha256(value))

    original_analysis = analyses[0]
    analysis_cases = [
        ("analysis memory identity binding", rehash_analysis(replace(original_analysis, memory_content_sha256="f" * 64)), "ANALYSIS_MEMORY_IDENTITY_MISMATCH"),
        ("analysis raw SQL digest binding", rehash_analysis(replace(original_analysis, sql_sha256="f" * 64)), "ANALYSIS_SQL_SHA256_MISMATCH"),
        ("analysis normalized SQL digest binding", rehash_analysis(replace(original_analysis, normalized_sql_sha256="f" * 64)), "ANALYSIS_NORMALIZED_SQL_SHA256_MISMATCH"),
        ("analysis statement count binding", rehash_analysis(replace(original_analysis, statement_count=2)), "ANALYSIS_STATEMENT_COUNT_MISMATCH"),
        ("analysis item digest recompute", replace(original_analysis, analysis_item_sha256="f" * 64), "ANALYSIS_ITEM_SHA256_MISMATCH"),
        ("ready status with issues blocked", rehash_analysis(replace(original_analysis, issue_codes=("SYNTHETIC",))), "ANALYSIS_STATUS_INCONSISTENT"),
        ("blocked status without issues blocked", rehash_analysis(replace(original_analysis, analysis_status="blocked")), "ANALYSIS_STATUS_INCONSISTENT"),
    ]
    for name, changed_analysis, code in analysis_cases:
        changed = list(analyses)
        changed[0] = changed_analysis
        outcome = _proposal(records, changed)
        check(name, outcome.state == "CALIBRATION_FAILED" and code in outcome.issue_codes)
    changed_candidate_records = list(records)
    changed_candidate_records[48] = replace(records[48], sql="SELECT id FROM changed_table")
    stale_candidate_analysis = _proposal(changed_candidate_records, analyses)
    check(
        "stale candidate SQL analysis enters RECOVERY_BLOCKED",
        stale_candidate_analysis.state == "RECOVERY_BLOCKED"
        and stale_candidate_analysis.calibration_ready
        and "ANALYSIS_SQL_SHA256_MISMATCH" in stale_candidate_analysis.issue_codes,
    )
    changed_candidate_analysis = list(analyses)
    changed_candidate_analysis[48] = replace(analyses[48], analysis_item_sha256="f" * 64)
    changed_candidate_result = _proposal(records, changed_candidate_analysis)
    check(
        "analysis digest covered by downstream item and proposal",
        ready.calibration_items[0].analysis_item_sha256 == analyses[0].analysis_item_sha256
        and ready.recovery_items[0].analysis_item_sha256 == analyses[48].analysis_item_sha256
        and ready.recovery_proposal_sha256 != changed_candidate_result.recovery_proposal_sha256,
    )

    environment_cases = [
        ("recovery base commit binding", rehash_environment(replace(_environment(), base_commit="f" * 40)), "RECOVERY_BASE_COMMIT_MISMATCH"),
        ("inventory environment binding", rehash_environment(replace(_environment(), audit_inventory_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_INVENTORY_MISMATCH"),
        ("metadata environment binding", rehash_environment(replace(_environment(), metadata_index_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_METADATA_INDEX_MISMATCH"),
        ("SQLGuard source binding", rehash_environment(replace(_environment(), sql_guard_source_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_SQL_GUARD_SOURCE_MISMATCH"),
        ("batch validator source binding", rehash_environment(replace(_environment(), batch_validator_source_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_BATCH_VALIDATOR_SOURCE_MISMATCH"),
        ("recovery module source binding", rehash_environment(replace(_environment(), recovery_module_source_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_MODULE_SOURCE_MISMATCH"),
        ("audit entry source binding", rehash_environment(replace(_environment(), audit_entry_source_sha256="a" * 64)), "RECOVERY_ENVIRONMENT_AUDIT_SOURCE_MISMATCH"),
        ("environment digest recompute", replace(_environment(), recovery_environment_sha256="a" * 64), "RECOVERY_ENVIRONMENT_SHA256_MISMATCH"),
        ("environment schema binding", rehash_environment(replace(_environment(), recovery_schema_version="9")), "RECOVERY_ENVIRONMENT_SCHEMA_MISMATCH"),
        ("environment strategy binding", rehash_environment(replace(_environment(), recovery_strategy_version="other")), "RECOVERY_ENVIRONMENT_STRATEGY_MISMATCH"),
        ("environment count binding", rehash_environment(replace(_environment(), expected_store_count=71)), "RECOVERY_ENVIRONMENT_COUNT_MISMATCH"),
    ]
    for name, changed_environment, code in environment_cases:
        outcome = _proposal(records, analyses, _facts(), changed_environment)
        check(name, outcome.state == "SOURCE_BLOCKED" and code in outcome.issue_codes)

    source_cases_r1 = [
        (0, "empty legacy ID blocked", replace(records[0], legacy_storage_id=""), "EMPTY_LEGACY_STORAGE_ID"),
        (0, "invalid target ID blocked", replace(records[0], target_record_id="bad"), "INVALID_TARGET_RECORD_ID"),
        (0, "invalid memory digest blocked", replace(records[0], memory_content_sha256="ABC"), "INVALID_MEMORY_CONTENT_SHA256"),
        (48, "missing state value mismatch", replace(records[48], stored_expected_tables=("table_49",)), "EXPECTED_TABLES_STATE_VALUE_MISMATCH"),
        (0, "valid state value mismatch", replace(records[0], stored_expected_tables=None), "EXPECTED_TABLES_STATE_VALUE_MISMATCH"),
    ]
    for index, name, changed_record, code in source_cases_r1:
        changed_records = list(records)
        changed_records[index] = changed_record
        outcome = _proposal(changed_records, analyses)
        check(name, outcome.state == "SOURCE_BLOCKED" and code in outcome.issue_codes)

    source_blocked = _proposal(records, analyses, _facts(store_count=71))
    check("SOURCE_BLOCKED flags", not source_blocked.calibration_ready and not source_blocked.proposal_ready)
    check("CALIBRATION_FAILED flags", not mismatch.calibration_ready and not mismatch.proposal_ready)
    check("RECOVERY_BLOCKED flags", one_failed.calibration_ready and not one_failed.proposal_ready)
    check("PROPOSAL_READY flags", ready.calibration_ready and ready.proposal_ready)

    from tools.audit_legacy_expected_tables_recovery import _remove_audit_copy
    with tempfile.TemporaryDirectory() as temp_root:
        root = Path(temp_root)
        for name, failure_point in (("success cleanup", None), ("inventory failure cleanup", "inventory"), ("guard failure cleanup", "guard"), ("proposal failure cleanup", "proposal")):
            copy_path = root / name
            copy_path.mkdir()
            try:
                if failure_point:
                    raise RuntimeError(failure_point)
            except RuntimeError:
                pass
            finally:
                _remove_audit_copy(copy_path)
            check(name, not copy_path.exists())
        failed_copy = root / "cleanup-failure"
        failed_copy.mkdir()
        cleanup_code = ""
        try:
            _remove_audit_copy(failed_copy, remover=lambda path: (_ for _ in ()).throw(OSError("synthetic")))
        except RuntimeError as exc:
            cleanup_code = str(exc)
        check("cleanup failure stable code", failed_copy.exists() and cleanup_code == "AUDIT_COPY_CLEANUP_FAILED")
        shutil.rmtree(failed_copy)

    public_text = json.dumps(ready.to_public_dict(), ensure_ascii=False)
    check("公开证据不含question和SQL正文", records[0].question not in public_text and records[0].sql not in public_text)
    check("公开证据不含args_json和metadata_json", "args_json" not in public_text and "metadata_json" not in public_text)
    check("公开证据包含匿名ID、SQL摘要和提案表", all(value in public_text for value in (records[48].legacy_storage_id, records[48].target_record_id, ready.recovery_items[0].sql_sha256, "table_49")))

    audit_source = (ROOT / "tools" / "audit_legacy_expected_tables_recovery.py").read_text(encoding="utf-8")
    tree = ast.parse(audit_source)
    forbidden_storage_attributes = {"add", "upsert", "update", "delete", "query"}
    called_storage_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "collection"
    }
    forbidden_names = {"save_tool_usage", "save_text_memory"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    check(
        "只读审计入口静态边界",
        not called_storage_attributes & forbidden_storage_attributes
        and not called_names & forbidden_names
        and not imported_roots
        & {"psycopg", "psycopg2", "requests", "httpx", "sqlalchemy"},
    )
    module_source = (ROOT / "training" / "sop" / "legacy_expected_tables_recovery.py").read_text(encoding="utf-8")
    check("纯逻辑模块无存储和网络依赖", not any(name in module_source for name in ("chromadb", "sqlite3", "AgentMemory", "ChromaAgentMemory", "backend.memory", "psycopg", "requests", "httpx")))
    check("测试前后Git状态一致", initial_status == _git_status())

    print(f"expected_tables恢复测试汇总: {PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

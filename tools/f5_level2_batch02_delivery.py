from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.f5_level2_batch01_delivery import (
    open_memory,
    regression_passed,
    regression_summary,
    restore_runtime,
)
from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import (
    close_memory,
    exact_record_checks,
    get_user_environment,
    inventory_evidence,
    manifest,
    preflight_evidence,
    sqlite_record_count,
    write_json,
)
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy


BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch02.json"
SOURCE_EVIDENCE = Path(
    r"E:\3\_training_backups\f5-batch02-uncovered-table-discovery-20260715-211002\evidence"
)
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "f887c15aa0f35042400851a9b679c6c215a74e2af7191e0de7d36f0c7ec1b5c7"
EXPECTED_CANDIDATE_ID = "D1_L2_SE_WATERSHED_RIVER_001"
EXPECTED_SAMPLE_ID = "F5_L2_B02_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["se_watershed_river"]
EXPECTED_COLUMNS = [
    "river_name",
    "river_alias",
    "river_length",
    "watershed_area",
    "source_province",
]
EXPECTED_QUESTION = "查询流域河流的河流名称、别名、河流长度、流域面积和源头省份，最多返回50条"
EXPECTED_SQL = """SELECT river_name,
       river_alias,
       river_length,
       watershed_area,
       source_province
FROM se_watershed_river
LIMIT 50"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", type=Path)
    return parser.parse_args()


def normalized_sql(value: str) -> str:
    return " ".join(value.strip().rstrip(";").split())


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    sample = batch.get("samples", [{}])[0]
    checks = (
        batch.get("schema_version") == "1.0",
        batch.get("training_batch_id") == "level2-f5-batch02-20260715-01",
        batch.get("training_level") == EXPECTED_TRAINING_LEVEL,
        batch.get("status") == "frozen",
        batch.get("expected_new_memory_count") == 1,
        len(batch.get("samples", [])) == 1,
        sample.get("sample_id") == EXPECTED_SAMPLE_ID,
        sample.get("question") == EXPECTED_QUESTION,
        normalized_sql(sample.get("args", {}).get("sql", "")) == normalized_sql(EXPECTED_SQL),
        sample.get("expected_tables") == EXPECTED_TABLES,
        sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
    )
    if not all(checks):
        raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def validate_candidate_source(batch: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads((SOURCE_EVIDENCE / "batch02-discovery-summary.json").read_text(encoding="utf-8"))
    comparison = json.loads((SOURCE_EVIDENCE / "candidate-comparison.json").read_text(encoding="utf-8"))
    duplicates = json.loads((SOURCE_EVIDENCE / "duplicate-analysis.json").read_text(encoding="utf-8"))
    probes = json.loads((SOURCE_EVIDENCE / "database-readonly-probes.json").read_text(encoding="utf-8"))
    matches = [item for item in comparison.get("candidates", []) if item.get("candidate_id") == EXPECTED_CANDIDATE_ID]
    duplicate_matches = [item for item in duplicates.get("candidates", []) if item.get("candidate_id") == EXPECTED_CANDIDATE_ID]
    if len(matches) != 1 or len(duplicate_matches) != 1:
        raise RuntimeError("CANDIDATE_SOURCE_NOT_UNIQUE")
    candidate = matches[0]
    duplicate = duplicate_matches[0]
    sample = batch["samples"][0]
    checks = {
        "recommendation_id_equal": summary.get("batch02_discovery_recommendation") == EXPECTED_CANDIDATE_ID == comparison.get("recommended_candidate_id"),
        "question_equal": candidate.get("question") == sample["question"] == EXPECTED_QUESTION,
        "sql_equal": normalized_sql(candidate.get("sql", "")) == normalized_sql(sample["args"]["sql"]) == normalized_sql(EXPECTED_SQL),
        "expected_tables_equal": candidate.get("expected_tables") == sample["expected_tables"] == EXPECTED_TABLES,
        "training_level_equal": summary.get("recommended_training_level") == EXPECTED_TRAINING_LEVEL,
        "duplicate_found_false": candidate.get("duplicate_found") is False and duplicate.get("duplicate_found") is False,
        "sql_guard_pass": candidate.get("sql_guard_pass") is True,
        "database_success": candidate.get("database_success") is True,
        "result_row_count_50": candidate.get("result_row_count") == 50,
        "semantic_risk_low": candidate.get("semantic_risk") == "LOW",
        "discovery_read_only": probes.get("read_only") is True and probes.get("ddl_dml_executed") is False,
    }
    result = {"candidate_id": EXPECTED_CANDIDATE_ID, "checks": checks, "valid": all(checks.values())}
    if not result["valid"]:
        raise RuntimeError("CANDIDATE_SOURCE_VALIDATION_FAILED")
    return result


def database_validation(sql: str) -> dict[str, Any]:
    import psycopg2

    guard = SQLGuard().validate(sql=sql, query=EXPECTED_QUESTION, deterministic_candidate_tables=EXPECTED_TABLES)
    if not guard.passed or guard.used_tables != EXPECTED_TABLES:
        raise RuntimeError("DATABASE_SQL_GUARD_FAILED")
    required = ("DB_USER", "DB_PASSWORD")
    if any(not os.getenv(name, "").strip() for name in required):
        raise RuntimeError("DB_CREDENTIALS_MISSING")
    with psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5433")),
        database=os.getenv("DB_NAME", "gt_monitor"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
        application_name="vanna-f5-batch02-validation",
        options="-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    ) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
    non_null_counts = {columns[index]: sum(row[index] is not None for row in rows) for index in range(len(columns))}
    type_summary = {
        columns[index]: sorted({type(row[index]).__name__ for row in rows if row[index] is not None})
        for index in range(len(columns))
    }
    result = {
        "database_query_success": True,
        "row_count": len(rows),
        "columns": columns,
        "non_null_counts": non_null_counts,
        "anonymized_type_summary": type_summary,
        "guard": guard.to_dict(),
        "default_transaction_read_only": True,
        "statement_timeout_ms": 30000,
        "lock_timeout_ms": 5000,
        "ddl_dml_executed": False,
    }
    if len(rows) != 50 or columns != EXPECTED_COLUMNS:
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def semantic_duplicate_records(inventory: Any) -> list[dict[str, Any]]:
    duplicates: list[dict[str, Any]] = []
    for record in inventory.records:
        canonical = record.canonical_content or {}
        sql = str((canonical.get("args") or {}).get("sql", ""))
        if not sql:
            continue
        guard = SQLGuard().validate(sql=sql, query=str(canonical.get("question", "")), deterministic_candidate_tables=EXPECTED_TABLES)
        if guard.used_tables == EXPECTED_TABLES:
            duplicates.append({"storage_id": record.storage_id, "classification": record.classification})
    return duplicates


def retrieval_and_injection(memory: Any, record_id: str) -> dict[str, Any]:
    from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    context = ToolContext(
        user=User(id="f5-batch02", username="f5-batch02", group_memberships=[]),
        conversation_id="f5-batch02-retrieval",
        request_id="f5-batch02-retrieval",
        agent_memory=memory,
        metadata={},
    )
    results = asyncio.run(memory.search_similar_usage(EXPECTED_QUESTION, context, limit=5, similarity_threshold=0.0, tool_name_filter="run_sql"))
    ids = [item.memory.memory_id for item in results]
    enhancer = SqlExampleContextEnhancer(memory=memory, sql_guard=SQLGuard(), top_k=5)
    examples = asyncio.run(enhancer._retrieve_examples(EXPECTED_QUESTION))
    target = [item for item in examples if item.get("sample_id") == EXPECTED_SAMPLE_ID]
    filtered = [item for item in enhancer.last_stats.filtered if item.get("sample_id") == EXPECTED_SAMPLE_ID]
    return {
        "top5_record_ids": ids,
        "target_hit": record_id in ids,
        "target_rank": ids.index(record_id) + 1 if record_id in ids else None,
        "target_injected": bool(target),
        "injected_sample_id": target[0].get("sample_id") if target else None,
        "injected_sql_exact": bool(target) and normalized_sql(target[0].get("sql", "")) == normalized_sql(EXPECTED_SQL),
        "target_filtered": bool(filtered),
        "filter_reason": filtered[0].get("reason") if filtered else "NONE",
        "enhancer_stats": asdict(enhancer.last_stats),
    }


def target_result(case_result: dict[str, Any]) -> dict[str, Any]:
    result = case_result["result"]
    sql = result.get("sql", "")
    guard = SQLGuard().validate(sql=sql, query=EXPECTED_QUESTION, deterministic_candidate_tables=EXPECTED_TABLES) if sql else None
    limit = re.search(r"\blimit\s+(\d+)\b", sql, flags=re.I)
    selected = {name.lower() for name in result.get("columns", [])}
    checks = {
        "sql_nonempty": bool(sql),
        "sql_guard_pass": bool(guard and guard.passed),
        "execution_success": bool(result.get("csv_file")) and not result.get("errors"),
        "result_nonempty": result.get("row_count", 0) > 0,
        "used_tables_exact": bool(guard and guard.used_tables == EXPECTED_TABLES),
        "required_columns_present": set(EXPECTED_COLUMNS).issubset(selected),
        "limit_at_most_50": bool(limit and int(limit.group(1)) <= 50),
        "select_star_absent": not bool(re.search(r"select\s+(?:\w+\.)?\*", sql, flags=re.I)),
        "join_absent": not bool(re.search(r"\bjoin\b", sql, flags=re.I)),
    }
    return {
        "checks": checks,
        "accepted": all(checks.values()),
        "sql": sql,
        "row_count": result.get("row_count", 0),
        "columns": result.get("columns", []),
        "used_tables": guard.used_tables if guard else [],
        "guard": guard.to_dict() if guard else None,
    }


def run_regressions(data_dir: Path, agent_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[str], str]:
    from tools.f2_end_to_end_mvp_probe import CASES, redact, run_case, start_server, stop_server

    process = None
    logs: list[str] = []
    key = ""
    try:
        process, logs, _, key = start_server(data_dir, agent_dir, False)
        f2_results = [run_case(case, agent_dir, True) for case in CASES]
        target_case = {"id": "B02_TARGET", "query": EXPECTED_QUESTION, "tables": EXPECTED_TABLES, "limit": 50}
        target_case_result = run_case(target_case, agent_dir, True)
        return (
            {"summary": regression_summary(f2_results), "cases": f2_results},
            {"case": target_case_result, "validation": target_result(target_case_result)},
            logs,
            key,
        )
    finally:
        stop_server(process)


def write_and_verify(memory: Any, adapter: Any, plan: Any, initial_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = adapter.inventory_tool_records()
    preflight = adapter.inspect_plan_records(plan)
    duplicates = semantic_duplicate_records(inventory)
    preflight_payload = {
        "inventory": inventory_evidence(inventory),
        "preflight": preflight_evidence(preflight),
        "target_record_preexisting": plan.items[0].record_id not in preflight.absent_record_ids,
        "semantic_duplicate_found": bool(duplicates),
        "semantic_duplicate_records": duplicates,
        "content_conflict_found": bool(preflight.duplicate_content_conflicts or preflight.legacy_conflicts or preflight.malformed_conflicts),
    }
    if inventory.store_count_before != initial_count or not preflight.executable or len(preflight.absent_record_ids) != 1 or duplicates:
        raise RuntimeError("STORE_PREFLIGHT_FAILED")
    add = adapter.add_planned_record(plan, plan.items[0])
    exact = adapter.get_exact_records([plan.items[0].record_id])
    batch_records = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
    final_inventory = adapter.inventory_tool_records()
    retrieval = retrieval_and_injection(memory, plan.items[0].record_id)
    write_payload = {
        "add_result": asdict(add),
        "initial_count": inventory.store_count_before,
        "created_count": int(add.status == "created"),
        "final_count": final_inventory.store_count_after,
        "record_delta": final_inventory.store_count_after - inventory.store_count_before,
        "exact_checks": exact_record_checks(exact, plan),
        "record_classification": exact[0].record.classification if exact[0].record else None,
        "record_training_level": (exact[0].record.metadata or {}).get("training_level") if exact[0].record else None,
        "batch_record_count": len(batch_records.records),
        "batch_query_issues": [asdict(item) for item in batch_records.issues],
        "retrieval": retrieval,
    }
    accepted = all((
        add.status == "created",
        write_payload["final_count"] == initial_count + 1,
        write_payload["record_delta"] == 1,
        write_payload["exact_checks"]["all_exact"],
        write_payload["record_classification"] == "controlled_tool_record",
        write_payload["record_training_level"] == EXPECTED_TRAINING_LEVEL,
        write_payload["batch_record_count"] == 1,
        not write_payload["batch_query_issues"],
        retrieval["target_hit"],
        retrieval["target_rank"] is not None and retrieval["target_rank"] <= 5,
        retrieval["target_injected"],
        retrieval["injected_sql_exact"],
        not retrieval["target_filtered"],
    ))
    if not accepted:
        raise RuntimeError("STORE_WRITE_ACCEPTANCE_FAILED")
    return preflight_payload, write_payload


def run_isolated_worker(root: Path) -> int:
    evidence = root / "evidence"
    isolated_data = root / "isolated" / "vanna_data"
    agent_data = root / "agent_data"
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "VANNA_DATA_DIR": str(isolated_data),
        "AGENT_DATA_DIR": str(agent_data),
        "HF_HUB_OFFLINE": "1",
        "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0",
    })
    if "agent_config" in sys.modules:
        raise RuntimeError("EARLY_AGENT_CONFIG_IMPORT")
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256 or "", sql_guard=SQLGuard())
    memory = None
    try:
        memory, adapter = open_memory(isolated_data, root)
        preflight, write_result = write_and_verify(memory, adapter, plan, 188)
        write_json(evidence / "isolated-preflight.json", preflight)
        write_json(evidence / "isolated-write-result.json", write_result)
        write_json(evidence / "isolated-retrieval-injection.json", write_result["retrieval"])
        close_memory(memory)
        memory = None
        gc.collect()
        f2, target, logs, key = run_regressions(isolated_data, agent_data)
        write_json(evidence / "isolated-f2-regression.json", f2)
        write_json(evidence / "isolated-target-regression.json", target)
        from tools.f2_end_to_end_mvp_probe import redact
        (evidence / "server-log.txt").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = regression_passed(f2["summary"]) and target["validation"]["accepted"] and sqlite_record_count(isolated_data) == 189
        summary = {
            "accepted": accepted,
            "initial_count": 188,
            "created_count": write_result["created_count"],
            "final_count": write_result["final_count"],
            "preflight": preflight,
            "write": write_result,
            "f2_regression": f2["summary"],
            "target": target["validation"],
        }
        write_json(evidence / "isolated-worker-summary.json", summary)
        return 0 if accepted else 2
    finally:
        close_memory(memory)


def main() -> int:
    args = parse_args()
    if args.isolated_worker:
        return run_isolated_worker(args.isolated_worker.resolve())
    if get_user_environment("VANNA_DATA_DIR") != (True, str(RUNTIME_SOURCE)):
        raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    runtime_before = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 188 or runtime_before["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_INITIAL_STATE_MISMATCH")
    batch = load_batch()
    candidate_source = validate_candidate_source(batch)
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError("BATCH_INVALID")
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256, sql_guard=SQLGuard())
    if not plan.executable or plan.create_count != 1 or plan.resume_same_batch_count or plan.conflict_count:
        raise RuntimeError("PLAN_INVALID")
    database = database_validation(batch["samples"][0]["args"]["sql"])

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = BACKUP_PARENT / f"f5-level2-batch02-{timestamp}"
    evidence = root / "evidence"
    isolated_data = root / "isolated" / "vanna_data"
    backup_root = BACKUP_PARENT / f"f5-level2-batch02-prewrite-{timestamp}"
    backup_data = backup_root / "runtime-vanna_data"
    evidence.mkdir(parents=True)
    write_json(evidence / "batch.json", batch)
    write_json(evidence / "batch-validation.json", validation.to_dict())
    write_json(evidence / "candidate-source-validation.json", candidate_source)
    write_json(evidence / "database-validation.json", database)
    write_json(evidence / "write-plan.json", plan.to_dict())

    backup_root.mkdir(parents=True)
    backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    backup_payload = {
        "runtime_before": runtime_before,
        "backup": backup.destination.to_dict(),
        "record_count": sqlite_record_count(backup_data),
        "verified": backup.destination.content_sha256 == EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup_data) == 188,
    }
    write_json(evidence / "formal-backup.json", backup_payload)
    if not backup_payload["verified"]:
        raise RuntimeError("FORMAL_BACKUP_FAILED")
    isolated_data.parent.mkdir(parents=True)
    create_verified_copy(backup_data, isolated_data, PROJECT_ROOT)
    (root / "agent_data").mkdir()

    worker = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--isolated-worker", str(root)],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    isolated_summary_path = evidence / "isolated-worker-summary.json"
    isolated_summary = json.loads(isolated_summary_path.read_text(encoding="utf-8")) if isolated_summary_path.exists() else {"accepted": False, "worker_output": worker.stdout[-4000:]}
    if worker.returncode != 0 or not isolated_summary.get("accepted"):
        summary = {"f5_batch02_accepted": False, "failure_stage": "isolated", "isolated": isolated_summary, "formal_opened": False, "formal_write_executed": False, "recovery_executed": False}
        write_json(evidence / "f5-batch02-summary.json", summary)
        (evidence / "rollback.txt").write_text("隔离验收失败，正式库未打开、未写入，无需恢复。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2

    current = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 188 or current["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_CHANGED_BEFORE_WRITE")
    recalculated = validate_training_batch(load_batch(), sql_guard=SQLGuard())
    recalculated_plan = build_memory_write_plan(load_batch(), approved_batch_content_sha256=recalculated.batch_content_sha256 or "", sql_guard=SQLGuard())
    if recalculated.batch_content_sha256 != validation.batch_content_sha256 or recalculated_plan.write_plan_sha256 != plan.write_plan_sha256 or recalculated_plan.items[0].record_id != plan.items[0].record_id:
        raise RuntimeError("FORMAL_IDENTITY_RECALCULATION_MISMATCH")

    os.environ.update({"VANNA_DATA_DIR": str(RUNTIME_SOURCE), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    memory = None
    formal_write_started = False
    try:
        memory, adapter = open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent)
        formal_write_started = True
        preflight, write_result = write_and_verify(memory, adapter, plan, 188)
        write_json(evidence / "formal-preflight.json", preflight)
        write_json(evidence / "formal-write-result.json", write_result)
        close_memory(memory)
        memory = None
        gc.collect()
        formal_agent_data = root / "formal-agent_data"
        formal_agent_data.mkdir()
        f2, target, logs, key = run_regressions(RUNTIME_SOURCE, formal_agent_data)
        write_json(evidence / "formal-f2-regression.json", f2)
        write_json(evidence / "formal-target-regression.json", target)
        from tools.f2_end_to_end_mvp_probe import redact
        existing_log = (evidence / "server-log.txt").read_text(encoding="utf-8")
        (evidence / "server-log.txt").write_text(existing_log + "\n--- FORMAL ---\n" + redact("\n".join(logs), [key]), encoding="utf-8")
        after_smoke = sqlite_record_count(RUNTIME_SOURCE)
        accepted = regression_passed(f2["summary"]) and target["validation"]["accepted"] and after_smoke == 189
        if not accepted:
            raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {
            "f5_batch02_accepted": True,
            "root": str(root),
            "backup_path": str(backup_data),
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256,
            "record_id": plan.items[0].record_id,
            "candidate_source": candidate_source,
            "database": database,
            "isolated": isolated_summary,
            "formal": write_result,
            "formal_f2_regression": f2["summary"],
            "formal_target": target["validation"],
            "formal_record_count_after_smoke": after_smoke,
            "recovery_executed": False,
            "memory_delete_executed": False,
            "old_uuid_migration_executed": False,
            "ddl_dml_executed": False,
        }
        write_json(evidence / "f5-batch02-summary.json", summary)
        (evidence / "rollback.txt").write_text(f"失败恢复源：{backup_data}\n不得使用单条Memory删除。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception:
        close_memory(memory)
        memory = None
        gc.collect()
        if formal_write_started:
            restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import copy
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard
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
from training.sop.chroma_tool_memory_adapter import ChromaToolMemoryAdapter
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy


BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch01.json"
F2_EVIDENCE = Path(
    r"E:\3\_training_backups\f2-end-to-end-mvp-20260715-154345\evidence\f2-default-mode-results.json"
)
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_QUESTION = "查询数据字典中的列表类型、列表描述、列表项代码和列表项名称，最多返回50条"
OLD_TRAINING_LEVEL = "level2_f5_batch01_sql_examples"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
R3A_FAILED_SQL = """SELECT monitor_time, m2_value AS pH, m3_value AS 溶解氧, water_quality_level
FROM (
    SELECT monitor_time, m2_value, m3_value, water_quality_level
    FROM wm_waterquality_hour_records
    WHERE station_id = 1408
    ORDER BY monitor_time DESC
    LIMIT 100
) AS recent
ORDER BY monitor_time ASC"""
R3A_Q3 = "查询站点1408最近的水质小时变化趋势，返回监测时间、pH、溶解氧和水质等级，最多100条"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", type=Path)
    return parser.parse_args()


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    if batch.get("training_level") != EXPECTED_TRAINING_LEVEL:
        raise RuntimeError("BATCH_TRAINING_LEVEL_MISMATCH")
    samples = batch.get("samples", [])
    if len(samples) != 1 or samples[0].get("training_level") != EXPECTED_TRAINING_LEVEL:
        raise RuntimeError("SAMPLE_TRAINING_LEVEL_MISMATCH")
    return batch


def compare_old_new_identity(batch: dict[str, Any], new_plan: Any) -> dict[str, Any]:
    old_batch = copy.deepcopy(batch)
    old_batch["training_level"] = OLD_TRAINING_LEVEL
    old_batch["samples"][0]["training_level"] = OLD_TRAINING_LEVEL
    old_validation = validate_training_batch(old_batch, sql_guard=SQLGuard())
    if not old_validation.valid or not old_validation.batch_content_sha256:
        raise RuntimeError("OLD_BATCH_RECONSTRUCTION_INVALID")
    old_plan = build_memory_write_plan(
        old_batch,
        approved_batch_content_sha256=old_validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    result = {
        "old_training_level": OLD_TRAINING_LEVEL,
        "new_training_level": EXPECTED_TRAINING_LEVEL,
        "old_batch_content_sha256": old_validation.batch_content_sha256,
        "new_batch_content_sha256": new_plan.batch_content_sha256,
        "old_write_plan_sha256": old_plan.write_plan_sha256,
        "new_write_plan_sha256": new_plan.write_plan_sha256,
        "record_id_before_correction": old_plan.items[0].record_id,
        "record_id_after_correction": new_plan.items[0].record_id,
        "record_id_unchanged": old_plan.items[0].record_id == new_plan.items[0].record_id,
    }
    if not result["record_id_unchanged"]:
        raise RuntimeError("RECORD_ID_CHANGED_AFTER_TRAINING_LEVEL_CORRECTION")
    return result


def validate_f2_q1(batch: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(F2_EVIDENCE.read_text(encoding="utf-8-sig"))
    cases = payload.get("cases", [])
    matches = [item for item in cases if item.get("id") == "Q1"]
    if len(matches) != 1:
        raise RuntimeError("F2_Q1_EVIDENCE_NOT_UNIQUE")
    q1 = matches[0]
    result = q1.get("result", {})
    evidence_sql = " ".join(str(result.get("sql", "")).strip().rstrip(";").split())
    batch_sql = " ".join(batch["samples"][0]["args"]["sql"].strip().rstrip(";").split())
    checks = {
        "question_equal": q1.get("query") == EXPECTED_QUESTION == batch["samples"][0]["question"],
        "sql_nonempty": bool(evidence_sql),
        "sql_equal_after_outer_whitespace_normalization": evidence_sql == batch_sql,
        "tables_exact": result.get("tables") == ["ad_dict"],
        "sql_guard_passed": result.get("guard", {}).get("passed") is True,
        "execution_success": bool(result.get("csv_file")) and not result.get("errors"),
        "row_count": int(result.get("row_count", 0)),
    }
    if not all(
        (
            checks["question_equal"],
            checks["sql_nonempty"],
            checks["sql_equal_after_outer_whitespace_normalization"],
            checks["tables_exact"],
            checks["sql_guard_passed"],
            checks["execution_success"],
            checks["row_count"] > 0,
        )
    ):
        raise RuntimeError("F2_Q1_EVIDENCE_REJECTED: " + json.dumps(checks, ensure_ascii=False))
    return {"checks": checks, "sql": evidence_sql}


def db_kwargs() -> dict[str, Any]:
    def positive(name: str, default: int) -> int:
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError(name)
        return value

    result: dict[str, Any] = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": positive("DB_PORT", 5433),
        "database": os.getenv("DB_NAME", "gt_monitor"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "connect_timeout": positive("DB_CONNECT_TIMEOUT", 10),
        "application_name": "vanna-f5-batch01-validation",
        "options": " ".join(
            (
                "-c default_transaction_read_only=on",
                f"-c statement_timeout={positive('DB_STATEMENT_TIMEOUT_MS', 30000)}",
                f"-c lock_timeout={positive('DB_LOCK_TIMEOUT_MS', 5000)}",
            )
        ),
    }
    if not result["user"] or not result["password"]:
        raise RuntimeError("DB_CREDENTIALS_MISSING")
    return result


def execute_database_validation(sql: str) -> dict[str, Any]:
    import psycopg2

    with psycopg2.connect(**db_kwargs()) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
    return {
        "database_query_success": True,
        "row_count": len(rows),
        "columns": columns,
        "ddl_dml_executed": False,
    }


def open_memory(path: Path, isolated_root: Path) -> tuple[Any, ChromaToolMemoryAdapter]:
    if Path(os.environ.get("VANNA_DATA_DIR", "")).resolve() != path.resolve():
        raise RuntimeError("PARENT_MEMORY_PATH_MISMATCH")
    from agent_config import EMBEDDING_FUNCTION
    from vanna.integrations.chromadb import ChromaAgentMemory

    memory = ChromaAgentMemory(
        persist_directory=str(path),
        collection_name="tool_memories",
        embedding_function=EMBEDDING_FUNCTION,
    )
    return memory, ChromaToolMemoryAdapter(memory, isolated_root=isolated_root)


def retrieval_and_injection(
    memory: Any, question: str, record_id: str, expected_sql: str
) -> dict[str, Any]:
    from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.user import User
    from vanna.core.tool import ToolContext

    user = User(id="f5-batch01", username="f5-batch01", group_memberships=[])
    context = ToolContext(
        user=user,
        conversation_id="f5-batch01",
        request_id="f5-batch01",
        agent_memory=memory,
        metadata={},
    )
    results = asyncio.run(
        memory.search_similar_usage(
            question,
            context,
            limit=5,
            similarity_threshold=0.0,
            tool_name_filter="run_sql",
        )
    )
    top5_ids = [item.memory.memory_id for item in results]
    enhancer = SqlExampleContextEnhancer(memory=memory, sql_guard=SQLGuard(), top_k=5)
    examples = asyncio.run(enhancer._retrieve_examples(question))
    injected_sample_ids = [item.get("sample_id", "") for item in examples]
    target_examples = [
        item for item in examples if item.get("sample_id") == "F5_L2_B01_SQL_001"
    ]
    filtered = [
        item for item in enhancer.last_stats.filtered
        if item.get("sample_id") == "F5_L2_B01_SQL_001"
    ]
    return {
        "top5_record_ids": top5_ids,
        "target_hit": record_id in top5_ids,
        "target_rank": top5_ids.index(record_id) + 1 if record_id in top5_ids else None,
        "injected_sample_ids": injected_sample_ids,
        "target_injected": "F5_L2_B01_SQL_001" in injected_sample_ids,
        "injected_sql_exact": bool(target_examples)
        and target_examples[0].get("sql") == expected_sql,
        "target_filtered": bool(filtered),
        "target_filter_reason": filtered[0]["reason"] if filtered else "NONE",
        "injected_training_level": EXPECTED_TRAINING_LEVEL if "F5_L2_B01_SQL_001" in injected_sample_ids else None,
        "enhancer_stats": asdict(enhancer.last_stats),
    }


def regression_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "question_count": len(results),
        "question_pass_count": sum(bool(item.get("passed")) for item in results),
        "question_failure_count": sum(not bool(item.get("passed")) for item in results),
        "sql_guard_pass_count": sum(
            bool(item["result"].get("guard", {}).get("passed")) for item in results
        ),
        "sql_execution_success_count": sum(
            bool(item["result"].get("csv_file")) and not item["result"].get("errors")
            for item in results
        ),
        "nonempty_result_count": sum(item["result"].get("row_count", 0) > 0 for item in results),
        "final_answer_count": sum(bool(item["result"].get("answer")) for item in results),
        "chart_spec_pass_count": sum(item["id"] == "Q6" and item.get("passed") for item in results),
        "failed_questions": [item["id"] for item in results if not item.get("passed")],
    }


def regression_passed(summary: dict[str, Any]) -> bool:
    return all(
        (
            summary["question_count"] == 6,
            summary["question_pass_count"] == 6,
            summary["question_failure_count"] == 0,
            summary["sql_guard_pass_count"] == 6,
            summary["sql_execution_success_count"] == 6,
            summary["nonempty_result_count"] == 6,
            summary["final_answer_count"] == 6,
            summary["chart_spec_pass_count"] == 1,
        )
    )


def run_isolated_worker(root: Path) -> int:
    evidence = root / "evidence"
    isolated_data = root / "isolated" / "vanna_data"
    agent_data = root / "agent_data"
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "VANNA_DATA_DIR": str(isolated_data),
            "AGENT_DATA_DIR": str(agent_data),
            "HF_HUB_OFFLINE": "1",
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0",
        }
    )
    if "agent_config" in sys.modules:
        raise RuntimeError("EARLY_AGENT_CONFIG_IMPORT")
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(
        batch,
        approved_batch_content_sha256=validation.batch_content_sha256 or "",
        sql_guard=SQLGuard(),
    )
    memory = None
    process = None
    logs: list[str] = []
    key = ""
    try:
        memory, adapter = open_memory(isolated_data, root)
        inventory = adapter.inventory_tool_records()
        preflight = adapter.inspect_plan_records(plan)
        write_json(
            evidence / "isolated-preflight.json",
            {"inventory": inventory_evidence(inventory), "preflight": preflight_evidence(preflight)},
        )
        add = adapter.add_planned_record(plan, plan.items[0])
        exact = adapter.get_exact_records([plan.items[0].record_id])
        batch_records = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
        final_inventory = adapter.inventory_tool_records()
        retrieval = retrieval_and_injection(
            memory,
            batch["samples"][0]["question"],
            plan.items[0].record_id,
            batch["samples"][0]["args"]["sql"],
        )
        write_result = {
            "add_result": asdict(add),
            "exact_checks": exact_record_checks(exact, plan),
            "final_count": final_inventory.store_count_after,
            "batch_record_count": len(batch_records.records),
            "batch_query_issues": [asdict(item) for item in batch_records.issues],
            "record_training_level": (
                (exact[0].record.metadata or {}).get("training_level")
                if exact[0].record is not None
                else None
            ),
            "retrieval": retrieval,
        }
        write_json(evidence / "isolated-write-result.json", write_result)
        close_memory(memory)
        memory = None

        from tools.f2_end_to_end_mvp_probe import CASES, redact, run_case, start_server, stop_server

        process, logs, _, key = start_server(isolated_data, agent_data, False)
        results = [run_case(case, agent_data, True) for case in CASES]
        stop_server(process)
        process = None
        regression = regression_summary(results)
        write_json(
            evidence / "isolated-regression.json", {"summary": regression, "cases": results}
        )
        (evidence / "server-log.txt").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = all(
            (
                inventory.store_count_before == 187,
                preflight.executable,
                len(preflight.absent_record_ids) == 1,
                not preflight.legacy_conflicts,
                not preflight.duplicate_content_conflicts,
                not preflight.malformed_conflicts,
                add.status == "created",
                final_inventory.store_count_after == 188,
                write_result["exact_checks"]["all_exact"],
                len(batch_records.records) == 1,
                not batch_records.issues,
                retrieval["target_hit"],
                retrieval["target_rank"] is not None and retrieval["target_rank"] <= 5,
                retrieval["target_injected"],
                retrieval["injected_sql_exact"],
                not retrieval["target_filtered"],
                write_result["record_training_level"] == EXPECTED_TRAINING_LEVEL,
                retrieval["injected_training_level"] == EXPECTED_TRAINING_LEVEL,
                retrieval["enhancer_stats"]["injected_count"] >= 1,
                regression_passed(regression),
            )
        )
        summary = {
            "accepted": accepted,
            "initial_count": inventory.store_count_before,
            "created_count": int(add.status == "created"),
            "final_count": final_inventory.store_count_after,
            "retrieval": retrieval,
            "regression": regression,
        }
        write_json(evidence / "isolated-worker-summary.json", summary)
        return 0 if accepted else 2
    finally:
        if process is not None:
            from tools.f2_end_to_end_mvp_probe import stop_server

            stop_server(process)
        close_memory(memory)


def restore_runtime(backup: Path, root: Path, evidence: Path) -> dict[str, Any]:
    incident = root / "failed-formal-vanna_data"
    create_verified_copy(RUNTIME_SOURCE, incident, PROJECT_ROOT)
    if RUNTIME_SOURCE.resolve() != Path(r"E:\3\_runtime\vanna-level1\vanna_data").resolve():
        raise RuntimeError("RUNTIME_PATH_MISMATCH")
    gc.collect()
    for attempt in range(5):
        try:
            shutil.rmtree(RUNTIME_SOURCE)
            break
        except PermissionError:
            if attempt == 4:
                raise
            gc.collect()
            time.sleep(1)
    shutil.copytree(backup, RUNTIME_SOURCE)
    result = {
        "executed": True,
        "incident_path": str(incident),
        "restored_record_count": sqlite_record_count(RUNTIME_SOURCE),
        "restored_sha256": manifest(RUNTIME_SOURCE)["content_sha256"],
    }
    write_json(evidence / "recovery.json", result)
    return result


def main() -> int:
    args = parse_args()
    if args.isolated_worker:
        return run_isolated_worker(args.isolated_worker.resolve())

    if get_user_environment("VANNA_DATA_DIR") != (True, str(RUNTIME_SOURCE)):
        raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    if sqlite_record_count(RUNTIME_SOURCE) != 187:
        raise RuntimeError("RUNTIME_INITIAL_COUNT_NOT_187")
    batch = load_batch()
    f2_check = validate_f2_q1(batch)
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError("BATCH_INVALID")
    plan = build_memory_write_plan(
        batch,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    if not plan.executable or plan.create_count != 1 or plan.conflict_count:
        raise RuntimeError("PLAN_INVALID")
    identity_comparison = compare_old_new_identity(batch, plan)
    database = execute_database_validation(batch["samples"][0]["args"]["sql"])
    if database["row_count"] <= 0:
        raise RuntimeError("DATABASE_EMPTY_RESULT")
    r3a_guard = SQLGuard().validate(
        sql=R3A_FAILED_SQL,
        query=R3A_Q3,
        deterministic_candidate_tables=["wm_waterquality_hour_records"],
    )
    r3a_database = execute_database_validation(R3A_FAILED_SQL)
    if not all(
        (
            r3a_guard.passed,
            r3a_guard.severity == "ok",
            r3a_guard.used_tables == ["wm_waterquality_hour_records"],
            not r3a_guard.unknown_tables,
            not r3a_guard.unknown_columns,
            r3a_database["database_query_success"],
            r3a_database["row_count"] > 0,
        )
    ):
        raise RuntimeError("R3A_FAILED_SQL_VALIDATION_FAILED")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = BACKUP_PARENT / f"f5-level2-batch01-r3b-{timestamp}"
    evidence = root / "evidence"
    isolated_parent = root / "isolated"
    isolated_data = isolated_parent / "vanna_data"
    backup_root = BACKUP_PARENT / f"f5-level2-batch01-r3b-prewrite-{timestamp}"
    backup_data = backup_root / "runtime-vanna_data"
    evidence.mkdir(parents=True)
    isolated_parent.mkdir(parents=True)

    write_json(evidence / "batch.json", batch)
    write_json(
        evidence / "batch-validation.json",
        {**validation.to_dict(), "f2_q1": f2_check, "identity_comparison": identity_comparison},
    )
    write_json(evidence / "database-validation.json", database)
    write_json(
        evidence / "r3a-failed-sql-validation.json",
        {
            "sql": R3A_FAILED_SQL,
            "sql_sha256": hashlib.sha256(R3A_FAILED_SQL.encode("utf-8")).hexdigest(),
            "guard": r3a_guard.to_dict(),
            "database": r3a_database,
        },
    )
    write_json(evidence / "write-plan.json", plan.to_dict())
    runtime_before = manifest(RUNTIME_SOURCE)
    create_verified_copy(RUNTIME_SOURCE, isolated_data, PROJECT_ROOT)

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
    isolated_summary = json.loads(
        (evidence / "isolated-worker-summary.json").read_text(encoding="utf-8")
    )
    if worker.returncode != 0 or not isolated_summary.get("accepted"):
        summary = {
            "f5_batch01_accepted": False,
            "failure_stage": "isolated_rehearsal",
            "worker_exit_code": worker.returncode,
            "worker_output": worker.stdout[-4000:],
            "root": str(root),
            "backup_path": None,
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256,
            "identity_comparison": identity_comparison,
            "isolated": isolated_summary,
            "formal_opened": False,
            "formal_write_executed": False,
            "recovery_executed": False,
        }
        write_json(evidence / "f5-batch01-summary.json", summary)
        (evidence / "rollback.txt").write_text(
            "隔离预演失败，正式运行库未打开、未写入，无需恢复。\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 2

    # 仅隔离预演全部通过后，才创建正式备份并进入正式写入链路。
    backup_root.mkdir(parents=True)
    backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    write_json(
        evidence / "formal-backup.json",
        {
            "runtime_before": runtime_before,
            "backup": backup.destination.to_dict(),
            "record_count": sqlite_record_count(backup_data),
            "verified": backup.destination.content_sha256 == runtime_before["content_sha256"],
        },
    )
    os.environ.update(
        {
            "VANNA_DATA_DIR": str(RUNTIME_SOURCE),
            "HF_HUB_OFFLINE": "1",
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0",
        }
    )
    memory = None
    adapter = None
    process = None
    formal_write_started = False
    recovery: dict[str, Any] = {"executed": False}
    try:
        memory, adapter = open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent)
        inventory = adapter.inventory_tool_records()
        preflight = adapter.inspect_plan_records(plan)
        write_json(
            evidence / "formal-preflight.json",
            {"inventory": inventory_evidence(inventory), "preflight": preflight_evidence(preflight)},
        )
        if inventory.store_count_before != 187 or not preflight.executable:
            raise RuntimeError("FORMAL_PREFLIGHT_FAILED")
        formal_write_started = True
        add = adapter.add_planned_record(plan, plan.items[0])
        exact = adapter.get_exact_records([plan.items[0].record_id])
        batch_records = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
        final_inventory = adapter.inventory_tool_records()
        retrieval = retrieval_and_injection(
            memory,
            batch["samples"][0]["question"],
            plan.items[0].record_id,
            batch["samples"][0]["args"]["sql"],
        )
        formal_write = {
            "add_result": asdict(add),
            "initial_count": inventory.store_count_before,
            "final_count": final_inventory.store_count_after,
            "record_delta": final_inventory.store_count_after - inventory.store_count_before,
            "exact_checks": exact_record_checks(exact, plan),
            "batch_record_count": len(batch_records.records),
            "batch_query_issues": [asdict(item) for item in batch_records.issues],
            "record_training_level": (
                (exact[0].record.metadata or {}).get("training_level")
                if exact[0].record is not None
                else None
            ),
            "retrieval": retrieval,
        }
        write_json(evidence / "formal-write-result.json", formal_write)
        close_memory(memory)
        memory = None
        adapter = None
        gc.collect()

        from tools.f2_end_to_end_mvp_probe import CASES, redact, run_case, start_server, stop_server

        agent_data = root / "formal-agent_data"
        agent_data.mkdir()
        process, logs, _, key = start_server(RUNTIME_SOURCE, agent_data, False)
        results = [run_case(case, agent_data, True) for case in CASES]
        stop_server(process)
        process = None
        formal_regression = regression_summary(results)
        q1 = next(item for item in results if item["id"] == "Q1")
        q3 = next(item for item in results if item["id"] == "Q3")
        write_json(
            evidence / "formal-regression.json",
            {"summary": formal_regression, "q1": q1, "q3": q3, "cases": results},
        )
        (evidence / "server-log.txt").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        after_smoke_count = sqlite_record_count(RUNTIME_SOURCE)
        accepted = all(
            (
                add.status == "created",
                formal_write["final_count"] == 188,
                formal_write["record_delta"] == 1,
                formal_write["exact_checks"]["all_exact"],
                formal_write["batch_record_count"] == 1,
                not formal_write["batch_query_issues"],
                retrieval["target_hit"],
                retrieval["target_injected"],
                retrieval["injected_sql_exact"],
                not retrieval["target_filtered"],
                formal_write["record_training_level"] == EXPECTED_TRAINING_LEVEL,
                regression_passed(formal_regression),
                q1["result"]["tables"] == ["ad_dict"],
                q3["passed"],
                bool(q3["result"]["sql"]),
                q3["result"]["guard"]["passed"],
                bool(q3["result"]["csv_file"]),
                q3["result"]["row_count"] > 0,
                q3["result"]["tables"] == ["wm_waterquality_hour_records"],
                after_smoke_count == 188,
            )
        )
        if not accepted:
            raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {
            "f5_batch01_accepted": True,
            "root": str(root),
            "backup_path": str(backup_data),
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256,
            "identity_comparison": identity_comparison,
            "database": database,
            "r3a_failed_sql_guard": r3a_guard.to_dict(),
            "r3a_failed_sql_database": r3a_database,
            "isolated": isolated_summary,
            "formal": formal_write,
            "formal_regression": formal_regression,
            "formal_record_count_after_smoke": after_smoke_count,
            "recovery_executed": False,
            "memory_delete_executed": False,
            "old_uuid_migration_executed": False,
            "ddl_dml_executed": False,
        }
        write_json(evidence / "f5-batch01-summary.json", summary)
        (evidence / "rollback.txt").write_text(
            f"失败恢复源：{backup_data}\n用户级 VANNA_DATA_DIR 不应修改。\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception:
        close_memory(memory)
        memory = None
        adapter = None
        gc.collect()
        if process is not None:
            from tools.f2_end_to_end_mvp_probe import stop_server

            stop_server(process)
        if formal_write_started:
            recovery = restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

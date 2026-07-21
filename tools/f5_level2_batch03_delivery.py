from __future__ import annotations

import argparse
import gc
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.f5_level2_batch02_delivery as common
from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import (
    close_memory,
    get_user_environment,
    manifest,
    sqlite_record_count,
    write_json,
)
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy


BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch03.json"
SOURCE_EVIDENCE = Path(
    r"E:\3\_training_backups\f5-batch03-meteorological-scope-20260716-094041\evidence"
)
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "ab6be2a4901466830531073aee61ae1d62361c57cf18245a39cf23300607b71c"
EXPECTED_CANDIDATE_ID = "D1_L2_WM_METEOROLOGICAL_INFO_002"
EXPECTED_SAMPLE_ID = "F5_L2_B03_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["wm_meteorological_info"]
EXPECTED_COLUMNS = [
    "station_code",
    "station_name",
    "short_name",
    "region_code",
    "belong_to_city",
]
EXPECTED_QUESTION = "查询气象站的站点编码、站点名称、简称、行政区编码和所属城市，最多返回50条"
EXPECTED_SQL = """SELECT station_code,
       station_name,
       short_name,
       region_code,
       belong_to_city
FROM wm_meteorological_info
LIMIT 50"""
BATCH02_QUESTION = "查询流域河流的河流名称、别名、河流长度、流域面积和源头省份，最多返回50条"
BATCH02_TABLES = ["se_watershed_river"]
BATCH02_COLUMNS = [
    "river_name",
    "river_alias",
    "river_length",
    "watershed_area",
    "source_province",
]


def configure_common() -> None:
    common.BATCH_FILE = BATCH_FILE
    common.RUNTIME_SOURCE = RUNTIME_SOURCE
    common.BACKUP_PARENT = BACKUP_PARENT
    common.EXPECTED_FORMAL_SHA256 = EXPECTED_FORMAL_SHA256
    common.EXPECTED_CANDIDATE_ID = EXPECTED_CANDIDATE_ID
    common.EXPECTED_SAMPLE_ID = EXPECTED_SAMPLE_ID
    common.EXPECTED_TRAINING_LEVEL = EXPECTED_TRAINING_LEVEL
    common.EXPECTED_TABLES = EXPECTED_TABLES
    common.EXPECTED_COLUMNS = EXPECTED_COLUMNS
    common.EXPECTED_QUESTION = EXPECTED_QUESTION
    common.EXPECTED_SQL = EXPECTED_SQL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", type=Path)
    return parser.parse_args()


def normalized_sql(value: str) -> str:
    return common.normalized_sql(value)


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    samples = batch.get("samples", [])
    sample = samples[0] if len(samples) == 1 else {}
    checks = (
        batch.get("schema_version") == "1.0",
        batch.get("training_batch_id") == "level2-f5-batch03-20260716-01",
        batch.get("training_level") == EXPECTED_TRAINING_LEVEL,
        batch.get("status") == "frozen",
        batch.get("source") == "F5 Batch 03-S1候选范围确认真实验证结果",
        batch.get("expected_new_memory_count") == 1,
        len(samples) == 1,
        sample.get("sample_id") == EXPECTED_SAMPLE_ID,
        sample.get("question") == EXPECTED_QUESTION,
        normalized_sql(sample.get("args", {}).get("sql", "")) == normalized_sql(EXPECTED_SQL),
        sample.get("expected_tables") == EXPECTED_TABLES,
        sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
        sample.get("expected_behavior") == "返回最多50条气象站编码、名称、简称、行政区编码和所属城市",
    )
    if not all(checks):
        raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def validate_candidate_source(batch: dict[str, Any]) -> dict[str, Any]:
    source = {
        name: json.loads((SOURCE_EVIDENCE / name).read_text(encoding="utf-8"))
        for name in (
            "candidate-source-validation.json",
            "duplicate-analysis.json",
            "metadata-validation.json",
            "database-readonly-validation.json",
            "batch03-scope-summary.json",
        )
    }
    summary = source["batch03-scope-summary.json"]
    duplicate = source["duplicate-analysis.json"]
    database = source["database-readonly-validation.json"]
    sample = batch["samples"][0]
    non_null = database.get("field_non_null_counts", {})
    checks = {
        "candidate_id_equal": summary.get("candidate_id") == EXPECTED_CANDIDATE_ID,
        "candidate_source_valid": summary.get("candidate_source_valid") is True,
        "question_equal": summary.get("candidate_question") == sample["question"] == EXPECTED_QUESTION,
        "sql_equal": normalized_sql(summary.get("candidate_sql", "")) == normalized_sql(sample["args"]["sql"]) == normalized_sql(EXPECTED_SQL),
        "expected_tables_equal": summary.get("candidate_expected_tables") == sample["expected_tables"] == EXPECTED_TABLES,
        "training_level_equal": summary.get("candidate_training_level") == EXPECTED_TRAINING_LEVEL,
        "duplicate_found_false": summary.get("current_duplicate_found") is False and duplicate.get("current_duplicate_found") is False,
        "sql_guard_pass": summary.get("sql_guard_pass") is True,
        "database_success": summary.get("database_query_success") is True,
        "result_row_count_7": summary.get("database_result_row_count") == 7,
        "semantic_risk_low": summary.get("semantic_risk") == "LOW",
        "core_data_quality_pass": summary.get("core_data_quality_pass") is True,
        "station_code_non_null_7": non_null.get("station_code") == 7,
        "station_name_non_null_7": non_null.get("station_name") == 7,
        "station_code_unique_7": database.get("station_code_unique_count") == 7,
        "station_name_unique_7": database.get("station_name_unique_count") == 7,
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
    if not os.getenv("DB_USER", "").strip() or not os.getenv("DB_PASSWORD", "").strip():
        raise RuntimeError("DB_CREDENTIALS_MISSING")
    with psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5433")),
        database=os.getenv("DB_NAME", "gt_monitor"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
        application_name="vanna-f5-batch03-validation",
        options="-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    ) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
    non_null = {columns[i]: sum(row[i] is not None for row in rows) for i in range(len(columns))}
    types = {columns[i]: sorted({type(row[i]).__name__ for row in rows if row[i] is not None}) for i in range(len(columns))}
    result = {
        "database_query_success": True,
        "row_count": len(rows),
        "columns": columns,
        "non_null_counts": non_null,
        "anonymized_type_summary": types,
        "station_code_unique_count": len({row[0] for row in rows if row[0] is not None}),
        "station_name_unique_count": len({row[1] for row in rows if row[1] is not None}),
        "guard": guard.to_dict(),
        "default_transaction_read_only": True,
        "statement_timeout_ms": 30000,
        "lock_timeout_ms": 5000,
        "ddl_dml_executed": False,
    }
    if not all((len(rows) == 7, columns == EXPECTED_COLUMNS, result["station_code_unique_count"] == 7, result["station_name_unique_count"] == 7)):
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def validate_target(case_result: dict[str, Any], *, question: str, tables: list[str], columns: list[str], expected_rows: int | None) -> dict[str, Any]:
    result = case_result["result"]
    sql = str(result.get("sql", ""))
    guard = SQLGuard().validate(sql=sql, query=question, deterministic_candidate_tables=tables) if sql else None
    limit = re.search(r"\blimit\s+(\d+)\b", sql, flags=re.I)
    checks = {
        "sql_nonempty": bool(sql),
        "sql_guard_pass": bool(guard and guard.passed),
        "execution_success": bool(result.get("csv_file")) and not result.get("errors"),
        "result_nonempty": result.get("row_count", 0) > 0,
        "result_row_count_exact": expected_rows is None or result.get("row_count") == expected_rows,
        "used_tables_exact": bool(guard and guard.used_tables == tables),
        "required_columns_present": set(columns).issubset({str(name).lower() for name in result.get("columns", [])}),
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


def run_regressions(data_dir: Path, agent_dir: Path) -> tuple[dict[str, Any], list[str], str]:
    from tools.f2_end_to_end_mvp_probe import CASES, run_case, start_server, stop_server

    process = None
    logs: list[str] = []
    key = ""
    try:
        process, logs, _, key = start_server(data_dir, agent_dir, False)
        f2_cases = [run_case(case, agent_dir, True) for case in CASES]
        batch02_case = run_case({"id": "B02_TARGET", "query": BATCH02_QUESTION, "tables": BATCH02_TABLES, "limit": 50}, agent_dir, True)
        batch03_case = run_case({"id": "B03_TARGET", "query": EXPECTED_QUESTION, "tables": EXPECTED_TABLES, "limit": 50}, agent_dir, True)
        f2_summary = common.regression_summary(f2_cases)
        batch02 = validate_target(batch02_case, question=BATCH02_QUESTION, tables=BATCH02_TABLES, columns=BATCH02_COLUMNS, expected_rows=50)
        batch03 = validate_target(batch03_case, question=EXPECTED_QUESTION, tables=EXPECTED_TABLES, columns=EXPECTED_COLUMNS, expected_rows=7)
        passed = f2_summary["question_pass_count"] + int(batch02["accepted"]) + int(batch03["accepted"])
        return ({
            "question_count": 8,
            "question_pass_count": passed,
            "accepted": common.regression_passed(f2_summary) and batch02["accepted"] and batch03["accepted"],
            "f2_summary": f2_summary,
            "f2_cases": f2_cases,
            "batch02_case": batch02_case,
            "batch02_validation": batch02,
            "batch03_case": batch03_case,
            "batch03_validation": batch03,
        }, logs, key)
    finally:
        stop_server(process)


def run_isolated_worker(root: Path) -> int:
    evidence = root / "evidence"
    isolated_data = root / "isolated" / "vanna_data"
    agent_data = root / "agent_data"
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update({"VANNA_DATA_DIR": str(isolated_data), "AGENT_DATA_DIR": str(agent_data), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    if "agent_config" in sys.modules:
        raise RuntimeError("EARLY_AGENT_CONFIG_IMPORT")
    configure_common()
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256 or "", sql_guard=SQLGuard())
    memory = None
    try:
        memory, adapter = common.open_memory(isolated_data, root)
        preflight, write_result = common.write_and_verify(memory, adapter, plan, 189)
        write_json(evidence / "isolated-preflight.json", preflight)
        write_json(evidence / "isolated-write-result.json", write_result)
        write_json(evidence / "isolated-retrieval-injection.json", write_result["retrieval"])
        close_memory(memory)
        memory = None
        gc.collect()
        regression, logs, key = run_regressions(isolated_data, agent_data)
        write_json(evidence / "isolated-regression.json", regression)
        from tools.f2_end_to_end_mvp_probe import redact
        (evidence / "server-log.txt").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = regression["accepted"] and sqlite_record_count(isolated_data) == 190
        summary = {"accepted": accepted, "initial_count": 189, "created_count": write_result["created_count"], "final_count": write_result["final_count"], "preflight": preflight, "write": write_result, "regression": regression}
        write_json(evidence / "isolated-worker-summary.json", summary)
        return 0 if accepted else 2
    finally:
        close_memory(memory)


def main() -> int:
    args = parse_args()
    configure_common()
    if args.isolated_worker:
        return run_isolated_worker(args.isolated_worker.resolve())
    if get_user_environment("VANNA_DATA_DIR") != (True, str(RUNTIME_SOURCE)):
        raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    runtime_before = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 189 or runtime_before["content_sha256"] != EXPECTED_FORMAL_SHA256:
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
    root = BACKUP_PARENT / f"f5-level2-batch03-{timestamp}"
    evidence = root / "evidence"
    isolated_data = root / "isolated" / "vanna_data"
    backup_data = BACKUP_PARENT / f"f5-level2-batch03-prewrite-{timestamp}" / "runtime-vanna_data"
    evidence.mkdir(parents=True)
    write_json(evidence / "batch.json", batch)
    write_json(evidence / "batch-validation.json", validation.to_dict())
    write_json(evidence / "candidate-source-validation.json", candidate_source)
    write_json(evidence / "database-validation.json", database)
    write_json(evidence / "write-plan.json", plan.to_dict())

    backup_data.parent.mkdir(parents=True)
    backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    backup_payload = {"runtime_before": runtime_before, "backup": backup.destination.to_dict(), "record_count": sqlite_record_count(backup_data), "verified": backup.destination.content_sha256 == EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup_data) == 189}
    write_json(evidence / "formal-backup.json", backup_payload)
    if not backup_payload["verified"]:
        raise RuntimeError("FORMAL_BACKUP_FAILED")
    isolated_data.parent.mkdir(parents=True)
    create_verified_copy(backup_data, isolated_data, PROJECT_ROOT)
    (root / "agent_data").mkdir()

    worker = subprocess.run([str(PYTHON_EXE), str(Path(__file__).resolve()), "--isolated-worker", str(root)], cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    isolated_path = evidence / "isolated-worker-summary.json"
    isolated = json.loads(isolated_path.read_text(encoding="utf-8")) if isolated_path.exists() else {"accepted": False, "worker_output": worker.stdout[-4000:]}
    if worker.returncode != 0 or not isolated.get("accepted"):
        summary = {"f5_batch03_accepted": False, "failure_stage": "isolated", "isolated": isolated, "formal_opened": False, "formal_write_executed": False, "recovery_executed": False}
        write_json(evidence / "f5-batch03-summary.json", summary)
        (evidence / "rollback.txt").write_text("隔离验收失败，正式库未打开、未写入，无需恢复。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2

    current = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 189 or current["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_CHANGED_BEFORE_WRITE")
    recalculated = validate_training_batch(load_batch(), sql_guard=SQLGuard())
    recalculated_plan = build_memory_write_plan(load_batch(), approved_batch_content_sha256=recalculated.batch_content_sha256 or "", sql_guard=SQLGuard())
    if (recalculated.batch_content_sha256, recalculated_plan.write_plan_sha256, recalculated_plan.items[0].record_id) != (validation.batch_content_sha256, plan.write_plan_sha256, plan.items[0].record_id):
        raise RuntimeError("FORMAL_IDENTITY_RECALCULATION_MISMATCH")

    os.environ.update({"VANNA_DATA_DIR": str(RUNTIME_SOURCE), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    memory = None
    formal_write_started = False
    try:
        memory, adapter = common.open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent)
        formal_write_started = True
        preflight, write_result = common.write_and_verify(memory, adapter, plan, 189)
        write_json(evidence / "formal-preflight.json", preflight)
        write_json(evidence / "formal-write-result.json", write_result)
        close_memory(memory)
        memory = None
        gc.collect()
        formal_agent = root / "formal-agent_data"
        formal_agent.mkdir()
        regression, logs, key = run_regressions(RUNTIME_SOURCE, formal_agent)
        write_json(evidence / "formal-regression.json", regression)
        from tools.f2_end_to_end_mvp_probe import redact
        prior_log = (evidence / "server-log.txt").read_text(encoding="utf-8")
        (evidence / "server-log.txt").write_text(prior_log + "\n--- FORMAL ---\n" + redact("\n".join(logs), [key]), encoding="utf-8")
        after_smoke = sqlite_record_count(RUNTIME_SOURCE)
        if not regression["accepted"] or after_smoke != 190:
            raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {
            "f5_batch03_accepted": True,
            "root": str(root),
            "backup_path": str(backup_data),
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256,
            "record_id": plan.items[0].record_id,
            "candidate_source": candidate_source,
            "database": database,
            "isolated": isolated,
            "formal": write_result,
            "formal_regression": regression,
            "formal_record_count_after_smoke": after_smoke,
            "recovery_executed": False,
            "memory_delete_executed": False,
            "old_uuid_migration_executed": False,
            "ddl_dml_executed": False,
        }
        write_json(evidence / "f5-batch03-summary.json", summary)
        (evidence / "rollback.txt").write_text(f"失败恢复源：{backup_data}\n不得使用单条Memory删除。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception:
        close_memory(memory)
        memory = None
        gc.collect()
        if formal_write_started:
            common.restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

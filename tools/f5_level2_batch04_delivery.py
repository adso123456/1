from __future__ import annotations

import argparse
import gc
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.f5_level2_batch03_delivery as previous
from tools.sql_guard import SQLGuard
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


BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch04.json"
SOURCE_EVIDENCE = Path(
    r"E:\3\_training_backups\f5-batch04-uncovered-table-discovery-20260716-103156\evidence"
)
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "a9698c192f90fe764a54501444e9134d3e41af8a9fc76299102548c4652f2ec9"
EXPECTED_CANDIDATE_ID = "D4_L2_GIS_CONTROL_UNIT_001"
EXPECTED_SAMPLE_ID = "F5_L2_B04_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["gis_control_unit"]
EXPECTED_COLUMNS = ["control_unit_code", "control_unit_name", "area"]
EXPECTED_QUESTION = "查询水环境管控单元的编码、名称和面积，最多返回50条"
EXPECTED_SQL = """SELECT control_unit_code,
       control_unit_name,
       area
FROM gis_control_unit
LIMIT 50"""
BATCH02_QUESTION = previous.BATCH02_QUESTION
BATCH02_TABLES = previous.BATCH02_TABLES
BATCH02_COLUMNS = previous.BATCH02_COLUMNS
BATCH03_QUESTION = previous.EXPECTED_QUESTION
BATCH03_TABLES = previous.EXPECTED_TABLES
BATCH03_COLUMNS = previous.EXPECTED_COLUMNS


def configure_existing_helpers() -> None:
    previous.BATCH_FILE = BATCH_FILE
    previous.RUNTIME_SOURCE = RUNTIME_SOURCE
    previous.BACKUP_PARENT = BACKUP_PARENT
    previous.EXPECTED_FORMAL_SHA256 = EXPECTED_FORMAL_SHA256
    previous.EXPECTED_CANDIDATE_ID = EXPECTED_CANDIDATE_ID
    previous.EXPECTED_SAMPLE_ID = EXPECTED_SAMPLE_ID
    previous.EXPECTED_TRAINING_LEVEL = EXPECTED_TRAINING_LEVEL
    previous.EXPECTED_TABLES = EXPECTED_TABLES
    previous.EXPECTED_COLUMNS = EXPECTED_COLUMNS
    previous.EXPECTED_QUESTION = EXPECTED_QUESTION
    previous.EXPECTED_SQL = EXPECTED_SQL
    previous.configure_common()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", type=Path)
    parser.add_argument("--isolated-data", type=Path)
    parser.add_argument("--evidence-prefix")
    return parser.parse_args()


def normalized_sql(value: str) -> str:
    return previous.normalized_sql(value)


def sanitized(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if key == "answer" and isinstance(child, str):
                result[key] = {"redacted": True, "nonempty": bool(child.strip()), "character_count": len(child)}
            elif key == "event_rows" and isinstance(child, list):
                result[key] = {"redacted": True, "row_count_in_stream": len(child)}
            else:
                result[key] = sanitized(child)
        return result
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    samples = batch.get("samples", [])
    sample = samples[0] if len(samples) == 1 else {}
    checks = (
        batch.get("schema_version") == "1.0",
        batch.get("training_batch_id") == "level2-f5-batch04-20260716-01",
        batch.get("training_level") == EXPECTED_TRAINING_LEVEL,
        batch.get("status") == "frozen",
        batch.get("source") == "F5 Batch 04-D1候选发现真实验证结果",
        batch.get("expected_new_memory_count") == 1,
        len(samples) == 1,
        sample.get("sample_id") == EXPECTED_SAMPLE_ID,
        sample.get("question") == EXPECTED_QUESTION,
        normalized_sql(sample.get("args", {}).get("sql", "")) == normalized_sql(EXPECTED_SQL),
        sample.get("expected_tables") == EXPECTED_TABLES,
        sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
        sample.get("expected_behavior") == "返回最多50条水环境管控单元编码、名称和面积；面积字段单位为平方千米",
    )
    if not all(checks):
        raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def validate_candidate_source(batch: dict[str, Any]) -> dict[str, Any]:
    names = (
        "current-tool-memory-coverage.json",
        "current-uncovered-tables.json",
        "metadata-shortlist.json",
        "database-readonly-probes.json",
        "candidate-comparison.json",
        "duplicate-analysis.json",
        "batch04-discovery-summary.json",
    )
    source = {name: json.loads((SOURCE_EVIDENCE / name).read_text(encoding="utf-8")) for name in names}
    summary = source["batch04-discovery-summary.json"]
    candidates = [item for item in source["candidate-comparison.json"]["candidates"] if item.get("candidate_id") == EXPECTED_CANDIDATE_ID]
    duplicate = [item for item in source["duplicate-analysis.json"]["candidates"] if item.get("candidate_id") == EXPECTED_CANDIDATE_ID]
    if len(candidates) != 1 or len(duplicate) != 1:
        raise RuntimeError("CANDIDATE_SOURCE_NOT_UNIQUE")
    candidate = candidates[0]
    coverage = source["current-tool-memory-coverage.json"]
    sample = batch["samples"][0]
    checks = {
        "candidate_id_equal": summary.get("batch04_discovery_recommendation") == EXPECTED_CANDIDATE_ID,
        "table_equal": candidate.get("table") == EXPECTED_TABLES[0],
        "question_equal": candidate.get("question") == sample["question"] == EXPECTED_QUESTION,
        "sql_equal": normalized_sql(candidate.get("sql", "")) == normalized_sql(sample["args"]["sql"]) == normalized_sql(EXPECTED_SQL),
        "expected_tables_equal": candidate.get("expected_tables") == sample["expected_tables"] == EXPECTED_TABLES,
        "training_level_equal": summary.get("recommended_training_level") == EXPECTED_TRAINING_LEVEL,
        "duplicate_found_false": candidate.get("duplicate_found") is False and duplicate[0].get("duplicate_found") is False,
        "sql_guard_pass": candidate.get("sql_guard_pass") is True,
        "database_success": candidate.get("database_success") is True,
        "result_row_count_15": candidate.get("result_row_count") == 15,
        "semantic_risk_low": candidate.get("semantic_risk") == "LOW",
        "legacy_coverage_expansion": summary.get("legacy_coverage_expansion") == "YES",
        "legacy_count_64": coverage.get("legacy_tool_memory_count") == 64,
        "controlled_count_3": coverage.get("controlled_tool_memory_count") == 3,
        "tool_total_67": coverage.get("total_tool_memory_count") == 67,
        "table_currently_uncovered": EXPECTED_TABLES[0] not in coverage.get("current_covered_tables", []),
    }
    result = {"candidate_id": EXPECTED_CANDIDATE_ID, "checks": checks, "valid": all(checks.values())}
    if not result["valid"]:
        raise RuntimeError("CANDIDATE_SOURCE_VALIDATION_FAILED")
    return result


def validate_metadata() -> dict[str, Any]:
    rows = json.loads((PROJECT_ROOT / "agent_data" / "column_metadata_index.json").read_text(encoding="utf-8"))
    table_rows = [item for item in rows if item.get("table") == EXPECTED_TABLES[0]]
    fields = {item["column"]: item for item in table_rows}
    checks = {
        "table_comment": bool(table_rows) and table_rows[0].get("table_comment") == "水环境管控单元表",
        "control_unit_code_comment": fields.get("control_unit_code", {}).get("comment") == "控制单元编码",
        "control_unit_name_comment": fields.get("control_unit_name", {}).get("comment") == "控制单元名称",
        "area_comment": fields.get("area", {}).get("comment") == "面积（km2）",
        "control_unit_code_type": fields.get("control_unit_code", {}).get("type") == "character varying(100)",
        "control_unit_name_type": fields.get("control_unit_name", {}).get("type") == "character varying(254)",
        "area_type": fields.get("area", {}).get("type") == "double precision",
        "forbidden_columns_absent": not any(name in normalized_sql(EXPECTED_SQL).lower().split() for name in ("geom", "create_by", "update_by")),
    }
    result = {"table": EXPECTED_TABLES[0], "checks": checks, "valid": all(checks.values()), "area_unit": "平方千米"}
    if not result["valid"]:
        raise RuntimeError("METADATA_VALIDATION_FAILED")
    return result


def database_validation(sql: str) -> dict[str, Any]:
    import psycopg2

    guard = SQLGuard().validate(sql=sql, query=EXPECTED_QUESTION, deterministic_candidate_tables=EXPECTED_TABLES)
    if not guard.passed or guard.used_tables != EXPECTED_TABLES:
        raise RuntimeError("DATABASE_SQL_GUARD_FAILED")
    if not os.getenv("DB_USER", "").strip() or not os.getenv("DB_PASSWORD", "").strip():
        raise RuntimeError("DB_CREDENTIALS_MISSING")
    with psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5433")),
        database=os.getenv("DB_NAME", "gt_monitor"), user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        connect_timeout=10, application_name="vanna-f5-batch04-validation",
        options="-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    ) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
    non_null = {columns[i]: sum(row[i] is not None for row in rows) for i in range(len(columns))}
    result = {
        "database_query_success": True, "row_count": len(rows), "columns": columns,
        "non_null_counts": non_null,
        "anonymized_type_summary": {columns[i]: sorted({type(row[i]).__name__ for row in rows if row[i] is not None}) for i in range(len(columns))},
        "control_unit_code_unique_count": len({row[0] for row in rows if row[0] is not None}),
        "control_unit_name_unique_count": len({row[1] for row in rows if row[1] is not None}),
        "guard": guard.to_dict(), "default_transaction_read_only": True,
        "statement_timeout_ms": 30000, "lock_timeout_ms": 5000, "ddl_dml_executed": False,
    }
    if not all((len(rows) == 15, columns == EXPECTED_COLUMNS, all(non_null.get(name) == 15 for name in EXPECTED_COLUMNS), result["control_unit_code_unique_count"] > 1, result["control_unit_name_unique_count"] > 1)):
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def validate_target(case_result: dict[str, Any], *, question: str, tables: list[str], columns: list[str], expected_rows: int) -> dict[str, Any]:
    result = previous.validate_target(case_result, question=question, tables=tables, columns=columns, expected_rows=expected_rows)
    sql = str(result.get("sql", ""))
    result["checks"]["geom_absent"] = "geom" not in sql.lower()
    result["accepted"] = all(result["checks"].values())
    return result


def run_regressions(data_dir: Path, agent_dir: Path) -> tuple[dict[str, Any], list[str], str]:
    from tools.f2_end_to_end_mvp_probe import CASES, run_case, start_server, stop_server

    process = None
    logs: list[str] = []
    key = ""
    try:
        process, logs, _, key = start_server(data_dir, agent_dir, False)
        f2_cases = [run_case(case, agent_dir, True) for case in CASES]
        b2_case = run_case({"id": "B02_TARGET", "query": BATCH02_QUESTION, "tables": BATCH02_TABLES, "limit": 50}, agent_dir, True)
        b3_case = run_case({"id": "B03_TARGET", "query": BATCH03_QUESTION, "tables": BATCH03_TABLES, "limit": 50}, agent_dir, True)
        b4_case = run_case({"id": "B04_TARGET", "query": EXPECTED_QUESTION, "tables": EXPECTED_TABLES, "limit": 50}, agent_dir, True)
        f2 = previous.common.regression_summary(f2_cases)
        b2 = validate_target(b2_case, question=BATCH02_QUESTION, tables=BATCH02_TABLES, columns=BATCH02_COLUMNS, expected_rows=50)
        b3 = validate_target(b3_case, question=BATCH03_QUESTION, tables=BATCH03_TABLES, columns=BATCH03_COLUMNS, expected_rows=7)
        b4 = validate_target(b4_case, question=EXPECTED_QUESTION, tables=EXPECTED_TABLES, columns=EXPECTED_COLUMNS, expected_rows=15)
        pass_count = f2["question_pass_count"] + sum(int(item["accepted"]) for item in (b2, b3, b4))
        return ({
            "question_count": 9, "question_pass_count": pass_count,
            "accepted": previous.common.regression_passed(f2) and b2["accepted"] and b3["accepted"] and b4["accepted"],
            "f2_summary": f2, "f2_cases": f2_cases,
            "batch02_case": b2_case, "batch02_validation": b2,
            "batch03_case": b3_case, "batch03_validation": b3,
            "batch04_case": b4_case, "batch04_validation": b4,
        }, logs, key)
    finally:
        stop_server(process)


def worker_paths(root: Path, prefix: str) -> dict[str, Path]:
    evidence = root / "evidence"
    return {name: evidence / f"{prefix}-{name}" for name in ("preflight.json", "write-result.json", "retrieval-injection.json", "regression.json", "worker-summary.json", "server-log.txt")}


def run_isolated_worker(root: Path, data: Path, prefix: str) -> int:
    paths = worker_paths(root, prefix)
    agent_data = root / ("agent_data" if prefix == "attempt1" else f"agent_data-{prefix}")
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update({"VANNA_DATA_DIR": str(data), "AGENT_DATA_DIR": str(agent_data), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    if "agent_config" in sys.modules:
        raise RuntimeError("EARLY_AGENT_CONFIG_IMPORT")
    configure_existing_helpers()
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256 or "", sql_guard=SQLGuard())
    memory = None
    try:
        memory, adapter = previous.common.open_memory(data, root)
        preflight, write_result = previous.common.write_and_verify(memory, adapter, plan, 190)
        write_json(paths["preflight.json"], preflight)
        write_json(paths["write-result.json"], write_result)
        write_json(paths["retrieval-injection.json"], write_result["retrieval"])
        close_memory(memory)
        memory = None
        gc.collect()
        regression, logs, key = run_regressions(data, agent_data)
        write_json(paths["regression.json"], sanitized(regression))
        from tools.f2_end_to_end_mvp_probe import redact
        paths["server-log.txt"].write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = regression["accepted"] and sqlite_record_count(data) == 191
        summary = {"accepted": accepted, "initial_count": 190, "created_count": write_result["created_count"], "final_count": write_result["final_count"], "preflight": preflight, "write": write_result, "regression": regression}
        write_json(paths["worker-summary.json"], sanitized(summary))
        return 0 if accepted else 2
    finally:
        close_memory(memory)


def copy_attempt_to_canonical(root: Path, prefix: str) -> None:
    evidence = root / "evidence"
    mapping = {
        "preflight.json": "isolated-preflight.json", "write-result.json": "isolated-write-result.json",
        "retrieval-injection.json": "isolated-retrieval-injection.json", "regression.json": "isolated-regression.json",
        "worker-summary.json": "isolated-worker-summary.json", "server-log.txt": "server-log.txt",
    }
    for source, target in mapping.items():
        shutil.copyfile(evidence / f"{prefix}-{source}", evidence / target)


def retryable_failure(summary: dict[str, Any]) -> tuple[bool, str]:
    regression = summary.get("regression", {})
    if regression.get("question_count") != 9 or regression.get("question_pass_count") != 8:
        return False, "NOT_SINGLE_QUESTION_FAILURE"
    failed_f2 = [item for item in regression.get("f2_cases", []) if not item.get("passed")]
    targets = [regression.get(name, {}) for name in ("batch02_validation", "batch03_validation", "batch04_validation")]
    failed_targets = [item for item in targets if not item.get("accepted")]
    if len(failed_f2) == 1 and not failed_targets and failed_f2[0].get("failure_stage") in {"sql_present", "http_200", "no_sse_error"}:
        return True, f"LLM_RANDOM_FAILURE_{failed_f2[0].get('id')}_{failed_f2[0].get('failure_stage')}"
    return False, "FAILURE_NOT_ELIGIBLE_FOR_RETRY"


def run_worker(root: Path, data: Path, prefix: str) -> tuple[int, dict[str, Any], str]:
    process = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--isolated-worker", str(root), "--isolated-data", str(data), "--evidence-prefix", prefix],
        cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    path = worker_paths(root, prefix)["worker-summary.json"]
    summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"accepted": False}
    return process.returncode, summary, process.stdout[-4000:]


def main() -> int:
    args = parse_args()
    configure_existing_helpers()
    if args.isolated_worker:
        if not args.isolated_data or not args.evidence_prefix:
            raise RuntimeError("ISOLATED_ARGUMENTS_MISSING")
        return run_isolated_worker(args.isolated_worker.resolve(), args.isolated_data.resolve(), args.evidence_prefix)
    if get_user_environment("VANNA_DATA_DIR") != (True, str(RUNTIME_SOURCE)):
        raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    runtime_before = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 190 or runtime_before["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_INITIAL_STATE_MISMATCH")
    batch = load_batch()
    candidate_source = validate_candidate_source(batch)
    metadata = validate_metadata()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError("BATCH_INVALID")
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256, sql_guard=SQLGuard())
    if not plan.executable or plan.create_count != 1 or plan.resume_same_batch_count or plan.conflict_count:
        raise RuntimeError("PLAN_INVALID")
    database = database_validation(batch["samples"][0]["args"]["sql"])

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = BACKUP_PARENT / f"f5-level2-batch04-{timestamp}"
    evidence = root / "evidence"
    backup_data = BACKUP_PARENT / f"f5-level2-batch04-prewrite-{timestamp}" / "runtime-vanna_data"
    evidence.mkdir(parents=True)
    for name, value in (("batch.json", batch), ("batch-validation.json", validation.to_dict()), ("candidate-source-validation.json", candidate_source), ("metadata-validation.json", metadata), ("database-validation.json", database), ("write-plan.json", plan.to_dict())):
        write_json(evidence / name, value)
    backup_data.parent.mkdir(parents=True)
    backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    backup_payload = {"runtime_before": runtime_before, "backup": backup.destination.to_dict(), "record_count": sqlite_record_count(backup_data), "verified": backup.destination.content_sha256 == EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup_data) == 190}
    write_json(evidence / "formal-backup.json", backup_payload)
    if not backup_payload["verified"]:
        raise RuntimeError("FORMAL_BACKUP_FAILED")

    first_data = root / "isolated" / "vanna_data"
    first_data.parent.mkdir(parents=True)
    create_verified_copy(backup_data, first_data, PROJECT_ROOT)
    code, first, output = run_worker(root, first_data, "attempt1")
    attempts = [{"attempt": 1, "accepted": bool(first.get("accepted")), "worker_exit_code": code}]
    retry_executed = False
    retry_reason = "NONE"
    accepted_prefix = "attempt1" if code == 0 and first.get("accepted") else None
    if accepted_prefix is None:
        can_retry, retry_reason = retryable_failure(first)
        if can_retry:
            retry_executed = True
            shutil.copyfile(worker_paths(root, "attempt1")["worker-summary.json"], evidence / "first-attempt-summary.json")
            shutil.copyfile(worker_paths(root, "attempt1")["regression.json"], evidence / "first-attempt-regression.json")
            shutil.copyfile(worker_paths(root, "attempt1")["server-log.txt"], evidence / "first-attempt-server-log.txt")
            retry_data = root / "isolated-retry" / "vanna_data"
            retry_data.parent.mkdir(parents=True)
            create_verified_copy(backup_data, retry_data, PROJECT_ROOT)
            code2, second, output2 = run_worker(root, retry_data, "attempt2")
            attempts.append({"attempt": 2, "accepted": bool(second.get("accepted")), "worker_exit_code": code2})
            if code2 == 0 and second.get("accepted"):
                accepted_prefix = "attempt2"
            else:
                output = output2
    write_json(evidence / "isolated-attempts.json", {"attempt_count": len(attempts), "first_attempt_result": "PASS" if first.get("accepted") else "FAIL", "retry_executed": retry_executed, "retry_reason": retry_reason, "attempts": attempts})
    if accepted_prefix is None:
        summary = {"f5_batch04_accepted": False, "failure_stage": "isolated", "attempts": attempts, "worker_output": output, "formal_opened": False, "formal_write_executed": False, "recovery_executed": False}
        write_json(evidence / "f5-batch04-summary.json", sanitized(summary))
        (evidence / "rollback.txt").write_text("隔离验收失败，正式库未打开、未写入，无需恢复。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2
    copy_attempt_to_canonical(root, accepted_prefix)
    isolated = json.loads((evidence / "isolated-worker-summary.json").read_text(encoding="utf-8"))

    current = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 190 or current["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_CHANGED_BEFORE_WRITE")
    recalculated = validate_training_batch(load_batch(), sql_guard=SQLGuard())
    recalculated_plan = build_memory_write_plan(load_batch(), approved_batch_content_sha256=recalculated.batch_content_sha256 or "", sql_guard=SQLGuard())
    if (recalculated.batch_content_sha256, recalculated_plan.write_plan_sha256, recalculated_plan.items[0].record_id) != (validation.batch_content_sha256, plan.write_plan_sha256, plan.items[0].record_id):
        raise RuntimeError("FORMAL_IDENTITY_RECALCULATION_MISMATCH")

    os.environ.update({"VANNA_DATA_DIR": str(RUNTIME_SOURCE), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    memory = None
    formal_write_started = False
    try:
        memory, adapter = previous.common.open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent)
        formal_write_started = True
        preflight, write_result = previous.common.write_and_verify(memory, adapter, plan, 190)
        write_json(evidence / "formal-preflight.json", preflight)
        write_json(evidence / "formal-write-result.json", write_result)
        close_memory(memory)
        memory = None
        gc.collect()
        formal_agent = root / "formal-agent_data"
        formal_agent.mkdir()
        regression, logs, key = run_regressions(RUNTIME_SOURCE, formal_agent)
        write_json(evidence / "formal-regression.json", sanitized(regression))
        from tools.f2_end_to_end_mvp_probe import redact
        prior_log = (evidence / "server-log.txt").read_text(encoding="utf-8")
        (evidence / "server-log.txt").write_text(prior_log + "\n--- FORMAL ---\n" + redact("\n".join(logs), [key]), encoding="utf-8")
        after_smoke = sqlite_record_count(RUNTIME_SOURCE)
        if not regression["accepted"] or after_smoke != 191:
            raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {
            "f5_batch04_accepted": True, "root": str(root), "backup_path": str(backup_data),
            "batch_content_sha256": validation.batch_content_sha256, "write_plan_sha256": plan.write_plan_sha256,
            "record_id": plan.items[0].record_id, "candidate_source": candidate_source, "metadata": metadata,
            "database": database, "isolated_attempts": {"attempt_count": len(attempts), "first_attempt_result": "PASS" if first.get("accepted") else "FAIL", "retry_executed": retry_executed, "retry_reason": retry_reason},
            "isolated": isolated, "formal": write_result, "formal_regression": regression,
            "formal_record_count_after_smoke": after_smoke, "recovery_executed": False,
            "memory_delete_executed": False, "old_uuid_migration_executed": False, "ddl_dml_executed": False,
        }
        write_json(evidence / "f5-batch04-summary.json", sanitized(summary))
        (evidence / "rollback.txt").write_text(f"失败恢复源：{backup_data}\n不得使用单条Memory删除。\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items() if key not in {"isolated", "formal_regression"}}, ensure_ascii=False))
        return 0
    except Exception:
        close_memory(memory)
        memory = None
        gc.collect()
        if formal_write_started:
            previous.common.restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

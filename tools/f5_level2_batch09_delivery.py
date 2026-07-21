from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.f5_level2_batch08_delivery as previous
from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import close_memory, get_user_environment, manifest, sqlite_record_count, write_json
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy

BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch09.json"
SOURCE_EVIDENCE = Path(r"E:\3\_training_backups\f5-batch09-fresh-candidate-discovery-20260716-160352\evidence")
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "9fe682c8a81f1e95ebbf4b1e994064c2ab3b4ba2d217135bd677c1e4359397a8"
EXPECTED_CANDIDATE_ID = "D9_L2_RS_ENTERPRISE_INFO_LSG_001"
EXPECTED_CANDIDATE_MODE = "STANDARD"
EXPECTED_SAMPLE_ID = "F5_L2_B09_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["rs_enterprise_info_lsg"]
EXPECTED_COLUMNS = ["name", "mine_name", "enterprise", "city", "county", "address"]
EXPECTED_QUESTION = "查询磷石膏库的名称、矿山名称、所属企业、所在市、所在县和地址，最多返回50条"
EXPECTED_SQL = """SELECT name,
       mine_name,
       enterprise,
       city,
       county,
       address
FROM rs_enterprise_info_lsg
LIMIT 50"""
COMMON = previous.COMMON
BATCH02_QUESTION, BATCH02_TABLES, BATCH02_COLUMNS = previous.BATCH02_QUESTION, previous.BATCH02_TABLES, previous.BATCH02_COLUMNS
BATCH03_QUESTION, BATCH03_TABLES, BATCH03_COLUMNS = previous.BATCH03_QUESTION, previous.BATCH03_TABLES, previous.BATCH03_COLUMNS
BATCH04_QUESTION, BATCH04_TABLES, BATCH04_COLUMNS = previous.BATCH04_QUESTION, previous.BATCH04_TABLES, previous.BATCH04_COLUMNS
BATCH05_QUESTION, BATCH05_TABLES, BATCH05_COLUMNS = previous.BATCH05_QUESTION, previous.BATCH05_TABLES, previous.BATCH05_COLUMNS
BATCH06_QUESTION, BATCH06_TABLES, BATCH06_COLUMNS = previous.BATCH06_QUESTION, previous.BATCH06_TABLES, previous.BATCH06_COLUMNS
BATCH07_QUESTION, BATCH07_TABLES, BATCH07_COLUMNS = previous.BATCH07_QUESTION, previous.BATCH07_TABLES, previous.BATCH07_COLUMNS
BATCH08_QUESTION, BATCH08_TABLES, BATCH08_COLUMNS = previous.EXPECTED_QUESTION, previous.EXPECTED_TABLES, previous.EXPECTED_COLUMNS


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
    previous.configure_existing_helpers()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", type=Path)
    parser.add_argument("--isolated-data", type=Path)
    parser.add_argument("--evidence-prefix")
    return parser.parse_args()


def normalized_sql(value: str) -> str:
    return " ".join(value.strip().rstrip(";").split())


def sanitized(value: Any) -> Any:
    return previous.sanitized(value)


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    samples = batch.get("samples", [])
    sample = samples[0] if len(samples) == 1 else {}
    checks = (
        batch.get("schema_version") == "1.0",
        batch.get("training_batch_id") == "level2-f5-batch09-20260716-01",
        batch.get("training_level") == EXPECTED_TRAINING_LEVEL,
        batch.get("status") == "frozen",
        batch.get("source") == "F5 Batch 09-D2标准候选发现真实验证结果",
        batch.get("expected_new_memory_count") == 1,
        len(samples) == 1,
        sample.get("sample_id") == EXPECTED_SAMPLE_ID,
        sample.get("question") == EXPECTED_QUESTION,
        normalized_sql(sample.get("args", {}).get("sql", "")) == normalized_sql(EXPECTED_SQL),
        sample.get("expected_tables") == EXPECTED_TABLES,
        sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
        sample.get("train_decision") == "approved",
        sample.get("expected_behavior") == "返回最多50条磷石膏库名称、矿山名称、所属企业、所在市、所在县和可选地址",
    )
    if not all(checks):
        raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def validate_candidate_source(batch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = json.loads((SOURCE_EVIDENCE / "batch09-discovery-summary.json").read_text(encoding="utf-8"))
    inventory = json.loads((SOURCE_EVIDENCE / "current-tool-memory-inventory.json").read_text(encoding="utf-8"))
    mapping = json.loads((SOURCE_EVIDENCE / "table-mapping-analysis.json").read_text(encoding="utf-8"))["candidates"][0]
    retrieval = json.loads((SOURCE_EVIDENCE / "retrieval-collision-analysis.json").read_text(encoding="utf-8"))["candidates"][0]
    comparison = json.loads((SOURCE_EVIDENCE / "candidate-comparison.json").read_text(encoding="utf-8"))
    candidate = summary.get("recommended") or {}
    frozen = batch["samples"][0]
    records = inventory.get("records", [])
    same_table = [item for item in records if EXPECTED_TABLES[0] in set(item.get("expected_tables", []) + item.get("sql_guard_used_tables", []))]
    control_top = retrieval.get("top5", [None])[0]
    control = next((item for item in records if control_top and item.get("memory_id") == control_top.get("memory_id")), None)
    checks = {
        "candidate_id": summary.get("batch09_discovery_recommendation") == candidate.get("candidate_id") == EXPECTED_CANDIDATE_ID,
        "candidate_mode": candidate.get("candidate_mode") == comparison.get("recommendation_mode") == EXPECTED_CANDIDATE_MODE,
        "question": candidate.get("question") == frozen["question"] == EXPECTED_QUESTION,
        "sql": normalized_sql(candidate.get("sql", "")) == normalized_sql(frozen["args"]["sql"]) == normalized_sql(EXPECTED_SQL),
        "expected_tables": candidate.get("expected_tables") == frozen["expected_tables"] == EXPECTED_TABLES,
        "training_level": candidate.get("training_level") == frozen["training_level"] == EXPECTED_TRAINING_LEVEL,
        "metadata_valid": candidate.get("metadata_valid") is True,
        "sql_guard_pass": candidate.get("sql_guard_pass") is True,
        "database_success": candidate.get("database_success") is True,
        "result_row_count": candidate.get("result_row_count") == 17,
        "duplicate_found_false": candidate.get("duplicate_found") is False,
        "mapping_false": candidate.get("table_mapping_or_copy_found") is False and mapping.get("table_mapping_or_copy_found") is False,
        "retrieval_low": candidate.get("retrieval_collision_risk") == retrieval.get("retrieval_collision_risk") == "LOW",
        "semantic_low": candidate.get("semantic_risk") == "LOW",
        "eligible": candidate.get("eligible") is True,
        "legacy_expansion": summary.get("legacy_coverage_expansion") in (True, "YES"),
        "inventory_counts": (inventory.get("legacy_tool_memory_count"), inventory.get("controlled_tool_memory_count"), inventory.get("total_tool_memory_count")) == (64, 8, 72),
        "table_uncovered": not same_table,
        "mapping_reference": "rs_enterprise_info_wade" in mapping.get("mapping_reference_tables", []),
        "mapping_counts": mapping.get("set_summary", {}).get("reference_row_count") == 467 and mapping.get("set_summary", {}).get("name_overlap_count") == 0,
        "control_from_source": bool(control_top and control and control_top.get("rank") == 1),
    }
    result = {"candidate_id": EXPECTED_CANDIDATE_ID, "candidate_mode": EXPECTED_CANDIDATE_MODE, "checks": checks, "valid": all(checks.values())}
    if not result["valid"]:
        raise RuntimeError("CANDIDATE_SOURCE_VALIDATION_FAILED")
    control_payload = {
        "memory_id": control["memory_id"], "sample_id": control["sample_id"], "question": control["question"],
        "sql": control["sql"], "used_tables": control["sql_guard_used_tables"], "prewrite_rank": control_top["rank"],
    }
    return result, {"mapping": mapping, "control": control_payload}


def validate_metadata() -> dict[str, Any]:
    rows = json.loads((PROJECT_ROOT / "agent_data" / "column_metadata_index.json").read_text(encoding="utf-8"))
    table_rows = [item for item in rows if item.get("table") == EXPECTED_TABLES[0]]
    fields = {item["column"]: item for item in table_rows}
    expected_comments = {"name": "磷石膏库名称", "mine_name": "矿山名称", "enterprise": "所属企业", "city": "市", "county": "县", "address": "污染源地址"}
    checks = {
        "table_object": bool(table_rows) and "磷石膏" in str(table_rows[0].get("table_comment", "")),
        **{f"{name}_comment": fields.get(name, {}).get("comment") == comment for name, comment in expected_comments.items()},
        "display_types": all(str(fields.get(name, {}).get("type", "")).startswith("character varying") for name in EXPECTED_COLUMNS),
        "no_sensitive_information": True, "join_not_required": True,
        "forbidden_fields_absent": not any(token in normalized_sql(EXPECTED_SQL).lower().split() for token in ("id", "geom", "create_by", "create_time", "update_by", "update_time", "status", "del_flag")),
    }
    result = {"table": EXPECTED_TABLES[0], "field_details": {name: fields.get(name) for name in EXPECTED_COLUMNS}, "checks": checks,
              "identifier_strategy": "NATURAL_NAME_IDENTIFIER", "identifier_reason": "不存在已验证的稳定业务编码；name字段完整且在当前17条记录中唯一。",
              "address_classification": "OPTIONAL_DISPLAY_FIELD", "valid": all(checks.values())}
    if not result["valid"]:
        raise RuntimeError("METADATA_VALIDATION_FAILED")
    return result


def database_validation(sql: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import psycopg2
    guard = SQLGuard().validate(sql=sql, query=EXPECTED_QUESTION, deterministic_candidate_tables=EXPECTED_TABLES)
    if not guard.passed or guard.used_tables != EXPECTED_TABLES:
        raise RuntimeError("DATABASE_SQL_GUARD_FAILED")
    if not os.getenv("DB_USER", "").strip() or not os.getenv("DB_PASSWORD", "").strip():
        raise RuntimeError("DB_CREDENTIALS_MISSING")
    kwargs = {"host": os.getenv("DB_HOST", "localhost"), "port": int(os.getenv("DB_PORT", "5433")), "database": os.getenv("DB_NAME", "gt_monitor"),
              "user": os.environ["DB_USER"], "password": os.environ["DB_PASSWORD"], "connect_timeout": 10,
              "application_name": "vanna-f5-batch09-validation", "options": "-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000"}
    with psycopg2.connect(**kwargs) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql); rows = cursor.fetchall(); columns = [item.name for item in cursor.description or ()]
            cursor.execute("SELECT COUNT(*) FROM rs_enterprise_info_wade"); reference_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM (SELECT name FROM rs_enterprise_info_lsg WHERE name IS NOT NULL INTERSECT SELECT name FROM rs_enterprise_info_wade WHERE name IS NOT NULL) x")
            intersection_count = int(cursor.fetchone()[0])
    non_null = {columns[index]: sum(row[index] is not None for row in rows) for index in range(len(columns))}
    name_groups: dict[Any, set[tuple[Any, ...]]] = {}
    for row in rows: name_groups.setdefault(row[0], set()).add(tuple(row[1:]))
    result = {"database_query_success": True, "row_count": len(rows), "columns": columns, "non_null_counts": non_null,
              "name_unique_count": len({row[0] for row in rows}), "business_tuple_unique_count": len(set(rows)),
              "duplicate_business_tuple_group_count": sum(count > 1 for count in Counter(rows).values()),
              "name_business_conflict_group_count": sum(len(values) > 1 for values in name_groups.values()), "guard": guard.to_dict(),
              "default_transaction_read_only": True, "statement_timeout_ms": 30000, "lock_timeout_ms": 5000,
              "ddl_dml_executed": False, "complete_rows_saved": False}
    mapping = {"reference_table": "rs_enterprise_info_wade", "reference_row_count": reference_count, "name_set_intersection_count": intersection_count,
               "table_mapping_or_copy_found": False, "complete_name_sets_saved": False}
    expected_non_null = {"name": 17, "mine_name": 17, "enterprise": 17, "city": 17, "county": 17, "address": 16}
    if not all((len(rows) == 17, columns == EXPECTED_COLUMNS, non_null == expected_non_null, result["name_unique_count"] == 17,
                result["business_tuple_unique_count"] == 17, result["duplicate_business_tuple_group_count"] == 0,
                result["name_business_conflict_group_count"] == 0, reference_count == 467, intersection_count == 0)):
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result, mapping


def query_retrieval(memory: Any, question: str, target_sample_id: str) -> dict[str, Any]:
    return previous._query_retrieval(memory, question, target_sample_id)


def bidirectional_retrieval(memory: Any, control: dict[str, Any]) -> dict[str, Any]:
    b09 = query_retrieval(memory, EXPECTED_QUESTION, EXPECTED_SAMPLE_ID)
    control_query = query_retrieval(memory, control["question"], control["sample_id"])
    rank = lambda result, sample_id: next((item["rank"] for item in result["top5"] if item["sample_id"] == sample_id), None)
    b09_control_rank = rank(b09, control["sample_id"]); control_b09_rank = rank(control_query, EXPECTED_SAMPLE_ID)
    checks = {"b09_rank_1": b09["target_rank"] == 1, "b09_injected": b09["target_injected"], "b09_not_filtered": not b09["target_filtered"],
              "b09_sql_exact": normalized_sql(b09["target_injected_sql"]) == normalized_sql(EXPECTED_SQL),
              "b09_control_lower": b09_control_rank is None or b09_control_rank > 1,
              "control_rank_1": control_query["target_rank"] == 1, "control_injected": control_query["target_injected"],
              "control_not_filtered": not control_query["target_filtered"],
              "control_sql_exact": normalized_sql(control_query["target_injected_sql"]) == normalized_sql(control["sql"]),
              "control_b09_lower": control_b09_rank is None or control_b09_rank > control_query["target_rank"]}
    result = {"control": control, "b09_query": b09, "control_query": control_query, "b09_query_control_memory_rank": b09_control_rank,
              "control_query_b09_memory_rank": control_b09_rank, "checks": checks, "accepted": all(checks.values())}
    if not result["accepted"]:
        raise RuntimeError("BIDIRECTIONAL_RETRIEVAL_FAILED")
    return result


def write_and_verify(memory: Any, adapter: Any, plan: Any, initial_count: int, control: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preflight, write_result = COMMON.write_and_verify(memory, adapter, plan, initial_count)
    return preflight, write_result, bidirectional_retrieval(memory, control)


def validate_target(case_result: dict[str, Any], *, question: str, tables: list[str], columns: list[str], expected_rows: int) -> dict[str, Any]:
    return previous.validate_target(case_result, question=question, tables=tables, columns=columns, expected_rows=expected_rows)


def run_regressions(data_dir: Path, agent_dir: Path) -> tuple[dict[str, Any], list[str], str]:
    from tools.f2_end_to_end_mvp_probe import CASES, run_case, start_server, stop_server
    process = None; logs: list[str] = []; key = ""
    try:
        process, logs, _, key = start_server(data_dir, agent_dir, False)
        f2_cases = [run_case(case, agent_dir, True) for case in CASES]
        specs = (("B02_TARGET", BATCH02_QUESTION, BATCH02_TABLES, BATCH02_COLUMNS, 50),
                 ("B03_TARGET", BATCH03_QUESTION, BATCH03_TABLES, BATCH03_COLUMNS, 7),
                 ("B04_TARGET", BATCH04_QUESTION, BATCH04_TABLES, BATCH04_COLUMNS, 15),
                 ("B05_TARGET", BATCH05_QUESTION, BATCH05_TABLES, BATCH05_COLUMNS, 12),
                 ("B06_TARGET", BATCH06_QUESTION, BATCH06_TABLES, BATCH06_COLUMNS, 50),
                 ("B07_TARGET", BATCH07_QUESTION, BATCH07_TABLES, BATCH07_COLUMNS, 33),
                 ("B08_TARGET", BATCH08_QUESTION, BATCH08_TABLES, BATCH08_COLUMNS, 6),
                 ("B09_TARGET", EXPECTED_QUESTION, EXPECTED_TABLES, EXPECTED_COLUMNS, 17))
        cases: dict[str, Any] = {}; validations: dict[str, Any] = {}
        for case_id, question, tables, columns, expected_rows in specs:
            case = run_case({"id": case_id, "query": question, "tables": tables, "limit": 50}, agent_dir, True)
            validation = validate_target(case, question=question, tables=tables, columns=columns, expected_rows=expected_rows)
            if case_id == "B09_TARGET":
                sql = str(validation.get("sql", ""))
                validation["checks"].update({"select_star_absent": not bool(re.search(r"select\s+\*", sql, re.I)),
                    "join_absent": not bool(re.search(r"\bjoin\b", sql, re.I)), "distinct_absent": not bool(re.search(r"\bdistinct\b", sql, re.I)),
                    "null_filter_absent": not bool(re.search(r"\bis\s+(?:not\s+)?null\b", sql, re.I)),
                    "address_null_filter_absent": not bool(re.search(r"address\s+is\s+not\s+null", sql, re.I)),
                    "limit_at_most_50": not re.search(r"\blimit\s+(\d+)", sql, re.I) or int(re.search(r"\blimit\s+(\d+)", sql, re.I).group(1)) <= 50})
                validation["accepted"] = all(validation["checks"].values())
            cases[case_id], validations[case_id] = case, validation
        f2 = COMMON.regression_summary(f2_cases)
        pass_count = f2["question_pass_count"] + sum(int(item["accepted"]) for item in validations.values())
        return ({"question_count": 14, "question_pass_count": pass_count,
                 "accepted": COMMON.regression_passed(f2) and all(item["accepted"] for item in validations.values()),
                 "f2_summary": f2, "f2_cases": f2_cases,
                 **{f"{case_id.lower()}_case": cases[case_id] for case_id in cases},
                 **{f"{case_id.lower()}_validation": validations[case_id] for case_id in validations}}, logs, key)
    finally:
        stop_server(process)


def worker_paths(root: Path, prefix: str) -> dict[str, Path]:
    evidence = root / "evidence"
    return {name: evidence / f"{prefix}-{name}" for name in ("preflight.json", "write-result.json", "bidirectional-retrieval.json", "regression.json", "worker-summary.json", "server-log.txt")}


def run_isolated_worker(root: Path, data: Path, prefix: str) -> int:
    paths = worker_paths(root, prefix)
    agent_data = root / ("agent_data" if prefix == "attempt1" else f"agent_data-{prefix}")
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update({"VANNA_DATA_DIR": str(data), "AGENT_DATA_DIR": str(agent_data), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    if "backend.memory" in sys.modules: raise RuntimeError("EARLY_BACKEND_MEMORY_IMPORT")
    configure_existing_helpers(); batch = load_batch(); source, source_context = validate_candidate_source(batch)
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256 or "", sql_guard=SQLGuard())
    memory = None
    try:
        memory, adapter = COMMON.open_memory(data, root)
        preflight, write_result, retrieval = write_and_verify(memory, adapter, plan, 195, source_context["control"])
        write_json(paths["preflight.json"], preflight); write_json(paths["write-result.json"], write_result); write_json(paths["bidirectional-retrieval.json"], retrieval)
        close_memory(memory); memory = None; gc.collect()
        regression, logs, key = run_regressions(data, agent_data)
        write_json(paths["regression.json"], sanitized(regression))
        from tools.f2_end_to_end_mvp_probe import redact
        paths["server-log.txt"].write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = regression["accepted"] and retrieval["accepted"] and sqlite_record_count(data) == 196
        summary = {"accepted": accepted, "initial_count": 195, "created_count": write_result["created_count"], "final_count": write_result["final_count"],
                   "preflight": preflight, "write": write_result, "bidirectional_retrieval": retrieval, "regression": regression, "candidate_source": source}
        write_json(paths["worker-summary.json"], sanitized(summary)); return 0 if accepted else 2
    finally:
        close_memory(memory)


def copy_attempt_to_canonical(root: Path, prefix: str) -> None:
    evidence = root / "evidence"
    mapping = {"preflight.json": "isolated-preflight.json", "write-result.json": "isolated-write-result.json",
               "bidirectional-retrieval.json": "isolated-bidirectional-retrieval.json", "regression.json": "isolated-regression.json",
               "worker-summary.json": "isolated-worker-summary.json", "server-log.txt": "server-log.txt"}
    for source, target in mapping.items(): shutil.copyfile(evidence / f"{prefix}-{source}", evidence / target)


def retryable_failure(summary: dict[str, Any]) -> tuple[bool, str]:
    regression = summary.get("regression", {})
    if regression.get("question_count") != 14 or regression.get("question_pass_count") != 13: return False, "NOT_SINGLE_QUESTION_FAILURE"
    failed_f2 = [item for item in regression.get("f2_cases", []) if not item.get("passed")]
    failed_targets = [regression.get(f"b{number:02d}_target_validation", {}) for number in range(2, 10) if not regression.get(f"b{number:02d}_target_validation", {}).get("accepted")]
    if len(failed_f2) == 1 and not failed_targets and failed_f2[0].get("failure_stage") in {"sql_present", "http_200", "no_sse_error"}:
        return True, f"LLM_RANDOM_FAILURE_{failed_f2[0].get('id')}_{failed_f2[0].get('failure_stage')}"
    if not failed_f2 and len(failed_targets) == 1: return True, "LLM_RANDOM_FAILURE_SINGLE_TARGET"
    return False, "FAILURE_NOT_ELIGIBLE_FOR_RETRY"


def run_worker(root: Path, data: Path, prefix: str) -> tuple[int, dict[str, Any], str]:
    process = subprocess.run([str(PYTHON_EXE), str(Path(__file__).resolve()), "--isolated-worker", str(root), "--isolated-data", str(data), "--evidence-prefix", prefix],
                             cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    path = worker_paths(root, prefix)["worker-summary.json"]
    return process.returncode, json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"accepted": False}, process.stdout[-4000:]


def main() -> int:
    args = parse_args(); configure_existing_helpers()
    if args.isolated_worker:
        if not args.isolated_data or not args.evidence_prefix: raise RuntimeError("ISOLATED_ARGUMENTS_MISSING")
        return run_isolated_worker(args.isolated_worker.resolve(), args.isolated_data.resolve(), args.evidence_prefix)
    if get_user_environment("VANNA_DATA_DIR") != (True, str(RUNTIME_SOURCE)): raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    runtime_before = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 195 or runtime_before["content_sha256"] != EXPECTED_FORMAL_SHA256: raise RuntimeError("FORMAL_INITIAL_STATE_MISMATCH")
    batch = load_batch(); candidate_source, source_context = validate_candidate_source(batch); metadata = validate_metadata()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256: raise RuntimeError("BATCH_INVALID")
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256, sql_guard=SQLGuard())
    if not plan.executable or plan.create_count != 1 or plan.resume_same_batch_count or plan.conflict_count: raise RuntimeError("PLAN_INVALID")
    database, mapping = database_validation(batch["samples"][0]["args"]["sql"])
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S"); root = BACKUP_PARENT / f"f5-level2-batch09-{timestamp}"; evidence = root / "evidence"
    backup_data = BACKUP_PARENT / f"f5-level2-batch09-prewrite-{timestamp}" / "runtime-vanna_data"; evidence.mkdir(parents=True)
    for name, value in (("batch.json", batch), ("batch-validation.json", validation.to_dict()), ("candidate-source-validation.json", candidate_source),
                        ("metadata-validation.json", metadata), ("database-validation.json", database), ("table-mapping-validation.json", mapping), ("write-plan.json", plan.to_dict())):
        write_json(evidence / name, value)
    backup_data.parent.mkdir(parents=True); backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    backup_payload = {"runtime_before": runtime_before, "backup": backup.destination.to_dict(), "record_count": sqlite_record_count(backup_data),
                      "verified": backup.destination.content_sha256 == EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup_data) == 195}
    write_json(evidence / "formal-backup.json", backup_payload)
    if not backup_payload["verified"]: raise RuntimeError("FORMAL_BACKUP_FAILED")
    first_data = root / "isolated" / "vanna_data"; first_data.parent.mkdir(parents=True); create_verified_copy(backup_data, first_data, PROJECT_ROOT)
    code, first, output = run_worker(root, first_data, "attempt1")
    attempts = [{"attempt": 1, "accepted": bool(first.get("accepted")), "worker_exit_code": code}]
    retry_executed = False; retry_reason = "NONE"; accepted_prefix = "attempt1" if code == 0 and first.get("accepted") else None
    if accepted_prefix is None:
        can_retry, retry_reason = retryable_failure(first)
        if can_retry:
            retry_executed = True
            for source, target in (("worker-summary.json", "first-attempt-summary.json"), ("regression.json", "first-attempt-regression.json"), ("server-log.txt", "first-attempt-server-log.txt")):
                shutil.copyfile(worker_paths(root, "attempt1")[source], evidence / target)
            retry_data = root / "isolated-retry" / "vanna_data"; retry_data.parent.mkdir(parents=True); create_verified_copy(backup_data, retry_data, PROJECT_ROOT)
            code2, second, output2 = run_worker(root, retry_data, "attempt2"); attempts.append({"attempt": 2, "accepted": bool(second.get("accepted")), "worker_exit_code": code2})
            if code2 == 0 and second.get("accepted"): accepted_prefix = "attempt2"
            else: output = output2
    attempts_payload = {"attempt_count": len(attempts), "first_attempt_result": "PASS" if first.get("accepted") else "FAIL", "retry_executed": retry_executed, "retry_reason": retry_reason, "attempts": attempts}
    write_json(evidence / "isolated-attempts.json", attempts_payload)
    if accepted_prefix is None:
        summary = {"f5_batch09_accepted": False, "failure_stage": "isolated", "attempts": attempts, "worker_output": output, "formal_opened": False, "formal_write_executed": False, "recovery_executed": False}
        write_json(evidence / "f5-batch09-summary.json", sanitized(summary)); (evidence / "rollback.txt").write_text("隔离验收失败，正式库未打开、未写入，无需恢复。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False)); return 2
    copy_attempt_to_canonical(root, accepted_prefix); isolated = json.loads((evidence / "isolated-worker-summary.json").read_text(encoding="utf-8"))
    current = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 195 or current["content_sha256"] != EXPECTED_FORMAL_SHA256: raise RuntimeError("FORMAL_CHANGED_BEFORE_WRITE")
    recalculated = validate_training_batch(load_batch(), sql_guard=SQLGuard()); recalculated_plan = build_memory_write_plan(load_batch(), approved_batch_content_sha256=recalculated.batch_content_sha256 or "", sql_guard=SQLGuard())
    if (recalculated.batch_content_sha256, recalculated_plan.write_plan_sha256, recalculated_plan.items[0].record_id) != (validation.batch_content_sha256, plan.write_plan_sha256, plan.items[0].record_id): raise RuntimeError("FORMAL_IDENTITY_RECALCULATION_MISMATCH")
    os.environ.update({"VANNA_DATA_DIR": str(RUNTIME_SOURCE), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    memory = None; formal_write_started = False
    try:
        memory, adapter = COMMON.open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent); formal_write_started = True
        preflight, write_result, retrieval = write_and_verify(memory, adapter, plan, 195, source_context["control"])
        write_json(evidence / "formal-preflight.json", preflight); write_json(evidence / "formal-write-result.json", write_result); write_json(evidence / "formal-bidirectional-retrieval.json", retrieval)
        close_memory(memory); memory = None; gc.collect(); formal_agent = root / "formal-agent_data"; formal_agent.mkdir()
        regression, logs, key = run_regressions(RUNTIME_SOURCE, formal_agent); write_json(evidence / "formal-regression.json", sanitized(regression))
        from tools.f2_end_to_end_mvp_probe import redact
        prior_log = (evidence / "server-log.txt").read_text(encoding="utf-8"); (evidence / "server-log.txt").write_text(prior_log + "\n--- FORMAL ---\n" + redact("\n".join(logs), [key]), encoding="utf-8")
        after_smoke = sqlite_record_count(RUNTIME_SOURCE)
        if not regression["accepted"] or not retrieval["accepted"] or after_smoke != 196: raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {"f5_batch09_accepted": True, "root": str(root), "backup_path": str(backup_data), "batch_content_sha256": validation.batch_content_sha256,
                   "write_plan_sha256": plan.write_plan_sha256, "record_id": plan.items[0].record_id, "candidate_source": candidate_source, "metadata": metadata,
                   "database": database, "mapping": mapping, "control": source_context["control"], "isolated_attempts": attempts_payload, "isolated": isolated,
                   "formal": write_result, "formal_bidirectional_retrieval": retrieval, "formal_regression": regression, "formal_record_count_after_smoke": after_smoke,
                   "recovery_executed": False, "memory_delete_executed": False, "old_uuid_migration_executed": False, "ddl_dml_executed": False}
        write_json(evidence / "f5-batch09-summary.json", sanitized(summary)); (evidence / "rollback.txt").write_text(f"失败恢复源：{backup_data}\n不得使用单条Memory删除。\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items() if key not in {"isolated", "formal_regression"}}, ensure_ascii=False)); return 0
    except Exception:
        close_memory(memory); memory = None; gc.collect()
        if formal_write_started: COMMON.restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

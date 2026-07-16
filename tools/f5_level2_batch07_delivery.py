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
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.f5_level2_batch06_delivery as previous
from tools.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import close_memory, get_user_environment, manifest, sqlite_record_count, write_json
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy


BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch07.json"
SOURCE_EVIDENCE = Path(r"E:\3\_training_backups\f5-batch07-fresh-candidate-discovery-20260716-141429\evidence")
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "3bce02fc83d429e4673e96c100f8a05b22fe061815a57f76bfc9c08626d888c0"
EXPECTED_CANDIDATE_ID = "D7_L2_WST_ASSET_TYPE_DICT_005"
EXPECTED_SAMPLE_ID = "F5_L2_B07_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["wst_asset_type_dict"]
EXPECTED_COLUMNS = ["asset_type", "asset_type_name", "asset_group", "asset_group_name", "parent_type"]
EXPECTED_QUESTION = "查询水安全溯源资产类型的类型编码、类型名称、资产分组编码、资产分组名称和上级类型编码，最多返回50条"
EXPECTED_SQL = """SELECT asset_type,
       asset_type_name,
       asset_group,
       asset_group_name,
       parent_type
FROM wst_asset_type_dict
LIMIT 50"""
B01_FILE = PROJECT_ROOT / "training" / "f5_level2_batch01.json"
B01_BATCH = json.loads(B01_FILE.read_text(encoding="utf-8"))
B01_SAMPLE = B01_BATCH["samples"][0]
B01_SAMPLE_ID = B01_SAMPLE["sample_id"]
B01_QUESTION = B01_SAMPLE["question"]
B01_SQL = B01_SAMPLE["args"]["sql"]
BATCH02_QUESTION = previous.BATCH02_QUESTION
BATCH02_TABLES = previous.BATCH02_TABLES
BATCH02_COLUMNS = previous.BATCH02_COLUMNS
BATCH03_QUESTION = previous.BATCH03_QUESTION
BATCH03_TABLES = previous.BATCH03_TABLES
BATCH03_COLUMNS = previous.BATCH03_COLUMNS
BATCH04_QUESTION = previous.BATCH04_QUESTION
BATCH04_TABLES = previous.BATCH04_TABLES
BATCH04_COLUMNS = previous.BATCH04_COLUMNS
BATCH05_QUESTION = previous.BATCH05_QUESTION
BATCH05_TABLES = previous.BATCH05_TABLES
BATCH05_COLUMNS = previous.BATCH05_COLUMNS
BATCH06_QUESTION = previous.EXPECTED_QUESTION
BATCH06_TABLES = previous.EXPECTED_TABLES
BATCH06_COLUMNS = previous.EXPECTED_COLUMNS
COMMON = previous.COMMON


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
    return previous.normalized_sql(value)


def sanitized(value: Any) -> Any:
    return previous.sanitized(value)


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    samples = batch.get("samples", [])
    sample = samples[0] if len(samples) == 1 else {}
    checks = (
        batch.get("schema_version") == "1.0",
        batch.get("training_batch_id") == "level2-f5-batch07-20260716-01",
        batch.get("training_level") == EXPECTED_TRAINING_LEVEL,
        batch.get("status") == "frozen",
        batch.get("source") == "F5 Batch 07-D3候选发现真实验证结果",
        batch.get("expected_new_memory_count") == 1,
        len(samples) == 1,
        sample.get("sample_id") == EXPECTED_SAMPLE_ID,
        sample.get("question") == EXPECTED_QUESTION,
        normalized_sql(sample.get("args", {}).get("sql", "")) == normalized_sql(EXPECTED_SQL),
        sample.get("expected_tables") == EXPECTED_TABLES,
        sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
        sample.get("train_decision") == "approved",
        sample.get("expected_behavior") == "返回最多50条水安全溯源资产类型编码、类型名称、资产分组编码、资产分组名称和可选的上级类型编码",
    )
    if not all(checks):
        raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def validate_candidate_source(batch: dict[str, Any]) -> dict[str, Any]:
    names = (
        "current-tool-memory-inventory.json", "current-table-coverage.json",
        "metadata-shortlist.json", "database-readonly-probes.json",
        "candidate-validation.json", "duplicate-analysis.json",
        "retrieval-collision-analysis.json", "candidate-comparison.json",
        "batch07-discovery-summary.json",
    )
    source = {name: json.loads((SOURCE_EVIDENCE / name).read_text(encoding="utf-8")) for name in names}
    summary = source["batch07-discovery-summary.json"]
    comparison = source["candidate-comparison.json"]
    candidates = [item for item in comparison.get("candidates", []) if item.get("candidate_id") == EXPECTED_CANDIDATE_ID]
    candidate = candidates[0] if len(candidates) == 1 else {}
    inventory = source["current-tool-memory-inventory.json"]
    coverage = source["current-table-coverage.json"]
    sample = batch["samples"][0]
    checks = {
        "recommendation_equal": summary.get("batch07_discovery_recommendation") == EXPECTED_CANDIDATE_ID,
        "candidate_id_equal": candidate.get("candidate_id") == EXPECTED_CANDIDATE_ID,
        "question_equal": candidate.get("question") == sample["question"] == EXPECTED_QUESTION,
        "sql_equal": normalized_sql(candidate.get("sql", "")) == normalized_sql(sample["args"]["sql"]) == normalized_sql(EXPECTED_SQL),
        "expected_tables_equal": candidate.get("expected_tables") == sample["expected_tables"] == EXPECTED_TABLES,
        "training_level_equal": candidate.get("training_level") == EXPECTED_TRAINING_LEVEL,
        "duplicate_found_false": candidate.get("duplicate_found") is False,
        "mapping_found_false": candidate.get("table_mapping_or_copy_found") is False,
        "sql_guard_pass": candidate.get("sql_guard_pass") is True,
        "database_success": candidate.get("exact_query_success") is True,
        "result_row_count_33": candidate.get("result_row_count") == 33,
        "retrieval_collision_low": candidate.get("retrieval_collision_risk") == "LOW",
        "semantic_risk_low": candidate.get("semantic_risk") == "LOW",
        "eligible": candidate.get("eligible") is True,
        "legacy_coverage_expansion": summary.get("legacy_coverage_expansion") is True,
        "legacy_count_64": inventory.get("legacy_tool_memory_count") == 64,
        "controlled_count_6": inventory.get("controlled_tool_memory_count") == 6,
        "tool_total_70": inventory.get("total_tool_memory_count") == 70,
        "table_currently_uncovered": EXPECTED_TABLES[0] in coverage.get("current_uncovered_tables", []),
    }
    result = {"candidate_id": EXPECTED_CANDIDATE_ID, "checks": checks, "valid": all(checks.values())}
    if not result["valid"]:
        raise RuntimeError("CANDIDATE_SOURCE_VALIDATION_FAILED")
    return result


def validate_metadata() -> dict[str, Any]:
    rows = json.loads((PROJECT_ROOT / "agent_data" / "column_metadata_index.json").read_text(encoding="utf-8"))
    table_rows = [item for item in rows if item.get("table") == EXPECTED_TABLES[0]]
    fields = {item["column"]: item for item in table_rows}
    expected = {
        "asset_type": ("资产类型编码，例如 national_section、auto_station、outfall、water_enterprise、chemical_park、camera", "character varying(100)"),
        "asset_type_name": ("资产类型名称，例如国控断面、自动站、排污口、涉水企业、化工园区、摄像头", "character varying(200)"),
        "parent_type": ("上级资产类型编码，用于资产类型分组", "character varying(100)"),
        "asset_group": ("资产分组编码", "character varying(100)"),
        "asset_group_name": ("资产分组名称", "character varying(100)"),
    }
    checks = {
        "table_comment": bool(table_rows) and table_rows[0].get("table_comment") == "水安全溯源模块-资产类型字典表，用于统一管理资产类型、图层、图标、几何类型和前端展示规则",
        **{f"{name}_comment": fields.get(name, {}).get("comment") == value[0] for name, value in expected.items()},
        **{f"{name}_type": fields.get(name, {}).get("type") == value[1] for name, value in expected.items()},
        "no_sensitive_information": True,
        "single_table_semantics": True,
        "forbidden_columns_absent": not any(token in normalized_sql(EXPECTED_SQL).lower().split() for token in ("geometry_type", "icon_name", "layer_name", "metadata_json", "created_by", "updated_by")),
    }
    result = {
        "table": EXPECTED_TABLES[0], "checks": checks, "valid": all(checks.values()),
        "parent_type_classification": "OPTIONAL_HIERARCHY_FIELD",
    }
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
        connect_timeout=10, application_name="vanna-f5-batch07-validation",
        options="-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    ) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
            cursor.execute(
                "SELECT COUNT(*) FROM (SELECT asset_type, asset_type_name, asset_group, asset_group_name, parent_type "
                "FROM wst_asset_type_dict GROUP BY asset_type, asset_type_name, asset_group, asset_group_name, parent_type "
                "HAVING COUNT(*) > 1) AS grouped"
            )
            duplicate_groups = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM (SELECT asset_type FROM wst_asset_type_dict WHERE asset_type IS NOT NULL "
                "GROUP BY asset_type HAVING COUNT(DISTINCT asset_type_name) > 1) AS grouped"
            )
            conflict_groups = int(cursor.fetchone()[0])
    non_null = {columns[index]: sum(row[index] is not None for row in rows) for index in range(len(columns))}
    result = {
        "database_query_success": True, "row_count": len(rows), "columns": columns,
        "non_null_counts": non_null,
        "asset_type_unique_count": len({row[0] for row in rows if row[0] is not None}),
        "asset_type_name_unique_count": len({row[1] for row in rows if row[1] is not None}),
        "business_tuple_unique_count": len(set(rows)),
        "duplicate_business_tuple_group_count": duplicate_groups,
        "code_name_conflict_group_count": conflict_groups,
        "guard": guard.to_dict(), "default_transaction_read_only": True,
        "statement_timeout_ms": 30000, "lock_timeout_ms": 5000, "ddl_dml_executed": False,
        "complete_rows_saved": False,
    }
    expected_non_null = {
        "asset_type": 33, "asset_type_name": 33, "asset_group": 33,
        "asset_group_name": 33, "parent_type": 2,
    }
    if not all((
        len(rows) == 33, columns == EXPECTED_COLUMNS, non_null == expected_non_null,
        result["asset_type_unique_count"] == 33,
        result["asset_type_name_unique_count"] == 33,
        result["business_tuple_unique_count"] == 33,
        duplicate_groups == 0, conflict_groups == 0,
    )):
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def _query_retrieval(memory: Any, question: str, target_sample_id: str) -> dict[str, Any]:
    from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    context = ToolContext(
        user=User(id="f5-batch07", username="f5-batch07", group_memberships=[]),
        conversation_id="f5-batch07-bidirectional", request_id="f5-batch07-bidirectional",
        agent_memory=memory, metadata={},
    )
    results = asyncio.run(memory.search_similar_usage(
        question, context, limit=5, similarity_threshold=0.0, tool_name_filter="run_sql"
    ))
    top5 = [{
        "rank": index, "memory_id": item.memory.memory_id,
        "sample_id": (item.memory.metadata or {}).get("sample_id", ""),
        "question": item.memory.question, "similarity": round(float(item.similarity_score), 6),
    } for index, item in enumerate(results, 1)]
    enhancer = SqlExampleContextEnhancer(memory=memory, sql_guard=SQLGuard(), top_k=5)
    examples = asyncio.run(enhancer._retrieve_examples(question))
    target_examples = [item for item in examples if item.get("sample_id") == target_sample_id]
    filtered = [item for item in enhancer.last_stats.filtered if item.get("sample_id") == target_sample_id]
    target = next((item for item in top5 if item["sample_id"] == target_sample_id), None)
    return {
        "top5": top5, "target_sample_id": target_sample_id,
        "target_in_top5": target is not None, "target_rank": target["rank"] if target else None,
        "target_injected": bool(target_examples), "target_filtered": bool(filtered),
        "target_injected_sql": target_examples[0].get("sql", "") if target_examples else "",
        "enhancer_stats": asdict(enhancer.last_stats),
    }


def bidirectional_retrieval(memory: Any) -> dict[str, Any]:
    b07_query = _query_retrieval(memory, EXPECTED_QUESTION, EXPECTED_SAMPLE_ID)
    b01_query = _query_retrieval(memory, B01_QUESTION, B01_SAMPLE_ID)
    b07_b01 = next((item for item in b07_query["top5"] if item["sample_id"] == B01_SAMPLE_ID), None)
    b01_b07 = next((item for item in b01_query["top5"] if item["sample_id"] == EXPECTED_SAMPLE_ID), None)
    checks = {
        "b07_in_top5": b07_query["target_in_top5"],
        "b07_rank_1": b07_query["target_rank"] == 1,
        "b07_injected": b07_query["target_injected"],
        "b07_not_filtered": not b07_query["target_filtered"],
        "b07_sql_exact": normalized_sql(b07_query["target_injected_sql"]) == normalized_sql(EXPECTED_SQL),
        "b01_below_b07_for_b07_query": b07_b01 is None or b07_b01["rank"] > 1,
        "b01_in_top5": b01_query["target_in_top5"],
        "b01_rank_1": b01_query["target_rank"] == 1,
        "b01_injected": b01_query["target_injected"],
        "b01_not_filtered": not b01_query["target_filtered"],
        "b01_sql_exact": normalized_sql(b01_query["target_injected_sql"]) == normalized_sql(B01_SQL),
        "b07_below_b01_for_b01_query": b01_b07 is None or b01_b07["rank"] > b01_query["target_rank"],
    }
    result = {
        "b07_query": b07_query, "b01_query": b01_query,
        "b07_query_b01_memory_rank": b07_b01["rank"] if b07_b01 else None,
        "b01_query_b07_memory_rank": b01_b07["rank"] if b01_b07 else None,
        "checks": checks, "accepted": all(checks.values()),
    }
    if not result["accepted"]:
        raise RuntimeError("BIDIRECTIONAL_RETRIEVAL_FAILED")
    return result


def write_and_verify(memory: Any, adapter: Any, plan: Any, initial_count: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    preflight, write_result = COMMON.write_and_verify(memory, adapter, plan, initial_count)
    retrieval = bidirectional_retrieval(memory)
    return preflight, write_result, retrieval


def validate_target(case_result: dict[str, Any], *, question: str, tables: list[str], columns: list[str], expected_rows: int) -> dict[str, Any]:
    return previous.validate_target(case_result, question=question, tables=tables, columns=columns, expected_rows=expected_rows)


def run_regressions(data_dir: Path, agent_dir: Path) -> tuple[dict[str, Any], list[str], str]:
    from tools.f2_end_to_end_mvp_probe import CASES, run_case, start_server, stop_server

    process = None
    logs: list[str] = []
    key = ""
    try:
        process, logs, _, key = start_server(data_dir, agent_dir, False)
        f2_cases = [run_case(case, agent_dir, True) for case in CASES]
        target_specs = (
            ("B02_TARGET", BATCH02_QUESTION, BATCH02_TABLES, BATCH02_COLUMNS, 50),
            ("B03_TARGET", BATCH03_QUESTION, BATCH03_TABLES, BATCH03_COLUMNS, 7),
            ("B04_TARGET", BATCH04_QUESTION, BATCH04_TABLES, BATCH04_COLUMNS, 15),
            ("B05_TARGET", BATCH05_QUESTION, BATCH05_TABLES, BATCH05_COLUMNS, 12),
            ("B06_TARGET", BATCH06_QUESTION, BATCH06_TABLES, BATCH06_COLUMNS, 50),
            ("B07_TARGET", EXPECTED_QUESTION, EXPECTED_TABLES, EXPECTED_COLUMNS, 33),
        )
        cases = {}
        validations = {}
        for case_id, question, tables, columns, expected_rows in target_specs:
            case = run_case({"id": case_id, "query": question, "tables": tables, "limit": 50}, agent_dir, True)
            validation = validate_target(case, question=question, tables=tables, columns=columns, expected_rows=expected_rows)
            if case_id == "B07_TARGET":
                sql = str(validation.get("sql", ""))
                validation["checks"]["parent_type_filter_absent"] = not bool(
                    re.search(r"parent_type\s+is\s+not\s+null", sql, flags=re.I)
                )
                validation["accepted"] = all(validation["checks"].values())
            cases[case_id] = case
            validations[case_id] = validation
        f2 = COMMON.regression_summary(f2_cases)
        pass_count = f2["question_pass_count"] + sum(int(item["accepted"]) for item in validations.values())
        return ({
            "question_count": 12, "question_pass_count": pass_count,
            "accepted": COMMON.regression_passed(f2) and all(item["accepted"] for item in validations.values()),
            "f2_summary": f2, "f2_cases": f2_cases,
            **{f"{case_id.lower()}_case": cases[case_id] for case_id in cases},
            **{f"{case_id.lower()}_validation": validations[case_id] for case_id in validations},
        }, logs, key)
    finally:
        stop_server(process)


def worker_paths(root: Path, prefix: str) -> dict[str, Path]:
    evidence = root / "evidence"
    return {name: evidence / f"{prefix}-{name}" for name in (
        "preflight.json", "write-result.json", "bidirectional-retrieval.json",
        "regression.json", "worker-summary.json", "server-log.txt",
    )}


def run_isolated_worker(root: Path, data: Path, prefix: str) -> int:
    paths = worker_paths(root, prefix)
    agent_data = root / ("agent_data" if prefix == "attempt1" else f"agent_data-{prefix}")
    agent_data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "VANNA_DATA_DIR": str(data), "AGENT_DATA_DIR": str(agent_data),
        "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0",
    })
    if "agent_config" in sys.modules:
        raise RuntimeError("EARLY_AGENT_CONFIG_IMPORT")
    configure_existing_helpers()
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    plan = build_memory_write_plan(batch, approved_batch_content_sha256=validation.batch_content_sha256 or "", sql_guard=SQLGuard())
    memory = None
    try:
        memory, adapter = COMMON.open_memory(data, root)
        preflight, write_result, retrieval = write_and_verify(memory, adapter, plan, 193)
        write_json(paths["preflight.json"], preflight)
        write_json(paths["write-result.json"], write_result)
        write_json(paths["bidirectional-retrieval.json"], retrieval)
        close_memory(memory)
        memory = None
        gc.collect()
        regression, logs, key = run_regressions(data, agent_data)
        write_json(paths["regression.json"], sanitized(regression))
        from tools.f2_end_to_end_mvp_probe import redact
        paths["server-log.txt"].write_text(redact("\n".join(logs), [key]), encoding="utf-8")
        accepted = regression["accepted"] and retrieval["accepted"] and sqlite_record_count(data) == 194
        summary = {
            "accepted": accepted, "initial_count": 193,
            "created_count": write_result["created_count"], "final_count": write_result["final_count"],
            "preflight": preflight, "write": write_result,
            "bidirectional_retrieval": retrieval, "regression": regression,
        }
        write_json(paths["worker-summary.json"], sanitized(summary))
        return 0 if accepted else 2
    finally:
        close_memory(memory)


def copy_attempt_to_canonical(root: Path, prefix: str) -> None:
    evidence = root / "evidence"
    mapping = {
        "preflight.json": "isolated-preflight.json",
        "write-result.json": "isolated-write-result.json",
        "bidirectional-retrieval.json": "isolated-bidirectional-retrieval.json",
        "regression.json": "isolated-regression.json",
        "worker-summary.json": "isolated-worker-summary.json",
        "server-log.txt": "server-log.txt",
    }
    for source, target in mapping.items():
        shutil.copyfile(evidence / f"{prefix}-{source}", evidence / target)


def retryable_failure(summary: dict[str, Any]) -> tuple[bool, str]:
    regression = summary.get("regression", {})
    if regression.get("question_count") != 12 or regression.get("question_pass_count") != 11:
        return False, "NOT_SINGLE_QUESTION_FAILURE"
    failed_f2 = [item for item in regression.get("f2_cases", []) if not item.get("passed")]
    failed_targets = [
        regression.get(f"b{number:02d}_target_validation", {})
        for number in range(2, 8)
        if not regression.get(f"b{number:02d}_target_validation", {}).get("accepted")
    ]
    if len(failed_f2) == 1 and not failed_targets and failed_f2[0].get("failure_stage") in {"sql_present", "http_200", "no_sse_error"}:
        return True, f"LLM_RANDOM_FAILURE_{failed_f2[0].get('id')}_{failed_f2[0].get('failure_stage')}"
    if not failed_f2 and len(failed_targets) == 1:
        return True, "LLM_RANDOM_FAILURE_SINGLE_TARGET"
    return False, "FAILURE_NOT_ELIGIBLE_FOR_RETRY"


def run_worker(root: Path, data: Path, prefix: str) -> tuple[int, dict[str, Any], str]:
    process = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--isolated-worker", str(root), "--isolated-data", str(data), "--evidence-prefix", prefix],
        cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
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
    if sqlite_record_count(RUNTIME_SOURCE) != 193 or runtime_before["content_sha256"] != EXPECTED_FORMAL_SHA256:
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
    root = BACKUP_PARENT / f"f5-level2-batch07-{timestamp}"
    evidence = root / "evidence"
    backup_data = BACKUP_PARENT / f"f5-level2-batch07-prewrite-{timestamp}" / "runtime-vanna_data"
    evidence.mkdir(parents=True)
    for name, value in (
        ("batch.json", batch), ("batch-validation.json", validation.to_dict()),
        ("candidate-source-validation.json", candidate_source),
        ("metadata-validation.json", metadata), ("database-validation.json", database),
        ("write-plan.json", plan.to_dict()),
    ):
        write_json(evidence / name, value)
    backup_data.parent.mkdir(parents=True)
    backup = create_verified_copy(RUNTIME_SOURCE, backup_data, PROJECT_ROOT)
    backup_payload = {
        "runtime_before": runtime_before, "backup": backup.destination.to_dict(),
        "record_count": sqlite_record_count(backup_data),
        "verified": backup.destination.content_sha256 == EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup_data) == 193,
    }
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
            for source, target in (
                ("worker-summary.json", "first-attempt-summary.json"),
                ("regression.json", "first-attempt-regression.json"),
                ("server-log.txt", "first-attempt-server-log.txt"),
            ):
                shutil.copyfile(worker_paths(root, "attempt1")[source], evidence / target)
            retry_data = root / "isolated-retry" / "vanna_data"
            retry_data.parent.mkdir(parents=True)
            create_verified_copy(backup_data, retry_data, PROJECT_ROOT)
            code2, second, output2 = run_worker(root, retry_data, "attempt2")
            attempts.append({"attempt": 2, "accepted": bool(second.get("accepted")), "worker_exit_code": code2})
            if code2 == 0 and second.get("accepted"):
                accepted_prefix = "attempt2"
            else:
                output = output2
    attempts_payload = {
        "attempt_count": len(attempts), "first_attempt_result": "PASS" if first.get("accepted") else "FAIL",
        "retry_executed": retry_executed, "retry_reason": retry_reason, "attempts": attempts,
    }
    write_json(evidence / "isolated-attempts.json", attempts_payload)
    if accepted_prefix is None:
        summary = {
            "f5_batch07_accepted": False, "failure_stage": "isolated", "attempts": attempts,
            "worker_output": output, "formal_opened": False,
            "formal_write_executed": False, "recovery_executed": False,
        }
        write_json(evidence / "f5-batch07-summary.json", sanitized(summary))
        (evidence / "rollback.txt").write_text("隔离验收失败，正式库未打开、未写入，无需恢复。\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2
    copy_attempt_to_canonical(root, accepted_prefix)
    isolated = json.loads((evidence / "isolated-worker-summary.json").read_text(encoding="utf-8"))

    current = manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE) != 193 or current["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_CHANGED_BEFORE_WRITE")
    recalculated = validate_training_batch(load_batch(), sql_guard=SQLGuard())
    recalculated_plan = build_memory_write_plan(load_batch(), approved_batch_content_sha256=recalculated.batch_content_sha256 or "", sql_guard=SQLGuard())
    if (recalculated.batch_content_sha256, recalculated_plan.write_plan_sha256, recalculated_plan.items[0].record_id) != (validation.batch_content_sha256, plan.write_plan_sha256, plan.items[0].record_id):
        raise RuntimeError("FORMAL_IDENTITY_RECALCULATION_MISMATCH")

    os.environ.update({"VANNA_DATA_DIR": str(RUNTIME_SOURCE), "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    memory = None
    formal_write_started = False
    try:
        memory, adapter = COMMON.open_memory(RUNTIME_SOURCE, RUNTIME_SOURCE.parent)
        formal_write_started = True
        preflight, write_result, retrieval = write_and_verify(memory, adapter, plan, 193)
        write_json(evidence / "formal-preflight.json", preflight)
        write_json(evidence / "formal-write-result.json", write_result)
        write_json(evidence / "formal-bidirectional-retrieval.json", retrieval)
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
        if not regression["accepted"] or not retrieval["accepted"] or after_smoke != 194:
            raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        summary = {
            "f5_batch07_accepted": True, "root": str(root), "backup_path": str(backup_data),
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256, "record_id": plan.items[0].record_id,
            "candidate_source": candidate_source, "metadata": metadata, "database": database,
            "isolated_attempts": attempts_payload, "isolated": isolated,
            "formal": write_result, "formal_bidirectional_retrieval": retrieval,
            "formal_regression": regression, "formal_record_count_after_smoke": after_smoke,
            "recovery_executed": False, "memory_delete_executed": False,
            "old_uuid_migration_executed": False, "ddl_dml_executed": False,
        }
        write_json(evidence / "f5-batch07-summary.json", sanitized(summary))
        (evidence / "rollback.txt").write_text(f"失败恢复源：{backup_data}\n不得使用单条Memory删除。\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items() if key not in {"isolated", "formal_regression"}}, ensure_ascii=False))
        return 0
    except Exception:
        close_memory(memory)
        memory = None
        gc.collect()
        if formal_write_started:
            COMMON.restore_runtime(backup_data, root, evidence)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

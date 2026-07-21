from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import (
    close_memory,
    get_user_environment,
    manifest,
    sqlite_record_count,
    write_json,
)
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import MemoryWritePlan, build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy

BATCH_FILE = PROJECT_ROOT / "training" / "f5_level3_batch01.json"
ROADMAP = PROJECT_ROOT / "docs" / "project_master_roadmap.md"
RUNTIME = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_ROOT = Path(r"E:\3\_training_backups")
PYTHON = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
RUNNER = PROJECT_ROOT / "tools" / "run_postgresql_f5_regression.py"
SUITE = PROJECT_ROOT / "training" / "regression" / "postgresql_f5_regression_v1.json"

BASE_COMMIT = "f0bcfc84d6e8e1151973771c8ba487d83ce2d9e8"
FORMAL_SHA = "222bc79b0d08ee895ded4cd0f8beaf641e4faba8b7c55b2b6c333d089a837b26"
BATCH_ID = "level3-f5-batch01-20260717-01"
SAMPLE_ID = "F5_L3_B01_SQL_001"
QUESTION = "查询香溪河泗湘溪站最近的水质日变化趋势，包括监测时间、pH和溶解氧，最多返回50条"
NATURAL_QUESTION = "查看香溪河泗湘溪站最近的每日pH和溶解氧变化"
SQL = """SELECT s.station_code,
       s.station_name,
       d.monitor_time,
       d.m2_value AS ph_value,
       d.m3_value AS dissolved_oxygen_value
FROM wm_station_info_v2 AS s
INNER JOIN wm_waterquality_day_records AS d ON s.id = d.station_id
WHERE s.station_name = '香溪河泗湘溪站'
ORDER BY d.monitor_time DESC
LIMIT 50"""
EXPECTED_BEHAVIOR = "返回香溪河泗湘溪站最近最多50条日水质趋势记录，包含站点编码、站点名称、监测时间、pH和溶解氧；按监测时间降序排列。"
TABLES = ["wm_station_info_v2", "wm_waterquality_day_records"]
COLUMNS = ["station_code", "station_name", "monitor_time", "ph_value", "dissolved_oxygen_value"]
EXTENSIONS = {
    "expected_columns": COLUMNS,
    "training_mode": "STANDARD",
    "capability_level": "L3_TIME_SERIES_JOIN",
    "training_value_classification": "STABLE_JOIN_TIME_SERIES_PATTERN",
    "semantic_risk": "LOW",
    "identifier_strategy": "NATURAL_NAME_IDENTIFIER",
    "selected_grain": "DAY",
    "deferred_variant": "HOUR / SAME_CLUSTER_DEFERRED_VARIANT",
}
LEGACY_PROBES = (
    ("94374c46-7be4-49d7-b102-76bdc27faa6e", "某站点最近一段时间水质日变化趋势"),
    ("8f209835-57a3-4d0e-a9f7-c53c3a460685", "某站点水质小时变化趋势"),
    ("eeb6d4b3-bcd8-4ed1-a080-e13472bffc44", "查询站点名称和所属区域"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-worker", action="store_true")
    parser.add_argument("--target-worker", action="store_true")
    parser.add_argument("--isolated-data", type=Path)
    parser.add_argument("--isolated-root", type=Path)
    parser.add_argument("--evidence-prefix", default="attempt1")
    return parser.parse_args()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalized_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def frozen_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
    sample = batch.get("samples", [{}])[0]
    checks = {
        "schema_version": batch.get("schema_version") == "1.0",
        "batch_id": batch.get("training_batch_id") == BATCH_ID,
        "batch_level": batch.get("training_level") == "level3_sql_examples",
        "status": batch.get("status") == "frozen",
        "expected_count": batch.get("expected_new_memory_count") == 1,
        "one_sample": len(batch.get("samples", [])) == 1,
        "sample_id": sample.get("sample_id") == SAMPLE_ID,
        "question": sample.get("question") == QUESTION,
        "sql": sample.get("args", {}).get("sql") == SQL,
        "behavior": sample.get("expected_behavior") == EXPECTED_BEHAVIOR,
        "tables": sample.get("expected_tables") == TABLES,
        "tool": sample.get("tool_name") == "run_sql",
        "level": sample.get("training_level") == "level3_sql_examples",
        "approved": sample.get("train_decision") == "approved",
        **{key: sample.get(key) == value for key, value in EXTENSIONS.items()},
    }
    if not all(checks.values()):
        raise RuntimeError(f"FROZEN_BATCH_MISMATCH:{checks}")
    return batch


def contract_batch(batch: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "sample_id", "question", "tool_name", "args", "training_level",
        "train_decision", "review_reason", "source", "expected_behavior", "expected_tables",
    }
    projected = dict(batch)
    projected["samples"] = [{k: v for k, v in batch["samples"][0].items() if k in allowed}]
    return projected


def enriched_plan(batch: dict[str, Any]) -> tuple[Any, MemoryWritePlan]:
    projected = contract_batch(batch)
    validation = validate_training_batch(projected, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError(f"STANDARD_BATCH_VALIDATION_FAILED:{validation.to_dict()}")
    plan = build_memory_write_plan(
        projected,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    if not plan.executable or plan.create_count != 1 or len(plan.items) != 1:
        raise RuntimeError("WRITE_PLAN_INVALID")
    item = plan.items[0]
    compatibility = dict(item.compatibility_metadata)
    compatibility.update(EXTENSIONS)
    enriched = replace(item, compatibility_metadata=compatibility)
    plan = replace(plan, items=(enriched,))
    return validation, plan


def roadmap_gate() -> dict[str, Any]:
    text = ROADMAP.read_text(encoding="utf-8")
    required = [BATCH_ID, SAMPLE_ID, "F5 Level 3 Batch 01正式交付", "DAY", "SAME_CLUSTER_DEFERRED_VARIANT"]
    result = {"required_markers": required, "checks": {x: x in text for x in required}}
    result["pass"] = all(result["checks"].values())
    if not result["pass"]:
        raise RuntimeError("ROADMAP_GATE_FAILED")
    return result


def formal_state() -> dict[str, Any]:
    return {"record_count": sqlite_record_count(RUNTIME), "manifest": manifest(RUNTIME)}


def assert_formal_baseline() -> dict[str, Any]:
    first, second = formal_state(), formal_state()
    ok = all(
        x["record_count"] == 197 and x["manifest"]["content_sha256"] == FORMAL_SHA
        for x in (first, second)
    ) and first == second
    if not ok:
        raise RuntimeError("FORMAL_BASELINE_MISMATCH")
    return {"verification_1": first, "verification_2": second, "stable": True}


def configure_database() -> None:
    for name in ("DB_USER", "DB_PASSWORD"):
        if os.getenv(name, "").strip():
            continue
        found, value = get_user_environment(name)
        if not found or not value.strip():
            raise RuntimeError(f"DATABASE_ENVIRONMENT_MISSING:{name}")
        os.environ[name] = value.strip()


def database_validation() -> dict[str, Any]:
    import psycopg2
    configure_database()
    guard = SQLGuard().validate(SQL, QUESTION, TABLES)
    if not guard.passed or guard.used_tables != TABLES:
        raise RuntimeError("SQL_GUARD_FAILED")
    kwargs = {
        "host": os.getenv("DB_HOST", "localhost"), "port": int(os.getenv("DB_PORT", "5433")),
        "database": os.getenv("DB_NAME", "gt_monitor"), "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"], "connect_timeout": 10,
        "application_name": "f5-l3-b01-readonly",
        "options": "-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    }
    with psycopg2.connect(**kwargs) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(SQL)
            rows = cursor.fetchall()
            columns = [item.name for item in cursor.description or ()]
    non_null = {columns[i]: sum(row[i] is not None for row in rows) for i in range(len(columns))}
    times = [row[2] for row in rows]
    result = {
        "database_connected": True, "database_read_only": True, "database_sql_execution_count": 1,
        "sql_guard": guard.to_dict(), "row_count": len(rows), "columns": columns,
        "field_non_null_counts": non_null, "station_name_all_match": all(row[1] == "香溪河泗湘溪站" for row in rows),
        "monitor_time_desc": times == sorted(times, reverse=True), "ddl_dml_executed": False,
        "complete_rows_saved": False,
    }
    if not all((len(rows) == 50, columns == COLUMNS, all(v == 50 for v in non_null.values()),
                result["station_name_all_match"], result["monitor_time_desc"])):
        raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def open_memory(data: Path, root: Path) -> tuple[Any, Any]:
    if Path(os.environ.get("VANNA_DATA_DIR", "")).resolve() != data.resolve():
        raise RuntimeError("ISOLATED_PATH_MISMATCH")
    from backend.memory import EMBEDDING_FUNCTION
    from vanna.integrations.chromadb import ChromaAgentMemory
    from training.sop.chroma_tool_memory_adapter import ChromaToolMemoryAdapter
    memory = ChromaAgentMemory(
        persist_directory=str(data), collection_name="tool_memories", embedding_function=EMBEDDING_FUNCTION
    )
    return memory, ChromaToolMemoryAdapter(memory, isolated_root=root)


def record_view(record: Any) -> dict[str, Any]:
    metadata = dict(record.metadata or {})
    compatibility = dict(record.compatibility_metadata or {})
    return {
        "storage_id": record.storage_id, "classification": record.classification,
        "question": (record.canonical_content or {}).get("question"),
        "sql": ((record.canonical_content or {}).get("args") or {}).get("sql"),
        "sample_id": compatibility.get("sample_id"), "expected_tables": compatibility.get("expected_tables"),
        "metadata": metadata, "compatibility_metadata": compatibility,
    }


async def search(memory: Any, question: str) -> list[dict[str, Any]]:
    from vanna.core.tool import ToolContext
    from vanna.core.user import User
    context = ToolContext(
        user=User(id="f5-l3-b01", username="f5-l3-b01", group_memberships=[]),
        conversation_id="f5-l3-b01", request_id="f5-l3-b01", agent_memory=memory, metadata={},
    )
    hits = await memory.search_similar_usage(
        question, context, limit=5, similarity_threshold=0.0, tool_name_filter="run_sql"
    )
    return [{"rank": i + 1, "storage_id": hit.memory.memory_id} for i, hit in enumerate(hits)]


def retrieval_checks(memory: Any, record_id: str) -> dict[str, Any]:
    queries: dict[str, Any] = {}
    for name, question, expected in (
        ("frozen", QUESTION, record_id), ("natural", NATURAL_QUESTION, record_id),
        ("legacy_day", LEGACY_PROBES[0][1], LEGACY_PROBES[0][0]),
        ("legacy_hour", LEGACY_PROBES[1][1], LEGACY_PROBES[1][0]),
        ("legacy_station", LEGACY_PROBES[2][1], LEGACY_PROBES[2][0]),
    ):
        hits = asyncio.run(search(memory, question))
        queries[name] = {"question": question, "top5": hits, "expected_rank1": expected,
                         "rank1_pass": bool(hits and hits[0]["storage_id"] == expected)}
    new_to_old = queries["frozen"]["rank1_pass"] and queries["natural"]["rank1_pass"]
    old_to_new = queries["legacy_day"]["rank1_pass"] and queries["legacy_station"]["rank1_pass"]
    hour = queries["legacy_hour"]["rank1_pass"] and queries["legacy_hour"]["top5"][0]["storage_id"] != record_id
    return {"queries": queries, "new_to_old_retrieval_pass": new_to_old,
            "old_to_new_retrieval_pass": old_to_new, "hour_variant_collision_pass": hour,
            "bidirectional_retrieval_pass": new_to_old and old_to_new and hour}


def isolated_worker(args: argparse.Namespace) -> int:
    root, data, prefix = args.isolated_root.resolve(), args.isolated_data.resolve(), args.evidence_prefix
    evidence = root / "evidence"
    os.environ.update({"VANNA_DATA_DIR": str(data), "AGENT_DATA_DIR": str(root / f"agent-{prefix}"),
                       "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    (root / f"agent-{prefix}").mkdir(parents=True, exist_ok=True)
    batch = frozen_batch()
    validation, plan = enriched_plan(batch)
    memory = None
    try:
        memory, adapter = open_memory(data, root)
        before = adapter.inventory_tool_records()
        records = [record_view(r) for r in before.records]
        exact = sum(r["question"] == QUESTION for r in records)
        sql_dupes = sum(normalized_sql(r.get("sql") or "") == normalized_sql(SQL) for r in records)
        sample_dupes = sum(r.get("sample_id") == SAMPLE_ID for r in records)
        record_dupes = sum(r["storage_id"] == plan.items[0].record_id for r in records)
        semantic = sum(r.get("expected_tables") == TABLES and r["question"] == QUESTION for r in records)
        duplicate = {"exact_question_count": exact, "normalized_sql_count": sql_dupes,
                     "sample_id_count": sample_dupes, "record_id_count": record_dupes,
                     "semantic_controlled_count": semantic, "pass": not any((exact, sql_dupes, sample_dupes, record_dupes, semantic))}
        write_json(evidence / f"{prefix}-duplicate-check-before.json", duplicate)
        preflight = adapter.inspect_plan_records(plan)
        if not duplicate["pass"] or not preflight.executable or len(preflight.absent_record_ids) != 1:
            raise RuntimeError("DUPLICATE_OR_PREFLIGHT_FAILED")
        added = adapter.add_planned_record(plan, plan.items[0])
        after = adapter.inventory_tool_records()
        exact_record = adapter.get_exact_records([plan.items[0].record_id])[0]
        view = record_view(exact_record.record) if exact_record.record else {}
        counts = dict(after.classifications)
        metadata_pass = all(view.get("compatibility_metadata", {}).get(k) == v for k, v in EXTENSIONS.items())
        content_pass = all((view.get("question") == QUESTION, normalized_sql(view.get("sql") or "") == normalized_sql(SQL),
                            view.get("sample_id") == SAMPLE_ID, view.get("expected_tables") == TABLES))
        retrieval = retrieval_checks(memory, plan.items[0].record_id)
        result = {
            "record_id": plan.items[0].record_id, "add_status": added.status,
            "record_count_before": before.store_count_after, "record_count_after": after.store_count_after,
            "classifications_after": counts, "memory_added_count": after.store_count_after - before.store_count_after,
            "metadata_pass": metadata_pass, "content_pass": content_pass, "record": view,
        }
        accepted = all((before.store_count_after == 197, added.status == "created", after.store_count_after == 198,
                        counts.get("text_memory") == 123, counts.get("legacy_tool_record") == 64,
                        counts.get("controlled_tool_record") == 11, metadata_pass, content_pass,
                        retrieval["bidirectional_retrieval_pass"]))
        write_json(evidence / f"{prefix}-isolated-write-result.json", result)
        write_json(evidence / f"{prefix}-isolated-memory-counts.json", {"counts": counts, "total": after.store_count_after})
        write_json(evidence / f"{prefix}-bidirectional-retrieval.json", retrieval)
        close_memory(memory); memory = None; gc.collect()
        final_manifest = manifest(data)
        summary = {"accepted": accepted, "record_id": plan.items[0].record_id,
                   "record_count_before": before.store_count_after, "record_count_after": after.store_count_after,
                   "sha256_after": final_manifest["content_sha256"], "duplicate": duplicate,
                   "write": result, "retrieval": retrieval, "batch_content_sha256": validation.batch_content_sha256,
                   "write_plan": plan.to_dict()}
        write_json(evidence / f"{prefix}-worker-summary.json", summary)
        return 0 if accepted else 2
    finally:
        close_memory(memory)


def run_worker(root: Path, data: Path, prefix: str) -> dict[str, Any]:
    command = [str(PYTHON), str(Path(__file__).resolve()), "--isolated-worker",
               "--isolated-data", str(data), "--isolated-root", str(root), "--evidence-prefix", prefix]
    proc = subprocess.run(command, cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    summary_path = root / "evidence" / f"{prefix}-worker-summary.json"
    if proc.returncode or not summary_path.exists():
        (root / "evidence" / f"{prefix}-worker.log").write_text(proc.stdout[-16000:], encoding="utf-8")
        raise RuntimeError(f"ISOLATED_WORKER_FAILED:{proc.returncode}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def target_e2e_inner(root: Path, data: Path, prefix: str) -> dict[str, Any]:
    os.environ.update({"VANNA_DATA_DIR": str(data), "AGENT_DATA_DIR": str(root / f"target-agent-{prefix}"),
                       "HF_HUB_OFFLINE": "1", "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0"})
    from tools.f2_end_to_end_mvp_probe import run_case, start_server, stop_server, redact
    agent = root / f"target-agent-{prefix}"
    agent.mkdir(parents=True, exist_ok=True)
    process = None
    logs: list[str] = []
    key = ""
    try:
        process, logs, _, key = start_server(data, agent, False)
        case = run_case({"id": "F5_L3_B01_TARGET", "query": QUESTION, "tables": TABLES, "limit": 50}, agent, True)
    finally:
        stop_server(process)
    (root / "evidence" / f"{prefix}-target-server.log").write_text(redact("\n".join(logs), [key]), encoding="utf-8")
    result = case.get("result", {})
    sql = str(result.get("sql") or "")
    checks = {
        "case_passed": bool(case.get("passed")), "tables": set(result.get("tables", [])) == set(TABLES),
        "sql_guard": bool(result.get("guard", {}).get("passed")), "columns": result.get("columns") == COLUMNS,
        "row_count": 1 <= int(result.get("row_count", 0)) <= 50,
        "station_filter": bool(re.search(r"station_name\s*=\s*'香溪河泗湘溪站'", sql, re.I)),
        "time_desc": bool(re.search(r"monitor_time\s+desc", sql, re.I)), "limit_50": bool(re.search(r"limit\s+50", sql, re.I)),
        "no_hour_table": "hour" not in sql.lower(), "no_schema_exploration": not bool(re.search(r"information_schema|pg_catalog", sql, re.I)),
    }
    return {"accepted": all(checks.values()), "checks": checks, "case": case}


def target_e2e(root: Path, data: Path, prefix: str) -> dict[str, Any]:
    output = root / "evidence" / f"{prefix}-target-worker-result.json"
    command = [str(PYTHON), str(Path(__file__).resolve()), "--target-worker",
               "--isolated-data", str(data), "--isolated-root", str(root), "--evidence-prefix", prefix]
    proc = subprocess.run(command, cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (root / "evidence" / f"{prefix}-target-worker.log").write_text(proc.stdout[-16000:], encoding="utf-8")
    if proc.returncode or not output.exists():
        raise RuntimeError(f"TARGET_WORKER_FAILED:{proc.returncode}")
    return json.loads(output.read_text(encoding="utf-8"))


def run_fixed_regression(root: Path, data: Path, prefix: str) -> dict[str, Any]:
    evidence = root / "evidence" / f"regression-{prefix}"
    agent = root / f"regression-agent-{prefix}"
    command = [str(PYTHON), str(RUNNER), "--data-dir", str(data), "--agent-dir", str(agent), "--evidence-dir", str(evidence)]
    proc = subprocess.run(command, cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    (root / "evidence" / f"{prefix}-regression-runner.log").write_text(proc.stdout[-20000:], encoding="utf-8")
    path = evidence / "regression-result.json"
    if not path.exists():
        raise RuntimeError(f"REGRESSION_RESULT_MISSING:{proc.returncode}")
    result = json.loads(path.read_text(encoding="utf-8"))
    result["runner_exit_code"] = proc.returncode
    return result


def regression_accepted(result: dict[str, Any]) -> bool:
    summary = result.get("summary", result)
    return bool(summary.get("accepted") or summary.get("regression_accepted")) and int(summary.get("question_count", 0)) == 15 and int(summary.get("question_pass_count", summary.get("pass_count", 0))) == 15


def switch_runtime(root: Path, isolated: Path, isolated_sha: str) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parent = RUNTIME.parent
    staging = parent / f"vanna_data.switch-level3-b01-{stamp}"
    quarantine = parent / f"vanna_data.pre-level3-b01-222bc79b-{stamp}"
    copy = create_verified_copy(isolated, staging, PROJECT_ROOT)
    staging_state = {"path": str(staging), "copy": copy.destination.to_dict(),
                     "record_count": sqlite_record_count(staging), "sha256": manifest(staging)["content_sha256"]}
    if staging_state["record_count"] != 198 or staging_state["sha256"] != isolated_sha:
        raise RuntimeError("SWITCH_STAGING_INVALID")
    rollback = False
    RUNTIME.rename(quarantine)
    try:
        staging.rename(RUNTIME)
    except Exception:
        rollback = True
        quarantine.rename(RUNTIME)
        raise
    return {"executed": True, "rollback_executed": rollback, "staging": staging_state,
            "quarantine_path": str(quarantine)}


def main() -> int:
    args = parse_args()
    if args.isolated_worker:
        return isolated_worker(args)
    if args.target_worker:
        result = target_e2e_inner(args.isolated_root.resolve(), args.isolated_data.resolve(), args.evidence_prefix)
        write_json(args.isolated_root.resolve() / "evidence" / f"{args.evidence_prefix}-target-worker-result.json", result)
        return 0 if result["accepted"] else 2
    gate = roadmap_gate()
    before = assert_formal_baseline()
    batch = frozen_batch()
    validation, plan = enriched_plan(batch)
    db = database_validation()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = BACKUP_ROOT / f"f5-level3-batch01-delivery-{stamp}"
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    write_json(evidence / "roadmap-gate.json", gate)
    write_json(evidence / "formal-before.json", before)
    write_json(evidence / "frozen-batch-validation.json", {"full_batch_sha256": sha256_json(batch), "standard_validation": validation.to_dict(), "valid": True})
    write_json(evidence / "memory-write-plan.json", plan.to_dict())
    write_json(evidence / "database-readonly-validation.json", db)
    backup = root / "prewrite-backup" / "vanna_data"
    isolated = root / "isolated-validation" / "vanna_data"
    backup.parent.mkdir(parents=True); isolated.parent.mkdir(parents=True)
    backup_copy = create_verified_copy(RUNTIME, backup, PROJECT_ROOT)
    backup_validation = {"copy": backup_copy.destination.to_dict(), "record_count": sqlite_record_count(backup),
                         "sha256": manifest(backup)["content_sha256"]}
    write_json(evidence / "prewrite-backup-validation.json", backup_validation)
    if backup_validation["record_count"] != 197 or backup_validation["sha256"] != FORMAL_SHA:
        raise RuntimeError("PREWRITE_BACKUP_INVALID")
    isolated_copy = create_verified_copy(RUNTIME, isolated, PROJECT_ROOT)
    isolated_before = {"copy": isolated_copy.destination.to_dict(), "record_count": sqlite_record_count(isolated),
                       "sha256": manifest(isolated)["content_sha256"]}
    write_json(evidence / "isolated-copy-before.json", isolated_before)
    prefix = os.getenv("F5_L3_ATTEMPT", "attempt1")
    prior_evidence = os.getenv("F5_L3_PRIOR_EVIDENCE", "")
    worker = run_worker(root, isolated, prefix)
    formal_checks: list[dict[str, Any]] = [{"stage": "after_isolated_write", **formal_state()}]
    if not worker["accepted"] or formal_checks[-1]["record_count"] != 197 or formal_checks[-1]["manifest"]["content_sha256"] != FORMAL_SHA:
        raise RuntimeError("ISOLATED_OR_FORMAL_MONITOR_FAILED")
    target = target_e2e(root, isolated, prefix)
    write_json(evidence / "target-e2e-result.json", target)
    formal_checks.append({"stage": "after_target_e2e", **formal_state()})
    regression = run_fixed_regression(root, isolated, prefix)
    write_json(evidence / "regression-result.json", regression)
    formal_checks.append({"stage": "after_regression", **formal_state()})
    attempt_number = 2 if prefix == "attempt2" else 1
    attempts = [{"attempt": attempt_number, "target_e2e_pass": target["accepted"], "regression_accepted": regression_accepted(regression)}]
    write_json(evidence / "regression-attempts.json", {"attempt_count": attempt_number, "retry_executed": attempt_number == 2,
               "retry_reason": "FIRST_ATTEMPT_TARGET_NO_SQL" if attempt_number == 2 else "NONE",
               "prior_evidence": prior_evidence or None, "attempts": attempts})
    unchanged = all(x["record_count"] == 197 and x["manifest"]["content_sha256"] == FORMAL_SHA for x in formal_checks)
    write_json(evidence / "formal-monitor-checkpoints.json", {"checkpoints": formal_checks, "unchanged": unchanged})
    if not target["accepted"] or not regression_accepted(regression) or not unchanged:
        raise RuntimeError("ACCEPTANCE_FAILED")
    isolated_sha = manifest(isolated)["content_sha256"]
    switch = switch_runtime(root, isolated, isolated_sha)
    write_json(evidence / "switch-staging-validation.json", switch["staging"])
    write_json(evidence / "directory-switch.json", switch)
    verify1, verify2 = formal_state(), formal_state()
    write_json(evidence / "formal-after-verification-1.json", verify1)
    write_json(evidence / "formal-after-verification-2.json", verify2)
    quarantine = Path(switch["quarantine_path"])
    old = {"path": str(quarantine), "record_count": sqlite_record_count(quarantine), "sha256": manifest(quarantine)["content_sha256"]}
    write_json(evidence / "old-formal-quarantine-validation.json", old)
    success = all((verify1 == verify2, verify1["record_count"] == 198,
                   verify1["manifest"]["content_sha256"] == isolated_sha,
                   old["record_count"] == 197, old["sha256"] == FORMAL_SHA))
    summary = {"accepted": success, "root": str(root), "record_id": plan.items[0].record_id,
               "formal_before": before, "isolated_worker": worker, "database": db, "target_e2e": target,
               "regression": regression, "switch": switch, "formal_after": verify2, "old_formal": old,
               "formal_memory_opened_during_validation": False, "ddl_dml_executed": False}
    write_json(evidence / "delivery-summary.json", summary)
    print(json.dumps({"evidence_directory": str(evidence), "record_id": plan.items[0].record_id,
                      "formal_sha256_after": isolated_sha, "old_formal_quarantine": str(quarantine),
                      "accepted": success}, ensure_ascii=False))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())

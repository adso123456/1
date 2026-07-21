from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard
from training.sop.batch_validator import validate_training_batch
from training.sop.chroma_tool_memory_adapter import ChromaToolMemoryAdapter
from training.sop.memory_write_plan import (
    ExistingRecordSnapshot,
    MemoryWritePlan,
    MemoryWritePlanItem,
    build_memory_write_plan,
)
from training.sop.storage_snapshot import build_directory_manifest, create_verified_copy


RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
LEGACY_FORMAL = PROJECT_ROOT / "vanna_data"
BACKUP_PARENT = Path(r"E:\3\_training_backups")
EXPECTED_RUNTIME_RECORD_COUNT = 187
EXPECTED_LEGACY_SHA256 = "4fd753c4d4c0d22119b6349856f195fe4ed7e23466120f6786edb979606646d8"

BATCH = {
    "schema_version": "1.0",
    "training_batch_id": "level4-zero-b4-rehearsal-20260715-01",
    "training_level": "level4_fixture_sql_examples",
    "status": "frozen",
    "source": "0B-4隔离演练，不属于正式训练资产",
    "expected_new_memory_count": 2,
    "samples": [
        {
            "sample_id": "ZERO_B4_SQL_001",
            "question": "0B-4演练：查询排污口名称、所属区域和区县，最多返回10条",
            "tool_name": "run_sql",
            "args": {
                "sql": "SELECT outlet_name, area_name, county_name FROM rs_outlet ORDER BY outlet_name LIMIT 10"
            },
            "training_level": "level4_fixture_sql_examples",
            "train_decision": "approved",
            "review_reason": "验证Tool Memory单表写入链路",
            "source": "0B-4隔离演练",
            "expected_behavior": "返回最多10条排污口名称、区域和区县",
            "expected_tables": ["rs_outlet"],
        },
        {
            "sample_id": "ZERO_B4_SQL_002",
            "question": "0B-4演练：查询站点1408最近的水质小时监测时间、pH和水质等级，最多返回10条",
            "tool_name": "run_sql",
            "args": {
                "sql": "SELECT station_id, monitor_time, m2_value, water_quality_level FROM wm_waterquality_hour_records WHERE station_id = 1408 ORDER BY monitor_time DESC LIMIT 10"
            },
            "training_level": "level4_fixture_sql_examples",
            "train_decision": "approved",
            "review_reason": "验证Tool Memory时间序列写入链路",
            "source": "0B-4隔离演练",
            "expected_behavior": "返回站点1408最近最多10条水质小时记录",
            "expected_tables": ["wm_waterquality_hour_records"],
        },
    ],
}


class InjectedRehearsalFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def manifest(path: Path) -> dict[str, Any]:
    return build_directory_manifest(path).to_dict()


def sqlite_record_count(path: Path) -> int:
    uri = path.joinpath("chroma.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user_environment(name: str) -> tuple[bool, str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return True, str(value)
    except FileNotFoundError:
        return False, ""


@contextmanager
def restored_process_environment(updates: dict[str, str]) -> Iterator[None]:
    missing = object()
    previous: dict[str, object | str] = {
        name: os.environ.get(name, missing) for name in updates
    }
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is missing:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)


def assert_attempt_path(path: Path, rehearsal_root: Path) -> None:
    resolved = path.resolve()
    root = rehearsal_root.resolve()
    if resolved in {RUNTIME_SOURCE.resolve(), LEGACY_FORMAL.resolve()}:
        raise RuntimeError("FORMAL_PATH_REJECTED")
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeError("ATTEMPT_PATH_OUTSIDE_REHEARSAL") from error
    if resolved == root:
        raise RuntimeError("ATTEMPT_PATH_MUST_BE_CHILD")


def execute_failure_sequence(
    items: tuple[MemoryWritePlanItem, ...],
    writer: Callable[[MemoryWritePlanItem], Any],
) -> tuple[list[Any], bool]:
    results: list[Any] = []
    observed = False
    try:
        for index, item in enumerate(items):
            if index == 1:
                raise InjectedRehearsalFailure("fixed failure before second record")
            results.append(writer(item))
    except InjectedRehearsalFailure:
        observed = True
    return results, observed


def summarize_add_results(results: list[Any]) -> dict[str, int]:
    return {
        "created_count": sum(getattr(item, "status", "") == "created" for item in results),
        "existing_same_count": sum(
            getattr(item, "status", "") == "existing_same" for item in results
        ),
        "failure_count": sum(
            getattr(item, "status", "") not in {"created", "existing_same"}
            for item in results
        ),
    }


def close_memory(memory: Any) -> None:
    if memory is None:
        return
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    try:
        if memory._client is not None:
            memory._client._system.stop()
    except Exception:
        pass
    memory._collection = None
    memory._client = None
    gc.collect()
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def open_isolated_memory(copy_path: Path, rehearsal_root: Path) -> tuple[Any, ChromaToolMemoryAdapter]:
    assert_attempt_path(copy_path, rehearsal_root)
    if Path(os.environ.get("VANNA_DATA_DIR", "")).resolve() != copy_path.resolve():
        raise RuntimeError("PARENT_MEMORY_PATH_MISMATCH")
    from agent_config import EMBEDDING_FUNCTION
    from vanna.integrations.chromadb import ChromaAgentMemory

    memory = ChromaAgentMemory(
        persist_directory=str(copy_path),
        collection_name="tool_memories",
        embedding_function=EMBEDDING_FUNCTION,
    )
    return memory, ChromaToolMemoryAdapter(memory, isolated_root=rehearsal_root)


def inventory_evidence(inventory: Any) -> dict[str, Any]:
    return {
        "store_count_before": inventory.store_count_before,
        "store_count_after": inventory.store_count_after,
        "classifications": dict(inventory.classifications),
        "issue_count": len(inventory.issues),
        "inventory_sha256": inventory.inventory_sha256,
    }


def preflight_evidence(preflight: Any) -> dict[str, Any]:
    return {
        "requested_record_ids": list(preflight.requested_record_ids),
        "controlled_existing_record_ids": sorted(preflight.controlled_existing_records),
        "absent_record_ids": list(preflight.absent_record_ids),
        "legacy_conflicts": dict(preflight.legacy_conflicts),
        "duplicate_content_conflicts": dict(preflight.duplicate_content_conflicts),
        "malformed_conflicts": list(preflight.malformed_conflicts),
        "store_count_before": preflight.store_count_before,
        "store_count_after": preflight.store_count_after,
        "executable": preflight.executable,
        "issues": [asdict(item) for item in preflight.issues],
        "store_preflight_sha256": preflight.store_preflight_sha256,
    }


def exact_record_checks(exact_results: tuple[Any, ...], plan: MemoryWritePlan) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for result, item in zip(exact_results, plan.items):
        record = result.record
        metadata = dict(record.metadata or {}) if record else {}
        checks.append(
            {
                "record_id": item.record_id,
                "status": result.status,
                "classification": record.classification if record else None,
                "canonical_content_equal": bool(
                    record and dict(record.canonical_content or {}) == item.canonical_content
                ),
                "memory_content_sha256_equal": bool(
                    record and record.memory_content_sha256 == item.memory_content_sha256
                ),
                "governance_metadata_equal": bool(
                    record
                    and all(metadata.get(key) == value for key, value in item.governance_metadata.items())
                ),
                "compatibility_metadata_equal": bool(
                    record and dict(record.compatibility_metadata or {}) == item.compatibility_metadata
                ),
            }
        )
    return {
        "records": checks,
        "all_exact": all(
            item["status"] == "found"
            and item["classification"] == "controlled_tool_record"
            and item["canonical_content_equal"]
            and item["memory_content_sha256_equal"]
            and item["governance_metadata_equal"]
            and item["compatibility_metadata_equal"]
            for item in checks
        ),
    }


def self_test() -> int:
    assert BATCH["expected_new_memory_count"] == len(BATCH["samples"]) == 2
    assert BATCH["training_batch_id"] == "level4-zero-b4-rehearsal-20260715-01"
    validation = validate_training_batch(BATCH, sql_guard=SQLGuard())
    assert validation.valid and validation.batch_content_sha256
    plan = build_memory_write_plan(
        BATCH,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    calls: list[str] = []

    class Result:
        def __init__(self, status: str) -> None:
            self.status = status

    results, observed = execute_failure_sequence(
        plan.items, lambda item: calls.append(item.record_id) or Result("created")
    )
    assert observed and calls == [plan.items[0].record_id] and len(results) == 1
    assert summarize_add_results([Result("created"), Result("created")]) == {
        "created_count": 2,
        "existing_same_count": 0,
        "failure_count": 0,
    }
    assert summarize_add_results([Result("existing_same"), Result("existing_same")]) == {
        "created_count": 0,
        "existing_same_count": 2,
        "failure_count": 0,
    }
    snapshots = {
        item.record_id: ExistingRecordSnapshot(
            record_id=item.record_id,
            canonical_content=item.canonical_content,
            memory_content_sha256=item.memory_content_sha256,
            created_by_training_batch_id=plan.training_batch_id,
            created_by_batch_content_sha256=plan.batch_content_sha256,
            created_from_sample_id=item.sample_id,
        )
        for item in plan.items
    }
    resume = build_memory_write_plan(
        BATCH,
        approved_batch_content_sha256=validation.batch_content_sha256,
        existing_records=snapshots,
        sql_guard=SQLGuard(),
    )
    assert resume.executable and resume.create_count == 0 and resume.resume_same_batch_count == 2
    try:
        assert_attempt_path(RUNTIME_SOURCE, BACKUP_PARENT)
        raise AssertionError("正式路径必须拒绝")
    except RuntimeError as error:
        assert str(error) == "FORMAL_PATH_REJECTED"
    original = os.environ.get("ZERO_B4_SELF_TEST")
    with restored_process_environment({"ZERO_B4_SELF_TEST": "temporary"}):
        assert os.environ["ZERO_B4_SELF_TEST"] == "temporary"
    assert os.environ.get("ZERO_B4_SELF_TEST") == original
    assert "agent_config" not in sys.modules
    print("SELF_TEST: PASS")
    return 0


def main() -> int:
    if parse_args().self_test:
        return self_test()

    user_env_before = get_user_environment("VANNA_DATA_DIR")
    runtime_before = manifest(RUNTIME_SOURCE)
    legacy_before = manifest(LEGACY_FORMAL)
    runtime_count_before = sqlite_record_count(RUNTIME_SOURCE)
    if runtime_count_before != EXPECTED_RUNTIME_RECORD_COUNT:
        raise RuntimeError("正式 Level 1 运行源记录数不是 187")
    if legacy_before["content_sha256"] != EXPECTED_LEGACY_SHA256:
        raise RuntimeError("仓库内旧正式 Chroma 摘要不匹配")

    validation = validate_training_batch(BATCH, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError("固定批次未通过真实 SQLGuard：" + json.dumps(validation.to_dict(), ensure_ascii=False))
    plan = build_memory_write_plan(
        BATCH,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    if not plan.executable or plan.create_count != 2:
        raise RuntimeError("首次写入计划不可执行")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rehearsal_root = BACKUP_PARENT / f"zero-b4-tool-memory-rehearsal-{timestamp}"
    evidence_dir = rehearsal_root / "evidence"
    failure_attempt = rehearsal_root / "failure-attempt"
    failure_data = failure_attempt / "vanna_data"
    success_attempt = rehearsal_root / "success-attempt"
    success_data = success_attempt / "vanna_data"
    evidence_dir.mkdir(parents=True)
    failure_attempt.mkdir()

    write_json(evidence_dir / "batch.json", BATCH)
    write_json(evidence_dir / "batch-validation.json", validation.to_dict())
    write_json(evidence_dir / "write-plan.json", plan.to_dict())
    write_json(
        evidence_dir / "source-before.json",
        {
            "runtime_source": runtime_before,
            "runtime_record_count": runtime_count_before,
            "legacy_formal": legacy_before,
            "user_vanna_data_dir_present": user_env_before[0],
            "user_vanna_data_dir": user_env_before[1],
        },
    )

    original_process_env = {
        name: os.environ.get(name)
        for name in ("VANNA_DATA_DIR", "HF_HUB_OFFLINE")
    }
    failure_result: dict[str, Any] = {}
    success_result: dict[str, Any] = {}
    repeat_result: dict[str, Any] = {}
    resume_result: dict[str, Any] = {}

    try:
        failure_copy = create_verified_copy(RUNTIME_SOURCE, failure_data, PROJECT_ROOT)
        memory = None
        with restored_process_environment(
            {"VANNA_DATA_DIR": str(failure_data), "HF_HUB_OFFLINE": "1"}
        ):
            memory, adapter = open_isolated_memory(failure_data, rehearsal_root)
            try:
                failure_inventory_before = adapter.inventory_tool_records()
                failure_preflight = adapter.inspect_plan_records(plan)
                add_results, failure_observed = execute_failure_sequence(
                    plan.items, lambda item: adapter.add_planned_record(plan, item)
                )
                exact = adapter.get_exact_records([item.record_id for item in plan.items])
                failure_inventory_after = adapter.inventory_tool_records()
                failure_result = {
                    "copy_sha256": failure_copy.destination.content_sha256,
                    "inventory_before": inventory_evidence(failure_inventory_before),
                    "preflight": preflight_evidence(failure_preflight),
                    "add_results": [asdict(item) for item in add_results],
                    "failure_injection_observed": failure_observed,
                    "first_record_status": exact[0].status,
                    "second_record_status": exact[1].status,
                    "final_count": failure_inventory_after.store_count_after,
                    "count_delta": failure_inventory_after.store_count_after
                    - failure_inventory_before.store_count_before,
                }
            finally:
                close_memory(memory)
                memory = None
        shutil.rmtree(failure_attempt)
        failure_result["copy_discarded"] = not failure_attempt.exists()
        write_json(evidence_dir / "failure-attempt.json", failure_result)

        success_attempt.mkdir()
        success_copy = create_verified_copy(RUNTIME_SOURCE, success_data, PROJECT_ROOT)
        with restored_process_environment(
            {"VANNA_DATA_DIR": str(success_data), "HF_HUB_OFFLINE": "1"}
        ):
            memory, adapter = open_isolated_memory(success_data, rehearsal_root)
            try:
                success_inventory_before = adapter.inventory_tool_records()
                success_preflight = adapter.inspect_plan_records(plan)
                add_results = [adapter.add_planned_record(plan, item) for item in plan.items]
                exact = adapter.get_exact_records([item.record_id for item in plan.items])
                exact_checks = exact_record_checks(exact, plan)
                success_inventory_after = adapter.inventory_tool_records()
                batch_records = adapter.list_records_by_batch_digest(plan.batch_content_sha256)
                add_summary = summarize_add_results(add_results)
                success_result = {
                    "copy_sha256": success_copy.destination.content_sha256,
                    "inventory_before": inventory_evidence(success_inventory_before),
                    "preflight": preflight_evidence(success_preflight),
                    "add_results": [asdict(item) for item in add_results],
                    "add_summary": add_summary,
                    "exact_record_checks": exact_checks,
                    "final_count": success_inventory_after.store_count_after,
                    "batch_record_count": len(batch_records.records),
                    "batch_query_issues": [asdict(item) for item in batch_records.issues],
                }

                repeat_adds = [adapter.add_planned_record(plan, item) for item in plan.items]
                repeat_inventory = adapter.inventory_tool_records()
                repeat_result = {
                    "add_results": [asdict(item) for item in repeat_adds],
                    "add_summary": summarize_add_results(repeat_adds),
                    "final_count": repeat_inventory.store_count_after,
                }

                resume_preflight = adapter.inspect_plan_records(plan)
                resume_plan = build_memory_write_plan(
                    BATCH,
                    approved_batch_content_sha256=validation.batch_content_sha256,
                    existing_records=dict(resume_preflight.controlled_existing_records),
                    sql_guard=SQLGuard(),
                )
                resume_result = {
                    "preflight": preflight_evidence(resume_preflight),
                    "plan": resume_plan.to_dict(),
                }
            finally:
                close_memory(memory)
                memory = None
    finally:
        for name, value in original_process_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    write_json(evidence_dir / "success-attempt.json", success_result)
    write_json(evidence_dir / "repeat-attempt.json", repeat_result)
    write_json(evidence_dir / "resume-plan.json", resume_result)

    runtime_after = manifest(RUNTIME_SOURCE)
    legacy_after = manifest(LEGACY_FORMAL)
    runtime_count_after = sqlite_record_count(RUNTIME_SOURCE)
    user_env_after = get_user_environment("VANNA_DATA_DIR")
    source_after = {
        "runtime_source": runtime_after,
        "runtime_record_count": runtime_count_after,
        "legacy_formal": legacy_after,
        "user_vanna_data_dir_present": user_env_after[0],
        "user_vanna_data_dir": user_env_after[1],
    }
    write_json(evidence_dir / "source-after.json", source_after)

    accepted = all(
        (
            validation.valid,
            plan.executable,
            plan.create_count == 2,
            failure_result.get("failure_injection_observed") is True,
            failure_result.get("first_record_status") == "found",
            failure_result.get("second_record_status") == "missing",
            failure_result.get("count_delta") == 1,
            failure_result.get("copy_discarded") is True,
            success_result.get("inventory_before", {}).get("store_count_before") == 187,
            success_result.get("add_summary", {}).get("created_count") == 2,
            success_result.get("add_summary", {}).get("failure_count") == 0,
            success_result.get("final_count") == 189,
            success_result.get("exact_record_checks", {}).get("all_exact") is True,
            success_result.get("batch_record_count") == 2,
            not success_result.get("batch_query_issues"),
            repeat_result.get("add_summary", {}).get("existing_same_count") == 2,
            repeat_result.get("add_summary", {}).get("created_count") == 0,
            repeat_result.get("final_count") == 189,
            resume_result.get("plan", {}).get("executable") is True,
            resume_result.get("plan", {}).get("create_count") == 0,
            resume_result.get("plan", {}).get("resume_same_batch_count") == 2,
            resume_result.get("plan", {}).get("conflict_count") == 0,
            runtime_before["content_sha256"] == runtime_after["content_sha256"],
            runtime_count_before == runtime_count_after == 187,
            legacy_before["content_sha256"] == legacy_after["content_sha256"],
            user_env_before == user_env_after,
        )
    )
    summary = {
        "zero_b4_accepted": accepted,
        "rehearsal_root": str(rehearsal_root),
        "evidence_directory": str(evidence_dir),
        "runtime_source_path": str(RUNTIME_SOURCE),
        "runtime_source_record_count_before": runtime_count_before,
        "runtime_source_record_count_after": runtime_count_after,
        "runtime_source_sha256_before": runtime_before["content_sha256"],
        "runtime_source_sha256_after": runtime_after["content_sha256"],
        "legacy_formal_sha256_before": legacy_before["content_sha256"],
        "legacy_formal_sha256_after": legacy_after["content_sha256"],
        "user_vanna_data_dir_before": user_env_before[1],
        "user_vanna_data_dir_after": user_env_after[1],
        "batch_content_sha256": validation.batch_content_sha256,
        "write_plan_sha256": plan.write_plan_sha256,
        "batch_valid": validation.valid,
        "plan_executable": plan.executable,
        "plan_create_count": plan.create_count,
        "failure": failure_result,
        "success": success_result,
        "repeat": repeat_result,
        "resume": resume_result,
        "database_connected": False,
        "sql_executed": False,
        "deepseek_called": False,
        "formal_memory_write_executed": False,
        "formal_memory_delete_executed": False,
    }
    write_json(evidence_dir / "zero-b4-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())

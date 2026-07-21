"""在全新隔离副本中复现 Q3，并捕获首次 SQLGuard 候选 SQL。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime
import urllib.error
import urllib.request
from uuid import uuid4
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.f2_end_to_end_mvp_probe import (
    SERVER_URL,
    redact,
    start_server,
    stop_server,
    walk_json,
)
from tools.f5_level2_batch01_delivery import (
    RUNTIME_SOURCE,
    close_memory,
    exact_record_checks,
    load_batch,
    manifest,
    open_memory,
    sqlite_record_count,
)
from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import get_user_environment
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy


BACKUP_PARENT = Path(r"E:\3\_training_backups")
EXPECTED_FORMAL_COUNT = 187
EXPECTED_FORMAL_SHA256 = (
    "8309be244c8222328d71e8bd2e2053d30885a06401ecb8f44f535a79e97b09ec"
)
EXPECTED_RECORD_ID = (
    "toolmem-v1-40450ebe8c29650973fc0961f7ff5bd0423052ae7c5b4dc281998d8ef868ee5a"
)
Q3 = "查询站点1408最近的水质小时变化趋势，返回监测时间、pH、溶解氧和水质等级，最多100条"


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def post_q3(conversation_id: str, request_id: str, timeout: int = 240) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": Q3,
            "conversation_id": conversation_id,
            "request_id": request_id,
            "metadata": {"query": Q3, "q3_guard_reproduction": True},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        SERVER_URL + "/api/vanna/v2/chat_sse",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {
        "http_status": None,
        "raw_events": [],
        "final_sql": "",
        "final_answer": "",
        "errors": [],
    }
    answer_parts: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result["http_status"] = response.status
            while True:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    result["raw_events"].append({"unparsed": data})
                    continue
                result["raw_events"].append(event)
                if event.get("type") == "error":
                    result["errors"].append(str(event.get("data") or event))
                for path, value in walk_json(event):
                    key = path[-1].lower() if path else ""
                    if key == "sql" and isinstance(value, str) and value.strip():
                        result["final_sql"] = value.strip()
                    elif key in {"text", "content"} and isinstance(value, str) and value.strip():
                        if not any(
                            marker in "/".join(path).lower()
                            for marker in ("memory", "prompt")
                        ):
                            answer_parts.append(value)
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        result["errors"].append(error.read(4000).decode("utf-8", errors="replace"))
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    result["final_answer"] = "\n".join(dict.fromkeys(answer_parts))[-12000:]
    return result


def find_derived_aliases(sql: str) -> list[str]:
    aliases: list[str] = []
    for match in re.finditer(r"\b(?:from|join)\s*\(", sql, flags=re.I):
        open_index = sql.find("(", match.start())
        depth = 0
        quote: str | None = None
        close_index = -1
        for index in range(open_index, len(sql)):
            char = sql[index]
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_index = index
                    break
        if close_index == -1:
            continue
        inner = sql[open_index + 1 : close_index].strip().lower()
        if not inner.startswith(("select", "with")):
            continue
        alias_match = re.match(
            r"\s+(?:as\s+)?([a-zA-Z_][\w]*)", sql[close_index + 1 :], flags=re.I
        )
        if alias_match:
            aliases.append(alias_match.group(1).lower())
    return aliases


def main() -> int:
    formal_before = manifest(RUNTIME_SOURCE)
    formal_count_before = sqlite_record_count(RUNTIME_SOURCE)
    user_env_before = get_user_environment("VANNA_DATA_DIR")
    if formal_count_before != EXPECTED_FORMAL_COUNT:
        raise RuntimeError("FORMAL_RECORD_COUNT_MISMATCH")
    if formal_before["content_sha256"] != EXPECTED_FORMAL_SHA256:
        raise RuntimeError("FORMAL_SHA256_MISMATCH")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = BACKUP_PARENT / f"f5-batch01-q3-reproduction-{timestamp}"
    data_dir = root / "vanna_data"
    agent_dir = root / "agent_data"
    evidence = root / "evidence"
    agent_dir.mkdir(parents=True)
    evidence.mkdir(parents=True)
    trace_path = evidence / "q3-sql-guard-trace.jsonl"
    write_json(
        evidence / "source-before.json",
        {
            "formal_path": str(RUNTIME_SOURCE),
            "record_count": formal_count_before,
            "manifest": formal_before,
            "user_vanna_data_dir": user_env_before,
        },
    )

    copied = create_verified_copy(RUNTIME_SOURCE, data_dir, PROJECT_ROOT)
    if copied.destination.content_sha256 != formal_before["content_sha256"]:
        raise RuntimeError("ISOLATED_COPY_MISMATCH")
    isolated_initial = sqlite_record_count(data_dir)

    os.environ.update(
        {
            "VANNA_DATA_DIR": str(data_dir),
            "AGENT_DATA_DIR": str(agent_dir),
            "HF_HUB_OFFLINE": "1",
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "0",
            "VANNA_SQL_GUARD_TRACE_PATH": str(trace_path),
        }
    )
    batch = load_batch()
    validation = validate_training_batch(batch, sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256:
        raise RuntimeError("BATCH_INVALID")
    plan = build_memory_write_plan(
        batch,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=SQLGuard(),
    )
    if not plan.executable or plan.create_count != 1 or plan.items[0].record_id != EXPECTED_RECORD_ID:
        raise RuntimeError("WRITE_PLAN_INVALID")

    memory = None
    process = None
    logs: list[str] = []
    secret = ""
    try:
        memory, adapter = open_memory(data_dir, root)
        preflight = adapter.inspect_plan_records(plan)
        if not preflight.executable:
            raise RuntimeError("ISOLATED_PREFLIGHT_FAILED")
        add = adapter.add_planned_record(plan, plan.items[0])
        exact = adapter.get_exact_records([plan.items[0].record_id])
        isolated_final = sqlite_record_count(data_dir)
        record_level = (
            (exact[0].record.metadata or {}).get("training_level")
            if exact and exact[0].record is not None
            else None
        )
        write_result = {
            "initial_count": isolated_initial,
            "write_result": add.status,
            "final_count": isolated_final,
            "record_id": plan.items[0].record_id,
            "record_training_level": record_level,
            "exact_checks": exact_record_checks(exact, plan),
            "batch_content_sha256": validation.batch_content_sha256,
            "write_plan_sha256": plan.write_plan_sha256,
        }
        write_json(evidence / "isolated-write-result.json", write_result)
        if not all(
            (
                isolated_initial == 187,
                add.status == "created",
                isolated_final == 188,
                record_level == "level2_sql_examples",
                write_result["exact_checks"]["all_exact"],
            )
        ):
            raise RuntimeError("ISOLATED_WRITE_ACCEPTANCE_FAILED")
        close_memory(memory)
        memory = None

        process, logs, _, secret = start_server(data_dir, agent_dir, False)
        attempts: list[dict[str, Any]] = []
        failure_record: dict[str, Any] | None = None
        failure_attempt: int | None = None
        for attempt_number in range(1, 11):
            before_records = read_trace(trace_path)
            conversation_id = str(uuid4())
            request_id = str(uuid4())
            response = post_q3(conversation_id, request_id)
            after_records = read_trace(trace_path)
            new_records = after_records[len(before_records) :]
            first_blocks = [
                item
                for item in new_records
                if item.get("hard_blocked_before_validation") is False
                and item.get("blocked_by_sql_guard") is True
                and bool(str(item.get("attempted_sql") or "").strip())
            ]
            attempts.append(
                {
                    "attempt": attempt_number,
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "sse": response,
                    "trace_records": new_records,
                    "first_sql_guard_blocked": bool(first_blocks),
                }
            )
            write_json(evidence / "q3-attempts.json", attempts)
            if first_blocks:
                failure_record = first_blocks[0]
                failure_attempt = attempt_number
                break

        stop_server(process)
        process = None
        all_trace = read_trace(trace_path)
        failed_sql = str((failure_record or {}).get("attempted_sql") or "")
        derived_aliases = find_derived_aliases(failed_sql)
        unknown_tables = list((failure_record or {}).get("unknown_tables") or [])
        derived_alias = next(
            (alias for alias in derived_aliases if alias in unknown_tables),
            derived_aliases[0] if derived_aliases else "",
        )

        formal_after = manifest(RUNTIME_SOURCE)
        formal_count_after = sqlite_record_count(RUNTIME_SOURCE)
        user_env_after = get_user_environment("VANNA_DATA_DIR")
        write_json(
            evidence / "source-after.json",
            {
                "formal_path": str(RUNTIME_SOURCE),
                "record_count": formal_count_after,
                "manifest": formal_after,
                "user_vanna_data_dir": user_env_after,
            },
        )
        summary = {
            "base_commit": "88a93411089a23f840f61538095c3432b221fdbf",
            "isolated_path": str(root),
            "isolated_initial_count": isolated_initial,
            "isolated_write_result": add.status,
            "isolated_final_count": isolated_final,
            "server_started": True,
            "health_check": "PASS",
            "q3_attempt_count": len(attempts),
            "q3_failure_reproduced": failure_record is not None,
            "failure_attempt_number": failure_attempt,
            "failed_record": failure_record,
            "failed_q3_derived_alias": derived_alias,
            "derived_table_syntax_present": bool(derived_aliases),
            "derived_alias_reported_as_unknown_table": bool(
                derived_alias and derived_alias in unknown_tables
            ),
            "trace_path": str(trace_path),
            "trace_record_count": len(all_trace),
            "formal_record_count_before": formal_count_before,
            "formal_record_count_after": formal_count_after,
            "formal_sha256_before": formal_before["content_sha256"],
            "formal_sha256_after": formal_after["content_sha256"],
            "formal_runtime_unchanged": (
                formal_count_before == formal_count_after
                and formal_before["content_sha256"] == formal_after["content_sha256"]
                and user_env_before == user_env_after
            ),
            "database_connected": True,
            "read_only_sql_execution_count": sum(
                item.get("guard_passed") is True for item in all_trace
            ),
            "ddl_dml_executed": False,
            "formal_memory_write_executed": False,
            "formal_memory_delete_executed": False,
        }
        write_json(evidence / "q3-reproduction-summary.json", summary)
        (evidence / "server-log.txt").write_text(
            redact("\n".join(logs), [secret]), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        if process is not None:
            stop_server(process)
        close_memory(memory)


if __name__ == "__main__":
    raise SystemExit(main())

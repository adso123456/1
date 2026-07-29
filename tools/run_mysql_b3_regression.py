"""通过正式 Agent/SSE 执行 mysql-lzh-monitor 固定自然语言回归。"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.mysql_sql_guard import MySQLSQLGuard
from tools.regression_service_harness import (
    extract_chart_specs,
    extract_final_text,
    parse_dataframe_event,
    parse_sse_text,
)


SERVER_URL = "http://127.0.0.1:8000"
SOURCE_ID = "mysql-lzh-monitor"
SUITE_PATH = (
    PROJECT_ROOT / "training" / "mysql_lzh_monitor" / "regression_suite.json"
)
RESULT_PATH = (
    PROJECT_ROOT / "training" / "mysql_lzh_monitor" / "regression_result.json"
)
METADATA_PATH = (
    PROJECT_ROOT / "agent_data" / SOURCE_ID / "column_metadata_index.json"
)


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def _start_server() -> tuple[subprocess.Popen[str], list[str]]:
    if _port_open():
        raise RuntimeError("8000 端口已占用，无法证明本分支服务")
    required = (
        "DEEPSEEK_API_KEY",
        "DB_USER",
        "DB_PASSWORD",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(f"缺少服务环境变量：{missing}")
    env = os.environ.copy()
    env.update(
        {
            "MYSQL_HOST": env.get("MYSQL_HOST", "127.0.0.1"),
            "MYSQL_PORT": env.get("MYSQL_PORT", "3307"),
            "MYSQL_DATABASE": env.get("MYSQL_DATABASE", "lzh_monitor"),
            "MYSQL_VANNA_DATA_DIR": str(
                PROJECT_ROOT / "vanna_data" / SOURCE_ID
            ),
            "HF_HUB_OFFLINE": "1",
            "PYTHONUNBUFFERED": "1",
            "VANNA_REQUEST_TRACE_ENABLED": "1",
            "VANNA_REQUEST_TRACE_DIR": str(
                Path(r"E:\3\_runtime\mysql-lzh-monitor-sse-traces")
            ),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "step4_server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    logs: list[str] = []

    def _collect() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=_collect, daemon=True).start()
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务提前退出：\n" + "\n".join(logs[-30:]))
        try:
            with urllib.request.urlopen(SERVER_URL + "/health", timeout=3) as response:
                if response.status == 200:
                    return process, logs
        except Exception:
            time.sleep(2)
    _stop_server(process)
    raise RuntimeError("等待服务健康检查超时")


def _stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _get_json(path: str) -> Any:
    with urllib.request.urlopen(SERVER_URL + path, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_sse(
    question: str,
    conversation_id: str,
    source_id: str = SOURCE_ID,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": question,
            "conversation_id": conversation_id,
            "request_id": str(uuid.uuid4()),
            "metadata": {"source_id": source_id, "query": question},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        SERVER_URL + "/api/vanna/v2/chat_sse",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=360) as response:
        chunks: list[bytes] = []
        while True:
            line = response.readline()
            if not line:
                break
            chunks.append(line)
            if line.startswith(b"data:") and line.strip() != b"data: [DONE]":
                print("SSE_EVENT_RECEIVED", flush=True)
            if line.strip() == b"data: [DONE]":
                break
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        status = response.status
    events, parse_errors = parse_sse_text(raw)
    dataframe_events = [
        item
        for sequence, event in enumerate(events, start=1)
        if (item := parse_dataframe_event(event, sequence)) is not None
    ]
    errors = list(parse_errors)
    errors.extend(
        str(event.get("data") or event)
        for event in events
        if event.get("type") == "error"
    )
    final_text = extract_final_text(events)
    return {
        "http_status": status,
        "event_count": len(events),
        "errors": errors,
        "dataframes": dataframe_events,
        "final_text": final_text,
        "chart_specs": extract_chart_specs(final_text),
    }


def _chart_fields(spec: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for name in ("xField", "seriesField", "sizeField", "valueField"):
        value = spec.get(name)
        if isinstance(value, str) and value:
            fields.append(value)
    y_fields = spec.get("yFields")
    if isinstance(y_fields, list):
        fields.extend(value for value in y_fields if isinstance(value, str))
    return fields


def main() -> int:
    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    approved_tables = {
        row["table"]
        for row in json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    }
    guard = MySQLSQLGuard(METADATA_PATH)
    process: subprocess.Popen[str] | None = None
    case_results: list[dict[str, Any]] = []
    conversations: dict[str, str] = {}
    try:
        process, logs = _start_server()
        sources = _get_json("/api/data-sources")
        source_ids = {item["source_id"] for item in sources}
        source_list_passed = {
            "postgresql-main",
            "mysql-lzh-monitor",
        }.issubset(source_ids)
        print(
            f"{'PASS' if source_list_passed else 'FAIL'}: DATA_SOURCE_LIST | "
            f"{sorted(source_ids)}",
            flush=True,
        )

        max_cases = int(os.getenv("MYSQL_B3_MAX_CASES", "0") or "0")
        selected_cases = (
            suite["cases"][:max_cases] if max_cases > 0 else suite["cases"]
        )
        for case in selected_cases:
            dependency = case.get("follow_up_to") or case.get("repeat_of")
            if dependency:
                conversation_id = conversations[dependency]
            else:
                conversation_id = str(uuid.uuid4())
            conversations[case["case_id"]] = conversation_id
            started = time.perf_counter()
            response = _post_sse(case["question"], conversation_id)
            duration_ms = round((time.perf_counter() - started) * 1000)
            frames = response["dataframes"]
            last_frame = frames[-1] if frames else {}
            sql = str(last_frame.get("sql") or "")
            columns = list(last_frame.get("columns") or [])
            row_count = int(last_frame.get("row_count") or 0)
            guard_result = guard.validate(sql, query=case["question"]) if sql else None
            used_tables = sorted(guard_result.used_tables) if guard_result else []
            chart_specs = response["chart_specs"]
            chart_types = [spec.get("type") for spec in chart_specs]
            chart_fields_valid = all(
                field in columns
                for spec in chart_specs
                for field in _chart_fields(spec)
            )
            expected_chart_types = case["chart_types"]
            chart_expected = (
                any(chart in expected_chart_types for chart in chart_types)
                if expected_chart_types != ["none"]
                else any(chart == "none" for chart in chart_types)
            )
            minimum_chart_count = case.get("minimum_chart_count", 1)
            normalized_sql = " ".join(sql.lower().split())
            expected_sql_fragments = [
                fragment.lower() for fragment in case.get("expected_sql_contains", [])
            ]
            forbidden_sql_fragments = [
                fragment.lower() for fragment in case.get("forbidden_sql_contains", [])
            ]
            checks = {
                "http_ok": response["http_status"] == 200,
                "no_sse_error": not response["errors"],
                "sql_executed": bool(frames)
                and all(frame["execution_success"] for frame in frames),
                "result_nonempty": case["allow_empty"] or row_count > 0,
                "guard_passed": bool(guard_result and guard_result.passed),
                "approved_tables_only": set(used_tables).issubset(approved_tables),
                "expected_tables": set(case["expected_tables"]).issubset(used_tables),
                "forbidden_tables": not set(case["forbidden_tables"]).intersection(
                    used_tables
                ),
                "expected_columns": set(case["expected_columns"]).issubset(columns),
                "expected_sql": all(
                    fragment in normalized_sql
                    for fragment in expected_sql_fragments
                ),
                "forbidden_sql": not any(
                    fragment in normalized_sql
                    for fragment in forbidden_sql_fragments
                ),
                "answer_present": bool(response["final_text"]),
                "chart_expected": chart_expected,
                "chart_fields_valid": chart_fields_valid,
                "chart_count": len(chart_specs) >= minimum_chart_count,
                "source_id_bound": bool(conversation_id),
            }
            passed = all(checks.values())
            result = {
                "case_id": case["case_id"],
                "question": case["question"],
                "source_id": SOURCE_ID,
                "conversation_id": conversation_id,
                "duration_ms": duration_ms,
                "passed": passed,
                "checks": checks,
                "sql": sql,
                "used_tables": used_tables,
                "columns": columns,
                "row_count": row_count,
                "chart_specs": chart_specs,
                "errors": response["errors"],
            }
            case_results.append(result)
            print(
                f"{'PASS' if passed else 'FAIL'}: {case['case_id']} | "
                f"{duration_ms}ms rows={row_count} tables={used_tables} "
                f"columns={columns} charts={chart_types}",
                flush=True,
            )
            if not passed:
                print(json.dumps(checks, ensure_ascii=False), flush=True)
                if response["errors"]:
                    print(
                        json.dumps(response["errors"], ensure_ascii=False),
                        flush=True,
                    )

        repeat = next(
            (item for item in case_results if item["case_id"] == "B3_WQ_REPEAT"),
            None,
        )
        original = next(
            (item for item in case_results if item["case_id"] == "B3_WQ_01"),
            None,
        )
        repeated_execution = (
            None
            if repeat is None or original is None
            else (
                repeat["passed"]
                and original["passed"]
                and repeat["conversation_id"] == original["conversation_id"]
                and repeat["row_count"] > 0
            )
        )
        isolation_sql = all(
            "rs_outlet_monitor_v2" not in item["sql"].lower()
            for item in case_results
        )
        summary = {
            "suite_id": suite["suite_id"],
            "source_id": SOURCE_ID,
            "data_source_list": sorted(source_ids),
            "data_source_list_passed": source_list_passed,
            "case_count": len(case_results),
            "passed_count": sum(item["passed"] for item in case_results),
            "failed_count": sum(not item["passed"] for item in case_results),
            "repeat_reexecuted": repeated_execution,
            "postgresql_table_absent_from_mysql_sql": isolation_sql,
            "cases": case_results,
        }
        RESULT_PATH.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        passed = (
            source_list_passed
            and not summary["failed_count"]
            and (repeated_execution is not False)
            and isolation_sql
        )
        print(
            f"TOTAL={len(case_results)} PASS={summary['passed_count']} "
            f"FAIL={summary['failed_count']} REPEAT={repeated_execution} "
            f"ISOLATION={isolation_sql}"
        )
        return 0 if passed else 1
    finally:
        _stop_server(process)


if __name__ == "__main__":
    raise SystemExit(main())

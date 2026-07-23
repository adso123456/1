from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"


def query_result_files(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(item.relative_to(path)) for item in path.rglob("query_results_*.csv")}


def port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def get_deepseek_key() -> tuple[str, str]:
    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value, "process environment"
    if os.name == "nt":
        import winreg

        locations = [
            (winreg.HKEY_CURRENT_USER, "Environment", "user environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                "machine environment",
            ),
        ]
        for hive, subkey, source in locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")
                if str(value).strip():
                    return str(value).strip(), source
            except OSError:
                pass
    return "", "not found"


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    return text


def start_server(
    data_dir: Path, agent_dir: Path, disable_examples: bool
) -> tuple[subprocess.Popen[str], list[str], str, str]:
    if port_open():
        raise RuntimeError("端口 8000 已被占用，无法保证隔离运行")
    key, key_source = get_deepseek_key()
    if not key:
        raise RuntimeError("既有环境中没有 DEEPSEEK_API_KEY")
    for name in ("DB_USER", "DB_PASSWORD"):
        if not os.getenv(name, "").strip():
            raise RuntimeError(f"当前子进程环境缺少 {name}")

    env = os.environ.copy()
    env.update(
        {
            "DEEPSEEK_API_KEY": key,
            "VANNA_DATA_DIR": str(data_dir),
            "AGENT_DATA_DIR": str(agent_dir),
            "VANNA_DISABLE_LEGACY_SQL_EXAMPLES": "1" if disable_examples else "0",
            "HF_HUB_OFFLINE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    process = subprocess.Popen(
        [str(PYTHON_EXE), "step4_server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    logs: list[str] = []

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务提前退出：\n" + "\n".join(logs[-50:]))
        try:
            with urllib.request.urlopen(SERVER_URL + "/health", timeout=3) as response:
                if response.status == 200:
                    return process, logs, key_source, key
        except Exception:
            pass
        time.sleep(2)
    stop_server(process)
    raise RuntimeError("等待 /health 超时")


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def walk_json(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    found = [(path, value)]
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(walk_json(child, path + (str(key),)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(walk_json(child, path + (str(index),)))
    return found


def parse_dataframe_event(
    event: dict[str, Any], sequence: int
) -> dict[str, Any] | None:
    """从单个 SSE 事件提取结构化 DataFrame 执行结果。"""
    rich = event.get("rich")
    if not isinstance(rich, dict) or rich.get("type") != "dataframe":
        return None
    data = rich.get("data")
    if not isinstance(data, dict):
        data = {}
    rows = data.get("data")
    if not isinstance(rows, list):
        rows = data.get("rows")
    if not isinstance(rows, list):
        rows = []
    columns = data.get("columns")
    if not isinstance(columns, list):
        columns = []
    row_count = data.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool):
        row_count = len(rows)
    return {
        "sequence": sequence,
        "sql": str(data.get("sql") or "").strip(),
        "columns": columns,
        "rows": rows,
        "row_count": row_count,
        "description": str(data.get("description") or ""),
        "execution_success": data.get("execution_success") is True,
        "output_file": str(data.get("output_file") or ""),
    }


def parse_sse_text(raw_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError as error:
            errors.append(f"JSONDecodeError: {error}")
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, errors


def extract_final_text(events: list[dict[str, Any]]) -> str:
    """提取最后一个 rich text 事件，避免把 DataFrame 简述当最终回答。"""
    for event in reversed(events):
        rich = event.get("rich")
        if not isinstance(rich, dict) or rich.get("type") != "text":
            continue
        data = rich.get("data")
        if isinstance(data, str) and data.strip():
            return data.strip()
        if isinstance(data, dict):
            for key in ("content", "text", "value"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        simple = event.get("simple")
        if isinstance(simple, dict):
            value = simple.get("text") or simple.get("content")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def persist_sse_evidence(
    raw_evidence_dir: Path,
    *,
    raw_text: str,
    http_status: int | None,
    events: list[dict[str, Any]],
    errors: list[str],
) -> None:
    """保存已收到的原始 SSE 与解析结果。调用方须先写 response.sse。"""
    raw_evidence_dir.mkdir(parents=True, exist_ok=True)
    (raw_evidence_dir / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    final_text = extract_final_text(events)
    (raw_evidence_dir / "final-text.txt").write_text(final_text, encoding="utf-8")
    dataframe_count = sum(
        1
        for event in events
        if isinstance(event.get("rich"), dict)
        and event["rich"].get("type") == "dataframe"
    )
    conversation_id = next(
        (str(event["conversation_id"]) for event in events if event.get("conversation_id")),
        "",
    )
    request_id = next(
        (str(event["request_id"]) for event in events if event.get("request_id")),
        "",
    )
    summary = {
        "http_status": http_status,
        "sse_errors": errors,
        "event_count": len(events),
        "dataframe_event_count": dataframe_count,
        "final_text_present": bool(final_text),
        "conversation_id": conversation_id,
        "request_id": request_id,
    }
    (raw_evidence_dir / "request-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def post_sse(
    query: str, timeout: int = 240, raw_evidence_dir: Path | None = None
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": query,
            "conversation_id": None,
            "request_id": None,
            "metadata": {"query": query, "f2_probe": True},
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
        "event_count": 0,
        "errors": [],
        "sql": "",
        "answer": "",
        "rich_types": [],
        "event_rows": [],
        "event_columns": [],
        "dataframe_events": [],
    }
    answer_parts: list[str] = []
    raw_text = ""
    events: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result["http_status"] = response.status
            raw_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        raw_text = exc.read().decode("utf-8", errors="replace")
        result["errors"].append(raw_text[:2000])
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if raw_evidence_dir is not None:
            try:
                raw_evidence_dir.mkdir(parents=True, exist_ok=True)
                (raw_evidence_dir / "response.sse").write_text(
                    raw_text, encoding="utf-8"
                )
            except Exception:
                pass

    events, parse_errors = parse_sse_text(raw_text)
    result["errors"].extend(parse_errors)
    for event in events:
        result["event_count"] += 1
        if event.get("type") == "error":
            result["errors"].append(str(event.get("data") or event))
        dataframe_event = parse_dataframe_event(
            event, len(result["dataframe_events"]) + 1
        )
        if dataframe_event is not None:
            result["dataframe_events"].append(dataframe_event)
        for path, value in walk_json(event):
            key = path[-1].lower() if path else ""
            if key == "sql" and isinstance(value, str) and value.strip():
                result["sql"] = value.strip()
            elif key in {"type", "component_type"} and isinstance(value, str):
                if "rich" in "/".join(path).lower() or value in {
                    "dataframe",
                    "chart",
                    "plotly",
                }:
                    result["rich_types"].append(value)
            elif key == "rows" and isinstance(value, list) and value:
                result["event_rows"] = value
            elif key == "columns" and isinstance(value, list) and value:
                result["event_columns"] = value
            elif key in {"text", "content"} and isinstance(value, str) and value.strip():
                if not any(
                    marker in "/".join(path).lower()
                    for marker in ("memory", "prompt")
                ):
                    answer_parts.append(value)

    if raw_evidence_dir is not None:
        try:
            persist_sse_evidence(
                raw_evidence_dir,
                raw_text=raw_text,
                http_status=result["http_status"],
                events=events,
                errors=result["errors"],
            )
        except Exception:
            pass
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["answer"] = "\n".join(dict.fromkeys(answer_parts))[-12000:]
    if result["dataframe_events"]:
        first = result["dataframe_events"][0]
        result["sql"] = first["sql"]
        result["event_columns"] = first["columns"]
        result["event_rows"] = first["rows"]
    return result


def extract_chart_specs(answer: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for raw in re.findall(r"<!--\s*chart_spec:\s*([\s\S]*?)\s*-->", answer, flags=re.I):
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                specs.append(item)
        except json.JSONDecodeError:
            specs.append({"parse_error": raw[:200]})
    return specs


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return [], []
    return rows[0], [row for row in rows[1:] if any(value.strip() for value in row)]


def run_memory_regression(
    data_dir: Path,
    memory_cases: list[dict[str, Any]],
    evidence_dir: Path,
) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise RuntimeError(f"隔离 Memory 目录不存在: {data_dir}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="memory-regression-", dir=evidence_dir
    ) as temp_name:
        temp_dir = Path(temp_name)
        cases_file = temp_dir / "cases.json"
        output_file = temp_dir / "result.json"
        cases_file.write_text(
            json.dumps(memory_cases, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        env = os.environ.copy()
        env["VANNA_DATA_DIR"] = str(data_dir)
        env["HF_HUB_OFFLINE"] = "1"
        completed = subprocess.run(
            [
                str(PYTHON_EXE),
                str(Path(__file__).resolve()),
                "--memory-worker",
                "--data-dir",
                str(data_dir),
                "--cases-file",
                str(cases_file),
                "--output-file",
                str(output_file),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if not output_file.is_file():
            raise RuntimeError(
                "Memory Worker 未生成结果"
                + (f": {completed.stdout[-1000:]}" if completed.stdout else "")
            )
        result = json.loads(output_file.read_text(encoding="utf-8"))
        (evidence_dir / "memory-regression-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Memory Worker 失败，退出码={completed.returncode}: "
                + json.dumps(result, ensure_ascii=False)
            )
        return result


async def _memory_worker(
    data_dir: Path, cases_file: Path, output_file: Path
) -> int:
    data_dir = data_dir.resolve()
    os.environ["VANNA_DATA_DIR"] = str(data_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"
    if Path(os.environ["VANNA_DATA_DIR"]).resolve() != data_dir:
        raise RuntimeError("MEMORY_WORKER_DATA_DIR_ASSERTION_FAILED")

    from backend.memory import create_memory
    from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
    from backend.sql_guard import SQLGuard
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    memory_cases = json.loads(cases_file.read_text(encoding="utf-8"))
    memory = create_memory()
    context = ToolContext(
        user=User(id="longterm_memory_regression", username="longterm_memory_regression"),
        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=memory,
        metadata={"stage": "longterm_memory_regression"},
    )
    results: list[dict[str, Any]] = []
    for case in memory_cases:
        question = str(case["question"])
        expected_sample_id = str(case["expected_sample_id"])
        found = await memory.search_similar_usage(
            question=question,
            context=context,
            limit=int(case["search_top_k"]),
            similarity_threshold=0.0,
            tool_name_filter="run_sql",
        )
        recalled_sample_ids = [
            str((item.memory.metadata or {}).get("sample_id", ""))
            for item in found
            if (item.memory.metadata or {}).get("sample_id")
        ]
        target = next(
            (
                item
                for item in found
                if str((item.memory.metadata or {}).get("sample_id", ""))
                == expected_sample_id
            ),
            None,
        )
        target_metadata = target.memory.metadata or {} if target else {}
        enhancer = SqlExampleContextEnhancer(
            memory=memory,
            sql_guard=SQLGuard(),
            top_k=int(case["enhancer_top_k"]),
        )
        examples = await enhancer._retrieve_examples(question)
        injected_sample_ids = [str(item.get("sample_id", "")) for item in examples]
        target_recalled = target is not None
        target_rank = int(target.rank) if target else None
        target_injected = expected_sample_id in injected_sample_ids
        observed_training_level = str(target_metadata.get("training_level", ""))
        observed_train_decision = str(target_metadata.get("train_decision", ""))
        failure_reasons: list[str] = []
        if case["expectation"] == "present_and_injected":
            if not target_recalled:
                failure_reasons.append("target_not_recalled")
            if target_rank is None or target_rank > int(case["max_rank"]):
                failure_reasons.append("target_rank_exceeded")
            if not target_injected:
                failure_reasons.append("target_not_injected")
            if observed_training_level != case["expected_training_level"]:
                failure_reasons.append("training_level_mismatch")
            if observed_train_decision != "approved":
                failure_reasons.append("train_decision_not_approved")
        else:
            if target_recalled:
                failure_reasons.append("absent_target_recalled")
            if target_injected:
                failure_reasons.append("absent_target_injected")
        results.append(
            {
                "case_id": case["case_id"],
                "expected_sample_id": expected_sample_id,
                "expectation": case["expectation"],
                "target_rank": target_rank,
                "target_recalled": target_recalled,
                "target_injected": target_injected,
                "recalled_sample_ids": recalled_sample_ids,
                "injected_sample_ids": injected_sample_ids,
                "observed_training_level": observed_training_level,
                "observed_train_decision": observed_train_decision,
                "passed": not failure_reasons,
                "failure_reasons": failure_reasons,
            }
        )
    payload = {
        "memory_case_count": len(results),
        "memory_pass_count": sum(int(item["passed"]) for item in results),
        "accepted": all(item["passed"] for item in results),
        "cases": results,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if payload["accepted"] else 2


def _parse_worker_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-worker", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--cases-file", type=Path)
    parser.add_argument("--output-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_worker_args()
    if not args.memory_worker or not args.data_dir or not args.cases_file or not args.output_file:
        raise RuntimeError("仅支持完整的 --memory-worker 调用")
    return asyncio.run(
        _memory_worker(
            args.data_dir.resolve(),
            args.cases_file.resolve(),
            args.output_file.resolve(),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SOURCE_DATA = Path(
    r"E:\3\_training_backups\full-schema-functional-115-20260715-152328\vanna_data"
)
F2_PARENT = Path(r"E:\3\_training_backups")
FORMAL_DATA = PROJECT_ROOT / "vanna_data"
FORMAL_AGENT_DATA = PROJECT_ROOT / "agent_data"
SERVER_URL = "http://127.0.0.1:8000"

CASES = [
    {
        "id": "Q1",
        "query": "查询数据字典中的列表类型、列表描述、列表项代码和列表项名称，最多返回50条",
        "tables": ["ad_dict"],
        "limit": 50,
    },
    {
        "id": "Q2",
        "query": "查询排污口名称、排污口编码和省级编码，最多返回50条",
        "tables": ["rs_outlet"],
        "limit": 50,
    },
    {
        "id": "Q3",
        "query": "查询站点1408最近的水质小时变化趋势，返回监测时间、pH、溶解氧和水质等级，最多100条",
        "tables": ["wm_waterquality_hour_records"],
        "limit": 100,
    },
    {
        "id": "Q4",
        "query": "查询排污口国家编码、名称及对应整治状态和整治类型记录明细，最多50条",
        "tables": ["rs_outlet_info_v2", "rs_outlet_remediation_v2"],
        "limit": 50,
    },
    {
        "id": "Q5",
        "query": "查询断面编码、断面名称以及所属水体的编码、名称和类型，最多100条",
        "tables": ["wm_section_info", "wm_waterbody_info"],
        "limit": 100,
    },
    {
        "id": "Q6",
        "query": "按省市区县统计排污口总数和有整治记录的排污口数量，包含没有整治记录的排污口，并用横向柱状图展示，最多100个地区",
        "tables": ["rs_outlet_info_v2", "rs_outlet_remediation_v2"],
        "limit": 100,
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def manifest(path: Path) -> dict[str, Any]:
    from training.sop.storage_snapshot import build_directory_manifest

    return build_directory_manifest(path).to_dict()


def chroma_record_count(path: Path) -> int:
    uri = path.joinpath("chroma.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])


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


def post_sse(query: str, timeout: int = 240) -> dict[str, Any]:
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
                    continue
                result["event_count"] += 1
                if event.get("type") == "error":
                    result["errors"].append(str(event.get("data") or event))
                for path, value in walk_json(event):
                    key = path[-1].lower() if path else ""
                    if key == "sql" and isinstance(value, str) and value.strip():
                        result["sql"] = value.strip()
                    elif key in {"type", "component_type"} and isinstance(value, str):
                        if "rich" in "/".join(path).lower() or value in {"dataframe", "chart", "plotly"}:
                            result["rich_types"].append(value)
                    elif key == "rows" and isinstance(value, list) and value:
                        result["event_rows"] = value
                    elif key == "columns" and isinstance(value, list) and value:
                        result["event_columns"] = value
                    elif key in {"text", "content"} and isinstance(value, str) and value.strip():
                        if not any(marker in "/".join(path).lower() for marker in ("memory", "prompt")):
                            answer_parts.append(value)
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(exc.read(2000).decode("utf-8", errors="replace"))
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["answer"] = "\n".join(dict.fromkeys(answer_parts))[-12000:]
    return result


def extract_tables(sql: str) -> list[str]:
    return sorted(
        {
            match.lower()
            for match in re.findall(
                r"\b(?:from|join)\s+(?:[a-zA-Z_][\w]*\.)?[\"`]?([a-zA-Z_][\w]*)[\"`]?",
                sql,
                flags=re.I,
            )
        }
    )


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


def anonymize(rows: list[list[Any]]) -> list[list[Any]]:
    def mask(value: Any) -> Any:
        if value is None or value == "":
            return None
        text = str(value)
        return f"<{type(value).__name__}:{len(text)}>"

    return [[mask(value) for value in row] for row in rows[:3]]


def memory_content(item: Any) -> str:
    memory = getattr(item, "memory", item)
    return str(getattr(memory, "content", "") or "")


async def context_diagnostics(query: str, targets: list[str], enabled: bool) -> dict[str, Any]:
    from agent_config import create_memory
    from tools.metadata_retriever import DeterministicMetadataRetriever
    from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
    from tools.sql_guard import SQLGuard

    candidates = DeterministicMetadataRetriever().retrieve(query, top_n=5)
    memory = create_memory()
    texts = await memory.search_text_memories(
        query=query,
        context=SimpleNamespace(metadata={"stage": "f2_probe"}),
        limit=5,
    )
    contents = [memory_content(item) for item in texts]
    target_hits = {
        target: sum(
            1
            for content in contents
            if re.search(rf"(?i)(?:表名\s*[：:]\s*|create\s+table\s+\"?){re.escape(target)}\b", content)
        )
        for target in targets
    }
    legacy = {"enabled": enabled, "returned_count": 0, "injected_count": 0}
    if enabled:
        enhancer = SqlExampleContextEnhancer(memory=memory, sql_guard=SQLGuard(), top_k=5)
        await enhancer._retrieve_examples(query)
        legacy.update(
            {
                "returned_count": enhancer.last_stats.returned_count,
                "injected_count": enhancer.last_stats.injected_count,
            }
        )
    return {
        "metadata_top5": [item.get("table_name", "") for item in candidates],
        "text_memory_returned_count": len(contents),
        "text_memory_total_chars": sum(map(len, contents)),
        "text_memory_target_hits": target_hits,
        "legacy_sql_examples": legacy,
    }


def evaluate(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    from tools.sql_guard import SQLGuard

    sql = result["sql"]
    tables = extract_tables(sql)
    guard = SQLGuard().validate(
        sql=sql,
        query=case["query"],
        deterministic_candidate_tables=case["tables"],
    ) if sql else None
    checks: dict[str, bool] = {
        "http_200": result["http_status"] == 200,
        "no_sse_error": not result["errors"],
        "sql_present": bool(sql),
        "guard_passed": bool(guard and guard.passed),
        "expected_tables_exact": set(tables) == set(case["tables"]),
        "csv_present": bool(result.get("csv_file")),
        "rows_present": result.get("row_count", 0) > 0,
        "answer_present": bool(result.get("answer", "").strip()),
        "single_select_only": bool(
            re.match(r"^\s*select\b", sql, flags=re.I)
            and len([part for part in sql.split(";") if part.strip()]) == 1
            and not re.search(
                r"\b(insert|update|delete|drop|alter|create|truncate|comment)\b",
                sql,
                flags=re.I,
            )
        ),
    }
    match = re.search(r"\blimit\s+(\d+)", sql, flags=re.I)
    checks["limit_valid"] = bool(match and int(match.group(1)) <= case["limit"])
    qid = case["id"]
    if qid == "Q3":
        checks["time_order"] = bool(re.search(r"\border\s+by\b[\s\S]*\bdesc\b", sql, flags=re.I))
        checks["threshold_forbidden"] = "wm_waterquality_threshold" not in sql.lower()
    if qid in {"Q4", "Q5"}:
        checks["join_present"] = bool(re.search(r"\bjoin\b[\s\S]*\bon\b", sql, flags=re.I))
    if qid == "Q6":
        checks["left_join"] = bool(re.search(r"\bleft\s+(?:outer\s+)?join\b", sql, flags=re.I))
        checks["group_by"] = bool(re.search(r"\bgroup\s+by\b", sql, flags=re.I))
        specs = result.get("chart_specs", [])
        checks["bar_chart"] = any(item.get("type") in {"horizontal_bar", "bar"} for item in specs)
        checks["chart_title"] = any(
            item.get("type") in {"horizontal_bar", "bar"} and bool(str(item.get("title") or "").strip())
            for item in specs
        )
        columns = set(result.get("columns", []))
        checks["chart_fields_valid"] = any(
            item.get("type") in {"horizontal_bar", "bar"}
            and item.get("xField") in columns
            and isinstance(item.get("yFields"), list)
            and bool(item.get("yFields"))
            and set(item["yFields"]).issubset(columns)
            for item in specs
        )
    failed = [name for name, passed in checks.items() if not passed]
    stage = "PASS" if not failed else failed[0]
    result["tables"] = tables
    result["guard"] = (
        {"passed": guard.passed, "severity": guard.severity, "reason": guard.reason}
        if guard
        else {"passed": False, "severity": "missing", "reason": "SQL missing"}
    )
    result["checks"] = checks
    return not failed, stage, checks


def run_case(case: dict[str, Any], agent_dir: Path, enabled: bool) -> dict[str, Any]:
    before = query_result_files(agent_dir)
    diagnostics = asyncio.run(context_diagnostics(case["query"], case["tables"], enabled))
    response = post_sse(case["query"])
    after = query_result_files(agent_dir)
    new_csv = sorted(after - before)
    response["csv_file"] = new_csv[-1] if new_csv else ""
    response["columns"] = response.get("event_columns", [])
    response["row_count"] = len(response.get("event_rows", []))
    response["preview_first3_anonymized"] = anonymize(response.get("event_rows", []))
    if new_csv:
        columns, rows = read_csv(agent_dir / new_csv[-1])
        response["columns"] = columns
        response["row_count"] = len(rows)
        response["preview_first3_anonymized"] = anonymize(rows)
    response["chart_specs"] = extract_chart_specs(response["answer"])
    passed, stage, _ = evaluate(case, response)
    return {
        "id": case["id"],
        "query": case["query"],
        "context": diagnostics,
        "result": response,
        "passed": passed,
        "failure_stage": stage,
    }


def self_test() -> int:
    sql = "SELECT a.x, COUNT(b.id) AS n FROM a LEFT JOIN b ON a.id=b.a_id GROUP BY a.x LIMIT 10"
    assert extract_tables(sql) == ["a", "b"]
    assert extract_chart_specs('x<!-- chart_spec: {"type":"horizontal_bar"} -->')[0]["type"] == "horizontal_bar"
    assert len(anonymize([["secret", 2], [None, "x"]])) == 2
    event = {"rich": {"data": {"sql": "SELECT 1", "rows": [[1]], "columns": ["n"]}}}
    paths = walk_json(event)
    assert any(path[-1:] == ("sql",) and value == "SELECT 1" for path, value in paths)
    with tempfile.TemporaryDirectory() as directory:
        sample = Path(directory) / "sample.csv"
        sample.write_text("a,b\n\n1,2\n\n", encoding="utf-8")
        columns, rows = read_csv(sample)
        assert columns == ["a", "b"] and rows == [["1", "2"]]
    print("SELF_TEST: PASS")
    return 0


def main() -> int:
    if parse_args().self_test:
        return self_test()
    if not SOURCE_DATA.is_dir():
        raise RuntimeError(f"F1 训练副本不存在：{SOURCE_DATA}")

    formal_before = manifest(FORMAL_DATA)
    source_before = manifest(SOURCE_DATA)
    formal_csv_before = query_result_files(FORMAL_AGENT_DATA)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    f2_root = F2_PARENT / f"f2-end-to-end-mvp-{timestamp}"
    data_dir = f2_root / "vanna_data"
    agent_dir = f2_root / "agent_data"
    evidence_dir = f2_root / "evidence"
    shutil.copytree(SOURCE_DATA, data_dir)
    agent_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)
    copied_manifest = manifest(data_dir)
    if copied_manifest["content_sha256"] != source_before["content_sha256"]:
        raise RuntimeError("F2 工作副本与 F1 源副本不等价")

    # 诊断检索与主服务必须指向同一隔离副本，禁止触碰正式 Chroma。
    os.environ["VANNA_DATA_DIR"] = str(data_dir)
    os.environ["AGENT_DATA_DIR"] = str(agent_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"

    all_logs: list[str] = []
    secrets: list[str] = []
    process: subprocess.Popen[str] | None = None
    try:
        process, logs, key_source, key = start_server(data_dir, agent_dir, False)
        secrets.append(key)
        default_results = [run_case(case, agent_dir, True) for case in CASES]
        stop_server(process)
        process = None
        all_logs.extend(["=== DEFAULT MODE ===", *logs])

        failed_ids = [item["id"] for item in default_results if not item["passed"]]
        disabled_results: list[dict[str, Any]] = []
        if failed_ids:
            process, logs, _, key = start_server(data_dir, agent_dir, True)
            secrets.append(key)
            for case in CASES:
                if case["id"] in failed_ids:
                    disabled_results.append(run_case(case, agent_dir, False))
            stop_server(process)
            process = None
            all_logs.extend(["=== DISABLED MODE ===", *logs])

        source_after = manifest(SOURCE_DATA)
        formal_after = manifest(FORMAL_DATA)
        formal_csv_after = query_result_files(FORMAL_AGENT_DATA)
        report = {
            "f2_root": str(f2_root),
            "source_data": str(SOURCE_DATA),
            "source_record_count": chroma_record_count(SOURCE_DATA),
            "copy_record_count_before": chroma_record_count(data_dir),
            "source_manifest_before": source_before,
            "source_manifest_after": source_after,
            "copy_manifest_initial": copied_manifest,
            "formal_manifest_before": formal_before,
            "formal_manifest_after": formal_after,
            "formal_query_results_before": sorted(formal_csv_before),
            "formal_query_results_after": sorted(formal_csv_after),
            "credential_source": key_source,
            "default_mode": default_results,
            "disabled_mode": disabled_results,
            "default_pass_count": sum(item["passed"] for item in default_results),
            "default_failed_ids": failed_ids,
            "source_unchanged": source_before["content_sha256"] == source_after["content_sha256"],
            "formal_unchanged": formal_before["content_sha256"] == formal_after["content_sha256"],
            "formal_agent_data_unchanged": formal_csv_before == formal_csv_after,
        }
        cases_json = json.dumps({"cases": default_results}, ensure_ascii=False, indent=2)
        (evidence_dir / "f2-default-mode-results.json").write_text(cases_json, encoding="utf-8")
        (evidence_dir / "f2-cases.json").write_text(cases_json, encoding="utf-8")
        if disabled_results:
            (evidence_dir / "f2-disabled-mode-results.json").write_text(
                json.dumps({"cases": disabled_results}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (evidence_dir / "f2-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (evidence_dir / "f2-server-log.txt").write_text(
            redact("\n".join(all_logs), secrets), encoding="utf-8"
        )
        print(json.dumps({"f2_root": str(f2_root), "summary": report}, ensure_ascii=False))
        return 0 if not failed_ids else 2
    finally:
        stop_server(process)


if __name__ == "__main__":
    raise SystemExit(main())

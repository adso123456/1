from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "level2_post_training_probe_result.md"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"
FORMAL_VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
FORMAL_AGENT_DATA_DIR = PROJECT_ROOT / "agent_data"
BASE_COMMIT = "559f72a39ac994ffac5bb0a27f3469cbda75274a"
ALLOWED_PROBE_STATUS_PATHS = {
    "step4_server.py",
    "tools/guarded_run_sql_tool.py",
    "tools/test_guarded_run_sql_tool.py",
    "tools/guarded_run_sql_tool_test_result.md",
    "tools/test_sql_guard_execution_chain.py",
    "tools/sql_guard_execution_chain_test_result.md",
    "tools/level2_post_training_probe.py",
    "tools/level2_post_training_probe_result.md",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.sql_guard import SQLGuard


TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "Q1",
        "question": "查询某站点水质日趋势中的 pH 和溶解氧变化",
        "expected": "使用 wm_waterquality_day_records，不能使用 wm_waterquality_threshold",
        "expect_table": "wm_waterquality_day_records",
        "forbid_tables": ["wm_waterquality_threshold"],
    },
    {
        "id": "Q2",
        "question": "某站点水质小时变化趋势",
        "expected": "使用 wm_waterquality_hour_records，不能退化到 day/month/threshold",
        "expect_table": "wm_waterquality_hour_records",
        "forbid_tables": [
            "wm_waterquality_day_records",
            "wm_waterquality_month_records",
            "wm_waterquality_threshold",
        ],
    },
    {
        "id": "Q3",
        "question": "某站点水质月变化趋势",
        "expected": "使用 wm_waterquality_month_records，不能退化到 day/hour/year/threshold",
        "expect_table": "wm_waterquality_month_records",
        "forbid_tables": [
            "wm_waterquality_day_records",
            "wm_waterquality_hour_records",
            "wm_waterquality_year_records",
            "wm_waterquality_threshold",
        ],
        "must_pass": True,
    },
    {
        "id": "Q4",
        "question": "查询排污口编码",
        "expected": "使用 rs_outlet 或 rs_outlet_info_v2，并包含明确 outlet_code 字段",
        "expect_any_table": ["rs_outlet", "rs_outlet_info_v2"],
        "expect_any_column": [
            "outlet_code",
            "outlet_code_national",
            "outlet_code_local",
            "outlet_code_province",
        ],
        "must_pass": True,
    },
    {
        "id": "Q5",
        "question": "查询排污口基础信息",
        "expected": "使用 rs_outlet，不能误用 rs_outlet_trace_v2",
        "expect_table": "rs_outlet",
        "forbid_tables": ["rs_outlet_trace_v2"],
    },
    {
        "id": "Q6",
        "question": "查询站点名称和所属区域",
        "expected": "使用 wm_station_info_v2，字段包含 station_name 或 station_code",
        "expect_table": "wm_station_info_v2",
        "expect_any_column": ["station_name", "station_code"],
    },
    {
        "id": "Q7",
        "question": "查询区域编码和区域名称",
        "expected": "使用 gis_region，字段包含 region_code 或 region_name",
        "expect_table": "gis_region",
        "expect_any_column": ["region_code", "region_name"],
    },
    {
        "id": "Q8",
        "question": "查询取水口名称和水源类型",
        "expected": "普通取水口基础信息使用 wm_water_intake，不能误用 wm_water_source_intake_v2",
        "expect_table": "wm_water_intake",
        "forbid_tables": ["wm_water_source_intake_v2"],
    },
    {
        "id": "Q9",
        "question": "查询 wm_waterquality_threshold 中的水质趋势",
        "expected": "诱导阈值表回答水质趋势必须被 SQL Guard 拦截或不能执行",
        "expect_guard_block": True,
        "must_pass": True,
    },
    {
        "id": "Q10",
        "question": "查询水源地取水口供水能力",
        "expected": "requires_manual_review 相关场景，不能作为 approved 训练成功依据",
        "requires_manual_review": True,
    },
]


def selected_test_cases() -> list[dict[str, Any]]:
    raw = os.getenv("LEVEL2_POST_TRAINING_PROBE_CASES", "").strip()
    if not raw:
        return TEST_CASES
    selected = {item.strip().upper() for item in raw.split(",") if item.strip()}
    return [case for case in TEST_CASES if case["id"].upper() in selected]


def run_command(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip()


def effective_initial_status(status_short: str) -> tuple[str, list[str]]:
    unexpected: list[str] = []
    for line in status_short.splitlines():
        if not line.strip():
            continue
        path = line[2:].strip().replace("\\", "/")
        if path not in ALLOWED_PROBE_STATUS_PATHS:
            unexpected.append(line)
    return ("" if not unexpected else status_short, unexpected)


def get_deepseek_api_key() -> tuple[str, str]:
    process_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if process_key:
        return process_key, "process env DEEPSEEK_API_KEY"
    if os.name == "nt":
        try:
            import winreg

            locations = [
                (winreg.HKEY_CURRENT_USER, "Environment", "user env DEEPSEEK_API_KEY"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                    "machine env DEEPSEEK_API_KEY",
                ),
            ]
            for hive, subkey, source in locations:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")
                    if str(value).strip():
                        return str(value).strip(), source
                except OSError:
                    continue
        except ImportError:
            pass
    return "", "not found"


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def url_get(path: str, timeout: int = 5) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=timeout) as response:
            return response.status, response.read(2048).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vanna_fingerprint(root: Path = FORMAL_VANNA_DATA_DIR) -> dict[str, tuple[int, str]]:
    names = {"chroma.sqlite3", "data_level0.bin", "length.bin", "header.bin", "link_lists.bin"}
    result: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return result
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name in names):
        key = str(path.relative_to(root.parent)).replace("\\", "/")
        result[key] = (path.stat().st_size, sha256_file(path))
    return result


def query_result_files(root: Path = FORMAL_AGENT_DATA_DIR) -> set[str]:
    if not root.exists():
        return set()
    return {str(path.relative_to(root.parent)).replace("\\", "/") for path in root.glob("**/query_results_*.csv")}


def setup_isolation() -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix="vanna_level2_post_training_probe_"))
    isolated_vanna = root / "vanna_data"
    isolated_agent = root / "agent_data"
    shutil.copytree(FORMAL_VANNA_DATA_DIR, isolated_vanna)
    isolated_agent.mkdir(parents=True, exist_ok=True)
    return {"root": root, "vanna_data": isolated_vanna, "agent_data": isolated_agent}


def start_server(isolation: dict[str, Path]) -> tuple[subprocess.Popen[str] | None, list[str], str]:
    if is_port_open(8000):
        return None, ["Port 8000 already open; stop to avoid non-isolated service"], "port 8000 occupied"
    api_key, key_source = get_deepseek_api_key()
    if not api_key:
        return None, [f"DEEPSEEK_API_KEY not found ({key_source})"], "DEEPSEEK_API_KEY not found"

    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = api_key
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data"])
    env["VANNA_PROBE_ISOLATED"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    logs: list[str] = []
    process = subprocess.Popen(
        [str(PYTHON_EXE), "step4_server.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def read_logs() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=read_logs, daemon=True).start()
    deadline = time.time() + 120
    while time.time() < deadline:
        if process.poll() is not None:
            return process, logs, "\n".join(logs[-80:]) or f"process exited {process.returncode}"
        status, _ = url_get("/health", timeout=2)
        if status == 200:
            return process, logs, ""
        time.sleep(2)
    return process, logs, "Timed out waiting for /health"


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def post_sse(question: str, timeout_seconds: int = 75) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": question,
            "conversation_id": None,
            "request_id": None,
            "metadata": {"query": question, "level2_post_training_probe": True},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVER_URL}/api/vanna/v2/chat_sse",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {
        "has_response": False,
        "errors": [],
        "events": 0,
        "generated_sql": "",
        "all_sql": [],
        "rich_types": [],
        "preview": "",
        "blocked_message": False,
    }
    chunks: list[str] = []
    deadline = time.time() + timeout_seconds
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result["http_status"] = response.status
            while time.time() < deadline:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                result["events"] += 1
                result["has_response"] = True
                if (
                    "SQL Guard blocked execution" in data
                    or "hard block" in data
                    or "系统限制" in data
                ):
                    result["blocked_message"] = True
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    chunks.append(data[:500])
                    continue
                if event.get("type") == "error":
                    result["errors"].append(event.get("data", {}).get("message", "unknown error"))
                rich = event.get("rich") or {}
                rich_type = rich.get("type")
                if rich_type:
                    result["rich_types"].append(rich_type)
                rich_data = rich.get("data") if isinstance(rich.get("data"), dict) else {}
                if isinstance(rich_data, dict):
                    sql = rich_data.get("sql")
                    if isinstance(sql, str) and sql.strip():
                        clean_sql = sql.strip()
                        result["generated_sql"] = clean_sql
                        result["all_sql"].append(clean_sql)
                    chunks.append(json.dumps(rich_data, ensure_ascii=False)[:500])
                text = ""
                if isinstance(event.get("text"), str):
                    text = event["text"]
                elif isinstance(event.get("content"), str):
                    text = event["content"]
                elif isinstance(event.get("data"), dict) and isinstance(event["data"].get("text"), str):
                    text = event["data"]["text"]
                if text:
                    chunks.append(text)
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(exc.read(1000).decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["preview"] = "\n".join(chunks)[:1200]
    if (
        "SQL Guard blocked execution" in result["preview"]
        or "hard block" in result["preview"]
        or "系统限制" in result["preview"]
    ):
        result["blocked_message"] = True
    return result


def p0_info(question: str) -> dict[str, Any]:
    candidates = DeterministicMetadataRetriever().retrieve(question, top_n=10)
    columns: list[str] = []
    for candidate in candidates:
        table = candidate["table_name"]
        for column in candidate.get("matched_columns", []):
            columns.append(f"{table}.{column['column_name']}")
    return {
        "tables": [candidate["table_name"] for candidate in candidates],
        "columns": columns[:30],
    }


def tables_from_sql(sql: str) -> list[str]:
    return re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)


def contains_ddl_dml(sql: str) -> bool:
    return bool(re.search(r"\b(insert|update|delete|drop|alter|create|truncate|comment)\b", sql, flags=re.I))


def evaluate_case(case: dict[str, Any], sse: dict[str, Any], p0: dict[str, Any]) -> dict[str, Any]:
    sql = sse.get("generated_sql") or ""
    guard = SQLGuard().validate(sql=sql, query=case["question"]) if sql else None
    used_tables = guard.used_tables if guard else tables_from_sql(sql)
    used_columns = guard.used_columns if guard else []
    true_sql_executed = "dataframe" in sse.get("rich_types", []) and not sse.get("blocked_message")
    status = "pass"
    reasons: list[str] = []

    if not sse.get("has_response"):
        status = "fail"
        reasons.append("服务无响应")
    if sse.get("errors"):
        status = "fail"
        reasons.extend(sse["errors"])
    if contains_ddl_dml(sql):
        status = "fail"
        reasons.append("出现 DDL/DML")

    if case.get("requires_manual_review"):
        status = "warning"
        reasons.append("requires_manual_review 场景，仅记录观察结果，不能作为 approved 训练成功依据")
    elif case.get("expect_guard_block"):
        blocked = (
            bool(sse.get("blocked_message"))
            or (guard is not None and not guard.passed)
            or not true_sql_executed
        )
        if not blocked or true_sql_executed:
            status = "fail"
            reasons.append("SQL Guard 未拦截或已执行")
    else:
        expected = case.get("expect_table")
        if expected:
            if not sql:
                status = "fail"
                reasons.append("未生成可校验 SQL")
            elif expected not in used_tables:
                status = "fail"
                reasons.append(f"生成 SQL 未命中预期表 {expected}")
        expected_any = case.get("expect_any_table") or []
        if expected_any:
            if not sql:
                status = "fail"
                reasons.append("未生成可校验 SQL")
            elif not any(table in used_tables for table in expected_any):
                status = "fail"
                reasons.append("生成 SQL 未命中任一预期表")
        forbidden = case.get("forbid_tables") or []
        if any(table in used_tables for table in forbidden):
            status = "fail"
            reasons.append("使用了禁止表：" + ", ".join(table for table in forbidden if table in used_tables))
        expected_cols = case.get("expect_any_column") or []
        if expected_cols:
            if not sql:
                status = "fail"
                reasons.append("未生成可校验 SQL")
            elif not any(any(col in actual for actual in used_columns) for col in expected_cols):
                status = "fail"
                reasons.append("生成 SQL 未命中预期字段")
        if guard is not None and not guard.passed:
            status = "fail"
            reasons.append("SQL Guard 未通过：" + guard.reason)

    return {
        "id": case["id"],
        "question": case["question"],
        "expected": case["expected"],
        "generated_sql": sql or "unknown",
        "used_tables": used_tables,
        "used_columns": used_columns,
        "p0_tables": p0["tables"],
        "p0_columns": p0["columns"],
        "sql_guard_result": guard.to_dict() if guard else {},
        "true_sql_executed": true_sql_executed,
        "response_preview": sse.get("preview", ""),
        "status": status,
        "reason": "符合预期" if not reasons and status == "pass" else "；".join(reasons),
    }


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def format_list(items: list[str]) -> str:
    return "；".join(items) if items else "无"


def write_report(summary: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    lines = [
        "# 第 2 级 SQL 示例训练后最小问答验证报告",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        summary["remote"],
        "```",
        f"- 当前 commit：{summary['commit']}",
        "- 初始 git status --short：",
        "```text",
        summary["initial_status"] or "clean",
        "```",
        f"- 临时隔离目录：{summary['temp_root']}",
        f"- 是否启动真实主服务：{bool_cn(summary['server_started'])}",
        f"- 是否使用临时 VANNA_DATA_DIR：{bool_cn(summary['used_temp_vanna'])}",
        f"- 是否使用临时 AGENT_DATA_DIR：{bool_cn(summary['used_temp_agent'])}",
        f"- 正式 vanna_data 前后是否变化：{bool_cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{bool_cn(summary['formal_query_added'])}",
        f"- 临时目录是否产生 ChromaDB 变化：{bool_cn(summary['temp_chroma_changed'])}",
        f"- 临时目录是否产生 query_results：{bool_cn(summary['temp_query_results'])}",
        f"- 测试问题总数：{summary['total']}",
        f"- pass 数量：{summary['pass_count']}",
        f"- warning 数量：{summary['warning_count']}",
        f"- fail 数量：{summary['fail_count']}",
        f"- fail 问题列表：{format_list(summary['fail_cases'])}",
        f"- warning 问题列表：{format_list(summary['warning_cases'])}",
        f"- 是否执行真实 SQL：{bool_cn(summary['executed_real_sql'])}",
        f"- 是否连接数据库：{bool_cn(summary['connected_database'])}",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        f"- 是否写入正式 ChromaDB：{bool_cn(summary['formal_vanna_changed'])}",
        "- 是否修改数据库结构：否",
        "- 是否进入第 3/4 级：否",
        f"- 启动失败原因：{summary['startup_failure'] or '无'}",
        f"- 当前结论：{summary['conclusion']}",
        f"- 下一步建议：{summary['next_step']}",
        "",
        "## 正式 vanna_data 指纹",
        "",
        f"- 验证前：{json.dumps(summary['formal_vanna_before'], ensure_ascii=False)}",
        f"- 验证后：{json.dumps(summary['formal_vanna_after'], ensure_ascii=False)}",
        "",
        "## 问题明细",
        "",
    ]
    for case in cases:
        guard = case["sql_guard_result"]
        lines.extend(
            [
                f"### {case['id']}",
                "",
                f"- question：{case['question']}",
                f"- expected：{case['expected']}",
                f"- generated_sql：{case['generated_sql']}",
                f"- used_tables：{', '.join(case['used_tables']) or 'unknown'}",
                f"- used_columns：{', '.join(case['used_columns']) or 'unknown'}",
                f"- P0 candidate tables：{', '.join(case['p0_tables']) or 'unknown'}",
                f"- P0 matched columns：{', '.join(case['p0_columns']) or 'unknown'}",
                f"- SQL Guard result：{json.dumps(guard, ensure_ascii=False) if guard else 'unknown'}",
                f"- true_sql_executed：{bool_cn(case['true_sql_executed'])}",
                f"- response preview：{case['response_preview'][:1200]}",
                f"- pass/warning/fail：{case['status']}",
                f"- reason：{case['reason']}",
                "",
            ]
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    remote = run_command(["git", "remote", "-v"])
    raw_initial_status = run_command(["git", "status", "--short"])
    initial_status, unexpected_status = effective_initial_status(raw_initial_status)
    commit = run_command(["git", "rev-parse", "HEAD"])
    if unexpected_status:
        raise SystemExit("工作区存在非本阶段文件改动，停止：" + "；".join(unexpected_status))
    if commit != BASE_COMMIT and not run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) == "":
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    temp_before = vanna_fingerprint(isolation["vanna_data"])
    process: subprocess.Popen[str] | None = None
    logs: list[str] = []
    startup_failure = ""
    server_started = False
    case_results: list[dict[str, Any]] = []
    try:
        process, logs, startup_failure = start_server(isolation)
        server_started = process is not None and not startup_failure
        if server_started:
            for case in selected_test_cases():
                print(f"RUN {case['id']}: {case['question']}", flush=True)
                p0 = p0_info(case["question"])
                sse = post_sse(case["question"])
                case_results.append(evaluate_case(case, sse, p0))
        else:
            case_results = [
                {
                    "id": case["id"],
                    "question": case["question"],
                    "expected": case["expected"],
                    "generated_sql": "unknown",
                    "used_tables": [],
                    "used_columns": [],
                    "p0_tables": p0_info(case["question"])["tables"],
                    "p0_columns": [],
                    "sql_guard_result": {},
                    "true_sql_executed": False,
                    "response_preview": "",
                    "status": "fail",
                    "reason": startup_failure,
                }
                for case in selected_test_cases()
            ]
    finally:
        stop_server(process)

    formal_vanna_after = vanna_fingerprint()
    formal_query_after = query_result_files()
    temp_after = vanna_fingerprint(isolation["vanna_data"])
    temp_chroma_changed = temp_before != temp_after
    temp_query_results = bool(query_result_files(isolation["agent_data"]))
    formal_vanna_changed = formal_vanna_before != formal_vanna_after
    formal_query_added = bool(formal_query_after - formal_query_before)
    pass_count = sum(1 for case in case_results if case["status"] == "pass")
    warning_count = sum(1 for case in case_results if case["status"] == "warning")
    fail_count = sum(1 for case in case_results if case["status"] == "fail")
    fail_cases = [case["id"] for case in case_results if case["status"] == "fail"]
    warning_cases = [case["id"] for case in case_results if case["status"] == "warning"]
    q3_pass = any(case["id"] == "Q3" and case["status"] == "pass" for case in case_results)
    q4_pass = any(case["id"] == "Q4" and case["status"] == "pass" for case in case_results)
    q9_pass = any(case["id"] == "Q9" and case["status"] == "pass" for case in case_results)
    q10_ok = any(case["id"] == "Q10" and case["status"] in {"warning", "pass"} for case in case_results)
    executed_real_sql = any(case["true_sql_executed"] for case in case_results)
    selected_ids = {case["id"] for case in selected_test_cases()}
    if selected_ids == {"Q9"}:
        pass_standard = not formal_vanna_changed and not formal_query_added and q9_pass
    else:
        pass_standard = (
            not formal_vanna_changed
            and not formal_query_added
            and pass_count >= 7
            and q3_pass
            and q4_pass
            and q9_pass
            and q10_ok
        )
    quality_fail_ids = {"Q1", "Q3", "Q5", "Q7"}
    quality_fail_exists = bool(quality_fail_ids.intersection(fail_cases))
    if pass_standard:
        conclusion = "通过"
        next_step = "可进入人工复核验证报告；不要进入第 3/4 级"
    elif formal_vanna_changed or formal_query_added:
        conclusion = "未通过"
        next_step = "正式 vanna_data 或 agent_data/query_results 出现变化，先隔离并清理；禁止进入第 3/4 级"
    elif not q9_pass:
        conclusion = "未通过"
        next_step = "继续修 SQL Guard 拦截链路；禁止进入第 3/4 级"
    elif quality_fail_exists:
        conclusion = "部分通过"
        next_step = "SQL Guard 阻塞已解决，但问答质量仍需分阶段修复；禁止进入第 3/4 级"
    else:
        conclusion = "未通过"
        next_step = "先分析 fail/warning 明细，禁止进入第 3/4 级"

    summary = {
        "remote": remote,
        "commit": commit,
        "initial_status": initial_status,
        "temp_root": str(isolation["root"]),
        "server_started": server_started,
        "used_temp_vanna": True,
        "used_temp_agent": True,
        "formal_vanna_changed": formal_vanna_changed,
        "formal_query_added": formal_query_added,
        "temp_chroma_changed": temp_chroma_changed,
        "temp_query_results": temp_query_results,
        "total": len(case_results),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "fail_cases": fail_cases,
        "warning_cases": warning_cases,
        "executed_real_sql": executed_real_sql,
        "connected_database": executed_real_sql or server_started,
        "startup_failure": startup_failure,
        "formal_vanna_before": formal_vanna_before,
        "formal_vanna_after": formal_vanna_after,
        "conclusion": conclusion,
        "next_step": next_step,
    }
    write_report(summary, case_results)
    print(f"报告: {REPORT_PATH}")
    print(f"pass={pass_count} warning={warning_count} fail={fail_count}")
    print(f"formal_vanna_changed={formal_vanna_changed}")
    print(f"formal_query_added={formal_query_added}")
    return 0 if pass_standard else 1


if __name__ == "__main__":
    raise SystemExit(main())

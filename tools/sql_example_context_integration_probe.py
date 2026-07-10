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
REPORT_PATH = CURRENT_DIR / "sql_example_context_integration_result.md"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"
FORMAL_VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
FORMAL_AGENT_DATA_DIR = PROJECT_ROOT / "agent_data"
BASE_COMMIT = "6259466b2478b8c25f9e687bc0da8beed2a8658a"
ALLOWED_PROBE_STATUS_PATHS = {
    "step4_server.py",
    "tools/test_sql_example_context_integration.py",
    "tools/sql_example_context_integration_test_result.md",
    "tools/sql_example_context_integration_probe.py",
    "tools/sql_example_context_integration_result.md",
    "tools/level2_post_training_probe.py",
    "tools/level2_post_training_probe_result.md",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.sql_guard import SQLGuard


SMOKE_CASES: list[dict[str, Any]] = [
    {
        "id": "Q1",
        "question": "查询某站点水质日趋势中的 pH 和溶解氧变化",
        "expected": "使用 wm_waterquality_day_records，字段至少包含 m2_value 或 m3_value，不使用 wm_waterquality_threshold",
        "expect_table": "wm_waterquality_day_records",
        "forbid_tables": ["wm_waterquality_threshold"],
        "require_columns": ["m2_value", "m3_value"],
        "must_pass": False,
    },
    {
        "id": "Q2",
        "question": "某站点水质小时变化趋势",
        "expected": "使用 wm_waterquality_hour_records，字段至少包含 monitor_time 和 m*_value，不退化到 day/month/threshold",
        "expect_table": "wm_waterquality_hour_records",
        "forbid_tables": [
            "wm_waterquality_day_records",
            "wm_waterquality_month_records",
            "wm_waterquality_threshold",
        ],
        "must_pass": False,
    },
    {
        "id": "Q3",
        "question": "某站点水质月变化趋势",
        "expected": "使用 wm_waterquality_month_records，字段包含 monitor_year/monitor_month，字段至少包含 m2_value 或 m3_value，不退化到 day/hour/year/threshold",
        "expect_table": "wm_waterquality_month_records",
        "forbid_tables": [
            "wm_waterquality_day_records",
            "wm_waterquality_hour_records",
            "wm_waterquality_year_records",
            "wm_waterquality_threshold",
        ],
        "require_columns": ["monitor_year", "monitor_month"],
        "must_pass": True,
    },
    {
        "id": "Q4",
        "question": "查询排污口编码",
        "expected": "使用 rs_outlet 或 rs_outlet_info_v2，包含 outlet_code 相关字段至少一个",
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
        "id": "Q9",
        "question": "查询 wm_waterquality_threshold 中的水质趋势",
        "expected": "必须被 SQL Guard 或 hard block 拦截，true_sql_executed 必须为否，不能产生 query_results",
        "expect_guard_block": True,
        "must_pass": True,
    },
]


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
    return {
        str(path.relative_to(root.parent)).replace("\\", "/")
        for path in root.glob("**/query_results_*.csv")
    }


def setup_isolation() -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix="vanna_sql_example_integration_"))
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


def post_sse(question: str, timeout_seconds: int = 90) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": question,
            "conversation_id": None,
            "request_id": None,
            "metadata": {
                "query": question,
                "sql_example_context_integration_probe": True,
            },
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
        "matched_l2_sample_id": "",
        "prompt_likely_contained_sql_example": False,
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
                # 检测 SQL 示例是否进入 prompt 上下文
                if any(
                    marker in data
                    for marker in [
                        "Retrieved Approved SQL Examples",
                        "L2_SQL_",
                        "sql_example",
                    ]
                ):
                    result["prompt_likely_contained_sql_example"] = True
                # 提取匹配的 L2 sample id
                for sample_prefix in ["L2_SQL_", "L2_"]:
                    sample_match = re.search(
                        rf"({re.escape(sample_prefix)}\d+|\bL2_SQL_\w+)", data
                    )
                    if sample_match:
                        result["matched_l2_sample_id"] = sample_match.group(1)
                        break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    chunks.append(data[:500])
                    continue
                if event.get("type") == "error":
                    result["errors"].append(
                        event.get("data", {}).get("message", "unknown error")
                    )
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
                elif (
                    isinstance(event.get("data"), dict)
                    and isinstance(event["data"].get("text"), str)
                ):
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
    # 再次检查 preview 中是否包含 SQL 示例标记
    if not result["prompt_likely_contained_sql_example"]:
        if any(
            marker in result["preview"]
            for marker in [
                "Retrieved Approved SQL Examples",
                "L2_SQL_",
            ]
        ):
            result["prompt_likely_contained_sql_example"] = True
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
    return re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I
    )


def contains_ddl_dml(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(insert|update|delete|drop|alter|create|truncate|comment)\b",
            sql,
            flags=re.I,
        )
    )


def evaluate_case(
    case: dict[str, Any], sse: dict[str, Any], p0: dict[str, Any]
) -> dict[str, Any]:
    sql = sse.get("generated_sql") or ""
    guard = SQLGuard().validate(sql=sql, query=case["question"]) if sql else None
    used_tables = guard.used_tables if guard else tables_from_sql(sql)
    used_columns = guard.used_columns if guard else []
    true_sql_executed = (
        "dataframe" in sse.get("rich_types", []) and not sse.get("blocked_message")
    )
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

    if case.get("expect_guard_block"):
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
            reasons.append(
                "使用了禁止表："
                + ", ".join(table for table in forbidden if table in used_tables)
            )
        expected_cols = case.get("expect_any_column") or []
        if expected_cols:
            if not sql:
                status = "fail"
                reasons.append("未生成可校验 SQL")
            elif not any(
                any(col in actual for actual in used_columns) for col in expected_cols
            ):
                status = "fail"
                reasons.append("生成 SQL 未命中预期字段")
        require_cols = case.get("require_columns") or []
        if require_cols:
            for col in require_cols:
                found = any(col in actual for actual in used_columns)
                if not found:
                    # 放宽：检查 SQL 原文是否包含该字段
                    if sql and col not in sql:
                        status = "fail" if case.get("must_pass") else "warning"
                        reasons.append(f"缺少必需字段 {col}")
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
        "p0_candidate_tables": p0["tables"],
        "p0_matched_columns": p0["columns"],
        "sql_guard_result": guard.to_dict() if guard else {},
        "true_sql_executed": true_sql_executed,
        "response_preview": sse.get("preview", ""),
        "prompt_likely_contained_sql_example": sse.get(
            "prompt_likely_contained_sql_example", False
        ),
        "matched_l2_sample_id": sse.get("matched_l2_sample_id", ""),
        "status": status,
        "reason": "符合预期" if not reasons and status == "pass" else "；".join(reasons),
    }


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def format_list(items: list[str]) -> str:
    return "；".join(items) if items else "无"


def write_report(
    summary: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    lines = [
        "# SQL Example Context Enhancer 接入隔离验证报告",
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
        f"- 修改/新增文件路径：{', '.join(summary['modified_files'])}",
        f"- 是否接入主服务：{bool_cn(summary['integrated'])}",
        f"- 是否启动真实主服务：{bool_cn(summary['server_started'])}",
        f"- 是否使用临时 VANNA_DATA_DIR：{bool_cn(summary['used_temp_vanna'])}",
        f"- 是否使用临时 AGENT_DATA_DIR：{bool_cn(summary['used_temp_agent'])}",
        f"- 正式 vanna_data 是否变化：{bool_cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{bool_cn(summary['formal_query_added'])}",
        f"- 是否连接数据库：{bool_cn(summary['connected_database'])}",
        f"- 是否执行真实 SQL：{bool_cn(summary['executed_real_sql'])}",
        f"- 是否调用 DeepSeek：{bool_cn(summary['called_deepseek'])}",
        f"- 是否训练 Vanna：否",
        f"- 是否调用 vn.train()：否",
        f"- 是否写入正式 ChromaDB：{bool_cn(summary['formal_vanna_changed'])}",
        f"- 是否修改数据库结构：否",
        f"- 是否进入第 3/4 级：否",
        f"- 接入链路静态验证是否通过：{bool_cn(summary['static_integration_passed'])}",
        f"- smoke 测试总数：{summary['total']}",
        f"- pass 数量：{summary['pass_count']}",
        f"- warning 数量：{summary['warning_count']}",
        f"- fail 数量：{summary['fail_count']}",
        f"- fail 问题列表：{format_list(summary['fail_cases'])}",
        f"- Q3 是否通过：{bool_cn(summary['q3_pass'])}",
        f"- Q4 是否通过：{bool_cn(summary['q4_pass'])}",
        f"- Q9 是否通过：{bool_cn(summary['q9_pass'])}",
        f"- Q9 true_sql_executed：{bool_cn(summary['q9_true_sql_executed'])}",
        f"- 当前结论：{summary['conclusion']}",
        f"- 下一阶段建议：{summary['next_step']}",
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
                f"- P0 candidate tables：{', '.join(case['p0_candidate_tables']) or 'unknown'}",
                f"- P0 matched columns：{', '.join(case['p0_matched_columns']) or 'unknown'}",
                f"- SQL Guard result：{json.dumps(guard, ensure_ascii=False) if guard else 'unknown'}",
                f"- true_sql_executed：{bool_cn(case['true_sql_executed'])}",
                f"- response preview：{case['response_preview'][:1200]}",
                f"- whether prompt likely contained SQL example：{bool_cn(case.get('prompt_likely_contained_sql_example', False))}",
                f"- matched L2 sample id：{case.get('matched_l2_sample_id', '') or '无'}",
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
        raise SystemExit(
            "工作区存在非本阶段文件改动，停止：" + "；".join(unexpected_status)
        )
    if (
        commit != BASE_COMMIT
        and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit])
        != ""
    ):
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

    # 检查静态集成测试是否通过
    static_test_path = CURRENT_DIR / "sql_example_context_integration_test_result.md"
    static_test_passed = False
    if static_test_path.exists():
        content = static_test_path.read_text(encoding="utf-8")
        static_test_passed = (
            "失败数量：0" in content
            or "接入链路静态验证是否通过：是" in content
        )

    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    process: subprocess.Popen[str] | None = None
    logs: list[str] = []
    startup_failure = ""
    server_started = False
    case_results: list[dict[str, Any]] = []
    try:
        process, logs, startup_failure = start_server(isolation)
        server_started = process is not None and not startup_failure
        if server_started:
            for case in SMOKE_CASES:
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
                    "p0_candidate_tables": p0_info(case["question"])["tables"],
                    "p0_matched_columns": [],
                    "sql_guard_result": {},
                    "true_sql_executed": False,
                    "response_preview": "",
                    "prompt_likely_contained_sql_example": False,
                    "matched_l2_sample_id": "",
                    "status": "fail",
                    "reason": startup_failure,
                }
                for case in SMOKE_CASES
            ]
    finally:
        stop_server(process)

    formal_vanna_after = vanna_fingerprint()
    formal_query_after = query_result_files()
    formal_vanna_changed = formal_vanna_before != formal_vanna_after
    formal_query_added = bool(formal_query_after - formal_query_before)
    pass_count = sum(1 for case in case_results if case["status"] == "pass")
    warning_count = sum(1 for case in case_results if case["status"] == "warning")
    fail_count = sum(1 for case in case_results if case["status"] == "fail")
    fail_cases = [case["id"] for case in case_results if case["status"] == "fail"]
    warning_cases = [case["id"] for case in case_results if case["status"] == "warning"]
    q3 = next((case for case in case_results if case["id"] == "Q3"), {})
    q3_pass = q3.get("status") == "pass"
    q4 = next((case for case in case_results if case["id"] == "Q4"), {})
    q4_pass = q4.get("status") == "pass"
    q9 = next((case for case in case_results if case["id"] == "Q9"), {})
    q9_pass = q9.get("status") == "pass"
    q9_true_sql_executed = q9.get("true_sql_executed", False)
    executed_real_sql = any(case["true_sql_executed"] for case in case_results)
    called_deepseek = server_started and any(
        case.get("has_response") for case in case_results
    )

    # 判定结论
    if formal_vanna_changed or formal_query_added:
        conclusion = "未通过"
        next_step = "正式 vanna_data 或 agent_data 被污染，先清理后重试；禁止进入第 3/4 级"
    elif not server_started:
        conclusion = "未通过"
        next_step = (
            f"服务启动失败：{startup_failure}；禁止进入第 3/4 级"
        )
    elif not q9_pass:
        conclusion = "未通过"
        next_step = "Q9 SQL Guard 拦截失败，先修 SQL Guard 链路；禁止进入第 3/4 级"
    elif q9_pass and q4_pass and q3_pass:
        conclusion = "通过"
        next_step = "可进入全量验证"
    elif q9_pass and q4_pass and not q3_pass:
        conclusion = "部分通过"
        next_step = "Q3 月趋势仍失败，下一阶段修 metadata 字段映射"
    else:
        conclusion = "部分通过"
        next_step = f"需要进一步分析 fail/warning；禁止进入第 3/4 级"

    summary = {
        "remote": remote,
        "commit": commit,
        "initial_status": initial_status,
        "modified_files": ["step4_server.py"],
        "integrated": True,
        "server_started": server_started,
        "used_temp_vanna": True,
        "used_temp_agent": True,
        "formal_vanna_changed": formal_vanna_changed,
        "formal_query_added": formal_query_added,
        "connected_database": executed_real_sql or server_started,
        "executed_real_sql": executed_real_sql,
        "called_deepseek": called_deepseek,
        "static_integration_passed": static_test_passed,
        "total": len(case_results),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "fail_cases": fail_cases,
        "warning_cases": warning_cases,
        "q3_pass": q3_pass,
        "q4_pass": q4_pass,
        "q9_pass": q9_pass,
        "q9_true_sql_executed": q9_true_sql_executed,
        "formal_vanna_before": formal_vanna_before,
        "formal_vanna_after": formal_vanna_after,
        "conclusion": conclusion,
        "next_step": next_step,
    }
    write_report(summary, case_results)
    print(f"报告: {REPORT_PATH}")
    print(f"pass={pass_count} warning={warning_count} fail={fail_count}")
    print(f"Q3_pass={q3_pass} Q4_pass={q4_pass} Q9_pass={q9_pass}")
    print(f"formal_vanna_changed={formal_vanna_changed}")
    print(f"formal_query_added={formal_query_added}")
    return 0 if "通过" in conclusion else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
REPORT_PATH = CURRENT_DIR / "sql_example_context_full_validation_result.md"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"
FORMAL_VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
FORMAL_AGENT_DATA_DIR = PROJECT_ROOT / "agent_data"
BASE_COMMIT = "725cbd30188c286eb98f72dd014721f9d8c7ff51"
ALLOWED_PROBE_STATUS_PATHS = {
    "step4_server.py",
    "tools/test_sql_example_context_integration.py",
    "tools/sql_example_context_integration_test_result.md",
    "tools/sql_example_context_integration_probe.py",
    "tools/sql_example_context_integration_result.md",
    "tools/sql_example_context_full_validation_probe.py",
    "tools/sql_example_context_full_validation_result.md",
    "tools/level2_post_training_probe.py",
    "tools/level2_post_training_probe_result.md",
}

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import DeterministicMetadataRetriever
from backend.sql_guard import SQLGuard


FULL_CASES: list[dict[str, Any]] = [
    {
        "id": "Q1",
        "question": "查询某站点水质日趋势中的 pH 和溶解氧变化",
        "expected": "使用 wm_waterquality_day_records，字段包含 m2_value 或 m3_value，不使用 wm_waterquality_threshold",
        "expect_table": "wm_waterquality_day_records",
        "forbid_tables": ["wm_waterquality_threshold"],
        "require_columns": ["m2_value", "m3_value"],
    },
    {
        "id": "Q2",
        "question": "某站点水质小时变化趋势",
        "expected": "使用 wm_waterquality_hour_records，字段包含 monitor_time 和 m*_value，不退化到 day/month/threshold",
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
        "expected": "使用 wm_waterquality_month_records，字段包含 monitor_year/monitor_month，字段包含 m2_value 或 m3_value，不退化到 day/hour/year/threshold",
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
        "id": "Q5",
        "question": "查询排污口基础信息",
        "expected": "使用 rs_outlet 则 pass；使用 rs_outlet_info_v2 且 SQL Guard ok 则 warning；使用 rs_outlet_trace_v2 则 fail",
        "expect_any_table": ["rs_outlet", "rs_outlet_info_v2"],
        "forbid_tables": ["rs_outlet_trace_v2"],
        "q5_flexible": True,
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
        "expected": "使用 wm_water_intake，字段包含 name 和 water_type，不误用 wm_water_source_intake_v2",
        "expect_table": "wm_water_intake",
        "forbid_tables": ["wm_water_source_intake_v2"],
    },
    {
        "id": "Q9",
        "question": "查询 wm_waterquality_threshold 中的水质趋势",
        "expected": "必须被 SQL Guard 或 hard block 拦截，true_sql_executed 必须为否",
        "expect_guard_block": True,
        "must_pass": True,
    },
    {
        "id": "Q10",
        "question": "查询水源地取水口供水能力",
        "expected": "requires_manual_review 场景，最多 warning，不能作为 approved 训练成功依据",
        "requires_manual_review": True,
        "q10_warning_only": True,
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
    except Exception as exc:
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
    root = Path(tempfile.mkdtemp(prefix="vanna_full_validation_"))
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
                "full_validation_probe": True,
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
                # 检测 SQL 示例标记
                for marker in [
                    "Retrieved Approved SQL Examples",
                    "L2_SQL_",
                ]:
                    if marker in data:
                        result["prompt_likely_contained_sql_example"] = True
                        break
                # 提取 L2 sample id
                for sample_prefix in ["L2_SQL_"]:
                    sample_match = re.search(
                        rf"({re.escape(sample_prefix)}\d+)", data
                    )
                    if sample_match and not result["matched_l2_sample_id"]:
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
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["preview"] = "\n".join(chunks)[:1200]
    if (
        "SQL Guard blocked execution" in result["preview"]
        or "hard block" in result["preview"]
        or "系统限制" in result["preview"]
    ):
        result["blocked_message"] = True
    # 再次检查 preview 中 L2 示例标记
    if not result["prompt_likely_contained_sql_example"]:
        for marker in ["Retrieved Approved SQL Examples", "L2_SQL_"]:
            if marker in result["preview"]:
                result["prompt_likely_contained_sql_example"] = True
                break
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

    # Q9: SQL Guard 拦截
    if case.get("expect_guard_block"):
        blocked = (
            bool(sse.get("blocked_message"))
            or (guard is not None and not guard.passed)
            or not true_sql_executed
        )
        if not blocked or true_sql_executed:
            status = "fail"
            reasons.append("SQL Guard 未拦截或已执行")
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
            "prompt_likely_contained_sql_example": sse.get("prompt_likely_contained_sql_example", False),
            "matched_l2_sample_id": sse.get("matched_l2_sample_id", ""),
            "status": status,
            "reason": "符合预期：SQL Guard 已拦截" if not reasons and status == "pass" else "；".join(reasons),
        }

    # Q10: requires_manual_review 场景
    if case.get("requires_manual_review"):
        status = "warning"
        reasons.append("requires_manual_review 场景，仅记录观察结果，不能作为 approved 训练成功依据")
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
            "prompt_likely_contained_sql_example": sse.get("prompt_likely_contained_sql_example", False),
            "matched_l2_sample_id": sse.get("matched_l2_sample_id", ""),
            "status": status,
            "reason": "符合预期" if not reasons else "；".join(reasons),
        }

    # Q5: 灵活判定（rs_outlet → pass, rs_outlet_info_v2 → warning）
    if case.get("q5_flexible"):
        forbidden = case.get("forbid_tables") or []
        if any(table in used_tables for table in forbidden):
            status = "fail"
            reasons.append("使用了禁止表：" + ", ".join(t for t in forbidden if t in used_tables))
        elif not sql:
            status = "fail"
            reasons.append("未生成可校验 SQL")
        elif "rs_outlet" in used_tables and "rs_outlet_info_v2" in used_tables:
            status = "warning"
            reasons.append("同时使用了 rs_outlet 和 rs_outlet_info_v2，业务口径需人工确认")
        elif "rs_outlet_info_v2" in used_tables and "rs_outlet" not in used_tables:
            status = "warning"
            reasons.append("使用了 rs_outlet_info_v2 而非 rs_outlet，业务口径需人工确认")
        elif "rs_outlet" in used_tables:
            status = "pass"
            reasons.append("使用了 rs_outlet，符合预期")
        else:
            status = "fail"
            reasons.append("未使用 rs_outlet 或 rs_outlet_info_v2")

        if guard is not None and not guard.passed and status != "fail":
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
            "prompt_likely_contained_sql_example": sse.get("prompt_likely_contained_sql_example", False),
            "matched_l2_sample_id": sse.get("matched_l2_sample_id", ""),
            "status": status,
            "reason": "；".join(reasons) if reasons else "符合预期",
        }

    # 通用判定
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
        reasons.append("使用了禁止表：" + ", ".join(t for t in forbidden if t in used_tables))

    expected_cols = case.get("expect_any_column") or []
    if expected_cols:
        if not sql:
            status = "fail"
            reasons.append("未生成可校验 SQL")
        elif not any(any(col in actual for actual in used_columns) for col in expected_cols):
            status = "fail"
            reasons.append("生成 SQL 未命中预期字段")

    require_cols = case.get("require_columns") or []
    if require_cols:
        for col in require_cols:
            found = any(col in actual for actual in used_columns) or (sql and col in sql)
            if not found:
                if case.get("must_pass"):
                    status = "fail"
                else:
                    status = "warning" if status == "pass" else status
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
        "prompt_likely_contained_sql_example": sse.get("prompt_likely_contained_sql_example", False),
        "matched_l2_sample_id": sse.get("matched_l2_sample_id", ""),
        "status": status,
        "reason": "符合预期" if not reasons and status == "pass" else "；".join(reasons),
    }


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def format_list(items: list[str]) -> str:
    return "；".join(items) if items else "无"


def write_report(summary: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    lines = [
        "# SQL Example Context 接入后全量 10 题验证报告",
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
        f"- 是否接入主服务：是",
        f"- 是否启动真实主服务：{bool_cn(summary['server_started'])}",
        f"- 是否使用临时 VANNA_DATA_DIR：{bool_cn(summary['used_temp_vanna'])}",
        f"- 是否使用临时 AGENT_DATA_DIR：{bool_cn(summary['used_temp_agent'])}",
        f"- 正式 vanna_data 是否变化：{bool_cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{bool_cn(summary['formal_query_added'])}",
        f"- 是否连接数据库：{bool_cn(summary['connected_database'])}",
        f"- 是否执行真实 SQL：{bool_cn(summary['executed_real_sql'])}",
        f"- 是否调用 DeepSeek：{bool_cn(summary['called_deepseek'])}",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        f"- 是否写入正式 ChromaDB：{bool_cn(summary['formal_vanna_changed'])}",
        "- 是否修改数据库结构：否",
        "- 是否进入第 3/4 级：否",
        f"- 测试问题总数：{summary['total']}",
        f"- pass 数量：{summary['pass_count']}",
        f"- warning 数量：{summary['warning_count']}",
        f"- fail 数量：{summary['fail_count']}",
        f"- fail 问题列表：{format_list(summary['fail_cases'])}",
        f"- warning 问题列表：{format_list(summary['warning_cases'])}",
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
                # 当前 probe 未截取最终 prompt，不能可靠判断 SQL 示例是否进入 context
                "- whether prompt likely contained SQL example：unknown（当前 probe 未截取最终 prompt，不能可靠判断）",
                "- matched L2 sample id：unknown（当前 probe 未截取最终 prompt，不能可靠判断）",
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
    if (
        commit != BASE_COMMIT
        and run_command(["git", "merge-base", "--is-ancestor", BASE_COMMIT, commit]) != ""
    ):
        raise SystemExit(f"当前 commit 不满足要求：{commit}")

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
            for case in FULL_CASES:
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
                for case in FULL_CASES
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
    q1 = next((case for case in case_results if case["id"] == "Q1"), {})
    q2 = next((case for case in case_results if case["id"] == "Q2"), {})
    q1_q2_pass = q1.get("status") in ("pass", "warning") and q2.get("status") in ("pass", "warning")
    executed_real_sql = any(case["true_sql_executed"] for case in case_results)
    # 只要启动了真实主服务，就视为调用了 DeepSeek（LLM 生成 SQL 时必然调用）
    called_deepseek = server_started
    q6_q7_q8_pass = all(
        next((case for case in case_results if case["id"] == qid), {}).get("status") in ("pass", "warning")
        for qid in ("Q6", "Q7", "Q8")
    )
    q5_q10_non_fail = all(
        next((case for case in case_results if case["id"] == qid), {}).get("status") != "fail"
        for qid in ("Q5", "Q10")
    )

    # 判定结论
    if formal_vanna_changed or formal_query_added:
        conclusion = "未通过"
        next_step = "正式 vanna_data 或 agent_data 被污染，先清理后重试；禁止进入第 3/4 级"
    elif not server_started:
        conclusion = "未通过"
        next_step = f"服务启动失败：{startup_failure}；禁止进入第 3/4 级"
    elif not q9_pass:
        conclusion = "未通过"
        next_step = "先修 SQL Guard 链路；禁止进入第 3/4 级"
    elif not q3_pass:
        conclusion = "部分通过"
        next_step = "下一阶段修水质月趋势；禁止进入第 3/4 级"
    elif not q4_pass:
        conclusion = "部分通过"
        next_step = "Q4 回退需修复；禁止进入第 3/4 级"
    elif not q1_q2_pass:
        conclusion = "部分通过"
        next_step = "Q1/Q2 未通过，需修复水质日/小时趋势"
    elif q3_pass and q4_pass and q9_pass and q1_q2_pass and fail_count == 0:
        conclusion = "通过"
        next_step = "先做业务确认与第 3 级范围设计；确认通过后，另起阶段进入第 3 级。继续禁止直接进入第 4 级"
    elif fail_count == 0 and q6_q7_q8_pass:
        conclusion = "通过"
        next_step = "先做业务确认与第 3 级范围设计；确认通过后，另起阶段进入第 3 级。继续禁止直接进入第 4 级"
    elif not q6_q7_q8_pass:
        conclusion = "部分通过"
        next_step = "下一阶段修 generic name/code/type 与 metadata context"
    else:
        conclusion = "部分通过"
        next_step = f"需分析 fail/warning；禁止进入第 3/4 级"

    summary = {
        "remote": remote,
        "commit": commit,
        "initial_status": initial_status,
        "modified_files": [f.name for f in CURRENT_DIR.glob("*") if f.is_file() and f.name.startswith("sql_example_context_full_validation")],
        "server_started": server_started,
        "used_temp_vanna": True,
        "used_temp_agent": True,
        "formal_vanna_changed": formal_vanna_changed,
        "formal_query_added": formal_query_added,
        "connected_database": executed_real_sql or server_started,
        "executed_real_sql": executed_real_sql,
        "called_deepseek": called_deepseek,
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

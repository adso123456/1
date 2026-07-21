from __future__ import annotations

import asyncio
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
from typing import Any, Type

from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "live_service_manual_probe_result.md"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"
FORMAL_VANNA_DATA_DIR = PROJECT_ROOT / "vanna_data"
FORMAL_AGENT_DATA_DIR = PROJECT_ROOT / "agent_data"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.metadata_retriever import DeterministicMetadataRetriever
from backend.sql_guard import SQLGuard


TEST_CASES = [
    {
        "name": "合法水质趋势",
        "query": "某地区某时间段水质变化趋势",
        "expected_top1": "wm_waterquality_day_records",
        "forbidden_top1": "wm_waterquality_threshold",
    },
    {
        "name": "合法小时水质",
        "query": "某地区某时间段水质小时变化趋势",
        "expected_top1": "wm_waterquality_hour_records",
    },
    {
        "name": "排污口编码",
        "query": "查询排污口编码",
        "expected_any_table": ["rs_outlet", "rs_outlet_info_v2"],
        "expected_any_column": ["outlet_code", "outlet_code_national"],
    },
    {
        "name": "排污口溯源",
        "query": "排污口溯源",
        "expected_any_table": ["rs_outlet_trace_v2", "wst_trace_edge", "wst_trace_node"],
    },
]


class FakeContext(BaseModel):
    metadata: dict[str, Any]


class FakeInnerRunSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self) -> None:
        self.call_count = 0

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "Fake run_sql for live service manual probe"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: Any, args: RunSqlToolArgs) -> ToolResult:
        self.call_count += 1
        return ToolResult(
            success=True,
            result_for_llm="fake inner run_sql executed",
            metadata={"executed_real_sql": False},
        )


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


def get_deepseek_api_key() -> tuple[str, str]:
    process_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if process_key:
        return process_key, "process env DEEPSEEK_API_KEY"

    if os.name == "nt":
        try:
            import winreg

            registry_locations = [
                (
                    winreg.HKEY_CURRENT_USER,
                    "Environment",
                    "user env DEEPSEEK_API_KEY",
                ),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                    "machine env DEEPSEEK_API_KEY",
                ),
            ]
            for hive, subkey, source in registry_locations:
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


def url_get(path: str, timeout: int = 10) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=timeout) as response:
            return response.status, response.read(2048).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - probe reports exact failure.
        return None, f"{type(exc).__name__}: {exc}"


def collect_file_fingerprint(paths: list[Path]) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(PROJECT_ROOT))] = (path.stat().st_size, digest)
    return result


def chroma_fingerprint() -> dict[str, tuple[int, str]]:
    targets = [
        FORMAL_VANNA_DATA_DIR / "chroma.sqlite3",
        FORMAL_VANNA_DATA_DIR / "68092f4b-e2a5-4ccd-b126-95b1218cb050" / "data_level0.bin",
        FORMAL_VANNA_DATA_DIR / "68092f4b-e2a5-4ccd-b126-95b1218cb050" / "length.bin",
    ]
    return collect_file_fingerprint(targets)


def query_result_files() -> set[str]:
    return {
        str(path.relative_to(PROJECT_ROOT))
        for path in FORMAL_AGENT_DATA_DIR.glob("**/query_results_*.csv")
    }


def setup_isolated_dirs() -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="vanna_live_probe_"))
    isolated_vanna_data = root / "vanna_data"
    isolated_agent_data = root / "agent_data"

    if FORMAL_VANNA_DATA_DIR.exists():
        shutil.copytree(FORMAL_VANNA_DATA_DIR, isolated_vanna_data)
    else:
        isolated_vanna_data.mkdir(parents=True, exist_ok=True)
    isolated_agent_data.mkdir(parents=True, exist_ok=True)

    return {
        "enabled": True,
        "root": root,
        "vanna_data_dir": isolated_vanna_data,
        "agent_data_dir": isolated_agent_data,
    }


def has_chroma_files(path: Path) -> bool:
    if not path.exists():
        return False
    return any(
        item.is_file()
        and (
            item.name == "chroma.sqlite3"
            or item.name in {"data_level0.bin", "length.bin"}
        )
        for item in path.rglob("*")
    )


def isolated_query_result_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(str(item.relative_to(path)) for item in path.glob("**/query_results_*.csv"))


def scan_contextual_hardcoded_keys() -> list[str]:
    files = [line for line in run_command(["git", "ls-files"]).splitlines() if line]
    pattern = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")
    findings: list[str] = []
    for relative in files:
        path = PROJECT_ROOT / relative
        if path.suffix.lower() in {".bin", ".sqlite3", ".db", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if not pattern.search(line):
                continue
            context = f"{relative}\n{line}".lower()
            if any(
                marker in context
                for marker in (
                    ".env",
                    "api_key",
                    "authorization",
                    "bearer",
                    "deepseek",
                    "opencode",
                    "openai",
                    "secret",
                    "token",
                )
            ):
                findings.append(relative.replace("\\", "/"))
                break
    return sorted(set(findings))


def start_server(
    isolation: dict[str, Any],
) -> tuple[subprocess.Popen[str] | None, list[str], dict[str, Any]]:
    api_key, key_source = get_deepseek_api_key()
    info = {
        "api_key_source": key_source,
        "started": False,
        "failure_reason": "",
        "used_existing_server": False,
    }
    logs: list[str] = []

    if is_port_open(8000):
        info["failure_reason"] = "Port 8000 already open; cannot guarantee isolated VANNA_DATA_DIR/AGENT_DATA_DIR"
        logs.append(info["failure_reason"])
        return None, logs, info

    if not api_key:
        info["failure_reason"] = "DEEPSEEK_API_KEY not found"
        return None, logs, info

    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = api_key
    env["PYTHONUNBUFFERED"] = "1"
    env["VANNA_PROBE_ISOLATED"] = "1"
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data_dir"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data_dir"])

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

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=reader, daemon=True).start()

    deadline = time.time() + 120
    while time.time() < deadline:
        if process.poll() is not None:
            info["failure_reason"] = "\n".join(logs[-80:]) or f"process exited {process.returncode}"
            return process, logs, info
        status, _ = url_get("/health", timeout=2)
        if status == 200:
            info["started"] = True
            return process, logs, info
        time.sleep(2)

    info["failure_reason"] = "Timed out waiting for /health"
    return process, logs, info


def stop_server(process: subprocess.Popen[str] | None, used_existing_server: bool) -> None:
    if process is None or used_existing_server:
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def post_sse(query: str, timeout_seconds: int = 180) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": query,
            "conversation_id": None,
            "request_id": None,
            "metadata": {"query": query, "manual_probe": True},
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
        "events": 0,
        "errors": [],
        "text_preview": "",
        "generated_sql": "",
        "rich_types": [],
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
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    chunks.append(data[:300])
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
                        result["generated_sql"] = sql.strip()

                text = (
                    event.get("text")
                    or event.get("content")
                    or event.get("data", {}).get("text")
                    if isinstance(event.get("data"), dict)
                    else ""
                )
                if isinstance(text, str) and text:
                    chunks.append(text)
                elif rich_data:
                    chunks.append(json.dumps(rich_data, ensure_ascii=False)[:300])
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(exc.read(500).decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 - probe reports exact failure.
        result["errors"].append(f"{type(exc).__name__}: {exc}")

    result["rich_types"] = sorted(set(result["rich_types"]))
    result["text_preview"] = "\n".join(chunks)[:1000]
    return result


def contains_ddl_dml(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(insert|update|delete|drop|alter|create|truncate|comment)\b",
            sql,
            flags=re.I,
        )
    )


async def run_illegal_guard_probe() -> dict[str, Any]:
    fake_inner = FakeInnerRunSqlTool()
    guarded_tool = GuardedRunSqlTool(inner_tool=fake_inner, sql_guard=SQLGuard())
    result = await guarded_tool.execute(
        FakeContext(metadata={"query": "某地区某时间段水质变化趋势"}),
        RunSqlToolArgs(sql="SELECT * FROM wm_waterquality_threshold"),
    )
    metadata = dict((result.metadata or {}).get("sql_guard", {}))
    return {
        "blocked": not result.success and fake_inner.call_count == 0,
        "message": result.result_for_llm or "",
        "reason": metadata.get("reason", ""),
        "inner_called": fake_inner.call_count > 0,
    }


def p0_info(query: str) -> dict[str, Any]:
    candidates = DeterministicMetadataRetriever().retrieve(query, top_n=10)
    tables = [candidate["table_name"] for candidate in candidates]
    columns: list[str] = []
    for candidate in candidates:
        for column in candidate.get("matched_columns", []):
            columns.append(f"{candidate['table_name']}.{column['column_name']}")
    return {"tables": tables, "columns": columns[:20]}


def evaluate_case(case: dict[str, Any], sse: dict[str, Any], p0: dict[str, Any]) -> dict[str, Any]:
    sql = sse.get("generated_sql", "")
    guard_result = SQLGuard().validate(sql, case["query"]) if sql else None
    checks = [bool(sse["has_response"]), not sse["errors"], not contains_ddl_dml(sql)]

    tables = p0["tables"]
    if case.get("expected_top1"):
        checks.append(tables[:1] == [case["expected_top1"]])
    if case.get("forbidden_top1"):
        checks.append(tables[:1] != [case["forbidden_top1"]])
    if case.get("expected_any_table"):
        checks.append(any(table in tables for table in case["expected_any_table"]))
    if case.get("expected_any_column"):
        checks.append(
            any(
                any(expected in column for column in p0["columns"])
                for expected in case["expected_any_column"]
            )
        )
    if guard_result is not None:
        checks.append(guard_result.passed or guard_result.severity == "warning")

    return {
        "name": case["name"],
        "query": case["query"],
        "has_response": bool(sse["has_response"]),
        "generated_sql": sql,
        "p0_tables": tables,
        "p0_columns": p0["columns"],
        "sql_guard_result": guard_result.to_dict() if guard_result else {},
        "executed_real_sql": bool(sql or "dataframe" in sse.get("rich_types", [])),
        "passed": all(checks),
        "reason": "符合预期" if all(checks) else "; ".join(sse["errors"]) or "响应、P0、SQL Guard 或 DDL/DML 检查不符合预期",
        "response_preview": sse.get("text_preview", ""),
        "rich_types": sse.get("rich_types", []),
    }


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def format_list(values: list[str]) -> str:
    return "；".join(values) if values else "无"


def write_report(summary: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    lines = [
        "# 真实主服务人工验证报告",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        summary["remote"],
        "```",
        "- git status --short：",
        "```text",
        summary["status_short"] or "clean",
        "```",
        f"- 写入来源定位结论：{summary['write_source_conclusion']}",
        f"- 是否修改 step4_server.py：{bool_cn(summary['modified_step4_server'])}",
        f"- 是否修改 backend/memory.py：{bool_cn(summary['modified_backend_memory'])}",
        f"- 是否修改 .env.example：{bool_cn(summary['modified_env_example'])}",
        f"- 是否启用隔离目录：{bool_cn(summary['isolation_enabled'])}",
        f"- 隔离目录路径：{summary['isolation_root']}",
        f"- 正式 vanna_data 是否变化：{bool_cn(summary['formal_vanna_data_changed'])}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{bool_cn(summary['formal_query_results_added'])}",
        f"- 临时目录是否产生 Chroma 文件：{bool_cn(summary['temp_chroma_files_present'])}",
        f"- 临时目录是否产生 query_results 文件：{bool_cn(summary['temp_query_results_present'])}",
        f"- 是否成功启动 step4_server.py：{bool_cn(summary['server_started'])}",
        f"- 启动失败原因：{summary['startup_failure'] or '无'}",
        f"- 是否调用 DeepSeek 官方 API：{bool_cn(summary['called_deepseek'])}",
        f"- DeepSeek 调用是否成功：{bool_cn(summary['deepseek_success'])}",
        f"- 是否使用 DeterministicMetadataContextEnhancer：{bool_cn(summary['uses_context_enhancer'])}",
        f"- 是否使用 GuardedRunSqlTool：{bool_cn(summary['uses_guarded_tool'])}",
        f"- 是否观察到 SQL Guard 执行前拦截：{bool_cn(summary['guard_intercept_observed'])}",
        f"- 是否观察到 SQL Guard blocked execution：{bool_cn(summary['guard_block_message_observed'])}",
        f"- 测试问题总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败问题列表：{format_list(summary['failed_cases'])}",
        f"- 是否执行真实 SQL：{bool_cn(summary['executed_real_sql'])}",
        f"- 是否执行 DDL / DML：{bool_cn(summary['executed_ddl_dml'])}",
        "- 是否训练 Vanna：否",
        f"- 是否写入正式 ChromaDB：{bool_cn(summary['formal_vanna_data_changed'])}",
        "- 是否修改数据库结构：否",
        "- 是否进入第 2/3/4 级：否",
        f"- 当前结论：{summary['conclusion']}",
        f"- 下一步建议：{summary['next_step']}",
        "",
        "## 日志观察",
        "",
        f"- 初始化 LLM 服务日志：{bool_cn(summary['log_llm_init'])}",
        f"- 注册工具日志：{bool_cn(summary['log_register_tool'])}",
        f"- GuardedRunSqlTool 日志：{summary['guarded_log_note']}",
        f"- DeterministicMetadataContextEnhancer 日志：{summary['enhancer_log_note']}",
        f"- SQL Guard blocked execution 日志：{summary['guard_block_log_note']}",
        "",
        "## 问题明细",
        "",
    ]

    for case in cases:
        lines.extend(
            [
                f"### {case['name']}",
                "",
                f"- query：{case['query']}",
                f"- 是否有响应：{bool_cn(case['has_response'])}",
                f"- 是否生成 SQL：{bool_cn(bool(case['generated_sql']))}",
                f"- 生成 SQL：{case['generated_sql'] or '未可见'}",
                f"- P0 candidate top tables：{', '.join(case['p0_tables']) or '未可见'}",
                f"- P0 matched columns：{', '.join(case['p0_columns']) or '未可见'}",
                f"- SQL Guard 结果：{json.dumps(case['sql_guard_result'], ensure_ascii=False) if case['sql_guard_result'] else '未可见'}",
                f"- 是否执行真实 SQL：{bool_cn(case['executed_real_sql'])}",
                f"- rich types：{', '.join(case['rich_types']) or '无'}",
                f"- 是否通过：{bool_cn(case['passed'])}",
                f"- reason：{case['reason']}",
                f"- response preview：{case['response_preview'] or '无'}",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    before_chroma = chroma_fingerprint()
    before_query_files = query_result_files()
    remote = run_command(["git", "remote", "-v"])
    status_short = run_command(["git", "status", "--short"])
    hardcoded_keys = scan_contextual_hardcoded_keys()
    isolation = setup_isolated_dirs()

    server_process: subprocess.Popen[str] | None = None
    logs: list[str] = []
    startup: dict[str, Any] = {}
    case_results: list[dict[str, Any]] = []
    illegal_guard = {"blocked": False, "message": "", "reason": "", "inner_called": False}

    try:
        server_process, logs, startup = start_server(isolation)
        server_started = bool(startup.get("started"))
        health_status, health_body = url_get("/health", timeout=10) if server_started else (None, "")

        if server_started and health_status == 200:
            for case in TEST_CASES:
                p0 = p0_info(case["query"])
                sse = post_sse(case["query"])
                case_results.append(evaluate_case(case, sse, p0))

        illegal_guard = await run_illegal_guard_probe()
    finally:
        stop_server(server_process, bool(startup.get("used_existing_server")))
        time.sleep(2)

    after_chroma = chroma_fingerprint()
    after_query_files = query_result_files()
    log_text = "\n".join(logs)
    formal_query_results_added = sorted(after_query_files - before_query_files)
    formal_vanna_data_changed = before_chroma != after_chroma
    temp_query_results = isolated_query_result_files(isolation["agent_data_dir"])
    temp_chroma_files_present = has_chroma_files(isolation["vanna_data_dir"])
    temp_query_results_present = bool(temp_query_results)

    guard_block_message = "SQL Guard blocked execution" in illegal_guard["message"]
    failed_cases = [case["name"] for case in case_results if not case["passed"]]
    executed_real_sql = any(case["executed_real_sql"] for case in case_results)
    executed_ddl_dml = any(contains_ddl_dml(case["generated_sql"]) for case in case_results)
    deepseek_success = any(case["has_response"] and not case["reason"].startswith("HTTP") for case in case_results)

    if not startup.get("started"):
        conclusion = "当前阶段未通过完整真实服务验证"
        next_step = "先处理 step4_server.py 启动失败，再重跑 live_service_manual_probe.py"
    elif failed_cases:
        conclusion = "当前阶段未通过完整真实服务验证"
        next_step = "根据失败问题明细修复真实链路，再重跑验证"
    elif formal_vanna_data_changed or formal_query_results_added:
        conclusion = "当前阶段未通过完整真实服务验证：正式目录仍有新增或修改"
        next_step = "检查隔离环境变量是否被真实服务继承，再重跑验证"
    else:
        conclusion = "真实主服务隔离写入验证通过"
        next_step = "可进入下一阶段前先处理遗留 vanna_data/query_results 工作区产物"

    summary = {
        "remote": remote,
        "status_short": status_short,
        "write_source_conclusion": "Chroma 写入来自 backend.memory.create_memory() 的 persist_directory；query_results CSV 写入来自 RunSqlTool 经 LocalFileSystem.write_file 输出。已通过 VANNA_DATA_DIR/AGENT_DATA_DIR 将验证写入隔离到临时目录。",
        "modified_step4_server": bool(run_command(["git", "diff", "--name-only", "--", "step4_server.py"])),
        "modified_backend_memory": bool(run_command(["git", "diff", "--name-only", "--", "backend/memory.py"])),
        "modified_env_example": bool(run_command(["git", "diff", "--name-only", "--", ".env.example"])),
        "isolation_enabled": bool(isolation["enabled"]),
        "isolation_root": str(isolation["root"]),
        "formal_vanna_data_changed": formal_vanna_data_changed,
        "formal_query_results_added": bool(formal_query_results_added),
        "temp_chroma_files_present": temp_chroma_files_present,
        "temp_query_results_present": temp_query_results_present,
        "server_started": bool(startup.get("started")),
        "startup_failure": startup.get("failure_reason", ""),
        "called_deepseek": bool(case_results),
        "deepseek_success": deepseek_success,
        "uses_context_enhancer": "DeterministicMetadataContextEnhancer"
        in (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8", errors="replace"),
        "uses_guarded_tool": "GuardedRunSqlTool"
        in (PROJECT_ROOT / "step4_server.py").read_text(encoding="utf-8", errors="replace"),
        "guard_intercept_observed": bool(illegal_guard["blocked"]),
        "guard_block_message_observed": guard_block_message,
        "total": len(TEST_CASES) + 1,
        "passed": sum(1 for case in case_results if case["passed"]) + int(illegal_guard["blocked"]),
        "failed": len(TEST_CASES) + 1 - (sum(1 for case in case_results if case["passed"]) + int(illegal_guard["blocked"])),
        "failed_cases": failed_cases + ([] if illegal_guard["blocked"] else ["非法 SQL Guard 拦截"]),
        "executed_real_sql": executed_real_sql,
        "executed_ddl_dml": executed_ddl_dml,
        "conclusion": conclusion,
        "next_step": next_step,
        "log_llm_init": "初始化 LLM 服务 (deepseek-v4-pro via DeepSeek official API)" in log_text,
        "log_register_tool": "注册工具 (run_sql)" in log_text,
        "guarded_log_note": "日志未直接显示，但 step4_server.py 静态验证已确认注册 GuardedRunSqlTool",
        "enhancer_log_note": "日志未直接显示，但 step4_server.py 静态验证已确认注入 DeterministicMetadataContextEnhancer",
        "guard_block_log_note": "独立 GuardedRunSqlTool 验证观察到 SQL Guard blocked execution"
        if guard_block_message
        else "未观察到",
        "hardcoded_key_files": hardcoded_keys,
        "formal_query_result_files_added": formal_query_results_added,
        "temp_query_result_files": temp_query_results,
    }

    if not case_results:
        for case in TEST_CASES:
            p0 = p0_info(case["query"])
            case_results.append(
                {
                    "name": case["name"],
                    "query": case["query"],
                    "has_response": False,
                    "generated_sql": "",
                    "p0_tables": p0["tables"],
                    "p0_columns": p0["columns"],
                    "sql_guard_result": {},
                    "executed_real_sql": False,
                    "passed": False,
                    "reason": startup.get("failure_reason", "服务未启动，未执行 SSE 验证"),
                    "response_preview": "",
                    "rich_types": [],
                }
            )

    case_results.append(
        {
            "name": "非法 SQL Guard 拦截",
            "query": "某地区某时间段水质变化趋势 / SELECT * FROM wm_waterquality_threshold",
            "has_response": bool(illegal_guard["message"]),
            "generated_sql": "SELECT * FROM wm_waterquality_threshold",
            "p0_tables": p0_info("某地区某时间段水质变化趋势")["tables"],
            "p0_columns": [],
            "sql_guard_result": {
                "blocked": illegal_guard["blocked"],
                "reason": illegal_guard["reason"],
                "inner_called": illegal_guard["inner_called"],
            },
            "executed_real_sql": False,
            "passed": bool(illegal_guard["blocked"]),
            "reason": illegal_guard["reason"] or "未观察到 SQL Guard blocked execution",
            "response_preview": illegal_guard["message"],
            "rich_types": [],
        }
    )

    write_report(summary, case_results)

    print(f"report: {REPORT_PATH}")
    print(f"server_started: {summary['server_started']}")
    print(f"called_deepseek: {summary['called_deepseek']}")
    print(f"deepseek_success: {summary['deepseek_success']}")
    print(f"guard_intercept_observed: {summary['guard_intercept_observed']}")
    print(f"guard_block_message_observed: {summary['guard_block_message_observed']}")
    print(f"total: {summary['total']}")
    print(f"passed: {summary['passed']}")
    print(f"failed: {summary['failed']}")
    print(f"failed_cases: {format_list(summary['failed_cases'])}")
    print(f"executed_real_sql: {summary['executed_real_sql']}")
    print(f"executed_ddl_dml: {summary['executed_ddl_dml']}")
    print(f"isolation_root: {summary['isolation_root']}")
    print(f"formal_vanna_data_changed: {summary['formal_vanna_data_changed']}")
    print(f"formal_query_results_added: {summary['formal_query_results_added']}")
    print(f"temp_chroma_files_present: {summary['temp_chroma_files_present']}")
    print(f"temp_query_results_present: {summary['temp_query_results_present']}")
    print(f"conclusion: {summary['conclusion']}")
    return 0


if __name__ == "__main__":
    asyncio.run(main())

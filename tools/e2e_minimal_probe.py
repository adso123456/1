from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "e2e_minimal_probe_result.md"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult

from tools.guarded_run_sql_tool import GuardedRunSqlTool
from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.sql_guard import SQLGuard


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"


TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "合法水质趋势问题",
        "query": "某地区某时间段水质变化趋势",
        "probe_sql": "SELECT * FROM wm_waterquality_day_records LIMIT 10",
        "expected_candidate": "wm_waterquality_day_records",
        "forbidden_top1": "wm_waterquality_threshold",
        "expected_guard_pass": True,
        "expected_inner_called": True,
    },
    {
        "name": "合法小时水质问题",
        "query": "某地区某时间段水质小时变化趋势",
        "probe_sql": "SELECT station_id, m1_value FROM wm_waterquality_hour_records LIMIT 10",
        "expected_candidate": "wm_waterquality_hour_records",
        "expected_guard_pass": True,
        "expected_inner_called": True,
    },
    {
        "name": "排污口编码问题",
        "query": "查询排污口编码",
        "probe_sql": "SELECT outlet_code FROM rs_outlet LIMIT 10",
        "expected_any_candidate": ["rs_outlet", "rs_outlet_info_v2"],
        "expected_any_column": ["outlet_code", "outlet_code_national"],
        "expected_guard_pass": True,
        "expected_inner_called": True,
    },
    {
        "name": "排污口溯源问题",
        "query": "排污口溯源",
        "probe_sql": "SELECT * FROM rs_outlet",
        "expected_any_candidate": ["rs_outlet_trace_v2"],
        "expected_guard_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "SQL Guard 拦截验证",
        "query": "某地区某时间段水质变化趋势",
        "probe_sql": "SELECT * FROM wm_waterquality_threshold",
        "expected_candidate": "wm_waterquality_day_records",
        "expected_guard_pass": False,
        "expected_inner_called": False,
    },
]


class FakeContext(BaseModel):
    metadata: dict[str, Any]


class FakeInnerRunSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self) -> None:
        self.call_count = 0
        self.called_sql: list[str] = []

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "Fake run_sql tool for minimal E2E probe"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: Any, args: RunSqlToolArgs) -> ToolResult:
        self.call_count += 1
        self.called_sql.append(args.sql)
        return ToolResult(
            success=True,
            result_for_llm="fake inner tool executed; no real SQL was executed",
            metadata={"fake_inner_tool": True, "executed_real_sql": False},
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


def read_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8", errors="replace")


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


def call_deepseek() -> dict[str, Any]:
    api_key, key_source = get_deepseek_api_key()
    result = {
        "called": False,
        "success": False,
        "status": None,
        "key_source": key_source,
        "reason": "",
    }
    if not api_key:
        result["reason"] = "DEEPSEEK_API_KEY not found"
        return result

    payload = json.dumps(
        {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    result["called"] = True
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(512)
            result["status"] = response.status
            result["success"] = 200 <= response.status < 300
            result["reason"] = f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["reason"] = f"HTTP {exc.code}: {exc.read(200).decode('utf-8', errors='replace')}"
    except Exception as exc:  # noqa: BLE001 - probe must report exact failure.
        result["reason"] = f"{type(exc).__name__}: {exc}"

    return result


def scan_hardcoded_keys() -> list[str]:
    paths_output = run_command(["git", "ls-files"])
    paths = [line.strip() for line in paths_output.splitlines() if line.strip()]
    findings: list[str] = []
    pattern = re.compile(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}")
    skipped_parts = {"vanna_venv", "node_modules", ".git", "__pycache__"}

    for relative in paths:
        path = PROJECT_ROOT / relative
        if any(part in skipped_parts for part in path.parts):
            continue
        if path.is_dir() or path.suffix.lower() in {".bin", ".sqlite3", ".db", ".pyc"}:
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
                    "apikey",
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


def check_env_files_safe() -> dict[str, Any]:
    risky_files = [".env", ".env.local"]
    tracked = [
        path for path in risky_files if run_command(["git", "ls-files", "--", path])
    ]
    present_unignored: list[str] = []

    for relative in risky_files:
        path = PROJECT_ROOT / relative
        if not path.exists():
            continue
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        if not ignored:
            present_unignored.append(relative)

    return {
        "safe": not tracked and not present_unignored,
        "tracked": tracked,
        "present_unignored": present_unignored,
    }


def static_checks() -> dict[str, Any]:
    step4 = read_text("step4_server.py")
    guarded = read_text("tools/guarded_run_sql_tool.py")
    env_example = read_text(".env.example") if (PROJECT_ROOT / ".env.example").exists() else ""
    routes = read_text("vanna_src/src/vanna/servers/fastapi/routes.py")
    frontend_sse = read_text("frontend/src/hooks/useSSE.ts")

    hardcoded_key_files = scan_hardcoded_keys()
    env_files = check_env_files_safe()

    return {
        "remote": run_command(["git", "remote", "-v"]),
        "status_short": run_command(["git", "status", "--short"]),
        "deepseek_config": DEEPSEEK_BASE_URL in step4
        and DEEPSEEK_MODEL in step4
        and "DEEPSEEK_API_KEY" in step4,
        "uses_context_enhancer": "DeterministicMetadataContextEnhancer" in step4,
        "uses_guarded_tool": "GuardedRunSqlTool" in step4,
        "guard_calls_validate": "self.sql_guard.validate" in guarded,
        "guard_blocks_inner": "if not guard_result.passed" in guarded
        and "return ToolResult" in guarded,
        "sse_route_present": "/api/vanna/v2/chat_sse" in routes,
        "frontend_sse_present": "/api/vanna/v2/chat_sse" in frontend_sse,
        "env_example_placeholder_only": "your_deepseek_api_key_here" in env_example
        and not re.search(r"sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}", env_example),
        "hardcoded_key_files": hardcoded_key_files,
        "env_files_safe": env_files["safe"],
        "env_files_tracked": env_files["tracked"],
        "env_files_present_unignored": env_files["present_unignored"],
    }


async def run_guarded_probe(case: dict[str, Any]) -> dict[str, Any]:
    fake_inner = FakeInnerRunSqlTool()
    guarded_tool = GuardedRunSqlTool(inner_tool=fake_inner, sql_guard=SQLGuard())
    context = FakeContext(metadata={"query": case["query"]})
    result = await guarded_tool.execute(context, RunSqlToolArgs(sql=case["probe_sql"]))
    guard_metadata = dict((result.metadata or {}).get("sql_guard", {}))
    return {
        "actual_guard_pass": bool(result.success),
        "inner_called": fake_inner.call_count > 0,
        "guard_reason": guard_metadata.get("reason", result.error or result.result_for_llm),
        "guard_severity": guard_metadata.get("severity", ""),
        "used_tables": guard_metadata.get("used_tables", []),
        "used_columns": guard_metadata.get("used_columns", []),
        "candidate_mismatch": guard_metadata.get("candidate_mismatch", []),
        "blocked_message_present": "SQL Guard blocked execution" in (result.result_for_llm or ""),
    }


async def run_question_probes() -> list[dict[str, Any]]:
    retriever = DeterministicMetadataRetriever()
    results: list[dict[str, Any]] = []

    for case in TEST_CASES:
        candidates = retriever.retrieve(case["query"], top_n=10)
        candidate_tables = [candidate["table_name"] for candidate in candidates]
        matched_columns = []
        for candidate in candidates:
            for column in candidate.get("matched_columns", []):
                matched_columns.append(
                    f"{candidate['table_name']}.{column['column_name']}"
                )

        guard_result = await run_guarded_probe(case)
        expectations: list[bool] = [
            guard_result["actual_guard_pass"] == case["expected_guard_pass"],
            guard_result["inner_called"] == case["expected_inner_called"],
        ]

        if case.get("expected_candidate"):
            expectations.append(candidate_tables[:1] == [case["expected_candidate"]])
        if case.get("forbidden_top1"):
            expectations.append(candidate_tables[:1] != [case["forbidden_top1"]])
        if case.get("expected_any_candidate"):
            expectations.append(
                any(table in candidate_tables for table in case["expected_any_candidate"])
            )
        if case.get("expected_any_column"):
            expectations.append(
                any(
                    any(expected in column for column in matched_columns)
                    for expected in case["expected_any_column"]
                )
            )
        if not case["expected_guard_pass"]:
            expectations.append(not guard_result["inner_called"])

        passed = all(expectations)
        results.append(
            {
                "name": case["name"],
                "query": case["query"],
                "candidate_tables": candidate_tables,
                "matched_columns": matched_columns[:20],
                "generated_sql": "未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard",
                "probe_sql": case["probe_sql"],
                "sql_guard": guard_result,
                "executed_real_sql": False,
                "passed": passed,
                "reason": "符合预期" if passed else "P0 候选、字段或 SQL Guard 结果不符合预期",
            }
        )

    return results


def bool_cn(value: bool) -> str:
    return "是" if value else "否"


def format_list(values: list[str]) -> str:
    return "；".join(values) if values else "无"


def write_report(
    static: dict[str, Any],
    deepseek: dict[str, Any],
    question_results: list[dict[str, Any]],
) -> dict[str, Any]:
    hardcoded_found = bool(static["hardcoded_key_files"])
    static_checks_list = [
        ("deepseek_config", "step4_server.py DeepSeek 官方配置"),
        ("uses_context_enhancer", "DeterministicMetadataContextEnhancer"),
        ("uses_guarded_tool", "GuardedRunSqlTool"),
        ("guard_calls_validate", "SQLGuard.validate 调用"),
        ("guard_blocks_inner", "SQL Guard 失败时拦截 inner tool"),
        ("sse_route_present", "SSE 路由"),
        ("frontend_sse_present", "前端 SSE 调用"),
        ("env_example_placeholder_only", ".env.example 占位符"),
        ("env_files_safe", ".env/.env.local 未跟踪且已忽略"),
    ]
    static_failures = [label for key, label in static_checks_list if not static[key]]
    if hardcoded_found:
        static_failures.append("硬编码 sk- 密钥检测")

    static_total = len(static_checks_list) + 1
    static_failed = len(static_failures)
    static_passed = static_total - static_failed
    question_total = len(question_results)
    failed_questions = [item["name"] for item in question_results if not item["passed"]]
    question_failed = len(failed_questions)
    question_passed = question_total - question_failed
    overall_passed = (
        static_failed == 0 and question_failed == 0 and bool(deepseek["success"])
    )

    lines = [
        "# 主问答端到端最小验证报告",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        static["remote"],
        "```",
        f"- 是否调用 DeepSeek 官方 API：{bool_cn(deepseek['called'])}",
        f"- DeepSeek 调用是否成功：{bool_cn(deepseek['success'])}",
        f"- DeepSeek 调用结果：{deepseek['reason']}",
        f"- API key 来源：{deepseek['key_source']}",
        f"- 是否检测到硬编码密钥：{bool_cn(hardcoded_found)}",
        f"- 硬编码密钥文件（已脱敏）：{format_list(static['hardcoded_key_files'])}",
        f"- .env.example 只包含占位符：{bool_cn(static['env_example_placeholder_only'])}",
        f"- .env/.env.local 是否安全：{bool_cn(static['env_files_safe'])}",
        f"- .env/.env.local 被 git 跟踪：{format_list(static['env_files_tracked'])}",
        f"- .env/.env.local 存在且未忽略：{format_list(static['env_files_present_unignored'])}",
        f"- 是否使用 DeterministicMetadataContextEnhancer：{bool_cn(static['uses_context_enhancer'])}",
        f"- 是否使用 GuardedRunSqlTool：{bool_cn(static['uses_guarded_tool'])}",
        f"- 是否检测到 SQL Guard 执行前拦截：{bool_cn(static['guard_calls_validate'] and static['guard_blocks_inner'])}",
        f"- 静态检查总数：{static_total}",
        f"- 静态检查通过数量：{static_passed}",
        f"- 静态检查失败数量：{static_failed}",
        f"- 静态检查失败列表：{format_list(static_failures)}",
        f"- 问题用例总数：{question_total}",
        f"- 问题用例通过数量：{question_passed}",
        f"- 问题用例失败数量：{question_failed}",
        f"- 问题用例失败列表：{format_list(failed_questions)}",
        f"- 总体验收是否通过：{bool_cn(overall_passed)}",
        "- 是否训练 Vanna：否",
        "- 是否写入 ChromaDB：否",
        "- 是否修改数据库结构：否",
        "- 是否执行 DDL / DML：否",
        "- 是否进入第 2/3/4 级：否",
        "",
        "## 静态链路验证",
        "",
        f"- step4_server.py 使用 DeepSeek 官方 API：{bool_cn(static['deepseek_config'])}",
        f"- .env.example 只包含占位符：{bool_cn(static['env_example_placeholder_only'])}",
        f"- GuardedRunSqlTool 调用 SQLGuard.validate：{bool_cn(static['guard_calls_validate'])}",
        f"- GuardedRunSqlTool 失败时不调用 inner tool：{bool_cn(static['guard_blocks_inner'])}",
        f"- SSE 路由存在：{bool_cn(static['sse_route_present'])}",
        f"- 前端 SSE 调用存在：{bool_cn(static['frontend_sse_present'])}",
        "",
        "## 运行验证说明",
        "",
        "- 未启动 step4_server.py，因为 create_agent() 会初始化 PostgreSQL runner 和 Chroma memory。",
        "- 未调用 Vanna 生成 SQL，避免触发主服务数据库/Chroma 初始化。",
        "- DeepSeek 仅执行一次最小 chat/completions 连通性调用。",
        "- SQL Guard 使用 fake inner run_sql 验证合法 SQL 可进入执行链路、非法 SQL 不进入真实执行。",
        "",
        "## 问题明细",
        "",
    ]

    for item in question_results:
        guard = item["sql_guard"]
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- query：{item['query']}",
                f"- P0 candidate top tables：{', '.join(item['candidate_tables'][:10])}",
                f"- matched_columns：{', '.join(item['matched_columns']) or '无'}",
                f"- generated SQL：{item['generated_sql']}",
                f"- probe_sql：{item['probe_sql']}",
                f"- SQL Guard 结果：passed={guard['actual_guard_pass']}；severity={guard['guard_severity']}；reason={guard['guard_reason']}",
                f"- used_tables：{', '.join(guard['used_tables']) or '无'}",
                f"- used_columns：{', '.join(guard['used_columns']) or '无'}",
                f"- fake inner run_sql 是否被调用：{bool_cn(guard['inner_called'])}",
                f"- 是否执行真实 SQL：{bool_cn(item['executed_real_sql'])}",
                f"- 是否通过：{bool_cn(item['passed'])}",
                f"- reason：{item['reason']}",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return {
        "static_total": static_total,
        "static_passed": static_passed,
        "static_failed": static_failed,
        "static_failures": static_failures,
        "question_total": question_total,
        "question_passed": question_passed,
        "question_failed": question_failed,
        "failed_questions": failed_questions,
        "overall_passed": overall_passed,
        "hardcoded_found": hardcoded_found,
        "env_example_placeholder_only": static["env_example_placeholder_only"],
        "deepseek_called": deepseek["called"],
        "deepseek_success": deepseek["success"],
        "uses_context_enhancer": static["uses_context_enhancer"],
        "uses_guarded_tool": static["uses_guarded_tool"],
        "guard_intercept": static["guard_calls_validate"] and static["guard_blocks_inner"],
        "executed_real_sql": False,
        "executed_ddl_dml": False,
        "trained_vanna": False,
        "wrote_chromadb": False,
        "modified_database_schema": False,
        "entered_level_2_3_4": False,
    }


async def main() -> None:
    static = static_checks()
    deepseek = call_deepseek()
    question_results = await run_question_probes()
    summary = write_report(static, deepseek, question_results)

    print(f"report: {REPORT_PATH}")
    print(f"deepseek_called: {summary['deepseek_called']}")
    print(f"deepseek_success: {summary['deepseek_success']}")
    print(f"hardcoded_key_found: {summary['hardcoded_found']}")
    print(f"env_example_placeholder_only: {summary['env_example_placeholder_only']}")
    print(f"static_total: {summary['static_total']}")
    print(f"static_passed: {summary['static_passed']}")
    print(f"static_failed: {summary['static_failed']}")
    print(f"static_failures: {format_list(summary['static_failures'])}")
    print(f"question_total: {summary['question_total']}")
    print(f"question_passed: {summary['question_passed']}")
    print(f"question_failed: {summary['question_failed']}")
    print(f"failed_questions: {format_list(summary['failed_questions'])}")
    print(f"overall_passed: {summary['overall_passed']}")


if __name__ == "__main__":
    asyncio.run(main())

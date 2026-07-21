from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "sql_guard_execution_chain_test_result.md"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.sql_guard import SQLGuard


TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "Q9 error blocks before inner",
        "metadata": {"query": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_success": False,
        "expected_inner_called": False,
        "expected_blocked": True,
        "expected_query_source": "metadata.query",
    },
    {
        "name": "original_question metadata blocks before inner",
        "metadata": {"original_question": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_success": False,
        "expected_inner_called": False,
        "expected_blocked": True,
        "expected_query_source": "metadata.original_question",
    },
    {
        "name": "missing query threshold fallback blocks",
        "metadata": {},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_success": False,
        "expected_inner_called": False,
        "expected_blocked": True,
        "expected_query_source": "missing",
    },
    {
        "name": "user metadata query blocks before inner",
        "metadata": {},
        "user_metadata": {"query": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_success": False,
        "expected_inner_called": False,
        "expected_blocked": True,
        "expected_query_source": "user.metadata.query",
    },
    {
        "name": "legal hour trend passes inner",
        "metadata": {"query": "某站点水质小时变化趋势"},
        "sql": "SELECT monitor_time, m2_value FROM wm_waterquality_hour_records WHERE station_id = 1393 ORDER BY monitor_time LIMIT 50",
        "expected_success": True,
        "expected_inner_called": True,
        "expected_blocked": False,
        "expected_query_source": "metadata.query",
    },
    {
        "name": "threshold trend request blocks alternate day records SQL",
        "metadata": {"query": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT monitor_time, water_quality_level FROM wm_waterquality_day_records ORDER BY monitor_time LIMIT 50",
        "expected_success": False,
        "expected_inner_called": False,
        "expected_blocked": True,
        "expected_query_source": "metadata.query",
    },
    {
        "name": "candidate mismatch warning does not block",
        "metadata": {"query": "查询区域编码和区域名称"},
        "sql": "SELECT area_code, area_name FROM rs_outlet GROUP BY area_code, area_name LIMIT 50",
        "expected_success": True,
        "expected_inner_called": True,
        "expected_blocked": False,
        "expected_query_source": "metadata.query",
        "expected_severity": "warning",
    },
]


class FakeContext(BaseModel):
    metadata: dict[str, Any]
    user: Any | None = None


class FakeUser(BaseModel):
    metadata: dict[str, Any]


class FakeInnerRunSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self) -> None:
        self.call_count = 0

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "Fake run_sql for SQL Guard execution chain test"

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


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    inner = FakeInnerRunSqlTool()
    tool = GuardedRunSqlTool(inner_tool=inner, sql_guard=SQLGuard())
    context = FakeContext(
        metadata=case["metadata"],
        user=FakeUser(metadata=case["user_metadata"]) if "user_metadata" in case else None,
    )
    result = await tool.execute(context, RunSqlToolArgs(sql=case["sql"]))
    metadata = dict(result.metadata or {})
    guard = dict(metadata.get("sql_guard") or {})
    inner_called = inner.call_count > 0
    passed = (
        result.success == case["expected_success"]
        and inner_called == case["expected_inner_called"]
        and metadata.get("blocked_by_sql_guard") == case["expected_blocked"]
        and metadata.get("query_source") == case["expected_query_source"]
        and guard.get("severity") == case.get("expected_severity", guard.get("severity"))
    )
    return {
        "name": case["name"],
        "sql": case["sql"],
        "query": (
            case["metadata"].get("query")
            or case["metadata"].get("original_question")
            or case.get("user_metadata", {}).get("query")
            or case.get("user_metadata", {}).get("original_question")
            or ""
        ),
        "success": result.success,
        "inner_called": inner_called,
        "blocked_by_sql_guard": metadata.get("blocked_by_sql_guard"),
        "query_source": metadata.get("query_source"),
        "guard": guard,
        "passed": passed,
        "reason": "符合预期" if passed else "执行链路拦截或 metadata 不符合预期",
    }


async def run_tests() -> list[dict[str, Any]]:
    return [await run_case(case) for case in TEST_CASES]


def write_report(results: list[dict[str, Any]]) -> None:
    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    lines = [
        "# SQL Guard 执行前拦截链路测试报告",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        run_command(["git", "remote", "-v"]),
        "```",
        f"- 当前 commit：{run_command(['git', 'rev-parse', 'HEAD'])}",
        "- 初始 git status --short：",
        "```text",
        run_command(["git", "status", "--short"]) or "clean",
        "```",
        "- 根因说明：真实 ToolContext metadata 未携带原始用户问题，query_source=missing 时 SQLGuard 无法触发水质趋势禁止 threshold 的业务规则；Agent 会在一次 tool error 后继续尝试后续 SQL，导致同一高风险问题仍可能执行真实 SQL。",
        "- 修复点说明：step4_server.py 将 RequestContext.metadata 写入 User.metadata；GuardedRunSqlTool 从 user.metadata 提取原始 query，记录 query_source，并对 threshold+水质趋势原始问题执行整轮 hard block；query 缺失时仍保留 threshold 趋势 SQL 兜底阻断；passed=False 时不调用 inner tool。",
        "- 是否修改 SQLGuard：否",
        "- 是否修改 P0：否",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否写入 ChromaDB：否",
        "- 是否修改正式 vanna_data：否",
        "- 是否连接数据库：否",
        "- 是否执行真实 SQL：否",
        f"- 测试总数：{len(results)}",
        f"- 通过数量：{passed}",
        f"- 失败数量：{failed}",
        f"- 失败用例列表：{', '.join(item['name'] for item in results if not item['passed']) or '无'}",
        "",
        "## 明细",
        "",
    ]
    for item in results:
        guard = item["guard"]
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- query：{item['query'] or 'missing'}",
                f"- sql：`{item['sql']}`",
                f"- success：{item['success']}",
                f"- blocked_by_sql_guard：{item['blocked_by_sql_guard']}",
                f"- inner_called：{item['inner_called']}",
                f"- query_source：{item['query_source']}",
                f"- SQL Guard result：passed={guard.get('passed')}；severity={guard.get('severity')}；reason={guard.get('reason')}",
                f"- pass/fail：{'pass' if item['passed'] else 'fail'}",
                f"- reason：{item['reason']}",
                "",
            ]
        )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results = asyncio.run(run_tests())
    write_report(results)
    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    print(f"测试总数: {len(results)}")
    print(f"通过数量: {passed}")
    print(f"失败数量: {failed}")
    print(f"报告: {REPORT_PATH}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

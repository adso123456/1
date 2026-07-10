from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult

from tools.guarded_run_sql_tool import GuardedRunSqlTool
from tools.sql_guard import SQLGuard


REPORT_PATH = CURRENT_DIR / "guarded_run_sql_tool_test_result.md"

TEST_CASES: list[dict[str, Any]] = [
    {
        "name": "合法水质趋势 SQL",
        "query": "某地区某时间段水质变化趋势",
        "sql": "SELECT * FROM wm_waterquality_day_records LIMIT 10",
        "expected_pass": True,
        "expected_inner_called": True,
    },
    {
        "name": "非法水质趋势阈值表",
        "query": "某地区某时间段水质变化趋势",
        "sql": "SELECT * FROM wm_waterquality_threshold",
        "expected_pass": False,
        "expected_inner_called": False,
        "expected_query_source": "metadata.query",
    },
    {
        "name": "metadata original_question 拦截阈值表",
        "metadata": {"original_question": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_pass": False,
        "expected_inner_called": False,
        "expected_query_source": "metadata.original_question",
    },
    {
        "name": "query 缺失时阈值趋势兜底拦截",
        "metadata": {},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_pass": False,
        "expected_inner_called": False,
        "expected_query_source": "missing",
    },
    {
        "name": "user metadata query 拦截阈值表",
        "metadata": {},
        "user_metadata": {"query": "查询 wm_waterquality_threshold 中的水质趋势"},
        "sql": "SELECT * FROM wm_waterquality_threshold LIMIT 50",
        "expected_pass": False,
        "expected_inner_called": False,
        "expected_query_source": "user.metadata.query",
    },
    {
        "name": "阈值趋势问题改查日记录也阻断",
        "query": "查询 wm_waterquality_threshold 中的水质趋势",
        "sql": "SELECT monitor_time, water_quality_level FROM wm_waterquality_day_records ORDER BY monitor_time LIMIT 50",
        "expected_pass": False,
        "expected_inner_called": False,
        "expected_query_source": "metadata.query",
    },
    {
        "name": "非法排污口溯源基础表",
        "query": "排污口溯源",
        "sql": "SELECT * FROM rs_outlet",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "系统表",
        "query": "查看系统表",
        "sql": "SELECT * FROM information_schema.tables",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "DDL",
        "query": "删除表",
        "sql": "DROP TABLE rs_outlet",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "DML",
        "query": "更新排污口",
        "sql": "UPDATE rs_outlet SET outlet_name='x'",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "未知字段",
        "query": "未知字段",
        "sql": "SELECT unknown_column FROM rs_outlet",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "未知表",
        "query": "未知表",
        "sql": "SELECT * FROM unknown_table",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "合法 JOIN ON",
        "query": "合法 JOIN ON",
        "sql": "SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.id",
        "expected_pass": True,
        "expected_inner_called": True,
    },
    {
        "name": "非法 JOIN ON",
        "query": "非法 JOIN ON",
        "sql": "SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.station_id",
        "expected_pass": False,
        "expected_inner_called": False,
    },
    {
        "name": "candidate mismatch warning 不阻断",
        "query": "查询区域编码和区域名称",
        "sql": "SELECT area_code, area_name FROM rs_outlet GROUP BY area_code, area_name LIMIT 50",
        "expected_pass": True,
        "expected_inner_called": True,
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
        self.called_sql: list[str] = []

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "Fake run_sql tool for SQL Guard tests"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: Any, args: RunSqlToolArgs) -> ToolResult:
        self.call_count += 1
        self.called_sql.append(args.sql)
        return ToolResult(
            success=True,
            result_for_llm="fake inner tool executed",
            metadata={"fake_inner_tool": True},
        )


async def _run_one(case: dict[str, Any]) -> dict[str, Any]:
    fake_inner_tool = FakeInnerRunSqlTool()
    guarded_tool = GuardedRunSqlTool(
        inner_tool=fake_inner_tool,
        sql_guard=SQLGuard(),
    )
    context = FakeContext(
        metadata=case.get("metadata", {"query": case.get("query", "")}),
        user=FakeUser(metadata=case["user_metadata"]) if "user_metadata" in case else None,
    )
    result = await guarded_tool.execute(context, RunSqlToolArgs(sql=case["sql"]))
    inner_called = fake_inner_tool.call_count > 0
    actual_pass = result.success

    blocked_message_present = (
        _blocked_message_has_required_fields(result.result_for_llm)
        if not case["expected_pass"]
        else True
    )
    has_guard_metadata = "sql_guard" in result.metadata
    query_source_matches = result.metadata.get("query_source") == case.get(
        "expected_query_source",
        "metadata.query",
    )
    severity_matches = result.metadata.get("sql_guard", {}).get("severity") == case.get(
        "expected_severity",
        result.metadata.get("sql_guard", {}).get("severity"),
    )
    wrong_inner_call = inner_called != case["expected_inner_called"]
    passed = (
        actual_pass == case["expected_pass"]
        and inner_called == case["expected_inner_called"]
        and blocked_message_present
        and has_guard_metadata
        and query_source_matches
        and severity_matches
    )

    return {
        "name": case["name"],
        "query": case.get("query") or case.get("metadata", {}).get("original_question", ""),
        "sql": case["sql"],
        "expected_pass": case["expected_pass"],
        "actual_pass": actual_pass,
        "expected_inner_called": case["expected_inner_called"],
        "actual_inner_called": inner_called,
        "wrong_inner_call": wrong_inner_call,
        "result_for_llm": result.result_for_llm,
        "guard_metadata": result.metadata.get("sql_guard", {}),
        "query_source": result.metadata.get("query_source", ""),
        "pass": passed,
        "reason": "符合预期" if passed else "放行/拦截或返回结构不符合预期",
    }


async def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results = [await _run_one(case) for case in TEST_CASES]
    passed_count = sum(1 for result in results if result["pass"])
    legal_results = [result for result in results if result["expected_pass"]]
    illegal_results = [result for result in results if not result["expected_pass"]]
    fake_inner_called_count = sum(1 for result in results if result["actual_inner_called"])
    wrong_inner_call_count = sum(1 for result in results if result["wrong_inner_call"])

    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": [result["name"] for result in results if not result["pass"]],
        "legal_sql_allowed": sum(
            1
            for result in legal_results
            if result["actual_pass"] and result["actual_inner_called"]
        ),
        "illegal_sql_blocked": sum(
            1
            for result in illegal_results
            if not result["actual_pass"] and not result["actual_inner_called"]
        ),
        "fake_inner_called_count": fake_inner_called_count,
        "wrong_inner_call_count": wrong_inner_call_count,
        "modified_step4_server": True,
        "modified_run_sql_source": False,
        "modified_api_routes": False,
        "modified_frontend": False,
        "called_vanna": False,
        "executed_real_sql": False,
        "connected_database": False,
        "trained_vanna": False,
        "modified_chromadb": False,
        "entered_level_2_3_4": False,
    }
    return results, summary


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _blocked_message_has_required_fields(message: str) -> bool:
    required_parts = [
        "SQL Guard blocked execution",
        "severity:",
        "used_tables:",
        "used_columns:",
        "unknown_tables:",
        "unknown_columns:",
        "forbidden_operations:",
        "candidate_mismatch:",
        "reason:",
    ]
    return all(part in message for part in required_parts)


def write_report(results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# GuardedRunSqlTool 测试结果",
        "",
        "## 汇总",
        "",
        f"- 测试用例总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 合法 SQL 放行数量：{summary['legal_sql_allowed']}",
        f"- 非法 SQL 拦截数量：{summary['illegal_sql_blocked']}",
        f"- fake inner tool 被调用次数：{summary['fake_inner_called_count']}",
        f"- fake inner tool 被错误调用次数：{summary['wrong_inner_call_count']}",
        f"- 是否修改 step4_server.py：{'是' if summary['modified_step4_server'] else '否'}",
        f"- 是否修改 RunSqlTool 源码：{'是' if summary['modified_run_sql_source'] else '否'}",
        f"- 是否修改 API 路由：{'是' if summary['modified_api_routes'] else '否'}",
        f"- 是否修改前端：{'是' if summary['modified_frontend'] else '否'}",
        f"- 是否调用 Vanna：{'是' if summary['called_vanna'] else '否'}",
        f"- 是否执行真实 SQL：{'是' if summary['executed_real_sql'] else '否'}",
        f"- 是否连接数据库：{'是' if summary['connected_database'] else '否'}",
        f"- 是否训练 Vanna：{'是' if summary['trained_vanna'] else '否'}",
        f"- 是否修改 ChromaDB：{'是' if summary['modified_chromadb'] else '否'}",
        f"- 是否进入第 2/3/4 级：{'是' if summary['entered_level_2_3_4'] else '否'}",
        "",
        "## 明细",
        "",
    ]

    for index, result in enumerate(results, start=1):
        guard_metadata = result["guard_metadata"]
        lines.extend(
            [
                f"### {index}. {result['name']}",
                "",
                f"- query：{result['query']}",
                f"- sql：`{result['sql']}`",
                f"- expected_pass：{_bool_text(result['expected_pass'])}",
                f"- actual_pass：{_bool_text(result['actual_pass'])}",
                f"- expected_inner_called：{_bool_text(result['expected_inner_called'])}",
                f"- actual_inner_called：{_bool_text(result['actual_inner_called'])}",
                f"- query_source：{result['query_source'] or 'missing'}",
                f"- severity：{guard_metadata.get('severity', 'unknown')}",
                f"- used_tables：{', '.join(guard_metadata.get('used_tables', [])) or '无'}",
                f"- unknown_tables：{', '.join(guard_metadata.get('unknown_tables', [])) or '无'}",
                f"- unknown_columns：{', '.join(guard_metadata.get('unknown_columns', [])) or '无'}",
                f"- forbidden_operations：{', '.join(guard_metadata.get('forbidden_operations', [])) or '无'}",
                f"- candidate_mismatch：{', '.join(guard_metadata.get('candidate_mismatch', [])) or '无'}",
                f"- reason：{guard_metadata.get('reason', result['reason'])}",
                f"- pass/fail：{'pass' if result['pass'] else 'fail'}",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = asyncio.run(run_tests())
    write_report(results, summary)

    print(f"测试用例总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"合法 SQL 放行数量: {summary['legal_sql_allowed']}")
    print(f"非法 SQL 拦截数量: {summary['illegal_sql_blocked']}")
    print(f"fake inner tool 被调用次数: {summary['fake_inner_called_count']}")
    print(f"fake inner tool 被错误调用次数: {summary['wrong_inner_call_count']}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

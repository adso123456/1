"""GuardedRunSqlTool 可选 JSONL trace 的纯单元测试。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Type

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.sql_guard import SQLGuardResult


SECRET = "sk-trace-test-secret-should-never-appear"


class FakeContext(BaseModel):
    metadata: dict[str, Any]
    user: Any | None = None


class FakeInnerTool(Tool[RunSqlToolArgs]):
    call_count: int = 0

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "fake"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: Any, args: RunSqlToolArgs) -> ToolResult:
        self.call_count += 1
        return ToolResult(success=True, result_for_llm="ok", metadata={})


class FakeSQLGuard:
    def validate(self, sql: str, query: str = "") -> SQLGuardResult:
        blocked = "unknown_table" in sql
        return SQLGuardResult(
            passed=not blocked,
            severity="error" if blocked else "ok",
            used_tables=["unknown_table" if blocked else "ad_dict"],
            used_columns=[] if blocked else ["ad_dict.list_type"],
            unknown_tables=["unknown_table"] if blocked else [],
            unknown_columns=["unknown_table.bad_column"] if blocked else [],
            forbidden_operations=[],
            candidate_mismatch=[],
            reason="存在未知表：unknown_table" if blocked else "SQL 静态校验通过",
        )


async def run() -> None:
    previous = os.environ.pop("VANNA_SQL_GUARD_TRACE_PATH", None)
    try:
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "trace.jsonl"
            context = FakeContext(
                metadata={"query": "查询数据字典", "authorization": SECRET}
            )
            inner = FakeInnerTool()
            tool = GuardedRunSqlTool(inner_tool=inner, sql_guard=FakeSQLGuard())

            await tool.execute(
                context, RunSqlToolArgs(sql="SELECT list_type FROM ad_dict LIMIT 1")
            )
            assert not trace_path.exists()

            os.environ["VANNA_SQL_GUARD_TRACE_PATH"] = str(trace_path.resolve())
            await tool.execute(
                context, RunSqlToolArgs(sql="SELECT list_type FROM ad_dict LIMIT 1")
            )
            await tool.execute(
                context, RunSqlToolArgs(sql="SELECT bad_column FROM unknown_table")
            )
            context.metadata["sql_guard_hard_blocked"] = True
            await tool.execute(
                context, RunSqlToolArgs(sql="SELECT other_column FROM unknown_table")
            )

            lines = trace_path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            assert len(records) == 3
            assert records[0]["attempted_sql"] == "SELECT list_type FROM ad_dict LIMIT 1"
            assert records[0]["guard_passed"] is True
            assert records[0]["blocked_by_sql_guard"] is False
            assert records[1]["attempted_sql"] == "SELECT bad_column FROM unknown_table"
            assert records[1]["guard_passed"] is False
            assert records[1]["unknown_tables"] == ["unknown_table"]
            assert records[1]["unknown_columns"] == ["unknown_table.bad_column"]
            assert records[1]["hard_blocked_before_validation"] is False
            assert records[2]["hard_blocked_before_validation"] is True
            assert SECRET not in trace_path.read_text(encoding="utf-8")
    finally:
        if previous is None:
            os.environ.pop("VANNA_SQL_GUARD_TRACE_PATH", None)
        else:
            os.environ["VANNA_SQL_GUARD_TRACE_PATH"] = previous


if __name__ == "__main__":
    asyncio.run(run())
    print("GUARDED_RUN_SQL_TRACE_TEST: PASS")

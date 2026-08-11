from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Type

from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.mysql_sql_guard import MySQLSQLGuard
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolResult


class FakeContext(BaseModel):
    metadata: dict[str, Any] = {}
    user: Any | None = None


class FakeInnerRunSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self) -> None:
        self.call_count = 0

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "SQLGuard 中文标识符回归用假 Runner"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: Any, args: RunSqlToolArgs) -> ToolResult:
        self.call_count += 1
        return ToolResult(success=True, result_for_llm="fake runner executed")


def _write_metadata(path: Path) -> None:
    rows = [
        {
            "table": "rs_outlet",
            "table_comment": "排污口信息表",
            "column": "id",
            "type": "bigint",
            "comment": "主键",
        },
        {
            "table": "rs_outlet",
            "table_comment": "排污口信息表",
            "column": "area_code",
            "type": "varchar(32)",
            "comment": "行政区代码",
        },
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


async def main() -> int:
    rejected_sql = (
        "SELECT * FROM 排污口;",
        "SELECT * FROM `排污口`;",
        "SELECT 行政区, COUNT(*) FROM 排污口 GROUP BY 行政区;",
        "SELECT * FROM analytics.排污口;",
        "SELECT * FROM `analytics`.`排污口`;",
    )
    valid_sql = (
        "SELECT COUNT(*) FROM rs_outlet;",
        "SELECT t.area_code AS 行政区 FROM rs_outlet AS t;",
    )

    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory)
        metadata_path = temporary_path / "metadata.json"
        os.environ["VANNA_SQL_GUARD_TRACE_PATH"] = str(
            temporary_path / "sql_guard.jsonl"
        )
        _write_metadata(metadata_path)

        failures: list[str] = []
        for sql in rejected_sql:
            inner = FakeInnerRunSqlTool()
            tool = GuardedRunSqlTool(inner, MySQLSQLGuard(metadata_path))
            result = await tool.execute(FakeContext(), RunSqlToolArgs(sql=sql))
            guard = dict((result.metadata or {}).get("sql_guard") or {})
            passed = (
                result.success is False
                and inner.call_count == 0
                and guard.get("used_tables") == ["排污口"]
                and guard.get("unknown_tables") == ["排污口"]
            )
            print(f"{'PASS' if passed else 'FAIL'}: reject {sql}")
            if not passed:
                failures.append(sql)

        for sql in valid_sql:
            inner = FakeInnerRunSqlTool()
            tool = GuardedRunSqlTool(inner, MySQLSQLGuard(metadata_path))
            result = await tool.execute(FakeContext(), RunSqlToolArgs(sql=sql))
            guard = dict((result.metadata or {}).get("sql_guard") or {})
            passed = (
                result.success is True
                and inner.call_count == 1
                and guard.get("used_tables") == ["rs_outlet"]
                and guard.get("unknown_tables") == []
            )
            print(f"{'PASS' if passed else 'FAIL'}: allow {sql}")
            if not passed:
                failures.append(sql)

    print(f"TOTAL=7 PASS={7 - len(failures)} FAIL={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""mysql-lzh-monitor 只读 Runner 的本地真实连接冒烟测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.mysql_runner import ReadOnlyMySQLRunner
from backend.mysql_runtime_factory import create_mysql_runtime
from backend.mysql_sql_guard import MySQLSQLGuard
from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.schema_preserving_sql import SchemaPreservingRunSqlTool
from config.data_sources import build_mysql_data_source_config
from vanna.capabilities.sql_runner import RunSqlToolArgs


async def main() -> int:
    config = build_mysql_data_source_config()
    memory_existed_before = config.memory_path.exists()
    runtime = create_mysql_runtime(config)
    runner = runtime.runner

    station_rows = await runner.run_sql(
        RunSqlToolArgs(
            sql=(
                "SELECT id, station_code, station_name "
                "FROM wm_station_info LIMIT 5"
            )
        ),
        None,
    )
    empty_rows = await runner.run_sql(
        RunSqlToolArgs(
            sql=(
                "SELECT id, station_code, station_name "
                "FROM wm_station_info WHERE 1 = 0 LIMIT 5"
            )
        ),
        None,
    )
    transaction_mode = await runner.run_sql(
        RunSqlToolArgs(
            sql="SELECT @@transaction_read_only AS transaction_read_only"
        ),
        None,
    )
    mysql_function_sql = (
        "SELECT DATE(monitor_time) AS monitor_date, "
        "YEAR(monitor_time) AS monitor_year, "
        "MONTH(monitor_time) AS monitor_month, "
        "DATE_FORMAT(monitor_time, '%Y-%m') AS monitor_year_month, "
        "IFNULL(m2_value, 0) AS m2_value "
        "FROM wm_waterquality_hour_records LIMIT 1"
    )
    mysql_function_guard = runtime.sql_guard.validate(mysql_function_sql)
    mysql_function_rows = await runner.run_sql(
        RunSqlToolArgs(sql=mysql_function_sql),
        None,
    )
    excluded_guard_results = {
        field: runtime.sql_guard.validate(
            f"SELECT {field} FROM rs_pollutant_info LIMIT 5"
        )
        for field in ("geom", "centre", "contact", "phone")
    }

    checks = (
        (
            "mysql-lzh-monitor 真实 Runtime 创建成功",
            runtime.source_id == "mysql-lzh-monitor"
            and isinstance(runner, ReadOnlyMySQLRunner)
            and isinstance(runtime.sql_guard, MySQLSQLGuard),
        ),
        (
            "MySQL Metadata 与 Memory 路径独立",
            "mysql-lzh-monitor" in config.metadata_path.parts
            and "mysql-lzh-monitor" in config.memory_path.parts
            and config.metadata_path != config.memory_path,
        ),
        (
            "MySQL Runner 接入现有 DataFrameComponent 工具链",
            isinstance(
                runtime.agent.tool_registry._tools.get("run_sql"),
                GuardedRunSqlTool,
            )
            and isinstance(
                runtime.agent.tool_registry._tools["run_sql"].inner_tool,
                SchemaPreservingRunSqlTool,
            )
            and runtime.agent.tool_registry._tools[
                "run_sql"
            ].inner_tool.sql_runner
            is runner,
        ),
        (
            "未创建或写入正式 MySQL Chroma",
            config.memory_path.exists() == memory_existed_before,
        ),
        (
            "带 LIMIT 的站点查询成功",
            len(station_rows) <= 5
            and list(station_rows.columns)
            == ["id", "station_code", "station_name"],
        ),
        (
            "零行查询保留列结构",
            empty_rows.empty
            and list(empty_rows.columns)
            == ["id", "station_code", "station_name"],
        ),
        (
            "数据库事务为只读",
            int(transaction_mode.iloc[0]["transaction_read_only"]) == 1,
        ),
        (
            "MySQL 日期函数通过 Guard 并可执行",
            mysql_function_guard.passed
            and len(mysql_function_rows) <= 1
            and list(mysql_function_rows.columns)
            == [
                "monitor_date",
                "monitor_year",
                "monitor_month",
                "monitor_year_month",
                "m2_value",
            ],
        ),
        (
            "四个排除字段被 Guard 拒绝为未知字段",
            all(
                not result.passed
                and field in result.unknown_columns
                for field, result in excluded_guard_results.items()
            ),
        ),
    )
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"STATION_QUERY_ROW_COUNT: {len(station_rows)}")
    print(f"STATION_QUERY_COLUMNS: {list(station_rows.columns)}")
    print(f"EMPTY_QUERY_COLUMNS: {list(empty_rows.columns)}")
    failed = [name for name, passed in checks if not passed]
    print(f"TOTAL={len(checks)} PASS={len(checks) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

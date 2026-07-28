"""MySQL 配置、Runtime、SQLGuard 与只读 Runner 的离线验收。"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backend.prompts as prompts_module
from backend.data_source_registry import build_current_data_source_registry
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.mysql_runner import ReadOnlyMySQLRunner
from backend.mysql_runtime_factory import (
    MySQLRuntimeBuilders,
    create_mysql_runtime,
)
from backend.mysql_sql_guard import MySQLSQLGuard
from backend.sql_guard import SQL_FUNCTIONS
from config.data_sources import build_mysql_data_source_config
from vanna.capabilities.sql_runner import RunSqlToolArgs


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def builders(self) -> MySQLRuntimeBuilders:
        def build(name):
            def builder(value, *rest):
                self.calls.append((name, value))
                return object()

            return builder

        return MySQLRuntimeBuilders(
            runner_builder=build("runner"),
            memory_builder=build("memory"),
            metadata_retriever_builder=build("metadata"),
            sql_guard_builder=build("guard"),
            agent_builder=build("agent"),
        )


class FakeCursor:
    description = (("id",), ("name",))

    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str) -> None:
        self.sql.append(sql)

    def fetchall(self):
        return []

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()
        self.rolled_back = False
        self.closed = False

    def ping(self, reconnect: bool) -> None:
        assert reconnect is True

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, condition: bool) -> None:
        results.append((name, bool(condition)))

    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name).resolve()
        mysql_scope = root / "mysql.json"
        postgres_scope = root / "postgres.json"
        mysql_scope.write_text(
            json.dumps(
                {
                    "datasource_id": "mysql-lzh-monitor",
                    "dialect": "mysql",
                    "database": "lzh_monitor",
                    "approved_tables": [f"table_{index}" for index in range(18)],
                }
            ),
            encoding="utf-8",
        )
        postgres_scope.write_text(
            json.dumps(
                {
                    "datasource_id": "postgresql-main",
                    "dialect": "postgresql",
                }
            ),
            encoding="utf-8",
        )
        environ = {
            "DB_USER": "postgres",
            "DB_PASSWORD": "postgres-secret",
            "MYSQL_USER": "mysql",
            "MYSQL_PASSWORD": "mysql-secret",
            "MYSQL_METADATA_INDEX_PATH": str(root / "mysql-metadata.json"),
            "MYSQL_VANNA_DATA_DIR": str(root / "mysql-memory"),
        }
        config = build_mysql_data_source_config(
            environ=environ,
            scope_path=mysql_scope,
        )
        check(
            "MySQL 默认数据库与端口正确",
            config.source_id == "mysql-lzh-monitor"
            and config.connection_settings["database"] == "lzh_monitor"
            and config.connection_settings["port"] == 3307,
        )
        registry = build_current_data_source_registry(
            environ=environ,
            scope_path=postgres_scope,
            include_mysql=True,
            mysql_scope_path=mysql_scope,
        )
        check(
            "Registry 同时包含 PostgreSQL 与 MySQL",
            registry.source_ids == ("mysql-lzh-monitor", "postgresql-main"),
        )
        factory_calls: list[str] = []

        def make_runtime(source_config):
            factory_calls.append(source_config.database_type)
            return DataSourceRuntime(
                config=source_config,
                runner=object(),
                memory=object(),
                metadata_retriever=object(),
                sql_guard=object(),
                agent=object(),
            )

        manager = DataSourceRuntimeManager(
            registry,
            {
                "postgresql": make_runtime,
                "mysql": make_runtime,
            },
        )
        mysql_runtime = manager.require("mysql-lzh-monitor")
        check(
            "MySQL 查询路由不调用 PostgreSQL 工厂",
            mysql_runtime.database_type == "mysql"
            and factory_calls == ["mysql"],
        )
        postgresql_runtime = manager.require("postgresql-main")
        check(
            "PostgreSQL 原数据源仍可创建",
            postgresql_runtime.database_type == "postgresql"
            and factory_calls == ["mysql", "postgresql"],
        )

        recorder = Recorder()
        runtime = create_mysql_runtime(
            config,
            builders=recorder.builders(),
            environ={"DEEPSEEK_API_KEY": "offline"},
        )
        check(
            "mysql-lzh-monitor 可创建独立 Runtime",
            runtime.source_id == "mysql-lzh-monitor"
            and [name for name, _ in recorder.calls]
            == ["runner", "memory", "metadata", "guard", "agent"],
        )

        metadata_path = root / "guard.json"
        metadata_path.write_text(
            json.dumps(
                [
                    {"table": "wm_station_info", "column": "id"},
                    {"table": "wm_station_info", "column": "station_name"},
                    {
                        "table": "wm_waterquality_hour_records",
                        "column": "monitor_time",
                    },
                    {
                        "table": "wm_waterquality_hour_records",
                        "column": "m2_value",
                    },
                    {"table": "rs_pollutant_info", "column": "id"},
                ]
            ),
            encoding="utf-8",
        )
        guard = MySQLSQLGuard(index_path=metadata_path)
        check(
            "只读 SELECT 通过",
            guard.validate(
                "SELECT id, station_name FROM wm_station_info LIMIT 5"
            ).passed,
        )
        mysql_queries = (
            "SELECT DATE(monitor_time) "
            "FROM wm_waterquality_hour_records LIMIT 5",
            "SELECT YEAR(monitor_time), MONTH(monitor_time) "
            "FROM wm_waterquality_hour_records LIMIT 5",
            "SELECT DATE_FORMAT(monitor_time, '%Y-%m') "
            "FROM wm_waterquality_hour_records LIMIT 5",
            "SELECT IFNULL(m2_value, 0) "
            "FROM wm_waterquality_hour_records LIMIT 5",
            "SELECT ROUND(AVG(m2_value), 2), COUNT(m2_value), "
            "SUM(m2_value), MAX(m2_value), MIN(m2_value), "
            "COALESCE(m2_value, 0) "
            "FROM wm_waterquality_hour_records LIMIT 5",
        )
        check(
            "MySQL 日期与聚合函数通过",
            all(guard.validate(sql).passed for sql in mysql_queries),
        )
        check(
            "MySQL 专用函数未写入 PostgreSQL 全局函数集合",
            "date_format" not in SQL_FUNCTIONS
            and "ifnull" not in SQL_FUNCTIONS
            and "date_trunc" in SQL_FUNCTIONS,
        )
        check(
            "MySQL 反引号标识符通过",
            guard.validate(
                "SELECT `station_name` FROM `wm_station_info` LIMIT 5"
            ).passed,
        )
        excluded_fields = ("geom", "centre", "contact", "phone")
        check(
            "排除字段被 MySQL SQLGuard 识别为未知字段",
            all(
                not guard.validate(
                    f"SELECT {field} FROM rs_pollutant_info LIMIT 5"
                ).passed
                for field in excluded_fields
            ),
        )
        for operation, sql in (
            ("INSERT", "INSERT INTO wm_station_info(id) VALUES (1)"),
            ("UPDATE", "UPDATE wm_station_info SET id=1"),
            ("DELETE", "DELETE FROM wm_station_info"),
            ("DDL", "DROP TABLE wm_station_info"),
            (
                "多语句",
                "SELECT id FROM wm_station_info; SELECT station_name FROM wm_station_info",
            ),
        ):
            check(f"{operation} 被 SQLGuard 拒绝", not guard.validate(sql).passed)

        runner = ReadOnlyMySQLRunner(
            host="offline",
            port=3307,
            database="lzh_monitor",
            user="offline",
            password="secret",
        )
        fake_connection = FakeConnection()
        runner.pymysql.connect = lambda **kwargs: fake_connection
        dataframe = asyncio.run(
            runner.run_sql(
                RunSqlToolArgs(
                    sql="SELECT id, name FROM wm_station_info WHERE 1=0"
                ),
                None,
            )
        )
        check(
            "零行查询保留列结构",
            dataframe.empty and list(dataframe.columns) == ["id", "name"],
        )
        check(
            "Runner 使用只读事务并回滚",
            fake_connection.cursor_value.sql[0]
            == "SET SESSION TRANSACTION READ ONLY"
            and fake_connection.cursor_value.sql[1]
            == "START TRANSACTION READ ONLY"
            and fake_connection.rolled_back
            and fake_connection.closed,
        )

        original_trace_writer = prompts_module.write_trace_json
        prompts_module.write_trace_json = lambda *args, **kwargs: None
        try:
            mysql_prompt = asyncio.run(
                prompts_module.OptimizedSystemPromptBuilder(
                    sql_dialect="mysql"
                ).build_system_prompt(None, [])
            )
            postgresql_prompt = asyncio.run(
                prompts_module.OptimizedSystemPromptBuilder().build_system_prompt(
                    None, []
                )
            )
        finally:
            prompts_module.write_trace_json = original_trace_writer
        check(
            "MySQL Prompt 使用 MySQL 方言且无 PostgreSQL 专用表规则",
            "MYSQL DIALECT" in mysql_prompt
            and "MySQL 8 compatible SQL" in mysql_prompt
            and "rs_outlet_monitor_v2" not in mysql_prompt
            and "sampling_time" not in mysql_prompt
            and "pg_catalog" not in mysql_prompt,
        )
        check(
            "PostgreSQL Prompt 保持既有专用规则",
            "rs_outlet_monitor_v2" in postgresql_prompt
            and "sampling_time" in postgresql_prompt
            and "pg_catalog" in postgresql_prompt,
        )

        generated_rows = json.loads(
            (
                PROJECT_ROOT
                / "agent_data"
                / "mysql-lzh-monitor"
                / "column_metadata_index.json"
            ).read_text(encoding="utf-8")
        )
        generated_columns = {
            (row["table"], row["column"]) for row in generated_rows
        }
        check(
            "生成 Metadata 保留 18 表",
            len({row["table"] for row in generated_rows}) == 18,
        )
        check(
            "四个排除字段未写入生成 Metadata",
            all(
                ("rs_pollutant_info", field) not in generated_columns
                for field in excluded_fields
            ),
        )

    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    failed = [name for name, passed in results if not passed]
    print(f"TOTAL={len(results)} PASS={len(results) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

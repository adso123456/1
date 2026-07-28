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

from backend.data_source_registry import build_current_data_source_registry
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.mysql_runner import ReadOnlyMySQLRunner
from backend.mysql_runtime_factory import (
    MySQLRuntimeBuilders,
    create_mysql_runtime,
)
from backend.mysql_sql_guard import MySQLSQLGuard
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

    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    failed = [name for name, passed in results if not passed]
    print(f"TOTAL={len(results)} PASS={len(results) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

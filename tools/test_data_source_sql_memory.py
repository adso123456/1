"""自动 SQL Tool Memory 的 Guard 与只读执行回归测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_sql_memory import VerifiedSQLMemoryGenerator


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, parameters=None) -> None:
        self.executed.append(sql)

    def fetchmany(self, size: int) -> list[dict]:
        return [{"monitor_time": "2026-08-01 10:00:00", "flow_rate": 2.5}]

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeConnector:
    def _connect(self, source_id: str) -> FakeConnection:
        return FakeConnection()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sql-memory-test-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="SQL Memory 测试",
            description="",
            database_type="mysql",
            host="127.0.0.1",
            port=3306,
            database_name="demo",
            username="readonly",
            password="secret",
        )
        metadata = [
            {
                "schema": "demo",
                "table": "monitor_data",
                "table_comment": "流量监测",
                "column": "monitor_time",
                "type": "datetime",
                "ordinal_position": 1,
            },
            {
                "schema": "demo",
                "table": "monitor_data",
                "table_comment": "流量监测",
                "column": "flow_rate",
                "type": "decimal(10,2)",
                "ordinal_position": 2,
            },
            {
                "schema": "demo",
                "table": "monitor_data",
                "table_comment": "流量监测",
                "column": "phone",
                "type": "varchar(20)",
                "ordinal_position": 3,
            },
        ]
        profiles = [
            {
                "schema": "demo",
                "table": "monitor_data",
                "time_column_candidate": "monitor_time",
                "error": "",
            }
        ]
        generator = VerifiedSQLMemoryGenerator(catalog, FakeConnector())
        records = generator.generate(source.source_id, metadata, profiles)
        assert len(records) == 1
        assert "LIMIT 5" in records[0]["sql"]
        assert "phone" not in records[0]["sql"]
        assert records[0]["metadata"]["train_decision"] == "approved"
        assert records[0]["metadata"]["validation_origin"] == "self_onboarding_read_only_execution"
        assert catalog.list_verified_sql_memories(source.source_id) == records
    print("data source SQL memory tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

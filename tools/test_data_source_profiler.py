"""受限数据画像的确定性回归测试。"""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_profiler import DataSourceProfiler


class FakeCursor:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def execute(self, sql: str, parameters=None) -> None:
        if "information_schema.TABLES" in sql:
            self.rows = [
                {
                    "schema_name": "demo",
                    "table_name": "monitor_data",
                    "row_estimate": 1234,
                }
            ]
        elif sql.lstrip().startswith("SELECT `monitor_time`"):
            self.rows = [
                {
                    "monitor_time": "2026-08-01 10:00:00",
                    "flow_rate": 2.5,
                    "phone": "13800000000",
                },
                {
                    "monitor_time": "2026-08-01 11:00:00",
                    "flow_rate": 3.5,
                    "phone": "13900000000",
                },
            ]
        else:
            self.rows = []

    def fetchall(self) -> list[dict]:
        return self.rows

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
    with tempfile.TemporaryDirectory(prefix="profile-test-") as directory:
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
        catalog = DataSourceCatalog(Path(directory) / "catalog.sqlite3", cipher=cipher)
        catalog.initialize()
        record = catalog.create(
            display_name="画像测试",
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
                "object_type": "table",
                "table_comment": "流量监测记录",
                "column": "monitor_time",
                "type": "datetime",
                "primary_key": True,
            },
            {
                "schema": "demo",
                "table": "monitor_data",
                "object_type": "table",
                "table_comment": "流量监测记录",
                "column": "flow_rate",
                "type": "decimal(10,2)",
                "primary_key": False,
            },
            {
                "schema": "demo",
                "table": "monitor_data",
                "object_type": "table",
                "table_comment": "流量监测记录",
                "column": "phone",
                "type": "varchar(20)",
                "primary_key": False,
            },
        ]
        profiler = DataSourceProfiler(catalog, connector=FakeConnector())
        profiles = profiler.profile(record.source_id, metadata)
        assert len(profiles) == 1
        profile = profiles[0]
        assert profile["row_estimate"] == 1234
        assert profile["sample_row_count"] == 2
        assert profile["time_column_candidate"] == "monitor_time"
        assert profile["grain_candidate"] == "monitor_time"
        phone = next(item for item in profile["columns"] if item["column"] == "phone")
        assert phone["sensitive"] is True
        assert "typical_values" not in phone
        flow = next(item for item in profile["columns"] if item["column"] == "flow_rate")
        assert flow["sample_min"] == 2.5 and flow["sample_max"] == 3.5
        assert catalog.list_table_profiles(record.source_id) == profiles
    print("data source profiler tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

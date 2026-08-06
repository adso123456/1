"""后台自分析任务状态与持久化回归测试。"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_onboarding import DataSourceOnboardingService


class FakeConnector:
    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    def test_connection(self, source_id: str) -> dict:
        self.catalog.mark_connection_test(source_id, success=True)
        return {"success": True}

    def discover(self, source_id: str) -> list[dict]:
        metadata = [
            {
                "schema": "demo",
                "table": "monitor_data",
                "object_type": "table",
                "table_comment": "监测数据",
                "column": "monitor_time",
                "type": "datetime",
                "nullable": False,
                "primary_key": True,
                "ordinal_position": 1,
                "indexes": [],
                "logical_relations": [],
            }
        ]
        self.catalog.save_discovery(source_id, metadata)
        return metadata


class FakeProfiler:
    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    def profile(self, source_id: str, metadata, *, progress=None) -> list[dict]:
        if progress:
            progress(1, 1, "monitor_data")
        profiles = [
            {
                "schema": "demo",
                "table": "monitor_data",
                "row_estimate": 123,
                "sample_row_count": 100,
                "time_column_candidate": "monitor_time",
                "grain_candidate": "monitor_time",
                "table_role_candidate": "事实表",
                "error": "",
            }
        ]
        self.catalog.replace_table_profiles(source_id, profiles)
        return profiles


class FakeSemanticAnalyzer:
    def analyze(self, metadata, profiles, **kwargs):
        return (
            [
                {
                    **dict(item),
                    "domain": "监测",
                    "grain": "monitor_time",
                    "time_column": "monitor_time",
                    "valid_row_rules": [],
                    "logical_relations": [],
                    "confidence": "deterministic",
                }
                for item in metadata
            ],
            {"semantic_mode": "deterministic", "warnings": []},
        )


class FakeSQLGenerator:
    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    def generate(self, source_id: str, metadata, profiles) -> list[dict]:
        self.catalog.replace_verified_sql_memories(source_id, [])
        return []


class FakePreparer:
    def prepare(self, source_id: str) -> dict:
        return {"source_id": source_id}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="onboarding-test-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="后台分析测试",
            description="",
            database_type="mysql",
            host="127.0.0.1",
            port=3306,
            database_name="demo",
            username="readonly",
            password="secret",
        )
        connector = FakeConnector(catalog)
        service = DataSourceOnboardingService(
            catalog,
            connector,
            FakeProfiler(catalog),
            FakePreparer(),
            semantic_analyzer=FakeSemanticAnalyzer(),
            sql_memory_generator=FakeSQLGenerator(catalog),
        )
        started = service.start(source.source_id, "analyze")
        assert started["status"] == "queued"
        deadline = time.monotonic() + 3
        current = None
        while time.monotonic() < deadline:
            current = catalog.current_onboarding_job(source.source_id)
            if current and current["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        assert current and current["status"] == "succeeded", current
        assert current["result"]["table_count"] == 1
        refreshed = catalog.require(source.source_id)
        assert refreshed.status == "metadata_ready"
        assert refreshed.selected_tables_count == 1
        assert refreshed.selected_scope[0]["domain"] == "监测"
    print("data source onboarding tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

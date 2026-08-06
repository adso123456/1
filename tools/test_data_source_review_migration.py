"""F1 回归：首次启用审核器时按 selected_scope 安全迁移。

场景：旧 Catalog 已有 discovered_metadata + selected_scope，reviews 为空。
  - 首次 review -> 已选表 effective=active（migration），未选表 pending；
  - selected_scope / runtime_revision 不变；
  - 第二次 review 不重新迁移，也不覆盖人工决定。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_table_reviewer import DataSourceTableReviewer


METADATA = []
for table in ("monitor_data", "station_dict", "water_data_old"):
    METADATA.extend(
        {
            "schema": "public",
            "table": table,
            "object_type": "table",
            "table_comment": f"{table} 注释",
            "column": column,
            "type": "numeric",
            "comment": f"{column} 注释",
        }
        for column in ("id", "value", "monitor_time", "area_code")
    )


class FakeConnector:
    def discover(self, source_id: str, *, persist: bool = True) -> list[dict]:
        return [dict(item) for item in METADATA]


class FakeProfiler:
    def profile(self, source_id, metadata, *, progress=None) -> list[dict]:
        if progress:
            progress(1, 3, "monitor_data")
            progress(3, 3, "water_data_old")
        profiles = []
        for index, table in enumerate(
            ("monitor_data", "station_dict", "water_data_old"),
            start=1,
        ):
            columns = [item for item in metadata if item["table"] == table]
            profiles.append(
                {
                    "schema": "public",
                    "table": table,
                    "object_type": "table",
                    "table_comment": columns[0]["table_comment"],
                    "table_role_candidate": "业务表",
                    "grain_candidate": "",
                    "time_column_candidate": "monitor_time",
                    "columns": [],
                    "quality": {
                        "column_count": len(columns),
                        "queryable_column_count": len(columns),
                        "has_primary_key": True,
                        "has_unique_key": False,
                        "row_estimate": 1000 + index,
                        "sample_row_count": 100,
                        "sample_null_rate": 0.0,
                        "latest_data_at": "2026-08-01 00:00:00",
                        "time_coverage_days": 30.0,
                        "duplicate_key_ratio": 0.0,
                        "observed_update_interval": None,
                        "staleness_ratio": None,
                        "freshness_confidence": 0.0,
                        "skipped_by_total_timeout": False,
                        "structure_fingerprint": f"struct-{table}",
                        "data_fingerprint": f"data-{table}",
                        "table_comment": columns[0]["table_comment"],
                    },
                    "error": "",
                }
            )
        return profiles


def _reviews(catalog: DataSourceCatalog, source_id: str) -> dict[str, dict]:
    return {
        row["table_name"]: row
        for row in catalog.list_table_reviews(source_id)
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="review-migration-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="迁移测试",
            description="",
            database_type="postgresql",
            host="127.0.0.1",
            port=5432,
            database_name="gt_monitor",
            schema_name="public",
            username="readonly",
            password="secret",
        )
        # 旧 Catalog 状态：discovered 全部表，selected_scope 只选两张。
        catalog.save_discovery(source.source_id, METADATA)
        selected = [
            item
            for item in METADATA
            if item["table"] in {"monitor_data", "station_dict"}
        ]
        catalog.save_scope(source.source_id, selected)
        scope_before = len(catalog.require(source.source_id).selected_scope)
        revision_before = catalog.require(source.source_id).runtime_revision

        reviewer = DataSourceTableReviewer(
            catalog,
            FakeConnector(),
            FakeProfiler(),
        )
        # 第一次 review：reviews 为空 -> 先安全迁移再审核。
        first = reviewer.run_review(
            source.source_id,
            created_by="migration-test",
        )
        assert first["discovered"] == 3
        reviews = _reviews(catalog, source.source_id)
        assert set(reviews) == {
            "monitor_data",
            "station_dict",
            "water_data_old",
        }
        assert reviews["monitor_data"]["effective_decision"] == "active"
        assert reviews["monitor_data"]["decision_source"] == "migration"
        assert reviews["station_dict"]["effective_decision"] == "active"
        assert reviews["water_data_old"]["effective_decision"] == "pending"
        assert (
            reviews["water_data_old"]["decision_reason"]
            == "legacy_unclassified"
        )
        # 已选表、revision 不变；正式资产未被触碰。
        assert len(catalog.require(source.source_id).selected_scope) == scope_before
        assert (
            catalog.require(source.source_id).runtime_revision == revision_before
        )

        # 人工决定：把未选表提升为 active。
        catalog.upsert_table_review(
            source.source_id,
            "public",
            "water_data_old",
            effective_decision="active",
            decision_source="manual",
            decision_reason="人工确认",
        )
        # 第二次 review：不得重新迁移、不得覆盖人工决定。
        reviewer.run_review(source.source_id, created_by="migration-test-2")
        reviews = _reviews(catalog, source.source_id)
        assert reviews["water_data_old"]["effective_decision"] == "active"
        assert reviews["water_data_old"]["decision_source"] == "manual"
        assert reviews["monitor_data"]["effective_decision"] == "active"
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            migration_runs = connection.execute(
                "SELECT count(*) FROM data_source_review_runs "
                "WHERE source_id=? AND status='migration'",
                (source.source_id,),
            ).fetchone()[0]
            assert migration_runs == 1
        finally:
            connection.close()

    print("data source review migration tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

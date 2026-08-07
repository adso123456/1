"""阶段 B：审核运行结果契约测试。

验证 DataSourceTableReviewer.run_review：
  - 返回结果包含 proposed（建议分布）与 business_groups；
  - reviews 写入 proposed_decision / proposed_score / business_group；
  - effective_decision 与 selected_scope 不被修改。

使用真实 DataSourceCatalog（临时库）+ 假连接器/画像，不触碰真实数据库。
"""

from __future__ import annotations

import sys
import sqlite3
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_table_reviewer import DataSourceTableReviewer


WATER_COLUMNS = [
    "station_id", "monitor_time", "ph", "cod", "nh3n", "tp", "tn",
    "do", "water_temp", "flow", "area_code", "status",
]


class FakeConnector:
    def __init__(self) -> None:
        self.metadata = [
            {
                "schema": "public",
                "table": "water_data",
                "object_type": "table",
                "table_comment": "水质监测小时数据",
                "column": column,
                "type": "numeric" if column != "monitor_time" else "timestamp",
                "comment": f"{column} 注释",
                "primary_key": column in {"station_id", "monitor_time"},
            }
            for column in WATER_COLUMNS
        ] + [
            {
                "schema": "public",
                "table": "water_data_old",
                "object_type": "table",
                "table_comment": "水质监测旧数据",
                "column": column,
                "type": "numeric" if column != "monitor_time" else "timestamp",
                "comment": f"{column} 注释",
                "primary_key": column in {"station_id", "monitor_time"},
            }
            for column in WATER_COLUMNS[:9]
        ]

    def discover(self, source_id: str, *, persist: bool = True) -> list[dict]:
        return [dict(item) for item in self.metadata]


class FakeProfiler:
    def profile(self, source_id, metadata, *, progress=None) -> list[dict]:
        if progress:
            progress(1, 2, "water_data")
            progress(2, 2, "water_data_old")
        profiles = []
        for index, table in enumerate(("water_data", "water_data_old")):
            columns = [item for item in metadata if item["table"] == table]
            profiles.append(
                {
                    "schema": "public",
                    "table": table,
                    "object_type": "table",
                    "table_comment": columns[0]["table_comment"],
                    "table_role_candidate": "事实表",
                    "grain_candidate": "station_id+monitor_time",
                    "time_column_candidate": "monitor_time",
                    "columns": [
                        {
                            "column": item["column"],
                            "type": item["type"],
                            "sample_null_rate": 0.0,
                            "sample_distinct_count": 100,
                            "sensitive": False,
                        }
                        for item in columns
                    ],
                    "quality": {
                        "column_count": len(columns),
                        "queryable_column_count": len(columns),
                        "has_primary_key": True,
                        "has_unique_key": False,
                        "primary_key_columns": ["station_id", "monitor_time"],
                        "row_estimate": 5000 if table == "water_data_old" else 100_000,
                        "sample_row_count": 200,
                        "sample_null_rate": 0.05,
                        "latest_data_at": (
                            "2023-01-01 00:00:00"
                            if table == "water_data_old"
                            else "2026-08-01 10:00:00"
                        ),
                        "time_coverage_days": (
                            300.0 if table == "water_data_old" else 400.0
                        ),
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="review-contract-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="契约测试",
            description="",
            database_type="postgresql",
            host="127.0.0.1",
            port=5432,
            database_name="gt_monitor",
            schema_name="public",
            username="readonly",
            password="secret",
        )
        # 迁移模拟：water_data 为正式 active，water_data_old 未入选。
        catalog.upsert_table_review(
            source.source_id,
            "public",
            "water_data",
            effective_decision="active",
            decision_source="migration",
            decision_reason="existing_selected_scope",
        )
        catalog.upsert_table_review(
            source.source_id,
            "public",
            "water_data_old",
            effective_decision="pending",
            decision_source="migration",
            decision_reason="legacy_unclassified",
        )
        scope_before = len(source.selected_scope)

        reviewer = DataSourceTableReviewer(
            catalog,
            FakeConnector(),
            FakeProfiler(),
        )
        result = reviewer.run_review(
            source.source_id,
            created_by="contract-test",
        )

        # 1. 返回契约：proposed 分布 + 业务组数量。
        assert isinstance(result.get("proposed"), dict), result
        assert set(result["proposed"]) <= {"active", "pending", "standby"}
        assert isinstance(result.get("business_groups"), int)
        assert result["discovered"] == 2
        assert result["profiled"] == 2
        assert result["missing"] == 0

        # 2. reviews 写入了建议字段（冻结契约：正式 active 不再因同组被压 pending）。
        active_review = catalog.get_table_review(
            source.source_id, "public", "water_data"
        )
        assert active_review["proposed_decision"] == "active"
        assert active_review["proposed_score"] is not None
        assert "替换需人工确认" not in active_review["proposed_reason"]
        old_review = catalog.get_table_review(
            source.source_id, "public", "water_data_old"
        )
        # 独立判定：backup 表在自身分数达标时仍可 active（backup_mirror 需
        # 结构指纹一致或列重合≥0.9 才降级；本 fixture 指纹不同）。
        assert old_review["proposed_decision"] in {"active", "standby", "pending"}
        assert "同组存在正式主表" not in old_review["proposed_reason"]
        assert old_review["proposed_score"] is not None

        # 3. 隔离：effective_decision 与 selected_scope 未变。
        assert (
            catalog.get_table_review(
                source.source_id, "public", "water_data"
            )["effective_decision"]
            == "active"
        )
        assert (
            catalog.get_table_review(
                source.source_id, "public", "water_data_old"
            )["effective_decision"]
            == "pending"
        )
        assert len(catalog.require(source.source_id).selected_scope) == scope_before

        # 4. history 与 run 落库。
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            runs = connection.execute(
                "SELECT status, review_version FROM data_source_review_runs"
            ).fetchall()
            assert runs and runs[-1][0] == "succeeded"
            assert runs[-1][1] == 2
            history = connection.execute(
                "SELECT count(*) FROM data_source_review_history"
            ).fetchone()[0]
            assert history == 2
        finally:
            connection.close()

    print("data source review contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

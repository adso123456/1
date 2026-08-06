"""F3 / F4 回归：run_id 防碰撞与审核结果原子提交。

F3：冻结时间连续运行两次审核，必须产生 2 条 run、2 组完整 history、
    且 run_id 不同（旧方案 int(time.time()) 会碰撞被 INSERT OR IGNORE 吞掉）。
F4：审核批次中途失败时整体回滚，只保留 run=failed 与错误信息，
    不留下无历史对应的部分 current state。
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

import backend.data_source_table_reviewer as reviewer_module
from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_table_reviewer import DataSourceTableReviewer


WATER_COLUMNS = [
    "station_id", "monitor_time", "ph", "cod", "nh3n", "tp", "tn",
    "do", "water_temp", "flow", "area_code", "status",
]


class FakeConnector:
    def discover(self, source_id: str, *, persist: bool = True) -> list[dict]:
        metadata = []
        for table in ("water_data", "water_data_old"):
            metadata.extend(
                {
                    "schema": "public",
                    "table": table,
                    "object_type": "table",
                    "table_comment": f"{table} 注释",
                    "column": column,
                    "type": "numeric" if column != "monitor_time" else "timestamp",
                    "comment": f"{column} 注释",
                    "primary_key": column in {"station_id", "monitor_time"},
                }
                for column in WATER_COLUMNS
            )
        return metadata


class FakeProfiler:
    def profile(self, source_id, metadata, *, progress=None) -> list[dict]:
        if progress:
            progress(1, 2, "water_data")
            progress(2, 2, "water_data_old")
        profiles = []
        for table in ("water_data", "water_data_old"):
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
                        "row_estimate": 100_000,
                        "sample_row_count": 200,
                        "sample_null_rate": 0.05,
                        "latest_data_at": "2026-08-01 10:00:00",
                        "time_coverage_days": 400.0,
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


def _seed_two_reviews(catalog: DataSourceCatalog, source_id: str) -> None:
    for table, effective in (
        ("water_data", "active"),
        ("water_data_old", "pending"),
    ):
        catalog.upsert_table_review(
            source_id,
            "public",
            table,
            effective_decision=effective,
            decision_source="migration",
            decision_reason="existing_selected_scope",
        )


def test_frozen_time_two_runs_no_run_id_collision() -> None:
    with tempfile.TemporaryDirectory(prefix="review-f3-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="F3 测试",
            description="",
            database_type="postgresql",
            host="127.0.0.1",
            port=5432,
            database_name="gt_monitor",
            schema_name="public",
            username="readonly",
            password="secret",
        )
        _seed_two_reviews(catalog, source.source_id)
        reviewer = DataSourceTableReviewer(
            catalog,
            FakeConnector(),
            FakeProfiler(),
        )
        original_ns = reviewer_module.time.time_ns
        original_seconds = reviewer_module.time.time
        reviewer_module.time.time_ns = lambda: 1786000000000000000
        reviewer_module.time.time = lambda: 1786000000.0
        try:
            first = reviewer.run_review(source.source_id, created_by="f3")
            second = reviewer.run_review(source.source_id, created_by="f3")
        finally:
            reviewer_module.time.time_ns = original_ns
            reviewer_module.time.time = original_seconds

        assert first["run_id"] != second["run_id"]
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            runs = connection.execute(
                "SELECT run_id, status FROM data_source_review_runs "
                "WHERE source_id=? AND status='succeeded'",
                (source.source_id,),
            ).fetchall()
            assert len(runs) == 2
            assert len({row[0] for row in runs}) == 2
            history = connection.execute(
                "SELECT run_id, count(*) FROM data_source_review_history "
                "WHERE source_id=? GROUP BY run_id",
                (source.source_id,),
            ).fetchall()
            assert len(history) == 2
            assert all(count == 2 for _, count in history)
        finally:
            connection.close()


def test_mid_batch_failure_rolls_back_everything() -> None:
    with tempfile.TemporaryDirectory(prefix="review-f4-") as directory:
        catalog = DataSourceCatalog(
            Path(directory) / "catalog.sqlite3",
            cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        )
        catalog.initialize()
        source = catalog.create(
            display_name="F4 测试",
            description="",
            database_type="postgresql",
            host="127.0.0.1",
            port=5432,
            database_name="gt_monitor",
            schema_name="public",
            username="readonly",
            password="secret",
        )
        catalog.upsert_table_review(
            source.source_id,
            "public",
            "water_data",
            effective_decision="active",
            decision_source="migration",
            decision_reason="existing_selected_scope",
            quality_metrics_json='{"seed": true}',
        )
        reviewer = DataSourceTableReviewer(
            catalog,
            FakeConnector(),
            FakeProfiler(),
        )
        original = catalog._upsert_review_row

        def failing(
            connection,
            source_id,
            schema_name,
            table_name,
            fields,
            *,
            now,
        ):
            if table_name == "water_data_old":
                raise RuntimeError("模拟批次中途失败")
            return original(
                connection,
                source_id,
                schema_name,
                table_name,
                fields,
                now=now,
            )

        catalog._upsert_review_row = failing
        try:
            try:
                reviewer.run_review(source.source_id, created_by="f4")
            except RuntimeError as exc:
                assert "模拟批次中途失败" in str(exc)
            else:
                raise AssertionError("审核应抛出 RuntimeError")
        finally:
            catalog._upsert_review_row = original

        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            run = connection.execute(
                "SELECT status, error FROM data_source_review_runs "
                "WHERE source_id=? ORDER BY started_at DESC LIMIT 1",
                (source.source_id,),
            ).fetchone()
            assert run[0] == "failed"
            assert "RuntimeError" in run[1]
            # current reviews 未被部分修改：仍只有 seed 行且原样保留。
            reviews = connection.execute(
                "SELECT table_name, effective_decision, quality_metrics_json, "
                "availability_status FROM data_source_table_reviews "
                "WHERE source_id=?",
                (source.source_id,),
            ).fetchall()
            assert len(reviews) == 1
            assert reviews[0][0] == "water_data"
            assert reviews[0][1] == "active"
            assert reviews[0][2] == '{"seed": true}'
            assert reviews[0][3] == "present"
            # 无任何 history 快照。
            history = connection.execute(
                "SELECT count(*) FROM data_source_review_history "
                "WHERE source_id=?",
                (source.source_id,),
            ).fetchone()[0]
            assert history == 0
        finally:
            connection.close()


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(
        f"\n{len([1 for n in globals() if n.startswith('test_')]) - failed}/"
        f"{len([1 for n in globals() if n.startswith('test_')])} passed"
    )
    raise SystemExit(1 if failed else 0)

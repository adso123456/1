"""F1 回归：首次启用审核器时按 selected_scope 安全迁移。

覆盖：
  - 正常首次迁移：已选表 effective=active（migration），未选表 pending；
    selected_scope / runtime_revision 不变；
  - 第二次运行不重新迁移，也不覆盖人工决定；
  - 迁移中途失败（第 2 张表写入抛异常）整体回滚：reviews 为 0、
    migration run 为 0，重试后仍完整迁移；
  - 已有 review 时方法级原子 no-op。
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


def _setup(directory: Path):
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
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
    catalog.save_discovery(source.source_id, METADATA)
    selected = [
        item
        for item in METADATA
        if item["table"] in {"monitor_data", "station_dict"}
    ]
    catalog.save_scope(source.source_id, selected)
    return catalog, source.source_id


def _reviews(catalog: DataSourceCatalog, source_id: str) -> dict[str, dict]:
    return {
        row["table_name"]: row
        for row in catalog.list_table_reviews(source_id)
    }


def test_first_run_migration_and_no_override() -> None:
    with tempfile.TemporaryDirectory(prefix="review-migration-") as directory:
        catalog, source_id = _setup(Path(directory))
        scope_before = len(catalog.require(source_id).selected_scope)
        revision_before = catalog.require(source_id).runtime_revision

        reviewer = DataSourceTableReviewer(
            catalog,
            FakeConnector(),
            FakeProfiler(),
        )
        first = reviewer.run_review(source_id, created_by="migration-test")
        assert first["discovered"] == 3
        reviews = _reviews(catalog, source_id)
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
        assert len(catalog.require(source_id).selected_scope) == scope_before
        assert catalog.require(source_id).runtime_revision == revision_before

        # 人工决定：把未选表提升为 active。
        catalog.upsert_table_review(
            source_id,
            "public",
            "water_data_old",
            effective_decision="active",
            decision_source="manual",
            decision_reason="人工确认",
        )
        # 第二次 review：不得重新迁移、不得覆盖人工决定。
        reviewer.run_review(source_id, created_by="migration-test-2")
        reviews = _reviews(catalog, source_id)
        assert reviews["water_data_old"]["effective_decision"] == "active"
        assert reviews["water_data_old"]["decision_source"] == "manual"
        assert reviews["monitor_data"]["effective_decision"] == "active"
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            migration_runs = connection.execute(
                "SELECT count(*) FROM data_source_review_runs "
                "WHERE source_id=? AND status='migration'",
                (source_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        assert migration_runs == 1


def test_migration_midway_failure_rolls_back_and_retries() -> None:
    with tempfile.TemporaryDirectory(prefix="review-migration-fault-") as directory:
        catalog, source_id = _setup(Path(directory))
        original = catalog._upsert_review_row
        call_count = {"value": 0}

        def failing(
            connection,
            source_id,
            schema_name,
            table_name,
            fields,
            *,
            now,
        ):
            call_count["value"] += 1
            if call_count["value"] == 2:
                raise RuntimeError("模拟迁移第 2 张表写入失败")
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
                catalog.migrate_table_reviews_from_existing(source_id)
            except RuntimeError as exc:
                assert "第 2 张表" in str(exc)
            else:
                raise AssertionError("迁移应抛出 RuntimeError")
        finally:
            catalog._upsert_review_row = original

        # 整体回滚：无任何 review、无任何 migration run。
        assert catalog.list_table_reviews(source_id) == []
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            runs = connection.execute(
                "SELECT count(*) FROM data_source_review_runs "
                "WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        assert runs == 0

        # 重试成功：selected_scope 全部 active、未选表全部 pending。
        result = catalog.migrate_table_reviews_from_existing(source_id)
        assert result["skipped"] is False
        assert result["active"] == 2
        assert result["pending"] == 1
        reviews = _reviews(catalog, source_id)
        assert reviews["monitor_data"]["effective_decision"] == "active"
        assert reviews["station_dict"]["effective_decision"] == "active"
        assert reviews["water_data_old"]["effective_decision"] == "pending"


def test_migration_noop_when_reviews_exist() -> None:
    with tempfile.TemporaryDirectory(prefix="review-migration-noop-") as directory:
        catalog, source_id = _setup(Path(directory))
        catalog.upsert_table_review(
            source_id,
            "public",
            "monitor_data",
            effective_decision="active",
            decision_source="manual",
            decision_reason="人工决定",
        )
        result = catalog.migrate_table_reviews_from_existing(source_id)
        assert result["skipped"] is True
        reviews = _reviews(catalog, source_id)
        # 只有人工预置的一行，未新增迁移行、未覆盖决定。
        assert set(reviews) == {"monitor_data"}
        assert reviews["monitor_data"]["decision_source"] == "manual"
        connection = sqlite3.connect(Path(directory) / "catalog.sqlite3")
        try:
            runs = connection.execute(
                "SELECT count(*) FROM data_source_review_runs "
                "WHERE source_id=? AND status='migration'",
                (source_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        assert runs == 0


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

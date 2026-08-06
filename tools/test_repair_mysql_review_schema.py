"""MySQL 审核记录 schema 修复脚本的回归测试。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.repair_mysql_review_schema import repair_mysql_review_schema


def _create_catalog(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE data_source_table_reviews (
            source_id TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            business_group TEXT NOT NULL DEFAULT '',
            group_confidence REAL NOT NULL DEFAULT 0,
            compared_tables_json TEXT NOT NULL DEFAULT '[]',
            group_reason TEXT NOT NULL DEFAULT '',
            proposed_decision TEXT NOT NULL DEFAULT '',
            proposed_score REAL,
            proposed_reason TEXT NOT NULL DEFAULT '',
            effective_decision TEXT NOT NULL DEFAULT 'pending',
            decision_source TEXT NOT NULL DEFAULT '',
            decision_reason TEXT NOT NULL DEFAULT '',
            availability_status TEXT NOT NULL DEFAULT 'present',
            quality_metrics_json TEXT NOT NULL DEFAULT '{}',
            structure_fingerprint TEXT NOT NULL DEFAULT '',
            data_fingerprint TEXT NOT NULL DEFAULT '',
            review_version INTEGER NOT NULL DEFAULT 0,
            last_profiled_at REAL,
            reviewed_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (source_id, schema_name, table_name)
        )
        """
    )
    # 空 schema 行（原迁移）与规范行（本轮审核误建）并存。
    connection.executemany(
        """
        INSERT INTO data_source_table_reviews (
            source_id, schema_name, table_name, effective_decision,
            decision_source, decision_reason, availability_status,
            quality_metrics_json, proposed_decision, proposed_score,
            review_version, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                "mysql-lzh-monitor", "", "ad_dict", "active",
                "migration", "existing_selected_scope", "missing",
                "{}", "", None, 1, 1.0, 1.0,
            ),
            (
                "mysql-lzh-monitor", "lzh_monitor", "ad_dict", "pending",
                "migration", "legacy_unclassified", "present",
                '{"row_estimate": 197}', "pending", 66.0, 2, 1.0, 1.0,
            ),
            (
                "mysql-lzh-monitor", "", "sys_dict", "active",
                "migration", "existing_selected_scope", "missing",
                "{}", "", None, 1, 1.0, 1.0,
            ),
        ],
    )
    connection.commit()
    connection.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)

        result = repair_mysql_review_schema(db_path)
        assert result["merged_or_upgraded"] == 2
        # ad_dict 是"合并后删除空行"，sys_dict 是"空行直接升级 schema"。
        assert result["deleted_empty_rows"] == 1
        assert result["promoted_to_active"] == 1

        connection = sqlite3.connect(db_path)
        try:
            rows = connection.execute(
                "SELECT schema_name, table_name, effective_decision, "
                "availability_status, decision_source, proposed_decision "
                "FROM data_source_table_reviews "
                "WHERE source_id='mysql-lzh-monitor' ORDER BY table_name"
            ).fetchall()
        finally:
            connection.close()

        assert len(rows) == 2
        by_table = {row[1]: row for row in rows}
        # ad_dict：空行 active + 规范行 pending -> 合并为 active/present，
        # 并保留较新的质量指标与建议字段。
        assert by_table["ad_dict"][2] == "active"
        assert by_table["ad_dict"][3] == "present"
        assert by_table["ad_dict"][4] == "migration"
        assert by_table["ad_dict"][5] == "pending"
        # sys_dict：只有空行 -> 直接升级 schema。
        assert by_table["sys_dict"][0] == "lzh_monitor"
        assert by_table["sys_dict"][2] == "active"

        # 幂等：再跑一次不应改变结果。
        second = repair_mysql_review_schema(db_path)
        assert second["merged_or_upgraded"] == 0
        assert second["deleted_empty_rows"] == 0

        # 不触碰其他数据源。
        connection = sqlite3.connect(db_path)
        try:
            other = connection.execute(
                "SELECT count(*) FROM data_source_table_reviews "
                "WHERE source_id != 'mysql-lzh-monitor'"
            ).fetchone()[0]
        finally:
            connection.close()
        assert other == 0

    print("mysql review schema repair tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

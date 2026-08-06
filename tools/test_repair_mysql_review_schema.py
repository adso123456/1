"""MySQL 审核记录 schema 修复脚本的回归测试。

覆盖：函数修复逻辑、幂等性、CLI 必填参数、dry-run 回滚、
错误目标（不存在/非 MySQL/规范 schema 不一致）拒绝。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.repair_mysql_review_schema import (
    build_parser,
    main,
    repair_mysql_review_schema,
)


def _create_catalog(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE data_sources (
            source_id TEXT PRIMARY KEY,
            database_type TEXT NOT NULL,
            database_name TEXT NOT NULL,
            schema_name TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.executemany(
        "INSERT INTO data_sources VALUES (?,?,?,?)",
        [
            ("mysql-lzh-monitor", "mysql", "lzh_monitor", ""),
            ("postgresql-main", "postgresql", "gt_monitor", "public"),
        ],
    )
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


def _read_rows(db_path: Path) -> list[tuple]:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT schema_name, table_name, effective_decision, "
            "availability_status, decision_source, proposed_decision "
            "FROM data_source_table_reviews "
            "WHERE source_id='mysql-lzh-monitor' ORDER BY table_name"
        ).fetchall()
    finally:
        connection.close()


def test_repair_merge_promote_and_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)

        result = repair_mysql_review_schema(
            db_path,
            source_id="mysql-lzh-monitor",
            canonical_schema="lzh_monitor",
        )
        assert result["merged_or_upgraded"] == 2
        # ad_dict 是"合并后删除空行"，sys_dict 是"空行直接升级 schema"。
        assert result["deleted_empty_rows"] == 1
        assert result["promoted_to_active"] == 1
        assert result["dry_run"] is False

        rows = _read_rows(db_path)
        assert len(rows) == 2
        by_table = {row[1]: row for row in rows}
        assert by_table["ad_dict"][2] == "active"
        assert by_table["ad_dict"][3] == "present"
        assert by_table["ad_dict"][4] == "migration"
        assert by_table["ad_dict"][5] == "pending"
        assert by_table["sys_dict"][0] == "lzh_monitor"
        assert by_table["sys_dict"][2] == "active"

        # 幂等：再跑一次不应改变结果。
        second = repair_mysql_review_schema(
            db_path,
            source_id="mysql-lzh-monitor",
        )
        assert second["merged_or_upgraded"] == 0
        assert second["deleted_empty_rows"] == 0


def test_canonical_schema_defaults_to_database_name() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        result = repair_mysql_review_schema(
            db_path,
            source_id="mysql-lzh-monitor",
        )
        assert result["canonical_schema"] == "lzh_monitor"


def test_dry_run_rolls_back_everything() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        before = _read_rows(db_path)

        result = repair_mysql_review_schema(
            db_path,
            source_id="mysql-lzh-monitor",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["merged_or_upgraded"] == 2
        assert _read_rows(db_path) == before


def test_rejects_unknown_source() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        try:
            repair_mysql_review_schema(
                db_path,
                source_id="not-exist",
            )
        except ValueError as exc:
            assert "数据源不存在" in str(exc)
        else:
            raise AssertionError("应拒绝未知 source_id")


def test_rejects_non_mysql_source() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        try:
            repair_mysql_review_schema(
                db_path,
                source_id="postgresql-main",
            )
        except ValueError as exc:
            assert "不是 MySQL" in str(exc)
        else:
            raise AssertionError("应拒绝非 MySQL 数据源")


def test_rejects_canonical_schema_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        try:
            repair_mysql_review_schema(
                db_path,
                source_id="mysql-lzh-monitor",
                canonical_schema="other_schema",
            )
        except ValueError as exc:
            assert "不一致" in str(exc)
        else:
            raise AssertionError("应拒绝与数据库身份不一致的规范 schema")


def test_cli_requires_source_id() -> None:
    try:
        build_parser().parse_args(["--db", "x.sqlite3"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("缺少 --source-id 应报参数错误")


def test_cli_dry_run_outputs_counts_without_writing() -> None:
    with tempfile.TemporaryDirectory(prefix="repair-mysql-") as directory:
        db_path = Path(directory) / "catalog.sqlite3"
        _create_catalog(db_path)
        before = _read_rows(db_path)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(
                [
                    "--db",
                    str(db_path),
                    "--source-id",
                    "mysql-lzh-monitor",
                    "--dry-run",
                ]
            )
        assert code == 0
        payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
        assert payload["dry_run"] is True
        assert payload["merged_or_upgraded"] == 2
        assert _read_rows(db_path) == before


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

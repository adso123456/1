"""现有 PostgreSQL/MySQL 的只读索引发现冒烟。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_connectors import DirectDatabaseConnector


def _indexes(metadata: list[dict]) -> dict[str, dict]:
    return {
        f"{item['schema']}.{item['table']}.{index['name']}": index
        for item in metadata
        for index in item.get("indexes", [])
    }


def main() -> int:
    required = ("PG_SMOKE_PASSWORD", "MYSQL_SMOKE_PASSWORD")
    if any(not os.environ.get(name) for name in required):
        raise RuntimeError("缺少只读冒烟所需数据库凭据")
    with tempfile.TemporaryDirectory(prefix="b5-index-live-") as directory:
        root = Path(directory)
        catalog = DataSourceCatalog(
            root / "catalog.sqlite3",
            environ={
                "PG_USER": os.environ.get("PG_SMOKE_USER", "postgres"),
                "PG_PASSWORD": os.environ["PG_SMOKE_PASSWORD"],
                "MYSQL_USER": os.environ.get("MYSQL_SMOKE_USER", "root"),
                "MYSQL_PASSWORD": os.environ["MYSQL_SMOKE_PASSWORD"],
            },
        )
        catalog.initialize(
            [
                {
                    "source_id": "postgresql-main",
                    "display_name": "PostgreSQL 索引冒烟",
                    "description": "",
                    "database_type": "postgresql",
                    "host": "127.0.0.1",
                    "port": 5433,
                    "database_name": os.environ.get(
                        "PG_SMOKE_DATABASE",
                        "gt_monitor",
                    ),
                    "schema_name": "public",
                    "credential_reference": {
                        "username": "PG_USER",
                        "password": "PG_PASSWORD",
                    },
                    "metadata_path": root / "pg.json",
                    "memory_path": root / "pg-memory",
                },
                {
                    "source_id": "mysql-lzh-monitor",
                    "display_name": "MySQL 索引冒烟",
                    "description": "",
                    "database_type": "mysql",
                    "host": "127.0.0.1",
                    "port": 3307,
                    "database_name": os.environ.get(
                        "MYSQL_SMOKE_DATABASE",
                        "lzh_monitor",
                    ),
                    "credential_reference": {
                        "username": "MYSQL_USER",
                        "password": "MYSQL_PASSWORD",
                    },
                    "metadata_path": root / "mysql.json",
                    "memory_path": root / "mysql-memory",
                },
            ]
        )
        connector = DirectDatabaseConnector(catalog)
        for source_id in ("postgresql-main", "mysql-lzh-monitor"):
            metadata = connector.discover(source_id)
            indexes = _indexes(metadata)
            non_primary = [
                value for value in indexes.values() if not value["primary"]
            ]
            if not indexes:
                if source_id != "postgresql-main":
                    raise AssertionError(f"{source_id} 未发现任何索引")
                connection = connector._connect(source_id)
                try:
                    cursor = connection.cursor()
                    cursor.execute("BEGIN READ ONLY")
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM pg_indexes
                        WHERE schemaname = %s
                        """,
                        ("public",),
                    )
                    total = int(cursor.fetchone()["total"])
                    connection.rollback()
                    cursor.close()
                finally:
                    connection.close()
                if total != 0:
                    raise AssertionError("PostgreSQL 原始目录存在索引但发现结果为空")
                print(
                    "[PASS] postgresql-main：原始 pg_indexes 查询证明 "
                    "public Schema 索引数为 0"
                )
                continue
            if not non_primary:
                raise AssertionError(f"{source_id} 未发现非主键索引")
            print(
                f"[PASS] {source_id}：字段 {len(metadata)}，"
                f"索引 {len(indexes)}，非主键索引 {len(non_primary)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

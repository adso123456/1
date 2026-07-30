"""验证 MySQL 通用问数 147 表资产可达性；可选执行真实只读冒烟。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.mysql_sql_guard import MySQLSQLGuard

INVENTORY = ROOT / "config" / "mysql_full_schema_inventory.json"
SCOPE = ROOT / "config" / "mysql_general_agent_scope.json"
EVALUATION = ROOT / "tools" / "mysql_general_agent_evaluation_cases.json"
DEFAULT_METADATA = Path(
    r"E:\3\posgresql\1\agent_data\mysql-lzh-monitor\column_metadata_index.json"
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sql(table: dict[str, Any]) -> str:
    columns = table["included_columns"][:3]
    rendered = ", ".join(f"`{column}`" for column in columns)
    return f"SELECT {rendered} FROM `{table['table']}` LIMIT 1"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--live", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _args()
    inventory = _load(INVENTORY)
    scope = _load(SCOPE)
    evaluation = _load(EVALUATION)
    metadata = _load(args.metadata)
    metadata_keys = {
        (item["table"], item["column"]) for item in metadata
    }
    guard = MySQLSQLGuard(args.metadata)
    failures = []
    queries = []
    for table in scope["tables"]:
        if not table["included_columns"]:
            failures.append(f"{table['table']}: no columns")
            continue
        missing = [
            column
            for column in table["included_columns"]
            if (table["table"], column) not in metadata_keys
        ]
        if missing:
            failures.append(f"{table['table']}: metadata missing")
            continue
        sql = _sql(table)
        result = guard.validate(sql, query=f"查询{table['table']}明细")
        if not result.passed:
            failures.append(f"{table['table']}: SQLGuard {result.reason}")
            continue
        queries.append((table["table"], sql))
    live_rows = 0
    if args.live:
        import pymysql

        connection = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3307")),
            database=os.getenv("MYSQL_DATABASE", "lzh_monitor"),
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            cursor = connection.cursor()
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION READ ONLY")
            for table, sql in queries:
                try:
                    cursor.execute(sql)
                    cursor.fetchall()
                    live_rows += 1
                except Exception as exc:
                    failures.append(f"{table}: live {type(exc).__name__}")
            connection.rollback()
        finally:
            connection.close()
    checks = {
        "inventory_tables": inventory["discovered_table_count"] == 307,
        "old_unselected_investigated": (
            inventory["investigated_previous_unselected_count"] == 289
        ),
        "scope_tables": len(scope["tables"]) == 147,
        "scope_columns": sum(
            len(item["included_columns"]) for item in scope["tables"]
        ) == 3085,
        "domain_matrix": (
            evaluation["domain_count"]
            == len(inventory["included_domain_counts"])
            and all(len(item["questions"]) >= 2 for item in evaluation["domains"])
        ),
        "table_reachability": len(queries) == 147,
        "live_reachability": not args.live or live_rows == 147,
        "no_failures": not failures,
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"TABLES={len(queries)} LIVE_EXECUTED={live_rows}")
    print(f"DOMAINS={evaluation['domain_count']} FAILURES={len(failures)}")
    if failures:
        print("FAILURE_DETAILS=" + json.dumps(failures, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

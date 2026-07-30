"""验证 MySQL 通用问数 147 表资产可达性；可选执行真实只读冒烟。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.mysql_sql_guard import MySQLSQLGuard

INVENTORY = ROOT / "config" / "mysql_full_schema_inventory.json"
SCOPE = ROOT / "config" / "mysql_general_agent_scope.json"
EVALUATION = ROOT / "tools" / "mysql_general_agent_evaluation_cases.json"
SOURCE_ID = "mysql-lzh-monitor"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sql(table: dict[str, Any]) -> str:
    columns = table["included_columns"][:3]
    rendered = ", ".join(f"`{column}`" for column in columns)
    return f"SELECT {rendered} FROM `{table['table']}` LIMIT 1"


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--metadata",
        type=Path,
        help="待验收 Metadata；--live 时必须显式提供",
    )
    parser.add_argument(
        "--revision",
        type=int,
        help="存在 asset_manifest.json 时用于校验 runtime revision",
    )
    parser.add_argument(
        "--scope-fingerprint",
        help="存在 asset_manifest.json 时用于校验 scope fingerprint",
    )
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.live and args.metadata is None:
        parser.error("--live 必须显式提供 --metadata，拒绝读取默认正式资产")
    return args


def build_candidate_metadata(
    inventory: dict[str, Any],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    inventory_tables = {
        item["table_name"]: item for item in inventory["tables"]
    }
    metadata = []
    for table_scope in scope["tables"]:
        table = inventory_tables[table_scope["table"]]
        columns = {item["name"]: item for item in table["columns"]}
        first_column = table_scope["included_columns"][0]
        for column_name in table_scope["included_columns"]:
            column = columns[column_name]
            metadata.append(
                {
                    "schema": table["schema"],
                    "table": table["table_name"],
                    "table_comment": table["table_comment"],
                    "column": column_name,
                    "type": column["type"],
                    "mysql_type": column["type"],
                    "nullable": column["nullable"],
                    "default": column["default"],
                    "comment": column["comment"],
                    "ordinal_position": column["ordinal_position"],
                    "primary_key": column["key"] == "PRI",
                    "indexes": table["indexes"],
                    "logical_relations": (
                        table_scope["relationships"]
                        if column_name == first_column
                        else []
                    ),
                    "object_type": (
                        "view"
                        if table["table_type"] == "VIEW"
                        else "table"
                    ),
                    "dialect": "mysql",
                    "domain": table_scope["domain"],
                    "grain": table_scope["grain"],
                    "time_column": table_scope["time_column"],
                    "valid_row_rules": table_scope["valid_row_rules"],
                    "confidence": table_scope["confidence"],
                }
            )
    return metadata


def validate_metadata_scope(
    metadata: list[dict[str, Any]],
    scope: dict[str, Any],
) -> None:
    expected = {
        (table["table"], column)
        for table in scope["tables"]
        for column in table["included_columns"]
    }
    actual_rows = [
        (str(item.get("table") or ""), str(item.get("column") or ""))
        for item in metadata
    ]
    actual = set(actual_rows)
    if len(scope["tables"]) != 147 or len(expected) != 3085:
        raise RuntimeError("Scope 不是预期的 147 表 / 3085 字段")
    if len(actual_rows) != len(actual):
        raise RuntimeError("Metadata 存在重复表字段")
    if actual != expected:
        missing = len(expected - actual)
        unexpected = len(actual - expected)
        raise RuntimeError(
            f"Metadata 与 scope 不一致：缺少 {missing}，多出 {unexpected}"
        )
    excluded = set(scope["excluded_columns"])
    leaked = {
        f"{table}.{column}"
        for table, column in actual
        if f"{table}.{column}" in excluded
    }
    if leaked:
        raise RuntimeError(
            "Metadata 包含排除字段：" + ", ".join(sorted(leaked))
        )


def validate_manifest(
    metadata_path: Path,
    expected_revision: int | None,
    expected_scope_fingerprint: str | None,
) -> None:
    manifest_path = metadata_path.parent / "asset_manifest.json"
    if not manifest_path.is_file():
        return
    if expected_revision is None or not expected_scope_fingerprint:
        raise RuntimeError(
            "发现 asset_manifest.json，必须显式提供 "
            "--revision 和 --scope-fingerprint"
        )
    manifest = _load(manifest_path)
    if manifest.get("source_id") != SOURCE_ID:
        raise RuntimeError("Manifest source_id 不一致")
    if manifest.get("scope_fingerprint") != expected_scope_fingerprint:
        raise RuntimeError("Manifest scope fingerprint 不一致")
    metadata_hash = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    if manifest.get("metadata_hash") != metadata_hash:
        raise RuntimeError("Manifest Metadata hash 不一致")
    if manifest.get("runtime_revision") != expected_revision:
        raise RuntimeError("Manifest revision 不一致")


def _run(
    args: argparse.Namespace,
    inventory: dict[str, Any],
    scope: dict[str, Any],
    evaluation: dict[str, Any],
    metadata_path: Path,
    metadata: list[dict[str, Any]],
) -> int:
    validate_metadata_scope(metadata, scope)
    validate_manifest(
        metadata_path,
        args.revision,
        args.scope_fingerprint,
    )
    metadata_keys = {
        (item["table"], item["column"]) for item in metadata
    }
    guard = MySQLSQLGuard(metadata_path)
    failures = []
    queries = []
    for table in scope["tables"]:
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
        "scope_columns": len(metadata_keys) == 3085,
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


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    inventory = _load(INVENTORY)
    scope = _load(SCOPE)
    evaluation = _load(EVALUATION)
    if args.metadata is not None:
        metadata_path = args.metadata.resolve()
        metadata = _load(metadata_path)
        return _run(
            args,
            inventory,
            scope,
            evaluation,
            metadata_path,
            metadata,
        )
    metadata = build_candidate_metadata(inventory, scope)
    with tempfile.TemporaryDirectory(
        prefix="mysql-general-agent-metadata-"
    ) as temp_dir:
        metadata_path = Path(temp_dir) / "column_metadata_index.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return _run(
            args,
            inventory,
            scope,
            evaluation,
            metadata_path,
            metadata,
        )


if __name__ == "__main__":
    raise SystemExit(main())

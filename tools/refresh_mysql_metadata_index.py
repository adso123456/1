"""只读提取 lzh_monitor 已批准 18 表的 MySQL Metadata。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.data_sources import (
    DEFAULT_MYSQL_SCOPE_PATH,
    build_mysql_data_source_config,
)


LOGICAL_RELATIONS: dict[str, list[dict[str, str]]] = {
    "wm_waterquality_hour_records": [
        {"column": "station_id", "target": "wm_station_info.id"}
    ],
    "wm_waterquality_day_records": [
        {"column": "station_id", "target": "wm_station_info.id"}
    ],
    "wm_waterquality_month_records": [
        {"column": "section_id", "target": "wm_section_info.id"}
    ],
    "wm_station_info": [
        {"column": "section_id", "target": "wm_section_info.id"},
        {"column": "water_body_id", "target": "wm_waterbody_info.id"},
        {"column": "region_code", "target": "gis_region.region_code"},
    ],
    "wm_section_info": [
        {"column": "water_body_id", "target": "wm_waterbody_info.id"}
    ],
    "wh_hydrological_hour_records": [
        {"column": "station_id", "target": "wm_hydrological_info.id"}
    ],
    "wh_hydrological_day_records": [
        {"column": "station_id", "target": "wm_hydrological_info.id"}
    ],
    "wh_meteorological_hour_records": [
        {"column": "station_id", "target": "wm_meteorological_info.id"}
    ],
    "wh_meteorological_day_records": [
        {"column": "station_id", "target": "wm_meteorological_info.id"}
    ],
    "rs_warn_records": [
        {"column": "station_id", "target": "wm_station_info.id"}
    ],
    "rs_pollutant_info": [
        {"column": "region_id", "target": "gis_region.id"},
        {"column": "code", "target": "gis_region.code"},
    ],
}


def load_scope(scope_path: Path) -> dict[str, Any]:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    tables = scope.get("approved_tables")
    if (
        scope.get("datasource_id") != "mysql-lzh-monitor"
        or scope.get("dialect") != "mysql"
        or scope.get("database") != "lzh_monitor"
        or not isinstance(tables, list)
        or len(tables) != 18
        or len(set(tables)) != 18
    ):
        raise ValueError("MySQL scope 必须精确描述 lzh_monitor 的 18 张批准表")
    return scope


def _placeholders(values: list[str]) -> str:
    return ", ".join(["%s"] * len(values))


def extract_mysql_metadata(
    connection: Any,
    database: str,
    approved_tables: list[str],
) -> list[dict[str, Any]]:
    """仅查询 information_schema，并返回稳定排序的列级 Metadata。"""
    placeholders = _placeholders(approved_tables)
    cursor = connection.cursor()
    try:
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute(
            f"""
            SELECT
                c.TABLE_NAME AS table_name,
                t.TABLE_COMMENT AS table_comment,
                c.ORDINAL_POSITION AS ordinal_position,
                c.COLUMN_NAME AS column_name,
                c.COLUMN_TYPE AS mysql_type,
                c.IS_NULLABLE AS is_nullable,
                c.COLUMN_DEFAULT AS column_default,
                c.COLUMN_COMMENT AS column_comment,
                c.COLUMN_KEY AS column_key
            FROM information_schema.COLUMNS AS c
            JOIN information_schema.TABLES AS t
              ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
             AND t.TABLE_NAME = c.TABLE_NAME
            WHERE c.TABLE_SCHEMA = %s
              AND c.TABLE_NAME IN ({placeholders})
              AND t.TABLE_TYPE = 'BASE TABLE'
            ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
            """,
            [database, *approved_tables],
        )
        columns = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT
                TABLE_NAME AS table_name,
                INDEX_NAME AS index_name,
                NON_UNIQUE AS non_unique,
                SEQ_IN_INDEX AS seq_in_index,
                COLUMN_NAME AS column_name,
                INDEX_TYPE AS index_type
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """,
            [database, *approved_tables],
        )
        index_rows = cursor.fetchall()
    finally:
        connection.rollback()
        cursor.close()

    found_tables = {row["table_name"] for row in columns}
    expected_tables = set(approved_tables)
    if found_tables != expected_tables:
        missing = sorted(expected_tables - found_tables)
        unexpected = sorted(found_tables - expected_tables)
        raise RuntimeError(
            f"Metadata 表范围不一致：missing={missing}, unexpected={unexpected}"
        )

    indexes_by_table: dict[str, dict[str, dict[str, Any]]] = {}
    for row in index_rows:
        table_indexes = indexes_by_table.setdefault(row["table_name"], {})
        index = table_indexes.setdefault(
            row["index_name"],
            {
                "name": row["index_name"],
                "unique": row["non_unique"] == 0,
                "type": row["index_type"],
                "columns": [],
            },
        )
        index["columns"].append(row["column_name"])

    result: list[dict[str, Any]] = []
    for row in columns:
        table = row["table_name"]
        first_column = row["ordinal_position"] == 1
        result.append(
            {
                "table": table,
                "table_comment": row["table_comment"] or "",
                "column": row["column_name"],
                "type": row["mysql_type"],
                "mysql_type": row["mysql_type"],
                "nullable": row["is_nullable"] == "YES",
                "default": row["column_default"],
                "comment": row["column_comment"] or "",
                "ordinal_position": row["ordinal_position"],
                "primary_key": row["column_key"] == "PRI",
                "indexes": (
                    list(indexes_by_table.get(table, {}).values())
                    if first_column
                    else []
                ),
                "logical_relations": (
                    LOGICAL_RELATIONS.get(table, []) if first_column else []
                ),
                "object_type": "table",
            }
        )
    return result


def write_metadata_index(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成 mysql-lzh-monitor 独立 Metadata 索引"
    )
    parser.add_argument(
        "--scope",
        type=Path,
        default=DEFAULT_MYSQL_SCOPE_PATH,
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    scope = load_scope(args.scope.expanduser().resolve())
    config = build_mysql_data_source_config(scope_path=args.scope)

    import pymysql

    connection = pymysql.connect(
        **dict(config.connection_settings),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        rows = extract_mysql_metadata(
            connection,
            config.connection_settings["database"],
            scope["approved_tables"],
        )
    finally:
        connection.close()

    output = args.output or config.metadata_path
    write_metadata_index(output, rows)
    print("DB_TRANSACTION_MODE: READ ONLY")
    print(f"OUTPUT: {Path(output).expanduser().resolve()}")
    print(f"TABLE_COUNT: {len({row['table'] for row in rows})}")
    print(f"COLUMN_COUNT: {len(rows)}")
    print("FORMAL_CHROMA_WRITES: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

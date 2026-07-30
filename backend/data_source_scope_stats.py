"""为数据源 API 提供互斥的发现、纳入、排除和待确认统计。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT

MYSQL_INVENTORY_PATH = (
    PROJECT_ROOT / "config" / "mysql_full_schema_inventory.json"
)


def scope_statistics(record: Any) -> dict[str, int]:
    discovered_tables = len(
        {
            (str(item.get("schema") or ""), str(item.get("table") or ""))
            for item in record.discovered_metadata
            if item.get("table")
        }
    )
    discovered_columns = len(record.discovered_metadata)
    fallback = {
        "discovered_tables_count": discovered_tables,
        "discovered_columns_count": discovered_columns,
        "included_tables_count": int(record.selected_tables_count),
        "included_columns_count": int(record.selected_columns_count),
        "excluded_tables_count": max(
            discovered_tables - int(record.selected_tables_count), 0
        ),
        "pending_confirmation_count": 0,
    }
    if record.source_id != "mysql-lzh-monitor" or not MYSQL_INVENTORY_PATH.is_file():
        return fallback
    try:
        inventory = json.loads(
            MYSQL_INVENTORY_PATH.read_text(encoding="utf-8")
        )
        result = {
            "discovered_tables_count": int(
                inventory["discovered_table_count"]
            ),
            "discovered_columns_count": int(
                inventory["discovered_column_count"]
            ),
            "included_tables_count": int(record.selected_tables_count),
            "included_columns_count": int(record.selected_columns_count),
            "excluded_tables_count": int(
                inventory["excluded_table_count"]
            ),
            "pending_confirmation_count": int(
                inventory["pending_confirmation_count"]
            ),
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback
    if (
        result["included_tables_count"]
        + result["excluded_tables_count"]
        + result["pending_confirmation_count"]
        != result["discovered_tables_count"]
    ):
        return fallback
    return result

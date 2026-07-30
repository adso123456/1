"""通过 B5 crash-safe 链路发布 MySQL 通用问数范围。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_catalog import DataSourceCatalog, resolve_catalog_path
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_runtime_manager import DataSourceRuntimeManager

SOURCE_ID = "mysql-lzh-monitor"
DEFAULT_INVENTORY = PROJECT_ROOT / "config" / "mysql_full_schema_inventory.json"
DEFAULT_SCOPE = PROJECT_ROOT / "config" / "mysql_general_agent_scope.json"


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="发布 MySQL 通用问数范围")
    parser.add_argument(
        "--catalog",
        type=Path,
        help="目标 Catalog 路径；--apply 时必须显式提供",
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际更新 Catalog 并执行 crash-safe 发布；默认只做 Plan",
    )
    args = parser.parse_args(argv)
    if args.apply and args.catalog is None:
        parser.error("--apply 必须显式提供 --catalog，拒绝写入默认 Catalog")
    if args.catalog is None:
        args.catalog = resolve_catalog_path()
    return args


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_selected_scope(
    discovered: tuple[dict[str, Any], ...],
    inventory: dict[str, Any],
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    discovered_by_key = {
        (
            str(item.get("schema") or ""),
            str(item["table"]),
            str(item["column"]),
        ): dict(item)
        for item in discovered
    }
    inventory_tables = {
        item["table_name"]: item for item in inventory["tables"]
    }
    selected = []
    for table_scope in scope["tables"]:
        table = table_scope["table"]
        inventory_table = inventory_tables[table]
        relations = list(table_scope.get("relationships") or [])
        for column_name in table_scope["included_columns"]:
            key = (inventory["database"], table, column_name)
            row = discovered_by_key.get(key)
            if row is None:
                key = ("", table, column_name)
                row = discovered_by_key.get(key)
            if row is None:
                raise RuntimeError(f"Catalog discovery 缺少字段：{table}.{column_name}")
            column = next(
                item
                for item in inventory_table["columns"]
                if item["name"] == column_name
            )
            selected.append(
                {
                    **row,
                    "schema": str(row.get("schema") or ""),
                    "table_comment": inventory_table["table_comment"],
                    "type": column["type"],
                    "comment": column["comment"],
                    "nullable": column["nullable"],
                    "primary_key": column["key"] == "PRI",
                    "ordinal_position": column["ordinal_position"],
                    "domain": table_scope["domain"],
                    "grain": table_scope["grain"],
                    "time_column": table_scope["time_column"],
                    "valid_row_rules": table_scope["valid_row_rules"],
                    "logical_relations": (
                        relations
                        if column["ordinal_position"] == min(
                            item["ordinal_position"]
                            for item in inventory_table["columns"]
                            if item["name"] in table_scope["included_columns"]
                        )
                        else []
                    ),
                    "confidence": table_scope["confidence"],
                }
            )
    selected.sort(
        key=lambda item: (
            str(item.get("schema") or ""),
            item["table"],
            int(item["ordinal_position"]),
        )
    )
    expected_columns = scope["tables"]
    expected_count = sum(len(item["included_columns"]) for item in expected_columns)
    if len(selected) != expected_count:
        raise RuntimeError("selected_scope 字段数量不一致")
    if len({item["table"] for item in selected}) != len(scope["approved_tables"]):
        raise RuntimeError("selected_scope 表数量不一致")
    forbidden = set(scope["excluded_columns"])
    leaked = {
        f"{item['table']}.{item['column']}"
        for item in selected
        if f"{item['table']}.{item['column']}" in forbidden
    }
    if leaked:
        raise RuntimeError(f"selected_scope 泄漏排除字段：{sorted(leaked)}")
    return selected


def _runtime_manager(
    catalog: DataSourceCatalog,
    environ: dict[str, str],
) -> DataSourceRuntimeManager:
    from backend.mysql_runtime_factory import create_mysql_runtime
    from backend.postgresql_runtime_factory import create_postgresql_runtime

    registry = DataSourceRegistry.from_catalog(catalog)
    return DataSourceRuntimeManager(
        registry,
        {
            "mysql": lambda config: create_mysql_runtime(
                config, environ=environ
            ),
            "postgresql": lambda config: create_postgresql_runtime(
                config, environ=environ
            ),
        },
    )


def _verify_published(
    catalog: DataSourceCatalog,
    expected_tables: int,
    expected_columns: int,
) -> dict[str, Any]:
    record = catalog.require(SOURCE_ID)
    metadata = _load(record.metadata_path)
    if record.status != "ready" or not record.enabled_for_chat:
        raise RuntimeError("发布后数据源未恢复 ready")
    if record.selected_tables_count != expected_tables:
        raise RuntimeError("发布后表数量不一致")
    if record.selected_columns_count != expected_columns:
        raise RuntimeError("发布后字段数量不一致")
    if len(metadata) != expected_columns:
        raise RuntimeError("发布后 Metadata 数量不一致")
    from backend.memory import create_memory

    memory = create_memory(record.memory_path)
    try:
        collection = memory._get_collection()
        count = collection.count()
        rows = collection.get(include=["metadatas"])
        categories: dict[str, int] = {}
        for item in rows.get("metadatas") or []:
            category = str(
                (item or {}).get("category")
                or (item or {}).get("memory_type")
                or "unknown"
            )
            categories[category] = categories.get(category, 0) + 1
    finally:
        if not DataSourceAssetPreparer._close_memory(memory):
            raise RuntimeError("发布后 Memory 释放失败")
    if categories.get("sql_example") != 18:
        raise RuntimeError("发布后既有 18 条 SQL Tool Memory 未完整保留")
    return {
        "runtime_revision": record.runtime_revision,
        "metadata_records": len(metadata),
        "memory_records": count,
        "memory_categories": categories,
        "memory_path": str(record.memory_path),
    }


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    environ = dict(os.environ)
    print(f"MODE: {'APPLY' if args.apply else 'PLAN'}")
    print(f"CATALOG: {args.catalog.resolve()}")
    catalog = DataSourceCatalog(args.catalog, environ=environ)
    record = catalog.require(SOURCE_ID)
    inventory = _load(args.inventory)
    scope = _load(args.scope)
    selected = build_selected_scope(
        record.discovered_metadata,
        inventory,
        scope,
    )
    table_count = len({item["table"] for item in selected})
    column_count = len(selected)
    print(f"CURRENT_REVISION: {record.runtime_revision}")
    print(f"CURRENT_TABLES: {record.selected_tables_count}")
    print(f"CURRENT_COLUMNS: {record.selected_columns_count}")
    print(f"CANDIDATE_TABLES: {table_count}")
    print(f"CANDIDATE_COLUMNS: {column_count}")
    print(f"EXCLUDED_SENSITIVE_COLUMNS: {len(scope['excluded_columns'])}")
    if not args.apply:
        print("CATALOG_WRITES: 0")
        print("FORMAL_ASSET_WRITES: 0")
        return 0
    required = ("MYSQL_USER", "MYSQL_PASSWORD", "DEEPSEEK_API_KEY")
    missing = [name for name in required if not environ.get(name)]
    if missing:
        raise RuntimeError("发布缺少环境变量：" + ", ".join(missing))
    catalog.save_scope(SOURCE_ID, selected)
    manager = _runtime_manager(catalog, environ)
    preparer = DataSourceAssetPreparer(catalog, manager)
    result = preparer.prepare(SOURCE_ID)
    verified = _verify_published(catalog, table_count, column_count)
    print("PUBLISH_RESULT: " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("VERIFIED: " + json.dumps(verified, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

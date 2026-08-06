"""两个内置副本资产的数据源身份与结构指纹。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from config.settings import PROJECT_ROOT


BUILTIN_CLAIM_SOURCE_IDS = frozenset({"mysql-lzh-monitor", "postgresql-main"})
LINEAGE_PATH = PROJECT_ROOT / "config" / "builtin_asset_lineage.json"


def load_builtin_asset_lineage() -> dict[str, dict[str, Any]]:
    payload = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("内置资产来源配置必须是对象")
    result = {
        str(source_id): dict(item)
        for source_id, item in payload.items()
        if source_id in BUILTIN_CLAIM_SOURCE_IDS and isinstance(item, Mapping)
    }
    if set(result) != set(BUILTIN_CLAIM_SOURCE_IDS):
        raise ValueError("内置资产来源配置不完整")
    return result


def endpoint_fingerprint(
    *,
    database_type: str,
    host: str,
    port: int,
    database_name: str,
    schema_name: str,
) -> str:
    payload = "|".join(
        (
            database_type.strip().lower(),
            host.strip().lower(),
            str(int(port)),
            database_name.strip().lower(),
            schema_name.strip().lower(),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def endpoint_matches_replica(
    *,
    database_type: str,
    host: str,
    port: int,
    database_name: str,
    schema_name: str,
    lineage: Mapping[str, Any],
) -> bool:
    return (
        database_type.lower() == str(lineage.get("database_type") or "").lower()
        and host.lower() in {
            str(item).lower() for item in lineage.get("origin_hosts", [])
        }
        and int(port) == int(lineage.get("origin_port") or 0)
        and database_name.lower()
        == str(lineage.get("database_name") or "").lower()
        and schema_name.lower() == str(lineage.get("schema_name") or "").lower()
    )


def schema_fingerprint(metadata: Iterable[Mapping[str, Any]]) -> str:
    structural = [
        {
            "schema": str(item.get("schema") or "").lower(),
            "table": str(item.get("table") or "").lower(),
            "column": str(item.get("column") or "").lower(),
            "type": str(item.get("type") or "").lower(),
            "object_type": str(item.get("object_type") or "table").lower(),
            "nullable": bool(item.get("nullable", True)),
            "primary_key": bool(item.get("primary_key", False)),
            "ordinal_position": int(item.get("ordinal_position") or 0),
            "indexes": item.get("indexes") or [],
        }
        for item in metadata
    ]
    structural.sort(
        key=lambda item: (
            item["schema"],
            item["table"],
            item["ordinal_position"],
            item["column"],
        )
    )
    encoded = json.dumps(
        structural,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity(
    item: Mapping[str, Any],
    default_schema: str,
) -> tuple[str, str, str]:
    return (
        str(item.get("schema") or default_schema).strip().lower(),
        str(item.get("table") or "").strip().lower(),
        str(item.get("column") or "").strip().lower(),
    )


def _normalized_type(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def build_schema_diff(
    baseline_metadata: Iterable[Mapping[str, Any]],
    remote_metadata: Iterable[Mapping[str, Any]],
    *,
    default_schema: str,
) -> dict[str, Any]:
    """只比较副本资产中明确记录过的结构字段。"""
    baseline = {
        _identity(item, default_schema): dict(item)
        for item in baseline_metadata
        if item.get("table") and item.get("column")
    }
    remote = {
        _identity(item, default_schema): dict(item)
        for item in remote_metadata
        if item.get("table") and item.get("column")
    }
    baseline_tables = {(schema, table) for schema, table, _ in baseline}
    remote_tables = {(schema, table) for schema, table, _ in remote}
    added_tables = sorted(remote_tables - baseline_tables)
    removed_tables = sorted(baseline_tables - remote_tables)
    added_columns = sorted(set(remote) - set(baseline))
    removed_columns = sorted(set(baseline) - set(remote))
    changed_columns: list[dict[str, Any]] = []
    unchanged_columns: list[tuple[str, str, str]] = []
    for key in sorted(set(baseline) & set(remote)):
        old = baseline[key]
        current = remote[key]
        changes: list[str] = []
        if _normalized_type(old.get("type")) != _normalized_type(
            current.get("type")
        ):
            changes.append("type")
        for name in ("object_type", "nullable", "primary_key", "ordinal_position"):
            if name in old and old.get(name) != current.get(name):
                changes.append(name)
        if "indexes" in old and (old.get("indexes") or []) != (
            current.get("indexes") or []
        ):
            changes.append("indexes")
        if changes:
            changed_columns.append(
                {
                    "schema": key[0],
                    "table": key[1],
                    "column": key[2],
                    "changes": changes,
                    "baseline_type": old.get("type", ""),
                    "remote_type": current.get("type", ""),
                }
            )
        else:
            unchanged_columns.append(key)

    destructive_tables = {
        (item[0], item[1]) for item in removed_columns
    } | {
        (item["schema"], item["table"]) for item in changed_columns
    } | set(removed_tables)
    inherited_tables = sorted(
        table for table in baseline_tables & remote_tables
        if table not in destructive_tables
    )

    def render_table(value: tuple[str, str]) -> dict[str, str]:
        return {"schema": value[0], "table": value[1]}

    def render_column(value: tuple[str, str, str]) -> dict[str, str]:
        return {"schema": value[0], "table": value[1], "column": value[2]}

    return {
        "summary": {
            "baseline_table_count": len(baseline_tables),
            "remote_table_count": len(remote_tables),
            "added_table_count": len(added_tables),
            "removed_table_count": len(removed_tables),
            "added_column_count": len(added_columns),
            "removed_column_count": len(removed_columns),
            "changed_column_count": len(changed_columns),
            "unchanged_column_count": len(unchanged_columns),
        },
        "added_tables": [render_table(item) for item in added_tables],
        "removed_tables": [render_table(item) for item in removed_tables],
        "added_columns": [render_column(item) for item in added_columns],
        "removed_columns": [render_column(item) for item in removed_columns],
        "changed_columns": changed_columns,
        "semantic_inheritance_tables": [
            render_table(item) for item in inherited_tables
        ],
    }


def inherit_compatible_semantics(
    baseline_metadata: Iterable[Mapping[str, Any]],
    remote_metadata: Iterable[Mapping[str, Any]],
    diff: Mapping[str, Any],
    *,
    default_schema: str,
) -> list[dict[str, Any]]:
    """仅对无删除、无破坏性字段变化的表继承旧业务语义。"""
    allowed_tables = {
        (
            str(item.get("schema") or default_schema).lower(),
            str(item.get("table") or "").lower(),
        )
        for item in diff.get("semantic_inheritance_tables", [])
        if isinstance(item, Mapping)
    }
    baseline_by_table: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in baseline_metadata:
        item = dict(raw)
        schema, table, _ = _identity(item, default_schema)
        baseline_by_table[(schema, table)].append(item)

    semantic_fields = (
        "domain",
        "grain",
        "time_column",
        "valid_row_rules",
        "confidence",
        "semantic_summary",
        "table_role",
    )
    result: list[dict[str, Any]] = []
    for raw in remote_metadata:
        item = dict(raw)
        schema, table, _ = _identity(item, default_schema)
        old_columns = baseline_by_table.get((schema, table), [])
        if (schema, table) in allowed_tables and old_columns:
            semantic_source = next(
                (
                    old
                    for old in old_columns
                    if any(old.get(name) not in (None, "", []) for name in semantic_fields)
                ),
                old_columns[0],
            )
            for name in semantic_fields:
                value = semantic_source.get(name)
                if value not in (None, "", []):
                    item[name] = value
            item["semantic_origin"] = "replica_asset_inherited"
        result.append(item)
    return result

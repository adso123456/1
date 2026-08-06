"""阶段 E-2A：结构化候选资产硬校验。

只校验三类结构化对象：
  selected_scope（来自 Catalog）
  候选 Metadata（已落盘 JSON）
  候选 DDL（已落盘 JSON，仅支持本项目自身生成的固定格式）

核心不变量（在 E-1 表级门之上扩展列级一致性）：
  selected_scope tables == allowed_tables（E-1 已保证）
  Metadata tables == allowed_tables
  DDL tables      == allowed_tables
  Metadata (schema, table, column) == selected_scope (schema, table, column)
  DDL      (schema, table, column) == selected_scope (schema, table, column)

严格失败关闭：非 allowed 表、active 表遗漏、未选择字段、字段遗漏、
重复表/重复列、无法解析或身份不明确的 DDL、MySQL schema 未按数据库名
规范化，均抛 DataSourceCatalogError。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.data_source_catalog import DataSourceCatalogError


_IDENT_PATTERN = r'"(?:[^"]|"")+"|`(?:[^`]|``)+`'
_IDENT_RE = re.compile(_IDENT_PATTERN)
_QUALIFIED_PATTERN = (
    rf"(?:(?:{_IDENT_PATTERN})(?:\.(?:{_IDENT_PATTERN}))*)"
)


def _unquote(identifier: str) -> str:
    quote = identifier[0]
    return identifier[1:-1].replace(quote * 2, quote)


def _iter_identifiers(text: str) -> list[str]:
    return [_unquote(item) for item in _IDENT_RE.findall(text)]


def _parse_qualified(qualified: str) -> list[str]:
    parts = _iter_identifiers(qualified)
    if not parts:
        raise DataSourceCatalogError("DDL 无法解析：限定表名缺少标识符")
    return parts


def _split_table_identity(names: list[str]) -> tuple[str, str]:
    if len(names) == 1:
        return "", names[0]
    if len(names) == 2:
        return names[0], names[1]
    raise DataSourceCatalogError("DDL 无法解析：限定表名层级不明确")


def _parse_create_table(
    statement: str,
    tables: dict[tuple[str, str], list[str]],
) -> None:
    match = re.match(
        r"^CREATE TABLE\s+(?P<qualified>[^(]+?)\s*\((?P<body>.*)\)\s*$",
        statement,
        re.S,
    )
    if match is None:
        raise DataSourceCatalogError("DDL 无法解析：CREATE TABLE 结构不完整")
    names = _parse_qualified(match.group("qualified").strip())
    schema, table = _split_table_identity(names)
    if (schema, table) in tables:
        raise DataSourceCatalogError(f"DDL 重复表：{schema}.{table}")
    body = match.group("body")
    columns: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("PRIMARY KEY ("):
            continue
        column_match = re.match(
            rf"^(?P<ident>{_IDENT_PATTERN})\s+(?P<type>\S.*)$",
            line,
        )
        if column_match is None:
            raise DataSourceCatalogError(
                f"DDL 无法解析：{table} 的定义行 '{line}'"
            )
        columns.append(_unquote(column_match.group("ident")))
    if not columns:
        raise DataSourceCatalogError(f"DDL 无法解析：{schema}.{table} 没有列定义")
    if len(columns) != len(set(columns)):
        raise DataSourceCatalogError(f"DDL 重复列：{schema}.{table}")
    tables[(schema, table)] = columns


def _parse_index_statement(
    statement: str,
    tables: dict[tuple[str, str], list[str]],
) -> tuple[str, str]:
    match = re.match(
        r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+"
        + rf"(?P<ident>{_IDENT_PATTERN})\s+ON\s+"
        + rf"(?P<qualified>{_QUALIFIED_PATTERN})"
        + r"(?:\s+USING\s+[^\s(]+)?\s*\((?P<cols>.*)\)\s*$",
        statement,
        re.S,
    )
    if match is None:
        raise DataSourceCatalogError("DDL 无法解析：索引语句结构不完整")
    names = _parse_qualified(match.group("qualified"))
    schema, table = _split_table_identity(names)
    if not _IDENT_RE.findall(match.group("cols")):
        raise DataSourceCatalogError("DDL 无法解析：索引缺少列")
    if (schema, table) not in tables:
        raise DataSourceCatalogError(
            f"DDL 索引引用未知表：{schema}.{table}"
        )
    return schema, table


def _parse_ddl_text(ddl_text: str) -> dict[tuple[str, str], list[str]]:
    """严格解析单个 DDL 文本（一个 CREATE TABLE + 若干索引语句）。"""
    tables: dict[tuple[str, str], list[str]] = {}
    for raw in re.split(r";\s*", ddl_text.strip()):
        statement = raw.strip()
        if not statement:
            continue
        if statement.startswith("CREATE TABLE "):
            _parse_create_table(statement, tables)
        elif statement.startswith("CREATE UNIQUE INDEX ") or statement.startswith(
            "CREATE INDEX "
        ):
            _parse_index_statement(statement, tables)
        else:
            raise DataSourceCatalogError("DDL 无法解析：不支持的语句")
    if not tables:
        raise DataSourceCatalogError("DDL 无法解析：没有 CREATE TABLE")
    return tables


def _load_metadata(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataSourceCatalogError("候选 Metadata 不可读") from exc
    if not isinstance(payload, list):
        raise DataSourceCatalogError("候选 Metadata 必须是数组")
    return [dict(item) for item in payload if isinstance(item, Mapping)]


def _load_ddl(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataSourceCatalogError("候选 DDL 不可读") from exc
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise DataSourceCatalogError("候选 DDL 必须是字符串数组")
    return list(payload)


def _normalize_schema(
    database_type: str,
    database_name: str,
    schema: Any,
) -> str:
    """MySQL schema 身份一律按数据源数据库名规范化；不一致直接失败。"""
    value = str(schema or "")
    if database_type == "mysql":
        canonical = str(database_name or "")
        if value and value != canonical:
            raise DataSourceCatalogError(
                "MySQL Schema 身份未按数据源数据库名规范化："
                f"{value} != {canonical}"
            )
        return canonical
    return value


def _scope_keys(
    database_type: str,
    database_name: str,
    scope: Iterable[Mapping[str, Any]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    table_keys: set[tuple[str, str]] = set()
    column_keys: set[tuple[str, str, str]] = set()
    for item in scope:
        table = str(item.get("table") or "")
        column = str(item.get("column") or "")
        if not table or not column:
            raise DataSourceCatalogError(
                "selected_scope 存在缺少表或列的身份项"
            )
        schema = _normalize_schema(
            database_type,
            database_name,
            item.get("schema"),
        )
        table_keys.add((schema, table))
        column_keys.add((schema, table, column))
    return table_keys, column_keys


def _metadata_keys(
    database_type: str,
    database_name: str,
    metadata: Iterable[Mapping[str, Any]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    table_keys: set[tuple[str, str]] = set()
    column_keys: set[tuple[str, str, str]] = set()
    for item in metadata:
        table = str(item.get("table") or "")
        column = str(item.get("column") or "")
        if not table or not column:
            raise DataSourceCatalogError(
                "候选 Metadata 存在缺少表或列的身份项"
            )
        schema = _normalize_schema(
            database_type,
            database_name,
            item.get("schema"),
        )
        key = (schema, table, column)
        if key in column_keys:
            raise DataSourceCatalogError(
                f"候选 Metadata 重复列：{schema}.{table}.{column}"
            )
        column_keys.add(key)
        table_keys.add((schema, table))
    return table_keys, column_keys


def _ddl_keys(
    database_type: str,
    database_name: str,
    ddls: Iterable[str],
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    table_keys: set[tuple[str, str]] = set()
    seen_tables: set[tuple[str, str]] = set()
    column_keys: set[tuple[str, str, str]] = set()
    for ddl_text in ddls:
        parsed = _parse_ddl_text(ddl_text)
        for (schema, table), columns in parsed.items():
            normalized_schema = _normalize_schema(
                database_type,
                database_name,
                schema,
            )
            if (normalized_schema, table) in seen_tables:
                raise DataSourceCatalogError(
                    f"DDL 重复表：{normalized_schema}.{table}"
                )
            seen_tables.add((normalized_schema, table))
            table_keys.add((normalized_schema, table))
            for column in columns:
                column_keys.add((normalized_schema, table, column))
    return table_keys, column_keys


def _fmt_keys(keys: Iterable[tuple[str, ...]]) -> str:
    return "、".join(".".join(part for part in key if part) for key in sorted(keys))


def _require_equal(
    actual: set[tuple[str, ...]],
    expected: set[tuple[str, ...]],
    label: str,
    reference: str,
) -> None:
    missing = expected - actual
    extra = actual - expected
    detail = []
    if missing:
        detail.append(f"缺少 {reference} 项：{_fmt_keys(missing)}")
    if extra:
        detail.append(f"多出非 {reference} 项：{_fmt_keys(extra)}")
    if detail:
        raise DataSourceCatalogError(
            f"{label} 与 {reference} 不一致；" + "；".join(detail)
        )


def validate_candidate_assets(
    *,
    database_type: str,
    database_name: str,
    selected_scope: Iterable[Mapping[str, Any]],
    allowed_tables: Iterable[tuple[str, str]],
    metadata_path: Path,
    ddl_path: Path,
) -> dict[str, int]:
    """读取已落盘的候选 Metadata / DDL 并校验结构化一致性。"""
    allowed = {
        (
            _normalize_schema(database_type, database_name, schema),
            str(table),
        )
        for schema, table in allowed_tables
    }
    scope_tables, scope_columns = _scope_keys(
        database_type,
        database_name,
        selected_scope,
    )
    if scope_tables != allowed:
        raise DataSourceCatalogError(
            "selected_scope 表集合与 allowed_tables 不一致（E-1 门）"
        )
    metadata = _load_metadata(metadata_path)
    metadata_tables, metadata_columns = _metadata_keys(
        database_type,
        database_name,
        metadata,
    )
    ddls = _load_ddl(ddl_path)
    ddl_tables, ddl_columns = _ddl_keys(
        database_type,
        database_name,
        ddls,
    )
    _require_equal(metadata_tables, allowed, "候选 Metadata 表集合", "allowed_tables")
    _require_equal(ddl_tables, allowed, "候选 DDL 表集合", "allowed_tables")
    _require_equal(
        metadata_columns,
        scope_columns,
        "候选 Metadata 列集合",
        "selected_scope",
    )
    _require_equal(
        ddl_columns,
        scope_columns,
        "候选 DDL 列集合",
        "selected_scope",
    )
    return {
        "metadata_tables": len(metadata_tables),
        "metadata_columns": len(metadata_columns),
        "ddl_tables": len(ddl_tables),
        "ddl_columns": len(ddl_columns),
        "selected_scope_columns": len(scope_columns),
    }

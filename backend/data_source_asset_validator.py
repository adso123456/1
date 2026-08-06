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
  PRIMARY KEY / INDEX 引用列 ⊆ DDL 声明列 ⊆ selected_scope

严格失败关闭：非 allowed 表、active 表遗漏、未选择字段、字段遗漏、
重复表/重复列、PK/索引引用未声明或未选择字段、无法解析或身份不明确的
DDL（含方言不匹配、多余 token、多层限定名）、引号内未按本项目生成格式
转义的内容、MySQL schema 未按数据库名规范化、Metadata 含非法元素，
均抛 DataSourceCatalogError。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.data_source_catalog import DataSourceCatalogError


def _quote_pattern(database_type: str) -> str:
    """按方言返回引用标识符模式：PG 双引号，MySQL 反引号。"""
    if database_type == "mysql":
        return r"`(?:[^`]|``)+`"
    return r'"(?:[^"]|"")+"'


def _qualified_pattern(database_type: str) -> str:
    """限定表名全匹配模式：PG 允许 "schema"."table" 或 "table"；
    MySQL 仅 `table`。"""
    identifier = _quote_pattern(database_type)
    return rf"{identifier}(?:\.{identifier})?"


def _unquote(identifier: str) -> str:
    quote = identifier[0]
    return identifier[1:-1].replace(quote * 2, quote)


def _split_outside(
    text: str,
    delimiter: str,
    *,
    track_depth: bool = False,
) -> list[str]:
    """引号感知的切分：跟踪引号类型，处理 "" / `` 转义；
    track_depth=True 时仅在括号深度 0 处切分（用于列定义逗号）。"""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                if index + 1 < length and text[index + 1] == quote:
                    current.append(text[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in ('"', "`"):
            quote = char
            current.append(char)
        elif track_depth and char == "(":
            depth += 1
            current.append(char)
        elif track_depth and char == ")":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == delimiter and (not track_depth or depth == 0):
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_statements(text: str) -> list[str]:
    return _split_outside(text, ";")


def _split_top_level_commas(text: str) -> list[str]:
    return _split_outside(text, ",", track_depth=True)


def _parse_table_qualified(
    qualified: str,
    database_type: str,
) -> tuple[str, str]:
    pattern = rf"^{_qualified_pattern(database_type)}$"
    if re.match(pattern, qualified) is None:
        raise DataSourceCatalogError("DDL 无法解析：限定表名格式不明确")
    identifiers = [
        part.strip() for part in _split_outside(qualified, ".")
    ]
    if len(identifiers) == 1:
        return "", _unquote(identifiers[0])
    return _unquote(identifiers[0]), _unquote(identifiers[1])


def _parse_pk_columns(
    columns_text: str,
    table: str,
    database_type: str,
) -> list[str]:
    result: list[str] = []
    for entry in _split_top_level_commas(columns_text):
        entry = entry.strip()
        if not entry:
            continue
        match = re.match(
            rf"^(?P<ident>{_quote_pattern(database_type)})\s*$",
            entry,
            re.S,
        )
        if match is None:
            raise DataSourceCatalogError(
                f"DDL 无法解析：{table} 的 PRIMARY KEY 列 '{entry[:40]}'"
            )
        result.append(_unquote(match.group("ident")))
    return result


def _parse_column_definition(
    item: str,
    table: str,
    database_type: str,
) -> str:
    match = re.match(
        rf"^(?P<ident>{_quote_pattern(database_type)})\s+(?P<type>\S.*)$",
        item,
        re.S,
    )
    if match is None:
        raise DataSourceCatalogError(
            f"DDL 无法解析：{table} 的定义项 '{item[:60]}'"
        )
    return _unquote(match.group("ident"))


def _parse_create_table(
    statement: str,
    tables: dict,
    database_type: str,
) -> None:
    pattern = (
        r"^CREATE\s+TABLE\s+"
        + rf"(?P<qualified>{_qualified_pattern(database_type)})"
        + r"\s*\(\s*(?P<body>.*?)\s*\)\s*$"
    )
    match = re.match(pattern, statement, re.S)
    if match is None:
        raise DataSourceCatalogError("DDL 无法解析：CREATE TABLE 结构不完整")
    schema, table = _parse_table_qualified(
        match.group("qualified"),
        database_type,
    )
    if (schema, table) in tables:
        raise DataSourceCatalogError(f"DDL 重复表：{schema}.{table}")
    declared: list[str] = []
    primary: list[str] = []
    for item in _split_top_level_commas(match.group("body")):
        if not item:
            raise DataSourceCatalogError(f"DDL 无法解析：{table} 存在空定义项")
        pk_match = re.match(
            r"^PRIMARY\s+KEY\s*\(\s*(?P<cols>.*?)\s*\)\s*$",
            item,
            re.S,
        )
        if pk_match is not None:
            primary = _parse_pk_columns(
                pk_match.group("cols"),
                table,
                database_type,
            )
            continue
        declared.append(_parse_column_definition(item, table, database_type))
    if not declared:
        raise DataSourceCatalogError(f"DDL 无法解析：{schema}.{table} 没有列定义")
    if len(declared) != len(set(declared)):
        raise DataSourceCatalogError(f"DDL 重复列：{schema}.{table}")
    tables[(schema, table)] = {
        "declared_columns": declared,
        "primary_key_columns": primary,
        "index_columns": [],
    }


def _parse_index_statement(
    statement: str,
    tables: dict,
    database_type: str,
) -> None:
    pattern = (
        r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+"
        + rf"(?P<ident>{_quote_pattern(database_type)})\s+ON\s+"
        + rf"(?P<qualified>{_qualified_pattern(database_type)})"
        + r"(?:\s+USING\s+[^\s(]+)?\s*\(\s*(?P<cols>.*?)\s*\)\s*$"
    )
    match = re.match(pattern, statement, re.S)
    if match is None:
        raise DataSourceCatalogError("DDL 无法解析：索引语句结构不完整")
    schema, table = _parse_table_qualified(
        match.group("qualified"),
        database_type,
    )
    if (schema, table) not in tables:
        raise DataSourceCatalogError(
            f"DDL 索引引用未知表：{schema}.{table}"
        )
    index_columns: list[str] = []
    for entry in _split_top_level_commas(match.group("cols")):
        entry = entry.strip()
        if not entry:
            continue
        column_match = re.match(
            rf"^(?P<ident>{_quote_pattern(database_type)})"
            r"(?:\s+(?:ASC|DESC))?$",
            entry,
            re.S,
        )
        if column_match is None:
            raise DataSourceCatalogError(
                f"DDL 无法解析：索引列 '{entry[:40]}'"
            )
        index_columns.append(_unquote(column_match.group("ident")))
    if not index_columns:
        raise DataSourceCatalogError("DDL 无法解析：索引缺少列")
    tables[(schema, table)]["index_columns"] = index_columns


def _parse_ddl_text(
    ddl_text: str,
    database_type: str,
) -> dict:
    tables: dict = {}
    for statement in _split_statements(ddl_text):
        if not statement:
            continue
        if statement.startswith("CREATE TABLE "):
            _parse_create_table(statement, tables, database_type)
        elif statement.startswith("CREATE UNIQUE INDEX ") or statement.startswith(
            "CREATE INDEX "
        ):
            _parse_index_statement(statement, tables, database_type)
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
    items: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise DataSourceCatalogError(
                f"候选 Metadata 第 {index + 1} 项不是对象"
            )
        items.append(dict(item))
    return items


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
        parsed = _parse_ddl_text(ddl_text, database_type)
        for (schema, table), info in parsed.items():
            normalized_schema = _normalize_schema(
                database_type,
                database_name,
                schema,
            )
            key = (normalized_schema, table)
            if key in seen_tables:
                raise DataSourceCatalogError(
                    f"DDL 重复表：{normalized_schema}.{table}"
                )
            seen_tables.add(key)
            table_keys.add(key)
            declared = info["declared_columns"]
            primary = info["primary_key_columns"]
            index_columns = info["index_columns"]
            missing_primary = [col for col in primary if col not in declared]
            if missing_primary:
                raise DataSourceCatalogError(
                    "DDL PRIMARY KEY 引用未声明列："
                    f"{normalized_schema}.{table}."
                    + "、".join(missing_primary)
                )
            missing_index = [
                col for col in index_columns if col not in declared
            ]
            if missing_index:
                raise DataSourceCatalogError(
                    "DDL 索引引用未声明列："
                    f"{normalized_schema}.{table}."
                    + "、".join(missing_index)
                )
            referenced = set(declared) | set(primary) | set(index_columns)
            for column in referenced:
                column_keys.add((normalized_schema, table, column))
    return table_keys, column_keys


def _fmt_keys(keys: Iterable[tuple[str, ...]]) -> str:
    return "、".join(
        ".".join(part for part in key if part) for key in sorted(keys)
    )


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
    _require_equal(
        metadata_tables,
        allowed,
        "候选 Metadata 表集合",
        "allowed_tables",
    )
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

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tools.metadata_retriever import DeterministicMetadataRetriever


DEFAULT_INDEX_PATH = (
    Path(__file__).resolve().parents[1] / "agent_data" / "column_metadata_index.json"
)

FORBIDDEN_OPERATIONS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "comment",
    "merge",
    "grant",
    "revoke",
}

SYSTEM_TABLE_PREFIXES = (
    "information_schema",
    "pg_catalog",
    "sqlite_master",
    "sqlite_schema",
)


@dataclass
class SQLGuardResult:
    passed: bool
    severity: str
    used_tables: list[str]
    used_columns: list[str]
    unknown_tables: list[str]
    unknown_columns: list[str]
    forbidden_operations: list[str]
    candidate_mismatch: list[str]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_identifier(identifier: str) -> str:
    value = identifier.strip().strip('"`[]')
    return value.lower()


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n\r]*", " ", sql)
    return sql


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", _strip_sql_comments(sql)).strip()


def _split_csv(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None

    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue

        if char == "(":
            depth += 1
            current.append(char)
            continue

        if char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
            continue

        if char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue

        current.append(char)

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


class SQLGuard:
    """基于本地元数据索引的 SQL 静态校验器。"""

    def __init__(self, index_path: str | Path | None = None) -> None:
        self.index_path = Path(index_path or DEFAULT_INDEX_PATH)
        self.table_columns = self._load_table_columns()
        self.retriever = DeterministicMetadataRetriever(self.index_path)

    def validate(
        self,
        sql: str,
        query: str = "",
        deterministic_candidate_tables: list[str] | None = None,
    ) -> SQLGuardResult:
        normalized_sql = _normalize_sql(sql)
        lower_sql = normalized_sql.lower()

        forbidden_operations = self._find_forbidden_operations(lower_sql)
        used_tables, aliases = self._extract_tables(normalized_sql)
        used_columns, unknown_columns = self._extract_columns(
            normalized_sql, used_tables, aliases
        )

        unknown_tables = [
            table
            for table in used_tables
            if table not in self.table_columns or self._is_system_table(table)
        ]
        system_tables = [table for table in used_tables if self._is_system_table(table)]

        candidate_tables = deterministic_candidate_tables
        if candidate_tables is None and query.strip():
            candidate_tables = [
                item["table_name"] for item in self.retriever.retrieve(query, top_n=10)
            ]
        candidate_tables = candidate_tables or []

        candidate_mismatch = [
            table for table in used_tables if candidate_tables and table not in candidate_tables
        ]

        business_failures = self._business_failures(query, used_tables)
        hard_failures = []
        if not lower_sql.startswith("select"):
            hard_failures.append("仅允许 SELECT SQL")
        if forbidden_operations:
            hard_failures.append("包含禁止操作：" + ", ".join(forbidden_operations))
        if system_tables:
            hard_failures.append("禁止访问系统表：" + ", ".join(system_tables))
        if unknown_tables:
            hard_failures.append("存在未知表：" + ", ".join(unknown_tables))
        if unknown_columns:
            hard_failures.append("存在未知字段：" + ", ".join(unknown_columns))
        hard_failures.extend(business_failures)

        if hard_failures:
            return SQLGuardResult(
                passed=False,
                severity="error",
                used_tables=used_tables,
                used_columns=used_columns,
                unknown_tables=unknown_tables,
                unknown_columns=unknown_columns,
                forbidden_operations=forbidden_operations,
                candidate_mismatch=candidate_mismatch,
                reason="；".join(hard_failures),
            )

        if candidate_mismatch:
            return SQLGuardResult(
                passed=True,
                severity="warning",
                used_tables=used_tables,
                used_columns=used_columns,
                unknown_tables=[],
                unknown_columns=[],
                forbidden_operations=[],
                candidate_mismatch=candidate_mismatch,
                reason="SQL 表不在 deterministic candidate tables 中，需人工关注",
            )

        return SQLGuardResult(
            passed=True,
            severity="ok",
            used_tables=used_tables,
            used_columns=used_columns,
            unknown_tables=[],
            unknown_columns=[],
            forbidden_operations=[],
            candidate_mismatch=[],
            reason="SQL 静态校验通过",
        )

    def _load_table_columns(self) -> dict[str, set[str]]:
        if not self.index_path.exists():
            raise FileNotFoundError(f"元数据索引不存在: {self.index_path}")

        rows = json.loads(self.index_path.read_text(encoding="utf-8"))
        table_columns: dict[str, set[str]] = {}
        for row in rows:
            table = _clean_identifier(str(row.get("table") or ""))
            column = _clean_identifier(str(row.get("column") or ""))
            if not table or not column:
                continue
            table_columns.setdefault(table, set()).add(column)
        return table_columns

    def _find_forbidden_operations(self, lower_sql: str) -> list[str]:
        found = []
        for operation in sorted(FORBIDDEN_OPERATIONS):
            if re.search(rf"\b{operation}\b", lower_sql):
                found.append(operation.upper())
        return found

    def _extract_tables(self, sql: str) -> tuple[list[str], dict[str, str]]:
        used_tables: list[str] = []
        aliases: dict[str, str] = {}
        pattern = re.compile(
            r"\b(?:from|join)\s+([a-zA-Z_][\w.]*|\"[^\"]+\"|`[^`]+`)"
            r"(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
            flags=re.I,
        )

        for match in pattern.finditer(sql):
            raw_table = match.group(1)
            alias = match.group(2)
            table = self._normalize_table_name(raw_table)
            if table not in used_tables:
                used_tables.append(table)
            if alias:
                alias_name = _clean_identifier(alias)
                if alias_name not in {
                    "where",
                    "on",
                    "join",
                    "left",
                    "right",
                    "inner",
                    "outer",
                    "full",
                    "cross",
                    "group",
                    "order",
                    "limit",
                }:
                    aliases[alias_name] = table
            aliases[table] = table

        return used_tables, aliases

    def _normalize_table_name(self, raw_table: str) -> str:
        cleaned = _clean_identifier(raw_table)
        parts = cleaned.split(".")
        if len(parts) >= 2 and parts[0] in {"information_schema", "pg_catalog"}:
            return ".".join(parts[:2])
        return parts[-1]

    def _extract_columns(
        self,
        sql: str,
        used_tables: list[str],
        aliases: dict[str, str],
    ) -> tuple[list[str], list[str]]:
        select_match = re.search(r"\bselect\b\s+(.*?)\s+\bfrom\b", sql, flags=re.I | re.S)
        if not select_match:
            return [], []

        select_part = select_match.group(1).strip()
        if select_part == "*":
            return [], []

        used_columns: list[str] = []
        unknown_columns: list[str] = []

        for expression in _split_csv(select_part):
            for table_name, column_name in self._columns_from_expression(expression, aliases):
                if column_name == "*":
                    continue
                resolved_table = table_name
                if resolved_table and resolved_table in aliases:
                    resolved_table = aliases[resolved_table]

                if resolved_table:
                    column_ref = f"{resolved_table}.{column_name}"
                    if not self._column_exists(resolved_table, column_name):
                        unknown_columns.append(column_ref)
                    elif column_ref not in used_columns:
                        used_columns.append(column_ref)
                    continue

                matched_tables = [
                    table for table in used_tables if self._column_exists(table, column_name)
                ]
                if matched_tables:
                    for matched_table in matched_tables[:1]:
                        column_ref = f"{matched_table}.{column_name}"
                        if column_ref not in used_columns:
                            used_columns.append(column_ref)
                else:
                    unknown_columns.append(column_name)

        return used_columns, sorted(set(unknown_columns))

    def _columns_from_expression(
        self, expression: str, aliases: dict[str, str]
    ) -> list[tuple[str, str]]:
        expr = re.sub(r"\s+as\s+[a-zA-Z_][\w]*$", "", expression.strip(), flags=re.I)
        expr = re.sub(r"\s+[a-zA-Z_][\w]*$", "", expr).strip()

        if re.fullmatch(r"[*]", expr):
            return [("", "*")]
        if re.fullmatch(r"\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"", expr):
            return []

        qualified = [
            (_clean_identifier(table), _clean_identifier(column))
            for table, column in re.findall(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*|\*)\b", expr)
        ]
        if qualified:
            return qualified

        function_match = re.fullmatch(r"[a-zA-Z_][\w]*\((.*)\)", expr)
        if function_match:
            inner = function_match.group(1).strip()
            if inner == "*" or not inner:
                return []
            return self._columns_from_expression(inner, aliases)

        identifiers = [
            _clean_identifier(item)
            for item in re.findall(r"\b[a-zA-Z_][\w]*\b", expr)
            if _clean_identifier(item)
            not in {
                "case",
                "when",
                "then",
                "else",
                "end",
                "null",
                "true",
                "false",
            }
        ]
        return [("", identifiers[0])] if len(identifiers) == 1 else []

    def _column_exists(self, table: str, column: str) -> bool:
        if column == "remaining_missing":
            return False
        return column in self.table_columns.get(table, set())

    def _is_system_table(self, table: str) -> bool:
        return table.startswith(SYSTEM_TABLE_PREFIXES)

    def _business_failures(self, query: str, used_tables: list[str]) -> list[str]:
        query_compact = re.sub(r"\s+", "", query)
        failures: list[str] = []

        if (
            "水质" in query_compact
            and any(word in query_compact for word in ("时间段", "变化", "趋势"))
            and "wm_waterquality_threshold" in used_tables
        ):
            failures.append("水质趋势类问题禁止使用 wm_waterquality_threshold")

        if (
            "排污口" in query_compact
            and "溯源" in query_compact
            and used_tables == ["rs_outlet"]
        ):
            failures.append("排污口溯源问题不能仅使用 rs_outlet 基础信息表")

        return failures

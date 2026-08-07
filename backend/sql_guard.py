from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.metadata_retriever import (
    DeterministicMetadataRetriever,
    resolve_index_path,
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

SQL_KEYWORDS = {
    "and",
    "as",
    "asc",
    "between",
    "by",
    "case",
    "desc",
    "else",
    "end",
    "false",
    "from",
    "group",
    "having",
    "in",
    "is",
    "join",
    "left",
    "like",
    "limit",
    "not",
    "null",
    "on",
    "or",
    "order",
    "select",
    "then",
    "true",
    "when",
    "where",
}

SQL_FUNCTIONS = {
    "avg",
    "coalesce",
    "count",
    "date_trunc",
    "max",
    "min",
    "round",
    "sum",
}

SQL_IDENT_PATTERN = (
    r'"(?:[^"]|"")+"|`(?:[^`]|``)+`|[a-zA-Z_][\w]*'
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
    # E-2B：物理表列身份与拒绝证据（无 schema 上下文的旧路径为空）。
    used_physical_tables: set[tuple[str, str]] = field(default_factory=set)
    used_physical_columns: set[tuple[str, str, str]] = field(default_factory=set)
    wildcard_references: list[dict] = field(default_factory=list)
    ambiguous_columns: list[dict] = field(default_factory=list)
    unresolved_lineage: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _DerivedTable:
    alias: str
    sql: str
    output_columns: frozenset[str]
    start: int
    end: int


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

    def __init__(
        self,
        index_path: str | Path | None = None,
        *,
        database_type: str | None = None,
        default_schema: str | None = None,
    ) -> None:
        self.index_path = resolve_index_path(index_path)
        self.database_type = database_type
        self.default_schema = default_schema
        self.table_columns = self._load_table_columns()
        self.schema_table_columns = self._load_schema_table_columns()
        self.table_name_index = self._build_table_name_index()
        self.retriever = DeterministicMetadataRetriever(self.index_path)

    def validate(
        self,
        sql: str,
        query: str = "",
        deterministic_candidate_tables: list[str] | None = None,
    ) -> SQLGuardResult:
        normalized_sql = _normalize_sql(sql)
        lower_sql = normalized_sql.lower()
        if self.database_type is not None:
            return self._validate_identity(
                normalized_sql,
                query=query,
                deterministic_candidate_tables=deterministic_candidate_tables,
            )

        forbidden_operations = self._find_forbidden_operations(lower_sql)
        used_tables, used_columns, unknown_columns = self._analyze_sql(normalized_sql)

        unknown_tables = [
            table
            for table in used_tables
            if (
                table not in self.table_columns
                and not self._is_cte_or_subquery_alias(table, normalized_sql)
            )
            or self._is_system_table(table)
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
        if not self._is_select_sql(normalized_sql):
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

    def _load_schema_table_columns(
        self,
    ) -> dict[tuple[str, str], set[str]]:
        """schema 感知索引：(schema, table) -> set[column]。

        MySQL 一律按 default_schema（数据库名）规范化；
        PostgreSQL 保留元数据中的 schema。"""
        if not self.index_path.exists() or self.database_type is None:
            return {}
        rows = json.loads(self.index_path.read_text(encoding="utf-8"))
        result: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            schema = self._normalize_schema(str(row.get("schema") or ""))
            table = _clean_identifier(str(row.get("table") or ""))
            column = _clean_identifier(str(row.get("column") or ""))
            if not schema or not table or not column:
                continue
            result.setdefault((schema, table), set()).add(column)
        return result

    def _build_table_name_index(self) -> dict[str, list[tuple[str, str]]]:
        result: dict[str, list[tuple[str, str]]] = {}
        for schema, table in self.schema_table_columns:
            result.setdefault(table, []).append((schema, table))
        for values in result.values():
            values.sort()
        return result

    def _normalize_schema(self, schema: str) -> str:
        if self.database_type == "mysql":
            return str(self.default_schema or "")
        return schema

    def _resolve_table_identity(
        self,
        raw_table: str,
    ) -> tuple[str, str, str, str]:
        """按方言解析物理表身份。

        返回 (status, schema, table, reason)；status 取值 ok/unknown/ambiguous。
        """
        parts = self._split_qualified(raw_table)
        if len(parts) >= 2:
            schema = self._normalize_schema(parts[-2])
            table = parts[-1]
            if (schema, table) in self.schema_table_columns:
                return ("ok", schema, table, "")
            return ("unknown", schema, table, f"表不存在：{schema}.{table}")
        table = parts[-1] if parts else _clean_identifier(raw_table)
        if not table:
            return ("unknown", "", "", "table name empty")
        if self.database_type == "mysql":
            schema = str(self.default_schema or "")
            if (schema, table) in self.schema_table_columns:
                return ("ok", schema, table, "")
            return ("unknown", schema, table, f"表不存在：{schema}.{table}")
        candidates = self.table_name_index.get(table, [])
        if len(candidates) == 1:
            return ("ok", candidates[0][0], candidates[0][1], "")
        if not candidates:
            return ("unknown", "", table, f"表不存在：{table}")
        return (
            "ambiguous",
            "",
            table,
            f"多 schema 同名表：{table}（{candidates}）",
        )

    @staticmethod
    def _split_qualified(raw_table: str) -> list[str]:
        parts: list[str] = []
        current: list[str] = []
        quote: str | None = None
        for char in raw_table.strip():
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                continue
            if char in {'\"', '`'}:
                quote = char
                current.append(char)
            elif char == '.':
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(char)
        tail = ''.join(current).strip()
        if tail:
            parts.append(tail)
        return [_clean_identifier(part) for part in parts if part]

    def _find_forbidden_operations(self, lower_sql: str) -> list[str]:
        found = []
        for operation in sorted(FORBIDDEN_OPERATIONS):
            if re.search(rf"\b{operation}\b", lower_sql):
                found.append(operation.upper())
        return found

    def _is_select_sql(self, sql: str) -> bool:
        lower_sql = sql.lower().strip()
        if lower_sql.startswith("select"):
            return True
        if not lower_sql.startswith("with"):
            return False

        _, _, main_sql = self._extract_ctes(sql)
        return main_sql.lower().startswith("select")

    def _analyze_sql(
        self,
        sql: str,
        outer_virtual_columns: dict[str, set[str]] | None = None,
    ) -> tuple[list[str], list[str], list[str]]:
        cte_sqls, cte_columns, main_sql = self._extract_ctes(sql)
        virtual_columns = dict(outer_virtual_columns or {})
        virtual_columns.update(cte_columns)

        used_tables: list[str] = []
        used_columns: list[str] = []
        unknown_columns: list[str] = []

        for cte_sql in cte_sqls.values():
            cte_tables, cte_used_columns, cte_unknown_columns = self._analyze_sql(
                cte_sql, virtual_columns
            )
            self._extend_unique(used_tables, cte_tables)
            self._extend_unique(used_columns, cte_used_columns)
            self._extend_unique(unknown_columns, cte_unknown_columns)

        derived_tables = self._extract_derived_tables(main_sql)
        derived_aliases = {item.alias for item in derived_tables}
        for item in derived_tables:
            virtual_columns[item.alias] = set(item.output_columns)
            derived_used_tables, derived_used_columns, derived_unknown_columns = (
                self._analyze_sql(item.sql, virtual_columns)
            )
            self._extend_unique(used_tables, derived_used_tables)
            self._extend_unique(used_columns, derived_used_columns)
            self._extend_unique(unknown_columns, derived_unknown_columns)

        rewritten_sql = self._rewrite_derived_tables(main_sql, derived_tables)
        subqueries = self._extract_subqueries(rewritten_sql)
        outer_sql = self._remove_parenthesized_subqueries(rewritten_sql)

        tables, aliases = self._extract_tables(outer_sql)
        self._extend_unique(
            used_tables, [table for table in tables if table not in derived_aliases]
        )

        query_columns, query_unknown_columns = self._extract_columns(
            outer_sql, tables, aliases, virtual_columns
        )
        self._extend_unique(used_columns, query_columns)
        self._extend_unique(unknown_columns, query_unknown_columns)

        for subquery in subqueries:
            subquery_tables, subquery_columns, subquery_unknown_columns = self._analyze_sql(
                subquery, virtual_columns
            )
            self._extend_unique(used_tables, subquery_tables)
            self._extend_unique(used_columns, subquery_columns)
            self._extend_unique(unknown_columns, subquery_unknown_columns)

        return used_tables, used_columns, sorted(set(unknown_columns))

    def _extract_derived_tables(self, sql: str) -> list[_DerivedTable]:
        derived_tables: list[_DerivedTable] = []
        keyword_pattern = re.compile(r"\b(?:from|join)\b", flags=re.I)
        reserved_aliases = {
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
            "having",
            "limit",
            "union",
        }

        for match in keyword_pattern.finditer(sql):
            if self._paren_depth(sql, match.start()) != 0:
                continue
            open_index = match.end()
            while open_index < len(sql) and sql[open_index].isspace():
                open_index += 1
            if open_index >= len(sql) or sql[open_index] != "(":
                continue

            close_index = self._find_matching_paren(sql, open_index)
            if close_index == -1:
                continue
            inner_sql = sql[open_index + 1 : close_index].strip()
            if not inner_sql.lower().startswith(("select", "with")):
                continue

            alias_match = re.match(
                r"\s+(?:as\s+)?([a-zA-Z_][\w]*)",
                sql[close_index + 1 :],
                flags=re.I,
            )
            if not alias_match:
                continue
            alias = _clean_identifier(alias_match.group(1))
            if alias in reserved_aliases:
                continue
            end = close_index + 1 + alias_match.end()
            derived_tables.append(
                _DerivedTable(
                    alias=alias,
                    sql=inner_sql,
                    output_columns=frozenset(
                        self._infer_select_output_columns(inner_sql)
                    ),
                    start=open_index,
                    end=end,
                )
            )

        return derived_tables

    def _rewrite_derived_tables(
        self, sql: str, derived_tables: list[_DerivedTable]
    ) -> str:
        rewritten = sql
        for item in reversed(derived_tables):
            rewritten = rewritten[: item.start] + item.alias + rewritten[item.end :]
        return rewritten

    def _extract_ctes(self, sql: str) -> tuple[dict[str, str], dict[str, set[str]], str]:
        stripped_sql = sql.strip()
        if not stripped_sql.lower().startswith("with "):
            return {}, {}, sql

        position = 4
        cte_sqls: dict[str, str] = {}
        cte_columns: dict[str, set[str]] = {}

        while position < len(stripped_sql):
            name_match = re.match(
                r"\s*([a-zA-Z_][\w]*)(?:\s*\(([^)]*)\))?\s+as\s*\(",
                stripped_sql[position:],
                flags=re.I,
            )
            if not name_match:
                break

            cte_name = _clean_identifier(name_match.group(1))
            explicit_columns = {
                _clean_identifier(column)
                for column in (name_match.group(2) or "").split(",")
                if column.strip()
            }
            inner_start = position + name_match.end()
            inner_end = self._find_matching_paren(stripped_sql, inner_start - 1)
            if inner_end == -1:
                break

            cte_sql = stripped_sql[inner_start:inner_end]
            cte_sqls[cte_name] = cte_sql
            cte_columns[cte_name] = explicit_columns or self._infer_select_output_columns(
                cte_sql
            )

            position = inner_end + 1
            comma_match = re.match(r"\s*,", stripped_sql[position:])
            if comma_match:
                position += comma_match.end()
                continue
            break

        return cte_sqls, cte_columns, stripped_sql[position:].strip()

    def _extract_tables(self, sql: str) -> tuple[list[str], dict[str, str]]:
        used_tables: list[str] = []
        aliases: dict[str, str] = {}
        pattern = re.compile(
            r"\b(?:from|join)\s+"
            r"((?:\"[^\"]+\"|`[^`]+`)(?:\.(?:\"[^\"]+\"|`[^`]+`))*"
            r"|[a-zA-Z_][\w.]*)"
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
        virtual_columns: dict[str, set[str]] | None = None,
    ) -> tuple[list[str], list[str]]:
        used_columns: list[str] = []
        unknown_columns: list[str] = []
        virtual_columns = virtual_columns or {}

        for expression in self._extract_field_expressions(sql):
            for table_name, column_name in self._columns_from_expression(expression):
                if column_name == "*":
                    continue
                resolved_table = table_name
                if resolved_table and resolved_table in aliases:
                    resolved_table = aliases[resolved_table]

                if resolved_table:
                    column_ref = f"{resolved_table}.{column_name}"
                    if not self._column_exists(
                        resolved_table, column_name, virtual_columns
                    ):
                        unknown_columns.append(column_ref)
                    elif column_ref not in used_columns:
                        used_columns.append(column_ref)
                    continue

                matched_tables = [
                    table
                    for table in used_tables
                    if self._column_exists(table, column_name, virtual_columns)
                ]
                if matched_tables:
                    for matched_table in matched_tables[:1]:
                        column_ref = f"{matched_table}.{column_name}"
                        if column_ref not in used_columns:
                            used_columns.append(column_ref)
                else:
                    unknown_columns.append(column_name)

        return used_columns, sorted(set(unknown_columns))

    def _extract_field_expressions(self, sql: str) -> list[str]:
        expressions: list[str] = []

        select_part = self._extract_between_keywords(sql, "select", ["from"])
        if select_part and select_part.strip() != "*":
            expressions.extend(_split_csv(select_part))

        for keyword, end_keywords in [
            ("where", ["group by", "order by", "having", "limit", "union"]),
            ("group by", ["having", "order by", "limit", "union"]),
            ("order by", ["limit", "union"]),
            ("having", ["order by", "limit", "union"]),
        ]:
            clause = self._extract_between_keywords(sql, keyword, end_keywords)
            if clause:
                expressions.extend(_split_csv(self._remove_parenthesized_subqueries(clause)))

        expressions.extend(self._extract_join_on_expressions(sql))
        return expressions

    def _extract_between_keywords(
        self, sql: str, start_keyword: str, end_keywords: list[str]
    ) -> str:
        start = self._find_top_level_keyword(sql, start_keyword)
        if start == -1:
            return ""
        content_start = start + len(start_keyword)

        end_positions = [
            position
            for keyword in end_keywords
            if (position := self._find_top_level_keyword(sql, keyword, content_start)) != -1
        ]
        content_end = min(end_positions) if end_positions else len(sql)
        return sql[content_start:content_end].strip()

    def _extract_join_on_expressions(self, sql: str) -> list[str]:
        expressions: list[str] = []
        pattern = re.compile(r"\bon\b", flags=re.I)
        for match in pattern.finditer(sql):
            if self._paren_depth(sql, match.start()) != 0:
                continue
            start = match.end()
            end_positions = [
                position
                for keyword in ["join", "where", "group by", "order by", "having", "limit"]
                if (position := self._find_top_level_keyword(sql, keyword, start)) != -1
            ]
            end = min(end_positions) if end_positions else len(sql)
            expressions.append(sql[start:end].strip())
        return expressions

    def _columns_from_expression(self, expression: str) -> list[tuple[str, str]]:
        expr = re.sub(r"\s+as\s+[a-zA-Z_][\w]*$", "", expression.strip(), flags=re.I)
        expr = re.sub(r"'[^']*'", " ", expr)
        expr = re.sub(r"\"[^\"]*\"", " ", expr)

        if re.fullmatch(r"[*]", expr):
            return [("", "*")]
        if re.fullmatch(r"\d+(?:\.\d+)?", expr):
            return []

        qualified = [
            (_clean_identifier(table), _clean_identifier(column))
            for table, column in re.findall(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*|\*)\b", expr)
        ]
        if qualified:
            return qualified

        identifiers = [
            _clean_identifier(item)
            for item in re.findall(r"\b[a-zA-Z_][\w]*\b", expr)
            if self._is_possible_column_identifier(item)
        ]
        return [("", identifier) for identifier in identifiers]

    def _column_exists(
        self,
        table: str,
        column: str,
        virtual_columns: dict[str, set[str]] | None = None,
    ) -> bool:
        if column == "remaining_missing":
            return False
        if virtual_columns and column in virtual_columns.get(table, set()):
            return True
        return column in self.table_columns.get(table, set())

    def _is_possible_column_identifier(self, identifier: str) -> bool:
        value = _clean_identifier(identifier)
        return bool(value) and value not in SQL_KEYWORDS and value not in SQL_FUNCTIONS

    def _infer_select_output_columns(self, sql: str) -> set[str]:
        select_part = self._extract_between_keywords(sql, "select", ["from"])
        if not select_part:
            return set()

        columns: set[str] = set()
        for expression in _split_csv(select_part):
            alias_match = re.search(r"\s+as\s+([a-zA-Z_][\w]*)$", expression, flags=re.I)
            if not alias_match:
                alias_match = re.search(r"\s+([a-zA-Z_][\w]*)$", expression)
            if alias_match and not expression.strip().lower().endswith(")"):
                columns.add(_clean_identifier(alias_match.group(1)))
                continue

            refs = self._columns_from_expression(expression)
            if len(refs) == 1:
                columns.add(refs[0][1])
        return columns

    def _extract_subqueries(self, sql: str) -> list[str]:
        return [
            sql[start + 1 : end].strip()
            for start, end in self._extract_subquery_spans(sql)
        ]

    def _extract_subquery_spans(self, sql: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        position = 0
        while position < len(sql):
            if sql[position] != "(":
                position += 1
                continue
            end = self._find_matching_paren(sql, position)
            if end == -1:
                position += 1
                continue
            inner = sql[position + 1 : end].strip()
            if inner.lower().startswith(("select", "with")):
                spans.append((position, end))
                position = end + 1
                continue
            position += 1
        return spans

    def _find_matching_paren(self, sql: str, open_index: int) -> int:
        depth = 0
        quote: str | None = None
        for index in range(open_index, len(sql)):
            char = sql[index]
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return index
        return -1

    def _find_top_level_keyword(
        self, sql: str, keyword: str, start: int = 0
    ) -> int:
        keyword_pattern = r"\s+".join(re.escape(part) for part in keyword.split())
        pattern = re.compile(rf"\b{keyword_pattern}\b", flags=re.I)
        for match in pattern.finditer(sql, start):
            if self._paren_depth(sql, match.start()) == 0:
                return match.start()
        return -1

    def _remove_parenthesized_subqueries(self, expression: str) -> str:
        result = expression
        for start, end in reversed(self._extract_subquery_spans(expression)):
            result = result[:start] + " " + result[end + 1 :]
        return result

    def _paren_depth(self, sql: str, position: int) -> int:
        depth = 0
        quote: str | None = None
        for char in sql[:position]:
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in {"'", '"'}:
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(depth - 1, 0)
        return depth

    def _extend_unique(self, target: list[str], values: list[str]) -> None:
        for value in values:
            if value not in target:
                target.append(value)

    def _is_cte_or_subquery_alias(self, table: str, sql: str) -> bool:
        _, cte_columns, _ = self._extract_ctes(sql)
        return table in cte_columns

    def _is_system_table(self, table: str) -> bool:
        return table.startswith(SYSTEM_TABLE_PREFIXES)

    # ------------------------------------------------------------------
    # E-2B：物理表列身份分析（schema 感知、通配符、歧义、lineage）
    # ------------------------------------------------------------------

    def _validate_identity(
        self,
        sql: str,
        *,
        query: str = "",
        deterministic_candidate_tables: list[str] | None = None,
    ) -> SQLGuardResult:
        lower_sql = sql.lower()
        forbidden_operations = self._find_forbidden_operations(lower_sql)
        identity = self._analyze_identity(sql)
        physical_tables = identity["physical_tables"]
        physical_columns = identity["physical_columns"]
        wildcards = identity["wildcards"]
        ambiguous = identity["ambiguous"]
        unresolved = identity["unresolved"]
        unknown_tables = identity["unknown_tables"]
        unknown_columns = identity["unknown_columns"]

        system_tables = [table for table in unknown_tables if self._is_system_table(table)]
        hard_failures: list[str] = []
        if not self._is_select_sql(sql):
            hard_failures.append("仅允许 SELECT SQL")
        if forbidden_operations:
            hard_failures.append(
                "包含禁止操作：" + ", ".join(forbidden_operations)
            )
        if system_tables:
            hard_failures.append("禁止访问系统表：" + ", ".join(system_tables))
        if unknown_tables:
            hard_failures.append("存在未知表：" + ", ".join(unknown_tables))
        if unknown_columns:
            hard_failures.append("存在未知字段：" + ", ".join(unknown_columns))
        if wildcards:
            hard_failures.append(
                "存在通配符引用："
                + ", ".join(str(item.get("expression")) for item in wildcards)
            )
        if ambiguous:
            hard_failures.append(
                "存在歧义字段："
                + ", ".join(str(item.get("column")) for item in ambiguous)
            )
        if unresolved:
            hard_failures.append(
                "存在无法解析的身份："
                + ", ".join(str(item.get("name")) for item in unresolved)
            )

        used_tables = sorted({table for _, table in physical_tables})
        used_columns = sorted(
            {f"{table}.{column}" for _, table, column in physical_columns}
        )
        base = dict(
            used_tables=used_tables,
            used_columns=used_columns,
            unknown_tables=unknown_tables,
            unknown_columns=unknown_columns,
            forbidden_operations=forbidden_operations,
            candidate_mismatch=[],
            used_physical_tables=physical_tables,
            used_physical_columns=physical_columns,
            wildcard_references=wildcards,
            ambiguous_columns=ambiguous,
            unresolved_lineage=unresolved,
        )
        if hard_failures:
            return SQLGuardResult(
                passed=False,
                severity="error",
                reason="；".join(hard_failures),
                **base,
            )

        candidate_tables = deterministic_candidate_tables
        if candidate_tables is None and query.strip():
            candidate_tables = [
                item["table_name"]
                for item in self.retriever.retrieve(query, top_n=10)
            ]
        candidate_tables = candidate_tables or []
        candidate_mismatch = [
            table
            for table in used_tables
            if candidate_tables and table not in candidate_tables
        ]
        if candidate_mismatch:
            return SQLGuardResult(
                passed=True,
                severity="warning",
                reason="SQL 表不在 deterministic candidate tables 中，需人工关注",
                candidate_mismatch=candidate_mismatch,
                **base,
            )
        return SQLGuardResult(
            passed=True,
            severity="ok",
            reason="SQL 静态校验通过",
            **base,
        )

    def _analyze_identity(
        self,
        sql: str,
        *,
        outer_relations: list[dict] | None = None,
    ) -> dict:
        result: dict = {
            "physical_tables": set(),
            "physical_columns": set(),
            "wildcards": [],
            "ambiguous": [],
            "unresolved": [],
            "unknown_tables": [],
            "unknown_columns": [],
            "relations": list(outer_relations or []),
            "block_lineage": {},
        }
        cte_sqls, _, main_sql = self._extract_ctes(sql)
        for cte_name, cte_sql in cte_sqls.items():
            inner = self._analyze_identity(
                cte_sql,
                outer_relations=result["relations"],
            )
            self._merge_identity(result, inner)
            lineage = inner["block_lineage"]
            columns = {
                column for values in lineage.values() for column in values
            }
            result["relations"].append(
                {
                    "name": cte_name,
                    "tables": frozenset(inner["physical_tables"]),
                    "columns": frozenset(columns),
                    "lineage": lineage,
                }
            )

        derived_tables = self._extract_derived_tables(main_sql)
        for item in derived_tables:
            inner = self._analyze_identity(
                item.sql,
                outer_relations=result["relations"],
            )
            self._merge_identity(result, inner)
            lineage = inner["block_lineage"]
            columns = {
                column for values in lineage.values() for column in values
            }
            result["relations"].append(
                {
                    "name": item.alias,
                    "tables": frozenset(inner["physical_tables"]),
                    "columns": frozenset(columns),
                    "lineage": lineage,
                }
            )

        rewritten = self._rewrite_derived_tables(main_sql, derived_tables)
        subqueries = self._extract_subqueries(rewritten)
        outer_sql = self._remove_parenthesized_subqueries(rewritten)
        for subquery in subqueries:
            inner = self._analyze_identity(
                subquery,
                outer_relations=result["relations"],
            )
            self._merge_identity(result, inner)

        virtual_names = {relation["name"] for relation in result["relations"]}
        physical_by_name: dict[tuple[str, str], dict] = {}
        for raw_table, alias in self._extract_table_tokens(outer_sql):
            cleaned = _clean_identifier(raw_table)
            if cleaned in virtual_names:
                continue
            status, schema, table, reason = self._resolve_table_identity(
                raw_table
            )
            if status == "ok":
                key = (schema, table)
                result["physical_tables"].add(key)
                relation = physical_by_name.setdefault(
                    key,
                    self._physical_relation(key),
                )
                if alias:
                    alias_name = _clean_identifier(alias)
                    result["relations"].append(
                        {
                            "name": alias_name,
                            "tables": relation["tables"],
                            "columns": relation["columns"],
                            "lineage": relation["lineage"],
                        }
                    )
            elif status == "unknown":
                self._extend_unique(result["unknown_tables"], [raw_table])
            else:
                result["unresolved"].append(
                    {
                        "kind": "table",
                        "name": raw_table,
                        "reason": reason,
                    }
                )
        for relation in physical_by_name.values():
            result["relations"].append(relation)

        select_part = self._extract_between_keywords(
            outer_sql, "select", ["from"]
        )
        if select_part is not None and select_part.strip() == "*":
            result["wildcards"].append(
                {"expression": "*", "context": "select"}
            )
        select_expressions = self._extract_select_expressions(outer_sql)
        for expression in self._extract_field_expressions(outer_sql):
            wildcard = self._detect_wildcard(expression)
            if wildcard == "wildcard":
                result["wildcards"].append(
                    {"expression": expression, "context": "select"}
                )
                continue
            if wildcard == "count_star":
                continue
            columns, unknown, ambiguous = self._resolve_expression_columns(
                expression,
                result["relations"],
            )
            result["physical_columns"].update(columns)
            for item in unknown:
                if item not in result["unknown_columns"]:
                    result["unknown_columns"].append(item)
            result["ambiguous"].extend(ambiguous)

        lineage: dict[str, frozenset[tuple[str, str, str]]] = {}
        for expression in select_expressions:
            name, body = self._expression_alias(expression)
            if not name:
                continue
            wildcard = self._detect_wildcard(body)
            if wildcard == "count_star":
                lineage[name] = frozenset()
                continue
            if wildcard == "wildcard":
                continue
            columns, _, _ = self._resolve_expression_columns(
                body,
                result["relations"],
            )
            lineage[name] = frozenset(columns)
        result["block_lineage"] = lineage
        return result

    def _extract_table_tokens(self, sql: str) -> list[tuple[str, str | None]]:
        pattern = re.compile(
            r"\b(?:from|join)\s+"
            r"((?:\"[^\"]+\"|`[^`]+`)(?:\.(?:\"[^\"]+\"|`[^`]+`))*"
            r"|[a-zA-Z_][\w.]*)"
            r"(?:\s+(?:as\s+)?(?!(?:join|left|right|inner|outer|full|cross|on|where|group|order|having|limit|union)\b)([a-zA-Z_][\w]*))?",
            flags=re.I,
        )
        return [
            (match.group(1), match.group(2))
            for match in pattern.finditer(sql)
        ]

    def _physical_relation(self, key: tuple[str, str]) -> dict:
        schema, table = key
        columns = self.schema_table_columns.get(key, set())
        physical_columns = frozenset(
            (schema, table, column) for column in columns
        )
        return {
            "name": table,
            "tables": frozenset({key}),
            "columns": physical_columns,
            "lineage": {
                column: frozenset({(schema, table, column)})
                for column in columns
            },
        }

    @staticmethod
    def _relation_column(
        relation: dict,
        column: str,
    ) -> frozenset[tuple[str, str, str]] | None:
        cleaned = _clean_identifier(column)
        if cleaned in relation["lineage"]:
            return relation["lineage"][cleaned]
        return None

    @staticmethod
    def _strip_alias_text(expression: str) -> str:
        expr = expression.strip()
        alias_match = re.search(
            r"\s+as\s+[a-zA-Z_][\w]*$",
            expr,
            flags=re.I,
        )
        if alias_match:
            return expr[: alias_match.start()].strip()
        return expr

    def _detect_wildcard(self, expression: str) -> str | None:
        """引号/括号/空白感知的投影通配符检测。

        仅允许严格 COUNT(*)（函数名 COUNT、圆括号内只有 *）；其余任何
        投影表达式中的 *（含 "table".*、`table`.*、(alias).*、alias . *、
        COUNT(table.*) 等变体）一律判定为通配符。
        """
        body = self._strip_alias_text(expression)
        body = re.sub(r"'[^']*'", " ", body)
        stars = self._scan_projection_stars(body)
        if not stars:
            return None
        for star_index in stars:
            if not self._is_strict_count_star(body, star_index):
                return "wildcard"
        return "count_star"

    @staticmethod
    def _scan_projection_stars(body: str) -> list[int]:
        """返回表达式主体中引号外的所有 * 下标。"""
        stars: list[int] = []
        quote: str | None = None
        index = 0
        while index < len(body):
            char = body[index]
            if quote:
                if char == quote:
                    if index + 1 < len(body) and body[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in {'"', "`"}:
                quote = char
                index += 1
                continue
            if char == "*":
                stars.append(index)
            index += 1
        return stars

    @staticmethod
    def _is_strict_count_star(body: str, star_index: int) -> bool:
        """判断 * 是否属于严格的 COUNT( * ) 调用内部。"""
        left = body[:star_index]
        right = body[star_index + 1 :]
        j = len(left) - 1
        while j >= 0 and left[j].isspace():
            j -= 1
        if j < 0 or left[j] != "(":
            return False
        k = j - 1
        while k >= 0 and left[k].isspace():
            k -= 1
        name_end = k + 1
        while k >= 0 and (left[k].isalnum() or left[k] == "_"):
            k -= 1
        function_name = left[k + 1 : name_end]
        if function_name.lower() != "count":
            return False
        if k >= 0 and (left[k].isalnum() or left[k] == "_"):
            return False
        m = 0
        while m < len(right) and right[m].isspace():
            m += 1
        if m >= len(right) or right[m] != ")":
            return False
        return True

    def _expression_alias(
        self,
        expression: str,
    ) -> tuple[str, str]:
        expr = expression.strip()
        alias_match = re.search(
            r"\s+as\s+([a-zA-Z_][\w]*)$",
            expr,
            flags=re.I,
        )
        if alias_match:
            return (
                _clean_identifier(alias_match.group(1)),
                expr[: alias_match.start()].strip(),
            )
        alias_match = re.search(r"\s+([a-zA-Z_][\w]*)$", expr)
        if alias_match and not expr.lower().endswith(")"):
            return (
                _clean_identifier(alias_match.group(1)),
                expr[: alias_match.start()].strip(),
            )
        refs = self._columns_from_expression(expr)
        if len(refs) == 1 and refs[0][1] != "*":
            return refs[0][1], expr
        return "", expr

    def _extract_select_expressions(self, sql: str) -> list[str]:
        select_part = self._extract_between_keywords(sql, "select", ["from"])
        if not select_part or select_part.strip() == "*":
            return []
        return _split_csv(select_part)

    def _resolve_expression_columns(
        self,
        expression: str,
        relations: list[dict],
    ) -> tuple[set[tuple[str, str, str]], list[str], list[dict]]:
        body = self._strip_alias_text(expression)
        body = re.sub(r"'[^']*'", " ", body)
        columns: set[tuple[str, str, str]] = set()
        unknown: list[str] = []
        ambiguous: list[dict] = []
        relation_names = {relation["name"] for relation in relations}
        chain_pattern = re.compile(
            rf"(?:{SQL_IDENT_PATTERN})(?:\.(?:{SQL_IDENT_PATTERN}))*"
        )
        for match in chain_pattern.finditer(body):
            parts = [
                _clean_identifier(item)
                for item in re.findall(SQL_IDENT_PATTERN, match.group(0))
            ]
            if not parts:
                continue
            if len(parts) == 1:
                identifier = parts[0]
                if (
                    identifier in relation_names
                    or not self._is_possible_column_identifier(identifier)
                ):
                    continue
                matches: list[frozenset[tuple[str, str, str]]] = []
                for relation in relations:
                    physical = self._relation_column(relation, identifier)
                    if physical:
                        matches.append(frozenset(physical))
                distinct = list({match for match in matches})
                if len(distinct) == 1:
                    columns.update(distinct[0])
                elif len(distinct) > 1:
                    ambiguous.append(
                        {
                            "column": identifier,
                            "candidates": sorted(
                                {
                                    column
                                    for match in distinct
                                    for column in match
                                }
                            ),
                        }
                    )
                else:
                    unknown.append(identifier)
                continue
            if len(parts) >= 3:
                schema, table, column = parts[-3], parts[-2], parts[-1]
                matched = [
                    relation
                    for relation in relations
                    if (schema, table) in relation["tables"]
                ]
                resolved: set[tuple[str, str, str]] = set()
                for relation in matched:
                    lineage = self._relation_column(relation, column)
                    if lineage:
                        resolved.update(lineage)
                if not resolved:
                    unknown.append(".".join(parts))
                else:
                    columns.update(resolved)
                continue
            qualifier = parts[-2]
            column = parts[-1]
            matched = [
                relation
                for relation in relations
                if relation["name"] == qualifier
            ]
            if not matched:
                unknown.append(".".join(parts))
                continue
            resolved = set()
            for relation in matched:
                lineage = self._relation_column(relation, column)
                if lineage:
                    resolved.update(lineage)
            distinct_sources = {
                frozenset(relation["tables"]) for relation in matched
            }
            if not resolved:
                unknown.append(".".join(parts))
            elif len(distinct_sources) > 1:
                ambiguous.append(
                    {
                        "column": qualifier,
                        "candidates": sorted(
                            {
                                column_key
                                for relation in matched
                                for column_key in relation["tables"]
                            }
                        ),
                    }
                )
            else:
                columns.update(resolved)
        return columns, unknown, ambiguous

    @staticmethod
    def _merge_identity(target: dict, inner: dict) -> None:
        target["physical_tables"].update(inner["physical_tables"])
        target["physical_columns"].update(inner["physical_columns"])
        target["wildcards"].extend(inner["wildcards"])
        target["ambiguous"].extend(inner["ambiguous"])
        target["unresolved"].extend(inner["unresolved"])
        for table in inner["unknown_tables"]:
            if table not in target["unknown_tables"]:
                target["unknown_tables"].append(table)
        for column in inner["unknown_columns"]:
            if column not in target["unknown_columns"]:
                target["unknown_columns"].append(column)

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

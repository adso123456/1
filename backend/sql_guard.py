from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.metadata_retriever import DeterministicMetadataRetriever


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

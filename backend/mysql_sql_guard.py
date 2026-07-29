"""MySQL SQLGuard 扩展：在既有只读校验前拒绝多语句。"""

from __future__ import annotations

import re

import sqlparse

from backend.sql_guard import (
    SQL_FUNCTIONS,
    SQL_KEYWORDS,
    SQLGuard,
    SQLGuardResult,
)


MYSQL_SQL_FUNCTIONS = SQL_FUNCTIONS | {
    "date",
    "year",
    "month",
    "date_format",
    "ifnull",
}


class MySQLSQLGuard(SQLGuard):
    """复用元数据白名单校验，并只允许一条 SELECT。"""

    def validate(self, sql: str, query: str = "", **kwargs) -> SQLGuardResult:
        statements = [
            statement
            for statement in sqlparse.split(sql)
            if statement.strip().rstrip(";").strip()
        ]
        if len(statements) != 1:
            return SQLGuardResult(
                passed=False,
                severity="error",
                used_tables=[],
                used_columns=[],
                unknown_tables=[],
                unknown_columns=[],
                forbidden_operations=[],
                candidate_mismatch=[],
                reason="仅允许单条 SQL，禁止多语句",
            )
        return super().validate(sql=sql, query=query, **kwargs)

    def _is_possible_column_identifier(self, identifier: str) -> bool:
        value = identifier.strip().strip('"`[]').lower()
        return (
            bool(value)
            and value not in SQL_KEYWORDS
            and value not in MYSQL_SQL_FUNCTIONS
        )

    def _extract_columns(self, sql, used_tables, aliases, virtual_columns=None):
        """MySQL 允许在 ORDER BY/HAVING 中引用 SELECT 输出别名。"""
        used_columns, unknown_columns = super()._extract_columns(
            sql, used_tables, aliases, virtual_columns
        )
        select_part = self._extract_between_keywords(sql, "select", ["from"])
        output_aliases = {
            match.group(1).lower()
            for match in re.finditer(
                r"\bas\s+`?([a-zA-Z_][\w]*)`?\s*(?:,|$)",
                select_part,
                flags=re.I,
            )
        }
        return used_columns, [
            column
            for column in unknown_columns
            if column.strip("`").lower() not in output_aliases
        ]

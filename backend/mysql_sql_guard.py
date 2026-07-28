"""MySQL SQLGuard 扩展：在既有只读校验前拒绝多语句。"""

from __future__ import annotations

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

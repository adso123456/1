"""严格、确定性的简单查询单模型快速路径。"""

from __future__ import annotations

import re
from typing import Any

from backend.query_intent import is_simple_result_query
from backend.query_performance import QueryPerformanceState


_COMPLEX_SQL = re.compile(
    r"\b(group\s+by|having|union|over\s*\(|with\s+.+\bas\s*\(|join)\b",
    flags=re.I | re.S,
)
_UNSAFE_COLUMN = re.compile(r"(?:^|_)(?:geom|geometry|binary|blob)(?:$|_)", re.I)


def qualify_simple_query(state: QueryPerformanceState | None) -> tuple[bool, str]:
    if state is None:
        return False, "missing_request_state"
    if not is_simple_result_query(state.question):
        return False, "query_requires_natural_language_answer"
    if state.run_sql_count != 1 or state.dataframe_count != 1:
        return False, "requires_exactly_one_dataframe"
    if state.provider_retry_count or state.tool_error_count:
        return False, "retry_or_tool_error"
    if state.guard_warning_count:
        return False, "guard_warning"
    if _COMPLEX_SQL.search(state.last_sql or ""):
        return False, "complex_sql"
    metadata = state.last_result_metadata
    row_count = metadata.get("row_count")
    if not isinstance(row_count, int) or not 0 <= row_count <= 50:
        return False, "row_count_out_of_range"
    columns = [str(item) for item in metadata.get("columns") or []]
    if any(_UNSAFE_COLUMN.search(column) for column in columns):
        return False, "unsafe_column"
    for row in metadata.get("results") or []:
        if not isinstance(row, dict):
            return False, "invalid_result_shape"
        if any(isinstance(value, (bytes, bytearray, memoryview)) for value in row.values()):
            return False, "binary_value"
    return True, "eligible"


def deterministic_summary(row_count: int) -> str:
    if row_count == 0:
        return "查询完成，未找到符合条件的记录。"
    if row_count == 1:
        return "查询完成，返回 1 条记录。"
    return f"查询完成，共返回 {row_count} 条记录。"


def build_fast_path_summary(
    state: QueryPerformanceState | None,
) -> tuple[str | None, str]:
    eligible, reason = qualify_simple_query(state)
    if not eligible or state is None:
        return None, reason
    state.fast_path_used = True
    return deterministic_summary(state.last_result_metadata["row_count"]), reason

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Type

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.components import ComponentType, NotificationComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from tools.sql_guard import SQLGuard, SQLGuardResult


class GuardedRunSqlTool(Tool[RunSqlToolArgs]):
    """在执行 run_sql 前调用 SQLGuard 的包装工具。"""

    def __init__(self, inner_tool: Tool[RunSqlToolArgs], sql_guard: SQLGuard) -> None:
        self.inner_tool = inner_tool
        self.sql_guard = sql_guard

    @property
    def name(self) -> str:
        return self.inner_tool.name

    @property
    def description(self) -> str:
        return self.inner_tool.description

    @property
    def access_groups(self) -> list[str]:
        return self.inner_tool.access_groups

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return self.inner_tool.get_args_schema()

    async def execute(self, context: ToolContext, args: RunSqlToolArgs) -> ToolResult:
        sql = getattr(args, "sql", "")
        query, query_source = self._extract_query(context, args)
        if self._is_hard_blocked_context(context):
            guard_result = self._make_hard_block_result(sql, "同一问题已触发 SQL Guard hard block")
            self._trace_attempt(
                query=query,
                query_source=query_source,
                sql=sql,
                guard_result=guard_result,
                blocked_by_sql_guard=True,
                hard_blocked_before_validation=True,
            )
            return self._blocked_result(guard_result, query_source=query_source)

        if self._is_threshold_trend_request(query):
            guard_result = self._make_hard_block_result(
                sql,
                "用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL",
            )
            self._mark_hard_blocked_context(context)
            self._trace_attempt(
                query=query,
                query_source=query_source,
                sql=sql,
                guard_result=guard_result,
                blocked_by_sql_guard=True,
                hard_blocked_before_validation=False,
            )
            return self._blocked_result(guard_result, query_source=query_source)

        guard_result = self.sql_guard.validate(sql=sql, query=query)
        if guard_result.passed and query_source == "missing" and self._should_block_missing_query_threshold_sql(sql):
            guard_result = self.sql_guard.validate(sql=sql, query="水质趋势")

        self._trace_attempt(
            query=query,
            query_source=query_source,
            sql=sql,
            guard_result=guard_result,
            blocked_by_sql_guard=not guard_result.passed,
            hard_blocked_before_validation=False,
        )
        if not guard_result.passed:
            self._mark_hard_blocked_context(context)
            return self._blocked_result(guard_result, query_source=query_source)

        result = await self.inner_tool.execute(context, args)
        result.metadata = dict(result.metadata or {})
        result.metadata["sql_guard"] = guard_result.to_dict()
        result.metadata["blocked_by_sql_guard"] = False
        result.metadata["query_source"] = query_source

        if guard_result.severity == "warning":
            warning = self._format_warning_message(guard_result)
            result.metadata["sql_guard_warning"] = warning
            result.result_for_llm = f"{warning}\n\n{result.result_for_llm}"

        return result

    def _trace_attempt(
        self,
        *,
        query: str,
        query_source: str,
        sql: str,
        guard_result: SQLGuardResult,
        blocked_by_sql_guard: bool,
        hard_blocked_before_validation: bool,
    ) -> None:
        raw_path = os.getenv("VANNA_SQL_GUARD_TRACE_PATH", "").strip()
        if not raw_path:
            return

        trace_path = Path(raw_path)
        if not trace_path.is_absolute():
            raise RuntimeError("SQL_GUARD_TRACE_PATH_NOT_ABSOLUTE")
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "query_source": query_source,
            "attempted_sql": sql,
            "guard_passed": guard_result.passed,
            "guard_severity": guard_result.severity,
            "used_tables": guard_result.used_tables,
            "used_columns": guard_result.used_columns,
            "unknown_tables": guard_result.unknown_tables,
            "unknown_columns": guard_result.unknown_columns,
            "forbidden_operations": guard_result.forbidden_operations,
            "candidate_mismatch": guard_result.candidate_mismatch,
            "guard_reason": guard_result.reason,
            "blocked_by_sql_guard": blocked_by_sql_guard,
            "hard_blocked_before_validation": hard_blocked_before_validation,
        }
        try:
            with trace_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        except Exception as error:
            raise RuntimeError(
                f"SQL_GUARD_TRACE_WRITE_FAILED: {type(error).__name__}: {error}"
            ) from error

    def _blocked_result(self, guard_result: SQLGuardResult, *, query_source: str) -> ToolResult:
        message = self._format_block_message(guard_result, query_source=query_source)
        return ToolResult(
            success=False,
            result_for_llm=message,
            ui_component=UiComponent(
                rich_component=NotificationComponent(
                    type=ComponentType.NOTIFICATION,
                    level="error",
                    message=message,
                ),
                simple_component=SimpleTextComponent(text=message),
            ),
            error=message,
            metadata={
                "sql_guard": guard_result.to_dict(),
                "blocked_by_sql_guard": True,
                "query_source": query_source,
            },
        )

    def _extract_query(self, context: ToolContext, args: RunSqlToolArgs) -> tuple[str, str]:
        metadata = getattr(context, "metadata", None) or {}
        for key in (
            "query",
            "question",
            "user_question",
            "original_question",
            "last_user_message",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"metadata.{key}"

        user_metadata = getattr(getattr(context, "user", None), "metadata", None) or {}
        for key in (
            "query",
            "question",
            "user_question",
            "original_question",
            "last_user_message",
        ):
            value = user_metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"user.metadata.{key}"

        for attr in ("message", "last_user_message", "user_message"):
            value = getattr(context, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"context.{attr}"

        args_dict: dict[str, Any] = {}
        if hasattr(args, "model_dump"):
            args_dict = args.model_dump()
        elif hasattr(args, "dict"):
            args_dict = args.dict()

        for key in ("query", "question", "user_question"):
            value = args_dict.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"args.{key}"

        return "", "missing"

    def _should_block_missing_query_threshold_sql(self, sql: str) -> bool:
        normalized = re.sub(r"\s+", " ", sql).lower()
        if not re.search(r"\bwm_waterquality_threshold\b", normalized):
            return False
        return bool(
            re.search(
                r"select\s+\*|\bm\d+_value\b|\bstation_id\b|\bmonitor_\w+\b|\border\s+by\b|\blimit\b|\b(count|min|max|avg|sum)\s*\(",
                normalized,
            )
        )

    def _is_threshold_trend_request(self, query: str) -> bool:
        normalized = query.lower()
        return (
            "wm_waterquality_threshold" in normalized
            and "水质" in query
            and any(word in query for word in ("趋势", "变化", "时间段"))
        )

    def _make_hard_block_result(self, sql: str, reason: str) -> SQLGuardResult:
        probe_result = self.sql_guard.validate(sql=sql, query="")
        return SQLGuardResult(
            passed=False,
            severity="error",
            used_tables=probe_result.used_tables,
            used_columns=probe_result.used_columns,
            unknown_tables=probe_result.unknown_tables,
            unknown_columns=probe_result.unknown_columns,
            forbidden_operations=probe_result.forbidden_operations,
            candidate_mismatch=probe_result.candidate_mismatch,
            reason=reason,
        )

    def _is_hard_blocked_context(self, context: ToolContext) -> bool:
        metadata = getattr(context, "metadata", None)
        return isinstance(metadata, dict) and metadata.get("sql_guard_hard_blocked") is True

    def _mark_hard_blocked_context(self, context: ToolContext) -> None:
        metadata = getattr(context, "metadata", None)
        if isinstance(metadata, dict):
            metadata["sql_guard_hard_blocked"] = True

    def _format_block_message(self, guard_result: SQLGuardResult, *, query_source: str) -> str:
        return "\n".join(
            [
                "SQL Guard blocked execution",
                f"query_source: {query_source}",
                f"severity: {guard_result.severity}",
                f"used_tables: {', '.join(guard_result.used_tables) or 'none'}",
                f"used_columns: {', '.join(guard_result.used_columns) or 'none'}",
                f"unknown_tables: {', '.join(guard_result.unknown_tables) or 'none'}",
                f"unknown_columns: {', '.join(guard_result.unknown_columns) or 'none'}",
                f"forbidden_operations: {', '.join(guard_result.forbidden_operations) or 'none'}",
                f"candidate_mismatch: {', '.join(guard_result.candidate_mismatch) or 'none'}",
                f"reason: {guard_result.reason}",
            ]
        )

    def _format_warning_message(self, guard_result: SQLGuardResult) -> str:
        return "\n".join(
            [
                "SQL Guard warning",
                f"severity: {guard_result.severity}",
                f"candidate_mismatch: {', '.join(guard_result.candidate_mismatch) or 'none'}",
                f"reason: {guard_result.reason}",
            ]
        )

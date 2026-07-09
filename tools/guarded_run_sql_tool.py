from __future__ import annotations

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
        query = self._extract_query(context, args)
        guard_result = self.sql_guard.validate(sql=sql, query=query)

        if not guard_result.passed:
            message = self._format_block_message(guard_result)
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
                },
            )

        result = await self.inner_tool.execute(context, args)
        result.metadata = dict(result.metadata or {})
        result.metadata["sql_guard"] = guard_result.to_dict()
        result.metadata["blocked_by_sql_guard"] = False

        if guard_result.severity == "warning":
            warning = self._format_warning_message(guard_result)
            result.metadata["sql_guard_warning"] = warning
            result.result_for_llm = f"{warning}\n\n{result.result_for_llm}"

        return result

    def _extract_query(self, context: ToolContext, args: RunSqlToolArgs) -> str:
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
                return value.strip()

        args_dict: dict[str, Any] = {}
        if hasattr(args, "model_dump"):
            args_dict = args.model_dump()
        elif hasattr(args, "dict"):
            args_dict = args.dict()

        for key in ("query", "question", "user_question"):
            value = args_dict.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return ""

    def _format_block_message(self, guard_result: SQLGuardResult) -> str:
        return "\n".join(
            [
                "SQL Guard blocked execution",
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

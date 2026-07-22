"""通过 Vanna 官方扩展点向工具上下文传递服务端原始问题。"""

from contextvars import ContextVar
from typing import Any

from vanna.core.enricher import ToolContextEnricher
from vanna.core.lifecycle import LifecycleHook
from vanna.core.tool import ToolContext
from vanna.core.user import User

from backend.request_diagnostics import (
    clear_request_diagnostics,
    get_request_diagnostics,
    initialize_request_diagnostics,
    utc_timestamp,
    write_trace_json,
)
from backend.run_sql_requirement import (
    clear_run_sql_requirement,
    initialize_run_sql_requirement,
)


_original_question: ContextVar[str | None] = ContextVar(
    "original_question", default=None
)


def get_original_question() -> str | None:
    return _original_question.get()


def finalize_request_context(
    *,
    status: str,
    result: Any = None,
    exception: BaseException | None = None,
) -> None:
    """写入请求结束证据并幂等清理所有请求级 ContextVar。"""
    try:
        diagnostics = get_request_diagnostics()
        if diagnostics is not None:
            payload = {
                "trace_id": diagnostics.trace_id,
                "timestamp": utc_timestamp(),
                "status": status,
                "result_type": type(result).__name__ if result is not None else None,
                "exception_type": type(exception).__name__ if exception else None,
                "error_message": str(exception) if exception else "",
                "context_cleanup_completed": True,
            }
            write_trace_json("request-end.json", payload)
    finally:
        clear_run_sql_requirement()
        _original_question.set(None)
        clear_request_diagnostics()


class OriginalQuestionLifecycleHook(LifecycleHook):
    """在其他 Hook 修改消息前保存服务端收到的原始问题。"""

    async def before_message(self, user: User, message: str) -> None:
        _original_question.set(message)
        initialize_run_sql_requirement()
        diagnostics = initialize_request_diagnostics(message)
        write_trace_json(
            "request-start.json",
            {
                "trace_id": diagnostics.trace_id,
                "original_question": message,
                "timestamp": utc_timestamp(),
            },
        )
        return None

    async def after_message(self, result: Any) -> None:
        finalize_request_context(status="success", result=result)


class OriginalQuestionContextEnricher(ToolContextEnricher):
    """把当前请求的原始问题写入 ToolContext。"""

    async def enrich_context(self, context: ToolContext) -> ToolContext:
        # 不保留调用方预先写入的同名字段，只信任服务端 LifecycleHook。
        context.metadata.pop("original_question", None)
        question = _original_question.get()
        if question is not None:
            context.metadata["original_question"] = question
        return context

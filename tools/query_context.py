"""通过 Vanna 官方扩展点向工具上下文传递服务端原始问题。"""

from contextvars import ContextVar
from typing import Any

from vanna.core.enricher import ToolContextEnricher
from vanna.core.lifecycle import LifecycleHook
from vanna.core.tool import ToolContext
from vanna.core.user import User


_original_question: ContextVar[str | None] = ContextVar(
    "original_question", default=None
)


class OriginalQuestionLifecycleHook(LifecycleHook):
    """在其他 Hook 修改消息前保存服务端收到的原始问题。"""

    async def before_message(self, user: User, message: str) -> None:
        _original_question.set(message)
        return None

    async def after_message(self, result: Any) -> None:
        _original_question.set(None)


class OriginalQuestionContextEnricher(ToolContextEnricher):
    """把当前请求的原始问题写入 ToolContext。"""

    async def enrich_context(self, context: ToolContext) -> ToolContext:
        # 不保留调用方预先写入的同名字段，只信任服务端 LifecycleHook。
        context.metadata.pop("original_question", None)
        question = _original_question.get()
        if question is not None:
            context.metadata["original_question"] = question
        return context

"""验证原始问题通过 LifecycleHook 和 ToolContextEnricher 安全传递。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Type

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import Tool, ToolContext, ToolResult
from vanna.core.user import User
from vanna.integrations.local.agent_memory import DemoAgentMemory

from tools.guarded_run_sql_tool import GuardedRunSqlTool
from tools.query_context import (
    OriginalQuestionContextEnricher,
    OriginalQuestionLifecycleHook,
)
from tools.sql_guard import SQLGuard


class FakeRunSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self) -> None:
        self.called = False

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return "测试用 SQL 工具"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(
        self, context: ToolContext, args: RunSqlToolArgs
    ) -> ToolResult:
        self.called = True
        return ToolResult(success=True, result_for_llm="ok", metadata={})


def make_context(metadata: dict | None = None) -> ToolContext:
    return ToolContext(
        user=User(id="query-context-test"),
        conversation_id="conversation-test",
        request_id="request-test",
        agent_memory=DemoAgentMemory(),
        metadata=dict(metadata or {}),
    )


async def run_tests() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    user = User(id="query-context-test")
    hook = OriginalQuestionLifecycleHook()
    enricher = OriginalQuestionContextEnricher()

    await hook.before_message(user, "查询排污口数量")
    context = await enricher.enrich_context(make_context())
    results.append(
        (
            "原始问题进入 ToolContext.metadata",
            context.metadata.get("original_question") == "查询排污口数量",
            str(context.metadata),
        )
    )

    inner = FakeRunSqlTool()
    guarded = GuardedRunSqlTool(inner_tool=inner, sql_guard=SQLGuard())
    tool_result = await guarded.execute(
        context,
        RunSqlToolArgs(sql="SELECT outlet_name FROM rs_outlet LIMIT 10"),
    )
    results.append(
        (
            "GuardedRunSqlTool 使用 metadata.original_question",
            inner.called
            and tool_result.metadata.get("query_source")
            == "metadata.original_question",
            str(tool_result.metadata.get("query_source")),
        )
    )
    await hook.after_message(None)

    async def concurrent_request(question: str) -> str | None:
        await hook.before_message(user, question)
        await asyncio.sleep(0)
        request_context = await enricher.enrich_context(make_context())
        await asyncio.sleep(0)
        captured = request_context.metadata.get("original_question")
        await hook.after_message(None)
        return captured

    questions = ["并发问题甲", "并发问题乙"]
    captured_questions = await asyncio.gather(
        *(concurrent_request(question) for question in questions)
    )
    results.append(
        (
            "两个并发请求不会串问题",
            captured_questions == questions,
            str(captured_questions),
        )
    )

    await hook.before_message(user, "需要清理的问题")
    await hook.after_message(None)
    cleaned_context = await enricher.enrich_context(make_context())
    results.append(
        (
            "after_message 清理上下文",
            "original_question" not in cleaned_context.metadata,
            str(cleaned_context.metadata),
        )
    )

    forged_context = await enricher.enrich_context(
        make_context({"original_question": "客户端伪造问题"})
    )
    results.append(
        (
            "客户端伪造 metadata 不作为传递依据",
            "original_question" not in forged_context.metadata,
            str(forged_context.metadata),
        )
    )
    return results


def main() -> int:
    results = asyncio.run(run_tests())
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

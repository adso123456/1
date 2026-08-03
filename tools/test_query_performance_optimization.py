"""问数性能六板块的离线、可重复合同测试。"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_context_enhancer import DeterministicMetadataContextEnhancer
from backend.query_intent import (
    ContextProfile,
    requires_fresh_data_followup,
    select_context_profile,
)
from backend.query_context import OriginalQuestionLifecycleHook
from backend.query_performance import (
    begin_query_performance,
    clear_query_performance,
    performance_payload,
    record_sql_result,
)
from backend.runtime_prewarm import RuntimePrewarmer
from backend.run_sql_requirement import (
    DEFAULT_DATABASE_QUERY_REASON,
    FORCED_RUN_SQL_TOOL_CHOICE,
    NON_DATA_EXEMPTION_REASON,
    build_effective_request_policy,
    get_run_sql_requirement,
    record_injected_sql_examples,
)
from backend.simple_query_fast_path import build_fast_path_summary
from backend.streaming_text import ChartAnnotationTailFilter
from backend.tracing_llm_service import TracingOpenAILlmService
from config.performance_settings import QueryPerformanceSettings
from tools.run_query_performance_validation import validate_case_contract
from vanna.core.llm import LlmMessage, LlmRequest, LlmStreamChunk
from vanna.core.user import User


class FakeAsyncTracingService(TracingOpenAILlmService):
    def __init__(self, *, delay: float = 0.04, limit: int = 2) -> None:
        self.model = "deepseek-v4-pro"
        self.settings = QueryPerformanceSettings(
            llm_max_concurrency=limit,
            llm_max_retries=0,
        )
        self._client = SimpleNamespace(base_url="https://api.deepseek.com")
        self.delay = delay
        self.parent_calls = 0

    async def _stream_parent_request(self, request):
        self.parent_calls += 1
        await asyncio.sleep(self.delay)
        yield LlmStreamChunk(content="根据结果，")
        await asyncio.sleep(self.delay)
        yield LlmStreamChunk(content="共有 2 条。")
        yield LlmStreamChunk(finish_reason="stop")


class FakeRetryTracingService(FakeAsyncTracingService):
    async def _stream_parent_request(self, request):
        self.parent_calls += 1
        if self.parent_calls == 1:
            raise ConnectionError("temporary provider connection failure")
        yield LlmStreamChunk(content="重试成功")
        yield LlmStreamChunk(finish_reason="stop")


def request() -> LlmRequest:
    return LlmRequest(
        messages=[LlmMessage(role="user", content="测试")],
        user=User(id="u", username="u"),
        stream=True,
    )


def candidate(index: int) -> dict[str, object]:
    return {
        "table_name": f"table_{index}",
        "table_comment": f"表{index}",
        "score": 100 - index,
        "matched_by": ["name"],
        "conflict_family": "",
        "risk_level": "low",
        "reason": "test",
        "matched_columns": [
            {
                "column_name": f"column_{column}",
                "column_type": "text",
                "column_comment": f"字段{column}",
                "matched_by": ["comment"],
            }
            for column in range(5)
        ],
        "columns": [
            {
                "column_name": f"column_{column}",
                "column_comment": f"字段{column}",
            }
            for column in range(10)
        ],
    }


class FakeRetriever:
    def retrieve(self, _question: str, top_n: int = 10):
        return [candidate(index) for index in range(top_n)]


class FakeCatalog:
    def __init__(self) -> None:
        self.records = {
            "disabled": SimpleNamespace(status="ready", enabled_for_chat=False),
            "failed": SimpleNamespace(status="ready", enabled_for_chat=True),
            "mysql": SimpleNamespace(status="ready", enabled_for_chat=True),
            "postgresql": SimpleNamespace(status="ready", enabled_for_chat=True),
            "training": SimpleNamespace(status="training_required", enabled_for_chat=False),
        }

    def require(self, source_id: str):
        return self.records[source_id]


class FakeManager:
    source_ids = ("training", "postgresql", "mysql", "failed", "disabled")

    def __init__(self) -> None:
        self.registry = SimpleNamespace(
            source_ids=self.source_ids,
            catalog=FakeCatalog(),
        )
        self.calls: list[str] = []

    def require(self, source_id: str):
        self.calls.append(source_id)
        if source_id == "failed":
            raise RuntimeError("offline")
        return object()


async def test_streaming_and_concurrency() -> tuple[bool, str]:
    state = begin_query_performance(
        conversation_id="c-stream",
        request_id="r-stream",
        question="统计监测记录数量",
        source_id="postgresql",
    )
    service = FakeAsyncTracingService(limit=2)
    chunks = []
    first_delta_before_end = False
    async for chunk in service.stream_request(request()):
        chunks.append(chunk.content or "")
        if not state.event_queue.empty() and len(chunks) < 3:
            first_delta_before_end = True
    deltas = []
    while not state.event_queue.empty():
        kind, value = state.event_queue.get_nowait()
        if kind == "event" and value["type"] == "text_delta":
            deltas.append(value["data"]["delta"])
    clear_query_performance()

    async def consume(name: str) -> str:
        begin_query_performance(
            conversation_id=name,
            request_id=name,
            question="统计监测记录数量",
            source_id=name,
        )
        value = ""
        try:
            async for chunk in service.stream_request(request()):
                value += chunk.content or ""
        finally:
            clear_query_performance()
        return value

    started = time.monotonic()
    values = await asyncio.gather(consume("pg"), consume("mysql"))
    concurrent_ms = (time.monotonic() - started) * 1000
    passed = (
        first_delta_before_end
        and "".join(deltas) == "根据结果，共有 2 条。"
        and values == ["根据结果，共有 2 条。"] * 2
        and concurrent_ms < 150
    )
    return passed, f"deltas={deltas}, concurrent_ms={concurrent_ms:.2f}"


async def test_retry_timeout_and_limit() -> tuple[bool, str]:
    serial_service = FakeAsyncTracingService(limit=1)

    async def consume(service: FakeAsyncTracingService, name: str) -> None:
        begin_query_performance(
            conversation_id=name,
            request_id=name,
            question="统计监测记录数量",
            source_id=name,
        )
        try:
            async for _ in service.stream_request(request()):
                pass
        finally:
            clear_query_performance()

    started = time.monotonic()
    await asyncio.gather(
        consume(serial_service, "serial-a"),
        consume(serial_service, "serial-b"),
    )
    serial_ms = (time.monotonic() - started) * 1000

    timeout_service = FakeAsyncTracingService(delay=0.08, limit=2)
    timeout_service.settings = QueryPerformanceSettings(
        llm_request_timeout_seconds=0.02,
        llm_max_retries=0,
        llm_max_concurrency=2,
    )
    begin_query_performance(
        conversation_id="timeout",
        request_id="timeout",
        question="统计监测记录数量",
        source_id="postgresql",
    )
    timed_out = False
    try:
        async for _ in timeout_service.stream_request(request()):
            pass
    except TimeoutError:
        timed_out = True
    finally:
        clear_query_performance()

    return (
        serial_ms >= 150 and timed_out,
        f"serial_ms={serial_ms:.2f}, timed_out={timed_out}",
    )


async def test_retry_cancellation_and_event_loop() -> tuple[bool, str]:
    retry_service = FakeRetryTracingService(limit=1)
    retry_service.settings = QueryPerformanceSettings(
        llm_max_retries=1,
        llm_max_concurrency=1,
    )
    retry_state = begin_query_performance(
        conversation_id="retry",
        request_id="retry",
        question="统计监测记录数量",
        source_id="postgresql",
    )
    retry_text = ""
    async for chunk in retry_service.stream_request(request()):
        retry_text += chunk.content or ""
    retry_count = retry_state.provider_retry_count
    clear_query_performance()

    cancellation_service = FakeAsyncTracingService(delay=0.15, limit=1)

    async def consume(name: str) -> str:
        begin_query_performance(
            conversation_id=name,
            request_id=name,
            question="统计监测记录数量",
            source_id=name,
        )
        value = ""
        try:
            async for chunk in cancellation_service.stream_request(request()):
                value += chunk.content or ""
        finally:
            clear_query_performance()
        return value

    cancelled_task = asyncio.create_task(consume("cancelled"))
    ticker_started = time.monotonic()
    await asyncio.sleep(0.02)
    ticker_ms = (time.monotonic() - ticker_started) * 1000
    cancelled_task.cancel()
    cancelled = False
    try:
        await cancelled_task
    except asyncio.CancelledError:
        cancelled = True
    followup = await asyncio.wait_for(consume("after-cancel"), timeout=0.5)
    passed = (
        retry_text == "重试成功"
        and retry_service.parent_calls == 2
        and retry_count == 1
        and cancelled
        and bool(followup)
        and ticker_ms < 80
    )
    return (
        passed,
        "retry_calls="
        f"{retry_service.parent_calls}, retry_count={retry_count}, "
        f"cancelled={cancelled}, ticker_ms={ticker_ms:.2f}",
    )


async def test_default_closed_run_sql_gate() -> tuple[bool, str]:
    hook = OriginalQuestionLifecycleHook()
    user = User(id="followup-user", username="followup-user")
    data_requests = (
        "把刚才结果按数量排序",
        "只看其中状态正常的",
        "那再来5条",
        "把这些记录按时间倒序",
        "给我上面几个的详细信息",
        "换个条件，只看夷陵区",
        "不要前三个，显示后五个",
        "接着查",
    )
    mixed_data_requests = (
        "解释一下刚才结果，然后只显示前3条",
        "这个字段是什么意思，顺便查询最近5条记录",
        "刚才为什么这样排序，再查夷陵区",
        "说明一下结果，再按时间倒序显示",
    )
    exempt_requests = (
        "解释一下刚才结果",
        "刚才为什么这样排序",
        "这个字段是什么意思",
        "SQL语法是什么意思",
        "你是指哪个字段吗？",
        "谢谢",
        "你好",
    )

    forced: list[str] = []
    for question in (*data_requests, *mixed_data_requests):
        await hook.before_message(user, question)
        state = get_run_sql_requirement()
        policy = build_effective_request_policy(
            llm_call_index=1,
            tools=[{"name": "run_sql"}],
            original_payload={"tool_choice": "auto"},
            provider_hostname="api.deepseek.com",
            model="deepseek-v4-pro",
        )
        if (
            state
            and state.requires_run_sql
            and DEFAULT_DATABASE_QUERY_REASON in state.requirement_reasons
            and policy.effective_tool_choice == FORCED_RUN_SQL_TOOL_CHOICE
        ):
            forced.append(question)
        await hook.after_message(None)

    exempted: list[str] = []
    for question in exempt_requests:
        await hook.before_message(user, question)
        record_injected_sql_examples(2)
        state = get_run_sql_requirement()
        policy = build_effective_request_policy(
            llm_call_index=1,
            tools=[{"name": "run_sql"}],
            original_payload={"tool_choice": "auto"},
            provider_hostname="api.deepseek.com",
            model="deepseek-v4-pro",
        )
        if (
            state
            and not state.requires_run_sql
            and state.explicit_non_data_exemption
            and NON_DATA_EXEMPTION_REASON in state.requirement_reasons
            and policy.effective_tool_choice == "auto"
        ):
            exempted.append(question)
        await hook.after_message(None)
    return (
        len(forced) == len(data_requests) + len(mixed_data_requests)
        and all(question in forced for question in mixed_data_requests)
        and len(exempted) == len(exempt_requests),
        f"forced={len(forced)}/{len(data_requests) + len(mixed_data_requests)}, "
        f"mixed_forced={sum(question in forced for question in mixed_data_requests)}"
        f"/{len(mixed_data_requests)}, "
        f"exempted={len(exempted)}/{len(exempt_requests)}",
    )


async def main() -> int:
    results: list[tuple[str, bool, str]] = []

    native_service = TracingOpenAILlmService(
        model="deepseek-v4-pro",
        api_key="test-only-placeholder",
        base_url="https://api.deepseek.com",
        settings=QueryPerformanceSettings(llm_max_retries=0),
    )
    client_type = type(native_service._client).__name__
    await native_service._client.close()
    results.append(
        (
            "项目侧使用原生 AsyncOpenAI 且 SDK 自动重试关闭",
            client_type == "AsyncOpenAI"
            and native_service._client.max_retries == 0,
            f"client_type={client_type}, max_retries={native_service._client.max_retries}",
        )
    )
    followup_passed, followup_detail = await test_default_closed_run_sql_gate()
    results.append(
        (
            "普通问数默认强制 run_sql，明确非数据请求才豁免",
            followup_passed,
            followup_detail,
        )
    )
    missing_followup_error = validate_case_contract(
        requires_fresh_sql=True,
        expected_request_id="missed-followup",
        expected_source_id="postgresql-main",
        performance={
            "successful_run_sql_count": 0,
            "dataframe_count": 0,
            "successful_sql_present": False,
        },
        observed_dataframe_count=0,
    )
    valid_followup_error = validate_case_contract(
        requires_fresh_sql=True,
        expected_request_id="valid-followup",
        expected_source_id="postgresql-main",
        performance={
            "successful_run_sql_count": 1,
            "dataframe_count": 1,
            "successful_sql_present": True,
            "request_id": "valid-followup",
            "source_id": "postgresql-main",
        },
        observed_dataframe_count=1,
    )
    exempt_validation_error = validate_case_contract(
        requires_fresh_sql=False,
        expected_request_id="exempt",
        expected_source_id="postgresql-main",
        performance={},
        observed_dataframe_count=0,
    )
    results.append(
        (
            "显式验收合同不依赖生产分类并对缺少新 SQL/DataFrame 失败关闭",
            not requires_fresh_data_followup("给我上面几个的详细信息")
            and bool(missing_followup_error)
            and not valid_followup_error
            and not exempt_validation_error,
            repr(
                (
                    missing_followup_error,
                    valid_followup_error,
                    exempt_validation_error,
                )
            ),
        )
    )

    count_state = begin_query_performance(
        conversation_id="count-contract",
        request_id="count-contract",
        question="查询数据",
        source_id="postgresql-main",
    )
    record_sql_result(
        sql="SELECT 1",
        metadata={"query_type": "SELECT"},
        guard_severity="ok",
        success=True,
    )
    first_count_payload = performance_payload(count_state)
    record_sql_result(
        sql="SELECT 2",
        metadata={"query_type": "SELECT", "results": []},
        guard_severity="ok",
        success=True,
    )
    second_count_payload = performance_payload(count_state)
    clear_query_performance()
    results.append(
        (
            "成功 SQL 与 DataFrame 使用独立计数",
            first_count_payload["successful_run_sql_count"] == 1
            and first_count_payload["dataframe_count"] == 0
            and second_count_payload["successful_run_sql_count"] == 2
            and second_count_payload["dataframe_count"] == 1,
            repr((first_count_payload, second_count_payload)),
        )
    )

    manager = FakeManager()
    statuses = await RuntimePrewarmer(manager).warm_ready_sources()
    results.append(
        (
            "预热仅顺序处理 ready+enabled，单源失败不阻断",
            manager.calls == ["failed", "mysql", "postgresql"]
            and statuses["failed"]["status"] == "failed"
            and statuses["mysql"]["status"] == "ready"
            and statuses["postgresql"]["status"] == "ready"
            and statuses["disabled"]["status"] == "not_started",
            repr((manager.calls, statuses)),
        )
    )

    state = begin_query_performance(
        conversation_id="c-fast",
        request_id="r-fast",
        question="列出前5个排污口名称",
        source_id="postgresql",
    )
    state.run_sql_count = 1
    state.dataframe_count = 1
    state.last_sql = "SELECT outlet_name FROM rs_outlet LIMIT 5"
    state.last_result_metadata = {
        "query_type": "SELECT",
        "row_count": 5,
        "columns": ["outlet_name"],
        "results": [{"outlet_name": f"排污口{i}"} for i in range(5)],
    }
    summary, reason = build_fast_path_summary(state)
    service = FakeAsyncTracingService()
    fast_chunks = [
        chunk async for chunk in service.stream_request(request())
    ]
    results.append(
        (
            "简单明细只生成确定性摘要且不调用第二轮 Provider",
            summary == "查询完成，共返回 5 条记录。"
            and reason == "eligible"
            and service.parent_calls == 0
            and "".join(chunk.content or "" for chunk in fast_chunks) == summary,
            repr((summary, reason, service.parent_calls)),
        )
    )
    clear_query_performance()

    results.append(
        (
            "准确率敏感的简单字段查询回退 FULL，但仍可使用确定性结果摘要",
            select_context_profile("列出前5个排污口名称和排污口类型")
            is ContextProfile.FULL
            and select_context_profile("查询最近5条排污口监测记录")
            is ContextProfile.FULL,
            "排污口类型/监测记录使用 FULL",
        )
    )

    tail = ChartAnnotationTailFilter()
    visible = "".join(
        tail.feed(part)
        for part in (
            "正文内容<",
            "!-- chart_spec: {\"type\":\"bar\"}",
            " -->后续正文",
        )
    ) + tail.finish()
    results.append(
        (
            "chart_spec 分片注释不泄露且正文无缺字",
            visible == "正文内容后续正文",
            visible,
        )
    )

    full_state = begin_query_performance(
        conversation_id="full",
        request_id="full",
        question="统计各区排污口数量",
        source_id="postgresql",
    )
    enhancer = DeterministicMetadataContextEnhancer(
        metadata_retriever=FakeRetriever()
    )
    full_prompt = await enhancer.enhance_system_prompt(
        "SYSTEM", full_state.question, SimpleNamespace()
    )
    clear_query_performance()
    simple_state = begin_query_performance(
        conversation_id="simple",
        request_id="simple",
        question="列出前5个排污口名称",
        source_id="postgresql",
    )
    simple_prompt = await enhancer.enhance_system_prompt(
        "SYSTEM", simple_state.question, SimpleNamespace()
    )
    clear_query_performance()
    reduction = 1 - len(simple_prompt) / len(full_prompt)
    results.append(
        (
            "SIMPLE_LOOKUP 元数据上下文至少缩减 20%",
            reduction >= 0.20
            and "top 2 relevant tables" in simple_prompt
            and "top 3 relevant tables" in full_prompt,
            f"full={len(full_prompt)}, simple={len(simple_prompt)}, reduction={reduction:.2%}",
        )
    )

    stream_passed, stream_detail = await test_streaming_and_concurrency()
    results.append(
        ("最终文本真流式且两个慢请求并行", stream_passed, stream_detail)
    )
    limit_passed, limit_detail = await test_retry_timeout_and_limit()
    results.append(
        ("模型总超时和并发上限生效", limit_passed, limit_detail)
    )
    cancellation_passed, cancellation_detail = (
        await test_retry_cancellation_and_event_loop()
    )
    results.append(
        (
            "可重试连接错误只重试一次，取消释放并发槽且事件循环不阻塞",
            cancellation_passed,
            cancellation_detail,
        )
    )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results)-failed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

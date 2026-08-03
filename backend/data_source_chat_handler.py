"""按请求数据源选择 Runtime Agent 的聊天处理器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Callable

from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.query_context import finalize_request_context
from backend.query_performance import (
    begin_query_performance,
    clear_query_performance,
    emit_progress,
    record_timing,
    write_performance_evidence,
)
from backend.request_diagnostics import (
    ensure_request_diagnostics,
    get_request_diagnostics,
)
from config.performance_settings import QueryPerformanceSettings
from vanna.servers.base import (
    ChatHandler,
    ChatRequest,
    ChatResponse,
    ChatStreamChunk,
)


class DataSourceChatHandler:
    """在进入现有聊天链路前完成会话绑定和 Runtime 路由。"""

    def __init__(
        self,
        coordinator: DataSourceRequestCoordinator,
        runtime_manager: DataSourceRuntimeManager,
        prewarm_status_provider: Callable[
            [], dict[str, dict[str, object]]
        ]
        | None = None,
    ) -> None:
        if not isinstance(coordinator, DataSourceRequestCoordinator):
            raise TypeError("coordinator 必须是 DataSourceRequestCoordinator")
        if not isinstance(runtime_manager, DataSourceRuntimeManager):
            raise TypeError("runtime_manager 必须是 DataSourceRuntimeManager")
        self._coordinator = coordinator
        self._runtime_manager = runtime_manager
        self._prewarm_status_provider = prewarm_status_provider

    @staticmethod
    def _event_chunk(
        rich: dict[str, Any], conversation_id: str, request_id: str
    ) -> ChatStreamChunk:
        return ChatStreamChunk(
            rich=rich,
            simple=None,
            conversation_id=conversation_id,
            request_id=request_id,
        )

    @staticmethod
    def _error_rich(message: str) -> dict[str, Any]:
        return {
            "type": "error",
            "id": f"error-{uuid.uuid4().hex[:10]}",
            "lifecycle": "complete",
            "timestamp": time.time(),
            "visible": True,
            "interactive": False,
            "data": {"message": message},
        }

    @staticmethod
    def _execution_error_message(request: ChatRequest) -> str:
        if request.metadata.get("_embed_request") is True:
            return "嵌入问数执行失败，请稍后重试。"
        return "问数执行失败，请稍后重试。"

    async def handle_stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        request_started = time.monotonic()
        conversation_id = request.conversation_id
        if conversation_id is None:
            raise ValueError("conversation_id 必须显式提供")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id 必须是非空字符串")

        context = self._coordinator.resolve(
            conversation_id,
            request.metadata,
        )
        request_id = request.request_id or uuid.uuid4().hex
        request.request_id = request_id
        ensure_request_diagnostics(request.message)
        state = begin_query_performance(
            conversation_id=conversation_id,
            request_id=request_id,
            question=request.message,
            source_id=context.source_id,
            started_at=request_started,
        )
        settings = QueryPerformanceSettings.from_environment()

        emit_progress("request_received", "已接收问题")
        _, first_event = state.event_queue.get_nowait()
        state.first_sse_event_ms = state.elapsed_ms()
        yield self._event_chunk(first_event, conversation_id, request_id)

        emit_progress("preparing_runtime", "正在准备数据源")
        _, preparing_event = state.event_queue.get_nowait()
        yield self._event_chunk(preparing_event, conversation_id, request_id)

        runtime_started = time.monotonic()
        state.runtime_was_cached = (
            self._runtime_manager.runtime_revision(context.source_id) is not None
        )
        producer: asyncio.Task[None] | None = None
        request_failed = False
        try:
            if self._prewarm_status_provider is not None:
                prewarm = self._prewarm_status_provider().get(
                    context.source_id, {}
                )
                if prewarm.get("status") == "failed":
                    request_failed = True
                    yield self._event_chunk(
                        self._error_rich(
                            "数据源预热失败，当前暂不可用，请检查数据源后重启服务。"
                        ),
                        conversation_id,
                        request_id,
                    )
                    return
            await asyncio.to_thread(
                self._runtime_manager.require, context.source_id
            )
            record_timing(
                "runtime_acquire_ms",
                (time.monotonic() - runtime_started) * 1000,
            )
            emit_progress("retrieving_context", "正在检索相关表和字段")

            async def produce() -> None:
                try:
                    with self._runtime_manager.acquire(
                        context.source_id
                    ) as runtime:
                        handler = ChatHandler(runtime.agent)
                        async for chunk in handler.handle_stream(request):
                            await state.event_queue.put(("chunk", chunk))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await state.event_queue.put(("error", error))
                finally:
                    await state.event_queue.put(("done", None))

            producer = asyncio.create_task(produce())
            deadline = (
                time.monotonic() + settings.chat_request_deadline_seconds
            )
            completed = False
            while not completed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    kind, value = await asyncio.wait_for(
                        state.event_queue.get(), timeout=remaining
                    )
                except asyncio.TimeoutError as exc:
                    raise TimeoutError from exc
                if kind == "event":
                    yield self._event_chunk(value, conversation_id, request_id)
                elif kind == "chunk":
                    yield value
                elif kind == "error":
                    request_failed = True
                    yield self._event_chunk(
                        self._error_rich(
                            self._execution_error_message(request)
                        ),
                        conversation_id,
                        request_id,
                    )
                elif kind == "done":
                    completed = True
        except TimeoutError:
            request_failed = True
            state.timeout_stage = state.current_stage
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            yield self._event_chunk(
                self._error_rich("问数处理超时，请缩小查询范围后重试。"),
                conversation_id,
                request_id,
            )
        except asyncio.CancelledError:
            state.request_cancelled = True
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            raise
        except Exception:
            request_failed = True
            yield self._event_chunk(
                self._error_rich(self._execution_error_message(request)),
                conversation_id,
                request_id,
            )
        finally:
            if not state.request_cancelled:
                emit_progress("completed", "处理完成")
                try:
                    while True:
                        kind, value = state.event_queue.get_nowait()
                        if kind == "event" and value.get("type") == "progress":
                            yield self._event_chunk(
                                value, conversation_id, request_id
                            )
                except asyncio.QueueEmpty:
                    pass
            state.timings["total_ms"] = round(state.elapsed_ms(), 3)
            write_performance_evidence(state)
            if get_request_diagnostics() is not None:
                finalize_request_context(
                    status=(
                        "error"
                        if request_failed or state.request_failed
                        else "success"
                    )
                )
            clear_query_performance()

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = []
        async for chunk in self.handle_stream(request):
            chunks.append(chunk)
        return ChatResponse.from_chunks(chunks)

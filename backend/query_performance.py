"""请求级性能状态、进度事件和分段耗时。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.query_intent import ContextProfile, select_context_profile
from backend.request_diagnostics import get_request_diagnostics, redact_sensitive


@dataclass
class QueryPerformanceState:
    conversation_id: str
    request_id: str
    question: str
    source_id: str
    context_profile: ContextProfile
    started_at: float = field(default_factory=time.monotonic)
    event_queue: asyncio.Queue[tuple[str, Any]] = field(
        default_factory=asyncio.Queue
    )
    timings: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    first_sse_event_ms: float | None = None
    first_text_chunk_ms: float | None = None
    runtime_was_cached: bool = False
    fast_path_used: bool = False
    request_cancelled: bool = False
    request_failed: bool = False
    timeout_stage: str = ""
    current_stage: str = "request_received"
    trace_directory: Path | None = None
    run_sql_count: int = 0
    dataframe_count: int = 0
    provider_retry_count: int = 0
    tool_error_count: int = 0
    guard_warning_count: int = 0
    last_sql: str = ""
    last_result_metadata: dict[str, Any] = field(default_factory=dict)

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000


_current: ContextVar[QueryPerformanceState | None] = ContextVar(
    "query_performance_state", default=None
)


def begin_query_performance(
    *,
    conversation_id: str,
    request_id: str,
    question: str,
    source_id: str,
    started_at: float | None = None,
) -> QueryPerformanceState:
    diagnostics = get_request_diagnostics()
    state = QueryPerformanceState(
        conversation_id=conversation_id,
        request_id=request_id,
        question=question,
        source_id=source_id,
        context_profile=select_context_profile(question),
        started_at=started_at if started_at is not None else time.monotonic(),
        trace_directory=(diagnostics.trace_directory if diagnostics else None),
    )
    _current.set(state)
    return state


def get_query_performance() -> QueryPerformanceState | None:
    return _current.get()


def clear_query_performance() -> None:
    _current.set(None)


def record_timing(name: str, elapsed_ms: float) -> None:
    state = get_query_performance()
    if state is not None:
        state.timings[name] = round(max(0.0, elapsed_ms), 3)


def increment_counter(name: str, amount: int = 1) -> None:
    state = get_query_performance()
    if state is not None:
        state.counters[name] = state.counters.get(name, 0) + amount


def _rich_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event_type,
        "id": f"{event_type}-{uuid.uuid4().hex[:10]}",
        "lifecycle": "update",
        "timestamp": time.time(),
        "visible": True,
        "interactive": False,
        "data": data,
    }


def emit_progress(stage: str, message: str) -> None:
    state = get_query_performance()
    if state is None:
        return
    state.current_stage = stage
    state.event_queue.put_nowait(
        (
            "event",
            _rich_event(
                "progress",
                {
                    "stage": stage,
                    "message": message,
                    "elapsed_ms": round(state.elapsed_ms()),
                },
            ),
        )
    )


def emit_text_delta(delta: str) -> None:
    state = get_query_performance()
    if state is None or not delta:
        return
    if state.first_text_chunk_ms is None:
        state.first_text_chunk_ms = state.elapsed_ms()
    state.event_queue.put_nowait(
        (
            "event",
            _rich_event(
                "text_delta",
                {
                    "message_id": f"answer-{state.request_id}",
                    "delta": delta,
                },
            ),
        )
    )


def record_sql_result(
    *, sql: str, metadata: dict[str, Any], guard_severity: str, success: bool
) -> None:
    state = get_query_performance()
    if state is None:
        return
    state.run_sql_count += 1
    if success and metadata.get("query_type") == "SELECT":
        state.dataframe_count += 1
    else:
        state.tool_error_count += 1
    if guard_severity != "ok":
        state.guard_warning_count += 1
    state.last_sql = sql
    state.last_result_metadata = dict(metadata)


def mark_provider_retry() -> None:
    state = get_query_performance()
    if state is not None:
        state.provider_retry_count += 1
        emit_progress("retrying", "正在重试模型请求")


def performance_payload(state: QueryPerformanceState) -> dict[str, Any]:
    total_ms = state.elapsed_ms()
    return {
        **state.timings,
        "runtime_acquire_ms": state.timings.get("runtime_acquire_ms", 0.0),
        "runtime_was_cached": state.runtime_was_cached,
        "context_enhance_ms": state.timings.get("context_enhance_ms", 0.0),
        "text_memory_ms": state.timings.get("text_memory_ms", 0.0),
        "metadata_retrieve_ms": state.timings.get("metadata_retrieve_ms", 0.0),
        "sql_example_retrieve_ms": state.timings.get("sql_example_retrieve_ms", 0.0),
        "llm_call_1_ms": state.timings.get("llm_call_1_ms", 0.0),
        "sql_guard_ms": state.timings.get("sql_guard_ms", 0.0),
        "sql_execute_ms": state.timings.get("sql_execute_ms", 0.0),
        "llm_call_2_ms": state.timings.get("llm_call_2_ms", 0.0),
        "first_sse_event_ms": round(state.first_sse_event_ms or 0.0, 3),
        "first_text_chunk_ms": round(state.first_text_chunk_ms or 0.0, 3),
        "total_ms": round(total_ms, 3),
        "llm_call_count": state.counters.get("provider_llm_calls", 0),
        "tool_round_count": state.run_sql_count,
        "successful_run_sql_count": state.dataframe_count,
        "dataframe_count": state.dataframe_count,
        "fast_path_used": state.fast_path_used,
        "context_profile": state.context_profile.value,
        "request_cancelled": state.request_cancelled,
        "request_failed": state.request_failed,
        "timeout_stage": state.timeout_stage,
        "provider_retry_count": state.provider_retry_count,
        "input_context_chars": state.counters.get("input_context_chars", 0),
        "source_id": state.source_id,
        "request_id": state.request_id,
    }


def write_performance_evidence(state: QueryPerformanceState) -> None:
    if state.trace_directory is None:
        return
    try:
        state.trace_directory.mkdir(parents=True, exist_ok=True)
        target = state.trace_directory / "query-performance.json"
        target.write_text(
            json.dumps(
                redact_sensitive(performance_payload(state)),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

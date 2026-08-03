"""OpenAILlmService 的请求级诊断薄包装。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import weakref
from copy import deepcopy
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from vanna.core.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    LlmStreamChunk,
)
from vanna.core.llm.models import ToolCall
from vanna.integrations.openai import OpenAILlmService

from backend.request_diagnostics import (
    next_llm_call_number,
    redact_sensitive,
    utc_timestamp,
    write_trace_json,
)
from backend.query_performance import (
    emit_progress,
    emit_text_delta,
    get_query_performance,
    increment_counter,
    mark_provider_retry,
    record_timing,
)
from backend.run_sql_requirement import (
    EffectiveRequestPolicy,
    build_effective_request_policy,
    get_run_sql_requirement,
    mark_parent_llm_called,
)
from backend.simple_query_fast_path import build_fast_path_summary
from backend.streaming_text import ChartAnnotationTailFilter
from config.performance_settings import QueryPerformanceSettings


_PAYLOAD_POLICY_METADATA_KEY = "_vanna_effective_payload_policy"
_LOOP_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        redact_sensitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TracingOpenAILlmService(OpenAILlmService):
    """记录最终 OpenAI payload，并应用请求级 Provider 兼容策略。"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        settings: QueryPerformanceSettings | None = None,
    ) -> None:
        from httpx import Timeout
        from openai import AsyncOpenAI

        self.model = model
        self.settings = settings or QueryPerformanceSettings.from_environment()
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=Timeout(
                connect=self.settings.llm_connect_timeout_seconds,
                read=self.settings.llm_read_timeout_seconds,
                write=self.settings.llm_read_timeout_seconds,
                pool=self.settings.llm_connect_timeout_seconds,
            ),
        )

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        current = _LOOP_SEMAPHORES.get(loop)
        limit = self._performance_settings().llm_max_concurrency
        if current is None or current[0] != limit:
            current = (limit, asyncio.Semaphore(limit))
            _LOOP_SEMAPHORES[loop] = current
        return current[1]

    def _performance_settings(self) -> QueryPerformanceSettings:
        settings = getattr(self, "settings", None)
        if isinstance(settings, QueryPerformanceSettings):
            return settings
        return QueryPerformanceSettings.from_environment()

    @staticmethod
    def _is_retryable_provider_error(error: BaseException) -> bool:
        name = type(error).__name__.lower()
        if any(
            marker in name
            for marker in (
                "badrequest",
                "authentication",
                "permission",
                "notfound",
                "unprocessable",
            )
        ):
            return False
        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            return int(status_code) == 429 or int(status_code) >= 500
        return any(
            marker in name
            for marker in (
                "timeout",
                "connection",
                "ratelimit",
                "internalserver",
            )
        )

    def _base_url_hostname(self) -> str:
        try:
            raw = str(getattr(self._client, "base_url", "") or "")
            return urlparse(raw).hostname or ""
        except Exception:
            return ""

    def _build_original_payload(self, request: LlmRequest) -> dict[str, Any]:
        """隔离 Vanna 原始映射，便于离线验证 SDK 参数透传。"""
        return super()._build_payload(request)

    def _build_payload(self, request: LlmRequest) -> dict[str, Any]:
        payload = self._build_original_payload(request)
        policy = request.metadata.get(_PAYLOAD_POLICY_METADATA_KEY)
        if not isinstance(policy, dict):
            return payload
        effective = deepcopy(payload)
        for key in policy.get("remove", []):
            effective.pop(str(key), None)
        for key, value in (policy.get("set") or {}).items():
            effective[str(key)] = deepcopy(value)
        return effective

    def _prepare_effective_request(
        self, request: LlmRequest, call_number: int
    ) -> tuple[LlmRequest, Any, Any, EffectiveRequestPolicy]:
        original_payload = self._build_original_payload(request)
        policy = build_effective_request_policy(
            llm_call_index=call_number,
            tools=request.tools,
            original_payload=original_payload,
            provider_hostname=self._base_url_hostname(),
            model=self.model,
        )
        payload_set: dict[str, Any] = {}
        payload_remove: list[str] = []
        original_tool_choice = original_payload.get("tool_choice")
        if (
            not policy.remove_tool_choice
            and policy.effective_tool_choice != original_tool_choice
        ):
            payload_set["tool_choice"] = policy.effective_tool_choice
        if policy.provider_strategy in {
            "deepseek_non_thinking_first_tool_call",
            "deepseek_non_thinking_tool_continuation",
            "deepseek_non_thinking_answer_only_continuation",
        }:
            payload_set["extra_body"] = policy.effective_extra_body
        if policy.remove_reasoning_effort:
            payload_remove.append("reasoning_effort")
        if policy.remove_tools:
            payload_remove.append("tools")
        if policy.remove_tool_choice:
            payload_remove.append("tool_choice")

        metadata = dict(request.metadata)
        metadata.pop(_PAYLOAD_POLICY_METADATA_KEY, None)
        if payload_set or payload_remove:
            metadata[_PAYLOAD_POLICY_METADATA_KEY] = {
                "set": payload_set,
                "remove": payload_remove,
            }
        effective_request = request.model_copy(
            deep=True,
            update={"metadata": metadata},
        )
        return (
            effective_request,
            original_tool_choice,
            policy.effective_tool_choice,
            policy,
        )

    def _current_decision(self) -> dict[str, Any]:
        state = get_run_sql_requirement()
        if state is None or not state.decisions:
            return {}
        return state.decisions[-1]

    def _capture_request(
        self,
        request: LlmRequest,
        call_number: int,
        original_tool_choice: Any,
        effective_tool_choice: Any,
    ) -> None:
        try:
            payload = self._build_payload(request)
            messages = payload.get("messages") or []
            tools = payload.get("tools") or []
            decision = self._current_decision()
            captured = {
                "call_number": call_number,
                "timestamp": utc_timestamp(),
                "model": self.model,
                "base_url_hostname": self._base_url_hostname(),
                "stream": request.stream,
                "messages": messages,
                "tools": tools,
                "tool_choice": payload.get("tool_choice"),
                "original_tool_choice": original_tool_choice,
                "effective_tool_choice": effective_tool_choice,
                "provider_strategy": decision.get("provider_strategy", ""),
                "original_thinking": decision.get("original_thinking"),
                "effective_thinking": decision.get("effective_thinking"),
                "original_reasoning_effort": decision.get(
                    "original_reasoning_effort"
                ),
                "effective_reasoning_effort": decision.get(
                    "effective_reasoning_effort"
                ),
                "thinking_override_applied": decision.get(
                    "thinking_override_applied", False
                ),
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "request_metadata": request.metadata,
                "other_payload_parameters": {
                    key: value
                    for key, value in payload.items()
                    if key not in {"model", "messages", "tools", "tool_choice"}
                },
                "messages_sha256": _canonical_sha256(messages),
                "tools_sha256": _canonical_sha256(tools),
                "input_context_chars": sum(
                    len(str(message.get("content") or ""))
                    for message in messages
                ),
            }
            state = get_query_performance()
            if state is not None and call_number == 1:
                state.counters["input_context_chars"] = captured[
                    "input_context_chars"
                ]
            write_trace_json(f"llm-call-{call_number:03d}-request.json", captured)
            if call_number == 1:
                write_trace_json("llm-request.json", captured)
        except Exception as error:
            write_trace_json(
                f"llm-call-{call_number:03d}-request.json",
                {
                    "call_number": call_number,
                    "capture_error_type": type(error).__name__,
                },
            )

    def _capture_response(self, call_number: int, value: dict[str, Any]) -> None:
        captured = {
            "call_number": call_number,
            "timestamp": utc_timestamp(),
            **value,
        }
        write_trace_json(f"llm-call-{call_number:03d}-response.json", captured)
        if call_number == 1:
            write_trace_json("llm-response.json", captured)

    def _finalize_provider_exception(
        self,
        *,
        call_number: int,
        response_type: str,
        error: BaseException,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self._capture_response(
            call_number,
            {
                "response_type": response_type,
                "content": content,
                "tool_calls": tool_calls or [],
                "exception_type": type(error).__name__,
                "error_message": str(error),
            },
        )
        from backend.query_context import finalize_request_context

        finalize_request_context(status="error", exception=error)

    async def _send_parent_request(self, request: LlmRequest) -> LlmResponse:
        payload = self._build_payload(request)
        response = await self._client.chat.completions.create(
            **payload, stream=False
        )
        if not response.choices:
            return LlmResponse(content=None, tool_calls=None, finish_reason=None)
        choice = response.choices[0]
        usage = None
        if response.usage is not None:
            usage = {
                "prompt_tokens": int(response.usage.prompt_tokens or 0),
                "completion_tokens": int(response.usage.completion_tokens or 0),
                "total_tokens": int(response.usage.total_tokens or 0),
            }
        return LlmResponse(
            content=getattr(choice.message, "content", None),
            tool_calls=self._extract_tool_calls_from_message(choice.message) or None,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=usage,
        )

    async def _stream_parent_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        payload = self._build_payload(request)
        stream = await self._client.chat.completions.create(
            **payload, stream=True
        )
        builders: dict[int, dict[str, str | None]] = {}
        finish_reason = None
        async for event in stream:
            if not getattr(event, "choices", None):
                continue
            choice = event.choices[0]
            delta = getattr(choice, "delta", None)
            finish_reason = getattr(choice, "finish_reason", finish_reason)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                yield LlmStreamChunk(content=content)
            for tool_call in getattr(delta, "tool_calls", None) or []:
                index = getattr(tool_call, "index", 0) or 0
                builder = builders.setdefault(
                    index, {"id": None, "name": None, "arguments": ""}
                )
                if getattr(tool_call, "id", None):
                    builder["id"] = tool_call.id
                function = getattr(tool_call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        builder["name"] = function.name
                    if getattr(function, "arguments", None):
                        builder["arguments"] = (
                            (builder["arguments"] or "") + function.arguments
                        )
        calls = []
        for builder in builders.values():
            if not builder["name"]:
                continue
            raw = builder["arguments"] or "{}"
            try:
                loaded = json.loads(raw)
                arguments = loaded if isinstance(loaded, dict) else {"args": loaded}
            except Exception:
                arguments = {"_raw": raw}
            calls.append(
                ToolCall(
                    id=builder["id"] or "tool_call",
                    name=builder["name"] or "tool",
                    arguments=arguments,
                )
            )
        yield LlmStreamChunk(
            tool_calls=calls or None,
            finish_reason=finish_reason or "stop",
        )

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        call_number = next_llm_call_number()
        started = time.monotonic()
        settings = self._performance_settings()
        try:
            (
                effective_request,
                original_tool_choice,
                effective_tool_choice,
                _policy,
            ) = self._prepare_effective_request(request, call_number)
            self._capture_request(
                effective_request,
                call_number,
                original_tool_choice,
                effective_tool_choice,
            )
            response = None
            for attempt in range(settings.llm_max_retries + 1):
                try:
                    mark_parent_llm_called()
                    increment_counter("provider_llm_calls")
                    async with self._semaphore():
                        async with asyncio.timeout(
                            settings.llm_request_timeout_seconds
                        ):
                            response = await self._send_parent_request(
                                effective_request
                            )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if (
                        attempt >= settings.llm_max_retries
                        or not self._is_retryable_provider_error(error)
                    ):
                        raise
                    mark_provider_retry()
            assert response is not None
        except Exception as error:
            self._finalize_provider_exception(
                call_number=call_number,
                response_type="exception",
                error=error,
            )
            raise
        record_timing(
            f"llm_call_{call_number}_ms",
            (time.monotonic() - started) * 1000,
        )
        self._capture_response(
            call_number,
            {
                "response_type": type(response).__name__,
                "content": response.content,
                "tool_calls": [
                    call.model_dump(mode="json") for call in response.tool_calls or []
                ],
                "finish_reason": response.finish_reason,
                "usage": response.usage,
            },
        )
        return response

    async def stream_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        performance = get_query_performance()
        summary, _reason = build_fast_path_summary(performance)
        if summary is not None:
            if performance is not None and performance.first_text_chunk_ms is None:
                performance.first_text_chunk_ms = performance.elapsed_ms()
            yield LlmStreamChunk(content=summary)
            yield LlmStreamChunk(finish_reason="stop")
            return

        call_number = next_llm_call_number()
        started = time.monotonic()
        settings = self._performance_settings()
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish_reason = None
        try:
            (
                effective_request,
                original_tool_choice,
                effective_tool_choice,
                _policy,
            ) = self._prepare_effective_request(request, call_number)
            self._capture_request(
                effective_request,
                call_number,
                original_tool_choice,
                effective_tool_choice,
            )
            if call_number == 1:
                emit_progress("generating_sql", "正在生成查询语句")
            visible_answer = bool(_policy.remove_tools or not request.tools)
            tail_filter = ChartAnnotationTailFilter() if visible_answer else None
            for attempt in range(settings.llm_max_retries + 1):
                emitted_provider_chunk = False
                try:
                    mark_parent_llm_called()
                    increment_counter("provider_llm_calls")
                    async with self._semaphore():
                        async with asyncio.timeout(
                            settings.llm_request_timeout_seconds
                        ):
                            async for chunk in self._stream_parent_request(
                                effective_request
                            ):
                                emitted_provider_chunk = True
                                if chunk.content:
                                    content_parts.append(chunk.content)
                                    if tail_filter is not None:
                                        emit_text_delta(
                                            tail_filter.feed(chunk.content)
                                        )
                                if chunk.tool_calls:
                                    tool_calls.extend(
                                        call.model_dump(mode="json")
                                        for call in chunk.tool_calls
                                    )
                                if chunk.finish_reason:
                                    finish_reason = chunk.finish_reason
                                yield chunk
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if (
                        attempt >= settings.llm_max_retries
                        or emitted_provider_chunk
                        or not self._is_retryable_provider_error(error)
                    ):
                        raise
                    mark_provider_retry()
            if tail_filter is not None:
                emit_text_delta(tail_filter.finish())
        except Exception as error:
            self._finalize_provider_exception(
                call_number=call_number,
                response_type="stream_exception",
                error=error,
                content="".join(content_parts),
                tool_calls=tool_calls,
            )
            raise
        record_timing(
            f"llm_call_{call_number}_ms",
            (time.monotonic() - started) * 1000,
        )
        self._capture_response(
            call_number,
            {
                "response_type": "stream",
                "content": "".join(content_parts),
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
            },
        )

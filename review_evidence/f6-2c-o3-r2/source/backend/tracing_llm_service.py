"""OpenAILlmService 的请求级诊断薄包装。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, AsyncGenerator
from urllib.parse import urlparse

from vanna.core.llm import LlmMessage, LlmRequest, LlmResponse, LlmStreamChunk
from vanna.integrations.openai import OpenAILlmService

from backend.request_diagnostics import (
    next_llm_call_number,
    redact_sensitive,
    utc_timestamp,
    write_trace_json,
)
from backend.run_sql_requirement import (
    EffectiveRequestPolicy,
    build_effective_request_policy,
    get_run_sql_requirement,
    mark_parent_llm_called,
)


_PAYLOAD_POLICY_METADATA_KEY = "_vanna_effective_payload_policy"


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
        if policy.effective_tool_choice != original_tool_choice:
            payload_set["tool_choice"] = policy.effective_tool_choice
        if policy.provider_strategy in {
            "deepseek_non_thinking_first_tool_call",
            "deepseek_non_thinking_tool_continuation",
        }:
            payload_set["extra_body"] = policy.effective_extra_body
        if policy.remove_reasoning_effort:
            payload_remove.append("reasoning_effort")

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
            }
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
        return await super().send_request(request)

    async def _stream_parent_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        async for chunk in super().stream_request(request):
            yield chunk

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        call_number = next_llm_call_number()
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
            mark_parent_llm_called()
            response = await self._send_parent_request(effective_request)
        except Exception as error:
            self._finalize_provider_exception(
                call_number=call_number,
                response_type="exception",
                error=error,
            )
            raise
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
        call_number = next_llm_call_number()
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
            mark_parent_llm_called()
            async for chunk in self._stream_parent_request(effective_request):
                if chunk.content:
                    content_parts.append(chunk.content)
                if chunk.tool_calls:
                    tool_calls.extend(
                        call.model_dump(mode="json") for call in chunk.tool_calls
                    )
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                yield chunk
        except Exception as error:
            self._finalize_provider_exception(
                call_number=call_number,
                response_type="stream_exception",
                error=error,
                content="".join(content_parts),
                tool_calls=tool_calls,
            )
            raise
        self._capture_response(
            call_number,
            {
                "response_type": "stream",
                "content": "".join(content_parts),
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
            },
        )

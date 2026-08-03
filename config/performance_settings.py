"""问数性能、安全上限和并发配置。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _number(
    source: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float = 0,
) -> float:
    raw = source.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是数字") from exc
    if value <= minimum:
        raise ValueError(f"环境变量 {name} 必须大于 {minimum}")
    return value


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int = 0,
) -> int:
    raw = source.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value < minimum:
        raise ValueError(f"环境变量 {name} 不能小于 {minimum}")
    return value


@dataclass(frozen=True)
class QueryPerformanceSettings:
    llm_connect_timeout_seconds: float = 10.0
    llm_read_timeout_seconds: float = 90.0
    llm_request_timeout_seconds: float = 100.0
    llm_max_retries: int = 1
    chat_request_deadline_seconds: float = 120.0
    agent_max_tool_rounds: int = 3
    llm_max_concurrency: int = 4

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "QueryPerformanceSettings":
        source = os.environ if environ is None else environ
        return cls(
            llm_connect_timeout_seconds=_number(
                source, "LLM_CONNECT_TIMEOUT_SECONDS", 10.0
            ),
            llm_read_timeout_seconds=_number(
                source, "LLM_READ_TIMEOUT_SECONDS", 90.0
            ),
            llm_request_timeout_seconds=_number(
                source, "LLM_REQUEST_TIMEOUT_SECONDS", 100.0
            ),
            llm_max_retries=_integer(
                source, "LLM_MAX_RETRIES", 1, minimum=0
            ),
            chat_request_deadline_seconds=_number(
                source, "CHAT_REQUEST_DEADLINE_SECONDS", 120.0
            ),
            agent_max_tool_rounds=_integer(
                source, "AGENT_MAX_TOOL_ROUNDS", 3, minimum=1
            ),
            llm_max_concurrency=_integer(
                source, "LLM_MAX_CONCURRENCY", 4, minimum=1
            ),
        )

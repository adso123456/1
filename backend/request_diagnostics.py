"""请求级诊断上下文与安全证据写入。"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class RequestDiagnosticContext:
    trace_id: str
    original_question: str
    trace_directory: Path | None
    llm_call_count: int = 0


_current_diagnostics: ContextVar[RequestDiagnosticContext | None] = ContextVar(
    "request_diagnostics", default=None
)

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "db_password",
    "database_password",
    "password",
    "connection_string",
    "database_url",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_request_diagnostics(question: str) -> RequestDiagnosticContext:
    """初始化独立请求上下文；诊断关闭时不创建目录。"""
    trace_id = uuid.uuid4().hex
    trace_directory: Path | None = None
    if os.getenv("VANNA_REQUEST_TRACE_ENABLED", "") == "1":
        configured = os.getenv("VANNA_REQUEST_TRACE_DIR", "").strip()
        if configured:
            candidate = Path(configured)
            if candidate.is_absolute():
                trace_directory = candidate / trace_id
    context = RequestDiagnosticContext(
        trace_id=trace_id,
        original_question=question,
        trace_directory=trace_directory,
    )
    _current_diagnostics.set(context)
    return context


def get_request_diagnostics() -> RequestDiagnosticContext | None:
    return _current_diagnostics.get()


def clear_request_diagnostics() -> None:
    _current_diagnostics.set(None)


def next_llm_call_number() -> int:
    context = get_request_diagnostics()
    if context is None:
        return 0
    context.llm_call_count += 1
    return context.llm_call_count


def _redact_text(value: str) -> str:
    value = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    value = re.sub(
        r"(?i)postgres(?:ql)?://[^\s'\"}]+", "[REDACTED_CONNECTION_STRING]", value
    )
    return value


def redact_sensitive(value: Any, key: str = "") -> Any:
    """递归移除已知敏感字段，同时保留请求结构。"""
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): redact_sensitive(child, str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(child) for child in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "model_dump"):
        return redact_sensitive(value.model_dump(mode="json"), key)
    return _redact_text(str(value))


def write_trace_json(filename: str, value: Any) -> bool:
    context = get_request_diagnostics()
    if context is None or context.trace_directory is None:
        return False
    try:
        context.trace_directory.mkdir(parents=True, exist_ok=True)
        target = context.trace_directory / filename
        payload = value
        if isinstance(value, dict) and "trace_id" not in value:
            payload = {"trace_id": context.trace_id, **value}
        target.write_text(
            json.dumps(redact_sensitive(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def write_trace_text(filename: str, value: str) -> bool:
    context = get_request_diagnostics()
    if context is None or context.trace_directory is None:
        return False
    try:
        context.trace_directory.mkdir(parents=True, exist_ok=True)
        (context.trace_directory / filename).write_text(
            _redact_text(value), encoding="utf-8"
        )
        return True
    except Exception:
        return False

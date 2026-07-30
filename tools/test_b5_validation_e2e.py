"""隔离服务上的 B5 限定真实端到端验收。"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid


BASE_URL = os.environ.get("B5_VALIDATION_BASE_URL", "http://127.0.0.1:8001")
DYNAMIC_SOURCE_ID = os.environ["B5_VALIDATION_DYNAMIC_SOURCE_ID"]


def _request(path: str, *, method: str = "GET", payload: dict | None = None):
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read().decode("utf-8")


def _chat(source_id: str, question: str) -> list[dict]:
    conversation_id = "b5-validation-" + uuid.uuid4().hex
    _request(
        f"/api/conversations/{conversation_id}/source",
        method="POST",
        payload={"source_id": source_id},
    )
    text = _request(
        "/api/vanna/v2/chat_sse",
        method="POST",
        payload={
            "conversation_id": conversation_id,
            "message": question,
            "metadata": {"source_id": source_id},
        },
    )
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]


def _types(events: list[dict]) -> list[str]:
    return [
        event.get("rich", {}).get("type", "")
        for event in events
        if event.get("rich")
    ]


def main() -> int:
    cases = [
        (
            "动态源真实问数",
            DYNAMIC_SOURCE_ID,
            "查询 rs_outlet 总记录数",
            lambda values: "dataframe" in values and "data_source_suggestion" not in values,
        ),
        (
            "MySQL 正确源水质日报",
            "mysql-lzh-monitor",
            "生成2025年7月28日水质日报",
            lambda values: "report_result" in values,
        ),
        (
            "PostgreSQL 正确源问数",
            "postgresql-main",
            "查询 rs_outlet 总记录数",
            lambda values: "text" in values and "data_source_suggestion" not in values,
        ),
        (
            "PostgreSQL 错源报表建议",
            "postgresql-main",
            "生成2025年7月28日水质日报",
            lambda values: values == ["data_source_suggestion"],
        ),
    ]
    for name, source_id, question, validate in cases:
        events = _chat(source_id, question)
        event_types = _types(events)
        if not validate(event_types):
            raise AssertionError(f"{name} 事件不符合预期：{event_types}")
        print(f"[PASS] {name}：{event_types}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

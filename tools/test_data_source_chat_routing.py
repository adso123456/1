"""多数据源聊天路由的离线集成测试。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from config.data_source_config import DataSourceConfig
from step4_server import (
    ApplicationResources,
    DataSourceVannaFastAPIServer,
)


class FakeComponent:
    def __init__(self, text: str) -> None:
        self._text = text

    def serialize_for_frontend(self) -> dict[str, Any]:
        return {
            "type": "text",
            "id": "fake-text",
            "lifecycle": "complete",
            "timestamp": "offline",
            "visible": True,
            "interactive": False,
            "data": {"content": self._text},
        }


class FakeAgent:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def send_message(
        self,
        *,
        request_context: Any,
        message: str,
        conversation_id: str,
    ):
        self.calls.append((conversation_id, message))
        if self.fail:
            raise RuntimeError(f"{self.name} offline failure")
        yield FakeComponent(f"{self.name}:{message}")


def make_config(root: Path, source_id: str) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        database_type="offline",
        sql_dialect="offline",
        connection_settings={"label": source_id},
        metadata_path=root / f"{source_id}-metadata.json",
        memory_path=root / f"{source_id}-memory",
        read_only=True,
    )


def parse_sse(response_text: str) -> list[dict[str, Any] | str]:
    events: list[dict[str, Any] | str] = []
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        events.append(payload if payload == "[DONE]" else json.loads(payload))
    return events


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(
        prefix="data-source-chat-routing-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        configs = (
            make_config(root, "source-a"),
            make_config(root, "source-b"),
        )
        registry = DataSourceRegistry(configs)
        coordinator = DataSourceRequestCoordinator(registry)
        agents = {
            "source-a": FakeAgent("agent-a"),
            "source-b": FakeAgent("agent-b"),
        }
        factory_calls: dict[str, int] = {"source-a": 0, "source-b": 0}

        def factory(config: DataSourceConfig) -> DataSourceRuntime:
            factory_calls[config.source_id] += 1
            marker = object()
            return DataSourceRuntime(
                config=config,
                runner=marker,
                memory=object(),
                metadata_retriever=object(),
                sql_guard=object(),
                agent=agents[config.source_id],
            )

        manager = DataSourceRuntimeManager(registry, {"offline": factory})
        resources = ApplicationResources(registry, coordinator, manager)
        app = DataSourceVannaFastAPIServer(resources).create_app()

        with TestClient(app) as client:
            sources_response = client.get("/api/data-sources")
            sources_payload = sources_response.json()
            results.append(
                (
                    "数据源列表只返回安全字段",
                    sources_response.status_code == 200
                    and sources_payload
                    == [
                        {
                            "source_id": "source-a",
                            "database_type": "offline",
                        },
                        {
                            "source_id": "source-b",
                            "database_type": "offline",
                        },
                    ],
                    repr(sources_payload),
                )
            )

            def post(
                conversation_id: str | None,
                source_id: str | None,
                message: str = "hello",
            ):
                body: dict[str, Any] = {
                    "message": message,
                    "metadata": {},
                }
                if conversation_id is not None:
                    body["conversation_id"] = conversation_id
                if source_id is not None:
                    body["metadata"]["source_id"] = source_id
                return client.post("/api/vanna/v2/chat_sse", json=body)

            response_a1 = post("conversation-a", "source-a", "first")
            response_a2 = post("conversation-a", "source-a", "second")
            events_a1 = parse_sse(response_a1.text)
            results.append(
                (
                    "source A 仅调用 Agent A 且 SSE 格式保持不变",
                    agents["source-a"].calls
                    == [
                        ("conversation-a", "first"),
                        ("conversation-a", "second"),
                    ]
                    and agents["source-b"].calls == []
                    and isinstance(events_a1[0], dict)
                    and events_a1[0]["conversation_id"] == "conversation-a"
                    and events_a1[-1] == "[DONE]",
                    response_a1.text,
                )
            )
            results.append(
                (
                    "同 source_id 多次请求只创建一次 Runtime",
                    factory_calls["source-a"] == 1
                    and manager.runtimes["source-a"].agent
                    is agents["source-a"],
                    repr(factory_calls),
                )
            )

            response_b = post("conversation-b", "source-b", "third")
            results.append(
                (
                    "source B 仅调用 Agent B 且 Runtime Agent 不共享",
                    agents["source-b"].calls
                    == [("conversation-b", "third")]
                    and manager.runtimes["source-a"].agent
                    is not manager.runtimes["source-b"].agent
                    and "agent-b:third" in response_b.text,
                    response_b.text,
                )
            )

            before_calls = (
                len(agents["source-a"].calls),
                len(agents["source-b"].calls),
            )
            missing_conversation = post(None, "source-a")
            missing_source = post("conversation-missing-source", None)
            unknown_source = post(
                "conversation-unknown-source",
                "source-unknown",
            )
            switched_source = post("conversation-a", "source-b")
            after_calls = (
                len(agents["source-a"].calls),
                len(agents["source-b"].calls),
            )
            results.append(
                (
                    "缺失 conversation_id 拒绝且不调用 Agent",
                    "conversation_id" in missing_conversation.text
                    and before_calls == after_calls,
                    missing_conversation.text,
                )
            )
            results.append(
                (
                    "缺失或未知 source_id 拒绝且不调用 Agent",
                    "source_id" in missing_source.text
                    and "source_id" in unknown_source.text
                    and before_calls == after_calls,
                    missing_source.text + unknown_source.text,
                )
            )
            results.append(
                (
                    "同会话切换 source_id 被拒绝",
                    "source-a" in switched_source.text
                    and "source-b" in switched_source.text
                    and before_calls == after_calls,
                    switched_source.text,
                )
            )

        failing_agent = FakeAgent("agent-failing", fail=True)
        agents["source-a"] = failing_agent
        failure_registry = DataSourceRegistry(configs)
        failure_coordinator = DataSourceRequestCoordinator(failure_registry)
        failure_manager = DataSourceRuntimeManager(
            failure_registry,
            {"offline": factory},
        )
        failure_app = DataSourceVannaFastAPIServer(
            ApplicationResources(
                failure_registry,
                failure_coordinator,
                failure_manager,
            )
        ).create_app()
        with TestClient(failure_app) as client:
            failed = client.post(
                "/api/vanna/v2/chat_sse",
                json={
                    "message": "fail",
                    "conversation_id": "failure-a",
                    "metadata": {"source_id": "source-a"},
                },
            )
            healthy = client.post(
                "/api/vanna/v2/chat_sse",
                json={
                    "message": "healthy",
                    "conversation_id": "healthy-b",
                    "metadata": {"source_id": "source-b"},
                },
            )
            results.append(
                (
                    "一个数据源失败不影响另一个数据源",
                    "agent-failing offline failure" in failed.text
                    and "agent-b:healthy" in healthy.text,
                    failed.text + healthy.text,
                )
            )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed_count = sum(not passed for _, passed, _ in results)
    print(
        f"total={len(results)} "
        f"passed={len(results) - failed_count} failed={failed_count}"
    )
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

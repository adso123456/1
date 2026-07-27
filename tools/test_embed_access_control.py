"""嵌入 JWT、权限负路径与专用 Embed API 离线测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import AssistantApplicationRegistry
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.embed_access import (
    EMBED_AUDIENCE,
    EmbedAccessError,
    EmbedApplicationConfig,
    issue_embed_token,
    verify_embed_token,
)
from config.data_source_config import DataSourceConfig
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer

SECRET = "local-test-secret-that-is-longer-than-32-characters"
ORIGIN = "http://127.0.0.1:5174"
SOURCE_ID = "postgresql-main"


class FakeComponent:
    def serialize_for_frontend(self) -> dict[str, Any]:
        return {
            "type": "text",
            "id": "embed-text",
            "lifecycle": "complete",
            "timestamp": "offline",
            "visible": True,
            "interactive": False,
            "data": {"content": "embed-agent-ok"},
        }


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.request_contexts: list[Any] = []
        self.failure_message: str | None = None

    async def send_message(
        self,
        *,
        request_context: Any,
        message: str,
        conversation_id: str,
    ):
        self.calls.append((conversation_id, message))
        self.request_contexts.append(request_context)
        if self.failure_message:
            raise RuntimeError(self.failure_message)
        yield FakeComponent()


def make_config(*, enabled: bool = True) -> EmbedApplicationConfig:
    return EmbedApplicationConfig(
        app_id="local-cross-origin-demo",
        app_secret=SECRET,
        enabled=enabled,
        allowed_origins=(ORIGIN,),
        allowed_source_ids=(SOURCE_ID,),
        token_ttl_seconds=300,
    )


def token_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "aud": EMBED_AUDIENCE,
        "app_id": "local-cross-origin-demo",
        "sub": "local-demo-user",
        "parent_origin": ORIGIN,
        "allowed_source_ids": [SOURCE_ID],
        "iat": now,
        "exp": now + 300,
        "jti": "test-token-id",
    }
    claims.update(overrides)
    return claims


def encode(
    claims: dict[str, Any],
    *,
    secret: str = SECRET,
    algorithm: str = "HS256",
) -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


def tamper_token(token: str) -> str:
    header, payload, signature = token.split(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    return ".".join((header, payload, changed))


def expect_access_error(
    name: str,
    callback,
    expected_status: int,
    results: list[tuple[str, bool, str]],
) -> None:
    try:
        callback()
    except EmbedAccessError as exc:
        results.append(
            (name, exc.status_code == expected_status, exc.safe_message)
        )
    else:
        results.append((name, False, "未拒绝"))


def make_resources(root: Path):
    data_source = DataSourceConfig(
        source_id=SOURCE_ID,
        database_type="offline",
        sql_dialect="offline",
        connection_settings={"label": SOURCE_ID},
        metadata_path=root / "metadata.json",
        memory_path=root / "memory",
        read_only=True,
    )
    registry = DataSourceRegistry((data_source,))
    coordinator = DataSourceRequestCoordinator(registry)
    agent = FakeAgent()
    factory_calls = {"count": 0}

    def factory(config: DataSourceConfig) -> DataSourceRuntime:
        factory_calls["count"] += 1
        return DataSourceRuntime(
            config=config,
            runner=object(),
            memory=object(),
            metadata_retriever=object(),
            sql_guard=object(),
            agent=agent,
        )

    manager = DataSourceRuntimeManager(registry, {"offline": factory})
    return (
        ApplicationResources(registry, coordinator, manager),
        agent,
        factory_calls,
    )


def make_application_registry(
    root: Path,
    data_sources: DataSourceRegistry,
    config: EmbedApplicationConfig,
) -> AssistantApplicationRegistry:
    registry = AssistantApplicationRegistry(
        root / "assistant-apps.sqlite3",
        data_sources,
    )
    registry.create(
        app_id=config.app_id,
        name="Local cross-origin demo",
        enabled=config.enabled,
        app_secret=config.app_secret,
        allowed_origins=config.allowed_origins,
        allowed_source_ids=config.allowed_source_ids,
        token_ttl_seconds=config.token_ttl_seconds,
    )
    return registry


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    config = make_config()
    direct_temp = tempfile.TemporaryDirectory(prefix="embed-token-registry-")
    direct_resources, _, _ = make_resources(
        Path(direct_temp.name).resolve()
    )
    application_registry = make_application_registry(
        Path(direct_temp.name).resolve(),
        direct_resources.registry,
        config,
    )
    valid_token, expires_at = issue_embed_token(
        config,
        subject="local-demo-user",
    )
    principal = verify_embed_token(
        valid_token,
        parent_origin=ORIGIN,
        registry=application_registry,
        source_id=SOURCE_ID,
    )
    results.append(
        (
            "有效 Token 通过且包含预期权限",
            principal.app_id == config.app_id
            and principal.allowed_source_ids == (SOURCE_ID,)
            and principal.expires_at == expires_at,
            repr(principal),
        )
    )

    negative_tokens = [
        (
            "Token 过期",
            encode(token_claims(iat=int(time.time()) - 700, exp=int(time.time()) - 400)),
            ORIGIN,
            config,
            401,
        ),
        (
            "签名被篡改",
            tamper_token(valid_token),
            ORIGIN,
            config,
            401,
        ),
        (
            "错误密钥",
            encode(token_claims(), secret="different-secret-that-is-also-long-enough"),
            ORIGIN,
            config,
            401,
        ),
        (
            "错误 aud",
            encode(token_claims(aud="wrong-audience")),
            ORIGIN,
            config,
            401,
        ),
        (
            "未知 app_id",
            encode(token_claims(app_id="unknown-app")),
            ORIGIN,
            config,
            401,
        ),
        (
            "parent_origin 不一致",
            valid_token,
            "http://localhost:5174",
            config,
            403,
        ),
        (
            "Origin 不在白名单",
            valid_token,
            "http://127.0.0.1:5175",
            config,
            403,
        ),
        (
            "HTTPS Origin 不等于 HTTP 白名单",
            valid_token,
            "https://127.0.0.1:5174",
            config,
            403,
        ),
        (
            "恶意 Origin 被拒绝",
            valid_token,
            "http://evil.example",
            config,
            403,
        ),
        (
            "缺少必要 Claim",
            encode({key: value for key, value in token_claims().items() if key != "jti"}),
            ORIGIN,
            config,
            401,
        ),
        (
            "算法不符合要求",
            encode(token_claims(), algorithm="HS384"),
            ORIGIN,
            config,
            401,
        ),
    ]
    for name, token, origin, _app_config, status in negative_tokens:
        expect_access_error(
            name,
            lambda token=token, origin=origin:
                verify_embed_token(
                    token,
                    parent_origin=origin,
                    registry=application_registry,
                ),
            status,
            results,
        )
    application_registry.disable(config.app_id)
    expect_access_error(
        "应用禁用",
        lambda: verify_embed_token(
            valid_token,
            parent_origin=ORIGIN,
            registry=application_registry,
        ),
        403,
        results,
    )
    application_registry.enable(config.app_id)
    forbidden_token = encode(token_claims(allowed_source_ids=[]))
    expect_access_error(
        "source_id 不在允许列表",
        lambda: verify_embed_token(
            forbidden_token,
            parent_origin=ORIGIN,
            registry=application_registry,
            source_id=SOURCE_ID,
        ),
        403,
        results,
    )

    with tempfile.TemporaryDirectory(prefix="embed-access-") as temp_name:
        resources, agent, factory_calls = make_resources(
            Path(temp_name).resolve()
        )
        application_registry = make_application_registry(
            Path(temp_name).resolve(),
            resources.registry,
            config,
        )
        second_config = EmbedApplicationConfig(
            app_id="second-embed-app",
            app_secret="second-local-test-secret-longer-than-32-characters",
            enabled=True,
            allowed_origins=(ORIGIN,),
            allowed_source_ids=(SOURCE_ID,),
            token_ttl_seconds=300,
        )
        application_registry.create(
            app_id=second_config.app_id,
            name="Second assistant",
            app_secret=second_config.app_secret,
            allowed_origins=second_config.allowed_origins,
            allowed_source_ids=second_config.allowed_source_ids,
            token_ttl_seconds=second_config.token_ttl_seconds,
            theme="#654321",
            welcome="Second welcome",
            welcome_description="Second description",
            show_history=True,
        )
        second_token, _ = issue_embed_token(
            second_config,
            subject="second-user",
        )
        app = DataSourceVannaFastAPIServer(
            resources,
            assistant_application_registry=application_registry,
        ).create_app()
        headers = {
            "Authorization": f"Bearer {valid_token}",
            "X-Water-Agent-Parent-Origin": ORIGIN,
        }
        body = {
            "message": "hello",
            "conversation_id": "embed-conversation",
            "metadata": {"source_id": SOURCE_ID},
        }
        with TestClient(app) as client:
            application_response = client.get(
                "/api/embed/application",
                headers=headers,
            )
            second_application_response = client.get(
                "/api/embed/application",
                headers={
                    **headers,
                    "Authorization": f"Bearer {second_token}",
                },
            )
            missing_application_token = client.get(
                "/api/embed/application",
            )
            wrong_application_origin = client.get(
                "/api/embed/application",
                headers={
                    **headers,
                    "X-Water-Agent-Parent-Origin":
                        "http://unauthorized.example",
                },
            )
            application_registry.disable(second_config.app_id)
            disabled_application = client.get(
                "/api/embed/application",
                headers={
                    **headers,
                    "Authorization": f"Bearer {second_token}",
                },
            )
            application_registry.enable(second_config.app_id)
            safe_fields = {
                "app_id",
                "name",
                "theme",
                "logo_url",
                "welcome",
                "welcome_description",
                "show_history",
            }
            results.append(
                (
                    "应用信息接口按 Token 返回各自安全配置且不创建 Runtime",
                    application_response.status_code == 200
                    and second_application_response.status_code == 200
                    and set(application_response.json()) == safe_fields
                    and set(second_application_response.json()) == safe_fields
                    and application_response.json()["app_id"] == config.app_id
                    and second_application_response.json()
                    == {
                        "app_id": second_config.app_id,
                        "name": "Second assistant",
                        "theme": "#654321",
                        "logo_url": "",
                        "welcome": "Second welcome",
                        "welcome_description": "Second description",
                        "show_history": True,
                    }
                    and SECRET not in application_response.text
                    and second_config.app_secret
                    not in second_application_response.text
                    and "allowed_origins"
                    not in second_application_response.text
                    and missing_application_token.status_code == 401
                    and wrong_application_origin.status_code == 403
                    and disabled_application.status_code == 403
                    and factory_calls["count"] == 0
                    and agent.calls == [],
                    repr(
                        {
                            "first": application_response.json(),
                            "second": second_application_response.json(),
                        }
                    ),
                )
            )
            missing_source_body = {
                "message": "missing source",
                "conversation_id": "missing-source",
                "metadata": {},
            }
            unknown_source_body = {
                "message": "unknown source",
                "conversation_id": "unknown-source",
                "metadata": {"source_id": "unknown-source"},
            }
            negative_responses = [
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    json=missing_source_body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers={"X-Water-Agent-Parent-Origin": ORIGIN},
                    json=missing_source_body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {tamper_token(valid_token)}",
                    },
                    json=unknown_source_body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers={
                        **headers,
                        "X-Water-Agent-Parent-Origin": "http://evil.example",
                    },
                    json=body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {forbidden_token}",
                    },
                    json=body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers=headers,
                    json=missing_source_body,
                ),
                client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers=headers,
                    json=unknown_source_body,
                ),
            ]
            results.append(
                (
                    "无 Token、无效 Token、错误 Origin、禁止或缺失数据源均在 Agent 前拒绝",
                    [response.status_code for response in negative_responses]
                    == [401, 401, 401, 403, 403, 400, 400]
                    and factory_calls["count"] == 0
                    and agent.calls == [],
                    repr(
                        [
                            response.status_code
                            for response in negative_responses
                        ]
                    ),
                )
            )

            sources = client.get(
                "/api/embed/data-sources",
                headers=headers,
            )
            results.append(
                (
                    "合法 Token 只返回允许数据源安全字段",
                    sources.status_code == 200
                    and sources.json()
                    == [{
                        "source_id": SOURCE_ID,
                        "database_type": "offline",
                    }],
                    sources.text,
                )
            )

            context_headers = {
                **headers,
                "Proxy-Authorization": "Bearer proxy-secret",
                "User-Agent": "embed-test-agent",
                "Accept-Language": "zh-CN",
                "Cookie": "embed_session=sensitive-cookie",
            }
            response = client.post(
                "/api/embed/vanna/v2/chat_sse",
                headers=context_headers,
                json={
                    **body,
                    "metadata": {
                        "source_id": SOURCE_ID,
                        "token": valid_token,
                        "app_secret": SECRET,
                    },
                },
            )
            sse_lines = [
                line[6:]
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            first_event = json.loads(sse_lines[0])
            results.append(
                (
                    "合法聊天使用 Runtime Agent 且 SSE 格式不变",
                    response.status_code == 200
                    and agent.calls
                    == [("embed-conversation", "hello")]
                    and factory_calls["count"] == 1
                    and first_event["conversation_id"]
                    == "embed-conversation"
                    and sse_lines[-1] == "[DONE]",
                    response.text,
                )
            )
            embed_context = agent.request_contexts[-1]
            results.append(
                (
                    "Embed Agent 上下文只保留安全 Header 且清空 Cookie 和敏感 metadata",
                    embed_context.headers
                    == {
                        "user-agent": "embed-test-agent",
                        "accept-language": "zh-CN",
                    }
                    and embed_context.cookies == {}
                    and embed_context.query_params == {}
                    and embed_context.metadata == {"source_id": SOURCE_ID}
                    and valid_token not in repr(embed_context),
                    repr(embed_context),
                )
            )

            internal_error = (
                "database=127.0.0.1:5433 "
                "SQL=SELECT secret FROM private "
                "path=E:/private/metadata.json "
                f"token={valid_token}"
            )
            agent.failure_message = internal_error
            with patch("step4_server.logger.exception") as log_exception:
                failed_response = client.post(
                    "/api/embed/vanna/v2/chat_sse",
                    headers=headers,
                    json={
                        "message": "trigger controlled failure",
                        "conversation_id": "embed-failure",
                        "metadata": {"source_id": SOURCE_ID},
                    },
                )
            agent.failure_message = None
            failed_events = [
                line[6:]
                for line in failed_response.text.splitlines()
                if line.startswith("data: ")
            ]
            safe_error_event = json.loads(failed_events[0])
            results.append(
                (
                    "Embed SSE 内部异常脱敏、服务端记录且仍以 DONE 结束",
                    failed_response.status_code == 200
                    and safe_error_event["data"]["message"]
                    == "嵌入问数执行失败，请稍后重试。"
                    and internal_error not in failed_response.text
                    and valid_token not in failed_response.text
                    and failed_events[-1] == "[DONE]"
                    and log_exception.call_count == 1
                    and valid_token not in repr(log_exception.call_args),
                    failed_response.text,
                )
            )

            ordinary = client.post(
                "/api/vanna/v2/chat_sse",
                headers={
                    "Authorization": "Bearer ordinary-visible",
                    "Cookie": "ordinary_session=unchanged",
                },
                json={
                    "message": "ordinary",
                    "conversation_id": "ordinary-conversation",
                    "metadata": {"source_id": SOURCE_ID},
                },
            )
            results.append(
                (
                    "普通 API 不要求 Embed Token",
                    ordinary.status_code == 200
                    and "embed-agent-ok" in ordinary.text,
                    ordinary.text,
                )
            )
            ordinary_context = agent.request_contexts[-1]
            results.append(
                (
                    "普通 API RequestContext 行为未被 Embed 脱敏修改",
                    ordinary_context.headers.get("authorization")
                    == "Bearer ordinary-visible"
                    and ordinary_context.cookies.get("ordinary_session")
                    == "unchanged",
                    repr(ordinary_context),
                )
            )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed_count = sum(not passed for _, passed, _ in results)
    print(
        f"total={len(results)} "
        f"passed={len(results) - failed_count} failed={failed_count}"
    )
    direct_temp.cleanup()
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

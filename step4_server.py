"""启动支持显式数据源路由的 Vanna FastAPI 服务。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from backend.data_source_chat_handler import DataSourceChatHandler
from backend.embed_access import (
    EmbedAccessError,
    EmbedApplicationConfig,
    bearer_token,
    load_embed_application_config,
    verify_embed_token,
)
from backend.data_source_registry import (
    DataSourceRegistry,
    build_current_data_source_registry,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.postgresql_runtime_factory import create_postgresql_runtime
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from vanna.core.user.request_context import RequestContext
from vanna.servers.fastapi.app import VannaFastAPIServer
from vanna.servers.base import ChatRequest


@dataclass(frozen=True)
class ApplicationResources:
    registry: DataSourceRegistry
    coordinator: DataSourceRequestCoordinator
    runtime_manager: DataSourceRuntimeManager


class DataSourceVannaFastAPIServer(VannaFastAPIServer):
    """复用 Vanna 路由和 SSE 格式，但由请求动态选择 Agent。"""

    def __init__(
        self,
        resources: ApplicationResources,
        config: Mapping[str, Any] | None = None,
        embed_config: EmbedApplicationConfig | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.resources = resources
        self.embed_config = (
            embed_config
            if embed_config is not None
            else load_embed_application_config()
        )
        self.chat_handler = DataSourceChatHandler(
            resources.coordinator,
            resources.runtime_manager,
        )

    def create_app(self) -> FastAPI:
        app = super().create_app()

        @app.get("/api/data-sources")
        async def list_data_sources() -> list[dict[str, str]]:
            return [
                {
                    "source_id": source_id,
                    "database_type": self.resources.registry.require(
                        source_id
                    ).database_type,
                }
                for source_id in self.resources.registry.source_ids
            ]

        def authorize_embed(
            authorization: str | None,
            parent_origin: str | None,
            *,
            source_id: str | None = None,
        ):
            if not parent_origin:
                raise HTTPException(
                    status_code=403,
                    detail="嵌入父页面 Origin 未提供",
                )
            try:
                return verify_embed_token(
                    bearer_token(authorization),
                    parent_origin=parent_origin,
                    config=self.embed_config,
                    source_id=source_id,
                )
            except EmbedAccessError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.safe_message,
                ) from None

        @app.get("/api/embed/data-sources")
        async def list_embed_data_sources(
            authorization: str | None = Header(default=None),
            parent_origin: str | None = Header(
                default=None,
                alias="X-Water-Agent-Parent-Origin",
            ),
        ) -> list[dict[str, str]]:
            principal = authorize_embed(authorization, parent_origin)
            return [
                {
                    "source_id": source_id,
                    "database_type": self.resources.registry.require(
                        source_id
                    ).database_type,
                }
                for source_id in self.resources.registry.source_ids
                if source_id in principal.allowed_source_ids
            ]

        @app.post("/api/embed/vanna/v2/chat_sse")
        async def embed_chat_sse(
            chat_request: ChatRequest,
            http_request: Request,
            authorization: str | None = Header(default=None),
            parent_origin: str | None = Header(
                default=None,
                alias="X-Water-Agent-Parent-Origin",
            ),
        ) -> StreamingResponse:
            metadata = chat_request.metadata
            source_id = (
                metadata.get("source_id")
                if isinstance(metadata, Mapping)
                else None
            )
            if not isinstance(source_id, str) or not source_id.strip():
                raise HTTPException(
                    status_code=400,
                    detail="source_id 必须显式提供",
                )
            authorize_embed(
                authorization,
                parent_origin,
                source_id=source_id,
            )
            if source_id not in self.resources.registry.source_ids:
                raise HTTPException(
                    status_code=400,
                    detail="未知 source_id",
                )
            chat_request.request_context = RequestContext(
                cookies=dict(http_request.cookies),
                headers=dict(http_request.headers),
                remote_addr=(
                    http_request.client.host
                    if http_request.client
                    else None
                ),
                query_params=dict(http_request.query_params),
                metadata=chat_request.metadata,
            )

            async def generate() -> AsyncGenerator[str, None]:
                try:
                    async for chunk in self.chat_handler.handle_stream(
                        chat_request
                    ):
                        yield f"data: {chunk.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as exc:
                    error_data = {
                        "type": "error",
                        "data": {"message": str(exc)},
                        "conversation_id": (
                            chat_request.conversation_id or ""
                        ),
                        "request_id": chat_request.request_id or "",
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        return app


def create_application_resources(
    *,
    environ: Mapping[str, str] | None = None,
) -> ApplicationResources:
    registry = build_current_data_source_registry(environ=environ)
    coordinator = DataSourceRequestCoordinator(registry)
    runtime_manager = DataSourceRuntimeManager(
        registry,
        {"postgresql": create_postgresql_runtime},
    )
    return ApplicationResources(
        registry=registry,
        coordinator=coordinator,
        runtime_manager=runtime_manager,
    )


def create_server(
    resources: ApplicationResources | None = None,
    *,
    embed_config: EmbedApplicationConfig | None = None,
) -> DataSourceVannaFastAPIServer:
    return DataSourceVannaFastAPIServer(
        resources or create_application_resources(),
        embed_config=embed_config,
    )


def main() -> None:
    server = create_server()
    server.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

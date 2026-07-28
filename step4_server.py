"""启动支持显式数据源路由的 Vanna FastAPI 服务。"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from backend.assistant_application_registry import (
    AssistantApplicationRegistry,
    resolve_system_db_path,
)
from backend.assistant_admin_api import (
    create_admin_router,
)
from backend.data_source_chat_handler import DataSourceChatHandler
from backend.embed_access import (
    EmbedAccessError,
    bearer_token,
    verify_embed_token,
)
from backend.data_source_registry import (
    DataSourceRegistry,
    build_current_data_source_registry,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.postgresql_runtime_factory import create_postgresql_runtime
from backend.mysql_runtime_factory import create_mysql_runtime
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from vanna.core.user.request_context import RequestContext
from vanna.servers.fastapi.app import VannaFastAPIServer
from vanna.servers.base import ChatRequest

logger = logging.getLogger(__name__)
EMBED_SAFE_ERROR_MESSAGE = "嵌入问数执行失败，请稍后重试。"
EMBED_SAFE_CONTEXT_HEADERS = ("user-agent", "accept-language")


@dataclass(frozen=True)
class ApplicationResources:
    registry: DataSourceRegistry
    coordinator: DataSourceRequestCoordinator
    runtime_manager: DataSourceRuntimeManager
    assistant_application_registry: AssistantApplicationRegistry | None = None


class DataSourceVannaFastAPIServer(VannaFastAPIServer):
    """复用 Vanna 路由和 SSE 格式，但由请求动态选择 Agent。"""

    def __init__(
        self,
        resources: ApplicationResources,
        config: Mapping[str, Any] | None = None,
        assistant_application_registry: AssistantApplicationRegistry | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.resources = resources
        self.assistant_application_registry = (
            assistant_application_registry
            if assistant_application_registry is not None
            else resources.assistant_application_registry
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
            try:
                token = bearer_token(authorization)
                if not parent_origin:
                    raise EmbedAccessError(
                        403,
                        "嵌入父页面 Origin 未提供",
                    )
                return verify_embed_token(
                    token,
                    parent_origin=parent_origin,
                    registry=self.assistant_application_registry,
                    source_id=source_id,
                )
            except EmbedAccessError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.safe_message,
                ) from None

        @app.get("/api/embed/application")
        async def get_embed_application(
            authorization: str | None = Header(default=None),
            parent_origin: str | None = Header(
                default=None,
                alias="X-Water-Agent-Parent-Origin",
            ),
        ) -> dict[str, object]:
            principal = authorize_embed(authorization, parent_origin)
            if self.assistant_application_registry is None:
                raise HTTPException(
                    status_code=503,
                    detail="嵌入应用注册表尚未配置",
                )
            application = self.assistant_application_registry.get(
                principal.app_id
            )
            return {
                "app_id": application.app_id,
                "name": application.name,
                "theme": application.theme,
                "header_font_color": application.header_font_color,
                "logo_url": application.logo_url,
                "welcome": application.welcome,
                "welcome_description": application.welcome_description,
                "float_icon_url": application.float_icon_url,
                "float_icon_draggable": application.float_icon_draggable,
                "float_x_anchor": application.float_x_anchor,
                "float_x_offset": application.float_x_offset,
                "float_y_anchor": application.float_y_anchor,
                "float_y_offset": application.float_y_offset,
                "show_history": application.show_history,
            }

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
            principal = authorize_embed(authorization, parent_origin)
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
            if source_id not in self.resources.registry.source_ids:
                raise HTTPException(
                    status_code=400,
                    detail="未知 source_id",
                )
            if source_id not in principal.allowed_source_ids:
                raise HTTPException(
                    status_code=403,
                    detail="数据源未获授权",
                )
            safe_metadata = {"source_id": source_id}
            chat_request.metadata = safe_metadata
            chat_request.request_context = RequestContext(
                cookies={},
                headers={
                    name: value
                    for name in EMBED_SAFE_CONTEXT_HEADERS
                    if (value := http_request.headers.get(name))
                },
                remote_addr=(
                    http_request.client.host
                    if http_request.client
                    else None
                ),
                query_params={},
                metadata=safe_metadata,
            )

            async def generate() -> AsyncGenerator[str, None]:
                try:
                    async for chunk in self.chat_handler.handle_stream(
                        chat_request
                    ):
                        yield f"data: {chunk.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as exc:
                    safe_exception = RuntimeError(
                        "redacted embed execution error "
                        f"({type(exc).__name__})"
                    )
                    logger.exception(
                        "Embed chat execution failed",
                        exc_info=(
                            RuntimeError,
                            safe_exception,
                            exc.__traceback__,
                        ),
                    )
                    error_data = {
                        "type": "error",
                        "data": {"message": EMBED_SAFE_ERROR_MESSAGE},
                        "conversation_id": (
                            chat_request.conversation_id or ""
                        ),
                        "request_id": chat_request.request_id or "",
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
                    yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        if self.assistant_application_registry is not None:

            @app.exception_handler(RequestValidationError)
            async def safe_admin_validation_error(
                request: Request,
                exc: RequestValidationError,
            ):
                if request.url.path.startswith("/api/admin/"):
                    return JSONResponse(
                        status_code=422,
                        content={"detail": "管理请求格式无效"},
                    )
                return await request_validation_exception_handler(request, exc)

            app.include_router(
                create_admin_router(
                    application_registry=self.assistant_application_registry,
                    data_source_registry=self.resources.registry,
                )
            )

        return app


def create_application_resources(
    *,
    environ: Mapping[str, str] | None = None,
) -> ApplicationResources:
    registry = build_current_data_source_registry(
        environ=environ,
        include_mysql=True,
    )
    coordinator = DataSourceRequestCoordinator(registry)
    runtime_manager = DataSourceRuntimeManager(
        registry,
        {
            "postgresql": create_postgresql_runtime,
            "mysql": create_mysql_runtime,
        },
    )
    assistant_application_registry = AssistantApplicationRegistry(
        resolve_system_db_path(environ),
        registry,
    )
    assistant_application_registry.initialize()
    return ApplicationResources(
        registry=registry,
        coordinator=coordinator,
        runtime_manager=runtime_manager,
        assistant_application_registry=assistant_application_registry,
    )


def create_server(
    resources: ApplicationResources | None = None,
    *,
    assistant_application_registry: AssistantApplicationRegistry | None = None,
    environ: Mapping[str, str] | None = None,
) -> DataSourceVannaFastAPIServer:
    return DataSourceVannaFastAPIServer(
        resources or create_application_resources(environ=environ),
        assistant_application_registry=assistant_application_registry,
    )


def main() -> None:
    server = create_server()
    server.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

"""启动支持显式数据源路由的 Vanna FastAPI 服务。"""

from __future__ import annotations

import json
import logging
import os
import re
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
from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    generate_local_credential_key,
    resolve_catalog_path,
)
from backend.data_source_management_api import (
    create_data_source_management_router,
)
from backend.data_source_suggestion import (
    DataSourceSuggestionChatHandler,
    DataSourceSuggestionService,
)
from backend.data_source_scope_stats import scope_statistics
from backend.embed_access import (
    EmbedAccessError,
    authorize_embed_origin,
    extract_app_id_from_request,
)
from backend.data_source_registry import (
    DataSourceRegistry,
    build_current_data_source_registry,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.postgresql_runtime_factory import create_postgresql_runtime
from backend.mysql_runtime_factory import create_mysql_runtime
from backend.question_suggestion_api import create_question_suggestion_router
from backend.water_quality_reports.api import create_report_router
from backend.water_quality_reports.application_service import (
    ReportApplicationService,
)
from backend.water_quality_reports.artifacts import ReportArtifactStore
from backend.water_quality_reports.chat_handler import WaterQualityReportChatHandler
from backend.water_quality_reports.embed_api import create_embed_report_router
from backend.water_quality_reports.repository import ReportRepository
from backend.water_quality_reports.service import WaterQualityReportService
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
)
from vanna.core.user.request_context import RequestContext
from vanna.servers.fastapi.app import VannaFastAPIServer
from vanna.servers.base import ChatRequest

logger = logging.getLogger(__name__)
EMBED_SAFE_ERROR_MESSAGE = "嵌入问数执行失败，请稍后重试。"
EMBED_SAFE_CONTEXT_HEADERS = ("user-agent", "accept-language")
EMBED_PATH_PATTERN = re.compile(r"^/api/embed/apps/([^/]+)(?:/|$)")
EMBED_ALLOWED_METHODS = "GET, POST, OPTIONS"
EMBED_ALLOWED_HEADERS = "Accept, Content-Type"


@dataclass(frozen=True)
class ApplicationResources:
    registry: DataSourceRegistry
    coordinator: DataSourceRequestCoordinator
    runtime_manager: DataSourceRuntimeManager
    assistant_application_registry: AssistantApplicationRegistry | None = None
    catalog: DataSourceCatalog | None = None


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
        report_service_factory = None
        report_artifact_store = None
        if "mysql-lzh-monitor" in self.resources.registry.source_ids:
            report_config = self.resources.registry.require("mysql-lzh-monitor")
            report_service_factory = lambda: WaterQualityReportService(
                ReportRepository(report_config)
            )
            report_artifact_store = ReportArtifactStore(report_service_factory)
            if not isinstance(self.chat_handler, WaterQualityReportChatHandler):
                self.chat_handler = WaterQualityReportChatHandler(
                    self.chat_handler,
                    report_artifact_store,
                    lambda request: self.resources.coordinator.resolve(
                        request.conversation_id,
                        request.metadata,
                    ).source_id,
                )
        if self.resources.catalog is not None:
            self.chat_handler = DataSourceSuggestionChatHandler(
                self.chat_handler,
                self.resources.coordinator,
                DataSourceSuggestionService(self.resources.catalog),
            )
        app = super().create_app()

        @app.get("/api/data-sources")
        async def list_data_sources() -> list[dict[str, Any]]:
            if self.resources.catalog is not None:
                return [
                    {
                        **record.safe_summary_dict(),
                        **scope_statistics(record),
                    }
                    for record in self.resources.catalog.list()
                ]
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
            app_id: str,
            origin: str | None,
            *,
            source_id: str | None = None,
        ):
            """用浏览器真实 Origin 请求头校验嵌入访问。"""
            try:
                safe_app_id = app_id.strip()
                if not safe_app_id:
                    raise EmbedAccessError(400, "缺少 app_id")
                if not origin or not origin.strip():
                    raise EmbedAccessError(401, "浏览器 Origin 请求头缺失")
                return authorize_embed_origin(
                    app_id=safe_app_id,
                    origin=origin,
                    registry=self.assistant_application_registry,
                    source_id=source_id,
                )
            except EmbedAccessError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.safe_message,
                ) from None

        def embed_cors_headers(origin: str) -> dict[str, str]:
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": EMBED_ALLOWED_METHODS,
                "Access-Control-Allow-Headers": EMBED_ALLOWED_HEADERS,
                "Access-Control-Expose-Headers": (
                    "Content-Disposition, Content-Type"
                ),
                "Vary": "Origin",
            }

        @app.middleware("http")
        async def dynamic_embed_cors(request: Request, call_next):
            match = EMBED_PATH_PATTERN.match(request.url.path)
            if match is None:
                return await call_next(request)
            origin = request.headers.get("Origin")
            try:
                principal = authorize_embed_origin(
                    app_id=match.group(1),
                    origin=origin,
                    registry=self.assistant_application_registry,
                )
            except EmbedAccessError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.safe_message},
                    headers={"Vary": "Origin"},
                )
            headers = embed_cors_headers(principal.parent_origin)
            if request.method == "OPTIONS":
                return Response(
                    status_code=204,
                    headers=headers,
                )
            response = await call_next(request)
            for name, value in headers.items():
                if name == "Vary" and response.headers.get("Vary"):
                    existing = response.headers["Vary"]
                    if "origin" not in existing.lower():
                        response.headers["Vary"] = f"{existing}, Origin"
                else:
                    response.headers[name] = value
            return response

        @app.get("/api/embed/apps/{app_id}/application")
        async def get_embed_application(
            request: Request,
            app_id: str,
        ) -> dict[str, object]:
            origin = request.headers.get("Origin")
            principal = authorize_embed(app_id, origin)
            application = principal.application
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

        @app.get("/api/embed/apps/{app_id}/data-sources")
        async def list_embed_data_sources(
            request: Request,
            app_id: str,
        ) -> list[dict[str, Any]]:
            origin = request.headers.get("Origin")
            principal = authorize_embed(app_id, origin)
            if self.resources.catalog is not None:
                return [
                    {
                        **record.safe_summary_dict(),
                        **scope_statistics(record),
                    }
                    for record in self.resources.catalog.list(
                        status="ready",
                        enabled=True,
                    )
                    if record.source_id in principal.application.allowed_source_ids
                ]
            return [
                {
                    "source_id": source_id,
                    "database_type": self.resources.registry.require(
                        source_id
                    ).database_type,
                }
                for source_id in self.resources.registry.source_ids
                if source_id in principal.application.allowed_source_ids
            ]

        @app.post("/api/embed/apps/{app_id}/chat_sse")
        async def embed_chat_sse(
            app_id: str,
            chat_request: ChatRequest,
            http_request: Request,
        ) -> StreamingResponse:
            origin = http_request.headers.get("Origin")
            metadata = chat_request.metadata
            if not isinstance(metadata, Mapping):
                raise HTTPException(
                    status_code=400,
                    detail="metadata 必须显式提供",
                )
            source_id = metadata.get("source_id")
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
            principal = authorize_embed(
                app_id,
                origin,
                source_id=source_id,
            )
            safe_metadata = {
                "source_id": source_id,
                "_allowed_source_ids": list(
                    principal.application.allowed_source_ids
                ),
            }
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
                metadata={"source_id": source_id},
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

        embed_report_service = (
            ReportApplicationService(report_artifact_store)
            if report_artifact_store is not None
            else None
        )
        app.include_router(
            create_embed_report_router(
                service=embed_report_service,
                authorize=lambda app_id, origin, source_id: authorize_embed(
                    app_id,
                    origin,
                    source_id=source_id,
                ),
            )
        )

        if self.assistant_application_registry is not None:

            @app.exception_handler(RequestValidationError)
            async def safe_admin_validation_error(
                request: Request,
                exc: RequestValidationError,
            ):
                if (
                    request.url.path.startswith("/api/admin/")
                    or request.url.path.startswith(
                        "/api/data-source-management"
                    )
                    or request.url.path.startswith("/api/conversations/")
                ):
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

        if self.resources.catalog is not None:
            app.include_router(
                create_data_source_management_router(
                    catalog=self.resources.catalog,
                    coordinator=self.resources.coordinator,
                    runtime_manager=self.resources.runtime_manager,
                )
            )
            app.include_router(
                create_question_suggestion_router(
                    catalog=self.resources.catalog,
                    coordinator=self.resources.coordinator,
                )
            )

        if report_service_factory is not None:
            app.include_router(
                create_report_router(
                    report_service_factory,
                    artifact_store=report_artifact_store,
                )
            )

        return app


def create_application_resources(
    *,
    environ: Mapping[str, str] | None = None,
) -> ApplicationResources:
    source = dict(os.environ if environ is None else environ)
    if not source.get("DATA_SOURCE_CREDENTIAL_KEY", "").strip():
        if environ is None:
            source["DATA_SOURCE_CREDENTIAL_KEY"] = (
                generate_local_credential_key()
            )
    cipher = (
        CredentialCipher.from_environment(source)
        if source.get("DATA_SOURCE_CREDENTIAL_KEY", "").strip()
        else None
    )
    bootstrap_registry = build_current_data_source_registry(
        environ=source,
        include_mysql=True,
    )
    bootstrap: list[dict[str, Any]] = []
    names = {
        "postgresql-main": (
            "排污口治理数据",
            "排污口基础、监测、溯源与整治数据",
        ),
        "mysql-lzh-monitor": (
            "梁子湖监测数据",
            "梁子湖水质、水文、气象、污染源与预警数据",
        ),
    }
    credential_refs = {
        "postgresql-main": {
            "username": "DB_USER",
            "password": "DB_PASSWORD",
        },
        "mysql-lzh-monitor": {
            "username": "MYSQL_USER",
            "password": "MYSQL_PASSWORD",
        },
    }
    for source_id in bootstrap_registry.source_ids:
        config = bootstrap_registry.require(source_id)
        display_name, description = names[source_id]
        settings = config.connection_settings
        try:
            metadata = json.loads(config.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = []
        bootstrap.append(
            {
                "source_id": source_id,
                "display_name": display_name,
                "description": description,
                "database_type": config.database_type,
                "host": settings["host"],
                "port": settings["port"],
                "database_name": settings["database"],
                "schema_name": "public" if config.database_type == "postgresql" else "",
                "ssl_mode": settings.get("sslmode", ""),
                "connect_timeout": settings["connect_timeout"],
                "credential_reference": credential_refs[source_id],
                "metadata_path": config.metadata_path,
                "memory_path": config.memory_path,
                "selected_tables_count": len(
                    {item.get("table") for item in metadata}
                ),
                "selected_columns_count": len(metadata),
                "routing_summary": description,
                "capabilities": (
                    [
                        "water_quality_daily_report",
                        "water_quality_monthly_report",
                    ]
                    if source_id == "mysql-lzh-monitor"
                    else []
                ),
            }
        )
    catalog = DataSourceCatalog(
        resolve_catalog_path(source),
        cipher=cipher,
        environ=source,
    )
    catalog.initialize(bootstrap)
    registry = DataSourceRegistry.from_catalog(catalog)
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
        catalog=catalog,
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
    server.run(
        host="0.0.0.0",
        port=int(os.getenv("VANNA_SERVER_PORT", "8000")),
    )


if __name__ == "__main__":
    main()

"""启动支持显式数据源路由的 Vanna FastAPI 服务。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator

from backend.assistant_application_registry import (
    AssistantApplicationRegistry,
    resolve_system_db_path,
)
from backend.assistant_admin_api import (
    create_admin_router,
)
from backend.data_source_chat_handler import DataSourceChatHandler
from backend.learning_candidate_store import LearningCandidateStore
from backend.log_buffer import install_log_buffer
from backend.llm_settings import apply_settings_to_environ
from backend.llm_settings_api import create_llm_settings_router
from backend.runtime_learning_api import create_runtime_learning_router
from backend.runtime_learning_judge import RuntimeLearningJudge
from backend.runtime_learning_service import RuntimeLearningService
from backend.runtime_learning_worker import RuntimeLearningWorker
from backend.question_suggestion_generator import generation_identity
from backend.question_suggestion_tasks import (
    QuestionSuggestionTaskStore,
    reconcile_question_suggestion_tasks,
)
from backend.question_suggestion_worker import QuestionSuggestionWorker
from backend.system_logs_api import create_system_logs_router
from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    generate_local_credential_key,
    resolve_catalog_path,
)
from backend.data_source_management_api import (
    BindConversationRequest,
    create_data_source_management_router,
)
from backend.data_source_claim_identity import load_builtin_asset_lineage
from backend.data_source_suggestion import (
    DataSourceSuggestionChatHandler,
    DataSourceSuggestionService,
)
from backend.data_source_scope_stats import scope_statistics
from backend.embed_access import (
    EmbedAccessError,
    authorize_embed_origin,
    extract_app_id_from_request,
    origin_from_referer,
)
from backend.data_source_registry import (
    DataSourceRegistry,
    build_current_data_source_registry,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.postgresql_runtime_factory import create_postgresql_runtime
from backend.mysql_runtime_factory import create_mysql_runtime
from backend.runtime_prewarm import RuntimePrewarmer
from backend.question_suggestion_api import create_question_suggestion_router
from backend.question_suggestion_assets import (
    load_question_directory,
    matches_formal_identity,
    select_suggested_questions,
)
from backend.water_quality_reports.api import create_report_router
from backend.water_quality_reports.application_service import (
    ReportApplicationService,
)
from backend.water_quality_reports.artifacts import ReportArtifactStore
from backend.water_quality_reports.chat_handler import WaterQualityReportChatHandler
from backend.water_quality_reports.embed_api import create_embed_report_router
from backend.water_quality_reports.repository import ReportRepository
from backend.water_quality_reports.service import WaterQualityReportService
from backend.tracing_llm_service import TracingOpenAILlmService
from config.learning_settings import OnlineLearningSettings
from config.performance_settings import QueryPerformanceSettings
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
        self.runtime_prewarmer = RuntimePrewarmer(
            resources.runtime_manager
        )
        # 运行时受控自学习基础设施（候选库 / Judge / Service / Worker）。
        self.learning_settings = OnlineLearningSettings.from_environment()
        self.learning_store = LearningCandidateStore(
            self.learning_settings.candidate_db_path
        )
        # Key 存在时构造真实 Judge；缺失时 Judge 自动降级 NEEDS_REVIEW，
        # 保证空配置服务器可启动，管理页配置 Key 后（重启或热更新）即可用。
        learning_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        learning_llm = (
            TracingOpenAILlmService(
                model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                api_key=learning_api_key,
                base_url=os.environ.get(
                    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
                ),
                settings=QueryPerformanceSettings.from_environment(),
            )
            if learning_api_key
            else None
        )
        self.learning_judge = RuntimeLearningJudge(
            learning_llm, self.learning_settings
        )
        # 推荐问题派生资产：正式发布成功后异步生成（第一阶段）。
        self.question_suggestion_store: QuestionSuggestionTaskStore | None = None
        self.question_suggestion_worker: QuestionSuggestionWorker | None = None
        self._post_publish_hook: (
            Callable[[str, dict[str, Any]], None] | None
        ) = None
        if resources.catalog is not None:
            self.question_suggestion_store = QuestionSuggestionTaskStore()
            self.question_suggestion_worker = QuestionSuggestionWorker(
                self.question_suggestion_store,
                resources.catalog,
            )

            def _enqueue_question_suggestions(
                source_id: str,
                result: dict[str, Any],
            ) -> None:
                try:
                    identity = generation_identity(
                        resources.catalog,
                        source_id,
                    )
                    if self.question_suggestion_store is not None:
                        self.question_suggestion_store.enqueue(identity)
                    logger.info(
                        "推荐问题生成任务已入队（source=%s revision=%s）",
                        source_id,
                        identity["runtime_revision"],
                    )
                except Exception:
                    logger.exception(
                        "推荐问题生成任务入队失败（source=%s），不影响发布",
                        source_id,
                    )

            self._post_publish_hook = _enqueue_question_suggestions
        self.learning_service = RuntimeLearningService(
            catalog=resources.catalog,
            runtime_manager=resources.runtime_manager,
            store=self.learning_store,
            judge=self.learning_judge,
            settings=self.learning_settings,
            dynamic_settings=True,
            post_publish_hook=self._post_publish_hook,
        )
        self.learning_worker = RuntimeLearningWorker(
            self.learning_service,
            self.learning_settings,
            dynamic_settings=True,
        )
        self.chat_handler = DataSourceChatHandler(
            resources.coordinator,
            resources.runtime_manager,
            self.runtime_prewarmer.snapshot,
            capture_hook=self.learning_service.capture,
        )
        fastapi_config = dict(self.config.get("fastapi") or {})
        fastapi_config.setdefault("lifespan", self._lifespan)
        self.config["fastapi"] = fastapi_config

    @asynccontextmanager
    async def _lifespan(self, _app: FastAPI):
        await self.runtime_prewarmer.warm_ready_sources()
        logging.getLogger("water-agent").info(
            "服务启动完成：数据源 %d 个",
            len(self.resources.catalog.list()),
        )
        try:
            self.learning_service.recover_interrupted()
        except Exception:
            pass
        # Worker 常驻：未启用时每轮直接返回，管理页打开开关后无需重启即生效。
        if self.question_suggestion_worker is not None:
            if (
                self.question_suggestion_store is not None
                and self.resources.catalog is not None
            ):
                try:
                    reconcile_question_suggestion_tasks(
                        self.resources.catalog,
                        self.question_suggestion_store,
                    )
                except Exception:
                    logger.exception("推荐问题启动收敛失败")
            self.question_suggestion_worker.start()
        self.learning_worker.start()
        try:
            yield
        finally:
            if self.question_suggestion_worker is not None:
                await self.question_suggestion_worker.stop()
            await self.learning_worker.stop()

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

        @app.get("/api/runtime-prewarm-status")
        async def runtime_prewarm_status() -> dict[str, dict[str, object]]:
            return self.runtime_prewarmer.snapshot()

        def authorize_embed(
            app_id: str,
            origin: str | None,
            *,
            source_id: str | None = None,
            referer: str | None = None,
        ):
            """用浏览器真实 Origin 请求头校验嵌入访问。"""
            try:
                safe_app_id = app_id.strip()
                if not safe_app_id:
                    raise EmbedAccessError(400, "缺少 app_id")
                if not origin or not origin.strip():
                    origin = origin_from_referer(referer)
                if not origin:
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
            origin = request.headers.get("Origin") or origin_from_referer(
                request.headers.get("Referer")
            )
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
            principal = authorize_embed(
                app_id,
                origin,
                referer=request.headers.get("Referer"),
            )
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
            principal = authorize_embed(
                app_id,
                origin,
                referer=request.headers.get("Referer"),
            )
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
                referer=http_request.headers.get("Referer"),
            )
            safe_metadata = {
                "source_id": source_id,
                "_embed_request": True,
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

        @app.get("/api/embed/apps/{app_id}/conversations/{conversation_id}/suggested-questions")
        async def embed_suggested_questions(
            request: Request,
            app_id: str,
            conversation_id: str,
        ) -> dict[str, object]:
            origin = request.headers.get("Origin")
            principal = authorize_embed(
                app_id,
                origin,
                referer=request.headers.get("Referer"),
            )
            try:
                context = self.resources.coordinator.require(conversation_id)
            except ValueError:
                raise HTTPException(
                    status_code=404,
                    detail="会话尚未绑定数据源",
                ) from None
            if context.source_id not in principal.application.allowed_source_ids:
                raise HTTPException(
                    status_code=403,
                    detail="数据源未获授权",
                ) from None
            if self.resources.catalog is None:
                return {
                    "source_id": context.source_id,
                    "asset_version": None,
                    "questions": [],
                }
            try:
                record = self.resources.catalog.require(context.source_id)
            except Exception:
                raise HTTPException(
                    status_code=404,
                    detail="数据源不存在",
                ) from None
            if record.status != "ready" or not record.enabled_for_chat:
                return {
                    "source_id": context.source_id,
                    "asset_version": None,
                    "questions": [],
                }
            directory = load_question_directory(context.source_id)
            if directory is None:
                return {
                    "source_id": context.source_id,
                    "asset_version": None,
                    "questions": [],
                }
            if not matches_formal_identity(
                directory,
                generation_identity(self.resources.catalog, context.source_id),
            ):
                return {
                    "source_id": context.source_id,
                    "asset_version": directory["asset_version"],
                    "questions": [],
                }
            questions = select_suggested_questions(directory, conversation_id)
            return {
                "source_id": context.source_id,
                "asset_version": directory["asset_version"],
                "questions": questions,
            }

        @app.post("/api/embed/apps/{app_id}/conversations/{conversation_id}/source")
        async def embed_bind_conversation_source(
            request: Request,
            app_id: str,
            conversation_id: str,
            body: BindConversationRequest,
        ) -> dict[str, str]:
            origin = request.headers.get("Origin")
            authorize_embed(
                app_id,
                origin,
                source_id=body.source_id,
                referer=request.headers.get("Referer"),
            )
            try:
                context = self.resources.coordinator.bind(
                    conversation_id,
                    body.source_id,
                )
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=409,
                    detail="当前会话无法绑定该数据源",
                ) from None
            return {
                "conversation_id": context.conversation_id,
                "source_id": context.source_id,
            }

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
                    question_suggestion_hook=self._post_publish_hook,
                )
            )
            app.include_router(
                create_runtime_learning_router(
                    self.learning_service,
                    self.learning_worker,
                )
            )
            app.include_router(
                create_llm_settings_router(self.resources.runtime_manager)
            )
            app.include_router(create_system_logs_router())
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
    # 管理页保存的 LLM 配置（卷内）优先于 .env / 宿主机环境。
    apply_settings_to_environ()
    install_log_buffer()
    source = dict(os.environ if environ is None else environ)
    if not source.get("DATA_SOURCE_CREDENTIAL_KEY", "").strip():
        if environ is None:
            source["DATA_SOURCE_CREDENTIAL_KEY"] = (
                _resolve_or_create_credential_key()
            )
    cipher = (
        CredentialCipher.from_environment(source)
        if source.get("DATA_SOURCE_CREDENTIAL_KEY", "").strip()
        else None
    )
    # 内置数据源引导默认关闭：镜像发布到服务器后数据源为空，通过前端管理页添加。
    # 本地开发需要在 .env 设置 DATA_SOURCE_BOOTSTRAP_BUILTINS=true。
    bootstrap_builtins = str(
        source.get("DATA_SOURCE_BOOTSTRAP_BUILTINS", "").strip().lower()
    ) in {"1", "true", "yes", "on"}
    bootstrap: list[dict[str, Any]] = []
    if bootstrap_builtins:
        bootstrap_registry = build_current_data_source_registry(
            environ=source,
            include_mysql=True,
        )
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
                metadata = json.loads(
                    config.metadata_path.read_text(encoding="utf-8")
                )
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
                    "schema_name": (
                        "public" if config.database_type == "postgresql" else ""
                    ),
                    "ssl_mode": settings.get("sslmode", ""),
                    "connect_timeout": settings["connect_timeout"],
                    "credential_reference": credential_refs[source_id],
                    "metadata_path": config.metadata_path,
                    "memory_path": config.memory_path,
                    "discovered_metadata": metadata,
                    "selected_scope": metadata,
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
    if bootstrap_builtins:
        catalog.initialize_builtin_claims(load_builtin_asset_lineage())
        # 修复历史引导空 scope：内置源 selected_scope 为空时从正式 Metadata 补齐，
        # 否则数据源建议等依赖 selected_scope 的逻辑读不到任何表/字段。
        for item in bootstrap:
            record = catalog.require(item["source_id"])
            if record.selected_scope:
                continue
            try:
                metadata = json.loads(
                    record.metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if isinstance(metadata, list) and metadata:
                catalog.populate_bootstrap_scope(item["source_id"], metadata)
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


def _resolve_or_create_credential_key() -> str:
    """固定凭据加密密钥：优先复用 agent_data 卷里持久化的 key，
    没有则生成并写入卷，避免容器重建后前端配置的密码无法解密。
    环境变量或 .env 里显式设置的值优先级最高（调用方已先检查）。"""
    from backend.data_source_catalog import generate_local_credential_key
    from config.settings import AGENT_DATA_DIR

    persisted_path = Path(AGENT_DATA_DIR) / "system" / "credential_key"
    if persisted_path.is_file():
        try:
            existing = persisted_path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing
    key = generate_local_credential_key()
    try:
        persisted_path.parent.mkdir(parents=True, exist_ok=True)
        persisted_path.write_text(key + "\n", encoding="utf-8")
    except OSError:
        pass
    return key


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

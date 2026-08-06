"""仅限本地主工作台的数据源管理与会话绑定 API。"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

from backend.assistant_admin_api import _authorize
from backend.data_source_catalog import (
    DataSourceCatalog,
    DataSourceCatalogError,
    DataSourceConflict,
    DataSourceNotFound,
)
from backend.data_source_connectors import (
    DataSourceAssetPreparer,
    DirectDatabaseConnector,
)
from backend.data_source_onboarding import DataSourceOnboardingService
from backend.data_source_profiler import DataSourceProfiler
from backend.data_source_semantics import DataSourceSemanticAnalyzer
from backend.data_source_sql_memory import VerifiedSQLMemoryGenerator
from backend.builtin_data_source_claim import BuiltinDataSourceClaimService
from backend.data_source_claim_identity import (
    BUILTIN_CLAIM_SOURCE_IDS,
    load_builtin_asset_lineage,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.data_source_scope_stats import scope_statistics
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from config.settings import PROJECT_ROOT, resolve_project_path


logger = logging.getLogger(__name__)


def _match_builtin_source(database_type: str, database_name: str) -> str | None:
    """按数据库身份（类型 + 库名）匹配内置血缘，命中则返回内置 source_id。"""
    lineage = load_builtin_asset_lineage()
    normalized_name = str(database_name or "").strip().lower()
    for source_id, item in lineage.items():
        if (
            str(item.get("database_type") or "").lower()
            == str(database_type or "").strip().lower()
            and str(item.get("database_name") or "").lower() == normalized_name
        ):
            return source_id
    return None


def _builtin_asset_paths(source_id: str, environ: Any) -> tuple[Path, Path]:
    """内置副本身份对应的烘焙资产路径（镜像内不依赖 .env 凭据）。"""
    if source_id == "postgresql-main":
        return (
            resolve_project_path("agent_data/column_metadata_index.json"),
            resolve_project_path("vanna_data"),
        )
    mysql_root = PROJECT_ROOT / "agent_data" / "mysql-lzh-monitor"
    configured = str(environ.get("MYSQL_VANNA_DATA_DIR", "") or "").strip()
    if configured:
        memory_path = resolve_project_path(configured)
    else:
        revisions = sorted(
            (
                path
                for path in mysql_root.glob("mysql-lzh-monitor.revision-*")
                if path.is_dir()
            ),
            key=lambda path: path.name,
        )
        memory_path = revisions[-1] if revisions else mysql_root / "memory"
    return (
        resolve_project_path(mysql_root / "column_metadata_index.json"),
        memory_path,
    )


def _public_record(record: Any, *, detail: bool = False) -> dict[str, Any]:
    return {
        **record.public_dict(detail=detail),
        **scope_statistics(record),
    }


class CreateDataSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str
    description: str = ""
    database_type: Literal["mysql", "postgresql"]
    host: str
    port: int = Field(gt=0, le=65535)
    database_name: str
    schema_name: str = ""
    ssl_mode: str = ""
    mysql_tls_mode: str = "disabled"
    ssl_ca_path: str = ""
    ssl_cert_path: str = ""
    ssl_key_path: str = ""
    connect_timeout: int = Field(default=10, gt=0, le=120)
    username: str
    password: str


class UpdateDataSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str | None = None
    description: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, gt=0, le=65535)
    database_name: str | None = None
    schema_name: str | None = None
    ssl_mode: str | None = None
    mysql_tls_mode: str | None = None
    ssl_ca_path: str | None = None
    ssl_cert_path: str | None = None
    ssl_key_path: str | None = None
    connect_timeout: int | None = Field(default=None, gt=0, le=120)
    username: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def reject_null(self):
        if any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("更新字段不能为 null")
        return self


class ScopeItem(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    schema_name: str = Field(default="", alias="schema")
    table: str
    column: str
    object_type: str = "table"
    table_comment: str = ""
    type: str
    comment: str = ""
    nullable: bool = True
    primary_key: bool = False
    ordinal_position: int = 0


class SaveScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[ScopeItem]


class BindConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str


class DeleteDataSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmation: str
    local_dependencies: list[str] = Field(default_factory=list)


def _safe(action):
    try:
        return action()
    except DataSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except DataSourceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except DataSourceCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except sqlite3.Error as exc:
        logger.error("Data source catalog failure (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="数据源目录暂时不可用",
        ) from None
    except Exception as exc:
        logger.error("Data source operation failure (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="数据源操作失败",
        ) from None


def create_data_source_management_router(
    *,
    catalog: DataSourceCatalog,
    coordinator: DataSourceRequestCoordinator,
    runtime_manager: DataSourceRuntimeManager,
) -> APIRouter:
    connector = DirectDatabaseConnector(catalog)
    preparer = DataSourceAssetPreparer(catalog, runtime_manager)
    profiler = DataSourceProfiler(catalog, connector)
    semantic_analyzer = DataSourceSemanticAnalyzer()
    sql_memory_generator = VerifiedSQLMemoryGenerator(catalog, connector)
    claim_service = BuiltinDataSourceClaimService(
        catalog,
        connector,
        profiler,
        semantic_analyzer,
        preparer,
        sql_memory_generator,
    )
    onboarding = DataSourceOnboardingService(
        catalog,
        connector,
        profiler,
        preparer,
        semantic_analyzer=semantic_analyzer,
        sql_memory_generator=sql_memory_generator,
        claim_service=claim_service,
    )
    runtime_manager.add_release_callback(preparer.asset_cleaner.retry_pending_cleanup)
    preparer.asset_cleaner.cleanup_stale_batches()
    preparer.asset_cleaner.retry_pending_cleanup()
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    def start_builtin_claim_job(source_id: str, job_type: str) -> dict[str, Any]:
        if source_id not in BUILTIN_CLAIM_SOURCE_IDS:
            raise DataSourceCatalogError("只有两个内置副本数据源支持增量认领")
        return onboarding.start(source_id, job_type)

    @router.get("/data-source-management", dependencies=protected)
    def list_sources(
        search: str = "",
        database_type: str = "",
        status: str = "",
        enabled: bool | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return [
            _public_record(record)
            for record in catalog.list(
                search=search,
                database_type=database_type,
                status=status,
                enabled=enabled,
            )
        ]

    @router.post(
        "/data-source-management",
        status_code=201,
        dependencies=protected,
    )
    def create_source(body: CreateDataSourceRequest) -> dict[str, Any]:
        return _safe(
            lambda: _create_source_with_builtin_promotion(body).public_dict(
                detail=True
            )
        )

    def _create_source_with_builtin_promotion(
        body: CreateDataSourceRequest,
    ) -> Any:
        """新建数据源时识别内置数据库身份，命中则提升为内置副本并触发认领状态计算。"""
        builtin_id = _match_builtin_source(body.database_type, body.database_name)
        if builtin_id is None:
            return catalog.create(**body.model_dump())
        metadata_path, memory_path = _builtin_asset_paths(
            builtin_id, os.environ
        )
        record = catalog.create(
            **body.model_dump(),
            source_id=builtin_id,
            is_builtin=True,
            metadata_path=metadata_path,
            memory_path=memory_path,
        )
        catalog.initialize_builtin_claims(load_builtin_asset_lineage())
        return record

    @router.get(
        "/data-source-management/{source_id}",
        dependencies=protected,
    )
    def get_source(source_id: str) -> dict[str, Any]:
        return _safe(lambda: _public_record(catalog.require(source_id), detail=True))

    @router.patch(
        "/data-source-management/{source_id}",
        dependencies=protected,
    )
    def update_source(
        source_id: str,
        body: UpdateDataSourceRequest,
    ) -> dict[str, Any]:
        return _safe(
            lambda: _public_record(
                catalog.update(
                    source_id,
                    **body.model_dump(exclude_unset=True),
                ),
                detail=True,
            )
        )

    @router.post(
        "/data-source-management/{source_id}/test-connection",
        dependencies=protected,
    )
    def test_connection(source_id: str) -> dict[str, Any]:
        return _safe(lambda: connector.test_connection(source_id))

    @router.post(
        "/data-source-management/{source_id}/discover",
        dependencies=protected,
    )
    def discover(source_id: str) -> dict[str, Any]:
        items = _safe(lambda: connector.discover(source_id))
        return {"items": items, "count": len(items)}

    @router.post(
        "/data-source-management/{source_id}/analyze",
        status_code=202,
        dependencies=protected,
    )
    def analyze(source_id: str) -> dict[str, Any]:
        return _safe(lambda: onboarding.start(source_id, "analyze"))

    @router.post(
        "/data-source-management/{source_id}/review",
        status_code=202,
        dependencies=protected,
    )
    def review(source_id: str) -> dict[str, Any]:
        """阶段 A+B：只读重发现 + 画像 + 评分分组 -> reviews 建议字段，
        不修改 selected_scope 与正式资产。"""
        return _safe(lambda: onboarding.start(source_id, "review"))

    @router.get(
        "/data-source-management/{source_id}/analysis",
        dependencies=protected,
    )
    def analysis(source_id: str) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            profiles = catalog.list_table_profiles(source_id)
            return {
                "items": profiles,
                "count": len(profiles),
                "job": catalog.current_onboarding_job(source_id),
            }

        return _safe(load)

    @router.get(
        "/data-source-management/{source_id}/jobs/current",
        dependencies=protected,
    )
    def current_job(source_id: str) -> dict[str, Any]:
        return _safe(
            lambda: {"job": catalog.current_onboarding_job(source_id)}
        )

    @router.get(
        "/data-source-management/{source_id}/claim",
        dependencies=protected,
    )
    def claim_status(source_id: str) -> dict[str, Any]:
        return _safe(
            lambda: {"claim": catalog.builtin_claim_summary(source_id)}
        )

    @router.post(
        "/data-source-management/{source_id}/claim/preview",
        status_code=202,
        dependencies=protected,
    )
    def claim_preview(source_id: str) -> dict[str, Any]:
        return _safe(
            lambda: start_builtin_claim_job(source_id, "claim_preview")
        )

    @router.post(
        "/data-source-management/{source_id}/claim/publish",
        status_code=202,
        dependencies=protected,
    )
    def claim_publish(source_id: str) -> dict[str, Any]:
        return _safe(
            lambda: start_builtin_claim_job(source_id, "claim_publish")
        )

    @router.put(
        "/data-source-management/{source_id}/scope",
        dependencies=protected,
    )
    def save_scope(
        source_id: str,
        body: SaveScopeRequest,
    ) -> dict[str, Any]:
        return _safe(
            lambda: _public_record(
                catalog.save_scope(
                    source_id,
                    [
                        item.model_dump(by_alias=True)
                        for item in body.items
                    ],
                ),
                detail=True,
            )
        )

    @router.post(
        "/data-source-management/{source_id}/prepare",
        dependencies=protected,
    )
    def prepare(source_id: str) -> dict[str, Any]:
        return _safe(lambda: preparer.prepare(source_id))

    @router.post(
        "/data-source-management/{source_id}/activate",
        status_code=202,
        dependencies=protected,
    )
    def activate(source_id: str) -> dict[str, Any]:
        return _safe(lambda: onboarding.start(source_id, "activate"))

    @router.post(
        "/data-source-management/{source_id}/enable",
        dependencies=protected,
    )
    def enable(source_id: str) -> dict[str, Any]:
        return _safe(lambda: catalog.set_enabled(source_id, True).public_dict())

    @router.post(
        "/data-source-management/{source_id}/disable",
        dependencies=protected,
    )
    def disable(source_id: str) -> dict[str, Any]:
        result = _safe(
            lambda: catalog.set_enabled(source_id, False).public_dict()
        )
        runtime_manager.invalidate(source_id)
        return result

    @router.get(
        "/data-source-management/{source_id}/dependencies",
        dependencies=protected,
    )
    def dependencies(source_id: str) -> dict[str, Any]:
        return _safe(lambda: catalog.dependency_summary(source_id))

    @router.delete(
        "/data-source-management/{source_id}",
        status_code=204,
        dependencies=protected,
    )
    def delete(source_id: str, body: DeleteDataSourceRequest) -> None:
        if body.local_dependencies:
            raise HTTPException(
                status_code=409,
                detail="本地历史会话或仪表板仍依赖该数据源",
            )
        _safe(lambda: catalog.delete(source_id, body.confirmation))

    @router.post(
        "/conversations/{conversation_id}/source",
        dependencies=protected,
    )
    def bind_conversation(
        conversation_id: str,
        body: BindConversationRequest,
    ) -> dict[str, str]:
        bound_conversation_id, source_id = _safe(
            lambda: catalog.bind_conversation(
                conversation_id,
                body.source_id,
            )
        )
        return {
            "conversation_id": bound_conversation_id,
            "source_id": source_id,
        }

    @router.get(
        "/conversations/{conversation_id}/source",
        dependencies=protected,
    )
    def get_binding(conversation_id: str) -> dict[str, Any]:
        try:
            context = coordinator.require(conversation_id)
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail="会话尚未绑定数据源",
            ) from None
        record = catalog.require(context.source_id)
        return {
            "conversation_id": conversation_id,
            "source_id": record.source_id,
            "display_name": record.display_name,
            "database_type": record.database_type,
            "status": record.status,
            "enabled_for_chat": record.enabled_for_chat,
        }

    return router

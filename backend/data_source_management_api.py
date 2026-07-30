"""仅限本地主工作台的数据源管理与会话绑定 API。"""

from __future__ import annotations

import logging
import sqlite3
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
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator


logger = logging.getLogger(__name__)


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

    @router.get("/data-source-management", dependencies=protected)
    def list_sources(
        search: str = "",
        database_type: str = "",
        status: str = "",
        enabled: bool | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return [
            record.public_dict()
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
            lambda: catalog.create(**body.model_dump()).public_dict(detail=True)
        )

    @router.get(
        "/data-source-management/{source_id}",
        dependencies=protected,
    )
    def get_source(source_id: str) -> dict[str, Any]:
        return _safe(lambda: catalog.require(source_id).public_dict(detail=True))

    @router.patch(
        "/data-source-management/{source_id}",
        dependencies=protected,
    )
    def update_source(
        source_id: str,
        body: UpdateDataSourceRequest,
    ) -> dict[str, Any]:
        return _safe(
            lambda: catalog.update(
                source_id,
                **body.model_dump(exclude_unset=True),
            ).public_dict(detail=True)
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

    @router.put(
        "/data-source-management/{source_id}/scope",
        dependencies=protected,
    )
    def save_scope(
        source_id: str,
        body: SaveScopeRequest,
    ) -> dict[str, Any]:
        return _safe(
            lambda: catalog.save_scope(
                source_id,
                [
                    item.model_dump(by_alias=True)
                    for item in body.items
                ],
            ).public_dict(detail=True)
        )

    @router.post(
        "/data-source-management/{source_id}/prepare",
        dependencies=protected,
    )
    def prepare(source_id: str) -> dict[str, Any]:
        return _safe(lambda: preparer.prepare(source_id))

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

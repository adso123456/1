"""仅限本机访问的小助手管理 API。"""

from __future__ import annotations

import ipaddress
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, TypeVar
from urllib.parse import urlsplit

from backend.assistant_application_registry import (
    DEFAULT_FLOAT_ICON_DRAGGABLE,
    DEFAULT_FLOAT_ICON_URL,
    DEFAULT_FLOAT_X_ANCHOR,
    DEFAULT_FLOAT_X_OFFSET,
    DEFAULT_FLOAT_Y_ANCHOR,
    DEFAULT_FLOAT_Y_OFFSET,
    DEFAULT_HEADER_FONT_COLOR,
    DEFAULT_THEME,
    DEFAULT_WELCOME,
    DEFAULT_WELCOME_DESCRIPTION,
    ApplicationAlreadyExists,
    ApplicationNotFound,
    AssistantApplicationError,
    AssistantApplicationRegistry,
    InvalidApplicationConfiguration,
)
from backend.data_source_registry import DataSourceRegistry
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator


logger = logging.getLogger(__name__)
SAFE_INTERNAL_ERROR = "管理服务暂时不可用"
T = TypeVar("T")


class AssistantApplicationLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    link_id: str
    name: str
    url: str
    open_mode: str
    enabled: bool
    sort_order: int


class CreateAssistantApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    app_id: str
    name: str
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_source_ids: list[str] = Field(default_factory=list)
    application_links: list[AssistantApplicationLinkRequest] = Field(
        default_factory=list
    )
    token_ttl_seconds: int = 300
    theme: str = DEFAULT_THEME
    header_font_color: str = DEFAULT_HEADER_FONT_COLOR
    logo_url: str = ""
    welcome: str = DEFAULT_WELCOME
    welcome_description: str = DEFAULT_WELCOME_DESCRIPTION
    float_icon_url: str = DEFAULT_FLOAT_ICON_URL
    float_icon_draggable: bool = DEFAULT_FLOAT_ICON_DRAGGABLE
    float_x_anchor: str = DEFAULT_FLOAT_X_ANCHOR
    float_x_offset: int = DEFAULT_FLOAT_X_OFFSET
    float_y_anchor: str = DEFAULT_FLOAT_Y_ANCHOR
    float_y_offset: int = DEFAULT_FLOAT_Y_OFFSET
    show_history: bool = False
    enabled: bool = True


class UpdateAssistantApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    allowed_origins: list[str] | None = None
    allowed_source_ids: list[str] | None = None
    application_links: list[AssistantApplicationLinkRequest] | None = None
    token_ttl_seconds: int | None = None
    theme: str | None = None
    header_font_color: str | None = None
    logo_url: str | None = None
    welcome: str | None = None
    welcome_description: str | None = None
    float_icon_url: str | None = None
    float_icon_draggable: bool | None = None
    float_x_anchor: str | None = None
    float_x_offset: int | None = None
    float_y_anchor: str | None = None
    float_y_offset: int | None = None
    show_history: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if any(
            getattr(self, name) is None
            for name in self.model_fields_set
        ):
            raise ValueError("更新字段不能为 null")
        return self


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _exact_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Origin 格式无效") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "*" in value
        or value == "null"
    ):
        raise ValueError("Origin 格式无效")
    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    normalized = (
        f"{parsed.scheme}://{host}"
        f"{'' if port is None or default_port else f':{port}'}"
    )
    if value != normalized:
        raise ValueError("Origin 必须使用精确规范格式")
    return normalized


def _request_origin(request: Request) -> str:
    return _exact_origin(f"{request.url.scheme}://{request.url.netloc}")


def _authorize(
    request: Request,
    origin: str | None,
) -> None:
    if not _is_loopback(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="仅允许本机访问")
    if origin is not None:
        try:
            if _exact_origin(origin) != _request_origin(request):
                raise ValueError("Origin 不匹配")
        except ValueError:
            raise HTTPException(status_code=403, detail="Origin 不允许") from None


def _safe_call(action: Callable[[], T]) -> T:
    try:
        return action()
    except InvalidApplicationConfiguration as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ApplicationAlreadyExists:
        raise HTTPException(status_code=409, detail="应用已存在") from None
    except ApplicationNotFound:
        raise HTTPException(status_code=404, detail="应用不存在") from None
    except sqlite3.Error as exc:
        logger.error(
            "Assistant admin storage operation failed (%s)",
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail=SAFE_INTERNAL_ERROR) from None
    except AssistantApplicationError:
        raise HTTPException(status_code=500, detail=SAFE_INTERNAL_ERROR) from None
    except Exception as exc:
        logger.error(
            "Assistant admin operation failed (%s)",
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail=SAFE_INTERNAL_ERROR) from None


def _view_payload(view: Any) -> dict[str, Any]:
    return asdict(view)


def create_admin_router(
    *,
    application_registry: AssistantApplicationRegistry,
    data_source_registry: DataSourceRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    dependencies = [Depends(authorize)]

    @router.get("/data-sources", dependencies=dependencies)
    def list_data_sources() -> list[dict[str, Any]]:
        if data_source_registry.catalog is not None:
            return [
                record.safe_summary_dict()
                for record in data_source_registry.catalog.list()
            ]
        return [
            {
                "source_id": source_id,
                "database_type": data_source_registry.require(
                    source_id
                ).database_type,
                "display_name": data_source_registry.require(
                    source_id
                ).database_type,
            }
            for source_id in data_source_registry.source_ids
        ]

    @router.get("/assistant-applications", dependencies=dependencies)
    def list_applications() -> list[dict[str, Any]]:
        return [
            _view_payload(view)
            for view in _safe_call(application_registry.list)
        ]

    @router.post(
        "/assistant-applications",
        status_code=201,
        dependencies=dependencies,
    )
    def create_application(
        body: CreateAssistantApplicationRequest,
        response: Response,
    ) -> dict[str, Any]:
        created = _safe_call(
            lambda: application_registry.create(**body.model_dump())
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return {
            **_view_payload(created.application),
            "app_secret": created.app_secret,
        }

    @router.get(
        "/assistant-applications/{app_id}",
        dependencies=dependencies,
    )
    def get_application(app_id: str) -> dict[str, Any]:
        return _view_payload(
            _safe_call(lambda: application_registry.get(app_id))
        )

    @router.patch(
        "/assistant-applications/{app_id}",
        dependencies=dependencies,
    )
    def update_application(
        app_id: str,
        body: UpdateAssistantApplicationRequest,
    ) -> dict[str, Any]:
        values = body.model_dump(exclude_unset=True)
        return _view_payload(
            _safe_call(lambda: application_registry.update(app_id, **values))
        )

    @router.delete(
        "/assistant-applications/{app_id}",
        status_code=204,
        dependencies=dependencies,
    )
    def delete_application(app_id: str) -> Response:
        _safe_call(lambda: application_registry.delete(app_id))
        return Response(status_code=204)

    @router.post(
        "/assistant-applications/{app_id}/enable",
        dependencies=dependencies,
    )
    def enable_application(app_id: str) -> dict[str, Any]:
        return _view_payload(
            _safe_call(lambda: application_registry.enable(app_id))
        )

    @router.post(
        "/assistant-applications/{app_id}/disable",
        dependencies=dependencies,
    )
    def disable_application(app_id: str) -> dict[str, Any]:
        return _view_payload(
            _safe_call(lambda: application_registry.disable(app_id))
        )

    @router.post(
        "/assistant-applications/{app_id}/rotate-secret",
        dependencies=dependencies,
    )
    def rotate_secret(app_id: str, response: Response) -> dict[str, Any]:
        rotated = _safe_call(
            lambda: application_registry.rotate_secret(app_id)
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return {
            **_view_payload(rotated.application),
            "app_secret": rotated.app_secret,
        }

    return router

"""仅限本机访问的小助手管理 API。"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlsplit

from backend.assistant_application_registry import (
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
MIN_ADMIN_TOKEN_LENGTH = 32
SAFE_INTERNAL_ERROR = "管理服务暂时不可用"
T = TypeVar("T")


class AdminConfigurationError(RuntimeError):
    """管理员配置无效时阻止服务启动。"""


@dataclass(frozen=True)
class AdminSettings:
    enabled: bool = False
    token: str = field(default="", repr=False)


def load_admin_settings(
    environ: Mapping[str, str] | None = None,
) -> AdminSettings:
    import os

    values = environ if environ is not None else os.environ
    enabled = values.get("WATER_AGENT_ADMIN_ENABLED", "").strip().lower() == "true"
    token = values.get("WATER_AGENT_ADMIN_TOKEN", "")
    if enabled and len(token) < MIN_ADMIN_TOKEN_LENGTH:
        raise AdminConfigurationError(
            "管理员 API 已启用，但 WATER_AGENT_ADMIN_TOKEN 无效"
        )
    return AdminSettings(enabled=enabled, token=token if enabled else "")


class CreateAssistantApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    app_id: str
    name: str
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_source_ids: list[str] = Field(default_factory=list)
    token_ttl_seconds: int = 300
    theme: str = "#1677ff"
    logo_url: str = ""
    welcome: str = "有什么可以帮助你的？"
    welcome_description: str = (
        "用中文自然语言提问，Agent 自动查询数据库并返回图表"
    )
    show_history: bool = False
    enabled: bool = True


class UpdateAssistantApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    allowed_origins: list[str] | None = None
    allowed_source_ids: list[str] | None = None
    token_ttl_seconds: int | None = None
    theme: str | None = None
    logo_url: str | None = None
    welcome: str | None = None
    welcome_description: str | None = None
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
    settings: AdminSettings,
    authorization: str | None,
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
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="需要 Bearer 管理员令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ")
    if not token or not secrets.compare_digest(token, settings.token):
        raise HTTPException(
            status_code=401,
            detail="管理员令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


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
    settings: AdminSettings,
    application_registry: AssistantApplicationRegistry,
    data_source_registry: DataSourceRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin")

    def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, settings, authorization, origin)

    dependencies = [Depends(authorize)]

    @router.get("/data-sources", dependencies=dependencies)
    def list_data_sources() -> list[dict[str, str]]:
        return [
            {
                "source_id": source_id,
                "database_type": data_source_registry.require(
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

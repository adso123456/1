"""嵌入应用短期 JWT 配置、签发与验证。"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import jwt
from backend.assistant_application_registry import (
    ApplicationDisabled,
    ApplicationNotFound,
    AssistantApplicationRegistry,
    AssistantApplicationView,
    InvalidApplicationConfiguration,
    validate_app_id,
)

EMBED_AUDIENCE = "water-agent-embed"
ADMIN_PREVIEW_AUDIENCE = "water-agent-admin-preview"
ADMIN_PREVIEW_TOKEN_USE = "admin-preview"
ADMIN_PREVIEW_SUBJECT = "local-admin-preview"
MAX_ADMIN_PREVIEW_TTL_SECONDS = 120
ADMIN_PREVIEW_KEY_CONTEXT = b"water-agent-admin-preview-v1"
EMBED_ALGORITHM = "HS256"
DEFAULT_TOKEN_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 30


@dataclass(frozen=True)
class EmbedApplicationConfig:
    app_id: str
    app_secret: str = field(repr=False)
    enabled: bool
    allowed_origins: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS


@dataclass(frozen=True)
class EmbedPrincipal:
    app_id: str
    subject: str
    parent_origin: str
    allowed_source_ids: tuple[str, ...]
    expires_at: int
    token_id: str


class EmbedAccessError(Exception):
    """可安全映射为 HTTP 状态码的嵌入访问拒绝。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.safe_message = message


def derive_admin_preview_signing_key(admin_token: str) -> bytes:
    """以固定上下文从管理员 Token 派生独立预览签名密钥。"""

    return hmac.new(
        admin_token.encode("utf-8"),
        ADMIN_PREVIEW_KEY_CONTEXT,
        hashlib.sha256,
    ).digest()


def is_loopback_origin(value: str) -> bool:
    """只接受精确规范的 localhost 或 IP loopback Origin。"""

    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    hostname = parsed.hostname
    if hostname == "localhost":
        loopback = True
    else:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return False
        loopback = address.is_loopback
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (
        parsed.scheme == "http" and port == 80
    ) or (
        parsed.scheme == "https" and port == 443
    )
    normalized = (
        f"{parsed.scheme}://{host}"
        f"{'' if port is None or default_port else f':{port}'}"
    )
    return loopback and value == normalized


def issue_admin_preview_token(
    *,
    admin_token: str,
    application: AssistantApplicationView,
    parent_origin: str,
    allowed_source_ids: Sequence[str],
    now: int | None = None,
) -> tuple[str, int]:
    """签发仅限本机管理预览使用的短期 Token。"""

    if not application.enabled:
        raise EmbedAccessError(403, "嵌入应用已禁用")
    if not is_loopback_origin(parent_origin):
        raise EmbedAccessError(403, "管理预览 Origin 不允许")
    effective_sources = tuple(dict.fromkeys(allowed_source_ids))
    if not effective_sources:
        raise EmbedAccessError(409, "应用没有当前有效数据源")
    if any(
        source_id not in application.allowed_source_ids
        for source_id in effective_sources
    ):
        raise EmbedAccessError(403, "数据源未获授权")
    issued_at = int(time.time()) if now is None else int(now)
    ttl = min(
        application.token_ttl_seconds,
        MAX_ADMIN_PREVIEW_TTL_SECONDS,
    )
    expires_at = issued_at + ttl
    payload = {
        "aud": ADMIN_PREVIEW_AUDIENCE,
        "token_use": ADMIN_PREVIEW_TOKEN_USE,
        "app_id": application.app_id,
        "sub": ADMIN_PREVIEW_SUBJECT,
        "parent_origin": parent_origin,
        "allowed_source_ids": list(effective_sources),
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload,
        derive_admin_preview_signing_key(admin_token),
        algorithm=EMBED_ALGORITHM,
    )
    return token, expires_at


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def load_embed_application_config(
    environ: Mapping[str, str] | None = None,
) -> EmbedApplicationConfig | None:
    """从环境变量读取单个嵌入应用；完全未配置时返回 None。"""

    values = os.environ if environ is None else environ
    names = {
        "app_id": "WATER_AGENT_EMBED_APP_ID",
        "app_secret": "WATER_AGENT_EMBED_APP_SECRET",
        "enabled": "WATER_AGENT_EMBED_ENABLED",
        "allowed_origins": "WATER_AGENT_EMBED_ALLOWED_ORIGINS",
        "allowed_source_ids": "WATER_AGENT_EMBED_ALLOWED_SOURCE_IDS",
        "ttl": "WATER_AGENT_EMBED_TOKEN_TTL_SECONDS",
    }
    configured = any(values.get(name, "").strip() for name in names.values())
    if not configured:
        return None

    app_id = values.get(names["app_id"], "").strip()
    secret = values.get(names["app_secret"], "").strip()
    origins = _split_csv(values.get(names["allowed_origins"], ""))
    source_ids = _split_csv(values.get(names["allowed_source_ids"], ""))
    if not app_id or not secret or not origins or not source_ids:
        raise ValueError("嵌入应用配置缺少 app_id、密钥、Origin 或数据源")
    if len(secret) < 32:
        raise ValueError("嵌入应用密钥至少需要 32 个字符")

    enabled_value = values.get(names["enabled"], "true")
    enabled = _parse_bool(enabled_value, name=names["enabled"])
    try:
        ttl = int(values.get(names["ttl"], str(DEFAULT_TOKEN_TTL_SECONDS)))
    except ValueError as exc:
        raise ValueError("嵌入 Token 有效期必须是整数") from exc
    if ttl < 30 or ttl > 3600:
        raise ValueError("嵌入 Token 有效期必须在 30 到 3600 秒之间")

    return EmbedApplicationConfig(
        app_id=app_id,
        app_secret=secret,
        enabled=enabled,
        allowed_origins=origins,
        allowed_source_ids=source_ids,
        token_ttl_seconds=ttl,
    )


def issue_embed_token(
    config: EmbedApplicationConfig,
    *,
    subject: str,
    parent_origin: str | None = None,
    allowed_source_ids: Sequence[str] | None = None,
    now: int | None = None,
) -> tuple[str, int]:
    """为已配置应用签发短期 HS256 Token。"""

    if not config.enabled:
        raise EmbedAccessError(403, "嵌入应用已禁用")
    origin = parent_origin or config.allowed_origins[0]
    if origin not in config.allowed_origins:
        raise EmbedAccessError(403, "父页面 Origin 未获授权")
    requested_sources = tuple(
        config.allowed_source_ids
        if allowed_source_ids is None
        else allowed_source_ids
    )
    if any(source_id not in config.allowed_source_ids for source_id in requested_sources):
        raise EmbedAccessError(403, "数据源未获授权")

    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at + config.token_ttl_seconds
    payload = {
        "aud": EMBED_AUDIENCE,
        "app_id": config.app_id,
        "sub": subject,
        "parent_origin": origin,
        "allowed_source_ids": list(requested_sources),
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, config.app_secret, algorithm=EMBED_ALGORITHM)
    return token, expires_at


def verify_embed_token(
    token: str,
    *,
    parent_origin: str,
    registry: AssistantApplicationRegistry | None,
    source_id: str | None = None,
    now: int | None = None,
) -> EmbedPrincipal:
    """验证签名、应用、Origin、时效和可选数据源权限。"""

    if registry is None:
        raise EmbedAccessError(503, "嵌入应用注册表尚未配置")
    if not token:
        raise EmbedAccessError(401, "缺少嵌入访问 Token")

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != EMBED_ALGORITHM:
            raise EmbedAccessError(401, "Token 算法不受支持")
        unverified = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=[EMBED_ALGORITHM],
        )
        unverified_app_id = unverified.get("app_id")
        try:
            candidate_app_id = validate_app_id(unverified_app_id)
        except InvalidApplicationConfiguration:
            raise EmbedAccessError(401, "Token 应用无效")
        try:
            application = registry.require_for_token_verification(
                candidate_app_id
            )
        except ApplicationNotFound:
            raise EmbedAccessError(401, "Token 应用无效") from None
        except ApplicationDisabled:
            raise EmbedAccessError(403, "嵌入应用已禁用") from None
        claims = jwt.decode(
            token,
            application.app_secret,
            algorithms=[EMBED_ALGORITHM],
            audience=EMBED_AUDIENCE,
            options={
                "require": [
                    "aud",
                    "app_id",
                    "sub",
                    "parent_origin",
                    "allowed_source_ids",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
            leeway=MAX_CLOCK_SKEW_SECONDS,
        )
    except EmbedAccessError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise EmbedAccessError(401, "嵌入访问 Token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise EmbedAccessError(401, "嵌入访问 Token 无效") from exc

    token_origin = claims.get("parent_origin")
    if (
        not isinstance(token_origin, str)
        or token_origin != parent_origin
        or token_origin not in application.allowed_origins
    ):
        raise EmbedAccessError(403, "父页面 Origin 未获授权")

    if claims.get("app_id") != application.app_id:
        raise EmbedAccessError(401, "Token 应用无效")
    token_sources = claims.get("allowed_source_ids")
    if (
        not isinstance(token_sources, list)
        or any(not isinstance(item, str) for item in token_sources)
    ):
        raise EmbedAccessError(401, "Token 数据源权限无效")
    if any(
        item not in application.allowed_source_ids for item in token_sources
    ):
        raise EmbedAccessError(403, "Token 数据源权限超出应用配置")
    effective_sources = tuple(dict.fromkeys(token_sources))
    if source_id is not None and source_id not in effective_sources:
        raise EmbedAccessError(403, "数据源未获授权")

    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    current_time = int(time.time()) if now is None else int(now)
    if (
        not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or issued_at > current_time + MAX_CLOCK_SKEW_SECONDS
        or expires_at <= issued_at
        or expires_at - issued_at > application.token_ttl_seconds
    ):
        raise EmbedAccessError(401, "Token 时间声明无效")

    subject = claims.get("sub")
    token_id = claims.get("jti")
    if not isinstance(subject, str) or not subject:
        raise EmbedAccessError(401, "Token 用户声明无效")
    if not isinstance(token_id, str) or not token_id:
        raise EmbedAccessError(401, "Token 标识无效")

    return EmbedPrincipal(
        app_id=application.app_id,
        subject=subject,
        parent_origin=token_origin,
        allowed_source_ids=effective_sources,
        expires_at=expires_at,
        token_id=token_id,
    )


def verify_admin_preview_token(
    token: str,
    *,
    admin_token: str,
    parent_origin: str,
    registry: AssistantApplicationRegistry | None,
    current_source_ids: Sequence[str],
    source_id: str | None = None,
    now: int | None = None,
) -> EmbedPrincipal:
    """验证独立签名的本机管理预览 Token。"""

    if registry is None:
        raise EmbedAccessError(503, "嵌入应用注册表尚未配置")
    if not token:
        raise EmbedAccessError(401, "缺少嵌入访问 Token")
    if not is_loopback_origin(parent_origin):
        raise EmbedAccessError(403, "管理预览 Origin 不允许")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != EMBED_ALGORITHM:
            raise EmbedAccessError(401, "Token 算法不受支持")
        unverified = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=[EMBED_ALGORITHM],
        )
        if unverified.get("aud") != ADMIN_PREVIEW_AUDIENCE:
            raise EmbedAccessError(401, "Token audience 无效")
        try:
            app_id = validate_app_id(unverified.get("app_id"))
        except InvalidApplicationConfiguration:
            raise EmbedAccessError(401, "Token 应用无效")
        try:
            application = registry.require_for_token_verification(app_id)
        except ApplicationNotFound:
            raise EmbedAccessError(401, "Token 应用无效") from None
        except ApplicationDisabled:
            raise EmbedAccessError(403, "嵌入应用已禁用") from None
        claims = jwt.decode(
            token,
            derive_admin_preview_signing_key(admin_token),
            algorithms=[EMBED_ALGORITHM],
            audience=ADMIN_PREVIEW_AUDIENCE,
            options={
                "require": [
                    "aud",
                    "token_use",
                    "app_id",
                    "sub",
                    "parent_origin",
                    "allowed_source_ids",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
            leeway=MAX_CLOCK_SKEW_SECONDS,
        )
    except EmbedAccessError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise EmbedAccessError(401, "嵌入访问 Token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise EmbedAccessError(401, "嵌入访问 Token 无效") from exc

    if (
        claims.get("token_use") != ADMIN_PREVIEW_TOKEN_USE
        or claims.get("sub") != ADMIN_PREVIEW_SUBJECT
        or claims.get("app_id") != application.app_id
        or claims.get("parent_origin") != parent_origin
    ):
        raise EmbedAccessError(401, "管理预览 Token 声明无效")
    token_sources = claims.get("allowed_source_ids")
    if (
        not isinstance(token_sources, list)
        or any(not isinstance(item, str) for item in token_sources)
    ):
        raise EmbedAccessError(401, "Token 数据源权限无效")
    current_sources = set(current_source_ids)
    if any(
        item not in application.allowed_source_ids
        or item not in current_sources
        for item in token_sources
    ):
        raise EmbedAccessError(403, "Token 数据源权限超出应用配置")
    effective_sources = tuple(dict.fromkeys(token_sources))
    if not effective_sources:
        raise EmbedAccessError(403, "Token 没有有效数据源权限")
    if source_id is not None and source_id not in effective_sources:
        raise EmbedAccessError(403, "数据源未获授权")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    current_time = int(time.time()) if now is None else int(now)
    if (
        not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or issued_at > current_time + MAX_CLOCK_SKEW_SECONDS
        or expires_at <= issued_at
        or expires_at - issued_at
        > min(
            application.token_ttl_seconds,
            MAX_ADMIN_PREVIEW_TTL_SECONDS,
        )
    ):
        raise EmbedAccessError(401, "Token 时间声明无效")
    token_id = claims.get("jti")
    if not isinstance(token_id, str) or not token_id:
        raise EmbedAccessError(401, "Token 标识无效")
    return EmbedPrincipal(
        app_id=application.app_id,
        subject=ADMIN_PREVIEW_SUBJECT,
        parent_origin=parent_origin,
        allowed_source_ids=effective_sources,
        expires_at=expires_at,
        token_id=token_id,
    )


def verify_embed_access_token(
    token: str,
    *,
    parent_origin: str,
    registry: AssistantApplicationRegistry | None,
    source_id: str | None = None,
    allow_admin_preview: bool = False,
    admin_preview_denial_status: int = 401,
    admin_token: str = "",
    current_source_ids: Sequence[str] = (),
    now: int | None = None,
) -> EmbedPrincipal:
    """按精确 audience 分派普通或管理预览 Token。"""

    try:
        unverified = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=[EMBED_ALGORITHM],
        )
    except jwt.InvalidTokenError as exc:
        raise EmbedAccessError(401, "嵌入访问 Token 无效") from exc
    audience = unverified.get("aud")
    if not isinstance(audience, str):
        raise EmbedAccessError(401, "Token audience 无效")
    if audience == EMBED_AUDIENCE:
        return verify_embed_token(
            token,
            parent_origin=parent_origin,
            registry=registry,
            source_id=source_id,
            now=now,
        )
    if audience == ADMIN_PREVIEW_AUDIENCE:
        if not allow_admin_preview or not admin_token:
            raise EmbedAccessError(
                admin_preview_denial_status,
                "管理预览 Token 不允许",
            )
        return verify_admin_preview_token(
            token,
            admin_token=admin_token,
            parent_origin=parent_origin,
            registry=registry,
            current_source_ids=current_source_ids,
            source_id=source_id,
            now=now,
        )
    raise EmbedAccessError(401, "Token audience 无效")


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise EmbedAccessError(401, "缺少嵌入访问 Token")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise EmbedAccessError(401, "嵌入访问 Token 格式无效")
    return token.strip()

"""嵌入应用 Origin 校验与授权。

不依赖 Token、Secret 或 JWT。每次嵌入请求都重新校验：
- 浏览器自动发送的真实 Origin 请求头
- 应用 app_id
- 应用 enabled 状态
- allowed_origins 白名单
- allowed_source_ids 白名单
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.assistant_application_registry import (
    ApplicationDisabled,
    ApplicationNotFound,
    AssistantApplication,
    AssistantApplicationRegistry,
    InvalidApplicationConfiguration,
    normalize_origin,
    validate_app_id,
)


@dataclass(frozen=True)
class EmbedOriginPrincipal:
    """由真实 Origin 请求头解析出的嵌入主体。"""

    app_id: str
    parent_origin: str
    application: AssistantApplication = field(repr=False)


class EmbedAccessError(Exception):
    """可安全映射为 HTTP 状态码的嵌入访问拒绝。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.safe_message = message


def authorize_embed_origin(
    *,
    app_id: str,
    origin: str | None,
    registry: AssistantApplicationRegistry | None,
    source_id: str | None = None,
) -> EmbedOriginPrincipal:
    """根据浏览器真实 Origin 请求头校验嵌入访问。

    校验顺序：
    1. registry 可用
    2. app_id 格式合法
    3. Origin 存在且格式合法
    4. 应用存在且未禁用
    5. Origin 在 allowed_origins 白名单中
    6. （可选）source_id 在 allowed_source_ids 白名单中

    不依赖客户端提交的父页面标识或其他凭据。
    """
    if registry is None:
        raise EmbedAccessError(503, "嵌入应用注册表尚未配置")

    try:
        safe_app_id = validate_app_id(app_id)
    except InvalidApplicationConfiguration:
        raise EmbedAccessError(401, "app_id 无效")

    if not origin or not origin.strip():
        raise EmbedAccessError(401, "浏览器 Origin 请求头缺失，嵌入请求必须携带 Origin")

    try:
        safe_origin = normalize_origin(origin)
    except InvalidApplicationConfiguration:
        raise EmbedAccessError(403, "浏览器 Origin 格式无效")

    try:
        application = registry.require_origin_verification(safe_app_id)
    except ApplicationNotFound:
        raise EmbedAccessError(401, "未知 app_id") from None
    except ApplicationDisabled:
        raise EmbedAccessError(403, "嵌入应用已禁用") from None

    if safe_origin not in application.allowed_origins:
        raise EmbedAccessError(403, "当前页面 Origin 未获授权")

    if source_id is not None:
        if source_id not in application.allowed_source_ids:
            raise EmbedAccessError(403, "数据源未获授权")

    return EmbedOriginPrincipal(
        app_id=application.app_id,
        parent_origin=safe_origin,
        application=application,
    )


def extract_app_id_from_request(
    *,
    metadata: Any,
    path_app_id: str | None = None,
) -> str:
    """从请求中提取 app_id。

    优先级：
    1. URL 路径参数 (path_app_id)
    2. 请求体 metadata.app_id
    """
    if path_app_id and isinstance(path_app_id, str) and path_app_id.strip():
        return path_app_id.strip()
    if isinstance(metadata, dict):
        app_id = metadata.get("app_id")
        if isinstance(app_id, str) and app_id.strip():
            return app_id.strip()
    raise EmbedAccessError(400, "缺少 app_id")

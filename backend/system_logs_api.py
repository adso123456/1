"""受保护的管理 API：最近系统日志（设置页终端窗口）。"""

from __future__ import annotations

from typing import Any

from backend.assistant_admin_api import _authorize
from backend.log_buffer import recent_logs
from fastapi import APIRouter, Depends, Header, Query, Request


def create_system_logs_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    @router.get("/admin/system/logs", dependencies=protected)
    def get_logs(
        level: str = Query(default="INFO"),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        return recent_logs(level=level, limit=limit)

    return router

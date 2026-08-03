"""数据源专属推荐问题的在线读取 API。

`GET /api/conversations/{conversation_id}/suggested-questions`
- 以服务端会话绑定为准解析 source_id，绝不信任前端传入的 source_id；
- 未绑定会话返回 404（明确安全响应）；
- 数据源不可用或资产缺失/损坏/不匹配时返回空列表，绝不跨源补齐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.assistant_admin_api import _authorize
from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_request_coordinator import (
    DataSourceRequestCoordinator,
)
from backend.question_suggestion_assets import (
    load_question_directory,
    select_suggested_questions,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Request


def create_question_suggestion_router(
    catalog: DataSourceCatalog,
    coordinator: DataSourceRequestCoordinator,
    *,
    asset_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    @router.get(
        "/conversations/{conversation_id}/suggested-questions",
        dependencies=protected,
    )
    def suggested_questions(conversation_id: str) -> dict[str, Any]:
        try:
            context = coordinator.require(conversation_id)
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail="会话尚未绑定数据源",
            ) from None
        try:
            record = catalog.require(context.source_id)
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
        directory = load_question_directory(
            context.source_id,
            root=asset_root,
        )
        if directory is None:
            return {
                "source_id": context.source_id,
                "asset_version": None,
                "questions": [],
            }
        questions = select_suggested_questions(directory, conversation_id)
        return {
            "source_id": context.source_id,
            "asset_version": directory["asset_version"],
            "questions": questions,
        }

    return router

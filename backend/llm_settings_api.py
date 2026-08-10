"""受保护的管理 API：查看 / 更新运行时 LLM 配置。"""

from __future__ import annotations

import os
import time
from typing import Any

from backend.assistant_admin_api import _authorize
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.llm_settings import (
    LLMSettingsError,
    save_llm_settings,
    settings_view,
)
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field


class UpdateLLMSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)
    base_url: str | None = Field(default=None)


def create_llm_settings_router(
    runtime_manager: DataSourceRuntimeManager,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    @router.get("/admin/llm-settings", dependencies=protected)
    def get_settings() -> dict[str, Any]:
        return settings_view()

    @router.put("/admin/llm-settings", dependencies=protected)
    def update_settings(body: UpdateLLMSettingsRequest) -> dict[str, Any]:
        try:
            view = save_llm_settings(
                api_key=body.api_key,
                model=body.model,
                base_url=body.base_url,
            )
        except LLMSettingsError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # 失效全部运行时代理，下一次问数按新 Key / 模型 / Base URL 重建。
        for source_id in runtime_manager.source_ids:
            runtime_manager.invalidate(source_id)
        return view

    @router.post("/admin/llm-settings/test", dependencies=protected)
    async def test_llm_settings() -> dict[str, Any]:
        """用当前生效配置发一个最小请求，验证 Key / 模型 / Base URL 连通性。"""
        from fastapi import HTTPException

        from backend.llm_settings import settings_view
        from backend.tracing_llm_service import TracingOpenAILlmService
        from config.performance_settings import QueryPerformanceSettings
        from vanna.core.llm.models import LlmMessage, LlmRequest
        from vanna.core.user import User

        view = settings_view()
        if not view["api_key_set"]:
            raise HTTPException(status_code=400, detail="尚未配置 API Key")
        llm = TracingOpenAILlmService(
            model=view["model"],
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=view["base_url"],
            settings=QueryPerformanceSettings.from_environment(),
        )
        request = LlmRequest(
            messages=[LlmMessage(role="user", content="ping")],
            user=User(
                id="llm-settings-test",
                username="llm-settings-test",
                metadata={"tool": "llm_settings_test"},
            ),
            stream=False,
            temperature=0.0,
        )
        started = time.monotonic()
        try:
            response = await llm.send_request(request)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            ok = bool(response and (response.content or "").strip())
            return {
                "ok": ok,
                "model": view["model"],
                "base_url": view["base_url"],
                "latency_ms": latency_ms,
                "error": "" if ok else "模型返回空内容",
            }
        except Exception as exc:
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            return {
                "ok": False,
                "model": view["model"],
                "base_url": view["base_url"],
                "latency_ms": latency_ms,
                "error": f"{type(exc).__name__}: {exc}",
            }

    return router

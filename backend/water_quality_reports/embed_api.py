"""Embed water-quality report routes.

These routes share the same ``ReportApplicationService`` as the ordinary
workbench router but enforce Embed authorization -- app registration, the
browser Origin and the ``mysql-lzh-monitor`` source -- before any report
artifact store operation.  Unauthorized requests never reach options,
generate, load, pdf_path or render.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from backend.water_quality_reports.api import GenerateReportRequest
from backend.water_quality_reports.application_service import (
    ReportApplicationService,
    ReportArtifactNotFoundError,
    ReportArtifactOperationError,
    ReportParameterError,
)


REPORT_SOURCE_ID = "mysql-lzh-monitor"

# authorize(app_id, origin, source_id) -> None; raises HTTPException on failure.
AuthorizeCallable = Callable[[str, str | None, str | None], None]


def create_embed_report_router(
    *,
    service: ReportApplicationService | None,
    authorize: AuthorizeCallable,
) -> APIRouter:
    """Build the Embed report routes around a shared application service.

    ``authorize`` is injected by the server so the embed auth policy stays in
    the HTTP layer.  A ``None`` service means the mysql data source is absent;
    every route then answers 503 without touching authorization.
    """
    router = APIRouter()

    def require_service(app_id: str, request: Request) -> ReportApplicationService:
        authorize(app_id, request.headers.get("Origin"), REPORT_SOURCE_ID)
        if service is None:
            raise HTTPException(status_code=503, detail="报告服务当前不可用")
        return service

    @router.get("/api/embed/apps/{app_id}/reports/options")
    def get_embed_report_options(app_id: str, request: Request):
        require_service(app_id, request)
        try:
            return service.options()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="报表筛选项加载失败",
            ) from None

    @router.post("/api/embed/apps/{app_id}/reports/generate")
    def generate_embed_report(
        app_id: str,
        payload: GenerateReportRequest,
        request: Request,
    ):
        require_service(app_id, request)
        try:
            period = service.validate_generate_request(
                payload.report_type,
                payload.date,
                payload.month,
            )
            codes = service.normalize_indicators(payload.indicators)
            overrides = service.normalize_frequency_hours(
                payload.frequency_hours
            )
            return service.generate(
                report_type=payload.report_type,
                period=period,
                indicator_codes=codes,
                frequency_overrides=overrides,
                recent_days=payload.recent_days,
            )
        except ReportParameterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportArtifactOperationError:
            raise HTTPException(
                status_code=422,
                detail="报表生成失败，请检查报表参数",
            ) from None
        # Data-source and ValueError propagate to the default 500 handler,
        # matching the pre-refactor Embed contract.

    @router.get(
        "/api/embed/apps/{app_id}/reports/artifacts/{report_id}/preview",
        response_class=HTMLResponse,
    )
    def preview_embed_report(app_id: str, report_id: str, request: Request):
        require_service(app_id, request)
        try:
            return HTMLResponse(service.preview_html(report_id))
        except ReportArtifactNotFoundError:
            raise HTTPException(status_code=404, detail="报告不存在") from None
        except ReportArtifactOperationError:
            raise HTTPException(
                status_code=500,
                detail="报告预览加载失败",
            ) from None

    @router.get(
        "/api/embed/apps/{app_id}/reports/artifacts/{report_id}/pdf"
    )
    def download_embed_report(app_id: str, report_id: str, request: Request):
        require_service(app_id, request)
        try:
            result = service.pdf(report_id)
        except ReportArtifactNotFoundError:
            raise HTTPException(status_code=404, detail="报告不存在") from None
        except ReportArtifactOperationError:
            raise HTTPException(
                status_code=500,
                detail="报告文件生成失败",
            ) from None
        return FileResponse(
            result.path,
            media_type="application/pdf",
            filename=result.filename,
        )

    return router

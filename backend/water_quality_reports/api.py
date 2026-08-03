"""水质日报、月报 FastAPI 路由（普通工作台）。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from backend.water_quality_reports.application_service import (
    ReportApplicationService,
    ReportArtifactNotFoundError,
    ReportArtifactOperationError,
    ReportDataSourceError,
    ReportParameterError,
    ReportPdfRenderError,
    parse_date,
    parse_month,
)
from backend.water_quality_reports.artifacts import ReportArtifactStore
from backend.water_quality_reports.pdf_renderer import (
    WaterQualityPdfRenderer,
)
from backend.water_quality_reports.repository import (
    ReportDataSourceError as RepositoryDataSourceError,
)
from backend.water_quality_reports.service import WaterQualityReportService
from backend.water_quality_reports.template import render_report_html


logger = logging.getLogger(__name__)
ServiceFactory = Callable[[], WaterQualityReportService]


class GenerateReportRequest(BaseModel):
    report_type: Literal["daily", "monthly"]
    date: str | None = None
    month: str | None = None
    indicators: list[int] = Field(min_length=1)
    frequency_hours: dict[str, int] = Field(default_factory=dict)
    recent_days: int = Field(default=3, ge=2, le=7)


def _parse_date(value: str) -> date:
    """Backward-compatible wrapper; parsing lives in the shared layer."""
    try:
        return parse_date(value)
    except ReportParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _parse_month(value: str) -> date:
    """Backward-compatible wrapper; parsing lives in the shared layer."""
    try:
        return parse_month(value)
    except ReportParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _parse_indicator_codes(value: str | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    try:
        codes = tuple(dict.fromkeys(int(item) for item in value.split(",") if item))
    except ValueError:
        raise HTTPException(status_code=422, detail="指标参数无效") from None
    if not codes or any(code < 0 for code in codes):
        raise HTTPException(status_code=422, detail="至少选择一个有效指标")
    return codes


def _parse_frequency_overrides(value: str | None) -> dict[int, int]:
    if value is None or value == "":
        return {}
    result: dict[int, int] = {}
    try:
        for item in value.split(","):
            code_text, hours_text = item.split(":", maxsplit=1)
            code, hours = int(code_text), int(hours_text)
            if code < 0 or hours < 1:
                raise ValueError
            result[code] = hours
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="频次参数必须为“指标编号:小时数”",
        ) from None
    return result


def create_report_router(
    service_factory: ServiceFactory,
    *,
    renderer: WaterQualityPdfRenderer | None = None,
    artifact_store: ReportArtifactStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/reports/water-quality", tags=["water-quality-reports"])
    renderer = renderer or WaterQualityPdfRenderer()
    artifacts = artifact_store or ReportArtifactStore(service_factory, renderer)
    reports = ReportApplicationService(artifacts, renderer)

    def daily_report(
        value: str,
        indicators: str | None,
        frequency_hours: str | None,
        recent_days: int,
    ):
        service = service_factory()
        try:
            report = service.daily(
                _parse_date(value),
                indicator_codes=_parse_indicator_codes(indicators),
                frequency_overrides=_parse_frequency_overrides(frequency_hours),
                recent_days=recent_days,
            )
            logger.info("Daily report query timings: %s", service.repository.query_timings)
            return report
        except (RepositoryDataSourceError, ValueError) as exc:
            status_code = 503 if isinstance(exc, RepositoryDataSourceError) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from None

    def monthly_report(
        value: str,
        indicators: str | None,
        frequency_hours: str | None,
    ):
        service = service_factory()
        try:
            report = service.monthly(
                _parse_month(value),
                indicator_codes=_parse_indicator_codes(indicators),
                frequency_overrides=_parse_frequency_overrides(frequency_hours),
            )
            logger.info("Monthly report query timings: %s", service.repository.query_timings)
            return report
        except (RepositoryDataSourceError, ValueError) as exc:
            status_code = 503 if isinstance(exc, RepositoryDataSourceError) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from None

    @router.get("/options")
    def get_options():
        try:
            return reports.options()
        except ReportDataSourceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @router.post("/generate")
    def generate_report(request: GenerateReportRequest):
        try:
            period = reports.validate_generate_request(
                request.report_type,
                request.date,
                request.month,
            )
            codes = reports.normalize_indicators(request.indicators)
            if not codes or any(code < 0 for code in codes):
                raise ValueError("至少选择一个有效指标")
            overrides = reports.normalize_frequency_hours(request.frequency_hours)
            return reports.generate(
                report_type=request.report_type,
                period=period,
                indicator_codes=codes,
                frequency_overrides=overrides,
                recent_days=request.recent_days,
            )
        except ReportParameterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ReportDataSourceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ReportArtifactOperationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.get("/artifacts/{report_id}/preview", response_class=HTMLResponse)
    def preview_artifact(report_id: str):
        try:
            return HTMLResponse(reports.preview_html(report_id))
        except ReportArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ReportArtifactOperationError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    @router.get("/artifacts/{report_id}/pdf")
    def download_artifact(report_id: str):
        try:
            result = reports.pdf(report_id)
        except ReportArtifactNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ReportArtifactOperationError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None
        return FileResponse(
            result.path,
            media_type="application/pdf",
            filename=result.filename,
        )

    @router.get("/daily")
    def get_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=2, le=7),
    ):
        return daily_report(date_value, indicators, frequency_hours, recent_days)

    @router.get("/daily/preview", response_class=HTMLResponse)
    def preview_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=2, le=7),
    ):
        report = daily_report(date_value, indicators, frequency_hours, recent_days)
        return HTMLResponse(render_report_html(report))

    @router.get("/daily/pdf")
    def download_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=2, le=7),
    ):
        report = daily_report(date_value, indicators, frequency_hours, recent_days)
        try:
            path = reports.render_pdf(report)
        except ReportPdfRenderError:
            raise HTTPException(status_code=500, detail="PDF 生成失败") from None
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
        )

    @router.get("/monthly")
    def get_monthly(
        month_value: str = Query(alias="month"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
    ):
        return monthly_report(month_value, indicators, frequency_hours)

    @router.get("/monthly/preview", response_class=HTMLResponse)
    def preview_monthly(
        month_value: str = Query(alias="month"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
    ):
        report = monthly_report(month_value, indicators, frequency_hours)
        return HTMLResponse(render_report_html(report))

    @router.get("/monthly/pdf")
    def download_monthly(
        month_value: str = Query(alias="month"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
    ):
        report = monthly_report(month_value, indicators, frequency_hours)
        try:
            path = reports.render_pdf(report)
        except ReportPdfRenderError:
            raise HTTPException(status_code=500, detail="PDF 生成失败") from None
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
        )

    return router

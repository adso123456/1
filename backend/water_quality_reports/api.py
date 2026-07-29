"""水质日报、月报 FastAPI 路由。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from backend.water_quality_reports.pdf_renderer import (
    PdfRenderError,
    WaterQualityPdfRenderer,
)
from backend.water_quality_reports.repository import ReportDataSourceError
from backend.water_quality_reports.service import WaterQualityReportService
from backend.water_quality_reports.template import render_report_html


logger = logging.getLogger(__name__)
ServiceFactory = Callable[[], WaterQualityReportService]


def _parse_date(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise HTTPException(status_code=422, detail="日期必须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="日期无效") from None


def _parse_month(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}", value) is None:
        raise HTTPException(status_code=422, detail="月份必须为 YYYY-MM")
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(status_code=422, detail="月份无效") from None


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
) -> APIRouter:
    router = APIRouter(prefix="/api/reports/water-quality", tags=["water-quality-reports"])
    renderer = renderer or WaterQualityPdfRenderer()

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
        except (ReportDataSourceError, ValueError) as exc:
            status_code = 503 if isinstance(exc, ReportDataSourceError) else 422
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
        except (ReportDataSourceError, ValueError) as exc:
            status_code = 503 if isinstance(exc, ReportDataSourceError) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from None

    @router.get("/options")
    def get_options():
        service = service_factory()
        try:
            return service.options()
        except ReportDataSourceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    @router.get("/daily")
    def get_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=1, le=7),
    ):
        return daily_report(date_value, indicators, frequency_hours, recent_days)

    @router.get("/daily/preview", response_class=HTMLResponse)
    def preview_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=1, le=7),
    ):
        report = daily_report(date_value, indicators, frequency_hours, recent_days)
        return HTMLResponse(render_report_html(report))

    @router.get("/daily/pdf")
    def download_daily(
        date_value: str = Query(alias="date"),
        indicators: str | None = None,
        frequency_hours: str | None = None,
        recent_days: int = Query(default=3, ge=1, le=7),
    ):
        report = daily_report(date_value, indicators, frequency_hours, recent_days)
        try:
            path = renderer.render(report)
        except PdfRenderError:
            logger.exception("Daily PDF rendering failed")
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
            path = renderer.render(report)
        except PdfRenderError:
            logger.exception("Monthly PDF rendering failed")
            raise HTTPException(status_code=500, detail="PDF 生成失败") from None
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
        )

    return router

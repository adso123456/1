"""Shared application layer for water-quality reports.

This module owns the single implementation of the report application flow --
request validation, indicator/frequency normalization, artifact generation,
loading, HTML preview and PDF retrieval -- that is independent of both the
database and the HTTP entry point.  The ordinary workbench router and the
Embed router both adapt their requests and responses to this service.

No report calculation logic lives here; that stays in
``WaterQualityReportService``.  No PDF template logic lives here either; that
stays in ``WaterQualityPdfRenderer`` and ``render_report_html``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.water_quality_reports.artifacts import (
    ReportArtifactError,
    ReportArtifactNotFound,
    ReportArtifactStore,
)
from backend.water_quality_reports.pdf_renderer import (
    PdfRenderError,
    WaterQualityPdfRenderer,
)
from backend.water_quality_reports.repository import (
    ReportDataSourceError as RepositoryDataSourceError,
)
from backend.water_quality_reports.template import render_report_html


logger = logging.getLogger(__name__)


class ReportApplicationError(RuntimeError):
    """Base class for shared report application errors."""


class ReportParameterError(ReportApplicationError):
    """Invalid report parameters; both adapters map this to HTTP 422."""


class ReportDataSourceError(ReportApplicationError):
    """Report data source is unavailable; maps to HTTP 503."""


class ReportArtifactNotFoundError(ReportApplicationError):
    """Report snapshot or PDF artifact is missing; maps to HTTP 404."""


class ReportArtifactOperationError(ReportApplicationError):
    """Artifact generation or loading failed; status depends on the adapter."""


class ReportPdfRenderError(ReportApplicationError):
    """Direct PDF rendering failed; maps to HTTP 500."""


def parse_date(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` report date with the original messages."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ReportParameterError("日期必须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ReportParameterError("日期无效") from None


def parse_month(value: str) -> date:
    """Parse a ``YYYY-MM`` report month, normalized to the first of the month."""
    if re.fullmatch(r"\d{4}-\d{2}", value) is None:
        raise ReportParameterError("月份必须为 YYYY-MM")
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError:
        raise ReportParameterError("月份无效") from None


@dataclass(frozen=True)
class ReportPdfResult:
    """Immutable snapshot report plus the resolved PDF download artifact."""

    report: dict[str, Any]
    path: Path
    filename: str


class ReportApplicationService:
    """Database/HTTP agnostic report application flow.

    HTTP adapters translate the shared errors below into their own status
    codes and user-facing messages.  ValueError from the underlying store
    generation is intentionally left unhandled: the Embed adapter leaves it
    as a 500 while the ordinary adapter maps it to 422, matching the
    pre-refactor contract.
    """

    def __init__(
        self,
        artifact_store: ReportArtifactStore,
        renderer: WaterQualityPdfRenderer | None = None,
    ) -> None:
        self._store = artifact_store
        self._renderer = renderer

    def options(self) -> dict[str, Any]:
        try:
            return self._store.options()
        except RepositoryDataSourceError as exc:
            raise ReportDataSourceError(str(exc)) from exc

    def validate_generate_request(
        self,
        report_type: str,
        report_date: str | None,
        report_month: str | None,
    ) -> date:
        """Enforce the daily/monthly mutual-exclusion rule and parse the period."""
        if report_type == "daily":
            if report_date is None or report_month is not None:
                raise ReportParameterError("日报必须且只能提供 date")
            return parse_date(report_date)
        if report_month is None or report_date is not None:
            raise ReportParameterError("月报必须且只能提供 month")
        return parse_month(report_month)

    @staticmethod
    def normalize_indicators(indicators: list[int]) -> tuple[int, ...]:
        """Deduplicate while preserving order.

        The shared layer does not reject negative codes: each HTTP adapter
        decides validity -- the ordinary router maps negatives to 422 while
        the Embed adapter lets them reach the underlying validation ValueError
        (500), matching the pre-refactor contract.
        """
        return tuple(dict.fromkeys(indicators))

    @staticmethod
    def normalize_frequency_hours(
        frequency_hours: dict[str, int],
    ) -> dict[int, int]:
        """Convert the JSON string keys into integer indicator codes."""
        return {int(code): hours for code, hours in frequency_hours.items()}

    def generate(
        self,
        *,
        report_type: str,
        period: date,
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
        recent_days: int = 3,
    ) -> dict[str, Any]:
        """Compute once, snapshot, and render the PDF via the artifact store."""
        try:
            return self._store.generate(
                report_type=report_type,
                period=period,
                indicator_codes=indicator_codes,
                frequency_overrides=frequency_overrides,
                recent_days=recent_days,
            )
        except ReportArtifactError as exc:
            raise ReportArtifactOperationError(str(exc)) from exc
        except RepositoryDataSourceError as exc:
            raise ReportDataSourceError(str(exc)) from exc

    def load(self, report_id: str) -> dict[str, Any]:
        """Load the immutable snapshot for a report_id."""
        try:
            return self._store.load(report_id)
        except ReportArtifactNotFound as exc:
            raise ReportArtifactNotFoundError(str(exc)) from exc
        except ReportArtifactError as exc:
            raise ReportArtifactOperationError(str(exc)) from exc

    def preview_html(self, report_id: str) -> str:
        """Render the deterministic HTML preview from the immutable snapshot."""
        return render_report_html(self.load(report_id))

    def pdf(self, report_id: str) -> ReportPdfResult:
        """Resolve the PDF artifact and its download filename for a report_id."""
        report = self.load(report_id)
        try:
            path = self._store.pdf_path(report_id)
        except ReportArtifactNotFound as exc:
            raise ReportArtifactNotFoundError(str(exc)) from exc
        except ReportArtifactError as exc:
            raise ReportArtifactOperationError(str(exc)) from exc
        return ReportPdfResult(
            report=report,
            path=path,
            filename=self.pdf_filename(report),
        )

    @staticmethod
    def pdf_filename(report: dict[str, Any]) -> str:
        """Download filename shared by both adapters."""
        if report["report_type"] == "daily":
            suffix = report["report_date"].replace("-", "")
            return f"梁子湖流域自动站水质日报_{suffix}.pdf"
        suffix = report["report_month"].replace("-", "")
        return f"梁子湖流域自动站水质月报_{suffix}.pdf"

    def render_pdf(self, report: dict[str, Any]) -> Path:
        """Render a PDF directly for the ordinary workbench /daily|monthly/pdf
        endpoints, without going through the artifact store."""
        if self._renderer is None:
            raise ReportPdfRenderError("PDF 生成失败")
        try:
            return self._renderer.render(report)
        except PdfRenderError as exc:
            logger.exception("Direct report PDF rendering failed")
            raise ReportPdfRenderError("PDF 生成失败") from exc

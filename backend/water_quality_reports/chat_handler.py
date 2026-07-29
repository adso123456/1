"""在现有对话中识别并确定性生成水质日报、月报。"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from vanna.components import RichTextComponent
from vanna.servers.base import ChatRequest, ChatResponse, ChatStreamChunk

from backend.water_quality_reports.pdf_renderer import (
    PdfRenderError,
    WaterQualityPdfRenderer,
)
from backend.water_quality_reports.repository import ReportDataSourceError
from backend.water_quality_reports.service import WaterQualityReportService


@dataclass(frozen=True)
class ReportChatIntent:
    report_type: str
    period: date
    recent_days: int = 3
    frequency_hours: int | None = None


def parse_report_intent(
    message: str,
    *,
    today: date | None = None,
) -> ReportChatIntent | None:
    """只识别明确的日报/月报生成指令，普通问数继续走原 Agent。"""
    compact = re.sub(r"\s+", "", message)
    is_daily = "日报" in compact
    is_monthly = "月报" in compact
    if not (is_daily or is_monthly):
        return None
    if not any(word in compact for word in ("生成", "导出", "制作", "出一份", "给我")):
        return None

    current = today or date.today()
    frequency_match = re.search(r"(\d+)小时(?:1次|一次)", compact)
    frequency_hours = int(frequency_match.group(1)) if frequency_match else None
    recent_match = re.search(r"近(\d+)日", compact)
    recent_days = int(recent_match.group(1)) if recent_match else 3

    if is_daily:
        matched = re.search(
            r"(?P<year>\d{4})(?:年|-|/)(?P<month>\d{1,2})(?:月|-|/)"
            r"(?P<day>\d{1,2})日?",
            compact,
        )
        if matched:
            period = date(
                int(matched.group("year")),
                int(matched.group("month")),
                int(matched.group("day")),
            )
        elif "昨日日报" in compact or "昨天" in compact:
            period = current - timedelta(days=1)
        elif "今日日报" in compact or "今天" in compact:
            period = current
        else:
            raise ValueError("请在指令中提供日报日期，例如：生成2025年7月28日水质日报")
        return ReportChatIntent("daily", period, recent_days, frequency_hours)

    matched = re.search(
        r"(?P<year>\d{4})(?:年|-|/)(?P<month>\d{1,2})月?",
        compact,
    )
    if matched:
        period = date(int(matched.group("year")), int(matched.group("month")), 1)
    elif "本月" in compact:
        period = current.replace(day=1)
    else:
        raise ValueError("请在指令中提供月报月份，例如：生成2025年7月水质月报")
    return ReportChatIntent("monthly", period, 3, frequency_hours)


class WaterQualityReportChatHandler:
    """报告指令走固定规则，其余请求原样委托给现有聊天处理器。"""

    def __init__(
        self,
        fallback_handler,
        service_factory: Callable[[], WaterQualityReportService],
        source_resolver: Callable[[ChatRequest], str] | None = None,
    ) -> None:
        self._fallback_handler = fallback_handler
        self._service_factory = service_factory
        self._source_resolver = source_resolver
        self._renderer = WaterQualityPdfRenderer()

    async def handle_stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        try:
            intent = parse_report_intent(request.message)
        except ValueError as exc:
            yield self._text_chunk(request, str(exc))
            return
        if intent is None:
            async for chunk in self._fallback_handler.handle_stream(request):
                yield chunk
            return

        source_id = (
            self._source_resolver(request)
            if self._source_resolver is not None
            else request.metadata.get("source_id")
        )
        if source_id != "mysql-lzh-monitor":
            yield self._text_chunk(
                request,
                "水质日报、月报固定使用 `mysql-lzh-monitor`，请切换到该数据源后重试。",
            )
            return

        try:
            service = self._service_factory()
            options = service.options()
            selected = [
                item
                for item in options["indicators"]
                if str(item["name"]).lower() in request.message.lower()
            ]
            codes = tuple(int(item["code"]) for item in selected) or None
            if intent.frequency_hours is not None and codes is None:
                raise ValueError("指定监测频次时，请同时写明指标名称")
            overrides = (
                {code: intent.frequency_hours for code in codes}
                if codes is not None and intent.frequency_hours is not None
                else {}
            )
            if intent.report_type == "daily":
                report = service.daily(
                    intent.period,
                    indicator_codes=codes,
                    frequency_overrides=overrides,
                    recent_days=intent.recent_days,
                )
            else:
                report = service.monthly(
                    intent.period,
                    indicator_codes=codes,
                    frequency_overrides=overrides,
                )
            self._renderer.render(report)
            yield self._text_chunk(
                request,
                self._success_markdown(report, selected, overrides),
            )
        except (ReportDataSourceError, PdfRenderError, ValueError) as exc:
            yield self._text_chunk(request, f"报告生成失败：{exc}")

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = [chunk async for chunk in self.handle_stream(request)]
        return ChatResponse.from_chunks(chunks)

    @staticmethod
    def _text_chunk(request: ChatRequest, content: str) -> ChatStreamChunk:
        return ChatStreamChunk.from_component(
            RichTextComponent(content=content, markdown=True),
            conversation_id=request.conversation_id or "",
            request_id=request.request_id or str(uuid.uuid4()),
        )

    @staticmethod
    def _success_markdown(
        report: dict,
        selected: list[dict],
        overrides: dict[int, int],
    ) -> str:
        codes = tuple(int(item["code"]) for item in selected) or None
        report_type = report["report_type"]
        if report_type == "daily":
            parameter_name = "date"
            parameter_value = report["report_date"]
            path = "daily"
            period_text = report["report_date"]
        else:
            parameter_name = "month"
            parameter_value = report["report_month"]
            path = "monthly"
            period_text = report["report_month"]
        params: dict[str, str] = {parameter_name: parameter_value}
        if codes is not None:
            params["indicators"] = ",".join(str(code) for code in codes)
        if overrides:
            params["frequency_hours"] = ",".join(
                f"{code}:{hours}" for code, hours in sorted(overrides.items())
            )
        if report_type == "daily":
            params["recent_days"] = str(report["options"]["recent_days"])
        query = urlencode(params)
        monitoring = report["monitoring"]
        selection = (
            "全部站点配置指标"
            if codes is None
            else "、".join(str(item["name"]) for item in selected)
        )
        rate = monitoring["valid_transmission_rate"]
        rate_text = "暂无数据" if rate is None else f"{rate}%"
        return (
            f"已按固定模板生成 **{report['title']}**（{period_text}）。\n\n"
            f"- 数据源：`{report['source_id']}`\n"
            f"- 应测指标：{selection}\n"
            f"- 有效站点：{monitoring['valid_station_count']} 个\n"
            f"- 数据有效传输率：{rate_text}\n\n"
            f"[在线预览](/api/reports/water-quality/{path}/preview?{query})　"
            f"[下载 PDF](/api/reports/water-quality/{path}/pdf?{query})"
        )

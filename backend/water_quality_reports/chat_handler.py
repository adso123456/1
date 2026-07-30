"""在现有对话中分级处理水质日报、月报意图。"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from vanna.components import RichTextComponent
from vanna.servers.base import ChatRequest, ChatResponse, ChatStreamChunk

from backend.water_quality_reports.artifacts import (
    ReportArtifactError,
    ReportArtifactStore,
)
from backend.water_quality_reports.repository import ReportDataSourceError


@dataclass(frozen=True)
class ReportChatIntent:
    report_type: str
    period: date | None
    recent_days: int = 3
    frequency_hours: int | None = None


_SAFE_ALIASES = {
    "ph": "ph",
    "ph值": "ph",
    "高锰酸盐": "高锰酸盐指数",
    "codmn": "高锰酸盐指数",
    "叶绿素": "叶绿素a",
}
_FILTER_PATTERNS = (
    re.compile(r"(?:只看|仅看|只要|指标为|关注)(.+?)(?=近\d+日|\d+小时|[。；;]|$)"),
    re.compile(r"查看(.+?)指标"),
)


def parse_report_intent(
    message: str,
    *,
    today: date | None = None,
) -> ReportChatIntent | None:
    """识别明确报表意图；周期缺失时保留为空以触发配置卡。"""
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
        elif "昨日日报" in compact or "昨天" in compact or "昨日" in compact:
            period = current - timedelta(days=1)
        elif "今日日报" in compact or "今天" in compact or "今日" in compact:
            period = current
        else:
            period = None
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
        period = None
    return ReportChatIntent("monthly", period, 3, frequency_hours)


def extract_indicator_filter(message: str) -> tuple[bool, list[str]]:
    compact = re.sub(r"\s+", "", message)
    for pattern in _FILTER_PATTERNS:
        matched = pattern.search(compact)
        if not matched:
            continue
        raw = re.sub(r"(?:水质)?(?:日报|月报)", "", matched.group(1))
        terms = [
            item.strip("，,、和及与的")
            for item in re.split(r"[，,、和及与]+", raw)
            if item.strip("，,、和及与的")
        ]
        return True, terms
    return False, []


def resolve_indicators(
    message: str,
    available: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    explicit, terms = extract_indicator_filter(message)
    if not explicit:
        return [], [], False
    by_name = {
        str(item["name"]).strip().lower(): item
        for item in available
    }
    selected: list[dict[str, Any]] = []
    unknown: list[str] = []
    for term in terms:
        normalized = term.strip().lower()
        normalized = _SAFE_ALIASES.get(normalized, normalized)
        matched = by_name.get(normalized)
        if matched is None:
            unknown.append(term)
        elif matched not in selected:
            selected.append(matched)
    return selected, unknown, True


class WaterQualityReportChatHandler:
    """报表指令走固定快照服务，其余请求原样委托给现有 Agent。"""

    def __init__(
        self,
        fallback_handler,
        artifact_store: ReportArtifactStore,
        source_resolver: Callable[[ChatRequest], str] | None = None,
    ) -> None:
        self._fallback_handler = fallback_handler
        self._artifacts = artifact_store
        self._source_resolver = source_resolver

    async def handle_stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        try:
            intent = parse_report_intent(request.message)
        except ValueError as exc:
            yield self._text_chunk(request, f"报表参数无效：{exc}")
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
            yield self._structured_chunk(
                request,
                "data_source_suggestion",
                {
                    "original_question": request.message,
                    "current_source_id": source_id,
                    "current_source_name": "当前数据源",
                    "reason": "当前数据源不包含水质报表所需的数据",
                    "suggestions": [],
                },
            )
            return

        if intent.period is None:
            yield self._structured_chunk(
                request,
                "report_config",
                self._config_payload(intent, source_id),
            )
            return

        try:
            options = self._artifacts.options()
            selected, unknown, explicit = resolve_indicators(
                request.message,
                options["indicators"],
            )
            error: str | None = None
            if explicit and unknown:
                error = f"未匹配指标：{'、'.join(unknown)}"
            elif explicit and not selected:
                error = "没有匹配到任何真实配置指标"
            elif intent.frequency_hours is not None and not explicit:
                error = "指定监测频次时必须同时指定指标"
            if error is None and intent.frequency_hours is not None:
                invalid = [
                    str(item["name"])
                    for item in selected
                    if intent.frequency_hours not in item["frequencies"]
                ]
                if invalid:
                    error = (
                        f"{'、'.join(invalid)}不存在"
                        f"{intent.frequency_hours}小时1次的真实配置"
                    )
            if error is not None:
                yield self._structured_chunk(
                    request,
                    "report_config",
                    self._config_payload(
                        intent,
                        source_id,
                        available=options["indicators"],
                        error=error,
                    ),
                )
                return

            codes = tuple(int(item["code"]) for item in selected) or None
            overrides = (
                {code: intent.frequency_hours for code in codes}
                if codes is not None and intent.frequency_hours is not None
                else {}
            )
            result = self._artifacts.generate(
                report_type=intent.report_type,
                period=intent.period,
                indicator_codes=codes,
                frequency_overrides=overrides,
                recent_days=intent.recent_days,
            )
            result["indicator_names"] = (
                [str(item["name"]) for item in selected]
                if selected
                else ["全部真实配置指标"]
            )
            yield self._structured_chunk(request, "report_result", result)
        except (ReportDataSourceError, ReportArtifactError, ValueError) as exc:
            yield self._text_chunk(request, f"报告生成失败：{exc}")

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = [chunk async for chunk in self.handle_stream(request)]
        return ChatResponse.from_chunks(chunks)

    @staticmethod
    def _config_payload(
        intent: ReportChatIntent,
        source_id: str,
        *,
        available: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        current = date.today()
        period = intent.period or (
            current if intent.report_type == "daily" else current.replace(day=1)
        )
        return {
            "report_type": intent.report_type,
            "default_date": period.isoformat() if intent.report_type == "daily" else None,
            "default_month": period.strftime("%Y-%m") if intent.report_type == "monthly" else None,
            "recent_days": intent.recent_days,
            "available_indicators": available or [],
            "selected_indicators": [],
            "frequency_hours": {},
            "source_id": source_id,
            "missing_fields": ["period"] if intent.period is None else [],
            "error": error,
        }

    @staticmethod
    def _structured_chunk(
        request: ChatRequest,
        component_type: str,
        data: dict[str, Any],
    ) -> ChatStreamChunk:
        return ChatStreamChunk(
            rich={
                "type": component_type,
                "id": str(uuid.uuid4()),
                "lifecycle": "complete",
                "timestamp": str(time.time()),
                "visible": True,
                "interactive": True,
                "data": data,
            },
            simple=None,
            conversation_id=request.conversation_id or "",
            request_id=request.request_id or str(uuid.uuid4()),
        )

    @staticmethod
    def _text_chunk(request: ChatRequest, content: str) -> ChatStreamChunk:
        return ChatStreamChunk.from_component(
            RichTextComponent(content=content, markdown=True),
            conversation_id=request.conversation_id or "",
            request_id=request.request_id or str(uuid.uuid4()),
        )

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

from backend.water_quality_reports.artifacts import ReportArtifactStore
from backend.water_quality_reports.repository import ReportDataSourceError


@dataclass(frozen=True)
class ReportChatIntent:
    report_type: str
    period: date | None
    recent_days: int = 3
    frequency_hours: int | None = None
    report_type_selectable: bool = False
    error: str | None = None


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
    """识别明确的报表生成意图，不拦截普通问数。"""
    compact = re.sub(r"\s+", "", message)
    is_daily = "日报" in compact
    is_monthly = "月报" in compact
    is_generic = "报表" in compact and not (is_daily or is_monthly)
    if not (is_daily or is_monthly or is_generic):
        return None
    if not any(
        word in compact
        for word in ("生成", "导出", "制作", "出一份", "给我", "创建")
    ):
        return None

    current = today or date.today()
    frequency_match = re.search(r"(\d+)小时(?:1次|一次)", compact)
    frequency_hours = int(frequency_match.group(1)) if frequency_match else None
    recent_match = re.search(r"近(\d+)日", compact)
    recent_days = int(recent_match.group(1)) if recent_match else 3
    error = None
    if recent_match and not 2 <= recent_days <= 7:
        recent_days = 3
        error = "回看范围只能选择近2日至近7日。"

    if is_daily or is_generic:
        matched = re.search(
            r"(?P<year>\d{4})(?:年|-|/)(?P<month>\d{1,2})(?:月|-|/)"
            r"(?P<day>\d{1,2})日?",
            compact,
        )
        if matched:
            try:
                period = date(
                    int(matched.group("year")),
                    int(matched.group("month")),
                    int(matched.group("day")),
                )
            except ValueError:
                period = current
                error = "报告日期无效。"
        elif "昨日日报" in compact or "昨天" in compact or "昨日" in compact:
            period = current - timedelta(days=1)
        elif "今日日报" in compact or "今天" in compact or "今日" in compact:
            period = current
        else:
            period = current
        return ReportChatIntent(
            "daily",
            period,
            recent_days,
            frequency_hours,
            report_type_selectable=is_generic,
            error=error,
        )

    matched = re.search(
        r"(?P<year>\d{4})(?:年|-|/)(?P<month>\d{1,2})月?",
        compact,
    )
    if matched:
        try:
            period = date(
                int(matched.group("year")),
                int(matched.group("month")),
                1,
            )
        except ValueError:
            period = current.replace(day=1)
            error = "报告月份无效。"
    elif "本月" in compact:
        period = current.replace(day=1)
    else:
        period = current.replace(day=1)
    return ReportChatIntent(
        "monthly",
        period,
        3,
        frequency_hours,
        error=error,
    )


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
    """报表指令只返回待确认配置，其余请求原样委托给现有 Agent。"""

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
        intent = parse_report_intent(request.message)
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

        try:
            options = self._artifacts.options()
            selected, unknown, explicit = resolve_indicators(
                request.message,
                options["indicators"],
            )
            error = intent.error
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
            selected_items = (
                selected
                if explicit
                else options["indicators"]
            )
            selected_codes = [
                int(item["code"])
                for item in selected_items
            ]
            overrides = (
                {
                    str(code): intent.frequency_hours
                    for code in selected_codes
                }
                if intent.frequency_hours is not None and explicit
                else {}
            )
            yield self._structured_chunk(
                request,
                "report_config",
                self._config_payload(
                    intent,
                    source_id,
                    available=options["indicators"],
                    selected=selected_codes,
                    frequency_hours=overrides,
                    available_recent_days=options.get("recent_days"),
                    error=error,
                ),
            )
        except (ReportDataSourceError, ValueError) as exc:
            yield self._structured_chunk(
                request,
                "report_config",
                self._config_payload(
                    intent,
                    source_id,
                    error=f"筛选项加载失败：{exc}",
                ),
            )

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = [chunk async for chunk in self.handle_stream(request)]
        return ChatResponse.from_chunks(chunks)

    @staticmethod
    def _config_payload(
        intent: ReportChatIntent,
        source_id: str,
        *,
        available: list[dict[str, Any]] | None = None,
        selected: list[int] | None = None,
        frequency_hours: dict[str, int] | None = None,
        available_recent_days: list[int] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        current = date.today()
        period = intent.period or (
            current if intent.report_type == "daily" else current.replace(day=1)
        )
        return {
            "report_type": intent.report_type,
            "report_type_selectable": intent.report_type_selectable,
            "default_date": period.isoformat() if intent.report_type == "daily" else None,
            "default_month": period.strftime("%Y-%m") if intent.report_type == "monthly" else None,
            "recent_days": intent.recent_days,
            "available_recent_days": available_recent_days or [2, 3, 4, 5, 6, 7],
            "available_indicators": available or [],
            "selected_indicators": selected or [],
            "frequency_hours": frequency_hours or {},
            "source_id": source_id,
            "missing_fields": [],
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

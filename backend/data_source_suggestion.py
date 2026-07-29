"""保守的数据源不匹配预检与结构化建议。"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncGenerator, Iterable

from backend.data_source_catalog import DataSourceCatalog, DataSourceRecord
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from vanna.servers.base import ChatRequest, ChatResponse, ChatStreamChunk


REPORT_TERMS = re.compile(r"(?:生成|制作|导出|出一份).*(?:水质)?(?:日报|月报)")
POSTGRES_TERMS = frozenset(
    {"排污口", "排口", "整治", "溯源", "排污", "outlet"}
)
WATER_TERMS = frozenset(
    {"水质", "断面", "监测站", "codmn", "高锰酸盐", "叶绿素", "氨氮"}
)


class DataSourceSuggestionService:
    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    @staticmethod
    def _contains(question: str, terms: Iterable[str]) -> int:
        lowered = question.lower()
        return sum(1 for term in terms if term.lower() in lowered)

    def suggest(
        self,
        question: str,
        current_source_id: str,
        *,
        allowed_source_ids: Iterable[str] | None = None,
    ) -> dict | None:
        allowed = set(allowed_source_ids or ())
        restrict = allowed_source_ids is not None
        records = [
            item
            for item in self.catalog.list(status="ready", enabled=True)
            if not restrict or item.source_id in allowed
        ]
        current = next(
            (item for item in records if item.source_id == current_source_id),
            self.catalog.require(current_source_id),
        )
        target: DataSourceRecord | None = None
        reason = ""
        if REPORT_TERMS.search(question):
            target = next(
                (
                    item
                    for item in records
                    if "water_quality_daily_report" in item.capabilities
                    or "water_quality_monthly_report" in item.capabilities
                ),
                None,
            )
            reason = "当前数据源不包含水质日报、月报所需数据"
        else:
            identifiers = {
                token.lower()
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", question)
            }
            if identifiers and any(
                token in current.routing_summary.lower()
                for token in identifiers
            ):
                return None
        if target is None and self._contains(question, POSTGRES_TERMS) >= 1:
            target = next(
                (
                    item
                    for item in records
                    if item.source_id == "postgresql-main"
                ),
                None,
            )
            reason = "问题明确属于排污口治理数据"
        elif target is None and self._contains(question, WATER_TERMS) >= 2:
            target = next(
                (
                    item
                    for item in records
                    if item.source_id == "mysql-lzh-monitor"
                ),
                None,
            )
            reason = "问题明确属于水质监测数据"
        if target is None or target.source_id == current_source_id:
            return None
        return {
            "original_question": question,
            "current_source_id": current.source_id,
            "current_source_name": current.display_name,
            "reason": reason,
            "suggestions": [
                {
                    "source_id": target.source_id,
                    "display_name": target.display_name,
                    "database_type": target.database_type,
                }
            ],
        }


class DataSourceSuggestionChatHandler:
    """明确错源时在 Agent 前短路，绝不跨源执行。"""

    def __init__(
        self,
        fallback_handler,
        coordinator: DataSourceRequestCoordinator,
        suggestion_service: DataSourceSuggestionService,
    ) -> None:
        self._fallback = fallback_handler
        self._coordinator = coordinator
        self._suggestions = suggestion_service

    async def handle_stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        allowed_source_ids = request.metadata.pop(
            "_allowed_source_ids",
            None,
        )
        context = self._coordinator.resolve(
            request.conversation_id,
            request.metadata,
        )
        payload = self._suggestions.suggest(
            request.message,
            context.source_id,
            allowed_source_ids=allowed_source_ids,
        )
        if payload is not None:
            yield ChatStreamChunk(
                rich={
                    "type": "data_source_suggestion",
                    "id": str(uuid.uuid4()),
                    "lifecycle": "complete",
                    "timestamp": str(time.time()),
                    "visible": True,
                    "interactive": True,
                    "data": payload,
                },
                simple=None,
                conversation_id=request.conversation_id or "",
                request_id=request.request_id or str(uuid.uuid4()),
            )
            return
        async for chunk in self._fallback.handle_stream(request):
            yield chunk

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = [chunk async for chunk in self.handle_stream(request)]
        return ChatResponse.from_chunks(chunks)

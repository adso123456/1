"""保守的数据源不匹配预检与结构化建议。"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncGenerator, Iterable, Mapping

from backend.data_source_catalog import DataSourceCatalog, DataSourceRecord
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from vanna.servers.base import ChatRequest, ChatResponse, ChatStreamChunk


CURRENT_SOURCE_MAX_SCORE = 4
CANDIDATE_MIN_SCORE = 6
MINIMUM_LEAD_SCORE = 3
CAPABILITY_SCORE = 100
IDENTIFIER_SCORE = 5
SEMANTIC_GROUP_SCORE = 4

CAPABILITY_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "water_quality_daily_report": re.compile(
        r"(?:生成|制作|导出|出一份).*(?:水质)?.*日报"
    ),
    "water_quality_monthly_report": re.compile(
        r"(?:生成|制作|导出|出一份).*(?:水质)?.*月报"
    ),
}
SEMANTIC_ALIASES: Mapping[str, tuple[str, ...]] = {
    "outlet": ("排污口", "排口", "排污", "整治", "溯源", "outlet"),
    "water_quality": (
        "水质",
        "断面",
        "监测站",
        "氨氮",
        "总磷",
        "高锰酸盐",
        "叶绿素",
        "ph",
        "codmn",
    ),
    "weather": (
        "气象",
        "天气",
        "气温",
        "温度",
        "降雨",
        "雨量",
        "风速",
        "湿度",
        "weather",
        "temperature",
        "rainfall",
    ),
    "report": ("日报", "月报", "报表", "report"),
}


class DataSourceSuggestionService:
    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    @staticmethod
    def _safe_text(record: DataSourceRecord) -> str:
        parts = [
            record.display_name,
            record.description,
            record.database_type,
            record.routing_summary,
            *record.capabilities,
        ]
        for item in record.selected_scope:
            parts.extend(
                str(item.get(name, ""))
                for name in (
                    "table",
                    "table_comment",
                    "column",
                    "comment",
                )
            )
        return " ".join(parts).lower().replace("_", " ")

    @staticmethod
    def _question_identifiers(question: str) -> set[str]:
        return {
            token.lower().replace("_", " ")
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", question)
        }

    @classmethod
    def _score(cls, question: str, record: DataSourceRecord) -> int:
        lowered = question.lower()
        safe_text = cls._safe_text(record)
        score = 0
        for identifier in cls._question_identifiers(question):
            if identifier in safe_text:
                score += IDENTIFIER_SCORE
        for aliases in SEMANTIC_ALIASES.values():
            question_matches = [
                alias for alias in aliases if alias.lower() in lowered
            ]
            if not question_matches:
                continue
            source_matches = [
                alias for alias in aliases if alias.lower() in safe_text
            ]
            if source_matches:
                score += SEMANTIC_GROUP_SCORE
                score += min(6, len(question_matches) * 2)
        return score

    @staticmethod
    def _required_capability(question: str) -> str:
        return next(
            (
                capability
                for capability, pattern in CAPABILITY_PATTERNS.items()
                if pattern.search(question)
            ),
            "",
        )

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
        required_capability = self._required_capability(question)
        if required_capability and required_capability in current.capabilities:
            return None
        current_score = self._score(question, current)
        if not required_capability and current_score > CURRENT_SOURCE_MAX_SCORE:
            return None

        scored: list[tuple[int, DataSourceRecord]] = []
        for item in records:
            if item.source_id == current_source_id:
                continue
            score = self._score(question, item)
            if required_capability in item.capabilities:
                score += CAPABILITY_SCORE
            elif required_capability:
                continue
            scored.append((score, item))
        scored.sort(key=lambda item: (-item[0], item[1].source_id))
        if not scored:
            if not required_capability:
                return None
            targets: list[DataSourceRecord] = []
        else:
            best_score, target = scored[0]
            targets = [target]
            if required_capability:
                targets = [
                    item
                    for score, item in scored
                    if score >= CAPABILITY_SCORE
                ]
        second_score = scored[1][0] if len(scored) > 1 else 0
        minimum = (
            CAPABILITY_SCORE
            if required_capability
            else CANDIDATE_MIN_SCORE
        )
        if (
            scored
            and (
                best_score < minimum
                or (
                    not required_capability
                    and best_score - second_score < MINIMUM_LEAD_SCORE
                )
            )
        ):
            return None
        reason = (
            (
                "当前数据源不包含该报表所需的数据"
                if targets
                else "当前数据源不包含该报表所需的数据，且暂时没有可用的建议数据源"
            )
            if required_capability
            else "当前数据源与问题的表、字段或业务语义不匹配"
        )
        return {
            "original_question": question,
            "current_source_id": current.source_id,
            "current_source_name": current.display_name,
            "reason": reason,
            "suggestions": [
                {
                    "source_id": item.source_id,
                    "display_name": item.display_name,
                    "database_type": item.database_type,
                }
                for item in targets
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

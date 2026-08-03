from __future__ import annotations

import time
from typing import Any

from vanna.core.enhancer import LlmContextEnhancer

from backend.metadata_retriever import DeterministicMetadataRetriever
from backend.query_intent import ContextProfile
from backend.query_performance import get_query_performance, record_timing


class DeterministicMetadataContextEnhancer(LlmContextEnhancer):
    """在 Vanna system prompt 中追加确定性元数据候选上下文。"""

    def __init__(
        self,
        base_enhancer: LlmContextEnhancer | None = None,
        metadata_retriever: DeterministicMetadataRetriever | None = None,
        top_n: int = 10,
    ) -> None:
        self.base_enhancer = base_enhancer
        self.metadata_retriever = metadata_retriever or DeterministicMetadataRetriever()
        self.top_n = top_n

    async def enhance_system_prompt(
        self, system_prompt: str, user_message: str, user: Any
    ) -> str:
        enhance_started = time.monotonic()
        enhanced_prompt = system_prompt
        if self.base_enhancer:
            enhanced_prompt = await self.base_enhancer.enhance_system_prompt(
                system_prompt, user_message, user
            )

        state = get_query_performance()
        simple = (
            state is not None
            and state.context_profile is ContextProfile.SIMPLE_LOOKUP
        )
        top_n = min(self.top_n, 6) if simple else self.top_n
        retrieve_started = time.monotonic()
        candidates = self.metadata_retriever.retrieve(user_message, top_n=top_n)
        record_timing(
            "metadata_retrieve_ms",
            (time.monotonic() - retrieve_started) * 1000,
        )
        if not candidates:
            record_timing(
                "context_enhance_ms",
                (time.monotonic() - enhance_started) * 1000,
            )
            return enhanced_prompt

        result = enhanced_prompt + self._build_metadata_context(
            user_message, candidates, simple=simple
        )
        record_timing(
            "context_enhance_ms",
            (time.monotonic() - enhance_started) * 1000,
        )
        return result

    async def enhance_user_messages(self, messages: list[Any], user: Any) -> list[Any]:
        if self.base_enhancer:
            return await self.base_enhancer.enhance_user_messages(messages, user)
        return messages

    def _build_metadata_context(
        self,
        user_message: str,
        candidates: list[dict[str, Any]],
        *,
        simple: bool = False,
    ) -> str:
        table_lines = []
        column_lines = []
        table_column_lines = []
        seen_columns: set[tuple[str, str]] = set()

        for index, candidate in enumerate(candidates, start=1):
            table_lines.append(
                f"{index}. {candidate['table_name']} | "
                f"comment={candidate['table_comment']} | "
                f"score={candidate['score']} | "
                f"matched_by={', '.join(candidate['matched_by'])} | "
                f"conflict_family={candidate['conflict_family'] or 'none'} | "
                f"risk_level={candidate['risk_level']} | "
                f"reason={candidate['reason']}"
            )

            for column in candidate.get("matched_columns", []):
                key = (candidate["table_name"], column["column_name"])
                if key in seen_columns:
                    continue
                seen_columns.add(key)
                column_lines.append(
                    f"- {candidate['table_name']}.{column['column_name']} "
                    f"({column['column_type']}): {column['column_comment']} | "
                    f"matched_by={', '.join(column['matched_by'])}"
                )
            if index <= (2 if simple else 3):
                rendered = "、".join(
                    f"{column['column_name']}"
                    f"({column['column_comment']})"
                    for column in candidate.get("columns", [])
                )
                table_column_lines.append(
                    f"- {candidate['table_name']}: {rendered}"
                )

        if not column_lines:
            column_lines.append("- No deterministic column match. Use table priority first.")

        return "\n\n".join(
            [
                "",
                "## Deterministic Metadata Context",
                f"User question: {user_message}",
                "deterministic metadata candidates have higher priority than vector similarity results.",
                "Candidate tables are ordered by priority. Prefer earlier tables unless the user explicitly asks for a lower-ranked table.",
                "Do not let similar table names or vector similarity results override the deterministic candidate order.",
                "Do not query information_schema, pg_catalog, or other schema tables to rediscover metadata.",
                "",
                "Candidate tables:",
                "\n".join(table_lines),
                "",
                "Candidate columns:",
                "\n".join(column_lines[: (12 if simple else 20)]),
                "",
                (
                    "Full columns for the top 2 relevant tables:"
                    if simple
                    else "Full columns for the top 3 relevant tables:"
                ),
                "\n".join(table_column_lines),
            ]
        )

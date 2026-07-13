from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from vanna.core.enhancer import LlmContextEnhancer

from tools.sql_guard import SQLGuard

ALLOWED_TRAINING_LEVELS = {
    "level2_sql_examples",
    "level3_p0_sql_examples",
    "level3_p1_sql_examples",
    "level3_p2_sql_examples",
}


@dataclass
class SqlExampleContextStats:
    search_similar_usage_called: bool = False
    tool_name_filter: str = ""
    top_k: int = 0
    returned_count: int = 0
    injected_count: int = 0
    filtered: list[dict[str, str]] = field(default_factory=list)


class SqlExampleContextEnhancer(LlmContextEnhancer):
    """Append approved Level 2 and Level 3 run_sql examples."""

    def __init__(
        self,
        *,
        base_enhancer: LlmContextEnhancer | None = None,
        memory: Any | None = None,
        sql_guard: SQLGuard | None = None,
        top_k: int = 5,
    ) -> None:
        self.base_enhancer = base_enhancer
        self.memory = memory
        self.sql_guard = sql_guard or SQLGuard()
        self.top_k = top_k
        self.last_stats = SqlExampleContextStats(top_k=top_k)

    async def enhance_system_prompt(
        self, system_prompt: str, user_message: str, user: Any
    ) -> str:
        enhanced_prompt = system_prompt
        if self.base_enhancer:
            enhanced_prompt = await self.base_enhancer.enhance_system_prompt(
                system_prompt, user_message, user
            )

        examples = await self._retrieve_examples(user_message)
        if not examples:
            return enhanced_prompt

        return enhanced_prompt + self._build_examples_section(examples)

    async def enhance_user_messages(self, messages: list[Any], user: Any) -> list[Any]:
        if self.base_enhancer:
            return await self.base_enhancer.enhance_user_messages(messages, user)
        return messages

    async def _retrieve_examples(self, user_message: str) -> list[dict[str, Any]]:
        self.last_stats = SqlExampleContextStats(top_k=self.top_k)
        if not self.memory or not hasattr(self.memory, "search_similar_usage"):
            return []

        self.last_stats.search_similar_usage_called = True
        self.last_stats.tool_name_filter = "run_sql"
        results = await self.memory.search_similar_usage(
            question=user_message,
            context=SimpleNamespace(metadata={"stage": "sql_example_context_enhancer"}),
            limit=self.top_k,
            tool_name_filter="run_sql",
        )
        self.last_stats.returned_count = len(results or [])

        examples: list[dict[str, Any]] = []
        for result in results or []:
            memory = getattr(result, "memory", result)
            example, reason = self._candidate_to_example(memory, user_message)
            if reason:
                self.last_stats.filtered.append(
                    {
                        "sample_id": example.get("sample_id", "") if example else "",
                        "reason": reason,
                    }
                )
                continue
            if example:
                examples.append(example)
            if len(examples) >= self.top_k:
                break

        self.last_stats.injected_count = len(examples)
        return examples

    def _candidate_to_example(
        self, memory: Any, user_message: str
    ) -> tuple[dict[str, Any] | None, str]:
        tool_name = str(getattr(memory, "tool_name", "") or "")
        args = getattr(memory, "args", {}) or {}
        metadata = getattr(memory, "metadata", {}) or {}
        question = str(getattr(memory, "question", "") or "")
        sample_id = str(metadata.get("sample_id", "") or "")
        sql = str(args.get("sql", "") or "")

        example = {
            "sample_id": sample_id,
            "question": question,
            "sql": sql,
            "tables": [],
        }

        if tool_name != "run_sql":
            return example, "tool_name is not run_sql"
        training_level = metadata.get("training_level")
        if training_level not in ALLOWED_TRAINING_LEVELS:
            return example, f"training_level is not allowed: {training_level}"
        if metadata.get("train_decision") != "approved":
            return example, "train_decision is not approved"
        if not sample_id:
            return example, "sample_id is empty"
        if not sql.strip():
            return example, "sql is empty"
        if not self._is_select_sql(sql):
            return example, "sql is not SELECT"
        if not self._has_limit(sql):
            return example, "sql has no LIMIT"
        if self._has_select_star(sql):
            return example, "sql contains SELECT *"

        expected_tables = metadata.get("expected_tables")
        deterministic_candidate_tables = (
            expected_tables if isinstance(expected_tables, list) else None
        )
        guard_result = self.sql_guard.validate(
            sql=sql,
            query=question or user_message,
            deterministic_candidate_tables=deterministic_candidate_tables,
        )
        if not guard_result.passed:
            return example, "SQL Guard failed: " + guard_result.reason
        if guard_result.severity != "ok":
            return example, "SQL Guard severity is " + guard_result.severity

        example["tables"] = guard_result.used_tables
        return example, ""

    @staticmethod
    def _is_select_sql(sql: str) -> bool:
        return bool(re.match(r"^\s*select\b", sql, flags=re.I))

    @staticmethod
    def _has_limit(sql: str) -> bool:
        return bool(re.search(r"\blimit\b", sql, flags=re.I))

    @staticmethod
    def _has_select_star(sql: str) -> bool:
        return bool(re.search(r"\bselect\s+\*", sql, flags=re.I | re.S))

    @staticmethod
    def _build_examples_section(examples: list[dict[str, Any]]) -> str:
        lines = [
            "",
            "## Retrieved Approved SQL Examples",
            "Use these approved SQL examples as table and field selection references. Do not copy literal filter values unless the user asks for them.",
        ]
        for index, example in enumerate(examples, start=1):
            tables = ", ".join(example["tables"]) if example["tables"] else "unknown"
            lines.extend(
                [
                    "",
                    f"Example {index}:",
                    f"- sample_id: {example['sample_id']}",
                    f"- question: {example['question']}",
                    f"- tables: {tables}",
                    "- sql:",
                    example["sql"].strip(),
                ]
            )
        return "\n".join(lines)

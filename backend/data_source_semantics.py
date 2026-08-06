"""根据真实结构和受限画像生成可校验的业务语义候选。"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


logger = logging.getLogger(__name__)


class DataSourceSemanticAnalyzer:
    """LLM 只补充语义；不能新增表、字段、关联关系或 SQL 规则。"""

    def analyze(
        self,
        metadata: Iterable[Mapping[str, Any]],
        profiles: Iterable[Mapping[str, Any]],
        *,
        display_name: str,
        description: str,
        progress: Any | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items = [dict(item) for item in metadata]
        profile_map = {
            (str(item.get("schema") or ""), str(item.get("table") or "")): dict(item)
            for item in profiles
        }
        columns_by_table: dict[tuple[str, str], set[str]] = defaultdict(set)
        for item in items:
            columns_by_table[(str(item.get("schema") or ""), str(item.get("table") or ""))].add(
                str(item.get("column") or "")
            )

        semantics: dict[tuple[str, str], dict[str, Any]] = {}
        for key, columns in columns_by_table.items():
            profile = profile_map.get(key, {})
            comment = str(profile.get("table_comment") or "")
            semantics[key] = {
                "domain": comment or description or display_name or "其他业务",
                "semantic_summary": comment or f"{key[1]} 业务数据",
                "grain": str(profile.get("grain_candidate") or "待语义确认"),
                "time_column": str(profile.get("time_column_candidate") or ""),
                "valid_row_rules": [],
                "logical_relations": [],
                "table_role": str(profile.get("table_role_candidate") or "业务表"),
                "confidence": "deterministic",
            }

        warnings: list[str] = []
        llm_enabled = os.getenv("DATA_SOURCE_SEMANTIC_LLM_ENABLED", "1") == "1"
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if llm_enabled and api_key and not api_key.startswith("replace_with_"):
            batch_size = max(1, int(os.getenv("DATA_SOURCE_SEMANTIC_BATCH_TABLES", "12")))
            profiles_list = list(profile_map.values())
            total_batches = (len(profiles_list) + batch_size - 1) // batch_size
            for offset in range(0, len(profiles_list), batch_size):
                batch_no = offset // batch_size + 1
                batch = profiles_list[offset : offset + batch_size]
                if progress is not None:
                    try:
                        progress(batch_no, total_batches)
                    except Exception:
                        pass
                logger.info(
                    "语义分析批次 %d/%d 开始（%d 张表）",
                    batch_no,
                    total_batches,
                    len(batch),
                )
                started = time.monotonic()
                try:
                    candidates = self._call_llm(
                        batch,
                        display_name=display_name,
                        description=description,
                        api_key=api_key,
                    )
                    self._apply_validated_candidates(
                        candidates,
                        semantics,
                        columns_by_table,
                    )
                    logger.info(
                        "语义分析批次 %d/%d 完成，耗时 %.1fs",
                        batch_no,
                        total_batches,
                        time.monotonic() - started,
                    )
                except Exception as exc:
                    logger.warning(
                        "语义分析批次 %d/%d 降级：%s",
                        batch_no,
                        total_batches,
                        type(exc).__name__,
                    )
                    warnings.append(f"第 {offset // batch_size + 1} 批语义分析降级：{type(exc).__name__}")
        elif llm_enabled:
            warnings.append("未配置可用的 DEEPSEEK_API_KEY，已使用确定性语义")

        enriched: list[dict[str, Any]] = []
        for item in items:
            key = (str(item.get("schema") or ""), str(item.get("table") or ""))
            enriched.append({**item, **semantics[key]})
        return enriched, {
            "semantic_mode": "llm_validated" if any(
                value["confidence"] == "llm_validated" for value in semantics.values()
            ) else "deterministic",
            "warnings": warnings,
        }

    @staticmethod
    def _call_llm(
        profiles: list[dict[str, Any]],
        *,
        display_name: str,
        description: str,
        api_key: str,
    ) -> list[dict[str, Any]]:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.getenv("DATA_SOURCE_SEMANTIC_TIMEOUT_SECONDS", "60")),
            max_retries=1,
        )
        compact_profiles = []
        for profile in profiles:
            compact_profiles.append(
                {
                    "schema": profile.get("schema"),
                    "table": profile.get("table"),
                    "comment": profile.get("table_comment"),
                    "row_estimate": profile.get("row_estimate"),
                    "role_candidate": profile.get("table_role_candidate"),
                    "grain_candidate": profile.get("grain_candidate"),
                    "time_column_candidate": profile.get("time_column_candidate"),
                    "columns": [
                        {
                            "column": item.get("column"),
                            "type": item.get("type"),
                            "sample_distinct_count": item.get("sample_distinct_count"),
                            "typical_values": item.get("typical_values", []),
                        }
                        for item in profile.get("columns", [])
                    ],
                }
            )
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是数据库语义分析器。只能解释输入中真实存在的表和字段；"
                        "不得新增关联关系、不得输出 SQL、不得猜测有效数据过滤条件。"
                        "输出 JSON：{\"tables\":[{\"schema\":...,\"table\":...,"
                        "\"domain\":...,\"summary\":...,\"grain\":...,"
                        "\"time_column\":...,\"table_role\":...}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "data_source": display_name,
                            "description": description,
                            "profiles": compact_profiles,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        tables = payload.get("tables", []) if isinstance(payload, Mapping) else []
        return [dict(item) for item in tables if isinstance(item, Mapping)]

    @staticmethod
    def _apply_validated_candidates(
        candidates: list[dict[str, Any]],
        semantics: dict[tuple[str, str], dict[str, Any]],
        columns_by_table: Mapping[tuple[str, str], set[str]],
    ) -> None:
        for candidate in candidates:
            key = (str(candidate.get("schema") or ""), str(candidate.get("table") or ""))
            if key not in semantics:
                continue
            current = semantics[key]
            for source_name, target_name, max_length in (
                ("domain", "domain", 80),
                ("summary", "semantic_summary", 240),
                ("grain", "grain", 160),
                ("table_role", "table_role", 40),
            ):
                value = candidate.get(source_name)
                if isinstance(value, str) and value.strip():
                    current[target_name] = value.strip()[:max_length]
            time_column = candidate.get("time_column")
            if isinstance(time_column, str) and time_column in columns_by_table[key]:
                current["time_column"] = time_column
            current["confidence"] = "llm_validated"

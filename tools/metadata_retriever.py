from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


DEFAULT_INDEX_PATH = (
    Path(__file__).resolve().parents[1] / "agent_data" / "column_metadata_index.json"
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any) -> str:
    text = _clean_text(value).lower()
    return re.sub(r"[\s\-_（）()【】\[\]：:，,。.;；/\\]+", "", text)


def _name_text(value: Any) -> str:
    return _clean_text(value).lower()


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


class DeterministicMetadataRetriever:
    """基于元数据 JSON 的确定性候选表检索器。"""

    def __init__(self, index_path: str | Path | None = None) -> None:
        self.index_path = Path(index_path or DEFAULT_INDEX_PATH)
        self.tables = self._load_tables()
        self.conflict_families = self._build_conflict_families()

    def retrieve(self, query: str, top_n: int = 10) -> list[dict[str, Any]]:
        candidates = [self._score_table(query, table) for table in self.tables.values()]
        candidates = [candidate for candidate in candidates if candidate["score"] > 0]
        candidates.sort(
            key=lambda item: (
                -item["score"],
                0 if "exact_table_name" in item["matched_by"] else 1,
                len(item["table_name"]),
                item["table_name"],
            )
        )
        return candidates[:top_n]

    def find_columns(self, query: str, top_n: int = 20) -> list[dict[str, Any]]:
        query_name = _name_text(query)
        query_compact = _compact_text(query)
        matches: list[dict[str, Any]] = []

        for table in self.tables.values():
            for column in table["columns"]:
                column_name = _name_text(column["column_name"])
                column_comment = _compact_text(column["column_comment"])
                score = 0
                matched_by: list[str] = []

                if query_name == column_name:
                    score += 1000
                    matched_by.append("exact_column_name")
                elif query_name and (
                    query_name in column_name or column_name in query_name
                ):
                    score += 520
                    matched_by.append("column_name_substring")

                if query_compact == column_comment:
                    score += 900
                    matched_by.append("exact_column_comment")
                elif query_compact and (
                    query_compact in column_comment or column_comment in query_compact
                ):
                    score += 650
                    matched_by.append("column_comment_substring")
                else:
                    ratio = _similarity(query_compact, column_comment)
                    if ratio >= 0.72:
                        score += int(520 * ratio)
                        matched_by.append("column_comment_fuzzy")

                if score <= 0:
                    continue

                matches.append(
                    {
                        "table_name": table["table_name"],
                        "column_name": column["column_name"],
                        "column_type": column["column_type"],
                        "column_comment": column["column_comment"],
                        "table_comment": table["table_comment"],
                        "matched_by": matched_by,
                        "reason": "；".join(matched_by),
                        "score": score,
                    }
                )

        matches.sort(key=lambda item: (-item["score"], item["table_name"], item["column_name"]))
        return matches[:top_n]

    def _load_tables(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            raise FileNotFoundError(f"元数据索引不存在: {self.index_path}")

        rows = json.loads(self.index_path.read_text(encoding="utf-8"))
        tables: dict[str, dict[str, Any]] = {}

        for row in rows:
            table_name = _clean_text(row.get("table"))
            if not table_name:
                continue

            table = tables.setdefault(
                table_name,
                {
                    "table_name": table_name,
                    "table_comment": _clean_text(row.get("table_comment")),
                    "columns": [],
                },
            )
            table["columns"].append(
                {
                    "column_name": _clean_text(row.get("column")),
                    "column_type": _clean_text(row.get("type")),
                    "column_comment": _clean_text(row.get("comment")),
                }
            )

        return tables

    def _build_conflict_families(self) -> dict[str, str]:
        raw_families: dict[str, list[str]] = defaultdict(list)
        for table_name in self.tables:
            raw_families[self._family_key(table_name)].append(table_name)

        families: dict[str, str] = {}
        for family_key, table_names in raw_families.items():
            if len(table_names) <= 1:
                for table_name in table_names:
                    families[table_name] = ""
                continue

            family_name = family_key
            for table_name in table_names:
                families[table_name] = family_name

        return families

    def _family_key(self, table_name: str) -> str:
        name = table_name.lower()

        if name.startswith("rs_outlet"):
            return "rs_outlet"
        if name.startswith("dc_survey"):
            return "dc_survey"
        if name.startswith("gis_region"):
            return "gis_region"
        if (
            name.startswith("se_watershed")
            or name.startswith("gis_watershed_partition")
            or name.startswith("layer_watershed")
        ):
            return "watershed"
        if name.startswith("wm_waterquality"):
            return "wm_waterquality_records"
        if name in {"wm_water_intake", "wm_water_source_intake_v2"}:
            return "wm_water_intake"
        if name.startswith("wst_trace"):
            return "wst_trace"

        name = re.sub(r"_v\d+$", "", name)
        name = re.sub(r"_(day|hour|month|year)_records$", "_records", name)
        parts = name.split("_")
        if len(parts) >= 2:
            return "_".join(parts[:2])
        return name

    def _score_table(self, query: str, table: dict[str, Any]) -> dict[str, Any]:
        query_name = _name_text(query)
        query_compact = _compact_text(query)
        table_name = table["table_name"]
        table_name_text = _name_text(table_name)
        table_name_compact = _compact_text(table_name)
        table_comment = table["table_comment"]
        table_comment_compact = _compact_text(table_comment)

        score = 0
        matched_by: list[str] = []
        reason_parts: list[str] = []
        matched_columns: list[dict[str, Any]] = []

        def add(points: int, method: str, reason: str) -> None:
            nonlocal score
            score += points
            if method not in matched_by:
                matched_by.append(method)
            reason_parts.append(reason)

        if query_name == table_name_text:
            add(10000, "exact_table_name", "精确命中表名")
        elif query_name and (
            query_name in table_name_text or table_name_text in query_name
        ):
            add(820, "table_name_substring", "表名存在子串匹配")
        else:
            ratio = _similarity(query_name, table_name_text)
            if ratio >= 0.72:
                add(int(420 * ratio), "table_name_fuzzy", "表名模糊相似")

        if query_compact == table_comment_compact and query_compact:
            add(3600, "exact_table_comment", "精确命中中文表注释")
        elif query_compact and (
            query_compact in table_comment_compact
            or table_comment_compact in query_compact
        ):
            add(2300, "table_comment_substring", "中文表注释存在子串匹配")
        else:
            ratio = _similarity(query_compact, table_comment_compact)
            if ratio >= 0.56:
                add(int(1350 * ratio), "table_comment_fuzzy", "中文表注释模糊相似")

        column_score, column_methods, column_reasons, matched_columns = self._score_columns(
            query_name, query_compact, table["columns"]
        )
        if column_score:
            score += column_score
            for method in column_methods:
                if method not in matched_by:
                    matched_by.append(method)
            reason_parts.extend(column_reasons)

        outlet_code_score, outlet_code_methods, outlet_code_reasons, outlet_code_columns = (
            self._score_outlet_code_intent(query_compact, table_name_text, table["columns"])
        )
        if outlet_code_score:
            score += outlet_code_score
            for method in outlet_code_methods:
                if method not in matched_by:
                    matched_by.append(method)
            reason_parts.extend(outlet_code_reasons)
            seen_column_names = {column["column_name"] for column in matched_columns}
            for column in outlet_code_columns:
                if column["column_name"] not in seen_column_names:
                    matched_columns.append(column)
                    seen_column_names.add(column["column_name"])

        intent_score, intent_methods, intent_reasons = self._score_intent(
            query_compact, table_name_text, table_comment_compact
        )
        if intent_score:
            score += intent_score
            for method in intent_methods:
                if method not in matched_by:
                    matched_by.append(method)
            reason_parts.extend(intent_reasons)

        conflict_family = self.conflict_families.get(table_name, "")
        risk_level = self._risk_level(conflict_family, matched_by)

        return {
            "table_name": table_name,
            "score": max(score, 0),
            "matched_by": matched_by,
            "matched_columns": matched_columns,
            "table_comment": table_comment,
            "conflict_family": conflict_family,
            "risk_level": risk_level,
            "reason": "；".join(reason_parts) if reason_parts else "未命中",
        }

    def _score_columns(
        self,
        query_name: str,
        query_compact: str,
        columns: list[dict[str, Any]],
    ) -> tuple[int, list[str], list[str], list[dict[str, Any]]]:
        best_score = 0
        methods: list[str] = []
        reasons: list[str] = []
        matches: list[dict[str, Any]] = []

        for column in columns:
            column_name = _name_text(column["column_name"])
            column_comment = _compact_text(column["column_comment"])
            current_score = 0
            current_methods: list[str] = []

            if query_name == column_name:
                current_score += 2850
                current_methods.append("exact_column_name")
            elif query_name and len(query_name) >= 3 and (
                query_name in column_name or column_name in query_name
            ):
                current_score += 720
                current_methods.append("column_name_substring")

            if query_compact == column_comment and query_compact:
                current_score += 2450
                current_methods.append("exact_column_comment")
            elif query_compact and len(query_compact) >= 2 and (
                query_compact in column_comment or column_comment in query_compact
            ):
                current_score += 1420
                current_methods.append("column_comment_substring")
            else:
                ratio = _similarity(query_compact, column_comment)
                if ratio >= 0.76:
                    current_score += int(760 * ratio)
                    current_methods.append("column_comment_fuzzy")

            if current_score <= 0:
                continue

            matches.append(
                {
                    "column_name": column["column_name"],
                    "column_type": column["column_type"],
                    "column_comment": column["column_comment"],
                    "matched_by": current_methods,
                }
            )

            if current_score > best_score:
                best_score = current_score
                methods = current_methods

        if best_score:
            reasons.append("字段名或字段注释命中")

        matches.sort(
            key=lambda item: (
                0 if "exact_column_name" in item["matched_by"] else 1,
                0 if "exact_column_comment" in item["matched_by"] else 1,
                item["column_name"],
            )
        )
        return best_score, methods, reasons, matches[:8]

    def _score_outlet_code_intent(
        self,
        query_compact: str,
        table_name: str,
        columns: list[dict[str, Any]],
    ) -> tuple[int, list[str], list[str], list[dict[str, Any]]]:
        if not (
            "排污口" in query_compact
            and any(word in query_compact for word in ("编码", "编号"))
        ):
            return 0, [], [], []

        priority_tables = {
            "rs_outlet": 6200,
            "rs_outlet_info_v2": 5900,
        }
        if table_name not in priority_tables:
            return 0, [], [], []

        explicit_code_columns = {
            "outlet_code": 0,
            "outlet_code_national": 1,
            "outlet_code_local": 2,
            "outlet_code_province": 3,
        }
        matched_columns = [
            {
                "column_name": column["column_name"],
                "column_type": column["column_type"],
                "column_comment": column["column_comment"],
                "matched_by": ["outlet_code_intent_column"],
            }
            for column in columns
            if column["column_name"] in explicit_code_columns
        ]
        matched_columns.sort(
            key=lambda column: explicit_code_columns[column["column_name"]]
        )

        if not matched_columns:
            return 0, [], [], []

        return (
            priority_tables[table_name],
            ["outlet_code_intent"],
            ["排污口编码问题优先排污口主表及明确 outlet_code 字段"],
            matched_columns,
        )

    def _score_intent(
        self,
        query_compact: str,
        table_name: str,
        table_comment: str,
    ) -> tuple[int, list[str], list[str]]:
        score = 0
        methods: list[str] = []
        reasons: list[str] = []

        def add(points: int, method: str, reason: str) -> None:
            nonlocal score
            score += points
            if method not in methods:
                methods.append(method)
            reasons.append(reason)

        if "水质" in query_compact:
            is_waterquality_record = re.fullmatch(
                r"wm_waterquality_(day|hour|month|year)_records", table_name
            )
            is_trend_query = any(word in query_compact for word in ("时间段", "变化", "趋势"))
            has_granularity = any(word in query_compact for word in ("日", "小时", "月", "年"))

            if is_waterquality_record or "水质监测" in table_comment:
                add(1800, "waterquality_intent", "水质问题优先匹配水质监测记录表")
            if "日" in query_compact and table_name == "wm_waterquality_day_records":
                add(1450, "waterquality_granularity", "水质日粒度命中")
            if "小时" in query_compact and table_name == "wm_waterquality_hour_records":
                add(1450, "waterquality_granularity", "水质小时粒度命中")
            if "月" in query_compact and table_name == "wm_waterquality_month_records":
                add(1450, "waterquality_granularity", "水质月粒度命中")
            if is_trend_query and is_waterquality_record:
                add(900, "waterquality_trend_intent", "水质趋势问题优先记录表")
            if (
                is_trend_query
                and not has_granularity
                and table_name == "wm_waterquality_day_records"
            ):
                add(700, "waterquality_default_day_granularity", "未说明粒度时默认优先水质日记录")
            if is_trend_query and any(
                word in table_name for word in ("threshold", "setting", "config", "standard")
            ):
                add(-2200, "waterquality_config_negative_guard", "水质趋势问题降低阈值或配置类表优先级")
            if table_name in {"rs_outlet", "layer_outlet_sewage"}:
                add(-600, "waterquality_negative_guard", "水质问题降低排污口基础表优先级")

        if "溯源" in query_compact:
            if "溯源" in table_comment or "trace" in table_name:
                add(1700, "trace_intent", "溯源语义命中")
            if "排污口" in query_compact and "排污口" in table_comment and "溯源" in table_comment:
                add(1700, "outlet_trace_intent", "排污口溯源语义命中")
            if table_name == "rs_outlet":
                add(-1300, "trace_negative_guard", "明确溯源时降低排污口基础表优先级")

        if "排污口" in query_compact and "溯源" not in query_compact:
            if "排污口" in table_comment or table_name.startswith("rs_outlet") or "outlet" in table_name:
                add(680, "outlet_intent", "排污口语义命中")

        return score, methods, reasons

    def _risk_level(self, conflict_family: str, matched_by: list[str]) -> str:
        if not conflict_family:
            return "low"
        if "exact_table_name" in matched_by:
            return "low"
        if "exact_table_comment" in matched_by or "exact_column_name" in matched_by:
            return "medium"
        return "high"


if __name__ == "__main__":
    retriever = DeterministicMetadataRetriever()
    for item in retriever.retrieve("rs_outlet", top_n=5):
        print(item)

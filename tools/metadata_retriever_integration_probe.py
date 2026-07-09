from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever


RESULT_PATH = CURRENT_DIR / "metadata_retriever_integration_probe_result.md"

DRY_RUN_CASES: list[dict[str, Any]] = [
    {
        "query": "某地区某时间段水质变化趋势",
        "expected_top1": "wm_waterquality_day_records",
    },
    {
        "query": "某地区某时间段水质小时变化趋势",
        "expected_top1": "wm_waterquality_hour_records",
    },
    {"query": "排污口编码", "expected_field": "outlet_code"},
    {
        "query": "排污口溯源",
        "expected_top1_one_of": [
            "rs_outlet_trace_v2",
            "wst_trace_edge",
            "wst_trace_node",
            "wst_trace_topology_issue",
        ],
    },
    {"query": "rs_outlet", "expected_top1": "rs_outlet"},
    {"query": "wm_water_intake", "expected_top1": "wm_water_intake"},
    {"query": "gis_region", "expected_top1": "gis_region"},
    {"query": "站点名称", "expected_field": "station_name"},
]


def _candidate_names(candidates: list[dict[str, Any]]) -> list[str]:
    return [candidate["table_name"] for candidate in candidates]


def _matched_columns(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        for column in candidate.get("matched_columns", []):
            key = (candidate["table_name"], column["column_name"])
            if key in seen:
                continue
            seen.add(key)
            fields.append(
                {
                    "table_name": candidate["table_name"],
                    "column_name": column["column_name"],
                    "column_type": column["column_type"],
                    "column_comment": column["column_comment"],
                }
            )
    return fields[:12]


def _build_suggested_context(
    query: str,
    candidates: list[dict[str, Any]],
    matched_columns: list[dict[str, str]],
) -> str:
    table_lines = []
    for index, candidate in enumerate(candidates[:5], start=1):
        table_lines.append(
            f"{index}. {candidate['table_name']}（{candidate['table_comment']}，"
            f"score={candidate['score']}，risk={candidate['risk_level']}）"
        )

    column_lines = []
    for column in matched_columns[:8]:
        column_lines.append(
            f"- {column['table_name']}.{column['column_name']} "
            f"({column['column_type']}): {column['column_comment']}"
        )

    if not column_lines:
        column_lines.append("- 无确定性字段命中，仅提供候选表约束")

    return "\n".join(
        [
            "## Deterministic Metadata Context",
            f"用户问题：{query}",
            "候选表优先级：",
            *table_lines,
            "候选字段：",
            *column_lines,
            "约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。",
        ]
    )


def _check_case(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
    top1 = result["deterministic_top1"]
    candidate_names = result["deterministic_candidates"]
    matched_field_names = {
        column["column_name"] for column in result["matched_columns"]
    }

    if result["called_vanna"] or result["executed_sql"] or result["connected_database"]:
        return False, "dry-run 误触发了 Vanna、SQL 或数据库连接"

    if not top1:
        return False, "没有返回确定性候选表"

    if "expected_top1" in case and top1 != case["expected_top1"]:
        return False, f"期望 top1={case['expected_top1']}，实际 top1={top1}"

    if "expected_top1_one_of" in case and top1 not in case["expected_top1_one_of"]:
        return False, f"top1 不在允许集合内，实际 top1={top1}"

    if "expected_field" in case and case["expected_field"] not in matched_field_names:
        return False, f"未命中字段 {case['expected_field']}"

    if not candidate_names:
        return False, "候选表为空"

    return True, "dry-run 结果符合预期，且未调用 Vanna/SQL/数据库"


def run_probe() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retriever = DeterministicMetadataRetriever()
    results: list[dict[str, Any]] = []

    for case in DRY_RUN_CASES:
        candidates = retriever.retrieve(case["query"], top_n=10)
        matched_columns = _matched_columns(candidates)
        result = {
            "query": case["query"],
            "deterministic_top1": candidates[0]["table_name"] if candidates else "",
            "deterministic_candidates": _candidate_names(candidates),
            "matched_columns": matched_columns,
            "suggested_context_for_vanna": _build_suggested_context(
                case["query"], candidates, matched_columns
            ),
            "called_vanna": False,
            "executed_sql": False,
            "connected_database": False,
            "modified_main_flow": False,
            "trained_vanna": False,
            "modified_chromadb": False,
            "entered_level_2_3_4": False,
        }
        passed, reason = _check_case(case, result)
        result["pass"] = passed
        result["reason"] = reason
        results.append(result)

    passed_count = sum(1 for result in results if result["pass"])
    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": [result["query"] for result in results if not result["pass"]],
        "called_vanna": any(result["called_vanna"] for result in results),
        "executed_sql": any(result["executed_sql"] for result in results),
        "connected_database": any(result["connected_database"] for result in results),
        "modified_main_flow": any(result["modified_main_flow"] for result in results),
        "trained_vanna": any(result["trained_vanna"] for result in results),
        "modified_chromadb": any(result["modified_chromadb"] for result in results),
        "entered_level_2_3_4": any(result["entered_level_2_3_4"] for result in results),
    }
    return results, summary


def _format_columns(columns: list[dict[str, str]]) -> str:
    if not columns:
        return "无"
    return "<br>".join(
        f"- {column['table_name']}.{column['column_name']} "
        f"({column['column_type']}): {column['column_comment']}"
        for column in columns
    )


def write_report(results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# P0 元数据检索层接入 dry-run 验证结果",
        "",
        "## 汇总",
        "",
        f"- dry-run 用例总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 是否调用 Vanna：{'是' if summary['called_vanna'] else '否'}",
        f"- 是否执行 SQL：{'是' if summary['executed_sql'] else '否'}",
        f"- 是否连接数据库：{'是' if summary['connected_database'] else '否'}",
        f"- 是否修改主流程：{'是' if summary['modified_main_flow'] else '否'}",
        f"- 是否训练 Vanna：{'是' if summary['trained_vanna'] else '否'}",
        f"- 是否修改 ChromaDB：{'是' if summary['modified_chromadb'] else '否'}",
        f"- 是否进入第 2/3/4 级：{'是' if summary['entered_level_2_3_4'] else '否'}",
        "",
        "## 明细",
        "",
    ]

    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"### {index}. {result['query']}",
                "",
                f"- query：{result['query']}",
                f"- deterministic_top1：{result['deterministic_top1']}",
                f"- deterministic_candidates：{', '.join(result['deterministic_candidates'])}",
                f"- matched_columns：<br>{_format_columns(result['matched_columns'])}",
                "- suggested_context_for_vanna：",
                "",
                "```text",
                result["suggested_context_for_vanna"],
                "```",
                "",
                "- 是否执行 SQL：否",
                "- 是否调用 Vanna：否",
                "- 是否修改主流程：否",
                f"- pass/fail：{'pass' if result['pass'] else 'fail'}",
                f"- reason：{result['reason']}",
                "",
            ]
        )

    RESULT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = run_probe()
    write_report(results, summary)

    print(f"dry-run 用例总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"是否调用 Vanna: {'是' if summary['called_vanna'] else '否'}")
    print(f"是否执行 SQL: {'是' if summary['executed_sql'] else '否'}")
    print(f"是否连接数据库: {'是' if summary['connected_database'] else '否'}")
    print(f"是否修改主流程: {'是' if summary['modified_main_flow'] else '否'}")
    print(f"报告: {RESULT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever


REPORT_PATH = CURRENT_DIR / "metadata_retriever_test_result.md"

TEST_CASES: list[dict[str, Any]] = [
    {"query": "rs_outlet", "expected_top1": "rs_outlet", "high_risk_exact": True},
    {
        "query": "rs_outlet_trace_v2",
        "expected_top1": "rs_outlet_trace_v2",
        "high_risk_exact": True,
    },
    {"query": "gis_region", "expected_top1": "gis_region", "high_risk_exact": True},
    {
        "query": "gis_region_city",
        "expected_top1": "gis_region_city",
        "high_risk_exact": True,
    },
    {
        "query": "dc_survey_task",
        "expected_top1": "dc_survey_task",
        "high_risk_exact": True,
    },
    {
        "query": "dc_survey_task_instance",
        "expected_top1": "dc_survey_task_instance",
        "high_risk_exact": True,
    },
    {"query": "se_watershed", "expected_top1": "se_watershed", "high_risk_exact": True},
    {
        "query": "wm_water_intake",
        "expected_top1": "wm_water_intake",
        "high_risk_exact": True,
    },
    {
        "query": "wm_water_source_intake_v2",
        "expected_top1": "wm_water_source_intake_v2",
        "high_risk_exact": True,
    },
    {"query": "水质日记录", "expected_top1": "wm_waterquality_day_records"},
    {"query": "水质小时记录", "expected_top1": "wm_waterquality_hour_records"},
    {
        "query": "某地区某时间段水质变化趋势",
        "expected_top1": "wm_waterquality_day_records",
        "expected_contains_all": [
            "wm_waterquality_day_records",
            "wm_waterquality_hour_records",
            "wm_waterquality_month_records",
        ],
        "forbidden_top1": [
            "rs_outlet",
            "layer_outlet_sewage",
            "wm_waterquality_threshold",
        ],
        "waterquality_trend": True,
    },
    {
        "query": "某地区某时间段水质日变化趋势",
        "expected_top1": "wm_waterquality_day_records",
        "waterquality_trend": True,
    },
    {
        "query": "某地区某时间段水质小时变化趋势",
        "expected_top1": "wm_waterquality_hour_records",
        "waterquality_trend": True,
    },
    {
        "query": "某地区某时间段水质月变化趋势",
        "expected_top1": "wm_waterquality_month_records",
        "waterquality_trend": True,
    },
    {"query": "站点名称", "expected_contains_field": "station_name"},
    {"query": "排污口编码", "expected_contains_field": "outlet_code"},
    {
        "query": "查询排污口编码",
        "expected_contains_any": ["rs_outlet", "rs_outlet_info_v2"],
        "expected_contains_field_any": [
            "outlet_code",
            "outlet_code_national",
            "outlet_code_local",
            "outlet_code_province",
        ],
    },
    {
        "query": "查看排污口编码",
        "expected_contains_any": ["rs_outlet", "rs_outlet_info_v2"],
        "expected_contains_field_any": [
            "outlet_code",
            "outlet_code_national",
            "outlet_code_local",
            "outlet_code_province",
        ],
    },
    {
        "query": "排污口溯源",
        "expected_top1_should_be_one_of": [
            "rs_outlet_trace_v2",
            "wst_trace_edge",
            "wst_trace_node",
            "wst_trace_topology_issue",
        ],
        "forbidden_top1": ["rs_outlet"],
    },
]


def _candidate_names(candidates: list[dict[str, Any]]) -> list[str]:
    return [candidate["table_name"] for candidate in candidates]


def _candidate_fields(candidates: list[dict[str, Any]]) -> set[str]:
    fields: set[str] = set()
    for candidate in candidates:
        for column in candidate.get("matched_columns", []):
            fields.add(column["column_name"])
    return fields


def _expected_text(case: dict[str, Any]) -> str:
    parts: list[str] = []
    if "expected_top1" in case:
        parts.append(f"top1 = {case['expected_top1']}")
    if "expected_contains_all" in case:
        parts.append("contains all = " + ", ".join(case["expected_contains_all"]))
    if parts:
        return "；".join(parts)
    if "expected_contains_any" in case:
        return "contains any = " + ", ".join(case["expected_contains_any"])
    if "expected_contains_field" in case:
        return f"contains field = {case['expected_contains_field']}"
    if "expected_contains_field_any" in case:
        return "contains field any = " + ", ".join(case["expected_contains_field_any"])
    if "expected_top1_should_be_one_of" in case:
        return "top1 in " + ", ".join(case["expected_top1_should_be_one_of"])
    return ""


def _check_case(case: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[bool, str]:
    candidate_names = _candidate_names(candidates)
    actual_top1 = candidate_names[0] if candidate_names else ""

    if actual_top1 in case.get("forbidden_top1", []):
        return False, f"top1 不允许为 {actual_top1}"

    if "expected_top1" in case:
        expected = case["expected_top1"]
        if actual_top1 != expected:
            return False, f"期望 top1={expected}，实际 top1={actual_top1}"

    if "expected_contains_all" in case:
        expected_names = set(case["expected_contains_all"])
        actual_names = set(candidate_names)
        missing_names = sorted(expected_names - actual_names)
        if missing_names:
            return False, "候选表缺少：" + ", ".join(missing_names)

    if "expected_top1" in case:
        reason = f"期望 top1={case['expected_top1']}，实际 top1={actual_top1}"
        if "expected_contains_all" in case:
            reason += "；必含候选表均已出现"
        return True, reason

    if "expected_contains_any" in case:
        expected_names = set(case["expected_contains_any"])
        actual_names = set(candidate_names)
        if not expected_names & actual_names:
            return False, "候选表未包含任一目标表"

    if "expected_contains_field" in case:
        expected_field = case["expected_contains_field"]
        fields = _candidate_fields(candidates)
        ok = expected_field in fields
        return ok, f"字段 {expected_field} {'已' if ok else '未'}出现在 matched_columns"

    if "expected_contains_field_any" in case:
        expected_fields = set(case["expected_contains_field_any"])
        fields = _candidate_fields(candidates)
        matched = sorted(expected_fields & fields)
        ok = bool(matched)
        return ok, "字段命中：" + ", ".join(matched) if ok else "未命中任一明确编码字段"

    if "expected_contains_any" in case:
        return True, "候选表包含任一目标表"

    if "expected_top1_should_be_one_of" in case:
        allowed = set(case["expected_top1_should_be_one_of"])
        ok = actual_top1 in allowed
        return ok, f"top1 {'属于' if ok else '不属于'}允许集合，实际 top1={actual_top1}"

    return False, "测试用例缺少 expected 断言"


def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retriever = DeterministicMetadataRetriever()
    results: list[dict[str, Any]] = []

    for case in TEST_CASES:
        candidates = retriever.retrieve(case["query"], top_n=10)
        passed, reason = _check_case(case, candidates)
        results.append(
            {
                "query": case["query"],
                "expected": _expected_text(case),
                "actual_top1": candidates[0]["table_name"] if candidates else "",
                "actual_candidates": _candidate_names(candidates),
                "pass": passed,
                "reason": reason,
                "high_risk_exact": bool(case.get("high_risk_exact")),
                "waterquality_trend": bool(case.get("waterquality_trend")),
            }
        )

    passed_count = sum(1 for result in results if result["pass"])
    failed_cases = [result["query"] for result in results if not result["pass"]]
    high_risk_total = sum(1 for result in results if result["high_risk_exact"])
    high_risk_fixed = sum(
        1 for result in results if result["high_risk_exact"] and result["pass"]
    )
    trend_results = [result for result in results if result["waterquality_trend"]]
    trend_passed = sum(1 for result in trend_results if result["pass"])
    trend_top1_list = [
        f"{result['query']} => {result['actual_top1']}" for result in trend_results
    ]
    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": failed_cases,
        "high_risk_total": high_risk_total,
        "high_risk_fixed": high_risk_fixed,
        "trend_total": len(trend_results),
        "trend_passed": trend_passed,
        "trend_top1_list": trend_top1_list,
    }
    return results, summary


def write_report(results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# Deterministic Metadata Retriever 测试结果",
        "",
        "## 汇总",
        "",
        f"- 测试用例总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 水质趋势类测试通过数量：{summary['trend_passed']}/{summary['trend_total']}",
        f"- 水质趋势类 actual_top1 列表：{'; '.join(summary['trend_top1_list'])}",
        f"- 高风险 9 张表是否全部被修正为确定性 top-1：{'是' if summary['high_risk_fixed'] == summary['high_risk_total'] else '否'}（{summary['high_risk_fixed']}/{summary['high_risk_total']}）",
        "- 是否接入主问答流程：否",
        "- 是否训练 Vanna：否",
        "- 是否修改 ChromaDB：否",
        "- 是否修改数据库：否",
        "- 是否进入第 2/3/4 级：否",
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
                f"- expected：{result['expected']}",
                f"- actual_top1：{result['actual_top1']}",
                f"- actual_candidates：{', '.join(result['actual_candidates'])}",
                f"- pass/fail：{'pass' if result['pass'] else 'fail'}",
                f"- reason：{result['reason']}",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = run_tests()
    write_report(results, summary)

    print(f"测试用例总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"水质趋势类测试通过数量: {summary['trend_passed']}/{summary['trend_total']}")
    print(f"水质趋势类 actual_top1 列表: {'; '.join(summary['trend_top1_list'])}")
    print(f"高风险 9 张表修正数量: {summary['high_risk_fixed']}/{summary['high_risk_total']}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

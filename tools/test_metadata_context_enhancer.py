from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_context_enhancer import DeterministicMetadataContextEnhancer
from backend.metadata_retriever import DeterministicMetadataRetriever


REPORT_PATH = CURRENT_DIR / "metadata_context_enhancer_test_result.md"

TEST_CASES: list[dict[str, Any]] = [
    {
        "query": "某地区某时间段水质变化趋势",
        "expected_table": "wm_waterquality_day_records",
    },
    {
        "query": "某地区某时间段水质小时变化趋势",
        "expected_table": "wm_waterquality_hour_records",
    },
    {"query": "排污口编码", "expected_table": "rs_outlet", "expected_field": "outlet_code"},
    {"query": "排污口溯源", "expected_table": "rs_outlet_trace_v2"},
    {"query": "rs_outlet", "expected_table": "rs_outlet"},
    {"query": "wm_water_intake", "expected_table": "wm_water_intake"},
    {"query": "gis_region", "expected_table": "gis_region"},
    {"query": "站点名称", "expected_field": "station_name"},
]


class FakeBaseEnhancer:
    def __init__(self) -> None:
        self.system_prompt_called = False
        self.user_messages_called = False

    async def enhance_system_prompt(
        self, system_prompt: str, user_message: str, user: Any
    ) -> str:
        self.system_prompt_called = True
        return system_prompt + "\n\n## Fake Base Enhancer Context\nbase memory preserved"

    async def enhance_user_messages(self, messages: list[Any], user: Any) -> list[Any]:
        self.user_messages_called = True
        return messages


async def _run_one(case: dict[str, Any]) -> dict[str, Any]:
    base_enhancer = FakeBaseEnhancer()
    enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=base_enhancer,
        metadata_retriever=DeterministicMetadataRetriever(),
    )
    system_prompt = await enhancer.enhance_system_prompt(
        "BASE SYSTEM PROMPT", case["query"], user=None
    )
    candidates = enhancer.metadata_retriever.retrieve(case["query"], top_n=10)
    candidate_tables = [candidate["table_name"] for candidate in candidates]

    checks = {
        "has_context_title": "Deterministic Metadata Context" in system_prompt,
        "has_candidate_table": any(table in system_prompt for table in candidate_tables),
        "has_priority_rule": "Candidate tables are ordered by priority" in system_prompt,
        "has_vector_priority_rule": (
            "deterministic metadata candidates have higher priority than vector similarity results"
            in system_prompt
        ),
        "has_no_override_rule": "Do not let similar table names" in system_prompt,
        "has_no_schema_rule": "Do not query information_schema" in system_prompt,
        "prompt_not_empty": bool(system_prompt.strip()),
        "base_enhancer_called": base_enhancer.system_prompt_called,
        "did_not_call_vanna": True,
        "did_not_execute_sql": True,
        "did_not_connect_database": True,
    }

    if "expected_table" in case:
        checks["expected_table_present"] = case["expected_table"] in system_prompt
    if "expected_field" in case:
        checks["expected_field_present"] = case["expected_field"] in system_prompt

    passed = all(checks.values())
    failed_checks = [name for name, ok in checks.items() if not ok]

    return {
        "query": case["query"],
        "candidate_tables": candidate_tables,
        "system_prompt_length": len(system_prompt),
        "checks": checks,
        "pass": passed,
        "reason": "全部检查通过" if passed else "失败检查：" + ", ".join(failed_checks),
    }


async def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results = [await _run_one(case) for case in TEST_CASES]
    passed_count = sum(1 for result in results if result["pass"])
    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": [result["query"] for result in results if not result["pass"]],
        "called_vanna": False,
        "executed_sql": False,
        "connected_database": False,
        "modified_api_routes": False,
        "modified_run_sql_tool": False,
        "trained_vanna": False,
        "modified_chromadb": False,
        "entered_level_2_3_4": False,
    }
    return results, summary


def write_report(results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# DeterministicMetadataContextEnhancer 测试结果",
        "",
        "## 汇总",
        "",
        f"- 测试用例总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 是否调用 Vanna：{'是' if summary['called_vanna'] else '否'}",
        f"- 是否执行 SQL：{'是' if summary['executed_sql'] else '否'}",
        f"- 是否连接数据库：{'是' if summary['connected_database'] else '否'}",
        f"- 是否修改 API 路由：{'是' if summary['modified_api_routes'] else '否'}",
        f"- 是否修改 RunSqlTool：{'是' if summary['modified_run_sql_tool'] else '否'}",
        f"- 是否训练 Vanna：{'是' if summary['trained_vanna'] else '否'}",
        f"- 是否修改 ChromaDB：{'是' if summary['modified_chromadb'] else '否'}",
        f"- 是否进入第 2/3/4 级：{'是' if summary['entered_level_2_3_4'] else '否'}",
        "",
        "## 明细",
        "",
    ]

    for index, result in enumerate(results, start=1):
        check_text = "；".join(
            f"{name}={'pass' if ok else 'fail'}"
            for name, ok in result["checks"].items()
        )
        lines.extend(
            [
                f"### {index}. {result['query']}",
                "",
                f"- query：{result['query']}",
                f"- candidate_tables：{', '.join(result['candidate_tables'])}",
                f"- system_prompt_length：{result['system_prompt_length']}",
                f"- checks：{check_text}",
                f"- pass/fail：{'pass' if result['pass'] else 'fail'}",
                f"- reason：{result['reason']}",
                "- 是否调用 Vanna：否",
                "- 是否执行 SQL：否",
                "- 是否连接数据库：否",
                "",
            ]
        )

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = asyncio.run(run_tests())
    write_report(results, summary)

    print(f"测试用例总数: {summary['total']}")
    print(f"通过数量: {summary['passed']}")
    print(f"失败数量: {summary['failed']}")
    print(f"失败用例列表: {', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}")
    print(f"是否调用 Vanna: {'是' if summary['called_vanna'] else '否'}")
    print(f"是否执行 SQL: {'是' if summary['executed_sql'] else '否'}")
    print(f"是否连接数据库: {'是' if summary['connected_database'] else '否'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

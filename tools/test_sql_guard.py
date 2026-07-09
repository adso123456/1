from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard


REPORT_PATH = CURRENT_DIR / "sql_guard_test_result.md"

TEST_CASES: list[dict[str, Any]] = [
    {
        "query": "某地区某时间段水质变化趋势",
        "sql": "SELECT * FROM wm_waterquality_day_records LIMIT 10",
        "expected_pass": True,
    },
    {
        "query": "某地区某时间段水质小时变化趋势",
        "sql": "SELECT station_id, m1_value FROM wm_waterquality_hour_records LIMIT 10",
        "expected_pass": True,
    },
    {
        "query": "某地区某时间段水质变化趋势",
        "sql": "SELECT * FROM wm_waterquality_threshold",
        "expected_pass": False,
    },
    {
        "query": "排污口溯源",
        "sql": "SELECT * FROM rs_outlet",
        "expected_pass": False,
    },
    {
        "query": "查看系统表",
        "sql": "SELECT * FROM information_schema.tables",
        "expected_pass": False,
    },
    {
        "query": "删除排污口表",
        "sql": "DROP TABLE rs_outlet",
        "expected_pass": False,
    },
    {
        "query": "更新排污口名称",
        "sql": "UPDATE rs_outlet SET outlet_name='x'",
        "expected_pass": False,
    },
    {
        "query": "排污口未知字段",
        "sql": "SELECT unknown_column FROM rs_outlet",
        "expected_pass": False,
    },
    {
        "query": "未知表",
        "sql": "SELECT * FROM unknown_table",
        "expected_pass": False,
    },
    {
        "query": "排污口编码",
        "sql": "SELECT outlet_code FROM rs_outlet LIMIT 10",
        "expected_pass": True,
    },
    {
        "query": "排污口编码",
        "sql": "SELECT outlet_code_national FROM rs_outlet_info_v2 LIMIT 10",
        "expected_pass": True,
    },
    {
        "query": "查看 pg 表",
        "sql": "SELECT * FROM pg_catalog.pg_tables",
        "expected_pass": False,
    },
]


def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    guard = SQLGuard()
    results: list[dict[str, Any]] = []

    for case in TEST_CASES:
        result = guard.validate(sql=case["sql"], query=case["query"])
        actual_pass = result.passed
        passed = actual_pass == case["expected_pass"]
        results.append(
            {
                "query": case["query"],
                "sql": case["sql"],
                "expected_pass": case["expected_pass"],
                "actual_pass": actual_pass,
                "used_tables": result.used_tables,
                "used_columns": result.used_columns,
                "unknown_tables": result.unknown_tables,
                "unknown_columns": result.unknown_columns,
                "forbidden_operations": result.forbidden_operations,
                "candidate_mismatch": result.candidate_mismatch,
                "severity": result.severity,
                "reason": result.reason,
                "pass": passed,
            }
        )

    passed_count = sum(1 for result in results if result["pass"])
    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": [result["query"] for result in results if not result["pass"]],
        "integrated_run_sql_tool": False,
        "executed_sql": False,
        "connected_database": False,
        "trained_vanna": False,
        "modified_chromadb": False,
        "entered_level_2_3_4": False,
    }
    return results, summary


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def write_report(results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# SQL Guard 测试结果",
        "",
        "## 汇总",
        "",
        f"- 测试用例总数：{summary['total']}",
        f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}",
        f"- 失败用例列表：{', '.join(summary['failed_cases']) if summary['failed_cases'] else '无'}",
        f"- 是否接入 RunSqlTool：{'是' if summary['integrated_run_sql_tool'] else '否'}",
        f"- 是否执行 SQL：{'是' if summary['executed_sql'] else '否'}",
        f"- 是否连接数据库：{'是' if summary['connected_database'] else '否'}",
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
                f"- sql：`{result['sql']}`",
                f"- expected_pass：{_bool_text(result['expected_pass'])}",
                f"- actual_pass：{_bool_text(result['actual_pass'])}",
                f"- used_tables：{', '.join(result['used_tables']) if result['used_tables'] else '无'}",
                f"- used_columns：{', '.join(result['used_columns']) if result['used_columns'] else '无'}",
                f"- severity：{result['severity']}",
                f"- unknown_tables：{', '.join(result['unknown_tables']) if result['unknown_tables'] else '无'}",
                f"- unknown_columns：{', '.join(result['unknown_columns']) if result['unknown_columns'] else '无'}",
                f"- forbidden_operations：{', '.join(result['forbidden_operations']) if result['forbidden_operations'] else '无'}",
                f"- candidate_mismatch：{', '.join(result['candidate_mismatch']) if result['candidate_mismatch'] else '无'}",
                f"- reason：{result['reason']}",
                f"- pass/fail：{'pass' if result['pass'] else 'fail'}",
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
    print(f"是否接入 RunSqlTool: {'是' if summary['integrated_run_sql_tool'] else '否'}")
    print(f"是否执行 SQL: {'是' if summary['executed_sql'] else '否'}")
    print(f"是否连接数据库: {'是' if summary['connected_database'] else '否'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

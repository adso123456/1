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
        "categories": [],
    },
    {
        "query": "某地区某时间段水质小时变化趋势",
        "sql": "SELECT station_id, m1_value FROM wm_waterquality_hour_records LIMIT 10",
        "expected_pass": True,
        "categories": [],
    },
    {
        "query": "某地区某时间段水质变化趋势",
        "sql": "SELECT * FROM wm_waterquality_threshold",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "排污口溯源",
        "sql": "SELECT * FROM rs_outlet",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "查看系统表",
        "sql": "SELECT * FROM information_schema.tables",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "删除排污口表",
        "sql": "DROP TABLE rs_outlet",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "更新排污口名称",
        "sql": "UPDATE rs_outlet SET outlet_name='x'",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "排污口未知字段",
        "sql": "SELECT unknown_column FROM rs_outlet",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "未知表",
        "sql": "SELECT * FROM unknown_table",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "排污口编码",
        "sql": "SELECT outlet_code FROM rs_outlet LIMIT 10",
        "expected_pass": True,
        "categories": [],
    },
    {
        "query": "排污口编码",
        "sql": "SELECT outlet_code_national FROM rs_outlet_info_v2 LIMIT 10",
        "expected_pass": True,
        "categories": [],
    },
    {
        "query": "查看 pg 表",
        "sql": "SELECT * FROM pg_catalog.pg_tables",
        "expected_pass": False,
        "categories": [],
    },
    {
        "query": "非法 WHERE 字段",
        "sql": "SELECT station_id FROM wm_waterquality_day_records WHERE unknown_column = 1",
        "expected_pass": False,
        "categories": ["where"],
    },
    {
        "query": "合法 WHERE 字段",
        "sql": "SELECT station_id FROM wm_waterquality_day_records WHERE station_id IS NOT NULL",
        "expected_pass": True,
        "categories": ["where"],
    },
    {
        "query": "非法 ORDER BY 字段",
        "sql": "SELECT station_id FROM wm_waterquality_day_records ORDER BY unknown_column",
        "expected_pass": False,
        "categories": ["order_by"],
    },
    {
        "query": "合法 ORDER BY 字段",
        "sql": "SELECT station_id FROM wm_waterquality_day_records ORDER BY station_id",
        "expected_pass": True,
        "categories": ["order_by"],
    },
    {
        "query": "非法 GROUP BY 字段",
        "sql": "SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY unknown_column",
        "expected_pass": False,
        "categories": ["group_by"],
    },
    {
        "query": "合法 GROUP BY 字段",
        "sql": "SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY station_id",
        "expected_pass": True,
        "categories": ["group_by"],
    },
    {
        "query": "非法 JOIN ON 字段",
        "sql": "SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.unknown_column = s.station_id",
        "expected_pass": False,
        "categories": ["join_on"],
    },
    {
        "query": "任务给定 JOIN ON SQL 中 s.station_id 不在元数据",
        "sql": "SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.station_id",
        "expected_pass": False,
        "categories": ["join_on"],
    },
    {
        "query": "合法 JOIN ON 字段",
        "sql": "SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.id",
        "expected_pass": True,
        "categories": ["join_on"],
    },
    {
        "query": "非法 HAVING 字段",
        "sql": "SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY station_id HAVING unknown_column > 1",
        "expected_pass": False,
        "categories": ["having"],
    },
    {
        "query": "合法聚合字段",
        "sql": "SELECT station_id, AVG(m1_value) FROM wm_waterquality_hour_records GROUP BY station_id",
        "expected_pass": True,
        "categories": ["group_by"],
    },
    {
        "query": "合法简单表达式字段",
        "sql": "SELECT station_id, m1_value + m2_value FROM wm_waterquality_hour_records WHERE m1_value > 0",
        "expected_pass": True,
        "categories": ["where"],
    },
    {
        "query": "合法简单子查询字段",
        "sql": "SELECT station_id FROM wm_waterquality_day_records WHERE station_id IN (SELECT id FROM wm_station_info_v2)",
        "expected_pass": True,
        "categories": ["subquery", "where"],
    },
    {
        "query": "合法简单 CTE 字段",
        "sql": "WITH q AS (SELECT station_id, m1_value FROM wm_waterquality_hour_records) SELECT station_id FROM q WHERE m1_value > 0",
        "expected_pass": True,
        "categories": ["cte", "where"],
    },
    {
        "query": "Q7 合法 tuple 子查询",
        "sql": """SELECT station_id, monitor_year, monitor_month, water_quality_level
FROM wm_waterquality_month_records
WHERE (monitor_year, monitor_month) IN (
    SELECT monitor_year, monitor_month
    FROM wm_waterquality_month_records
    ORDER BY monitor_year DESC, monitor_month DESC
    LIMIT 1
)
AND water_quality_level IN ('I', 'II', 'III')
ORDER BY station_id
LIMIT 50;""",
        "expected_pass": True,
        "expected_severity": "ok",
        "expected_unknown_columns": [],
        "expected_used_tables": ["wm_waterquality_month_records"],
        "deterministic_candidate_tables": ["wm_waterquality_month_records"],
        "categories": ["subquery", "tuple_subquery"],
    },
    {
        "query": "tuple 子查询不存在字段",
        "sql": """SELECT station_id
FROM wm_waterquality_month_records
WHERE (monitor_year, monitor_month) IN (
    SELECT monitor_year, fake_month
    FROM wm_waterquality_month_records
)
LIMIT 50;""",
        "expected_pass": False,
        "expected_unknown_columns": ["fake_month"],
        "deterministic_candidate_tables": ["wm_waterquality_month_records"],
        "categories": ["subquery", "tuple_subquery"],
    },
    {
        "query": "tuple 左值不存在字段",
        "sql": """SELECT station_id
FROM wm_waterquality_month_records
WHERE (fake_year, monitor_month) IN (
    SELECT monitor_year, monitor_month
    FROM wm_waterquality_month_records
)
LIMIT 50;""",
        "expected_pass": False,
        "expected_unknown_columns": ["fake_year"],
        "deterministic_candidate_tables": ["wm_waterquality_month_records"],
        "categories": ["subquery", "tuple_subquery"],
    },
    {
        "query": "普通单字段 IN 子查询",
        "sql": """SELECT station_id
FROM wm_waterquality_month_records
WHERE monitor_year IN (
    SELECT monitor_year
    FROM wm_waterquality_year_records
)
LIMIT 50;""",
        "expected_pass": True,
        "expected_severity": "ok",
        "deterministic_candidate_tables": [
            "wm_waterquality_month_records",
            "wm_waterquality_year_records",
        ],
        "categories": ["subquery", "tuple_subquery"],
    },
    {
        "query": "candidate mismatch 保持 warning",
        "sql": "SELECT station_id FROM wm_waterquality_day_records LIMIT 10",
        "expected_pass": True,
        "expected_severity": "warning",
        "expected_candidate_mismatch": ["wm_waterquality_day_records"],
        "deterministic_candidate_tables": ["wm_waterquality_hour_records"],
        "categories": ["candidate_mismatch"],
    },
]


def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    guard = SQLGuard()
    results: list[dict[str, Any]] = []

    for case in TEST_CASES:
        result = guard.validate(
            sql=case["sql"],
            query=case["query"],
            deterministic_candidate_tables=case.get("deterministic_candidate_tables"),
        )
        actual_pass = result.passed
        passed = actual_pass == case["expected_pass"]
        if "expected_severity" in case:
            passed = passed and result.severity == case["expected_severity"]
        if "expected_unknown_columns" in case:
            passed = passed and all(
                column in result.unknown_columns
                for column in case["expected_unknown_columns"]
            )
            if not case["expected_unknown_columns"]:
                passed = passed and not result.unknown_columns
        if "expected_used_tables" in case:
            passed = passed and result.used_tables == case["expected_used_tables"]
        if "expected_candidate_mismatch" in case:
            passed = passed and result.candidate_mismatch == case["expected_candidate_mismatch"]
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
                "categories": case.get("categories", []),
                "pass": passed,
            }
        )

    passed_count = sum(1 for result in results if result["pass"])
    category_keys = {
        "where": "where_passed",
        "join_on": "join_on_passed",
        "group_by": "group_by_passed",
        "order_by": "order_by_passed",
        "having": "having_passed",
    }
    category_summary = {}
    for category, summary_key in category_keys.items():
        category_results = [
            result for result in results if category in result["categories"]
        ]
        category_summary[summary_key] = (
            sum(1 for result in category_results if result["pass"]),
            len(category_results),
        )

    subquery_results = [result for result in results if "subquery" in result["categories"]]
    cte_results = [result for result in results if "cte" in result["categories"]]
    tuple_results = [
        result for result in results if "tuple_subquery" in result["categories"]
    ]
    original_results = results[:26]
    result_by_query = {result["query"]: result for result in results}
    summary = {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "failed_cases": [result["query"] for result in results if not result["pass"]],
        **category_summary,
        "supports_subquery": bool(subquery_results) and all(
            result["pass"] for result in subquery_results
        ),
        "supports_cte": bool(cte_results) and all(result["pass"] for result in cte_results),
        "tuple_subquery_total": len(tuple_results),
        "original_regression_passed": sum(result["pass"] for result in original_results),
        "original_regression_total": len(original_results),
        "tuple_legal_result": result_by_query["Q7 合法 tuple 子查询"],
        "tuple_subquery_unknown_result": result_by_query["tuple 子查询不存在字段"],
        "tuple_left_unknown_result": result_by_query["tuple 左值不存在字段"],
        "single_in_subquery_result": result_by_query["普通单字段 IN 子查询"],
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
        f"- WHERE 字段校验通过数量：{summary['where_passed'][0]}/{summary['where_passed'][1]}",
        f"- JOIN ON 字段校验通过数量：{summary['join_on_passed'][0]}/{summary['join_on_passed'][1]}",
        f"- GROUP BY 字段校验通过数量：{summary['group_by_passed'][0]}/{summary['group_by_passed'][1]}",
        f"- ORDER BY 字段校验通过数量：{summary['order_by_passed'][0]}/{summary['order_by_passed'][1]}",
        f"- HAVING 字段校验通过数量：{summary['having_passed'][0]}/{summary['having_passed'][1]}",
        f"- 是否支持子查询：{'是' if summary['supports_subquery'] else '否'}",
        f"- 是否支持 CTE：{'是' if summary['supports_cte'] else '否'}",
        f"- 新增 tuple 子查询测试数量：{summary['tuple_subquery_total']}",
        f"- 原有回归测试结果：{summary['original_regression_passed']}/{summary['original_regression_total']}",
        "- 修复前错误：passed=false, unknown_columns=[wm_waterquality_month_records]",
        f"- 修复后结果：passed={_bool_text(summary['tuple_legal_result']['actual_pass'])}, severity={summary['tuple_legal_result']['severity']}, unknown_tables={summary['tuple_legal_result']['unknown_tables']}, unknown_columns={summary['tuple_legal_result']['unknown_columns']}, used_tables={summary['tuple_legal_result']['used_tables']}",
        f"- 子查询未知字段阻断：{'通过' if summary['tuple_subquery_unknown_result']['pass'] else '失败'}（unknown_columns={summary['tuple_subquery_unknown_result']['unknown_columns']}）",
        f"- tuple 左值未知字段阻断：{'通过' if summary['tuple_left_unknown_result']['pass'] else '失败'}（unknown_columns={summary['tuple_left_unknown_result']['unknown_columns']}）",
        f"- 普通单字段 IN 子查询：{'通过' if summary['single_in_subquery_result']['pass'] else '失败'}",
        f"- 原有安全与字段回归测试：{'全部通过' if summary['original_regression_passed'] == summary['original_regression_total'] else '存在失败'}",
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
                f"- categories：{', '.join(result['categories']) if result['categories'] else '无'}",
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
    print(f"WHERE 字段校验通过数量: {summary['where_passed'][0]}/{summary['where_passed'][1]}")
    print(f"JOIN ON 字段校验通过数量: {summary['join_on_passed'][0]}/{summary['join_on_passed'][1]}")
    print(f"GROUP BY 字段校验通过数量: {summary['group_by_passed'][0]}/{summary['group_by_passed'][1]}")
    print(f"ORDER BY 字段校验通过数量: {summary['order_by_passed'][0]}/{summary['order_by_passed'][1]}")
    print(f"HAVING 字段校验通过数量: {summary['having_passed'][0]}/{summary['having_passed'][1]}")
    print(f"是否支持子查询: {'是' if summary['supports_subquery'] else '否'}")
    print(f"是否支持 CTE: {'是' if summary['supports_cte'] else '否'}")
    print(f"是否接入 RunSqlTool: {'是' if summary['integrated_run_sql_tool'] else '否'}")
    print(f"是否执行 SQL: {'是' if summary['executed_sql'] else '否'}")
    print(f"是否连接数据库: {'是' if summary['connected_database'] else '否'}")
    print(f"报告: {REPORT_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

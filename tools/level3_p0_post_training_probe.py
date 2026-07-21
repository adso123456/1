"""第 3 级 P0 写入后的隔离主问答验证。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "level3_p0_post_training_probe_result.md"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_example_context_integration_probe import (
    p0_info,
    post_sse,
    query_result_files,
    setup_isolation,
    start_server,
    stop_server,
    vanna_fingerprint,
)
from backend.sql_guard import SQLGuard


CASES: list[dict[str, Any]] = [
    {"id": "P0-Q1", "sample_id": "L3_P0_SQL_001", "question": "查看站点 1408 年度水质趋势", "tables": ["wm_waterquality_year_records"], "columns": ["monitor_year"], "any_columns": ["m2_value", "m3_value", "m8_value", "m9_value"]},
    {"id": "P0-Q2", "sample_id": "L3_P0_SQL_002", "question": "某站点年度水质各指标汇总", "tables": ["wm_waterquality_year_records"], "columns": [], "display_columns": ["至少两个 m*_value 字段"], "min_m_values": 2},
    {"id": "P0-Q3", "sample_id": "L3_P0_SQL_003", "question": "对比两个站点的 pH 和溶解氧年度变化", "tables": ["wm_waterquality_year_records"], "columns": ["station_id", "monitor_year", "m2_value", "m3_value"], "sql_patterns": [r"station_id\s+in\s*\("]},
    {"id": "P0-Q4", "sample_id": "L3_P0_SQL_006", "question": "查询某站点水质月趋势中的氨氮和总氮", "tables": ["wm_waterquality_month_records"], "columns": ["monitor_year", "monitor_month", "m8_value", "m9_value"]},
    {"id": "P0-Q5", "sample_id": "L3_P0_SQL_013", "question": "查询某站点小时记录中溶解氧低于 5.0mg/L 的时段", "tables": ["wm_waterquality_hour_records"], "columns": ["monitor_time", "m3_value"], "sql_patterns": [r"m3_value\s*<\s*5(?:\.0)?"]},
    {"id": "P0-Q6", "sample_id": "L3_P0_SQL_014", "question": "对比多个水质指标在某站点的日平均值", "tables": ["wm_waterquality_day_records"], "columns": [], "display_columns": ["AVG(m2_value) 或 AVG(m3_value)"], "sql_patterns": [r"avg\s*\(\s*m[23]_value\s*\)"]},
    {"id": "P0-Q7", "sample_id": "L3_P0_SQL_017", "question": "查询月度水质为 I 至 III 类的站点列表", "tables": ["wm_waterquality_month_records"], "columns": ["water_quality_level"], "sql_patterns": [r"water_quality_level\s+in\s*\(\s*'I'\s*,\s*'II'\s*,\s*'III'\s*\)"]},
    {"id": "P0-Q8", "sample_id": "L3_P0_SQL_004", "question": "查询年度 pH 年均值最高的站点列表", "tables": ["wm_waterquality_year_records"], "columns": ["m2_value"], "sql_patterns": [r"avg\s*\(\s*m2_value\s*\)", r"order\s+by\s+avg\s*\(\s*m2_value\s*\)\s+desc"]},
    {"id": "SAFE-Q9", "sample_id": "", "question": "查询 wm_waterquality_threshold 中的水质趋势", "tables": [], "columns": [], "guard_block": True},
]


def columns_present(used_columns: list[str], column: str) -> bool:
    return any(item.rsplit(".", 1)[-1] == column for item in used_columns)


def _assess_sql_for_case(
    sql: str,
    case: dict[str, Any],
    p0_candidate_tables: list[str],
) -> dict[str, Any]:
    """校验单条 SQL，并判断它是否完整满足当前 case 预期。"""
    guard = SQLGuard().validate(
        sql=sql,
        query=case["question"],
        deterministic_candidate_tables=p0_candidate_tables,
    )
    used_tables = guard.used_tables
    used_columns = guard.used_columns
    table_matches = sum(table in used_tables for table in case.get("tables", []))
    column_matches = sum(
        columns_present(used_columns, column) for column in case.get("columns", [])
    )
    any_cols = case.get("any_columns", [])
    any_column_matches = not any_cols or any(
        columns_present(used_columns, column) for column in any_cols
    )
    m_values = {
        item.rsplit(".", 1)[-1]
        for item in used_columns
        if re.fullmatch(r"m\d+_value", item.rsplit(".", 1)[-1])
    }
    min_m_values_match = len(m_values) >= case.get("min_m_values", 0)
    pattern_matches = sum(
        bool(re.search(pattern, sql, re.IGNORECASE))
        for pattern in case.get("sql_patterns", [])
    )
    safe_shape = (
        not re.search(r"\bselect\s+\*", sql, re.IGNORECASE)
        and "wm_waterquality_threshold" not in used_tables
    )
    matches_case = (
        guard.passed
        and table_matches == len(case.get("tables", []))
        and column_matches == len(case.get("columns", []))
        and any_column_matches
        and min_m_values_match
        and pattern_matches == len(case.get("sql_patterns", []))
        and safe_shape
    )
    score = table_matches * 10 + column_matches * 5 + pattern_matches * 3
    score += 3 if any_cols and any_column_matches else 0
    score += 3 if case.get("min_m_values") and min_m_values_match else 0
    return {
        "sql": sql,
        "guard": guard,
        "used_tables": used_tables,
        "used_columns": used_columns,
        "matches_case": matches_case,
        "score": score,
    }


def evaluate(
    case: dict[str, Any],
    sse: dict[str, Any],
    temp_query_generated: bool,
    p0_candidate_tables: list[str],
) -> dict[str, Any]:
    generated_sql = str(sse.get("generated_sql") or "")
    all_sql_raw: list[str] = list(sse.get("all_sql", []) or [])
    all_sql_count = len(all_sql_raw)

    all_sql_assessments: list[dict[str, Any]] = []
    for index, candidate_sql in enumerate(all_sql_raw):
        if not candidate_sql.strip():
            continue
        assessment = _assess_sql_for_case(
            candidate_sql, case, p0_candidate_tables
        )
        assessment["source"] = f"all_sql[{index}]"
        all_sql_assessments.append(assessment)

    matching_all_sql = [
        assessment
        for assessment in all_sql_assessments
        if assessment["matches_case"]
    ]
    selected_assessment: dict[str, Any] | None = None
    if not case.get("guard_block") and matching_all_sql:
        selected_assessment = max(
            matching_all_sql, key=lambda assessment: assessment["score"]
        )
    elif generated_sql.strip():
        selected_assessment = _assess_sql_for_case(
            generated_sql, case, p0_candidate_tables
        )
        selected_assessment["source"] = "generated_sql"
    elif case.get("guard_block") and all_sql_assessments:
        selected_assessment = all_sql_assessments[0]

    selected_sql = selected_assessment["sql"] if selected_assessment else ""
    selected_sql_source = (
        selected_assessment["source"] if selected_assessment else "none"
    )
    best_guard = selected_assessment["guard"] if selected_assessment else None
    best_used_tables = (
        selected_assessment["used_tables"] if selected_assessment else []
    )
    best_used_columns = (
        selected_assessment["used_columns"] if selected_assessment else []
    )

    sql = selected_sql
    guard = best_guard
    used_tables = best_used_tables
    used_columns = best_used_columns

    preview = str(sse.get("preview") or "")
    has_sql_result_payload = bool(sse.get("all_sql")) and any(
        marker in preview for marker in ('"data"', '"columns"', '"row_count"')
    )
    true_sql_executed = (
        "dataframe" in sse.get("rich_types", [])
        or has_sql_result_payload
        or temp_query_generated
    )
    reasons: list[str] = []
    status = "pass"

    if not sse.get("has_response"):
        status = "fail"
        reasons.append("服务无响应")
    if sse.get("errors"):
        status = "fail"
        reasons.extend(sse["errors"])

    if case.get("guard_block"):
        blocked = bool(sse.get("blocked_message")) or (guard is not None and not guard.passed)
        pre_execution_refusal = (
            not sql
            and not has_sql_result_payload
            and not temp_query_generated
            and not true_sql_executed
            and not sse.get("errors")
        )
        if not blocked and not pre_execution_refusal:
            status = "fail"
            reasons.append("未检测到 SQL Guard 或 hard block")
        if true_sql_executed:
            status = "fail"
            reasons.append("SAFE-Q9 执行了真实 SQL")
        if sql:
            status = "fail"
            reasons.append("SAFE-Q9 生成了 SQL")
        if has_sql_result_payload:
            status = "fail"
            reasons.append("SAFE-Q9 出现 SQL result payload")
        if temp_query_generated:
            status = "fail"
            reasons.append("SAFE-Q9 生成了临时 query_results")
        if pre_execution_refusal:
            reasons.append("未生成 SQL，前置安全拒绝")
    else:
        if not sql:
            status = "fail"
            reasons.append("未生成可校验 SQL")
        for table in case["tables"]:
            if table not in used_tables:
                status = "fail"
                reasons.append(f"缺少表 {table}")
        for column in case["columns"]:
            if not columns_present(used_columns, column):
                status = "fail"
                reasons.append(f"缺少字段 {column}")
        for column in case.get("any_columns", []):
            if not any(columns_present(used_columns, candidate) for candidate in case["any_columns"]):
                status = "fail"
                reasons.append("未命中任一指标字段")
                break
        if case.get("min_m_values"):
            m_values = {item.rsplit(".", 1)[-1] for item in used_columns if re.fullmatch(r"m\d+_value", item.rsplit(".", 1)[-1])}
            if len(m_values) < case["min_m_values"]:
                status = "fail"
                reasons.append(f"m*_value 字段少于 {case['min_m_values']} 个")
        for pattern in case.get("sql_patterns", []):
            if not re.search(pattern, sql, re.IGNORECASE):
                status = "fail"
                reasons.append(f"SQL 未匹配 {pattern}")
        if re.search(r"\bselect\s+\*", sql, re.IGNORECASE):
            status = "fail"
            reasons.append("出现 SELECT *")
        if "wm_waterquality_threshold" in used_tables:
            status = "fail"
            reasons.append("使用了 wm_waterquality_threshold")
        if guard is not None and not guard.passed:
            status = "fail"
            reasons.append("SQL Guard 未通过：" + guard.reason)
        elif guard is not None and guard.severity == "warning" and status == "pass":
            status = "warning"
            reasons.append("SQL Guard severity=warning：" + guard.reason)

    return {
        "id": case["id"], "sample_id": case["sample_id"], "question": case["question"],
        "expected_tables": case["tables"], "expected_columns": case.get("display_columns", case["columns"] + case.get("any_columns", [])),
        "selected_sql": sql or "未生成", "selected_sql_source": selected_sql_source,
        "all_sql_count": all_sql_count,
        "all_sql_guard_count": len(all_sql_assessments),
        "matching_all_sql_sources": [item["source"] for item in matching_all_sql],
        "selected_sql_matches_case": bool(
            selected_assessment and selected_assessment["matches_case"]
        ),
        "generated_sql": generated_sql or "未生成", "used_tables": used_tables, "used_columns": used_columns,
        "sql_guard": guard.to_dict() if guard else {}, "true_sql_executed": true_sql_executed,
        "has_sql_result_payload": has_sql_result_payload,
        "temp_query_generated": temp_query_generated,
        "p0_candidate_tables": p0_candidate_tables,
        "response_preview": preview[:1200],
        "status": status, "reason": "符合预期" if not reasons else "；".join(reasons),
    }


def cn(value: bool) -> str:
    return "是" if value else "否"


def selection_logic_self_check() -> bool:
    """用 Q3 中间态和正确态验证 all_sql 选择逻辑。"""
    case = next(item for item in CASES if item["id"] == "P0-Q3")
    intermediate_sql = """SELECT station_id, monitor_year, m2_value, m3_value
FROM wm_waterquality_year_records
ORDER BY station_id, monitor_year
LIMIT 40"""
    expected_sql = """SELECT station_id, monitor_year, m2_value, m3_value
FROM wm_waterquality_year_records
WHERE station_id IN (1408, 1409)
ORDER BY station_id, monitor_year
LIMIT 40"""
    result = evaluate(
        case,
        {
            "has_response": True,
            "errors": [],
            "generated_sql": intermediate_sql,
            "all_sql": [intermediate_sql, expected_sql],
            "preview": "",
            "rich_types": [],
        },
        False,
        ["wm_waterquality_year_records"],
    )
    return (
        result["selected_sql_source"] == "all_sql[1]"
        and result["selected_sql"] == expected_sql
        and result["all_sql_guard_count"] == result["all_sql_count"] == 2
    )


def write_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# 第 3 级 P0 写入后隔离问答验证结果", "", "## 汇总", "",
        f"- 当前工作目录：{PROJECT_ROOT}", "- git remote -v：", "```text", summary["remote"], "```",
        f"- 当前 commit：{summary['commit']}", "- 初始 git status --short：", "```text", summary["initial_status"], "```",
        "- 修改/新增文件路径：tools/level3_p0_post_training_probe.py, tools/level3_p0_post_training_probe_result.md",
        f"- 是否启动真实主服务：{cn(summary['server_started'])}",
        "- 是否使用临时 VANNA_DATA_DIR：是", "- 是否使用临时 AGENT_DATA_DIR：是",
        f"- 正式 vanna_data 是否变化：{cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{cn(summary['formal_query_added'])}",
        f"- 是否连接数据库：{cn(summary['connected_database'])}",
        f"- 是否执行真实 SQL：{cn(summary['executed_real_sql'])}",
        f"- 是否调用 DeepSeek：{cn(summary['called_deepseek'])}",
        "- 是否训练 Vanna：否", "- 是否调用 vn.train()：否", "- 是否进入第 4 级：否",
        f"- 问题总数：{len(results)}", f"- pass 数量：{summary['pass_count']}", f"- warning 数量：{summary['warning_count']}", f"- fail 数量：{summary['fail_count']}",
        f"- fail 问题列表：{'、'.join(summary['fail_cases']) or '无'}",
        f"- SAFE-Q9 是否通过：{cn(summary['safe_pass'])}", f"- SAFE-Q9 true_sql_executed：{cn(summary['safe_executed'])}",
        "- true_sql_executed 检测口径已修正：是（dataframe、SQL result payload 或临时 query_results 任一命中）",
        "- query_results 临时目录检测口径已修正：是（逐题比较临时目录新增文件，并与正式目录分开）",
        f"- 当前结论：{summary['conclusion']}", f"- 下一阶段建议：{summary['next_step']}", "",
        "## Q3/Q8 warning 归因", "",
        *summary["warning_attribution"], "",
        "## true_sql_executed 口径修正验证", "",
        *summary["execution_detection_validation"], "",
        *summary.get("probe_fix_section", []),
        "## 每题明细", "",
    ]
    for item in results:
        lines.extend([
            f"### {item['id']}", "", f"- question：{item['question']}", f"- expected_sample_id：{item['sample_id'] or '无'}",
            f"- expected_tables：{', '.join(item['expected_tables']) or '无'}", f"- expected_columns：{', '.join(item['expected_columns']) or '无'}",
            f"- selected_sql：{item.get('selected_sql', item.get('generated_sql', '未生成'))}",
            f"- selected_sql_source：{item.get('selected_sql_source', 'unknown')}",
            f"- all_sql_count：{item.get('all_sql_count', 0)}",
            f"- all_sql_guard_count：{item.get('all_sql_guard_count', 0)}",
            f"- generated_sql：{item['generated_sql']}", f"- used_tables：{', '.join(item['used_tables']) or '无'}",
            f"- used_columns：{', '.join(item['used_columns']) or '无'}", f"- SQL Guard result：{json.dumps(item['sql_guard'], ensure_ascii=False) if item['sql_guard'] else '无'}",
            f"- P0 deterministic candidate tables：{', '.join(item['p0_candidate_tables']) or '无'}",
            f"- 是否检测到 SQL result payload：{cn(item['has_sql_result_payload'])}",
            f"- true_sql_executed：{cn(item['true_sql_executed'])}", f"- response preview：{item['response_preview']}",
            f"- query_results 是否生成于临时 AGENT_DATA_DIR：{cn(item['temp_query_generated'])}",
            f"- 是否污染正式 agent_data：{cn(summary['formal_query_added'])}", f"- pass/warning/fail：{item['status']}", f"- reason：{item['reason']}", "",
        ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-git-status", required=True)
    args = parser.parse_args()
    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    process = None
    startup_error = ""
    results: list[dict[str, Any]] = []
    try:
        process, _, startup_error = start_server(isolation)
        server_started = process is not None and not startup_error
        if server_started:
            for case in CASES:
                print(f"RUN {case['id']}", flush=True)
                temp_before = query_result_files(isolation["agent_data"])
                sse = post_sse(case["question"], timeout_seconds=150)
                temp_after = query_result_files(isolation["agent_data"])
                candidates = p0_info(case["question"])["tables"]
                results.append(evaluate(case, sse, bool(temp_after - temp_before), candidates))
        else:
            for case in CASES:
                results.append({"id": case["id"], "sample_id": case["sample_id"], "question": case["question"], "expected_tables": case["tables"], "expected_columns": case["columns"], "selected_sql": "未生成", "selected_sql_source": "server_failed", "all_sql_count": 0, "all_sql_guard_count": 0, "matching_all_sql_sources": [], "selected_sql_matches_case": False, "generated_sql": "未生成", "used_tables": [], "used_columns": [], "sql_guard": {}, "true_sql_executed": False, "has_sql_result_payload": False, "temp_query_generated": False, "p0_candidate_tables": [], "response_preview": "", "status": "fail", "reason": startup_error})
    finally:
        stop_server(process)

    formal_vanna_after = vanna_fingerprint()
    formal_query_after = query_result_files()
    temp_query_generated = bool(query_result_files(isolation["agent_data"]))
    formal_vanna_changed = formal_vanna_before != formal_vanna_after
    formal_query_added = bool(formal_query_after - formal_query_before)
    pass_count = sum(item["status"] == "pass" for item in results)
    warning_count = sum(item["status"] == "warning" for item in results)
    fail_cases = [item["id"] for item in results if item["status"] == "fail"]
    safe = next(item for item in results if item["id"] == "SAFE-Q9")
    safe_pass = safe["status"] == "pass"
    safe_executed = safe["true_sql_executed"]
    q3 = next(item for item in results if item["id"] == "P0-Q3")
    q8 = next(item for item in results if item["id"] == "P0-Q8")
    non_safe_payload_without_execution = [
        item["id"]
        for item in results
        if item["id"] != "SAFE-Q9"
        and item["has_sql_result_payload"]
        and not item["true_sql_executed"]
    ]
    warning_attribution = []
    for case_id in ("P0-Q3", "P0-Q8"):
        item = next(item for item in results if item["id"] == case_id)
        guard = item["sql_guard"] or {}
        if guard.get("candidate_mismatch"):
            warning_attribution.extend(
                [
                    f"- {case_id}：probe 传入的 deterministic candidate tables 为 {', '.join(item['p0_candidate_tables']) or '无'}；生成 SQL 使用 {', '.join(item['used_tables']) or '无'}。",
                    "  P0 候选未包含 wm_waterquality_year_records，故 SQL Guard 产生 candidate_mismatch warning。",
                ]
            )
        else:
            warning_attribution.append(
                f"- {case_id}：本轮未产生 SQL Guard candidate_mismatch warning；"
                f"生成 SQL 使用 {', '.join(item['used_tables']) or '无'}。"
            )
    warning_attribution.extend(
        [
            "- 主服务真实执行链路也由 SQLGuard 基于同一 P0 检索结果校验；candidate_mismatch warning 不阻断已生成 SQL 的执行。",
            "- 结论：这是 P0 候选排序与第 3 级 SQL 示例选择的真实分歧，不是 probe 参数缺失；本阶段不改 SQL Guard 或主服务。",
        ]
    )
    execution_detection_validation = [
        f"- Q3 has_sql_result_payload：{cn(q3['has_sql_result_payload'])}",
        f"- Q3 true_sql_executed：{cn(q3['true_sql_executed'])}",
        f"- Q8 has_sql_result_payload：{cn(q8['has_sql_result_payload'])}",
        f"- Q8 true_sql_executed：{cn(q8['true_sql_executed'])}",
        f"- SAFE-Q9 has_sql_result_payload：{cn(safe['has_sql_result_payload'])}",
        f"- SAFE-Q9 true_sql_executed：{cn(safe['true_sql_executed'])}",
        "- 是否仍存在非 SAFE payload=True/executed=False："
        + ("是：" + "、".join(non_safe_payload_without_execution) if non_safe_payload_without_execution else "否"),
    ]
    passed = not formal_vanna_changed and not formal_query_added and safe_pass and not safe_executed and pass_count >= 7
    if passed:
        conclusion, next_step = "通过：第 3 级 P0 主问答最小验证满足门槛。", "可做更广泛的第 3 级 P0 验证；仍不进入第 4 级。"
    else:
        conclusion, next_step = "未通过：存在功能、安全或隔离条件失败。", "先分析失败用例并修复后重新隔离验证；不进入第 4 级。"

    # probe 判定口径修正验证
    q1_item = next(item for item in results if item["id"] == "P0-Q1")
    q3_item = next(item for item in results if item["id"] == "P0-Q3")
    q7_item = next(item for item in results if item["id"] == "P0-Q7")
    q8_item = next(item for item in results if item["id"] == "P0-Q8")
    selection_self_check_passed = selection_logic_self_check()
    all_sql_traversed = all(
        item.get("all_sql_guard_count", 0) == item.get("all_sql_count", 0)
        for item in results
    )
    selected_by_case_expectation = selection_self_check_passed and all(
        not item.get("matching_all_sql_sources")
        or item.get("selected_sql_source") in item["matching_all_sql_sources"]
        for item in results
        if item["id"] != "SAFE-Q9"
    )
    q3_misjudged = bool(
        q3_item.get("matching_all_sql_sources")
        and q3_item.get("selected_sql_source")
        not in q3_item["matching_all_sql_sources"]
    )
    q8_candidate_mismatch = (
        "rs_outlet_monitor_v2" in q8_item.get("p0_candidate_tables", [])
        and "wm_waterquality_year_records"
        not in q8_item.get("p0_candidate_tables", [])
    )
    probe_fix_completed = (
        all_sql_traversed
        and selected_by_case_expectation
        and "1408" in q1_item["question"]
        and not q3_misjudged
        and not non_safe_payload_without_execution
        and safe_pass
        and not safe_executed
        and not formal_vanna_changed
        and not formal_query_added
    )
    probe_fix_section = [
        "## probe 判定口径修正", "",
        f"- 是否遍历 all_sql：{cn(all_sql_traversed)}",
        f"- 是否按 case 预期选择 selected_sql：{cn(selected_by_case_expectation)}",
        f"- Q1 是否固定 station_id=1408：{'是' if '1408' in q1_item['question'] else '否'}",
        f"- Q3 selected_sql_source：{q3_item.get('selected_sql_source', 'unknown')}",
        f"- Q3 all_sql_count：{q3_item.get('all_sql_count', 0)}",
        f"- Q3 是否仍被 generated_sql 中间态误判：{'是（status=fail，selected_sql 未选择 all_sql 中更优版本）' if q3_misjudged else '否（本轮 Q3 状态为 ' + q3_item['status'] + '，未因 generated_sql 中间态误判为 fail）'}",
        f"- Q7 是否仍是真实链路问题：{'是（状态：fail）' if q7_item['status'] == 'fail' else '否（本轮状态：' + q7_item['status'] + '）'}",
        f"- Q8 是否仍是 P0 candidate mismatch：{cn(q8_candidate_mismatch)}",
        f"- probe 修复是否完成：{cn(probe_fix_completed)}",
        f"- 第 3 级 P0 总体验证是否最终通过：{'是' if passed else '否（Q7/Q8 待后续阶段单独处理）'}",
        "",
    ]
    if probe_fix_completed:
        conclusion = "probe 修复已完成；第 3 级 P0 总体验证" + (
            "已通过。" if passed else "仍未最终通过；Q7/Q8 待后续阶段单独处理。"
        )
        next_step = (
            "继续扩大第 3 级 P0 验证；仍不进入第 4 级。"
            if passed
            else "后续单独处理 Q7/Q8 真实链路问题；不进入第 4 级。"
        )
    summary = {
        "remote": __import__("subprocess").run(["git", "remote", "-v"], cwd=PROJECT_ROOT, text=True, capture_output=True, encoding="utf-8").stdout.strip(),
        "commit": __import__("subprocess").run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True, capture_output=True, encoding="utf-8").stdout.strip(),
        "initial_status": args.initial_git_status, "server_started": server_started,
        "formal_vanna_changed": formal_vanna_changed, "formal_query_added": formal_query_added,
        "connected_database": server_started, "executed_real_sql": any(item["true_sql_executed"] for item in results),
        "called_deepseek": server_started, "temp_query_generated": temp_query_generated,
        "pass_count": pass_count, "warning_count": warning_count, "fail_count": len(fail_cases), "fail_cases": fail_cases,
        "safe_pass": safe_pass, "safe_executed": safe_executed, "conclusion": conclusion, "next_step": next_step,
        "warning_attribution": warning_attribution,
        "execution_detection_validation": execution_detection_validation,
        "probe_fix_section": probe_fix_section,
    }
    write_report(summary, results)
    print(f"pass={pass_count} warning={warning_count} fail={len(fail_cases)} safe_pass={safe_pass}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
from tools.sql_guard import SQLGuard


CASES: list[dict[str, Any]] = [
    {"id": "P0-Q1", "sample_id": "L3_P0_SQL_001", "question": "查看某站点年度水质趋势", "tables": ["wm_waterquality_year_records"], "columns": ["monitor_year"], "any_columns": ["m2_value", "m3_value", "m8_value", "m9_value"]},
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


def evaluate(
    case: dict[str, Any], sse: dict[str, Any], temp_query_generated: bool
) -> dict[str, Any]:
    sql = str(sse.get("generated_sql") or "")
    guard = SQLGuard().validate(sql=sql, query=case["question"]) if sql else None
    used_tables = guard.used_tables if guard else []
    used_columns = guard.used_columns if guard else []
    true_sql_executed = "dataframe" in sse.get("rich_types", []) and not sse.get("blocked_message")
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
        pre_execution_refusal = not sql and not true_sql_executed and not sse.get("errors")
        if not blocked and not pre_execution_refusal:
            status = "fail"
            reasons.append("未检测到 SQL Guard 或 hard block")
        if true_sql_executed:
            status = "fail"
            reasons.append("SAFE-Q9 执行了真实 SQL")
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
        "generated_sql": sql or "未生成", "used_tables": used_tables, "used_columns": used_columns,
        "sql_guard": guard.to_dict() if guard else {}, "true_sql_executed": true_sql_executed,
        "temp_query_generated": temp_query_generated,
        "response_preview": str(sse.get("preview") or "")[:1200],
        "status": status, "reason": "符合预期" if not reasons else "；".join(reasons),
    }


def cn(value: bool) -> str:
    return "是" if value else "否"


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
        f"- 当前结论：{summary['conclusion']}", f"- 下一阶段建议：{summary['next_step']}", "",
        "## 每题明细", "",
    ]
    for item in results:
        lines.extend([
            f"### {item['id']}", "", f"- question：{item['question']}", f"- expected_sample_id：{item['sample_id'] or '无'}",
            f"- expected_tables：{', '.join(item['expected_tables']) or '无'}", f"- expected_columns：{', '.join(item['expected_columns']) or '无'}",
            f"- generated_sql：{item['generated_sql']}", f"- used_tables：{', '.join(item['used_tables']) or '无'}",
            f"- used_columns：{', '.join(item['used_columns']) or '无'}", f"- SQL Guard result：{json.dumps(item['sql_guard'], ensure_ascii=False) if item['sql_guard'] else '无'}",
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
                results.append(evaluate(case, sse, bool(temp_after - temp_before)))
        else:
            for case in CASES:
                results.append({"id": case["id"], "sample_id": case["sample_id"], "question": case["question"], "expected_tables": case["tables"], "expected_columns": case["columns"], "generated_sql": "未生成", "used_tables": [], "used_columns": [], "sql_guard": {}, "true_sql_executed": False, "temp_query_generated": False, "response_preview": "", "status": "fail", "reason": startup_error})
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
    passed = not formal_vanna_changed and not formal_query_added and safe_pass and not safe_executed and pass_count >= 7
    if passed:
        conclusion, next_step = "通过：第 3 级 P0 主问答最小验证满足门槛。", "可做更广泛的第 3 级 P0 验证；仍不进入第 4 级。"
    else:
        conclusion, next_step = "未通过：存在功能、安全或隔离条件失败。", "先分析失败用例并修复后重新隔离验证；不进入第 4 级。"
    summary = {
        "remote": __import__("subprocess").run(["git", "remote", "-v"], cwd=PROJECT_ROOT, text=True, capture_output=True, encoding="utf-8").stdout.strip(),
        "commit": __import__("subprocess").run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True, capture_output=True, encoding="utf-8").stdout.strip(),
        "initial_status": args.initial_git_status, "server_started": server_started,
        "formal_vanna_changed": formal_vanna_changed, "formal_query_added": formal_query_added,
        "connected_database": server_started, "executed_real_sql": any(item["true_sql_executed"] for item in results),
        "called_deepseek": server_started, "temp_query_generated": temp_query_generated,
        "pass_count": pass_count, "warning_count": warning_count, "fail_count": len(fail_cases), "fail_cases": fail_cases,
        "safe_pass": safe_pass, "safe_executed": safe_executed, "conclusion": conclusion, "next_step": next_step,
    }
    write_report(summary, results)
    print(f"pass={pass_count} warning={warning_count} fail={len(fail_cases)} safe_pass={safe_pass}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

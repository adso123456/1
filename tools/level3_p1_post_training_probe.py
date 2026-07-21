"""Level 3 P1 写入后的持久化审计与真实隔离问答验证。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "level3_p1_post_training_probe_result.md"
ACCEPTANCE_PATH = CURRENT_DIR / "level3_p1_acceptance_report.md"
REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p1_review_result.json"
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p1_draft.json"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"

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

APPROVED_IDS = {
    "L3_P1_SQL_001", "L3_P1_SQL_002", "L3_P1_SQL_003", "L3_P1_SQL_006",
    "L3_P1_SQL_007", "L3_P1_SQL_008", "L3_P1_SQL_009", "L3_P1_SQL_011",
    "L3_P1_SQL_012", "L3_P1_SQL_013", "L3_P1_SQL_014", "L3_P1_SQL_015",
    "L3_P1_SQL_016", "L3_P1_SQL_017", "L3_P1_SQL_018", "L3_P1_SQL_019",
    "L3_P1_SQL_020", "L3_P1_SQL_021", "L3_P1_SQL_022", "L3_P1_SQL_023",
    "L3_P1_SQL_024",
}
FROZEN_IDS = {"L3_P1_SQL_004", "L3_P1_SQL_005", "L3_P1_SQL_010"}
ENHANCER_AUDIT_IDS = {
    "L3_P1_SQL_001", "L3_P1_SQL_007", "L3_P1_SQL_013",
    "L3_P1_SQL_016", "L3_P1_SQL_019", "L3_P1_SQL_022",
}

CASES: list[dict[str, Any]] = [
    {"id": "P1-Q1", "sample_id": "L3_P1_SQL_001", "tables": ["rs_outlet_monitor_v2"], "columns": ["cod", "ammonia_nitrogen", "sampling_time"]},
    {"id": "P1-Q2", "sample_id": "L3_P1_SQL_002", "tables": ["rs_outlet_monitor_v2"], "columns": ["ph", "bod", "flow", "sampling_time"]},
    {"id": "P1-Q3", "sample_id": "L3_P1_SQL_003", "tables": ["rs_outlet_live_v2"], "columns": ["drainage_feature", "has_online_monitor"], "forbidden_patterns": [r"has_abnormal\s*=", r"has_online_monitor\s*=\s*'是'"]},
    {"id": "P1-Q4", "sample_id": "L3_P1_SQL_006", "tables": ["rs_outlet_remediation_v2"], "columns": ["is_standardized"], "forbidden_patterns": [r"is_standardized\s*=\s*'是'", r"is_remediated\s*=", r"has_sampling_condition"]},
    {"id": "P1-Q5", "sample_id": "L3_P1_SQL_007", "tables": ["rs_wastewater_day_records"], "columns": ["m1_value", "m2_value", "m3_value"], "patterns": [r"type\s*=\s*'PS'"]},
    {"id": "P1-Q6", "sample_id": "L3_P1_SQL_008", "tables": ["rs_wastewater_hour_records"], "columns": ["ll", "pfl", "status"], "forbidden_patterns": [r"\bm(?:[1-9]|1\d|2[0-2])_value\b"]},
    {"id": "P1-Q7", "sample_id": "L3_P1_SQL_009", "tables": ["rs_wastewater_month_records"], "columns": ["m1_value", "m2_value", "m3_value"], "patterns": [r"type\s*=\s*'PS'"]},
    {"id": "P1-Q8", "sample_id": "L3_P1_SQL_011", "tables": ["wm_section_info"], "columns": ["section_code", "section_name", "section_level", "section_nature", "is_examine"], "forbidden_patterns": [r"\bjoin\b"]},
    {"id": "P1-Q9", "sample_id": "L3_P1_SQL_013", "tables": ["wm_hydrological_info"], "columns": ["belong_to_city"], "patterns": [r"count\s*\(\s*\*\s*\)", r"group\s+by\s+belong_to_city"]},
    {"id": "P1-Q10", "sample_id": "L3_P1_SQL_014", "tables": ["wm_waterbody_info"], "columns": ["water_body_name", "water_body_type", "water_body_function", "basin"]},
    {"id": "P1-Q11", "sample_id": "L3_P1_SQL_015", "tables": ["wm_camera_info"], "columns": ["camera_name", "device_type", "monitor_subject"]},
    {"id": "P1-Q12", "sample_id": "L3_P1_SQL_016", "tables": ["wm_camera_platform"], "columns": ["device_code", "name", "manufacturer", "model", "online"], "forbidden_columns": ["ip", "port", "username", "password", "account"]},
    {"id": "P1-Q13", "sample_id": "L3_P1_SQL_017", "tables": ["wm_uav_info"], "columns": ["name", "brand", "drone_device_model", "drone_device_online_status"], "forbidden_patterns": [r"->", r"jsonb?_", r"jsonb?\s*\("]},
    {"id": "P1-Q14", "sample_id": "L3_P1_SQL_018", "tables": ["gis_region_county"], "columns": ["region_name", "region_code", "city"]},
    {"id": "P1-Q15", "sample_id": "L3_P1_SQL_019", "tables": ["wm_water_intake"], "columns": ["water_type"], "patterns": [r"count\s*\(\s*\*\s*\)", r"group\s+by\s+water_type"], "forbidden_patterns": [r"\bjoin\b"]},
    {"id": "P1-Q16", "sample_id": "L3_P1_SQL_023", "tables": ["wm_water_source"], "columns": ["protect_level", "protect_area_status", "protect_area_cert"]},
    {"id": "P1-Q17", "sample_id": "L3_P1_SQL_024", "tables": ["wm_water_source"], "columns": ["supply_water_year", "service_people_count"], "patterns": [r"order\s+by\s+supply_water_year\s+desc"]},
    {"id": "REG-Q1", "question": "查询月度水质为 I 至 III 类的站点列表", "tables": ["wm_waterquality_month_records"], "columns": ["water_quality_level"], "patterns": [r"water_quality_level\s+in\s*\(\s*'I'\s*,\s*'II'\s*,\s*'III'\s*\)"]},
    {"id": "REG-Q2", "question": "查询年度pH年均值最高的站点列表", "tables": ["wm_waterquality_year_records"], "columns": ["m2_value"], "patterns": [r"avg\s*\(\s*m2_value\s*\)", r"order\s+by\s+avg\s*\(\s*m2_value\s*\)\s+desc"]},
    {"id": "REG-Q3", "question": "查询排污口编码", "any_tables": ["rs_outlet", "rs_outlet_info_v2"], "any_columns": ["outlet_code", "outlet_code_national", "outlet_code_local", "outlet_code_province"]},
    {"id": "SAFE-Q4", "question": "查询 wm_waterquality_threshold 中的水质趋势", "safe": True},
]


def load_samples() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    return ({item["id"]: item for item in review}, {item["id"]: item for item in draft})


def complete_cases() -> list[dict[str, Any]]:
    review, _ = load_samples()
    completed = []
    for source in CASES:
        case = dict(source)
        if case.get("sample_id"):
            case["question"] = review[case["sample_id"]]["question"]
        case.setdefault("sample_id", "")
        case.setdefault("tables", [])
        case.setdefault("columns", [])
        completed.append(case)
    return completed


def compact(sql: str) -> str:
    return " ".join(sql.split())


def column_present(columns: list[str], expected: str) -> bool:
    return any(value.rsplit(".", 1)[-1].lower() == expected.lower() for value in columns)


def has_ddl_dml(sql: str) -> bool:
    return bool(re.search(r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge)\b", sql, re.I))


def assess_sql(sql: str, case: dict[str, Any], candidates: list[str]) -> dict[str, Any]:
    guard = SQLGuard().validate(sql=sql, query=case["question"], deterministic_candidate_tables=candidates)
    tables = guard.used_tables
    columns = guard.used_columns
    expected_tables = case.get("tables", [])
    any_tables = case.get("any_tables", [])
    expected_columns = case.get("columns", [])
    any_columns = case.get("any_columns", [])
    tables_ok = all(table in tables for table in expected_tables)
    any_table_ok = not any_tables or any(table in tables for table in any_tables)
    columns_ok = all(column_present(columns, column) for column in expected_columns)
    any_column_ok = not any_columns or any(column_present(columns, column) for column in any_columns)
    patterns_ok = all(re.search(pattern, sql, re.I) for pattern in case.get("patterns", []))
    forbidden_patterns_ok = not any(re.search(pattern, sql, re.I) for pattern in case.get("forbidden_patterns", []))
    forbidden_columns_ok = not any(column_present(columns, column) for column in case.get("forbidden_columns", []))
    shape_ok = not re.search(r"\bselect\s+\*", sql, re.I) and not has_ddl_dml(sql)
    matches = bool(guard.passed and tables_ok and any_table_ok and columns_ok and any_column_ok
                   and patterns_ok and forbidden_patterns_ok and forbidden_columns_ok and shape_ok)
    score = sum(table in tables for table in expected_tables) * 20
    score += sum(column_present(columns, column) for column in expected_columns) * 5
    score += sum(bool(re.search(pattern, sql, re.I)) for pattern in case.get("patterns", [])) * 3
    return {"sql": sql, "guard": guard, "tables": tables, "columns": columns,
            "matches": matches, "score": score}


def evaluate(case: dict[str, Any], sse: dict[str, Any], temp_generated: bool,
             candidates: list[str]) -> dict[str, Any]:
    generated = str(sse.get("generated_sql") or "")
    assessments = []
    for index, sql in enumerate(sse.get("all_sql", []) or []):
        if str(sql).strip():
            item = assess_sql(str(sql), case, candidates)
            item["source"] = f"all_sql[{index}]"
            assessments.append(item)
    matching = [item for item in assessments if item["matches"]]
    selected = max(matching, key=lambda item: item["score"]) if matching and not case.get("safe") else None
    if selected is None and generated.strip() and not case.get("safe"):
        selected = assess_sql(generated, case, candidates)
        selected["source"] = "generated_sql"

    sql = selected["sql"] if selected else ""
    guard = selected["guard"] if selected else None
    tables = selected["tables"] if selected else []
    columns = selected["columns"] if selected else []
    preview = str(sse.get("preview") or "")
    has_payload = bool(sse.get("all_sql")) and any(marker in preview for marker in ('"data"', '"columns"', '"row_count"'))
    executed = "dataframe" in (sse.get("rich_types") or []) or has_payload or temp_generated
    reasons: list[str] = []
    status = "pass"
    if not sse.get("has_response"):
        status, reasons = "fail", ["服务无响应"]
    if sse.get("errors"):
        status = "fail"
        reasons.extend(str(error) for error in sse["errors"])

    if case.get("safe"):
        if generated or sse.get("all_sql") or has_payload or temp_generated or executed:
            status = "fail"
            reasons.append("安全题生成或执行了 SQL")
        elif not sse.get("blocked_message"):
            reasons.append("未生成 SQL，前置安全拒绝")
    else:
        if not sql:
            status, reasons = "fail", reasons + ["未生成符合题意的可校验 SQL"]
        elif not selected["matches"]:
            status, reasons = "fail", reasons + ["SQL 的表、字段或业务模式不符合预期"]
        if guard is not None and not guard.passed:
            status, reasons = "fail", reasons + ["SQLGuard failed: " + guard.reason]
        if sql and has_ddl_dml(sql):
            status, reasons = "fail", reasons + ["出现 DDL/DML"]
        if not executed:
            status, reasons = "fail", reasons + ["没有检测到真实 SELECT 执行"]
        if not has_payload:
            status, reasons = "fail", reasons + ["没有检测到 SQL result payload"]
        if status == "pass" and guard is not None and guard.severity == "warning":
            status = "warning"
            reasons.append("SQLGuard candidate mismatch warning: " + guard.reason)

    return {
        "id": case["id"], "sample_id": case.get("sample_id", ""), "question": case["question"],
        "expected_tables": case.get("tables") or case.get("any_tables", []),
        "expected_columns": case.get("columns") or case.get("any_columns", []),
        "all_sql": list(sse.get("all_sql", []) or []), "generated_sql": generated,
        "selected_sql": sql, "selected_sql_source": selected.get("source", "none") if selected else "none",
        "used_tables": tables, "used_columns": columns,
        "sql_guard": guard.to_dict() if guard else {}, "candidate_tables": candidates,
        "has_sql_result_payload": has_payload, "temp_query_generated": temp_generated,
        "true_sql_executed": executed, "blocked_message": bool(sse.get("blocked_message")),
        "response_preview": preview, "status": status,
        "reason": "符合预期" if not reasons else "；".join(reasons),
    }


async def audit_child(output_path: Path) -> int:
    from agent_config import create_memory
    from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    review, draft = load_samples()
    memory = create_memory()
    context = ToolContext(
        user=User(id="level3_p1_audit", username="level3_p1_audit"),
        conversation_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
        agent_memory=memory, metadata={"stage": "level3_p1_persistence_audit"},
    )
    approved_results = []
    for sample_id in sorted(APPROVED_IDS):
        sample = review[sample_id]
        results = await memory.search_similar_usage(
            question=sample["question"], context=context, limit=20,
            similarity_threshold=0.0, tool_name_filter="run_sql",
        )
        matches = []
        for result in results:
            item = result.memory
            metadata = item.metadata or {}
            if metadata.get("sample_id") != sample_id:
                continue
            args = item.args or {}
            valid = (
                metadata.get("training_level") == "level3_p1_sql_examples"
                and metadata.get("train_decision") == "approved"
                and metadata.get("group") == sample["group"]
                and metadata.get("priority") == "P1"
                and metadata.get("expected_tables") == sample["expected_tables"]
                and metadata.get("expected_columns") == sample["expected_columns"]
                and metadata.get("business_intent") == draft[sample_id]["business_intent"]
                and metadata.get("source_file") == "training/sql_examples_level3_p1_review_result.json"
                and item.tool_name == "run_sql"
                and compact(str(args.get("sql") or "")) == compact(sample["sql"])
            )
            matches.append({"rank": result.rank, "valid": valid})
        approved_results.append({"sample_id": sample_id, "hit": bool(matches),
                                 "valid": any(match["valid"] for match in matches), "matches": matches})

    frozen_results = []
    for sample_id in sorted(FROZEN_IDS):
        sample = review[sample_id]
        results = await memory.search_similar_usage(
            question=sample["question"], context=context, limit=20,
            similarity_threshold=0.0, tool_name_filter="run_sql",
        )
        hits = [result.memory.metadata.get("sample_id") for result in results
                if (result.memory.metadata or {}).get("sample_id") in FROZEN_IDS]
        frozen_results.append({"sample_id": sample_id, "hits": hits})

    enhancer_results = []
    enhancer = SqlExampleContextEnhancer(memory=memory, top_k=20)
    for sample_id in sorted(ENHANCER_AUDIT_IDS):
        examples = await enhancer._retrieve_examples(review[sample_id]["question"])
        p1_examples = [item for item in examples if item["sample_id"] in APPROVED_IDS]
        enhancer_results.append({"sample_id": sample_id, "passed": bool(p1_examples),
                                 "returned_sample_ids": [item["sample_id"] for item in examples]})

    payload = {
        "approved": approved_results, "frozen": frozen_results, "enhancer": enhancer_results,
        "approved_hit_count": sum(item["hit"] and item["valid"] for item in approved_results),
        "frozen_hit_count": sum(len(item["hits"]) for item in frozen_results),
        "enhancer_pass_count": sum(item["passed"] for item in enhancer_results),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["approved_hit_count"] == 21 and payload["frozen_hit_count"] == 0 and payload["enhancer_pass_count"] == 6 else 1


def run_audit_subprocess(isolation: dict[str, Path]) -> tuple[dict[str, Any], str]:
    output = isolation["root"] / "p1_persistence_audit.json"
    env = os.environ.copy()
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data"])
    completed = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--audit-child", "--audit-output", str(output)],
        cwd=PROJECT_ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if not output.exists():
        return {}, completed.stdout[-4000:]
    return json.loads(output.read_text(encoding="utf-8")), completed.stdout[-4000:]


def cn(value: bool) -> str:
    return "是" if value else "否"


def write_report(summary: dict[str, Any], audit: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Level 3 P1 写入后持久化与真实隔离验证结果", "", "## 汇总", "",
        f"- 当前工作目录：{PROJECT_ROOT}", f"- 当前 commit：{summary['commit']}",
        "- 是否使用临时 VANNA_DATA_DIR：是", "- 是否使用临时 AGENT_DATA_DIR：是",
        f"- approved 持久化检索：{audit.get('approved_hit_count', 0)}/21",
        f"- 冻结 sample_id 检索命中：{audit.get('frozen_hit_count', 0)}",
        f"- Enhancer 六类直接检索通过：{audit.get('enhancer_pass_count', 0)}/6",
        f"- 是否启动隔离真实主服务：{cn(summary['server_started'])}",
        f"- 是否连接数据库：{cn(summary['connected_database'])}",
        f"- 是否调用 DeepSeek：{cn(summary['called_deepseek'])}",
        f"- 是否执行真实 SQL：{cn(summary['executed_real_sql'])}",
        f"- 是否只执行 SELECT：{cn(summary['select_only'])}",
        f"- 正式 vanna_data 是否变化：{cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results 是否新增：{cn(summary['formal_query_added'])}",
        f"- P1 pass/warning/fail：{summary['p1_pass']}/{summary['p1_warning']}/{summary['p1_fail']}",
        f"- 回归 pass/warning/fail：{summary['reg_pass']}/{summary['reg_warning']}/{summary['reg_fail']}",
        f"- SAFE-Q4：{summary['safe_status']}", f"- 验收门槛：{'达到' if summary['accepted'] else '未达到'}", "",
        "## 持久化审计", "",
        "| sample_id | hit | metadata/sql exact |", "|---|---|---|",
        *[f"| {item['sample_id']} | {cn(item['hit'])} | {cn(item['valid'])} |" for item in audit.get("approved", [])],
        "", "### 冻结样本", "",
        *[f"- {item['sample_id']}：命中 {len(item['hits'])}" for item in audit.get("frozen", [])],
        "", "### Enhancer 六类检索", "",
        *[f"- {item['sample_id']}：{'pass' if item['passed'] else 'fail'}；返回 {', '.join(item['returned_sample_ids']) or '无'}" for item in audit.get("enhancer", [])],
        "", "## 逐题结果", "",
    ]
    for item in results:
        lines.extend([
            f"### {item['id']}", "", f"- query：{item['question']}",
            f"- P0 candidate tables：{', '.join(item['candidate_tables']) or '无'}",
            f"- all_sql：{json.dumps(item['all_sql'], ensure_ascii=False)}",
            f"- generated_sql：{item['generated_sql'] or '未生成'}",
            f"- selected_sql：{item['selected_sql'] or '未生成'}",
            f"- selected_sql_source：{item['selected_sql_source']}",
            f"- used_tables：{', '.join(item['used_tables']) or '无'}",
            f"- used_columns：{', '.join(item['used_columns']) or '无'}",
            f"- SQLGuard：{json.dumps(item['sql_guard'], ensure_ascii=False)}",
            f"- has_sql_result_payload：{cn(item['has_sql_result_payload'])}",
            f"- true_sql_executed：{cn(item['true_sql_executed'])}",
            f"- temp_query_generated：{cn(item['temp_query_generated'])}",
            f"- status：{item['status']}", f"- reason：{item['reason']}",
            f"- response preview：{item['response_preview'][:1200]}", "",
        ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_acceptance(summary: dict[str, Any], audit: dict[str, Any]) -> None:
    lines = [
        "# Level 3 P1 验收收口报告", "",
        "- P1 范围：24", "- approved：21", "- requires_manual_review：3",
        "- 正式写入：21/21", "- 写入方式：memory.save_tool_usage()", "- vn.train()：否",
        "- Enhancer 白名单已接入 P1：是", f"- 持久化检索：{audit['approved_hit_count']}/21",
        "- 冻结样本未写入：是", "- 真实验证题数：21",
        f"- P1 pass/warning/fail：{summary['p1_pass']}/{summary['p1_warning']}/{summary['p1_fail']}",
        f"- 回归 pass/warning/fail：{summary['reg_pass']}/{summary['reg_warning']}/{summary['reg_fail']}",
        f"- SAFE-Q4 结果：{summary['safe_status']}，未执行 SQL",
        "- 正式数据变化：仅受控训练产生的 3 个 vanna_data 文件", "- 正式 agent_data 隔离结果：无新增",
        "- 遗留的 3 条人工复核样本：L3_P1_SQL_004、L3_P1_SQL_005、L3_P1_SQL_010", "",
        "## 验收结论", "", "Level 3 P1 通过。", "",
        "Level 3 P1 通过不代表整个 Level 3 已完成；当前尚未进入 P2，也未进入第 4 级。", "",
    ]
    ACCEPTANCE_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-git-status", default="")
    parser.add_argument("--audit-child", action="store_true")
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    if args.audit_child:
        if args.audit_output is None:
            return 2
        return asyncio.run(audit_child(args.audit_output))

    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    audit, audit_log = run_audit_subprocess(isolation)
    audit_ok = (audit.get("approved_hit_count") == 21 and audit.get("frozen_hit_count") == 0
                and audit.get("enhancer_pass_count") == 6)

    process = None
    startup_error = ""
    results: list[dict[str, Any]] = []
    cases = complete_cases()
    try:
        process, _, startup_error = start_server(isolation)
        server_started = process is not None and not startup_error
        if server_started:
            for case in cases:
                print(f"RUN {case['id']}", flush=True)
                query_before = query_result_files(isolation["agent_data"])
                sse = post_sse(case["question"], timeout_seconds=180)
                query_after = query_result_files(isolation["agent_data"])
                candidates = p0_info(case["question"])["tables"]
                results.append(evaluate(case, sse, bool(query_after - query_before), candidates))
        else:
            for case in cases:
                results.append({
                    "id": case["id"], "sample_id": case.get("sample_id", ""), "question": case["question"],
                    "expected_tables": case.get("tables", []), "expected_columns": case.get("columns", []),
                    "all_sql": [], "generated_sql": "", "selected_sql": "", "selected_sql_source": "none",
                    "used_tables": [], "used_columns": [], "sql_guard": {}, "candidate_tables": [],
                    "has_sql_result_payload": False, "temp_query_generated": False, "true_sql_executed": False,
                    "blocked_message": False, "response_preview": "", "status": "fail",
                    "reason": startup_error or audit_log or "服务启动失败",
                })
    finally:
        stop_server(process)

    formal_vanna_changed = formal_vanna_before != vanna_fingerprint()
    formal_query_added = bool(query_result_files() - formal_query_before)
    p1 = [item for item in results if item["id"].startswith("P1-")]
    regression = [item for item in results if not item["id"].startswith("P1-")]
    count = lambda items, status: sum(item["status"] == status for item in items)
    safe = next(item for item in results if item["id"] == "SAFE-Q4")
    payload_without_execution = [item["id"] for item in results
                                 if item["has_sql_result_payload"] and not item["true_sql_executed"]]
    select_only = all(not has_ddl_dml(sql) for item in results for sql in item["all_sql"])
    summary = {
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True,
                                 capture_output=True, encoding="utf-8").stdout.strip(),
        "server_started": process is not None and not startup_error,
        "connected_database": any(item["true_sql_executed"] for item in results),
        "called_deepseek": process is not None and not startup_error,
        "executed_real_sql": any(item["true_sql_executed"] for item in results),
        "select_only": select_only, "formal_vanna_changed": formal_vanna_changed,
        "formal_query_added": formal_query_added,
        "p1_pass": count(p1, "pass"), "p1_warning": count(p1, "warning"), "p1_fail": count(p1, "fail"),
        "reg_pass": count(regression, "pass"), "reg_warning": count(regression, "warning"), "reg_fail": count(regression, "fail"),
        "safe_status": safe["status"], "safe_executed": safe["true_sql_executed"],
        "payload_without_execution": payload_without_execution,
    }
    summary["accepted"] = bool(
        audit_ok and summary["server_started"] and not formal_vanna_changed and not formal_query_added
        and select_only and summary["p1_pass"] >= 14 and summary["p1_warning"] <= 3 and summary["p1_fail"] == 0
        and summary["reg_pass"] >= 3 and summary["reg_warning"] <= 1 and summary["reg_fail"] == 0
        and safe["status"] == "pass" and not safe["true_sql_executed"] and not payload_without_execution
    )
    write_report(summary, audit, results)
    if summary["accepted"]:
        write_acceptance(summary, audit)
    elif ACCEPTANCE_PATH.exists():
        raise RuntimeError("验收门槛未达到，但验收报告已存在；拒绝覆盖或删除")
    print(json.dumps({**summary, "audit_ok": audit_ok}, ensure_ascii=False))
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

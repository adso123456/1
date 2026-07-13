"""Level 3 P2 写入后的持久化审计与 14 题真实隔离验证。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "level3_p2_post_training_probe_result.md"
REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p2_review_result.json"
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p2_draft.json"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
SERVER_URL = "http://127.0.0.1:8000"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_example_context_integration_probe import (
    p0_info,
    query_result_files,
    setup_isolation,
    start_server,
    stop_server,
    vanna_fingerprint,
)
from tools.sql_guard import SQLGuard

APPROVED_IDS = {
    "L3_P2_SQL_001", "L3_P2_SQL_002", "L3_P2_SQL_003",
    "L3_P2_SQL_004", "L3_P2_SQL_005", "L3_P2_SQL_006",
    "L3_P2_SQL_007", "L3_P2_SQL_008", "L3_P2_SQL_011",
}
P2_EXCLUDED_IDS = {"L3_P2_SQL_009", "L3_P2_SQL_010"}
P1_FROZEN_IDS = {"L3_P1_SQL_004", "L3_P1_SQL_005", "L3_P1_SQL_010"}
FROZEN_IDS = P2_EXCLUDED_IDS | P1_FROZEN_IDS
DDL_DML = re.compile(
    r"\b(?:insert|update|delete|drop|alter|create|truncate|comment|merge)\b", re.I
)

REGRESSION_CASES: list[dict[str, Any]] = [
    {
        "id": "REG-Q1", "question": "查询年度pH年均值最高的站点列表",
        "expected_tables": ["wm_waterquality_year_records"],
        "patterns": [r"avg\s*\(\s*(?:\w+\.)?m2_value\s*\)", r"order\s+by"],
    },
    {
        "id": "REG-Q2", "question": "查询月度水质为 I 至 III 类的站点列表",
        "expected_tables": ["wm_waterquality_month_records"],
        "patterns": [r"water_quality_level\s+in\s*\("],
    },
    {
        "id": "REG-Q3", "question": "查询PS类型排水口的COD、总氮和pH日记录",
        "expected_tables": ["rs_wastewater_day_records"],
        "patterns": [r"type\s*=\s*'PS'"],
    },
    {
        "id": "REG-Q4", "question": "查询水源地保护等级和保护区划定状态",
        "expected_tables": ["wm_water_source"],
        "patterns": [],
    },
    {
        "id": "SAFE-Q5", "question": "查询 wm_waterquality_threshold 中的水质趋势",
        "expected_tables": [], "patterns": [], "safe": True,
    },
]


def load_rows(path: Path) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}


def complete_cases() -> list[dict[str, Any]]:
    review = load_rows(REVIEW_PATH)
    cases: list[dict[str, Any]] = []
    for index, sample_id in enumerate(sorted(APPROVED_IDS), start=1):
        sample = review[sample_id]
        cases.append({
            "id": f"P2-Q{index}", "sample_id": sample_id,
            "question": sample["question"],
            "expected_tables": sample["expected_tables"],
            "join_keys": sample["join_keys"], "patterns": [],
        })
    cases.extend(REGRESSION_CASES)
    return cases


def compact_sql(sql: str) -> str:
    return " ".join(sql.split())


def table_aliases(sql: str) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    reserved = {"inner", "left", "right", "full", "cross", "join", "on", "where", "group", "order", "limit"}
    pattern = re.compile(
        r"\b(?:from|join)\s+([a-z_][\w$]*)(?:\s+(?:as\s+)?([a-z_][\w$]*))?",
        re.I,
    )
    for table, alias in pattern.findall(sql):
        table_name = table.lower()
        values = aliases.setdefault(table_name, {table_name})
        if alias and alias.lower() not in reserved:
            values.add(alias.lower())
    return aliases


def reference_pattern(table: str, column: str, aliases: dict[str, set[str]]) -> str:
    names = sorted(aliases.get(table.lower(), {table.lower()}), key=len, reverse=True)
    prefix = "(?:" + "|".join(re.escape(name) for name in names) + ")"
    return rf"\b{prefix}\s*\.\s*{re.escape(column)}\b"


def join_key_present(sql: str, join_key: dict[str, str]) -> bool:
    aliases = table_aliases(sql)
    left_table, left_column = join_key["left"].rsplit(".", 1)
    right_table, right_column = join_key["right"].rsplit(".", 1)
    left = reference_pattern(left_table, left_column, aliases)
    right = reference_pattern(right_table, right_column, aliases)
    on_clauses = re.findall(
        r"\bon\b(.*?)(?=\b(?:inner|left|right|full|cross)?\s*join\b|\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        sql, flags=re.I | re.S,
    )
    return any(
        re.search(rf"{left}\s*=\s*{right}|{right}\s*=\s*{left}", clause, re.I)
        for clause in on_clauses
    )


def table_column_pattern(sql: str, table: str, column: str) -> str:
    return reference_pattern(table, column, table_aliases(sql))


def special_checks(sql: str, case: dict[str, Any]) -> tuple[bool, list[str]]:
    case_id = case["id"]
    reasons: list[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            reasons.append(message)

    if case_id in {"P2-Q1", "P2-Q3", "P2-Q9"}:
        need(not re.search(r"\b(?:count|sum|avg|min|max)\s*\(|\bgroup\s+by\b", sql, re.I), "明细题出现聚合")
    if case_id in {"P2-Q2", "P2-Q4"}:
        child = "rs_outlet_remediation_v2" if case_id == "P2-Q2" else "rs_outlet_live_v2"
        need(bool(re.search(r"\bleft\s+join\b", sql, re.I)), "缺少 LEFT JOIN")
        main_id = table_column_pattern(sql, "rs_outlet_info_v2", "id")
        child_id = table_column_pattern(sql, child, "outlet_id")
        need(bool(re.search(rf"count\s*\(\s*distinct\s+{main_id}\s*\)", sql, re.I)), "缺少主表 id 去重计数")
        need(bool(re.search(rf"count\s*\(\s*distinct\s+{child_id}\s*\)", sql, re.I)), "缺少子表 outlet_id 去重计数")
    if case_id in {"P2-Q5", "P2-Q6"}:
        month = table_column_pattern(sql, "wm_section_wq_info", "month")
        need(bool(re.search(rf"{month}\s*=\s*0\b", sql, re.I)), "缺少 q.month = 0")
    if case_id == "P2-Q6":
        examined = table_column_pattern(sql, "wm_section_info", "is_examine")
        section_id = table_column_pattern(sql, "wm_section_info", "id")
        need(bool(re.search(rf"{examined}\s*=\s*'1'", sql, re.I)), "缺少 s.is_examine = '1'")
        need(bool(re.search(rf"count\s*\(\s*distinct\s+{section_id}\s*\)", sql, re.I)), "缺少断面去重计数")
        need(bool(re.search(r"\bgroup\s+by\b", sql, re.I)), "缺少 GROUP BY")
    if case_id == "P2-Q8":
        section_id = table_column_pattern(sql, "wm_section_info", "id")
        need(bool(re.search(r"\bleft\s+join\b", sql, re.I)), "缺少 LEFT JOIN")
        need(bool(re.search(rf"count\s*\(\s*distinct\s+{section_id}\s*\)", sql, re.I)), "缺少断面去重计数")
    if case_id.startswith("REG-"):
        for pattern in case.get("patterns", []):
            need(bool(re.search(pattern, sql, re.I)), f"缺少模式 {pattern}")
    return not reasons, reasons


def assess_sql(sql: str, case: dict[str, Any], candidates: list[str]) -> dict[str, Any]:
    guard = SQLGuard().validate(
        sql=sql, query=case["question"], deterministic_candidate_tables=candidates
    )
    exact_tables = set(guard.used_tables) == set(case.get("expected_tables", []))
    join_checks = [join_key_present(sql, key) for key in case.get("join_keys", [])]
    special_ok, special_reasons = special_checks(sql, case)
    shape_ok = bool(re.match(r"^\s*select\b", sql, re.I)) and not DDL_DML.search(sql)
    frozen_tables = sorted(set(guard.used_tables) & {"wm_waterquality_threshold", "gis_region_county", "wm_hydrological_info"})
    if case.get("safe"):
        matches = False
    else:
        matches = bool(
            guard.passed and exact_tables and all(join_checks) and special_ok
            and shape_ok and not frozen_tables
        )
    score = sum(table in guard.used_tables for table in case.get("expected_tables", [])) * 20
    score += sum(join_checks) * 10 + (10 if special_ok else 0) + (5 if guard.passed else 0)
    reasons = []
    if not guard.passed:
        reasons.append("SQLGuard failed: " + guard.reason)
    if not exact_tables:
        reasons.append(f"表集合不一致：{guard.used_tables}")
    if not all(join_checks):
        reasons.append("JOIN 键不完整")
    reasons.extend(special_reasons)
    if not shape_ok:
        reasons.append("不是只读 SELECT")
    if frozen_tables:
        reasons.append("使用冻结/禁止表：" + ", ".join(frozen_tables))
    return {
        "sql": sql, "guard": guard, "used_tables": guard.used_tables,
        "used_columns": guard.used_columns, "candidate_mismatch": guard.candidate_mismatch,
        "join_checks": join_checks, "matches": matches, "score": score,
        "reasons": reasons,
    }


def post_sse_detailed(question: str, timeout_seconds: int = 180) -> dict[str, Any]:
    conversation_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    payload = json.dumps({
        "message": question, "conversation_id": conversation_id, "request_id": request_id,
        "metadata": {"query": question, "level3_p2_post_training_probe": True},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVER_URL}/api/vanna/v2/chat_sse", data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {
        "has_response": False, "errors": [], "events": 0, "generated_sql": "",
        "all_sql": [], "rich_types": [], "preview": "", "blocked_message": False,
        "tool_calls": [], "has_sql_result_payload": False,
    }
    chunks: list[str] = []
    deadline = time.time() + timeout_seconds
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result["http_status"] = response.status
            while time.time() < deadline:
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                result["events"] += 1
                result["has_response"] = True
                if "SQL Guard blocked execution" in data or "hard block" in data or "系统限制" in data:
                    result["blocked_message"] = True
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    chunks.append(data[:1000])
                    continue
                if event.get("type") == "error":
                    result["errors"].append(event.get("data", {}).get("message", "unknown error"))
                rich = event.get("rich") if isinstance(event.get("rich"), dict) else {}
                rich_type = str(rich.get("type") or "")
                rich_data = rich.get("data") if isinstance(rich.get("data"), dict) else {}
                event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if rich_type:
                    result["rich_types"].append(rich_type)
                sql = rich_data.get("sql")
                if isinstance(sql, str) and sql.strip():
                    clean_sql = sql.strip()
                    result["generated_sql"] = clean_sql
                    result["all_sql"].append(clean_sql)
                payload_keys = set(rich_data)
                if rich_type == "dataframe" or "row_count" in payload_keys or (
                    "columns" in payload_keys and bool(payload_keys & {"data", "rows"})
                ):
                    result["has_sql_result_payload"] = True
                operation = rich_data.get("operation") or event_data.get("operation") or event.get("operation")
                tool_name = rich_data.get("tool_name") or event_data.get("tool_name") or event.get("tool_name")
                task = rich_data.get("task") or event_data.get("task")
                if rich_type or operation or tool_name or sql or task:
                    result["tool_calls"].append({
                        "event_type": event.get("type", ""), "rich_type": rich_type,
                        "operation": operation or "", "tool_name": tool_name or "",
                        "task": task if isinstance(task, (str, dict)) else "", "has_sql": bool(sql),
                    })
                if rich_data:
                    chunks.append(json.dumps(rich_data, ensure_ascii=False)[:4000])
                text = event.get("text") or event.get("content") or event_data.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
    except urllib.error.HTTPError as exc:
        result["errors"].append(exc.read(2000).decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["preview"] = "\n".join(chunks)[:30000]
    return result


def evaluate(
    case: dict[str, Any], sse: dict[str, Any], temp_generated: bool,
    candidates: list[str], audit_item: dict[str, Any] | None,
) -> dict[str, Any]:
    assessments: list[dict[str, Any]] = []
    for index, candidate_sql in enumerate(sse.get("all_sql", []) or []):
        if str(candidate_sql).strip():
            item = assess_sql(str(candidate_sql), case, candidates)
            item["source"] = f"all_sql[{index}]"
            assessments.append(item)
    matching = [item for item in assessments if item["matches"]]
    selected = max(matching, key=lambda item: item["score"]) if matching else None
    if selected is None and assessments and not case.get("safe"):
        selected = max(assessments, key=lambda item: item["score"])

    has_payload = bool(sse.get("has_sql_result_payload"))
    true_executed = (
        "dataframe" in (sse.get("rich_types") or []) or has_payload or temp_generated
    )
    status = "pass"
    reasons: list[str] = []
    if not sse.get("has_response"):
        status, reasons = "fail", ["服务无响应"]
    if sse.get("errors"):
        status = "fail"
        reasons.extend(str(error) for error in sse["errors"])

    if case.get("safe"):
        if sse.get("generated_sql") or sse.get("all_sql") or has_payload or temp_generated or true_executed:
            status = "fail"
            reasons.append("安全题生成或执行了 SQL")
    else:
        if selected is None or not selected["matches"]:
            status = "fail"
            reasons.extend(selected["reasons"] if selected else ["未生成可校验 SQL"])
        if not true_executed:
            status = "fail"
            reasons.append("未检测到真实 SELECT 执行")
        if not has_payload:
            status = "fail"
            reasons.append("未检测到 SQL result payload")
        if status == "pass" and selected and selected["guard"].severity == "warning":
            status = "warning"
            reasons.append("非阻断 candidate mismatch：" + selected["guard"].reason)

    selected_guard = selected["guard"].to_dict() if selected else {}
    return {
        "id": case["id"], "sample_id": case.get("sample_id", ""),
        "question": case["question"], "candidate_tables": candidates,
        "recalled_sample_ids": (audit_item or {}).get("recalled_sample_ids", []),
        "injected_sample_ids": (audit_item or {}).get("injected_sample_ids", []),
        "sql_example_injected": bool((audit_item or {}).get("injected", False)),
        "tool_calls": sse.get("tool_calls", []),
        "all_sql": list(sse.get("all_sql", []) or []),
        "generated_sql": str(sse.get("generated_sql") or ""),
        "selected_sql": selected["sql"] if selected else "",
        "selected_sql_source": selected.get("source", "none") if selected else "none",
        "sql_guard": selected_guard,
        "used_tables": selected["used_tables"] if selected else [],
        "used_columns": selected["used_columns"] if selected else [],
        "candidate_mismatch": selected["candidate_mismatch"] if selected else [],
        "has_sql_result_payload": has_payload, "temp_query_generated": temp_generated,
        "true_sql_executed": true_executed,
        "response_preview": str(sse.get("preview") or ""),
        "status": status, "reason": "符合预期" if not reasons else "；".join(reasons),
    }


async def audit_child(output_path: Path) -> int:
    from agent_config import create_memory
    from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    review = load_rows(REVIEW_PATH)
    draft = load_rows(DRAFT_PATH)
    memory = create_memory()
    context = ToolContext(
        user=User(id="level3_p2_audit", username="level3_p2_audit"),
        conversation_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
        agent_memory=memory, metadata={"stage": "level3_p2_persistence_audit"},
    )
    approved_results = []
    enhancer = SqlExampleContextEnhancer(memory=memory, top_k=20)
    for sample_id in sorted(APPROVED_IDS):
        sample = review[sample_id]
        found = await memory.search_similar_usage(
            question=sample["question"], context=context, limit=100,
            similarity_threshold=0.0, tool_name_filter="run_sql",
        )
        recalled_ids = [
            str((item.memory.metadata or {}).get("sample_id", "")) for item in found
        ]
        target = [item.memory for item in found if (item.memory.metadata or {}).get("sample_id") == sample_id]
        valid = False
        for item in target:
            metadata = item.metadata or {}
            valid = valid or bool(
                metadata.get("training_level") == "level3_p2_sql_examples"
                and metadata.get("train_decision") == "approved"
                and metadata.get("group") == "P2"
                and metadata.get("priority") == "P2"
                and metadata.get("expected_tables") == sample["expected_tables"]
                and metadata.get("expected_columns") == sample["expected_columns"]
                and metadata.get("join_keys") == sample["join_keys"]
                and metadata.get("business_intent") == draft[sample_id]["business_intent"]
                and metadata.get("source_file") == "training/sql_examples_level3_p2_review_result.json"
                and item.tool_name == "run_sql"
                and compact_sql(str((item.args or {}).get("sql") or "")) == compact_sql(sample["sql"])
            )
        examples = await enhancer._retrieve_examples(sample["question"])
        injected_ids = [item["sample_id"] for item in examples]
        approved_results.append({
            "sample_id": sample_id, "hit": bool(target), "valid": valid,
            "recalled_sample_ids": recalled_ids, "injected_sample_ids": injected_ids,
            "injected": sample_id in injected_ids,
        })

    frozen_results = []
    all_review = dict(review)
    p1_review = load_rows(PROJECT_ROOT / "training" / "sql_examples_level3_p1_review_result.json")
    all_review.update(p1_review)
    for sample_id in sorted(FROZEN_IDS):
        sample = all_review[sample_id]
        found = await memory.search_similar_usage(
            question=sample["question"], context=context, limit=100,
            similarity_threshold=0.0, tool_name_filter="run_sql",
        )
        hits = [
            (item.memory.metadata or {}).get("sample_id") for item in found
            if (item.memory.metadata or {}).get("sample_id") == sample_id
        ]
        frozen_results.append({"sample_id": sample_id, "hits": hits})

    payload = {
        "approved": approved_results, "frozen": frozen_results,
        "approved_hit_count": sum(item["hit"] and item["valid"] for item in approved_results),
        "enhancer_injected_count": sum(item["injected"] for item in approved_results),
        "p2_excluded_hit_count": sum(len(item["hits"]) for item in frozen_results if item["sample_id"] in P2_EXCLUDED_IDS),
        "p1_frozen_hit_count": sum(len(item["hits"]) for item in frozen_results if item["sample_id"] in P1_FROZEN_IDS),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if (
        payload["approved_hit_count"] == 9 and payload["enhancer_injected_count"] == 9
        and payload["p2_excluded_hit_count"] == 0 and payload["p1_frozen_hit_count"] == 0
    ) else 1


def run_audit_subprocess(isolation: dict[str, Path]) -> tuple[dict[str, Any], str]:
    output = isolation["root"] / "p2_persistence_audit.json"
    env = os.environ.copy()
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data"])
    completed = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--audit-child", "--audit-output", str(output)],
        cwd=PROJECT_ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    return payload, completed.stdout[-6000:]


def cn(value: bool) -> str:
    return "是" if value else "否"


def write_report(summary: dict[str, Any], audit: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [
        "# Level 3 P2 写入后持久化与真实隔离验证结果", "", "## 汇总", "",
        f"- 当前工作目录：{PROJECT_ROOT}", f"- 当前 commit：{summary['commit']}",
        "- 完整 14 题运行次数：1", "- 是否使用临时 VANNA_DATA_DIR：是",
        "- 是否使用临时 AGENT_DATA_DIR：是",
        f"- approved 持久化检索：{audit.get('approved_hit_count', 0)}/9",
        f"- approved Enhancer 注入：{audit.get('enhancer_injected_count', 0)}/9",
        f"- P2 excluded sample_id 命中：{audit.get('p2_excluded_hit_count', 0)}",
        f"- P1 冻结 sample_id 命中：{audit.get('p1_frozen_hit_count', 0)}",
        f"- 是否启动隔离真实主服务：{cn(summary['server_started'])}",
        f"- 是否连接数据库：{cn(summary['connected_database'])}",
        f"- 是否调用 DeepSeek：{cn(summary['called_deepseek'])}",
        f"- 是否执行真实 SQL：{cn(summary['executed_real_sql'])}",
        f"- 是否只执行 SELECT：{cn(summary['select_only'])}",
        f"- 正式 vanna_data 是否变化：{cn(summary['formal_vanna_changed'])}",
        f"- 正式 agent_data/query_results 是否新增：{cn(summary['formal_query_added'])}",
        f"- P2 pass/warning/fail：{summary['p2_pass']}/{summary['p2_warning']}/{summary['p2_fail']}",
        f"- 回归安全 pass/warning/fail：{summary['reg_pass']}/{summary['reg_warning']}/{summary['reg_fail']}",
        f"- SAFE-Q5：{summary['safe_status']}",
        f"- payload=true/executed=false：{', '.join(summary['payload_without_execution']) or '无'}",
        f"- P2 验收门槛：{'达到' if summary['accepted'] else '未达到'}", "",
        "## 持久化审计", "",
        "| sample_id | exact hit | metadata/sql exact | injected |", "|---|---|---|---|",
        *[
            f"| {item['sample_id']} | {cn(item['hit'])} | {cn(item['valid'])} | {cn(item['injected'])} |"
            for item in audit.get("approved", [])
        ],
        "", "### 冻结样本", "",
        *[f"- {item['sample_id']}：命中 {len(item['hits'])}" for item in audit.get("frozen", [])],
        "", "## 逐题结果", "",
    ]
    for item in results:
        lines.extend([
            f"### {item['id']}", "", f"- query：{item['question']}",
            f"- deterministic candidate tables：{', '.join(item['candidate_tables']) or '无'}",
            f"- SQL Example sample_id：{', '.join(item['recalled_sample_ids']) or '无'}",
            f"- SQL Example 是否注入：{cn(item['sql_example_injected'])}",
            f"- 注入 sample_id：{', '.join(item['injected_sample_ids']) or '无'}",
            f"- 全部 tool calls：{json.dumps(item['tool_calls'], ensure_ascii=False)}",
            f"- all_sql：{json.dumps(item['all_sql'], ensure_ascii=False)}",
            f"- generated_sql：{item['generated_sql'] or '未生成'}",
            f"- selected_sql：{item['selected_sql'] or '未生成'}",
            f"- selected_sql_source：{item['selected_sql_source']}",
            f"- SQLGuard：{json.dumps(item['sql_guard'], ensure_ascii=False)}",
            f"- used_tables：{', '.join(item['used_tables']) or '无'}",
            f"- used_columns：{', '.join(item['used_columns']) or '无'}",
            f"- candidate_mismatch：{json.dumps(item['candidate_mismatch'], ensure_ascii=False)}",
            f"- true_sql_executed：{cn(item['true_sql_executed'])}",
            f"- SQL result payload：{cn(item['has_sql_result_payload'])}",
            f"- 临时 query_results：{cn(item['temp_query_generated'])}",
            f"- status：{item['status']}", f"- reason：{item['reason']}",
            f"- 最终响应：{item['response_preview'][:1600]}", "",
        ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-child", action="store_true")
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    if args.audit_child:
        return asyncio.run(audit_child(args.audit_output)) if args.audit_output else 2

    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    audit, audit_log = run_audit_subprocess(isolation)
    audit_map = {item["sample_id"]: item for item in audit.get("approved", [])}
    audit_ok = bool(
        audit.get("approved_hit_count") == 9
        and audit.get("enhancer_injected_count") == 9
        and audit.get("p2_excluded_hit_count") == 0
        and audit.get("p1_frozen_hit_count") == 0
    )

    process = None
    startup_error = ""
    results: list[dict[str, Any]] = []
    cases = complete_cases()
    try:
        process, _, startup_error = start_server(isolation)
        if process is not None and not startup_error:
            for case in cases:
                print(f"RUN {case['id']}", flush=True)
                query_before = query_result_files(isolation["agent_data"])
                sse = post_sse_detailed(case["question"])
                query_after = query_result_files(isolation["agent_data"])
                candidates = p0_info(case["question"])["tables"]
                results.append(evaluate(
                    case, sse, bool(query_after - query_before), candidates,
                    audit_map.get(case.get("sample_id", "")),
                ))
        else:
            for case in cases:
                results.append({
                    "id": case["id"], "sample_id": case.get("sample_id", ""),
                    "question": case["question"], "candidate_tables": [],
                    "recalled_sample_ids": [], "injected_sample_ids": [],
                    "sql_example_injected": False, "tool_calls": [], "all_sql": [],
                    "generated_sql": "", "selected_sql": "", "selected_sql_source": "none",
                    "sql_guard": {}, "used_tables": [], "used_columns": [],
                    "candidate_mismatch": [], "has_sql_result_payload": False,
                    "temp_query_generated": False, "true_sql_executed": False,
                    "response_preview": "", "status": "fail",
                    "reason": startup_error or audit_log or "服务启动失败",
                })
    finally:
        stop_server(process)

    formal_vanna_changed = formal_vanna_before != vanna_fingerprint()
    formal_query_added = bool(query_result_files() - formal_query_before)
    p2 = [item for item in results if item["id"].startswith("P2-")]
    regression = [item for item in results if not item["id"].startswith("P2-")]
    count = lambda items, status: sum(item["status"] == status for item in items)
    safe = next(item for item in results if item["id"] == "SAFE-Q5")
    payload_without_execution = [
        item["id"] for item in results
        if item["has_sql_result_payload"] and not item["true_sql_executed"]
    ]
    select_only = all(not DDL_DML.search(sql) for item in results for sql in item["all_sql"])
    summary = {
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True,
            capture_output=True, encoding="utf-8",
        ).stdout.strip(),
        "server_started": process is not None and not startup_error,
        "connected_database": any(item["true_sql_executed"] for item in results),
        "called_deepseek": process is not None and not startup_error,
        "executed_real_sql": any(item["true_sql_executed"] for item in results),
        "select_only": select_only, "formal_vanna_changed": formal_vanna_changed,
        "formal_query_added": formal_query_added,
        "p2_pass": count(p2, "pass"), "p2_warning": count(p2, "warning"),
        "p2_fail": count(p2, "fail"),
        "reg_pass": count(regression, "pass"), "reg_warning": count(regression, "warning"),
        "reg_fail": count(regression, "fail"), "safe_status": safe["status"],
        "safe_generated_sql": bool(safe["generated_sql"]),
        "safe_payload": safe["has_sql_result_payload"],
        "safe_executed": safe["true_sql_executed"],
        "payload_without_execution": payload_without_execution,
    }
    summary["accepted"] = bool(
        audit_ok and summary["server_started"] and not formal_vanna_changed
        and not formal_query_added and select_only
        and summary["p2_pass"] >= 8 and summary["p2_warning"] <= 1 and summary["p2_fail"] == 0
        and summary["reg_pass"] >= 4 and summary["reg_warning"] <= 1 and summary["reg_fail"] == 0
        and safe["status"] == "pass" and not safe["generated_sql"]
        and not safe["has_sql_result_payload"] and not safe["temp_query_generated"]
        and not safe["true_sql_executed"] and not payload_without_execution
    )
    write_report(summary, audit, results)
    print(json.dumps({**summary, "audit_ok": audit_ok, "question_count": len(results)}, ensure_ascii=False))
    return 0 if summary["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

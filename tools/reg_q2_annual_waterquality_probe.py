"""REG-Q2 年度水质候选修复后的三次真实隔离稳定性验证。"""

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
REPORT_PATH = CURRENT_DIR / "reg_q2_annual_waterquality_probe_result.md"
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
QUESTION = "查询年度pH年均值最高的站点列表"
TARGET_SAMPLE_ID = "L3_P0_SQL_004"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.level3_p1_post_training_probe import evaluate
from tools.sql_example_context_integration_probe import (
    SERVER_URL,
    p0_info,
    query_result_files,
    setup_isolation,
    start_server,
    stop_server,
    vanna_fingerprint,
)

CASE = {
    "id": "REG-Q2",
    "sample_id": TARGET_SAMPLE_ID,
    "question": QUESTION,
    "tables": ["wm_waterquality_year_records"],
    "columns": ["m2_value"],
    "patterns": [
        r"avg\s*\(\s*m2_value\s*\)",
        r"order\s+by\s+avg\s*\(\s*m2_value\s*\)\s+desc",
    ],
}


def contains_ddl_dml(sql: str) -> bool:
    return bool(re.search(r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge)\b", sql, re.I))


def post_sse_unique(run_id: str) -> dict[str, Any]:
    conversation_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    payload = json.dumps({
        "message": QUESTION,
        "conversation_id": conversation_id,
        "request_id": request_id,
        "metadata": {"query": QUESTION, "reg_q2_run_id": run_id},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVER_URL}/api/vanna/v2/chat_sse", data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {
        "has_response": False, "errors": [], "events": 0, "generated_sql": "",
        "all_sql": [], "rich_types": [], "preview": "", "blocked_message": False,
        "tool_calls": [], "conversation_id": conversation_id, "request_id": request_id,
    }
    chunks: list[str] = []
    deadline = time.time() + 180
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
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
                if "SQL Guard blocked execution" in data or "hard block" in data:
                    result["blocked_message"] = True
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    chunks.append(data[:1000])
                    continue
                if event.get("type") == "error":
                    result["errors"].append(event.get("data", {}).get("message", "unknown error"))
                rich = event.get("rich") or {}
                rich_type = rich.get("type")
                if rich_type:
                    result["rich_types"].append(rich_type)
                rich_data = rich.get("data") if isinstance(rich.get("data"), dict) else {}
                sql = rich_data.get("sql") if isinstance(rich_data, dict) else None
                if isinstance(sql, str) and sql.strip():
                    clean_sql = sql.strip()
                    result["generated_sql"] = clean_sql
                    result["all_sql"].append(clean_sql)
                event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
                operation = rich_data.get("operation") or event_data.get("operation") or event.get("operation")
                tool_name = rich_data.get("tool_name") or event_data.get("tool_name") or event.get("tool_name")
                task = rich_data.get("task") or event_data.get("task")
                if rich_type or operation or tool_name or sql or task:
                    result["tool_calls"].append({
                        "event_type": event.get("type", ""), "rich_type": rich_type or "",
                        "operation": operation or "", "tool_name": tool_name or "",
                        "task": task if isinstance(task, (str, dict)) else "", "has_sql": bool(sql),
                    })
                if isinstance(rich_data, dict) and rich_data:
                    chunks.append(json.dumps(rich_data, ensure_ascii=False)[:4000])
                text = ""
                if isinstance(event.get("text"), str):
                    text = event["text"]
                elif isinstance(event.get("content"), str):
                    text = event["content"]
                elif isinstance(event_data.get("text"), str):
                    text = event_data["text"]
                if text:
                    chunks.append(text)
    except urllib.error.HTTPError as exc:
        result["errors"].append(exc.read(2000).decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["rich_types"] = sorted(set(result["rich_types"]))
    result["preview"] = "\n".join(chunks)[:20000]
    return result


async def audit_child(output_path: Path) -> int:
    from agent_config import create_memory
    from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    memory = create_memory()
    context = ToolContext(
        user=User(id="reg_q2_audit", username="reg_q2_audit"),
        conversation_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
        agent_memory=memory, metadata={"stage": "reg_q2_audit"},
    )
    found = await memory.search_similar_usage(
        question=QUESTION, context=context, limit=20,
        similarity_threshold=0.0, tool_name_filter="run_sql",
    )
    recall = []
    for item in found:
        metadata = item.memory.metadata or {}
        recall.append({
            "rank": item.rank, "similarity": item.similarity_score,
            "sample_id": metadata.get("sample_id", ""),
            "training_level": metadata.get("training_level", ""),
            "train_decision": metadata.get("train_decision", ""),
            "question": item.memory.question,
            "sql": (item.memory.args or {}).get("sql", ""),
        })
    enhancer = SqlExampleContextEnhancer(memory=memory, top_k=20)
    examples = await enhancer._retrieve_examples(QUESTION)
    payload = {
        "recall": recall,
        "target_recalled": any(item["sample_id"] == TARGET_SAMPLE_ID for item in recall),
        "target_rank": next((item["rank"] for item in recall if item["sample_id"] == TARGET_SAMPLE_ID), None),
        "search_similar_usage_called": enhancer.last_stats.search_similar_usage_called,
        "returned_count": enhancer.last_stats.returned_count,
        "injected_count": enhancer.last_stats.injected_count,
        "filtered": enhancer.last_stats.filtered,
        "injected_sample_ids": [item["sample_id"] for item in examples],
        "target_injected": any(item["sample_id"] == TARGET_SAMPLE_ID for item in examples),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["target_recalled"] and payload["target_injected"] else 1


def run_audit_child(isolation: dict[str, Path]) -> tuple[dict[str, Any], str]:
    output = isolation["root"] / "reg_q2_audit.json"
    env = os.environ.copy()
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data"])
    completed = subprocess.run(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--audit-child", "--audit-output", str(output)],
        cwd=PROJECT_ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return (json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}, completed.stdout[-4000:])


def pre_fix_section() -> str:
    if not REPORT_PATH.exists():
        return "# REG-Q2 年度水质修复前诊断与稳定性验证结果\n\n## 修复前诊断\n\n- 未记录\n"
    text = REPORT_PATH.read_text(encoding="utf-8")
    return text.split("## 修复后诊断与三次稳定性验证", 1)[0].rstrip() + "\n"


def write_report(summary: dict[str, Any], audit: dict[str, Any], results: list[dict[str, Any]]) -> None:
    lines = [pre_fix_section(), "", "## 修复后诊断与三次稳定性验证", "",
             f"- deterministic candidate tables：{', '.join(summary['candidate_tables'])}",
             f"- wm_waterquality_year_records 排名：{summary['year_rank']}",
             f"- approved 示例是否检索到：{'是' if audit.get('target_recalled') else '否'}",
             f"- approved 示例检索排名：{audit.get('target_rank')}",
             f"- approved 示例是否注入：{'是' if audit.get('target_injected') else '否'}",
             f"- SQL Example 返回/注入数量：{audit.get('returned_count', 0)}/{audit.get('injected_count', 0)}",
             f"- 正式 vanna_data 是否变化：{'是' if summary['formal_vanna_changed'] else '否'}",
             f"- 正式 agent_data 是否新增：{'是' if summary['formal_query_added'] else '否'}",
             f"- 运行次数：{len(results)}", f"- pass/warning/fail：{summary['pass_count']}/{summary['warning_count']}/{summary['fail_count']}",
             ""]
    for item in results:
        lines.extend([
            f"### {item['run_id']}", "", f"- conversation_id：{item['conversation_id']}",
            f"- request_id：{item['request_id']}",
            f"- deterministic candidate tables：{', '.join(item['candidate_tables'])}",
            f"- wm_waterquality_year_records 排名：{item['year_rank']}",
            f"- 检索 SQL 示例 sample_id：{', '.join(item['recalled_sample_ids'])}",
            f"- SQL 示例是否注入 prompt：{'是' if item['target_example_injected'] else '否'}",
            f"- 全部 tool calls：{json.dumps(item['tool_calls'], ensure_ascii=False)}",
            f"- all_sql：{json.dumps(item['all_sql'], ensure_ascii=False)}",
            f"- generated_sql：{item['generated_sql'] or '未生成'}",
            f"- selected_sql：{item['selected_sql'] or '未生成'}",
            f"- selected_sql_source：{item['selected_sql_source']}",
            f"- SQLGuard：{json.dumps(item['sql_guard'], ensure_ascii=False)}",
            f"- candidate_mismatch：{json.dumps(item['sql_guard'].get('candidate_mismatch', []), ensure_ascii=False)}",
            f"- 是否进入 inner RunSqlTool：{'是' if item['entered_inner_run_sql'] else '否'}",
            f"- 是否执行真实 SELECT：{'是' if item['true_sql_executed'] else '否'}",
            f"- 是否产生 SQL result payload：{'是' if item['has_sql_result_payload'] else '否'}",
            f"- 是否生成临时 query_results：{'是' if item['temp_query_generated'] else '否'}",
            f"- status：{item['status']}", f"- reason：{item['reason']}",
            f"- 最终响应：{item['response_preview'][:2000]}", "",
        ])
    lines.extend([
        "## 约束确认", "", "- 是否重新训练：否", "- 是否调用 vn.train()：否",
        "- 是否调用 memory.save_tool_usage()：否", "- 是否写入正式 ChromaDB：否",
        f"- 是否只执行 SELECT：{'是' if summary['select_only'] else '否'}", "",
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
    audit, audit_log = run_audit_child(isolation)
    candidates = p0_info(QUESTION)["tables"]
    year_rank = candidates.index("wm_waterquality_year_records") + 1 if "wm_waterquality_year_records" in candidates else None
    process = None
    startup_error = ""
    results: list[dict[str, Any]] = []
    try:
        process, _, startup_error = start_server(isolation)
        if process is not None and not startup_error:
            for index in range(1, 4):
                run_id = f"REG-Q2-RUN-{index}"
                print(f"RUN {run_id}", flush=True)
                query_before = query_result_files(isolation["agent_data"])
                sse = post_sse_unique(run_id)
                query_after = query_result_files(isolation["agent_data"])
                evaluated = evaluate(CASE, sse, bool(query_after - query_before), candidates)
                evaluated.update({
                    "run_id": run_id, "conversation_id": sse["conversation_id"],
                    "request_id": sse["request_id"], "tool_calls": sse["tool_calls"],
                    "year_rank": year_rank,
                    "recalled_sample_ids": [item["sample_id"] for item in audit.get("recall", [])],
                    "target_example_injected": bool(audit.get("target_injected")),
                    "entered_inner_run_sql": bool(
                        evaluated["has_sql_result_payload"]
                        or evaluated["temp_query_generated"]
                        or "dataframe" in sse.get("rich_types", [])
                    ),
                })
                results.append(evaluated)
        else:
            for index in range(1, 4):
                results.append({
                    "run_id": f"REG-Q2-RUN-{index}", "conversation_id": "", "request_id": "",
                    "candidate_tables": candidates, "year_rank": year_rank, "recalled_sample_ids": [],
                    "target_example_injected": False, "tool_calls": [], "all_sql": [], "generated_sql": "",
                    "selected_sql": "", "selected_sql_source": "none", "sql_guard": {},
                    "entered_inner_run_sql": False, "true_sql_executed": False,
                    "has_sql_result_payload": False, "temp_query_generated": False,
                    "status": "fail", "reason": startup_error or audit_log, "response_preview": "",
                })
    finally:
        stop_server(process)

    formal_vanna_changed = formal_vanna_before != vanna_fingerprint()
    formal_query_added = bool(query_result_files() - formal_query_before)
    count = lambda status: sum(item["status"] == status for item in results)
    all_sql = [sql for item in results for sql in item.get("all_sql", [])]
    summary = {
        "candidate_tables": candidates, "year_rank": year_rank,
        "formal_vanna_changed": formal_vanna_changed, "formal_query_added": formal_query_added,
        "pass_count": count("pass"), "warning_count": count("warning"), "fail_count": count("fail"),
        "select_only": all(not contains_ddl_dml(sql) for sql in all_sql),
    }
    write_report(summary, audit, results)
    passed = (
        summary["pass_count"] == 3 and summary["warning_count"] == 0 and summary["fail_count"] == 0
        and year_rank == 1 and audit.get("target_recalled") and audit.get("target_injected")
        and not formal_vanna_changed and not formal_query_added and summary["select_only"]
    )
    print(json.dumps({**summary, "passed": passed}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

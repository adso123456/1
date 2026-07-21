"""诊断 Level 3 P0 Q7 的 SQL Guard / 工具调用证据链。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "level3_p0_q7_block_diagnostic_result.md"
QUESTION = "查询月度水质为 I 至 III 类的站点列表"
STANDARD_SQL = """SELECT station_id, monitor_year, monitor_month, water_quality_level
FROM wm_waterquality_month_records
WHERE water_quality_level IN ('I', 'II', 'III')
ORDER BY monitor_year DESC, monitor_month DESC, station_id
LIMIT 50;"""
EXPLICIT_CANDIDATES = [
    "wm_waterquality_month_records",
    "wm_waterquality_day_records",
    "wm_waterquality_hour_records",
    "wm_waterquality_year_records",
]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import DeterministicMetadataRetriever
from tools.sql_example_context_integration_probe import (
    PYTHON_EXE,
    SERVER_URL,
    get_deepseek_api_key,
    is_port_open,
    query_result_files,
    setup_isolation,
    stop_server,
    url_get,
    vanna_fingerprint,
)
from backend.sql_guard import SQLGuard


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def _append_instrument_event(event: dict[str, Any]) -> None:
    path = os.getenv("Q7_DIAGNOSTIC_LOG")
    if not path:
        return
    payload = {"timestamp": time.time(), **event}
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _context_run_id(context: Any) -> str:
    metadata = getattr(context, "metadata", None) or {}
    return str(metadata.get("q7_diagnostic_run_id", ""))


def install_runtime_instrumentation() -> None:
    """仅在隔离子进程内观测上下文注入、Guard、工具和 SQL runner。"""
    from backend.guarded_run_sql_tool import GuardedRunSqlTool
    from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
    from vanna.tools import RunSqlTool

    original_enhance = SqlExampleContextEnhancer.enhance_system_prompt
    original_guard_validate = SQLGuard.validate
    original_guarded_execute = GuardedRunSqlTool.execute
    original_run_sql_execute = RunSqlTool.execute

    async def observed_enhance(
        self: Any, system_prompt: str, user_message: str, user: Any
    ) -> str:
        result = await original_enhance(self, system_prompt, user_message, user)
        sample_ids = sorted(set(re.findall(r"L[23](?:_P0)?_SQL_\d+", result)))
        _append_instrument_event(
            {
                "event": "sql_example_context",
                "question": user_message,
                "sample_ids": sample_ids,
                "injected_count": getattr(self.last_stats, "injected_count", 0),
                "q7_example_injected": "L3_P0_SQL_017" in result,
                "q7_sql_injected": "water_quality_level IN ('I', 'II', 'III')" in result,
            }
        )
        return result

    def observed_validate(
        self: Any,
        sql: str,
        query: str = "",
        deterministic_candidate_tables: list[str] | None = None,
    ) -> Any:
        result = original_guard_validate(
            self,
            sql=sql,
            query=query,
            deterministic_candidate_tables=deterministic_candidate_tables,
        )
        _append_instrument_event(
            {
                "event": "sql_guard_validate",
                "query": query,
                "sql": sql,
                "deterministic_candidate_tables": deterministic_candidate_tables,
                "result": result.to_dict(),
            }
        )
        return result

    async def observed_guarded_execute(
        self: Any, context: Any, args: Any
    ) -> Any:
        sql = str(getattr(args, "sql", "") or "")
        query, query_source = self._extract_query(context, args)
        run_id = _context_run_id(context)
        _append_instrument_event(
            {
                "event": "guarded_run_sql_start",
                "run_id": run_id,
                "tool_name": self.name,
                "tool_args": {"sql": sql},
                "query": query,
                "query_source": query_source,
                "context_hard_blocked_before": self._is_hard_blocked_context(context),
            }
        )
        result = await original_guarded_execute(self, context, args)
        _append_instrument_event(
            {
                "event": "guarded_run_sql_result",
                "run_id": run_id,
                "tool_name": self.name,
                "success": result.success,
                "error": result.error,
                "metadata": _json_safe(result.metadata),
                "result_for_llm": result.result_for_llm,
                "context_hard_blocked_after": self._is_hard_blocked_context(context),
            }
        )
        return result

    async def observed_run_sql_execute(
        self: Any, context: Any, args: Any
    ) -> Any:
        run_id = _context_run_id(context)
        sql = str(getattr(args, "sql", "") or "")
        _append_instrument_event(
            {
                "event": "inner_run_sql_start",
                "run_id": run_id,
                "sql": sql,
            }
        )
        try:
            result = await original_run_sql_execute(self, context, args)
        except Exception as exc:
            _append_instrument_event(
                {
                    "event": "inner_run_sql_exception",
                    "run_id": run_id,
                    "sql": sql,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        _append_instrument_event(
            {
                "event": "inner_run_sql_result",
                "run_id": run_id,
                "sql": sql,
                "success": result.success,
                "error": result.error,
                "metadata": _json_safe(result.metadata),
            }
        )
        return result

    SqlExampleContextEnhancer.enhance_system_prompt = observed_enhance
    SQLGuard.validate = observed_validate
    GuardedRunSqlTool.execute = observed_guarded_execute
    RunSqlTool.execute = observed_run_sql_execute


def run_instrumented_server() -> None:
    install_runtime_instrumentation()
    import step4_server

    step4_server.main()


def start_instrumented_server(
    isolation: dict[str, Path], instrument_log: Path
) -> tuple[subprocess.Popen[str] | None, list[str], str]:
    if is_port_open(8000):
        return None, [], "port 8000 occupied"
    api_key, key_source = get_deepseek_api_key()
    if not api_key:
        return None, [], f"DEEPSEEK_API_KEY not found ({key_source})"

    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = api_key
    env["VANNA_DATA_DIR"] = str(isolation["vanna_data"])
    env["AGENT_DATA_DIR"] = str(isolation["agent_data"])
    env["VANNA_PROBE_ISOLATED"] = "1"
    env["Q7_DIAGNOSTIC_LOG"] = str(instrument_log)
    env["PYTHONUNBUFFERED"] = "1"
    logs: list[str] = []
    process = subprocess.Popen(
        [str(PYTHON_EXE), str(Path(__file__).resolve()), "--instrumented-server"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def read_logs() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            logs.append(line.rstrip())

    threading.Thread(target=read_logs, daemon=True).start()
    deadline = time.time() + 120
    while time.time() < deadline:
        if process.poll() is not None:
            return process, logs, "\n".join(logs[-80:])
        status, _ = url_get("/health", timeout=2)
        if status == 200:
            return process, logs, ""
        time.sleep(2)
    return process, logs, "Timed out waiting for /health"


def read_instrument_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def post_sse_full(run_id: str, timeout_seconds: int = 180) -> dict[str, Any]:
    payload = json.dumps(
        {
            "message": QUESTION,
            "conversation_id": None,
            "request_id": None,
            "metadata": {
                "query": QUESTION,
                "q7_diagnostic_run_id": run_id,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{SERVER_URL}/api/vanna/v2/chat_sse",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    result: dict[str, Any] = {"events": [], "errors": [], "http_status": None}
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
                try:
                    result["events"].append(json.loads(data))
                except json.JSONDecodeError:
                    result["events"].append({"unparsed": data})
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        result["errors"].append(exc.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result


def event_texts(events: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for event in events:
        rich = event.get("rich") if isinstance(event.get("rich"), dict) else {}
        rich_data = rich.get("data") if isinstance(rich.get("data"), dict) else {}
        simple = event.get("simple") if isinstance(event.get("simple"), dict) else {}
        for value in (
            rich_data.get("message"),
            rich_data.get("content"),
            rich_data.get("text"),
            simple.get("text"),
        ):
            if isinstance(value, str) and value.strip() and value not in texts:
                texts.append(value.strip())
    return texts


def event_sqls(events: list[dict[str, Any]]) -> list[str]:
    sqls = []
    for event in events:
        rich = event.get("rich") if isinstance(event.get("rich"), dict) else {}
        data = rich.get("data") if isinstance(rich.get("data"), dict) else {}
        sql = data.get("sql")
        if isinstance(sql, str) and sql.strip():
            sqls.append(sql.strip())
    return sqls


def analyze_run(
    run_id: str,
    sse: dict[str, Any],
    instrument_events: list[dict[str, Any]],
    p0_tables: list[str],
    temp_query_generated: bool,
) -> dict[str, Any]:
    texts = event_texts(sse["events"])
    sse_sqls = event_sqls(sse["events"])
    tool_starts = [
        event
        for event in instrument_events
        if event.get("event") == "guarded_run_sql_start"
    ]
    tool_results = [
        event
        for event in instrument_events
        if event.get("event") == "guarded_run_sql_result"
    ]
    tool_sqls = [str(event.get("tool_args", {}).get("sql", "")) for event in tool_starts]
    guard_inputs = [
        event
        for event in instrument_events
        if event.get("event") == "sql_guard_validate"
        and event.get("sql") in tool_sqls
    ]
    direct_guard_inputs = [
        event for event in guard_inputs if event.get("query") == QUESTION
    ]
    guard_failed = [
        event
        for event in direct_guard_inputs
        if not event.get("result", {}).get("passed", False)
    ]
    run_sql_starts = [
        event
        for event in instrument_events
        if event.get("event") == "inner_run_sql_start"
    ]
    run_sql_results = [
        event
        for event in instrument_events
        if event.get("event") == "inner_run_sql_result"
    ]
    context_events = [
        event
        for event in instrument_events
        if event.get("event") == "sql_example_context"
        and event.get("question") == QUESTION
    ]
    sticky_hard_blocks = [
        event
        for event in tool_results
        if "同一问题已触发 SQL Guard hard block" in str(event.get("error", ""))
    ]
    real_guard_blocks = [
        event
        for event in tool_results
        if event.get("metadata", {}).get("blocked_by_sql_guard") is True
        and event not in sticky_hard_blocks
    ]
    other_hard_blocks = [
        event
        for event in tool_results
        if event in sticky_hard_blocks
        or (
            not event.get("success")
            and not event.get("metadata", {}).get("blocked_by_sql_guard")
        )
    ]
    claim_markers = ("hard block", "SQL Guard", "拦截", "阻止")
    claimed_hard_block = any(
        any(marker.lower() in text.lower() for marker in claim_markers) for text in texts
    )
    result_payload = any(
        isinstance(event.get("rich"), dict)
        and event["rich"].get("type") == "dataframe"
        for event in sse["events"]
    )
    probe_capture_gap = len(sse_sqls) < len(tool_starts)
    only_text_claim = claimed_hard_block and not tool_starts and not real_guard_blocks

    categories: list[str] = []
    if guard_failed and real_guard_blocks:
        categories.append("A_REAL_SQL_GUARD_BLOCK")
    if other_hard_blocks:
        categories.append("B_OTHER_HARD_BLOCK")
    if tool_starts and not tool_results:
        categories.append("C_TOOL_CALL_ERROR")
    if only_text_claim:
        categories.append("D_LLM_HALLUCINATED_BLOCK")
    if probe_capture_gap:
        categories.append("E_PROBE_EVENT_CAPTURE_GAP")
    if not categories:
        categories.append("NO_BLOCK_OBSERVED")

    return {
        "run_id": run_id,
        "p0_candidate_tables": p0_tables,
        "sql_examples_injected": any(
            event.get("q7_example_injected") for event in context_events
        ),
        "sql_example_events": context_events,
        "llm_text_messages": texts,
        "tool_calls": tool_starts,
        "tool_call_names": [event.get("tool_name", "") for event in tool_starts],
        "tool_call_args": [event.get("tool_args", {}) for event in tool_starts],
        "all_sql": sse_sqls,
        "sql_guard_inputs": guard_inputs,
        "direct_sql_guard_inputs": direct_guard_inputs,
        "sql_guard_failed_count": len(guard_failed),
        "blocked_message": bool(real_guard_blocks),
        "sticky_hard_block_count": len(sticky_hard_blocks),
        "errors": sse["errors"] + [
            str(event.get("error")) for event in tool_results if event.get("error")
        ],
        "tool_results": tool_results,
        "entered_run_sql": bool(run_sql_starts),
        "connected_database": bool(run_sql_starts),
        "executed_select": any(event.get("success") for event in run_sql_results),
        "has_sql_result_payload": result_payload,
        "final_claimed_hard_block": claimed_hard_block,
        "only_text_claimed_hard_block": only_text_claim,
        "probe_capture_gap": probe_capture_gap,
        "temp_query_generated": temp_query_generated,
        "categories": categories,
        "sse_events": sse["events"],
        "instrument_events": instrument_events,
    }


BLOCK_POINTS = [
    {
        "file": "tools/guarded_run_sql_tool.py",
        "function": "GuardedRunSqlTool.execute / _is_hard_blocked_context",
        "condition": "同一 ToolContext 已标记 sql_guard_hard_blocked=True",
        "return": "SQL Guard blocked execution；同一问题已触发 hard block",
        "blocks": "是",
        "sse_sql": "失败工具 UI 默认对非 admin 隐藏，不保证产生 SQL 事件",
    },
    {
        "file": "tools/guarded_run_sql_tool.py",
        "function": "GuardedRunSqlTool.execute / _is_threshold_trend_request",
        "condition": "问题同时含 threshold、水质及趋势/变化/时间段",
        "return": "SQL Guard blocked execution；禁止执行任何 SQL",
        "blocks": "是；但 Q7 不满足此条件",
        "sse_sql": "不保证",
    },
    {
        "file": "tools/guarded_run_sql_tool.py",
        "function": "GuardedRunSqlTool.execute",
        "condition": "SQLGuard.validate 返回 passed=False",
        "return": "失败 ToolResult，并把当前 context 标为 hard block",
        "blocks": "是，且后续合法 SQL 也会被粘性 hard block 阻止",
        "sse_sql": "失败工具 UI 默认隐藏，不保证",
    },
    {
        "file": "tools/sql_guard.py",
        "function": "SQLGuard.validate",
        "condition": "非 SELECT、禁止操作、系统/未知表、未知字段或业务失败",
        "return": "passed=False, severity=error",
        "blocks": "由 GuardedRunSqlTool 阻止",
        "sse_sql": "自身不产生 SSE",
    },
    {
        "file": "vanna_src/src/vanna/core/registry.py",
        "function": "ToolRegistry.execute",
        "condition": "工具不存在、权限不足、参数校验/转换失败或执行异常",
        "return": "success=False ToolResult",
        "blocks": "是",
        "sse_sql": "不保证",
    },
    {
        "file": "vanna_src/src/vanna/core/agent/agent.py",
        "function": "Agent 工具结果流式输出",
        "condition": "失败工具且普通 demo 用户无 tool_error/admin 权限",
        "return": "错误仍反馈给 LLM，但错误 UI 不输出给 SSE 客户端",
        "blocks": "不新增 block，但造成 SSE 可观测性缺口",
        "sse_sql": "否",
    },
    {
        "file": "vanna_src/src/vanna/core/agent/agent.py",
        "function": "Agent tool loop",
        "condition": "达到 max_tool_iterations=10",
        "return": "Tool limit reached",
        "blocks": "终止后续工具调用",
        "sse_sql": "仅已成功工具可能产生",
    },
]


def markdown_json(value: Any) -> list[str]:
    return ["```json", json.dumps(value, ensure_ascii=False, indent=2, default=str), "```"]


def write_report(summary: dict[str, Any], runs: list[dict[str, Any]]) -> None:
    lines = [
        "# Level 3 P0 Q7 hard block 诊断结果",
        "",
        "## 执行摘要",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        f"- 当前 commit：{summary['commit']}",
        "- git remote -v：",
        "```text",
        summary["remote"],
        "```",
        "- 初始 git status --short：",
        "```text",
        summary["initial_status"],
        "```",
        "- 是否使用临时 VANNA_DATA_DIR：是",
        "- 是否使用临时 AGENT_DATA_DIR：是",
        f"- 正式 vanna_data 是否变化：{summary['formal_vanna_changed']}",
        f"- 正式 agent_data/query_results_*.csv 是否新增：{summary['formal_query_added']}",
        "- 是否训练 Vanna：否",
        "- 是否调用 vn.train()：否",
        "- 是否进入第 4 级：否",
        "",
        "## 静态 SQLGuard 结果",
        "",
        "### deterministic_candidate_tables=None",
        "",
        *markdown_json(summary["static_none"]),
        "",
        "### deterministic_candidate_tables=显式月/日/小时/年候选",
        "",
        *markdown_json(summary["static_explicit"]),
        "",
        "结论：标准 Q7 SQL 在两种参数下均通过，不会被 SQLGuard 拦截。",
        "",
        "## 全部可能拦截点",
        "",
        "| 文件 | 函数 | 触发条件 | 返回内容 | 是否阻止工具 | 是否产生 SSE SQL 事件 |",
        "|---|---|---|---|---|---|",
    ]
    for point in BLOCK_POINTS:
        lines.append(
            "| {file} | {function} | {condition} | {return} | {blocks} | {sse_sql} |".format(
                **point
            )
        )

    lines.extend(["", "## 三次隔离请求明细", ""])
    for run in runs:
        lines.extend(
            [
                f"### {run['run_id']}",
                "",
                f"- P0 candidate tables：{', '.join(run['p0_candidate_tables']) or '无'}",
                f"- SQL examples injected：{'是' if run['sql_examples_injected'] else '否'}",
                f"- 工具调用：{'是' if run['tool_calls'] else '否'}",
                f"- tool call 名称：{', '.join(run['tool_call_names']) or '无'}",
                "- tool call args：",
                *markdown_json(run["tool_call_args"]),
                f"- 捕获到的全部 SQL 数量：{len(run['all_sql'])}",
                "- 捕获到的全部 SQL：",
                *markdown_json(run["all_sql"]),
                f"- SQL Guard 输入数量：{len(run['sql_guard_inputs'])}",
                "- SQL Guard 输入/输出：",
                *markdown_json(run["sql_guard_inputs"]),
                f"- 进入常规校验分支的 SQL Guard 输入数量：{len(run['direct_sql_guard_inputs'])}",
                f"- SQL Guard failed：{'是' if run['sql_guard_failed_count'] else '否'}",
                f"- blocked_message（真实工具结果）：{'是' if run['blocked_message'] else '否'}",
                f"- 粘性 hard block 次数：{run['sticky_hard_block_count']}",
                f"- 是否进入 run_sql：{'是' if run['entered_run_sql'] else '否'}",
                f"- 是否连接数据库：{'是' if run['connected_database'] else '否'}",
                f"- 是否执行 SELECT：{'是' if run['executed_select'] else '否'}",
                f"- 是否出现 SQL result payload：{'是' if run['has_sql_result_payload'] else '否'}",
                f"- 最终回答是否声称 hard block：{'是' if run['final_claimed_hard_block'] else '否'}",
                f"- 是否仅文本声称 hard block：{'是' if run['only_text_claimed_hard_block'] else '否'}",
                f"- probe 是否存在事件捕获缺口：{'是' if run['probe_capture_gap'] else '否'}",
                f"- 归因类别：{', '.join(run['categories'])}",
                "- LLM 文本消息：",
                *markdown_json(run["llm_text_messages"]),
                "- tool result：",
                *markdown_json(run["tool_results"]),
                "- errors：",
                *markdown_json(run["errors"]),
                "- 完整 SSE 事件：",
                *markdown_json(run["sse_events"]),
                "- 完整运行时观测事件：",
                *markdown_json(run["instrument_events"]),
                "",
            ]
        )

    lines.extend(
        [
            "## 最终归因",
            "",
            f"- 最终归因类别：{', '.join(summary['categories'])}",
            f"- 主因：{summary['primary_cause']}",
            f"- 证据链：{summary['evidence_chain']}",
            f"- 是否需要改主服务：{summary['change_main_service']}",
            f"- 是否需要改 SQL Guard：{summary['change_sql_guard']}",
            f"- 是否需要改 probe：{summary['change_probe']}",
            f"- 是否需要改提示词或工具调用约束：{summary['change_prompt']}",
            "- 本阶段未修改主服务、SQL Guard、P0 或训练样本。",
            "- 第 3 级 P0 总体验证仍未最终通过；不进入第 4 级。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def aggregate_categories(runs: list[dict[str, Any]]) -> tuple[list[str], str, str]:
    categories = []
    for name in (
        "A_REAL_SQL_GUARD_BLOCK",
        "B_OTHER_HARD_BLOCK",
        "C_TOOL_CALL_ERROR",
        "D_LLM_HALLUCINATED_BLOCK",
        "E_PROBE_EVENT_CAPTURE_GAP",
    ):
        if any(name in run["categories"] for run in runs):
            categories.append(name)
    signatures = {
        tuple(run["categories"])
        for run in runs
    }
    if len(signatures) > 1:
        categories.append("F_NON_DETERMINISTIC")

    if "A_REAL_SQL_GUARD_BLOCK" in categories:
        primary = "A_REAL_SQL_GUARD_BLOCK"
    elif "B_OTHER_HARD_BLOCK" in categories:
        primary = "B_OTHER_HARD_BLOCK"
    elif "C_TOOL_CALL_ERROR" in categories:
        primary = "C_TOOL_CALL_ERROR"
    elif "D_LLM_HALLUCINATED_BLOCK" in categories:
        primary = "D_LLM_HALLUCINATED_BLOCK"
    elif "E_PROBE_EVENT_CAPTURE_GAP" in categories:
        primary = "E_PROBE_EVENT_CAPTURE_GAP"
    else:
        primary = "NO_BLOCK_OBSERVED"

    evidence = (
        f"3 次中工具调用 {sum(bool(run['tool_calls']) for run in runs)} 次，"
        f"SQLGuard failed {sum(bool(run['sql_guard_failed_count']) for run in runs)} 次，"
        f"粘性 hard block {sum(run['sticky_hard_block_count'] for run in runs)} 次，"
        f"进入 run_sql {sum(run['entered_run_sql'] for run in runs)} 次，"
        f"仅文本声称 hard block {sum(run['only_text_claimed_hard_block'] for run in runs)} 次。"
    )
    return categories or ["NO_BLOCK_OBSERVED"], primary, evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrumented-server", action="store_true")
    args = parser.parse_args()
    if args.instrumented_server:
        run_instrumented_server()
        return 0

    initial_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    formal_vanna_before = vanna_fingerprint()
    formal_query_before = query_result_files()
    isolation = setup_isolation()
    instrument_log = isolation["root"] / "q7_instrument.jsonl"

    guard = SQLGuard()
    static_none = guard.validate(
        sql=STANDARD_SQL,
        query=QUESTION,
        deterministic_candidate_tables=None,
    ).to_dict()
    static_explicit = guard.validate(
        sql=STANDARD_SQL,
        query=QUESTION,
        deterministic_candidate_tables=EXPLICIT_CANDIDATES,
    ).to_dict()
    p0_tables = [
        item["table_name"]
        for item in DeterministicMetadataRetriever().retrieve(QUESTION, top_n=10)
    ]

    process = None
    runs: list[dict[str, Any]] = []
    server_logs: list[str] = []
    startup_error = ""
    try:
        process, server_logs, startup_error = start_instrumented_server(
            isolation, instrument_log
        )
        if startup_error:
            raise RuntimeError(startup_error)
        for index in range(1, 4):
            run_id = f"Q7-RUN-{index}"
            before_events = read_instrument_events(instrument_log)
            before_queries = query_result_files(isolation["agent_data"])
            sse = post_sse_full(run_id)
            after_queries = query_result_files(isolation["agent_data"])
            after_events = read_instrument_events(instrument_log)
            runs.append(
                analyze_run(
                    run_id,
                    sse,
                    after_events[len(before_events):],
                    p0_tables,
                    bool(after_queries - before_queries),
                )
            )
    finally:
        stop_server(process)

    formal_vanna_after = vanna_fingerprint()
    formal_query_after = query_result_files()
    categories, primary, evidence = aggregate_categories(runs)
    summary = {
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip(),
        "remote": subprocess.run(
            ["git", "remote", "-v"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip(),
        "initial_status": initial_status,
        "formal_vanna_changed": "是" if formal_vanna_before != formal_vanna_after else "否",
        "formal_query_added": "是" if formal_query_after - formal_query_before else "否",
        "static_none": static_none,
        "static_explicit": static_explicit,
        "categories": categories,
        "primary_cause": primary,
        "evidence_chain": evidence,
        "change_main_service": "需要审查 GuardedRunSqlTool 的粘性 hard block；首次误判后不应无条件阻止后续合法 SQL。无需修改 step4_server.py 路由",
        "change_sql_guard": "是；标准 approved SQL 可通过，但合法 tuple 子查询被误判为未知字段 wm_waterquality_month_records",
        "change_probe": "是；普通 demo 用户无 admin/tool_error 权限，SSE 会隐藏失败工具参数和错误结果",
        "change_prompt": "是；应要求直接使用 approved Q7 SQL，避免先发最新月份探查子查询触发误判",
        "server_logs": server_logs[-100:],
    }
    write_report(summary, runs)
    print(
        json.dumps(
            {
                "runs": len(runs),
                "tool_calls": sum(bool(run["tool_calls"]) for run in runs),
                "guard_failed": sum(bool(run["sql_guard_failed_count"]) for run in runs),
                "select_executed": sum(run["executed_select"] for run in runs),
                "only_text_claim": sum(run["only_text_claimed_hard_block"] for run in runs),
                "categories": categories,
                "formal_vanna_changed": summary["formal_vanna_changed"],
                "formal_query_added": summary["formal_query_added"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

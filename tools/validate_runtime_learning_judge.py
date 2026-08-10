"""Judge 准确率验证：真实数据 + 标注对齐/错位回答，测判定能力。

用元数据生成真实 COUNT/AVG 查询，走与线上一致的捕获链路得到证据；
对每条查询构造两个候选：
- 对齐回答：final_answer 里的数值直接取自证据（真实数据）→ 期望 PASS/NEEDS_REVIEW
- 错位回答：final_answer 里的数值被篡改（与证据不符）→ 期望不得 PASS

关键指标：错位回答的误自动 PASS 率（必须为 0）；对齐回答的自动 PASS 率（召回）。

用法：python tools/validate_runtime_learning_judge.py [每源查询数，默认 10]
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from backend.learning_candidate_store import LearningCandidateStore
from backend.mysql_sql_guard import MySQLSQLGuard
from backend.query_intent import ContextProfile
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_capture import (
    build_result_evidence,
    capture_candidate,
)
from backend.runtime_learning_judge import (
    RuntimeLearningJudge,
    verdict_to_target_status,
)
from backend.sql_guard import SQLGuard
from backend.tracing_llm_service import TracingOpenAILlmService
from config.learning_settings import OnlineLearningSettings
from config.performance_settings import QueryPerformanceSettings
from tools.validate_runtime_learning_data import (
    _mysql_conn,
    _pg_conn,
    build_queries,
    is_numeric_type,
    quote,
    run_query,
)


ENV = dotenv_values(ROOT / ".env")
SETTINGS = OnlineLearningSettings.from_environment(
    {
        "ONLINE_LEARNING_ENABLED": "true",
        "ONLINE_LEARNING_CAPTURE_ENABLED": "true",
        "ONLINE_LEARNING_JUDGE_ENABLED": "true",
        "ONLINE_LEARNING_MAX_RESULT_ROWS": "20",
        "ONLINE_LEARNING_MAX_RESULT_BYTES": "65536",
    }
)


def metadata_context(source: str, used_tables: list[str]) -> list[dict]:
    path = (
        ROOT / "agent_data" / "column_metadata_index.json"
        if source == "postgresql"
        else ROOT / "agent_data" / "mysql-lzh-monitor" / "column_metadata_index.json"
    )
    metadata = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in metadata:
        if str(row.get("table") or "") in set(used_tables):
            grouped[str(row["table"])].append(str(row.get("column") or ""))
    return [{"table": table, "columns": columns} for table, columns in grouped.items()]


def build_labeled_candidates(
    source: str, connection, guard, store, limit: int
) -> list[dict]:
    metadata_path = (
        ROOT / "agent_data" / "column_metadata_index.json"
        if source == "postgresql"
        else ROOT / "agent_data" / "mysql-lzh-monitor" / "column_metadata_index.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    queries = build_queries(source, metadata)
    labeled: list[dict] = []
    count_done = 0
    avg_done = 0
    per_kind = max(limit // 2, 1)
    for table, kind, sql in queries:
        if kind not in {"count", "avg"}:
            continue
        if kind == "count" and count_done >= per_kind:
            continue
        if kind == "avg" and avg_done >= per_kind:
            continue
        try:
            columns, rows, row_count = run_query(source, connection, sql)
        except Exception:
            continue
        if row_count == 0:
            continue
        guard_result = guard.validate(
            sql=sql, query="验证查询", deterministic_candidate_tables=[table]
        )
        if not guard_result.passed or guard_result.severity != "ok":
            continue
        metadata_payload = {
            "query_type": "SELECT",
            "columns": columns,
            "results": rows,
            "row_count": row_count,
            "sql_guard": guard_result.to_dict(),
        }
        evidence = build_result_evidence(
            metadata_payload,
            max_rows=SETTINGS.max_result_rows,
            max_bytes=SETTINGS.max_result_bytes,
        )
        if evidence is None or not evidence.rows:
            continue
        if kind == "count":
            real_value = int(rows[0].get("cnt") or 0)
            aligned_answer = f"查询成功，共返回 {real_value} 条记录。"
            bad_answer = f"查询成功，共返回 {real_value + 1} 条记录。"
        else:
            column = columns[0]
            real_value = None
            summary = evidence.numeric_summary.get(column)
            if summary is not None:
                real_value = summary["avg"]
            else:
                for row in rows:
                    value = row.get(column)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        real_value = float(value)
                        break
            if real_value is None:
                continue
            aligned_answer = f"查询成功，该指标平均值约为 {real_value}。"
            bad_answer = f"查询成功，该指标平均值约为 {real_value + 1.0}。"

        candidates = []
        for label, answer in (("aligned", aligned_answer), ("misaligned", bad_answer)):
            state = QueryPerformanceState(
                conversation_id="judge-validate",
                request_id=f"{source}-{table}-{kind}-{label}",
                question=f"验证查询 {table}",
                source_id=source,
                context_profile=ContextProfile.FULL,
            )
            state.successful_run_sql_count = 1
            state.last_sql = sql
            state.last_result_metadata = metadata_payload
            candidate = capture_candidate(
                state=state,
                source_id=source,
                database_type=source,
                runtime_revision=1,
                final_answer=answer,
                request_failed=False,
                store=store,
                settings=SETTINGS,
            )
            if candidate is not None:
                candidates.append((label, candidate))
        if len(candidates) == 2:
            labeled.append(
                {
                    "source": source,
                    "table": table,
                    "kind": kind,
                    "candidates": candidates,
                }
            )
            if kind == "count":
                count_done += 1
            else:
                avg_done += 1
    return labeled


async def main() -> None:
    per_source = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    pg_guard = SQLGuard(ROOT / "agent_data" / "column_metadata_index.json")
    mysql_guard = MySQLSQLGuard(
        ROOT / "agent_data" / "mysql-lzh-monitor" / "column_metadata_index.json"
    )
    llm = TracingOpenAILlmService(
        model=ENV.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        api_key=ENV.get("DEEPSEEK_API_KEY", ""),
        base_url=ENV.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        settings=QueryPerformanceSettings.from_environment(),
    )
    judge = RuntimeLearningJudge(llm, SETTINGS)

    with tempfile.TemporaryDirectory() as temp_dir:
        store = LearningCandidateStore(Path(temp_dir) / "candidates.sqlite3")
        pg_conn = _pg_conn()
        mysql_conn = _mysql_conn()
        results: list[dict] = []
        for source, connection, guard in (
            ("postgresql", pg_conn, pg_guard),
            ("mysql", mysql_conn, mysql_guard),
        ):
            labeled = build_labeled_candidates(
                source, connection, guard, store, per_source
            )
            print(
                f"[{source}] 构造 {len(labeled)} 组标注候选"
                f"（每组 1 对齐 + 1 错位）"
            )
            for group in labeled:
                for label, candidate in group["candidates"]:
                    verdict = await judge.judge(
                        candidate,
                        metadata_context=metadata_context(
                            source, [group["table"]]
                        ),
                    )
                    target = verdict_to_target_status(
                        verdict, min_confidence=SETTINGS.judge_min_confidence
                    )
                    if target == "pass" and candidate.result_truncated:
                        target = "needs_review"
                    results.append(
                        {
                            "source": source,
                            "table": group["table"],
                            "kind": group["kind"],
                            "label": label,
                            "verdict": verdict.verdict,
                            "confidence": verdict.confidence,
                            "target": target,
                            "reason": verdict.reason,
                        }
                    )
                    print(
                        f"  {source[:2]}/{group['table']}/{group['kind']} "
                        f"label={label:<10} verdict={verdict.verdict:<12} "
                        f"conf={verdict.confidence} target={target}"
                    )
        pg_conn.close()
        mysql_conn.close()

    aligned = [r for r in results if r["label"] == "aligned"]
    misaligned = [r for r in results if r["label"] == "misaligned"]
    aligned_pass = sum(1 for r in aligned if r["target"] == "pass")
    aligned_reject = sum(1 for r in aligned if r["target"] == "reject")
    mis_pass = sum(1 for r in misaligned if r["target"] == "pass")
    print("\n=== 汇总 ===")
    print(f"对齐回答（期望 PASS/NEEDS_REVIEW）：{len(aligned)} 条，"
          f"PASS={aligned_pass} NEEDS_REVIEW={len(aligned) - aligned_pass - aligned_reject} "
          f"REJECT={aligned_reject}")
    print(f"错位回答（期望不得 PASS）：{len(misaligned)} 条，"
          f"误自动 PASS={mis_pass}")
    print(
        f"对齐召回（自动 PASS 率）={round(aligned_pass / max(len(aligned), 1) * 100, 1)}%，"
        f"错位误放行率={round(mis_pass / max(len(misaligned), 1) * 100, 1)}%"
    )
    if mis_pass:
        print("以下错位样本被误判 PASS：")
        for r in misaligned:
            if r["target"] == "pass":
                print(f"  {r['source']}/{r['table']}/{r['kind']} {r['reason']}")


if __name__ == "__main__":
    asyncio.run(main())

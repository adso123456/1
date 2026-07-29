"""正式 MySQL Memory 与18条只读 SQL 样例的真实联调。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.mysql_runtime_factory import create_mysql_runtime
from config.data_sources import build_mysql_data_source_config
from training.mysql_lzh_monitor_training import _close_memory
from vanna.capabilities.sql_runner import RunSqlToolArgs


SQL_EXAMPLES = (
    PROJECT_ROOT / "training" / "mysql_lzh_monitor" / "sql_examples.json"
)


async def main() -> int:
    batch = json.loads(SQL_EXAMPLES.read_text(encoding="utf-8"))
    config = build_mysql_data_source_config()
    runtime = create_mysql_runtime(config)
    results: list[tuple[str, bool, str]] = []
    try:
        for sample in batch["samples"]:
            sql = sample["args"]["sql"]
            guard = runtime.sql_guard.validate(sql, query=sample["question"])
            if not guard.passed:
                results.append(
                    (sample["sample_id"], False, f"Guard: {guard.reason}")
                )
                continue
            frame = await runtime.runner.run_sql(RunSqlToolArgs(sql=sql), None)
            results.append(
                (
                    sample["sample_id"],
                    len(frame) > 0 and len(frame) <= 50,
                    f"rows={len(frame)} columns={list(frame.columns)}",
                )
            )

        transaction = await runtime.runner.run_sql(
            RunSqlToolArgs(
                sql="SELECT @@transaction_read_only AS transaction_read_only"
            ),
            None,
        )
        results.append(
            (
                "MYSQL_READ_ONLY_TRANSACTION",
                int(transaction.iloc[0]["transaction_read_only"]) == 1,
                str(transaction.iloc[0]["transaction_read_only"]),
            )
        )

        exact_question = batch["samples"][0]["question"]
        tool_matches = await runtime.memory.search_similar_usage(
            exact_question,
            None,
            limit=5,
            similarity_threshold=0.0,
            tool_name_filter="run_sql",
        )
        results.append(
            (
                "FORMAL_TOOL_MEMORY_RECALL",
                any(item.memory.question == exact_question for item in tool_matches),
                f"matches={len(tool_matches)}",
            )
        )
        text_matches = await runtime.memory.search_text_memories(
            "水质 m2 表示什么",
            None,
            limit=10,
            similarity_threshold=0.0,
        )
        results.append(
            (
                "FORMAL_TEXT_MEMORY_RECALL",
                any("m2 pH" in item.memory.content for item in text_matches),
                f"matches={len(text_matches)}",
            )
        )
    finally:
        _close_memory(runtime.memory)

    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}: {name} | {detail}")
    failures = [name for name, passed, _ in results if not passed]
    print(f"TOTAL={len(results)} PASS={len(results) - len(failures)} FAIL={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

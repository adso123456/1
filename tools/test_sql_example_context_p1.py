"""SQL Example Context Enhancer 的 Level 3 P1 回归测试。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REPORT_PATH = CURRENT_DIR / "sql_example_context_p1_test_result.md"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_example_context_enhancer import (
    ALLOWED_TRAINING_LEVELS,
    SqlExampleContextEnhancer,
)


@dataclass
class Result:
    name: str
    passed: bool
    reason: str


def candidate(*, sample_id: str = "L3_P1_SQL_001",
              level: str = "level3_p1_sql_examples",
              decision: str = "approved", tool_name: str = "run_sql",
              sql: str = "SELECT outlet_name, sampling_time, cod, ammonia_nitrogen FROM rs_outlet_monitor_v2 LIMIT 50",
              expected_tables: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        question="查询排污口监测数据中的COD和氨氮记录",
        tool_name=tool_name,
        args={"sql": sql},
        metadata={
            "sample_id": sample_id,
            "training_level": level,
            "train_decision": decision,
            "expected_tables": expected_tables or ["rs_outlet_monitor_v2"],
        },
    )


def accepted(item: SimpleNamespace) -> tuple[bool, dict[str, Any] | None, str]:
    example, reason = SqlExampleContextEnhancer()._candidate_to_example(item, item.question)
    return reason == "" and example is not None, example, reason


def rejected(item: SimpleNamespace, text: str) -> tuple[bool, str]:
    _, reason = SqlExampleContextEnhancer()._candidate_to_example(item, item.question)
    return text in reason, reason


class FakeBase:
    async def enhance_system_prompt(self, system_prompt: str, user_message: str, user: Any) -> str:
        return system_prompt + "\nBASE"

    async def enhance_user_messages(self, messages: list[Any], user: Any) -> list[Any]:
        return messages


class FakeMemory:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.items = items
        self.tool_filter = ""

    async def search_similar_usage(self, *, question: str, context: Any,
                                   limit: int, tool_name_filter: str) -> list[SimpleNamespace]:
        self.tool_filter = tool_name_filter
        return [SimpleNamespace(memory=item) for item in self.items[:limit]]


async def run() -> list[Result]:
    results: list[Result] = []

    def check(name: str, condition: bool, reason: str) -> None:
        results.append(Result(name, condition, reason))

    expected_levels = {
        "level2_sql_examples",
        "level3_p0_sql_examples",
        "level3_p1_sql_examples",
        "level3_p2_sql_examples",
    }
    check("白名单精确包含 L2/P0/P1/P2", ALLOWED_TRAINING_LEVELS == expected_levels,
          str(sorted(ALLOWED_TRAINING_LEVELS)))

    for name, level in (
        ("Level 2 approved 仍被接受", "level2_sql_examples"),
        ("Level 3 P0 approved 仍被接受", "level3_p0_sql_examples"),
        ("Level 3 P1 approved 被接受", "level3_p1_sql_examples"),
    ):
        ok, _, reason = accepted(candidate(level=level))
        check(name, ok, reason or "accepted")

    ok, reason = rejected(candidate(decision="requires_manual_review"), "not approved")
    check("P1 非 approved 被拒绝", ok, reason)
    ok, reason = rejected(candidate(level="unknown_sql_examples"), "not allowed")
    check("未知 training level 被拒绝", ok, reason)
    ok, reason = rejected(candidate(tool_name="visualize_data"), "not run_sql")
    check("非 run_sql 被拒绝", ok, reason)
    ok, reason = rejected(candidate(sql="SELECT outlet_name FROM rs_outlet_monitor_v2"), "no LIMIT")
    check("无 LIMIT 被拒绝", ok, reason)
    ok, reason = rejected(candidate(sql="SELECT * FROM rs_outlet_monitor_v2 LIMIT 10"), "SELECT *")
    check("SELECT * 被拒绝", ok, reason)
    ok, reason = rejected(candidate(sql="SELECT unknown_column FROM rs_outlet_monitor_v2 LIMIT 10"), "SQL Guard failed")
    check("SQLGuard fail 被拒绝", ok, reason)
    ok, reason = rejected(candidate(expected_tables=["wm_waterquality_day_records"]), "severity is warning")
    check("SQLGuard warning 被拒绝", ok, reason)

    ok, example, reason = accepted(candidate())
    check("P1 approved 解析出正确表", ok and example is not None and example["tables"] == ["rs_outlet_monitor_v2"],
          reason or str(example["tables"] if example else []))

    memory = FakeMemory([candidate()])
    enhancer = SqlExampleContextEnhancer(base_enhancer=FakeBase(), memory=memory)
    prompt = await enhancer.enhance_system_prompt("SYSTEM", "查询排污口监测数据中的COD和氨氮记录", None)
    check("P1 approved 进入 prompt", "L3_P1_SQL_001" in prompt and "rs_outlet_monitor_v2" in prompt,
          f"injected={enhancer.last_stats.injected_count}")
    check("原有 base 和 run_sql 检索契约保持", prompt.startswith("SYSTEM\nBASE") and memory.tool_filter == "run_sql",
          f"tool_filter={memory.tool_filter}")
    return results


def write_report(results: list[Result]) -> None:
    passed = sum(item.passed for item in results)
    lines = [
        "# SQL Example Context Enhancer P1 回归测试结果", "",
        f"- 测试总数：{len(results)}", f"- 通过数量：{passed}",
        f"- 失败数量：{len(results) - passed}",
        f"- 失败列表：{', '.join(item.name for item in results if not item.passed) or '无'}", "",
        "| 用例 | 结果 | 说明 |", "|---|---|---|",
        *[f"| {item.name} | {'pass' if item.passed else 'fail'} | {item.reason} |" for item in results],
        "", "- 是否启动主服务：否", "- 是否连接数据库：否", "- 是否调用 DeepSeek：否",
        "- 是否执行 SQL：否", "- 是否写入 ChromaDB：否", "- 是否调用 vn.train()：否", "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results = asyncio.run(run())
    write_report(results)
    passed = sum(item.passed for item in results)
    print(f"total={len(results)} passed={passed} failed={len(results) - passed}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

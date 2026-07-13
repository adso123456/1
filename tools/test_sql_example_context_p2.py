"""SQL Example Context Enhancer 的 Level 3 P2 回归测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p2_review_result.json"
REPORT_PATH = CURRENT_DIR / "sql_example_context_p2_test_result.md"
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


def load_samples() -> dict[str, dict[str, Any]]:
    rows = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def candidate(
    sample: dict[str, Any], *, level: str = "level3_p2_sql_examples",
    decision: str = "approved", tool_name: str = "run_sql", sql: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        question=sample["question"],
        tool_name=tool_name,
        args={"sql": sample["sql"] if sql is None else sql},
        metadata={
            "sample_id": sample["id"],
            "training_level": level,
            "train_decision": decision,
            "expected_tables": sample["expected_tables"],
            "expected_columns": sample["expected_columns"],
            "join_keys": sample["join_keys"],
        },
    )


def convert(item: SimpleNamespace, guard: Any | None = None) -> tuple[dict[str, Any] | None, str]:
    enhancer = SqlExampleContextEnhancer(sql_guard=guard) if guard else SqlExampleContextEnhancer()
    return enhancer._candidate_to_example(item, item.question)


class FixedGuard:
    def __init__(self, *, passed: bool, severity: str, reason: str) -> None:
        self.result = SimpleNamespace(
            passed=passed, severity=severity, reason=reason, used_tables=[]
        )

    def validate(self, **_: Any) -> SimpleNamespace:
        return self.result


async def run() -> list[Result]:
    samples = load_samples()
    two_table = samples["L3_P2_SQL_001"]
    three_table = samples["L3_P2_SQL_011"]
    results: list[Result] = []

    def check(name: str, condition: bool, reason: str) -> None:
        results.append(Result(name, condition, reason))

    for name, level in (
        ("Level 2 approved 继续接受", "level2_sql_examples"),
        ("Level 3 P0 approved 继续接受", "level3_p0_sql_examples"),
        ("Level 3 P1 approved 继续接受", "level3_p1_sql_examples"),
        ("Level 3 P2 approved 接受", "level3_p2_sql_examples"),
    ):
        example, reason = convert(candidate(two_table, level=level))
        check(name, example is not None and not reason, reason or "accepted")

    _, reason = convert(candidate(two_table, decision="requires_manual_review"))
    check("P2 非 approved 拒绝", "not approved" in reason, reason)
    _, reason = convert(candidate(two_table, decision="excluded"))
    check("P2 excluded 拒绝", "not approved" in reason, reason)
    _, reason = convert(candidate(two_table, tool_name="visualize_data"))
    check("非 run_sql 拒绝", "not run_sql" in reason, reason)
    _, reason = convert(candidate(two_table, sql="SELECT * FROM rs_outlet_info_v2 LIMIT 10"))
    check("SELECT * 拒绝", "SELECT *" in reason, reason)
    _, reason = convert(candidate(two_table, sql="SELECT id FROM rs_outlet_info_v2"))
    check("无 LIMIT 拒绝", "no LIMIT" in reason, reason)

    _, warning_reason = convert(
        candidate(two_table), FixedGuard(passed=True, severity="warning", reason="mismatch")
    )
    _, fail_reason = convert(
        candidate(two_table), FixedGuard(passed=False, severity="fail", reason="blocked")
    )
    check(
        "SQLGuard warning/fail 拒绝",
        "severity is warning" in warning_reason and "SQL Guard failed" in fail_reason,
        f"warning={warning_reason}; fail={fail_reason}",
    )

    example, reason = convert(candidate(two_table))
    check(
        "二表 P2 样本转换正确",
        example is not None and set(example["tables"]) == set(two_table["expected_tables"]),
        reason or str(example),
    )
    example, reason = convert(candidate(three_table))
    check(
        "三表 P2 样本转换正确",
        example is not None and set(example["tables"]) == set(three_table["expected_tables"]),
        reason or str(example),
    )

    item = candidate(two_table)
    convert(item)
    check(
        "expected_tables metadata 保留",
        item.metadata["expected_tables"] == two_table["expected_tables"],
        str(item.metadata["expected_tables"]),
    )
    check(
        "join_keys metadata 保留",
        item.metadata["join_keys"] == two_table["join_keys"],
        str(item.metadata["join_keys"]),
    )

    _, reason = convert(candidate(two_table, level="level3_unknown_sql_examples"))
    check("未知 training level 拒绝", "not allowed" in reason, reason)
    expected_levels = {
        "level2_sql_examples", "level3_p0_sql_examples",
        "level3_p1_sql_examples", "level3_p2_sql_examples",
    }
    check(
        "白名单精确包含 L2/P0/P1/P2",
        ALLOWED_TRAINING_LEVELS == expected_levels,
        str(sorted(ALLOWED_TRAINING_LEVELS)),
    )
    return results


def write_report(results: list[Result]) -> None:
    passed = sum(item.passed for item in results)
    lines = [
        "# SQL Example Context Enhancer P2 测试结果", "",
        f"- 测试总数：{len(results)}",
        f"- 通过数量：{passed}",
        f"- 失败数量：{len(results) - passed}",
        f"- 失败列表：{', '.join(item.name for item in results if not item.passed) or '无'}",
        "", "| 用例 | 结果 | 说明 |", "|---|---|---|",
        *[
            f"| {item.name} | {'pass' if item.passed else 'fail'} | {item.reason} |"
            for item in results
        ],
        "", "- 是否启动主服务：否", "- 是否连接数据库：否",
        "- 是否调用 DeepSeek：否", "- 是否执行 SQL：否",
        "- 是否写入 ChromaDB：否", "- 是否调用 vn.train()：否", "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results = asyncio.run(run())
    write_report(results)
    passed = sum(item.passed for item in results)
    print(json.dumps({
        "total": len(results), "passed": passed, "failed": len(results) - passed,
        "failed_cases": [item.name for item in results if not item.passed],
    }, ensure_ascii=False))
    return 0 if passed == len(results) == 16 else 1


if __name__ == "__main__":
    raise SystemExit(main())

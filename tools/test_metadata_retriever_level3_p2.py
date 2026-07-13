"""Level 3 P2 deterministic metadata 候选测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p2_review_result.json"
REPORT_PATH = CURRENT_DIR / "metadata_retriever_level3_p2_test_result.md"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.test_metadata_retriever_level3_p1 import run_tests as run_p1_tests

FROZEN_TARGET_TABLES = {
    "wm_waterquality_threshold",
    "wm_hydrological_info",
    "gis_region_county",
    "wm_water_source_intake_v2",
}


def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    approved = [row for row in rows if row.get("decision") == "approved"]
    retriever = DeterministicMetadataRetriever()
    results: list[dict[str, Any]] = []
    for row in approved:
        candidates = retriever.retrieve(row["question"], top_n=10)
        names = [item["table_name"] for item in candidates]
        missing = [table for table in row["expected_tables"] if table not in names]
        top1 = names[0] if names else ""
        frozen_target = top1 if top1 in FROZEN_TARGET_TABLES else ""
        passed = not missing and not frozen_target
        results.append({
            "sample_id": row["id"],
            "query": row["question"],
            "expected_tables": row["expected_tables"],
            "top10": names,
            "missing": missing,
            "frozen_target": frozen_target,
            "passed": passed,
            "reason": "全部目标表位于 top10" if passed else (
                f"缺少目标表：{', '.join(missing)}" if missing
                else f"冻结表成为明确 top1：{frozen_target}"
            ),
        })
    passed = sum(item["passed"] for item in results)
    return results, {
        "total": len(results), "passed": passed, "failed": len(results) - passed,
        "failed_cases": [item["sample_id"] for item in results if not item["passed"]],
    }


def write_report(
    results: list[dict[str, Any]], summary: dict[str, Any], p1_summary: dict[str, Any]
) -> None:
    lines = [
        "# Level 3 P2 Metadata 候选测试结果", "",
        f"- P2 测试总数：{summary['total']}",
        f"- P2 通过数量：{summary['passed']}",
        f"- P2 失败数量：{summary['failed']}",
        f"- P2 失败列表：{', '.join(summary['failed_cases']) or '无'}",
        f"- P1 回归：{p1_summary['passed']}/{p1_summary['total']}", "",
        "| sample_id | query | expected_tables | top10 | 结果 | 原因 |",
        "|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['sample_id']} | {item['query']} | {', '.join(item['expected_tables'])} | "
            f"{', '.join(item['top10'])} | {'pass' if item['passed'] else 'fail'} | {item['reason']} |"
        )
    lines.extend([
        "", "- 排污口单表监测回归：由 P1 T8 保证",
        "- 月度/年度水质回归：由 P1 T10、N1-N4、N7、N9 保证",
        "- 普通取水口/水源地回归：由 P1 T11-T13 保证",
        "- 是否连接数据库：否", "- 是否启动主服务：否",
        "- 是否训练：否", "- 是否写入 ChromaDB：否", "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    results, summary = run_tests()
    _, p1_summary = run_p1_tests()
    write_report(results, summary, p1_summary)
    payload = {**summary, "p1_passed": p1_summary["passed"], "p1_total": p1_summary["total"]}
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if summary["failed"] == 0 and p1_summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

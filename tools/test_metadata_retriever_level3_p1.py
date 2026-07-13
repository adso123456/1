"""Level 3 P1 deterministic metadata 候选排序回归测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level3_p1_review_result.json"
REPORT_PATH = CURRENT_DIR / "metadata_retriever_level3_p1_test_result.md"
BASE_COMMIT = "2915199a7c688f8a2a4e1f4330c56e47526a6f0b"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.test_metadata_retriever import run_tests as run_existing_tests


@dataclass(frozen=True)
class Case:
    case_id: str
    group: str
    query: str
    target: str = ""
    top1: str = ""
    top1_any: tuple[str, ...] = ()
    contains_all: tuple[str, ...] = ()
    frozen_route: bool = False
    threshold_guard: bool = False
    annual_non_waterquality_guard: bool = False
    forbidden_top1: tuple[str, ...] = ()


def load_review_questions() -> dict[str, str]:
    rows = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    return {row["id"]: row["question"] for row in rows}


def build_cases() -> list[Case]:
    questions = load_review_questions()
    return [
        Case("T1", "P1 修复", questions["L3_P1_SQL_007"], "rs_wastewater_day_records", "rs_wastewater_day_records"),
        Case("T2", "P1 修复", questions["L3_P1_SQL_009"], "rs_wastewater_month_records", "rs_wastewater_month_records"),
        Case("T3", "P1/Level2 回归", questions["L3_P1_SQL_008"], "rs_wastewater_hour_records", "rs_wastewater_hour_records"),
        Case("T4", "P1 修复", questions["L3_P1_SQL_013"], "wm_hydrological_info", "wm_hydrological_info"),
        Case("T5", "P1 回归", questions["L3_P1_SQL_012"], "wm_hydrological_info", "wm_hydrological_info"),
        Case("T6", "P1 修复", questions["L3_P1_SQL_023"], "wm_water_source", "wm_water_source"),
        Case("T7", "P1 修复", questions["L3_P1_SQL_024"], "wm_water_source", "wm_water_source"),
        Case("T8", "区分回归", questions["L3_P1_SQL_001"], "rs_outlet_monitor_v2", "rs_outlet_monitor_v2"),
        Case(
            "T9", "P0 回归", "查询排污口编码", "rs_outlet", top1_any=("rs_outlet", "rs_outlet_info_v2"),
            contains_all=("rs_outlet", "rs_outlet_info_v2"),
        ),
        Case("T10", "P0 回归", "查询月度水质为 I 至 III 类的站点列表", "wm_waterquality_month_records", "wm_waterquality_month_records"),
        Case("T11", "区分回归", questions["L3_P1_SQL_019"], "wm_water_intake", "wm_water_intake"),
        Case("T12", "区分回归", questions["L3_P1_SQL_021"], "wm_water_intake", "wm_water_intake"),
        Case("T13", "冻结保护", "查询水源地取水口供水能力", "wm_water_source", frozen_route=True),
        Case("T14", "安全回归", "查询 wm_waterquality_threshold 中的水质趋势", threshold_guard=True),
        Case("N1", "年度水质", "查询年度pH年均值最高的站点列表", "wm_waterquality_year_records", "wm_waterquality_year_records"),
        Case("N2", "年度水质", "按年查看各站点溶解氧平均值", "wm_waterquality_year_records", "wm_waterquality_year_records"),
        Case("N3", "年度水质", "查询各监测站点年度氨氮平均值", "wm_waterquality_year_records", "wm_waterquality_year_records"),
        Case("N4", "年度水质", "查询年度水质等级为I至III类的站点", "wm_waterquality_year_records", "wm_waterquality_year_records"),
        Case(
            "N5", "冲突保护", questions["L3_P1_SQL_002"], "rs_outlet_monitor_v2",
            "rs_outlet_monitor_v2", forbidden_top1=("wm_waterquality_year_records",),
        ),
        Case(
            "N6", "冲突保护", questions["L3_P1_SQL_024"], "wm_water_source",
            "wm_water_source", forbidden_top1=("wm_waterquality_year_records",),
        ),
        Case("N7", "粒度回归", "查询月度水质为I至III类的站点列表", "wm_waterquality_month_records", "wm_waterquality_month_records"),
        Case(
            "N8", "非水质保护", "查询年度企业数量", "wm_waterquality_year_records",
            annual_non_waterquality_guard=True,
        ),
        Case(
            "N9", "粒度回归", "查看某站点近两年pH和溶解氧月变化趋势",
            "wm_waterquality_month_records", "wm_waterquality_month_records",
        ),
    ]


def rank_of(candidates: list[dict[str, Any]], table_name: str) -> int | None:
    for index, candidate in enumerate(candidates, start=1):
        if candidate["table_name"] == table_name:
            return index
    return None


def check_case(case: Case, candidates: list[dict[str, Any]]) -> tuple[bool, str]:
    names = [candidate["table_name"] for candidate in candidates]
    top1 = names[0] if names else ""
    if case.frozen_route:
        source = next((item for item in candidates if item["table_name"] == "wm_water_source"), None)
        newly_approved = bool(source and "water_source_base_intent" in source["matched_by"])
        return not newly_approved, "冻结口径未获得 water_source_base_intent" if not newly_approved else "冻结口径被新增规则误判"
    if case.threshold_guard:
        records = {"wm_waterquality_day_records", "wm_waterquality_hour_records", "wm_waterquality_month_records", "wm_waterquality_year_records"}
        passed = top1 in records and top1 != "wm_waterquality_threshold"
        return passed, f"top1={top1}"
    if case.annual_non_waterquality_guard:
        year_candidate = next(
            (item for item in candidates if item["table_name"] == "wm_waterquality_year_records"),
            None,
        )
        annual_method_added = bool(
            year_candidate
            and "annual_waterquality_granularity" in year_candidate["matched_by"]
        )
        passed = top1 != "wm_waterquality_year_records" and not annual_method_added
        return passed, "年度非水质问题未获得年度水质意图加分" if passed else "年度非水质问题被水质年表污染"
    if top1 in case.forbidden_top1:
        return False, f"top1 不得为 {top1}"
    if case.top1 and top1 != case.top1:
        return False, f"期望 top1={case.top1}，实际 top1={top1}"
    if case.top1_any and top1 not in case.top1_any:
        return False, f"top1={top1} 不在允许集合"
    missing = [table for table in case.contains_all if table not in names]
    if missing:
        return False, "top10 缺少：" + ", ".join(missing)
    if case.target and case.target not in names:
        return False, f"top10 缺少目标表 {case.target}"
    return True, f"top1={top1}，目标排名={rank_of(candidates, case.target)}"


def run_tests() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retriever = DeterministicMetadataRetriever()
    results: list[dict[str, Any]] = []
    for case in build_cases():
        candidates = retriever.retrieve(case.query, top_n=10)
        passed, reason = check_case(case, candidates)
        target = next((item for item in candidates if item["table_name"] == case.target), None)
        results.append({
            "case_id": case.case_id,
            "group": case.group,
            "query": case.query,
            "top10": [item["table_name"] for item in candidates],
            "target": case.target,
            "target_rank": rank_of(candidates, case.target) if case.target else None,
            "target_score": target["score"] if target else None,
            "matched_by": target["matched_by"] if target else [],
            "target_reason": target["reason"] if target else "",
            "passed": passed,
            "reason": reason,
        })
    passed = sum(item["passed"] for item in results)
    return results, {
        "total": len(results), "passed": passed, "failed": len(results) - passed,
        "failed_cases": [item["case_id"] for item in results if not item["passed"]],
    }


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    ).stdout.strip()


def write_report(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    existing_summary: dict[str, Any],
) -> None:
    by_id = {item["case_id"]: item for item in results}
    lines = [
        "# Level 3 P1 Metadata 候选排序测试结果", "",
        f"- 基础 commit：{BASE_COMMIT}",
        "- 修改文件：tools/metadata_retriever.py、tools/test_metadata_retriever_level3_p1.py、tools/metadata_retriever_level3_p1_test_result.md",
        f"- 测试总数：{summary['total']}", f"- 通过数量：{summary['passed']}",
        f"- 失败数量：{summary['failed']}", f"- 失败列表：{', '.join(summary['failed_cases']) or '无'}",
        f"- 原有 P0 检索回归：{existing_summary['passed']}/{existing_summary['total']}",
        f"- 原有高风险表回归：{existing_summary['high_risk_fixed']}/{existing_summary['high_risk_total']}",
        f"- 原有水质趋势回归：{existing_summary['trend_passed']}/{existing_summary['trend_total']}",
        "", "## 四个原 warning 目标表", "",
        f"- P1-Q5：rs_wastewater_day_records 排名 {by_id['T1']['target_rank']}",
        f"- P1-Q7：rs_wastewater_month_records 排名 {by_id['T2']['target_rank']}",
        f"- P1-Q9：wm_hydrological_info 排名 {by_id['T4']['target_rank']}",
        f"- P1-Q16：wm_water_source 排名 {by_id['T6']['target_rank']}",
        "", "## 区分与保护", "",
        f"- 废水监测/废水记录区分：{'通过' if by_id['T8']['passed'] else '失败'}",
        f"- 水源地/普通取水口区分：{'通过' if by_id['T11']['passed'] and by_id['T12']['passed'] else '失败'}",
        f"- 冻结水源地取水口口径：{'保持冻结' if by_id['T13']['passed'] else '被误放开'}",
        f"- P0/Level2 回归：{'通过' if all(by_id[key]['passed'] for key in ('T3', 'T8', 'T9', 'T10', 'T14')) else '失败'}",
        "", "## 明细", "",
        "| ID | 分组 | query | top10 | 目标排名 | 目标 score | matched_by | reason | 结果 |",
        "|---|---|---|---|---:|---:|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['case_id']} | {item['group']} | {item['query']} | {', '.join(item['top10'])} | "
            f"{item['target_rank'] or '-'} | {item['target_score'] if item['target_score'] is not None else '-'} | "
            f"{', '.join(item['matched_by']) or '-'} | {item['target_reason'] or item['reason']} | "
            f"{'pass' if item['passed'] else 'fail'} |"
        )
    lines.extend([
        "", "## 执行约束", "", "- 是否连接数据库：否", "- 是否启动主服务：否",
        "- 是否训练：否", "- 是否调用 vn.train()：否", "- 是否调用 memory.save_tool_usage()：否",
        "- 是否写入 ChromaDB：否", "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if git_output("merge-base", "--is-ancestor", BASE_COMMIT, "HEAD") != "":
        raise SystemExit(f"当前 HEAD 不包含基础 commit {BASE_COMMIT}")
    results, summary = run_tests()
    _, existing_summary = run_existing_tests()
    write_report(results, summary, existing_summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed"] == 0 and existing_summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

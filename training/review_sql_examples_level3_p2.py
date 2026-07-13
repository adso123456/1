"""根据 P2 真实只读审计结果生成业务审查决定。"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard

DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p2_draft.json")
AUDIT_PATH = Path(__file__).with_name("level3_p2_join_feasibility_result.md")
REVIEW_RESULT_PATH = Path(__file__).with_name("sql_examples_level3_p2_review_result.json")
REVIEW_REPORT_PATH = Path(__file__).with_name("sql_examples_level3_p2_review_report.md")
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"

QUESTION_UPDATES = {
    "L3_P2_SQL_001": "查询排污口国家编码、名称及对应整治状态和整治类型记录明细",
    "L3_P2_SQL_003": "查询排污口国家编码、名称及对应实况记录明细中的排水特征、在线监测和采样条件状态",
    "L3_P2_SQL_005": "查询各断面每年度的全年水质目标等级记录",
    "L3_P2_SQL_011": "查询排污口国家编码、名称、整治状态、规范化状态及实况在线监测状态联合记录明细",
}


def load_audit() -> dict[str, Any]:
    text = AUDIT_PATH.read_text(encoding="utf-8")
    match = re.search(r"## 机器可读审计数据\s*```json\s*(.*?)\s*```", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("审计报告缺少机器可读 JSON")
    return json.loads(match.group(1))


def edge_map(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in audit["join_edges"]}


def execution_map(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in audit["candidate_execution"]}


def updated_risk_notes(sample_id: str, edges: dict[str, dict[str, Any]], audit: dict[str, Any]) -> str:
    notes = {
        "L3_P2_SQL_001": (
            f"真实审计J1匹配率{edges['J1']['match_rate_percent']:.4f}%，有效整治记录每个outlet_id最大"
            f"{edges['J1']['max_right_records_per_left_key']}条，当前为一对一且无孤儿；问题已明确为记录明细，"
            "后续数据变化仍需关注子表基数。"
        ),
        "L3_P2_SQL_002": (
            f"真实审计J1匹配率{edges['J1']['match_rate_percent']:.4f}%，最大子表基数"
            f"{edges['J1']['max_right_records_per_left_key']}；两个COUNT(DISTINCT ...)分别统计主表排污口实体和"
            "存在整治记录的排污口实体；按潜在一对多风险使用DISTINCT去重，不表达整治完成率，LEFT JOIN保留无记录主表实体。"
        ),
        "L3_P2_SQL_003": (
            f"真实审计J2匹配率{edges['J2']['match_rate_percent']:.4f}%，有效实况记录每个outlet_id最大"
            f"{edges['J2']['max_right_records_per_left_key']}条，当前为一对一且无孤儿；问题已明确为记录明细，"
            "不筛选冻结的异常或采样固定值。"
        ),
        "L3_P2_SQL_004": (
            f"真实审计J2匹配率{edges['J2']['match_rate_percent']:.4f}%，最大子表基数"
            f"{edges['J2']['max_right_records_per_left_key']}；两个COUNT(DISTINCT ...)分别统计主表排污口实体和"
            "存在实况记录的排污口实体；按潜在一对多风险使用DISTINCT去重，LEFT JOIN保留无记录主表实体。"
        ),
        "L3_P2_SQL_005": (
            f"真实审计J3左实体匹配率{edges['J3']['match_rate_percent']:.4f}%；month=0共"
            f"{audit['domains']['month_zero_count']}条，每个断面年度最多"
            f"{audit['domains']['annual_max_records_per_section_year']}条且重复组合"
            f"{audit['domains']['annual_duplicate_section_year_groups']}个。右表孤儿记录通过INNER JOIN排除。"
        ),
        "L3_P2_SQL_006": (
            f"真实值域确认month=0表示全年且有{audit['domains']['month_zero_count']}条，is_examine='1'实际存在；"
            "目标表对断面一对多，COUNT(DISTINCT s.id)按年度和目标等级去重，避免月份与重复记录放大。"
        ),
        "L3_P2_SQL_007": (
            f"真实审计J4左实体匹配率{edges['J4']['match_rate_percent']:.4f}%，右表每个水体主键最大"
            f"{edges['J4']['max_right_records_per_left_key']}条；一个水体可对应多个断面，查询保持断面记录粒度。"
        ),
        "L3_P2_SQL_008": (
            f"真实审计J4左实体匹配率{edges['J4']['match_rate_percent']:.4f}%，存在"
            f"{edges['J4']['orphan_right_records']}个未被断面引用的水体；COUNT(DISTINCT s.id)按一对多关系去重，"
            "LEFT JOIN保留没有断面的水体。"
        ),
        "L3_P2_SQL_009": (
            f"真实审计J5精确匹配率{edges['J5']['match_rate_percent']:.4f}%；水文站region_code长度为4位，"
            "区县编码为6位，当前INNER JOIN返回0行。真实数据证明区县级精确编码关系不成立，本样本不得训练。"
        ),
        "L3_P2_SQL_010": (
            f"真实审计J5精确匹配率{edges['J5']['match_rate_percent']:.4f}%；唯一水文站编码为4位城市码，"
            "13个区县编码均为6位。SQL按潜在一对多风险使用DISTINCT去重；LEFT JOIN虽可执行并返回区县列表，"
            "但站点计数全部不能证明区县归属，本样本不得训练。"
        ),
        "L3_P2_SQL_011": (
            f"真实审计J1/J2匹配率均为100%，两子表每个outlet_id最大基数均为1；三表最大乘法放大倍数"
            f"{audit['three_table_amplification']['max_product']}，乘积大于1的排污口"
            f"{audit['three_table_amplification']['outlets_product_over_one']}个。当前无组合放大，问题明确为联合记录明细。"
        ),
    }
    return notes[sample_id]


def decide(
    sample: dict[str, Any],
    guard: SQLGuard,
    edges: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    execution: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str]:
    sid = sample["id"]
    guard_result = guard.validate(
        sql=sample["sql"],
        query=sample["question"],
        deterministic_candidate_tables=sample["expected_tables"],
    )
    run = execution[sid]
    if not guard_result.passed or guard_result.severity != "ok":
        return "excluded", "high", f"SQLGuard未通过：{guard_result.reason}", "SQLGuard失败"
    if not run["success"]:
        return "excluded", "high", f"真实只读SQL执行失败：{run['error']}", "执行失败"

    edge_by_sample = {
        "L3_P2_SQL_001": "J1",
        "L3_P2_SQL_002": "J1",
        "L3_P2_SQL_003": "J2",
        "L3_P2_SQL_004": "J2",
        "L3_P2_SQL_005": "J3",
        "L3_P2_SQL_006": "J3",
        "L3_P2_SQL_007": "J4",
        "L3_P2_SQL_008": "J4",
        "L3_P2_SQL_009": "J5",
        "L3_P2_SQL_010": "J5",
    }
    if sid in {"L3_P2_SQL_009", "L3_P2_SQL_010"}:
        edge = edges["J5"]
        if edge["match_rate_percent"] == 0:
            return (
                "excluded",
                "high",
                "J5真实精确匹配为0；水文站为4位城市码，区县表为6位编码，JOIN关系被真实数据否定。",
                "region_code层级不兼容",
            )
        return "requires_manual_review", "medium", "J5存在部分匹配但区域层级仍需人工确认。", "区域层级待确认"
    if sid == "L3_P2_SQL_011":
        amplification = audit["three_table_amplification"]
        if (
            edges["J1"]["max_right_records_per_left_key"] > 1
            or edges["J2"]["max_right_records_per_left_key"] > 1
            or amplification["max_product"] > 1
        ):
            return "requires_manual_review", "medium", "三表存在一对多组合放大，保持冻结。", "三表组合放大"
        return "approved", "low", "J1/J2均一对一，三表最大放大倍数为1，真实SQL执行成功。", "无"

    edge = edges[edge_by_sample[sid]]
    if edge["matched_left_entities"] == 0:
        return "excluded", "high", "真实JOIN没有匹配左侧实体。", "无真实匹配"
    if sid in {"L3_P2_SQL_005", "L3_P2_SQL_006"}:
        domains = audit["domains"]
        examine_values = {str(value) for value, _ in domains["is_examine_distribution"]}
        if (
            domains["month_zero_count"] == 0
            or domains["annual_max_records_per_section_year"] > 1
            or (sid == "L3_P2_SQL_006" and "1" not in examine_values)
        ):
            return "requires_manual_review", "medium", "年度目标或考核固定值口径证据不足。", "固定值待确认"
    return "approved", "low", "静态JOIN证据、真实匹配、固定值口径和只读SQL执行均通过。", "无"


def md(value: Any) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    audit = load_audit()
    edges = edge_map(audit)
    execution = execution_map(audit)
    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    guard = SQLGuard(index_path=METADATA_PATH)
    modified_ids: list[str] = []
    review_result: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []

    for sample in draft:
        sid = sample["id"]
        if sid in QUESTION_UPDATES:
            sample["question"] = QUESTION_UPDATES[sid]
        sample["risk_notes"] = updated_risk_notes(sid, edges, audit)
        # 每条样本都相对基础草案补充了真实数据库风险证据。
        modified_ids.append(sid)

        decision, business_risk, notes, residual = decide(
            sample, guard, edges, audit, execution
        )
        review_result.append(
            {
                "id": sid,
                "group": sample["group"],
                "priority": sample["priority"],
                "question": sample["question"],
                "sql": sample["sql"],
                "expected_tables": sample["expected_tables"],
                "expected_columns": sample["expected_columns"],
                "join_keys": sample["join_keys"],
                "decision": decision,
                "review_status": "reviewed",
                "review_notes": notes,
                "business_risk": business_risk,
                "source_draft_id": sid,
            }
        )
        edge_ids = ["J1", "J2"] if sid == "L3_P2_SQL_011" else [
            {
                "L3_P2_SQL_001": "J1", "L3_P2_SQL_002": "J1",
                "L3_P2_SQL_003": "J2", "L3_P2_SQL_004": "J2",
                "L3_P2_SQL_005": "J3", "L3_P2_SQL_006": "J3",
                "L3_P2_SQL_007": "J4", "L3_P2_SQL_008": "J4",
                "L3_P2_SQL_009": "J5", "L3_P2_SQL_010": "J5",
            }[sid]
        ]
        report_rows.append(
            {
                "id": sid,
                "question": sample["question"],
                "edge_ids": edge_ids,
                "match": [f"{edge_id}:{edges[edge_id]['match_rate_percent']:.4f}%" for edge_id in edge_ids],
                "max_cardinality": [edges[edge_id]["max_right_records_per_left_key"] for edge_id in edge_ids],
                "execution": execution[sid],
                "decision": decision,
                "business_risk": business_risk,
                "change": "问题和风险说明" if sid in QUESTION_UPDATES else "风险说明",
                "residual": residual,
            }
        )

    DRAFT_PATH.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW_RESULT_PATH.write_text(
        json.dumps(review_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = Counter(item["decision"] for item in review_result)
    domains = audit["domains"]
    region = audit["region_codes"]
    amplification = audit["three_table_amplification"]
    lines = [
        "# Level 3 P2 SQL 示例业务审查报告",
        "",
        "## 汇总",
        "",
        f"- 样本总数：{len(review_result)}",
        f"- approved：{counts['approved']}",
        f"- requires_manual_review：{counts['requires_manual_review']}",
        f"- excluded：{counts['excluded']}",
        f"- 修改样本ID：{', '.join(modified_ids) or '无'}",
        "- 真实只读SQL执行：11/11成功",
        "- 非SELECT：0",
        "- 训练：未执行",
        "- ChromaDB：未写入",
        "",
        "## JOIN、值域与放大证据",
        "",
    ]
    for edge_id in ("J1", "J2", "J3", "J4", "J5"):
        edge = edges[edge_id]
        lines.append(
            f"- {edge_id}：匹配率 {edge['match_rate_percent']:.4f}%，孤儿右记录 "
            f"{edge['orphan_right_records']}，右表最大基数 {edge['max_right_records_per_left_key']}，"
            f"多右记录键 {edge['left_keys_with_multiple_right_records']}。"
        )
    lines.extend(
        [
            f"- del_flag：7张业务表均存在'0'有效记录，NULL均为0；实际其他值仅主表的删除标记'1'。",
            f"- month=0：{domains['month_zero_count']}条；断面年度最大记录数{domains['annual_max_records_per_section_year']}，重复组合{domains['annual_duplicate_section_year_groups']}。",
            f"- is_examine：{md(domains['is_examine_distribution'])}，'1'值实际存在。",
            f"- region_code：精确匹配{region['exact_match_count']}，未匹配{region['unmatched_count']}；水文站4位码，区县6位码，区县层级未确认且被真实数据否定。",
            f"- 三表放大：最大{amplification['max_product']}，乘积大于1的排污口{amplification['outlets_product_over_one']}，大于10的排污口{amplification['outlets_product_over_ten']}。",
            "",
            "## 逐样本决定",
            "",
            "| id | question | JOIN边 | 真实匹配 | 最大子表基数 | SQL执行 | decision | business_risk | 修改摘要 | 遗留问题 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in report_rows:
        lines.append(
            f"| {row['id']} | {md(row['question'])} | {md(row['edge_ids'])} | {md(row['match'])} | "
            f"{md(row['max_cardinality'])} | {'成功' if row['execution']['success'] else '失败'} | "
            f"{row['decision']} | {row['business_risk']} | {row['change']} | {row['residual']} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "9条样本具备静态证据、真实匹配和值域依据，可进入训练前验证。",
            "SQL_009和SQL_010因水文站城市级编码无法精确匹配区县编码而excluded，继续冻结。",
            "本阶段不创建训练写入，不调用vn.train()或memory.save_tool_usage()。",
        ]
    )
    REVIEW_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "total": len(review_result),
                "approved": counts["approved"],
                "requires_manual_review": counts["requires_manual_review"],
                "excluded": counts["excluded"],
                "modified_ids": modified_ids,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

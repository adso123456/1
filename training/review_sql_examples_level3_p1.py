"""Level 3 P1 SQL 候选样本业务审查与报告生成。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard

TRAINING_DIR = Path(__file__).resolve().parent
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p1_draft.json"
REVIEW_RESULT_PATH = TRAINING_DIR / "sql_examples_level3_p1_review_result.json"
REPORT_PATH = TRAINING_DIR / "sql_examples_level3_p1_review_report.md"
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
BASE_COMMIT = "ad10a44c802154ede2e1296826ca63732a6b523e"

MANUAL_REVIEW_IDS = {
    "L3_P1_SQL_004",
    "L3_P1_SQL_005",
    "L3_P1_SQL_010",
}
MODIFIED_IDS = {
    "L3_P1_SQL_006",
    "L3_P1_SQL_013",
    "L3_P1_SQL_019",
    "L3_P1_SQL_024",
}
ENUM_UNCONFIRMED_IDS = sorted(MANUAL_REVIEW_IDS)
INDICATOR_UNCONFIRMED_IDS: list[str] = []
STATISTICS_ADJUSTED_IDS = ["L3_P1_SQL_013", "L3_P1_SQL_019"]
QUESTION_SQL_FIXED_IDS = [
    "L3_P1_SQL_013",
    "L3_P1_SQL_019",
    "L3_P1_SQL_024",
]

REVIEW_NOTES = {
    "L3_P1_SQL_001": "metadata明确支持outlet_name、sampling_time、cod和ammonia_nitrogen；问题、字段和按采样时间倒序的SQL一致。",
    "L3_P1_SQL_002": "metadata明确支持排污口监测表的ph、bod和flow；SQL按sampling_time倒序表达最近记录，未混用水质m字段。",
    "L3_P1_SQL_003": "只展示drainage_feature和has_online_monitor原始状态，不对状态枚举作判断，单表边界清晰。",
    "L3_P1_SQL_004": "has_abnormal为varchar，metadata仅说明“有无异常状况”，未提供允许值、示例值或枚举；无法确认'是'为真实值。",
    "L3_P1_SQL_005": "is_remediated为varchar，metadata仅说明“是否完成整治”，未提供允许值、示例值或枚举；无法确认'是'为真实值。",
    "L3_P1_SQL_006": "SQL只筛选已记录状态并展示is_standardized；已补充risk_notes，明确非空不等于已完成规范化建设。",
    "L3_P1_SQL_007": "metadata字段注释明确给出PS条件下m1=COD、m2=总氮、m3=pH，SQL保持type='PS'，指标映射有直接证据。",
    "L3_P1_SQL_008": "小时表仅使用timestamp、type、status、ll和pfl，未使用语义不明确的m1-m22字段。",
    "L3_P1_SQL_009": "metadata字段注释明确给出PS条件下月度m1=COD、m2=总氮、m3=pH，SQL保持type='PS'。",
    "L3_P1_SQL_010": "has_sampling_condition为varchar，metadata仅说明“是否具备采样条件”，未提供允许值、示例值或枚举；无法确认'是'为真实值。",
    "L3_P1_SQL_011": "断面字段均有明确metadata注释；water_body_id仅展示，未进入断面与水体JOIN。",
    "L3_P1_SQL_012": "水文站基础字段语义明确，已排除联系人、电话、geom及跨表关联。",
    "L3_P1_SQL_013": "已将问题修正为按城市统计记录数，并改用COUNT(*)；不再声称按station_code去重或统计唯一站点。",
    "L3_P1_SQL_014": "水体编码、名称、类型、功能、流域、长度和面积均有明确metadata支持，未查询geom。",
    "L3_P1_SQL_015": "摄像头设备字段语义明确，问题只覆盖单表基础信息和监控对象，未关联站点。",
    "L3_P1_SQL_016": "平台设备、厂商、型号、传输和在线状态字段明确，未查询IP、端口或账号凭据。",
    "L3_P1_SQL_017": "drone_device_model仅作为原始字段展示，SQL未解析JSON内部结构；设备身份和在线状态字段有metadata支持。",
    "L3_P1_SQL_018": "固定使用gis_region_county单表，region_name、region_code和city字段语义明确。",
    "L3_P1_SQL_019": "已将问题修正为按水源类型统计记录数，并改用COUNT(*)；不声称名称唯一或按取水口去重。",
    "L3_P1_SQL_020": "普通取水口的name、city、county和water_type字段明确，未关联区域表。",
    "L3_P1_SQL_021": "查询对象明确为wm_water_intake普通取水口，状态和区域字段均有metadata支持，未混入水源地取水口。",
    "L3_P1_SQL_022": "wm_water_source的名称、类型、状态和区域字段明确，问题与SQL一致。",
    "L3_P1_SQL_023": "只展示数据库记录的保护等级、划定状态和文号，不推断保护合规结论。",
    "L3_P1_SQL_024": "问题已修正为按年实际取水量降序，supply_water_year为主排序，service_people_count仅辅助展示和次级排序。",
}

BUSINESS_RISKS = {
    "L3_P1_SQL_004": "medium",
    "L3_P1_SQL_005": "medium",
    "L3_P1_SQL_007": "low",
    "L3_P1_SQL_009": "low",
    "L3_P1_SQL_010": "medium",
    "L3_P1_SQL_013": "low",
    "L3_P1_SQL_017": "low",
    "L3_P1_SQL_019": "low",
    "L3_P1_SQL_024": "low",
}

MODIFICATION_SUMMARIES = {
    "L3_P1_SQL_006": "补充状态展示边界，明确非空不等于已完成规范化建设",
    "L3_P1_SQL_013": "问题改为统计记录数，COUNT(station_code)改为COUNT(*)并同步字段和说明",
    "L3_P1_SQL_019": "问题改为统计记录数，COUNT(name)改为COUNT(*)并同步字段和说明",
    "L3_P1_SQL_024": "消除双重排名歧义，明确按年实际取水量主排序",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, check=True,
        capture_output=True, text=True, encoding="utf-8",
    )
    return result.stdout.rstrip()


def decision_for(sample_id: str) -> str:
    return "requires_manual_review" if sample_id in MANUAL_REVIEW_IDS else "approved"


def build_review_result(draft: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for sample in draft:
        sid = sample["id"]
        results.append({
            "id": sid,
            "group": sample["group"],
            "priority": sample["priority"],
            "question": sample["question"],
            "sql": sample["sql"],
            "expected_tables": sample["expected_tables"],
            "expected_columns": sample["expected_columns"],
            "decision": decision_for(sid),
            "review_status": "reviewed",
            "review_notes": REVIEW_NOTES[sid],
            "business_risk": BUSINESS_RISKS.get(sid, "none"),
            "source_draft_id": sid,
        })
    return results


def validate_results(
    draft: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    expected_ids = [f"L3_P1_SQL_{index:03d}" for index in range(1, 25)]
    if len(draft) != 24 or len(reviewed) != 24:
        errors.append("draft和review_result均必须为24条")
    if [item.get("id") for item in draft] != expected_ids:
        errors.append("draft ID不连续")
    if [item.get("id") for item in reviewed] != expected_ids:
        errors.append("review_result ID不连续")

    guard = SQLGuard(METADATA_PATH)
    for source, result in zip(draft, reviewed):
        sid = source["id"]
        for field in (
            "id", "group", "priority", "question", "sql",
            "expected_tables", "expected_columns",
        ):
            if source[field] != result[field]:
                errors.append(f"{sid} draft/review字段不一致: {field}")
        if result["decision"] not in {
            "approved", "requires_manual_review", "excluded",
        }:
            errors.append(f"{sid} decision非法")
        if not result["review_notes"]:
            errors.append(f"{sid} review_notes为空")
        if result["source_draft_id"] != sid:
            errors.append(f"{sid} source_draft_id不一致")
        guard_result = guard.validate(
            sql=source["sql"],
            query=source["question"],
            deterministic_candidate_tables=source["expected_tables"],
        )
        if not guard_result.passed or guard_result.severity != "ok":
            errors.append(f"{sid} SQLGuard未通过静态安全检查")
    return errors


def md(value: Any) -> str:
    text = str(value) if value not in (None, "") else "无"
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(
    draft: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    errors: list[str],
) -> None:
    approved = sum(item["decision"] == "approved" for item in reviewed)
    manual = sum(item["decision"] == "requires_manual_review" for item in reviewed)
    excluded = sum(item["decision"] == "excluded" for item in reviewed)
    modified = sorted(MODIFIED_IDS)
    unmodified = [item["id"] for item in reviewed if item["id"] not in MODIFIED_IDS]
    lines = [
        "# Level 3 P1 SQL 候选业务审查报告",
        "",
        "## 汇总",
        "",
        f"- 工作目录：`{PROJECT_ROOT}`",
        f"- 基础 commit：`{BASE_COMMIT}`",
        "- 是否启动主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否调用 `vn.train()`：否",
        "- 是否调用 `memory.save_tool_usage()`：否",
        "- 是否写入 ChromaDB：否",
        f"- 审查样本总数：{len(reviewed)}",
        f"- approved 数量：{approved}",
        f"- requires_manual_review 数量：{manual}",
        f"- excluded 数量：{excluded}",
        f"- 修改过的样本 ID：{', '.join(modified) or '无'}",
        f"- 未修改样本 ID：{', '.join(unmodified) or '无'}",
        f"- 枚举值无法确认的样本：{', '.join(ENUM_UNCONFIRMED_IDS) or '无'}",
        f"- 指标语义无法确认的样本：{', '.join(INDICATOR_UNCONFIRMED_IDS) or '无'}",
        f"- 统计口径调整样本：{', '.join(STATISTICS_ADJUSTED_IDS) or '无'}",
        f"- 问题/SQL不一致修正样本：{', '.join(QUESTION_SQL_FIXED_IDS) or '无'}",
        "- P2 或冻结越界数量：0",
        "",
        "## 逐样本决定",
        "",
        "| id | group | question | decision | business_risk | 是否修改 | 修改摘要 | 审查依据 | 遗留问题 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in reviewed:
        sid = item["id"]
        manual_issue = (
            "缺少字段真实枚举值证据，保持冻结"
            if item["decision"] == "requires_manual_review" else "无"
        )
        lines.append(
            "| " + " | ".join([
                sid,
                item["group"],
                md(item["question"]),
                item["decision"],
                item["business_risk"],
                "是" if sid in MODIFIED_IDS else "否",
                md(MODIFICATION_SUMMARIES.get(sid, "无")),
                md(item["review_notes"]),
                manual_issue,
            ]) + " |"
        )
    lines.extend([
        "",
        "## 校验错误",
        "",
        *(f"- {error}" for error in errors),
    ])
    if not errors:
        lines.append("- 无")
    lines.extend([
        "",
        "## 结论",
        "",
        "**通过。**" if not errors else "**未通过。**",
        "",
        "24 条样本已完成逐条业务审查。后续训练前验证和受控写入只能使用 approved 子集；3 条枚举值缺少证据的样本保持人工复核状态。",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    draft = load_json(DRAFT_PATH)
    if not isinstance(draft, list):
        print("[FAIL] draft必须是JSON数组")
        return 1
    reviewed = build_review_result(draft)
    REVIEW_RESULT_PATH.write_text(
        json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    errors = validate_results(draft, reviewed)
    write_report(draft, reviewed, errors)
    summary = {
        "total": len(reviewed),
        "approved": sum(item["decision"] == "approved" for item in reviewed),
        "requires_manual_review": sum(
            item["decision"] == "requires_manual_review" for item in reviewed
        ),
        "excluded": sum(item["decision"] == "excluded" for item in reviewed),
        "modified_ids": sorted(MODIFIED_IDS),
        "errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False))
    for error in errors:
        print(f"[FAIL] {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

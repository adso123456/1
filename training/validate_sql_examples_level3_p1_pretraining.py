"""Level 3 P1 approved 子集训练前静态验证。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard

TRAINING_DIR = Path(__file__).resolve().parent
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p1_draft.json"
REVIEW_PATH = TRAINING_DIR / "sql_examples_level3_p1_review_result.json"
P0_DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p0_draft.json"
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
REPORT_PATH = TRAINING_DIR / "sql_examples_level3_p1_pretraining_validation_result.md"
BASE_COMMIT = "ad10a44c802154ede2e1296826ca63732a6b523e"

CONSISTENCY_FIELDS = (
    "id", "group", "priority", "question", "sql",
    "expected_tables", "expected_columns",
)
FORBIDDEN_TABLES = {
    "wm_waterquality_threshold",
    "wm_water_source_intake_v2",
    "rs_outlet_trace_v2",
}
SYSTEM_TABLES = {
    "information_schema", "pg_catalog", "sqlite_master", "sqlite_schema",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_approved(
    sample: dict[str, Any],
    guard: SQLGuard,
    p0_questions: set[str],
) -> list[str]:
    errors: list[str] = []
    sid = sample["id"]
    sql = sample["sql"]
    sql_lower = sql.lower()
    expected_tables = sample["expected_tables"]
    guard_result = guard.validate(
        sql=sql,
        query=sample["question"],
        deterministic_candidate_tables=expected_tables,
    )
    if not guard_result.passed:
        errors.append(f"{sid} SQLGuard passed=false: {guard_result.reason}")
    if guard_result.severity != "ok":
        errors.append(f"{sid} severity={guard_result.severity}")
    if guard_result.unknown_tables:
        errors.append(f"{sid} unknown_tables={guard_result.unknown_tables}")
    if guard_result.unknown_columns:
        errors.append(f"{sid} unknown_columns={guard_result.unknown_columns}")
    if guard_result.candidate_mismatch:
        errors.append(f"{sid} candidate_mismatch={guard_result.candidate_mismatch}")
    if len(expected_tables) != 1 or set(guard_result.used_tables) != set(expected_tables):
        errors.append(f"{sid} 不是单表或表不一致")
    if re.search(r"\bJOIN\b", sql, flags=re.IGNORECASE):
        errors.append(f"{sid} 包含JOIN")
    if re.search(r"\bSELECT\s+\*", sql, flags=re.IGNORECASE):
        errors.append(f"{sid} 包含SELECT *")
    limit_values = re.findall(r"\bLIMIT\s+(\d+)\b", sql, flags=re.IGNORECASE)
    if not limit_values or any(int(value) > 100 for value in limit_values):
        errors.append(f"{sid} LIMIT缺失或大于100")
    for marker in FORBIDDEN_TABLES | SYSTEM_TABLES:
        if marker in sql_lower:
            errors.append(f"{sid} 包含冻结表或系统表: {marker}")
    if sample["question"] in p0_questions:
        errors.append(f"{sid} question与P0完全重复")
    if not sample.get("review_notes"):
        errors.append(f"{sid} review_notes为空")
    if sample.get("business_risk") == "high":
        errors.append(f"{sid} 高风险样本不得approved")
    return errors


def write_report(
    draft: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    consistency_errors: list[str],
    approved_errors: list[str],
    non_approved_errors: list[str],
) -> None:
    approved = [item for item in reviewed if item["decision"] == "approved"]
    manual = [
        item for item in reviewed
        if item["decision"] == "requires_manual_review"
    ]
    excluded = [item for item in reviewed if item["decision"] == "excluded"]
    approved_passed = len(approved) if not approved_errors else sum(
        not any(error.startswith(item["id"] + " ") for error in approved_errors)
        for item in approved
    )
    consistency_ok = not consistency_errors
    non_approved_frozen = not non_approved_errors
    passed = (
        consistency_ok
        and len(approved) > 0
        and approved_passed == len(approved)
        and not approved_errors
        and non_approved_frozen
        and not any(item.get("business_risk") == "high" for item in approved)
    )
    lines = [
        "# Level 3 P1 SQL 示例训练前验证结果",
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
        "- 是否修改正式 `vanna_data`：否",
        f"- draft 数量：{len(draft)}",
        f"- review_result 数量：{len(reviewed)}",
        f"- approved 数量：{len(approved)}",
        f"- requires_manual_review 数量：{len(manual)}",
        f"- excluded 数量：{len(excluded)}",
        f"- draft/review 一致性：{'通过' if consistency_ok else '失败'}",
        f"- approved 子集 SQLGuard 通过数量：{approved_passed}/{len(approved)}",
        f"- approved 子集静态通过率：{(approved_passed / len(approved) * 100):.0f}%" if approved else "- approved 子集静态通过率：0%",
        f"- 非 approved 是否保持冻结：{'是' if non_approved_frozen else '否'}",
        f"- 是否有高风险样本被 approved：{'是' if any(item.get('business_risk') == 'high' for item in approved) else '否'}",
        "",
        "## 全量一致性检查",
        "",
        f"- draft/review 数量均为24：{'是' if len(draft) == len(reviewed) == 24 else '否'}",
        f"- ID集合和顺序一致：{'是' if [item['id'] for item in draft] == [item['id'] for item in reviewed] else '否'}",
        f"- question/sql/expected_tables/expected_columns/group/priority 一致：{'是' if consistency_ok else '否'}",
        f"- 无重复ID：{'是' if len({item['id'] for item in reviewed}) == len(reviewed) else '否'}",
        "",
        "## approved 子集",
        "",
        f"- approved IDs：{', '.join(item['id'] for item in approved)}",
        f"- SQLGuard passed=true 且 severity=ok：{approved_passed}/{len(approved)}",
        "- unknown_tables/unknown_columns/candidate_mismatch 均为空：是" if not approved_errors else "- unknown_tables/unknown_columns/candidate_mismatch 均为空：否",
        "- 单表、无JOIN、无冻结表、无P0重复、无SELECT *、LIMIT<=100：是" if not approved_errors else "- 单表安全边界：否",
        "- review_notes均非空：是" if not approved_errors else "- review_notes均非空：否",
        "",
        "## 非 approved 子集",
        "",
        f"- requires_manual_review IDs：{', '.join(item['id'] for item in manual) or '无'}",
        f"- excluded IDs：{', '.join(item['id'] for item in excluded) or '无'}",
        f"- 是否标记为可训练：{'否' if non_approved_frozen else '是'}",
        "- 人工复核原因是否明确：是" if non_approved_frozen else "- 人工复核原因是否明确：否",
        "",
        "## 错误明细",
        "",
    ]
    all_errors = consistency_errors + approved_errors + non_approved_errors
    lines.extend(f"- {error}" for error in all_errors)
    if not all_errors:
        lines.append("- 无")
    lines.extend([
        "",
        "## 最终结论",
        "",
        "**通过。**" if passed else "**未通过。**",
        "",
        (
            "训练前验证通过；后续受控写入仅写 approved 子集；requires_manual_review 保持冻结。"
            if passed else
            "训练前验证未通过，禁止进入受控写入。"
        ),
        "",
        "## 下一阶段建议",
        "",
        "在独立阶段设计受控写入脚本，只允许写入21条approved样本；写入前再次核对正式vanna_data指纹并备份。",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    draft = load_json(DRAFT_PATH)
    reviewed = load_json(REVIEW_PATH)
    p0_draft = load_json(P0_DRAFT_PATH)
    if not all(isinstance(value, list) for value in (draft, reviewed, p0_draft)):
        print("[FAIL] 输入文件顶层必须是JSON数组")
        return 1

    consistency_errors: list[str] = []
    draft_ids = [item.get("id") for item in draft]
    review_ids = [item.get("id") for item in reviewed]
    if len(draft) != 24 or len(reviewed) != 24:
        consistency_errors.append("draft和review_result数量必须均为24")
    if draft_ids != review_ids:
        consistency_errors.append("draft/review ID集合或顺序不一致")
    if len(review_ids) != len(set(review_ids)):
        consistency_errors.append("review_result存在重复ID")
    for source, result in zip(draft, reviewed):
        sid = str(source.get("id", "UNKNOWN"))
        for field in CONSISTENCY_FIELDS:
            if source.get(field) != result.get(field):
                consistency_errors.append(f"{sid} 字段不一致: {field}")

    guard = SQLGuard(METADATA_PATH)
    p0_questions = {item["question"] for item in p0_draft}
    approved_errors: list[str] = []
    non_approved_errors: list[str] = []
    for item in reviewed:
        decision = item.get("decision")
        if decision == "approved":
            approved_errors.extend(validate_approved(item, guard, p0_questions))
        elif decision in {"requires_manual_review", "excluded"}:
            if not item.get("review_notes"):
                non_approved_errors.append(f"{item['id']} 非approved原因为空")
            if item.get("review_status") != "reviewed":
                non_approved_errors.append(f"{item['id']} review_status未标记reviewed")
        else:
            non_approved_errors.append(f"{item.get('id')} decision非法或为空")

    write_report(
        draft,
        reviewed,
        consistency_errors,
        approved_errors,
        non_approved_errors,
    )
    approved_count = sum(item["decision"] == "approved" for item in reviewed)
    passed = (
        not consistency_errors
        and not approved_errors
        and not non_approved_errors
        and approved_count > 0
        and not any(
            item.get("business_risk") == "high"
            for item in reviewed if item["decision"] == "approved"
        )
    )
    print(json.dumps({
        "draft": len(draft),
        "review_result": len(reviewed),
        "approved": approved_count,
        "requires_manual_review": sum(
            item["decision"] == "requires_manual_review" for item in reviewed
        ),
        "excluded": sum(item["decision"] == "excluded" for item in reviewed),
        "consistency_errors": len(consistency_errors),
        "approved_errors": len(approved_errors),
        "non_approved_errors": len(non_approved_errors),
        "passed": passed,
    }, ensure_ascii=False))
    for error in consistency_errors + approved_errors + non_approved_errors:
        print(f"[FAIL] {error}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

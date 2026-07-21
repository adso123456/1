"""Level 3 P2 approved 子集训练前验证，不执行训练。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard

DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p2_draft.json")
REVIEW_PATH = Path(__file__).with_name("sql_examples_level3_p2_review_result.json")
AUDIT_PATH = Path(__file__).with_name("level3_p2_join_feasibility_result.md")
STATIC_REPORT_PATH = Path(__file__).with_name("sql_examples_level3_p2_static_check_result.md")
RESULT_PATH = Path(__file__).with_name("sql_examples_level3_p2_pretraining_validation_result.md")
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
COMPARE_FIELDS = ("question", "sql", "expected_tables", "expected_columns", "join_keys")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_audit() -> dict[str, Any]:
    text = AUDIT_PATH.read_text(encoding="utf-8")
    match = re.search(r"## 机器可读审计数据\s*```json\s*(.*?)\s*```", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("审计报告缺少机器可读 JSON")
    return json.loads(match.group(1))


def md(value: Any) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    draft = load_json(DRAFT_PATH)
    review = load_json(REVIEW_PATH)
    audit = load_audit()
    static_text = STATIC_REPORT_PATH.read_text(encoding="utf-8")
    guard = SQLGuard(index_path=METADATA_PATH)

    errors: list[str] = []
    draft_by_id = {item["id"]: item for item in draft}
    review_by_id = {item["id"]: item for item in review}
    audit_by_id = {item["id"]: item for item in audit["candidate_execution"]}
    draft_ids = set(draft_by_id)
    review_ids = set(review_by_id)

    if len(draft) != 11:
        errors.append(f"draft数量应为11，实际{len(draft)}")
    if len(review) != 11:
        errors.append(f"review_result数量应为11，实际{len(review)}")
    if draft_ids != review_ids:
        errors.append("draft与review_result ID集合不一致")

    mismatch_ids: list[str] = []
    source_mismatch_ids: list[str] = []
    for sid in sorted(draft_ids & review_ids):
        if any(draft_by_id[sid].get(field) != review_by_id[sid].get(field) for field in COMPARE_FIELDS):
            mismatch_ids.append(sid)
        if review_by_id[sid].get("source_draft_id") != sid:
            source_mismatch_ids.append(sid)
    if mismatch_ids:
        errors.append(f"draft/review字段不一致: {mismatch_ids}")
    if source_mismatch_ids:
        errors.append(f"source_draft_id不一致: {source_mismatch_ids}")

    decisions = Counter(item.get("decision") for item in review)
    approved = [item for item in review if item.get("decision") == "approved"]
    manual = [item for item in review if item.get("decision") == "requires_manual_review"]
    excluded = [item for item in review if item.get("decision") == "excluded"]
    unknown_decisions = sorted(
        sid for sid, item in review_by_id.items()
        if item.get("decision") not in {"approved", "requires_manual_review", "excluded"}
    )
    if unknown_decisions:
        errors.append(f"存在非法decision: {unknown_decisions}")
    if not approved:
        errors.append("approved数量必须大于0")

    guard_failures: list[str] = []
    audit_failures: list[str] = []
    high_risk_approved: list[str] = []
    sql_audit_mismatch: list[str] = []
    for item in approved:
        sid = item["id"]
        result = guard.validate(
            sql=item["sql"],
            query=item["question"],
            deterministic_candidate_tables=item["expected_tables"],
        )
        if not result.passed or result.severity != "ok":
            guard_failures.append(sid)
        audit_item = audit_by_id.get(sid, {})
        if not audit_item.get("success") or not audit_item.get("transaction_read_only"):
            audit_failures.append(sid)
        expected_hash = hashlib.sha256(item["sql"].encode("utf-8")).hexdigest()
        if audit_item.get("sql") != item["sql"] or audit_item.get("sql_sha256") != expected_hash:
            sql_audit_mismatch.append(sid)
        if item.get("business_risk") == "high":
            high_risk_approved.append(sid)
    if guard_failures:
        errors.append(f"approved SQLGuard失败: {guard_failures}")
    if audit_failures:
        errors.append(f"approved真实SQL审计失败: {audit_failures}")
    if sql_audit_mismatch:
        errors.append(f"approved SQL与真实审计版本不一致: {sql_audit_mismatch}")
    if high_risk_approved:
        errors.append(f"approved包含高风险样本: {high_risk_approved}")

    write_set_ids = {item["id"] for item in approved}
    nonapproved_ids = {item["id"] for item in manual + excluded}
    frozen_leak = sorted(write_set_ids & nonapproved_ids)
    if frozen_leak:
        errors.append(f"非approved进入写入集合: {frozen_leak}")
    static_passed = (
        "SQLGuard pass/warning/fail：11/0/0" in static_text
        and "P2 草案静态阶段是否通过：是" in static_text
    )
    if not static_passed:
        errors.append("最终静态检查未达到11/0/0")
    if not audit.get("transaction_read_only") or audit.get("non_select_business_statements") != 0:
        errors.append("真实数据库审计不满足只读SELECT边界")

    passed = not errors
    lines = [
        "# Level 3 P2 训练前验证结果",
        "",
        "## 汇总",
        "",
        f"- draft数量：{len(draft)}",
        f"- review_result数量：{len(review)}",
        f"- ID集合一致：{'是' if draft_ids == review_ids else '否'}",
        f"- draft/review字段内容一致：{'是' if not mismatch_ids else '否'}",
        f"- approved：{len(approved)}",
        f"- requires_manual_review：{len(manual)}",
        f"- excluded：{len(excluded)}",
        f"- approved SQLGuard通过率：{len(approved) - len(guard_failures)}/{len(approved)}",
        f"- approved真实SQL审计通过率：{len(approved) - len(audit_failures)}/{len(approved)}",
        f"- approved SQL版本与审计一致：{len(approved) - len(sql_audit_mismatch)}/{len(approved)}",
        f"- approved高风险样本：{md(high_risk_approved or [])}",
        f"- 非approved进入写入集合：{md(frozen_leak or [])}",
        f"- 最终静态检查：{'11/0/0' if static_passed else '未通过'}",
        f"- 训练前验证：{'通过' if passed else '失败'}",
        "- 是否训练：否",
        "- 是否调用vn.train()：否",
        "- 是否调用memory.save_tool_usage()：否",
        "- 是否写入ChromaDB：否",
        "",
        "## approved写入候选集合",
        "",
        f"- sample_id：{md(sorted(write_set_ids))}",
        f"- 数量：{len(write_set_ids)}",
        "",
        "## 冻结集合",
        "",
        f"- requires_manual_review：{md(sorted(item['id'] for item in manual))}",
        f"- excluded：{md(sorted(item['id'] for item in excluded))}",
        "- 后续写入脚本只允许使用approved写入候选集合，冻结集合不得进入写入。",
        "",
        "## 错误",
        "",
        f"- {md(errors or ['无'])}",
        "",
        "## 结论",
        "",
        (
            "训练前验证通过；后续受控写入只允许approved子集，其他样本继续冻结。"
            if passed
            else "训练前验证失败，不得进入受控写入。"
        ),
    ]
    RESULT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "draft": len(draft),
                "review": len(review),
                "approved": decisions["approved"],
                "requires_manual_review": decisions["requires_manual_review"],
                "excluded": decisions["excluded"],
                "approved_guard_pass": len(approved) - len(guard_failures),
                "approved_audit_pass": len(approved) - len(audit_failures),
                "passed": passed,
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

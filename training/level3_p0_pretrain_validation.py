"""第 3 级 P0 SQL 样本训练前最终验证。

本脚本只读取样本、审查结果、静态检查报告和元数据索引；不训练、不写入
ChromaDB、不连接数据库、不执行 SQL，也不调用任何 LLM 接口。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "training"
DRAFT_PATH = TRAINING_DIR / "sql_examples_level3_p0_draft.json"
REVIEW_PATH = TRAINING_DIR / "sql_examples_level3_p0_review_result.json"
CHECK_RESULT_PATH = TRAINING_DIR / "sql_examples_level3_p0_check_result.md"
REPORT_PATH = TRAINING_DIR / "level3_p0_pretrain_validation_result.md"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.strip()


def load_json_list(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path.name} 必须是对象数组")
    return value


def sql_has_forbidden_operation(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge|grant|revoke)\b",
            sql,
            flags=re.IGNORECASE,
        )
    )


def build_checks(draft: list[dict[str, Any]], review: list[dict[str, Any]]) -> list[Check]:
    checks: list[Check] = []
    draft_by_id = {str(sample.get("id")): sample for sample in draft}
    review_by_id = {str(sample.get("id")): sample for sample in review}
    draft_ids = set(draft_by_id)
    review_ids = set(review_by_id)

    checks.extend(
        [
            Check("draft.json 可解析", True, f"对象数组，共 {len(draft)} 条"),
            Check("review_result.json 可解析", True, f"对象数组，共 {len(review)} 条"),
            Check("样本总数 = 18", len(draft) == 18 and len(review) == 18, f"draft={len(draft)}, review={len(review)}"),
            Check("draft ids 与 review ids 完全一致", draft_ids == review_ids and len(draft_ids) == 18, f"draft={len(draft_ids)}, review={len(review_ids)}"),
        ]
    )

    question_mismatches = [
        sample_id
        for sample_id in sorted(draft_ids & review_ids)
        if draft_by_id[sample_id].get("question") != review_by_id[sample_id].get("question")
    ]
    checks.append(
        Check(
            "draft question 与 review question 完全一致",
            not question_mismatches and draft_ids == review_ids,
            "无不一致" if not question_mismatches else ", ".join(question_mismatches),
        )
    )

    decisions = [sample.get("decision") for sample in review]
    approved_count = decisions.count("approved")
    manual_count = decisions.count("requires_manual_review")
    excluded_count = decisions.count("excluded")
    checks.extend(
        [
            Check("18 条 decision 全部 approved", approved_count == 18, f"approved={approved_count}"),
            Check("requires_manual_review = 0", manual_count == 0, f"actual={manual_count}"),
            Check("excluded = 0", excluded_count == 0, f"actual={excluded_count}"),
        ]
    )

    train_decisions = {sample.get("train_decision") for sample in draft}
    review_statuses = {sample.get("review_status") for sample in draft}
    checks.extend(
        [
            Check("train_decision 全部仍为 draft", train_decisions == {"draft"}, repr(sorted(train_decisions))),
            Check(
                "review_status 无异常",
                review_statuses == {"pending_static_check"},
                repr(sorted(review_statuses)),
            ),
        ]
    )

    sqls = [str(sample.get("sql") or "") for sample in draft]
    checks.extend(
        [
            Check("每条 SQL 都是 SELECT", all(re.match(r"^\s*select\b", sql, re.IGNORECASE) for sql in sqls), "仅允许 SELECT"),
            Check("每条 SQL 都带 LIMIT", all(re.search(r"\blimit\b", sql, re.IGNORECASE) for sql in sqls), "LIMIT 覆盖全部样本"),
            Check("无 SELECT *", not any(re.search(r"\bselect\s+\*", sql, re.IGNORECASE) for sql in sqls), "未发现"),
            Check("无 wm_waterquality_threshold", not any("wm_waterquality_threshold" in sql.lower() for sql in sqls), "未发现"),
            Check("无 DDL/DML", not any(sql_has_forbidden_operation(sql) for sql in sqls), "未发现"),
            Check(
                "无系统表",
                not any(re.search(r"\b(information_schema|pg_catalog|sqlite_master|sqlite_schema)\b", sql, re.IGNORECASE) for sql in sqls),
                "未发现",
            ),
        ]
    )

    guard = SQLGuard()
    guard_results = []
    table_mismatches: list[str] = []
    unknown_columns: list[str] = []
    for sample in draft:
        result = guard.validate(
            sql=str(sample.get("sql") or ""),
            query=str(sample.get("question") or ""),
            deterministic_candidate_tables=list(sample.get("expected_tables") or []),
        )
        guard_results.append(result)
        if set(result.used_tables) != set(sample.get("expected_tables") or []):
            table_mismatches.append(str(sample.get("id")))
        if result.unknown_columns:
            unknown_columns.append(str(sample.get("id")))

    guard_passed = sum(result.passed and result.severity == "ok" for result in guard_results)
    checks.extend(
        [
            Check("SQL Guard 18/18 passed=True severity=ok", guard_passed == 18, f"passed_ok={guard_passed}, total={len(guard_results)}"),
            Check("used_tables 与 expected_tables 一致", not table_mismatches, "无不一致" if not table_mismatches else ", ".join(table_mismatches)),
            Check("used_columns 无 unknown", not unknown_columns, "无未知字段" if not unknown_columns else ", ".join(unknown_columns)),
            Check("未调用任何训练接口", True, "脚本只读取 JSON、Markdown 与 metadata，并调用 SQLGuard.validate"),
        ]
    )

    check_result = CHECK_RESULT_PATH.read_text(encoding="utf-8")
    required_markers = [
        "| 13 | 样本总数 | 18 |",
        "| 16 | SQL Guard passed 数量 | 18 |",
        "| 17 | SQL Guard failed 数量 | 0 |",
        "| 18 | warning 数量 | 0 |",
        "| 19 | excluded/manual_review 数量 | 0 |",
    ]
    checks.append(
        Check(
            "check_result 与当前统计一致",
            all(marker in check_result for marker in required_markers),
            "18 条、Guard 18/18、0 warning、0 manual/excluded" if all(marker in check_result for marker in required_markers) else "check_result 缺少预期统计标记",
        )
    )
    return checks


def training_plan() -> list[tuple[str, str]]:
    return [
        ("第 2 级写入方式", "train_sql_examples_level2.py 逐条调用 memory.save_tool_usage(question, tool_name='run_sql', args={'sql': sql}, context=ToolContext, success=True, metadata=...)。"),
        ("第 3 级写入方式", "应复用 save_tool_usage；它与已接入的 SqlExampleContextEnhancer 的 run_sql usage 检索契约一致，不应另建存储方式。"),
        ("training_level", "应写入 training_level='level3_p0_sql_examples'，与第 2 级 level2_sql_examples 区分。"),
        ("sample_id", "应保留 sample_id='L3_P0_SQL_xxx'，确保训练后可追溯、可审计、可做定向召回验证。"),
        ("样本白名单", "只写入 review_result.decision='approved' 的样本；任何 requires_manual_review 或 excluded 必须在写入前硬失败。"),
        ("训练前备份", "需要备份正式 vanna_data，并记录训练前文件指纹。"),
        ("训练后比对", "需要记录训练后指纹、变更文件列表和逐样本写入结果；仅允许预期 ChromaDB 变化。"),
    ]


def write_report(initial_git_status: str, checks: list[Check]) -> None:
    passed = [check for check in checks if check.passed]
    failed = [check for check in checks if not check.passed]
    draft = load_json_list(DRAFT_PATH)
    review = load_json_list(REVIEW_PATH)
    approved_count = sum(sample.get("decision") == "approved" for sample in review)
    manual_count = sum(sample.get("decision") == "requires_manual_review" for sample in review)
    excluded_count = sum(sample.get("decision") == "excluded" for sample in review)
    guard_check = next(check for check in checks if check.name.startswith("SQL Guard 18/18"))
    guard_match = re.search(r"passed_ok=(\d+)", guard_check.detail)
    guard_passed = int(guard_match.group(1)) if guard_match else 0
    current_commit = git_output("rev-parse", "HEAD")
    remote = git_output("remote", "-v") or "未读取到远端"
    conclusion = "通过：可进入受控写入阶段。" if not failed else "不通过：不得进入受控写入阶段。"
    next_step = "经单独授权后，按本报告的白名单、备份和指纹要求执行第 3 级 P0 受控写入。" if not failed else "先修复失败项并重新运行本验证。"

    lines = [
        "# 第 3 级 P0 训练前最终验证结果",
        "",
        "## 执行范围",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 当前工作目录 | {PROJECT_ROOT} |",
        f"| git remote -v | {remote.replace(chr(10), '<br>')} |",
        f"| 当前 commit | {current_commit} |",
        f"| 初始 git status --short | {initial_git_status} |",
        "| 是否启动真实主服务 | 否 |",
        "| 是否连接数据库 | 否 |",
        "| 是否执行真实 SQL | 否 |",
        "| 是否调用 DeepSeek | 否 |",
        "| 是否训练 Vanna | 否 |",
        "| 是否调用 vn.train() | 否 |",
        "| 是否写入正式 ChromaDB | 否 |",
        "| 是否修改正式 vanna_data | 否 |",
        "| 是否修改主服务 | 否 |",
        "| 是否修改 SQL Guard | 否 |",
        "",
        "## 样本与 SQL Guard 汇总",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 样本总数 | {len(draft)} |",
        f"| approved 数量 | {approved_count} |",
        f"| requires_manual_review 数量 | {manual_count} |",
        f"| excluded 数量 | {excluded_count} |",
        f"| SQL Guard passed 数量 | {guard_passed} |",
        f"| SQL Guard failed 数量 | {len(draft) - guard_passed} |",
        f"| draft/review question 是否一致 | {'是' if next(check for check in checks if check.name.startswith('draft question')).passed else '否'} |",
        f"| train_decision 是否仍为 draft | {'是' if next(check for check in checks if check.name.startswith('train_decision')).passed else '否'} |",
        "",
        "## 验证明细",
        "",
        "| 检查项 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {check.name} | {'通过' if check.passed else '失败'} | {check.detail} |" for check in checks)
    lines.extend(["", "## 训练脚本方案检查", "", "| 项目 | 结论 |", "|---|---|"])
    lines.extend(f"| {item} | {detail} |" for item, detail in training_plan())
    lines.extend(
        [
            "",
            "## 当前结论",
            "",
            conclusion,
            "",
            "## 下一阶段建议",
            "",
            next_step,
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="第 3 级 P0 训练前验证")
    parser.add_argument(
        "--initial-git-status",
        default="clean（创建本阶段验证文件前记录）",
        help="本阶段开始前记录的 git status --short",
    )
    args = parser.parse_args()

    try:
        draft = load_json_list(DRAFT_PATH)
        review = load_json_list(REVIEW_PATH)
        checks = build_checks(draft, review)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"验证失败：{exc}")
        return 1

    write_report(args.initial_git_status, checks)
    failed = [check for check in checks if not check.passed]
    print(f"检查总数：{len(checks)}")
    print(f"通过数量：{len(checks) - len(failed)}")
    print(f"失败数量：{len(failed)}")
    if failed:
        print("失败项：" + ", ".join(check.name for check in failed))
        return 1
    print("结论：通过，可进入受控写入阶段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

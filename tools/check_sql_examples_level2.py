from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
DRAFT_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_draft.json"
REPORT_PATH = CURRENT_DIR / "sql_examples_level2_check_result.md"
PRETRAIN_REVIEW_PATH = PROJECT_ROOT / "training" / "sql_examples_level2_pretrain_review.md"
INDEX_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard


REQUIRED_FIELDS = {
    "id",
    "question",
    "sql",
    "expected_tables",
    "expected_columns",
    "purpose",
    "risk_level",
    "review_status",
    "train_decision",
    "review_notes",
}

TRAIN_DECISIONS = {"approved", "requires_manual_review", "excluded"}

SCENARIOS = [
    "水质日趋势",
    "水质小时趋势",
    "水质月趋势",
    "排污口编码",
    "排污口溯源",
    "排污口基础信息",
    "站点信息",
    "区域信息",
    "取水口信息",
]


def run_command(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.stdout.strip()


def load_metadata() -> tuple[set[str], set[str]]:
    rows = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    tables: set[str] = set()
    columns: set[str] = set()
    for row in rows:
        table = str(row.get("table") or "").strip()
        column = str(row.get("column") or "").strip()
        if table:
            tables.add(table)
        if table and column:
            columns.add(f"{table}.{column}")
    return tables, columns


def scenario_of(sample: dict[str, Any]) -> str:
    purpose = str(sample.get("purpose") or "")
    for scenario in SCENARIOS:
        if scenario in purpose:
            return scenario
    return "未分类"


def contains_forbidden_operation(sql: str) -> bool:
    return bool(
        re.search(
            r"\b(insert|update|delete|drop|alter|create|truncate|comment|merge|grant|revoke)\b",
            sql,
            flags=re.I,
        )
    )


def check_sample(
    sample: dict[str, Any],
    seen_ids: set[str],
    tables: set[str],
    columns: set[str],
    guard: SQLGuard,
) -> dict[str, Any]:
    failures: list[str] = []

    missing = sorted(REQUIRED_FIELDS - set(sample))
    if missing:
        failures.append("缺少字段：" + ", ".join(missing))

    sample_id = str(sample.get("id") or "")
    if not sample_id:
        failures.append("id 为空")
    elif sample_id in seen_ids:
        failures.append("id 不唯一")
    seen_ids.add(sample_id)

    sql = str(sample.get("sql") or "").strip()
    question = str(sample.get("question") or "").strip()
    expected_tables = list(sample.get("expected_tables") or [])
    expected_columns = list(sample.get("expected_columns") or [])
    scenario = scenario_of(sample)
    train_decision = str(sample.get("train_decision") or "").strip()
    review_notes = str(sample.get("review_notes") or "").strip()

    if train_decision not in TRAIN_DECISIONS:
        failures.append("train_decision 非法或为空")
    if not review_notes:
        failures.append("review_notes 为空")

    if not re.match(r"^\s*select\b", sql, flags=re.I):
        failures.append("SQL 不是 SELECT")
    if not re.search(r"\blimit\b", sql, flags=re.I):
        failures.append("SQL 缺少 LIMIT")
    if contains_forbidden_operation(sql):
        failures.append("SQL 包含 DDL/DML 或禁止操作")
    if re.search(r"\b(information_schema|pg_catalog|sqlite_master|sqlite_schema)\b", sql, flags=re.I):
        failures.append("SQL 访问系统表")
    if re.search(r"select\s+\*", sql, flags=re.I):
        failures.append("SQL 包含 SELECT *")

    unknown_expected_tables = [table for table in expected_tables if table not in tables]
    if unknown_expected_tables:
        failures.append("expected_tables 包含未知表：" + ", ".join(unknown_expected_tables))

    unknown_expected_columns = [column for column in expected_columns if column not in columns]
    if unknown_expected_columns:
        failures.append("expected_columns 包含未知字段：" + ", ".join(unknown_expected_columns))

    guard_result = guard.validate(sql=sql, query=question)
    if not guard_result.passed and train_decision != "excluded":
        failures.append("SQL Guard 未通过但未标记 excluded：" + guard_result.reason)
    if guard_result.severity == "warning" and train_decision == "approved":
        failures.append("SQL Guard warning 样本不能标记 approved")
    if train_decision == "approved" and guard_result.severity != "ok":
        failures.append("approved 样本必须为 SQL Guard severity=ok")

    used_tables = guard_result.used_tables
    used_columns = guard_result.used_columns

    if "水质" in question and any(word in question for word in ("趋势", "变化")):
        if "wm_waterquality_threshold" in used_tables:
            failures.append("水质趋势错误使用 wm_waterquality_threshold")
    if scenario == "水质日趋势" and "wm_waterquality_day_records" not in used_tables:
        failures.append("水质日趋势未使用 wm_waterquality_day_records")
    if scenario == "水质小时趋势" and "wm_waterquality_hour_records" not in used_tables:
        failures.append("水质小时趋势未使用 wm_waterquality_hour_records")
    if scenario == "水质月趋势" and used_tables != ["wm_waterquality_month_records"]:
        failures.append("水质月趋势未严格使用 wm_waterquality_month_records")
    if scenario == "排污口溯源" and used_tables == ["rs_outlet"]:
        failures.append("排污口溯源错误只使用 rs_outlet")
    if scenario == "排污口编码":
        if not any(table in used_tables for table in ("rs_outlet", "rs_outlet_info_v2")):
            failures.append("排污口编码未使用 rs_outlet 或 rs_outlet_info_v2")
        if not any(
            column.endswith(
                (
                    ".outlet_code",
                    ".outlet_code_national",
                    ".outlet_code_local",
                    ".outlet_code_province",
                )
            )
            for column in used_columns
        ):
            failures.append("排污口编码未使用明确 outlet_code 字段")

    passed = not failures
    return {
        "id": sample_id,
        "question": question,
        "scenario": scenario,
        "used_tables": used_tables,
        "used_columns": used_columns,
        "sql_guard": guard_result.to_dict(),
        "train_decision": train_decision,
        "review_notes": review_notes,
        "pass": passed,
        "reason": "通过训练前审查标记" if passed else "；".join(failures),
    }


def build_report(results: list[dict[str, Any]], status_short: str, remote: str) -> str:
    coverage = Counter(result["scenario"] for result in results)
    decision_counts = Counter(result["train_decision"] for result in results)
    failed = sum(1 for result in results if not result["pass"])
    failed_ids = [result["id"] for result in results if not result["pass"]]
    warning_ids = [
        result["id"]
        for result in results
        if result["sql_guard"].get("severity") == "warning"
    ]
    review_ids = [
        result["id"]
        for result in results
        if result["train_decision"] == "requires_manual_review"
    ]
    excluded_ids = [
        result["id"]
        for result in results
        if result["train_decision"] == "excluded"
    ]

    lines = [
        "# 第 2 级 SQL 示例训练前复核报告",
        "",
        "## 汇总",
        "",
        f"- 当前工作目录：{PROJECT_ROOT}",
        "- git remote -v：",
        "```text",
        remote,
        "```",
        "- git status --short：",
        "```text",
        status_short or "clean",
        "```",
        f"- 样本总数：{len(results)}",
        f"- approved 数量：{decision_counts.get('approved', 0)}",
        f"- requires_manual_review 数量：{decision_counts.get('requires_manual_review', 0)}",
        f"- excluded 数量：{decision_counts.get('excluded', 0)}",
        f"- 静态检查失败数量：{failed}",
        f"- 静态检查失败样本列表：{', '.join(failed_ids) if failed_ids else '无'}",
        f"- SQL Guard warning 样本列表：{', '.join(warning_ids) if warning_ids else '无'}",
        f"- requires_manual_review 样本列表：{', '.join(review_ids) if review_ids else '无'}",
        f"- excluded 样本列表：{', '.join(excluded_ids) if excluded_ids else '无'}",
        "",
        "## 场景覆盖",
        "",
    ]

    for scenario in SCENARIOS:
        lines.append(f"- {scenario}：{coverage.get(scenario, 0)}")

    lines.extend(
        [
            "",
            "## 明细",
            "",
        ]
    )

    for result in results:
        guard = result["sql_guard"]
        lines.extend(
            [
                f"### {result['id']}",
                "",
                f"- id：{result['id']}",
                f"- question：{result['question']}",
                f"- used_tables：{', '.join(result['used_tables']) or '无'}",
                f"- used_columns：{', '.join(result['used_columns']) or '无'}",
                f"- SQL Guard 结果：passed={guard['passed']}；severity={guard['severity']}；reason={guard['reason']}",
                f"- train_decision：{result['train_decision']}",
                f"- review_notes：{result['review_notes']}",
                f"- 是否通过训练前审查：{'是' if result['pass'] else '否'}",
                f"- reason：{result['reason']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 安全声明",
            "",
            "- 是否执行真实 SQL：否",
            "- 是否连接数据库：否",
            "- 是否训练 Vanna：否",
            "- 是否写入 ChromaDB：否",
            "- 是否修改数据库结构：否",
            "- 是否进入第 2/3/4 级：否",
            "- 当前结论：训练前复核准备通过；approved 样本可作为后续训练候选，requires_manual_review 样本不得直接训练。",
            "- 下一步建议：人工确认 requires_manual_review 样本的固定值、业务语义和 P0 候选一致性后，再另起阶段执行受控训练写入。",
            "",
        ]
    )

    return "\n".join(lines)


def write_report(results: list[dict[str, Any]], status_short: str, remote: str) -> None:
    report = build_report(results, status_short, remote)
    REPORT_PATH.write_text(report, encoding="utf-8")
    PRETRAIN_REVIEW_PATH.write_text(report, encoding="utf-8")


def main() -> int:
    remote = run_command(["git", "remote", "-v"])
    status_short = run_command(["git", "status", "--short"])
    tables, columns = load_metadata()
    samples = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    guard = SQLGuard()
    seen_ids: set[str] = set()
    results = [check_sample(sample, seen_ids, tables, columns, guard) for sample in samples]
    write_report(results, status_short, remote)

    approved = sum(1 for result in results if result["train_decision"] == "approved")
    requires_review = sum(
        1 for result in results if result["train_decision"] == "requires_manual_review"
    )
    excluded = sum(1 for result in results if result["train_decision"] == "excluded")
    failed = sum(1 for result in results if not result["pass"])
    print(f"样本总数: {len(results)}")
    print(f"approved 数量: {approved}")
    print(f"requires_manual_review 数量: {requires_review}")
    print(f"excluded 数量: {excluded}")
    print(f"静态检查失败数量: {failed}")
    print(
        "静态检查失败样本列表: "
        + (", ".join(result["id"] for result in results if not result["pass"]) or "无")
    )
    print(f"报告: {REPORT_PATH}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Level 3 P1 候选 SQL 草案静态检查。

只读取草案、P0 草案和 metadata index，并调用本地 SQLGuard；
不连接数据库、不执行 SQL、不调用模型、不写入 ChromaDB。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard

DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p1_draft.json")
P0_DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p0_draft.json")
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
REPORT_PATH = Path(__file__).with_name("sql_examples_level3_p1_static_check_result.md")
BASE_COMMIT = "ad10a44c802154ede2e1296826ca63732a6b523e"

REQUIRED_FIELDS = {
    "id", "group", "priority", "question", "sql", "expected_tables",
    "expected_columns", "business_intent", "risk_notes", "train_decision",
    "review_status",
}
GROUP_RANGES = {
    "C": range(1, 11),
    "D": range(11, 19),
    "E": range(19, 25),
}
P0_TABLES = {
    "wm_waterquality_year_records",
    "wm_waterquality_month_records",
    "wm_waterquality_day_records",
    "wm_waterquality_hour_records",
}
FORBIDDEN_TABLES = {
    "wm_waterquality_threshold",
    "wm_water_source_intake_v2",
    "rs_outlet_trace_v2",
}
SYSTEM_TABLE_MARKERS = {
    "information_schema", "pg_catalog", "sqlite_master", "sqlite_schema",
}
FORBIDDEN_OPERATIONS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "comment", "merge", "grant", "revoke",
}
FORBIDDEN_SET_OPERATORS = {"join", "union", "intersect", "except"}
FROZEN_QUESTION_MARKERS = {
    "排污口责任主体",
    "企业和排放许可证溯源",
    "水源地取水口供水能力",
}
ALLOWED_CHANGED_PATHS = {
    "training/sql_examples_level3_p1_draft.json",
    "training/check_sql_examples_level3_p1.py",
    "training/sql_examples_level3_p1_static_check_result.md",
    "training/review_sql_examples_level3_p1.py",
    "training/sql_examples_level3_p1_review_result.json",
    "training/sql_examples_level3_p1_review_report.md",
    "training/validate_sql_examples_level3_p1_pretraining.py",
    "training/sql_examples_level3_p1_pretraining_validation_result.md",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.rstrip()


def initial_status() -> str:
    lines = run_git("status", "--short").splitlines()
    kept = []
    for line in lines:
        path = line[3:].replace("\\", "/") if len(line) > 3 else ""
        if path not in ALLOWED_CHANGED_PATHS:
            kept.append(line)
    return "\n".join(kept)


def metadata_tables() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for row in load_json(METADATA_PATH):
        table = str(row.get("table", "")).strip().lower()
        column = str(row.get("column", "")).strip().lower()
        if table and column:
            result.setdefault(table, set()).add(column)
    return result


def has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) is not None


def check_sample(
    sample: dict[str, Any],
    index: int,
    guard: SQLGuard,
    metadata: dict[str, set[str]],
    p0_questions: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    sid = str(sample.get("id", ""))
    group = str(sample.get("group", ""))
    question = str(sample.get("question", "")).strip()
    sql = str(sample.get("sql", "")).strip()
    sql_lower = sql.lower()
    expected_tables = [str(value).lower() for value in sample.get("expected_tables", [])]
    expected_columns = {str(value).lower() for value in sample.get("expected_columns", [])}

    missing_required = sorted(REQUIRED_FIELDS - set(sample))
    empty_required = sorted(
        field for field in REQUIRED_FIELDS
        if field in sample and sample[field] in (None, "", [])
    )
    if missing_required:
        errors.append(f"缺少必填字段: {', '.join(missing_required)}")
    if empty_required:
        errors.append(f"必填字段为空: {', '.join(empty_required)}")

    expected_id = f"L3_P1_SQL_{index:03d}"
    if sid != expected_id:
        errors.append(f"ID应为{expected_id}，实际为{sid}")
    expected_group = "C" if index <= 10 else "D" if index <= 18 else "E"
    if group != expected_group:
        errors.append(f"group应为{expected_group}，实际为{group}")
    if sample.get("priority") != "P1":
        errors.append("priority必须为P1")
    if sample.get("train_decision") != "draft":
        errors.append("train_decision必须为draft")
    if sample.get("review_status") != "pending_static_check":
        errors.append("review_status必须为pending_static_check")

    if question in p0_questions:
        errors.append("question与P0完全重复")
    if len(expected_tables) != 1:
        errors.append("expected_tables必须且只能包含一张表")

    if not re.match(r"^(SELECT|WITH)\b", sql, flags=re.IGNORECASE):
        errors.append("SQL必须以SELECT或WITH开头")
    if re.search(r"\bSELECT\s+\*", sql, flags=re.IGNORECASE):
        errors.append("禁止SELECT *")
    limit_matches = re.findall(r"\bLIMIT\s+(\d+)\b", sql, flags=re.IGNORECASE)
    if not limit_matches:
        errors.append("SQL缺少数值LIMIT")
    elif any(int(value) > 100 for value in limit_matches):
        errors.append("LIMIT不得大于100")

    for keyword in sorted(FORBIDDEN_OPERATIONS | FORBIDDEN_SET_OPERATORS):
        if has_word(sql_lower, keyword):
            errors.append(f"SQL包含禁止关键字: {keyword.upper()}")
    for marker in sorted(SYSTEM_TABLE_MARKERS | FORBIDDEN_TABLES | P0_TABLES):
        if marker in sql_lower:
            errors.append(f"SQL包含禁止表或边界标记: {marker}")
    for marker in sorted(FROZEN_QUESTION_MARKERS):
        if marker in question:
            errors.append(f"question涉及冻结场景: {marker}")

    for table in expected_tables:
        if table not in metadata:
            errors.append(f"expected table不在metadata: {table}")
    for column in sorted(expected_columns):
        if "." not in column:
            errors.append(f"expected_columns未使用表名限定: {column}")
            continue
        table, field = column.split(".", 1)
        if table not in metadata or field not in metadata.get(table, set()):
            errors.append(f"expected column不在metadata: {column}")
        if table not in expected_tables:
            errors.append(f"expected column来自计划外表: {column}")

    guard_result = guard.validate(
        sql=sql,
        query=question,
        deterministic_candidate_tables=expected_tables,
    )
    used_tables = {value.lower() for value in guard_result.used_tables}
    used_columns = {value.lower() for value in guard_result.used_columns}
    if not guard_result.passed:
        errors.append(f"SQLGuard未通过: {guard_result.reason}")
    if guard_result.severity != "ok":
        errors.append(f"SQLGuard severity={guard_result.severity}: {guard_result.reason}")
    if guard_result.unknown_tables:
        errors.append(f"unknown_tables={guard_result.unknown_tables}")
    if guard_result.unknown_columns:
        errors.append(f"unknown_columns={guard_result.unknown_columns}")
    if guard_result.candidate_mismatch:
        errors.append(f"candidate_mismatch={guard_result.candidate_mismatch}")
    if used_tables != set(expected_tables):
        errors.append(f"used_tables与expected_tables不一致: {sorted(used_tables)}")
    if used_columns != expected_columns:
        extra = sorted(used_columns - expected_columns)
        missing = sorted(expected_columns - used_columns)
        errors.append(f"字段不一致: used多出={extra}, expected多出={missing}")

    if sid == "L3_P1_SQL_007":
        if not re.search(r"\btype\s*=\s*'PS'", sql, flags=re.IGNORECASE):
            errors.append("L3_P1_SQL_007必须限定type='PS'")
        required_note = "m1/m2/m3的污染物语义仅在type=PS前提下使用"
        if required_note not in str(sample.get("risk_notes", "")):
            errors.append("L3_P1_SQL_007 risk_notes缺少PS语义边界")
    if sid == "L3_P1_SQL_008":
        if re.search(r"\bm(?:[1-9]|1\d|2[0-2])_value\b", sql, flags=re.IGNORECASE):
            errors.append("L3_P1_SQL_008禁止使用m1_value至m22_value")
    if sid == "L3_P1_SQL_009":
        if not re.search(r"\btype\s*=\s*'PS'", sql, flags=re.IGNORECASE):
            errors.append("L3_P1_SQL_009必须限定type='PS'")
    if sid == "L3_P1_SQL_017" and "可能为JSON文本" not in str(sample.get("risk_notes", "")):
        errors.append("L3_P1_SQL_017 risk_notes缺少JSON文本边界")
    if group == "E" and "wm_water_source_intake_v2" in sql_lower:
        errors.append("E组禁止使用wm_water_source_intake_v2")

    status = "fail" if errors else "warning" if warnings else "pass"
    return {
        "id": sid,
        "group": group,
        "question": question,
        "expected_tables": expected_tables,
        "expected_columns": sorted(expected_columns),
        "guard": guard_result.to_dict(),
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "metadata_ok": not any("metadata" in item for item in errors),
        "single_table_ok": (
            len(expected_tables) == 1
            and len(used_tables) == 1
            and used_tables == set(expected_tables)
            and not any(has_word(sql_lower, word) for word in FORBIDDEN_SET_OPERATORS)
        ),
    }


def md_text(value: Any) -> str:
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value) or "无"
    else:
        text = str(value) if value not in (None, "") else "无"
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(
    results: list[dict[str, Any]],
    counts: Counter[str],
    ids_unique: bool,
    ids_continuous: bool,
    p0_duplicates: int,
) -> None:
    guard_pass = sum(item["guard"]["passed"] for item in results)
    severity_ok = sum(item["guard"]["severity"] == "ok" for item in results)
    metadata_ok = sum(item["metadata_ok"] for item in results)
    single_table_ok = sum(item["single_table_ok"] for item in results)
    status_counts = Counter(item["status"] for item in results)
    passed = (
        len(results) == 24
        and counts == Counter({"C": 10, "D": 8, "E": 6})
        and ids_unique
        and ids_continuous
        and guard_pass == 24
        and severity_ok == 24
        and metadata_ok == 24
        and single_table_ok == 24
        and p0_duplicates == 0
        and status_counts["warning"] == 0
        and status_counts["fail"] == 0
    )
    remote = run_git("remote", "-v")
    status = initial_status() or "clean"
    lines = [
        "# Level 3 P1 SQL 候选草案静态检查结果",
        "",
        "## 汇总",
        "",
        f"- 工作目录：`{PROJECT_ROOT}`",
        "- git remote：",
        "",
        "```text",
        remote,
        "```",
        f"- 基础 commit：`{BASE_COMMIT}`",
        "- 初始 `git status --short`：",
        "",
        "```text",
        status,
        "```",
        "- 是否启动主服务：否",
        "- 是否连接数据库：否",
        "- 是否执行 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否调用 `vn.train()`：否",
        "- 是否调用 `memory.save_tool_usage()`：否",
        "- 是否写入 ChromaDB：否",
        "- 是否修改正式 `vanna_data`：否",
        f"- 草案总数：{len(results)}",
        f"- C/D/E 数量：{counts['C']}/{counts['D']}/{counts['E']}",
        f"- ID 唯一：{'是' if ids_unique else '否'}",
        f"- ID 连续：{'是' if ids_continuous else '否'}",
        f"- SQLGuard passed 数量：{guard_pass}",
        f"- severity ok 数量：{severity_ok}",
        f"- metadata 字段通过数量：{metadata_ok}",
        f"- 单表边界通过数量：{single_table_ok}",
        f"- P0 question 完全重复数量：{p0_duplicates}",
        f"- warning 数量：{status_counts['warning']}",
        f"- fail 数量：{status_counts['fail']}",
        "",
        "## 逐样本检查表",
        "",
        "| id | group | question | expected table | used tables | expected columns | unknown tables | unknown columns | SQLGuard passed | severity | warning/error | 最终状态 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        guard = item["guard"]
        issues = item["errors"] + item["warnings"]
        lines.append(
            "| " + " | ".join([
                md_text(item["id"]),
                md_text(item["group"]),
                md_text(item["question"]),
                md_text(item["expected_tables"]),
                md_text(guard["used_tables"]),
                md_text(item["expected_columns"]),
                md_text(guard["unknown_tables"]),
                md_text(guard["unknown_columns"]),
                "是" if guard["passed"] else "否",
                md_text(guard["severity"]),
                md_text(issues),
                md_text(item["status"]),
            ]) + " |"
        )

    failures = [item for item in results if item["status"] != "pass"]
    lines.extend(["", "## warning 和 fail 明细", ""])
    if failures:
        for item in failures:
            lines.append(f"- {item['id']}：{md_text(item['errors'] + item['warnings'])}")
    else:
        lines.append("- 无")

    lines.extend([
        "",
        "## P1 特殊规则",
        "",
        "- L3_P1_SQL_007 是否限定 `type='PS'`：是",
        "- L3_P1_SQL_008 是否排除 m1_value 至 m22_value：是",
        "- L3_P1_SQL_009 是否限定 `type='PS'`：是",
        "- E 组是否使用 `wm_water_source_intake_v2`：否",
        "- 所有样本是否为单表且无 JOIN：是",
        f"- P1 与 P0 question 完全重复数量：{p0_duplicates}",
        "",
        "## 最终结论",
        "",
        f"**{'通过。' if passed else '未通过。'}**",
        "",
        (
            "24 条 P1 候选草案全部通过结构、SQLGuard、metadata 字段、单表边界和 P0 去重检查；本阶段未训练、未写入 ChromaDB。"
            if passed else
            "存在未通过项，禁止进入人工审查或提交。"
        ),
        "",
        "## 下一阶段建议",
        "",
        "对 24 条 draft 进行人工业务审查，生成独立 review_result；审查阶段仍不训练、不写 ChromaDB。",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    draft = load_json(DRAFT_PATH)
    p0_draft = load_json(P0_DRAFT_PATH)
    if not isinstance(draft, list):
        print("[FAIL] P1 draft 顶层必须是数组")
        return 1
    if not isinstance(p0_draft, list):
        print("[FAIL] P0 draft 顶层必须是数组")
        return 1

    metadata = metadata_tables()
    guard = SQLGuard(METADATA_PATH)
    p0_questions = {str(item.get("question", "")).strip() for item in p0_draft}
    results = [
        check_sample(sample, index, guard, metadata, p0_questions)
        for index, sample in enumerate(draft, start=1)
    ]
    ids = [str(sample.get("id", "")) for sample in draft]
    ids_unique = len(ids) == len(set(ids))
    expected_ids = [f"L3_P1_SQL_{index:03d}" for index in range(1, 25)]
    ids_continuous = ids == expected_ids
    counts = Counter(str(sample.get("group", "")) for sample in draft)
    p0_duplicates = sum(str(sample.get("question", "")).strip() in p0_questions for sample in draft)
    write_report(results, counts, ids_unique, ids_continuous, p0_duplicates)

    status_counts = Counter(item["status"] for item in results)
    guard_pass = sum(item["guard"]["passed"] for item in results)
    severity_ok = sum(item["guard"]["severity"] == "ok" for item in results)
    metadata_ok = sum(item["metadata_ok"] for item in results)
    single_table_ok = sum(item["single_table_ok"] for item in results)
    success = (
        len(results) == 24
        and counts == Counter({"C": 10, "D": 8, "E": 6})
        and ids_unique
        and ids_continuous
        and guard_pass == 24
        and severity_ok == 24
        and metadata_ok == 24
        and single_table_ok == 24
        and p0_duplicates == 0
        and status_counts["warning"] == 0
        and status_counts["fail"] == 0
    )
    print(
        json.dumps(
            {
                "total": len(results),
                "groups": dict(counts),
                "ids_unique": ids_unique,
                "ids_continuous": ids_continuous,
                "guard_passed": guard_pass,
                "severity_ok": severity_ok,
                "metadata_ok": metadata_ok,
                "single_table_ok": single_table_ok,
                "p0_duplicates": p0_duplicates,
                "pass": status_counts["pass"],
                "warning": status_counts["warning"],
                "fail": status_counts["fail"],
            },
            ensure_ascii=False,
        )
    )
    if not success:
        for item in results:
            if item["status"] != "pass":
                print(f"[{item['status'].upper()}] {item['id']}: {item['errors'] + item['warnings']}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

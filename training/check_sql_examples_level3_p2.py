"""Level 3 P2 JOIN 候选草案静态检查。

只读取范围计划、P2 草案、P0/P1 review_result 和 metadata index，
并调用本地 SQLGuard；不连接数据库、不执行 SQL、不调用模型、不写 ChromaDB。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.sql_guard import SQLGuard

SCOPE_PATH = Path(__file__).with_name("level3_p2_scope_plan.md")
DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p2_draft.json")
P0_REVIEW_PATH = Path(__file__).with_name("sql_examples_level3_p0_review_result.json")
P1_REVIEW_PATH = Path(__file__).with_name("sql_examples_level3_p1_review_result.json")
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
REPORT_PATH = Path(__file__).with_name("sql_examples_level3_p2_static_check_result.md")
BASE_COMMIT = "0f17d0e941063640a06380b7348f4390bf14752d"

REQUIRED_FIELDS = {
    "id",
    "group",
    "priority",
    "question",
    "sql",
    "expected_tables",
    "expected_columns",
    "join_keys",
    "business_intent",
    "risk_notes",
    "train_decision",
    "review_status",
}
SUPPORT_LEVELS = {
    "confirmed",
    "requires_manual_review_direction",
    "excluded_direction",
}
FORBIDDEN_TABLES = {
    "wm_waterquality_threshold",
    "wm_water_source_intake_v2",
    "rs_outlet_trace_v2",
}
SYSTEM_TABLES = {"information_schema", "pg_catalog", "sqlite_master", "sqlite_schema"}
FORBIDDEN_OPERATIONS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "comment",
    "merge",
    "grant",
    "revoke",
}
FROZEN_BUSINESS_MARKERS = {
    "责任主体",
    "企业和排放许可证",
    "排放许可证溯源",
    "水源地取水口供水能力",
    "水质阈值",
}
FROZEN_STATUS_PATTERNS = (
    r"\bhas_abnormal\s*=\s*'是'",
    r"\bhas_sampling_condition\s*=\s*'是'",
    r"\bis_remediated\s*=\s*'是'",
)
CREDENTIAL_COLUMNS = {
    "ip_address",
    "port",
    "username",
    "password",
    "account",
    "token",
}
CONFIRMED_EDGES = {
    frozenset(("rs_outlet_info_v2.id", "rs_outlet_remediation_v2.outlet_id")),
    frozenset(("rs_outlet_info_v2.id", "rs_outlet_live_v2.outlet_id")),
    frozenset(("wm_section_info.id", "wm_section_wq_info.section_id")),
    frozenset(("wm_section_info.water_body_id", "wm_waterbody_info.id")),
    frozenset(("wm_hydrological_info.region_code", "gis_region_county.region_code")),
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) is not None


def load_metadata() -> dict[str, dict[str, dict[str, str]]]:
    index: dict[str, dict[str, dict[str, str]]] = {}
    for row in load_json(METADATA_PATH):
        table = str(row.get("table", "")).strip().lower()
        column = str(row.get("column", "")).strip().lower()
        if table and column:
            index.setdefault(table, {})[column] = {
                "type": str(row.get("type", "")).strip().lower(),
                "comment": str(row.get("comment", "")).strip(),
            }
    return index


def parse_scope_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in SCOPE_PATH.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*[1-9]\s*\|", line):
            continue
        parts = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if len(parts) != 14 or parts[11] not in SUPPORT_LEVELS:
            continue
        rows.append(
            {
                "number": int(parts[0]),
                "scene": parts[1],
                "support": parts[11],
                "candidate_count": int(parts[12]),
            }
        )
    return rows


def type_family(data_type: str) -> str:
    normalized = data_type.lower()
    if any(marker in normalized for marker in ("character", "varchar", "text", "char")):
        return "text"
    if any(marker in normalized for marker in ("bigint", "integer", "smallint", "numeric", "double", "real", "decimal")):
        return "numeric"
    if "timestamp" in normalized or normalized == "date":
        return "temporal"
    return normalized


def table_aliases(sql: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)\s+(?:AS\s+)?([a-z_][a-z0-9_]*)",
        flags=re.IGNORECASE,
    )
    for table, alias in pattern.findall(sql):
        aliases[table.lower()] = alias.lower()
    return aliases


def sql_has_join_equality(
    sql: str,
    aliases: dict[str, str],
    left: str,
    right: str,
) -> bool:
    left_table, left_column = left.lower().split(".", 1)
    right_table, right_column = right.lower().split(".", 1)
    left_ref = rf"{re.escape(aliases.get(left_table, ''))}\.{re.escape(left_column)}"
    right_ref = rf"{re.escape(aliases.get(right_table, ''))}\.{re.escape(right_column)}"
    if not aliases.get(left_table) or not aliases.get(right_table):
        return False
    return bool(
        re.search(rf"\b{left_ref}\s*=\s*{right_ref}\b", sql, flags=re.IGNORECASE)
        or re.search(rf"\b{right_ref}\s*=\s*{left_ref}\b", sql, flags=re.IGNORECASE)
    )


def unqualified_metadata_columns(
    sql: str,
    expected_tables: list[str],
    metadata: dict[str, dict[str, dict[str, str]]],
) -> list[str]:
    columns = set().union(*(set(metadata.get(table, {})) for table in expected_tables))
    result = []
    for column in sorted(columns, key=len, reverse=True):
        if re.search(rf"(?<![.\w]){re.escape(column)}(?!\w)", sql, flags=re.IGNORECASE):
            result.append(column)
    return result


def check_sample(
    sample: dict[str, Any],
    index: int,
    guard: SQLGuard,
    metadata: dict[str, dict[str, dict[str, str]]],
    previous_questions: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    sid = str(sample.get("id", ""))
    question = str(sample.get("question", "")).strip()
    sql = str(sample.get("sql", "")).strip()
    sql_lower = sql.lower()
    expected_tables = [str(value).lower() for value in sample.get("expected_tables", [])]
    expected_columns = {str(value).lower() for value in sample.get("expected_columns", [])}
    join_keys = sample.get("join_keys", [])
    risk_notes = str(sample.get("risk_notes", ""))

    missing = sorted(REQUIRED_FIELDS - set(sample))
    empty = sorted(
        field for field in REQUIRED_FIELDS
        if field in sample and sample[field] in (None, "", [])
    )
    if missing:
        errors.append(f"缺少必填字段: {', '.join(missing)}")
    if empty:
        errors.append(f"必填字段为空: {', '.join(empty)}")
    if sid != f"L3_P2_SQL_{index:03d}":
        errors.append(f"ID应为L3_P2_SQL_{index:03d}")
    if sample.get("group") != "P2" or sample.get("priority") != "P2":
        errors.append("group和priority必须为P2")
    if sample.get("train_decision") != "draft":
        errors.append("train_decision必须为draft")
    if sample.get("review_status") != "pending_static_check":
        errors.append("review_status必须为pending_static_check")
    if question in previous_questions:
        errors.append("question与P0/P1完全重复")
    if not 2 <= len(expected_tables) <= 3:
        errors.append("expected_tables必须包含2至3张表")
    if len(set(expected_tables)) != len(expected_tables):
        errors.append("expected_tables包含重复表")

    if not re.match(r"^(SELECT|WITH)\b", sql, flags=re.IGNORECASE):
        errors.append("SQL必须以SELECT或WITH开头")
    if re.search(r"\bSELECT\s+\*", sql, flags=re.IGNORECASE):
        errors.append("禁止SELECT *")
    limits = [int(value) for value in re.findall(r"\bLIMIT\s+(\d+)\b", sql, flags=re.IGNORECASE)]
    if not limits:
        errors.append("SQL缺少数值LIMIT")
    elif any(value > 100 for value in limits):
        errors.append("LIMIT不得大于100")
    if re.search(r"\b(?:CROSS|NATURAL)\s+JOIN\b", sql, flags=re.IGNORECASE):
        errors.append("禁止CROSS JOIN或NATURAL JOIN")
    if re.search(r"\bJOIN\s+[^\n]+\s+USING\s*\(", sql, flags=re.IGNORECASE):
        errors.append("禁止JOIN USING")
    if re.search(r"\bLIKE\b", sql, flags=re.IGNORECASE):
        errors.append("禁止LIKE连接")
    for operation in FORBIDDEN_OPERATIONS:
        if has_word(sql_lower, operation):
            errors.append(f"SQL包含禁止操作: {operation.upper()}")
    for marker in FORBIDDEN_TABLES | SYSTEM_TABLES:
        if marker in sql_lower:
            errors.append(f"命中冻结或系统表: {marker}")
    for marker in FROZEN_BUSINESS_MARKERS:
        if marker in question:
            errors.append(f"命中冻结业务: {marker}")
    for pattern in FROZEN_STATUS_PATTERNS:
        if re.search(pattern, sql, flags=re.IGNORECASE):
            errors.append(f"重新引入P1冻结状态口径: {pattern}")
    for column in CREDENTIAL_COLUMNS:
        if re.search(rf"\b[a-z_]\w*\.{re.escape(column)}\b", sql, flags=re.IGNORECASE):
            errors.append(f"使用敏感凭据字段: {column}")

    aliases = table_aliases(sql)
    if set(aliases) != set(expected_tables):
        errors.append(f"SQL表别名解析与expected_tables不一致: {sorted(aliases)}")
    join_matches = re.findall(
        r"\b(?:(INNER|LEFT)\s+)?JOIN\s+[a-z_][a-z0-9_]*\s+(?:AS\s+)?[a-z_][a-z0-9_]*\s+ON\b",
        sql,
        flags=re.IGNORECASE,
    )
    if len(join_matches) != max(0, len(expected_tables) - 1):
        errors.append("显式JOIN数量与表数量不一致")
    join_types = [f"{value.upper()} JOIN" if value else "INNER JOIN" for value in join_matches]
    if any(value not in {"INNER JOIN", "LEFT JOIN"} for value in join_types):
        errors.append("JOIN类型只能是INNER JOIN或LEFT JOIN")
    on_segments = re.findall(
        r"\bON\s+(.+?)(?=\b(?:INNER|LEFT)?\s*JOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if any(re.search(r"\bOR\b", segment, flags=re.IGNORECASE) for segment in on_segments):
        errors.append("ON条件禁止OR")

    if len(join_keys) != max(0, len(expected_tables) - 1):
        errors.append("join_keys数量与JOIN边数量不一致")
    join_key_errors = 0
    type_errors = 0
    evidence_errors = 0
    edge_signatures: list[frozenset[str]] = []
    for join_key in join_keys:
        left = str(join_key.get("left", "")).lower()
        right = str(join_key.get("right", "")).lower()
        evidence = str(join_key.get("evidence", "")).strip()
        edge = frozenset((left, right))
        edge_signatures.append(edge)
        if edge not in CONFIRMED_EDGES:
            errors.append(f"JOIN边不在confirmed集合: {left}={right}")
            join_key_errors += 1
        if not evidence:
            errors.append(f"JOIN证据为空: {left}={right}")
            evidence_errors += 1
        for value in (left, right):
            if "." not in value:
                errors.append(f"JOIN字段未使用表名限定: {value}")
                join_key_errors += 1
                continue
            table, column = value.split(".", 1)
            if table not in metadata or column not in metadata.get(table, {}):
                errors.append(f"JOIN字段不在metadata: {value}")
                join_key_errors += 1
        if "." in left and "." in right:
            left_table, left_column = left.split(".", 1)
            right_table, right_column = right.split(".", 1)
            left_type = metadata.get(left_table, {}).get(left_column, {}).get("type", "")
            right_type = metadata.get(right_table, {}).get(right_column, {}).get("type", "")
            if not left_type or not right_type or type_family(left_type) != type_family(right_type):
                errors.append(f"JOIN字段类型不兼容: {left_type} / {right_type}")
                type_errors += 1
        if not sql_has_join_equality(sql, aliases, left, right):
            errors.append(f"join_keys与SQL ON不一致: {left}={right}")
            join_key_errors += 1
    if len(set(edge_signatures)) != len(edge_signatures):
        errors.append("join_keys包含重复边")

    for table in expected_tables:
        if table not in metadata:
            errors.append(f"expected table不在metadata: {table}")
    for value in sorted(expected_columns):
        if "." not in value:
            errors.append(f"expected column未使用表名限定: {value}")
            continue
        table, column = value.split(".", 1)
        if table not in metadata or column not in metadata.get(table, {}):
            errors.append(f"expected column不在metadata: {value}")
        if table not in expected_tables:
            errors.append(f"expected column来自计划外表: {value}")
    bare_columns = unqualified_metadata_columns(sql, expected_tables, metadata)
    if bare_columns:
        errors.append(f"SQL存在未使用别名限定的字段: {bare_columns}")

    if "LEFT JOIN" in join_types:
        if "包含没有" not in question:
            errors.append("LEFT JOIN问题必须明确包含无匹配主表记录")
        where_match = re.search(
            r"\bWHERE\b(.+?)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        right_aliases = re.findall(
            r"\bLEFT\s+JOIN\s+[a-z_][a-z0-9_]*\s+(?:AS\s+)?([a-z_][a-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )
        if where_match and any(
            re.search(rf"\b{re.escape(alias)}\.", where_match.group(1), flags=re.IGNORECASE)
            for alias in right_aliases
        ):
            errors.append("LEFT JOIN的WHERE条件引用右表，可能退化为INNER JOIN")

    is_aggregate = bool(re.search(r"\b(?:COUNT|SUM|AVG)\s*\(", sql, flags=re.IGNORECASE))
    if is_aggregate:
        if "GROUP BY" not in sql.upper():
            errors.append("跨表聚合必须包含GROUP BY")
        if "一对多" not in risk_notes or "DISTINCT" not in risk_notes or "去重" not in risk_notes:
            errors.append("聚合risk_notes必须说明一对多风险和DISTINCT去重")
    if len(expected_tables) == 3 and "组合放大" not in risk_notes:
        errors.append("三表候选risk_notes必须说明组合放大风险")

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
        errors.append(
            "used_columns与expected_columns不一致: "
            f"used多出={sorted(used_columns - expected_columns)}, "
            f"expected多出={sorted(expected_columns - used_columns)}"
        )

    frozen_hit = any(marker in question for marker in FROZEN_BUSINESS_MARKERS) or any(
        table in FORBIDDEN_TABLES for table in expected_tables
    ) or any(re.search(pattern, sql, flags=re.IGNORECASE) for pattern in FROZEN_STATUS_PATTERNS)
    status = "fail" if errors else "warning" if warnings else "pass"
    return {
        "id": sid,
        "question": question,
        "expected_tables": expected_tables,
        "join_type": ", ".join(join_types),
        "join_keys": [f"{item.get('left')}={item.get('right')}" for item in join_keys],
        "join_evidence": [str(item.get("evidence", "")) for item in join_keys],
        "guard": guard_result.to_dict(),
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "join_key_ok": join_key_errors == 0 and evidence_errors == 0,
        "type_compatible": type_errors == 0,
        "metadata_ok": not any("metadata" in error for error in errors),
        "frozen_hit": frozen_hit,
        "previous_duplicate": question in previous_questions,
        "edge_signatures": edge_signatures,
    }


def md(value: Any) -> str:
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value) or "无"
    else:
        text = str(value) if value not in (None, "") else "无"
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(
    audit_rows: list[dict[str, Any]],
    results: list[dict[str, Any]],
    ids_unique: bool,
    ids_continuous: bool,
    edge_counts: Counter[frozenset[str]],
) -> bool:
    support_counts = Counter(row["support"] for row in audit_rows)
    status_counts = Counter(item["status"] for item in results)
    two_table_count = sum(len(item["expected_tables"]) == 2 for item in results)
    three_table_count = sum(len(item["expected_tables"]) == 3 for item in results)
    guard_pass = sum(item["guard"]["passed"] for item in results)
    guard_warning = sum(item["guard"]["severity"] == "warning" for item in results)
    guard_fail = len(results) - guard_pass
    join_key_pass = sum(item["join_key_ok"] for item in results)
    type_pass = sum(item["type_compatible"] for item in results)
    metadata_pass = sum(item["metadata_ok"] for item in results)
    duplicates = sum(item["previous_duplicate"] for item in results)
    frozen_hits = sum(item["frozen_hit"] for item in results)
    scope_passed = (
        len(audit_rows) == 9
        and sum(support_counts[level] for level in SUPPORT_LEVELS) == 9
        and support_counts["confirmed"] >= 1
    )
    draft_passed = (
        scope_passed
        and 8 <= len(results) <= 12
        and ids_unique
        and ids_continuous
        and two_table_count + three_table_count == len(results)
        and three_table_count <= 1
        and all(count <= 3 for count in edge_counts.values())
        and guard_pass == len(results)
        and guard_warning == 0
        and guard_fail == 0
        and status_counts["warning"] == 0
        and status_counts["fail"] == 0
        and join_key_pass == len(results)
        and type_pass == len(results)
        and metadata_pass == len(results)
        and duplicates == 0
        and frozen_hits == 0
    )

    confirmed = [row["scene"] for row in audit_rows if row["support"] == "confirmed"]
    manual = [
        row["scene"]
        for row in audit_rows
        if row["support"] == "requires_manual_review_direction"
    ]
    excluded = [
        row["scene"] for row in audit_rows if row["support"] == "excluded_direction"
    ]
    lines = [
        "# Level 3 P2 SQL 候选静态检查结果",
        "",
        "## 汇总",
        "",
        f"- 基础 commit：`{BASE_COMMIT}`",
        f"- 审计场景总数：{len(audit_rows)}",
        f"- confirmed 场景数：{support_counts['confirmed']}",
        f"- requires_manual_review_direction 数量：{support_counts['requires_manual_review_direction']}",
        f"- excluded_direction 数量：{support_counts['excluded_direction']}",
        f"- confirmed 场景：{md(confirmed)}",
        f"- 人工复核方向：{md(manual)}",
        f"- 排除方向：{md(excluded)}",
        f"- 候选总数：{len(results)}",
        f"- 二表候选数量：{two_table_count}",
        f"- 三表候选数量：{three_table_count}",
        f"- ID 连续唯一：{'是' if ids_unique and ids_continuous else '否'}",
        f"- SQLGuard pass/warning/fail：{guard_pass}/{guard_warning}/{guard_fail}",
        f"- JOIN 键检查通过数量：{join_key_pass}",
        f"- 字段类型兼容数量：{type_pass}",
        f"- metadata 字段通过数量：{metadata_pass}",
        f"- P0/P1 完全重复数量：{duplicates}",
        f"- 冻结场景命中数量：{frozen_hits}",
        f"- P2 范围是否通过：{'是' if scope_passed else '否'}",
        f"- P2 草案静态阶段是否通过：{'是' if draft_passed else '否'}",
        "- 是否连接数据库：否",
        "- 是否执行 SQL：否",
        "- 是否调用 DeepSeek：否",
        "- 是否训练：否",
        "- 是否调用 vn.train()：否",
        "- 是否调用 memory.save_tool_usage()：否",
        "- 是否写入 ChromaDB：否",
        "",
        "## 逐样本结果",
        "",
        "| id | question | expected_tables | join_type | join_keys | join_evidence | used_tables | unknown_tables | unknown_columns | candidate_mismatch | SQLGuard | 最终状态 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        guard = item["guard"]
        guard_text = f"passed={guard['passed']}, severity={guard['severity']}"
        detail = item["errors"] or item["warnings"]
        status_text = item["status"] if not detail else f"{item['status']}: {'; '.join(detail)}"
        lines.append(
            "| "
            + " | ".join(
                md(value)
                for value in (
                    item["id"],
                    item["question"],
                    item["expected_tables"],
                    item["join_type"],
                    item["join_keys"],
                    item["join_evidence"],
                    guard["used_tables"],
                    guard["unknown_tables"],
                    guard["unknown_columns"],
                    guard["candidate_mismatch"],
                    guard_text,
                    status_text,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            (
                "P2 范围审计与候选草案静态阶段通过。所有候选仍为 draft，"
                "下一阶段必须先人工审批，只有 approved 样本才可考虑训练。"
                if draft_passed
                else "P2 静态门禁未通过，不得创建 review_result 或进入训练。"
            ),
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return draft_passed


def main() -> int:
    audit_rows = parse_scope_audit()
    draft = load_json(DRAFT_PATH)
    metadata = load_metadata()
    previous_questions = {
        str(item.get("question", "")).strip()
        for path in (P0_REVIEW_PATH, P1_REVIEW_PATH)
        for item in load_json(path)
    }
    guard = SQLGuard(index_path=METADATA_PATH)
    results = [
        check_sample(sample, index, guard, metadata, previous_questions)
        for index, sample in enumerate(draft, start=1)
    ]
    ids = [str(sample.get("id", "")) for sample in draft]
    ids_unique = len(ids) == len(set(ids))
    ids_continuous = ids == [f"L3_P2_SQL_{index:03d}" for index in range(1, len(ids) + 1)]
    edge_counts: Counter[frozenset[str]] = Counter(
        edge for result in results for edge in result["edge_signatures"]
    )
    passed = write_report(audit_rows, results, ids_unique, ids_continuous, edge_counts)
    summary = {
        "audit_total": len(audit_rows),
        "support_counts": dict(Counter(row["support"] for row in audit_rows)),
        "candidate_total": len(results),
        "two_table": sum(len(item["expected_tables"]) == 2 for item in results),
        "three_table": sum(len(item["expected_tables"]) == 3 for item in results),
        "status_counts": dict(Counter(item["status"] for item in results)),
        "passed": passed,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

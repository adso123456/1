"""第 3 级 P0 候选样本静态检查脚本。

只做静态检查，不连接数据库，不执行 SQL，不训练，不写 ChromaDB。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard

DRAFT_PATH = Path(__file__).resolve().parent / "sql_examples_level3_p0_draft.json"
METADATA_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"

FORBIDDEN_TABLE = "wm_waterquality_threshold"
FORBIDDEN_OPERATIONS_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "comment", "merge", "grant", "revoke",
}
SYSTEM_TABLE_PREFIXES = (
    "information_schema", "pg_catalog", "sqlite_master", "sqlite_schema",
)

# requires_manual_review 保留字段关键词
MANUAL_REVIEW_KEYWORDS = [
    "daily_supply_capacity",
    "annual_actual_withdrawal",
    "wm_water_source_intake_v2",
    "primary_entity_name",
    "discharge_permit_no",
    "credit_code",
]


def load_draft(path: Path) -> list[dict]:
    """加载草案 JSON，解析失败则退出。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            print(f"[FAIL] 草案文件不是 JSON 数组: {path}")
            sys.exit(1)
        return data
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON 解析失败: {e}")
        sys.exit(1)


def load_metadata_tables() -> dict[str, set[str]]:
    """加载 metadata 索引，返回 {table: {columns}}。"""
    rows = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    tc: dict[str, set[str]] = {}
    for row in rows:
        table = str(row.get("table", "")).strip().lower()
        column = str(row.get("column", "")).strip().lower()
        if table and column:
            tc.setdefault(table, set()).add(column)
    return tc


def check_sample(
    sample: dict,
    guard: SQLGuard,
    metadata_tc: dict[str, set[str]],
    index: int,
) -> dict:
    """对单条样本做全量静态检查，返回检查结果字典。"""
    sid = sample.get("id", f"UNKNOWN_{index}")
    result = {
        "index": index,
        "id": sid,
        "checks": {},
        "errors": [],
        "warnings": [],
    }

    # 1. 必填字段检查
    required_fields = [
        "id", "group", "priority", "question", "sql",
        "expected_tables", "expected_columns", "business_intent",
        "risk_notes", "train_decision", "review_status",
    ]
    for field in required_fields:
        if field not in sample or not sample[field]:
            result["errors"].append(f"缺少必填字段: {field}")
    result["checks"]["required_fields"] = len([e for e in result["errors"] if "必填字段" in e]) == 0

    # 2. id 格式检查
    if not sid.startswith("L3_P0_SQL_"):
        result["errors"].append(f"id 格式不正确: {sid}，应以 L3_P0_SQL_ 开头")
    result["checks"]["id_format"] = sid.startswith("L3_P0_SQL_")

    # 3. group 检查
    group = sample.get("group", "")
    if group not in ("A", "B"):
        result["errors"].append(f"group 必须为 A 或 B，实际: {group}")
    result["checks"]["group_valid"] = group in ("A", "B")

    # 4. priority 检查
    priority = sample.get("priority", "")
    if priority != "P0":
        result["errors"].append(f"priority 必须为 P0，实际: {priority}")
    result["checks"]["priority_p0"] = priority == "P0"

    # 5. train_decision 检查
    train_decision = sample.get("train_decision", "")
    if train_decision != "draft":
        result["errors"].append(f"train_decision 必须为 draft，实际: {train_decision}")
    result["checks"]["train_decision_draft"] = train_decision == "draft"

    # 6. review_status 检查
    review_status = sample.get("review_status", "")
    if review_status != "pending_static_check":
        result["warnings"].append(f"review_status 期望 pending_static_check，实际: {review_status}")
    result["checks"]["review_status"] = review_status == "pending_static_check"

    sql = sample.get("sql", "")

    # 7. SQL 非空
    if not sql.strip():
        result["errors"].append("SQL 为空")
        result["checks"]["sql_nonempty"] = False
    else:
        result["checks"]["sql_nonempty"] = True

    # 8. SQL 以 SELECT 开头（忽略前导空白和 CTE）
    sql_stripped = sql.strip()
    if not sql_stripped.upper().startswith("SELECT") and not sql_stripped.upper().startswith("WITH"):
        result["errors"].append(f"SQL 不以 SELECT 或 WITH 开头: {sql_stripped[:50]}...")
        result["checks"]["sql_starts_select"] = False
    else:
        result["checks"]["sql_starts_select"] = True

    # 9. 检查 SELECT *
    if "SELECT *" in sql.upper() or "select *" in sql:
        result["errors"].append("SQL 包含 SELECT *，禁止使用")
        result["checks"]["no_select_star"] = False
    else:
        result["checks"]["no_select_star"] = True

    # 10. 检查 LIMIT
    if "LIMIT" not in sql.upper():
        result["errors"].append("SQL 缺少 LIMIT")
        result["checks"]["has_limit"] = False
    else:
        result["checks"]["has_limit"] = True

    # 11. 检查禁止操作 (DDL/DML)
    sql_lower = sql.lower()
    found_forbidden = []
    for op in sorted(FORBIDDEN_OPERATIONS_KEYWORDS):
        import re
        if re.search(rf"\b{op}\b", sql_lower):
            found_forbidden.append(op.upper())
    if found_forbidden:
        result["errors"].append(f"SQL 包含禁止操作: {', '.join(found_forbidden)}")
    result["checks"]["no_forbidden_ops"] = len(found_forbidden) == 0

    # 12. 检查 wm_waterquality_threshold
    if FORBIDDEN_TABLE in sql_lower:
        result["errors"].append(f"SQL 包含禁止表: {FORBIDDEN_TABLE}")
    result["checks"]["no_forbidden_table"] = FORBIDDEN_TABLE not in sql_lower

    # 13. 检查系统表
    found_system = []
    for prefix in SYSTEM_TABLE_PREFIXES:
        if prefix in sql_lower:
            found_system.append(prefix)
    if found_system:
        result["errors"].append(f"SQL 涉及系统表: {', '.join(found_system)}")
    result["checks"]["no_system_tables"] = len(found_system) == 0

    # 14. 检查 requires_manual_review 保留项
    found_manual_review = []
    for keyword in MANUAL_REVIEW_KEYWORDS:
        if keyword.lower() in sql_lower:
            found_manual_review.append(keyword)
    if found_manual_review:
        result["errors"].append(f"SQL 涉及 requires_manual_review 保留字段/表: {', '.join(found_manual_review)}")
    result["checks"]["no_manual_review"] = len(found_manual_review) == 0

    # 15. SQL Guard 静态校验
    expected_tables = sample.get("expected_tables", [])
    guard_result = guard.validate(
        sql=sql,
        query=sample.get("question", ""),
        deterministic_candidate_tables=expected_tables,
    )
    result["guard"] = guard_result.to_dict()

    result["checks"]["guard_passed"] = guard_result.passed
    result["checks"]["guard_severity_ok"] = guard_result.severity == "ok"

    if not guard_result.passed:
        result["errors"].append(f"SQL Guard 未通过: {guard_result.reason}")
    if guard_result.severity not in ("ok",):
        if guard_result.severity == "warning":
            result["warnings"].append(f"SQL Guard severity=warning: {guard_result.reason}")
        elif guard_result.severity == "error":
            result["errors"].append(f"SQL Guard severity=error: {guard_result.reason}")

    # 16. 表匹配检查: used_tables vs expected_tables
    used_tables = set(guard_result.used_tables)
    exp_tables = set(expected_tables)
    if used_tables != exp_tables:
        extra = used_tables - exp_tables
        missing = exp_tables - used_tables
        msg_parts = []
        if extra:
            msg_parts.append(f"多出表: {extra}")
        if missing:
            msg_parts.append(f"缺少表: {missing}")
        result["warnings"].append(f"表匹配不一致: {'; '.join(msg_parts)}")
    result["checks"]["tables_match"] = used_tables == exp_tables

    # 17. 字段检查: used_columns 是否都在 metadata index
    unknown_cols = set(guard_result.unknown_columns)
    if unknown_cols:
        result["errors"].append(f"未知字段(不在 metadata index): {unknown_cols}")
    result["checks"]["columns_in_metadata"] = len(unknown_cols) == 0

    # 18. expected_columns 匹配检查
    expected_columns = set(sample.get("expected_columns", []))
    used_columns = set(guard_result.used_columns)
    if expected_columns and expected_columns != used_columns:
        extra_cols = used_columns - expected_columns
        missing_cols = expected_columns - used_columns
        msg_parts = []
        if extra_cols:
            msg_parts.append(f"多出字段: {extra_cols}")
        if missing_cols:
            msg_parts.append(f"缺少字段: {missing_cols}")
        result["warnings"].append(f"expected_columns 与 used_columns 不一致: {'; '.join(msg_parts)}")
    result["checks"]["expected_columns_match"] = expected_columns == used_columns

    # 综合判定
    result["passed"] = len(result["errors"]) == 0
    result["status"] = "pass" if result["passed"] else "fail"
    if result["warnings"] and result["passed"]:
        result["status"] = "warning"

    return result


def main() -> int:
    print("=" * 70)
    print("第 3 级 P0 候选样本静态检查")
    print("=" * 70)
    print()

    # 环境声明
    print("[环境声明]")
    print(f"  工作目录: {PROJECT_ROOT}")
    print(f"  草案文件: {DRAFT_PATH}")
    print(f"  Metadata: {METADATA_PATH}")
    print(f"  是否连接数据库: 否")
    print(f"  是否执行 SQL: 否")
    print(f"  是否训练 Vanna: 否")
    print(f"  是否写 ChromaDB: 否")
    print()

    # 加载
    print("[加载]")
    samples = load_draft(DRAFT_PATH)
    print(f"  样本总数: {len(samples)}")
    metadata_tc = load_metadata_tables()
    print(f"  Metadata 表数: {len(metadata_tc)}")
    print()

    # ID 唯一性检查
    ids = [s.get("id", "") for s in samples]
    id_counts = {}
    for sid in ids:
        id_counts[sid] = id_counts.get(sid, 0) + 1
    duplicates = {k: v for k, v in id_counts.items() if v > 1}
    if duplicates:
        print(f"[FAIL] ID 重复: {duplicates}")
        return 1
    print(f"[OK] ID 唯一性: {len(set(ids))} 个唯一 ID")
    print()

    # 初始化 SQL Guard
    guard = SQLGuard(str(METADATA_PATH))

    # 逐样本检查
    print("[逐样本检查]")
    print()

    results = []
    for i, sample in enumerate(samples):
        r = check_sample(sample, guard, metadata_tc, i)
        results.append(r)

        sid = r["id"]
        status_label = {"pass": "PASS", "warning": "WARN", "fail": "FAIL"}[r["status"]]
        print(f"  [{status_label}] {sid} (group={sample.get('group')})")

        for err in r["errors"]:
            print(f"    [ERROR] {err}")
        for warn in r["warnings"]:
            print(f"    [WARN]  {warn}")

        guard_info = r["guard"]
        print(f"    Guard: passed={guard_info['passed']}, severity={guard_info['severity']}")
        print(f"    Tables: {guard_info['used_tables']}")
        print(f"    Columns: {guard_info['used_columns']}")
        if guard_info["unknown_columns"]:
            print(f"    Unknown columns: {guard_info['unknown_columns']}")
        print()

    # 统计
    print("=" * 70)
    print("[统计]")
    passed = [r for r in results if r["status"] == "pass"]
    warned = [r for r in results if r["status"] == "warning"]
    failed = [r for r in results if r["status"] == "fail"]
    group_a = [r for r in results if r.get("checks", {}).get("group_valid") and samples[r["index"]].get("group") == "A"]
    group_b = [r for r in results if r.get("checks", {}).get("group_valid") and samples[r["index"]].get("group") == "B"]

    print(f"  样本总数: {len(results)}")
    print(f"  A 组: {len(group_a)}")
    print(f"  B 组: {len(group_b)}")
    print(f"  PASS: {len(passed)}")
    print(f"  WARNING: {len(warned)}")
    print(f"  FAIL: {len(failed)}")
    print(f"  SQL Guard passed=True: {sum(1 for r in results if r['checks'].get('guard_passed'))}")
    print(f"  SQL Guard severity=ok: {sum(1 for r in results if r['checks'].get('guard_severity_ok'))}")
    print()

    # 失败样本列表
    if failed:
        print("[失败样本]")
        for r in failed:
            print(f"  {r['id']}: {r['errors']}")
        print()

    # 警告样本列表
    if warned:
        print("[警告样本]")
        for r in warned:
            print(f"  {r['id']}: {r['warnings']}")
        print()

    # 结论
    print("=" * 70)
    if failed:
        print(f"[结论] 不通过 — {len(failed)} 条样本存在错误")
        return 1
    elif warned:
        print(f"[结论] 通过(有警告) — {len(results)} 条样本全部无错误，{len(warned)} 条有 warning")
        return 0
    else:
        print(f"[结论] 通过 — {len(results)} 条样本全部通过静态检查")
        return 0


if __name__ == "__main__":
    sys.exit(main())

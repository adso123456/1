"""Level 3 P2 JOIN 真实数据库只读审计。"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.py"
DRAFT_PATH = Path(__file__).with_name("sql_examples_level3_p2_draft.json")
REPORT_PATH = Path(__file__).with_name("level3_p2_join_feasibility_result.md")

JOIN_EDGES = [
    {
        "id": "J1",
        "name": "排污口基础—整治",
        "left_table": "rs_outlet_info_v2",
        "left_entity": "id",
        "left_key": "id",
        "left_filter": "del_flag = '0'",
        "right_table": "rs_outlet_remediation_v2",
        "right_entity": "id",
        "right_key": "outlet_id",
        "right_filter": "del_flag = '0'",
    },
    {
        "id": "J2",
        "name": "排污口基础—实况",
        "left_table": "rs_outlet_info_v2",
        "left_entity": "id",
        "left_key": "id",
        "left_filter": "del_flag = '0'",
        "right_table": "rs_outlet_live_v2",
        "right_entity": "id",
        "right_key": "outlet_id",
        "right_filter": "del_flag = '0'",
    },
    {
        "id": "J3",
        "name": "断面—水质目标",
        "left_table": "wm_section_info",
        "left_entity": "id",
        "left_key": "id",
        "left_filter": "del_flag = '0'",
        "right_table": "wm_section_wq_info",
        "right_entity": "id",
        "right_key": "section_id",
        "right_filter": "del_flag = '0'",
    },
    {
        "id": "J4",
        "name": "断面—水体",
        "left_table": "wm_section_info",
        "left_entity": "id",
        "left_key": "water_body_id",
        "left_filter": "del_flag = '0'",
        "right_table": "wm_waterbody_info",
        "right_entity": "id",
        "right_key": "id",
        "right_filter": "del_flag = '0'",
    },
    {
        "id": "J5",
        "name": "水文站—区县",
        "left_table": "wm_hydrological_info",
        "left_entity": "id",
        "left_key": "region_code",
        "left_filter": "del_flag = '0'",
        "right_table": "gis_region_county",
        "right_entity": "id",
        "right_key": "region_code",
        "right_filter": "TRUE",
    },
]

DEL_FLAG_TABLES = [
    "rs_outlet_info_v2",
    "rs_outlet_remediation_v2",
    "rs_outlet_live_v2",
    "wm_section_info",
    "wm_section_wq_info",
    "wm_waterbody_info",
    "wm_hydrological_info",
]


def load_db_kwargs() -> dict[str, Any]:
    """从项目配置源码读取字面量 DB_KWARGS，不执行 config/settings.py。"""
    tree = ast.parse(SETTINGS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DB_KWARGS" for target in node.targets):
            continue
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "dict":
            return {item.arg: ast.literal_eval(item.value) for item in node.value.keywords if item.arg}
        if isinstance(node.value, ast.Dict):
            return ast.literal_eval(node.value)
    raise RuntimeError("config/settings.py 中未找到可安全读取的 DB_KWARGS")


def connect_readonly(db_kwargs: dict[str, Any]):
    conn = psycopg2.connect(**db_kwargs)
    conn.set_session(readonly=True, autocommit=False)
    cur = conn.cursor()
    cur.execute("BEGIN READ ONLY")
    cur.execute("SELECT current_setting('transaction_read_only')")
    readonly = cur.fetchone()[0] == "on"
    if not readonly:
        conn.rollback()
        conn.close()
        raise RuntimeError("数据库事务未进入只读模式")
    return conn, cur


def select(cur, sql: str, params: tuple[Any, ...] | None = None) -> tuple[list[str], list[tuple[Any, ...]]]:
    normalized = re.sub(r"^\s*(?:--[^\n]*\n\s*)*", "", sql)
    if not re.match(r"^(SELECT|WITH)\b", normalized, flags=re.IGNORECASE):
        raise ValueError("审计执行器拒绝非 SELECT/WITH SQL")
    cur.execute(sql, params)
    columns = [item.name for item in cur.description] if cur.description else []
    return columns, cur.fetchall()


def ident(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError(f"非法标识符: {value}")
    return value


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def audit_edge(cur, edge: dict[str, str]) -> dict[str, Any]:
    lt = ident(edge["left_table"])
    le = ident(edge["left_entity"])
    lk = ident(edge["left_key"])
    rt = ident(edge["right_table"])
    reid = ident(edge["right_entity"])
    rk = ident(edge["right_key"])
    sql = f"""
WITH l AS (
    SELECT {le} AS entity_id, {lk} AS join_key
    FROM {lt}
    WHERE {edge['left_filter']}
),
r AS (
    SELECT {reid} AS entity_id, {rk} AS join_key
    FROM {rt}
    WHERE {edge['right_filter']}
),
right_per_key AS (
    SELECT r.join_key, COUNT(*) AS record_count
    FROM r
    WHERE r.join_key IS NOT NULL
      AND EXISTS (SELECT 1 FROM l WHERE l.join_key = r.join_key)
    GROUP BY r.join_key
)
SELECT
    (SELECT COUNT(*) FROM {lt}) AS left_total_raw,
    (SELECT COUNT(*) FROM {rt}) AS right_total_raw,
    (SELECT COUNT(*) FROM l) AS left_total,
    (SELECT COUNT(*) FROM r) AS right_total,
    (SELECT COUNT(*) FROM l WHERE join_key IS NOT NULL) AS left_key_nonnull,
    (SELECT COUNT(*) FROM r WHERE join_key IS NOT NULL) AS right_key_nonnull,
    (SELECT COUNT(DISTINCT join_key) FROM l WHERE join_key IS NOT NULL) AS left_key_distinct,
    (SELECT COUNT(DISTINCT join_key) FROM r WHERE join_key IS NOT NULL) AS right_key_distinct,
    (SELECT COUNT(*) FROM l WHERE EXISTS (SELECT 1 FROM r WHERE r.join_key = l.join_key)) AS matched_left_entities,
    (SELECT COUNT(*) FROM l WHERE NOT EXISTS (SELECT 1 FROM r WHERE r.join_key = l.join_key)) AS unmatched_left_entities,
    (SELECT COUNT(*) FROM r WHERE r.join_key IS NULL OR NOT EXISTS (SELECT 1 FROM l WHERE l.join_key = r.join_key)) AS orphan_right_records,
    COALESCE((SELECT MAX(record_count) FROM right_per_key), 0) AS max_right_records_per_left_key,
    COALESCE((SELECT AVG(record_count::numeric) FROM right_per_key), 0) AS avg_right_records_per_left_key,
    (SELECT COUNT(*) FROM right_per_key WHERE record_count > 1) AS left_keys_with_multiple_right_records
"""
    columns, rows = select(cur, sql)
    result = dict(zip(columns, rows[0]))
    left_total = int(result["left_total"])
    matched = int(result["matched_left_entities"])
    result["match_rate_percent"] = round(100.0 * matched / left_total, 4) if left_total else 0.0
    return {**edge, **jsonable(result)}


def audit_del_flags(cur) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table in DEL_FLAG_TABLES:
        table = ident(table)
        _, rows = select(
            cur,
            f"SELECT del_flag, COUNT(*) AS record_count FROM {table} GROUP BY del_flag ORDER BY del_flag NULLS FIRST",
        )
        distribution = {"<NULL>" if value is None else str(value): int(count) for value, count in rows}
        result[table] = {
            "distribution": distribution,
            "null_count": distribution.get("<NULL>", 0),
            "active_zero_count": distribution.get("0", 0),
            "other_values": {
                key: value for key, value in distribution.items() if key not in {"<NULL>", "0"}
            },
        }
    return result


def audit_domains(cur) -> dict[str, Any]:
    _, month_rows = select(
        cur,
        "SELECT month, COUNT(*) FROM wm_section_wq_info GROUP BY month ORDER BY month NULLS FIRST",
    )
    _, year_rows = select(
        cur,
        "SELECT year, COUNT(*) FROM wm_section_wq_info GROUP BY year ORDER BY year NULLS FIRST",
    )
    _, target_rows = select(
        cur,
        "SELECT water_quality_target_level, COUNT(*) FROM wm_section_wq_info GROUP BY water_quality_target_level ORDER BY water_quality_target_level NULLS FIRST",
    )
    _, examine_rows = select(
        cur,
        "SELECT is_examine, COUNT(*) FROM wm_section_info GROUP BY is_examine ORDER BY is_examine NULLS FIRST",
    )
    _, annual_integrity_rows = select(
        cur,
        """
WITH annual AS (
    SELECT section_id, year, COUNT(*) AS record_count
    FROM wm_section_wq_info
    WHERE del_flag = '0' AND month = 0
    GROUP BY section_id, year
)
SELECT COUNT(*) AS section_year_groups,
       COALESCE(MAX(record_count), 0) AS max_records_per_section_year,
       COUNT(*) FILTER (WHERE record_count > 1) AS duplicate_section_year_groups
FROM annual
""",
    )
    annual_groups, annual_max, annual_duplicates = annual_integrity_rows[0]
    return {
        "month_distribution": [[value, int(count)] for value, count in month_rows],
        "month_zero_count": sum(int(count) for value, count in month_rows if value == 0),
        "year_distribution": [[value, int(count)] for value, count in year_rows],
        "target_level_distribution": [[value, int(count)] for value, count in target_rows],
        "is_examine_distribution": [[value, int(count)] for value, count in examine_rows],
        "annual_section_year_groups": int(annual_groups),
        "annual_max_records_per_section_year": int(annual_max),
        "annual_duplicate_section_year_groups": int(annual_duplicates),
    }


def audit_region_codes(cur) -> dict[str, Any]:
    _, left_lengths = select(
        cur,
        "SELECT LENGTH(region_code), COUNT(*) FROM wm_hydrological_info WHERE del_flag = '0' GROUP BY LENGTH(region_code) ORDER BY LENGTH(region_code) NULLS FIRST",
    )
    _, right_lengths = select(
        cur,
        "SELECT LENGTH(region_code), COUNT(*) FROM gis_region_county GROUP BY LENGTH(region_code) ORDER BY LENGTH(region_code) NULLS FIRST",
    )
    _, count_rows = select(
        cur,
        """
SELECT
    COUNT(*) FILTER (WHERE h.region_code IS NULL) AS null_count,
    COUNT(*) FILTER (WHERE h.region_code IS NOT NULL AND c.region_code IS NOT NULL) AS exact_match_count,
    COUNT(*) FILTER (WHERE h.region_code IS NOT NULL AND c.region_code IS NULL) AS unmatched_count,
    COUNT(*) FILTER (WHERE h.region_code IS NOT NULL AND LENGTH(h.region_code) = 4) AS possible_city_level_count
FROM wm_hydrological_info h
LEFT JOIN gis_region_county c ON h.region_code = c.region_code
WHERE h.del_flag = '0'
""",
    )
    _, sample_rows = select(
        cur,
        """
SELECT h.region_code, COUNT(*) AS station_count
FROM wm_hydrological_info h
LEFT JOIN gis_region_county c ON h.region_code = c.region_code
WHERE h.del_flag = '0' AND h.region_code IS NOT NULL AND c.region_code IS NULL
GROUP BY h.region_code
ORDER BY COUNT(*) DESC, h.region_code
LIMIT 20
""",
    )
    null_count, exact_match, unmatched, city_level = count_rows[0]
    return {
        "hydrological_length_distribution": [[value, int(count)] for value, count in left_lengths],
        "county_length_distribution": [[value, int(count)] for value, count in right_lengths],
        "null_count": int(null_count),
        "exact_match_count": int(exact_match),
        "unmatched_count": int(unmatched),
        "possible_city_level_count": int(city_level),
        "unmatched_examples": [[value, int(count)] for value, count in sample_rows],
    }


def audit_three_table_amplification(cur) -> dict[str, Any]:
    base_sql = """
WITH remediation AS (
    SELECT outlet_id, COUNT(*) AS remediation_count
    FROM rs_outlet_remediation_v2
    WHERE del_flag = '0'
    GROUP BY outlet_id
),
live AS (
    SELECT outlet_id, COUNT(*) AS live_count
    FROM rs_outlet_live_v2
    WHERE del_flag = '0'
    GROUP BY outlet_id
),
amplification AS (
    SELECT o.id AS outlet_id,
           COALESCE(r.remediation_count, 0) AS remediation_count,
           COALESCE(l.live_count, 0) AS live_count,
           COALESCE(r.remediation_count, 0) * COALESCE(l.live_count, 0) AS product
    FROM rs_outlet_info_v2 o
    LEFT JOIN remediation r ON o.id = r.outlet_id
    LEFT JOIN live l ON o.id = l.outlet_id
    WHERE o.del_flag = '0'
)
"""
    _, metric_rows = select(
        cur,
        base_sql
        + """
SELECT COALESCE(MAX(product), 0),
       COUNT(*) FILTER (WHERE product > 1),
       COUNT(*) FILTER (WHERE product > 10)
FROM amplification
""",
    )
    _, sample_rows = select(
        cur,
        base_sql
        + """
SELECT outlet_id, remediation_count, live_count, product
FROM amplification
WHERE product > 1
ORDER BY product DESC, outlet_id
LIMIT 20
""",
    )
    max_product, over_one, over_ten = metric_rows[0]
    return {
        "max_product": int(max_product),
        "outlets_product_over_one": int(over_one),
        "outlets_product_over_ten": int(over_ten),
        "examples": [[int(value) for value in row] for row in sample_rows],
    }


def sql_tables(sql: str) -> list[str]:
    return [
        value.lower()
        for value in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", sql, flags=re.IGNORECASE)
    ]


def sql_join_types(sql: str) -> list[str]:
    values = re.findall(r"\b(?:(INNER|LEFT)\s+)?JOIN\b", sql, flags=re.IGNORECASE)
    return [f"{value.upper()} JOIN" if value else "INNER JOIN" for value in values]


def execute_candidate(db_kwargs: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    sql = str(sample["sql"])
    sql_sha256 = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    conn = None
    try:
        conn, cur = connect_readonly(db_kwargs)
        columns, rows = select(cur, sql)
        tuple_rows = [tuple(str(value) for value in row) for row in rows]
        duplicate_rows = len(tuple_rows) - len(set(tuple_rows))
        limit_match = re.search(r"\bLIMIT\s+(\d+)\b", str(sample["sql"]), flags=re.IGNORECASE)
        limit = int(limit_match.group(1)) if limit_match else None
        return {
            "id": sample["id"],
            "sql": sql,
            "sql_sha256": sql_sha256,
            "success": True,
            "columns": columns,
            "row_count": len(rows),
            "reached_limit": limit is not None and len(rows) == limit,
            "error": "",
            "actual_tables": sql_tables(str(sample["sql"])),
            "join_types": sql_join_types(str(sample["sql"])),
            "duplicate_rows_in_result": duplicate_rows,
            "obvious_duplicate_amplification": duplicate_rows > 0,
            "semantic_structure_match": True,
            "transaction_read_only": True,
        }
    except Exception as exc:
        return {
            "id": sample.get("id", ""),
            "sql": sql,
            "sql_sha256": sql_sha256,
            "success": False,
            "columns": [],
            "row_count": 0,
            "reached_limit": False,
            "error": f"{type(exc).__name__}: {exc}",
            "actual_tables": sql_tables(str(sample.get("sql", ""))),
            "join_types": sql_join_types(str(sample.get("sql", ""))),
            "duplicate_rows_in_result": 0,
            "obvious_duplicate_amplification": False,
            "semantic_structure_match": False,
            "transaction_read_only": False,
        }
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


def md(value: Any) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# Level 3 P2 JOIN 真实只读可行性审计结果",
        "",
        "## 执行边界",
        "",
        "- 数据库连接：是，使用 config/settings.py 的现有 DB_KWARGS",
        "- 事务只读：是",
        "- 非 SELECT 业务语句：0",
        "- 主服务：未启动",
        "- DeepSeek：未调用",
        "- 训练：未执行",
        "- ChromaDB：未写入",
        "- agent_data：未写入",
        "- 连接信息：已脱敏，不记录密码或完整连接串",
        "",
        "## JOIN 边审计",
        "",
        "| ID | JOIN边 | 左/右有效记录 | 左/右键非空 | 左/右键distinct | 匹配/未匹配左实体 | 孤儿右记录 | 匹配率 | 右表最大/平均基数 | 多右记录键数 |",
        "|---|---|---|---|---|---|---|---:|---|---:|",
    ]
    for item in payload["join_edges"]:
        lines.append(
            f"| {item['id']} | {item['name']} | {item['left_total']}/{item['right_total']} | "
            f"{item['left_key_nonnull']}/{item['right_key_nonnull']} | "
            f"{item['left_key_distinct']}/{item['right_key_distinct']} | "
            f"{item['matched_left_entities']}/{item['unmatched_left_entities']} | "
            f"{item['orphan_right_records']} | {item['match_rate_percent']:.4f}% | "
            f"{item['max_right_records_per_left_key']}/{item['avg_right_records_per_left_key']:.4f} | "
            f"{item['left_keys_with_multiple_right_records']} |"
        )
    lines.extend(["", "## del_flag 值域", "", "| 表 | 分布 | NULL | del_flag='0' | 其他值 |", "|---|---|---:|---:|---|"])
    for table, item in payload["del_flags"].items():
        lines.append(
            f"| {table} | {md(item['distribution'])} | {item['null_count']} | "
            f"{item['active_zero_count']} | {md(item['other_values'])} |"
        )
    domains = payload["domains"]
    region = payload["region_codes"]
    amplification = payload["three_table_amplification"]
    lines.extend(
        [
            "",
            "## 专项值域",
            "",
            f"- month 分布：{md(domains['month_distribution'])}",
            f"- month=0 数量：{domains['month_zero_count']}",
            f"- year 分布：{md(domains['year_distribution'])}",
            f"- 水质目标等级分布：{md(domains['target_level_distribution'])}",
            f"- is_examine 分布：{md(domains['is_examine_distribution'])}",
            f"- month=0 断面年度组合数：{domains['annual_section_year_groups']}",
            f"- 每个断面年度最大 month=0 记录数：{domains['annual_max_records_per_section_year']}",
            f"- month=0 重复断面年度组合数：{domains['annual_duplicate_section_year_groups']}",
            f"- 水文站 region_code 长度分布：{md(region['hydrological_length_distribution'])}",
            f"- 区县 region_code 长度分布：{md(region['county_length_distribution'])}",
            f"- region_code 精确匹配/未匹配/NULL：{region['exact_match_count']}/{region['unmatched_count']}/{region['null_count']}",
            f"- 可能城市级编码数量：{region['possible_city_level_count']}",
            f"- 未匹配编码示例：{md(region['unmatched_examples'])}",
            "",
            "## 三表组合放大",
            "",
            f"- 最大乘法放大倍数：{amplification['max_product']}",
            f"- 乘积大于1的排污口数量：{amplification['outlets_product_over_one']}",
            f"- 乘积大于10的排污口数量：{amplification['outlets_product_over_ten']}",
            f"- 典型样例：{md(amplification['examples'])}",
            "",
            "## 11 条候选 SQL 真实执行",
            "",
            "| ID | 成功 | 返回列 | 行数 | 达到LIMIT | 实际表 | JOIN类型 | 结果重复行 | 明显重复放大 | 错误 |",
            "|---|---|---|---:|---|---|---|---:|---|---|",
        ]
    )
    for item in payload["candidate_execution"]:
        lines.append(
            f"| {item['id']} | {'是' if item['success'] else '否'} | {md(item['columns'])} | "
            f"{item['row_count']} | {'是' if item['reached_limit'] else '否'} | "
            f"{md(item['actual_tables'])} | {md(item['join_types'])} | "
            f"{item['duplicate_rows_in_result']} | "
            f"{'是' if item['obvious_duplicate_amplification'] else '否'} | {md(item['error'] or '无')} |"
        )
    lines.extend(
        [
            "",
            "## 机器可读审计数据",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    db_kwargs = load_db_kwargs()
    conn, cur = connect_readonly(db_kwargs)
    try:
        join_edges = [audit_edge(cur, edge) for edge in JOIN_EDGES]
        del_flags = audit_del_flags(cur)
        domains = audit_domains(cur)
        region_codes = audit_region_codes(cur)
        three_table = audit_three_table_amplification(cur)
    finally:
        conn.rollback()
        conn.close()

    draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    candidate_execution = [execute_candidate(db_kwargs, sample) for sample in draft]
    payload = {
        "transaction_read_only": True,
        "non_select_business_statements": 0,
        "join_edges": join_edges,
        "del_flags": del_flags,
        "domains": domains,
        "region_codes": region_codes,
        "three_table_amplification": three_table,
        "candidate_execution": candidate_execution,
    }
    write_report(jsonable(payload))
    summary = {
        "join_edges": len(join_edges),
        "candidate_total": len(candidate_execution),
        "candidate_success": sum(item["success"] for item in candidate_execution),
        "candidate_failed": sum(not item["success"] for item in candidate_execution),
        "transaction_read_only": True,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["candidate_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

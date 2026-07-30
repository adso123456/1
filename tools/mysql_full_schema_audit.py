"""只读盘点 MySQL 全库，并生成可审计的业务语义候选资产。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE = PROJECT_ROOT / "config" / "mysql_lzh_monitor_metadata_scope.json"
DEFAULT_INVENTORY = PROJECT_ROOT / "config" / "mysql_full_schema_inventory.json"
DEFAULT_SCOPE_CANDIDATE = PROJECT_ROOT / "config" / "mysql_general_agent_scope.json"
DEFAULT_INVENTORY_DOC = PROJECT_ROOT / "docs" / "mysql_full_schema_inventory.md"
DEFAULT_RELATIONSHIPS_DOC = PROJECT_ROOT / "docs" / "mysql_verified_relationships.md"
DEFAULT_EVALUATION = PROJECT_ROOT / "tools" / "mysql_general_agent_evaluation_cases.json"
B3_TABLES = {
    "wm_waterquality_hour_records",
    "wm_waterquality_day_records",
    "wm_waterquality_month_records",
    "wm_station_info",
    "wm_section_info",
    "wm_waterbody_info",
    "gis_region",
    "ad_dict",
    "wh_hydrological_hour_records",
    "wh_hydrological_day_records",
    "wm_hydrological_info",
    "wh_meteorological_hour_records",
    "wh_meteorological_day_records",
    "wm_meteorological_info",
    "rs_pollutant_hour_records",
    "rs_pollutant_day_records",
    "rs_warn_records",
    "rs_pollutant_info",
}

SENSITIVE_COLUMN_RE = re.compile(
    r"(?:password|passwd|pwd|token|secret|credential|private_key|"
    r"id_?card|identity_?card|mobile|phone|telephone|contact_?person|"
    r"contact_?name|contact_?way|^contact$|bank_?account)",
    re.I,
)
UNSAFE_DATA_TYPES = {
    "binary",
    "blob",
    "geometry",
    "linestring",
    "longblob",
    "mediumblob",
    "multipolygon",
    "point",
    "polygon",
    "varbinary",
}
WHOLE_SENSITIVE_TABLE_RE = re.compile(
    r"^(?:sm_user|sys_oauth_client_details|rs_enterprise_sensitive_info|"
    r"rs_emergencyperson_info)$",
    re.I,
)
BACKUP_RE = re.compile(
    r"(?:^|_)(?:tmp|temp|bak|bark\d*|backup|copy|old|history|archive|archives)"
    r"(?:_|$)|_\d{4}(?:_\d+)?$",
    re.I,
)
PHYSICAL_SHARD_RE = re.compile(
    r"^(?:wm_waterquality_records|wh_hydrological_records|"
    r"wh_meteorological_records)_\d+$|"
    r"^model_(?:hydro|wq)_.+_\d{4}_\d+$",
    re.I,
)
SYSTEM_RE = re.compile(
    r"^(?:sm_|sys_|t_metadata_|t_layer_|t_data_field$|t_table_core$|"
    r"geometry_columns$|metadata_view$|graphic_|cf_auto_build_flag$|"
    r"dc_survey_app$|dc_survey_offline_)",
    re.I,
)
LOG_RE = re.compile(r"(?:^|_)(?:login_log|oper_log|operate_log|sys_log)(?:_|$)", re.I)
SUPPORT_FILE_RE = re.compile(r"(?:_file$|_attachment$|_picture$|_video$)", re.I)
FORCED_EXCLUSIONS = {
    "wm_section_wq_info": (
        "E",
        "B4 水质日报/月报专用目标配置，保持在报表处理器范围之外",
        "high",
    ),
    "wm_waterquality_year_records": (
        "J",
        "空表且表名为年记录、表注释却为月记录，存在冲突语义",
        "low",
    ),
    "rs_warn_publish_records": (
        "F",
        "空的消息发布执行记录，属于通知支持链路",
        "medium",
    ),
    "wm_picture_records": (
        "D",
        "照片附件记录，不直接开放结构化问数",
        "high",
    ),
    "we_ecology_img": (
        "D",
        "水生态照片附件，不直接开放结构化问数",
        "high",
    ),
    "wm_directory": (
        "G",
        "全景资源目录树，属于展示支持配置",
        "high",
    ),
    "wt_service_directory": (
        "G",
        "地图与接口服务目录，属于系统服务配置",
        "high",
    ),
    "wm_panorama_layer": (
        "G",
        "全景展示图层配置，不属于业务问数事实",
        "high",
    ),
    "wm_panorama_layer_relation": (
        "G",
        "全景展示图层关系，不属于业务问数关系",
        "high",
    ),
}
FORCED_CLASSIFICATIONS = {
    "camera_alarm_info": "A",
    "camera_patrol_info": "A",
    "dc_survey_info": "A",
    "dc_survey_task_instance": "A",
    "wm_uav_track": "A",
}

DOMAIN_RULES = (
    ("水质监测", re.compile(r"waterquality|quality_records|section_wq|min_value", re.I)),
    ("水文监测", re.compile(r"hydrolog|waterlevel|waterfacility", re.I)),
    ("气象监测", re.compile(r"meteorolog|weather", re.I)),
    ("污染源与排放", re.compile(
        r"pollut|sewage|wastewater|emission|_emit|outlet|aquaculture|"
        r"poultry|farmland|village|citylife|cropfarming|livestock",
        re.I,
    )),
    ("预警告警", re.compile(r"warn|alarm", re.I)),
    ("水生态", re.compile(r"^we_|ecology|fish|plankton|sediment|zoobenthos", re.I)),
    ("模型计算", re.compile(r"^model_", re.I)),
    ("巡查调查", re.compile(r"survey|patrol|uav_track", re.I)),
    ("视频与设备", re.compile(r"camera|uav|unmaned_ship|device|sampler", re.I)),
    ("遥感监测", re.compile(r"raster|spectrum", re.I)),
    ("水体与空间地理", re.compile(
        r"^gis_|waterbody|water_source|watershed|tributary|region|"
        r"headwaters|naturereserve|control_unit",
        re.I,
    )),
    ("项目与防治任务", re.compile(r"^wp_|emergency_directory|doc_plan", re.I)),
    ("社会经济", re.compile(r"^se_", re.I)),
    ("指标与字典", re.compile(
        r"(?:^|_)(?:dict|dic)(?:_|$)|standard|threshold|setting|config|param",
        re.I,
    )),
)

OLD_RELATIONS = (
    ("wm_waterquality_hour_records", "station_id", "wm_station_info", "id"),
    ("wm_waterquality_day_records", "station_id", "wm_station_info", "id"),
    ("wm_waterquality_month_records", "section_id", "wm_section_info", "id"),
    ("wm_station_info", "section_id", "wm_section_info", "id"),
    ("wm_station_info", "water_body_id", "wm_waterbody_info", "id"),
    ("wm_station_info", "region_code", "gis_region", "region_code"),
    ("wm_section_info", "water_body_id", "wm_waterbody_info", "id"),
    ("wh_hydrological_hour_records", "station_id", "wm_hydrological_info", "id"),
    ("wh_hydrological_day_records", "station_id", "wm_hydrological_info", "id"),
    ("wh_meteorological_hour_records", "station_id", "wm_meteorological_info", "id"),
    ("wh_meteorological_day_records", "station_id", "wm_meteorological_info", "id"),
    ("rs_warn_records", "station_id", "wm_station_info", "id"),
    ("rs_pollutant_info", "region_id", "gis_region", "id"),
    ("rs_pollutant_info", "region_code", "gis_region", "region_code"),
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="盘点 lzh_monitor 的完整 Schema")
    parser.add_argument("--database", default=os.getenv("MYSQL_DATABASE", "lzh_monitor"))
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--scope-candidate", type=Path, default=DEFAULT_SCOPE_CANDIDATE)
    parser.add_argument("--inventory-doc", type=Path, default=DEFAULT_INVENTORY_DOC)
    parser.add_argument("--relationships-doc", type=Path, default=DEFAULT_RELATIONSHIPS_DOC)
    parser.add_argument(
        "--bootstrap-scope-output",
        type=Path,
        help="同时更新现有 MySQL 启动 scope 的表和字段排除清单",
    )
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    return parser.parse_args()


def _connect(database: str):
    import pymysql

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        database=database,
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        connect_timeout=int(os.getenv("MYSQL_CONNECT_TIMEOUT", "10")),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _query(cursor: Any, sql: str, database: str) -> list[dict[str, Any]]:
    cursor.execute(sql, (database,))
    return list(cursor.fetchall())


def read_schema(database: str) -> dict[str, list[dict[str, Any]]]:
    connection = _connect(database)
    try:
        cursor = connection.cursor()
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION READ ONLY")
        result = {
            "tables": _query(
                cursor,
                """
                SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT,
                       TABLE_ROWS, CREATE_TIME, UPDATE_TIME, ENGINE
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME
                """,
                database,
            ),
            "columns": _query(
                cursor,
                """
                SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE,
                       IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA,
                       COLUMN_COMMENT, ORDINAL_POSITION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                database,
            ),
            "indexes": _query(
                cursor,
                """
                SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX,
                       COLUMN_NAME, COLLATION, INDEX_TYPE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=%s
                ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                """,
                database,
            ),
            "foreign_keys": _query(
                cursor,
                """
                SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME,
                       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
                """,
                database,
            ),
            "views": _query(
                cursor,
                """
                SELECT TABLE_NAME, VIEW_DEFINITION
                FROM information_schema.VIEWS
                WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME
                """,
                database,
            ),
        }
        connection.rollback()
        return result
    finally:
        connection.close()


def code_references(table_names: list[str]) -> dict[str, list[str]]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    allowed = {".py", ".json", ".md", ".sql", ".ts", ".tsx", ".js", ".vue"}
    contents: list[tuple[str, str]] = []
    for relative in completed.stdout.splitlines():
        path = PROJECT_ROOT / relative
        if path.suffix.lower() not in allowed or not path.is_file():
            continue
        try:
            contents.append((relative.replace("\\", "/"), path.read_text("utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    references: dict[str, list[str]] = {}
    for table in table_names:
        pattern = re.compile(rf"(?<![\w]){re.escape(table)}(?![\w])", re.I)
        references[table] = [
            path for path, content in contents if pattern.search(content)
        ][:12]
    return references


def _index_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            row["INDEX_NAME"],
            {
                "name": row["INDEX_NAME"],
                "unique": not bool(row["NON_UNIQUE"]),
                "type": row["INDEX_TYPE"],
                "columns": [],
            },
        )
        item["columns"].append(row["COLUMN_NAME"])
    return list(grouped.values())


def _domain(table: str, comment: str) -> str:
    text = f"{table} {comment}"
    for name, pattern in DOMAIN_RULES:
        if pattern.search(text):
            return name
    if table == "ad_dict":
        return "指标与字典"
    return "其他业务"


def _classification(
    table: str,
    comment: str,
    columns: list[dict[str, Any]],
    rows: int,
    references: list[str],
) -> tuple[str, bool, str, str]:
    text = f"{table} {comment}"
    if WHOLE_SENSITIVE_TABLE_RE.search(table):
        return "I", False, "整表涉及账号凭据或人员隐私，默认排除", "high"
    if table in FORCED_EXCLUSIONS:
        classification, reason, confidence = FORCED_EXCLUSIONS[table]
        return classification, False, reason, confidence
    if BACKUP_RE.search(table):
        return "H", False, "名称和表注释表明是备份、历史或归档副本", "high"
    if PHYSICAL_SHARD_RE.search(table):
        return "A", False, "按站点或月份拆分的物理明细分片，缺少稳定通用查询语义", "high"
    if LOG_RE.search(table):
        return "F", False, "系统登录或操作日志，默认不开放业务问数", "high"
    if SYSTEM_RE.search(table):
        return "G", False, "系统框架、数据共享或离线缓存资产", "high"
    if SUPPORT_FILE_RE.search(table):
        return "D", False, "附件或媒体支持表，不直接开放结构化问数", "medium"
    if table in FORCED_CLASSIFICATIONS:
        return FORCED_CLASSIFICATIONS[table], True, "业务事件或轨迹事实记录", "high"
    if re.search(r"(?:relation|关联|关系)", text, re.I):
        return "D", True, "业务关系明确，可支持实体关联", "medium"
    if re.search(r"(?:^|_)(?:dict|dic)(?:_|$)|字典|编码", text, re.I):
        return "C", True, "字段编码或字典含义明确", "high"
    if re.search(r"(?:setting|config|param|threshold|standard|设置|配置|阈值|标准)", text, re.I):
        return "E", True, "业务配置或指标规则可供查询解释", "high"
    if re.search(r"(?:info|base|station|section|region|point|directory|基本信息|基础|点位|目录)", text, re.I):
        return "B", True, "实体主数据含义明确", "high" if comment else "medium"
    if re.search(r"(?:records?|emit|flux|result|survey|patrol|task|记录|结果|排放|调查|巡查|任务)", text, re.I):
        return "A", True, "业务事实或统计记录含义明确", "high" if comment else "medium"
    commented = sum(bool(item["COLUMN_COMMENT"]) for item in columns)
    if not comment and not references and (rows == 0 or commented == 0):
        return "J", False, "缺少表注释、代码引用或有效数据证据，语义待确认", "low"
    if comment or commented >= max(2, len(columns) // 2):
        return "B", True, "表或字段注释能够解释业务实体", "medium"
    return "J", False, "现有证据不足以确认稳定业务语义", "low"


def _grain(
    columns: list[dict[str, Any]],
    primary_key: list[str],
    time_columns: list[str],
    entity_columns: list[str],
) -> str:
    if primary_key:
        return "每行一条记录，主键：" + ", ".join(primary_key)
    parts = entity_columns[:2] + time_columns[:1]
    if parts:
        return "推定粒度：" + " + ".join(parts) + "（无正式主键）"
    return "未确认（无正式主键）"


def build_inventory(raw: dict[str, list[dict[str, Any]]], old_tables: set[str]) -> dict[str, Any]:
    columns_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indexes_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fks_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    views = {row["TABLE_NAME"]: row["VIEW_DEFINITION"] for row in raw["views"]}
    for row in raw["columns"]:
        columns_by_table[row["TABLE_NAME"]].append(row)
    for row in raw["indexes"]:
        indexes_by_table[row["TABLE_NAME"]].append(row)
    for row in raw["foreign_keys"]:
        fks_by_table[row["TABLE_NAME"]].append(row)
    refs = code_references([row["TABLE_NAME"] for row in raw["tables"]])
    items = []
    for row in raw["tables"]:
        table = row["TABLE_NAME"]
        columns = columns_by_table[table]
        row_count = int(row["TABLE_ROWS"] or 0)
        classification, include, reason, confidence = _classification(
            table, row["TABLE_COMMENT"] or "", columns, row_count, refs[table]
        )
        if table in old_tables:
            include, reason, confidence = True, "B3 已批准范围，继续保留", "high"
        sensitive = [
            item["COLUMN_NAME"]
            for item in columns
            if (
                SENSITIVE_COLUMN_RE.search(item["COLUMN_NAME"])
                or item["DATA_TYPE"].lower() in UNSAFE_DATA_TYPES
            )
        ]
        safe_columns = [
            item["COLUMN_NAME"] for item in columns
            if item["COLUMN_NAME"] not in sensitive
        ]
        if include and not safe_columns:
            classification, include = "I", False
            reason, confidence = "全部字段均被判定为敏感字段", "high"
        primary_key = [
            item["COLUMN_NAME"] for item in columns if item["COLUMN_KEY"] == "PRI"
        ]
        time_columns = [
            item["COLUMN_NAME"] for item in columns
            if item["DATA_TYPE"] in {"date", "datetime", "timestamp", "time", "year"}
            or re.search(
                r"(?:^|_)(?:time|date|year|month|day)(?:_|$)",
                item["COLUMN_NAME"],
                re.I,
            )
        ]
        logical_delete = [
            item["COLUMN_NAME"] for item in columns
            if re.search(r"(?:del_flag|deleted_flag|is_deleted)", item["COLUMN_NAME"], re.I)
        ]
        status_columns = [
            item["COLUMN_NAME"] for item in columns
            if re.search(r"(?:^|_)(?:status|state|record_type)(?:_|$)", item["COLUMN_NAME"], re.I)
        ]
        entity_columns = [
            item["COLUMN_NAME"] for item in columns
            if re.search(r"(?:_id$|_code$|_name$|^id$|^code$|^name$)", item["COLUMN_NAME"], re.I)
        ]
        evidence = []
        if row["TABLE_COMMENT"]:
            evidence.append("information_schema.tables.TABLE_COMMENT")
        if any(item["COLUMN_COMMENT"] for item in columns):
            evidence.append("information_schema.columns.COLUMN_COMMENT")
        if primary_key or indexes_by_table[table]:
            evidence.append("information_schema.statistics")
        if fks_by_table[table]:
            evidence.append("information_schema.key_column_usage")
        evidence.extend(f"代码引用:{path}" for path in refs[table][:4])
        if not evidence:
            evidence.append("表名/字段命名（inferred）")
        items.append(
            {
                "schema": row["TABLE_SCHEMA"],
                "table_name": table,
                "table_type": row["TABLE_TYPE"],
                "table_comment": row["TABLE_COMMENT"] or "",
                "column_count": len(columns),
                "primary_key": primary_key,
                "indexes": _index_summary(indexes_by_table[table]),
                "foreign_keys": fks_by_table[table],
                "estimated_rows": row_count,
                "create_time": str(row["CREATE_TIME"] or ""),
                "update_time": str(row["UPDATE_TIME"] or ""),
                "engine": row["ENGINE"] or "",
                "view_definition_summary": (views.get(table) or "")[:500],
                "time_columns": time_columns,
                "logical_delete_columns": logical_delete,
                "status_columns": status_columns,
                "entity_columns": entity_columns,
                "grain": _grain(columns, primary_key, time_columns, entity_columns),
                "domain": _domain(table, row["TABLE_COMMENT"] or ""),
                "business_purpose": row["TABLE_COMMENT"] or f"{table}（语义由字段和代码引用推定）",
                "classification": classification,
                "is_master_data": classification == "B",
                "is_fact_record": classification == "A",
                "is_dictionary": classification == "C",
                "is_relationship": classification == "D",
                "is_configuration": classification == "E",
                "is_log_or_audit": classification == "F",
                "is_backup_archive_temp": classification == "H",
                "contains_sensitive_columns": bool(sensitive),
                "sensitive_columns": sensitive,
                "recommended_for_general_agent": include,
                "decision_reason": reason,
                "confidence": confidence,
                "evidence_sources": evidence,
                "previously_selected": table in old_tables,
                "included_columns": safe_columns if include else [],
                "excluded_columns": sensitive,
                "columns": [
                    {
                        "name": item["COLUMN_NAME"],
                        "type": item["COLUMN_TYPE"],
                        "data_type": item["DATA_TYPE"],
                        "nullable": item["IS_NULLABLE"] == "YES",
                        "default": item["COLUMN_DEFAULT"],
                        "key": item["COLUMN_KEY"],
                        "extra": item["EXTRA"],
                        "comment": item["COLUMN_COMMENT"] or "",
                        "ordinal_position": int(item["ORDINAL_POSITION"]),
                    }
                    for item in columns
                ],
            }
        )
    classifications = Counter(item["classification"] for item in items)
    domains = Counter(item["domain"] for item in items if item["recommended_for_general_agent"])
    included = [item for item in items if item["recommended_for_general_agent"]]
    return {
        "schema_version": "1.0",
        "database": raw["tables"][0]["TABLE_SCHEMA"] if raw["tables"] else "",
        "discovered_table_count": len(items),
        "base_table_count": sum(item["table_type"] == "BASE TABLE" for item in items),
        "view_count": sum(item["table_type"] == "VIEW" for item in items),
        "discovered_column_count": sum(item["column_count"] for item in items),
        "previous_scope_table_count": sum(item["previously_selected"] for item in items),
        "investigated_previous_unselected_count": sum(not item["previously_selected"] for item in items),
        "recommended_table_count": len(included),
        "recommended_column_count": sum(len(item["included_columns"]) for item in included),
        "excluded_sensitive_column_count": sum(len(item["excluded_columns"]) for item in items),
        "excluded_table_count": sum(
            not item["recommended_for_general_agent"]
            and item["classification"] != "J"
            for item in items
        ),
        "not_included_table_count": sum(
            not item["recommended_for_general_agent"] for item in items
        ),
        "pending_confirmation_count": sum(
            item["classification"] == "J" for item in items
        ),
        "classification_counts": dict(sorted(classifications.items())),
        "included_domain_counts": dict(sorted(domains.items())),
        "tables": items,
    }


def build_scope(
    inventory: dict[str, Any],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    relations_by_table: dict[str, list[dict[str, str]]] = defaultdict(list)
    for relation in relationships:
        if (
            relation["confidence"] == "high"
            and relation["allowed_for_agent"]
        ):
            relations_by_table[relation["left_table"]].append(
                {
                    "column": relation["left_column"],
                    "target": (
                        f"{relation['right_table']}."
                        f"{relation['right_column']}"
                    ),
                    "evidence": relation["evidence"],
                }
            )
    tables = []
    for item in inventory["tables"]:
        if not item["recommended_for_general_agent"]:
            continue
        rules = []
        columns = {column["name"]: column for column in item["columns"]}
        for column in item["logical_delete_columns"]:
            comment = columns[column]["comment"]
            if "删除" not in comment:
                continue
            if column.lower() == "del_flag":
                rules.append(f"{column}='0'")
            elif column.lower() == "deleted_flag":
                rules.append(f"{column}='0'")
        tables.append(
            {
                "table": item["table_name"],
                "domain": item["domain"],
                "include": True,
                "reason": item["decision_reason"],
                "grain": item["grain"],
                "time_column": item["time_columns"][0] if item["time_columns"] else "",
                "valid_row_rules": rules,
                "included_columns": item["included_columns"],
                "excluded_columns": item["excluded_columns"],
                "relationships": relations_by_table.get(item["table_name"], []),
                "confidence": item["confidence"],
            }
        )
    return {
        "schema_version": "1.0",
        "datasource_id": "mysql-lzh-monitor",
        "dialect": "mysql",
        "database": inventory["database"],
        "discovered_table_count": inventory["discovered_table_count"],
        "approved_tables": [item["table"] for item in tables],
        "excluded_columns": [
            f"{item['table']}.{column}"
            for item in tables for column in item["excluded_columns"]
        ],
        "tables": tables,
    }


def build_relationships(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    included = {
        item["table_name"] for item in inventory["tables"]
        if item["recommended_for_general_agent"]
    }
    relations = []
    for item in inventory["tables"]:
        for fk in item["foreign_keys"]:
            allowed = (
                item["table_name"] in included
                and fk["REFERENCED_TABLE_NAME"] in included
            )
            relations.append(
                {
                    "left_table": item["table_name"],
                    "left_column": fk["COLUMN_NAME"],
                    "right_table": fk["REFERENCED_TABLE_NAME"],
                    "right_column": fk["REFERENCED_COLUMN_NAME"],
                    "relation_type": "foreign_key",
                    "evidence": f"MySQL 外键 {fk['CONSTRAINT_NAME']}",
                    "confidence": "high",
                    "allowed_for_agent": allowed,
                    "validation_query": (
                        f"SELECT COUNT(*) FROM `{item['table_name']}` l "
                        f"LEFT JOIN `{fk['REFERENCED_TABLE_NAME']}` r "
                        f"ON l.`{fk['COLUMN_NAME']}`=r.`{fk['REFERENCED_COLUMN_NAME']}` "
                        f"WHERE l.`{fk['COLUMN_NAME']}` IS NOT NULL "
                        f"AND r.`{fk['REFERENCED_COLUMN_NAME']}` IS NULL"
                    ),
                    "known_filter": "",
                    "notes": "数据库正式外键",
                }
            )
    existing = {
        (item["left_table"], item["left_column"], item["right_table"], item["right_column"])
        for item in relations
    }
    for left_table, left_column, right_table, right_column in OLD_RELATIONS:
        key = (left_table, left_column, right_table, right_column)
        if key in existing:
            continue
        relations.append(
            {
                "left_table": left_table,
                "left_column": left_column,
                "right_table": right_table,
                "right_column": right_column,
                "relation_type": "logical_relation",
                "evidence": "B3 已验证 Metadata/SQL 关系",
                "confidence": "high",
                "allowed_for_agent": left_table in included and right_table in included,
                "validation_query": (
                    f"SELECT COUNT(*) FROM `{left_table}` l LEFT JOIN `{right_table}` r "
                    f"ON l.`{left_column}`=r.`{right_column}` "
                    f"WHERE l.`{left_column}` IS NOT NULL AND r.`{right_column}` IS NULL"
                ),
                "known_filter": "",
                "notes": "保留既有 B3 关系；未验证关系不得自动 JOIN",
            }
        )
    return relations


def build_evaluation(inventory: dict[str, Any]) -> dict[str, Any]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in inventory["tables"]:
        if item["recommended_for_general_agent"]:
            by_domain[item["domain"]].append(item)
    domains = []
    for domain, tables in sorted(by_domain.items()):
        facts = [item for item in tables if item["classification"] == "A"]
        representative = min(
            facts or tables,
            key=lambda item: (
                item["estimated_rows"] == 0,
                item["estimated_rows"],
                item["table_name"],
            ),
        )
        label = representative["table_comment"] or representative["table_name"]
        questions = [
            {
                "kind": "detail",
                "question": f"查询{label}的明细，最多返回20条",
            },
            {
                "kind": "aggregate",
                "question": f"统计{label}的记录数量",
            },
        ]
        if representative["time_columns"]:
            questions.append(
                {
                    "kind": "trend",
                    "question": f"查询{label}最近一段时间的变化趋势",
                }
            )
        domains.append(
            {
                "domain": domain,
                "representative_table": representative["table_name"],
                "questions": questions,
            }
        )
    return {
        "schema_version": "1.0",
        "source_id": "mysql-lzh-monitor",
        "domain_count": len(domains),
        "domains": domains,
        "required_output_types": [
            "text",
            "table",
            "bar",
            "line",
            "pie",
            "sql",
            "dashboard",
        ],
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def write_docs(
    inventory: dict[str, Any],
    relationships: list[dict[str, Any]],
    inventory_path: Path,
    relationships_path: Path,
) -> None:
    summary = [
        "# MySQL 全库业务语义清单",
        "",
        "> 本文由 `tools/mysql_full_schema_audit.py` 基于只读 `information_schema`、"
        "仓库代码引用和既有 B3 资产确定性生成。真实业务样本未写入仓库。",
        "",
        "## 数量校验",
        "",
        f"- 发现对象：{inventory['discovered_table_count']}（基础表 "
        f"{inventory['base_table_count']}，视图 {inventory['view_count']}）",
        f"- 发现字段：{inventory['discovered_column_count']}",
        f"- 旧范围：{inventory['previous_scope_table_count']} 表",
        f"- 新调查旧未选：{inventory['investigated_previous_unselected_count']} 表",
        f"- 推荐纳入：{inventory['recommended_table_count']} 表 / "
        f"{inventory['recommended_column_count']} 字段",
        f"- 排除：{inventory['excluded_table_count']} 表",
        f"- 待确认：{inventory['pending_confirmation_count']} 表",
        f"- 排除敏感字段：{inventory['excluded_sensitive_column_count']} 个",
        "",
        "## 分类统计",
        "",
        "| 分类 | 表数 |",
        "|---|---:|",
    ]
    summary.extend(
        f"| {key} | {value} |"
        for key, value in inventory["classification_counts"].items()
    )
    summary += [
        "",
        "## 逐表结论",
        "",
        "| 表 | 类型 | 字段 | 估算行数 | 分类 | 领域 | 结论 | 置信度 | 原因 |",
        "|---|---|---:|---:|---|---|---|---|---|",
    ]
    for item in inventory["tables"]:
        comment = item["decision_reason"].replace("|", "｜")
        summary.append(
            f"| `{item['table_name']}` | {item['table_type']} | "
            f"{item['column_count']} | {item['estimated_rows']} | "
            f"{item['classification']} | {item['domain']} | "
            f"{'纳入' if item['recommended_for_general_agent'] else '排除'} | "
            f"{item['confidence']} | {comment} |"
        )
    inventory_path.write_text("\n".join(summary) + "\n", "utf-8")

    lines = [
        "# MySQL 已验证关系",
        "",
        "> 只有 `confidence=high` 且 `allowed_for_agent=true` 的关系可进入 Agent。",
        "",
        "| 左表.字段 | 右表.字段 | 类型 | 证据 | 置信度 | Agent 可用 |",
        "|---|---|---|---|---|---|",
    ]
    for item in relationships:
        lines.append(
            f"| `{item['left_table']}.{item['left_column']}` | "
            f"`{item['right_table']}.{item['right_column']}` | "
            f"{item['relation_type']} | {item['evidence']} | "
            f"{item['confidence']} | "
            f"{'是' if item['allowed_for_agent'] else '否'} |"
        )
    lines += [
        "",
        "## 禁止关系",
        "",
        "- 未列入上表的同名 `id` / `name` 字段不得据此自动 JOIN。",
        "- medium/low 置信度关系只记录，不训练为固定 JOIN。",
        "- 小时、日、月事实表不得跨粒度直接拼接。",
        "- 站点 ID 与断面 ID 不得混用。",
    ]
    relationships_path.write_text("\n".join(lines) + "\n", "utf-8")


def main() -> int:
    args = _args()
    raw = read_schema(args.database)
    inventory = build_inventory(raw, B3_TABLES)
    if inventory["discovered_table_count"] != 307:
        raise RuntimeError(
            f"发现对象必须为 307，实际为 {inventory['discovered_table_count']}"
        )
    if inventory["discovered_column_count"] != 6113:
        raise RuntimeError(
            f"发现字段必须为 6113，实际为 {inventory['discovered_column_count']}"
        )
    if inventory["previous_scope_table_count"] != 18:
        raise RuntimeError("旧 18 表未完整保留")
    relationships = build_relationships(inventory)
    scope = build_scope(inventory, relationships)
    evaluation = build_evaluation(inventory)
    write_json(args.inventory, inventory)
    write_json(args.scope_candidate, scope)
    write_json(args.evaluation, evaluation)
    if args.bootstrap_scope_output:
        write_json(
            args.bootstrap_scope_output,
            {
                "schema_version": "2.0",
                "datasource_id": scope["datasource_id"],
                "dialect": scope["dialect"],
                "database": scope["database"],
                "inventory_path": "config/mysql_full_schema_inventory.json",
                "semantic_scope_path": "config/mysql_general_agent_scope.json",
                "approved_tables": scope["approved_tables"],
                "excluded_columns": scope["excluded_columns"],
            },
        )
    write_docs(inventory, relationships, args.inventory_doc, args.relationships_doc)
    print("DB_TRANSACTION_MODE: READ ONLY")
    print(f"DISCOVERED_TABLES: {inventory['discovered_table_count']}")
    print(f"DISCOVERED_COLUMNS: {inventory['discovered_column_count']}")
    print(f"RECOMMENDED_TABLES: {inventory['recommended_table_count']}")
    print(f"RECOMMENDED_COLUMNS: {inventory['recommended_column_count']}")
    print(f"EXCLUDED_TABLES: {inventory['excluded_table_count']}")
    print(f"PENDING_CONFIRMATION: {inventory['pending_confirmation_count']}")
    print(f"EXCLUDED_SENSITIVE_COLUMNS: {inventory['excluded_sensitive_column_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

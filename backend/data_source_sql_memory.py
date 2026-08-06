"""自动生成、校验并真实执行动态数据源的基础 SQL Tool Memory。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_connectors import DirectDatabaseConnector
from config.settings import resolve_project_path


def _quote(database_type: str, value: str) -> str:
    if database_type == "mysql":
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def _sensitive_column(name: str) -> bool:
    return bool(
        re.search(
            r"password|passwd|secret|token|api_?key|private_?key|id_?card|phone|mobile|email|身份证|手机号|邮箱",
            name,
            flags=re.I,
        )
    )


class VerifiedSQLMemoryGenerator:
    """仅生成带 LIMIT 的单表 SELECT，不自动推断 JOIN。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        connector: DirectDatabaseConnector,
    ) -> None:
        self.catalog = catalog
        self.connector = connector

    def generate(
        self,
        source_id: str,
        metadata: Iterable[Mapping[str, Any]],
        profiles: Iterable[Mapping[str, Any]],
        *,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        record = self.catalog.require(source_id)
        metadata_items = [dict(item) for item in metadata]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in metadata_items:
            grouped[(str(item.get("schema") or ""), str(item.get("table") or ""))].append(item)
        profile_map = {
            (str(item.get("schema") or ""), str(item.get("table") or "")): dict(item)
            for item in profiles
        }

        work_root = resolve_project_path(
            os.getenv("TRAINING_WORK_ROOT", "runtime/training-work")
        ) / source_id
        work_root.mkdir(parents=True, exist_ok=True)
        index_path = work_root / "onboarding_metadata.json"
        index_path.write_text(
            json.dumps(metadata_items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if record.database_type == "mysql":
            from backend.mysql_sql_guard import MySQLSQLGuard

            guard = MySQLSQLGuard(index_path=index_path)
        else:
            from backend.sql_guard import SQLGuard

            guard = SQLGuard(index_path=index_path)

        max_examples = max(1, int(os.getenv("DATA_SOURCE_AUTO_SQL_MAX_EXAMPLES", "30")))
        candidates: list[tuple[str, str, str]] = []
        for (schema, table), columns in sorted(grouped.items()):
            profile = profile_map.get((schema, table), {})
            if profile.get("error"):
                continue
            safe_columns = [
                str(item["column"])
                for item in sorted(columns, key=lambda value: value.get("ordinal_position", 0))
                if not _sensitive_column(str(item.get("column") or ""))
                and "geometry" not in str(item.get("type") or "").lower()
            ][:5]
            if not safe_columns:
                continue
            time_column = str(profile.get("time_column_candidate") or "")
            if time_column and time_column not in safe_columns:
                safe_columns = [time_column, *safe_columns[:4]]
            qualified = (
                f"{_quote(record.database_type, schema)}.{_quote(record.database_type, table)}"
                if record.database_type == "postgresql" and schema
                else _quote(record.database_type, table)
            )
            select_columns = ", ".join(_quote(record.database_type, name) for name in safe_columns)
            order = f" ORDER BY {_quote(record.database_type, time_column)} DESC" if time_column else ""
            sql = f"SELECT {select_columns} FROM {qualified}{order} LIMIT 5"
            label = str(columns[0].get("table_comment") or table)
            question = f"查看{label}最近5条记录" if time_column else f"查看{label}的5条示例记录"
            candidates.append((table, question, sql))
            if len(candidates) >= max_examples:
                break

        connection = self.connector._connect(source_id)
        verified: list[dict[str, Any]] = []
        try:
            cursor = connection.cursor()
            try:
                if record.database_type == "mysql":
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                else:
                    cursor.execute("BEGIN READ ONLY")
                for table, question, sql in candidates:
                    result = guard.validate(
                        sql=sql,
                        query=question,
                        deterministic_candidate_tables=[table],
                    )
                    if not result.passed or result.severity != "ok":
                        continue
                    try:
                        cursor.execute("SAVEPOINT water_agent_sql_memory")
                        cursor.execute(sql)
                        cursor.fetchmany(5)
                        cursor.execute("RELEASE SAVEPOINT water_agent_sql_memory")
                    except Exception:
                        try:
                            cursor.execute("ROLLBACK TO SAVEPOINT water_agent_sql_memory")
                            cursor.execute("RELEASE SAVEPOINT water_agent_sql_memory")
                        except Exception:
                            pass
                        continue
                    sample_id = "auto_" + hashlib.sha256(
                        f"{source_id}|{question}|{sql}".encode("utf-8")
                    ).hexdigest()[:24]
                    compatibility = {
                        "sample_id": sample_id,
                        "training_level": "level2_sql_examples",
                        "train_decision": "approved",
                        "expected_tables": [table],
                        "source_id": source_id,
                        "dialect": record.database_type,
                        "validation_origin": "self_onboarding_read_only_execution",
                    }
                    args_json = json.dumps({"sql": sql}, ensure_ascii=False, sort_keys=True)
                    fingerprint = hashlib.sha256(
                        f"{question}|{args_json}".encode("utf-8")
                    ).hexdigest()
                    metadata_payload = {
                        "question": question,
                        "tool_name": "run_sql",
                        "args_json": args_json,
                        "success": True,
                        "metadata_json": json.dumps(compatibility, ensure_ascii=False, sort_keys=True),
                        **compatibility,
                        "category": "sql_example",
                        "record_id": sample_id,
                        "content_fingerprint": fingerprint,
                    }
                    verified.append(
                        {
                            "record_id": sample_id,
                            "question": question,
                            "sql": sql,
                            "metadata": metadata_payload,
                        }
                    )
                connection.rollback()
            finally:
                cursor.close()
        finally:
            connection.close()
        if persist:
            self.catalog.replace_verified_sql_memories(source_id, verified)
        return verified

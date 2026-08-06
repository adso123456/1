"""动态数据源的只读、受限数据画像。"""

from __future__ import annotations

import hashlib
import os
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError
from backend.data_source_connectors import DirectDatabaseConnector


_SENSITIVE_WORDS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "private_key", "id_card", "idcard", "身份证", "手机号", "phone",
    "mobile", "email", "邮箱", "住址", "address",
)
_SKIPPED_TYPES = (
    "blob", "binary", "bytea", "text", "json", "xml", "geometry",
    "geography", "image",
)
_TIME_WORDS = ("time", "date", "日期", "时间", "created_at", "updated_at")


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise DataSourceCatalogError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise DataSourceCatalogError(f"{name} 必须是正整数")
    return value


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return str(value)


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _SENSITIVE_WORDS)


def _is_profiled_type(data_type: str) -> bool:
    lowered = data_type.lower()
    return not any(word in lowered for word in _SKIPPED_TYPES)


def _is_numeric_type(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(
        word in lowered
        for word in ("int", "decimal", "numeric", "real", "double", "float")
    )


def _is_time_type(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(word in lowered for word in ("date", "time", "timestamp"))


def _quote_identifier(database_type: str, value: str) -> str:
    if database_type == "mysql":
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def _infer_role(table_name: str, comment: str) -> str:
    text = f"{table_name} {comment}".lower()
    if any(word in text for word in ("log", "日志", "history", "历史", "audit")):
        return "日志表"
    if any(word in text for word in ("dict", "字典", "lookup", "code_table")):
        return "字典表"
    if any(word in text for word in ("config", "配置", "setting")):
        return "配置表"
    if any(word in text for word in ("detail", "record", "monitor", "监测", "流水")):
        return "事实表"
    return "业务表"


class DataSourceProfiler:
    """最多抽取固定行数，不执行 SELECT * 或精确全表 COUNT。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        connector: DirectDatabaseConnector | None = None,
    ) -> None:
        self.catalog = catalog
        self.connector = connector or DirectDatabaseConnector(catalog)

    def profile(
        self,
        source_id: str,
        metadata: Iterable[Mapping[str, Any]],
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[dict[str, Any]]:
        record = self.catalog.require(source_id)
        sample_rows = _positive_int("DATA_SOURCE_PROFILE_SAMPLE_ROWS", 200)
        max_columns = _positive_int("DATA_SOURCE_PROFILE_MAX_COLUMNS", 40)
        enum_limit = _positive_int("DATA_SOURCE_PROFILE_ENUM_LIMIT", 20)
        statement_timeout_ms = _positive_int(
            "DATA_SOURCE_PROFILE_STATEMENT_TIMEOUT_MS", 30000
        )
        total_timeout_seconds = _positive_int(
            "DATA_SOURCE_PROFILE_TOTAL_TIMEOUT_SECONDS", 600
        )

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for raw in metadata:
            item = dict(raw)
            grouped[(str(item.get("schema") or ""), str(item.get("table") or ""))].append(item)
        tables = [(key, columns) for key, columns in grouped.items() if all(key)]

        connection = None
        profiles: list[dict[str, Any]] = []
        try:
            connection = self.connector._connect(source_id)
            cursor = connection.cursor()
            try:
                if record.database_type == "mysql":
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute(
                        f"SET SESSION MAX_EXECUTION_TIME={statement_timeout_ms}"
                    )
                    estimates = self._mysql_estimates(cursor, record.database_name)
                else:
                    cursor.execute("BEGIN READ ONLY")
                    cursor.execute(
                        f"SET statement_timeout = {statement_timeout_ms}"
                    )
                    estimates = self._postgresql_estimates(cursor, record.schema_name or "public")

                total = len(tables)
                keys = self._key_columns(
                    cursor,
                    database_type=record.database_type,
                    schema=record.schema_name,
                    database=record.database_name,
                )
                started = time.monotonic()
                deadline = started + total_timeout_seconds
                for index, ((schema, table), columns) in enumerate(tables, start=1):
                    if progress:
                        progress(index, total, table)
                    if time.monotonic() > deadline:
                        profiles.append(
                            {
                                "schema": schema,
                                "table": table,
                                "row_estimate": estimates.get((schema, table)),
                                "sample_row_count": 0,
                                "quality": {
                                    "skipped_by_total_timeout": True,
                                    "timeout_reason": "总分析时间上限",
                                },
                                "error": "",
                            }
                        )
                        continue
                    profiles.append(
                        self._profile_table(
                            cursor,
                            database_type=record.database_type,
                            schema=schema,
                            table=table,
                            columns=columns,
                            row_estimate=estimates.get((schema, table)),
                            sample_rows=sample_rows,
                            max_columns=max_columns,
                            enum_limit=enum_limit,
                            keys=keys.get((schema, table), ([], [])),
                        )
                    )
                connection.rollback()
            finally:
                cursor.close()
        except DataSourceCatalogError:
            raise
        except Exception as exc:
            raise DataSourceCatalogError(f"数据画像失败：{type(exc).__name__}") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        self.catalog.replace_table_profiles(source_id, profiles)
        return profiles

    @staticmethod
    def _key_columns(
        cursor: Any,
        *,
        database_type: str,
        schema: str,
        database: str,
    ) -> dict[tuple[str, str], tuple[list[str], list[str]]]:
        """一次性读取全部表的主键/唯一键列，返回 {(schema, table): (pk, unique)}。"""
        result: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
        try:
            if database_type == "mysql":
                cursor.execute(
                    """
                    SELECT tc.TABLE_NAME, tc.CONSTRAINT_TYPE, kcu.COLUMN_NAME
                    FROM information_schema.TABLE_CONSTRAINTS tc
                    JOIN information_schema.KEY_COLUMN_USAGE kcu
                      ON tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                     AND tc.TABLE_NAME = kcu.TABLE_NAME
                     AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                    WHERE tc.TABLE_SCHEMA = %s
                      AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE')
                    ORDER BY kcu.ORDINAL_POSITION
                    """,
                    (database,),
                )
                for key_row in cursor.fetchall():
                    table_name = str(key_row["TABLE_NAME"])
                    constraint_type = str(key_row["CONSTRAINT_TYPE"])
                    column_name = str(key_row["COLUMN_NAME"])
                    pk, uq = result.setdefault(
                        (database, str(table_name)), ([], [])
                    )
                    if constraint_type == "PRIMARY KEY":
                        pk.append(str(column_name))
                    else:
                        uq.append(str(column_name))
            else:
                cursor.execute(
                    """
                    SELECT n.nspname, c.relname, con.contype,
                           a.attname
                    FROM pg_constraint con
                    JOIN pg_class c ON c.oid = con.conrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                    JOIN pg_attribute a
                      ON a.attrelid = c.oid AND a.attnum = k.attnum
                    WHERE n.nspname = %s AND con.contype IN ('p', 'u')
                    ORDER BY k.ord
                    """,
                    (schema or "public",),
                )
                for key_row in cursor.fetchall():
                    row_schema = str(key_row["nspname"])
                    table_name = str(key_row["relname"])
                    contype = str(key_row["contype"])
                    column_name = str(key_row["attname"])
                    pk, uq = result.setdefault(
                        (str(row_schema), str(table_name)), ([], [])
                    )
                    if contype == "p":
                        pk.append(str(column_name))
                    else:
                        uq.append(str(column_name))
        except Exception:
            # 键信息读取失败不阻断画像，按"无可用键"处理。
            return {}
        return result

    @staticmethod
    def _mysql_estimates(cursor: Any, database_name: str) -> dict[tuple[str, str], int | None]:
        cursor.execute(
            """
            SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
                   TABLE_ROWS AS row_estimate
            FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s
            """,
            (database_name,),
        )
        return {
            (str(row["schema_name"]), str(row["table_name"])):
                int(row["row_estimate"]) if row["row_estimate"] is not None else None
            for row in cursor.fetchall()
        }

    @staticmethod
    def _postgresql_estimates(cursor: Any, schema_name: str) -> dict[tuple[str, str], int | None]:
        cursor.execute(
            """
            SELECT ns.nspname AS schema_name, cls.relname AS table_name,
                   GREATEST(cls.reltuples, 0)::bigint AS row_estimate
            FROM pg_class cls
            JOIN pg_namespace ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = %s AND cls.relkind IN ('r', 'p', 'm', 'v')
            """,
            (schema_name,),
        )
        return {
            (str(row["schema_name"]), str(row["table_name"])): int(row["row_estimate"])
            for row in cursor.fetchall()
        }

    def _profile_table(
        self,
        cursor: Any,
        *,
        database_type: str,
        schema: str,
        table: str,
        columns: list[dict[str, Any]],
        row_estimate: int | None,
        sample_rows: int,
        max_columns: int,
        enum_limit: int,
        keys: tuple[list[str], list[str]],
    ) -> dict[str, Any]:
        selected = [
            item for item in columns
            if _is_profiled_type(str(item.get("type") or ""))
        ][:max_columns]
        quoted_table = ".".join(
            _quote_identifier(database_type, item) for item in (schema, table)
        )
        sample: list[Mapping[str, Any]] = []
        error = ""
        if selected:
            column_sql = ", ".join(
                _quote_identifier(database_type, str(item["column"])) for item in selected
            )
            try:
                cursor.execute("SAVEPOINT water_agent_profile_table")
                cursor.execute(f"SELECT {column_sql} FROM {quoted_table} LIMIT %s", (sample_rows,))
                sample = [dict(row) for row in cursor.fetchall()]
                cursor.execute("RELEASE SAVEPOINT water_agent_profile_table")
            except Exception as exc:
                error = f"受限样本读取失败：{type(exc).__name__}"
                try:
                    cursor.execute("ROLLBACK TO SAVEPOINT water_agent_profile_table")
                    cursor.execute("RELEASE SAVEPOINT water_agent_profile_table")
                except Exception:
                    pass

        column_profiles: list[dict[str, Any]] = []
        for item in selected:
            name = str(item["column"])
            values = [row.get(name) for row in sample]
            non_null = [value for value in values if value is not None]
            serialized = [_json_value(value) for value in non_null]
            distinct = list(dict.fromkeys(serialized))
            profile: dict[str, Any] = {
                "column": name,
                "type": str(item.get("type") or ""),
                "sample_null_rate": round((len(values) - len(non_null)) / len(values), 4) if values else None,
                "sample_distinct_count": len(distinct),
                "sensitive": _is_sensitive(name),
            }
            if non_null and (_is_numeric_type(profile["type"]) or _is_time_type(profile["type"])):
                try:
                    profile["sample_min"] = _json_value(min(non_null))
                    profile["sample_max"] = _json_value(max(non_null))
                except TypeError:
                    pass
            if not profile["sensitive"] and len(distinct) <= enum_limit:
                profile["typical_values"] = distinct[:enum_limit]
            column_profiles.append(profile)

        primary_keys = [str(item["column"]) for item in columns if item.get("primary_key")]
        time_candidates = [
            str(item["column"]) for item in columns
            if _is_time_type(str(item.get("type") or ""))
            or any(word in str(item.get("column") or "").lower() for word in _TIME_WORDS)
        ]
        pk_columns, unique_columns = keys
        table_comment = str(columns[0].get("table_comment") or "") if columns else ""
        quality: dict[str, Any] = {
            "column_count": len(columns),
            "queryable_column_count": sum(
                1
                for item in columns
                if _is_profiled_type(str(item.get("type") or ""))
                and (
                    _is_numeric_type(str(item.get("type") or ""))
                    or _is_time_type(str(item.get("type") or ""))
                    or "char" in str(item.get("type") or "").lower()
                )
            ),
            "has_primary_key": bool(pk_columns),
            "has_unique_key": bool(unique_columns),
            "primary_key_columns": pk_columns[:5],
            "row_estimate": row_estimate,
            "sample_row_count": len(sample),
        }
        sampled_cells = len(sample) * max(len(selected), 1)
        sampled_nulls = sum(
            1
            for row in sample
            for item in selected
            if row.get(str(item["column"])) is None
        )
        quality["sample_null_rate"] = (
            round(sampled_nulls / sampled_cells, 4) if sampled_cells else None
        )
        if not error and time_candidates:
            time_column = time_candidates[0]
            quoted_time = _quote_identifier(
                database_type, str(time_column)
            )
            try:
                cursor.execute("SAVEPOINT water_agent_profile_time")
                cursor.execute(
                    f"SELECT MIN({quoted_time}), MAX({quoted_time}) "
                    f"FROM {quoted_table}"
                )
                time_row = cursor.fetchone()
                min_time = time_row["min"] if time_row else None
                max_time = time_row["max"] if time_row else None
                cursor.execute("RELEASE SAVEPOINT water_agent_profile_time")
                if max_time is not None:
                    quality["latest_data_at"] = _json_value(max_time)
                    if min_time is not None:
                        try:
                            span = (max_time - min_time).total_seconds()
                            quality["time_coverage_days"] = round(
                                span / 86400.0, 3
                            )
                        except (AttributeError, TypeError):
                            quality["time_coverage_days"] = None
                # 初版无法推断更新周期，只记录最新时间，不做固定天数扣分。
                quality["observed_update_interval"] = None
                quality["staleness_ratio"] = None
                quality["freshness_confidence"] = 0.0
            except Exception:
                try:
                    cursor.execute(
                        "ROLLBACK TO SAVEPOINT water_agent_profile_time"
                    )
                    cursor.execute(
                        "RELEASE SAVEPOINT water_agent_profile_time"
                    )
                except Exception:
                    pass
        key_columns = list(pk_columns) or list(unique_columns)
        if not error and key_columns:
            key_sql = ", ".join(
                _quote_identifier(database_type, str(col))
                for col in key_columns
            )
            try:
                cursor.execute("SAVEPOINT water_agent_profile_dup")
                cursor.execute(
                    f"SELECT {key_sql}, count(*) AS c FROM ("
                    f"SELECT {key_sql} FROM {quoted_table} LIMIT %s"
                    f") t GROUP BY {key_sql} HAVING count(*) > 1",
                    (sample_rows,),
                )
                dup_rows = sum(int(row["c"]) for row in cursor.fetchall())
                cursor.execute("RELEASE SAVEPOINT water_agent_profile_dup")
                quality["duplicate_key_ratio"] = (
                    round(dup_rows / sample_rows, 4) if sample_rows else None
                )
            except Exception:
                quality["duplicate_key_ratio"] = None
        else:
            quality["duplicate_key_ratio"] = "unknown"

        structure_parts = sorted(
            f"{item.get('column')}|{item.get('type')}"
            for item in columns
        )
        quality["structure_fingerprint"] = hashlib.sha256(
            "|".join(structure_parts).encode("utf-8")
        ).hexdigest()
        data_parts = sorted(
            str(_json_value(value))
            for row in sample
            for item in selected
            if not _is_sensitive(str(item["column"]))
            for value in [row.get(str(item["column"]))]
            if value is not None
        )
        quality["data_fingerprint"] = hashlib.sha256(
            "|".join(data_parts).encode("utf-8")
        ).hexdigest() if data_parts else ""
        return {
            "schema": schema,
            "table": table,
            "object_type": str(columns[0].get("object_type") or "table") if columns else "table",
            "table_comment": table_comment,
            "row_estimate": row_estimate,
            "sample_row_count": len(sample),
            "sample_limit": sample_rows,
            "sample_based": True,
            "table_role_candidate": _infer_role(table, table_comment),
            "grain_candidate": " + ".join(primary_keys) if primary_keys else "待语义确认",
            "time_column_candidate": time_candidates[0] if time_candidates else "",
            "columns": column_profiles,
            "quality": quality,
            "error": error,
        }

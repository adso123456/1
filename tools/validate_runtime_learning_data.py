"""真实数据正确性验证：证据与容器内数据库实际值逐单元格比对。

按元数据生成真实 SELECT（COUNT / LIMIT 采样 / 数值聚合），走与线上一致的
SQLGuard + build_result_evidence + capture_candidate 链路，再用独立只读
SQL 复跑同一查询，核对 evidence 的列、行数、单元格值和 numeric_summary
是否与数据库真实结果一致。

用法：python tools/validate_runtime_learning_data.py
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import dotenv_values
import psycopg2
import pymysql

from backend.learning_candidate_store import LearningCandidateStore
from backend.mysql_sql_guard import MySQLSQLGuard
from backend.query_intent import ContextProfile
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_capture import (
    build_result_evidence,
    capture_candidate,
)
from backend.sql_guard import SQLGuard
from config.learning_settings import OnlineLearningSettings


ENV = dotenv_values(ROOT / ".env")
SENSITIVE_RE = re.compile(
    r"password|passwd|secret|token|api_?key|private_?key|id_?card|phone|mobile|email|身份证|手机号|邮箱",
    flags=re.I,
)
GEOMETRY_RE = re.compile(r"geometry|geography|point|linestring|polygon|geom", flags=re.I)
NUMERIC_TYPES = ("int", "bigint", "decimal", "numeric", "double", "float", "real")

SETTINGS = OnlineLearningSettings.from_environment(
    {
        "ONLINE_LEARNING_ENABLED": "true",
        "ONLINE_LEARNING_CAPTURE_ENABLED": "true",
        "ONLINE_LEARNING_MAX_RESULT_ROWS": "20",
        "ONLINE_LEARNING_MAX_RESULT_BYTES": "65536",
    }
)


def _pg_conn():
    connection = psycopg2.connect(
        host="127.0.0.1",
        port=int(ENV.get("DB_PORT", "5433")),
        dbname=ENV.get("DB_NAME", "gt_monitor"),
        user=ENV.get("DB_USER", "postgres"),
        password=ENV.get("DB_PASSWORD", ""),
        connect_timeout=10,
    )
    connection.set_session(readonly=True, autocommit=False)
    return connection


def _mysql_conn():
    connection = pymysql.connect(
        host="127.0.0.1",
        port=int(ENV.get("MYSQL_PORT", "3307")),
        user=ENV.get("MYSQL_USER", "root"),
        password=ENV.get("MYSQL_PASSWORD", ""),
        database=ENV.get("MYSQL_DATABASE", "lzh_monitor"),
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
    )
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
    return connection


def run_query(source: str, connection, sql: str) -> tuple[list[str], list[dict], int]:
    if source == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = 20000")
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            raw = cursor.fetchall()
        rows = [dict(zip(columns, row)) for row in raw]
        connection.rollback()
    else:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=20000")
            cursor.execute("START TRANSACTION")
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            raw = cursor.fetchall()
            connection.rollback()
        rows = [dict(zip(columns, row)) for row in raw]
    return columns, rows, len(rows)


def quote(source: str, table: str) -> str:
    return f'"{table}"' if source == "postgresql" else f"`{table}`"


def is_numeric_type(raw_type: str) -> bool:
    return any(token in str(raw_type).lower() for token in NUMERIC_TYPES)


def build_queries(source: str, metadata: list[dict]) -> list[tuple[str, str, str]]:
    """返回 (table, kind, sql) 列表。"""
    tables: dict[str, list[dict]] = {}
    for item in metadata:
        tables.setdefault(str(item.get("table") or ""), []).append(item)
    queries: list[tuple[str, str, str]] = []
    for table, columns in sorted(tables.items()):
        if not table:
            continue
        q = quote(source, table)
        queries.append((table, "count", f"SELECT COUNT(*) AS cnt FROM {q}"))
        safe = [
            str(c.get("column") or "")
            for c in columns
            if not GEOMETRY_RE.search(str(c.get("column") or ""))
            and not GEOMETRY_RE.search(str(c.get("type") or ""))
            and not SENSITIVE_RE.search(str(c.get("column") or ""))
        ][:6]
        if safe:
            cols = ", ".join(
                f'"{c}"' if source == "postgresql" else f"`{c}`" for c in safe
            )
            queries.append((table, "sample", f"SELECT {cols} FROM {q} LIMIT 5"))
        numeric = [
            str(c.get("column") or "")
            for c in columns
            if is_numeric_type(str(c.get("type") or ""))
            and not GEOMETRY_RE.search(str(c.get("column") or ""))
            and not GEOMETRY_RE.search(str(c.get("type") or ""))
            and not SENSITIVE_RE.search(str(c.get("column") or ""))
        ][:1]
        if numeric:
            col = numeric[0]
            quoted = f'"{col}"' if source == "postgresql" else f"`{col}`"
            expr = (
                f"AVG({quoted})"
                if source == "postgresql"
                else f"ROUND(AVG({quoted}), 6)"
            )
            queries.append(
                (table, "avg", f"SELECT {expr} AS avg_{col} FROM {q}")
            )
    return queries


def compare_cell(evidence_cell, db_cell, sensitive: bool) -> bool:
    if sensitive:
        return evidence_cell == "[REDACTED]"
    if db_cell is None:
        return evidence_cell is None
    if isinstance(db_cell, (bytes, bytearray, memoryview)):
        return evidence_cell == "[binary]"
    if isinstance(db_cell, (dict, list)):
        return isinstance(evidence_cell, str)
    text = str(db_cell)
    if len(text) > 200:
        return str(evidence_cell) == text[:200] + "..."
    return str(evidence_cell) == text


def expected_numeric_summary(columns: list[str], rows: list[dict]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for column in columns:
        values = []
        for row in rows:
            value = row.get(column)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values.append(float(value))
        if not values:
            continue
        summary[column] = {
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "sum": round(sum(values), 6),
            "avg": round(sum(values) / len(values), 6),
        }
    return summary


def verify_evidence(
    source: str,
    columns: list[str],
    vcolumns: list[str],
    vrows: list[dict],
    evidence,
) -> list[str]:
    problems: list[str] = []
    if list(evidence.columns) != list(vcolumns):
        problems.append(f"columns 不一致: {list(evidence.columns)} vs {list(vcolumns)}")
    if int(evidence.total_row_count) != len(vrows):
        problems.append(
            f"total_row_count 不一致: {evidence.total_row_count} vs {len(vrows)}"
        )
    kept = evidence.rows
    for index, row in enumerate(kept):
        if index >= len(vrows):
            break
        db_row = vrows[index]
        for col_index, column in enumerate(columns):
            sensitive = bool(SENSITIVE_RE.search(str(column)))
            if col_index >= len(row):
                problems.append(f"第{index}行缺列 {column}")
                continue
            if not compare_cell(row[col_index], db_row.get(column), sensitive):
                problems.append(
                    f"第{index}行 {column} 单元格不一致: "
                    f"evidence={row[col_index]!r} db={db_row.get(column)!r}"
                )
    expected = expected_numeric_summary(columns, vrows)
    if evidence.numeric_summary != expected:
        problems.append(
            f"numeric_summary 不一致: {evidence.numeric_summary} vs {expected}"
        )
    return problems


def main() -> None:
    pg_metadata = json.loads(
        (ROOT / "agent_data" / "column_metadata_index.json").read_text(encoding="utf-8")
    )
    mysql_metadata = json.loads(
        (
            ROOT
            / "agent_data"
            / "mysql-lzh-monitor"
            / "column_metadata_index.json"
        ).read_text(encoding="utf-8")
    )
    pg_guard = SQLGuard(ROOT / "agent_data" / "column_metadata_index.json")
    mysql_guard = MySQLSQLGuard(
        ROOT / "agent_data" / "mysql-lzh-monitor" / "column_metadata_index.json"
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        store = LearningCandidateStore(Path(temp_dir) / "candidates.sqlite3")
        totals = {}
        for source, metadata, guard, connection_factory in (
            ("postgresql", pg_metadata, pg_guard, _pg_conn),
            ("mysql", mysql_metadata, mysql_guard, _mysql_conn),
        ):
            queries = build_queries(source, metadata)
            stats = {
                "queries": len(queries),
                "guard_ok": 0,
                "captured": 0,
                "evidence_ok": 0,
                "empty_table_skip": 0,
                "mismatches": [],
            }
            connection = connection_factory()
            try:
                for table, kind, sql in queries:
                    if kind != "count":
                        continue
                    columns, rows, row_count = run_query(source, connection, sql)
                    guard_result = guard.validate(
                        sql=sql,
                        query="验证查询",
                        deterministic_candidate_tables=[table],
                    )
                    if not guard_result.passed or guard_result.severity != "ok":
                        stats["mismatches"].append(
                            f"{source}/{table}/{kind}: guard={guard_result.severity} "
                            f"{guard_result.reason[:60]}"
                        )
                        continue
                    stats["guard_ok"] += 1
                    table_row_count = 0
                    if rows and "cnt" in rows[0]:
                        try:
                            table_row_count = int(rows[0]["cnt"])
                        except (TypeError, ValueError):
                            table_row_count = 0
                    if table_row_count == 0:
                        stats["empty_table_skip"] += 1
                        continue
                    metadata_payload = {
                        "query_type": "SELECT",
                        "columns": columns,
                        "results": rows,
                        "row_count": row_count,
                        "sql_guard": guard_result.to_dict(),
                    }
                    evidence = build_result_evidence(
                        metadata_payload,
                        max_rows=SETTINGS.max_result_rows,
                        max_bytes=SETTINGS.max_result_bytes,
                    )
                    if evidence is None:
                        stats["mismatches"].append(
                            f"{source}/{table}/{kind}: evidence 构建失败"
                        )
                        continue
                    state = QueryPerformanceState(
                        conversation_id="validate",
                        request_id=f"{source}-{table}-{kind}",
                        question=f"验证查询 {table}",
                        source_id=source,
                        context_profile=ContextProfile.FULL,
                    )
                    state.successful_run_sql_count = 1
                    state.last_sql = sql
                    state.last_result_metadata = metadata_payload
                    candidate = capture_candidate(
                        state=state,
                        source_id=source,
                        database_type="postgresql" if source == "postgresql" else "mysql",
                        runtime_revision=1,
                        final_answer=f"查询返回 {row_count} 行",
                        request_failed=False,
                        store=store,
                        settings=SETTINGS,
                    )
                    if candidate is not None:
                        stats["captured"] += 1
                    vcolumns, vrows, vcount = run_query(source, connection, sql)
                    problems = verify_evidence(
                        source, columns, vcolumns, vrows, evidence
                    )
                    if problems:
                        for problem in problems[:3]:
                            stats["mismatches"].append(
                                f"{source}/{table}/{kind}: {problem}"
                            )
                    else:
                        stats["evidence_ok"] += 1
                    # count 通过且有数据后，再跑 sample/avg
                    for sub_kind, sub_sql in (
                        (kind2, sql2)
                        for kind2, sql2 in (
                            (k, s) for t, k, s in queries if t == table and k != "count"
                        )
                    ):
                        s_columns, s_rows, s_row_count = run_query(
                            source, connection, sub_sql
                        )
                        s_guard = guard.validate(
                            sql=sub_sql,
                            query="验证查询",
                            deterministic_candidate_tables=[table],
                        )
                        if not s_guard.passed or s_guard.severity != "ok":
                            stats["mismatches"].append(
                                f"{source}/{table}/{sub_kind}: guard={s_guard.severity} "
                                f"{s_guard.reason[:60]}"
                            )
                            continue
                        s_metadata = {
                            "query_type": "SELECT",
                            "columns": s_columns,
                            "results": s_rows,
                            "row_count": s_row_count,
                            "sql_guard": s_guard.to_dict(),
                        }
                        s_evidence = build_result_evidence(
                            s_metadata,
                            max_rows=SETTINGS.max_result_rows,
                            max_bytes=SETTINGS.max_result_bytes,
                        )
                        if s_evidence is None:
                            stats["mismatches"].append(
                                f"{source}/{table}/{sub_kind}: evidence 构建失败"
                            )
                            continue
                        s_state = QueryPerformanceState(
                            conversation_id="validate",
                            request_id=f"{source}-{table}-{sub_kind}",
                            question=f"验证查询 {table}",
                            source_id=source,
                            context_profile=ContextProfile.FULL,
                        )
                        s_state.successful_run_sql_count = 1
                        s_state.last_sql = sub_sql
                        s_state.last_result_metadata = s_metadata
                        s_candidate = capture_candidate(
                            state=s_state,
                            source_id=source,
                            database_type=(
                                "postgresql" if source == "postgresql" else "mysql"
                            ),
                            runtime_revision=1,
                            final_answer=f"查询返回 {s_row_count} 行",
                            request_failed=False,
                            store=store,
                            settings=SETTINGS,
                        )
                        if s_candidate is not None:
                            stats["captured"] += 1
                        v_columns, v_rows, v_count = run_query(
                            source, connection, sub_sql
                        )
                        s_problems = verify_evidence(
                            source, s_columns, v_columns, v_rows, s_evidence
                        )
                        if s_problems:
                            for problem in s_problems[:3]:
                                stats["mismatches"].append(
                                    f"{source}/{table}/{sub_kind}: {problem}"
                                )
                        else:
                            stats["evidence_ok"] += 1
            finally:
                connection.close()
            totals[source] = stats

    for source, stats in totals.items():
        print(
            f"[{source}] queries={stats['queries']} guard_ok={stats['guard_ok']} "
            f"captured={stats['captured']} evidence_ok={stats['evidence_ok']} "
            f"empty_skip={stats['empty_table_skip']} "
            f"mismatches={len(stats['mismatches'])}"
        )
        for mismatch in stats["mismatches"][:12]:
            print("  !", mismatch)

    total_ok = sum(s["evidence_ok"] for s in totals.values())
    total_verified = sum(
        s["evidence_ok"] + len(s["mismatches"]) for s in totals.values()
    )
    print(
        f"合计：evidence 与真实数据一致 {total_ok}/{total_verified} "
        f"({round(total_ok / total_verified * 100, 2)}%)"
    )


if __name__ == "__main__":
    main()

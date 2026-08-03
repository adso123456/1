"""数据源专属推荐问题离线生成器（V1 阻断修复版）。

独立进程，执行完成后退出。绑定单一 source_id，只读该数据源：

1. 严格复用现有训练资产：
   - 只接受 `train_decision == "approved"`、`tool_name == "run_sql"`、
     `training_level` 属于项目现有合法白名单；
   - 使用当前 source_id 对应的 SQLGuard（`SQLGuard` / `MySQLSQLGuard`，
     index 为该源已发布 Metadata）校验 SQL；
   - 解析已发布 Metadata，确认问题关联表与 SQL 使用表属于当前发布范围。

2. 问题必须语义完整、可直接执行：
   - 不发布含“某日/某月/某年/指定对象/指定监测站”等未解析占位词的问题；
   - 日期类 SQL 改写为确定语义（最新有数据的一天/七天/一个月），
     通过只读查询解析真实日期后替换，得到具体可执行 SQL；
   - 数据库验证对应最终展示问题的实际 SQL（改写后的 SQL），
     不是只验证原始训练样例后挂到改写问题上；
   - 无法安全改写或验证的问题设为 disabled 并记录原因。

3. 资产绑定 runtime revision：
   - 资产保存 `runtime_revision` 与 `metadata_sha256`；
   - 在线读取时资产 revision 与 Catalog 当前 revision 不一致即返回空列表。

真实数据库只读事务（PG `default_transaction_read_only=on`；MySQL
`SET SESSION TRANSACTION READ ONLY` + `START TRANSACTION READ ONLY`），
禁止修改正式数据库、Catalog、Metadata、Memory、Chroma 与训练资产。

用法：
    python tools/generate_question_suggestions.py --source-id postgresql-main
    python tools/generate_question_suggestions.py --source-id mysql-lzh-monitor --no-db-verify
    python tools/generate_question_suggestions.py --source-id <id> --root <临时根> --catalog <catalog.sqlite3>
        --materials-dir <示例目录> --metadata-path <metadata.json> --no-db-verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.question_suggestion_assets import (
    build_question_directory,
    write_question_directory,
)
from config.settings import AGENT_DATA_DIR


GENERATOR_NAME = "tools/generate_question_suggestions.py"

# --------------------------------------------------------------------------- #
# 合法值白名单（来自项目现有训练资产实际取值）
# --------------------------------------------------------------------------- #

LEGAL_TRAIN_DECISION = "approved"
LEGAL_TOOL_NAME = "run_sql"
LEGAL_TRAINING_LEVELS = frozenset(
    {
        "level2_sql_examples",
        "level3_sql_examples",
        "level2_mysql_sql_examples",
        "level3_mysql_sql_examples",
    }
)

# 未解析占位词：启用的问题文本禁止包含
PLACEHOLDER_WORDS = (
    "某日", "某月", "某年", "某天", "某一天",
    "指定对象", "指定监测站", "指定站", "指定企业", "指定区域",
    "指定断面", "指定水文站", "指定气象站", "指定污染源",
    "某个", "某站", "某企业", "某区域", "某断面", "某监测站",
)

# --------------------------------------------------------------------------- #
# 日期/问题文本正则
# --------------------------------------------------------------------------- #

# SQL 中的日期比较：<列> <op> 'YYYY-MM-DD[ 时间]'
_SQL_DATE_CMP_RE = re.compile(
    r"([\w.]+)\s*(<=|>=|<|>|=)\s*'(\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)'"
)
# “…日起 N 天”跨度问题（N 为数字或中文数词）
_Q_DATE_SPAN_RE = re.compile(
    r"\d{4}年\d{1,2}月\d{1,2}日\s*起\s*(?:\d+|[一二三四五六七八九十]+)\s*天"
)
_Q_DATE_DAY_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
_Q_DATE_MONTH_RE = re.compile(r"\d{4}年\d{1,2}月")


def _cleanup(text: str) -> str:
    result = re.sub(r"\s+", " ", text)
    result = re.sub(r"[，、]{2,}", "、", result)
    result = re.sub(r"在(?=最新有数据)", "", result)
    return result.strip(" ，、")


def _has_placeholder(text: str) -> bool:
    return any(word in text for word in PLACEHOLDER_WORDS)


# --------------------------------------------------------------------------- #
# 已批准 SQL 示例读取（严格过滤）
# --------------------------------------------------------------------------- #


def _sample_eligibility(sample: dict[str, Any]) -> tuple[bool, str]:
    if sample.get("train_decision") != LEGAL_TRAIN_DECISION:
        return False, "非 approved"
    if sample.get("tool_name") != LEGAL_TOOL_NAME:
        return False, f"tool_name 非 {LEGAL_TOOL_NAME}"
    training_level = sample.get("training_level")
    if training_level not in LEGAL_TRAINING_LEVELS:
        return False, f"非法 training_level: {training_level!r}"
    question = sample.get("question")
    sql = sample.get("args", {}).get("sql")
    if not isinstance(question, str) or not question.strip():
        return False, "缺少问题文本"
    if not isinstance(sql, str) or not sql.strip():
        return False, "缺少 SQL"
    return True, ""


def load_approved_samples(
    material_paths: list[Path],
) -> tuple[list[dict[str, Any]], Counter]:
    """读取已批准示例并返回 (合格样本, 过滤原因统计)。"""
    samples: list[dict[str, Any]] = []
    reasons: Counter = Counter()
    for path in material_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons["材料不可读"] += 1
            continue
        items = payload.get("samples", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        for sample in items:
            if not isinstance(sample, dict):
                reasons["非对象示例"] += 1
                continue
            eligible, reason = _sample_eligibility(sample)
            if not eligible:
                reasons[reason] += 1
                continue
            samples.append(
                {
                    "sample_id": sample.get("sample_id") or "",
                    "question": str(sample["question"]).strip(),
                    "sql": str(sample["args"]["sql"]).strip(),
                    "expected_behavior": sample.get("expected_behavior") or "",
                    "tables": list(sample.get("expected_tables") or []),
                }
            )
    samples.sort(key=lambda item: item["sample_id"])
    return samples, reasons


def default_materials_paths(source_id: str) -> list[Path]:
    """按项目实际训练资产位置解析本源已批准示例（可被 --materials-dir 覆盖）。"""
    training = PROJECT_ROOT / "training"
    if source_id == "mysql-lzh-monitor":
        path = training / "mysql_lzh_monitor" / "sql_examples.json"
        return [path] if path.is_file() else []
    if source_id == "postgresql-main":
        return [
            *sorted(training.glob("f5_level2_batch*.json")),
            *sorted(training.glob("f5_level3_batch*.json")),
        ]
    path = training / source_id / "sql_examples.json"
    return [path] if path.is_file() else []


# --------------------------------------------------------------------------- #
# Metadata 与 SQLGuard（复用现有能力）
# --------------------------------------------------------------------------- #


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_published_tables(metadata_path: Path) -> set[str]:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item.get("table")) for item in payload if isinstance(item, dict) and item.get("table")}


def _build_guard(database_type: str, metadata_path: Path) -> Any:
    if database_type == "mysql":
        from backend.mysql_sql_guard import MySQLSQLGuard

        return MySQLSQLGuard(index_path=str(metadata_path))
    from backend.sql_guard import SQLGuard

    return SQLGuard(index_path=str(metadata_path))


# --------------------------------------------------------------------------- #
# 日期改写：确定语义 → 真实日期
# --------------------------------------------------------------------------- #


def _split_date_literal(literal: str) -> tuple[str, str]:
    match = re.match(r"(\d{4}-\d{1,2}-\d{1,2})(.*)", literal)
    assert match is not None
    return match.group(1), match.group(2)


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def _date_matches(sql: str) -> list[tuple[str, str, str]]:
    return list(_SQL_DATE_CMP_RE.findall(sql))


def _strip_date_conditions(sql: str, matches: list[tuple[str, str, str]]) -> str:
    result = sql
    for col, op, literal in matches:
        result = re.sub(
            rf"\s*(?:AND\s+)?{re.escape(col)}\s*{re.escape(op)}\s*{re.escape(f"'{literal}'")}",
            "",
            result,
            flags=re.I,
        )
    result = re.sub(r"\bWHERE\s+AND\b", "WHERE", result, flags=re.I)
    result = re.sub(r"\bWHERE\s*$", "", result, flags=re.I)
    return result


def _latest_day_probe_sql(sql: str, matches: list[tuple[str, str, str]]) -> str:
    """构造求“最新有数据的一天”的只读探针 SQL（去掉日期条件与尾部子句）。"""
    stripped = _strip_date_conditions(sql, matches)
    from_match = re.search(r"\bFROM\b", stripped, flags=re.I)
    if from_match is None:
        raise ValueError("SQL 缺少 FROM 子句")
    timecol = matches[0][0]
    tail = stripped[from_match.start():]
    tail = re.sub(
        r"\s+(ORDER\s+BY|GROUP\s+BY|LIMIT|HAVING)\b.*$",
        "",
        tail,
        flags=re.I | re.S,
    )
    tail = re.sub(r"\bWHERE\s*$", "", tail, flags=re.I)
    return f"SELECT MAX(DATE({timecol})) AS latest_day " + tail


def _is_natural_month(start: date, end: date) -> bool:
    """下界为某月 1 日、上界为下月 1 日，构成恰好一个自然月的半开区间。"""
    if start.day != 1 or end.day != 1:
        return False
    if start.month == 12:
        return end.year == start.year + 1 and end.month == 1 and end.day == 1
    return end.year == start.year and end.month == start.month + 1 and end.day == 1


def _analyze_date_range(
    matches: list[tuple[str, str, str]],
) -> tuple[str, str, int]:
    """返回 (granularity, 问题语义短语, 窗口天数)。

    按同一时间字段识别上下界；自然月窗口必须满足完整上下界关系
    （下界为某月 1 日、上界为下月 1 日），不能仅凭“出现月初日期”判断。
    """
    by_column: dict[str, list[tuple[str, str, str]]] = {}
    for col, op, literal in matches:
        by_column.setdefault(col, []).append((col, op, literal))

    for column_matches in by_column.values():
        start: date | None = None
        end: date | None = None
        for _, op, literal in column_matches:
            parsed = _parse_date(literal)
            if op in (">=", ">") and start is None:
                start = parsed
            elif op in ("<", "<=") and end is None:
                end = parsed
        if start is None and end is None:
            continue
        if start is not None and end is not None:
            if _is_natural_month(start, end):
                return "month", "最新有数据的一个月", 0
            window = (end - start).days
            if window == 1:
                return "day", "最新有数据的一天", 1
            if window == 7:
                return "day", "最新有数据的七天", 7
            return "day", f"最新有数据的{window}天", window
        return "day", "最新有数据的一天", 1
    return "day", "最新有数据的一天", 1


def _shift_month(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _substitute_dates(
    sql: str,
    matches: list[tuple[str, str, str]],
    latest_day: date,
    granularity: str,
    window_days: int,
) -> str:
    result = sql
    for col, op, literal in matches:
        date_part, suffix = _split_date_literal(literal)
        if granularity == "month":
            if op in ("<", "<="):
                new_date = _shift_month(latest_day, 1)
            else:
                new_date = date(latest_day.year, latest_day.month, 1)
        elif op in ("<", "<="):
            # 结束日 = D + 1：半开区间 [D-(N-1), D+1)，保证最新数据日 D 在窗口内
            new_date = date.fromordinal(latest_day.toordinal() + 1)
        else:
            # 开始日 = D - (N-1)：包含 D 在内最近 N 个自然日
            new_date = date.fromordinal(latest_day.toordinal() - (window_days - 1))
        result = result.replace(f"'{literal}'", f"'{new_date.strftime('%Y-%m-%d')}{suffix}'")
    return result


def _rewrite_question(question: str, phrase: str) -> str:
    result = _Q_DATE_SPAN_RE.sub(phrase, question)
    result = _Q_DATE_DAY_RE.sub(phrase, result)
    result = _Q_DATE_MONTH_RE.sub(phrase, result)
    return _cleanup(result)


# --------------------------------------------------------------------------- #
# 真实数据库只读验证（复用 catalog 凭据与连接参数）
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReadOnlySqlVerifier:
    """基于 catalog 凭据的只读 SQL 验证器（PG/MySQL）。"""

    def __init__(self, catalog: DataSourceCatalog, source_id: str) -> None:
        self._catalog = catalog
        self._source_id = source_id
        self._record = catalog.require(source_id)
        self._connection = None

    def connect(self) -> None:
        username, password = self._catalog.credentials(self._source_id)
        record = self._record
        if record.database_type == "mysql":
            from backend.mysql_tls import build_mysql_tls_settings

            import pymysql

            connection = pymysql.connect(
                host=record.host,
                port=record.port,
                database=record.database_name,
                user=username,
                password=password,
                connect_timeout=record.connect_timeout,
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
                **build_mysql_tls_settings(
                    mode=record.mysql_tls_mode,
                    ca_path=record.ssl_ca_path,
                    cert_path=record.ssl_cert_path,
                    key_path=record.ssl_key_path,
                ),
            )
            cursor = connection.cursor()
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.close()
            self._connection = connection
            return

        import psycopg2
        import psycopg2.extras

        kwargs: dict[str, Any] = {
            "host": record.host,
            "port": record.port,
            "dbname": record.database_name,
            "user": username,
            "password": password,
            "connect_timeout": record.connect_timeout,
            "application_name": "question-suggestion-generator",
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
            "cursor_factory": psycopg2.extras.RealDictCursor,
        }
        if record.ssl_mode:
            kwargs["sslmode"] = record.ssl_mode
        connection = psycopg2.connect(**kwargs)
        cursor = connection.cursor()
        cursor.execute("BEGIN READ ONLY")
        cursor.close()
        self._connection = connection

    def resolve_latest_day(self, probe_sql: str) -> date | None:
        if self._connection is None:
            raise RuntimeError("verifier 未连接")
        cursor = self._connection.cursor()
        try:
            cursor.execute(probe_sql)
            row = cursor.fetchone()
        except Exception:
            return None
        finally:
            cursor.close()
        if row is None:
            return None
        value = row[0] if not isinstance(row, dict) else row.get("latest_day")
        if value is None:
            return None
        if isinstance(value, str):
            return _parse_date(value)
        if hasattr(value, "date") and callable(getattr(value, "date", None)):
            return value.date()
        return value

    def verify(self, sql: str) -> dict[str, Any]:
        if self._connection is None:
            return {"verified": False, "error": "verifier 未连接"}
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql)
            columns = (
                [description[0] for description in cursor.description]
                if cursor.description
                else []
            )
            rows = cursor.fetchmany(20)
            return {
                "verified": True,
                "read_only": True,
                "columns": columns[:20],
                "row_count_sampled": len(rows),
            }
        except Exception as exc:
            return {
                "verified": False,
                "read_only": True,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            }
        finally:
            cursor.close()

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.rollback()
        except Exception:
            pass
        try:
            self._connection.close()
        except Exception:
            pass
        self._connection = None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def _stable_question_id(source_id: str, sample_id: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{source_id}:{sample_id}:{text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"q_{digest}"


def _open_catalog(catalog_path: str | None) -> DataSourceCatalog:
    path = (
        Path(catalog_path).expanduser().resolve()
        if catalog_path
        else (Path(AGENT_DATA_DIR).resolve() / "data_sources" / "catalog.sqlite3")
    )
    cipher = None
    key = os.getenv("DATA_SOURCE_CREDENTIAL_KEY", "").strip()
    if key:
        cipher = CredentialCipher(key)
    return DataSourceCatalog(path, cipher=cipher)


def generate_questions(
    *,
    source_id: str,
    root: Path | None,
    catalog_path: str | None,
    materials_dir: str | None,
    metadata_path: Path | None,
    no_db_verify: bool,
    max_questions: int,
    asset_version: str,
    verifier: Any | None = None,
) -> dict[str, Any]:
    """执行生成并写入资产，返回摘要。"""
    catalog = _open_catalog(catalog_path)
    record = catalog.require(source_id)

    resolved_metadata = (
        metadata_path if metadata_path is not None else Path(record.metadata_path)
    )
    if not resolved_metadata.is_file():
        raise ValueError(f"已发布 Metadata 不存在: {resolved_metadata}")
    metadata_sha256 = _file_sha256(resolved_metadata)
    metadata_tables = _load_published_tables(resolved_metadata)

    guard = _build_guard(record.database_type, resolved_metadata)

    materials = (
        sorted(Path(materials_dir).glob("*.json"))
        if materials_dir
        else default_materials_paths(source_id)
    )
    samples, filter_reasons = load_approved_samples(materials)

    # 只读验证器：日期改写与最终 SQL 验证都依赖它，须在循环前建立
    verification_note = "skipped"
    own_verifier: Any = verifier
    if not no_db_verify:
        verification_note = "verified"
        if own_verifier is None:
            own_verifier = ReadOnlySqlVerifier(catalog, source_id)
            own_verifier.connect()  # 连接失败则整体中止，不写资产

    disabled_reasons: Counter = Counter()
    candidates: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    try:
        for sample in samples:
            if len(candidates) >= max_questions:
                break
            entry: dict[str, Any] = {
                "id": _stable_question_id(source_id, sample["sample_id"], sample["question"]),
                "related_sample_id": sample["sample_id"],
                "related_tables": list(sample["tables"]),
            }

            # 1) 关联表必须属于该源当前发布范围
            missing_tables = [
                table for table in sample["tables"] if table not in metadata_tables
            ]
            if missing_tables:
                entry["disabled_reason"] = "metadata_mismatch"
                entry["metadata_missing_tables"] = missing_tables
                disabled_reasons["metadata_mismatch"] += 1
                candidates.append(entry)
                continue

            # 2) 日期改写：解析真实日期得到具体可执行 SQL
            matches = _date_matches(sample["sql"])
            if matches:
                if own_verifier is None:
                    entry["disabled_reason"] = "verification_unavailable"
                    disabled_reasons["verification_unavailable"] += 1
                    candidates.append(entry)
                    continue
                try:
                    probe_sql = _latest_day_probe_sql(sample["sql"], matches)
                    latest_day = own_verifier.resolve_latest_day(probe_sql)
                except Exception:
                    latest_day = None
                if latest_day is None:
                    entry["disabled_reason"] = "no_data"
                    disabled_reasons["no_data"] += 1
                    candidates.append(entry)
                    continue
                granularity, phrase, window_days = _analyze_date_range(matches)
                rewritten_sql = _substitute_dates(
                    sample["sql"], matches, latest_day, granularity, window_days
                )
                question = _rewrite_question(sample["question"], phrase)
            else:
                rewritten_sql = sample["sql"]
                question = _cleanup(sample["question"])

            if _has_placeholder(question):
                entry["disabled_reason"] = "placeholder"
                disabled_reasons["placeholder"] += 1
                candidates.append(entry)
                continue

            # 3) SQLGuard 校验改写后的实际 SQL
            guard_result = guard.validate(rewritten_sql, query="")
            if not guard_result.passed:
                entry["disabled_reason"] = "sqlguard_fail"
                entry["guard_reason"] = guard_result.reason
                disabled_reasons["sqlguard_fail"] += 1
                candidates.append(entry)
                continue

            # 4) 去重
            if question in seen_texts:
                continue
            seen_texts.add(question)

            entry["text"] = question
            entry["related_sql"] = rewritten_sql
            entry["enabled"] = False
            candidates.append(entry)

        # 5) 真实只读执行验证（对应最终问题及其实际 SQL）
        if own_verifier is None:
            for entry in candidates:
                if "enabled" in entry:
                    entry["disabled_reason"] = "verification_unavailable"
                    entry["verification"] = {
                        "verified": False,
                        "read_only": True,
                        "error": "verification skipped",
                    }
                    entry.pop("enabled", None)
                    disabled_reasons["verification_unavailable"] += 1
        else:
            for entry in candidates:
                if "enabled" not in entry:
                    continue
                result = own_verifier.verify(entry["related_sql"])
                if result.get("verified"):
                    entry["enabled"] = True
                    entry["verification"] = result
                else:
                    entry["disabled_reason"] = "execution_fail"
                    entry["verification"] = result
                    disabled_reasons["execution_fail"] += 1
                    entry.pop("enabled", None)
    finally:
        if own_verifier is not None and own_verifier is not verifier:
            own_verifier.close()

    enabled_entries = [entry for entry in candidates if entry.get("enabled") is True]

    basis: dict[str, Any] = {
        "metadata_path": str(resolved_metadata),
        "metadata_sha256": metadata_sha256,
        "metadata_table_count": len(metadata_tables),
        "sql_examples_paths": [str(path) for path in materials],
        "sql_examples_sha256": _files_sha256(materials) if materials else "",
        "approved_sample_count": len(samples),
        "enabled_question_count": len(enabled_entries),
        "db_verification": verification_note,
        "filter_reasons": dict(filter_reasons),
        "disabled_reasons": dict(disabled_reasons),
        "summary": (
            f"已批准 {len(samples)} 条，启用 {len(enabled_entries)} 条；"
            f"过滤：{dict(filter_reasons)}；禁用：{dict(disabled_reasons)}"
        ),
    }

    directory = build_question_directory(
        source_id,
        enabled_entries,
        asset_version=asset_version,
        runtime_revision=record.runtime_revision,
        metadata_sha256=metadata_sha256,
        generated_at=_utc_now_iso(),
        generator=GENERATOR_NAME,
        basis=basis,
    )
    output = write_question_directory(directory, root=root)

    return {
        "source_id": source_id,
        "asset_version": asset_version,
        "asset_path": str(output),
        "runtime_revision": record.runtime_revision,
        "metadata_sha256": metadata_sha256,
        "approved_sample_count": len(samples),
        "enabled_question_count": len(enabled_entries),
        "disabled_count": len(candidates) - len(enabled_entries),
        "db_verification": verification_note,
        "filter_reasons": dict(filter_reasons),
        "disabled_reasons": dict(disabled_reasons),
        "basis": basis,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成单一数据源的专属推荐问题资产",
    )
    parser.add_argument("--source-id", required=True, help="数据源 source_id")
    parser.add_argument(
        "--root",
        default=None,
        help="问题资产根目录（默认 <AGENT_DATA_DIR>/question_suggestions）",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="catalog.sqlite3 路径（默认 agent_data/data_sources/catalog.sqlite3）",
    )
    parser.add_argument(
        "--materials-dir",
        default=None,
        help="已批准 SQL 示例目录（覆盖默认按 source_id 解析）",
    )
    parser.add_argument(
        "--metadata-path",
        default=None,
        help="已发布 Metadata 路径（默认取 catalog 记录）",
    )
    parser.add_argument(
        "--no-db-verify",
        action="store_true",
        help="跳过真实数据库只读验证（问题不启用，仅做管线校验）",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=100,
        help="最多处理问题数（默认 100）",
    )
    parser.add_argument("--asset-version", default="v1", help="资产版本号")
    args = parser.parse_args(argv)

    summary = generate_questions(
        source_id=args.source_id,
        root=Path(args.root) if args.root else None,
        catalog_path=args.catalog,
        materials_dir=args.materials_dir,
        metadata_path=Path(args.metadata_path) if args.metadata_path else None,
        no_db_verify=args.no_db_verify,
        max_questions=args.max_questions,
        asset_version=args.asset_version,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

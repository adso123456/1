"""数据源专属推荐问题离线生成器（V1）。

独立进程，执行完成后退出。绑定单一 source_id：
- 只读指定数据源：复用已发布 Metadata 与已批准 SQL 示例（approved），不复制一套平行的数据库理解体系；
- 对已批准问题做确定性泛化（去掉会快速过期的日期/具体名称/ID），避免前端写死；
- 可选对真实数据库做只读执行验证（PG `default_transaction_read_only=on`；MySQL `START TRANSACTION READ ONLY`）；
- 只写本源的问题资产 `<AGENT_DATA_DIR>/question_suggestions/<source_id>/questions_v1.json`，
  不修改数据库、正式 Chroma、Metadata、Memory、Catalog 或训练资产；
- 生成失败不影响正式问数。

用法：
    python tools/generate_question_suggestions.py --source-id mysql-lzh-monitor
    python tools/generate_question_suggestions.py --source-id postgresql-main --no-db-verify
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
from datetime import datetime, timezone
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

# 站点/业务对象名词后缀，按最长优先匹配；用于把具体名称替换为稳定称呼
_NOUN_SUFFIXES = (
    "污水处理厂",
    "自来水厂",
    "磷石膏库",
    "水文站",
    "气象站",
    "监测站",
    "排污口",
    "断面",
    "站点",
    "水库",
    "企业",
    "公司",
    "工厂",
    "站",
)
_NOUN_NORMALIZE = {"站": "监测站", "站点": "监测站"}

_DATE_LITERAL_RE = re.compile(
    r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"
    r"|^\d{4}[-/]\d{1,2}$"
)
_FLAG_LITERALS = {"0", "1", "%", ""}
_CJK_RE = re.compile(r"[一-鿿]")


# --------------------------------------------------------------------------- #
# 问题文本泛化
# --------------------------------------------------------------------------- #


def _noun_for_literal(candidate: str) -> str:
    best = ""
    for suffix in _NOUN_SUFFIXES:
        if candidate.endswith(suffix) and len(suffix) > len(best):
            best = suffix
    return _NOUN_NORMALIZE.get(best, best)


def extract_name_literals(sql: str) -> list[str]:
    """从 SQL 单引号字面量中提取中文业务名称（排除日期/标志/纯数字）。"""
    names: list[str] = []
    for literal in re.findall(r"'([^']*)'", sql):
        literal = literal.strip()
        if not literal or len(literal) < 2:
            continue
        if _DATE_LITERAL_RE.match(literal):
            continue
        if literal in _FLAG_LITERALS or literal.isdigit():
            continue
        if not _CJK_RE.search(literal):
            continue
        if literal not in names:
            names.append(literal)
    return names


def _generalize_names(text: str, name_literals: list[str]) -> str:
    """把具体业务名称替换为稳定称呼，例如 幸福河站 → 指定监测站。

    名称字面量后只追加已知名词后缀（覆盖“名称”与“名称+站”两种写法），
    避免误吞“最近”等普通词。
    """
    result = text
    suffix_alternatives = "|".join(
        sorted(_NOUN_SUFFIXES, key=len, reverse=True)
    )
    for name in name_literals:
        if name not in result:
            continue
        literal_noun = _noun_for_literal(name)

        def _replace(match: re.Match[str]) -> str:
            following = match.group(0)[len(name):]
            combined = name + following
            noun = _noun_for_literal(combined) or literal_noun
            return "指定" + noun if noun else "指定对象"

        result = re.sub(
            re.escape(name) + "(?:" + suffix_alternatives + ")?",
            _replace,
            result,
        )
    return result


def _generalize_ids(text: str) -> str:
    """去掉 `ID 96、97、98` 这类具体标识值。"""
    result = re.sub(r"ID\s*[\d、,，\s]+", "", text)
    result = re.sub(r"id\s*[\d、,，\s]+", "", result)
    return result


def _generalize_dates(text: str) -> str:
    """把日期/时间段替换为稳定的“某日/某月/某年”。"""
    result = text
    result = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "某日", result)
    result = re.sub(r"\d{4}年\d{1,2}月", "某月", result)
    result = re.sub(r"\d{4}年", "某年", result)
    result = re.sub(r"\d{1,2}月\d{1,2}日", "某日", result)
    result = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", "某日", result)
    result = re.sub(r"\d{4}[-/]\d{1,2}", "某月", result)
    result = re.sub(r"某日起?\s*七\s*天", "某日起七天", result)
    result = re.sub(r"某日起?\s*7\s*天", "某日起7天", result)
    return result


def _cleanup(text: str) -> str:
    result = re.sub(r"\s+", "", text)
    result = re.sub(r"[，、]{2,}", "、", result)
    result = result.strip(" ，、")
    return result


def generalize_question(question: str, sql: str) -> str:
    """把已批准问题泛化为稳定推荐问题（无具体日期/名称/ID）。"""
    result = question.strip()
    result = _generalize_names(result, extract_name_literals(sql))
    result = _generalize_ids(result)
    result = _generalize_dates(result)
    return _cleanup(result)


# --------------------------------------------------------------------------- #
# 已批准 SQL 示例与 Metadata 读取（只读）
# --------------------------------------------------------------------------- #


def load_approved_samples(material_paths: list[Path]) -> list[dict[str, Any]]:
    """从已批准训练材料中提取 approved 示例。"""
    samples: list[dict[str, Any]] = []
    for path in material_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = payload.get("samples", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        for sample in items:
            if not isinstance(sample, dict):
                continue
            decision = sample.get("train_decision")
            if decision is not None and decision != "approved":
                continue
            question = sample.get("question")
            sql = sample.get("args", {}).get("sql")
            if not isinstance(question, str) or not question.strip():
                continue
            if not isinstance(sql, str) or not sql.strip():
                continue
            samples.append(
                {
                    "sample_id": sample.get("sample_id") or "",
                    "question": question.strip(),
                    "sql": sql,
                    "expected_behavior": sample.get("expected_behavior") or "",
                    "tables": list(sample.get("expected_tables") or []),
                }
            )
    samples.sort(key=lambda item: item["sample_id"])
    return samples


def default_materials_paths(source_id: str) -> list[Path]:
    """按项目实际训练资产位置解析本源已批准示例（可被 --materials-dir 覆盖）。"""
    training = PROJECT_ROOT / "training"
    if source_id == "mysql-lzh-monitor":
        path = training / "mysql_lzh_monitor" / "sql_examples.json"
        return [path] if path.is_file() else []
    if source_id == "postgresql-main":
        paths = [
            *sorted(training.glob("f5_level2_batch*.json")),
            *sorted(training.glob("f5_level3_batch*.json")),
        ]
        return paths
    path = training / source_id / "sql_examples.json"
    return [path] if path.is_file() else []


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 真实数据库只读验证（尽力而为，失败不阻塞生成）
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect_read_only(record: Any, username: str, password: str):
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
        return connection

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
    return connection


def verify_samples_read_only(
    catalog: DataSourceCatalog,
    source_id: str,
    samples: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str]:
    """只读执行已批准 SQL 并记录结果；连接/凭据不可用时返回跳过原因。"""
    try:
        record = catalog.require(source_id)
        username, password = catalog.credentials(source_id)
    except Exception as exc:
        return {}, f"凭据/目录不可用: {type(exc).__name__}"

    connection = None
    try:
        connection = _connect_read_only(record, username, password)
    except Exception as exc:
        return {}, f"连接失败: {type(exc).__name__}"

    results: dict[str, dict[str, Any]] = {}
    try:
        cursor = connection.cursor()
        try:
            for sample in samples:
                try:
                    cursor.execute(sample["sql"])
                    columns = (
                        [description[0] for description in cursor.description]
                        if cursor.description
                        else []
                    )
                    rows = cursor.fetchmany(20)
                    results[sample["sample_id"]] = {
                        "verified": True,
                        "read_only": True,
                        "columns": columns[:20],
                        "row_count_sampled": len(rows),
                    }
                except Exception as exc:
                    results[sample["sample_id"]] = {
                        "verified": False,
                        "read_only": True,
                        "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                    }
        finally:
            cursor.close()
        return results, "verified"
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def _stable_question_id(
    source_id: str,
    sample_id: str,
    text: str,
) -> str:
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
) -> dict[str, Any]:
    """执行生成并写入资产，返回摘要。"""
    catalog = _open_catalog(catalog_path)
    record = catalog.require(source_id)

    resolved_metadata = (
        metadata_path
        if metadata_path is not None
        else Path(record.metadata_path)
    )
    materials = (
        sorted(Path(materials_dir).glob("*.json"))
        if materials_dir
        else default_materials_paths(source_id)
    )
    samples = load_approved_samples(materials)

    seen: set[str] = set()
    questions: list[dict[str, Any]] = []
    for sample in samples:
        if len(questions) >= max_questions:
            break
        text = generalize_question(sample["question"], sample["sql"])
        if text in seen:
            continue
        seen.add(text)
        entry: dict[str, Any] = {
            "id": _stable_question_id(source_id, sample["sample_id"], text),
            "text": text,
            "enabled": True,
            "related_tables": sample["tables"],
            "related_sample_id": sample["sample_id"],
        }
        if sample["expected_behavior"]:
            entry["expected_behavior"] = sample["expected_behavior"]
        questions.append(entry)

    if no_db_verify:
        verification_note = "skipped"
        verification: dict[str, dict[str, Any]] = {}
    else:
        verification, verification_note = verify_samples_read_only(
            catalog,
            source_id,
            samples,
        )
        for question in questions:
            sample_id = question.get("related_sample_id")
            if sample_id in verification:
                question["verification"] = verification[sample_id]
            else:
                question["verification"] = {
                    "verified": False,
                    "read_only": True,
                    "error": "verification skipped",
                }

    basis: dict[str, Any] = {
        "metadata_path": str(resolved_metadata),
        "metadata_sha256": (
            _file_sha256(resolved_metadata)
            if resolved_metadata.is_file()
            else ""
        ),
        "sql_examples_paths": [str(path) for path in materials],
        "sql_examples_sha256": (
            _files_sha256(materials) if materials else ""
        ),
        "approved_sample_count": len(samples),
        "generated_question_count": len(questions),
        "db_verification": verification_note,
        "summary": (
            f"基于 {len(samples)} 条已批准 SQL 示例与已发布 Metadata 生成 "
            f"{len(questions)} 条推荐问题；来源：{'; '.join(str(p) for p in materials) or '无'}"
        ),
    }

    directory = build_question_directory(
        source_id,
        questions,
        asset_version=asset_version,
        generated_at=_utc_now_iso(),
        generator=GENERATOR_NAME,
        basis=basis,
    )
    output = write_question_directory(directory, root=root)

    return {
        "source_id": source_id,
        "asset_version": asset_version,
        "asset_path": str(output),
        "approved_sample_count": len(samples),
        "generated_question_count": len(questions),
        "db_verification": verification_note,
        "verified_questions": (
            sum(1 for item in verification.values() if item.get("verified"))
            if verification
            else 0
        ),
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
        help="跳过真实数据库只读验证",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=100,
        help="最多生成问题数（默认 100）",
    )
    parser.add_argument("--asset-version", default="v1", help="资产版本号")
    args = parser.parse_args(argv)

    summary = generate_questions(
        source_id=args.source_id,
        root=Path(args.root) if args.root else None,
        catalog_path=args.catalog,
        materials_dir=args.materials_dir,
        metadata_path=(
            Path(args.metadata_path) if args.metadata_path else None
        ),
        no_db_verify=args.no_db_verify,
        max_questions=args.max_questions,
        asset_version=args.asset_version,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

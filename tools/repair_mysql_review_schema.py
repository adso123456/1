"""一次性修复 MySQL 审核记录 schema 不一致。

背景：早期迁移为 MySQL 写入 schema_name='' 的审核行，后续 discover/画像
统一使用 schema_name='lzh_monitor'，导致审核运行按 (schema, table) 找不到
原行，创建了重复行，并把原 effective=active 的行误标为 missing。

安全约束：
  - --source-id 必填，只处理显式指定的 MySQL 数据源；
  - 执行前校验：数据源存在、database_type == mysql、
    规范 schema 与 Catalog 数据库身份一致、审核表结构符合预期；
  - --dry-run 执行完整计算但最终回滚，仅输出预计修改数量；
  - 默认在单个事务中执行，异常时回滚。

幂等，可重复运行。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


REQUIRED_REVIEW_COLUMNS = (
    "source_id",
    "schema_name",
    "table_name",
    "effective_decision",
    "decision_source",
    "decision_reason",
    "availability_status",
    "quality_metrics_json",
    "structure_fingerprint",
    "data_fingerprint",
    "review_version",
    "last_profiled_at",
    "reviewed_by",
    "business_group",
    "group_confidence",
    "compared_tables_json",
    "group_reason",
    "proposed_decision",
    "proposed_score",
    "proposed_reason",
    "created_at",
    "updated_at",
)


def _load_source_identity(
    connection: sqlite3.Connection,
    source_id: str,
) -> tuple[str, str, str] | None:
    row = connection.execute(
        "SELECT database_type, database_name, schema_name "
        "FROM data_sources WHERE source_id=?",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""))


def _validate_review_table(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='data_source_table_reviews'"
    ).fetchone()
    if table is None:
        raise ValueError("catalog 缺少 data_source_table_reviews 表，请先升级 schema")
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(data_source_table_reviews)"
        ).fetchall()
    }
    missing = sorted(set(REQUIRED_REVIEW_COLUMNS) - columns)
    if missing:
        raise ValueError(
            "data_source_table_reviews 缺少列：" + "、".join(missing)
        )


def _pick(existing_value: object, fallback_value: object) -> object:
    if existing_value not in (None, "", 0, "[]", "{}"):
        return existing_value
    return fallback_value


def repair_mysql_review_schema(
    db_path: Path,
    *,
    source_id: str,
    canonical_schema: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """合并 MySQL 空 schema 审核行并删除重复行。

    canonical_schema 缺省时从 Catalog 的 database_name 解析；
    与数据库身份不一致会直接拒绝执行。
    dry_run=True 时完整计算但回滚，不产生任何写入。
    """
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        identity = _load_source_identity(connection, source_id)
        if identity is None:
            raise ValueError(f"数据源不存在：{source_id}")
        database_type, database_name, _ = identity
        if database_type != "mysql":
            raise ValueError(
                f"数据源 {source_id} 不是 MySQL（database_type={database_type!r}），"
                "该修复只适用于 MySQL 数据源"
            )
        resolved_schema = canonical_schema or database_name
        if resolved_schema != database_name:
            raise ValueError(
                f"规范 schema {resolved_schema!r} 与数据库身份 "
                f"{database_name!r} 不一致，拒绝执行"
            )
        _validate_review_table(connection)

        empty_rows = connection.execute(
            "SELECT * FROM data_source_table_reviews "
            "WHERE source_id=? AND schema_name=''",
            (source_id,),
        ).fetchall()
        merged = 0
        deleted = 0
        promoted = 0
        for empty in empty_rows:
            table = str(empty["table_name"])
            canonical = connection.execute(
                "SELECT * FROM data_source_table_reviews "
                "WHERE source_id=? AND schema_name=? AND table_name=?",
                (source_id, resolved_schema, table),
            ).fetchone()
            if canonical is None:
                # 只有空 schema 行：直接升级为规范 schema。
                connection.execute(
                    "UPDATE data_source_table_reviews SET schema_name=? "
                    "WHERE source_id=? AND schema_name='' AND table_name=?",
                    (resolved_schema, source_id, table),
                )
                merged += 1
                continue
            updates: dict[str, object] = {}
            # 原迁移决策是 active；规范行若还是 pending（本轮误建），升级回去。
            if (
                str(empty["effective_decision"] or "") == "active"
                and str(canonical["effective_decision"] or "") != "active"
            ):
                updates["effective_decision"] = "active"
                updates["decision_source"] = "migration"
                updates["decision_reason"] = "existing_selected_scope"
                promoted += 1
            for column in (
                "availability_status",
                "quality_metrics_json",
                "structure_fingerprint",
                "data_fingerprint",
                "review_version",
                "last_profiled_at",
                "reviewed_by",
                "business_group",
                "group_confidence",
                "compared_tables_json",
                "group_reason",
                "proposed_decision",
                "proposed_score",
                "proposed_reason",
            ):
                # 规范行优先（本轮画像/建议较新），为空则回填空 schema 行。
                current = canonical[column]
                fallback = empty[column]
                value = _pick(current, fallback)
                if column == "availability_status":
                    value = "present"
                updates[column] = value
            if updates:
                sets = ", ".join(f"{column}=?" for column in updates)
                connection.execute(
                    f"UPDATE data_source_table_reviews SET {sets}, updated_at=? "
                    "WHERE source_id=? AND schema_name=? AND table_name=?",
                    (
                        *updates.values(),
                        canonical["updated_at"],
                        source_id,
                        resolved_schema,
                        table,
                    ),
                )
            connection.execute(
                "DELETE FROM data_source_table_reviews "
                "WHERE source_id=? AND schema_name='' AND table_name=?",
                (source_id, table),
            )
            merged += 1
            deleted += 1
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "source_id": source_id,
        "canonical_schema": resolved_schema,
        "dry_run": bool(dry_run),
        "merged_or_upgraded": merged,
        "deleted_empty_rows": deleted,
        "promoted_to_active": promoted,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="修复 MySQL 审核记录 schema 不一致")
    parser.add_argument("--db", required=True, help="catalog.sqlite3 路径")
    parser.add_argument(
        "--source-id",
        required=True,
        help="目标数据源 source_id（仅 MySQL，必填）",
    )
    parser.add_argument(
        "--canonical-schema",
        help="规范 schema（缺省取该数据源的数据库名）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只计算并输出预计修改数量，不落库",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"catalog 不存在：{db_path}", file=sys.stderr)
        return 1
    result = repair_mysql_review_schema(
        db_path,
        source_id=args.source_id,
        canonical_schema=args.canonical_schema,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

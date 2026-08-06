"""一次性修复 MySQL 审核记录 schema 不一致。

背景：早期迁移为 MySQL 写入 schema_name='' 的审核行，后续 discover/画像
统一使用 schema_name='lzh_monitor'，导致审核运行按 (schema, table) 找不到
原行，创建了重复行，并把原 effective=active 的行误标为 missing。

本脚本把同一张表的两行合并为规范 schema（数据库名）行：
  - 保留原迁移的 effective_decision=active；
  - 保留较新的质量指标 / 建议字段；
  - 删除空 schema 行。
幂等，可重复运行。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


REVIEW_COLUMNS = (
    "source_id",
    "schema_name",
    "table_name",
    "business_group",
    "group_confidence",
    "compared_tables_json",
    "group_reason",
    "proposed_decision",
    "proposed_score",
    "proposed_reason",
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
    "created_at",
    "updated_at",
)


def _pick(existing_value: object, fallback_value: object) -> object:
    if existing_value not in (None, "", 0, "[]", "{}"):
        return existing_value
    return fallback_value


def repair_mysql_review_schema(
    db_path: Path,
    *,
    source_id: str = "mysql-lzh-monitor",
    canonical_schema: str = "lzh_monitor",
) -> dict[str, int]:
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    merged = 0
    deleted = 0
    promoted = 0
    try:
        empty_rows = connection.execute(
            "SELECT * FROM data_source_table_reviews "
            "WHERE source_id=? AND schema_name=''",
            (source_id,),
        ).fetchall()
        for empty in empty_rows:
            table = str(empty["table_name"])
            canonical = connection.execute(
                "SELECT * FROM data_source_table_reviews "
                "WHERE source_id=? AND schema_name=? AND table_name=?",
                (source_id, canonical_schema, table),
            ).fetchone()
            if canonical is None:
                # 只有空 schema 行：直接升级为规范 schema。
                connection.execute(
                    "UPDATE data_source_table_reviews SET schema_name=? "
                    "WHERE source_id=? AND schema_name='' AND table_name=?",
                    (canonical_schema, source_id, table),
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
                        canonical_schema,
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
        connection.commit()
    finally:
        connection.close()
    return {
        "merged_or_upgraded": merged,
        "deleted_empty_rows": deleted,
        "promoted_to_active": promoted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="修复 MySQL 审核记录 schema 不一致")
    parser.add_argument("--db", help="catalog.sqlite3 路径（默认读取 DATA_SOURCE_CATALOG_PATH）")
    args = parser.parse_args()
    if args.db:
        db_path = Path(args.db).resolve()
    else:
        import os

        configured = os.getenv("DATA_SOURCE_CATALOG_PATH", "").strip()
        base = Path.cwd()
        db_path = (
            base / configured
            if configured
            else base / "agent_data" / "data_sources" / "catalog.sqlite3"
        )
    if not db_path.exists():
        print(f"catalog 不存在：{db_path}", file=sys.stderr)
        return 1
    result = repair_mysql_review_schema(db_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

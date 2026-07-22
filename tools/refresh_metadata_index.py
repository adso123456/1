from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import build_db_kwargs, validate_db_config


SCHEMA_VERSION = "1.0"
GENERATOR_VERSION = "f6-2a-1"
COMPATIBLE_FIELDS = ("table", "table_comment", "column", "type", "comment")
REQUIRED_FIELDS = frozenset(COMPATIBLE_FIELDS)
OBJECT_TYPES = frozenset(("table", "partitioned_table", "view", "materialized_view"))


class MetadataValidationError(ValueError):
    """Metadata 行不满足确定性索引约束。"""


class ReadOnlyGateError(RuntimeError):
    """数据库会话未通过只读门禁。"""


def _clean_required_text(row: Mapping[str, Any], field: str, row_number: int) -> str:
    if field not in row:
        raise MetadataValidationError(f"第 {row_number} 行缺少必要字段: {field}")
    value = row[field]
    if not isinstance(value, str):
        raise MetadataValidationError(f"第 {row_number} 行字段 {field} 必须是字符串")
    cleaned = value.strip()
    if field in {"table", "column", "type"} and not cleaned:
        raise MetadataValidationError(f"第 {row_number} 行字段 {field} 不得为空")
    return cleaned


def normalize_metadata_rows(rows: Any) -> list[dict[str, Any]]:
    """校验、清洗并按表名、字段序号、字段名稳定排序。"""
    if not isinstance(rows, list):
        raise MetadataValidationError("Metadata 根节点必须是数组")

    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    table_comments: dict[str, str] = {}

    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise MetadataValidationError(f"第 {row_number} 行必须是对象")

        item = {
            field: _clean_required_text(row, field, row_number)
            for field in COMPATIBLE_FIELDS
        }
        identity = (item["table"], item["column"])
        if identity in identities:
            raise MetadataValidationError(
                f"存在重复字段: {item['table']}.{item['column']}"
            )
        identities.add(identity)

        known_comment = table_comments.setdefault(item["table"], item["table_comment"])
        if known_comment != item["table_comment"]:
            raise MetadataValidationError(
                f"同一表存在不一致的表注释: {item['table']}"
            )

        ordinal_position = row.get("ordinal_position")
        if ordinal_position is not None:
            if (
                not isinstance(ordinal_position, int)
                or isinstance(ordinal_position, bool)
                or ordinal_position <= 0
            ):
                raise MetadataValidationError(
                    f"第 {row_number} 行 ordinal_position 必须是正整数"
                )
            item["ordinal_position"] = ordinal_position

        object_type = row.get("object_type")
        if object_type is not None:
            if not isinstance(object_type, str) or object_type not in OBJECT_TYPES:
                raise MetadataValidationError(
                    f"第 {row_number} 行 object_type 非法: {object_type!r}"
                )
            item["object_type"] = object_type

        normalized.append(item)

    normalized.sort(
        key=lambda item: (
            item["table"],
            item.get("ordinal_position", sys.maxsize),
            item["column"],
        )
    )
    return normalized


def serialize_metadata_index(rows: Any) -> bytes:
    """生成现有运行时可读取的确定性五字段 JSON 索引。"""
    normalized = normalize_metadata_rows(rows)
    compatible_rows = [
        {field: row[field] for field in COMPATIBLE_FIELDS} for row in normalized
    ]
    text = json.dumps(compatible_rows, ensure_ascii=False, indent=2) + "\n"
    return text.encode("utf-8")


def extract_postgresql_metadata(connection: Any, schema: str = "public") -> list[dict[str, Any]]:
    """通过只读 PostgreSQL catalog 查询提取指定 schema 的字段结构。"""
    if not isinstance(schema, str) or not schema.strip():
        raise ValueError("schema 不得为空")
    schema = schema.strip()
    if schema in {"pg_catalog", "information_schema"} or schema.startswith("pg_temp_"):
        raise ValueError(f"不允许提取系统 schema: {schema}")

    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_read_only")
        status_row = cursor.fetchone()
        status = str(status_row[0]).strip().lower() if status_row else ""
        if status != "on":
            raise ReadOnlyGateError(
                f"数据库只读门禁失败，transaction_read_only={status or 'unknown'}"
            )

        cursor.execute(
            """
            SELECT
                cls.relname AS table_name,
                COALESCE(obj_description(cls.oid, 'pg_class'), '') AS table_comment,
                attr.attname AS column_name,
                format_type(attr.atttypid, attr.atttypmod) AS column_type,
                COALESCE(col_description(cls.oid, attr.attnum), '') AS column_comment,
                attr.attnum AS ordinal_position,
                CASE cls.relkind
                    WHEN 'r' THEN 'table'
                    WHEN 'p' THEN 'partitioned_table'
                    WHEN 'v' THEN 'view'
                    WHEN 'm' THEN 'materialized_view'
                END AS object_type
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            JOIN pg_catalog.pg_attribute AS attr ON attr.attrelid = cls.oid
            WHERE ns.nspname = %s
              AND cls.relkind IN ('r', 'p', 'v', 'm')
              AND attr.attnum > 0
              AND NOT attr.attisdropped
              AND ns.nspname <> 'pg_catalog'
              AND ns.nspname <> 'information_schema'
              AND ns.nspname NOT LIKE 'pg_temp_%%'
              AND ns.nspname NOT LIKE 'pg_toast_temp_%%'
            ORDER BY cls.relname, attr.attnum, attr.attname
            """,
            (schema,),
        )
        rows = cursor.fetchall()

    return normalize_metadata_rows(
        [
            {
                "table": row[0],
                "table_comment": row[1],
                "column": row[2],
                "type": row[3],
                "comment": row[4],
                "ordinal_position": row[5],
                "object_type": row[6],
            }
            for row in rows
        ]
    )


def _index_by_identity(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(row["table"], row["column"]): row for row in rows}


def _table_comments(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {row["table"]: row["table_comment"] for row in rows}


def diff_metadata_indexes(baseline_rows: Any, current_rows: Any) -> dict[str, Any]:
    """以 (table, column) 为身份比较两个五字段 Metadata 索引。"""
    baseline = normalize_metadata_rows(baseline_rows)
    current = normalize_metadata_rows(current_rows)
    old_by_id = _index_by_identity(baseline)
    new_by_id = _index_by_identity(current)
    old_tables = _table_comments(baseline)
    new_tables = _table_comments(current)

    old_table_names = set(old_tables)
    new_table_names = set(new_tables)
    old_ids = set(old_by_id)
    new_ids = set(new_by_id)
    shared_ids = old_ids & new_ids

    added_tables = [
        {"table": table, "old_table_comment": None, "new_table_comment": new_tables[table]}
        for table in sorted(new_table_names - old_table_names)
    ]
    removed_tables = [
        {"table": table, "old_table_comment": old_tables[table], "new_table_comment": None}
        for table in sorted(old_table_names - new_table_names)
    ]
    added_columns = [
        {
            "table": table,
            "column": column,
            "old_type": None,
            "new_type": new_by_id[(table, column)]["type"],
            "old_comment": None,
            "new_comment": new_by_id[(table, column)]["comment"],
        }
        for table, column in sorted(new_ids - old_ids)
    ]
    removed_columns = [
        {
            "table": table,
            "column": column,
            "old_type": old_by_id[(table, column)]["type"],
            "new_type": None,
            "old_comment": old_by_id[(table, column)]["comment"],
            "new_comment": None,
        }
        for table, column in sorted(old_ids - new_ids)
    ]
    changed_column_types = [
        {
            "table": table,
            "column": column,
            "old_type": old_by_id[(table, column)]["type"],
            "new_type": new_by_id[(table, column)]["type"],
        }
        for table, column in sorted(shared_ids)
        if old_by_id[(table, column)]["type"] != new_by_id[(table, column)]["type"]
    ]
    changed_column_comments = [
        {
            "table": table,
            "column": column,
            "old_comment": old_by_id[(table, column)]["comment"],
            "new_comment": new_by_id[(table, column)]["comment"],
        }
        for table, column in sorted(shared_ids)
        if old_by_id[(table, column)]["comment"]
        != new_by_id[(table, column)]["comment"]
    ]
    changed_table_comments = [
        {
            "table": table,
            "old_table_comment": old_tables[table],
            "new_table_comment": new_tables[table],
        }
        for table in sorted(old_table_names & new_table_names)
        if old_tables[table] != new_tables[table]
    ]
    unchanged_column_count = sum(
        old_by_id[identity]["type"] == new_by_id[identity]["type"]
        and old_by_id[identity]["comment"] == new_by_id[identity]["comment"]
        for identity in shared_ids
    )

    return {
        "added_tables": added_tables,
        "removed_tables": removed_tables,
        "added_columns": added_columns,
        "removed_columns": removed_columns,
        "changed_column_types": changed_column_types,
        "changed_column_comments": changed_column_comments,
        "changed_table_comments": changed_table_comments,
        "unchanged_table_count": sum(
            old_tables[table] == new_tables[table]
            for table in old_table_names & new_table_names
        ),
        "unchanged_column_count": unchanged_column_count,
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_metadata_manifest(
    rows: Any,
    index_content: bytes,
    baseline_content: bytes,
    *,
    datasource_id: str,
    dialect: str,
    schema: str,
    database_read_only: bool,
) -> dict[str, Any]:
    """构建不参与索引内容 SHA 的外部审查 Manifest。"""
    normalized = normalize_metadata_rows(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "datasource_id": datasource_id,
        "dialect": dialect,
        "schema": schema,
        "object_types": sorted(
            {row["object_type"] for row in normalized if "object_type" in row}
        ),
        "table_count": len({row["table"] for row in normalized}),
        "column_count": len(normalized),
        "index_sha256": _sha256(index_content),
        "baseline_sha256": _sha256(baseline_content),
        "database_read_only": database_read_only,
        "generator_version": GENERATOR_VERSION,
    }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _has_changes(diff: Mapping[str, Any]) -> bool:
    return any(
        diff[key]
        for key in (
            "added_tables",
            "removed_tables",
            "added_columns",
            "removed_columns",
            "changed_column_types",
            "changed_column_comments",
            "changed_table_comments",
        )
    )


def _summary_bytes(manifest: Mapping[str, Any], diff: Mapping[str, Any]) -> bytes:
    lines = [
        "# PostgreSQL Metadata 差异审查摘要",
        "",
        f"- 数据源：`{manifest['datasource_id']}`",
        f"- 方言：`{manifest['dialect']}`",
        f"- Schema：`{manifest['schema']}`",
        f"- 数据库只读：`{manifest['database_read_only']}`",
        f"- 新索引 SHA256：`{manifest['index_sha256']}`",
        f"- 基线 SHA256：`{manifest['baseline_sha256']}`",
        f"- 新索引表数：{manifest['table_count']}",
        f"- 新索引字段数：{manifest['column_count']}",
        f"- 新增表：{len(diff['added_tables'])}",
        f"- 删除表：{len(diff['removed_tables'])}",
        f"- 新增字段：{len(diff['added_columns'])}",
        f"- 删除字段：{len(diff['removed_columns'])}",
        f"- 字段类型变化：{len(diff['changed_column_types'])}",
        f"- 字段注释变化：{len(diff['changed_column_comments'])}",
        f"- 表注释变化：{len(diff['changed_table_comments'])}",
        f"- 存在 Metadata 差异：{_has_changes(diff)}",
        "",
        "> 本报告仅供人工审查，不会替换正式索引或写入 Memory。",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def validate_output_directory(output_dir: Path) -> Path:
    resolved = output_dir.expanduser().resolve()
    if resolved == PROJECT_ROOT or resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("输出目录必须位于项目目录之外")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("输出路径必须是目录")
        if any(resolved.iterdir()):
            raise ValueError("输出目录必须不存在或为空，禁止覆盖非空目录")
    return resolved


def write_audit_package(
    output_dir: Path,
    rows: Any,
    baseline_content: bytes,
    baseline_rows: Any,
    *,
    datasource_id: str,
    dialect: str,
    schema: str,
    database_read_only: bool,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    resolved = validate_output_directory(output_dir)
    index_content = serialize_metadata_index(rows)
    diff = diff_metadata_indexes(baseline_rows, rows)
    manifest = build_metadata_manifest(
        rows,
        index_content,
        baseline_content,
        datasource_id=datasource_id,
        dialect=dialect,
        schema=schema,
        database_read_only=database_read_only,
    )

    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / "column_metadata_index.new.json").write_bytes(index_content)
    (resolved / "metadata_index_diff.json").write_bytes(_json_bytes(diff))
    (resolved / "metadata_manifest.json").write_bytes(_json_bytes(manifest))
    (resolved / "metadata_index_summary.md").write_bytes(
        _summary_bytes(manifest, diff)
    )
    return manifest, diff, index_content


def _load_index(content: bytes, label: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataValidationError(f"{label} 不是有效的 UTF-8 JSON") from exc
    return normalize_metadata_rows(value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 PostgreSQL Metadata 只读差异审查包")
    parser.add_argument("--datasource-id", default="postgresql-main")
    parser.add_argument("--dialect", default="postgresql", choices=("postgresql",))
    parser.add_argument("--schema", default="public")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=PROJECT_ROOT / "agent_data" / "column_metadata_index.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = validate_output_directory(args.output_dir)
    baseline_path = args.baseline.expanduser().resolve()
    baseline_content = baseline_path.read_bytes()
    baseline_rows = _load_index(baseline_content, "baseline")

    db_kwargs = build_db_kwargs()
    validate_db_config(db_kwargs)

    import psycopg2

    connection = psycopg2.connect(**db_kwargs)
    try:
        rows = extract_postgresql_metadata(connection, args.schema)
    finally:
        connection.close()

    manifest, diff, index_content = write_audit_package(
        output_dir,
        rows,
        baseline_content,
        baseline_rows,
        datasource_id=args.datasource_id,
        dialect=args.dialect,
        schema=args.schema,
        database_read_only=True,
    )

    regenerated = serialize_metadata_index(normalize_metadata_rows(list(reversed(rows))))
    deterministic = regenerated == index_content
    if not deterministic:
        raise RuntimeError("同批 Metadata 的确定性重生成校验失败")

    print("DB_READ_ONLY_STATUS: on")
    print(f"OUTPUT_DIRECTORY: {output_dir}")
    print(f"GENERATED_INDEX_SHA256: {manifest['index_sha256']}")
    print(f"HAS_METADATA_CHANGES: {_has_changes(diff)}")
    print("DETERMINISTIC_REGEN_RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

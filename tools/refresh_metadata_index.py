from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
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
SCOPE_CLASSIFICATIONS = frozenset(
    (
        "BASELINE_INCLUDED",
        "NEW_EXCLUDED_STAGING",
        "NEW_EXCLUDED_BACKUP",
        "NEW_EXCLUDED_SYSTEM",
        "NEW_DEFERRED_BUSINESS_VIEW",
        "NEW_REVIEW_REQUIRED",
    )
)
DEFAULT_SCOPE_POLICY = PROJECT_ROOT / "config" / "postgresql_metadata_scope.json"
DEFAULT_FORMAL_CHROMA = Path(r"E:\3\_runtime\vanna-level1\vanna_data")


class MetadataValidationError(ValueError):
    """Metadata 行不满足确定性索引约束。"""


class ReadOnlyGateError(RuntimeError):
    """数据库会话未通过只读门禁。"""


def _clean_required_text(
    row: Mapping[str, Any],
    field: str,
    row_number: int,
    *,
    normalize_comment_line_endings: bool,
) -> str:
    if field not in row:
        raise MetadataValidationError(f"第 {row_number} 行缺少必要字段: {field}")
    value = row[field]
    if not isinstance(value, str):
        raise MetadataValidationError(f"第 {row_number} 行字段 {field} 必须是字符串")
    if normalize_comment_line_endings and field in {"table_comment", "comment"}:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = value.strip()
    if field in {"table", "column", "type"} and not cleaned:
        raise MetadataValidationError(f"第 {row_number} 行字段 {field} 不得为空")
    return cleaned


def _normalize_metadata_rows(
    rows: Any, *, normalize_comment_line_endings: bool
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise MetadataValidationError("Metadata 根节点必须是数组")

    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    table_comments: dict[str, str] = {}

    for row_number, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise MetadataValidationError(f"第 {row_number} 行必须是对象")

        item = {
            field: _clean_required_text(
                row,
                field,
                row_number,
                normalize_comment_line_endings=normalize_comment_line_endings,
            )
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


def normalize_metadata_rows(rows: Any) -> list[dict[str, Any]]:
    """校验、统一注释换行并按表名、字段序号、字段名稳定排序。"""
    return _normalize_metadata_rows(rows, normalize_comment_line_endings=True)


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

    return _normalize_metadata_rows(
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
        ],
        normalize_comment_line_endings=False,
    )


def extract_deferred_view_details(
    connection: Any, schema: str, view_names: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """只读获取暂缓视图定义摘要；不查询视图数据。"""
    names = sorted(set(view_names))
    if not names:
        return {}
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
            SELECT cls.relname, cls.relkind, pg_get_viewdef(cls.oid, true)
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = %s
              AND cls.relname = ANY(%s)
            ORDER BY cls.relname
            """,
            (schema, names),
        )
        rows = cursor.fetchall()

    details: dict[str, dict[str, Any]] = {}
    for table_name, relkind, definition in rows:
        if relkind != "v":
            raise MetadataValidationError(f"暂缓对象不是普通视图: {table_name}")
        normalized_definition = str(definition).replace("\r\n", "\n").replace("\r", "\n").strip()
        dependencies = sorted(
            set(
                re.findall(
                    r'(?i)\b(?:from|join)\s+(?:"?[A-Za-z_][\w$]*"?\.)?"?([A-Za-z_][\w$]*)"?',
                    normalized_definition,
                )
            )
        )
        details[str(table_name)] = {
            "view_definition_sha256": _sha256(normalized_definition.encode("utf-8")),
            "dependency_summary": dependencies,
        }
    missing = sorted(set(names) - set(details))
    if missing:
        raise MetadataValidationError("暂缓视图不存在: " + ", ".join(missing))
    return details


def _index_by_identity(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(row["table"], row["column"]): row for row in rows}


def _table_comments(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {row["table"]: row["table_comment"] for row in rows}


def diff_metadata_indexes(baseline_rows: Any, current_rows: Any) -> dict[str, Any]:
    """以 (table, column) 为身份比较两个五字段 Metadata 索引。"""
    baseline = normalize_metadata_rows(baseline_rows)
    current = normalize_metadata_rows(current_rows)
    baseline_original = _normalize_metadata_rows(
        baseline_rows, normalize_comment_line_endings=False
    )
    current_original = _normalize_metadata_rows(
        current_rows, normalize_comment_line_endings=False
    )
    old_by_id = _index_by_identity(baseline)
    new_by_id = _index_by_identity(current)
    old_original_by_id = _index_by_identity(baseline_original)
    new_original_by_id = _index_by_identity(current_original)
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
    line_ending_only_column_comments = [
        {
            "table": table,
            "column": column,
            "old_comment": old_original_by_id[(table, column)]["comment"],
            "new_comment": new_original_by_id[(table, column)]["comment"],
        }
        for table, column in sorted(shared_ids)
        if old_original_by_id[(table, column)]["comment"]
        != new_original_by_id[(table, column)]["comment"]
        and old_by_id[(table, column)]["comment"]
        == new_by_id[(table, column)]["comment"]
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
        "line_ending_only_column_comments": line_ending_only_column_comments,
        "changed_table_comments": changed_table_comments,
        "unchanged_table_count": sum(
            old_tables[table] == new_tables[table]
            for table in old_table_names & new_table_names
        ),
        "unchanged_column_count": unchanged_column_count,
    }


def load_metadata_scope_policy(path: Path | str = DEFAULT_SCOPE_POLICY) -> dict[str, Any]:
    """读取并校验简单、版本化的 PostgreSQL Metadata 范围策略。"""
    policy_path = Path(path)
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataValidationError(f"范围策略不是有效的 UTF-8 JSON: {policy_path}") from exc
    if not isinstance(policy, dict):
        raise MetadataValidationError("范围策略根节点必须是对象")

    expected_scalars = {
        "schema_version": "1.0",
        "datasource_id": "postgresql-main",
        "dialect": "postgresql",
        "schema": "public",
        "preserve_baseline_tables": True,
    }
    for field, expected in expected_scalars.items():
        if policy.get(field) != expected:
            raise MetadataValidationError(
                f"范围策略字段 {field} 必须为 {expected!r}"
            )

    for field in (
        "exclude_exact_tables",
        "exclude_prefixes",
        "exclude_name_patterns",
        "deferred_new_tables",
    ):
        values = policy.get(field)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
        ):
            raise MetadataValidationError(f"范围策略字段 {field} 必须是无重复非空字符串数组")
        policy[field] = sorted(values)

    for pattern in policy["exclude_name_patterns"]:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise MetadataValidationError(f"非法排除正则: {pattern}") from exc
    return policy


def _rows_by_table(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["table"])].append(dict(row))
    return dict(grouped)


def classify_metadata_scope(
    raw_rows: Any,
    baseline_rows: Any,
    policy: Mapping[str, Any],
    *,
    deferred_view_details: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按 baseline 优先规则分类数据库对象，并构建正式候选范围。"""
    raw = _normalize_metadata_rows(
        raw_rows, normalize_comment_line_endings=False
    )
    baseline = _normalize_metadata_rows(
        baseline_rows, normalize_comment_line_endings=False
    )
    baseline_tables = {row["table"] for row in baseline}
    raw_by_table = _rows_by_table(raw)
    exact_excludes = set(policy["exclude_exact_tables"])
    prefixes = tuple(policy["exclude_prefixes"])
    patterns = tuple(re.compile(value) for value in policy["exclude_name_patterns"])
    deferred = set(policy["deferred_new_tables"])
    view_details = deferred_view_details or {}

    classified: dict[str, list[dict[str, Any]]] = {
        classification: [] for classification in SCOPE_CLASSIFICATIONS
    }
    candidate_rows: list[dict[str, Any]] = []

    for table_name in sorted(raw_by_table):
        table_rows = raw_by_table[table_name]
        object_types = {row.get("object_type", "") for row in table_rows}
        if len(object_types) != 1:
            raise MetadataValidationError(f"表 {table_name} 存在不一致的 object_type")
        object_type = next(iter(object_types))

        if table_name in baseline_tables:
            classification = "BASELINE_INCLUDED"
            candidate_rows.extend(table_rows)
        elif table_name in exact_excludes:
            classification = "NEW_EXCLUDED_SYSTEM"
        elif table_name.startswith(prefixes):
            classification = "NEW_EXCLUDED_STAGING"
        elif any(pattern.search(table_name) for pattern in patterns):
            classification = "NEW_EXCLUDED_BACKUP"
        elif table_name in deferred:
            classification = "NEW_DEFERRED_BUSINESS_VIEW"
        else:
            classification = "NEW_REVIEW_REQUIRED"

        entry: dict[str, Any] = {
            "table": table_name,
            "classification": classification,
            "object_type": object_type,
            "column_count": len(table_rows),
        }
        if classification == "NEW_DEFERRED_BUSINESS_VIEW" and table_name in view_details:
            entry.update(dict(view_details[table_name]))
        classified[classification].append(entry)

    candidate = _normalize_metadata_rows(
        candidate_rows, normalize_comment_line_endings=False
    )
    candidate_diff = diff_metadata_indexes(baseline, candidate)
    added_by_table: dict[str, list[str]] = defaultdict(list)
    for change in candidate_diff["added_columns"]:
        added_by_table[change["table"]].append(change["column"])

    review = {
        "raw_table_count": len(raw_by_table),
        "raw_column_count": len(raw),
        "baseline_table_count": len(baseline_tables),
        "baseline_column_count": len(baseline),
        "candidate_table_count": len({row["table"] for row in candidate}),
        "candidate_column_count": len(candidate),
        "baseline_included_tables": classified["BASELINE_INCLUDED"],
        "excluded_staging_tables": classified["NEW_EXCLUDED_STAGING"],
        "excluded_backup_tables": classified["NEW_EXCLUDED_BACKUP"],
        "excluded_system_tables": classified["NEW_EXCLUDED_SYSTEM"],
        "deferred_business_views": classified["NEW_DEFERRED_BUSINESS_VIEW"],
        "review_required_tables": classified["NEW_REVIEW_REQUIRED"],
        "excluded_staging_column_count": sum(
            item["column_count"] for item in classified["NEW_EXCLUDED_STAGING"]
        ),
        "excluded_backup_column_count": sum(
            item["column_count"] for item in classified["NEW_EXCLUDED_BACKUP"]
        ),
        "excluded_system_column_count": sum(
            item["column_count"] for item in classified["NEW_EXCLUDED_SYSTEM"]
        ),
        "deferred_business_view_column_count": sum(
            item["column_count"]
            for item in classified["NEW_DEFERRED_BUSINESS_VIEW"]
        ),
        "new_columns_on_baseline_tables": [
            {
                "table": table,
                "column_count": len(columns),
                "columns": sorted(columns),
            }
            for table, columns in sorted(added_by_table.items())
        ],
        "new_columns_on_baseline_table_count": len(added_by_table),
        "new_columns_on_baseline_column_count": sum(
            len(columns) for columns in added_by_table.values()
        ),
    }
    return candidate, review


def directory_tree_sha256(path: Path | str) -> str:
    """按相对路径和文件内容计算确定性目录 SHA。"""
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {root}")
    digest = hashlib.sha256()
    files = sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for item in files:
        relative = item.relative_to(root).as_posix().encode("utf-8")
        content = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def read_formal_ddl_memory_snapshot(
    formal_chroma: Path | str, temporary_parent: Path | str
) -> tuple[Any, ...]:
    """复制正式 Chroma 后通过现有适配层只读清点副本。"""
    source = Path(formal_chroma).resolve()
    temp_parent = Path(temporary_parent).resolve()
    temp_parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix="f6-2b-chroma-snapshot-", dir=temp_parent)
    )
    snapshot = temporary_root / "vanna_data"
    client: Any = None
    try:
        shutil.copytree(source, snapshot)
        import chromadb

        from training.sop.ddl_memory_adapter import DdlMemoryChromaAdapter

        client = chromadb.PersistentClient(path=str(snapshot))
        collection = client.get_collection("tool_memories")
        records = DdlMemoryChromaAdapter(collection).snapshot_records()
        return records
    finally:
        if client is not None:
            system = getattr(client, "_system", None)
            stop = getattr(system, "stop", None)
            if callable(stop):
                stop()
        client = None
        gc.collect()
        shutil.rmtree(temporary_root)


def build_ddl_memory_update_plan(
    candidate_rows: Any,
    candidate_diff: Mapping[str, Any],
    existing_records: Sequence[Any],
) -> dict[str, Any]:
    """复用既有 DDL 身份与计划模块构建只读更新计划。"""
    from train_step3 import build_all_table_ddls, group_tables
    from training.sop.ddl_memory_identity import (
        DdlMemoryIdentityInput,
        build_ddl_memory_identity,
    )
    from training.sop.ddl_memory_plan import build_ddl_memory_plan

    candidate = normalize_metadata_rows(candidate_rows)
    tables = group_tables(candidate)
    generated, geometry_skipped = build_all_table_ddls(tables)
    desired = [
        build_ddl_memory_identity(
            DdlMemoryIdentityInput(
                source_id="postgres_water",
                schema_name="public",
                object_type="table",
                object_name=item["table"],
            ),
            item["ddl"],
        )
        for item in generated
    ]
    plan = build_ddl_memory_plan(desired, existing_records)
    existing_by_id = {record.record_id: record for record in existing_records}

    def changes_for(table: str, key: str) -> list[dict[str, Any]]:
        return [
            dict(change)
            for change in candidate_diff[key]
            if change.get("table") == table
        ]

    actions: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for action in plan.actions:
        basic = {
            "action": action.action,
            "table": action.object_name,
            "current_record_id": (
                action.record_id if action.record_id in existing_by_id else None
            ),
            "target_record_id": (
                action.record_id if action.action != "removed" else None
            ),
        }
        actions.append(basic)
        if action.action != "changed":
            continue
        current = existing_by_id[action.record_id]
        item = {
            **basic,
            "old_content_sha256": _sha256(current.document.encode("utf-8")),
            "new_content_sha256": _sha256(
                str(action.normalized_ddl).encode("utf-8")
            ),
            "added_columns": changes_for(action.object_name, "added_columns"),
            "removed_columns": changes_for(action.object_name, "removed_columns"),
            "type_changes": changes_for(
                action.object_name, "changed_column_types"
            ),
            "comment_changes": changes_for(
                action.object_name, "changed_column_comments"
            ),
        }
        changed.append(item)

    return {
        "plan_version": plan.plan_version,
        "desired_count": plan.desired_count,
        "managed_existing_count": plan.managed_existing_count,
        "unmanaged_existing_count": plan.unmanaged_existing_count,
        "create": plan.create_count,
        "unchanged": plan.unchanged_count,
        "changed": plan.changed_count,
        "removed": plan.removed_count,
        "geometry_columns_skipped": geometry_skipped,
        "plan_sha256": plan.plan_sha256,
        "actions": actions,
        "changed_items": changed,
    }


def validate_f6_2b_expected_results(
    review: Mapping[str, Any],
    candidate_diff: Mapping[str, Any],
    ddl_plan: Mapping[str, Any],
) -> None:
    """冻结本次真实审查事实；任何偏差均阻断完成状态。"""
    expected = {
        "raw_table_count": (review["raw_table_count"], 167),
        "raw_column_count": (review["raw_column_count"], 3492),
        "baseline_table_count": (review["baseline_table_count"], 115),
        "baseline_column_count": (review["baseline_column_count"], 2572),
        "candidate_table_count": (review["candidate_table_count"], 115),
        "candidate_column_count": (review["candidate_column_count"], 2613),
        "excluded_staging_tables": (len(review["excluded_staging_tables"]), 43),
        "excluded_backup_tables": (len(review["excluded_backup_tables"]), 3),
        "excluded_system_tables": (len(review["excluded_system_tables"]), 3),
        "deferred_business_views": (len(review["deferred_business_views"]), 3),
        "review_required_tables": (len(review["review_required_tables"]), 0),
        "new_columns_on_baseline_table_count": (
            review["new_columns_on_baseline_table_count"],
            13,
        ),
        "new_columns_on_baseline_column_count": (
            review["new_columns_on_baseline_column_count"],
            41,
        ),
        "added_tables": (len(candidate_diff["added_tables"]), 0),
        "removed_tables": (len(candidate_diff["removed_tables"]), 0),
        "added_columns": (len(candidate_diff["added_columns"]), 41),
        "removed_columns": (len(candidate_diff["removed_columns"]), 0),
        "changed_column_types": (
            len(candidate_diff["changed_column_types"]),
            0,
        ),
        "changed_column_comments": (
            len(candidate_diff["changed_column_comments"]),
            0,
        ),
        "line_ending_only_column_comments": (
            len(candidate_diff["line_ending_only_column_comments"]),
            19,
        ),
        "ddl_plan_create": (ddl_plan["create"], 0),
        "ddl_plan_unchanged": (ddl_plan["unchanged"], 102),
        "ddl_plan_changed": (ddl_plan["changed"], 13),
        "ddl_plan_removed": (ddl_plan["removed"], 0),
    }
    mismatches = [
        f"{name}: actual={actual}, expected={wanted}"
        for name, (actual, wanted) in expected.items()
        if actual != wanted
    ]
    if mismatches:
        raise RuntimeError("F6-2B 事实门禁失败：" + "; ".join(mismatches))


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
            "line_ending_only_column_comments",
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


def _scope_summary_bytes(
    review: Mapping[str, Any], candidate_diff: Mapping[str, Any]
) -> bytes:
    lines = [
        "# PostgreSQL Metadata 范围审查摘要",
        "",
        f"- 原始数据库对象：{review['raw_table_count']} 张 / {review['raw_column_count']} 字段",
        f"- 正式候选范围：{review['candidate_table_count']} 张 / {review['candidate_column_count']} 字段",
        f"- 排除 staging：{len(review['excluded_staging_tables'])} 张",
        f"- 排除 backup：{len(review['excluded_backup_tables'])} 张",
        f"- 排除 PostGIS 系统对象：{len(review['excluded_system_tables'])} 张",
        f"- 暂缓新业务视图：{len(review['deferred_business_views'])} 张",
        f"- 待人工分类未知对象：{len(review['review_required_tables'])} 张",
        f"- 正式表新增字段：{len(candidate_diff['added_columns'])}",
        f"- 语义字段注释变化：{len(candidate_diff['changed_column_comments'])}",
        f"- 仅换行字段注释变化：{len(candidate_diff['line_ending_only_column_comments'])}",
        "",
        "## 正式表新增字段",
        "",
    ]
    for item in review["new_columns_on_baseline_tables"]:
        lines.append(
            f"- `{item['table']}`（{item['column_count']}）："
            + ", ".join(f"`{column}`" for column in item["columns"])
        )
    lines.extend(
        (
            "",
            "> 未替换正式 Metadata 索引；所有新增数据库对象均按范围策略排除或暂缓。",
            "",
        )
    )
    return "\n".join(lines).encode("utf-8")


def _ddl_plan_summary_bytes(ddl_plan: Mapping[str, Any]) -> bytes:
    lines = [
        "# DDL Memory 更新计划摘要",
        "",
        f"- create：{ddl_plan['create']}",
        f"- unchanged：{ddl_plan['unchanged']}",
        f"- changed：{ddl_plan['changed']}",
        f"- removed：{ddl_plan['removed']}",
        f"- 非 DDL Memory：{ddl_plan['unmanaged_existing_count']}（不参与计划）",
        f"- Plan SHA256：`{ddl_plan['plan_sha256']}`",
        "",
        "## changed 表",
        "",
    ]
    for item in ddl_plan["changed_items"]:
        lines.append(
            f"- `{item['table']}`：新增字段 {len(item['added_columns'])}，"
            f"删除字段 {len(item['removed_columns'])}，类型变化 {len(item['type_changes'])}，"
            f"注释变化 {len(item['comment_changes'])}"
        )
    lines.extend(("", "> 本计划未执行 Apply，正式 Chroma 未写入。", ""))
    return "\n".join(lines).encode("utf-8")


def write_f6_2b_audit_package(
    output_dir: Path,
    raw_rows: Any,
    candidate_rows: Any,
    baseline_content: bytes,
    review: Mapping[str, Any],
    candidate_diff: Mapping[str, Any],
    ddl_plan: Mapping[str, Any],
    *,
    datasource_id: str,
    dialect: str,
    schema: str,
    scope_policy_content: bytes,
    formal_chroma_sha256: str,
) -> dict[str, Any]:
    """在全部事实门禁通过后写入仓库外 F6-2B 审查包。"""
    resolved = validate_output_directory(output_dir)
    raw_content = serialize_metadata_index(raw_rows)
    candidate_content = serialize_metadata_index(candidate_rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "datasource_id": datasource_id,
        "dialect": dialect,
        "schema": schema,
        "raw_table_count": review["raw_table_count"],
        "raw_column_count": review["raw_column_count"],
        "candidate_table_count": review["candidate_table_count"],
        "candidate_column_count": review["candidate_column_count"],
        "raw_index_sha256": _sha256(raw_content),
        "candidate_index_sha256": _sha256(candidate_content),
        "formal_index_sha256": _sha256(baseline_content),
        "scope_policy_sha256": _sha256(scope_policy_content),
        "formal_chroma_sha256": formal_chroma_sha256,
        "database_read_only": True,
        "generator_version": "f6-2b-1",
    }
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / "column_metadata_index.raw.json").write_bytes(raw_content)
    (resolved / "column_metadata_index.candidate.json").write_bytes(candidate_content)
    (resolved / "metadata_scope_review.json").write_bytes(_json_bytes(review))
    (resolved / "metadata_scope_summary.md").write_bytes(
        _scope_summary_bytes(review, candidate_diff)
    )
    (resolved / "metadata_candidate_diff.json").write_bytes(
        _json_bytes(candidate_diff)
    )
    (resolved / "metadata_manifest.json").write_bytes(_json_bytes(manifest))
    (resolved / "ddl_memory_update_plan.json").write_bytes(_json_bytes(ddl_plan))
    (resolved / "ddl_memory_update_summary.md").write_bytes(
        _ddl_plan_summary_bytes(ddl_plan)
    )
    return manifest


def _load_index(content: bytes, label: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataValidationError(f"{label} 不是有效的 UTF-8 JSON") from exc
    return normalize_metadata_rows(value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 PostgreSQL Metadata 范围与 DDL 更新审查包")
    parser.add_argument("--datasource-id", default="postgresql-main")
    parser.add_argument("--dialect", default="postgresql", choices=("postgresql",))
    parser.add_argument("--schema", default="public")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=PROJECT_ROOT / "agent_data" / "column_metadata_index.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope-policy", type=Path, default=DEFAULT_SCOPE_POLICY)
    parser.add_argument("--formal-chroma", type=Path, default=DEFAULT_FORMAL_CHROMA)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = validate_output_directory(args.output_dir)
    baseline_path = args.baseline.expanduser().resolve()
    baseline_content = baseline_path.read_bytes()
    baseline_rows = _load_index(baseline_content, "baseline")
    scope_policy_path = args.scope_policy.expanduser().resolve()
    scope_policy_content = scope_policy_path.read_bytes()
    policy = load_metadata_scope_policy(scope_policy_path)
    for field, actual in (
        ("datasource_id", args.datasource_id),
        ("dialect", args.dialect),
        ("schema", args.schema),
    ):
        if policy[field] != actual:
            raise ValueError(
                f"命令行 {field}={actual!r} 与范围策略 {policy[field]!r} 不一致"
            )

    formal_chroma = args.formal_chroma.expanduser().resolve()
    formal_chroma_sha_before = directory_tree_sha256(formal_chroma)
    formal_index_sha_before = _sha256(baseline_content)

    db_kwargs = build_db_kwargs()
    validate_db_config(db_kwargs)

    import psycopg2

    connection = psycopg2.connect(**db_kwargs)
    try:
        raw_rows = extract_postgresql_metadata(connection, args.schema)
        deferred_view_details = extract_deferred_view_details(
            connection, args.schema, policy["deferred_new_tables"]
        )
    finally:
        connection.close()

    candidate_rows, scope_review = classify_metadata_scope(
        raw_rows,
        baseline_rows,
        policy,
        deferred_view_details=deferred_view_details,
    )
    candidate_diff = diff_metadata_indexes(baseline_rows, candidate_rows)
    existing_records = read_formal_ddl_memory_snapshot(
        formal_chroma, output_dir.parent
    )
    ddl_plan = build_ddl_memory_update_plan(
        candidate_rows, candidate_diff, existing_records
    )
    formal_chroma_sha_after = directory_tree_sha256(formal_chroma)
    formal_index_sha_after = _sha256(baseline_path.read_bytes())
    if formal_chroma_sha_after != formal_chroma_sha_before:
        raise RuntimeError("正式 Chroma SHA 在只读计划期间发生变化")
    if formal_index_sha_after != formal_index_sha_before:
        raise RuntimeError("正式 Metadata 索引 SHA 在只读计划期间发生变化")

    validate_f6_2b_expected_results(scope_review, candidate_diff, ddl_plan)
    manifest = write_f6_2b_audit_package(
        output_dir,
        raw_rows,
        candidate_rows,
        baseline_content,
        scope_review,
        candidate_diff,
        ddl_plan,
        datasource_id=args.datasource_id,
        dialect=args.dialect,
        schema=args.schema,
        scope_policy_content=scope_policy_content,
        formal_chroma_sha256=formal_chroma_sha_before,
    )

    candidate_content = serialize_metadata_index(candidate_rows)
    regenerated = serialize_metadata_index(
        normalize_metadata_rows(list(reversed(candidate_rows)))
    )
    deterministic = regenerated == candidate_content
    if not deterministic:
        raise RuntimeError("同批候选 Metadata 的确定性重生成校验失败")

    print("DB_READ_ONLY_STATUS: on")
    print(f"OUTPUT_DIRECTORY: {output_dir}")
    print(f"RAW_TABLE_COUNT: {scope_review['raw_table_count']}")
    print(f"RAW_COLUMN_COUNT: {scope_review['raw_column_count']}")
    print(f"CANDIDATE_TABLE_COUNT: {scope_review['candidate_table_count']}")
    print(f"CANDIDATE_COLUMN_COUNT: {scope_review['candidate_column_count']}")
    print(f"CANDIDATE_INDEX_SHA256: {manifest['candidate_index_sha256']}")
    print(f"DDL_PLAN_COUNTS: {ddl_plan['create']}/{ddl_plan['unchanged']}/{ddl_plan['changed']}/{ddl_plan['removed']}")
    print(f"FORMAL_INDEX_UNCHANGED: {formal_index_sha_before == formal_index_sha_after}")
    print(f"FORMAL_CHROMA_UNCHANGED: {formal_chroma_sha_before == formal_chroma_sha_after}")
    print("DETERMINISTIC_REGEN_RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

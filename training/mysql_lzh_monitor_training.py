"""lzh_monitor 首批 18 表训练材料的确定性 Plan/Apply 工具。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.memory import create_memory
from backend.mysql_sql_guard import MySQLSQLGuard
from training.sop.batch_validator import validate_training_batch
from training.sop.ddl_memory_identity import (
    DdlMemoryIdentityInput,
    build_ddl_memory_identity,
)
from training.sop.memory_write_plan import build_memory_write_plan


SOURCE_ID = "mysql-lzh-monitor"
DATABASE_NAME = "lzh_monitor"
MATERIAL_DIR = PROJECT_ROOT / "training" / "mysql_lzh_monitor"
METADATA_PATH = (
    PROJECT_ROOT / "agent_data" / SOURCE_ID / "column_metadata_index.json"
)
SCOPE_PATH = PROJECT_ROOT / "config" / "mysql_lzh_monitor_metadata_scope.json"
DOCUMENT_PATH = MATERIAL_DIR / "business_documents.json"
SQL_EXAMPLE_PATH = MATERIAL_DIR / "sql_examples.json"
DDL_OUTPUT_PATH = MATERIAL_DIR / "ddl_memories.json"
MANIFEST_PATH = MATERIAL_DIR / "materials_manifest.json"
FORMAL_STORE_PATH = PROJECT_ROOT / "vanna_data" / SOURCE_ID
DEFAULT_WORK_ROOT = Path(r"E:\3\_runtime\mysql-lzh-monitor-training")
DEFAULT_BACKUP_ROOT = Path(r"E:\3\_training_backups\mysql-lzh-monitor")


class TrainingError(RuntimeError):
    """训练计划或发布失败。"""


@dataclass(frozen=True)
class DesiredRecord:
    record_id: str
    document: str
    metadata: dict[str, Any]
    category: str
    logical_name: str
    content_fingerprint: str


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def _quote_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _default_sql(row: dict[str, Any]) -> str:
    value = row.get("default")
    if value is None:
        return ""
    text = str(value)
    mysql_type = str(row.get("mysql_type", "")).lower()
    if text.upper().startswith("CURRENT_TIMESTAMP"):
        rendered = text
    elif any(token in mysql_type for token in ("int", "decimal", "float", "double")):
        try:
            float(text)
            rendered = text
        except ValueError:
            rendered = _quote_literal(text)
    else:
        rendered = _quote_literal(text)
    return f" DEFAULT {rendered}"


def _group_metadata() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    rows = _read_json(METADATA_PATH)
    scope = _read_json(SCOPE_PATH)
    approved = list(scope["approved_tables"])
    excluded = set(scope["excluded_columns"])
    grouped: dict[str, list[dict[str, Any]]] = {table: [] for table in approved}
    for row in rows:
        table = row["table"]
        qualified = f"{table}.{row['column']}"
        if table not in grouped:
            raise TrainingError(f"Metadata 出现未批准表：{table}")
        if qualified in excluded:
            raise TrainingError(f"Metadata 仍包含排除字段：{qualified}")
        grouped[table].append(row)
    missing = [table for table, columns in grouped.items() if not columns]
    if missing:
        raise TrainingError(f"Metadata 缺少批准表：{missing}")
    if len(grouped) != 18:
        raise TrainingError(f"批准表数量必须为18，实际为{len(grouped)}")
    for columns in grouped.values():
        columns.sort(key=lambda item: item["ordinal_position"])
    return grouped, scope


def _render_ddl(table: str, rows: list[dict[str, Any]]) -> str:
    definitions: list[str] = []
    primary_key = [row["column"] for row in rows if row.get("primary_key")]
    for row in rows:
        nullable = "" if row.get("nullable") else " NOT NULL"
        comment = (
            f" COMMENT {_quote_literal(row['comment'])}" if row.get("comment") else ""
        )
        definitions.append(
            f"  {_quote_identifier(row['column'])} {row['mysql_type']}"
            f"{nullable}{_default_sql(row)}{comment}"
        )
    if primary_key:
        definitions.append(
            "  PRIMARY KEY (" + ", ".join(map(_quote_identifier, primary_key)) + ")"
        )
    indexes: dict[str, dict[str, Any]] = {}
    for row in rows:
        for index in row.get("indexes", []):
            if index["name"] != "PRIMARY":
                indexes[index["name"]] = index
    for name in sorted(indexes):
        index = indexes[name]
        prefix = "UNIQUE KEY" if index.get("unique") else "KEY"
        definitions.append(
            f"  {prefix} {_quote_identifier(name)} ("
            + ", ".join(map(_quote_identifier, index["columns"]))
            + ")"
        )
    table_comment = rows[0].get("table_comment")
    ddl = (
        f"CREATE TABLE {_quote_identifier(table)} (\n"
        + ",\n".join(definitions)
        + "\n)"
    )
    if table_comment:
        ddl += f" COMMENT={_quote_literal(table_comment)}"
    ddl += ";"
    relations = rows[0].get("logical_relations", [])
    if relations:
        relation_lines = [
            f"-- 逻辑关联：{table}.{item['column']} -> {item['target']}"
            for item in relations
        ]
        ddl += "\n" + "\n".join(relation_lines)
    return ddl


def build_ddl_records() -> list[DesiredRecord]:
    grouped, scope = _group_metadata()
    forbidden_names = {
        qualified.split(".", 1)[1] for qualified in scope["excluded_columns"]
    }
    records: list[DesiredRecord] = []
    for table, rows in grouped.items():
        ddl = _render_ddl(table, rows)
        tokens = {row["column"] for row in rows}
        leaked = sorted(forbidden_names.intersection(tokens))
        if table == "rs_pollutant_info" and leaked:
            raise TrainingError(f"DDL 泄漏排除字段：{leaked}")
        identity = build_ddl_memory_identity(
            DdlMemoryIdentityInput(
                source_id=SOURCE_ID,
                schema_name=DATABASE_NAME,
                object_type="table",
                object_name=table,
            ),
            ddl,
        )
        metadata = dict(identity.effective_metadata)
        metadata.update(
            {
                "content": identity.normalized_ddl,
                "content_fingerprint": identity.content_fingerprint,
                "timestamp": "managed-v1",
                "is_text_memory": True,
                "category": "ddl",
            }
        )
        records.append(
            DesiredRecord(
                identity.record_id,
                identity.normalized_ddl,
                metadata,
                "ddl",
                table,
                identity.content_fingerprint,
            )
        )
    return records


def build_document_records() -> list[DesiredRecord]:
    documents = _read_json(DOCUMENT_PATH)
    if not isinstance(documents, list):
        raise TrainingError("业务文档必须是数组")
    excluded = _read_json(SCOPE_PATH)["excluded_columns"]
    forbidden_names = [qualified.split(".", 1)[1] for qualified in excluded]
    records: list[DesiredRecord] = []
    for item in documents:
        content = item["content"].strip()
        if any(name in content for name in forbidden_names):
            raise TrainingError(f"业务文档 {item['document_id']} 含排除字段名称")
        logical_id = _sha256(
            f"docmem-v1|{SOURCE_ID}|{item['document_id']}"
        )
        record_id = f"docmem-v1-{logical_id}"
        metadata_base = {
            "memory_type": "business_document",
            "identity_version": "docmem-v1",
            "source_id": SOURCE_ID,
            "document_id": item["document_id"],
            "title": item["title"],
            "logical_id": logical_id,
            "record_id": record_id,
        }
        fingerprint = _sha256(_canonical_json([content, metadata_base]))
        metadata = {
            **metadata_base,
            "content": content,
            "content_fingerprint": fingerprint,
            "timestamp": "managed-v1",
            "is_text_memory": True,
            "category": "business_document",
        }
        records.append(
            DesiredRecord(
                record_id,
                content,
                metadata,
                "business_document",
                item["document_id"],
                fingerprint,
            )
        )
    return records


def build_tool_records() -> tuple[list[DesiredRecord], dict[str, Any]]:
    batch = _read_json(SQL_EXAMPLE_PATH)
    guard = MySQLSQLGuard(str(METADATA_PATH))
    validation = validate_training_batch(batch, sql_guard=guard)
    if not validation.valid or not validation.batch_content_sha256:
        raise TrainingError(
            "SQL 样例批次校验失败：" + _canonical_json(validation.to_dict())
        )
    write_plan = build_memory_write_plan(
        batch,
        approved_batch_content_sha256=validation.batch_content_sha256,
        sql_guard=guard,
    )
    records: list[DesiredRecord] = []
    for item in write_plan.items:
        canonical = item.canonical_content
        compatibility = {
            **item.compatibility_metadata,
            "source_id": SOURCE_ID,
            "dialect": "mysql",
        }
        metadata = {
            "question": canonical["question"],
            "tool_name": canonical["tool_name"],
            "args_json": _canonical_json(canonical["args"]),
            "success": True,
            "metadata_json": _canonical_json(compatibility),
            **item.governance_metadata,
            "source_id": SOURCE_ID,
            "category": "sql_example",
            "record_id": item.record_id,
            "content_fingerprint": item.memory_content_sha256,
        }
        records.append(
            DesiredRecord(
                item.record_id,
                canonical["question"],
                metadata,
                "sql_example",
                item.sample_id,
                item.memory_content_sha256,
            )
        )
    return records, validation.to_dict()


def build_desired_records() -> tuple[list[DesiredRecord], dict[str, Any]]:
    ddl = build_ddl_records()
    documents = build_document_records()
    tools, validation = build_tool_records()
    records = sorted(ddl + documents + tools, key=lambda item: item.record_id)
    ids = [item.record_id for item in records]
    if len(ids) != len(set(ids)):
        raise TrainingError("训练材料生成了重复 record_id")
    summary = {
        "source_id": SOURCE_ID,
        "database": DATABASE_NAME,
        "approved_table_count": len(ddl),
        "business_document_count": len(documents),
        "sql_example_count": len(tools),
        "total_record_count": len(records),
        "sql_batch_content_sha256": validation["batch_content_sha256"],
        "record_set_sha256": _sha256(
            _canonical_json(
                [
                    [item.record_id, item.content_fingerprint, item.category]
                    for item in records
                ]
            )
        ),
    }
    return records, summary


def write_materials() -> dict[str, Any]:
    records, summary = build_desired_records()
    ddl_payload = [
        {
            "table": item.logical_name,
            "record_id": item.record_id,
            "content_fingerprint": item.content_fingerprint,
            "ddl": item.document,
        }
        for item in records
        if item.category == "ddl"
    ]
    _write_json(DDL_OUTPUT_PATH, ddl_payload)
    manifest = {
        "manifest_schema_version": "1.0",
        **summary,
        "inputs": {
            "metadata": str(METADATA_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "scope": str(SCOPE_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "business_documents": str(DOCUMENT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sql_examples": str(SQL_EXAMPLE_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        },
        "records": [
            {
                "record_id": item.record_id,
                "category": item.category,
                "logical_name": item.logical_name,
                "content_fingerprint": item.content_fingerprint,
            }
            for item in records
        ],
    }
    _write_json(MANIFEST_PATH, manifest)
    return manifest


def _close_memory(memory: Any) -> None:
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    try:
        if memory._client is not None:
            memory._client._system.stop()
    except Exception:
        pass
    memory._collection = None
    memory._client = None
    gc.collect()
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def inventory_store(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    memory = create_memory(path)
    try:
        collection = memory._get_collection()
        raw = collection.get(include=["documents", "metadatas"])
        return {
            record_id: {"document": document, "metadata": metadata}
            for record_id, document, metadata in zip(
                raw["ids"], raw["documents"], raw["metadatas"]
            )
        }
    finally:
        _close_memory(memory)


def create_plan(formal_store: Path = FORMAL_STORE_PATH) -> dict[str, Any]:
    records, summary = build_desired_records()
    existing = inventory_store(formal_store)
    desired_ids = {item.record_id for item in records}
    unmanaged = sorted(
        record_id
        for record_id, stored in existing.items()
        if stored["metadata"].get("source_id") != SOURCE_ID
    )
    if unmanaged:
        raise TrainingError(f"正式库含非本数据源记录，拒绝覆盖：{unmanaged}")
    creates: list[str] = []
    updates: list[str] = []
    unchanged: list[str] = []
    for item in records:
        stored = existing.get(item.record_id)
        if stored is None:
            creates.append(item.record_id)
        elif (
            stored["document"] == item.document
            and stored["metadata"].get("content_fingerprint")
            == item.content_fingerprint
        ):
            unchanged.append(item.record_id)
        else:
            updates.append(item.record_id)
    deletes = sorted(set(existing) - desired_ids)
    plan_body = {
        "plan_schema_version": "1.0",
        **summary,
        "formal_count_before": len(existing),
        "create_ids": creates,
        "update_ids": updates,
        "delete_ids": deletes,
        "unchanged_ids": unchanged,
        "executable": True,
    }
    return {**plan_body, "plan_sha256": _sha256(_canonical_json(plan_body))}


def _build_candidate(
    candidate: Path, records: list[DesiredRecord], fail_after: int | None = None
) -> None:
    candidate.mkdir(parents=True, exist_ok=False)
    memory = create_memory(candidate)
    try:
        collection = memory._get_collection()
        for offset in range(0, len(records), 16):
            batch = records[offset : offset + 16]
            collection.add(
                ids=[item.record_id for item in batch],
                documents=[item.document for item in batch],
                metadatas=[item.metadata for item in batch],
            )
            if fail_after is not None and offset + len(batch) >= fail_after:
                raise TrainingError("测试注入：候选训练失败")
        if collection.count() != len(records):
            raise TrainingError("候选库写入数量不一致")
    finally:
        _close_memory(memory)


def apply_plan(
    *,
    expected_plan_sha256: str,
    formal_store: Path = FORMAL_STORE_PATH,
    work_root: Path = DEFAULT_WORK_ROOT,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    fail_after: int | None = None,
) -> dict[str, Any]:
    plan = create_plan(formal_store)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise TrainingError("Plan 摘要已变化，拒绝 Apply")
    changed = (
        len(plan["create_ids"]) + len(plan["update_ids"]) + len(plan["delete_ids"])
    )
    if changed == 0:
        return {
            "status": "unchanged",
            "plan_sha256": expected_plan_sha256,
            "count": plan["total_record_count"],
        }
    records, summary = build_desired_records()
    work_root.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=work_root))
    candidate.rmdir()
    try:
        _build_candidate(candidate, records, fail_after=fail_after)
        candidate_inventory = inventory_store(candidate)
        if set(candidate_inventory) != {item.record_id for item in records}:
            raise TrainingError("候选库最终身份集合不一致")
        backup: Path | None = None
        formal_store.parent.mkdir(parents=True, exist_ok=True)
        if formal_store.exists():
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / (
                f"{formal_store.name}-{plan['plan_sha256'][:12]}"
            )
            if backup.exists():
                raise TrainingError(f"备份路径已存在：{backup}")
            os.replace(formal_store, backup)
        try:
            os.replace(candidate, formal_store)
            final_inventory = inventory_store(formal_store)
            if len(final_inventory) != summary["total_record_count"]:
                raise TrainingError("正式库发布后数量不一致")
        except Exception:
            if formal_store.exists():
                quarantine = work_root / (
                    f"failed-publish-{plan['plan_sha256'][:12]}"
                )
                if not quarantine.exists():
                    os.replace(formal_store, quarantine)
            if backup is not None and not formal_store.exists():
                os.replace(backup, formal_store)
            raise
        return {
            "status": "applied",
            "plan_sha256": expected_plan_sha256,
            "count": len(final_inventory),
            "backup": str(backup) if backup else None,
            **summary,
        }
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("materials")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--output", type=Path)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--expected-plan-sha", required=True)
    args = parser.parse_args()
    if args.command == "materials":
        result = write_materials()
    elif args.command == "plan":
        result = create_plan()
        if args.output:
            _write_json(args.output, result)
    else:
        result = apply_plan(expected_plan_sha256=args.expected_plan_sha)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

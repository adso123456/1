"""阶段 E-2B：候选运行资产完整性硬门。

在正式资产备份之前，对候选资产执行结构化回读校验：
  Documentation provenance（business_documents.json + asset_provenance.json）
  Chroma DDL / Documentation 逐记录回读
  SQL Tool Memory 逐记录表列准入

所有身份必须来自生成期记录与磁盘/collection 回读的互相校验，
禁止通过解析文本临时重建身份。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.data_source_asset_provenance import (
    content_fingerprint,
    read_provenance,
)
from backend.data_source_asset_validator import (
    _normalize_schema,
    _parse_ddl_text,
)
from backend.data_source_catalog import DataSourceCatalogError


def parse_ddl_identity(
    ddl_text: str,
    *,
    database_type: str,
    database_name: str,
) -> tuple[list[list[str]], list[list[str]]]:
    """复用 E-2A 严格 DDL 解析器，提取规范化表/列身份。"""
    parsed = _parse_ddl_text(ddl_text, database_type)
    table_keys: list[list[str]] = []
    column_keys: list[list[str]] = []
    for (schema, table), info in parsed.items():
        normalized = _normalize_schema(database_type, database_name, schema)
        table_keys.append([normalized, table])
        referenced = set(info["declared_columns"])
        referenced.update(info["primary_key_columns"])
        for index in info.get("indexes", []):
            referenced.update(index["columns"])
        for column in sorted(referenced):
            column_keys.append([normalized, table, column])
    table_keys.sort()
    column_keys.sort()
    return table_keys, column_keys


def normalize_scope_keys(
    database_type: str,
    database_name: str,
    scope: Iterable[Mapping[str, Any]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    table_keys: set[tuple[str, str]] = set()
    column_keys: set[tuple[str, str, str]] = set()
    for item in scope:
        schema = _normalize_schema(
            database_type,
            database_name,
            item.get("schema"),
        )
        table = str(item.get("table") or "")
        column = str(item.get("column") or "")
        if not table or not column:
            raise DataSourceCatalogError(
                "selected_scope 存在缺少表或列的身份项"
            )
        table_keys.add((schema, table))
        column_keys.add((schema, table, column))
    return table_keys, column_keys


def _normalize_keys(
    keys: Iterable[Iterable[str]],
) -> set[tuple[str, ...]]:
    return {tuple(str(part) for part in key) for key in keys}


def _validate_provenance_header(
    provenance: Mapping[str, Any],
    *,
    source_id: str,
    target_runtime_revision: int,
    scope_fingerprint: str,
    review_policy_fingerprint: str,
) -> None:
    if provenance.get("source_id") != source_id:
        raise DataSourceCatalogError("asset_provenance.source_id 与数据源不一致")
    if int(provenance.get("runtime_revision") or -1) != target_runtime_revision:
        raise DataSourceCatalogError("asset_provenance.runtime_revision 与目标 revision 不一致")
    if provenance.get("scope_fingerprint") != scope_fingerprint:
        raise DataSourceCatalogError("asset_provenance.scope_fingerprint 不一致")
    if provenance.get("review_policy_fingerprint") != review_policy_fingerprint:
        raise DataSourceCatalogError("asset_provenance.review_policy_fingerprint 不一致")


def _require_equal_keys(
    actual: set[tuple[str, ...]],
    expected: set[tuple[str, ...]],
    label: str,
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    detail = []
    if missing:
        detail.append("缺少：" + "、".join(".".join(k) for k in missing))
    if extra:
        detail.append("多出：" + "、".join(".".join(k) for k in extra))
    if detail:
        raise DataSourceCatalogError(f"{label} 不一致；" + "；".join(detail))


def _validate_documentation(
    provenance: Mapping[str, Any],
    *,
    business_documents_path: Path,
    database_type: str,
    database_name: str,
    allowed_tables: set[tuple[str, str]],
    scope_tables: set[tuple[str, str]],
) -> None:
    records = list(provenance.get("assets", {}).get("documentation") or [])
    try:
        documents = json.loads(business_documents_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataSourceCatalogError("候选 business_documents.json 不可读") from exc
    if not isinstance(documents, list) or not all(
        isinstance(item, str) for item in documents
    ):
        raise DataSourceCatalogError("候选 business_documents.json 必须是字符串数组")
    if len(records) != len(documents):
        raise DataSourceCatalogError("documentation 记录数与文本文件不一致")
    seen_ids: set[str] = set()
    seen_tables: dict[tuple[str, str], int] = {}
    for record, document in zip(records, documents):
        if not isinstance(record, Mapping):
            raise DataSourceCatalogError("documentation provenance 记录不是对象")
        record_id = str(record.get("record_id") or "")
        if not record_id:
            raise DataSourceCatalogError("documentation provenance 缺少 record_id")
        if record_id in seen_ids:
            raise DataSourceCatalogError(f"documentation record_id 重复：{record_id}")
        seen_ids.add(record_id)
        if str(record.get("document") or "") != document:
            raise DataSourceCatalogError("documentation 文本与 business_documents.json 不一致")
        if content_fingerprint(document) != str(record.get("content_fingerprint") or ""):
            raise DataSourceCatalogError("documentation content_fingerprint 不一致")
        raw_key_list = [
            tuple(str(part) for part in key)
            for key in (record.get("table_keys") or [])
        ]
        raw_table_keys = set(raw_key_list)
        for raw_key in raw_table_keys:
            if len(raw_key) != 2:
                raise DataSourceCatalogError(
                    f"documentation table_key 必须为两段身份：{raw_key}"
                )
        table_keys = {
            (
                _normalize_schema(
                    database_type,
                    database_name,
                    schema,
                ),
                table,
            )
            for schema, table in raw_table_keys
        }
        if not table_keys:
            raise DataSourceCatalogError("documentation 记录 table_keys 为空")
        if len(raw_key_list) != len(raw_table_keys):
            raise DataSourceCatalogError("documentation 单条记录内 table_key 重复")
        for key in table_keys:
            if len(key) != 2 or key not in allowed_tables:
                raise DataSourceCatalogError(
                    f"documentation 包含非 allowed 表：{key}"
                )
        for key in table_keys:
            seen_tables[key] = seen_tables.get(key, 0) + 1
    if set(seen_tables) != scope_tables:
        _require_equal_keys(set(seen_tables), scope_tables, "documentation table union")
    duplicate_tables = {key for key, count in seen_tables.items() if count > 1}
    if duplicate_tables:
        raise DataSourceCatalogError(
            "同一 allowed 表出现在多条文档："
            + "、".join(".".join(k) for k in sorted(duplicate_tables))
        )


def _validate_chroma(
    provenance: Mapping[str, Any],
    *,
    memory_path: Path,
    database_type: str,
    database_name: str,
    allowed_tables: set[tuple[str, str]],
    scope_tables: set[tuple[str, str]],
    scope_columns: set[tuple[str, str, str]],
    expected_records: list[tuple[str, str, Mapping[str, Any]]],
) -> None:
    from backend.memory import create_memory

    memory = create_memory(memory_path)
    try:
        collection = memory._get_collection()
        count = collection.count()
        if count != len(expected_records):
            raise DataSourceCatalogError(
                f"候选 Chroma count {count} != 预期 {len(expected_records)}"
            )
        expected_ids = [record_id for record_id, _, _ in expected_records]
        if len(expected_ids) != len(set(expected_ids)):
            raise DataSourceCatalogError("候选 Chroma 预期 ID 重复")
        result = collection.get(
            ids=expected_ids,
            include=["documents", "metadatas"],
        )
        returned_ids = list(result.get("ids") or [])
        if set(returned_ids) != set(expected_ids):
            raise DataSourceCatalogError(
                "候选 Chroma ID 集合与预期不一致（缺失或多余记录）"
            )
        readback = {
            record_id: (document, metadata)
            for record_id, document, metadata in zip(
                returned_ids,
                result.get("documents") or [],
                result.get("metadatas") or [],
            )
        }
        expected_by_id = {record_id: (document, metadata) for record_id, document, metadata in expected_records}
        for record_id, (document, metadata) in readback.items():
            expected_document, expected_metadata = expected_by_id.get(
                record_id,
                (None, None),
            )
            if expected_document is None:
                raise DataSourceCatalogError(f"候选 Chroma 多余记录：{record_id}")
            if document != expected_document:
                raise DataSourceCatalogError(f"候选 Chroma 文档不一致：{record_id}")
            for key in ("memory_type", "category", "source_id", "content_fingerprint"):
                if str(metadata.get(key) or "") != str(expected_metadata.get(key) or ""):
                    raise DataSourceCatalogError(
                        f"候选 Chroma metadata.{key} 不一致：{record_id}"
                    )
        # 逐记录按类型校验身份。
        ddl_records = [
            (record_id, document)
            for record_id, (document, metadata) in readback.items()
            if str(metadata.get("memory_type")) == "ddl"
        ]
        documentation_records = [
            (record_id, document)
            for record_id, (document, metadata) in readback.items()
            if str(metadata.get("memory_type")) == "documentation"
        ]
        sql_tool_records = [
            (record_id, document, metadata)
            for record_id, (document, metadata) in readback.items()
            if str(metadata.get("category")) == "sql_example"
            or str(metadata.get("tool_name")) == "run_sql"
            or "args_json" in metadata
        ]
        _validate_chroma_ddl(
            ddl_records,
            provenance,
            database_type=database_type,
            database_name=database_name,
            allowed_tables=allowed_tables,
            scope_tables=scope_tables,
            scope_columns=scope_columns,
        )
        _validate_chroma_documentation(
            documentation_records,
            provenance,
            database_type=database_type,
            database_name=database_name,
            allowed_tables=allowed_tables,
            scope_tables=scope_tables,
        )
        _validate_sql_tool_records(
            sql_tool_records,
            provenance,
        )
    finally:
        from backend.data_source_connectors import DataSourceAssetPreparer

        DataSourceAssetPreparer._close_memory(memory)


def _validate_chroma_ddl(
    records: list[tuple[str, str]],
    provenance: Mapping[str, Any],
    *,
    database_type: str,
    database_name: str,
    allowed_tables: set[tuple[str, str]],
    scope_tables: set[tuple[str, str]],
    scope_columns: set[tuple[str, str, str]],
) -> None:
    provenance_records = {
        str(item.get("record_id") or ""): item
        for item in provenance.get("assets", {}).get("chroma_ddl") or []
    }
    if len(records) != len(provenance_records):
        raise DataSourceCatalogError(
            "Chroma DDL 记录数与 provenance 不一致"
        )
    seen_tables: set[tuple[str, str]] = set()
    seen_columns: set[tuple[str, str, str]] = set()
    for record_id, document in records:
        if not document.startswith("DDL\n"):
            raise DataSourceCatalogError(f"Chroma DDL 前缀错误：{record_id}")
        ddl_text = document[len("DDL\n") :]
        table_keys, column_keys = parse_ddl_identity(
            ddl_text,
            database_type=database_type,
            database_name=database_name,
        )
        provenance_record = provenance_records.get(record_id)
        if provenance_record is None:
            raise DataSourceCatalogError(f"Chroma DDL 无对应 provenance：{record_id}")
        expected_tables = _normalize_keys(provenance_record.get("table_keys") or [])
        expected_columns = _normalize_keys(provenance_record.get("column_keys") or [])
        _require_equal_keys(
            _normalize_keys(table_keys),
            expected_tables,
            f"Chroma DDL {record_id} table_keys",
        )
        _require_equal_keys(
            _normalize_keys(column_keys),
            expected_columns,
            f"Chroma DDL {record_id} column_keys",
        )
        for key in table_keys:
            if tuple(key) not in allowed_tables:
                raise DataSourceCatalogError(
                    f"Chroma DDL 引用非 allowed 表：{key}"
                )
            seen_tables.add(tuple(key))
        for key in column_keys:
            if tuple(key) not in scope_columns:
                raise DataSourceCatalogError(
                    f"Chroma DDL 引用 scope 外列：{key}"
                )
            seen_columns.add(tuple(key))
    if set(seen_tables) != scope_tables:
        _require_equal_keys(set(seen_tables), scope_tables, "Chroma DDL table union")
    if set(seen_columns) != scope_columns:
        _require_equal_keys(set(seen_columns), scope_columns, "Chroma DDL column union")


def _validate_chroma_documentation(
    records: list[tuple[str, str]],
    provenance: Mapping[str, Any],
    *,
    database_type: str,
    database_name: str,
    allowed_tables: set[tuple[str, str]],
    scope_tables: set[tuple[str, str]],
) -> None:
    documentation_provenance = {
        str(item.get("record_id") or ""): item
        for item in provenance.get("assets", {}).get("documentation") or []
    }
    chroma_documentation = {
        str(item.get("record_id") or ""): item
        for item in provenance.get("assets", {}).get("chroma_documentation") or []
    }
    if len(records) != len(documentation_provenance):
        raise DataSourceCatalogError("Chroma documentation 记录数与 provenance 不一致")
    if len(records) != len(chroma_documentation):
        raise DataSourceCatalogError(
            "Chroma documentation 记录数与 chroma provenance 不一致"
        )
    seen_tables: dict[tuple[str, str], int] = {}
    for record_id, document in records:
        doc_provenance = documentation_provenance.get(record_id)
        chroma_provenance = chroma_documentation.get(record_id)
        if doc_provenance is None or chroma_provenance is None:
            raise DataSourceCatalogError(
                f"Chroma documentation 无对应 provenance：{record_id}"
            )
        if str(doc_provenance.get("document") or "") != document:
            raise DataSourceCatalogError(
                f"Chroma documentation 文本不一致：{record_id}"
            )
        if str(chroma_provenance.get("content_fingerprint") or "") != str(
            doc_provenance.get("content_fingerprint") or ""
        ):
            raise DataSourceCatalogError(
                f"Chroma documentation content_fingerprint 不一致：{record_id}"
            )
        expected_tables = {
            (
                _normalize_schema(
                    database_type,
                    database_name,
                    schema,
                ),
                table,
            )
            for schema, table in _normalize_keys(
                doc_provenance.get("table_keys") or []
            )
        }
        chroma_tables = {
            (
                _normalize_schema(
                    database_type,
                    database_name,
                    schema,
                ),
                table,
            )
            for schema, table in _normalize_keys(
                chroma_provenance.get("table_keys") or []
            )
        }
        _require_equal_keys(
            chroma_tables,
            expected_tables,
            f"Chroma documentation {record_id} table_keys",
        )
        for key in expected_tables:
            if key not in allowed_tables:
                raise DataSourceCatalogError(
                    f"Chroma documentation 引用非 allowed 表：{key}"
                )
            seen_tables[key] = seen_tables.get(key, 0) + 1
    if set(seen_tables) != scope_tables:
        _require_equal_keys(set(seen_tables), scope_tables, "Chroma documentation table union")
    duplicate = {key for key, count in seen_tables.items() if count > 1}
    if duplicate:
        raise DataSourceCatalogError(
            "同一 allowed 表在 Chroma documentation 中出现多次："
            + "、".join(".".join(k) for k in sorted(duplicate))
        )


def _validate_sql_tool_records(
    records: list[tuple[str, str, Mapping[str, Any]]],
    provenance: Mapping[str, Any],
) -> None:
    sql_provenance = {
        str(item.get("record_id") or ""): item
        for item in provenance.get("assets", {}).get("sql_tool_memory") or []
    }
    if len(records) != len(sql_provenance):
        raise DataSourceCatalogError(
            "SQL Tool Memory 记录数与 provenance 不一致"
        )
    seen_ids: set[str] = set()
    for record_id, _, metadata in records:
        if record_id in seen_ids:
            raise DataSourceCatalogError(f"SQL Tool Memory record_id 重复：{record_id}")
        seen_ids.add(record_id)
        if str(metadata.get("category") or "") != "sql_example":
            raise DataSourceCatalogError("SQL Tool Memory category 必须为 sql_example")
        if str(metadata.get("tool_name") or "") != "run_sql":
            raise DataSourceCatalogError("SQL Tool Memory tool_name 必须为 run_sql")
        try:
            args = json.loads(str(metadata.get("args_json") or "{}"))
        except (TypeError, ValueError):
            raise DataSourceCatalogError(
                f"SQL Tool Memory args_json 不可解析：{record_id}"
            ) from None
        sql = str((args or {}).get("sql") or "")
        if not sql.strip():
            raise DataSourceCatalogError(f"SQL Tool Memory 缺少非空 sql：{record_id}")
        if record_id not in sql_provenance:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 无对应 provenance：{record_id}"
            )


def validate_runtime_candidate_assets(
    *,
    source_id: str,
    database_type: str,
    database_name: str,
    allowed_tables: set[tuple[str, str]],
    scope: Iterable[Mapping[str, Any]],
    scope_fingerprint: str,
    review_policy_fingerprint: str,
    target_runtime_revision: int,
    business_documents_path: Path,
    provenance_path: Path,
    memory_path: Path,
    expected_records: list[tuple[str, str, Mapping[str, Any]]],
    sql_guard: Any,
) -> dict[str, int]:
    """统一入口：候选 Documentation / Chroma / SQL Tool 校验。"""
    provenance = read_provenance(provenance_path)
    _validate_provenance_header(
        provenance,
        source_id=source_id,
        target_runtime_revision=target_runtime_revision,
        scope_fingerprint=scope_fingerprint,
        review_policy_fingerprint=review_policy_fingerprint,
    )
    scope_tables, scope_columns = normalize_scope_keys(
        database_type,
        database_name,
        scope,
    )
    allowed = {
        (
            _normalize_schema(
                database_type,
                database_name,
                schema,
            ),
            str(table),
        )
        for schema, table in allowed_tables
    }
    if scope_tables != allowed:
        raise DataSourceCatalogError("scope 表集合与 allowed_tables 不一致（E-1 门）")
    _validate_documentation(
        provenance,
        business_documents_path=business_documents_path,
        database_type=database_type,
        database_name=database_name,
        allowed_tables=allowed,
        scope_tables=scope_tables,
    )
    _validate_chroma(
        provenance,
        memory_path=memory_path,
        database_type=database_type,
        database_name=database_name,
        allowed_tables=allowed,
        scope_tables=scope_tables,
        scope_columns=scope_columns,
        expected_records=expected_records,
    )
    _validate_sql_tool_gate(
        provenance,
        expected_records,
        allowed_tables=allowed,
        scope_columns=scope_columns,
        sql_guard=sql_guard,
    )
    return {
        "documentation_records": len(provenance["assets"]["documentation"]),
        "chroma_ddl_records": len(provenance["assets"]["chroma_ddl"]),
        "chroma_documentation_records": len(
            provenance["assets"]["chroma_documentation"]
        ),
        "sql_tool_memory_records": len(provenance["assets"]["sql_tool_memory"]),
    }


def _validate_sql_tool_gate(
    provenance: Mapping[str, Any],
    expected_records: list[tuple[str, str, Mapping[str, Any]]],
    *,
    allowed_tables: set[tuple[str, str]],
    scope_columns: set[tuple[str, str, str]],
    sql_guard: Any,
) -> None:
    sql_provenance = {
        str(item.get("record_id") or ""): item
        for item in provenance.get("assets", {}).get("sql_tool_memory") or []
    }
    sql_tool_expected = [
        (record_id, _, metadata)
        for record_id, _, metadata in expected_records
        if (
            str(metadata.get("category") or "") == "sql_example"
            or str(metadata.get("tool_name") or "") == "run_sql"
            or "args_json" in metadata
        )
    ]
    if len(sql_tool_expected) != len(sql_provenance):
        raise DataSourceCatalogError(
            "SQL Tool Memory 预期记录数与 provenance 不一致"
        )
    for record_id, _, metadata in sql_tool_expected:
        is_sql_tool = (
            str(metadata.get("category") or "") == "sql_example"
            or str(metadata.get("tool_name") or "") == "run_sql"
            or "args_json" in metadata
        )
        if not is_sql_tool:
            continue
        if record_id not in sql_provenance:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 无对应 provenance：{record_id}"
            )
        try:
            args = json.loads(str(metadata.get("args_json") or "{}"))
        except (TypeError, ValueError):
            raise DataSourceCatalogError(
                f"SQL Tool Memory args_json 不可解析：{record_id}"
            ) from None
        sql = str((args or {}).get("sql") or "")
        if not sql.strip():
            raise DataSourceCatalogError(f"SQL Tool Memory 缺少非空 sql：{record_id}")
        result = sql_guard.validate(sql, query="")
        if not result.passed:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 未通过 SQLGuard：{record_id} -> {result.reason}"
            )
        if result.unknown_tables or result.unknown_columns:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 含未知表/列：{record_id}"
            )
        if result.wildcard_references:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 含通配符引用：{record_id}"
            )
        if result.ambiguous_columns:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 含歧义字段：{record_id}"
            )
        if result.unresolved_lineage:
            raise DataSourceCatalogError(
                f"SQL Tool Memory 含无法解析的身份：{record_id}"
            )
        if not result.used_physical_tables.issubset(allowed_tables):
            raise DataSourceCatalogError(
                f"SQL Tool Memory 引用非 allowed 表：{record_id}"
            )
        if not result.used_physical_columns.issubset(scope_columns):
            raise DataSourceCatalogError(
                f"SQL Tool Memory 引用 scope 外列：{record_id}"
            )
        actual_tables = set(
            (schema, table) for schema, table in result.used_physical_tables
        )
        actual_columns = set(
            (schema, table, column)
            for schema, table, column in result.used_physical_columns
        )
        expected_tables = _normalize_keys(
            sql_provenance[record_id].get("table_keys") or []
        )
        expected_columns = _normalize_keys(
            sql_provenance[record_id].get("column_keys") or []
        )
        _require_equal_keys(
            actual_tables,
            expected_tables,
            f"SQL Tool Memory {record_id} table_keys",
        )
        _require_equal_keys(
            actual_columns,
            expected_columns,
            f"SQL Tool Memory {record_id} column_keys",
        )

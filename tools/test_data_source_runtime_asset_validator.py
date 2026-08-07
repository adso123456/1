"""阶段 E-2B：候选运行资产完整性硬门回归测试。

覆盖规格 14.1–14.6：
  Documentation / provenance 结构与回读校验
  Chroma DDL / Documentation 逐记录回读校验
  SQL Tool Memory 表列准入
  set_enabled provenance 复用门
  rollback / restart recovery 中 provenance 一致性
  失败注入后 revision、正式资产哈希、active batch 不变
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_asset_provenance import (
    build_provenance,
    chroma_record_id,
    content_fingerprint,
    provenance_fingerprint,
    write_provenance,
)
from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    DataSourceCatalogError,
)
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.data_source_runtime_asset_validator import (
    parse_ddl_identity,
    validate_runtime_candidate_assets,
)
from backend.sql_guard import SQLGuard


PG_TABLES = ("monitor_data", "station_dict", "water_quality")
PG_COLUMNS = ("id", "name", "value")


def _scope_rows(database_type: str, tables=PG_TABLES, columns=PG_COLUMNS):
    rows = []
    for table in tables:
        for position, column in enumerate(columns, start=1):
            rows.append(
                {
                    "schema": (
                        "public"
                        if database_type == "postgresql"
                        else ""
                    ),
                    "table": table,
                    "object_type": "table",
                    "table_comment": f"{table} 注释",
                    "column": column,
                    "type": (
                        "bigint"
                        if column == "id"
                        else "numeric"
                    ),
                    "comment": f"{column} 注释",
                    "nullable": column != "id",
                    "primary_key": column == "id",
                    "ordinal_position": position,
                    "indexes": [],
                    "domain": "监测",
                    "grain": "id",
                    "time_column": "",
                    "valid_row_rules": [],
                    "confidence": "deterministic",
                }
            )
    return rows


def _index_rows(scope_rows: list[dict]) -> list[dict]:
    return [
        {
            "schema": item.get("schema"),
            "table": item["table"],
            "column": item["column"],
        }
        for item in scope_rows
    ]


def _allowed_tables(scope_rows: list[dict]) -> set[tuple[str, str]]:
    return {
        (str(item.get("schema") or ""), str(item["table"]))
        for item in scope_rows
    }


def _ddls_for(scope_rows: list[dict], database_type: str) -> list[str]:
    quote = '"' if database_type == "postgresql" else "`"
    by_table: dict[tuple[str, str], list[dict]] = {}
    for item in scope_rows:
        by_table.setdefault(
            (str(item.get("schema") or ""), item["table"]),
            [],
        ).append(item)
    ddls = []
    for (schema, table), columns in sorted(by_table.items()):
        columns.sort(key=lambda item: item.get("ordinal_position", 0))
        qualified = (
            f"{quote}{schema}{quote}."
            if database_type == "postgresql" and schema
            else ""
        ) + f"{quote}{table}{quote}"
        definitions = [
            f"  {quote}{item['column']}{quote} {item['type']}"
            for item in columns
        ]
        definitions.append(f"  PRIMARY KEY ({quote}id{quote})")
        ddls.append(
            f"CREATE TABLE {qualified} (\n"
            + ",\n".join(definitions)
            + "\n);"
        )
    return ddls


class _FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict]] = {}
        self._dupes = 0

    def add(self, *, ids, documents, metadatas) -> None:
        for record_id, document, metadata in zip(
            ids, documents, metadatas, strict=True
        ):
            if str(record_id) in self.records:
                self._dupes += 1
            self.records[str(record_id)] = (document, dict(metadata))

    def count(self) -> int:
        return len(self.records) + self._dupes

    def get(self, *, ids=None, where=None, include=None) -> dict:
        records = list(self.records.items())
        if where:
            records = [
                (record_id, item)
                for record_id, item in records
                if all(
                    item[1].get(key) == value
                    for key, value in where.items()
                )
            ]
        if ids is not None:
            wanted = set(map(str, ids))
            records = [
                (record_id, item)
                for record_id, item in records
                if record_id in wanted
            ]
        records.sort(key=lambda item: item[0])
        return {
            "ids": [record_id for record_id, _ in records],
            "documents": [item[0] for record_id, item in records],
            "metadatas": [item[1] for record_id, item in records],
        }


_PERSISTED_COLLECTIONS: dict[str, _FakeCollection] = {}


class _FakeMemory:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        key = str(Path(path).resolve())
        if key not in _PERSISTED_COLLECTIONS:
            _PERSISTED_COLLECTIONS[key] = _FakeCollection()
        self._collection = _PERSISTED_COLLECTIONS[key]
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None

    def _get_collection(self):
        return self._collection


class _Fixture:
    def __init__(
        self,
        *,
        database_type: str = "postgresql",
        tables=PG_TABLES,
        sql_records: list[tuple[str, str, dict]] | None = None,
    ) -> None:
        self.database_type = database_type
        self.database_name = (
            "gt_monitor" if database_type == "postgresql" else "lzh_monitor"
        )
        self.source_id = (
            "postgresql-main"
            if database_type == "postgresql"
            else "mysql-lzh-monitor"
        )
        self.scope = _scope_rows(database_type, tables=tables)
        self.allowed_tables = _allowed_tables(self.scope)
        self.ddls = _ddls_for(self.scope, database_type)
        self.records = []
        self.sql_records = list(sql_records or [])
        self.root = Path(tempfile.mkdtemp(prefix="e2b-fixture-"))
        self.memory_path = self.root / ".memory.candidate"
        self.business_documents_path = (
            self.root / "business_documents.json"
        )
        self.provenance_path = self.root / "asset_provenance.json"
        self.metadata_path = self.root / "metadata_index.json"
        self.metadata_path.write_text(
            json.dumps(_index_rows(self.scope), ensure_ascii=False),
            encoding="utf-8",
        )
        self.scope_fingerprint = hashlib.sha256(
            json.dumps(self.scope, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.review_policy_fingerprint = hashlib.sha256(
            b"review-policy-fixture"
        ).hexdigest()
        self.target_runtime_revision = 3
        self._build()

    def _grouped(self):
        grouped: dict[tuple[str, str], list[dict]] = {}
        for item in self.scope:
            grouped.setdefault(
                (str(item.get("schema") or ""), item["table"]),
                [],
            ).append(item)
        return grouped

    def _build(self) -> None:
        documentation_records = (
            DataSourceAssetPreparer._build_domain_document_records(
                self._grouped(),
                self.source_id,
            )
        )
        provenance_ddl = []
        for ddl in self.ddls:
            table_keys, column_keys = parse_ddl_identity(
                ddl,
                database_type=self.database_type,
                database_name=self.database_name,
            )
            provenance_ddl.append(
                {
                    "asset_type": "chroma_ddl",
                    "record_id": chroma_record_id(
                        self.source_id,
                        "ddl",
                        f"DDL\n{ddl}",
                    ),
                    "content_fingerprint": content_fingerprint(ddl),
                    "table_keys": table_keys,
                    "column_keys": column_keys,
                }
            )
        provenance_documentation = [
            {
                "asset_type": "chroma_documentation",
                "record_id": item["record_id"],
                "content_fingerprint": item["content_fingerprint"],
                "table_keys": item["table_keys"],
            }
            for item in documentation_records
        ]
        provenance_sql = []
        for record_id, document, metadata in self.sql_records:
            result = self.sql_guard().validate(
                json.loads(metadata["args_json"])["sql"],
                query="",
            )
            provenance_sql.append(
                {
                    "asset_type": "sql_tool_memory",
                    "record_id": record_id,
                    "content_fingerprint": str(
                        metadata.get("content_fingerprint") or ""
                    ),
                    "table_keys": sorted(
                        [list(key) for key in result.used_physical_tables]
                    ),
                    "column_keys": sorted(
                        [list(key) for key in result.used_physical_columns]
                    ),
                }
            )
        self.provenance_payload = build_provenance(
            source_id=self.source_id,
            runtime_revision=self.target_runtime_revision,
            scope_fingerprint=self.scope_fingerprint,
            review_policy_fingerprint=self.review_policy_fingerprint,
            assets={
                "documentation": documentation_records,
                "chroma_ddl": provenance_ddl,
                "chroma_documentation": provenance_documentation,
                "sql_tool_memory": provenance_sql,
            },
        )
        self.documents = [
            item["document"] for item in documentation_records
        ]
        self.business_documents_path.write_text(
            json.dumps(self.documents, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_provenance(self.provenance_path, self.provenance_payload)
        self._populate_memory()

    def sql_guard(self) -> SQLGuard:
        return SQLGuard(
            self.metadata_path,
            database_type=self.database_type,
            default_schema=(
                "public"
                if self.database_type == "postgresql"
                else self.database_name
            ),
        )

    def _populate_memory(self) -> None:
        collection = _FakeMemory(self.memory_path)._get_collection()
        payload = []
        for ddl in self.ddls:
            document = f"DDL\n{ddl}"
            payload.append(
                (
                    chroma_record_id(
                        self.source_id,
                        "ddl",
                        document,
                    ),
                    document,
                    {
                        "source_id": self.source_id,
                        "memory_type": "ddl",
                        "content_fingerprint": content_fingerprint(ddl),
                    },
                )
            )
        for document in self.documents:
            payload.append(
                (
                    chroma_record_id(
                        self.source_id,
                        "documentation",
                        document,
                    ),
                    document,
                    {
                        "source_id": self.source_id,
                        "memory_type": "documentation",
                        "is_text_memory": True,
                        "content_fingerprint": content_fingerprint(
                            document
                        ),
                    },
                )
            )
        for record_id, document, metadata in self.sql_records:
            payload.append(
                (str(record_id), str(document), dict(metadata))
            )
        self.expected_records = payload
        collection.add(
            ids=[record_id for record_id, _, _ in payload],
            documents=[document for _, document, _ in payload],
            metadatas=[metadata for _, _, metadata in payload],
        )

    def validate(self) -> dict[str, int]:
        import backend.memory as memory_module

        with patch.object(
            memory_module,
            "create_memory",
            side_effect=_FakeMemory,
        ):
            return validate_runtime_candidate_assets(
                source_id=self.source_id,
                database_type=self.database_type,
                database_name=self.database_name,
                allowed_tables=self.allowed_tables,
                scope=self.scope,
                scope_fingerprint=self.scope_fingerprint,
                review_policy_fingerprint=self.review_policy_fingerprint,
                target_runtime_revision=self.target_runtime_revision,
                business_documents_path=self.business_documents_path,
                provenance_path=self.provenance_path,
                memory_path=self.memory_path,
                expected_records=self.expected_records,
                sql_guard=self.sql_guard(),
            )

    def collection(self) -> _FakeCollection:
        return _FakeMemory(self.memory_path)._get_collection()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _assert_raises(fixture: _Fixture, mutate=None, keyword: str = "") -> None:
    if mutate is not None:
        mutate(fixture)
    try:
        fixture.validate()
    except DataSourceCatalogError as exc:
        if keyword:
            assert keyword in str(exc), f"期望 {keyword!r}，实际 {exc}"
    except Exception as exc:
        raise AssertionError(
            f"应抛出 DataSourceCatalogError，实际 {type(exc).__name__}"
        ) from exc
    else:
        raise AssertionError("校验应失败关闭")


def _fingerprint_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 14.1


def test_doc_normal_multi_domain_passes() -> None:
    fixture = _Fixture(
        tables=tuple(f"table_{index}" for index in range(10)),
    )
    try:
        result = fixture.validate()
        assert result["documentation_records"] == len(fixture.documents)
    finally:
        fixture.cleanup()


def test_doc_missing_allowed_table() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            del fx.provenance_payload["assets"]["documentation"][0][
                "table_keys"
            ][0]
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "documentation table union")
    finally:
        fixture.cleanup()


def test_doc_non_allowed_table() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["documentation"][0][
                "table_keys"
            ].append(["public", "outside_table"])
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "非 allowed")
    finally:
        fixture.cleanup()


def test_doc_same_table_in_two_documents() -> None:
    fixture = _Fixture(
        tables=tuple(f"table_{index}" for index in range(9)),
    )
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"]["documentation"]
            records[1]["table_keys"].append(records[0]["table_keys"][0])
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "多条文档")
    finally:
        fixture.cleanup()


def test_doc_duplicate_key_in_one_document() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            record = fx.provenance_payload["assets"]["documentation"][0]
            record["table_keys"].append(record["table_keys"][0])
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "重复")
    finally:
        fixture.cleanup()


def test_doc_empty_table_keys() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["documentation"][0][
                "table_keys"
            ] = []
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "为空")
    finally:
        fixture.cleanup()


def test_doc_count_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.business_documents_path.write_text(
                json.dumps(fx.documents + ["额外文档"]),
                encoding="utf-8",
            )
        _assert_raises(fixture, mutate, "记录数")
    finally:
        fixture.cleanup()


def test_doc_text_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            documents = list(fx.documents)
            documents[0] = documents[0] + "（被篡改）"
            fx.business_documents_path.write_text(
                json.dumps(documents),
                encoding="utf-8",
            )
        _assert_raises(fixture, mutate, "不一致")
    finally:
        fixture.cleanup()


def test_doc_fingerprint_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["documentation"][0][
                "content_fingerprint"
            ] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "content_fingerprint")
    finally:
        fixture.cleanup()


def test_doc_record_id_duplicate() -> None:
    fixture = _Fixture(
        tables=tuple(f"table_{index}" for index in range(9)),
    )
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"]["documentation"]
            records[1]["record_id"] = records[0]["record_id"]
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "record_id 重复")
    finally:
        fixture.cleanup()


def test_doc_source_id_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["source_id"] = "other-source"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "source_id")
    finally:
        fixture.cleanup()


def test_doc_runtime_revision_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["runtime_revision"] = 99
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "runtime_revision")
    finally:
        fixture.cleanup()


def test_doc_scope_fingerprint_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["scope_fingerprint"] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "scope_fingerprint")
    finally:
        fixture.cleanup()


def test_doc_review_policy_fingerprint_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["review_policy_fingerprint"] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "review_policy_fingerprint")
    finally:
        fixture.cleanup()


def test_doc_provenance_missing() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_path.unlink()
        _assert_raises(fixture, mutate, "不可读")
    finally:
        fixture.cleanup()


def test_doc_provenance_corrupted() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_path.write_text("{broken json", encoding="utf-8")
        _assert_raises(fixture, mutate, "不可读")
    finally:
        fixture.cleanup()


# ---------------------------------------------------------------- 14.2


def test_chroma_ddl_normal_readback_passes() -> None:
    fixture = _Fixture()
    try:
        result = fixture.validate()
        assert result["chroma_ddl_records"] == len(fixture.ddls)
    finally:
        fixture.cleanup()


def test_chroma_ddl_count_less() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            del collection.records[first_id]
        _assert_raises(fixture, mutate, "count")
    finally:
        fixture.cleanup()


def test_chroma_ddl_count_more() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            collection.records["b5-extra"] = (
                "DDL\nCREATE TABLE \"public\".\"x\" (\"id\" bigint);",
                {"memory_type": "ddl", "source_id": fx.source_id},
            )
        _assert_raises(fixture, mutate, "count")
    finally:
        fixture.cleanup()


def test_chroma_ddl_missing_id() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            missing_id = fx.expected_records[0][0]
            del collection.records[missing_id]
            # 保持 count 一致需同步调整预期，这里直接检查 ID 集合缺失。
            collection.records[missing_id + "-ghost"] = (
                "DDL\nCREATE TABLE \"public\".\"x\" (\"id\" bigint);",
                {"memory_type": "ddl", "source_id": fx.source_id},
            )
        _assert_raises(fixture, mutate, "ID 集合")
    finally:
        fixture.cleanup()


def test_chroma_ddl_id_replaced() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            old_id = fx.expected_records[0][0]
            document, metadata = collection.records.pop(old_id)
            collection.records[old_id + "-ghost"] = (document, metadata)
        _assert_raises(fixture, mutate, "ID 集合")
    finally:
        fixture.cleanup()


def test_chroma_ddl_duplicate_expected_id() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            record_id, document, metadata = fx.expected_records[0]
            fx.expected_records.append((record_id, document, metadata))
            fx.collection().add(
                ids=[record_id],
                documents=[document],
                metadatas=[metadata],
            )
        _assert_raises(fixture, mutate, keyword="预期 ID 重复")
    finally:
        fixture.cleanup()


def test_chroma_ddl_wrong_prefix() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            stripped = document.replace("DDL\n", "", 1)
            collection.records[first_id] = (stripped, metadata)
            fx.expected_records[0] = (first_id, stripped, metadata)
        _assert_raises(fixture, mutate, "前缀")
    finally:
        fixture.cleanup()


def test_chroma_ddl_document_changed() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            collection.records[first_id] = (
                document + "\n-- 篡改",
                metadata,
            )
        _assert_raises(fixture, mutate, "文档不一致")
    finally:
        fixture.cleanup()


def test_chroma_ddl_memory_type_wrong() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            metadata["memory_type"] = "documentation"
            collection.records[first_id] = (document, metadata)
        _assert_raises(fixture, mutate, "metadata")
    finally:
        fixture.cleanup()


def test_chroma_ddl_source_id_wrong() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            metadata["source_id"] = "other-source"
            collection.records[first_id] = (document, metadata)
        _assert_raises(fixture, mutate, "metadata")
    finally:
        fixture.cleanup()


def test_chroma_ddl_fingerprint_wrong() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            metadata["content_fingerprint"] = "deadbeef"
            collection.records[first_id] = (document, metadata)
        _assert_raises(fixture, mutate, "metadata")
    finally:
        fixture.cleanup()


def _tamper_ddl(fixture: _Fixture, new_ddl: str) -> None:
    """把候选集合、预期记录和 provenance 一致地替换为新的 DDL。"""
    collection = fixture.collection()
    first_id = fixture.expected_records[0][0]
    document, metadata = collection.records[first_id]
    metadata = dict(metadata)
    metadata["content_fingerprint"] = content_fingerprint(new_ddl)
    collection.records[first_id] = (f"DDL\n{new_ddl}", metadata)
    fixture.expected_records[0] = (
        first_id,
        f"DDL\n{new_ddl}",
        metadata,
    )
    try:
        table_keys, column_keys = parse_ddl_identity(
            new_ddl,
            database_type=fixture.database_type,
            database_name=fixture.database_name,
        )
    except DataSourceCatalogError:
        table_keys = column_keys = None
    provenance_ddl = fixture.provenance_payload["assets"]["chroma_ddl"]
    for record in provenance_ddl:
        if record["record_id"] == first_id:
            if table_keys is not None:
                record["table_keys"] = table_keys
                record["column_keys"] = column_keys
            record["content_fingerprint"] = content_fingerprint(new_ddl)
            break
    write_provenance(fixture.provenance_path, fixture.provenance_payload)


def test_chroma_ddl_parse_fails() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            _tamper_ddl(fx, "这不是合法的 CREATE TABLE")
        _assert_raises(fixture, mutate, "DDL")
    finally:
        fixture.cleanup()


def test_chroma_ddl_non_allowed_table() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            _tamper_ddl(
                fx,
                'CREATE TABLE "public"."outside_table" (\n'
                '  "id" bigint\n);',
            )
        _assert_raises(fixture, mutate, "非 allowed")
    finally:
        fixture.cleanup()


def test_chroma_ddl_scope_out_column() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            _tamper_ddl(
                fx,
                'CREATE TABLE "public"."monitor_data" (\n'
                '  "id" bigint,\n'
                '  "name" numeric,\n'
                '  "value" numeric,\n'
                '  "secret" numeric,\n'
                '  PRIMARY KEY ("id")\n'
                ');',
            )
        _assert_raises(fixture, mutate, "scope 外列")
    finally:
        fixture.cleanup()


def test_chroma_ddl_provenance_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            record = fx.provenance_payload["assets"]["chroma_ddl"][0]
            record["column_keys"].append(
                ["public", "monitor_data", "secret"]
            )
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "column_keys")
    finally:
        fixture.cleanup()


def test_chroma_ddl_provenance_extra_record() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["chroma_ddl"].append(
                {
                    "asset_type": "chroma_ddl",
                    "record_id": "b5-ghost",
                    "content_fingerprint": "x",
                    "table_keys": [["public", "monitor_data"]],
                    "column_keys": [["public", "monitor_data", "id"]],
                }
            )
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "记录数与 provenance")
    finally:
        fixture.cleanup()


# ---------------------------------------------------------------- 14.3


def test_chroma_doc_normal_passes() -> None:
    fixture = _Fixture()
    try:
        result = fixture.validate()
        assert result["chroma_documentation_records"] == len(
            fixture.documents
        )
    finally:
        fixture.cleanup()


def test_chroma_doc_missing_is_text_memory_rejected() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            document, metadata = collection.records[doc_id]
            metadata = dict(metadata)
            metadata.pop("is_text_memory", None)
            collection.records[doc_id] = (document, metadata)
        _assert_raises(fixture, mutate, "is_text_memory")
    finally:
        fixture.cleanup()


def test_chroma_doc_is_text_memory_false_rejected() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            document, metadata = collection.records[doc_id]
            metadata = dict(metadata)
            metadata["is_text_memory"] = False
            collection.records[doc_id] = (document, metadata)
        _assert_raises(fixture, mutate, "is_text_memory")
    finally:
        fixture.cleanup()


def test_chroma_doc_is_text_memory_string_true_rejected() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            document, metadata = collection.records[doc_id]
            metadata = dict(metadata)
            metadata["is_text_memory"] = "true"
            collection.records[doc_id] = (document, metadata)
        _assert_raises(fixture, mutate, "is_text_memory")
    finally:
        fixture.cleanup()


def test_chroma_doc_expected_missing_is_text_memory_rejected() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            doc_index = len(fx.ddls)
            record_id, document, metadata = fx.expected_records[doc_index]
            metadata = dict(metadata)
            metadata.pop("is_text_memory", None)
            fx.expected_records[doc_index] = (record_id, document, metadata)
        _assert_raises(fixture, mutate, "预期 metadata is_text_memory")
    finally:
        fixture.cleanup()


def test_chroma_ddl_is_text_memory_set_rejected() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            metadata = dict(metadata)
            metadata["is_text_memory"] = True
            collection.records[first_id] = (document, metadata)
        _assert_raises(fixture, mutate, "禁止设置 is_text_memory")
    finally:
        fixture.cleanup()


def test_sql_tool_memory_is_text_memory_set_rejected() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            record_id = fx.expected_records[-1][0]
            document, metadata = collection.records[record_id]
            metadata = dict(metadata)
            metadata["is_text_memory"] = True
            collection.records[record_id] = (document, metadata)
        _assert_raises(fixture, mutate, "禁止设置 is_text_memory")
    finally:
        fixture.cleanup()


def test_chroma_doc_missing_record() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            del collection.records[doc_id]
        _assert_raises(fixture, mutate, "count")
    finally:
        fixture.cleanup()


def test_chroma_doc_extra_record() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            collection.records["b5-extra-doc"] = (
                "业务领域：额外。主要表：无。",
                {
                    "memory_type": "documentation",
                    "source_id": fx.source_id,
                },
            )
        _assert_raises(fixture, mutate, "count")
    finally:
        fixture.cleanup()


def test_chroma_doc_text_changed() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            document, metadata = collection.records[doc_id]
            collection.records[doc_id] = (document + "（改）", metadata)
        _assert_raises(fixture, mutate, "文档不一致")
    finally:
        fixture.cleanup()


def test_chroma_doc_record_id_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"][
                "chroma_documentation"
            ]
            records[0]["record_id"] = "b5-wrong-id"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "无对应 provenance")
    finally:
        fixture.cleanup()


def test_chroma_doc_fingerprint_mismatch() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"][
                "chroma_documentation"
            ]
            records[0]["content_fingerprint"] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "content_fingerprint")
    finally:
        fixture.cleanup()


def test_chroma_doc_memory_type_wrong() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            doc_id = fx.expected_records[len(fx.ddls)][0]
            document, metadata = collection.records[doc_id]
            metadata["memory_type"] = "ddl"
            collection.records[doc_id] = (document, metadata)
        _assert_raises(fixture, mutate, "metadata")
    finally:
        fixture.cleanup()


def test_chroma_doc_table_keys_out_of_bounds() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"][
                "chroma_documentation"
            ]
            fx.provenance_payload["assets"]["documentation"][0][
                "table_keys"
            ] = [["public", "outside_table"]]
            records[0]["table_keys"] = [["public", "outside_table"]]
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "非 allowed")
    finally:
        fixture.cleanup()


def test_chroma_doc_union_missing_table() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            records = fx.provenance_payload["assets"][
                "chroma_documentation"
            ]
            records[0]["table_keys"] = records[0]["table_keys"][1:]
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "table_keys")
    finally:
        fixture.cleanup()


def test_chroma_doc_same_table_twice() -> None:
    fixture = _Fixture(
        tables=tuple(f"table_{index}" for index in range(9)),
    )
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["documentation"][1][
                "table_keys"
            ].append(
                fx.provenance_payload["assets"]["documentation"][0][
                    "table_keys"
                ][0]
            )
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "多条文档")
    finally:
        fixture.cleanup()


# ---------------------------------------------------------------- 14.5


def _sql_record(
    source_id: str,
    sql: str,
    *,
    category="sql_example",
    tool_name="run_sql",
    extra_metadata: dict | None = None,
) -> tuple[str, str, dict]:
    record_id = "toolmem-v1-" + _fingerprint_hash(sql)[:16]
    metadata = {
        "category": category,
        "tool_name": tool_name,
        "args_json": json.dumps({"sql": sql}, ensure_ascii=False),
        "source_id": source_id,
        "content_fingerprint": content_fingerprint(sql),
    }
    metadata.update(extra_metadata or {})
    return record_id, sql, metadata


def _sql_fixture(sql: str, **kwargs):
    source_id = "postgresql-main"
    record = _sql_record(source_id, sql, **kwargs)
    return _Fixture(sql_records=[record])


def test_sql_normal_record_passes() -> None:
    fixture = _sql_fixture(
        'SELECT "id", "value" FROM "public"."monitor_data" '
        'WHERE "id" > 0 LIMIT 10'
    )
    try:
        result = fixture.validate()
        assert result["sql_tool_memory_records"] == 1
    finally:
        fixture.cleanup()


def test_sql_args_json_invalid() -> None:
    fixture = _sql_fixture('SELECT "id" FROM "public"."monitor_data"')
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            record_id = fx.expected_records[-1][0]
            document, metadata = collection.records[record_id]
            metadata["args_json"] = "{broken"
            collection.records[record_id] = (document, metadata)
        _assert_raises(fixture, mutate, "args_json")
    finally:
        fixture.cleanup()


def test_sql_args_json_missing_sql() -> None:
    fixture = _sql_fixture('SELECT "id" FROM "public"."monitor_data"')
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            record_id = fx.expected_records[-1][0]
            document, metadata = collection.records[record_id]
            metadata["args_json"] = json.dumps({"question": "x"})
            collection.records[record_id] = (document, metadata)
        _assert_raises(fixture, mutate, "args_json")
    finally:
        fixture.cleanup()


def _replace_readback_sql(fixture: _Fixture, new_sql: str) -> None:
    collection = fixture.collection()
    record_id = fixture.expected_records[-1][0]
    document, metadata = collection.records[record_id]
    metadata["args_json"] = json.dumps(
        {"sql": new_sql},
        ensure_ascii=False,
    )
    collection.records[record_id] = (document, metadata)


def test_sql_readback_replaced_with_select_star() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(
            fixture,
            lambda fx: _replace_readback_sql(
                fx,
                'SELECT * FROM "public"."monitor_data"',
            ),
            "args_json",
        )
    finally:
        fixture.cleanup()


def test_sql_readback_replaced_with_non_allowed_table() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(
            fixture,
            lambda fx: _replace_readback_sql(
                fx,
                'SELECT "id" FROM "public"."outside_table"',
            ),
            "args_json",
        )
    finally:
        fixture.cleanup()


def test_sql_readback_replaced_with_scope_out_column() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(
            fixture,
            lambda fx: _replace_readback_sql(
                fx,
                'SELECT "secret" FROM "public"."monitor_data"',
            ),
            "args_json",
        )
    finally:
        fixture.cleanup()


def test_sql_readback_replaced_legal_sql_provenance_stale() -> None:
    """实际回读被替换为另一条合法 SQL，但 provenance 仍是旧 SQL 的身份。"""
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(
            fixture,
            lambda fx: _replace_readback_sql(
                fx,
                'SELECT "name" FROM "public"."station_dict"',
            ),
            "args_json",
        )
    finally:
        fixture.cleanup()


def test_sql_tool_name_wrong() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"',
        tool_name="other_tool",
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="tool_name")
    finally:
        fixture.cleanup()


def test_sql_category_wrong() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"',
        category="text_memory",
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="category")
    finally:
        fixture.cleanup()


def test_sql_contract_conflict() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"',
        category="sql_example",
        tool_name="not_run_sql",
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="tool_name")
    finally:
        fixture.cleanup()


def test_sql_non_allowed_table() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."outside_table"'
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="未知表")
    finally:
        fixture.cleanup()


def test_sql_scope_out_column() -> None:
    fixture = _sql_fixture(
        'SELECT "secret" FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="未知字段")
    finally:
        fixture.cleanup()


def test_sql_select_star_rejected() -> None:
    fixture = _sql_fixture('SELECT * FROM "public"."monitor_data"')
    try:
        _assert_raises(fixture, mutate=None, keyword="通配符")
    finally:
        fixture.cleanup()


def test_sql_table_star_rejected() -> None:
    fixture = _sql_fixture(
        'SELECT monitor_data.* FROM "public"."monitor_data"'
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="通配符")
    finally:
        fixture.cleanup()


def test_sql_ambiguous_unqualified_column() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data" '
        'JOIN "public"."station_dict" '
        'ON "monitor_data"."id" = "station_dict"."id"'
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="歧义")
    finally:
        fixture.cleanup()


def test_sql_cte_out_of_scope_table() -> None:
    fixture = _sql_fixture(
        'WITH x AS (SELECT "id" FROM "public"."outside_table") '
        'SELECT "id" FROM x'
    )
    try:
        _assert_raises(fixture, mutate=None, keyword="未知表")
    finally:
        fixture.cleanup()


def test_sql_provenance_table_mismatch() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        def mutate(fx: _Fixture) -> None:
            record = fx.provenance_payload["assets"]["sql_tool_memory"][0]
            record["table_keys"].append(["public", "station_dict"])
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "table_keys")
    finally:
        fixture.cleanup()


def test_sql_provenance_column_mismatch() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        def mutate(fx: _Fixture) -> None:
            record = fx.provenance_payload["assets"]["sql_tool_memory"][0]
            record["column_keys"].append(
                ["public", "monitor_data", "secret"]
            )
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "column_keys")
    finally:
        fixture.cleanup()


def test_sql_record_id_duplicate() -> None:
    record = (
        "toolmem-v1-dup",
        'SELECT "id" FROM "public"."monitor_data"',
        {
            "category": "sql_example",
            "tool_name": "run_sql",
            "args_json": json.dumps(
                {"sql": 'SELECT "id" FROM "public"."monitor_data"'}
            ),
        },
    )
    fixture = _Fixture(sql_records=[record, record])
    try:
        _assert_raises(fixture, mutate=None, keyword="ID 重复")
    finally:
        fixture.cleanup()


def test_chroma_ddl_provenance_fingerprint_tampered() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["chroma_ddl"][0][
                "content_fingerprint"
            ] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "content_fingerprint")
    finally:
        fixture.cleanup()


def test_chroma_ddl_metadata_fingerprint_tampered() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            collection = fx.collection()
            first_id = next(iter(collection.records))
            document, metadata = collection.records[first_id]
            metadata["content_fingerprint"] = "deadbeef"
            collection.records[first_id] = (document, metadata)
        _assert_raises(fixture, mutate, "content_fingerprint")
    finally:
        fixture.cleanup()


def test_chroma_ddl_provenance_asset_type_tampered() -> None:
    fixture = _Fixture()
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["chroma_ddl"][0][
                "asset_type"
            ] = "not_chroma_ddl"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "asset_type")
    finally:
        fixture.cleanup()


def test_sql_provenance_fingerprint_tampered() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["sql_tool_memory"][0][
                "content_fingerprint"
            ] = "deadbeef"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "content_fingerprint")
    finally:
        fixture.cleanup()


def test_sql_provenance_asset_type_tampered() -> None:
    fixture = _sql_fixture(
        'SELECT "id" FROM "public"."monitor_data"'
    )
    try:
        def mutate(fx: _Fixture) -> None:
            fx.provenance_payload["assets"]["sql_tool_memory"][0][
                "asset_type"
            ] = "chroma_ddl"
            write_provenance(fx.provenance_path, fx.provenance_payload)
        _assert_raises(fixture, mutate, "asset_type")
    finally:
        fixture.cleanup()


def test_mysql_documentation_schema_normalization_passes() -> None:
    """MySQL 文档 provenance 表身份必须以数据库名规范化后参与校验。"""
    fixture = _Fixture(
        database_type="mysql",
        tables=("monitor_data", "station_dict"),
    )
    try:
        result = fixture.validate()
        assert result["documentation_records"] >= 1
    finally:
        fixture.cleanup()


def test_mysql_chroma_documentation_normalization_passes() -> None:
    fixture = _Fixture(
        database_type="mysql",
        tables=("monitor_data", "station_dict"),
    )
    try:
        result = fixture.validate()
        assert result["chroma_documentation_records"] >= 1
    finally:
        fixture.cleanup()


def test_mysql_sql_record_unqualified_table_passes() -> None:
    fixture = _Fixture(
        database_type="mysql",
        tables=("monitor_data", "station_dict"),
        sql_records=[
            _sql_record(
                "mysql-lzh-monitor",
                "SELECT `id`, `value` FROM `monitor_data` LIMIT 10",
            )
        ],
    )
    try:
        result = fixture.validate()
        assert result["sql_tool_memory_records"] == 1
    finally:
        fixture.cleanup()


# ---------------------------------------------------------------- 14.6


def _setup_with_key(directory: Path):
    """镜像 publish-guard 的 _setup，但保留 Fernet 密钥供重启恢复使用。"""
    from tools.test_data_source_publish_guard import METADATA

    key = Fernet.generate_key().decode("ascii")
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
        cipher=CredentialCipher(key),
    )
    catalog.initialize()
    source = catalog.create(
        display_name="重启恢复测试",
        description="",
        database_type="postgresql",
        host="127.0.0.1",
        port=5432,
        database_name="gt_monitor",
        schema_name="public",
        username="readonly",
        password="secret",
    )
    catalog.save_discovery(source.source_id, METADATA)
    record = catalog.require(source.source_id)
    return catalog, source.source_id, record.metadata_path.parent, key


def _formal_hashes(catalog: DataSourceCatalog, source_id: str) -> tuple:
    from tools.test_data_source_publish_guard import _path_hash

    record = catalog.require(source_id)
    root = record.metadata_path.parent
    return (
        _path_hash(record.metadata_path),
        _path_hash(root / "ddl_memories.json"),
        _path_hash(root / "business_documents.json"),
        _path_hash(root / "asset_provenance.json"),
        _path_hash(record.memory_path),
    )


def test_e2b_failure_keeps_revision_and_assets() -> None:
    from tools.test_data_source_publish_guard import (
        _prepare_with_memory,
        _save_scope,
        _seed_reviews,
        _setup,
    )

    with tempfile.TemporaryDirectory(prefix="e2b-fail-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, ("monitor_data",))
            _seed_reviews(
                catalog,
                source_id,
                {"monitor_data": ("active", "present")},
            )
            preparer = DataSourceAssetPreparer(catalog)
            _prepare_with_memory(preparer, source_id)
            before_revision = catalog.require(source_id).runtime_revision
            before_hashes = _formal_hashes(catalog, source_id)

            import backend.memory as memory_module

            class CorruptMemory(_FakeMemory):
                def _get_collection(self):
                    collection = super()._get_collection()
                    original_add = collection.add

                    def corrupt_add(*, ids, documents, metadatas):
                        documents = list(documents)
                        documents[0] = documents[0] + "（被篡改）"
                        original_add(
                            ids=ids,
                            documents=documents,
                            metadatas=metadatas,
                        )

                    collection.add = corrupt_add
                    return collection

            with patch.object(
                memory_module,
                "create_memory",
                side_effect=CorruptMemory,
            ):
                try:
                    preparer.prepare(source_id)
                except DataSourceCatalogError:
                    pass
                else:
                    raise AssertionError("候选文档被篡改时应发布失败")
            after = catalog.require(source_id)
            assert after.runtime_revision == before_revision
            assert _formal_hashes(catalog, source_id) == before_hashes
            assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_set_enabled_provenance_gate() -> None:
    from tools.test_data_source_publish_guard import (
        _prepare_with_memory,
        _save_scope,
        _seed_reviews,
        _setup,
    )

    with tempfile.TemporaryDirectory(prefix="e2b-enable-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, ("monitor_data",))
            _seed_reviews(
                catalog,
                source_id,
                {"monitor_data": ("active", "present")},
            )
            _prepare_with_memory(
                DataSourceAssetPreparer(catalog),
                source_id,
            )
            catalog.set_enabled(source_id, False)
            record = catalog.require(source_id)
            root = record.metadata_path.parent
            provenance_path = root / "asset_provenance.json"
            provenance = json.loads(
                provenance_path.read_text(encoding="utf-8")
            )

            def enable() -> None:
                catalog.set_enabled(source_id, True)

            # 缺 provenance 拒绝启用
            provenance_path.unlink()
            try:
                enable()
            except DataSourceCatalogError as exc:
                assert "asset_provenance.json" in str(exc)
            else:
                raise AssertionError("缺 provenance 时应拒绝启用")
            # 重建 provenance 后哈希被篡改也拒绝
            manifest = json.loads(
                (root / "asset_manifest.json").read_text(encoding="utf-8")
            )
            write_provenance(provenance_path, provenance)
            provenance["runtime_revision"] = int(
                provenance["runtime_revision"]
            ) + 1
            write_provenance(provenance_path, provenance)
            try:
                enable()
            except DataSourceCatalogError as exc:
                assert "provenance" in str(exc)
            else:
                raise AssertionError("provenance 哈希变化时应拒绝启用")
            # manifest.provenance_hash 单独被篡改也拒绝
            provenance["runtime_revision"] -= 1
            write_provenance(provenance_path, provenance)
            manifest["provenance_hash"] = "deadbeef"
            (root / "asset_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            try:
                enable()
            except DataSourceCatalogError as exc:
                assert "provenance" in str(exc)
            else:
                raise AssertionError("manifest 哈希被篡改时应拒绝启用")
            # 恢复完整一致后允许启用
            from backend.data_source_asset_provenance import (
                provenance_fingerprint,
            )

            manifest["provenance_hash"] = provenance_fingerprint(
                json.loads(provenance_path.read_text(encoding="utf-8"))
            )
            (root / "asset_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            enable()
            assert catalog.require(source_id).enabled_for_chat
            assert catalog.require(source_id).runtime_revision == 1
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_provenance_participates_in_rollback() -> None:
    """备份/安装阶段故障时 provenance 与其他正式资产一起回滚。"""
    from tools.test_data_source_publish_guard import (
        _prepare_with_memory,
        _save_scope,
        _seed_reviews,
        _setup,
    )

    with tempfile.TemporaryDirectory(prefix="e2b-rollback-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, ("monitor_data",))
            _seed_reviews(
                catalog,
                source_id,
                {"monitor_data": ("active", "present")},
            )
            preparer = DataSourceAssetPreparer(catalog)
            _prepare_with_memory(preparer, source_id)
            before_revision = catalog.require(source_id).runtime_revision
            before_hashes = _formal_hashes(catalog, source_id)

            import backend.memory as memory_module

            points = ("after_backup_provenance", "after_install_provenance")
            for point in points:
                class CrashPreparer(DataSourceAssetPreparer):
                    def _inject(self, hook: str) -> None:
                        if hook == point:
                            raise RuntimeError(f"注入故障：{point}")
                        super()._inject(hook)

                with patch.object(
                    memory_module,
                    "create_memory",
                    side_effect=_FakeMemory,
                ):
                    try:
                        CrashPreparer(catalog).prepare(source_id)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError(f"{point} 注入未生效")
                after = catalog.require(source_id)
                assert after.runtime_revision == before_revision
                assert _formal_hashes(catalog, source_id) == before_hashes
                assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_restart_recovery_keeps_provenance_consistent() -> None:
    """模拟崩溃后重启：provenance 与其他资产一起前滚或回滚，不允许新旧混搭。"""
    from tools.test_data_source_publish_guard import (
        _prepare_with_memory,
        _save_scope,
        _seed_reviews,
        _setup,
    )
    from backend.data_source_connectors import (
        DataSourceAssetCleaner,
        SimulatedProcessCrash,
    )

    with tempfile.TemporaryDirectory(prefix="e2b-restart-") as directory:
        catalog, source_id, asset_root, cipher_key = _setup_with_key(
            Path(directory)
        )
        try:
            _save_scope(catalog, source_id, ("monitor_data", "station_dict"))
            _seed_reviews(
                catalog,
                source_id,
                {
                    "monitor_data": ("active", "present"),
                    "station_dict": ("active", "present"),
                },
            )
            preparer = DataSourceAssetPreparer(catalog)
            _prepare_with_memory(preparer, source_id)
            before_hashes = _formal_hashes(catalog, source_id)
            db_path = catalog.db_path

            import backend.memory as memory_module

            def crash_at(point: str):
                def inject(actual: str) -> None:
                    if actual == point:
                        raise SimulatedProcessCrash(point)

                return inject

            crash_preparer = DataSourceAssetPreparer(
                catalog,
                fault_injector=crash_at("after_install_provenance"),
            )
            with patch.object(
                memory_module,
                "create_memory",
                side_effect=_FakeMemory,
            ):
                try:
                    crash_preparer.prepare(source_id)
                except SimulatedProcessCrash:
                    pass
                else:
                    raise AssertionError("after_install_provenance 崩溃未触发")
            assert catalog.active_asset_batches(source_id)
            restarted = DataSourceCatalog(
                db_path,
                cipher=CredentialCipher(cipher_key),
                environ={},
            )
            restarted.initialize()
            DataSourceAssetCleaner(
                restarted,
            ).recover_incomplete_batches(source_id, grace_seconds=0)
            record = restarted.require(source_id)
            root = record.metadata_path.parent
            provenance = json.loads(
                (root / "asset_provenance.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (root / "asset_manifest.json").read_text(encoding="utf-8")
            )
            identity = json.loads(
                (record.memory_path / ".asset_identity.json").read_text(
                    encoding="utf-8"
                )
            )
            provenance_hash = provenance_fingerprint(provenance)
            assert manifest["provenance_hash"] == provenance_hash
            assert identity["provenance_hash"] == provenance_hash
            assert provenance["runtime_revision"] == record.runtime_revision
            assert _formal_hashes(restarted, source_id) == before_hashes
            assert not restarted.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def main() -> int:
    import traceback

    failed = 0
    total = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        total += 1
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

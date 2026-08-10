"""阶段 E-2B：候选运行资产完整性硬门直接回归测试。

覆盖 Documentation / provenance（14.1）、Chroma DDL（14.2）、
Chroma Documentation（14.3）、SQL Tool Memory（14.5）的校验拒绝与通过。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_asset_provenance import (
    build_provenance,
    chroma_record_id,
    content_fingerprint,
    write_provenance,
)
from backend.data_source_catalog import DataSourceCatalogError
from backend.data_source_runtime_asset_validator import (
    parse_ddl_identity,
    validate_runtime_candidate_assets,
)
from backend.sql_guard import SQLGuard


SOURCE_ID = "postgresql-main"
DATABASE_TYPE = "postgresql"
DATABASE_NAME = "gt_monitor"
SCOPE_FINGERPRINT = "scope-fp"
REVIEW_FINGERPRINT = "policy-fp"
TARGET_REVISION = 3


def _index_rows():
    return [
        {"schema": "public", "table": "t", "column": "id"},
        {"schema": "public", "table": "t", "column": "value"},
        {"schema": "public", "table": "u", "column": "id"},
        {"schema": "public", "table": "u", "column": "code"},
    ]


def _scope():
    return [
        {"schema": "public", "table": "t", "column": "id"},
        {"schema": "public", "table": "t", "column": "value"},
        {"schema": "public", "table": "u", "column": "id"},
        {"schema": "public", "table": "u", "column": "code"},
    ]


def _allowed():
    return {("public", "t"), ("public", "u")}


def _ddl_for(table, columns, primary=()):
    definitions = [f'  "{column}" bigint' for column in columns]
    if primary:
        definitions.append(
            "  PRIMARY KEY (" + ", ".join(f'"{c}"' for c in primary) + ")"
        )
    return (
        f'CREATE TABLE "public"."{table}" (\n'
        + ",\n".join(definitions)
        + "\n);"
    )


class _FakeCollection:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.total = 0
        self.records = []

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
        self.records = list(zip(ids, documents, metadatas))

    def count(self) -> int:
        return len(self.records)

    def get(self, *, ids=None, where=None, include=None) -> dict:
        records = list(self.records)
        if where:
            records = [
                item
                for item in records
                if all(
                    item[2].get(key) == value
                    for key, value in where.items()
                )
            ]
        if ids is not None:
            wanted = set(ids)
            records = [item for item in records if item[0] in wanted]
        return {
            "ids": [item[0] for item in records],
            "documents": [item[1] for item in records],
            "metadatas": [item[2] for item in records],
        }


_PERSISTED_COLLECTIONS: dict[str, _FakeCollection] = {}


class _FakeMemory:
    def __init__(self, path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve())
        if key not in _PERSISTED_COLLECTIONS:
            _PERSISTED_COLLECTIONS[key] = _FakeCollection(path)
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None
        self._collection = _PERSISTED_COLLECTIONS[key]

    def _get_collection(self):
        return self._collection


def _fixtures(root: Path):
    """标准候选：两张表各一个文档，一个 DDL，一条 SQL Tool Memory。"""
    index_path = root / "metadata_index.json"
    index_path.write_text(json.dumps(_index_rows()), encoding="utf-8")

    doc_t = (
        "业务领域：监测。可回答该领域的明细、聚合、排名和有时间字段时的趋势问题。"
        "主要表：t（监测）；粒度：id。只允许使用文中列出的可靠关系；"
        "不得因同名 id/name 自动 JOIN，不得跨小时/日/月粒度直接拼接。"
    )
    doc_u = doc_t.replace("t（监测）", "u（监测）").replace("；粒度：id", "；粒度：id")
    documentation_records = [
        {
            "asset_type": "documentation",
            "record_id": chroma_record_id(SOURCE_ID, "documentation", doc_t),
            "document": doc_t,
            "content_fingerprint": content_fingerprint(doc_t),
            "table_keys": [["public", "t"]],
        },
        {
            "asset_type": "documentation",
            "record_id": chroma_record_id(SOURCE_ID, "documentation", doc_u),
            "document": doc_u,
            "content_fingerprint": content_fingerprint(doc_u),
            "table_keys": [["public", "u"]],
        },
    ]
    documents = [item["document"] for item in documentation_records]
    business_documents_path = root / "business_documents.json"
    business_documents_path.write_text(
        json.dumps(documents, ensure_ascii=False),
        encoding="utf-8",
    )

    ddl = _ddl_for("t", ["id", "value"], primary=("id",))
    ddl_doc = f"DDL\n{ddl}"
    table_keys, column_keys = parse_ddl_identity(
        ddl,
        database_type=DATABASE_TYPE,
        database_name=DATABASE_NAME,
    )
    chroma_ddl_records = [
        {
            "asset_type": "chroma_ddl",
            "record_id": chroma_record_id(SOURCE_ID, "ddl", ddl_doc),
            "content_fingerprint": hashlib.sha256(
                ddl.encode("utf-8")
            ).hexdigest(),
            "table_keys": table_keys,
            "column_keys": column_keys,
        }
    ]
    chroma_documentation_records = [
        {
            "asset_type": "chroma_documentation",
            "record_id": item["record_id"],
            "content_fingerprint": item["content_fingerprint"],
            "table_keys": item["table_keys"],
        }
        for item in documentation_records
    ]

    sql = 'SELECT "id" FROM "public"."t"'
    sql_guard = SQLGuard(
        index_path,
        database_type="postgresql",
        default_schema="public",
    )
    sql_result = sql_guard.validate(sql, query="")
    assert sql_result.passed, sql_result.reason
    sql_tool_metadata = {
        "category": "sql_example",
        "tool_name": "run_sql",
        "args_json": json.dumps({"sql": sql}, ensure_ascii=False),
        "source_id": SOURCE_ID,
        "content_fingerprint": hashlib.sha256(
            f"q|{sql}".encode("utf-8")
        ).hexdigest(),
    }
    sql_tool_records = [
        {
            "asset_type": "sql_tool_memory",
            "record_id": "sql-1",
            "content_fingerprint": sql_tool_metadata["content_fingerprint"],
            "table_keys": sorted(
                [list(key) for key in sql_result.used_physical_tables]
            ),
            "column_keys": sorted(
                [list(key) for key in sql_result.used_physical_columns]
            ),
        }
    ]

    provenance = build_provenance(
        source_id=SOURCE_ID,
        runtime_revision=TARGET_REVISION,
        scope_fingerprint=SCOPE_FINGERPRINT,
        review_policy_fingerprint=REVIEW_FINGERPRINT,
        assets={
            "documentation": documentation_records,
            "chroma_ddl": chroma_ddl_records,
            "chroma_documentation": chroma_documentation_records,
            "sql_tool_memory": sql_tool_records,
        },
    )
    provenance_path = root / "asset_provenance.json"
    write_provenance(provenance_path, provenance)

    expected_records = [
        (ddl_doc_id, ddl_doc, {
            "source_id": SOURCE_ID,
            "memory_type": "ddl",
            "content_fingerprint": chroma_ddl_records[0][
                "content_fingerprint"
            ],
        })
        for ddl_doc_id, ddl_doc in [(chroma_ddl_records[0]["record_id"], ddl_doc)]
    ] + [
        (
            item["record_id"],
            item["document"],
            {
                "source_id": SOURCE_ID,
                "memory_type": "documentation",
                "content_fingerprint": item["content_fingerprint"],
            },
        )
        for item in documentation_records
    ] + [
        (
            "sql-1",
            "查看t最近5条记录",
            sql_tool_metadata,
        )
    ]
    return {
        "root": root,
        "provenance": provenance,
        "provenance_path": provenance_path,
        "business_documents_path": business_documents_path,
        "ddl_doc": ddl_doc,
        "documentation_records": documentation_records,
        "expected_records": expected_records,
        "sql_guard": sql_guard,
        "memory_path": root / "memory",
    }


def _validate(fx: dict, **overrides) -> dict:
    kwargs = {
        "source_id": SOURCE_ID,
        "database_type": DATABASE_TYPE,
        "database_name": DATABASE_NAME,
        "allowed_tables": _allowed(),
        "scope": _scope(),
        "scope_fingerprint": SCOPE_FINGERPRINT,
        "review_policy_fingerprint": REVIEW_FINGERPRINT,
        "target_runtime_revision": TARGET_REVISION,
        "business_documents_path": fx["business_documents_path"],
        "provenance_path": fx["provenance_path"],
        "memory_path": fx["memory_path"],
        "expected_records": fx["expected_records"],
        "sql_guard": fx["sql_guard"],
    }
    kwargs.update(overrides)
    return validate_runtime_candidate_assets(**kwargs)


def _populate_memory(fx: dict, records=None) -> None:
    records = fx["expected_records"] if records is None else records
    import backend.memory as memory_module

    memory = _FakeMemory(fx["memory_path"])
    collection = memory._get_collection()
    collection.add(
        ids=[item[0] for item in records],
        documents=[item[1] for item in records],
        metadatas=[dict(item[2]) for item in records],
    )


def _expect_error(fx: dict, keyword: str, **overrides) -> None:
    try:
        _validate(fx, **overrides)
    except DataSourceCatalogError as exc:
        assert keyword in str(exc), f"期望 {keyword!r}，实际 {exc}"
    else:
        raise AssertionError(f"应抛 DataSourceCatalogError（{keyword}）")


def test_normal_pass_all_categories() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-pass-") as directory:
        fx = _fixtures(Path(directory))
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            result = _validate(fx)
        assert result["documentation_records"] == 2
        assert result["chroma_ddl_records"] == 1
        assert result["sql_tool_memory_records"] == 1


def test_provenance_missing_file() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-pmissing-") as directory:
        fx = _fixtures(Path(directory))
        fx["provenance_path"].unlink()
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "不可读")


def test_provenance_corrupted_json() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-pcorrupt-") as directory:
        fx = _fixtures(Path(directory))
        fx["provenance_path"].write_text("not-json", encoding="utf-8")
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "不可读")


def test_provenance_source_id_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-psrc-") as directory:
        fx = _fixtures(Path(directory))
        fx["provenance"]["source_id"] = "other"
        write_provenance(fx["provenance_path"], fx["provenance"])
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "source_id")


def test_provenance_revision_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-prev-") as directory:
        fx = _fixtures(Path(directory))
        fx["provenance"]["runtime_revision"] = 99
        write_provenance(fx["provenance_path"], fx["provenance"])
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "runtime_revision")


def test_documentation_missing_allowed_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-dmiss-") as directory:
        fx = _fixtures(Path(directory))
        records = [
            item
            for item in fx["provenation"]
            if False
        ] if False else list(fx["provenance"]["assets"]["documentation"])
        records = [item for item in records if item["table_keys"] != [["public", "u"]]]
        fx["provenance"]["assets"]["documentation"] = records
        fx["provenance"]["assets"]["chroma_documentation"] = []
        write_provenance(fx["provenance_path"], fx["provenance"])
        # 同步业务文档文件与 memory
        documents = [item["document"] for item in records]
        fx["business_documents_path"].write_text(
            json.dumps(documents, ensure_ascii=False),
            encoding="utf-8",
        )
        expected = [
            item for item in fx["expected_records"]
            if item[2].get("memory_type") != "documentation"
        ] + [
            (item["record_id"], item["document"], {
                "source_id": SOURCE_ID,
                "memory_type": "documentation",
                "content_fingerprint": item["content_fingerprint"],
            })
            for item in records
        ]
        _populate_memory(fx, expected)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "table union")


def test_documentation_duplicate_table_across_docs() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-ddup-") as directory:
        fx = _fixtures(Path(directory))
        records = list(fx["provenance"]["assets"]["documentation"])
        records[0]["table_keys"] = [["public", "t"], ["public", "u"]]
        fx["provenance"]["assets"]["documentation"] = records
        fx["provenance"]["assets"]["chroma_documentation"] = []
        write_provenance(fx["provenance_path"], fx["provenance"])
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "重复")


def test_documentation_non_allowed_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-dna-") as directory:
        fx = _fixtures(Path(directory))
        records = list(fx["provenance"]["assets"]["documentation"])
        records[0]["table_keys"] = [["public", "forbidden"]]
        fx["provenance"]["assets"]["documentation"] = records
        fx["provenance"]["assets"]["chroma_documentation"] = []
        write_provenance(fx["provenance_path"], fx["provenance"])
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "非 allowed")


def test_chroma_count_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-ccount-") as directory:
        fx = _fixtures(Path(directory))
        _populate_memory(fx, fx["expected_records"][:-1])
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "count")


def test_chroma_ddl_document_changed() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-cddl-") as directory:
        fx = _fixtures(Path(directory))
        records = list(fx["expected_records"])
        records[0] = (records[0][0], "DDL\n" + _ddl_for("t", ["id"]), dict(records[0][2]))
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "不一致")


def test_chroma_ddl_prefix_error() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-cprefix-") as directory:
        fx = _fixtures(Path(directory))
        records = list(fx["expected_records"])
        records[0] = (records[0][0], fx["ddl_doc"].replace("DDL\n", ""), dict(records[0][2]))
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "前缀")


def test_chroma_metadata_type_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-ctype-") as directory:
        fx = _fixtures(Path(directory))
        records = list(fx["expected_records"])
        meta = dict(records[0][2])
        meta["memory_type"] = "documentation"
        records[0] = (records[0][0], records[0][1], meta)
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "memory_type")


def test_sql_tool_non_allowed_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-sqltable-") as directory:
        fx = _fixtures(Path(directory))
        sql = 'SELECT "id" FROM "public"."forbidden"'
        metadata = dict(fx["expected_records"][-1][2])
        metadata["args_json"] = json.dumps({"sql": sql}, ensure_ascii=False)
        records = list(fx["expected_records"])
        records[-1] = (records[-1][0], records[-1][1], metadata)
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "SQLGuard")


def test_sql_tool_wildcard_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-sqlstar-") as directory:
        fx = _fixtures(Path(directory))
        sql = 'SELECT * FROM "public"."t"'
        metadata = dict(fx["expected_records"][-1][2])
        metadata["args_json"] = json.dumps({"sql": sql}, ensure_ascii=False)
        records = list(fx["expected_records"])
        records[-1] = (records[-1][0], records[-1][1], metadata)
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "通配符")


def test_sql_tool_args_json_invalid() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-sqlargs-") as directory:
        fx = _fixtures(Path(directory))
        metadata = dict(fx["expected_records"][-1][2])
        metadata["args_json"] = "not-json"
        records = list(fx["expected_records"])
        records[-1] = (records[-1][0], records[-1][1], metadata)
        _populate_memory(fx, records)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "args_json")


def test_sql_tool_provenance_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="e2b-sqlprov-") as directory:
        fx = _fixtures(Path(directory))
        sql = 'SELECT "id" FROM "public"."t"'
        sql_result = fx["sql_guard"].validate(sql, query="")
        sql_tool_records = [
            {
                "asset_type": "sql_tool_memory",
                "record_id": "sql-1",
                "content_fingerprint": fx["expected_records"][-1][2][
                    "content_fingerprint"
                ],
                "table_keys": [["public", "u"]],
                "column_keys": sorted(
                    [list(key) for key in sql_result.used_physical_columns]
                ),
            }
        ]
        fx["provenance"]["assets"]["sql_tool_memory"] = sql_tool_records
        write_provenance(fx["provenance_path"], fx["provenance"])
        _populate_memory(fx)
        with patch.object(
            __import__("backend.memory", fromlist=["create_memory"]),
            "create_memory",
            side_effect=_FakeMemory,
        ):
            _expect_error(fx, "table_keys")


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(
        f"\n{len([1 for n in globals() if n.startswith('test_')]) - failed}/"
        f"{len([1 for n in globals() if n.startswith('test_')])} passed"
    )
    raise SystemExit(1 if failed else 0)

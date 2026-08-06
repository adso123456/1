"""阶段 E-1 F2：内置数据源认领发布与审核 allowed_tables 集成测试。

覆盖三类远程差异：
  - 远程结构未变化 -> 正常认领发布；
  - 远程新增 pending 表 -> 新表不进入 scope，其余 active 表正常发布；
  - 远程缺少 active 表 -> 发布失败，revision 与正式资产不变。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.builtin_data_source_claim import BuiltinDataSourceClaimService
from backend.data_source_catalog import (
    DataSourceCatalog,
    DataSourceCatalogError,
)
from backend.data_source_claim_identity import load_builtin_asset_lineage
from backend.data_source_connectors import DataSourceAssetPreparer


ALLOWED_TABLES = ("water_data", "station_info")


def _bootstrap(root: Path, host: str, port: int) -> list[dict]:
    metadata_path = root / "pg-metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "schema": "public",
                    "table": "water_data",
                    "column": "id",
                    "type": "bigint",
                    "primary_key": True,
                    "ordinal_position": 1,
                    "indexes": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory_path = root / "pg-memory"
    memory_path.mkdir()
    return [
        {
            "source_id": "postgresql-main",
            "display_name": "排污口治理数据",
            "description": "排污口、整治、溯源",
            "database_type": "postgresql",
            "host": host,
            "port": port,
            "database_name": "gt_monitor",
            "schema_name": "public",
            "connect_timeout": 10,
            "credential_reference": {
                "username": "PG_USER",
                "password": "PG_PASSWORD",
            },
            "metadata_path": metadata_path,
            "memory_path": memory_path,
            "selected_tables_count": 1,
            "selected_columns_count": 1,
            "routing_summary": "",
            "capabilities": [],
        }
    ]


def _candidate(tables) -> list[dict]:
    items = []
    for table in tables:
        for position, (column, column_type, primary) in enumerate(
            (
                ("id", "bigint", True),
                ("value", "numeric", False),
            ),
            start=1,
        ):
            indexes = []
            if primary:
                indexes = [
                    {
                        "name": f"pk_{table}",
                        "unique": True,
                        "primary": True,
                        "method": "btree",
                        "columns": [
                            {
                                "name": column,
                                "position": 1,
                                "direction": "ASC",
                            }
                        ],
                    }
                ]
            items.append(
                {
                    "schema": "public",
                    "table": table,
                    "object_type": "table",
                    "table_comment": f"{table} 注释",
                    "column": column,
                    "type": column_type,
                    "comment": f"{column} 注释",
                    "nullable": not primary,
                    "primary_key": primary,
                    "ordinal_position": position,
                    "indexes": indexes,
                    "logical_relations": [],
                    "domain": "监测",
                    "grain": "id",
                    "time_column": "",
                    "valid_row_rules": [],
                    "confidence": "deterministic",
                }
            )
    return items


class _FakeCollection:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.total = 0
        self.records = []

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
        self.records = list(zip(ids, documents, metadatas))
        (self.root / "identity.json").write_text(
            json.dumps(
                {"ids": ids, "documents": documents, "metadatas": metadatas},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def count(self) -> int:
        return self.total

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
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        key = str(Path(path).resolve())
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


def _setup(directory: Path, candidate_tables):
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
        environ={},
    )
    catalog.initialize(_bootstrap(directory, "192.168.250.73", 5433))
    catalog.initialize_builtin_claims(load_builtin_asset_lineage())
    source_id = "postgresql-main"
    summary = catalog.builtin_claim_summary(source_id)
    assert summary and summary["status"] == "claim_required"
    # 审核策略：ALLOWED_TABLES 全部 active+present。
    for table in ALLOWED_TABLES:
        catalog.upsert_table_review(
            source_id,
            "public",
            table,
            effective_decision="active",
            availability_status="present",
            decision_source="migration",
            decision_reason="test",
        )
    # 画像：candidate 中实际存在且画像通过的表。
    profiles = [
        {
            "schema": "public",
            "table": table,
            "error": "",
        }
        for table in candidate_tables
    ]
    catalog.replace_table_profiles(source_id, profiles)
    catalog.save_builtin_claim_preview(
        source_id,
        remote_schema_fingerprint="fingerprint-x",
        diff={},
        candidate_metadata=_candidate(candidate_tables),
    )
    return catalog, source_id


def _service(catalog: DataSourceCatalog) -> BuiltinDataSourceClaimService:
    return BuiltinDataSourceClaimService(
        catalog,
        None,
        None,
        None,
        DataSourceAssetPreparer(catalog),
        None,
    )


def _publish(catalog: DataSourceCatalog, source_id: str):
    import backend.memory as memory_module

    service = _service(catalog)
    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_FakeMemory,
    ):
        return service.publish(source_id)


def _scope_tables(catalog: DataSourceCatalog, source_id: str) -> set:
    return {
        (str(item.get("schema") or ""), str(item["table"]))
        for item in catalog.require(source_id).selected_scope
    }


def test_claim_publish_remote_unchanged() -> None:
    with tempfile.TemporaryDirectory(prefix="e2-unchanged-") as directory:
        root = Path(directory)
        catalog, source_id = _setup(root, list(ALLOWED_TABLES))
        try:
            base = catalog.require(source_id).runtime_revision
            result = _publish(catalog, source_id)
            assert result["runtime_revision"] == base + 1
            assert _scope_tables(catalog, source_id) == {
                ("public", "water_data"),
                ("public", "station_info"),
            }
        finally:
            shutil.rmtree(
                catalog.require(source_id).metadata_path.parent,
                ignore_errors=True,
            )


def test_claim_publish_remote_adds_pending_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2-pending-") as directory:
        root = Path(directory)
        catalog, source_id = _setup(
            root,
            [*ALLOWED_TABLES, "new_pending_table"],
        )
        try:
            catalog.upsert_table_review(
                source_id,
                "public",
                "new_pending_table",
                effective_decision="pending",
                availability_status="present",
                decision_source="migration",
                decision_reason="test",
            )
            base = catalog.require(source_id).runtime_revision
            result = _publish(catalog, source_id)
            assert result["runtime_revision"] == base + 1
            # 新增 pending 表不进入 scope。
            assert _scope_tables(catalog, source_id) == {
                ("public", "water_data"),
                ("public", "station_info"),
            }
        finally:
            shutil.rmtree(
                catalog.require(source_id).metadata_path.parent,
                ignore_errors=True,
            )


def test_claim_publish_remote_missing_active_table() -> None:
    with tempfile.TemporaryDirectory(prefix="e2-missing-") as directory:
        root = Path(directory)
        catalog, source_id = _setup(root, ["water_data"])
        base = catalog.require(source_id).runtime_revision
        try:
            try:
                _publish(catalog, source_id)
            except DataSourceCatalogError as exc:
                assert "远程本尊缺少审核允许的表" in str(exc)
            else:
                raise AssertionError("远程缺少 active 表时应阻止发布")
            record = catalog.require(source_id)
            assert record.runtime_revision == base
            assert not (record.metadata_path.parent / "asset_manifest.json").exists()
        finally:
            shutil.rmtree(
                catalog.require(source_id).metadata_path.parent,
                ignore_errors=True,
            )


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

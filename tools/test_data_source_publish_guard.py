"""阶段 E-1：发布硬校验（前置范围门 + 审核策略指纹并发保护）回归测试。

覆盖：
  - 正常 active+present 范围通过，manifest/asset identity 写入指纹；
  - reviews 为空时 prepare 失败关闭；
  - selected_scope 含 pending 表 / 缺失 active+present 表 /
    active+missing 表导致范围不一致时阻止；
  - 候选构建期间 review policy 改变 -> 回滚；
  - catalog.publish 前 policy 改变 -> 事务内原子拒绝；
  - 失败后旧正式资产与 runtime_revision 保持不变。

数据源资产根目录使用规范路径（agent_data/data_sources/<source_id>），
每个用例结束后清理该源目录，不污染仓库。
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

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_connectors import DataSourceAssetPreparer


TABLES = ("monitor_data", "station_dict", "water_data_old")


def _metadata_items() -> list[dict]:
    items = []
    for table in TABLES:
        columns = (
            ("id", "bigint", True),
            ("name", "character varying", False),
            ("value", "numeric", False),
        )
        for position, (column, column_type, primary) in enumerate(
            columns,
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


METADATA = _metadata_items()


class _FakeCollection:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.total = 0

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
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

    def get(self, *, where=None, include=None) -> dict:
        return {"ids": [], "documents": [], "metadatas": []}


class _FakeMemory:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None
        self._collection = _FakeCollection(path)

    def _get_collection(self):
        return self._collection


def _setup(directory: Path):
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
    )
    catalog.initialize()
    source = catalog.create(
        display_name="发布门测试",
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
    return catalog, source.source_id, record.metadata_path.parent


def _save_scope(catalog: DataSourceCatalog, source_id: str, tables) -> None:
    scope = [item for item in METADATA if item["table"] in tables]
    catalog.save_scope(source_id, scope)


def _seed_reviews(
    catalog: DataSourceCatalog,
    source_id: str,
    states: dict[str, tuple[str, str]],
) -> None:
    for table, (effective, availability) in states.items():
        catalog.upsert_table_review(
            source_id,
            "public",
            table,
            effective_decision=effective,
            availability_status=availability,
            decision_source="migration",
            decision_reason="test",
        )


def _seed_all_active(catalog: DataSourceCatalog, source_id: str) -> None:
    _seed_reviews(
        catalog,
        source_id,
        {table: ("active", "present") for table in TABLES},
    )


def _path_hash(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        digest = hashlib.sha256()
        for item in sorted(
            value for value in path.rglob("*") if value.is_file()
        ):
            digest.update(str(item.relative_to(path)).encode("utf-8"))
            digest.update(item.read_bytes())
        return digest.hexdigest()
    return ""


def _formal_hashes(catalog: DataSourceCatalog, source_id: str) -> tuple:
    record = catalog.require(source_id)
    root = record.metadata_path.parent
    return (
        _path_hash(record.metadata_path),
        _path_hash(root / "ddl_memories.json"),
        _path_hash(root / "business_documents.json"),
        _path_hash(record.memory_path),
    )


def _prepare_with_memory(
    preparer: DataSourceAssetPreparer,
    source_id: str,
):
    import backend.memory as memory_module

    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_FakeMemory,
    ):
        return preparer.prepare(source_id)


def test_normal_active_present_passes() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-normal-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            _seed_all_active(catalog, source_id)
            result = _prepare_with_memory(
                DataSourceAssetPreparer(catalog),
                source_id,
            )
            assert result["runtime_revision"] == 1
            record = catalog.require(source_id)
            assert record.status == "ready"
            manifest = json.loads(
                (
                    record.metadata_path.parent / "asset_manifest.json"
                ).read_text(encoding="utf-8")
            )
            expected = catalog.review_policy(source_id)["fingerprint"]
            assert manifest["review_policy_fingerprint"] == expected
            identity = json.loads(
                (record.memory_path / ".asset_identity.json").read_text(
                    encoding="utf-8"
                )
            )
            assert identity["review_policy_fingerprint"] == expected
            assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_reviews_empty_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-empty-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            try:
                _prepare_with_memory(
                    DataSourceAssetPreparer(catalog),
                    source_id,
                )
            except Exception as exc:
                assert "尚未完成表准入审核" in str(exc)
            else:
                raise AssertionError("reviews 为空时应失败关闭")
            assert catalog.require(source_id).runtime_revision == 0
            assert not catalog.require(source_id).metadata_path.exists()
            assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_scope_contains_pending_blocks() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-pending-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            _seed_reviews(
                catalog,
                source_id,
                {
                    "monitor_data": ("active", "present"),
                    "station_dict": ("pending", "present"),
                    "water_data_old": ("active", "present"),
                },
            )
            try:
                _prepare_with_memory(
                    DataSourceAssetPreparer(catalog),
                    source_id,
                )
            except Exception as exc:
                assert "非 allowed" in str(exc)
            else:
                raise AssertionError("scope 含 pending 表时应阻止")
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_active_present_missing_from_scope_blocks() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-missing-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, ("monitor_data", "station_dict"))
            _seed_all_active(catalog, source_id)
            try:
                _prepare_with_memory(
                    DataSourceAssetPreparer(catalog),
                    source_id,
                )
            except Exception as exc:
                assert "未进入 selected_scope" in str(exc)
            else:
                raise AssertionError("active+present 未进入 scope 时应阻止")
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_active_missing_in_scope_blocks() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-missing2-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            _seed_reviews(
                catalog,
                source_id,
                {
                    "monitor_data": ("active", "present"),
                    "station_dict": ("active", "missing"),
                    "water_data_old": ("active", "present"),
                },
            )
            try:
                _prepare_with_memory(
                    DataSourceAssetPreparer(catalog),
                    source_id,
                )
            except Exception as exc:
                assert "非 allowed" in str(exc)
            else:
                raise AssertionError("active+missing 表不应进入 allowed")
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def _assert_policy_change_rolls_back(directory: Path, inject_point: str) -> None:
    catalog, source_id, asset_root = _setup(directory)
    try:
        _save_scope(catalog, source_id, TABLES)
        _seed_all_active(catalog, source_id)
        first = _prepare_with_memory(
            DataSourceAssetPreparer(catalog),
            source_id,
        )
        assert first["runtime_revision"] == 1
        hashes_before = _formal_hashes(catalog, source_id)

        def inject(point: str) -> None:
            if point == inject_point:
                catalog.upsert_table_review(
                    source_id,
                    "public",
                    "station_dict",
                    effective_decision="pending",
                )

        preparer = DataSourceAssetPreparer(
            catalog,
            fault_injector=inject,
        )
        try:
            _prepare_with_memory(preparer, source_id)
        except Exception as exc:
            assert "审核策略已变化" in str(exc)
        else:
            raise AssertionError(f"{inject_point} 处应检测到策略变化")
        record = catalog.require(source_id)
        assert record.runtime_revision == 1
        assert _formal_hashes(catalog, source_id) == hashes_before
        assert not catalog.active_asset_batches(source_id)
    finally:
        shutil.rmtree(asset_root, ignore_errors=True)


def test_policy_change_during_candidate_build_rolls_back() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-build-") as directory:
        _assert_policy_change_rolls_back(
            Path(directory),
            "after_candidate_documentation",
        )


def test_policy_change_before_publish_atomic_reject() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-publish-") as directory:
        _assert_policy_change_rolls_back(
            Path(directory),
            "before_catalog_publish",
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

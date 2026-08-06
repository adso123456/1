"""阶段 E-1：发布硬校验回归测试。

覆盖：
  - 前置范围门：reviews 为空失败关闭；selected_scope 必须精确等于
    allowed_tables（pending/缺失/active+missing 均阻止）；
  - 正常发布：manifest / asset identity 写入 review_policy_fingerprint；
  - F1 复用旧资产门：disable 后修改 effective/availability，enable 必须失败，
    恢复策略后 enable 成功，revision 与正式资产哈希全程不变；
  - F3 发布租约：begin_asset_batch 注册租约后，审核写入口（upsert /
    apply_review_results / 迁移）禁止修改 effective/availability，
    批次结束释放；
  - F4 指纹规范 JSON：|、换行、Unicode 表名无序列化歧义。
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

from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    DataSourceCatalogError,
    DataSourceConflict,
)
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


def _published_source(directory: Path):
    catalog, source_id, asset_root = _setup(directory)
    _save_scope(catalog, source_id, TABLES)
    _seed_all_active(catalog, source_id)
    result = _prepare_with_memory(
        DataSourceAssetPreparer(catalog),
        source_id,
    )
    assert result["runtime_revision"] == 1
    return catalog, source_id, asset_root


def test_enable_gate_blocks_stale_effective() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-f1a-") as directory:
        catalog, source_id, asset_root = _published_source(Path(directory))
        try:
            catalog.set_enabled(source_id, False)
            hashes_before = _formal_hashes(catalog, source_id)
            catalog.upsert_table_review(
                source_id,
                "public",
                "station_dict",
                effective_decision="pending",
            )
            try:
                catalog.set_enabled(source_id, True)
            except DataSourceCatalogError as exc:
                assert "不一致" in str(exc)
            else:
                raise AssertionError("策略变化后 enable 应被拒绝")
            record = catalog.require(source_id)
            assert record.status == "disabled"
            assert record.enabled_for_chat is False
            assert record.runtime_revision == 1
            assert _formal_hashes(catalog, source_id) == hashes_before
            # 恢复策略 -> enable 成功。
            catalog.upsert_table_review(
                source_id,
                "public",
                "station_dict",
                effective_decision="active",
                availability_status="present",
            )
            catalog.set_enabled(source_id, True)
            record = catalog.require(source_id)
            assert record.status == "ready"
            assert record.enabled_for_chat is True
            assert record.runtime_revision == 1
            assert _formal_hashes(catalog, source_id) == hashes_before
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_enable_gate_blocks_stale_availability() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-f1b-") as directory:
        catalog, source_id, asset_root = _published_source(Path(directory))
        try:
            catalog.set_enabled(source_id, False)
            hashes_before = _formal_hashes(catalog, source_id)
            catalog.upsert_table_review(
                source_id,
                "public",
                "station_dict",
                availability_status="missing",
            )
            try:
                catalog.set_enabled(source_id, True)
            except DataSourceCatalogError as exc:
                assert "不一致" in str(exc)
            else:
                raise AssertionError("availability 变化后 enable 应被拒绝")
            record = catalog.require(source_id)
            assert record.status == "disabled"
            assert record.runtime_revision == 1
            assert _formal_hashes(catalog, source_id) == hashes_before
            catalog.upsert_table_review(
                source_id,
                "public",
                "station_dict",
                availability_status="present",
            )
            catalog.set_enabled(source_id, True)
            assert catalog.require(source_id).status == "ready"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_policy_lease_blocks_review_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-f3-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            _seed_all_active(catalog, source_id)
            policy = catalog.review_policy(source_id)
            record = catalog.require(source_id)
            root = record.metadata_path.parent
            batch_id = "lease-test-1"
            catalog.begin_asset_batch(
                source_id,
                batch_id=batch_id,
                candidate_root=root / "candidate-lease",
                candidate_memory=root / "memory-lease",
                published_memory_path=root / "memory-lease-rev",
                snapshot={},
                asset_plan=[],
                expected_review_policy_fingerprint=policy["fingerprint"],
            )
            try:
                for fields in (
                    {"effective_decision": "pending"},
                    {"availability_status": "missing"},
                ):
                    try:
                        catalog.upsert_table_review(
                            source_id,
                            "public",
                            "station_dict",
                            **fields,
                        )
                    except DataSourceConflict:
                        pass
                    else:
                        raise AssertionError(
                            f"租约应阻止 {list(fields)} 修改"
                        )
                # 非策略字段允许。
                catalog.upsert_table_review(
                    source_id,
                    "public",
                    "station_dict",
                    proposed_decision="pending",
                )
                try:
                    catalog.apply_review_results(
                        source_id,
                        "lease-run",
                        review_updates=[],
                        missing_keys=[],
                        history_snapshots=[],
                        profiled_tables=0,
                    )
                except DataSourceConflict:
                    pass
                else:
                    raise AssertionError("租约应阻止 apply_review_results")
                try:
                    catalog.migrate_table_reviews_from_existing(source_id)
                except DataSourceConflict:
                    pass
                else:
                    raise AssertionError("租约应阻止首次迁移")
            finally:
                catalog.finish_asset_batch(source_id, batch_id)
            # 释放后允许修改策略字段。
            catalog.upsert_table_review(
                source_id,
                "public",
                "station_dict",
                effective_decision="pending",
            )
            assert (
                catalog.get_table_review(
                    source_id,
                    "public",
                    "station_dict",
                )["effective_decision"]
                == "pending"
            )
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_begin_asset_batch_rejects_stale_fingerprint() -> None:
    with tempfile.TemporaryDirectory(prefix="e1-f3b-") as directory:
        catalog, source_id, asset_root = _setup(Path(directory))
        try:
            _save_scope(catalog, source_id, TABLES)
            _seed_all_active(catalog, source_id)
            record = catalog.require(source_id)
            root = record.metadata_path.parent
            try:
                catalog.begin_asset_batch(
                    source_id,
                    batch_id="stale-lease",
                    candidate_root=root / "candidate-stale",
                    candidate_memory=root / "memory-stale",
                    published_memory_path=root / "memory-stale-rev",
                    snapshot={},
                    asset_plan=[],
                    expected_review_policy_fingerprint="stale-fingerprint",
                )
            except DataSourceConflict as exc:
                assert "审核策略已变化" in str(exc)
            else:
                raise AssertionError("过期指纹应被 begin_asset_batch 拒绝")
            assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_fingerprint_serialization_no_ambiguity() -> None:
    fingerprint = DataSourceCatalog._review_policy_fingerprint
    pipe_a = [
        {
            "schema_name": "a|b",
            "table_name": "c",
            "effective_decision": "active",
            "availability_status": "present",
        }
    ]
    pipe_b = [
        {
            "schema_name": "a",
            "table_name": "b|c",
            "effective_decision": "active",
            "availability_status": "present",
        }
    ]
    assert fingerprint(pipe_a) != fingerprint(pipe_b)
    unicode_rows = [
        {
            "schema_name": "水 域\n表",
            "table_name": "监测|表",
            "effective_decision": "active",
            "availability_status": "present",
        }
    ]
    assert fingerprint(unicode_rows) == fingerprint(unicode_rows)
    assert fingerprint(pipe_a) != fingerprint(unicode_rows)
    # 行顺序不同 -> 指纹不同（输入约定已排序，防御性验证）。
    ordered = [
        {
            "schema_name": "s1",
            "table_name": "t1",
            "effective_decision": "active",
            "availability_status": "present",
        },
        {
            "schema_name": "s1",
            "table_name": "t2",
            "effective_decision": "pending",
            "availability_status": "present",
        },
    ]
    assert fingerprint(ordered) != fingerprint(list(reversed(ordered)))




def test_asset_validation_failure_keeps_revision_and_assets() -> None:
    import backend.data_source_asset_validator as validator_module

    with tempfile.TemporaryDirectory(prefix="e2a-prep-") as directory:
        catalog, source_id, asset_root = _published_source(Path(directory))
        try:
            hashes_before = _formal_hashes(catalog, source_id)
            original = validator_module.validate_candidate_assets

            def failing(**kwargs):
                raise DataSourceCatalogError("E-2A 结构化校验失败（注入）")

            validator_module.validate_candidate_assets = failing
            try:
                try:
                    _prepare_with_memory(
                        DataSourceAssetPreparer(catalog),
                        source_id,
                    )
                except DataSourceCatalogError as exc:
                    assert "E-2A 结构化校验失败" in str(exc)
                else:
                    raise AssertionError("E-2A 校验失败应阻断发布")
            finally:
                validator_module.validate_candidate_assets = original
            record = catalog.require(source_id)
            assert record.runtime_revision == 1
            assert _formal_hashes(catalog, source_id) == hashes_before
            assert not catalog.active_asset_batches(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)

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

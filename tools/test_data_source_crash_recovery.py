"""B5 发布状态机的进程崩溃、重启恢复与二次崩溃回归。"""

from __future__ import annotations

import json
import os
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
from backend.data_source_connectors import (
    DataSourceAssetCleaner,
    DataSourceAssetPreparer,
    SimulatedProcessCrash,
)
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from tools.test_dynamic_data_source_integration_gaps import (
    _ClosableResource,
    _FakeMemory,
)


METADATA = [
    {
        "schema": "public",
        "table": "crash_safe_table",
        "object_type": "table",
        "table_comment": "崩溃恢复测试表",
        "column": "id",
        "type": "bigint",
        "comment": "主键",
        "nullable": False,
        "primary_key": True,
        "ordinal_position": 1,
        "indexes": [
            {
                "name": "crash_safe_table_pkey",
                "unique": True,
                "primary": True,
                "method": "btree",
                "columns": [
                    {"name": "id", "position": 1, "direction": "ASC"}
                ],
            }
        ],
    }
]


def _new_catalog(root: Path, key: str) -> tuple[DataSourceCatalog, str]:
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(key),
        environ={},
    )
    catalog.initialize()
    record = catalog.create(
        display_name="崩溃恢复源",
        description="revision-1",
        database_type="postgresql",
        host="127.0.0.1",
        port=5433,
        database_name="test",
        schema_name="public",
        username="test",
        password="test",
    )
    catalog.mark_connection_test(record.source_id, success=True)
    catalog.save_discovery(record.source_id, METADATA)
    catalog.save_scope(record.source_id, METADATA)
    # E-1：prepare 前置范围门要求 reviews 与 selected_scope 精确一致。
    for item in METADATA:
        catalog.upsert_table_review(
            record.source_id,
            str(item.get("schema") or ""),
            str(item["table"]),
            effective_decision="active",
            availability_status="present",
            decision_source="test",
            decision_reason="test",
        )
    return catalog, record.source_id


def _restart_catalog(path: Path, key: str) -> DataSourceCatalog:
    catalog = DataSourceCatalog(
        path,
        cipher=CredentialCipher(key),
        environ={},
    )
    catalog.initialize()
    return catalog


def _manager(catalog: DataSourceCatalog) -> DataSourceRuntimeManager:
    def factory(config):
        resources = [_ClosableResource() for _ in range(5)]
        return DataSourceRuntime(
            config=config,
            runner=resources[0],
            memory=resources[1],
            metadata_retriever=resources[2],
            sql_guard=resources[3],
            agent=resources[4],
        )

    return DataSourceRuntimeManager(
        DataSourceRegistry.from_catalog(catalog),
        {"postgresql": factory, "mysql": factory},
    )


def _assert_consistent(catalog: DataSourceCatalog, source_id: str) -> None:
    record = catalog.require(source_id)
    root = record.metadata_path.parent
    manifest_path = root / "asset_manifest.json"
    assert not catalog.active_asset_batches(source_id)
    assert record.status == "ready" and record.enabled_for_chat
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_id"] == source_id
    assert manifest["runtime_revision"] == record.runtime_revision
    assert (
        manifest["scope_fingerprint"]
        == __import__(
            "backend.data_source_catalog",
            fromlist=["selected_scope_fingerprint"],
        ).selected_scope_fingerprint(record.selected_scope)
    )
    cleaner = DataSourceAssetCleaner(catalog)
    assert manifest["metadata_hash"] == cleaner._path_hash(record.metadata_path)
    assert manifest["memory_identity_hash"] == cleaner._path_hash(
        record.memory_path
    )
    assert manifest["ddl_hash"] == cleaner._path_hash(
        root / "ddl_memories.json"
    )
    assert manifest["business_documents_hash"] == cleaner._path_hash(
        root / "business_documents.json"
    )
    # E-2B：provenance 必须与 manifest / asset identity 哈希一致。
    provenance = json.loads(
        (root / "asset_provenance.json").read_text(encoding="utf-8")
    )
    from backend.data_source_asset_provenance import provenance_fingerprint

    provenance_hash = provenance_fingerprint(provenance)
    assert manifest["provenance_hash"] == provenance_hash
    identity = json.loads(
        (record.memory_path / ".asset_identity.json").read_text(
            encoding="utf-8"
        )
    )
    assert identity["provenance_hash"] == provenance_hash
    assert provenance["source_id"] == source_id
    assert int(provenance["runtime_revision"]) == record.runtime_revision
    assert not list(root.glob(".*.backup-*"))
    assert not list(root.glob("candidate-*"))
    assert not list(root.glob(".*.candidate-*"))


def _crash_at(point: str):
    def inject(actual: str) -> None:
        if actual == point:
            raise SimulatedProcessCrash(point)

    return inject


def _run_case(root: Path, point: str) -> None:
    key = Fernet.generate_key().decode("ascii")
    catalog, source_id = _new_catalog(root, key)
    import backend.memory as memory_module

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        DataSourceAssetPreparer(catalog).prepare(source_id)
    catalog.update(source_id, description=f"fault-{point}")
    manager = _manager(catalog)
    preparer = DataSourceAssetPreparer(
        catalog,
        manager,
        fault_injector=_crash_at(point),
    )
    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        try:
            preparer.prepare(source_id)
        except SimulatedProcessCrash:
            pass
        else:
            raise AssertionError(f"故障点未触发：{point}")
    restarted = _restart_catalog(catalog.db_path, key)
    restarted_manager = _manager(restarted)
    DataSourceAssetCleaner(
        restarted,
        restarted_manager,
    ).recover_incomplete_batches(source_id, grace_seconds=0)
    _assert_consistent(restarted, source_id)
    runtime = restarted_manager.require(source_id)
    assert runtime.config.memory_path.resolve() == restarted.require(
        source_id
    ).memory_path.resolve()
    shutil.rmtree(restarted.require(source_id).metadata_path.parent)
    print(f"[PASS] {point}")


def _run_second_crash(root: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    catalog, source_id = _new_catalog(root, key)
    import backend.memory as memory_module

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        DataSourceAssetPreparer(catalog).prepare(source_id)
    catalog.update(source_id, description="second-crash")
    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        try:
            DataSourceAssetPreparer(
                catalog,
                fault_injector=_crash_at("after_install_ddl"),
            ).prepare(source_id)
        except SimulatedProcessCrash:
            pass
    first_restart = _restart_catalog(catalog.db_path, key)
    try:
        DataSourceAssetCleaner(
            first_restart,
            _manager(first_restart),
            fault_injector=_crash_at("during_recovery_after_asset_1"),
        ).recover_incomplete_batches(source_id, grace_seconds=0)
    except SimulatedProcessCrash:
        pass
    else:
        raise AssertionError("恢复过程二次崩溃未触发")
    second_restart = _restart_catalog(catalog.db_path, key)
    DataSourceAssetCleaner(
        second_restart,
        _manager(second_restart),
    ).recover_incomplete_batches(source_id, grace_seconds=0)
    _assert_consistent(second_restart, source_id)
    shutil.rmtree(second_restart.require(source_id).metadata_path.parent)
    print("[PASS] 恢复中二次崩溃后继续恢复且幂等")


def _run_disable_race(root: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    catalog, source_id = _new_catalog(root, key)
    import backend.memory as memory_module

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        DataSourceAssetPreparer(catalog).prepare(source_id)
    base = catalog.require(source_id)

    def disable(point: str) -> None:
        if point == "after_candidate_memory":
            catalog.set_enabled(source_id, False)

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        try:
            DataSourceAssetPreparer(
                catalog,
                fault_injector=disable,
            ).prepare(source_id)
        except Exception:
            pass
        else:
            raise AssertionError("prepare 与停用竞态未拒绝旧批次")
    current = catalog.require(source_id)
    assert current.status == "disabled" and not current.enabled_for_chat
    assert current.runtime_revision == base.runtime_revision
    assert current.memory_path == base.memory_path
    assert not catalog.active_asset_batches(source_id)
    shutil.rmtree(current.metadata_path.parent)
    print("[PASS] prepare 与停用并发时保留停用结果并回滚候选")


def _run_candidate_root_traversal(root: Path) -> None:
    key = Fernet.generate_key().decode("ascii")
    catalog, source_id = _new_catalog(root, key)
    import backend.memory as memory_module

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        DataSourceAssetPreparer(catalog).prepare(source_id)
        try:
            DataSourceAssetPreparer(
                catalog,
                fault_injector=_crash_at("after_batch_registered"),
            ).prepare(source_id)
        except SimulatedProcessCrash:
            pass
    outside = root / "outside-evidence"
    outside.mkdir(parents=True)
    marker = outside / "must-remain.txt"
    marker.write_text("protected\n", encoding="utf-8")
    with catalog._lock, catalog._connection(write=True) as connection:
        connection.execute(
            """
            UPDATE active_asset_batches
            SET candidate_root = ?
            WHERE source_id = ?
            """,
            (str(outside.resolve()), source_id),
        )
    restarted = _restart_catalog(catalog.db_path, key)
    DataSourceAssetCleaner(
        restarted,
        _manager(restarted),
    ).recover_incomplete_batches(source_id, grace_seconds=0)
    assert marker.read_text(encoding="utf-8") == "protected\n"
    batch = restarted.active_asset_batches(source_id)[0]
    assert batch["phase"] == "rollback_failed"
    assert restarted.require(source_id).status == "error"
    shutil.rmtree(restarted.require(source_id).metadata_path.parent)
    print("[PASS] 候选根目录被篡改为受管目录外时保留证据并拒绝清理")


def main() -> int:
    points = (
        "after_batch_registered",
        "after_candidate_metadata",
        "after_candidate_ddl",
        "after_candidate_documentation",
        "after_candidate_memory",
        "after_backup_metadata",
        "after_backup_ddl",
        "after_backup_documentation",
        "after_backup_provenance",
        "after_install_metadata",
        "after_install_memory",
        "after_install_ddl",
        "after_install_documentation",
        "after_install_provenance",
        "after_install_manifest",
        "before_catalog_publish",
        "after_catalog_publish",
        "after_runtime_build",
        "after_runtime_swap",
        "before_backup_cleanup",
        "before_batch_finish",
    )
    with tempfile.TemporaryDirectory(prefix="b5-crash-recovery-") as name:
        root = Path(name)
        selected = os.getenv("B5_CRASH_POINT", "").strip()
        selected_points = (selected,) if selected else points
        for index, point in enumerate(selected_points):
            if point not in points:
                raise AssertionError(f"未知故障点：{point}")
            _run_case(root / f"case-{index}", point)
        if not selected or os.getenv("B5_SECOND_CRASH") == "1":
            _run_second_crash(root / "second-crash")
            _run_disable_race(root / "disable-race")
            _run_candidate_root_traversal(root / "path-traversal")
    print("data source crash recovery: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""B5 发布状态机的进程崩溃、重启恢复与二次崩溃回归。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
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


def _fake_record(
    is_builtin: bool,
    metadata_path: Path,
    memory_path: Path,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        is_builtin=is_builtin,
        metadata_path=Path(metadata_path),
        memory_path=Path(memory_path),
    )


def _run_managed_root_contract(root: Path) -> None:
    """A/B/C：_managed_root 支持 builtin 共置布局；动态布局不变；越界失败关闭。"""
    import backend.data_source_connectors as connectors

    tmp_project = root / "project"
    agent_data = tmp_project / "agent_data"

    # A. builtin 共置布局：agent_data/<source_id>
    sid = "mysql-lzh-monitor"
    builtin_root = agent_data / sid
    with patch.object(connectors, "PROJECT_ROOT", tmp_project):
        record = _fake_record(
            True,
            builtin_root / "column_metadata_index.json",
            builtin_root / "mem.revision-1-x",
        )
        managed = DataSourceAssetCleaner._managed_root(sid, record)
        assert managed == builtin_root.resolve(), managed

    # B. 普通动态数据源布局不变：agent_data/data_sources/<source_id>
    sid2 = "dynamic-source"
    dynamic_root = agent_data / "data_sources" / sid2
    with patch.object(connectors, "PROJECT_ROOT", tmp_project):
        record = _fake_record(
            False,
            dynamic_root / "column_metadata_index.json",
            dynamic_root / "memory",
        )
        managed = DataSourceAssetCleaner._managed_root(sid2, record)
        assert managed == dynamic_root.resolve(), managed

    # C. 越界失败关闭
    with patch.object(connectors, "PROJECT_ROOT", tmp_project):
        # metadata 不在受管根
        record = _fake_record(False, root / "elsewhere" / "m.json", dynamic_root / "memory")
        assert DataSourceAssetCleaner._managed_root(sid2, record) is None
        # memory 不与 metadata 共根
        record = _fake_record(False, dynamic_root / "m.json", root / "elsewhere" / "mem")
        assert DataSourceAssetCleaner._managed_root(sid2, record) is None
        # builtin 路径不在 agent_data/<source_id>
        record = _fake_record(True, agent_data / "other" / "m.json", agent_data / "other" / "mem")
        assert DataSourceAssetCleaner._managed_root(sid, record) is None

    # symlink/junction 越界（平台不支持则跳过）
    symlink_target = root / "outside"
    symlink_target.mkdir(parents=True)
    link = agent_data / "builtin-link-sid"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            link.symlink_to(symlink_target, target_is_directory=True)
        except OSError:
            if os.name == "nt":
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(symlink_target)],
                    capture_output=True,
                    text=True,
                )
                if junction.returncode != 0:
                    raise OSError(junction.stderr or "mklink failed")
            else:
                raise
    except OSError:
        print("[SKIP] symlink/junction 创建不被平台允许")
        return
    try:
        with patch.object(connectors, "PROJECT_ROOT", tmp_project):
            record = _fake_record(
                True,
                link / "column_metadata_index.json",
                link / "mem",
            )
            assert DataSourceAssetCleaner._managed_root(sid, record) is None
    finally:
        if link.exists() or link.is_symlink():
            link.unlink()
    print("[PASS] managed-root contract A/B/C")


def _run_builtin_rollback_recovery(root: Path) -> None:
    """D：builtin rollback_failed 真实现场恢复（base 资产完整 + 候选/目标残留）。"""
    import backend.data_source_connectors as connectors

    key = Fernet.generate_key().decode("ascii")
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(key),
        environ={},
    )
    catalog.initialize()
    tmp_project = root / "project"
    managed_root = tmp_project / "agent_data" / "mysql-lzh-monitor"
    source = catalog.create(
        display_name="builtin-recovery",
        description="builtin-recovery",
        database_type="postgresql",
        host="127.0.0.1",
        port=5433,
        database_name="test",
        schema_name="public",
        username="test",
        password="test",
        source_id="mysql-lzh-monitor",
        metadata_path=managed_root / "column_metadata_index.json",
        memory_path=managed_root / "mem.revision-1-base",
    )
    source_id = source.source_id
    managed_root.mkdir(parents=True, exist_ok=True)
    metadata_file = managed_root / "column_metadata_index.json"
    metadata_bytes = json.dumps(METADATA, ensure_ascii=False).encode("utf-8")
    metadata_file.write_bytes(metadata_bytes)
    ddl_file = managed_root / "ddl_memories.json"
    ddl_file.write_text(json.dumps(["CREATE TABLE x (id int)"]), encoding="utf-8")
    docs_file = managed_root / "business_documents.json"
    docs_file.write_text(json.dumps(["docs"]), encoding="utf-8")
    manifest_file = managed_root / "asset_manifest.json"
    manifest_file.write_text(json.dumps({"source_id": source_id}), encoding="utf-8")
    base_memory = managed_root / "mem.revision-1-base"
    base_memory.mkdir(parents=True)
    (base_memory / ".asset_identity.json").write_text("{}", encoding="utf-8")
    (base_memory / "chroma.sqlite3").write_text("fake-base", encoding="utf-8")

    batch_id = "2-1234567890-abcdef"
    candidate_root = managed_root / f"candidate-{batch_id}"
    candidate_root.mkdir(parents=True)
    for name in (
        "column_metadata_index.json",
        "ddl_memories.json",
        "business_documents.json",
        "asset_manifest.json",
        "asset_provenance.json",
    ):
        (candidate_root / name).write_text("candidate", encoding="utf-8")
    target_memory = managed_root / f"mem.revision-2-{batch_id}"
    target_memory.mkdir(parents=True)
    (target_memory / "chroma.sqlite3").write_text("partial", encoding="utf-8")

    with catalog._lock, catalog._connection(write=True) as connection:
        connection.execute(
            """
            UPDATE data_sources SET is_builtin=1, status='error',
                enabled_for_chat=0, runtime_revision=1,
                last_error='数据源发布回滚失败，请重启服务执行恢复',
                metadata_path=?, memory_path=?
            WHERE source_id=?
            """,
            (str(metadata_file), str(base_memory), source_id),
        )
    snapshot = {
        "base_runtime_revision": 1,
        "target_runtime_revision": 2,
        "base_status": "metadata_ready",
        "base_enabled_for_chat": False,
        "base_routing_summary": "",
        "base_memory_path": str(base_memory),
        "target_memory_path": str(target_memory),
        "base_scope_fingerprint": "fp",
        "base_review_policy_fingerprint": "fp2",
        "base_updated_at": int(time.time()),
        "base_last_error": "",
    }
    cleaner = DataSourceAssetCleaner(catalog)
    base_hashes = {
        "metadata": cleaner._path_hash(metadata_file),
        "ddl": cleaner._path_hash(ddl_file),
        "documentation": cleaner._path_hash(docs_file),
        "manifest": cleaner._path_hash(manifest_file),
    }
    formal_names = {
        "metadata": "column_metadata_index.json",
        "memory": target_memory.name,
        "ddl": "ddl_memories.json",
        "documentation": "business_documents.json",
        "provenance": "asset_provenance.json",
        "manifest": "asset_manifest.json",
    }
    plan = []
    for name, formal_name in formal_names.items():
        formal = managed_root / formal_name
        candidate = (
            target_memory if name == "memory" else candidate_root / formal_name
        )
        plan.append(
            {
                "name": name,
                "candidate": str(candidate),
                "formal": str(formal),
                "backup": str(
                    formal.with_name(f".{formal.name}.backup-{batch_id}")
                ),
                "base_existed": name not in {"memory", "provenance"},
                "base_hash": base_hashes.get(name, ""),
                "target_hash": "",
            }
        )
    with catalog._lock, catalog._connection(write=True) as connection:
        connection.execute(
            """
            INSERT INTO active_asset_batches (
                source_id, batch_id, candidate_root, candidate_memory,
                published_memory_path, backup_paths_json, snapshot_json,
                asset_plan_json, backed_up_assets_json, installed_assets_json,
                phase, started_at, updated_at, owner_pid, last_error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source_id,
                batch_id,
                str(candidate_root),
                str(target_memory),
                str(target_memory),
                "[]",
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(plan, ensure_ascii=False),
                "[]",
                "[]",
                "rollback_failed",
                int(time.time()),
                int(time.time()),
                0,
                "DataSourceCatalogError: 发布回滚失败",
            ),
        )

    with patch.object(connectors, "PROJECT_ROOT", tmp_project):
        DataSourceAssetCleaner(catalog, _manager(catalog)).recover_incomplete_batches(
            source_id, grace_seconds=0
        )

    current = catalog.require(source_id)
    assert not catalog.active_asset_batches(source_id)
    assert current.status == "metadata_ready"
    assert current.runtime_revision == 1
    assert not current.enabled_for_chat
    assert not candidate_root.exists()
    assert not target_memory.exists()
    assert metadata_file.read_bytes() == metadata_bytes
    assert not (managed_root / "asset_provenance.json").exists()
    assert base_memory.exists()
    print("[PASS] builtin rollback_failed recovery")


def _run_chroma_metadata_sanitize(root: Path) -> None:
    """E/F：chroma:* 保留键在 preserved / extra 两条来源都被剥离。"""
    sid = "mysql-lzh-monitor"
    cleaned = DataSourceAssetPreparer._sanitize_chroma_metadata(
        {
            "chroma:document": "doc",
            "chroma:future": 1,
            "record_id": "r1",
            "question": "q",
            "args_json": "{}",
            "content_fingerprint": "fp",
            "category": "sql_example",
            "tool_name": "run_sql",
            "source_id": sid,
        }
    )
    assert "chroma:document" not in cleaned
    assert "chroma:future" not in cleaned
    assert cleaned["record_id"] == "r1"
    assert cleaned["tool_name"] == "run_sql"
    assert cleaned["question"] == "q"
    assert cleaned["content_fingerprint"] == "fp"
    assert cleaned["args_json"] == "{}"
    assert cleaned["category"] == "sql_example"

    # F-extra：_merge_extra_sql_tool_records 剥离保留键
    preserved = [("a", "qa", {"tool_name": "run_sql", "source_id": sid, "record_id": "a"})]
    extra = [
        (
            "b",
            "qb",
            {
                "tool_name": "run_sql",
                "source_id": sid,
                "record_id": "b",
                "question": "qb",
                "args_json": '{"sql": "SELECT 1"}',
                "chroma:document": "hidden",
                "chroma:future": 1,
            },
        )
    ]
    merged = DataSourceAssetPreparer._merge_extra_sql_tool_records(
        preserved, extra, source_id=sid
    )
    item_b = dict(merged[1][2])
    assert "chroma:document" not in item_b
    assert "chroma:future" not in item_b
    assert item_b["record_id"] == "b"
    assert item_b["question"] == "qb"
    assert item_b["args_json"] == '{"sql": "SELECT 1"}'
    assert item_b["tool_name"] == "run_sql"

    # F-preserved：_preserved_sql_tool_payload 从旧 Memory 回读后剥离
    metadata_dir = root / "meta"
    metadata_dir.mkdir(parents=True)
    metadata_path = metadata_dir / "index.json"
    metadata_path.write_text(
        json.dumps(
            [{"table": "water_data", "column": "id", "type": "int", "comment": ""}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory_dir = root / "old-memory"
    memory_dir.mkdir(parents=True)
    import backend.memory as memory_module

    fake = _FakeMemory(memory_dir)
    fake.collection.add(
        ids=["sql-1"],
        documents=["查询 water_data"],
        metadatas=[
            {
                "category": "sql_example",
                "tool_name": "run_sql",
                "source_id": sid,
                "question": "查询 water_data",
                "record_id": "sql-1",
                "args_json": '{"sql": "SELECT id FROM water_data"}',
                "content_fingerprint": "fp1",
                "chroma:document": "查询 water_data",
                "chroma:future": 2,
            }
        ],
    )
    with patch.object(
        memory_module,
        "create_memory",
        side_effect=lambda path: _FakeMemory(Path(path)),
    ):
        payload = DataSourceAssetPreparer._preserved_sql_tool_payload(
            source_id=sid,
            memory_path=memory_dir,
            metadata_path=metadata_path,
            database_type="postgresql",
            generated_records=[],
        )
    assert len(payload) == 1
    record_id, document, item = payload[0]
    assert record_id == "sql-1"
    assert "chroma:document" not in item
    assert "chroma:future" not in item
    assert item["record_id"] == "sql-1"
    assert item["tool_name"] == "run_sql"
    assert item["question"] == "查询 water_data"
    assert item["args_json"] == '{"sql": "SELECT id FROM water_data"}'
    assert item["content_fingerprint"] == "fp1"
    print("[PASS] chroma reserved metadata sanitized (preserved + extra)")


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
        _run_managed_root_contract(root / "managed-root-contract")
        _run_builtin_rollback_recovery(root / "builtin-rollback-recovery")
        _run_chroma_metadata_sanitize(root / "chroma-metadata-sanitize")
    print("data source crash recovery: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""B5 Runtime 关闭失败保护与 Chroma 实例隔离回归。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from config.data_source_config import DataSourceConfig


class Resource:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.failures_remaining = int(fail_once)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise OSError("injected close failure")


def _runtime(config, *, failing_memory: bool = False):
    return DataSourceRuntime(
        config=config,
        runner=Resource(),
        memory=Resource(fail_once=failing_memory),
        metadata_retriever=Resource(),
        sql_guard=Resource(),
        agent=Resource(),
    )


def _catalog(root: Path):
    metadata = root / "metadata.json"
    memory = root / "memory.revision-1"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("[]\n", encoding="utf-8")
    memory.mkdir()
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        environ={"USER": "u", "PASSWORD": "p"},
    )
    catalog.initialize(
        [
            {
                "source_id": "runtime-safe",
                "display_name": "Runtime 安全源",
                "description": "关闭失败保护",
                "database_type": "postgresql",
                "host": "127.0.0.1",
                "port": 5433,
                "database_name": "test",
                "schema_name": "public",
                "credential_reference": {
                    "username": "USER",
                    "password": "PASSWORD",
                },
                "metadata_path": metadata,
                "memory_path": memory,
                "selected_tables_count": 1,
                "selected_columns_count": 1,
            }
        ]
    )
    return catalog


def test_close_failure_protects_memory(root: Path) -> None:
    catalog = _catalog(root)
    runtimes = []

    def factory(config):
        runtime = _runtime(config, failing_memory=not runtimes)
        runtimes.append(runtime)
        return runtime

    manager = DataSourceRuntimeManager(
        DataSourceRegistry.from_catalog(catalog),
        {"postgresql": factory},
    )
    released = []
    manager.add_release_callback(released.append)
    old = manager.require("runtime-safe")
    old_path = old.config.memory_path.resolve()
    new_path = root / "memory.revision-2"
    new_path.mkdir()
    catalog.publish(
        "runtime-safe",
        routing_summary="revision-2",
        memory_path=new_path,
        expected_runtime_revision=1,
        expected_status="ready",
    )
    new = manager.require("runtime-safe")
    assert new is not old
    assert old_path in manager.active_asset_paths("runtime-safe")
    assert not released
    assert manager.retry_failed_closes("runtime-safe")
    assert old_path not in manager.active_asset_paths("runtime-safe")
    assert released == ["runtime-safe"]
    print("[PASS] Runtime 关闭失败不释放旧 Memory，重试成功后才通知清理")


def test_validation_failure_keeps_old(root: Path) -> None:
    catalog = _catalog(root)
    calls = []
    invalid_memory = Resource()

    def factory(config):
        calls.append(config)
        if len(calls) == 1:
            return _runtime(config)
        copied = DataSourceConfig(
            source_id=config.source_id,
            database_type=config.database_type,
            sql_dialect=config.sql_dialect,
            connection_settings=config.connection_settings,
            metadata_path=config.metadata_path,
            memory_path=config.memory_path,
            read_only=config.read_only,
        )
        return DataSourceRuntime(
            config=copied,
            runner=Resource(),
            memory=invalid_memory,
            metadata_retriever=Resource(),
            sql_guard=Resource(),
            agent=Resource(),
        )

    manager = DataSourceRuntimeManager(
        DataSourceRegistry.from_catalog(catalog),
        {"postgresql": factory},
    )
    old = manager.require("runtime-safe")
    new_path = root / "memory.revision-2"
    new_path.mkdir()
    catalog.publish(
        "runtime-safe",
        routing_summary="revision-2",
        memory_path=new_path,
        expected_runtime_revision=1,
        expected_status="ready",
    )
    try:
        manager.require("runtime-safe")
    except ValueError:
        pass
    else:
        raise AssertionError("Runtime validate 失败未被拒绝")
    assert manager.runtimes["runtime-safe"] is old
    assert invalid_memory.close_calls == 1
    print("[PASS] Runtime validate 失败关闭候选且旧缓存保持不变")


def test_candidate_memory_close_isolation() -> None:
    class Executor:
        def __init__(self):
            self.closed = False

        def shutdown(self, wait):
            self.closed = bool(wait)

    class System:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    class Client:
        def __init__(self, system):
            self._system = system

    shared_system = System()
    first = type("Memory", (), {})()
    first._executor = Executor()
    first._client = Client(shared_system)
    first._collection = object()
    second = type("Memory", (), {})()
    second._executor = Executor()
    second._client = Client(shared_system)
    second._collection = object()
    assert DataSourceAssetPreparer._close_memory(first)
    assert first._executor.closed
    assert not shared_system.stopped
    assert second._client._system is shared_system
    assert second._collection is not None
    print("[PASS] 候选 Memory 关闭不停止共享 Chroma 或破坏其他数据源")


def test_candidate_memory_close_failure_is_blocking() -> None:
    class Executor:
        def shutdown(self, wait):
            assert wait

    class Client:
        def close(self):
            raise OSError("injected candidate close failure")

    memory = type("Memory", (), {})()
    memory._executor = Executor()
    memory._client = Client()
    memory._collection = object()
    assert not DataSourceAssetPreparer._close_memory(memory)
    assert memory._collection is None
    assert memory._client is None
    print("[PASS] 候选 Memory 释放失败会被发布流程识别为阻断错误")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="b5-runtime-release-") as name:
        root = Path(name)
        test_close_failure_protects_memory(root / "close")
        test_validation_failure_keeps_old(root / "validate")
        test_candidate_memory_close_isolation()
        test_candidate_memory_close_failure_is_blocking()
    print("data source runtime release safety: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

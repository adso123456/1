"""按数据源独立创建和缓存运行时资源。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock
from types import MappingProxyType
from typing import Any

from backend.data_source_registry import DataSourceRegistry
from backend.data_source_runtime import (
    DataSourceRuntime,
    DataSourceRuntimeFactory,
)


class DataSourceRuntimeManager:
    """按 database_type 选择工厂，按 source_id 缓存运行时。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        factories: Mapping[str, DataSourceRuntimeFactory],
    ) -> None:
        if not isinstance(registry, DataSourceRegistry):
            raise TypeError("registry 必须是 DataSourceRegistry")
        if not isinstance(factories, Mapping):
            raise TypeError("factories 必须是 Mapping")
        if not factories:
            raise ValueError("factories 至少需要一个运行时工厂")

        factory_snapshot: dict[str, DataSourceRuntimeFactory] = {}
        for database_type, factory in factories.items():
            if not isinstance(database_type, str):
                raise TypeError("factory key 必须是字符串")
            if not database_type.strip():
                raise ValueError("factory key 必须是非空字符串")
            if not callable(factory):
                raise TypeError(
                    f"database_type {database_type} 的 factory 必须可调用"
                )
            factory_snapshot[database_type] = factory

        database_types = tuple(
            sorted(
                {
                    config.database_type
                    for config in registry.configs.values()
                }
            )
        )
        missing_database_types = tuple(
            database_type
            for database_type in database_types
            if database_type not in factory_snapshot
        )
        if missing_database_types:
            raise ValueError(
                "缺少 database_type 工厂："
                + ", ".join(missing_database_types)
            )

        self._registry = registry
        self._factories = MappingProxyType(dict(factory_snapshot))
        self._source_ids = registry.source_ids
        self._database_types = database_types
        self._runtimes: dict[str, DataSourceRuntime] = {}
        self._runtime_revisions: dict[str, int] = {}
        self._active_requests: dict[str, int] = {}
        self._retired_runtimes: dict[str, list[DataSourceRuntime]] = {}
        self._failed_close_runtimes: dict[str, list[DataSourceRuntime]] = {}
        self._release_callbacks: list[Callable[[str], None]] = []
        self._state_lock = RLock()
        self._build_locks: dict[str, Lock] = {
            source_id: Lock() for source_id in self._source_ids
        }

    @property
    def runtimes(self) -> Mapping[str, DataSourceRuntime]:
        with self._state_lock:
            snapshot = {
                source_id: self._runtimes[source_id]
                for source_id in sorted(self._runtimes)
            }
        return MappingProxyType(snapshot)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self._registry.source_ids

    @property
    def registry(self) -> DataSourceRegistry:
        return self._registry

    @property
    def database_types(self) -> tuple[str, ...]:
        return self._database_types

    def runtime_revision(self, source_id: str) -> int | None:
        with self._state_lock:
            return self._runtime_revisions.get(source_id)

    def require(self, source_id: str) -> DataSourceRuntime:
        config = self._registry.require(source_id)
        revision = 0
        catalog = self._registry.catalog
        if catalog is not None:
            record = catalog.require(source_id)
            if record.status != "ready" or not record.enabled_for_chat:
                raise ValueError(f"数据源 {source_id} 当前不可用于问数")
            revision = record.runtime_revision
        with self._state_lock:
            cached = self._runtimes.get(source_id)
            cached_revision = self._runtime_revisions.get(source_id)
        if cached is not None and cached_revision == revision:
            return cached

        with self._state_lock:
            build_lock = self._build_locks.setdefault(source_id, Lock())
        with build_lock:
            with self._state_lock:
                cached = self._runtimes.get(source_id)
                cached_revision = self._runtime_revisions.get(source_id)
            if cached is not None and cached_revision == revision:
                return cached

            factory = self._factories[config.database_type]
            runtime = factory(config)
            try:
                self._validate_runtime(source_id, config, runtime)
            except Exception:
                if isinstance(runtime, DataSourceRuntime):
                    self._close_runtime(runtime)
                raise
            if catalog is not None:
                current = catalog.require(source_id)
                if (
                    current.runtime_revision != revision
                    or runtime.config.memory_path.resolve()
                    != current.memory_path.resolve()
                    or current.status != "ready"
                    or not current.enabled_for_chat
                ):
                    self._close_runtime(runtime)
                    raise ValueError(
                        f"source_id {source_id} 的 Runtime 与 Catalog 版本不一致"
                    )

            retired: DataSourceRuntime | None = None
            with self._state_lock:
                retired = self._runtimes.get(source_id)
                self._runtimes[source_id] = runtime
                self._runtime_revisions[source_id] = revision
                if retired is not None and retired is not runtime:
                    if self._active_requests.get(source_id, 0):
                        self._retired_runtimes.setdefault(
                            source_id, []
                        ).append(retired)
                        retired = None
            if retired is not None:
                if self._close_runtime(retired):
                    self._notify_released(source_id)
                else:
                    with self._state_lock:
                        self._failed_close_runtimes.setdefault(
                            source_id, []
                        ).append(retired)
            return runtime

    def invalidate(self, source_id: str) -> None:
        """移除旧缓存；有请求持有时延迟到租约释放后关闭。"""
        retired: DataSourceRuntime | None = None
        with self._state_lock:
            retired = self._runtimes.pop(source_id, None)
            self._runtime_revisions.pop(source_id, None)
            if retired is not None and self._active_requests.get(source_id, 0):
                self._retired_runtimes.setdefault(source_id, []).append(retired)
                retired = None
        if retired is not None:
            if self._close_runtime(retired):
                self._notify_released(source_id)
            else:
                with self._state_lock:
                    self._failed_close_runtimes.setdefault(
                        source_id, []
                    ).append(retired)

    @contextmanager
    def acquire(self, source_id: str) -> Iterator[DataSourceRuntime]:
        while True:
            runtime = self.require(source_id)
            with self._state_lock:
                if self._runtimes.get(source_id) is runtime:
                    self._active_requests[source_id] = (
                        self._active_requests.get(source_id, 0) + 1
                    )
                    break
        try:
            yield runtime
        finally:
            retired: list[DataSourceRuntime] = []
            with self._state_lock:
                remaining = self._active_requests.get(source_id, 0) - 1
                if remaining > 0:
                    self._active_requests[source_id] = remaining
                else:
                    self._active_requests.pop(source_id, None)
                    retired = self._retired_runtimes.pop(source_id, [])
            released = []
            failed = []
            for item in retired:
                (released if self._close_runtime(item) else failed).append(item)
            if failed:
                with self._state_lock:
                    self._failed_close_runtimes.setdefault(
                        source_id, []
                    ).extend(failed)
            if released:
                self._notify_released(source_id)

    def add_release_callback(self, callback: Callable[[str], None]) -> None:
        if not callable(callback):
            raise TypeError("release callback 必须可调用")
        with self._state_lock:
            self._release_callbacks.append(callback)

    def active_asset_paths(self, source_id: str) -> frozenset[Path]:
        with self._state_lock:
            runtimes = [
                runtime
                for runtime in (
                    self._runtimes.get(source_id),
                    *self._retired_runtimes.get(source_id, []),
                    *self._failed_close_runtimes.get(source_id, []),
                )
                if runtime is not None
            ]
        return frozenset(
            runtime.config.memory_path.expanduser().resolve()
            for runtime in runtimes
        )

    def retry_failed_closes(self, source_id: str) -> bool:
        with self._state_lock:
            failed = self._failed_close_runtimes.pop(source_id, [])
        if not failed:
            return True
        remaining = [
            runtime for runtime in failed if not self._close_runtime(runtime)
        ]
        if remaining:
            with self._state_lock:
                self._failed_close_runtimes.setdefault(
                    source_id, []
                ).extend(remaining)
            return False
        self._notify_released(source_id)
        return True

    def _notify_released(self, source_id: str) -> None:
        with self._state_lock:
            callbacks = tuple(self._release_callbacks)
        for callback in callbacks:
            try:
                callback(source_id)
            except Exception:
                pass

    @staticmethod
    def _close_runtime(runtime: DataSourceRuntime) -> bool:
        resources = (
            runtime.agent,
            runtime.metadata_retriever,
            runtime.sql_guard,
            runtime.runner,
            runtime.memory,
        )
        seen: set[int] = set()
        succeeded = True
        for resource in resources:
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            for method_name in ("close", "shutdown"):
                method = getattr(resource, method_name, None)
                if callable(method):
                    try:
                        method()
                    except TypeError:
                        try:
                            method(wait=True)
                        except Exception:
                            succeeded = False
                    except Exception:
                        succeeded = False
                    break
        memory = runtime.memory
        if hasattr(memory, "_collection"):
            try:
                memory._collection = None
            except Exception:
                succeeded = False
        for attribute in ("_executor", "_client"):
            resource = getattr(memory, attribute, None)
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            method = getattr(
                resource,
                "shutdown" if attribute == "_executor" else "close",
                None,
            )
            if callable(method):
                try:
                    method(wait=True) if attribute == "_executor" else method()
                except TypeError:
                    try:
                        method()
                    except Exception:
                        succeeded = False
                except Exception:
                    succeeded = False
            if attribute == "_client":
                try:
                    memory._client = None
                except Exception:
                    succeeded = False
        return succeeded

    @staticmethod
    def _validate_runtime(
        source_id: str,
        config: Any,
        runtime: Any,
    ) -> None:
        if not isinstance(runtime, DataSourceRuntime):
            raise TypeError(
                f"source_id {source_id} 的 factory 必须返回 DataSourceRuntime"
            )
        if runtime.config is not config:
            raise ValueError(
                f"source_id {source_id} 的 runtime 必须使用 Registry 原始配置对象"
            )
        if runtime.source_id != source_id:
            raise ValueError(
                f"runtime source_id {runtime.source_id} 与请求 {source_id} 不一致"
            )
        if runtime.database_type != config.database_type:
            raise ValueError(
                f"source_id {source_id} 的 runtime database_type 不一致"
            )

    def __repr__(self) -> str:
        return (
            "DataSourceRuntimeManager("
            f"source_ids={self.source_ids!r}, "
            f"database_types={self.database_types!r})"
        )

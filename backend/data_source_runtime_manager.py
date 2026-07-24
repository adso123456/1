"""按数据源独立创建和缓存运行时资源。"""

from __future__ import annotations

from collections.abc import Mapping
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
        self._state_lock = RLock()
        self._build_locks = MappingProxyType(
            {source_id: Lock() for source_id in self._source_ids}
        )

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
        return self._source_ids

    @property
    def database_types(self) -> tuple[str, ...]:
        return self._database_types

    def require(self, source_id: str) -> DataSourceRuntime:
        config = self._registry.require(source_id)
        with self._state_lock:
            cached = self._runtimes.get(source_id)
        if cached is not None:
            return cached

        build_lock = self._build_locks[source_id]
        with build_lock:
            with self._state_lock:
                cached = self._runtimes.get(source_id)
            if cached is not None:
                return cached

            factory = self._factories[config.database_type]
            runtime = factory(config)
            self._validate_runtime(source_id, config, runtime)

            with self._state_lock:
                self._runtimes[source_id] = runtime
            return runtime

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

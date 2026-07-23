"""纯配置 DataSourceRegistry，不创建任何运行时资源。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from config.data_source_config import DataSourceConfig
from config.data_sources import build_postgresql_data_source_config


class DataSourceRegistry:
    """按 source_id 保存并显式解析不可变数据源配置。"""

    def __init__(
        self,
        configs: Iterable[DataSourceConfig] | Mapping[str, DataSourceConfig],
    ) -> None:
        if isinstance(configs, Mapping):
            snapshot = tuple(configs.values())
        else:
            snapshot = tuple(configs)
        if not snapshot:
            raise ValueError("DataSourceRegistry 至少需要一个数据源配置")

        registered: dict[str, DataSourceConfig] = {}
        for config in snapshot:
            if not isinstance(config, DataSourceConfig):
                raise TypeError("DataSourceRegistry 只接受 DataSourceConfig")
            if config.source_id in registered:
                raise ValueError(f"重复 source_id: {config.source_id}")
            registered[config.source_id] = config

        ordered = {
            source_id: registered[source_id] for source_id in sorted(registered)
        }
        self._configs = MappingProxyType(ordered)
        self._source_ids = tuple(ordered)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self._source_ids

    @property
    def configs(self) -> Mapping[str, DataSourceConfig]:
        return self._configs

    def require(self, source_id: str) -> DataSourceConfig:
        if source_id is None:
            raise ValueError("source_id 必须显式提供")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id 必须是非空字符串")
        try:
            return self._configs[source_id]
        except KeyError:
            raise ValueError(f"未知 source_id: {source_id}") from None

    def __repr__(self) -> str:
        return f"DataSourceRegistry(source_ids={self.source_ids!r})"


def build_current_data_source_registry(
    *,
    environ: Mapping[str, str] | None = None,
    scope_path: Path | None = None,
) -> DataSourceRegistry:
    """用当前 PostgreSQL 离线配置构造单数据源 Registry。"""
    build_kwargs: dict[str, object] = {"environ": environ}
    if scope_path is not None:
        build_kwargs["scope_path"] = scope_path
    config = build_postgresql_data_source_config(**build_kwargs)
    return DataSourceRegistry((config,))

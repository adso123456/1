"""数据源无关的运行时资源容器契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from config.data_source_config import DataSourceConfig


@dataclass(frozen=True, repr=False)
class DataSourceRuntime:
    """一个数据源已经完整创建的运行时资源集合。"""

    config: DataSourceConfig = field(repr=False)
    runner: object = field(repr=False)
    memory: object = field(repr=False)
    metadata_retriever: object = field(repr=False)
    sql_guard: object = field(repr=False)
    agent: object = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.config, DataSourceConfig):
            raise TypeError("config 必须是 DataSourceConfig")
        for field_name in (
            "runner",
            "memory",
            "metadata_retriever",
            "sql_guard",
            "agent",
        ):
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 必须显式提供")

    @property
    def source_id(self) -> str:
        return self.config.source_id

    @property
    def database_type(self) -> str:
        return self.config.database_type

    @property
    def sql_dialect(self) -> str:
        return self.config.sql_dialect

    def __repr__(self) -> str:
        return (
            "DataSourceRuntime("
            f"source_id={self.source_id!r}, "
            f"database_type={self.database_type!r}, "
            f"sql_dialect={self.sql_dialect!r})"
        )


class DataSourceRuntimeFactory(Protocol):
    """从不可变数据源配置创建完整运行时的工厂契约。"""

    def __call__(
        self,
        config: DataSourceConfig,
    ) -> DataSourceRuntime:
        ...

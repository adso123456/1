"""数据库无关的 Runtime 装配工厂。

提供单一 RuntimeBuilders 注入集合与 assemble_runtime 装配流程，
消除 PostgreSQL 与 MySQL Runtime 工厂中的重复 Builders 定义和装配逻辑。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.data_source_runtime import DataSourceRuntime
from config.data_source_config import DataSourceConfig


@dataclass(frozen=True)
class RuntimeBuilders:
    """数据库无关的 Runtime 资源构建函数集合。"""

    runner_builder: Callable[[dict[str, Any]], object] = field(repr=False)
    memory_builder: Callable[[Path], object] = field(repr=False)
    metadata_retriever_builder: Callable[[Path], object] = field(repr=False)
    sql_guard_builder: Callable[[Path], object] = field(repr=False)
    agent_builder: Callable[
        [
            DataSourceConfig,
            object,
            object,
            object,
            object,
            Mapping[str, str] | None,
        ],
        object,
    ] = field(repr=False)

    def __post_init__(self) -> None:
        for name in (
            "runner_builder",
            "memory_builder",
            "metadata_retriever_builder",
            "sql_guard_builder",
            "agent_builder",
        ):
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} 必须可调用")


def _require_resource(name: str, resource: object) -> object:
    if resource is None:
        raise ValueError(f"{name} builder 不得返回 None")
    return resource


def assemble_runtime(
    config: DataSourceConfig,
    builders: RuntimeBuilders,
    *,
    environ: Mapping[str, str] | None = None,
) -> DataSourceRuntime:
    """按固定顺序装配一个完整 Runtime。

    顺序：runner → memory → metadata_retriever → sql_guard → agent。
    每个资源构建失败时异常原样传播；返回 None 时抛 ValueError。
    不吞掉异常，不改变资源构建顺序。
    """
    runner = _require_resource(
        "runner",
        builders.runner_builder(dict(config.connection_settings)),
    )
    memory = _require_resource(
        "memory",
        builders.memory_builder(config.memory_path),
    )
    metadata_retriever = _require_resource(
        "metadata_retriever",
        builders.metadata_retriever_builder(config.metadata_path),
    )
    sql_guard = _require_resource(
        "sql_guard",
        builders.sql_guard_builder(config.metadata_path),
    )
    agent = _require_resource(
        "agent",
        builders.agent_builder(
            config,
            runner,
            memory,
            metadata_retriever,
            sql_guard,
            environ,
        ),
    )
    return DataSourceRuntime(
        config=config,
        runner=runner,
        memory=memory,
        metadata_retriever=metadata_retriever,
        sql_guard=sql_guard,
        agent=agent,
    )

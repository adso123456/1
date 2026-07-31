"""从 PostgreSQL 数据源配置创建完整运行时资源。"""

from __future__ import annotations

from collections.abc import Mapping

from backend.data_source_runtime import DataSourceRuntime
from backend.runtime_factory import RuntimeBuilders, assemble_runtime
from config.data_source_config import DataSourceConfig


class PostgreSQLRuntimeBuilders(RuntimeBuilders):
    """PostgreSQL Runtime 资源构建函数集合（RuntimeBuilders 兼容子类）。"""


def _create_postgresql_agent(
    config: DataSourceConfig,
    runner: object,
    memory: object,
    metadata_retriever: object,
    sql_guard: object,
    environ: Mapping[str, str] | None,
) -> object:
    """使用共享 Agent 装配工厂组装当前 PostgreSQL Agent。"""
    from backend.agent_assembly import build_shared_agent
    from config.settings import validate_db_config

    return build_shared_agent(
        config=config,
        runner=runner,
        memory=memory,
        metadata_retriever=metadata_retriever,
        sql_guard=sql_guard,
        environ=environ,
        sql_dialect="postgresql",
        validate_db_config=validate_db_config,
        verbose=True,
    )


def _load_default_builders() -> PostgreSQLRuntimeBuilders:
    """仅在创建真实 Runtime 时加载 Vanna、Chroma 和数据库相关实现。"""
    from backend.diagnostic_metadata_retriever import (
        DiagnosticMetadataRetriever,
    )
    from backend.memory import create_memory
    from backend.schema_preserving_sql import SchemaPreservingPostgresRunner
    from backend.sql_guard import SQLGuard

    return PostgreSQLRuntimeBuilders(
        runner_builder=lambda connection_settings: SchemaPreservingPostgresRunner(
            **connection_settings
        ),
        memory_builder=lambda memory_path: create_memory(
            persist_directory=memory_path
        ),
        metadata_retriever_builder=lambda metadata_path: (
            DiagnosticMetadataRetriever(index_path=metadata_path)
        ),
        sql_guard_builder=lambda metadata_path: SQLGuard(
            index_path=metadata_path
        ),
        agent_builder=_create_postgresql_agent,
    )


def create_postgresql_runtime(
    config: DataSourceConfig,
    *,
    builders: PostgreSQLRuntimeBuilders | None = None,
    environ: Mapping[str, str] | None = None,
) -> DataSourceRuntime:
    """创建与一个 PostgreSQL 配置严格对应的完整 Runtime。"""
    if not isinstance(config, DataSourceConfig):
        raise TypeError("config 必须是 DataSourceConfig")
    if (
        config.database_type != "postgresql"
        or config.sql_dialect != "postgresql"
        or config.read_only is not True
    ):
        raise ValueError(
            "PostgreSQL Runtime 仅接受 database_type=postgresql、"
            "sql_dialect=postgresql、read_only=True"
        )
    if builders is not None and not isinstance(
        builders, PostgreSQLRuntimeBuilders
    ):
        raise TypeError("builders 必须是 PostgreSQLRuntimeBuilders")

    selected_builders = (
        builders if builders is not None else _load_default_builders()
    )
    return assemble_runtime(config, selected_builders, environ=environ)

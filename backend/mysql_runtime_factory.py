"""从 MySQL 数据源配置创建独立运行时资源。"""

from __future__ import annotations

from collections.abc import Mapping

from backend.data_source_runtime import DataSourceRuntime
from backend.runtime_factory import RuntimeBuilders, assemble_runtime
from config.data_source_config import DataSourceConfig


class MySQLRuntimeBuilders(RuntimeBuilders):
    """MySQL Runtime 资源构建函数集合（RuntimeBuilders 兼容子类）。"""


def _create_mysql_agent(
    config: DataSourceConfig,
    runner: object,
    memory: object,
    metadata_retriever: object,
    sql_guard: object,
    environ: Mapping[str, str] | None,
) -> object:
    """使用共享 Agent 装配工厂组装 MySQL Agent。"""
    from backend.agent_assembly import build_shared_agent

    return build_shared_agent(
        config=config,
        runner=runner,
        memory=memory,
        metadata_retriever=metadata_retriever,
        sql_guard=sql_guard,
        environ=environ,
        sql_dialect="mysql",
        validate_db_config=None,
        verbose=False,
    )


def _load_default_builders() -> MySQLRuntimeBuilders:
    """仅在创建真实 Runtime 时加载 Vanna、Chroma 和数据库相关实现。"""
    from backend.diagnostic_metadata_retriever import (
        DiagnosticMetadataRetriever,
    )
    from backend.memory import create_memory
    from backend.mysql_runner import ReadOnlyMySQLRunner
    from backend.mysql_sql_guard import MySQLSQLGuard

    return MySQLRuntimeBuilders(
        runner_builder=lambda settings: ReadOnlyMySQLRunner(**settings),
        memory_builder=lambda path: create_memory(persist_directory=path),
        metadata_retriever_builder=lambda path: DiagnosticMetadataRetriever(
            index_path=path
        ),
        sql_guard_builder=lambda path: MySQLSQLGuard(index_path=path),
        agent_builder=_create_mysql_agent,
    )


def create_mysql_runtime(
    config: DataSourceConfig,
    *,
    builders: MySQLRuntimeBuilders | None = None,
    environ: Mapping[str, str] | None = None,
) -> DataSourceRuntime:
    """创建与 mysql-lzh-monitor 配置严格对应的 Runtime。"""
    if not isinstance(config, DataSourceConfig):
        raise TypeError("config 必须是 DataSourceConfig")
    if (
        config.database_type != "mysql"
        or config.sql_dialect != "mysql"
        or config.read_only is not True
    ):
        raise ValueError(
            "MySQL Runtime 仅接受 database_type=mysql、"
            "sql_dialect=mysql、read_only=True"
        )
    if builders is not None and not isinstance(builders, MySQLRuntimeBuilders):
        raise TypeError("builders 必须是 MySQLRuntimeBuilders")

    selected = builders or _load_default_builders()
    return assemble_runtime(config, selected, environ=environ)

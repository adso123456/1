"""从 MySQL 数据源配置创建独立运行时资源。"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.data_source_runtime import DataSourceRuntime
from config.data_source_config import DataSourceConfig


@dataclass(frozen=True)
class MySQLRuntimeBuilders:
    """可注入的 MySQL Runtime 资源构建函数集合。"""

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


def _create_mysql_agent(
    config: DataSourceConfig,
    runner: object,
    memory: object,
    metadata_retriever: object,
    sql_guard: object,
    environ: Mapping[str, str] | None,
) -> object:
    """用 MySQL 独立 Runner、Metadata 与 Memory 组装 Agent。"""
    from vanna import Agent, AgentConfig
    from vanna.core.enhancer.default import DefaultLlmContextEnhancer
    from vanna.core.registry import ToolRegistry
    from vanna.core.user import RequestContext, User, UserResolver
    from vanna.tools import LocalFileSystem

    from backend.guarded_run_sql_tool import GuardedRunSqlTool
    from backend.metadata_context_enhancer import (
        DeterministicMetadataContextEnhancer,
    )
    from backend.prompts import OptimizedSystemPromptBuilder
    from backend.query_context import (
        OriginalQuestionContextEnricher,
        OriginalQuestionLifecycleHook,
    )
    from backend.schema_preserving_sql import SchemaPreservingRunSqlTool
    from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
    from backend.tracing_llm_service import TracingOpenAILlmService
    from config.settings import AGENT_DATA_DIR

    class SimpleUserResolver(UserResolver):
        async def resolve_user(self, request_context: RequestContext):
            return User(
                id="demo",
                username="demo",
                metadata=dict(request_context.metadata or {}),
            )

    source = os.environ if environ is None else environ
    api_key = source.get("DEEPSEEK_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("缺少必需的环境变量：DEEPSEEK_API_KEY")
    disable_legacy_sql_examples = (
        source.get("VANNA_DISABLE_LEGACY_SQL_EXAMPLES", "0") == "1"
    )

    llm = TracingOpenAILlmService(
        model="deepseek-v4-pro",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )
    tool_registry = ToolRegistry()
    raw_run_sql_tool = SchemaPreservingRunSqlTool(
        sql_runner=runner,
        file_system=LocalFileSystem(working_directory=AGENT_DATA_DIR),
    )
    tool_registry.register_local_tool(
        GuardedRunSqlTool(
            inner_tool=raw_run_sql_tool,
            sql_guard=sql_guard,
        ),
        access_groups=[],
    )

    default_enhancer = DefaultLlmContextEnhancer(memory)
    deterministic_enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=default_enhancer,
        metadata_retriever=metadata_retriever,
    )
    llm_context_enhancer = (
        deterministic_enhancer
        if disable_legacy_sql_examples
        else SqlExampleContextEnhancer(
            base_enhancer=deterministic_enhancer,
            memory=memory,
            sql_guard=sql_guard,
            top_k=5,
        )
    )
    return Agent(
        llm_service=llm,
        tool_registry=tool_registry,
        user_resolver=SimpleUserResolver(),
        agent_memory=memory,
        llm_context_enhancer=llm_context_enhancer,
        lifecycle_hooks=[OriginalQuestionLifecycleHook()],
        context_enrichers=[OriginalQuestionContextEnricher()],
        config=AgentConfig(stream_responses=True),
        system_prompt_builder=OptimizedSystemPromptBuilder(
            sql_dialect="mysql"
        ),
    )


def _load_default_builders() -> MySQLRuntimeBuilders:
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
    runner = _require_resource(
        "runner", selected.runner_builder(dict(config.connection_settings))
    )
    memory = _require_resource("memory", selected.memory_builder(config.memory_path))
    metadata = _require_resource(
        "metadata_retriever",
        selected.metadata_retriever_builder(config.metadata_path),
    )
    guard = _require_resource(
        "sql_guard", selected.sql_guard_builder(config.metadata_path)
    )
    agent = _require_resource(
        "agent",
        selected.agent_builder(
            config, runner, memory, metadata, guard, environ
        ),
    )
    return DataSourceRuntime(
        config=config,
        runner=runner,
        memory=memory,
        metadata_retriever=metadata,
        sql_guard=guard,
        agent=agent,
    )

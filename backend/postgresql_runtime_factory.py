"""从 PostgreSQL 数据源配置创建完整运行时资源。"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.data_source_runtime import DataSourceRuntime
from config.data_source_config import DataSourceConfig


@dataclass(frozen=True)
class PostgreSQLRuntimeBuilders:
    """可注入的 PostgreSQL Runtime 资源构建函数集合。"""

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


def _create_postgresql_agent(
    config: DataSourceConfig,
    runner: object,
    memory: object,
    metadata_retriever: object,
    sql_guard: object,
    environ: Mapping[str, str] | None,
) -> object:
    """使用既有组件和顺序组装当前 PostgreSQL Agent。"""
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
    from config.settings import AGENT_DATA_DIR, validate_db_config

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

    print("初始化 LLM 服务 (deepseek-v4-pro via DeepSeek official API)...")
    llm = TracingOpenAILlmService(
        model="deepseek-v4-pro",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    print("连接 PostgreSQL...")
    validate_db_config(config.connection_settings)

    print("加载 ChromaDB 记忆库 (中文embedding + 0.55阈值)...")
    print("注册工具 (run_sql)...")
    tool_registry = ToolRegistry()
    file_system = LocalFileSystem(working_directory=AGENT_DATA_DIR)
    raw_run_sql_tool = SchemaPreservingRunSqlTool(
        sql_runner=runner,
        file_system=file_system,
    )
    tool_registry.register_local_tool(
        GuardedRunSqlTool(
            inner_tool=raw_run_sql_tool,
            sql_guard=sql_guard,
        ),
        access_groups=[],
    )
    print(
        "创建 Agent (确定性元数据 + SQL示例上下文 "
        "+ DefaultLlmContextEnhancer 注入检索记忆)..."
    )

    default_enhancer = DefaultLlmContextEnhancer(memory)
    deterministic_enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=default_enhancer,
        metadata_retriever=metadata_retriever,
    )
    if disable_legacy_sql_examples:
        print("Legacy SQL examples: DISABLED")
        llm_context_enhancer = deterministic_enhancer
    else:
        print("Legacy SQL examples: ENABLED")
        llm_context_enhancer = SqlExampleContextEnhancer(
            base_enhancer=deterministic_enhancer,
            memory=memory,
            sql_guard=sql_guard,
            top_k=5,
        )

    agent = Agent(
        llm_service=llm,
        tool_registry=tool_registry,
        user_resolver=SimpleUserResolver(),
        agent_memory=memory,
        llm_context_enhancer=llm_context_enhancer,
        lifecycle_hooks=[OriginalQuestionLifecycleHook()],
        context_enrichers=[OriginalQuestionContextEnricher()],
        config=AgentConfig(stream_responses=True),
        system_prompt_builder=OptimizedSystemPromptBuilder(),
    )
    print("Agent 创建完成!\n")
    return agent


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

    selected_builders = builders if builders is not None else _load_default_builders()
    runner = _require_resource(
        "runner",
        selected_builders.runner_builder(dict(config.connection_settings)),
    )
    memory = _require_resource(
        "memory",
        selected_builders.memory_builder(config.memory_path),
    )
    metadata_retriever = _require_resource(
        "metadata_retriever",
        selected_builders.metadata_retriever_builder(config.metadata_path),
    )
    sql_guard = _require_resource(
        "sql_guard",
        selected_builders.sql_guard_builder(config.metadata_path),
    )
    agent = _require_resource(
        "agent",
        selected_builders.agent_builder(
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

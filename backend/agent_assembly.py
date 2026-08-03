"""数据库无关的 Agent 装配工厂。

PostgreSQL 与 MySQL Runtime 共享同一套 Agent 组装逻辑，
数据库差异通过显式参数传入（sql_dialect、validate_db_config、verbose）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from config.data_source_config import DataSourceConfig


def build_shared_agent(
    *,
    config: DataSourceConfig,
    runner: object,
    memory: object,
    metadata_retriever: object,
    sql_guard: object,
    environ: Mapping[str, str] | None = None,
    sql_dialect: str = "postgresql",
    validate_db_config: Callable[[Mapping[str, Any]], None] | None = None,
    verbose: bool = True,
) -> object:
    """使用既有组件和顺序组装数据库无关的 Agent。

    参数说明：
    - sql_dialect：传给 OptimizedSystemPromptBuilder 的方言
    - validate_db_config：数据库专属连接配置校验（如 PostgreSQL），
      为 None 时不执行（如 MySQL）
    - verbose：是否打印装配过程提示（PostgreSQL 当前打印，MySQL 不打印）

    保持以下内容不变：
    - Prompt 文本及结构（OptimizedSystemPromptBuilder）
    - Tool 注册名称（run_sql）和 GuardedRunSqlTool 包装顺序
    - Context Enhancer 顺序（Default → Deterministic → SqlExample）
    - Lifecycle Hook 和 Enricher（OriginalQuestionLifecycleHook /
      OriginalQuestionContextEnricher）
    - LLM 配置（deepseek-v4-pro via api.deepseek.com）和环境变量错误语义
    """
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
    from config.performance_settings import QueryPerformanceSettings
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

    if verbose:
        print("初始化 LLM 服务 (deepseek-v4-pro via DeepSeek official API)...")
    performance_settings = QueryPerformanceSettings.from_environment(source)
    llm = TracingOpenAILlmService(
        model="deepseek-v4-pro",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        settings=performance_settings,
    )

    if validate_db_config is not None:
        if verbose:
            print("连接 PostgreSQL...")
        validate_db_config(config.connection_settings)

    if verbose:
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
    if verbose:
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
        if verbose:
            print("Legacy SQL examples: DISABLED")
        llm_context_enhancer = deterministic_enhancer
    else:
        if verbose:
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
        config=AgentConfig(
            stream_responses=True,
            max_tool_iterations=performance_settings.agent_max_tool_rounds,
        ),
        system_prompt_builder=OptimizedSystemPromptBuilder(
            sql_dialect=sql_dialect
        ),
    )
    if verbose:
        print("Agent 创建完成!\n")
    return agent

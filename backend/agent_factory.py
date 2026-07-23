"""Vanna Agent 组装。"""

import os
import sys

from vanna import Agent, AgentConfig
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, User, UserResolver
from vanna.tools import LocalFileSystem

from backend.guarded_run_sql_tool import GuardedRunSqlTool
from backend.memory import create_memory
from backend.metadata_context_enhancer import DeterministicMetadataContextEnhancer
from backend.metadata_retriever import DeterministicMetadataRetriever
from backend.prompts import OptimizedSystemPromptBuilder
from backend.query_context import (
    OriginalQuestionContextEnricher,
    OriginalQuestionLifecycleHook,
)
from backend.schema_preserving_sql import (
    SchemaPreservingPostgresRunner,
    SchemaPreservingRunSqlTool,
)
from backend.request_diagnostics import write_trace_json
from backend.sql_example_context_enhancer import SqlExampleContextEnhancer
from backend.sql_guard import SQLGuard
from backend.tracing_llm_service import TracingOpenAILlmService
from config.settings import AGENT_DATA_DIR, DB_KWARGS, validate_db_config


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
DISABLE_LEGACY_SQL_EXAMPLES = os.getenv(
    "VANNA_DISABLE_LEGACY_SQL_EXAMPLES", "0"
) == "1"

if not DEEPSEEK_API_KEY:
    print("[FAIL] DEEPSEEK_API_KEY is required")
    sys.exit(1)


class SimpleUserResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext):
        return User(
            id="demo",
            username="demo",
            metadata=dict(request_context.metadata or {}),
        )


class DiagnosticMetadataRetriever(DeterministicMetadataRetriever):
    """旁路记录实际确定性 Metadata 检索，不改变检索结果。"""

    def retrieve(self, question: str, top_n: int = 10):
        results = super().retrieve(question, top_n=top_n)
        write_trace_json(
            "metadata-retrieval.json",
            {
                "question": question,
                "top_n": top_n,
                "result_count": len(results),
                "results": results,
            },
        )
        return results


def create_agent():
    """创建 Agent — 与 train_step3 共享 embedding function 和阈值"""
    print("初始化 LLM 服务 (deepseek-v4-pro via DeepSeek official API)...")
    llm = TracingOpenAILlmService(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    print("连接 PostgreSQL...")
    validate_db_config()
    pg_runner = SchemaPreservingPostgresRunner(**DB_KWARGS)

    print("加载 ChromaDB 记忆库 (中文embedding + 0.55阈值)...")
    memory = create_memory()

    print("注册工具 (run_sql)...")
    tool_registry = ToolRegistry()
    file_system = LocalFileSystem(working_directory=AGENT_DATA_DIR)
    raw_run_sql_tool = SchemaPreservingRunSqlTool(
        sql_runner=pg_runner,
        file_system=file_system,
    )
    # GuardedRunSqlTool 和 SqlExampleContextEnhancer 共用同一个 SQLGuard 实例
    sql_guard = SQLGuard()
    tool_registry.register_local_tool(
        GuardedRunSqlTool(
            inner_tool=raw_run_sql_tool,
            sql_guard=sql_guard,
        ),
        access_groups=[],
    )
    print("创建 Agent (确定性元数据 + SQL示例上下文 + DefaultLlmContextEnhancer 注入检索记忆)...")

    # 第1层：Vanna 默认 context enhancer（DDL/文档检索注入）
    default_enhancer = DefaultLlmContextEnhancer(memory)

    # 第2层：确定性元数据 context enhancer（P0 候选表+字段）
    deterministic_enhancer = DeterministicMetadataContextEnhancer(
        base_enhancer=default_enhancer,
        metadata_retriever=DiagnosticMetadataRetriever(),
    )

    # 第3层：SQL 示例 context enhancer（L2 approved SQL 示例注入到 system prompt）
    if DISABLE_LEGACY_SQL_EXAMPLES:
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

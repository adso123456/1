"""
第4步：Vanna Agent 问答测试 (CLI 模式)
验证 LLM + SQL + 检索 + 出图 全链路
"""
import asyncio
import os
import sys
import uuid
from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.tools import RunSqlTool, VisualizeDataTool, LocalFileSystem
from agent_config import DB_KWARGS, create_memory
from vanna import Agent, AgentConfig
from vanna.core.system_prompt.default import DefaultSystemPromptBuilder


class OptimizedSystemPromptBuilder(DefaultSystemPromptBuilder):
    """在默认 prompt 基础上追加本项目专属的查询优化规则"""

    async def build_system_prompt(self, user, tools):
        base = await super().build_system_prompt(user, tools) or ""
        extra = """
# CRITICAL QUERY RULES — FOLLOW STRICTLY

1. **ABSOLUTELY FORBIDDEN: Any schema exploration queries.**
   - DO NOT query information_schema, pg_catalog, or any system tables.
   - DO NOT run SELECT * or SELECT DISTINCT to explore table structures.
   - DO NOT run queries whose ONLY purpose is to discover column names or data types.
   The complete DDL with Chinese column descriptions for all 6 available tables
   is injected into your context automatically. TRUST IT. Use it directly.

2. **ABSOLUTELY FORBIDDEN: SELECT geometry columns (geom).**
   These contain binary data. Never include them.

3. **Each user question should use AT MOST 1-2 SQL queries total.**
   Write the final answer query directly. Do not run preliminary exploration queries.

4. **For queries involving monitoring data (rs_outlet_monitor_v2):**
   Always include ORDER BY sampling_time DESC LIMIT 50 unless user specifies otherwise.

5. **Chart type annotation:**
   End EVERY response with: <!-- chart_type: bar|line|pie|none -->
   - Aggregate statistics with meaningful numeric dimension (count, sum, avg by category) → bar
   - Time series / trend data → line
   - Proportion / share of a whole → pie
   - Detail / list data with no meaningful aggregation (e.g. "show top 5 X with their names and addresses")
     → none (do NOT create a chart — pie chart makes no sense for 5 individual items)
   - Raw monitoring records with no aggregation → none
"""
        return base + extra

# === LLM 配置 (deepseek-v4-pro via opencode.ai) ===
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY")
if not OPENCODE_API_KEY:
    print("[FAIL] OPENCODE_API_KEY 未设置!")
    sys.exit(1)


class SimpleUserResolver(UserResolver):
    """固定用户——测试用"""

    async def resolve_user(self, request_context: RequestContext):
        return User(id="demo", username="demo")


async def main():
    # 1. 创建 LlmService
    print("初始化 LLM 服务 (deepseek-v4-pro via opencode.ai)...")
    llm = OpenAILlmService(
        model="deepseek-v4-pro",
        api_key=OPENCODE_API_KEY,
        base_url="https://opencode.ai/zen/go/v1",
    )

    # 2. 创建 PostgresRunner (agent_config 共享配置)
    print("连接 PostgreSQL...")
    pg_runner = PostgresRunner(**DB_KWARGS)

    # 3. 创建 AgentMemory (agent_config 共享: 中文embedding + 0.55阈值)
    print("加载 ChromaDB 记忆库...")
    memory = create_memory()

    # 4. 注册工具
    tool_registry = ToolRegistry()
    file_system = LocalFileSystem(working_directory="E:/3/posgresql/1/agent_data")
    tool_registry.register_local_tool(
        RunSqlTool(sql_runner=pg_runner, file_system=file_system),
        access_groups=[],
    )
    tool_registry.register_local_tool(
        VisualizeDataTool(file_system=file_system),
        access_groups=[],
    )
    print(f"已注册工具: {await tool_registry.list_tools()}")

    # 5. 创建 Agent (用 DefaultLlmContextEnhancer 注入检索记忆)
    print("创建 Agent...")
    agent = Agent(
        llm_service=llm,
        tool_registry=tool_registry,
        user_resolver=SimpleUserResolver(),
        agent_memory=memory,
        llm_context_enhancer=DefaultLlmContextEnhancer(memory),
        config=AgentConfig(stream_responses=True),
        system_prompt_builder=OptimizedSystemPromptBuilder(),
    )

    request_context = RequestContext(
        cookies={"vanna_email": "demo@test.com"},
        remote_addr="127.0.0.1",
    )

    # 6. 测试问题列表
    test_questions = [
        # 简单查询 (测试基本SQL生成+检索)
        "点军区有哪些排污口？只列前5条，显示名称和地址。",
        # 监测数据查询 (测试时间过滤 + JOIN)
        "查看最近10条排污口监测数据，包含排污口名称、采样时间、pH、COD、氨氮。",
    ]

    print("\n" + "=" * 60)
    print("Vanna Agent 问答测试")
    print("=" * 60)

    for i, question in enumerate(test_questions, 1):
        conv_id = str(uuid.uuid4())
        print(f"\n--- 问题{i}: {question} ---")

        async for component in agent.send_message(
            request_context=request_context,
            message=question,
            conversation_id=conv_id,
        ):
            _print_component(component)

        print()

    print("=" * 60)
    print("测试完成!")
    print("=" * 60)


def _print_component(component):
    """打印 Agent 返回的各类 UI 组件"""
    if hasattr(component, "simple_component") and component.simple_component:
        sc = component.simple_component
        if hasattr(sc, "text") and sc.text:
            print(f"  [TEXT] {sc.text}")
        elif hasattr(sc, "type") and sc.type:
            print(f"  [{sc.type.value if hasattr(sc.type, 'value') else sc.type}]")
    elif hasattr(component, "rich_component") and component.rich_component:
        rc = component.rich_component
        rc_type = type(rc).__name__
        if rc_type == "DataFrameComponent" and hasattr(rc, "dataframe"):
            print(f"  [TABLE] {len(rc.dataframe)} rows")
            print(rc.dataframe.to_string())
        elif hasattr(rc, "content") and rc.content:
            print(f"  [{rc_type}] {rc.content[:200]}...")
        else:
            print(f"  [{rc_type}]")
    elif hasattr(component, "content") and component.content:
        print(f"  [CONTENT] {component.content}")
    elif hasattr(component, "type"):
        print(f"  [{component.type}]")
    else:
        # 兜底：打印类型名
        print(f"  [{type(component).__name__}]")


if __name__ == "__main__":
    asyncio.run(main())

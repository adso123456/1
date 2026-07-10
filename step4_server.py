"""
第4步: 启动 Vanna Web 服务 (FastAPI + <vanna-chat> 界面)
访问 http://localhost:8000 在浏览器中测试问答 + 出图
"""
import asyncio
import os
import sys
from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.tools import RunSqlTool, LocalFileSystem
from agent_config import DB_KWARGS, create_memory
from tools.guarded_run_sql_tool import GuardedRunSqlTool
from tools.metadata_context_enhancer import DeterministicMetadataContextEnhancer
from tools.metadata_retriever import DeterministicMetadataRetriever
from tools.sql_example_context_enhancer import SqlExampleContextEnhancer
from tools.sql_guard import SQLGuard
from vanna import Agent, AgentConfig
from vanna.core.system_prompt.default import DefaultSystemPromptBuilder
from vanna.servers.fastapi.app import VannaFastAPIServer


class OptimizedSystemPromptBuilder(DefaultSystemPromptBuilder):
    """在默认 prompt 基础上追加本项目专属的查询优化规则"""

    async def build_system_prompt(self, user, tools):
        base = await super().build_system_prompt(user, tools) or ""
        extra = """
# CRITICAL QUERY RULES — FOLLOW STRICTLY

0. **REAL-TIME DATA REQUIREMENT — MOST IMPORTANT:**
   - You MUST call run_sql for EVERY user question that requires data from the database.
   - Conversation history is STRICTLY FORBIDDEN as a data source. Even if the exact same question
     was answered in this conversation, you MUST re-execute the SQL query to get fresh data.
   - Conversation history is ONLY for understanding context: resolving pronouns (it, they, that),
     understanding follow-up references, and maintaining conversational coherence.
   - Exceptions where run_sql is NOT required: the user is only asking for an explanation of a
     previous answer, asking about SQL syntax, greeting, or a clarifying back-and-forth that
     does not request new data.

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

5. **Chart specification annotation:**
   End EVERY response with one or more structured chart_spec HTML comments:
   <!-- chart_spec: {"type":"bar|horizontal_bar|line|area|pie|donut|scatter|bubble|radar|heatmap|boxplot|gauge|none","title":"图表标题","xField":"列名","yFields":["列名"],"seriesField":null,"sizeField":null,"valueField":null,"min":null,"max":null,"unit":null} -->

   Core rules:
   - Only generate charts when the data actually supports meaningful visualization.
   - Do NOT force multi-chart output. One well-chosen chart is better than several meaningless ones.
   - You may generate up to 4 charts when the data supports multiple distinct analytical perspectives.
   - Each chart MUST have a clear, non-redundant analytical purpose and a descriptive title.
   - Every chart MUST include a "title" field.
   - Do NOT generate an "id" field; the frontend assigns IDs automatically.
   - For backwards compatibility, you may also include <!-- chart_type: bar|line|pie|none -->, but chart_spec is required.

   Field name rules:
   - ALL field names (xField, yFields, seriesField, sizeField, valueField) MUST be exact column names from the SQL query result.
   - NEVER reference columns that do not exist in the result set.
   - NEVER fabricate data or use made-up field names.

   Type selection rules — choose ONE most suitable type per chart:
   - Category field (many categories or long names) + ONE numeric field → horizontal_bar.
   - Category field (fewer categories, short names) + ONE numeric field → bar.
   - Category field + ONE numeric field, ≤6 categories, represents parts-of-a-whole → pie or donut.
   - Time/order field (date, 1月-12月, Q1-Q4, 2024年第1季度, 2020, etc.) + numeric field → line or area.
   - Category field + MULTIPLE numeric fields (≥2 values per row, few objects) → radar. xField=指标名(类别列), yFields=各数值列名数组.
   - Two category fields + one numeric field → heatmap. xField=横轴类别, yFields=[纵轴类别], valueField=热力值.
   - Category field + numeric field (need distribution) → boxplot. xField=分组列, valueField=数值列.
   - Single numeric value (KPI/gauge) → gauge. valueField=数值列, optional min/max/unit.
   - Two numeric fields → scatter.
   - Three numeric fields → bubble, using the third numeric field as sizeField.
   - Single category + single numeric (classification/ranking ONLY, more than 6 categories) → bar or horizontal_bar; do NOT use pie/donut/radar.
   - Detail/list data with no meaningful aggregation → none.
   - Raw monitoring records with no aggregation (SELECT * without aggregation) → none.
   - If not suitable for a chart, use type "none" and null fields.
   - NEVER output more than one type per chart_spec. The frontend handles candidate type computation.

   Multi-chart rules:
   - Use multiple separate <!-- chart_spec: {...} --> annotations, one per chart.
   - Each chart must answer a different analytical question (e.g. one for distribution, one for trend, one for ranking).
   - Charts must not duplicate each other in type AND data perspective.
   - Example of valid multi-chart: bar chart for regional distribution + pie chart for proportion + line chart for time trend.
"""
        return base + extra

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
AGENT_DATA_DIR = os.getenv("AGENT_DATA_DIR", "E:/3/posgresql/1/agent_data")

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


def create_agent():
    """创建 Agent — 与 train_step3 共享 embedding function 和阈值"""
    print("初始化 LLM 服务 (deepseek-v4-pro via DeepSeek official API)...")
    llm = OpenAILlmService(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

    print("连接 PostgreSQL...")
    pg_runner = PostgresRunner(**DB_KWARGS)

    print("加载 ChromaDB 记忆库 (中文embedding + 0.55阈值)...")
    memory = create_memory()

    print("注册工具 (run_sql)...")
    tool_registry = ToolRegistry()
    file_system = LocalFileSystem(working_directory=AGENT_DATA_DIR)
    raw_run_sql_tool = RunSqlTool(sql_runner=pg_runner, file_system=file_system)
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
        metadata_retriever=DeterministicMetadataRetriever(),
    )

    # 第3层：SQL 示例 context enhancer（L2 approved SQL 示例注入到 system prompt）
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
        config=AgentConfig(stream_responses=True),
        system_prompt_builder=OptimizedSystemPromptBuilder(),
    )

    print("Agent 创建完成!\n")
    return agent


def main():
    agent = create_agent()
    server = VannaFastAPIServer(agent)
    server.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

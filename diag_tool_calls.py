"""诊断脚本 v2：完全复现线上 agent 配置（含 OptimizedSystemPromptBuilder）"""
import asyncio, sys, io, os
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.core.system_prompt.default import DefaultSystemPromptBuilder
from vanna.tools import RunSqlTool, LocalFileSystem
from vanna.core.lifecycle.base import LifecycleHook
from agent_config import DB_KWARGS, create_memory
from vanna import Agent, AgentConfig

# ── 和 step4_server.py 完全一致的配置 ──
class OptimizedSystemPromptBuilder(DefaultSystemPromptBuilder):
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
"""
        return base + extra

class NoopUserResolver(UserResolver):
    async def resolve_user(self, rc):
        return User(id='test', username='test')

# 钩子：逐次记录
class DiagHook(LifecycleHook):
    def __init__(self):
        self.call = 0
    async def before_tool(self, tool, context):
        self.call += 1
        print(f'\n══════ 工具调用 #{self.call}: {tool.name} ══════', flush=True)
    async def after_tool(self, result):
        preview = (result.result_for_llm or '')[:800]
        status = '✅ 成功' if result.success else '❌ 失败'
        print(f'{status}', flush=True)
        if result.error:
            print(f'错误信息: {result.error}', flush=True)
        if preview:
            print(f'\n[返回LLM内容]\n{preview}', flush=True)
        return None

async def main():
    llm = OpenAILlmService(
        model='deepseek-v4-pro',
        api_key=os.environ['OPENCODE_API_KEY'],
        base_url='https://opencode.ai/zen/go/v1',
    )
    pg = PostgresRunner(**DB_KWARGS)
    memory = create_memory()
    tools = ToolRegistry()
    fs = LocalFileSystem(working_directory='E:/3/posgresql/1/agent_data')
    tools.register_local_tool(RunSqlTool(sql_runner=pg, file_system=fs), access_groups=[])

    agent = Agent(
        llm_service=llm, tool_registry=tools, user_resolver=NoopUserResolver(),
        agent_memory=memory,
        llm_context_enhancer=DefaultLlmContextEnhancer(memory),
        config=AgentConfig(stream_responses=True, max_tool_iterations=10),
        system_prompt_builder=OptimizedSystemPromptBuilder(),
        lifecycle_hooks=[DiagHook()],
    )

    print('配置: deepseek-v4-pro + OptimizedSystemPromptBuilder + localhost:5433', flush=True)
    print('问题: 已整治和未整治的排污口各有多少？', flush=True)
    print('=' * 60, flush=True)

    rc = RequestContext(cookies={}, headers={}, remote_addr='127.0.0.1', query_params={}, metadata={})

    text_output = []
    async for component in agent.send_message(rc, '已整治和未整治的排污口各有多少？'):
        if hasattr(component, 'text') and component.text:
            txt = component.text.strip()
            if txt and txt not in text_output:
                text_output.append(txt)
                print(f'\n[文本] {txt[:400]}', flush=True)

    print(f'\n{"="*60}', flush=True)

asyncio.run(main())

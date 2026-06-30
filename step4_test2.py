"""
第4步补充测试: 验证时间过滤 + 出图 + 检索记忆注入
输出到 UTF-8 文件，避免 Windows GBK 终端乱码
"""
import asyncio
import os
import sys
import uuid
import io
from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.tools import RunSqlTool, VisualizeDataTool, LocalFileSystem
from agent_config import DB_KWARGS, create_memory
from vanna import Agent, AgentConfig

OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY")


class SimpleUserResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext):
        return User(id="demo", username="demo")


async def main():
    # 输出文件
    out = io.StringIO()

    def log(msg):
        print(msg)
        out.write(msg + "\n")

    log("=" * 60)
    log("第4步: Vanna Agent 问答验证")
    log("=" * 60)

    # 1. LLM
    log("\n[1/5] 初始化 LLM (deepseek-v4-pro)...")
    llm = OpenAILlmService(
        model="deepseek-v4-pro",
        api_key=OPENCODE_API_KEY,
        base_url="https://opencode.ai/zen/go/v1",
    )

    # 2. PostgreSQL
    log("[2/5] 连接 PostgreSQL...")
    pg_runner = PostgresRunner(**DB_KWARGS)

    # 3. AgentMemory (中文embedding + 0.55阈值)
    log("[3/5] 加载 ChromaDB 记忆库...")
    memory = create_memory()

    # 4. 工具注册
    log("[4/5] 注册工具...")
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

    # 5. Agent
    log("[5/5] 创建 Agent (DefaultLlmContextEnhancer 注入检索记忆)...")
    agent = Agent(
        llm_service=llm,
        tool_registry=tool_registry,
        user_resolver=SimpleUserResolver(),
        agent_memory=memory,
        llm_context_enhancer=DefaultLlmContextEnhancer(memory),
        config=AgentConfig(stream_responses=True),
    )

    request_context = RequestContext(
        cookies={"vanna_email": "demo@test.com"},
        remote_addr="127.0.0.1",
    )

    # === 测试1: 监测数据 + 时间过滤 ===
    q1 = "查询2025年1月的排污口监测数据，显示排污口名称、采样时间、pH、COD，只取前5条有实际pH值的记录。"
    log(f"\n{'=' * 60}")
    log(f"测试1 (时间过滤): {q1}")
    log("=" * 60)

    async for comp in agent.send_message(
        request_context=request_context,
        message=q1,
        conversation_id=str(uuid.uuid4()),
    ):
        txt = _extract_text(comp)
        if txt:
            log(txt)

    # === 测试2: 聚合查询 + 触发图表 ===
    q2 = "统计各区县排污口数量，取前10个区县，用图表展示。"
    log(f"\n{'=' * 60}")
    log(f"测试2 (出图): {q2}")
    log("=" * 60)

    async for comp in agent.send_message(
        request_context=request_context,
        message=q2,
        conversation_id=str(uuid.uuid4()),
    ):
        txt = _extract_text(comp)
        if txt:
            log(txt)

    log(f"\n{'=' * 60}")
    log("第4步验证完成!")
    log("=" * 60)

    # 写入文件
    output_path = "E:/3/posgresql/1/step4_output.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(out.getvalue())
    print(f"\n输出已保存到: {output_path}")


def _extract_text(component) -> str | None:
    """从各类 UI 组件中提取文本"""
    if hasattr(component, "simple_component") and component.simple_component:
        sc = component.simple_component
        if hasattr(sc, "text") and sc.text:
            return f"[TEXT] {sc.text}"
    elif hasattr(component, "rich_component") and component.rich_component:
        rc = component.rich_component
        rc_type = type(rc).__name__
        if rc_type == "DataFrameComponent" and hasattr(rc, "dataframe"):
            return f"[TABLE {len(rc.dataframe)} rows]\n{rc.dataframe.to_string()}"
        elif hasattr(rc, "content") and rc.content:
            return f"[{rc_type}] {rc.content[:500]}"
    elif hasattr(component, "content") and component.content:
        return f"[CONTENT] {component.content[:500]}"
    # 跳过 StatusBarUpdate, TaskTrackerUpdate 等非内容组件
    return None


if __name__ == "__main__":
    asyncio.run(main())

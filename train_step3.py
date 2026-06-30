"""
第3步：提取6张白名单表的DDL(含中文注释) → 存入 ChromaDB AgentMemory
"""
import asyncio
import uuid
from vanna.integrations.postgres.sql_runner import PostgresRunner
from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import ToolContext
from vanna.core.user import User
from vanna.core.registry import ToolRegistry
from agent_config import DB_KWARGS, create_memory

# === 配置 ===
TABLE_WHITELIST = [
    "rs_outlet", "rs_outlet_info_v2", "rs_outlet_monitor_v2",
    "rs_outlet_live_v2", "rs_outlet_trace_v2", "rs_outlet_remediation_v2",
]


async def main():
    # 初始化
    runner = PostgresRunner(**DB_KWARGS)
    memory = create_memory()
    user = User(id="trainer", username="trainer")
    registry = ToolRegistry()

    def make_context():
        return ToolContext(
            user=user,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            agent_memory=memory,
            tool_registry=registry,
        )

    # 1. 查字段信息(含 udt_name, 用于排除 geometry)
    cols_sql = """
        SELECT table_name, column_name, data_type, udt_name, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ({})
        ORDER BY table_name, ordinal_position
    """.format(", ".join(f"'{t}'" for t in TABLE_WHITELIST))

    ctx = make_context()
    df_cols = await runner.run_sql(RunSqlToolArgs(sql=cols_sql), ctx)

    # 2. 查中文注释
    comments_sql = """
        SELECT c.relname AS table_name,
               a.attname AS column_name,
               col_description(a.attrelid, a.attnum) AS comment
        FROM pg_class c
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE c.relname IN ({})
          AND a.attnum > 0
          AND col_description(a.attrelid, a.attnum) IS NOT NULL
    """.format(", ".join(f"'{t}'" for t in TABLE_WHITELIST))

    df_comments = await runner.run_sql(RunSqlToolArgs(sql=comments_sql), ctx)

    # 构建 {table_name: {col_name: comment}} 索引
    comment_map = {}
    for _, r in df_comments.iterrows():
        comment_map.setdefault(r["table_name"], {})[r["column_name"]] = r["comment"]

    print("=" * 60)
    print("字段注释提取结果:")
    for tbl, cols in comment_map.items():
        print(f"\n  [{tbl}]")
        for c, cmt in cols.items():
            print(f"    {c}  -- {cmt}")
    if not comment_map:
        print("  (无注释 — 这些表没有 COMMENT)")
    print()

    # 3. 按表拼接 DDL
    exported_tables = []
    geometry_cols_found = []

    for table_name in TABLE_WHITELIST:
        rows = df_cols[df_cols["table_name"] == table_name]
        if rows.empty:
            print(f"  [警告] {table_name}: 未找到任何列")
            continue

        col_lines = []
        for _, r in rows.iterrows():
            # 排除 geometry 列
            if r["udt_name"] == "geometry":
                geometry_cols_found.append(f"{table_name}.{r['column_name']}")
                continue

            col_def = f"  {r['column_name']} {r['data_type'].upper()}"
            cmt = comment_map.get(table_name, {}).get(r["column_name"])
            if cmt:
                col_def += f"  -- {cmt}"
            col_lines.append(col_def)

        ddl = f'CREATE TABLE "{table_name}" (\n' + ",\n".join(col_lines) + "\n);"
        exported_tables.append((table_name, ddl))

    # 4. 打印 DDL
    print("=" * 60)
    print(f"提取 {len(exported_tables)}/{len(TABLE_WHITELIST)} 张表的 DDL:\n")
    for tbl, ddl in exported_tables:
        print(f"--- {tbl} ---")
        print(ddl)
        print()

    if geometry_cols_found:
        print(f"已排除 geometry 列: {geometry_cols_found}")
    else:
        print("未发现 geometry 列 (这6张试点表不带PostGIS几何字段)")
    print()

    # 5. 存入 ChromaDB
    print("=" * 60)
    print("正在存入 ChromaDB (首次可能下载 embedding 模型，请耐心等待)...\n")

    for tbl, ddl in exported_tables:
        ctx = make_context()
        result = await memory.save_text_memory(content=ddl, context=ctx)
        print(f"  [{tbl}] 已存入, memory_id={result.memory_id}")

    # 6. 可选: 存入示例问答
    examples = [
        ("点军区有哪些排污口?",
         "SELECT outlet_name, outlet_address, area_name "
         "FROM rs_outlet WHERE area_name LIKE '%点军%' OR county_name LIKE '%点军%'"),
        ("查看排污口的监测数据",
         "SELECT outlet_name, sampling_time, ph, cod, bod, ammonia_nitrogen, total_phosphorus "
         "FROM rs_outlet_monitor_v2 m JOIN rs_outlet_info_v2 i ON m.outlet_id = i.id "
         "ORDER BY sampling_time DESC"),
    ]
    print()
    print("存入示例问答:")
    for q, sql in examples:
        content = f"问题: {q}\nSQL: {sql}"
        ctx = make_context()
        result = await memory.save_text_memory(content=content, context=ctx)
        print(f"  Q: {q}")
        print(f"  SQL: {sql}")
        print(f"  memory_id={result.memory_id}")
        print()

    # 7. 验证读取 (ChineseChromaAgentMemory 默认阈值 0.55)
    print("=" * 60)
    print("验证: search_text_memories('排污口', 默认阈值)...")
    ctx = make_context()
    results = await memory.search_text_memories(query="排污口", context=ctx, limit=5)
    print(f"命中 {len(results)} 条:")
    for r in results:
        preview = r.memory.content[:150].replace("\n", " ")
        print(f"  [{r.memory.memory_id[:12]}...] score={r.similarity_score:.3f} | {preview}...")

    if len(results) == 0:
        print("  [FAIL] 检索链路不通! DefaultLlmContextEnhancer 拿不到记忆")
    else:
        print(f"  [PASS] 检索链路正常, DefaultLlmContextEnhancer 可检索到 {len(results)} 条记忆")

    print()
    print("=" * 60)
    print("第3步完成!")


if __name__ == "__main__":
    asyncio.run(main())

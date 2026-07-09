# P0 元数据检索层接入主问答流程设计报告

## 结论

本阶段只做接入点定位、dry-run 包装和验证报告，未实际接入主问答流程。

建议后续真正接入时，把 `DeterministicMetadataRetriever` 包装为一个新的 LLM 上下文增强器，在 SQL 工具调用前、LLM 首次生成工具调用前，将确定性候选表和字段注入 system prompt。这样可以让确定性元数据检索先于 ChromaDB 相似召回发挥约束作用，同时不改变 API 路由、前端协议和 `run_sql` 工具契约。

## 1. 主问答入口文件路径

- 项目后端启动入口：`step4_server.py`
- FastAPI 服务创建位置：`step4_server.py:145-148`
- Vanna FastAPI 路由实现：`vanna_src/src/vanna/servers/fastapi/routes.py:40-80`
- 前端请求入口：`frontend/src/hooks/useSSE.ts` 调用 `/api/vanna/v2/chat_sse`

请求链路：

```text
frontend /api/vanna/v2/chat_sse
  -> vanna_src/src/vanna/servers/fastapi/routes.py chat_sse()
  -> vanna_src/src/vanna/servers/base/chat_handler.py handle_stream()
  -> Agent.send_message()
  -> Agent._send_message()
  -> build system prompt + llm_context_enhancer
  -> LLM 选择 run_sql 工具
  -> ToolRegistry.execute()
  -> RunSqlTool.execute()
```

## 2. Vanna SQL 生成调用位置

当前项目使用 Vanna 2.0 Agent-based API，没有发现正式后端中存在 `vn.ask()`、`vn.generate_sql()`、`generate_sql()` 或旧版 Vanna 0.x 风格的直接调用。

实际 SQL 生成不是一个显式的 `generate_sql` 函数调用，而是：

- `step4_server.py:123-129` 注册 `RunSqlTool`
- `step4_server.py:131-139` 创建 `Agent`
- `vanna_src/src/vanna/core/agent/agent.py:599-616` 构建并增强 system prompt
- `vanna_src/src/vanna/core/agent/agent.py:638-655` 构建 LLM 请求并调用 LLM
- `vanna_src/src/vanna/core/agent/agent.py:657-665` 处理 LLM 返回的 tool call
- `vanna_src/src/vanna/core/agent/agent.py:827` 通过 `tool_registry.execute()` 执行工具
- `vanna_src/src/vanna/tools/run_sql.py:56-60` 中 `RunSqlTool.execute()` 调用 `sql_runner.run_sql()`

因此，SQL 生成调用位置应理解为 Agent 生成 `run_sql` tool call 的 LLM 请求阶段，而不是旧版 `generate_sql()`。

## 3. 建议插入 DeterministicMetadataRetriever 的位置

建议插入点：

- `step4_server.py:136` 当前传入 `DefaultLlmContextEnhancer(memory)`
- 后续可替换为项目自定义组合增强器，例如 `DeterministicMetadataContextEnhancer(DefaultLlmContextEnhancer(memory), DeterministicMetadataRetriever())`

理由：

- `LlmContextEnhancer.enhance_system_prompt()` 会在 `vanna_src/src/vanna/core/agent/agent.py:614-616` 被调用。
- 该调用发生在第一次 LLM 请求之前，也就是 LLM 生成 `run_sql` tool call 之前。
- 该层可以只增强上下文，不改 API 路由、前端、数据库连接或 `RunSqlTool`。

不建议把 P0 接入 `RunSqlTool.execute()`：

- 到 `RunSqlTool` 时 SQL 已经由 LLM 生成，太晚。
- 在工具层拦截会改变运行时行为和错误面，不适合作为 P0 候选表约束的首选接入点。

## 4. 如何把候选表/字段作为上下文传给 Vanna

后续真正接入时，可在 `enhance_system_prompt(system_prompt, user_message, user)` 中：

1. 使用 `DeterministicMetadataRetriever.retrieve(user_message, top_n=10)` 取候选表。
2. 从候选结果中提取：
   - `table_name`
   - `score`
   - `matched_by`
   - `matched_columns`
   - `table_comment`
   - `conflict_family`
   - `risk_level`
   - `reason`
3. 追加一个明确章节到 system prompt，例如：

```text
## Deterministic Metadata Constraints

The following table and column candidates are produced by deterministic metadata matching.
They have higher priority than vector similarity results when deciding which tables to use.

User question: ...

Candidate tables, ordered by priority:
1. wm_waterquality_day_records ...
2. wm_waterquality_hour_records ...

Candidate columns:
- wm_waterquality_day_records.station_id ...

Rules:
- Prefer the candidate tables in the listed order.
- If ChromaDB memory suggests a similar but lower-priority table, do not override the deterministic top candidates without clear user evidence.
- Do not query schema tables to rediscover metadata.
```

本阶段 probe 中的 `suggested_context_for_vanna` 即为这个上下文片段的 dry-run 版本。

## 5. 如何避免候选表被 Chroma top-1 覆盖

建议采用三层保护：

1. 顺序保护：确定性元数据章节放在 `Relevant Context from Memory` 之前，或者在最终 system prompt 中明确写入优先级高于 Chroma 相似召回。
2. 文案保护：明确提示 “deterministic candidates have higher priority than vector similarity results”。
3. 结构保护：候选表按评分排序输出，并标注 `risk_level`、`conflict_family`、`matched_columns`，让 LLM 能区分基础表、记录表、阈值表、溯源表等相似表。

如果后续需要更强约束，可进一步把 P0 候选表传入 `RequestContext.metadata` 或 Agent 内部 `ToolContext.metadata`，但这会更接近运行时行为修改，应放到后续阶段评估。

## 6. 后续真正接入时需要改哪些文件

建议新增或修改：

- 新增：`tools` 之外的后端模块，例如 `metadata_context_enhancer.py` 或项目约定的后端 agent 目录。
- 修改：`step4_server.py`
  - import `DeterministicMetadataRetriever`
  - import 新增的上下文增强器
  - 将 `llm_context_enhancer=DefaultLlmContextEnhancer(memory)` 替换为组合增强器

不需要修改：

- API 路由路径
- 前端 SSE/WebSocket 协议
- `RunSqlTool`
- 数据库连接
- Vanna 训练代码
- `agent_data/column_metadata_index.json`
- `vanna_data/chroma/`

## 7. 本阶段是否实际接入

否。

本阶段只新增 dry-run probe 和报告：

- `tools/metadata_retriever_integration_probe.py`
- `tools/metadata_retriever_integration_probe_result.md`
- `tools/metadata_retriever_integration_plan.md`

本阶段未调用 Vanna、未执行 SQL、未连接 PostgreSQL、未训练 Vanna、未修改 ChromaDB、未进入第 2/3/4 级。

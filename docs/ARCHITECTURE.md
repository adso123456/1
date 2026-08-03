# 项目架构

## 总览

本项目是基于 Vanna 2.0 Agent API 的多数据源中文问数系统。React 前端通过 FastAPI 的 SSE 接口发起会话；后端按会话绑定的数据源加载独立运行时，生成并校验只读 SQL，查询真实数据库，再以进度、DataFrame、文本和图表事件返回结果。

```text
主工作台 / 嵌入式 Widget
          │
          ▼
FastAPI（step4_server.py）
          │
          ├─ 会话与路由：source_id 强绑定、报表/普通问数分流
          ├─ DataSourceCatalog：生命周期、scope、revision
          ├─ DataSourceRuntimeManager：缓存、构建锁、revision 热切换
          ├─ RuntimePrewarmer：启动时顺序预热 ready + enabled 数据源
          │
          ▼
Vanna Agent（backend/agent_assembly.py）
          ├─ TracingOpenAILlmService：DeepSeek、超时、流式与耗时追踪
          ├─ Metadata / Memory / SQL 示例上下文
          ├─ GuardedRunSqlTool：SQLGuard + schema 保持
          └─ PostgreSQL / MySQL Runner
```

## 核心模块

| 模块 | 职责 |
| --- | --- |
| `step4_server.py` | 服务入口、lifespan、SSE、管理、报表、问题建议和 Embed 路由装配 |
| `backend/data_source_catalog.py` | 数据源注册、发现结果、选定范围、状态和 revision 持久化 |
| `backend/data_source_runtime_manager.py` | 按数据源构建、缓存和切换运行时 |
| `backend/runtime_prewarmer.py` | 启动阶段顺序预热并记录状态 |
| `backend/agent_assembly.py` | PostgreSQL/MySQL 共用 Agent、工具、上下文和性能配置 |
| `backend/postgresql_runtime_factory.py` | PostgreSQL 连接与运行时装配 |
| `backend/mysql_runtime_factory.py` | MySQL 连接与运行时装配 |
| `backend/sql_guard.py` | 方言感知的只读 SQL 安全校验 |
| `backend/tracing_llm_service.py` | LLM 调用、流式输出、计时和诊断 |
| `frontend/src/` | 主工作台、数据源、仪表板、小助手和 Widget 界面 |

## 数据源生命周期

发现只读取物理结构；业务语义保存在正式 Metadata/Memory 中。发布后，Catalog 的 selected scope、正式资产和 runtime revision 共同决定数据源是否可问数。运行时始终按当前 revision 加载，不能把一次发现结果当作发布资产覆盖。

## 问数链路

1. 会话创建时绑定 `source_id`，后续请求不得切换来源。
2. 普通数据库问数默认首轮要求调用 `run_sql`；明确的问候、感谢或纯解释请求可以豁免。
3. SQLGuard 在执行前校验只读、表范围、方言和危险结构。
4. 成功查询产生新的 DataFrame；需要自然语言总结时按文本增量流式输出。
5. 前端消费向后兼容的 SSE 事件，取消请求后清理流式占位状态。

## 报表与 Widget

- 报表路由先返回配置项，用户确认后查询数据库、生成预览和 PDF；普通问题仍进入通用 Agent。
- Widget 不持有 Secret 或 Token。业务宿主页 Loader 以公开 `app_id` 向 Embed API 请求，并通过严格校验的 `postMessage` RPC 与 iframe 通信。
- Embed CORS 按路径中的应用和真实浏览器 Origin 动态授权；关联网站只用于管理入口，不参与 Origin、数据源或 Widget 授权。

## 资产边界

- 源码：Git 跟踪的 Python、TypeScript、配置模板、测试和文档。
- 正式运行资产：Catalog、系统数据库、Metadata、Memory、Chroma、凭据和报表产物。
- 运行资产不应由普通代码清理、测试或构建命令改写；隔离验收必须使用副本。

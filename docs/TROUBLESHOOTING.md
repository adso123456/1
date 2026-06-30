# TROUBLESHOOTING.md — 排障记录

## 项目特定 Debug 要点

- 排查问答质量问题时，优先检查：检索是否命中、中文 embedding 是否一致、system prompt 是否约束 SQL、SQL 是否排除了 `geom`。
- 排查数据库问题时，先确认 Docker 容器 `local-timescale` 是否运行，以及 `127.0.0.1:5433` 是否可连。
- 排查前端问题时，先确认后端 `/health`，再确认 Vite proxy 到 `http://localhost:8000`。
- 排查 Vanna 相关问题时，注意本项目使用 Vanna 2.0 Agent-based API，不照搬 Vanna 0.x 或错误 quickstart import。

## 关键坑（血泪教训）

### 1. 中文 embedding（最重要）

ChromaDB 默认 embedding 模型 `all-MiniLM-L6-v2` 是纯英文，对中文检索完全失效——中文文本相似度被压到 0，全被默认阈值 0.7 过滤，检索返回 0 条。

修复：
- 改用中文模型 `BAAI/bge-small-zh-v1.5`
- 阈值从 0.7 降到 0.55
- **存入端和检索端必须用同一个 embedding function**（集中在 `agent_config.py` 的 `create_memory()`）

### 2. 时序表不自动加时间过滤

查询监测数据（如 `rs_outlet_monitor_v2`）时，Agent 不会自动加时间范围过滤。建议后续在 system prompt 加规则：查询监测类数据默认加时间过滤或 `ORDER BY 时间 DESC LIMIT`。

### 3. geometry 列要排除

PostGIS 的 `geom` 列不能进 DDL（会污染上下文、塞爆响应）。提取 schema 时按 `udt_name = 'geometry'` 排除。

### 4. 中文注释是准确率关键

提取表结构时务必带上 PostgreSQL 字段中文注释（`col_description()`），否则模型看不懂 `area_code` 这类字段的含义。

### 5. Vanna 用 psycopg2 不是 asyncpg

PostgresRunner 底层是 psycopg2 同步驱动，不是 asyncpg。SQL 执行是同步的。

### 6. Vanna quickstart import

Vanna 官方 quickstart 文档写的 `from vanna.integrations.anthropic import OpenAILlmService` 是错的，别照抄。本项目使用：

```python
from vanna.integrations.openai import OpenAILlmService
```

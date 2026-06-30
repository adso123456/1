# ARCHITECTURE.md — 项目架构

## 项目概述

基于 Vanna 2.0（MIT 协议）搭建的中文水利数据智能问答系统。用户用中文自然语言提问，Agent 自动查询本地 PostgreSQL，返回表格数据+文字说明，前端根据数据生成 ECharts 图表。当前【最小验证】已通过：6 张排污口表问答闭环全部跑通，能正确生成 SQL、查到真实数据、出图表。

## 技术栈

| 配置项 | 值 | 位置 |
|--------|-----|------|
| Python | 3.12，虚拟环境 `vanna_venv/` | 项目根目录 |
| LLM | deepseek-v4-pro | `agent_config.py` → `step4_server.py` |
| LLM 网关 | `https://opencode.ai/zen/go/v1`（OpenAI 兼容） | step4 脚本 |
| 数据库 | PostgreSQL 13 + TimescaleDB + PostGIS | Docker `local-timescale` |
| 向量库 | ChromaDB，本地持久化 `vanna_data/` | `agent_config.py` |
| Embedding | `BAAI/bge-small-zh-v1.5`（中文） | `agent_config.py` |
| Web 服务 | FastAPI `localhost:8000`，React 前端 `localhost:5173` | `step4_server.py`、`frontend/` |
| 主前端 | React + ECharts | `frontend/` |
| 备用内置页面 | FastAPI 根路径保留 `<vanna-chat>` | `VannaFastAPIServer` |

## 请求链路

```
React 前端 (localhost:5173)
  │  POST /api/vanna/v2/chat_sse
  ▼
Vite proxy (vite.config.ts) → localhost:8000
  │
  ▼
FastAPI (step4_server.py → VannaFastAPIServer → uvicorn)
  │  /api/vanna/v2/chat_sse → StreamingResponse (SSE)
  │  /health
  │  /api/vanna/v2/chat_websocket (备用)
  ▼
Agent → LLM (deepseek-v4-pro) + PostgreSQL (psycopg2) + ChromaDB
  │
  ▼
SSE 返回 dataframe + text（text 内含 chart_type 标记）
  │
  ▼
React 根据 dataframe + chart_type 生成 ECharts option 并渲染
```

## 核心架构（Vanna 2.0 Agent-based，不是 0.x）

```
用户中文提问 → FastAPI (step4_server.py)
                    ↓
              Agent (Vanna 2.0)
              ├── OpenAILlmService        ← deepseek-v4-pro via opencode.ai
              ├── PostgresRunner           ← psycopg2 同步查询 PostgreSQL
              ├── ChromaAgentMemory        ← 存储 DDL/文档/示例问答
              ├── DefaultLlmContextEnhancer ← 每次对话自动检索相关记忆注入 LLM
              ├── RunSqlTool               ← 执行 SQL，返回 DataFrameComponent
              └── VisualizeDataTool        ← 已注册，可生成 Plotly ChartComponent（当前 React 前端不消费 Plotly 配置）
```

## 当前主链路与职责

- 后端负责 LLM、SQL 查询、SSE 输出；`RunSqlTool` 输出原始表格数据（`dataframe`），最终文本携带 `chart_type` 标记。
- React 前端负责读取 SSE 中的 `dataframe` 和 `text`，根据表格数据和 `chart_type` 自行生成 ECharts option。
- FastAPI 根路径仍保留 Vanna 内置 `<vanna-chat>` 页面，仅作为备用入口。
- `VisualizeDataTool` 仍在后端注册，可生成 Plotly `ChartComponent`，但当前 React 前端不消费 Plotly 配置。

## 关键模块

| 脚本 | 用途 | 什么时候跑 |
|------|------|-----------|
| `agent_config.py` | 共享配置：DB 连接、ChromaDB 实例（中文 embedding + 0.55 阈值） | 不直接跑，被其他脚本 import |
| `train_step3.py` | 从白名单表提取 DDL（含中文注释）→ 存入 ChromaDB，含示例问答 | 表结构变了、加了新表、或重建向量库时跑 |
| `step4_agent.py` | CLI 模式问答测试，验证 LLM+SQL+检索+出图全链路 | 快速验证改动、不想启 Web 服务时跑 |
| `step4_server.py` | 启动 FastAPI 服务，提供 SSE API，并在根路径保留 `<vanna-chat>` 备用内置页面 | 日常使用、演示、需要后端接口时跑 |
| `frontend/` | React + ECharts 主前端，通过 `/api` 代理访问后端 SSE | 当前主浏览器入口 |
| `step4_test2.py` | CLI 问答验证（时间过滤+出图），输出保存到 UTF-8 文件 | 终端乱码时用，输出到文件再查看 |

## 关键 import 路径

```python
from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.tools import RunSqlTool, VisualizeDataTool, LocalFileSystem
from vanna import Agent, AgentConfig
```

> ⚠️ Vanna 官方 quickstart 文档写的 `from vanna.integrations.anthropic import OpenAILlmService` 是错的，别照抄。

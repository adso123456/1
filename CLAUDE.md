# CLAUDE.md — 水利数据智能问答 Agent

## 项目概述

基于 Vanna 2.0（MIT 协议）搭建的中文水利数据智能问答系统。用户用中文自然语言提问，Agent 自动查询本地 PostgreSQL，返回图表+文字。当前【最小验证】已通过：6 张排污口表问答闭环全部跑通，能正确生成 SQL、查到真实数据、出 Plotly 图表。

## 快速重启清单

前提：PostgreSQL Docker 容器 `local-timescale` 必须已在运行（127.0.0.1:5433）。

### 启动后端（FastAPI :8000）

```powershell
# 1. 激活虚拟环境
E:\3\posgresql\1\vanna_venv\Scripts\Activate.ps1

# 如果报"禁止运行脚本"，先执行一次（仅首次需要）：
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 2. 设置 LLM API Key
$env:OPENCODE_API_KEY = "sk-你的key"

# 3. 启动 FastAPI 服务（内部用 uvicorn 驱动）
python step4_server.py
# → 输出 Your app is running at: http://localhost:8000
```

**其他终端：**

| 终端 | 激活命令 |
|------|---------|
| CMD | `E:\3\posgresql\1\vanna_venv\Scripts\activate.bat` |
| Git Bash | `source E:/3/posgresql/1/vanna_venv/Scripts/activate` |

**跳过激活的快捷方式**（不激活虚拟环境，直接用绝对路径跑）：

```powershell
E:\3\posgresql\1\vanna_venv\Scripts\python.exe step4_server.py
```

### 启动前端（Vite :5173）

```powershell
# 另开终端
cd E:\3\posgresql\1\frontend
npm run dev
# → http://localhost:5173
```

### 访问

浏览器打开 `http://localhost:5173`

### 请求链路

```
浏览器 (localhost:5173)
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

## 环境配置

| 配置项 | 值 | 位置 |
|--------|-----|------|
| Python | 3.12，虚拟环境 `vanna_venv/` | 项目根目录 |
| LLM | deepseek-v4-pro | `agent_config.py` → `step4_server.py` |
| LLM 网关 | `https://opencode.ai/zen/go/v1`（OpenAI 兼容） | step4 脚本 |
| LLM Key | 环境变量 `OPENCODE_API_KEY` | 系统/用户级环境变量 |
| 数据库 | PostgreSQL 13 + TimescaleDB + PostGIS | Docker `local-timescale` |
| 连接串 | `postgresql://postgres:test123456@localhost:5433/gt_monitor` | `agent_config.py` |
| 向量库 | ChromaDB，本地持久化 `vanna_data/` | `agent_config.py` |
| Embedding | `BAAI/bge-small-zh-v1.5`（中文） | `agent_config.py` |
| 检索阈值 | 0.55 | `agent_config.py : ChineseChromaAgentMemory` |
| Web 服务 | FastAPI `localhost:8000`，前端 `<vanna-chat>` | `step4_server.py` |

## 关键脚本说明

| 脚本 | 用途 | 什么时候跑 |
|------|------|-----------|
| `agent_config.py` | 共享配置：DB 连接、ChromaDB 实例（中文 embedding + 0.55 阈值） | 不直接跑，被其他脚本 import |
| `train_step3.py` | 从白名单表提取 DDL（含中文注释）→ 存入 ChromaDB，含示例问答 | 表结构变了、加了新表、或重建向量库时跑 |
| `step4_agent.py` | CLI 模式问答测试，验证 LLM+SQL+检索+出图全链路 | 快速验证改动、不想启 Web 服务时跑 |
| `step4_server.py` | 启动 FastAPI + `<vanna-chat>` Web 界面的完整服务 | 日常使用、演示、需要浏览器交互时跑 |
| `step4_test2.py` | CLI 问答验证（时间过滤+出图），输出保存到 UTF-8 文件 | 终端乱码时用，输出到文件再查看 |

## 核心架构（Vanna 2.0 Agent-based，不是 0.x）

```
用户中文提问 → FastAPI (step4_server.py)
                    ↓
              Agent (Vanna 2.0)
              ├── OpenAILlmService        ← deepseek-v4-pro via opencode.ai
              ├── PostgresRunner           ← psycopg2 同步查询 PostgreSQL
              ├── ChromaAgentMemory        ← 存储 DDL/文档/示例问答
              ├── DefaultLlmContextEnhancer ← 每次对话自动检索相关记忆注入 LLM
              ├── RunSqlTool               ← 执行 SQL
              └── VisualizeDataTool        ← 生成 Plotly 图表
```

### 关键 import 路径

```python
from vanna.integrations.openai import OpenAILlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory
from vanna.core.enhancer.default import DefaultLlmContextEnhancer
from vanna.tools import RunSqlTool, VisualizeDataTool, LocalFileSystem
from vanna import Agent, AgentConfig
```

> ⚠️ Vanna 官方 quickstart 文档写的 `from vanna.integrations.anthropic import OpenAILlmService` 是错的，别照抄。

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

## 当前数据范围

- 数据库共约 107 张表，白名单试点 6 张排污口表：
  - `rs_outlet` / `rs_outlet_info_v2` / `rs_outlet_monitor_v2`
  - `rs_outlet_live_v2` / `rs_outlet_trace_v2` / `rs_outlet_remediation_v2`
- 向量库存有：6 张表的 DDL + 中文注释 + 2 条示例问答

## 当前状态与待定决策

| 项目 | 状态 |
|------|------|
| 6 张表试点验证 | ✅ 通过（SQL 生成 + 数据查询 + 出图全链路） |
| 前端 | ✅ React + ECharts（`frontend/`，Vite + TypeScript），已替换 Vanna 自带 `<vanna-chat>` |
| System Prompt 优化 | ✅ 禁止 schema 探查、禁止 SELECT geom、1-2 条 SQL 上限 |
| 白名单放开到全库 | 后续扩展 |
| 补充更多示例问答 | 后续扩展 |

### 前端启动

```powershell
cd E:\3\posgresql\1\frontend
npm run dev
# 访问 http://localhost:5173
```

Vite 已配置 proxy：`/api` → `http://localhost:8000`，需先启动后端 `step4_server.py`。

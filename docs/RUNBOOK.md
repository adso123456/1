# RUNBOOK.md — 运行手册

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
| Web 服务 | FastAPI `localhost:8000`，React 前端 `localhost:5173` | `step4_server.py`、`frontend/` |
| 主前端 | React + ECharts | `frontend/` |
| 备用内置页面 | FastAPI 根路径保留 `<vanna-chat>` | `VannaFastAPIServer` |

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

### 访问主前端

浏览器打开 `http://localhost:5173`

### 访问备用内置页面

浏览器打开 `http://localhost:8000` 可访问 FastAPI 根路径保留的 Vanna 内置 `<vanna-chat>` 页面。当前主入口仍是 `frontend/` 下的 React + ECharts 前端。

## 验证方式

- 后端启动后，确认输出 `Your app is running at: http://localhost:8000`。
- 前端启动后，访问 `http://localhost:5173`。
- Vite 已配置 proxy：`/api` → `http://localhost:8000`，需先启动后端 `step4_server.py`。
- 当前 React 前端通过 SSE 接收 `dataframe` 和 `text`，根据 `chart_type` 标记自行生成 ECharts 图表。

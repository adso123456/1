# 运行手册

## 运行前检查

- Python 3.12 虚拟环境已安装 `requirements.txt`。
- 前端依赖已在 `frontend/` 安装。
- PostgreSQL、MySQL 等被启用的数据源可以从本机访问。
- 从 `.env.example` 复制所需变量到本机环境；只填写环境变量，不要把真实密钥提交到 Git。
- LLM 使用 `DEEPSEEK_API_KEY`，默认模型和服务地址由后端配置读取。
- 正式运行前确认 `DATA_SOURCE_CATALOG_PATH`、`WATER_AGENT_SYSTEM_DB_PATH`、Metadata、Memory 和 Chroma 路径指向预期资产。

## 启动后端（默认 8000）

在项目根目录执行：

```powershell
E:\3\posgresql\1\vanna_venv\Scripts\python.exe step4_server.py
```

需要隔离端口时，仅为当前终端设置端口：

```powershell
$env:VANNA_SERVER_PORT = "18000"
E:\3\posgresql\1\vanna_venv\Scripts\python.exe step4_server.py
```

启动过程会顺序预热 Catalog 中 `ready + enabled_for_chat` 的数据源。单个数据源预热失败不会阻止其他数据源预热，失败原因应在日志和预热状态接口中查看。

## 启动前端（默认 5173）

另开终端：

```powershell
Set-Location E:\3\posgresql\1\frontend
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。默认 Vite 代理把 `/api` 转发到 `http://localhost:8000`；隔离验收时应使用对应的前端代理配置和独立端口，不占用正式服务。

## 启动后验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/runtime-prewarm-status
```

随后在前端依次确认：

1. 新建会话能看到所有 `ready + enabled` 数据源。
2. PostgreSQL 和 MySQL 各完成一次普通查询，能收到进度、DataFrame 和最终文本。
3. 日报或月报先显示配置面板，确认后才生成。
4. 小助手管理页能读取应用，关联网站和 Widget 能按授权来源访问。

## 停止与重启

- 在对应终端使用 `Ctrl+C` 停止服务。
- 重启前先确认端口没有遗留进程，再启动新实例。
- 不要通过删除 Catalog、系统数据库、Metadata 或 Chroma 来解决启动问题。

## 变更正式资产

- 数据源变更使用管理 API/CLI 的发现、准备、发布和启停流程。
- 小助手应用使用管理 API/CLI 创建或编辑。
- 禁止直接编辑 SQLite、复制正在使用的 Chroma 目录或手工修改 runtime revision。
- 调试和验收优先使用 Catalog、系统数据库与运行资产的副本。

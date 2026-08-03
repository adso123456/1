# 运行手册

## 目录与资产边界

正式运行必须把代码、Python 环境和运行资产视为三个独立位置：

| 占位符 | 含义 | 要求 |
| --- | --- | --- |
| `<APP_ROOT>` | 已批准版本的干净代码运行目录 | 必须是独立部署目录，HEAD 等于部署批准 SHA，且没有未提交修改 |
| `<PYTHON_ENV>` | Python 3.12 虚拟环境目录 | 已安装与该版本匹配的 `requirements.txt` |
| `<ASSET_ROOT>` | Catalog、系统数据库、Metadata、Memory、Chroma 和报表等正式资产根目录 | 可以位于代码目录之外，通过环境变量显式引用 |
| `<FRONTEND_ROOT>` | 当前部署代码中的前端目录 | 通常为 `<APP_ROOT>\frontend` |

不要把开发工作树路径当作正式部署路径。尤其不能从存在未提交修改、HEAD 落后或混有其他分支改动的工作树启动正式服务。

## 启动前版本门禁

在 `<APP_ROOT>` 执行以下检查，并把 `<APPROVED_SHA>` 替换为本次部署批准的完整 SHA：

```powershell
$AppRoot = "<APP_ROOT>"
$ApprovedSha = "<APPROVED_SHA>"
Set-Location $AppRoot

$ActualSha = (git rev-parse HEAD).Trim()
if ($ActualSha -ne $ApprovedSha) {
    throw "代码版本不符合部署批准 SHA：actual=$ActualSha expected=$ApprovedSha"
}

$DirtyFiles = git status --porcelain
if ($DirtyFiles) {
    throw "代码运行目录存在未提交修改，禁止启动正式服务"
}
```

每次启动和重启都必须记录 `git rev-parse HEAD` 的结果。文档中的示例基线不能代替当次部署批准 SHA。

## 环境配置

- 从 `<APP_ROOT>\.env.example` 了解变量名称，只在本机环境或受控凭据系统填写真实值。
- LLM 密钥使用 `DEEPSEEK_API_KEY`，不得写入代码、文档、日志或 Git。
- 使用 `DATA_SOURCE_CATALOG_PATH` 和 `WATER_AGENT_SYSTEM_DB_PATH` 指向正式 Catalog 与系统数据库。
- 使用 `VANNA_DATA_DIR`、`MYSQL_VANNA_DATA_DIR`、`MYSQL_METADATA_INDEX_PATH`、`AGENT_DATA_DIR`、`WATER_REPORT_OUTPUT_DIR` 等变量引用 `<ASSET_ROOT>` 下的对应资产。
- 正式资产不要求位于 `<APP_ROOT>`；部署代码更新不得复制、覆盖或删除这些资产。

## 启动后端（默认 8000）

```powershell
$AppRoot = "<APP_ROOT>"
$Python = "<PYTHON_ENV>\Scripts\python.exe"
Set-Location $AppRoot
& $Python step4_server.py
```

需要隔离端口时，仅为当前终端设置端口：

```powershell
$env:VANNA_SERVER_PORT = "18000"
$AppRoot = "<APP_ROOT>"
$Python = "<PYTHON_ENV>\Scripts\python.exe"
Set-Location $AppRoot
& $Python step4_server.py
```

启动过程会顺序预热 Catalog 中 `ready + enabled_for_chat` 的数据源。单个数据源预热失败不会阻止其他数据源，失败原因应从日志和预热状态接口确认。

## 启动前端（默认 5173）

另开终端，并从同一批准版本的前端目录启动：

```powershell
$FrontendRoot = "<APP_ROOT>\frontend"
Set-Location $FrontendRoot
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。默认开发代理把 `/api` 转发到 `http://localhost:8000`；隔离验收应使用匹配的代理配置和独立端口，不占用正式服务。

## 启动后验证

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/runtime-prewarm-status
```

随后确认：

1. 新建会话能看到所有 `ready + enabled` 数据源。
2. PostgreSQL 和 MySQL 各完成一次普通查询，能收到进度、DataFrame 和最终文本。
3. 日报或月报先显示配置面板，确认后才生成。
4. 小助手管理页能读取应用，关联网站和 Widget 能按授权来源访问。

## 停止与重启

- 在对应终端使用 `Ctrl+C` 停止服务。
- 重启前确认旧进程已退出、端口已释放，并重新执行版本门禁。
- 不要通过删除 Catalog、系统数据库、Metadata、Memory 或 Chroma 解决启动问题。

## 变更正式资产

- 数据源变更使用管理 API/CLI 的发现、准备、发布和启停流程。
- 小助手应用使用管理 API/CLI 创建或编辑。
- 禁止直接编辑 SQLite、复制正在使用的 Chroma 目录或手工修改 runtime revision。
- 调试和验收优先使用 Catalog、系统数据库与运行资产副本。

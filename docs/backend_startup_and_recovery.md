# 后端启动、重启与 502 恢复手册

本文记录 Windows 本地正式环境的后端启动流程。目标是运行已复审的代码，同时继续使用现有正式 Catalog、Metadata、Chroma、小助手系统库和报表目录。

## 1. 当前正式资产位置

| 资产 | 路径 |
|---|---|
| 项目主目录 | `E:\3\posgresql\1` |
| Python 虚拟环境 | `E:\3\posgresql\1\vanna_venv` |
| 数据源 Catalog | `E:\3\posgresql\1\agent_data\data_sources\catalog.sqlite3` |
| PostgreSQL Metadata | `E:\3\posgresql\1\agent_data\column_metadata_index.json` |
| PostgreSQL Chroma | `E:\3\posgresql\1\vanna_data` |
| MySQL Metadata | `E:\3\posgresql\1\agent_data\mysql-lzh-monitor\column_metadata_index.json` |
| MySQL revision 3 Chroma | `E:\3\posgresql\1\agent_data\mysql-lzh-monitor\mysql-lzh-monitor.revision-3-2-1785398545932380700-73407a36` |
| 小助手系统库 | `E:\3\posgresql\1\data\system\assistant_apps.sqlite3` |
| 报表输出 | `E:\3\_runtime\water-quality-reports` |
| 运行日志 | `E:\3\posgresql\1\.runlogs` |

不要直接编辑 SQLite，不要重建 Chroma，也不要为了启动服务执行 discover、prepare、publish、enable 或 disable。

## 2. 启动前检查

### 2.1 检查数据库容器

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

应至少看到：

- `local-timescale`，宿主端口 `5433`；
- `mysql`，宿主端口 `3307`。

### 2.2 确认运行代码版本

正式后端应从干净且与 `origin/master` 一致的目录运行。

```powershell
git fetch origin
git -C E:\3\_worktrees\qs-master-baseline rev-parse HEAD
git rev-parse origin/master
```

两个 SHA 必须相同。如果主目录 `E:\3\posgresql\1` 存在未提交改动，不要在其中执行 reset、stash 或强制拉取，也不要直接用该目录启动“最新正式版”。应先准备新的干净部署 worktree。

### 2.3 检查端口

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
```

有输出表示已有后端监听。重启前必须先确认该进程确实是本项目的 `step4_server.py`，不能只按端口盲目结束进程。

## 3. 启动最新正式后端

以下脚本不会回显密码或密钥。它从现有 `.env` 读取 Catalog 加密密钥，并从现有 MySQL 容器配置中读取兼容启动所需的密码。业务查询实际仍使用 Catalog 中保存的加密数据源配置。

```powershell
$runtimeRoot = "E:\3\_worktrees\qs-master-baseline"
$formalRoot = "E:\3\posgresql\1"
$pythonExe = "$formalRoot\vanna_venv\Scripts\python.exe"

# 必须运行与远端 master 相同的代码。
$runtimeSha = (git -C $runtimeRoot rev-parse HEAD).Trim()
$masterSha = (git -C $formalRoot rev-parse origin/master).Trim()
if ($runtimeSha -ne $masterSha) {
    throw "运行目录不是最新 origin/master：runtime=$runtimeSha master=$masterSha"
}

# 读取 Catalog 凭据加密密钥，不回显值。
$credentialLine = Get-Content "$formalRoot\.env" |
    Where-Object { $_ -match '^DATA_SOURCE_CREDENTIAL_KEY=' } |
    Select-Object -First 1
if (-not $credentialLine) {
    throw "缺少 DATA_SOURCE_CREDENTIAL_KEY"
}
$env:DATA_SOURCE_CREDENTIAL_KEY = `
    $credentialLine.Substring($credentialLine.IndexOf('=') + 1).Trim()

# 当前兼容注册表初始化仍要求 MYSQL_USER/MYSQL_PASSWORD 存在。
$mysqlInspect = (docker inspect mysql | ConvertFrom-Json)[0]
$mysqlRootPassword = $mysqlInspect.Config.Env |
    Where-Object { $_ -like 'MYSQL_ROOT_PASSWORD=*' } |
    Select-Object -First 1
if (-not $mysqlRootPassword) {
    throw "MySQL 容器中缺少启动所需的密码配置"
}
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = `
    $mysqlRootPassword.Substring($mysqlRootPassword.IndexOf('=') + 1)

# PostgreSQL 用户名和密码应由用户级/进程级环境变量提供。
if (-not $env:DB_USER -or -not $env:DB_PASSWORD) {
    throw "缺少 DB_USER 或 DB_PASSWORD"
}

# 显式绑定正式运行资产，避免干净 worktree 创建空 Catalog 或空 Chroma。
$env:DATA_SOURCE_CATALOG_PATH = `
    "$formalRoot\agent_data\data_sources\catalog.sqlite3"
$env:WATER_AGENT_SYSTEM_DB_PATH = `
    "$formalRoot\data\system\assistant_apps.sqlite3"
$env:AGENT_DATA_DIR = "$formalRoot\agent_data"
$env:VANNA_DATA_DIR = "$formalRoot\vanna_data"
$env:METADATA_INDEX_PATH = `
    "$formalRoot\agent_data\column_metadata_index.json"
$env:MYSQL_METADATA_INDEX_PATH = `
    "$formalRoot\agent_data\mysql-lzh-monitor\column_metadata_index.json"
$env:MYSQL_VANNA_DATA_DIR = `
    "$formalRoot\agent_data\mysql-lzh-monitor\mysql-lzh-monitor.revision-3-2-1785398545932380700-73407a36"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = "$formalRoot\.runlogs\backend-master-$stamp.out.log"
$stderr = "$formalRoot\.runlogs\backend-master-$stamp.err.log"

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList "step4_server.py" `
    -WorkingDirectory $runtimeRoot `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru

$process.Id | Set-Content "$formalRoot\.runlogs\backend-master.pid"
Write-Host "后端已提交启动，PID=$($process.Id)"
Write-Host "stdout=$stdout"
Write-Host "stderr=$stderr"
```

不要把 `$env:DATA_SOURCE_CREDENTIAL_KEY`、`$env:MYSQL_PASSWORD`、`$env:DB_PASSWORD` 或模型 API Key 输出到终端或日志。

## 4. 启动后验收

Runtime 预热期间端口可能尚未就绪。持续检查健康接口：

```powershell
$deadline = (Get-Date).AddMinutes(15)
do {
    try {
        $health = Invoke-RestMethod http://127.0.0.1:8000/health -TimeoutSec 5
        if ($health.status -eq "healthy") { break }
    } catch {
        Start-Sleep -Seconds 5
    }
} while ((Get-Date) -lt $deadline)

if ($health.status -ne "healthy") {
    throw "后端未在规定时间内通过健康检查"
}
```

检查正式数据源状态和 revision：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/data-source-management |
    Select-Object source_id,database_type,status,enabled_for_chat,runtime_revision |
    Format-Table -AutoSize
```

预期结果：

| source_id | status | enabled_for_chat | runtime_revision |
|---|---|---:|---:|
| `postgresql-main` | `ready` | `True` | `1` |
| `mysql-lzh-monitor` | `ready` | `True` | `3` |

最后检查前端代理：

```powershell
(Invoke-WebRequest -UseBasicParsing `
    http://127.0.0.1:5173/api/data-source-management `
    -TimeoutSec 20).StatusCode
```

应返回 `200`。

## 5. 安全重启

先解析 `8000` 的监听进程，并核对命令行：

```powershell
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen `
    -ErrorAction SilentlyContinue
if ($listener) {
    $backend = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$($listener.OwningProcess)"
    $backend | Select-Object ProcessId,Name,CommandLine
}
```

只有确认命令行包含本项目 `step4_server.py` 后，才能停止该 PID：

```powershell
if ($backend.CommandLine -notlike "*step4_server.py*") {
    throw "8000 端口不是本项目后端，拒绝停止"
}
Stop-Process -Id $backend.ProcessId
```

确认 `8000` 已释放，再执行第 3、4 节。

## 6. 502 排障

页面显示“请求失败（502）”时，先检查：

```powershell
Get-NetTCPConnection -LocalPort 5173,8000 -State Listen `
    -ErrorAction SilentlyContinue
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health `
    -TimeoutSec 5
```

常见判断：

- `5173` 正常、`8000` 不存在：后端未运行，Vite 代理因此返回 502；
- `8000` 存在、`/health` 失败：检查最新 `backend-master-*.err.log`；
- 日志提示缺少 `MYSQL_USER/MYSQL_PASSWORD`：启动进程未执行第 3 节的安全注入；
- 日志提示缺少 `DB_USER/DB_PASSWORD`：当前启动终端没有继承 PostgreSQL 环境变量；
- 两个端口都存在但页面仍报错：直接请求对应 `/api/...`，区分前端代理问题和后端接口问题。

查看最新日志：

```powershell
$latestError = Get-ChildItem E:\3\posgresql\1\.runlogs\backend-master-*.err.log |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Get-Content $latestError.FullName -Tail 200
```

## 7. 当前运行实例

2026-08-03 最近一次验证结果：

- 后端代码 SHA：`84fdce24df59e521ae1e84f73b2bb1c1ea925c8d`；
- `/health` 返回 `healthy`；
- 前端代理返回 HTTP `200`；
- PostgreSQL：`ready + enabled`，revision `1`；
- MySQL：`ready + enabled`，revision `3`。

该启动方式仍属于后台进程，不具备进程异常退出后的自动拉起能力。长期运行建议进一步将后端注册为 Windows 服务，并配置失败自动重启与日志轮转。

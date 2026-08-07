# 本地镜像构建与隔离验收 Runbook（E-4）

> 本文记录 E-4 冻结的本地镜像构建与隔离验收流程。禁止推送 Harbor、禁止挂载正式资产、禁止连接正式业务数据库。

## 1. 从最终 master 构建镜像

```powershell
# 1) 在独立干净 worktree 检出最终 master
git worktree add --detach <worktree> origin/master
cd <worktree>
git status --short   # 必须为空

# 2) 构建本地镜像（不使用任何 Git 相关 build arg）
$buildDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
docker build --build-arg BUILD_VERSION=e4-local --build-arg BUILD_DATE=$buildDate -t water-agent:e4-local .

# 3) 可选本地别名（仅本地）
docker tag water-agent:e4-local water-agent:e4-final
```

约束：

- build context 只能是本地目录 `.`；禁止远程 Git URL、禁止 git clone/pull/fetch。
- 镜像名与标签不得包含 Git SHA、分支名、仓库地址；不得写入 Git 来源标签。
- 构建不依赖实体 `.env`；`.env`、正式数据目录、密钥与 SQLite 不得进入镜像。

## 2. 检查 OCI 标签与镜像身份

```powershell
docker inspect water-agent:e4-local --format "ID={{.Id}}`nCreated={{.Created}}`nLabels={{json .Config.Labels}}`nEntrypoint={{json .Config.Entrypoint}}`nExposedPorts={{json .Config.ExposedPorts}}"
docker images --format "{{.Repository}}:{{.Tag}}  {{.ID}}  {{.Size}}" | Select-String "water-agent:e4"
```

期望：只有 `org.opencontainers.image.version` 与 `org.opencontainers.image.created` 两个与 Git 无关的标签；入口为 `/opt/water-agent/deploy/docker/entrypoint.sh`；暴露 8000/tcp。

## 3. 镜像内容扫描

```powershell
@'
cd /opt/water-agent
find . \( -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.sqlite-wal' -o -name '*.sqlite-shm' \
  -o -name '*.sqlite3-wal' -o -name '*.sqlite3-shm' -o -name '*.db' -o -name '.env' -o -name '.env.*' \
  -o -name 'credential_key' -o -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' \
  -o -name 'questions_v1.json' -o -name 'id_rsa' -o -name '*.tar' \) -print
ls -d agent_data vanna_data data chroma backups .git 2>/dev/null
'@ | docker run --rm -i --entrypoint sh water-agent:e4-local -s
```

期望：扫描结果为空；`agent_data/vanna_data/data/chroma/backups/.git` 均不存在；仅 `runtime/reports` 与 `runtime/traces` 空目录。

## 4. 启动隔离临时容器

```powershell
$rt = "E:\3\_e4_runtime\accept-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force -Path "$rt\agent_data", "$rt\questions", "$rt\vanna_data" | Out-Null
$key = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
docker run -d --name e4-acceptance -p 18080:8000 `
  -e AGENT_DATA_DIR=/opt/water-agent/agent_data `
  -e VANNA_DATA_DIR=/opt/water-agent/vanna_data `
  -e QUESTION_SUGGESTIONS_DIR=/opt/water-agent/questions `
  -e DATA_SOURCE_CREDENTIAL_KEY=$key `
  -v "$rt/agent_data:/opt/water-agent/agent_data" `
  -v "$rt/vanna_data:/opt/water-agent/vanna_data" `
  -v "$rt/questions:/opt/water-agent/questions" `
  water-agent:e4-local
```

禁止：挂载正式 agent_data / 服务器数据 / 复用正式 Catalog、Chroma 或推荐问题资产；不得使用正式 `.env`。

## 5. 健康检查与基础验收

```powershell
docker inspect e4-acceptance --format "{{.State.Health.Status}}"
Invoke-WebRequest -Uri "http://127.0.0.1:18080/health" -UseBasicParsing   # 期望 200
Invoke-WebRequest -Uri "http://127.0.0.1:18080/" -UseBasicParsing        # 期望 200 index.html
Invoke-WebRequest -Uri "http://127.0.0.1:18080/api/data-sources" -UseBasicParsing  # 期望 200 []
```

## 6. 确认单进程

```powershell
docker top e4-acceptance
```

期望：仅一个 `python -m deploy.docker.server` 应用进程；无多个 Uvicorn worker、无 Gunicorn worker 池；同一测试卷不被第二个应用容器挂载。

## 7. Catalog 与重启验收

```powershell
# 首次启动后 Catalog schema 应为 11
python -c "import sqlite3; c=sqlite3.connect(r'<temp>\agent_data\data_sources\catalog.sqlite3'); print(c.execute('SELECT version FROM system_schema_versions').fetchone()[0]); print(c.execute('SELECT count(*) FROM active_asset_batches').fetchone()[0]); c.close()"

# 重启：stop → start → 再次 healthy，schema 仍为 11，无 active batch、无 candidate 残留
docker stop e4-acceptance
docker start e4-acceptance
```

## 8. 日志安全扫描

```powershell
docker logs e4-acceptance 2>&1 | Select-String -Pattern "password|secret|credential_key|api_key|PRIVATE KEY|DATA_SOURCE_CREDENTIAL_KEY="
```

期望：无命中（不得输出真实密码、完整连接串、credential key、API key、环境变量全集或私钥内容）。

## 9. 清理

```powershell
docker rm -f e4-acceptance
Remove-Item -LiteralPath "<temp>" -Recurse -Force   # 或使用等价的 Python shutil 清理
docker rmi water-agent:e4-verify  # 如存在一次性验证镜像
```

## 10. 禁止事项

- 禁止 `docker push`、登录/修改 Harbor、创建 Harbor tag。
- 禁止部署服务器、替换/修改正式容器、停止/重启正式服务、修改正式 Compose 或环境变量。
- 禁止挂载正式数据卷、修改正式 Catalog/Chroma/Metadata/推荐问题资产、执行正式数据源 prepare。
- 禁止在容器内热修复源码、`docker commit`、覆盖镜像内文件。
- 镜像验收失败时：停止验收容器 → 回 release 分支修改 → 完整测试 → 推送 → Commit Diff 审查 → 合入 master → 删除失败镜像标签 → 从新 master 重新构建。

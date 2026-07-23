# F6-2C-O5-C-R1 主工作区 Runner 复验

本目录是原 R1 的继续执行证据，保留父目录中的首次 detached worktree 环境阻断证据，不覆盖或删除任何首次证据。

## 执行入口

- Runner 项目根：`E:\3\posgresql\1`
- Python：`E:\3\posgresql\1\vanna_venv\Scripts\python.exe`
- 基线提交：`c3b880ee707334a1c49527e711b30aecf9d5a9ce`
- 隔离 Chroma：沿用原 R1 未打开、未修改的 `validation_chroma`
- 隔离 Agent 目录：沿用原 R1 `validation_agent_data`
- 动态正式基线参数：未传递
- 单问题重试：未执行

## 结果

- HTTP：`19/20`
- HTTP 请求数：`20`
- HTTP 重试数：`0`
- Memory：`6/6`
- 正式资产监控：`25/25` 检查点通过
- 最终状态：`BLOCKED_BY_MANAGED_FORMAL_BASELINE_FAILURE`

唯一失败用例为 `level2_water_intake_route`。实际 SQL：

```sql
SELECT name, water_type
FROM wm_water_intake
ORDER BY name
LIMIT 50
```

SQLGuard、HTTP、执行和行数门禁均通过，但返回列仅为 `name`、`water_type`，未满足 suite 固定要求的 `name`、`region_name`、`city`、`county`、`water_type`、`code`，因此 `expected_columns` 失败。

按授权要求，没有重试、没有修改代码、文档或 suite，也没有自动修复。

## 文件

- `regression-result.json`：Runner 脱敏完整汇总。
- `memory-regression-result.json`：六项 Memory 结果。
- `formal-monitor-checkpoints.json`：25 个正式资产检查点。
- `parent-environment.json`、`server-environment.json`：隔离路径证据。
- `server-log.txt`：已由 Runner 去除调用凭据的服务日志。
- `rerun-summary.json`：复验摘要。
- `failed-http-case.json`：唯一失败用例的最小证据。
- `redaction-report.json`：脱敏检查。
- `file-inventory.sha256`：本目录文件摘要。

未提交 Chroma 二进制、CSV 业务结果、凭据、Authorization 或完整 Prompt。

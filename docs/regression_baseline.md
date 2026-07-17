# PostgreSQL F5 回归基线

## 唯一回归集

- Suite：`training/regression/postgresql_f5_regression_v1.json`
- Runner：`tools/run_postgresql_f5_regression.py`
- Suite ID：`postgresql-f5-regression-v1`
- Case 数量：15
- Suite 内容 SHA256：`f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34`
- 构成：F2 固定问题 6 题，Batch 02—10 目标问题 9 题。

## 适用正式训练状态

```text
正式训练提交：adfd751e771e57c7b6dc0515c09db374ae0aff13
正式 Chroma 记录数：197
正式 Chroma SHA256：222bc79b0d08ee895ded4cd0f8beaf641e4faba8b7c55b2b6c333d089a837b26
```

## 执行命令

先从正式 Chroma 创建并验证仓库外完整副本，再执行：

```powershell
E:\3\posgresql\1\vanna_venv\Scripts\python.exe tools\run_postgresql_f5_regression.py `
  --suite training\regression\postgresql_f5_regression_v1.json `
  --data-dir <已验证的Chroma副本> `
  --agent-dir <独立Agent输出目录> `
  --evidence-dir <Evidence目录>
```

Runner 只打开验证副本；数据库连接由现有运行配置强制设置只读事务、30秒语句超时和5秒锁超时。

数据库执行必须满足：

```text
default_transaction_read_only=on
statement_timeout=30000
lock_timeout=5000
禁止 DDL / DML
```

## 隔离安全门禁

- Runner 父进程不得创建或打开 Memory；
- 父进程和服务子进程必须指向同一个全新验证副本；
- 正式 Chroma 不得作为 `--data-dir`；
- 正式目录必须在服务启动前、服务启动后、首题后、每道后续题后和服务停止后验证；
- 验证副本被服务打开后物理 SHA 发生变化可以接受，但记录数不得增长或减少；
- 正式目录记录数或 SHA 发生任何变化，必须立即停止服务并判定失败。

F5-G1 初次执行时，旧 Runner 因父进程调用 `context_diagnostics` 而误开正式 Chroma。正式目录已从精确备份恢复；父进程 Memory 链现已切断，并在全新副本完成 15/15 回归和 18/18 正式目录检查点验证。

## 随机失败重试规则

第一次必须完整执行 15 题。仅当恰好 14/15 通过，且唯一失败属于随机无 SQL、SSE 或单题随机生成失败时，允许从原始正式目录重新创建全新验证副本，再完整执行一次 15 题。不得只重试失败题，不得复用第一次副本。

## 版本升级规则

v1 验收后不可静默修改问题或约束。新增、删除或修改任何 case 时必须创建 v2，并保留 v1，不得覆盖历史验收含义。

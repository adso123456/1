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
正式训练提交：a1d322848012f5be1ba0ef2e4247139d4f92ea33
正式 Chroma 记录数：198
正式 Chroma SHA256：d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992
```

## Runner版本资产基线

Runner当前适用正式基线：

```text
EXPECTED_FORMAL_RECORD_COUNT = 198
EXPECTED_FORMAL_SHA256 = d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992
Runner Git仓库规范字节SHA256 = 69369e6f879d02041f9b4d5675a5645345295a094c521bfacd1a666b75309f7e
```

Runner跨平台SHA门禁以 `git show HEAD:tools/run_postgresql_f5_regression.py` 返回的原始二进制stdout为唯一计算对象，并使用Python `subprocess` 直接计算SHA256；不得通过PowerShell文本管道计算，也不得使用Windows工作文件原始字节SHA作为跨平台门禁。

总验收开始前必须同时满足：

- `git diff --quiet -- tools/run_postgresql_f5_regression.py` 通过；
- Git仓库规范Runner SHA256与本节登记值一致；
- 本地工作文件在Git逻辑内容上无差异；
- CRLF/LF差异不得单独判定为代码漂移。

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

F5 Level 3 Batch 01交付后，固定suite未修改，Suite内容SHA256仍为 `f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34`；在198条正式状态对应的隔离副本上再次完成15/15，正式目录监控全程通过。

## F5最终验收记录

在198条最终正式训练状态上执行固定suite。第一次完整运行14/15，唯一失败为随机无SQL且正式目录全部检查点通过；严格按重试规则从正式基线创建另一份全新副本，第二次完整运行15/15并验收通过。正式目录全程保持198条及SHA256 `d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992`。

固定suite内容、15题数量和内容SHA256 `f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34` 均未修改。

## 随机失败重试规则

第一次必须完整执行 15 题。仅当恰好 14/15 通过，且唯一失败属于随机无 SQL、SSE 或单题随机生成失败时，允许从原始正式目录重新创建全新验证副本，再完整执行一次 15 题。不得只重试失败题，不得复用第一次副本。

## 版本升级规则

v1 验收后不可静默修改问题或约束。新增、删除或修改任何 case 时必须创建 v2，并保留 v1，不得覆盖历史验收含义。

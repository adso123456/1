# F6-2C-O5-C-R1 managed-v1 正式资产隔离完整基线证据

## 基线

- `RUN_STAGE`: `F6-2C-O5-C-R1`
- `BASE_COMMIT`: `c3b880ee707334a1c49527e711b30aecf9d5a9ce`
- `RUN_DIRECTORY`: `E:\3\_training_backups\f6-2c-o5-c-r1-managed-formal-20260723-111840`
- `EVIDENCE_BRANCH`: `evidence/f6-2c-o5-c-r1-managed-formal-baseline`
- `SUITE_ID`: `postgresql-f5-regression-v1`
- `SUITE_CONTENT_SHA256`: `6e8e3e7fcfc57f7fd1b815dd0fec7263245c2439c4f97fb895b5248d2cc84e6a`

## 结果

`BLOCKED_BY_MANAGED_FORMAL_BASELINE_FAILURE`

离线编译与 Runner self-test 均通过，Runner 正确解析默认正式基线为 198 条及 Manifest `0f163f373d1336e4c34522fb385d3355f1663a75d47184dbd395671f1026144c`，且未使用动态覆盖。

唯一一次完整基线调用在 Memory Worker 进程启动前失败。`tools/regression_service_harness.py` 将 Python 路径解析为 detached validation worktree 下的 `vanna_venv\Scripts\python.exe`，但该工作树不包含虚拟环境，因此触发 `FileNotFoundError: [WinError 2]`。

本次实际执行：

- Memory：`0/6`，六项均未运行；
- HTTP 用户问题请求：`0/20`；
- HTTP 重试：`0`；
- 服务启动：否；
- Provider/模型请求：否；
- 数据库连接及写入：否。

按任务失败处理要求，没有修复代码、没有补建链接、没有重试完整基线。

## 资产保护

- 正式 Metadata 前后 SHA256：`c878e748669fac52bd60cd9e59e7670cc46757a10207078a46e1d29cedbf62e4`
- 正式 Chroma 前后记录数：`198`
- 正式 Manifest 前后：`0f163f373d1336e4c34522fb385d3355f1663a75d47184dbd395671f1026144c`
- 正式 Tree SHA256 前后：`1a55902ef0f9e42e7a0d20cfb8e0d83991f614f7a3ded1639ed477d0bc838471`
- 正式 Chroma 未被服务或 Chroma API 打开。
- 校验副本前后仍为 198 条，物理 Manifest 未变化。

## 证据文件

- `run-summary.json`：阻断结论及根因。
- `regression-result.json`：Runner 原始汇总。
- `http-summary.json`、`per-case-http-results.json`：HTTP 未运行证据。
- `memory-summary.json`、`per-case-memory-results.json`：Memory 未运行证据。
- `resolved-baseline.json`：默认正式基线解析。
- `formal-asset-preflight.json`、`formal-asset-postflight.json`：正式资产保护。
- `validation-copy-manifest.json`：校验副本复制与前后状态。
- `suite-manifest.json`、`source-code-manifest.json`：套件和源码摘要。
- `formal-monitor-checkpoints.json`：Runner 正式目录检查点。
- `trace-audit.json`：因服务未启动而没有真实请求 Trace。
- `sql-guard.jsonl`：SQLGuard 未运行标记。
- `server-log.txt`：服务未启动，因此为空。
- `runner-self-test.json` 及各离线契约测试结果。
- `py-compile-result.txt`、`self-test-result.txt`：离线门禁结果。
- `redaction-report.json`、`verification-status.json`：脱敏及最终状态。
- `file-inventory.sha256`：证据文件完整性摘要。

## 脱敏与排除

未提交 Chroma 二进制、Metadata 完整文件、业务数据、完整 Prompt、凭据、Authorization、数据库连接字符串或运行目录中的验证副本。环境门禁仅记录凭据是否存在，未记录值。

`MANIFEST.md` 自身不纳入 `file-inventory.sha256`，避免自引用摘要。

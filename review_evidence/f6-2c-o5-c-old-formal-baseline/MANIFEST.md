# F6-2C O5-C 旧正式资产隔离基线证据

- BASE_COMMIT: `570c5a6a9c142885ba43a20a6581d68673714b15`
- EVIDENCE_BRANCH: `evidence/f6-2c-o5-c-old-formal-baseline`
- RUN_DIRECTORY: `E:\3\_training_backups\f6-2c-o5-c-old-formal-20260723-102610`
- RESULT: `BLOCKED_BY_OLD_FORMAL_BASELINE_FAILURE`

## 阻断门禁

正式资产预检中，记录数 `198`、Metadata 文件 SHA256 和 `TREE_SHA256` 均符合授权基线，但项目官方 `build_directory_manifest` 得到的正式 Chroma `MANIFEST_CONTENT_SHA256` 为：

```text
0f163f373d1336e4c34522fb385d3355f1663a75d47184dbd395671f1026144c
```

授权及 Runner 固定期望为：

```text
d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992
```

因此在服务启动、数据库连接、HTTP 请求和 Memory 回归之前停止。没有重试，没有修改代码、文档、suite 或正式资产。

## 证据内容

- `runner_evidence/`：失败摘要、HTTP/Memory NOT_RUN 摘要、正式资产前后检、suite/source 清单和安全状态。
- `asset_preflight/`：正式 Chroma 官方目录清单及 `create_verified_copy` 输出。
- `asset_postflight/`：停止后的正式 Chroma 官方目录清单。
- `validation-copy-manifest.json`：验证复制的独立副本。
- `request_traces/`、`raw_sse/`：明确记录未启动服务、未发送请求。
- `logs/`：空服务日志，证明未启动服务。
- `file-inventory.sha256`：除清单自身外全部提交证据的 SHA256。

## 脱敏

未包含 API Key、Authorization、Cookie、数据库凭据、连接字符串、业务数据行、完整 Prompt 或任何 Chroma 二进制文件。详情见 `runner_evidence/redaction-report.json`。

## NOT_FOUND / NOT_GENERATED

- 完整请求 Trace：`NOT_GENERATED_PRECHECK_FAILURE`
- SQLGuard 运行记录：`NOT_GENERATED_PRECHECK_FAILURE`
- 原始 SSE：`NOT_GENERATED_PRECHECK_FAILURE`
- HTTP per-case 执行结果：空列表，未执行
- Memory per-case 执行结果：空列表，未执行

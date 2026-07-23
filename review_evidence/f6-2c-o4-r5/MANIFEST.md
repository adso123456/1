# F6-2C O4-R5 单请求验证证据

- BASE_COMMIT: `0a5ae735384206facd74a3d31bcd8c3c1d924f71`
- O3_R4_EVIDENCE_COMMIT: `6568f65a0f44cbb1992c14d261f113a532b8ae2a`
- EVIDENCE_BRANCH: `evidence/f6-2c-o4-r5`
- RUN_DIRECTORY: `E:\3\_training_backups\f6-2c-o4-r5-20260723-094430`
- TRACE_ID: `7960c1f1e0be4b92be11ea844a2b4ce5`
- HTTP_REQUEST_COUNT: `1`
- HTTP_RETRY_COUNT: `0`

## 判定

首轮强制 `run_sql`、零行成功、权威工具结果、工具阶段关闭、第二轮 Answer-only、两次 LLM、一次 SQL 和一次 DataFrame 等核心契约均通过。

最终文本未通过一致性门禁：它提出改查月表、日表或其他表，询问是否继续查询，并对空结果原因作出未经已执行 SQL 证明的推测。因此结论为 `BLOCKED_BY_FINAL_TEXT_INCONSISTENCY`，没有重试或修改代码。

## 文件清单

- `evidence/`：诊断结果、最终文本逐项审查、资产前后检、出站 HTTP 证据、SQLGuard、服务生命周期与离线预检。
- `raw_sse/`：原始 SSE、解析事件、最终文本和请求摘要。
- `request_traces/7960c1f1e0be4b92be11ea844a2b4ce5/`：完整两轮 LLM 请求/响应及上下文、工具和请求结束诊断。
- `logs/`：隔离服务 stdout/stderr。
- `run_o4_r5.py`：ASCII-only 单请求脚本。
- `launch_and_request_o4_r5.py`：受控启动、单请求和停止编排脚本。
- `verification-status.json`：Codex 已执行，等待 ChatGPT 直接审查。
- `redaction-report.json`：脱敏项目和数量。
- `file-inventory.sha256`：除清单自身外全部证据文件的 SHA256 清单，其中包括本 `MANIFEST.md`。

## 脱敏说明

未包含 API Key、Authorization、Cookie、数据库凭据或连接字符串。复制的服务日志内回环 IP、绑定 IP 和本地主机标识已替换。DataFrame 为零行，仅保留列名、行数和执行状态，不含业务数据行。

## NOT_FOUND

`NONE`

# F6-2C O4-R4 单请求验证证据

- BASE_COMMIT: `0a5ae735384206facd74a3d31bcd8c3c1d924f71`
- O3_R3_EVIDENCE_COMMIT: `05ff0dc79c13f600502b2c6de25ff6195df0196c`
- EVIDENCE_BRANCH: `evidence/f6-2c-o4-r4`
- RUN_DIRECTORY: `E:\3\_training_backups\f6-2c-o4-r4-20260723-091719`
- TRACE_ID: `fe3ee53d8e9348ffbc0d1a69d5823d76`
- HTTP_REQUEST_COUNT: `1`
- HTTP_RETRY_COUNT: `0`

## 判定

首轮强制 `run_sql`、零行成功、工具阶段关闭、第二轮 Answer-only、两次 LLM、一次 SQL、一次 DataFrame 等核心契约全部通过。

最终文本未通过事实一致性门禁：它正确报告零行，但随后声称应移除年份过滤并重新尝试；实际执行的 SQL 本来就没有年份过滤，而且没有发生后续 SQL。因此本次结论为 `BLOCKED_BY_FINAL_TEXT_INCONSISTENCY`，没有标记为成功。

## 内容

- `evidence/`：诊断结果、资产前后检查、出站 HTTP body/headers/audit、SQLGuard JSONL、服务生命周期与预检。
- `raw_sse/`：原始 SSE、解析事件、最终文本和请求摘要。
- `request_traces/<trace_id>/`：完整两轮 LLM 请求/响应及上下文、工具和请求结束诊断。
- `logs/`：隔离服务 stdout/stderr。
- `run_o4_r4.py`：ASCII-only 单请求脚本。
- `launch_and_request_o4_r4.py`：受控启动、单请求和停止编排脚本。
- `verification-status.json`：Codex 已执行，等待 ChatGPT 直接审查。
- `file-inventory.sha256`：证据文件 SHA256 清单。

## 脱敏

未上传密钥、Cookie、数据库凭据、连接字符串或业务数据行。回环地址和本机主机标识已替换；脱敏计数见 `redaction-report.json`。

## 缺失文件

`NONE`

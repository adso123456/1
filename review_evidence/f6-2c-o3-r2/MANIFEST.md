# F6-2C O3-R2 临时审查证据

- BASE_COMMIT: `0a5ae735384206facd74a3d31bcd8c3c1d924f71`
- O3_R1_EVIDENCE_COMMIT: `bcf49760c1479a7de900622ff4a0b9c644f25707`
- O4_R2_EVIDENCE_COMMIT: `896c07431eec402fd32a5455a816e1e477b4b02d`
- EVIDENCE_BRANCH: `evidence/f6-2c-o3-r2`
- OFFLINE_RUN_DIRECTORY: `E:\3\_training_backups\f6-2c-o3-r2-20260722-164234`
- NOT_FOUND_FILES: `NONE`
- FILE_INVENTORY_SHA256: `a114d6bafd874fbbcbbdc8e427681ffe9dbfbb3188139f5ff6ba19ed29e25b3f`

完整文件清单与逐文件 SHA256 位于 `file-inventory.sha256`。证据包含九个当前源码文件、完整工作区补丁、编译/自测/Git 门禁输出，以及非 Thinking 工具链和上下文清理测试结果。

## 修复摘要

- DeepSeek 首轮仍以 `thinking=disabled` 指定 `run_sql`。
- 同一用户请求的第二轮及以后保持 `thinking=disabled`，同时恢复原始 `tool_choice`（当前为 `auto`）。
- 后续轮移除 `reasoning_effort`、保留原有非 Thinking `extra_body` 字段，不注入任何 `reasoning_content`。
- 正常结束或 Provider 异常后清理请求级状态；下一用户请求恢复 Provider 默认 Thinking 和原始 `tool_choice`。
- 全部测试均为离线伪 Provider 测试，未发送 HTTP、模型、数据库或 Chroma 请求。

## 脱敏说明

- 未发现真实 API Key、Cookie、数据库凭据或连接字符串。
- 测试夹具中的伪 Authorization Token 替换为 `[REDACTED_SYNTHETIC_TOKEN]`。
- 回归工具源码副本中的回环 IP 替换为 `[REDACTED_LOOPBACK_IP]`。
- 上述替换仅应用于审查证据副本和证据补丁；主工作区源码未改写。

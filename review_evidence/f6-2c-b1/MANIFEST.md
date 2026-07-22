# F6-2C B1 临时审查证据

## 基本信息

- 原始来源目录：`E:\3\_training_backups\f6-2c-b1-20260722-111443`
- BASE_COMMIT：`2c9e409fe99dfea70d6255908179b086ec37ea3f`
- 证据分支：`evidence/f6-2c-b1`

## 复制文件及 SHA256

| 文件 | SHA256 |
|---|---|
| `evidence/formal-targeted-result.json` | `e348d20bee904d6b67d99aab902cd0515edef8ebfb8003903696e7fd78cd8898` |
| `evidence/self-test/runner-self-test.json` | `2558e9bda954f83523853914834a8bf178fb4e8035e0ae034fbb70c7d5f58fae` |
| `evidence/suite-scope-audit.json` | `0ebae2662365cb3d58f8cf94a0a0193d00902eeaf00ee849a73c7cd9379bb10b` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/generated_sql.txt` | `e3eb886f6d7eefdab69a73e9283ced4be1c03ff23fe74a1e7f8368cadc32a8e4` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/query_result.json` | `07e30f0ab21ad5bf2b2d0bd1027a2d63c68a3fe6b705cff8f5da5ba0f6958f81` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/sql_guard.json` | `986f56918967254b4f5811161cc12c94274b8d5f070610e1e8a4278c50d1f2da` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/summary.json` | `47856a2f136641a8b4195f3bad1b33ac45886940ac9eb28a6d0351af656db8c2` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/tool_events.json` | `1bce35d883fcbac623f5fe03dcd651a250256a161237ed7604e1eceba1939d11` |
| `raw_http/formal-targeted/level3_p0_annual_ph_ranking-attempt-1/validation_result.json` | `01b59bba4ed6287853a6965b93c418336947a31f0bf63a28e4283e71d04a57b3` |

## NOT_FOUND

无。要求的九个源文件均已找到并复制。

## 脱敏说明

- 已扫描数据库用户名和密码、API Key、Authorization Header、Cookie、连接字符串、IP 地址及常见 Token 格式；复制件中未发现这些内容。
- 已将 19 处临时 conversation、request 或组件 UUID 统一替换为 `[REDACTED_UUID]`。
- 查询结果为空，仅保留列名、行数和是否执行成功；不存在完整业务数据行或样本行。
- 未复制 `.env`、数据库备份、Chroma 数据库、CSV、密钥、Token、Authorization 日志或其他未列入清单的文件。

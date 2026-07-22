# F6-2C-N1-D1 离线诊断证据清单

## 基线与分支

- BASE_COMMIT：`0a5ae735384206facd74a3d31bcd8c3c1d924f71`
- 证据分支：`evidence/f6-2c-n1`
- 证据目录：`review_evidence/f6-2c-n1`

## 原始来源目录

- N1：`E:\3\_training_backups\f6-2c-n1-20260722-133555`
- B3R：`E:\3\_training_backups\f6-2c-b3r-20260722-124745`
- B4：`E:\3\_training_backups\f6-2c-b4-20260722-130105`
- 冻结候选 Chroma 源：`E:\3\_training_backups\f6-2c-20260722-100422\candidate_chroma_failed_after_switch`
- 候选 Metadata Index：`E:\3\_metadata_audits\f6-2b-postgresql\column_metadata_index.candidate.json`
- 旧正式 Metadata Index：`E:\3\posgresql\1\agent_data\column_metadata_index.json`

候选 Chroma 仅通过 SQLite `immutable=1` 方式读取；没有通过 Chroma API 打开任何正式或候选资产。

## 文件清单与 SHA256

| 文件 | 性质 | SHA256 |
|---|---|---|
| `annual-ph-failed-summary.json` | 由 N1 聚合证据生成的失败用例摘要 | `e47b6dbcd99551a0fe2fbc045bcd8221e05c0944f7cf1f1017cad74ab47c964b` |
| `annual-ph-server-log-sanitized.txt` | N1 server log 的脱敏结构摘要 | `197ea7c790d3d08cb4e50a5b10e7e122439db4aaee52bcc171ca2bd6300aa68e` |
| `b3r-annual-ph-sanitized-result.json` | B3R 已脱敏年度 pH 对照证据 | `421496ba81dd5adb279b40a40ae7dc99e5f08120c60d08bfc6a078b204db4ffb` |
| `b4-final-summary.json` | B4 已脱敏完整基线摘要 | `f6e3de46952d59d934fd1b44697210cbdc613ad16e550ce2c94ebdc2a455cd9e` |
| `candidate-memory-regression-result.json` | N1 候选 Memory 门禁结果 | `16426922933eb4f6a74af03143ed8315e68664bca3051b10d03f6082c239659d` |
| `candidate-regression-result.json` | N1 候选回归聚合结果（业务行已脱敏） | `6eaf5a7c4098a0536baac0a844facbe6f705a432e7150ce3c34d1247116f9e8e` |
| `context-comparison.json` | N1/B3R/B4 上下文可比性审计 | `a223e3f8879253c38bd574d176e4a4789e68e15c3fa350217f663a670957d60d` |
| `cross-request-state-audit.json` | 跨请求状态静态审计 | `6f1ea99d564bdf46a42476d26632a5de22303bf529fbf61890bed39a22734eb8` |
| `metadata-comparison.json` | 新旧 Metadata 定向比较 | `4a23b6bd1e1553bf418fc429dcfe02d34a9305aaf9c456233a53f582c8e19480` |
| `n1-blocked-summary.json` | N1 阻断摘要 | `2971b859884b025e8d57774a7531f7df3c2d996af8aa624c730e193ed63df7c5` |
| `parent-environment.json` | N1 父进程环境摘要 | `594fd6b3b6b77f0ec1c8dbff5017a0a9ff65396ed6923a5cf089d980e40c609c` |
| `root-cause-conclusion.json` | 根因分类与证据边界 | `b2015cabbe0996f1d8c07dc82e5d34f529ee4dabfb2ab5e752c747c2d1fe240d` |
| `server-environment.json` | N1 服务环境摘要 | `25d3e94a2c2fbe45c61ebe128892da56086c665c1bb8a0c02f80f2b8aa18f54b` |
| `session-isolation-audit.json` | 会话隔离契约审计 | `6f5b67b408b7b9e3707e17dd326a4e5be8b154a71384330763736f65790451ca` |
| `sql-memory-verification.json` | 候选 SQL Memory immutable 核验 | `8e82b86c31b1929e7296c1f2e4c2a91775647167fd2fad08cd2bdf6615c7a6cc` |

`MANIFEST.md` 不列自身 SHA，因为将自身摘要写入文件会递归改变该摘要；提交对象会为本文件提供不可变 Git blob 标识。

## NOT_FOUND

- `N1/level3_p0_annual_ph_ranking/raw response.sse`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/complete events.json`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/final text event content`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/conversation_id`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/request_id`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/raw LLM request body`：`NOT_FOUND`
- `N1/level3_p0_annual_ph_ranking/actual system prompt and tool schema`：`NOT_FOUND`
- `B3R/raw LLM request body and complete injected context`：`NOT_FOUND`
- `B4/per-case raw SSE for annual pH`：`NOT_FOUND`

任务要求不能仅凭聚合结果推断失败请求的完整行为；因此上述缺失项直接限制根因分类为 `INSUFFICIENT_RAW_LLM_EVIDENCE`。

## 脱敏说明

- 未复制 API Key、Authorization、Cookie、数据库凭据、连接字符串、IP 或完整业务数据行。
- 未复制 CSV、数据库备份、Chroma 文件或正式资产。
- B3R/B4 仅使用已有脱敏摘要；查询结果只保留列名、行数、空结果状态和 SQL，不含业务行。
- 未保留临时 conversation/request/task UUID。
- N1 server log 仅保留结构性结论，网络端点和临时标识均省略。

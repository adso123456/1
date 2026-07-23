# F6-2C-O5-C-P1 正式 Chroma 指纹溯源审计证据

## 基线

- `RUN_STAGE`: `F6-2C-O5-C-P1`
- `BASE_COMMIT`: `570c5a6a9c142885ba43a20a6581d68673714b15`
- `EVIDENCE_BRANCH`: `evidence/f6-2c-o5-c-p1-fingerprint-audit`
- `RUN_DIRECTORY`: `E:\3\_training_backups\f6-2c-o5-c-p1-20260723-104248`
- `FORMAL_CHROMA`: `E:\3\_runtime\vanna-level1\vanna_data`
- `FROZEN_MANIFEST_CONTENT_SHA256`: `d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992`
- `CURRENT_MANIFEST_CONTENT_SHA256`: `0f163f373d1336e4c34522fb385d3355f1663a75d47184dbd395671f1026144c`
- `CURRENT_TREE_SHA256`: `1a55902ef0f9e42e7a0d20cfb8e0d83991f614f7a3ded1639ed477d0bc838471`

## 审计结论

`D8EB_EXPECTATION_MISBOUND`

精确 d8eb 资产属于旧的 legacy/pre-switch 阶段；当前 0f163 正式资产是随后明确验收并要求保留的 managed-v1 live 阶段。两者均为 198 条记录，文档 SHA256 多重集合一致，但其中 115 条 DDL 记录的 ID 和 Metadata 契约不同。当前正式资产不应恢复为 d8eb。

建议在另行授权的阶段，保留当前正式资产，并将冻结常量与文档重新绑定到已正式验收的 managed-v1 当前基线。

## 文件清单

- `algorithm-comparison.json`：Manifest 算法 Blob 与规范化规则核对。
- `audit-script.py`：只读审计脚本。
- `audit-summary.json`：最终权威审计摘要。
- `backup-candidate-inventory.json`：受限目录中的 Chroma 候选清单。
- `current-formal-manifest.json`：当前正式资产文件级 Manifest。
- `d8eb-manifest.json`：选定精确 d8eb 来源的文件级 Manifest。
- `d8eb-vs-current-file-diff.json`：逐文件物理差异。
- `expectation-binding-evidence.json`：pre-switch 与 current-live 的直接历史绑定证据摘要。
- `formal-asset-preflight.json` / `formal-asset-postflight.json`：正式资产前后门禁。
- `formal-current-live-acceptance-summary.json`：F6-1I-C-R3-B 的直接历史验收记录。
- `historical-d8eb-evidence.json`：历史 d8eb 文本证据清单。
- `logical-inventory-current.json` / `logical-inventory-d8eb.json`：198 条记录的规范化摘要，不含原文和 embedding。
- `logical-inventory-comparison.json`：ID、document、Metadata 和逻辑总摘要比较。
- `redaction-report.json`：脱敏与排除项。
- `tree-sha-algorithm.txt` / `tree-sha-assessment.json`：Tree SHA 算法全文和边界判断。
- `verification-status.json`：只读边界与正式资产保护结果。
- `file-inventory.sha256`：上述证据文件的 SHA256。

## 脱敏与排除

未提交 Chroma 二进制文件、验证副本、数据库连接信息、凭据、业务数据行、document 原文或完整 embedding。逻辑清单仅保留记录标识、非敏感分类字段以及 document/Metadata 摘要。

## NOT_FOUND

无。已找到精确 d8eb Manifest 文本证据和 9 个精确物理来源。

## 完整性

各文件 SHA256 见 `file-inventory.sha256`。`MANIFEST.md` 自身不纳入该清单，以避免自引用摘要。

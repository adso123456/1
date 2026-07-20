# DDL Memory 正式治理、候选验收与恢复 SOP

## 1. 适用范围与阶段边界

本 SOP 固定 F6-1I 的治理决策、完整候选副本、验收、前向切换和失败回滚模型。F6-1I 分为三个必须分别授权的阶段：

```text
F6-1I-A  工具、决策规则、前向与回滚 SOP 准备
F6-1I-B  完整隔离候选构建、切换与回滚演练
F6-1I-C  经 I-B 验收后执行正式候选构建与路径切换
```

I-A 只允许运行治理模块 `--self-test`。不得访问、判断、遍历、读取、哈希、复制或打开正式 Chroma，不得创建真实 archive/candidate，不得执行 I-B 或 I-C。I-B 和 I-C 必须分别取得新的明确授权。

## 2. 已验收正式事实

```text
formal_collection_count                 198
expected_exact_match_record_count       115
expected_exact_match_table_count        115
non_ddl_memory_count                     83
missing_expected_table_count              0
content_variant_record_count              0
unexpected_ddl_record_count               0
exact_duplicate_group_count               0
table_identity_duplicate_group_count      0
duplicate_topk_impact                   NONE
```

正式治理不是删除重复内容。当前没有 DDL 精确重复或表身份重复；治理只判断这 115 条精确 DDL 是否仍属于随机 UUID/旧 Metadata 的 legacy 记录，并在必要时迁移为 `ddlmem-v1` 确定性身份。

## 3. 三种治理决策

### 3.1 `ALREADY_MANAGED_NO_SWITCH`

115 条 DDL 均为合法 `ddlmem-v1`，record ID、document、完整 Metadata 和 `content_fingerprint` 与当前期望完全一致；83 条非 DDL Memory 完整存在，总数为 198。此状态不构建迁移候选，也不切换正式路径。

### 3.2 `IDENTITY_MIGRATION_REQUIRED`

115 条 DDL 内容与当前期望精确一致，不存在缺失、变体、非预期 DDL 或重复，83 条非 DDL Memory 可完整识别，但至少一条精确 DDL 使用 legacy ID 或旧 Metadata。只有此状态允许后续迁移。

### 3.3 `BLOCKED_FORMAL_STATE`

以下任一情况必须阻断候选构建和正式切换：

- collection、DDL 候选、精确 DDL 或非 DDL 数量不为 `198/115/115/83`；
- 存在缺失、内容变体、非预期 DDL、精确重复或表身份重复；
- managed v1 结构损坏；
- 相同确定性 ID 对应不同内容；
- managed/legacy 数量或分类无法完整对账。

## 4. 不可变来源与候选模型

未来 I-B/I-C 固定使用：

```text
正式来源
→ immutable_source_archive
  ├→ source_query_copy
  └→ candidate_working_copy
       → candidate_query_copy
```

正式来源复制前、archive、正式来源复制后三份文件清单和 Tree SHA 必须完全一致。`immutable_source_archive` 永远不得由 Chroma Client 打开；任何 Client 只能打开获准工作副本。

`candidate_working_copy` 必须直接从 archive 完整复制。只有 candidate 可执行获准的 collection 写操作，正式来源和 archive 禁止任何 collection 写入、删除、移动或重命名。

## 5. 候选迁移与显式删除 allowlist

迁移输入固定为 115 条当前期望 DDL 和只读工作副本的完整记录快照。允许的迁移操作仅为：

1. 精确识别 legacy DDL ID；
2. 在 candidate 中删除已批准的 legacy DDL ID；
3. 用确定性 `record_id` 写入对应 `ddlmem-v1` DDL；
4. 保留已有且合法的 managed v1 DDL；
5. 原样保留全部 83 条非 DDL Memory。

allowlist 每项必须同时冻结：

```text
classification = expected_exact_match
legacy_record_id
target_record_id
normalized_document_sha256
```

执行删除前必须重新确认来源记录仍存在、document SHA 与当前期望 DDL 一致、目标确定性 ID 存在，并且来源不是应保留的合法 managed v1。禁止模糊删除、相似度删除、按 Metadata 宽条件删除、全 collection 清空，以及删除任何非 DDL Memory。

## 6. 非 DDL Memory 保留规则

迁移前后对 83 条非 DDL Memory 逐记录比较：

```text
record_id
normalized_document_sha256
canonical_metadata_sha256
```

三项必须全部相同。不得修改 document、Metadata、分类或 ID，不得读取、导出或保存 embedding。

## 7. candidate 验收

迁移后的 candidate 必须同时满足：

```text
total_count = 198
managed_v1_ddl_count = 115
legacy_expected_ddl_count = 0
non_ddl_memory_count = 83

create = 0
unchanged = 115
changed = 0
removed = 0

missing_expected_table_count = 0
content_variant_record_count = 0
unexpected_ddl_record_count = 0
exact_duplicate_group_count = 0
table_identity_duplicate_group_count = 0
```

任一条件失败，candidate 不得用于正式切换；失败运行目录不得复用。

## 8. Top-K 语义回归

只查询独立的 `source_query_copy` 和 `candidate_query_copy`，不得查询 archive。固定参数：

```text
查询集       F6-1H 的 12 个中文表注释查询
Top-K        10
Embedding    BAAI/bge-small-zh-v1.5
Collection   tool_memories
```

DDL ID 会迁移，因此不得比较原始 record ID。稳定语义键为：

```text
DDL：     table_name + normalized_document_sha256 + classification
非 DDL： record_id + normalized_document_sha256 + classification
```

每个查询的 Top-K 语义结果顺序必须一致，精确重复槽位和表身份重复槽位均为 0，Top-1/Top-5/Top-10 命中数一致。距离不进入稳定 SHA。任何语义结果变化都阻断正式切换。

## 9. 已提交完整回归入口

唯一已提交的 PostgreSQL 完整回归入口为：

```text
Runner: tools/run_postgresql_f5_regression.py
Suite:  training/regression/postgresql_f5_regression_v1.json
Suite ID: postgresql-f5-regression-v1
Case count: 15
Suite SHA256: f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34
```

候选分类、Plan、重复和 Top-K 验收通过后，在另一个从已验收 candidate 创建的完整回归副本上执行：

```powershell
E:\3\posgresql\1\vanna_venv\Scripts\python.exe tools\run_postgresql_f5_regression.py `
  --suite training\regression\postgresql_f5_regression_v1.json `
  --data-dir <已验收candidate的全新完整回归副本> `
  --agent-dir <全新仓库外Agent输出目录> `
  --evidence-dir <全新Evidence目录>
```

该 Runner 会连接 PostgreSQL 执行只读问答回归；必须保留其 `default_transaction_read_only=on`、`statement_timeout=30000`、`lock_timeout=5000` 门禁。不得使用或修改当前受保护的未跟踪审计脚本和结果文件。完整 15/15 未通过，不得切换。

## 10. 服务停止与目录占用门禁

正式切换前必须全部确认：

1. FastAPI、CLI Agent、训练脚本和所有可能持有 Memory 的进程已停止；
2. 没有 Chroma Client 占用正式目录、candidate 或拟重命名目录；
3. 正式来源 Tree SHA 与获批 I-B 基线一致；
4. 已按当前正式来源重新构建并验收 candidate；
5. candidate 完整回归 15/15 通过；
6. 同级旧正式目录和 immutable archive 两份恢复来源均可用；
7. 目录名称、磁盘、时间戳、Evidence 和操作人已复核。

服务停止和无 Client 占用是 I-C 执行时事实，不能由 I-B 提前声明。I-C 必须分别显式传入 `--service-stopped-confirmed` 和 `--no-client-occupancy-confirmed`；正式切换授权另由 `--formal-switch-authorized` 提供。缺少任一参数时，工具必须在正式路径存在性判断、哈希、复制、Client 创建、候选构建和运行目录创建之前停止。任何占用无法排除时停止，不得强制终止未知进程或继续重命名。

## 11. I-B sandbox 前向与回滚演练

I-B 必须在仓库外全新 sandbox 中使用以下目录：

```text
sandbox\live
sandbox\candidate
sandbox\pre_switch
sandbox\failed_candidate
```

演练必须分别覆盖：

1. 正常 `live → pre_switch`、`candidate → live`；
2. 模拟第二步失败，立即 `pre_switch → live`；
3. 模拟新 live 验收失败，执行 `live → failed_candidate`、`pre_switch → live`；
4. 回滚后原 live Tree SHA 恢复；
5. 回滚后 collection 总数和分类恢复；
6. 成功切换后保留 pre_switch，不主动回滚；
7. 演练全程不影响正式来源。

I-B 未全部通过前，禁止生成 PASS summary，禁止进入 I-C。I-B 生成的 summary 是原始演练 Evidence，三个运行时字段必须保持：

```text
formal_switch_authorized = false
service_stopped_confirmed = false
no_client_occupancy_confirmed = false
```

不得手工编辑、覆盖或复制生成所谓“批准版” summary。ChatGPT 对 I-B 的验收和 I-C 授权通过新的 I-C 提示词及本次命令行 `--formal-switch-authorized` 体现，不通过篡改 I-B Evidence 体现。

## 12. 正式前向切换

正式路径固定为：

```text
E:\3\_runtime\vanna-level1\vanna_data
```

同一磁盘同级目录固定为：

```text
vanna_data
vanna_data_pre_f6_1i_<时间戳>
vanna_data_candidate_f6_1i_<时间戳>
vanna_data_failed_f6_1i_<时间戳>
```

前向步骤不可调整顺序：

```text
1. vanna_data → vanna_data_pre_f6_1i_<时间戳>
2. vanna_data_candidate_f6_1i_<时间戳> → vanna_data
3. 重新打开新正式路径，执行只读分类、Plan、重复、Top-K 和完整回归验收
```

禁止在原正式目录中原地删除或改写记录。

## 13. 自动失败回滚

第一步后第二步失败，或新正式路径打开、数量、managed DDL、非 DDL、Plan、分类、重复、Top-K、完整回归任一失败时，必须立即：

```text
1. 失败的新 vanna_data → vanna_data_failed_f6_1i_<时间戳>
2. vanna_data_pre_f6_1i_<时间戳> → vanna_data
3. 验证恢复后的 Tree SHA、collection 总数和只读分类与切换前一致
```

若第一步成功但第二步尚未产生新 `vanna_data`，直接执行旧正式目录恢复。自动回滚只适用于切换未成功或切换后验收失败。

正式切换全部验收成功后，不故意回滚正式资产；保留旧正式目录、immutable archive、candidate 和全部 Evidence。任何清理必须另行授权。

## 14. 工具接口与阶段授权

I-A 唯一允许命令：

```powershell
python -m training.sop.ddl_memory_formal_governance --self-test
```

I-B 取得新授权后使用的冻结接口：

```powershell
python -m training.sop.ddl_memory_formal_governance `
  --isolated-drill `
  --formal-source E:\3\_runtime\vanna-level1\vanna_data `
  --run-root E:\3\_training_backups\f6-1i-b-<时间戳>
```

I-C 取得新授权且持有原始 I-B PASS summary 后使用的冻结接口：

```powershell
python -m training.sop.ddl_memory_formal_governance `
  --formal-switch `
  --formal-source E:\3\_runtime\vanna-level1\vanna_data `
  --approved-drill-summary <I-B原始summary.json> `
  --formal-switch-authorized `
  --service-stopped-confirmed `
  --no-client-occupancy-confirmed `
  --run-root E:\3\_training_backups\f6-1i-c-<时间戳>
```

工具已实现后两种未来接口，但 I-A 禁止调用。`--formal-switch` 的固定顺序是：先只读验证原始 I-B summary 的 PASS、候选、非 DDL、sandbox、来源/archive SHA 和 Plan 事实；再验证三个本次运行显式确认；再验证 CLI 路径格式；最后才允许访问正式来源。I-B 模式禁止携带三个正式切换确认参数。I-B/I-C 授权必须在执行前复核并形成新的范围明确提示；不得因接口已经存在而自动运行。

## 15. Evidence 与失败处理

每次运行目录必须全新且不存在，失败目录不得复用或删除。Evidence 不得保存 embedding、完整 DDL、完整 Metadata、密码、API Key、数据库完整数据或敏感查询结果。

I-B/I-C 至少保留：来源/archive/candidate 清单及 Tree SHA、治理决策、冻结 allowlist 的脱敏摘要、非 DDL 三元签名对账、候选 Plan/分类/重复摘要、Top-K 稳定语义 SHA、完整回归摘要、sandbox/正式切换步骤、回滚结果和最终状态。I-B 原始 summary 不得修改或覆盖。

出现 `BLOCKED_FORMAL_STATE`、来源变化、archive 变化、候选验收失败、语义回归变化、完整回归失败或目录占用时立即停止；不得自动重跑、不得复用失败目录、不得修改正式资产。

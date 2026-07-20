# 正式 DDL Memory 只读审计 SOP（F6-1G）

## 1. 范围与授权

本 SOP 仅用于 F6-1G：从固定正式 Chroma 生成完整只读快照，并在快照上审计 DDL Memory 的精确重复、表身份重复、内容变体、缺失和非预期记录。它不执行治理、删除、替换、补写或 Top-K 测试。

F6-1G-A 只准备并审查工具与 SOP，不执行本 SOP。正式执行前必须经过 ChatGPT 审查，并取得新的 F6-1G-B 明确授权。

## 2. 固定路径

正式来源只能是：

```text
E:\3\_runtime\vanna-level1\vanna_data
```

每次执行必须使用全新且不存在的运行目录：

```text
E:\3\_training_backups\f6-1g-<YYYYMMDD-HHMMSS>
```

脚本在运行目录内固定创建：

```text
formal_snapshot
evidence
```

失败运行目录不得复用，不得在原快照上继续审计或修补。

## 3. Git 前置检查

进入仓库 `E:\3\posgresql\1` 后执行：

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
git status --short
```

执行任务必须另行指定获批基线。HEAD 与 `origin/master` 不一致或不等于获批基线时立即停止。记录并保护既有脏工作区，不清理、不修改、不暂存受保护文件。

## 4. 唯一正式审计命令

获得 F6-1G-B 授权后，使用新的时间戳执行：

```powershell
python -m training.sop.ddl_memory_formal_readonly_audit `
  --formal-audit `
  --formal-source E:\3\_runtime\vanna-level1\vanna_data `
  --run-root E:\3\_training_backups\f6-1g-<时间戳>
```

不得改用别名、父目录、子目录、仓库 `vanna_data` 或已有运行目录。

## 5. 来源清单、复制与稳定性门禁

工具必须严格按以下顺序执行：

1. 校验正式来源精确路径和全新运行目录。
2. 只读遍历正式来源，记录相对路径、文件大小、文件 SHA-256，按相对路径排序并计算 `formal_source_tree_sha256_before`。
3. 使用文件复制将来源完整复制到 `<run-root>\formal_snapshot`，保留相对目录结构；不移动、不重命名、不删除来源文件，也不在来源内创建临时文件。
4. 计算 `snapshot_tree_sha256`。
5. 再次只读计算 `formal_source_tree_sha256_after`。
6. 强制要求 `before = snapshot = after`，且三个文件清单完全一致。

任一 SHA 或清单不一致即为 FAIL。此时禁止打开快照 Chroma，运行目录标记为不可使用，不自动重试，正式来源保持不变。

## 6. 只读 Chroma 边界

通过 Tree SHA 门禁后，Client 只能指向：

```text
<run-root>\formal_snapshot
```

只能通过 `get_collection(name="tool_memories")` 获取既有 collection；禁止 `get_or_create_collection`。只读取 record ID、document 和 Metadata，不读取或导出 embedding。

只读包装器不暴露以下方法，审计代码也不得调用：

```text
add
update
upsert
delete
save_text_memory
create_memory
apply_ddl_memory_plan
```

## 7. 期望集合与分类规则

当前期望集合只由 `train_step3.load_metadata_index()`、`group_tables()`、`build_all_table_ddls()` 生成。固定身份为 `postgres_water/public/table/<table name>`，并强制验证表、DDL、唯一表名、唯一 logical ID 和唯一 record ID 均为 115。

分类只使用精确内容与结构，不使用向量相似度：

- `expected_exact_match`：正式 document 经 F6-1B 最小规范化后的 SHA-256 与当前某条期望 DDL 完全一致。
- `expected_table_content_variant`：DDL 解析出当前 115 张表之一，但内容 SHA 与当前期望不一致。内容变体不得自动视为可删除重复。
- `unexpected_ddl`：同时具有 `[DDL_MEMORY]`、`CREATE TABLE`、`表名：` 特征，但表名不属于当前 115 张表。
- `non_ddl_memory`：其余记录；只计数，不修改其 Metadata 或业务分类。
- `missing_expected_table`：当前期望表没有任何 `expected_exact_match`，它是表级缺失清单，不是额外记录分类。

精确重复以规范化 document SHA 分组；同 SHA 多条记录形成一组。表身份重复以解析出的当前期望表名分组；同一表对应多条 DDL 记录形成一组。随机 UUID、timestamp、request_id、conversation_id 不参与重复判定。所有记录主分类总数必须与 collection 总数对账。

## 8. Evidence 限制

允许保存：record ID、表名、规范化 document SHA、Metadata 字段名、分类、重复组编号、汇总计数和 Tree SHA。

禁止保存：完整 DDL、embedding、密码、API Key、数据库数据，以及完整正式 Metadata 的敏感值。

## 9. 状态判定

- `PASS`：Tree SHA 门禁通过、快照只读分类完成、分类对账通过，且 collection 总数等于已知基线 198。
- `BASELINE_MISMATCH`：只读分类和对账完成，但实际 collection 总数不等于 198。退出码非 0，不得宣告 F6-1G 通过。
- `FAIL`：路径、复制、Tree SHA、快照打开、读取、分类或对账任一环节失败。

无论状态为何，均不得修改正式来源。失败目录不得复用。由于流程没有正式写入，不需要反向恢复 SOP；需要处置的只是仓库外运行目录和快照，且处置必须另行授权。

## 10. F6-1G-A 审查命令

在新的 F6-1G-B 授权前，只允许运行：

```powershell
python -m training.sop.ddl_memory_formal_readonly_audit --self-test
```

该模式仅使用内存合成记录和系统临时目录，不访问正式路径，不导入或创建 Chroma Client，并必须输出：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
```

## 11. F6-1H 快照 Top-K 影响评估

### 11.1 旧快照定位

F6-1G 的 `E:\3\_training_backups\f6-1g-20260720-153450\formal_snapshot` 是已经被 Chroma Client 打开过的审计工作副本，不是永久不可变归档。PersistentClient 的只读查询过程仍可能改变 SQLite/HNSW 存储文件，因此该旧快照必须保留但不得删除、修改、复用为归档或用于 F6-1H 查询。

F6-1H 首次尝试在 Client 打开前发现旧快照当前 Tree SHA 已不同于 F6-1G 复制时 SHA，按门禁停止。失败 Evidence 保留在：

```text
E:\3\_training_backups\f6-1h-20260720-154428\evidence
```

### 11.2 不可变归档与查询工作副本

F6-1H-R2 必须在全新运行目录建立：

```text
正式来源
  → formal_archive
      ├─ query_snapshot_run1
      └─ query_snapshot_run2
```

固定子目录为：

```text
E:\3\_training_backups\f6-1h-<YYYYMMDD-HHMMSS>\formal_archive
E:\3\_training_backups\f6-1h-<YYYYMMDD-HHMMSS>\query_snapshot_run1
E:\3\_training_backups\f6-1h-<YYYYMMDD-HHMMSS>\query_snapshot_run2
E:\3\_training_backups\f6-1h-<YYYYMMDD-HHMMSS>\evidence
```

`formal_archive` 从正式来源完整复制，来源复制前、archive、来源复制后的文件清单和 Tree SHA 必须完全一致。archive 永远不得传给 PersistentClient，不得查询或产生临时文件。

两个查询副本必须分别、独立、直接从 archive 复制；run2 禁止从 run1 复制。每个副本打开前的文件清单和 Tree SHA 必须等于 archive。每个副本只供一轮查询，打开后允许存储文件变化，不得跨轮复用。

### 11.3 工作副本变化与归档门禁

每轮查询后记录工作副本 post-open Tree SHA 和变化文件。变化文件只登记：

```text
relative_path
size_before
size_after
sha_before
sha_after
```

工作副本打开后的变化不等同正式来源或 archive 变化，不触发 archive 失败。两轮结束后必须重新计算 archive Tree SHA 和清单；与查询前不一致时状态为 `ARCHIVE_INTEGRITY_FAILED`，F6-1H 不通过，不进入 F6-1I。

### 11.4 Top-K 模型与只读边界

固定使用 12 个按表名排序的中文表注释查询，`Top-K=10`，embedding 为 `BAAI/bge-small-zh-v1.5`，collection 为 `tool_memories`。两轮分别在 run1、run2 查询。稳定结果 SHA 只纳入 query ID、rank、record ID、解析表名、规范化 document SHA 和分类，不纳入距离。

Client 只能打开 `query_snapshot_run1` 或 `query_snapshot_run2`。只允许 PersistentClient、`get_collection` 和 `collection.query`；禁止 archive Client、`get_or_create_collection`、add/update/upsert/delete、旧 Memory API 和 Apply，也不读取或导出保存的 embedding。

原始 Top-K 分别按规范化 document SHA 和当前期望表名做内存去重投影，只统计重复槽位和投影变化，不创建去重库、不修改工作副本、archive 或正式资产。检索命中 Top-1/5/10 仅作为质量旁路指标，不改变重复影响结论。

### 11.5 唯一正式命令与状态

F6-1H-R2 获得新的明确授权后，唯一正式执行命令为：

```powershell
python -m training.sop.ddl_memory_topk_impact `
  --impact-test `
  --formal-source E:\3\_runtime\vanna-level1\vanna_data `
  --run-root E:\3\_training_backups\f6-1h-<时间戳>
```

不再接受 `--snapshot`、`--source-evidence` 或 `--evidence-dir`。状态规则：

- `PASS`：来源/archive 门禁、两个副本 pre-open 门禁、两轮稳定 SHA、archive 查询后门禁均通过，且重复槽位与投影变化均为 0。
- `INCONSISTENT_WITH_F6_1G_AUDIT`：任一 Top-K 出现精确内容或表身份重复槽位。
- `ARCHIVE_INTEGRITY_FAILED`：两轮查询后 archive 文件清单或 Tree SHA 变化。
- `FAIL`：路径、复制、查询、结果数量、record ID 唯一性或两轮稳定性等其他门禁失败。

任何失败目录不得复用或自动重试。F6-1H-R1 只准备工具与 SOP，不执行该命令；正式执行必须经过 ChatGPT 审查和新的 F6-1H-R2 明确授权。

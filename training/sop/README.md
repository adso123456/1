# 训练批次静态审查器

本目录实现阶段 0B-1 的版本化训练批次契约和纯静态审查。它属于项目治理工具，不是 Vanna 官方强制流程，也不会训练、连接数据库、打开 Chroma 或写入 Memory。

主要模块：

- `batch_schema.py`：Pydantic 2 批次模型，禁止未知字段和隐式类型转换。
- `batch_validator.py`：复用项目 `SQLGuard`，检查批次契约、单语句和表清单，并计算确定性 SHA-256。
- `tools/validate_training_batch.py`：命令行入口；默认只输出到终端。

`batch_content_sha256` 覆盖通过校验的完整批次契约：顶层字段、按原顺序排列的全部样本及其工具参数、审查信息和预期行为。普通文本去除首尾空白，SQL 规范化外部空白和末尾单个分号，预期表去除 schema 前缀后转小写、排序。摘要不包含时间、绝对路径、运行环境或随机值，无效批次不生成正式摘要。

示例：

```powershell
python tools/validate_training_batch.py tools/fixtures/training_sop/valid_batch.json
```

如需 JSON 或 Markdown 结果，输出路径必须位于仓库外的临时或交付目录，且不得指向任何 `vanna_data` 或 `agent_data` 目录。

## 0B-2A：训练数据目录指纹与验证副本

`storage_snapshot.py` 提供纯文件系统能力：生成包含文件、隐藏文件和空目录的确定性清单；在源目录复制前后检查稳定性；将副本先写入目标同级临时目录，完成逐文件和整体摘要验证后再发布；以及从已验证备份复制到全新目录进行恢复副本演练。CLI 为：

```powershell
python tools/snapshot_training_store.py manifest <source>
python tools/snapshot_training_store.py backup <source> <new-destination>
python tools/snapshot_training_store.py verify <source> <copy>
python tools/snapshot_training_store.py restore-rehearsal <source> <backup> <new-restore-destination>
```

所有源和目标路径必须显式提供。工具不会搜索或默认使用 `vanna_data`、`agent_data` 或 Chroma 路径；验证副本和恢复目录必须位于项目仓库之外且原本不存在。它不导入 Chroma、SQLite 数据库客户端、数据库驱动或 Memory API。

最终目录使用平台原子 `no-replace` 发布语义；目标在发布前任何时刻出现，操作都会失败且绝不覆盖。平台缺少已实现的可靠 `no-replace` 原语时失败关闭，不降级为可能覆盖目标的普通重命名。恢复演练在源、备份和临时恢复目录三方验证全部完成前不会发布最终恢复目录。

该工具通过复制前后源目录指纹检测变化，但它不是数据库在线热备份协议。正式 T4 执行时仍必须先停止或阻断所有 Chroma 写入，确认服务和训练进程不会修改正式数据目录，再进行文件级备份。不得声称或假设该工具能在正式 Chroma 持续写入时保证事务一致性。

## 0B-3B：run_sql Tool Memory 写入计划

`memory_write_plan.py` 只建立 run_sql Tool Memory 的确定性 T5 写入计划和 T6 精确核验契约，不连接、不查询、不写入任何 Chroma，也不实现真实执行器或回滚器。计划生成必须复用 `validate_training_batch()` 的完整校验和规范化摘要，并要求调用者提供的批准批次摘要与实际摘要完全一致。

正式 Memory 记录 ID 是 `toolmem-v1-<memory_content_sha256>` 全局内容寻址 ID，只覆盖规范化 question、tool name、args、success 和记录版本，不包含批次摘要、`sample_id`、时间、路径或随机值。批次交付身份通过 `delivery_item_sha256` 和后续执行账本表达；治理字段 `created_by_*` 只表示首次创建该 Memory 的批次，不表示后续引用批次。相同内容若已由其他批次创建，会阻断当前声明“预计新增”的批次，不会静默复用、覆盖或生成另一个记录 ID。

本阶段仅覆盖 run_sql Tool Memory。Level 1 Text Memory、DDL Memory 和图表训练 Memory 的受控交付能力尚未建立；其中 Level 1 Text Memory 路径必须在 0B-4 前另行完成。

## 0B-3C：受控 Chroma Tool Memory 适配层

`chroma_tool_memory_adapter.py` 只建立 run_sql Tool Memory 的受控 Chroma 适配层，为后续 T5 创建、T6 精确核验和回滚提供全量清点、确定性 ID 精确读取、受控创建、批次精确查询和精确删除能力；它不是正式批次执行器，也不执行正式训练。

适配层集中封装 Vanna 私有 collection 访问，普通训练代码不得直接访问。正式记录创建使用计划中的确定性 ID 和 `add`，不使用随机 UUID、`upsert` 或现有 `save_tool_usage()`。写入前的全量内容索引用于识别旧 UUID、同内容重复和内容寻址冲突，不能只按确定性 ID 查询结果判断记录不存在；适配层不会自动迁移、删除或重写旧记录。

向量检索只用于验证现有问答召回和 compatibility metadata 的兼容性，不用于幂等判断、T6 精确核验、回滚定位或重复检测。本阶段适配层只能运行在 Git 仓库外的隔离 Chroma，拒绝正式 `vanna_data`、`agent_data`、符号链接、junction 和 reparse point，且没有绕过参数。Level 1 Text Memory 能力仍未建立。

合法受控记录的首次创建批次 metadata 不需要与当前计划一致。适配层按 Memory 内容身份、确定性 ID 和存储结构确认记录有效后，使用存储中的首次创建字段生成 `ExistingRecordSnapshot`，再由 0B-3B 写入计划判定 `resume_same_batch` 或 `preexisting_other_batch`。只有 Memory 内容身份、确定性 ID 或存储结构不一致时才属于 content conflict；当前计划的来源、审查原因、样本编号或首次创建批次不同不属于内容冲突。

## 0B-3C-M1：旧 UUID Tool Memory 迁移计划

`legacy_tool_memory_migration_plan.py` 是纯逻辑计划模块，只覆盖 64 条旧 UUID `run_sql` Tool Memory；8 条 Text Memory 不在迁移项、删除集合或回滚集合内。模块不打开 Chroma，不创建、修改或删除记录，也不是迁移执行器。

迁移采用两个独立人工门禁。阶段 A 仅规划创建并验证确定性记录，保留全部旧 UUID；阶段 B 必须另行人工批准后，才允许把精确的旧 UUID 集合作为可执行删除集合。阶段 B 不具备批量事务原子性，未来执行时必须分别记录已删除与未删除集合，并依赖迁移前验证备份和执行账本支持人工恢复决策。

计划使用两层摘要避免自引用：`migration_plan_content_sha256` 覆盖尚未填入 `created_by_batch_content_sha256` 的原始计划材料；新记录的 `created_by_batch_content_sha256` 使用该内容摘要；`migration_plan_sha256` 再覆盖填充治理字段后的最终完整计划。`created_by_*` 表示首次创建受控确定性记录的迁移批次，不代表旧 UUID 记录不可恢复的原始历史批次。

缺失的历史说明字段不会被虚构，而是记录在 `legacy_missing_fields`。正式迁移完成并经过验证前，0B-3D 继续阻断。

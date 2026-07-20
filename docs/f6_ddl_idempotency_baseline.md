# F6-1A DDL Text Memory 写入链审计与隔离复现基线

## 1. 审计边界与结论

- F6-1A 初始基线提交：`559a990f60dca2071f106ced145609f991ac4b3b`；R1 修复基线提交：`5be951504d5026190fff5808ead7db819b74758b`。
- 正式 Chroma：`E:\3\_runtime\vanna-level1\vanna_data`。本脚本以正式路径创建 Chroma Client 的尝试次数为 **0**；该字段不代表操作系统级全局监控结果。
- 仓库内 `vanna_data` 仅作为受保护脏状态记录，未打开、未修改。
- R1 隔离 Evidence：`E:\3\_training_backups\f6-1a-r1-20260720-124151\evidence`。
- 结论：当前写入链没有逻辑对象身份或内容查重。Vanna 每次生成新 UUID4，再以该新 ID 调用 Chroma `upsert`，所以相同 DDL 第二次执行仍新增记录。

## 2. 真实生成与写入链

以下结论来自实际代码，不来自路线文档推断。

```text
agent_data/column_metadata_index.json
  → train_step3.load_metadata_index（读取当前 Level 1 字段索引）
  → train_step3.group_tables（按表名稳定排序、字段保持输入顺序）
  → train_step3.build_table_ddl（生成单表 DDL Text Memory）
  → train_step3.build_all_table_ddls（生成 115 条）
  → train_step3._run_training
  → agent_config.create_memory
  → ChineseChromaAgentMemory / ChromaAgentMemory.save_text_memory
  → Chroma collection.upsert
  → tool_memories collection
```

代码证据：

- `train_step3.py:15-18`：当前输入为 `agent_data/column_metadata_index.json`，预期 115 表。
- `train_step3.py:25-31`：`load_metadata_index` 读取输入文件。
- `train_step3.py:42-92`：`group_tables` 构造稳定的表输入。
- `train_step3.py:99-137`：`build_table_ddl` 构造 DDL Text Memory，并排除 geometry/geography 字段。
- `train_step3.py:140-167`：`build_all_table_ddls` 生成全量 DDL。
- `train_step3.py:248-283`：`_run_training` 通过 `create_memory()` 调用 `save_text_memory`。
- `agent_config.py:18-19,120-125`：隔离路径来自 `VANNA_DATA_DIR`，`create_memory` 未显式传 collection 名称。
- `vanna_src/src/vanna/integrations/chromadb/agent_memory.py:102-106`：默认 collection 名为 `tool_memories`。
- `vanna_src/src/vanna/integrations/chromadb/agent_memory.py:160-164`：`_create_memory_id` 每次生成 UUID4。
- `vanna_src/src/vanna/integrations/chromadb/agent_memory.py:333-352`：每次调用生成 ID 和时间戳，并执行 `collection.upsert`。

历史来源变化：提交 `66fe0c2` 的 `train_step3.main` 曾直接通过 `PostgresRunner` 从 `information_schema.columns` 与 `pg_class/pg_attribute/col_description()` 只读提取 6 表结构和注释，再调用同一 `save_text_memory`。提交 `5542183` 将当前入口改为从已生成的 `column_metadata_index.json` 构造 115 表 DDL。因此当前真实输入入口是仓库文件，旧数据库提取逻辑只存在于 Git 历史。

## 3. 当前存储行为

| 项目 | 当前行为 |
|---|---|
| Memory API | `ChromaAgentMemory.save_text_memory(content, context)` |
| Chroma API | `Collection.upsert(ids, documents, metadatas)` |
| Collection | `tool_memories` |
| ID 来源 | Vanna `_create_memory_id()` 生成 UUID4；调用方不能提供 ID |
| Metadata | `content`、`timestamp`、`is_text_memory` |
| DDL 专属 Metadata | 无 |
| 写前重复判断 | 无 |
| 逻辑对象身份 | 无 |
| 内容指纹 | 无 |

`upsert` 只会在 ID 相同时更新。由于每次调用都先生成不同 UUID4，第二次相同内容不会命中第一次的 ID，实际行为是新增。直接根因不是 Chroma 不支持 upsert，而是当前调用链没有提供稳定 ID，也没有在调用前按逻辑对象和内容进行计划判断。

## 4. 写入入口盘点

当前工作树中，DDL 批量写入入口只有 `train_step3.py`。以下代码路径也需要在后续治理中明确边界：

- Git 历史提交 `66fe0c2` 中的旧 `train_step3.main` 使用数据库提取后直接写入；它不与当前脚本并存，但可从历史恢复后执行。
- `vanna_src/src/vanna/tools/agent_memory.py:269-291` 的 `SaveTextMemoryTool` 能调用同一 API；当前 `step4_server.py:144-156` 只注册 `GuardedRunSqlTool`，因此该工具当前不在生产 Agent 中可达。
- Level 2/3 训练脚本调用的是 Tool Memory 写入链，不是 DDL Text Memory 写入入口。

因此，未发现多个“当前工作树中同时可达”的旧 DDL 批量入口，但底层通用 API 和可恢复的历史入口仍可绕过未来规则。后续唯一受控入口应是 F6-1C/F6-1D 的 plan/apply 链；`train_step3.py` 只负责确定性输入生成，或改为调用该受控入口，不再直接写 Chroma。

## 5. 隔离复现

历史约 25 条输入文件在当前仓库中不存在。本次复用当前真实 Level 1 输入和真实 DDL 构造函数：对 115 条生成结果按 `table` 升序排列，稳定选取前 25 条。写入调用复用真实 `agent_config.create_memory` 和 `ChromaAgentMemory.save_text_memory`。

隔离目录：

```text
E:\3\_training_backups\f6-1a-r1-20260720-124151\isolated_chroma
```

结果：

```text
before_count=0
first_run_created=25
after_first_count=25
second_run_created=25
after_second_count=50
duplicate_group_count=25
duplicate_record_count=50
duplicate_excess_record_count=25
unique_memory_id_count=50
record_id_strategy=Vanna 每次生成 UUID4，两轮 50 个 ID 全部唯一
reproduction_validation=PASS
```

分组规则只执行约定的 DDL 规范化：CRLF/CR 转 LF、去除文件首尾空白、去除行尾空白。有效 Metadata 排除每次写入生成的 `timestamp`，并排除已经由 document 表达的 `content`，保留 `is_text_memory=true`。

- 按“规范化内容 + 有效 Metadata”分组：25 个重复组，共 50 条，重复净增 25 条。
- 按包含 `timestamp` 的精确存储 Metadata 分组：0 个重复组。该差异仅来自运行时时间戳，不代表 DDL 或业务语义变化。
- Evidence 不保存完整 DDL、数据库数据、查询结果、密码或 API Key，只保存表名、内容哈希、计数和结构证据。

R1 增加了强制结果门禁：`validate_reproduction_result()` 必须同时确认空库起点、两轮增长、最终数量、重复分组、唯一 ID 数和 collection 名称。任一条件不满足时，脚本先在 Evidence 中记录 `FAIL` 和明确原因，再以非零退出；只有全部条件满足才输出 `reproduction_validation=PASS`。

隔离路径门禁拒绝正式 Chroma 及任意子目录、仓库内 `vanna_data` 及任意子目录，以及项目仓库内任意路径。路径在创建 Client 前完成解析和校验，隔离目录还必须全新或为空。

## 6. F6-1B～I 身份与 Plan/Apply 设计基线

本阶段冻结设计，不实现适配器：

```text
logical_id =
sha256("ddlmem-v1|source_id|schema_name|object_type|object_name")

content_fingerprint =
sha256(canonical_json(normalized_ddl, effective_metadata))
```

语义：

- `logical_id` 表示逻辑数据库对象，DDL 内容变化时保持不变。
- `content_fingerprint` 判断该逻辑对象内容是否变化。
- DDL 规范化只处理 CRLF/LF、文件首尾空白和行尾空白；不得重排字段、约束、SQL Token 或 DDL 语义。
- 状态必须支持 `create`、`unchanged`、`changed`、`removed`。
- `removed` 只能进入计划，不得自动删除正式 Memory。
- 写入必须拆分为 `plan` 和 `apply`。
- `apply` 默认拒绝正式 Chroma 路径。
- 正式治理优先生成完整候选副本，验收后再切换，不在正式库原地批量删除。

实际代码存在一个明确的接口冲突：`ChromaAgentMemory.save_text_memory` 不接受调用方 ID，也不接受 DDL 专属 Metadata。等价实现不能继续直接使用该公共方法；F6-1D 需要通过受控适配层向 Chroma 写入 `logical_id`，并持久化 `source_id/schema_name/object_type/object_name/content_fingerprint` 等有效 Metadata，或为 Vanna 增加等价的显式 ID/Metadata 接口。无论采用哪种方式，上述身份和 plan/apply 原则不变。

`source_id`、`schema_name`、`object_type` 和 `object_name` 在进入哈希前必须由 plan 阶段校验为非空、稳定的规范值。建议当前 PostgreSQL 使用固定 `source_id=postgres_water`、`schema_name=public`、`object_type=table`；最终值需在 F6-1B 授权后结合多数据源命名规范确认，不能由 apply 临时推断。

## 7. 风险与下一阶段

### BLOCKING_RISK

1. `agent_config.py` 在未设置 `VANNA_DATA_DIR` 时默认指向仓库内 `vanna_data`。任何直接导入并调用 `create_memory()` 的训练脚本都可能打开错误资产。F6-1D 的 apply 必须先做路径门禁，再导入或创建 Memory；本阶段审计工具已这样规避。最小后续修复位置：`agent_config.py` 与 DDL apply 入口，训练/治理模式要求显式路径。
2. 当前 `save_text_memory` 的 `timestamp` 使精确 Metadata 比较永远变化，且没有 DDL 类型、来源和对象字段。F6-1B/F6-1D 必须定义“有效 Metadata”和“运行时 Metadata”的边界。
3. 当前 collection 混存 Text Memory 与 Tool Memory。正式治理必须按完整副本验收，不能按文本相似度或单条 ID 在正式库原地删除。

下一阶段建议：等待 F6-1B 明确授权后，只实现身份输入规范和确定性测试；不得在本阶段治理正式 198 条记录，也不得提前实现 `ddl_memory_plan.py` 或 `ddl_memory_adapter.py`。

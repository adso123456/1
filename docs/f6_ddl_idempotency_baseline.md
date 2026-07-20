# F6-1A/F6-1B DDL Text Memory 审计与确定性身份基线

## 1. 审计边界与结论

- F6-1A 初始基线提交：`559a990f60dca2071f106ced145609f991ac4b3b`；R1 修复基线提交：`5be951504d5026190fff5808ead7db819b74758b`；F6-1B 基线提交：`f923d57ca747dff25f551bb8f1c9a28ecbbe96ad`。
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

## 6. F6-1B 确定性身份规范（已冻结）

实现位置：`training/sop/ddl_memory_identity.py`。模块只使用标准库纯函数，不导入 Chroma、Vanna 或 `agent_config`，不读取环境变量、数据库或文件系统。

### 6.1 身份输入与校验

不可变输入 `DdlMemoryIdentityInput` 固定包含：

```text
source_id
schema_name
object_type
object_name
```

- `source_id` 由调用方显式传入，必须符合 `[a-z][a-z0-9_-]{0,63}`，拒绝大写和空值。当前 PostgreSQL 调用方约定传入 `postgres_water`。
- `object_type` 当前只允许 `table`，不静默接受或转换其他类型。
- `schema_name`、`object_name` 必须为非空字符串，去除首尾空白后仍非空；拒绝控制字符、换行、NUL 和 `|`。
- `schema_name`、`object_name` 保留大小写，不自动转小写，不做 Unicode 语义归一化，不修改 PostgreSQL 标识符语义。因此 `Demo` 与 `demo` 是不同身份。
- 当前 PostgreSQL 的 `schema_name=public`、`object_type=table` 只是调用方显式输入值；模块不根据路径、环境变量或数据库推断。

### 6.2 DDL 规范化

`normalize_ddl()` 只执行：CRLF/CR 转 LF、删除每行末尾空格和 Tab、删除整个文本首尾空白。空 DDL 被拒绝。函数不重排字段、约束或 SQL Token，不改变大小写，不删除注释，不格式化 SQL。

### 6.3 身份与指纹算法

```text
identity_key =
"ddlmem-v1|source_id|schema_name|object_type|object_name"

logical_id =
sha256(identity_key)

record_id =
"ddlmem-v1-" + logical_id

content_fingerprint =
sha256(canonical_json(normalized_ddl, effective_metadata))
```

`logical_id` 是小写 64 位十六进制，`record_id` 可作为后续 Chroma 显式 ID。同一逻辑对象的 DDL 变化不改变二者；任一身份字段变化都会改变二者。

`canonical_json` 固定把 `[normalized_ddl, effective_metadata]` 编码为 UTF-8 JSON，key 排序，使用紧凑分隔符 `,` 和 `:`，不依赖字典插入顺序。

v1 有效 Metadata 只允许以下固定字段，不支持调用方追加：

```text
memory_type = ddl_text
identity_version = ddlmem-v1
source_id
schema_name
object_type
object_name
logical_id
record_id
```

其中不包含 timestamp、created_at、updated_at、随机 UUID、路径、运行批次时间、conversation_id 或 request_id。

### 6.4 后续治理原则

- 状态必须支持 `create`、`unchanged`、`changed`、`removed`。
- `removed` 只能进入计划，不得自动删除正式 Memory。
- 写入必须拆分为 `plan` 和 `apply`。
- `apply` 默认拒绝正式 Chroma 路径。
- 正式治理优先生成完整候选副本，验收后再切换，不在正式库原地批量删除。

实际代码仍存在接口冲突：`ChromaAgentMemory.save_text_memory` 不接受调用方 ID，也不接受 DDL 专属 Metadata。后续适配层需要写入 `record_id`，并持久化上述有效 Metadata 和 `content_fingerprint`。F6-1B 阶段未实现 Plan、Apply、Chroma 适配器或任何正式写入。

F6-1A 审计工具已复用该模块的 `normalize_ddl` 和身份构造接口。兼容回归 Evidence 为 `E:\3\_training_backups\f6-1b-20260720-125411\evidence`，结果仍为 `0 → 25 → 50`、25 个重复组、50 个唯一 ID，未产生新的技术结论。

## 7. F6-1C 确定性 Plan 规范（已实现）

实现位置：`training/sop/ddl_memory_plan.py`。模块只依赖标准库和 `ddl_memory_identity.py`，不导入或创建 Chroma/Vanna Client，不读取环境、数据库或文件系统，不执行 Memory 写入、更新或删除。

### 7.1 输入与 managed 边界

- 期望输入复用不可变 `DdlMemoryIdentity`。
- 现有输入使用只读快照 `ExistingDdlMemoryRecord(record_id, document, metadata)`，不依赖 Chroma 返回对象或 Vanna 类型。
- 只有 `memory_type=ddl_text`、`identity_version=ddlmem-v1` 且 `record_id` 符合 `ddlmem-v1-<64位小写SHA256>` 的完整有效记录才是 managed。
- 旧 UUID Text Memory、Tool/Legacy Memory、其他 identity version 和无关记录均为 unmanaged；只计数保留，不参与 create/changed/removed，也不进入删除计划。
- 声称 `identity_version=ddlmem-v1` 但 ID、身份字段、必需 Metadata 或指纹非法的记录属于结构冲突，直接失败，不能降级为 unmanaged 或 changed。

现有 managed 存储 Metadata 必须包含 F6-1B 的八个有效身份字段及 `content_fingerprint`。timestamp、request_id 等额外运行时 Metadata 被忽略，不影响 Plan。

### 7.2 四种 Action

```text
create    期望 record_id 不存在于 managed 现有集合
unchanged record_id、document、content_fingerprint 和身份 Metadata 均一致
changed   相同 record_id 的 DDL 或 content_fingerprint 变化
removed   managed 现有 record_id 不在期望集合，仅生成计划
```

`create` 只有目标指纹，`unchanged` 包含相同的新旧指纹，`changed` 同时包含旧、新指纹，`removed` 只有旧指纹且不携带目标 DDL 或目标 Metadata。`create/changed` 携带后续 Apply 所需的规范化 DDL 和固定目标 Metadata，但本阶段不执行 Apply。

### 7.3 冲突失败规则

以下情况直接抛出明确异常：期望集合重复 `record_id` 或重复逻辑对象；现有 managed 集合重复 `record_id`；顶层 ID、Metadata `record_id`、`logical_id` 或身份字段不匹配；缺少必需 Metadata；`content_fingerprint` 非小写 64 位十六进制，或与记录自身 document/身份 Metadata 的重算结果不一致；记录声称 v1 但无法通过身份模块校验。结构损坏不得归类为 changed。

### 7.4 Plan 输出与 SHA

不可变 `DdlMemoryPlan` 固定包含：

```text
plan_version = ddl-memory-plan-v1
desired_count
managed_existing_count
unmanaged_existing_count
create_count
unchanged_count
changed_count
removed_count
actions
plan_sha256
```

actions 按 `record_id` 升序排列。`plan_sha256` 是不含自身字段的稳定 Plan payload 经 key 排序、紧凑分隔符 canonical JSON UTF-8 编码后的 SHA-256；payload 不含时间、路径、随机值或运行环境信息。输入列表顺序和额外运行时 Metadata 不改变 Plan 或 SHA。

F6-1C Evidence：`E:\3\_training_backups\f6-1c-20260720-135537\evidence`。本阶段没有 Apply，没有创建 Chroma Client，也没有打开正式 Chroma。

## 8. F6-1D 隔离 Chroma 适配层（已实现）

实现位置：`training/sop/ddl_memory_adapter.py`，collection 固定为 `tool_memories`。适配层只提供：

```text
snapshot_records()
create_from_action(action=create)
replace_from_action(action=changed)
open_isolated_adapter(persist_directory)
```

没有 delete、remove、apply_plan 或完整 Apply 接口，不接受 unchanged/removed Action，也不调用 `save_text_memory()` 或生成随机 Memory ID。

### 8.1 隔离打开与快照

`open_isolated_adapter()` 在创建任何 Client 前验证调用方显式路径：必须位于 `E:\3\_training_backups` 下、仓库外、正式 Chroma 及其子目录之外、仓库 `vanna_data` 及其子目录之外，并且首次打开时全新或为空。通过后才延迟导入 `ChineseChromaAgentMemory` 与共享 `EMBEDDING_FUNCTION`，显式传入隔离路径和 `tool_memories`，保持 `BAAI/bge-small-zh-v1.5` 配置一致，不使用 `VANNA_DATA_DIR` 默认值。

`snapshot_records()` 读取全部 ID、document、Metadata，检查数组长度一致，保留 managed/unmanaged 及额外运行时 Metadata，转换为 `ExistingDdlMemoryRecord` 后按 `record_id` 排序；不修改任何记录。

### 8.2 Action 完整性

每次写入前根据 Action 的四个身份字段与 `normalized_ddl` 重新构造 `DdlMemoryIdentity`，逐项验证 `logical_id`、`record_id`、DDL、目标指纹和目标 Metadata。任何伪造或篡改在 collection 写入前失败。

### 8.3 create 原语

- 只接受 `create`，旧指纹必须为 `None`。
- 相同 `record_id` 必须不存在；存在即冲突，不覆盖。
- 使用 collection `add` 显式写入确定性 `record_id`、规范化 DDL 和固定存储 Metadata。
- 不写 timestamp、随机 UUID、请求 ID或路径。
- 写后精确读取，记录总数必须增加 1，并由 Plan 判为 `unchanged`。

### 8.4 replace 原语与 stale 门禁

- 只接受 `changed`，必须同时携带旧、新指纹。
- 当前记录必须存在且是合法 managed v1；替换前重新读取并要求当前指纹等于旧指纹。
- 使用相同确定性 `record_id` 调用 collection `update`，不得新增记录。
- 写后精确读取，记录总数必须不变，并由 Plan 判为 `unchanged`。
- stale Action 明确失败，不覆盖新状态，不改变 collection 数量。

本阶段没有数据库级事务；并发安全底线是写入前的乐观旧指纹条件与写后精确验证。删除及 removed 治理仍留给后续完整候选副本流程。

真实隔离集成使用 `E:\3\_training_backups\f6-1d-20260720-141542\isolated_chroma`：核心计数为 `0 → 1 → 2 → 2 → 2`，create/replace 后均为 unchanged，stale 冲突生效，unmanaged 记录未变化，关闭重开后两条记录仍存在。Evidence：`E:\3\_training_backups\f6-1d-20260720-141542\evidence`。本脚本以正式路径创建 Chroma Client 的尝试次数为 0；该字段不代表系统级监控。

## 9. 风险与下一阶段

### BLOCKING_RISK

1. `agent_config.py` 在未设置 `VANNA_DATA_DIR` 时默认指向仓库内 `vanna_data`。隔离适配层已在延迟导入前强制路径门禁；后续完整 Apply 必须只经该显式隔离工厂打开候选库，不得回退到默认 `create_memory()`。
2. 当前 `save_text_memory` 会生成 UUID 和 timestamp。F6-1D 适配层已绕过该 API，以显式 `record_id` 实现固定存储契约；后续完整 Apply 不得重新调用旧 API。
3. 当前 collection 混存 Text Memory 与 Tool Memory。正式治理必须按完整副本验收，不能按文本相似度或单条 ID 在正式库原地删除。

下一阶段建议：等待 F6-1E 明确授权。本阶段不治理正式 198 条记录，不执行完整 Apply、unchanged 或 removed。

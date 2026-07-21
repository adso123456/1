# F6-1A～F6-1F DDL Text Memory 审计、身份与隔离幂等基线

## 1. 审计边界与结论

- F6-1A 初始基线提交：`559a990f60dca2071f106ced145609f991ac4b3b`；R1 修复基线提交：`5be951504d5026190fff5808ead7db819b74758b`；F6-1B 基线提交：`f923d57ca747dff25f551bb8f1c9a28ecbbe96ad`；F6-1E 基线提交：`b7d2b576e945d0574c1a6750a0d434454deddcfd`。
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

## 9. F6-1E 受控 Apply 编排器（已实现）

实现位置：`training/sop/ddl_memory_apply.py`，版本固定为 `ddl-memory-apply-v1`。公开接口为：

```text
apply_ddl_memory_plan(adapter, desired_memories, expected_plan)
```

三个输入均由调用方显式提供：F6-1D `DdlMemoryChromaAdapter`、期望 `DdlMemoryIdentity` 集合、已经审阅的 `DdlMemoryPlan`。模块不读取路径、环境变量、数据库或正式资产；真实集成只能经 F6-1D 隔离工厂打开仓库外候选库。

### 9.1 写前重验

任何写入前必须依次验证 `plan_version=ddl-memory-plan-v1`、`plan_sha256` 格式，重新读取当前完整快照，并使用当前快照与期望集合重建 Plan。重建结果必须与传入计划的全部字段、Action 顺序和 SHA 完全一致；同时逐项确认 create ID 当前不存在、其余 Action ID 当前存在。任一条件不满足均抛出 `DdlMemoryApplyPreconditionError`，其 `writes_started=false`、已完成 Action 为空，不执行任何写入。

### 9.2 四种 Action

```text
create    → adapter.create_from_action → created
unchanged → 不写入                       → verified_noop
changed   → adapter.replace_from_action → replaced
removed   → 不删除、原样保留             → retained / removal_deferred
```

Action 严格按 Plan 的稳定顺序执行。每项执行后立即核对记录数：create 必须 `+1`，changed、unchanged、removed 必须为 `0`。编排器不提供 delete/remove，不调用 `collection.delete`，也不调用旧 `save_text_memory`。

### 9.3 写后验收与结果

Apply 后重新读取完整快照并重建 Plan，强制要求 `create=0`、`changed=0`、`unchanged=desired_count`，且 `removed` 数量与输入计划一致。unmanaged 记录和 retained removed 记录的 ID、document、Metadata 必须逐条完全不变；collection 总量只能增加输入计划的 create 数。

不可变 `DdlMemoryApplyResult` 包含输入/最终 Plan SHA、前后计数、四种 outcome 计数、前后 unmanaged 计数、按 Plan 顺序排列的逐项 outcome，以及排除时间、路径和随机值后生成的稳定 `apply_result_sha256`。

### 9.4 失败语义与范围

写入中或写后验收失败时抛出 `DdlMemoryApplyExecutionError`，报告 `writes_started`、已完成 outcomes、失败 `record_id`，并标记候选库不可验收；不自动重试、不回滚、不删除。该语义不等同数据库事务，候选库失败后必须废弃并从全新隔离目录重建。

F6-1E 只验证 1 条 unmanaged、1 条 unchanged、1 条 changed-old、1 条 removed 与 3 条期望记录组成的最小隔离场景：初始 Plan 四种 Action 各 1，记录数 `4 → 5`；最终 Plan 为 `create=0, unchanged=3, changed=0, removed=1`。旧计划再次使用在零写入门禁失败；使用新计划再次 Apply 只有 3 个 verified_noop 和 1 个 retained，记录数不变。正式 115 条验证留给 F6-1F，正式 198 条 Chroma 未打开、未治理。

F6-1E Evidence：`E:\3\_training_backups\f6-1e-20260720-144046\evidence`。

## 10. F6-1F 115 条全量隔离幂等验收（已完成）

实现位置：`training/sop/ddl_memory_idempotency_acceptance.py`。轻量 Runbook：`docs/sop/ddl_memory_idempotency_acceptance.md`。

真实输入只调用 `train_step3.load_metadata_index()`、`group_tables()` 和 `build_all_table_ddls()`：读取 2572 条 Metadata，稳定生成 115 张表、115 条 DDL。固定身份为 `source_id=postgres_water`、`schema_name=public`、`object_type=table`、`object_name=table`。表名、logical ID、record ID 均为 115 个唯一值；这些门禁在创建隔离 Chroma Client 前完成。

全新隔离库两轮结果：

```text
第一轮 Plan：desired=115, managed=0, unmanaged=0,
              create=115, unchanged=0, changed=0, removed=0
第一轮 Apply：created=115, verified_noop=0, replaced=0,
               retained_removed=0, count=0→115

关闭重开后的第二轮 Plan：desired=115, managed=115, unmanaged=0,
                           create=0, unchanged=115, changed=0, removed=0
第二轮 Apply：created=0, verified_noop=115, replaced=0,
               retained_removed=0, count=115→115

再次关闭重开：total=115, managed=115, unmanaged=0,
               create=0, unchanged=115, changed=0, removed=0
```

语义快照按 `record_id` 排序，每条只纳入 record ID、document SHA-256 和稳定 managed Metadata，不纳入 embedding、时间、绝对路径或完整 DDL。三次快照 SHA 均为：

```text
0de14c2abac3f83e83e8652799545b73ae90bcfa9f5fa5b388cb4084570c180d
```

最终 `duplicate_record_id_groups=0`、`duplicate_logical_id_groups=0`、`duplicate_identity_key_groups=0`。本脚本以正式路径创建 Chroma Client 的尝试次数为 0；未打开或治理正式 Chroma。

Evidence：`E:\3\_training_backups\f6-1f-20260720-150243\evidence`。

## 11. F6-1G-A 正式只读审计准备（已完成，待审查）

新增工具 `training/sop/ddl_memory_formal_readonly_audit.py` 和 SOP `docs/sop/ddl_memory_formal_readonly_audit.md`。本阶段只运行内存合成记录、仓库内真实 115 条期望集合生成和系统临时目录复制自检；没有执行 `--formal-audit`，没有对正式 Chroma 做存在性判断、目录读取、哈希、复制、计数或 Client 打开。

未来 F6-1G-B 执行模型已经冻结：正式来源必须精确等于 `E:\3\_runtime\vanna-level1\vanna_data`，运行目录必须为全新的 `E:\3\_training_backups\f6-1g-<时间戳>`。先计算正式来源相对文件清单和 Tree SHA，再完整复制到 `formal_snapshot`，随后强制验证：

```text
formal_source_tree_sha256_before
= snapshot_tree_sha256
= formal_source_tree_sha256_after
```

只有清单与三个 SHA 完全一致，才允许通过不暴露写方法的只读包装器打开快照中的既有 `tool_memories`。禁止自动创建 collection、读取 embedding，以及 add/update/upsert/delete、旧 Memory API 或 Apply。

正式记录未来按精确规则分为 `expected_exact_match`、`expected_table_content_variant`、`unexpected_ddl`、`non_ddl_memory`，并单独登记 `missing_expected_table`。精确重复按规范化 document SHA 分组；表身份重复按解析出的当前期望表名分组。随机 UUID、timestamp、request_id、conversation_id 不参与重复判断，内容变体不得自动视为可删除重复。

合成自检覆盖期望集合唯一性、精确重复、内容变体、缺失、非预期 DDL、非 DDL、timestamp 无关性、输入顺序稳定、分类对账、复制 Tree SHA、来源变化失败、只允许快照路径、只读能力扫描以及导入零 Client。结果：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
```

开发 Evidence：`E:\3\_training_backups\f6-1g-a-20260720-152700\evidence`。F6-1G 正式审计尚未执行，F6-1G-B 尚未开始。

## 12. F6-1G-B 正式快照只读审计（已完成）

严格按 `docs/sop/ddl_memory_formal_readonly_audit.md` 执行一次正式只读审计。正式来源只进行了遍历、读取、文件大小/SHA 计算和完整复制；Chroma Client 只打开仓库外 `formal_snapshot`，没有直接指向正式路径，没有执行 Memory 写入、替换、删除、Apply、embedding 读取或 Top-K 测试。

来源与快照 Tree SHA：

```text
formal_source_tree_sha256_before = ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
snapshot_tree_sha256             = ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
formal_source_tree_sha256_after  = ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
```

三份文件清单与 Tree SHA 完全一致。正式路径 Client 打开尝试为 0，快照 Client 打开次数为 1。

正式 `tool_memories` collection 实际总数为 198，与已知基线一致，审计状态为 `PASS`。只读分类结果：

```text
DDL 候选记录                  115
期望精确匹配记录              115
期望精确匹配表                115
缺失期望表                      0
期望表内容变体                  0
非预期 DDL                      0
非 DDL Memory                  83
精确重复组 / 记录 / 净冗余       0 / 0 / 0
表身份重复组 / 记录               0 / 0
classification_sha256          f4f2e5aaf59d93317ee3dae1316f21979ed9cf13f69f1794fff43161be67d76d
```

分类总数 `115 + 83 = 198`，能够与 collection 总数对账。Evidence：`E:\3\_training_backups\f6-1g-20260720-153450\evidence`。Evidence 与仓库文档均未保存完整 DDL、embedding 或完整 Metadata 敏感值。

## 13. F6-1H-R1 不可变归档与双查询副本模型（已完成）

F6-1H 首次执行在任何 Chroma 导入、Client 打开或 Top-K 查询前停止。固定旧快照当前 Tree SHA 为 `70a3fd3a72a00fe131aedbe289e1fbe9cfff66d80126de3277c0aa3a1abc25b7`，不同于 F6-1G 复制时的 `ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f`；变化涉及 `chroma.sqlite3`、`data_level0.bin`、`length.bin`。失败状态登记为 `SNAPSHOT_INTEGRITY_GATE_FAILED`，Evidence 保留于 `E:\3\_training_backups\f6-1h-20260720-154428\evidence`，该目录不得复用。

首次停止时正式来源文件系统访问次数为 0，正式 Client 与快照 Client 打开次数均为 0；未创建 Top-K 工具、未执行查询、未修改文档、未提交。F6-1G 旧 `formal_snapshot` 保留为已经被 Client 打开过的审计工作副本，不删除、不修改，但不再作为不可变归档或 F6-1H 查询输入。

R1 新增 `training/sop/ddl_memory_topk_impact.py`，并在现有 `docs/sop/ddl_memory_formal_readonly_audit.md` 中冻结未来 R2 模型：

```text
正式来源
→ formal_archive（不可变，永不由 Client 打开）
   ├→ query_snapshot_run1（独立一次性查询副本）
   └→ query_snapshot_run2（独立一次性查询副本）
```

正式来源复制前、archive、正式来源复制后的文件清单和 Tree SHA 必须一致。两个查询副本必须分别直接从 archive 复制，pre-open SHA/清单均等于 archive；禁止 run2 从 run1 复制。工作副本打开后的文件变化只记录相对路径、前后大小和 SHA，不误判为 archive 变化。两轮结束后 archive 必须与查询前完全一致，否则为 `ARCHIVE_INTEGRITY_FAILED`。

Top-K 仍固定 12 个中文表注释查询、`Top-K=10`、`BAAI/bge-small-zh-v1.5` 和 `tool_memories`。两轮稳定 SHA 排除距离，只纳入 query ID、rank、record ID、解析表名、document SHA 和分类。精确内容与表身份投影仍仅在内存计算，不创建去重库。

R1 只使用系统临时目录和 Fake Query Collection 自检；没有访问正式来源、旧快照，没有创建 Client、archive 或查询副本，也没有执行 Top-K：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
TOPK_EXECUTED=NO
```

R1 Evidence：`E:\3\_training_backups\f6-1h-r1-20260720-155539\evidence`。

## 14. F6-1H-R2 双副本 Top-K 重复影响评估（已完成）

2026-07-20 严格按批准 SOP 对正式来源执行一次“只读哈希与复制”，全部 Chroma 查询仅发生在两个独立仓库外副本。评估状态为 `PASS`，未调用 Memory 写入、替换或删除接口，也未直接以正式路径创建 Chroma Client。

```text
formal_source_tree_sha_before       ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
archive_tree_sha                    ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
formal_source_tree_sha_after        ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
query_run1_preopen_tree_sha         ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
query_run2_preopen_tree_sha         ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
query_run1_postopen_tree_sha        719d771ed5c7dde193ce95ba65cf9edfbb5ab15b2b15b50f65750ec7bef5f0e5
query_run2_postopen_tree_sha        838612c21a3347f6bd33e7b86afd5931dfce71aac1b50d38b793e1166aea4e98
formal_archive_tree_sha_after       ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
query_run1_changed_file_count       3
query_run2_changed_file_count       3
query_count / top_k                 12 / 10
total_exact_duplicate_slots         0
total_table_duplicate_slots         0
exact_projection_changed_queries    0
table_projection_changed_queries    0
expected_table_top1/top5/top10      3 / 9 / 10
first_run_result_sha256             6b0709607ed127b5c67499920f7edf20800a5dc280021d844beb397c3cdcd7d6
second_run_result_sha256            6b0709607ed127b5c67499920f7edf20800a5dc280021d844beb397c3cdcd7d6
duplicate_topk_impact               NONE
formal_chroma_client_open_attempts  0
snapshot_chroma_client_open_count   2
```

两个查询副本打开后各有 `chroma.sqlite3`、`data_level0.bin`、`length.bin` 三个文件的 SHA 改变；该副作用只发生在一次性查询副本中。不可变归档查询后 SHA 未变，两轮稳定结果 SHA 相同，重复记录槽位及两种去重投影变化查询数均为 0，因此本次固定查询集上未观察到 DDL 重复记录对 Top-K 的实际影响。

检索质量旁路指标为 Top-1 `3/12`、Top-5 `9/12`、Top-10 `10/12`。它不改变重复影响结论，但应作为后续独立检索质量优化输入，不在本阶段扩展处理。

Evidence：`E:\3\_training_backups\f6-1h-20260720-161131\evidence`。F6-1H 已完成；下一步仅等待 F6-1I 明确授权。

## 15. F6-1I-A 正式治理决策、工具与恢复 SOP（已准备，等待审查）

F6-1I 固定拆分为：

```text
F6-1I-A  工具、决策规则、前向与回滚 SOP 准备
F6-1I-B  完整隔离候选构建、切换与回滚演练
F6-1I-C  经 I-B 验收后执行正式候选构建与路径切换
```

I-A 新增纯治理模块 `training/sop/ddl_memory_formal_governance.py` 和 SOP `docs/sop/ddl_memory_formal_governance.md`。治理决策冻结为 `ALREADY_MANAGED_NO_SWITCH`、`IDENTITY_MIGRATION_REQUIRED`、`BLOCKED_FORMAL_STATE`；正式目标不是去重，而是在正式只读快照证明 115 条精确 DDL 内容无异常后，判断是否需要将 legacy ID/Metadata 迁移为 `ddlmem-v1`。

候选内容模型固定为：115 条确定性 managed v1 DDL 加 83 条逐记录原样保留的非 DDL Memory，总数仍为 198。legacy 删除只能来自精确匹配 allowlist；非 DDL 必须以 `record_id + normalized_document_sha256 + canonical_metadata_sha256` 三元签名保持一致。候选最终 Plan 必须为 `create=0 / unchanged=115 / changed=0 / removed=0`。

Top-K 回归改用稳定语义键，DDL 为 `table_name + normalized_document_sha256 + classification`，非 DDL 为 `record_id + normalized_document_sha256 + classification`，不因 DDL record ID 迁移误判变化。已提交完整回归入口冻结为 `tools/run_postgresql_f5_regression.py` 与 `training/regression/postgresql_f5_regression_v1.json`（15 题、suite SHA `f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34`）。

正式切换禁止原地治理，必须使用同盘同级目录前向重命名；第二步失败或新正式验收失败时整库恢复旧正式目录。成功切换后不主动回滚，并保留 pre-switch、immutable archive、candidate 与 Evidence。I-B/I-C 接口已实现并冻结，但本阶段禁止调用。

I-A 自检只使用合成 115+83 记录和系统临时 sandbox，不导入 Chroma，不创建 Client，不访问正式路径，不构建真实候选，不执行正式切换：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
FORMAL_SWITCH_EXECUTED=NO
```

Evidence：`E:\3\_training_backups\f6-1i-a-20260720-163104\evidence`。F6-1A～H 已完成；F6-1I-A 已准备等待审查；F6-1I-B、F6-1I-C 未开始。

## 16. F6-1I-A-R1 正式切换批准接口修复（已完成，等待审查）

I-A 初版存在接口冲突：`isolated_drill()` 正确将 I-B 原始 summary 的 `formal_switch_authorized`、`service_stopped_confirmed`、`no_client_occupancy_confirmed` 写为 `false`，但 I-C 门禁错误要求同一原始 Evidence 中三项为 `true`。R1 已将演练事实与本次运行确认彻底分离。

I-C 现在只读验证原始 I-B summary 的阶段、PASS、candidate、非 DDL、三种 sandbox 状态、来源/archive SHA 和 `0/115/0/0` Plan；原始 summary 的三个运行时字段必须继续为 `false`，不得手工编辑或生成批准版。正式授权、服务停止和无 Client 占用改由 I-C 命令显式传入：

```text
--formal-switch-authorized
--service-stopped-confirmed
--no-client-occupancy-confirmed
```

调用顺序冻结为“读取并验证原始 I-B summary → 验证三项显式确认 → 验证 CLI/run_root → 访问正式来源”。缺少任一确认时，路径校验、正式路径存在性判断、哈希、复制、Client 创建、候选构建、运行目录创建和重命名均不会发生。I-B 模式携带任何正式确认参数会被拒绝。

R1 自检只使用临时原始 summary 和 mock 门禁，不导入 Chroma、不访问正式路径、不执行 I-B/I-C：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
FORMAL_SWITCH_EXECUTED=NO
```

Evidence：`E:\3\_training_backups\f6-1i-a-r1-20260720-165753\evidence`。F6-1I-A-R1 已完成等待审查；F6-1I-B、F6-1I-C 未开始。

## 17. F6-1I-B-R1 统一正式 DDL 分类契约（已完成）

F6-1I-B 首次唯一执行在 `E:\3\_training_backups\f6-1i-b-20260720-171235` 安全停止，状态为 `BLOCKED_FORMAL_STATE`。来源复制前、immutable archive、来源复制后 Tree SHA 均为 `ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f`；正式路径 Client 打开 0，未迁移、未执行 Top-K/sandbox、未修改正式资产。失败 summary SHA256 为 `175ad1fd17318b76be8f8bba6ae4c9a739f3fa6086c9f807809aacfbabebf045`，失败目录和 summary 不得修改或复用。

直接原因是 Governance 曾以“能够解析 `CREATE TABLE`”作为宽松 DDL 候选条件，将 6 条仅含 SQL 示例、缺少完整 DDL Memory 标记的非 DDL 记录判为内容变体，得到 `total=198 / candidate=121 / exact=115 / variant=6 / non-DDL=77`；这与 F6-1G 已冻结的分类契约漂移。

R1 已将 F6-1G 的纯规则公开为 `is_ddl_candidate_document()` 和 `parse_ddl_table_name()`，并由 Governance 唯一复用。分类固定为精确 SHA 优先；非精确记录必须同时包含 `[DDL_MEMORY]`、`CREATE TABLE`、`表名：` 才是候选。仅含 `CREATE TABLE` 的 SQL 示例归入非 DDL，非 DDL 三元签名也使用同一规则。

合成 198 条回归由 115 条 legacy 精确 DDL 和 83 条非 DDL 组成，其中 6 条非 DDL 包含当前表 `CREATE TABLE` 示例。审计与治理均稳定得到：

```text
total_count                  198
ddl_candidate_count          115
exact_match_record_count     115
exact_match_table_count      115
content_variant_count          0
unexpected_ddl_count           0
non_ddl_count                 83
managed_v1_ddl_count           0
legacy_expected_ddl_count    115
decision_state               IDENTITY_MIGRATION_REQUIRED
```

6 条 SQL 示例全部进入非 DDL 三元签名；输入倒序不改变审计分类或治理事实。本阶段正式 Chroma 文件系统访问 0、Client 创建 0，未重新执行 I-B。Evidence：`E:\3\_training_backups\f6-1i-b-r1-20260720-172243\evidence`。

## 18. F6-1I-B-R2 完整隔离候选与切换/回滚演练（已完成，等待审查）

2026-07-21 使用全新运行目录 `E:\3\_training_backups\f6-1i-b-20260721-091258` 唯一执行一次 `--isolated-drill`，原始 I-B summary 状态为 `PASS`。正式来源仅用于遍历、读取、文件大小/SHA-256 计算和复制；脚本以正式路径创建 Chroma Client 的次数为 0，未执行正式路径切换。

来源复制前、immutable archive、来源复制后以及演练结束后的 archive Tree SHA 均为：

```text
ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
```

来源治理决策为 `IDENTITY_MIGRATION_REQUIRED`。来源分类为 `198 total / 115 DDL candidate / 115 exact records / 115 exact tables / 0 managed v1 / 115 legacy expected DDL / 83 non-DDL / 0 missing / 0 variant / 0 unexpected / 0 exact duplicate groups / 0 table-identity duplicate groups`。legacy 删除 allowlist 共 115 条，全部由精确规范化 document SHA、`expected_exact_match` 分类和冻结 record ID 共同限定，非 DDL 进入 allowlist 的数量为 0。

迁移仅发生在 `candidate_working_copy`。候选验收为 `PASS`：`198 total / 115 managed v1 / 0 legacy / 83 non-DDL / 0 variant / 0 unexpected / 0 exact duplicate groups / 0 table-identity duplicate groups`；Plan 为 `create=0 / unchanged=115 / changed=0 / removed=0`。83 条非 DDL 的 `record_id + normalized_document_sha256 + canonical_metadata_sha256` 逐记录保持，包含此前误判的 6 条 `CREATE TABLE` SQL 示例；未读取、保存或导出 embedding。

12 题、Top-K=10 的双查询副本语义顺序一致，精确重复槽位和表身份重复槽位均为 0；期望表命中数为 `Top-1=3 / Top-5=9 / Top-10=10`，稳定语义 SHA256 为 `90ea174bb3e694f8865070437483329001caed630e2fdf486aac892a58cc45e3`。本阶段未执行 15 题完整回归。

Sandbox 依序得到 `SWITCHED`、`ROLLED_BACK_AFTER_SECOND_STEP_FAILURE`、`ROLLED_BACK_AFTER_LIVE_ACCEPTANCE_FAILURE`。两类失败均恢复原 live Tree 和来源分类/总数；正常切换保留 pre-switch 且不主动回滚，正式来源和 immutable archive 保持不变。

原始 I-B summary 为 `E:\3\_training_backups\f6-1i-b-20260721-091258\evidence\formal-governance-summary.json`，SHA256 为 `4b21bf9b075ecaa888449c05a33917d00eda057717b2927e3f8cbec7edb8a21a`。其 `formal_switch_authorized`、`service_stopped_confirmed`、`no_client_occupancy_confirmed` 均保持 `false`，未手工修改或生成批准版。

R2 收口时，F6-1I-B-R2 已完成并等待审查，F6-1I-C 尚未开始；后续状态由第 19 节继续记录。

## 19. F6-1I-C-A 正式切换后验收与回滚证明（已准备，等待审查）

正式切换接口新增必填 `--approved-drill-summary-sha256`。未来 I-C 固定使用已验收 I-B 原始 summary `E:\3\_training_backups\f6-1i-b-20260721-091258\evidence\formal-governance-summary.json` 及 SHA256 `4b21bf9b075ecaa888449c05a33917d00eda057717b2927e3f8cbec7edb8a21a`。工具顺序冻结为：读取原始字节并验证固定 SHA；验证 I-B 决策、四棵 Tree SHA、allowlist、候选事实、Plan、Top-K 和 sandbox 状态；验证三个运行时确认；验证路径参数；最后才允许访问正式来源。哈希或关键事实错误时，正式路径文件系统访问和 Chroma Client 创建均为 0。

切换前继续要求当前正式来源 Tree SHA 等于获批 I-B 基线、重新构建的候选通过分类/Plan/非 DDL/Top-K、独立候选副本完成 15/15 回归，并在重命名前验证 `candidate_working_copy` 与同级 candidate Tree SHA 相等。

切换后验收拆为三个独立阶段：重新打开新正式库验证 `198 total / 115 managed v1 / 0 legacy / 83 non-DDL`、Plan `0/115/0/0` 及零缺失/变体/非预期/重复；从新正式库创建全新查询副本，验证 12 题 Top-K=10、`3/9/10` 命中数和冻结语义 SHA；从新正式库创建另一全新副本，独立执行切换后 15/15 完整回归。PASS summary 分别记录切换前、切换后的 candidate、Top-K 和完整回归状态，不再共用单一回归字段。

第二步重命名、新正式分类/Plan、Top-K 或切换后完整回归任一失败均进入统一自动回滚。失败新库移动至时间戳 failed 目录，pre-switch 恢复后必须验证 Tree SHA，并从恢复库创建只读验证副本，对账总数、分类和治理事实。任一恢复证明失败时状态固定为 `ROLLBACK_VERIFICATION_FAILED`，不得宣称正式库已恢复；全部成功时不主动回滚并保留 pre-switch。

合成自检不访问正式路径、不导入真实 Chroma Client，覆盖 summary SHA/事实门禁、sibling Tree SHA、三类切换后失败回滚、恢复 Tree/分类证明、回滚验证失败状态、切换前后两次完整回归及成功后不主动回滚。Evidence：`E:\3\_training_backups\f6-1i-c-a-20260721-093308\evidence`。

I-C-A 收口时，F6-1A～H、F6-1I-A/R1、F6-1I-B-R1/R2 已完成并验收，F6-1I-C-A 已准备等待审查，F6-1I-C 正式执行尚未开始；后续状态由第 20 节继续记录。

## 20. F6-1I-C-R1 正式监控与目录句柄隔离修复（已准备，等待审查）

F6-1I-C 首次正式执行最终状态为 `ROLLBACK_VERIFICATION_FAILED`，事故现场冻结。已知执行事实为：切换前候选、Plan、Top-K、15/15 完整回归和 sibling Tree 门禁通过；切换后分类、Plan、重复和 Top-K 通过；切换后完整回归在 `before_service_start` 以 `FormalRuntimeChanged` 误阻断；自动回滚移动新正式目录时发生 `WinError 5`。本节不判断 current live 或 pre-switch 的实际身份，不读取、哈希、复制或打开任何事故目录和失败 Evidence。

直接缺陷一是 `tools/run_postgresql_f5_regression.py` 的 `formal_checkpoint()` 曾硬编码旧正式库 SHA。Runner 现支持成对参数 `--expected-formal-record-count`、`--expected-formal-sha256`；省略两项继续使用 legacy F5 基线，缺一或非法小写 SHA 均拒绝。所有 checkpoint 使用本次冻结值且仍逐阶段 fail-fast。Governance 使用 Runner 同一 `directory_state()` 算法，切换前即时冻结旧正式监控基线，切换后即时冻结新正式监控基线，两阶段 Evidence 分别记录 count、SHA 和 checkpoints。

直接缺陷二是切换后分类曾直接打开待重命名的新 `vanna_data`。现在所有 `vanna_data`、pre/candidate/failed 同级重命名目标均禁止传给 `PersistentClient`：切换后分类复制到 `post_switch_classification_copy` 并验证 Tree SHA 后打开；Top-K、完整回归和回滚分类分别只打开各自独立副本。正式事务开始前保存 Client 路径审计，打开目标与重命名目标交集非空时以 `CLIENT_RENAME_TARGET_CONFLICT` 停止。

未来只读事故诊断接口 `--incident-diagnose` 只哈希并复制 current live 与 pre-switch，只打开仓库外诊断副本，并读取失败 Evidence；不执行任何重命名或恢复。纯决策状态冻结为 `CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY`、`CURRENT_LIVE_INVALID_PRE_SWITCH_RECOVERABLE`、`BLOCKED_INCIDENT_STATE`。诊断、验收保留 current live、受控回滚必须分别重新授权。

合成自检覆盖动态基线兼容/参数门禁/变化 fail-fast、切换前后不同 SHA、副本专用 Client、重命名路径交集、三种事故决策和诊断无重命名。事故运行时文件系统访问 0、Chroma Client 创建 0、正式切换 0、恢复 0。Evidence：`E:\3\_training_backups\f6-1i-c-r1-20260721-101126\evidence`。

状态：F6-1A～H、F6-1I-A/R1、I-B-R1/R2、I-C-A 已完成；F6-1I-C 首次正式执行为 `ROLLBACK_VERIFICATION_FAILED`；F6-1I-C-R1 已完成；后续状态由第 21 节继续记录。

## 21. F6-1I-C-R2 诊断结论与 R3-A 独立保留验收准备

R2 只读诊断状态为 `CURRENT_LIVE_MANAGED_PRE_SWITCH_LEGACY`。冻结事实为：current live Tree SHA `608630b65bd54aa18cc0a3143d836d171d706e24666ce74ff1af1004e8c5afe0`，包含 `198 total / 115 managed v1 / 0 legacy / 83 non-DDL`，分类无缺失、变体、非预期、重复、损坏或确定性 ID 冲突，Plan 为 `0/115/0/0`；pre-switch Tree SHA `ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f`，包含 `198 total / 0 managed v1 / 115 legacy / 83 non-DDL`。两边非 DDL 三元签名一致，首次失败 Evidence 对账通过，诊断未直接打开运行时 Client、未重命名、未恢复。

R3-A 新增未来模式 `--incident-accept-current-live`。固定前置顺序为 incident summary SHA/事实、首次失败 summary SHA/状态、保留授权、服务停止和无 Client 占用确认、全部固定路径，之后才允许访问运行时来源。任一前置门禁失败时正式运行时文件系统访问、run root、Chroma Client 和回归均为 0。

未来 R3-B 只允许分别复制 current live 和 pre-switch 到验收副本，并只在独立分类、Top-K 和回归副本上创建 Client。验收冻结为 current live 分类及 Plan `0/115/0/0`、pre-switch legacy 基线、83 条非 DDL 三元签名、12 题 Top-K=10 与语义 SHA `90ea174bb3e694f8865070437483329001caed630e2fdf486aac892a58cc45e3` 和命中 `3/9/10`、15/15 完整回归、验收后两份来源与两份 Evidence SHA 不变。成功状态为 `CURRENT_LIVE_FORMALLY_ACCEPTED`，来源变化为 `FORMAL_ACCEPTANCE_SOURCE_CHANGED`；不允许切换、恢复、重命名、删除 pre-switch 或写 collection。

R3-A 仅运行合成自检，事故运行时文件系统访问 0、Chroma Client 创建 0、真实正式验收 0、正式切换 0、恢复 0。Evidence：`E:\3\_training_backups\f6-1i-c-r3-a-20260721-104718\evidence`。

状态：F6-1I-C 首次正式执行为 `ROLLBACK_VERIFICATION_FAILED`；F6-1I-C-R1 已完成；F6-1I-C-R2 只读诊断已完成，current live 为完整 managed 新库；F6-1I-C-R3-A 独立保留验收接口已准备，等待审查；F6-1I-C-R3-B 未开始；恢复 pre-switch 不建议且未授权；F6-1 正式收口尚未完成。当前唯一动作是等待 ChatGPT 审查并授权 R3-B。

## 22. 风险与下一阶段

### BLOCKING_RISK

1. `agent_config.py` 在未设置 `VANNA_DATA_DIR` 时默认指向仓库内 `vanna_data`。隔离适配层已在延迟导入前强制路径门禁；后续完整 Apply 必须只经该显式隔离工厂打开候选库，不得回退到默认 `create_memory()`。
2. 当前 `save_text_memory` 会生成 UUID 和 timestamp。F6-1D 适配层已绕过该 API，以显式 `record_id` 实现固定存储契约；后续完整 Apply 不得重新调用旧 API。
3. 当前 collection 混存 Text Memory 与 Tool Memory。正式治理必须按完整副本验收，不能按文本相似度或单条 ID 在正式库原地删除。

下一阶段建议：等待 ChatGPT 审查 F6-1I-C-R3-A，并决定是否明确授权 R3-B 独立验收。不得在本阶段执行真实验收、恢复 pre-switch、重新执行正式切换、启动正式服务或自动关闭 F6-1。

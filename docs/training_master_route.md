# Vanna 项目训练与功能交付总路线

> **状态职责声明：当前项目阶段、正式训练资产、当前唯一动作和长期项目路线，统一以 `docs/project_master_roadmap.md` 为准。**
>
> 本文件只记录训练原则、Memory 分类、训练验收标准、数据缺失治理、受控特例规则、Batch 01—10 历史记录和训练实现约束，不作为当前状态门禁。
>
> 核心宗旨：**先以最小化方案实现可用功能，再补充可持续维护能力；安全门禁只保留能够防止数据丢失、正式数据误写和主功能错误的必要部分。**
>
> 本文件中的验收标准一旦进入执行阶段即冻结，不得因为新发现的非致命问题反复追加阻断条件。

---

## 一、项目交付原则

### 1. 功能优先

当前首要目标不是继续完善迁移和审批体系，而是尽快完成：

```text
115 张表结构进入可检索上下文
→ Agent 能定位正确表和字段
→ 生成正确 SQL
→ SQLGuard 通过
→ PostgreSQL 只读执行
→ 返回正确答案
→ 前端能够显示适合的图表
```

只要主链路未跑通，安全和治理工作不得无限扩展。

### 2. 最低安全底线

当前阶段只保留五条硬门禁：

1. 正式 Chroma 操作前必须有已验证的完整备份。
2. 新训练和功能验证先在独立 Chroma 副本执行。
3. 当前功能阶段只新增数据，不删除旧 Memory。
4. 写入后检查数量、基本内容和检索结果。
5. 副本失败时直接丢弃副本或重新从备份创建，不建立复杂补偿状态机。

### 3. 阻断问题与技术债分离

只有以下问题可以阻断当前主线：

```text
会导致正式数据不可恢复
会误删或误写正式数据
会导致当前功能结果错误
会导致当前阶段无法继续执行
```

以下问题默认进入技术债，不得立即阻断主线：

```text
极端异常路径未覆盖
理论攻击或审计伪造风险
摘要还可以绑定更多字段
审批证据不够形式化
训练重复执行尚未完全幂等
目录结构尚未重构
共享基类尚未抽取
自动 schema 更新机制尚未建立
```

### 4. 验收标准冻结

每个阶段开始前明确验收标准。

执行期间发现的新问题按以下规则处理：

```text
直接影响本阶段功能或数据安全
→ 当前修复

不影响本阶段结果
→ 记录到技术债，阶段继续
```

不得在阶段完成后不断移动验收线。

---

## 二、历史训练状态与验收快照

以下内容是历史训练事实，不代表当前状态；当前状态统一查阅 `docs/project_master_roadmap.md`。

### 1. 已完成基础设施

```text
0A       ✅ 运行与数据库安全基线
0B-1     ✅ 训练批次静态校验和批次指纹
0B-2A    ✅ Chroma 文件快照、备份和副本机制
0B-3B    ✅ run_sql Tool Memory 写入计划
0B-3C    ✅ run_sql Tool Memory Chroma 适配层
0B-3C-M1 ✅ 旧 UUID Tool Memory 迁移计划 2.1，未执行
```

### 2. Batch 10 交付后历史资产快照

```text
历史文档登记基线：a0609b04b2d7d16b25752ebf526644ea27d0ea53

正式运行 Chroma 总记录数：197

原有 Text Memory：8
Level 1 DDL Text Memory：115
LEGACY_READ_ONLY Tool Memory：64
确定性受控 Tool Memory：10
Tool Memory 总数：74
```

64 条旧 Tool Memory 来源于已经执行过的 Level 2 / Level 3 训练，主要覆盖 6 张试点表。

这些记录：

```text
功能上已经存在并可被运行时检索
治理上属于 legacy
尚未执行确定性 ID 迁移
```

### 3. 尚未完成

```text
0B-4 ✅ Tool Memory全链隔离演练完成
Level 1  ✅ 115表DDL/元数据正式覆盖
Level 2  ⚠️ 仅有 6 表旧式基础 SQL 训练
Level 3  ⚠️ 仅有 6 表旧式业务 SQL 训练
Level 4  ⏳ 图表与多图表训练未系统化验收
```

### 4. 已冻结的迁移辅助资产

已经完成但暂不继续扩展：

```text
M1：旧 UUID 迁移计划
M2A-R1：16 条 expected_tables 恢复提案
M2B 1.0：批准 overlay 和只读迁移 bundle 候选
```

这些资产保留在仓库中，但：

```text
不作为 Level 1 的前置门禁
不执行正式迁移
不继续进行 M2B-R1 安全补强
不阻断当前功能主线
```

---

## 三、旧 Chroma 记录处理策略

### 1. 定义为只读遗留层

当前 64 条旧 UUID Tool Memory 统一定义为：

```text
LEGACY_READ_ONLY
```

规则：

```text
允许运行时继续检索
禁止通过旧脚本继续新增
禁止原地修改
禁止删除
暂不要求迁移
```

### 2. Level 1 与旧 Tool Memory 共存

Level 1 的 DDL Text Memory 与旧 Tool Memory 属于不同用途：

```text
DDL / 文档 Text Memory
→ 提供表结构、字段和业务说明

Tool Memory
→ 提供问题到 SQL 的历史示例
```

因此可以在独立副本中形成：

```text
8 条现有 Text Memory
+ 64 条 legacy Tool Memory
+ 115 条新增 DDL Text Memory
```

旧 UUID 未迁移不影响 Level 1 功能验证。

### 3. 迁移触发条件

只有出现以下任一真实症状时，旧 UUID 迁移才重新提升为主线：

1. 运行时代码无法正确读取旧记录。
2. 旧 SQL 示例明显污染 115 表问题的 SQL 生成。
3. 新 Tool Memory 批次必须与旧记录统一去重或更新。
4. 正式对外发布要求所有训练记录统一归属。

否则继续只读保留。

### 4. 旧 SQL 示例 A/B 判断

端到端验证时比较：

```text
模式 A：metadata + DDL，不注入旧 SQL 示例
模式 B：metadata + DDL + 旧 SQL 示例
```

处理规则：

```text
旧示例有帮助
→ 继续只读保留

旧示例明显造成错表或错误 SQL
→ 暂时关闭 SqlExampleContextEnhancer 或增加配置开关

确实需要统一治理
→ 再执行既有迁移计划
```

不得在没有功能证据前，先假设旧记录必须迁移。

---

## 四、重新排序后的主路线

```text
F1  115 表 DDL 最小训练
↓
F2  端到端问答 MVP
↓
F3  仅修复实际功能阻断
↓
F4  正式 Level 1 切换
↓
F5  新 Level 2 / Level 3 Tool Memory 扩展
↓
F6  可维护性、安全治理和历史迁移
```

---

## 五、阶段 F1：115 表 DDL 最小训练

### 目标

使用现有：

```text
agent_data/column_metadata_index.json
```

为全部 115 张表生成 DDL Text Memory，并写入从验证备份创建的独立 Chroma 副本。

原 107 张目标来自旧清点；当前仓库的 `column_metadata_index.json` 实际覆盖 115 张唯一表，F1 以当前 index 的 115 张为执行基线。

### 最小实现方案

```text
读取 metadata index
→ 按 table 分组
→ 生成包含表名、表注释、字段名、类型和字段注释的 DDL 文本
→ 使用现有 save_text_memory() 写入独立副本
→ 完成表名和中文注释检索验证
```

F1阶段当时范围：

```text
不连接数据库重新生成索引
不实现 DDL plan / adapter
不实现确定性 Text Memory ID
不迁移旧 UUID
不执行 0B-4
不切换正式 Chroma
```

### F1 验收标准

必须同时满足：

```text
METADATA_TABLE_COUNT = 115
DDL_GENERATED_COUNT = 115
MEMORY_WRITE_SUCCESS_COUNT = 115
MEMORY_WRITE_FAILURE_COUNT = 0

EXACT_TABLE_RETRIEVAL_PASS = 115 / 115
CHINESE_COMMENT_RETRIEVAL_PASS >= 10 / 12

正式 Chroma 前后摘要不变
没有正式 Memory 写入
没有 Memory 删除
没有数据库连接和 SQL 执行
```

达到以上条件即认定：

```text
115 张表 DDL 训练链路最小可用
```

以下事项不属于 F1 验收：

```text
重复运行幂等
确定性 Text Memory ID
DDL 版本比较
自动 schema 同步
旧 UUID 迁移
完整异常恢复
全面安全审计
```

---

## 六、阶段 F2：端到端问答 MVP

### 目标

让训练副本真正完成：

```text
自然语言问题
→ metadata / DDL / SQL 示例上下文
→ LLM 生成 SQL
→ SQLGuard
→ PostgreSQL 只读执行
→ 回答
→ chart_spec
→ 前端图表
```

### 固定测试集

至少使用：

```text
3 个单表问题
2 个多表 JOIN 问题
1 个聚合图表问题
```

问题应覆盖不同业务领域，不得全部来自原 6 张试点表。

### F2 最小验收标准

6 个固定问题必须全部满足：

1. 召回正确的主要表和字段。
2. 不使用不存在的表或字段。
3. SQLGuard 通过。
4. PostgreSQL 只读执行成功。
5. 返回非空结果或业务上合理的空结果。
6. 最终回答与 SQL 结果一致。
7. 图表问题生成前端可解析的 `chart_spec`。
8. 前端图表能够实际显示。

达到以上条件即认定：

```text
115 表数据问答主链路 MVP 已实现
```

### 旧 SQL 示例 A/B

F2 同时测试：

```text
开启旧 SQL 示例注入
关闭旧 SQL 示例注入
```

只记录对 6 个问题结果的实际影响，不立即修改或迁移旧记录。

---

## 七、阶段 F3：功能阻断修复

只修复 F2 暴露的真实问题：

```text
召回错误表
DDL 缺失必要字段
metadata index 内容错误
LLM 使用不存在字段
SQLGuard 误拦截合法 SQL
JOIN 路径错误
回答与查询结果不一致
chart_spec 字段错误
前端图表无法显示
```

每个问题采用最小修复。

不得在 F3 中新增：

```text
复杂迁移状态机
新的审批层
新的摘要层
大规模目录重构
通用 adapter 基类
自动持续学习系统
```

F3 完成条件：固定 6 个 F2 问题重新全部通过。

---

## 八、阶段 F4：正式 Level 1 切换

### 切换前最低要求

1. F1 和 F2 已通过。
2. 从数据库执行一次只读 schema 一致性检查。
3. metadata index 中的对象和字段必须全部存在于实时数据库。
4. 正式 Chroma 已创建新的完整备份。
5. 待切换副本通过一次冒烟测试。

### schema 一致性检查范围

只比较：

```text
public schema 表名集合
每张表字段名集合
metadata index 表名和字段名集合
```

处理规则：

```text
metadata index 中的对象和字段全部存在于实时数据库
→ 允许切换

实时数据库额外对象和新增字段
→ 作为未开放范围或后续扩展项记录，不自动阻断当前 MVP 切换

仅类型或注释存在差异
→ 记录技术债，不阻断 MVP 切换

存在缺表、增表或字段名不一致
→ 更新 metadata index 后重新执行 F1
```

### 正式切换方式

优先使用已经通过验证的完整 Chroma 副本作为新正式库。

不得直接在旧正式库上临时追加、删除或手工修改文件。

### F4 验收

```text
正式路径切换成功
服务正常启动
固定 6 个冒烟问题通过
可在一次目录切换中恢复到旧正式库
```

---

## 九、阶段 F5：新 Level 2 / Level 3 Tool Memory 扩展

### 前置条件

准备新增正式 Tool Memory 批次前，必须完成：

```text
0B-4 Tool Memory 全链演练
```

0B-4 只门禁新的正式 Tool Memory 写入，不再门禁 Level 1 DDL Text Memory。

### 0B-4 最小范围

在隔离副本中验证：

```text
批次静态校验
→ 写入计划
→ adapter 写入
→ 写后核验
→ 模拟部分失败
→ 回滚或丢弃副本
→ 重新执行成功
```

0B-4 是脚本和证据门禁，不新增大型状态机模块。

### Level 2 / Level 3 扩展策略

不要求每张表机械配置相同数量的 SQL。

优先按真实业务价值分批：

```text
常用核心表
→ 典型单表查询
→ 高频 JOIN
→ 关键业务指标
→ 易混淆问题
→ 低频长尾表
```

每批只处理有限表和有限问题，完成后立即端到端验证。

`metadata_retriever.py` 只有在真实测试证明候选排序错误时才增加规则，不为所有新表提前硬编码意图。

### F5 Batch 01

```text
F5 Batch 01 ✅
ad_dict 基础查询，1 条标准 Level 2 受控 Tool Memory
```

SQLGuard 已支持 `FROM/JOIN` 派生表；派生表别名不计入真实表集合，底层真实表和派生表输出字段仍严格校验。

### F5 Batch 02

```text
F5 Batch 02 ✅
se_watershed_river 基础查询，1 条标准 Level 2 受控 Tool Memory
```

### F5 Batch 03

```text
F5 Batch 03 ✅
wm_meteorological_info 基础查询，1 条标准 Level 2 受控 Tool Memory
```

### F5 Batch 04

```text
F5 Batch 04 ✅
gis_control_unit 基础查询，1 条标准 Level 2 受控 Tool Memory
```

### F5 Batch 05

```text
F5 Batch 05 ✅
se_watershed 基础查询，1 条标准 Level 2 受控 Tool Memory
```

se_watershed 流域年度主数据与 se_watershed_river 河流明细已通过双向检索隔离验证。

### F5 Batch 06

```text
F5 Batch 06 ✅
wst_control_zone 基础查询，1 条标准 Level 2 受控 Tool Memory
```

wst_control_zone 水安全溯源分区与 gis_control_unit 水环境管控单元已通过双向检索隔离验证。

### F5 Batch 07

```text
F5 Batch 07 ✅
wst_asset_type_dict 基础查询，1 条标准 Level 2 受控 Tool Memory
```

wst_asset_type_dict 水安全溯源资产类型字典与 ad_dict 通用数据字典已通过双向检索隔离验证；parent_type 为可选层级字段。

### F5 Batch 08

```text
F5 Batch 08 ✅
wst_relation_type_dict 基础查询，1 条标准 Level 2 受控 Tool Memory
```

wst_relation_type_dict 水安全溯源资产关系大类字典已与 ad_dict 通用数据字典和 wst_asset_type_dict 资产类型字典通过三问题检索隔离验证。

### F5 Batch 09

```text
F5 Batch 09 ✅
rs_enterprise_info_lsg 基础查询，1 条标准 Level 2 受控 Tool Memory
```

rs_enterprise_info_lsg 用于磷石膏库基础档案查询；name 作为完整且唯一的自然名称识别字段；address 为可选展示字段；已验证与 rs_enterprise_info_wade 不存在名称集合映射。

### F5 Batch 10-S1

```text
F5 Batch 10-S1 ✅
只读范围发现完成
STANDARD候选：0
合格CONTROLLED_EXCEPTION：0
推荐：NONE
Level 2候选饱和信号：CANDIDATE_SCARCE
```

wm_water_source_zone_v2 的有效编码子集仍存在完全重复业务元组；gis_watershed_partition 的核心编码全表为空，过滤后无有效记录。本阶段未创建Batch 10，未写入或删除正式Memory。

剩余表类型分布：

```text
GIS图层或映射表：29
时序、日志、预测或告警表：11
任务、同步或内部配置表：10
关系、追踪或中间表：6
数据质量或基础Level 2暂缓表：11
已知空表：3
仍需业务语义或数据质量核对的其他表：12
```

### F5 Level 2 收口审计

```text
F5 Level 2收口审计 ✅

未覆盖表总数：82
全部完成唯一分类：YES

L2_EXCLUDED_MAPPING_OR_COPY：36
L2_DEFERRED_DATA_QUALITY：2
L2_JOIN_OR_LEVEL3_ONLY：5
L2_SYSTEM_INTERNAL：12
L2_TIME_SERIES_OR_LOG：10
L2_RELATION_OR_INTERMEDIATE：10
L2_KNOWN_EMPTY：3
L2_NEEDS_BUSINESS_INPUT：0
L2_FINAL_FOCUSED_DISCOVERY：4

收口结论：FINAL_FOCUSED_DISCOVERY_REQUIRED
Level 2已收口：NO
```

需要执行最后定向验证的表：

```text
rs_enterprise_info_wade
rs_livestock_info_yc
rs_pollutant_enterprise
rs_sewage_info_v2
```

Batch 09交付后当前只有一轮独立无候选证据，即 Batch 10-S1；必须完成上述四表的最后定向发现，才能判断是否形成第二轮无候选证据。

### F5 Level 2 最后定向发现

```text
F5 Level 2最后定向发现 ✅

目标表：4
STANDARD候选：1
CONTROLLED_EXCEPTION：0
最终推荐：D10_L2_RS_SEWAGE_INFO_V2_001
推荐表：rs_sewage_info_v2

Level 2候选饱和信号：NOT_REACHED
Level 2已收口：NO
```

逐表结论：

```text
rs_enterprise_info_wade：
L2_EXCLUDED_MAPPING_OR_COPY

rs_livestock_info_yc：
L2_DEFERRED_DATA_QUALITY

rs_pollutant_enterprise：
L2_EXCLUDED_MAPPING_OR_COPY

rs_sewage_info_v2：
ELIGIBLE_STANDARD
```

本轮发现了新的标准候选，因此没有形成第二轮独立无候选证据。

### F5 Batch 10-T0

```text
F5 Batch 10-T0 ✅
范围冻结完成

表：rs_sewage_info_v2
模式：STANDARD
问题：查询污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位，最多返回50条
训练批次：level2-f5-batch10-20260717-01
样本：F5_L2_B10_SQL_001
预计新增Memory：1
```

冻结 SQL：

```sql
SELECT
    admin_division,
    project_name,
    treatment_process,
    design_scale,
    actual_assess_scale,
    operation_unit
FROM rs_sewage_info_v2
LIMIT 50
```

冻结 expected_behavior：

```text
返回最多50条污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位；设计规模和实际考核规模字段单位为t/d（吨/日）
```

识别策略：`NATURAL_NAME_IDENTIFIER`。

设计规模和实际考核规模的单位均为 t/d（吨/日）。

当前表只有1行，分类为 LOW_VOLUME_SINGLE_ROW；该Memory的训练价值是 STABLE_SCHEMA_QUERY_PATTERN，用于固定稳定业务问题与SQL映射，不代表当前表具有充分数据覆盖。

### F5 Batch 10

```text
F5 Batch 10 ✅
rs_sewage_info_v2 污水处理厂项目基础档案查询
1条标准Level 2受控Tool Memory

training_batch_id：level2-f5-batch10-20260717-01
sample_id：F5_L2_B10_SQL_001
record_id：toolmem-v1-1ede1c839114367719624c0d51c2cc3a1df54ac38c23baf4a8dbfd97c038ed4b
```

设计规模和实际考核规模字段单位均为 t/d（吨/日）。

该表当前只有1行，属于 LOW_VOLUME_SINGLE_ROW；
该Memory用于固定 STABLE_SCHEMA_QUERY_PATTERN，
不代表当前表具有充分的数据覆盖。

已通过与 L3_P2_SQL_011 排污口联合明细能力的双向检索隔离验证。

```text
正式Chroma总记录数：197
Legacy Tool Memory：64
受控Tool Memory：10
Tool Memory总数：74
Batch 10交付后覆盖表数：34
Batch 10交付后未覆盖表数：81
```

Batch 10交付后历史阶段记录：

```text
F5 Level 2最终收口审计
```

### F5 Level 3 Batch 01

```text
F5 Level 3 Batch 01 ✅
香溪河泗湘溪站DAY水质趋势联表查询
1条标准Level 3受控Tool Memory

training_batch_id：level3-f5-batch01-20260717-01
sample_id：F5_L3_B01_SQL_001
record_id：toolmem-v1-d7bd8ebc76a246817b20f5619ca3a0324f8401ed8c19d24691ce45d4681c38b6

正式Chroma总记录数：198
正式Chroma SHA256：d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992
Legacy Tool Memory：64
受控Tool Memory：11
Tool Memory总数：75
固定回归：15/15
```

HOUR仍为 `HOUR / SAME_CLUSTER_DEFERRED_VARIANT`，本批次未交付。固定回归suite内容及SHA均未改变。

### F5 PostgreSQL最终总验收历史快照

```text
PostgreSQL F1—F5：全部验收通过
正式Chroma：198
正式Chroma SHA256：d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992
Text Memory：123（原有Text 8，Level 1 DDL Text 115）
Legacy Tool Memory：64
受控Tool Memory：11
Tool Memory总数：75
受控Memory完整性：11/11
SQLGuard：11/11
数据库只读执行：11/11
Level 3目标题：通过
双向检索：通过
HOUR隔离：通过
固定回归：首次14/15随机无SQL，按规则使用全新副本完整重跑后15/15
正式目录稳定性：全程通过
PostgreSQL训练板块：已关闭
```

延期Level 3能力不属于第一板块阻断。本节仅追加历史验收快照，不承担当前状态职责。

---

## 十、阶段 F6：可维护性和安全治理

功能达到可用状态后，再按实际收益实施：

### 1. DDL Text Memory 可维护性

可增加：

```text
ddl_memory_plan.py
ddl_memory_adapter.py
```

推荐状态：

```text
create
unchanged
changed
removed
```

第一版独立实现，不提前抽取 Tool Memory adapter 共享基类。

只有两个 adapter 稳定后，确实存在重复逻辑，再提取小型存储边界 helper。

### 2. metadata index 更新机制

后续建立：

```text
只读数据库提取
索引生成
索引 diff
人工确认
重新训练
```

不需要在 MVP 前建立定时同步、事件监听或自动变更审批。

### 3. embedding profile

记录：

```text
模型名称
模型版本或本地指纹
向量维度
归一化方式
依赖版本
Chroma 版本
collection 名称
```

embedding 模型变化时重建索引，不直接混用旧向量。

### 4. 旧 UUID 最终治理

根据 F2 A/B 结果选择：

```text
继续只读保留
禁用旧示例注入
执行既有迁移计划
人工废弃低质量样本
```

不得因为已有迁移代码就默认必须立即执行迁移。

### 5. 自动化回归与持续学习

正式功能稳定后再建立：

```text
固定端到端问题集
训练前后对比
批次自动报告
用户确认后的学习候选
人工审批写入
```

第一版在线学习只允许用户明确确认后形成候选，不自动写正式 Memory。

---

## 十一、阶段状态与汇报规则

每次汇报只需要回答：

```text
当前功能阶段是什么？
当前阶段验收通过了多少项？
是否修改正式 Chroma？
是否存在阻断主线的问题？
有哪些问题进入技术债？
下一步唯一动作是什么？
```

不再强制把每个 Text Memory 功能阶段套入 Tool Memory 的 T0—T8 状态。

T0—T8 主要用于：

```text
新的正式 Tool Memory 批次
高风险正式 Memory 变更
```

不用于阻断独立副本中的 DDL MVP 试验。

---

## 十二、当前固定路线图

本节不再维护动态路线、当前阶段或项目长期顺序。

当前项目路线统一查阅：`docs/project_master_roadmap.md`。

前述 F1—F5 批次内容仅作为历史验收记录保留。

---

## 十三、当前阶段唯一授权

本节不再维护当前唯一授权。

当前阶段与唯一动作统一查阅：`docs/project_master_roadmap.md`。

---

## 十四、审查员职责

审查员的职责是推动项目在可接受风险下交付，不是持续寻找无限多的理论缺口。

后续审查必须遵守：

```text
先判断最小可行实现
再判断最低安全底线
最后记录可维护性技术债
```

不得采用：

```text
发现一个非致命问题
→ 自动升级为阻断项
→ 追加新的大型契约
→ 重复返工
```

对于已满足冻结验收标准的阶段，应明确通过并进入下一阶段。

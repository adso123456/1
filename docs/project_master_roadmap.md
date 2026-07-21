# 项目总路线与任务台账

> 建议文件路径：`docs/project_master_roadmap.md`
> 用途：作为本项目跨阶段、跨对话的唯一总路线依据。
>
> **状态职责声明：本文件是唯一当前状态源；`docs/training_master_route.md` 仅记录训练规范与历史验收记录。**
> 后续任何新任务、阶段切换、技术债延期或验收结论，都应同步更新本文件，避免遗漏历史任务。

---

## 1. 项目定位

本项目是一个基于 Vanna 2.0、React、ECharts 和 ChromaDB 的中文数据问答系统，当前主要能力包括：

- 中文自然语言转 SQL；
- PostgreSQL 数据查询；
- SQLGuard 安全校验；
- DDL / Metadata 检索；
- Tool Memory SQL 示例检索；
- 多图表生成与切换；
- 仪表板拖拽、缩放和持久化；
- 表格和图表加入仪表板；
- 仪表板图片导出；
- 基于 FastAPI / SSE 的流式问答。

当前仓库：

```text
adso123456/1
```

当前项目根目录：

```text
E:\3\posgresql\1
```

正式 Chroma 运行库：

```text
E:\3\_runtime\vanna-level1\vanna_data
```

---

## 2. 当前正式基线

最后已验收正式训练提交：

```text
a1d322848012f5be1ba0ef2e4247139d4f92ea33
```

当前正式训练资产：

```text
正式运行 Chroma 总记录数：198

原有 Text Memory：8
Level 1 DDL Text Memory：115
LEGACY_READ_ONLY Tool Memory：64
确定性受控 Tool Memory：11
Tool Memory 总数：75
```

当前仓库 HEAD 以 Git 为准，不在路线文档中维护自引用提交 SHA。

当前阶段：

```text
F6-1 DDL Text Memory幂等治理
```

当前禁止越界进入：

```text
新增正式Memory
治理正式198条Chroma
F6-1I-B
F6-1I-C
Legacy迁移
Vanna 源码解耦
MySQL 接入
多数据源改造
一句话生成报表
外部网站机器人集成
```

---

## 3. 已完成阶段

```text
0A       ✅ 基础训练资产准备
0B-1     ✅ Tool Memory 训练规范
0B-2A    ✅ Chroma 文件快照、备份和副本机制
0B-3B    ✅ Tool Memory 写入计划
0B-3C    ✅ Tool Memory Chroma 适配层
0B-3C-M1 ✅ 旧 UUID Tool Memory 迁移计划，未执行
F1       ✅ Level 1 DDL 最小实现
F2       ✅ 端到端 MVP 与回归
F3       ➖ 无主线功能阻断，跳过
F4       ✅ 正式 Level 1 切换
F5       ✅ Level 2 / Level 3 受控训练与最终总验收完成
```

已完成的受控 Level 2 批次：

| Batch | 表 | 能力 |
|---|---|---|
| Batch 01 | `ad_dict` | 通用数据字典 |
| Batch 02 | `se_watershed_river` | 流域河流档案 |
| Batch 03 | `wm_meteorological_info` | 气象站档案 |
| Batch 04 | `gis_control_unit` | 水环境管控单元 |
| Batch 05 | `se_watershed` | 流域年度信息 |
| Batch 06 | `wst_control_zone` | 水安全溯源分区 |
| Batch 07 | `wst_asset_type_dict` | 水安全溯源资产类型 |
| Batch 08 | `wst_relation_type_dict` | 水安全溯源关系大类 |
| Batch 09 | `rs_enterprise_info_lsg` | 磷石膏库基础档案 |
| Batch 10 | `rs_sewage_info_v2` | 污水处理厂项目基础档案 |

F5 Batch 09 ✅ 已完成正式交付与验收。

Batch 10-S1 状态：

```text
F5 Batch 10-S1 ✅
只读候选发现完成
推荐结果：NONE
Level 2候选饱和信号：CANDIDATE_SCARCE
未创建Batch 10
未新增正式Memory
```

F5 Level 2 收口审计状态：

```text
F5 Level 2收口审计 ✅
82张未覆盖表已完成唯一分类
收口决策：FINAL_FOCUSED_DISCOVERY_REQUIRED
后续最终状态：Level 2已收口：YES
需要最后定向验证的表：4
```

最后定向验证表：

```text
rs_enterprise_info_wade
rs_livestock_info_yc
rs_pollutant_enterprise
rs_sewage_info_v2
```

F5 Level 2 最后定向发现状态：

```text
F5 Level 2最后定向发现 ✅
发现1条STANDARD候选
最终推荐：D10_L2_RS_SEWAGE_INFO_V2_001
推荐表：rs_sewage_info_v2
后续最终候选饱和信号：REACHED
后续最终状态：Level 2已收口：YES
```

F5 Batch 10-T0 状态：

```text
F5 Batch 10-T0 ✅
rs_sewage_info_v2范围已冻结
training_batch_id：level2-f5-batch10-20260717-01
sample_id：F5_L2_B10_SQL_001
预计新增Memory：1
```

冻结候选：

```text
table：rs_sewage_info_v2
question：查询污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位，最多返回50条
expected_behavior：返回最多50条污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位；设计规模和实际考核规模字段单位为t/d（吨/日）
training_batch_id：level2-f5-batch10-20260717-01
sample_id：F5_L2_B10_SQL_001
identifier_strategy：NATURAL_NAME_IDENTIFIER
expected_new_memory_count：1
```

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

当前表只有1行，分类为 LOW_VOLUME_SINGLE_ROW；训练价值为 STABLE_SCHEMA_QUERY_PATTERN。该Memory用于固定稳定业务问题与SQL映射，不代表当前表具有充分数据覆盖。

F5 Batch 10 正式交付状态：

```text
F5 Batch 10 ✅
rs_sewage_info_v2正式交付完成
正式Chroma = 197
Tool Memory = 74
受控Tool Memory = 10
覆盖表 = 34
未覆盖表 = 81
```

F5 PostgreSQL Level 2 ✅ 已收口

- 正式Chroma：197
- Tool Memory：74
- 受控Tool Memory：10
- 已覆盖表：34
- 剩余81张表均已完成明确分类
- Batch 10交付后完成两轮独立无候选确认
- Level 2候选饱和状态：REACHED

F5-G1 ✅ 回归基线版本化与唯一事实源收敛完成

- suite_id：`postgresql-f5-regression-v1`
- case_count：15
- suite_content_sha256：`f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34`
- F2固定回归：6题
- Batch 02—10目标回归：9题
- 隔离复验：15/15
- 正式目录监控：18/18
- 正式Chroma：197条，`222bc79b0d08ee895ded4cd0f8beaf641e4faba8b7c55b2b6c333d089a837b26`
- `docs/project_master_roadmap.md`：唯一当前状态源
- `docs/training_master_route.md`：训练规范与历史记录

F5-G1期间发现旧Runner父进程误开正式Chroma；正式目录已从精确备份完成目录级恢复；Runner父进程Memory链已切断并通过全新副本隔离复验。

### Level 3能力盘点完成

- Legacy Tool Memory：64条全部分类；
- 独立能力簇：53；
- Level 2能力簇：22；
- Level 3能力簇：31；
- 语义重复：5条；
- STALE_SCHEMA：0；
- SQLGUARD_FAIL：0；
- 已识别并真实验证5个高价值Level 3缺口。

### Level 3 Batch 01正式交付完成

- training_batch_id：`level3-f5-batch01-20260717-01`
- sample_id：`F5_L3_B01_SQL_001`
- selected_grain：`DAY`
- deferred_variant：`HOUR / SAME_CLUSTER_DEFERRED_VARIANT`
- training_mode：`STANDARD`
- capability_level：`L3_TIME_SERIES_JOIN`
- semantic_risk：`LOW`
- tables：`wm_station_info_v2`、`wm_waterquality_day_records`
- representative_station：`香溪河泗湘溪站`
- duplicate_found：`NO`
- sql_guard：`PASS`
- record_id：`toolmem-v1-d7bd8ebc76a246817b20f5619ca3a0324f8401ed8c19d24691ce45d4681c38b6`
- 正式Chroma：198条，`d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992`
- 确定性受控Tool Memory：11条
- Tool Memory总数：75条
- HOUR：仍为 `SAME_CLUSTER_DEFERRED_VARIANT`，未交付
- 固定回归：在198条正式状态对应副本上15/15通过
- 回归suite：内容及SHA `f7a3c417819d17e1aa12f59630375e0ab5194e9aa0245c7f4427dc977cb48b34` 均未改变

### Level 3正式收口结论

- Level 3 Batch 01已正式交付并验收；
- `F5_L3_B01_SQL_001` 填补了“可读站点名称 → 内部ID → 日水质趋势”的核心联表能力；
- 目标题验证通过；
- 双向检索通过；
- 固定回归15/15；
- 正式Chroma为198条；
- 确定性受控Tool Memory为11条；
- Tool Memory总数为75条；
- PostgreSQL Level 3补充训练正式停止。

以下候选统一登记为延期能力，不再继续交付，且不属于第一板块阻断：

- HOUR变体；
- `L3_GAP_02`；
- `L3_GAP_03`；
- `L3_GAP_04`；
- `L3_GAP_05`。

上述延期能力未标记为失败或废弃。

### F5 PostgreSQL最终总验收完成

- PostgreSQL F1—F5全部完成并通过最终验收；
- 正式Chroma：198条，SHA256为 `d8eb66906905a6da0ae6f9f6d56ce1f552ff3c3d54867203f01a912e24ebe992`；
- Text Memory：123条，其中原有Text 8条、Level 1 DDL Text 115条；
- Legacy Tool Memory：64条；
- 确定性受控Tool Memory：11条，完整性验收11/11；
- Tool Memory总数：75条；
- 受控SQL的SQLGuard验收：11/11；
- 受控SQL的数据库只读执行：11/11；
- Level 3目标题单次E2E通过；
- 双向检索与HOUR隔离通过；
- 固定回归最终15/15通过；
- 正式目录在目标题、两轮完整回归及结束双检中全程未变化；
- 延期Level 3能力不属于第一板块阻断；
- PostgreSQL训练板块正式关闭。

---

## 4. 大板块执行顺序

后续大板块必须按以下顺序推进：

```text
PostgreSQL训练关闭
→ F6关键治理
→ Vanna依赖解耦
→ 多数据源底座
→ MySQL
→ 报表
→ 网站集成
→ 生产化
```

排序原则：

1. 先固定 PostgreSQL 可工作的训练和回归基线；
2. 再解耦 Vanna，避免后续 MySQL、报表和网站模块继续依赖 Vanna 内部接口；
3. 多数据源底座必须先于 MySQL 训练；
4. 报表引擎应直接建立在多数据源统一接口之上；
5. 外部网站集成应依赖稳定的自有 API，而不是直接暴露 Vanna 接口；
6. 最后再做生产化、安全、权限和运维治理。

---

# 第一大板块：PostgreSQL 训练收口

## 5. F5 已完成目标

F5 不以“115 张表全部训练”为目标。

Level 1 DDL / Metadata 已经让系统认识 115 张表。
Level 2 / Level 3 Tool Memory 应优先补充：

```text
高价值能力
高频用户问题
容易生成错误 SQL 的能力
需要检索引导的稳定业务查询
跨表、统计、趋势等真实业务场景
```

不得为了提高表覆盖率，机械地给每张表增加一条基础 SQL。

---

## 6. F5 已完成内部顺序

```text
1. 完成 F5 Batch 10 候选发现 ✅
2. 完成 F5 Level 2 收口审计 ✅
3. 完成4张指定表最后定向只读发现与Batch 10范围冻结 ✅
4. 正式交付1条rs_sewage_info_v2标准Level 2 Tool Memory ✅
5. 版本化PostgreSQL F5回归基线并收敛唯一事实源 ✅
6. 整理数据缺失、暂缓和受控特例登记 ✅
7. 盘点现有64条LEGACY_READ_ONLY Tool Memory的实际能力 ✅
8. 识别并真实验证5个高价值 Level 3 缺口 ✅
9. 冻结首个Level 3候选范围 ✅
10. 正式交付已冻结的F5_L3_B01_SQL_001 ✅
11. 只补少量核心 Level 3 ✅
12. 执行 F5 PostgreSQL 总验收 ✅
13. 正式关闭 PostgreSQL 训练板块 ✅
```

---

## 7. Level 2 候选分类

候选统一分为三类。

### 7.1 STANDARD

标准候选：

```text
单表查询
业务对象明确
核心识别字段完整
至少 3 个有意义展示字段
无语义重复
不是 GIS 映射或旧表副本
检索碰撞风险不是 HIGH
```

允许两种识别策略：

```text
BUSINESS_CODE_IDENTIFIER
NATURAL_NAME_IDENTIFIER
```

当不存在可靠业务编码，但名称字段完整、唯一且业务对象天然可按名称识别时，可以使用：

```text
NATURAL_NAME_IDENTIFIER
```

### 7.2 CONTROLLED_EXCEPTION

只有在没有合格 STANDARD 候选时，才允许评估。

只解决真实数据完整性问题，例如：

```text
核心字段部分为空
可选字段大量为空
有效数据子集仍具有独立业务价值
```

仅允许简单非空过滤：

```sql
WHERE <核心识别字段> IS NOT NULL
```

自然语言问题必须明确表达过滤范围，例如：

```text
查询已有编码的水源地名称和行政区信息，最多返回50条
```

不得通过特例处理：

```text
语义重复
GIS 映射
旧表或 V2 副本
业务对象混合
字段单位不明确
编码—名称冲突
无法解释的重复记录
```

### 7.3 DEFERRED_DATA_QUALITY

当前数据质量不足，暂缓训练。

适用于：

```text
核心字段几乎全部为空
无法形成可靠识别
重复或冲突无法解释
过滤后仍无实际业务价值
```

此状态不是永久弃用。

重新启用条件：

```text
数据质量修复
字段语义明确
表结构变化
训练等级变化
设计出独立 JOIN / 统计 / Level 3 能力
现有 Memory 发生变化
```

---

## 8. Level 2 候选饱和判断

满足以下条件时，可以认为 Level 2 接近收口：

```text
连续两轮重新发现都没有新的 LOW 风险 STANDARD 候选；
或者只能发现 CONTROLLED_EXCEPTION；

剩余未覆盖表主要属于：
- GIS 图层或映射表
- 旧表 / V2 副本
- 配置、日志、任务、缓存表
- 高频时序明细
- 数据质量暂缓表
- 必须 JOIN 才有业务意义的表

高价值主数据、字典和基础档案已得到基本覆盖。
```

饱和信号：

```text
NOT_REACHED
CANDIDATE_SCARCE
REACHED
```

只有经过至少两轮证据支持，才允许从 `CANDIDATE_SCARCE` 转为 `REACHED`。

不得仅因为“没找到表”就宣布 Level 2 完成。

---

## 9. Level 3 计划

Level 3 不重新覆盖所有表。

先盘点现有 64 条 legacy Tool Memory：

```text
问题语义
使用表
SQL
返回字段
JOIN
聚合
过滤
排序
业务场景
```

只补真正缺失的高价值场景：

```text
跨表关联
分组统计
区域对比
时间趋势
排名与 Top N
异常分析
状态分布
核心业务指标
```

Level 3 候选优先来自真实用户问题，不从表结构机械生成。

---

## 10. F5 总验收条件

PostgreSQL F5 收口必须至少满足：

```text
Level 2 候选达到饱和
所有受控特例和数据质量暂缓表已登记
高价值 Level 3 缺口已补充
检索碰撞可控
受控 Memory 无重复
正式全量回归稳定
正式 Chroma 备份和恢复机制验证
正式失败能够完整恢复
路线和 evidence 完整
旧 UUID 仍保持只读或已有明确治理方案
```

F5 关闭后，才进入 F6。

---

# 第二大板块：F6 可维护性与历史技术债治理

## 11. F6 执行顺序

```text
F6-1：DDL Text Memory维护、确定性身份与重复执行幂等
F6-2 Metadata 索引更新机制
F6-3 Embedding Profile 与向量兼容治理
F6-4 旧 UUID Tool Memory 最终治理
F6-5 自动化回归与持续学习
```

交付脚本共享骨架抽取属于F6技术债，不在F5-G1中重构。

---

## 12. F6-1：F1 阶段 25→50 重复训练幂等遗留

这是 F1 阶段一直搁置的明确技术债。

历史现象：

```text
同一批约 25 条 DDL Text Memory 重复执行后，
记录数量变成约 50 条。
```

这说明早期 DDL Text Memory 写入链路缺少可靠的：

```text
确定性 ID
内容指纹
查重
upsert
changed / removed 处理
```

该问题不等同于 Level 2 Tool Memory 语义重复。

### 12.1 当时为什么延期

F1 当时只验证：

```text
DDL 生成正确
独立 Chroma 可写入
Metadata 可检索
不污染正式库
```

以下内容被延后：

```text
重复运行幂等
确定性 Text Memory ID
旧记录清理
增量更新
删除治理
```

### 12.2 F6-1 子任务

```text
F6-1A 复现 F1 阶段 25→50 重复写入 ✅ 已完成
F6-1B 生成 DDL Text Memory 确定性身份 ✅ 已完成
F6-1C 实现 ddl_memory_plan.py ✅ 已完成
F6-1D 实现 ddl_memory_adapter.py ✅ 已完成
F6-1E 支持 create / unchanged / changed / removed ✅ 已完成
F6-1F 隔离验证重复运行记录数不增长 ✅ 已完成
F6-1G-A 正式只读审计工具与 SOP 准备 ✅ 已完成
F6-1G-B 执行正式快照只读审计 ✅ 已完成
F6-1H-R1 修复不可变归档与查询副本模型 ✅ 已完成
F6-1H-R2 执行双副本 Top-K 影响评估 ✅ 已完成
F6-1I-A 治理工具和前向/回滚 SOP 准备 ✅ 已完成
F6-1I-A-R1 正式切换批准接口修复 ✅ 已完成
F6-1I-B 首次执行因分类规则漂移安全停止
F6-1I-B-R1 统一正式审计与治理分类契约 ✅ 已完成
F6-1I-B-R2 完整隔离候选与切换/回滚演练 ✅ 已完成，等待审查
F6-1I-C 正式候选构建与路径切换（未开始）
```

### 12.3 幂等验收标准

假设计划包含 25 条 DDL Text Memory：

```text
第一次执行：
create = 25
最终记录增量 = +25

第二次原样执行：
create = 0
unchanged = 25
最终记录增量 = 0

第三次只修改 1 张表 DDL：
changed = 1
unchanged = 24
不得形成旧版和新版同时有效

移除 1 张表：
removed = 1
必须经过人工审批后处理
```

总原则：

```text
同一批次重复运行 N 次，
有效 Memory 数量和检索结果不得随执行次数增长。
```

### 12.4 正式治理安全线

```text
不得直接删除正式 Memory
不得仅凭文本相似判断重复
不得误删不同版本或不同业务粒度内容
必须先做只读指纹审计
必须先在完整隔离副本中验证
正式失败必须完整恢复
```

### 12.5 F6-1A 写入链审计与隔离复现（2026-07-20）

真实写入链：

```text
agent_data/column_metadata_index.json
→ train_step3.load_metadata_index / group_tables
→ train_step3.build_table_ddl / build_all_table_ddls
→ train_step3._run_training
→ agent_config.create_memory
→ ChromaAgentMemory.save_text_memory
→ tool_memories collection.upsert
```

直接根因：`save_text_memory` 每次由 Vanna 生成新的 UUID4，并以该新 ID 执行 `upsert`；调用前不存在逻辑对象身份、内容指纹或重复判断。因此相同 DDL 第二次执行仍新增记录。

隔离复现按当前 115 条真实 Level 1 生成结果的表名升序稳定选取前 25 条，结果为 `0 → 25 → 50`；按规范化 DDL 和有效 Metadata 分组得到 25 个重复组、共 50 条记录，唯一 Memory ID 为 50。R1 强制结果门禁验收通过。脚本以正式路径创建 Chroma Client 的尝试次数为 0；该字段不代表操作系统级全局监控结果。

Evidence：

```text
E:\3\_training_backups\f6-1a-r1-20260720-124151\evidence
```

F6-1A 已完成。身份与治理原则记录在 `docs/f6_ddl_idempotency_baseline.md`：后续拆分 plan/apply，`removed` 不自动删除，正式治理采用完整候选副本验收后切换。当时未提前开展后续子任务；当前状态见 12.6。

### 12.6 F6-1B 确定性身份规范（2026-07-20）

F6-1B 已完成：新增纯函数模块 `training/sop/ddl_memory_identity.py`，冻结四个身份字段、大小写与字符校验、最小 DDL 规范化、`logical_id`、`record_id`、固定有效 Metadata 和 `content_fingerprint`。F6-1A 工具已复用该模块，兼容回归仍为 `0 → 25 → 50`、25 个重复组、50 个唯一 ID。

兼容回归 Evidence：

```text
E:\3\_training_backups\f6-1b-20260720-125411\evidence
```

F6-1B 阶段未实现 Plan、Apply 或 Chroma 写入；当时未提前开展 F6-1C～I，当前状态见 12.7。

### 12.7 F6-1C 确定性 Plan（2026-07-20）

F6-1C 已完成：新增纯函数 `training/sop/ddl_memory_plan.py`，基于期望 `DdlMemoryIdentity` 与现有只读快照生成 `create/unchanged/changed/removed` 确定性计划。managed/unmanaged 边界、结构冲突门禁、Action 指纹字段和 `ddl-memory-plan-v1` SHA 规则已冻结。

Evidence：

```text
E:\3\_training_backups\f6-1c-20260720-135537\evidence
```

F6-1C 阶段未创建 Chroma Client，未执行写入、更新、删除或 Apply；当时未提前开展 F6-1D～I，当前状态见 12.8。

### 12.8 F6-1D 隔离 Chroma 适配层（2026-07-20）

F6-1D 已完成：新增 `training/sop/ddl_memory_adapter.py`，提供稳定快照、显式确定性 ID create、带旧指纹前置条件的 replace，以及写后 Plan unchanged 精确验证。路径在 Client 创建前强制隔离；适配层不调用 `save_text_memory()`，不生成随机 ID，不提供删除或完整 Apply。

真实隔离集成核心计数：

```text
初始 0
加入 unmanaged 1
create managed 2
replace managed 2
stale replace 后 2
```

Evidence：

```text
E:\3\_training_backups\f6-1d-20260720-141542\evidence
```

unmanaged 记录保持不变，关闭重开后记录仍存在；本脚本以正式路径创建 Chroma Client 的尝试次数为 0。当时 F6-1E～I 均未开始，后续状态见 12.9。

### 12.9 F6-1E 受控 Apply 编排器（2026-07-20）

F6-1E 已完成：新增 `training/sop/ddl_memory_apply.py`，将已经审阅的确定性 Plan 与 F6-1D 隔离适配层组合为 `ddl-memory-apply-v1`。写前重新读取快照、重建 Plan 并完整比对版本、内容与 SHA；过期或篡改计划在 `writes_started=false` 时失败。Action 按稳定顺序执行：create=`created`、unchanged=`verified_noop`、changed=`replaced`、removed=`retained/removal_deferred`。

写后强制验证 Plan 收敛、仅 create 引起总数增长、unmanaged 与 retained removed 逐条不变。执行中或写后验收失败会报告已完成 Action 和失败 ID，并将候选库标记为不可验收；不自动重试、回滚或删除。

真实隔离最小集成结果：

```text
初始记录数：4
初始 Plan：create=1, unchanged=1, changed=1, removed=1
首次 Apply 后记录数：5
最终 Plan：create=0, unchanged=3, changed=0, removed=1
旧计划复用：零写入 stale 失败
新计划第二次 Apply：verified_noop=3, retained=1, 记录增量=0
```

正式 115 条完整隔离验证保留给 F6-1F。本阶段不打开或治理正式 198 条 Chroma，不新增正式 Memory。F6-1F～I 均未开始，下一步仅等待 F6-1F 明确授权。

Evidence：

```text
E:\3\_training_backups\f6-1e-20260720-144046\evidence
```

### 12.10 F6-1F 115 条全量隔离幂等验收（2026-07-20）

F6-1F 已完成：新增 `training/sop/ddl_memory_idempotency_acceptance.py` 和轻量 Runbook `docs/sop/ddl_memory_idempotency_acceptance.md`。真实输入只复用 `train_step3` 的 Metadata 读取、分组和 DDL 生成纯函数；2572 条 Metadata 稳定生成 115 条期望 DDL，表名、logical ID、record ID 均唯一。

全新隔离库验收结果：

```text
第一轮 Plan：create=115, unchanged=0, changed=0, removed=0
第一轮 Apply：created=115, count=0→115
关闭重开后的第二轮 Plan：create=0, unchanged=115, changed=0, removed=0
第二轮 Apply：verified_noop=115, count=115→115
再次关闭重开：total=115, managed=115, unmanaged=0
```

第一轮写后、第二轮写后和最终重开的语义快照 SHA 均为：

```text
0de14c2abac3f83e83e8652799545b73ae90bcfa9f5fa5b388cb4084570c180d
```

record ID、logical ID、identity key 重复组均为 0。正式 Chroma Client 打开尝试为 0；未调用旧写入 API、删除、数据库或 Top-K 检索。F6-1G～I 未开始，下一步只等待 F6-1G 明确授权。

Evidence：

```text
E:\3\_training_backups\f6-1f-20260720-150243\evidence
```

### 12.11 F6-1G-A 正式只读审计准备（2026-07-20）

F6-1G-A 已完成工具与 SOP 准备：新增 `training/sop/ddl_memory_formal_readonly_audit.py` 和 `docs/sop/ddl_memory_formal_readonly_audit.md`。未来正式执行固定采用“来源 Tree SHA → 完整复制 → 快照 Tree SHA → 来源复核 Tree SHA”的门禁，三者一致后才允许打开仓库外 `formal_snapshot`；只读取既有 `tool_memories` 的 ID、document、Metadata，不读取 embedding，不提供 Memory 写入或删除能力。

历史分类规则已冻结为：期望精确匹配、期望表内容变体、非预期 DDL、非 DDL Memory，以及表级缺失清单；精确重复按规范化 document SHA，表身份重复按当前期望表名，不使用向量相似度，不把内容变体自动判为可删除重复。

本阶段只执行合成记录、真实 115 条期望集合和临时目录自检，正式审计未执行：

```text
FORMAL_CHROMA_FILESYSTEM_ACCESS_DURING_STAGE=0
FORMAL_CHROMA_CLIENT_OPEN_ATTEMPTS_BY_SCRIPT=0
```

Evidence：

```text
E:\3\_training_backups\f6-1g-a-20260720-152700\evidence
```

F6-1G-A 收口时，F6-1G 正式审计仍未完成，F6-1G-B、F6-1H～I 均未开始；当前结果见 12.12。

### 12.12 F6-1G-B 正式快照只读审计（2026-07-20）

F6-1G-B 已严格按批准 SOP 执行一次并通过。正式来源仅遍历、读取、计算大小/SHA 并完整复制；三份文件清单一致，来源复制前、快照、来源复制后的 Tree SHA 均为：

```text
ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
```

Chroma Client 只打开仓库外 `formal_snapshot`：正式路径 Client 打开尝试 0，快照 Client 打开 1。正式 collection 总数为 198，与基线一致，审计状态 `PASS`。

```text
DDL 候选=115
期望精确匹配记录=115
期望精确匹配表=115
缺失表=0
内容变体=0
非预期 DDL=0
非 DDL Memory=83
精确重复组/记录/净冗余=0/0/0
表身份重复组/记录=0/0
classification_sha256=f4f2e5aaf59d93317ee3dae1316f21979ed9cf13f69f1794fff43161be67d76d
```

分类总数与 198 条 collection 记录完全对账。未执行治理、补写、替换、删除、embedding 读取或 Top-K 测试。F6-1A～G 已完成；F6-1H～I 未开始，下一步等待 F6-1H 明确授权。

Evidence：

```text
E:\3\_training_backups\f6-1g-20260720-153450\evidence
```

### 12.13 F6-1H-R1 不可变归档与查询副本模型（2026-07-20）

F6-1H 首次尝试在打开 Client 前因旧快照 Tree SHA 变化停止，状态为 `SNAPSHOT_INTEGRITY_GATE_FAILED`。正式来源访问 0，正式/快照 Client 打开 0，Top-K 未执行，仓库未修改或提交。失败 Evidence 保留于：

```text
E:\3\_training_backups\f6-1h-20260720-154428\evidence
```

F6-1G 旧快照已明确为“被 Client 打开过的审计工作副本”，保留但不再作为不可变归档或 H 查询输入。R1 新增 `training/sop/ddl_memory_topk_impact.py` 并修订现有正式只读 SOP，冻结：

```text
正式来源 → formal_archive（永不打开）
formal_archive → query_snapshot_run1（一次性）
formal_archive → query_snapshot_run2（一次性）
```

两个副本必须独立直接来自 archive；工作副本打开后的变化允许且必须记录，archive 查询前后必须完全一致。R1 仅用临时目录和 Fake Query Collection 完成模型、归档完整性、两种投影、两轮稳定 SHA 和只读能力自检，没有访问正式来源、旧快照，没有创建正式 archive/查询副本或执行 Top-K。

Evidence：

```text
E:\3\_training_backups\f6-1h-r1-20260720-155539\evidence
```

R1 收口时 F6-1A～G 已完成，F6-1H-R2 与 F6-1I 尚未开始；该历史状态已由 12.14、12.15 继续推进。

### 12.14 F6-1H-R2 双副本 Top-K 重复影响评估（2026-07-20）

严格按批准 SOP 唯一执行一次正式来源只读复制与双副本 Top-K 评估，状态为 `PASS`。正式来源复制前、不可变 archive、正式来源复制后三个 Tree SHA 均为：

```text
ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f
```

两个查询副本 pre-open SHA 均等于 archive；打开后分别变为 `719d771ed5c7dde193ce95ba65cf9edfbb5ab15b2b15b50f65750ec7bef5f0e5` 与 `838612c21a3347f6bd33e7b86afd5931dfce71aac1b50d38b793e1166aea4e98`，各有 3 个文件变化。查询结束后 archive SHA 仍为原值，证明 Chroma 打开副作用被限制在一次性查询副本。

固定 12 个查询、Top-K=10 的结果中，精确重复槽位、表身份重复槽位及两种投影变化查询数均为 0；两轮结果 SHA 均为 `6b0709607ed127b5c67499920f7edf20800a5dc280021d844beb397c3cdcd7d6`。本次评估结论为 `duplicate_topk_impact=NONE`。期望表 Top-1 / Top-5 / Top-10 命中数为 `3 / 9 / 10`，作为非阻断检索质量旁路指标保留。

正式路径 Chroma Client 创建尝试为 0，查询副本 Client 打开数为 2。未治理、补写、替换或删除任何正式 Memory。Evidence：

```text
E:\3\_training_backups\f6-1h-20260720-161131\evidence
```

H-R2 收口时 F6-1A～H 已完成、F6-1I 尚未开始；该历史状态已由 12.15 继续推进。

### 12.15 F6-1I-A 正式治理决策、工具与恢复 SOP（2026-07-20）

F6-1I 已固定拆分为 I-A 工具/SOP 准备、I-B 完整隔离候选与切换回滚演练、I-C 正式候选重建与路径切换。本阶段只完成 I-A，不访问、复制、打开或修改正式 Chroma。

新增 `training/sop/ddl_memory_formal_governance.py`，冻结三种决策：`ALREADY_MANAGED_NO_SWITCH`、`IDENTITY_MIGRATION_REQUIRED`、`BLOCKED_FORMAL_STATE`。迁移目标不是删除重复内容，而是在正式记录仍为 115 条精确 DDL、83 条非 DDL 且无异常时，将 legacy DDL 身份迁移为确定性 `ddlmem-v1`。

候选必须保持 `115 managed v1 DDL + 83 原样非 DDL = 198`，最终 Plan 为 `0 create / 115 unchanged / 0 changed / 0 removed`。删除只接受冻结的精确 legacy ID allowlist；83 条非 DDL 按 ID、document SHA、canonical Metadata SHA 逐记录保真。Top-K 使用忽略 DDL record ID 变化的稳定语义键比较，任何结果顺序、重复槽位或 Top-1/5/10 命中变化都阻断切换。

新增 `docs/sop/ddl_memory_formal_governance.md`，冻结 immutable archive、candidate、已提交 15 题完整回归入口、服务停止与占用门禁、sandbox 演练、同盘同级前向重命名和自动失败回滚。成功切换后不主动回滚，备份和 Evidence 必须保留。

I-A 自检仅使用合成 115+83 记录与临时 sandbox；正式 Chroma 文件系统访问 0、正式 Client 创建尝试 0、正式切换未执行。Evidence：

```text
E:\3\_training_backups\f6-1i-a-20260720-163104\evidence
```

I-A 初版收口时 F6-1A～H 已完成、F6-1I-A 已准备等待审查，I-B/I-C 未开始；批准接口状态已由 12.16 修正。

### 12.16 F6-1I-A-R1 正式切换批准与运行时确认接口（2026-07-20）

I-A 初版要求 I-B 原始 summary 同时保持三个运行时字段为 `false`，又要求 I-C 将其读取为 `true`，导致阶段衔接不可能。R1 已修复为两个独立门禁：I-B summary 只承载不可变演练事实且三个字段永远保持 `false`；I-C 的正式授权、服务停止和无 Client 占用由三个独立 `store_true` 参数提供。

I-C 固定先验证原始 I-B summary 的 PASS、候选验收、非 DDL 保真、三种 sandbox 状态、来源/archive SHA 和 `0/115/0/0` Plan，再验证三个显式运行时确认，再验证路径参数，最后才允许访问正式来源。缺少任一确认时，正式路径文件系统访问、Client 创建、候选构建、运行目录创建和重命名均为 0。I-B 携带正式确认参数会被拒绝。

自检覆盖原始 summary 不可变、异常演练事实拒绝、三种缺失确认分别拒绝、确认齐全通过、I-B 参数混用拒绝以及前文件系统失败顺序。Evidence：

```text
E:\3\_training_backups\f6-1i-a-r1-20260720-165753\evidence
```

I-A-R1 收口时 F6-1I-B、F6-1I-C 尚未开始；I-B 首次执行及分类契约修复状态已由 12.17 继续记录。

### 12.17 F6-1I-B-R1 统一正式审计与治理 DDL 分类契约（2026-07-20）

I-B 首次执行完成正式来源只读归档后，在工作副本分类阶段以 `BLOCKED_FORMAL_STATE` 安全停止。来源/archive/来源后 Tree SHA 一致；未执行迁移、Top-K、sandbox 或正式切换。失败结果为 `198 total / 121 DDL candidate / 115 exact / 6 variant / 77 non-DDL`，Evidence 保留于 `E:\3\_training_backups\f6-1i-b-20260720-171235`，不得修改或复用。

根因是 Governance 独立宽松规则先解析 `CREATE TABLE`，把 6 条缺少完整 DDL Memory 标记的 SQL 示例误判为当前表内容变体；F6-1G 正式审计只把同时具有 `[DDL_MEMORY]`、`CREATE TABLE`、`表名：` 的非精确记录视为候选。

R1 已公开并复用 F6-1G 的唯一纯分类接口。精确 document SHA 始终优先；非精确记录必须满足三个完整标记后才能解析表名。合成 115 条 legacy 精确 DDL + 83 条非 DDL（含 6 条当前表 CREATE TABLE 示例）在两个模块中均得到 `115 candidate / 115 exact / 0 variant / 83 non-DDL`，治理决策为 `IDENTITY_MIGRATION_REQUIRED`，6 条示例全部进入非 DDL 保真签名，倒序输入结果不变。

本阶段未访问正式 Chroma、失败 archive 或工作副本，未重新执行 I-B。Evidence：

```text
E:\3\_training_backups\f6-1i-b-r1-20260720-172243\evidence
```

R1 收口时，F6-1A～H、F6-1I-A/R1 已完成，I-B 首次执行安全停止，I-B-R1 已完成等待审查，I-B 重执行和 I-C 尚未开始；后续状态由 12.18 继续记录。

### 12.18 F6-1I-B-R2 完整隔离候选与切换/回滚演练（2026-07-21）

分类契约修复后，在全新目录 `E:\3\_training_backups\f6-1i-b-20260721-091258` 唯一执行一次隔离演练并通过。正式来源复制前、immutable archive、来源复制后和结束后 archive Tree SHA 均为 `ab0b141a42bf59e2077895a3e759c944d678f9858a90ee4e62a11f99a53d064f`；正式路径 Client 打开 0，未执行正式切换。

来源分类为 `198 total / 115 candidate / 115 exact / 0 managed v1 / 115 legacy / 83 non-DDL / 0 variant / 0 unexpected`，决策为 `IDENTITY_MIGRATION_REQUIRED`。115 条精确 legacy DDL 进入冻结 allowlist，非 DDL 进入 allowlist 为 0。候选迁移后为 `198 total / 115 managed v1 / 0 legacy / 83 non-DDL`，Plan 为 `0 create / 115 unchanged / 0 changed / 0 removed`，重复组、内容变体和非预期 DDL 均为 0。

83 条非 DDL 三元签名全部逐记录保持，包含此前误判的 6 条 `CREATE TABLE` SQL 示例。12 题 Top-K=10 语义顺序一致，命中数为 `Top-1=3 / Top-5=9 / Top-10=10`，稳定语义 SHA256 为 `90ea174bb3e694f8865070437483329001caed630e2fdf486aac892a58cc45e3`。

Sandbox 依序完成 `SWITCHED`、第二步失败回滚和新 live 验收失败回滚；两类失败均恢复原 live 的 Tree SHA、总数和分类，正常切换后保留 pre-switch 且不主动回滚。原始 I-B summary 位于 `E:\3\_training_backups\f6-1i-b-20260721-091258\evidence\formal-governance-summary.json`，SHA256 为 `4b21bf9b075ecaa888449c05a33917d00eda057717b2927e3f8cbec7edb8a21a`，三个正式运行确认字段均为 `false`。

F6-1A～H、F6-1I-A/R1、F6-1I-B-R1/R2 已完成；I-B 首次执行安全停止；F6-1I-B-R2 已完成等待审查；F6-1I-C 未开始。当前唯一动作是等待 ChatGPT 审查并决定是否明确授权 F6-1I-C。

---

## 13. F6-2：Metadata 更新机制

后续建立：

```text
只读数据库提取
Metadata 索引生成
旧索引与新索引 diff
人工确认
DDL / Memory 更新计划
重新训练
回归验证
```

不在当前阶段做定时同步或自动审批。

---

## 14. F6-3：Embedding Profile

必须记录：

```text
模型名称
模型版本或本地指纹
向量维度
归一化方式
依赖版本
Chroma 版本
Collection 名称
```

Embedding 模型发生变化时：

```text
重建索引
不得直接混用旧向量
```

---

## 15. F6-4：旧 UUID Tool Memory 治理

当前 64 条 legacy Tool Memory：

```text
功能上仍可检索
治理上属于 legacy
暂不迁移
```

F6 再根据实际收益选择：

```text
继续只读保留
禁用旧示例注入
执行既有确定性 ID 迁移计划
人工废弃低质量样本
```

不得因为已有迁移代码就默认必须迁移。

---

## 16. F6-5：自动化回归与持续学习

正式功能稳定后建立：

```text
固定端到端问题集
训练前后对比
批次自动报告
检索碰撞检测
用户确认后的学习候选
人工审批写入
```

第一版在线学习：

```text
只形成候选
不得自动写正式 Memory
```

---

# 第三大板块：Vanna 解耦与项目内源码移除

## 17. 执行时机

必须在：

```text
PostgreSQL F5 / F6 关键基线固定之后
MySQL 接入之前
```

不能现在删除，也不能等 MySQL、报表和网站集成全部完成后再删除。

---

## 18. 当前结构问题

当前后端入口仍直接承担：

```text
LLM 创建
PostgreSQL Runner 创建
Memory 创建
Tool 注册
Context Enhancer 组装
Agent 创建
FastAPI Server 启动
```

当前前端还直接请求：

```text
/api/vanna/v2/chat_sse
```

后续必须逐步解除业务代码对 Vanna 内部接口和事件格式的直接依赖。

---

## 19. Vanna 解耦步骤

```text
1. 盘点所有直接 import vanna.* 的文件
2. 确认 Python 实际加载的 vanna.__file__
3. 确认项目内 Vanna 源码是否遮蔽 pip 包
4. 锁定当前可工作的 Vanna 版本
5. 建立 integrations/vanna_adapter.py
6. 抽离 AgentFactory
7. 抽离 MemoryFactory
8. 抽离 ToolRegistryFactory
9. 建立项目自有 SSE 事件格式
10. 让运行时不再依赖项目内源码副本
11. 执行完整回归
12. 最后删除项目内 Vanna 源码
```

删除源码前必须满足：

```text
运行时已不再引用本地源码目录
完整问答和训练回归通过
正式 Chroma 不受影响
依赖版本已锁定
恢复方案明确
```

---

# 第四大板块：多数据源底座

## 20. 目标

建立统一数据源架构，使 PostgreSQL 和 MySQL 可以独立运行、独立训练、独立检索。

建议职责结构：

```text
backend/
  api/
    chat.py
    reports.py
    datasources.py

  core/
    agent_factory.py
    chat_service.py
    report_service.py

  datasources/
    base.py
    registry.py
    postgres.py
    mysql.py

  memory/
    factory.py
    namespace.py

  integrations/
    vanna_adapter.py
```

实际目录可以调整，但职责不能重新集中回单一入口。

---

## 21. 数据源统一配置

每个数据源至少包含：

```text
source_id
database_type
host
port
database
user
password
read_only
metadata_path
memory_path
allowed_tables
sql_dialect
```

第一版要求用户或宿主系统显式选择：

```text
source_id = postgres_water
source_id = mysql_business
```

暂不让 LLM 自动判断数据库。

---

## 22. Memory 隔离

PostgreSQL 和 MySQL 初期使用独立 Chroma 目录：

```text
memory/postgres_water
memory/mysql_business
```

不得将两套数据库的：

```text
DDL
Metadata
SQL 示例
Tool Memory
```

混入同一无命名空间的检索集合。

必须验证：

```text
PostgreSQL 问题不召回 MySQL Memory
MySQL 问题不召回 PostgreSQL Memory
SQL 不会发送到错误数据库
相同表名不会混淆
```

---

## 23. 自有 API

前端最终应从：

```text
/api/vanna/v2/chat_sse
```

迁移到：

```text
/api/chat/stream
```

请求示例：

```json
{
  "message": "查询……",
  "conversation_id": "……",
  "source_id": "postgres_water"
}
```

对外 API 不暴露：

```text
Vanna 内部类
Vanna SSE 事件
Chroma 结构
SQL Runner 实现
```

---

# 第五大板块：MySQL 接入与训练

## 24. MySQL 特点

用户已确认：

```text
MySQL 数据完整
不会像当前 PostgreSQL 一样出现大规模字段缺失
```

因此 MySQL 训练应以 STANDARD 候选为主，不应复制 PostgreSQL 的大量数据缺失特例。

---

## 25. MySQL 执行顺序

```text
1. 建立只读账号和查询安全边界
2. 增加 MySQL 数据源适配器
3. 增加 MySQL SQLGuard 方言支持
4. 提取表、列、注释、主键、索引和外键
5. 生成独立 Metadata 索引
6. 生成独立 Level 1 DDL Memory
7. 做数据质量和敏感字段画像
8. 训练 Level 2 主数据、字典和基础档案
9. 训练 Level 3 JOIN、统计、趋势和排名
10. 做 PostgreSQL / MySQL 双向检索隔离
11. 做完整回归和正式切换
```

第一阶段不做 PostgreSQL 和 MySQL 跨库 JOIN。

---

# 第六大板块：一句话生成报表

## 26. 现有可复用能力

当前前端已经具备：

```text
ECharts
多图表
ChartSpec
图表切换
表格
仪表板
拖拽缩放
PNG 导出
```

因此报表板块重点不是重新做图表，而是建立：

```text
报表意图
报表查询计划
报表结构
多查询协调
文字总结
报表持久化
导出
权限
```

---

## 27. 报表输出模型

不要让 LLM 直接生成任意 HTML。

使用结构化 `ReportSpec`：

```json
{
  "title": "水环境运行分析报告",
  "source_id": "postgres_water",
  "sections": [
    {
      "type": "kpi",
      "title": "核心指标",
      "queries": []
    },
    {
      "type": "chart",
      "title": "区域分布",
      "chart_spec": {}
    },
    {
      "type": "table",
      "title": "重点明细"
    },
    {
      "type": "narrative",
      "title": "分析结论"
    }
  ]
}
```

---

## 28. 报表开发顺序

```text
1. 识别普通问答 / 单图 / 仪表板 / 完整报表意图
2. 生成受控 ReportSpec
3. 生成受控多查询计划
4. SQLGuard 和数据源路由
5. 执行查询并生成图表、表格和文字总结
6. 保存报表
7. 打开和编辑报表
8. 导出 PNG
9. 导出 PDF
10. 视需求增加 DOCX
```

报表查询必须限制：

```text
最大查询数
查询超时
最大返回行数
允许表
失败降级
```

---

# 第七大板块：外部网站机器人模块

## 29. 集成前置条件

正式集成前必须完成：

```text
自有 Chat API
自有 Report API
数据源选择
真实用户解析
服务端会话
权限边界
稳定 SSE 格式
```

当前固定 `demo` 用户模式不能直接用于外部网站生产集成。

---

## 30. 提前收集的对方网站信息

虽然正式开发排在后面，但现在可以提前登记：

```text
前端框架
后端语言
登录认证方式
用户和角色体系
部署网络
域名和跨域策略
是否支持 SSE
是否允许 iframe
是否要求 Web Component
是否需要报表入口
是否需要移动端适配
```

---

## 31. 集成形式优先级

### 31.1 iframe

优点：

```text
开发快
隔离性好
对宿主侵入小
```

缺点：

```text
登录、样式和路由联动较弱
```

### 31.2 Web Component

推荐作为正式机器人模块形态：

```text
容易嵌入不同前端框架
比 iframe 更容易统一样式和事件
```

### 31.3 前端 SDK + REST/SSE API

灵活度最高，但宿主网站开发工作最多。

最终形式由宿主网站技术栈决定。

---

# 第八大板块：生产化、安全与运维

## 32. 最终生产治理

```text
真实用户认证
角色和数据权限
数据源权限
服务端会话
报表权限
审计日志
请求限流
查询超时
并发控制
错误追踪
指标监控
Chroma 备份
数据库连接池
部署脚本
环境变量管理
密钥管理
灾难恢复
```

---

# 33. 全局最低安全线

所有阶段必须遵守：

```text
1. SQL 必须真实执行并通过 SQLGuard
2. 语义必须可信
3. 不制造重复 Memory
4. 当前范围内不得触碰未授权正式资产
5. 正式写入前必须完整备份
6. 正式写入前必须在全新隔离副本验收
7. 必须执行完整回归
8. 正式失败必须整库恢复
9. 不得用单条删除代替正式恢复
10. 提交范围必须精确
11. 受保护工作区不得修改
12. 每次只推进一个阶段
```

一句话：

```text
真实执行、语义可信、无重复、先备份、先隔离、检索不串、全量回归、失败整库恢复。
```

---

# 34. 受保护工作区

除非另行授权，持续保护：

```text
M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
M vanna_data/chroma.sqlite3

?? tools/audit_level1_chroma_coverage.py
?? tools/full_levels1_3_regression.py
?? tools/full_levels1_3_regression_result.md
?? tools/level1_chroma_coverage_result.md
?? tools/level1_chroma_inventory.json
```

不得：

```text
修改
暂存
删除
恢复
覆盖
```

---

# 35. 文件维护规则

本文件必须在以下事件发生后更新：

```text
批次正式验收完成
阶段切换
新增大板块
技术债延期
技术债关闭
训练资产数量变化
正式基线提交变化
路线顺序变化
候选饱和判断变化
```

更新时至少修改：

```text
当前正式基线
当前训练资产
当前阶段
已完成批次
当前唯一动作
后续任务顺序
新增技术债
```

---

# 36. 当前任务看板

| 板块 | 状态 | 当前节点 |
|---|---|---|
| PostgreSQL Level 1 | 已完成 | 115 表 DDL / Metadata |
| PostgreSQL Level 2 | 已完成 | Batch 01—10完成，候选饱和REACHED |
| PostgreSQL Level 3 | 已正式收口 | Batch 01已交付，其余候选登记为延期能力 |
| PostgreSQL F5 总验收 | 已完成 | F1—F5最终验收通过，PostgreSQL训练板块关闭 |
| F6 DDL 幂等治理 | 进行中 | I-B首次执行安全停止；I-B-R1已完成；I-B-R2已完成待审查；I-C未开始 |
| Vanna 源码移除 | 已排期 | F5 / F6 关键基线后、MySQL 前 |
| 多数据源架构 | 已排期 | Vanna 解耦后 |
| MySQL 训练 | 已登记 | 独立 Metadata 和 Memory |
| 一句话报表 | 已登记 | MySQL 接入后 |
| 外部网站机器人 | 已登记 | 自有 API 和报表稳定后 |
| 生产化治理 | 已登记 | 最后阶段 |

---

# 37. 当前唯一动作

```text
等待 ChatGPT 审查并决定是否明确授权 F6-1I-C。
```

当前不得治理正式198条Chroma，不得新增正式Memory，不得执行正式切换；不得自动进入F6-1I-C或开展Legacy、Vanna解耦、MySQL及其他板块。后续阶段必须经新的明确授权。

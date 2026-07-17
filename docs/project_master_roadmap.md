# 项目总路线与任务台账

> 建议文件路径：`docs/project_master_roadmap.md`
> 用途：作为本项目跨阶段、跨对话的唯一总路线依据。
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

最后已验收提交：

```text
adfd751e771e57c7b6dc0515c09db374ae0aff13
```

当前正式训练资产：

```text
正式运行 Chroma 总记录数：197

原有 Text Memory：8
Level 1 DDL Text Memory：115
LEGACY_READ_ONLY Tool Memory：64
确定性受控 Tool Memory：10
Tool Memory 总数：74
```

当前阶段：

```text
F5 Level 2最终收口审计
```

当前禁止越界进入：

```text
Level 3 补充训练
F5 总验收
F6 治理
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
F5       ⏳ Level 2 / Level 3 受控训练进行中
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
Level 2已收口：NO
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
Level 2候选饱和信号：NOT_REACHED
Level 2已收口：NO
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

当前阶段：

```text
F5 Level 2最终收口审计
```

---

## 4. 大板块执行顺序

后续大板块必须按以下顺序推进：

```text
A. PostgreSQL 当前训练板块收口
→ B. Vanna 依赖解耦并移除项目内源码副本
→ C. 建立多数据源底座
→ D. 接入并训练 MySQL
→ E. 开发“一句话生成报表”
→ F. 集成为外部网站机器人模块
→ G. 生产化、安全与运维治理
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

## 5. F5 当前目标

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

## 6. 当前 F5 内部顺序

```text
1. 完成 F5 Batch 10 候选发现 ✅
2. 完成 F5 Level 2 收口审计 ✅
3. 完成4张指定表最后定向只读发现与Batch 10范围冻结 ✅
4. 正式交付1条rs_sewage_info_v2标准Level 2 Tool Memory ✅
5. 整理数据缺失、暂缓和受控特例登记
6. 盘点现有 64 条 legacy Level 2 / Level 3 能力
7. 识别真正缺失的高价值 Level 3 场景
8. 只补少量核心 Level 3
9. 执行 F5 PostgreSQL 总验收
10. 正式关闭 PostgreSQL 训练板块
```

当前唯一动作：

```text
复核Batch 10交付后的Level 2最终饱和状态。
```

判断分支：

```text
若发现合格候选：
→ 冻结并交付一条Batch 10
→ Level 2继续保持未收口

若4张表全部无候选：
→ 形成Batch 09交付后的第二轮独立无候选证据
→ Level 2候选饱和状态可判定为REACHED
→ 单独执行Level 2收口落盘
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
F6-1A 复现 F1 阶段 25→50 重复写入
F6-1B 生成 DDL Text Memory 确定性身份
F6-1C 实现 ddl_memory_plan.py
F6-1D 实现 ddl_memory_adapter.py
F6-1E 支持 create / unchanged / changed / removed
F6-1F 隔离验证重复运行记录数不增长
F6-1G 审计正式 115 条 DDL Memory 是否存在历史重复
F6-1H 评估重复记录对 Top-K 检索的影响
F6-1I 制定正式库治理与恢复方案
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
| PostgreSQL Level 2 | 进行中 | Batch 10已完成；等待Level 2最终收口审计 |
| PostgreSQL Level 3 | 待盘点 | Level 2 饱和后 |
| PostgreSQL F5 总验收 | 未开始 | Level 2 / Level 3 收口后 |
| F6 DDL 幂等治理 | 已登记 | 包含 F1 25→50 遗留 |
| Vanna 源码移除 | 已排期 | F5 / F6 关键基线后、MySQL 前 |
| 多数据源架构 | 已排期 | Vanna 解耦后 |
| MySQL 训练 | 已登记 | 独立 Metadata 和 Memory |
| 一句话报表 | 已登记 | MySQL 接入后 |
| 外部网站机器人 | 已登记 | 自有 API 和报表稳定后 |
| 生产化治理 | 已登记 | 最后阶段 |

---

# 37. 当前唯一动作

```text
复核Batch 10交付后的Level 2最终饱和状态。
```

本阶段不得开始其他大板块。

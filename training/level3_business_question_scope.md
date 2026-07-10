# 第 3 级业务问法范围设计文档

## 基础信息

- 当前工作目录：E:\3\posgresql\1
- 远端仓库：https://github.com/adso123456/1.git
- 当前 commit：7c9beec3a99cdf4f1b4af95b7b75547cb21f7844
- 文档状态：范围设计（不涉及训练）
- 生成日期：2026-07-10

---

## 前置风险判断

| # | 检查项 | 值 |
|---|--------|-----|
| 1 | 是否启动真实主服务 | 否 |
| 2 | 是否连接数据库 | 否 |
| 3 | 是否执行真实 SQL | 否 |
| 4 | 是否调用 DeepSeek | 否 |
| 5 | 是否训练 Vanna | 否 |
| 6 | 是否调用 vn.train() | 否 |
| 7 | 是否写入正式 ChromaDB | 否 |
| 8 | 是否修改主服务 | 否 |
| 9 | 是否进入第 3 级训练 | 否，本阶段只做范围设计 |
| 10 | 是否进入第 4 级图表训练 | 否 |

---

## 1. 第 2 级验收基线

```text
第 2 级验收通过
10 题全量隔离验证：9 pass / 1 warning / 0 fail
Q1 日趋势 pH/溶解氧：pass
Q2 小时趋势：pass
Q3 月趋势：pass（从之前 fail 修复为 pass）
Q4 排污口编码：pass
Q5 排污口基础信息：pass
Q6 站点名称和区域：pass
Q7 区域编码和名称：pass
Q8 取水口名称和水源类型：pass
Q9 阈值表拦截：pass，true_sql_executed=否
Q10 水源地取水口供水能力：warning / requires_manual_review
正式 vanna_data 指纹未变化（验证前后一致）
正式 agent_data/query_results_*.csv 未新增
未训练 Vanna
未调用 vn.train()
未进入第 3/4 级
```

第 2 级训练样本情况：

| 状态 | 数量 | ID 列表 |
|------|------|---------|
| approved / 已训练 | 16 | L2_SQL_001 ~ L2_SQL_010, L2_SQL_013 ~ L2_SQL_018 |
| requires_manual_review / 未训练 | 3 | L2_SQL_011, L2_SQL_012, L2_SQL_019 |

---

## 2. 第 3 级目标

第 3 级只覆盖**业务问法泛化**，不覆盖图表训练（图表训练属于第 4 级）。

核心目标：在第 2 级已建立的表/字段选择基础上，扩展更多业务场景和问法变体，提升模型对真实用户问法的覆盖度和鲁棒性。

### 2.1 第 3 级覆盖方向

```text
1. 水质趋势泛化问法（年度趋势、多指标对比、跨粒度组合）
2. 排污口业务查询（监测、实况、整治、废水排放趋势）
3. 站点与区域业务查询（断面、水文、摄像头、无人机、水体）
4. 取水口/水源地基础信息（普通取水口扩展、水源信息）
5. 安全边界/拒答类负向样本
```

### 2.2 第 3 级与第 2 级的区别

| 维度 | 第 2 级 | 第 3 级 |
|------|---------|---------|
| 目标 | 建立基础表/字段选择能力 | 泛化业务问法覆盖 |
| 问法复杂度 | 单一意图、单一表为主 | 多表 JOIN、聚合、对比 |
| 表覆盖 | 9 张核心表 | 扩展到 20+ 张业务表 |
| 指标覆盖 | m1~m9 基础水质指标 | 年度指标、废水指标、监测指标 |
| 安全边界 | Q9 阈值表拦截 | 增加多类型拒答样本 |

---

## 3. 第 3 级不做什么

```text
1. 不做图表生成训练（第 4 级）
2. 不做第 4 级图表配置
3. 不训练 requires_manual_review 样本（L2_SQL_011, L2_SQL_012, L2_SQL_019 保持冻结）
4. 不放宽 Q9 / SQL Guard 拦截规则
5. 不新增 DDL/DML 能力
6. 不引导查询系统表（information_schema, pg_catalog）
7. 不训练 SELECT *
8. 不训练 wm_waterquality_threshold 水质趋势查询
9. 不把 Q10 直接 approved
10. 不修改主服务
11. 不修改 SQL Guard
12. 不修改 P0 / metadata context enhancer
13. 不新增数据库 COMMENT ON / DDL
```

---

## 4. 候选业务问法分组

### A 组：水质趋势增强（年度 + 多指标对比）

**业务目标**：将第 2 级的水质趋势从"日/小时/月单一粒度趋势"扩展到年度趋势、多指标横向对比、跨站点对比。

**建议样本数量**：8~12

**代表问题列表**：

| # | 问题 | 预期表 | 关键字段 |
|---|------|--------|---------|
| A1 | 查看某站点年度水质趋势 | wm_waterquality_year_records | station_id, monitor_year, m2_value, m3_value, water_quality_level |
| A2 | 某站点年度水质各指标汇总 | wm_waterquality_year_records | station_id, monitor_year, m1~m9_value, water_quality_level |
| A3 | 对比两个站点的 pH 和溶解氧年变化 | wm_waterquality_year_records | station_id, monitor_year, m2_value, m3_value |
| A4 | 某站点水质各指标年度排名 | wm_waterquality_year_records | station_id, monitor_year, m*_value |
| A5 | 查询某站点水质月趋势中的氨氮和总氮 | wm_waterquality_month_records | station_id, monitor_year, monitor_month, m8_value, m9_value |
| A6 | 查看某站点 pH 和溶解氧在最近 12 个月的变化趋势 | wm_waterquality_month_records | station_id, monitor_year, monitor_month, m2_value, m3_value |
| A7 | 某站点不同水质等级在月记录中的占比 | wm_waterquality_month_records | station_id, water_quality_level |
| A8 | 查询年度水质最优/最劣站点排名 | wm_waterquality_year_records | station_id, monitor_year, water_quality_level, m*_value |

**关键表**：
- `wm_waterquality_year_records`（水质监测年记录表）——第 2 级未覆盖
- `wm_waterquality_month_records`（水质监测月记录表）——第 2 级已覆盖 2 条，第 3 级扩展

**关键字段**：
- `wm_waterquality_year_records.monitor_year`, `.m1_value` ~ `.m9_value`, `.water_quality_level`
- `wm_waterquality_month_records.monitor_year`, `.monitor_month`, `.m8_value`（氨氮）, `.m9_value`（总氮）

**是否允许训练**：是（SQL Guard 校验通过后）

**风险点**：
- 年度表字段与日/小时/月表结构相似（m*_value + m*_count），需确保不混淆
- 年度记录可能较少，LIMIT 设定需合理（建议 ≤100）
- "最优/最劣"排序方向需在 question 或 SQL 中明确（ASC/DESC）

**验收标准**：
- 静态 SQL Guard 全部 passed=True, severity=ok
- 不误用 wm_waterquality_threshold
- 不退化到日/小时表
- 不出现 SELECT *

**优先级**：P0

---

### B 组：水质指标对比/排名

**业务目标**：训练模型在不同指标间做对比、排名，处理"最好/最差/变化最大"等业务语义。

**建议样本数量**：6~10

**代表问题列表**：

| # | 问题 | 预期表 | 关键字段 |
|---|------|--------|---------|
| B1 | 查看某站点日记录中 pH 最高和最低的日期 | wm_waterquality_day_records | station_id, monitor_time, m2_value |
| B2 | 某站点小时记录中溶解氧低于某阈值的时段 | wm_waterquality_hour_records | station_id, monitor_time, m3_value |
| B3 | 对比多个水质指标在某站点的日平均值 | wm_waterquality_day_records | station_id, m2_value, m3_value, m4_value, m5_value |
| B4 | 查询某站点最近一个月各水质等级天数统计 | wm_waterquality_day_records | station_id, water_quality_level, monitor_time |
| B5 | 某站点日记录中水质等级为劣V类的记录列表 | wm_waterquality_day_records | station_id, monitor_time, water_quality_level |
| B6 | 查询月度水质达标的站点列表 | wm_waterquality_month_records | station_id, water_quality_level, monitor_year, monitor_month |

**关键表**：
- `wm_waterquality_day_records`（已有 L2_SQL_001, L2_SQL_002, L2_SQL_003）
- `wm_waterquality_hour_records`（已有 L2_SQL_004, L2_SQL_005, L2_SQL_006）
- `wm_waterquality_month_records`（已有 L2_SQL_007, L2_SQL_008）

**关键字段**：
- `m1_value`（水温）~ `m9_value`（总氮）以及 `m10_value`（化学需氧量）
- `water_quality_level`（I/II/III/IV/V/劣V）

**是否允许训练**：是（SQL Guard 校验通过后）

**风险点**：
- "低于阈值"、"达标"等比较条件需在 SQL 中给出具体阈值或使用 IS NOT NULL 过滤
- "最近一个月"等相对时间表述在 SQL 示例中应使用固定日期，避免模型学到硬编码日期
- 水质等级 "劣V" 类在 question 中需正确书写，避免编码问题

**验收标准**：
- 静态 SQL Guard 全部 passed=True, severity=ok
- 比较运算符（<, >, BETWEEN）不出现在 forbidden_operations 中
- 不误用 wm_waterquality_threshold

**优先级**：P0

---

### C 组：排污口业务查询（监测 + 实况 + 整治 + 废水排放）

**业务目标**：将排污口从"基础信息/编码查询"扩展到监测数据、实况状态、整治进展、废水排放趋势等完整业务场景。

**建议样本数量**：10~16

**代表问题列表**：

| # | 问题 | 预期表 | 关键字段 |
|---|------|--------|---------|
| C1 | 查询排污口监测数据中的 COD 和氨氮 | rs_outlet_monitor_v2 | outlet_name, sampling_time, cod, ammonia_nitrogen |
| C2 | 查看某排污口最近的 pH 监测记录 | rs_outlet_monitor_v2 | outlet_name, sampling_time, ph |
| C3 | 查询排污口实况状态（排水特征、在线监测情况） | rs_outlet_live_v2 | outlet_name, drainage_feature, has_online_monitor, has_abnormal |
| C4 | 统计有异常状况的排污口数量 | rs_outlet_live_v2 | outlet_name, has_abnormal |
| C5 | 查询已整治的排污口及整治类型 | rs_outlet_remediation_v2 | outlet_name, is_remediated, remediation_type, is_standardized |
| C6 | 查询排污口废水日排放趋势（COD/总氮/总磷） | rs_wastewater_day_records | pollutant_id, timestamp, m1_value, m2_value, m3_value |
| C7 | 查看排污口废水小时排放流量趋势 | rs_wastewater_hour_records | pollutant_id, timestamp, ll, pfl |
| C8 | 排污口月度废水排放总量统计 | rs_wastewater_month_records | pollutant_id, monitor_year, monitor_month, ll, pfl |
| C9 | 按区域统计排污口整治完成率 | rs_outlet + rs_outlet_remediation_v2 | area_name, is_remediated |
| C10 | 查询具备采样条件的排污口列表 | rs_outlet_live_v2 | outlet_name, has_sampling_condition |

**关键表**：
- `rs_outlet_monitor_v2`（排污口监测）——第 2 级未覆盖
- `rs_outlet_live_v2`（排污口实况）——第 2 级未覆盖
- `rs_outlet_remediation_v2`（排污口整治）——第 2 级未覆盖
- `rs_wastewater_day_records`（废水日记录）——第 2 级未覆盖
- `rs_wastewater_hour_records`（废水小时记录）——第 2 级未覆盖
- `rs_wastewater_month_records`（废水月记录）——第 2 级未覆盖
- `rs_outlet`（排污口基础）——第 2 级已覆盖 2 条

**关键字段**：
- `rs_outlet_monitor_v2.cod`, `.ammonia_nitrogen`, `.ph`, `.bod`, `.flow`, `.total_phosphorus`, `.total_nitrogen`
- `rs_outlet_live_v2.drainage_feature`, `.has_online_monitor`, `.has_abnormal`, `.has_sampling_condition`
- `rs_outlet_remediation_v2.is_remediated`, `.remediation_type`, `.is_standardized`
- `rs_wastewater_*_records.ll`（流量）, `.pfl`（排放量）, `.m1_value` ~ `.m22_value`

**是否允许训练**：是（SQL Guard 校验通过后）

**风险点**：
- 废水表 (rs_wastewater_*) 的 m*_value 语义与水质表不同：PS（排水口）=COD/总氮/pH，PQ（排口）=烟尘/二氧化硫/氮氧化物，需在 question 中明确上下文避免混淆
- 废水表与排污口基础表通过 pollutant_id 关联，JOIN 场景需谨慎设计
- L2_SQL_011, L2_SQL_012 的溯源场景仍冻结，不能在此组中变相引入
- 排污口监测/实况/整治表均有 outlet_name 字段，不与 rs_outlet 混淆

**验收标准**：
- 静态 SQL Guard 全部 passed=True, severity=ok
- 废水表指标字段选择与 PS/PQ 类型语义一致
- 不涉及溯源口径（不绕过 L2_SQL_011/L2_SQL_012 的冻结）
- 不出现 SELECT *

**优先级**：P1

---

### D 组：站点/区域业务查询（断面 + 水文 + 水体 + 扩展信息）

**业务目标**：将站点和区域从"名称/编码查询"扩展到断面、水文站、水体信息、摄像头/无人机等扩展设备信息。

**建议样本数量**：8~14

**代表问题列表**：

| # | 问题 | 预期表 | 关键字段 |
|---|------|--------|---------|
| D1 | 查询断面基础信息和所属水体 | wm_section_info | section_code, section_name, section_level, water_body_id |
| D2 | 查询考核断面的水质目标等级 | wm_section_info + wm_section_wq_info | section_name, year, water_quality_target_level |
| D3 | 查询水文站基础信息 | wm_hydrological_info | station_code, station_name, water_type, region_code |
| D4 | 按城市统计水文站数量 | wm_hydrological_info | belong_to_city, station_code |
| D5 | 查询水体基础信息 | wm_waterbody_info | —（需确认 metadata 中实际字段） |
| D6 | 查看某区域的监测站点和对应摄像头 | wm_station_info_v2 + wm_camera_info | station_name, camera 相关字段 |
| D7 | 查询摄像头平台覆盖情况 | wm_camera_info + wm_camera_platform | camera 相关字段 |
| D8 | 查看无人机巡检信息 | wm_uav_info | —（需确认 metadata 中实际字段） |
| D9 | 按区域层级查看站点分布 | wm_station_info_v2 + gis_region | station_name, region_name, region_level |
| D10 | 查询城市级别区域列表 | gis_region_city | region_name, region_code |
| D11 | 查询区县级区域列表 | gis_region_county | region_name, region_code, city |

**关键表**：
- `wm_section_info`（断面基础信息表）——第 2 级未覆盖
- `wm_section_wq_info`（断面水质目标信息表）——第 2 级未覆盖
- `wm_hydrological_info`（水文站基础信息表）——第 2 级未覆盖
- `wm_waterbody_info`（水体信息表）——第 2 级未覆盖
- `wm_camera_info` / `wm_camera_platform`（摄像头）——第 2 级未覆盖
- `wm_uav_info`（无人机）——第 2 级未覆盖
- `gis_region_city` / `gis_region_county`（区域层级）——第 2 级未覆盖
- `wm_station_info_v2`（站点信息）——第 2 级已覆盖 2 条
- `gis_region`（区域编码）——第 2 级已覆盖 1 条

**关键字段**：
- `wm_section_info.section_code`, `.section_name`, `.section_level`, `.section_nature`, `.is_examine`
- `wm_section_wq_info.year`, `.month`, `.water_quality_target_level`
- `wm_hydrological_info.station_code`, `.station_name`, `.water_type`, `.belong_to_city`
- `gis_region_city.region_name`, `.region_code`
- `gis_region_county.region_name`, `.region_code`, `.city`

**是否允许训练**：是（SQL Guard 校验通过后，部分表需先确认 metadata 中实际有可用字段）

**风险点**：
- `wm_waterbody_info`, `wm_uav_info`, `wm_camera_info` 在 metadata index 中存在但尚未逐字段确认语义，样本设计前需确认字段可用性
- 断面 + 水质目标的 JOIN 需要 section_id 关联，字段映射需准确
- 区域层级表（gis_region_city/county）与 gis_region 的关系需理清，避免重复覆盖
- 水文站信息表字段较多（70+），需精选代表性字段，不全部暴露

**验收标准**：
- 静态 SQL Guard 全部 passed=True, severity=ok
- 表选择不退化到 generic name/type 表
- 断面、水文、站点三类场景互不混淆
- 不出现 SELECT *

**优先级**：P1

---

### E 组：取水口/水源地业务查询

**业务目标**：在第 2 级已覆盖 `wm_water_intake` 基础查询的基础上，扩展到水源信息、水源地保护区、水源状态等更多业务场景。**注意：不使用 `wm_water_source_intake_v2`**。

**建议样本数量**：6~10

**代表问题列表**：

| # | 问题 | 预期表 | 关键字段 |
|---|------|--------|---------|
| E1 | 按水源类型统计取水口分布 | wm_water_intake | water_type, name, region_name |
| E2 | 按城市查看取水口数量和类型 | wm_water_intake | city, water_type, name |
| E3 | 查询水源地基础信息和保护区级别 | wm_water_source | name, source_type, protect_level, source_state |
| E4 | 按水源类型统计水源地数量 | wm_water_source | source_type, name |
| E5 | 查询供水人口最多的水源地 | wm_water_source | name, service_people_count, supply_water_daily |
| E6 | 查询水源地保护区划定状态 | wm_water_source | name, protect_area_status, protect_level, protect_area_cert |
| E7 | 按流域查看取水口分布 | wm_water_intake | name, region_name, water_type |
| E8 | 查看某水源地的年实际取水量 | wm_water_source | name, supply_water_year, supply_water_daily |

**关键表**：
- `wm_water_intake`（取水口）——第 2 级已覆盖 1 条（L2_SQL_018），第 3 级扩展
- `wm_water_source`（水源地基础信息）——第 2 级未覆盖
- **不使用** `wm_water_source_intake_v2`（水源地取水口，Q10 相关表）

**关键字段**：
- `wm_water_intake.name`, `.city`, `.county`, `.water_type`, `.region_name`
- `wm_water_source.name`, `.source_type`, `.source_state`, `.protect_level`, `.service_people_count`, `.supply_water_daily`, `.supply_water_year`

**是否允许训练**：是（SQL Guard 校验通过后，且不涉及 wm_water_source_intake_v2）

**风险点**：
- 严格区分 `wm_water_intake`（普通取水口）和 `wm_water_source_intake_v2`（水源地取水口）
- `wm_water_source` 字段众多（50+），需精选，不暴露敏感或未确认口径的字段（如经纬度、地下水位等）
- Q10（水源地取水口供水能力）仍为 requires_manual_review，E 组样本不能变相覆盖 Q10 的场景
- "供水能力"语义在 `wm_water_source` 中使用 `supply_water_daily`（日供水量）和 `supply_water_year`（年实际取水量），不涉及 `wm_water_source_intake_v2.daily_supply_capacity`

**验收标准**：
- 静态 SQL Guard 全部 passed=True, severity=ok
- 不使用 wm_water_source_intake_v2
- 不覆盖 Q10 的 requires_manual_review 场景
- 不出现 SELECT *

**优先级**：P1

---

### F 组：负向安全边界样本

**业务目标**：训练模型在面对危险/越权请求时正确拒绝或触发 SQL Guard，不生成可执行 SQL。

**建议样本数量**：8~12

**代表问题列表**：

| # | 问题 | 预期行为 | 拦截机制 |
|---|------|---------|---------|
| F1 | 给我导出所有排污口数据到 CSV | 拒绝 DML/导出请求 | SQL Guard: forbidden_operations |
| F2 | 删除某站点所有水质记录 | 拒绝 DELETE | SQL Guard: forbidden_operations |
| F3 | 修改排污口编码 | 拒绝 UPDATE | SQL Guard: forbidden_operations |
| F4 | 查询系统表里的用户密码 | 拒绝系统表访问 | SQL Guard: system_table_prefixes |
| F5 | 查询 wm_waterquality_threshold 的水质趋势 | 拒绝阈值表查询 | SQL Guard / hard block（复用 Q9 机制） |
| F6 | 创建一个新的排污口记录 | 拒绝 CREATE/INSERT | SQL Guard: forbidden_operations |
| F7 | 查询所有字段 SELECT * FROM rs_outlet | 拒绝 SELECT * | SQL Guard: SELECT * 检测 |
| F8 | 查一下水质阈值表的分类标准 | 拒绝阈值表访问 | SQL Guard / hard block |
| F9 | 修改数据库表结构加一列 | 拒绝 ALTER TABLE | SQL Guard: forbidden_operations |
| F10 | 查所有数据不加 LIMIT | 拒绝无 LIMIT 查询 | SQL Guard: LIMIT 检测 |

**关键表**：
- 不涉及正常业务表查询
- F5/F8 涉及 `wm_waterquality_threshold`（必须拦截）

**是否允许训练**：是（作为负向样本训练，SQL 列为空或为占位拒绝语句）

**风险点**：
- 负向样本需区别于 requires_manual_review：后者是"可能合法但口径未确认"，负向是"确定不应该执行"
- 负向样本训练时不写入可执行 SQL，question 映射到 SQL Guard 拦截行为
- 需确认训练框架支持负向样本格式（sql 字段为空、或标记为 blocked/refused）
- 不要在负向样本中引入新的表或字段知识

**验收标准**：
- 每个负向样本在隔离验证时 SQL Guard 必须 passed=False 或 severity=blocked
- 不能有 true_sql_executed=是
- 不能覆盖 Q10 等 requires_manual_review 场景

**优先级**：P3（第 3 级最后执行，不影响正向业务样本训练）

---

## 5. 样本准入规则

所有进入第 3 级的候选样本必须满足以下全部规则：

```text
1. 必须是 SELECT 语句（或负向样本为 blocked/empty）
2. 必须带 LIMIT（正向样本）
3. 禁止 SELECT *（必须显式列出字段）
4. SQL Guard 必须 passed=True 且 severity=ok（正向样本）
5. 表和字段必须来自 agent_data/column_metadata_index.json
6. 不能涉及 wm_waterquality_threshold 水质趋势查询
7. 不能涉及 DDL / DML / 系统表
8. 不能涉及 requires_manual_review 未确认场景（L2_SQL_011, L2_SQL_012, L2_SQL_019 及其扩展）
9. 不能把 Q10 直接 approved
10. 每条样本必须有明确业务问题和可解释 SQL
11. 问题文本必须使用中文，清晰表达业务意图
12. SQL 中的固定值（station_id, 日期等）仅作为示例条件，不引导模型固定使用
```

---

## 6. requires_manual_review 保留项

以下场景在第 3 级继续冻结，不允许训练：

```text
Q10 水源地取水口供水能力
  - 当前状态：warning / requires_manual_review
  - 冻结原因：wm_water_source_intake_v2 的 daily_supply_capacity / annual_actual_withdrawal 字段口径未确认
  - 第 3 级处理：继续冻结，不新增相关样本

L2_SQL_011 排污口溯源责任主体统计
  - 当前状态：requires_manual_review / 未训练
  - 冻结原因：rs_outlet_trace_v2.primary_entity_name 责任主体统计口径未确认
  - 第 3 级处理：继续冻结

L2_SQL_012 排污口溯源企业和排放许可证
  - 当前状态：requires_manual_review / 未训练
  - 冻结原因：溯源企业、排放许可证、信用代码字段口径需人工确认
  - 第 3 级处理：继续冻结

wm_water_source_intake_v2 相关口径不清问题
  - 涉及字段：daily_supply_capacity（日供水能力）、annual_actual_withdrawal（年实际取水量）
  - 问题："供水能力"字段在 wm_water_intake、wm_water_source、wm_water_source_intake_v2 三张表中语义不完全一致
  - 第 3 级处理：E 组只使用 wm_water_intake 和 wm_water_source，不使用 wm_water_source_intake_v2

跨表统计口径未确认问题
  - 例如：排污口数量统计口径（rs_outlet vs rs_outlet_info_v2 vs rs_outlet_live_v2）
  - 例如：站点数量统计口径（wm_station_info vs wm_station_info_v2）
  - 第 3 级处理：样本中对涉及多表口径的场景加 review_notes 标注，不确定的不进训练
```

---

## 7. 第 3 级优先级排序

```text
P0（最高优先，第 3 级必须先做）：
  A 组：水质趋势增强（年度趋势 + 多指标对比）
  B 组：水质指标对比/排名

P1（第 3 级核心扩展）：
  C 组：排污口业务查询（监测 + 实况 + 整治 + 废水排放）
  D 组：站点/区域业务查询（断面 + 水文 + 水体 + 扩展信息）
  E 组：取水口/水源地业务查询

P2（条件允许时做）：
  统计聚合类问法（跨表 COUNT/SUM/AVG 等复杂聚合）
  JOIN 场景（如站点-断面-水质目标三表关联）

P3（安全兜底，最后做）：
  F 组：负向安全边界样本
```

---

## 8. 第 3 级验收标准

第 3 级训练完成后，验收必须满足以下全部条件：

```text
1. 静态 SQL Guard 通过率 = 100%（正向样本 passed=True, severity=ok）
2. metadata 表字段命中率 ≥ 95%（每样本所用表字段均在 column_metadata_index.json 中）
3. 隔离验证通过率 ≥ 90%（类似第 2 级 10 题验证，通过标准 ≥ 90%）
4. Q9 安全回归：wm_waterquality_threshold 查询仍被拦截，true_sql_executed=否
5. Q10 manual-review 保持 warning：不因第 3 级训练而意外 pass
6. 正式 vanna_data 指纹控制：训练前后比对，仅允许 ChromaDB 存储文件变化
7. 正式 agent_data 不污染：不新增 query_results_*.csv
8. 不出现 requires_manual_review 样本被训练
9. 不出现 SELECT * 样本被训练
10. 不出现 wm_waterquality_threshold 正向样本
11. 每样本 review_notes 中注明风险点和业务语义确认情况
```

---

## 9. 第 3 级估算规模

| 分组 | 建议样本数 | 优先级 |
|------|-----------|--------|
| A 水质趋势增强 | 8~12 | P0 |
| B 水质指标对比/排名 | 6~10 | P0 |
| C 排污口业务查询 | 10~16 | P1 |
| D 站点/区域业务查询 | 8~14 | P1 |
| E 取水口/水源地业务查询 | 6~10 | P1 |
| F 负向安全边界样本 | 8~12 | P3 |
| **合计** | **46~74** | — |

建议第一期（P0+P1）控制在 40~55 条样本，P3 负向样本独立成批。

---

## 10. 推荐下一阶段

```text
下一阶段：第 3 级候选样本草案设计
  - 基于本文档的分组和准入规则，逐条设计候选 SQL 示例
  - 每样本通过 SQL Guard 静态校验
  - 产出文件：training/sql_examples_level3_draft.json
  - 仍不训练、不写 ChromaDB、不改主服务

后续阶段规划：
  1. [当前] 第 3 级业务问法范围设计（本文档）
  2. [下一步] 第 3 级候选样本草案设计（逐条 SQL + 静态校验）
  3. [后续] 第 3 级样本审查（人工确认 + SQL Guard + 安全复核）
  4. [后续] 第 3 级训练写入（受控写入 ChromaDB）
  5. [后续] 第 3 级验证（10+ 题隔离验证）
  6. [远期] 第 4 级图表配置/训练（另一个阶段）
```

---

## 确认声明

```text
当前只是范围设计
不含可直接训练 JSON
不调用 vn.train()
不写 ChromaDB
不进入第 4 级
不启动真实主服务
不连接数据库
不执行真实 SQL
不调用 DeepSeek
不修改主服务
不修改 SQL Guard
不修改 P0 / metadata context
```

---

## 文档版本

- v1.0, 2026-07-10：初版范围设计
- 预计下一版：样本草案设计完成后更新

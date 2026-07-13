# Level 3 P1 精确范围计划

## 1. 基础信息

- 工作目录：`E:\3\posgresql\1`
- git remote：`origin https://github.com/adso123456/1.git`（fetch/push）
- 基础 commit：`a2bceb272df1071ff0e4bec9892a76244e0bcc29`
- 初始 `git status --short`：

```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```

- 计划文件：`training/level3_p1_scope_plan.md`
- 本阶段性质：范围规划和只读 metadata 字段审计

## 2. Level 3 P0 验收基线

- Level 3 P0 验收结论：通过
- approved 样本：18
- 受控写入成功：18，失败：0
- 写入方式：`memory.save_tool_usage()`，未调用 `vn.train()`
- 最终隔离验证：9 题，7 pass / 2 warning / 0 fail
- Q7 tuple 子查询修复后通过
- SAFE-Q9 在执行前安全拒绝
- Q3/Q8 candidate mismatch 保留为非阻断技术债，本阶段不处理
- P0 已覆盖 A/B 组水质年度、月度、日记录和小时记录业务

## 3. P1 目标与边界

Level 3 P1 只覆盖三组单表业务扩展：

- C 组：排污口监测、实况、整治和废水记录
- D 组：断面、水文、水体、摄像头、无人机和区域层级
- E 组：普通取水口和水源地

边界如下：

- 只规划单表查询或单表简单聚合、筛选、排序
- 所有表和计划字段必须存在于 `agent_data/column_metadata_index.json`
- 不重复 P0 的水质趋势、指标对比和水质等级核心方向
- 不包含跨表 JOIN、跨表聚合或三表关联，这些场景移入 P2
- 不包含冻结场景，不使用 `wm_water_source_intake_v2`
- 不创建可训练 JSON，不编写完整训练 SQL，不训练、不写 ChromaDB
- 不修改 metadata index、P0 candidate ranking 或业务代码

## 4. 精确候选规模：C10 / D8 / E6 / 总计24

| 分组 | 范围 | 候选数量 | ID 规划 |
|---|---|---:|---|
| C | 排污口业务查询 | 10 | L3_P1_SQL_001 至 L3_P1_SQL_010 |
| D | 站点、区域、断面、水文及扩展信息 | 8 | L3_P1_SQL_011 至 L3_P1_SQL_018 |
| E | 取水口和水源地业务查询 | 6 | L3_P1_SQL_019 至 L3_P1_SQL_024 |
| **总计** |  | **24** |  |

24 是候选规模，不代表最终全部 approved。后续人工审查仍可判定为 `approved`、`requires_manual_review` 或 `excluded`。

## 5. C 组详细范围

### 5.1 候选方向与数量

| 编号 | 表 | 候选方向 | 计划字段 | 数量 | 准入说明 |
|---|---|---|---|---:|---|
| C1-C2 | `rs_outlet_monitor_v2` | 排污口 COD/氨氮监测；最近 pH/BOD/流量监测记录 | outlet_name, sampling_time, cod, ammonia_nitrogen, ph, bod, flow, total_phosphorus, total_nitrogen | 2 | 单表筛选、排序，字段语义明确 |
| C3-C4 | `rs_outlet_live_v2` | 排水特征与在线监测状态；异常或具备采样条件筛选 | outlet_name, drainage_feature, has_online_monitor, has_abnormal, has_sampling_condition | 2 | 不选择 geom |
| C5-C6 | `rs_outlet_remediation_v2` | 已整治排污口及整治类型；规范化建设状态筛选或统计 | outlet_name, is_remediated, remediation_type, is_standardized | 2 | 不与 `rs_outlet` JOIN |
| C7 | `rs_wastewater_day_records` | PS 排水口 COD、总氮、pH 日记录或日排放趋势 | pollutant_id, timestamp, type, status, ll, pfl, m1_value, m2_value, m3_value | 1 | 必须限定 `type=PS`，避免 PQ 双重语义 |
| C8 | `rs_wastewater_hour_records` | 废水小时流量、排放量和状态趋势 | pollutant_id, timestamp, type, status, ll, pfl | 1 | 禁止使用语义未说明的 m1-m22 指标 |
| C9 | `rs_wastewater_month_records` | PS 月度 COD、总氮、pH 或月排放量 | pollutant_id, timestamp, monitor_year, monitor_month, type, status, ll, pfl, m1_value, m2_value, m3_value | 1 | 必须限定 `type=PS` |
| C10 | `rs_outlet_live_v2` 或 `rs_outlet_remediation_v2` | 单表异常、采样条件、整治或规范化状态的简单 COUNT/筛选 | outlet_name, has_abnormal, has_sampling_condition, is_remediated, is_standardized | 1 | 草案阶段只能从二者选一，不跨表 |
| **合计** |  |  |  | **10** |  |

### 5.2 C 组禁止内容

- 排污口溯源、责任主体统计、企业和排放许可证关联
- `rs_outlet_trace_v2`、L2_SQL_011、L2_SQL_012
- `rs_outlet` 与整治表的跨表完成率统计
- 复杂多表 JOIN 或跨表聚合
- 未限定 PS/PQ 类型时使用双重语义指标
- `rs_wastewater_hour_records.m1_value` 至 `m22_value` 的具体污染物问法

## 6. D 组详细范围

### 6.1 候选方向与数量

| 编号 | 表 | 候选方向 | 计划字段 | 数量 | 准入说明 |
|---|---|---|---|---:|---|
| D1 | `wm_section_info` | 断面编码、名称、级别、属性和考核状态 | section_code, section_name, section_level, section_nature, is_examine, water_body_id | 1 | 水体 ID 只展示，不 JOIN 水体表 |
| D2-D3 | `wm_hydrological_info` | 水文站基础信息；按城市、水体类型或建站状态简单统计 | station_code, station_name, water_type, region_code, belong_to_city, station_type, build_state | 2 | 不选择联系人、电话或 geom |
| D4 | `wm_waterbody_info` | 水体基础信息、流域或类型筛选 | water_body_code, water_body_name, water_body_type, water_body_function, basin, length, area | 1 | 字段语义明确，不查询 geom |
| D5 | `wm_camera_info` | 摄像头设备基础信息或按监控对象筛选 | camera_name, device_type, device_code, device_supplier, address, monitor_subject | 1 | 不与站点关联，不使用经纬度作为核心问法 |
| D6 | `wm_camera_platform` | 摄像头平台设备、厂商、型号和在线状态 | device_code, name, manufacturer, model, transport, online | 1 | 不与 `wm_camera_info` JOIN，不暴露 IP/端口 |
| D7 | `wm_uav_info` | 无人机名称、品牌、型号和在线状态 | code, name, brand, drone_sn, drone_callsign, drone_device_model, drone_device_online_status, gateway_device_online_status | 1 | 设备模型字段可能为 JSON 文本，只做展示/筛选 |
| D8 | `gis_region_city` 或 `gis_region_county` | 城市或区县行政区名称与编码列表 | region_name, region_code, city | 1 | 草案阶段二选一；`city` 仅 county 表可用 |
| **合计** |  |  |  | **8** |  |

### 6.2 D 组字段审计结论

- `wm_waterbody_info`：26 个字段，水体编码、名称、类型、功能、流域、长度和面积语义明确，准入 P1。
- `wm_camera_info`：16 个字段，设备名称、类型、编码、厂商、地址和监控对象语义明确，准入 P1。
- `wm_camera_platform`：21 个字段，平台设备、厂商、型号、传输方式和在线状态语义明确，准入 P1；IP、端口不作为业务候选核心字段。
- `wm_uav_info`：39 个字段，设备身份、品牌、型号和在线状态语义明确，准入 P1；JSON 文本和 Unix 时间字段列为草案审查风险。

### 6.3 D 组禁止内容

- `wm_section_info + wm_section_wq_info` JOIN
- 站点 + 摄像头 JOIN
- 摄像头 + 平台 JOIN
- 站点 + `gis_region` JOIN
- 断面 + 水体 JOIN
- 三表关联或跨表统计

## 7. E 组详细范围

### 7.1 候选方向与数量

| 编号 | 表 | 候选方向 | 计划字段 | 数量 | 准入说明 |
|---|---|---|---|---:|---|
| E1 | `wm_water_intake` | 按水源类型统计普通取水口 | name, water_type | 1 | 单表 COUNT/GROUP BY |
| E2 | `wm_water_intake` | 按城市或区县查看普通取水口 | name, city, county, water_type | 1 | 不关联区域表 |
| E3 | `wm_water_intake` | 按行政区域查看普通取水口及使用状态 | name, region_code, region_name, water_type, used_mark | 1 | 与水源地取水口口径分离 |
| E4 | `wm_water_source` | 水源地基础信息、类型和状态 | name, source_type, source_state, region_name | 1 | 单表基础查询 |
| E5 | `wm_water_source` | 水源地保护等级或保护区划定状态 | name, protect_level, protect_area_status, protect_area_cert | 1 | 不推断未记录的保护结论 |
| E6 | `wm_water_source` | 水源地服务人口或年实际取水量排序/筛选 | name, service_people_count, supply_water_year, supply_water_daily | 1 | 明确字段来自水源地表，不表述为取水口供水能力 |
| **合计** |  |  |  | **6** |  |

### 7.2 E 组禁止内容

- `wm_water_source_intake_v2`
- 水源地取水口供水能力、`daily_supply_capacity`、该表口径的 `annual_actual_withdrawal`
- Q10 requires_manual_review 场景
- 混用 `wm_water_intake`、`wm_water_source` 和 `wm_water_source_intake_v2`

## 8. 元数据字段审计表

审计对象共 16 张表。字段数量和存在性均来自 `agent_data/column_metadata_index.json`。

| 表名 | 存在 | 字段数 | 计划使用字段 | 缺失计划字段 | 语义是否明确 | 准入 P1 | 风险备注 |
|---|---|---:|---|---|---|---|---|
| rs_outlet_monitor_v2 | 是 | 19 | outlet_name, sampling_time, cod, ammonia_nitrogen, ph, bod, flow, total_phosphorus, total_nitrogen | 无 | 是 | 是 | 仅单表监测 |
| rs_outlet_live_v2 | 是 | 14 | outlet_name, drainage_feature, has_online_monitor, has_abnormal, has_sampling_condition | 无 | 是 | 是 | 排除 geom |
| rs_outlet_remediation_v2 | 是 | 14 | outlet_name, is_remediated, remediation_type, is_standardized | 无 | 是 | 是 | 不做跨表完成率 |
| rs_wastewater_day_records | 是 | 59 | pollutant_id, timestamp, type, status, ll, pfl, m1_value, m2_value, m3_value | 无 | 条件明确 | 是 | m1-m3 必须限定 type=PS |
| rs_wastewater_hour_records | 是 | 57 | pollutant_id, timestamp, type, status, ll, pfl | 无 | 计划字段明确；m1-m22 不明确 | 是（受限） | 禁用 m1-m22 具体指标语义 |
| rs_wastewater_month_records | 是 | 62 | pollutant_id, timestamp, monitor_year, monitor_month, type, status, ll, pfl, m1_value, m2_value, m3_value | 无 | 条件明确 | 是 | m1-m3 必须限定 type=PS |
| wm_section_info | 是 | 26 | section_code, section_name, section_level, section_nature, is_examine, water_body_id | 无 | 是 | 是 | water_body_id 不用于 JOIN |
| wm_hydrological_info | 是 | 40 | station_code, station_name, water_type, region_code, belong_to_city, station_type, build_state | 无 | 是 | 是 | 排除联系人、电话、geom |
| wm_waterbody_info | 是 | 26 | water_body_code, water_body_name, water_body_type, water_body_function, basin, length, area | 无 | 是 | 是 | 排除 geom |
| wm_camera_info | 是 | 16 | camera_name, device_type, device_code, device_supplier, address, monitor_subject | 无 | 是 | 是 | 不与站点 JOIN |
| wm_camera_platform | 是 | 21 | device_code, name, manufacturer, model, transport, online | 无 | 是 | 是 | 不暴露 IP/端口，不与 camera_info JOIN |
| wm_uav_info | 是 | 39 | code, name, brand, drone_sn, drone_callsign, drone_device_model, drone_device_online_status, gateway_device_online_status | 无 | 基本明确 | 是 | JSON 文本仅展示，需人工复核问法 |
| gis_region_city | 是 | 4 | region_name, region_code | 无 | 是 | 是 | 排除 geom |
| gis_region_county | 是 | 5 | region_name, region_code, city | 无 | 是 | 是 | 排除 geom |
| wm_water_intake | 是 | 14 | name, city, county, water_type, region_code, region_name, used_mark | 无 | 是 | 是 | 与 source_intake_v2 严格区分 |
| wm_water_source | 是 | 36 | name, source_type, source_state, region_name, protect_level, protect_area_status, protect_area_cert, service_people_count, supply_water_daily, supply_water_year | 无 | 是 | 是 | 不表述为 source_intake_v2 供水能力 |

审计汇总：

- 审计表数量：16
- metadata index 不存在的表：无
- 存在缺失计划字段的表：无
- 字段语义不明确的表：`rs_wastewater_hour_records`（仅 m1_value 至 m22_value；这些字段不纳入 P1 计划）
- 全部计划使用字段均来自 metadata index：是

## 9. P0 去重检查

### 9.1 P0 已覆盖业务方向

- 年度水质趋势、年度指标汇总、年度站点对比和年度水质排名
- 水质月趋势、月度指标变化和月度水质等级分布/列表
- 日记录最高/最低、日均指标、最近月份等级统计和劣 V 类筛选
- 小时溶解氧低值时段

### 9.2 P1 新增业务方向

- 排污口监测、实况、整治和废水日/小时/月记录
- 断面、水文、水体、摄像头平台、无人机和区域层级单表查询
- 普通取水口分类/区域查询和水源地状态、保护、服务人口/取水量查询

### 9.3 可能重叠点与处理

| 可能重叠点 | 去重处理方式 |
|---|---|
| 趋势、最高/最低、统计等通用问法形式 | 仅复用查询形式，不复用 P0 水质记录表、指标组合或业务意图 |
| E 组普通取水口与 Level 2 基础取水口问法 | P1 只做类型统计、城市/区县和使用状态扩展，不重复单纯名称查询 |
| 区域名称/编码与既有 gis_region 问法 | P1 使用 city/county 层级表，强调行政层级，不重复通用区域编码问法 |

- 与 P0 核心业务重复数量：0
- P1 候选草案阶段必须再次进行逐条文本去重：是

## 10. 移入 Level 3 P2 的场景

以下 9 类场景只登记，不设计 SQL、不实施：

1. 排污口基础表与整治表 JOIN
2. 排污口跨表完成率统计
3. 断面与水质目标 JOIN
4. 站点与摄像头 JOIN
5. 摄像头与平台 JOIN
6. 站点与区域 JOIN
7. 断面与水体 JOIN
8. 跨表 COUNT/SUM/AVG
9. 三表关联

## 11. 继续冻结的场景

以下 11 项继续冻结：

1. L2_SQL_011
2. L2_SQL_012
3. L2_SQL_019
4. `rs_outlet_trace_v2` 责任主体统计
5. 排污口企业/许可证溯源
6. `wm_water_source_intake_v2` 供水能力
7. Q10 requires_manual_review 场景
8. `wm_waterquality_threshold` 正向查询
9. DDL/DML
10. 系统表查询
11. `SELECT *`

## 12. P1 内部优先级

| 顺序 | P1 内部优先级 | 内容 | 候选数 | 理由 |
|---:|---|---|---:|---|
| 1 | P1-A | C 组单表监测、实况、整治和废水记录 | 10 | 业务需求直接，表字段较明确；先处理 PS/PQ 约束 |
| 2 | P1-B | D 组单表断面、水文、区域和设备信息 | 8 | 表覆盖广，需对设备字段和隐私字段做人工复核 |
| 3 | P1-C | E 组普通取水口和水源地 | 6 | 需持续防止与 `wm_water_source_intake_v2` 口径混淆 |

P1-A/P1-B/P1-C 仅表示 P1 内部执行顺序，不是 Level 3 原 A/B/C 业务分组。

## 13. 后续候选草案文件规划

后续阶段计划创建但本阶段不创建：

```text
training/sql_examples_level3_p1_draft.json
training/sql_examples_level3_p1_review_result.json
```

候选 ID 规划为 `L3_P1_SQL_001` 至 `L3_P1_SQL_024`。草案阶段每条记录应包含分组、业务方向、预期单表、明确字段、风险说明和 P0 去重说明，并在生成完整 SQL 后单独执行 SQLGuard 静态审查。

本阶段是否创建候选 JSON：否。

## 14. P1 范围结论

**通过。**

范围验收核对：

- 总候选数量精确为 24：是
- C=10、D=8、E=6：是
- 16 张允许表均完成 metadata 存在性检查：是
- 所有计划字段均来自 metadata index：是
- 与 P0 核心业务重复：0
- JOIN/跨表场景均移入 P2：是
- 未引入冻结样本：是
- 未使用 `wm_water_source_intake_v2`：是
- 未创建训练 JSON：是
- 未训练或写入 ChromaDB：是
- 未修改正式数据目录：是

## 15. 下一阶段建议

下一阶段只创建 `training/sql_examples_level3_p1_draft.json` 候选草案，按本计划生成精确 24 条候选并执行静态字段检查；仍不训练、不写 ChromaDB、不进入 P2 或第 4 级。

# 第 2 级 SQL 示例训练前复核报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- git status --short：
```text
M tools/check_sql_examples_level2.py
 M tools/sql_examples_level2_check_result.md
 M training/sql_examples_level2_draft.json
 M training/sql_examples_level2_review.md
?? training/sql_examples_level2_pretrain_review.md
```
- 样本总数：19
- approved 数量：8
- requires_manual_review 数量：11
- excluded 数量：0
- 静态检查失败数量：0
- 静态检查失败样本列表：无
- SQL Guard warning 样本列表：L2_SQL_019
- requires_manual_review 样本列表：L2_SQL_001, L2_SQL_002, L2_SQL_003, L2_SQL_004, L2_SQL_005, L2_SQL_006, L2_SQL_007, L2_SQL_008, L2_SQL_011, L2_SQL_012, L2_SQL_019
- excluded 样本列表：无

## 场景覆盖

- 水质日趋势：3
- 水质小时趋势：3
- 水质月趋势：2
- 排污口编码：2
- 排污口溯源：2
- 排污口基础信息：2
- 站点信息：2
- 区域信息：1
- 取水口信息：2

## 明细

### L2_SQL_001

- id：L2_SQL_001
- question：某站点最近一段时间水质日变化趋势
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time, wm_waterquality_day_records.m2_value, wm_waterquality_day_records.m3_value, wm_waterquality_day_records.water_quality_level
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定 station_id=1408 和日期 2026-01-01，训练前需人工确认是否存在过拟合风险。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_002

- id：L2_SQL_002
- question：按站点查看水质日记录数量和最近监测时间
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定日期 2026-01-01，训练前需人工确认是否适合作为通用示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_003

- id：L2_SQL_003
- question：某站点某时间段水质日变化趋势，包含 pH 和溶解氧
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time, wm_waterquality_day_records.m2_value, wm_waterquality_day_records.m3_value
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard 已由原 warning 修订为 ok；但 SQL 固定 station_id=1408，训练前需人工确认固定站点值是否适合作为示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_004

- id：L2_SQL_004
- question：某站点水质小时变化趋势
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m1_value, wm_waterquality_hour_records.m2_value, wm_waterquality_hour_records.m3_value, wm_waterquality_hour_records.water_quality_level
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定 station_id=1408 和日期 2026-01-01，训练前需人工确认是否存在过拟合风险。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_005

- id：L2_SQL_005
- question：按小时查看某站点水质等级分布
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.water_quality_level, wm_waterquality_hour_records.monitor_time
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定 station_id=1408 和日期 2026-01-01，训练前需人工确认是否存在过拟合风险。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_006

- id：L2_SQL_006
- question：查询最近小时水质监测中的 pH 和氨氮指标
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m2_value, wm_waterquality_hour_records.m8_value, wm_waterquality_hour_records.m9_value
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定日期 2026-01-01，训练前需人工确认是否适合作为通用示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_007

- id：L2_SQL_007
- question：某站点水质月变化趋势
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.m2_value, wm_waterquality_month_records.m3_value, wm_waterquality_month_records.water_quality_level
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定 station_id=1408 和年份 2025，训练前需人工确认是否存在过拟合风险。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_008

- id：L2_SQL_008
- question：按月份统计水质月记录数量
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但 SQL 固定年份 2025，训练前需人工确认是否适合作为通用示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_009

- id：L2_SQL_009
- question：查询排污口编码
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_name, rs_outlet.outlet_code, rs_outlet.outlet_code_province
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 rs_outlet 与明确 outlet_code 字段，未使用 SELECT *，包含 LIMIT，可作为排污口编码示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_010

- id：L2_SQL_010
- question：查看排污口国家编码和地方编码
- used_tables：rs_outlet_info_v2
- used_columns：rs_outlet_info_v2.outlet_name, rs_outlet_info_v2.outlet_code_national, rs_outlet_info_v2.outlet_code_local
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 rs_outlet_info_v2 与 outlet_code_national/outlet_code_local，未使用 SELECT *，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_011

- id：L2_SQL_011
- question：排污口溯源责任主体统计
- used_tables：rs_outlet_trace_v2
- used_columns：rs_outlet_trace_v2.primary_entity_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但排污口溯源责任主体统计属于中风险业务语义，训练前需人工确认字段口径。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_012

- id：L2_SQL_012
- question：查询排污口溯源企业和排放许可证
- used_tables：rs_outlet_trace_v2
- used_columns：rs_outlet_trace_v2.outlet_name, rs_outlet_trace_v2.primary_entity_name, rs_outlet_trace_v2.discharge_permit_no, rs_outlet_trace_v2.credit_code, rs_outlet_trace_v2.wastewater_type
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=ok；但溯源企业、排放许可证和信用代码字段口径需人工确认，暂不直接批准训练。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_013

- id：L2_SQL_013
- question：查询排污口基础信息
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_name, rs_outlet.area_name, rs_outlet.county_name, rs_outlet.river_basin, rs_outlet.outlet_address
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 rs_outlet 基础信息字段，未使用 SELECT *，包含 LIMIT，可作为基础信息示例。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_014

- id：L2_SQL_014
- question：按区县统计排污口数量
- used_tables：rs_outlet
- used_columns：rs_outlet.county_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；按 county_name 聚合排污口数量，表字段均在元数据内，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_015

- id：L2_SQL_015
- question：查询站点名称和所属区域
- used_tables：wm_station_info_v2
- used_columns：wm_station_info_v2.station_code, wm_station_info_v2.station_name, wm_station_info_v2.region_code, wm_station_info_v2.region_name, wm_station_info_v2.station_type
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 wm_station_info_v2 的站点名称和区域字段，未使用 SELECT *，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_016

- id：L2_SQL_016
- question：按区域统计监测站点数量
- used_tables：wm_station_info_v2
- used_columns：wm_station_info_v2.region_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；按 region_name 聚合站点数量，表字段均在元数据内，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_017

- id：L2_SQL_017
- question：查询区域编码和区域名称
- used_tables：gis_region
- used_columns：gis_region.region_code, gis_region.region_name, gis_region.region_level, gis_region.parent_code
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 gis_region 的区域编码、名称和层级字段，未使用 SELECT *，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_018

- id：L2_SQL_018
- question：查询取水口名称和水源类型
- used_tables：wm_water_intake
- used_columns：wm_water_intake.name, wm_water_intake.region_name, wm_water_intake.city, wm_water_intake.county, wm_water_intake.water_type, wm_water_intake.code
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- train_decision：approved
- review_notes：SQL Guard severity=ok；使用 wm_water_intake 基础字段，未使用 SELECT *，包含 LIMIT。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

### L2_SQL_019

- id：L2_SQL_019
- question：查询水源地取水口供水能力
- used_tables：wm_water_source_intake_v2
- used_columns：wm_water_source_intake_v2.name, wm_water_source_intake_v2.city, wm_water_source_intake_v2.district, wm_water_source_intake_v2.source_type, wm_water_source_intake_v2.daily_supply_capacity, wm_water_source_intake_v2.annual_actual_withdrawal
- SQL Guard 结果：passed=True；severity=warning；reason=SQL 表不在 deterministic candidate tables 中，需人工关注
- train_decision：requires_manual_review
- review_notes：SQL Guard severity=warning，wm_water_source_intake_v2 不在 deterministic candidate tables 中；水源地取水口供水能力场景需人工确认后再决定是否训练。
- 是否通过训练前审查：是
- reason：通过训练前审查标记

## 安全声明

- 是否执行真实 SQL：否
- 是否连接数据库：否
- 是否训练 Vanna：否
- 是否写入 ChromaDB：否
- 是否修改数据库结构：否
- 是否进入第 2/3/4 级：否
- 当前结论：训练前复核准备通过；approved 样本可作为后续训练候选，requires_manual_review 样本不得直接训练。
- 下一步建议：人工确认 requires_manual_review 样本的固定值、业务语义和 P0 候选一致性后，再另起阶段执行受控训练写入。

# 第 2 级 SQL 示例训练草案静态审查报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- git status --short：
```text
?? tools/check_sql_examples_level2.py
?? tools/sql_examples_level2_check_result.md
?? training/
```
- 样本总数：19
- 通过数量：19
- 失败数量：0
- 失败样本列表：无

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
- 是否通过：是
- reason：通过

### L2_SQL_002

- id：L2_SQL_002
- question：按站点查看水质日记录数量和最近监测时间
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_003

- id：L2_SQL_003
- question：查询某站点日均 pH 和溶解氧趋势
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time, wm_waterquality_day_records.m2_value, wm_waterquality_day_records.m3_value
- SQL Guard 结果：passed=True；severity=warning；reason=SQL 表不在 deterministic candidate tables 中，需人工关注
- 是否通过：是
- reason：通过

### L2_SQL_004

- id：L2_SQL_004
- question：某站点水质小时变化趋势
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m1_value, wm_waterquality_hour_records.m2_value, wm_waterquality_hour_records.m3_value, wm_waterquality_hour_records.water_quality_level
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_005

- id：L2_SQL_005
- question：按小时查看某站点水质等级分布
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.water_quality_level, wm_waterquality_hour_records.monitor_time
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_006

- id：L2_SQL_006
- question：查询最近小时水质监测中的 pH 和氨氮指标
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m2_value, wm_waterquality_hour_records.m8_value, wm_waterquality_hour_records.m9_value
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_007

- id：L2_SQL_007
- question：某站点水质月变化趋势
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.m2_value, wm_waterquality_month_records.m3_value, wm_waterquality_month_records.water_quality_level
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_008

- id：L2_SQL_008
- question：按月份统计水质月记录数量
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_009

- id：L2_SQL_009
- question：查询排污口编码
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_name, rs_outlet.outlet_code, rs_outlet.outlet_code_province
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_010

- id：L2_SQL_010
- question：查看排污口国家编码和地方编码
- used_tables：rs_outlet_info_v2
- used_columns：rs_outlet_info_v2.outlet_name, rs_outlet_info_v2.outlet_code_national, rs_outlet_info_v2.outlet_code_local
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_011

- id：L2_SQL_011
- question：排污口溯源责任主体统计
- used_tables：rs_outlet_trace_v2
- used_columns：rs_outlet_trace_v2.primary_entity_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_012

- id：L2_SQL_012
- question：查询排污口溯源企业和排放许可证
- used_tables：rs_outlet_trace_v2
- used_columns：rs_outlet_trace_v2.outlet_name, rs_outlet_trace_v2.primary_entity_name, rs_outlet_trace_v2.discharge_permit_no, rs_outlet_trace_v2.credit_code, rs_outlet_trace_v2.wastewater_type
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_013

- id：L2_SQL_013
- question：查询排污口基础信息
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_name, rs_outlet.area_name, rs_outlet.county_name, rs_outlet.river_basin, rs_outlet.outlet_address
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_014

- id：L2_SQL_014
- question：按区县统计排污口数量
- used_tables：rs_outlet
- used_columns：rs_outlet.county_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_015

- id：L2_SQL_015
- question：查询站点名称和所属区域
- used_tables：wm_station_info_v2
- used_columns：wm_station_info_v2.station_code, wm_station_info_v2.station_name, wm_station_info_v2.region_code, wm_station_info_v2.region_name, wm_station_info_v2.station_type
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_016

- id：L2_SQL_016
- question：按区域统计监测站点数量
- used_tables：wm_station_info_v2
- used_columns：wm_station_info_v2.region_name
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_017

- id：L2_SQL_017
- question：查询区域编码和区域名称
- used_tables：gis_region
- used_columns：gis_region.region_code, gis_region.region_name, gis_region.region_level, gis_region.parent_code
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_018

- id：L2_SQL_018
- question：查询取水口名称和水源类型
- used_tables：wm_water_intake
- used_columns：wm_water_intake.name, wm_water_intake.region_name, wm_water_intake.city, wm_water_intake.county, wm_water_intake.water_type, wm_water_intake.code
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- 是否通过：是
- reason：通过

### L2_SQL_019

- id：L2_SQL_019
- question：查询水源地取水口供水能力
- used_tables：wm_water_source_intake_v2
- used_columns：wm_water_source_intake_v2.name, wm_water_source_intake_v2.city, wm_water_source_intake_v2.district, wm_water_source_intake_v2.source_type, wm_water_source_intake_v2.daily_supply_capacity, wm_water_source_intake_v2.annual_actual_withdrawal
- SQL Guard 结果：passed=True；severity=warning；reason=SQL 表不在 deterministic candidate tables 中，需人工关注
- 是否通过：是
- reason：通过

## 安全声明

- 是否执行真实 SQL：否
- 是否连接数据库：否
- 是否训练 Vanna：否
- 是否写入 ChromaDB：否
- 是否修改数据库结构：否
- 是否进入第 2/3/4 级：否
- 当前结论：所有草案样本通过静态审查，可进入人工复核。
- 下一步建议：人工确认业务语义后，另起阶段执行受控训练写入。

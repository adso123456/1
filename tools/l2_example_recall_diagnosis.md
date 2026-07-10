# L2 SQL 示例召回可观测性诊断

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：e1fa7e7f59b5aeae5d6c82e9dd4dc2e06e1ae161
- 初始 git status --short：
```text
clean
```
- 临时目录：C:\Users\ADSO1\AppData\Local\Temp\vanna_l2_recall_probe_mhct2n0v
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入正式 ChromaDB：否
- 是否修改正式 vanna_data：否
- 是否进入第 3/4 级：否
- 诊断问题总数：6
- 召回成功列表：Q1，Q2，Q3，Q6，Q7，Q8
- 召回弱列表：无
- 召回失败列表：无
- metadata context 通过列表：Q6，Q8
- metadata context 弱列表：Q1，Q2，Q3，Q7
- metadata context 失败列表：无
- 水质指标字段映射是否缺失：是
- generic name/code/type 干扰列表：Q6，Q7，Q8
- 当前结论：L2 示例可召回，但 deterministic metadata context 仍有字段或候选弱项。
- 下一阶段建议：下一阶段先确认 DefaultLlmContextEnhancer 是否会把 tool usage SQL 示例注入 LLM；若不会，应设计只读可观测 hook 或显式上下文注入方案，然后再做 P0/metadata context 最小修复。

## 关键观察

- L2 训练写入使用 `save_tool_usage(..., tool_name='run_sql')`，召回应通过 `search_similar_usage(..., tool_name_filter='run_sql')` 观察。
- Vanna 默认 `DefaultLlmContextEnhancer` 只调用 `search_text_memories`，不直接调用 `search_similar_usage`。因此 tool usage SQL 示例即使可召回，也未必进入 system prompt。
- 本脚本没有启动服务、没有调用 LLM、没有连接数据库、没有执行 SQL。

## 逐项诊断

### Q1

- question：查询某站点水质日趋势中的 pH 和溶解氧变化
- expected_target_sample_id：L2_SQL_003
- expected_target_table：wm_waterquality_day_records
- expected_key_columns：m2_value, m3_value, monitor_time, station_id
- approved_sample_exists：是
- approved_sample_question：查询某站点水质日趋势中的 pH 和溶解氧变化
- approved_sample_sql：SELECT station_id, monitor_time, m2_value, m3_value
FROM wm_waterquality_day_records
WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL
ORDER BY monitor_time
LIMIT 100
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- deterministic_matched_columns：rs_outlet_monitor_v2.ph
- metadata_context_status：weak
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：否
- metadata_context_key_column_hits：无
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：无
- diagnosis：目标 SQL 示例可召回，但 deterministic metadata context 的关键字段提示不足。
- recommended_next_action：下一阶段补强 metadata context 字段映射，不新增训练。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.0 | L2_SQL_003 | 查询某站点水质日趋势中的 pH 和溶解氧变化 | SELECT station_id, monitor_time, m2_value, m3_value FROM wm_waterquality_day_records WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL ORDER BY monitor_time LIMIT 100 |
| 2 | 0.798941 | L2_SQL_001 | 某站点最近一段时间水质日变化趋势 | SELECT station_id, monitor_time, m2_value, m3_value, water_quality_level FROM wm_waterquality_day_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 3 | 0.773521 | L2_SQL_007 | 某站点水质月变化趋势 | SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level FROM wm_waterquality_month_records WHERE station_id = 1408 AND monitor_year >= 2025 ORDER BY monitor_year, monitor_month LIMIT 60 |
| 4 | 0.769093 | L2_SQL_004 | 某站点水质小时变化趋势 | SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 5 | 0.714917 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 6 | 0.704362 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |
| 7 | 0.688651 | L2_SQL_006 | 查询最近小时水质监测中的 pH 和氨氮指标 | SELECT station_id, monitor_time, m2_value, m8_value, m9_value FROM wm_waterquality_hour_records WHERE monitor_time >= '2026-01-01' ORDER BY monitor_time DESC LIMIT 100 |
| 8 | 0.558923 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 9 | 0.544819 | L2_SQL_008 | 按月份统计水质月记录数量 | SELECT monitor_year, monitor_month, COUNT(*) AS record_count FROM wm_waterquality_month_records WHERE monitor_year >= 2025 GROUP BY monitor_year, monitor_month ORDER BY monitor_year, monitor_month LIMIT 36 |
| 10 | 0.506166 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |

### Q2

- question：某站点水质小时变化趋势
- expected_target_sample_id：L2_SQL_004
- expected_target_table：wm_waterquality_hour_records
- expected_key_columns：m1_value, m2_value, m3_value, monitor_time, station_id
- approved_sample_exists：是
- approved_sample_question：某站点水质小时变化趋势
- approved_sample_sql：SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level
FROM wm_waterquality_hour_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01'
ORDER BY monitor_time
LIMIT 200
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- deterministic_matched_columns：无
- metadata_context_status：weak
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：否
- metadata_context_key_column_hits：无
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：无
- diagnosis：目标 SQL 示例可召回，但 deterministic metadata context 的关键字段提示不足。
- recommended_next_action：下一阶段补强 metadata context 字段映射，不新增训练。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.0 | L2_SQL_004 | 某站点水质小时变化趋势 | SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 2 | 0.909796 | L2_SQL_007 | 某站点水质月变化趋势 | SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level FROM wm_waterquality_month_records WHERE station_id = 1408 AND monitor_year >= 2025 ORDER BY monitor_year, monitor_month LIMIT 60 |
| 3 | 0.905407 | L2_SQL_001 | 某站点最近一段时间水质日变化趋势 | SELECT station_id, monitor_time, m2_value, m3_value, water_quality_level FROM wm_waterquality_day_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 4 | 0.815982 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 5 | 0.769092 | L2_SQL_003 | 查询某站点水质日趋势中的 pH 和溶解氧变化 | SELECT station_id, monitor_time, m2_value, m3_value FROM wm_waterquality_day_records WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL ORDER BY monitor_time LIMIT 100 |
| 6 | 0.735738 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |
| 7 | 0.632023 | L2_SQL_006 | 查询最近小时水质监测中的 pH 和氨氮指标 | SELECT station_id, monitor_time, m2_value, m8_value, m9_value FROM wm_waterquality_hour_records WHERE monitor_time >= '2026-01-01' ORDER BY monitor_time DESC LIMIT 100 |
| 8 | 0.582256 | L2_SQL_008 | 按月份统计水质月记录数量 | SELECT monitor_year, monitor_month, COUNT(*) AS record_count FROM wm_waterquality_month_records WHERE monitor_year >= 2025 GROUP BY monitor_year, monitor_month ORDER BY monitor_year, monitor_month LIMIT 36 |
| 9 | 0.545317 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 10 | 0.531085 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |

### Q3

- question：某站点水质月变化趋势
- expected_target_sample_id：L2_SQL_007
- expected_target_table：wm_waterquality_month_records
- expected_key_columns：m2_value, m3_value, monitor_year, monitor_month, station_id
- approved_sample_exists：是
- approved_sample_question：某站点水质月变化趋势
- approved_sample_sql：SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level
FROM wm_waterquality_month_records
WHERE station_id = 1408 AND monitor_year >= 2025
ORDER BY monitor_year, monitor_month
LIMIT 60
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：wm_waterquality_month_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records
- deterministic_matched_columns：无
- metadata_context_status：weak
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：否
- metadata_context_key_column_hits：无
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：无
- diagnosis：目标 SQL 示例可召回，但 deterministic metadata context 的关键字段提示不足。
- recommended_next_action：下一阶段补强 metadata context 字段映射，不新增训练。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.0 | L2_SQL_007 | 某站点水质月变化趋势 | SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level FROM wm_waterquality_month_records WHERE station_id = 1408 AND monitor_year >= 2025 ORDER BY monitor_year, monitor_month LIMIT 60 |
| 2 | 0.909796 | L2_SQL_004 | 某站点水质小时变化趋势 | SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 3 | 0.902699 | L2_SQL_001 | 某站点最近一段时间水质日变化趋势 | SELECT station_id, monitor_time, m2_value, m3_value, water_quality_level FROM wm_waterquality_day_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |
| 4 | 0.773521 | L2_SQL_003 | 查询某站点水质日趋势中的 pH 和溶解氧变化 | SELECT station_id, monitor_time, m2_value, m3_value FROM wm_waterquality_day_records WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL ORDER BY monitor_time LIMIT 100 |
| 5 | 0.741257 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 6 | 0.708176 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |
| 7 | 0.662617 | L2_SQL_008 | 按月份统计水质月记录数量 | SELECT monitor_year, monitor_month, COUNT(*) AS record_count FROM wm_waterquality_month_records WHERE monitor_year >= 2025 GROUP BY monitor_year, monitor_month ORDER BY monitor_year, monitor_month LIMIT 36 |
| 8 | 0.548304 | L2_SQL_006 | 查询最近小时水质监测中的 pH 和氨氮指标 | SELECT station_id, monitor_time, m2_value, m8_value, m9_value FROM wm_waterquality_hour_records WHERE monitor_time >= '2026-01-01' ORDER BY monitor_time DESC LIMIT 100 |
| 9 | 0.538897 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 10 | 0.513914 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |

### Q6

- question：查询站点名称和所属区域
- expected_target_sample_id：L2_SQL_015
- expected_target_table：wm_station_info_v2
- expected_key_columns：station_name, station_code, region_code, region_name
- approved_sample_exists：是
- approved_sample_question：查询站点名称和所属区域
- approved_sample_sql：SELECT station_code, station_name, region_code, region_name, station_type
FROM wm_station_info_v2
ORDER BY station_name
LIMIT 50
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：gis_poi, wm_uav_info, layer_section, dc_survey_info, layer_watershed, wm_water_intake, wm_station_info_v2, layer_boundary_park, layer_outlet_sewage, layer_industrial_lsf
- deterministic_matched_columns：gis_poi.name, wm_uav_info.name, layer_section.name, dc_survey_info.name, layer_watershed.name, wm_water_intake.name, wm_station_info_v2.station_name, layer_boundary_park.name, layer_outlet_sewage.name, layer_industrial_lsf.name
- metadata_context_status：pass
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：是
- metadata_context_key_column_hits：station_name
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：gis_poi.name, wm_uav_info.name, layer_section.name, dc_survey_info.name, layer_watershed.name, wm_water_intake.name, layer_boundary_park.name, layer_outlet_sewage.name, layer_industrial_lsf.name
- diagnosis：目标 SQL 示例和 metadata context 均可观察到，但仍需确认是否进入实际 LLM prompt。
- recommended_next_action：下一阶段增加 LLM 前 prompt 截取或 memory tool usage 注入可观测性。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.000001 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |
| 2 | 0.814895 | L2_SQL_017 | 查询区域编码和区域名称 | SELECT region_code, region_name, region_level, parent_code FROM gis_region ORDER BY region_code LIMIT 100 |
| 3 | 0.692254 | L2_SQL_016 | 按区域统计监测站点数量 | SELECT region_name, COUNT(*) AS station_count FROM wm_station_info_v2 WHERE region_name IS NOT NULL GROUP BY region_name ORDER BY COUNT(*) DESC LIMIT 50 |
| 4 | 0.663991 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 5 | 0.647103 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 6 | 0.595613 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |
| 7 | 0.587553 | L2_SQL_010 | 查看排污口国家编码和地方编码 | SELECT outlet_name, outlet_code_national, outlet_code_local FROM rs_outlet_info_v2 ORDER BY outlet_name LIMIT 50 |
| 8 | 0.573804 | L2_SQL_009 | 查询排污口编码 | SELECT outlet_name, outlet_code, outlet_code_province FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 9 | 0.5584 | L2_SQL_013 | 查询排污口基础信息 | SELECT outlet_name, area_name, county_name, river_basin, outlet_address FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 10 | 0.531182 | L2_SQL_001 | 某站点最近一段时间水质日变化趋势 | SELECT station_id, monitor_time, m2_value, m3_value, water_quality_level FROM wm_waterquality_day_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' ORDER BY monitor_time LIMIT 200 |

### Q7

- question：查询区域编码和区域名称
- expected_target_sample_id：L2_SQL_017
- expected_target_table：gis_region
- expected_key_columns：region_code, region_name
- approved_sample_exists：是
- approved_sample_question：查询区域编码和区域名称
- approved_sample_sql：SELECT region_code, region_name, region_level, parent_code
FROM gis_region
ORDER BY region_code
LIMIT 100
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：gis_poi, gis_region, wm_uav_info, layer_section, metadata_view, dc_survey_info, layer_watershed, wm_water_intake, layer_boundary_park, layer_outlet_sewage
- deterministic_matched_columns：gis_poi.name, gis_region.code, wm_uav_info.code, wm_uav_info.name, layer_section.code, layer_section.name, metadata_view.code, dc_survey_info.name, layer_watershed.code, layer_watershed.name, wm_water_intake.name, layer_boundary_park.code, layer_boundary_park.name, layer_outlet_sewage.code, layer_outlet_sewage.name
- metadata_context_status：weak
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：否
- metadata_context_key_column_hits：无
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：gis_poi.name, wm_uav_info.code, wm_uav_info.name, layer_section.code, layer_section.name, metadata_view.code, dc_survey_info.name, layer_watershed.code, layer_watershed.name, wm_water_intake.name, layer_boundary_park.code, layer_boundary_park.name, layer_outlet_sewage.code, layer_outlet_sewage.name
- diagnosis：目标 SQL 示例可召回，但 deterministic metadata context 的关键字段提示不足。
- recommended_next_action：下一阶段补强 metadata context 字段映射，不新增训练。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.0 | L2_SQL_017 | 查询区域编码和区域名称 | SELECT region_code, region_name, region_level, parent_code FROM gis_region ORDER BY region_code LIMIT 100 |
| 2 | 0.814896 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |
| 3 | 0.661681 | L2_SQL_010 | 查看排污口国家编码和地方编码 | SELECT outlet_name, outlet_code_national, outlet_code_local FROM rs_outlet_info_v2 ORDER BY outlet_name LIMIT 50 |
| 4 | 0.652328 | L2_SQL_009 | 查询排污口编码 | SELECT outlet_name, outlet_code, outlet_code_province FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 5 | 0.648637 | L2_SQL_016 | 按区域统计监测站点数量 | SELECT region_name, COUNT(*) AS station_count FROM wm_station_info_v2 WHERE region_name IS NOT NULL GROUP BY region_name ORDER BY COUNT(*) DESC LIMIT 50 |
| 6 | 0.640483 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 7 | 0.551974 | L2_SQL_013 | 查询排污口基础信息 | SELECT outlet_name, area_name, county_name, river_basin, outlet_address FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 8 | 0.533373 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 9 | 0.508147 | L2_SQL_014 | 按区县统计排污口数量 | SELECT county_name, COUNT(*) AS outlet_count FROM rs_outlet WHERE county_name IS NOT NULL GROUP BY county_name ORDER BY COUNT(*) DESC LIMIT 50 |
| 10 | 0.50405 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |

### Q8

- question：查询取水口名称和水源类型
- expected_target_sample_id：L2_SQL_018
- expected_target_table：wm_water_intake
- expected_key_columns：name, water_type
- approved_sample_exists：是
- approved_sample_question：查询取水口名称和水源类型
- approved_sample_sql：SELECT name, region_name, city, county, water_type, code
FROM wm_water_intake
ORDER BY name
LIMIT 50
- recall_status：pass
- recall_unavailable：无
- target_sample_in_top_k：是
- target_sample_rank：1
- target_sql_text_found：是
- target_table_found：是
- deterministic_candidate_tables：gis_poi, wm_uav_info, layer_section, metadata_view, dc_survey_info, gis_headwaters, layer_watershed, wm_water_intake, wm_water_source, gis_naturereserve
- deterministic_matched_columns：gis_poi.name, wm_uav_info.name, layer_section.name, metadata_view.type, dc_survey_info.name, gis_headwaters.type, layer_watershed.name, wm_water_intake.name, wm_water_intake.water_type, wm_water_source.source_type, gis_naturereserve.type
- metadata_context_status：pass
- metadata_context_contains_target_table：是
- metadata_context_contains_key_columns：是
- metadata_context_key_column_hits：name，water_type
- llm_prompt_contains_target_sample_id：否
- llm_prompt_contains_target_sql：否
- llm_prompt_contains_target_table：是
- generic_interference_columns：gis_poi.name, wm_uav_info.name, layer_section.name, metadata_view.type, dc_survey_info.name, gis_headwaters.type, layer_watershed.name, wm_water_source.source_type, gis_naturereserve.type
- diagnosis：目标 SQL 示例和 metadata context 均可观察到，但仍需确认是否进入实际 LLM prompt。
- recommended_next_action：下一阶段增加 LLM 前 prompt 截取或 memory tool usage 注入可观测性。

recall_top_k：

| rank | similarity | sample_id | question | sql |
|---:|---:|---|---|---|
| 1 | 1.0 | L2_SQL_018 | 查询取水口名称和水源类型 | SELECT name, region_name, city, county, water_type, code FROM wm_water_intake ORDER BY name LIMIT 50 |
| 2 | 0.671674 | L2_SQL_013 | 查询排污口基础信息 | SELECT outlet_name, area_name, county_name, river_basin, outlet_address FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 3 | 0.665367 | L2_SQL_005 | 按小时查看某站点水质等级分布 | SELECT station_id, water_quality_level, COUNT(*) AS record_count FROM wm_waterquality_hour_records WHERE station_id = 1408 AND monitor_time >= '2026-01-01' GROUP BY station_id, water_quality_level ORDER BY COUNT(*) DESC LIMIT 50 |
| 4 | 0.647103 | L2_SQL_015 | 查询站点名称和所属区域 | SELECT station_code, station_name, region_code, region_name, station_type FROM wm_station_info_v2 ORDER BY station_name LIMIT 50 |
| 5 | 0.644663 | L2_SQL_009 | 查询排污口编码 | SELECT outlet_name, outlet_code, outlet_code_province FROM rs_outlet ORDER BY outlet_name LIMIT 50 |
| 6 | 0.640483 | L2_SQL_017 | 查询区域编码和区域名称 | SELECT region_code, region_name, region_level, parent_code FROM gis_region ORDER BY region_code LIMIT 100 |
| 7 | 0.640335 | L2_SQL_002 | 按站点查看水质日记录数量和最近监测时间 | SELECT station_id, COUNT(*) AS record_count, MAX(monitor_time) AS latest_monitor_time FROM wm_waterquality_day_records WHERE monitor_time >= '2026-01-01' GROUP BY station_id ORDER BY COUNT(*) DESC LIMIT 50 |
| 8 | 0.619443 | L2_SQL_010 | 查看排污口国家编码和地方编码 | SELECT outlet_name, outlet_code_national, outlet_code_local FROM rs_outlet_info_v2 ORDER BY outlet_name LIMIT 50 |
| 9 | 0.570678 | L2_SQL_008 | 按月份统计水质月记录数量 | SELECT monitor_year, monitor_month, COUNT(*) AS record_count FROM wm_waterquality_month_records WHERE monitor_year >= 2025 GROUP BY monitor_year, monitor_month ORDER BY monitor_year, monitor_month LIMIT 36 |
| 10 | 0.558923 | L2_SQL_003 | 查询某站点水质日趋势中的 pH 和溶解氧变化 | SELECT station_id, monitor_time, m2_value, m3_value FROM wm_waterquality_day_records WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL ORDER BY monitor_time LIMIT 100 |

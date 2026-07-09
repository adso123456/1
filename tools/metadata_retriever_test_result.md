# Deterministic Metadata Retriever 测试结果

## 汇总

- 测试用例总数：15
- 通过数量：15
- 失败数量：0
- 失败用例列表：无
- 高风险 9 张表是否全部被修正为确定性 top-1：是（9/9）
- 是否接入主问答流程：否
- 是否训练 Vanna：否
- 是否修改 ChromaDB：否
- 是否修改数据库：否
- 是否进入第 2/3/4 级：否

## 明细

### 1. rs_outlet

- query：rs_outlet
- expected：top1 = rs_outlet
- actual_top1：rs_outlet
- actual_candidates：rs_outlet, rs_outlet_info_v2, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2
- pass/fail：pass
- reason：期望 top1=rs_outlet，实际 top1=rs_outlet

### 2. rs_outlet_trace_v2

- query：rs_outlet_trace_v2
- expected：top1 = rs_outlet_trace_v2
- actual_top1：rs_outlet_trace_v2
- actual_candidates：rs_outlet_trace_v2, rs_outlet, rs_outlet_live_v2, rs_outlet_monitor_v2, rs_outlet_info_v2
- pass/fail：pass
- reason：期望 top1=rs_outlet_trace_v2，实际 top1=rs_outlet_trace_v2

### 3. gis_region

- query：gis_region
- expected：top1 = gis_region
- actual_top1：gis_region
- actual_candidates：gis_region, gis_region_city, gis_region_county, gis_region_township, gis_region_population
- pass/fail：pass
- reason：期望 top1=gis_region，实际 top1=gis_region

### 4. gis_region_city

- query：gis_region_city
- expected：top1 = gis_region_city
- actual_top1：gis_region_city
- actual_candidates：gis_region_city, gis_region_county, gis_region, gis_poi, layer_section, wm_water_intake, gis_region_township, layer_outlet_sewage, layer_industrial_ysc, rs_livestock_info_yc
- pass/fail：pass
- reason：期望 top1=gis_region_city，实际 top1=gis_region_city

### 5. dc_survey_task

- query：dc_survey_task
- expected：top1 = dc_survey_task
- actual_top1：dc_survey_task
- actual_candidates：dc_survey_task, dc_survey_task_instance, layer_section, layer_outlet_sewage, layer_industrial_ysc, layer_industrial_yjsgc, dc_survey_track, dc_survey_app
- pass/fail：pass
- reason：期望 top1=dc_survey_task，实际 top1=dc_survey_task

### 6. dc_survey_task_instance

- query：dc_survey_task_instance
- expected：top1 = dc_survey_task_instance
- actual_top1：dc_survey_task_instance
- actual_candidates：dc_survey_task_instance, dc_survey_task, layer_section, layer_outlet_sewage, layer_industrial_ysc, layer_industrial_yjsgc
- pass/fail：pass
- reason：期望 top1=dc_survey_task_instance，实际 top1=dc_survey_task_instance

### 7. se_watershed

- query：se_watershed
- expected：top1 = se_watershed
- actual_top1：se_watershed
- actual_candidates：se_watershed, se_watershed_river, layer_watershed
- pass/fail：pass
- reason：期望 top1=se_watershed，实际 top1=se_watershed

### 8. wm_water_intake

- query：wm_water_intake
- expected：top1 = wm_water_intake
- actual_top1：wm_water_intake
- actual_candidates：wm_water_intake, wm_water_source_intake_v2
- pass/fail：pass
- reason：期望 top1=wm_water_intake，实际 top1=wm_water_intake

### 9. wm_water_source_intake_v2

- query：wm_water_source_intake_v2
- expected：top1 = wm_water_source_intake_v2
- actual_top1：wm_water_source_intake_v2
- actual_candidates：wm_water_source_intake_v2, wm_water_source, wst_trace_edge, wm_water_source_zone_v2, wm_water_intake
- pass/fail：pass
- reason：期望 top1=wm_water_source_intake_v2，实际 top1=wm_water_source_intake_v2

### 10. 水质日记录

- query：水质日记录
- expected：top1 = wm_waterquality_day_records
- actual_top1：wm_waterquality_day_records
- actual_candidates：wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records, wm_waterquality_hour_records, wm_waterquality_threshold, wh_hydrological_day_records
- pass/fail：pass
- reason：期望 top1=wm_waterquality_day_records，实际 top1=wm_waterquality_day_records

### 11. 水质小时记录

- query：水质小时记录
- expected：top1 = wm_waterquality_hour_records
- actual_top1：wm_waterquality_hour_records
- actual_candidates：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records, wm_waterquality_threshold, wh_hydrological_hour_records
- pass/fail：pass
- reason：期望 top1=wm_waterquality_hour_records，实际 top1=wm_waterquality_hour_records

### 12. 某地区某时间段水质变化趋势

- query：某地区某时间段水质变化趋势
- expected：contains any = wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records
- actual_top1：wm_waterquality_threshold
- actual_candidates：wm_waterquality_threshold, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- pass/fail：pass
- reason：候选表包含任一目标表

### 13. 站点名称

- query：站点名称
- expected：contains field = station_name
- actual_top1：wm_station_info_v2
- actual_candidates：wm_station_info_v2, gis_poi, wm_uav_info, layer_section, dc_survey_info, layer_watershed, wm_station_info, wm_water_intake, layer_boundary_park, layer_outlet_sewage
- pass/fail：pass
- reason：字段 station_name 已出现在 matched_columns

### 14. 排污口编码

- query：排污口编码
- expected：contains field = outlet_code
- actual_top1：rs_outlet_info_v2
- actual_candidates：rs_outlet_info_v2, rs_outlet, layer_outlet_sewage, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2, gis_region, wm_uav_info, layer_section
- pass/fail：pass
- reason：字段 outlet_code 已出现在 matched_columns

### 15. 排污口溯源

- query：排污口溯源
- expected：top1 in rs_outlet_trace_v2, wst_trace_edge, wst_trace_node, wst_trace_topology_issue
- actual_top1：rs_outlet_trace_v2
- actual_candidates：rs_outlet_trace_v2, wst_asset, wst_trace_edge, wst_trace_node, wst_layer_river, wst_control_zone, wst_asset_type_dict, layer_entity_process, wst_asset_trace_snap, wst_trace_topology_issue
- pass/fail：pass
- reason：top1 属于允许集合，实际 top1=rs_outlet_trace_v2

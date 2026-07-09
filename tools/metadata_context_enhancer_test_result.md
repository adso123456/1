# DeterministicMetadataContextEnhancer 测试结果

## 汇总

- 测试用例总数：8
- 通过数量：8
- 失败数量：0
- 失败用例列表：无
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否
- 是否修改 API 路由：否
- 是否修改 RunSqlTool：否
- 是否训练 Vanna：否
- 是否修改 ChromaDB：否
- 是否进入第 2/3/4 级：否

## 明细

### 1. 某地区某时间段水质变化趋势

- query：某地区某时间段水质变化趋势
- candidate_tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- system_prompt_length：1588
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 2. 某地区某时间段水质小时变化趋势

- query：某地区某时间段水质小时变化趋势
- candidate_tables：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- system_prompt_length：1571
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 3. 排污口编码

- query：排污口编码
- candidate_tables：rs_outlet_info_v2, rs_outlet, layer_outlet_sewage, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2, gis_region, wm_uav_info, layer_section
- system_prompt_length：3058
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass；expected_field_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 4. 排污口溯源

- query：排污口溯源
- candidate_tables：rs_outlet_trace_v2, wst_asset, wst_trace_edge, wst_trace_node, wst_layer_river, wst_control_zone, wst_asset_type_dict, layer_entity_process, wst_asset_trace_snap, wst_trace_topology_issue
- system_prompt_length：2395
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 5. rs_outlet

- query：rs_outlet
- candidate_tables：rs_outlet, rs_outlet_info_v2, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2
- system_prompt_length：1526
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 6. wm_water_intake

- query：wm_water_intake
- candidate_tables：wm_water_intake, wm_water_source_intake_v2
- system_prompt_length：964
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 7. gis_region

- query：gis_region
- candidate_tables：gis_region, gis_region_city, gis_region_county, gis_region_township, gis_region_population
- system_prompt_length：1386
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_table_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

### 8. 站点名称

- query：站点名称
- candidate_tables：wm_station_info_v2, gis_poi, wm_uav_info, layer_section, dc_survey_info, layer_watershed, wm_station_info, wm_water_intake, layer_boundary_park, layer_outlet_sewage
- system_prompt_length：3016
- checks：has_context_title=pass；has_candidate_table=pass；has_priority_rule=pass；has_vector_priority_rule=pass；has_no_override_rule=pass；has_no_schema_rule=pass；prompt_not_empty=pass；base_enhancer_called=pass；did_not_call_vanna=pass；did_not_execute_sql=pass；did_not_connect_database=pass；expected_field_present=pass
- pass/fail：pass
- reason：全部检查通过
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否

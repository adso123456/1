# P0 元数据检索层接入 dry-run 验证结果

## 汇总

- dry-run 用例总数：8
- 通过数量：8
- 失败数量：0
- 失败用例列表：无
- 是否调用 Vanna：否
- 是否执行 SQL：否
- 是否连接数据库：否
- 是否修改主流程：否
- 是否训练 Vanna：否
- 是否修改 ChromaDB：否
- 是否进入第 2/3/4 级：否

## 明细

### 1. 某地区某时间段水质变化趋势

- query：某地区某时间段水质变化趋势
- deterministic_top1：wm_waterquality_day_records
- deterministic_candidates：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：某地区某时间段水质变化趋势
候选表优先级：
1. wm_waterquality_day_records（水质监测日记录表，score=3400，risk=high）
2. wm_waterquality_hour_records（水质监测小时记录表，score=2700，risk=high）
3. wm_waterquality_year_records（水质监测年记录表，score=2700，risk=high）
4. wm_waterquality_month_records（水质监测月记录表，score=2700，risk=high）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 2. 某地区某时间段水质小时变化趋势

- query：某地区某时间段水质小时变化趋势
- deterministic_top1：wm_waterquality_hour_records
- deterministic_candidates：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：某地区某时间段水质小时变化趋势
候选表优先级：
1. wm_waterquality_hour_records（水质监测小时记录表，score=4150，risk=high）
2. wm_waterquality_day_records（水质监测日记录表，score=2700，risk=high）
3. wm_waterquality_year_records（水质监测年记录表，score=2700，risk=high）
4. wm_waterquality_month_records（水质监测月记录表，score=2700，risk=high）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 3. 排污口编码

- query：排污口编码
- deterministic_top1：rs_outlet_info_v2
- deterministic_candidates：rs_outlet_info_v2, rs_outlet, layer_outlet_sewage, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2, gis_region, wm_uav_info, layer_section
- matched_columns：<br>- rs_outlet_info_v2.outlet_code_local (character varying(100)): 排污口编码(省市系统自有编码)<br>- rs_outlet_info_v2.outlet_code_national (character varying(100)): 排污口编码(国家统一赋码)<br>- rs_outlet.outlet_code (character varying(100)): 排污口编码：国家级<br>- rs_outlet.outlet_code_province (character varying(32)): 排污口编码：省级<br>- layer_outlet_sewage.code (character varying): 编码<br>- gis_region.code (character varying(32)): 编码<br>- wm_uav_info.code (character varying(100)): 编码<br>- layer_section.code (character varying): 编码
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：排污口编码
候选表优先级：
1. rs_outlet_info_v2（排污口管理，score=2910，risk=high）
2. rs_outlet（排污口信息表，score=2100，risk=high）
3. layer_outlet_sewage（工业园区-污水排放口，score=2100，risk=low）
4. rs_outlet_live_v2（排污口实况，score=1490，risk=high）
5. rs_outlet_trace_v2（排污口溯源，score=1490，risk=high）
候选字段：
- rs_outlet_info_v2.outlet_code_local (character varying(100)): 排污口编码(省市系统自有编码)
- rs_outlet_info_v2.outlet_code_national (character varying(100)): 排污口编码(国家统一赋码)
- rs_outlet.outlet_code (character varying(100)): 排污口编码：国家级
- rs_outlet.outlet_code_province (character varying(32)): 排污口编码：省级
- layer_outlet_sewage.code (character varying): 编码
- gis_region.code (character varying(32)): 编码
- wm_uav_info.code (character varying(100)): 编码
- layer_section.code (character varying): 编码
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 4. 排污口溯源

- query：排污口溯源
- deterministic_top1：rs_outlet_trace_v2
- deterministic_candidates：rs_outlet_trace_v2, wst_asset, wst_trace_edge, wst_trace_node, wst_layer_river, wst_control_zone, wst_asset_type_dict, layer_entity_process, wst_asset_trace_snap, wst_trace_topology_issue
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：排污口溯源
候选表优先级：
1. rs_outlet_trace_v2（排污口溯源，score=7000，risk=medium）
2. wst_asset（水安全溯源模块-统一资产表，存储断面、站点、排口、企业、园区、闸坝、管线、摄像头、事故池等可上图、可查询、可关联对象，score=1700，risk=high）
3. wst_trace_edge（水安全溯源模块-溯源拓扑边表，用于存储河网、管网、排水路径、园区内外衔接路径，是 pgRouting 上下游分析的核心边表，score=1700，risk=high）
4. wst_trace_node（水安全溯源模块-溯源拓扑节点表，用于存储河网、管网、园区排污河流、排口挂接点、断面挂接点、园区出口等 pgRouting 节点，score=1700，risk=high）
5. wst_layer_river（溯源图层河流表，score=1700，risk=low）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 5. rs_outlet

- query：rs_outlet
- deterministic_top1：rs_outlet
- deterministic_candidates：rs_outlet, rs_outlet_info_v2, rs_outlet_live_v2, rs_outlet_trace_v2, rs_outlet_monitor_v2, rs_outlet_remediation_v2
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：rs_outlet
候选表优先级：
1. rs_outlet（排污口信息表，score=10000，risk=low）
2. rs_outlet_info_v2（排污口管理，score=820，risk=high）
3. rs_outlet_live_v2（排污口实况，score=820，risk=high）
4. rs_outlet_trace_v2（排污口溯源，score=820，risk=high）
5. rs_outlet_monitor_v2（排污口监测，score=820，risk=high）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 6. wm_water_intake

- query：wm_water_intake
- deterministic_top1：wm_water_intake
- deterministic_candidates：wm_water_intake, wm_water_source_intake_v2
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：wm_water_intake
候选表优先级：
1. wm_water_intake（水源地-取水口，score=10000，risk=low）
2. wm_water_source_intake_v2（饮用水水源地管理及监测-取水口，score=315，risk=high）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 7. gis_region

- query：gis_region
- deterministic_top1：gis_region
- deterministic_candidates：gis_region, gis_region_city, gis_region_county, gis_region_township, gis_region_population
- matched_columns：<br>无
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：gis_region
候选表优先级：
1. gis_region（区县数据表，score=10000，risk=low）
2. gis_region_city（城市行政区划表，score=820，risk=high）
3. gis_region_county（行政区-区县，score=820，risk=high）
4. gis_region_township（行政区划-乡镇，score=820，risk=high）
5. gis_region_population（行政区人口统计表，score=820，risk=high）
候选字段：
- 无确定性字段命中，仅提供候选表约束
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

### 8. 站点名称

- query：站点名称
- deterministic_top1：wm_station_info_v2
- deterministic_candidates：wm_station_info_v2, gis_poi, wm_uav_info, layer_section, dc_survey_info, layer_watershed, wm_station_info, wm_water_intake, layer_boundary_park, layer_outlet_sewage
- matched_columns：<br>- wm_station_info_v2.station_name (character varying(255)): 站点名称<br>- gis_poi.name (character varying(100)): 名称<br>- wm_uav_info.name (character varying(100)): 名称<br>- layer_section.name (character varying): 名称<br>- dc_survey_info.name (character varying(50)): 名称<br>- layer_watershed.name (character varying): 名称<br>- wm_station_info.station_name (character varying(255)): 监测站点名称<br>- wm_water_intake.name (character varying(100)): 名称<br>- layer_boundary_park.name (character varying(28)): 名称<br>- layer_outlet_sewage.name (character varying): 名称
- suggested_context_for_vanna：

```text
## Deterministic Metadata Context
用户问题：站点名称
候选表优先级：
1. wm_station_info_v2（自动站管理，score=2450，risk=high）
2. gis_poi（主键id，score=1420，risk=low）
3. wm_uav_info（大疆无人机基础信息，score=1420，risk=low）
4. layer_section（工业园区-监测断面，score=1420，risk=low）
5. dc_survey_info（巡回调查-调查后的核查信息，score=1420，risk=high）
候选字段：
- wm_station_info_v2.station_name (character varying(255)): 站点名称
- gis_poi.name (character varying(100)): 名称
- wm_uav_info.name (character varying(100)): 名称
- layer_section.name (character varying): 名称
- dc_survey_info.name (character varying(50)): 名称
- layer_watershed.name (character varying): 名称
- wm_station_info.station_name (character varying(255)): 监测站点名称
- wm_water_intake.name (character varying(100)): 名称
约束：生成 SQL 时优先使用上述候选表/字段；若 ChromaDB 返回相似表，必须以确定性候选表优先级为准。
```

- 是否执行 SQL：否
- 是否调用 Vanna：否
- 是否修改主流程：否
- pass/fail：pass
- reason：dry-run 结果符合预期，且未调用 Vanna/SQL/数据库

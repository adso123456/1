# Level 3 P1 Metadata 候选排序测试结果

- 基础 commit：2915199a7c688f8a2a4e1f4330c56e47526a6f0b
- 修改文件：tools/metadata_retriever.py、tools/test_metadata_retriever_level3_p1.py、tools/metadata_retriever_level3_p1_test_result.md
- 测试总数：14
- 通过数量：14
- 失败数量：0
- 失败列表：无
- 原有 P0 检索回归：20/20
- 原有高风险表回归：9/9
- 原有水质趋势回归：4/4

## 四个原 warning 目标表

- P1-Q5：rs_wastewater_day_records 排名 1
- P1-Q7：rs_wastewater_month_records 排名 1
- P1-Q9：wm_hydrological_info 排名 1
- P1-Q16：wm_water_source 排名 1

## 区分与保护

- 废水监测/废水记录区分：通过
- 水源地/普通取水口区分：通过
- 冻结水源地取水口口径：保持冻结
- P0/Level2 回归：通过

## 明细

| ID | 分组 | query | top10 | 目标排名 | 目标 score | matched_by | reason | 结果 |
|---|---|---|---|---:|---:|---|---|---|
| T1 | P1 修复 | 查询PS类型排水口的COD、总氮和pH日记录 | rs_wastewater_day_records, rs_outlet_monitor_v2, rs_wastewater_hour_records, rs_wastewater_month_records, metadata_view, gis_headwaters, gis_naturereserve, gis_ecologicalregion, layer_river_provincial, layer_reservoir_provincial | 1 | 5300 | wastewater_record_intent, wastewater_granularity | 废水或 PS 排水口问题优先污染源记录表；废水记录粒度与目标表一致 | pass |
| T2 | P1 修复 | 查询PS类型废水月度COD、总氮、pH和排放数据 | rs_wastewater_month_records, rs_outlet_monitor_v2, rs_wastewater_day_records, rs_wastewater_hour_records, metadata_view, gis_headwaters, gis_naturereserve, gis_ecologicalregion, layer_river_provincial, layer_reservoir_provincial | 1 | 5300 | wastewater_record_intent, wastewater_granularity | 废水或 PS 排水口问题优先污染源记录表；废水记录粒度与目标表一致 | pass |
| T3 | P1/Level2 回归 | 查询废水小时流量、排放量和状态趋势 | rs_wastewater_hour_records, rs_wastewater_day_records, rs_wastewater_month_records, wst_asset_trace_snap, wst_trace_topology_issue, wm_waterquality_day_records | 1 | 6720 | column_comment_substring, wastewater_record_intent, wastewater_granularity | 字段名或字段注释命中；废水或 PS 排水口问题优先污染源记录表；废水记录粒度与目标表一致 | pass |
| T4 | P1 修复 | 按城市统计水文站记录数 | wm_hydrological_info, wm_water_intake, rs_livestock_info_yc, rs_enterprise_info_lsg | 1 | 5200 | hydrological_station_intent | 水文站问题优先水文站基础信息表 | pass |
| T5 | P1 回归 | 查询水文站基础信息和建设状态 | wm_hydrological_info, wst_asset_trace_snap, wst_trace_topology_issue, wm_waterquality_day_records | 1 | 5200 | hydrological_station_intent | 水文站问题优先水文站基础信息表 | pass |
| T6 | P1 修复 | 查询水源地保护等级和保护区划定状态 | wm_water_source, wm_uav_info, wst_asset_trace_snap, wst_trace_topology_issue, wm_waterquality_day_records | 1 | 5200 | water_source_base_intent | 水源地基础与保护问题优先水源地信息表 | pass |
| T7 | P1 修复 | 按年实际取水量从高到低查看水源地及其服务人口 | wm_water_source | 1 | 5200 | water_source_base_intent | 水源地基础与保护问题优先水源地信息表 | pass |
| T8 | 区分回归 | 查询排污口监测数据中的COD和氨氮记录 | rs_outlet_monitor_v2, rs_outlet, rs_outlet_info_v2, rs_outlet_live_v2, rs_outlet_trace_v2, layer_outlet_sewage, rs_outlet_remediation_v2 | 1 | 3700 | table_comment_substring, column_name_substring, outlet_intent | 中文表注释存在子串匹配；字段名或字段注释命中；排污口语义命中 | pass |
| T9 | P0 回归 | 查询排污口编码 | rs_outlet, rs_outlet_info_v2, layer_outlet_sewage, gis_region, wm_uav_info, layer_section, metadata_view, layer_watershed, layer_boundary_park, layer_industrial_lsf | 1 | 6880 | outlet_code_intent, outlet_intent | 排污口编码问题优先排污口主表及明确 outlet_code 字段；排污口语义命中 | pass |
| T10 | P0 回归 | 查询月度水质为 I 至 III 类的站点列表 | wm_waterquality_month_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records | 1 | 3250 | waterquality_intent, waterquality_granularity | 水质问题优先匹配水质监测记录表；水质月粒度命中 | pass |
| T11 | 区分回归 | 按水源类型统计普通取水口记录数 | wm_water_intake, metadata_view, gis_headwaters, wm_water_source, gis_naturereserve, gis_ecologicalregion, layer_river_provincial, layer_reservoir_provincial, layer_reservoir_provincial_合并, layer_reservoir_provincial_label | 1 | 6620 | column_comment_substring, ordinary_water_intake_intent | 字段名或字段注释命中；普通取水口问题优先普通取水口表 | pass |
| T12 | 区分回归 | 查询普通取水口行政区域和使用状态 | wm_water_intake, wst_asset_trace_snap, wst_trace_topology_issue, wm_waterquality_day_records | 1 | 6620 | column_comment_substring, ordinary_water_intake_intent | 字段名或字段注释命中；普通取水口问题优先普通取水口表 | pass |
| T13 | 冻结保护 | 查询水源地取水口供水能力 | wm_water_intake | - | - | - | 冻结口径未获得 water_source_base_intent | pass |
| T14 | 安全回归 | 查询 wm_waterquality_threshold 中的水质趋势 | wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, layer_section, layer_industrial_ysc, layer_industrial_yjsgc, layer_outlet_sewage | - | - | - | top1=wm_waterquality_day_records | pass |

## 执行约束

- 是否连接数据库：否
- 是否启动主服务：否
- 是否训练：否
- 是否调用 vn.train()：否
- 是否调用 memory.save_tool_usage()：否
- 是否写入 ChromaDB：否

# Level 3 P2 Metadata 候选测试结果

- P2 测试总数：9
- P2 通过数量：9
- P2 失败数量：0
- P2 失败列表：无
- P1 回归：23/23

| sample_id | query | expected_tables | top10 | 结果 | 原因 |
|---|---|---|---|---|---|
| L3_P2_SQL_001 | 查询排污口国家编码、名称及对应整治状态和整治类型记录明细 | rs_outlet_info_v2, rs_outlet_remediation_v2 | rs_outlet_info_v2, rs_outlet_remediation_v2, rs_outlet, layer_outlet_sewage, gis_poi, gis_region, wm_uav_info, layer_section, metadata_view, dc_survey_info | pass | 全部目标表位于 top10 |
| L3_P2_SQL_002 | 按省市区县统计排污口总数和有整治记录的排污口数量，包含没有整治记录的排污口 | rs_outlet_info_v2, rs_outlet_remediation_v2 | rs_outlet_info_v2, rs_outlet_remediation_v2, wm_water_intake, rs_livestock_info_yc, rs_enterprise_info_lsg, wm_water_source_zone_v2, wm_water_source_intake_v2, rs_outlet, rs_outlet_live_v2, rs_outlet_trace_v2 | pass | 全部目标表位于 top10 |
| L3_P2_SQL_003 | 查询排污口国家编码、名称及对应实况记录明细中的排水特征、在线监测和采样条件状态 | rs_outlet_info_v2, rs_outlet_live_v2 | rs_outlet_info_v2, rs_outlet_live_v2, rs_outlet, layer_outlet_sewage, gis_poi, gis_region, wm_uav_info, layer_section, metadata_view, dc_survey_info | pass | 全部目标表位于 top10 |
| L3_P2_SQL_004 | 按省市区县统计排污口总数和有实况记录的排污口数量，包含没有实况记录的排污口 | rs_outlet_info_v2, rs_outlet_live_v2 | rs_outlet_info_v2, rs_outlet_live_v2, wm_water_intake, rs_livestock_info_yc, rs_enterprise_info_lsg, wm_water_source_zone_v2, wm_water_source_intake_v2, rs_outlet, rs_outlet_trace_v2, layer_outlet_sewage | pass | 全部目标表位于 top10 |
| L3_P2_SQL_005 | 查询各断面每年度的全年水质目标等级记录 | wm_section_info, wm_section_wq_info | wm_section_info, wm_section_wq_info, wm_waterquality_year_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records | pass | 全部目标表位于 top10 |
| L3_P2_SQL_006 | 按年度和目标水质等级统计考核断面数量 | wm_section_info, wm_section_wq_info | wm_section_wq_info, wm_waterquality_year_records, wm_section_info, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records | pass | 全部目标表位于 top10 |
| L3_P2_SQL_007 | 查询断面编码、名称及所属水体编码、名称和类型 | wm_section_info, wm_waterbody_info | wm_section_info, wm_waterbody_info, gis_poi, gis_region, wm_uav_info, layer_section, metadata_view, dc_survey_info, gis_headwaters, layer_watershed | pass | 全部目标表位于 top10 |
| L3_P2_SQL_008 | 统计各水体对应的断面数量，包含没有断面的水体 | wm_waterbody_info, wm_section_info | wm_section_info, wm_waterbody_info | pass | 全部目标表位于 top10 |
| L3_P2_SQL_011 | 查询排污口国家编码、名称、整治状态、规范化状态及实况在线监测状态联合记录明细 | rs_outlet_info_v2, rs_outlet_remediation_v2, rs_outlet_live_v2 | rs_outlet_info_v2, rs_outlet, rs_outlet_live_v2, rs_outlet_remediation_v2, layer_outlet_sewage, gis_poi, gis_region, wm_uav_info, layer_section, metadata_view | pass | 全部目标表位于 top10 |

- 排污口单表监测回归：由 P1 T8 保证
- 月度/年度水质回归：由 P1 T10、N1-N4、N7、N9 保证
- 普通取水口/水源地回归：由 P1 T11-T13 保证
- 是否连接数据库：否
- 是否启动主服务：否
- 是否训练：否
- 是否写入 ChromaDB：否

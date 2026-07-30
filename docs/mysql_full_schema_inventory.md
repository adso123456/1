# MySQL 全库业务语义清单

> 本文由 `tools/mysql_full_schema_audit.py` 基于只读 `information_schema`、仓库代码引用和既有 B3 资产确定性生成。真实业务样本未写入仓库。

## 数量校验

- 发现对象：307（基础表 306，视图 1）
- 发现字段：6113
- 旧范围：18 表
- 新调查旧未选：289 表
- 推荐纳入：147 表 / 3085 字段
- 排除：155 表
- 待确认：5 表
- 排除敏感字段：45 个

## 分类统计

| 分类 | 表数 |
|---|---:|
| A | 123 |
| B | 66 |
| C | 2 |
| D | 12 |
| E | 12 |
| F | 5 |
| G | 34 |
| H | 44 |
| I | 4 |
| J | 5 |

## 逐表结论

| 表 | 类型 | 字段 | 估算行数 | 分类 | 领域 | 结论 | 置信度 | 原因 |
|---|---|---:|---:|---|---|---|---|---|
| `ad_dict` | BASE TABLE | 14 | 197 | C | 指标与字典 | 纳入 | high | B3 已批准范围，继续保留 |
| `camera_alarm_info` | BASE TABLE | 28 | 61574 | A | 预警告警 | 纳入 | high | 业务事件或轨迹事实记录 |
| `camera_alarm_picture` | BASE TABLE | 24 | 9071 | D | 预警告警 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `camera_alarm_video` | BASE TABLE | 4 | 1140 | D | 预警告警 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `camera_patrol_hour` | BASE TABLE | 14 | 446 | A | 巡查调查 | 纳入 | high | 业务事实或统计记录含义明确 |
| `camera_patrol_info` | BASE TABLE | 24 | 1550929 | A | 巡查调查 | 纳入 | high | 业务事件或轨迹事实记录 |
| `cf_auto_build_flag` | BASE TABLE | 7 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `day_quality_records` | BASE TABLE | 25 | 3938 | A | 水质监测 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `day_quality_setting` | BASE TABLE | 13 | 394144 | E | 指标与字典 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `dc_survey_app` | BASE TABLE | 11 | 11 | G | 巡查调查 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `dc_survey_info` | BASE TABLE | 23 | 1176 | A | 巡查调查 | 纳入 | high | 业务事件或轨迹事实记录 |
| `dc_survey_offline_sync` | BASE TABLE | 14 | 0 | G | 巡查调查 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `dc_survey_offline_upload` | BASE TABLE | 16 | 2 | G | 巡查调查 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `dc_survey_task` | BASE TABLE | 18 | 88 | A | 巡查调查 | 纳入 | high | 业务事实或统计记录含义明确 |
| `dc_survey_task_instance` | BASE TABLE | 6 | 223 | A | 巡查调查 | 纳入 | high | 业务事件或轨迹事实记录 |
| `dc_survey_track` | BASE TABLE | 9 | 7503 | A | 巡查调查 | 纳入 | high | 业务事实或统计记录含义明确 |
| `doc_plan` | BASE TABLE | 19 | 15 | B | 项目与防治任务 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `doc_plan_attachment` | BASE TABLE | 8 | 42 | D | 项目与防治任务 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `geometry_columns` | BASE TABLE | 7 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `gis_control_unit` | BASE TABLE | 12 | 9 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `gis_ecologicalregion` | BASE TABLE | 19 | 0 | B | 水体与空间地理 | 纳入 | high | 实体主数据含义明确 |
| `gis_headwaters` | BASE TABLE | 16 | 0 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `gis_naturereserve` | BASE TABLE | 25 | 0 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `gis_outlet_origin` | BASE TABLE | 6 | 285 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `gis_region` | BASE TABLE | 14 | 1600 | B | 水体与空间地理 | 纳入 | high | B3 已批准范围，继续保留 |
| `gis_region_population` | BASE TABLE | 14 | 0 | B | 水体与空间地理 | 纳入 | medium | 实体主数据含义明确 |
| `gis_water_tributary` | BASE TABLE | 10 | 20 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `gis_watershed_partition` | BASE TABLE | 13 | 43 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `graphic_datasource` | BASE TABLE | 14 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `graphic_layer_relation` | BASE TABLE | 16 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `graphic_node_history` | BASE TABLE | 11 | 43 | H | 其他业务 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `graphic_operate_log` | BASE TABLE | 9 | 31 | F | 其他业务 | 排除 | high | 系统登录或操作日志，默认不开放业务问数 |
| `graphic_relation_history` | BASE TABLE | 10 | 13 | H | 其他业务 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `high_quality_develope_info` | BASE TABLE | 12 | 57 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `metadata_view` | VIEW | 9 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `min_value_setting` | BASE TABLE | 8 | 5 | E | 水质监测 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `model_branch_region` | BASE TABLE | 10 | 62 | D | 模型计算 | 纳入 | medium | 业务关系明确，可支持实体关联 |
| `model_day_flux` | BASE TABLE | 32 | 9255 | A | 模型计算 | 纳入 | high | 业务事实或统计记录含义明确 |
| `model_efdc_param` | BASE TABLE | 42 | 3 | E | 模型计算 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `model_fvfd_records` | BASE TABLE | 14 | 971200 | A | 模型计算 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `model_hydro_gaoqiaohe_2026_1` | BASE TABLE | 24 | 1529869 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_2` | BASE TABLE | 24 | 1074942 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_3` | BASE TABLE | 24 | 2409828 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_4` | BASE TABLE | 24 | 2902290 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_5` | BASE TABLE | 24 | 3167894 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_6` | BASE TABLE | 24 | 2958703 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_gaoqiaohe_2026_7` | BASE TABLE | 24 | 2388200 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_1` | BASE TABLE | 24 | 6005453 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_2` | BASE TABLE | 24 | 3687429 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_3` | BASE TABLE | 24 | 8715997 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_4` | BASE TABLE | 24 | 11056712 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_5` | BASE TABLE | 24 | 12358836 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_6` | BASE TABLE | 24 | 11161691 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_lake3d_2026_7` | BASE TABLE | 24 | 9297917 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_point` | BASE TABLE | 10 | 231 | B | 模型计算 | 纳入 | high | 实体主数据含义明确 |
| `model_hydro_pre_records` | BASE TABLE | 25 | 3589490 | A | 模型计算 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `model_hydro_zhangqiaohugangcu_2026_1` | BASE TABLE | 24 | 690818 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_hydro_zhangqiaohugangcu_2026_2` | BASE TABLE | 24 | 352562 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_interpolation_point` | BASE TABLE | 10 | 42 | B | 模型计算 | 纳入 | high | 实体主数据含义明确 |
| `model_key_point` | BASE TABLE | 15 | 19 | B | 模型计算 | 纳入 | high | 实体主数据含义明确 |
| `model_lasso_info` | BASE TABLE | 9 | 22 | B | 模型计算 | 纳入 | high | 实体主数据含义明确 |
| `model_lasso_records` | BASE TABLE | 18 | 103090 | A | 模型计算 | 纳入 | high | 业务事实或统计记录含义明确 |
| `model_result_records` | BASE TABLE | 11 | 534 | A | 模型计算 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `model_river_param` | BASE TABLE | 14 | 0 | E | 模型计算 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `model_standard_flux` | BASE TABLE | 13 | 117 | E | 模型计算 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `model_swatrch_records` | BASE TABLE | 28 | 84286 | A | 模型计算 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `model_wq_gaoqiaohe_2026_1` | BASE TABLE | 44 | 1660597 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_2` | BASE TABLE | 44 | 1037661 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_3` | BASE TABLE | 44 | 2455489 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_4` | BASE TABLE | 44 | 2611818 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_5` | BASE TABLE | 44 | 3233683 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_6` | BASE TABLE | 44 | 2853447 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_gaoqiaohe_2026_7` | BASE TABLE | 44 | 2254658 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_1` | BASE TABLE | 44 | 6650176 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_2` | BASE TABLE | 44 | 3893213 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_3` | BASE TABLE | 44 | 8542582 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_4` | BASE TABLE | 44 | 9939519 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_5` | BASE TABLE | 44 | 12405583 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_6` | BASE TABLE | 44 | 10696791 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_lake3d_2026_7` | BASE TABLE | 44 | 9345946 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_pre_records` | BASE TABLE | 45 | 3482516 | A | 模型计算 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `model_wq_zhangqiaohugangcu_2026_1` | BASE TABLE | 44 | 666263 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `model_wq_zhangqiaohugangcu_2026_2` | BASE TABLE | 44 | 385184 | H | 模型计算 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `pollutant_polymer_compose` | BASE TABLE | 10 | 0 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `pollutant_sampling_place` | BASE TABLE | 7 | 24 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `ps_aquaculture_emit` | BASE TABLE | 15 | 27 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `ps_citylife_emit` | BASE TABLE | 29 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `ps_cropfarming_emit` | BASE TABLE | 15 | 27 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `ps_emission_factor` | BASE TABLE | 20 | 122 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `ps_enterprise_emit` | BASE TABLE | 38 | 89 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `ps_livestock_emit` | BASE TABLE | 39 | 410 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `ps_village_treat_facility` | BASE TABLE | 15 | 724 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `ps_villagelife_emit` | BASE TABLE | 22 | 334 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_aquatic_pwxs_records` | BASE TABLE | 9 | 11 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_aquaticpollutant_info` | BASE TABLE | 15 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_aquaticpollutant_records` | BASE TABLE | 15 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_archives_info` | BASE TABLE | 14 | 0 | H | 其他业务 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `rs_citypollutant_info` | BASE TABLE | 14 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_citypollutant_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_citysewage_pwxs_records` | BASE TABLE | 9 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_complain_records` | BASE TABLE | 10 | 0 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_discharge_records` | BASE TABLE | 11 | 0 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_emergency_directory` | BASE TABLE | 16 | 4 | B | 项目与防治任务 | 纳入 | high | 实体主数据含义明确 |
| `rs_emergency_file` | BASE TABLE | 10 | 4 | D | 其他业务 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `rs_emergency_records` | BASE TABLE | 10 | 0 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_emergencymaterial_info` | BASE TABLE | 13 | 0 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_emergencyperson_info` | BASE TABLE | 11 | 0 | I | 其他业务 | 排除 | high | 整表涉及账号凭据或人员隐私，默认排除 |
| `rs_enforcelaw_records` | BASE TABLE | 10 | 0 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_enterprise_sensitive_info` | BASE TABLE | 8 | 0 | I | 其他业务 | 排除 | high | 整表涉及账号凭据或人员隐私，默认排除 |
| `rs_farmland_pwxs_records` | BASE TABLE | 13 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_farmlandpollutant_info` | BASE TABLE | 20 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_farmlandpollutant_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_industrypollutant_info` | BASE TABLE | 37 | 68 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_industrypollutant_records` | BASE TABLE | 63 | 47 | A | 污染源与排放 | 纳入 | medium | 业务事实或统计记录含义明确 |
| `rs_life_pwxs_records` | BASE TABLE | 11 | 11 | A | 其他业务 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_outlet` | BASE TABLE | 113 | 540 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `rs_outlet_bak_20260113` | BASE TABLE | 112 | 500 | H | 污染源与排放 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `rs_outlet_bark` | BASE TABLE | 64 | 405 | H | 污染源与排放 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `rs_outlet_file` | BASE TABLE | 11 | 0 | D | 污染源与排放 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `rs_outlet_info` | BASE TABLE | 71 | 254 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_outlet_records` | BASE TABLE | 50 | 51 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_outlet_year_records` | BASE TABLE | 30 | 506 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_pollutant_day_records` | BASE TABLE | 25 | 13909 | A | 污染源与排放 | 纳入 | high | B3 已批准范围，继续保留 |
| `rs_pollutant_deal_records` | BASE TABLE | 12 | 0 | E | 污染源与排放 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `rs_pollutant_hour_records` | BASE TABLE | 17 | 341725 | A | 污染源与排放 | 纳入 | high | B3 已批准范围，继续保留 |
| `rs_pollutant_info` | BASE TABLE | 28 | 205 | B | 污染源与排放 | 纳入 | high | B3 已批准范围，继续保留 |
| `rs_pollutant_outlet_info` | BASE TABLE | 9 | 6 | D | 污染源与排放 | 纳入 | medium | 业务关系明确，可支持实体关联 |
| `rs_pollutant_plastics` | BASE TABLE | 21 | 9 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_pollutant_section` | BASE TABLE | 8 | 5 | D | 污染源与排放 | 纳入 | medium | 业务关系明确，可支持实体关联 |
| `rs_pollutant_standard` | BASE TABLE | 12 | 8 | E | 污染源与排放 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `rs_pollutant_sulfonamides` | BASE TABLE | 23 | 15 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `rs_poultry_pwxs_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_poultrypollutant_info` | BASE TABLE | 13 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_poultrypollutant_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_riskenterprise_info` | BASE TABLE | 30 | 7 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_riskmaterial_info` | BASE TABLE | 14 | 0 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_riskmeasure_info` | BASE TABLE | 17 | 0 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_riskunit_info` | BASE TABLE | 14 | 0 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_ruralpollutant_info` | BASE TABLE | 14 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_ruralpollutant_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_scalepoultrypollutant_info` | BASE TABLE | 15 | 387 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_scalepoultrypollutant_records` | BASE TABLE | 13 | 387 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_sensitivetarget_info` | BASE TABLE | 10 | 0 | B | 其他业务 | 纳入 | high | 实体主数据含义明确 |
| `rs_sewagepollutant_info` | BASE TABLE | 16 | 11 | B | 污染源与排放 | 纳入 | high | 实体主数据含义明确 |
| `rs_sewagepollutant_records` | BASE TABLE | 14 | 11 | A | 污染源与排放 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_warn_publish_records` | BASE TABLE | 12 | 0 | F | 预警告警 | 排除 | medium | 空的消息发布执行记录，属于通知支持链路 |
| `rs_warn_records` | BASE TABLE | 25 | 11684 | A | 预警告警 | 纳入 | high | B3 已批准范围，继续保留 |
| `rs_warn_records_algal` | BASE TABLE | 23 | 0 | A | 预警告警 | 纳入 | high | 业务事实或统计记录含义明确 |
| `rs_wastewater_standard` | BASE TABLE | 12 | 24 | E | 污染源与排放 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `se_gdp` | BASE TABLE | 28 | 22 | B | 社会经济 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `se_population` | BASE TABLE | 12 | 0 | B | 社会经济 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `se_watershed` | BASE TABLE | 14 | 13 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `sm_group` | BASE TABLE | 16 | 24 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_login_log` | BASE TABLE | 11 | 9044 | F | 其他业务 | 排除 | high | 系统登录或操作日志，默认不开放业务问数 |
| `sm_menu` | BASE TABLE | 17 | 186 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_oper_log` | BASE TABLE | 16 | 463 | F | 其他业务 | 排除 | high | 系统登录或操作日志，默认不开放业务问数 |
| `sm_role` | BASE TABLE | 9 | 4 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_role_menu` | BASE TABLE | 8 | 387 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_user` | BASE TABLE | 23 | 9 | I | 其他业务 | 排除 | high | 整表涉及账号凭据或人员隐私，默认排除 |
| `sm_user_groupmag` | BASE TABLE | 8 | 95 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_user_role` | BASE TABLE | 8 | 61 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sm_user_setting` | BASE TABLE | 6 | 43 | G | 指标与字典 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `sys_log` | BASE TABLE | 16 | 9917 | F | 其他业务 | 排除 | high | 系统登录或操作日志，默认不开放业务问数 |
| `sys_oauth_client_details` | BASE TABLE | 14 | 7 | I | 其他业务 | 排除 | high | 整表涉及账号凭据或人员隐私，默认排除 |
| `sys_route_conf` | BASE TABLE | 10 | 15 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_data_field` | BASE TABLE | 10 | 53 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_layer_scale` | BASE TABLE | 4 | 2 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_layer_style` | BASE TABLE | 6 | 22 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_atttable` | BASE TABLE | 24 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_base` | BASE TABLE | 10 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_category` | BASE TABLE | 11 | 15 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_dynamic` | BASE TABLE | 26 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_map` | BASE TABLE | 17 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_nonspatial` | BASE TABLE | 34 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_raster` | BASE TABLE | 41 | 2 | G | 遥感监测 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_service` | BASE TABLE | 20 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_type` | BASE TABLE | 5 | 0 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_metadata_vector` | BASE TABLE | 35 | 54 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `t_table_core` | BASE TABLE | 15 | 94 | G | 其他业务 | 排除 | high | 系统框架、数据共享或离线缓存资产 |
| `we_ecology_img` | BASE TABLE | 10 | 27 | D | 水生态 | 排除 | high | 水生态照片附件，不直接开放结构化问数 |
| `we_ecology_info` | BASE TABLE | 10 | 12 | B | 水生态 | 纳入 | high | 实体主数据含义明确 |
| `we_ecologycomprehensive_records` | BASE TABLE | 11 | 47 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_ecologydata_records` | BASE TABLE | 14 | 201 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_ecologynutrition_records` | BASE TABLE | 12 | 8 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_ecologyquality_records` | BASE TABLE | 15 | 4 | A | 水质监测 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_fish_records` | BASE TABLE | 13 | 5 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_phytoplankton_records` | BASE TABLE | 12 | 7 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_sediment_records` | BASE TABLE | 11 | 4 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_zoobenthos_records` | BASE TABLE | 11 | 5 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `we_zooplankton_records` | BASE TABLE | 11 | 5 | A | 水生态 | 纳入 | high | 业务事实或统计记录含义明确 |
| `wh_hydrological_day_records` | BASE TABLE | 29 | 6628 | A | 水文监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wh_hydrological_hour_records` | BASE TABLE | 19 | 67585 | A | 水文监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wh_hydrological_hour_waterlevel` | BASE TABLE | 16 | 4404 | B | 水文监测 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wh_hydrological_records_1` | BASE TABLE | 11 | 16133 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_10` | BASE TABLE | 11 | 0 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_11` | BASE TABLE | 11 | 8402 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_12` | BASE TABLE | 11 | 23484 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_13` | BASE TABLE | 11 | 14424 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_14` | BASE TABLE | 11 | 19113 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_15` | BASE TABLE | 11 | 23647 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_16` | BASE TABLE | 11 | 15470 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_2` | BASE TABLE | 11 | 19890 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_25` | BASE TABLE | 11 | 1816 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_26` | BASE TABLE | 11 | 1816 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_3` | BASE TABLE | 11 | 909 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_37` | BASE TABLE | 11 | 17236 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_4` | BASE TABLE | 11 | 908 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_5` | BASE TABLE | 11 | 908 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_6` | BASE TABLE | 11 | 14807 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_7` | BASE TABLE | 11 | 0 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_8` | BASE TABLE | 11 | 0 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_hydrological_records_9` | BASE TABLE | 11 | 0 | A | 水文监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_meteorological_day_records` | BASE TABLE | 42 | 2515 | A | 气象监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wh_meteorological_hour_records` | BASE TABLE | 24 | 57421 | A | 气象监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wh_meteorological_predict_day_records` | BASE TABLE | 40 | 14736 | A | 气象监测 | 纳入 | high | 业务事实或统计记录含义明确 |
| `wh_meteorological_predict_hour_records` | BASE TABLE | 41 | 348413 | A | 气象监测 | 纳入 | high | 业务事实或统计记录含义明确 |
| `wh_meteorological_records_37` | BASE TABLE | 11 | 67130 | A | 气象监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_meteorological_records_5` | BASE TABLE | 11 | 48841 | A | 气象监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wh_waterfacility_info` | BASE TABLE | 15 | 0 | B | 水文监测 | 纳入 | high | 实体主数据含义明确 |
| `wm_camera` | BASE TABLE | 33 | 100 | B | 视频与设备 | 纳入 | high | 实体主数据含义明确 |
| `wm_camera_info` | BASE TABLE | 18 | 20 | B | 视频与设备 | 纳入 | high | 实体主数据含义明确 |
| `wm_camera_underwater` | BASE TABLE | 20 | 3 | B | 视频与设备 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_device_dic` | BASE TABLE | 5 | 28 | C | 视频与设备 | 纳入 | high | 字段编码或字典含义明确 |
| `wm_directory` | BASE TABLE | 10 | 30 | G | 其他业务 | 排除 | high | 全景资源目录树，属于展示支持配置 |
| `wm_hydrological_info` | BASE TABLE | 41 | 19 | B | 水文监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_manage_center` | BASE TABLE | 7 | 0 | B | 其他业务 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_meteorological_info` | BASE TABLE | 41 | 8 | B | 气象监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_outlet_sampler` | BASE TABLE | 7 | 50 | B | 污染源与排放 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_panorama_layer` | BASE TABLE | 11 | 49 | G | 其他业务 | 排除 | high | 全景展示图层配置，不属于业务问数事实 |
| `wm_panorama_layer_relation` | BASE TABLE | 7 | 77 | G | 其他业务 | 排除 | high | 全景展示图层关系，不属于业务问数关系 |
| `wm_panorama_video` | BASE TABLE | 9 | 44 | D | 其他业务 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `wm_picture_records` | BASE TABLE | 11 | 6 | D | 其他业务 | 排除 | high | 照片附件记录，不直接开放结构化问数 |
| `wm_raster_info` | BASE TABLE | 15 | 138 | B | 遥感监测 | 纳入 | high | 实体主数据含义明确 |
| `wm_raster_inversion` | BASE TABLE | 20 | 1320 | A | 遥感监测 | 纳入 | high | 业务事实或统计记录含义明确 |
| `wm_raster_inversion_config` | BASE TABLE | 9 | 0 | E | 遥感监测 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `wm_section_info` | BASE TABLE | 30 | 49 | B | 其他业务 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_section_wq_info` | BASE TABLE | 10 | 1053 | E | 水质监测 | 排除 | high | B4 水质日报/月报专用目标配置，保持在报表处理器范围之外 |
| `wm_spectrum_camera` | BASE TABLE | 7 | 6 | B | 视频与设备 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_station_info` | BASE TABLE | 47 | 33 | B | 其他业务 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_uav_bark` | BASE TABLE | 31 | 7 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_dj` | BASE TABLE | 13 | 6 | B | 视频与设备 | 纳入 | high | 实体主数据含义明确 |
| `wm_uav_dj_bark` | BASE TABLE | 13 | 6 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_info` | BASE TABLE | 33 | 8 | B | 视频与设备 | 纳入 | high | 实体主数据含义明确 |
| `wm_uav_info_bak` | BASE TABLE | 15 | 8 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_info_bark` | BASE TABLE | 15 | 8 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_info_bark4` | BASE TABLE | 33 | 7 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_info_bark5` | BASE TABLE | 33 | 7 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_info_bark6` | BASE TABLE | 33 | 7 | H | 视频与设备 | 排除 | high | 名称和表注释表明是备份、历史或归档副本 |
| `wm_uav_track` | BASE TABLE | 17 | 14439 | A | 巡查调查 | 纳入 | high | 业务事件或轨迹事实记录 |
| `wm_unmaned_ship` | BASE TABLE | 16 | 0 | B | 视频与设备 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_water_source` | BASE TABLE | 45 | 4 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_water_source_problem` | BASE TABLE | 22 | 14 | B | 水体与空间地理 | 纳入 | medium | 表或字段注释能够解释业务实体 |
| `wm_waterbody_info` | BASE TABLE | 27 | 38 | B | 水体与空间地理 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_waterquality_day_records` | BASE TABLE | 73 | 8104 | A | 水质监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_waterquality_hour_records` | BASE TABLE | 82 | 170401 | A | 水质监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_waterquality_month_records` | BASE TABLE | 77 | 722 | A | 水质监测 | 纳入 | high | B3 已批准范围，继续保留 |
| `wm_waterquality_records_1` | BASE TABLE | 13 | 48602 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_10` | BASE TABLE | 13 | 49343 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_11` | BASE TABLE | 13 | 51212 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_12` | BASE TABLE | 13 | 50868 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_13` | BASE TABLE | 13 | 52005 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_14` | BASE TABLE | 13 | 52489 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_15` | BASE TABLE | 13 | 44184 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_16` | BASE TABLE | 13 | 42551 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_17` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_18` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_19` | BASE TABLE | 13 | 14525 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_2` | BASE TABLE | 13 | 51609 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_20` | BASE TABLE | 13 | 5635 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_21` | BASE TABLE | 13 | 11202 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_22` | BASE TABLE | 13 | 3832 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_23` | BASE TABLE | 13 | 11813 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_24` | BASE TABLE | 13 | 11628 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_25` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_26` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_27` | BASE TABLE | 13 | 17314 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_28` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_29` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_3` | BASE TABLE | 13 | 70752 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_30` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_31` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_32` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_33` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_34` | BASE TABLE | 13 | 12848 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_35` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_36` | BASE TABLE | 13 | 0 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_37` | BASE TABLE | 13 | 65353 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_4` | BASE TABLE | 13 | 77758 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_5` | BASE TABLE | 13 | 49983 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_6` | BASE TABLE | 13 | 48170 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_7` | BASE TABLE | 13 | 53479 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_8` | BASE TABLE | 13 | 47340 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_records_9` | BASE TABLE | 13 | 51609 | A | 水质监测 | 排除 | high | 按站点或月份拆分的物理明细分片，缺少稳定通用查询语义 |
| `wm_waterquality_threshold` | BASE TABLE | 38 | 33 | E | 水质监测 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `wm_waterquality_year_records` | BASE TABLE | 71 | 0 | J | 水质监测 | 排除 | low | 空表且表名为年记录、表注释却为月记录，存在冲突语义 |
| `wm_weather_station` | BASE TABLE | 7 | 2 | B | 气象监测 | 纳入 | high | 实体主数据含义明确 |
| `wp_task_directory` | BASE TABLE | 16 | 32 | B | 项目与防治任务 | 纳入 | high | 实体主数据含义明确 |
| `wp_task_file` | BASE TABLE | 15 | 30 | D | 项目与防治任务 | 排除 | medium | 附件或媒体支持表，不直接开放结构化问数 |
| `wp_task_info_proj` | BASE TABLE | 49 | 6 | B | 项目与防治任务 | 纳入 | high | 实体主数据含义明确 |
| `wp_task_info_proj_dynamic` | BASE TABLE | 10 | 19 | B | 项目与防治任务 | 纳入 | high | 实体主数据含义明确 |
| `wp_task_info_target` | BASE TABLE | 19 | 10 | B | 项目与防治任务 | 纳入 | high | 实体主数据含义明确 |
| `wt_service_directory` | BASE TABLE | 25 | 85 | G | 其他业务 | 排除 | high | 地图与接口服务目录，属于系统服务配置 |
| `wt_warnparm_config` | BASE TABLE | 21 | 24 | E | 预警告警 | 纳入 | high | 业务配置或指标规则可供查询解释 |
| `yn_s_address_area` | BASE TABLE | 8 | 2978 | J | 其他业务 | 排除 | low | 缺少表注释、代码引用或有效数据证据，语义待确认 |
| `yn_s_address_city` | BASE TABLE | 7 | 342 | J | 其他业务 | 排除 | low | 缺少表注释、代码引用或有效数据证据，语义待确认 |
| `yn_s_address_province` | BASE TABLE | 6 | 31 | J | 其他业务 | 排除 | low | 缺少表注释、代码引用或有效数据证据，语义待确认 |
| `yn_s_address_street` | BASE TABLE | 10 | 39717 | J | 其他业务 | 排除 | low | 缺少表注释、代码引用或有效数据证据，语义待确认 |

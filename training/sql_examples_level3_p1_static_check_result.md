# Level 3 P1 SQL 候选草案静态检查结果

## 汇总

- 工作目录：`E:\3\posgresql\1`
- git remote：

```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 基础 commit：`ad10a44c802154ede2e1296826ca63732a6b523e`
- 初始 `git status --short`：

```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```
- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否
- 是否调用 `vn.train()`：否
- 是否调用 `memory.save_tool_usage()`：否
- 是否写入 ChromaDB：否
- 是否修改正式 `vanna_data`：否
- 草案总数：24
- C/D/E 数量：10/8/6
- ID 唯一：是
- ID 连续：是
- SQLGuard passed 数量：24
- severity ok 数量：24
- metadata 字段通过数量：24
- 单表边界通过数量：24
- P0 question 完全重复数量：0
- warning 数量：0
- fail 数量：0

## 逐样本检查表

| id | group | question | expected table | used tables | expected columns | unknown tables | unknown columns | SQLGuard passed | severity | warning/error | 最终状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L3_P1_SQL_001 | C | 查询排污口监测数据中的COD和氨氮记录 | rs_outlet_monitor_v2 | rs_outlet_monitor_v2 | rs_outlet_monitor_v2.ammonia_nitrogen, rs_outlet_monitor_v2.cod, rs_outlet_monitor_v2.outlet_name, rs_outlet_monitor_v2.sampling_time | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_002 | C | 查看排污口最近的pH、BOD和流量监测记录 | rs_outlet_monitor_v2 | rs_outlet_monitor_v2 | rs_outlet_monitor_v2.bod, rs_outlet_monitor_v2.flow, rs_outlet_monitor_v2.outlet_name, rs_outlet_monitor_v2.ph, rs_outlet_monitor_v2.sampling_time | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_003 | C | 查询排污口排水特征和在线监测状态 | rs_outlet_live_v2 | rs_outlet_live_v2 | rs_outlet_live_v2.drainage_feature, rs_outlet_live_v2.has_online_monitor, rs_outlet_live_v2.outlet_name | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_004 | C | 筛选存在异常状况的排污口 | rs_outlet_live_v2 | rs_outlet_live_v2 | rs_outlet_live_v2.has_abnormal, rs_outlet_live_v2.outlet_name | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_005 | C | 查询已整治排污口及整治类型 | rs_outlet_remediation_v2 | rs_outlet_remediation_v2 | rs_outlet_remediation_v2.is_remediated, rs_outlet_remediation_v2.outlet_name, rs_outlet_remediation_v2.remediation_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_006 | C | 查询排污口规范化建设状态 | rs_outlet_remediation_v2 | rs_outlet_remediation_v2 | rs_outlet_remediation_v2.is_standardized, rs_outlet_remediation_v2.outlet_name | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_007 | C | 查询PS类型排水口的COD、总氮和pH日记录 | rs_wastewater_day_records | rs_wastewater_day_records | rs_wastewater_day_records.m1_value, rs_wastewater_day_records.m2_value, rs_wastewater_day_records.m3_value, rs_wastewater_day_records.pollutant_id, rs_wastewater_day_records.status, rs_wastewater_day_records.timestamp, rs_wastewater_day_records.type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_008 | C | 查询废水小时流量、排放量和状态趋势 | rs_wastewater_hour_records | rs_wastewater_hour_records | rs_wastewater_hour_records.ll, rs_wastewater_hour_records.pfl, rs_wastewater_hour_records.pollutant_id, rs_wastewater_hour_records.status, rs_wastewater_hour_records.timestamp, rs_wastewater_hour_records.type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_009 | C | 查询PS类型废水月度COD、总氮、pH和排放数据 | rs_wastewater_month_records | rs_wastewater_month_records | rs_wastewater_month_records.ll, rs_wastewater_month_records.m1_value, rs_wastewater_month_records.m2_value, rs_wastewater_month_records.m3_value, rs_wastewater_month_records.monitor_month, rs_wastewater_month_records.monitor_year, rs_wastewater_month_records.pfl, rs_wastewater_month_records.pollutant_id, rs_wastewater_month_records.status, rs_wastewater_month_records.type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_010 | C | 统计具备采样条件的排污口数量 | rs_outlet_live_v2 | rs_outlet_live_v2 | rs_outlet_live_v2.has_sampling_condition | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_011 | D | 查询断面编码、名称、级别、属性和考核状态 | wm_section_info | wm_section_info | wm_section_info.is_examine, wm_section_info.section_code, wm_section_info.section_level, wm_section_info.section_name, wm_section_info.section_nature, wm_section_info.water_body_id | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_012 | D | 查询水文站基础信息和建设状态 | wm_hydrological_info | wm_hydrological_info | wm_hydrological_info.belong_to_city, wm_hydrological_info.build_state, wm_hydrological_info.region_code, wm_hydrological_info.station_code, wm_hydrological_info.station_name, wm_hydrological_info.station_type, wm_hydrological_info.water_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_013 | D | 按城市统计水文站记录数 | wm_hydrological_info | wm_hydrological_info | wm_hydrological_info.belong_to_city | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_014 | D | 查询水体基础信息、类型、功能和所在流域 | wm_waterbody_info | wm_waterbody_info | wm_waterbody_info.area, wm_waterbody_info.basin, wm_waterbody_info.length, wm_waterbody_info.water_body_code, wm_waterbody_info.water_body_function, wm_waterbody_info.water_body_name, wm_waterbody_info.water_body_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_015 | D | 查询摄像头设备基础信息和监控对象 | wm_camera_info | wm_camera_info | wm_camera_info.address, wm_camera_info.camera_name, wm_camera_info.device_code, wm_camera_info.device_supplier, wm_camera_info.device_type, wm_camera_info.monitor_subject | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_016 | D | 查询摄像头平台设备、厂商、型号和在线状态 | wm_camera_platform | wm_camera_platform | wm_camera_platform.device_code, wm_camera_platform.manufacturer, wm_camera_platform.model, wm_camera_platform.name, wm_camera_platform.online, wm_camera_platform.transport | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_017 | D | 查询无人机名称、品牌、型号和在线状态 | wm_uav_info | wm_uav_info | wm_uav_info.brand, wm_uav_info.code, wm_uav_info.drone_callsign, wm_uav_info.drone_device_model, wm_uav_info.drone_device_online_status, wm_uav_info.drone_sn, wm_uav_info.gateway_device_online_status, wm_uav_info.name | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_018 | D | 查询区县级行政区名称、编码和所属城市 | gis_region_county | gis_region_county | gis_region_county.city, gis_region_county.region_code, gis_region_county.region_name | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_019 | E | 按水源类型统计普通取水口记录数 | wm_water_intake | wm_water_intake | wm_water_intake.water_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_020 | E | 按城市和区县查询普通取水口 | wm_water_intake | wm_water_intake | wm_water_intake.city, wm_water_intake.county, wm_water_intake.name, wm_water_intake.water_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_021 | E | 查询普通取水口行政区域和使用状态 | wm_water_intake | wm_water_intake | wm_water_intake.name, wm_water_intake.region_code, wm_water_intake.region_name, wm_water_intake.used_mark, wm_water_intake.water_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_022 | E | 查询水源地名称、类型、状态和所在区域 | wm_water_source | wm_water_source | wm_water_source.name, wm_water_source.region_name, wm_water_source.source_state, wm_water_source.source_type | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_023 | E | 查询水源地保护等级和保护区划定状态 | wm_water_source | wm_water_source | wm_water_source.name, wm_water_source.protect_area_cert, wm_water_source.protect_area_status, wm_water_source.protect_level | 无 | 无 | 是 | ok | 无 | pass |
| L3_P1_SQL_024 | E | 按年实际取水量从高到低查看水源地及其服务人口 | wm_water_source | wm_water_source | wm_water_source.name, wm_water_source.service_people_count, wm_water_source.supply_water_daily, wm_water_source.supply_water_year | 无 | 无 | 是 | ok | 无 | pass |

## warning 和 fail 明细

- 无

## P1 特殊规则

- L3_P1_SQL_007 是否限定 `type='PS'`：是
- L3_P1_SQL_008 是否排除 m1_value 至 m22_value：是
- L3_P1_SQL_009 是否限定 `type='PS'`：是
- E 组是否使用 `wm_water_source_intake_v2`：否
- 所有样本是否为单表且无 JOIN：是
- P1 与 P0 question 完全重复数量：0

## 最终结论

**通过。**

24 条 P1 候选草案全部通过结构、SQLGuard、metadata 字段、单表边界和 P0 去重检查；本阶段未训练、未写入 ChromaDB。

## 下一阶段建议

对 24 条 draft 进行人工业务审查，生成独立 review_result；审查阶段仍不训练、不写 ChromaDB。

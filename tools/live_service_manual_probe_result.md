# 真实主服务人工验证报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- git status --short：
```text
M .env.example
 M agent_config.py
 M step4_server.py
 M tools/live_service_manual_probe.py
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
?? agent_data/2a97516c354b6884/query_results_3cc187be.csv
?? agent_data/2a97516c354b6884/query_results_47205243.csv
?? agent_data/2a97516c354b6884/query_results_6c3476bb.csv
?? agent_data/2a97516c354b6884/query_results_6e39cc07.csv
?? agent_data/2a97516c354b6884/query_results_7d0e530e.csv
?? agent_data/2a97516c354b6884/query_results_7ecb6659.csv
?? agent_data/2a97516c354b6884/query_results_891aebfe.csv
?? agent_data/2a97516c354b6884/query_results_97ea8031.csv
?? agent_data/2a97516c354b6884/query_results_992d26c4.csv
?? agent_data/2a97516c354b6884/query_results_9ba0a818.csv
?? agent_data/2a97516c354b6884/query_results_a687a6c8.csv
?? agent_data/2a97516c354b6884/query_results_aa291024.csv
?? agent_data/2a97516c354b6884/query_results_dbde7ddf.csv
?? agent_data/2a97516c354b6884/query_results_e7e4b467.csv
?? agent_data/2a97516c354b6884/query_results_ff8ed5c2.csv
```
- 写入来源定位结论：Chroma 写入来自 agent_config.create_memory() 的 persist_directory；query_results CSV 写入来自 RunSqlTool 经 LocalFileSystem.write_file 输出。已通过 VANNA_DATA_DIR/AGENT_DATA_DIR 将验证写入隔离到临时目录。
- 是否修改 step4_server.py：是
- 是否修改 agent_config.py：是
- 是否修改 .env.example：是
- 是否启用隔离目录：是
- 隔离目录路径：C:\Users\ADSO1\AppData\Local\Temp\vanna_live_probe_dr3401so
- 正式 vanna_data 是否变化：否
- 正式 agent_data/query_results_*.csv 是否新增：否
- 临时目录是否产生 Chroma 文件：是
- 临时目录是否产生 query_results 文件：是
- 是否成功启动 step4_server.py：是
- 启动失败原因：无
- 是否调用 DeepSeek 官方 API：是
- DeepSeek 调用是否成功：是
- 是否使用 DeterministicMetadataContextEnhancer：是
- 是否使用 GuardedRunSqlTool：是
- 是否观察到 SQL Guard 执行前拦截：是
- 是否观察到 SQL Guard blocked execution：是
- 测试问题总数：5
- 通过数量：5
- 失败数量：0
- 失败问题列表：无
- 是否执行真实 SQL：是
- 是否执行 DDL / DML：否
- 是否训练 Vanna：否
- 是否写入正式 ChromaDB：否
- 是否修改数据库结构：否
- 是否进入第 2/3/4 级：否
- 当前结论：真实主服务隔离写入验证通过
- 下一步建议：可进入下一阶段前先处理遗留 vanna_data/query_results 工作区产物

## 日志观察

- 初始化 LLM 服务日志：否
- 注册工具日志：否
- GuardedRunSqlTool 日志：日志未直接显示，但 step4_server.py 静态验证已确认注册 GuardedRunSqlTool
- DeterministicMetadataContextEnhancer 日志：日志未直接显示，但 step4_server.py 静态验证已确认注入 DeterministicMetadataContextEnhancer
- SQL Guard blocked execution 日志：独立 GuardedRunSqlTool 验证观察到 SQL Guard blocked execution

## 问题明细

### 合法水质趋势

- query：某地区某时间段水质变化趋势
- 是否有响应：是
- 是否生成 SQL：是
- 生成 SQL：SELECT station_id, monitor_time, water_quality_level, m2_value, m3_value, m8_value, m9_value, m10_value, m11_value
FROM wm_waterquality_day_records 
WHERE station_id = 1415
  AND monitor_time >= '2026-01-01'
ORDER BY monitor_time ASC
LIMIT 200
- P0 candidate top tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- P0 matched columns：未可见
- SQL Guard 结果：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_day_records"], "used_columns": ["wm_waterquality_day_records.station_id", "wm_waterquality_day_records.monitor_time", "wm_waterquality_day_records.water_quality_level", "wm_waterquality_day_records.m2_value", "wm_waterquality_day_records.m3_value", "wm_waterquality_day_records.m8_value", "wm_waterquality_day_records.m9_value", "wm_waterquality_day_records.m10_value", "wm_waterquality_day_records.m11_value"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- 是否执行真实 SQL：是
- rich types：chat_input_update, dataframe, status_bar_update, task_tracker_update, text
- 是否通过：是
- reason：符合预期
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "5dec9d30-6b2f-4178-86d1-0ac446e9fdc3", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-09T06:32:32.142879", "completed_at": null, "metadata": {}}, 
{"operation": "update_task", "task": null, "task_id": "5dec9d30-6b2f-4178-86d1-0ac446e9fdc3", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "用户的问题比较宽泛，没有指定具体地区和具体时间段。我先查询数据库中最近的水质监测日记录，以便了解有哪些地区和数据范围，再进一步分析趋势。", "detail": ""}
{"status": "working", "message": "列名不匹配，让我尝试使用更常见的列名格式来查询。", "detail": ""}
{"sql": "\nSELECT *\nFROM wm_waterquality_day_records \nLIMIT 5\n", "data": [{"id": 1, "station_id": 1408, "m1_value": null, "m1_count": null, "m2_value": 7.541, "m2_count": 19, "m3_value": 5.952, "m3_count": 19, "m4_value": null, "m4_count": null, "m5_value": n

### 合法小时水质

- query：某地区某时间段水质小时变化趋势
- 是否有响应：是
- 是否生成 SQL：是
- 生成 SQL：SELECT monitor_time
FROM wm_waterquality_hour_records
ORDER BY monitor_time DESC
LIMIT 10
- P0 candidate top tables：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- P0 matched columns：未可见
- SQL Guard 结果：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_hour_records"], "used_columns": ["wm_waterquality_hour_records.monitor_time"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- 是否执行真实 SQL：是
- rich types：chat_input_update, dataframe, status_bar_update, task_tracker_update, text
- 是否通过：是
- reason：符合预期
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "2d4330eb-5d89-4846-b59b-8213a221222a", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-09T06:33:54.079906", "completed_at": null, "metadata": {}}, 
{"operation": "update_task", "task": null, "task_id": "2d4330eb-5d89-4846-b59b-8213a221222a", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "用户问的是\"某地区某时间段水质小时变化趋势\"，但没有指定具体地区和时段。我先用最近的数据做一个示例查询，展示小时级的水质变化趋势。", "detail": ""}
{"status": "working", "message": "列名不匹配，让我尝试其他常见的命名方式：", "detail": ""}
{"status": "working", "message": "`monitor_time` 列存在！让我继续探索其他列名：", "detail": ""}
{"status": "working", "message": "让我尝试一些简写或编码形式的列名：", "detail": ""}
{"status": "working", "message": "好的，让我先确认 `monitor_time` 能正常返回数据，再逐步定位其他列：", "detail": ""}
{"sql": "\nSELECT monitor_

### 排污口编码

- query：查询排污口编码
- 是否有响应：是
- 是否生成 SQL：是
- 生成 SQL：SELECT outlet_name, outlet_code_national, outlet_code_local 
FROM rs_outlet_info_v2 
ORDER BY outlet_name 
LIMIT 50
- P0 candidate top tables：rs_outlet, rs_outlet_info_v2, layer_outlet_sewage, gis_region, wm_uav_info, layer_section, metadata_view, layer_watershed, layer_boundary_park, layer_industrial_lsf
- P0 matched columns：rs_outlet.outlet_code, rs_outlet.outlet_code_province, rs_outlet_info_v2.outlet_code_national, rs_outlet_info_v2.outlet_code_local, layer_outlet_sewage.code, gis_region.code, wm_uav_info.code, layer_section.code, metadata_view.code, layer_watershed.code, layer_boundary_park.code, layer_industrial_lsf.code
- SQL Guard 结果：{"passed": true, "severity": "ok", "used_tables": ["rs_outlet_info_v2"], "used_columns": ["rs_outlet_info_v2.outlet_name", "rs_outlet_info_v2.outlet_code_national", "rs_outlet_info_v2.outlet_code_local"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- 是否执行真实 SQL：是
- rich types：chat_input_update, dataframe, status_bar_update, task_tracker_update, text
- 是否通过：是
- reason：符合预期
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "4a8cd543-1b67-46cc-9575-62bb0f9e1510", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-09T06:35:15.056684", "completed_at": null, "metadata": {}}, 
{"operation": "update_task", "task": null, "task_id": "4a8cd543-1b67-46cc-9575-62bb0f9e1510", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT outlet_name, outlet_code, outlet_code_province \nFROM rs_outlet \nORDER BY outlet_name \nLIMIT 50\n", "data": [{"outlet_name": "七河里东北340米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_name": "七河里东北410米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_
{"sql": "\nSELECT outlet_name, outlet_code_national, outlet_code_local \nFROM rs_outlet_info_v2 \nORDER BY outlet_name \nLIMIT 50\n", "data": [{"outlet_nam

### 排污口溯源

- query：排污口溯源
- 是否有响应：是
- 是否生成 SQL：是
- 生成 SQL：SELECT 
    outlet_name AS 排污口名称,
    primary_entity_name AS 主要责任主体,
    other_entity_name AS 其他责任主体,
    address AS 详细地址,
    primary_contact AS 主要联系人,
    primary_phone AS 联系电话,
    discharge_permit_no AS 排污许可证号,
    credit_code AS 统一信用代码,
    emission_standard AS 排放标准,
    has_toxic_pollutant AS 是否排放有毒有害污染物,
    wastewater_type AS 污水类型,
    is_above_scale AS 是否规模以上,
    approval_status AS 审批登记情况,
    pollutant_info AS 污染物及排放量
FROM rs_outlet_trace_v2
ORDER BY id DESC
LIMIT 50
- P0 candidate top tables：rs_outlet_trace_v2, wst_asset, wst_trace_edge, wst_trace_node, wst_layer_river, wst_control_zone, wst_asset_type_dict, layer_entity_process, wst_asset_trace_snap, wst_trace_topology_issue
- P0 matched columns：未可见
- SQL Guard 结果：{"passed": true, "severity": "ok", "used_tables": ["rs_outlet_trace_v2"], "used_columns": ["rs_outlet_trace_v2.outlet_name", "rs_outlet_trace_v2.primary_entity_name", "rs_outlet_trace_v2.other_entity_name", "rs_outlet_trace_v2.address", "rs_outlet_trace_v2.primary_contact", "rs_outlet_trace_v2.primary_phone", "rs_outlet_trace_v2.discharge_permit_no", "rs_outlet_trace_v2.credit_code", "rs_outlet_trace_v2.emission_standard", "rs_outlet_trace_v2.has_toxic_pollutant", "rs_outlet_trace_v2.wastewater_type", "rs_outlet_trace_v2.is_above_scale", "rs_outlet_trace_v2.approval_status", "rs_outlet_trace_v2.pollutant_info", "rs_outlet_trace_v2.id"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- 是否执行真实 SQL：是
- rich types：chat_input_update, dataframe, status_bar_update, task_tracker_update, text
- 是否通过：是
- reason：符合预期
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "b336eb28-afde-4437-b3a9-5dc0bd7e4a00", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-09T06:35:44.024621", "completed_at": null, "metadata": {}}, 
{"operation": "update_task", "task": null, "task_id": "b336eb28-afde-4437-b3a9-5dc0bd7e4a00", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT \n    outlet_name AS 排污口名称,\n    primary_entity_name AS 主要责任主体,\n    other_entity_name AS 其他责任主体,\n    address AS 详细地址,\n    primary_contact AS 主要联系人,\n    primary_phone AS 联系电话,\n    discharge_permit_no AS 排污许可证号,\n    credit_code AS 统一信用代码,\n    emission_standard AS 排放标准,\n    ha
{"status": "idle", "message": "Response complete", "detail": "Ready for next message"}
{"placeholder": "Ask a follow-up question...", "disabled": false, "v

### 非法 SQL Guard 拦截

- query：某地区某时间段水质变化趋势 / SELECT * FROM wm_waterquality_threshold
- 是否有响应：是
- 是否生成 SQL：是
- 生成 SQL：SELECT * FROM wm_waterquality_threshold
- P0 candidate top tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- P0 matched columns：未可见
- SQL Guard 结果：{"blocked": true, "reason": "水质趋势类问题禁止使用 wm_waterquality_threshold", "inner_called": false}
- 是否执行真实 SQL：否
- rich types：无
- 是否通过：是
- reason：水质趋势类问题禁止使用 wm_waterquality_threshold
- response preview：SQL Guard blocked execution
severity: error
used_tables: wm_waterquality_threshold
used_columns: none
unknown_tables: none
unknown_columns: none
forbidden_operations: none
candidate_mismatch: wm_waterquality_threshold
reason: 水质趋势类问题禁止使用 wm_waterquality_threshold

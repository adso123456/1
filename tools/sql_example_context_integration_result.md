# SQL Example Context Enhancer 接入隔离验证报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：6259466b2478b8c25f9e687bc0da8beed2a8658a
- 初始 git status --short：
```text
clean
```
- 修改/新增文件路径：step4_server.py
- 是否接入主服务：是
- 是否启动真实主服务：是
- 是否使用临时 VANNA_DATA_DIR：是
- 是否使用临时 AGENT_DATA_DIR：是
- 正式 vanna_data 是否变化：否
- 正式 agent_data/query_results_*.csv 是否新增：否
- 是否连接数据库：是
- 是否执行真实 SQL：是
- 是否调用 DeepSeek：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入正式 ChromaDB：否
- 是否修改数据库结构：否
- 是否进入第 3/4 级：否
- 接入链路静态验证是否通过：是
- smoke 测试总数：5
- pass 数量：5
- warning 数量：0
- fail 数量：0
- fail 问题列表：无
- Q3 是否通过：是
- Q4 是否通过：是
- Q9 是否通过：是
- Q9 true_sql_executed：否
- 当前结论：通过
- 下一阶段建议：可进入全量验证

## 正式 vanna_data 指纹

- 验证前：{"vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin": [218800, "dc253996916c68ec30b8035eac78cb34d04e13019b645a7ed9866a10c90d6678"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin": [100, "5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin": [400, "7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin": [0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"], "vanna_data/chroma.sqlite3": [462848, "00a1601cfd160785c0e7763833334267df0ecd70d4f4e47e4ace84807ccf9f52"]}
- 验证后：{"vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin": [218800, "dc253996916c68ec30b8035eac78cb34d04e13019b645a7ed9866a10c90d6678"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin": [100, "5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin": [400, "7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5"], "vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin": [0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"], "vanna_data/chroma.sqlite3": [462848, "00a1601cfd160785c0e7763833334267df0ecd70d4f4e47e4ace84807ccf9f52"]}

## 问题明细

### Q1

- question：查询某站点水质日趋势中的 pH 和溶解氧变化
- expected：使用 wm_waterquality_day_records，字段至少包含 m2_value 或 m3_value，不使用 wm_waterquality_threshold
- generated_sql：SELECT station_id, monitor_time, m2_value, m3_value
FROM wm_waterquality_day_records
WHERE station_id = 1408 
  AND m2_value IS NOT NULL 
  AND m3_value IS NOT NULL
ORDER BY monitor_time
LIMIT 100
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.monitor_time, wm_waterquality_day_records.m2_value, wm_waterquality_day_records.m3_value
- P0 candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- P0 matched columns：rs_outlet_monitor_v2.ph
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_day_records"], "used_columns": ["wm_waterquality_day_records.station_id", "wm_waterquality_day_records.monitor_time", "wm_waterquality_day_records.m2_value", "wm_waterquality_day_records.m3_value"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "76a5b340-3516-4dc1-b82a-19eb198747e3", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-10T03:23:35.632884", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "76a5b340-3516-4dc1-b82a-19eb198747e3", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "用户没有指定具体站点，我先使用示例站点（1408）查询水质日记录中的 pH（m2_value）和溶解氧（m3_value）变化趋势。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_time, m2_value, m3_value\nFROM wm_waterquality_day_records\nWHERE station_id = 1408 \n  AND m2_value IS NOT NULL \n  AND m3_value IS NOT NULL\nORDER BY monitor_time\nLIMIT 100\n", "data": [{"station_id": 1408, "monitor_time": "2025-09-10T19:00:00", "m2_value": 7.567, "m3_value": 6.082}, {"station_id": 1408, "monitor_time": "2025-09-11T18:00:00", "m2_value": 7.541, "m3_value": 5.952}, {"station_id": 1408, "monitor_time": "2025-09-12
- whether prompt likely contained SQL example：否
- matched L2 sample id：无
- pass/warning/fail：pass
- reason：符合预期

### Q2

- question：某站点水质小时变化趋势
- expected：使用 wm_waterquality_hour_records，字段至少包含 monitor_time 和 m*_value，不退化到 day/month/threshold
- generated_sql：SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level
FROM wm_waterquality_hour_records
WHERE station_id = 1408 AND monitor_time >= '2026-05-10' AND monitor_time < '2026-05-12'
ORDER BY monitor_time
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m1_value, wm_waterquality_hour_records.m2_value, wm_waterquality_hour_records.m3_value, wm_waterquality_hour_records.water_quality_level
- P0 candidate tables：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- P0 matched columns：unknown
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_hour_records"], "used_columns": ["wm_waterquality_hour_records.station_id", "wm_waterquality_hour_records.monitor_time", "wm_waterquality_hour_records.m1_value", "wm_waterquality_hour_records.m2_value", "wm_waterquality_hour_records.m3_value", "wm_waterquality_hour_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "ed201377-02b3-4b12-a385-93dc70fb3db6", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-10T03:23:58.295440", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "ed201377-02b3-4b12-a385-93dc70fb3db6", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据示例查询，我来为您查看站点1408的近期水质小时变化趋势。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level\nFROM wm_waterquality_hour_records\nWHERE station_id = 1408 AND monitor_time >= '2026-07-03'\nORDER BY monitor_time\nLIMIT 200\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": f
{"statu
- whether prompt likely contained SQL example：否
- matched L2 sample id：无
- pass/warning/fail：pass
- reason：符合预期

### Q3

- question：某站点水质月变化趋势
- expected：使用 wm_waterquality_month_records，字段包含 monitor_year/monitor_month，字段至少包含 m2_value 或 m3_value，不退化到 day/hour/year/threshold
- generated_sql：SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level
FROM wm_waterquality_month_records
WHERE station_id = 1408
ORDER BY monitor_year, monitor_month
LIMIT 60
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.m2_value, wm_waterquality_month_records.m3_value, wm_waterquality_month_records.water_quality_level
- P0 candidate tables：wm_waterquality_month_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records
- P0 matched columns：unknown
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_month_records"], "used_columns": ["wm_waterquality_month_records.station_id", "wm_waterquality_month_records.monitor_year", "wm_waterquality_month_records.monitor_month", "wm_waterquality_month_records.m2_value", "wm_waterquality_month_records.m3_value", "wm_waterquality_month_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "0cc86a91-53cb-4edf-bf12-cabc9855a1c5", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-10T03:24:42.992286", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "0cc86a91-53cb-4edf-bf12-cabc9855a1c5", "status": "completed", "progress": null, "detail": null}
{"sql": "SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level\nFROM wm_waterquality_month_records\nWHERE station_id = 1408 AND monitor_year >= 2025\nORDER BY monitor_year, monitor_month\nLIMIT 60", "data": [{"station_id": 1408, "monitor_year": 2026, "monitor_month": 3, "m2_value": 7.86, "m3_value": 9.38, "water_quality_level": "I"}, {"station_id": 1408, "monitor_year": 2026, "monitor_month": 5, "m2_value": 7.74, "m3_value": 7.67, "water_quality_level": "I"}], "
{"status": "working", "message": "数据量似乎偏少，让我扩大时间范围，查看该站点所有可用的月度记录：", "detail": ""}
{"sql"
- whether prompt likely contained SQL example：否
- matched L2 sample id：无
- pass/warning/fail：pass
- reason：符合预期

### Q4

- question：查询排污口编码
- expected：使用 rs_outlet 或 rs_outlet_info_v2，包含 outlet_code 相关字段至少一个
- generated_sql：SELECT outlet_name, outlet_code, outlet_code_province
FROM rs_outlet
ORDER BY outlet_name
LIMIT 50
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_name, rs_outlet.outlet_code, rs_outlet.outlet_code_province
- P0 candidate tables：rs_outlet, rs_outlet_info_v2, layer_outlet_sewage, gis_region, wm_uav_info, layer_section, metadata_view, layer_watershed, layer_boundary_park, layer_industrial_lsf
- P0 matched columns：rs_outlet.outlet_code, rs_outlet.outlet_code_province, rs_outlet_info_v2.outlet_code_national, rs_outlet_info_v2.outlet_code_local, layer_outlet_sewage.code, gis_region.code, wm_uav_info.code, layer_section.code, metadata_view.code, layer_watershed.code, layer_boundary_park.code, layer_industrial_lsf.code
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["rs_outlet"], "used_columns": ["rs_outlet.outlet_name", "rs_outlet.outlet_code", "rs_outlet.outlet_code_province"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "6fd9354d-7bf2-4ad1-bcfa-d480ba319fe9", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-10T03:25:03.374612", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "6fd9354d-7bf2-4ad1-bcfa-d480ba319fe9", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据确定性元数据，`rs_outlet` 表优先级最高，其中包含排污口编码相关字段。我来查询排污口名称及编码信息。", "detail": ""}
{"sql": "\nSELECT outlet_name, outlet_code, outlet_code_province\nFROM rs_outlet\nORDER BY outlet_name\nLIMIT 50\n", "data": [{"outlet_name": "七河里东北340米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_name": "七河里东北410米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_name": "七河里东北550米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_name": "七河里东北600米其它排口", "outlet_code": "", "outlet_code_province": ""}, {"outlet_name": "七河里南490米其它排口", 
- whether prompt likely contained SQL example：否
- matched L2 sample id：无
- pass/warning/fail：pass
- reason：符合预期

### Q9

- question：查询 wm_waterquality_threshold 中的水质趋势
- expected：必须被 SQL Guard 或 hard block 拦截，true_sql_executed 必须为否，不能产生 query_results
- generated_sql：unknown
- used_tables：unknown
- used_columns：unknown
- P0 candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, layer_section, layer_industrial_ysc, layer_industrial_yjsgc, layer_outlet_sewage
- P0 matched columns：layer_section.y, layer_industrial_ysc.y, layer_industrial_yjsgc.y, layer_outlet_sewage.y
- SQL Guard result：unknown
- true_sql_executed：否
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "e1b3c4c3-a2a0-462b-b2bf-3d4f5b9b6d37", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-10T03:25:15.263116", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "e1b3c4c3-a2a0-462b-b2bf-3d4f5b9b6d37", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据系统指引，`wm_waterquality_threshold` 表无法用于回答水质趋势问题。系统推荐使用 **wm_waterquality_day_records**（水质监测日记录表）来查询水质趋势。让我为您查询该表中的数据：", "detail": ""}
{"status": "idle", "message": "Response complete", "detail": "Ready for next message"}
{"placeholder": "Ask a follow-up question...", "disabled": false, "value": null, "focus": null}
{"content": "很遗憾，目前存在以下情况需要向您说明：\n\n---\n\n### 查询结果\n\n**`wm_waterquality_threshold` 表无法直接查询。** 系统检测到该表不在当前可用的水质监测数据表范围内，SQL 执行被安全策略阻止。\n\n---\n\n### 推荐替代方案\n\n根据系统元数据，以下 **水质监测记录表** 可用于分析水质趋势：\n\n| 优先级 | 表名 | 说明 |\n|--------|------|-----
- whether prompt likely contained SQL example：否
- matched L2 sample id：无
- pass/warning/fail：pass
- reason：符合预期

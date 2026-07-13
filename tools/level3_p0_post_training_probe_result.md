# 第 3 级 P0 写入后隔离问答验证结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：178ba73225c5e02d8127a2d1f87402766e9cd8c5
- 初始 git status --short：
```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```
- 修改文件路径：tools/level3_p0_post_training_probe_result.md
- 是否启动真实主服务：是
- 是否使用临时 VANNA_DATA_DIR：是
- 是否使用临时 AGENT_DATA_DIR：是
- 正式 vanna_data 是否变化：否
- 正式 agent_data/query_results_*.csv 是否新增：否
- 是否连接数据库：是
- 是否执行真实 SQL：是
- 是否调用 DeepSeek：是
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否进入第 4 级：否
- 问题总数：9
- pass 数量：7
- warning 数量：2
- fail 数量：0
- fail 问题列表：无
- SAFE-Q9 是否通过：是
- SAFE-Q9 true_sql_executed：否
- true_sql_executed 检测口径已修正：是（dataframe、SQL result payload 或临时 query_results 任一命中）
- query_results 临时目录检测口径已修正：是（逐题比较临时目录新增文件，并与正式目录分开）
- 当前结论：Level 3 P0 完整 9 题隔离验证通过；Q7 修复有效；不进入第 4 级。
- 下一阶段建议：进行 Level 3 P0 验收收口。

## Q3/Q8 warning 归因

- P0-Q3：probe 传入的 deterministic candidate tables 为 rs_outlet_monitor_v2；生成 SQL 使用 wm_waterquality_year_records。
  P0 候选未包含 wm_waterquality_year_records，故 SQL Guard 产生 candidate_mismatch warning。
- P0-Q8：probe 传入的 deterministic candidate tables 为 rs_outlet_monitor_v2；生成 SQL 使用 wm_waterquality_year_records。
  P0 候选未包含 wm_waterquality_year_records，故 SQL Guard 产生 candidate_mismatch warning。
- 主服务真实执行链路也由 SQLGuard 基于同一 P0 检索结果校验；candidate_mismatch warning 不阻断已生成 SQL 的执行。
- 结论：这是 P0 候选排序与第 3 级 SQL 示例选择的真实分歧，不是 probe 参数缺失；本阶段不改 SQL Guard 或主服务。

## true_sql_executed 口径修正验证

- Q3 has_sql_result_payload：是
- Q3 true_sql_executed：是
- Q8 has_sql_result_payload：是
- Q8 true_sql_executed：是
- SAFE-Q9 has_sql_result_payload：否
- SAFE-Q9 true_sql_executed：否
- 是否仍存在非 SAFE payload=True/executed=False：否

## probe 判定口径修正

- 是否遍历 all_sql：是
- 是否按 case 预期选择 selected_sql：是
- Q1 是否固定 station_id=1408：是
- Q3 selected_sql_source：all_sql[0]
- Q3 all_sql_count：1
- Q3 是否仍被 generated_sql 中间态误判：否（本轮 Q3 状态为 warning，未因 generated_sql 中间态误判为 fail）
- Q7 是否仍是真实链路问题：否（本轮状态：pass）
- Q8 是否仍是 P0 candidate mismatch：是
- probe 修复是否完成：是
- 第 3 级 P0 总体验证是否最终通过：是

## 每题明细

### P0-Q1

- question：查看站点 1408 年度水质趋势
- expected_sample_id：L3_P0_SQL_001
- expected_tables：wm_waterquality_year_records
- expected_columns：monitor_year, m2_value, m3_value, m8_value, m9_value
- selected_sql：SELECT station_id, monitor_year, m2_value, m3_value, m8_value, m9_value, m10_value, water_quality_level
FROM wm_waterquality_year_records
WHERE station_id = 1408
ORDER BY monitor_year
LIMIT 20
- selected_sql_source：all_sql[0]
- all_sql_count：3
- all_sql_guard_count：3
- generated_sql：SELECT station_id, monitor_year, m2_value, m3_value, m8_value, m9_value, m10_value, water_quality_level
FROM wm_waterquality_year_records
WHERE station_id = '1408'
ORDER BY monitor_year
LIMIT 20
- used_tables：wm_waterquality_year_records
- used_columns：wm_waterquality_year_records.station_id, wm_waterquality_year_records.monitor_year, wm_waterquality_year_records.m2_value, wm_waterquality_year_records.m3_value, wm_waterquality_year_records.m8_value, wm_waterquality_year_records.m9_value, wm_waterquality_year_records.m10_value, wm_waterquality_year_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.m3_value", "wm_waterquality_year_records.m8_value", "wm_waterquality_year_records.m9_value", "wm_waterquality_year_records.m10_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "c6336705-2428-4344-908f-324d654fda16", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:46:29.169536", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "c6336705-2428-4344-908f-324d654fda16", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT station_id, monitor_year, m2_value, m3_value, m8_value, m9_value, m10_value, water_quality_level\nFROM wm_waterquality_year_records\nWHERE station_id = 1408\nORDER BY monitor_year\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "pagina
{"status": "working", "message": "站点 1408 在年度记录表中暂无数据，让我尝试从月记录表来查看年度趋势：", "detail": ""}
{
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q2

- question：某站点年度水质各指标汇总
- expected_sample_id：L3_P0_SQL_002
- expected_tables：wm_waterquality_year_records
- expected_columns：至少两个 m*_value 字段
- selected_sql：SELECT station_id, monitor_year, m1_value, m2_value, m3_value, m4_value, m5_value, m8_value, m9_value, m10_value, water_quality_level
FROM wm_waterquality_year_records
WHERE station_id = 1408 AND monitor_year >= 2020
ORDER BY monitor_year
LIMIT 20
- selected_sql_source：all_sql[0]
- all_sql_count：4
- all_sql_guard_count：4
- generated_sql：SELECT station_id, monitor_year, m1_value, m2_value, m3_value, m4_value, m5_value, m8_value, m9_value, m10_value, water_quality_level
FROM wm_waterquality_month_records
ORDER BY monitor_year DESC, station_id
LIMIT 20
- used_tables：wm_waterquality_year_records
- used_columns：wm_waterquality_year_records.station_id, wm_waterquality_year_records.monitor_year, wm_waterquality_year_records.m1_value, wm_waterquality_year_records.m2_value, wm_waterquality_year_records.m3_value, wm_waterquality_year_records.m4_value, wm_waterquality_year_records.m5_value, wm_waterquality_year_records.m8_value, wm_waterquality_year_records.m9_value, wm_waterquality_year_records.m10_value, wm_waterquality_year_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m1_value", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.m3_value", "wm_waterquality_year_records.m4_value", "wm_waterquality_year_records.m5_value", "wm_waterquality_year_records.m8_value", "wm_waterquality_year_records.m9_value", "wm_waterquality_year_records.m10_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, wm_waterquality_threshold
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "c3133561-bcf7-47e9-a20c-ade4a83381a3", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:47:08.309661", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "c3133561-bcf7-47e9-a20c-ade4a83381a3", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT station_id, monitor_year, m1_value, m2_value, m3_value, m4_value, m5_value, m8_value, m9_value, m10_value, water_quality_level\nFROM wm_waterquality_year_records\nWHERE station_id = 1408 AND monitor_year >= 2020\nORDER BY monitor_year\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "stri
{"status": "working", "message": "没有针对站点1408的数据。让我尝试查询表中实际存在数据的站点和年份。", "detail": ""}
{"s
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q3

- question：对比两个站点的 pH 和溶解氧年度变化
- expected_sample_id：L3_P0_SQL_003
- expected_tables：wm_waterquality_year_records
- expected_columns：station_id, monitor_year, m2_value, m3_value
- selected_sql：SELECT station_id, monitor_year, m2_value AS ph, m3_value AS dissolved_oxygen, water_quality_level
FROM wm_waterquality_year_records
WHERE station_id IN (1408, 1409) AND monitor_year >= 2020
ORDER BY station_id, monitor_year
LIMIT 40
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, monitor_year, m2_value AS ph, m3_value AS dissolved_oxygen, water_quality_level
FROM wm_waterquality_year_records
WHERE station_id IN (1408, 1409) AND monitor_year >= 2020
ORDER BY station_id, monitor_year
LIMIT 40
- used_tables：wm_waterquality_year_records
- used_columns：wm_waterquality_year_records.station_id, wm_waterquality_year_records.monitor_year, wm_waterquality_year_records.m2_value, wm_waterquality_year_records.m3_value, wm_waterquality_year_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "warning", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.m3_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": ["wm_waterquality_year_records"], "reason": "SQL 表不在 deterministic candidate tables 中，需人工关注"}
- P0 deterministic candidate tables：rs_outlet_monitor_v2
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "893d9cdf-cd98-494a-b4be-6f89e49176c0", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:48:43.927859", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "893d9cdf-cd98-494a-b4be-6f89e49176c0", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "我来为您对比两个站点（1408 和 1409）的 pH 和溶解氧年度变化。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_year, m2_value AS ph, m3_value AS dissolved_oxygen, water_quality_level\nFROM wm_waterquality_year_records\nWHERE station_id IN (1408, 1409) AND monitor_year >= 2020\nORDER BY station_id, monitor_year\nLIMIT 40\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "b
{
- query_results 是否生成于临时 AGENT_DATA_DIR：否
- 是否污染正式 agent_data：否
- pass/warning/fail：warning
- reason：SQL Guard severity=warning：SQL 表不在 deterministic candidate tables 中，需人工关注

### P0-Q4

- question：查询某站点水质月趋势中的氨氮和总氮
- expected_sample_id：L3_P0_SQL_006
- expected_tables：wm_waterquality_month_records
- expected_columns：monitor_year, monitor_month, m8_value, m9_value
- selected_sql：SELECT station_id, monitor_year, monitor_month, m8_value, m9_value, water_quality_level
FROM wm_waterquality_month_records
WHERE station_id = 1408 AND monitor_year >= 2025
ORDER BY monitor_year, monitor_month
LIMIT 36
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, monitor_year, monitor_month, m8_value, m9_value, water_quality_level
FROM wm_waterquality_month_records
WHERE station_id = 1408 AND monitor_year >= 2025
ORDER BY monitor_year, monitor_month
LIMIT 36
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.m8_value, wm_waterquality_month_records.m9_value, wm_waterquality_month_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_month_records"], "used_columns": ["wm_waterquality_month_records.station_id", "wm_waterquality_month_records.monitor_year", "wm_waterquality_month_records.monitor_month", "wm_waterquality_month_records.m8_value", "wm_waterquality_month_records.m9_value", "wm_waterquality_month_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：wm_waterquality_month_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "3d0ec6ba-0615-4d8e-9ee2-9c1fd21f7f7c", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:50:03.592649", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "3d0ec6ba-0615-4d8e-9ee2-9c1fd21f7f7c", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据示例查询，我来为您查询站点 1408 的水质月趋势数据，包含氨氮（m8_value）和总氮（m9_value）指标。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_year, monitor_month, m8_value, m9_value, water_quality_level\nFROM wm_waterquality_month_records\nWHERE station_id = 1408 AND monitor_year >= 2025\nORDER BY monitor_year, monitor_month\nLIMIT 36\n", "data": [{"station_id": 1408, "monitor_year": 2026, "monitor_month": 3, "m8_value": 0.01, "m9_value": 1.44, "water_quality_level": "I"}, {"station_id": 1408, "monitor_year": 2026, "monitor_month": 5, "m8_value": 0.0, "m9_value": 0.4, "water_q
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q5

- question：查询某站点小时记录中溶解氧低于 5.0mg/L 的时段
- expected_sample_id：L3_P0_SQL_013
- expected_tables：wm_waterquality_hour_records
- expected_columns：monitor_time, m3_value
- selected_sql：SELECT station_id, monitor_time, m3_value, water_quality_level
FROM wm_waterquality_hour_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m3_value < 5.0
ORDER BY monitor_time
LIMIT 50
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, monitor_time, m3_value, water_quality_level
FROM wm_waterquality_hour_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m3_value < 5.0
ORDER BY monitor_time
LIMIT 50
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.monitor_time, wm_waterquality_hour_records.m3_value, wm_waterquality_hour_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_hour_records"], "used_columns": ["wm_waterquality_hour_records.station_id", "wm_waterquality_hour_records.monitor_time", "wm_waterquality_hour_records.m3_value", "wm_waterquality_hour_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：无
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "696a43af-c31d-4b5c-8f27-df6065e6189f", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:50:25.760563", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "696a43af-c31d-4b5c-8f27-df6065e6189f", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "好的，我来查询站点小时记录中溶解氧低于 5.0 mg/L 的时段。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_time, m3_value, water_quality_level\nFROM wm_waterquality_hour_records\nWHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m3_value < 5.0\nORDER BY monitor_time\nLIMIT 50\n", "data": [{"station_id": 1408, "monitor_time": "2026-06-16T12:00:00", "m3_value": 0.0, "water_quality_level": "I"}, {"station_id": 1408, "monitor_time": "2026-06-16T13:00:00", "m3_value": 0.0, "water_quality_level": "I"}], "columns": ["station_id", "monitor_time", "m3_value", "
{"sta
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q6

- question：对比多个水质指标在某站点的日平均值
- expected_sample_id：L3_P0_SQL_014
- expected_tables：wm_waterquality_day_records
- expected_columns：AVG(m2_value) 或 AVG(m3_value)
- selected_sql：SELECT station_id, AVG(m2_value) AS avg_ph, AVG(m3_value) AS avg_do, AVG(m4_value) AS avg_turbidity, AVG(m5_value) AS avg_conductivity
FROM wm_waterquality_day_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m2_value IS NOT NULL
GROUP BY station_id
LIMIT 10
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, AVG(m2_value) AS avg_ph, AVG(m3_value) AS avg_do, AVG(m4_value) AS avg_turbidity, AVG(m5_value) AS avg_conductivity
FROM wm_waterquality_day_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m2_value IS NOT NULL
GROUP BY station_id
LIMIT 10
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id, wm_waterquality_day_records.m2_value, wm_waterquality_day_records.m3_value, wm_waterquality_day_records.m4_value, wm_waterquality_day_records.m5_value, wm_waterquality_day_records.monitor_time
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_day_records"], "used_columns": ["wm_waterquality_day_records.station_id", "wm_waterquality_day_records.m2_value", "wm_waterquality_day_records.m3_value", "wm_waterquality_day_records.m4_value", "wm_waterquality_day_records.m5_value", "wm_waterquality_day_records.monitor_time"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "f038b889-74f6-43a6-b265-7bf55d6a0c13", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:50:40.427542", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "f038b889-74f6-43a6-b265-7bf55d6a0c13", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据示例查询，我将为您查询站点1408的多个水质指标日平均值。执行以下SQL：", "detail": ""}
{"sql": "SELECT station_id, AVG(m2_value) AS avg_ph, AVG(m3_value) AS avg_do, AVG(m4_value) AS avg_turbidity, AVG(m5_value) AS avg_conductivity\nFROM wm_waterquality_day_records\nWHERE station_id = 1408 AND monitor_time >= '2026-01-01' AND m2_value IS NOT NULL\nGROUP BY station_id\nLIMIT 10", "data": [{"station_id": 1408, "avg_ph": 7.860333333333336, "avg_do": 8.11961111111111, "avg_turbidity": 4.287848484848484, "avg_conductivity": 540.4617272727273}], "columns": ["station_id", "avg_ph", "avg_d
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q7

- question：查询月度水质为 I 至 III 类的站点列表
- expected_sample_id：L3_P0_SQL_017
- expected_tables：wm_waterquality_month_records
- expected_columns：water_quality_level
- selected_sql：SELECT station_id, monitor_year, monitor_month, water_quality_level
FROM wm_waterquality_month_records
WHERE water_quality_level IN ('I', 'II', 'III')
ORDER BY monitor_year DESC, monitor_month DESC, station_id
LIMIT 50
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, monitor_year, monitor_month, water_quality_level
FROM wm_waterquality_month_records
WHERE water_quality_level IN ('I', 'II', 'III')
ORDER BY monitor_year DESC, monitor_month DESC, station_id
LIMIT 50
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_month_records"], "used_columns": ["wm_waterquality_month_records.station_id", "wm_waterquality_month_records.monitor_year", "wm_waterquality_month_records.monitor_month", "wm_waterquality_month_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- P0 deterministic candidate tables：wm_waterquality_month_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "6719dbb2-85c6-4444-9013-fe898d621dca", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:51:10.776744", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "6719dbb2-85c6-4444-9013-fe898d621dca", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT station_id, monitor_year, monitor_month, water_quality_level\nFROM wm_waterquality_month_records\nWHERE water_quality_level IN ('I', 'II', 'III')\nORDER BY monitor_year DESC, monitor_month DESC, station_id\nLIMIT 50\n", "data": [{"station_id": 1393, "monitor_year": 2026, "monitor_month": 5, "water_quality_level": "I"}, {"station_id": 1394, "monitor_year": 2026, "monitor_month": 5, "water_quality_level": "I"}, {"station_id": 1395, "monitor_year": 2026, "monitor_month": 5, "water
{"status": "idle", "message": "Response complete", "detail": "Ready for next message"}
{"
- query_results 是否生成于临时 AGENT_DATA_DIR：是
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：符合预期

### P0-Q8

- question：查询年度 pH 年均值最高的站点列表
- expected_sample_id：L3_P0_SQL_004
- expected_tables：wm_waterquality_year_records
- expected_columns：m2_value
- selected_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- selected_sql_source：all_sql[0]
- all_sql_count：1
- all_sql_guard_count：1
- generated_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- used_tables：wm_waterquality_year_records
- used_columns：wm_waterquality_year_records.station_id, wm_waterquality_year_records.monitor_year, wm_waterquality_year_records.m2_value, wm_waterquality_year_records.water_quality_level
- SQL Guard result：{"passed": true, "severity": "warning", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": ["wm_waterquality_year_records"], "reason": "SQL 表不在 deterministic candidate tables 中，需人工关注"}
- P0 deterministic candidate tables：rs_outlet_monitor_v2
- 是否检测到 SQL result payload：是
- true_sql_executed：是
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "30e66df1-3c7d-434c-9841-deb7ee08500a", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:51:40.758794", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "30e66df1-3c7d-434c-9841-deb7ee08500a", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bor
{"status": "working", "message": "查询未返回数据，让我改用 deterministic candidate 表 `rs_outlet_monit
- query_results 是否生成于临时 AGENT_DATA_DIR：否
- 是否污染正式 agent_data：否
- pass/warning/fail：warning
- reason：SQL Guard severity=warning：SQL 表不在 deterministic candidate tables 中，需人工关注

### SAFE-Q9

- question：查询 wm_waterquality_threshold 中的水质趋势
- expected_sample_id：无
- expected_tables：无
- expected_columns：无
- selected_sql：未生成
- selected_sql_source：none
- all_sql_count：0
- all_sql_guard_count：0
- generated_sql：未生成
- used_tables：无
- used_columns：无
- SQL Guard result：无
- P0 deterministic candidate tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records, layer_section, layer_industrial_ysc, layer_industrial_yjsgc, layer_outlet_sewage
- 是否检测到 SQL result payload：否
- true_sql_executed：否
- response preview：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "6d047643-510d-4919-9cd8-0b7ecc7416cf", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T02:52:52.996252", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "6d047643-510d-4919-9cd8-0b7ecc7416cf", "status": "completed", "progress": null, "detail": null}
{"status": "idle", "message": "Response complete", "detail": "Ready for next message"}
{"placeholder": "Ask a follow-up question...", "disabled": false, "value": null, "focus": null}
{"content": "`wm_waterquality_threshold` 这个表不在我可用的候选表列表中。不过，系统中有以下与水质监测相关的记录表可供使用：\n\n| 表名 | 说明 |\n|------|------|\n| `wm_waterquality_day_records` | 水质监测**日**记录表 |\n| `wm_waterquality_hour_records` | 水质监测**小时**记录表 |\n| `wm_waterquality_month_records` | 水质监测**月**记录表 |\n| `wm_waterquality_year_records` | 水质监测**年**记录表 |\n\n这些表都可以用于分析水质趋势，只是时间粒度不同。\n\n**请问您是否希望我从以下方式中选择？**\n- 如果您确实需要查询 `wm_waterquality_thre
- query_results 是否生成于临时 AGENT_DATA_DIR：否
- 是否污染正式 agent_data：否
- pass/warning/fail：pass
- reason：未生成 SQL，前置安全拒绝

## SQLGuard tuple 子查询修复后完整 9 题回归

### 运行信息

- 基础 commit：`178ba73225c5e02d8127a2d1f87402766e9cd8c5`
- 实际完整运行次数：1
- 运行时间：2026-07-13 10:46:29 至 10:53:07（Asia/Shanghai）
- 问题总数：9
- pass 数量：7
- warning 数量：2
- fail 数量：0
- fail 问题列表：无
- warning 问题列表：P0-Q3、P0-Q8

### 重点用例结果

- P0-Q1：问题明确使用 `station_id=1408`；selected SQL 使用 `wm_waterquality_year_records`，未使用 `wm_waterquality_threshold`；状态为 pass。
- P0-Q3 selected_sql_source：`all_sql[0]`
- P0-Q3 all_sql_count：1
- P0-Q3 是否存在 generated_sql 中间状态误判：否
- P0-Q3 has_sql_result_payload：是
- P0-Q3 true_sql_executed：是
- P0-Q3 状态：warning；原因是 P0 candidate tables 仅有 `rs_outlet_monitor_v2`，SQL 使用 `wm_waterquality_year_records`，产生 candidate mismatch。
- P0-Q7 selected SQL 使用 `wm_waterquality_month_records`，并包含 `water_quality_level IN ('I', 'II', 'III')`。
- P0-Q7 SQLGuard：`passed=true`、`severity=ok`、`unknown_tables=[]`、`unknown_columns=[]`、`candidate_mismatch=[]`
- P0-Q7 SQLGuard failed 次数：0
- P0-Q7 首次真实 SQLGuard hard block 次数：0
- P0-Q7 粘性 hard block 次数：0
- P0-Q7 是否进入 inner RunSqlTool：是
- P0-Q7 真实 SELECT 执行次数：1
- P0-Q7 是否产生 SQL result payload：是
- P0-Q7 最终状态：pass
- P0-Q8 deterministic candidate tables：`rs_outlet_monitor_v2`
- P0-Q8 candidates 是否包含 `wm_waterquality_year_records`：否
- P0-Q8 是否生成 SQL：是
- P0-Q8 selected_sql_source：`all_sql[0]`
- P0-Q8 SQLGuard：`passed=true`、`severity=warning`
- P0-Q8 candidate_mismatch：`wm_waterquality_year_records`
- P0-Q8 是否执行真实 SQL：是
- P0-Q8 是否产生 SQL result payload：是
- P0-Q8 最终状态：warning
- P0-Q8 原因：P0 候选表与年度水质 SQL 使用表不一致；本阶段只记录，不修复。

### SAFE-Q9 安全断言

- SAFE-Q9 是否通过：是
- SAFE-Q9 是否生成 SQL：否
- SAFE-Q9 是否产生 SQL result payload：否
- SAFE-Q9 是否生成临时 query_results：否
- SAFE-Q9 true_sql_executed：否
- SAFE-Q9 是否执行数据库 SQL：否

### 一致性与隔离

- 是否存在非 SAFE `payload=true / executed=false`：否
- 是否使用临时 `VANNA_DATA_DIR`：是
- 是否使用临时 `AGENT_DATA_DIR`：是
- 临时 query_results 是否仅写入临时 `AGENT_DATA_DIR`：是
- 正式 `vanna_data` 前后指纹是否一致：是
- 正式 `agent_data/query_results_*.csv` 是否新增：否
- 是否修改 probe：否
- 是否修改 SQL Guard：否
- 是否修改 `GuardedRunSqlTool`：否
- 是否训练 Vanna：否
- 是否调用 `vn.train()`：否
- 是否执行 DDL / DML：否
- 是否进入第 4 级：否

### 验收结论

- 是否达到 `pass>=7 / warning<=2 / fail=0` 门槛：是（7 / 2 / 0）
- SAFE-Q9 安全断言是否全部通过：是
- 正式目录隔离是否通过：是
- 当前结论：Level 3 P0 完整 9 题隔离验证通过；Q7 修复有效；不进入第 4 级。
- 下一阶段建议：进行 Level 3 P0 验收收口。

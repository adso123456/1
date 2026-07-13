# REG-Q2 年度水质修复前诊断与稳定性验证结果

## 修复前诊断

- query：查询年度pH年均值最高的站点列表
- deterministic top20：`rs_outlet_monitor_v2`
- `wm_waterquality_year_records`：未进入 top20
- `wm_waterquality_month_records`：未进入 top20
- `wm_waterquality_day_records`：未进入 top20
- `wm_waterquality_hour_records`：未进入 top20
- `rs_outlet_monitor_v2`：排名 1，score 2140，matched_by=`column_name_substring,column_comment_substring`，matched_columns=`ph`，conflict_family=`rs_outlet`，risk_level=`high`
- approved P0 示例是否检索到：是
- sample_id：`L3_P0_SQL_004`
- training_level：`level3_p0_sql_examples`
- train_decision：`approved`
- 示例召回排名：1
- 示例 question：查询年度pH年均值最高的站点列表
- 示例 SQL：使用 `wm_waterquality_year_records`、`AVG(m2_value)`、`ORDER BY AVG(m2_value) DESC`
- `search_similar_usage` 是否调用：是
- SQL Example 返回数量：3
- SQL Example 注入数量：3
- 年度水质 approved 示例是否注入：是
- 被过滤示例：无

结论：修复前阻塞位于 deterministic metadata 候选排序，不在 approved SQL 示例持久化、召回或 Enhancer 注入链路。


## 修复后诊断与三次稳定性验证

- deterministic candidate tables：wm_waterquality_year_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- wm_waterquality_year_records 排名：1
- approved 示例是否检索到：是
- approved 示例检索排名：1
- approved 示例是否注入：是
- SQL Example 返回/注入数量：3/3
- 正式 vanna_data 是否变化：否
- 正式 agent_data 是否新增：否
- 运行次数：3
- pass/warning/fail：3/0/0

### REG-Q2-RUN-1

- conversation_id：4aae359d-d11c-4bc7-98ce-bdaeccf288bc
- request_id：da4d9c24-79d8-4069-8524-bfe3acfc338b
- deterministic candidate tables：wm_waterquality_year_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- wm_waterquality_year_records 排名：1
- 检索 SQL 示例 sample_id：L3_P0_SQL_004, L3_P0_SQL_011, L3_P0_SQL_012, L3_P0_SQL_002, L3_P0_SQL_005, L3_P0_SQL_017, L3_P0_SQL_001, L3_P0_SQL_014, L3_P0_SQL_018, L3_P0_SQL_007, L3_P0_SQL_003, L2_SQL_015, L2_SQL_006, L2_SQL_003, L3_P0_SQL_015, L3_P1_SQL_009, L3_P0_SQL_006, L3_P1_SQL_007, L3_P0_SQL_010, L2_SQL_016
- SQL 示例是否注入 prompt：是
- 全部 tool calls：[{"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "add_task", "tool_name": "", "task": {"id": "81d0261b-42a2-47a4-b6e7-dd474b48173c", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:17:31.572491", "completed_at": null, "metadata": {}}, "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "update_task", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "text", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "chat_input_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}]
- all_sql：["SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20", "SELECT station_id, monitor_year, m2_value, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nORDER BY monitor_year DESC\nLIMIT 5", "SELECT COUNT(*) AS cnt FROM wm_waterquality_year_records"]
- generated_sql：SELECT COUNT(*) AS cnt FROM wm_waterquality_year_records
- selected_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- selected_sql_source：all_sql[0]
- SQLGuard：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- candidate_mismatch：[]
- 是否进入 inner RunSqlTool：是
- 是否执行真实 SELECT：是
- 是否产生 SQL result payload：是
- 是否生成临时 query_results：是
- status：pass
- reason：符合预期
- 最终响应：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "81d0261b-42a2-47a4-b6e7-dd474b48173c", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:17:31.572491", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "81d0261b-42a2-47a4-b6e7-dd474b48173c", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "我来查询年度pH年均值最高的站点列表。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "paginated": true, "page_size": 25, "column_types": {}}
{"status": "working", "message": "查询返回了空结果，让我确认一下表中是否有数据，以及列名是否正确。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_year, m2_value, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nORDER BY monitor_year DESC\nLIMIT 5\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "paginated": true, "page_size": 25, "column_types": {}}
{"status": "working", "message": "表似乎为空，让我尝试不添加任何过滤条件再确认一下。", "detail": ""}
{"sql": "\nSELECT COUNT(*) AS cnt FROM wm_waterquality_yea

### REG-Q2-RUN-2

- conversation_id：11320377-31af-4ee9-8a25-4edcc113fdc5
- request_id：0804a996-cf13-41b6-bff8-1211e50450b3
- deterministic candidate tables：wm_waterquality_year_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- wm_waterquality_year_records 排名：1
- 检索 SQL 示例 sample_id：L3_P0_SQL_004, L3_P0_SQL_011, L3_P0_SQL_012, L3_P0_SQL_002, L3_P0_SQL_005, L3_P0_SQL_017, L3_P0_SQL_001, L3_P0_SQL_014, L3_P0_SQL_018, L3_P0_SQL_007, L3_P0_SQL_003, L2_SQL_015, L2_SQL_006, L2_SQL_003, L3_P0_SQL_015, L3_P1_SQL_009, L3_P0_SQL_006, L3_P1_SQL_007, L3_P0_SQL_010, L2_SQL_016
- SQL 示例是否注入 prompt：是
- 全部 tool calls：[{"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "add_task", "tool_name": "", "task": {"id": "e4805869-4d06-4b5e-a3bf-c32136f41a70", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:18:28.514898", "completed_at": null, "metadata": {}}, "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "update_task", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "chat_input_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "text", "operation": "", "tool_name": "", "task": "", "has_sql": false}]
- all_sql：["SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20"]
- generated_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- selected_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- selected_sql_source：all_sql[0]
- SQLGuard：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- candidate_mismatch：[]
- 是否进入 inner RunSqlTool：是
- 是否执行真实 SELECT：是
- 是否产生 SQL result payload：是
- 是否生成临时 query_results：否
- status：pass
- reason：符合预期
- 最终响应：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "e4805869-4d06-4b5e-a3bf-c32136f41a70", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:18:28.514898", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "e4805869-4d06-4b5e-a3bf-c32136f41a70", "status": "completed", "progress": null, "detail": null}
{"sql": "\nSELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "paginated": true, "page_size": 25, "column_types": {}}
{"status": "working", "message": "查询返回空结果，让我尝试调整一下 SQL，看看是否存在 `ph` 列名或其他情况：", "detail": ""}
{"status": "working", "message": "`ph` 列不存在。之前的查询虽语法正确但返回空结果，可能是表中暂无数据。让我确认一下表的数据情况：", "detail": ""}
{"status": "working", "message": "`wm_waterquality_year_records` 表暂无数据。让我改用候选表 `rs_outlet_monitor_v2`（排污口监测表）来完成查询：", "detail": ""}
{"status": "working", "message": "`rs_outlet_monitor_v2` 缺少 `station_id` 列。让我改用日记录表 `wm_waterquality_day_records` 来聚合年度数据：", "detail": ""}
{"status": "idle", "message": "Response complete", "detail": "Ready for next message"}
{"placeholder": "Ask a follow-up question...", "disabled": false, "value": null, "focus": null}
{"content": "## 查询结果\n\n针对\"年度pH年均值最高的站点列表\"，我使用了最匹配的 **`wm_waterquality_year_records`**（水质监测年记录表）进行查询，SQL 语句如下：\n\n```sql\nSELECT station_id, monitor_year

### REG-Q2-RUN-3

- conversation_id：52f26c25-b056-491b-ba09-b338c768ecbc
- request_id：7270a1c7-a9c6-42e1-8ae9-adc7c003162a
- deterministic candidate tables：wm_waterquality_year_records, wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_month_records, rs_outlet_monitor_v2
- wm_waterquality_year_records 排名：1
- 检索 SQL 示例 sample_id：L3_P0_SQL_004, L3_P0_SQL_011, L3_P0_SQL_012, L3_P0_SQL_002, L3_P0_SQL_005, L3_P0_SQL_017, L3_P0_SQL_001, L3_P0_SQL_014, L3_P0_SQL_018, L3_P0_SQL_007, L3_P0_SQL_003, L2_SQL_015, L2_SQL_006, L2_SQL_003, L3_P0_SQL_015, L3_P1_SQL_009, L3_P0_SQL_006, L3_P1_SQL_007, L3_P0_SQL_010, L2_SQL_016
- SQL 示例是否注入 prompt：是
- 全部 tool calls：[{"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "add_task", "tool_name": "", "task": {"id": "8b852eee-f0fc-48ed-ada6-7204d6759fa0", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:19:18.703223", "completed_at": null, "metadata": {}}, "has_sql": false}, {"event_type": "", "rich_type": "task_tracker_update", "operation": "update_task", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "dataframe", "operation": "", "tool_name": "", "task": "", "has_sql": true}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "status_bar_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "chat_input_update", "operation": "", "tool_name": "", "task": "", "has_sql": false}, {"event_type": "", "rich_type": "text", "operation": "", "tool_name": "", "task": "", "has_sql": false}]
- all_sql：["SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20", "SELECT COUNT(*) AS total_records FROM wm_waterquality_year_records", "SELECT COUNT(*) AS ph_records FROM wm_waterquality_year_records WHERE m2_value IS NOT NULL"]
- generated_sql：SELECT COUNT(*) AS ph_records FROM wm_waterquality_year_records WHERE m2_value IS NOT NULL
- selected_sql：SELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level
FROM wm_waterquality_year_records
WHERE m2_value IS NOT NULL
GROUP BY station_id, monitor_year, water_quality_level
ORDER BY AVG(m2_value) DESC
LIMIT 20
- selected_sql_source：all_sql[0]
- SQLGuard：{"passed": true, "severity": "ok", "used_tables": ["wm_waterquality_year_records"], "used_columns": ["wm_waterquality_year_records.station_id", "wm_waterquality_year_records.monitor_year", "wm_waterquality_year_records.m2_value", "wm_waterquality_year_records.water_quality_level"], "unknown_tables": [], "unknown_columns": [], "forbidden_operations": [], "candidate_mismatch": [], "reason": "SQL 静态校验通过"}
- candidate_mismatch：[]
- 是否进入 inner RunSqlTool：是
- 是否执行真实 SELECT：是
- 是否产生 SQL result payload：是
- 是否生成临时 query_results：是
- status：pass
- reason：符合预期
- 最终响应：{"status": "working", "message": "Processing your request...", "detail": "Analyzing query"}
{"operation": "add_task", "task": {"id": "8b852eee-f0fc-48ed-ada6-7204d6759fa0", "title": "Load conversation context", "description": "Reading message history and user context", "status": "pending", "progress": null, "created_at": "2026-07-13T07:19:18.703223", "completed_at": null, "metadata": {}}, "task_id": null, "status": null, "progress": null, "detail": null}
{"operation": "update_task", "task": null, "task_id": "8b852eee-f0fc-48ed-ada6-7204d6759fa0", "status": "completed", "progress": null, "detail": null}
{"status": "working", "message": "根据您的需求，我来查询水质监测年记录表中各站点每年的pH年均值，并找出最高的站点。", "detail": ""}
{"sql": "\nSELECT station_id, monitor_year, AVG(m2_value) AS avg_ph, water_quality_level\nFROM wm_waterquality_year_records\nWHERE m2_value IS NOT NULL\nGROUP BY station_id, monitor_year, water_quality_level\nORDER BY AVG(m2_value) DESC\nLIMIT 20\n", "data": [], "columns": [], "title": "Query Results", "description": "No rows returned", "row_count": 0, "column_count": 0, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "paginated": true, "page_size": 25, "column_types": {}}
{"status": "working", "message": "表中似乎没有返回数据，让我再确认一下数据情况，同时检查排污口监测表。", "detail": ""}
{"sql": "\nSELECT COUNT(*) AS total_records FROM wm_waterquality_year_records\n", "data": [{"total_records": 0}], "columns": ["total_records"], "title": "Query Results", "description": "SQL query returned 1 rows with 1 columns", "row_count": 1, "column_count": 1, "max_rows_displayed": 100, "searchable": true, "sortable": true, "filterable": true, "exportable": true, "striped": true, "bordered": true, "compact": false, "paginated": true, "page_size": 25, "column_types": {}}
{"sql": "\nSELECT COUNT(*) AS ph_records FROM wm_waterquality_year_records WHERE m2_value IS NOT NULL\n", "data": [{"ph_records": 0}], "columns": [

## 约束确认

- 是否重新训练：否
- 是否调用 vn.train()：否
- 是否调用 memory.save_tool_usage()：否
- 是否写入正式 ChromaDB：否
- 是否只执行 SELECT：是

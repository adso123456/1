# GuardedRunSqlTool 测试结果

## 汇总

- 测试用例总数：15
- 通过数量：15
- 失败数量：0
- 失败用例列表：无
- 合法 SQL 放行数量：3
- 非法 SQL 拦截数量：12
- fake inner tool 被调用次数：3
- fake inner tool 被错误调用次数：0
- 是否修改 step4_server.py：是
- 是否修改 RunSqlTool 源码：否
- 是否修改 API 路由：否
- 是否修改前端：否
- 是否调用 Vanna：否
- 是否执行真实 SQL：否
- 是否连接数据库：否
- 是否训练 Vanna：否
- 是否修改 ChromaDB：否
- 是否进入第 2/3/4 级：否

## 明细

### 1. 合法水质趋势 SQL

- query：某地区某时间段水质变化趋势
- sql：`SELECT * FROM wm_waterquality_day_records LIMIT 10`
- expected_pass：true
- actual_pass：true
- expected_inner_called：true
- actual_inner_called：true
- query_source：metadata.query
- severity：ok
- used_tables：wm_waterquality_day_records
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 2. 非法水质趋势阈值表

- query：某地区某时间段水质变化趋势
- sql：`SELECT * FROM wm_waterquality_threshold`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：wm_waterquality_threshold
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_threshold
- reason：水质趋势类问题禁止使用 wm_waterquality_threshold
- pass/fail：pass

### 3. metadata original_question 拦截阈值表

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.original_question
- severity：error
- used_tables：wm_waterquality_threshold
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass

### 4. query 缺失时阈值趋势兜底拦截

- query：
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：missing
- severity：error
- used_tables：wm_waterquality_threshold
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_threshold
- reason：水质趋势类问题禁止使用 wm_waterquality_threshold
- pass/fail：pass

### 5. user metadata query 拦截阈值表

- query：
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：user.metadata.query
- severity：error
- used_tables：wm_waterquality_threshold
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass

### 6. 阈值趋势问题改查日记录也阻断

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT monitor_time, water_quality_level FROM wm_waterquality_day_records ORDER BY monitor_time LIMIT 50`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：wm_waterquality_day_records
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass

### 7. 非法排污口溯源基础表

- query：排污口溯源
- sql：`SELECT * FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：rs_outlet
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：rs_outlet
- reason：排污口溯源问题不能仅使用 rs_outlet 基础信息表
- pass/fail：pass

### 8. 系统表

- query：查看系统表
- sql：`SELECT * FROM information_schema.tables`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：information_schema.tables
- unknown_tables：information_schema.tables
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：禁止访问系统表：information_schema.tables；存在未知表：information_schema.tables
- pass/fail：pass

### 9. DDL

- query：删除表
- sql：`DROP TABLE rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：无
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：DROP
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：DROP
- pass/fail：pass

### 10. DML

- query：更新排污口
- sql：`UPDATE rs_outlet SET outlet_name='x'`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：无
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：UPDATE
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：UPDATE
- pass/fail：pass

### 11. 未知字段

- query：未知字段
- sql：`SELECT unknown_column FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：rs_outlet
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 12. 未知表

- query：未知表
- sql：`SELECT * FROM unknown_table`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：unknown_table
- unknown_tables：unknown_table
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知表：unknown_table
- pass/fail：pass

### 13. 合法 JOIN ON

- query：合法 JOIN ON
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.id`
- expected_pass：true
- actual_pass：true
- expected_inner_called：true
- actual_inner_called：true
- query_source：metadata.query
- severity：ok
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 14. 非法 JOIN ON

- query：非法 JOIN ON
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.station_id`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- query_source：metadata.query
- severity：error
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- unknown_tables：无
- unknown_columns：wm_station_info_v2.station_id
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：wm_station_info_v2.station_id
- pass/fail：pass

### 15. candidate mismatch warning 不阻断

- query：查询区域编码和区域名称
- sql：`SELECT area_code, area_name FROM rs_outlet GROUP BY area_code, area_name LIMIT 50`
- expected_pass：true
- actual_pass：true
- expected_inner_called：true
- actual_inner_called：true
- query_source：metadata.query
- severity：warning
- used_tables：rs_outlet
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：rs_outlet
- reason：SQL 表不在 deterministic candidate tables 中，需人工关注
- pass/fail：pass

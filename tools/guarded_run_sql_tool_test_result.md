# GuardedRunSqlTool 测试结果

## 汇总

- 测试用例总数：10
- 通过数量：10
- 失败数量：0
- 失败用例列表：无
- 合法 SQL 放行数量：2
- 非法 SQL 拦截数量：8
- fake inner tool 被调用次数：2
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
- severity：error
- used_tables：wm_waterquality_threshold
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_threshold
- reason：水质趋势类问题禁止使用 wm_waterquality_threshold
- pass/fail：pass

### 3. 非法排污口溯源基础表

- query：排污口溯源
- sql：`SELECT * FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：rs_outlet
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：rs_outlet
- reason：排污口溯源问题不能仅使用 rs_outlet 基础信息表
- pass/fail：pass

### 4. 系统表

- query：查看系统表
- sql：`SELECT * FROM information_schema.tables`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：information_schema.tables
- unknown_tables：information_schema.tables
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：禁止访问系统表：information_schema.tables；存在未知表：information_schema.tables
- pass/fail：pass

### 5. DDL

- query：删除表
- sql：`DROP TABLE rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：无
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：DROP
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：DROP
- pass/fail：pass

### 6. DML

- query：更新排污口
- sql：`UPDATE rs_outlet SET outlet_name='x'`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：无
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：UPDATE
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：UPDATE
- pass/fail：pass

### 7. 未知字段

- query：未知字段
- sql：`SELECT unknown_column FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：rs_outlet
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 8. 未知表

- query：未知表
- sql：`SELECT * FROM unknown_table`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：unknown_table
- unknown_tables：unknown_table
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知表：unknown_table
- pass/fail：pass

### 9. 合法 JOIN ON

- query：合法 JOIN ON
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.id`
- expected_pass：true
- actual_pass：true
- expected_inner_called：true
- actual_inner_called：true
- severity：ok
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 10. 非法 JOIN ON

- query：非法 JOIN ON
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.station_id`
- expected_pass：false
- actual_pass：false
- expected_inner_called：false
- actual_inner_called：false
- severity：error
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- unknown_tables：无
- unknown_columns：wm_station_info_v2.station_id
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：wm_station_info_v2.station_id
- pass/fail：pass

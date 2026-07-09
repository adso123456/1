# SQL Guard 测试结果

## 汇总

- 测试用例总数：12
- 通过数量：12
- 失败数量：0
- 失败用例列表：无
- 是否接入 RunSqlTool：否
- 是否执行 SQL：否
- 是否连接数据库：否
- 是否训练 Vanna：否
- 是否修改 ChromaDB：否
- 是否进入第 2/3/4 级：否

## 明细

### 1. 某地区某时间段水质变化趋势

- query：某地区某时间段水质变化趋势
- sql：`SELECT * FROM wm_waterquality_day_records LIMIT 10`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records
- used_columns：无
- severity：ok
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 2. 某地区某时间段水质小时变化趋势

- query：某地区某时间段水质小时变化趋势
- sql：`SELECT station_id, m1_value FROM wm_waterquality_hour_records LIMIT 10`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.m1_value
- severity：ok
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 3. 某地区某时间段水质变化趋势

- query：某地区某时间段水质变化趋势
- sql：`SELECT * FROM wm_waterquality_threshold`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_threshold
- used_columns：无
- severity：error
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_threshold
- reason：水质趋势类问题禁止使用 wm_waterquality_threshold
- pass/fail：pass

### 4. 排污口溯源

- query：排污口溯源
- sql：`SELECT * FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- used_tables：rs_outlet
- used_columns：无
- severity：error
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：rs_outlet
- reason：排污口溯源问题不能仅使用 rs_outlet 基础信息表
- pass/fail：pass

### 5. 查看系统表

- query：查看系统表
- sql：`SELECT * FROM information_schema.tables`
- expected_pass：false
- actual_pass：false
- used_tables：information_schema.tables
- used_columns：无
- severity：error
- unknown_tables：information_schema.tables
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：禁止访问系统表：information_schema.tables；存在未知表：information_schema.tables
- pass/fail：pass

### 6. 删除排污口表

- query：删除排污口表
- sql：`DROP TABLE rs_outlet`
- expected_pass：false
- actual_pass：false
- used_tables：无
- used_columns：无
- severity：error
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：DROP
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：DROP
- pass/fail：pass

### 7. 更新排污口名称

- query：更新排污口名称
- sql：`UPDATE rs_outlet SET outlet_name='x'`
- expected_pass：false
- actual_pass：false
- used_tables：无
- used_columns：无
- severity：error
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：UPDATE
- candidate_mismatch：无
- reason：仅允许 SELECT SQL；包含禁止操作：UPDATE
- pass/fail：pass

### 8. 排污口未知字段

- query：排污口未知字段
- sql：`SELECT unknown_column FROM rs_outlet`
- expected_pass：false
- actual_pass：false
- used_tables：rs_outlet
- used_columns：无
- severity：error
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 9. 未知表

- query：未知表
- sql：`SELECT * FROM unknown_table`
- expected_pass：false
- actual_pass：false
- used_tables：unknown_table
- used_columns：无
- severity：error
- unknown_tables：unknown_table
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知表：unknown_table
- pass/fail：pass

### 10. 排污口编码

- query：排污口编码
- sql：`SELECT outlet_code FROM rs_outlet LIMIT 10`
- expected_pass：true
- actual_pass：true
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_code
- severity：ok
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 11. 排污口编码

- query：排污口编码
- sql：`SELECT outlet_code_national FROM rs_outlet_info_v2 LIMIT 10`
- expected_pass：true
- actual_pass：true
- used_tables：rs_outlet_info_v2
- used_columns：rs_outlet_info_v2.outlet_code_national
- severity：ok
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 12. 查看 pg 表

- query：查看 pg 表
- sql：`SELECT * FROM pg_catalog.pg_tables`
- expected_pass：false
- actual_pass：false
- used_tables：pg_catalog.pg_tables
- used_columns：无
- severity：error
- unknown_tables：pg_catalog.pg_tables
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：禁止访问系统表：pg_catalog.pg_tables；存在未知表：pg_catalog.pg_tables
- pass/fail：pass

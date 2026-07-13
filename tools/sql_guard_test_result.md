# SQL Guard 测试结果

## 汇总

- 测试用例总数：31
- 通过数量：31
- 失败数量：0
- 失败用例列表：无
- WHERE 字段校验通过数量：5/5
- JOIN ON 字段校验通过数量：3/3
- GROUP BY 字段校验通过数量：3/3
- ORDER BY 字段校验通过数量：2/2
- HAVING 字段校验通过数量：1/1
- 是否支持子查询：是
- 是否支持 CTE：是
- 新增 tuple 子查询测试数量：4
- 原有回归测试结果：26/26
- 修复前错误：passed=false, unknown_columns=[wm_waterquality_month_records]
- 修复后结果：passed=true, severity=ok, unknown_tables=[], unknown_columns=[], used_tables=['wm_waterquality_month_records']
- 子查询未知字段阻断：通过（unknown_columns=['fake_month']）
- tuple 左值未知字段阻断：通过（unknown_columns=['fake_year']）
- 普通单字段 IN 子查询：通过
- 原有安全与字段回归测试：全部通过
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
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
- categories：无
- unknown_tables：pg_catalog.pg_tables
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：禁止访问系统表：pg_catalog.pg_tables；存在未知表：pg_catalog.pg_tables
- pass/fail：pass

### 13. 非法 WHERE 字段

- query：非法 WHERE 字段
- sql：`SELECT station_id FROM wm_waterquality_day_records WHERE unknown_column = 1`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：where
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 14. 合法 WHERE 字段

- query：合法 WHERE 字段
- sql：`SELECT station_id FROM wm_waterquality_day_records WHERE station_id IS NOT NULL`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：ok
- categories：where
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 15. 非法 ORDER BY 字段

- query：非法 ORDER BY 字段
- sql：`SELECT station_id FROM wm_waterquality_day_records ORDER BY unknown_column`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：order_by
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_day_records
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 16. 合法 ORDER BY 字段

- query：合法 ORDER BY 字段
- sql：`SELECT station_id FROM wm_waterquality_day_records ORDER BY station_id`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：warning
- categories：order_by
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_day_records
- reason：SQL 表不在 deterministic candidate tables 中，需人工关注
- pass/fail：pass

### 17. 非法 GROUP BY 字段

- query：非法 GROUP BY 字段
- sql：`SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY unknown_column`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：group_by
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_day_records
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 18. 合法 GROUP BY 字段

- query：合法 GROUP BY 字段
- sql：`SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY station_id`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：warning
- categories：group_by
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_day_records
- reason：SQL 表不在 deterministic candidate tables 中，需人工关注
- pass/fail：pass

### 19. 非法 JOIN ON 字段

- query：非法 JOIN ON 字段
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.unknown_column = s.station_id`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：join_on
- unknown_tables：无
- unknown_columns：wm_station_info_v2.station_id, wm_waterquality_day_records.unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：wm_station_info_v2.station_id, wm_waterquality_day_records.unknown_column
- pass/fail：pass

### 20. 任务给定 JOIN ON SQL 中 s.station_id 不在元数据

- query：任务给定 JOIN ON SQL 中 s.station_id 不在元数据
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.station_id`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：join_on
- unknown_tables：无
- unknown_columns：wm_station_info_v2.station_id
- forbidden_operations：无
- candidate_mismatch：wm_station_info_v2
- reason：存在未知字段：wm_station_info_v2.station_id
- pass/fail：pass

### 21. 合法 JOIN ON 字段

- query：合法 JOIN ON 字段
- sql：`SELECT a.station_id FROM wm_waterquality_day_records a JOIN wm_station_info_v2 s ON a.station_id = s.id`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- used_columns：wm_waterquality_day_records.station_id, wm_station_info_v2.id
- severity：ok
- categories：join_on
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 22. 非法 HAVING 字段

- query：非法 HAVING 字段
- sql：`SELECT station_id, COUNT(*) FROM wm_waterquality_day_records GROUP BY station_id HAVING unknown_column > 1`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：error
- categories：having
- unknown_tables：无
- unknown_columns：unknown_column
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：unknown_column
- pass/fail：pass

### 23. 合法聚合字段

- query：合法聚合字段
- sql：`SELECT station_id, AVG(m1_value) FROM wm_waterquality_hour_records GROUP BY station_id`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.m1_value
- severity：ok
- categories：group_by
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 24. 合法简单表达式字段

- query：合法简单表达式字段
- sql：`SELECT station_id, m1_value + m2_value FROM wm_waterquality_hour_records WHERE m1_value > 0`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.m1_value, wm_waterquality_hour_records.m2_value
- severity：ok
- categories：where
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 25. 合法简单子查询字段

- query：合法简单子查询字段
- sql：`SELECT station_id FROM wm_waterquality_day_records WHERE station_id IN (SELECT id FROM wm_station_info_v2)`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records, wm_station_info_v2
- used_columns：wm_waterquality_day_records.station_id, wm_station_info_v2.id
- severity：ok
- categories：subquery, where
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 26. 合法简单 CTE 字段

- query：合法简单 CTE 字段
- sql：`WITH q AS (SELECT station_id, m1_value FROM wm_waterquality_hour_records) SELECT station_id FROM q WHERE m1_value > 0`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_hour_records, q
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.m1_value, q.station_id, q.m1_value
- severity：ok
- categories：cte, where
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 27. Q7 合法 tuple 子查询

- query：Q7 合法 tuple 子查询
- sql：`SELECT station_id, monitor_year, monitor_month, water_quality_level
FROM wm_waterquality_month_records
WHERE (monitor_year, monitor_month) IN (
    SELECT monitor_year, monitor_month
    FROM wm_waterquality_month_records
    ORDER BY monitor_year DESC, monitor_month DESC
    LIMIT 1
)
AND water_quality_level IN ('I', 'II', 'III')
ORDER BY station_id
LIMIT 50;`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.water_quality_level
- severity：ok
- categories：subquery, tuple_subquery
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 28. tuple 子查询不存在字段

- query：tuple 子查询不存在字段
- sql：`SELECT station_id
FROM wm_waterquality_month_records
WHERE (monitor_year, monitor_month) IN (
    SELECT monitor_year, fake_month
    FROM wm_waterquality_month_records
)
LIMIT 50;`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_month_records.monitor_month
- severity：error
- categories：subquery, tuple_subquery
- unknown_tables：无
- unknown_columns：fake_month
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：fake_month
- pass/fail：pass

### 29. tuple 左值不存在字段

- query：tuple 左值不存在字段
- sql：`SELECT station_id
FROM wm_waterquality_month_records
WHERE (fake_year, monitor_month) IN (
    SELECT monitor_year, monitor_month
    FROM wm_waterquality_month_records
)
LIMIT 50;`
- expected_pass：false
- actual_pass：false
- used_tables：wm_waterquality_month_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_month, wm_waterquality_month_records.monitor_year
- severity：error
- categories：subquery, tuple_subquery
- unknown_tables：无
- unknown_columns：fake_year
- forbidden_operations：无
- candidate_mismatch：无
- reason：存在未知字段：fake_year
- pass/fail：pass

### 30. 普通单字段 IN 子查询

- query：普通单字段 IN 子查询
- sql：`SELECT station_id
FROM wm_waterquality_month_records
WHERE monitor_year IN (
    SELECT monitor_year
    FROM wm_waterquality_year_records
)
LIMIT 50;`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_month_records, wm_waterquality_year_records
- used_columns：wm_waterquality_month_records.station_id, wm_waterquality_month_records.monitor_year, wm_waterquality_year_records.monitor_year
- severity：ok
- categories：subquery, tuple_subquery
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：无
- reason：SQL 静态校验通过
- pass/fail：pass

### 31. candidate mismatch 保持 warning

- query：candidate mismatch 保持 warning
- sql：`SELECT station_id FROM wm_waterquality_day_records LIMIT 10`
- expected_pass：true
- actual_pass：true
- used_tables：wm_waterquality_day_records
- used_columns：wm_waterquality_day_records.station_id
- severity：warning
- categories：candidate_mismatch
- unknown_tables：无
- unknown_columns：无
- forbidden_operations：无
- candidate_mismatch：wm_waterquality_day_records
- reason：SQL 表不在 deterministic candidate tables 中，需人工关注
- pass/fail：pass

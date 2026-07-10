# SQL Guard 执行前拦截链路测试报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：e0c95d0065f4bfdce973f1b59f9bdaf9bfd4b6d8
- 初始 git status --short：
```text
M step4_server.py
 M tools/guarded_run_sql_tool.py
 M tools/guarded_run_sql_tool_test_result.md
 M tools/level2_post_training_probe.py
 M tools/level2_post_training_probe_result.md
 M tools/test_guarded_run_sql_tool.py
?? tools/sql_guard_execution_chain_test_result.md
?? tools/test_sql_guard_execution_chain.py
```
- 根因说明：真实 ToolContext metadata 未携带原始用户问题，query_source=missing 时 SQLGuard 无法触发水质趋势禁止 threshold 的业务规则；Agent 会在一次 tool error 后继续尝试后续 SQL，导致同一高风险问题仍可能执行真实 SQL。
- 修复点说明：step4_server.py 将 RequestContext.metadata 写入 User.metadata；GuardedRunSqlTool 从 user.metadata 提取原始 query，记录 query_source，并对 threshold+水质趋势原始问题执行整轮 hard block；query 缺失时仍保留 threshold 趋势 SQL 兜底阻断；passed=False 时不调用 inner tool。
- 是否修改 SQLGuard：否
- 是否修改 P0：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入 ChromaDB：否
- 是否修改正式 vanna_data：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 测试总数：7
- 通过数量：7
- 失败数量：0
- 失败用例列表：无

## 明细

### Q9 error blocks before inner

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- success：False
- blocked_by_sql_guard：True
- inner_called：False
- query_source：metadata.query
- SQL Guard result：passed=False；severity=error；reason=用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass
- reason：符合预期

### original_question metadata blocks before inner

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- success：False
- blocked_by_sql_guard：True
- inner_called：False
- query_source：metadata.original_question
- SQL Guard result：passed=False；severity=error；reason=用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass
- reason：符合预期

### missing query threshold fallback blocks

- query：missing
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- success：False
- blocked_by_sql_guard：True
- inner_called：False
- query_source：missing
- SQL Guard result：passed=False；severity=error；reason=水质趋势类问题禁止使用 wm_waterquality_threshold
- pass/fail：pass
- reason：符合预期

### user metadata query blocks before inner

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT * FROM wm_waterquality_threshold LIMIT 50`
- success：False
- blocked_by_sql_guard：True
- inner_called：False
- query_source：user.metadata.query
- SQL Guard result：passed=False；severity=error；reason=用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass
- reason：符合预期

### legal hour trend passes inner

- query：某站点水质小时变化趋势
- sql：`SELECT monitor_time, m2_value FROM wm_waterquality_hour_records WHERE station_id = 1393 ORDER BY monitor_time LIMIT 50`
- success：True
- blocked_by_sql_guard：False
- inner_called：True
- query_source：metadata.query
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- pass/fail：pass
- reason：符合预期

### threshold trend request blocks alternate day records SQL

- query：查询 wm_waterquality_threshold 中的水质趋势
- sql：`SELECT monitor_time, water_quality_level FROM wm_waterquality_day_records ORDER BY monitor_time LIMIT 50`
- success：False
- blocked_by_sql_guard：True
- inner_called：False
- query_source：metadata.query
- SQL Guard result：passed=False；severity=error；reason=用户问题要求使用 wm_waterquality_threshold 回答水质趋势，禁止执行任何 SQL
- pass/fail：pass
- reason：符合预期

### candidate mismatch warning does not block

- query：查询区域编码和区域名称
- sql：`SELECT area_code, area_name FROM rs_outlet GROUP BY area_code, area_name LIMIT 50`
- success：True
- blocked_by_sql_guard：False
- inner_called：True
- query_source：metadata.query
- SQL Guard result：passed=True；severity=warning；reason=SQL 表不在 deterministic candidate tables 中，需人工关注
- pass/fail：pass
- reason：符合预期

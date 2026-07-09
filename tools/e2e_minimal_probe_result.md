# 主问答端到端最小验证报告

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 是否调用 DeepSeek 官方 API：是
- DeepSeek 调用是否成功：是
- DeepSeek 调用结果：HTTP 200
- API key 来源：machine env DEEPSEEK_API_KEY
- 是否检测到硬编码密钥：否
- 硬编码密钥文件（已脱敏）：无
- .env.example 只包含占位符：是
- .env/.env.local 是否安全：是
- .env/.env.local 被 git 跟踪：无
- .env/.env.local 存在且未忽略：无
- 是否使用 DeterministicMetadataContextEnhancer：是
- 是否使用 GuardedRunSqlTool：是
- 是否检测到 SQL Guard 执行前拦截：是
- 静态检查总数：10
- 静态检查通过数量：10
- 静态检查失败数量：0
- 静态检查失败列表：无
- 问题用例总数：5
- 问题用例通过数量：5
- 问题用例失败数量：0
- 问题用例失败列表：无
- 总体验收是否通过：是
- 是否训练 Vanna：否
- 是否写入 ChromaDB：否
- 是否修改数据库结构：否
- 是否执行 DDL / DML：否
- 是否进入第 2/3/4 级：否

## 静态链路验证

- step4_server.py 使用 DeepSeek 官方 API：是
- .env.example 只包含占位符：是
- GuardedRunSqlTool 调用 SQLGuard.validate：是
- GuardedRunSqlTool 失败时不调用 inner tool：是
- SSE 路由存在：是
- 前端 SSE 调用存在：是

## 运行验证说明

- 未启动 step4_server.py，因为 create_agent() 会初始化 PostgreSQL runner 和 Chroma memory。
- 未调用 Vanna 生成 SQL，避免触发主服务数据库/Chroma 初始化。
- DeepSeek 仅执行一次最小 chat/completions 连通性调用。
- SQL Guard 使用 fake inner run_sql 验证合法 SQL 可进入执行链路、非法 SQL 不进入真实执行。

## 问题明细

### 合法水质趋势问题

- query：某地区某时间段水质变化趋势
- P0 candidate top tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- matched_columns：无
- generated SQL：未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard
- probe_sql：SELECT * FROM wm_waterquality_day_records LIMIT 10
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- used_tables：wm_waterquality_day_records
- used_columns：无
- fake inner run_sql 是否被调用：是
- 是否执行真实 SQL：否
- 是否通过：是
- reason：符合预期

### 合法小时水质问题

- query：某地区某时间段水质小时变化趋势
- P0 candidate top tables：wm_waterquality_hour_records, wm_waterquality_day_records, wm_waterquality_year_records, wm_waterquality_month_records
- matched_columns：无
- generated SQL：未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard
- probe_sql：SELECT station_id, m1_value FROM wm_waterquality_hour_records LIMIT 10
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- used_tables：wm_waterquality_hour_records
- used_columns：wm_waterquality_hour_records.station_id, wm_waterquality_hour_records.m1_value
- fake inner run_sql 是否被调用：是
- 是否执行真实 SQL：否
- 是否通过：是
- reason：符合预期

### 排污口编码问题

- query：查询排污口编码
- P0 candidate top tables：rs_outlet, rs_outlet_info_v2, layer_outlet_sewage, gis_region, wm_uav_info, layer_section, metadata_view, layer_watershed, layer_boundary_park, layer_industrial_lsf
- matched_columns：rs_outlet.outlet_code, rs_outlet.outlet_code_province, rs_outlet_info_v2.outlet_code_national, rs_outlet_info_v2.outlet_code_local, layer_outlet_sewage.code, gis_region.code, wm_uav_info.code, layer_section.code, metadata_view.code, layer_watershed.code, layer_boundary_park.code, layer_industrial_lsf.code
- generated SQL：未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard
- probe_sql：SELECT outlet_code FROM rs_outlet LIMIT 10
- SQL Guard 结果：passed=True；severity=ok；reason=SQL 静态校验通过
- used_tables：rs_outlet
- used_columns：rs_outlet.outlet_code
- fake inner run_sql 是否被调用：是
- 是否执行真实 SQL：否
- 是否通过：是
- reason：符合预期

### 排污口溯源问题

- query：排污口溯源
- P0 candidate top tables：rs_outlet_trace_v2, wst_asset, wst_trace_edge, wst_trace_node, wst_layer_river, wst_control_zone, wst_asset_type_dict, layer_entity_process, wst_asset_trace_snap, wst_trace_topology_issue
- matched_columns：无
- generated SQL：未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard
- probe_sql：SELECT * FROM rs_outlet
- SQL Guard 结果：passed=False；severity=error；reason=排污口溯源问题不能仅使用 rs_outlet 基础信息表
- used_tables：rs_outlet
- used_columns：无
- fake inner run_sql 是否被调用：否
- 是否执行真实 SQL：否
- 是否通过：是
- reason：符合预期

### SQL Guard 拦截验证

- query：某地区某时间段水质变化趋势
- P0 candidate top tables：wm_waterquality_day_records, wm_waterquality_hour_records, wm_waterquality_year_records, wm_waterquality_month_records
- matched_columns：无
- generated SQL：未调用 Vanna 生成 SQL；使用 probe_sql 验证 SQL Guard
- probe_sql：SELECT * FROM wm_waterquality_threshold
- SQL Guard 结果：passed=False；severity=error；reason=水质趋势类问题禁止使用 wm_waterquality_threshold
- used_tables：wm_waterquality_threshold
- used_columns：无
- fake inner run_sql 是否被调用：否
- 是否执行真实 SQL：否
- 是否通过：是
- reason：符合预期

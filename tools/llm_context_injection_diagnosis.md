# LLM 上下文注入可观测性诊断

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：893db0615c4b4c60eeafa2909a9fe4dcb69e9bf4
- 初始 git status --short：
```text
clean
```
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入正式 ChromaDB：否
- 是否修改正式 vanna_data：否
- 是否进入第 3/4 级：否
- 诊断问题总数：6
- DefaultLlmContextEnhancer 是否调用 search_text_memories：是
- DefaultLlmContextEnhancer 是否调用 search_similar_usage：否
- DefaultLlmContextEnhancer 是否调用 save_tool_usage：否
- DefaultLlmContextEnhancer 是否使用 tool_name_filter：否
- DeterministicMetadataContextEnhancer 是否追加 deterministic metadata：是
- DeterministicMetadataContextEnhancer 是否调用 base enhancer：是
- SQL 示例进入 prompt 列表：无
- SQL 示例未进入 prompt 列表：Q1，Q2，Q3，Q6，Q7，Q8
- metadata context 进入 prompt 列表：Q1，Q2，Q3，Q6，Q7，Q8
- 字段 context 弱列表：Q1，Q2，Q3，Q7
- 根因判断：DefaultLlmContextEnhancer 调用 search_text_memories，未调用 search_similar_usage；第 2 级 save_tool_usage 写入的 run_sql SQL 示例不会自动进入当前 system prompt。
- 下一阶段最小修复建议：新增显式 SQL example context injector 或包装 enhancer：在 base enhancer 后调用 agent_memory.search_similar_usage(tool_name_filter='run_sql')，只读注入 top-k SQL 示例；继续保留 deterministic metadata context，并先用 fake/isolated 诊断验证。
- 不建议做的事：不要继续盲目训练更多 SQL 示例；不要改 SQL Guard；不要进入第 3/4 级；不要放松 Q3/Q4/Q9 判定。

## 静态源码诊断结果

- DefaultLlmContextEnhancer 源码路径：E:\3\posgresql\1\vanna_src\src\vanna\core\enhancer\default.py
- 包含 search_text_memories：是
- 包含 search_similar_usage：否
- 包含 save_tool_usage：否
- 包含 tool_name_filter：否
- DeterministicMetadataContextEnhancer 源码路径：E:\3\posgresql\1\tools\metadata_context_enhancer.py
- 追加 Deterministic Metadata Context：是
- 调用 base_enhancer.enhance_system_prompt：是
- 调用 search_similar_usage：否

## fake memory 动态诊断结果

fake memory 同时实现 `search_text_memories()` 和 `search_similar_usage()`；每个问题中 `search_similar_usage()` 都预置返回目标 L2 SQL 示例。实际调用结果显示：base enhancer 调用了 `search_text_memories()`，没有调用 `search_similar_usage()`。

## 逐项 prompt 命中结果

### Q1

- question：查询某站点水质日趋势中的 pH 和溶解氧变化
- target_sample_id：L2_SQL_003
- target_table：wm_waterquality_day_records
- key_columns：m2_value, m3_value, monitor_time, station_id
- sample_question：查询某站点水质日趋势中的 pH 和溶解氧变化
- sample_sql：SELECT station_id, monitor_time, m2_value, m3_value
FROM wm_waterquality_day_records
WHERE station_id = 1408 AND m2_value IS NOT NULL AND m3_value IS NOT NULL
ORDER BY monitor_time
LIMIT 100
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：否
- key_column_hits：无
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。 同时补强关键字段映射。

### Q2

- question：某站点水质小时变化趋势
- target_sample_id：L2_SQL_004
- target_table：wm_waterquality_hour_records
- key_columns：m1_value, m2_value, m3_value, monitor_time, station_id
- sample_question：某站点水质小时变化趋势
- sample_sql：SELECT station_id, monitor_time, m1_value, m2_value, m3_value, water_quality_level
FROM wm_waterquality_hour_records
WHERE station_id = 1408 AND monitor_time >= '2026-01-01'
ORDER BY monitor_time
LIMIT 200
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：否
- key_column_hits：无
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。 同时补强关键字段映射。

### Q3

- question：某站点水质月变化趋势
- target_sample_id：L2_SQL_007
- target_table：wm_waterquality_month_records
- key_columns：m2_value, m3_value, monitor_year, monitor_month, station_id
- sample_question：某站点水质月变化趋势
- sample_sql：SELECT station_id, monitor_year, monitor_month, m2_value, m3_value, water_quality_level
FROM wm_waterquality_month_records
WHERE station_id = 1408 AND monitor_year >= 2025
ORDER BY monitor_year, monitor_month
LIMIT 60
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：否
- key_column_hits：无
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。 同时补强关键字段映射。

### Q6

- question：查询站点名称和所属区域
- target_sample_id：L2_SQL_015
- target_table：wm_station_info_v2
- key_columns：station_name, station_code, region_code, region_name
- sample_question：查询站点名称和所属区域
- sample_sql：SELECT station_code, station_name, region_code, region_name, station_type
FROM wm_station_info_v2
ORDER BY station_name
LIMIT 50
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：是
- key_column_hits：station_name
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。

### Q7

- question：查询区域编码和区域名称
- target_sample_id：L2_SQL_017
- target_table：gis_region
- key_columns：region_code, region_name
- sample_question：查询区域编码和区域名称
- sample_sql：SELECT region_code, region_name, region_level, parent_code
FROM gis_region
ORDER BY region_code
LIMIT 100
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：否
- key_column_hits：无
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。 同时补强关键字段映射。

### Q8

- question：查询取水口名称和水源类型
- target_sample_id：L2_SQL_018
- target_table：wm_water_intake
- key_columns：name, water_type
- sample_question：查询取水口名称和水源类型
- sample_sql：SELECT name, region_name, city, county, water_type, code
FROM wm_water_intake
ORDER BY name
LIMIT 50
- default_enhancer_called_search_text_memories：是
- default_enhancer_called_search_similar_usage：否
- deterministic_enhancer_called_base：是
- final_prompt_contains_sample_id：否
- final_prompt_contains_sample_question：否
- final_prompt_contains_sample_sql：否
- final_prompt_contains_target_table：是
- final_prompt_contains_key_columns：是
- key_column_hits：name，water_type
- final_prompt_contains_deterministic_metadata：是
- diagnosis：SQL 示例未进入最终 prompt；DefaultLlmContextEnhancer 只调用 text memory，fake search_similar_usage 中的 run_sql 示例未被读取。
- recommended_next_action：下一阶段设计显式 SQL example context injector，调用 search_similar_usage 并注入只读 SQL 示例。

# SQL Example Context Enhancer 测试结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：ca12e77ba24b3d12df0aeb0421094500caff3d3d
- 初始 git status --short：
```text
M tools/sql_example_context_enhancer.py
 M tools/sql_example_context_enhancer_test_result.md
 M tools/test_sql_example_context_enhancer.py
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```
- 测试总数：17
- 通过数量：17
- 失败数量：0
- 失败用例列表：无
- 是否调用 base enhancer：是
- 是否调用 search_similar_usage：是
- tool_name_filter 是否为 run_sql：是
- approved 示例是否进入 prompt：是
- 是否允许 level2_sql_examples：是
- 是否允许 level3_p0_sql_examples：是
- 未知 training_level 是否被过滤：是
- requires_manual_review 是否被过滤：是
- SELECT * 是否被过滤：是
- SQL Guard warning/error 是否被过滤：是
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入正式 ChromaDB：否
- 是否修改正式 vanna_data：否
- 是否进入第 3/4 级：否
- 当前结论：通过
- 下一阶段建议：如需主服务接入，另起阶段用该 enhancer 包装现有 context enhancer，并先做 fake/isolated 验证。

## 明细

### 1. 会调用 base enhancer

- pass/fail：pass
- reason：base_enhancer.enhance_system_prompt 被调用
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 2. 会调用 search_similar_usage

- pass/fail：pass
- reason：called=True, tool_name_filter=run_sql
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 3. Q3 月趋势 approved 示例进入 prompt

- pass/fail：pass
- reason：关键内容均已出现
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 4. Q1 日趋势 approved 示例进入 prompt

- pass/fail：pass
- reason：关键内容均已出现
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 5. Q6 站点信息 approved 示例进入 prompt

- pass/fail：pass
- reason：关键内容均已出现
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 6. level3_p0_sql_examples approved 示例进入 prompt 并保留 sample_id/SQL

- pass/fail：pass
- reason：sample_id、SQL 和年度表均已注入
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 7. requires_manual_review 不进入 prompt

- pass/fail：pass
- reason：injected_count=0
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 8. excluded 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L3_P0_SQL_EXCLUDED', 'reason': 'train_decision is not approved'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 9. 未知 training_level 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'UNKNOWN_LEVEL', 'reason': 'training_level is not allowed: level3_p1_sql_examples'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 10. 空 training_level 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'EMPTY_LEVEL', 'reason': 'training_level is not allowed: '}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 11. SQL Guard warning 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L2_SQL_WARNING', 'reason': 'SQL Guard severity is warning'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 12. SQL Guard error 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L3_P0_SQL_GUARD_ERROR', 'reason': 'SQL Guard failed: 存在未知字段：unknown_column'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 13. SELECT * 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L2_SQL_SELECT_STAR', 'reason': 'sql contains SELECT *'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 14. 非 run_sql 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'NON_RUN_SQL', 'reason': 'tool_name is not run_sql'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 15. 无 LIMIT 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'NO_LIMIT', 'reason': 'sql has no LIMIT'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 16. 无召回结果时不破坏原 prompt

- pass/fail：pass
- reason：prompt='SYSTEM\nBASE_CONTEXT', injected_count=0
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 17. top_k 限制生效

- pass/fail：pass
- reason：last_limit=3, injected_count=3, injected_ids=['L2_SQL_TOP_0', 'L2_SQL_TOP_1', 'L2_SQL_TOP_2']
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

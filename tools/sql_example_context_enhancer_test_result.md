# SQL Example Context Enhancer 测试结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：944590cdf80eff70b458353a9af0039ae522237b
- 初始 git status --short：
```text
clean
```
- 测试总数：10
- 通过数量：10
- 失败数量：0
- 失败用例列表：无
- 是否调用 base enhancer：是
- 是否调用 search_similar_usage：是
- tool_name_filter 是否为 run_sql：是
- approved 示例是否进入 prompt：是
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

### 6. requires_manual_review 不进入 prompt

- pass/fail：pass
- reason：injected_count=0
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 7. SQL Guard warning 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L2_SQL_WARNING', 'reason': 'SQL Guard severity is warning'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 8. SELECT * 不进入 prompt

- pass/fail：pass
- reason：filtered=[{'sample_id': 'L2_SQL_SELECT_STAR', 'reason': 'sql contains SELECT *'}]
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 9. 无召回结果时不破坏原 prompt

- pass/fail：pass
- reason：prompt='SYSTEM\nBASE_CONTEXT', injected_count=0
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

### 10. top_k 限制生效

- pass/fail：pass
- reason：last_limit=3, injected_count=3, injected_ids=['L2_SQL_TOP_0', 'L2_SQL_TOP_1', 'L2_SQL_TOP_2']
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

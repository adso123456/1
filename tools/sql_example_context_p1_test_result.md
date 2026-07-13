# SQL Example Context Enhancer P1 回归测试结果

- 测试总数：14
- 通过数量：14
- 失败数量：0
- 失败列表：无

| 用例 | 结果 | 说明 |
|---|---|---|
| 白名单精确包含 L2/P0/P1 | pass | ['level2_sql_examples', 'level3_p0_sql_examples', 'level3_p1_sql_examples'] |
| Level 2 approved 仍被接受 | pass | accepted |
| Level 3 P0 approved 仍被接受 | pass | accepted |
| Level 3 P1 approved 被接受 | pass | accepted |
| P1 非 approved 被拒绝 | pass | train_decision is not approved |
| 未知 training level 被拒绝 | pass | training_level is not allowed: level3_p2_sql_examples |
| 非 run_sql 被拒绝 | pass | tool_name is not run_sql |
| 无 LIMIT 被拒绝 | pass | sql has no LIMIT |
| SELECT * 被拒绝 | pass | sql contains SELECT * |
| SQLGuard fail 被拒绝 | pass | SQL Guard failed: 存在未知字段：unknown_column |
| SQLGuard warning 被拒绝 | pass | SQL Guard severity is warning |
| P1 approved 解析出正确表 | pass | ['rs_outlet_monitor_v2'] |
| P1 approved 进入 prompt | pass | injected=1 |
| 原有 base 和 run_sql 检索契约保持 | pass | tool_filter=run_sql |

- 是否启动主服务：否
- 是否连接数据库：否
- 是否调用 DeepSeek：否
- 是否执行 SQL：否
- 是否写入 ChromaDB：否
- 是否调用 vn.train()：否

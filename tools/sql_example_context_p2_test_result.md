# SQL Example Context Enhancer P2 测试结果

- 测试总数：16
- 通过数量：16
- 失败数量：0
- 失败列表：无

| 用例 | 结果 | 说明 |
|---|---|---|
| Level 2 approved 继续接受 | pass | accepted |
| Level 3 P0 approved 继续接受 | pass | accepted |
| Level 3 P1 approved 继续接受 | pass | accepted |
| Level 3 P2 approved 接受 | pass | accepted |
| P2 非 approved 拒绝 | pass | train_decision is not approved |
| P2 excluded 拒绝 | pass | train_decision is not approved |
| 非 run_sql 拒绝 | pass | tool_name is not run_sql |
| SELECT * 拒绝 | pass | sql contains SELECT * |
| 无 LIMIT 拒绝 | pass | sql has no LIMIT |
| SQLGuard warning/fail 拒绝 | pass | warning=SQL Guard severity is warning; fail=SQL Guard failed: blocked |
| 二表 P2 样本转换正确 | pass | {'sample_id': 'L3_P2_SQL_001', 'question': '查询排污口国家编码、名称及对应整治状态和整治类型记录明细', 'sql': "SELECT o.outlet_code_national, o.outlet_name, r.is_remediated, r.remediation_type\nFROM rs_outlet_info_v2 AS o\nINNER JOIN rs_outlet_remediation_v2 AS r ON o.id = r.outlet_id\nWHERE o.del_flag = '0' AND r.del_flag = '0'\nORDER BY o.outlet_name\nLIMIT 50", 'tables': ['rs_outlet_info_v2', 'rs_outlet_remediation_v2']} |
| 三表 P2 样本转换正确 | pass | {'sample_id': 'L3_P2_SQL_011', 'question': '查询排污口国家编码、名称、整治状态、规范化状态及实况在线监测状态联合记录明细', 'sql': "SELECT o.outlet_code_national, o.outlet_name, r.is_remediated, r.is_standardized, l.drainage_feature, l.has_online_monitor\nFROM rs_outlet_info_v2 AS o\nINNER JOIN rs_outlet_remediation_v2 AS r ON o.id = r.outlet_id\nINNER JOIN rs_outlet_live_v2 AS l ON o.id = l.outlet_id\nWHERE o.del_flag = '0' AND r.del_flag = '0' AND l.del_flag = '0'\nORDER BY o.outlet_name\nLIMIT 50", 'tables': ['rs_outlet_info_v2', 'rs_outlet_remediation_v2', 'rs_outlet_live_v2']} |
| expected_tables metadata 保留 | pass | ['rs_outlet_info_v2', 'rs_outlet_remediation_v2'] |
| join_keys metadata 保留 | pass | [{'left': 'rs_outlet_info_v2.id', 'right': 'rs_outlet_remediation_v2.outlet_id', 'evidence': 'metadata 注释：rs_outlet_info_v2.id 为主键ID，rs_outlet_remediation_v2.outlet_id 为关联排污口ID；两侧均为 bigint。'}] |
| 未知 training level 拒绝 | pass | training_level is not allowed: level3_unknown_sql_examples |
| 白名单精确包含 L2/P0/P1/P2 | pass | ['level2_sql_examples', 'level3_p0_sql_examples', 'level3_p1_sql_examples', 'level3_p2_sql_examples'] |

- 是否启动主服务：否
- 是否连接数据库：否
- 是否调用 DeepSeek：否
- 是否执行 SQL：否
- 是否写入 ChromaDB：否
- 是否调用 vn.train()：否

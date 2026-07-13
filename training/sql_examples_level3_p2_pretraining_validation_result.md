# Level 3 P2 训练前验证结果

## 汇总

- draft数量：11
- review_result数量：11
- ID集合一致：是
- draft/review字段内容一致：是
- approved：9
- requires_manual_review：0
- excluded：2
- approved SQLGuard通过率：9/9
- approved真实SQL审计通过率：9/9
- approved SQL版本与审计一致：9/9
- approved高风险样本：[]
- 非approved进入写入集合：[]
- 最终静态检查：11/0/0
- 训练前验证：通过
- 是否训练：否
- 是否调用vn.train()：否
- 是否调用memory.save_tool_usage()：否
- 是否写入ChromaDB：否

## approved写入候选集合

- sample_id：["L3_P2_SQL_001", "L3_P2_SQL_002", "L3_P2_SQL_003", "L3_P2_SQL_004", "L3_P2_SQL_005", "L3_P2_SQL_006", "L3_P2_SQL_007", "L3_P2_SQL_008", "L3_P2_SQL_011"]
- 数量：9

## 冻结集合

- requires_manual_review：[]
- excluded：["L3_P2_SQL_009", "L3_P2_SQL_010"]
- 后续写入脚本只允许使用approved写入候选集合，冻结集合不得进入写入。

## 错误

- ["无"]

## 结论

训练前验证通过；后续受控写入只允许approved子集，其他样本继续冻结。

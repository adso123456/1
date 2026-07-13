# Level 3 P1 SQL 示例训练前验证结果

## 汇总

- 工作目录：`E:\3\posgresql\1`
- 基础 commit：`ad10a44c802154ede2e1296826ca63732a6b523e`
- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否
- 是否调用 `vn.train()`：否
- 是否调用 `memory.save_tool_usage()`：否
- 是否写入 ChromaDB：否
- 是否修改正式 `vanna_data`：否
- draft 数量：24
- review_result 数量：24
- approved 数量：21
- requires_manual_review 数量：3
- excluded 数量：0
- draft/review 一致性：通过
- approved 子集 SQLGuard 通过数量：21/21
- approved 子集静态通过率：100%
- 非 approved 是否保持冻结：是
- 是否有高风险样本被 approved：否

## 全量一致性检查

- draft/review 数量均为24：是
- ID集合和顺序一致：是
- question/sql/expected_tables/expected_columns/group/priority 一致：是
- 无重复ID：是

## approved 子集

- approved IDs：L3_P1_SQL_001, L3_P1_SQL_002, L3_P1_SQL_003, L3_P1_SQL_006, L3_P1_SQL_007, L3_P1_SQL_008, L3_P1_SQL_009, L3_P1_SQL_011, L3_P1_SQL_012, L3_P1_SQL_013, L3_P1_SQL_014, L3_P1_SQL_015, L3_P1_SQL_016, L3_P1_SQL_017, L3_P1_SQL_018, L3_P1_SQL_019, L3_P1_SQL_020, L3_P1_SQL_021, L3_P1_SQL_022, L3_P1_SQL_023, L3_P1_SQL_024
- SQLGuard passed=true 且 severity=ok：21/21
- unknown_tables/unknown_columns/candidate_mismatch 均为空：是
- 单表、无JOIN、无冻结表、无P0重复、无SELECT *、LIMIT<=100：是
- review_notes均非空：是

## 非 approved 子集

- requires_manual_review IDs：L3_P1_SQL_004, L3_P1_SQL_005, L3_P1_SQL_010
- excluded IDs：无
- 是否标记为可训练：否
- 人工复核原因是否明确：是

## 错误明细

- 无

## 最终结论

**通过。**

训练前验证通过；后续受控写入仅写 approved 子集；requires_manual_review 保持冻结。

## 下一阶段建议

在独立阶段设计受控写入脚本，只允许写入21条approved样本；写入前再次核对正式vanna_data指纹并备份。

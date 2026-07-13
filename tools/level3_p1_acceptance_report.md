# Level 3 P1 验收收口报告

- P1 范围：24
- approved：21
- requires_manual_review：3
- 正式写入：21/21
- 写入方式：memory.save_tool_usage()
- vn.train()：否
- Enhancer 白名单已接入 P1：是
- 持久化检索：21/21
- 冻结样本未写入：是
- 真实验证题数：21
- P1 pass/warning/fail：17/0/0
- 回归 pass/warning/fail：4/0/0
- SAFE-Q4 结果：pass，未执行 SQL
- 正式数据变化：仅受控训练产生的 3 个 vanna_data 文件
- 正式 agent_data 隔离结果：无新增
- 遗留的 3 条人工复核样本：L3_P1_SQL_004、L3_P1_SQL_005、L3_P1_SQL_010
- 本阶段重复训练：否
- 本阶段调用 memory.save_tool_usage()：否

## 候选排序修复

- 既有 P1 candidate ranking 修复保持通过
- 年度水质 REG-Q2 修复：`wm_waterquality_year_records` 稳定排名第 1
- 年度水质指标语义：站点/断面 + 水质指标 + 明确年度粒度
- 排污口 pH 冲突保护：仍优先 `rs_outlet_monitor_v2`
- 水源地年度实际取水量冲突保护：仍优先 `wm_water_source`
- 月度水质粒度保护：仍优先 `wm_waterquality_month_records`
- metadata 静态测试：23/23 通过

## REG-Q2 稳定性

- 独立真实请求：3 次
- pass/warning/fail：3/0/0
- approved SQL 示例召回：是
- approved SQL 示例注入：是
- SQLGuard：3 次均 passed=true、severity=ok
- 真实 SELECT：3 次均执行
- SQL result payload：3 次均产生
- 完整 21 题回归中的 REG-Q2：pass
- 正式 vanna_data 指纹变化：否
- 正式 agent_data/query_results 新增：否

## 验收结论

Level 3 P1 通过。

Level 3 P1 通过不代表整个 Level 3 已完成；当前尚未进入 P2，也未进入第 4 级。

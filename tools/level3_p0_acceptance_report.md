# Level 3 P0 验收收口报告

## 1. 基础信息

- 工作目录：`E:\3\posgresql\1`
- git remote：`origin https://github.com/adso123456/1.git`（fetch/push）
- 基础 commit：`ee2df18fb4b9371487035542c7f7da5d05446790`
- 初始 `git status --short`：

```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```

- 收口报告文件路径：`tools/level3_p0_acceptance_report.md`

## 2. 本次收口操作范围

- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否
- 是否调用 `vn.train()`：否
- 是否调用 `memory.save_tool_usage()`：否
- 是否写入 ChromaDB：否
- 是否修改正式 `vanna_data`：否
- 是否进入 Level 3 P1/P2：否
- 是否进入第 4 级：否

本章节仅描述本次验收收口。本次操作只读取既有报告、样本审查结果、训练脚本和 Git 历史，没有重新训练或重新写入数据。

## 3. Level 3 P0 样本范围

- P0 样本总数：18
- approved 数量：18
- requires_manual_review 数量：0
- excluded 数量：0
- 写入范围：只包含 approved
- 训练批次标识：`level3_p0_sql_examples`
- 工具名称：`run_sql`

## 4. 真实受控写入方式

- 是否调用 `vn.train()`：否
- 是否使用 `memory.save_tool_usage()`：是
- 是否写入正式 ChromaDB：是
- 是否修改正式 `vanna_data`：是
- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否

此前受控写入脚本通过 `create_memory()` 创建 Memory，然后对每条 approved SQL 示例调用：

```python
await memory.save_tool_usage(
    question=question,
    tool_name="run_sql",
    args={"sql": sql},
    ...,
    success=True,
    metadata={...},
)
```

该过程把 approved SQL 工具使用示例写入正式 Memory/ChromaDB，不是通过 `vn.train()` 完成。`memory.save_tool_usage()` 仅发生在此前受控写入阶段，本次收口没有调用。

## 5. 正式写入结果

- 批准写入数量：18
- 写入成功数量：18
- 写入失败数量：0
- skipped 数量：0
- requires_manual_review 写入数量：0
- excluded 写入数量：0
- 备份目录：`E:\3\_backup\level3_p0_training_20260710_145745`

此前受控写入产生的三个预期变化文件：

```text
vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
vanna_data/chroma.sqlite3
```

这三个文件的变化属于此前 approved SQL 示例受控写入产生的预期正式数据变化。它们没有被 add 或提交，不属于本次验收收口产生的修改，也不属于数据污染。

## 6. 关键阶段与提交

| commit | 阶段与作用 |
|---|---|
| `9c2a3291b3706a06a9de77b674678c31569761a0` | 生成 Level 3 P0 候选样本草案 |
| `1a3973b1fe78d012a259c59bc90e0a0c1a8b1176` | 完成 P0 人工审查 |
| `5e2ab08ce82143eefb777f8ab1a0779e38dcd736` | 修正 manual_review 项 |
| `95dbdc08052917ad267baca6040be6b1a2e92ea7` | 同步 review_result 问题字段 |
| `898458e115d5017f4059d81aec9f8d8f405767bd` | 完成 P0 训练前验证 |
| `ca12e77ba24b3d12df0aeb0421094500caff3d3d` | 使用 `memory.save_tool_usage()` 完成 18 条 approved 示例受控正式写入 |
| `cd2e46e5cdad513c5807e3ab56f4b08c06655066` | 将 Level 3 P0 加入 SQL Example Context Enhancer 白名单 |
| `990621a129d73bbfa5868b024d062d8c81d0a55a` | 修复 probe 的 `all_sql` 选择判定 |
| `81d757bd20f922c38b5bcfdea197fa90c674bcdc` | 诊断 Q7 hard block 根因 |
| `59b500adfd6d6971a7a110e370cf23c9e3184355` | 修复 SQLGuard tuple 子查询解析 |
| `178ba73225c5e02d8127a2d1f87402766e9cd8c5` | 完成 Q7 修复后 3 次隔离回归 |
| `ee2df18fb4b9371487035542c7f7da5d05446790` | 完成 Level 3 P0 完整 9 题最终验证 |

## 7. SQLGuard Q7 问题修复

原问题：合法 tuple 子查询中的表名被错误识别为未知字段。

修复方式：先从外层 SQL 提取并移除子查询片段，独立分析外层字段，再递归分析子查询的表和字段。

静态测试结果：

- SQLGuard 测试总数：31
- 通过：31
- 失败：0
- 原有回归：26/26

真实 Q7 隔离回归：

- 运行次数：3
- 成功次数：3
- tuple 子查询出现次数：1
- tuple 子查询误判次数：0
- SQLGuard failed 次数：0
- 粘性 hard block 次数：0
- 真实 SELECT 执行次数：4

结论：Q7 已由 SQLGuard tuple 子查询解析修复解决，暂不需要修改 `GuardedRunSqlTool`。

## 8. 最终完整 9 题验证

- 问题总数：9
- pass：7
- warning：2
- fail：0
- 验收门槛：`pass>=7 / warning<=2 / fail=0`
- 是否达到门槛：是

| 用例 | 状态 |
|---|---|
| P0-Q1 | pass |
| P0-Q2 | pass |
| P0-Q3 | warning |
| P0-Q4 | pass |
| P0-Q5 | pass |
| P0-Q6 | pass |
| P0-Q7 | pass |
| P0-Q8 | warning |
| SAFE-Q9 | pass |

## 9. Q3/Q8 warning 说明

P0-Q3 和 P0-Q8 的 deterministic candidate tables 均只返回 `rs_outlet_monitor_v2`，但模型最终正确生成并执行了使用 `wm_waterquality_year_records` 的 SQL。

两题的 SQL Guard 结果：

- `passed=true`
- `severity=warning`
- `candidate_mismatch=wm_waterquality_year_records`

执行结果：

- 真实 SQL 已执行
- 存在 SQL result payload
- `true_sql_executed=true`
- 业务链路未被阻断

Q3/Q8 warning 是非阻断 candidate ranking 技术债。它不影响 Level 3 P0 达到既定验收门槛。该问题尚未修复，不能视为 P0 排序已正确或 candidate mismatch 已消失。

## 10. SAFE-Q9 安全验收

- 是否生成 SQL：否
- 是否产生 SQL result payload：否
- 是否生成临时 query_results：否
- `true_sql_executed`：否
- 是否执行数据库 SQL：否
- 最终状态：pass

结论：`wm_waterquality_threshold` 水质趋势问题持续在 SQL 执行前被安全拒绝。

## 11. 隔离与数据安全

- 验证是否使用临时 `VANNA_DATA_DIR`：是
- 验证是否使用临时 `AGENT_DATA_DIR`：是
- 正式 `vanna_data` 前后指纹是否一致：是
- 正式 `agent_data/query_results_*.csv` 是否新增：否
- 是否存在非 SAFE `payload=true/executed=false`：否
- 是否执行 DDL/DML：否

最终隔离验证期间正式 `vanna_data` 没有发生新变化。当前工作区中的三个正式 `vanna_data` 修改来自此前已知受控写入，不属于隔离验证污染。

## 12. Level 3 P0 验收结论

**通过。**

Level 3 P0 已完成：

- 样本范围设计
- 候选样本草案
- 人工审查
- review_result 同步
- 训练前验证
- 使用 `memory.save_tool_usage()` 受控正式写入
- 训练后隔离验证
- probe 判定口径修复
- Q7 SQLGuard 修复
- 完整 9 题最终验收

Level 3 P0 通过不代表整个 Level 3 已全部完成。当前尚未进入 Level 3 P1/P2，也未进入第 4 级。

## 13. 遗留项

1. Q3/Q8 年度水质问题的 P0 candidate ranking 仍会返回 `rs_outlet_monitor_v2`，标记为非阻断技术债。
2. 后续 Level 3 P1/P2 的范围与优先级尚未确定。
3. Level 1 Chroma DDL/documentation 覆盖审计尚未完成。

## 14. 下一阶段建议

下一阶段先制定 Level 3 P1 的范围、优先级和候选样本数量。本阶段不直接创建 P1 样本、不训练、不进入第 4 级。

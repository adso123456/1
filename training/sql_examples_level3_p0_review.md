# 第 3 级 P0 候选样本人工审查报告

## 基础信息

- 当前工作目录：E:\3\posgresql\1
- 远端仓库：origin https://github.com/adso123456/1.git
- 当前 commit：9c2a3291b3706a06a9de77b674678c31569761a0
- 初始 git status --short：clean
- 审查日期：2026-07-10

## 本阶段操作范围

| 项目 | 值 |
|------|-----|
| 是否启动真实主服务 | 否 |
| 是否连接数据库 | 否 |
| 是否执行真实 SQL | 否 |
| 是否调用 DeepSeek | 否 |
| 是否训练 Vanna | 否 |
| 是否调用 vn.train() | 否 |
| 是否写入正式 ChromaDB | 否 |
| 是否修改正式 vanna_data | 否 |
| 是否进入第 4 级 | 否 |
| 是否只做人工审查 | 是 |

## 审查统计

| 项目 | 值 |
|------|-----|
| 审查样本总数 | 18 |
| approved | 18 |
| requires_manual_review | 0 |
| excluded | 0 |
| 高风险样本 | 0（4 条已修正） |

## 审查结论汇总

| ID | Group | Decision | Risk | 问题摘要 |
|----|-------|----------|------|---------|
| L3_P0_SQL_001 | A | approved | low | 年度趋势：语义清晰，表粒度正确 |
| L3_P0_SQL_002 | A | approved | low | 多指标汇总：字段选择合理 |
| L3_P0_SQL_003 | A | approved | low | 多站点对比：IN条件模式正确 |
| L3_P0_SQL_004 | A | approved | medium | 已修正：pH年均值排序，去掉最优判断 |
| L3_P0_SQL_005 | A | approved | medium | 较差站点：V/劣V筛选+COD排序合理 |
| L3_P0_SQL_006 | A | approved | low | 氨氮/总氮月趋势：扩展指标正确 |
| L3_P0_SQL_007 | A | approved | medium | 已修正：近两年月趋势，去掉最近12个月 |
| L3_P0_SQL_008 | A | approved | low | 等级分布：GROUP BY+COUNT正确 |
| L3_P0_SQL_009 | A | approved | low | 水温趋势：单指标专项正确 |
| L3_P0_SQL_010 | A | approved | low | COD趋势：单指标专项正确 |
| L3_P0_SQL_011 | B | approved | low | pH最高记录：纯数据极值查询 |
| L3_P0_SQL_012 | B | approved | low | pH最低记录：与011成对 |
| L3_P0_SQL_013 | B | approved | medium | 已修正：阈值5.0mg/L直接写明，去掉偏低判断 |
| L3_P0_SQL_014 | B | approved | low | 多指标日平均值：AVG模式正确 |
| L3_P0_SQL_015 | B | approved | medium | 一个月等级统计：日期范围精确 |
| L3_P0_SQL_016 | B | approved | low | 劣V类筛选：等级筛选正确 |
| L3_P0_SQL_017 | B | approved | medium | 已修正：I至III类筛选，去掉达标判断 |
| L3_P0_SQL_018 | B | approved | low | 氨氮最高记录：极值查询正确 |

## 重点审查项详析

### 1. L3_P0_SQL_004 — pH最优站点排名（高风险，requires_manual_review）

**问题**：question 使用「最优站点排名」，SQL 用 `ORDER BY AVG(m2_value) DESC`（pH从高到低）。

**分析**：pH 并非越高越好。地表水标准（GB 3838-2002）规定pH应在6~9范围内。pH过高表示碱性污染，同样属于水质问题。将pH降序等同于「最优」是错误的业务语义映射。

**影响**：如果直接训练，模型会学到「最优=pH最高」，在真实业务中产生误导性排名。

**SQL结构**：AVG+GROUP BY+ORDER BY 的模式本身正确且有训练价值。

**建议修正**：
- **推荐**：将question改为「查询年度pH年均值最高的站点」，去掉「最优」判断，变为纯数据查询。SQL不需要改。
- 备选：如需保留「最优」，SQL需改为 `ORDER BY ABS(AVG(m2_value) - 7.0) ASC`（距离中性最接近），但增加SQL复杂度。

### 2. L3_P0_SQL_007 — 最近12个月趋势（高风险，requires_manual_review）

**问题**：question 说「最近12个月」，SQL 用 `monitor_year >= 2025` + `LIMIT 24`。

**分析**：
- `monitor_year >= 2025` 不等于「最近12个月」——这是一个固定年份过滤，不表达「最近的」动态含义
- `LIMIT 24` 也不保证返回恰好12条月记录（实际可能不足12条，也可能超过24条）
- 模型可能学到「最近12个月 = monitor_year >= 2025 LIMIT 24」的错误映射

**影响**：固定年份的SQL无法泛化到「最近N个月」的真实用户问法。

**建议修正**：
- **推荐**：将question改为「查看某站点近两年pH和溶解氧月变化趋势」，与SQL的 `monitor_year >= 2025 LIMIT 24` 完全匹配。
- 备选：保留question但SQL使用相对日期函数（如 PostgreSQL 的 `monitor_time >= CURRENT_DATE - INTERVAL '12 months'`），但会增加SQL复杂度和数据库依赖。

### 3. L3_P0_SQL_013 — 溶解氧偏低阈值（高风险，requires_manual_review）

**问题**：阈值 `m3_value < 5.0` 作为「偏低」的通用标准。

**分析**：GB 3838-2002 溶解氧标准因水体功能类别而异：
- I类 ≥ 7.5 mg/L
- II类 ≥ 6 mg/L
- III类 ≥ 5 mg/L
- IV类 ≥ 3 mg/L
- V类 ≥ 2 mg/L

5.0 对III类水是达标线（刚好合格），对I/II类水属于明显偏低。阈值的业务含义取决于目标水体的功能区划。

**影响**：模型可能学到「偏低 = m3_value < 5.0」作为通用规则。

**建议修正**：
- 业务方确认适用阈值后，更新SQL中的具体数值
- 或者将question改为「溶解氧低于5.0mg/L的时段」，去掉「偏低」判断，直接写明阈值

### 4. L3_P0_SQL_017 — 达标站点口径（高风险，requires_manual_review）

**问题**：用水质等级 I/II/III 表示「达标」。

**分析**：达标标准取决于水体的功能区划目标：
- 饮用水源保护区一等区 → 要求II类及以上
- 饮用水源保护区二等区 → 要求III类及以上
- 工业用水区 → IV类即可达标
- 农业用水区 → V类即可达标

将 I/II/III 无条件等同于「达标」在所有场景下都是过度简化。

**影响**：模型可能在不同业务场景下机械使用 I/II/III = 达标。

**建议修正**：
- **推荐**：将question改为「查询月度水质为优良等级(I~III类)的站点」，去掉「达标」判断
- SQL结构正确，仅需修正question措辞

## 低风险样本要点

14 条 approved 样本中，值得关注的细节：

- **L3_P0_SQL_005**：「排名」一词暗示窗口函数RANK()，但SQL实际为ORDER BY排序列表。与第2级L2_SQL_002用法一致，可接受。
- **L3_P0_SQL_015**：与L3_P0_SQL_007的对比——此处日期范围 `'2026-06-01' ~ '2026-07-01'` 确实精确表达了一个月窗口，与question匹配。仅固定时间点为示例。
- **L3_P0_SQL_016**：`water_quality_level = '劣V'` 需在验证时确认数据库实际存储值（可能是「劣V」「劣V类」等变体）。
- **L3_P0_SQL_010**：risk_notes中提示避免误用m12_value很重要，year_records中m10=COD，但其他表中m12也可能是COD。

## 当前结论

**18 条样本人工审查完成。18 approved, 0 requires_manual_review, 0 excluded。**

4 条原 manual_review 样本已通过修改 question（不改 SQL）完成修正：
- L3_P0_SQL_004：去掉「最优」判断 → pH年均值排序
- L3_P0_SQL_007：去掉「最近12个月」→ 近两年月趋势
- L3_P0_SQL_013：去掉「偏低」判断 → 低于5.0mg/L固定阈值
- L3_P0_SQL_017：去掉「达标」判断 → I至III类筛选

全部 18 条可以进入下一阶段（第 3 级 P0 训练写入）。

## 下一阶段建议

1. 第 3 级 P0 训练写入（受控写入 ChromaDB，18 条全部 approved）
2. 训练后做 P0 隔离验证（类似第 2 级 10 题验证）
3. P0 验证通过后再启动 P1（C/D/E 组）草案设计
4. 继续禁止直接进入第 4 级

---

## 确认声明

```text
当前只是人工审查
不包含训练操作
不调用 vn.train()
不写 ChromaDB
不进入第 4 级
不启动真实主服务
不连接数据库
不执行真实 SQL
不调用 DeepSeek
不修改主服务
不修改 SQL Guard
不修改草案 JSON
```

# 第 3 级 P0 候选样本静态审查报告

## 基础信息

- 当前工作目录：E:\3\posgresql\1
- 远端仓库：origin https://github.com/adso123456/1.git
- 当前 commit：09d413e1509fed36d562396776061687b9e2477f
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
| 是否进入第 4 级图表训练 | 否 |
| 是否只做 A/B 组 P0 草案 | 是 |
| 是否涉及 C/D/E/F 组 | 否 |

## 样本统计

| 项目 | 值 |
|------|-----|
| 样本总数 | 18 |
| A 组（水质趋势增强） | 10 |
| B 组（水质指标对比/排名） | 8 |
| P0 占比 | 100% |
| train_decision=draft | 18 |
| review_status=pending_static_check | 18 |

## 静态检查结果

| 项目 | 值 |
|------|-----|
| SQL Guard passed=True | 18/18 (100%) |
| SQL Guard severity=ok | 18/18 (100%) |
| PASS | 18 |
| WARNING | 0 |
| FAIL | 0 |
| SELECT * | 0 |
| wm_waterquality_threshold | 0 |
| DDL/DML | 0 |
| 系统表 | 0 |
| requires_manual_review | 0 |
| 缺少 LIMIT | 0 |
| ID 重复 | 0 |

## A 组明细（10 条）

| ID | Question | 表 | 关键字段 | Guard |
|----|----------|-----|---------|-------|
| L3_P0_SQL_001 | 查看某站点年度水质趋势 | wm_waterquality_year_records | station_id, monitor_year, m2/m3/m8/m9_value, water_quality_level | ok |
| L3_P0_SQL_002 | 某站点年度水质各指标汇总 | wm_waterquality_year_records | station_id, monitor_year, m1~m5/m8~m10_value, water_quality_level | ok |
| L3_P0_SQL_003 | 对比两个站点的pH和溶解氧年度变化 | wm_waterquality_year_records | station_id, monitor_year, m2/m3_value, water_quality_level | ok |
| L3_P0_SQL_004 | 查询年度水质pH均值最优站点排名 | wm_waterquality_year_records | station_id, monitor_year, m2_value, water_quality_level | ok |
| L3_P0_SQL_005 | 查询年度水质较差站点排名 | wm_waterquality_year_records | station_id, monitor_year, m8/m9/m10_value, water_quality_level | ok |
| L3_P0_SQL_006 | 查询某站点水质月趋势中的氨氮和总氮 | wm_waterquality_month_records | station_id, monitor_year, monitor_month, m8/m9_value, water_quality_level | ok |
| L3_P0_SQL_007 | 查看某站点pH和溶解氧在最近12个月的变化趋势 | wm_waterquality_month_records | station_id, monitor_year, monitor_month, m2/m3_value, water_quality_level | ok |
| L3_P0_SQL_008 | 某站点不同水质等级在月记录中的分布 | wm_waterquality_month_records | station_id, monitor_year, water_quality_level | ok |
| L3_P0_SQL_009 | 某站点年度水温变化趋势 | wm_waterquality_year_records | station_id, monitor_year, m1_value, water_quality_level | ok |
| L3_P0_SQL_010 | 某站点年度化学需氧量变化趋势 | wm_waterquality_year_records | station_id, monitor_year, m10_value, water_quality_level | ok |

## B 组明细（8 条）

| ID | Question | 表 | 关键字段 | Guard |
|----|----------|-----|---------|-------|
| L3_P0_SQL_011 | 查看某站点日记录中pH值最高的记录 | wm_waterquality_day_records | station_id, monitor_time, m2_value, water_quality_level | ok |
| L3_P0_SQL_012 | 查看某站点日记录中pH值最低的记录 | wm_waterquality_day_records | station_id, monitor_time, m2_value, water_quality_level | ok |
| L3_P0_SQL_013 | 某站点小时记录中溶解氧偏低的时段 | wm_waterquality_hour_records | station_id, monitor_time, m3_value, water_quality_level | ok |
| L3_P0_SQL_014 | 对比多个水质指标在某站点的日平均值 | wm_waterquality_day_records | station_id, monitor_time, AVG(m2/m3/m4/m5_value) | ok |
| L3_P0_SQL_015 | 查询某站点最近一个月各水质等级天数统计 | wm_waterquality_day_records | station_id, monitor_time, water_quality_level | ok |
| L3_P0_SQL_016 | 某站点日记录中水质等级为劣V类的记录 | wm_waterquality_day_records | station_id, monitor_time, m2/m3/m8_value, water_quality_level | ok |
| L3_P0_SQL_017 | 查询月度水质达标站点列表 | wm_waterquality_month_records | station_id, monitor_year, monitor_month, water_quality_level | ok |
| L3_P0_SQL_018 | 某站点日记录中氨氮值最高的记录 | wm_waterquality_day_records | station_id, monitor_time, m8_value, water_quality_level | ok |

## 安全审查

| 检查项 | 结果 |
|--------|------|
| 无 wm_waterquality_threshold | 通过 |
| 无 DDL/DML | 通过 |
| 无系统表 | 通过 |
| 无 SELECT * | 通过 |
| 全部带 LIMIT | 通过 |
| 全部为 SELECT | 通过 |
| 无 requires_manual_review 字段/表 | 通过 |
| 无溯源场景（L2_SQL_011/012 相关） | 通过 |
| 无 Q10 供水能力场景 | 通过 |
| 未放宽 Q9 拦截 | 通过（不涉及） |

## 覆盖分析

### 表覆盖

| 表 | 第 2 级样本数 | 第 3 级 P0 新增 | 总覆盖 |
|----|-------------|----------------|--------|
| wm_waterquality_year_records | 0 | 7 | 7 |
| wm_waterquality_month_records | 2 | 4 | 6 |
| wm_waterquality_day_records | 3 | 5 | 8 |
| wm_waterquality_hour_records | 3 | 1 | 4 |

### 问法覆盖

| 问法类型 | 覆盖 |
|----------|------|
| 年度趋势 | L3_P0_SQL_001, 002, 009, 010 |
| 多站点对比 | L3_P0_SQL_003 |
| 站点排名(聚合) | L3_P0_SQL_004, 005 |
| 月趋势扩展指标 | L3_P0_SQL_006, 007 |
| 等级分布统计 | L3_P0_SQL_008, 015 |
| 极值查询(最高/最低) | L3_P0_SQL_011, 012, 018 |
| 阈值过滤 | L3_P0_SQL_013 |
| 多指标均值 | L3_P0_SQL_014 |
| 等级筛选 | L3_P0_SQL_016 |
| 达标筛选 | L3_P0_SQL_017 |

## 风险提示

1. **年度表 (year_records) 是第 2 级未覆盖的新表**：实际验证时需要确认模型能正确选择 year_records 而非退化到 day/hour/month。
2. **AVG + GROUP BY 聚合场景增多**：第 2 级仅 L2_SQL_002/008/014/016 使用聚合，第 3 级新增 5 条聚合样本，需验证模型不会滥用聚合。
3. **固定值作为示例**：所有 SQL 中的 station_id=1408、monitor_year=2025 等均为示例值，训练和验证时需明确说明不引导模型固定使用。
4. **劣V 中文编码**：L3_P0_SQL_005 和 L3_P0_SQL_016 使用 water_quality_level='劣V'，验证时需确认编码一致。
5. **hour_records B 组仅 1 条**：B 组小时表覆盖偏少，后续可在 C~F 组或专项补充中增加。

## 当前结论

**通过。** 18 条 P0 候选样本全部通过静态 SQL Guard 校验（passed=True, severity=ok），无 SELECT *、无禁止表、无 DDL/DML、无 requires_manual_review 场景。可以进入下一阶段。

## 下一阶段建议

1. 第 3 级 P0 样本人工审查（逐条确认业务语义 + SQL 合理性）
2. 审查通过后，进入第 3 级 P0 训练写入（受控写入 ChromaDB）
3. 训练后做 P0 隔离验证（类似第 2 级 10 题验证）
4. P0 验证通过后再启动 P1（C/D/E 组）草案设计

---

## 确认声明

```text
当前只是 P0 候选样本草案设计
不含训练操作
不调用 vn.train()
不写 ChromaDB
不进入第 4 级
不启动真实主服务
不连接数据库
不执行真实 SQL
不调用 DeepSeek
不修改主服务
不修改 SQL Guard
```

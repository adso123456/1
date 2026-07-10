# 第 2 级验收收口报告

## 基础信息

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：888ea187d06faa3d76c3d64aab54afbf2eb8c82f
- 初始 git status --short：clean

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
| 是否进入第 3/4 级 | 否 |

## 上一阶段全量验证结果（SQL Example Context 接入后）

| 项目 | 值 |
|------|-----|
| 测试问题总数 | 10 |
| pass 数量 | 9 |
| warning 数量 | 1（Q10） |
| fail 数量 | 0 |
| Q3（水质月趋势） | pass |
| Q4（排污口编码） | pass |
| Q9（阈值表拦截） | pass |
| Q9 true_sql_executed | 否 |
| 正式 vanna_data 是否变化 | 否 |
| 正式 agent_data/query_results_*.csv 是否新增 | 否 |

### 每题明细

| 问题 | 表 | 关键字段 | 结果 |
|------|-----|---------|------|
| Q1 日趋势 | wm_waterquality_day_records | m2_value, m3_value | pass |
| Q2 小时趋势 | wm_waterquality_hour_records | m1_value, m2_value, m3_value | pass |
| Q3 月趋势 | wm_waterquality_month_records | monitor_year, monitor_month, m2_value, m3_value | pass |
| Q4 排污口编码 | rs_outlet | outlet_code, outlet_code_province | pass |
| Q5 排污口基础 | rs_outlet | outlet_name, area_name, county_name… | pass |
| Q6 站点名称 | wm_station_info_v2 | station_code, station_name, region_code, region_name | pass |
| Q7 区域编码 | gis_region | region_code, region_name | pass |
| Q8 取水口 | wm_water_intake | name, water_type | pass |
| Q9 阈值表 | 被 SQL Guard 拦截 | — | pass（未执行 SQL） |
| Q10 供水能力 | wm_water_intake | name, water_type | warning（manual review） |

## 已修正的报告口径

### 1. DeepSeek 调用

- 修正前：报告中写"是否调用 DeepSeek：否"
- 修正后：**真实验证阶段为"是"**（启动了真实主服务，LLM 生成 SQL 时必然调用了 DeepSeek）。本阶段收口不调用。
- 同时修正了探针脚本中的 `called_deepseek` 逻辑。

### 2. SQL 示例是否进入 prompt

- 修正前：报告中写"whether prompt likely contained SQL example：否"、"matched L2 sample id：无"
- 修正后：**统一改为 unknown**。当前 full validation probe 未截取最终 prompt，不能可靠判断 SQL 示例是否进入 context。SQL 结果与 L2 示例高度一致，但不作为直接证据。

### 3. Q10 口径

- Q10 保持 **warning / requires_manual_review**，不能作为 approved 训练成功依据，不会因执行了 SQL 而升级为 pass。

### 4. 下一阶段建议

- 修正前：可考虑进入第 3/4 级或业务确认
- 修正后：**先做业务确认与第 3 级范围设计；确认通过后，另起阶段进入第 3 级。继续禁止直接进入第 4 级。**

## 第 2 级验收结论

**通过。**

经过以下三个阶段，SQL Example Context Enhancer 接入项目验证完成：

| 阶段 | 内容 | 结果 |
|------|------|------|
| Stage 1 | 静态集成测试 | 6/6 通过 |
| Stage 2 | 5 题 smoke 隔离验证 | 5/5 通过 |
| Stage 3 | 10 题全量隔离验证 | 9 pass, 1 warning, 0 fail |

关键里程碑：
- Q3（水质月趋势）从之前的 fail 修复为 pass
- Q9 持续被 SQL Guard 拦截，true_sql_executed=否
- Q4 未回退
- 正式 vanna_data 未被污染
- 未训练 Vanna，未调用 vn.train()
- 未进入第 3/4 级

## 下一阶段建议

1. 先做业务确认与第 3 级范围设计（确定哪些业务问法需要训练、优先级排序）
2. 确认通过后，另起阶段进入第 3 级业务问法训练
3. 继续禁止直接进入第 4 级图表训练

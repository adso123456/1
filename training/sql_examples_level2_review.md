# 第 2 级 SQL 示例训练草案审查说明

## 阶段声明

本文件只是第 2 级 SQL 示例训练草案说明，配套草案文件为 `training/sql_examples_level2_draft.json`。

- 尚未写入 ChromaDB。
- 尚未训练 Vanna。
- 未调用 `vn.train()`。
- 未连接数据库。
- 未执行真实 SQL。
- 所有样本必须经过人工确认后，才能在后续独立阶段进入正式训练。

## 当前覆盖场景

- 水质日趋势：3 条
- 水质小时趋势：3 条
- 水质月趋势：2 条
- 排污口编码：2 条
- 排污口溯源：2 条
- 排污口基础信息：2 条
- 站点信息：2 条
- 区域信息：1 条
- 取水口信息：2 条

## 仍缺样本的方向

- 区域信息当前只有 1 条，可在人工确认后补充市/县/镇街层级样本。
- 取水口信息当前覆盖基础信息和供水能力，可后续补充按行政区、水源类型聚合样本。
- 水质趋势当前以单表查询为主，暂未加入站点表 join，以降低训练草案风险。

## 需要重点人工检查的样本

- `L2_SQL_011`：排污口溯源责任主体统计，风险等级 medium。
- `L2_SQL_012`：排污口溯源企业和排放许可证，风险等级 medium。

以上两条涉及溯源业务语义，虽然 SQL Guard 已静态通过，但建议人工确认字段含义和展示口径。

## 水质趋势表使用情况

- 水质日趋势：`wm_waterquality_day_records`
- 水质小时趋势：`wm_waterquality_hour_records`
- 水质月趋势：`wm_waterquality_month_records`

草案中未使用 `wm_waterquality_threshold`，也未让月趋势退化到 day/hour/year records。

## 排污口场景表使用情况

- 排污口编码：
  - `rs_outlet`
  - `rs_outlet_info_v2`
- 排污口溯源：
  - `rs_outlet_trace_v2`

排污口溯源样本没有只查询 `rs_outlet`；排污口编码样本使用了 `outlet_code`、`outlet_code_province`、`outlet_code_national`、`outlet_code_local` 等明确编码字段。

## 静态审查结论

`tools/check_sql_examples_level2.py` 已完成静态审查：

- JSON 可解析。
- 19 条样本 ID 唯一。
- 所有 SQL 均为 `SELECT`。
- 所有 SQL 均包含 `LIMIT`。
- 未发现 DDL / DML。
- 未访问系统表。
- 未发现未知表或未知字段。
- 未发现 `SELECT *`。
- SQL Guard 全部通过。

当前结论：草案可进入人工复核，但不得直接训练。

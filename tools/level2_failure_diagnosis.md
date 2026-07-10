# 第 2 级训练后失败样本归因诊断

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：f9bdbc2fb8631956cbb4390afec7e35ea88a1555
- 初始 git status --short：
```text
clean
```
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否训练 Vanna：否
- 是否调用 vn.train()：否
- 是否写入 ChromaDB：否
- 是否修改正式 vanna_data：否
- 是否进入第 3/4 级：否
- 失败问题总数：7

## 诊断口径

本报告只基于已有文件做静态归因：`tools/level2_post_training_probe_result.md`、`tools/level2_post_training_probe.py`、第 2 级 SQL 示例审查/训练结果、P0 检索测试结果、`agent_data/column_metadata_index.json` 以及相关工具代码。未启动服务，未连接数据库，未执行 SQL，未训练。

上一阶段失败项中，Q1/Q2/Q3/Q6/Q7/Q8 均表现为未生成可校验 SQL；Q5 已生成并执行 SQL，SQL Guard 通过，但因为 probe 期望只接受 `rs_outlet`，而实际用了 `rs_outlet_info_v2` 被判失败。

## 逐项诊断表

| id | question | expected | observed | P0 candidate 是否命中 | approved SQL 示例是否覆盖 | 主要失败类型 | 是否是真失败 | 是否可能是 probe 预期过窄 | 推荐下一步 | 优先级 |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | 查询某站点水质日趋势中的 pH 和溶解氧变化 | `wm_waterquality_day_records`，禁用 `wm_waterquality_threshold` | 未生成可校验 SQL；P0 top1 为 `wm_waterquality_day_records`；matched columns 仅出现 `rs_outlet_monitor_v2.ph` | 表命中；关键字段命中不足，未把日表 `m2_value/m3_value` 作为 matched columns 展示 | 覆盖；`L2_SQL_003` 与问题完全一致，SQL 使用日表 `m2_value/m3_value` | 训练样本召回不足 + 字段语义上下文不足 + 模型生成策略问题 | 是 | 否 | 先补水质指标字段映射到 system prompt / metadata context，并验证 L2 示例是否被召回；不改 SQL Guard | P0 |
| Q2 | 某站点水质小时变化趋势 | `wm_waterquality_hour_records`，禁退化到 day/month/threshold | 未生成可校验 SQL；P0 top1 为 `wm_waterquality_hour_records`；matched columns unknown | 表命中；字段命中不足 | 覆盖；`L2_SQL_004` 高相似，SQL 使用小时表趋势字段 | 训练样本召回不足 + DDL/字段上下文不足 + 模型生成策略问题 | 是 | 否 | 补小时趋势示例召回验证和水质指标字段提示；可考虑让 probe 带具体 station_id/时间范围减少模型试探 | P0 |
| Q3 | 某站点水质月变化趋势 | `wm_waterquality_month_records`，禁退化到 day/hour/year/threshold | 未生成可校验 SQL；P0 top1 为 `wm_waterquality_month_records`；matched columns unknown | 表命中；字段命中不足 | 覆盖；`L2_SQL_007` 与问题完全一致，SQL 使用月表 `monitor_year/monitor_month/m2_value/m3_value` | 训练样本召回不足 + 字段语义上下文不足 + 模型生成策略问题 | 是 | 否 | 最高优先修复。先验证月趋势 approved 示例是否进入 LLM 上下文，再补月表年月字段与指标字段强提示 | P0 |
| Q5 | 查询排污口基础信息 | `rs_outlet`，禁用 `rs_outlet_trace_v2` | 生成 SQL 使用 `rs_outlet_info_v2`，字段完整，SQL Guard passed=True，真实 SQL 已执行 | 命中；P0 顺序为 `rs_outlet`, `rs_outlet_info_v2` | 覆盖；`L2_SQL_013` 使用 `rs_outlet`，同时 `L2_SQL_010` 覆盖 `rs_outlet_info_v2` 编码字段 | probe 预期可能过窄 + 业务语义需确认 | 不确定；从 SQL 合法性看不是技术真失败 | 是 | 人工确认 `rs_outlet_info_v2` 是否可作为“排污口基础信息”表；若可以，将 probe 改为接受 `rs_outlet` 或 `rs_outlet_info_v2` | P1 |
| Q6 | 查询站点名称和所属区域 | `wm_station_info_v2`，字段含 `station_name` 或 `station_code` | 未生成可校验 SQL；P0 中 `wm_station_info_v2` 排第 7；matched columns 被多个表的 generic `name` 干扰，但包含 `wm_station_info_v2.station_name` | 弱命中；目标表排名偏低 | 覆盖；`L2_SQL_015` 与问题完全一致，使用 `station_code/station_name/region_code/region_name` | P0 候选排序不足 + generic name 干扰 + 训练样本召回不足 | 是 | 否 | 增强“站点名称/所属区域”意图规则，提高 `wm_station_info_v2` 排名，并保留 exact L2 示例 | P1 |
| Q7 | 查询区域编码和区域名称 | `gis_region`，字段含 `region_code` 或 `region_name` | 未生成可校验 SQL；P0 中 `gis_region` 排第 2；matched columns 显示 `gis_region.code` 而非 `region_code/region_name` | 表弱命中；关键字段命中偏泛化 | 覆盖；`L2_SQL_017` 与问题完全一致，使用 `gis_region.region_code/region_name`；metadata 中真实存在这两个字段 | P0 字段匹配不足 + generic code/name 干扰 + 训练样本召回不足 | 是 | 低；metadata 证实 `region_code/region_name` 存在 | 先修 P0 字段匹配，让“区域编码/区域名称”优先命中 `region_code/region_name`；不应把预期改成 `code/name` | P1 |
| Q8 | 查询取水口名称和水源类型 | 普通取水口使用 `wm_water_intake`，禁用 `wm_water_source_intake_v2` | 未生成可校验 SQL；P0 中 `wm_water_intake` 排第 8；matched columns 包含 `wm_water_intake.name/water_type`，但前面有多个泛化表 | 弱命中；目标表排名偏低 | 覆盖；`L2_SQL_018` 与问题完全一致，使用 `wm_water_intake.name/water_type` | P0 候选排序不足 + generic name/type 干扰 + 训练样本召回不足 | 是 | 否 | 增强“普通取水口/水源类型”场景规则，提高 `wm_water_intake` 排名；人工确认普通取水口与水源地取水口的边界 | P1 |

## 逐项要点

### Q1/Q2/Q3 水质趋势

- P0 表排序已经基本正确：日/小时/月问题分别把 `wm_waterquality_day_records`、`wm_waterquality_hour_records`、`wm_waterquality_month_records` 放在 top1。
- 失败不来自 SQL Guard 阻断，因为没有生成 SQL，SQL Guard result 为 unknown。
- 第 2 级 approved 样本覆盖充分：`L2_SQL_003`、`L2_SQL_004`、`L2_SQL_007` 分别与 Q1/Q2/Q3 高相似或完全一致。
- 更可能的问题是训练样本未被稳定召回，或召回后没有压过模型的“先探索/不确定字段”策略。
- 字段语义也需要加强：metadata 中 `m2_value=PH监测值`、`m3_value=溶解氧监测值` 存在，但 P0 matched columns 对 Q1 反而出现 `rs_outlet_monitor_v2.ph`，对 Q2/Q3 为 unknown。这说明 deterministic context 对“pH/溶解氧/趋势字段”的候选字段提示不足。
- Q3 必须优先修复，因为上一阶段通过标准明确要求月趋势稳定通过。

### Q5 排污口基础信息

- 当前 SQL 使用 `rs_outlet_info_v2`，字段覆盖国家/地方编码、排污口名称、分类、流域、省市区县、乡镇、水功能区等基础信息；SQL Guard severity=ok。
- P0 也同时给出 `rs_outlet` 和 `rs_outlet_info_v2`，且没有误用 `rs_outlet_trace_v2`。
- 因此 Q5 更像 probe 预期过窄，而不是技术真失败。
- 需要人工确认：业务上“排污口基础信息”是否只允许 `rs_outlet`，还是 `rs_outlet_info_v2` 也可作为基础信息视图/表。如果可接受，下一阶段应调整 probe 预期为 `rs_outlet` 或 `rs_outlet_info_v2`。

### Q6 站点信息

- metadata 中 `wm_station_info_v2` 有 `station_code`、`station_name`、`region_code`、`region_name`。
- L2 approved 样本 `L2_SQL_015` 与验证问题完全一致。
- 但 P0 中 `wm_station_info_v2` 排第 7，前面被 `gis_poi`、`wm_uav_info`、`layer_section` 等 generic `name` 表干扰。
- 归因更偏 P0 排序/字段匹配不足叠加 SQL 示例召回不足。

### Q7 区域信息

- metadata 中 `gis_region.region_code`、`gis_region.region_name` 真实存在；probe 预期不是字段错误。
- 当前 P0 matched columns 显示 `gis_region.code`，且大量 generic `code/name` 字段进入候选，说明“区域编码/区域名称”没有稳定绑定到 `region_code/region_name`。
- L2 approved 样本 `L2_SQL_017` 与验证问题完全一致，但模型仍未生成 SQL，说明仅靠已训练 SQL 示例还不稳定。

### Q8 取水口信息

- metadata 中 `wm_water_intake.name`、`wm_water_intake.water_type` 真实存在。
- L2 approved 样本 `L2_SQL_018` 与验证问题完全一致。
- P0 把 `wm_water_intake` 排在第 8，前面有 `gis_poi`、`wm_uav_info`、`layer_section`、`metadata_view`、`gis_headwaters` 等泛化 name/type 表。
- 该问题适合改 P0 和 metadata context，不适合改 SQL Guard。
- 还需人工确认“普通取水口”与 `wm_water_source_intake_v2` 的“水源地取水口”边界，避免后续把 Q10 的 requires_manual_review 场景错误批准。

## 总体归因分类统计

| 分类 | 涉及问题 | 数量 | 说明 |
|---|---|---:|---|
| P0 候选不足 | Q6, Q7, Q8 | 3 | 主要是目标表排名偏低或关键字段被 generic `name/code/type` 干扰 |
| 训练样本召回不足 | Q1, Q2, Q3, Q6, Q7, Q8 | 6 | 多个验证问题已有完全一致或高相似 approved 样本，但仍未生成 SQL |
| 字段语义/DDL 上下文不足 | Q1, Q2, Q3, Q6, Q7, Q8 | 6 | 水质指标字段、站点/区域/取水口字段提示不足或不稳定 |
| Guard 阻断导致模型试探失败 | 无 | 0 | 失败项没有 SQL Guard failed；Q5 Guard ok，其余无 SQL |
| probe 预期过窄 | Q5 | 1 | `rs_outlet_info_v2` 是否可接受需确认 |
| 业务语义需人工确认 | Q5, Q8 | 2 | 排污口基础信息表口径、普通取水口和水源地取水口边界需确认 |

## 修复优先级建议

1. P0：修复水质趋势 SQL 生成稳定性，优先 Q3，再 Q1/Q2。不要改 SQL Guard；先确认 L2 SQL 示例是否进入 LLM 上下文，再补“水质指标字段映射”到 metadata context，例如 pH -> `m2_value`、溶解氧 -> `m3_value`，并覆盖 day/hour/month 三类表。
2. P1：修复 generic 字段干扰导致的 P0 排序问题。重点是 Q6 的 `wm_station_info_v2`、Q7 的 `gis_region.region_code/region_name`、Q8 的 `wm_water_intake.name/water_type`。
3. P1：人工确认 Q5 业务口径。若 `rs_outlet_info_v2` 可作为排污口基础信息表，调整 probe 预期；若业务必须使用 `rs_outlet`，再补 system prompt 或 SQL 示例约束。
4. P2：优化 probe 问题设计。对水质趋势可加入具体 station_id 或时间范围做辅助验证，减少模型因“某站点/最近一段时间”不具体而试探；但不要用放宽 Q3/Q4/Q9 标准来制造通过。

## 不建议做的事

- 不建议修改 SQL Guard 来放行或拦截这些失败项；当前失败主因不是 Guard。
- 不建议直接进入第 3/4 级训练。
- 不建议新增大量 SQL 示例后立即训练，应先确认现有 approved 样本是否被召回。
- 不建议把 Q7 预期改成 `code/name`；metadata 已证实 `region_code/region_name` 存在。
- 不建议把 Q10 或水源地取水口供水能力场景轻易标为 approved。
- 不建议为了通过验证而放松 Q3/Q4/Q9 判定。

## 下一阶段建议

下一阶段建议拆成两个小阶段：

1. 先做“L2 示例召回可观测性”诊断：在不训练、不执行 SQL 的前提下，确认每个问题进入 LLM 前实际包含哪些 SQL 示例、DDL 片段和 deterministic metadata context。
2. 再做“P0/metadata context 最小修复”：优先加入水质指标字段映射和站点/区域/取水口意图排序规则，然后重新跑最小验证。修复完成前继续禁止进入第 3/4 级。


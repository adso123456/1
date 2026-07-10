# 第 3 级范围设计检查清单

## 设计完整性检查

- [x] 第 2 级验收基线已写清
- [x] 第 3 级目标已明确（5 个方向）
- [x] 第 3 级不做什么已明确（13 项）
- [x] 候选分组 ≥ 5 组（实际 6 组：A~F）
- [x] 每组有业务目标
- [x] 每组有建议样本数量
- [x] 每组有代表问题
- [x] 每组有预期表
- [x] 每组有关键字段
- [x] 每组有是否允许训练
- [x] 每组有风险点
- [x] 每组有验收标准
- [x] 每组有优先级
- [x] 样本准入规则已写清（12 条）
- [x] requires_manual_review 保留项已列出（4 类 + Q10）
- [x] 第 3 级优先级已排序
- [x] 第 3 级验收标准已明确（11 条）
- [x] 推荐下一阶段已明确

## 禁止事项对照

- [x] 未训练 Vanna
- [x] 未调用 vn.train()
- [x] 未写入正式 ChromaDB
- [x] 未修改正式 vanna_data
- [x] 未启动真实主服务
- [x] 未连接 PostgreSQL
- [x] 未执行真实 SQL
- [x] 未调用 DeepSeek API
- [x] 未修改主服务
- [x] 未修改 SQL Example Context Enhancer
- [x] 未修改 P0
- [x] 未修改 SQL Guard
- [x] 未新增可训练样本 JSON
- [x] 未修改 Level 2 样本
- [x] 未修改数据库结构
- [x] 未执行 DDL/DML
- [x] 未执行 COMMENT ON
- [x] 未进入第 4 级图表训练
- [x] 未提交真实 API Key
- [x] 未提交 .env
- [x] 未 git add .
- [x] 未 git add -A
- [x] 未 git reset --hard
- [x] 未 git stash
- [x] 未 git clean

## 分组覆盖检查

| 分组 | 新表数量 | 已有表扩展 | 优先级 |
|------|---------|-----------|--------|
| A 水质趋势增强 | 1 (year_records) | month_records | P0 |
| B 水质指标对比/排名 | 0 | day/hour/month_records | P0 |
| C 排污口业务查询 | 6 (monitor/live/remediation/wastewater*3) | rs_outlet | P1 |
| D 站点/区域业务查询 | 8 (section/wq_info/hydrological/waterbody/camera*2/uav/city/county) | station_info_v2, gis_region | P1 |
| E 取水口/水源地 | 1 (wm_water_source) | wm_water_intake | P1 |
| F 负向安全边界 | 0 | 不适用 | P3 |

## 未覆盖的表（有业务价值但暂不纳入第 3 级）

以下表在 metadata 中存在且有潜在业务价值，但暂不纳入第 3 级：

- `rs_pollutant_enterprise` / `rs_pollutant_info`：污染源企业信息，与溯源场景相关（溯源已冻结）
- `rs_warn_records`：预警记录，需确认预警口径
- `rs_sewage_info_v2` / `rs_sewage_park_info`：污水/园区信息
- `layer_*` 系列：GIS 图层表，可能更适合第 4 级图表场景
- `wh_*` 系列：水文/气象记录表，需确认与 wm_* 水质表的边界
- `wst_*` 系列：水环境资产追溯表，与溯源冻结相关
- `cf_auto_build_flag` / `day_quality_records` / `day_quality_setting` / `min_value_setting`：配置/设置表

## 确认声明

```text
本文档仅作为 level3_business_question_scope.md 的辅助检查清单
不包含额外设计内容
不引入新文件
```

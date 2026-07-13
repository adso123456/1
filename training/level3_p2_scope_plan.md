# Level 3 P2 JOIN 范围计划

## 1. 基础信息

- 工作目录：`E:\3\posgresql\1`
- git remote：`origin https://github.com/adso123456/1.git`（fetch/push）
- 基础 commit：`0f17d0e941063640a06380b7348f4390bf14752d`
- 本阶段性质：只读证据审计、范围设计、候选草案和静态检查
- 数据来源：metadata index、P0/P1 review_result、P0/P1 验收报告和仓库既有 SQL
- 未连接数据库、未执行 SQL、未启动主服务、未调用 DeepSeek、未训练、未写 ChromaDB

初始 `git status --short` 仅有以下历史受控变化：

```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```

## 2. P0/P1 验收基线

- Level 3 P0：18 条 approved 已受控写入，P0 已验收通过。
- Level 3 P1：范围 24 条，21 条 approved 已受控写入，3 条 requires_manual_review 继续冻结。
- P1 最终验证：17 条业务题 17/0/0，回归安全题 4/0/0，SAFE-Q4 通过。
- P1 验收结论：通过；不代表整个 Level 3 已完成。
- P2 不重复训练 P0/P1，不修改已有 review_result。

## 3. P2 目标与边界

P2 只处理 P1 移交的九类跨表方向。候选必须使用 metadata 中存在、类型兼容且语义有证据的明确 ID 或业务编码 JOIN。禁止名称模糊连接、空间连接、系统表、冻结表、`SELECT *`、DDL/DML 和凭据字段。

本阶段只创建 `draft`，不进行人工审批，不创建 review_result，不进入训练。

## 4. 九类场景审计

| 序号 | 场景 | 左表 | 右表 | 左 JOIN 字段 | 右 JOIN 字段 | 字段类型 | metadata 注释证据 | 仓库既有 SQL/代码证据 | 连接语义 | 基数/去重 | 支持级别 | 候选数量 | 风险说明 |
|---:|---|---|---|---|---|---|---|---|---|---|---|---:|---|
| 1 | 排污口基础表与整治表 JOIN | rs_outlet_info_v2 | rs_outlet_remediation_v2 | id | outlet_id | bigint / bigint | `id=主键ID`；`outlet_id=关联排污口ID` | `train_step3.py` 已使用同一 v2 子表约定：monitor_v2.outlet_id=info_v2.id | v2 排污口主记录到整治记录 | 可能一对多；列表保留记录粒度，聚合用 DISTINCT | confirmed | 2 | 不把 outlet_name 当 JOIN 键；不解释“已整治”枚举 |
| 2 | 排污口跨表完成率统计 | rs_outlet_info_v2 | rs_outlet_remediation_v2 | id | outlet_id | bigint / bigint | JOIN 键存在，但 `is_remediated` 只有字段名称，无已确认值域 | P1 的 L3_P1_SQL_005 因固定“是”继续冻结 | JOIN 可行，但“完成率”分子口径未确认 | 一对多且状态枚举未确认 | requires_manual_review_direction | 0 | 后续需确认 is_remediated 值域和每个排污口有效记录唯一性 |
| 3 | 断面与水质目标 JOIN | wm_section_info | wm_section_wq_info | id | section_id | bigint / bigint | `id=断面id`；`section_id=断面id` | `level3_business_question_scope.md` 明确登记该 JOIN | 断面到年度/月度水质目标 | 一对多；按年度/月度分组，断面计数用 DISTINCT | confirmed | 2 | month=0 的“全年”语义来自 metadata 注释 |
| 4 | 站点与摄像头 JOIN | wm_station_info_v2 | wm_camera_info | 无 | 无 | 无 | 摄像头仅有名称、地址、monitor_subject，无站点/断面归属 ID | P1 明确禁止以名称、地址或监控对象关联 | 无可靠结构化连接键 | 无法静态判断基数 | excluded_direction | 0 | 禁止 station_name=monitor_subject、名称 LIKE、经纬度或空间距离 |
| 5 | 摄像头与平台 JOIN | wm_camera_info | wm_camera_platform | device_code | device_code | varchar(100) / varchar(20) | 注释分别为“设备编号”和“设备编码(国标20位)” | 仓库未发现两表 JOIN 证据；P1 明确保持单表 | 是否同一国标编码体系未被证明 | 唯一性和空值比例未知 | requires_manual_review_direction | 0 | 需人工确认 camera_info.device_code 是否为国标20位且唯一 |
| 6 | 站点与区域 JOIN | wm_hydrological_info | gis_region_county | region_code | region_code | varchar(16) / varchar(50) | 两侧均为行政区划编码；右表明确为区县表 | P1 分别验证水文站 region_code 与区县 region_code 字段 | 固定为区县级精确编码关联，不混用城市表 | 区县到站点一对多；统计用 DISTINCT | confirmed | 2 | 仅做区县口径；不同时 JOIN gis_region_city |
| 7 | 断面与水体 JOIN | wm_section_info | wm_waterbody_info | water_body_id | id | bigint / bigint | `water_body_id=水体id`；`id=id` | P1 将 water_body_id 只展示并明确移交 P2 | 断面所属水体 | 水体到断面一对多；统计用 DISTINCT | confirmed | 2 | 排除 geom，不使用名称连接 |
| 8 | 跨表 COUNT/SUM/AVG | 多个 confirmed 主表 | 对应 confirmed 子表 | 见 JOIN 键证据表 | 见 JOIN 键证据表 | 兼容 | 只基于 confirmed JOIN 边 | P1 已覆盖各单表字段，本阶段只增加跨表口径 | 仅 COUNT；不设计无可靠数值语义的 SUM/AVG | 聚合均说明一对多并使用 DISTINCT | confirmed | 4 | 禁止把子表记录数误当业务实体数 |
| 9 | 三表关联 | rs_outlet_info_v2 | remediation_v2 + live_v2 | id | outlet_id / outlet_id | bigint / bigint | 两条边分别满足排污口 v2 主子表语义 | 同一 v2 子表约定，且两条边独立 confirmed | 主表同时关联整治和实况记录 | 两个一对多边可能乘法放大；不做聚合 | confirmed | 1 | 仅保留 1 条，人工审查时重点确认子表有效记录唯一性 |

审计汇总：9 个场景，6 个 confirmed，2 个 requires_manual_review_direction，1 个 excluded_direction。

## 5. confirmed 场景

1. 排污口基础表与整治表 JOIN
2. 断面与水质目标 JOIN
3. 水文站与区县区域 JOIN
4. 断面与水体 JOIN
5. 基于 confirmed 边的跨表 COUNT
6. 排污口基础、整治、实况三表关联

## 6. requires_manual_review_direction 场景

1. 排污口跨表整治完成率：需要确认 `is_remediated` 值域和有效记录唯一性。
2. 摄像头与平台 JOIN：需要确认两个 `device_code` 是否属于同一国标编码体系及唯一性。

## 7. excluded_direction 场景

1. 站点与摄像头 JOIN：metadata 中没有站点 ID、断面 ID 或设备归属键，名称、地址和空间位置均不允许替代。

## 8. JOIN 键证据表

| JOIN 边 | 左字段注释 | 右字段注释 | 类型兼容 | metadata 存在 | 证据结论 |
|---|---|---|---|---|---|
| rs_outlet_info_v2.id = rs_outlet_remediation_v2.outlet_id | 主键ID | 关联排污口ID | 是，bigint | 是 | confirmed；v2 主子表 ID 关联 |
| rs_outlet_info_v2.id = rs_outlet_live_v2.outlet_id | 主键ID | 关联排污口ID | 是，bigint | 是 | confirmed；v2 主子表 ID 关联 |
| wm_section_info.id = wm_section_wq_info.section_id | 断面id | 断面id | 是，bigint | 是 | confirmed；注释直接一致 |
| wm_section_info.water_body_id = wm_waterbody_info.id | 水体id | id | 是，bigint | 是 | confirmed；所属水体 ID 到水体主键 |
| wm_hydrological_info.region_code = gis_region_county.region_code | 行政区划代码 | 行政区编码 | 是，字符类型 | 是 | confirmed；固定区县级精确编码 |

未确认键：

- `wm_camera_info.device_code = wm_camera_platform.device_code`：字符类型兼容，但编码标准证据不足。
- 站点与摄像头：没有候选键。

## 9. 字段类型兼容性

- `bigint = bigint`：4 条 confirmed ID JOIN 边全部兼容。
- `varchar(16) = varchar(50)`：同属 PostgreSQL 字符类型，且语义均为行政区划编码；固定采用精确等值 JOIN。
- 不对 JOIN 字段使用 CAST、函数、LIKE、OR 或名称转换。

## 10. 候选数量和分布

| 关系/方向 | 候选数 |
|---|---:|
| 排污口基础 + 整治 | 2 |
| 排污口基础 + 实况 | 2 |
| 断面 + 水质目标 | 2 |
| 断面 + 水体 | 2 |
| 水文站 + 区县 | 2 |
| 排污口三表关联 | 1 |
| **合计** | **11** |

- 二表候选：10
- 三表候选：1
- 同一 JOIN 边最多出现 3 次；三表候选只占 1 条。
- 聚合只使用 COUNT(DISTINCT ...)；不假设 JOIN 键唯一。

## 11. 冻结清单

- L2_SQL_011、L2_SQL_012、L2_SQL_019
- rs_outlet_trace_v2 责任主体统计
- 企业和排放许可证溯源
- wm_water_source_intake_v2、水源地取水口供水能力
- wm_waterquality_threshold 正向查询
- L3_P1_SQL_004（异常状态固定值）、L3_P1_SQL_005（已整治固定值）、L3_P1_SQL_010（采样条件固定值）
- DDL/DML、系统表、SELECT *、凭据字段

## 12. P0/P1 去重

- P0/P1 review_result 中的 question 全部作为精确去重集合。
- P2 问题均明确包含两个或三个业务对象，不复用 P0/P1 单表问题文本。
- P2 只新增 JOIN、包含无匹配主记录的 LEFT JOIN 或跨表去重统计。
- 完全重复目标：0。

## 13. 风险与后续人工审查重点

1. 排污口整治、实况子表是否每个 outlet_id 只有一条有效记录。
2. `del_flag='0'` 是否是全部 v2 业务表的一致有效记录口径。
3. 水文站 `region_code` 是否始终使用区县级完整编码。
4. LEFT JOIN 聚合中的主表总数与子表覆盖数是否符合业务口径。
5. 三表候选可能因两个一对多边发生组合放大，不得直接扩展为聚合训练样本。
6. 本阶段不审批；所有 draft 仍需下一阶段逐条人工复核。

## 14. P2 范围结论

九类方向已完成审计。6 个方向具有静态可证明的 JOIN 证据，可支持 11 条候选；2 个方向保留人工复核，1 个方向排除。P2 范围审计通过，可以进入候选草案静态检查，但不能直接训练。

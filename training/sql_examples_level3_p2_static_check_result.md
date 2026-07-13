# Level 3 P2 SQL 候选静态检查结果

## 汇总

- 基础 commit：`0f17d0e941063640a06380b7348f4390bf14752d`
- 审计场景总数：9
- confirmed 场景数：6
- requires_manual_review_direction 数量：2
- excluded_direction 数量：1
- confirmed 场景：排污口基础表与整治表 JOIN, 断面与水质目标 JOIN, 站点与区域 JOIN, 断面与水体 JOIN, 跨表 COUNT/SUM/AVG, 三表关联
- 人工复核方向：排污口跨表完成率统计, 摄像头与平台 JOIN
- 排除方向：站点与摄像头 JOIN
- 候选总数：11
- 二表候选数量：10
- 三表候选数量：1
- ID 连续唯一：是
- SQLGuard pass/warning/fail：11/0/0
- JOIN 键检查通过数量：11
- 字段类型兼容数量：11
- metadata 字段通过数量：11
- P0/P1 完全重复数量：0
- 冻结场景命中数量：0
- P2 范围是否通过：是
- P2 草案静态阶段是否通过：是
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否
- 是否训练：否
- 是否调用 vn.train()：否
- 是否调用 memory.save_tool_usage()：否
- 是否写入 ChromaDB：否

## 逐样本结果

| id | question | expected_tables | join_type | join_keys | join_evidence | used_tables | unknown_tables | unknown_columns | candidate_mismatch | SQLGuard | 最终状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L3_P2_SQL_001 | 查询排污口国家编码、名称及对应整治状态和整治类型 | rs_outlet_info_v2, rs_outlet_remediation_v2 | INNER JOIN | rs_outlet_info_v2.id=rs_outlet_remediation_v2.outlet_id | metadata 注释：rs_outlet_info_v2.id 为主键ID，rs_outlet_remediation_v2.outlet_id 为关联排污口ID；两侧均为 bigint。 | rs_outlet_info_v2, rs_outlet_remediation_v2 | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_002 | 按省市区县统计排污口总数和有整治记录的排污口数量，包含没有整治记录的排污口 | rs_outlet_info_v2, rs_outlet_remediation_v2 | LEFT JOIN | rs_outlet_info_v2.id=rs_outlet_remediation_v2.outlet_id | metadata 注释证明主键ID与关联排污口ID，类型均为 bigint；仅统计是否存在整治记录，不计算未确认的完成率。 | rs_outlet_info_v2, rs_outlet_remediation_v2 | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_003 | 查询排污口国家编码、名称及对应排水特征、在线监测和采样条件状态 | rs_outlet_info_v2, rs_outlet_live_v2 | INNER JOIN | rs_outlet_info_v2.id=rs_outlet_live_v2.outlet_id | metadata 注释：主键ID关联实况表的排污口ID；两侧均为 bigint。 | rs_outlet_info_v2, rs_outlet_live_v2 | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_004 | 按省市区县统计排污口总数和有实况记录的排污口数量，包含没有实况记录的排污口 | rs_outlet_info_v2, rs_outlet_live_v2 | LEFT JOIN | rs_outlet_info_v2.id=rs_outlet_live_v2.outlet_id | metadata 注释证明主键ID与关联排污口ID，类型均为 bigint。 | rs_outlet_info_v2, rs_outlet_live_v2 | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_005 | 查询各断面年度全年水质目标等级 | wm_section_info, wm_section_wq_info | INNER JOIN | wm_section_info.id=wm_section_wq_info.section_id | metadata 两侧注释均为断面id，类型均为 bigint；month=0 的注释明确表示全年。 | wm_section_info, wm_section_wq_info | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_006 | 按年度和目标水质等级统计考核断面数量 | wm_section_info, wm_section_wq_info | INNER JOIN | wm_section_info.id=wm_section_wq_info.section_id | metadata 两侧注释均为断面id且类型均为 bigint；is_examine 注释明确 0/1，month 注释明确 0 表示全年。 | wm_section_info, wm_section_wq_info | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_007 | 查询断面编码、名称及所属水体编码、名称和类型 | wm_section_info, wm_waterbody_info | INNER JOIN | wm_section_info.water_body_id=wm_waterbody_info.id | metadata 注释：section.water_body_id 为水体id，waterbody.id 为主记录id；类型均为 bigint。 | wm_section_info, wm_waterbody_info | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_008 | 统计各水体对应的断面数量，包含没有断面的水体 | wm_waterbody_info, wm_section_info | LEFT JOIN | wm_waterbody_info.id=wm_section_info.water_body_id | metadata 注释证明水体主记录id与断面所属水体id，类型均为 bigint。 | wm_waterbody_info, wm_section_info | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_009 | 查询水文站编码、名称及所属区县编码、名称和城市 | wm_hydrological_info, gis_region_county | INNER JOIN | wm_hydrological_info.region_code=gis_region_county.region_code | metadata 两侧字段均为行政区划编码且为字符类型；右表明确是区县行政区，采用精确等值 JOIN。 | wm_hydrological_info, gis_region_county | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_010 | 统计各区县的水文站数量，包含没有水文站的区县 | gis_region_county, wm_hydrological_info | LEFT JOIN | gis_region_county.region_code=wm_hydrological_info.region_code | metadata 两侧字段均为行政区划编码且为字符类型；固定使用区县表精确关联。 | gis_region_county, wm_hydrological_info | 无 | 无 | 无 | passed=True, severity=ok | pass |
| L3_P2_SQL_011 | 查询排污口国家编码、名称、整治状态、规范化状态及实况在线监测状态 | rs_outlet_info_v2, rs_outlet_remediation_v2, rs_outlet_live_v2 | INNER JOIN, INNER JOIN | rs_outlet_info_v2.id=rs_outlet_remediation_v2.outlet_id, rs_outlet_info_v2.id=rs_outlet_live_v2.outlet_id | metadata 注释证明排污口主键ID与整治表关联排污口ID，两侧均为 bigint。, metadata 注释证明排污口主键ID与实况表关联排污口ID，两侧均为 bigint。 | rs_outlet_info_v2, rs_outlet_remediation_v2, rs_outlet_live_v2 | 无 | 无 | 无 | passed=True, severity=ok | pass |

## 结论

P2 范围审计与候选草案静态阶段通过。所有候选仍为 draft，下一阶段必须先人工审批，只有 approved 样本才可考虑训练。

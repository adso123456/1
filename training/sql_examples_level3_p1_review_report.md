# Level 3 P1 SQL 候选业务审查报告

## 汇总

- 工作目录：`E:\3\posgresql\1`
- 基础 commit：`ad10a44c802154ede2e1296826ca63732a6b523e`
- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否
- 是否调用 `vn.train()`：否
- 是否调用 `memory.save_tool_usage()`：否
- 是否写入 ChromaDB：否
- 审查样本总数：24
- approved 数量：21
- requires_manual_review 数量：3
- excluded 数量：0
- 修改过的样本 ID：L3_P1_SQL_006, L3_P1_SQL_013, L3_P1_SQL_019, L3_P1_SQL_024
- 未修改样本 ID：L3_P1_SQL_001, L3_P1_SQL_002, L3_P1_SQL_003, L3_P1_SQL_004, L3_P1_SQL_005, L3_P1_SQL_007, L3_P1_SQL_008, L3_P1_SQL_009, L3_P1_SQL_010, L3_P1_SQL_011, L3_P1_SQL_012, L3_P1_SQL_014, L3_P1_SQL_015, L3_P1_SQL_016, L3_P1_SQL_017, L3_P1_SQL_018, L3_P1_SQL_020, L3_P1_SQL_021, L3_P1_SQL_022, L3_P1_SQL_023
- 枚举值无法确认的样本：L3_P1_SQL_004, L3_P1_SQL_005, L3_P1_SQL_010
- 指标语义无法确认的样本：无
- 统计口径调整样本：L3_P1_SQL_013, L3_P1_SQL_019
- 问题/SQL不一致修正样本：L3_P1_SQL_013, L3_P1_SQL_019, L3_P1_SQL_024
- P2 或冻结越界数量：0

## 逐样本决定

| id | group | question | decision | business_risk | 是否修改 | 修改摘要 | 审查依据 | 遗留问题 |
|---|---|---|---|---|---|---|---|---|
| L3_P1_SQL_001 | C | 查询排污口监测数据中的COD和氨氮记录 | approved | none | 否 | 无 | metadata明确支持outlet_name、sampling_time、cod和ammonia_nitrogen；问题、字段和按采样时间倒序的SQL一致。 | 无 |
| L3_P1_SQL_002 | C | 查看排污口最近的pH、BOD和流量监测记录 | approved | none | 否 | 无 | metadata明确支持排污口监测表的ph、bod和flow；SQL按sampling_time倒序表达最近记录，未混用水质m字段。 | 无 |
| L3_P1_SQL_003 | C | 查询排污口排水特征和在线监测状态 | approved | none | 否 | 无 | 只展示drainage_feature和has_online_monitor原始状态，不对状态枚举作判断，单表边界清晰。 | 无 |
| L3_P1_SQL_004 | C | 筛选存在异常状况的排污口 | requires_manual_review | medium | 否 | 无 | has_abnormal为varchar，metadata仅说明“有无异常状况”，未提供允许值、示例值或枚举；无法确认'是'为真实值。 | 缺少字段真实枚举值证据，保持冻结 |
| L3_P1_SQL_005 | C | 查询已整治排污口及整治类型 | requires_manual_review | medium | 否 | 无 | is_remediated为varchar，metadata仅说明“是否完成整治”，未提供允许值、示例值或枚举；无法确认'是'为真实值。 | 缺少字段真实枚举值证据，保持冻结 |
| L3_P1_SQL_006 | C | 查询排污口规范化建设状态 | approved | none | 是 | 补充状态展示边界，明确非空不等于已完成规范化建设 | SQL只筛选已记录状态并展示is_standardized；已补充risk_notes，明确非空不等于已完成规范化建设。 | 无 |
| L3_P1_SQL_007 | C | 查询PS类型排水口的COD、总氮和pH日记录 | approved | low | 否 | 无 | metadata字段注释明确给出PS条件下m1=COD、m2=总氮、m3=pH，SQL保持type='PS'，指标映射有直接证据。 | 无 |
| L3_P1_SQL_008 | C | 查询废水小时流量、排放量和状态趋势 | approved | none | 否 | 无 | 小时表仅使用timestamp、type、status、ll和pfl，未使用语义不明确的m1-m22字段。 | 无 |
| L3_P1_SQL_009 | C | 查询PS类型废水月度COD、总氮、pH和排放数据 | approved | low | 否 | 无 | metadata字段注释明确给出PS条件下月度m1=COD、m2=总氮、m3=pH，SQL保持type='PS'。 | 无 |
| L3_P1_SQL_010 | C | 统计具备采样条件的排污口数量 | requires_manual_review | medium | 否 | 无 | has_sampling_condition为varchar，metadata仅说明“是否具备采样条件”，未提供允许值、示例值或枚举；无法确认'是'为真实值。 | 缺少字段真实枚举值证据，保持冻结 |
| L3_P1_SQL_011 | D | 查询断面编码、名称、级别、属性和考核状态 | approved | none | 否 | 无 | 断面字段均有明确metadata注释；water_body_id仅展示，未进入断面与水体JOIN。 | 无 |
| L3_P1_SQL_012 | D | 查询水文站基础信息和建设状态 | approved | none | 否 | 无 | 水文站基础字段语义明确，已排除联系人、电话、geom及跨表关联。 | 无 |
| L3_P1_SQL_013 | D | 按城市统计水文站记录数 | approved | low | 是 | 问题改为统计记录数，COUNT(station_code)改为COUNT(*)并同步字段和说明 | 已将问题修正为按城市统计记录数，并改用COUNT(*)；不再声称按station_code去重或统计唯一站点。 | 无 |
| L3_P1_SQL_014 | D | 查询水体基础信息、类型、功能和所在流域 | approved | none | 否 | 无 | 水体编码、名称、类型、功能、流域、长度和面积均有明确metadata支持，未查询geom。 | 无 |
| L3_P1_SQL_015 | D | 查询摄像头设备基础信息和监控对象 | approved | none | 否 | 无 | 摄像头设备字段语义明确，问题只覆盖单表基础信息和监控对象，未关联站点。 | 无 |
| L3_P1_SQL_016 | D | 查询摄像头平台设备、厂商、型号和在线状态 | approved | none | 否 | 无 | 平台设备、厂商、型号、传输和在线状态字段明确，未查询IP、端口或账号凭据。 | 无 |
| L3_P1_SQL_017 | D | 查询无人机名称、品牌、型号和在线状态 | approved | low | 否 | 无 | drone_device_model仅作为原始字段展示，SQL未解析JSON内部结构；设备身份和在线状态字段有metadata支持。 | 无 |
| L3_P1_SQL_018 | D | 查询区县级行政区名称、编码和所属城市 | approved | none | 否 | 无 | 固定使用gis_region_county单表，region_name、region_code和city字段语义明确。 | 无 |
| L3_P1_SQL_019 | E | 按水源类型统计普通取水口记录数 | approved | low | 是 | 问题改为统计记录数，COUNT(name)改为COUNT(*)并同步字段和说明 | 已将问题修正为按水源类型统计记录数，并改用COUNT(*)；不声称名称唯一或按取水口去重。 | 无 |
| L3_P1_SQL_020 | E | 按城市和区县查询普通取水口 | approved | none | 否 | 无 | 普通取水口的name、city、county和water_type字段明确，未关联区域表。 | 无 |
| L3_P1_SQL_021 | E | 查询普通取水口行政区域和使用状态 | approved | none | 否 | 无 | 查询对象明确为wm_water_intake普通取水口，状态和区域字段均有metadata支持，未混入水源地取水口。 | 无 |
| L3_P1_SQL_022 | E | 查询水源地名称、类型、状态和所在区域 | approved | none | 否 | 无 | wm_water_source的名称、类型、状态和区域字段明确，问题与SQL一致。 | 无 |
| L3_P1_SQL_023 | E | 查询水源地保护等级和保护区划定状态 | approved | none | 否 | 无 | 只展示数据库记录的保护等级、划定状态和文号，不推断保护合规结论。 | 无 |
| L3_P1_SQL_024 | E | 按年实际取水量从高到低查看水源地及其服务人口 | approved | low | 是 | 消除双重排名歧义，明确按年实际取水量主排序 | 问题已修正为按年实际取水量降序，supply_water_year为主排序，service_people_count仅辅助展示和次级排序。 | 无 |

## 校验错误

- 无

## 结论

**通过。**

24 条样本已完成逐条业务审查。后续训练前验证和受控写入只能使用 approved 子集；3 条枚举值缺少证据的样本保持人工复核状态。

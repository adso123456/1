# MySQL 已验证关系

> 只有 `confidence=high` 且 `allowed_for_agent=true` 的关系可进入 Agent。

| 左表.字段 | 右表.字段 | 类型 | 证据 | 置信度 | Agent 可用 |
|---|---|---|---|---|---|
| `wm_panorama_video.layer_id` | `wm_panorama_layer.id` | foreign_key | MySQL 外键 fk_layer_id | high | 否 |
| `wm_waterquality_hour_records.station_id` | `wm_station_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_waterquality_day_records.station_id` | `wm_station_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_waterquality_month_records.section_id` | `wm_section_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_station_info.section_id` | `wm_section_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_station_info.water_body_id` | `wm_waterbody_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_station_info.region_code` | `gis_region.region_code` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wm_section_info.water_body_id` | `wm_waterbody_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wh_hydrological_hour_records.station_id` | `wm_hydrological_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wh_hydrological_day_records.station_id` | `wm_hydrological_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wh_meteorological_hour_records.station_id` | `wm_meteorological_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `wh_meteorological_day_records.station_id` | `wm_meteorological_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `rs_warn_records.station_id` | `wm_station_info.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `rs_pollutant_info.region_id` | `gis_region.id` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |
| `rs_pollutant_info.region_code` | `gis_region.region_code` | logical_relation | B3 已验证 Metadata/SQL 关系 | high | 是 |

## 禁止关系

- 未列入上表的同名 `id` / `name` 字段不得据此自动 JOIN。
- medium/low 置信度关系只记录，不训练为固定 JOIN。
- 小时、日、月事实表不得跨粒度直接拼接。
- 站点 ID 与断面 ID 不得混用。

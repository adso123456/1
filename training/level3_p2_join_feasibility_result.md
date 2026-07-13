# Level 3 P2 JOIN 真实只读可行性审计结果

## 执行边界

- 数据库连接：是，使用 agent_config.py 的现有 DB_KWARGS
- 事务只读：是
- 非 SELECT 业务语句：0
- 主服务：未启动
- DeepSeek：未调用
- 训练：未执行
- ChromaDB：未写入
- agent_data：未写入
- 连接信息：已脱敏，不记录密码或完整连接串

## JOIN 边审计

| ID | JOIN边 | 左/右有效记录 | 左/右键非空 | 左/右键distinct | 匹配/未匹配左实体 | 孤儿右记录 | 匹配率 | 右表最大/平均基数 | 多右记录键数 |
|---|---|---|---|---|---|---|---:|---|---:|
| J1 | 排污口基础—整治 | 5101/5101 | 5101/5101 | 5101/5101 | 5101/0 | 0 | 100.0000% | 1/1.0000 | 0 |
| J2 | 排污口基础—实况 | 5101/5101 | 5101/5101 | 5101/5101 | 5101/0 | 0 | 100.0000% | 1/1.0000 | 0 |
| J3 | 断面—水质目标 | 55/1404 | 55/1404 | 55/57 | 55/0 | 52 | 100.0000% | 26/24.5818 | 55 |
| J4 | 断面—水体 | 55/45 | 55/45 | 25/45 | 55/0 | 20 | 100.0000% | 1/1.0000 | 0 |
| J5 | 水文站—区县 | 1/13 | 1/13 | 1/13 | 0/1 | 13 | 0.0000% | 0/0.0000 | 0 |

## del_flag 值域

| 表 | 分布 | NULL | del_flag='0' | 其他值 |
|---|---|---:|---:|---|
| rs_outlet_info_v2 | {"0": 5101, "1": 1} | 0 | 5101 | {"1": 1} |
| rs_outlet_remediation_v2 | {"0": 5101} | 0 | 5101 | {} |
| rs_outlet_live_v2 | {"0": 5101} | 0 | 5101 | {} |
| wm_section_info | {"0": 55, "1": 3} | 0 | 55 | {"1": 3} |
| wm_section_wq_info | {"0": 1404} | 0 | 1404 | {} |
| wm_waterbody_info | {"0": 45} | 0 | 45 | {} |
| wm_hydrological_info | {"0": 1} | 0 | 1 | {} |

## 专项值域

- month 分布：[[0, 108], [1, 108], [2, 108], [3, 108], [4, 108], [5, 108], [6, 108], [7, 108], [8, 108], [9, 108], [10, 108], [11, 108], [12, 108]]
- month=0 数量：108
- year 分布：[[2025, 663], [2026, 741]]
- 水质目标等级分布：[["II", 715], ["III", 507], ["IV", 182]]
- is_examine 分布：[[null, 24], ["0", 17], ["1", 17]]
- month=0 断面年度组合数：108
- 每个断面年度最大 month=0 记录数：1
- month=0 重复断面年度组合数：0
- 水文站 region_code 长度分布：[[4, 1]]
- 区县 region_code 长度分布：[[6, 13]]
- region_code 精确匹配/未匹配/NULL：0/1/0
- 可能城市级编码数量：1
- 未匹配编码示例：[["4205", 1]]

## 三表组合放大

- 最大乘法放大倍数：1
- 乘积大于1的排污口数量：0
- 乘积大于10的排污口数量：0
- 典型样例：[]

## 11 条候选 SQL 真实执行

| ID | 成功 | 返回列 | 行数 | 达到LIMIT | 实际表 | JOIN类型 | 结果重复行 | 明显重复放大 | 错误 |
|---|---|---|---:|---|---|---|---:|---|---|
| L3_P2_SQL_001 | 是 | ["outlet_code_national", "outlet_name", "is_remediated", "remediation_type"] | 50 | 是 | ["rs_outlet_info_v2", "rs_outlet_remediation_v2"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_002 | 是 | ["province_city_district", "outlet_count", "remediation_record_outlet_count"] | 14 | 否 | ["rs_outlet_info_v2", "rs_outlet_remediation_v2"] | ["LEFT JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_003 | 是 | ["outlet_code_national", "outlet_name", "drainage_feature", "has_online_monitor", "has_sampling_condition"] | 50 | 是 | ["rs_outlet_info_v2", "rs_outlet_live_v2"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_004 | 是 | ["province_city_district", "outlet_count", "live_record_outlet_count"] | 14 | 否 | ["rs_outlet_info_v2", "rs_outlet_live_v2"] | ["LEFT JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_005 | 是 | ["section_code", "section_name", "year", "water_quality_target_level"] | 100 | 是 | ["wm_section_info", "wm_section_wq_info"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_006 | 是 | ["year", "water_quality_target_level", "section_count"] | 6 | 否 | ["wm_section_info", "wm_section_wq_info"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_007 | 是 | ["section_code", "section_name", "water_body_code", "water_body_name", "water_body_type"] | 55 | 否 | ["wm_section_info", "wm_waterbody_info"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_008 | 是 | ["water_body_code", "water_body_name", "section_count"] | 45 | 否 | ["wm_waterbody_info", "wm_section_info"] | ["LEFT JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_009 | 是 | ["station_code", "station_name", "region_code", "region_name", "city"] | 0 | 否 | ["wm_hydrological_info", "gis_region_county"] | ["INNER JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_010 | 是 | ["region_code", "region_name", "city", "station_count"] | 13 | 否 | ["gis_region_county", "wm_hydrological_info"] | ["LEFT JOIN"] | 0 | 否 | 无 |
| L3_P2_SQL_011 | 是 | ["outlet_code_national", "outlet_name", "is_remediated", "is_standardized", "drainage_feature", "has_online_monitor"] | 50 | 是 | ["rs_outlet_info_v2", "rs_outlet_remediation_v2", "rs_outlet_live_v2"] | ["INNER JOIN", "INNER JOIN"] | 0 | 否 | 无 |

## 机器可读审计数据

```json
{
  "transaction_read_only": true,
  "non_select_business_statements": 0,
  "join_edges": [
    {
      "id": "J1",
      "name": "排污口基础—整治",
      "left_table": "rs_outlet_info_v2",
      "left_entity": "id",
      "left_key": "id",
      "left_filter": "del_flag = '0'",
      "right_table": "rs_outlet_remediation_v2",
      "right_entity": "id",
      "right_key": "outlet_id",
      "right_filter": "del_flag = '0'",
      "left_total_raw": 5102,
      "right_total_raw": 5101,
      "left_total": 5101,
      "right_total": 5101,
      "left_key_nonnull": 5101,
      "right_key_nonnull": 5101,
      "left_key_distinct": 5101,
      "right_key_distinct": 5101,
      "matched_left_entities": 5101,
      "unmatched_left_entities": 0,
      "orphan_right_records": 0,
      "max_right_records_per_left_key": 1,
      "avg_right_records_per_left_key": 1.0,
      "left_keys_with_multiple_right_records": 0,
      "match_rate_percent": 100.0
    },
    {
      "id": "J2",
      "name": "排污口基础—实况",
      "left_table": "rs_outlet_info_v2",
      "left_entity": "id",
      "left_key": "id",
      "left_filter": "del_flag = '0'",
      "right_table": "rs_outlet_live_v2",
      "right_entity": "id",
      "right_key": "outlet_id",
      "right_filter": "del_flag = '0'",
      "left_total_raw": 5102,
      "right_total_raw": 5101,
      "left_total": 5101,
      "right_total": 5101,
      "left_key_nonnull": 5101,
      "right_key_nonnull": 5101,
      "left_key_distinct": 5101,
      "right_key_distinct": 5101,
      "matched_left_entities": 5101,
      "unmatched_left_entities": 0,
      "orphan_right_records": 0,
      "max_right_records_per_left_key": 1,
      "avg_right_records_per_left_key": 1.0,
      "left_keys_with_multiple_right_records": 0,
      "match_rate_percent": 100.0
    },
    {
      "id": "J3",
      "name": "断面—水质目标",
      "left_table": "wm_section_info",
      "left_entity": "id",
      "left_key": "id",
      "left_filter": "del_flag = '0'",
      "right_table": "wm_section_wq_info",
      "right_entity": "id",
      "right_key": "section_id",
      "right_filter": "del_flag = '0'",
      "left_total_raw": 58,
      "right_total_raw": 1404,
      "left_total": 55,
      "right_total": 1404,
      "left_key_nonnull": 55,
      "right_key_nonnull": 1404,
      "left_key_distinct": 55,
      "right_key_distinct": 57,
      "matched_left_entities": 55,
      "unmatched_left_entities": 0,
      "orphan_right_records": 52,
      "max_right_records_per_left_key": 26,
      "avg_right_records_per_left_key": 24.581818181818182,
      "left_keys_with_multiple_right_records": 55,
      "match_rate_percent": 100.0
    },
    {
      "id": "J4",
      "name": "断面—水体",
      "left_table": "wm_section_info",
      "left_entity": "id",
      "left_key": "water_body_id",
      "left_filter": "del_flag = '0'",
      "right_table": "wm_waterbody_info",
      "right_entity": "id",
      "right_key": "id",
      "right_filter": "del_flag = '0'",
      "left_total_raw": 58,
      "right_total_raw": 45,
      "left_total": 55,
      "right_total": 45,
      "left_key_nonnull": 55,
      "right_key_nonnull": 45,
      "left_key_distinct": 25,
      "right_key_distinct": 45,
      "matched_left_entities": 55,
      "unmatched_left_entities": 0,
      "orphan_right_records": 20,
      "max_right_records_per_left_key": 1,
      "avg_right_records_per_left_key": 1.0,
      "left_keys_with_multiple_right_records": 0,
      "match_rate_percent": 100.0
    },
    {
      "id": "J5",
      "name": "水文站—区县",
      "left_table": "wm_hydrological_info",
      "left_entity": "id",
      "left_key": "region_code",
      "left_filter": "del_flag = '0'",
      "right_table": "gis_region_county",
      "right_entity": "id",
      "right_key": "region_code",
      "right_filter": "TRUE",
      "left_total_raw": 1,
      "right_total_raw": 13,
      "left_total": 1,
      "right_total": 13,
      "left_key_nonnull": 1,
      "right_key_nonnull": 13,
      "left_key_distinct": 1,
      "right_key_distinct": 13,
      "matched_left_entities": 0,
      "unmatched_left_entities": 1,
      "orphan_right_records": 13,
      "max_right_records_per_left_key": 0,
      "avg_right_records_per_left_key": 0.0,
      "left_keys_with_multiple_right_records": 0,
      "match_rate_percent": 0.0
    }
  ],
  "del_flags": {
    "rs_outlet_info_v2": {
      "distribution": {
        "0": 5101,
        "1": 1
      },
      "null_count": 0,
      "active_zero_count": 5101,
      "other_values": {
        "1": 1
      }
    },
    "rs_outlet_remediation_v2": {
      "distribution": {
        "0": 5101
      },
      "null_count": 0,
      "active_zero_count": 5101,
      "other_values": {}
    },
    "rs_outlet_live_v2": {
      "distribution": {
        "0": 5101
      },
      "null_count": 0,
      "active_zero_count": 5101,
      "other_values": {}
    },
    "wm_section_info": {
      "distribution": {
        "0": 55,
        "1": 3
      },
      "null_count": 0,
      "active_zero_count": 55,
      "other_values": {
        "1": 3
      }
    },
    "wm_section_wq_info": {
      "distribution": {
        "0": 1404
      },
      "null_count": 0,
      "active_zero_count": 1404,
      "other_values": {}
    },
    "wm_waterbody_info": {
      "distribution": {
        "0": 45
      },
      "null_count": 0,
      "active_zero_count": 45,
      "other_values": {}
    },
    "wm_hydrological_info": {
      "distribution": {
        "0": 1
      },
      "null_count": 0,
      "active_zero_count": 1,
      "other_values": {}
    }
  },
  "domains": {
    "month_distribution": [
      [
        0,
        108
      ],
      [
        1,
        108
      ],
      [
        2,
        108
      ],
      [
        3,
        108
      ],
      [
        4,
        108
      ],
      [
        5,
        108
      ],
      [
        6,
        108
      ],
      [
        7,
        108
      ],
      [
        8,
        108
      ],
      [
        9,
        108
      ],
      [
        10,
        108
      ],
      [
        11,
        108
      ],
      [
        12,
        108
      ]
    ],
    "month_zero_count": 108,
    "year_distribution": [
      [
        2025,
        663
      ],
      [
        2026,
        741
      ]
    ],
    "target_level_distribution": [
      [
        "II",
        715
      ],
      [
        "III",
        507
      ],
      [
        "IV",
        182
      ]
    ],
    "is_examine_distribution": [
      [
        null,
        24
      ],
      [
        "0",
        17
      ],
      [
        "1",
        17
      ]
    ],
    "annual_section_year_groups": 108,
    "annual_max_records_per_section_year": 1,
    "annual_duplicate_section_year_groups": 0
  },
  "region_codes": {
    "hydrological_length_distribution": [
      [
        4,
        1
      ]
    ],
    "county_length_distribution": [
      [
        6,
        13
      ]
    ],
    "null_count": 0,
    "exact_match_count": 0,
    "unmatched_count": 1,
    "possible_city_level_count": 1,
    "unmatched_examples": [
      [
        "4205",
        1
      ]
    ]
  },
  "three_table_amplification": {
    "max_product": 1,
    "outlets_product_over_one": 0,
    "outlets_product_over_ten": 0,
    "examples": []
  },
  "candidate_execution": [
    {
      "id": "L3_P2_SQL_001",
      "sql": "SELECT o.outlet_code_national, o.outlet_name, r.is_remediated, r.remediation_type\nFROM rs_outlet_info_v2 AS o\nINNER JOIN rs_outlet_remediation_v2 AS r ON o.id = r.outlet_id\nWHERE o.del_flag = '0' AND r.del_flag = '0'\nORDER BY o.outlet_name\nLIMIT 50",
      "sql_sha256": "3ad57965b36f2305b750f8bb45b64e42fcbbbbb80ec8216461eeef115e4f9096",
      "success": true,
      "columns": [
        "outlet_code_national",
        "outlet_name",
        "is_remediated",
        "remediation_type"
      ],
      "row_count": 50,
      "reached_limit": true,
      "error": "",
      "actual_tables": [
        "rs_outlet_info_v2",
        "rs_outlet_remediation_v2"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_002",
      "sql": "SELECT o.province_city_district, COUNT(DISTINCT o.id) AS outlet_count, COUNT(DISTINCT r.outlet_id) AS remediation_record_outlet_count\nFROM rs_outlet_info_v2 AS o\nLEFT JOIN rs_outlet_remediation_v2 AS r ON o.id = r.outlet_id AND r.del_flag = '0'\nWHERE o.del_flag = '0'\nGROUP BY o.province_city_district\nORDER BY COUNT(DISTINCT r.outlet_id) DESC\nLIMIT 100",
      "sql_sha256": "03c502bd72f0d45434647f7721a41891fc27ec9a658079d9951b29cf77ccc9ec",
      "success": true,
      "columns": [
        "province_city_district",
        "outlet_count",
        "remediation_record_outlet_count"
      ],
      "row_count": 14,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "rs_outlet_info_v2",
        "rs_outlet_remediation_v2"
      ],
      "join_types": [
        "LEFT JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_003",
      "sql": "SELECT o.outlet_code_national, o.outlet_name, l.drainage_feature, l.has_online_monitor, l.has_sampling_condition\nFROM rs_outlet_info_v2 AS o\nINNER JOIN rs_outlet_live_v2 AS l ON o.id = l.outlet_id\nWHERE o.del_flag = '0' AND l.del_flag = '0'\nORDER BY o.outlet_name\nLIMIT 50",
      "sql_sha256": "1b50d1e1d279f91a648bef519bdc3b349f68b2d6ee7ef322a01067e1f1c17302",
      "success": true,
      "columns": [
        "outlet_code_national",
        "outlet_name",
        "drainage_feature",
        "has_online_monitor",
        "has_sampling_condition"
      ],
      "row_count": 50,
      "reached_limit": true,
      "error": "",
      "actual_tables": [
        "rs_outlet_info_v2",
        "rs_outlet_live_v2"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_004",
      "sql": "SELECT o.province_city_district, COUNT(DISTINCT o.id) AS outlet_count, COUNT(DISTINCT l.outlet_id) AS live_record_outlet_count\nFROM rs_outlet_info_v2 AS o\nLEFT JOIN rs_outlet_live_v2 AS l ON o.id = l.outlet_id AND l.del_flag = '0'\nWHERE o.del_flag = '0'\nGROUP BY o.province_city_district\nORDER BY COUNT(DISTINCT l.outlet_id) DESC\nLIMIT 100",
      "sql_sha256": "8ec1eb1799d20d2570117d910987d021a38961b82ebca8bba04eea71f04ddb58",
      "success": true,
      "columns": [
        "province_city_district",
        "outlet_count",
        "live_record_outlet_count"
      ],
      "row_count": 14,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "rs_outlet_info_v2",
        "rs_outlet_live_v2"
      ],
      "join_types": [
        "LEFT JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_005",
      "sql": "SELECT s.section_code, s.section_name, q.year, q.water_quality_target_level\nFROM wm_section_info AS s\nINNER JOIN wm_section_wq_info AS q ON s.id = q.section_id\nWHERE s.del_flag = '0' AND q.del_flag = '0' AND q.month = 0\nORDER BY q.year DESC, s.section_name\nLIMIT 100",
      "sql_sha256": "c511a5fd535e42e7011c9982b21de04a04b1d6b9baf149568c051556ad337dab",
      "success": true,
      "columns": [
        "section_code",
        "section_name",
        "year",
        "water_quality_target_level"
      ],
      "row_count": 100,
      "reached_limit": true,
      "error": "",
      "actual_tables": [
        "wm_section_info",
        "wm_section_wq_info"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_006",
      "sql": "SELECT q.year, q.water_quality_target_level, COUNT(DISTINCT s.id) AS section_count\nFROM wm_section_info AS s\nINNER JOIN wm_section_wq_info AS q ON s.id = q.section_id\nWHERE s.del_flag = '0' AND q.del_flag = '0' AND q.month = 0 AND s.is_examine = '1'\nGROUP BY q.year, q.water_quality_target_level\nORDER BY q.year DESC, q.water_quality_target_level\nLIMIT 100",
      "sql_sha256": "333d7c1f6fa97247619c59811f610665fcca3aa8f7cd9e53babd7b9dee7479c0",
      "success": true,
      "columns": [
        "year",
        "water_quality_target_level",
        "section_count"
      ],
      "row_count": 6,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "wm_section_info",
        "wm_section_wq_info"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_007",
      "sql": "SELECT s.section_code, s.section_name, w.water_body_code, w.water_body_name, w.water_body_type\nFROM wm_section_info AS s\nINNER JOIN wm_waterbody_info AS w ON s.water_body_id = w.id\nWHERE s.del_flag = '0' AND w.del_flag = '0'\nORDER BY w.water_body_name, s.section_name\nLIMIT 100",
      "sql_sha256": "008705a8b18abc21486ef7f057a83325e70a717677f18d3612179b09930f59c3",
      "success": true,
      "columns": [
        "section_code",
        "section_name",
        "water_body_code",
        "water_body_name",
        "water_body_type"
      ],
      "row_count": 55,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "wm_section_info",
        "wm_waterbody_info"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_008",
      "sql": "SELECT w.water_body_code, w.water_body_name, COUNT(DISTINCT s.id) AS section_count\nFROM wm_waterbody_info AS w\nLEFT JOIN wm_section_info AS s ON w.id = s.water_body_id AND s.del_flag = '0'\nWHERE w.del_flag = '0'\nGROUP BY w.water_body_code, w.water_body_name\nORDER BY COUNT(DISTINCT s.id) DESC, w.water_body_name\nLIMIT 100",
      "sql_sha256": "e127420fdb6ee267c2a3f34e612f4267c10d7670f5d7699a3eb04459673ef597",
      "success": true,
      "columns": [
        "water_body_code",
        "water_body_name",
        "section_count"
      ],
      "row_count": 45,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "wm_waterbody_info",
        "wm_section_info"
      ],
      "join_types": [
        "LEFT JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_009",
      "sql": "SELECT h.station_code, h.station_name, c.region_code, c.region_name, c.city\nFROM wm_hydrological_info AS h\nINNER JOIN gis_region_county AS c ON h.region_code = c.region_code\nWHERE h.del_flag = '0'\nORDER BY c.city, c.region_name, h.station_name\nLIMIT 100",
      "sql_sha256": "c4bc6d66a0b39d3f0fa277975f710c246682651dff13740388363c9587d77530",
      "success": true,
      "columns": [
        "station_code",
        "station_name",
        "region_code",
        "region_name",
        "city"
      ],
      "row_count": 0,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "wm_hydrological_info",
        "gis_region_county"
      ],
      "join_types": [
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_010",
      "sql": "SELECT c.region_code, c.region_name, c.city, COUNT(DISTINCT h.id) AS station_count\nFROM gis_region_county AS c\nLEFT JOIN wm_hydrological_info AS h ON c.region_code = h.region_code AND h.del_flag = '0'\nGROUP BY c.region_code, c.region_name, c.city\nORDER BY COUNT(DISTINCT h.id) DESC, c.region_name\nLIMIT 100",
      "sql_sha256": "c6f67241d33088800c7e1c22d8f6e6c750cb3d9a27644a9587345f609a6de8c9",
      "success": true,
      "columns": [
        "region_code",
        "region_name",
        "city",
        "station_count"
      ],
      "row_count": 13,
      "reached_limit": false,
      "error": "",
      "actual_tables": [
        "gis_region_county",
        "wm_hydrological_info"
      ],
      "join_types": [
        "LEFT JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    },
    {
      "id": "L3_P2_SQL_011",
      "sql": "SELECT o.outlet_code_national, o.outlet_name, r.is_remediated, r.is_standardized, l.drainage_feature, l.has_online_monitor\nFROM rs_outlet_info_v2 AS o\nINNER JOIN rs_outlet_remediation_v2 AS r ON o.id = r.outlet_id\nINNER JOIN rs_outlet_live_v2 AS l ON o.id = l.outlet_id\nWHERE o.del_flag = '0' AND r.del_flag = '0' AND l.del_flag = '0'\nORDER BY o.outlet_name\nLIMIT 50",
      "sql_sha256": "52211900ec59555bd350922e4709b2c8cd12d4d2e319e4dd80eb93d70992d1bb",
      "success": true,
      "columns": [
        "outlet_code_national",
        "outlet_name",
        "is_remediated",
        "is_standardized",
        "drainage_feature",
        "has_online_monitor"
      ],
      "row_count": 50,
      "reached_limit": true,
      "error": "",
      "actual_tables": [
        "rs_outlet_info_v2",
        "rs_outlet_remediation_v2",
        "rs_outlet_live_v2"
      ],
      "join_types": [
        "INNER JOIN",
        "INNER JOIN"
      ],
      "duplicate_rows_in_result": 0,
      "obvious_duplicate_amplification": false,
      "semantic_structure_match": true,
      "transaction_read_only": true
    }
  ]
}
```

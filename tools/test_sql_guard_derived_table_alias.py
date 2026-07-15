"""SQLGuard FROM/JOIN 派生表别名聚焦回归。"""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.sql_guard import SQLGuard


Q3 = "查询站点1408最近的水质小时变化趋势，返回监测时间、pH、溶解氧和水质等级，最多100条"
REAL_FAILED_SQL = """SELECT monitor_time, m2_value AS pH, m3_value AS 溶解氧, water_quality_level
FROM (
    SELECT monitor_time, m2_value, m3_value, water_quality_level
    FROM wm_waterquality_hour_records
    WHERE station_id = 1408
    ORDER BY monitor_time DESC
    LIMIT 100
) AS recent
ORDER BY monitor_time ASC"""


def assert_pass(sql: str, expected_tables: list[str], query: str = "") -> None:
    result = SQLGuard().validate(
        sql=sql,
        query=query,
        deterministic_candidate_tables=expected_tables,
    )
    assert result.passed, result.to_dict()
    assert result.severity == "ok", result.to_dict()
    assert set(result.used_tables) == set(expected_tables), result.to_dict()
    assert len(result.used_tables) == len(expected_tables), result.to_dict()
    assert result.unknown_tables == [], result.to_dict()
    assert result.unknown_columns == [], result.to_dict()


def main() -> None:
    assert_pass(REAL_FAILED_SQL, ["wm_waterquality_hour_records"], Q3)

    explicit_alias = """SELECT recent.monitor_time, recent.m2_value
FROM (
    SELECT monitor_time, m2_value
    FROM wm_waterquality_hour_records
    WHERE station_id = 1408
    LIMIT 10
) AS recent
ORDER BY recent.monitor_time DESC
LIMIT 10"""
    assert_pass(explicit_alias, ["wm_waterquality_hour_records"])

    implicit_alias = explicit_alias.replace(") AS recent", ") recent")
    assert_pass(implicit_alias, ["wm_waterquality_hour_records"])

    join_derived = """SELECT s.id AS station_id, recent.latest_monitor_time
FROM wm_station_info_v2 AS s
LEFT JOIN (
    SELECT station_id, MAX(monitor_time) AS latest_monitor_time
    FROM wm_waterquality_hour_records
    GROUP BY station_id
) AS recent
ON s.id = recent.station_id
LIMIT 50"""
    assert_pass(
        join_derived,
        ["wm_station_info_v2", "wm_waterquality_hour_records"],
    )

    unknown_output = SQLGuard().validate(
        sql="""SELECT recent.nonexistent_column
FROM (
    SELECT monitor_time
    FROM wm_waterquality_hour_records
    LIMIT 10
) AS recent""",
        deterministic_candidate_tables=["wm_waterquality_hour_records"],
    )
    assert not unknown_output.passed
    assert "recent.nonexistent_column" in unknown_output.unknown_columns

    unknown_base_column = SQLGuard().validate(
        sql="""SELECT recent.monitor_time
FROM (
    SELECT nonexistent_column AS monitor_time
    FROM wm_waterquality_hour_records
    LIMIT 10
) AS recent""",
        deterministic_candidate_tables=["wm_waterquality_hour_records"],
    )
    assert not unknown_base_column.passed
    assert any("nonexistent_column" in item for item in unknown_base_column.unknown_columns)

    unknown_base_table = SQLGuard().validate(
        sql="""SELECT recent.monitor_time
FROM (
    SELECT monitor_time
    FROM nonexistent_table
    LIMIT 10
) AS recent""",
        deterministic_candidate_tables=["wm_waterquality_hour_records"],
    )
    assert not unknown_base_table.passed
    assert "nonexistent_table" in unknown_base_table.unknown_tables

    ordinary_unknown = SQLGuard().validate(
        sql="SELECT monitor_time FROM nonexistent_table LIMIT 10",
        deterministic_candidate_tables=["wm_waterquality_hour_records"],
    )
    assert not ordinary_unknown.passed
    assert "nonexistent_table" in ordinary_unknown.unknown_tables

    cte = """WITH recent AS (
    SELECT monitor_time, m2_value
    FROM wm_waterquality_hour_records
    WHERE station_id = 1408
    LIMIT 10
)
SELECT monitor_time, m2_value
FROM recent
LIMIT 10"""
    cte_result = SQLGuard().validate(sql=cte)
    assert cte_result.passed, cte_result.to_dict()
    assert cte_result.unknown_tables == []
    assert cte_result.unknown_columns == []

    assert_pass(
        "SELECT list_type, item_code FROM ad_dict LIMIT 10", ["ad_dict"]
    )
    assert_pass(
        "SELECT outlet_name, outlet_code FROM rs_outlet LIMIT 10", ["rs_outlet"]
    )
    assert_pass(
        """SELECT a.station_id, s.station_name
FROM wm_waterquality_hour_records AS a
JOIN wm_station_info_v2 AS s ON a.station_id = s.id
LIMIT 10""",
        ["wm_waterquality_hour_records", "wm_station_info_v2"],
    )

    for sql in (
        "DROP TABLE rs_outlet",
        "UPDATE rs_outlet SET outlet_name = 'x'",
    ):
        result = SQLGuard().validate(sql=sql)
        assert not result.passed
        assert result.forbidden_operations

    print("SQL_GUARD_DERIVED_TABLE_ALIAS_TEST: PASS")


if __name__ == "__main__":
    main()

"""MySQL 全库 inventory 与通用问数 scope 的确定性约束。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "config" / "mysql_full_schema_inventory.json"
SCOPE_PATH = ROOT / "config" / "mysql_general_agent_scope.json"
B3_TABLES = {
    "wm_waterquality_hour_records",
    "wm_waterquality_day_records",
    "wm_waterquality_month_records",
    "wm_station_info",
    "wm_section_info",
    "wm_waterbody_info",
    "gis_region",
    "ad_dict",
    "wh_hydrological_hour_records",
    "wh_hydrological_day_records",
    "wm_hydrological_info",
    "wh_meteorological_hour_records",
    "wh_meteorological_day_records",
    "wm_meteorological_info",
    "rs_pollutant_hour_records",
    "rs_pollutant_day_records",
    "rs_warn_records",
    "rs_pollutant_info",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_inventory_counts_and_unique_classification() -> None:
    inventory = _load(INVENTORY_PATH)
    tables = inventory["tables"]
    assert len(tables) == 307
    assert len({item["table_name"] for item in tables}) == 307
    assert sum(item["column_count"] for item in tables) == 6113
    assert inventory["investigated_previous_unselected_count"] == 289
    assert sum(inventory["classification_counts"].values()) == 307
    assert (
        inventory["recommended_table_count"]
        + inventory["excluded_table_count"]
        + inventory["pending_confirmation_count"]
        == 307
    )
    assert all(item["classification"] in set("ABCDEFGHIJ") for item in tables)
    assert all(item["decision_reason"] for item in tables)
    assert all(item["evidence_sources"] for item in tables)


def test_old_scope_is_preserved() -> None:
    inventory = _load(INVENTORY_PATH)
    included = {
        item["table_name"]
        for item in inventory["tables"]
        if item["recommended_for_general_agent"]
    }
    assert len(B3_TABLES) == 18
    assert B3_TABLES <= included


def test_scope_matches_inventory_and_excludes_unsafe_columns() -> None:
    inventory = _load(INVENTORY_PATH)
    scope = _load(SCOPE_PATH)
    expected = {
        item["table_name"]: item
        for item in inventory["tables"]
        if item["recommended_for_general_agent"]
    }
    actual = {item["table"]: item for item in scope["tables"]}
    assert actual.keys() == expected.keys()
    assert scope["approved_tables"] == list(actual)
    assert len(actual) == inventory["recommended_table_count"]
    assert sum(len(item["included_columns"]) for item in actual.values()) == (
        inventory["recommended_column_count"]
    )
    for table, item in actual.items():
        assert item["included_columns"]
        assert not set(item["included_columns"]) & set(item["excluded_columns"])
        assert item["included_columns"] == expected[table]["included_columns"]
        assert item["excluded_columns"] == expected[table]["excluded_columns"]


def test_known_exclusions_and_report_boundary() -> None:
    inventory = _load(INVENTORY_PATH)
    tables = {item["table_name"]: item for item in inventory["tables"]}
    excluded = {
        "sm_user",
        "sys_oauth_client_details",
        "rs_outlet_bak_20260113",
        "wm_uav_info_bark4",
        "wm_section_wq_info",
        "wm_waterquality_year_records",
        "geometry_columns",
        "sm_login_log",
    }
    assert all(not tables[name]["recommended_for_general_agent"] for name in excluded)
    assert tables["wm_section_wq_info"]["classification"] == "E"
    assert tables["wm_waterquality_year_records"]["classification"] == "J"


def test_old_sensitive_exclusions_remain_excluded() -> None:
    scope = _load(SCOPE_PATH)
    excluded = set(scope["excluded_columns"])
    assert {
        "rs_pollutant_info.geom",
        "rs_pollutant_info.centre",
        "rs_pollutant_info.contact",
        "rs_pollutant_info.phone",
    } <= excluded


def test_only_verified_relationships_are_agent_allowed() -> None:
    scope = _load(SCOPE_PATH)
    relationships = [
        (table["table"], relation)
        for table in scope["tables"]
        for relation in table["relationships"]
    ]
    assert len(relationships) == 14
    assert {
        (
            table,
            item["column"],
            item["target"],
        )
        for table, item in relationships
    } >= {
        ("rs_pollutant_info", "region_id", "gis_region.id"),
        (
            "rs_pollutant_info",
            "region_code",
            "gis_region.region_code",
        ),
    }


def test_mysql_time_ranges_are_half_open() -> None:
    from backend.mysql_sql_guard import MySQLSQLGuard

    guard = MySQLSQLGuard(
        ROOT / "agent_data" / "mysql-lzh-monitor" / "column_metadata_index.json"
    )
    assert guard.validate(
        "SELECT monitor_time FROM wh_hydrological_day_records "
        "WHERE monitor_time >= '2025-01-01' "
        "AND monitor_time < '2025-02-01' LIMIT 10"
    ).passed
    assert not guard.validate(
        "SELECT monitor_time FROM wh_hydrological_day_records "
        "WHERE monitor_time <= '2025-02-01' LIMIT 10"
    ).passed
    assert not guard.validate(
        "SELECT monitor_time FROM wh_hydrological_day_records "
        "WHERE monitor_time BETWEEN '2025-01-01' AND '2025-02-01' LIMIT 10"
    ).passed

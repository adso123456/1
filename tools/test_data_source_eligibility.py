"""Eligibility Gate 的独立回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_eligibility import (
    ELIGIBLE,
    INELIGIBLE,
    UNKNOWN,
    evaluate_table_eligibility,
)


def _profile(
    table: str,
    comment: str = "",
    *,
    columns: list[str] | None = None,
    **quality_overrides: object,
) -> dict:
    quality = {
        "queryable_column_count": 8,
        "row_estimate": 100,
        "sample_row_count": 20,
        "skipped_by_total_timeout": False,
    }
    quality.update(quality_overrides)
    return {
        "schema": "lzh_monitor",
        "table": table,
        "table_comment": comment,
        "columns": [
            {"column": column} for column in (columns or ["id", "name"])
        ],
        "quality": quality,
        "error": "",
    }


def test_known_additions_are_split_by_project_eligibility() -> None:
    expected_ineligible = {
        "camera_alarm_video": "file_or_media_support",
        "dc_survey_app": "application_package",
        "dc_survey_offline_upload": "upload_cache",
        "doc_plan_attachment": "file_or_media_support",
        "model_river_param": "model_runtime_parameter",
        "rs_outlet_bark": "backup_or_temporary",
        "sys_log": "system_log",
        "wm_directory": "platform_configuration",
        "wp_task_file": "file_or_media_support",
        "wt_service_directory": "platform_configuration",
    }
    comments = {
        "dc_survey_app": "巡回调查 APP 版本安装包",
        "model_river_param": "一维河网初始化参数",
    }
    for table, category in expected_ineligible.items():
        result = evaluate_table_eligibility(_profile(table, comments.get(table, "")))
        assert result.status == INELIGIBLE, (table, result)
        assert result.category == category, (table, result)

    expected_eligible = {
        "camera_patrol_hour": "水环境摄像头小时巡查结果",
        "dc_survey_info": "巡回调查信息",
        "dc_survey_task": "巡回调查任务",
        "wm_unmaned_ship": "水质无人船监测",
        "wp_task_directory": "水污染防治任务目录",
        "wp_task_info_proj": "水污染防治项目任务",
        "wp_task_info_proj_dynamic": "水污染防治项目动态",
        "wp_task_info_target": "水污染防治目标考核",
    }
    for table, comment in expected_eligible.items():
        result = evaluate_table_eligibility(_profile(table, comment))
        assert result.status == ELIGIBLE, (table, result)


def test_structural_gates_fail_closed() -> None:
    empty = evaluate_table_eligibility(
        _profile("water_monitor", row_estimate=0, sample_row_count=0)
    )
    assert empty.status == INELIGIBLE
    assert empty.category == "confirmed_empty"

    no_columns = evaluate_table_eligibility(
        _profile("water_monitor", queryable_column_count=0)
    )
    assert no_columns.status == INELIGIBLE
    assert no_columns.category == "no_queryable_columns"

    failed_profile = _profile("water_monitor")
    failed_profile["error"] = "timeout"
    assert evaluate_table_eligibility(failed_profile).status == UNKNOWN


def test_generic_substrings_and_columns_do_not_prove_business_scope() -> None:
    cases = [
        _profile("workstation_config", "桌面平台配置"),
        _profile("intersection_mapping", "路口映射"),
        _profile("system_environment_config", "环境变量/系统环境配置"),
        _profile(
            "technical_device_registry",
            "平台设备注册表",
            columns=["id", "station_id", "status"],
        ),
    ]
    for profile in cases:
        result = evaluate_table_eligibility(profile)
        assert result.status == UNKNOWN, (profile["table"], result)


def test_domain_dimensions_require_domain_context() -> None:
    eligible = {
        "water_quality_station": "水质监测站点",
        "environment_area_dict": "水环境行政区划字典",
        "pollution_warning_threshold": "污染预警阈值",
    }
    for table, comment in eligible.items():
        result = evaluate_table_eligibility(_profile(table, comment))
        assert result.status == ELIGIBLE, (table, result)
        assert result.category == "water_environment_dimension", (table, result)

    platform_cases = {
        "platform_station": "平台服务站点",
        "system_area_dict": "系统区域字典",
        "alert_threshold": "通用告警阈值",
    }
    for table, comment in platform_cases.items():
        result = evaluate_table_eligibility(_profile(table, comment))
        assert result.status == UNKNOWN, (table, result)


def test_confirmed_compound_domain_aliases_are_eligible() -> None:
    tables = (
        "we_phytoplankton_records",
        "we_ecologynutrition_records",
        "wm_waterquality_day_records",
        "wm_waterquality_hour_records",
        "wm_waterquality_month_records",
        "rs_industrypollutant_records",
    )
    for table in tables:
        result = evaluate_table_eligibility(_profile(table))
        assert result.status == ELIGIBLE, (table, result)
        assert result.category == "water_environment_business", (table, result)
    fuzzy = evaluate_table_eligibility(_profile("waterqualityish_config"))
    assert fuzzy.status == UNKNOWN, fuzzy


def test_gate_never_emits_governance_decisions() -> None:
    result = evaluate_table_eligibility(_profile("water_monitor")).as_dict()
    assert "proposed_decision" not in result
    assert "effective_decision" not in result
    assert "selected_scope" not in result


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"Eligibility Gate tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()

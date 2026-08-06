"""阶段 B：确定性评分与同业务表分组的回归测试。

纯函数测试，不依赖数据库，重点锁定：
  1. 评分维度与扣分口径；
  2. 关键指标 unknown 不能建议 active；
  3. 同业务组规则（含正式主表 / 分差过小 / 粒度不一致）；
  4. compute_proposals 只写建议字段，不触碰 effective_decision。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_table_scorer import (
    compute_proposals,
    group_tables,
    score_table,
)


def _profile(
    table: str,
    columns: list[str],
    *,
    row_estimate: int | None = 100_000,
    sample_count: int = 200,
    latest: str | None = "2026-08-01 10:00:00",
    coverage: float | None = 300.0,
    has_pk: bool = True,
    has_unique: bool = False,
    duplicate_ratio: object = 0.0,
    null_rates: dict[str, float] | None = None,
    table_comment: str = "水质监测小时数据",
    role: str = "事实表",
    time_column: str = "monitor_time",
    grain: str = "station_id+monitor_time",
    column_count: int | None = None,
    schema: str = "public",
) -> dict:
    null_rates = null_rates or {}
    quality = {
        "column_count": column_count if column_count is not None else len(columns),
        "queryable_column_count": len(columns),
        "has_primary_key": has_pk,
        "has_unique_key": has_unique,
        "primary_key_columns": ["id"] if has_pk else [],
        "row_estimate": row_estimate,
        "sample_row_count": sample_count,
        "sample_null_rate": 0.05,
        "latest_data_at": latest,
        "time_coverage_days": coverage,
        "duplicate_key_ratio": duplicate_ratio,
        "observed_update_interval": None,
        "staleness_ratio": None,
        "freshness_confidence": 0.0,
        "skipped_by_total_timeout": False,
        "table_comment": table_comment,
    }
    return {
        "schema": schema,
        "table": table,
        "object_type": "table",
        "table_comment": table_comment,
        "table_role_candidate": role,
        "grain_candidate": grain,
        "time_column_candidate": time_column,
        "columns": [
            {
                "column": name,
                "type": "numeric",
                "sample_null_rate": null_rates.get(name, 0.0),
                "sample_distinct_count": 50,
                "sensitive": False,
            }
            for name in columns
        ],
        "quality": quality,
        "error": "",
    }


WATER_COLUMNS = [
    "station_id", "monitor_time", "ph", "cod", "nh3n", "tp", "tn",
    "do", "water_temp", "flow", "area_code", "status",
]


def test_score_high_quality_table_reaches_active_threshold() -> None:
    result = score_table(
        _profile("water_data", WATER_COLUMNS),
        _profile("water_data", WATER_COLUMNS)["quality"],
        comment_ratio=0.9,
    )
    assert result["score"] >= 80
    assert result["can_propose_active"] is True
    assert result["confidence"] >= 0.55


def test_score_backup_mark_deducts_10() -> None:
    backup = _profile("water_data_old", WATER_COLUMNS)
    normal = _profile("water_data", WATER_COLUMNS)
    backup_score = score_table(backup, backup["quality"], 0.8)["score"]
    normal_score = score_table(normal, normal["quality"], 0.8)["score"]
    assert normal_score - backup_score == 10


def test_score_data_table_without_latest_cannot_be_active() -> None:
    profile = _profile(
        "monitor_data",
        WATER_COLUMNS,
        latest=None,
        coverage=None,
    )
    result = score_table(profile, profile["quality"], 0.5)
    assert result["can_propose_active"] is False
    assert "缺少最新数据时间" in "；".join(result["warnings"])


def test_score_static_dict_without_time_is_not_blocked() -> None:
    profile = _profile(
        "sys_dict",
        ["id", "code", "name", "sort_no"],
        latest=None,
        coverage=None,
        role="字典表",
        time_column="",
        grain="",
    )
    result = score_table(profile, profile["quality"], 1.0)
    assert result["can_propose_active"] is True
    assert result["confidence"] >= 0.55


def test_group_detects_backup_family() -> None:
    profiles = [
        _profile("water_data", WATER_COLUMNS),
        _profile("water_data_old", WATER_COLUMNS[:9]),
        _profile("water_data_backup", WATER_COLUMNS[:8]),
    ]
    groups = group_tables(profiles)
    assert len(groups) == 1
    members = {key[1] for key in groups[0]["members"]}
    assert members == {"water_data", "water_data_old", "water_data_backup"}
    assert groups[0]["confidence"] >= 0.55


def test_group_does_not_merge_dict_with_owner_table() -> None:
    asset_columns = ["id", "name", "type", "area", "create_time"]
    dict_columns = ["id", "type_code", "type_name", "sort_no"]
    profiles = [
        _profile("wst_asset", asset_columns, role="业务表", time_column="create_time"),
        _profile(
            "wst_asset_type_dict",
            dict_columns,
            role="字典表",
            time_column="",
        ),
    ]
    groups = group_tables(profiles)
    assert groups == []


def test_proposal_group_with_active_force_pending() -> None:
    profiles = [
        _profile("water_data", WATER_COLUMNS),
        _profile("water_data_old", WATER_COLUMNS[:9], row_estimate=5000),
        _profile("water_data_backup", WATER_COLUMNS[:8], row_estimate=50_000),
    ]
    existing = {
        ("public", "water_data"): {"effective_decision": "active"},
    }
    proposals = compute_proposals(profiles, {}, existing)
    for key, fields in proposals.items():
        assert fields["proposed_decision"] == "pending"
        assert fields["business_group"] == "waterdata"
        assert "替换需人工确认" in fields["proposed_reason"]


def test_proposal_group_without_active_picks_top_and_standby() -> None:
    profiles = [
        _profile("water_data", WATER_COLUMNS, row_estimate=100_000),
        _profile("water_data_old", WATER_COLUMNS[:9], row_estimate=5_000),
        _profile("water_data_backup", WATER_COLUMNS[:8], row_estimate=50_000),
    ]
    proposals = compute_proposals(profiles, {}, {})
    assert proposals[("public", "water_data")]["proposed_decision"] == "active"
    assert proposals[("public", "water_data_backup")]["proposed_decision"] == "standby"
    assert proposals[("public", "water_data_old")]["proposed_decision"] == "standby"


def test_proposal_granularity_mix_force_pending() -> None:
    day = _profile(
        "wm_waterquality_day_records",
        WATER_COLUMNS,
        grain="station_id+day",
    )
    month = _profile(
        "wm_waterquality_month_records",
        WATER_COLUMNS,
        grain="station_id+month",
    )
    proposals = compute_proposals([day, month], {}, {})
    for key, fields in proposals.items():
        assert fields["proposed_decision"] == "pending"
        assert "粒度不一致" in fields["proposed_reason"]


def test_proposal_standalone_thresholds() -> None:
    good = _profile("high_quality", WATER_COLUMNS, row_estimate=500_000)
    mid_columns = [
        "enterprise_id", "enterprise_name", "license_no", "industry_type",
        "address", "contact", "area_code", "registered_capital",
    ]
    mid = _profile(
        "mid_quality",
        mid_columns,
        row_estimate=5_000,
        latest="2026-01-01 00:00:00",
        coverage=100.0,
        role="业务表",
        time_column="register_date",
        grain="enterprise_id",
    )
    low_columns = ["id", "name", "sort_no"]
    low = _profile(
        "low_quality",
        low_columns,
        row_estimate=50,
        latest=None,
        coverage=None,
        time_column="",
        grain="",
        role="业务表",
    )
    proposals = compute_proposals([good, mid, low], {}, {})
    assert proposals[("public", "high_quality")]["proposed_decision"] == "active"
    assert proposals[("public", "mid_quality")]["proposed_decision"] == "pending"
    assert proposals[("public", "low_quality")]["proposed_decision"] == "standby"


def test_proposal_never_touches_effective_decision() -> None:
    profiles = [_profile("water_data", WATER_COLUMNS)]
    proposals = compute_proposals(profiles, {}, {})
    for fields in proposals.values():
        assert "effective_decision" not in fields
        assert "decision_source" not in fields


def test_proposal_unknown_key_metrics_force_pending() -> None:
    profile = _profile(
        "monitor_data",
        WATER_COLUMNS,
        has_pk=False,
        duplicate_ratio="unknown",
    )
    proposals = compute_proposals([profile], {}, {})
    fields = proposals[("public", "monitor_data")]
    assert fields["proposed_decision"] == "pending"
    assert "关键质量指标 unknown" in fields["proposed_reason"]


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len([1 for n in globals() if n.startswith('test_')]) - failed}/{len([1 for n in globals() if n.startswith('test_')])} passed")
    raise SystemExit(1 if failed else 0)

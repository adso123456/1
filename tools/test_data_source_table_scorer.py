"""阶段 B：确定性评分与同业务表分组的回归测试（冻结契约版）。

纯函数测试，不依赖数据库，重点锁定：
  1. 每表独立评分、独立判定；组不再 winner-takes-all；
  2. update_interval unknown 完全中性；非时序表时间维度 N/A-neutral；
  3. confirmed_empty -> standby，数据状态未知 -> pending；
  4. 非业务高置信排除（taxonomy）与业务反证；
  5. 组内唯一自动降级：duplicate_structure / backup_mirror；
  6. compute_proposals 只写建议字段，不触碰 effective_decision。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_table_scorer import (
    _business_time_column,
    _is_audit_time_column,
    _is_time_series_like,
    _looks_time_column,
    classify_non_business_evidence,
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
    structure_fingerprint: str = "",
    data_fingerprint: str = "",
    error: str = "",
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
        "structure_fingerprint": structure_fingerprint,
        "data_fingerprint": data_fingerprint,
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
        "error": error,
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
        table_comment="系统业务字典",
    )
    result = score_table(profile, profile["quality"], 1.0)
    assert result["can_propose_active"] is True
    assert result["confidence"] >= 0.55
    # 非时序表时间维度 N/A-neutral：不缺新鲜度/覆盖/更新分
    assert result["breakdown"]["数据新鲜度"] == 20.0
    assert result["breakdown"]["时间覆盖连续性"] == 10.0
    assert result["breakdown"]["持续更新迹象"] == 5.0


def test_audit_time_column_not_time_series() -> None:
    profile = _profile(
        "wm_station_info",
        ["id", "name", "create_time", "update_time", "region_code"],
        latest=None,
        coverage=None,
        role="业务表",
        time_column="create_time",
        grain="",
        table_comment="站点基础信息",
    )
    assert _is_time_series_like(profile, profile["quality"]) is False
    result = score_table(
        profile,
        profile["quality"],
        1.0,
        static_volume=True,
    )
    assert result["breakdown"]["有效数据量"] == 15.0
    assert result["can_propose_active"] is True


def test_business_time_column_is_time_series() -> None:
    profile = _profile("monitor_data", WATER_COLUMNS)
    assert _is_time_series_like(profile, profile["quality"]) is True


def test_time_column_lexical_boundary_actor_fields() -> None:
    """_by 结尾的 actor/operator 字段、普通 id 字段不得命中时间类型识别。"""
    for column in (
        "update_by", "created_by", "modified_by",
        "candidate_id", "validated_by",
    ):
        assert _looks_time_column(column) is False, column
        assert _business_time_column(column) is False, column


def test_time_column_lexical_boundary_time_tokens() -> None:
    """独立时间 token 识别：year/month/day/hour 及业务观测前缀组合。"""
    for column in (
        "monitor_year", "year", "monitor_time", "sampling_time",
        "stat_date", "record_time", "sample_date", "report_time",
        "timestamp",
    ):
        assert _looks_time_column(column) is True, column


def test_time_column_business_layer_excludes_audit_and_plain() -> None:
    """审计/技术时间字段是时间类型，但不能作为业务观测时间证据。"""
    for column in (
        "create_time", "created_at", "update_time", "updated_at",
        "modify_time", "sync_time",
    ):
        assert _looks_time_column(column) is True, column
        assert _is_audit_time_column(column) is True, column
        assert _business_time_column(column) is False, column
    assert _business_time_column("year") is False
    assert _business_time_column("time") is False


def test_business_time_column_excludes_audit_prefix_combos() -> None:
    """业务前缀 + 审计时间组合（data_update_time 等）必须先经 audit 层排除。"""
    for column in ("data_update_time", "record_created_at", "monitor_update_time"):
        assert _looks_time_column(column) is True, column
        assert _is_audit_time_column(column) is True, column
        assert _business_time_column(column) is False, column
    for column in ("monitor_year", "sampling_time", "stat_date", "record_time"):
        assert _business_time_column(column) is True, column


def test_update_by_does_not_pollute_business_time_judgment() -> None:
    """真实回归：update_by 不得抢占/污染业务时间判断，monitor_year 才是证据。"""
    profile = _profile(
        "we_fish_records",
        [
            "id", "water_body_id", "species", "section",
            "monitor_year", "create_by", "create_time",
            "update_by", "update_time",
        ],
        latest=None,
        coverage=None,
        role="业务表",
        time_column="monitor_year",
        grain="water_body_id+monitor_year",
        table_comment="鱼类",
    )
    # monitor_year 是业务观测时间，update_by 不是。
    assert _business_time_column("monitor_year") is True
    assert _business_time_column("update_by") is False
    assert _is_time_series_like(profile, profile["quality"]) is True
    # 仅存在 update_by 时不得判为时序表。
    only_actor = _profile(
        "wm_station_info",
        ["id", "name", "update_by", "update_time"],
        latest=None,
        coverage=None,
        role="业务表",
        time_column="",
        grain="",
        table_comment="站点基础信息",
    )
    assert _is_time_series_like(only_actor, only_actor["quality"]) is False


def test_static_table_volume_full_for_nonempty() -> None:
    profile = _profile(
        "std_dict",
        ["id", "code", "name", "sort_no"],
        row_estimate=8,
        latest=None,
        coverage=None,
        role="字典表",
        time_column="",
        grain="",
        table_comment="业务字典",
    )
    result = score_table(
        profile,
        profile["quality"],
        1.0,
        static_volume=True,
    )
    assert result["breakdown"]["有效数据量"] == 15.0


def test_static_table_volume_unknown_rows_with_sample() -> None:
    profile = _profile(
        "std_dict",
        ["id", "code", "name"],
        row_estimate=None,
        latest=None,
        coverage=None,
        role="字典表",
        time_column="",
        grain="",
        table_comment="业务字典",
    )
    result = score_table(
        profile,
        profile["quality"],
        1.0,
        static_volume=True,
    )
    assert result["breakdown"]["有效数据量"] == 12.0
    assert any("行数估算未知" in warning for warning in result["warnings"])


def test_time_series_volume_curve_unchanged() -> None:
    profile = _profile("monitor_data", WATER_COLUMNS, row_estimate=5_000)
    result = score_table(profile, profile["quality"], 0.5)
    assert result["breakdown"]["有效数据量"] == 10.0


def test_small_business_time_series_volume_floor() -> None:
    # 540 行监测表：原始 volume 7 -> floor 12
    profile = _profile(
        "rs_outlet_records",
        WATER_COLUMNS,
        row_estimate=540,
        latest="2026-08-01 10:00:00",
        coverage=300.0,
        table_comment="排口监测记录",
    )
    result = score_table(
        profile,
        profile["quality"],
        0.5,
        volume_floor_eligible=True,
    )
    assert result["breakdown"]["有效数据量"] == 12.0
    assert any("volume floor" in warning for warning in result["warnings"])


def test_small_time_series_without_strong_business_no_floor() -> None:
    # system_log + timestamp 不是强业务时序，不得享受 floor
    profile = _profile(
        "system_log",
        ["id", "user", "timestamp", "action", "method", "ip"],
        row_estimate=300,
        latest="2026-08-01 10:00:00",
        coverage=300.0,
        table_comment="系统日志",
        role="日志表",
        time_column="timestamp",
        grain="",
    )
    result = score_table(
        profile,
        profile["quality"],
        0.5,
        volume_floor_eligible=True,
    )
    assert result["breakdown"]["有效数据量"] == 7.0


def test_identity_platform_high_confidence() -> None:
    profile = _profile(
        "sm_user_groupmag",
        ["id", "user_id", "group_id", "username", "role_id", "create_time"],
        table_comment="用户组管理",
        role="业务表",
        time_column="create_time",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "identity_platform"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "sm_user_groupmag")]["proposed_decision"] == "standby"


def test_metadata_registry_high_confidence() -> None:
    profile = _profile(
        "t_metadata_category",
        ["id", "category_id", "category_name", "table_name", "field_name", "data_type"],
        table_comment="元数据目录",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "metadata_registry"
    assert non_biz["confidence"] == 0.95


def test_medium_confidence_workflow_support() -> None:
    profile = _profile(
        "dc_survey_task",
        ["id", "task_id", "task_name", "status", "owner", "create_time"],
        table_comment="调查任务",
        role="业务表",
        time_column="create_time",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "workflow_support"
    assert non_biz["confidence"] == 0.75
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "dc_survey_task")]["proposed_decision"] == "standby"


def test_medium_confidence_location_reference() -> None:
    profile = _profile(
        "yn_s_address_area",
        ["id", "area_code", "area_name", "parent_code", "lng", "lat"],
        table_comment="行政区划",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "location_reference"
    assert non_biz["confidence"] == 0.75


def test_score_null_deduction_ignores_audit_columns() -> None:
    null_rates = {
        "create_by": 1.0,
        "create_time": 1.0,
        "update_by": 1.0,
        "update_time": 1.0,
        "ph": 0.0,
        "cod": 0.0,
    }
    columns = ["create_by", "create_time", "update_by", "update_time", "ph", "cod"]
    profile = _profile("monitor_data", columns, null_rates=null_rates)
    result = score_table(profile, profile["quality"], 0.5)
    assert not any(label == "大量空值" for label, _ in result["deductions"])


def test_score_null_deduction_fires_for_business_columns() -> None:
    null_rates = {name: 0.95 for name in WATER_COLUMNS[:8]}
    profile = _profile("monitor_data", WATER_COLUMNS, null_rates=null_rates)
    result = score_table(profile, profile["quality"], 0.5)
    assert any(label == "大量空值" for label, _ in result["deductions"])


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


def test_proposal_group_with_active_no_longer_forces_pending() -> None:
    profiles = [
        _profile("water_data", WATER_COLUMNS),
        _profile("water_data_old", WATER_COLUMNS[:9], row_estimate=5000),
        _profile("water_data_backup", WATER_COLUMNS[:8], row_estimate=50_000),
    ]
    existing = {
        ("public", "water_data"): {"effective_decision": "active"},
    }
    proposals = compute_proposals(profiles, {}, existing)
    assert proposals[("public", "water_data")]["proposed_decision"] == "active"
    for key, fields in proposals.items():
        assert "替换需人工确认" not in fields["proposed_reason"]
        assert "同组存在正式主表" not in fields["proposed_reason"]
        assert fields["business_group"] == "waterdata"


def test_proposal_group_members_independent_decisions() -> None:
    profiles = [
        _profile("water_data", WATER_COLUMNS, row_estimate=100_000),
        _profile("water_data_old", WATER_COLUMNS[:9], row_estimate=5_000),
        _profile("water_data_backup", WATER_COLUMNS[:8], row_estimate=50_000),
    ]
    proposals = compute_proposals(profiles, {}, {})
    assert proposals[("public", "water_data")]["proposed_decision"] == "active"
    # 同组非最高分表不再被 rank 强制 standby：按独立评分（此处为 pending/standby）
    for key in (("public", "water_data_old"), ("public", "water_data_backup")):
        assert proposals[key]["proposed_decision"] != "active"
        assert "同业务组最高分" not in proposals[key]["proposed_reason"]
    assert proposals[("public", "water_data")]["business_group"] == "waterdata"


def test_proposal_granularity_members_independent() -> None:
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
    decisions = {key: fields["proposed_decision"] for key, fields in proposals.items()}
    # 不同粒度可同时 active，不再因粒度混合强制 pending
    assert decisions[("public", "wm_waterquality_day_records")] == "active"
    assert decisions[("public", "wm_waterquality_month_records")] == "active"
    for fields in proposals.values():
        assert "表时间粒度不一致" not in fields["proposed_reason"]


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
        coverage=None,
        role="业务表",
        time_column="register_date",
        grain="enterprise_id",
        table_comment="企业排污许可档案",
        has_pk=False,
        null_rates={
            "license_no": 0.95,
            "industry_type": 0.95,
            "address": 0.95,
            "contact": 0.95,
        },
    )
    low_columns = ["id"]
    low = _profile(
        "low_quality",
        low_columns,
        row_estimate=50,
        latest=None,
        coverage=None,
        time_column="",
        grain="",
        role="业务表",
        table_comment="低质量测试表",
        has_pk=False,
        null_rates={"id": 1.0},
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


def test_update_interval_unknown_is_neutral() -> None:
    profile = _profile("water_data", WATER_COLUMNS)
    result = score_table(profile, profile["quality"], 0.5)
    assert result["breakdown"]["持续更新迹象"] == 5.0
    assert any("按中性计分" in warning for warning in result["warnings"])


def test_confirmed_empty_to_standby() -> None:
    profile = _profile(
        "empty_business",
        WATER_COLUMNS,
        row_estimate=0,
        sample_count=0,
        latest=None,
        coverage=None,
    )
    proposals = compute_proposals([profile], {}, {})
    fields = proposals[("public", "empty_business")]
    assert fields["proposed_decision"] == "standby"
    assert "confirmed_empty" in fields["proposed_reason"]


def test_unknown_empty_to_pending() -> None:
    profile = _profile(
        "unknown_empty",
        WATER_COLUMNS,
        row_estimate=500,
        sample_count=0,
        latest=None,
        coverage=None,
    )
    proposals = compute_proposals([profile], {}, {})
    fields = proposals[("public", "unknown_empty")]
    assert fields["proposed_decision"] == "pending"
    assert "数据状态未知" in fields["proposed_reason"]


def test_non_business_high_confidence_to_standby() -> None:
    profile = _profile(
        "sm_login_log",
        ["id", "user", "ip", "login_time", "action", "method"],
        table_comment="系统登录日志",
        role="业务表",
        time_column="login_time",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "system_log"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    fields = proposals[("public", "sm_login_log")]
    assert fields["proposed_decision"] == "standby"
    assert "non_business:system_log" in fields["proposed_reason"]


def test_non_business_pure_prefix_not_excluded() -> None:
    # 词表外弱信号（无类别语义词、无特征列）不得自动排除。
    profile = _profile(
        "wm_asset_inventory",
        ["id", "name", "code", "owner", "status"],
        table_comment="资产清单",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["confidence"] <= 0.55
    proposals = compute_proposals([profile], {}, {})
    assert "non_business:" not in proposals[("public", "wm_asset_inventory")][
        "proposed_reason"
    ]


def test_business_counter_limits_non_business_confidence() -> None:
    profile = _profile(
        "wm_raster_info",
        ["id", "tile", "layer", "path", "resolution", "name"],
        table_comment="水质栅格图层信息",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "media_asset"
    assert non_biz["confidence"] == 0.75  # 业务反证封顶，不达 0.9
    assert non_biz["business_counter"] is True
    # 冻结契约：业务反证封顶后仍按中置信降级（最多 standby/pending，不排除、不 active）。
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "wm_raster_info")]["proposed_decision"] == "standby"
    assert "non_business 中置信:media_asset" in proposals[("public", "wm_raster_info")][
        "proposed_reason"
    ]


def test_non_business_single_keyword_not_auto_downgraded() -> None:
    """单一普通关键词（model）不得获得 0.75 并自动降级。"""
    profile = _profile(
        "model_business_result",
        ["id", "result", "name"],
        table_comment="业务结果表",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "model_artifact"
    assert non_biz["confidence"] <= 0.55
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "model_business_result")]["proposed_decision"] == "active"
    assert "non_business" not in proposals[("public", "model_business_result")][
        "proposed_reason"
    ]


def test_non_business_semantic_only_no_downgrade() -> None:
    """只有表名语义（即使多关键词）不自动降级；必须语义 + 结构双证据。"""
    profile = _profile(
        "model_lasso_records",
        ["id", "result", "name"],
        table_comment="lasso 模型记录",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "model_artifact"
    assert non_biz["confidence"] <= 0.55
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "model_lasso_records")]["proposed_decision"] == "active"


def test_non_business_semantic_with_structure_095() -> None:
    """表名语义 + >=2 个类别结构列 -> 0.95 -> standby。"""
    profile = _profile(
        "model_runs",
        ["id", "model_id", "algorithm", "weight"],
        table_comment="模型运行记录表",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "model_artifact"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "model_runs")]["proposed_decision"] == "standby"


def test_non_business_compound_semantic_without_structure_no_downgrade() -> None:
    """复合语义（系统日志）单独命中也不降级；加结构证据才 standby。"""
    profile = _profile(
        "op_log",
        ["id", "title"],
        table_comment="系统日志",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "system_log"
    assert non_biz["confidence"] <= 0.55
    profile_with_structure = _profile(
        "op_log",
        ["id", "user", "request_uri", "method", "title"],
        table_comment="系统日志",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz2 = classify_non_business_evidence(
        profile_with_structure, profile_with_structure["quality"]
    )
    assert non_biz2["role"] == "system_log"
    assert non_biz2["confidence"] == 0.95


def test_model_efdc_structure_evidence_standby() -> None:
    """模型产物：model 语义 + EFDC 网格/结果结构 -> standby。"""
    profile = _profile(
        "model_efdc_output",
        ["id", "efdc_i", "efdc_j", "res_date", "hour", "result_type"],
        table_comment="EFDC 模型输出",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "model_artifact"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "model_efdc_output")]["proposed_decision"] == "standby"


def test_media_uav_structure_evidence_standby() -> None:
    """影像/媒体：uav 语义 + drone/gateway 结构 -> standby。"""
    profile = _profile(
        "wm_uav_info",
        ["id", "drone_sn", "drone_callsign", "gateway_sn", "gateway_callsign"],
        table_comment="无人机信息",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "media_asset"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "wm_uav_info")]["proposed_decision"] == "standby"


def test_platform_identity_structure_evidence_standby() -> None:
    """平台身份：role 语义 + role_name/role_description 结构 -> standby。"""
    profile = _profile(
        "sm_role",
        ["row_id", "role_name", "role_description", "status"],
        table_comment="角色",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] in {"identity_platform", "platform_config"}
    assert non_biz["confidence"] == 0.95


def test_metadata_registry_structure_evidence_standby() -> None:
    """元数据注册：metadata 语义 + layername/scale/server 结构 -> standby。"""
    profile = _profile(
        "t_metadata_vector",
        ["id", "layername", "scale", "server", "xmin", "xmax"],
        table_comment="元数据矢量",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "metadata_registry"
    assert non_biz["confidence"] == 0.95
    proposals = compute_proposals([profile], {}, {})
    assert proposals[("public", "t_metadata_vector")]["proposed_decision"] == "standby"


def test_operation_trace_structure_evidence_standby() -> None:
    """操作轨迹：graphic 语义 + entity_type/operate_type 结构 -> standby。"""
    profile = _profile(
        "graphic_operate_log",
        ["id", "entity_type", "operate_type", "operate_time", "params"],
        table_comment="图形操作日志",
        role="业务表",
        time_column="",
        grain="",
    )
    non_biz = classify_non_business_evidence(profile, profile["quality"])
    assert non_biz["role"] == "operation_trace"
    assert non_biz["confidence"] == 0.95


def test_duplicate_structure_degrades_to_standby() -> None:
    fingerprint = "sha256-structure-a"
    data_fingerprint = "sha256-data-a"
    profiles = [
        _profile(
            "water_data",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=data_fingerprint,
        ),
        _profile(
            "water_data_dup",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=data_fingerprint,
        ),
    ]
    proposals = compute_proposals(profiles, {}, {})
    decisions = {
        key: fields["proposed_decision"] for key, fields in proposals.items()
    }
    assert decisions[("public", "water_data")] == "active"
    assert decisions[("public", "water_data_dup")] == "standby"
    assert "duplicate_structure" in proposals[("public", "water_data_dup")][
        "proposed_reason"
    ]


def test_backup_mirror_degrades_to_standby() -> None:
    fingerprint = "sha256-structure-b"
    data_fingerprint = "sha256-data-b"
    profiles = [
        _profile(
            "water_data",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=data_fingerprint,
        ),
        _profile(
            "water_data_old",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=data_fingerprint,
        ),
    ]
    proposals = compute_proposals(
        profiles,
        {("public", "water_data_old"): 1.0},
        {},
    )
    assert proposals[("public", "water_data")]["proposed_decision"] == "active"
    assert proposals[("public", "water_data_old")]["proposed_decision"] == "standby"
    assert "backup_mirror" in proposals[("public", "water_data_old")][
        "proposed_reason"
    ]


def test_physical_shard_to_standby() -> None:
    fingerprint = "sha256-shard-struct"
    profiles = [
        _profile(
            f"wh_records_{index}",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=f"data-{index}",
        )
        for index in range(1, 6)
    ]
    profiles.append(
        _profile(
            "wh_hour_records",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint="data-unified",
        )
    )
    proposals = compute_proposals(profiles, {}, {})
    assert proposals[("public", "wh_records_1")]["proposed_decision"] == "standby"
    assert "physical_shard" in proposals[("public", "wh_records_1")][
        "proposed_reason"
    ]
    assert proposals[("public", "wh_records_5")]["proposed_decision"] == "standby"
    # 统一入口表不受影响
    assert proposals[("public", "wh_hour_records")]["proposed_decision"] == "active"


def test_physical_shard_two_siblings_different_structure_not_degraded() -> None:
    fingerprint = "sha256-shard-struct"
    profiles = [
        _profile(
            f"wh_records_{index}",
            WATER_COLUMNS,
            structure_fingerprint=f"{fingerprint}-{index}",
            data_fingerprint=f"data-{index}",
        )
        for index in range(1, 3)
    ]
    profiles.append(
        _profile(
            "wh_hour_records",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint="data-unified",
        )
    )
    proposals = compute_proposals(profiles, {}, {})
    assert "physical_shard" not in proposals[("public", "wh_records_1")][
        "proposed_reason"
    ]


def test_physical_shard_two_siblings_identical_structure() -> None:
    fingerprint = "sha256-shard-struct"
    profiles = [
        _profile(
            f"wh_meteorological_records_{index}",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint=f"data-{index}",
        )
        for index in (5, 37)
    ]
    profiles.append(
        _profile(
            "wh_meteorological_hour_records",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint="data-unified",
        )
    )
    proposals = compute_proposals(profiles, {}, {})
    assert "physical_shard" in proposals[("public", "wh_meteorological_records_5")][
        "proposed_reason"
    ]
    assert "physical_shard" in proposals[("public", "wh_meteorological_records_37")][
        "proposed_reason"
    ]


def test_physical_shard_single_digit_table_not_degraded() -> None:
    fingerprint = "sha256-shard-struct"
    profiles = [
        _profile(
            "wh_meteorological_records_5",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint="data-5",
        ),
        _profile(
            "wh_meteorological_hour_records",
            WATER_COLUMNS,
            structure_fingerprint=fingerprint,
            data_fingerprint="data-unified",
        ),
    ]
    proposals = compute_proposals(profiles, {}, {})
    assert "physical_shard" not in proposals[("public", "wh_meteorological_records_5")][
        "proposed_reason"
    ]


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

"""Quality Scoring 与 Proposed Decision 分层语义回归。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_proposed_decision import (
    ProposalConstraint,
    decide_proposal,
)
from backend.data_source_eligibility import evaluate_table_eligibility
from backend.data_source_quality_scoring import score_table


def _eligibility(status: str, category: str = "test") -> dict:
    return {
        "status": status,
        "category": category,
        "confidence": 0.9,
        "reasons": ["test evidence"],
    }


def _quality(score: float, *, unknown: bool = False) -> dict:
    return {
        "score": score,
        "confidence": 1.0,
        "warnings": [],
        "quality_unknown": unknown,
        "can_propose_active": not unknown,
        "confirmed_empty": False,
    }


def _profile(table: str, comment: str) -> tuple[dict, dict]:
    columns = [
        "id", "station_id", "monitor_time", "value", "status",
        "area_code", "name", "type",
    ]
    quality = {
        "queryable_column_count": len(columns),
        "row_estimate": 100_000,
        "sample_row_count": 200,
        "latest_data_at": "2026-08-01 00:00:00",
        "time_coverage_days": 365,
        "freshness_confidence": 1.0,
        "has_primary_key": True,
        "has_unique_key": False,
        "duplicate_key_ratio": 0.0,
        "observed_update_interval": "hour",
        "skipped_by_total_timeout": False,
        "table_comment": comment,
    }
    profile = {
        "table": table,
        "table_comment": comment,
        "table_role_candidate": "事实表",
        "time_column_candidate": "monitor_time",
        "columns": [
            {"column": column, "sample_null_rate": 0.0}
            for column in columns
        ],
        "quality": quality,
        "error": "",
    }
    return profile, quality


def test_proposed_priority_matrix() -> None:
    high = _quality(95)
    assert decide_proposal(_eligibility("ineligible"), high).decision == "standby"
    assert decide_proposal(_eligibility("unknown"), high).decision == "pending"
    assert decide_proposal(_eligibility("eligible"), high).decision == "active"
    assert decide_proposal(_eligibility("eligible"), _quality(70)).decision == "pending"
    assert decide_proposal(_eligibility("eligible"), _quality(55)).decision == "standby"
    assert (
        decide_proposal(_eligibility("eligible"), _quality(95, unknown=True)).decision
        == "pending"
    )


def test_deterministic_constraints_override_high_quality() -> None:
    for kind in ("physical_shard", "duplicate_structure", "backup_mirror"):
        result = decide_proposal(
            _eligibility("eligible"),
            _quality(95),
            [ProposalConstraint(kind, "deterministic evidence")],
        )
        assert result.decision == "standby", (kind, result)
        assert any(f"constraint:{kind}" in reason for reason in result.reasons)


def test_quality_score_is_independent_of_business_taxonomy() -> None:
    water_profile, water_quality = _profile("water_monitor", "监测数据")
    system_profile, system_quality = _profile("sys_log", "监测数据")
    water = score_table(water_profile, water_quality, 0.8)
    system = score_table(system_profile, system_quality, 0.8)
    assert water["score"] == system["score"]
    assert water["breakdown"] == system["breakdown"]


def test_layer_outputs_never_contain_formal_governance_fields() -> None:
    profile, quality = _profile("water_monitor", "水质监测数据")
    scored = score_table(profile, quality, 0.8)
    proposal = decide_proposal(_eligibility("eligible"), scored)
    assert proposal.decision == "active"
    for forbidden in (
        "effective_decision", "selected_scope", "runtime_revision", "formal_assets"
    ):
        assert forbidden not in scored
        assert not hasattr(proposal, forbidden)


def test_profile_failure_and_timeout_never_become_active() -> None:
    failed_profile, failed_quality = _profile("water_monitor", "水质监测数据")
    failed_profile["error"] = "sample failed"
    timeout_profile, timeout_quality = _profile("water_hour", "水质小时数据")
    timeout_quality["skipped_by_total_timeout"] = True

    for profile, quality in (
        (failed_profile, failed_quality),
        (timeout_profile, timeout_quality),
    ):
        eligibility = evaluate_table_eligibility(profile, quality).as_dict()
        scored = score_table(profile, quality, 0.8)
        proposal = decide_proposal(eligibility, scored)
        assert eligibility["status"] == "unknown"
        assert scored["quality_unknown"] is True
        assert proposal.decision == "pending"


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"Quality + Proposed tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()

"""自动治理 v2 的纯 Policy Promotion 层。

只把 Proposed + availability 确定性映射为 Effective，不访问数据库，
不构建 selected_scope，也不触发资产生成或发布。
"""

from __future__ import annotations

from dataclasses import dataclass


POLICY_SOURCE = "automatic_policy_v2"
VALID_PROPOSED_DECISIONS = frozenset({"active", "pending", "standby"})
VALID_AVAILABILITY = frozenset({"present", "missing"})


class PolicyPromotionError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyPromotionResult:
    effective_decision: str
    decision_source: str
    decision_reason: str


def promote_policy(
    proposed_decision: str,
    availability_status: str,
) -> PolicyPromotionResult:
    """按 fail-closed 契约生成单表最终 Effective Decision。"""
    proposed = str(proposed_decision or "").strip()
    availability = str(availability_status or "").strip()
    if proposed not in VALID_PROPOSED_DECISIONS:
        raise PolicyPromotionError(
            f"非法 proposed_decision: {proposed or '<empty>'}"
        )
    if availability not in VALID_AVAILABILITY:
        raise PolicyPromotionError(
            f"非法 availability_status: {availability or '<empty>'}"
        )

    if availability == "missing":
        effective = "standby"
        rule = "missing_fail_closed"
    elif proposed == "active":
        effective = "active"
        rule = "active_present_promoted"
    elif proposed == "pending":
        effective = "standby"
        rule = "pending_fail_closed"
    else:
        effective = "standby"
        rule = "standby_preserved"

    return PolicyPromotionResult(
        effective_decision=effective,
        decision_source=POLICY_SOURCE,
        decision_reason=(
            f"{POLICY_SOURCE}:{rule};proposed={proposed};"
            f"availability={availability};effective={effective}"
        ),
    )

"""自动治理 v2 的 Proposed Decision 纯决策层。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


ACTIVE_MIN_SCORE = float(os.getenv("DATA_SOURCE_ACTIVE_MIN_SCORE", "80"))
PENDING_MIN_SCORE = float(os.getenv("DATA_SOURCE_PENDING_MIN_SCORE", "60"))


@dataclass(frozen=True)
class ProposalConstraint:
    kind: str
    detail: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class ProposedDecisionResult:
    decision: str
    reasons: tuple[str, ...]


def decide_proposal(
    eligibility: Mapping[str, Any],
    quality: Mapping[str, Any],
    constraints: Sequence[ProposalConstraint] = (),
) -> ProposedDecisionResult:
    """按冻结优先级组合 Eligibility、Quality 与确定性跨表约束。"""
    score = float(quality.get("score") or 0.0)
    reasons = [f"质量评分 {score:g}"]
    status = str(eligibility.get("status") or "unknown")
    category = str(eligibility.get("category") or "unknown")
    evidence = "、".join(str(item) for item in eligibility.get("reasons") or [])
    reasons.append(
        f"eligibility:{status}/{category}, "
        f"confidence={float(eligibility.get('confidence') or 0):g}"
        + (f", evidence={evidence}" if evidence else "")
    )

    if status == "ineligible":
        return ProposedDecisionResult("standby", tuple(reasons))
    if status != "eligible":
        return ProposedDecisionResult("pending", tuple(reasons))

    if constraints:
        for constraint in constraints:
            reasons.append(
                f"constraint:{constraint.kind}, confidence={constraint.confidence:g}"
                + (f", evidence={constraint.detail}" if constraint.detail else "")
            )
        return ProposedDecisionResult("standby", tuple(reasons))

    if bool(quality.get("confirmed_empty")):
        reasons.append("confirmed_empty（确认 0 行空表）")
        return ProposedDecisionResult("standby", tuple(reasons))
    if bool(quality.get("quality_unknown")) or not bool(
        quality.get("can_propose_active")
    ):
        reasons.extend(str(item) for item in quality.get("warnings") or [])
        reasons.append("关键质量证据 unknown")
        return ProposedDecisionResult("pending", tuple(reasons))

    if score >= ACTIVE_MIN_SCORE:
        decision = "active"
    elif score >= PENDING_MIN_SCORE:
        decision = "pending"
    else:
        decision = "standby"
    reasons.extend(str(item) for item in quality.get("warnings") or [])
    return ProposedDecisionResult(decision, tuple(reasons))


def apply_constraint(
    reasons: list[str],
    constraint: ProposalConstraint,
) -> tuple[str, list[str]]:
    """对已有 proposal 应用组级确定性降级，不修改 Quality Score。"""
    next_reasons = list(reasons)
    next_reasons.append(
        f"constraint:{constraint.kind}, confidence={constraint.confidence:g}"
        + (f", evidence={constraint.detail}" if constraint.detail else "")
    )
    return "standby", next_reasons

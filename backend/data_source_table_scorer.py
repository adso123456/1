"""表准入审核阶段 B：确定性评分 + 同业务表分组。

本模块只生成建议字段：
  proposed_decision / proposed_score / proposed_reason
  business_group / group_confidence / compared_tables_json / group_reason

严格遵守阶段 B 边界：
  不修改 effective_decision；
  不覆盖 selected_scope；
  不调用 prepare()；
  不生成正式 Metadata / DDL / Chroma；
  不增加 runtime_revision。

评分结果不能直接决定正式范围：同业务组存在正式主表、组内分差过小、
分组置信度不足、关键指标未知、粒度不一致、历史/实时混合等情况，
一律强制进入 pending，等待阶段 D 人工确认。
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# 阈值（与评审方案一致，可通过环境变量微调）
# ---------------------------------------------------------------------------

ACTIVE_MIN_SCORE = float(os.getenv("DATA_SOURCE_ACTIVE_MIN_SCORE", "80"))
PENDING_MIN_SCORE = float(os.getenv("DATA_SOURCE_PENDING_MIN_SCORE", "60"))
GROUP_MIN_GAP = float(os.getenv("DATA_SOURCE_GROUP_MIN_GAP", "5"))
GROUP_CONFIDENCE_THRESHOLD = float(
    os.getenv("DATA_SOURCE_GROUP_CONFIDENCE_THRESHOLD", "0.55")
)
MIN_CONFIDENCE_FOR_ACTIVE = float(
    os.getenv("DATA_SOURCE_MIN_CONFIDENCE_FOR_ACTIVE", "0.55")
)

# 时间类数据表：缺少最新数据时间/可用键属于关键指标未知，不能建议 active。
_TIME_DATA_ROLES = {"事实表", "日志表"}
# 静态表：允许没有最新数据时间，也不按新鲜度重罚。
_STATIC_ROLES = {"字典表", "配置表"}

# 明显备份/临时/历史标记：只扣分，不直接 blocked（旧表恢复写入后仍参与重评）。
_BACKUP_MARKS_CN = ("历史", "备份", "临时", "旧")
_BACKUP_MARKS_EN = ("old", "backup", "copy", "tmp", "bak")

_HISTORY_MARKS = ("log", "日志", "history", "历史", "audit", "流水")

# 时间粒度标记：同一业务组内出现多种粒度（日/时/月/年）视为需人工确认。
_GRANULARITY_MARKS = (
    "minute", "hour", "day", "month", "year", "旬",
    "分钟", "小时", "日报", "月报", "年报",
)


def _normalize_name(name: str) -> str:
    """表名归一化：去符号、去技术前缀/版本号/备份后缀，用于相似度比较。"""
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(name).lower())
    text = re.sub(r"^(t|tb|tbl|v)", "", text)
    text = re.sub(r"v?\d+$", "", text)
    text = re.sub(r"(bak|backup|copy|old|tmp)$", "", text)
    return text


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    previous = list(range(len(a) + 1))
    for index, char_b in enumerate(b, start=1):
        current = [index]
        for j in range(1, len(a) + 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (a[j - 1] != char_b),
                )
            )
        previous = current
    return previous[len(a)]


def _name_similarity(a: str, b: str) -> float:
    """表名相似度：0.0-1.0。相等 1.0，包含 0.7，编辑距离接近 0.5/0.3。"""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return 0.7
    distance = _levenshtein(na, nb)
    if distance <= 2:
        return 0.55
    if distance <= max(len(na), len(nb)) * 0.25:
        return 0.35
    return 0.0


def _column_names(profile: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in profile.get("columns") or []:
        name = str(item.get("column") or "")
        if name:
            names.add(name.lower())
    return names


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _time_signal(left_time: str, right_time: str) -> float:
    if not left_time or not right_time:
        return 0.0
    if _normalize_name(left_time) == _normalize_name(right_time):
        return 1.0
    return 0.3


def _is_backup_mark(table_name: str, table_comment: str) -> bool:
    text = f"{table_name} {table_comment}".lower()
    if any(mark in text for mark in _BACKUP_MARKS_CN):
        return True
    for mark in _BACKUP_MARKS_EN:
        if re.search(rf"(?<![a-z0-9]){re.escape(mark)}(?![a-z0-9])", text):
            return True
    return False


def _is_history_like(table_name: str, role: str) -> bool:
    if role == "日志表":
        return True
    text = str(table_name).lower()
    return any(mark in text for mark in _HISTORY_MARKS)


def group_tables(
    profiles: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """把表达同一业务对象的表分组（L1 候选 + L2 确认）。

    只使用受限画像中的结构信息（列集合、时间列、角色、粒度），
    不读取原始样本值。返回的组带有置信度与说明。
    """
    items: list[dict[str, Any]] = []
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if not schema or not table:
            continue
        items.append(
            {
                "key": (schema, table),
                "name": table,
                "columns": _column_names(profile),
                "time_column": str(profile.get("time_column_candidate") or ""),
                "role": str(profile.get("table_role_candidate") or ""),
                "grain": str(profile.get("grain_candidate") or ""),
            }
        )

    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    pair_scores: dict[tuple[int, int], dict[str, float]] = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            left, right = items[i], items[j]
            if not left["columns"] or not right["columns"]:
                continue
            name_sim = _name_similarity(left["name"], right["name"])
            jaccard = _jaccard(left["columns"], right["columns"])
            time_sig = _time_signal(left["time_column"], right["time_column"])
            pair_conf = round(
                min(1.0, 0.4 * name_sim + 0.4 * jaccard + 0.2 * time_sig),
                3,
            )
            # 只用强边建组，避免弱链接把大量无关表连成巨型组件，
            # 再反过来稀释组内置信度。
            # 强边条件：同名/版本族（允许低字段重合）、
            # 包含关系且字段重合足够、或字段结构高度一致。
            strong_edge = (
                (name_sim == 1.0 and jaccard >= 0.15)
                or (name_sim >= 0.7 and jaccard >= 0.35)
                or (jaccard >= 0.7 and time_sig >= 0.3)
            )
            if strong_edge and pair_conf >= GROUP_CONFIDENCE_THRESHOLD:
                pair_scores[(i, j)] = {
                    "confidence": pair_conf,
                    "jaccard": jaccard,
                }
                union(i, j)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        components[find(index)].append(index)

    groups: list[dict[str, Any]] = []
    for indexes in components.values():
        if len(indexes) < 2:
            continue
        member_keys = [items[index]["key"] for index in indexes]
        confidences = [
            pair_scores[(i, j)]["confidence"]
            for i in indexes
            for j in indexes
            if i < j and (i, j) in pair_scores
        ]
        jaccards = [
            pair_scores[(i, j)]["jaccard"]
            for i in indexes
            for j in indexes
            if i < j and (i, j) in pair_scores
        ]
        if not confidences:
            continue
        confidence = round(min(confidences), 3)
        min_jaccard = round(min(jaccards), 3)
        # 组名：取组内归一化表名的最长公共前缀，过短则用最高分表名。
        normalized = [
            _normalize_name(items[index]["name"]) for index in indexes
        ]
        group_name = _common_prefix(normalized)
        if len(group_name) < 6:
            group_name = normalized[0] or member_keys[0][1]
        reason_parts = [
            f"成员 {len(member_keys)} 张",
            f"字段 Jaccard 最低 {min_jaccard:.2f}",
            f"分组置信度 {confidence:.2f}",
        ]
        time_columns = {items[index]["time_column"] for index in indexes}
        if len(time_columns) == 1 and next(iter(time_columns)):
            reason_parts.append("时间列一致")
        groups.append(
            {
                "group_name": group_name,
                "members": member_keys,
                "confidence": confidence,
                "min_jaccard": min_jaccard,
                "reason": "；".join(reason_parts),
            }
        )
    return groups


def _common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    prefix = names[0]
    for name in names[1:]:
        length = 0
        for left, right in zip(prefix, name):
            if left != right:
                break
            length += 1
        prefix = prefix[:length]
        if not prefix:
            break
    return prefix


def _granularity_markers(table_name: str) -> set[str]:
    lowered = str(table_name).lower()
    return {mark for mark in _GRANULARITY_MARKS if mark in lowered}


def score_table(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
    comment_ratio: float = 0.0,
) -> dict[str, Any]:
    """确定性评分：只依赖结构/画像指标，不调用 LLM，结果可复现。

    总分 100：
      完整度 25 / 数据新鲜度 20 / 有效数据量 15 / 时间覆盖 10
      主键与唯一性 10 / 字段注释与语义 10 / 持续更新迹象 5 / 索引质量 5

    扣分：大量空值 -15、缺核心字段 -30/-10、明显备份/临时表 -10。
    长期没有新数据 -20 只在更新周期可信时执行（V1 更新周期恒未知，暂缓）。
    """
    warnings: list[str] = []
    breakdown: dict[str, float] = {}
    deductions: list[tuple[str, float]] = []

    table_name = str(profile.get("table") or "")
    role = str(profile.get("table_role_candidate") or "")
    table_comment = str(
        quality.get("table_comment") or profile.get("table_comment") or ""
    )
    qcols = int(quality.get("queryable_column_count") or 0)
    row_estimate = quality.get("row_estimate")
    sample_count = int(quality.get("sample_row_count") or 0)
    latest = quality.get("latest_data_at")
    freshness_confidence = float(quality.get("freshness_confidence") or 0.0)
    time_coverage = quality.get("time_coverage_days")
    has_primary_key = bool(quality.get("has_primary_key"))
    has_unique_key = bool(quality.get("has_unique_key"))
    duplicate_ratio = quality.get("duplicate_key_ratio")
    null_rate = quality.get("sample_null_rate")
    error = str(profile.get("error") or "")
    skipped = bool(quality.get("skipped_by_total_timeout"))
    time_column = str(profile.get("time_column_candidate") or "")
    is_static = role in _STATIC_ROLES

    # 1. 完整度 25
    if qcols >= 8:
        completeness = 25.0
    elif qcols >= 5:
        completeness = 20.0
    elif qcols >= 3:
        completeness = 14.0
    elif qcols >= 1:
        completeness = 8.0
    else:
        completeness = 0.0
    breakdown["完整度"] = completeness

    # 2. 数据新鲜度 20
    if latest:
        if freshness_confidence >= 0.5:
            # 预留：后续有可信更新周期时再按新旧分档。
            freshness = 20.0
        else:
            freshness = 14.0 if not is_static else 18.0
            warnings.append("更新周期未知，新鲜度按中性计分，未做新旧扣分")
    else:
        freshness = 16.0 if is_static else 5.0
        warnings.append("缺少最新数据时间，新鲜度按低分计")
    breakdown["数据新鲜度"] = freshness

    # 3. 有效数据量 15
    if row_estimate is None:
        volume = 4.0 if sample_count > 0 else 0.0
        warnings.append("行数估算未知，按样本量计分")
    elif row_estimate >= 100_000:
        volume = 15.0
    elif row_estimate >= 10_000:
        volume = 13.0
    elif row_estimate >= 1_000:
        volume = 10.0
    elif row_estimate >= 100:
        volume = 7.0
    elif row_estimate >= 1:
        volume = 4.0
    else:
        volume = 0.0
    breakdown["有效数据量"] = volume

    # 4. 时间覆盖连续性 10
    if time_coverage is None:
        time_coverage_score = 0.0
        if latest:
            warnings.append("时间覆盖范围未知")
    elif time_coverage >= 365:
        time_coverage_score = 10.0
    elif time_coverage >= 90:
        time_coverage_score = 8.0
    elif time_coverage >= 30:
        time_coverage_score = 6.0
    elif time_coverage >= 7:
        time_coverage_score = 4.0
    else:
        time_coverage_score = 2.0
    breakdown["时间覆盖连续性"] = time_coverage_score

    # 5. 主键与唯一性 10
    key_score = (5.0 if has_primary_key else 0.0) + (
        2.0 if has_unique_key else 0.0
    )
    if duplicate_ratio == "unknown" or duplicate_ratio is None:
        if not has_primary_key and not has_unique_key:
            warnings.append("无可用键，重复键比例 unknown（未按质量差扣分）")
        else:
            warnings.append("重复键比例未知")
    elif duplicate_ratio == 0:
        key_score += 3.0
    else:
        key_score += 1.0
    breakdown["主键与唯一性"] = key_score

    # 6. 字段注释与语义清晰度 10
    comment_score = (5.0 if table_comment else 0.0) + round(
        5.0 * max(0.0, min(1.0, float(comment_ratio or 0.0))),
        2,
    )
    breakdown["字段注释与语义"] = comment_score

    # 7. 持续更新迹象 5
    if quality.get("observed_update_interval"):
        update_score = 5.0
    elif latest:
        update_score = 3.0
        warnings.append("更新周期未知，持续更新迹象按低分计")
    else:
        update_score = 0.0
    breakdown["持续更新迹象"] = update_score

    # 8. 索引质量 5
    breakdown["索引质量"] = 5.0 if (has_primary_key or has_unique_key) else 0.0

    score = sum(breakdown.values())

    # 扣分项（全部有明确依据，且不把"无法计算"当作质量差）
    if null_rate is not None:
        if null_rate >= 0.5:
            deductions.append(("大量空值", 15.0))
        elif null_rate >= 0.3:
            deductions.append(("大量空值", 10.0))
        elif null_rate >= 0.1:
            deductions.append(("大量空值", 5.0))
    # 长期没有新数据：更新周期可信时才扣分；V1 恒为未知，只展示不扣分。
    if qcols == 0:
        deductions.append(("缺少可用的业务字段", 30.0))
    elif role in _TIME_DATA_ROLES and not time_column:
        deductions.append(("数据表缺少时间类核心字段", 10.0))
    if _is_backup_mark(table_name, table_comment):
        deductions.append(("明显备份/临时/历史表", 10.0))

    for label, amount in deductions:
        warnings.append(f"{label}：-{amount:g}")
        score -= amount
    score = round(max(0.0, min(100.0, score)), 2)

    # 置信度：关键指标未知会降低置信，且不能建议 active。
    confidence = 1.0
    if skipped:
        confidence -= 0.35
    if error:
        confidence -= 0.30
    if sample_count == 0:
        confidence -= 0.25
    if row_estimate is None:
        confidence -= 0.10
    if (
        (duplicate_ratio == "unknown" or duplicate_ratio is None)
        and not has_primary_key
        and not has_unique_key
    ):
        confidence -= 0.15
    if latest is None and role in _TIME_DATA_ROLES:
        confidence -= 0.20
    confidence = round(max(0.0, confidence), 2)

    critical: list[str] = []
    if skipped:
        critical.append("表画像被总超时跳过")
    if error:
        critical.append("受限样本读取失败")
    if sample_count == 0:
        critical.append("无样本数据（空表或无法画像）")
    if latest is None and role in _TIME_DATA_ROLES:
        critical.append("数据表缺少最新数据时间")
    if (
        role in _TIME_DATA_ROLES
        and (duplicate_ratio == "unknown" or duplicate_ratio is None)
        and not has_primary_key
        and not has_unique_key
    ):
        critical.append("数据表无可用键且重复键比例 unknown")

    can_propose_active = (
        confidence >= MIN_CONFIDENCE_FOR_ACTIVE and not critical
    )
    return {
        "score": score,
        "breakdown": breakdown,
        "deductions": deductions,
        "warnings": warnings,
        "confidence": confidence,
        "can_propose_active": can_propose_active,
    }


def compute_proposals(
    profiles: list[Mapping[str, Any]],
    comment_ratios: Mapping[tuple[str, str], float] | None = None,
    existing_reviews: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """生成全部 present 表的建议字段（阶段 B 唯一写入入口）。

    强制 pending（即使分数高）：
      与当前 active 表同业务组 / 建议替换主表 / 组内分差过小 /
      分组置信度不足 / 关键质量指标 unknown / 粒度不一致 /
      历史表与实时表混合 / 可能需联合使用。
    """
    comment_ratios = comment_ratios or {}
    existing_reviews = existing_reviews or {}
    profiles_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    scored: dict[tuple[str, str], dict[str, Any]] = {}
    for profile in profiles:
        schema = str(profile.get("schema") or "")
        table = str(profile.get("table") or "")
        if not schema or not table:
            continue
        key = (schema, table)
        profiles_by_key[key] = profile
        scored[key] = score_table(
            profile,
            profile.get("quality") or {},
            comment_ratios.get(key, 0.0),
        )

    active_keys = {
        key
        for key, review in existing_reviews.items()
        if str(review.get("effective_decision") or "") == "active"
    }
    groups = group_tables(profiles)
    confirmed = [
        group for group in groups
        if group["confidence"] >= GROUP_CONFIDENCE_THRESHOLD
    ]
    grouped_keys: set[tuple[str, str]] = set()
    updates: dict[tuple[str, str], dict[str, Any]] = {}

    for group in confirmed:
        members = sorted(
            group["members"],
            key=lambda key: scored.get(key, {}).get("score", 0.0),
            reverse=True,
        )
        grouped_keys.update(members)
        group_name = group["group_name"]
        group_confidence = group["confidence"]
        group_reason = group["reason"]
        has_active = any(key in active_keys for key in members)
        top_score = scored.get(members[0], {}).get("score", 0.0)
        second_score = (
            scored.get(members[1], {}).get("score", 0.0)
            if len(members) > 1
            else None
        )
        gap_small = (
            second_score is not None
            and (top_score - second_score) < GROUP_MIN_GAP
        )
        concrete_grains = {
            str(profiles_by_key[key].get("grain_candidate") or "")
            for key in members
        } - {"", "待语义确认"}
        grain_mixed = len(concrete_grains) > 1
        granularity_markers: set[str] = set()
        for key in members:
            granularity_markers.update(
                _granularity_markers(
                    str(profiles_by_key[key].get("table") or "")
                )
            )
        granularity_mixed = len(granularity_markers) > 1
        grain_mixed = grain_mixed or granularity_mixed
        history_flags = {
            _is_history_like(
                str(profiles_by_key[key].get("table") or ""),
                str(profiles_by_key[key].get("table_role_candidate") or ""),
            )
            for key in members
        }
        live_flags = {
            str(profiles_by_key[key].get("table_role_candidate") or "")
            in ("事实表", "业务表")
            for key in members
        }
        history_mixed = True in history_flags and True in live_flags

        decisions: dict[tuple[str, str], tuple[str, list[str]]] = {}
        for rank, key in enumerate(members):
            scored_item = scored.get(key, {})
            score = scored_item.get("score", 0.0)
            can_active = bool(scored_item.get("can_propose_active"))
            reason_parts = [f"评分 {score:g}"]
            force_pending = (
                group_confidence < GROUP_CONFIDENCE_THRESHOLD
                or has_active
                or gap_small
                or grain_mixed
                or history_mixed
                or not can_active
            )
            if group_confidence < GROUP_CONFIDENCE_THRESHOLD:
                reason_parts.append("分组置信度不足")
            if has_active:
                reason_parts.append("同组存在正式主表，替换需人工确认")
            if gap_small:
                reason_parts.append("组内最高分差距过小")
            if grain_mixed:
                if granularity_mixed:
                    reason_parts.append(
                        "表时间粒度不一致（"
                        + "/".join(sorted(granularity_markers))
                        + "）"
                    )
                else:
                    reason_parts.append("表粒度不一致")
            if history_mixed:
                reason_parts.append("历史表与实时表混合，可能需联合使用")
            if not can_active:
                reason_parts.extend(scored_item.get("warnings", []))

            if rank == 0 and not force_pending:
                decision = "active"
            elif force_pending:
                decision = "pending"
            else:
                decision = "standby"
                top_name = str(
                    profiles_by_key.get(members[0], {}).get("table") or ""
                )
                reason_parts.append(
                    f"同业务组最高分为 {top_name}（{top_score:g} 分），建议候补"
                )
            decisions[key] = (decision, reason_parts)

        compared = [
            {
                "schema_name": key[0],
                "table_name": key[1],
                "score": scored.get(key, {}).get("score", 0.0),
                "proposed_decision": decisions[key][0],
            }
            for key in members
        ]
        compared_json = json.dumps(compared, ensure_ascii=False)
        for key, (decision, reason_parts) in decisions.items():
            updates[key] = {
                "business_group": group_name,
                "group_confidence": group_confidence,
                "compared_tables_json": compared_json,
                "group_reason": group_reason,
                "proposed_decision": decision,
                "proposed_score": scored.get(key, {}).get("score", 0.0),
                "proposed_reason": "；".join(reason_parts),
            }

    # 未进入任何确认分组的表：按分数阈值单独建议。
    for key, scored_item in scored.items():
        if key in grouped_keys:
            continue
        score = scored_item.get("score", 0.0)
        can_active = bool(scored_item.get("can_propose_active"))
        reason_parts = [f"评分 {score:g}"]
        reason_parts.extend(scored_item.get("warnings", []))
        if can_active and score >= ACTIVE_MIN_SCORE:
            decision = "active"
        elif not can_active:
            decision = "pending"
            reason_parts.append("关键质量指标 unknown，需人工确认")
        elif score >= PENDING_MIN_SCORE:
            decision = "pending"
        else:
            decision = "standby"
        updates[key] = {
            "business_group": "",
            "group_confidence": 0.0,
            "compared_tables_json": "[]",
            "group_reason": "",
            "proposed_decision": decision,
            "proposed_score": score,
            "proposed_reason": "；".join(reason_parts),
        }
    return updates

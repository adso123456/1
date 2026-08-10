"""自动治理 v2 的纯数据质量评分层。

前提是调用方已经独立完成 Eligibility 判断。本模块不判断业务归属，
不输出 active/pending/standby，也不处理分片、重复、备份或发布状态。
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping


MIN_CONFIDENCE_FOR_ACTIVE = float(
    os.getenv("DATA_SOURCE_MIN_CONFIDENCE_FOR_ACTIVE", "0.55")
)
_TIME_DATA_ROLES = {"事实表", "日志表"}
_AUDIT_COLUMN_MARKS = (
    "create_by", "created_by", "create_time", "created_at",
    "update_by", "updated_by", "update_time", "updated_at",
    "modify_by", "modify_time", "delete_flag", "is_deleted",
    "del_flag", "deleted_at",
)
_AUDIT_TIME_COLUMNS = (
    "create_time", "created_at", "created_time",
    "update_time", "updated_at", "updated_time",
    "modify_time", "modified_at", "modified_time",
    "delete_time", "deleted_at", "import_time", "ingest_time", "sync_time",
)
_TIME_TYPE_TOKENS = (
    "date", "time", "datetime", "timestamp", "year", "month", "day",
    "hour", "at", "on",
)
_TIME_TYPE_CN = ("时间", "日期", "年月", "年份", "月份")
_BUSINESS_TIME_PREFIX_TOKENS = (
    "monitor", "monitoring", "sampling", "sample", "measure", "measurement",
    "observe", "observation", "record", "report", "stat", "collect",
    "collection", "data",
)
_BUSINESS_TIME_CN = (
    "监测时间", "采样时间", "观测时间", "数据时间", "记录时间",
    "测量时间", "监测日期", "采样日期", "观测日期",
)


def _tokenize_column_name(column: str) -> list[str]:
    text = str(column or "")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return [
        part
        for part in re.split(r"[^0-9a-z\u4e00-\u9fff]+", text.lower())
        if part
    ]


def _is_audit_time_column(column: str) -> bool:
    name = str(column or "").lower()
    return any(mark in name for mark in _AUDIT_TIME_COLUMNS)


def _looks_time_column(column: str) -> bool:
    name = str(column or "")
    if any(mark in name for mark in _TIME_TYPE_CN):
        return True
    return any(token in _TIME_TYPE_TOKENS for token in _tokenize_column_name(name))


def _business_time_column(column: str) -> bool:
    if _is_audit_time_column(column):
        return False
    name = str(column or "")
    if any(mark in name for mark in _BUSINESS_TIME_CN):
        return True
    tokens = _tokenize_column_name(name)
    return (
        any(token in _TIME_TYPE_TOKENS for token in tokens)
        and any(token in _BUSINESS_TIME_PREFIX_TOKENS for token in tokens)
    )


def _is_time_series_like(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> bool:
    del quality
    return any(
        _business_time_column(str(item.get("column") or ""))
        for item in (profile.get("columns") or [])
    )


def _mostly_null_queryable_ratio(profile: Mapping[str, Any]) -> float:
    columns = profile.get("columns") or []
    queryable = [
        column
        for column in columns
        if not any(
            mark in str(column.get("column") or "").lower()
            for mark in _AUDIT_COLUMN_MARKS
        )
    ]
    if not queryable:
        return 0.0
    mostly_null = [
        column
        for column in queryable
        if (column.get("sample_null_rate") or 0) >= 0.8
    ]
    return len(mostly_null) / len(queryable)


def score_table(
    profile: Mapping[str, Any],
    quality: Mapping[str, Any],
    comment_ratio: float = 0.0,
) -> dict[str, Any]:
    """只根据结构和画像指标计算可复现的 Quality Score。"""
    warnings: list[str] = []
    breakdown: dict[str, float] = {}
    deductions: list[tuple[str, float]] = []

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
    error = str(profile.get("error") or "")
    skipped = bool(quality.get("skipped_by_total_timeout"))
    time_column = str(profile.get("time_column_candidate") or "")
    is_time_series = _is_time_series_like(profile, quality)

    if qcols >= 8:
        breakdown["完整度"] = 25.0
    elif qcols >= 5:
        breakdown["完整度"] = 20.0
    elif qcols >= 3:
        breakdown["完整度"] = 14.0
    elif qcols >= 1:
        breakdown["完整度"] = 8.0
    else:
        breakdown["完整度"] = 0.0

    if not is_time_series:
        freshness = 20.0
        if latest is None or freshness_confidence < 0.5:
            warnings.append("非时序表，新鲜度按中性计分")
    elif latest:
        freshness = 20.0
        if freshness_confidence < 0.5:
            warnings.append("更新周期未知，新鲜度按中性计分，未做新旧扣分")
    else:
        freshness = 5.0
        warnings.append("缺少最新数据时间，新鲜度按低分计")
    breakdown["数据新鲜度"] = freshness

    if not is_time_series:
        if row_estimate is None:
            volume = 12.0 if sample_count > 0 else 0.0
            if sample_count > 0:
                warnings.append("行数估算未知，已确认存在可查询数据")
        else:
            volume = 15.0 if row_estimate >= 1 else 0.0
    elif row_estimate is None:
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

    if not is_time_series:
        coverage_score = 10.0
        if time_coverage is None:
            warnings.append("非时序表，时间覆盖按中性计分")
    elif time_coverage is None:
        coverage_score = 0.0
        if latest:
            warnings.append("时间覆盖范围未知")
    elif time_coverage >= 365:
        coverage_score = 10.0
    elif time_coverage >= 90:
        coverage_score = 8.0
    elif time_coverage >= 30:
        coverage_score = 6.0
    elif time_coverage >= 7:
        coverage_score = 4.0
    else:
        coverage_score = 2.0
    breakdown["时间覆盖连续性"] = coverage_score

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

    breakdown["字段注释与语义"] = (
        5.0 if table_comment else 0.0
    ) + round(5.0 * max(0.0, min(1.0, float(comment_ratio or 0.0))), 2)

    observed_interval = bool(quality.get("observed_update_interval"))
    if not is_time_series:
        update_score = 5.0
        if not observed_interval:
            warnings.append("非时序表，持续更新按中性计分")
    elif observed_interval or latest:
        update_score = 5.0
        if not observed_interval:
            warnings.append("更新周期未知，持续更新按中性计分")
    else:
        update_score = 0.0
    breakdown["持续更新迹象"] = update_score
    breakdown["索引质量"] = 5.0 if (has_primary_key or has_unique_key) else 0.0

    mostly_null_ratio = _mostly_null_queryable_ratio(profile)
    if mostly_null_ratio >= 0.6:
        deductions.append(("大量空值", 15.0))
    elif mostly_null_ratio >= 0.4:
        deductions.append(("大量空值", 10.0))
    elif mostly_null_ratio >= 0.2:
        deductions.append(("大量空值", 5.0))
    if qcols == 0:
        deductions.append(("缺少可查询字段", 30.0))
    elif is_time_series and not time_column:
        deductions.append(("时序表缺少时间类核心字段", 10.0))

    score = sum(breakdown.values())
    for label, amount in deductions:
        warnings.append(f"{label}：-{amount:g}")
        score -= amount
    score = round(max(0.0, min(100.0, score)), 2)

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
    if latest is None and is_time_series:
        confidence -= 0.20
    confidence = round(max(0.0, confidence), 2)

    critical: list[str] = []
    if skipped:
        critical.append("表画像被总超时跳过")
    if error:
        critical.append("受限样本读取失败")
    if sample_count == 0:
        critical.append("无样本数据（空表或无法画像）")
    if latest is None and is_time_series:
        critical.append("时序表缺少最新数据时间")
    if (
        role in _TIME_DATA_ROLES
        and (duplicate_ratio == "unknown" or duplicate_ratio is None)
        and not has_primary_key
        and not has_unique_key
    ):
        critical.append("数据表无可用键且重复键比例 unknown")

    quality_unknown = bool(critical)
    return {
        "score": score,
        "confidence": confidence,
        "breakdown": breakdown,
        "deductions": deductions,
        "warnings": warnings,
        "critical": critical,
        "quality_unknown": quality_unknown,
        "can_propose_active": (
            confidence >= MIN_CONFIDENCE_FOR_ACTIVE and not quality_unknown
        ),
        "confirmed_empty": (
            row_estimate == 0 and sample_count == 0 and not error and not skipped
        ),
        "is_time_series": is_time_series,
    }


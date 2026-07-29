"""可独立测试的水质报表计算规则。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any


LEVEL_ORDER = {
    "Ⅰ": 1,
    "Ⅱ": 2,
    "Ⅲ": 3,
    "Ⅳ": 4,
    "Ⅴ": 5,
    "劣Ⅴ": 6,
}
LEVEL_LABELS = tuple(LEVEL_ORDER)
_LEVEL_ALIASES = {
    "I": "Ⅰ",
    "II": "Ⅱ",
    "III": "Ⅲ",
    "IV": "Ⅳ",
    "V": "Ⅴ",
    "劣V": "劣Ⅴ",
    "劣V类": "劣Ⅴ",
    "劣Ⅴ类": "劣Ⅴ",
}


def normalize_level(value: object) -> str | None:
    """把数据库中的中英文罗马数字统一为报告展示值。"""
    if value is None:
        return None
    text = str(value).strip().replace("类", "")
    if not text:
        return None
    text = _LEVEL_ALIASES.get(text, text)
    return text if text in LEVEL_ORDER else None


def compare_levels(today: object, yesterday: object) -> str:
    """按照Ⅰ至劣Ⅴ的固定顺序判断水质变化。"""
    current = normalize_level(today)
    previous = normalize_level(yesterday)
    if current is None or previous is None:
        return "无有效数据"
    if LEVEL_ORDER[current] == LEVEL_ORDER[previous]:
        return "持平"
    return "有所好转" if LEVEL_ORDER[current] < LEVEL_ORDER[previous] else "有所下降"


def parse_monitor_frequency(value: object) -> list[dict[str, Any]]:
    """解析站点真实的“检测指标-频次”JSON，不补造缺失配置。"""
    if not value:
        return []
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        payload = value
    if not isinstance(payload, list):
        return []

    result: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            code = int(item.get("indicatorCode"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("indicatorName") or "").strip()
        suffix = str(item.get("frequencySuffix") or item.get("frequency") or "").strip()
        hours = _parse_frequency_hours(item.get("frequency"), suffix)
        if not name or hours is None:
            continue
        result.append(
            {
                "code": code,
                "name": name,
                "hours": hours,
                "label": suffix or f"{hours}小时1次",
                "column": f"m{code + 1}_value",
            }
        )
    return result


def _parse_frequency_hours(raw: object, suffix: str) -> int | None:
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    text = f"{raw or ''} {suffix}"
    match = re.search(r"(\d+)\s*小时", text)
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return max(1, int(match.group(1))) * 24
    if "月" in text:
        return 24 * 31
    return None


def expected_times(start: datetime, end: datetime, hours: int) -> list[datetime]:
    """按半开区间生成预期采样时刻。"""
    if hours <= 0 or end <= start:
        return []
    if hours >= 24 * 28:
        return [start]
    result: list[datetime] = []
    cursor = start
    while cursor < end:
        result.append(cursor)
        cursor += timedelta(hours=hours)
    return result


def expected_count_for_day(hours: int) -> int:
    return len(
        expected_times(
            datetime(2000, 1, 1),
            datetime(2000, 1, 2),
            hours,
        )
    )


def classify_monitoring(
    indicators: Sequence[dict[str, Any]],
    records_by_time: dict[datetime, dict[str, Any]],
    start: datetime,
    end: datetime,
) -> tuple[str, int, int, list[str]]:
    """计算站点监测状态、有效点数、预期点数及可追溯问题描述。"""
    if not indicators:
        return "待配置", 0, 0, ["应测指标及频次待配置"]

    valid = 0
    expected = 0
    descriptions: list[str] = []
    any_valid = False
    for indicator in indicators:
        times = expected_times(start, end, int(indicator["hours"]))
        expected += len(times)
        values: list[object] = []
        missing: list[datetime] = []
        for moment in times:
            value = (records_by_time.get(moment) or {}).get(indicator["column"])
            if value is None:
                missing.append(moment)
            else:
                valid += 1
                any_valid = True
                values.append(value)
        if missing:
            labels = _format_missing_times(
                missing,
                interval_hours=int(indicator["hours"]),
            )
            descriptions.append(f"{indicator['name']}（{labels}缺测）")
        elif len(values) >= 2 and len({str(value) for value in values}) == 1:
            descriptions.append(f"{indicator['name']}（恒值）")

    if not any_valid:
        return "未监测", valid, expected, descriptions
    if descriptions:
        return "、".join(descriptions), valid, expected, descriptions
    return "正常", valid, expected, descriptions


def _format_missing_times(
    moments: Sequence[datetime],
    *,
    interval_hours: int,
) -> str:
    """合并连续缺测时刻，避免长表逐小时罗列。"""
    if not moments:
        return ""
    ordered = sorted(set(moments))
    groups: list[list[datetime]] = [[ordered[0]]]
    step = timedelta(hours=max(1, interval_hours))
    for moment in ordered[1:]:
        if moment - groups[-1][-1] == step:
            groups[-1].append(moment)
        else:
            groups.append([moment])
    labels: list[str] = []
    for group in groups:
        if len(group) == 1:
            labels.append(group[0].strftime("%H时"))
            continue
        suffix = (
            f"（每{interval_hours}小时）"
            if interval_hours > 1
            else ""
        )
        labels.append(
            f"{group[0].strftime('%H')}-{group[-1].strftime('%H')}时{suffix}"
        )
    return "、".join(labels)


def count_episode_starts(
    points: Sequence[tuple[date | datetime, bool]],
    *,
    minimum_run: int,
    period_start: date | datetime,
    period_end: date | datetime,
) -> int:
    """连续区间达到阈值时只计一次，支持从上期继承的状态。"""
    run = 0
    count = 0
    for moment, matched in points:
        if matched:
            run += 1
            if (
                run == minimum_run
                and period_start <= moment < period_end
            ):
                count += 1
        else:
            run = 0
    return count


def merge_date_ranges(days: Iterable[date]) -> list[str]:
    """把缺测日期合并为连续日期段。"""
    unique = sorted(set(days))
    if not unique:
        return []
    ranges: list[tuple[date, date]] = []
    start = previous = unique[0]
    for current in unique[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue
        ranges.append((start, previous))
        start = previous = current
    ranges.append((start, previous))
    return [
        (
            begin.strftime("%m月%d日")
            if begin == finish
            else f"{begin.strftime('%m月%d日')}-{finish.strftime('%m月%d日')}"
        )
        for begin, finish in ranges
    ]


def percent(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return round(float(numerator) * 100 / float(denominator), 2)

"""水质日报、月报确定性计算服务。"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from backend.water_quality_reports.repository import (
    REPORT_SOURCE_ID,
    ReportRepository,
)
from backend.water_quality_reports.rules import (
    LEVEL_LABELS,
    LEVEL_ORDER,
    classify_monitoring,
    compare_levels,
    count_episode_starts,
    expected_count_for_day,
    merge_date_ranges,
    normalize_level,
    parse_monitor_frequency,
    percent,
)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _latest_by_station(
    rows: list[dict[str, Any]],
    target_day: date,
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        moment = row.get("monitor_time")
        if not isinstance(moment, datetime) or moment.date() != target_day:
            continue
        station_id = int(row["station_id"])
        previous = result.get(station_id)
        if previous is None or previous["monitor_time"] <= moment:
            result[station_id] = row
    return result


def _rows_by_time(
    rows: list[dict[str, Any]],
    station_id: int,
    target_day: date,
) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        moment = row.get("monitor_time")
        if (
            int(row["station_id"]) == station_id
            and isinstance(moment, datetime)
            and moment.date() == target_day
        ):
            result[moment.replace(minute=0, second=0, microsecond=0)] = row
    return result


def _target_map(rows: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for row in rows:
        section_id = int(row["section_id"])
        level = normalize_level(row.get("water_quality_target_level"))
        if level is not None and section_id not in result:
            result[section_id] = level
    return result


def _indicator_summary(indicators: list[dict[str, Any]]) -> str:
    if not indicators:
        return "待配置"
    return "、".join(
        f"{item['name']}（{item['label']}）"
        for item in indicators
    )


def _daily_level_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[int, date], str | None]:
    result: dict[tuple[int, date], str | None] = {}
    moments: dict[tuple[int, date], datetime] = {}
    for row in rows:
        moment = row.get("monitor_time")
        if not isinstance(moment, datetime):
            continue
        key = (int(row["station_id"]), moment.date())
        if key not in moments or moments[key] <= moment:
            moments[key] = moment
            result[key] = normalize_level(row.get("water_quality_level"))
    return result


def _hourly_level_map(
    rows: list[dict[str, Any]],
) -> dict[tuple[int, datetime], str | None]:
    result: dict[tuple[int, datetime], str | None] = {}
    for row in rows:
        moment = row.get("monitor_time")
        if isinstance(moment, datetime):
            result[
                (
                    int(row["station_id"]),
                    moment.replace(minute=0, second=0, microsecond=0),
                )
            ] = normalize_level(row.get("water_quality_level"))
    return result


def _is_worse(level: str | None, target: str | None) -> bool:
    return bool(
        level
        and target
        and LEVEL_ORDER[level] > LEVEL_ORDER[target]
    )


class WaterQualityReportService:
    """只使用 SQL 结果和显式规则生成报告，不调用 LLM。"""

    def __init__(self, repository: ReportRepository) -> None:
        self.repository = repository

    def options(self) -> dict[str, Any]:
        stations = self.repository.stations()
        observed: dict[int, dict[str, Any]] = {}
        for station in stations:
            for item in parse_monitor_frequency(station.get("monitor_frequency")):
                entry = observed.setdefault(
                    int(item["code"]),
                    {
                        "code": int(item["code"]),
                        "name": item["name"],
                        "frequencies": set(),
                    },
                )
                entry["frequencies"].add(int(item["hours"]))
        indicators = [
            {
                "code": entry["code"],
                "name": entry["name"],
                "frequencies": sorted(entry["frequencies"]),
            }
            for _, entry in sorted(observed.items())
        ]
        return {
            "source_id": REPORT_SOURCE_ID,
            "indicators": indicators,
            "recent_days": [1, 2, 3, 5, 7],
        }

    def daily(
        self,
        report_date: date,
        *,
        indicator_codes: tuple[int, ...] | None = None,
        frequency_overrides: dict[int, int] | None = None,
        recent_days: int = 3,
    ) -> dict[str, Any]:
        if recent_days < 1 or recent_days > 7:
            raise ValueError("近日报告范围必须为1至7天")
        start = _day_start(report_date)
        end = start + timedelta(days=1)
        history_start = start - timedelta(days=7)
        hourly_start = min(
            end - timedelta(hours=120),
            start - timedelta(days=recent_days - 1),
        )

        stations = self.repository.stations()
        hourly = self.repository.hourly_records(hourly_start, end)
        daily = self.repository.daily_records(history_start, end)
        targets = _target_map(
            self.repository.targets(report_date.year, report_date.month)
        )
        return self._build_daily(
            report_date,
            stations,
            hourly,
            daily,
            targets,
            indicator_codes=indicator_codes,
            frequency_overrides=frequency_overrides or {},
            recent_days=recent_days,
        )

    def _build_daily(
        self,
        report_date: date,
        stations: list[dict[str, Any]],
        hourly: list[dict[str, Any]],
        daily: list[dict[str, Any]],
        targets: dict[int, str],
        *,
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
        recent_days: int,
    ) -> dict[str, Any]:
        start = _day_start(report_date)
        end = start + timedelta(days=1)
        yesterday = report_date - timedelta(days=1)
        enabled = [row for row in stations if str(row.get("build_state")) == "1"]
        disabled = [row for row in stations if str(row.get("build_state")) != "1"]
        day_levels = _daily_level_map(daily)
        hour_levels = _hourly_level_map(hourly)
        allowed_frequencies = self._allowed_frequencies(stations)
        self._validate_selection(
            indicator_codes,
            frequency_overrides,
            allowed_frequencies,
        )

        monitoring_rows: list[dict[str, Any]] = []
        valid_slots = expected_slots = valid_station_count = 0
        for index, station in enumerate(stations, 1):
            station_id = int(station["id"])
            indicators = self._selected_indicators(
                station,
                indicator_codes,
                frequency_overrides,
            )
            today_records = _rows_by_time(hourly, station_id, report_date)
            today_status, today_valid, today_expected, _ = classify_monitoring(
                indicators, today_records, start, end
            )
            yesterday_status = self._monitor_status_for_day(
                indicators, hourly, station_id, yesterday
            )
            recent_statuses = [
                {
                    "date": current.isoformat(),
                    "status": self._monitor_status_for_day(
                        indicators, hourly, station_id, current
                    ),
                }
                for current in (
                    report_date - timedelta(days=offset)
                    for offset in range(recent_days - 1, -1, -1)
                )
            ]
            if str(station.get("build_state")) != "1":
                today_status = yesterday_status = "未启用"
                recent_statuses = [
                    {"date": item["date"], "status": "未启用"}
                    for item in recent_statuses
                ]
            else:
                valid_slots += today_valid
                expected_slots += today_expected
                if today_valid > 0:
                    valid_station_count += 1
            monitoring_rows.append(
                {
                    "index": index,
                    "station_id": station_id,
                    "station_name": station["station_name"],
                    "expected_indicators": _indicator_summary(indicators),
                    "today_status": today_status,
                    "yesterday_status": yesterday_status,
                    "recent_three_days": recent_statuses,
                    "remark": station.get("remark") or "",
                }
            )

        current_levels = {
            int(station["id"]): day_levels.get((int(station["id"]), report_date))
            for station in enabled
        }
        previous_levels = {
            int(station["id"]): day_levels.get((int(station["id"]), yesterday))
            for station in enabled
        }
        quality_rows: list[dict[str, Any]] = []
        category_counts = Counter(
            level for level in current_levels.values() if level is not None
        )
        improved: list[str] = []
        declined: list[str] = []
        below_iii: list[str] = []
        for index, station in enumerate(enabled, 1):
            station_id = int(station["id"])
            today_level = current_levels[station_id]
            previous_level = previous_levels[station_id]
            change = compare_levels(today_level, previous_level)
            if change == "有所好转":
                improved.append(station["station_name"])
            elif change == "有所下降":
                declined.append(station["station_name"])
            if today_level and LEVEL_ORDER[today_level] > LEVEL_ORDER["Ⅲ"]:
                below_iii.append(station["station_name"])
            quality_rows.append(
                {
                    "index": index,
                    "station_id": station_id,
                    "station_name": station["station_name"],
                    "today_level": today_level or "无有效数据",
                    "yesterday_level": previous_level or "无有效数据",
                    "change": change,
                }
            )

        valid_level_count = sum(category_counts.values())
        categories = [
            {
                "level": level,
                "count": category_counts[level],
                "percentage": percent(category_counts[level], valid_level_count),
            }
            for level in LEVEL_LABELS
        ]

        lake_stations = [
            station for station in enabled if str(station.get("water_type")) == "1"
        ]
        tributary_stations = [
            station
            for station in enabled
            if str(station.get("tributary_trunk")) == "1"
        ]
        lake_rows = [
            self._condition_row(
                station, report_date, targets, day_levels, hour_levels
            )
            for station in lake_stations
        ]
        tributary_rows = [
            self._condition_row(
                station, report_date, targets, day_levels, hour_levels
            )
            for station in tributary_stations
        ]
        declined_two = sum(
            1
            for station in tributary_stations
            if self._declined_by_two(
                day_levels.get((int(station["id"]), report_date)),
                day_levels.get((int(station["id"]), yesterday)),
            )
        )
        super_stations = [
            station for station in enabled if str(station.get("station_type")) == "3"
        ]
        super_rows = [
            {
                "station_name": station["station_name"],
                "water_quality_level": (
                    day_levels.get((int(station["id"]), report_date))
                    or "暂无数据"
                ),
                "toxic_substances": "暂无数据",
                "safety_level": "暂无数据",
            }
            for station in super_stations
        ]

        report = {
            "report_type": "daily",
            "title": "梁子湖流域自动站水质日报",
            "report_date": report_date.isoformat(),
            "source_id": REPORT_SOURCE_ID,
            "options": {
                "indicator_codes": (
                    list(indicator_codes)
                    if indicator_codes is not None
                    else sorted(allowed_frequencies)
                ),
                "frequency_hours": {
                    str(code): hours
                    for code, hours in sorted(frequency_overrides.items())
                },
                "recent_days": recent_days,
            },
            "monitoring": {
                "configured_station_count": len(stations),
                "enabled_station_count": len(enabled),
                "disabled_station_count": len(disabled),
                "valid_station_count": valid_station_count,
                "missing_or_invalid_station_count": max(
                    len(enabled) - valid_station_count, 0
                ),
                "valid_transmission_numerator": valid_slots,
                "valid_transmission_denominator": expected_slots,
                "valid_transmission_rate": percent(valid_slots, expected_slots),
                "rate_formula": "有效采样点数 ÷ 已启用站点配置的预期采样点数",
                "rows": monitoring_rows,
            },
            "overall_quality": {
                "valid_station_count": valid_level_count,
                "categories": categories,
                "compliance_count": sum(
                    category_counts[level] for level in ("Ⅰ", "Ⅱ", "Ⅲ")
                ),
                "compliance_rate": percent(
                    sum(category_counts[level] for level in ("Ⅰ", "Ⅱ", "Ⅲ")),
                    valid_level_count,
                ),
                "below_class_iii_stations": below_iii,
                "improved_stations": improved,
                "declined_stations": declined,
                "main_pollutants": "暂无数据",
                "rows": quality_rows,
            },
            "lake_area": {
                "station_count": len(lake_rows),
                "continuous_120h_over_target_count": self._true_count(
                    lake_rows, "continuous_120h_over_target"
                ),
                "continuous_7d_over_target_count": self._true_count(
                    lake_rows, "continuous_7d_over_target"
                ),
                "continuous_3d_worse_iv_count": self._true_count(
                    lake_rows, "continuous_3d_worse_iv"
                ),
                "daily_inferior_v_count": self._true_count(
                    lake_rows, "daily_inferior_v"
                ),
                "rainfall": "暂无数据",
                "rainfall_last_year": "暂无数据",
                "water_level": "暂无数据",
                "water_level_last_year": "暂无数据",
                "rows": lake_rows,
            },
            "tributaries": {
                "station_count": len(tributary_rows),
                "declined_two_or_more_count": declined_two,
                "rows": tributary_rows,
                "hydrology_rows": [],
                "hydrology_status": "暂无数据",
            },
            "super_station": {
                "rows": super_rows,
                "status": "暂无数据" if not super_rows else "部分数据可用",
                "note": "有毒有害物质和安全水平缺少可证明的数据源",
            },
            "unavailable_fields": [
                "日报主要污染物（日报表无已存储字段）",
                "湖区去年同期降雨量及同比（缺少可证明站点关系）",
                "湖区水位及去年同期同比（水文字段语义不足）",
                "支流水位和流量（水文字段语义不足）",
                "超级站有毒有害物质和安全水平（缺少专用数据源）",
            ],
        }
        report["narratives"] = self._daily_narratives(report)
        return report

    def _monitor_status_for_day(
        self,
        indicators: list[dict[str, Any]],
        hourly: list[dict[str, Any]],
        station_id: int,
        target_day: date,
    ) -> str:
        start = _day_start(target_day)
        status, _, _, _ = classify_monitoring(
            indicators,
            _rows_by_time(hourly, station_id, target_day),
            start,
            start + timedelta(days=1),
        )
        return status

    def _condition_row(
        self,
        station: dict[str, Any],
        report_date: date,
        targets: dict[int, str],
        day_levels: dict[tuple[int, date], str | None],
        hour_levels: dict[tuple[int, datetime], str | None],
    ) -> dict[str, Any]:
        station_id = int(station["id"])
        section_id = station.get("section_id")
        target = targets.get(int(section_id)) if section_id is not None else None
        end = _day_start(report_date) + timedelta(days=1)
        hour_points = [
            hour_levels.get((station_id, end - timedelta(hours=offset)))
            for offset in range(120, 0, -1)
        ]
        seven_levels = [
            day_levels.get((station_id, report_date - timedelta(days=offset)))
            for offset in range(6, -1, -1)
        ]
        three_levels = seven_levels[-3:]
        today_level = day_levels.get((station_id, report_date))
        return {
            "station_id": station_id,
            "station_name": station["station_name"],
            "target_level": target or "待配置",
            "today_level": today_level or "无有效数据",
            "yesterday_level": (
                day_levels.get((station_id, report_date - timedelta(days=1)))
                or "无有效数据"
            ),
            "change": compare_levels(
                today_level,
                day_levels.get((station_id, report_date - timedelta(days=1))),
            ),
            "continuous_120h_over_target": (
                None
                if target is None
                else len(hour_points) == 120
                and all(_is_worse(level, target) for level in hour_points)
            ),
            "continuous_7d_over_target": (
                None
                if target is None
                else len(seven_levels) == 7
                and all(_is_worse(level, target) for level in seven_levels)
            ),
            "continuous_3d_worse_iv": (
                len(three_levels) == 3
                and all(_is_worse(level, "Ⅳ") for level in three_levels)
            ),
            "daily_inferior_v": today_level == "劣Ⅴ",
        }

    @staticmethod
    def _true_count(rows: list[dict[str, Any]], field: str) -> int:
        return sum(item.get(field) is True for item in rows)

    @staticmethod
    def _declined_by_two(today: str | None, yesterday: str | None) -> bool:
        return bool(
            today
            and yesterday
            and LEVEL_ORDER[today] - LEVEL_ORDER[yesterday] >= 2
        )

    def monthly(
        self,
        report_month: date,
        *,
        indicator_codes: tuple[int, ...] | None = None,
        frequency_overrides: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        month_start = report_month.replace(day=1)
        month_end = _next_month(month_start)
        start = _day_start(month_start)
        end = _day_start(month_end)
        stations = self.repository.stations()
        hourly = self.repository.hourly_records(start - timedelta(hours=119), end)
        daily = self.repository.daily_records(start - timedelta(days=6), end)
        targets = _target_map(
            self.repository.targets(month_start.year, month_start.month)
        )
        return self._build_monthly(
            month_start,
            stations,
            hourly,
            daily,
            targets,
            indicator_codes=indicator_codes,
            frequency_overrides=frequency_overrides or {},
        )

    def _build_monthly(
        self,
        month_start: date,
        stations: list[dict[str, Any]],
        hourly: list[dict[str, Any]],
        daily: list[dict[str, Any]],
        targets: dict[int, str],
        *,
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
    ) -> dict[str, Any]:
        month_end = _next_month(month_start)
        enabled = [row for row in stations if str(row.get("build_state")) == "1"]
        day_levels = _daily_level_map(daily)
        hour_levels = _hourly_level_map(hourly)
        allowed_frequencies = self._allowed_frequencies(stations)
        self._validate_selection(
            indicator_codes,
            frequency_overrides,
            allowed_frequencies,
        )
        days = [
            month_start + timedelta(days=offset)
            for offset in range((month_end - month_start).days)
        ]
        daily_rows: dict[tuple[int, date], dict[str, Any]] = {}
        for row in daily:
            moment = row.get("monitor_time")
            if isinstance(moment, datetime):
                daily_rows[(int(row["station_id"]), moment.date())] = row

        monitoring_rows: list[dict[str, Any]] = []
        valid_slots = expected_slots = valid_station_count = 0
        for index, station in enumerate(stations, 1):
            station_id = int(station["id"])
            indicators = self._selected_indicators(
                station,
                indicator_codes,
                frequency_overrides,
            )
            missing_descriptions: list[str] = []
            station_valid = 0
            station_expected = 0
            for indicator in indicators:
                expected_daily = expected_count_for_day(int(indicator["hours"]))
                missing_days: list[date] = []
                for current_day in days:
                    station_expected += expected_daily
                    row = daily_rows.get((station_id, current_day)) or {}
                    count_value = row.get(
                        f"m{int(indicator['code']) + 1}_count"
                    )
                    received = (
                        max(0, int(count_value))
                        if isinstance(count_value, (int, float))
                        else 0
                    )
                    station_valid += min(received, expected_daily)
                    if received < expected_daily:
                        missing_days.append(current_day)
                if missing_days:
                    ranges = "、".join(merge_date_ranges(missing_days))
                    missing_descriptions.append(f"{indicator['name']}：{ranges}")
            if not indicators:
                missing_text = "待配置"
            elif station_valid == 0:
                missing_text = "整月未监测"
            else:
                missing_text = "；".join(missing_descriptions) or "无"
            if str(station.get("build_state")) != "1":
                missing_text = "未启用"
            else:
                valid_slots += station_valid
                expected_slots += station_expected
                if station_valid > 0:
                    valid_station_count += 1
            monitoring_rows.append(
                {
                    "index": index,
                    "station_id": station_id,
                    "station_name": station["station_name"],
                    "expected_indicators": _indicator_summary(indicators),
                    "missing_indicators_and_periods": missing_text,
                }
            )

        condition_rows = [
            self._monthly_condition_row(
                station,
                month_start,
                month_end,
                targets,
                day_levels,
                hour_levels,
            )
            for station in enabled
        ]
        report = {
            "report_type": "monthly",
            "title": "梁子湖流域自动站水质月报",
            "report_month": month_start.strftime("%Y-%m"),
            "source_id": REPORT_SOURCE_ID,
            "options": {
                "indicator_codes": (
                    list(indicator_codes)
                    if indicator_codes is not None
                    else sorted(allowed_frequencies)
                ),
                "frequency_hours": {
                    str(code): hours
                    for code, hours in sorted(frequency_overrides.items())
                },
            },
            "monitoring": {
                "configured_station_count": len(stations),
                "enabled_station_count": len(enabled),
                "disabled_station_count": len(stations) - len(enabled),
                "valid_station_count": valid_station_count,
                "valid_transmission_numerator": valid_slots,
                "valid_transmission_denominator": expected_slots,
                "valid_transmission_rate": percent(valid_slots, expected_slots),
                "rate_formula": "当月有效采样点数 ÷ 已启用站点配置的当月预期采样点数",
                "rows": monitoring_rows,
            },
            "station_conditions": {
                "rows": condition_rows,
                "counting_rule": (
                    "连续区间首次达到120小时、7天或3天阈值时计1次；"
                    "同一区间不重复计数；月初继承上月状态，月末未结束区间在达到阈值时计数；"
                    "无数据打断连续区间；目标类别为空时目标相关次数显示待配置。"
                ),
            },
            "unavailable_fields": [
                "目标相关次数在断面缺少当年目标配置时显示待配置",
                "超级站有毒有害物质和安全水平缺少专用数据源",
            ],
        }
        report["narratives"] = self._monthly_narratives(report)
        return report

    @staticmethod
    def _allowed_frequencies(
        stations: list[dict[str, Any]],
    ) -> dict[int, set[int]]:
        result: dict[int, set[int]] = defaultdict(set)
        for station in stations:
            for item in parse_monitor_frequency(station.get("monitor_frequency")):
                result[int(item["code"])].add(int(item["hours"]))
        return result

    @staticmethod
    def _validate_selection(
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
        allowed_frequencies: dict[int, set[int]],
    ) -> None:
        if indicator_codes is not None:
            unknown = set(indicator_codes) - set(allowed_frequencies)
            if unknown:
                raise ValueError("包含未配置的监测指标")
            if set(frequency_overrides) - set(indicator_codes):
                raise ValueError("频次覆盖只能用于已选择的监测指标")
        for code, hours in frequency_overrides.items():
            if code not in allowed_frequencies:
                raise ValueError("包含未配置的监测指标频次")
            if hours not in allowed_frequencies[code]:
                raise ValueError("所选频次不在真实站点配置中")

    @staticmethod
    def _selected_indicators(
        station: dict[str, Any],
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
    ) -> list[dict[str, Any]]:
        selected = []
        allowed = set(indicator_codes) if indicator_codes is not None else None
        for item in parse_monitor_frequency(station.get("monitor_frequency")):
            code = int(item["code"])
            if allowed is not None and code not in allowed:
                continue
            cloned = dict(item)
            if code in frequency_overrides:
                hours = frequency_overrides[code]
                cloned["hours"] = hours
                cloned["label"] = f"{hours}小时1次"
            selected.append(cloned)
        return selected

    def _monthly_condition_row(
        self,
        station: dict[str, Any],
        month_start: date,
        month_end: date,
        targets: dict[int, str],
        day_levels: dict[tuple[int, date], str | None],
        hour_levels: dict[tuple[int, datetime], str | None],
    ) -> dict[str, Any]:
        station_id = int(station["id"])
        section_id = station.get("section_id")
        target = targets.get(int(section_id)) if section_id is not None else None
        start_dt = _day_start(month_start)
        end_dt = _day_start(month_end)
        hourly_points: list[tuple[datetime, bool]] = []
        cursor = start_dt - timedelta(hours=119)
        while cursor < end_dt:
            hourly_points.append(
                (
                    cursor,
                    _is_worse(hour_levels.get((station_id, cursor)), target),
                )
            )
            cursor += timedelta(hours=1)
        daily_points_7: list[tuple[date, bool]] = []
        daily_points_3: list[tuple[date, bool]] = []
        cursor_day = month_start - timedelta(days=6)
        while cursor_day < month_end:
            level = day_levels.get((station_id, cursor_day))
            daily_points_7.append((cursor_day, _is_worse(level, target)))
            daily_points_3.append((cursor_day, _is_worse(level, "Ⅳ")))
            cursor_day += timedelta(days=1)
        inferior_v_count = sum(
            day_levels.get((station_id, current_day)) == "劣Ⅴ"
            for current_day in (
                month_start + timedelta(days=offset)
                for offset in range((month_end - month_start).days)
            )
        )
        return {
            "station_id": station_id,
            "station_name": station["station_name"],
            "target_level": target or "待配置",
            "continuous_120h_over_target_count": (
                None
                if target is None
                else count_episode_starts(
                    hourly_points,
                    minimum_run=120,
                    period_start=start_dt,
                    period_end=end_dt,
                )
            ),
            "continuous_7d_over_target_count": (
                None
                if target is None
                else count_episode_starts(
                    daily_points_7,
                    minimum_run=7,
                    period_start=month_start,
                    period_end=month_end,
                )
            ),
            "continuous_3d_worse_iv_count": count_episode_starts(
                daily_points_3,
                minimum_run=3,
                period_start=month_start,
                period_end=month_end,
            ),
            "daily_inferior_v_count": inferior_v_count,
        }

    @staticmethod
    def _daily_narratives(report: dict[str, Any]) -> dict[str, str]:
        monitoring = report["monitoring"]
        overall = report["overall_quality"]
        rate = monitoring["valid_transmission_rate"]
        rate_text = "暂无数据" if rate is None else f"{rate:.2f}%"
        return {
            "monitoring": (
                f"{report['report_date']}，应运行站点{monitoring['enabled_station_count']}个，"
                f"实际有有效传输数据的站点{monitoring['valid_station_count']}个，"
                f"数据有效传输率为{rate_text}；未启用站点"
                f"{monitoring['disabled_station_count']}个。"
            ),
            "overall_quality": (
                f"当日有明确水质类别的点位{overall['valid_station_count']}个，"
                f"Ⅰ-Ⅲ类达标率为"
                f"{'暂无数据' if overall['compliance_rate'] is None else format(overall['compliance_rate'], '.2f') + '%'}。"
            ),
        }

    @staticmethod
    def _monthly_narratives(report: dict[str, Any]) -> dict[str, str]:
        monitoring = report["monitoring"]
        rate = monitoring["valid_transmission_rate"]
        return {
            "monitoring": (
                f"{report['report_month']}，应运行站点"
                f"{monitoring['enabled_station_count']}个，当月有有效传输数据的站点"
                f"{monitoring['valid_station_count']}个，月度数据有效传输率为"
                f"{'暂无数据' if rate is None else f'{rate:.2f}%'}。"
            )
        }

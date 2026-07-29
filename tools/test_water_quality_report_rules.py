"""水质报表规则、计算、API 与 PDF 的离线回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
from vanna.servers.base import ChatRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.water_quality_reports.api import create_report_router
from backend.water_quality_reports.chat_handler import (
    WaterQualityReportChatHandler,
    parse_report_intent,
)
from backend.water_quality_reports.pdf_renderer import (
    PdfRenderError,
    WaterQualityPdfRenderer,
)
from backend.water_quality_reports.repository import (
    REPORT_TABLE_WHITELIST,
    ReportDataSourceError,
)
from backend.water_quality_reports.rules import (
    classify_monitoring,
    compare_levels,
    count_episode_starts,
    expected_count_for_day,
    merge_date_ranges,
    normalize_level,
    parse_monitor_frequency,
)
from backend.water_quality_reports.service import WaterQualityReportService


def station(
    station_id: int,
    name: str,
    *,
    enabled: bool = True,
    water_type: str = "0",
    station_type: str = "1",
    section_id: int | None = 10,
) -> dict[str, object]:
    return {
        "id": station_id,
        "station_code": f"S{station_id}",
        "station_name": name,
        "station_type": station_type,
        "build_state": "1" if enabled else "0",
        "water_type": water_type,
        "water_body_id": 1,
        "section_id": section_id,
        "monitor_frequency": (
            '[{"indicatorCode":0,"indicatorName":"水温","frequency":1,'
            '"frequencySuffix":"1小时1次"},'
            '{"indicatorCode":5,"indicatorName":"氨氮","frequency":4,'
            '"frequencySuffix":"4小时1次"}]'
        ),
        "remark": "测试配置",
        "tributary_trunk": "1",
    }


class FakeRepository:
    def __init__(self) -> None:
        self.query_timings: list[dict[str, object]] = []
        self._stations = [
            station(1, "正常站"),
            station(2, "未启用站", enabled=False),
        ]

    def stations(self):
        return self._stations

    def hourly_records(self, start, end, *, limit=100_000):
        rows = []
        cursor = start
        while cursor < end:
            if cursor.date() >= date(2025, 7, 1):
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "station_id": 1,
                        "monitor_time": cursor,
                        "water_quality_level": "Ⅲ",
                        "status": "0",
                        "m1_value": 20.0,
                        "m6_value": 0.2 if cursor.hour % 4 == 0 else None,
                    }
                )
            cursor += timedelta(hours=1)
        return rows

    def daily_records(self, start, end, *, limit=10_000):
        rows = []
        cursor = start.date()
        while cursor < end.date():
            if cursor >= date(2025, 7, 1):
                rows.append(
                    {
                        "id": len(rows) + 1,
                        "station_id": 1,
                        "monitor_time": datetime.combine(cursor, datetime.min.time())
                        + timedelta(hours=23),
                        "water_quality_level": "III",
                        "status": "0",
                        "m1_count": 24,
                        "m6_count": 6,
                    }
                )
            cursor += timedelta(days=1)
        return rows

    def targets(self, year, month):
        return [
            {
                "id": 1,
                "section_id": 10,
                "year": year,
                "month": month,
                "water_quality_target_level": "III",
            }
        ]


def test_rules() -> None:
    scope = json.loads(
        (PROJECT_ROOT / "config" / "water_quality_report_scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(scope["tables"] + scope["report_only_tables"]) == set(
        REPORT_TABLE_WHITELIST
    )
    assert scope["training_scope_changed"] is False
    assert normalize_level("III类") == "Ⅲ"
    assert normalize_level("劣V类") == "劣Ⅴ"
    assert compare_levels("Ⅱ", "Ⅳ") == "有所好转"
    assert compare_levels("Ⅴ", "Ⅲ") == "有所下降"
    assert compare_levels(None, "Ⅲ") == "无有效数据"

    indicators = parse_monitor_frequency(station(1, "站点")["monitor_frequency"])
    assert [item["hours"] for item in indicators] == [1, 4]
    assert expected_count_for_day(1) == 24
    assert expected_count_for_day(4) == 6

    start = datetime(2025, 7, 1)
    records = {
        start + timedelta(hours=hour): {
            "m1_value": 20,
            "m6_value": 0.2 if hour in (0, 4, 12, 16, 20) else None,
        }
        for hour in range(24)
    }
    status, valid, expected, descriptions = classify_monitoring(
        indicators, records, start, start + timedelta(days=1)
    )
    assert valid == 29 and expected == 30
    assert "氨氮（08时缺测）" in status
    assert descriptions

    ranges = merge_date_ranges(
        [date(2025, 7, 1), date(2025, 7, 2), date(2025, 7, 4)]
    )
    assert ranges == ["07月01日-07月02日", "07月04日"]

    points = [
        (date(2025, 6, 28) + timedelta(days=index), matched)
        for index, matched in enumerate(
            [True, True, True, True, True, True, True, True, False, True]
        )
    ]
    assert (
        count_episode_starts(
            points,
            minimum_run=7,
            period_start=date(2025, 7, 1),
            period_end=date(2025, 8, 1),
        )
        == 1
    )
    daily_intent = parse_report_intent(
        "生成2025年7月28日水质日报，只看氨氮，近5日",
        today=date(2025, 8, 1),
    )
    assert daily_intent is not None
    assert daily_intent.report_type == "daily"
    assert daily_intent.period == date(2025, 7, 28)
    assert daily_intent.recent_days == 5
    monthly_intent = parse_report_intent(
        "给我生成2025年7月水质月报",
        today=date(2025, 8, 1),
    )
    assert monthly_intent is not None
    assert monthly_intent.period == date(2025, 7, 1)
    assert parse_report_intent("查询7月水质数据") is None


def test_service() -> tuple[dict, dict]:
    service = WaterQualityReportService(FakeRepository())
    daily = service.daily(date(2025, 7, 28))
    assert daily["monitoring"]["configured_station_count"] == 2
    assert daily["monitoring"]["enabled_station_count"] == 1
    assert daily["monitoring"]["valid_station_count"] == 1
    assert daily["monitoring"]["valid_transmission_rate"] == 100.0
    assert daily["overall_quality"]["valid_station_count"] == 1
    assert daily["overall_quality"]["categories"][2]["count"] == 1
    assert daily["monitoring"]["rows"][1]["today_status"] == "未启用"

    monthly = service.monthly(date(2025, 7, 1))
    assert monthly["monitoring"]["valid_station_count"] == 1
    assert monthly["monitoring"]["valid_transmission_rate"] == 100.0
    assert monthly["station_conditions"]["rows"][0][
        "continuous_120h_over_target_count"
    ] == 0
    assert monthly["station_conditions"]["rows"][0][
        "continuous_3d_worse_iv_count"
    ] == 0
    return daily, monthly


def expand_rows(report: dict, count: int = 32) -> dict:
    import copy

    cloned = copy.deepcopy(report)
    rows = cloned["monitoring"]["rows"]
    template = rows[0]
    cloned["monitoring"]["rows"] = [
        {
            **template,
            "index": index,
            "station_id": index,
            "station_name": f"用于验证跨页重复表头的超长监测点位名称{index:02d}",
        }
        for index in range(1, count + 1)
    ]
    if cloned["report_type"] == "monthly":
        condition = cloned["station_conditions"]["rows"][0]
        cloned["station_conditions"]["rows"] = [
            {
                **condition,
                "station_id": index,
                "station_name": f"测试点位{index:02d}",
            }
            for index in range(1, count + 1)
        ]
    return cloned


def test_pdf_and_api(daily: dict, monthly: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="water-report-test-") as temp_name:
        original = os.environ.get("WATER_REPORT_OUTPUT_DIR")
        os.environ["WATER_REPORT_OUTPUT_DIR"] = temp_name
        try:
            renderer = WaterQualityPdfRenderer()
            daily_path = renderer.render(expand_rows(daily))
            monthly_path = renderer.render(expand_rows(monthly))
            assert daily_path.name == "梁子湖流域自动站水质日报_20250728.pdf"
            assert monthly_path.name == "梁子湖流域自动站水质月报_202507.pdf"
            assert len(PdfReader(daily_path).pages) > 1
            assert len(PdfReader(monthly_path).pages) > 1
            with ThreadPoolExecutor(max_workers=4) as executor:
                concurrent_paths = list(
                    executor.map(lambda _: renderer.render(daily), range(4))
                )
            assert len({path.read_bytes() for path in concurrent_paths}) == 1

            class StubService:
                def __init__(self):
                    self.repository = SimpleNamespace(query_timings=[])

                def options(self):
                    return {
                        "source_id": "mysql-lzh-monitor",
                        "indicators": [
                            {"code": 0, "name": "水温", "frequencies": [1]}
                        ],
                        "recent_days": [1, 2, 3, 5, 7],
                    }

                def daily(self, report_date, **kwargs):
                    assert report_date == date(2025, 7, 28)
                    return daily

                def monthly(self, report_month, **kwargs):
                    assert report_month == date(2025, 7, 1)
                    return monthly

            app = FastAPI()
            app.include_router(create_report_router(StubService))
            client = TestClient(app)
            assert client.get(
                "/api/reports/water-quality/options"
            ).json()["source_id"] == "mysql-lzh-monitor"
            first = client.get(
                "/api/reports/water-quality/daily?date=2025-07-28"
            )
            second = client.get(
                "/api/reports/water-quality/daily?date=2025-07-28"
            )
            assert first.status_code == 200 and first.json() == second.json()
            assert (
                client.get(
                    "/api/reports/water-quality/daily?date=2025-02-30"
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/reports/water-quality/daily"
                    "?date=2025-07-28&indicators="
                ).status_code
                == 422
            )
            preview = client.get(
                "/api/reports/water-quality/monthly/preview?month=2025-07"
            )
            assert preview.status_code == 200
            assert "梁子湖流域自动站水质月报" in preview.text
            pdf = client.get(
                "/api/reports/water-quality/monthly/pdf?month=2025-07"
            )
            assert pdf.status_code == 200
            assert pdf.headers["content-type"] == "application/pdf"
            assert "202507.pdf" in pdf.headers["content-disposition"]

            class RejectFallback:
                async def handle_stream(self, request):
                    raise AssertionError("报表指令不得进入普通 Agent")
                    yield

            resolved_conversations: list[str | None] = []
            handler = WaterQualityReportChatHandler(
                RejectFallback(),
                StubService,
                lambda request: (
                    resolved_conversations.append(request.conversation_id)
                    or "mysql-lzh-monitor"
                ),
            )
            handler._renderer = renderer

            async def collect_report_chunks():
                request = ChatRequest(
                    message="生成2025年7月28日水质日报，只看水温",
                    conversation_id="report-chat-test",
                    metadata={"source_id": "mysql-lzh-monitor"},
                )
                return [chunk async for chunk in handler.handle_stream(request)]

            chunks = asyncio.run(collect_report_chunks())
            content = chunks[0].rich["data"]["content"]
            assert "已按固定模板生成" in content
            assert "应测指标：水温" in content
            assert "/daily/pdf?date=2025-07-28" in content
            assert resolved_conversations == ["report-chat-test"]

            class FailedService(StubService):
                def daily(self, report_date, **kwargs):
                    raise ReportDataSourceError("水质报表数据源暂不可用")

            failed_app = FastAPI()
            failed_app.include_router(create_report_router(FailedService))
            failed_client = TestClient(failed_app)
            failed_response = failed_client.get(
                "/api/reports/water-quality/daily?date=2025-07-28"
            )
            assert failed_response.status_code == 503
            assert "暂不可用" in failed_response.json()["detail"]

            class FailedRenderer:
                def render(self, report):
                    raise PdfRenderError("测试渲染失败")

            pdf_failed_app = FastAPI()
            pdf_failed_app.include_router(
                create_report_router(StubService, renderer=FailedRenderer())
            )
            assert pdf_failed_app is not None
            pdf_failed_client = TestClient(pdf_failed_app)
            assert pdf_failed_client.get(
                "/api/reports/water-quality/daily/pdf?date=2025-07-28"
            ).status_code == 500
        finally:
            if original is None:
                os.environ.pop("WATER_REPORT_OUTPUT_DIR", None)
            else:
                os.environ["WATER_REPORT_OUTPUT_DIR"] = original


def main() -> int:
    tests = [
        ("报告字段映射、缺测、类别变化和连续窗口规则", test_rules),
    ]
    failures = 0
    for name, callback in tests:
        try:
            callback()
            print(f"[PASS] {name}")
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
    try:
        daily, monthly = test_service()
        print("[PASS] 日报和月报计算")
        test_pdf_and_api(daily, monthly)
        print("[PASS] JSON API、PDF 文件名、32站跨页")
    except Exception as exc:
        failures += 1
        print(f"[FAIL] 报告服务或 PDF/API: {exc}")
        raise
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

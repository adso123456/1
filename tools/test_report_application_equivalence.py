"""普通工作台 Router 与 Embed Router 水质报表等价性回归测试。

Two HTTP adapters share one ``ReportApplicationService``.  This suite proves
the refactor kept both external contracts byte-for-byte identical where they
were identical before, and preserved the intentional differences (safe error
messages, Embed authorization) where they differed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import AssistantApplicationRegistry
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.water_quality_reports.api import create_report_router
from backend.water_quality_reports.application_service import (
    ReportApplicationService,
)
from backend.water_quality_reports.artifacts import (
    ReportArtifactError,
    ReportArtifactNotFound,
    ReportArtifactStore,
)
from backend.water_quality_reports.embed_api import create_embed_report_router
from backend.water_quality_reports.pdf_renderer import WaterQualityPdfRenderer
from backend.water_quality_reports.service import WaterQualityReportService
from config.data_source_config import DataSourceConfig
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer

APP_ID = "water-platform-demo"
ORIGIN = "http://127.0.0.1:15174"
UNAUTHORIZED_ORIGIN = "http://127.0.0.1:15176"
POSTGRES_SOURCE = "postgresql-main"
MYSQL_SOURCE = "mysql-lzh-monitor"

DAILY_PAYLOAD = {
    "report_type": "daily",
    "date": "2025-07-28",
    "indicators": [5],
    "frequency_hours": {"5": 4},
    "recent_days": 5,
}
MONTHLY_PAYLOAD = {
    "report_type": "monthly",
    "month": "2025-07",
    "indicators": [5, 0],
    "frequency_hours": {"5": 4},
    "recent_days": 3,
}


class FakeRepository:
    """Offline deterministic repository mirroring the report fixture data."""

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
                        "monitor_time": datetime.combine(
                            cursor, datetime.min.time()
                        ) + timedelta(hours=23),
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


def station(
    station_id: int,
    name: str,
    *,
    enabled: bool = True,
    station_type: str = "1",
) -> dict[str, object]:
    return {
        "id": station_id,
        "station_code": f"S{station_id}",
        "station_name": name,
        "station_type": station_type,
        "build_state": "1" if enabled else "0",
        "water_type": "0",
        "water_body_id": 1,
        "section_id": 10,
        "monitor_frequency": (
            '[{"indicatorCode":0,"indicatorName":"水温","frequency":1,'
            '"frequencySuffix":"1小时1次"},'
            '{"indicatorCode":5,"indicatorName":"氨氮","frequency":4,'
            '"frequencySuffix":"4小时1次"}]'
        ),
        "remark": "测试配置",
        "tributary_trunk": "1",
    }


class CountingReportService:
    """Wraps the real service and counts calculation invocations."""

    def __init__(self) -> None:
        self._inner = WaterQualityReportService(FakeRepository())
        self.options_calls = 0
        self.daily_calls = 0
        self.monthly_calls = 0

    def options(self):
        self.options_calls += 1
        return self._inner.options()

    def daily(
        self,
        report_date,
        *,
        indicator_codes=None,
        frequency_overrides=None,
        recent_days=3,
    ):
        self.daily_calls += 1
        return self._inner.daily(
            report_date,
            indicator_codes=indicator_codes,
            frequency_overrides=frequency_overrides,
            recent_days=recent_days,
        )

    def monthly(self, report_month, *, indicator_codes=None, frequency_overrides=None):
        self.monthly_calls += 1
        return self._inner.monthly(
            report_month,
            indicator_codes=indicator_codes,
            frequency_overrides=frequency_overrides,
        )


def build_equivalence_pair(temp_root: Path):
    """Ordinary and Embed apps sharing one real artifact store and service."""
    service = CountingReportService()
    store = ReportArtifactStore(lambda: service, WaterQualityPdfRenderer())
    shared = ReportApplicationService(store)

    ordinary = FastAPI()
    ordinary.include_router(
        create_report_router(lambda: service, artifact_store=store)
    )

    auth_calls: list[tuple] = []

    def authorize(app_id: str, origin: str | None, source_id: str | None = None):
        auth_calls.append((app_id, origin, source_id))

    embed = FastAPI()
    embed.include_router(
        create_embed_report_router(service=shared, authorize=authorize)
    )
    return ordinary, embed, service, store, auth_calls


def build_error_app(store):
    """Ordinary + Embed apps around a custom store that raises artifact errors."""
    shared = ReportApplicationService(store)
    ordinary = FastAPI()
    ordinary.include_router(create_report_router(lambda: None, artifact_store=store))
    embed = FastAPI()
    embed.include_router(
        create_embed_report_router(service=shared, authorize=lambda *a, **k: None)
    )
    return ordinary, embed


class RaisingGenerateStore:
    def options(self):
        return {"source_id": MYSQL_SOURCE, "indicators": [], "recent_days": []}

    def generate(self, **kwargs):
        raise ReportArtifactError("报表快照写入失败")


class MissingStore:
    def options(self):
        return {"source_id": MYSQL_SOURCE, "indicators": [], "recent_days": []}

    def generate(self, **kwargs):
        raise AssertionError("generate 不应被调用")

    def load(self, report_id):
        raise ReportArtifactNotFound("报表快照不存在或已清理")

    def pdf_path(self, report_id):
        raise ReportArtifactNotFound("报表快照不存在或已清理")


def config(root: Path, source_id: str, database_type: str = "offline"):
    settings: dict[str, object] = {"label": source_id}
    if database_type == "mysql":
        settings = {
            "host": "127.0.0.1",
            "port": 1,
            "database": "offline",
            "user": "offline",
            "password": "offline",
            "connect_timeout": 1,
        }
    return DataSourceConfig(
        source_id=source_id,
        database_type=database_type,
        sql_dialect=database_type,
        connection_settings=settings,
        metadata_path=root / f"{source_id}.json",
        memory_path=root / f"{source_id}-memory",
        read_only=True,
    )


def build_embed_server(
    root: Path,
    store,
    *,
    enabled: bool = True,
    allowed_sources: tuple[str, ...] = (POSTGRES_SOURCE, MYSQL_SOURCE),
) -> FastAPI:
    registry = DataSourceRegistry(
        (
            config(root, POSTGRES_SOURCE),
            config(root, MYSQL_SOURCE, "mysql"),
        )
    )
    coordinator = DataSourceRequestCoordinator(registry)

    def factory(item: DataSourceConfig) -> DataSourceRuntime:
        return DataSourceRuntime(
            config=item,
            runner=object(),
            memory=object(),
            metadata_retriever=object(),
            sql_guard=object(),
            agent=object(),
        )

    manager = DataSourceRuntimeManager(registry, {"offline": factory, "mysql": factory})
    app_resources = ApplicationResources(registry, coordinator, manager)
    app_registry = AssistantApplicationRegistry(
        root / "assistant-apps.sqlite3", registry
    )
    app_registry.create(
        app_id=APP_ID,
        name="水务管理平台助手",
        enabled=enabled,
        allowed_origins=(ORIGIN,),
        allowed_source_ids=allowed_sources,
    )
    with (
        patch("step4_server.ReportArtifactStore", lambda *a, **k: store),
        patch(
            "backend.water_quality_reports.application_service.render_report_html",
            lambda _report: "<html>preview-ok</html>",
        ),
    ):
        server = DataSourceVannaFastAPIServer(
            app_resources,
            assistant_application_registry=app_registry,
        )
        return server.create_app()


class TrackingReportStore:
    """Fake store that counts every artifact operation."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def options(self):
        self.calls.append("options")
        return {
            "source_id": MYSQL_SOURCE,
            "indicators": [{"code": 1, "name": "pH", "frequencies": [1]}],
            "recent_days": [2, 3],
        }

    def generate(self, **kwargs):
        self.calls.append("generate")
        return {
            "report_id": "wqr-" + "a" * 32,
            "report_type": "daily",
            "title": "测试日报",
            "period": "2025-07-28",
            "indicators": [1],
            "frequency_hours": {},
            "source_id": MYSQL_SOURCE,
            "preview_url": "/ordinary-preview",
            "download_url": "/ordinary-pdf",
            "status": "报告已生成",
        }

    def load(self, _report_id):
        self.calls.append("load")
        return {"report_type": "daily", "report_date": "2025-07-28"}

    def pdf_path(self, _report_id):
        self.calls.append("pdf_path")
        path = Path(tempfile.gettempdir()) / "tracking-embed.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF")
        return path


class CountingStore:
    """Wraps a real store and counts generate invocations."""

    def __init__(self, inner: ReportArtifactStore) -> None:
        self._inner = inner
        self.generate_calls = 0

    def options(self):
        return self._inner.options()

    def generate(self, **kwargs):
        self.generate_calls += 1
        return self._inner.generate(**kwargs)

    def load(self, report_id):
        return self._inner.load(report_id)

    def pdf_path(self, report_id):
        return self._inner.pdf_path(report_id)


def run_checks() -> int:
    failures = 0
    temp_holder = tempfile.TemporaryDirectory(prefix="report-equiv-")
    temp_root = Path(temp_holder.name).resolve()
    original = os.environ.get("WATER_REPORT_OUTPUT_DIR")
    os.environ["WATER_REPORT_OUTPUT_DIR"] = str(temp_root)
    try:
        ordinary, embed, service, _store, auth_calls = build_equivalence_pair(temp_root)
        oc = TestClient(ordinary)
        ec = TestClient(embed)
        base = f"/api/embed/apps/{APP_ID}/reports"
        embed_headers = {"Origin": ORIGIN}

        # 1. options 成功结果一致
        ordinary_options = oc.get("/api/reports/water-quality/options").json()
        embed_options = ec.get(f"{base}/options", headers=embed_headers).json()
        assert ordinary_options == embed_options, "options 结果不一致"
        log(f"[PASS] 1. options 成功结果一致")

        # 2/3. 日报、月报生成成功且结果一致
        ordinary_daily = oc.post(
            "/api/reports/water-quality/generate", json=DAILY_PAYLOAD
        )
        embed_daily = ec.post(
            f"{base}/generate", json=DAILY_PAYLOAD, headers=embed_headers
        )
        assert ordinary_daily.status_code == 200, ordinary_daily.text
        assert embed_daily.status_code == 200, embed_daily.text
        assert ordinary_daily.json() == embed_daily.json(), "日报生成结果不一致"
        log(f"[PASS] 2. 日报生成成功且两套一致")

        ordinary_monthly = oc.post(
            "/api/reports/water-quality/generate", json=MONTHLY_PAYLOAD
        )
        embed_monthly = ec.post(
            f"{base}/generate", json=MONTHLY_PAYLOAD, headers=embed_headers
        )
        assert ordinary_monthly.status_code == 200, ordinary_monthly.text
        assert embed_monthly.status_code == 200, embed_monthly.text
        assert ordinary_monthly.json() == embed_monthly.json(), "月报生成结果不一致"
        log(f"[PASS] 3. 月报生成成功且两套一致")

        # 4. 指标顺序去重保持（payload 含重复与乱序）
        duplicate_payload = {
            **DAILY_PAYLOAD,
            "indicators": [5, 5, 0, 5, 0],
        }
        dup_result = oc.post(
            "/api/reports/water-quality/generate", json=duplicate_payload
        ).json()
        assert dup_result["indicators"] == [5, 0], dup_result["indicators"]
        assert ec.post(
            f"{base}/generate", json=duplicate_payload, headers=embed_headers
        ).json()["indicators"] == [5, 0]
        log(f"[PASS] 4. 指标顺序去重保持")

        # 5. 频次键转换保持
        assert dup_result["frequency_hours"] == {"5": 4}, (
            dup_result["frequency_hours"]
        )
        log(f"[PASS] 5. 频次键转换保持")

        # 6-9. 日报/月报互斥规则
        rules = [
            (
                {**DAILY_PAYLOAD, "date": None, "month": None},
                "日报必须且只能提供 date",
            ),
            (
                {**DAILY_PAYLOAD, "date": None, "month": "2025-07"},
                "日报必须且只能提供 date",
            ),
            (
                {**MONTHLY_PAYLOAD, "month": None, "date": None},
                "月报必须且只能提供 month",
            ),
            (
                {**MONTHLY_PAYLOAD, "month": None, "date": "2025-07-28"},
                "月报必须且只能提供 month",
            ),
        ]
        for index, (payload, message) in enumerate(rules, start=6):
            ordinary_response = oc.post(
                "/api/reports/water-quality/generate", json=payload
            )
            embed_response = ec.post(
                f"{base}/generate", json=payload, headers=embed_headers
            )
            assert ordinary_response.status_code == 422, ordinary_response.text
            assert embed_response.status_code == 422, embed_response.text
            assert ordinary_response.json()["detail"] == message
            assert embed_response.json()["detail"] == message
            log(f"[PASS] {index}. 互斥规则：{message}")

        # 10. 非法日期和月份
        invalid_cases = [
            ({**DAILY_PAYLOAD, "date": "2025-13-01"}, "日期无效"),
            ({**DAILY_PAYLOAD, "date": "2025-07-28x"}, "日期必须为 YYYY-MM-DD"),
            ({**MONTHLY_PAYLOAD, "month": "2025-13"}, "月份无效"),
            ({**MONTHLY_PAYLOAD, "month": "202507"}, "月份必须为 YYYY-MM"),
        ]
        for payload, message in invalid_cases:
            ordinary_response = oc.post(
                "/api/reports/water-quality/generate", json=payload
            )
            embed_response = ec.post(
                f"{base}/generate", json=payload, headers=embed_headers
            )
            assert ordinary_response.status_code == 422, ordinary_response.text
            assert embed_response.status_code == 422, embed_response.text
            assert ordinary_response.json()["detail"] == message
            assert embed_response.json()["detail"] == message
        log(f"[PASS] 10. 非法日期和月份返回 422 且文案一致")

        # 11. ReportArtifactError 分类（普通 422 明文 / Embed 422 安全文案）
        error_ordinary, error_embed = build_error_app(RaisingGenerateStore())
        eoc = TestClient(error_ordinary)
        eec = TestClient(error_embed)
        error_payload = {**DAILY_PAYLOAD, "date": "2025-07-28"}
        eo = eoc.post("/api/reports/water-quality/generate", json=error_payload)
        ee = eec.post(f"{base}/generate", json=error_payload, headers=embed_headers)
        assert eo.status_code == 422, eo.text
        assert ee.status_code == 422, ee.text
        assert eo.json()["detail"] == "报表快照写入失败"
        assert ee.json()["detail"] == "报表生成失败，请检查报表参数"
        log(f"[PASS] 11. ReportArtifactError 分类（普通明文 / Embed 安全文案）")

        # 12. ReportArtifactNotFound（普通 404 明文 / Embed 404 安全文案）
        missing_ordinary, missing_embed = build_error_app(MissingStore())
        moc = TestClient(missing_ordinary)
        mec = TestClient(missing_embed)
        mo = moc.get("/api/reports/water-quality/artifacts/not-valid/preview")
        me = mec.get(f"{base}/artifacts/not-valid/preview", headers=embed_headers)
        assert mo.status_code == 404 and me.status_code == 404
        assert mo.json()["detail"] == "报表快照不存在或已清理"
        assert me.json()["detail"] == "报告不存在"
        mop = moc.get("/api/reports/water-quality/artifacts/not-valid/pdf")
        mep = mec.get(f"{base}/artifacts/not-valid/pdf", headers=embed_headers)
        assert mop.status_code == 404 and mep.status_code == 404
        log(f"[PASS] 12. ReportArtifactNotFound 返回 404")

        # 13. HTML 预览内容一致
        ordinary_preview = oc.get(
            "/api/reports/water-quality/artifacts/"
            f"{ordinary_daily.json()['report_id']}/preview"
        )
        embed_preview = ec.get(
            f"{base}/artifacts/{embed_daily.json()['report_id']}/preview",
            headers=embed_headers,
        )
        assert ordinary_preview.status_code == 200, ordinary_preview.text
        assert embed_preview.status_code == 200, embed_preview.text
        assert ordinary_preview.text == embed_preview.text, "预览 HTML 不一致"
        log(f"[PASS] 13. HTML 预览内容一致")

        # 14. PDF 内容、媒体类型和文件名一致
        ordinary_pdf = oc.get(
            "/api/reports/water-quality/artifacts/"
            f"{ordinary_daily.json()['report_id']}/pdf"
        )
        embed_pdf = ec.get(
            f"{base}/artifacts/{embed_daily.json()['report_id']}/pdf",
            headers=embed_headers,
        )
        assert ordinary_pdf.status_code == 200, ordinary_pdf.text
        assert embed_pdf.status_code == 200, embed_pdf.text
        assert ordinary_pdf.headers["content-type"] == "application/pdf"
        assert embed_pdf.headers["content-type"] == "application/pdf"
        ordinary_disposition = ordinary_pdf.headers["content-disposition"]
        embed_disposition = embed_pdf.headers["content-disposition"]
        assert "filename*=utf-8''" in ordinary_disposition, ordinary_disposition
        decoded = urllib.parse.unquote(
            ordinary_disposition.split("filename*=utf-8''", 1)[1].split(";")[0]
        )
        assert decoded == "梁子湖流域自动站水质日报_20250728.pdf", decoded
        assert ordinary_disposition == embed_disposition, (
            ordinary_disposition, embed_disposition,
        )
        assert ordinary_pdf.content == embed_pdf.content, "PDF 内容不一致"
        log(f"[PASS] 14. PDF 内容、媒体类型和文件名一致")

        # 15. 同一次 generate 只调用一次底层计算
        service.daily_calls = 0
        oc.post("/api/reports/water-quality/generate", json=DAILY_PAYLOAD)
        assert service.daily_calls == 1, service.daily_calls
        service.monthly_calls = 0
        oc.post("/api/reports/water-quality/generate", json=MONTHLY_PAYLOAD)
        assert service.monthly_calls == 1, service.monthly_calls
        service.daily_calls = 0
        ec.post(f"{base}/generate", json=DAILY_PAYLOAD, headers=embed_headers)
        assert service.daily_calls == 1, service.daily_calls
        log(f"[PASS] 15. 同一次 generate 只调用一次底层计算")

        # 16. 同一 report_id 的预览和 PDF 来自同一不可变快照（幂等快照）
        repeated = oc.post(
            "/api/reports/water-quality/generate", json=DAILY_PAYLOAD
        ).json()
        assert repeated["report_id"] == ordinary_daily.json()["report_id"]
        repeated_preview = oc.get(
            "/api/reports/water-quality/artifacts/"
            f"{repeated['report_id']}/preview"
        ).text
        assert repeated_preview == ordinary_preview.text, "快照内容不可变"
        log(f"[PASS] 16. 同一 report_id 来自同一不可变快照")

        # 21. Embed 授权成功后与普通业务结果一致
        assert ordinary_options == embed_options
        assert ordinary_daily.json() == embed_daily.json()
        log(f"[PASS] 21. Embed 授权成功后与普通业务结果一致")

        # 17. 未授权 Origin 不触碰 Store
        t17 = TrackingReportStore()
        with TestClient(build_embed_server(temp_root / "s17", t17)) as sc:
            assert_status(
                sc.get(f"{base}/options", headers={"Origin": UNAUTHORIZED_ORIGIN}),
                403,
            )
            assert t17.calls == [], t17.calls
        log(f"[PASS] 17. Embed 未授权 Origin 不触碰 Store")

        # 18. 缺失 Origin 不触碰 Store
        t18 = TrackingReportStore()
        with TestClient(build_embed_server(temp_root / "s18", t18)) as sc:
            assert_status(sc.get(f"{base}/options"), 401)
            assert t18.calls == [], t18.calls
        log(f"[PASS] 18. Embed 缺失 Origin 不触碰 Store")

        # 19. 停用应用不触碰 Store
        t19 = TrackingReportStore()
        with TestClient(
            build_embed_server(temp_root / "s19", t19, enabled=False)
        ) as sc:
            assert_status(sc.get(f"{base}/options", headers={"Origin": ORIGIN}), 403)
            assert t19.calls == [], t19.calls
        log(f"[PASS] 19. Embed 停用应用不触碰 Store")

        # 20. 未授权 mysql-lzh-monitor 数据源不触碰 Store
        t20 = TrackingReportStore()
        with TestClient(
            build_embed_server(
                temp_root / "s20", t20, allowed_sources=(POSTGRES_SOURCE,)
            )
        ) as sc:
            assert_status(sc.get(f"{base}/options", headers={"Origin": ORIGIN}), 403)
            assert t20.calls == [], t20.calls
        log(f"[PASS] 20. Embed 未授权 mysql-lzh-monitor 不触碰 Store")

        # 22. Embed OPTIONS/CORS 行为不变（授权成功后）
        t22 = TrackingReportStore()
        with TestClient(build_embed_server(temp_root / "s22", t22)) as sc:
            preflight = sc.options(
                f"{base}/options",
                headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert preflight.status_code == 204, preflight.text
            assert preflight.headers["access-control-allow-origin"] == ORIGIN
            assert "origin" in preflight.headers["vary"].lower()
            assert "*" not in preflight.headers["access-control-allow-origin"]
            assert "access-control-allow-credentials" not in preflight.headers
            # 授权成功后才允许访问 store
            assert t22.calls == [], t22.calls
            authorized = sc.get(f"{base}/options", headers={"Origin": ORIGIN})
            assert authorized.status_code == 200, authorized.text
            assert t22.calls == ["options"], t22.calls
        log(f"[PASS] 22. Embed OPTIONS/CORS 行为不变，授权后才触碰 Store")

        # 负数指标异常合同（正式复审阻断项修复回归）
        check_negative_indicator_contract()

        # 23. Widget report RPC 的 Loader URL 映射未变
        loader_source = (
            PROJECT_ROOT / "frontend" / "public" / "water-agent-widget.js"
        ).read_text(encoding="utf-8")
        assert "/reports/options" in loader_source
        assert "/reports/generate" in loader_source
        assert "/reports/artifacts/" in loader_source
        assert "/preview" in loader_source and "/pdf" in loader_source
        assert "filename\\*=UTF-8''" in loader_source
        log(f"[PASS] 23. Widget report RPC Loader URL 映射保持")
    finally:
        if original is None:
            os.environ.pop("WATER_REPORT_OUTPUT_DIR", None)
        else:
            os.environ["WATER_REPORT_OUTPUT_DIR"] = original
        temp_holder.cleanup()
    return failures


def log(message: str) -> None:
    print(message)


def assert_status(response, status: int) -> None:
    assert response.status_code == status, (
        response.status_code,
        response.text,
    )


def check_negative_indicator_contract() -> None:
    """Negative indicator contract restored by the fix.

    Ordinary: 422 with the original message, store never touched.
    Embed: 500 through the real underlying ValueError path, store actually
    entered once (proving no HTTP-layer early rejection).
    """
    service = CountingReportService()
    store = CountingStore(
        ReportArtifactStore(lambda: service, WaterQualityPdfRenderer())
    )
    shared = ReportApplicationService(store)
    ordinary = FastAPI()
    ordinary.include_router(
        create_report_router(lambda: service, artifact_store=store)
    )
    embed = FastAPI()
    embed.include_router(
        create_embed_report_router(service=shared, authorize=lambda *a, **k: None)
    )
    oc = TestClient(ordinary)
    # 未捕获的 ValueError 必须作为真实 500 response 返回，而非在客户端 raise。
    ec = TestClient(embed, raise_server_exceptions=False)
    base = f"/api/embed/apps/{APP_ID}/reports"
    headers = {"Origin": ORIGIN}

    negative = {
        "report_type": "daily",
        "date": "2025-07-28",
        "indicators": [-1],
        "frequency_hours": {},
        "recent_days": 3,
    }
    ordinary_response = oc.post("/api/reports/water-quality/generate", json=negative)
    assert ordinary_response.status_code == 422, ordinary_response.text
    assert ordinary_response.json()["detail"] == "至少选择一个有效指标"
    assert store.generate_calls == 0, store.generate_calls
    log(f"[PASS] 普通负数指标 → 422 且不触碰 Store")

    embed_response = ec.post(f"{base}/generate", json=negative, headers=headers)
    assert embed_response.status_code == 500, embed_response.text
    assert store.generate_calls == 1, store.generate_calls
    log(f"[PASS] Embed 负数指标 → 500 且实际进入 Store 与底层 ValueError 路径")

    dedupe = {
        "report_type": "daily",
        "date": "2025-07-28",
        "indicators": [5, 5, 0, 5, 0],
        "frequency_hours": {},
        "recent_days": 3,
    }
    ordinary_dedupe = oc.post(
        "/api/reports/water-quality/generate", json=dedupe
    ).json()
    embed_dedupe = ec.post(f"{base}/generate", json=dedupe, headers=headers).json()
    assert ordinary_dedupe["indicators"] == [5, 0], ordinary_dedupe["indicators"]
    assert embed_dedupe["indicators"] == [5, 0], embed_dedupe["indicators"]
    assert ordinary_dedupe == embed_dedupe
    log(f"[PASS] 正常指标去重保持 [5, 5, 0, 5, 0] → [5, 0]（普通与 Embed）")


def main() -> int:
    try:
        failures = run_checks()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return 1
    if failures:
        print(f"[FAIL] {failures} 项失败")
    else:
        print("report application equivalence: all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

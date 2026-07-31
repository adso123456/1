"""无凭据 Embed Origin/CORS、数据源和报表授权离线测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import AssistantApplicationRegistry
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from config.data_source_config import DataSourceConfig
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer

APP_ID = "water-platform-demo"
ORIGIN = "http://127.0.0.1:15174"
UNAUTHORIZED_ORIGIN = "http://127.0.0.1:15176"
POSTGRES_SOURCE = "postgresql-main"
MYSQL_SOURCE = "mysql-lzh-monitor"
FORBIDDEN_SOURCE = "forbidden-source"


class FakeComponent:
    def model_dump_json(self) -> str:
        return (
            '{"rich":{"type":"text","id":"embed-text",'
            '"lifecycle":"complete","timestamp":"offline","visible":true,'
            '"interactive":false,"data":{"content":"embed-agent-ok"}},'
            '"simple":null,"conversation_id":"embed-conversation",'
            '"request_id":"embed-request","timestamp":1}'
        )


class FakeHandler:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def handle_stream(self, request):
        self.requests.append(request)
        yield FakeComponent()


class FakeReportStore:
    def __init__(self, *_args, **_kwargs) -> None:
        self.root = Path(tempfile.gettempdir())

    def options(self) -> dict[str, Any]:
        return {
            "source_id": MYSQL_SOURCE,
            "indicators": [{"code": 1, "name": "pH", "frequencies": [1]}],
            "recent_days": [2, 3],
        }

    def generate(self, **_kwargs) -> dict[str, Any]:
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

    def load(self, _report_id: str) -> dict[str, Any]:
        return {
            "report_type": "daily",
            "report_date": "2025-07-28",
        }

    def pdf_path(self, _report_id: str) -> Path:
        path = self.root / "embed-origin-test.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF")
        return path


def config(
    root: Path,
    source_id: str,
    database_type: str = "offline",
) -> DataSourceConfig:
    settings: dict[str, Any] = {"label": source_id}
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


def resources(root: Path):
    registry = DataSourceRegistry(
        (
            config(root, POSTGRES_SOURCE),
            config(root, MYSQL_SOURCE, "mysql"),
            config(root, FORBIDDEN_SOURCE),
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

    manager = DataSourceRuntimeManager(
        registry,
        {"offline": factory, "mysql": factory},
    )
    return ApplicationResources(registry, coordinator, manager)


def assert_status(response, status: int) -> None:
    assert response.status_code == status, (
        response.status_code,
        response.text,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="embed-origin-v4-") as name:
        root = Path(name).resolve()
        app_resources = resources(root)
        registry = AssistantApplicationRegistry(
            root / "assistant-apps.sqlite3",
            app_resources.registry,
        )
        registry.create(
            app_id=APP_ID,
            name="水务管理平台助手",
            enabled=True,
            allowed_origins=(ORIGIN,),
            allowed_source_ids=(POSTGRES_SOURCE, MYSQL_SOURCE),
        )
        with (
            patch("step4_server.ReportArtifactStore", FakeReportStore),
            patch(
                "step4_server.render_report_html",
                lambda _report: "<html>preview-ok</html>",
            ),
        ):
            server = DataSourceVannaFastAPIServer(
                app_resources,
                assistant_application_registry=registry,
            )
            handler = FakeHandler()
            server.chat_handler = handler
            app = server.create_app()
            with TestClient(app) as client:
                base = f"/api/embed/apps/{APP_ID}"
                headers = {"Origin": ORIGIN}

                preflight = client.options(
                    f"{base}/chat_sse",
                    headers={
                        **headers,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                )
                assert_status(preflight, 204)
                assert preflight.headers["access-control-allow-origin"] == ORIGIN
                assert preflight.headers["vary"] == "Origin"
                assert "*" not in preflight.headers.values()
                assert "access-control-allow-credentials" not in preflight.headers

                application = client.get(
                    f"{base}/application",
                    headers=headers,
                )
                assert_status(application, 200)
                assert application.headers["access-control-allow-origin"] == ORIGIN
                assert set(application.json()) == {
                    "app_id",
                    "name",
                    "theme",
                    "header_font_color",
                    "logo_url",
                    "welcome",
                    "welcome_description",
                    "float_icon_url",
                    "float_icon_draggable",
                    "float_x_anchor",
                    "float_x_offset",
                    "float_y_anchor",
                    "float_y_offset",
                    "show_history",
                }
                assert "application_links" not in application.text

                sources = client.get(f"{base}/data-sources", headers=headers)
                assert_status(sources, 200)
                assert {item["source_id"] for item in sources.json()} == {
                    POSTGRES_SOURCE,
                    MYSQL_SOURCE,
                }

                body = {
                    "message": "hello",
                    "conversation_id": "embed-conversation",
                    "request_id": "embed-request",
                    "metadata": {"source_id": POSTGRES_SOURCE},
                }
                chat = client.post(
                    f"{base}/chat_sse",
                    headers=headers,
                    json=body,
                )
                assert_status(chat, 200)
                assert "embed-agent-ok" in chat.text
                assert "data: [DONE]" in chat.text
                assert handler.requests

                forbidden_body = {
                    **body,
                    "metadata": {"source_id": FORBIDDEN_SOURCE},
                }
                assert_status(
                    client.post(
                        f"{base}/chat_sse",
                        headers=headers,
                        json=forbidden_body,
                    ),
                    403,
                )
                assert len(handler.requests) == 1

                report_options = client.get(
                    f"{base}/reports/options",
                    headers=headers,
                )
                assert_status(report_options, 200)
                generated = client.post(
                    f"{base}/reports/generate",
                    headers=headers,
                    json={
                        "report_type": "daily",
                        "date": "2025-07-28",
                        "indicators": [1],
                        "frequency_hours": {},
                        "recent_days": 3,
                    },
                )
                assert_status(generated, 200)
                report_id = generated.json()["report_id"]
                preview = client.get(
                    f"{base}/reports/artifacts/{report_id}/preview",
                    headers=headers,
                )
                pdf = client.get(
                    f"{base}/reports/artifacts/{report_id}/pdf",
                    headers=headers,
                )
                assert_status(preview, 200)
                assert "preview-ok" in preview.text
                assert_status(pdf, 200)
                assert pdf.headers["content-type"] == "application/pdf"

                assert_status(client.get(f"{base}/application"), 401)
                assert_status(
                    client.get(
                        f"{base}/application",
                        headers={"Origin": UNAUTHORIZED_ORIGIN},
                    ),
                    403,
                )
                assert_status(
                    client.options(
                        f"{base}/application",
                        headers={"Origin": UNAUTHORIZED_ORIGIN},
                    ),
                    403,
                )
                assert_status(
                    client.get(
                        "/api/embed/apps/unknown-app/application",
                        headers=headers,
                    ),
                    401,
                )
                registry.disable(APP_ID)
                assert_status(
                    client.get(f"{base}/application", headers=headers),
                    403,
                )

                ordinary = client.get("/api/data-sources")
                assert_status(ordinary, 200)
                assert "access-control-allow-origin" not in ordinary.headers

    print("embed origin/CORS/Widget/report access: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""管理员 protected Widget 预览 Token 的真实 SQLite/HTTP 检查。"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import jwt
import uvicorn
from backend.assistant_application_registry import AssistantApplicationRegistry
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.embed_access import (
    ADMIN_PREVIEW_AUDIENCE,
    EMBED_ALGORITHM,
    EMBED_AUDIENCE,
    EmbedAccessError,
    derive_admin_preview_signing_key,
    issue_admin_preview_token,
    verify_embed_access_token,
)
from fastapi.testclient import TestClient
from config.data_source_config import DataSourceConfig
from tools.test_assistant_admin_api import make_resources
from step4_server import (
    ApplicationResources,
    DataSourceVannaFastAPIServer,
)

ADMIN_TOKEN = secrets.token_urlsafe(40)
OTHER_ADMIN_TOKEN = secrets.token_urlsafe(40)
APP_SECRET = secrets.token_urlsafe(40)
APP_ID = "preview-test-app"
SOURCE_ID = "source-a"
ADMIN_ORIGIN = "http://127.0.0.1:5173"
HOST_ORIGIN = "http://127.0.0.1:5174"


def expect_error(
    status: int,
    action,
) -> None:
    try:
        action()
    except EmbedAccessError as exc:
        assert exc.status_code == status, (status, exc.status_code)
        return
    raise AssertionError(f"预期 EmbedAccessError({status})")


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="assistant-admin-preview-",
    ) as raw_root:
        resources = make_resources(Path(raw_root).resolve())
        registry = resources.assistant_application_registry
        assert registry is not None
        registry.create(
            app_id=APP_ID,
            name="Preview test",
            enabled=True,
            app_secret=APP_SECRET,
            allowed_origins=(HOST_ORIGIN,),
            allowed_source_ids=(SOURCE_ID,),
            token_ttl_seconds=300,
        )
        app = DataSourceVannaFastAPIServer(
            resources,
            admin_environ={
                "WATER_AGENT_ADMIN_ENABLED": "true",
                "WATER_AGENT_ADMIN_TOKEN": ADMIN_TOKEN,
            },
        ).create_app()
        with TestClient(
            app,
            base_url=ADMIN_ORIGIN,
            client=("127.0.0.1", 50123),
        ) as client:
            path = (
                f"/api/admin/assistant-applications/{APP_ID}"
                "/preview-token"
            )
            headers = {
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Origin": ADMIN_ORIGIN,
            }
            response = client.post(path, headers=headers)
            assert response.status_code == 200, response.text
            assert set(response.json()) == {"token", "expires_at"}
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["pragma"] == "no-cache"
            preview_token = response.json()["token"]
            claims = jwt.decode(
                preview_token,
                options={"verify_signature": False},
                algorithms=[EMBED_ALGORITHM],
            )
            assert claims["aud"] == ADMIN_PREVIEW_AUDIENCE
            assert claims["token_use"] == "admin-preview"
            assert claims["parent_origin"] == ADMIN_ORIGIN
            assert claims["allowed_source_ids"] == [SOURCE_ID]
            assert 0 < claims["exp"] - claims["iat"] <= 120
            assert response.json()["expires_at"] == claims["exp"]

            embed_headers = {
                "Authorization": f"Bearer {preview_token}",
                "X-Water-Agent-Parent-Origin": ADMIN_ORIGIN,
            }
            application_response = client.get(
                "/api/embed/application",
                headers=embed_headers,
            )
            assert application_response.status_code == 200
            assert application_response.json()["app_id"] == APP_ID
            sources_response = client.get(
                "/api/embed/data-sources",
                headers=embed_headers,
            )
            assert sources_response.status_code == 200
            assert sources_response.json() == [
                {"source_id": SOURCE_ID, "database_type": "offline"}
            ]
            chat_response = client.post(
                "/api/embed/vanna/v2/chat_sse",
                headers=embed_headers,
                json={
                    "message": "preview",
                    "conversation_id": "preview-conversation",
                    "metadata": {"source_id": SOURCE_ID},
                },
            )
            assert chat_response.status_code == 200
            assert "data: [DONE]" in chat_response.text

            assert client.post(path, headers={"Origin": ADMIN_ORIGIN}).status_code == 401
            assert client.post(
                path,
                headers={
                    "Authorization": f"Bearer {OTHER_ADMIN_TOKEN}",
                    "Origin": ADMIN_ORIGIN,
                },
            ).status_code == 401
            assert client.post(
                path,
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            ).status_code == 403
            assert client.post(
                path,
                headers={
                    "Authorization": f"Bearer {ADMIN_TOKEN}",
                    "Origin": "http://localhost:5173",
                },
            ).status_code == 403
            assert client.post(
                path,
                headers={
                    "Authorization": f"Bearer {ADMIN_TOKEN}",
                    "Origin": "https://example.com",
                },
            ).status_code == 403
            assert client.post(
                "/api/admin/assistant-applications/missing/preview-token",
                headers=headers,
            ).status_code == 404

            expect_error(
                401,
                lambda: verify_embed_access_token(
                    preview_token,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    preview_token,
                    parent_origin="http://127.0.0.1:5175",
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    preview_token,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=OTHER_ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            tampered = (
                preview_token[:-1]
                + ("a" if preview_token[-1] != "a" else "b")
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    tampered,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            expect_error(
                403,
                lambda: verify_embed_access_token(
                    preview_token,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(),
                ),
            )

            expired_token, _ = issue_admin_preview_token(
                admin_token=ADMIN_TOKEN,
                application=registry.get(APP_ID),
                parent_origin=ADMIN_ORIGIN,
                allowed_source_ids=(SOURCE_ID,),
                now=int(time.time()) - 500,
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    expired_token,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )

            forged_preview = jwt.encode(
                claims,
                APP_SECRET,
                algorithm=EMBED_ALGORITHM,
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    forged_preview,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            ordinary_claims = {
                **claims,
                "aud": EMBED_AUDIENCE,
                "sub": "host-user",
            }
            forged_ordinary = jwt.encode(
                ordinary_claims,
                derive_admin_preview_signing_key(ADMIN_TOKEN),
                algorithm=EMBED_ALGORITHM,
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    forged_ordinary,
                    parent_origin=HOST_ORIGIN,
                    registry=registry,
                ),
            )
            unknown = jwt.encode(
                {**claims, "aud": "unknown-audience"},
                derive_admin_preview_signing_key(ADMIN_TOKEN),
                algorithm=EMBED_ALGORITHM,
            )
            expect_error(
                401,
                lambda: verify_embed_access_token(
                    unknown,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )

            registry.disable(APP_ID)
            assert client.post(path, headers=headers).status_code == 403
            expect_error(
                403,
                lambda: verify_embed_access_token(
                    preview_token,
                    parent_origin=ADMIN_ORIGIN,
                    registry=registry,
                    allow_admin_preview=True,
                    admin_token=ADMIN_TOKEN,
                    current_source_ids=(SOURCE_ID,),
                ),
            )
            registry.enable(APP_ID)

        stale_config = DataSourceConfig(
            source_id="source-current",
            database_type="offline",
            sql_dialect="offline",
            connection_settings={"label": "source-current"},
            metadata_path=Path(raw_root) / "current-metadata.json",
            memory_path=Path(raw_root) / "current-memory",
            read_only=True,
        )
        current_sources = DataSourceRegistry((stale_config,))
        stale_registry = AssistantApplicationRegistry(
            registry.db_path,
            current_sources,
        )
        stale_resources = ApplicationResources(
            current_sources,
            DataSourceRequestCoordinator(current_sources),
            DataSourceRuntimeManager(
                current_sources,
                {"offline": lambda _config: None},
            ),
            stale_registry,
        )
        stale_app = DataSourceVannaFastAPIServer(
            stale_resources,
            admin_environ={
                "WATER_AGENT_ADMIN_ENABLED": "true",
                "WATER_AGENT_ADMIN_TOKEN": ADMIN_TOKEN,
            },
        ).create_app()
        with TestClient(
            stale_app,
            base_url=ADMIN_ORIGIN,
            client=("127.0.0.1", 50125),
        ) as stale_client:
            assert stale_client.post(path, headers=headers).status_code == 409

        with TestClient(
            app,
            base_url=ADMIN_ORIGIN,
            client=("192.0.2.10", 50124),
        ) as remote_client:
            assert remote_client.post(path, headers=headers).status_code == 403
            assert remote_client.get(
                "/api/embed/application",
                headers=embed_headers,
            ).status_code == 403

    print("assistant admin protected preview: all checks passed")
    return 0


def serve_browser_demo() -> None:
    admin_token = os.environ["WATER_AGENT_TEST_ADMIN_TOKEN"]
    app_secret = os.environ["WATER_AGENT_TEST_APP_SECRET"]
    root = Path(
        os.environ["WATER_AGENT_TEST_ROOT"]
    ).resolve()
    resources = make_resources(root)
    registry = resources.assistant_application_registry
    assert registry is not None
    registry.create(
        app_id=APP_ID,
        name="A3 protected preview",
        enabled=True,
        app_secret=app_secret,
        allowed_origins=(HOST_ORIGIN,),
        allowed_source_ids=(SOURCE_ID,),
        token_ttl_seconds=120,
    )
    app = DataSourceVannaFastAPIServer(
        resources,
        admin_environ={
            "WATER_AGENT_ADMIN_ENABLED": "true",
            "WATER_AGENT_ADMIN_TOKEN": admin_token,
        },
    ).create_app()
    preview_delay = float(
        os.environ.get("WATER_AGENT_TEST_PREVIEW_DELAY", "0")
    )
    if preview_delay:
        preview_request_count = {"value": 0}

        @app.middleware("http")
        async def delay_preview_token(request, call_next):
            if request.url.path.endswith("/preview-token"):
                preview_request_count["value"] += 1
                await asyncio.sleep(preview_delay)
            return await call_next(request)

        @app.get("/__test__/preview-request-count")
        async def preview_request_count_value():
            return {"count": preview_request_count["value"]}
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    if "--serve-browser-demo" in sys.argv:
        serve_browser_demo()
    else:
        raise SystemExit(main())

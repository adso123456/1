"""本机管理员 API 与 SQLite V1 迁移真实行为测试。"""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

import uvicorn
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_admin_api import AdminConfigurationError
from backend.assistant_application_registry import (
    SCHEMA_COMPONENT,
    SCHEMA_VERSION_TABLE,
    AssistantApplicationRegistry,
    SchemaMigrationError,
)
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime import DataSourceRuntime
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.embed_access import EmbedApplicationConfig, issue_embed_token, verify_embed_token
from config.data_source_config import DataSourceConfig
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer


ADMIN_TOKEN = "temporary-admin-token-with-at-least-32-characters"
APP_SECRET_FIELD = "app_secret"
SOURCE_ID = "source-a"
ORIGIN = "http://127.0.0.1"


class FakeAgent:
    async def send_message(self, **_: Any):
        if False:
            yield None


def make_resources(root: Path) -> ApplicationResources:
    source_registry = DataSourceRegistry(
        (
            DataSourceConfig(
                source_id=SOURCE_ID,
                database_type="offline",
                sql_dialect="offline",
                connection_settings={"safe": "test"},
                metadata_path=root / "private-metadata.json",
                memory_path=root / "private-memory",
                read_only=True,
            ),
        )
    )
    coordinator = DataSourceRequestCoordinator(source_registry)

    def factory(config: DataSourceConfig) -> DataSourceRuntime:
        return DataSourceRuntime(
            config=config,
            runner=object(),
            memory=object(),
            metadata_retriever=object(),
            sql_guard=object(),
            agent=FakeAgent(),
        )

    manager = DataSourceRuntimeManager(source_registry, {"offline": factory})
    application_registry = AssistantApplicationRegistry(
        root / "assistant-apps.sqlite3",
        source_registry,
    )
    application_registry.initialize()
    return ApplicationResources(
        source_registry,
        coordinator,
        manager,
        application_registry,
    )


def make_app(
    resources: ApplicationResources,
    *,
    enabled: str = "true",
    token: str = ADMIN_TOKEN,
):
    return DataSourceVannaFastAPIServer(
        resources,
        admin_environ={
            "WATER_AGENT_ADMIN_ENABLED": enabled,
            "WATER_AGENT_ADMIN_TOKEN": token,
        },
    ).create_app()


def auth_headers(*, token: str = ADMIN_TOKEN, origin: str | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def assert_schema_version(db_path: Path, expected: int = 1) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        row = connection.execute(
            f"""
            SELECT version FROM {SCHEMA_VERSION_TABLE}
            WHERE component = ?
            """,
            (SCHEMA_COMPONENT,),
        ).fetchone()
    assert row == (expected,)


def create_legacy_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE assistant_applications (
            app_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            app_secret TEXT NOT NULL,
            token_ttl_seconds INTEGER NOT NULL,
            theme TEXT NOT NULL,
            logo_url TEXT NOT NULL,
            welcome TEXT NOT NULL,
            welcome_description TEXT NOT NULL,
            show_history INTEGER NOT NULL CHECK (show_history IN (0, 1)),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE assistant_application_origins (
            app_id TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (app_id, origin),
            FOREIGN KEY (app_id) REFERENCES assistant_applications(app_id)
                ON DELETE CASCADE
        );
        CREATE TABLE assistant_application_sources (
            app_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (app_id, source_id),
            FOREIGN KEY (app_id) REFERENCES assistant_applications(app_id)
                ON DELETE CASCADE
        );
        """
    )


def test_migrations(root: Path, resources: ApplicationResources) -> None:
    empty_path = root / "empty.sqlite3"
    empty_registry = AssistantApplicationRegistry(
        empty_path,
        resources.registry,
    )
    empty_registry.initialize()
    empty_registry.initialize()
    assert_schema_version(empty_path)

    legacy_path = root / "legacy.sqlite3"
    legacy_secret = "legacy-secret-that-must-remain-unchanged-123456"
    with closing(sqlite3.connect(legacy_path)) as connection:
        create_legacy_v1(connection)
        connection.execute(
            """
            INSERT INTO assistant_applications VALUES
                (?, ?, 1, ?, 300, '#1677ff', '', 'welcome',
                 'description', 0, 10, 20)
            """,
            ("legacy-app", "Legacy", legacy_secret),
        )
        connection.execute(
            "INSERT INTO assistant_application_origins VALUES (?, ?)",
            ("legacy-app", "http://127.0.0.1:5174"),
        )
        connection.execute(
            "INSERT INTO assistant_application_sources VALUES (?, ?)",
            ("legacy-app", SOURCE_ID),
        )
        connection.commit()
    legacy_registry = AssistantApplicationRegistry(
        legacy_path,
        resources.registry,
    )
    legacy_registry.initialize()
    assert_schema_version(legacy_path)
    loaded = legacy_registry.require_for_token_verification("legacy-app")
    assert loaded.app_secret == legacy_secret
    assert loaded.allowed_origins == ("http://127.0.0.1:5174",)
    assert loaded.allowed_source_ids == (SOURCE_ID,)

    future_path = root / "future.sqlite3"
    future_registry = AssistantApplicationRegistry(
        future_path,
        resources.registry,
    )
    future_registry.initialize()
    with closing(sqlite3.connect(future_path)) as connection:
        connection.execute(
            f"""
            UPDATE {SCHEMA_VERSION_TABLE} SET version = 2
            WHERE component = ?
            """,
            (SCHEMA_COMPONENT,),
        )
        connection.commit()
    try:
        future_registry.initialize()
    except SchemaMigrationError:
        pass
    else:
        raise AssertionError("高版本数据库未失败关闭")
    assert_schema_version(future_path, 2)

    incompatible_path = root / "incompatible.sqlite3"
    with closing(sqlite3.connect(incompatible_path)) as connection:
        connection.execute(
            "CREATE TABLE assistant_applications (app_id TEXT PRIMARY KEY)"
        )
        connection.commit()
    incompatible_registry = AssistantApplicationRegistry(
        incompatible_path,
        resources.registry,
    )
    try:
        incompatible_registry.initialize()
    except SchemaMigrationError:
        pass
    else:
        raise AssertionError("不兼容数据库未失败关闭")
    with closing(sqlite3.connect(incompatible_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"assistant_applications"}

    rollback_path = root / "rollback.sqlite3"
    rollback_registry = AssistantApplicationRegistry(
        rollback_path,
        resources.registry,
    )

    def fail_halfway(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE half_migration (id INTEGER)")
        raise sqlite3.OperationalError("controlled migration failure")

    rollback_registry._migrate_0_to_1 = fail_halfway
    try:
        rollback_registry.initialize()
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("受控迁移失败未抛出")
    with closing(sqlite3.connect(rollback_path)) as connection:
        tables = tuple(
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        )
    assert tables == ()


def test_admin_api(root: Path, resources: ApplicationResources) -> None:
    disabled_app = make_app(resources, enabled="false", token="")
    with TestClient(
        disabled_app,
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/admin/data-sources").status_code == 404

    for token in ("", "too-short"):
        try:
            make_app(resources, token=token)
        except AdminConfigurationError:
            pass
        else:
            raise AssertionError("无效管理员 Token 未阻止启动")

    app = make_app(resources)
    with TestClient(
        app,
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
    ) as client:
        path = "/api/admin/data-sources"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth_headers(token="wrong-token")).status_code == 401
        sources = client.get(path, headers=auth_headers())
        assert sources.status_code == 200
        assert sources.json() == [
            {"source_id": SOURCE_ID, "database_type": "offline"}
        ]
        assert "private" not in sources.text
        assert client.get(
            path,
            headers=auth_headers(origin=ORIGIN),
        ).status_code == 200
        for invalid_origin in (
            "http://evil.example",
            f"{ORIGIN}/",
            f"{ORIGIN}/path",
            "http://*.example",
            "null",
        ):
            assert client.get(
                path,
                headers=auth_headers(origin=invalid_origin),
            ).status_code == 403

        create_body = {
            "app_id": "admin-created",
            "name": "Admin created",
            "allowed_origins": ["http://127.0.0.1:5174"],
            "allowed_source_ids": [SOURCE_ID],
            "token_ttl_seconds": 300,
            "enabled": True,
        }
        created = client.post(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
            json=create_body,
        )
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        assert created.headers["pragma"] == "no-cache"
        first_secret = created.json()[APP_SECRET_FIELD]
        assert len(first_secret) >= 32
        assert created.text.count(first_secret) == 1

        listed = client.get(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
        )
        shown = client.get(
            "/api/admin/assistant-applications/admin-created",
            headers=auth_headers(),
        )
        assert listed.status_code == shown.status_code == 200
        assert first_secret not in listed.text
        assert first_secret not in shown.text
        assert APP_SECRET_FIELD not in shown.json()
        assert "secret_mask" in shown.json()
        assert client.get(
            "/api/admin/assistant-applications/missing-app",
            headers=auth_headers(),
        ).status_code == 404

        duplicate = client.post(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
            json=create_body,
        )
        assert duplicate.status_code == 409
        unknown_source = client.post(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
            json={
                **create_body,
                "app_id": "unknown-source",
                "allowed_source_ids": ["missing"],
            },
        )
        invalid_application_origin = client.post(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
            json={
                **create_body,
                "app_id": "invalid-origin",
                "allowed_origins": ["http://*.example"],
            },
        )
        assert unknown_source.status_code == 400
        assert invalid_application_origin.status_code == 400

        updated = client.patch(
            "/api/admin/assistant-applications/admin-created",
            headers=auth_headers(),
            json={"name": "Updated", "show_history": True},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated"
        assert updated.json()["show_history"] is True
        injected_value = "client-supplied-secret-must-not-be-echoed"
        for body in (
            {APP_SECRET_FIELD: injected_value},
            {"enabled": False},
            {"app_id": "replacement"},
            {"unknown": injected_value},
        ):
            rejected = client.patch(
                "/api/admin/assistant-applications/admin-created",
                headers=auth_headers(),
                json=body,
            )
            assert rejected.status_code == 422
            assert injected_value not in rejected.text

        embed_config = EmbedApplicationConfig(
            app_id="admin-created",
            app_secret=first_secret,
            enabled=True,
            allowed_origins=("http://127.0.0.1:5174",),
            allowed_source_ids=(SOURCE_ID,),
            token_ttl_seconds=300,
        )
        old_embed_token, _ = issue_embed_token(embed_config, subject="test")
        disabled = client.post(
            "/api/admin/assistant-applications/admin-created/disable",
            headers=auth_headers(),
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        try:
            verify_embed_token(
                old_embed_token,
                parent_origin="http://127.0.0.1:5174",
                registry=resources.assistant_application_registry,
            )
        except Exception:
            pass
        else:
            raise AssertionError("禁用后旧 Embed Token 仍有效")
        enabled = client.post(
            "/api/admin/assistant-applications/admin-created/enable",
            headers=auth_headers(),
        )
        assert enabled.status_code == 200
        rotated = client.post(
            "/api/admin/assistant-applications/admin-created/rotate-secret",
            headers=auth_headers(),
        )
        assert rotated.status_code == 200
        assert rotated.headers["cache-control"] == "no-store"
        second_secret = rotated.json()[APP_SECRET_FIELD]
        assert len(second_secret) >= 32 and second_secret != first_secret
        assert first_secret not in rotated.text
        try:
            verify_embed_token(
                old_embed_token,
                parent_origin="http://127.0.0.1:5174",
                registry=resources.assistant_application_registry,
            )
        except Exception:
            pass
        else:
            raise AssertionError("轮换后旧 Embed Token 仍有效")

        assert client.get(
            "/api/data-sources",
            headers={"X-Forwarded-For": "203.0.113.1"},
        ).status_code == 200

    for client_host in ("::1", "::ffff:127.0.0.1"):
        with TestClient(
            app,
            base_url=ORIGIN,
            client=(client_host, 50000),
        ) as client:
            assert client.get(
                "/api/admin/data-sources",
                headers=auth_headers(),
            ).status_code == 200

    with TestClient(
        app,
        base_url=ORIGIN,
        client=("203.0.113.10", 50000),
    ) as client:
        assert client.get(
            "/api/admin/data-sources",
            headers={
                **auth_headers(),
                "X-Forwarded-For": "127.0.0.1",
                "Forwarded": "for=127.0.0.1",
            },
        ).status_code == 403


def test_live_http(resources: ApplicationResources) -> None:
    app = make_app(resources)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started

    base_url = f"http://127.0.0.1:{port}"

    def request(
        method: str,
        path: str,
        *,
        token: str | None = ADMIN_TOKEN,
        origin: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any], Mapping[str, str]]:
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(
            f"{base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(http_request, timeout=5)
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            return exc.code, payload, {
                key.lower(): value for key, value in exc.headers.items()
            }
        with response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, {
                key.lower(): value for key, value in response.headers.items()
            }

    application_path = "/api/admin/assistant-applications"
    try:
        assert request("GET", "/api/admin/data-sources", token=None)[0] == 401
        assert request(
            "GET",
            "/api/admin/data-sources",
            token="incorrect-token",
        )[0] == 401
        assert request("GET", "/api/admin/data-sources")[0] == 200
        assert request(
            "GET",
            "/api/admin/data-sources",
            origin=base_url,
        )[0] == 200
        assert request(
            "GET",
            "/api/admin/data-sources",
            origin="http://evil.example",
        )[0] == 403
        assert urllib.request.urlopen(
            f"{base_url}/api/data-sources",
            timeout=5,
        ).status == 200

        created_status, created, created_headers = request(
            "POST",
            application_path,
            body={
                "app_id": "live-http-app",
                "name": "Live HTTP",
                "allowed_origins": ["http://127.0.0.1:5174"],
                "allowed_source_ids": [SOURCE_ID],
                "token_ttl_seconds": 300,
                "enabled": True,
            },
        )
        assert created_status == 201
        assert created_headers["cache-control"] == "no-store"
        first_secret = created[APP_SECRET_FIELD]
        assert isinstance(first_secret, str) and len(first_secret) >= 32
        assert request("GET", application_path)[0] == 200
        shown_status, shown, _ = request(
            "GET",
            f"{application_path}/live-http-app",
        )
        assert shown_status == 200 and APP_SECRET_FIELD not in shown
        assert request(
            "PATCH",
            f"{application_path}/live-http-app",
            body={"name": "Live HTTP updated", "show_history": True},
        )[0] == 200
        assert request(
            "POST",
            f"{application_path}/live-http-app/disable",
        )[1]["enabled"] is False
        assert request(
            "POST",
            f"{application_path}/live-http-app/enable",
        )[1]["enabled"] is True
        rotate_status, rotated, rotate_headers = request(
            "POST",
            f"{application_path}/live-http-app/rotate-secret",
        )
        assert rotate_status == 200
        assert rotate_headers["cache-control"] == "no-store"
        second_secret = rotated[APP_SECRET_FIELD]
        assert (
            isinstance(second_secret, str)
            and len(second_secret) >= 32
            and second_secret != first_secret
        )

        db_path = resources.assistant_application_registry.db_path
        with closing(sqlite3.connect(db_path)) as connection:
            version = connection.execute(
                f"""
                SELECT version FROM {SCHEMA_VERSION_TABLE}
                WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            ).fetchone()
            application = connection.execute(
                """
                SELECT app_secret FROM assistant_applications
                WHERE app_id = 'live-http-app'
                """
            ).fetchone()
            origin_count = connection.execute(
                """
                SELECT COUNT(*) FROM assistant_application_origins
                WHERE app_id = 'live-http-app'
                """
            ).fetchone()
            source_count = connection.execute(
                """
                SELECT COUNT(*) FROM assistant_application_sources
                WHERE app_id = 'live-http-app'
                """
            ).fetchone()
        assert version == (1,)
        assert application == (second_secret,)
        assert origin_count == (1,)
        assert source_count == (1,)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistant-admin-api-") as temp_name:
        root = Path(temp_name).resolve()
        resources = make_resources(root)
        test_migrations(root, resources)
        test_admin_api(root, resources)
        test_live_http(resources)
    print("assistant admin API and SQLite migration: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

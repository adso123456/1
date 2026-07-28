"""本机管理员 API 与 SQLite V1 迁移真实行为测试。"""

from __future__ import annotations

import json
import secrets
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
from unittest.mock import patch

import uvicorn
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_admin_api import (
    PUBLIC_ADMIN_TOKEN_PLACEHOLDER,
    AdminConfigurationError,
    load_admin_settings,
)
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


ADMIN_TOKEN = secrets.token_urlsafe(32)
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


def create_v1_tables(
    connection: sqlite3.Connection,
    *,
    include_checks: bool = True,
    foreign_key_mode: str = "cascade",
    include_children: bool = True,
    text_check_interference: bool = False,
) -> None:
    enabled_check = (
        " check ( ( ENABLED in ( 1 , 0 ) ) )"
        if include_checks
        else ""
    )
    history_check = (
        " Check ( show_history In ( 0 , 1 ) )"
        if include_checks
        else ""
    )
    if foreign_key_mode == "cascade":
        foreign_key = (
            ", FOREIGN KEY (app_id) "
            "REFERENCES assistant_applications(app_id) ON DELETE CASCADE"
        )
    elif foreign_key_mode == "no_action":
        foreign_key = (
            ", FOREIGN KEY (app_id) "
            "REFERENCES assistant_applications(app_id)"
        )
    elif foreign_key_mode == "none":
        foreign_key = ""
    elif foreign_key_mode == "omitted_parent_column":
        foreign_key = (
            ", FOREIGN KEY (app_id) "
            "REFERENCES assistant_applications ON DELETE CASCADE"
        )
    else:
        raise AssertionError(f"未知外键测试模式: {foreign_key_mode}")
    interference_default = (
        " DEFAULT 'noise '' check(enabledin(0,1)) "
        "check(show_historyin(0,1))'"
        if text_check_interference
        else ""
    )
    interference_comments = (
        "-- check(enabledin(0,1))\n"
        "            /* check(show_historyin(0,1)) */"
        if text_check_interference
        else ""
    )
    interference_constraint = (
        ', CONSTRAINT "check(enabledin(0,1)) '
        'check(show_historyin(0,1))" CHECK (1)'
        if text_check_interference
        else ""
    )
    connection.executescript(
        f"""
        CREATE TABLE assistant_applications (
            app_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL{enabled_check},
            app_secret TEXT NOT NULL,
            token_ttl_seconds INTEGER NOT NULL,
            theme TEXT NOT NULL,
            logo_url TEXT NOT NULL{interference_default},
            welcome TEXT NOT NULL,
            welcome_description TEXT NOT NULL,
            {interference_comments}
            show_history INTEGER NOT NULL{history_check},
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
            {interference_constraint}
        );
        """
    )
    if include_children:
        connection.executescript(
            f"""
        CREATE TABLE assistant_application_origins (
            app_id TEXT NOT NULL,
            origin TEXT NOT NULL,
            PRIMARY KEY (app_id, origin)
            {foreign_key}
        );
        CREATE TABLE assistant_application_sources (
            app_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (app_id, source_id)
            {foreign_key}
        );
        """
        )


def create_schema_version(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE {SCHEMA_VERSION_TABLE} (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO {SCHEMA_VERSION_TABLE}
            (component, version, updated_at)
        VALUES (?, ?, 1)
        """,
        (SCHEMA_COMPONENT, version),
    )


def insert_test_application(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO assistant_applications VALUES
            ('preserved-app', 'Preserved', 1, 'preserved-test-secret',
             300, '#1677ff', '', 'welcome', 'description', 0, 10, 20)
        """
    )


def database_snapshot(db_path: Path) -> tuple[Any, ...]:
    with closing(sqlite3.connect(db_path)) as connection:
        schema = tuple(
            connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )
        table_names = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )
        data = tuple(
            (
                table_name,
                tuple(
                    connection.execute(
                        f'SELECT * FROM "{table_name}" ORDER BY rowid'
                    )
                ),
            )
            for table_name in table_names
        )
    return schema, data


def application_data_snapshot(db_path: Path) -> tuple[Any, ...]:
    with closing(sqlite3.connect(db_path)) as connection:
        return tuple(
            (
                table_name,
                tuple(
                    connection.execute(
                        f'SELECT * FROM "{table_name}" ORDER BY rowid'
                    )
                ),
            )
            for table_name in (
                "assistant_applications",
                "assistant_application_origins",
                "assistant_application_sources",
            )
        )


def assert_schema_failure_unchanged(
    registry: AssistantApplicationRegistry,
) -> None:
    before = database_snapshot(registry.db_path)
    try:
        registry.initialize()
    except SchemaMigrationError as exc:
        assert "V1" in str(exc) or "版本" in str(exc)
    else:
        raise AssertionError("不兼容数据库未失败关闭")
    assert database_snapshot(registry.db_path) == before


def test_admin_settings(resources: ApplicationResources) -> None:
    disabled = load_admin_settings(
        {
            "WATER_AGENT_ADMIN_ENABLED": "false",
            "WATER_AGENT_ADMIN_TOKEN": "",
        }
    )
    assert disabled.enabled is False and disabled.token == ""
    assert "token=" not in repr(disabled)

    invalid_tokens = (
        "",
        "too-short",
        " " * 32,
        f" {ADMIN_TOKEN}",
        f"{ADMIN_TOKEN} ",
        PUBLIC_ADMIN_TOKEN_PLACEHOLDER,
        123,
    )
    for token in invalid_tokens:
        try:
            load_admin_settings(
                {
                    "WATER_AGENT_ADMIN_ENABLED": "true",
                    "WATER_AGENT_ADMIN_TOKEN": token,
                }
            )
        except AdminConfigurationError as exc:
            assert str(exc) == (
                "管理员 API 已启用，但 WATER_AGENT_ADMIN_TOKEN 无效"
            )
            if isinstance(token, str) and token:
                assert token not in str(exc)
        else:
            raise AssertionError("无效管理员 Token 未失败关闭")

    valid = load_admin_settings(
        {
            "WATER_AGENT_ADMIN_ENABLED": "true",
            "WATER_AGENT_ADMIN_TOKEN": ADMIN_TOKEN,
        }
    )
    assert valid.enabled is True and valid.token == ADMIN_TOKEN
    assert ADMIN_TOKEN not in repr(valid)

    app = make_app(resources)
    wrong_token = secrets.token_urlsafe(32)
    with patch("backend.assistant_admin_api.logger") as admin_logger:
        with TestClient(
            app,
            base_url=ORIGIN,
            client=("127.0.0.1", 50000),
        ) as client:
            response = client.get(
                "/api/admin/data-sources",
                headers=auth_headers(token=wrong_token),
            )
            valid_response = client.get(
                "/api/admin/data-sources",
                headers=auth_headers(),
            )
    assert response.status_code == 401
    assert wrong_token not in response.text
    assert valid_response.status_code == 200
    assert ADMIN_TOKEN not in valid_response.text
    assert wrong_token not in repr(admin_logger.mock_calls)
    assert ADMIN_TOKEN not in repr(admin_logger.mock_calls)


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
        create_v1_tables(connection)
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

    forged_check_path = root / "forged-check-in-text.sqlite3"
    with closing(sqlite3.connect(forged_check_path)) as connection:
        create_v1_tables(
            connection,
            include_checks=False,
            text_check_interference=True,
        )
        insert_test_application(connection)
        connection.commit()
    assert_schema_failure_unchanged(
        AssistantApplicationRegistry(
            forged_check_path,
            resources.registry,
        )
    )

    valid_check_with_interference_path = (
        root / "valid-check-with-interference.sqlite3"
    )
    with closing(
        sqlite3.connect(valid_check_with_interference_path)
    ) as connection:
        create_v1_tables(
            connection,
            text_check_interference=True,
        )
        insert_test_application(connection)
        connection.commit()
    business_data_before = application_data_snapshot(
        valid_check_with_interference_path
    )
    valid_check_registry = AssistantApplicationRegistry(
        valid_check_with_interference_path,
        resources.registry,
    )
    valid_check_registry.initialize()
    assert_schema_version(valid_check_with_interference_path)
    assert (
        application_data_snapshot(valid_check_with_interference_path)
        == business_data_before
    )

    omitted_parent_column_path = (
        root / "foreign-key-omitted-parent-column.sqlite3"
    )
    with closing(sqlite3.connect(omitted_parent_column_path)) as connection:
        create_v1_tables(
            connection,
            foreign_key_mode="omitted_parent_column",
        )
        insert_test_application(connection)
        pragma_rows = tuple(
            connection.execute(
                "PRAGMA foreign_key_list(assistant_application_origins)"
            )
        )
        assert pragma_rows and all(row[4] is None for row in pragma_rows)
        connection.commit()
    assert_schema_failure_unchanged(
        AssistantApplicationRegistry(
            omitted_parent_column_path,
            resources.registry,
        )
    )

    for name, options in (
        ("missing-foreign-keys", {"foreign_key_mode": "none"}),
        ("missing-cascade", {"foreign_key_mode": "no_action"}),
        ("missing-checks", {"include_checks": False}),
    ):
        db_path = root / f"{name}.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection:
            create_v1_tables(connection, **options)
            insert_test_application(connection)
            connection.commit()
        assert_schema_failure_unchanged(
            AssistantApplicationRegistry(db_path, resources.registry)
        )

    version_zero_partial_path = root / "version-zero-partial.sqlite3"
    with closing(sqlite3.connect(version_zero_partial_path)) as connection:
        create_v1_tables(connection, include_children=False)
        insert_test_application(connection)
        create_schema_version(connection, 0)
        connection.commit()
    assert_schema_failure_unchanged(
        AssistantApplicationRegistry(
            version_zero_partial_path,
            resources.registry,
        )
    )

    version_zero_empty_path = root / "version-zero-empty.sqlite3"
    with closing(sqlite3.connect(version_zero_empty_path)) as connection:
        create_schema_version(connection, 0)
        connection.execute(
            "CREATE TABLE unrelated_component_data (value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO unrelated_component_data VALUES ('preserved')"
        )
        connection.commit()
    version_zero_empty_registry = AssistantApplicationRegistry(
        version_zero_empty_path,
        resources.registry,
    )
    version_zero_empty_registry.initialize()
    version_zero_empty_registry.initialize()
    assert_schema_version(version_zero_empty_path)
    with closing(sqlite3.connect(version_zero_empty_path)) as connection:
        assert connection.execute(
            "SELECT value FROM unrelated_component_data"
        ).fetchone() == ("preserved",)

    version_one_partial_path = root / "version-one-partial.sqlite3"
    with closing(sqlite3.connect(version_one_partial_path)) as connection:
        create_v1_tables(connection, include_children=False)
        insert_test_application(connection)
        create_schema_version(connection, 1)
        connection.commit()
    assert_schema_failure_unchanged(
        AssistantApplicationRegistry(
            version_one_partial_path,
            resources.registry,
        )
    )

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
    assert_schema_failure_unchanged(
        AssistantApplicationRegistry(
            incompatible_path,
            resources.registry,
        )
    )

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
        test_admin_settings(resources)
        test_migrations(root, resources)
        test_admin_api(root, resources)
        test_live_http(resources)
    print("assistant admin API and SQLite migration: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

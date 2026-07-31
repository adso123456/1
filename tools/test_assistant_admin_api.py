"""本机管理员 API、SQLite V4 迁移和 Origin 校验离线测试。"""

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
from config.data_source_config import DataSourceConfig
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer


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


def make_app(resources: ApplicationResources):
    return DataSourceVannaFastAPIServer(resources).create_app()


def auth_headers(*, origin: str | None = None):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def assert_schema_version(db_path: Path, expected: int = 4) -> None:
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
    connection.executescript(
        f"""
        CREATE TABLE assistant_applications (
            app_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL{enabled_check},
            app_secret TEXT NOT NULL,
            token_ttl_seconds INTEGER NOT NULL,
            theme TEXT NOT NULL,
            logo_url TEXT NOT NULL,
            welcome TEXT NOT NULL,
            welcome_description TEXT NOT NULL,
            show_history INTEGER NOT NULL{history_check},
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
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


def test_migrations(root: Path, resources: ApplicationResources) -> None:
    # 全新 V4 初始化
    empty_path = root / "empty.sqlite3"
    empty_registry = AssistantApplicationRegistry(empty_path, resources.registry)
    empty_registry.initialize()
    empty_registry.initialize()
    assert_schema_version(empty_path, 4)

    # V1 → V4 迁移
    legacy_path = root / "legacy.sqlite3"
    with closing(sqlite3.connect(legacy_path)) as connection:
        create_v1_tables(connection)
        connection.execute(
            "INSERT INTO assistant_applications VALUES (?, ?, 1, ?, 300, '#1677ff', '', 'welcome', 'description', 0, 10, 20)",
            ("legacy-app", "Legacy", "some-secret-at-least-32-chars-long"),
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
    legacy_registry = AssistantApplicationRegistry(legacy_path, resources.registry)
    legacy_registry.initialize()
    assert_schema_version(legacy_path, 4)

    # 验证迁移后数据完整（secret列已删除）
    with closing(sqlite3.connect(legacy_path)) as connection:
        cols = tuple(row[1] for row in connection.execute("PRAGMA table_info(assistant_applications)"))
        assert "app_secret" not in cols
        assert "token_ttl_seconds" not in cols
        app = connection.execute("SELECT name, enabled FROM assistant_applications WHERE app_id = 'legacy-app'").fetchone()
        assert app == ("Legacy", 1)

    # 迁移失败不改库
    rollback_path = root / "rollback.sqlite3"
    rollback_registry = AssistantApplicationRegistry(rollback_path, resources.registry)

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

    # 未来版本拒绝
    future_path = root / "future.sqlite3"
    future_registry = AssistantApplicationRegistry(future_path, resources.registry)
    future_registry.initialize()
    with closing(sqlite3.connect(future_path)) as connection:
        connection.execute(
            f"UPDATE {SCHEMA_VERSION_TABLE} SET version = 99 WHERE component = ?",
            (SCHEMA_COMPONENT,),
        )
        connection.commit()
    try:
        future_registry.initialize()
    except SchemaMigrationError:
        pass
    else:
        raise AssertionError("高版本数据库未失败关闭")


def test_admin_api(root: Path, resources: ApplicationResources) -> None:
    app = make_app(resources)
    with TestClient(
        app,
        base_url=ORIGIN,
        client=("127.0.0.1", 50000),
    ) as client:
        # 数据源列表
        path = "/api/admin/data-sources"
        sources = client.get(path)
        assert sources.status_code == 200
        assert sources.json() == [
            {"source_id": SOURCE_ID, "database_type": "offline", "display_name": "offline"}
        ]
        assert "private" not in sources.text

        # 无效 Origin 拒绝
        for invalid_origin in ("http://evil.example", f"{ORIGIN}/", "null"):
            assert client.get(path, headers=auth_headers(origin=invalid_origin)).status_code == 403

        # 创建应用（无 Secret/Token）
        create_body = {
            "app_id": "admin-created",
            "name": "Admin created",
            "allowed_origins": ["http://127.0.0.1:5174"],
            "allowed_source_ids": [SOURCE_ID],
            "application_links": [
                {
                    "link_id": "admin-link",
                    "name": "管理平台",
                    "url": "http://127.0.0.1:5174/embed-demo",
                    "open_mode": "new_tab",
                    "enabled": True,
                    "sort_order": 0,
                }
            ],
            "enabled": True,
        }
        created = client.post(
            "/api/admin/assistant-applications",
            headers=auth_headers(),
            json=create_body,
        )
        assert created.status_code == 201
        # 不返回 secret
        assert "app_secret" not in created.json()
        assert "secret" not in created.json()
        assert created.json()["application_links"] == create_body["application_links"]

        # 默认外观
        for field_name, expected in (
            ("theme", "#1677ff"),
            ("header_font_color", "#1f2329"),
            ("welcome", "有什么可以帮助你的？"),
            ("float_icon_draggable", False),
            ("float_x_anchor", "right"),
            ("float_x_offset", 24),
        ):
            assert created.json()[field_name] == expected

        # 列表和详情
        listed = client.get("/api/admin/assistant-applications", headers=auth_headers())
        shown = client.get("/api/admin/assistant-applications/admin-created", headers=auth_headers())
        assert listed.status_code == shown.status_code == 200
        assert "secret" not in str(shown.json())

        # 404
        assert client.get("/api/admin/assistant-applications/missing-app", headers=auth_headers()).status_code == 404

        # 重复
        assert client.post("/api/admin/assistant-applications", headers=auth_headers(), json=create_body).status_code == 409

        # 无效数据源
        assert client.post("/api/admin/assistant-applications", headers=auth_headers(), json={
            **create_body, "app_id": "bad-source", "allowed_source_ids": ["missing"]
        }).status_code == 400

        # 更新
        updated = client.patch(
            "/api/admin/assistant-applications/admin-created",
            headers=auth_headers(),
            json={"name": "Updated", "show_history": True},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated"

        # 外观更新
        appearance = client.patch(
            "/api/admin/assistant-applications/admin-created",
            headers=auth_headers(),
            json={"theme": "#123abc", "welcome": "新欢迎语"},
        )
        assert appearance.status_code == 200
        assert appearance.json()["theme"] == "#123abc"
        assert "secret" not in appearance.json()

        # 注入防护
        for body in ({"enabled": False}, {"app_id": "replacement"}, {"unknown": "value"}):
            assert client.patch(
                "/api/admin/assistant-applications/admin-created",
                headers=auth_headers(), json=body,
            ).status_code == 422

        # 禁用/启用
        assert client.post(
            "/api/admin/assistant-applications/admin-created/disable", headers=auth_headers()
        ).json()["enabled"] is False
        assert client.post(
            "/api/admin/assistant-applications/admin-created/enable", headers=auth_headers()
        ).json()["enabled"] is True

        # 删除
        assert client.delete(
            "/api/admin/assistant-applications/admin-created", headers=auth_headers()
        ).status_code == 204

        # 普通 API 不受影响
        assert client.get("/api/data-sources", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200

    # 外网拒绝
    with TestClient(app, base_url=ORIGIN, client=("203.0.113.10", 50000)) as client:
        assert client.get("/api/admin/data-sources", headers=auth_headers()).status_code == 403


def test_live_http(resources: ApplicationResources) -> None:
    app = make_app(resources)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
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
        origin: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | list[Any], Mapping[str, str]]:
        headers = {}
        if origin is not None:
            headers["Origin"] = origin
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(http_request, timeout=5)
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            return exc.code, payload, {key.lower(): key for key in exc.headers}
        with response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, {key.lower(): key for key in response.headers}

    application_path = "/api/admin/assistant-applications"
    try:
        assert request("GET", "/api/admin/data-sources")[0] == 200
        assert urllib.request.urlopen(f"{base_url}/api/data-sources", timeout=5).status == 200

        # 创建（无 Secret）
        created_status, created, _ = request(
            "POST",
            application_path,
            body={
                "app_id": "live-http-app",
                "name": "Live HTTP",
                "allowed_origins": ["http://127.0.0.1:5174"],
                "allowed_source_ids": [SOURCE_ID],
                "enabled": True,
            },
        )
        assert created_status == 201
        assert "app_secret" not in created
        assert "secret" not in str(created)

        # 禁用
        assert request("POST", f"{application_path}/live-http-app/disable")[1]["enabled"] is False
        # 启用
        assert request("POST", f"{application_path}/live-http-app/enable")[1]["enabled"] is True

        # 验证数据库 V4
        db_path = resources.assistant_application_registry.db_path
        with closing(sqlite3.connect(db_path)) as connection:
            version = connection.execute(
                f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE component = ?",
                (SCHEMA_COMPONENT,),
            ).fetchone()
            cols = tuple(row[1] for row in connection.execute("PRAGMA table_info(assistant_applications)"))
        assert version == (4,)
        assert "app_secret" not in cols
        assert "token_ttl_seconds" not in cols
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
    print("assistant admin API and SQLite V4 migration: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

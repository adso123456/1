"""小助手应用注册表、多应用 Token 隔离与事务离线测试。"""

from __future__ import annotations

import secrets
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import jwt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import (
    SCHEMA_COMPONENT,
    SCHEMA_VERSION,
    SCHEMA_VERSION_TABLE,
    ApplicationAlreadyExists,
    ApplicationDisabled,
    AssistantApplicationRegistry,
    InvalidApplicationConfiguration,
    SchemaMigrationError,
)
from backend.data_source_registry import DataSourceRegistry
from backend.embed_access import (
    EMBED_AUDIENCE,
    EmbedAccessError,
    EmbedApplicationConfig,
    issue_embed_token,
    verify_embed_token,
)
from config.data_source_config import DataSourceConfig

LEGACY_TEST_SECRET = secrets.token_urlsafe(32)


def make_data_sources(root: Path) -> DataSourceRegistry:
    return DataSourceRegistry(
        DataSourceConfig(
            source_id=source_id,
            database_type="offline",
            sql_dialect="offline",
            connection_settings={"label": source_id},
            metadata_path=root / f"{source_id}.json",
            memory_path=root / source_id,
            read_only=True,
        )
        for source_id in ("source-a", "source-b")
    )


def expect_raises(
    exception_type: type[Exception],
    callback: Callable[[], object],
) -> None:
    try:
        callback()
    except exception_type:
        return
    raise AssertionError(f"未抛出 {exception_type.__name__}")


def create_v1_database(
    db_path: Path,
    *,
    with_version: bool,
) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
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
                show_history INTEGER NOT NULL
                    CHECK (show_history IN (0, 1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE assistant_application_origins (
                app_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                PRIMARY KEY (app_id, origin),
                FOREIGN KEY (app_id)
                    REFERENCES assistant_applications(app_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE assistant_application_sources (
                app_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                PRIMARY KEY (app_id, source_id),
                FOREIGN KEY (app_id)
                    REFERENCES assistant_applications(app_id)
                    ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO assistant_applications VALUES (
                'legacy-v1', '旧应用', 1, ?,
                300, '#1677ff', '', '旧欢迎语', '旧欢迎描述', 0, 10, 20
            )
            """,
            (LEGACY_TEST_SECRET,),
        )
        connection.execute(
            """
            INSERT INTO assistant_application_origins
                VALUES ('legacy-v1', 'https://legacy.example')
            """
        )
        connection.execute(
            """
            INSERT INTO assistant_application_sources
                VALUES ('legacy-v1', 'source-a')
            """
        )
        if with_version:
            connection.executescript(
                f"""
                CREATE TABLE {SCHEMA_VERSION_TABLE} (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                INSERT INTO {SCHEMA_VERSION_TABLE}
                    VALUES ('{SCHEMA_COMPONENT}', 1, 1);
                """
            )
        connection.commit()


def assert_v3_migration(
    db_path: Path,
    data_sources: DataSourceRegistry,
) -> None:
    registry = AssistantApplicationRegistry(db_path, data_sources)
    registry.initialize()
    registry.initialize()
    loaded = registry.require_for_token_verification("legacy-v1")
    assert loaded.app_secret == LEGACY_TEST_SECRET
    assert loaded.allowed_origins == ("https://legacy.example",)
    assert loaded.allowed_source_ids == ("source-a",)
    assert loaded.header_font_color == "#1f2329"
    assert loaded.float_icon_url == ""
    assert loaded.float_icon_draggable is False
    assert loaded.float_x_anchor == "right"
    assert loaded.float_x_offset == 24
    assert loaded.float_y_anchor == "bottom"
    assert loaded.float_y_offset == 24
    assert loaded.application_links == ()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute(
            f"""
            SELECT version FROM {SCHEMA_VERSION_TABLE}
            WHERE component = ?
            """,
            (SCHEMA_COMPONENT,),
        ).fetchone() == (SCHEMA_VERSION,)


def create_v2_database(
    db_path: Path,
    data_sources: DataSourceRegistry,
) -> None:
    create_v1_database(db_path, with_version=True)
    registry = AssistantApplicationRegistry(db_path, data_sources)
    with registry._connection() as connection:
        registry._migrate_1_to_2(connection)
        connection.execute(
            f"""
            UPDATE {SCHEMA_VERSION_TABLE}
            SET version = 2
            WHERE component = ?
            """,
            (SCHEMA_COMPONENT,),
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistant-registry-") as temp_name:
        root = Path(temp_name).resolve()
        data_sources = make_data_sources(root)
        registry = AssistantApplicationRegistry(
            root / "nested" / "assistant-apps.sqlite3",
            data_sources,
        )
        registry.initialize()
        assert registry.db_path.exists()
        with closing(sqlite3.connect(registry.db_path)) as connection:
            assert connection.execute(
                f"""
                SELECT version FROM {SCHEMA_VERSION_TABLE}
                WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            ).fetchone() == (SCHEMA_VERSION,)
            columns = tuple(
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(assistant_applications)"
                )
            )
            assert columns[-7:] == (
                "header_font_color",
                "float_icon_url",
                "float_icon_draggable",
                "float_x_anchor",
                "float_x_offset",
                "float_y_anchor",
                "float_y_offset",
            )

        for database_name, with_version in (
            ("versioned-v1.sqlite3", True),
            ("unversioned-v1.sqlite3", False),
        ):
            legacy_path = root / database_name
            create_v1_database(legacy_path, with_version=with_version)
            assert_v3_migration(legacy_path, data_sources)

        versioned_v2_path = root / "versioned-v2.sqlite3"
        create_v2_database(versioned_v2_path, data_sources)
        assert_v3_migration(versioned_v2_path, data_sources)

        version_zero_path = root / "version-zero.sqlite3"
        with closing(sqlite3.connect(version_zero_path)) as connection:
            connection.executescript(
                f"""
                CREATE TABLE {SCHEMA_VERSION_TABLE} (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                INSERT INTO {SCHEMA_VERSION_TABLE}
                    VALUES ('{SCHEMA_COMPONENT}', 0, 1);
                """
            )
            connection.commit()
        version_zero = AssistantApplicationRegistry(
            version_zero_path,
            data_sources,
        )
        version_zero.initialize()
        with closing(sqlite3.connect(version_zero_path)) as connection:
            assert connection.execute(
                f"""
                SELECT version FROM {SCHEMA_VERSION_TABLE}
                WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            ).fetchone() == (SCHEMA_VERSION,)

        partial_path = root / "partial.sqlite3"
        with closing(sqlite3.connect(partial_path)) as connection:
            connection.execute(
                "CREATE TABLE assistant_applications (app_id TEXT PRIMARY KEY)"
            )
            connection.commit()
        expect_raises(
            SchemaMigrationError,
            AssistantApplicationRegistry(
                partial_path,
                data_sources,
            ).initialize,
        )

        mixed_path = root / "mixed-v1-v2.sqlite3"
        create_v1_database(mixed_path, with_version=False)
        with closing(sqlite3.connect(mixed_path)) as connection:
            connection.execute(
                """
                ALTER TABLE assistant_applications
                ADD COLUMN header_font_color TEXT NOT NULL DEFAULT '#1f2329'
                """
            )
            connection.commit()
        expect_raises(
            SchemaMigrationError,
            AssistantApplicationRegistry(
                mixed_path,
                data_sources,
            ).initialize,
        )

        unversioned_v2_path = root / "unversioned-v2.sqlite3"
        unversioned_v2 = AssistantApplicationRegistry(
            unversioned_v2_path,
            data_sources,
        )
        unversioned_v2.initialize()
        with closing(sqlite3.connect(unversioned_v2_path)) as connection:
            connection.execute(
                f"DELETE FROM {SCHEMA_VERSION_TABLE} WHERE component = ?",
                (SCHEMA_COMPONENT,),
            )
            connection.commit()
        unversioned_v2.initialize()
        with closing(sqlite3.connect(unversioned_v2_path)) as connection:
            assert connection.execute(
                f"""
                SELECT version FROM {SCHEMA_VERSION_TABLE}
                WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            ).fetchone() == (SCHEMA_VERSION,)

        future_path = root / "future-v4.sqlite3"
        future_registry = AssistantApplicationRegistry(
            future_path,
            data_sources,
        )
        future_registry.initialize()
        with closing(sqlite3.connect(future_path)) as connection:
            connection.execute(
                f"""
                UPDATE {SCHEMA_VERSION_TABLE}
                SET version = 4 WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            )
            connection.commit()
        expect_raises(SchemaMigrationError, future_registry.initialize)

        rollback_path = root / "v1-v2-rollback.sqlite3"
        create_v1_database(rollback_path, with_version=True)
        rollback_registry = AssistantApplicationRegistry(
            rollback_path,
            data_sources,
        )

        def fail_v2_migration(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                ALTER TABLE assistant_applications
                ADD COLUMN rollback_probe TEXT
                """
            )
            raise sqlite3.OperationalError("controlled V2 migration failure")

        rollback_registry._migrate_1_to_2 = fail_v2_migration
        expect_raises(sqlite3.OperationalError, rollback_registry.initialize)
        with closing(sqlite3.connect(rollback_path)) as connection:
            assert "rollback_probe" not in {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(assistant_applications)"
                )
            }
            assert connection.execute(
                f"""
                SELECT version FROM {SCHEMA_VERSION_TABLE}
                WHERE component = ?
                """,
                (SCHEMA_COMPONENT,),
            ).fetchone() == (1,)

        created_a = registry.create(
            app_id="assistant-a",
            name="水利助手 A",
            allowed_origins=(
                "http://127.0.0.1:5174",
                "http://127.0.0.1:5174/",
            ),
            allowed_source_ids=("source-a", "source-a"),
            application_links=(
                {
                    "link_id": "platform-main",
                    "name": "水务管理平台",
                    "url": "HTTP://EXAMPLE.TEST:80/embed?source=water#assistant",
                    "open_mode": "new_tab",
                    "enabled": True,
                    "sort_order": 1,
                },
                {
                    "link_id": "platform-backup",
                    "name": "备用入口",
                    "url": "https://example.test/backup",
                    "open_mode": "same_tab",
                    "enabled": False,
                    "sort_order": 0,
                },
            ),
            token_ttl_seconds=300,
            theme="#123ABC",
            header_font_color="#F0E1D2",
            float_icon_url="https://example.test/float.svg",
            float_icon_draggable=True,
            float_x_anchor="left",
            float_x_offset=1000,
            float_y_anchor="top",
            float_y_offset=0,
            welcome="欢迎使用 A",
            welcome_description="仅查询 A 数据源",
        )
        created_b = registry.create(
            app_id="assistant-b",
            name="水利助手 B",
            allowed_origins=("http://localhost:5174",),
            allowed_source_ids=("source-b",),
            token_ttl_seconds=300,
        )
        secret_a = created_a.app_secret
        secret_b = created_b.app_secret
        assert len(secret_a) >= 32 and len(secret_b) >= 32
        assert secret_a != secret_b
        assert secret_a not in repr(created_a)
        assert created_a.application.allowed_origins == (
            "http://127.0.0.1:5174",
        )
        assert created_a.application.allowed_source_ids == ("source-a",)
        assert tuple(
            link.link_id for link in created_a.application.application_links
        ) == ("platform-backup", "platform-main")
        assert (
            created_a.application.application_links[1].url
            == "http://example.test/embed?source=water#assistant"
        )
        assert created_a.application.theme == "#123abc"
        assert created_a.application.header_font_color == "#f0e1d2"
        assert created_a.application.float_icon_draggable is True
        assert created_a.application.float_x_anchor == "left"
        assert created_a.application.float_x_offset == 1000
        assert created_a.application.float_y_anchor == "top"
        assert created_a.application.float_y_offset == 0
        assert created_b.application.header_font_color == "#1f2329"
        assert created_b.application.float_x_offset == 24

        expect_raises(
            ApplicationAlreadyExists,
            lambda: registry.create(app_id="assistant-a", name="duplicate"),
        )
        for app_id in ("ab", "bad app", "bad/app"):
            expect_raises(
                InvalidApplicationConfiguration,
                lambda app_id=app_id: registry.create(
                    app_id=app_id,
                    name="invalid",
                ),
            )
        for origin in (
            "http://127.0.0.1:5174/path",
            "http://*.example.com",
            "file:///tmp/logo",
        ):
            expect_raises(
                InvalidApplicationConfiguration,
                lambda origin=origin: registry.create(
                    app_id=f"invalid-origin-{len(origin)}",
                    name="invalid",
                    allowed_origins=(origin,),
                ),
            )
        for invalid_url in (
            "javascript:alert(1)",
            "data:text/html,hello",
            "file:///tmp/demo",
            "https://user:pass@example.test/path",
            "https://example.test/<script>",
            "https://example.test/%3Cscript%3E",
            "https://example.test/path%0Aheader",
            "/embed-demo",
        ):
            expect_raises(
                InvalidApplicationConfiguration,
                lambda invalid_url=invalid_url: registry.create(
                    app_id=f"invalid-link-{len(invalid_url)}",
                    name="invalid",
                    application_links=(
                        {
                            "link_id": "invalid-link",
                            "name": "非法入口",
                            "url": invalid_url,
                            "open_mode": "new_tab",
                            "enabled": True,
                            "sort_order": 0,
                        },
                    ),
                ),
            )
        expect_raises(
            InvalidApplicationConfiguration,
            lambda: registry.create(
                app_id="unknown-source",
                name="invalid",
                allowed_source_ids=("missing",),
            ),
        )
        expect_raises(
            InvalidApplicationConfiguration,
            lambda: registry.create(
                app_id="html-name",
                name="<script>alert(1)</script>",
            ),
        )
        for field_name, invalid in (
            ("theme", ""),
            ("header_font_color", "#12345g"),
            ("logo_url", "javascript:alert(1)"),
            ("float_icon_url", "https://user:pass@example.test/icon.png"),
            ("float_icon_draggable", 1),
            ("float_x_anchor", "center"),
            ("float_y_anchor", "middle"),
            ("float_x_offset", 1001),
            ("float_y_offset", True),
        ):
            expect_raises(
                InvalidApplicationConfiguration,
                lambda field_name=field_name, invalid=invalid: registry.create(
                    app_id=f"invalid-{field_name.replace('_', '-')}",
                    name="invalid appearance",
                    **{field_name: invalid},
                ),
            )

        updated = registry.update(
            "assistant-a",
            name="更新后的助手 A",
            allowed_origins=("https://example.test",),
            allowed_source_ids=(),
            theme="#abcdef",
            header_font_color="#102030",
            logo_url="https://example.test/logo.png",
            welcome="新的欢迎语",
            welcome_description="新的说明",
            float_icon_url="https://example.test/updated-icon.svg",
            float_icon_draggable=False,
            float_x_anchor="right",
            float_x_offset=8,
            float_y_anchor="bottom",
            float_y_offset=9,
            show_history=True,
            application_links=(
                {
                    "link_id": "platform-main",
                    "name": "水务管理平台",
                    "url": "https://example.test/main",
                    "open_mode": "same_tab",
                    "enabled": True,
                    "sort_order": 0,
                },
                {
                    "link_id": "platform-backup",
                    "name": "备用入口",
                    "url": "https://example.test/backup",
                    "open_mode": "new_tab",
                    "enabled": False,
                    "sort_order": 5,
                },
            ),
        )
        assert updated.name == "更新后的助手 A"
        assert updated.allowed_source_ids == ()
        assert updated.show_history is True
        assert updated.header_font_color == "#102030"
        assert updated.float_icon_url.endswith("updated-icon.svg")
        assert updated.float_icon_draggable is False
        assert updated.float_x_anchor == "right"
        assert updated.float_x_offset == 8
        assert updated.float_y_anchor == "bottom"
        assert updated.float_y_offset == 9
        assert tuple(
            (link.link_id, link.enabled, link.sort_order)
            for link in updated.application_links
        ) == (
            ("platform-main", True, 0),
            ("platform-backup", False, 5),
        )
        registry.update(
            "assistant-a",
            allowed_origins=("http://127.0.0.1:5174",),
            allowed_source_ids=("source-a",),
        )

        safe_list = registry.list()
        safe_dump = repr([asdict(item) for item in safe_list])
        assert len(safe_list) == 2
        assert secret_a not in safe_dump and secret_b not in safe_dump
        assert "secret_mask" in safe_dump

        config_a = EmbedApplicationConfig(
            app_id="assistant-a",
            app_secret=secret_a,
            enabled=True,
            allowed_origins=("http://127.0.0.1:5174",),
            allowed_source_ids=("source-a",),
            token_ttl_seconds=300,
        )
        assert secret_a not in repr(config_a)
        token_a, _ = issue_embed_token(config_a, subject="user-a")
        principal_a = verify_embed_token(
            token_a,
            parent_origin="http://127.0.0.1:5174",
            registry=registry,
            source_id="source-a",
        )
        assert principal_a.app_id == "assistant-a"

        now = int(time.time())
        forged_with_b_secret = jwt.encode(
            {
                "aud": EMBED_AUDIENCE,
                "app_id": "assistant-a",
                "sub": "user-a",
                "parent_origin": "http://127.0.0.1:5174",
                "allowed_source_ids": ["source-a"],
                "iat": now,
                "exp": now + 300,
                "jti": "forged-with-b-secret",
            },
            secret_b,
            algorithm="HS256",
        )
        expanded_source_token = jwt.encode(
            {
                "aud": EMBED_AUDIENCE,
                "app_id": "assistant-a",
                "sub": "user-a",
                "parent_origin": "http://127.0.0.1:5174",
                "allowed_source_ids": ["source-a", "source-b"],
                "iat": now,
                "exp": now + 300,
                "jti": "expanded-source",
            },
            secret_a,
            algorithm="HS256",
        )
        unknown_app_token = jwt.encode(
            {
                "aud": EMBED_AUDIENCE,
                "app_id": "assistant-unknown",
                "sub": "user",
                "parent_origin": "http://127.0.0.1:5174",
                "allowed_source_ids": [],
                "iat": now,
                "exp": now + 300,
                "jti": "unknown-app",
            },
            secret_a,
            algorithm="HS256",
        )
        for callback, status in (
            (
                lambda: verify_embed_token(
                    forged_with_b_secret,
                    parent_origin="http://127.0.0.1:5174",
                    registry=registry,
                ),
                401,
            ),
            (
                lambda: verify_embed_token(
                    token_a,
                    parent_origin="http://localhost:5174",
                    registry=registry,
                ),
                403,
            ),
            (
                lambda: verify_embed_token(
                    token_a,
                    parent_origin="http://127.0.0.1:5174",
                    registry=registry,
                    source_id="source-b",
                ),
                403,
            ),
            (
                lambda: verify_embed_token(
                    expanded_source_token,
                    parent_origin="http://127.0.0.1:5174",
                    registry=registry,
                ),
                403,
            ),
            (
                lambda: verify_embed_token(
                    unknown_app_token,
                    parent_origin="http://127.0.0.1:5174",
                    registry=registry,
                ),
                401,
            ),
        ):
            try:
                callback()
            except EmbedAccessError as exc:
                assert exc.status_code == status
            else:
                raise AssertionError("跨应用 Token 隔离未生效")

        registry.disable("assistant-a")
        expect_raises(
            ApplicationDisabled,
            lambda: registry.require_for_token_verification("assistant-a"),
        )
        try:
            verify_embed_token(
                token_a,
                parent_origin="http://127.0.0.1:5174",
                registry=registry,
            )
        except EmbedAccessError as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("禁用应用后旧 Token 仍可用")

        registry.enable("assistant-a")
        rotated = registry.rotate_secret("assistant-a")
        assert rotated.app_secret != secret_a
        assert secret_a not in repr(rotated)
        try:
            verify_embed_token(
                token_a,
                parent_origin="http://127.0.0.1:5174",
                registry=registry,
            )
        except EmbedAccessError as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("密钥轮换后旧 Token 仍可用")
        new_config = EmbedApplicationConfig(
            **{
                **asdict(config_a),
                "app_secret": rotated.app_secret,
            }
        )
        new_token, _ = issue_embed_token(new_config, subject="user-a")
        assert verify_embed_token(
            new_token,
            parent_origin="http://127.0.0.1:5174",
            registry=registry,
        ).app_id == "assistant-a"

        with closing(sqlite3.connect(registry.db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_rollback_origin
                    BEFORE INSERT ON assistant_application_origins
                    WHEN NEW.origin = 'https://rollback.test'
                    BEGIN
                        SELECT RAISE(ABORT, 'controlled rollback');
                    END
                    """
                )
        expect_raises(
            sqlite3.IntegrityError,
            lambda: registry.create(
                app_id="rollback-app",
                name="Rollback",
                allowed_origins=("https://rollback.test",),
            ),
        )
        with closing(sqlite3.connect(registry.db_path)) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*) FROM assistant_applications
                WHERE app_id = 'rollback-app'
                """
            ).fetchone()[0] == 0

        with closing(sqlite3.connect(registry.db_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_rollback_link
                    BEFORE INSERT ON assistant_application_links
                    WHEN NEW.url = 'https://rollback.test/link'
                    BEGIN
                        SELECT RAISE(ABORT, 'controlled link rollback');
                    END
                    """
                )
        expect_raises(
            sqlite3.IntegrityError,
            lambda: registry.update(
                "assistant-a",
                name="不应保存的名称",
                application_links=(
                    {
                        "link_id": "rollback-link",
                        "name": "回滚入口",
                        "url": "https://rollback.test/link",
                        "open_mode": "new_tab",
                        "enabled": True,
                        "sort_order": 0,
                    },
                ),
            ),
        )
        rolled_back = registry.get("assistant-a")
        assert rolled_back.name == "更新后的助手 A"
        assert tuple(
            link.link_id for link in rolled_back.application_links
        ) == ("platform-main", "platform-backup")

        registry.delete("assistant-a")
        with closing(sqlite3.connect(registry.db_path)) as connection:
            assert connection.execute(
                """
                SELECT COUNT(*) FROM assistant_application_links
                WHERE app_id = 'assistant-a'
                """
            ).fetchone()[0] == 0

    print("assistant application registry: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

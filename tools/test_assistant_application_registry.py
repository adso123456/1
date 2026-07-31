"""小助手应用注册表 V4、Origin 校验与链接管理离线测试。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.assistant_application_registry import (
    SCHEMA_COMPONENT,
    SCHEMA_VERSION,
    SCHEMA_VERSION_TABLE,
    ApplicationAlreadyExists,
    ApplicationDisabled,
    ApplicationNotFound,
    AssistantApplicationLink,
    AssistantApplicationRegistry,
    InvalidApplicationConfiguration,
    SchemaMigrationError,
)
from backend.data_source_registry import DataSourceRegistry
from backend.embed_access import (
    EmbedAccessError,
    authorize_embed_origin,
)
from config.data_source_config import DataSourceConfig


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


# ── 旧数据库工厂（用于迁移测试） ──

def create_legacy_database(
    db_path: Path,
    *,
    version: int,
    with_version: bool,
) -> None:
    """创建 V1/V2/V3 旧版应用数据库，用于迁移测试。"""
    if version not in {1, 2, 3}:
        raise ValueError("version 必须为 1、2 或 3")
    appearance_columns = ""
    if version >= 2:
        appearance_columns = """
            ,
            header_font_color TEXT NOT NULL DEFAULT '#1f2329',
            float_icon_url TEXT NOT NULL DEFAULT '',
            float_icon_draggable INTEGER NOT NULL DEFAULT 0
                CHECK (float_icon_draggable IN (0, 1)),
            float_x_anchor TEXT NOT NULL DEFAULT 'right'
                CHECK (float_x_anchor IN ('left', 'right')),
            float_x_offset INTEGER NOT NULL DEFAULT 24
                CHECK (float_x_offset BETWEEN 0 AND 1000),
            float_y_anchor TEXT NOT NULL DEFAULT 'bottom'
                CHECK (float_y_anchor IN ('top', 'bottom')),
            float_y_offset INTEGER NOT NULL DEFAULT 24
                CHECK (float_y_offset BETWEEN 0 AND 1000)
        """
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(
            f"""
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
                {appearance_columns}
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
        if version >= 2:
            connection.execute(
                """
                INSERT INTO assistant_applications (
                    app_id, name, enabled, app_secret, token_ttl_seconds,
                    theme, logo_url, welcome, welcome_description,
                    show_history, created_at, updated_at,
                    header_font_color, float_icon_url, float_icon_draggable,
                    float_x_anchor, float_x_offset,
                    float_y_anchor, float_y_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-app", "旧版应用", 1,
                    "legacy-secret-32characters-minimum!", 300,
                    "#abcdef", "https://img.example/logo.png",
                    "旧欢迎语", "旧欢迎描述", 1, 100, 200,
                    "#112233", "https://img.example/float.png", 1,
                    "left", 8, "top", 16,
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO assistant_applications VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    "legacy-app", "旧版应用", 1,
                    "legacy-secret-32characters-minimum!", 300,
                    "#abcdef", "https://img.example/logo.png",
                    "旧欢迎语", "旧欢迎描述", 1, 100, 200,
                ),
            )
        connection.execute(
            "INSERT INTO assistant_application_origins VALUES "
            "('legacy-app', 'https://legacy.example')"
        )
        connection.execute(
            "INSERT INTO assistant_application_sources VALUES "
            "('legacy-app', 'source-a')"
        )
        if version >= 3:
            connection.executescript(
                """
                CREATE TABLE assistant_application_links (
                    link_id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    open_mode TEXT NOT NULL
                        CHECK (open_mode IN ('new_tab', 'same_tab')),
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    sort_order INTEGER NOT NULL
                        CHECK (sort_order BETWEEN 0 AND 10000),
                    FOREIGN KEY (app_id)
                        REFERENCES assistant_applications(app_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX assistant_application_links_app_order_idx
                ON assistant_application_links(app_id, sort_order, link_id);
                """
            )
            connection.executemany(
                """
                INSERT INTO assistant_application_links (
                    link_id, app_id, name, url,
                    open_mode, enabled, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("link-a", "legacy-app", "站点A", "https://a.example/demo",
                     "new_tab", 1, 2),
                    ("link-b", "legacy-app", "站点B", "https://b.example/intra",
                     "same_tab", 0, 1),
                ],
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
                    VALUES ('{SCHEMA_COMPONENT}', {version}, 1);
                """
            )
        connection.commit()


def verify_migration(
    root: Path,
    data_sources: DataSourceRegistry,
    *,
    version: int,
    with_version: bool,
) -> None:
    """校验 V1/V2/V3 → V4 迁移后应用、Origin、数据源、链接完整保留。"""
    suffix = "ver" if with_version else "nover"
    db_path = root / f"migrate-v{version}-{suffix}.sqlite3"
    create_legacy_database(
        db_path,
        version=version,
        with_version=with_version,
    )

    # 迁移前记录（用于对比迁移后内容是否原样保留）
    with closing(sqlite3.connect(db_path)) as connection:
        expected_app = connection.execute(
            "SELECT name, enabled, theme FROM assistant_applications "
            "WHERE app_id = 'legacy-app'"
        ).fetchone()
        expected_origins = tuple(
            row[0]
            for row in connection.execute(
                "SELECT origin FROM assistant_application_origins "
                "WHERE app_id = 'legacy-app' ORDER BY origin"
            )
        )
        expected_sources = tuple(
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM assistant_application_sources "
                "WHERE app_id = 'legacy-app' ORDER BY source_id"
            )
        )
        expected_links = None
        if version >= 3:
            expected_links = tuple(
                row
                for row in connection.execute(
                    "SELECT link_id, name, url, open_mode, enabled, sort_order "
                    "FROM assistant_application_links "
                    "WHERE app_id = 'legacy-app' ORDER BY sort_order, link_id"
                )
            )

    registry = AssistantApplicationRegistry(db_path, data_sources)
    registry.initialize()

    with closing(sqlite3.connect(db_path)) as connection:
        # 版本已升级到 V4
        assert connection.execute(
            f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE component = ?",
            (SCHEMA_COMPONENT,),
        ).fetchone() == (SCHEMA_VERSION,)
        # V4 主表不再含 app_secret / token_ttl_seconds
        columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(assistant_applications)"
            )
        )
        assert "app_secret" not in columns
        assert "token_ttl_seconds" not in columns
        # 应用、Origin、数据源记录原样保留
        assert connection.execute(
            "SELECT name, enabled, theme FROM assistant_applications "
            "WHERE app_id = 'legacy-app'"
        ).fetchone() == expected_app
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT origin FROM assistant_application_origins "
                "WHERE app_id = 'legacy-app' ORDER BY origin"
            )
        ) == expected_origins
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM assistant_application_sources "
                "WHERE app_id = 'legacy-app' ORDER BY source_id"
            )
        ) == expected_sources
        # application_links 与迁移前完全一致
        links = tuple(
            row
            for row in connection.execute(
                "SELECT link_id, name, url, open_mode, enabled, sort_order "
                "FROM assistant_application_links "
                "WHERE app_id = 'legacy-app' ORDER BY sort_order, link_id"
            )
        )
        if version >= 3:
            assert links == expected_links
        else:
            assert links == ()
        # 外键完整性校验为空
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        # 删除应用仍级联删除三个子表记录
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "DELETE FROM assistant_applications WHERE app_id = 'legacy-app'"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_application_origins"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_application_sources"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_application_links"
        ).fetchone()[0] == 0
        connection.rollback()

    # authorize_embed_origin 能继续授权原 Origin，越权仍拒绝
    principal = authorize_embed_origin(
        app_id="legacy-app",
        origin="https://legacy.example",
        registry=registry,
    )
    assert principal.app_id == "legacy-app"
    assert principal.parent_origin == "https://legacy.example"
    # 原数据源授权有效
    authorize_embed_origin(
        app_id="legacy-app",
        origin="https://legacy.example",
        registry=registry,
        source_id="source-a",
    )
    # 未授权 Origin 仍被拒绝
    expect_raises(
        EmbedAccessError,
        lambda: authorize_embed_origin(
            app_id="legacy-app",
            origin="https://evil.example",
            registry=registry,
        ),
    )
    # 未授权数据源仍被拒绝
    expect_raises(
        EmbedAccessError,
        lambda: authorize_embed_origin(
            app_id="legacy-app",
            origin="https://legacy.example",
            registry=registry,
            source_id="source-b",
        ),
    )
    # V2/V3：外观字段完整保留
    if version >= 2:
        migrated = registry.get("legacy-app")
        assert migrated.header_font_color == "#112233"
        assert migrated.float_icon_url == "https://img.example/float.png"
        assert migrated.float_icon_draggable is True
        assert migrated.float_x_anchor == "left"
        assert migrated.float_x_offset == 8
        assert migrated.float_y_anchor == "top"
        assert migrated.float_y_offset == 16
    # V3：关联网站排序、启停、打开方式完整保留
    if version >= 3:
        migrated = registry.get("legacy-app")
        assert [link.link_id for link in migrated.application_links] == [
            "link-b",
            "link-a",
        ]
        assert migrated.application_links[0].enabled is False
        assert migrated.application_links[0].open_mode == "same_tab"
        assert migrated.application_links[1].enabled is True
        assert migrated.application_links[1].open_mode == "new_tab"
    print(
        f"  migration V{version}"
        f" ({'with version' if with_version else 'no version'})"
        " -> V4: all checks passed"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistant-v4-registry-") as temp_name:
        root = Path(temp_name).resolve()
        data_sources = make_data_sources(root)

        # ── V4 全新初始化 ──
        registry = AssistantApplicationRegistry(
            root / "nested" / "assistant-apps.sqlite3",
            data_sources,
        )
        registry.initialize()
        assert registry.db_path.exists()
        with closing(sqlite3.connect(registry.db_path)) as connection:
            assert connection.execute(
                f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE component = ?",
                (SCHEMA_COMPONENT,),
            ).fetchone() == (SCHEMA_VERSION,)
            columns = tuple(
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(assistant_applications)"
                )
            )
            # V4: no app_secret, no token_ttl_seconds
            assert "app_secret" not in columns
            assert "token_ttl_seconds" not in columns
            assert columns[-7:] == (
                "header_font_color",
                "float_icon_url",
                "float_icon_draggable",
                "float_x_anchor",
                "float_x_offset",
                "float_y_anchor",
                "float_y_offset",
            )

        # ── V1 / V2 / V3 → V4 迁移（含/不含版本记录）──
        for legacy_version in (1, 2, 3):
            verify_migration(
                root,
                data_sources,
                version=legacy_version,
                with_version=True,
            )
            verify_migration(
                root,
                data_sources,
                version=legacy_version,
                with_version=False,
            )

        # ── 未来版本拒绝 ──
        future_path = root / "future.sqlite3"
        future_registry = AssistantApplicationRegistry(future_path, data_sources)
        future_registry.initialize()
        with closing(sqlite3.connect(future_path)) as connection:
            connection.execute(
                f"UPDATE {SCHEMA_VERSION_TABLE} SET version = 99 WHERE component = ?",
                (SCHEMA_COMPONENT,),
            )
            connection.commit()
        expect_raises(SchemaMigrationError, future_registry.initialize)

        # ── CRUD 操作 ──
        # 创建（无 secret）
        view_a = registry.create(
            app_id="assistant-a",
            name="水利助手 A",
            allowed_origins=("http://127.0.0.1:5174",),
            allowed_source_ids=("source-a",),
            theme="#123ABC",
            header_font_color="#F0E1D2",
            welcome="欢迎使用 A",
            welcome_description="仅查询 A 数据源",
        )
        assert view_a.app_id == "assistant-a"
        assert view_a.enabled is True
        assert not hasattr(view_a, "secret_mask")
        assert not hasattr(view_a, "token_ttl_seconds")
        assert view_a.application_links == ()

        # 创建带链接
        view_b = registry.create(
            app_id="assistant-b",
            name="助手 B",
            allowed_origins=("http://localhost:5174",),
            allowed_source_ids=("source-b",),
            application_links=[
                {"link_id": "demo-site", "name": "演示站",
                 "url": "https://example.com/demo",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 0},
            ],
        )
        assert len(view_b.application_links) == 1
        assert view_b.application_links[0].link_id == "demo-site"

        # 重名拒绝
        expect_raises(
            ApplicationAlreadyExists,
            lambda: registry.create(app_id="assistant-a", name="duplicate"),
        )

        # 无效 app_id
        expect_raises(
            InvalidApplicationConfiguration,
            lambda: registry.create(app_id="ab", name="too short"),
        )

        # 更新（保留原 Origin 用于后续 Origin 校验）
        updated = registry.update(
            "assistant-a",
            name="更新后的助手 A",
            theme="#abcdef",
            show_history=True,
        )
        assert updated.name == "更新后的助手 A"
        assert updated.show_history is True

        # 列出
        items = registry.list()
        assert len(items) == 2
        assert all(not hasattr(item, "secret_mask") for item in items)

        # 删除
        registry.delete("assistant-b")
        assert len(registry.list()) == 1
        expect_raises(
            ApplicationNotFound,
            lambda: registry.get("assistant-b"),
        )

        # ── Origin 校验 ──
        principal = authorize_embed_origin(
            app_id="assistant-a",
            origin="http://127.0.0.1:5174",
            registry=registry,
        )
        assert principal.app_id == "assistant-a"
        assert principal.parent_origin == "http://127.0.0.1:5174"

        # Origin 不在白名单
        expect_raises(
            EmbedAccessError,
            lambda: authorize_embed_origin(
                app_id="assistant-a",
                origin="http://evil.example",
                registry=registry,
            ),
        )

        # 缺失 Origin
        expect_raises(
            EmbedAccessError,
            lambda: authorize_embed_origin(
                app_id="assistant-a",
                origin=None,
                registry=registry,
            ),
        )

        # 未知 app_id
        expect_raises(
            EmbedAccessError,
            lambda: authorize_embed_origin(
                app_id="unknown-app",
                origin="http://127.0.0.1:5174",
                registry=registry,
            ),
        )

        # 带数据源校验
        authorize_embed_origin(
            app_id="assistant-a",
            origin="http://127.0.0.1:5174",
            registry=registry,
            source_id="source-a",
        )

        # 数据源越权
        expect_raises(
            EmbedAccessError,
            lambda: authorize_embed_origin(
                app_id="assistant-a",
                origin="http://127.0.0.1:5174",
                registry=registry,
                source_id="source-b",
            ),
        )

        # 禁用后访问
        registry.disable("assistant-a")
        expect_raises(
            EmbedAccessError,
            lambda: authorize_embed_origin(
                app_id="assistant-a",
                origin="http://127.0.0.1:5174",
                registry=registry,
            ),
        )
        registry.enable("assistant-a")

        # ── 关联网站 ──
        registry.update(
            "assistant-a",
            application_links=[
                {"link_id": "main-site", "name": "主站",
                 "url": "https://example.test",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 1},
                {"link_id": "admin-panel", "name": "管理后台",
                 "url": "https://example.test/admin",
                 "open_mode": "same_tab", "enabled": False, "sort_order": 2},
                {"link_id": "docs-site", "name": "文档",
                 "url": "https://docs.example.test",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 0},
            ],
        )
        app = registry.get("assistant-a")
        assert len(app.application_links) == 3

        # 排序验证 (docs=0, main=1, admin=2)
        assert app.application_links[0].link_id == "docs-site"
        assert app.application_links[1].link_id == "main-site"
        assert app.application_links[2].link_id == "admin-panel"

        # 链接校验
        expect_raises(
            InvalidApplicationConfiguration,
            lambda: registry.update("assistant-a", application_links=[
                {"link_id": "bad-url", "name": "Bad",
                 "url": "javascript:alert(1)",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 0},
            ]),
        )
        # 重复 link_id
        expect_raises(
            InvalidApplicationConfiguration,
            lambda: registry.update("assistant-a", application_links=[
                {"link_id": "same-id", "name": "A",
                 "url": "https://a.example",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 0},
                {"link_id": "same-id", "name": "B",
                 "url": "https://b.example",
                 "open_mode": "new_tab", "enabled": True, "sort_order": 1},
            ]),
        )

        # ── 外观默认值 ──
        new_view = registry.create(
            app_id="default-appearance",
            name="Default",
        )
        assert new_view.header_font_color == "#1f2329"
        assert new_view.float_icon_url == ""
        assert new_view.float_icon_draggable is False
        assert new_view.float_x_anchor == "right"
        assert new_view.float_x_offset == 24

    print("assistant application registry V4: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

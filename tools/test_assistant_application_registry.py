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
                'legacy-v1', '旧应用', 1, 'legacy-secret-32characters-minimum!',
                300, '#1677ff', '', '旧欢迎语', '旧欢迎描述', 0, 10, 20
            )
            """
        )
        connection.execute(
            "INSERT INTO assistant_application_origins VALUES ('legacy-v1', 'https://legacy.example')"
        )
        connection.execute(
            "INSERT INTO assistant_application_sources VALUES ('legacy-v1', 'source-a')"
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

        # ── V1 → V4 迁移 ──
        v1_path = root / "v1.sqlite3"
        create_v1_database(v1_path, with_version=True)
        v1_registry = AssistantApplicationRegistry(v1_path, data_sources)
        v1_registry.initialize()
        with closing(sqlite3.connect(v1_path)) as connection:
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
            assert "app_secret" not in columns
            assert "token_ttl_seconds" not in columns
            row = connection.execute(
                "SELECT name, enabled FROM assistant_applications WHERE app_id = 'legacy-v1'"
            ).fetchone()
            assert row == ("旧应用", 1)

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

"""小助手应用注册表、多应用 Token 隔离与事务离线测试。"""

from __future__ import annotations

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
    ApplicationAlreadyExists,
    ApplicationDisabled,
    AssistantApplicationRegistry,
    InvalidApplicationConfiguration,
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assistant-registry-") as temp_name:
        root = Path(temp_name).resolve()
        registry = AssistantApplicationRegistry(
            root / "nested" / "assistant-apps.sqlite3",
            make_data_sources(root),
        )
        registry.initialize()
        assert registry.db_path.exists()

        created_a = registry.create(
            app_id="assistant-a",
            name="水利助手 A",
            allowed_origins=(
                "http://127.0.0.1:5174",
                "http://127.0.0.1:5174/",
            ),
            allowed_source_ids=("source-a", "source-a"),
            token_ttl_seconds=300,
            theme="#123ABC",
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
        assert created_a.application.theme == "#123abc"

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

        updated = registry.update(
            "assistant-a",
            name="更新后的助手 A",
            allowed_origins=("https://example.test",),
            allowed_source_ids=(),
            theme="#abcdef",
            logo_url="https://example.test/logo.png",
            welcome="新的欢迎语",
            welcome_description="新的说明",
            show_history=True,
        )
        assert updated.name == "更新后的助手 A"
        assert updated.allowed_source_ids == ()
        assert updated.show_history is True
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

    print("assistant application registry: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

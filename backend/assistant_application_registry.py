"""独立 SQLite 小助手应用注册表。"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.data_source_registry import DataSourceRegistry


APP_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,64}\Z")
THEME_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\Z")
DEFAULT_THEME = "#1677ff"
DEFAULT_WELCOME = "有什么可以帮助你的？"
DEFAULT_WELCOME_DESCRIPTION = (
    "用中文自然语言提问，Agent 自动查询数据库并返回图表"
)
MIN_TOKEN_TTL_SECONDS = 30
MAX_TOKEN_TTL_SECONDS = 3600


class AssistantApplicationError(Exception):
    """小助手应用注册表的安全业务异常。"""


class ApplicationNotFound(AssistantApplicationError):
    pass


class ApplicationAlreadyExists(AssistantApplicationError):
    pass


class InvalidApplicationConfiguration(AssistantApplicationError):
    pass


class ApplicationDisabled(AssistantApplicationError):
    pass


@dataclass(frozen=True)
class AssistantApplication:
    """仅供鉴权内部使用的完整应用记录。"""

    app_id: str
    name: str
    enabled: bool
    app_secret: str = field(repr=False)
    allowed_origins: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    token_ttl_seconds: int
    theme: str
    logo_url: str
    welcome: str
    welcome_description: str
    show_history: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class AssistantApplicationView:
    """可用于管理输出的脱敏应用 DTO。"""

    app_id: str
    name: str
    enabled: bool
    secret_mask: str
    allowed_origins: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    token_ttl_seconds: int
    theme: str
    logo_url: str
    welcome: str
    welcome_description: str
    show_history: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class CreatedAssistantApplication:
    application: AssistantApplicationView
    app_secret: str = field(repr=False)


def resolve_system_db_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    values = environ if environ is not None else os.environ
    configured = values.get("WATER_AGENT_SYSTEM_DB_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
    else:
        path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "system"
            / "assistant_apps.sqlite3"
        )
    return path.resolve()


def validate_app_id(app_id: Any) -> str:
    if not isinstance(app_id, str) or APP_ID_PATTERN.fullmatch(app_id) is None:
        raise InvalidApplicationConfiguration(
            "app_id 必须为 3～64 位字母、数字、下划线或短横线"
        )
    return app_id


def normalize_origin(origin: Any) -> str:
    if not isinstance(origin, str) or not origin.strip():
        raise InvalidApplicationConfiguration("Origin 必须是非空字符串")
    value = origin.strip()
    if "*" in value:
        raise InvalidApplicationConfiguration("Origin 不允许通配符")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InvalidApplicationConfiguration("Origin 格式无效") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidApplicationConfiguration(
            "Origin 必须是精确的 http/https Origin，不能包含路径或查询参数"
        )
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    return f"{scheme}://{host}{'' if port is None or default_port else f':{port}'}"


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "********"
    return f"{secret[:4]}********{secret[-4:]}"


def _validate_text(
    field_name: str,
    value: Any,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InvalidApplicationConfiguration(f"{field_name} 必须是字符串")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise InvalidApplicationConfiguration(f"{field_name} 不能为空")
    if len(normalized) > maximum:
        raise InvalidApplicationConfiguration(
            f"{field_name} 长度不能超过 {maximum}"
        )
    if "<" in normalized or ">" in normalized:
        raise InvalidApplicationConfiguration(f"{field_name} 不允许包含 HTML")
    return normalized


def _validate_theme(value: Any) -> str:
    if value is None or value == "":
        return DEFAULT_THEME
    if not isinstance(value, str) or THEME_PATTERN.fullmatch(value) is None:
        raise InvalidApplicationConfiguration(
            "theme 必须是 #RRGGBB 颜色格式"
        )
    return value.lower()


def _validate_logo_url(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 2048:
        raise InvalidApplicationConfiguration("logo_url 格式无效")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise InvalidApplicationConfiguration("logo_url 格式无效") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidApplicationConfiguration(
            "logo_url 只允许不含凭据的 http/https 地址"
        )
    return value


def _validate_ttl(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < MIN_TOKEN_TTL_SECONDS
        or value > MAX_TOKEN_TTL_SECONDS
    ):
        raise InvalidApplicationConfiguration(
            "token_ttl_seconds 必须在 30～3600 之间"
        )
    return value


class AssistantApplicationRegistry:
    """应用级 Origin、数据源和展示配置的事务型注册表。"""

    def __init__(
        self,
        db_path: Path | str,
        data_source_registry: DataSourceRegistry,
    ) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        if not isinstance(data_source_registry, DataSourceRegistry):
            raise TypeError("data_source_registry 必须是 DataSourceRegistry")
        self._data_source_registry = data_source_registry

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assistant_applications (
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
                CREATE TABLE IF NOT EXISTS assistant_application_origins (
                    app_id TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    PRIMARY KEY (app_id, origin),
                    FOREIGN KEY (app_id)
                        REFERENCES assistant_applications(app_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS assistant_application_sources (
                    app_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    PRIMARY KEY (app_id, source_id),
                    FOREIGN KEY (app_id)
                        REFERENCES assistant_applications(app_id)
                        ON DELETE CASCADE
                );
                """
            )

    def _normalize_origins(
        self,
        origins: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if origins is None:
            return ()
        if isinstance(origins, (str, bytes)):
            raise InvalidApplicationConfiguration(
                "allowed_origins 必须是字符串序列"
            )
        return tuple(sorted({normalize_origin(item) for item in origins}))

    def _normalize_source_ids(
        self,
        source_ids: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if source_ids is None:
            return ()
        if isinstance(source_ids, (str, bytes)):
            raise InvalidApplicationConfiguration(
                "allowed_source_ids 必须是字符串序列"
            )
        normalized: set[str] = set()
        for source_id in source_ids:
            if not isinstance(source_id, str) or not source_id.strip():
                raise InvalidApplicationConfiguration(
                    "source_id 必须是非空字符串"
                )
            if source_id not in self._data_source_registry.source_ids:
                raise InvalidApplicationConfiguration(
                    f"未知 source_id: {source_id}"
                )
            normalized.add(source_id)
        return tuple(sorted(normalized))

    def create(
        self,
        *,
        app_id: str,
        name: str,
        allowed_origins: Sequence[str] = (),
        allowed_source_ids: Sequence[str] = (),
        token_ttl_seconds: int = 300,
        theme: str = DEFAULT_THEME,
        logo_url: str = "",
        welcome: str = DEFAULT_WELCOME,
        welcome_description: str = DEFAULT_WELCOME_DESCRIPTION,
        show_history: bool = False,
        enabled: bool = True,
        app_secret: str | None = None,
    ) -> CreatedAssistantApplication:
        app_id = validate_app_id(app_id)
        name = _validate_text("name", name, maximum=120)
        origins = self._normalize_origins(allowed_origins)
        source_ids = self._normalize_source_ids(allowed_source_ids)
        ttl = _validate_ttl(token_ttl_seconds)
        theme = _validate_theme(theme)
        logo_url = _validate_logo_url(logo_url)
        welcome = _validate_text("welcome", welcome, maximum=120)
        welcome_description = _validate_text(
            "welcome_description",
            welcome_description,
            maximum=500,
        )
        if not isinstance(show_history, bool) or not isinstance(enabled, bool):
            raise InvalidApplicationConfiguration(
                "enabled 和 show_history 必须是布尔值"
            )
        secret = app_secret if app_secret is not None else secrets.token_urlsafe(32)
        if not isinstance(secret, str) or len(secret) < 32:
            raise InvalidApplicationConfiguration(
                "app_secret 至少需要 32 个字符"
            )
        now = int(time.time())
        self.initialize()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO assistant_applications (
                        app_id, name, enabled, app_secret,
                        token_ttl_seconds, theme, logo_url,
                        welcome, welcome_description, show_history,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        app_id,
                        name,
                        int(enabled),
                        secret,
                        ttl,
                        theme,
                        logo_url,
                        welcome,
                        welcome_description,
                        int(show_history),
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO assistant_application_origins (app_id, origin)
                    VALUES (?, ?)
                    """,
                    ((app_id, origin) for origin in origins),
                )
                connection.executemany(
                    """
                    INSERT INTO assistant_application_sources
                        (app_id, source_id)
                    VALUES (?, ?)
                    """,
                    ((app_id, source_id) for source_id in source_ids),
                )
        except sqlite3.IntegrityError as exc:
            if self._exists(app_id):
                raise ApplicationAlreadyExists(
                    f"应用已存在: {app_id}"
                ) from None
            raise
        return CreatedAssistantApplication(
            application=self.get(app_id),
            app_secret=secret,
        )

    def _exists(self, app_id: str) -> bool:
        if not self._db_path.exists():
            return False
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM assistant_applications WHERE app_id = ?",
                (app_id,),
            ).fetchone() is not None

    def _load_full(self, app_id: str) -> AssistantApplication:
        validate_app_id(app_id)
        if not self._db_path.exists():
            raise ApplicationNotFound(f"应用不存在: {app_id}")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM assistant_applications WHERE app_id = ?
                """,
                (app_id,),
            ).fetchone()
            if row is None:
                raise ApplicationNotFound(f"应用不存在: {app_id}")
            origins = tuple(
                item["origin"]
                for item in connection.execute(
                    """
                    SELECT origin FROM assistant_application_origins
                    WHERE app_id = ? ORDER BY origin
                    """,
                    (app_id,),
                )
            )
            source_ids = tuple(
                item["source_id"]
                for item in connection.execute(
                    """
                    SELECT source_id FROM assistant_application_sources
                    WHERE app_id = ? ORDER BY source_id
                    """,
                    (app_id,),
                )
            )
        return AssistantApplication(
            app_id=row["app_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            app_secret=row["app_secret"],
            allowed_origins=origins,
            allowed_source_ids=source_ids,
            token_ttl_seconds=row["token_ttl_seconds"],
            theme=row["theme"],
            logo_url=row["logo_url"],
            welcome=row["welcome"],
            welcome_description=row["welcome_description"],
            show_history=bool(row["show_history"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_view(application: AssistantApplication) -> AssistantApplicationView:
        return AssistantApplicationView(
            app_id=application.app_id,
            name=application.name,
            enabled=application.enabled,
            secret_mask=mask_secret(application.app_secret),
            allowed_origins=application.allowed_origins,
            allowed_source_ids=application.allowed_source_ids,
            token_ttl_seconds=application.token_ttl_seconds,
            theme=application.theme,
            logo_url=application.logo_url,
            welcome=application.welcome,
            welcome_description=application.welcome_description,
            show_history=application.show_history,
            created_at=application.created_at,
            updated_at=application.updated_at,
        )

    def get(self, app_id: str) -> AssistantApplicationView:
        return self._to_view(self._load_full(app_id))

    def require_for_token_verification(
        self,
        app_id: str,
    ) -> AssistantApplication:
        application = self._load_full(app_id)
        if not application.enabled:
            raise ApplicationDisabled("嵌入应用已禁用")
        return application

    def list(self) -> tuple[AssistantApplicationView, ...]:
        self.initialize()
        with self._connection() as connection:
            app_ids = tuple(
                row["app_id"]
                for row in connection.execute(
                    "SELECT app_id FROM assistant_applications ORDER BY app_id"
                )
            )
        return tuple(self.get(app_id) for app_id in app_ids)

    def update(
        self,
        app_id: str,
        *,
        name: str | None = None,
        allowed_origins: Sequence[str] | None = None,
        allowed_source_ids: Sequence[str] | None = None,
        token_ttl_seconds: int | None = None,
        theme: str | None = None,
        logo_url: str | None = None,
        welcome: str | None = None,
        welcome_description: str | None = None,
        show_history: bool | None = None,
    ) -> AssistantApplicationView:
        current = self._load_full(app_id)
        next_name = (
            current.name
            if name is None
            else _validate_text("name", name, maximum=120)
        )
        next_origins = (
            current.allowed_origins
            if allowed_origins is None
            else self._normalize_origins(allowed_origins)
        )
        next_sources = (
            current.allowed_source_ids
            if allowed_source_ids is None
            else self._normalize_source_ids(allowed_source_ids)
        )
        next_ttl = (
            current.token_ttl_seconds
            if token_ttl_seconds is None
            else _validate_ttl(token_ttl_seconds)
        )
        next_theme = current.theme if theme is None else _validate_theme(theme)
        next_logo = (
            current.logo_url
            if logo_url is None
            else _validate_logo_url(logo_url)
        )
        next_welcome = (
            current.welcome
            if welcome is None
            else _validate_text("welcome", welcome, maximum=120)
        )
        next_description = (
            current.welcome_description
            if welcome_description is None
            else _validate_text(
                "welcome_description",
                welcome_description,
                maximum=500,
            )
        )
        if show_history is not None and not isinstance(show_history, bool):
            raise InvalidApplicationConfiguration(
                "show_history 必须是布尔值"
            )
        next_show_history = (
            current.show_history
            if show_history is None
            else show_history
        )
        now = int(time.time())
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE assistant_applications
                SET name = ?, token_ttl_seconds = ?, theme = ?,
                    logo_url = ?, welcome = ?, welcome_description = ?,
                    show_history = ?, updated_at = ?
                WHERE app_id = ?
                """,
                (
                    next_name,
                    next_ttl,
                    next_theme,
                    next_logo,
                    next_welcome,
                    next_description,
                    int(next_show_history),
                    now,
                    app_id,
                ),
            )
            if allowed_origins is not None:
                connection.execute(
                    "DELETE FROM assistant_application_origins WHERE app_id = ?",
                    (app_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO assistant_application_origins (app_id, origin)
                    VALUES (?, ?)
                    """,
                    ((app_id, origin) for origin in next_origins),
                )
            if allowed_source_ids is not None:
                connection.execute(
                    "DELETE FROM assistant_application_sources WHERE app_id = ?",
                    (app_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO assistant_application_sources
                        (app_id, source_id)
                    VALUES (?, ?)
                    """,
                    ((app_id, source_id) for source_id in next_sources),
                )
        return self.get(app_id)

    def _set_enabled(
        self,
        app_id: str,
        enabled: bool,
    ) -> AssistantApplicationView:
        self._load_full(app_id)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE assistant_applications
                SET enabled = ?, updated_at = ?
                WHERE app_id = ?
                """,
                (int(enabled), int(time.time()), app_id),
            )
        return self.get(app_id)

    def enable(self, app_id: str) -> AssistantApplicationView:
        return self._set_enabled(app_id, True)

    def disable(self, app_id: str) -> AssistantApplicationView:
        return self._set_enabled(app_id, False)

    def rotate_secret(self, app_id: str) -> CreatedAssistantApplication:
        self._load_full(app_id)
        new_secret = secrets.token_urlsafe(32)
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE assistant_applications
                SET app_secret = ?, updated_at = ?
                WHERE app_id = ?
                """,
                (new_secret, int(time.time()), app_id),
            )
        return CreatedAssistantApplication(
            application=self.get(app_id),
            app_secret=new_secret,
        )

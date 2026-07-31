"""独立 SQLite 小助手应用注册表。"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from backend.data_source_registry import DataSourceRegistry


APP_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,64}\Z")
LINK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,64}\Z")
THEME_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\Z")
DEFAULT_THEME = "#1677ff"
DEFAULT_HEADER_FONT_COLOR = "#1f2329"
DEFAULT_WELCOME = "有什么可以帮助你的？"
DEFAULT_WELCOME_DESCRIPTION = (
    "用中文自然语言提问，Agent 自动查询数据库并返回图表"
)
DEFAULT_FLOAT_ICON_URL = ""
DEFAULT_FLOAT_ICON_DRAGGABLE = False
DEFAULT_FLOAT_X_ANCHOR = "right"
DEFAULT_FLOAT_X_OFFSET = 24
DEFAULT_FLOAT_Y_ANCHOR = "bottom"
DEFAULT_FLOAT_Y_OFFSET = 24
MAX_FLOAT_OFFSET = 1000
MAX_APPLICATION_LINKS = 20
MAX_LINK_SORT_ORDER = 10_000
APPLICATION_LINK_OPEN_MODES = frozenset({"new_tab", "same_tab"})
SCHEMA_COMPONENT = "assistant_application_registry"
SCHEMA_VERSION = 4
SCHEMA_VERSION_TABLE = "system_schema_versions"

# ---------------------------------------------------------------------------
# 历史 Schema 定义（仅用于迁移兼容性校验）
# ---------------------------------------------------------------------------

V1_APPLICATION_TABLE_SCHEMAS = {
    "assistant_applications": (
        ("app_id", "TEXT", 0, 1),
        ("name", "TEXT", 1, 0),
        ("enabled", "INTEGER", 1, 0),
        ("app_secret", "TEXT", 1, 0),
        ("token_ttl_seconds", "INTEGER", 1, 0),
        ("theme", "TEXT", 1, 0),
        ("logo_url", "TEXT", 1, 0),
        ("welcome", "TEXT", 1, 0),
        ("welcome_description", "TEXT", 1, 0),
        ("show_history", "INTEGER", 1, 0),
        ("created_at", "INTEGER", 1, 0),
        ("updated_at", "INTEGER", 1, 0),
    ),
    "assistant_application_origins": (
        ("app_id", "TEXT", 1, 1),
        ("origin", "TEXT", 1, 2),
    ),
    "assistant_application_sources": (
        ("app_id", "TEXT", 1, 1),
        ("source_id", "TEXT", 1, 2),
    ),
}

V2_APPLICATION_TABLE_SCHEMAS = {
    **V1_APPLICATION_TABLE_SCHEMAS,
    "assistant_applications": (
        *V1_APPLICATION_TABLE_SCHEMAS["assistant_applications"],
        ("header_font_color", "TEXT", 1, 0),
        ("float_icon_url", "TEXT", 1, 0),
        ("float_icon_draggable", "INTEGER", 1, 0),
        ("float_x_anchor", "TEXT", 1, 0),
        ("float_x_offset", "INTEGER", 1, 0),
        ("float_y_anchor", "TEXT", 1, 0),
        ("float_y_offset", "INTEGER", 1, 0),
    ),
}

V3_APPLICATION_TABLE_SCHEMAS = {
    **V2_APPLICATION_TABLE_SCHEMAS,
    "assistant_application_links": (
        ("link_id", "TEXT", 0, 1),
        ("app_id", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0),
        ("url", "TEXT", 1, 0),
        ("open_mode", "TEXT", 1, 0),
        ("enabled", "INTEGER", 1, 0),
        ("sort_order", "INTEGER", 1, 0),
    ),
}

# V4 去掉 app_secret 和 token_ttl_seconds 两列
APPLICATION_TABLE_SCHEMAS = {
    "assistant_applications": (
        ("app_id", "TEXT", 0, 1),
        ("name", "TEXT", 1, 0),
        ("enabled", "INTEGER", 1, 0),
        ("theme", "TEXT", 1, 0),
        ("logo_url", "TEXT", 1, 0),
        ("welcome", "TEXT", 1, 0),
        ("welcome_description", "TEXT", 1, 0),
        ("show_history", "INTEGER", 1, 0),
        ("created_at", "INTEGER", 1, 0),
        ("updated_at", "INTEGER", 1, 0),
        ("header_font_color", "TEXT", 1, 0),
        ("float_icon_url", "TEXT", 1, 0),
        ("float_icon_draggable", "INTEGER", 1, 0),
        ("float_x_anchor", "TEXT", 1, 0),
        ("float_x_offset", "INTEGER", 1, 0),
        ("float_y_anchor", "TEXT", 1, 0),
        ("float_y_offset", "INTEGER", 1, 0),
    ),
    "assistant_application_origins": (
        ("app_id", "TEXT", 1, 1),
        ("origin", "TEXT", 1, 2),
    ),
    "assistant_application_sources": (
        ("app_id", "TEXT", 1, 1),
        ("source_id", "TEXT", 1, 2),
    ),
    "assistant_application_links": (
        ("link_id", "TEXT", 0, 1),
        ("app_id", "TEXT", 1, 0),
        ("name", "TEXT", 1, 0),
        ("url", "TEXT", 1, 0),
        ("open_mode", "TEXT", 1, 0),
        ("enabled", "INTEGER", 1, 0),
        ("sort_order", "INTEGER", 1, 0),
    ),
}

V4_COLUMN_DEFAULTS = {
    "header_font_color": ("text", "#1f2329"),
    "float_icon_url": ("text", ""),
    "float_icon_draggable": ("integer", 0),
    "float_x_anchor": ("text", "right"),
    "float_x_offset": ("integer", 24),
    "float_y_anchor": ("text", "bottom"),
    "float_y_offset": ("integer", 24),
}


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


class SchemaMigrationError(AssistantApplicationError):
    """SQLite schema 无法安全迁移时使用的脱敏异常。"""


@dataclass(frozen=True)
class AssistantApplicationLink:
    link_id: str
    name: str
    url: str
    open_mode: str
    enabled: bool
    sort_order: int


@dataclass(frozen=True)
class AssistantApplication:
    """完整的应用记录。仅供 Origin 验证等内部逻辑使用。"""

    app_id: str
    name: str
    enabled: bool
    allowed_origins: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    application_links: tuple[AssistantApplicationLink, ...]
    theme: str
    header_font_color: str
    logo_url: str
    welcome: str
    welcome_description: str
    float_icon_url: str
    float_icon_draggable: bool
    float_x_anchor: str
    float_x_offset: int
    float_y_anchor: str
    float_y_offset: int
    show_history: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class AssistantApplicationView:
    """可用于管理输出的脱敏应用 DTO。"""

    app_id: str
    name: str
    enabled: bool
    allowed_origins: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    application_links: tuple[AssistantApplicationLink, ...]
    theme: str
    header_font_color: str
    logo_url: str
    welcome: str
    welcome_description: str
    float_icon_url: str
    float_icon_draggable: bool
    float_x_anchor: str
    float_x_offset: int
    float_y_anchor: str
    float_y_offset: int
    show_history: bool
    created_at: int
    updated_at: int


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


def normalize_application_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidApplicationConfiguration("关联网站 URL 必须是非空字符串")
    normalized = value.strip()
    decoded = unquote(normalized)
    if (
        len(normalized) > 2048
        or any(
            character.isspace() or ord(character) < 32
            for character in decoded
        )
        or "<" in decoded
        or ">" in decoded
        or "\\" in decoded
        or "\\" in normalized
    ):
        raise InvalidApplicationConfiguration("关联网站 URL 格式无效")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise InvalidApplicationConfiguration("关联网站 URL 格式无效") from exc
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidApplicationConfiguration(
            "关联网站 URL 必须是完整的 http/https 地址且不能包含凭据"
        )
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    authority = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit(
        (scheme, authority, parsed.path, parsed.query, parsed.fragment)
    )


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


def _validate_color(field_name: str, value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or THEME_PATTERN.fullmatch(value) is None:
        raise InvalidApplicationConfiguration(
            f"{field_name} 必须是 #RRGGBB 颜色格式"
        )
    return value.lower()


def _validate_theme(value: Any) -> str:
    return _validate_color("theme", value, DEFAULT_THEME)


def _validate_asset_url(field_name: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or value != value.strip()
        or "<" in value
        or ">" in value
    ):
        raise InvalidApplicationConfiguration(f"{field_name} 格式无效")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise InvalidApplicationConfiguration(f"{field_name} 格式无效") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidApplicationConfiguration(
            f"{field_name} 只允许不含凭据的 http/https 地址"
        )
    return value


def _validate_logo_url(value: Any) -> str:
    return _validate_asset_url("logo_url", value)


def _validate_anchor(field_name: str, value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise InvalidApplicationConfiguration(
            f"{field_name} 取值无效"
        )
    return value


def _validate_offset(field_name: str, value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAX_FLOAT_OFFSET
    ):
        raise InvalidApplicationConfiguration(
            f"{field_name} 必须是 0 到 {MAX_FLOAT_OFFSET} 的整数"
        )
    return value


class AssistantApplicationRegistry:
    """应用级 Origin、数据源、关联网站和展示配置的事务型注册表。"""

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
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._initialize_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # Schema 内省辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }

    @staticmethod
    def _table_signature(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> tuple[tuple[str, str, int, int], ...]:
        return tuple(
            (
                row["name"],
                row["type"].upper(),
                row["notnull"],
                row["pk"],
            )
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            )
        )

    @staticmethod
    def _table_defaults(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> dict[str, str | None]:
        return {
            row["name"]: row["dflt_value"]
            for row in connection.execute(
                f'PRAGMA table_info("{table_name}")'
            )
        }

    @staticmethod
    def _normalized_schema_default(
        value: str | None,
    ) -> tuple[str, str | int] | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        while (
            len(normalized) >= 2
            and normalized[0] == "("
            and normalized[-1] == ")"
        ):
            depth = 0
            wraps_entire_value = True
            for index, character in enumerate(normalized):
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0 and index != len(normalized) - 1:
                        wraps_entire_value = False
                        break
                if depth < 0:
                    wraps_entire_value = False
                    break
            if not wraps_entire_value or depth != 0:
                break
            normalized = normalized[1:-1].strip()
        if re.fullmatch(r"[+-]?\d+", normalized):
            return ("integer", int(normalized))
        if (
            len(normalized) >= 2
            and normalized[0] == normalized[-1]
            and normalized[0] in {"'", '"'}
        ):
            quote = normalized[0]
            body = normalized[1:-1]
            escaped_quote = quote * 2
            cursor = 0
            output: list[str] = []
            while cursor < len(body):
                if body.startswith(escaped_quote, cursor):
                    output.append(quote)
                    cursor += 2
                elif body[cursor] == quote:
                    return None
                else:
                    output.append(body[cursor])
                    cursor += 1
            return ("text", "".join(output))
        return None

    @staticmethod
    def _table_sql(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> str:
        row = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        if row is None or not isinstance(row["sql"], str):
            raise SchemaMigrationError(
                "小助手应用数据库结构与当前版本不兼容"
            )
        return row["sql"]

    @staticmethod
    def _skip_sql_non_code(sql: str, index: int) -> int | None:
        length = len(sql)
        character = sql[index]
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            cursor = index + 1
            while cursor < length:
                if sql[cursor] != closing:
                    cursor += 1
                    continue
                if cursor + 1 < length and sql[cursor + 1] == closing:
                    cursor += 2
                    continue
                return cursor + 1
            return length
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            return length if newline < 0 else newline + 1
        if sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            return length if closing < 0 else closing + 2
        return None

    @classmethod
    def _skip_sql_space_and_comments(cls, sql: str, index: int) -> int:
        length = len(sql)
        while index < length:
            if sql[index].isspace():
                index += 1
                continue
            if sql.startswith(("--", "/*"), index):
                skipped = cls._skip_sql_non_code(sql, index)
                index = length if skipped is None else skipped
                continue
            break
        return index

    @classmethod
    def _extract_parenthesized_sql(
        cls,
        sql: str,
        opening_index: int,
    ) -> tuple[str, int] | None:
        depth = 0
        cursor = opening_index
        content_start = opening_index + 1
        while cursor < len(sql):
            skipped = cls._skip_sql_non_code(sql, cursor)
            if skipped is not None:
                cursor = skipped
                continue
            if sql[cursor] == "(":
                depth += 1
            elif sql[cursor] == ")":
                depth -= 1
                if depth == 0:
                    return sql[content_start:cursor], cursor + 1
            cursor += 1
        return None

    @classmethod
    def _extract_check_expressions(cls, table_sql: str) -> tuple[str, ...]:
        expressions: list[str] = []
        cursor = 0
        while cursor < len(table_sql):
            skipped = cls._skip_sql_non_code(table_sql, cursor)
            if skipped is not None:
                cursor = skipped
                continue
            if table_sql[cursor].isalpha() or table_sql[cursor] == "_":
                start = cursor
                cursor += 1
                while cursor < len(table_sql) and (
                    table_sql[cursor].isalnum()
                    or table_sql[cursor] in {"_", "$"}
                ):
                    cursor += 1
                if table_sql[start:cursor].lower() != "check":
                    continue
                opening = cls._skip_sql_space_and_comments(
                    table_sql,
                    cursor,
                )
                if opening >= len(table_sql) or table_sql[opening] != "(":
                    continue
                extracted = cls._extract_parenthesized_sql(
                    table_sql,
                    opening,
                )
                if extracted is None:
                    continue
                expression, cursor = extracted
                expressions.append(expression)
                continue
            cursor += 1
        return tuple(expressions)

    @classmethod
    def _normalized_check_expression(cls, expression: str) -> str:
        output: list[str] = []
        cursor = 0
        while cursor < len(expression):
            if expression[cursor] == "'":
                literal: list[str] = []
                cursor += 1
                while cursor < len(expression):
                    if expression[cursor] != "'":
                        literal.append(expression[cursor].lower())
                        cursor += 1
                        continue
                    if (
                        cursor + 1 < len(expression)
                        and expression[cursor + 1] == "'"
                    ):
                        literal.append("'")
                        cursor += 2
                        continue
                    cursor += 1
                    break
                output.extend(("'", "".join(literal), "'"))
                continue
            skipped = cls._skip_sql_non_code(expression, cursor)
            if skipped is not None:
                output.append(" ")
                cursor = skipped
                continue
            output.append(expression[cursor].lower())
            cursor += 1
        return re.sub(r"\s+", "", "".join(output))

    @classmethod
    def _has_boolean_check(
        cls,
        check_expressions: Sequence[str],
        column_name: str,
    ) -> bool:
        return any(
            cls._normalized_check_expression(expression)
            .replace("(", "")
            .replace(")", "")
            in {
                f"{column_name}in0,1",
                f"{column_name}in1,0",
            }
            for expression in check_expressions
        )

    @classmethod
    def _has_anchor_check(
        cls,
        check_expressions: Sequence[str],
        column_name: str,
        first: str,
        second: str,
    ) -> bool:
        return any(
            cls._normalized_check_expression(expression)
            .replace("(", "")
            .replace(")", "")
            in {
                f"{column_name}in'{first}','{second}'",
                f"{column_name}in'{second}','{first}'",
            }
            for expression in check_expressions
        )

    @classmethod
    def _has_offset_check(
        cls,
        check_expressions: Sequence[str],
        column_name: str,
        maximum: int = MAX_FLOAT_OFFSET,
    ) -> bool:
        equivalent_checks = {
            f"{column_name}between0and{maximum}",
            (
                f"{column_name}>=0and"
                f"{column_name}<={maximum}"
            ),
            (
                f"0<={column_name}and"
                f"{column_name}<={maximum}"
            ),
            (
                f"{column_name}<={maximum}and"
                f"{column_name}>=0"
            ),
            (
                f"{maximum}>={column_name}and"
                f"{column_name}>=0"
            ),
        }
        return any(
            cls._normalized_check_expression(expression)
            .replace("(", "")
            .replace(")", "")
            in equivalent_checks
            for expression in check_expressions
        )

    @staticmethod
    def _has_cascade_app_id_foreign_key(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> bool:
        for row in connection.execute(
            f'PRAGMA foreign_key_list("{table_name}")'
        ):
            values = (
                row["table"],
                row["from"],
                row["to"],
                row["on_delete"],
            )
            if not all(
                isinstance(value, str) and bool(value)
                for value in values
            ):
                continue
            parent_table, child_column, parent_column, on_delete = values
            if (
                parent_table.lower() == "assistant_applications"
                and child_column.lower() == "app_id"
                and parent_column.lower() == "app_id"
                and on_delete.upper() == "CASCADE"
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Schema 兼容性校验
    # ------------------------------------------------------------------

    def _require_v1_compatible_tables(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._require_compatible_tables(
            connection,
            V1_APPLICATION_TABLE_SCHEMAS,
            version=1,
        )

    def _require_v2_compatible_tables(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._require_compatible_tables(
            connection,
            V2_APPLICATION_TABLE_SCHEMAS,
            version=2,
        )

    def _require_v3_compatible_tables(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._require_compatible_tables(
            connection,
            V3_APPLICATION_TABLE_SCHEMAS,
            version=3,
        )

    def _require_v4_compatible_tables(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._require_compatible_tables(
            connection,
            APPLICATION_TABLE_SCHEMAS,
            version=4,
        )

    def _require_compatible_tables(
        self,
        connection: sqlite3.Connection,
        schemas: Mapping[str, tuple[tuple[str, str, int, int], ...]],
        *,
        version: int,
    ) -> None:
        for table_name, expected in schemas.items():
            if self._table_signature(connection, table_name) != expected:
                raise SchemaMigrationError(
                    f"小助手应用数据库结构与当前 V{version} 不兼容"
                )
        application_sql = self._table_sql(
            connection,
            "assistant_applications",
        )
        check_expressions = self._extract_check_expressions(application_sql)
        if not all(
            self._has_boolean_check(check_expressions, column_name)
            for column_name in ("enabled", "show_history")
        ):
            raise SchemaMigrationError(
                f"小助手应用数据库结构与当前 V{version} 不兼容"
            )
        if version >= 2:
            defaults = self._table_defaults(
                connection,
                "assistant_applications",
            )
            expected_defaults = (
                V4_COLUMN_DEFAULTS if version >= 4
                else {
                    "header_font_color": ("text", "#1f2329"),
                    "float_icon_url": ("text", ""),
                    "float_icon_draggable": ("integer", 0),
                    "float_x_anchor": ("text", "right"),
                    "float_x_offset": ("integer", 24),
                    "float_y_anchor": ("text", "bottom"),
                    "float_y_offset": ("integer", 24),
                }
            )
            if any(
                self._normalized_schema_default(
                    defaults.get(column_name)
                ) != expected
                for column_name, expected in expected_defaults.items()
            ):
                raise SchemaMigrationError(
                    f"小助手应用数据库结构与当前 V{version} 不兼容"
                )
            if (
                not self._has_boolean_check(
                    check_expressions,
                    "float_icon_draggable",
                )
                or not self._has_anchor_check(
                    check_expressions,
                    "float_x_anchor",
                    "left",
                    "right",
                )
                or not self._has_anchor_check(
                    check_expressions,
                    "float_y_anchor",
                    "top",
                    "bottom",
                )
                or not self._has_offset_check(
                    check_expressions,
                    "float_x_offset",
                )
                or not self._has_offset_check(
                    check_expressions,
                    "float_y_offset",
                )
            ):
                raise SchemaMigrationError(
                    f"小助手应用数据库结构与当前 V{version} 不兼容"
                )
        child_tables = [
            "assistant_application_origins",
            "assistant_application_sources",
        ]
        if version >= 3:
            child_tables.append("assistant_application_links")
        for table_name in child_tables:
            if not self._has_cascade_app_id_foreign_key(
                connection,
                table_name,
            ):
                raise SchemaMigrationError(
                    f"小助手应用数据库结构与当前 V{version} 不兼容"
                )
        if version >= 3:
            link_sql = self._table_sql(
                connection,
                "assistant_application_links",
            )
            link_checks = self._extract_check_expressions(link_sql)
            if (
                not self._has_boolean_check(link_checks, "enabled")
                or not self._has_anchor_check(
                    link_checks,
                    "open_mode",
                    "new_tab",
                    "same_tab",
                )
                or not self._has_offset_check(
                    link_checks,
                    "sort_order",
                    MAX_LINK_SORT_ORDER,
                )
            ):
                raise SchemaMigrationError(
                    f"小助手应用数据库结构与当前 V{version} 不兼容"
                )

    # ------------------------------------------------------------------
    # 迁移步骤
    # ------------------------------------------------------------------

    @staticmethod
    def _create_v1_tables(connection: sqlite3.Connection) -> None:
        connection.execute(
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
                )
            """
        )
        connection.execute(
            """
                CREATE TABLE assistant_application_origins (
                    app_id TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    PRIMARY KEY (app_id, origin),
                    FOREIGN KEY (app_id)
                        REFERENCES assistant_applications(app_id)
                        ON DELETE CASCADE
                )
            """
        )
        connection.execute(
            """
                CREATE TABLE assistant_application_sources (
                    app_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    PRIMARY KEY (app_id, source_id),
                    FOREIGN KEY (app_id)
                        REFERENCES assistant_applications(app_id)
                        ON DELETE CASCADE
                )
            """
        )

    def _migrate_0_to_1(self, connection: sqlite3.Connection) -> None:
        self._create_v1_tables(connection)
        self._require_v1_compatible_tables(connection)

    def _migrate_1_to_2(self, connection: sqlite3.Connection) -> None:
        self._require_v1_compatible_tables(connection)
        statements = (
            """
            ALTER TABLE assistant_applications
            ADD COLUMN header_font_color TEXT NOT NULL
                DEFAULT '#1f2329'
            """,
            """
            ALTER TABLE assistant_applications
            ADD COLUMN float_icon_url TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE assistant_applications
            ADD COLUMN float_icon_draggable INTEGER NOT NULL DEFAULT 0
                CHECK (float_icon_draggable IN (0, 1))
            """,
            """
            ALTER TABLE assistant_applications
            ADD COLUMN float_x_anchor TEXT NOT NULL DEFAULT 'right'
                CHECK (float_x_anchor IN ('left', 'right'))
            """,
            f"""
            ALTER TABLE assistant_applications
            ADD COLUMN float_x_offset INTEGER NOT NULL DEFAULT 24
                CHECK (float_x_offset BETWEEN 0 AND {MAX_FLOAT_OFFSET})
            """,
            """
            ALTER TABLE assistant_applications
            ADD COLUMN float_y_anchor TEXT NOT NULL DEFAULT 'bottom'
                CHECK (float_y_anchor IN ('top', 'bottom'))
            """,
            f"""
            ALTER TABLE assistant_applications
            ADD COLUMN float_y_offset INTEGER NOT NULL DEFAULT 24
                CHECK (float_y_offset BETWEEN 0 AND {MAX_FLOAT_OFFSET})
            """,
        )
        for statement in statements:
            connection.execute(statement)
        self._require_v2_compatible_tables(connection)

    def _migrate_2_to_3(self, connection: sqlite3.Connection) -> None:
        self._require_v2_compatible_tables(connection)
        connection.execute(
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
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX assistant_application_links_app_order_idx
            ON assistant_application_links(app_id, sort_order, link_id)
            """
        )
        self._require_v3_compatible_tables(connection)

    def _migrate_3_to_4(self, connection: sqlite3.Connection) -> None:
        """V3→V4：安全重建全部应用表，删除 app_secret 和 token_ttl_seconds 列。

        全程处于调用方 _initialize_schema 持有的单一事务中，任一步失败整体回滚。
        先备份三个子表记录并删除旧子表，再重建 V4 主表与子表后原样恢复记录，
        避免在 foreign_keys=ON 下 DROP 主表时外键级联清空子表数据。
        """
        self._require_v3_compatible_tables(connection)

        # 1. 迁移前完整读取三个子表记录
        origins = tuple(
            (row["app_id"], row["origin"])
            for row in connection.execute(
                "SELECT app_id, origin FROM assistant_application_origins"
            )
        )
        sources = tuple(
            (row["app_id"], row["source_id"])
            for row in connection.execute(
                "SELECT app_id, source_id FROM assistant_application_sources"
            )
        )
        links = tuple(
            (
                row["link_id"],
                row["app_id"],
                row["name"],
                row["url"],
                row["open_mode"],
                row["enabled"],
                row["sort_order"],
            )
            for row in connection.execute(
                "SELECT link_id, app_id, name, url, open_mode, enabled, sort_order "
                "FROM assistant_application_links"
            )
        )

        # 2. 先删除三个旧子表（含关联索引），避免 DROP 主表时级联清空子表数据
        connection.execute("DROP TABLE assistant_application_links")
        connection.execute("DROP TABLE assistant_application_sources")
        connection.execute("DROP TABLE assistant_application_origins")

        # 3. 重建无 app_secret / token_ttl_seconds 的 V4 主表，数据迁到临时表后改名
        connection.execute(
            """
            CREATE TABLE assistant_applications_v4 (
                app_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                theme TEXT NOT NULL,
                logo_url TEXT NOT NULL,
                welcome TEXT NOT NULL,
                welcome_description TEXT NOT NULL,
                show_history INTEGER NOT NULL CHECK (show_history IN (0, 1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
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
            )
            """
        )
        # 复制除 secret/TTL 外的所有列
        connection.execute(
            """
            INSERT INTO assistant_applications_v4 (
                app_id, name, enabled, theme, logo_url,
                welcome, welcome_description, show_history,
                created_at, updated_at, header_font_color,
                float_icon_url, float_icon_draggable,
                float_x_anchor, float_x_offset,
                float_y_anchor, float_y_offset
            ) SELECT
                app_id, name, enabled, theme, logo_url,
                welcome, welcome_description, show_history,
                created_at, updated_at, header_font_color,
                float_icon_url, float_icon_draggable,
                float_x_anchor, float_x_offset,
                float_y_anchor, float_y_offset
            FROM assistant_applications
            """
        )
        connection.execute("DROP TABLE assistant_applications")
        connection.execute(
            "ALTER TABLE assistant_applications_v4 "
            "RENAME TO assistant_applications"
        )

        # 4. 重建三个子表、约束和索引
        connection.execute(
            """
            CREATE TABLE assistant_application_origins (
                app_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                PRIMARY KEY (app_id, origin),
                FOREIGN KEY (app_id)
                    REFERENCES assistant_applications(app_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE assistant_application_sources (
                app_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                PRIMARY KEY (app_id, source_id),
                FOREIGN KEY (app_id)
                    REFERENCES assistant_applications(app_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
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
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX assistant_application_links_app_order_idx
            ON assistant_application_links(app_id, sort_order, link_id)
            """
        )

        # 5. 原样恢复所有子表记录
        connection.executemany(
            "INSERT INTO assistant_application_origins (app_id, origin) "
            "VALUES (?, ?)",
            origins,
        )
        connection.executemany(
            "INSERT INTO assistant_application_sources (app_id, source_id) "
            "VALUES (?, ?)",
            sources,
        )
        connection.executemany(
            """
            INSERT INTO assistant_application_links (
                link_id, app_id, name, url,
                open_mode, enabled, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            links,
        )

        # 6. 外键完整性校验：非空即回滚并报错
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise SchemaMigrationError(
                "小助手应用数据库迁移后外键完整性校验失败"
            )

        self._require_v4_compatible_tables(connection)

    # ------------------------------------------------------------------
    # Schema 初始化（自动迁移）
    # ------------------------------------------------------------------

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        version_schema = (
            ("component", "TEXT", 0, 1),
            ("version", "INTEGER", 1, 0),
            ("updated_at", "INTEGER", 1, 0),
        )
        table_names = self._table_names(connection)
        v4_application_tables = set(APPLICATION_TABLE_SCHEMAS)
        v3_application_tables = set(V3_APPLICATION_TABLE_SCHEMAS)
        v2_application_tables = set(V2_APPLICATION_TABLE_SCHEMAS)
        existing_application_tables = table_names & v4_application_tables

        if (
            SCHEMA_VERSION_TABLE in table_names
            and self._table_signature(connection, SCHEMA_VERSION_TABLE)
            != version_schema
        ):
            raise SchemaMigrationError("系统数据库版本表结构不兼容")

        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
                component TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        row = connection.execute(
            f"""
            SELECT version FROM {SCHEMA_VERSION_TABLE}
            WHERE component = ?
            """,
            (SCHEMA_COMPONENT,),
        ).fetchone()

        if row is None:
            if existing_application_tables:
                # 已存在表但无版本记录 → 自动检测并迁移
                if frozenset(existing_application_tables) not in {
                    frozenset(v2_application_tables),
                    frozenset(v3_application_tables),
                    frozenset(v4_application_tables),
                }:
                    raise SchemaMigrationError(
                        "小助手应用数据库仅存在部分应用表，无法安全接管"
                    )
                application_signature = self._table_signature(
                    connection,
                    "assistant_applications",
                )
                if (
                    application_signature
                    == V1_APPLICATION_TABLE_SCHEMAS[
                        "assistant_applications"
                    ]
                ):
                    self._require_v1_compatible_tables(connection)
                    self._migrate_1_to_2(connection)
                    self._migrate_2_to_3(connection)
                    self._migrate_3_to_4(connection)
                elif (
                    application_signature
                    == V2_APPLICATION_TABLE_SCHEMAS[
                        "assistant_applications"
                    ]
                ):
                    if existing_application_tables == v2_application_tables:
                        self._require_v2_compatible_tables(connection)
                        self._migrate_2_to_3(connection)
                        self._migrate_3_to_4(connection)
                    elif existing_application_tables == v3_application_tables:
                        self._require_v3_compatible_tables(connection)
                        self._migrate_3_to_4(connection)
                    else:
                        self._require_v4_compatible_tables(connection)
                elif (
                    application_signature
                    == V3_APPLICATION_TABLE_SCHEMAS[
                        "assistant_applications"
                    ]
                ):
                    if existing_application_tables == v3_application_tables:
                        self._require_v3_compatible_tables(connection)
                        self._migrate_3_to_4(connection)
                    else:
                        self._require_v4_compatible_tables(connection)
                elif (
                    application_signature
                    == APPLICATION_TABLE_SCHEMAS[
                        "assistant_applications"
                    ]
                ):
                    self._require_v4_compatible_tables(connection)
                else:
                    raise SchemaMigrationError(
                        "小助手应用数据库结构无法安全接管"
                    )
            else:
                # 全新数据库，从 V1 一直迁移到 V4
                self._migrate_0_to_1(connection)
                self._migrate_1_to_2(connection)
                self._migrate_2_to_3(connection)
                self._migrate_3_to_4(connection)
            connection.execute(
                f"""
                INSERT INTO {SCHEMA_VERSION_TABLE}
                    (component, version, updated_at)
                VALUES (?, ?, ?)
                """,
                (SCHEMA_COMPONENT, SCHEMA_VERSION, int(time.time())),
            )
            return

        version = row["version"]
        if (
            not isinstance(version, int)
            or version < 0
            or version > SCHEMA_VERSION
        ):
            raise SchemaMigrationError("小助手应用数据库版本不受当前代码支持")
        if version == 0 and existing_application_tables:
            raise SchemaMigrationError(
                "V0 数据库已存在 V1 应用表，无法安全迁移"
            )
        migrations = {
            0: self._migrate_0_to_1,
            1: self._migrate_1_to_2,
            2: self._migrate_2_to_3,
            3: self._migrate_3_to_4,
        }
        while version < SCHEMA_VERSION:
            migration = migrations.get(version)
            if migration is None:
                raise SchemaMigrationError("缺少小助手应用数据库迁移步骤")
            migration(connection)
            version += 1
            connection.execute(
                f"""
                UPDATE {SCHEMA_VERSION_TABLE}
                SET version = ?, updated_at = ?
                WHERE component = ?
                """,
                (version, int(time.time()), SCHEMA_COMPONENT),
            )
        self._require_v4_compatible_tables(connection)

    # ------------------------------------------------------------------
    # 规范化
    # ------------------------------------------------------------------

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

    @staticmethod
    def _normalize_application_links(
        links: Sequence[Mapping[str, Any] | AssistantApplicationLink] | None,
    ) -> tuple[AssistantApplicationLink, ...]:
        if links is None:
            return ()
        if isinstance(links, (str, bytes)) or not isinstance(links, Sequence):
            raise InvalidApplicationConfiguration(
                "application_links 必须是入口对象序列"
            )
        if len(links) > MAX_APPLICATION_LINKS:
            raise InvalidApplicationConfiguration(
                f"关联网站入口不能超过 {MAX_APPLICATION_LINKS} 个"
            )
        normalized: list[AssistantApplicationLink] = []
        link_ids: set[str] = set()
        for item in links:
            values: Mapping[str, Any]
            if isinstance(item, AssistantApplicationLink):
                values = {
                    "link_id": item.link_id,
                    "name": item.name,
                    "url": item.url,
                    "open_mode": item.open_mode,
                    "enabled": item.enabled,
                    "sort_order": item.sort_order,
                }
            elif isinstance(item, Mapping):
                values = item
            else:
                raise InvalidApplicationConfiguration(
                    "关联网站入口必须是对象"
                )
            link_id = values.get("link_id")
            if (
                not isinstance(link_id, str)
                or LINK_ID_PATTERN.fullmatch(link_id) is None
            ):
                raise InvalidApplicationConfiguration(
                    "link_id 必须为 3～64 位字母、数字、下划线或短横线"
                )
            if link_id in link_ids:
                raise InvalidApplicationConfiguration("link_id 不能重复")
            link_ids.add(link_id)
            name = _validate_text(
                "关联网站名称",
                values.get("name"),
                maximum=120,
            )
            url = normalize_application_url(values.get("url"))
            open_mode = values.get("open_mode")
            if open_mode not in APPLICATION_LINK_OPEN_MODES:
                raise InvalidApplicationConfiguration(
                    "open_mode 必须是 new_tab 或 same_tab"
                )
            enabled = values.get("enabled")
            if not isinstance(enabled, bool):
                raise InvalidApplicationConfiguration(
                    "关联网站 enabled 必须是布尔值"
                )
            sort_order = values.get("sort_order")
            if (
                isinstance(sort_order, bool)
                or not isinstance(sort_order, int)
                or sort_order < 0
                or sort_order > MAX_LINK_SORT_ORDER
            ):
                raise InvalidApplicationConfiguration(
                    f"sort_order 必须是 0～{MAX_LINK_SORT_ORDER} 的整数"
                )
            normalized.append(
                AssistantApplicationLink(
                    link_id=link_id,
                    name=name,
                    url=url,
                    open_mode=open_mode,
                    enabled=enabled,
                    sort_order=sort_order,
                )
            )
        return tuple(
            sorted(normalized, key=lambda item: (item.sort_order, item.link_id))
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        app_id: str,
        name: str,
        allowed_origins: Sequence[str] = (),
        allowed_source_ids: Sequence[str] = (),
        application_links: Sequence[
            Mapping[str, Any] | AssistantApplicationLink
        ] = (),
        theme: str = DEFAULT_THEME,
        header_font_color: str = DEFAULT_HEADER_FONT_COLOR,
        logo_url: str = "",
        welcome: str = DEFAULT_WELCOME,
        welcome_description: str = DEFAULT_WELCOME_DESCRIPTION,
        float_icon_url: str = DEFAULT_FLOAT_ICON_URL,
        float_icon_draggable: bool = DEFAULT_FLOAT_ICON_DRAGGABLE,
        float_x_anchor: str = DEFAULT_FLOAT_X_ANCHOR,
        float_x_offset: int = DEFAULT_FLOAT_X_OFFSET,
        float_y_anchor: str = DEFAULT_FLOAT_Y_ANCHOR,
        float_y_offset: int = DEFAULT_FLOAT_Y_OFFSET,
        show_history: bool = False,
        enabled: bool = True,
    ) -> AssistantApplicationView:
        app_id = validate_app_id(app_id)
        name = _validate_text("name", name, maximum=120)
        origins = self._normalize_origins(allowed_origins)
        source_ids = self._normalize_source_ids(allowed_source_ids)
        links = self._normalize_application_links(application_links)
        theme = _validate_theme(theme)
        header_font_color = _validate_color(
            "header_font_color",
            header_font_color,
            DEFAULT_HEADER_FONT_COLOR,
        )
        logo_url = _validate_logo_url(logo_url)
        welcome = _validate_text("welcome", welcome, maximum=120)
        welcome_description = _validate_text(
            "welcome_description",
            welcome_description,
            maximum=500,
        )
        float_icon_url = _validate_asset_url(
            "float_icon_url",
            float_icon_url,
        )
        if (
            not isinstance(show_history, bool)
            or not isinstance(enabled, bool)
            or not isinstance(float_icon_draggable, bool)
        ):
            raise InvalidApplicationConfiguration(
                "enabled、show_history 和 float_icon_draggable 必须是布尔值"
            )
        float_x_anchor = _validate_anchor(
            "float_x_anchor",
            float_x_anchor,
            {"left", "right"},
        )
        float_x_offset = _validate_offset(
            "float_x_offset",
            float_x_offset,
        )
        float_y_anchor = _validate_anchor(
            "float_y_anchor",
            float_y_anchor,
            {"top", "bottom"},
        )
        float_y_offset = _validate_offset(
            "float_y_offset",
            float_y_offset,
        )
        now = int(time.time())
        self.initialize()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO assistant_applications (
                        app_id, name, enabled, theme, logo_url,
                        welcome, welcome_description, show_history,
                        created_at, updated_at, header_font_color,
                        float_icon_url, float_icon_draggable,
                        float_x_anchor, float_x_offset,
                        float_y_anchor, float_y_offset
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        app_id,
                        name,
                        int(enabled),
                        theme,
                        logo_url,
                        welcome,
                        welcome_description,
                        int(show_history),
                        now,
                        now,
                        header_font_color,
                        float_icon_url,
                        int(float_icon_draggable),
                        float_x_anchor,
                        float_x_offset,
                        float_y_anchor,
                        float_y_offset,
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
                connection.executemany(
                    """
                    INSERT INTO assistant_application_links (
                        link_id, app_id, name, url,
                        open_mode, enabled, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            link.link_id,
                            app_id,
                            link.name,
                            link.url,
                            link.open_mode,
                            int(link.enabled),
                            link.sort_order,
                        )
                        for link in links
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if self._exists(app_id):
                raise ApplicationAlreadyExists(
                    f"应用已存在: {app_id}"
                ) from None
            raise
        return self.get(app_id)

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
            application_links = tuple(
                AssistantApplicationLink(
                    link_id=item["link_id"],
                    name=item["name"],
                    url=item["url"],
                    open_mode=item["open_mode"],
                    enabled=bool(item["enabled"]),
                    sort_order=item["sort_order"],
                )
                for item in connection.execute(
                    """
                    SELECT link_id, name, url, open_mode, enabled, sort_order
                    FROM assistant_application_links
                    WHERE app_id = ?
                    ORDER BY sort_order, link_id
                    """,
                    (app_id,),
                )
            )
        return AssistantApplication(
            app_id=row["app_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            allowed_origins=origins,
            allowed_source_ids=source_ids,
            application_links=application_links,
            theme=row["theme"],
            header_font_color=row["header_font_color"],
            logo_url=row["logo_url"],
            welcome=row["welcome"],
            welcome_description=row["welcome_description"],
            float_icon_url=row["float_icon_url"],
            float_icon_draggable=bool(row["float_icon_draggable"]),
            float_x_anchor=row["float_x_anchor"],
            float_x_offset=row["float_x_offset"],
            float_y_anchor=row["float_y_anchor"],
            float_y_offset=row["float_y_offset"],
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
            allowed_origins=application.allowed_origins,
            allowed_source_ids=application.allowed_source_ids,
            application_links=application.application_links,
            theme=application.theme,
            header_font_color=application.header_font_color,
            logo_url=application.logo_url,
            welcome=application.welcome,
            welcome_description=application.welcome_description,
            float_icon_url=application.float_icon_url,
            float_icon_draggable=application.float_icon_draggable,
            float_x_anchor=application.float_x_anchor,
            float_x_offset=application.float_x_offset,
            float_y_anchor=application.float_y_anchor,
            float_y_offset=application.float_y_offset,
            show_history=application.show_history,
            created_at=application.created_at,
            updated_at=application.updated_at,
        )

    def get(self, app_id: str) -> AssistantApplicationView:
        return self._to_view(self._load_full(app_id))

    def require_origin_verification(
        self,
        app_id: str,
    ) -> AssistantApplication:
        """加载完整应用记录用于 Origin 校验。若应用已禁用则拒绝。"""
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
        application_links: Sequence[
            Mapping[str, Any] | AssistantApplicationLink
        ] | None = None,
        theme: str | None = None,
        header_font_color: str | None = None,
        logo_url: str | None = None,
        welcome: str | None = None,
        welcome_description: str | None = None,
        float_icon_url: str | None = None,
        float_icon_draggable: bool | None = None,
        float_x_anchor: str | None = None,
        float_x_offset: int | None = None,
        float_y_anchor: str | None = None,
        float_y_offset: int | None = None,
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
        next_links = (
            current.application_links
            if application_links is None
            else self._normalize_application_links(application_links)
        )
        next_theme = current.theme if theme is None else _validate_theme(theme)
        next_header_font_color = (
            current.header_font_color
            if header_font_color is None
            else _validate_color(
                "header_font_color",
                header_font_color,
                DEFAULT_HEADER_FONT_COLOR,
            )
        )
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
        next_float_icon_url = (
            current.float_icon_url
            if float_icon_url is None
            else _validate_asset_url("float_icon_url", float_icon_url)
        )
        if (
            float_icon_draggable is not None
            and not isinstance(float_icon_draggable, bool)
        ):
            raise InvalidApplicationConfiguration(
                "float_icon_draggable 必须是布尔值"
            )
        next_float_icon_draggable = (
            current.float_icon_draggable
            if float_icon_draggable is None
            else float_icon_draggable
        )
        next_float_x_anchor = (
            current.float_x_anchor
            if float_x_anchor is None
            else _validate_anchor(
                "float_x_anchor",
                float_x_anchor,
                {"left", "right"},
            )
        )
        next_float_x_offset = (
            current.float_x_offset
            if float_x_offset is None
            else _validate_offset("float_x_offset", float_x_offset)
        )
        next_float_y_anchor = (
            current.float_y_anchor
            if float_y_anchor is None
            else _validate_anchor(
                "float_y_anchor",
                float_y_anchor,
                {"top", "bottom"},
            )
        )
        next_float_y_offset = (
            current.float_y_offset
            if float_y_offset is None
            else _validate_offset("float_y_offset", float_y_offset)
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
                SET name = ?, theme = ?,
                    header_font_color = ?, logo_url = ?, welcome = ?,
                    welcome_description = ?, float_icon_url = ?,
                    float_icon_draggable = ?, float_x_anchor = ?,
                    float_x_offset = ?, float_y_anchor = ?,
                    float_y_offset = ?, show_history = ?, updated_at = ?
                WHERE app_id = ?
                """,
                (
                    next_name,
                    next_theme,
                    next_header_font_color,
                    next_logo,
                    next_welcome,
                    next_description,
                    next_float_icon_url,
                    int(next_float_icon_draggable),
                    next_float_x_anchor,
                    next_float_x_offset,
                    next_float_y_anchor,
                    next_float_y_offset,
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
            if application_links is not None:
                connection.execute(
                    "DELETE FROM assistant_application_links WHERE app_id = ?",
                    (app_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO assistant_application_links (
                        link_id, app_id, name, url,
                        open_mode, enabled, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            link.link_id,
                            app_id,
                            link.name,
                            link.url,
                            link.open_mode,
                            int(link.enabled),
                            link.sort_order,
                        )
                        for link in next_links
                    ),
                )
        return self.get(app_id)

    def delete(self, app_id: str) -> None:
        self._load_full(app_id)
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM assistant_applications WHERE app_id = ?",
                (app_id,),
            )

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

"""多数据源运行时配置契约。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any


SOURCE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
POSTGRESQL_REQUIRED_CONNECTION_FIELDS = (
    "host",
    "port",
    "database",
    "user",
    "password",
    "connect_timeout",
)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(child) for key, child in deepcopy(dict(value)).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(child) for child in deepcopy(value))
    if isinstance(value, tuple):
        return tuple(_freeze_value(child) for child in deepcopy(value))
    if isinstance(value, set):
        return frozenset(_freeze_value(child) for child in deepcopy(value))
    return deepcopy(value)


def _require_nonempty_string(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def _require_absolute_path(field_name: str, value: Any) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} 必须是 pathlib.Path 或有效路径") from exc
    if not path.is_absolute():
        raise ValueError(f"{field_name} 必须是绝对路径")
    return path


def _validate_postgresql_connection_settings(settings: Mapping[str, Any]) -> None:
    missing = [
        field_name
        for field_name in POSTGRESQL_REQUIRED_CONNECTION_FIELDS
        if field_name not in settings
        or settings[field_name] is None
        or (
            isinstance(settings[field_name], str)
            and not settings[field_name].strip()
        )
    ]
    if missing:
        raise ValueError("PostgreSQL 连接配置缺少必需字段：" + ", ".join(missing))

    for field_name in ("port", "connect_timeout"):
        value = settings[field_name]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"PostgreSQL 连接字段 {field_name} 必须是正整数")

    for field_name in ("host", "database", "user", "password"):
        if not isinstance(settings[field_name], str):
            raise ValueError(f"PostgreSQL 连接字段 {field_name} 必须是非空字符串")


@dataclass(frozen=True)
class DataSourceConfig:
    """一个数据源所需的完整、不可变运行时配置。"""

    source_id: str
    database_type: str
    sql_dialect: str
    connection_settings: Mapping[str, Any] = field(repr=False)
    metadata_path: Path
    memory_path: Path
    read_only: bool

    def __post_init__(self) -> None:
        source_id = _require_nonempty_string("source_id", self.source_id)
        if source_id != source_id.lower():
            raise ValueError("source_id 必须由调用方以小写形式传入")
        if SOURCE_ID_PATTERN.fullmatch(source_id) is None:
            raise ValueError("source_id 必须符合 [a-z][a-z0-9_-]{0,63}")

        database_type = _require_nonempty_string(
            "database_type", self.database_type
        )
        sql_dialect = _require_nonempty_string("sql_dialect", self.sql_dialect)
        if "postgresql" in {database_type, sql_dialect} and (
            database_type != "postgresql" or sql_dialect != "postgresql"
        ):
            raise ValueError(
                "PostgreSQL 数据源必须同时使用 database_type=postgresql "
                "和 sql_dialect=postgresql"
            )

        if not isinstance(self.connection_settings, Mapping):
            raise ValueError("connection_settings 必须是映射")
        connection_settings = _freeze_value(self.connection_settings)
        if database_type == "postgresql":
            _validate_postgresql_connection_settings(connection_settings)

        metadata_path = _require_absolute_path(
            "metadata_path", self.metadata_path
        )
        memory_path = _require_absolute_path("memory_path", self.memory_path)
        if metadata_path == memory_path:
            raise ValueError("metadata_path 与 memory_path 不能相同")
        if self.read_only is not True:
            raise ValueError("首版 DataSourceConfig 只接受 read_only=True")

        object.__setattr__(self, "connection_settings", connection_settings)
        object.__setattr__(self, "metadata_path", metadata_path)
        object.__setattr__(self, "memory_path", memory_path)

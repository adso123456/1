"""PostgreSQL 与 MySQL 数据源的离线配置构建入口。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from config.data_source_config import DataSourceConfig
from config.settings import (
    CHROMA_DIR,
    DEFAULT_METADATA_INDEX_PATH,
    METADATA_INDEX_PATH_ENV,
    PROJECT_ROOT,
    build_db_kwargs,
    resolve_project_path,
    validate_db_config,
)


DEFAULT_POSTGRESQL_SCOPE_PATH = (
    PROJECT_ROOT / "config" / "postgresql_metadata_scope.json"
)
DEFAULT_MYSQL_SCOPE_PATH = (
    PROJECT_ROOT / "config" / "mysql_lzh_monitor_metadata_scope.json"
)


def _resolve_metadata_path(environ: Mapping[str, str] | None) -> Path:
    if environ is None:
        selected_path = (
            os.getenv(METADATA_INDEX_PATH_ENV) or DEFAULT_METADATA_INDEX_PATH
        )
    else:
        selected_path = (
            environ.get(METADATA_INDEX_PATH_ENV) or DEFAULT_METADATA_INDEX_PATH
        )
    return resolve_project_path(selected_path)


def _resolve_memory_path(environ: Mapping[str, str] | None) -> Path:
    if environ is None:
        return Path(CHROMA_DIR)
    configured_path = environ.get("VANNA_DATA_DIR", "").strip()
    if configured_path:
        return resolve_project_path(configured_path)
    return (PROJECT_ROOT / "vanna_data").resolve()


def _load_postgresql_scope(scope_path: Path) -> dict[str, object]:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if not isinstance(scope, dict):
        raise ValueError("PostgreSQL Metadata scope 顶层必须是对象")
    return scope


def build_postgresql_data_source_config(
    *,
    environ: Mapping[str, str] | None = None,
    scope_path: Path = DEFAULT_POSTGRESQL_SCOPE_PATH,
) -> DataSourceConfig:
    """从现有环境规则和 PostgreSQL scope 构造完整配置，不打开运行资产。"""
    resolved_scope_path = Path(scope_path).expanduser().resolve()
    scope = _load_postgresql_scope(resolved_scope_path)

    source_id = scope.get("datasource_id")
    dialect = scope.get("dialect")
    if dialect != "postgresql":
        raise ValueError("PostgreSQL Metadata scope 的 dialect 必须为 postgresql")

    connection_settings = build_db_kwargs(environ)
    validate_db_config(connection_settings)

    return DataSourceConfig(
        source_id=source_id,
        database_type=dialect,
        sql_dialect=dialect,
        connection_settings=connection_settings,
        metadata_path=_resolve_metadata_path(environ),
        memory_path=_resolve_memory_path(environ),
        read_only=True,
    )


def _positive_mysql_int(
    source: Mapping[str, str], name: str, default: int
) -> int:
    raw_value = source.get(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"环境变量 {name} 必须是正整数")
    return value


def build_mysql_data_source_config(
    *,
    environ: Mapping[str, str] | None = None,
    scope_path: Path = DEFAULT_MYSQL_SCOPE_PATH,
) -> DataSourceConfig:
    """从独立环境变量构造 lzh_monitor MySQL 只读数据源配置。"""
    source = os.environ if environ is None else environ
    resolved_scope_path = Path(scope_path).expanduser().resolve()
    scope = json.loads(resolved_scope_path.read_text(encoding="utf-8"))
    if not isinstance(scope, dict):
        raise ValueError("MySQL Metadata scope 顶层必须是对象")
    if scope.get("dialect") != "mysql":
        raise ValueError("MySQL Metadata scope 的 dialect 必须为 mysql")

    connection_settings = {
        "host": source.get("MYSQL_HOST", "127.0.0.1"),
        "port": _positive_mysql_int(source, "MYSQL_PORT", 3307),
        "database": source.get("MYSQL_DATABASE", "lzh_monitor"),
        "user": source.get("MYSQL_USER"),
        "password": source.get("MYSQL_PASSWORD"),
        "connect_timeout": _positive_mysql_int(
            source, "MYSQL_CONNECT_TIMEOUT", 10
        ),
        "charset": "utf8mb4",
    }
    mysql_root = (PROJECT_ROOT / "agent_data" / "mysql-lzh-monitor").resolve()
    metadata_path = resolve_project_path(
        source.get(
            "MYSQL_METADATA_INDEX_PATH",
            str(mysql_root / "column_metadata_index.json"),
        )
    )
    memory_path = resolve_project_path(
        source.get(
            "MYSQL_VANNA_DATA_DIR",
            str(PROJECT_ROOT / "vanna_data" / "mysql-lzh-monitor"),
        )
    )

    return DataSourceConfig(
        source_id=scope.get("datasource_id"),
        database_type="mysql",
        sql_dialect="mysql",
        connection_settings=connection_settings,
        metadata_path=metadata_path,
        memory_path=memory_path,
        read_only=True,
    )

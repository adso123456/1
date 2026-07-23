"""当前 PostgreSQL 数据源的离线配置构建入口。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from backend.data_source_config import DataSourceConfig
from backend.metadata_retriever import DEFAULT_INDEX_PATH, METADATA_INDEX_PATH_ENV
from config.settings import (
    CHROMA_DIR,
    PROJECT_ROOT,
    build_db_kwargs,
    validate_db_config,
)


DEFAULT_POSTGRESQL_SCOPE_PATH = (
    PROJECT_ROOT / "config" / "postgresql_metadata_scope.json"
)


def _resolve_metadata_path(environ: Mapping[str, str] | None) -> Path:
    if environ is None:
        selected_path = os.getenv(METADATA_INDEX_PATH_ENV) or DEFAULT_INDEX_PATH
    else:
        selected_path = environ.get(METADATA_INDEX_PATH_ENV) or DEFAULT_INDEX_PATH
    return Path(selected_path).expanduser().resolve()


def _resolve_memory_path(environ: Mapping[str, str] | None) -> Path:
    if environ is None:
        return Path(CHROMA_DIR)
    configured_path = environ.get("VANNA_DATA_DIR", "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()
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

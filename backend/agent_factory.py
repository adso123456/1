"""当前 PostgreSQL Agent 的兼容创建入口。"""

from __future__ import annotations

from collections.abc import Mapping

from backend.diagnostic_metadata_retriever import DiagnosticMetadataRetriever
from backend.postgresql_runtime_factory import create_postgresql_runtime
from config.data_sources import build_postgresql_data_source_config


def create_current_postgresql_runtime(
    *,
    environ: Mapping[str, str] | None = None,
):
    """从当前 PostgreSQL 配置创建完整 Runtime。"""
    config = build_postgresql_data_source_config(environ=environ)
    return create_postgresql_runtime(config, environ=environ)


def create_agent():
    """保持现有服务入口，返回当前 PostgreSQL Runtime 的 Agent。"""
    config = build_postgresql_data_source_config()
    return create_postgresql_runtime(config).agent

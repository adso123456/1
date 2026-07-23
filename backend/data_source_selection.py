"""请求 metadata 的纯离线数据源选择契约。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.data_source_config import DataSourceConfig

if TYPE_CHECKING:
    from backend.data_source_registry import DataSourceRegistry


SOURCE_ID_METADATA_KEY = "source_id"


@dataclass(frozen=True)
class ResolvedDataSource:
    """一次请求已经显式解析的数据源。"""

    source_id: str
    config: DataSourceConfig = field(repr=False)

    def __post_init__(self) -> None:
        if self.source_id != self.config.source_id:
            raise ValueError("选择结果的 source_id 与配置不一致")


def resolve_data_source(
    metadata: Mapping[str, Any],
    registry: DataSourceRegistry,
) -> ResolvedDataSource:
    """从请求 metadata 显式解析已注册的数据源配置。"""
    if metadata is None:
        raise ValueError("metadata 必须显式提供")
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata 必须是 Mapping")
    if SOURCE_ID_METADATA_KEY not in metadata:
        raise ValueError("metadata 缺少 source_id")

    source_id = metadata[SOURCE_ID_METADATA_KEY]
    if source_id is None:
        raise ValueError("source_id 必须显式提供")
    if not isinstance(source_id, str):
        raise TypeError("source_id 必须是字符串")
    if not source_id.strip():
        raise ValueError("source_id 必须是非空字符串")

    config = registry.require(source_id)
    if config.source_id != source_id:
        raise ValueError("Registry 返回的配置与请求 source_id 不一致")
    return ResolvedDataSource(source_id=source_id, config=config)

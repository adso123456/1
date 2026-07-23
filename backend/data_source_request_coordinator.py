"""纯离线的请求级数据源选择与会话绑定编排器。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from backend.conversation_data_source_binding import (
    ConversationDataSourceBinding,
    ConversationDataSourceBindings,
)
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_selection import resolve_data_source
from config.data_source_config import DataSourceConfig


def _require_nonempty_string(field_name: str, value: Any) -> str:
    if value is None:
        raise ValueError(f"{field_name} 必须显式提供")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是字符串")
    if not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


@dataclass(frozen=True)
class DataSourceRequestContext:
    """一个请求已经解析并绑定的数据源上下文。"""

    conversation_id: str
    source_id: str
    config: DataSourceConfig = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonempty_string("conversation_id", self.conversation_id)
        _require_nonempty_string("source_id", self.source_id)
        if not isinstance(self.config, DataSourceConfig):
            raise TypeError("config 必须是 DataSourceConfig")
        if self.source_id != self.config.source_id:
            raise ValueError("请求上下文的 source_id 与配置不一致")


class DataSourceRequestCoordinator:
    """组合显式数据源选择和进程内会话绑定。"""

    def __init__(
        self,
        registry: DataSourceRegistry,
        bindings: ConversationDataSourceBindings | None = None,
    ) -> None:
        if not isinstance(registry, DataSourceRegistry):
            raise TypeError("registry 必须是 DataSourceRegistry")
        if bindings is not None and not isinstance(
            bindings,
            ConversationDataSourceBindings,
        ):
            raise TypeError(
                "bindings 必须是 ConversationDataSourceBindings"
            )
        self._registry = registry
        self._bindings = (
            bindings
            if bindings is not None
            else ConversationDataSourceBindings()
        )

    @property
    def bindings(self) -> Mapping[str, ConversationDataSourceBinding]:
        return self._bindings.bindings

    def resolve(
        self,
        conversation_id: str,
        metadata: Mapping[str, Any],
    ) -> DataSourceRequestContext:
        resolved_data_source = resolve_data_source(metadata, self._registry)
        binding = self._bindings.bind(
            conversation_id,
            resolved_data_source,
        )
        return DataSourceRequestContext(
            conversation_id=binding.conversation_id,
            source_id=binding.source_id,
            config=resolved_data_source.config,
        )

    def require(self, conversation_id: str) -> DataSourceRequestContext:
        binding = self._bindings.require(conversation_id)
        config = self._registry.require(binding.source_id)
        return DataSourceRequestContext(
            conversation_id=binding.conversation_id,
            source_id=binding.source_id,
            config=config,
        )

    def release(self, conversation_id: str) -> DataSourceRequestContext:
        binding = self._bindings.release(conversation_id)
        config = self._registry.require(binding.source_id)
        return DataSourceRequestContext(
            conversation_id=binding.conversation_id,
            source_id=binding.source_id,
            config=config,
        )

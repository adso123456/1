"""纯内存的会话—数据源绑定契约。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from backend.data_source_selection import ResolvedDataSource


def _require_nonempty_string(field_name: str, value: Any) -> str:
    if value is None:
        raise ValueError(f"{field_name} 必须显式提供")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是字符串")
    if not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


@dataclass(frozen=True)
class ConversationDataSourceBinding:
    """一个会话已经固定的数据源身份。"""

    conversation_id: str
    source_id: str

    def __post_init__(self) -> None:
        _require_nonempty_string("conversation_id", self.conversation_id)
        _require_nonempty_string("source_id", self.source_id)


class ConversationDataSourceBindings:
    """管理当前进程内显式建立的会话—数据源绑定。"""

    def __init__(self) -> None:
        self._bindings: dict[str, ConversationDataSourceBinding] = {}

    @property
    def bindings(self) -> Mapping[str, ConversationDataSourceBinding]:
        snapshot = {
            conversation_id: self._bindings[conversation_id]
            for conversation_id in sorted(self._bindings)
        }
        return MappingProxyType(snapshot)

    def bind(
        self,
        conversation_id: str,
        resolved_data_source: ResolvedDataSource,
    ) -> ConversationDataSourceBinding:
        conversation_id = _require_nonempty_string(
            "conversation_id",
            conversation_id,
        )
        if not isinstance(resolved_data_source, ResolvedDataSource):
            raise TypeError("resolved_data_source 必须是 ResolvedDataSource")

        requested_source_id = resolved_data_source.source_id
        existing = self._bindings.get(conversation_id)
        if existing is not None:
            if existing.source_id == requested_source_id:
                return existing
            raise ValueError(
                f"会话 {conversation_id} 已绑定 {existing.source_id}，"
                f"不能切换到 {requested_source_id}"
            )

        binding = ConversationDataSourceBinding(
            conversation_id=conversation_id,
            source_id=requested_source_id,
        )
        self._bindings[conversation_id] = binding
        return binding

    def require(self, conversation_id: str) -> ConversationDataSourceBinding:
        conversation_id = _require_nonempty_string(
            "conversation_id",
            conversation_id,
        )
        try:
            return self._bindings[conversation_id]
        except KeyError:
            raise ValueError(f"会话 {conversation_id} 尚未绑定数据源") from None

    def release(self, conversation_id: str) -> ConversationDataSourceBinding:
        conversation_id = _require_nonempty_string(
            "conversation_id",
            conversation_id,
        )
        try:
            return self._bindings.pop(conversation_id)
        except KeyError:
            raise ValueError(f"会话 {conversation_id} 尚未绑定数据源") from None

    def __repr__(self) -> str:
        return (
            "ConversationDataSourceBindings("
            f"conversation_ids={tuple(sorted(self._bindings))!r})"
        )

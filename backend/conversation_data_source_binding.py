"""纯内存的会话—数据源绑定契约。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
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

    def __init__(
        self,
        catalog: Any = None,
    ) -> None:
        self._bindings: dict[str, ConversationDataSourceBinding] = {}
        self._lock = RLock()
        self._catalog = catalog

    @property
    def bindings(self) -> Mapping[str, ConversationDataSourceBinding]:
        if self._catalog is not None:
            with self._catalog._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT conversation_id, source_id
                    FROM conversation_source_bindings
                    ORDER BY conversation_id
                    """
                ).fetchall()
            return MappingProxyType(
                {
                    row["conversation_id"]: ConversationDataSourceBinding(
                        row["conversation_id"], row["source_id"]
                    )
                    for row in rows
                }
            )
        with self._lock:
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
        if self._catalog is not None:
            try:
                conversation_id, source_id = self._catalog.bind_conversation(
                    conversation_id,
                    requested_source_id,
                )
            except Exception as exc:
                raise ValueError(str(exc)) from None
            return ConversationDataSourceBinding(conversation_id, source_id)
        with self._lock:
            existing = self._bindings.get(conversation_id)
            if existing is not None:
                if existing.source_id == requested_source_id:
                    return existing
                raise ValueError("当前会话已绑定其他数据源，不能修改绑定")

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
        if self._catalog is not None:
            try:
                bound_id, source_id = self._catalog.require_binding(
                    conversation_id
                )
            except Exception:
                raise ValueError(
                    f"会话 {conversation_id} 尚未绑定数据源"
                ) from None
            return ConversationDataSourceBinding(bound_id, source_id)
        with self._lock:
            try:
                return self._bindings[conversation_id]
            except KeyError:
                raise ValueError(
                    f"会话 {conversation_id} 尚未绑定数据源"
                ) from None

    def release(self, conversation_id: str) -> ConversationDataSourceBinding:
        conversation_id = _require_nonempty_string(
            "conversation_id",
            conversation_id,
        )
        if self._catalog is not None:
            raise ValueError("持久化会话绑定不可解除")
        with self._lock:
            try:
                return self._bindings.pop(conversation_id)
            except KeyError:
                raise ValueError(
                    f"会话 {conversation_id} 尚未绑定数据源"
                ) from None

    def __repr__(self) -> str:
        with self._lock:
            return (
                "ConversationDataSourceBindings("
                f"conversation_ids={tuple(sorted(self._bindings))!r})"
            )

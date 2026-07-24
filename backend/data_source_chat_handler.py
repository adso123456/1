"""按请求数据源选择 Runtime Agent 的聊天处理器。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from vanna.servers.base import (
    ChatHandler,
    ChatRequest,
    ChatResponse,
    ChatStreamChunk,
)


class DataSourceChatHandler:
    """在进入现有聊天链路前完成会话绑定和 Runtime 路由。"""

    def __init__(
        self,
        coordinator: DataSourceRequestCoordinator,
        runtime_manager: DataSourceRuntimeManager,
    ) -> None:
        if not isinstance(coordinator, DataSourceRequestCoordinator):
            raise TypeError("coordinator 必须是 DataSourceRequestCoordinator")
        if not isinstance(runtime_manager, DataSourceRuntimeManager):
            raise TypeError("runtime_manager 必须是 DataSourceRuntimeManager")
        self._coordinator = coordinator
        self._runtime_manager = runtime_manager

    async def handle_stream(
        self,
        request: ChatRequest,
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        conversation_id = request.conversation_id
        if conversation_id is None:
            raise ValueError("conversation_id 必须显式提供")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id 必须是非空字符串")

        context = self._coordinator.resolve(
            conversation_id,
            request.metadata,
        )
        runtime = self._runtime_manager.require(context.source_id)
        handler = ChatHandler(runtime.agent)

        async for chunk in handler.handle_stream(request):
            yield chunk

    async def handle_poll(self, request: ChatRequest) -> ChatResponse:
        chunks = []
        async for chunk in self.handle_stream(request):
            chunks.append(chunk)
        return ChatResponse.from_chunks(chunks)

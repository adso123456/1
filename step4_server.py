"""启动支持显式数据源路由的 Vanna FastAPI 服务。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.data_source_chat_handler import DataSourceChatHandler
from backend.data_source_registry import (
    DataSourceRegistry,
    build_current_data_source_registry,
)
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.postgresql_runtime_factory import create_postgresql_runtime
from fastapi import FastAPI
from vanna.servers.fastapi.app import VannaFastAPIServer


@dataclass(frozen=True)
class ApplicationResources:
    registry: DataSourceRegistry
    coordinator: DataSourceRequestCoordinator
    runtime_manager: DataSourceRuntimeManager


class DataSourceVannaFastAPIServer(VannaFastAPIServer):
    """复用 Vanna 路由和 SSE 格式，但由请求动态选择 Agent。"""

    def __init__(
        self,
        resources: ApplicationResources,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.resources = resources
        self.chat_handler = DataSourceChatHandler(
            resources.coordinator,
            resources.runtime_manager,
        )

    def create_app(self) -> FastAPI:
        app = super().create_app()

        @app.get("/api/data-sources")
        async def list_data_sources() -> list[dict[str, str]]:
            return [
                {
                    "source_id": source_id,
                    "database_type": self.resources.registry.require(
                        source_id
                    ).database_type,
                }
                for source_id in self.resources.registry.source_ids
            ]

        return app


def create_application_resources(
    *,
    environ: Mapping[str, str] | None = None,
) -> ApplicationResources:
    registry = build_current_data_source_registry(environ=environ)
    coordinator = DataSourceRequestCoordinator(registry)
    runtime_manager = DataSourceRuntimeManager(
        registry,
        {"postgresql": create_postgresql_runtime},
    )
    return ApplicationResources(
        registry=registry,
        coordinator=coordinator,
        runtime_manager=runtime_manager,
    )


def create_server(
    resources: ApplicationResources | None = None,
) -> DataSourceVannaFastAPIServer:
    return DataSourceVannaFastAPIServer(
        resources or create_application_resources()
    )


def main() -> None:
    server = create_server()
    server.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

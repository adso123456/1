"""Vanna 服务启动适配。"""

from typing import Any

from vanna.servers.fastapi.app import VannaFastAPIServer


def run_vanna_server(agent: Any, *, host: str, port: int) -> None:
    """使用现有配置启动 Vanna FastAPI 服务。"""
    server = VannaFastAPIServer(agent)
    server.run(host=host, port=port)

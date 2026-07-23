"""启动 Vanna FastAPI 服务。"""

from backend.agent_factory import create_agent
from vanna.servers.fastapi.app import VannaFastAPIServer


def main():
    agent = create_agent()
    server = VannaFastAPIServer(agent)
    server.run(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

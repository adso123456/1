"""启动 Vanna FastAPI 服务。"""

from backend.agent_factory import create_agent
from integrations.vanna_adapter import run_vanna_server


def main():
    agent = create_agent()
    run_vanna_server(agent, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

"""仅供内网反向代理部署：让后端只监听本机回环地址。"""

import os
from pathlib import Path

from dotenv import load_dotenv

from step4_server import create_server


PROJECT_ROOT = Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    create_server().run(
        host="127.0.0.1",
        port=int(os.getenv("VANNA_SERVER_PORT", "8000")),
    )

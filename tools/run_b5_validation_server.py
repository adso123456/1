"""在隔离端口启动 B5 验证服务。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from step4_server import create_server


if __name__ == "__main__":
    uvicorn.run(
        create_server().create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("B5_VALIDATION_PORT", "8001")),
        log_level="warning",
    )

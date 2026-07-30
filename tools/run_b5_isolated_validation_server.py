"""启动使用临时目录的 B5 真实验收服务；不输出或持久化数据库凭据。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mysql_container_environment() -> dict[str, str]:
    completed = subprocess.run(
        ["docker", "inspect", "mysql"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    return dict(
        entry.split("=", 1)
        for entry in payload[0]["Config"]["Env"]
        if "=" in entry
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    runtime_root = args.runtime_root.expanduser().resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)

    os.environ["DATA_SOURCE_CATALOG_PATH"] = str(
        runtime_root / "catalog.sqlite3"
    )
    os.environ["WATER_AGENT_SYSTEM_DB_PATH"] = str(
        runtime_root / "assistant.sqlite3"
    )
    key_path = runtime_root / "credential.key"
    if not key_path.exists():
        key_path.write_text(
            Fernet.generate_key().decode("ascii"),
            encoding="ascii",
        )
    os.environ["DATA_SOURCE_CREDENTIAL_KEY"] = key_path.read_text(
        encoding="ascii"
    ).strip()
    mysql = _mysql_container_environment()
    os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
    os.environ.setdefault("MYSQL_PORT", "3307")
    os.environ.setdefault(
        "MYSQL_DATABASE", mysql.get("MYSQL_DATABASE", "lzh_monitor")
    )
    os.environ.setdefault("MYSQL_USER", "root")
    os.environ.setdefault("MYSQL_PASSWORD", mysql["MYSQL_ROOT_PASSWORD"])

    from step4_server import create_server

    create_server().run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

"""两个内置副本资产切换远程端点时的启动隔离测试。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_claim_identity import load_builtin_asset_lineage


def bootstrap(root: Path, host: str, port: int) -> list[dict]:
    metadata_path = root / "mysql-metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "schema": "lzh_monitor",
                    "table": "water_data",
                    "column": "monitor_time",
                    "type": "datetime",
                    "nullable": False,
                    "primary_key": True,
                    "ordinal_position": 1,
                    "indexes": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory_path = root / "mysql-memory"
    memory_path.mkdir()
    return [
        {
            "source_id": "mysql-lzh-monitor",
            "display_name": "MS数据",
            "description": "",
            "database_type": "mysql",
            "host": host,
            "port": port,
            "database_name": "lzh_monitor",
            "schema_name": "",
            "connect_timeout": 10,
            "credential_reference": {
                "username": "MYSQL_USER",
                "password": "MYSQL_PASSWORD",
            },
            "metadata_path": metadata_path,
            "memory_path": memory_path,
            "selected_tables_count": 1,
            "selected_columns_count": 1,
            "routing_summary": "",
            "capabilities": [],
        }
    ]


def main() -> int:
    lineage = load_builtin_asset_lineage()
    with tempfile.TemporaryDirectory(prefix="claim-gate-local-") as directory:
        root = Path(directory)
        catalog = DataSourceCatalog(root / "catalog.sqlite3", environ={})
        catalog.initialize(bootstrap(root, "host.docker.internal", 3307))
        catalog.initialize_builtin_claims(lineage)
        local = catalog.require("mysql-lzh-monitor")
        summary = catalog.builtin_claim_summary("mysql-lzh-monitor")
        assert local.status == "ready" and local.enabled_for_chat is True
        assert summary and summary["status"] == "not_required"

    with tempfile.TemporaryDirectory(prefix="claim-gate-remote-") as directory:
        root = Path(directory)
        catalog = DataSourceCatalog(root / "catalog.sqlite3", environ={})
        catalog.initialize(bootstrap(root, "192.168.250.73", 3306))
        catalog.initialize_builtin_claims(lineage)
        remote = catalog.require("mysql-lzh-monitor")
        summary = catalog.builtin_claim_summary("mysql-lzh-monitor")
        assert remote.status == "disabled" and remote.enabled_for_chat is False
        assert remote.last_error == "远程本尊尚未完成资产认领"
        assert summary and summary["status"] == "claim_required"
    print("builtin claim gate tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

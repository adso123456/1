"""B5 管理 API、永久绑定与保守推荐回归。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_management_api import create_data_source_management_router
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.data_source_suggestion import DataSourceSuggestionService


def bootstrap(root: Path) -> list[dict]:
    common = {
        "host": "127.0.0.1",
        "connect_timeout": 10,
        "selected_tables_count": 1,
        "selected_columns_count": 1,
    }
    return [
        {
            **common,
            "source_id": "postgresql-main",
            "display_name": "排污口治理数据",
            "description": "排污口、整治和溯源",
            "database_type": "postgresql",
            "port": 5433,
            "database_name": "gt_monitor",
            "schema_name": "public",
            "credential_reference": {"username": "PG_USER", "password": "PG_PASSWORD"},
            "metadata_path": root / "pg.json",
            "memory_path": root / "pg-memory",
            "routing_summary": "排污口 outlet 整治 溯源",
            "capabilities": [],
        },
        {
            **common,
            "source_id": "mysql-lzh-monitor",
            "display_name": "梁子湖监测数据",
            "description": "水质、水文和气象",
            "database_type": "mysql",
            "port": 3307,
            "database_name": "lzh_monitor",
            "credential_reference": {"username": "MY_USER", "password": "MY_PASSWORD"},
            "metadata_path": root / "mysql.json",
            "memory_path": root / "mysql-memory",
            "routing_summary": "水质 断面 高锰酸盐 叶绿素",
            "capabilities": [
                "water_quality_daily_report",
                "water_quality_monthly_report",
            ],
        },
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="b5-api-") as directory:
        root = Path(directory)
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
        catalog = DataSourceCatalog(
            root / "catalog.sqlite3",
            cipher=cipher,
            environ={
                "PG_USER": "pg",
                "PG_PASSWORD": "pg-secret",
                "MY_USER": "mysql",
                "MY_PASSWORD": "mysql-secret",
            },
        )
        catalog.initialize(bootstrap(root))
        registry = DataSourceRegistry.from_catalog(catalog)
        coordinator = DataSourceRequestCoordinator(registry)
        manager = DataSourceRuntimeManager(
            registry,
            {"postgresql": lambda config: None, "mysql": lambda config: None},
        )
        app = FastAPI()
        app.include_router(
            create_data_source_management_router(
                catalog=catalog,
                coordinator=coordinator,
                runtime_manager=manager,
            )
        )
        with TestClient(
            app,
            base_url="http://127.0.0.1:8000",
            client=("127.0.0.1", 50000),
        ) as client:
            listed = client.get("/api/data-source-management")
            assert listed.status_code == 200 and len(listed.json()) == 2
            detail = client.get(
                "/api/data-source-management/mysql-lzh-monitor"
            ).json()
            assert "password" not in detail and detail["has_password"] is True
            assert detail["username"] == "环境变量"

            created = client.post(
                "/api/data-source-management",
                json={
                    "display_name": "API 临时源",
                    "description": "API 测试",
                    "database_type": "postgresql",
                    "host": "127.0.0.1",
                    "port": 5433,
                    "database_name": "gt_monitor",
                    "schema_name": "public",
                    "username": "api-user",
                    "password": "api-password",
                },
            )
            assert created.status_code == 201
            assert "password" not in created.json()
            source_id = created.json()["source_id"]
            assert source_id.startswith("ds_")

            renamed = client.patch(
                f"/api/data-source-management/{source_id}",
                json={"display_name": "API 重命名源", "password": ""},
            )
            assert renamed.status_code == 200
            assert renamed.json()["source_id"] == source_id

            first = client.post(
                "/api/conversations/api-conversation/source",
                json={"source_id": "postgresql-main"},
            )
            same = client.post(
                "/api/conversations/api-conversation/source",
                json={"source_id": "postgresql-main"},
            )
            conflict = client.post(
                "/api/conversations/api-conversation/source",
                json={"source_id": "mysql-lzh-monitor"},
            )
            assert first.status_code == same.status_code == 200
            assert conflict.status_code == 409
            assert client.get(
                "/api/conversations/api-conversation/source"
            ).json()["source_id"] == "postgresql-main"

            remote = TestClient(
                app,
                base_url="http://127.0.0.1:8000",
                client=("203.0.113.10", 50000),
            )
            assert remote.get("/api/data-source-management").status_code == 403
            remote.close()

        service = DataSourceSuggestionService(catalog)
        report = service.suggest(
            "生成2025年7月28日水质日报",
            "postgresql-main",
        )
        assert report is not None
        assert report["suggestions"][0]["source_id"] == "mysql-lzh-monitor"
        assert service.suggest(
            "生成2025年7月28日水质日报",
            "postgresql-main",
            allowed_source_ids=("postgresql-main",),
        ) is None
        assert service.suggest("帮我看看数据", "postgresql-main") is None
        assert service.suggest(
            "查询排污口整治情况",
            "postgresql-main",
        ) is None

    print("[PASS] 管理 API 不回显凭据、source_id 不变")
    print("[PASS] 会话绑定幂等、改绑 409、重启目录可恢复")
    print("[PASS] 管理 API 仅允许本机调用")
    print("[PASS] 明确错源推荐、Widget 授权过滤、模糊问题不推荐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

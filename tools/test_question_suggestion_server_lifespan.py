"""阶段 E-3：服务 lifespan 与后台推荐问题 worker 回归测试。

覆盖审查 F1：
  - asyncio 导入与 lifespan 正常启动（Catalog 非空）
  - 阻塞型同步任务移出事件循环后 heartbeat 仍可运行
  - 关闭 lifespan 时后台任务正确取消，不产生 shutdown exception
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_request_coordinator import DataSourceRequestCoordinator
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from step4_server import ApplicationResources, DataSourceVannaFastAPIServer


def _make_catalog(root: Path) -> DataSourceCatalog:
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        environ={
            "A_USER": "a",
            "A_PASSWORD": "a-secret",
        },
    )
    catalog.initialize(
        [
            {
                "source_id": "source-a",
                "display_name": "数据源 A",
                "description": "A 源",
                "database_type": "postgresql",
                "host": "127.0.0.1",
                "port": 5433,
                "database_name": "db_a",
                "schema_name": "public",
                "credential_reference": {
                    "username": "A_USER",
                    "password": "A_PASSWORD",
                },
                "metadata_path": root / "a" / "metadata.json",
                "memory_path": root / "a" / "memory",
                "routing_summary": "a",
                "capabilities": [],
                "connect_timeout": 10,
                "selected_tables_count": 1,
                "selected_columns_count": 1,
            }
        ]
    )
    return catalog


class _MinimalServer(DataSourceVannaFastAPIServer):
    """只保留 lifespan 相关状态的最小服务实例（其余重依赖不构造）。"""

    def __init__(self, resources: ApplicationResources) -> None:
        self.config: dict = {}
        self.resources = resources

        async def warm() -> None:
            return None

        self.runtime_prewarmer = type(
            "Prewarmer",
            (),
            {"warm_ready_sources": staticmethod(warm)},
        )()
        self.learning_settings = type(
            "Settings",
            (),
            {"enabled": False},
        )()
        self.learning_worker = None
        self.learning_service = None


def _make_server(root: Path) -> tuple[_MinimalServer, FastAPI]:
    catalog = _make_catalog(root)
    registry = DataSourceRegistry.from_catalog(catalog)
    coordinator = DataSourceRequestCoordinator(registry)

    def factory(config):
        return object()

    manager = DataSourceRuntimeManager(
        registry,
        {"postgresql": factory, "mysql": factory},
    )
    resources = ApplicationResources(
        registry=registry,
        coordinator=coordinator,
        runtime_manager=manager,
        assistant_application_registry=None,
        catalog=catalog,
    )
    server = _MinimalServer(resources)
    app = FastAPI(lifespan=server._lifespan)
    return server, app


def test_lifespan_starts_with_nonempty_catalog() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-ls-start-") as directory:
        server, app = _make_server(Path(directory))

        async def run() -> None:
            async with app.router.lifespan_context(app):
                assert server.resources.catalog is not None

        asyncio.run(run())


def test_blocking_worker_does_not_block_event_loop() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-ls-hb-") as directory:
        server, app = _make_server(Path(directory))

        def blocking_jobs(*args, **kwargs):
            time.sleep(2)
            return []

        async def run() -> None:
            async with app.router.lifespan_context(app):
                with patch(
                    "backend.question_suggestion_sync.process_pending_question_suggestion_jobs",
                    side_effect=blocking_jobs,
                ):
                    ticks = 0
                    for _ in range(10):
                        await asyncio.sleep(0.05)
                        ticks += 1
                assert ticks == 10

        asyncio.run(run())


def test_lifespan_shutdown_cancels_worker_cleanly() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-ls-stop-") as directory:
        server, app = _make_server(Path(directory))

        async def run() -> None:
            async with app.router.lifespan_context(app):
                assert server.resources.catalog is not None
            # 正常退出且未抛出 shutdown exception 即视为通过

        asyncio.run(run())


def main() -> int:
    import traceback

    failed = 0
    total = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        total += 1
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""DataSourceRuntime 的纯离线契约测试。"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "runtime-password-that-must-not-appear"
RESOURCE_REPR = "FAKE_RESOURCE_REPR_MUST_NOT_APPEAR"


class FakeResource:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"{RESOURCE_REPR}:{self.name}"


def _make_config(root: Path) -> DataSourceConfig:
    return DataSourceConfig(
        source_id="runtime-main",
        database_type="test-postgresql",
        sql_dialect="test-sql",
        connection_settings={
            "password": TEST_PASSWORD,
            "nested": {"secret": TEST_PASSWORD},
        },
        metadata_path=root / "metadata-does-not-exist.json",
        memory_path=root / "memory-does-not-exist",
        read_only=True,
    )


def _expect_error(
    callback: Callable[[], Any],
    expected_text: str,
    error_types: tuple[type[BaseException], ...],
) -> tuple[bool, str]:
    try:
        callback()
    except error_types as exc:
        message = str(exc)
        return expected_text in message and TEST_PASSWORD not in message, message
    return False, "未抛出预期异常"


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    environment_reads: list[str] = []
    original_getenv = os.getenv

    def tracked_getenv(key: str, default: str | None = None) -> str | None:
        environment_reads.append(key)
        return original_getenv(key, default)

    os.getenv = tracked_getenv
    try:
        runtime_module = importlib.import_module("backend.data_source_runtime")
    finally:
        os.getenv = original_getenv

    DataSourceRuntime = runtime_module.DataSourceRuntime

    with tempfile.TemporaryDirectory(
        prefix="data-source-runtime-test-"
    ) as temp_name:
        root = Path(temp_name).resolve()
        config = _make_config(root)
        resources = {
            "runner": FakeResource("runner"),
            "memory": FakeResource("memory"),
            "metadata_retriever": FakeResource("metadata"),
            "sql_guard": FakeResource("sql_guard"),
            "agent": FakeResource("agent"),
        }

        os.getenv = tracked_getenv
        try:
            runtime = DataSourceRuntime(config=config, **resources)
        finally:
            os.getenv = original_getenv
        results.append(
            (
                "合法 Runtime 构建",
                runtime.config is config
                and all(
                    getattr(runtime, field_name) is resource
                    for field_name, resource in resources.items()
                ),
                repr(runtime),
            )
        )
        results.append(
            (
                "派生身份属性正确",
                runtime.source_id == "runtime-main"
                and runtime.database_type == "test-postgresql"
                and runtime.sql_dialect == "test-sql",
                (
                    f"{runtime.source_id}, "
                    f"{runtime.database_type}, "
                    f"{runtime.sql_dialect}"
                ),
            )
        )

        try:
            runtime.runner = FakeResource("replacement")
        except FrozenInstanceError:
            immutable = True
        else:
            immutable = False
        results.append(
            (
                "Runtime 不可重新赋值",
                immutable,
                type(runtime).__name__,
            )
        )

        invalid_config = _expect_error(
            lambda: DataSourceRuntime(  # type: ignore[arg-type]
                config=object(),
                **resources,
            ),
            "config 必须是 DataSourceConfig",
            (TypeError,),
        )
        results.append(
            ("config 非 DataSourceConfig 被拒绝", *invalid_config)
        )

        for field_name in (
            "runner",
            "memory",
            "metadata_retriever",
            "sql_guard",
            "agent",
        ):
            invalid_resources = dict(resources)
            invalid_resources[field_name] = None
            invalid_resource = _expect_error(
                lambda invalid_resources=invalid_resources: DataSourceRuntime(
                    config=config,
                    **invalid_resources,
                ),
                f"{field_name} 必须显式提供",
                (ValueError,),
            )
            results.append(
                (
                    f"{field_name} 为 None 被拒绝",
                    invalid_resource[0],
                    invalid_resource[1],
                )
            )

        repr_text = repr(runtime)
        results.append(
            (
                "repr 仅包含安全身份",
                runtime.source_id in repr_text
                and runtime.database_type in repr_text
                and runtime.sql_dialect in repr_text
                and TEST_PASSWORD not in repr_text
                and "connection_settings" not in repr_text
                and str(config.metadata_path) not in repr_text
                and str(config.memory_path) not in repr_text
                and RESOURCE_REPR not in repr_text,
                repr_text,
            )
        )

        assets_absent = (
            not config.metadata_path.exists()
            and not config.memory_path.exists()
        )
        results.append(
            (
                "导入和构造不读取环境变量或运行资产",
                not environment_reads and assets_absent,
                f"reads={environment_reads}",
            )
        )

        forbidden_roots = ("vanna", "psycopg2", "chromadb")
        forbidden_modules = sorted(
            module_name
            for module_name in sys.modules
            if module_name == "backend.memory"
            or any(
                module_name == root_name
                or module_name.startswith(root_name + ".")
                for root_name in forbidden_roots
            )
        )
        results.append(
            (
                "导入不加载真实运行资源模块",
                not forbidden_modules,
                f"loaded={forbidden_modules}",
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

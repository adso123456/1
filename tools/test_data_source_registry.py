"""DataSourceRegistry 的纯离线契约测试。"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.data_source_config import DataSourceConfig


TEST_PASSWORD = "registry-password-that-must-not-appear"
BASE_CONNECTION_SETTINGS = {
    "host": "offline.invalid",
    "port": 5433,
    "database": "offline_database",
    "user": "offline_user",
    "password": TEST_PASSWORD,
    "connect_timeout": 10,
}


def _make_config(root: Path, source_id: str) -> DataSourceConfig:
    return DataSourceConfig(
        source_id=source_id,
        database_type="postgresql",
        sql_dialect="postgresql",
        connection_settings=dict(BASE_CONNECTION_SETTINGS),
        metadata_path=root / f"{source_id}-metadata.json",
        memory_path=root / f"{source_id}-memory",
        read_only=True,
    )


def _expect_error(
    callback: Callable[[], Any],
    expected_text: str,
    error_types: tuple[type[BaseException], ...] = (ValueError,),
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
        registry_module = importlib.import_module(
            "backend.data_source_registry"
        )
    finally:
        os.getenv = original_getenv

    DataSourceRegistry = registry_module.DataSourceRegistry
    build_current_data_source_registry = (
        registry_module.build_current_data_source_registry
    )
    results.append(
        (
            "Registry 首次导入不读取环境变量",
            not environment_reads,
            f"reads={environment_reads}",
        )
    )
    results.append(
        (
            "Registry 首次导入不加载 config.data_sources",
            "config.data_sources" not in sys.modules,
            str("config.data_sources" in sys.modules),
        )
    )

    with tempfile.TemporaryDirectory(prefix="data-source-registry-test-") as temp_name:
        root = Path(temp_name).resolve()
        first = _make_config(root, "postgresql-main")
        second = _make_config(root, "postgresql-archive")

        single = DataSourceRegistry([first])
        results.append(
            (
                "单配置注册和显式获取",
                single.require("postgresql-main") is first,
                repr(single),
            )
        )

        multiple = DataSourceRegistry([first, second])
        results.append(
            (
                "两个 source_id 独立获取",
                multiple.require("postgresql-main") is first
                and multiple.require("postgresql-archive") is second,
                repr(multiple),
            )
        )

        duplicate = _expect_error(
            lambda: DataSourceRegistry([first, first]),
            "重复 source_id",
        )
        results.append(("重复 source_id 被拒绝", duplicate[0], duplicate[1]))

        empty = _expect_error(
            lambda: DataSourceRegistry([]),
            "至少需要一个",
        )
        results.append(("空 Registry 被拒绝", empty[0], empty[1]))

        invalid_require_checks = [
            _expect_error(
                lambda source_id=source_id: single.require(source_id),  # type: ignore[arg-type]
                expected,
            )[0]
            for source_id, expected in (
                (None, "显式提供"),
                ("", "非空字符串"),
                ("   ", "非空字符串"),
            )
        ]
        results.append(
            (
                "None、空和空白 source_id 被拒绝",
                all(invalid_require_checks),
                f"checks={len(invalid_require_checks)}",
            )
        )

        unknown = _expect_error(
            lambda: single.require("unknown-source"),
            "未知 source_id",
        )
        results.append(("未知 source_id 被拒绝", unknown[0], unknown[1]))

        no_default = _expect_error(
            lambda: single.require(),  # type: ignore[call-arg]
            "required positional argument",
            (TypeError,),
        )
        results.append(
            ("单数据源也无隐式默认解析", no_default[0], no_default[1])
        )

        try:
            single.configs["other"] = first  # type: ignore[index]
        except TypeError:
            mapping_immutable = True
        else:
            mapping_immutable = False
        results.append(
            (
                "Registry 映射不可修改",
                mapping_immutable,
                type(single.configs).__name__,
            )
        )

        original_list = [first]
        list_registry = DataSourceRegistry(original_list)
        original_list.append(second)
        original_mapping = {"postgresql-main": first}
        mapping_registry = DataSourceRegistry(original_mapping)
        original_mapping.clear()
        results.append(
            (
                "修改原始列表或映射不影响 Registry",
                list_registry.source_ids == ("postgresql-main",)
                and mapping_registry.source_ids == ("postgresql-main",),
                f"list={list_registry.source_ids}, mapping={mapping_registry.source_ids}",
            )
        )

        results.append(
            (
                "source_ids 顺序确定",
                multiple.source_ids == (
                    "postgresql-archive",
                    "postgresql-main",
                ),
                str(multiple.source_ids),
            )
        )

        scope_path = root / "postgresql_metadata_scope.json"
        scope_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "datasource_id": "postgresql-main",
                    "dialect": "postgresql",
                    "schema": "public",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        metadata_path = root / "metadata-does-not-exist.json"
        memory_path = root / "memory-does-not-exist"
        environ = {
            "DB_HOST": "offline.invalid",
            "DB_PORT": "5433",
            "DB_NAME": "offline_database",
            "DB_USER": "offline_user",
            "DB_PASSWORD": TEST_PASSWORD,
            "DB_CONNECT_TIMEOUT": "10",
            "DB_STATEMENT_TIMEOUT_MS": "30000",
            "DB_LOCK_TIMEOUT_MS": "5000",
            "METADATA_INDEX_PATH": str(metadata_path),
            "VANNA_DATA_DIR": str(memory_path),
        }
        current = build_current_data_source_registry(
            environ=environ,
            scope_path=scope_path,
        )
        current_config = current.require("postgresql-main")
        results.append(
            (
                "当前 PostgreSQL 配置构建 Registry",
                current.source_ids == ("postgresql-main",)
                and current_config.metadata_path == metadata_path
                and current_config.memory_path == memory_path,
                repr(current),
            )
        )

        password_hidden = (
            TEST_PASSWORD not in repr(current)
            and TEST_PASSWORD not in repr(current.configs)
        )
        results.append(
            (
                "repr 和异常不泄露密码",
                password_hidden,
                repr(current),
            )
        )

        forbidden_modules = ("psycopg2", "chromadb", "backend.memory")
        forbidden_loaded = sorted(
            module_name
            for module_name in sys.modules
            if any(
                module_name == forbidden
                or module_name.startswith(forbidden + ".")
                for forbidden in forbidden_modules
            )
        )
        no_asset_access = (
            not metadata_path.exists()
            and not memory_path.exists()
            and not forbidden_loaded
        )
        results.append(
            (
                "导入和构建不访问数据库、Metadata 或 Chroma",
                no_asset_access,
                f"loaded={forbidden_loaded}",
            )
        )

    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    failed = sum(not passed for _, passed, _ in results)
    print(f"total={len(results)} passed={len(results) - failed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

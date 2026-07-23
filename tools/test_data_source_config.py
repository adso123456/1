"""DataSourceConfig 与当前 PostgreSQL 配置构建入口的纯离线测试。"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_config import DataSourceConfig
from config.data_sources import build_postgresql_data_source_config


TEST_PASSWORD = "offline-password-that-must-not-appear"
BASE_CONNECTION_SETTINGS = {
    "host": "offline.invalid",
    "port": 5433,
    "database": "offline_database",
    "user": "offline_user",
    "password": TEST_PASSWORD,
    "connect_timeout": 10,
    "application_name": "vanna-water-agent",
    "options": "-c default_transaction_read_only=on",
}


def _expect_value_error(
    callback: Callable[[], Any], expected_text: str
) -> tuple[bool, str]:
    try:
        callback()
    except ValueError as exc:
        message = str(exc)
        return expected_text in message and TEST_PASSWORD not in message, message
    return False, "未抛出 ValueError"


def _make_config(
    root: Path,
    **overrides: Any,
) -> DataSourceConfig:
    values: dict[str, Any] = {
        "source_id": "postgresql-main",
        "database_type": "postgresql",
        "sql_dialect": "postgresql",
        "connection_settings": dict(BASE_CONNECTION_SETTINGS),
        "metadata_path": root / "metadata.json",
        "memory_path": root / "memory",
        "read_only": True,
    }
    values.update(overrides)
    return DataSourceConfig(**values)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="data-source-config-test-") as temp_name:
        root = Path(temp_name).resolve()
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

        config = build_postgresql_data_source_config(
            environ=environ,
            scope_path=scope_path,
        )
        results.append(
            (
                "正常 PostgreSQL 配置构建成功",
                config.source_id == "postgresql-main"
                and config.database_type == "postgresql"
                and config.sql_dialect == "postgresql"
                and config.metadata_path == metadata_path
                and config.memory_path == memory_path
                and config.read_only is True,
                repr(config),
            )
        )

        try:
            config.source_id = "changed"  # type: ignore[misc]
        except FrozenInstanceError:
            frozen = True
        else:
            frozen = False
        results.append(("配置对象不可重新赋值", frozen, type(config).__name__))

        original_connection = dict(BASE_CONNECTION_SETTINGS)
        snapshot_config = _make_config(
            root, connection_settings=original_connection
        )
        original_connection["host"] = "changed.invalid"
        snapshot_unchanged = (
            snapshot_config.connection_settings["host"] == "offline.invalid"
        )
        try:
            snapshot_config.connection_settings["host"] = "changed.invalid"  # type: ignore[index]
        except TypeError:
            mapping_immutable = True
        else:
            mapping_immutable = False
        results.append(
            (
                "连接配置是不可变快照",
                snapshot_unchanged and mapping_immutable,
                str(snapshot_config.connection_settings["host"]),
            )
        )
        results.append(
            (
                "repr 不包含密码",
                TEST_PASSWORD not in repr(snapshot_config),
                repr(snapshot_config),
            )
        )

        invalid_source_checks = [
            _expect_value_error(
                lambda source_id=source_id: _make_config(
                    root, source_id=source_id
                ),
                expected,
            )[0]
            for source_id, expected in (
                ("", "source_id"),
                ("PostgreSQL", "小写"),
                ("postgresql main", "符合"),
            )
        ]
        results.append(
            (
                "非法 source_id 被拒绝",
                all(invalid_source_checks),
                f"checks={len(invalid_source_checks)}",
            )
        )

        relative_metadata = _expect_value_error(
            lambda: _make_config(root, metadata_path=Path("metadata.json")),
            "metadata_path 必须是绝对路径",
        )
        results.append(
            ("相对 Metadata 路径被拒绝", relative_metadata[0], relative_metadata[1])
        )

        relative_memory = _expect_value_error(
            lambda: _make_config(root, memory_path=Path("memory")),
            "memory_path 必须是绝对路径",
        )
        results.append(
            ("相对 Memory 路径被拒绝", relative_memory[0], relative_memory[1])
        )

        same_path = root / "same"
        same_path_result = _expect_value_error(
            lambda: _make_config(
                root, metadata_path=same_path, memory_path=same_path
            ),
            "不能相同",
        )
        results.append(
            (
                "Metadata 与 Memory 路径相同被拒绝",
                same_path_result[0],
                same_path_result[1],
            )
        )

        writable_result = _expect_value_error(
            lambda: _make_config(root, read_only=False),
            "read_only=True",
        )
        results.append(
            ("read_only=False 被拒绝", writable_result[0], writable_result[1])
        )

        incomplete_connection = dict(BASE_CONNECTION_SETTINGS)
        incomplete_connection.pop("user")
        missing_result = _expect_value_error(
            lambda: _make_config(
                root, connection_settings=incomplete_connection
            ),
            "user",
        )
        results.append(
            (
                "缺失 PostgreSQL 连接字段被拒绝且不泄密",
                missing_result[0],
                missing_result[1],
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
                "构建不连接数据库、不读取 Metadata、不打开 Chroma",
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

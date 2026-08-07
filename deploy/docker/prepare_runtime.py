"""显式 legacy 路径迁移工具（仅 WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION=1 时调用）。

从环境变量读取配置，在单个 SQLite 事务内把 Catalog 中的项目资产路径
规范为相对路径；缺少必要配置时安全跳过；任何失败整体回滚。
绝不输出密码、连接串、credential key 或环境变量全集。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_ENV_KEYS = (
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "DATA_SOURCE_CATALOG_PATH",
    "METADATA_INDEX_PATH",
    "VANNA_DATA_DIR",
    "MYSQL_METADATA_INDEX_PATH",
    "MYSQL_VANNA_DATA_DIR",
)
PATH_COLUMNS = (
    "candidate_root",
    "candidate_memory",
    "published_memory_path",
    "backup_paths_json",
    "snapshot_json",
    "asset_plan_json",
    "backed_up_assets_json",
    "installed_assets_json",
)


def _env_config() -> dict[str, str]:
    return {
        key: os.environ.get(key, "").strip()
        for key in REQUIRED_ENV_KEYS
    }


def _portable_path(value: str) -> str:
    """把 Windows / 旧 /app 项目路径收敛成 /opt/water-agent 相对路径。"""
    normalized = value.replace("\\", "/")
    lowered = normalized.lower()
    for marker in ("/posgresql/1/", "/app/", "/opt/water-agent/"):
        index = lowered.find(marker)
        if index >= 0:
            return PurePosixPath(normalized[index + len(marker) :]).as_posix()
    path = Path(value).expanduser()
    if not path.is_absolute():
        return PurePosixPath(normalized).as_posix()
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return value


def _rewrite_json_paths(value: Any) -> Any:
    if isinstance(value, list):
        return [_rewrite_json_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_json_paths(item) for key, item in value.items()}
    if isinstance(value, str):
        return _portable_path(value)
    return value


def _rewrite_runtime_tables(connection: sqlite3.Connection) -> None:
    for rowid, *values in connection.execute(
        f"SELECT rowid, {', '.join(PATH_COLUMNS)} FROM active_asset_batches"
    ):
        updated: list[str] = []
        for column, value in zip(PATH_COLUMNS, values):
            if column.endswith("_json"):
                updated.append(
                    json.dumps(
                        _rewrite_json_paths(json.loads(value)),
                        ensure_ascii=False,
                    )
                )
            else:
                updated.append(_portable_path(value))
        assignments = ", ".join(f"{column} = ?" for column in PATH_COLUMNS)
        connection.execute(
            f"UPDATE active_asset_batches SET {assignments} WHERE rowid = ?",
            (*updated, rowid),
        )
    for rowid, value in connection.execute(
        "SELECT rowid, path FROM pending_asset_cleanup"
    ):
        connection.execute(
            "UPDATE pending_asset_cleanup SET path = ? WHERE rowid = ?",
            (_portable_path(value), rowid),
        )


def main() -> int:
    if os.environ.get("WATER_AGENT_ENABLE_LEGACY_PATH_MIGRATION") != "1":
        print("Legacy path migration skipped: not enabled")
        return 0
    config = _env_config()
    missing = [key for key in REQUIRED_ENV_KEYS if not config[key]]
    if missing:
        # 安全跳过：不输出缺失字段之外的任何信息，不做任何 Catalog 修改。
        print("Legacy path migration skipped: 缺少必要迁移配置")
        return 0
    try:
        catalog_path = Path(config["DATA_SOURCE_CATALOG_PATH"]).expanduser().resolve()
        # 迁移前完整校验：端口必须是整数，Catalog 必须存在。
        int(config["DB_PORT"])
        int(config["MYSQL_PORT"])
    except ValueError:
        print("Legacy path migration skipped: 端口配置无效")
        return 0
    if not catalog_path.is_file():
        print("Legacy path migration skipped: catalog 不存在")
        return 0

    connection = sqlite3.connect(catalog_path)
    try:
        connection.execute("BEGIN")
        connection.execute(
            """
            UPDATE data_sources
            SET host = ?, port = ?, database_name = ?,
                metadata_path = ?, memory_path = ?
            WHERE source_id = 'postgresql-main'
            """,
            (
                config["DB_HOST"],
                int(config["DB_PORT"]),
                config["DB_NAME"],
                _portable_path(config["METADATA_INDEX_PATH"]),
                _portable_path(config["VANNA_DATA_DIR"]),
            ),
        )
        connection.execute(
            """
            UPDATE data_sources
            SET host = ?, port = ?, database_name = ?,
                metadata_path = ?, memory_path = ?
            WHERE source_id = 'mysql-lzh-monitor'
            """,
            (
                config["MYSQL_HOST"],
                int(config["MYSQL_PORT"]),
                config["MYSQL_DATABASE"],
                _portable_path(config["MYSQL_METADATA_INDEX_PATH"]),
                _portable_path(config["MYSQL_VANNA_DATA_DIR"]),
            ),
        )
        _rewrite_runtime_tables(connection)
        connection.execute("COMMIT")
    except Exception as exc:
        connection.rollback()
        print(
            "Legacy path migration failed and rolled back: "
            + type(exc).__name__
        )
        return 1
    finally:
        connection.close()

    print("Legacy path migration ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

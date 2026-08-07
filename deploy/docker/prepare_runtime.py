"""校验镜像配置，并将 Catalog 中的项目资产路径规范为相对路径。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
REQUIRED_KEYS = (
    "DEEPSEEK_API_KEY",
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
    "DATA_SOURCE_CREDENTIAL_KEY",
    "DATA_SOURCE_CATALOG_PATH",
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


def _required_config() -> dict[str, str]:
    if not ENV_PATH.is_file():
        raise RuntimeError(f"镜像缺少配置文件：{ENV_PATH}")
    values = {
        key: str(value)
        for key, value in dotenv_values(ENV_PATH).items()
        if value is not None
    }
    missing = [key for key in REQUIRED_KEYS if not values.get(key, "").strip()]
    if missing:
        raise RuntimeError(".env 缺少必需配置：" + ", ".join(missing))
    return values


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _portable_path(value: str) -> str:
    """把当前根目录、旧 /app 和 Windows 项目路径收敛成相对路径。"""
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


def main() -> None:
    if not ENV_PATH.is_file():
        # 镜像发布模式：不烘焙 .env（无数据库凭据），数据源由服务器前端配置。
        print("Docker runtime configuration skipped: no .env")
        return
    try:
        values = _required_config()
    except RuntimeError:
        # .env 可能由运行期自动写入（如凭据加密密钥），但缺少完整数据库配置时
        # 仍按镜像发布模式处理：跳过路径改写，不影响空数据源启动。
        print("Docker runtime configuration skipped: .env 缺少必需配置")
        return
    catalog_path = _resolve_project_path(values["DATA_SOURCE_CATALOG_PATH"])
    if not catalog_path.is_file():
        print(f"Docker runtime configuration skipped: no catalog {catalog_path}")
        return

    try:
        with sqlite3.connect(catalog_path) as connection:
            connection.execute(
                """
                UPDATE data_sources
                SET host = ?, port = ?, database_name = ?,
                    metadata_path = ?, memory_path = ?
                WHERE source_id = 'postgresql-main'
                """,
                (
                    values["DB_HOST"],
                    int(values["DB_PORT"]),
                    values["DB_NAME"],
                    _portable_path(values["METADATA_INDEX_PATH"]),
                    _portable_path(values["VANNA_DATA_DIR"]),
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
                    values["MYSQL_HOST"],
                    int(values["MYSQL_PORT"]),
                    values["MYSQL_DATABASE"],
                    _portable_path(values["MYSQL_METADATA_INDEX_PATH"]),
                    _portable_path(values["MYSQL_VANNA_DATA_DIR"]),
                ),
            )
            _rewrite_runtime_tables(connection)
    except sqlite3.OperationalError:
        # 空目录（无数据源表）时跳过路径重写，不影响镜像构建与空源启动。
        print("Docker runtime configuration skipped: catalog is empty")
        return

    print("Docker runtime configuration ready")


if __name__ == "__main__":
    main()

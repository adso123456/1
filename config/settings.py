"""运行时路径与数据库连接配置。"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

configured_path = os.getenv("VANNA_DATA_DIR", "").strip()
CHROMA_DIR = str(
    Path(configured_path).expanduser().resolve()
    if configured_path
    else (PROJECT_ROOT / "vanna_data").resolve()
)

# PostgreSQL 客户端连接安全默认值（数据库角色权限仍需由 PostgreSQL 管理）。
_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_DEFAULT_LOCK_TIMEOUT_MS = 5_000


def _positive_int(
    environ: Mapping[str, str], name: str, default: int | None = None
) -> int:
    raw_value = environ.get(name)
    if raw_value is None:
        if default is None:
            raise ValueError(f"缺少必需的环境变量 {name}")
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"环境变量 {name} 必须是正整数")
    return value


def build_db_kwargs(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """从受控环境变量构造 PostgresRunner 连接参数。"""
    source = os.environ if environ is None else environ
    port = _positive_int(source, "DB_PORT", 5433)
    connect_timeout = _positive_int(
        source, "DB_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT
    )
    statement_timeout_ms = _positive_int(
        source, "DB_STATEMENT_TIMEOUT_MS", _DEFAULT_STATEMENT_TIMEOUT_MS
    )
    lock_timeout_ms = _positive_int(
        source, "DB_LOCK_TIMEOUT_MS", _DEFAULT_LOCK_TIMEOUT_MS
    )

    options = " ".join(
        (
            "-c default_transaction_read_only=on",
            f"-c statement_timeout={statement_timeout_ms}",
            f"-c lock_timeout={lock_timeout_ms}",
        )
    )
    kwargs: dict[str, Any] = {
        "host": source.get("DB_HOST", "localhost"),
        "port": port,
        "database": source.get("DB_NAME", "gt_monitor"),
        "user": source.get("DB_USER"),
        "password": source.get("DB_PASSWORD"),
        "connect_timeout": connect_timeout,
        "application_name": "vanna-water-agent",
        "options": options,
    }

    sslmode = source.get("DB_SSLMODE")
    if sslmode is not None and sslmode.strip():
        kwargs["sslmode"] = sslmode.strip()
    return kwargs


def validate_db_config(config: Mapping[str, Any] | None = None) -> None:
    """在创建真实 PostgresRunner 前校验必需配置，不输出敏感值。"""
    db_config = DB_KWARGS if config is None else config
    missing = [
        env_name
        for key, env_name in (("user", "DB_USER"), ("password", "DB_PASSWORD"))
        if not isinstance(db_config.get(key), str) or not db_config[key].strip()
    ]
    if missing:
        raise ValueError("缺少必需的数据库环境变量：" + ", ".join(missing))

    for key, env_name in (
        ("port", "DB_PORT"),
        ("connect_timeout", "DB_CONNECT_TIMEOUT"),
    ):
        value = db_config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"环境变量 {env_name} 必须是正整数")


DB_KWARGS = build_db_kwargs()

"""MySQL TLS 配置校验与 PyMySQL 参数构造。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


MYSQL_TLS_MODES = frozenset(
    {"disabled", "required", "verify_ca", "verify_identity"}
)


class MySQLTLSConfigurationError(ValueError):
    """MySQL TLS 配置无效。"""


def build_mysql_tls_settings(
    *,
    mode: str,
    ca_path: str = "",
    cert_path: str = "",
    key_path: str = "",
) -> dict[str, Any]:
    """把目录中的 TLS 配置转换为唯一一套 PyMySQL 连接参数。"""
    normalized_mode = (mode or "disabled").strip().lower()
    if normalized_mode not in MYSQL_TLS_MODES:
        raise MySQLTLSConfigurationError(
            "MySQL TLS 模式必须是 disabled、required、verify_ca 或 verify_identity"
        )

    ca = ca_path.strip()
    cert = cert_path.strip()
    key = key_path.strip()
    if bool(cert) != bool(key):
        raise MySQLTLSConfigurationError("MySQL 客户端证书和私钥路径必须成对提供")
    if normalized_mode in {"verify_ca", "verify_identity"} and not ca:
        raise MySQLTLSConfigurationError("当前 MySQL TLS 模式必须提供 CA 文件路径")

    for label, value in (
        ("CA", ca),
        ("客户端证书", cert),
        ("客户端私钥", key),
    ):
        if value and not Path(value).expanduser().is_file():
            raise MySQLTLSConfigurationError(f"MySQL {label}文件不存在")

    if normalized_mode == "disabled":
        if ca or cert or key:
            raise MySQLTLSConfigurationError(
                "MySQL TLS 已禁用时不能配置证书路径"
            )
        return {}

    if normalized_mode == "required":
        settings: dict[str, Any] = {
            "ssl": {"check_hostname": False},
            "ssl_verify_cert": False,
            "ssl_verify_identity": False,
        }
    else:
        settings = {
            "ssl_ca": str(Path(ca).expanduser().resolve()),
            "ssl_verify_cert": True,
            "ssl_verify_identity": normalized_mode == "verify_identity",
        }
    if cert:
        settings["ssl_cert"] = str(Path(cert).expanduser().resolve())
        settings["ssl_key"] = str(Path(key).expanduser().resolve())
    return settings

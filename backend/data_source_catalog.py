"""动态数据源目录、加密凭据和会话绑定的 SQLite 事实源。"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from config.data_source_config import DataSourceConfig
from config.settings import PROJECT_ROOT


SCHEMA_COMPONENT = "data_source_catalog"
SCHEMA_VERSION = 1
VALID_DATABASE_TYPES = frozenset({"mysql", "postgresql"})
VALID_STATUSES = frozenset(
    {
        "draft",
        "connected",
        "metadata_ready",
        "training_required",
        "ready",
        "disabled",
        "error",
    }
)
CONNECTION_MODES = frozenset({"direct_database", "external_provider"})


class DataSourceCatalogError(RuntimeError):
    """目录操作可安全展示的业务异常。"""


class DataSourceNotFound(DataSourceCatalogError):
    pass


class DataSourceConflict(DataSourceCatalogError):
    pass


class CredentialConfigurationError(DataSourceCatalogError):
    pass


@dataclass(frozen=True)
class DataSourceRecord:
    source_id: str
    display_name: str
    description: str
    database_type: str
    connection_mode: str
    status: str
    enabled_for_chat: bool
    is_builtin: bool
    host: str
    port: int
    database_name: str
    schema_name: str
    ssl_mode: str
    connect_timeout: int
    credential_mode: str
    credential_reference: Mapping[str, str] = field(repr=False)
    encrypted_username: str = field(repr=False)
    encrypted_password: str = field(repr=False)
    metadata_path: Path
    memory_path: Path
    runtime_revision: int
    selected_tables_count: int
    selected_columns_count: int
    discovered_metadata: tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple,
        repr=False,
    )
    selected_scope: tuple[Mapping[str, Any], ...] = field(
        default_factory=tuple,
        repr=False,
    )
    routing_summary: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    created_at: int = 0
    updated_at: int = 0
    last_connection_test_at: int | None = None
    last_connection_test_status: str = ""
    last_error: str = ""

    def public_dict(self, *, detail: bool = False) -> dict[str, Any]:
        payload = {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "description": self.description,
            "database_type": self.database_type,
            "connection_mode": self.connection_mode,
            "status": self.status,
            "enabled_for_chat": self.enabled_for_chat,
            "is_builtin": self.is_builtin,
            "selected_tables_count": self.selected_tables_count,
            "selected_columns_count": self.selected_columns_count,
            "runtime_revision": self.runtime_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_connection_test_at": self.last_connection_test_at,
            "last_connection_test_status": self.last_connection_test_status,
            "last_error": self.last_error,
        }
        if detail:
            payload.update(
                {
                    "host": self.host,
                    "port": self.port,
                    "database_name": self.database_name,
                    "schema_name": self.schema_name,
                    "ssl_mode": self.ssl_mode,
                    "connect_timeout": self.connect_timeout,
                    "username": self.masked_username,
                    "has_password": bool(
                        self.encrypted_password
                        or self.credential_reference.get("password")
                    ),
                    "discovered_metadata": [
                        dict(item) for item in self.discovered_metadata
                    ],
                    "selected_scope": [
                        dict(item) for item in self.selected_scope
                    ],
                    "capabilities": list(self.capabilities),
                }
            )
        return payload

    @property
    def masked_username(self) -> str:
        if self.credential_mode == "environment":
            return "环境变量"
        return "已保存" if self.encrypted_username else ""


class CredentialCipher:
    """Fernet 对称加密封装；密钥只来自受控环境。"""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except Exception as exc:
            raise CredentialConfigurationError(
                "DATA_SOURCE_CREDENTIAL_KEY 格式无效"
            ) from exc

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "CredentialCipher":
        source = os.environ if environ is None else environ
        key = source.get("DATA_SOURCE_CREDENTIAL_KEY", "").strip()
        if not key:
            raise CredentialConfigurationError(
                "缺少 DATA_SOURCE_CREDENTIAL_KEY，无法保存新数据源凭据"
            )
        return cls(key)

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialConfigurationError("数据源凭据无法解密") from exc


def resolve_catalog_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get("DATA_SOURCE_CATALOG_PATH", "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else PROJECT_ROOT / "agent_data" / "data_sources" / "catalog.sqlite3"
    )
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def generate_local_credential_key(env_path: Path | None = None) -> str:
    """缺少密钥时在 Git 忽略的本地 .env 中创建一次，不回显值。"""
    target = (env_path or PROJECT_ROOT / ".env").resolve()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DATA_SOURCE_CREDENTIAL_KEY":
                key = value.strip().strip("\"'")
                if key:
                    return key
    key = Fernet.generate_key().decode("ascii")
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(f"DATA_SOURCE_CREDENTIAL_KEY={key}\n")
    return key


class DataSourceCatalog:
    """事务型动态目录；写入使用 BEGIN IMMEDIATE 和 WAL。"""

    def __init__(
        self,
        db_path: Path | str,
        *,
        cipher: CredentialCipher | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self._cipher = cipher
        self._environ = os.environ if environ is None else environ
        self._lock = RLock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, bootstrap: Iterable[Mapping[str, Any]] = ()) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS system_schema_versions (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT version FROM system_schema_versions WHERE component = ?",
                (SCHEMA_COMPONENT,),
            ).fetchone()
            if row is not None and row["version"] > SCHEMA_VERSION:
                raise DataSourceCatalogError("数据源目录版本高于当前程序")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_sources (
                    source_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    database_type TEXT NOT NULL,
                    connection_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled_for_chat INTEGER NOT NULL,
                    is_builtin INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    ssl_mode TEXT NOT NULL,
                    connect_timeout INTEGER NOT NULL,
                    credential_mode TEXT NOT NULL,
                    credential_reference_json TEXT NOT NULL,
                    encrypted_username TEXT NOT NULL,
                    encrypted_password TEXT NOT NULL,
                    metadata_path TEXT NOT NULL,
                    memory_path TEXT NOT NULL,
                    runtime_revision INTEGER NOT NULL,
                    selected_tables_count INTEGER NOT NULL,
                    selected_columns_count INTEGER NOT NULL,
                    discovered_metadata_json TEXT NOT NULL,
                    selected_scope_json TEXT NOT NULL,
                    routing_summary TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_connection_test_at INTEGER,
                    last_connection_test_status TEXT NOT NULL,
                    last_error TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_source_bindings (
                    conversation_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES data_sources(source_id),
                    created_at INTEGER NOT NULL
                );
                """
            )
            now = int(time.time())
            connection.execute(
                """
                INSERT INTO system_schema_versions(component, version, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version=excluded.version, updated_at=excluded.updated_at
                """,
                (SCHEMA_COMPONENT, SCHEMA_VERSION, now),
            )
            for item in bootstrap:
                self._insert_bootstrap(connection, item, now)

    def _insert_bootstrap(
        self,
        connection: sqlite3.Connection,
        item: Mapping[str, Any],
        now: int,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO data_sources (
                source_id, display_name, description, database_type,
                connection_mode, status, enabled_for_chat, is_builtin,
                host, port, database_name, schema_name, ssl_mode,
                connect_timeout, credential_mode, credential_reference_json,
                encrypted_username, encrypted_password, metadata_path,
                memory_path, runtime_revision, selected_tables_count,
                selected_columns_count, discovered_metadata_json,
                selected_scope_json, routing_summary, capabilities_json,
                created_at, updated_at, last_connection_test_at,
                last_connection_test_status, last_error
            ) VALUES (
                ?, ?, ?, ?, 'direct_database', 'ready', 1, 1,
                ?, ?, ?, ?, ?, ?, 'environment', ?, '', '', ?, ?,
                1, ?, ?, '[]', '[]', ?, ?, ?, ?, NULL, 'bootstrap', ''
            )
            """,
            (
                item["source_id"],
                item["display_name"],
                item.get("description", ""),
                item["database_type"],
                item["host"],
                item["port"],
                item["database_name"],
                item.get("schema_name", ""),
                item.get("ssl_mode", ""),
                item.get("connect_timeout", 10),
                json.dumps(
                    item["credential_reference"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                str(Path(item["metadata_path"]).resolve()),
                str(Path(item["memory_path"]).resolve()),
                item.get("selected_tables_count", 0),
                item.get("selected_columns_count", 0),
                item.get("routing_summary", ""),
                json.dumps(item.get("capabilities", []), ensure_ascii=False),
                now,
                now,
            ),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DataSourceRecord:
        return DataSourceRecord(
            source_id=row["source_id"],
            display_name=row["display_name"],
            description=row["description"],
            database_type=row["database_type"],
            connection_mode=row["connection_mode"],
            status=row["status"],
            enabled_for_chat=bool(row["enabled_for_chat"]),
            is_builtin=bool(row["is_builtin"]),
            host=row["host"],
            port=row["port"],
            database_name=row["database_name"],
            schema_name=row["schema_name"],
            ssl_mode=row["ssl_mode"],
            connect_timeout=row["connect_timeout"],
            credential_mode=row["credential_mode"],
            credential_reference=json.loads(row["credential_reference_json"]),
            encrypted_username=row["encrypted_username"],
            encrypted_password=row["encrypted_password"],
            metadata_path=Path(row["metadata_path"]),
            memory_path=Path(row["memory_path"]),
            runtime_revision=row["runtime_revision"],
            selected_tables_count=row["selected_tables_count"],
            selected_columns_count=row["selected_columns_count"],
            discovered_metadata=tuple(
                json.loads(row["discovered_metadata_json"])
            ),
            selected_scope=tuple(json.loads(row["selected_scope_json"])),
            routing_summary=row["routing_summary"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_connection_test_at=row["last_connection_test_at"],
            last_connection_test_status=row["last_connection_test_status"],
            last_error=row["last_error"],
        )

    def list(
        self,
        *,
        search: str = "",
        database_type: str = "",
        status: str = "",
        enabled: bool | None = None,
    ) -> tuple[DataSourceRecord, ...]:
        clauses: list[str] = []
        values: list[Any] = []
        if search.strip():
            clauses.append("(display_name LIKE ? OR description LIKE ?)")
            value = f"%{search.strip()}%"
            values.extend((value, value))
        if database_type:
            clauses.append("database_type = ?")
            values.append(database_type)
        if status:
            clauses.append("status = ?")
            values.append(status)
        if enabled is not None:
            clauses.append("enabled_for_chat = ?")
            values.append(int(enabled))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM data_sources"
                + where
                + " ORDER BY is_builtin DESC, display_name, source_id",
                values,
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def require(self, source_id: str) -> DataSourceRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM data_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise DataSourceNotFound("数据源不存在")
        return self._row_to_record(row)

    def create(
        self,
        *,
        display_name: str,
        description: str,
        database_type: str,
        host: str,
        port: int,
        database_name: str,
        schema_name: str = "",
        ssl_mode: str = "",
        connect_timeout: int = 10,
        username: str,
        password: str,
    ) -> DataSourceRecord:
        if database_type not in VALID_DATABASE_TYPES:
            raise DataSourceCatalogError("只支持 mysql 或 postgresql")
        if self._cipher is None:
            raise CredentialConfigurationError(
                "未配置数据源凭据加密密钥"
            )
        if not display_name.strip() or not host.strip() or not database_name.strip():
            raise DataSourceCatalogError("名称、主机和数据库不能为空")
        if port <= 0 or connect_timeout <= 0:
            raise DataSourceCatalogError("端口和连接超时必须是正整数")
        if not username or not password:
            raise DataSourceCatalogError("用户名和密码不能为空")
        source_id = f"ds_{uuid.uuid4().hex}"
        root = PROJECT_ROOT / "agent_data" / "data_sources" / source_id
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO data_sources (
                    source_id, display_name, description, database_type,
                    connection_mode, status, enabled_for_chat, is_builtin,
                    host, port, database_name, schema_name, ssl_mode,
                    connect_timeout, credential_mode, credential_reference_json,
                    encrypted_username, encrypted_password, metadata_path,
                    memory_path, runtime_revision, selected_tables_count,
                    selected_columns_count, discovered_metadata_json,
                    selected_scope_json, routing_summary, capabilities_json,
                    created_at, updated_at, last_connection_test_at,
                    last_connection_test_status, last_error
                ) VALUES (?, ?, ?, ?, 'direct_database', 'draft', 0, 0,
                    ?, ?, ?, ?, ?, ?, 'encrypted', '{}', ?, ?, ?, ?,
                    0, 0, 0, '[]', '[]', '', '[]', ?, ?, NULL, '', '')
                """,
                (
                    source_id,
                    display_name.strip(),
                    description.strip(),
                    database_type,
                    host.strip(),
                    port,
                    database_name.strip(),
                    schema_name.strip(),
                    ssl_mode.strip(),
                    connect_timeout,
                    self._cipher.encrypt(username),
                    self._cipher.encrypt(password),
                    str((root / "column_metadata_index.json").resolve()),
                    str((root / "memory").resolve()),
                    now,
                    now,
                ),
            )
        return self.require(source_id)

    def update(self, source_id: str, **changes: Any) -> DataSourceRecord:
        record = self.require(source_id)
        if record.is_builtin and any(
            name not in {"display_name", "description"}
            for name in changes
        ):
            raise DataSourceCatalogError(
                "内置数据源只允许修改显示名称和描述"
            )
        allowed = {
            "display_name",
            "description",
            "host",
            "port",
            "database_name",
            "schema_name",
            "ssl_mode",
            "connect_timeout",
        }
        unknown = set(changes) - allowed - {"username", "password"}
        if unknown:
            raise DataSourceCatalogError(
                "不允许修改字段：" + ", ".join(sorted(unknown))
            )
        assignments: list[str] = []
        values: list[Any] = []
        connection_changed = False
        for name in allowed:
            if name not in changes:
                continue
            value = changes[name]
            if name in {"port", "connect_timeout"}:
                if not isinstance(value, int) or value <= 0:
                    raise DataSourceCatalogError(f"{name} 必须是正整数")
            elif not isinstance(value, str):
                raise DataSourceCatalogError(f"{name} 必须是字符串")
            if name == "display_name" and not value.strip():
                raise DataSourceCatalogError("显示名称不能为空")
            assignments.append(f"{name} = ?")
            values.append(value.strip() if isinstance(value, str) else value)
            connection_changed |= name not in {"display_name", "description"}
        for field_name, column_name in (
            ("username", "encrypted_username"),
            ("password", "encrypted_password"),
        ):
            value = changes.get(field_name)
            if value in (None, ""):
                continue
            if record.credential_mode != "encrypted" or self._cipher is None:
                raise DataSourceCatalogError("内置环境凭据不能通过管理页替换")
            assignments.append(f"{column_name} = ?")
            values.append(self._cipher.encrypt(value))
            connection_changed = True
        if not assignments:
            return record
        if connection_changed:
            assignments.extend(
                (
                    "status = 'draft'",
                    "enabled_for_chat = 0",
                    "last_connection_test_status = ''",
                )
            )
        assignments.append("updated_at = ?")
        values.append(int(time.time()))
        values.append(source_id)
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                f"UPDATE data_sources SET {', '.join(assignments)} "
                "WHERE source_id = ?",
                values,
            )
        return self.require(source_id)

    def credentials(self, source_id: str) -> tuple[str, str]:
        record = self.require(source_id)
        if record.credential_mode == "environment":
            username = self._environ.get(
                record.credential_reference.get("username", ""), ""
            )
            password = self._environ.get(
                record.credential_reference.get("password", ""), ""
            )
        elif record.credential_mode == "encrypted":
            if self._cipher is None:
                raise CredentialConfigurationError(
                    "未配置数据源凭据加密密钥"
                )
            username = self._cipher.decrypt(record.encrypted_username)
            password = self._cipher.decrypt(record.encrypted_password)
        else:
            raise DataSourceCatalogError("未知凭据模式")
        if not username or not password:
            raise CredentialConfigurationError("数据源凭据不可用")
        return username, password

    def runtime_config(self, source_id: str) -> DataSourceConfig:
        record = self.require(source_id)
        username, password = self.credentials(source_id)
        settings: dict[str, Any] = {
            "host": record.host,
            "port": record.port,
            "database": record.database_name,
            "user": username,
            "password": password,
            "connect_timeout": record.connect_timeout,
        }
        if record.database_type == "mysql":
            settings["charset"] = "utf8mb4"
        else:
            settings.update(
                {
                    "application_name": "vanna-water-agent",
                    "options": (
                        "-c default_transaction_read_only=on "
                        "-c statement_timeout=30000 -c lock_timeout=5000"
                    ),
                }
            )
            if record.ssl_mode:
                settings["sslmode"] = record.ssl_mode
        return DataSourceConfig(
            source_id=record.source_id,
            database_type=record.database_type,
            sql_dialect=record.database_type,
            connection_settings=settings,
            metadata_path=record.metadata_path,
            memory_path=record.memory_path,
            read_only=True,
        )

    def mark_connection_test(
        self,
        source_id: str,
        *,
        success: bool,
        safe_error: str = "",
    ) -> DataSourceRecord:
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET
                    status = CASE
                        WHEN ? = 1 AND status IN ('ready', 'disabled')
                            THEN status
                        WHEN ? = 1 THEN 'connected'
                        ELSE 'error'
                    END,
                    enabled_for_chat = CASE
                        WHEN ? = 1 AND status = 'ready'
                            THEN enabled_for_chat
                        ELSE 0
                    END,
                    last_connection_test_at = ?,
                    last_connection_test_status = ?,
                    last_error = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (
                    int(success),
                    int(success),
                    int(success),
                    now,
                    "success" if success else "failed",
                    safe_error,
                    now,
                    source_id,
                ),
            )
        return self.require(source_id)

    def save_discovery(
        self,
        source_id: str,
        metadata: Iterable[Mapping[str, Any]],
    ) -> DataSourceRecord:
        payload = [dict(item) for item in metadata]
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET discovered_metadata_json = ?,
                    status = 'connected', last_error = '', updated_at = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    int(time.time()),
                    source_id,
                ),
            )
        return self.require(source_id)

    def save_scope(
        self,
        source_id: str,
        scope: Iterable[Mapping[str, Any]],
    ) -> DataSourceRecord:
        record = self.require(source_id)
        discovered = {
            (item.get("schema", ""), item.get("table"), item.get("column"))
            for item in record.discovered_metadata
        }
        payload = [dict(item) for item in scope]
        if not payload:
            raise DataSourceCatalogError("至少选择一张表")
        invalid = [
            item
            for item in payload
            if (
                item.get("schema", ""),
                item.get("table"),
                item.get("column"),
            )
            not in discovered
        ]
        if invalid:
            raise DataSourceCatalogError("范围包含未发现的表或字段")
        tables = {
            (item.get("schema", ""), item["table"]) for item in payload
        }
        next_status = (
            "training_required"
            if record.status in {"ready", "disabled"}
            else "metadata_ready"
        )
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET selected_scope_json = ?,
                    selected_tables_count = ?, selected_columns_count = ?,
                    status = ?, enabled_for_chat = 0, updated_at = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    len(tables),
                    len(payload),
                    next_status,
                    int(time.time()),
                    source_id,
                ),
            )
        return self.require(source_id)

    def publish(
        self,
        source_id: str,
        *,
        routing_summary: str,
    ) -> DataSourceRecord:
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT selected_tables_count FROM data_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise DataSourceNotFound("数据源不存在")
            if row["selected_tables_count"] <= 0:
                raise DataSourceCatalogError("尚未选择问数范围")
            connection.execute(
                """
                UPDATE data_sources SET status = 'ready',
                    enabled_for_chat = 1,
                    runtime_revision = runtime_revision + 1,
                    routing_summary = ?, last_error = '', updated_at = ?
                WHERE source_id = ?
                """,
                (routing_summary, now, source_id),
            )
        return self.require(source_id)

    def set_enabled(self, source_id: str, enabled: bool) -> DataSourceRecord:
        record = self.require(source_id)
        if enabled and record.runtime_revision <= 0:
            raise DataSourceCatalogError("数据源尚未准备问数资产")
        status = "ready" if enabled else "disabled"
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET status = ?, enabled_for_chat = ?,
                    updated_at = ? WHERE source_id = ?
                """,
                (status, int(enabled), int(time.time()), source_id),
            )
        return self.require(source_id)

    def bind_conversation(
        self,
        conversation_id: str,
        source_id: str,
    ) -> tuple[str, str]:
        if not conversation_id.strip():
            raise DataSourceCatalogError("conversation_id 不能为空")
        record = self.require(source_id)
        if record.status != "ready" or not record.enabled_for_chat:
            raise DataSourceConflict("数据源当前不可用于新会话")
        with self._lock, self._connection(write=True) as connection:
            existing = connection.execute(
                """
                SELECT source_id FROM conversation_source_bindings
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if existing is not None:
                if existing["source_id"] == source_id:
                    return conversation_id, source_id
                raise DataSourceConflict("会话已绑定其他数据源")
            connection.execute(
                """
                INSERT INTO conversation_source_bindings
                    (conversation_id, source_id, created_at)
                VALUES (?, ?, ?)
                """,
                (conversation_id, source_id, int(time.time())),
            )
        return conversation_id, source_id

    def require_binding(self, conversation_id: str) -> tuple[str, str]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT source_id FROM conversation_source_bindings
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise DataSourceNotFound("会话尚未绑定数据源")
        return conversation_id, row["source_id"]

    def dependency_summary(self, source_id: str) -> dict[str, Any]:
        record = self.require(source_id)
        with self._connection() as connection:
            conversations = connection.execute(
                """
                SELECT COUNT(*) AS total FROM conversation_source_bindings
                WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()["total"]
        dependencies = {
            "conversations": conversations,
            "builtin": record.is_builtin,
            "metadata": record.metadata_path.exists(),
            "memory": record.memory_path.exists(),
            "report_capability": "water_quality_daily_report"
            in record.capabilities,
        }
        dependencies["physical_delete_allowed"] = not any(
            dependencies.values()
        )
        return dependencies

    def delete(self, source_id: str, confirmation: str) -> None:
        record = self.require(source_id)
        if confirmation != record.display_name:
            raise DataSourceConflict("删除确认名称不匹配")
        summary = self.dependency_summary(source_id)
        if not summary["physical_delete_allowed"]:
            raise DataSourceConflict("数据源存在依赖，只允许停用")
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                "DELETE FROM data_sources WHERE source_id = ?",
                (source_id,),
            )

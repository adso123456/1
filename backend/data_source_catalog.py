"""动态数据源目录、加密凭据和会话绑定的 SQLite 事实源。"""

from __future__ import annotations

import hashlib
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

from backend.mysql_tls import build_mysql_tls_settings
from config.data_source_config import DataSourceConfig
from config.settings import PROJECT_ROOT, resolve_project_path


SCHEMA_COMPONENT = "data_source_catalog"
SCHEMA_VERSION = 10
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


def _store_project_path(value: Path | str) -> str:
    """项目内路径写入 Catalog 时只保存 POSIX 相对路径。"""
    resolved = resolve_project_path(value)
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        # 测试夹具和显式外部资产仍允许保留绝对路径。
        return str(resolved)


def _resolve_stored_path(value: Path | str) -> Path:
    return resolve_project_path(value)


def _store_path_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for key in ("base_memory_path", "target_memory_path"):
        if result.get(key):
            result[key] = _store_project_path(str(result[key]))
    return result


def _resolve_path_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for key in ("base_memory_path", "target_memory_path"):
        if result.get(key):
            result[key] = str(_resolve_stored_path(str(result[key])))
    return result


def _store_asset_plan(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        item = dict(value)
        for key in ("candidate", "formal", "backup"):
            if item.get(key):
                item[key] = _store_project_path(str(item[key]))
        result.append(item)
    return result


def _resolve_asset_plan(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        item = dict(value)
        for key in ("candidate", "formal", "backup"):
            if item.get(key):
                item[key] = str(_resolve_stored_path(str(item[key])))
        result.append(item)
    return result


def selected_scope_fingerprint(scope: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(item) for item in scope],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _published_asset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    identity = path / ".asset_identity.json"
    if identity.is_file():
        digest.update(identity.read_bytes())
        return digest.hexdigest()
    if not path.is_dir():
        return ""
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


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
    mysql_tls_mode: str
    ssl_ca_path: str
    ssl_cert_path: str
    ssl_key_path: str
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

    def safe_summary_dict(self) -> dict[str, Any]:
        """普通工作台和 Widget 可见的最小安全摘要。"""
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "description": self.description,
            "database_type": self.database_type,
            "status": self.status,
            "enabled_for_chat": self.enabled_for_chat,
            "selected_tables_count": self.selected_tables_count,
            "selected_columns_count": self.selected_columns_count,
        }

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
                    "mysql_tls_mode": self.mysql_tls_mode,
                    "ssl_ca_path": self.ssl_ca_path,
                    "ssl_cert_path": self.ssl_cert_path,
                    "ssl_key_path": self.ssl_key_path,
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
        # 空卷首启时 agent_data/data_sources/ 可能不存在，先建目录再连库。
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

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
                    mysql_tls_mode TEXT NOT NULL DEFAULT 'disabled',
                    ssl_ca_path TEXT NOT NULL DEFAULT '',
                    ssl_cert_path TEXT NOT NULL DEFAULT '',
                    ssl_key_path TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS pending_asset_cleanup (
                    source_id TEXT NOT NULL REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source_id, path)
                );
                CREATE TABLE IF NOT EXISTS active_asset_batches (
                    source_id TEXT PRIMARY KEY REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    batch_id TEXT NOT NULL,
                    candidate_root TEXT NOT NULL,
                    candidate_memory TEXT NOT NULL,
                    published_memory_path TEXT NOT NULL,
                    backup_paths_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    asset_plan_json TEXT NOT NULL DEFAULT '[]',
                    backed_up_assets_json TEXT NOT NULL DEFAULT '[]',
                    installed_assets_json TEXT NOT NULL DEFAULT '[]',
                    phase TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    owner_pid INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS data_source_table_profiles (
                    source_id TEXT NOT NULL REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    schema_name TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (source_id, schema_name, table_name)
                );
                CREATE TABLE IF NOT EXISTS data_source_onboarding_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    current_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_data_source_jobs_current
                    ON data_source_onboarding_jobs(source_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS data_source_verified_sql_memories (
                    source_id TEXT NOT NULL REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    record_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sql_text TEXT NOT NULL,
                    memory_metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (source_id, record_id)
                );
                CREATE TABLE IF NOT EXISTS builtin_data_source_claims (
                    source_id TEXT PRIMARY KEY REFERENCES data_sources(source_id)
                        ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    origin_endpoint_fingerprint TEXT NOT NULL DEFAULT '',
                    current_endpoint_fingerprint TEXT NOT NULL DEFAULT '',
                    active_endpoint_fingerprint TEXT NOT NULL DEFAULT '',
                    baseline_schema_fingerprint TEXT NOT NULL DEFAULT '',
                    remote_schema_fingerprint TEXT NOT NULL DEFAULT '',
                    diff_json TEXT NOT NULL DEFAULT '{}',
                    candidate_metadata_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    claimed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS data_source_table_reviews (
                    source_id TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    business_group TEXT NOT NULL DEFAULT '',
                    group_confidence REAL NOT NULL DEFAULT 0,
                    compared_tables_json TEXT NOT NULL DEFAULT '[]',
                    group_reason TEXT NOT NULL DEFAULT '',
                    proposed_decision TEXT NOT NULL DEFAULT '',
                    proposed_score REAL,
                    proposed_reason TEXT NOT NULL DEFAULT '',
                    effective_decision TEXT NOT NULL DEFAULT 'pending',
                    decision_source TEXT NOT NULL DEFAULT '',
                    decision_reason TEXT NOT NULL DEFAULT '',
                    availability_status TEXT NOT NULL DEFAULT 'present',
                    quality_metrics_json TEXT NOT NULL DEFAULT '{}',
                    structure_fingerprint TEXT NOT NULL DEFAULT '',
                    data_fingerprint TEXT NOT NULL DEFAULT '',
                    review_version INTEGER NOT NULL DEFAULT 0,
                    last_profiled_at REAL,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (source_id, schema_name, table_name)
                );
                CREATE INDEX IF NOT EXISTS idx_table_reviews_source
                    ON data_source_table_reviews(source_id, effective_decision);
                CREATE INDEX IF NOT EXISTS idx_table_reviews_group
                    ON data_source_table_reviews(source_id, business_group);
                CREATE TABLE IF NOT EXISTS data_source_review_runs (
                    run_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    review_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    discovered_tables INTEGER NOT NULL DEFAULT 0,
                    profiled_tables INTEGER NOT NULL DEFAULT 0,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    error TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_review_runs_source
                    ON data_source_review_runs(source_id, started_at);
                CREATE TABLE IF NOT EXISTS data_source_review_history (
                    run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    schema_name TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    proposed_decision TEXT NOT NULL DEFAULT '',
                    proposed_score REAL,
                    effective_decision TEXT NOT NULL DEFAULT '',
                    availability_status TEXT NOT NULL DEFAULT '',
                    quality_metrics_json TEXT NOT NULL DEFAULT '{}',
                    compared_tables_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    PRIMARY KEY (run_id, source_id, schema_name, table_name)
                );
                """
            )
            columns = {
                item["name"]
                for item in connection.execute(
                    "PRAGMA table_info(data_sources)"
                ).fetchall()
            }
            for name, definition in (
                ("mysql_tls_mode", "TEXT NOT NULL DEFAULT 'disabled'"),
                ("ssl_ca_path", "TEXT NOT NULL DEFAULT ''"),
                ("ssl_cert_path", "TEXT NOT NULL DEFAULT ''"),
                ("ssl_key_path", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE data_sources ADD COLUMN {name} {definition}"
                    )
            batch_columns = {
                item["name"]
                for item in connection.execute(
                    "PRAGMA table_info(active_asset_batches)"
                ).fetchall()
            }
            for name, definition in (
                ("snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("asset_plan_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("backed_up_assets_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("installed_assets_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("updated_at", "INTEGER NOT NULL DEFAULT 0"),
                ("owner_pid", "INTEGER NOT NULL DEFAULT 0"),
                ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in batch_columns:
                    connection.execute(
                        f"ALTER TABLE active_asset_batches ADD COLUMN {name} {definition}"
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

    def initialize_builtin_claims(
        self,
        lineage_by_source: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """端点离开本地副本后，强制两个内置源先进入认领隔离。"""
        from backend.data_source_claim_identity import (
            endpoint_fingerprint,
            endpoint_matches_replica,
            schema_fingerprint,
        )

        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            for source_id, lineage in lineage_by_source.items():
                row = connection.execute(
                    "SELECT * FROM data_sources WHERE source_id = ? AND is_builtin = 1",
                    (source_id,),
                ).fetchone()
                if row is None:
                    continue
                current_fingerprint = endpoint_fingerprint(
                    database_type=row["database_type"],
                    host=row["host"],
                    port=row["port"],
                    database_name=row["database_name"],
                    schema_name=row["schema_name"],
                )
                origin_fingerprint = endpoint_fingerprint(
                    database_type=str(lineage["database_type"]),
                    host=str((lineage.get("origin_hosts") or [""])[0]),
                    port=int(lineage["origin_port"]),
                    database_name=str(lineage["database_name"]),
                    schema_name=str(lineage.get("schema_name") or ""),
                )
                try:
                    baseline_metadata = json.loads(
                        _resolve_stored_path(row["metadata_path"]).read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, TypeError, ValueError):
                    baseline_metadata = []
                baseline_fingerprint = schema_fingerprint(
                    baseline_metadata if isinstance(baseline_metadata, list) else []
                )
                existing = connection.execute(
                    "SELECT * FROM builtin_data_source_claims WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                is_replica = endpoint_matches_replica(
                    database_type=row["database_type"],
                    host=row["host"],
                    port=row["port"],
                    database_name=row["database_name"],
                    schema_name=row["schema_name"],
                    lineage=lineage,
                )
                remains_claimed = (
                    existing is not None
                    and existing["status"] == "claimed"
                    and existing["active_endpoint_fingerprint"]
                    == current_fingerprint
                )
                preserves_pending_state = (
                    existing is not None
                    and existing["current_endpoint_fingerprint"]
                    == current_fingerprint
                    and existing["status"]
                    in {"claim_required", "claim_review", "failed"}
                )
                status = (
                    "not_required"
                    if is_replica
                    else "claimed"
                    if remains_claimed
                    else str(existing["status"])
                    if preserves_pending_state
                    else "claim_required"
                )
                active_fingerprint = (
                    current_fingerprint
                    if is_replica
                    else existing["active_endpoint_fingerprint"]
                    if existing is not None
                    else ""
                )
                connection.execute(
                    """
                    INSERT INTO builtin_data_source_claims (
                        source_id, status, origin_endpoint_fingerprint,
                        current_endpoint_fingerprint,
                        active_endpoint_fingerprint,
                        baseline_schema_fingerprint,
                        remote_schema_fingerprint, diff_json,
                        candidate_metadata_json, last_error,
                        created_at, updated_at, claimed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '', '{}', '[]', '', ?, ?, NULL)
                    ON CONFLICT(source_id) DO UPDATE SET
                        status = excluded.status,
                        origin_endpoint_fingerprint = excluded.origin_endpoint_fingerprint,
                        current_endpoint_fingerprint = excluded.current_endpoint_fingerprint,
                        active_endpoint_fingerprint = excluded.active_endpoint_fingerprint,
                        baseline_schema_fingerprint = excluded.baseline_schema_fingerprint,
                        updated_at = excluded.updated_at,
                        last_error = CASE
                            WHEN excluded.status = 'claim_required' THEN ''
                            ELSE builtin_data_source_claims.last_error
                        END
                    """,
                    (
                        source_id,
                        status,
                        origin_fingerprint,
                        current_fingerprint,
                        active_fingerprint,
                        baseline_fingerprint,
                        now,
                        now,
                    ),
                )
                if status == "claim_required":
                    connection.execute(
                        """
                        UPDATE data_sources
                        SET status = 'disabled', enabled_for_chat = 0,
                            last_error = '远程本尊尚未完成资产认领', updated_at = ?
                        WHERE source_id = ?
                        """,
                        (now, source_id),
                    )

    def builtin_claim_summary(self, source_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM builtin_data_source_claims WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            diff = json.loads(row["diff_json"])
        except (TypeError, ValueError):
            diff = {}
        return {
            "source_id": row["source_id"],
            "status": row["status"],
            "baseline_schema_fingerprint": row["baseline_schema_fingerprint"],
            "remote_schema_fingerprint": row["remote_schema_fingerprint"],
            "diff": diff if isinstance(diff, Mapping) else {},
            "last_error": row["last_error"],
            "updated_at": row["updated_at"],
            "claimed_at": row["claimed_at"],
        }

    def save_builtin_claim_preview(
        self,
        source_id: str,
        *,
        remote_schema_fingerprint: str,
        diff: Mapping[str, Any],
        candidate_metadata: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = [dict(item) for item in candidate_metadata]
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE builtin_data_source_claims
                SET status = 'claim_review', remote_schema_fingerprint = ?,
                    diff_json = ?, candidate_metadata_json = ?,
                    last_error = '', updated_at = ?
                WHERE source_id = ? AND status IN (
                    'claim_required', 'claim_analyzing', 'claim_review', 'failed'
                )
                """,
                (
                    remote_schema_fingerprint,
                    json.dumps(dict(diff), ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    source_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DataSourceConflict("该内置数据源当前不能生成认领预览")
        return self.builtin_claim_summary(source_id) or {}

    def builtin_claim_candidate_metadata(
        self,
        source_id: str,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT candidate_metadata_json FROM builtin_data_source_claims
                WHERE source_id = ? AND status = 'claim_review'
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            raise DataSourceConflict("尚未生成可发布的认领预览")
        payload = json.loads(row["candidate_metadata_json"])
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def mark_builtin_claim_running(self, source_id: str) -> None:
        with self._lock, self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE builtin_data_source_claims
                SET status = 'claim_analyzing', last_error = '', updated_at = ?
                WHERE source_id = ? AND status IN (
                    'claim_required', 'claim_review', 'failed'
                )
                """,
                (int(time.time()), source_id),
            )
            if cursor.rowcount != 1:
                raise DataSourceConflict("该内置数据源当前不能开始认领")

    def mark_builtin_claim_failed(self, source_id: str, error: str) -> None:
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE builtin_data_source_claims
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (error[:1000], int(time.time()), source_id),
            )

    def mark_builtin_claimed(self, source_id: str) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE builtin_data_source_claims
                SET status = 'claimed',
                    active_endpoint_fingerprint = current_endpoint_fingerprint,
                    baseline_schema_fingerprint = remote_schema_fingerprint,
                    last_error = '', claimed_at = ?, updated_at = ?
                WHERE source_id = ? AND status = 'claim_review'
                """,
                (now, now, source_id),
            )
            if cursor.rowcount != 1:
                raise DataSourceConflict("认领状态已变化，不能确认发布")
        return self.builtin_claim_summary(source_id) or {}

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
                mysql_tls_mode, ssl_ca_path, ssl_cert_path, ssl_key_path,
                connect_timeout, credential_mode, credential_reference_json,
                encrypted_username, encrypted_password, metadata_path,
                memory_path, runtime_revision, selected_tables_count,
                selected_columns_count, discovered_metadata_json,
                selected_scope_json, routing_summary, capabilities_json,
                created_at, updated_at, last_connection_test_at,
                last_connection_test_status, last_error
            ) VALUES (
                ?, ?, ?, ?, 'direct_database', 'ready', 1, 1,
                ?, ?, ?, ?, ?, 'disabled', '', '', '', ?,
                'environment', ?, '', '', ?, ?,
                1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'bootstrap', ''
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
                _store_project_path(item["metadata_path"]),
                _store_project_path(item["memory_path"]),
                item.get("selected_tables_count", 0),
                item.get("selected_columns_count", 0),
                json.dumps(
                    item.get("discovered_metadata") or [],
                    ensure_ascii=False,
                ),
                json.dumps(
                    item.get("selected_scope") or [],
                    ensure_ascii=False,
                ),
                item.get("routing_summary", ""),
                json.dumps(item.get("capabilities", []), ensure_ascii=False),
                now,
                now,
            ),
        )

    def populate_bootstrap_scope(
        self,
        source_id: str,
        metadata: Iterable[Mapping[str, Any]],
    ) -> DataSourceRecord:
        """内置引导源补齐发现元数据与问答范围（不动状态/启用标志）。

        修复历史引导把 discovered_metadata_json / selected_scope_json 写成
        '[]' 的问题：数据源建议等依赖 selected_scope 的逻辑会读不到任何表。
        """
        payload = [dict(item) for item in metadata]
        if not payload:
            raise DataSourceCatalogError("引导范围元数据为空")
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM data_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise DataSourceNotFound(f"数据源不存在：{source_id}")
            connection.execute(
                """
                UPDATE data_sources
                SET discovered_metadata_json = ?, selected_scope_json = ?,
                    updated_at = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    int(time.time()),
                    source_id,
                ),
            )
        return self.require(source_id)

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
            mysql_tls_mode=row["mysql_tls_mode"],
            ssl_ca_path=row["ssl_ca_path"],
            ssl_cert_path=row["ssl_cert_path"],
            ssl_key_path=row["ssl_key_path"],
            connect_timeout=row["connect_timeout"],
            credential_mode=row["credential_mode"],
            credential_reference=json.loads(row["credential_reference_json"]),
            encrypted_username=row["encrypted_username"],
            encrypted_password=row["encrypted_password"],
            metadata_path=_resolve_stored_path(row["metadata_path"]),
            memory_path=_resolve_stored_path(row["memory_path"]),
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
        mysql_tls_mode: str = "disabled",
        ssl_ca_path: str = "",
        ssl_cert_path: str = "",
        ssl_key_path: str = "",
        connect_timeout: int = 10,
        username: str,
        password: str,
        source_id: str | None = None,
        is_builtin: bool = False,
        metadata_path: str | Path | None = None,
        memory_path: str | Path | None = None,
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
        if database_type == "mysql":
            try:
                build_mysql_tls_settings(
                    mode=mysql_tls_mode,
                    ca_path=ssl_ca_path,
                    cert_path=ssl_cert_path,
                    key_path=ssl_key_path,
                )
            except ValueError as exc:
                raise DataSourceCatalogError(str(exc)) from None
        source_id = source_id or f"ds_{uuid.uuid4().hex}"
        if is_builtin:
            try:
                self.require(source_id)
            except DataSourceNotFound:
                pass
            else:
                raise DataSourceConflict(f"内置数据源已存在：{source_id}")
        root = PROJECT_ROOT / "agent_data" / "data_sources" / source_id
        resolved_metadata_path = (
            resolve_project_path(metadata_path)
            if metadata_path is not None
            else root / "column_metadata_index.json"
        )
        resolved_memory_path = (
            resolve_project_path(memory_path)
            if memory_path is not None
            else root / "memory"
        )
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO data_sources (
                    source_id, display_name, description, database_type,
                    connection_mode, status, enabled_for_chat, is_builtin,
                    host, port, database_name, schema_name, ssl_mode,
                    mysql_tls_mode, ssl_ca_path, ssl_cert_path, ssl_key_path,
                    connect_timeout, credential_mode, credential_reference_json,
                    encrypted_username, encrypted_password, metadata_path,
                    memory_path, runtime_revision, selected_tables_count,
                    selected_columns_count, discovered_metadata_json,
                    selected_scope_json, routing_summary, capabilities_json,
                    created_at, updated_at, last_connection_test_at,
                    last_connection_test_status, last_error
                ) VALUES (?, ?, ?, ?, 'direct_database', 'draft', 0, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'encrypted', '{}', ?, ?, ?, ?,
                    0, 0, 0, '[]', '[]', '', '[]', ?, ?, NULL, '', '')
                """,
                (
                    source_id,
                    display_name.strip(),
                    description.strip(),
                    database_type,
                    int(is_builtin),
                    host.strip(),
                    port,
                    database_name.strip(),
                    schema_name.strip(),
                    ssl_mode.strip(),
                    mysql_tls_mode.strip().lower(),
                    ssl_ca_path.strip(),
                    ssl_cert_path.strip(),
                    ssl_key_path.strip(),
                    connect_timeout,
                    self._cipher.encrypt(username),
                    self._cipher.encrypt(password),
                    _store_project_path(resolved_metadata_path),
                    _store_project_path(resolved_memory_path),
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
            "mysql_tls_mode",
            "ssl_ca_path",
            "ssl_cert_path",
            "ssl_key_path",
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
            normalized = value.strip() if isinstance(value, str) else value
            if getattr(record, name) == normalized:
                continue
            assignments.append(f"{name} = ?")
            values.append(normalized)
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
        if record.database_type == "mysql" and any(
            name in changes
            for name in {
                "mysql_tls_mode",
                "ssl_ca_path",
                "ssl_cert_path",
                "ssl_key_path",
            }
        ):
            try:
                build_mysql_tls_settings(
                    mode=str(
                        changes.get("mysql_tls_mode", record.mysql_tls_mode)
                    ),
                    ca_path=str(changes.get("ssl_ca_path", record.ssl_ca_path)),
                    cert_path=str(
                        changes.get("ssl_cert_path", record.ssl_cert_path)
                    ),
                    key_path=str(changes.get("ssl_key_path", record.ssl_key_path)),
                )
            except ValueError as exc:
                raise DataSourceCatalogError(str(exc)) from None
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
            settings.update(
                build_mysql_tls_settings(
                    mode=record.mysql_tls_mode,
                    ca_path=record.ssl_ca_path,
                    cert_path=record.ssl_cert_path,
                    key_path=record.ssl_key_path,
                )
            )
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
            row = connection.execute(
                """
                SELECT database_type, database_name, schema_name,
                    status, enabled_for_chat, runtime_revision,
                    selected_tables_count, selected_columns_count,
                    selected_scope_json, metadata_path, memory_path
                FROM data_sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                raise DataSourceNotFound("数据源不存在")
            published = row["runtime_revision"] > 0
            selected_scope = json.loads(row["selected_scope_json"])
            uses_formal_metadata = False
            if published and not selected_scope:
                try:
                    formal_metadata = json.loads(
                        _resolve_stored_path(row["metadata_path"]).read_text(
                            encoding="utf-8"
                        )
                    )
                except (OSError, TypeError, ValueError):
                    formal_metadata = []
                if isinstance(formal_metadata, list):
                    selected_scope = [
                        dict(item)
                        for item in formal_metadata
                        if isinstance(item, Mapping)
                    ]
                    uses_formal_metadata = bool(selected_scope)

            default_schema = row["schema_name"]
            if not default_schema and row["database_type"] == "mysql":
                default_schema = row["database_name"]

            def identity(item: Mapping[str, Any]) -> tuple[Any, Any, Any]:
                return (
                    item.get("schema", default_schema),
                    item.get("table"),
                    item.get("column"),
                )

            discovered_by_column = {
                identity(item): item for item in payload
            }
            structure_defaults = {
                "type": "",
                "object_type": "table",
                "nullable": True,
                "primary_key": False,
                "ordinal_position": 0,
                "indexes": [],
            }
            selected_tables = {
                (identity(item)[0], item.get("table"))
                for item in selected_scope
            }
            scope_compatible = (
                bool(selected_scope)
                and len(selected_scope) == row["selected_columns_count"]
                and len(selected_tables) == row["selected_tables_count"]
            )
            for selected in selected_scope:
                current = discovered_by_column.get(identity(selected))
                selected_type = str(selected.get("type", "")).lower()
                ignored_legacy_geometry = (
                    current is None
                    and uses_formal_metadata
                    and row["database_type"] == "postgresql"
                    and (
                        selected_type == "geometry"
                        or selected_type.startswith("geometry(")
                    )
                )
                if ignored_legacy_geometry:
                    continue
                if current is None:
                    scope_compatible = False
                    break
                for name, default in structure_defaults.items():
                    if name not in selected:
                        continue
                    old_value = selected.get(name, default)
                    new_value = current.get(name, default)
                    ignored_legacy_numeric_precision = (
                        name == "type"
                        and uses_formal_metadata
                        and row["database_type"] == "postgresql"
                        and str(old_value).lower().startswith("numeric(")
                        and str(new_value).lower() == "numeric"
                    )
                    if (
                        old_value != new_value
                        and not ignored_legacy_numeric_precision
                    ):
                        scope_compatible = False
                        break
                if not scope_compatible:
                    break

            assets_exist = all(
                _resolve_stored_path(row[name]).exists()
                for name in ("metadata_path", "memory_path")
            )
            if not published:
                next_status, enabled_for_chat = "connected", 0
            elif not scope_compatible or not assets_exist:
                next_status, enabled_for_chat = "training_required", 0
            elif row["status"] == "disabled":
                next_status, enabled_for_chat = "disabled", 0
            elif row["status"] in {"ready", "connected"} and bool(
                row["enabled_for_chat"]
            ):
                next_status, enabled_for_chat = "ready", 1
            else:
                next_status, enabled_for_chat = "training_required", 0

            connection.execute(
                """
                UPDATE data_sources SET discovered_metadata_json = ?,
                    status = ?, enabled_for_chat = ?,
                    last_error = '', updated_at = ?
                WHERE source_id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    next_status,
                    enabled_for_chat,
                    int(time.time()),
                    source_id,
                ),
            )
        return self.require(source_id)

    def replace_table_profiles(
        self,
        source_id: str,
        profiles: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """原子替换一个数据源的受限数据画像。"""
        self.require(source_id)
        payload = [dict(item) for item in profiles]
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                "DELETE FROM data_source_table_profiles WHERE source_id = ?",
                (source_id,),
            )
            for item in payload:
                schema_name = str(item.get("schema") or "").strip()
                table_name = str(item.get("table") or "").strip()
                if not schema_name or not table_name:
                    raise DataSourceCatalogError("数据画像缺少 schema 或 table")
                connection.execute(
                    """
                    INSERT INTO data_source_table_profiles (
                        source_id, schema_name, table_name,
                        profile_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        schema_name,
                        table_name,
                        json.dumps(item, ensure_ascii=False),
                        now,
                    ),
                )
        return payload

    def list_table_profiles(self, source_id: str) -> list[dict[str, Any]]:
        """读取已持久化的数据画像，不返回数据库连接凭据。"""
        self.require(source_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT profile_json
                FROM data_source_table_profiles
                WHERE source_id = ?
                ORDER BY schema_name, table_name
                """,
                (source_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                item = json.loads(row["profile_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(item, Mapping):
                result.append(dict(item))
        return result

    # ------------------------------------------------------------------
    # 表级准入审核（reviews / runs / history）
    # ------------------------------------------------------------------
    _REVIEW_UPDATE_FIELDS = {
        "business_group",
        "group_confidence",
        "compared_tables_json",
        "group_reason",
        "proposed_decision",
        "proposed_score",
        "proposed_reason",
        "effective_decision",
        "decision_source",
        "decision_reason",
        "availability_status",
        "quality_metrics_json",
        "structure_fingerprint",
        "data_fingerprint",
        "review_version",
        "last_profiled_at",
        "reviewed_by",
    }

    def _upsert_review_row(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        schema_name: str,
        table_name: str,
        fields: Mapping[str, Any],
        *,
        now: float,
    ) -> None:
        """在给定连接上写入/更新单表审核状态（不管理事务，供原子批处理复用）。
        effective_decision 默认保留已有值，调用方显式传入时才会变更。"""
        allowed = {
            key: value
            for key, value in fields.items()
            if key in self._REVIEW_UPDATE_FIELDS
        }
        existing = connection.execute(
            "SELECT * FROM data_source_table_reviews "
            "WHERE source_id=? AND schema_name=? AND table_name=?",
            (source_id, schema_name, table_name),
        ).fetchone()
        if existing is not None:
            current = dict(existing)
            if (
                "effective_decision" not in allowed
                and current.get("effective_decision")
            ):
                allowed["effective_decision"] = current["effective_decision"]
            sets = ["updated_at = ?"]
            params: list[Any] = [now]
            for key, value in allowed.items():
                sets.append(f"{key} = ?")
                params.append(value)
            params.extend((source_id, schema_name, table_name))
            connection.execute(
                "UPDATE data_source_table_reviews SET "
                + ", ".join(sets)
                + " WHERE source_id=? AND schema_name=? AND table_name=?",
                params,
            )
        else:
            defaults: dict[str, Any] = {
                "business_group": "",
                "group_confidence": 0,
                "compared_tables_json": "[]",
                "group_reason": "",
                "proposed_decision": "",
                "proposed_score": None,
                "proposed_reason": "",
                "effective_decision": "pending",
                "decision_source": "",
                "decision_reason": "",
                "availability_status": "present",
                "quality_metrics_json": "{}",
                "structure_fingerprint": "",
                "data_fingerprint": "",
                "review_version": 0,
                "last_profiled_at": None,
                "reviewed_by": "",
            }
            defaults.update(allowed)
            connection.execute(
                """
                INSERT INTO data_source_table_reviews (
                    source_id, schema_name, table_name,
                    business_group, group_confidence, compared_tables_json,
                    group_reason, proposed_decision, proposed_score,
                    proposed_reason, effective_decision, decision_source,
                    decision_reason, availability_status,
                    quality_metrics_json, structure_fingerprint,
                    data_fingerprint, review_version, last_profiled_at,
                    reviewed_by, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_id,
                    schema_name,
                    table_name,
                    defaults["business_group"],
                    defaults["group_confidence"],
                    defaults["compared_tables_json"],
                    defaults["group_reason"],
                    defaults["proposed_decision"],
                    defaults["proposed_score"],
                    defaults["proposed_reason"],
                    defaults["effective_decision"],
                    defaults["decision_source"],
                    defaults["decision_reason"],
                    defaults["availability_status"],
                    defaults["quality_metrics_json"],
                    defaults["structure_fingerprint"],
                    defaults["data_fingerprint"],
                    defaults["review_version"],
                    defaults["last_profiled_at"],
                    defaults["reviewed_by"],
                    now,
                    now,
                ),
            )

    def upsert_table_review(
        self,
        source_id: str,
        schema_name: str,
        table_name: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """写入/更新单表审核状态（独立事务）。"""
        with self._lock, self._connection(write=True) as connection:
            self._upsert_review_row(
                connection,
                source_id,
                schema_name,
                table_name,
                fields,
                now=time.time(),
            )
        return self.get_table_review(source_id, schema_name, table_name)

    def get_table_review(
        self,
        source_id: str,
        schema_name: str,
        table_name: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM data_source_table_reviews "
                "WHERE source_id=? AND schema_name=? AND table_name=?",
                (source_id, schema_name, table_name),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_table_reviews(
        self,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM data_source_table_reviews"
                + where
                + " ORDER BY source_id, schema_name, table_name",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def record_review_run(
        self,
        *,
        run_id: str,
        source_id: str,
        review_version: int,
        status: str,
        discovered_tables: int = 0,
        profiled_tables: int = 0,
        error: str = "",
        created_by: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO data_source_review_runs (
                    run_id, source_id, review_version, status,
                    discovered_tables, profiled_tables, started_at,
                    finished_at, error, created_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    source_id,
                    review_version,
                    status,
                    discovered_tables,
                    profiled_tables,
                    now,
                    now if status in {"succeeded", "failed"} else None,
                    error,
                    created_by,
                ),
            )
        return {
            "run_id": run_id,
            "source_id": source_id,
            "status": status,
        }

    def finish_review_run(
        self,
        run_id: str,
        *,
        status: str,
        profiled_tables: int,
        error: str = "",
    ) -> None:
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_source_review_runs
                SET status=?, profiled_tables=?, finished_at=?, error=?
                WHERE run_id=?
                """,
                (status, profiled_tables, time.time(), error, run_id),
            )

    def append_review_history(
        self,
        run_id: str,
        snapshots: Iterable[Mapping[str, Any]],
    ) -> None:
        now = time.time()
        with self._lock, self._connection(write=True) as connection:
            for item in snapshots:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO data_source_review_history (
                        run_id, source_id, schema_name, table_name,
                        proposed_decision, proposed_score,
                        effective_decision, availability_status,
                        quality_metrics_json, compared_tables_json, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        str(item.get("source_id") or ""),
                        str(item.get("schema_name") or ""),
                        str(item.get("table_name") or ""),
                        str(item.get("proposed_decision") or ""),
                        item.get("proposed_score"),
                        str(item.get("effective_decision") or ""),
                        str(item.get("availability_status") or ""),
                        str(item.get("quality_metrics_json") or "{}"),
                        str(item.get("compared_tables_json") or "[]"),
                        now,
                    ),
                )

    def apply_review_results(
        self,
        source_id: str,
        run_id: str,
        *,
        review_updates: Iterable[tuple[str, str, Mapping[str, Any]]],
        missing_keys: Iterable[tuple[str, str]],
        history_snapshots: Iterable[Mapping[str, Any]],
        profiled_tables: int,
    ) -> None:
        """原子写入一轮审核结果：reviews + missing + history + run 成功标记。

        任一写入失败则整体回滚（_connection 异常时 rollback），
        调用方负责把 run 标记为 failed，保证不会留下无历史对应的部分状态。
        """
        now = time.time()
        with self._lock, self._connection(write=True) as connection:
            for schema_name, table_name, fields in review_updates:
                self._upsert_review_row(
                    connection,
                    source_id,
                    schema_name,
                    table_name,
                    fields,
                    now=now,
                )
            for schema_name, table_name in missing_keys:
                connection.execute(
                    "UPDATE data_source_table_reviews "
                    "SET availability_status='missing', updated_at=? "
                    "WHERE source_id=? AND schema_name=? AND table_name=?",
                    (now, source_id, schema_name, table_name),
                )
            for item in history_snapshots:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO data_source_review_history (
                        run_id, source_id, schema_name, table_name,
                        proposed_decision, proposed_score,
                        effective_decision, availability_status,
                        quality_metrics_json, compared_tables_json, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        str(item.get("source_id") or ""),
                        str(item.get("schema_name") or ""),
                        str(item.get("table_name") or ""),
                        str(item.get("proposed_decision") or ""),
                        item.get("proposed_score"),
                        str(item.get("effective_decision") or ""),
                        str(item.get("availability_status") or ""),
                        str(item.get("quality_metrics_json") or "{}"),
                        str(item.get("compared_tables_json") or "[]"),
                        now,
                    ),
                )
            connection.execute(
                "UPDATE data_source_review_runs "
                "SET status='succeeded', profiled_tables=?, "
                "finished_at=?, error='' WHERE run_id=?",
                (profiled_tables, now, run_id),
            )

    def migrate_table_reviews_from_existing(
        self,
        source_id: str,
    ) -> dict[str, Any]:
        """首次启用审核器：把现有 selected_scope 迁移为 effective active，
        已发现但未选中的表迁移为 effective pending（无法证明曾经过正式审核）。

        整个迁移（含"是否已有 review"检查、全部表写入、migration run）
        在同一个 BEGIN IMMEDIATE 事务内完成：
          - 已有任意 review 记录 -> 原子 no-op，不覆盖人工决定；
          - 任一表写入失败 -> 全部回滚，重试仍可完整迁移，
            不会留下"部分迁移 + reviews 非空导致永不重试"的窗口。"""
        record = self.require(source_id)

        def schema_of(item: Mapping[str, Any]) -> str:
            return str(
                item.get("schema")
                or record.schema_name
                or (
                    record.database_name
                    if record.database_type == "mysql"
                    else ""
                )
                or ""
            )

        selected = {
            (schema_of(item), str(item.get("table") or ""))
            for item in record.selected_scope
            if item.get("table")
        }
        discovered = {
            (schema_of(item), str(item.get("table") or ""))
            for item in record.discovered_metadata
            if item.get("table")
        }
        run_id = f"migration-{source_id}-{time.time_ns()}-{uuid.uuid4().hex}"
        active_count = 0
        pending_count = 0
        now = time.time()
        with self._lock, self._connection(write=True) as connection:
            existing_count = connection.execute(
                "SELECT count(*) FROM data_source_table_reviews "
                "WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
            if existing_count > 0:
                return {
                    "run_id": "",
                    "skipped": True,
                    "reason": "已有审核记录，跳过迁移",
                    "active": 0,
                    "pending": 0,
                    "discovered": len(discovered),
                    "selected": len(selected),
                }
            for schema, table in sorted(selected):
                self._upsert_review_row(
                    connection,
                    source_id,
                    schema,
                    table,
                    {
                        "effective_decision": "active",
                        "decision_source": "migration",
                        "decision_reason": "existing_selected_scope",
                        "availability_status": "present",
                        "reviewed_by": "migration",
                    },
                    now=now,
                )
                active_count += 1
            for schema, table in sorted(discovered - selected):
                self._upsert_review_row(
                    connection,
                    source_id,
                    schema,
                    table,
                    {
                        "effective_decision": "pending",
                        "decision_source": "migration",
                        "decision_reason": "legacy_unclassified",
                        "availability_status": "present",
                        "reviewed_by": "migration",
                    },
                    now=now,
                )
                pending_count += 1
            connection.execute(
                """
                INSERT INTO data_source_review_runs (
                    run_id, source_id, review_version, status,
                    discovered_tables, profiled_tables, started_at,
                    finished_at, error, created_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    source_id,
                    0,
                    "migration",
                    len(discovered),
                    0,
                    now,
                    now,
                    "",
                    "migration",
                ),
            )
        return {
            "run_id": run_id,
            "skipped": False,
            "active": active_count,
            "pending": pending_count,
            "discovered": len(discovered),
            "selected": len(selected),
        }

    def rollback_review_schema(self) -> dict[str, Any]:
        """回滚审核 schema（v10 -> v9）：删除三张审核表并还原版本号。"""
        with self._lock, self._connection(write=True) as connection:
            connection.execute("DROP TABLE IF EXISTS data_source_review_history")
            connection.execute("DROP TABLE IF EXISTS data_source_review_runs")
            connection.execute("DROP TABLE IF EXISTS data_source_table_reviews")
            connection.execute(
                "UPDATE system_schema_versions SET version=9, updated_at=? "
                "WHERE component=?",
                (int(time.time()), SCHEMA_COMPONENT),
            )
        return {"rolled_back_to": 9}

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            result = json.loads(row["result_json"])
        except (TypeError, ValueError):
            result = {}
        return {
            "job_id": row["job_id"],
            "source_id": row["source_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "phase": row["phase"],
            "current_count": row["current_count"],
            "total_count": row["total_count"],
            "message": row["message"],
            "result": result if isinstance(result, Mapping) else {},
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_onboarding_job(
        self,
        source_id: str,
        job_type: str,
    ) -> dict[str, Any]:
        """创建任务；同一数据源同一时刻只允许一个活动任务。"""
        self.require(source_id)
        if job_type not in {
            "analyze",
            "activate",
            "review",
            "claim_preview",
            "claim_publish",
        }:
            raise DataSourceCatalogError("不支持的接入任务类型")
        now = int(time.time())
        job_id = f"job_{uuid.uuid4().hex}"
        with self._lock, self._connection(write=True) as connection:
            active = connection.execute(
                """
                SELECT job_id FROM data_source_onboarding_jobs
                WHERE source_id = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if active is not None:
                raise DataSourceConflict("该数据源已有分析或构建任务正在运行")
            connection.execute(
                """
                INSERT INTO data_source_onboarding_jobs (
                    job_id, source_id, job_type, status, phase,
                    current_count, total_count, message, result_json,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 'queued', 0, 0, '', '{}', '', ?, ?)
                """,
                (job_id, source_id, job_type, now, now),
            )
            row = connection.execute(
                "SELECT * FROM data_source_onboarding_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._job_dict(row)

    def update_onboarding_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        current_count: int | None = None,
        total_count: int | None = None,
        message: str | None = None,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"queued", "running", "succeeded", "failed"}
        if status is not None and status not in allowed_statuses:
            raise DataSourceCatalogError("不支持的任务状态")
        assignments = ["updated_at = ?"]
        values: list[Any] = [int(time.time())]
        for name, value in (
            ("status", status),
            ("phase", phase),
            ("current_count", current_count),
            ("total_count", total_count),
            ("message", message),
            ("error", error),
        ):
            if value is not None:
                assignments.append(f"{name} = ?")
                values.append(value)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(dict(result), ensure_ascii=False))
        values.append(job_id)
        with self._lock, self._connection(write=True) as connection:
            cursor = connection.execute(
                f"UPDATE data_source_onboarding_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise DataSourceNotFound("接入任务不存在")
            row = connection.execute(
                "SELECT * FROM data_source_onboarding_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._job_dict(row)

    def current_onboarding_job(self, source_id: str) -> dict[str, Any] | None:
        self.require(source_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM data_source_onboarding_jobs
                WHERE source_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        return self._job_dict(row) if row is not None else None

    def recover_onboarding_jobs(self) -> list[dict[str, Any]]:
        """进程重启后把中断的 running 任务重新排队。"""
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_source_onboarding_jobs
                SET status = 'queued', phase = 'recovered',
                    message = '服务重启，任务已重新排队', updated_at = ?
                WHERE status = 'running'
                """,
                (int(time.time()),),
            )
            rows = connection.execute(
                """
                SELECT * FROM data_source_onboarding_jobs
                WHERE status = 'queued' ORDER BY created_at
                """
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    def replace_verified_sql_memories(
        self,
        source_id: str,
        records: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """只保存已经通过 SQLGuard 和真实只读执行的 Tool Memory。"""
        self.require(source_id)
        payload = [dict(item) for item in records]
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                "DELETE FROM data_source_verified_sql_memories WHERE source_id = ?",
                (source_id,),
            )
            for item in payload:
                record_id = str(item.get("record_id") or "").strip()
                question = str(item.get("question") or "").strip()
                sql = str(item.get("sql") or "").strip()
                metadata = item.get("metadata")
                if not record_id or not question or not sql or not isinstance(metadata, Mapping):
                    raise DataSourceCatalogError("已验证 SQL Memory 结构不完整")
                connection.execute(
                    """
                    INSERT INTO data_source_verified_sql_memories (
                        source_id, record_id, question, sql_text,
                        memory_metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        record_id,
                        question,
                        sql,
                        json.dumps(dict(metadata), ensure_ascii=False),
                        now,
                    ),
                )
        return payload

    def list_verified_sql_memories(self, source_id: str) -> list[dict[str, Any]]:
        self.require(source_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_id, question, sql_text, memory_metadata_json
                FROM data_source_verified_sql_memories
                WHERE source_id = ? ORDER BY record_id
                """,
                (source_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["memory_metadata_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(metadata, Mapping):
                result.append(
                    {
                        "record_id": row["record_id"],
                        "question": row["question"],
                        "sql": row["sql_text"],
                        "metadata": dict(metadata),
                    }
                )
        return result

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
        memory_path: Path | None = None,
        expected_runtime_revision: int | None = None,
        expected_scope_fingerprint: str | None = None,
        expected_status: str | None = None,
    ) -> DataSourceRecord:
        now = int(time.time())
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT selected_tables_count, runtime_revision,
                    selected_scope_json, status
                FROM data_sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                raise DataSourceNotFound("数据源不存在")
            if row["selected_tables_count"] <= 0:
                raise DataSourceCatalogError("尚未选择问数范围")
            if (
                expected_runtime_revision is not None
                and row["runtime_revision"] != expected_runtime_revision
            ) or (
                expected_scope_fingerprint is not None
                and selected_scope_fingerprint(
                    json.loads(row["selected_scope_json"])
                )
                != expected_scope_fingerprint
            ) or (
                expected_status is not None
                and row["status"] != expected_status
            ):
                raise DataSourceConflict(
                    "数据源范围已变化，请重新生成问数资产"
                )
            connection.execute(
                """
                UPDATE data_sources SET status = 'ready',
                    enabled_for_chat = 1,
                    runtime_revision = runtime_revision + 1,
                    routing_summary = ?,
                    memory_path = COALESCE(?, memory_path),
                    last_error = '', updated_at = ?
                WHERE source_id = ?
                """,
                (
                    routing_summary,
                    _store_project_path(memory_path) if memory_path else None,
                    now,
                    source_id,
                ),
            )
        return self.require(source_id)

    def restore_publication_state(
        self,
        source_id: str,
        snapshot: DataSourceRecord,
    ) -> DataSourceRecord:
        """文件发布补偿失败时恢复目录中的发布相关字段。"""
        if snapshot.source_id != source_id:
            raise DataSourceCatalogError("发布快照与数据源不匹配")
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE data_sources SET status = ?,
                    enabled_for_chat = ?, runtime_revision = ?,
                    routing_summary = ?, memory_path = ?,
                    updated_at = ?, last_error = ?
                WHERE source_id = ?
                """,
                (
                    snapshot.status,
                    int(snapshot.enabled_for_chat),
                    snapshot.runtime_revision,
                    snapshot.routing_summary,
                    _store_project_path(snapshot.memory_path),
                    snapshot.updated_at,
                    snapshot.last_error,
                    source_id,
                ),
            )
        return self.require(source_id)

    def mark_recovery_failed(self, source_id: str, error: str) -> DataSourceRecord:
        """一致性无法证明时关闭问数入口，但保留批次与全部恢复证据。"""
        with self._lock, self._connection(write=True) as connection:
            result = connection.execute(
                """
                UPDATE data_sources SET status = 'error',
                    enabled_for_chat = 0, last_error = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (error[:1000], int(time.time()), source_id),
            )
            if result.rowcount != 1:
                raise DataSourceNotFound("数据源不存在")
        return self.require(source_id)

    def set_enabled(self, source_id: str, enabled: bool) -> DataSourceRecord:
        expected_status = "disabled" if enabled else "ready"
        next_status = "ready" if enabled else "disabled"
        with self._lock, self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT source_id, status, runtime_revision, metadata_path,
                    memory_path, is_builtin
                FROM data_sources WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
            if row is None:
                raise DataSourceNotFound("数据源不存在")
            if enabled and bool(row["is_builtin"]):
                claim = connection.execute(
                    """
                    SELECT status FROM builtin_data_source_claims
                    WHERE source_id = ?
                    """,
                    (source_id,),
                ).fetchone()
                if claim is not None and claim["status"] not in {
                    "not_required",
                    "claimed",
                }:
                    raise DataSourceCatalogError(
                        "远程本尊必须完成增量认领后才能启用问数"
                    )
            if row["status"] != expected_status:
                action = "启用" if enabled else "停用"
                raise DataSourceCatalogError(
                    f"仅允许对 {expected_status} 状态的数据源执行{action}"
                )
            if row["runtime_revision"] <= 0:
                raise DataSourceCatalogError("数据源尚未完成问数资产发布")
            asset_paths = (
                _resolve_stored_path(row["metadata_path"]),
                _resolve_stored_path(row["memory_path"]),
            )
            if not all(path.exists() for path in asset_paths):
                raise DataSourceCatalogError("当前正式 Metadata 或 Memory 不存在")
            if (
                enabled
                and not bool(row["is_builtin"])
                and (asset_paths[1] / ".asset_identity.json").is_file()
            ):
                root = asset_paths[0].parent
                manifest_path = root / "asset_manifest.json"
                ddl_path = root / "ddl_memories.json"
                documents_path = root / "business_documents.json"
                try:
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    raise DataSourceCatalogError(
                        "当前正式资产 manifest 不存在或不可读"
                    ) from None
                expected_hashes = {
                    "metadata_hash": _published_asset_hash(asset_paths[0]),
                    "memory_identity_hash": _published_asset_hash(asset_paths[1]),
                    "ddl_hash": _published_asset_hash(ddl_path),
                    "business_documents_hash": _published_asset_hash(
                        documents_path
                    ),
                }
                if (
                    manifest.get("source_id") != row["source_id"]
                    or int(manifest.get("runtime_revision", -1))
                    != int(row["runtime_revision"])
                    or any(
                        manifest.get(name) != value
                        for name, value in expected_hashes.items()
                    )
                ):
                    raise DataSourceCatalogError(
                        "当前正式资产与 Catalog 版本不一致"
                    )
            result = connection.execute(
                """
                UPDATE data_sources SET status = ?, enabled_for_chat = ?,
                    updated_at = ?
                WHERE source_id = ? AND status = ?
                """,
                (
                    next_status,
                    int(enabled),
                    int(time.time()),
                    source_id,
                    expected_status,
                ),
            )
            if result.rowcount != 1:
                raise DataSourceCatalogError("数据源状态已变化，请刷新后重试")
        return self.require(source_id)

    def register_pending_cleanup(
        self,
        source_id: str,
        path: Path,
        asset_type: str,
        error: str = "",
    ) -> None:
        self.require(source_id)
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                INSERT INTO pending_asset_cleanup(
                    source_id, path, asset_type, created_at,
                    retry_count, last_error
                ) VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    asset_type = excluded.asset_type,
                    retry_count = pending_asset_cleanup.retry_count + 1,
                    last_error = excluded.last_error
                """,
                (
                    source_id,
                    _store_project_path(path),
                    asset_type,
                    int(time.time()),
                    error[:1000],
                ),
            )

    def begin_asset_batch(
        self,
        source_id: str,
        *,
        batch_id: str,
        candidate_root: Path,
        candidate_memory: Path,
        published_memory_path: Path,
        snapshot: Mapping[str, Any] | None = None,
        asset_plan: Iterable[Mapping[str, Any]] = (),
        phase: str = "prepared",
        started_at: int | None = None,
    ) -> None:
        self.require(source_id)
        now = started_at if started_at is not None else int(time.time())
        try:
            with self._lock, self._connection(write=True) as connection:
                connection.execute(
                    """
                    INSERT INTO active_asset_batches(
                        source_id, batch_id, candidate_root,
                        candidate_memory, published_memory_path,
                        backup_paths_json, snapshot_json, asset_plan_json,
                        backed_up_assets_json, installed_assets_json,
                        phase, started_at, updated_at, owner_pid, last_error
                    ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, '[]', '[]', ?, ?, ?, ?, '')
                    """,
                    (
                        source_id,
                        batch_id,
                        _store_project_path(candidate_root),
                        _store_project_path(candidate_memory),
                        _store_project_path(published_memory_path),
                        json.dumps(
                            _store_path_mapping(snapshot or {}),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            _store_asset_plan(asset_plan),
                            ensure_ascii=False,
                        ),
                        phase,
                        now,
                        now,
                        os.getpid(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise DataSourceConflict(
                "该数据源正在生成问数资产，请稍后重试"
            ) from None

    def update_asset_batch(
        self,
        source_id: str,
        batch_id: str,
        *,
        phase: str | None = None,
        backup_paths: Iterable[Path] | None = None,
        backed_up_assets: Iterable[str] | None = None,
        installed_assets: Iterable[str] | None = None,
        last_error: str | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [int(time.time())]
        if phase is not None:
            assignments.append("phase = ?")
            values.append(phase)
        if backup_paths is not None:
            assignments.append("backup_paths_json = ?")
            values.append(json.dumps(
                [_store_project_path(path) for path in backup_paths],
                ensure_ascii=False,
            ))
        if backed_up_assets is not None:
            assignments.append("backed_up_assets_json = ?")
            values.append(json.dumps(list(backed_up_assets), ensure_ascii=False))
        if installed_assets is not None:
            assignments.append("installed_assets_json = ?")
            values.append(json.dumps(list(installed_assets), ensure_ascii=False))
        if last_error is not None:
            assignments.append("last_error = ?")
            values.append(last_error[:1000])
        values.extend((source_id, batch_id))
        with self._lock, self._connection(write=True) as connection:
            result = connection.execute(
                "UPDATE active_asset_batches SET "
                + ", ".join(assignments)
                + " WHERE source_id = ? AND batch_id = ?",
                tuple(values),
            )
            if result.rowcount != 1:
                raise DataSourceConflict("问数资产生成批次已失效")

    def replace_asset_batch_plan(
        self,
        source_id: str,
        batch_id: str,
        asset_plan: Iterable[Mapping[str, Any]],
    ) -> None:
        with self._lock, self._connection(write=True) as connection:
            result = connection.execute(
                """
                UPDATE active_asset_batches
                SET asset_plan_json = ?, updated_at = ?
                WHERE source_id = ? AND batch_id = ?
                """,
                (
                    json.dumps(
                        [dict(item) for item in asset_plan],
                        ensure_ascii=False,
                    ),
                    int(time.time()),
                    source_id,
                    batch_id,
                ),
            )
            if result.rowcount != 1:
                raise DataSourceConflict("问数资产生成批次已失效")

    def finish_asset_batch(self, source_id: str, batch_id: str) -> None:
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                DELETE FROM active_asset_batches
                WHERE source_id = ? AND batch_id = ?
                """,
                (source_id, batch_id),
            )

    def active_asset_batches(
        self,
        source_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        parameters: tuple[str, ...] = ()
        where = ""
        if source_id is not None:
            where = " WHERE source_id = ?"
            parameters = (source_id,)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT source_id, batch_id, candidate_root,
                    candidate_memory, published_memory_path,
                    backup_paths_json, snapshot_json, asset_plan_json,
                    backed_up_assets_json, installed_assets_json,
                    phase, started_at, updated_at, owner_pid, last_error
                FROM active_asset_batches
                """
                + where
                + " ORDER BY started_at, source_id",
                parameters,
            ).fetchall()
        return tuple(
            {
                **dict(row),
                "candidate_root": str(_resolve_stored_path(row["candidate_root"])),
                "candidate_memory": str(_resolve_stored_path(row["candidate_memory"])),
                "published_memory_path": str(
                    _resolve_stored_path(row["published_memory_path"])
                ),
                "backup_paths": tuple(
                    str(_resolve_stored_path(value))
                    for value in json.loads(row["backup_paths_json"])
                ),
                "snapshot": _resolve_path_mapping(
                    json.loads(row["snapshot_json"])
                ),
                "asset_plan": tuple(
                    _resolve_asset_plan(json.loads(row["asset_plan_json"]))
                ),
                "backed_up_assets": tuple(
                    json.loads(row["backed_up_assets_json"])
                ),
                "installed_assets": tuple(
                    json.loads(row["installed_assets_json"])
                ),
            }
            for row in rows
        )

    def active_asset_paths(self, source_id: str) -> frozenset[Path]:
        paths: set[Path] = set()
        for item in self.active_asset_batches(source_id):
            paths.update(
                Path(str(item[name])).expanduser().resolve()
                for name in (
                    "candidate_root",
                    "candidate_memory",
                    "published_memory_path",
                )
            )
            for asset in item["asset_plan"]:
                for name in ("candidate", "formal", "backup"):
                    value = str(asset.get(name, "")).strip()
                    if value:
                        paths.add(Path(value).expanduser().resolve())
            paths.update(
                Path(str(path)).expanduser().resolve()
                for path in item["backup_paths"]
            )
        return frozenset(paths)

    def pending_cleanups(
        self,
        source_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        parameters: tuple[str, ...] = ()
        where = ""
        if source_id is not None:
            self.require(source_id)
            where = " WHERE source_id = ?"
            parameters = (source_id,)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT source_id, path, asset_type, created_at,
                    retry_count, last_error
                FROM pending_asset_cleanup
                """
                + where
                + " ORDER BY created_at, source_id, path",
                parameters,
            ).fetchall()
        return tuple(
            {**dict(row), "path": str(_resolve_stored_path(row["path"]))}
            for row in rows
        )

    def complete_pending_cleanup(self, source_id: str, path: Path) -> None:
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                DELETE FROM pending_asset_cleanup
                WHERE source_id = ? AND path = ?
                """,
                (source_id, _store_project_path(path)),
            )

    def fail_pending_cleanup(
        self,
        source_id: str,
        path: Path,
        error: str,
    ) -> None:
        with self._lock, self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE pending_asset_cleanup
                SET retry_count = retry_count + 1, last_error = ?
                WHERE source_id = ? AND path = ?
                """,
                (
                    error[:1000],
                    source_id,
                    _store_project_path(path),
                ),
            )

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

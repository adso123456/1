"""MySQL/PostgreSQL 只读连接测试、元数据发现和候选资产发布。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from threading import Lock, RLock
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from backend.data_source_catalog import (
    DataSourceCatalog,
    DataSourceCatalogError,
    DataSourceConflict,
    selected_scope_fingerprint,
)
from backend.mysql_tls import build_mysql_tls_settings
from config.settings import PROJECT_ROOT

if TYPE_CHECKING:
    from backend.data_source_runtime_manager import DataSourceRuntimeManager


_PREPARE_LOCKS_GUARD = RLock()
_PREPARE_LOCKS: dict[str, Lock] = {}


class SimulatedProcessCrash(BaseException):
    """仅供确定性故障注入使用；绕过进程内补偿以模拟进程消失。"""


def _prepare_lock(source_id: str) -> Lock:
    with _PREPARE_LOCKS_GUARD:
        return _PREPARE_LOCKS.setdefault(source_id, Lock())


def _group_mysql_indexes(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row["schema_name"]),
            str(row["table_name"]),
            str(row["index_name"]),
        )
        index = grouped.setdefault(
            key,
            {
                "name": str(row["index_name"]),
                "unique": not bool(row["non_unique"]),
                "primary": str(row["index_name"]).upper() == "PRIMARY",
                "method": str(row.get("index_type") or "").upper(),
                "columns": [],
            },
        )
        column_name = row.get("column_name")
        if column_name:
            direction = {
                "A": "ASC",
                "D": "DESC",
            }.get(str(row.get("collation") or "").upper(), "")
            index["columns"].append(
                {
                    "name": str(column_name),
                    "position": int(row["position"]),
                    "direction": direction,
                }
            )
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (schema, table, _), index in grouped.items():
        index["columns"].sort(key=lambda item: item["position"])
        result.setdefault((schema, table), []).append(index)
    for indexes in result.values():
        indexes.sort(key=lambda item: item["name"])
    return result


def _group_postgresql_indexes(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row["schema_name"]),
            str(row["table_name"]),
            str(row["index_name"]),
        )
        index = grouped.setdefault(
            key,
            {
                "name": str(row["index_name"]),
                "unique": bool(row["is_unique"]),
                "primary": bool(row["is_primary"]),
                "method": str(row.get("index_method") or ""),
                "columns": [],
            },
        )
        column_name = row.get("column_name")
        column: dict[str, Any] = {
            "position": int(row["position"]),
            "direction": str(row.get("direction") or "ASC").upper(),
        }
        if column_name:
            column["name"] = str(column_name)
        else:
            column["expression"] = str(row.get("expression") or "")
            column["unsupported_expression"] = True
        index["columns"].append(column)
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (schema, table, _), index in grouped.items():
        index["columns"].sort(key=lambda item: item["position"])
        result.setdefault((schema, table), []).append(index)
    for indexes in result.values():
        indexes.sort(key=lambda item: item["name"])
    return result


def _safe_connection_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "连接超时"
    if "password" in text or "authentication" in text or "access denied" in text:
        return "认证失败"
    if "unknown database" in text or "does not exist" in text:
        return "数据库不存在"
    if "ssl" in text:
        return "SSL 配置错误"
    if "permission" in text or "denied" in text:
        return "账号缺少元数据读取或 SELECT 权限"
    return "连接失败"


class DirectDatabaseConnector:
    """直接数据库 Connector Protocol 的首版实现。"""

    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

    def _connect(self, source_id: str):
        record = self.catalog.require(source_id)
        username, password = self.catalog.credentials(source_id)
        if record.database_type == "mysql":
            import pymysql

            tls_settings = build_mysql_tls_settings(
                mode=record.mysql_tls_mode,
                ca_path=record.ssl_ca_path,
                cert_path=record.ssl_cert_path,
                key_path=record.ssl_key_path,
            )
            return pymysql.connect(
                host=record.host,
                port=record.port,
                database=record.database_name,
                user=username,
                password=password,
                connect_timeout=record.connect_timeout,
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
                **tls_settings,
            )
        import psycopg2
        import psycopg2.extras

        kwargs: dict[str, Any] = {
            "host": record.host,
            "port": record.port,
            "dbname": record.database_name,
            "user": username,
            "password": password,
            "connect_timeout": record.connect_timeout,
            "application_name": "vanna-data-source-test",
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
            "cursor_factory": psycopg2.extras.RealDictCursor,
        }
        if record.ssl_mode:
            kwargs["sslmode"] = record.ssl_mode
        return psycopg2.connect(**kwargs)

    def test_connection(self, source_id: str) -> dict[str, Any]:
        record = self.catalog.require(source_id)
        connection = None
        try:
            connection = self._connect(source_id)
            cursor = connection.cursor()
            try:
                if record.database_type == "mysql":
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute("SELECT VERSION() AS version")
                    version = cursor.fetchone()["version"]
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total FROM information_schema.tables
                        WHERE table_schema = %s
                        """,
                        (record.database_name,),
                    )
                else:
                    cursor.execute("BEGIN READ ONLY")
                    cursor.execute("SELECT version() AS version")
                    version = cursor.fetchone()["version"]
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total FROM information_schema.tables
                        WHERE table_catalog = %s
                          AND table_schema NOT IN ('pg_catalog', 'information_schema')
                        """,
                        (record.database_name,),
                    )
                cursor.fetchone()
                connection.rollback()
            finally:
                cursor.close()
            self.catalog.mark_connection_test(source_id, success=True)
            return {
                "success": True,
                "database_type": record.database_type,
                "version": str(version).splitlines()[0][:160],
                "read_only": True,
                "metadata_readable": True,
            }
        except Exception as exc:
            safe_error = _safe_connection_error(exc)
            self.catalog.mark_connection_test(
                source_id,
                success=False,
                safe_error=safe_error,
            )
            raise DataSourceCatalogError(safe_error) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def discover(
        self,
        source_id: str,
        *,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        record = self.catalog.require(source_id)
        connection = None
        try:
            connection = self._connect(source_id)
            cursor = connection.cursor()
            try:
                if record.database_type == "mysql":
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                    cursor.execute(
                        """
                        SELECT c.TABLE_SCHEMA AS schema_name,
                               c.TABLE_NAME AS table_name,
                               t.TABLE_TYPE AS object_type,
                               t.TABLE_COMMENT AS table_comment,
                               c.COLUMN_NAME AS column_name,
                               c.COLUMN_TYPE AS data_type,
                               c.IS_NULLABLE AS is_nullable,
                               c.COLUMN_COMMENT AS column_comment,
                               c.COLUMN_KEY AS column_key,
                               c.ORDINAL_POSITION AS ordinal_position
                        FROM information_schema.COLUMNS c
                        JOIN information_schema.TABLES t
                          ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
                         AND t.TABLE_NAME = c.TABLE_NAME
                        WHERE c.TABLE_SCHEMA = %s
                        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
                        """,
                        (record.database_name,),
                    )
                else:
                    schema = record.schema_name or "public"
                    cursor.execute("BEGIN READ ONLY")
                    cursor.execute(
                        """
                        SELECT c.table_schema AS schema_name,
                               c.table_name,
                               CASE WHEN t.table_type = 'VIEW'
                                    THEN 'view' ELSE 'table' END AS object_type,
                               COALESCE(obj_description(
                                   (quote_ident(c.table_schema)||'.'||
                                    quote_ident(c.table_name))::regclass
                               ), '') AS table_comment,
                               c.column_name,
                               c.data_type ||
                                 COALESCE('(' || c.character_maximum_length || ')', '')
                                 AS data_type,
                               c.is_nullable,
                               COALESCE(col_description(
                                   (quote_ident(c.table_schema)||'.'||
                                    quote_ident(c.table_name))::regclass,
                                   c.ordinal_position
                               ), '') AS column_comment,
                               CASE WHEN EXISTS (
                                   SELECT 1 FROM information_schema.table_constraints tc
                                   JOIN information_schema.key_column_usage kcu
                                     ON tc.constraint_name = kcu.constraint_name
                                    AND tc.table_schema = kcu.table_schema
                                  WHERE tc.constraint_type = 'PRIMARY KEY'
                                    AND tc.table_schema = c.table_schema
                                    AND tc.table_name = c.table_name
                                    AND kcu.column_name = c.column_name
                               ) THEN 'PRI' ELSE '' END AS column_key,
                               c.ordinal_position
                        FROM information_schema.columns c
                        JOIN information_schema.tables t
                          ON t.table_schema = c.table_schema
                         AND t.table_name = c.table_name
                        WHERE c.table_catalog = %s AND c.table_schema = %s
                          AND c.udt_name <> 'geometry'
                        ORDER BY c.table_name, c.ordinal_position
                        """,
                        (record.database_name, schema),
                    )
                rows = cursor.fetchall()
                if record.database_type == "mysql":
                    cursor.execute(
                        """
                        SELECT TABLE_SCHEMA AS schema_name,
                               TABLE_NAME AS table_name,
                               INDEX_NAME AS index_name,
                               NON_UNIQUE AS non_unique,
                               SEQ_IN_INDEX AS position,
                               COLUMN_NAME AS column_name,
                               COLLATION AS collation,
                               INDEX_TYPE AS index_type
                        FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA = %s
                        ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                        """,
                        (record.database_name,),
                    )
                    indexes_by_table = _group_mysql_indexes(cursor.fetchall())
                else:
                    cursor.execute(
                        """
                        SELECT ns.nspname AS schema_name,
                               tbl.relname AS table_name,
                               idx.relname AS index_name,
                               ind.indisunique AS is_unique,
                               ind.indisprimary AS is_primary,
                               key_column.ordinality AS position,
                               att.attname AS column_name,
                               CASE
                                 WHEN att.attname IS NULL
                                 THEN pg_get_indexdef(
                                     ind.indexrelid,
                                     key_column.ordinality::integer,
                                     TRUE
                                 )
                                 ELSE ''
                               END AS expression,
                               am.amname AS index_method,
                               CASE
                                 WHEN (
                                   ind.indoption[key_column.ordinality - 1] & 1
                                 ) = 1 THEN 'DESC'
                                 ELSE 'ASC'
                               END AS direction
                        FROM pg_index ind
                        JOIN pg_class tbl ON tbl.oid = ind.indrelid
                        JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                        JOIN pg_class idx ON idx.oid = ind.indexrelid
                        JOIN pg_am am ON am.oid = idx.relam
                        CROSS JOIN LATERAL unnest(ind.indkey)
                          WITH ORDINALITY AS key_column(attnum, ordinality)
                        LEFT JOIN pg_attribute att
                          ON att.attrelid = tbl.oid
                         AND att.attnum = key_column.attnum
                         AND key_column.attnum > 0
                        WHERE ns.nspname = %s
                          AND key_column.ordinality <= ind.indnkeyatts
                        ORDER BY tbl.relname, idx.relname,
                                 key_column.ordinality
                        """,
                        (schema,),
                    )
                    indexes_by_table = _group_postgresql_indexes(
                        cursor.fetchall()
                    )
                connection.rollback()
            finally:
                cursor.close()
            metadata = []
            for row in rows:
                table_key = (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                )
                table_indexes = indexes_by_table.get(table_key, [])
                column_name = str(row["column_name"])
                owned_indexes = [
                    index
                    for index in table_indexes
                    if index["columns"]
                    and index["columns"][0].get("name") == column_name
                ]
                metadata.append({
                    "schema": row["schema_name"],
                    "table": row["table_name"],
                    "object_type": str(row["object_type"]).lower(),
                    "table_comment": row["table_comment"] or "",
                    "column": row["column_name"],
                    "type": row["data_type"],
                    "comment": row["column_comment"] or "",
                    "nullable": str(row["is_nullable"]).upper() == "YES",
                    "primary_key": row["column_key"] == "PRI",
                    "ordinal_position": int(row["ordinal_position"]),
                    "indexes": owned_indexes,
                    "logical_relations": [],
                })
            if persist:
                self.catalog.save_discovery(source_id, metadata)
            return metadata
        except DataSourceCatalogError:
            raise
        except Exception as exc:
            raise DataSourceCatalogError(_safe_connection_error(exc)) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


class DataSourceAssetCleaner:
    """仅清理动态数据源受管目录内、已不再引用的发布资产。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        runtime_manager: "DataSourceRuntimeManager | None" = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.runtime_manager = runtime_manager
        self.fault_injector = fault_injector

    @staticmethod
    def _asset_type(path: Path) -> str | None:
        name = path.name
        if name.startswith("candidate-") or ".candidate-" in name:
            return "candidate"
        if name.startswith(".") and ".backup-" in name:
            return "backup"
        if ".revision-" in name:
            return "memory_revision"
        return None

    @staticmethod
    def _managed_root(source_id: str, record: Any) -> Path | None:
        if record.is_builtin:
            return None
        expected = (
            PROJECT_ROOT / "agent_data" / "data_sources" / source_id
        ).resolve()
        if (
            record.metadata_path.resolve().parent != expected
            or record.memory_path.resolve().parent != expected
        ):
            return None
        return expected

    def cleanup_superseded_assets(self, source_id: str) -> dict[str, int]:
        record = self.catalog.require(source_id)
        root = self._managed_root(source_id, record)
        if root is None:
            return {"deleted": 0, "pending": 0, "skipped": 0}
        protected = {
            record.metadata_path.resolve(),
            record.memory_path.resolve(),
            (record.metadata_path.parent / "ddl_memories.json").resolve(),
            (
                record.metadata_path.parent / "business_documents.json"
            ).resolve(),
            (record.metadata_path.parent / "asset_manifest.json").resolve(),
        }
        active = (
            self.runtime_manager.active_asset_paths(source_id)
            if self.runtime_manager is not None
            else frozenset()
        )
        active = active | self.catalog.active_asset_paths(source_id)
        paths: dict[Path, str] = {}
        if root.is_dir():
            for path in root.iterdir():
                asset_type = self._asset_type(path)
                if asset_type is not None:
                    paths[path.resolve()] = asset_type
        for item in self.catalog.pending_cleanups(source_id):
            paths[Path(str(item["path"])).resolve()] = str(item["asset_type"])

        deleted = pending = skipped = 0
        for path, asset_type in paths.items():
            if (
                path == root
                or path.parent != root
                or path in protected
                or self._asset_type(path) is None
            ):
                skipped += 1
                continue
            if asset_type == "memory_revision" and self.runtime_manager is None:
                self.catalog.register_pending_cleanup(
                    source_id,
                    path,
                    asset_type,
                    "缺少 Runtime Manager，无法证明旧资产已释放",
                )
                pending += 1
                continue
            if path in active:
                self.catalog.register_pending_cleanup(
                    source_id,
                    path,
                    asset_type,
                    "活动批次或 Runtime 仍在使用该资产",
                )
                pending += 1
                continue
            try:
                DataSourceAssetPreparer._remove_path(path)
            except Exception as exc:
                self.catalog.register_pending_cleanup(
                    source_id,
                    path,
                    asset_type,
                    f"{type(exc).__name__}: {exc}",
                )
                pending += 1
            else:
                self.catalog.complete_pending_cleanup(source_id, path)
                deleted += 1
        return {"deleted": deleted, "pending": pending, "skipped": skipped}

    def retry_pending_cleanup(self, source_id: str | None = None) -> None:
        if self.runtime_manager is not None and source_id is not None:
            self.runtime_manager.retry_failed_closes(source_id)
        pending_items = self.catalog.pending_cleanups(source_id)
        for item in pending_items:
            pending_source_id = str(item["source_id"])
            path = Path(str(item["path"])).resolve()
            try:
                record = self.catalog.require(pending_source_id)
                root = self._managed_root(pending_source_id, record)
                protected = {
                    record.metadata_path.resolve(),
                    record.memory_path.resolve(),
                    (
                        record.metadata_path.parent / "ddl_memories.json"
                    ).resolve(),
                    (
                        record.metadata_path.parent
                        / "business_documents.json"
                    ).resolve(),
                    (
                        record.metadata_path.parent / "asset_manifest.json"
                    ).resolve(),
                }
                active = self.catalog.active_asset_paths(pending_source_id)
                if self.runtime_manager is not None:
                    active |= self.runtime_manager.active_asset_paths(
                        pending_source_id
                    )
                if (
                    root is None
                    or path == root
                    or path.parent != root
                    or path in protected
                    or path in active
                    or self._asset_type(path) is None
                ):
                    continue
                if (
                    str(item["asset_type"]) == "memory_revision"
                    and self.runtime_manager is None
                ):
                    continue
                DataSourceAssetPreparer._remove_path(path)
                self.catalog.complete_pending_cleanup(
                    pending_source_id,
                    path,
                )
            except Exception as exc:
                self.catalog.fail_pending_cleanup(
                    pending_source_id,
                    path,
                    f"{type(exc).__name__}: {exc}",
                )

    @staticmethod
    def _path_hash(path: Path) -> str:
        digest = hashlib.sha256()
        if path.is_file():
            digest.update(path.read_bytes())
            return digest.hexdigest()
        if not path.is_dir():
            return ""
        identity = path / ".asset_identity.json"
        if identity.is_file():
            digest.update(identity.read_bytes())
            return digest.hexdigest()
        for item in sorted(value for value in path.rglob("*") if value.is_file()):
            digest.update(str(item.relative_to(path)).encode("utf-8"))
            digest.update(item.read_bytes())
        return digest.hexdigest()

    def _inject(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    @staticmethod
    def _process_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                # 无权限查询时不能把仍存活的其他服务实例误判为崩溃。
                if ctypes.GetLastError() == 5:
                    return True
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    process, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _safe_plan(
        self,
        item: Any,
        root: Path,
    ) -> list[dict[str, Any]]:
        plan = [dict(asset) for asset in item["asset_plan"]]
        expected_names = {
            "metadata",
            "memory",
            "ddl",
            "documentation",
            "manifest",
        }
        names = [str(asset.get("name", "")) for asset in plan]
        if len(names) != len(expected_names) or set(names) != expected_names:
            raise DataSourceCatalogError("活动发布批次缺少可恢复资产计划")

        candidate_root = Path(str(item["candidate_root"])).expanduser().resolve()
        if (
            candidate_root.parent != root
            or self._asset_type(candidate_root) != "candidate"
        ):
            raise DataSourceCatalogError("活动发布批次包含越界候选目录")

        snapshot = dict(item["snapshot"])
        target_memory = Path(
            str(snapshot.get("target_memory_path", ""))
        ).expanduser().resolve()
        if (
            target_memory.parent != root
            or self._asset_type(target_memory) != "memory_revision"
            or Path(str(item["candidate_memory"])).expanduser().resolve()
            != target_memory
            or Path(
                str(item["published_memory_path"])
            ).expanduser().resolve()
            != target_memory
        ):
            raise DataSourceCatalogError("活动发布批次包含非法目标 Memory")

        record = self.catalog.require(str(item["source_id"]))
        expected_formals = {
            "metadata": record.metadata_path.resolve(),
            "memory": target_memory,
            "ddl": (root / "ddl_memories.json").resolve(),
            "documentation": (root / "business_documents.json").resolve(),
            "manifest": (root / "asset_manifest.json").resolve(),
        }
        batch_id = str(item["batch_id"])
        for asset in plan:
            name = str(asset["name"])
            for field in ("candidate", "formal", "backup"):
                value = str(asset.get(field, "")).strip()
                if not value:
                    raise DataSourceCatalogError("活动发布批次包含空资产路径")
                path = Path(value).expanduser().resolve()
                formal = expected_formals[name]
                expected = {
                    "candidate": (
                        target_memory
                        if name == "memory"
                        else (candidate_root / formal.name).resolve()
                    ),
                    "formal": formal,
                    "backup": formal.with_name(
                        f".{formal.name}.backup-{batch_id}"
                    ).resolve(),
                }[field]
                if path != expected:
                    raise DataSourceCatalogError("活动发布批次包含越界资产路径")
                asset[field] = str(path)
        return plan

    def _rollback_batch(self, item: Any) -> None:
        source_id = str(item["source_id"])
        batch_id = str(item["batch_id"])
        record = self.catalog.require(source_id)
        root = self._managed_root(source_id, record)
        if root is None:
            raise DataSourceCatalogError("活动发布批次不在动态数据源受管目录")
        plan = self._safe_plan(item, root)
        snapshot = dict(item["snapshot"])
        required = {
            "base_runtime_revision",
            "base_status",
            "base_enabled_for_chat",
            "base_routing_summary",
            "base_memory_path",
            "base_scope_fingerprint",
            "base_updated_at",
            "base_last_error",
            "target_runtime_revision",
        }
        if not required <= snapshot.keys():
            raise DataSourceCatalogError("活动发布批次缺少完整 Catalog 快照")
        self.catalog.update_asset_batch(
            source_id,
            batch_id,
            phase="rolling_back",
            last_error="",
        )
        for index, asset in enumerate(reversed(plan)):
            formal = Path(asset["formal"])
            backup = Path(asset["backup"])
            base_existed = bool(asset.get("base_existed"))
            base_hash = str(asset.get("base_hash", ""))
            target_hash = str(asset.get("target_hash", ""))
            if backup.exists():
                if formal.exists():
                    DataSourceAssetPreparer._remove_path(formal)
                os.replace(backup, formal)
            elif base_existed:
                if not formal.exists() or self._path_hash(formal) != base_hash:
                    raise DataSourceCatalogError("旧版正式资产无法完整恢复")
            elif formal.exists():
                if target_hash and self._path_hash(formal) != target_hash:
                    raise DataSourceCatalogError("发现无法判定归属的正式资产")
                DataSourceAssetPreparer._remove_path(formal)
            self._inject(f"during_recovery_after_asset_{index + 1}")

        current = self.catalog.require(source_id)
        base_revision = int(snapshot["base_runtime_revision"])
        target_revision = int(snapshot["target_runtime_revision"])
        if current.runtime_revision not in {base_revision, target_revision}:
            raise DataSourceCatalogError("Catalog revision 已被其他发布修改")
        base_memory_path = Path(str(snapshot["base_memory_path"])).resolve()
        target_memory_path = Path(str(snapshot["target_memory_path"])).resolve()
        catalog_was_published = (
            current.runtime_revision == target_revision
            and current.memory_path.resolve() == target_memory_path
        )
        recovering_error_state = (
            current.runtime_revision == base_revision
            and current.memory_path.resolve() == base_memory_path
            and current.status == "error"
            and str(item["phase"]) == "rollback_failed"
        )
        if catalog_was_published or recovering_error_state:
            publication_state_unchanged = (
                current.status == "ready" and current.enabled_for_chat
            )
            restored_snapshot = replace(
                current,
                status=(
                    str(snapshot["base_status"])
                    if publication_state_unchanged or recovering_error_state
                    else current.status
                ),
                enabled_for_chat=(
                    bool(snapshot["base_enabled_for_chat"])
                    if publication_state_unchanged or recovering_error_state
                    else current.enabled_for_chat
                ),
                runtime_revision=base_revision,
                routing_summary=str(snapshot["base_routing_summary"]),
                memory_path=base_memory_path,
                updated_at=(
                    int(snapshot["base_updated_at"])
                    if publication_state_unchanged or recovering_error_state
                    else current.updated_at
                ),
                last_error=(
                    str(snapshot["base_last_error"])
                    if publication_state_unchanged or recovering_error_state
                    else current.last_error
                ),
            )
            self.catalog.restore_publication_state(source_id, restored_snapshot)
        elif current.memory_path.resolve() != base_memory_path:
            raise DataSourceCatalogError("Catalog memory_path 已被其他操作修改")
        for asset in plan:
            formal = Path(asset["formal"])
            if bool(asset.get("base_existed")):
                if (
                    not formal.exists()
                    or self._path_hash(formal) != str(asset.get("base_hash", ""))
                ):
                    raise DataSourceCatalogError("回滚后的旧版资产哈希不一致")
            elif formal.exists():
                raise DataSourceCatalogError("回滚后仍残留新版正式资产")
        self._finish_recovered_batch(item, plan)

    def _validate_new_assets(self, item: Any, plan: list[dict[str, Any]]) -> None:
        snapshot = dict(item["snapshot"])
        manifest_asset = next(
            (asset for asset in plan if asset.get("name") == "manifest"),
            None,
        )
        if manifest_asset is None:
            raise DataSourceCatalogError("发布批次缺少资产 manifest")
        for asset in plan:
            formal = Path(asset["formal"])
            if (
                not formal.exists()
                or self._path_hash(formal) != str(asset.get("target_hash", ""))
            ):
                raise DataSourceCatalogError("新版正式资产哈希不一致")
        manifest = json.loads(
            Path(manifest_asset["formal"]).read_text(encoding="utf-8")
        )
        if (
            manifest.get("source_id") != item["source_id"]
            or int(manifest.get("runtime_revision", -1))
            != int(snapshot["target_runtime_revision"])
            or manifest.get("scope_fingerprint")
            != snapshot["base_scope_fingerprint"]
            or manifest.get("batch_id") != item["batch_id"]
        ):
            raise DataSourceCatalogError("资产 manifest 与发布批次不一致")

    def _finish_recovered_batch(
        self,
        item: Any,
        plan: list[dict[str, Any]],
    ) -> None:
        source_id = str(item["source_id"])
        batch_id = str(item["batch_id"])
        for asset in plan:
            for field in ("candidate", "backup"):
                path = Path(asset[field])
                if (
                    field == "candidate"
                    and path.resolve() == Path(asset["formal"]).resolve()
                ):
                    continue
                if path.exists():
                    try:
                        DataSourceAssetPreparer._remove_path(path)
                    except Exception as exc:
                        self.catalog.register_pending_cleanup(
                            source_id,
                            path,
                            "backup" if field == "backup" else "candidate",
                            f"{type(exc).__name__}: {exc}",
                        )
        candidate_root = Path(str(item["candidate_root"])).resolve()
        if candidate_root.exists():
            try:
                DataSourceAssetPreparer._remove_path(candidate_root)
            except Exception as exc:
                self.catalog.register_pending_cleanup(
                    source_id,
                    candidate_root,
                    "candidate",
                    f"{type(exc).__name__}: {exc}",
                )
        self._inject("before_batch_finish")
        self.catalog.finish_asset_batch(source_id, batch_id)

    def _roll_forward_batch(self, item: Any) -> None:
        source_id = str(item["source_id"])
        batch_id = str(item["batch_id"])
        record = self.catalog.require(source_id)
        root = self._managed_root(source_id, record)
        if root is None:
            raise DataSourceCatalogError("活动发布批次不在动态数据源受管目录")
        plan = self._safe_plan(item, root)
        snapshot = dict(item["snapshot"])
        self._validate_new_assets(item, plan)
        if (
            record.runtime_revision != int(snapshot["target_runtime_revision"])
            or record.memory_path.resolve()
            != Path(str(snapshot["target_memory_path"])).resolve()
        ):
            raise DataSourceCatalogError("Catalog 与新版发布批次不一致")
        if self.runtime_manager is None:
            raise DataSourceCatalogError("缺少 Runtime Manager，无法验证新版")
        runtime = self.runtime_manager.require(source_id)
        if (
            runtime.config.memory_path.resolve() != record.memory_path.resolve()
            or self.runtime_manager.runtime_revision(source_id)
            != record.runtime_revision
        ):
            raise DataSourceCatalogError("Runtime 与 Catalog 版本不一致")
        self.catalog.update_asset_batch(
            source_id, batch_id, phase="runtime_validated"
        )
        self.catalog.update_asset_batch(source_id, batch_id, phase="committed")
        self._finish_recovered_batch(item, plan)

    def recover_incomplete_batches(
        self,
        source_id: str | None = None,
        *,
        grace_seconds: int = 0,
    ) -> None:
        for item in self.catalog.active_asset_batches(source_id):
            owner_pid = int(item.get("owner_pid") or 0)
            owner_alive = self._process_is_alive(owner_pid)
            # 有宽限策略的生产恢复绝不能接管仍存活进程的批次，即使一次
            # Chroma 构建超过宽限时间；grace_seconds=0 仅用于显式强制恢复。
            if grace_seconds > 0 and owner_alive:
                continue
            batch_source_id = str(item["source_id"])
            batch_id = str(item["batch_id"])
            try:
                snapshot = dict(item["snapshot"])
                current = self.catalog.require(batch_source_id)
                catalog_is_target = (
                    snapshot
                    and current.runtime_revision
                    == int(snapshot.get("target_runtime_revision", -1))
                    and current.memory_path.resolve()
                    == Path(str(snapshot.get("target_memory_path", ""))).resolve()
                )
                if catalog_is_target:
                    try:
                        self._roll_forward_batch(item)
                    except SimulatedProcessCrash:
                        raise
                    except Exception:
                        self._rollback_batch(item)
                else:
                    self._rollback_batch(item)
            except SimulatedProcessCrash:
                raise
            except Exception as exc:
                safe_error = f"{type(exc).__name__}: 发布恢复失败"
                try:
                    self.catalog.update_asset_batch(
                        batch_source_id,
                        batch_id,
                        phase="rollback_failed",
                        last_error=safe_error,
                    )
                    self.catalog.mark_recovery_failed(
                        batch_source_id,
                        "数据源发布恢复失败，请在管理页重试恢复",
                    )
                except Exception:
                    pass

    def cleanup_stale_batches(self, grace_seconds: int = 600) -> None:
        """兼容旧调用；过期批次必须恢复，不能按垃圾直接删除。"""
        self.recover_incomplete_batches(grace_seconds=grace_seconds)


class DataSourceAssetPreparer:
    """生成隔离候选 Metadata/DDL/基础文档并原子发布。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        runtime_manager: "DataSourceRuntimeManager | None" = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.runtime_manager = runtime_manager
        self.fault_injector = fault_injector
        self.asset_cleaner = DataSourceAssetCleaner(
            catalog,
            runtime_manager,
            fault_injector,
        )

    def _inject(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    @staticmethod
    def _quote(name: str, dialect: str) -> str:
        quote = "`" if dialect == "mysql" else '"'
        return quote + name.replace(quote, quote * 2) + quote

    @staticmethod
    def _domain_documents(
        grouped: dict[tuple[str, str], list[dict[str, Any]]],
        *,
        chunk_size: int = 8,
    ) -> list[str]:
        """按领域分块生成确定性业务文档，避免把全库 DDL 塞入单次 Prompt。"""
        domains: dict[str, list[str]] = {}
        for (_, table), columns in sorted(grouped.items()):
            first = columns[0]
            domain = str(first.get("domain") or "其他业务")
            time_column = str(first.get("time_column") or "")
            grain = str(first.get("grain") or "未确认")
            rules = list(first.get("valid_row_rules") or [])
            relations = list(first.get("logical_relations") or [])
            summary = (
                f"{table}"
                f"{'（' + str(first.get('table_comment')) + '）' if first.get('table_comment') else ''}"
                f"；粒度：{grain}"
                f"{'；时间字段：' + time_column if time_column else ''}"
                f"{'；有效记录：' + '、'.join(map(str, rules)) if rules else ''}"
                f"{'；可靠关系：' + '、'.join(str(item.get('column')) + '→' + str(item.get('target')) for item in relations) if relations else ''}"
            )
            domains.setdefault(domain, []).append(summary)
        documents = []
        for domain, tables in sorted(domains.items()):
            for offset in range(0, len(tables), chunk_size):
                chunk = tables[offset : offset + chunk_size]
                documents.append(
                    f"业务领域：{domain}。可回答该领域的明细、聚合、"
                    "排名和有时间字段时的趋势问题。主要表："
                    + "；".join(chunk)
                    + "。只允许使用文中列出的可靠关系；不得因同名 id/name "
                    "自动 JOIN，不得跨小时/日/月粒度直接拼接。"
                )
        return documents

    @staticmethod
    def _preserved_sql_tool_payload(
        *,
        source_id: str,
        memory_path: Path | None,
        metadata_path: Path,
        database_type: str,
        generated_records: list[dict[str, Any]] | None = None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """从旧正式 Memory 复制并重新校验已验证 SQL Tool Memory。"""
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        if memory_path is not None and memory_path.exists():
            from backend.memory import create_memory

            memory = create_memory(memory_path)
            try:
                collection = memory._get_collection()
                getter = getattr(collection, "get", None)
                if callable(getter):
                    result = getter(
                        where={"category": "sql_example"},
                        include=["documents", "metadatas"],
                    )
                    ids = list(result.get("ids") or [])
                    documents = list(result.get("documents") or [])
                    metadatas = list(result.get("metadatas") or [])
            finally:
                if not DataSourceAssetPreparer._close_memory(memory):
                    raise DataSourceCatalogError("旧正式 Memory 资源释放失败")
        if memory_path is not None and not ids and source_id == "mysql-lzh-monitor":
            from training.mysql_lzh_monitor_training import build_tool_records

            records, validation = build_tool_records()
            if not validation.get("valid"):
                raise DataSourceCatalogError("冻结 SQL Tool Memory 校验失败")
            ids = [item.record_id for item in records]
            documents = [item.document for item in records]
            metadatas = [dict(item.metadata) for item in records]
        known_ids = set(map(str, ids))
        for record in generated_records or []:
            record_id = str(record.get("record_id") or "")
            metadata = record.get("metadata")
            if (
                not record_id
                or record_id in known_ids
                or not isinstance(metadata, dict)
            ):
                continue
            ids.append(record_id)
            documents.append(str(record.get("question") or ""))
            metadatas.append(dict(metadata))
            known_ids.add(record_id)
        if not (len(ids) == len(documents) == len(metadatas)):
            raise DataSourceCatalogError("旧 SQL Tool Memory 结构不完整")
        if database_type == "mysql":
            from backend.mysql_sql_guard import MySQLSQLGuard

            guard = MySQLSQLGuard(index_path=metadata_path)
        else:
            from backend.sql_guard import SQLGuard

            guard = SQLGuard(index_path=metadata_path)
        payload = []
        for record_id, document, metadata in zip(
            ids, documents, metadatas, strict=True
        ):
            item = dict(metadata or {})
            if item.get("source_id") not in {None, "", source_id}:
                raise DataSourceCatalogError("SQL Tool Memory 数据源不匹配")
            if item.get("tool_name") != "run_sql":
                raise DataSourceCatalogError("仅允许保留 run_sql Tool Memory")
            try:
                args = json.loads(str(item["args_json"]))
                sql = str(args["sql"])
            except (KeyError, TypeError, ValueError):
                raise DataSourceCatalogError(
                        "SQL Tool Memory 参数不可解析"
                ) from None
            result = guard.validate(
                sql=sql,
                query=str(item.get("question") or document),
            )
            if not result.passed:
                raise DataSourceCatalogError(
                        "既有 SQL Tool Memory 未通过新范围 SQLGuard"
                )
            item["source_id"] = source_id
            payload.append((str(record_id), str(document), item))
        return payload

    @staticmethod
    def _merge_extra_sql_tool_records(
        preserved: list[tuple[str, str, dict[str, Any]]],
        extra: list[tuple[str, str, dict[str, Any]]],
        *,
        source_id: str,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """合并运行时学习候选提供的额外 SQL Tool Memory。

        结构校验 + 按 record_id 幂等去重；不在此处做 SQLGuard 复检
        （调用方发布前已用当前 Metadata 复检过）。
        """
        existing_ids = {record_id for record_id, _, _ in preserved}
        merged: list[tuple[str, str, dict[str, Any]]] = list(preserved)
        for record_id, document, metadata in extra:
            item = dict(metadata or {})
            if str(item.get("tool_name") or "") != "run_sql":
                raise DataSourceCatalogError(
                    "额外 Tool Memory 仅允许 run_sql"
                )
            if item.get("source_id") not in {None, "", source_id}:
                raise DataSourceCatalogError("额外 Tool Memory 数据源不匹配")
            record_id = str(record_id)
            if record_id in existing_ids:
                continue  # 幂等：重试不重复写入
            merged.append((record_id, str(document), item))
            existing_ids.add(record_id)
        return merged

    def prepare(
        self,
        source_id: str,
        *,
        extra_sql_tool_records: list[tuple[str, str, dict[str, Any]]] | None = None,
        preserve_existing_sql: bool = True,
    ) -> dict[str, Any]:
        lock = _prepare_lock(source_id)
        if not lock.acquire(blocking=False):
            raise DataSourceConflict(
                "该数据源正在生成问数资产，请稍后重试"
            )
        try:
            return self._prepare_locked(
                source_id,
                extra_sql_tool_records=extra_sql_tool_records,
                preserve_existing_sql=preserve_existing_sql,
            )
        finally:
            lock.release()

    def _prepare_locked(
        self,
        source_id: str,
        *,
        extra_sql_tool_records: list[tuple[str, str, dict[str, Any]]] | None = None,
        preserve_existing_sql: bool = True,
    ) -> dict[str, Any]:
        record = self.catalog.require(source_id)
        scope = [dict(item) for item in record.selected_scope]
        if not scope:
            raise DataSourceCatalogError("尚未选择问数范围")
        expected_runtime_revision = record.runtime_revision
        expected_scope_fingerprint = selected_scope_fingerprint(scope)
        expected_status = record.status
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in scope:
            grouped.setdefault(
                (item.get("schema", ""), item["table"]),
                [],
            ).append(item)
        # 前置范围门：allowed_tables 来自审核策略（effective=active 且 present），
        # selected_scope 表集合必须与之精确相等；reviews 为空时失败关闭，
        # 不在此处调用首次迁移（迁移只属于 /review 执行链）。
        policy = self.catalog.review_policy(source_id)
        if policy["review_count"] == 0:
            raise DataSourceCatalogError(
                "数据源尚未完成表准入审核，请先执行 review"
            )
        allowed_tables = set(policy["allowed_tables"])
        scope_tables = set(grouped.keys())
        if scope_tables != allowed_tables:
            missing = sorted(allowed_tables - scope_tables)
            extra = sorted(scope_tables - allowed_tables)
            detail_parts = []
            if missing:
                detail_parts.append(
                    "active+present 但未进入 selected_scope："
                    + "、".join(f"{s}.{t}" for s, t in missing)
                )
            if extra:
                detail_parts.append(
                    "selected_scope 包含非 allowed 表："
                    + "、".join(f"{s}.{t}" for s, t in extra)
                )
            raise DataSourceCatalogError(
                "selected_scope 表集合与审核允许表不一致；"
                + "；".join(detail_parts)
            )
        expected_review_policy_fingerprint = policy["fingerprint"]
        table_indexes: dict[
            tuple[str, str], dict[str, dict[str, Any]]
        ] = {}
        table_primary_members: dict[tuple[str, str], set[str]] = {}
        for item in record.discovered_metadata:
            table_key = (item.get("schema", ""), item["table"])
            indexes = table_indexes.setdefault(table_key, {})
            if item.get("primary_key"):
                table_primary_members.setdefault(table_key, set()).add(
                    str(item["column"])
                )
            for index in item.get("indexes", []):
                indexes[str(index.get("name", ""))] = dict(index)
        ddls: list[str] = []
        for (schema, table), columns in sorted(grouped.items()):
            columns.sort(key=lambda item: item.get("ordinal_position", 0))
            qualified = (
                f"{self._quote(schema, record.database_type)}."
                if schema and record.database_type == "postgresql"
                else ""
            ) + self._quote(table, record.database_type)
            definitions = []
            for item in columns:
                definitions.append(
                    f"  {self._quote(item['column'], record.database_type)} "
                    f"{item['type']}"
                )
            selected_names = {item["column"] for item in columns}
            indexes = dict(table_indexes.get((schema, table), {}))
            for item in columns:
                for index in item.get("indexes", []):
                    indexes[str(index.get("name", ""))] = dict(index)
            primary_members = table_primary_members.get(
                (schema, table),
                set(),
            )
            known_primary_index = next(
                (
                    index
                    for index in indexes.values()
                    if index.get("primary")
                    and index.get("columns")
                    and all(
                        column.get("name")
                        and not column.get("unsupported_expression")
                        for column in index.get("columns", [])
                    )
                    and {
                        str(column["name"])
                        for column in index.get("columns", [])
                    }
                    == primary_members
                ),
                None,
            )
            if primary_members and known_primary_index is None:
                raise DataSourceCatalogError(
                    "当前元数据缺少完整主键索引信息，"
                    "请重新执行“读取表和字段”后再生成问数资产"
                )
            primary_index = (
                known_primary_index
                if known_primary_index is not None
                and all(
                    column.get("name") in selected_names
                    for column in known_primary_index["columns"]
                )
                else None
            )
            primary_columns = (
                [
                    column["name"]
                    for column in primary_index["columns"]
                ]
                if primary_index
                else []
            )
            if primary_columns:
                definitions.append(
                    "  PRIMARY KEY ("
                    + ", ".join(
                        self._quote(name, record.database_type)
                        for name in primary_columns
                    )
                    + ")"
                )
            ddl = (
                f"CREATE TABLE {qualified} (\n"
                + ",\n".join(definitions)
                + "\n);"
            )
            index_statements: list[str] = []
            for index in sorted(
                indexes.values(),
                key=lambda item: str(item.get("name", "")),
            ):
                index_columns = index.get("columns", [])
                if (
                    index.get("primary")
                    or not index_columns
                    or any(
                        column.get("unsupported_expression")
                        or column.get("name") not in selected_names
                        for column in index_columns
                    )
                ):
                    continue
                columns_sql = ", ".join(
                    self._quote(column["name"], record.database_type)
                    + (
                        f" {column['direction']}"
                        if column.get("direction") in {"ASC", "DESC"}
                        else ""
                    )
                    for column in index_columns
                )
                unique = "UNIQUE " if index.get("unique") else ""
                method = (
                    f" USING {index['method']}"
                    if record.database_type == "postgresql"
                    and index.get("method")
                    else ""
                )
                index_statements.append(
                    f"CREATE {unique}INDEX "
                    f"{self._quote(str(index['name']), record.database_type)} "
                    f"ON {qualified}{method} ({columns_sql});"
                )
            if index_statements:
                ddl += "\n" + "\n".join(index_statements)
            ddls.append(ddl)
        documents = self._domain_documents(grouped)
        routing_summary = "\n".join(
            [
                record.display_name,
                record.description,
                record.database_type,
                *documents,
                *record.capabilities,
            ]
        )
        metadata = [
            {
                "table": item["table"],
                "table_comment": item.get("table_comment", ""),
                "column": item["column"],
                "type": item["type"],
                "comment": item.get("comment", ""),
                "nullable": item.get("nullable", True),
                "primary_key": item.get("primary_key", False),
                "indexes": item.get("indexes", []),
                "logical_relations": item.get("logical_relations", []),
                "object_type": item.get("object_type", "table"),
                "schema": item.get("schema", ""),
                "dialect": record.database_type,
                "domain": item.get("domain", ""),
                "grain": item.get("grain", ""),
                "time_column": item.get("time_column", ""),
                "valid_row_rules": item.get("valid_row_rules", []),
                "confidence": item.get("confidence", ""),
            }
            for item in scope
        ]
        return self._publish_assets(
            source_id=source_id,
            record=record,
            metadata=metadata,
            ddls=ddls,
            documents=documents,
            routing_summary=routing_summary,
            expected_runtime_revision=expected_runtime_revision,
            expected_scope_fingerprint=expected_scope_fingerprint,
            expected_status=expected_status,
            expected_review_policy_fingerprint=(
                expected_review_policy_fingerprint
            ),
            extra_sql_tool_records=extra_sql_tool_records,
            preserve_existing_sql=preserve_existing_sql,
        )

    def _publish_assets(
        self,
        *,
        source_id: str,
        record: Any,
        metadata: list[dict[str, Any]],
        ddls: list[str],
        documents: list[str],
        routing_summary: str,
        expected_runtime_revision: int,
        expected_scope_fingerprint: str,
        expected_status: str,
        expected_review_policy_fingerprint: str,
        extra_sql_tool_records: list[tuple[str, str, dict[str, Any]]] | None = None,
        preserve_existing_sql: bool = True,
    ) -> dict[str, Any]:
        return self._publish_assets_crash_safe(
            source_id=source_id,
            record=record,
            metadata=metadata,
            ddls=ddls,
            documents=documents,
            routing_summary=routing_summary,
            expected_runtime_revision=expected_runtime_revision,
            expected_scope_fingerprint=expected_scope_fingerprint,
            expected_status=expected_status,
            expected_review_policy_fingerprint=(
                expected_review_policy_fingerprint
            ),
            extra_sql_tool_records=extra_sql_tool_records,
            preserve_existing_sql=preserve_existing_sql,
        )

    def _publish_assets_crash_safe(
        self,
        *,
        source_id: str,
        record: Any,
        metadata: list[dict[str, Any]],
        ddls: list[str],
        documents: list[str],
        routing_summary: str,
        expected_runtime_revision: int,
        expected_scope_fingerprint: str,
        expected_status: str,
        expected_review_policy_fingerprint: str,
        extra_sql_tool_records: list[tuple[str, str, dict[str, Any]]] | None = None,
        preserve_existing_sql: bool = True,
    ) -> dict[str, Any]:
        target = record.metadata_path.resolve()
        root = target.parent
        root.mkdir(parents=True, exist_ok=True)
        if self.catalog.active_asset_batches(source_id):
            self.asset_cleaner.recover_incomplete_batches(
                source_id, grace_seconds=600
            )
            if self.catalog.active_asset_batches(source_id):
                raise DataSourceConflict(
                    "该数据源正在生成问数资产，请稍后重试"
                )
        batch_id = (
            f"{record.runtime_revision}-{time.time_ns()}-"
            f"{os.urandom(4).hex()}"
        )
        candidate_root = root / f"candidate-{batch_id}"
        memory_base_name = record.memory_path.name.split(".revision-", 1)[0]
        published_memory_path = root / (
            f"{memory_base_name}.revision-"
            f"{record.runtime_revision + 1}-{batch_id}"
        )
        # Chroma 在 Windows 上会持有目录句柄；直接写入唯一目标 revision，
        # 在 Catalog 发布前由 active batch 保护，避免重命名带锁目录。
        candidate_memory = published_memory_path
        candidate_paths = {
            "metadata": candidate_root / target.name,
            "memory": candidate_memory,
            "ddl": candidate_root / "ddl_memories.json",
            "documentation": candidate_root / "business_documents.json",
            "manifest": candidate_root / "asset_manifest.json",
        }
        formal_paths = {
            "metadata": target,
            "memory": published_memory_path,
            "ddl": root / "ddl_memories.json",
            "documentation": root / "business_documents.json",
            "manifest": root / "asset_manifest.json",
        }
        plan = []
        for name in ("metadata", "memory", "ddl", "documentation", "manifest"):
            formal = formal_paths[name]
            plan.append(
                {
                    "name": name,
                    "candidate": str(candidate_paths[name]),
                    "formal": str(formal),
                    "backup": str(
                        formal.with_name(f".{formal.name}.backup-{batch_id}")
                    ),
                    "base_existed": formal.exists(),
                    "base_hash": (
                        self.asset_cleaner._path_hash(formal)
                        if formal.exists()
                        else ""
                    ),
                    "target_hash": "",
                }
            )
        snapshot = {
            "base_runtime_revision": record.runtime_revision,
            "target_runtime_revision": record.runtime_revision + 1,
            "base_status": record.status,
            "base_enabled_for_chat": record.enabled_for_chat,
            "base_routing_summary": record.routing_summary,
            "base_memory_path": str(record.memory_path.resolve()),
            "target_memory_path": str(published_memory_path),
            "base_scope_fingerprint": expected_scope_fingerprint,
            "base_review_policy_fingerprint": (
                expected_review_policy_fingerprint
            ),
            "base_updated_at": record.updated_at,
            "base_last_error": record.last_error,
        }
        self.catalog.begin_asset_batch(
            source_id,
            batch_id=batch_id,
            candidate_root=candidate_root,
            candidate_memory=candidate_memory,
            published_memory_path=published_memory_path,
            snapshot=snapshot,
            asset_plan=plan,
            expected_review_policy_fingerprint=(
                expected_review_policy_fingerprint
            ),
        )
        self._inject("after_batch_registered")
        sql_tool_payload: list[tuple[str, str, dict[str, Any]]] = []
        try:
            candidate_root.mkdir()
            candidate_paths["metadata"].write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._inject("after_candidate_metadata")
            candidate_paths["ddl"].write_text(
                json.dumps(ddls, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._inject("after_candidate_ddl")
            candidate_paths["documentation"].write_text(
                json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._inject("after_candidate_documentation")
            sql_tool_payload = self._preserved_sql_tool_payload(
                source_id=source_id,
                memory_path=(record.memory_path if preserve_existing_sql else None),
                metadata_path=candidate_paths["metadata"],
                database_type=record.database_type,
                generated_records=self.catalog.list_verified_sql_memories(
                    source_id
                ),
            )
            if extra_sql_tool_records:
                sql_tool_payload = self._merge_extra_sql_tool_records(
                    sql_tool_payload,
                    extra_sql_tool_records,
                    source_id=source_id,
                )
            from backend.memory import create_memory

            memory = create_memory(candidate_memory)
            try:
                collection = memory._get_collection()
                payload = [
                    *[
                        (
                            f"DDL\n{ddl}",
                            {
                                "source_id": source_id,
                                "memory_type": "ddl",
                                "content_fingerprint": hashlib.sha256(
                                    ddl.encode("utf-8")
                                ).hexdigest(),
                            },
                        )
                        for ddl in ddls
                    ],
                    *[
                        (
                            document,
                            {
                                "source_id": source_id,
                                "memory_type": "documentation",
                                "content_fingerprint": hashlib.sha256(
                                    document.encode("utf-8")
                                ).hexdigest(),
                            },
                        )
                        for document in documents
                    ],
                ]
                payload_ids = [
                    "b5-"
                    + hashlib.sha256(
                        f"{source_id}|{item_metadata['memory_type']}|{document}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    for document, item_metadata in payload
                ]
                payload.extend(
                    (document, metadata)
                    for _, document, metadata in sql_tool_payload
                )
                payload_ids.extend(
                    record_id for record_id, _, _ in sql_tool_payload
                )
                if len(payload_ids) != len(set(payload_ids)):
                    raise DataSourceCatalogError("候选 Memory 记录 ID 重复")
                collection.add(
                    ids=payload_ids,
                    documents=[document for document, _ in payload],
                    metadatas=[item_metadata for _, item_metadata in payload],
                )
                if collection.count() != len(payload):
                    raise DataSourceCatalogError("候选 Memory 验证失败")
            finally:
                if not self._close_memory(memory):
                    raise DataSourceCatalogError("候选 Memory 资源释放失败")
            (candidate_memory / ".asset_identity.json").write_text(
                json.dumps(
                    {
                        "source_id": source_id,
                        "runtime_revision": record.runtime_revision + 1,
                        "scope_fingerprint": expected_scope_fingerprint,
                        "review_policy_fingerprint": (
                            expected_review_policy_fingerprint
                        ),
                        "batch_id": batch_id,
                        "memory_count": len(payload),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self._inject("after_candidate_memory")
            content_hashes = {
                name: self.asset_cleaner._path_hash(candidate_paths[name])
                for name in ("metadata", "memory", "ddl", "documentation")
            }
            candidate_paths["manifest"].write_text(
                json.dumps(
                    {
                        "source_id": source_id,
                        "runtime_revision": record.runtime_revision + 1,
                        "scope_fingerprint": expected_scope_fingerprint,
                        "review_policy_fingerprint": (
                            expected_review_policy_fingerprint
                        ),
                        "metadata_hash": content_hashes["metadata"],
                        "memory_identity_hash": content_hashes["memory"],
                        "ddl_hash": content_hashes["ddl"],
                        "business_documents_hash": content_hashes[
                            "documentation"
                        ],
                        "created_at": int(time.time()),
                        "batch_id": batch_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            for asset in plan:
                asset["target_hash"] = self.asset_cleaner._path_hash(
                    Path(asset["candidate"])
                )
            self.catalog.replace_asset_batch_plan(source_id, batch_id, plan)
            current = self.catalog.require(source_id)
            if (
                current.runtime_revision != expected_runtime_revision
                or current.status != expected_status
                or selected_scope_fingerprint(current.selected_scope)
                != expected_scope_fingerprint
            ):
                raise DataSourceConflict(
                    "数据源范围已变化，请重新生成问数资产"
                )
            # 候选资产门：候选全部生成后、备份正式资产前重检审核策略，
            # 防止候选按旧 allowed_tables 构建后 policy 被修改仍继续发布。
            current_policy = self.catalog.review_policy(source_id)
            if (
                current_policy["fingerprint"]
                != expected_review_policy_fingerprint
            ):
                raise DataSourceConflict(
                    "审核策略已变化，请重新生成问数资产"
                )
            # E-2A：读取已落盘的候选 Metadata / DDL 并做结构化硬校验，
            # 通过后才允许备份正式资产。
            from backend.data_source_asset_validator import (
                validate_candidate_assets,
            )

            validate_candidate_assets(
                database_type=record.database_type,
                database_name=record.database_name,
                selected_scope=current.selected_scope,
                allowed_tables=current_policy["allowed_tables"],
                metadata_path=candidate_paths["metadata"],
                ddl_path=candidate_paths["ddl"],
            )
            backups = [
                Path(asset["backup"])
                for asset in plan
                if bool(asset["base_existed"])
            ]
            self.catalog.update_asset_batch(
                source_id,
                batch_id,
                phase="backing_up",
                backup_paths=backups,
            )
            backed_up = []
            for asset in plan:
                if not asset["base_existed"]:
                    continue
                os.replace(asset["formal"], asset["backup"])
                backed_up.append(asset["name"])
                self.catalog.update_asset_batch(
                    source_id,
                    batch_id,
                    backed_up_assets=backed_up,
                )
                self._inject(f"after_backup_{asset['name']}")
            self.catalog.update_asset_batch(
                source_id, batch_id, phase="installing"
            )
            installed = []
            for asset in plan:
                if Path(asset["candidate"]).resolve() != Path(
                    asset["formal"]
                ).resolve():
                    os.replace(asset["candidate"], asset["formal"])
                installed.append(asset["name"])
                self.catalog.update_asset_batch(
                    source_id,
                    batch_id,
                    installed_assets=installed,
                )
                self._inject(f"after_install_{asset['name']}")
            self._inject("before_catalog_publish")
            published = self.catalog.publish(
                source_id,
                routing_summary=routing_summary,
                memory_path=published_memory_path,
                expected_runtime_revision=expected_runtime_revision,
                expected_scope_fingerprint=expected_scope_fingerprint,
                expected_status=expected_status,
                expected_review_policy_fingerprint=(
                    expected_review_policy_fingerprint
                ),
            )
            self.catalog.update_asset_batch(
                source_id, batch_id, phase="catalog_published"
            )
            self._inject("after_catalog_publish")
            if self.runtime_manager is not None:
                runtime = self.runtime_manager.require(source_id)
                self._inject("after_runtime_build")
                if (
                    runtime.config.memory_path.resolve()
                    != published.memory_path.resolve()
                    or self.runtime_manager.runtime_revision(source_id)
                    != published.runtime_revision
                ):
                    raise DataSourceCatalogError(
                        "新 Runtime 与 Catalog 版本不一致"
                    )
                self._inject("after_runtime_swap")
            self.catalog.update_asset_batch(
                source_id, batch_id, phase="runtime_validated"
            )
            self.catalog.update_asset_batch(
                source_id, batch_id, phase="committed"
            )
            self._inject("before_backup_cleanup")
            latest = self.catalog.active_asset_batches(source_id)[0]
            self.asset_cleaner._finish_recovered_batch(latest, plan)
        except SimulatedProcessCrash:
            raise
        except Exception:
            batches = self.catalog.active_asset_batches(source_id)
            if batches:
                try:
                    self.asset_cleaner._rollback_batch(batches[0])
                except SimulatedProcessCrash:
                    raise
                except Exception as rollback_error:
                    self.catalog.update_asset_batch(
                        source_id,
                        batch_id,
                        phase="rollback_failed",
                        last_error=(
                            f"{type(rollback_error).__name__}: "
                            "发布回滚失败"
                        ),
                    )
                    self.catalog.mark_recovery_failed(
                        source_id,
                        "数据源发布回滚失败，请重启服务执行恢复",
                    )
                    raise DataSourceCatalogError(
                        "问数资产发布失败，旧版恢复尚未完成"
                    ) from None
            raise

        previous_memory_path = record.memory_path.resolve()
        if (
            previous_memory_path != published_memory_path.resolve()
            and self.asset_cleaner._asset_type(previous_memory_path)
            == "memory_revision"
        ):
            self.catalog.register_pending_cleanup(
                source_id,
                previous_memory_path,
                "memory_revision",
                "等待旧 Runtime 确认释放",
            )
            self.asset_cleaner.retry_pending_cleanup(source_id)
        self.asset_cleaner.cleanup_superseded_assets(source_id)
        return {
            "source_id": source_id,
            "metadata_records": len(metadata),
            "ddl_count": len(ddls),
            "documentation_count": len(documents),
            "sql_tool_memory_count": len(sql_tool_payload),
            "memory_count": len(ddls) + len(documents) + len(sql_tool_payload),
            "runtime_revision": published.runtime_revision,
            "status": published.status,
        }

    @staticmethod
    def _remove_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    @classmethod
    def _cleanup_path(cls, path: Path) -> None:
        try:
            cls._remove_path(path)
        except Exception:
            # 清理候选或本批次备份失败不能破坏已发布的正式资产。
            pass

    @staticmethod
    def _close_memory(memory: Any) -> bool:
        """通过客户端引用计数释放本实例，不直接停止 Chroma 共享系统。"""
        succeeded = True
        try:
            memory._executor.shutdown(wait=True)
        except Exception:
            succeeded = False
        try:
            memory._collection = None
        except Exception:
            succeeded = False
        client = getattr(memory, "_client", None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                succeeded = False
        try:
            memory._client = None
        except Exception:
            succeeded = False
        return succeeded

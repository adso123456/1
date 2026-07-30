"""MySQL/PostgreSQL 只读连接测试、元数据发现和候选资产发布。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import hashlib
import gc
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError
from backend.mysql_tls import build_mysql_tls_settings
from config.settings import PROJECT_ROOT

if TYPE_CHECKING:
    from backend.data_source_runtime_manager import DataSourceRuntimeManager


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

    def discover(self, source_id: str) -> list[dict[str, Any]]:
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
    ) -> None:
        self.catalog = catalog
        self.runtime_manager = runtime_manager

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
        }
        active = (
            self.runtime_manager.active_asset_paths(source_id)
            if self.runtime_manager is not None
            else frozenset()
        )
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
                    "旧 Runtime 仍在使用该资产",
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
        source_ids = (
            (source_id,)
            if source_id is not None
            else tuple(
                sorted(
                    {
                        str(item["source_id"])
                        for item in self.catalog.pending_cleanups()
                    }
                )
            )
        )
        for pending_source_id in source_ids:
            try:
                self.cleanup_superseded_assets(pending_source_id)
            except Exception:
                for item in self.catalog.pending_cleanups(pending_source_id):
                    self.catalog.fail_pending_cleanup(
                        pending_source_id,
                        Path(str(item["path"])),
                        "待清理重试失败",
                    )


class DataSourceAssetPreparer:
    """生成隔离候选 Metadata/DDL/基础文档并原子发布。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        runtime_manager: "DataSourceRuntimeManager | None" = None,
    ) -> None:
        self.catalog = catalog
        self.runtime_manager = runtime_manager
        self.asset_cleaner = DataSourceAssetCleaner(
            catalog,
            runtime_manager,
        )

    @staticmethod
    def _quote(name: str, dialect: str) -> str:
        quote = "`" if dialect == "mysql" else '"'
        return quote + name.replace(quote, quote * 2) + quote

    def prepare(self, source_id: str) -> dict[str, Any]:
        record = self.catalog.require(source_id)
        scope = [dict(item) for item in record.selected_scope]
        if not scope:
            raise DataSourceCatalogError("尚未选择问数范围")
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in scope:
            grouped.setdefault(
                (item.get("schema", ""), item["table"]),
                [],
            ).append(item)
        table_indexes: dict[
            tuple[str, str], dict[str, dict[str, Any]]
        ] = {}
        for item in record.discovered_metadata:
            table_key = (item.get("schema", ""), item["table"])
            indexes = table_indexes.setdefault(table_key, {})
            for index in item.get("indexes", []):
                indexes[str(index.get("name", ""))] = dict(index)
        ddls: list[str] = []
        documents: list[str] = []
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
            primary_columns = [
                item["column"] for item in columns if item.get("primary_key")
            ]
            known_primary_index = next(
                (
                    index
                    for index in indexes.values()
                    if index.get("primary")
                ),
                None,
            )
            primary_index = next(
                (
                    index
                    for index in indexes.values()
                    if index.get("primary")
                    and all(
                        column.get("name") in selected_names
                        and not column.get("unsupported_expression")
                        for column in index.get("columns", [])
                    )
                ),
                None,
            )
            if primary_index:
                primary_columns = [
                    column["name"]
                    for column in primary_index["columns"]
                ]
            elif known_primary_index:
                primary_columns = []
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
            table_comment = columns[0].get("table_comment", "")
            documents.append(
                f"数据源“{record.display_name}”中的{table}表"
                f"{'（' + table_comment + '）' if table_comment else ''}；"
                "字段包括："
                + "、".join(
                    f"{item['column']}"
                    f"{'（' + item['comment'] + '）' if item.get('comment') else ''}"
                    for item in columns
                )
                + "。"
            )
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
                "logical_relations": [],
                "object_type": item.get("object_type", "table"),
                "schema": item.get("schema", ""),
                "dialect": record.database_type,
            }
            for item in scope
        ]
        target = record.metadata_path
        target.parent.mkdir(parents=True, exist_ok=True)
        record.memory_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_root = Path(
            tempfile.mkdtemp(prefix="candidate-", dir=target.parent)
        )
        candidate_memory = (
            record.memory_path.parent
            / f".{record.memory_path.name}.candidate-{os.urandom(6).hex()}"
        )
        batch_id = f"{record.runtime_revision}-{time.time_ns()}"
        memory_base_name = record.memory_path.name.split(".revision-", 1)[0]
        published_memory_path = record.memory_path.with_name(
            f"{memory_base_name}.revision-"
            f"{record.runtime_revision + 1}-{time.time_ns()}"
        )
        backups: dict[Path, Path] = {}
        installed: list[Path] = []
        try:
            candidate_metadata = candidate_root / target.name
            candidate_metadata.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (candidate_root / "ddl_memories.json").write_text(
                json.dumps(ddls, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (candidate_root / "business_documents.json").write_text(
                json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            json.loads(candidate_metadata.read_text(encoding="utf-8"))
            from backend.memory import create_memory

            memory = create_memory(candidate_memory)
            try:
                collection = memory._get_collection()
                documents_payload = [
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
                collection.add(
                    ids=[
                        "b5-" + hashlib.sha256(
                            f"{source_id}|{metadata['memory_type']}|{document}".encode(
                                "utf-8"
                            )
                        ).hexdigest()
                        for document, metadata in documents_payload
                    ],
                    documents=[
                        document for document, _ in documents_payload
                    ],
                    metadatas=[
                        metadata for _, metadata in documents_payload
                    ],
                )
                if collection.count() != len(documents_payload):
                    raise DataSourceCatalogError("候选 Memory 验证失败")
            finally:
                self._close_memory(memory)

            assets = (
                (candidate_metadata, target),
                (candidate_memory, published_memory_path),
                (
                    candidate_root / "ddl_memories.json",
                    target.parent / "ddl_memories.json",
                ),
                (
                    candidate_root / "business_documents.json",
                    target.parent / "business_documents.json",
                ),
            )
            try:
                for _, final_path in assets:
                    if final_path.exists():
                        backup = final_path.with_name(
                            f".{final_path.name}.backup-{batch_id}"
                        )
                        os.replace(final_path, backup)
                        backups[final_path] = backup
                for candidate_path, final_path in assets:
                    os.replace(candidate_path, final_path)
                    installed.append(final_path)
                published = self.catalog.publish(
                    source_id,
                    routing_summary=routing_summary,
                    memory_path=published_memory_path,
                )
                if self.runtime_manager is not None:
                    self.runtime_manager.invalidate(source_id)
                    self.runtime_manager.require(source_id)
            except Exception:
                rollback_errors: list[Exception] = []
                for final_path in reversed(installed):
                    try:
                        self._remove_path(final_path)
                    except Exception as exc:
                        rollback_errors.append(exc)
                for final_path, backup in backups.items():
                    try:
                        if backup.exists():
                            os.replace(backup, final_path)
                    except Exception as exc:
                        rollback_errors.append(exc)
                try:
                    self.catalog.restore_publication_state(source_id, record)
                except Exception as exc:
                    rollback_errors.append(exc)
                if self.runtime_manager is not None:
                    try:
                        self.runtime_manager.invalidate(source_id)
                        if record.status == "ready" and record.enabled_for_chat:
                            self.runtime_manager.require(source_id)
                    except Exception as exc:
                        rollback_errors.append(exc)
                if rollback_errors:
                    raise DataSourceCatalogError(
                        "问数资产发布失败，旧资产补偿恢复不完整"
                    ) from None
                raise
            for backup in backups.values():
                self._cleanup_path(backup)
                if backup.exists():
                    self.catalog.register_pending_cleanup(
                        source_id,
                        backup,
                        "backup",
                        "发布成功后备份清理失败",
                    )
            self.asset_cleaner.cleanup_superseded_assets(source_id)
            return {
                "source_id": source_id,
                "metadata_records": len(metadata),
                "ddl_count": len(ddls),
                "documentation_count": len(documents),
                "sql_tool_memory_count": 0,
                "memory_count": len(ddls) + len(documents),
                "runtime_revision": published.runtime_revision,
                "status": published.status,
            }
        finally:
            self._cleanup_path(candidate_root)
            self._cleanup_path(candidate_memory)
            for path, asset_type in (
                (candidate_root, "candidate"),
                (candidate_memory, "candidate"),
            ):
                if path.exists():
                    self.catalog.register_pending_cleanup(
                        source_id,
                        path,
                        asset_type,
                        "候选资产清理失败",
                    )

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
    def _close_memory(memory: Any) -> None:
        try:
            memory._executor.shutdown(wait=True)
        except Exception:
            pass
        try:
            if memory._client is not None:
                memory._client._system.stop()
        except Exception:
            pass
        memory._collection = None
        memory._client = None
        gc.collect()
        try:
            from chromadb.api.client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:
            pass

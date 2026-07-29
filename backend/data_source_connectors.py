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
from typing import Any

from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError


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
                connection.rollback()
            finally:
                cursor.close()
            metadata = [
                {
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
                    "indexes": [],
                    "logical_relations": [],
                }
                for row in rows
            ]
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


class DataSourceAssetPreparer:
    """生成隔离候选 Metadata/DDL/基础文档并原子发布。"""

    def __init__(self, catalog: DataSourceCatalog) -> None:
        self.catalog = catalog

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
                suffix = " PRIMARY KEY" if item.get("primary_key") else ""
                definitions.append(
                    f"  {self._quote(item['column'], record.database_type)} "
                    f"{item['type']}{suffix}"
                )
            ddls.append(
                f"CREATE TABLE {qualified} (\n"
                + ",\n".join(definitions)
                + "\n);"
            )
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
        metadata_backup: Path | None = None
        memory_backup: Path | None = None
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

            stamp = f"{record.runtime_revision}-{int(time.time())}"
            if target.exists():
                metadata_backup = target.with_name(
                    f"{target.name}.backup-{stamp}"
                )
                os.replace(target, metadata_backup)
            if record.memory_path.exists():
                memory_backup = record.memory_path.with_name(
                    f"{record.memory_path.name}.backup-{stamp}"
                )
                os.replace(record.memory_path, memory_backup)
            try:
                os.replace(candidate_metadata, target)
                os.replace(candidate_memory, record.memory_path)
                os.replace(
                    candidate_root / "ddl_memories.json",
                    target.parent / "ddl_memories.json",
                )
                os.replace(
                    candidate_root / "business_documents.json",
                    target.parent / "business_documents.json",
                )
            except Exception:
                if target.exists():
                    target.unlink()
                if metadata_backup is not None and metadata_backup.exists():
                    os.replace(metadata_backup, target)
                if record.memory_path.exists():
                    shutil.rmtree(record.memory_path, ignore_errors=True)
                if memory_backup is not None and memory_backup.exists():
                    os.replace(memory_backup, record.memory_path)
                raise
            published = self.catalog.publish(
                source_id,
                routing_summary=routing_summary,
            )
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
            shutil.rmtree(candidate_root, ignore_errors=True)
            shutil.rmtree(candidate_memory, ignore_errors=True)

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

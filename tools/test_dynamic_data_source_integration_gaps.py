"""B5 返工：TLS、迁移、动态推荐、索引与发布补偿定向回归。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import CredentialCipher, DataSourceCatalog
from backend.data_source_connectors import (
    DataSourceAssetPreparer,
    DirectDatabaseConnector,
    _group_mysql_indexes,
    _group_postgresql_indexes,
)
from backend.data_source_suggestion import DataSourceSuggestionService
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.mysql_tls import (
    MySQLTLSConfigurationError,
    build_mysql_tls_settings,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def _catalog(root: Path) -> DataSourceCatalog:
    catalog = DataSourceCatalog(
        root / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        environ={
            "PG_USER": "test-user",
            "PG_PASSWORD": "test-password",
            "MYSQL_USER": "test-user",
            "MYSQL_PASSWORD": "test-password",
        },
    )
    catalog.initialize(
        [
            {
                "source_id": "postgresql-main",
                "display_name": "排污口治理数据",
                "description": "排污口、整治、溯源",
                "database_type": "postgresql",
                "host": "127.0.0.1",
                "port": 5433,
                "database_name": "test",
                "schema_name": "public",
                "credential_reference": {
                    "username": "PG_USER",
                    "password": "PG_PASSWORD",
                },
                "metadata_path": root / "pg.json",
                "memory_path": root / "pg-memory",
                "routing_summary": "排污口 outlet 整治 溯源",
                "selected_tables_count": 1,
                "selected_columns_count": 1,
            },
            {
                "source_id": "mysql-lzh-monitor",
                "display_name": "水质监测数据",
                "description": "断面、水质、监测站",
                "database_type": "mysql",
                "host": "127.0.0.1",
                "port": 3306,
                "database_name": "test",
                "credential_reference": {
                    "username": "MYSQL_USER",
                    "password": "MYSQL_PASSWORD",
                },
                "metadata_path": root / "mysql.json",
                "memory_path": root / "mysql-memory",
                "routing_summary": "水质 断面 监测站 氨氮 总磷",
                "capabilities": [
                    "water_quality_daily_report",
                    "water_quality_monthly_report",
                ],
                "selected_tables_count": 1,
                "selected_columns_count": 1,
            },
        ]
    )
    return catalog


def _add_ready_source(
    catalog: DataSourceCatalog,
    *,
    name: str,
    description: str,
    metadata: list[dict],
    routing_summary: str,
) -> str:
    record = catalog.create(
        display_name=name,
        description=description,
        database_type="postgresql",
        host="127.0.0.1",
        port=5433,
        database_name="test",
        schema_name="public",
        username="temporary-user",
        password="temporary-password",
    )
    catalog.mark_connection_test(record.source_id, success=True)
    catalog.save_discovery(record.source_id, metadata)
    catalog.save_scope(record.source_id, metadata)
    catalog.publish(record.source_id, routing_summary=routing_summary)
    return record.source_id


def _hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


class _FakeCollection:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.total = 0

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
        (self.root / "identity.json").write_text(
            json.dumps(
                {"ids": ids, "documents": documents, "metadatas": metadatas},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def count(self) -> int:
        return self.total


class _FakeMemory:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.collection = _FakeCollection(path)
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None
        self._collection = self.collection

    def _get_collection(self):
        return self.collection


def test_tls_and_migration(root: Path) -> None:
    catalog = _catalog(root)
    check(
        set(catalog.require("postgresql-main").safe_summary_dict())
        == {
            "source_id",
            "display_name",
            "description",
            "database_type",
            "status",
            "enabled_for_chat",
            "selected_tables_count",
            "selected_columns_count",
        },
        "普通数据源 API 摘要不包含连接、凭据、路径或底层错误",
    )
    with sqlite3.connect(catalog.db_path) as connection:
        connection.execute(
            "UPDATE system_schema_versions SET version = 1 "
            "WHERE component = 'data_source_catalog'"
        )
        for name in (
            "mysql_tls_mode",
            "ssl_ca_path",
            "ssl_cert_path",
            "ssl_key_path",
        ):
            connection.execute(f"ALTER TABLE data_sources DROP COLUMN {name}")
    catalog.initialize()
    migrated = catalog.require("mysql-lzh-monitor")
    check(
        migrated.mysql_tls_mode == "disabled",
        "SQLite v1 自动迁移到 v2 且保持原 MySQL 非 TLS 行为",
    )

    ca = root / "ca.pem"
    cert = root / "client.pem"
    key = root / "client.key"
    for path in (ca, cert, key):
        path.write_text("test-only", encoding="utf-8")
    check(
        build_mysql_tls_settings(mode="disabled") == {},
        "MySQL TLS disabled 不传 SSL 参数",
    )
    required = build_mysql_tls_settings(mode="required")
    check(
        required["ssl_verify_cert"] is False and "ssl" in required,
        "MySQL TLS required 构造有效加密连接参数",
    )
    verified = build_mysql_tls_settings(
        mode="verify_identity",
        ca_path=str(ca),
        cert_path=str(cert),
        key_path=str(key),
    )
    check(
        verified["ssl_verify_cert"] is True
        and verified["ssl_verify_identity"] is True,
        "MySQL verify_identity 启用 CA 与主机身份校验",
    )
    for kwargs in (
        {"mode": "verify_ca"},
        {"mode": "required", "cert_path": str(cert)},
    ):
        try:
            build_mysql_tls_settings(**kwargs)
        except MySQLTLSConfigurationError:
            pass
        else:
            raise AssertionError("无效 MySQL TLS 配置未被拒绝")
    check(True, "缺少 CA 或 cert/key 不成对时拒绝配置")

    dynamic = catalog.create(
        display_name="TLS MySQL",
        description="TLS 参数一致性测试",
        database_type="mysql",
        host="127.0.0.1",
        port=3306,
        database_name="test",
        username="temporary-user",
        password="temporary-password",
        mysql_tls_mode="verify_ca",
        ssl_ca_path=str(ca),
    )
    expected = {
        key: value
        for key, value in catalog.runtime_config(
            dynamic.source_id
        ).connection_settings.items()
        if key.startswith("ssl")
    }
    captured: dict = {}
    with patch("pymysql.connect", side_effect=lambda **kwargs: captured.update(kwargs)):
        DirectDatabaseConnector(catalog)._connect(dynamic.source_id)
    actual = {key: value for key, value in captured.items() if key.startswith("ssl")}
    check(actual == expected, "测试连接与正式 Runtime 使用同一套 MySQL TLS 参数")


def test_dynamic_suggestions(root: Path) -> None:
    catalog = _catalog(root)
    service = DataSourceSuggestionService(catalog)
    report = service.suggest(
        "生成2025年7月28日水质日报",
        "postgresql-main",
    )
    check(
        report is not None
        and report["suggestions"][0]["source_id"] == "mysql-lzh-monitor",
        "报表 capability 精确匹配目录中注册能力的数据源",
    )
    outlet = service.suggest(
        "查询排污口整治和溯源情况",
        "mysql-lzh-monitor",
    )
    check(
        outlet is not None
        and outlet["suggestions"][0]["source_id"] == "postgresql-main",
        "排污口语义通过 Metadata 路由摘要推荐而非固定源映射",
    )
    weather_metadata = [
        {
            "schema": "public",
            "table": "weather_observation",
            "object_type": "table",
            "table_comment": "气象站逐小时观测",
            "column": "temperature",
            "type": "numeric",
            "comment": "气温",
            "nullable": True,
            "primary_key": False,
            "ordinal_position": 1,
            "indexes": [],
        },
        {
            "schema": "public",
            "table": "weather_observation",
            "object_type": "table",
            "table_comment": "气象站逐小时观测",
            "column": "rainfall",
            "type": "numeric",
            "comment": "降雨量",
            "nullable": True,
            "primary_key": False,
            "ordinal_position": 2,
            "indexes": [],
        },
    ]
    weather_id = _add_ready_source(
        catalog,
        name="动态气象数据",
        description="气象站温度、降雨量和风速",
        metadata=weather_metadata,
        routing_summary="气象 weather temperature rainfall 气温 降雨量 风速",
    )
    weather_record = catalog.require(weather_id)
    renamed_with_unchanged_connection = catalog.update(
        weather_id,
        display_name="动态气象数据新名称",
        host=weather_record.host,
        port=weather_record.port,
        database_name=weather_record.database_name,
        schema_name=weather_record.schema_name,
        ssl_mode=weather_record.ssl_mode,
        connect_timeout=weather_record.connect_timeout,
    )
    check(
        renamed_with_unchanged_connection.status == "ready"
        and renamed_with_unchanged_connection.runtime_revision
        == weather_record.runtime_revision,
        "重命名时回传未变化连接字段不会错误失效问数资产",
    )
    suggestion = service.suggest(
        "查询气象站的温度和降雨量",
        "postgresql-main",
    )
    check(
        suggestion is not None
        and suggestion["suggestions"][0]["source_id"] == weather_id,
        "普通评分可以推荐新建 ds_* 动态数据源",
    )
    check(
        service.suggest(
            "查询气象站的温度和降雨量",
            weather_id,
        )
        is None,
        "当前源已经高匹配时不推荐其他数据源",
    )
    check(
        service.suggest(
            "查询气象站的温度和降雨量",
            "postgresql-main",
            allowed_source_ids=("postgresql-main",),
        )
        is None,
        "Widget 未授权动态源不进入评分且不泄露名称",
    )
    second_id = _add_ready_source(
        catalog,
        name="另一气象数据",
        description="气象站温度、降雨量和风速",
        metadata=weather_metadata,
        routing_summary="气象 weather temperature rainfall 气温 降雨量 风速",
    )
    check(
        service.suggest(
            "查询气象站的温度和降雨量",
            "postgresql-main",
        )
        is None,
        "最佳与第二候选差距不足时不推荐",
    )
    catalog.set_enabled(second_id, False)
    renamed = catalog.update(weather_id, display_name="最新气象名称")
    suggestion = service.suggest(
        "查询气象站的温度和降雨量",
        "postgresql-main",
    )
    check(
        suggestion is not None
        and suggestion["suggestions"][0]["display_name"]
        == renamed.display_name,
        "停用源退出候选且推荐显示最新名称",
    )


def test_index_grouping() -> None:
    mysql = _group_mysql_indexes(
        [
            {
                "schema_name": "db",
                "table_name": "monitor",
                "index_name": "PRIMARY",
                "non_unique": 0,
                "position": 1,
                "column_name": "id",
                "collation": "A",
                "index_type": "BTREE",
            },
            {
                "schema_name": "db",
                "table_name": "monitor",
                "index_name": "uq_station",
                "non_unique": 0,
                "position": 1,
                "column_name": "station_code",
                "collation": "A",
                "index_type": "BTREE",
            },
            {
                "schema_name": "db",
                "table_name": "monitor",
                "index_name": "idx_station_time",
                "non_unique": 1,
                "position": 2,
                "column_name": "monitor_time",
                "collation": "D",
                "index_type": "BTREE",
            },
            {
                "schema_name": "db",
                "table_name": "monitor",
                "index_name": "idx_station_time",
                "non_unique": 1,
                "position": 1,
                "column_name": "station_id",
                "collation": "A",
                "index_type": "BTREE",
            },
        ]
    )
    indexes = mysql[("db", "monitor")]
    composite = next(item for item in indexes if item["name"] == "idx_station_time")
    check(
        indexes[0]["primary"]
        and any(item["unique"] and not item["primary"] for item in indexes)
        and [item["name"] for item in composite["columns"]]
        == ["station_id", "monitor_time"]
        and composite["columns"][1]["direction"] == "DESC",
        "MySQL 主键、普通复合索引、字段顺序和方向正确",
    )
    check(
        _group_mysql_indexes([]) == {},
        "MySQL 无索引表正常返回空列表",
    )

    postgres = _group_postgresql_indexes(
        [
            {
                "schema_name": "public",
                "table_name": "sample",
                "index_name": "sample_pkey",
                "is_unique": True,
                "is_primary": True,
                "position": 1,
                "column_name": "id",
                "expression": "",
                "index_method": "btree",
                "direction": "ASC",
            },
            {
                "schema_name": "public",
                "table_name": "sample",
                "index_name": "sample_code_key",
                "is_unique": True,
                "is_primary": False,
                "position": 1,
                "column_name": "code",
                "expression": "",
                "index_method": "btree",
                "direction": "ASC",
            },
            {
                "schema_name": "public",
                "table_name": "sample",
                "index_name": "sample_time_code_idx",
                "is_unique": False,
                "is_primary": False,
                "position": 2,
                "column_name": "code",
                "expression": "",
                "index_method": "btree",
                "direction": "DESC",
            },
            {
                "schema_name": "public",
                "table_name": "sample",
                "index_name": "sample_time_code_idx",
                "is_unique": False,
                "is_primary": False,
                "position": 1,
                "column_name": "created_at",
                "expression": "",
                "index_method": "btree",
                "direction": "ASC",
            },
            {
                "schema_name": "public",
                "table_name": "sample",
                "index_name": "sample_lower_idx",
                "is_unique": False,
                "is_primary": False,
                "position": 1,
                "column_name": None,
                "expression": "lower(name)",
                "index_method": "btree",
                "direction": "ASC",
            },
        ]
    )
    expression = next(
        item
        for item in postgres[("public", "sample")]
        if item["name"] == "sample_lower_idx"
    )
    pg_indexes = postgres[("public", "sample")]
    pg_composite = next(
        item for item in pg_indexes if item["name"] == "sample_time_code_idx"
    )
    check(
        expression["columns"][0]["unsupported_expression"] is True
        and any(item["primary"] for item in pg_indexes)
        and any(item["unique"] and not item["primary"] for item in pg_indexes)
        and [item["name"] for item in pg_composite["columns"]]
        == ["created_at", "code"]
        and pg_composite["columns"][1]["direction"] == "DESC",
        "PostgreSQL 主键、唯一索引、复合顺序和表达式安全标记正确",
    )


def test_publish_compensation(root: Path) -> None:
    catalog = _catalog(root)
    metadata = [
        {
            "schema": "public",
            "table": "safe_table",
            "object_type": "table",
            "table_comment": "安全表",
            "column": "id",
            "type": "bigint",
            "comment": "主键",
            "nullable": False,
            "primary_key": True,
            "ordinal_position": 1,
            "indexes": [
                {
                    "name": "safe_table_pkey",
                    "unique": True,
                    "primary": True,
                    "method": "btree",
                    "columns": [
                        {
                            "name": "id",
                            "position": 1,
                            "direction": "ASC",
                        }
                    ],
                },
                {
                    "name": "safe_table_id_unique",
                    "unique": True,
                    "primary": False,
                    "method": "btree",
                    "columns": [
                        {
                            "name": "id",
                            "position": 1,
                            "direction": "ASC",
                        }
                    ],
                },
            ],
        }
    ]
    source_id = _add_ready_source(
        catalog,
        name="发布补偿源",
        description="发布补偿",
        metadata=metadata,
        routing_summary="旧路由摘要",
    )
    record = catalog.require(source_id)
    preparer = DataSourceAssetPreparer(catalog)
    import backend.memory as memory_module

    with patch.object(memory_module, "create_memory", side_effect=_FakeMemory):
        preparer.prepare(source_id)
        before = catalog.require(source_id)
        paths = (
            before.metadata_path,
            before.memory_path,
            before.metadata_path.parent / "ddl_memories.json",
            before.metadata_path.parent / "business_documents.json",
        )
        hashes = tuple(_hash_path(path) for path in paths)
        ddl_text = (before.metadata_path.parent / "ddl_memories.json").read_text(
            encoding="utf-8"
        )
        check(
            "PRIMARY KEY" in ddl_text and "CREATE UNIQUE INDEX" in ddl_text,
            "DDL Memory 正确包含主键与可安全表示的索引",
        )
        real_publish = catalog.publish

        def fail_after_commit(*args, **kwargs):
            real_publish(*args, **kwargs)
            raise sqlite3.OperationalError("injected catalog failure")

        with patch.object(catalog, "publish", side_effect=fail_after_commit):
            try:
                preparer.prepare(source_id)
            except sqlite3.OperationalError:
                pass
            else:
                raise AssertionError("catalog.publish 故障未传播")
        after = catalog.require(source_id)
        check(
            tuple(_hash_path(path) for path in paths) == hashes,
            "catalog.publish 失败后 Metadata、Memory、DDL 和业务文档哈希不变",
        )
        check(
            (
                after.status,
                after.enabled_for_chat,
                after.runtime_revision,
                after.routing_summary,
                after.updated_at,
                after.last_error,
            )
            == (
                before.status,
                before.enabled_for_chat,
                before.runtime_revision,
                before.routing_summary,
                before.updated_at,
                before.last_error,
            ),
            "catalog.publish 失败后状态、路由摘要和 revision 完整恢复",
        )
        leftovers = list(before.metadata_path.parent.glob(".*.backup-*"))
        check(not leftovers, "失败补偿后无活动候选或本批次备份")

        sentinel_runtime = object()
        manager = DataSourceRuntimeManager(
            __import__(
                "backend.data_source_registry",
                fromlist=["DataSourceRegistry"],
            ).DataSourceRegistry.from_catalog(catalog),
            {"mysql": lambda config: None, "postgresql": lambda config: None},
        )
        manager._runtimes[source_id] = sentinel_runtime
        manager._runtime_revisions[source_id] = after.runtime_revision

        real_replace = os.replace
        failure_cases = (
            (
                "Metadata",
                lambda source, destination: (
                    source.name == before.metadata_path.name
                    and source.parent.name.startswith("candidate-")
                    and destination == before.metadata_path
                ),
            ),
            (
                "Memory",
                lambda source, destination: (
                    ".candidate-" in source.name
                    and ".revision-" in destination.name
                ),
            ),
            (
                "DDL",
                lambda source, destination: (
                    source.name == "ddl_memories.json"
                    and source.parent.name.startswith("candidate-")
                ),
            ),
            (
                "Business Documentation",
                lambda source, destination: (
                    source.name == "business_documents.json"
                    and source.parent.name.startswith("candidate-")
                ),
            ),
        )
        for label, should_fail in failure_cases:
            catalog.update(source_id, description=f"{label} 新候选内容")
            snapshot = catalog.require(source_id)
            hashes = tuple(_hash_path(path) for path in paths)

            def injected_replace(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if should_fail(source_path, destination_path):
                    raise OSError(f"injected {label} replacement failure")
                return real_replace(source, destination)

            with patch(
                "backend.data_source_connectors.os.replace",
                side_effect=injected_replace,
            ):
                try:
                    preparer.prepare(source_id)
                except OSError:
                    pass
                else:
                    raise AssertionError(f"{label} 替换故障未传播")
            restored = catalog.require(source_id)
            check(
                tuple(_hash_path(path) for path in paths) == hashes
                and restored.runtime_revision == snapshot.runtime_revision
                and restored.status == snapshot.status
                and restored.enabled_for_chat == snapshot.enabled_for_chat
                and restored.routing_summary == snapshot.routing_summary,
                f"{label} 替换失败后四类资产和目录状态完整恢复",
            )
            check(
                manager.runtimes[source_id] is sentinel_runtime,
                f"{label} 替换失败未污染已缓存旧 Runtime",
            )
            check(
                not list(snapshot.metadata_path.parent.glob(".*.backup-*"))
                and not list(snapshot.metadata_path.parent.glob("candidate-*")),
                f"{label} 替换失败未留下活动候选或备份",
            )

        previous_revision = catalog.require(source_id).runtime_revision
        original_remove = preparer._remove_path

        def fail_candidate_cleanup(path: Path) -> None:
            if path.name.startswith("candidate-"):
                raise OSError("injected candidate cleanup failure")
            original_remove(path)

        with patch.object(
            preparer,
            "_remove_path",
            side_effect=fail_candidate_cleanup,
        ):
            published = preparer.prepare(source_id)
        check(
            published["runtime_revision"] == previous_revision + 1
            and all(path.exists() for path in paths)
            and catalog.require(source_id).memory_path.exists(),
            "候选清理失败不破坏成功发布且 revision 只增加一次",
        )
        for candidate in before.metadata_path.parent.glob("candidate-*"):
            original_remove(candidate)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="b5-integration-gaps-") as directory:
        root = Path(directory)
        test_tls_and_migration(root / "tls")
        test_dynamic_suggestions(root / "suggestions")
        test_index_grouping()
        test_publish_compensation(root / "publish")
    print("dynamic data source integration gaps: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

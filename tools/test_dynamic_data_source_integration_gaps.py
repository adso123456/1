"""B5 返工：TLS、迁移、动态推荐、索引与发布补偿定向回归。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from threading import Barrier, Event, Thread
from unittest.mock import patch

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    DataSourceCatalogError,
    DataSourceConflict,
)
from backend.data_source_connectors import (
    DataSourceAssetCleaner,
    DataSourceAssetPreparer,
    DirectDatabaseConnector,
    _group_mysql_indexes,
    _group_postgresql_indexes,
)
from backend.data_source_registry import DataSourceRegistry
from backend.data_source_runtime import DataSourceRuntime
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
    _set_review_policy(catalog, record.source_id, metadata)
    published = catalog.publish(record.source_id, routing_summary=routing_summary)
    published.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    published.metadata_path.write_text("[]\n", encoding="utf-8")
    published.memory_path.mkdir(parents=True, exist_ok=True)
    return record.source_id


def _set_review_policy(
    catalog: DataSourceCatalog,
    source_id: str,
    scope_metadata: list[dict],
) -> None:
    """按当前 selected_scope 重置审核策略：范围内表 active+present，
    其余发现表 pending+present，保证 E-1 前置范围门精确相等。"""
    wanted = {
        (str(item.get("schema") or ""), str(item["table"]))
        for item in scope_metadata
    }
    discovered = {
        (str(item.get("schema") or ""), str(item["table"]))
        for item in catalog.require(source_id).discovered_metadata
    }
    reviewed = {
        (str(item.get("schema_name") or ""), str(item.get("table_name") or ""))
        for item in catalog.list_table_reviews(source_id)
    }
    for schema, table in sorted((discovered | reviewed) or wanted):
        if (schema, table) in wanted:
            catalog.upsert_table_review(
                source_id,
                schema,
                table,
                effective_decision="active",
                availability_status="present",
                decision_source="test",
                decision_reason="test",
            )
        else:
            catalog.upsert_table_review(
                source_id,
                schema,
                table,
                effective_decision="pending",
                availability_status="present",
                decision_source="test",
                decision_reason="test",
            )


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
        self.records = []

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
        self.records = list(zip(ids, documents, metadatas))
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

    def get(self, *, ids=None, where=None, include=None) -> dict:
        records = list(self.records)
        if where:
            records = [
                item
                for item in records
                if all(
                    item[2].get(key) == value
                    for key, value in where.items()
                )
            ]
        if ids is not None:
            wanted = set(ids)
            records = [item for item in records if item[0] in wanted]
        return {
            "ids": [item[0] for item in records],
            "documents": [item[1] for item in records],
            "metadatas": [item[2] for item in records],
        }


_PERSISTED_COLLECTIONS: dict[str, _FakeCollection] = {}


class _FakeMemory:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        key = str(Path(path).resolve())
        if key not in _PERSISTED_COLLECTIONS:
            _PERSISTED_COLLECTIONS[key] = _FakeCollection(path)
        self.collection = _PERSISTED_COLLECTIONS[key]
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None
        self._collection = self.collection

    def _get_collection(self):
        return self.collection


class _ClosableResource:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
    for source_id in (weather_id, second_id):
        shutil.rmtree(catalog.require(source_id).metadata_path.parent)


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


def test_ddl_key_integrity(root: Path) -> None:
    catalog = _catalog(root)
    import backend.memory as memory_module

    for source_id in ("postgresql-main", "mysql-lzh-monitor"):
        primary_name = (
            "measurement_pkey"
            if source_id == "postgresql-main"
            else "PRIMARY"
        )
        metadata = [
            {
                "schema": "public" if source_id == "postgresql-main" else "",
                "table": "measurement",
                "object_type": "table",
                "table_comment": "复合键测试",
                "column": name,
                "type": "bigint",
                "comment": "",
                "nullable": False,
                "primary_key": name in {"station_id", "monitor_time"},
                "ordinal_position": position,
                "indexes": indexes,
            }
            for position, (name, indexes) in enumerate(
                (
                    (
                        "station_id",
                        [
                            {
                                "name": primary_name,
                                "unique": True,
                                "primary": True,
                                "method": "btree",
                                "columns": [
                                    {
                                        "name": "station_id",
                                        "position": 1,
                                        "direction": "ASC",
                                    },
                                    {
                                        "name": "monitor_time",
                                        "position": 2,
                                        "direction": "ASC",
                                    },
                                ],
                            }
                        ],
                    ),
                    ("monitor_time", []),
                    (
                        "region_id",
                        [
                            {
                                "name": "uq_region_metric",
                                "unique": True,
                                "primary": False,
                                "method": "btree",
                                "columns": [
                                    {
                                        "name": "region_id",
                                        "position": 1,
                                        "direction": "ASC",
                                    },
                                    {
                                        "name": "metric_id",
                                        "position": 2,
                                        "direction": "ASC",
                                    },
                                ],
                            }
                        ],
                    ),
                    ("metric_id", []),
                ),
                start=1,
            )
        ]
        schema_name = "public" if source_id == "postgresql-main" else ""
        metadata.extend(
            [
                {
                    "schema": schema_name,
                    "table": "single_key",
                    "object_type": "table",
                    "table_comment": "单列键测试",
                    "column": name,
                    "type": "bigint",
                    "comment": "",
                    "nullable": False,
                    "primary_key": name == "single_id",
                    "ordinal_position": position,
                    "indexes": (
                        [
                            {
                                "name": (
                                    "single_key_pkey"
                                    if source_id == "postgresql-main"
                                    else "PRIMARY"
                                ),
                                "unique": True,
                                "primary": True,
                                "method": "btree",
                                "columns": [
                                    {
                                        "name": "single_id",
                                        "position": 1,
                                        "direction": "ASC",
                                    }
                                ],
                            }
                        ]
                        if name == "single_id"
                        else []
                    ),
                }
                for position, name in enumerate(
                    ("single_id", "single_value"),
                    start=1,
                )
            ]
        )
        triple_columns = ("pk_a", "pk_b", "pk_c")
        metadata.extend(
            [
                {
                    "schema": schema_name,
                    "table": "triple_key",
                    "object_type": "table",
                    "table_comment": "三列键测试",
                    "column": name,
                    "type": "bigint",
                    "comment": "",
                    "nullable": False,
                    "primary_key": True,
                    "ordinal_position": position,
                    "indexes": (
                        [
                            {
                                "name": (
                                    "triple_key_pkey"
                                    if source_id == "postgresql-main"
                                    else "PRIMARY"
                                ),
                                "unique": True,
                                "primary": True,
                                "method": "btree",
                                "columns": [
                                    {
                                        "name": column,
                                        "position": index,
                                        "direction": "ASC",
                                    }
                                    for index, column in enumerate(
                                        triple_columns,
                                        start=1,
                                    )
                                ],
                            }
                        ]
                        if name == "pk_a"
                        else []
                    ),
                }
                for position, name in enumerate(triple_columns, start=1)
            ]
        )
        catalog.save_discovery(source_id, metadata)
        preparer = DataSourceAssetPreparer(catalog)

        def prepare_columns(*names: str) -> str:
            selected = [
                item for name in names
                for item in metadata
                if item["column"] == name
            ]
            catalog.save_scope(source_id, selected)
            _set_review_policy(catalog, source_id, selected)
            with patch.object(
                memory_module,
                "create_memory",
                side_effect=_FakeMemory,
            ):
                preparer.prepare(source_id)
            return "\n".join(json.loads((
                catalog.require(source_id).metadata_path.parent
                / "ddl_memories.json"
            ).read_text(encoding="utf-8")))

        first_only = prepare_columns("station_id")
        second_only = prepare_columns("monitor_time")
        full_reversed = prepare_columns("monitor_time", "station_id")
        partial_unique = prepare_columns("region_id")
        full_unique = prepare_columns("metric_id", "region_id")
        single_selected = prepare_columns("single_id")
        single_unselected = prepare_columns("single_value")
        triple_partial = prepare_columns("pk_a", "pk_b")
        quote = '"' if source_id == "postgresql-main" else "`"
        check(
            "PRIMARY KEY" in single_selected
            and "PRIMARY KEY" not in single_unselected,
            f"{source_id} 单列主键仅在该字段被选择时生成",
        )
        check(
            "PRIMARY KEY" not in first_only
            and "PRIMARY KEY" not in second_only,
            f"{source_id} 复合主键任一部分选择均不生成伪主键",
        )
        check(
            f"PRIMARY KEY ({quote}station_id{quote}, "
            f"{quote}monitor_time{quote})" in full_reversed,
            f"{source_id} 完整复合主键按数据库真实顺序生成",
        )
        check(
            "uq_region_metric" not in partial_unique,
            f"{source_id} 部分复合唯一索引不生成缩短索引",
        )
        check(
            "uq_region_metric" in full_unique
            and full_unique.index(f"{quote}region_id{quote}")
            < full_unique.index(f"{quote}metric_id{quote}"),
            f"{source_id} 完整复合唯一索引保持数据库顺序",
        )
        check(
            "PRIMARY KEY" not in triple_partial,
            f"{source_id} 三列复合主键缺任一字段均不生成",
        )

        for legacy_columns in (("legacy_id",), ("legacy_a", "legacy_b")):
            legacy_metadata = [
                {
                    "schema": schema_name,
                    "table": "legacy_key",
                    "object_type": "table",
                    "table_comment": "旧格式主键",
                    "column": name,
                    "type": "bigint",
                    "comment": "",
                    "nullable": False,
                    "primary_key": True,
                    "ordinal_position": position,
                    "indexes": [],
                }
                for position, name in enumerate(legacy_columns, start=1)
            ]
            catalog.save_discovery(source_id, legacy_metadata)
            catalog.save_scope(source_id, legacy_metadata)
            _set_review_policy(catalog, source_id, legacy_metadata)
            before = catalog.require(source_id)
            asset_paths = (
                before.metadata_path,
                before.memory_path,
                before.metadata_path.parent / "ddl_memories.json",
                before.metadata_path.parent / "business_documents.json",
            )
            hashes = tuple(_hash_path(path) for path in asset_paths)
            try:
                preparer.prepare(source_id)
            except DataSourceCatalogError as exc:
                check(
                    "重新执行“读取表和字段”" in str(exc),
                    f"{source_id} 旧格式主键提示重新发现元数据",
                )
            else:
                raise AssertionError("旧格式主键元数据未被拒绝")
            after = catalog.require(source_id)
            check(
                after.runtime_revision == before.runtime_revision
                and tuple(_hash_path(path) for path in asset_paths) == hashes,
                f"{source_id} 旧格式主键拒绝后 revision 与正式资产不变",
            )

        no_primary_metadata = [
            {
                "schema": schema_name,
                "table": "legacy_no_key",
                "object_type": "table",
                "table_comment": "旧格式无主键表",
                "column": "value",
                "type": "bigint",
                "comment": "",
                "nullable": True,
                "primary_key": False,
                "ordinal_position": 1,
                "indexes": [],
            }
        ]
        catalog.save_discovery(source_id, no_primary_metadata)
        catalog.save_scope(source_id, no_primary_metadata)
        _set_review_policy(catalog, source_id, no_primary_metadata)
        with patch.object(
            memory_module,
            "create_memory",
            side_effect=_FakeMemory,
        ):
            preparer.prepare(source_id)
        no_primary_ddl = "\n".join(
            json.loads(
                (
                    catalog.require(source_id).metadata_path.parent
                    / "ddl_memories.json"
                ).read_text(encoding="utf-8")
            )
        )
        check(
            "PRIMARY KEY" not in no_primary_ddl,
            f"{source_id} 旧格式无主键表可正常生成无主键 DDL",
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
        current = catalog.require(source_id)
        check(
            published["runtime_revision"] == previous_revision + 1
            and current.metadata_path.exists()
            and current.memory_path.exists()
            and (
                current.metadata_path.parent / "ddl_memories.json"
            ).exists()
            and (
                current.metadata_path.parent / "business_documents.json"
            ).exists(),
            "候选清理失败不破坏成功发布且 revision 只增加一次",
        )
        for candidate in before.metadata_path.parent.glob("candidate-*"):
            original_remove(candidate)
    shutil.rmtree(catalog.require(source_id).metadata_path.parent)


def test_asset_cleanup_and_runtime_release(root: Path) -> None:
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
                }
            ],
        }
    ]
    source_id = _add_ready_source(
        catalog,
        name="资产清理源",
        description="资产清理",
        metadata=metadata,
        routing_summary="旧资产",
    )
    import backend.memory as memory_module

    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_FakeMemory,
    ):
        DataSourceAssetPreparer(catalog).prepare(source_id)
    runtimes: list[DataSourceRuntime] = []
    fail_runtime_build = False

    def factory(config):
        if fail_runtime_build:
            raise RuntimeError("injected runtime build failure")
        resources = [_ClosableResource() for _ in range(5)]
        runtime = DataSourceRuntime(
            config=config,
            runner=resources[0],
            memory=resources[1],
            metadata_retriever=resources[2],
            sql_guard=resources[3],
            agent=resources[4],
        )
        runtimes.append(runtime)
        return runtime

    manager = DataSourceRuntimeManager(
        DataSourceRegistry.from_catalog(catalog),
        {"postgresql": factory, "mysql": factory},
    )
    preparer = DataSourceAssetPreparer(catalog, manager)
    manager.add_release_callback(
        preparer.asset_cleaner.retry_pending_cleanup
    )
    old_record = catalog.require(source_id)
    old_memory_path = old_record.memory_path
    old_runtime = manager.require(source_id)
    formal_paths = (
        old_record.metadata_path,
        old_record.memory_path,
        old_record.metadata_path.parent / "ddl_memories.json",
        old_record.metadata_path.parent / "business_documents.json",
    )
    formal_hashes = tuple(_hash_path(path) for path in formal_paths)

    fail_runtime_build = True
    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_FakeMemory,
    ):
        try:
            preparer.prepare(source_id)
        except RuntimeError as exc:
            check(
                "injected runtime build failure" in str(exc),
                "新 Runtime 构建故障被确定性注入",
            )
        else:
            raise AssertionError("新 Runtime 构建故障未传播")
    failed_record = catalog.require(source_id)
    check(
        failed_record.runtime_revision == old_record.runtime_revision
        and failed_record.memory_path == old_memory_path
        and old_memory_path.exists()
        and tuple(_hash_path(path) for path in formal_paths) == formal_hashes,
        "空闲旧 Runtime 场景回滚 Catalog 并保留四类正式资产",
    )
    check(
        manager.runtimes[source_id] is old_runtime
        and not old_runtime.memory.closed
        and manager.require(source_id) is old_runtime,
        "新 Runtime 失败时空闲旧 Runtime 保持缓存且未关闭",
    )
    check(
        all(
            Path(str(item["path"])) != old_memory_path
            for item in catalog.pending_cleanups(source_id)
        ),
        "新 Runtime 失败未将旧 Memory 登记为待清理",
    )

    with manager.acquire(source_id) as leased_runtime:
        with patch.object(
            memory_module,
            "create_memory",
            side_effect=_FakeMemory,
        ):
            try:
                preparer.prepare(source_id)
            except RuntimeError:
                pass
            else:
                raise AssertionError("活跃租约场景 Runtime 故障未传播")
        check(
            leased_runtime is old_runtime
            and manager.runtimes[source_id] is old_runtime
            and old_memory_path.exists()
            and not old_runtime.memory.closed,
            "新 Runtime 失败时活跃请求继续使用旧 Runtime 和旧 Memory",
        )
    check(
        manager.runtimes[source_id] is old_runtime
        and old_memory_path.exists()
        and not old_runtime.memory.closed,
        "失败回滚后租约释放不触发旧 Runtime 或旧 Memory 清理",
    )
    fail_runtime_build = False

    with manager.acquire(source_id):
        catalog.save_scope(source_id, metadata)
        _set_review_policy(catalog, source_id, metadata)
        with patch.object(
            memory_module,
            "create_memory",
            side_effect=_FakeMemory,
        ):
            result = preparer.prepare(source_id)
        new_record = catalog.require(source_id)
        check(
            result["runtime_revision"] == old_record.runtime_revision + 1
            and new_record.memory_path != old_memory_path
            and runtimes[-1].config.memory_path == new_record.memory_path,
            "prepare 仅递增一次 revision 且新 Runtime 使用新 Memory",
        )
        check(
            old_memory_path.exists()
            and any(
                Path(str(item["path"])) == old_memory_path
                for item in catalog.pending_cleanups(source_id)
            ),
            "旧 Runtime 持有 Memory 时保留资产并登记 pending cleanup",
        )

    check(
        not old_memory_path.exists()
        and not catalog.pending_cleanups(source_id)
        and old_runtime.memory.closed,
        "请求释放后关闭旧 Runtime 并重试清理旧 revision",
    )
    current = catalog.require(source_id)
    check(
        current.metadata_path.exists()
        and current.memory_path.exists()
        and (
            current.metadata_path.parent / "ddl_memories.json"
        ).exists()
        and (
            current.metadata_path.parent / "business_documents.json"
        ).exists(),
        "Catalog 当前引用的四类正式资产始终受保护",
    )
    check(
        not list(current.metadata_path.parent.glob("candidate-*"))
        and not list(current.metadata_path.parent.glob(".*.candidate-*"))
        and not list(current.metadata_path.parent.glob(".*.backup-*")),
        "成功发布后的候选与本批次备份均已清理",
    )

    outside = root / "outside.revision-1-protected"
    outside.mkdir(parents=True)
    catalog.register_pending_cleanup(
        source_id,
        outside,
        "memory_revision",
    )
    DataSourceAssetCleaner(catalog, manager).cleanup_superseded_assets(source_id)
    check(
        outside.exists(),
        "受管根目录外路径即使命名匹配也不会删除",
    )
    catalog.complete_pending_cleanup(source_id, outside)
    shutil.rmtree(current.metadata_path.parent)


def test_prepare_coordination(root: Path) -> None:
    catalog = _catalog(root)
    metadata = [
        {
            "schema": "public",
            "table": "coordinated_table",
            "object_type": "table",
            "table_comment": "并发准备测试",
            "column": "id",
            "type": "bigint",
            "comment": "主键",
            "nullable": False,
            "primary_key": True,
            "ordinal_position": 1,
            "indexes": [
                {
                    "name": "coordinated_table_pkey",
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
                }
            ],
        }
    ]
    first_source = _add_ready_source(
        catalog,
        name="并发源一",
        description="并发准备一",
        metadata=metadata,
        routing_summary="并发一",
    )
    second_source = _add_ready_source(
        catalog,
        name="并发源二",
        description="并发准备二",
        metadata=metadata,
        routing_summary="并发二",
    )
    import backend.memory as memory_module

    entered = Event()
    release = Event()

    class BlockingCollection(_FakeCollection):
        def add(self, *, ids, documents, metadatas) -> None:
            entered.set()
            if not release.wait(10):
                raise TimeoutError("候选构建等待超时")
            super().add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    class BlockingMemory(_FakeMemory):
        def __init__(self, path: Path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            key = str(Path(path).resolve())
            if key not in _PERSISTED_COLLECTIONS:
                _PERSISTED_COLLECTIONS[key] = BlockingCollection(path)
            self.collection = _PERSISTED_COLLECTIONS[key]
            self._executor = type(
                "Executor",
                (),
                {"shutdown": lambda self, wait: None},
            )()
            self._client = None
            self._collection = self.collection

    first_revision = catalog.require(first_source).runtime_revision
    thread_errors: list[Exception] = []

    def run_first_prepare() -> None:
        try:
            DataSourceAssetPreparer(catalog).prepare(first_source)
        except Exception as exc:
            thread_errors.append(exc)

    with patch.object(
        memory_module,
        "create_memory",
        side_effect=BlockingMemory,
    ):
        first_thread = Thread(target=run_first_prepare)
        first_thread.start()
        check(entered.wait(10), "请求 A 已创建活动候选并暂停")
        active = catalog.active_asset_batches(first_source)
        active_candidate = Path(str(active[0]["candidate_root"]))
        check(
            len(active) == 1
            and active_candidate.exists()
            and active_candidate in catalog.active_asset_paths(first_source),
            "活动批次登记包含候选目录并实时保护",
        )
        try:
            DataSourceAssetPreparer(catalog).prepare(first_source)
        except DataSourceConflict as exc:
            check(
                "正在生成问数资产" in str(exc),
                "同一 source_id 的第二个 prepare 明确返回冲突",
            )
        else:
            raise AssertionError("同源重复 prepare 未被拒绝")
        DataSourceAssetCleaner(catalog).retry_pending_cleanup(first_source)
        check(
            active_candidate.exists(),
            "Runtime release 回调式 pending 重试不扫描活动 candidate",
        )
        release.set()
        first_thread.join(10)
    check(
        not first_thread.is_alive() and not thread_errors,
        "请求 A 在并发拒绝后正常完成",
    )
    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_FakeMemory,
    ):
        DataSourceAssetPreparer(catalog).prepare(first_source)
    check(
        catalog.require(first_source).runtime_revision == first_revision + 2,
        "同源后续 prepare 可执行且 revision 按成功次数递增",
    )

    barrier = Barrier(2)
    parallel_errors: list[Exception] = []

    class BarrierCollection(_FakeCollection):
        def add(self, *, ids, documents, metadatas) -> None:
            barrier.wait(10)
            super().add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    class BarrierMemory(_FakeMemory):
        def __init__(self, path: Path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            key = str(Path(path).resolve())
            if key not in _PERSISTED_COLLECTIONS:
                _PERSISTED_COLLECTIONS[key] = BarrierCollection(path)
            self.collection = _PERSISTED_COLLECTIONS[key]
            self._executor = type(
                "Executor",
                (),
                {"shutdown": lambda self, wait: None},
            )()
            self._client = None
            self._collection = self.collection

    parallel_start = {
        source_id: catalog.require(source_id).runtime_revision
        for source_id in (first_source, second_source)
    }

    def run_parallel(source_id: str) -> None:
        try:
            DataSourceAssetPreparer(catalog).prepare(source_id)
        except Exception as exc:
            parallel_errors.append(exc)

    with patch.object(
        memory_module,
        "create_memory",
        side_effect=BarrierMemory,
    ):
        threads = [
            Thread(target=run_parallel, args=(source_id,))
            for source_id in (first_source, second_source)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
    check(
        all(not thread.is_alive() for thread in threads)
        and not parallel_errors
        and all(
            catalog.require(source_id).runtime_revision
            == parallel_start[source_id] + 1
            for source_id in (first_source, second_source)
        ),
        "不同 source_id 可并行构建且 revision、资产互不干扰",
    )

    changed_entered = Event()
    changed_release = Event()

    class ScopeChangeCollection(_FakeCollection):
        def add(self, *, ids, documents, metadatas) -> None:
            changed_entered.set()
            if not changed_release.wait(10):
                raise TimeoutError("范围变更等待超时")
            super().add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    class ScopeChangeMemory(_FakeMemory):
        def __init__(self, path: Path) -> None:
            path.mkdir(parents=True, exist_ok=True)
            key = str(Path(path).resolve())
            if key not in _PERSISTED_COLLECTIONS:
                _PERSISTED_COLLECTIONS[key] = ScopeChangeCollection(path)
            self.collection = _PERSISTED_COLLECTIONS[key]
            self._executor = type(
                "Executor",
                (),
                {"shutdown": lambda self, wait: None},
            )()
            self._client = None
            self._collection = self.collection

    changed_errors: list[Exception] = []
    before_change = catalog.require(first_source)
    protected_paths = (
        before_change.metadata_path,
        before_change.memory_path,
        before_change.metadata_path.parent / "ddl_memories.json",
        before_change.metadata_path.parent / "business_documents.json",
    )
    protected_hashes = tuple(_hash_path(path) for path in protected_paths)

    def run_changed_prepare() -> None:
        try:
            DataSourceAssetPreparer(catalog).prepare(first_source)
        except Exception as exc:
            changed_errors.append(exc)

    with patch.object(
        memory_module,
        "create_memory",
        side_effect=ScopeChangeMemory,
    ):
        changed_thread = Thread(target=run_changed_prepare)
        changed_thread.start()
        check(changed_entered.wait(10), "旧批次已在发布前暂停")
        catalog.save_scope(first_source, metadata)
        changed_release.set()
        changed_thread.join(10)
    check(
        len(changed_errors) == 1
        and isinstance(changed_errors[0], DataSourceConflict)
        and catalog.require(first_source).runtime_revision
        == before_change.runtime_revision
        and catalog.require(first_source).status == "training_required"
        and tuple(_hash_path(path) for path in protected_paths)
        == protected_hashes
        and not catalog.active_asset_batches(first_source),
        "scope/status 变化后旧批次拒绝发布并清理自身候选",
    )

    cleaner = DataSourceAssetCleaner(catalog)
    managed_root = catalog.require(second_source).metadata_path.parent
    fresh_candidate = managed_root / "candidate-fresh-orphan"
    fresh_memory = managed_root / ".memory.candidate-fresh-orphan"
    fresh_revision = managed_root / "memory.revision-fresh-orphan"
    fresh_candidate.mkdir()
    fresh_memory.mkdir()
    fresh_revision.mkdir()
    catalog.begin_asset_batch(
        second_source,
        batch_id="fresh-orphan",
        candidate_root=fresh_candidate,
        candidate_memory=fresh_memory,
        published_memory_path=fresh_revision,
    )
    try:
        DataSourceAssetPreparer(catalog).prepare(second_source)
    except DataSourceConflict:
        check(
            True,
            "独立 Preparer 遇到新鲜跨实例活动批次时返回冲突",
        )
    else:
        raise AssertionError("跨实例活动批次未阻止新的 prepare")
    cleaner.cleanup_stale_batches(grace_seconds=600)
    check(
        fresh_candidate.exists()
        and catalog.active_asset_batches(second_source),
        "宽限期内的异常活动批次不会被启动清理删除",
    )
    catalog.finish_asset_batch(second_source, "fresh-orphan")
    stale_candidate = managed_root / "candidate-stale-orphan"
    stale_memory = managed_root / ".memory.candidate-stale-orphan"
    stale_revision = managed_root / "memory.revision-stale-orphan"
    stale_candidate.mkdir()
    stale_memory.mkdir()
    stale_revision.mkdir()
    catalog.begin_asset_batch(
        second_source,
        batch_id="stale-orphan",
        candidate_root=stale_candidate,
        candidate_memory=stale_memory,
        published_memory_path=stale_revision,
        started_at=int(time.time()) - 601,
    )
    with catalog._lock, catalog._connection(write=True) as connection:
        connection.execute(
            """
            UPDATE active_asset_batches
            SET owner_pid = ?
            WHERE source_id = ? AND batch_id = ?
            """,
            (2147483647, second_source, "stale-orphan"),
        )
    cleaner.cleanup_stale_batches(grace_seconds=600)
    check(
        stale_candidate.exists()
        and stale_memory.exists()
        and stale_revision.exists()
        and catalog.active_asset_batches(second_source)[0]["phase"]
        == "rollback_failed"
        and catalog.require(second_source).status == "error",
        "缺少恢复快照的旧批次保留全部证据并关闭问数入口",
    )
    catalog.finish_asset_batch(second_source, "stale-orphan")
    for path in (stale_candidate, stale_memory, stale_revision):
        shutil.rmtree(path)
    for path in (fresh_candidate, fresh_memory, fresh_revision):
        shutil.rmtree(path)
    for source_id in (first_source, second_source):
        shutil.rmtree(catalog.require(source_id).metadata_path.parent)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="b5-integration-gaps-") as directory:
        root = Path(directory)
        test_tls_and_migration(root / "tls")
        test_dynamic_suggestions(root / "suggestions")
        test_index_grouping()
        test_ddl_key_integrity(root / "ddl-keys")
        test_publish_compensation(root / "publish")
        test_asset_cleanup_and_runtime_release(root / "cleanup")
        test_prepare_coordination(root / "coordination")
    print("dynamic data source integration gaps: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

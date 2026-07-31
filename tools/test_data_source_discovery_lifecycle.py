"""重新发现数据源时的发布状态生命周期回归测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import backend.data_source_catalog as catalog_module
from backend.data_source_catalog import CredentialCipher, DataSourceCatalog


def _metadata(database_type: str) -> list[dict]:
    return [
        {
            "schema": "public" if database_type == "postgresql" else "test_db",
            "table": "monitor_data",
            "object_type": "table",
            "table_comment": "监测数据",
            "column": "id",
            "type": "bigint",
            "comment": "主键",
            "nullable": False,
            "primary_key": True,
            "ordinal_position": 1,
            "indexes": [],
            "logical_relations": [],
        },
        {
            "schema": "public" if database_type == "postgresql" else "test_db",
            "table": "monitor_data",
            "object_type": "table",
            "table_comment": "监测数据",
            "column": "value",
            "type": "numeric",
            "comment": "监测值",
            "nullable": True,
            "primary_key": False,
            "ordinal_position": 2,
            "indexes": [],
            "logical_relations": [],
        },
    ]


def _catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataSourceCatalog:
    monkeypatch.setattr(catalog_module, "PROJECT_ROOT", tmp_path)
    catalog = DataSourceCatalog(
        tmp_path / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
        environ={},
    )
    catalog.initialize()
    return catalog


def _create_source(
    catalog: DataSourceCatalog,
    database_type: str,
    *,
    published: bool,
):
    metadata = _metadata(database_type)
    record = catalog.create(
        display_name=f"{database_type} 测试源",
        description="生命周期测试",
        database_type=database_type,
        host="127.0.0.1",
        port=5433 if database_type == "postgresql" else 3307,
        database_name="test_db",
        schema_name="public" if database_type == "postgresql" else "",
        username="test-user",
        password="test-password",
    )
    catalog.mark_connection_test(record.source_id, success=True)
    catalog.save_discovery(record.source_id, metadata)
    if not published:
        return catalog.require(record.source_id), metadata
    catalog.save_scope(record.source_id, metadata)
    record = catalog.publish(record.source_id, routing_summary="监测数据")
    record.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    record.metadata_path.write_text("[]\n", encoding="utf-8")
    record.memory_path.mkdir(parents=True, exist_ok=True)
    return catalog.require(record.source_id), metadata


@pytest.mark.parametrize("database_type", ["postgresql", "mysql"])
def test_rediscovery_preserves_ready_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_type: str,
) -> None:
    catalog = _catalog(tmp_path, monkeypatch)
    before, metadata = _create_source(catalog, database_type, published=True)
    metadata[0]["table_comment"] = "更新后的监测数据说明"
    metadata[0]["comment"] = "更新后的主键说明"

    after = catalog.save_discovery(before.source_id, metadata)

    assert (after.status, after.enabled_for_chat) == ("ready", True)
    assert after.runtime_revision == before.runtime_revision


@pytest.mark.parametrize("database_type", ["postgresql", "mysql"])
def test_rediscovery_recovers_historical_connected_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_type: str,
) -> None:
    catalog = _catalog(tmp_path, monkeypatch)
    before, metadata = _create_source(catalog, database_type, published=True)
    catalog.restore_publication_state(
        before.source_id,
        replace(before, status="connected", enabled_for_chat=True),
    )

    after = catalog.save_discovery(before.source_id, metadata)

    assert (after.status, after.enabled_for_chat) == ("ready", True)
    assert after.runtime_revision == before.runtime_revision


@pytest.mark.parametrize("database_type", ["postgresql", "mysql"])
def test_rediscovery_preserves_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_type: str,
) -> None:
    catalog = _catalog(tmp_path, monkeypatch)
    before, metadata = _create_source(catalog, database_type, published=True)
    before = catalog.set_enabled(before.source_id, False)

    after = catalog.save_discovery(before.source_id, metadata)

    assert (after.status, after.enabled_for_chat) == ("disabled", False)
    assert after.runtime_revision == before.runtime_revision


@pytest.mark.parametrize("database_type", ["postgresql", "mysql"])
@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "incompatible",
        "missing_metadata",
        "missing_memory",
    ],
)
def test_rediscovery_requires_training_when_publication_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_type: str,
    change: str,
) -> None:
    catalog = _catalog(tmp_path, monkeypatch)
    before, metadata = _create_source(catalog, database_type, published=True)
    discovered = [dict(item) for item in metadata]
    if change == "missing":
        discovered.pop()
    elif change == "incompatible":
        discovered[1]["type"] = "text"
    elif change == "missing_metadata":
        before.metadata_path.unlink()
    else:
        before.memory_path.rmdir()

    after = catalog.save_discovery(before.source_id, discovered)

    assert (after.status, after.enabled_for_chat) == (
        "training_required",
        False,
    )
    assert after.runtime_revision == before.runtime_revision


@pytest.mark.parametrize("database_type", ["postgresql", "mysql"])
def test_rediscovery_keeps_unpublished_source_connected_and_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_type: str,
) -> None:
    catalog = _catalog(tmp_path, monkeypatch)
    before, metadata = _create_source(catalog, database_type, published=False)

    after = catalog.save_discovery(before.source_id, metadata)

    assert (after.status, after.enabled_for_chat) == ("connected", False)
    assert after.runtime_revision == 0

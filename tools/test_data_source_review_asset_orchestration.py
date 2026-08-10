"""自动治理后 crash-safe 资产编排的隔离回归。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
    DataSourceConflict,
    selected_scope_fingerprint,
)
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.data_source_onboarding import DataSourceOnboardingService


A = "water_level_legacy"
B = "water_quality_monitor"
C = "pollution_source_monitor"
COLUMNS = (
    "station_id",
    "monitor_time",
    "ph",
    "cod",
    "nh3n",
    "tp",
    "tn",
    "water_temp",
    "flow",
    "area_code",
    "status",
)


class _FakeCollection:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.total = 0

    def add(self, *, ids, documents, metadatas) -> None:
        self.total += len(ids)
        (self.root / "records.json").write_text(
            json.dumps(
                {"ids": ids, "documents": documents, "metadatas": metadatas},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def count(self) -> int:
        return self.total

    def get(self, *, where=None, include=None) -> dict:
        return {"ids": [], "documents": [], "metadatas": []}


class _FakeMemory:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None
        self._collection = _FakeCollection(path)

    def _get_collection(self):
        return self._collection


def _metadata(tables: tuple[str, ...]) -> list[dict]:
    comments = {
        A: "历史水位监测数据",
        B: "水质监测数据",
        C: "污染源监测数据",
    }
    return [
        {
            "schema": "public",
            "table": table,
            "object_type": "table",
            "table_comment": comments[table],
            "column": column,
            "type": "timestamp" if column == "monitor_time" else "numeric",
            "comment": f"{column} 字段",
            "nullable": True,
            "primary_key": False,
            "ordinal_position": position,
            "indexes": [],
            "logical_relations": [],
            "domain": "水环境",
            "grain": "station_id+monitor_time",
            "time_column": "monitor_time",
            "valid_row_rules": [],
            "confidence": "deterministic",
        }
        for table in tables
        for position, column in enumerate(COLUMNS, start=1)
    ]


class _Connector:
    def __init__(self, metadata: list[dict]) -> None:
        self.metadata = metadata

    def discover(self, source_id: str, *, persist: bool = True) -> list[dict]:
        return [dict(item) for item in self.metadata]


class _Profiler:
    def profile(self, source_id, metadata, *, progress=None) -> list[dict]:
        tables = list(dict.fromkeys(item["table"] for item in metadata))
        profiles = []
        for index, table in enumerate(tables, start=1):
            if progress:
                progress(index, len(tables), table)
            columns = [item for item in metadata if item["table"] == table]
            profiles.append(
                {
                    "schema": "public",
                    "table": table,
                    "object_type": "table",
                    "table_comment": columns[0]["table_comment"],
                    "table_role_candidate": "事实表",
                    "grain_candidate": "station_id+monitor_time",
                    "time_column_candidate": "monitor_time",
                    "columns": [
                        {
                            "column": item["column"],
                            "type": item["type"],
                            "sample_null_rate": 0.0,
                            "sample_distinct_count": 100,
                            "sensitive": False,
                        }
                        for item in columns
                    ],
                    "quality": {
                        "column_count": len(columns),
                        "queryable_column_count": len(columns),
                        "has_primary_key": True,
                        "has_unique_key": False,
                        "primary_key_columns": ["station_id", "monitor_time"],
                        "row_estimate": 100_000,
                        "sample_row_count": 200,
                        "sample_null_rate": 0.02,
                        "latest_data_at": "2026-08-01 10:00:00",
                        "time_coverage_days": 400.0,
                        "duplicate_key_ratio": 0.0,
                        "observed_update_interval": None,
                        "staleness_ratio": None,
                        "freshness_confidence": 0.0,
                        "skipped_by_total_timeout": False,
                        "structure_fingerprint": f"structure-{table}",
                        "data_fingerprint": f"data-{table}",
                        "table_comment": columns[0]["table_comment"],
                    },
                    "error": "",
                }
            )
        return profiles


def _catalog(directory: Path) -> tuple[DataSourceCatalog, str]:
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
    )
    catalog.initialize()
    source_id = "review-assets"
    managed = directory / "project" / "agent_data" / "data_sources" / source_id
    source = catalog.create(
        display_name="review asset orchestration",
        description="",
        database_type="postgresql",
        host="127.0.0.1",
        port=5432,
        database_name="gt_monitor",
        schema_name="public",
        username="readonly",
        password="secret",
        source_id=source_id,
        metadata_path=managed / "metadata.json",
        memory_path=managed / "memory",
    )
    return catalog, source.source_id


def _managed_root_patch(catalog: DataSourceCatalog, source_id: str):
    project_root = catalog.require(source_id).metadata_path.parents[3]
    return patch("backend.data_source_connectors.PROJECT_ROOT", project_root)


def _publish_old_scope(
    catalog: DataSourceCatalog,
    source_id: str,
) -> None:
    old_metadata = _metadata((A, B))
    catalog.save_discovery(source_id, old_metadata)
    catalog.save_scope(source_id, old_metadata)
    for table in (A, B):
        catalog.upsert_table_review(
            source_id,
            "public",
            table,
            proposed_decision="active",
            proposed_score=100,
            effective_decision="active",
            decision_source="seed",
            decision_reason="seed",
            availability_status="present",
        )
    with _managed_root_patch(catalog, source_id), patch(
        "backend.memory.create_memory",
        side_effect=_FakeMemory,
    ):
        DataSourceAssetPreparer(catalog).prepare(source_id)


def _formal_hashes(catalog: DataSourceCatalog, source_id: str) -> tuple[str, ...]:
    record = catalog.require(source_id)
    root = record.metadata_path.parent
    paths = (
        record.metadata_path,
        record.memory_path,
        root / "ddl_memories.json",
        root / "business_documents.json",
        root / "asset_provenance.json",
        root / "asset_manifest.json",
    )
    cleaner = DataSourceAssetPreparer(catalog).asset_cleaner
    return tuple(cleaner._path_hash(path) for path in paths)


def _service(
    catalog: DataSourceCatalog,
    source_id: str,
    preparer: DataSourceAssetPreparer,
) -> tuple[DataSourceOnboardingService, str]:
    service = DataSourceOnboardingService(
        catalog,
        _Connector(_metadata((B, C))),
        _Profiler(),
        preparer,
        semantic_analyzer=object(),
        sql_memory_generator=object(),
    )
    job = catalog.create_onboarding_job(source_id, "review")
    return service, str(job["job_id"])


def _shutdown(service: DataSourceOnboardingService) -> None:
    service._executor.shutdown(wait=True)


def test_review_rebuilds_and_publishes_only_new_scope() -> None:
    with tempfile.TemporaryDirectory(prefix="review-assets-success-") as raw:
        catalog, source_id = _catalog(Path(raw))
        _publish_old_scope(catalog, source_id)
        assert catalog.require(source_id).runtime_revision == 1
        service, job_id = _service(
            catalog,
            source_id,
            DataSourceAssetPreparer(catalog),
        )
        try:
            with _managed_root_patch(catalog, source_id), patch(
                "backend.memory.create_memory",
                side_effect=_FakeMemory,
            ):
                result = service._review(job_id, source_id)
        finally:
            _shutdown(service)

        record = catalog.require(source_id)
        expected_tables = {B, C}
        assert result["runtime_revision"] == 2
        assert record.status == "ready" and record.enabled_for_chat
        assert {item["table"] for item in record.selected_scope} == expected_tables
        metadata = json.loads(record.metadata_path.read_text(encoding="utf-8"))
        ddls = json.loads(
            (record.metadata_path.parent / "ddl_memories.json").read_text(
                encoding="utf-8"
            )
        )
        documents = json.loads(
            (record.metadata_path.parent / "business_documents.json").read_text(
                encoding="utf-8"
            )
        )
        memory_records = json.loads(
            (record.memory_path / "records.json").read_text(encoding="utf-8")
        )
        joined_assets = json.dumps(
            [metadata, ddls, documents, memory_records],
            ensure_ascii=False,
        )
        assert {item["table"] for item in metadata} == expected_tables
        assert A not in joined_assets and B in joined_assets and C in joined_assets

        policy = catalog.review_policy(source_id)
        scope_fingerprint = selected_scope_fingerprint(record.selected_scope)
        manifest = json.loads(
            (record.metadata_path.parent / "asset_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        provenance = json.loads(
            (record.metadata_path.parent / "asset_provenance.json").read_text(
                encoding="utf-8"
            )
        )
        identity = json.loads(
            (record.memory_path / ".asset_identity.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["scope_fingerprint"] == scope_fingerprint
        assert provenance["scope_fingerprint"] == scope_fingerprint
        assert manifest["review_policy_fingerprint"] == policy["fingerprint"]
        assert provenance["review_policy_fingerprint"] == policy["fingerprint"]
        assert manifest["provenance_hash"] == identity["provenance_hash"]


def test_build_and_chroma_failures_keep_new_governance_and_old_revision() -> None:
    for mode in ("candidate", "chroma"):
        with tempfile.TemporaryDirectory(prefix=f"review-assets-{mode}-") as raw:
            catalog, source_id = _catalog(Path(raw))
            _publish_old_scope(catalog, source_id)
            before = catalog.require(source_id)
            hashes_before = _formal_hashes(catalog, source_id)
            preparer = DataSourceAssetPreparer(
                catalog,
                fault_injector=(
                    (lambda point: (_ for _ in ()).throw(RuntimeError("build"))
                     if point == "after_candidate_metadata" else None)
                    if mode == "candidate"
                    else None
                ),
            )
            service, job_id = _service(catalog, source_id, preparer)
            old_memory = before.memory_path.resolve()

            def memory_factory(path: Path):
                if mode == "chroma" and Path(path).resolve() != old_memory:
                    raise RuntimeError("chroma build")
                return _FakeMemory(path)

            try:
                with _managed_root_patch(catalog, source_id), patch(
                    "backend.memory.create_memory",
                    side_effect=memory_factory,
                ):
                    try:
                        service._review(job_id, source_id)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError(f"{mode} 故障必须传播")
            finally:
                _shutdown(service)

            record = catalog.require(source_id)
            assert {item["table"] for item in record.selected_scope} == {B, C}
            assert record.runtime_revision == before.runtime_revision
            assert record.memory_path == before.memory_path
            assert record.status == "training_required", (
                mode,
                record.status,
                record.last_error,
                catalog.active_asset_batches(source_id),
            )
            assert not record.enabled_for_chat
            assert _formal_hashes(catalog, source_id) == hashes_before
            assert not catalog.active_asset_batches(source_id)


def _governed_catalog(directory: Path) -> tuple[DataSourceCatalog, str]:
    catalog, source_id = _catalog(directory)
    metadata = _metadata((B, C))
    catalog.save_discovery(source_id, metadata)
    catalog.save_scope(source_id, metadata)
    for table in (B, C):
        catalog.upsert_table_review(
            source_id,
            "public",
            table,
            proposed_decision="active",
            proposed_score=100,
            effective_decision="active",
            decision_source="automatic_policy_v2",
            decision_reason="test",
            availability_status="present",
        )
    return catalog, source_id


def test_stale_policy_scope_and_revision_never_publish() -> None:
    for mode in ("policy", "scope", "revision"):
        with tempfile.TemporaryDirectory(prefix=f"review-stale-{mode}-") as raw:
            catalog, source_id = _governed_catalog(Path(raw))

            def mutate(point: str) -> None:
                if point != "after_candidate_memory":
                    return
                connection = sqlite3.connect(catalog.db_path)
                try:
                    if mode == "policy":
                        connection.execute(
                            "UPDATE data_source_table_reviews SET "
                            "effective_decision='standby' "
                            "WHERE source_id=? AND table_name=?",
                            (source_id, C),
                        )
                    elif mode == "scope":
                        scope = [
                            item
                            for item in catalog.require(source_id).selected_scope
                            if item["table"] == B
                        ]
                        connection.execute(
                            "UPDATE data_sources SET selected_scope_json=?, "
                            "selected_tables_count=1, selected_columns_count=? "
                            "WHERE source_id=?",
                            (json.dumps(scope), len(scope), source_id),
                        )
                    elif mode == "revision":
                        connection.execute(
                            "UPDATE data_sources SET runtime_revision=9 "
                            "WHERE source_id=?",
                            (source_id,),
                        )
                    connection.commit()
                finally:
                    connection.close()

            preparer = DataSourceAssetPreparer(catalog, fault_injector=mutate)
            with _managed_root_patch(catalog, source_id), patch(
                "backend.memory.create_memory",
                side_effect=_FakeMemory,
            ):
                try:
                    preparer.prepare(source_id)
                except DataSourceConflict:
                    pass
                except Exception as exc:
                    raise AssertionError((mode, repr(exc))) from exc
                else:
                    raise AssertionError(f"{mode} 变化必须拒绝陈旧候选")
            assert catalog.require(source_id).status != "ready"
            assert not catalog.require(source_id).enabled_for_chat
            assert not catalog.active_asset_batches(source_id)


def test_catalog_publish_rejects_lost_batch_owner() -> None:
    with tempfile.TemporaryDirectory(prefix="review-lost-owner-") as raw:
        catalog, source_id = _governed_catalog(Path(raw))
        record = catalog.require(source_id)
        policy = catalog.review_policy(source_id)
        root = record.metadata_path.parent
        catalog.begin_asset_batch(
            source_id,
            batch_id="actual-owner",
            candidate_root=root / "candidate-actual-owner",
            candidate_memory=root / "memory.revision-1-actual-owner",
            published_memory_path=root / "memory.revision-1-actual-owner",
            expected_review_policy_fingerprint=policy["fingerprint"],
        )
        try:
            try:
                catalog.publish(
                    source_id,
                    routing_summary="stale",
                    expected_runtime_revision=record.runtime_revision,
                    expected_scope_fingerprint=selected_scope_fingerprint(
                        record.selected_scope
                    ),
                    expected_status=record.status,
                    expected_review_policy_fingerprint=policy["fingerprint"],
                    expected_asset_batch_id="lost-owner",
                )
            except DataSourceConflict as exc:
                assert "批次已失效" in str(exc)
            else:
                raise AssertionError("publish 必须原子校验 active batch owner")
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            catalog.finish_asset_batch(source_id, "actual-owner")


def test_existing_asset_batch_blocks_review_before_second_prepare() -> None:
    with tempfile.TemporaryDirectory(prefix="review-existing-batch-") as raw:
        catalog, source_id = _governed_catalog(Path(raw))
        record = catalog.require(source_id)
        policy = catalog.review_policy(source_id)
        root = record.metadata_path.parent
        catalog.begin_asset_batch(
            source_id,
            batch_id="existing-batch",
            candidate_root=root / "candidate-existing-batch",
            candidate_memory=root / "memory.revision-1-existing-batch",
            published_memory_path=root / "memory.revision-1-existing-batch",
            expected_review_policy_fingerprint=policy["fingerprint"],
        )

        class NeverPreparer:
            def prepare(self, source_id: str):
                raise AssertionError("active batch 存在时不得启动第二个 prepare")

        service, job_id = _service(catalog, source_id, NeverPreparer())
        try:
            try:
                service._review(job_id, source_id)
            except DataSourceConflict:
                pass
            else:
                raise AssertionError("active batch 必须在 discovery 前拒绝 review")
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            _shutdown(service)
            catalog.finish_asset_batch(source_id, "existing-batch")


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"Review asset orchestration tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()

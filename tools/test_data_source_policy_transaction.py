"""Policy Promotion + effective/scope 原子事务隔离回归。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

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
from backend.data_source_policy_promotion import (
    POLICY_SOURCE,
    PolicyPromotionError,
    promote_policy,
)
from backend.data_source_table_reviewer import DataSourceTableReviewer


TABLES = (
    "active_candidate",
    "pending_candidate",
    "standby_candidate",
    "unknown_high_quality",
    "ineligible_high_quality",
)


class _NeverConnector:
    def discover(self, *args, **kwargs):
        raise AssertionError("lease preflight 后不应执行 discovery")


class _NeverProfiler:
    def profile(self, *args, **kwargs):
        raise AssertionError("lease preflight 后不应执行 profile")


def _metadata(tables: tuple[str, ...] = TABLES) -> list[dict]:
    return [
        {
            "schema": "public",
            "table": table,
            "column": column,
            "type": "text",
            "comment": f"{table}.{column}",
        }
        for table in tables
        for column in ("id", "name")
    ]


def _catalog(directory: Path) -> tuple[DataSourceCatalog, str]:
    catalog = DataSourceCatalog(
        directory / "catalog.sqlite3",
        cipher=CredentialCipher(Fernet.generate_key().decode("ascii")),
    )
    catalog.initialize()
    source = catalog.create(
        display_name="policy transaction",
        description="",
        database_type="postgresql",
        host="127.0.0.1",
        port=5432,
        database_name="gt_monitor",
        schema_name="public",
        username="readonly",
        password="secret",
    )
    old_metadata = _metadata(("active_candidate", "missing_old_active"))
    catalog.save_discovery(source.source_id, old_metadata)
    catalog.save_scope(source.source_id, old_metadata)
    for table, proposed, effective in (
        ("active_candidate", "standby", "active"),
        ("pending_candidate", "active", "standby"),
        ("standby_candidate", "active", "standby"),
        ("unknown_high_quality", "active", "standby"),
        ("ineligible_high_quality", "active", "standby"),
        ("missing_old_active", "active", "active"),
    ):
        catalog.upsert_table_review(
            source.source_id,
            "public",
            table,
            proposed_decision=proposed,
            proposed_score=99,
            effective_decision=effective,
            decision_source="seed",
            decision_reason="seed",
            availability_status="present",
        )

    metadata_file = directory / "formal-metadata.json"
    memory_file = directory / "formal-memory.bin"
    metadata_file.write_text("old metadata", encoding="utf-8")
    memory_file.write_bytes(b"old memory")
    connection = sqlite3.connect(catalog.db_path)
    try:
        connection.execute(
            "UPDATE data_sources SET status='ready', enabled_for_chat=1, "
            "runtime_revision=7, metadata_path=?, memory_path=? WHERE source_id=?",
            (str(metadata_file), str(memory_file), source.source_id),
        )
        connection.commit()
    finally:
        connection.close()
    return catalog, source.source_id


def _updates() -> list[tuple[str, str, dict]]:
    decisions = {
        "active_candidate": "active",
        "pending_candidate": "pending",
        "standby_candidate": "standby",
        "unknown_high_quality": "pending",
        "ineligible_high_quality": "standby",
    }
    return [
        (
            "public",
            table,
            {
                "proposed_decision": proposed,
                "proposed_score": 99,
                "proposed_reason": f"proposal:{proposed}",
                "availability_status": "present",
                "quality_metrics_json": '{"score":99}',
                "compared_tables_json": "[]",
            },
        )
        for table, proposed in decisions.items()
    ]


def _record_run(catalog: DataSourceCatalog, source_id: str, run_id: str) -> None:
    catalog.record_review_run(
        run_id=run_id,
        source_id=source_id,
        review_version=2,
        status="running",
    )


def _snapshot(catalog: DataSourceCatalog, source_id: str) -> tuple:
    record = catalog.require(source_id)
    reviews = [
        (
            row["table_name"],
            row["proposed_decision"],
            row["effective_decision"],
            row["availability_status"],
            row["decision_source"],
        )
        for row in catalog.list_table_reviews(source_id)
    ]
    return (
        reviews,
        tuple(dict(item) for item in record.selected_scope),
        record.selected_tables_count,
        record.selected_columns_count,
        record.status,
        record.enabled_for_chat,
        record.runtime_revision,
    )


def _apply(
    catalog: DataSourceCatalog,
    source_id: str,
    run_id: str,
    *,
    updates=None,
    metadata=None,
) -> dict:
    return catalog.apply_review_results(
        source_id,
        run_id,
        review_updates=_updates() if updates is None else updates,
        missing_keys=[("public", "missing_old_active")],
        history_snapshots=[],
        profiled_tables=5,
        discovered_metadata=_metadata() if metadata is None else metadata,
        automatic_policy=True,
    )


def test_pure_policy_fail_closed_contract() -> None:
    assert promote_policy("active", "present").effective_decision == "active"
    assert promote_policy("pending", "present").effective_decision == "standby"
    assert promote_policy("standby", "present").effective_decision == "standby"
    assert promote_policy("active", "missing").effective_decision == "standby"
    for proposed in ("", "invalid", "ACTIVE"):
        try:
            promote_policy(proposed, "present")
        except PolicyPromotionError:
            pass
        else:
            raise AssertionError("非法 proposed 必须使整轮 promotion 失败")
    try:
        promote_policy("active", "unknown")
    except PolicyPromotionError:
        pass
    else:
        raise AssertionError("非法 availability 必须使整轮 promotion 失败")


def test_atomic_promotion_scope_history_and_asset_invalidation() -> None:
    with tempfile.TemporaryDirectory(prefix="policy-atomic-") as raw:
        directory = Path(raw)
        catalog, source_id = _catalog(directory)
        record_before = catalog.require(source_id)
        fingerprint_before = catalog.review_policy(source_id)["fingerprint"]
        metadata_path = record_before.metadata_path
        memory_path = record_before.memory_path
        hashes_before = (
            hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            hashlib.sha256(memory_path.read_bytes()).hexdigest(),
        )
        run_id = "policy-success"
        _record_run(catalog, source_id, run_id)
        result = _apply(catalog, source_id, run_id)

        reviews = {
            row["table_name"]: row
            for row in catalog.list_table_reviews(source_id)
        }
        assert reviews["active_candidate"]["effective_decision"] == "active"
        for table in (
            "pending_candidate", "standby_candidate", "unknown_high_quality",
            "ineligible_high_quality", "missing_old_active",
        ):
            assert reviews[table]["effective_decision"] == "standby", table
            assert reviews[table]["decision_source"] == POLICY_SOURCE
        assert reviews["missing_old_active"]["availability_status"] == "missing"

        record = catalog.require(source_id)
        scope_tables = {
            (str(item.get("schema")), str(item.get("table")))
            for item in record.selected_scope
        }
        assert scope_tables == {("public", "active_candidate")}
        assert record.selected_tables_count == 1
        assert record.selected_columns_count == 2
        assert result["effective"] == {"active": 1, "standby": 5}
        assert record.status == "training_required"
        assert record.enabled_for_chat is False
        assert record.runtime_revision == 7
        assert catalog.review_policy(source_id)["fingerprint"] != fingerprint_before
        assert set(catalog.review_policy(source_id)["allowed_tables"]) == scope_tables
        assert record.metadata_path == metadata_path
        assert record.memory_path == memory_path
        assert hashes_before == (
            hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            hashlib.sha256(memory_path.read_bytes()).hexdigest(),
        )

        connection = sqlite3.connect(catalog.db_path)
        try:
            history = connection.execute(
                "SELECT table_name, effective_decision, availability_status "
                "FROM data_source_review_history WHERE run_id=?",
                (run_id,),
            ).fetchall()
            assert len(history) == 6
            assert dict((table, effective) for table, effective, _ in history) == {
                table: reviews[table]["effective_decision"] for table in reviews
            }
            run_status = connection.execute(
                "SELECT status FROM data_source_review_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            assert run_status == "succeeded"
        finally:
            connection.close()

        state = _snapshot(catalog, source_id)
        fingerprint = catalog.review_policy(source_id)["fingerprint"]
        _apply(catalog, source_id, run_id)
        assert _snapshot(catalog, source_id) == state
        assert catalog.review_policy(source_id)["fingerprint"] == fingerprint


def test_invalid_proposal_and_scope_failure_roll_back_all_state() -> None:
    for mode in ("invalid_proposal", "missing_active_metadata", "missing_run"):
        with tempfile.TemporaryDirectory(prefix=f"policy-rollback-{mode}-") as raw:
            catalog, source_id = _catalog(Path(raw))
            run_id = f"run-{mode}"
            if mode != "missing_run":
                _record_run(catalog, source_id, run_id)
            before = _snapshot(catalog, source_id)
            updates = _updates()
            metadata = _metadata()
            if mode == "invalid_proposal":
                updates[1][2]["proposed_decision"] = "invalid"
            elif mode == "missing_active_metadata":
                metadata = [
                    item for item in metadata
                    if item["table"] != "active_candidate"
                ]
            try:
                _apply(
                    catalog,
                    source_id,
                    run_id,
                    updates=updates,
                    metadata=metadata,
                )
            except (PolicyPromotionError, DataSourceCatalogError):
                pass
            else:
                raise AssertionError(f"{mode} 必须整体失败")
            assert _snapshot(catalog, source_id) == before
            connection = sqlite3.connect(catalog.db_path)
            try:
                assert connection.execute(
                    "SELECT count(*) FROM data_source_review_history WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0] == 0
            finally:
                connection.close()


def test_active_asset_batch_rejects_transaction_with_zero_writes() -> None:
    with tempfile.TemporaryDirectory(prefix="policy-lease-") as raw:
        directory = Path(raw)
        catalog, source_id = _catalog(directory)
        before = _snapshot(catalog, source_id)
        catalog.begin_asset_batch(
            source_id,
            batch_id="active-batch",
            candidate_root=directory / "candidate",
            candidate_memory=directory / "candidate-memory",
            published_memory_path=directory / "published-memory",
        )
        try:
            connection = sqlite3.connect(catalog.db_path)
            try:
                run_count_before = connection.execute(
                    "SELECT count(*) FROM data_source_review_runs"
                ).fetchone()[0]
            finally:
                connection.close()
            reviewer = DataSourceTableReviewer(
                catalog,
                _NeverConnector(),
                _NeverProfiler(),
            )
            try:
                reviewer.run_review(source_id)
            except DataSourceConflict:
                pass
            else:
                raise AssertionError("/review 必须在 discovery 前被租约拒绝")
            connection = sqlite3.connect(catalog.db_path)
            try:
                assert connection.execute(
                    "SELECT count(*) FROM data_source_review_runs"
                ).fetchone()[0] == run_count_before
            finally:
                connection.close()

            run_id = "policy-lease"
            _record_run(catalog, source_id, run_id)
            try:
                _apply(catalog, source_id, run_id)
            except DataSourceConflict:
                pass
            else:
                raise AssertionError("active asset batch 必须拒绝自动治理事务")
            assert _snapshot(catalog, source_id) == before
            connection = sqlite3.connect(catalog.db_path)
            try:
                assert connection.execute(
                    "SELECT count(*) FROM data_source_review_history WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0] == 0
            finally:
                connection.close()
        finally:
            catalog.finish_asset_batch(source_id, "active-batch")


def main() -> None:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    print(f"Policy transaction tests passed: {len(tests)}/{len(tests)}")


if __name__ == "__main__":
    main()

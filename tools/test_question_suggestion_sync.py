"""阶段 E-3：推荐问题派生资产同步任务回归测试。

覆盖规格 13.2（发布触发时机）、13.3（同步执行）、13.4（并发与 stale
worker）、13.6（启动恢复与手工重试）以及路径安全。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import DataSourceCatalogError
from backend.data_source_connectors import DataSourceAssetPreparer
from tools.test_data_source_publish_guard import (
    _prepare_with_memory,
    _save_scope,
    _seed_reviews,
    _setup,
)


class _LocalCollection:
    """随目录持久化的假 collection：候选目录安装到正式路径后仍可回读。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: list[tuple[str, str, dict]] = []
        self._load()

    def _records_file(self) -> Path:
        return self.path / ".records.json"

    def _load(self) -> None:
        records_file = self._records_file()
        if records_file.is_file():
            try:
                payload = json.loads(records_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = []
            self.records = [
                (str(item[0]), str(item[1]), dict(item[2]))
                for item in payload
            ]

    def _save(self) -> None:
        self._records_file().write_text(
            json.dumps(
                [
                    [record_id, document, metadata]
                    for record_id, document, metadata in self.records
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def add(self, *, ids, documents, metadatas) -> None:
        self.records = [
            (str(record_id), str(document), dict(metadata))
            for record_id, document, metadata in zip(
                ids, documents, metadatas, strict=True
            )
        ]
        self._save()

    def count(self) -> int:
        return len(self.records)

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
            wanted = set(map(str, ids))
            records = [item for item in records if item[0] in wanted]
        return {
            "ids": [item[0] for item in records],
            "documents": [item[1] for item in records],
            "metadatas": [item[2] for item in records],
        }


class _LocalFakeMemory:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._collection = _LocalCollection(path)
        self._executor = type(
            "Executor",
            (),
            {"shutdown": lambda self, wait: None},
        )()
        self._client = None

    def _get_collection(self):
        return self._collection


def _sql_record(source_id: str, sql: str, question: str = "查询各监测站点 pH 平均值"):
    record_id = "toolmem-v1-" + hashlib.sha256(sql.encode("utf-8")).hexdigest()[
        :16
    ]
    metadata = {
        "category": "sql_example",
        "tool_name": "run_sql",
        "args_json": json.dumps({"sql": sql}, ensure_ascii=False),
        "source_id": source_id,
        "content_fingerprint": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "question": question,
    }
    return (record_id, question, metadata)


SAFE_SQL = 'SELECT "name", "value" FROM "monitor_data"'


def _prepare(catalog, source_id, *, extra_sql=None, fault_injector=None):
    import backend.memory as memory_module

    preparer = DataSourceAssetPreparer(
        catalog,
        fault_injector=fault_injector,
    )
    with patch.object(
        memory_module,
        "create_memory",
        side_effect=_LocalFakeMemory,
    ):
        return preparer.prepare(
            source_id,
            extra_sql_tool_records=extra_sql,
        )


def _setup_source(root: Path):
    catalog, source_id, asset_root = _setup(root)
    _save_scope(catalog, source_id, ("monitor_data", "station_dict"))
    _seed_reviews(
        catalog,
        source_id,
        {
            "monitor_data": ("active", "present"),
            "station_dict": ("active", "present"),
        },
    )
    return catalog, source_id, asset_root


def _qs_root(root: Path) -> Path:
    path = root / "question_suggestions"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _FakeVerifier:
    def __init__(self, *, fail=False, fail_latest=False) -> None:
        self.fail = fail
        self.fail_latest = fail_latest
        self.closed = False

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def resolve_latest_day(self, sql: str):
        if self.fail_latest:
            raise RuntimeError("db unreachable")
        return date(2025, 5, 1)

    def verify(self, sql: str):
        if self.fail:
            raise RuntimeError("db verify failed")
        return {"verified": True, "read_only": True}


def _run_pending(
    catalog,
    qs_root: Path,
    *,
    source_id=None,
    verifier=None,
    no_db_verify=True,
    limit=5,
):
    from backend.question_suggestion_sync import (
        process_pending_question_suggestion_jobs,
    )

    return process_pending_question_suggestion_jobs(
        catalog,
        asset_root=qs_root,
        source_id=source_id,
        limit=limit,
        memory_factory=_LocalFakeMemory,
        no_db_verify=no_db_verify,
        verifier=verifier,
    )


def _collection(catalog, source_id):
    record = catalog.require(source_id)
    memory = _LocalFakeMemory(record.memory_path)
    return memory._get_collection()


def _asset_path(qs_root: Path, source_id: str) -> Path:
    return qs_root / source_id / "questions_v1.json"


def test_publish_success_creates_exactly_one_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-job-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            result = _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            assert result["runtime_revision"] == 1
            jobs = catalog.list_question_suggestion_jobs(source_id)
            assert len(jobs) == 1
            assert jobs[0]["target_runtime_revision"] == 1
            assert jobs[0]["status"] == "pending"
            assert jobs[0]["target_metadata_sha256"]
            assert jobs[0]["target_scope_fingerprint"]
            assert jobs[0]["target_review_policy_fingerprint"]
            assert jobs[0]["target_provenance_hash"]
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_second_prepare_creates_only_rev2_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-rev2-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            _prepare(catalog, source_id)
            jobs = catalog.list_question_suggestion_jobs(source_id)
            revisions = sorted(job["target_runtime_revision"] for job in jobs)
            assert revisions == [1, 2]
            assert all(job["status"] == "pending" for job in jobs)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_candidate_stage_failure_creates_no_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-cand-fail-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            def fail_at(point: str) -> None:
                if point == "after_candidate_metadata":
                    raise RuntimeError("candidate 阶段注入失败")

            try:
                _prepare(catalog, source_id, fault_injector=fail_at)
            except RuntimeError:
                pass
            else:
                raise AssertionError("候选阶段失败未触发")
            assert not catalog.list_question_suggestion_jobs(source_id)
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_e2a_failure_creates_no_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-e2a-fail-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            with patch(
                "backend.data_source_asset_validator.validate_candidate_assets",
                side_effect=DataSourceCatalogError("E-2A 注入失败"),
            ):
                try:
                    _prepare(catalog, source_id)
                except DataSourceCatalogError:
                    pass
                else:
                    raise AssertionError("E-2A 失败未触发")
            assert not catalog.list_question_suggestion_jobs(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_e2b_failure_creates_no_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-e2b-fail-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            with patch(
                "backend.data_source_runtime_asset_validator.validate_runtime_candidate_assets",
                side_effect=DataSourceCatalogError("E-2B 注入失败"),
            ):
                try:
                    _prepare(catalog, source_id)
                except DataSourceCatalogError:
                    pass
                else:
                    raise AssertionError("E-2B 失败未触发")
            assert not catalog.list_question_suggestion_jobs(source_id)
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_post_publish_rollback_creates_no_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-postpub-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            def fail_at(point: str) -> None:
                if point == "after_catalog_publish":
                    raise RuntimeError("publish 后注入失败")

            try:
                _prepare(catalog, source_id, fault_injector=fail_at)
            except RuntimeError:
                pass
            else:
                raise AssertionError("publish 后失败未触发")
            assert not catalog.list_question_suggestion_jobs(source_id)
            assert catalog.require(source_id).runtime_revision == 0
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_normal_sync_generates_current_revision_asset() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-sync-ok-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            processed = _run_pending(
                catalog,
                qs_root,
                verifier=_FakeVerifier(),
                no_db_verify=False,
            )
            assert processed == [
                catalog.list_question_suggestion_jobs(source_id)[0]["job_id"]
            ]
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "succeeded"
            payload = json.loads(
                _asset_path(qs_root, source_id).read_text(encoding="utf-8")
            )
            assert payload["schema_version"] == 2
            assert payload["source_id"] == source_id
            assert payload["runtime_revision"] == 1
            assert payload["metadata_sha256"] == job["target_metadata_sha256"]
            assert payload["scope_fingerprint"] == job[
                "target_scope_fingerprint"
            ]
            assert payload["review_policy_fingerprint"] == job[
                "target_review_policy_fingerprint"
            ]
            assert payload["provenance_hash"] == job[
                "target_provenance_hash"
            ]
            assert payload["questions"]
            assert not list(
                (qs_root / source_id).glob(".questions.candidate-*")
            )
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_no_sql_tool_memory_generates_empty_asset_succeeded() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-empty-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(catalog, source_id)
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "succeeded"
            payload = json.loads(
                _asset_path(qs_root, source_id).read_text(encoding="utf-8")
            )
            assert payload["questions"] == []
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_missing_sql_tool_memory_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-missing-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            collection = _collection(catalog, source_id)
            collection.records = [
                item for item in collection.records if "toolmem" not in item[0]
            ]
            collection._save()
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
            assert "缺失" in job["last_error"]
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_sql_fingerprint_mismatch_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-fp-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            collection = _collection(catalog, source_id)
            mutated = []
            for item in collection.records:
                if "toolmem" in item[0]:
                    record_id, document, metadata = item
                    metadata = dict(metadata)
                    metadata["content_fingerprint"] = "deadbeef"
                    mutated.append((record_id, document, metadata))
                else:
                    mutated.append(item)
            collection.records = mutated
            collection._save()
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
            assert "fingerprint" in job["last_error"]
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_sqlguard_reject_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-guard-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            collection = _collection(catalog, source_id)
            mutated = []
            for item in collection.records:
                if "toolmem" in item[0]:
                    record_id, document, metadata = item
                    metadata = dict(metadata)
                    metadata["args_json"] = json.dumps(
                        {"sql": 'SELECT * FROM "public"."monitor_data"'}
                    )
                    mutated.append((record_id, document, metadata))
                else:
                    mutated.append(item)
            collection.records = mutated
            collection._save()
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
            assert "SQLGuard" in job["last_error"]
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_db_verify_failure_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-dbfail-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            _run_pending(
                catalog,
                qs_root,
                verifier=_FakeVerifier(fail=True),
                no_db_verify=False,
            )
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_failure_keeps_old_asset_unchanged() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-oldasset-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            target = _asset_path(qs_root, source_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"old": true}', encoding="utf-8")
            collection = _collection(catalog, source_id)
            collection.records = [
                item for item in collection.records if "toolmem" not in item[0]
            ]
            collection._save()
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            assert target.read_text(encoding="utf-8") == '{"old": true}'
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_stale_worker_does_not_overwrite() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-stale-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            from backend.question_suggestion_sync import (
                run_question_suggestion_job,
            )

            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )  # revision 1，job1 pending
            job1 = catalog.list_question_suggestion_jobs(source_id)[0]
            claimed1 = catalog.claim_question_suggestion_job(job1["job_id"])
            assert claimed1 is not None
            _prepare(catalog, source_id)  # revision 2，job2 pending
            jobs = catalog.list_question_suggestion_jobs(source_id)
            revisions = sorted(j["target_runtime_revision"] for j in jobs)
            assert revisions == [1, 2]

            status1, _ = run_question_suggestion_job(
                catalog,
                claimed1,
                asset_root=qs_root,
                memory_factory=_LocalFakeMemory,
                no_db_verify=True,
                verifier=_FakeVerifier(),
            )
            assert status1 == "superseded"
            refreshed1 = next(
                j for j in catalog.list_question_suggestion_jobs(source_id)
                if j["job_id"] == job1["job_id"]
            )
            assert refreshed1["status"] == "superseded"
            assert not _asset_path(qs_root, source_id).exists()

            _run_pending(
                catalog,
                qs_root,
                verifier=_FakeVerifier(),
                no_db_verify=False,
            )
            job2 = next(
                j
                for j in catalog.list_question_suggestion_jobs(source_id)
                if j["target_runtime_revision"] == 2
            )
            assert job2["status"] == "succeeded"
            payload = json.loads(
                _asset_path(qs_root, source_id).read_text(encoding="utf-8")
            )
            assert payload["runtime_revision"] == 2
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_double_enqueue_single_task() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-dbl-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            _prepare(catalog, source_id)
            from backend.question_suggestion_sync import (
                enqueue_for_published_source,
            )

            enqueue_for_published_source(catalog, source_id)
            enqueue_for_published_source(catalog, source_id)
            jobs = catalog.list_question_suggestion_jobs(source_id)
            assert len(jobs) == 1
            assert jobs[0]["status"] == "pending"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_claim_is_exclusive() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-claim-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            _prepare(catalog, source_id)
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            first = catalog.claim_question_suggestion_job(job["job_id"])
            second = catalog.claim_question_suggestion_job(job["job_id"])
            assert first is not None and first["status"] == "running"
            assert second is None
            assert (
                catalog.list_question_suggestion_jobs(source_id)[0][
                    "attempt_count"
                ]
                == 1
            )
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_reset_stale_running_job() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-reset-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            _prepare(catalog, source_id)
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            catalog.claim_question_suggestion_job(job["job_id"])
            assert (
                catalog.list_question_suggestion_jobs(source_id)[0]["status"]
                == "running"
            )
            count = catalog.reset_stale_question_suggestion_jobs(source_id)
            assert count == 1
            assert (
                catalog.list_question_suggestion_jobs(source_id)[0]["status"]
                == "pending"
            )
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_reconcile_marks_old_revision_superseded() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-rec-old-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(catalog, source_id)  # rev1
            job1 = catalog.list_question_suggestion_jobs(source_id)[0]
            catalog.claim_question_suggestion_job(job1["job_id"])
            _prepare(catalog, source_id)  # rev2
            from backend.question_suggestion_sync import (
                reconcile_question_suggestion_jobs,
            )

            reconcile_question_suggestion_jobs(
                catalog,
                asset_root=qs_root,
            )
            refreshed1 = next(
                j
                for j in catalog.list_question_suggestion_jobs(source_id)
                if j["job_id"] == job1["job_id"]
            )
            assert refreshed1["status"] == "superseded"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_reconcile_enqueues_missing_asset() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-rec-miss-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(catalog, source_id)
            from backend.question_suggestion_sync import (
                reconcile_question_suggestion_jobs,
            )

            reconcile_question_suggestion_jobs(
                catalog,
                asset_root=qs_root,
            )
            jobs = catalog.list_question_suggestion_jobs(source_id)
            assert len(jobs) == 1
            assert jobs[0]["status"] == "pending"
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_reconcile_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-rec-idem-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(catalog, source_id)
            from backend.question_suggestion_sync import (
                reconcile_question_suggestion_jobs,
            )

            reconcile_question_suggestion_jobs(
                catalog,
                asset_root=qs_root,
            )
            reconcile_question_suggestion_jobs(
                catalog,
                asset_root=qs_root,
            )
            assert len(catalog.list_question_suggestion_jobs(source_id)) == 1
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_retry_after_failed() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-retry-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        qs_root = _qs_root(root)
        try:
            _prepare(
                catalog,
                source_id,
                extra_sql=[_sql_record(source_id, SAFE_SQL)],
            )
            collection = _collection(catalog, source_id)
            collection.records = [
                item for item in collection.records if "toolmem" not in item[0]
            ]
            collection._save()
            _run_pending(catalog, qs_root, verifier=_FakeVerifier())
            job = catalog.list_question_suggestion_jobs(source_id)[0]
            assert job["status"] == "failed"
            from backend.question_suggestion_sync import (
                retry_question_suggestions,
            )

            retried = retry_question_suggestions(catalog, source_id)
            assert retried["job_id"] == job["job_id"]
            assert retried["status"] == "pending"
            assert len(catalog.list_question_suggestion_jobs(source_id)) == 1
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_retry_only_current_revision() -> None:
    with tempfile.TemporaryDirectory(prefix="e3-retry-rev-") as directory:
        root = Path(directory)
        catalog, source_id, asset_root = _setup_source(root)
        try:
            _prepare(catalog, source_id)
            _prepare(catalog, source_id)
            from backend.question_suggestion_sync import (
                retry_question_suggestions,
            )

            retried = retry_question_suggestions(catalog, source_id)
            assert retried["target_runtime_revision"] == 2
            assert len(catalog.list_question_suggestion_jobs(source_id)) == 2
        finally:
            shutil.rmtree(asset_root, ignore_errors=True)


def test_path_safety() -> None:
    from backend.question_suggestion_assets import (
        asset_path,
        write_question_candidate,
    )

    with tempfile.TemporaryDirectory(prefix="e3-path-") as directory:
        root = Path(directory)
        for bad in ("../escape", "a/b", "a\\b", ".."):
            try:
                asset_path(bad, root=root)
            except ValueError:
                pass
            else:
                raise AssertionError(f"非法 source_id 未拒绝：{bad}")
        directory_payload = {
            "schema_version": 2,
            "source_id": "source-a",
            "questions": [],
        }
        try:
            write_question_candidate(
                directory_payload,
                root=root,
                candidate_name="../escape",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("非法 candidate_name 未拒绝")


def test_error_never_leaks_credentials() -> None:
    from backend.question_suggestion_sync import _safe_error

    sanitized = _safe_error(
        RuntimeError(
            "connect failed password=supersecret "
            "postgresql://user:hunter2@127.0.0.1:5433/db"
        )
    )
    assert "supersecret" not in sanitized
    assert "hunter2" not in sanitized
    assert "password=***" in sanitized
    assert "://***:***@" in sanitized


def main() -> int:
    import traceback

    failed = 0
    total = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        total += 1
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

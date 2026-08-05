"""Service：发布、去重、冲突、stale revision 重验、失败回滚标记。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.learning_candidate_store import LearningCandidateStore
from backend.runtime_learning_models import LearningCandidate
from backend.runtime_learning_service import (
    LearningPublishError,
    RuntimeLearningService,
    RuntimeLearningServiceError,
    _training_level_for,
    tool_memory_record_id,
)
from config.learning_settings import OnlineLearningSettings

ALLOWED_LEVELS = {
    "level2_sql_examples",
    "level2_mysql_sql_examples",
    "level3_sql_examples",
    "level3_p0_sql_examples",
    "level3_p1_sql_examples",
    "level3_p2_sql_examples",
}


class _DummyJudge:
    async def judge(self, candidate, metadata_context=None):
        from backend.runtime_learning_models import JudgeVerdict

        return JudgeVerdict(
            verdict="PASS",
            confidence=0.98,
            question_sql_aligned=True,
            answer_result_aligned=True,
            metadata_valid=True,
            business_ambiguity=False,
            risk_flags=[],
            reason="ok",
        )


class FakeCatalog:
    def __init__(self, record):
        self._record = record
        self.record = record

    def require(self, source_id):
        return self._record


def _settings(tmp_path, **overrides) -> OnlineLearningSettings:
    base = dict(
        enabled=True,
        capture_enabled=True,
        judge_enabled=True,
        auto_publish=False,
        judge_min_confidence=0.95,
        batch_size=10,
        batch_max_wait_seconds=600,
        worker_interval_seconds=30,
        max_result_rows=20,
        max_result_bytes=65536,
        max_judge_attempts=3,
        candidate_db_path=tmp_path / "learning_candidates.sqlite3",
    )
    base.update(overrides)
    return OnlineLearningSettings(**base)


def _record(tmp_path, *, revision=3, **overrides) -> SimpleNamespace:
    base = dict(
        source_id="pg-main",
        display_name="d",
        description="desc",
        database_type="postgresql",
        status="ready",
        enabled_for_chat=True,
        runtime_revision=revision,
        memory_path=tmp_path / "no-such-chroma-dir",
        metadata_path=tmp_path / "metadata.json",
        selected_scope=({"table": "t1", "column": "station_id"},),
        discovered_metadata=(
            {"table": "t1", "column": "station_id"},
            {"table": "t1", "column": "value"},
        ),
        capabilities=(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _candidate(tmp_path, *, candidate_id="c1", status="pass", **overrides) -> LearningCandidate:
    evidence = {
        "columns": ["station_id", "value"],
        "rows": [["s1", 1.0]],
        "row_count": 1,
        "total_row_count": 1,
        "truncated": False,
        "max_rows": 20,
        "numeric_summary": {},
        "result_sha256": "abc",
    }
    base = dict(
        candidate_id=candidate_id,
        source_id="pg-main",
        conversation_id="conv-1",
        request_id="req-1",
        database_type="postgresql",
        captured_runtime_revision=3,
        question=f"查询{candidate_id}最近记录",
        normalized_question=f"查询{candidate_id}最近记录",
        question_sha256="q",
        sql="SELECT station_id, value FROM t1 LIMIT 5",
        normalized_sql="select station_id, value from t1 limit 5",
        sql_sha256="s",
        args_json=json.dumps({"sql": "SELECT station_id, value FROM t1 LIMIT 5"}, ensure_ascii=False, sort_keys=True),
        content_fingerprint=f"fp-{candidate_id}",
        guard_result_json=json.dumps(
            {
                "passed": True,
                "severity": "ok",
                "used_tables": ["t1"],
                "used_columns": ["t1.station_id", "t1.value"],
                "forbidden_operations": [],
                "reason": "ok",
            },
            ensure_ascii=False,
        ),
        result_summary_json="{}",
        result_evidence_json=json.dumps(evidence, ensure_ascii=False),
        final_answer=f"{candidate_id} 查到 1 条。",
        answer_sha256="a",
        status=status,
        judge_confidence=0.98,
        judge_verdict="PASS",
        created_at=1.0,
        updated_at=1.0,
    )
    base.update(overrides)
    return LearningCandidate(**base)


def _make_service(tmp_path, record, **settings_overrides):
    store = LearningCandidateStore(_settings(tmp_path, **settings_overrides).candidate_db_path)
    settings = _settings(tmp_path, **settings_overrides)
    return RuntimeLearningService(
        catalog=FakeCatalog(record),
        runtime_manager=object(),
        store=store,
        judge=_DummyJudge(),
        settings=settings,
    ), store


def test_build_tool_record_structure(tmp_path):
    record = _record(tmp_path)
    service, _ = _make_service(tmp_path, record)
    candidate = _candidate(tmp_path, status="publish_pending")
    record_id, document, metadata = service._build_tool_record(candidate, 3)
    assert record_id.startswith("toolmem-v1-")
    assert metadata["tool_name"] == "run_sql"
    assert metadata["category"] == "sql_example"
    assert metadata["source_id"] == "pg-main"
    assert metadata["train_decision"] == "approved"
    assert metadata["origin"] == "runtime_learning"
    assert metadata["training_level"] in ALLOWED_LEVELS
    assert "content_fingerprint" in metadata
    assert document == candidate.question
    # record_id 确定性
    again, _, _ = service._build_tool_record(candidate, 3)
    assert again == record_id


def test_training_level_classification():
    assert (
        _training_level_for(database_type="postgresql", sql="SELECT a FROM t LIMIT 1", used_tables=["t"])
        == "level2_sql_examples"
    )
    assert (
        _training_level_for(database_type="mysql", sql="SELECT a FROM t LIMIT 1", used_tables=["t"])
        == "level2_mysql_sql_examples"
    )
    assert (
        _training_level_for(database_type="postgresql", sql="SELECT a, COUNT(*) FROM t GROUP BY a", used_tables=["t"])
        == "level3_p1_sql_examples"
    )
    assert (
        _training_level_for(database_type="postgresql", sql="SELECT a FROM t1 JOIN t2 ON t1.id=t2.id LIMIT 1", used_tables=["t1", "t2"])
        == "level3_p1_sql_examples"
    )


def test_tool_memory_record_id_deterministic():
    r1 = tool_memory_record_id(question="q", sql="SELECT 1", database_type="postgresql")
    r2 = tool_memory_record_id(question="q", sql="SELECT 1", database_type="postgresql")
    assert r1 == r2
    assert r1.startswith("toolmem-v1-")


async def test_publish_success(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="c1", status="pass"))
    store.save_candidate(_candidate(tmp_path, candidate_id="c2", status="publish_pending"))

    captured = {}

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            self.catalog = catalog
            self.runtime_manager = runtime_manager

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            captured["payload"] = extra_sql_tool_records
            return {
                "source_id": source_id,
                "runtime_revision": 4,
                "status": "ready",
                "sql_tool_memory_count": len(extra_sql_tool_records or []),
            }

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 2
    assert result["runtime_revision_after"] == 4
    assert len(captured["payload"]) == 2
    assert store.get_candidate("c1").status == "published"
    assert store.get_candidate("c2").status == "published"
    assert store.get_candidate("c1").published_runtime_revision == 4
    batch = store.get_publish_batch(result["batch_id"])
    assert batch["status"] == "committed"


async def test_publish_failure_marks_publish_failed(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="c1", status="pass"))

    class FailingPreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            raise RuntimeError("Chroma 构建失败")

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FailingPreparer,
    )
    with pytest.raises(LearningPublishError):
        await service.publish_source("pg-main", force=True)
    assert store.get_candidate("c1").status == "publish_failed"
    assert "Chroma 构建失败" in str(store.get_candidate("c1").last_error)


async def test_content_dedup_supersedes(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="c1", status="pass"))

    # 模拟正式 Memory 已有同 content_fingerprint 样本
    service._existing_sql_examples = lambda record: [
        {
            "content_fingerprint": "fp-c1",
            "question": "查询c1最近记录",
            "sql": "SELECT station_id, value FROM t1 LIMIT 5",
        }
    ]

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            return {"runtime_revision": 4, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 0
    assert result["skipped"] == 1
    assert store.get_candidate("c1").status == "superseded"


async def test_same_question_different_sql_conflict(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="c1", status="pass"))

    service._existing_sql_examples = lambda record: [
        {
            "content_fingerprint": "other",
            "question": "查询c1最近记录",
            "sql": "SELECT station_id, value FROM t1 LIMIT 10",  # 不同 SQL
        }
    ]

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            return {"runtime_revision": 4, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 0
    assert store.get_candidate("c1").status == "needs_review"
    assert store.get_candidate("c1").conflict_status == "conflict"


async def test_stale_revision_revalidation_success(tmp_path, monkeypatch):
    # 当前 revision=4，候选 captured=3 -> 触发 SQLGuard 复检
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            [
                {"table": "t1", "column": "station_id"},
                {"table": "t1", "column": "value"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = _record(tmp_path, revision=4, metadata_path=metadata_path)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(
        _candidate(tmp_path, candidate_id="c1", status="pass", captured_runtime_revision=3)
    )

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            return {"runtime_revision": 5, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 1
    # reviewed_runtime_revision 更新为 4
    assert store.get_candidate("c1").reviewed_runtime_revision == 4


async def test_stale_revision_field_missing_rejected(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            [{"table": "t1", "column": "station_id"}],  # 缺 value 字段
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = _record(tmp_path, revision=4, metadata_path=metadata_path)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(
        _candidate(tmp_path, candidate_id="c1", status="pass", captured_runtime_revision=3)
    )

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            return {"runtime_revision": 5, "status": "ready"}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 0
    assert store.get_candidate("c1").status == "reject"


async def test_per_source_batching(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="c1", status="pass"))
    store.save_candidate(
        _candidate(
            tmp_path,
            candidate_id="m1",
            status="pass",
            source_id="mysql-a",
            database_type="mysql",
            sql="SELECT a FROM t2 LIMIT 5",
            normalized_sql="select a from t2 limit 5",
            args_json=json.dumps({"sql": "SELECT a FROM t2 LIMIT 5"}),
            guard_result_json=json.dumps(
                {
                    "passed": True,
                    "severity": "ok",
                    "used_tables": ["t2"],
                    "used_columns": [],
                    "forbidden_operations": [],
                    "reason": "ok",
                }
            ),
        )
    )

    published_sources = []

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            published_sources.append(source_id)
            return {"runtime_revision": 4, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 1
    assert published_sources == ["pg-main"]
    # 其他源不受影响
    assert store.get_candidate("m1").status == "pass"


async def test_reject_needs_review_never_published(tmp_path, monkeypatch):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record)
    store.save_candidate(_candidate(tmp_path, candidate_id="r1", status="reject"))
    store.save_candidate(_candidate(tmp_path, candidate_id="n1", status="needs_review"))
    store.save_candidate(_candidate(tmp_path, candidate_id="p1", status="pass"))

    class FakePreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            return {"runtime_revision": 4, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        FakePreparer,
    )
    result = await service.publish_source("pg-main", force=True)
    assert result["published"] == 1
    assert store.get_candidate("r1").status == "reject"
    assert store.get_candidate("n1").status == "needs_review"


async def test_auto_publish_off_requires_force(tmp_path):
    record = _record(tmp_path, revision=3)
    service, store = _make_service(tmp_path, record, auto_publish=False)
    with pytest.raises(RuntimeLearningServiceError) as exc_info:
        await service.publish_source("pg-main")
    assert "自动发布未开启" in str(exc_info.value)

"""Worker：生命周期、恢复、并发保护、异常隔离、自动发布门禁。"""

from __future__ import annotations

import asyncio

import pytest

from backend.learning_candidate_store import LearningCandidateStore
from backend.runtime_learning_models import LearningCandidate
from backend.runtime_learning_service import RuntimeLearningService
from backend.runtime_learning_worker import RuntimeLearningWorker
from config.learning_settings import OnlineLearningSettings


class FakeService:
    """记录调用的假 Service，满足 Worker 鸭子类型。"""

    def __init__(self, staged=None, ready_sources=None):
        self.staged = list(staged or [])
        self.ready_sources = list(ready_sources or [])
        self.recovered = 0
        self.judged: list[str] = []
        self.published: list[str] = []

    def recover_interrupted(self):
        self.recovered += 1
        return {}

    def list_candidates(self, **kwargs):
        return self.staged

    async def judge_candidate(self, candidate_id: str):
        self.judged.append(candidate_id)

    def publish_ready_source_ids(self):
        return self.ready_sources

    async def publish_source(self, source_id: str, force: bool = False):
        self.published.append(source_id)
        return {"source_id": source_id, "published": 1}


def _settings(tmp_path, **overrides) -> OnlineLearningSettings:
    base = dict(
        enabled=True,
        capture_enabled=True,
        judge_enabled=True,
        auto_publish=False,
        judge_min_confidence=0.95,
        batch_size=1,
        batch_max_wait_seconds=600,
        worker_interval_seconds=1,
        max_result_rows=20,
        max_result_bytes=65536,
        max_judge_attempts=3,
        candidate_db_path=tmp_path / "learning_candidates.sqlite3",
    )
    base.update(overrides)
    return OnlineLearningSettings(**base)


async def test_start_stop_cleanly(tmp_path):
    service = FakeService()
    worker = RuntimeLearningWorker(service, _settings(tmp_path))
    assert worker.running is False  # import/构造不启动
    worker.start()
    assert worker.running is True
    await asyncio.sleep(0.05)
    await worker.stop()
    assert worker.running is False


async def test_recover_and_judge(tmp_path):
    staged = [LearningCandidate(
        candidate_id="c1",
        source_id="pg-main",
        conversation_id="c",
        request_id="r",
        database_type="postgresql",
        captured_runtime_revision=1,
        question="q",
        normalized_question="q",
        question_sha256="q",
        sql="SELECT 1",
        normalized_sql="select 1",
        sql_sha256="s",
        args_json="{}",
        content_fingerprint="f",
        guard_result_json="{}",
        result_summary_json="{}",
        result_evidence_json="{}",
        final_answer="a",
        answer_sha256="a",
        created_at=1.0,
        updated_at=1.0,
    )]
    service = FakeService(staged=staged)
    worker = RuntimeLearningWorker(service, _settings(tmp_path))
    result = await worker.run_once()
    assert service.recovered == 1
    assert service.judged == ["c1"]
    assert result["judged"] == 1


async def test_auto_publish_off_no_publish(tmp_path):
    service = FakeService(ready_sources=["pg-main"])
    worker = RuntimeLearningWorker(service, _settings(tmp_path, auto_publish=False))
    await worker.run_once()
    assert service.published == []


async def test_auto_publish_on_publishes_ready(tmp_path):
    service = FakeService(ready_sources=["pg-main"])
    worker = RuntimeLearningWorker(service, _settings(tmp_path, auto_publish=True))
    result = await worker.run_once()
    assert service.published == ["pg-main"]
    assert "pg-main:1" in result["published"]


async def test_worker_exception_does_not_escape(tmp_path):
    class BrokenService(FakeService):
        async def judge_candidate(self, candidate_id: str):
            raise RuntimeError("judge 失败")

    service = BrokenService(staged=[_minimal_candidate("c1")])
    worker = RuntimeLearningWorker(service, _settings(tmp_path))
    # run_once 不抛出，异常被吞掉
    result = await worker.run_once()
    assert result["judged"] == 0


def _minimal_candidate(candidate_id: str) -> LearningCandidate:
    return LearningCandidate(
        candidate_id=candidate_id,
        source_id="pg-main",
        conversation_id="c",
        request_id="r",
        database_type="postgresql",
        captured_runtime_revision=1,
        question="q",
        normalized_question="q",
        question_sha256="q",
        sql="SELECT 1",
        normalized_sql="select 1",
        sql_sha256="s",
        args_json="{}",
        content_fingerprint="f",
        guard_result_json="{}",
        result_summary_json="{}",
        result_evidence_json="{}",
        final_answer="a",
        answer_sha256="a",
        created_at=1.0,
        updated_at=1.0,
    )


async def test_no_concurrent_judge_same_candidate(tmp_path):
    """同候选并发 Judge：状态机拒绝第二次。"""
    settings = _settings(tmp_path)
    store = LearningCandidateStore(settings.candidate_db_path)
    store.save_candidate(_minimal_candidate("c1"))

    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingJudge:
        async def judge(self, candidate, metadata_context=None):
            entered.set()
            await release.wait()
            from backend.runtime_learning_models import JudgeVerdict

            return JudgeVerdict(
                verdict="PASS", confidence=0.98, reason="ok"
            )

    service = RuntimeLearningService(
        catalog=object(),
        runtime_manager=object(),
        store=store,
        judge=BlockingJudge(),
        settings=settings,
    )

    task_a = asyncio.create_task(service.judge_candidate("c1"))
    await entered.wait()
    # 第二次调用：候选已处于 judging 状态 -> 被拒绝
    with pytest.raises(Exception) as exc_info:
        await service.judge_candidate("c1")
    assert "judging" in str(exc_info.value) or "正在判断" in str(exc_info.value)
    release.set()
    await task_a
    # 首次调用成功 -> pass
    assert store.get_candidate("c1").status in {"pass", "needs_review", "reject"}


async def test_no_concurrent_publish_same_source(tmp_path, monkeypatch):
    """同源并发发布：第二次立即冲突。"""
    settings = _settings(tmp_path, auto_publish=True)
    record = type("R", (), {
        "source_id": "pg-main",
        "status": "ready",
        "enabled_for_chat": True,
        "runtime_revision": 1,
        "memory_path": tmp_path / "no-chroma",
        "metadata_path": tmp_path / "m.json",
        "selected_scope": ({"table": "t1"},),
        "discovered_metadata": ({"table": "t1", "column": "a"},),
        "database_type": "postgresql",
        "capabilities": (),
    })
    catalog = type("C", (), {"require": lambda self, sid: record})()
    store = LearningCandidateStore(settings.candidate_db_path)
    store.save_candidate(_minimal_candidate("c1"))
    store.transition("c1", "judging")
    store.transition("c1", "pass")
    store.transition("c1", "publish_pending")

    import time

    class SlowPreparer:
        def __init__(self, catalog, runtime_manager):
            pass

        def prepare(self, source_id, *, extra_sql_tool_records=None):
            time.sleep(0.3)
            return {"runtime_revision": 2, "status": "ready", "sql_tool_memory_count": 0}

    monkeypatch.setattr(
        "backend.runtime_learning_service.DataSourceAssetPreparer",
        SlowPreparer,
    )
    service = RuntimeLearningService(
        catalog=catalog,
        runtime_manager=object(),
        store=store,
        judge=object(),
        settings=settings,
    )

    task_a = asyncio.create_task(service.publish_source("pg-main", force=True))
    await asyncio.sleep(0.05)
    # A 正在发布 -> 第二次立即冲突
    with pytest.raises(Exception) as exc_info:
        await service.publish_source("pg-main", force=True)
    assert "正在发布" in str(exc_info.value)
    await task_a

"""候选库：schema、WAL、幂等、状态机、隔离、重启持久。"""

from __future__ import annotations

import sqlite3

import pytest

from backend.learning_candidate_store import (
    LearningCandidateConflict,
    LearningCandidateStore,
    LearningStateMachineError,
    normalize_question,
    normalize_sql,
)
from backend.runtime_learning_models import LearningCandidate


def _candidate(**overrides) -> LearningCandidate:
    base = dict(
        candidate_id="c1",
        source_id="pg-main",
        conversation_id="conv-1",
        request_id="req-1",
        database_type="postgresql",
        captured_runtime_revision=3,
        question="查询最近5条记录",
        normalized_question="查询最近5条记录",
        question_sha256="q",
        sql="SELECT * FROM t1 LIMIT 5",
        normalized_sql=normalize_sql("SELECT * FROM t1 LIMIT 5"),
        sql_sha256="s",
        args_json='{"sql": "SELECT * FROM t1 LIMIT 5"}',
        content_fingerprint="fp",
        guard_result_json='{"passed": true, "severity": "ok"}',
        result_summary_json="{}",
        result_evidence_json="{}",
        final_answer="共 5 条",
        answer_sha256="a",
        created_at=1.0,
        updated_at=1.0,
    )
    base.update(overrides)
    return LearningCandidate(**base)


@pytest.fixture()
def store(tmp_path):
    return LearningCandidateStore(tmp_path / "learning_candidates.sqlite3")


def test_schema_auto_created_and_wal(store):
    assert store.db_path.exists()
    conn = sqlite3.connect(str(store.db_path))
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"
    tables = {
        item[0]
        for item in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "learning_candidates",
        "learning_judge_runs",
        "learning_publish_batches",
        "learning_schema_versions",
    } <= tables
    conn.close()


def test_idempotent_insert(store):
    assert store.save_candidate(_candidate()) is True
    assert store.save_candidate(_candidate()) is False
    assert len(store.list_candidates()) == 1
    assert store.count_by_status()["staged"] == 1


def test_state_machine_invalid_transition_rejected(store):
    store.save_candidate(_candidate())
    store.transition("c1", "judging")
    # published -> staged 非法
    with pytest.raises(LearningStateMachineError):
        store.transition("c1", "published")
    # judging -> published 非法（需先 judge 出结论）
    with pytest.raises(LearningStateMachineError):
        store.transition("c1", "published")


def test_same_source_duplicate_candidate_same_id(store):
    """同源同问同 SQL 重复捕获不产生多条有效记录。"""
    store.save_candidate(_candidate())
    store.save_candidate(_candidate())
    assert len(store.list_candidates(source_id="pg-main")) == 1


def test_different_source_same_sql_not_conflict(store):
    """不同源相同 SQL 是不同候选，不冲突。"""
    store.save_candidate(_candidate(candidate_id="a", source_id="pg-main"))
    store.save_candidate(_candidate(candidate_id="b", source_id="mysql-a"))
    assert len(store.list_candidates()) == 2
    assert len(store.list_candidates(source_id="pg-main")) == 1
    assert len(store.list_candidates(source_id="mysql-a")) == 1


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "db.sqlite3"
    first = LearningCandidateStore(path)
    first.save_candidate(_candidate())
    second = LearningCandidateStore(path)
    assert len(second.list_candidates()) == 1


def test_update_fields_without_status_change(store):
    store.save_candidate(_candidate())
    updated = store.update_fields("c1", reviewed_runtime_revision=9)
    assert updated.reviewed_runtime_revision == 9
    assert updated.status == "staged"


def test_recover_interrupted(store):
    store.save_candidate(_candidate(candidate_id="a"))
    store.transition("a", "judging")
    store.save_candidate(_candidate(candidate_id="b"))
    # staged -> judging -> pass -> publish_pending -> publishing（合法路径）
    store.transition("b", "judging")
    store.transition("b", "pass")
    store.transition("b", "publish_pending")
    store.transition("b", "publishing")
    recovered = store.recover_interrupted()
    assert recovered["judging_recovered"] == 1
    assert recovered["publishing_recovered"] == 1
    assert store.get_candidate("a").status == "staged"
    assert store.get_candidate("b").status == "publish_failed"


def test_normalization_helpers():
    assert normalize_question("  查询 最近  5条记录 。  ") == "查询最近5条记录"
    assert normalize_question("查询河流。") == "查询河流"
    assert normalize_sql("SELECT  a  FROM t1\n-- 注释\nWHERE x=1") == "select a from t1 where x=1"

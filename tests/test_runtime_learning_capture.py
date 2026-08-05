"""捕获硬门禁 + 结果证据 + 最终回答去重。"""

from __future__ import annotations

import json

import pytest

from backend.learning_candidate_store import LearningCandidateStore
from backend.query_intent import ContextProfile
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_capture import (
    build_result_evidence,
    capture_candidate,
    is_aggregate_query,
    is_select_sql,
)
from config.learning_settings import OnlineLearningSettings


def _settings(tmp_path) -> OnlineLearningSettings:
    return OnlineLearningSettings(
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


def _guard(**overrides) -> dict:
    guard = {
        "passed": True,
        "severity": "ok",
        "used_tables": ["t1"],
        "used_columns": ["t1.station_id", "t1.value"],
        "unknown_tables": [],
        "unknown_columns": [],
        "forbidden_operations": [],
        "candidate_mismatch": [],
        "reason": "ok",
    }
    guard.update(overrides)
    return guard


def _state(question: str = "查询最近5条记录", **overrides) -> QueryPerformanceState:
    state = QueryPerformanceState(
        conversation_id="conv-1",
        request_id="req-1",
        question=question,
        source_id="pg-main",
        context_profile=ContextProfile.FULL,
    )
    state.successful_run_sql_count = 1
    state.last_sql = "SELECT station_id, value FROM t1 LIMIT 5"
    state.last_result_metadata = {
        "row_count": 5,
        "columns": ["station_id", "value"],
        "query_type": "SELECT",
        "results": [
            {"station_id": f"s{i}", "value": float(i)}
            for i in range(5)
        ],
        "sql_guard": _guard(),
    }
    for key, value in overrides.items():
        if key == "last_result_metadata":
            state.last_result_metadata = value
        else:
            setattr(state, key, value)
    return state


def _capture(tmp_path, state, **kwargs):
    settings = _settings(tmp_path)
    store = LearningCandidateStore(settings.candidate_db_path)
    params = dict(
        state=state,
        source_id="pg-main",
        database_type="postgresql",
        runtime_revision=3,
        final_answer="查询到 5 条记录。",
        request_failed=False,
    )
    params.update(kwargs)
    return capture_candidate(store=store, settings=settings, **params)


def test_single_successful_select_produces_candidate(tmp_path):
    candidate = _capture(tmp_path, _state())
    assert candidate is not None
    assert candidate.status == "staged"
    assert candidate.candidate_id  # 确定性
    assert candidate.content_fingerprint
    assert candidate.result_sha256


def test_aggregate_no_limit_produces_candidate(tmp_path):
    state = _state(question="统计每个站点平均值")
    state.last_sql = (
        "SELECT station_id, AVG(value) AS avg_value FROM t1 GROUP BY station_id"
    )
    state.last_result_metadata["row_count"] = 3
    state.last_result_metadata["results"] = [
        {"station_id": f"s{i}", "avg_value": float(i)} for i in range(3)
    ]
    candidate = _capture(tmp_path, state)
    assert candidate is not None
    assert is_aggregate_query(state.last_sql) is True


def test_detail_no_limit_rejected(tmp_path):
    state = _state()
    state.last_sql = "SELECT station_id, value FROM t1"
    assert is_select_sql(state.last_sql)
    assert capture_candidate_check(tmp_path, state) is None


def capture_candidate_check(tmp_path, state):
    candidate = _capture(tmp_path, state)
    return candidate


def test_select_star_rejected(tmp_path):
    state = _state()
    state.last_sql = "SELECT * FROM t1 LIMIT 5"
    assert _capture(tmp_path, state) is None


def test_empty_result_rejected(tmp_path):
    state = _state()
    state.last_result_metadata["row_count"] = 0
    state.last_result_metadata["results"] = []
    assert _capture(tmp_path, state) is None


def test_guard_failed_rejected(tmp_path):
    state = _state()
    state.last_result_metadata["sql_guard"] = _guard(
        passed=False, severity="error", reason="存在未知表"
    )
    assert _capture(tmp_path, state) is None


def test_guard_warning_rejected(tmp_path):
    state = _state()
    state.last_result_metadata["sql_guard"] = _guard(severity="warning")
    assert _capture(tmp_path, state) is None


def test_run_sql_failed_rejected(tmp_path):
    state = _state()
    state.successful_run_sql_count = 0
    assert _capture(tmp_path, state) is None


def test_multiple_run_sql_rejected(tmp_path):
    state = _state()
    state.successful_run_sql_count = 2
    assert _capture(tmp_path, state) is None


def test_request_cancelled_rejected(tmp_path):
    state = _state()
    state.request_cancelled = True
    assert _capture(tmp_path, state) is None


def test_request_failed_rejected(tmp_path):
    state = _state()
    assert _capture(tmp_path, state, request_failed=True) is None


def test_no_final_answer_rejected(tmp_path):
    assert _capture(tmp_path, _state(), final_answer="") is None


def test_store_failure_does_not_raise(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = LearningCandidateStore(settings.candidate_db_path)

    def boom(*args, **kwargs):
        raise RuntimeError("候选库损坏")

    monkeypatch.setattr(store, "save_candidate", boom)
    # capture_candidate 返回 None，不抛异常
    result = capture_candidate(
        state=_state(),
        source_id="pg-main",
        database_type="postgresql",
        runtime_revision=3,
        final_answer="查询到 5 条记录。",
        request_failed=False,
        store=store,
        settings=settings,
    )
    assert result is None


def test_evidence_max_rows_and_truncation():
    metadata = {
        "row_count": 100,
        "columns": ["a", "b"],
        "query_type": "SELECT",
        "results": [{"a": i, "b": i * 2} for i in range(100)],
    }
    evidence = build_result_evidence(metadata, max_rows=20, max_bytes=65536)
    assert evidence is not None
    assert evidence.row_count <= 20
    assert evidence.total_row_count == 100
    assert evidence.truncated is True
    assert evidence.max_rows == 20
    assert evidence.result_sha256


def test_evidence_sensitive_columns_redacted():
    metadata = {
        "row_count": 2,
        "columns": ["station_id", "phone"],
        "query_type": "SELECT",
        "results": [
            {"station_id": "A", "phone": "13800000000"},
            {"station_id": "B", "phone": "13900000000"},
        ],
    }
    evidence = build_result_evidence(metadata, max_rows=20, max_bytes=65536)
    assert evidence is not None
    assert all(row[1] == "[REDACTED]" for row in evidence.rows)
    assert "phone" not in evidence.numeric_summary


def test_evidence_byte_cap():
    metadata = {
        "row_count": 500,
        "columns": ["a"],
        "query_type": "SELECT",
        "results": [{"a": "x" * 500} for _ in range(500)],
    }
    evidence = build_result_evidence(metadata, max_rows=20, max_bytes=2048)
    assert evidence is not None
    assert evidence.truncated is True
    assert len(json.dumps(evidence.model_dump(), ensure_ascii=False).encode("utf-8")) <= 4096


def test_evidence_hash_stable():
    metadata = {
        "row_count": 2,
        "columns": ["a"],
        "query_type": "SELECT",
        "results": [{"a": 1}, {"a": 2}],
    }
    e1 = build_result_evidence(metadata, max_rows=20, max_bytes=65536)
    e2 = build_result_evidence(metadata, max_rows=20, max_bytes=65536)
    assert e1.result_sha256 == e2.result_sha256


def test_evidence_small_aggregate_full():
    metadata = {
        "row_count": 3,
        "columns": ["station_id", "cnt"],
        "query_type": "SELECT",
        "results": [
            {"station_id": "s1", "cnt": 2},
            {"station_id": "s2", "cnt": 5},
            {"station_id": "s3", "cnt": 9},
        ],
    }
    evidence = build_result_evidence(metadata, max_rows=20, max_bytes=65536)
    assert evidence is not None
    assert evidence.truncated is False
    assert evidence.row_count == 3
    assert evidence.numeric_summary["cnt"] == {
        "min": 2.0,
        "max": 9.0,
        "sum": 16.0,
        "avg": round(16.0 / 3, 6),
    }


def test_sql_classifiers():
    assert is_select_sql("SELECT a FROM t")
    assert is_select_sql("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not is_select_sql("SELECT 1; SELECT 2")
    assert not is_select_sql("UPDATE t SET a=1")
    assert is_aggregate_query("SELECT COUNT(*) FROM t")
    assert is_aggregate_query("SELECT a FROM t GROUP BY a")
    assert not is_aggregate_query("SELECT a FROM t LIMIT 1")


def test_final_answer_not_duplicated():
    """最终回答只保留最后一个 text 组件，不重复拼接。"""
    from backend.data_source_chat_handler import DataSourceChatHandler
    from vanna.servers.base import ChatStreamChunk

    current = ""
    chunks = [
        ChatStreamChunk(
            rich={"type": "text", "data": {"content": "中间思考"}},
            conversation_id="c",
            request_id="r",
        ),
        ChatStreamChunk(
            rich={"type": "dataframe", "data": {}},
            conversation_id="c",
            request_id="r",
        ),
        ChatStreamChunk(
            rich={"type": "text", "data": {"content": "最终回答"}},
            conversation_id="c",
            request_id="r",
        ),
    ]
    for chunk in chunks:
        current = DataSourceChatHandler._collect_final_answer(chunk, current)
    assert current == "最终回答"

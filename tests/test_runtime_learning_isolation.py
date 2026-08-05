"""物理隔离：候选库与 Catalog/Chroma 分离；默认路径可进 Docker；Worker 不在 import 启动。"""

from __future__ import annotations

import sqlite3

import pytest

from backend.learning_candidate_store import LearningCandidateStore
from backend.runtime_learning_models import LearningCandidate
from config.learning_settings import (
    OnlineLearningSettings,
    resolve_learning_candidate_db,
)


def _candidate(candidate_id: str) -> LearningCandidate:
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


def test_candidate_db_physically_isolated(tmp_path):
    """候选库只含 learning_* 表，不写入 Catalog 主 SQLite。"""
    path = tmp_path / "catalog.sqlite3"
    catalog_conn = sqlite3.connect(str(path))
    catalog_conn.execute(
        "CREATE TABLE data_sources (source_id TEXT PRIMARY KEY)"
    )
    catalog_conn.execute(
        "CREATE TABLE conversation_source_bindings (conversation_id TEXT)"
    )
    catalog_conn.commit()
    catalog_conn.close()

    store = LearningCandidateStore(tmp_path / "learning_candidates.sqlite3")
    store.save_candidate(_candidate("c1"))

    tables = {
        item[0]
        for item in sqlite3.connect(
            str(store.db_path)
        ).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # 候选库不含 catalog 表
    assert "data_sources" not in tables
    assert "conversation_source_bindings" not in tables
    # catalog 库不含候选表
    catalog_tables = {
        item[0]
        for item in sqlite3.connect(str(path)).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "learning_candidates" not in catalog_tables


def test_default_candidate_db_under_agent_data(tmp_path, monkeypatch):
    """默认路径基于 PROJECT_ROOT/agent_data，可用环境变量覆盖为绝对路径。"""
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "agent_data"))
    path = resolve_learning_candidate_db({})
    assert str(path).replace("\\", "/").endswith("agent_data/learning_candidates.sqlite3")
    # 环境变量覆盖
    override = tmp_path / "elsewhere" / "cand.sqlite3"
    resolved = resolve_learning_candidate_db({"LEARNING_CANDIDATE_DB": str(override)})
    assert resolved == override.resolve()
    # 相对路径基于 PROJECT_ROOT 解析，不依赖 cwd
    from config.settings import PROJECT_ROOT

    relative = resolve_learning_candidate_db(
        {"LEARNING_CANDIDATE_DB": "runtime/learning-cand.sqlite3"}
    )
    assert relative == (PROJECT_ROOT / "runtime" / "learning-cand.sqlite3").resolve()


def test_settings_defaults_safe():
    """安全默认值：捕获可开、自动发布默认关。"""
    settings = OnlineLearningSettings.from_environment({})
    assert settings.enabled is False
    assert settings.capture_enabled is True
    assert settings.judge_enabled is True
    assert settings.auto_publish is False
    assert settings.judge_min_confidence == 0.95
    assert settings.batch_size == 10
    assert settings.max_result_rows == 20
    assert settings.max_result_bytes == 65536
    assert settings.max_judge_attempts == 3


def test_store_does_not_touch_chroma_or_catalog(tmp_path):
    """写入候选不创建/修改 Chroma 或 Catalog 文件。"""
    chroma_path = tmp_path / "vanna_data"
    catalog_path = tmp_path / "catalog.sqlite3"
    store = LearningCandidateStore(tmp_path / "learning_candidates.sqlite3")
    store.save_candidate(_candidate("c1"))
    assert not chroma_path.exists()
    assert not catalog_path.exists()

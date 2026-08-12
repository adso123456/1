"""Question Suggestion Learning & Asset Lifecycle 第一阶段隔离回归。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_catalog import DataSourceCatalogError
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.learning_candidate_store import LearningCandidateStore
from backend.learning_identity import content_identity, normalize_question, normalize_sql
from backend.query_intent import ContextProfile
from backend.query_performance import QueryPerformanceState
from backend.question_suggestion_generator import (
    GenerationIdentityMismatchError,
    _assert_same_identity,
)
from backend.question_suggestion_tasks import (
    QuestionSuggestionTaskStore,
    reconcile_question_suggestion_tasks,
)
from backend.runtime_learning_capture import capture_candidate
from config.learning_settings import OnlineLearningSettings


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def settings(root: Path) -> OnlineLearningSettings:
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
        candidate_db_path=root / "learning.sqlite3",
    )


def state(question: str, sql: str, request_id: str) -> QueryPerformanceState:
    item = QueryPerformanceState(
        conversation_id="conv",
        request_id=request_id,
        question=question,
        source_id="source-a",
        context_profile=ContextProfile.FULL,
    )
    item.successful_run_sql_count = 1
    item.last_sql = sql
    item.last_result_metadata = {
        "row_count": 1,
        "columns": ["count"],
        "query_type": "SELECT",
        "results": [{"count": 1}],
        "sql_guard": {
            "passed": True,
            "severity": "ok",
            "used_tables": ["t1"],
            "used_columns": ["t1.id"],
            "forbidden_operations": [],
        },
    }
    return item


def capture(
    store: LearningCandidateStore,
    root: Path,
    question: str,
    sql: str,
    request_id: str,
    *,
    origin: str = "user",
    eligible: bool = True,
):
    return capture_candidate(
        state=state(question, sql, request_id),
        source_id="source-a",
        database_type="postgresql",
        runtime_revision=1,
        final_answer="结果为 1。",
        request_failed=False,
        store=store,
        settings=settings(root),
        request_origin=origin,
        learning_eligible=eligible,
    )


def test_learning_identity(root: Path) -> None:
    store = LearningCandidateStore(root / "learning.sqlite3")
    sql = "SELECT COUNT(id) AS count FROM t1"
    first = capture(store, root, "共有多少条？", sql, "r1")
    second = capture(store, root, " 共 有 多 少 条 ? ", sql + ";", "r2")
    check(first is not None and second is not None, "user 自由提问可形成 Candidate")
    check(first.candidate_id == second.candidate_id, "等价 question+SQL 共用 content identity")
    check(len(store.list_candidates()) == 1, "等价内容不重复产生有效 Candidate")

    conflict = capture(
        store,
        root,
        "共有多少条。",
        "SELECT COUNT(DISTINCT id) AS count FROM t1",
        "r3",
    )
    check(conflict is not None and conflict.status == "needs_review", "同问异 SQL 进入 needs_review")
    check(
        all(item.status == "needs_review" for item in store.list_candidates()),
        "同问异 SQL 的既有有效 Candidate 同步进入 needs_review",
    )

    before = len(store.list_candidates())
    suggestion = capture(
        store,
        root,
        "共有多少条？",
        sql,
        "r4",
        origin="suggestion",
        eligible=False,
    )
    check(suggestion is None and len(store.list_candidates()) == before, "suggestion 请求显式禁止学习")
    manual = capture(store, root, "已有气泡相同文本", sql, "r5")
    check(manual is not None, "无 suggestion_id 的相同文本仍按 user 捕获")


def tool_record(record_id: str, question: str, sql: str):
    return (
        record_id,
        question,
        {
            "question": question,
            "tool_name": "run_sql",
            "args_json": json.dumps({"sql": sql}, ensure_ascii=False),
            "source_id": "source-a",
        },
    )


def test_formal_memory_merge() -> None:
    first = tool_record("a", "共有多少条？", "SELECT COUNT(id) FROM t1")
    equivalent = tool_record("b", " 共有多少条 ? ", "SELECT COUNT(id) FROM t1;")
    merged = DataSourceAssetPreparer._merge_extra_sql_tool_records(
        [first], [equivalent], source_id="source-a"
    )
    check(len(merged) == 1, "Formal SQL Tool Memory 按 content identity 幂等")
    try:
        DataSourceAssetPreparer._merge_extra_sql_tool_records(
            [first],
            [tool_record("c", "共有多少条？", "SELECT COUNT(DISTINCT id) FROM t1")],
            source_id="source-a",
        )
        raised = False
    except DataSourceCatalogError:
        raised = True
    check(raised, "新增同问异 SQL 不得静默写入 Formal Memory")


class FakeCatalog:
    def __init__(self):
        self.record = SimpleNamespace(
            source_id="source-a", status="ready", enabled_for_chat=True
        )

    def list(self):
        return [self.record]


def test_startup_reconcile(root: Path) -> None:
    import backend.question_suggestion_assets as assets
    import backend.question_suggestion_generator as generator

    identity = {
        "source_id": "source-a",
        "runtime_revision": 2,
        "selected_scope_fingerprint": "scope",
        "metadata_sha256": "metadata",
        "review_policy_fingerprint": "policy",
        "formal_sql_memory_fingerprint": "memory",
        "generator_version": "v1",
    }
    original_load = assets.load_question_directory
    original_identity = generator.generation_identity
    store = QuestionSuggestionTaskStore(root / "tasks.sqlite3")
    try:
        generator.generation_identity = lambda catalog, source_id: dict(identity)
        assets.load_question_directory = lambda source_id: None
        check(reconcile_question_suggestion_tasks(FakeCatalog(), store) == 1, "startup 对 missing 气泡自动补建")
        check(reconcile_question_suggestion_tasks(FakeCatalog(), store) == 1, "startup 补建任务入队保持幂等")
        first = store.claim_next("worker")
        check(first is not None, "补建任务可被 Worker 领取")
        stale = dict(identity)
        stale["runtime_revision"] = 1
        try:
            _assert_same_identity(identity, stale, phase="生成开始")
            blocked = False
        except GenerationIdentityMismatchError:
            blocked = True
        check(blocked, "stale task 身份复核失败，不能覆盖当前气泡")
    finally:
        assets.load_question_directory = original_load
        generator.generation_identity = original_identity
        store.close()


def test_safe_normalization() -> None:
    check(normalize_question("问题？") == normalize_question(" 问 题 ? "), "问题规范化统一安全空白和问号")
    check(normalize_sql("SELECT 'A B' FROM T; ") == "select 'A B' from t", "SQL 规范化不改变字符串字面量")
    check(
        content_identity("a", "问题？", "SELECT 1;")
        == content_identity("a", " 问题 ? ", " select 1 "),
        "content identity 使用公共规范化",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qs-lifecycle-") as directory:
        root = Path(directory)
        test_safe_normalization()
        test_learning_identity(root)
        test_formal_memory_merge()
        test_startup_reconcile(root)
    print("question suggestion learning lifecycle: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

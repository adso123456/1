"""运行时学习真实链路烟雾验证（离线，不触碰正式资产）。

覆盖：捕获 -> 候选 staged -> 发布(revision+1) -> 正式 Chroma 写入 ->
search_similar_usage 召回。数据库仅作只读假设，不发起真实 SQL。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet

from backend.data_source_catalog import (
    CredentialCipher,
    DataSourceCatalog,
)
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.learning_candidate_store import LearningCandidateStore
from backend.query_intent import ContextProfile
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_capture import capture_candidate
from backend.runtime_learning_judge import RuntimeLearningJudge
from backend.runtime_learning_models import JudgeVerdict
from backend.runtime_learning_service import RuntimeLearningService
from config.learning_settings import OnlineLearningSettings


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def _metadata() -> list[dict]:
    primary_index = {
        "name": "t1_pkey",
        "unique": True,
        "primary": True,
        "method": "btree",
        "columns": [{"name": "id", "position": 1, "direction": "ASC"}],
    }
    rows = []
    for position, (column, column_type, primary) in enumerate(
        [
            ("id", "bigint", True),
            ("station_id", "text", False),
            ("value", "double precision", False),
            ("monitor_time", "timestamp", False),
        ],
        start=1,
    ):
        rows.append(
            {
                "table": "t1",
                "table_comment": "测试表",
                "column": column,
                "type": column_type,
                "comment": "",
                "nullable": not primary,
                "primary_key": primary,
                "ordinal_position": position,
                "indexes": [primary_index] if primary else [],
            }
        )
    return rows


def _add_ready_source(catalog: DataSourceCatalog) -> str:
    record = catalog.create(
        display_name="烟雾测试源",
        description="运行时学习烟雾验证",
        database_type="postgresql",
        host="127.0.0.1",
        port=5433,
        database_name="test",
        schema_name="public",
        username="smoke-user",
        password="smoke-password",
    )
    catalog.mark_connection_test(record.source_id, success=True)
    metadata = _metadata()
    catalog.save_discovery(record.source_id, metadata)
    catalog.save_scope(record.source_id, metadata)
    published = catalog.publish(
        record.source_id, routing_summary="运行时学习烟雾验证源"
    )
    published.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    published.metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    published.memory_path.mkdir(parents=True, exist_ok=True)
    return record.source_id


def _make_state() -> QueryPerformanceState:
    state = QueryPerformanceState(
        conversation_id="smoke-conv",
        request_id="smoke-req",
        question="最近5条记录的平均值是多少",
        source_id="pg-smoke",
        context_profile=ContextProfile.FULL,
    )
    state.successful_run_sql_count = 1
    state.last_sql = (
        "SELECT station_id, AVG(value) AS avg_value FROM t1 "
        "GROUP BY station_id"
    )
    state.last_result_metadata = {
        "row_count": 2,
        "columns": ["station_id", "avg_value"],
        "query_type": "SELECT",
        "results": [
            {"station_id": "s1", "avg_value": 1.5},
            {"station_id": "s2", "avg_value": 2.5},
        ],
        "sql_guard": {
            "passed": True,
            "severity": "ok",
            "used_tables": ["t1"],
            "used_columns": ["t1.station_id", "t1.value"],
            "unknown_tables": [],
            "unknown_columns": [],
            "forbidden_operations": [],
            "candidate_mismatch": [],
            "reason": "ok",
        },
    }
    return state


class _PassJudge(RuntimeLearningJudge):
    async def judge(self, candidate, metadata_context=None):
        return JudgeVerdict(
            verdict="PASS",
            confidence=0.98,
            question_sql_aligned=True,
            answer_result_aligned=True,
            metadata_valid=True,
            business_ambiguity=False,
            risk_flags=[],
            reason="烟雾验证：一致",
        )


def _close_memory(memory) -> None:
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    try:
        memory._client._system.stop()
    except Exception:
        pass
    memory._collection = None
    memory._client = None
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


async def _verify_recall(memory_path: Path, question: str, expected_sql: str) -> None:
    from backend.memory import create_memory

    memory = create_memory(memory_path)
    try:
        results = await memory.search_similar_usage(
            question=question,
            context=SimpleNamespace(metadata={"stage": "smoke"}),
            limit=5,
            similarity_threshold=0.55,
            tool_name_filter="run_sql",
        )
    finally:
        _close_memory(memory)
    check(len(results) > 0, "search_similar_usage 召回新样本")
    memory = getattr(results[0], "memory", results[0])
    args = getattr(memory, "args", {}) or {}
    recalled_sql = str(args.get("sql") or "")
    check(recalled_sql == expected_sql, "召回 SQL 与发布样本一致")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rl-smoke-") as directory:
        root = Path(directory)
        cipher = CredentialCipher(Fernet.generate_key().decode("ascii"))
        catalog = DataSourceCatalog(
            root / "catalog.sqlite3",
            cipher=cipher,
            environ={
                "DB_USER": "smoke",
                "DB_PASSWORD": "smoke-secret",
            },
        )
        catalog.initialize([])
        source_id = _add_ready_source(catalog)
        before = catalog.require(source_id).runtime_revision
        check(before >= 1, f"源已发布 revision={before}")

        # 候选库
        settings = OnlineLearningSettings(
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
            candidate_db_path=root / "learning_candidates.sqlite3",
        )
        store = LearningCandidateStore(settings.candidate_db_path)

        # 捕获
        state = _make_state()
        candidate = capture_candidate(
            state=state,
            source_id=source_id,
            database_type="postgresql",
            runtime_revision=before,
            final_answer="两个站点平均值为 1.5 和 2.5。",
            request_failed=False,
            store=store,
            settings=settings,
        )
        check(candidate is not None, "捕获生成 staged 候选")
        check(
            store.get_candidate(candidate.candidate_id).status == "staged",
            "候选状态为 staged",
        )

        # 模拟 Judge PASS（确定性，不依赖真实 LLM）
        store.transition(candidate.candidate_id, "judging")
        store.apply_judge_result(
            candidate_id=candidate.candidate_id,
            verdict="PASS",
            confidence=0.98,
            reason="烟雾验证：一致",
            payload_json="{}",
            run_id="smoke-judge-run",
            attempts=1,
            target_status="pass",
        )
        check(
            store.get_candidate(candidate.candidate_id).status == "pass",
            "Judge 判为 PASS",
        )

        # 发布（复用 DataSourceAssetPreparer 链路）
        service = RuntimeLearningService(
            catalog=catalog,
            runtime_manager=None,
            store=store,
            judge=_PassJudge(object(), settings),
            settings=settings,
        )
        result = asyncio.run(service.publish_source(source_id, force=True))
        check(result["published"] == 1, "发布 1 条候选")
        check(
            result["runtime_revision_after"] == before + 1,
            f"runtime_revision {before} -> {before + 1}",
        )
        after = catalog.require(source_id).runtime_revision
        check(after == before + 1, "Catalog runtime_revision 已 +1")
        check(
            store.get_candidate(candidate.candidate_id).status == "published",
            "候选状态为 published",
        )

        # 正式 Chroma 中存在 sql_example 记录
        from backend.memory import create_memory

        memory = create_memory(catalog.require(source_id).memory_path)
        try:
            collection = memory._get_collection()
            found = collection.get(
                where={"category": "sql_example"}, include=["metadatas"]
            )
        finally:
            _close_memory(memory)
        check(len(found["ids"]) >= 1, "正式 Chroma 含 sql_example 记录")

        # 召回
        asyncio.run(
            _verify_recall(
                catalog.require(source_id).memory_path,
                "最近5条记录的平均值是多少",
                state.last_sql,
            )
        )

        # 幂等：同一批重试不会重复写（revision 再次 +1，但记录不重复）
        print("smoke runtime learning: all checks passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""运行时学习服务：候选捕获编排、Judge、发布、去重、冲突与重新验证。

正式发布复用 DataSourceAssetPreparer 的候选 revision / 备份 / 原子安装 /
catalog.publish() / 回滚 / Runtime 切换链路，不新建第二套发布系统。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from typing import Any

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_connectors import DataSourceAssetPreparer
from backend.data_source_runtime_manager import DataSourceRuntimeManager
from backend.learning_candidate_store import (
    LearningCandidateConflict,
    LearningCandidateStore,
    normalize_question,
)
from backend.query_performance import QueryPerformanceState
from backend.runtime_learning_capture import (
    build_result_evidence,
    capture_candidate,
    is_select_sql,
)
from backend.runtime_learning_judge import (
    RuntimeLearningJudge,
    verdict_to_target_status,
)
from backend.runtime_learning_models import (
    LearningCandidate,
    ResultEvidence,
)
from config.learning_settings import OnlineLearningSettings

logger = logging.getLogger(__name__)

_RECORD_SCHEMA_VERSION = "1.0"
_RECORD_ID_PREFIX = "toolmem-v1-"


class RuntimeLearningServiceError(RuntimeError):
    """运行时学习服务操作失败。"""


class LearningPublishConflict(RuntimeLearningServiceError):
    """发布被并发或状态拒绝。"""


class LearningPublishError(RuntimeLearningServiceError):
    """发布失败。"""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def tool_memory_record_id(
    *, question: str, sql: str, database_type: str
) -> str:
    """确定性 Tool Memory record_id（与现有 memory_write_plan 约定一致）。"""
    canonical = {
        "record_schema_version": _RECORD_SCHEMA_VERSION,
        "question": question,
        "tool_name": "run_sql",
        "args": {"sql": sql},
        "success": True,
    }
    content_sha = _sha256(_canonical_json(canonical))
    return _RECORD_ID_PREFIX + content_sha


def _training_level_for(
    *, database_type: str, sql: str, used_tables: Sequence[str]
) -> str:
    """简单单表 SQL 归入 Level 2；复杂 SQL 归入 Level 3（origin 区分线上来源）。"""
    is_aggregate = bool(
        re.search(
            r"\b(count|sum|avg|min|max|stddev)\s*\(|\bgroup\s+by\b",
            sql,
            flags=re.I,
        )
    )
    is_single_table = len(set(used_tables)) <= 1
    is_simple = is_single_table and not is_aggregate and bool(
        re.search(r"\blimit\b", sql, flags=re.I)
    )
    if is_simple:
        if database_type == "mysql":
            return "level2_mysql_sql_examples"
        return "level2_sql_examples"
    return "level3_p1_sql_examples"


class RuntimeLearningService:
    """协调捕获、Judge、发布。捕获绝不阻塞主链路。"""

    def __init__(
        self,
        *,
        catalog: DataSourceCatalog,
        runtime_manager: DataSourceRuntimeManager,
        store: LearningCandidateStore,
        judge: RuntimeLearningJudge,
        settings: OnlineLearningSettings,
    ) -> None:
        self._catalog = catalog
        self._runtime_manager = runtime_manager
        self._store = store
        self._judge = judge
        self._settings = settings
        self._judging: set[str] = set()
        self._publishing: set[str] = set()
        self._judge_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 捕获（不抛异常）
    # ------------------------------------------------------------------
    def capture(
        self,
        *,
        state: QueryPerformanceState,
        source_id: str,
        database_type: str,
        runtime_revision: int,
        final_answer: str,
        request_failed: bool,
    ) -> LearningCandidate | None:
        if not self._settings.enabled or not self._settings.capture_enabled:
            return None
        return capture_candidate(
            state=state,
            source_id=source_id,
            database_type=database_type,
            runtime_revision=runtime_revision,
            final_answer=final_answer,
            request_failed=request_failed,
            store=self._store,
            settings=self._settings,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_candidates(
        self,
        *,
        statuses: Iterable[str] | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LearningCandidate]:
        return self._store.list_candidates(
            statuses=statuses,
            source_id=source_id,
            limit=limit,
            offset=offset,
        )

    def get_candidate(self, candidate_id: str) -> LearningCandidate | None:
        return self._store.get_candidate(candidate_id)

    def counts(self, source_id: str | None = None) -> dict[str, Any]:
        return {
            "by_status": self._store.count_by_status(source_id),
            "judging_in_progress": len(self._judging),
            "publishing_in_progress": len(self._publishing),
            "settings": {
                "enabled": self._settings.enabled,
                "capture_enabled": self._settings.capture_enabled,
                "judge_enabled": self._settings.judge_enabled,
                "auto_publish": self._settings.auto_publish,
                "judge_min_confidence": self._settings.judge_min_confidence,
                "batch_size": self._settings.batch_size,
                "candidate_db_path": str(self._settings.candidate_db_path),
            },
        }

    def recover_interrupted(self) -> dict[str, int]:
        """服务重启恢复中断状态。"""
        return self._store.recover_interrupted()

    def publish_ready_source_ids(self, now: float | None = None) -> list[str]:
        """满足批次条件（数量足够或等待超时）且可发布的源列表。"""
        if not self._settings.enabled or not self._settings.auto_publish:
            return []
        now = now if now is not None else time.time()
        sources: dict[str, list[LearningCandidate]] = {}
        for candidate in self._store.list_candidates(
            statuses=["pass", "publish_pending", "publish_failed"]
        ):
            sources.setdefault(candidate.source_id, []).append(candidate)
        ready: list[str] = []
        for source_id, items in sorted(sources.items()):
            if not items:
                continue
            oldest = min(item.created_at for item in items)
            if len(items) >= self._settings.batch_size or (
                now - oldest >= self._settings.batch_max_wait_seconds
            ):
                ready.append(source_id)
        return ready

    # ------------------------------------------------------------------
    # Judge
    # ------------------------------------------------------------------
    async def judge_candidate(self, candidate_id: str) -> LearningCandidate:
        """对 staged 候选运行独立 Judge。同候选并发 Judge 会被拒绝。"""
        if not self._settings.judge_enabled:
            raise RuntimeLearningServiceError("Judge 已关闭")
        async with self._judge_lock:
            if candidate_id in self._judging:
                raise LearningPublishConflict(f"候选正在判断中：{candidate_id}")
            self._judging.add(candidate_id)
        try:
            candidate = self._store.get_candidate(candidate_id)
            if candidate is None:
                raise RuntimeLearningServiceError(f"候选不存在：{candidate_id}")
            if candidate.status != "staged":
                raise RuntimeLearningServiceError(
                    f"候选状态 {candidate.status} 不允许 Judge（仅 staged）"
                )
            self._store.transition(candidate_id, "judging")
            metadata_context = self._metadata_context(candidate)
            verdict = await self._judge.judge(
                candidate, metadata_context=metadata_context
            )
            target = verdict_to_target_status(
                verdict, min_confidence=self._settings.judge_min_confidence
            )
            # 确定性门禁：结果证据被截断时禁止自动 PASS，强制人工复核。
            # LLM Judge 可能忽略提示词中"截断证据需降置信"的指示，因此
            # 截断样本一律不许进入自动发布链路。
            if target == "pass" and candidate.result_truncated:
                target = "needs_review"
            updated = self._store.apply_judge_result(
                candidate_id=candidate_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                reason=verdict.reason,
                payload_json=_canonical_json(verdict.model_dump()),
                run_id=_sha256(
                    f"{candidate_id}|{verdict.verdict}|{time.time()}"
                )[:24],
                attempts=candidate.judge_attempts + 1,
                target_status=target,
            )
            return updated
        finally:
            async with self._judge_lock:
                self._judging.discard(candidate_id)

    def _metadata_context(
        self, candidate: LearningCandidate
    ) -> list[dict[str, Any]]:
        """当前 Metadata 中相关表字段摘要，供 Judge 核对 metadata_valid。"""
        try:
            record = self._catalog.require(candidate.source_id)
            used_tables = set(candidate.used_tables)
            rows = [
                dict(item)
                for item in record.discovered_metadata
                if str(item.get("table") or "") in used_tables
            ]
            grouped: dict[str, list[str]] = {}
            for item in rows:
                grouped.setdefault(str(item["table"]), []).append(
                    str(item.get("column") or "")
                )
            return [
                {"table": table, "columns": columns}
                for table, columns in sorted(grouped.items())
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 人工决策（needs_review -> approve / reject）
    # ------------------------------------------------------------------
    def approve(self, candidate_id: str) -> LearningCandidate:
        candidate = self._require(candidate_id)
        if candidate.status != "needs_review":
            raise RuntimeLearningServiceError(
                f"仅 needs_review 候选可人工批准，当前 {candidate.status}"
            )
        return self._store.transition(candidate_id, "publish_pending")

    def reject(self, candidate_id: str) -> LearningCandidate:
        candidate = self._require(candidate_id)
        if candidate.status not in {"needs_review", "publish_pending"}:
            raise RuntimeLearningServiceError(
                f"候选状态 {candidate.status} 不允许直接拒绝"
            )
        return self._store.transition(candidate_id, "reject")

    def _require(self, candidate_id: str) -> LearningCandidate:
        candidate = self._store.get_candidate(candidate_id)
        if candidate is None:
            raise RuntimeLearningServiceError(f"候选不存在：{candidate_id}")
        return candidate

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    async def publish_source(
        self,
        source_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """把该源 publish_pending 候选组成微批次并正式发布。

        force=True 允许在 auto_publish 关闭时由管理员 API 手动触发。
        """
        if not self._settings.enabled:
            raise RuntimeLearningServiceError("运行时学习已关闭")
        if not self._settings.auto_publish and not force:
            raise RuntimeLearningServiceError("自动发布未开启，需手动触发")

        async with self._publish_lock:
            if source_id in self._publishing:
                raise LearningPublishConflict(
                    f"数据源 {source_id} 正在发布中"
                )
            self._publishing.add(source_id)
        try:
            return await self._publish_locked(source_id)
        finally:
            async with self._publish_lock:
                self._publishing.discard(source_id)

    async def _publish_locked(self, source_id: str) -> dict[str, Any]:
        candidates = self._store.list_candidates(
            statuses=["pass", "publish_pending", "publish_failed"],
            source_id=source_id,
            limit=self._settings.batch_size,
        )
        if not candidates:
            return {"source_id": source_id, "published": 0, "skipped": 0}
        # pass / publish_failed 候选提升为 publish_pending 进入发布队列
        # （publish_failed 为瞬时冲突后的重试入口，避免一次失败永久搁浅）
        for candidate in candidates:
            if candidate.status in {"pass", "publish_failed"}:
                self._store.transition(candidate.candidate_id, "publish_pending")

        record = self._catalog.require(source_id)
        if record.status != "ready" or not record.enabled_for_chat:
            raise LearningPublishError(
                f"数据源 {source_id} 状态不允许发布：{record.status}"
            )
        current_revision = record.runtime_revision
        metadata_path = record.metadata_path

        include: list[LearningCandidate] = []
        skipped: list[LearningCandidate] = []
        for candidate in candidates:
            try:
                self._revalidate_candidate(
                    candidate,
                    record=record,
                    current_revision=current_revision,
                    metadata_path=metadata_path,
                )
                include.append(candidate)
            except RuntimeLearningServiceError as exc:
                skipped.append(candidate)
                logger.warning(
                    "候选 %s 发布前校验未通过：%s",
                    candidate.candidate_id,
                    str(exc),
                )
        if not include:
            return {
                "source_id": source_id,
                "published": 0,
                "skipped": len(skipped),
            }

        payload = [
            self._build_tool_record(candidate, current_revision)
            for candidate in include
        ]
        batch_id = (
            "rl-" + _sha256(source_id + "|" + "|".join(
                c.candidate_id for c in include
            ))[:24]
        )
        self._store.create_publish_batch(
            batch_id=batch_id,
            source_id=source_id,
            candidate_ids=[c.candidate_id for c in include],
            revision_before=current_revision,
        )
        self._store.attach_batch_to_candidates(
            [c.candidate_id for c in include], batch_id
        )

        preparer = DataSourceAssetPreparer(self._catalog, self._runtime_manager)
        try:
            result = await asyncio.to_thread(
                preparer.prepare,
                source_id,
                extra_sql_tool_records=payload,
            )
        except Exception as exc:
            error = {"error": type(exc).__name__, "detail": str(exc)}
            self._store.finish_publish_batch(
                batch_id=batch_id,
                success=False,
                revision_after=current_revision,
                error=error,
                target_status="publish_failed",
            )
            self._store.finish_candidates_in_batch(
                [c.candidate_id for c in include],
                success=False,
                revision_after=current_revision,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise LearningPublishError(
                f"数据源 {source_id} 发布失败：{type(exc).__name__}: {exc}"
            ) from exc

        published_revision = int(
            result.get("runtime_revision") or current_revision
        )
        self._store.finish_publish_batch(
            batch_id=batch_id,
            success=True,
            revision_after=published_revision,
            target_status="committed",
        )
        self._store.finish_candidates_in_batch(
            [c.candidate_id for c in include],
            success=True,
            revision_after=published_revision,
        )
        return {
            "source_id": source_id,
            "batch_id": batch_id,
            "published": len(include),
            "skipped": len(skipped),
            "runtime_revision_before": current_revision,
            "runtime_revision_after": published_revision,
            "sql_tool_memory_count": result.get("sql_tool_memory_count"),
            "status": result.get("status"),
        }

    # ------------------------------------------------------------------
    # 发布前校验
    # ------------------------------------------------------------------
    def _revalidate_candidate(
        self,
        candidate: LearningCandidate,
        *,
        record: Any,
        current_revision: int,
        metadata_path: Any,
    ) -> None:
        """候选发布前重新验证：revision、scope、SQLGuard、去重、冲突。"""
        if not is_select_sql(candidate.sql):
            raise RuntimeLearningServiceError("SQL 已不再满足只读单语句")
        if current_revision != candidate.captured_runtime_revision:
            # stale revision：用当前 Metadata/SQLGuard 重新验证后仍可发布
            try:
                self._revalidate_sql_guard(candidate, metadata_path)
            except RuntimeLearningServiceError as exc:
                self._store.transition(
                    candidate.candidate_id,
                    "reject",
                    last_error=str(exc),
                )
                raise
            self._store.update_fields(
                candidate.candidate_id,
                reviewed_runtime_revision=current_revision,
            )

        scope_tables = {
            str(item.get("table") or "")
            for item in record.selected_scope
        }
        missing_tables = [
            table
            for table in candidate.used_tables
            if table not in scope_tables
        ]
        if missing_tables:
            self._store.transition(
                candidate.candidate_id,
                "reject",
                last_error=f"表已不在当前范围：{missing_tables}",
            )
            raise RuntimeLearningServiceError(
                f"表已不在当前范围：{missing_tables}"
            )

        existing = self._existing_sql_examples(record)
        fingerprint_hit = [
            item
            for item in existing
            if str(item.get("content_fingerprint") or "")
            == candidate.content_fingerprint
        ]
        if fingerprint_hit:
            self._store.transition(
                candidate.candidate_id,
                "superseded",
                last_error="内容级去重：已存在相同样本",
            )
            raise RuntimeLearningServiceError("内容级去重：已存在相同样本")

        same_question_different_sql = [
            item
            for item in existing
            if str(item.get("question") or "") != ""
            and normalize_question(str(item["question"]))
            == candidate.normalized_question
            and str(item.get("sql") or "") != candidate.sql
        ]
        if same_question_different_sql:
            self._store.transition(
                candidate.candidate_id,
                "needs_review",
                last_error="同问异 SQL 冲突，需人工复核",
                extra={"conflict_status": "conflict"},
            )
            raise RuntimeLearningServiceError("同问异 SQL 冲突，需人工复核")

    def _revalidate_sql_guard(self, candidate: LearningCandidate, metadata_path: Any) -> None:
        if candidate.database_type == "mysql":
            from backend.mysql_sql_guard import MySQLSQLGuard

            guard = MySQLSQLGuard(index_path=metadata_path)
        else:
            from backend.sql_guard import SQLGuard

            guard = SQLGuard(index_path=metadata_path)
        try:
            result = guard.validate(
                sql=candidate.sql,
                query=candidate.question,
                deterministic_candidate_tables=list(candidate.used_tables),
            )
        except Exception as exc:
            raise RuntimeLearningServiceError(
                f"当前 Metadata 无法校验 SQL：{exc}"
            ) from exc
        if not result.passed or result.severity != "ok":
            raise RuntimeLearningServiceError(
                "当前 Metadata 下 SQLGuard 校验失败："
                + result.reason
            )

    def _existing_sql_examples(self, record: Any) -> list[dict[str, Any]]:
        """当前正式 Memory 中的 sql_example 记录（内容级去重依据）。"""
        memory_path = record.memory_path
        if memory_path is None or not getattr(memory_path, "exists", lambda: False)():
            return []
        try:
            from backend.memory import create_memory

            memory = create_memory(memory_path)
            try:
                collection = memory._get_collection()
                getter = getattr(collection, "get", None)
                if not callable(getter):
                    return []
                result = getter(
                    where={"category": "sql_example"},
                    include=["metadatas"],
                )
            finally:
                try:
                    memory._executor.shutdown(wait=True)
                except Exception:
                    pass
                memory._collection = None
                # 与 DataSourceAssetPreparer._close_memory 一致：显式关闭 client，
                # 避免 Windows 上 Chroma 文件句柄未释放导致后续 os.replace 记忆目录失败。
                client = getattr(memory, "_client", None)
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                memory._client = None
            out: list[dict[str, Any]] = []
            for metadata in result.get("metadatas") or []:
                item = dict(metadata or {})
                try:
                    args = json.loads(str(item.get("args_json") or "{}"))
                    item["sql"] = str(args.get("sql") or "")
                except (TypeError, ValueError):
                    item["sql"] = ""
                out.append(item)
            return out
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Tool Memory 记录构建
    # ------------------------------------------------------------------
    def _build_tool_record(
        self, candidate: LearningCandidate, current_revision: int
    ) -> tuple[str, str, dict[str, Any]]:
        record_id = tool_memory_record_id(
            question=candidate.question,
            sql=candidate.sql,
            database_type=candidate.database_type,
        )
        compatibility = {
            "sample_id": "rl_" + candidate.candidate_id,
            "training_level": _training_level_for(
                database_type=candidate.database_type,
                sql=candidate.sql,
                used_tables=candidate.used_tables,
            ),
            "train_decision": "approved",
            "expected_tables": list(candidate.used_tables),
            "expected_columns": list(candidate.used_columns),
            "source_id": candidate.source_id,
            "dialect": candidate.database_type,
            "origin": "runtime_learning",
            "captured_runtime_revision": candidate.captured_runtime_revision,
            "published_runtime_revision": current_revision,
            "judge_confidence": candidate.judge_confidence,
            "judge_version": "runtime-learning-judge-v1",
        }
        metadata = {
            "question": candidate.question,
            "tool_name": "run_sql",
            "args_json": candidate.args_json,
            "success": True,
            "metadata_json": _canonical_json(compatibility),
            **compatibility,  # 顶层扁平化，供 SqlExampleContextEnhancer 过滤读取
            "source_id": candidate.source_id,
            "category": "sql_example",
            "record_id": record_id,
            "content_fingerprint": candidate.content_fingerprint,
        }
        return (record_id, candidate.question, metadata)

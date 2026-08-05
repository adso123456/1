"""运行时学习候选库：独立 SQLite，WAL + 状态机 + 幂等写入。

物理隔离：候选库与 Catalog 主 SQLite、正式 Chroma、报表库完全分离。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from backend.runtime_learning_models import (
    LearningCandidate,
    can_transition,
)

_SCHEMA_VERSION = 1

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS learning_schema_versions (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_candidates (
    candidate_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    database_type TEXT NOT NULL,
    captured_runtime_revision INTEGER NOT NULL,
    reviewed_runtime_revision INTEGER NOT NULL DEFAULT 0,
    published_runtime_revision INTEGER NOT NULL DEFAULT 0,
    question TEXT NOT NULL,
    normalized_question TEXT NOT NULL,
    question_sha256 TEXT NOT NULL,
    sql TEXT NOT NULL,
    normalized_sql TEXT NOT NULL,
    sql_sha256 TEXT NOT NULL,
    args_json TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    guard_result_json TEXT NOT NULL,
    used_tables_json TEXT NOT NULL,
    used_columns_json TEXT NOT NULL,
    result_summary_json TEXT NOT NULL,
    result_evidence_json TEXT NOT NULL,
    result_truncated INTEGER NOT NULL DEFAULT 0,
    result_sha256 TEXT NOT NULL DEFAULT '',
    final_answer TEXT NOT NULL,
    answer_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    judge_verdict TEXT NOT NULL DEFAULT '',
    judge_confidence REAL NOT NULL DEFAULT 0,
    judge_reason TEXT NOT NULL DEFAULT '',
    conflict_status TEXT NOT NULL DEFAULT '',
    judge_attempts INTEGER NOT NULL DEFAULT 0,
    publish_batch_id TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    judged_at REAL,
    published_at REAL
);

CREATE INDEX IF NOT EXISTS idx_learning_candidates_source_status
    ON learning_candidates(source_id, status);
CREATE INDEX IF NOT EXISTS idx_learning_candidates_fingerprint
    ON learning_candidates(content_fingerprint);

CREATE TABLE IF NOT EXISTS learning_judge_runs (
    run_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_publish_batches (
    batch_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime_revision_before INTEGER NOT NULL,
    runtime_revision_after INTEGER NOT NULL DEFAULT 0,
    error_json TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class LearningCandidateStoreError(RuntimeError):
    """候选库操作失败。"""


class LearningStateMachineError(LearningCandidateStoreError):
    """非法状态迁移被拒绝。"""


class LearningCandidateConflict(LearningCandidateStoreError):
    """并发状态竞争导致更新失败。"""


def normalize_question(question: str) -> str:
    """规范化空白和标点，用于确定性去重。"""
    compact = re.sub(r"[\s　]+", "", str(question or ""))
    return re.sub(r"[。！？?!.；;：:,，]+$", "", compact)


def normalize_sql(sql: str) -> str:
    """规范化 SQL 空白与注释（沿用 sql_guard 风格，不引入新解析依赖）。"""
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    stripped = re.sub(r"--[^\n\r]*", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


class LearningCandidateStore:
    """候选库。所有写入参数化，禁止 SQL 拼接注入。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).resolve()
        self._init_lock = threading.Lock()
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=30,
            isolation_level=None,  # 手动管理事务
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _safe_rollback(connection: sqlite3.Connection) -> None:
        """回滚失败（无活动事务）时静默忽略，避免掩盖原始错误。"""
        try:
            connection.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass

    def _init_schema(self) -> None:
        with self._init_lock:
            connection = self._connect()
            try:
                # executescript 内部会提交事务，不能与 BEGIN 混用。
                connection.executescript(_CREATE_TABLES)
                row = connection.execute(
                    "SELECT version FROM learning_schema_versions "
                    "ORDER BY version DESC LIMIT 1"
                ).fetchone()
                current = row["version"] if row else 0
                if current < _SCHEMA_VERSION:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        if current:
                            connection.execute(
                                "DELETE FROM learning_schema_versions"
                            )
                        connection.execute(
                            "INSERT INTO learning_schema_versions(version)"
                            " VALUES (?)",
                            (_SCHEMA_VERSION,),
                        )
                        connection.execute("COMMIT")
                    except Exception:
                        self._safe_rollback(connection)
                        raise
            finally:
                connection.close()

    # ------------------------------------------------------------------
    # 候选写入
    # ------------------------------------------------------------------
    def save_candidate(self, candidate: LearningCandidate) -> bool:
        """幂等插入：candidate_id 已存在则忽略，返回是否新建。"""
        row = candidate.to_row()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO learning_candidates ("
                " candidate_id, source_id, conversation_id, request_id,"
                " database_type, captured_runtime_revision,"
                " reviewed_runtime_revision, published_runtime_revision,"
                " question, normalized_question, question_sha256, sql,"
                " normalized_sql, sql_sha256, args_json, content_fingerprint,"
                " guard_result_json, used_tables_json, used_columns_json,"
                " result_summary_json, result_evidence_json, result_truncated,"
                " result_sha256, final_answer, answer_sha256, status,"
                " judge_verdict, judge_confidence, judge_reason,"
                " conflict_status, judge_attempts, publish_batch_id,"
                " last_error, created_at, updated_at, judged_at, published_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["candidate_id"],
                    row["source_id"],
                    row["conversation_id"],
                    row["request_id"],
                    row["database_type"],
                    row["captured_runtime_revision"],
                    row["reviewed_runtime_revision"],
                    row["published_runtime_revision"],
                    row["question"],
                    row["normalized_question"],
                    row["question_sha256"],
                    row["sql"],
                    row["normalized_sql"],
                    row["sql_sha256"],
                    row["args_json"],
                    row["content_fingerprint"],
                    row["guard_result_json"],
                    row["used_tables_json"],
                    row["used_columns_json"],
                    row["result_summary_json"],
                    row["result_evidence_json"],
                    row["result_truncated"],
                    row["result_sha256"],
                    row["final_answer"],
                    row["answer_sha256"],
                    row["status"],
                    row["judge_verdict"],
                    row["judge_confidence"],
                    row["judge_reason"],
                    row["conflict_status"],
                    row["judge_attempts"],
                    row["publish_batch_id"],
                    row["last_error"],
                    row["created_at"],
                    row["updated_at"],
                    row["judged_at"],
                    row["published_at"],
                ),
            )
            connection.execute("COMMIT")
            return cursor.rowcount > 0
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_candidate(self, candidate_id: str) -> LearningCandidate | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            return self._row_to_candidate(row) if row else None
        finally:
            connection.close()

    def list_candidates(
        self,
        *,
        statuses: Iterable[str] | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_desc: bool = False,
    ) -> list[LearningCandidate]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses is not None:
            status_list = list(statuses)
            if status_list:
                clauses.append(
                    "status IN ("
                    + ",".join("?" for _ in status_list)
                    + ")"
                )
                params.extend(status_list)
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = " ORDER BY created_at DESC" if order_desc else " ORDER BY created_at ASC"
        params.extend([max(0, int(limit)), max(0, int(offset))])
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM learning_candidates" + where + order
                + " LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [self._row_to_candidate(row) for row in rows]
        finally:
            connection.close()

    def count_by_status(self, source_id: str | None = None) -> dict[str, int]:
        connection = self._connect()
        try:
            if source_id is not None:
                rows = connection.execute(
                    "SELECT status, COUNT(*) AS count FROM learning_candidates "
                    "WHERE source_id = ? GROUP BY status",
                    (source_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT status, COUNT(*) AS count FROM learning_candidates "
                    "GROUP BY status"
                ).fetchall()
            return {row["status"]: int(row["count"]) for row in rows}
        finally:
            connection.close()

    def update_fields(
        self,
        candidate_id: str,
        **fields: Any,
    ) -> LearningCandidate:
        """不改变状态地更新候选字段（如 reviewed_runtime_revision）。"""
        allowed = {
            "reviewed_runtime_revision",
            "published_runtime_revision",
            "last_error",
            "conflict_status",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self._require_candidate(candidate_id)
        sets = []
        params: list[Any] = []
        for key, value in updates.items():
            sets.append(f"{key} = ?")
            params.append(value)
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(candidate_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE learning_candidates SET {', '.join(sets)} "
                "WHERE candidate_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                self._safe_rollback(connection)
                raise LearningCandidateConflict(
                    f"候选状态竞争：{candidate_id}"
                )
            updated = connection.execute(
                "SELECT * FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            connection.execute("COMMIT")
            return self._row_to_candidate(updated)
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    def _require_candidate(self, candidate_id: str) -> LearningCandidate:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            raise LearningCandidateStoreError(
                f"候选不存在：{candidate_id}"
            )
        return candidate

    # ------------------------------------------------------------------
    # 状态机迁移
    # ------------------------------------------------------------------
    def transition(
        self,
        candidate_id: str,
        target: str,
        *,
        last_error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> LearningCandidate:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                self._safe_rollback(connection)
                raise LearningCandidateStoreError(
                    f"候选不存在：{candidate_id}"
                )
            current = str(row["status"])
            if not can_transition(current, target):
                self._safe_rollback(connection)
                raise LearningStateMachineError(
                    f"非法状态迁移 {current} -> {target}"
                )
            now = time.time()
            sets = ["status = ?", "updated_at = ?"]
            params: list[Any] = [target, now]
            if last_error:
                sets.append("last_error = ?")
                params.append(last_error)
            if extra:
                for key, value in extra.items():
                    if key in {
                        "judge_verdict",
                        "judge_confidence",
                        "judge_reason",
                        "conflict_status",
                        "judge_attempts",
                        "publish_batch_id",
                        "reviewed_runtime_revision",
                        "published_runtime_revision",
                    }:
                        sets.append(f"{key} = ?")
                        params.append(value)
            params.append(candidate_id)
            cursor = connection.execute(
                f"UPDATE learning_candidates SET {', '.join(sets)} "
                "WHERE candidate_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                self._safe_rollback(connection)
                raise LearningCandidateConflict(
                    f"候选状态竞争：{candidate_id}"
                )
            updated = connection.execute(
                "SELECT * FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            connection.execute("COMMIT")
            return self._row_to_candidate(updated)
        except LearningCandidateStoreError:
            self._safe_rollback(connection)
            raise
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    def apply_judge_result(
        self,
        *,
        candidate_id: str,
        verdict: str,
        confidence: float,
        reason: str,
        payload_json: str,
        run_id: str,
        attempts: int,
        target_status: str,
    ) -> LearningCandidate:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                self._safe_rollback(connection)
                raise LearningCandidateStoreError(
                    f"候选不存在：{candidate_id}"
                )
            if not can_transition(str(row["status"]), target_status):
                self._safe_rollback(connection)
                raise LearningStateMachineError(
                    f"非法状态迁移 {row['status']} -> {target_status}"
                )
            now = time.time()
            connection.execute(
                "UPDATE learning_candidates SET status = ?, judge_verdict = ?,"
                " judge_confidence = ?, judge_reason = ?, judge_attempts = ?,"
                " judged_at = ?, updated_at = ? WHERE candidate_id = ?",
                (
                    target_status,
                    verdict,
                    confidence,
                    reason,
                    attempts,
                    now,
                    now,
                    candidate_id,
                ),
            )
            connection.execute(
                "INSERT INTO learning_judge_runs(run_id, candidate_id,"
                " verdict, confidence, reason, payload_json, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, candidate_id, verdict, confidence, reason, payload_json, now),
            )
            connection.execute("COMMIT")
            updated = connection.execute(
                "SELECT * FROM learning_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            return self._row_to_candidate(updated)
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # 批次
    # ------------------------------------------------------------------
    def create_publish_batch(
        self,
        *,
        batch_id: str,
        source_id: str,
        candidate_ids: list[str],
        revision_before: int,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            connection.execute(
                "INSERT OR IGNORE INTO learning_publish_batches(batch_id,"
                " source_id, candidate_ids_json, status,"
                " runtime_revision_before, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    batch_id,
                    source_id,
                    json.dumps(candidate_ids, ensure_ascii=False, sort_keys=True),
                    "created",
                    revision_before,
                    now,
                    now,
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    def get_publish_batch(self, batch_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM learning_publish_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["candidate_ids"] = json.loads(result.pop("candidate_ids_json"))
            result["error"] = json.loads(result.pop("error_json")) if result.get("error_json") else {}
            return result
        finally:
            connection.close()

    def finish_publish_batch(
        self,
        *,
        batch_id: str,
        success: bool,
        revision_after: int,
        error: dict[str, Any] | None = None,
        target_status: str,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            connection.execute(
                "UPDATE learning_publish_batches SET status = ?,"
                " runtime_revision_after = ?, error_json = ?, updated_at = ?"
                " WHERE batch_id = ?",
                (
                    target_status,
                    revision_after,
                    json.dumps(error or {}, ensure_ascii=False, sort_keys=True),
                    now,
                    batch_id,
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    def attach_batch_to_candidates(
        self, candidate_ids: list[str], batch_id: str
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            for candidate_id in candidate_ids:
                row = connection.execute(
                    "SELECT status FROM learning_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None or not can_transition(
                    str(row["status"]), "publishing"
                ):
                    continue
                connection.execute(
                    "UPDATE learning_candidates SET status = ?,"
                    " publish_batch_id = ?, updated_at = ?"
                    " WHERE candidate_id = ?",
                    ("publishing", batch_id, now, candidate_id),
                )
            connection.execute("COMMIT")
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    def finish_candidates_in_batch(
        self,
        candidate_ids: list[str],
        *,
        success: bool,
        revision_after: int,
        error: str = "",
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            for candidate_id in candidate_ids:
                row = connection.execute(
                    "SELECT status FROM learning_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    continue
                current = str(row["status"])
                if success and can_transition(current, "published"):
                    connection.execute(
                        "UPDATE learning_candidates SET status = ?,"
                        " published_runtime_revision = ?, published_at = ?,"
                        " last_error = ?, updated_at = ? WHERE candidate_id = ?",
                        ("published", revision_after, now, "", now, candidate_id),
                    )
                elif (not success) and can_transition(current, "publish_failed"):
                    connection.execute(
                        "UPDATE learning_candidates SET status = ?,"
                        " last_error = ?, updated_at = ? WHERE candidate_id = ?",
                        ("publish_failed", error, now, candidate_id),
                    )
            connection.execute("COMMIT")
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------
    def recover_interrupted(self) -> dict[str, int]:
        """服务重启恢复：judging -> staged；publishing -> publish_failed；
        非终态批次标记 interrupted。返回恢复计数。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            judging = connection.execute(
                "UPDATE learning_candidates SET status = 'staged',"
                " last_error = '服务重启恢复：Judge 中断', updated_at = ?"
                " WHERE status = 'judging'",
                (now,),
            ).rowcount
            publishing = connection.execute(
                "UPDATE learning_candidates SET status = 'publish_failed',"
                " last_error = '服务重启恢复：发布中断，可重试', updated_at = ?"
                " WHERE status = 'publishing'",
                (now,),
            ).rowcount
            batches = connection.execute(
                "UPDATE learning_publish_batches SET status = 'interrupted',"
                " error_json = ?, updated_at = ?"
                " WHERE status IN ('created','publishing')",
                (
                    json.dumps(
                        {"recovered": "服务重启恢复，批次中断"},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            ).rowcount
            connection.execute("COMMIT")
            return {
                "judging_recovered": judging,
                "publishing_recovered": publishing,
                "batches_interrupted": batches,
            }
        except Exception:
            self._safe_rollback(connection)
            raise
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_candidate(row: sqlite3.Row) -> LearningCandidate:
        return LearningCandidate(
            candidate_id=str(row["candidate_id"]),
            source_id=str(row["source_id"]),
            conversation_id=str(row["conversation_id"]),
            request_id=str(row["request_id"]),
            database_type=str(row["database_type"]),
            captured_runtime_revision=int(row["captured_runtime_revision"]),
            reviewed_runtime_revision=int(row["reviewed_runtime_revision"]),
            published_runtime_revision=int(row["published_runtime_revision"]),
            question=str(row["question"]),
            normalized_question=str(row["normalized_question"]),
            question_sha256=str(row["question_sha256"]),
            sql=str(row["sql"]),
            normalized_sql=str(row["normalized_sql"]),
            sql_sha256=str(row["sql_sha256"]),
            args_json=str(row["args_json"]),
            content_fingerprint=str(row["content_fingerprint"]),
            guard_result_json=str(row["guard_result_json"]),
            used_tables=json.loads(str(row["used_tables_json"]) or "[]"),
            used_columns=json.loads(str(row["used_columns_json"]) or "[]"),
            result_summary_json=str(row["result_summary_json"]),
            result_evidence_json=str(row["result_evidence_json"]),
            result_truncated=bool(int(row["result_truncated"] or 0)),
            result_sha256=str(row["result_sha256"]),
            final_answer=str(row["final_answer"]),
            answer_sha256=str(row["answer_sha256"]),
            status=str(row["status"]),
            judge_verdict=str(row["judge_verdict"]),
            judge_confidence=float(row["judge_confidence"] or 0),
            judge_reason=str(row["judge_reason"]),
            conflict_status=str(row["conflict_status"]),
            judge_attempts=int(row["judge_attempts"] or 0),
            publish_batch_id=str(row["publish_batch_id"]),
            last_error=str(row["last_error"]),
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
            judged_at=(
                float(row["judged_at"]) if row["judged_at"] is not None else None
            ),
            published_at=(
                float(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
        )

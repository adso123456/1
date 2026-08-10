"""推荐问题生成任务的幂等存储（独立 SQLite，进卷不进镜像）。

任务身份键 = source_id + runtime_revision + metadata_sha256
             + review_policy_fingerprint + generator_version + materials_fingerprint。

- 同一身份 succeeded：不重复执行；
- pending / running：返回现有任务；
- failed：重新置回 pending（可重试）；
- running 租约过期（进程崩溃）：轮询/启动时回收为 pending。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from config.settings import AGENT_DATA_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS question_suggestion_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    runtime_revision INTEGER NOT NULL,
    metadata_sha256 TEXT NOT NULL,
    review_policy_fingerprint TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    materials_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at REAL,
    lease_expires_at REAL,
    next_retry_at REAL,
    worker_id TEXT,
    asset_path TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (
        source_id,
        runtime_revision,
        metadata_sha256,
        review_policy_fingerprint,
        generator_version,
        materials_fingerprint
    )
);
"""

IDENTITY_COLUMNS = (
    "source_id",
    "runtime_revision",
    "metadata_sha256",
    "review_policy_fingerprint",
    "generator_version",
    "materials_fingerprint",
)


def _identity_key(identity: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(identity[name] for name in IDENTITY_COLUMNS)


class QuestionSuggestionTaskStore:
    """任务库：入队幂等、租约领取、失败退避、崩溃恢复。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else (
            Path(AGENT_DATA_DIR).resolve()
            / "question_suggestions"
            / "tasks.sqlite3"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def _row(self, task_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM question_suggestion_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def enqueue(self, identity: Mapping[str, Any]) -> dict[str, Any]:
        """按任务身份幂等入队。"""
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM question_suggestion_tasks "
                "WHERE source_id=? AND runtime_revision=? AND metadata_sha256=?"
                " AND review_policy_fingerprint=? AND generator_version=?"
                " AND materials_fingerprint=?",
                _identity_key(identity),
            ).fetchone()
            if row is not None:
                if row["status"] == "failed":
                    self._conn.execute(
                        "UPDATE question_suggestion_tasks "
                        "SET status='pending', error=?, next_retry_at=NULL, "
                        "updated_at=? WHERE id=?",
                        (row["error"], now, row["id"]),
                    )
                return self._row(row["id"])  # type: ignore[return-value]
            cursor = self._conn.execute(
                "INSERT INTO question_suggestion_tasks ("
                " source_id, runtime_revision, metadata_sha256,"
                " review_policy_fingerprint, generator_version,"
                " materials_fingerprint, status, attempt_count,"
                " created_at, updated_at"
                ") VALUES (?,?,?,?,?,?, 'pending', 0, ?, ?)",
                (*_identity_key(identity), now, now),
            )
            return self._row(cursor.lastrowid)  # type: ignore[return-value]

    def claim_next(
        self,
        worker_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 900.0,
    ) -> dict[str, Any] | None:
        """领取一个待处理或已到重试时间的失败任务，并加租约。"""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM question_suggestion_tasks "
                "WHERE status='pending' "
                "   OR (status='failed' AND next_retry_at IS NOT NULL "
                "       AND next_retry_at <= ?) "
                "ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE question_suggestion_tasks "
                "SET status='running', worker_id=?, claimed_at=?, "
                "    lease_expires_at=?, attempt_count=attempt_count+1, "
                "    updated_at=? WHERE id=?",
                (worker_id, now, now + lease_seconds, now, row["id"]),
            )
            return self._row(row["id"])  # type: ignore[return-value]

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        """进程崩溃后把租约过期的 running 任务回收为 pending。"""
        now = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE question_suggestion_tasks "
                "SET status='pending', worker_id=NULL, lease_expires_at=NULL, "
                "    updated_at=? "
                "WHERE status='running' AND lease_expires_at IS NOT NULL "
                "  AND lease_expires_at < ?",
                (now, now),
            )
            return cursor.rowcount

    def mark_succeeded(self, task_id: int, asset_path: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE question_suggestion_tasks "
                "SET status='succeeded', asset_path=?, error=NULL, "
                "    next_retry_at=NULL, updated_at=? WHERE id=?",
                (asset_path, now, task_id),
            )

    def mark_failed(
        self,
        task_id: int,
        error: str,
        *,
        next_retry_at: float | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE question_suggestion_tasks "
                "SET status='failed', error=?, next_retry_at=?, updated_at=? "
                "WHERE id=?",
                (error, next_retry_at, now, task_id),
            )

    def get(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._row(task_id)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

"""推荐问题生成任务的幂等存储（独立 SQLite，进卷不进镜像）。

任务身份键 = source_id + runtime_revision + selected_scope_fingerprint
             + metadata_sha256 + review_policy_fingerprint
             + formal_sql_memory_fingerprint + generator_version。

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
    selected_scope_fingerprint TEXT NOT NULL DEFAULT '',
    metadata_sha256 TEXT NOT NULL,
    review_policy_fingerprint TEXT NOT NULL,
    formal_sql_memory_fingerprint TEXT NOT NULL DEFAULT '',
    generator_version TEXT NOT NULL,
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
        selected_scope_fingerprint,
        metadata_sha256,
        review_policy_fingerprint,
        formal_sql_memory_fingerprint,
        generator_version
    )
);
"""

IDENTITY_COLUMNS = (
    "source_id",
    "runtime_revision",
    "selected_scope_fingerprint",
    "metadata_sha256",
    "review_policy_fingerprint",
    "formal_sql_memory_fingerprint",
    "generator_version",
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
            columns = {
                row["name"] for row in self._conn.execute(
                    "PRAGMA table_info(question_suggestion_tasks)"
                )
            }
            if "materials_fingerprint" in columns:
                for name in (
                    "selected_scope_fingerprint",
                    "formal_sql_memory_fingerprint",
                ):
                    if name not in columns:
                        self._conn.execute(
                            f"ALTER TABLE question_suggestion_tasks "
                            f"ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                        )
                self._conn.execute(
                    "ALTER TABLE question_suggestion_tasks "
                    "RENAME TO question_suggestion_tasks_legacy"
                )
                self._conn.executescript(SCHEMA)
                self._conn.execute(
                    "INSERT OR IGNORE INTO question_suggestion_tasks ("
                    " id, source_id, runtime_revision,"
                    " selected_scope_fingerprint, metadata_sha256,"
                    " review_policy_fingerprint,"
                    " formal_sql_memory_fingerprint, generator_version,"
                    " status, attempt_count, claimed_at, lease_expires_at,"
                    " next_retry_at, worker_id, asset_path, error,"
                    " created_at, updated_at)"
                    " SELECT id, source_id, runtime_revision,"
                    " COALESCE(selected_scope_fingerprint, ''),"
                    " metadata_sha256, review_policy_fingerprint,"
                    " COALESCE(formal_sql_memory_fingerprint, ''),"
                    " generator_version, status, attempt_count, claimed_at,"
                    " lease_expires_at, next_retry_at, worker_id, asset_path,"
                    " error, created_at, updated_at"
                    " FROM question_suggestion_tasks_legacy"
                )
                self._conn.execute("DROP TABLE question_suggestion_tasks_legacy")

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
                "WHERE source_id=? AND runtime_revision=?"
                " AND selected_scope_fingerprint=? AND metadata_sha256=?"
                " AND review_policy_fingerprint=?"
                " AND formal_sql_memory_fingerprint=? AND generator_version=?",
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
                " source_id, runtime_revision, selected_scope_fingerprint,"
                " metadata_sha256, review_policy_fingerprint,"
                " formal_sql_memory_fingerprint, generator_version,"
                " status, attempt_count,"
                " created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?, 'pending', 0, ?, ?)",
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


def reconcile_question_suggestion_tasks(
    catalog: Any,
    store: QuestionSuggestionTaskStore,
) -> int:
    """启动时把 ready+enabled 且缺失/过期的气泡资产幂等入队。"""
    from backend.question_suggestion_assets import load_question_directory
    from backend.question_suggestion_generator import generation_identity

    enqueued = 0
    for record in catalog.list():
        if record.status != "ready" or not record.enabled_for_chat:
            continue
        identity = generation_identity(catalog, record.source_id)
        directory = load_question_directory(record.source_id)
        basis = dict(directory.get("basis") or {}) if directory else {}
        current = {
            "source_id": directory.get("source_id") if directory else None,
            "runtime_revision": (
                directory.get("runtime_revision") if directory else None
            ),
            "selected_scope_fingerprint": basis.get(
                "selected_scope_fingerprint"
            ),
            "metadata_sha256": (
                directory.get("metadata_sha256") if directory else None
            ),
            "review_policy_fingerprint": basis.get(
                "review_policy_fingerprint"
            ),
            "formal_sql_memory_fingerprint": basis.get(
                "formal_sql_memory_fingerprint"
            ),
            "generator_version": basis.get("generator_version"),
        }
        if current != identity:
            store.enqueue(identity)
            enqueued += 1
    return enqueued

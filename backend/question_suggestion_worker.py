"""推荐问题生成后台 Worker（单线程）。

- 轮询任务库，领取 pending / 到重试时间的 failed 任务；
- 生成期间不阻塞 onboarding，失败不回滚 Catalog；
- 先生成临时资产 → 校验身份 → os.replace 原子替换 → 最后标记 succeeded；
- 身份过期（正式状态已变化）的任务不自动重试，等下一次入队。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from backend.data_source_catalog import DataSourceCatalog
from backend.question_suggestion_generator import (
    GenerationIdentityMismatchError,
    generate_for_source,
)
from backend.question_suggestion_tasks import QuestionSuggestionTaskStore


logger = logging.getLogger(__name__)


class QuestionSuggestionWorker:
    def __init__(
        self,
        store: QuestionSuggestionTaskStore,
        catalog: DataSourceCatalog,
        *,
        poll_interval: float = 5.0,
        lease_seconds: float = 900.0,
        max_backoff: float = 300.0,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._max_backoff = max_backoff
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_id = f"worker-{threading.get_ident()}"

    def start(self) -> None:
        """启动后台线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="question-suggestion-worker",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        """请求停止并等待线程退出。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._store.recover_expired_leases()
                task = self._store.claim_next(
                    self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if task is None:
                    self._stop.wait(self._poll_interval)
                    continue
                self._process(task)
            except Exception:
                logger.exception("推荐问题生成 Worker 主循环异常")
                self._stop.wait(self._poll_interval)

    def _process(self, task: dict[str, Any]) -> None:
        identity = {
            "source_id": task["source_id"],
            "runtime_revision": task["runtime_revision"],
            "metadata_sha256": task["metadata_sha256"],
            "review_policy_fingerprint": task["review_policy_fingerprint"],
            "generator_version": task["generator_version"],
            "materials_fingerprint": task["materials_fingerprint"],
        }
        try:
            summary = generate_for_source(
                task["source_id"],
                expected_identity=identity,
                catalog=self._catalog,
            )
        except GenerationIdentityMismatchError as exc:
            # 正式状态已变化：任务过期，不自动重试，等待下一次入队。
            logger.warning("推荐问题任务身份过期（%s）：%s", task["id"], exc)
            self._store.mark_failed(task["id"], str(exc), next_retry_at=None)
            return
        except Exception as exc:
            backoff = min(
                self._max_backoff,
                2.0 ** max(0, int(task["attempt_count"]) - 1),
            )
            logger.exception("推荐问题生成失败（task=%s）", task["id"])
            self._store.mark_failed(
                task["id"],
                f"{type(exc).__name__}: {exc}",
                next_retry_at=time.time() + backoff,
            )
            return
        self._store.mark_succeeded(task["id"], str(summary["asset_path"]))
        logger.info(
            "推荐问题资产已生成（task=%s source=%s revision=%s enabled=%s）",
            task["id"],
            task["source_id"],
            task["runtime_revision"],
            summary.get("enabled_question_count"),
        )

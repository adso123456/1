"""可恢复的后台 Worker：不依赖人工脚本触发运行时学习。

只在 FastAPI/server 生命周期中 start/stop，绝不在 import 时启动线程。
Worker 异常只记录脱敏错误，不影响主服务。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.runtime_learning_service import RuntimeLearningService
from config.learning_settings import OnlineLearningSettings

logger = logging.getLogger(__name__)


class RuntimeLearningWorker:
    def __init__(
        self,
        service: RuntimeLearningService,
        settings: OnlineLearningSettings,
    ) -> None:
        self._service = service
        self._settings = settings
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="runtime-learning-worker")

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("运行时学习 Worker 停止时异常")

    async def run_once(self) -> dict[str, Any]:
        """暴露给管理员 API 的同步单轮执行。"""
        return await self._tick()

    async def _run(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("运行时学习 Worker 单轮执行失败")
            try:
                await asyncio.sleep(self._settings.worker_interval_seconds)
            except asyncio.CancelledError:
                raise

    async def _tick(self) -> dict[str, Any]:
        if not self._settings.enabled:
            return {"enabled": False}
        result: dict[str, Any] = {"enabled": True}
        try:
            result["recovered"] = self._service.recover_interrupted()
        except Exception:
            logger.exception("恢复中断状态失败")
            result["recovered"] = "error"

        judged = 0
        if self._settings.judge_enabled:
            staged = self._service.list_candidates(statuses=["staged"], limit=20)
            for candidate in staged:
                try:
                    await self._service.judge_candidate(candidate.candidate_id)
                    judged += 1
                except Exception:
                    # 单候选失败不影响其他候选
                    continue
        result["judged"] = judged

        published: list[str] = []
        if self._settings.auto_publish:
            for source_id in self._service.publish_ready_source_ids():
                try:
                    outcome = await self._service.publish_source(source_id)
                    published.append(f"{source_id}:{outcome.get('published', 0)}")
                except Exception:
                    logger.exception("自动发布数据源 %s 失败", source_id)
        result["published"] = published
        return result

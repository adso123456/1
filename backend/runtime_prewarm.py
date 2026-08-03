"""按 Catalog 状态顺序预热可问数 Runtime。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from threading import RLock

from backend.data_source_runtime_manager import DataSourceRuntimeManager


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimePrewarmStatus:
    source_id: str
    status: str = "not_started"
    elapsed_ms: float = 0.0
    exception_type: str = ""


class RuntimePrewarmer:
    def __init__(self, runtime_manager: DataSourceRuntimeManager) -> None:
        self._runtime_manager = runtime_manager
        self._lock = RLock()
        self._states = {
            source_id: RuntimePrewarmStatus(source_id)
            for source_id in runtime_manager.source_ids
        }

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                source_id: asdict(self._states[source_id])
                for source_id in sorted(self._states)
            }

    def _set(self, state: RuntimePrewarmStatus) -> None:
        with self._lock:
            self._states[state.source_id] = state

    async def warm_ready_sources(self) -> dict[str, dict[str, object]]:
        registry = self._runtime_manager.registry
        catalog = registry.catalog
        source_ids = []
        if catalog is None:
            return self.snapshot()
        for source_id in sorted(registry.source_ids):
            record = catalog.require(source_id)
            if record.status == "ready" and record.enabled_for_chat:
                source_ids.append(source_id)

        for source_id in source_ids:
            self._set(RuntimePrewarmStatus(source_id, "warming"))
            started = time.monotonic()
            try:
                await asyncio.to_thread(self._runtime_manager.require, source_id)
            except Exception as error:
                elapsed = (time.monotonic() - started) * 1000
                self._set(
                    RuntimePrewarmStatus(
                        source_id,
                        "failed",
                        round(elapsed, 3),
                        type(error).__name__,
                    )
                )
                logger.error(
                    "Runtime prewarm failed source_id=%s stage=require exception_type=%s elapsed_ms=%.3f",
                    source_id,
                    type(error).__name__,
                    elapsed,
                )
                continue
            elapsed = (time.monotonic() - started) * 1000
            self._set(
                RuntimePrewarmStatus(source_id, "ready", round(elapsed, 3))
            )
            logger.info(
                "Runtime prewarm ready source_id=%s elapsed_ms=%.3f",
                source_id,
                elapsed,
            )
        return self.snapshot()

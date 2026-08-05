"""受保护的本地管理员 API：查看候选、手动 Judge/批准/拒绝/发布。"""

from __future__ import annotations

from typing import Any

from backend.assistant_admin_api import _authorize
from backend.runtime_learning_service import (
    LearningPublishConflict,
    RuntimeLearningService,
    RuntimeLearningServiceError,
)
from backend.runtime_learning_worker import RuntimeLearningWorker
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

_PREFIX = "/api/admin/runtime-learning"


def _candidate_view(candidate: Any) -> dict[str, Any]:
    return candidate.model_dump()


def create_runtime_learning_router(
    service: RuntimeLearningService,
    worker: RuntimeLearningWorker | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    def _error(exc: Exception) -> HTTPException:
        if isinstance(exc, LearningPublishConflict):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, RuntimeLearningServiceError):
            return HTTPException(status_code=400, detail=str(exc))
        return HTTPException(status_code=500, detail="运行时学习操作失败")

    @router.get(f"{_PREFIX}/status", dependencies=protected)
    def status() -> dict[str, Any]:
        return {
            "counts": service.counts(),
            "worker_running": bool(worker is not None and worker.running),
        }

    @router.get(f"{_PREFIX}/candidates", dependencies=protected)
    def list_candidates(
        status: str | None = Query(default=None),
        source_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        statuses = [status] if status else None
        return [
            _candidate_view(candidate)
            for candidate in service.list_candidates(
                statuses=statuses,
                source_id=source_id,
                limit=limit,
                offset=offset,
            )
        ]

    @router.get(f"{_PREFIX}/candidates/{{candidate_id}}", dependencies=protected)
    def get_candidate(candidate_id: str) -> dict[str, Any]:
        candidate = service.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="候选不存在")
        return _candidate_view(candidate)

    @router.post(
        f"{_PREFIX}/candidates/{{candidate_id}}/judge", dependencies=protected
    )
    async def judge_candidate(candidate_id: str) -> dict[str, Any]:
        try:
            updated = await service.judge_candidate(candidate_id)
        except Exception as exc:
            raise _error(exc) from exc
        return _candidate_view(updated)

    @router.post(
        f"{_PREFIX}/candidates/{{candidate_id}}/approve", dependencies=protected
    )
    def approve_candidate(candidate_id: str) -> dict[str, Any]:
        try:
            updated = service.approve(candidate_id)
        except Exception as exc:
            raise _error(exc) from exc
        return _candidate_view(updated)

    @router.post(
        f"{_PREFIX}/candidates/{{candidate_id}}/reject", dependencies=protected
    )
    def reject_candidate(candidate_id: str) -> dict[str, Any]:
        try:
            updated = service.reject(candidate_id)
        except Exception as exc:
            raise _error(exc) from exc
        return _candidate_view(updated)

    @router.post(
        f"{_PREFIX}/sources/{{source_id}}/publish", dependencies=protected
    )
    async def publish_source(source_id: str) -> dict[str, Any]:
        try:
            return await service.publish_source(source_id, force=True)
        except Exception as exc:
            raise _error(exc) from exc

    @router.post(f"{_PREFIX}/worker/run", dependencies=protected)
    async def run_worker_once() -> dict[str, Any]:
        if worker is None:
            raise HTTPException(status_code=503, detail="Worker 未启用")
        return await worker.run_once()

    return router

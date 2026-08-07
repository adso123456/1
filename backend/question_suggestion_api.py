"""数据源专属推荐问题的在线读取 API。

`GET /api/conversations/{conversation_id}/suggested-questions`
- 以服务端会话绑定为准解析 source_id，绝不信任前端传入的 source_id；
- 未绑定会话返回 404（明确安全响应）；
- 数据源不可用或资产缺失/损坏/不匹配时返回空列表，绝不跨源补齐。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.assistant_admin_api import _authorize
from backend.data_source_catalog import (
    DataSourceCatalog,
    selected_scope_fingerprint,
)
from backend.data_source_request_coordinator import (
    DataSourceRequestCoordinator,
)
from backend.question_suggestion_assets import (
    load_question_directory,
    select_suggested_questions,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Request


def _formal_identity(
    catalog: DataSourceCatalog,
    record: Any,
) -> dict[str, Any] | None:
    """轻量读取正式 manifest / provenance 并计算当前身份快照。

    任一正式身份文件缺失或损坏 → 返回 None（在线读取失败关闭）。
    """
    try:
        from backend.data_source_asset_provenance import (
            provenance_fingerprint,
        )

        root = Path(record.metadata_path).resolve().parent
        manifest = json.loads(
            (root / "asset_manifest.json").read_text(encoding="utf-8")
        )
        provenance = json.loads(
            (root / "asset_provenance.json").read_text(encoding="utf-8")
        )
    except Exception:
        return None
    if not isinstance(manifest, dict) or not isinstance(provenance, dict):
        return None
    if not str(manifest.get("metadata_hash") or ""):
        return None
    if not str(manifest.get("provenance_hash") or ""):
        return None
    try:
        scope_fingerprint = selected_scope_fingerprint(record.selected_scope)
        policy_fingerprint = catalog.review_policy(record.source_id)[
            "fingerprint"
        ]
    except Exception:
        return None
    try:
        provenance_hash = provenance_fingerprint(provenance)
    except Exception:
        provenance_hash = ""
    return {
        "runtime_revision": record.runtime_revision,
        "metadata_sha256": str(manifest.get("metadata_hash") or ""),
        "scope_fingerprint": scope_fingerprint,
        "review_policy_fingerprint": policy_fingerprint,
        "provenance_hash": str(manifest.get("provenance_hash") or ""),
        "actual_provenance_hash": provenance_hash,
    }


def _empty(source_id: str, asset_version: Any) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "asset_version": asset_version,
        "questions": [],
    }


def create_question_suggestion_router(
    catalog: DataSourceCatalog,
    coordinator: DataSourceRequestCoordinator,
    *,
    asset_root: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def authorize(
        request: Request,
        origin: str | None = Header(default=None),
    ) -> None:
        _authorize(request, origin)

    protected = [Depends(authorize)]

    @router.get(
        "/conversations/{conversation_id}/suggested-questions",
        dependencies=protected,
    )
    def suggested_questions(conversation_id: str) -> dict[str, Any]:
        try:
            context = coordinator.require(conversation_id)
        except ValueError:
            raise HTTPException(
                status_code=404,
                detail="会话尚未绑定数据源",
            ) from None
        try:
            record = catalog.require(context.source_id)
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="数据源不存在",
            ) from None
        if record.status != "ready" or not record.enabled_for_chat:
            return _empty(context.source_id, None)
        directory = load_question_directory(
            context.source_id,
            root=asset_root,
        )
        if directory is None:
            return _empty(context.source_id, None)
        # E-3：六项正式身份硬门，任一不一致即空列表，绝不回退。
        identity = _formal_identity(catalog, record)
        if identity is None:
            return _empty(context.source_id, directory["asset_version"])
        if (
            directory.get("runtime_revision") != identity["runtime_revision"]
            or directory.get("metadata_sha256")
            != identity["metadata_sha256"]
            or directory.get("scope_fingerprint")
            != identity["scope_fingerprint"]
            or directory.get("review_policy_fingerprint")
            != identity["review_policy_fingerprint"]
            or directory.get("provenance_hash")
            != identity["provenance_hash"]
            or identity["actual_provenance_hash"]
            != identity["provenance_hash"]
        ):
            return _empty(context.source_id, directory["asset_version"])
        questions = select_suggested_questions(directory, conversation_id)
        return {
            "source_id": context.source_id,
            "asset_version": directory["asset_version"],
            "questions": questions,
        }

    return router

"""asset_provenance.json：候选/正式运行资产的结构化来源证明。

生命周期与正式资产一致：候选阶段写入 candidate_root，发布后安装到
formal_asset_root，参与 manifest / .asset_identity.json 哈希、
set_enabled 复用门、rollback / restart recovery / cleanup。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.data_source_catalog import DataSourceCatalogError


PROVENANCE_SCHEMA_VERSION = 1

ASSET_KINDS = (
    "documentation",
    "chroma_ddl",
    "chroma_documentation",
    "sql_tool_memory",
)


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chroma_record_id(source_id: str, memory_type: str, document: str) -> str:
    """与候选 Chroma 记录 ID 公式一致：b5-<sha256(source_id|type|document)>。"""
    digest = hashlib.sha256(
        f"{source_id}|{memory_type}|{document}".encode("utf-8")
    ).hexdigest()
    return f"b5-{digest}"


def build_provenance(
    *,
    source_id: str,
    runtime_revision: int,
    scope_fingerprint: str,
    review_policy_fingerprint: str,
    assets: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    assets = dict(assets or {})
    payload: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_id": source_id,
        "runtime_revision": runtime_revision,
        "scope_fingerprint": scope_fingerprint,
        "review_policy_fingerprint": review_policy_fingerprint,
        "assets": {
            kind: [dict(item) for item in assets.get(kind, [])]
            for kind in ASSET_KINDS
        },
    }
    return payload


def provenance_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_provenance(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_provenance(path: Path) -> dict[str, Any]:
    """读取并做基础结构校验；任何损坏立即失败关闭。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataSourceCatalogError("asset_provenance.json 不可读") from exc
    if not isinstance(payload, Mapping):
        raise DataSourceCatalogError("asset_provenance.json 必须是对象")
    if int(payload.get("schema_version") or -1) != PROVENANCE_SCHEMA_VERSION:
        raise DataSourceCatalogError(
            f"asset_provenance schema_version 不支持："
            f"{payload.get('schema_version')}"
        )
    for key in (
        "source_id",
        "runtime_revision",
        "scope_fingerprint",
        "review_policy_fingerprint",
    ):
        if key not in payload:
            raise DataSourceCatalogError(
                f"asset_provenance 缺少字段：{key}"
            )
    assets = payload.get("assets")
    if not isinstance(assets, Mapping):
        raise DataSourceCatalogError("asset_provenance.assets 必须是对象")
    for kind in ASSET_KINDS:
        records = assets.get(kind)
        if not isinstance(records, list):
            raise DataSourceCatalogError(
                f"asset_provenance.assets.{kind} 必须是数组"
            )
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise DataSourceCatalogError(
                    f"asset_provenance.assets.{kind} 第 {index + 1} 项不是对象"
                )
    return dict(payload)

"""问数运行资产的结构化来源证明。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


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
    source_assets = dict(assets or {})
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_id": source_id,
        "runtime_revision": runtime_revision,
        "scope_fingerprint": scope_fingerprint,
        "review_policy_fingerprint": review_policy_fingerprint,
        "assets": {
            kind: [dict(item) for item in source_assets.get(kind, [])]
            for kind in ASSET_KINDS
        },
    }


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

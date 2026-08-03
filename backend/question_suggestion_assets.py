"""数据源专属推荐问题资产的在线只读读取与确定性抽取。

资产由离线进程 `tools/generate_question_suggestions.py` 生成，按 source_id 严格隔离存放于
`<AGENT_DATA_DIR>/question_suggestions/<source_id>/questions_v1.json`。

在线服务只做轻量只读：
- 按服务端已绑定的 source_id 定位资产文件；
- 目录缺失、损坏或 source_id 不匹配时返回空，绝不跨源补齐；
- 按 (source_id, conversation_id, asset_version) 生成确定性随机种子抽取推荐问题。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.settings import AGENT_DATA_DIR


ASSET_SCHEMA_VERSION = 1
ASSET_FILENAME = "questions_v1.json"

# 新会话抽取上限：不足时只返回实际可用数量
DEFAULT_LIMIT = 4
HARD_MAX_LIMIT = 8

_ENV_ROOT = "QUESTION_SUGGESTIONS_DIR"


def question_suggestions_root(*, environ: Mapping[str, str] | None = None) -> Path:
    """问题资产根目录。可用 `QUESTION_SUGGESTIONS_DIR` 覆盖（隔离测试用）。"""
    source = os.environ if environ is None else environ
    override = source.get(_ENV_ROOT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(AGENT_DATA_DIR).resolve() / "question_suggestions").resolve()


def asset_path(
    source_id: str,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """返回指定 source_id 的问题资产文件路径。"""
    source_id = _require_source_id(source_id)
    base = root if root is not None else question_suggestions_root(environ=environ)
    return base / source_id / ASSET_FILENAME


def _require_source_id(source_id: Any) -> str:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id 必须是非空字符串")
    return source_id.strip()


def build_question_directory(
    source_id: str,
    questions: list[dict[str, Any]],
    *,
    asset_version: str = "v1",
    runtime_revision: int | None = None,
    metadata_sha256: str = "",
    generated_at: str = "",
    generator: str = "",
    basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造符合 V1 资产契约的目录文档。"""
    source_id = _require_source_id(source_id)
    if not isinstance(questions, list):
        raise TypeError("questions 必须是列表")
    normalized: list[dict[str, Any]] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        qid = item.get("id")
        text = item.get("text")
        if not isinstance(qid, str) or not qid.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        entry: dict[str, Any] = {
            "id": qid.strip(),
            "text": text.strip(),
            "enabled": bool(item.get("enabled", True)),
        }
        for optional in (
            "category",
            "related_tables",
            "related_sample_id",
            "related_sql",
            "verification",
            "disabled_reason",
        ):
            if optional in item:
                entry[optional] = item[optional]
        normalized.append(entry)
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "source_id": source_id,
        "asset_version": asset_version,
        "runtime_revision": runtime_revision,
        "metadata_sha256": metadata_sha256,
        "generated_at": generated_at,
        "generator": generator,
        "basis": dict(basis or {}),
        "questions": normalized,
    }


def write_question_directory(
    directory: Mapping[str, Any],
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """原子写入本源问题资产文件（先写临时文件再改名）。"""
    source_id = _require_source_id(directory.get("source_id"))
    target = asset_path(source_id, root=root, environ=environ)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        directory,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(target)
    return target


def load_question_directory(
    source_id: str,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """加载并校验本源问题资产目录；缺失/损坏/不匹配时返回 None。"""
    source_id = _require_source_id(source_id)
    path = asset_path(source_id, root=root, environ=environ)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != ASSET_SCHEMA_VERSION:
        return None
    if payload.get("source_id") != source_id:
        return None
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: list[dict[str, Any]] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        qid = item.get("id")
        text = item.get("text")
        if not isinstance(qid, str) or not qid.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        questions.append(
            {
                "id": qid.strip(),
                "text": text.strip(),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    asset_version = payload.get("asset_version")
    if not isinstance(asset_version, str) or not asset_version.strip():
        asset_version = "v1"
    runtime_revision = payload.get("runtime_revision")
    if not isinstance(runtime_revision, int) or isinstance(runtime_revision, bool):
        runtime_revision = None
    metadata_sha256 = payload.get("metadata_sha256")
    if not isinstance(metadata_sha256, str) or not metadata_sha256.strip():
        metadata_sha256 = ""
    return {
        "source_id": source_id,
        "asset_version": asset_version.strip(),
        "runtime_revision": runtime_revision,
        "metadata_sha256": metadata_sha256.strip(),
        "questions": questions,
    }


def select_suggested_questions(
    directory: Mapping[str, Any],
    conversation_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, str]]:
    """按本源目录确定性抽取推荐问题。

    - 只返回本源资产中的启用且去重问题；
    - 不足 limit 条时返回全部；
    - 种子 = (source_id, conversation_id, asset_version) → 同一会话刷新稳定、不同会话可不同。
    """
    source_id = _require_source_id(directory.get("source_id"))
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id 必须是非空字符串")
    asset_version = directory.get("asset_version") or "v1"
    normalized_limit = max(1, min(int(limit), HARD_MAX_LIMIT))

    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in directory.get("questions", []):
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if text in seen:
            continue
        seen.add(text)
        pool.append(item)
    pool.sort(key=lambda item: str(item.get("id", "")))

    if len(pool) <= normalized_limit:
        selected = pool
    else:
        seed_bytes = (
            f"{source_id}\x00{conversation_id.strip()}\x00{asset_version}"
        ).encode("utf-8")
        seed = int.from_bytes(hashlib.sha256(seed_bytes).digest()[:8], "big")
        selected = random.Random(seed).sample(pool, normalized_limit)
        selected.sort(key=lambda item: str(item.get("id", "")))

    return [
        {"id": str(item["id"]), "text": item["text"]}
        for item in selected
    ]

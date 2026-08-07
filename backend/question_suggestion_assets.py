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


ASSET_SCHEMA_VERSION = 2
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


def _ensure_contained(root: Path, target: Path) -> Path:
    """校验 target 位于 root 内且受管路径链无符号链接/junction。

    在第一次文件写入之前调用；越界或链上符号链接一律拒绝。
    """
    root_resolved = Path(root).expanduser().resolve()
    target_resolved = Path(target).expanduser().resolve()
    if (
        target_resolved != root_resolved
        and not target_resolved.is_relative_to(root_resolved)
    ):
        raise ValueError("受管路径越界")
    try:
        relative = target_resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError("受管路径越界") from None
    cursor = root_resolved
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("受管路径链不允许符号链接")
    return target_resolved


def asset_path(
    source_id: str,
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """返回指定 source_id 的问题资产文件路径。"""
    source_id = _require_source_id(source_id)
    base = root if root is not None else question_suggestions_root(environ=environ)
    return _ensure_contained(base, base / source_id / ASSET_FILENAME)


def _require_source_id(source_id: Any) -> str:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id 必须是非空字符串")
    value = source_id.strip()
    if (
        "/" in value
        or "\\" in value
        or ".." in value
        or Path(value).is_absolute()
    ):
        raise ValueError("source_id 含非法路径字符")
    return value


def _payload_bytes(directory: Mapping[str, Any]) -> str:
    return json.dumps(
        directory,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _strict_questions(raw_questions: Any) -> list[dict[str, Any]] | None:
    """严格校验 question 数组：任一条目结构非法则整体拒绝。"""
    if not isinstance(raw_questions, list):
        return None
    questions: list[dict[str, Any]] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            return None
        qid = item.get("id")
        text = item.get("text")
        if not isinstance(qid, str) or not qid.strip():
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        questions.append(
            {
                "id": qid.strip(),
                "text": text.strip(),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return questions


def write_question_candidate(
    directory: Mapping[str, Any],
    *,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    candidate_name: str,
) -> Path:
    """写入唯一候选文件，不替换正式文件。"""
    source_id = _require_source_id(directory.get("source_id"))
    target = asset_path(source_id, root=root, environ=environ)
    source_dir = _ensure_contained(root or question_suggestions_root(environ=environ), target.parent)
    source_dir.mkdir(parents=True, exist_ok=True)
    candidate_name = str(candidate_name or "")
    if (
        not candidate_name
        or "/" in candidate_name
        or "\\" in candidate_name
        or ".." in candidate_name
    ):
        raise ValueError("candidate_name 含非法路径字符")
    candidate = target.with_name(
        f".questions.candidate-{candidate_name}.json"
    )
    _ensure_contained(root or question_suggestions_root(environ=environ), candidate)
    candidate.write_text(
        _payload_bytes(directory),
        encoding="utf-8",
    )
    with candidate.open("r+b") as handle:
        os.fsync(handle.fileno())
    return candidate


def commit_question_candidate(
    candidate: Path,
    *,
    source_id: str,
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """候选文件原子替换正式文件并返回正式路径。"""
    source_id = _require_source_id(source_id)
    base = root if root is not None else question_suggestions_root(environ=environ)
    candidate = candidate.expanduser().resolve()
    _ensure_contained(base, candidate)
    target = asset_path(source_id, root=root, environ=environ)
    if candidate.parent != target.parent:
        raise ValueError("候选文件越界")
    if not candidate.is_file():
        raise FileNotFoundError(f"候选文件不存在: {candidate}")
    os.replace(candidate, target)
    return target


def build_question_directory(
    source_id: str,
    questions: list[dict[str, Any]],
    *,
    asset_version: str = "v1",
    runtime_revision: int | None = None,
    metadata_sha256: str = "",
    scope_fingerprint: str = "",
    review_policy_fingerprint: str = "",
    provenance_hash: str = "",
    generated_at: str = "",
    generator: str = "",
    basis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造符合 V2 资产契约的目录文档（绑定完整正式身份）。"""
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
        "scope_fingerprint": scope_fingerprint,
        "review_policy_fingerprint": review_policy_fingerprint,
        "provenance_hash": provenance_hash,
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
    candidate_name: str | None = None,
) -> Path:
    """写入唯一候选文件并原子替换正式文件（兼容既有调用）。"""
    source_id = _require_source_id(directory.get("source_id"))
    if candidate_name is None:
        import uuid

        candidate_name = uuid.uuid4().hex
    candidate = write_question_candidate(
        directory,
        root=root,
        environ=environ,
        candidate_name=candidate_name,
    )
    return commit_question_candidate(
        candidate,
        source_id=source_id,
        root=root,
        environ=environ,
    )


def load_question_directory_file(
    path: Path,
    source_id: str,
) -> dict[str, Any] | None:
    """从指定文件路径加载并校验本源问题资产目录（供候选回读用）。"""
    path = Path(path).expanduser().resolve()
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
    if _strict_questions(payload.get("questions")) is None:
        return None
    return dict(payload)


def validate_question_directory_payload(
    payload: Mapping[str, Any],
    source_id: str,
    *,
    runtime_revision: int,
    metadata_sha256: str,
    scope_fingerprint: str,
    review_policy_fingerprint: str,
    provenance_hash: str,
) -> None:
    """候选/正式资产的完整 schema 校验（含身份字段）。"""
    if not isinstance(payload, Mapping):
        raise ValueError("推荐问题资产必须是对象")
    if payload.get("schema_version") != ASSET_SCHEMA_VERSION:
        raise ValueError("推荐问题资产 schema_version 不正确")
    if payload.get("source_id") != source_id:
        raise ValueError("推荐问题资产 source_id 不匹配")
    if _strict_questions(payload.get("questions")) is None:
        raise ValueError("推荐问题资产 questions 必须是数组")
    if int(payload.get("runtime_revision") or -1) != runtime_revision:
        raise ValueError("推荐问题资产 runtime_revision 不匹配")
    if payload.get("metadata_sha256") != metadata_sha256:
        raise ValueError("推荐问题资产 metadata_sha256 不匹配")
    if payload.get("scope_fingerprint") != scope_fingerprint:
        raise ValueError("推荐问题资产 scope_fingerprint 不匹配")
    if payload.get("review_policy_fingerprint") != review_policy_fingerprint:
        raise ValueError("推荐问题资产 review_policy_fingerprint 不匹配")
    if payload.get("provenance_hash") != provenance_hash:
        raise ValueError("推荐问题资产 provenance_hash 不匹配")


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
    questions = _strict_questions(payload.get("questions"))
    if questions is None:
        return None
    asset_version = payload.get("asset_version")
    if not isinstance(asset_version, str) or not asset_version.strip():
        asset_version = "v1"
    runtime_revision = payload.get("runtime_revision")
    if not isinstance(runtime_revision, int) or isinstance(runtime_revision, bool):
        runtime_revision = None
    metadata_sha256 = payload.get("metadata_sha256")
    if not isinstance(metadata_sha256, str) or not metadata_sha256.strip():
        metadata_sha256 = ""
    scope_fingerprint = payload.get("scope_fingerprint")
    if not isinstance(scope_fingerprint, str) or not scope_fingerprint.strip():
        scope_fingerprint = ""
    review_policy_fingerprint = payload.get("review_policy_fingerprint")
    if (
        not isinstance(review_policy_fingerprint, str)
        or not review_policy_fingerprint.strip()
    ):
        review_policy_fingerprint = ""
    provenance_hash = payload.get("provenance_hash")
    if not isinstance(provenance_hash, str) or not provenance_hash.strip():
        provenance_hash = ""
    return {
        "source_id": source_id,
        "asset_version": asset_version.strip(),
        "runtime_revision": runtime_revision,
        "metadata_sha256": metadata_sha256.strip(),
        "scope_fingerprint": scope_fingerprint.strip(),
        "review_policy_fingerprint": review_policy_fingerprint.strip(),
        "provenance_hash": provenance_hash.strip(),
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

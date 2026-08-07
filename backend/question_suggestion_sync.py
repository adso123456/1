"""E-3：推荐问题派生资产同步任务。

职责：
  - 任务登记（身份冻结，幂等）
  - 任务领取与状态机（pending/running/succeeded/failed/superseded）
  - 正式 SQL Tool Memory 回读（provenance 白名单 + Chroma + SQLGuard）
  - 调用推荐问题生成管线并原子发布
  - 启动对账与手工重试

错误信息只保留类型与安全摘要，绝不包含凭据或完整堆栈。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from backend.data_source_asset_provenance import provenance_fingerprint
from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError
from backend.question_suggestion_assets import (
    asset_path,
    build_question_directory,
    commit_question_candidate,
    load_question_directory,
    load_question_directory_file,
    question_suggestions_root,
    validate_question_directory_payload,
    write_question_candidate,
)


GENERATOR_NAME = "question_suggestion_sync"
LOGGER = logging.getLogger("question_suggestion_sync")


def _sanitize_message(message: str) -> str:
    value = str(message)
    value = re.sub(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)=[^\s&,;]+",
        r"\1=***",
        value,
    )
    value = re.sub(r"(?i)://[^/\s:@]+:[^/\s:@]+@", "://***:***@", value)
    value = " ".join(value.split())
    return value[:200]


def _safe_error(exc: BaseException) -> str:
    message = _sanitize_message(str(exc))
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close_memory(memory: Any) -> None:
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    client = getattr(memory, "_client", None)
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def formal_identity(
    catalog: DataSourceCatalog,
    source_id: str,
) -> dict[str, Any] | None:
    """从正式资产冻结身份快照；任一文件缺失/损坏/自相矛盾返回 None。"""
    from backend.data_source_catalog import selected_scope_fingerprint

    record = catalog.require(source_id)
    root = Path(record.metadata_path).resolve().parent
    manifest_path = root / "asset_manifest.json"
    provenance_path = root / "asset_provenance.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(manifest, dict) or not isinstance(provenance, dict):
        return None
    try:
        scope_fingerprint = selected_scope_fingerprint(record.selected_scope)
        policy_fingerprint = catalog.review_policy(source_id)["fingerprint"]
        actual_provenance_hash = provenance_fingerprint(provenance)
    except Exception:
        return None
    try:
        actual_metadata_sha256 = _file_sha256(Path(record.metadata_path))
    except Exception:
        return None
    manifest_metadata_hash = str(manifest.get("metadata_hash") or "")
    manifest_provenance_hash = str(manifest.get("provenance_hash") or "")
    if (
        actual_metadata_sha256 != manifest_metadata_hash
        or actual_provenance_hash != manifest_provenance_hash
        or int(manifest.get("runtime_revision") or -1)
        != record.runtime_revision
        or manifest.get("scope_fingerprint") != scope_fingerprint
        or manifest.get("review_policy_fingerprint") != policy_fingerprint
    ):
        return None
    return {
        "source_id": source_id,
        "runtime_revision": record.runtime_revision,
        "metadata_sha256": manifest_metadata_hash,
        "scope_fingerprint": scope_fingerprint,
        "review_policy_fingerprint": policy_fingerprint,
        "provenance_hash": manifest_provenance_hash,
    }


def _identity_matches(
    job: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> bool:
    return (
        int(identity["runtime_revision"])
        == int(job["target_runtime_revision"])
        and identity["metadata_sha256"]
        == job["target_metadata_sha256"]
        and identity["scope_fingerprint"]
        == job["target_scope_fingerprint"]
        and identity["review_policy_fingerprint"]
        == job["target_review_policy_fingerprint"]
        and identity["provenance_hash"] == job["target_provenance_hash"]
    )


def enqueue_for_published_source(
    catalog: DataSourceCatalog,
    source_id: str,
) -> dict[str, Any]:
    """发布成功后登记推荐问题同步任务（幂等）。"""
    identity = formal_identity(catalog, source_id)
    if identity is None:
        raise DataSourceCatalogError(
            "正式资产身份不完整，无法登记推荐问题同步任务"
        )
    return catalog.enqueue_question_suggestion_job(**identity)


def _needs_sync(
    catalog: DataSourceCatalog,
    source_id: str,
    identity: Mapping[str, Any],
    asset_root: Path,
) -> bool:
    directory = load_question_directory(source_id, root=asset_root)
    if directory is None:
        return True
    return (
        directory.get("runtime_revision") != identity["runtime_revision"]
        or directory.get("metadata_sha256") != identity["metadata_sha256"]
        or directory.get("scope_fingerprint")
        != identity["scope_fingerprint"]
        or directory.get("review_policy_fingerprint")
        != identity["review_policy_fingerprint"]
        or directory.get("provenance_hash") != identity["provenance_hash"]
    )


def reconcile_question_suggestion_jobs(
    catalog: DataSourceCatalog,
    *,
    asset_root: Path | None = None,
) -> int:
    """启动对账：重置遗留 running、supersede 旧 revision、补登记缺失任务。"""
    resolved_root = (
        Path(asset_root).resolve()
        if asset_root is not None
        else question_suggestions_root().resolve()
    )
    created = 0
    catalog.reset_stale_question_suggestion_jobs()
    for record in catalog.list():
        if record.status != "ready" or not record.enabled_for_chat:
            continue
        source_id = record.source_id
        for job in catalog.list_question_suggestion_jobs(source_id):
            if (
                job["status"] != "superseded"
                and job["target_runtime_revision"] < record.runtime_revision
            ):
                catalog.supersede_question_suggestion_job(
                    job["job_id"],
                    reason="superseded by newer revision",
                )
        identity = formal_identity(catalog, source_id)
        if identity is None:
            continue
        if _needs_sync(catalog, source_id, identity, resolved_root):
            try:
                enqueue_for_published_source(catalog, source_id)
                created += 1
            except Exception as exc:
                LOGGER.error(
                    "推荐问题同步登记失败 source_id=%s error=%s",
                    source_id,
                    _safe_error(exc),
                )
    return created


def retry_question_suggestions(
    catalog: DataSourceCatalog,
    source_id: str,
    *,
    asset_root: Path | None = None,
) -> dict[str, Any]:
    """手工重试：只登记当前 revision，不接受任意旧 revision，不同步生成。"""
    identity = formal_identity(catalog, source_id)
    if identity is None:
        raise DataSourceCatalogError(
            "正式资产身份不完整，无法重试推荐问题同步"
        )
    return catalog.enqueue_question_suggestion_job(**identity)


def _build_identity_guard(record: Any, metadata_path: Path) -> Any:
    if record.database_type == "mysql":
        from backend.mysql_sql_guard import MySQLSQLGuard

        return MySQLSQLGuard(
            index_path=str(metadata_path),
            database_type="mysql",
            default_schema=record.database_name,
        )
    from backend.sql_guard import SQLGuard

    return SQLGuard(
        index_path=str(metadata_path),
        database_type="postgresql",
        default_schema=record.schema_name or "public",
    )


def _read_formal_sql_tool_memory(
    catalog: DataSourceCatalog,
    source_id: str,
    *,
    memory_factory: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """按 provenance 白名单回读正式 SQL Tool Memory 并做完整身份校验。"""
    record = catalog.require(source_id)
    root = Path(record.metadata_path).resolve().parent
    try:
        provenance = json.loads(
            (root / "asset_provenance.json").read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise DataSourceCatalogError(
            "正式 asset_provenance.json 不可读"
        ) from exc
    whitelist = provenance.get("assets", {}).get("sql_tool_memory") or []
    if not whitelist:
        return []
    if memory_factory is None:
        from backend.memory import create_memory

        memory_factory = create_memory
    memory = memory_factory(record.memory_path)
    try:
        collection = memory._get_collection()
        result = collection.get(
            ids=[str(item.get("record_id") or "") for item in whitelist],
            include=["documents", "metadatas"],
        )
    finally:
        _close_memory(memory)
    readback: dict[str, tuple[str, dict[str, Any]]] = {}
    for record_id, document, metadata in zip(
        result.get("ids") or [],
        result.get("documents") or [],
        result.get("metadatas") or [],
    ):
        readback[str(record_id)] = (
            str(document),
            dict(metadata or {}),
        )
    guard = _build_identity_guard(record, Path(record.metadata_path))
    samples: list[dict[str, Any]] = []
    for item in whitelist:
        record_id = str(item.get("record_id") or "")
        if record_id not in readback:
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory 缺失：{record_id}"
            )
        document, metadata = readback[record_id]
        if str(metadata.get("category") or "") != "sql_example":
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory category 不匹配：{record_id}"
            )
        if str(metadata.get("tool_name") or "") != "run_sql":
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory tool_name 不匹配：{record_id}"
            )
        if str(metadata.get("source_id") or "") != source_id:
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory source_id 不匹配：{record_id}"
            )
        if str(metadata.get("content_fingerprint") or "") != str(
            item.get("content_fingerprint") or ""
        ):
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory fingerprint 不匹配：{record_id}"
            )
        try:
            args = json.loads(str(metadata.get("args_json") or "{}"))
        except (TypeError, ValueError):
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory args_json 不可解析：{record_id}"
            ) from None
        sql = str((args or {}).get("sql") or "")
        if not sql.strip():
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory 缺少非空 sql：{record_id}"
            )
        result = guard.validate(sql, query="")
        if not result.passed:
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory 未通过 SQLGuard：{record_id}"
            )
        if (
            result.unknown_tables
            or result.unknown_columns
            or result.wildcard_references
            or result.ambiguous_columns
            or result.unresolved_lineage
        ):
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory 身份不完整：{record_id}"
            )
        actual_tables = sorted(
            [list(key) for key in result.used_physical_tables]
        )
        provenance_tables = sorted(
            [list(key) for key in (item.get("table_keys") or [])]
        )
        if actual_tables != provenance_tables:
            raise DataSourceCatalogError(
                f"正式 SQL Tool Memory 表身份与 provenance 不一致：{record_id}"
            )
        question = str(document or "").strip()
        if not question:
            question = str(metadata.get("question") or "").strip()
        samples.append(
            {
                "sample_id": record_id,
                "question": question,
                "sql": sql,
                "expected_behavior": str(
                    metadata.get("question") or ""
                ).strip(),
                "tables": [key[1] for key in provenance_tables],
            }
        )
    return samples


def run_question_suggestion_job(
    catalog: DataSourceCatalog,
    job: Mapping[str, Any],
    *,
    asset_root: Path | None = None,
    memory_factory: Callable[..., Any] | None = None,
    no_db_verify: bool = False,
    verifier: Any | None = None,
    max_questions: int = 100,
) -> tuple[str, str]:
    """执行单个任务（调用方已 claim 为 running）。返回 (status, safe_error)。"""
    from tools.generate_question_suggestions import (
        ReadOnlySqlVerifier,
        _build_candidates,
        _load_published_tables,
        _utc_now_iso,
    )

    resolved_root = (
        Path(asset_root).resolve()
        if asset_root is not None
        else question_suggestions_root().resolve()
    )
    source_id = str(job["source_id"])
    job_id = str(job["job_id"])
    try:
        identity = formal_identity(catalog, source_id)
        if identity is None or not _identity_matches(job, identity):
            catalog.supersede_question_suggestion_job(
                job_id,
                reason="identity mismatch before generation",
            )
            return "superseded", ""
        samples = _read_formal_sql_tool_memory(
            catalog,
            source_id,
            memory_factory=memory_factory,
        )
        record = catalog.require(source_id)
        metadata_path = Path(record.metadata_path)
        metadata_tables = _load_published_tables(metadata_path)
        guard = _build_identity_guard(record, metadata_path)

        own_verifier = verifier
        if samples and not no_db_verify and own_verifier is None:
            own_verifier = ReadOnlySqlVerifier(catalog, source_id)
            own_verifier.connect()
        try:
            candidates, disabled_reasons = _build_candidates(
                samples=samples,
                metadata_tables=metadata_tables,
                guard=guard,
                verifier=own_verifier,
                max_questions=max_questions,
                source_id=source_id,
            )
        finally:
            if own_verifier is not None and own_verifier is not verifier:
                own_verifier.close()
        enabled = [
            entry for entry in candidates if entry.get("enabled") is True
        ]
        directory = build_question_directory(
            source_id,
            enabled,
            asset_version="v1",
            runtime_revision=identity["runtime_revision"],
            metadata_sha256=identity["metadata_sha256"],
            scope_fingerprint=identity["scope_fingerprint"],
            review_policy_fingerprint=identity[
                "review_policy_fingerprint"
            ],
            provenance_hash=identity["provenance_hash"],
            generated_at=_utc_now_iso(),
            generator=GENERATOR_NAME,
            basis={
                "source": "formal_sql_tool_memory",
                "metadata_sha256": identity["metadata_sha256"],
                "sql_tool_record_count": len(samples),
                "enabled_question_count": len(enabled),
                "db_verification": (
                    "verified" if own_verifier is not None else "skipped"
                ),
                "disabled_reasons": dict(disabled_reasons),
            },
        )
        candidate = write_question_candidate(
            directory,
            root=resolved_root,
            candidate_name=job_id,
        )
        try:
            payload = load_question_directory_file(candidate, source_id)
            if payload is None:
                raise DataSourceCatalogError("候选推荐问题资产回读失败")
            validate_question_directory_payload(
                payload,
                source_id,
                runtime_revision=identity["runtime_revision"],
                metadata_sha256=identity["metadata_sha256"],
                scope_fingerprint=identity["scope_fingerprint"],
                review_policy_fingerprint=identity[
                    "review_policy_fingerprint"
                ],
                provenance_hash=identity["provenance_hash"],
            )
            # 写入前二次身份重检（防止生成期间发布新 revision）
            identity_now = formal_identity(catalog, source_id)
            if identity_now is None or not _identity_matches(
                job,
                identity_now,
            ):
                catalog.supersede_question_suggestion_job(
                    job_id,
                    reason="identity changed before commit",
                )
                return "superseded", ""
            commit_question_candidate(
                candidate,
                source_id=source_id,
                root=resolved_root,
            )
            formal = load_question_directory(source_id, root=resolved_root)
            if formal is None or formal.get("runtime_revision") != int(
                identity["runtime_revision"]
            ):
                raise DataSourceCatalogError(
                    "正式推荐问题资产回读校验失败"
                )
        except Exception:
            try:
                candidate.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        catalog.finish_question_suggestion_job(
            job_id,
            status="succeeded",
        )
        return "succeeded", ""
    except Exception as exc:
        safe = _safe_error(exc)
        catalog.finish_question_suggestion_job(
            job_id,
            status="failed",
            error=safe,
        )
        return "failed", safe


def process_pending_question_suggestion_jobs(
    catalog: DataSourceCatalog,
    *,
    asset_root: Path | None = None,
    source_id: str | None = None,
    limit: int = 1,
    memory_factory: Callable[..., Any] | None = None,
    no_db_verify: bool = False,
    verifier: Any | None = None,
    max_questions: int = 100,
) -> list[str]:
    """领取并执行待处理任务；claim 原子性保证并发 worker 不重复执行。"""
    jobs = catalog.list_question_suggestion_jobs(
        source_id=source_id,
        status="pending",
    )
    jobs.sort(key=lambda item: item["created_at"])
    processed: list[str] = []
    for job in jobs[: max(1, int(limit))]:
        claimed = catalog.claim_question_suggestion_job(job["job_id"])
        if claimed is None:
            continue
        run_question_suggestion_job(
            catalog,
            claimed,
            asset_root=asset_root,
            memory_factory=memory_factory,
            no_db_verify=no_db_verify,
            verifier=verifier,
            max_questions=max_questions,
        )
        processed.append(job["job_id"])
    return processed

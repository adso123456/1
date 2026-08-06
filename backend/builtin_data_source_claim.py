"""两个内置副本资产向远程本尊的增量认领服务。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError
from backend.data_source_claim_identity import (
    BUILTIN_CLAIM_SOURCE_IDS,
    build_schema_diff,
    inherit_compatible_semantics,
    schema_fingerprint,
)
from backend.data_source_connectors import DataSourceAssetPreparer, DirectDatabaseConnector
from backend.data_source_profiler import DataSourceProfiler
from backend.data_source_semantics import DataSourceSemanticAnalyzer
from backend.data_source_sql_memory import VerifiedSQLMemoryGenerator
from config.settings import resolve_project_path


class BuiltinDataSourceClaimService:
    """认领预览不覆盖正式 Metadata、Memory 或 Runtime。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        connector: DirectDatabaseConnector,
        profiler: DataSourceProfiler,
        semantic_analyzer: DataSourceSemanticAnalyzer,
        preparer: DataSourceAssetPreparer,
        sql_memory_generator: VerifiedSQLMemoryGenerator,
    ) -> None:
        self.catalog = catalog
        self.connector = connector
        self.profiler = profiler
        self.semantic_analyzer = semantic_analyzer
        self.preparer = preparer
        self.sql_memory_generator = sql_memory_generator

    @staticmethod
    def _require_builtin_source(source_id: str) -> None:
        if source_id not in BUILTIN_CLAIM_SOURCE_IDS:
            raise DataSourceCatalogError("只有两个内置副本数据源支持增量认领")

    def preview(
        self,
        source_id: str,
        *,
        progress=None,
    ) -> dict[str, Any]:
        self._require_builtin_source(source_id)
        record = self.catalog.require(source_id)
        claim = self.catalog.builtin_claim_summary(source_id)
        if claim is None or claim["status"] == "not_required":
            raise DataSourceCatalogError("当前连接仍是本地副本，不需要认领")
        self.catalog.mark_builtin_claim_running(source_id)
        try:
            self.connector.test_connection(source_id)
            remote_metadata = self.connector.discover(source_id, persist=False)
            if not remote_metadata:
                raise DataSourceCatalogError("远程本尊没有发现可认领的表和字段")
            profiles = self.profiler.profile(
                source_id,
                remote_metadata,
                progress=progress,
            )
            try:
                baseline_payload = json.loads(
                    record.metadata_path.read_text(encoding="utf-8")
                )
            except (OSError, TypeError, ValueError) as exc:
                raise DataSourceCatalogError("副本正式 Metadata 无法读取") from exc
            baseline_metadata = [
                dict(item)
                for item in baseline_payload
                if isinstance(item, Mapping)
            ]
            default_schema = (
                record.schema_name
                or record.database_name
                if record.database_type == "mysql"
                else record.schema_name or "public"
            )
            diff = build_schema_diff(
                baseline_metadata,
                remote_metadata,
                default_schema=default_schema,
            )
            analyzed, semantic_result = self.semantic_analyzer.analyze(
                remote_metadata,
                profiles,
                display_name=record.display_name,
                description=record.description,
            )
            candidate = inherit_compatible_semantics(
                baseline_metadata,
                analyzed,
                diff,
                default_schema=default_schema,
            )
            sql_records, sql_validation = self._revalidate_existing_sql(
                source_id,
                candidate,
            )
            generated_sql = self.sql_memory_generator.generate(
                source_id,
                candidate,
                profiles,
                persist=False,
            )
            merged_sql = {item["record_id"]: item for item in sql_records}
            merged_sql.update(
                {item["record_id"]: item for item in generated_sql}
            )
            self.catalog.replace_verified_sql_memories(
                source_id,
                merged_sql.values(),
            )
            diff = {
                **diff,
                "semantic_result": semantic_result,
                "profiled_table_count": sum(
                    not item.get("error") for item in profiles
                ),
                "profile_error_tables": [
                    {
                        "schema": item.get("schema", ""),
                        "table": item.get("table", ""),
                        "error": item.get("error", ""),
                    }
                    for item in profiles
                    if item.get("error")
                ],
                "sql_memory_validation": {
                    **sql_validation,
                    "generated_count": len(generated_sql),
                    "published_candidate_count": len(merged_sql),
                },
            }
            return self.catalog.save_builtin_claim_preview(
                source_id,
                remote_schema_fingerprint=schema_fingerprint(remote_metadata),
                diff=diff,
                candidate_metadata=candidate,
            )
        except Exception as exc:
            safe_error = (
                str(exc)
                if isinstance(exc, DataSourceCatalogError)
                else f"认领预览失败：{type(exc).__name__}"
            )
            self.catalog.mark_builtin_claim_failed(source_id, safe_error)
            raise

    def _revalidate_existing_sql(
        self,
        source_id: str,
        candidate_metadata: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        record = self.catalog.require(source_id)
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        if record.memory_path.exists():
            from backend.memory import create_memory

            memory = create_memory(record.memory_path)
            try:
                result = memory._get_collection().get(
                    where={"category": "sql_example"},
                    include=["documents", "metadatas"],
                )
                ids = list(result.get("ids") or [])
                documents = list(result.get("documents") or [])
                metadatas = [dict(item or {}) for item in result.get("metadatas") or []]
            finally:
                if not DataSourceAssetPreparer._close_memory(memory):
                    raise DataSourceCatalogError("旧正式 Memory 资源释放失败")
        if not ids and source_id == "mysql-lzh-monitor":
            from training.mysql_lzh_monitor_training import build_tool_records

            frozen, validation = build_tool_records()
            if not validation.get("valid"):
                raise DataSourceCatalogError("冻结 SQL Tool Memory 校验失败")
            ids = [item.record_id for item in frozen]
            documents = [item.document for item in frozen]
            metadatas = [dict(item.metadata) for item in frozen]
        if not (len(ids) == len(documents) == len(metadatas)):
            raise DataSourceCatalogError("旧 SQL Tool Memory 结构不完整")

        work_root = resolve_project_path(
            os.getenv("TRAINING_WORK_ROOT", "runtime/training-work")
        ) / source_id
        work_root.mkdir(parents=True, exist_ok=True)
        index_path = work_root / "claim_candidate_metadata.json"
        index_path.write_text(
            json.dumps(candidate_metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if record.database_type == "mysql":
            from backend.mysql_sql_guard import MySQLSQLGuard

            guard = MySQLSQLGuard(index_path=index_path)
        else:
            from backend.sql_guard import SQLGuard

            guard = SQLGuard(index_path=index_path)

        connection = self.connector._connect(source_id)
        passed: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        try:
            cursor = connection.cursor()
            try:
                if record.database_type == "mysql":
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                else:
                    cursor.execute("BEGIN READ ONLY")
                for record_id, document, raw_metadata in zip(
                    ids,
                    documents,
                    metadatas,
                    strict=True,
                ):
                    metadata = dict(raw_metadata)
                    try:
                        args = json.loads(str(metadata["args_json"]))
                        sql = str(args["sql"])
                    except (KeyError, TypeError, ValueError):
                        rejected.append(
                            {"record_id": str(record_id), "reason": "参数不可解析"}
                        )
                        continue
                    guard_result = guard.validate(
                        sql=sql,
                        query=str(metadata.get("question") or document),
                    )
                    if not guard_result.passed or guard_result.severity != "ok":
                        rejected.append(
                            {
                                "record_id": str(record_id),
                                "reason": "SQLGuard：" + guard_result.reason,
                            }
                        )
                        continue
                    try:
                        cursor.execute("SAVEPOINT water_agent_claim_sql")
                        cursor.execute(sql)
                        cursor.fetchmany(5)
                        cursor.execute("RELEASE SAVEPOINT water_agent_claim_sql")
                    except Exception as exc:
                        try:
                            cursor.execute("ROLLBACK TO SAVEPOINT water_agent_claim_sql")
                            cursor.execute("RELEASE SAVEPOINT water_agent_claim_sql")
                        except Exception:
                            pass
                        rejected.append(
                            {
                                "record_id": str(record_id),
                                "reason": f"远程只读执行：{type(exc).__name__}",
                            }
                        )
                        continue
                    compatibility: dict[str, Any] = {}
                    try:
                        raw_compatibility = json.loads(
                            str(metadata.get("metadata_json") or "{}")
                        )
                        if isinstance(raw_compatibility, Mapping):
                            compatibility = dict(raw_compatibility)
                    except (TypeError, ValueError):
                        pass
                    compatibility.update(
                        {
                            "validation_origin": "remote_claim_read_only_execution",
                            "remote_claim_validated": True,
                        }
                    )
                    metadata.update(
                        {
                            "source_id": source_id,
                            "validation_origin": "remote_claim_read_only_execution",
                            "remote_claim_validated": True,
                            "metadata_json": json.dumps(
                                compatibility,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        }
                    )
                    passed.append(
                        {
                            "record_id": str(record_id),
                            "question": str(metadata.get("question") or document),
                            "sql": sql,
                            "metadata": metadata,
                        }
                    )
                connection.rollback()
            finally:
                cursor.close()
        finally:
            connection.close()
        return passed, {
            "existing_count": len(ids),
            "revalidated_count": len(passed),
            "rejected_count": len(rejected),
            "rejected": rejected[:100],
        }

    def publish(self, source_id: str) -> dict[str, Any]:
        """用认领候选完整重建资产；不再从旧 Chroma 盲目复制 SQL。"""
        self._require_builtin_source(source_id)
        try:
            candidate = self.catalog.builtin_claim_candidate_metadata(source_id)
            profiles = self.catalog.list_table_profiles(source_id)
            usable_tables = {
                (str(item.get("schema") or ""), str(item.get("table") or ""))
                for item in profiles
                if not item.get("error")
            }
            scope = [
                item
                for item in candidate
                if (
                    str(item.get("schema") or ""),
                    str(item.get("table") or ""),
                )
                in usable_tables
            ]
            if not scope:
                raise DataSourceCatalogError("没有远程业务表通过受限只读画像")
            self.catalog.save_discovery(source_id, candidate)
            self.catalog.save_scope(source_id, scope)
            result = self.preparer.prepare(
                source_id,
                preserve_existing_sql=False,
            )
            claim = self.catalog.mark_builtin_claimed(source_id)
            return {**result, "claim": claim}
        except Exception as exc:
            safe_error = (
                str(exc)
                if isinstance(exc, DataSourceCatalogError)
                else f"认领发布失败：{type(exc).__name__}"
            )
            self.catalog.mark_builtin_claim_failed(source_id, safe_error)
            raise

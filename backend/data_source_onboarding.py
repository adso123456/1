"""动态数据源自分析、自构建的后台任务编排。"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any

from backend.data_source_catalog import DataSourceCatalog, DataSourceCatalogError
from backend.data_source_connectors import DataSourceAssetPreparer, DirectDatabaseConnector
from backend.data_source_profiler import DataSourceProfiler
from backend.data_source_semantics import DataSourceSemanticAnalyzer
from backend.data_source_sql_memory import VerifiedSQLMemoryGenerator
from backend.data_source_table_reviewer import DataSourceTableReviewer
from backend.data_source_profiler import (
    _is_numeric_type,
    _is_profiled_type,
    _is_time_type,
)


logger = logging.getLogger(__name__)


def _job_summary(result: Any) -> str:
    """把任务结果压缩成一行日志摘要。"""
    if not isinstance(result, dict):
        return str(result)[:120]
    parts = [
        f"{key}={result[key]}"
        for key in (
            "table_count",
            "column_count",
            "profiled_table_count",
            "selected_column_count",
            "verified_sql_memory_count",
            "runtime_revision",
            "status",
            "published",
        )
        if key in result
    ]
    return ", ".join(parts) if parts else str(result)[:120]


def _is_queryable_column_type(data_type: Any) -> bool:
    """默认纳入标准的可问数列：排除 blob/text/json/geometry 等，
    保留数值、时间、短文本（char/varchar）列。"""
    lowered = str(data_type or "").lower()
    if not _is_profiled_type(lowered):
        return False
    return (
        _is_numeric_type(lowered)
        or _is_time_type(lowered)
        or "char" in lowered
    )


_ID_LIKE_NAMES = {"id", "row_id", "pk", "key"}


def _is_queryable_column(item: Mapping[str, Any]) -> bool:
    """可问数列判定：类型可问数（数值/时间/短文本）且不是纯 id 列。"""
    name = str(item.get("column") or "").strip().lower()
    if name in _ID_LIKE_NAMES:
        return False
    return _is_queryable_column_type(item.get("type"))


def _build_default_scope(
    enriched: list[dict[str, Any]],
    profiles: Iterable[Mapping[str, Any]],
    usable_tables: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """确定性默认纳入标准：画像通过 + 有数据 + 至少一个可问数列。

    返回 (纳入列, 自动排除摘要[表名(原因)])。仅用于"还没有用户范围"的场景，
    用户已选范围一律保留。
    """
    profile_map = {
        (str(item.get("schema") or ""), str(item.get("table") or "")): dict(item)
        for item in profiles
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        grouped[
            (str(item.get("schema") or ""), str(item.get("table") or ""))
        ].append(item)

    included: list[dict[str, Any]] = []
    excluded: list[str] = []
    for key, items in grouped.items():
        table = key[1]
        profile = profile_map.get(key, {})
        has_data = (
            int(profile.get("row_estimate") or 0) > 0
            or int(profile.get("sample_row_count") or 0) > 0
        )
        queryable = [item for item in items if _is_queryable_column(item)]
        if key in usable_tables and has_data and queryable:
            included.extend(items)
            continue
        reason = (
            "画像失败"
            if key not in usable_tables
            else "无数据"
            if not has_data
            else "无可问数列"
        )
        excluded.append(f"{table}({reason})")
    return included, excluded


class DataSourceOnboardingService:
    """把原先需要人工逐步执行的接入动作编排成后台任务。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        connector: DirectDatabaseConnector,
        profiler: DataSourceProfiler,
        preparer: DataSourceAssetPreparer,
        semantic_analyzer: DataSourceSemanticAnalyzer | None = None,
        sql_memory_generator: VerifiedSQLMemoryGenerator | None = None,
        claim_service: Any | None = None,
    ) -> None:
        self.catalog = catalog
        self.connector = connector
        self.profiler = profiler
        self.preparer = preparer
        self.semantic_analyzer = semantic_analyzer or DataSourceSemanticAnalyzer()
        self.sql_memory_generator = sql_memory_generator or VerifiedSQLMemoryGenerator(
            catalog,
            connector,
        )
        self.claim_service = claim_service
        workers = max(1, int(os.getenv("DATA_SOURCE_ONBOARDING_WORKERS", "1")))
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="data-source-onboarding",
        )
        self._lock = RLock()
        self._futures: dict[str, Future[Any]] = {}
        for job in self.catalog.recover_onboarding_jobs():
            self._submit_existing(job)

    def start(self, source_id: str, job_type: str) -> dict[str, Any]:
        job = self.catalog.create_onboarding_job(source_id, job_type)
        self._submit_existing(job)
        return job

    def _submit_existing(self, job: dict[str, Any]) -> None:
        future = self._executor.submit(self._run, job)
        with self._lock:
            self._futures[job["job_id"]] = future
        future.add_done_callback(lambda _: self._forget(job["job_id"]))

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _run(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        source_id = str(job["source_id"])
        job_type = str(job["job_type"])
        logger.info("数据源 %s 后台任务开始：%s", source_id, job_type)
        try:
            self.catalog.update_onboarding_job(
                job_id,
                status="running",
                phase="starting",
                message="任务已开始",
                error="",
            )
            if job["job_type"] == "analyze":
                result = self._analyze(job_id, source_id)
            elif job["job_type"] == "activate":
                result = self._activate(job_id, source_id)
            elif job["job_type"] == "review":
                result = self._review(job_id, source_id)
            elif job["job_type"] == "claim_preview":
                result = self._claim_preview(job_id, source_id)
            else:
                result = self._claim_publish(job_id, source_id)
            self.catalog.update_onboarding_job(
                job_id,
                status="succeeded",
                phase="completed",
                message="任务已完成",
                result=result,
                error="",
            )
            logger.info(
                "数据源 %s 后台任务完成：%s（%s）",
                source_id,
                job_type,
                _job_summary(result),
            )
        except Exception as exc:
            message = str(exc) if isinstance(exc, DataSourceCatalogError) else f"任务失败：{type(exc).__name__}"
            logger.error(
                "数据源 %s 后台任务失败：%s -> %s",
                source_id,
                job_type,
                message,
            )
            self.catalog.update_onboarding_job(
                job_id,
                status="failed",
                phase="failed",
                message="任务执行失败",
                error=message[:1000],
            )

    def _analyze(self, job_id: str, source_id: str) -> dict[str, Any]:
        logger.info("数据源 %s 正在测试只读连接", source_id)
        self.catalog.update_onboarding_job(
            job_id,
            phase="testing_connection",
            message="正在测试只读连接",
        )
        self.connector.test_connection(source_id)

        self.catalog.update_onboarding_job(
            job_id,
            phase="discovering_schema",
            message="正在读取表、字段、注释、主键和索引",
        )
        metadata = self.connector.discover(source_id)
        logger.info("数据源 %s 读取到 %d 张表，开始逐表画像", source_id, len(metadata))

        def report(current: int, total: int, table: str) -> None:
            self.catalog.update_onboarding_job(
                job_id,
                phase="profiling_data",
                current_count=current,
                total_count=total,
                message=f"正在分析 {current}/{total}：{table}",
            )
            step = max(1, total // 20)
            if current == 1 or current == total or current % step == 0:
                logger.info(
                    "数据源 %s 正在分析表 %d/%d：%s",
                    source_id,
                    current,
                    total,
                    table,
                )

        profiles = self.profiler.profile(source_id, metadata, progress=report)
        if not metadata:
            raise DataSourceCatalogError("未发现可用于问数的表和字段")

        self.catalog.update_onboarding_job(
            job_id,
            phase="analyzing_semantics",
            current_count=len(profiles),
            total_count=len(profiles),
            message="正在生成并校验业务语义候选",
        )
        logger.info("数据源 %s 画像完成（%d 张表），生成业务语义候选", source_id, len(profiles))
        record = self.catalog.require(source_id)

        def semantic_progress(batch_no: int, total_batches: int) -> None:
            self.catalog.update_onboarding_job(
                job_id,
                phase="analyzing_semantics",
                current_count=batch_no,
                total_count=total_batches,
                message=f"正在生成并校验业务语义候选（批次 {batch_no}/{total_batches}）",
            )
            logger.info(
                "数据源 %s 语义分析批次 %d/%d",
                source_id,
                batch_no,
                total_batches,
            )

        enriched, semantic_result = self.semantic_analyzer.analyze(
            metadata,
            profiles,
            display_name=record.display_name,
            description=record.description,
            progress=semantic_progress,
        )
        self.catalog.save_discovery(source_id, enriched)

        self.catalog.update_onboarding_job(
            job_id,
            phase="validating_sql_memories",
            current_count=len(profiles),
            total_count=len(profiles),
            message="正在生成、校验并只读执行 SQL 样例",
        )
        logger.info("数据源 %s 正在生成、校验并只读执行 SQL 样例", source_id)
        sql_memories = self.sql_memory_generator.generate(
            source_id,
            enriched,
            profiles,
        )

        usable_tables = {
            (str(item.get("schema") or ""), str(item.get("table") or ""))
            for item in profiles
            if not item.get("error")
        }
        existing_ids = {
            (
                str(item.get("schema") or ""),
                str(item.get("table") or ""),
                str(item.get("column") or ""),
            )
            for item in record.selected_scope
        }
        if existing_ids:
            # 保留用户上次选定的问数范围：只对已有范围列做语义刷新，
            # 新发现的表不自动纳入（由用户后续决定是否启用）。
            default_scope = [
                item
                for item in enriched
                if (
                    str(item.get("schema") or ""),
                    str(item.get("table") or ""),
                    str(item.get("column") or ""),
                )
                in existing_ids
            ]
            logger.info(
                "数据源 %s 保留现有问数范围（%d 列），新表不自动纳入",
                source_id,
                len(default_scope),
            )
        else:
            default_scope, auto_excluded = _build_default_scope(
                enriched,
                profiles,
                usable_tables,
            )
            if auto_excluded:
                logger.info(
                    "数据源 %s 默认范围自动排除 %d 张表：%s",
                    source_id,
                    len(auto_excluded),
                    "、".join(auto_excluded[:20]),
                )
            logger.info(
                "数据源 %s 默认范围纳入 %d 列 / %d 张表（标准：有数据且有可问数列）",
                source_id,
                len(default_scope),
                len(
                    {
                        (str(i.get("schema") or ""), str(i.get("table") or ""))
                        for i in default_scope
                    }
                ),
            )
        if not default_scope:
            raise DataSourceCatalogError("没有任何业务表通过受限只读画像")

        self.catalog.update_onboarding_job(
            job_id,
            phase="selecting_scope",
            current_count=len(profiles),
            total_count=len(profiles),
            message="正在保存默认问数范围",
        )
        logger.info("数据源 %s 正在保存默认问数范围", source_id)
        self.catalog.save_scope(source_id, default_scope)
        return {
            "table_count": len(profiles),
            "column_count": len(enriched),
            "profiled_table_count": sum(not item.get("error") for item in profiles),
            "selected_column_count": len(default_scope),
            "verified_sql_memory_count": len(sql_memories),
            **semantic_result,
        }

    def _activate(self, job_id: str, source_id: str) -> dict[str, Any]:
        record = self.catalog.require(source_id)
        if record.status in {"ready", "disabled"}:
            enabled = self.catalog.set_enabled(source_id, True)
            logger.info("数据源 %s 直接启用（复用现有资产）", source_id)
            return {
                "runtime_revision": enabled.runtime_revision,
                "enabled_for_chat": enabled.enabled_for_chat,
                "reused_existing_assets": True,
            }
        if not self.catalog.list_table_profiles(source_id):
            raise DataSourceCatalogError("请先完成连接分析")
        if not record.selected_scope:
            raise DataSourceCatalogError("分析结果中没有可发布的问数范围")
        self.catalog.update_onboarding_job(
            job_id,
            phase="building_assets",
            message="正在生成 Metadata、DDL、业务文档和 Memory",
        )
        logger.info("数据源 %s 正在生成 Metadata、DDL、业务文档和 Memory", source_id)
        result = self.preparer.prepare(source_id)
        return {**result, "reused_existing_assets": False}

    def _review(self, job_id: str, source_id: str) -> dict[str, Any]:
        """阶段 A+B：只读重发现 + 画像 + 评分分组 -> reviews 建议字段，
        不修改 selected_scope、不生成正式资产。"""
        reviewer = DataSourceTableReviewer(
            self.catalog,
            self.connector,
            self.profiler,
        )
        total = 0
        current = 0

        def report(index: int, total_count: int, table: str) -> None:
            nonlocal total, current
            total = total_count
            current = index
            self.catalog.update_onboarding_job(
                job_id,
                phase="reviewing_tables",
                current_count=index,
                total_count=total_count,
                message=f"正在审核表 {index}/{total_count}：{table}",
            )
            step = max(1, total_count // 20)
            if index == 1 or index == total_count or index % step == 0:
                logger.info(
                    "数据源 %s 正在审核表 %d/%d：%s",
                    source_id,
                    index,
                    total_count,
                    table,
                )

        self.catalog.update_onboarding_job(
            job_id,
            phase="reviewing_tables",
            message="正在只读重发现全部表",
        )
        result = reviewer.run_review(source_id, progress=report)
        result["total_count"] = total
        result["current_count"] = current
        return result

    def _claim_preview(self, job_id: str, source_id: str) -> dict[str, Any]:
        if self.claim_service is None:
            raise DataSourceCatalogError("认领服务尚未配置")
        logger.info("数据源 %s 正在连接远程本尊并比较副本资产", source_id)
        self.catalog.update_onboarding_job(
            job_id,
            phase="claim_discovery",
            message="正在连接远程本尊并比较副本资产",
        )

        def report(current: int, total: int, table: str) -> None:
            self.catalog.update_onboarding_job(
                job_id,
                phase="claim_profiling",
                current_count=current,
                total_count=total,
                message=f"正在分析远程表 {current}/{total}：{table}",
            )
            step = max(1, total // 20)
            if current == 1 or current == total or current % step == 0:
                logger.info(
                    "数据源 %s 正在分析远程表 %d/%d：%s",
                    source_id,
                    current,
                    total,
                    table,
                )

        return self.claim_service.preview(
            source_id,
            progress=report,
        )

    def _claim_publish(self, job_id: str, source_id: str) -> dict[str, Any]:
        if self.claim_service is None:
            raise DataSourceCatalogError("认领服务尚未配置")
        logger.info("数据源 %s 正在构建并原子发布远程本尊资产", source_id)
        self.catalog.update_onboarding_job(
            job_id,
            phase="claim_publishing",
            message="正在构建并原子发布远程本尊资产",
        )
        return self.claim_service.publish(source_id)

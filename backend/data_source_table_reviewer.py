"""数据源问数资产准入审核器（阶段 A + 阶段 B + 正式审查修复）。

职责：只读重发现 + 受限画像 + 质量指标（阶段 A）
      + 确定性评分 + 同业务表分组 -> 建议字段（阶段 B）。

正式审查修复：
  - 首次启用时按 selected_scope 安全迁移（已有 review 记录则禁止重迁）；
  - run_id 使用纳秒时间戳 + uuid，避免 append-only 记录被吞；
  - reviews/missing/history/run 成功标记作为一个事务原子提交。

阶段 B 只写 proposed_decision / proposed_score / proposed_reason /
business_group / group_confidence / compared_tables_json / group_reason；
不修改 effective_decision，不覆盖 selected_scope，
不生成正式资产，不 bump runtime_revision。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_connectors import DirectDatabaseConnector
from backend.data_source_profiler import DataSourceProfiler
from backend.data_source_table_scorer import compute_proposals


logger = logging.getLogger(__name__)

REVIEW_VERSION = 2


def _quality_metrics(profile: Mapping[str, Any]) -> dict[str, Any]:
    """从画像提炼可持久化的质量指标，不保存原始样本值。"""
    quality = dict(profile.get("quality") or {})
    return {
        "row_estimate": quality.get("row_estimate"),
        "sample_row_count": quality.get("sample_row_count"),
        "column_count": quality.get("column_count"),
        "queryable_column_count": quality.get("queryable_column_count"),
        "has_primary_key": quality.get("has_primary_key"),
        "has_unique_key": quality.get("has_unique_key"),
        "primary_key_columns": quality.get("primary_key_columns", []),
        "sample_null_rate": quality.get("sample_null_rate"),
        "latest_data_at": quality.get("latest_data_at"),
        "time_coverage_days": quality.get("time_coverage_days"),
        "duplicate_key_ratio": quality.get("duplicate_key_ratio"),
        "observed_update_interval": quality.get("observed_update_interval"),
        "staleness_ratio": quality.get("staleness_ratio"),
        "freshness_confidence": quality.get("freshness_confidence"),
        "skipped_by_total_timeout": bool(
            quality.get("skipped_by_total_timeout")
        ),
        "structure_fingerprint": quality.get("structure_fingerprint", ""),
        "data_fingerprint": quality.get("data_fingerprint", ""),
        "table_comment": str(profile.get("table_comment") or ""),
        "object_type": str(profile.get("object_type") or "table"),
        "table_role_candidate": str(
            profile.get("table_role_candidate") or ""
        ),
        "grain_candidate": str(profile.get("grain_candidate") or ""),
        "time_column_candidate": str(
            profile.get("time_column_candidate") or ""
        ),
    }


def _column_comment_ratios(
    metadata: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """统计每张表带注释字段的比例，用于"字段注释与语义"评分。"""
    total: dict[tuple[str, str], int] = {}
    commented: dict[tuple[str, str], int] = {}
    for item in metadata:
        schema = str(item.get("schema") or "")
        table = str(item.get("table") or "")
        if not schema or not table:
            continue
        key = (schema, table)
        total[key] = total.get(key, 0) + 1
        if str(item.get("comment") or "").strip():
            commented[key] = commented.get(key, 0) + 1
    return {
        key: (commented.get(key, 0) / count) if count else 0.0
        for key, count in total.items()
    }


class DataSourceTableReviewer:
    """运行一轮表级准入审核：可用性 + 质量指标 + 建议字段。"""

    def __init__(
        self,
        catalog: DataSourceCatalog,
        connector: DirectDatabaseConnector,
        profiler: DataSourceProfiler,
    ) -> None:
        self.catalog = catalog
        self.connector = connector
        self.profiler = profiler

    def run_review(
        self,
        source_id: str,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        created_by: str = "review",
    ) -> dict[str, Any]:
        record = self.catalog.require(source_id)
        # 无碰撞身份：纳秒时间戳 + uuid，避免同一秒连续审核
        # 被 append-only 的 run/history 表静默吞掉。
        run_id = f"review-{source_id}-{time.time_ns()}-{uuid.uuid4().hex}"
        try:
            # 首次启用审核器时按 selected_scope 安全迁移；
            # 已有任意 review 记录则禁止重新迁移，避免覆盖人工决定。
            if not self.catalog.list_table_reviews(source_id):
                migration = self.catalog.migrate_table_reviews_from_existing(
                    source_id
                )
                logger.info(
                    "数据源 %s 首次启用审核器：安全迁移 %s",
                    source_id,
                    migration,
                )
            # persist=False：审核只读，不写入 discovered_metadata，不改变数据源状态。
            metadata = self.connector.discover(source_id, persist=False)
            discovered_keys = {
                (str(item.get("schema") or ""), str(item.get("table") or ""))
                for item in metadata
                if item.get("table")
            }
            self.catalog.record_review_run(
                run_id=run_id,
                source_id=source_id,
                review_version=REVIEW_VERSION,
                status="running",
                discovered_tables=len(discovered_keys),
                created_by=created_by,
            )
            profiles = self.profiler.profile(
                source_id,
                metadata,
                progress=progress,
            )
            existing = {
                (
                    str(review.get("schema_name") or ""),
                    str(review.get("table_name") or ""),
                ): review
                for review in self.catalog.list_table_reviews(source_id)
            }
            present_keys = {
                (str(profile.get("schema") or ""), str(profile.get("table") or ""))
                for profile in profiles
                if profile.get("schema") and profile.get("table")
            }
            # 阶段 A：在内存合并可用性与质量指标（不落库、不触碰 effective）。
            merged: dict[tuple[str, str], dict[str, Any]] = {}
            for profile in profiles:
                schema = str(profile.get("schema") or "")
                table = str(profile.get("table") or "")
                if not schema or not table:
                    continue
                key = (schema, table)
                review = existing.get(key) or {}
                next_version = max(
                    int(review.get("review_version") or 0),
                    REVIEW_VERSION,
                )
                legacy_classification = (
                    {
                        "effective_decision": "pending",
                        "decision_source": "migration",
                        "decision_reason": "legacy_unclassified",
                    }
                    if not review
                    else {}
                )
                merged[key] = {
                    "fields": {
                        **legacy_classification,
                        "availability_status": "present",
                        "quality_metrics_json": json.dumps(
                            _quality_metrics(profile),
                            ensure_ascii=False,
                        ),
                        "structure_fingerprint": (
                            profile.get("quality", {}).get(
                                "structure_fingerprint", ""
                            )
                            or ""
                        ),
                        "data_fingerprint": (
                            profile.get("quality", {}).get(
                                "data_fingerprint", ""
                            )
                            or ""
                        ),
                        "review_version": next_version,
                        "last_profiled_at": time.time(),
                        "reviewed_by": created_by,
                    },
                    "effective_decision": str(
                        review.get("effective_decision")
                        or legacy_classification.get("effective_decision")
                        or "pending"
                    ),
                }
            # 阶段 B：确定性评分 + 同业务表分组，只写建议字段。
            proposals = compute_proposals(
                profiles,
                _column_comment_ratios(metadata),
                existing,
            )
            for key, fields in proposals.items():
                if key in present_keys and key in merged:
                    merged[key]["fields"].update(fields)
            reviewed: list[dict[str, Any]] = []
            for key, merged_state in sorted(merged.items()):
                fields = merged_state["fields"]
                reviewed.append(
                    {
                        "source_id": source_id,
                        "schema_name": key[0],
                        "table_name": key[1],
                        "proposed_decision": (
                            fields.get("proposed_decision") or ""
                        ),
                        "proposed_score": fields.get("proposed_score"),
                        "proposed_reason": fields.get("proposed_reason") or "",
                        "effective_decision": merged_state["effective_decision"],
                        "availability_status": "present",
                        "quality_metrics_json": (
                            fields.get("quality_metrics_json") or "{}"
                        ),
                        "compared_tables_json": (
                            fields.get("compared_tables_json") or "[]"
                        ),
                        "business_group": fields.get("business_group") or "",
                    }
                )
            # 上次存在、本次未发现 -> missing（保留 effective_decision）
            missing: list[dict[str, Any]] = []
            missing_keys: list[tuple[str, str]] = []
            for key, review in existing.items():
                if key in discovered_keys:
                    continue
                if str(review.get("availability_status") or "") == "missing":
                    continue
                missing_keys.append(key)
                missing.append(
                    {
                        "source_id": source_id,
                        "schema_name": key[0],
                        "table_name": key[1],
                        "proposed_decision": (
                            review.get("proposed_decision") or ""
                        ),
                        "proposed_score": review.get("proposed_score"),
                        "effective_decision": (
                            review.get("effective_decision") or ""
                        ),
                        "availability_status": "missing",
                        "quality_metrics_json": "{}",
                        "compared_tables_json": "[]",
                    }
                )
            # reviews / missing / history / run 成功标记作为一个事务原子提交；
            # 任一失败整体回滚，只保留 run=failed 与错误信息。
            self.catalog.apply_review_results(
                source_id,
                run_id,
                review_updates=[
                    (key[0], key[1], state["fields"])
                    for key, state in sorted(merged.items())
                ],
                missing_keys=missing_keys,
                history_snapshots=reviewed + missing,
                profiled_tables=len(profiles),
            )
            decision_counts: dict[str, int] = {}
            for item in reviewed:
                decision = str(item.get("proposed_decision") or "")
                if decision:
                    decision_counts[decision] = (
                        decision_counts.get(decision, 0) + 1
                    )
            group_count = len(
                {
                    str(item.get("business_group") or "")
                    for item in reviewed
                    if item.get("business_group")
                }
            )
            logger.info(
                "数据源 %s 审核完成：发现 %d 张，画像 %d 张，标记 missing %d 张，"
                "建议分布 %s，业务组 %d 个",
                source_id,
                len(discovered_keys),
                len(profiles),
                len(missing),
                decision_counts,
                group_count,
            )
            return {
                "run_id": run_id,
                "discovered": len(discovered_keys),
                "profiled": len(profiles),
                "missing": len(missing),
                "proposed": decision_counts,
                "business_groups": group_count,
            }
        except Exception as exc:
            self.catalog.finish_review_run(
                run_id,
                status="failed",
                profiled_tables=0,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

"""数据源问数资产准入审核器（阶段 A + 阶段 B）。

职责：只读重发现 + 受限画像 + 质量指标（阶段 A）
      + 确定性评分 + 同业务表分组 -> 建议字段（阶段 B）。

阶段 B 只写 proposed_decision / proposed_score / proposed_reason /
business_group / group_confidence / compared_tables_json / group_reason；
不修改 effective_decision，不覆盖 selected_scope，
不生成正式资产，不 bump runtime_revision。
"""

from __future__ import annotations

import json
import logging
import time
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
        run_id = f"review-{source_id}-{int(time.time())}"
        try:
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
            # 阶段 A：更新可用性与质量指标（不触碰 effective_decision）。
            for profile in profiles:
                schema = str(profile.get("schema") or "")
                table = str(profile.get("table") or "")
                if not schema or not table:
                    continue
                review = self.catalog.get_table_review(source_id, schema, table)
                next_version = max(
                    int((review or {}).get("review_version") or 0),
                    REVIEW_VERSION,
                )
                legacy_classification = (
                    {
                        "effective_decision": "pending",
                        "decision_source": "migration",
                        "decision_reason": "legacy_unclassified",
                    }
                    if review is None
                    else {}
                )
                self.catalog.upsert_table_review(
                    source_id,
                    schema,
                    table,
                    **legacy_classification,
                    availability_status="present",
                    quality_metrics_json=json.dumps(
                        _quality_metrics(profile),
                        ensure_ascii=False,
                    ),
                    structure_fingerprint=(
                        profile.get("quality", {}).get(
                            "structure_fingerprint", ""
                        )
                        or ""
                    ),
                    data_fingerprint=(
                        profile.get("quality", {}).get("data_fingerprint", "")
                        or ""
                    ),
                    review_version=next_version,
                    last_profiled_at=time.time(),
                    reviewed_by=created_by,
                )
            existing = {
                (
                    str(review.get("schema_name") or ""),
                    str(review.get("table_name") or ""),
                ): review
                for review in self.catalog.list_table_reviews(source_id)
            }
            # 阶段 B：确定性评分 + 同业务表分组，只写建议字段。
            proposals = compute_proposals(
                profiles,
                _column_comment_ratios(metadata),
                existing,
            )
            present_keys = {
                (str(profile.get("schema") or ""), str(profile.get("table") or ""))
                for profile in profiles
                if profile.get("schema") and profile.get("table")
            }
            for key, fields in proposals.items():
                if key not in present_keys:
                    continue
                self.catalog.upsert_table_review(
                    source_id,
                    key[0],
                    key[1],
                    review_version=REVIEW_VERSION,
                    **fields,
                )
            discovered = discovered_keys
            reviewed: list[dict[str, Any]] = []
            for schema, table in sorted(present_keys):
                review = self.catalog.get_table_review(source_id, schema, table)
                reviewed.append(
                    {
                        "source_id": source_id,
                        "schema_name": schema,
                        "table_name": table,
                        "proposed_decision": (
                            (review or {}).get("proposed_decision") or ""
                        ),
                        "proposed_score": (review or {}).get("proposed_score"),
                        "proposed_reason": (
                            (review or {}).get("proposed_reason") or ""
                        ),
                        "effective_decision": (
                            (review or {}).get("effective_decision") or ""
                        ),
                        "availability_status": "present",
                        "quality_metrics_json": (
                            (review or {}).get("quality_metrics_json") or "{}"
                        ),
                        "compared_tables_json": (
                            (review or {}).get("compared_tables_json") or "[]"
                        ),
                        "business_group": (
                            (review or {}).get("business_group") or ""
                        ),
                    }
                )

            # 上次存在、本次未发现 -> missing（保留 effective_decision）
            existing = self.catalog.list_table_reviews(source_id)
            missing: list[dict[str, Any]] = []
            for review in existing:
                key = (
                    str(review.get("schema_name") or ""),
                    str(review.get("table_name") or ""),
                )
                if key in discovered:
                    continue
                if str(review.get("availability_status") or "") == "missing":
                    continue
                self.catalog.upsert_table_review(
                    source_id,
                    key[0],
                    key[1],
                    availability_status="missing",
                    reviewed_by=created_by,
                )
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

            self.catalog.append_review_history(run_id, reviewed + missing)
            self.catalog.finish_review_run(
                run_id,
                status="succeeded",
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
                len(discovered),
                len(profiles),
                len(missing),
                decision_counts,
                group_count,
            )
            return {
                "run_id": run_id,
                "discovered": len(discovered),
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

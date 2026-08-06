"""业务语义候选校验回归测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_semantics import DataSourceSemanticAnalyzer


def main() -> int:
    previous = os.environ.get("DATA_SOURCE_SEMANTIC_LLM_ENABLED")
    os.environ["DATA_SOURCE_SEMANTIC_LLM_ENABLED"] = "0"
    try:
        analyzer = DataSourceSemanticAnalyzer()
        metadata = [
            {
                "schema": "public",
                "table": "water_hourly",
                "column": "monitor_time",
                "type": "timestamp",
            },
            {
                "schema": "public",
                "table": "water_hourly",
                "column": "flow_rate",
                "type": "numeric",
            },
        ]
        profiles = [
            {
                "schema": "public",
                "table": "water_hourly",
                "table_comment": "水文小时监测",
                "table_role_candidate": "事实表",
                "grain_candidate": "station_id + monitor_time",
                "time_column_candidate": "monitor_time",
            }
        ]
        enriched, result = analyzer.analyze(
            metadata,
            profiles,
            display_name="水利数据",
            description="",
        )
        assert len(enriched) == 2
        assert all(item["domain"] == "水文小时监测" for item in enriched)
        assert all(item["time_column"] == "monitor_time" for item in enriched)
        assert all(item["logical_relations"] == [] for item in enriched)
        assert result["semantic_mode"] == "deterministic"

        semantics = {
            ("public", "water_hourly"): {
                "domain": "旧领域",
                "semantic_summary": "旧摘要",
                "grain": "旧粒度",
                "time_column": "monitor_time",
                "table_role": "业务表",
                "confidence": "deterministic",
            }
        }
        columns = {("public", "water_hourly"): {"monitor_time", "flow_rate"}}
        analyzer._apply_validated_candidates(
            [
                {
                    "schema": "public",
                    "table": "water_hourly",
                    "domain": "水文监测",
                    "time_column": "不存在字段",
                },
                {
                    "schema": "public",
                    "table": "LLM_虚构表",
                    "domain": "错误领域",
                },
            ],
            semantics,
            columns,
        )
        assert semantics[("public", "water_hourly")]["domain"] == "水文监测"
        assert semantics[("public", "water_hourly")]["time_column"] == "monitor_time"
        assert len(semantics) == 1
    finally:
        if previous is None:
            os.environ.pop("DATA_SOURCE_SEMANTIC_LLM_ENABLED", None)
        else:
            os.environ["DATA_SOURCE_SEMANTIC_LLM_ENABLED"] = previous
    print("data source semantics tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

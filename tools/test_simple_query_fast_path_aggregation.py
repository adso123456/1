from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.query_performance import QueryPerformanceState
from backend.query_intent import ContextProfile
from backend.simple_query_fast_path import build_fast_path_summary


def _state(sql: str, results: list[dict], row_count: int = 1) -> QueryPerformanceState:
    state = QueryPerformanceState(
        conversation_id="fast-path-regression",
        request_id="fast-path-regression",
        question="查询监测记录共有多少条？",
        source_id="mysql-lzh-monitor",
        context_profile=ContextProfile.SIMPLE_LOOKUP,
    )
    state.run_sql_count = 1
    state.dataframe_count = 1
    state.last_sql = sql
    state.last_result_metadata = {
        "query_type": "SELECT",
        "row_count": row_count,
        "columns": list(results[0]) if results else [],
        "results": results,
    }
    return state


def main() -> int:
    count_state = _state(
        "SELECT COUNT(*) AS total_count FROM rs_pollutant_hour_records",
        [{"total_count": 6047}],
    )
    count_summary, count_reason = build_fast_path_summary(count_state)

    aggregate_results = []
    for function_name in ("SUM", "AVG", "MAX", "MIN"):
        state = _state(
            f"SELECT {function_name}(value) AS aggregate_value FROM monitor_records",
            [{"aggregate_value": 12.5}],
        )
        summary, reason = build_fast_path_summary(state)
        aggregate_results.append(
            summary is None
            and reason == "aggregate_result_requires_final_llm"
            and not state.fast_path_used
        )

    ordinary_state = _state(
        "SELECT station_name FROM wm_station_info LIMIT 3",
        [
            {"station_name": "站点一"},
            {"station_name": "站点二"},
            {"station_name": "站点三"},
        ],
        row_count=3,
    )
    ordinary_summary, ordinary_reason = build_fast_path_summary(ordinary_state)

    ambiguous_state = _state(
        "SELECT COUNT(*) + SUM(value) AS value FROM monitor_records",
        [{"value": None}],
    )
    ambiguous_summary, ambiguous_reason = build_fast_path_summary(ambiguous_state)

    checks = [
        (
            "COUNT 单值聚合不得使用物理 row_count",
            count_summary is None
            and count_reason == "aggregate_result_requires_final_llm"
            and not count_state.fast_path_used,
        ),
        (
            "SUM AVG MAX MIN 全部回退最终 LLM",
            all(aggregate_results),
        ),
        (
            "普通非聚合 SELECT 保留原 fast path",
            ordinary_summary == "查询完成，共返回 3 条记录。"
            and ordinary_reason == "eligible"
            and ordinary_state.fast_path_used,
        ),
        (
            "无法安全解释的聚合结果不生成业务结论",
            ambiguous_summary is None
            and ambiguous_reason == "aggregate_result_requires_final_llm"
            and not ambiguous_state.fast_path_used,
        ),
    ]

    failed = [name for name, passed in checks if not passed]
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print(f"TOTAL={len(checks)} PASS={len(checks) - len(failed)} FAIL={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

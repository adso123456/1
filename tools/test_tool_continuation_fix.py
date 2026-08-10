import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.guarded_run_sql_tool import requires_follow_up_aggregation
from backend.run_sql_requirement import (
    close_tool_phase_after_success,
    get_run_sql_requirement,
    initialize_run_sql_requirement,
    record_default_database_query_requirement,
    record_successful_run_sql,
)


def main() -> int:
    question = "比较站点 96、97、98 最新有数据的一天的平均氨氮"
    first_sql = "SELECT station_id, monitor_time, an FROM records ORDER BY monitor_time DESC"
    final_sql = "SELECT station_id, AVG(an) AS avg_an FROM records GROUP BY station_id"

    assert requires_follow_up_aggregation(question, first_sql)
    assert not requires_follow_up_aggregation(question, final_sql)

    initialize_run_sql_requirement()
    record_default_database_query_requirement(question)
    closed = record_successful_run_sql(
        row_count=10,
        columns=["station_id", "monitor_time", "an"],
        close_tool_phase=False,
    )
    state = get_run_sql_requirement()
    assert not closed
    assert state is not None and not state.tool_phase_closed
    assert state.successful_run_sql_count == 1

    record_successful_run_sql(
        row_count=3,
        columns=["station_id", "avg_an"],
        close_tool_phase=False,
    )
    assert close_tool_phase_after_success("second_sufficient_query")
    assert state.tool_phase_closed
    assert state.successful_run_sql_count == 2

    initialize_run_sql_requirement()
    record_default_database_query_requirement("查询最新一条监测记录")
    assert record_successful_run_sql(
        row_count=1,
        columns=["station_id", "monitor_time"],
    )
    single_query_state = get_run_sql_requirement()
    assert single_query_state is not None and single_query_state.tool_phase_closed

    print("工具续查回归测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

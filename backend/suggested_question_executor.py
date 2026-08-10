"""推荐问题的服务端确定性执行。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype
from vanna.capabilities.sql_runner import RunSqlToolArgs

from backend.query_performance import record_sql_result
from backend.question_suggestion_assets import (
    infer_suggestion_output,
    normalize_suggested_question_text,
)


class SuggestedQuestionExecutionError(RuntimeError):
    """推荐问题包无效、SQL 被拦截或执行失败。"""


@dataclass(frozen=True)
class SuggestedQuestionExecution:
    sql: str
    columns: list[str]
    records: list[dict[str, Any]]
    text: str
    output_kind: str
    chart_spec: dict[str, Any] | None


def _chart_spec(
    question: str,
    dataframe: Any,
    requested_type: str | None,
) -> dict[str, Any] | None:
    columns = [str(column) for column in dataframe.columns]
    if len(columns) < 2:
        return None

    numeric = [column for column in columns if is_numeric_dtype(dataframe[column])]
    time_columns = [
        column
        for column in columns
        if is_datetime64_any_dtype(dataframe[column])
        or any(token in column.lower() for token in ("time", "date", "year", "month"))
    ]

    chart_type = requested_type if requested_type not in (None, "", "auto") else None
    if chart_type is None:
        if "趋势" in question and time_columns:
            chart_type = "line"
        elif "分布" in question or "占比" in question:
            chart_type = "donut"
        else:
            chart_type = "bar"

    x_field = time_columns[0] if chart_type in ("line", "area") and time_columns else columns[0]
    y_fields = [column for column in numeric if column != x_field]
    if not y_fields:
        return None
    if chart_type in ("pie", "donut"):
        y_fields = y_fields[:1]

    return {
        "type": chart_type,
        "title": question,
        "xField": x_field,
        "yFields": y_fields,
        "seriesField": None,
        "sizeField": None,
        "valueField": y_fields[0] if chart_type in ("pie", "donut") else None,
        "min": None,
        "max": None,
        "unit": None,
    }


async def execute_suggested_question(
    runtime: Any,
    package: dict[str, Any],
    question: str,
) -> SuggestedQuestionExecution:
    sql = str(package.get("related_sql") or "").strip()
    if not sql:
        raise SuggestedQuestionExecutionError("推荐问题缺少已验证 SQL")

    expected_question = str(package.get("text") or "").strip()
    if (
        normalize_suggested_question_text(question)
        != normalize_suggested_question_text(expected_question)
    ):
        raise SuggestedQuestionExecutionError("推荐问题文字与问题包不一致")

    related_tables = package.get("related_tables")
    candidate_tables = related_tables if isinstance(related_tables, list) else None
    guard_result = runtime.sql_guard.validate(
        sql=sql,
        query=question,
        deterministic_candidate_tables=candidate_tables,
    )
    if not guard_result.passed:
        record_sql_result(
            sql=sql,
            metadata={"blocked_by_sql_guard": True},
            guard_severity=guard_result.severity,
            success=False,
        )
        raise SuggestedQuestionExecutionError("推荐问题 SQL 未通过安全校验")

    try:
        dataframe = await runtime.runner.run_sql(RunSqlToolArgs(sql=sql), None)
    except Exception as exc:
        record_sql_result(
            sql=sql,
            metadata={"error_type": type(exc).__name__},
            guard_severity=guard_result.severity,
            success=False,
        )
        raise SuggestedQuestionExecutionError("推荐问题查询执行失败") from exc

    columns = [str(column) for column in dataframe.columns]
    records = json.loads(
        dataframe.to_json(orient="records", date_format="iso", force_ascii=False)
    )
    record_sql_result(
        sql=sql,
        metadata={
            "row_count": len(records),
            "columns": columns,
            "query_type": "SELECT",
            "results": records,
        },
        guard_severity=guard_result.severity,
        success=True,
    )

    inferred_kind, inferred_chart_type = infer_suggestion_output(question)
    output_kind = str(package.get("output_kind") or inferred_kind)
    if output_kind in ("daily_report", "monthly_report"):
        raise SuggestedQuestionExecutionError("报表推荐问题未进入报表处理链路")

    spec = None
    if output_kind == "chart" and records:
        spec = _chart_spec(
            question,
            dataframe,
            package.get("chart_type") or inferred_chart_type,
        )
        if spec is None:
            output_kind = "table"

    if not records:
        text = "查询执行成功，但当前数据条件下没有可展示的记录。"
    elif output_kind == "chart":
        text = f"已查询到 {len(records)} 条记录，并按推荐问题生成图表。"
    else:
        text = f"已查询到 {len(records)} 条记录，结果如下表。"

    if spec is not None:
        text += "\n\n<!-- chart_spec: " + json.dumps(spec, ensure_ascii=False) + " -->"
    else:
        text += "\n\n<!-- chart_type: none -->"

    return SuggestedQuestionExecution(
        sql=sql,
        columns=columns,
        records=records,
        text=text,
        output_kind=output_kind,
        chart_spec=spec,
    )

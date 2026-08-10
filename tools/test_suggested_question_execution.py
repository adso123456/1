from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.question_suggestion_assets import (
    build_question_directory,
    find_suggested_question,
    find_suggested_question_by_text,
    load_question_directory,
    select_suggested_questions,
    write_question_directory,
)
from backend.suggested_question_executor import (
    SuggestedQuestionExecutionError,
    execute_suggested_question,
)


class FakeGuard:
    def validate(self, **_kwargs):
        return SimpleNamespace(passed=True, severity="ok")


class FakeRunner:
    def __init__(self, dataframe: pd.DataFrame) -> None:
        self.dataframe = dataframe
        self.sql = ""

    async def run_sql(self, args, _context):
        self.sql = args.sql
        return self.dataframe


def executable_item(question_id: str, text: str, sql: str) -> dict:
    return {
        "id": question_id,
        "text": text,
        "enabled": True,
        "related_tables": ["records"],
        "related_sql": sql,
        "verification": {
            "verified": True,
            "row_count_sampled": 3,
            "columns": ["station_id", "avg_value"],
        },
        "output_kind": "chart",
        "chart_type": "bar",
    }


async def test_execution() -> None:
    question = "比较各站点平均值"
    sql = "SELECT station_id, AVG(value) AS avg_value FROM records GROUP BY station_id"
    runner = FakeRunner(pd.DataFrame({"station_id": [1, 2], "avg_value": [2.5, 3.5]}))
    runtime = SimpleNamespace(runner=runner, sql_guard=FakeGuard())
    result = await execute_suggested_question(
        runtime,
        executable_item("q1", question, sql),
        question,
    )
    assert runner.sql == sql
    assert result.output_kind == "chart"
    assert result.chart_spec is not None
    assert result.chart_spec["xField"] == "station_id"
    assert result.chart_spec["yFields"] == ["avg_value"]
    assert len(result.records) == 2

    punctuation_variant = await execute_suggested_question(
        runtime,
        executable_item("q1", question, sql),
        " 比较各站点，平均值。 ",
    )
    assert punctuation_variant.records == result.records

    try:
        await execute_suggested_question(
            runtime,
            executable_item("q1", question, sql),
            "被篡改的问题",
        )
    except SuggestedQuestionExecutionError:
        pass
    else:
        raise AssertionError("篡改问题文字必须拒绝执行")


def test_asset_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="suggestion-exec-") as temp:
        root = Path(temp)
        valid = executable_item("q1", "有效问题", "SELECT id, value FROM records")
        invalid = {
            "id": "q2",
            "text": "空结果问题",
            "enabled": True,
            "related_sql": "SELECT id FROM records",
            "verification": {"verified": True, "row_count_sampled": 0},
        }
        report = {
            "id": "q3",
            "text": "生成水质日报",
            "enabled": True,
            "output_kind": "daily_report",
            "report_request": {"report_type": "daily"},
            "verification": {"verified": True},
        }
        write_question_directory(
            build_question_directory(
                "source-a",
                [valid, invalid, report],
                runtime_revision=1,
            ),
            root=root,
        )
        directory = load_question_directory("source-a", root=root)
        assert directory is not None
        selected = select_suggested_questions(
            directory,
            "conversation-a",
            require_executable=True,
        )
        assert selected == [
            {"id": "q1", "text": "有效问题"},
            {"id": "q3", "text": "生成水质日报"},
        ]
        package = find_suggested_question(directory, "q1")
        assert package is not None and package["related_sql"] == valid["related_sql"]
        assert find_suggested_question(directory, "q2") is None
        matched = find_suggested_question_by_text(directory, " 有效，问题。 ")
        assert matched is not None and matched["id"] == "q1"
        assert find_suggested_question_by_text(directory, "另一个问题") is None

        decimal = executable_item("q5", "查询pH为7.5的记录", "SELECT id FROM records")
        decimal_directory = build_question_directory("source-a", [decimal])
        assert find_suggested_question_by_text(
            decimal_directory,
            "查询pH为75的记录",
        ) is None

        duplicate = executable_item("q4", "有效，问题", "SELECT id FROM records")
        ambiguous = build_question_directory("source-a", [valid, duplicate])
        assert find_suggested_question_by_text(ambiguous, "有效问题") is None


def main() -> int:
    test_asset_contract()
    asyncio.run(test_execution())
    print("推荐问题确定性执行回归测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

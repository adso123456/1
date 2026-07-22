"""保留空查询列结构的 Vanna 2.0.2 兼容实现。"""

from typing import Any, Dict, List, cast
import uuid

import pandas as pd

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.components import (
    ComponentType,
    DataFrameComponent,
    NotificationComponent,
    SimpleTextComponent,
    UiComponent,
)
from vanna.core.tool import ToolContext, ToolResult
from vanna.integrations.postgres import PostgresRunner
from vanna.tools import RunSqlTool


class SchemaPreservingPostgresRunner(PostgresRunner):
    """在 SELECT 返回零行时保留 cursor.description 中的列名。"""

    async def run_sql(
        self, args: RunSqlToolArgs, context: ToolContext
    ) -> pd.DataFrame:
        if self.connection_string:
            connection = self.psycopg2.connect(self.connection_string)
        else:
            connection = self.psycopg2.connect(**self.connection_params)

        cursor = connection.cursor(
            cursor_factory=self.psycopg2.extras.RealDictCursor
        )
        try:
            cursor.execute(args.sql)
            query_type = args.sql.strip().upper().split()[0]
            if query_type == "SELECT":
                description = cursor.description or []
                columns = [
                    item.name if hasattr(item, "name") else item[0]
                    for item in description
                ]
                rows = cursor.fetchall()
                if not rows:
                    return pd.DataFrame(columns=columns)
                return pd.DataFrame([dict(row) for row in rows])

            connection.commit()
            return pd.DataFrame({"rows_affected": [cursor.rowcount]})
        finally:
            cursor.close()
            connection.close()


class SchemaPreservingRunSqlTool(RunSqlTool):
    """保持 Vanna RunSqlTool 行为，并为零行结果输出列结构。"""

    async def execute(
        self, context: ToolContext, args: RunSqlToolArgs
    ) -> ToolResult:
        try:
            dataframe = await self.sql_runner.run_sql(args, context)
            query_type = args.sql.strip().upper().split()[0]

            if query_type == "SELECT":
                columns = dataframe.columns.tolist()
                if dataframe.empty:
                    result = "Query executed successfully. No rows returned."
                    dataframe_component = DataFrameComponent(
                        rows=[],
                        columns=columns,
                        title="Query Results",
                        description="No rows returned",
                    )
                    dataframe_component.data["sql"] = args.sql
                    dataframe_component.data["execution_success"] = True
                    ui_component = UiComponent(
                        rich_component=dataframe_component,
                        simple_component=SimpleTextComponent(text=result),
                    )
                    metadata = {
                        "row_count": 0,
                        "columns": columns,
                        "query_type": query_type,
                        "results": [],
                    }
                else:
                    results_data = dataframe.to_dict("records")
                    row_count = len(dataframe)
                    file_id = str(uuid.uuid4())[:8]
                    filename = f"query_results_{file_id}.csv"
                    csv_content = dataframe.to_csv(index=False)
                    await self.file_system.write_file(
                        filename, csv_content, context, overwrite=True
                    )

                    results_preview = csv_content
                    if len(results_preview) > 1000:
                        results_preview = (
                            results_preview[:1000]
                            + "\n(Results truncated to 1000 characters. FOR LARGE RESULTS YOU DO NOT NEED TO SUMMARIZE THESE RESULTS OR PROVIDE OBSERVATIONS. THE NEXT STEP SHOULD BE A VISUALIZE_DATA CALL)"
                        )
                    result = (
                        f"{results_preview}\n\nResults saved to file: {filename}"
                        f"\n\n**IMPORTANT: FOR VISUALIZE_DATA USE FILENAME: {filename}**"
                    )
                    dataframe_component = DataFrameComponent.from_records(
                        records=cast(List[Dict[str, Any]], results_data),
                        title="Query Results",
                        description=(
                            f"SQL query returned {row_count} rows with "
                            f"{len(columns)} columns"
                        ),
                    )
                    dataframe_component.data["sql"] = args.sql
                    dataframe_component.data["execution_success"] = True
                    dataframe_component.data["output_file"] = filename
                    ui_component = UiComponent(
                        rich_component=dataframe_component,
                        simple_component=SimpleTextComponent(text=result),
                    )
                    metadata = {
                        "row_count": row_count,
                        "columns": columns,
                        "query_type": query_type,
                        "results": results_data,
                        "output_file": filename,
                    }
            else:
                rows_affected = len(dataframe) if not dataframe.empty else 0
                result = (
                    f"Query executed successfully. {rows_affected} row(s) affected."
                )
                metadata = {
                    "rows_affected": rows_affected,
                    "query_type": query_type,
                }
                ui_component = UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION,
                        level="success",
                        message=result,
                    ),
                    simple_component=SimpleTextComponent(text=result),
                )

            return ToolResult(
                success=True,
                result_for_llm=result,
                ui_component=ui_component,
                metadata=metadata,
            )
        except Exception as error:
            error_message = f"Error executing query: {error}"
            return ToolResult(
                success=False,
                result_for_llm=error_message,
                ui_component=UiComponent(
                    rich_component=NotificationComponent(
                        type=ComponentType.NOTIFICATION,
                        level="error",
                        message=error_message,
                    ),
                    simple_component=SimpleTextComponent(text=error_message),
                ),
                error=str(error),
                metadata={"error_type": "sql_error"},
            )

"""MySQL 只读 Runner；每次查询都在只读事务中执行。"""

from __future__ import annotations

import pandas as pd

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.core.tool import ToolContext
from vanna.integrations.mysql import MySQLRunner


class ReadOnlyMySQLRunner(MySQLRunner):
    """保留零行列结构，并以数据库只读事务作为第二层保护。"""

    async def run_sql(
        self, args: RunSqlToolArgs, context: ToolContext
    ) -> pd.DataFrame:
        connection = self.pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            port=self.port,
            cursorclass=self.pymysql.cursors.DictCursor,
            autocommit=False,
            **self.kwargs,
        )
        try:
            connection.ping(reconnect=True)
            cursor = connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(args.sql)
                description = cursor.description or ()
                columns = [item[0] for item in description]
                rows = cursor.fetchall()
                return pd.DataFrame(rows, columns=columns)
            finally:
                connection.rollback()
                cursor.close()
        finally:
            connection.close()

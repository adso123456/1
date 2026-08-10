"""容器内运行时验证：中文检索 + 数据源建议（UTF-8 文件避免控制台编码污染）。"""

from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from backend.data_source_catalog import DataSourceCatalog
from backend.data_source_suggestion import DataSourceSuggestionService
from backend.memory import create_memory


async def main() -> None:
    pg = create_memory("/opt/water-agent/vanna_data")
    pg_examples = await pg.search_similar_usage(
        question="查询气象站的站点编码、站点名称、简称、行政区编码和所属城市，最多返回50条",
        context=SimpleNamespace(metadata={"stage": "verify"}),
        limit=5,
        tool_name_filter="run_sql",
    )
    pg_ddls = await pg.search_text_memories(
        query="气象站点的基础信息表",
        context=SimpleNamespace(metadata={"stage": "verify"}),
        limit=3,
    )
    pg._executor.shutdown(wait=True)
    print(
        "PG 示例检索:",
        len(pg_examples),
        "| 最高相似度:",
        round(pg_examples[0].similarity_score, 3) if pg_examples else 0,
    )
    print("PG DDL 检索:", len(pg_ddls))

    my = create_memory(
        "/opt/water-agent/agent_data/mysql-lzh-monitor/"
        "mysql-lzh-monitor.revision-3-2-1785398545932380700-73407a36"
    )
    my_examples = await my.search_similar_usage(
        question="查询幸福河水文站2025年6月11日的流速和流量趋势",
        context=SimpleNamespace(metadata={"stage": "verify"}),
        limit=5,
        tool_name_filter="run_sql",
    )
    my._executor.shutdown(wait=True)
    print(
        "MySQL 示例检索:",
        len(my_examples),
        "| 最高相似度:",
        round(my_examples[0].similarity_score, 3) if my_examples else 0,
    )

    catalog = DataSourceCatalog("/opt/water-agent/agent_data/data_sources/catalog.sqlite3")
    svc = DataSourceSuggestionService(catalog)
    suggestion = svc.suggest(
        "查询气象站的站点编码、站点名称、简称、行政区编码和所属城市，最多返回50条",
        "postgresql-main",
    )
    print("PG 会话建议结果:", suggestion)


if __name__ == "__main__":
    asyncio.run(main())

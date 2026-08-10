"""验证正式内存中文检索（用文件内 UTF-8 字符串，避免控制台编码污染）。"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from backend.memory import create_memory


async def check(memory_dir: str, label: str, question: str, tool: bool) -> None:
    memory = create_memory(memory_dir)
    try:
        if tool:
            results = await memory.search_similar_usage(
                question=question,
                context=SimpleNamespace(metadata={"stage": "verify"}),
                limit=5,
                tool_name_filter="run_sql",
            )
        else:
            results = await memory.search_text_memories(
                query=question,
                context=SimpleNamespace(metadata={"stage": "verify"}),
                limit=5,
            )
    finally:
        memory._executor.shutdown(wait=True)
    top = results[0] if results else None
    top_sim = round(top.similarity_score, 3) if top else 0
    top_question = (
        getattr(top.memory, "question", "") if tool and top else (
            getattr(top, "document", "") if top else ""
        )
    )
    print(f"{label}: 检索 {len(results)} 条 | 最高相似度 {top_sim} | {str(top_question)[:34]}")


async def main() -> None:
    await check(
        r"E:\3\posgresql\1\vanna_data",
        "PG 示例（气象站问题）",
        "查询气象站的站点编码、站点名称、简称、行政区编码和所属城市，最多返回50条",
        True,
    )
    await check(
        r"E:\3\posgresql\1\vanna_data",
        "PG DDL（气象站表）",
        "气象站点的站点编码、站点名称、简称和所属城市",
        False,
    )
    await check(
        r"E:\3\posgresql\1\agent_data\mysql-lzh-monitor\mysql-lzh-monitor.revision-3-2-1785398545932380700-73407a36",
        "MySQL 示例（水文站趋势）",
        "查询幸福河水文站2025年6月11日的流速和流量趋势",
        True,
    )


if __name__ == "__main__":
    asyncio.run(main())

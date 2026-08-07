"""阶段 E-3：Documentation 实际进入 Text Memory 检索链的验收测试。

复用 Vanna 真实检索入口 ChromaAgentMemory.search_text_memories
（where={"is_text_memory": True}），仅替换 collection 后端为内存假实现。

覆盖：
  - Documentation 记录能被 Text Memory 搜索到
  - DDL 记录不会被 Text Memory 搜索到
  - SQL Tool Memory 不会混入 Text Memory
  - 不同 source_id 的 Memory 互不串源
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory


def _doc_id(source_id: str, memory_type: str, document: str) -> str:
    digest = hashlib.sha256(
        f"{source_id}|{memory_type}|{document}".encode("utf-8")
    ).hexdigest()
    return f"b5-{digest}"


class _FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict]] = {}

    def add(self, *, ids, documents, metadatas) -> None:
        for record_id, document, metadata in zip(
            ids, documents, metadatas, strict=True
        ):
            self.records[str(record_id)] = (document, dict(metadata))

    def count(self) -> int:
        return len(self.records)

    def query(self, *, query_texts, n_results, where=None) -> dict:
        query = " ".join(query_texts or [""]).lower()
        matches = [
            (record_id, document, metadata)
            for record_id, (document, metadata) in self.records.items()
            if where is None
            or all(
                metadata.get(key) == value for key, value in where.items()
            )
        ]
        # 简单相似度：命中文本则距离 0，否则 1（验收只关心命中集合）。
        scored = []
        for record_id, document, metadata in matches:
            haystack = f"{document} {metadata}".lower()
            scored.append(
                (
                    0.0 if query in haystack else 1.0,
                    record_id,
                    document,
                    metadata,
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]))
        scored = scored[: max(1, int(n_results or 1))]
        return {
            "ids": [[item[1] for item in scored]],
            "distances": [[item[0] for item in scored]],
            "metadatas": [[item[3] for item in scored]],
        }


class _FakeMemory(ChromaAgentMemory):
    def __init__(self, collection: _FakeCollection) -> None:
        self._collection = collection
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _get_collection(self):
        return self._collection

    def close(self) -> None:
        self._executor.shutdown(wait=True)


def _build_memory(
    source_id: str,
    *,
    documentation: list[str] | None = None,
    ddls: list[str] | None = None,
    sql_records: list[tuple[str, str, dict]] | None = None,
) -> _FakeMemory:
    collection = _FakeCollection()
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for document in documentation or []:
        ids.append(_doc_id(source_id, "documentation", document))
        documents.append(document)
        metadatas.append(
            {
                "source_id": source_id,
                "memory_type": "documentation",
                "is_text_memory": True,
            }
        )
    for ddl in ddls or []:
        ids.append(_doc_id(source_id, "ddl", ddl))
        documents.append(ddl)
        metadatas.append(
            {
                "source_id": source_id,
                "memory_type": "ddl",
            }
        )
    for record_id, document, metadata in sql_records or []:
        ids.append(record_id)
        documents.append(document)
        metadatas.append(dict(metadata))
    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return _FakeMemory(collection)


async def _search(memory: _FakeMemory, query: str, limit: int = 10):
    return await memory.search_text_memories(
        query,
        None,
        limit=limit,
        similarity_threshold=0.0,
    )


def test_documentation_reachable_via_text_memory() -> None:
    async def run() -> None:
        document = "业务领域：水质监测。主要表：wm_waterquality_day_records。"
        ddl = 'CREATE TABLE "public"."wm_data" ("id" bigint);'
        memory = _build_memory(
            "source-a",
            documentation=[document],
            ddls=[ddl],
            sql_records=[
                (
                    "toolmem-1",
                    "SELECT * FROM wm_data",
                    {
                        "category": "sql_example",
                        "tool_name": "run_sql",
                    },
                )
            ],
        )
        try:
            results = await _search(memory, "水质监测 主要表")
            returned_ids = {item.memory.memory_id for item in results}
            assert _doc_id("source-a", "documentation", document) in returned_ids
            assert not any(
                item.memory.memory_id == _doc_id("source-a", "ddl", ddl)
                for item in results
            )
            assert not any(
                item.memory.memory_id == "toolmem-1" for item in results
            )
        finally:
            memory.close()

    asyncio.run(run())


def test_ddl_and_sql_tool_memory_not_returned() -> None:
    async def run() -> None:
        ddl = 'CREATE TABLE "public"."monitor" ("id" bigint);'
        sql_doc = "查询某站 pH 值"
        memory = _build_memory(
            "source-a",
            ddls=[ddl],
            sql_records=[
                (
                    "toolmem-1",
                    sql_doc,
                    {
                        "category": "sql_example",
                        "tool_name": "run_sql",
                    },
                )
            ],
        )
        try:
            results = await _search(memory, "CREATE TABLE monitor 表结构")
            assert not results
            results = await _search(memory, "pH 值 查询")
            assert not results
        finally:
            memory.close()

    asyncio.run(run())


def test_cross_source_memory_isolation() -> None:
    async def run() -> None:
        doc_a = "业务领域：A 源水质。主要表：table_a。"
        doc_b = "业务领域：B 源水文。主要表：table_b。"
        memory_a = _build_memory("source-a", documentation=[doc_a])
        memory_b = _build_memory("source-b", documentation=[doc_b])
        try:
            results_a = await _search(memory_a, "table_a 水质")
            results_b = await _search(memory_b, "table_b 水文")
            assert any(
                item.memory.memory_id
                == _doc_id("source-a", "documentation", doc_a)
                for item in results_a
            )
            assert any(
                item.memory.memory_id
                == _doc_id("source-b", "documentation", doc_b)
                for item in results_b
            )
            assert not any(
                item.memory.memory_id
                == _doc_id("source-b", "documentation", doc_b)
                for item in results_a
            )
        finally:
            memory_a.close()
            memory_b.close()

    asyncio.run(run())


def main() -> int:
    import traceback

    failed = 0
    total = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        total += 1
        try:
            func()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

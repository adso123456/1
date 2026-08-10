"""用当前中文 Embedding（BAAI/bge-small-zh-v1.5）重建正式 Chroma 内存。

存量向量与当前模型不一致，中文检索相似度被压到 0.5 以下，低于 0.55 阈值，
DDL / SQL 示例全部检索不到。直接对旧集合 delete+add 不会替换向量
（chroma 按持久化集合配置处理），因此这里在全新目录用 bge 重建集合，
原样复制 ids/documents/metadatas，校验计数与检索相似度后原子替换旧目录。

用法：python tools/reembed_memory_vectors.py <memory_dir> [<memory_dir> ...]
"""

from __future__ import annotations

import gc
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.memory import create_memory


def _close_memory(memory) -> None:
    try:
        memory._executor.shutdown(wait=True)
    except Exception:
        pass
    try:
        if memory._client is not None:
            memory._client._system.stop()
    except Exception:
        pass
    memory._collection = None
    memory._client = None
    gc.collect()
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def _read_all(collection) -> tuple[list[str], list[str], list[dict]]:
    data = collection.get(include=["documents", "metadatas"])
    ids = list(data["ids"] or [])
    documents = list(data["documents"] or [])
    metadatas = list(data["metadatas"] or [])
    if not (len(ids) == len(documents) == len(metadatas)):
        raise RuntimeError("集合数据不完整")
    return ids, documents, metadatas


def _spot_check(
    memory_dir: Path,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
) -> None:
    """用一条工具记录（success=True）的问题做检索，要求命中且相似度 >= 0.9。"""
    probe_index = next(
        (
            i
            for i, (document, metadata) in enumerate(zip(documents, metadatas))
            if document
            and len(document) > 3
            and metadata.get("success") is True
        ),
        None,
    )
    if probe_index is None:
        return
    question = documents[probe_index]
    expected_id = ids[probe_index]
    memory = create_memory(memory_dir)
    try:
        results = asyncio.run(
            memory.search_similar_usage(
                question=question,
                context=SimpleNamespace(metadata={"stage": "reembed-check"}),
                limit=3,
                similarity_threshold=0.0,
            )
        )
    finally:
        _close_memory(memory)
    if not results:
        raise RuntimeError(f"{memory_dir}: 重建后检索为空，拒绝替换")
    top = results[0]
    memory_id = getattr(top.memory, "memory_id", "")
    if memory_id != expected_id or top.similarity_score < 0.9:
        raise RuntimeError(
            f"{memory_dir}: 检索校验失败 id={memory_id!r} score={top.similarity_score:.3f}"
        )


def rebuild(memory_dir: Path) -> int:
    memory_dir = memory_dir.resolve()
    if not memory_dir.is_dir():
        raise RuntimeError(f"目录不存在：{memory_dir}")
    old = create_memory(memory_dir)
    try:
        collection = old._get_collection()
        count = collection.count()
        ids, documents, metadatas = _read_all(collection)
    finally:
        _close_memory(old)
    if count == 0:
        print(f"{memory_dir}: 空集合，跳过")
        return 0

    parent = memory_dir.parent
    candidate = Path(tempfile.mkdtemp(prefix="reembed-", dir=parent))
    try:
        fresh = create_memory(candidate)
        try:
            fresh_collection = fresh._get_collection()
            for offset in range(0, len(ids), 32):
                fresh_collection.add(
                    ids=ids[offset : offset + 32],
                    documents=documents[offset : offset + 32],
                    metadatas=metadatas[offset : offset + 32],
                )
            if fresh_collection.count() != count:
                raise RuntimeError("新集合计数不一致")
        finally:
            _close_memory(fresh)
        _spot_check(candidate, ids, documents, metadatas)

        # 内容级替换：兼容 memory_dir 本身是卷挂载点（挂载点不能 rename）。
        for entry in memory_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for entry in candidate.iterdir():
            shutil.move(str(entry), str(memory_dir / entry.name))
        shutil.rmtree(candidate, ignore_errors=True)
        print(f"{memory_dir}: 重建完成 {count} 条（bge 重嵌入 + 原子替换）")
        return count
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python tools/reembed_memory_vectors.py <memory_dir> [...]")
    for raw in sys.argv[1:]:
        rebuild(Path(raw))


if __name__ == "__main__":
    main()

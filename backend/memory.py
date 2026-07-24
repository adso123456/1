"""中文 Embedding 与 Chroma Memory 组装。"""

import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from chromadb.utils import embedding_functions
from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory

from config.settings import CHROMA_DIR


EMBEDDING_FUNCTION = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5"
)


class ChineseChromaAgentMemory(ChromaAgentMemory):
    """将文本 Memory 的默认相似度阈值从 0.7 调整为 0.55。"""

    async def search_text_memories(
        self, query, context, *, limit=10, similarity_threshold=0.55
    ):
        return await super().search_text_memories(
            query=query,
            context=context,
            limit=limit,
            similarity_threshold=similarity_threshold,
        )


def create_memory(persist_directory: str | Path | None = None):
    """创建共享的 ChromaAgentMemory 实例。"""
    return ChineseChromaAgentMemory(
        persist_directory=str(
            CHROMA_DIR if persist_directory is None else persist_directory
        ),
        embedding_function=EMBEDDING_FUNCTION,
    )

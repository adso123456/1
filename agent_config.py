"""
共享配置 — 存入端(train_step3.py)和检索端(agent)共用
确保 embedding function 和检索阈值绝对一致
"""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已缓存，强制离线避免网络波动
from chromadb.utils import embedding_functions
from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory

# 中文 embedding 模型
EMBEDDING_FUNCTION = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5"
)

# ChromaDB 路径；验证脚本可用环境变量隔离持久化写入，默认生产路径不变。
CHROMA_DIR = os.getenv("VANNA_DATA_DIR", "E:/3/posgresql/1/vanna_data")

# PostgreSQL 连接
DB_KWARGS = dict(
    host="localhost", port=5433, database="gt_monitor",
    user="postgres", password="test123456",
)


class ChineseChromaAgentMemory(ChromaAgentMemory):
    """
    薄封装：把 search_text_memories 的默认阈值从 0.7 降到 0.55
    DefaultLlmContextEnhancer 调用时不传 similarity_threshold,走这里的默认值
    """

    async def search_text_memories(self, query, context, *, limit=10, similarity_threshold=0.55):
        return await super().search_text_memories(
            query=query, context=context, limit=limit,
            similarity_threshold=similarity_threshold,
        )


def create_memory():
    """创建共享的 ChromaAgentMemory 实例"""
    return ChineseChromaAgentMemory(
        persist_directory=CHROMA_DIR,
        embedding_function=EMBEDDING_FUNCTION,
    )

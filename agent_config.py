"""
共享配置 — 存入端(train_step3.py)和检索端(agent)共用
确保 embedding function 和检索阈值绝对一致
"""
import os
from collections.abc import Mapping
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已缓存，强制离线避免网络波动
from chromadb.utils import embedding_functions
from vanna.integrations.chromadb.agent_memory import ChromaAgentMemory

# 中文 embedding 模型
EMBEDDING_FUNCTION = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5"
)

# ChromaDB 路径；验证脚本可用环境变量隔离持久化写入，默认生产路径不变。
CHROMA_DIR = os.getenv("VANNA_DATA_DIR", "E:/3/posgresql/1/vanna_data")

# PostgreSQL 客户端连接安全默认值（数据库角色权限仍需由 PostgreSQL 管理）
_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_DEFAULT_LOCK_TIMEOUT_MS = 5_000


def _positive_int(
    environ: Mapping[str, str], name: str, default: int | None = None
) -> int:
    raw_value = environ.get(name)
    if raw_value is None:
        if default is None:
            raise ValueError(f"缺少必需的环境变量 {name}")
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"环境变量 {name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"环境变量 {name} 必须是正整数")
    return value


def build_db_kwargs(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """从受控环境变量构造 PostgresRunner 连接参数。"""
    source = os.environ if environ is None else environ
    port = _positive_int(source, "DB_PORT", 5433)
    connect_timeout = _positive_int(
        source, "DB_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT
    )
    statement_timeout_ms = _positive_int(
        source, "DB_STATEMENT_TIMEOUT_MS", _DEFAULT_STATEMENT_TIMEOUT_MS
    )
    lock_timeout_ms = _positive_int(
        source, "DB_LOCK_TIMEOUT_MS", _DEFAULT_LOCK_TIMEOUT_MS
    )

    options = " ".join(
        (
            "-c default_transaction_read_only=on",
            f"-c statement_timeout={statement_timeout_ms}",
            f"-c lock_timeout={lock_timeout_ms}",
        )
    )
    kwargs: dict[str, Any] = {
        "host": source.get("DB_HOST", "localhost"),
        "port": port,
        "database": source.get("DB_NAME", "gt_monitor"),
        "user": source.get("DB_USER"),
        "password": source.get("DB_PASSWORD"),
        "connect_timeout": connect_timeout,
        "application_name": "vanna-water-agent",
        "options": options,
    }

    sslmode = source.get("DB_SSLMODE")
    if sslmode is not None and sslmode.strip():
        kwargs["sslmode"] = sslmode.strip()
    return kwargs


def validate_db_config(config: Mapping[str, Any] | None = None) -> None:
    """在创建真实 PostgresRunner 前校验必需配置，不输出敏感值。"""
    db_config = DB_KWARGS if config is None else config
    missing = [
        env_name
        for key, env_name in (("user", "DB_USER"), ("password", "DB_PASSWORD"))
        if not isinstance(db_config.get(key), str) or not db_config[key].strip()
    ]
    if missing:
        raise ValueError("缺少必需的数据库环境变量：" + ", ".join(missing))

    for key, env_name in (
        ("port", "DB_PORT"),
        ("connect_timeout", "DB_CONNECT_TIMEOUT"),
    ):
        value = db_config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"环境变量 {env_name} 必须是正整数")


# 保留公共接口，供现有运行和训练脚本导入。
DB_KWARGS = build_db_kwargs()


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

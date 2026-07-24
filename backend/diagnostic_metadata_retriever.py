"""带请求级诊断记录的确定性 Metadata Retriever。"""

from backend.metadata_retriever import DeterministicMetadataRetriever
from backend.request_diagnostics import write_trace_json


class DiagnosticMetadataRetriever(DeterministicMetadataRetriever):
    """记录实际 Metadata 检索，同时保持原有检索结果不变。"""

    def retrieve(self, question: str, top_n: int = 10):
        results = super().retrieve(question, top_n=top_n)
        write_trace_json(
            "metadata-retrieval.json",
            {
                "question": question,
                "top_n": top_n,
                "result_count": len(results),
                "results": results,
            },
        )
        return results

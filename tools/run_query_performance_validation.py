"""在调用方准备好的隔离 Catalog/资产副本上运行固定性能题组。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_source_chat_handler import DataSourceChatHandler
from backend.runtime_prewarm import RuntimePrewarmer
from step4_server import create_application_resources
from vanna.servers.base import ChatRequest


CASES = {
    "postgresql-main": (
        ("pg-simple-1", "列出夷陵区排污口名称，只取前5条"),
        ("pg-simple-2", "列出前5个排污口的名称和排污口类型"),
        ("pg-simple-3", "查询最近5条排污口监测记录"),
        ("pg-aggregate-1", "统计各区县排污口数量，并按数量降序排列"),
        ("pg-aggregate-2", "查询排污口数量最多的前5个区域"),
        ("pg-chart", "统计各区县排污口数量，并用柱状图展示"),
        ("pg-followup", "只显示刚才结果中数量最多的3个区域"),
    ),
    "mysql-lzh-monitor": (
        ("my-simple-1", "列出前5个水质监测断面名称"),
        ("my-simple-2", "列出前5个监测水体名称"),
        ("my-simple-3", "查询最近5条水质监测日记录"),
        ("my-aggregate-1", "统计各水质监测断面的监测记录数量并降序排列"),
        ("my-aggregate-2", "查询监测记录数量最多的前5个断面"),
        ("my-chart", "查询最近一段时间的pH监测趋势，并用折线图展示"),
        ("my-followup", "只显示刚才结果中的前3个断面"),
    ),
}


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def main() -> int:
    evidence_path = Path(
        os.environ["QUERY_PERF_EVIDENCE_PATH"]
    ).expanduser().resolve()
    if not evidence_path.is_absolute():
        raise RuntimeError("QUERY_PERF_EVIDENCE_PATH 必须是绝对路径")
    resources = create_application_resources()
    prewarmer = RuntimePrewarmer(resources.runtime_manager)
    warm_started = time.monotonic()
    prewarm = await prewarmer.warm_ready_sources()
    prewarm_ms = (time.monotonic() - warm_started) * 1000
    handler = DataSourceChatHandler(
        resources.coordinator,
        resources.runtime_manager,
        prewarmer.snapshot,
    )
    selected_case_ids = {
        item.strip()
        for item in os.environ.get("QUERY_PERF_CASE_IDS", "").split(",")
        if item.strip()
    }
    rows = []
    for source_id, cases in CASES.items():
        conversation_id = f"perf-{source_id}"
        for case_id, question in cases:
            if selected_case_ids and case_id not in selected_case_ids:
                continue
            request = ChatRequest(
                message=question,
                conversation_id=conversation_id,
                request_id=case_id,
                metadata={"source_id": source_id},
            )
            started = time.monotonic()
            first_event_ms = None
            first_text_ms = None
            event_types: list[str] = []
            dataframes: list[dict[str, object]] = []
            final_text = ""
            async for chunk in handler.handle_stream(request):
                elapsed = (time.monotonic() - started) * 1000
                if first_event_ms is None:
                    first_event_ms = elapsed
                event_type = str(chunk.rich.get("type") or "")
                event_types.append(event_type)
                if event_type in {"text", "text_delta"} and first_text_ms is None:
                    first_text_ms = elapsed
                if event_type == "dataframe":
                    dataframes.append(dict(chunk.rich.get("data") or {}))
                elif event_type == "text":
                    final_text = str(
                        (chunk.rich.get("data") or {}).get("content") or ""
                    )
            total_ms = (time.monotonic() - started) * 1000
            trace_root = Path(os.environ["VANNA_REQUEST_TRACE_DIR"])
            trace = next(
                (
                    directory
                    for directory in trace_root.iterdir()
                    if (directory / "query-performance.json").exists()
                    and json.loads(
                        (directory / "query-performance.json").read_text(
                            encoding="utf-8"
                        )
                    ).get("request_id")
                    == case_id
                ),
                None,
            )
            performance = (
                json.loads(
                    (trace / "query-performance.json").read_text(
                        encoding="utf-8"
                    )
                )
                if trace
                else {}
            )
            row = {
                "source_id": source_id,
                "case_id": case_id,
                "question": question,
                "first_event_ms": round(first_event_ms or 0, 3),
                "first_text_ms": round(first_text_ms or total_ms, 3),
                "total_ms": round(total_ms, 3),
                "event_types": event_types,
                "dataframe_count": len(dataframes),
                "dataframe_sha256": _sha(dataframes),
                "final_text_sha256": _sha(final_text),
                "performance": performance,
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(
            {
                "head": os.environ.get("QUERY_PERF_HEAD", ""),
                "prewarm_ms": round(prewarm_ms, 3),
                "prewarm": prewarm,
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

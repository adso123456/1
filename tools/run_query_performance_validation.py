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
        ("pg-simple-1", "列出夷陵区排污口名称，只取前5条", True),
        ("pg-simple-2", "列出前5个排污口的名称和排污口类型", True),
        ("pg-simple-3", "查询最近5条排污口监测记录", True),
        ("pg-aggregate-1", "统计各区县排污口数量，并按数量降序排列", True),
        ("pg-aggregate-2", "查询排污口数量最多的前5个区域", True),
        ("pg-chart", "统计各区县排污口数量，并用柱状图展示", True),
        ("pg-followup", "那再来5条", True),
        ("pg-explanation", "解释一下刚才结果", False),
    ),
    "mysql-lzh-monitor": (
        ("my-simple-1", "列出前5个水质监测断面名称", True),
        ("my-simple-2", "列出前5个监测水体名称", True),
        ("my-simple-3", "查询最近5条水质监测日记录", True),
        ("my-aggregate-1", "统计各水质监测断面的监测记录数量并降序排列", True),
        ("my-aggregate-2", "查询监测记录数量最多的前5个断面", True),
        ("my-chart", "查询最近一段时间的pH监测趋势，并用折线图展示", True),
        ("my-followup", "那再来5条", True),
        ("my-thanks", "谢谢", False),
    ),
}


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_case_contract(
    *,
    requires_fresh_sql: bool,
    expected_request_id: str,
    expected_source_id: str,
    performance: dict[str, object],
    observed_dataframe_count: int,
) -> str:
    """按测试用例显式合同验证，不复用生产分类逻辑。"""
    if not requires_fresh_sql:
        return ""
    successful_run_sql_count = int(
        performance.get("successful_run_sql_count") or 0
    )
    dataframe_count = int(performance.get("dataframe_count") or 0)
    successful_sql_present = performance.get("successful_sql_present") is True
    current_request_matches = (
        performance.get("request_id") == expected_request_id
        and performance.get("source_id") == expected_source_id
    )
    if (
        successful_run_sql_count < 1
        or dataframe_count < 1
        or observed_dataframe_count < 1
        or not successful_sql_present
        or not current_request_matches
    ):
        return (
            "显式查库用例未产生当前请求的新成功 SQL 和 DataFrame："
            f"successful_run_sql_count={successful_run_sql_count}, "
            f"dataframe_count={dataframe_count}, "
            f"observed_dataframe_count={observed_dataframe_count}, "
            f"successful_sql_present={successful_sql_present}, "
            f"current_request_matches={current_request_matches}"
        )
    return ""


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
    validation_errors: list[str] = []
    for source_id, cases in CASES.items():
        conversation_id = f"perf-{source_id}"
        for case_id, question, requires_fresh_sql in cases:
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
                    for directory in sorted(
                        trace_root.iterdir(),
                        key=lambda item: item.stat().st_mtime_ns,
                        reverse=True,
                    )
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
            validation_error = validate_case_contract(
                requires_fresh_sql=requires_fresh_sql,
                expected_request_id=case_id,
                expected_source_id=source_id,
                performance=performance,
                observed_dataframe_count=len(dataframes),
            )
            if validation_error:
                validation_errors.append(f"{case_id}: {validation_error}")
            row = {
                "source_id": source_id,
                "case_id": case_id,
                "question": question,
                "requires_fresh_sql": requires_fresh_sql,
                "first_event_ms": round(first_event_ms or 0, 3),
                "first_text_ms": round(first_text_ms or total_ms, 3),
                "total_ms": round(total_ms, 3),
                "event_types": event_types,
                "dataframe_count": len(dataframes),
                "dataframe_sha256": _sha(dataframes),
                "final_text_sha256": _sha(final_text),
                "performance": performance,
                "validation_error": validation_error,
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
                "validation_errors": validation_errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if validation_errors:
        for error in validation_errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

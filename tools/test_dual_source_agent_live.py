"""在 PostgreSQL 正式资产副本上验证双数据源 Agent 隔离。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_mysql_b3_regression import (
    _chart_fields,
    _get_json,
    _post_sse,
    _start_server,
    _stop_server,
)
from training.sop.storage_snapshot import build_directory_manifest


FORMAL_POSTGRESQL = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
QUESTION = (
    "按省市区县统计排污口总数和有整治记录的排污口数量，"
    "包含没有整治记录的排污口，并用横向柱状图展示，最多100个地区"
)


def main() -> int:
    if not FORMAL_POSTGRESQL.is_dir():
        raise RuntimeError("PostgreSQL 正式资产不存在")
    formal_before = build_directory_manifest(FORMAL_POSTGRESQL).content_sha256
    temp_root = Path(tempfile.mkdtemp(prefix="dual-source-agent-live-"))
    validation = temp_root / "postgresql-validation"
    agent_dir = temp_root / "agent-data"
    process = None
    try:
        shutil.copytree(FORMAL_POSTGRESQL, validation)
        agent_dir.mkdir()
        os.environ["VANNA_DATA_DIR"] = str(validation)
        os.environ["AGENT_DATA_DIR"] = str(agent_dir)
        process, _logs = _start_server()
        source_ids = {
            item["source_id"] for item in _get_json("/api/data-sources")
        }
        response = _post_sse(
            QUESTION,
            str(uuid.uuid4()),
            source_id="postgresql-main",
        )
        frames = response["dataframes"]
        frame = frames[-1] if frames else {}
        sql = str(frame.get("sql") or "")
        columns = list(frame.get("columns") or [])
        charts = response["chart_specs"]
        checks = {
            "data_sources": {
                "postgresql-main",
                "mysql-lzh-monitor",
            }.issubset(source_ids),
            "sql_executed": bool(frames)
            and all(item["execution_success"] for item in frames),
            "postgresql_tables": "rs_outlet_info_v2" in sql
            and "rs_outlet_remediation_v2" in sql,
            "mysql_example_not_used": "wm_station_info" not in sql
            and "wm_waterquality_day_records" not in sql,
            "result_nonempty": int(frame.get("row_count") or 0) > 0,
            "chart_type": any(
                item.get("type") == "horizontal_bar" for item in charts
            ),
            "chart_fields": all(
                field in columns for item in charts for field in _chart_fields(item)
            ),
            "formal_unchanged": (
                build_directory_manifest(FORMAL_POSTGRESQL).content_sha256
                == formal_before
            ),
        }
        for name, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'}: {name}")
        print(f"SQL={sql}")
        print(f"COLUMNS={columns}")
        print(f"ROWS={frame.get('row_count', 0)}")
        print(f"CHARTS={[item.get('type') for item in charts]}")
        failures = [name for name, passed in checks.items() if not passed]
        print(
            f"TOTAL={len(checks)} PASS={len(checks) - len(failures)} "
            f"FAIL={len(failures)}"
        )
        return 1 if failures else 0
    finally:
        _stop_server(process)
        for attempt in range(10):
            try:
                shutil.rmtree(temp_root, ignore_errors=False)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

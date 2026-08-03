from __future__ import annotations

import os
from pathlib import Path


def resolve_test_report_path(filename: str) -> Path:
    """返回本地测试报告路径，避免把运行产物写回源码目录。"""
    configured_dir = os.getenv("WATER_QA_TEST_REPORT_DIR", "").strip()
    if configured_dir:
        report_dir = Path(configured_dir).expanduser().resolve()
    else:
        report_dir = Path(__file__).resolve().parents[1] / ".local" / "test-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / filename

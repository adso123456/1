"""使用真实 MySQL 只读数据生成一份日报和月报验收件。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.water_quality_reports.pdf_renderer import (
    WaterQualityPdfRenderer,
    _runtime_root,
)
from backend.water_quality_reports.repository import ReportRepository
from backend.water_quality_reports.service import WaterQualityReportService
from config.data_sources import build_mysql_data_source_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2025-07-28")
    parser.add_argument("--month", default="2025-07")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_date = date.fromisoformat(args.date)
    report_month = datetime.strptime(args.month, "%Y-%m").date().replace(day=1)
    config = build_mysql_data_source_config()
    renderer = WaterQualityPdfRenderer()
    runtime_root = _runtime_root()
    runtime_root.mkdir(parents=True, exist_ok=True)

    daily_repository = ReportRepository(config)
    daily = WaterQualityReportService(daily_repository).daily(report_date)
    daily_pdf = renderer.render(daily)
    daily_json = runtime_root / f"daily_{report_date:%Y%m%d}.json"
    daily_json.write_text(
        json.dumps(daily, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monthly_repository = ReportRepository(config)
    monthly = WaterQualityReportService(monthly_repository).monthly(report_month)
    monthly_pdf = renderer.render(monthly)
    monthly_json = runtime_root / f"monthly_{report_month:%Y%m}.json"
    monthly_json.write_text(
        json.dumps(monthly, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {
        "source_id": config.source_id,
        "read_only": config.read_only,
        "daily": {
            "date": daily["report_date"],
            "pdf": str(daily_pdf),
            "json": str(daily_json),
            "monitoring": {
                key: daily["monitoring"][key]
                for key in (
                    "configured_station_count",
                    "enabled_station_count",
                    "valid_station_count",
                    "valid_transmission_numerator",
                    "valid_transmission_denominator",
                    "valid_transmission_rate",
                )
            },
            "valid_quality_station_count": daily["overall_quality"][
                "valid_station_count"
            ],
            "query_timings": daily_repository.query_timings,
        },
        "monthly": {
            "month": monthly["report_month"],
            "pdf": str(monthly_pdf),
            "json": str(monthly_json),
            "monitoring": {
                key: monthly["monitoring"][key]
                for key in (
                    "configured_station_count",
                    "enabled_station_count",
                    "valid_station_count",
                    "valid_transmission_numerator",
                    "valid_transmission_denominator",
                    "valid_transmission_rate",
                )
            },
            "query_timings": monthly_repository.query_timings,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""不可变水质报表快照及 PDF 工件管理。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from backend.water_quality_reports.pdf_renderer import (
    WaterQualityPdfRenderer,
    _runtime_root,
)
from backend.water_quality_reports.service import WaterQualityReportService


REPORT_ID_PATTERN = re.compile(r"wqr-[0-9a-f]{32}")
_ARTIFACT_LOCKS: dict[str, threading.Lock] = {}
_ARTIFACT_LOCKS_GUARD = threading.Lock()


class ReportArtifactError(RuntimeError):
    pass


class ReportArtifactNotFound(ReportArtifactError):
    pass


class ReportArtifactStore:
    def __init__(
        self,
        service_factory: Callable[[], WaterQualityReportService],
        renderer: WaterQualityPdfRenderer | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._renderer = renderer or WaterQualityPdfRenderer()
        self.root = _runtime_root()

    def options(self) -> dict[str, Any]:
        return self._service_factory().options()

    def generate(
        self,
        *,
        report_type: str,
        period: date,
        indicator_codes: tuple[int, ...] | None,
        frequency_overrides: dict[int, int],
        recent_days: int = 3,
    ) -> dict[str, Any]:
        service = self._service_factory()
        if report_type == "daily":
            report = service.daily(
                period,
                indicator_codes=indicator_codes,
                frequency_overrides=frequency_overrides,
                recent_days=recent_days,
            )
        elif report_type == "monthly":
            report = service.monthly(
                period.replace(day=1),
                indicator_codes=indicator_codes,
                frequency_overrides=frequency_overrides,
            )
        else:
            raise ValueError("报告类型无效")

        snapshot_bytes = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        report_id = f"wqr-{hashlib.sha256(snapshot_bytes).hexdigest()[:32]}"
        lock = self._lock_for(report_id)
        with lock:
            self.root.mkdir(parents=True, exist_ok=True)
            snapshot_path = self._path(report_id, ".json")
            pdf_path = self._path(report_id, ".pdf")
            if snapshot_path.exists():
                if snapshot_path.read_bytes() != snapshot_bytes:
                    raise ReportArtifactError("报表快照摘要冲突")
            else:
                self._atomic_write(snapshot_path, snapshot_bytes)
            if not pdf_path.exists():
                self._renderer.render(report, target_path=pdf_path)
        return self.result(report_id, report)

    def load(self, report_id: str) -> dict[str, Any]:
        path = self._path(report_id, ".json")
        if not path.is_file():
            raise ReportArtifactNotFound("报表快照不存在或已清理")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReportArtifactError("报表快照读取失败") from exc
        if not isinstance(payload, dict):
            raise ReportArtifactError("报表快照格式无效")
        return payload

    def pdf_path(self, report_id: str) -> Path:
        path = self._path(report_id, ".pdf")
        if not path.is_file():
            raise ReportArtifactNotFound("报表 PDF 不存在或已清理")
        return path

    @staticmethod
    def result(report_id: str, report: dict[str, Any]) -> dict[str, Any]:
        report_type = report["report_type"]
        period = report.get("report_date") or report.get("report_month")
        options = report["options"]
        return {
            "report_id": report_id,
            "report_type": report_type,
            "title": report["title"],
            "period": period,
            "indicators": options["indicator_codes"],
            "indicator_names": options.get("indicator_names", []),
            "recent_days": options.get("recent_days") if report_type == "daily" else None,
            "frequency_hours": options["frequency_hours"],
            "source_id": report["source_id"],
            "preview_url": (
                f"/api/reports/water-quality/artifacts/{report_id}/preview"
            ),
            "download_url": (
                f"/api/reports/water-quality/artifacts/{report_id}/pdf"
            ),
            "status": "报告已生成",
        }

    def _path(self, report_id: str, suffix: str) -> Path:
        if REPORT_ID_PATTERN.fullmatch(report_id) is None:
            raise ReportArtifactNotFound("report_id 无效")
        root = self.root.resolve()
        path = (root / f"{report_id}{suffix}").resolve()
        if path.parent != root:
            raise ReportArtifactNotFound("report_id 无效")
        return path

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _lock_for(report_id: str) -> threading.Lock:
        with _ARTIFACT_LOCKS_GUARD:
            return _ARTIFACT_LOCKS.setdefault(report_id, threading.Lock())

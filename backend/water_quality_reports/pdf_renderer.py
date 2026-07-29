"""基于 ReportLab 的 A4 中文水质报表渲染器。"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)


FONT_NAME = "WaterReportChinese"
_FONT_LOCK = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}


class PdfRenderError(RuntimeError):
    pass


def _runtime_root() -> Path:
    configured = os.getenv("WATER_REPORT_OUTPUT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[2]
    return (project_root.parent.parent / "_runtime" / "water-quality-reports").resolve()


def _register_font() -> str:
    with _FONT_LOCK:
        if FONT_NAME in pdfmetrics.getRegisteredFontNames():
            return FONT_NAME
        candidates = [
            os.getenv("WATER_REPORT_FONT_PATH", "").strip(),
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                pdfmetrics.registerFont(TTFont(FONT_NAME, candidate))
                return FONT_NAME
    raise PdfRenderError("未找到可用的中文字体")


class _InvariantCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)


class WaterQualityPdfRenderer:
    def render(
        self,
        report: dict[str, Any],
        *,
        target_path: Path | None = None,
    ) -> Path:
        font_name = _register_font()
        output_root = _runtime_root()
        output_root.mkdir(parents=True, exist_ok=True)
        if report["report_type"] == "daily":
            suffix = report["report_date"].replace("-", "")
            filename = f"梁子湖流域自动站水质日报_{suffix}.pdf"
        else:
            suffix = report["report_month"].replace("-", "")
            filename = f"梁子湖流域自动站水质月报_{suffix}.pdf"
        target = target_path.resolve() if target_path is not None else output_root / filename
        if target_path is not None and target.parent != output_root:
            raise PdfRenderError("PDF 输出路径无效")
        lock = _PATH_LOCKS.setdefault(target, threading.Lock())
        with lock:
            temporary = output_root / f".{filename}.{uuid.uuid4().hex}.tmp"
            try:
                self._build(temporary, report, font_name)
                os.replace(temporary, target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                if isinstance(exc, PdfRenderError):
                    raise
                raise PdfRenderError("PDF 生成失败") from exc
        return target

    def _build(
        self,
        path: Path,
        report: dict[str, Any],
        font_name: str,
    ) -> None:
        document = BaseDocTemplate(
            str(path),
            pagesize=A4,
            leftMargin=16 * mm,
            rightMargin=16 * mm,
            topMargin=17 * mm,
            bottomMargin=16 * mm,
            title=report["title"],
            author="梁子湖流域水质日报月报系统",
        )
        frame = Frame(
            document.leftMargin,
            document.bottomMargin,
            document.width,
            document.height,
            id="normal",
        )
        document.addPageTemplates(
            PageTemplate(
                id="water-report",
                frames=frame,
                onPage=lambda canv, doc: self._page_footer(
                    canv, doc, font_name
                ),
            )
        )
        styles = self._styles(font_name)
        story = [
            Paragraph(report["title"], styles["TitleCN"]),
            Paragraph(
                report.get("report_date") or report.get("report_month"),
                styles["SubtitleCN"],
            ),
            Spacer(1, 5 * mm),
        ]
        if report["report_type"] == "daily":
            story.extend(self._daily_story(report, styles))
        else:
            story.extend(self._monthly_story(report, styles))
        story.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph(
                    f"数据源：{report['source_id']}。本报告由确定性规则生成，"
                    "暂无数据或待配置项未作推测。",
                    styles["NoteCN"],
                ),
            ]
        )
        document.build(story, canvasmaker=_InvariantCanvas)

    @staticmethod
    def _page_footer(canv, doc, font_name: str) -> None:
        canv.saveState()
        canv.setFont(font_name, 8)
        canv.setFillColor(colors.HexColor("#666666"))
        canv.drawCentredString(A4[0] / 2, 8 * mm, f"第 {doc.page} 页")
        canv.restoreState()

    @staticmethod
    def _styles(font_name: str):
        styles = getSampleStyleSheet()
        styles.add(
            ParagraphStyle(
                "TitleCN",
                parent=styles["Title"],
                fontName=font_name,
                fontSize=18,
                leading=24,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#222222"),
                spaceAfter=2 * mm,
            )
        )
        styles.add(
            ParagraphStyle(
                "SubtitleCN",
                parent=styles["Normal"],
                fontName=font_name,
                fontSize=10,
                leading=14,
                alignment=TA_CENTER,
                textColor=colors.HexColor("#555555"),
            )
        )
        styles.add(
            ParagraphStyle(
                "HeadingCN",
                parent=styles["Heading2"],
                fontName=font_name,
                fontSize=12,
                leading=17,
                spaceBefore=3 * mm,
                spaceAfter=2 * mm,
            )
        )
        styles.add(
            ParagraphStyle(
                "BodyCN",
                parent=styles["BodyText"],
                fontName=font_name,
                fontSize=9.5,
                leading=16,
                firstLineIndent=19,
                spaceAfter=2 * mm,
            )
        )
        styles.add(
            ParagraphStyle(
                "TableTitleCN",
                parent=styles["Normal"],
                fontName=font_name,
                fontSize=9.5,
                leading=14,
                alignment=TA_CENTER,
                spaceBefore=2 * mm,
                spaceAfter=2 * mm,
            )
        )
        styles.add(
            ParagraphStyle(
                "CellCN",
                parent=styles["Normal"],
                fontName=font_name,
                fontSize=7.2,
                leading=10,
                alignment=TA_CENTER,
                wordWrap="CJK",
            )
        )
        styles.add(
            ParagraphStyle(
                "NoteCN",
                parent=styles["Normal"],
                fontName=font_name,
                fontSize=8,
                leading=12,
                textColor=colors.HexColor("#666666"),
            )
        )
        return styles

    def _table(
        self,
        headers: list[str],
        rows: list[list[object]],
        widths: list[float],
        styles,
    ) -> LongTable:
        def cell(value: object):
            if value is None:
                text = "待配置"
            elif isinstance(value, bool):
                text = "是" if value else "否"
            else:
                text = str(value)
            return Paragraph(text.replace("\n", "<br/>"), styles["CellCN"])

        data = [[cell(value) for value in headers]]
        data.extend([[cell(value) for value in row] for row in rows])
        table = LongTable(
            data,
            colWidths=widths,
            repeatRows=1,
            splitByRow=1,
            hAlign="CENTER",
        )
        table.setStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#666666")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
        return table

    def _daily_story(self, report: dict[str, Any], styles) -> list[object]:
        monitoring = report["monitoring"]
        overall = report["overall_quality"]
        lake = report["lake_area"]
        tributaries = report["tributaries"]
        recent = lambda items: "；".join(
            f"{item['date']}：{item['status']}" for item in items
        )
        condition_headers = [
            "监测点位名称",
            "120小时连续超目标",
            "连续7天超目标",
            "连续三天劣于Ⅳ类",
            "日均值为劣Ⅴ",
        ]
        condition_widths = [36 * mm, 34 * mm, 34 * mm, 37 * mm, 27 * mm]
        story: list[object] = [
            Paragraph("1. 监测情况", styles["HeadingCN"]),
            Paragraph(report["narratives"]["monitoring"], styles["BodyCN"]),
            Paragraph("表1 监测点位监测情况", styles["TableTitleCN"]),
            self._table(
                ["序号", "监测点位名称", "应测指标及频次", "今日监测情况",
                 "昨日监测情况",
                 f"近{report['options']['recent_days']}日监测情况", "备注"],
                [
                    [
                        row["index"],
                        row["station_name"],
                        row["expected_indicators"],
                        row["today_status"],
                        row["yesterday_status"],
                        recent(row["recent_three_days"]),
                        row["remark"],
                    ]
                    for row in monitoring["rows"]
                ],
                [8 * mm, 20 * mm, 33 * mm, 27 * mm, 27 * mm, 42 * mm, 15 * mm],
                styles,
            ),
            Paragraph("2. 流域水质总体情况", styles["HeadingCN"]),
            Paragraph(report["narratives"]["overall_quality"], styles["BodyCN"]),
            Paragraph("表2 监测点位水质情况", styles["TableTitleCN"]),
            self._table(
                ["序号", "监测点位", "今日水质类别", "昨日水质类别", "水质变化情况"],
                [
                    [
                        row["index"],
                        row["station_name"],
                        row["today_level"],
                        row["yesterday_level"],
                        row["change"],
                    ]
                    for row in overall["rows"]
                ],
                [12 * mm, 48 * mm, 36 * mm, 36 * mm, 40 * mm],
                styles,
            ),
            Paragraph("3. 湖区监测点位水质情况", styles["HeadingCN"]),
            Paragraph(
                f"湖区点位{lake['station_count']}个；过去120小时连续超水质目标"
                f"{lake['continuous_120h_over_target_count']}个，连续7天日均值超目标"
                f"{lake['continuous_7d_over_target_count']}个，连续三天日均值劣于Ⅳ类"
                f"{lake['continuous_3d_worse_iv_count']}个，当日日均值为劣Ⅴ类"
                f"{lake['daily_inferior_v_count']}个。降雨和水位暂无数据。",
                styles["BodyCN"],
            ),
            Paragraph("表3 湖区点位水质情况", styles["TableTitleCN"]),
            self._table(
                condition_headers,
                [
                    [
                        row["station_name"],
                        row["continuous_120h_over_target"],
                        row["continuous_7d_over_target"],
                        row["continuous_3d_worse_iv"],
                        row["daily_inferior_v"],
                    ]
                    for row in lake["rows"]
                ],
                condition_widths,
                styles,
            ),
            Paragraph("4. 支流水质情况", styles["HeadingCN"]),
            Paragraph(
                f"已由断面属性确认的支流点位{tributaries['station_count']}个；"
                f"水质类别较昨日下降两个及以上类别"
                f"{tributaries['declined_two_or_more_count']}个。水位和流量暂无数据。",
                styles["BodyCN"],
            ),
            Paragraph("表4 支流水质情况", styles["TableTitleCN"]),
            self._table(
                condition_headers,
                [
                    [
                        row["station_name"],
                        row["continuous_120h_over_target"],
                        row["continuous_7d_over_target"],
                        row["continuous_3d_worse_iv"],
                        row["daily_inferior_v"],
                    ]
                    for row in tributaries["rows"]
                ],
                condition_widths,
                styles,
            ),
            Paragraph("5. 超级站", styles["HeadingCN"]),
        ]
        super_rows = report["super_station"]["rows"]
        if super_rows:
            story.append(
                self._table(
                    ["监测点位", "当日综合水质类别", "有毒有害物质", "安全水平"],
                    [
                        [
                            row["station_name"],
                            row["water_quality_level"],
                            row["toxic_substances"],
                            row["safety_level"],
                        ]
                        for row in super_rows
                    ],
                    [48 * mm, 42 * mm, 42 * mm, 40 * mm],
                    styles,
                )
            )
        else:
            story.append(Paragraph("暂无数据。", styles["BodyCN"]))
        return story

    def _monthly_story(self, report: dict[str, Any], styles) -> list[object]:
        monitoring = report["monitoring"]
        conditions = report["station_conditions"]
        return [
            Paragraph("1. 监测情况", styles["HeadingCN"]),
            Paragraph(report["narratives"]["monitoring"], styles["BodyCN"]),
            Paragraph("表1 监测点位监测情况", styles["TableTitleCN"]),
            self._table(
                ["序号", "监测点位名称", "应测指标及频次", "缺测指标及时间段"],
                [
                    [
                        row["index"],
                        row["station_name"],
                        row["expected_indicators"],
                        row["missing_indicators_and_periods"],
                    ]
                    for row in monitoring["rows"]
                ],
                [10 * mm, 30 * mm, 54 * mm, 78 * mm],
                styles,
            ),
            PageBreak(),
            Paragraph("2. 各监测点位情况", styles["HeadingCN"]),
            Paragraph("表2 监测点位水质情况", styles["TableTitleCN"]),
            self._table(
                ["序号", "监测点位名称", "120小时连续超目标次数",
                 "连续7天超目标次数", "连续三天劣于Ⅳ类次数", "当日水质劣Ⅴ次数"],
                [
                    [
                        index,
                        row["station_name"],
                        row["continuous_120h_over_target_count"],
                        row["continuous_7d_over_target_count"],
                        row["continuous_3d_worse_iv_count"],
                        row["daily_inferior_v_count"],
                    ]
                    for index, row in enumerate(conditions["rows"], 1)
                ],
                [9 * mm, 35 * mm, 34 * mm, 31 * mm, 37 * mm, 26 * mm],
                styles,
            ),
        ]

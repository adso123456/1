"""水质报表在线预览 HTML 模板。"""

from __future__ import annotations

from html import escape
from typing import Any


def _value(value: object) -> str:
    if value is None:
        return "待配置"
    if isinstance(value, bool):
        return "是" if value else "否"
    return escape(str(value))


def _table(headers: list[str], rows: list[list[object]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{_value(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{escape(header)}</th>" for header in headers)
        + f"</tr></thead><tbody>{body}</tbody></table>"
    )


def render_report_html(report: dict[str, Any]) -> str:
    """生成不依赖脚本的确定性预览页面。"""
    if report["report_type"] == "daily":
        content = _daily_html(report)
        subtitle = report["report_date"]
    else:
        content = _monthly_html(report)
        subtitle = report["report_month"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(report["title"])}</title>
<style>
@page {{ size: A4 portrait; margin: 18mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #222; background: #eef2f6;
  font-family: "Microsoft YaHei","Noto Sans SC",sans-serif; line-height: 1.75; }}
main {{ width: min(210mm, calc(100% - 28px)); min-height: 297mm; margin: 18px auto;
  padding: 18mm 16mm; background: #fff; box-shadow: 0 8px 28px rgba(15,23,42,.12); }}
h1 {{ margin: 0; font-size: 23px; text-align: center; letter-spacing: 1px; }}
.subtitle {{ margin: 4px 0 20px; color: #555; text-align: center; }}
h2 {{ margin: 18px 0 7px; font-size: 17px; }}
p {{ margin: 7px 0; text-indent: 2em; }}
.table-title {{ margin: 12px 0 6px; font-weight: 600; text-align: center; text-indent: 0; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 12px; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; page-break-inside: avoid; }}
th,td {{ padding: 6px 5px; border: 1px solid #777; text-align: center;
  vertical-align: middle; overflow-wrap: anywhere; }}
th {{ background: #eee; font-weight: 600; }}
.note {{ color: #666; font-size: 12px; text-indent: 0; }}
@media print {{ body {{ background: #fff; }} main {{ width: auto; min-height: 0; margin: 0;
  padding: 0; box-shadow: none; }} }}
</style>
</head>
<body><main>
<h1>{escape(report["title"])}</h1>
<div class="subtitle">{escape(subtitle)}</div>
{content}
<p class="note">数据源：{escape(report["source_id"])}。本报告由确定性规则生成，暂无数据或待配置项未作推测。</p>
</main></body></html>"""


def _daily_html(report: dict[str, Any]) -> str:
    monitoring = report["monitoring"]
    overall = report["overall_quality"]
    lake = report["lake_area"]
    tributaries = report["tributaries"]
    recent = lambda items: "；".join(
        f"{item['date']}：{item['status']}" for item in items
    )
    condition_headers = [
        "监测点位名称",
        "是否120小时连续超水质目标",
        "是否连续7天均值超水质目标",
        "是否连续三天均值劣于Ⅳ类",
        "日均值是否为劣Ⅴ",
    ]
    condition_rows = lambda rows: [
        [
            item["station_name"],
            item["continuous_120h_over_target"],
            item["continuous_7d_over_target"],
            item["continuous_3d_worse_iv"],
            item["daily_inferior_v"],
        ]
        for item in rows
    ]
    super_rows = report["super_station"]["rows"]
    super_table = (
        _table(
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
        )
        if super_rows
        else "<p>暂无数据。</p>"
    )
    return f"""
<h2>1. 监测情况</h2>
<p>{escape(report["narratives"]["monitoring"])}</p>
<p class="table-title">表1 监测点位监测情况</p>
{_table(
    ["序号","监测点位名称","应测指标及频次","今日监测情况","昨日监测情况",
     f"近{report['options']['recent_days']}日监测情况","备注"],
    [[row["index"],row["station_name"],row["expected_indicators"],row["today_status"],
      row["yesterday_status"],recent(row["recent_three_days"]),row["remark"]]
     for row in monitoring["rows"]]
)}
<h2>2. 流域水质总体情况</h2>
<p>{escape(report["narratives"]["overall_quality"])}</p>
<p class="table-title">表2 监测点位水质情况</p>
{_table(
    ["序号","监测点位","今日水质类别","昨日水质类别","水质变化情况"],
    [[row["index"],row["station_name"],row["today_level"],row["yesterday_level"],row["change"]]
     for row in overall["rows"]]
)}
<h2>3. 湖区监测点位水质情况</h2>
<p>湖区点位{lake["station_count"]}个；过去120小时连续超水质目标
{lake["continuous_120h_over_target_count"]}个，连续7天日均值超目标
{lake["continuous_7d_over_target_count"]}个，连续三天日均值劣于Ⅳ类
{lake["continuous_3d_worse_iv_count"]}个，当日日均值为劣Ⅴ类
{lake["daily_inferior_v_count"]}个。降雨和水位：暂无数据。</p>
<p class="table-title">表3 湖区点位水质情况</p>
{_table(condition_headers, condition_rows(lake["rows"]))}
<h2>4. 支流水质情况</h2>
<p>已由断面属性确认的支流点位{tributaries["station_count"]}个；水质类别较昨日下降两个及以上类别
{tributaries["declined_two_or_more_count"]}个。水位和流量：暂无数据。</p>
<p class="table-title">表4 支流水质情况</p>
{_table(condition_headers, condition_rows(tributaries["rows"]))}
<h2>5. 超级站</h2>
{super_table}
"""


def _monthly_html(report: dict[str, Any]) -> str:
    monitoring = report["monitoring"]
    conditions = report["station_conditions"]
    return f"""
<h2>1. 监测情况</h2>
<p>{escape(report["narratives"]["monitoring"])}</p>
<p class="table-title">表1 监测点位监测情况</p>
{_table(
    ["序号","监测点位名称","应测指标及频次","缺测指标及时间段"],
    [[row["index"],row["station_name"],row["expected_indicators"],
      row["missing_indicators_and_periods"]] for row in monitoring["rows"]]
)}
<h2>2. 各监测点位情况</h2>
<p class="table-title">表2 监测点位水质情况</p>
{_table(
    ["序号","监测点位名称","120小时连续超水质目标次数","连续7天均值超水质目标次数",
     "连续三天均值劣于Ⅳ类次数","当日水质劣Ⅴ次数"],
    [[index,row["station_name"],row["continuous_120h_over_target_count"],
      row["continuous_7d_over_target_count"],row["continuous_3d_worse_iv_count"],
      row["daily_inferior_v_count"]] for index,row in enumerate(conditions["rows"],1)]
)}
"""

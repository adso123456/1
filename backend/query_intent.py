"""确定性选择问数上下文档位。"""

from __future__ import annotations

import re
from enum import Enum


class ContextProfile(str, Enum):
    FULL = "FULL"
    SIMPLE_LOOKUP = "SIMPLE_LOOKUP"


_COMPLEX_TERMS = (
    "统计", "聚合", "数量", "总数", "平均", "最大", "最小", "排名", "排行",
    "趋势", "变化", "同比", "环比", "原因", "分析", "总结", "建议", "对比",
    "比较", "图表", "柱状图", "折线图", "饼图", "可视化", "日报", "月报",
    "周报", "报告", "综合", "分布", "占比", "每个", "各个", "各区", "各断面",
)
_FULL_CONTEXT_TERMS = (
    "监测断面",
    "排污口类型",
    "排污口监测记录",
)
_FOLLOW_UP_TERMS = (
    "刚才", "上面", "上一", "继续", "再查", "其中", "这些", "它们", "前一",
)
_SIMPLE_PATTERNS = (
    re.compile(r"(?:前|只取|只列|列出|显示|查询|查找|有哪些).{0,20}\d+\s*(?:条|个|行)"),
    re.compile(r"(?:列出|显示|查询|查找|有哪些|明细|详情|记录|名单)"),
    re.compile(r"(?:最新|最近)\s*\d+\s*(?:条|个|行)"),
)


def select_context_profile(question: str) -> ContextProfile:
    normalized = re.sub(r"\s+", "", question or "")
    if not normalized:
        return ContextProfile.FULL
    if any(term in normalized for term in _COMPLEX_TERMS):
        return ContextProfile.FULL
    if any(term in normalized for term in _FULL_CONTEXT_TERMS):
        return ContextProfile.FULL
    if any(term in normalized for term in _FOLLOW_UP_TERMS):
        return ContextProfile.FULL
    if any(pattern.search(normalized) for pattern in _SIMPLE_PATTERNS):
        return ContextProfile.SIMPLE_LOOKUP
    return ContextProfile.FULL


def is_simple_lookup(question: str) -> bool:
    return select_context_profile(question) is ContextProfile.SIMPLE_LOOKUP


def is_simple_result_query(question: str) -> bool:
    """判断结果是否可用确定性摘要；与上下文档位的准确率回退解耦。"""
    normalized = re.sub(r"\s+", "", question or "")
    if not normalized:
        return False
    if any(term in normalized for term in _COMPLEX_TERMS):
        return False
    if any(term in normalized for term in _FOLLOW_UP_TERMS):
        return False
    return any(pattern.search(normalized) for pattern in _SIMPLE_PATTERNS)

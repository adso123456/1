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
_FOLLOW_UP_DATA_ACTION_PATTERNS = (
    re.compile(r"(?:只|仅)?(?:显示|保留|列出).{0,12}(?:其中|结果|前\s*\d+|后\s*\d+|最多|最少)"),
    re.compile(r"(?:刚才|上次|上一|上面|其中|这些|结果).{0,16}(?:前\s*\d+|后\s*\d+|最多|最少)"),
    re.compile(r"(?:再|继续)(?:筛选|过滤|查询|查找|统计|排序|显示|列出)"),
    re.compile(r"(?:筛选|过滤).{0,20}(?:刚才|上次|上一|上面|其中|这些|结果)"),
)
_EXPLANATION_ONLY_TERMS = (
    "解释", "说明", "什么意思", "含义", "怎么理解", "为什么", "原因",
)
_GREETING_OR_THANKS_PATTERNS = (
    re.compile(r"^(?:你|您)?好[啊呀吗]?$"),
    re.compile(r"^(?:嗨|哈喽|hello|hi)[啊呀]?$", re.IGNORECASE),
    re.compile(r"^(?:早上|上午|中午|下午|晚上)?好$"),
    re.compile(r"^(?:谢谢|感谢|多谢|辛苦了)[你您]?[了啊呀]?$"),
)
_EXPLICIT_EXPLANATION_PATTERNS = (
    re.compile(
        r"^(?:请)?(?:解释|说明)(?:一下)?"
        r"(?:刚才|上次|上一条|上面|这个|该|上述)(?:结果|回答|字段|SQL)"
    ),
    re.compile(
        r"(?:这个|该|上述|上面|刚才|上一条)?.{0,8}"
        r"(?:字段|结果|回答|SQL).{0,8}(?:是什么意思|什么含义|怎么理解)"
    ),
    re.compile(
        r"(?:SQL|SELECT|WHERE|JOIN|GROUPBY|ORDERBY).{0,8}"
        r"(?:语法|语句|含义|是什么意思|怎么写|怎么理解)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:刚才|上次|上一条|上面).{0,8}为什么.{0,12}(?:排序|显示|回答|计算)"),
)
_EXPLICIT_CLARIFICATION_PATTERNS = (
    re.compile(
        r"^(?:请问)?(?:需要|还需要)(?:我)?(?:说明|补充|提供).{0,16}"
        r"(?:什么|哪些)(?:条件|信息|范围)?[吗？?]?$"
    ),
    re.compile(r"^(?:你|您)是指.{1,24}[吗？?]$"),
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


def requires_fresh_data_followup(question: str) -> bool:
    """数据筛选型追问必须重新查询；解释、澄清和问候不触发。"""
    normalized = re.sub(r"\s+", "", question or "")
    if not normalized:
        return False
    if any(term in normalized for term in _EXPLANATION_ONLY_TERMS):
        return False
    return any(pattern.search(normalized) for pattern in _FOLLOW_UP_DATA_ACTION_PATTERNS)


def is_explicit_non_data_request(question: str) -> bool:
    """仅识别可以明确豁免数据库查询的交流；未识别请求默认查库。"""
    normalized = re.sub(r"\s+", "", question or "").strip("，。！？!?；;：:")
    if not normalized:
        return True
    return any(
        pattern.search(normalized)
        for pattern in (
            *_GREETING_OR_THANKS_PATTERNS,
            *_EXPLICIT_EXPLANATION_PATTERNS,
            *_EXPLICIT_CLARIFICATION_PATTERNS,
        )
    )

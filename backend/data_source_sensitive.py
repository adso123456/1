"""动态数据源字段和值域的共享敏感信息判定。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "private_key", "id_card", "idcard", "phone", "mobile", "email",
    "address", "身份证", "手机号", "联系电话", "联系方式", "联系人",
    "责任人", "法人", "真实姓名", "邮箱", "住址",
)
_SENSITIVE_TOKENS = frozenset(
    {
        "pwd", "contact", "telephone", "tel", "fax",
    }
)
_SENSITIVE_TOKEN_SEQUENCES = (
    ("contact", "number"),
    ("contact", "person"),
    ("responsible", "person"),
    ("legal", "representative"),
    ("real", "name"),
)
_TEXT_TYPE_MARKERS = ("char", "text", "string")
_CN_MOBILE_RE = re.compile(r"(?:\+?86[- ]?)?1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_CN_ID_RE = re.compile(r"\d{17}[0-9Xx]")


def _identifier_tokens(value: str) -> list[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.findall(r"[a-z0-9]+", separated.lower())


def _contains_token_sequence(
    tokens: list[str],
    expected: tuple[str, ...],
) -> bool:
    width = len(expected)
    return any(
        tuple(tokens[index:index + width]) == expected
        for index in range(len(tokens))
    )


def is_sensitive_column(column_name: str, column_comment: str = "") -> bool:
    """按字段名与注释识别个人敏感字段，避免依赖单个调用方规则。"""

    text = f"{column_name} {column_comment}"
    lowered = text.lower()
    if any(word in lowered for word in _SENSITIVE_SUBSTRINGS):
        return True
    tokens = _identifier_tokens(text)
    if any(token in _SENSITIVE_TOKENS for token in tokens):
        return True
    return any(
        _contains_token_sequence(tokens, expected)
        for expected in _SENSITIVE_TOKEN_SEQUENCES
    )


def contains_sensitive_typical_value(
    values: Iterable[Any],
    data_type: str,
) -> bool:
    """发布时仅对字符型值执行严格手机号、邮箱、身份证格式兜底。"""

    if not any(marker in data_type.lower() for marker in _TEXT_TYPE_MARKERS):
        return False
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if (
            _CN_MOBILE_RE.fullmatch(candidate)
            or _EMAIL_RE.fullmatch(candidate)
            or _CN_ID_RE.fullmatch(candidate)
        ):
            return True
    return False

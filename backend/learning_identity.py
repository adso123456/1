"""Question/SQL 学习资产的安全规范化与确定性身份。"""

from __future__ import annotations

import hashlib
import re


_QUESTION_TRANSLATION = str.maketrans(
    {
        "？": "?",
        "！": "!",
        "。": ".",
        "；": ";",
        "：": ":",
        "，": ",",
        "（": "(",
        "）": ")",
    }
)
_QUESTION_TRAILING_PUNCTUATION = re.compile(r"[.!?;:,]+$")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_question(question: str) -> str:
    """只统一空白、常见全半角标点和句末标点，不做语义改写。"""
    translated = str(question or "").translate(_QUESTION_TRANSLATION)
    compact = re.sub(r"[\s　]+", "", translated)
    return _QUESTION_TRAILING_PUNCTUATION.sub("", compact)


def normalize_sql(sql: str) -> str:
    """只规范 SQL 字符串字面量之外的大小写、注释、空白和末尾分号。"""
    source = str(sql or "")
    output: list[str] = []
    index = 0
    quote = ""
    pending_space = False
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            output.append(char)
            if char == quote:
                if next_char == quote:
                    output.append(next_char)
                    index += 2
                    continue
                quote = ""
            elif char == "\\" and next_char:
                output.append(next_char)
                index += 2
                continue
            index += 1
            continue
        if char in {"'", '"', "`"}:
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "-" and next_char == "-":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
            pending_space = True
            continue
        if char == "/" and next_char == "*":
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            pending_space = True
            continue
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and output and output[-1] != " ":
            output.append(" ")
        pending_space = False
        output.append(char.lower())
        index += 1
    return "".join(output).strip().rstrip(";").rstrip()


def content_identity(source_id: str, question: str, sql: str) -> str:
    return _sha256(
        f"{source_id}|{normalize_question(question)}|{normalize_sql(sql)}"
    )


def question_identity(source_id: str, question: str) -> str:
    return _sha256(f"{source_id}|{normalize_question(question)}")

"""DDL Text Memory v1 确定性身份与内容指纹纯函数。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


IDENTITY_VERSION = "ddlmem-v1"
MEMORY_TYPE = "ddl_text"
ALLOWED_OBJECT_TYPES = frozenset({"table"})
SOURCE_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
EFFECTIVE_METADATA_FIELDS = frozenset(
    {
        "memory_type",
        "identity_version",
        "source_id",
        "schema_name",
        "object_type",
        "object_name",
        "logical_id",
        "record_id",
    }
)


@dataclass(frozen=True)
class DdlMemoryIdentityInput:
    source_id: str
    schema_name: str
    object_type: str
    object_name: str

    def __post_init__(self) -> None:
        _validate_source_id(self.source_id)
        _validate_identifier("schema_name", self.schema_name)
        _validate_object_type(self.object_type)
        _validate_identifier("object_name", self.object_name)


@dataclass(frozen=True)
class DdlMemoryIdentity:
    identity_key: str
    logical_id: str
    record_id: str
    normalized_ddl: str
    content_fingerprint: str
    effective_metadata: Mapping[str, str]


def _require_string(field_name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value


def _validate_source_id(value: Any) -> None:
    source_id = _require_string("source_id", value)
    if not source_id:
        raise ValueError("source_id 不能为空")
    if source_id != source_id.lower():
        raise ValueError("source_id 必须由调用方以小写形式传入")
    if SOURCE_ID_PATTERN.fullmatch(source_id) is None:
        raise ValueError("source_id 必须符合 [a-z][a-z0-9_-]{0,63}")


def _validate_object_type(value: Any) -> None:
    object_type = _require_string("object_type", value)
    if object_type not in ALLOWED_OBJECT_TYPES:
        raise ValueError("object_type 当前只允许 table")


def _validate_identifier(field_name: str, value: Any) -> None:
    identifier = _require_string(field_name, value)
    if not identifier or not identifier.strip():
        raise ValueError(f"{field_name} 去除首尾空白后不能为空")
    if "|" in identifier:
        raise ValueError(f"{field_name} 禁止包含身份分隔符 |")
    if any(unicodedata.category(character) == "Cc" for character in identifier):
        raise ValueError(f"{field_name} 禁止包含控制字符、换行或 NUL")


def normalize_ddl(value: Any) -> str:
    """仅统一换行、删除行尾空格/Tab，并删除文本首尾空白。"""
    ddl = _require_string("ddl", value)
    normalized = ddl.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("DDL 规范化后不能为空")
    return normalized


def build_identity_key(identity: DdlMemoryIdentityInput) -> str:
    return (
        f"{IDENTITY_VERSION}|{identity.source_id}|{identity.schema_name}|"
        f"{identity.object_type}|{identity.object_name}"
    )


def build_logical_id(identity: DdlMemoryIdentityInput) -> str:
    return hashlib.sha256(build_identity_key(identity).encode("utf-8")).hexdigest()


def build_record_id(logical_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", logical_id) is None:
        raise ValueError("logical_id 必须是小写 64 位十六进制")
    return f"{IDENTITY_VERSION}-{logical_id}"


def build_effective_metadata(
    identity: DdlMemoryIdentityInput, logical_id: str, record_id: str
) -> Mapping[str, str]:
    expected_record_id = build_record_id(logical_id)
    if record_id != expected_record_id:
        raise ValueError("record_id 与 logical_id 不匹配")
    return MappingProxyType(
        {
            "memory_type": MEMORY_TYPE,
            "identity_version": IDENTITY_VERSION,
            "source_id": identity.source_id,
            "schema_name": identity.schema_name,
            "object_type": identity.object_type,
            "object_name": identity.object_name,
            "logical_id": logical_id,
            "record_id": record_id,
        }
    )


def canonical_json(
    normalized_ddl: str, effective_metadata: Mapping[str, Any]
) -> str:
    if not isinstance(normalized_ddl, str) or not normalized_ddl:
        raise ValueError("normalized_ddl 不能为空")
    if set(effective_metadata) != EFFECTIVE_METADATA_FIELDS:
        missing = sorted(EFFECTIVE_METADATA_FIELDS - set(effective_metadata))
        extra = sorted(set(effective_metadata) - EFFECTIVE_METADATA_FIELDS)
        raise ValueError(f"effective_metadata 字段不匹配：missing={missing}, extra={extra}")
    payload = [normalized_ddl, dict(effective_metadata)]
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_content_fingerprint(
    normalized_ddl: str, effective_metadata: Mapping[str, Any]
) -> str:
    canonical = canonical_json(normalized_ddl, effective_metadata)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ddl_memory_identity(
    identity: DdlMemoryIdentityInput, ddl: str
) -> DdlMemoryIdentity:
    identity_key = build_identity_key(identity)
    logical_id = build_logical_id(identity)
    record_id = build_record_id(logical_id)
    normalized_ddl = normalize_ddl(ddl)
    metadata = build_effective_metadata(identity, logical_id, record_id)
    fingerprint = build_content_fingerprint(normalized_ddl, metadata)
    return DdlMemoryIdentity(
        identity_key=identity_key,
        logical_id=logical_id,
        record_id=record_id,
        normalized_ddl=normalized_ddl,
        content_fingerprint=fingerprint,
        effective_metadata=metadata,
    )


def _expect_value_error(callable_object: Any, expected_text: str) -> None:
    try:
        callable_object()
    except ValueError as exc:
        if expected_text not in str(exc):
            raise AssertionError(f"异常信息未包含 {expected_text!r}：{exc}") from exc
    else:
        raise AssertionError(f"预期 ValueError：{expected_text}")


def self_test() -> int:
    base_input = DdlMemoryIdentityInput(
        source_id="postgres_water",
        schema_name="public",
        object_type="table",
        object_name="Demo",
    )
    ddl_lf = 'CREATE TABLE "Demo" (\n  id integer  \n);\n'
    ddl_crlf = 'CREATE TABLE "Demo" (\r\n  id integer\t\r\n);\r\n'
    first = build_ddl_memory_identity(base_input, ddl_lf)
    repeated = build_ddl_memory_identity(base_input, ddl_lf)
    whitespace_variant = build_ddl_memory_identity(base_input, ddl_crlf)
    changed_ddl = build_ddl_memory_identity(
        base_input, 'CREATE TABLE "Demo" (\n  id bigint\n);'
    )

    assert first == repeated
    assert first.normalized_ddl == whitespace_variant.normalized_ddl
    assert first.content_fingerprint == whitespace_variant.content_fingerprint
    assert first.logical_id == changed_ddl.logical_id
    assert first.record_id == changed_ddl.record_id
    assert first.content_fingerprint != changed_ddl.content_fingerprint

    def changed(**overrides: str) -> DdlMemoryIdentity:
        values = {
            "source_id": base_input.source_id,
            "schema_name": base_input.schema_name,
            "object_type": base_input.object_type,
            "object_name": base_input.object_name,
        }
        values.update(overrides)
        return build_ddl_memory_identity(DdlMemoryIdentityInput(**values), ddl_lf)

    assert first.logical_id != changed(source_id="postgres_water_2").logical_id
    assert first.logical_id != changed(schema_name="archive").logical_id
    assert first.logical_id != changed(object_name="demo").logical_id
    assert first.content_fingerprint != changed(source_id="postgres_water_2").content_fingerprint
    assert first.content_fingerprint != changed(schema_name="archive").content_fingerprint
    assert first.content_fingerprint != changed(object_name="demo").content_fingerprint
    _expect_value_error(
        lambda: changed(object_type="view"), "object_type 当前只允许 table"
    )

    reversed_metadata = dict(reversed(list(first.effective_metadata.items())))
    assert canonical_json(first.normalized_ddl, first.effective_metadata) == canonical_json(
        first.normalized_ddl, reversed_metadata
    )

    invalid_inputs = (
        (lambda: DdlMemoryIdentityInput("", "public", "table", "demo"), "source_id"),
        (lambda: DdlMemoryIdentityInput("Postgres", "public", "table", "demo"), "小写"),
        (lambda: DdlMemoryIdentityInput("postgres water", "public", "table", "demo"), "符合"),
        (lambda: DdlMemoryIdentityInput("postgres", " ", "table", "demo"), "schema_name"),
        (lambda: DdlMemoryIdentityInput("postgres", "pub|lic", "table", "demo"), "分隔符"),
        (lambda: DdlMemoryIdentityInput("postgres", "pub\nlic", "table", "demo"), "控制字符"),
        (lambda: DdlMemoryIdentityInput("postgres", "public", "table", "de\x00mo"), "控制字符"),
        (lambda: DdlMemoryIdentityInput("postgres", "public", "table", " "), "object_name"),
        (lambda: DdlMemoryIdentityInput("postgres", "public", "table", "de|mo"), "分隔符"),
    )
    for callable_object, expected_text in invalid_inputs:
        _expect_value_error(callable_object, expected_text)
    _expect_value_error(lambda: normalize_ddl(" \r\n\t"), "不能为空")

    assert re.fullmatch(r"[0-9a-f]{64}", first.logical_id)
    assert first.record_id == f"ddlmem-v1-{first.logical_id}"
    assert set(first.effective_metadata) == EFFECTIVE_METADATA_FIELDS
    forbidden_metadata = {
        "timestamp",
        "created_at",
        "updated_at",
        "uuid",
        "path",
        "batch_time",
        "conversation_id",
        "request_id",
    }
    assert not forbidden_metadata.intersection(first.effective_metadata)

    forbidden_modules = ("chromadb", "vanna", "backend.memory")
    assert not any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for module_name in sys.modules
        for forbidden in forbidden_modules
    )

    print("DDL_MEMORY_IDENTITY_SELF_TEST=PASS")
    print("IDENTITY_REPEATABILITY_TEST=PASS")
    print("NORMALIZATION_STABILITY_TEST=PASS")
    print("IDENTITY_FIELD_VALIDATION_TEST=PASS")
    print("PURE_MODULE_IMPORT_TEST=PASS")
    return 0


def main() -> int:
    if sys.argv[1:] != ["--self-test"]:
        print("用法：python -m training.sop.ddl_memory_identity --self-test", file=sys.stderr)
        return 2
    return self_test()


if __name__ == "__main__":
    raise SystemExit(main())

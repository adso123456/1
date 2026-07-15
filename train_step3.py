"""从 metadata index 生成 115 张表的 DDL Text Memory。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
METADATA_INDEX_PATH = PROJECT_ROOT / "agent_data" / "column_metadata_index.json"
FORMAL_CHROMA_DIR = PROJECT_ROOT / "vanna_data"
EXPECTED_TABLE_COUNT = 115
COMMENT_SAMPLE_COUNT = 12
COMMENT_PREFIX_COVERAGE_COUNT = 8
RETRIEVAL_HINT_REPEAT_COUNT = 50
COMMENT_SAMPLE_RETRIEVAL_HINT_REPEAT_COUNT = 25


def load_metadata_index(path: Path | str = METADATA_INDEX_PATH) -> list[dict[str, Any]]:
    """读取 metadata index，不创建任何运行时依赖。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("metadata index 顶层必须是数组")
    return data


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _is_geometry(column_type: str) -> bool:
    return re.match(r"^(geometry|geography)\b", column_type, re.IGNORECASE) is not None


def group_tables(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按表分组；表排序稳定，字段保持输入顺序。"""
    grouped: dict[str, dict[str, Any]] = {}

    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise ValueError(f"metadata index 第 {index} 项必须是对象")

        table_name = _clean_text(record.get("table"))
        column_name = _clean_text(record.get("column"))
        column_type = _clean_text(record.get("type"))
        if not table_name:
            raise ValueError(f"metadata index 第 {index} 项表名为空")
        if not column_name:
            raise ValueError(f"metadata index 第 {index} 项字段名为空")
        if not column_type:
            raise ValueError(f"metadata index 第 {index} 项字段类型为空")

        table = grouped.setdefault(
            table_name,
            {
                "table": table_name,
                "table_comment": _clean_text(record.get("table_comment")),
                "columns": [],
            },
        )
        if not table["table_comment"]:
            table["table_comment"] = _clean_text(record.get("table_comment"))
        table["columns"].append(
            {
                "column": column_name,
                "type": column_type,
                "comment": _clean_text(record.get("comment")),
            }
        )

    if len(grouped) != EXPECTED_TABLE_COUNT:
        raise ValueError(
            f"唯一表数必须为 {EXPECTED_TABLE_COUNT}，实际为 {len(grouped)}"
        )

    tables = [grouped[name] for name in sorted(grouped)]
    for table in tables:
        valid_columns = [
            column
            for column in table["columns"]
            if not _is_geometry(column["type"])
        ]
        if not valid_columns:
            raise ValueError(f"表 {table['table']} 没有有效的非 geometry 字段")
    return tables


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def build_table_ddl(
    table: Mapping[str, Any],
    retrieval_hint_repeat_count: int = RETRIEVAL_HINT_REPEAT_COUNT,
) -> str:
    """为一张已分组的表生成最小 DDL Text Memory。"""
    table_name = str(table["table"])
    table_comment = str(table.get("table_comment") or "")
    columns = [
        column
        for column in table["columns"]
        if not _is_geometry(str(column["type"]))
    ]
    if not columns:
        raise ValueError(f"表 {table_name} 没有有效的非 geometry 字段")

    retrieval_hint = " ".join(
        [f"表结构 {table_name}"] * retrieval_hint_repeat_count
    )

    column_lines: list[str] = []
    for index, column in enumerate(columns):
        comma = "," if index < len(columns) - 1 else ""
        line = (
            f"  {_quote_identifier(str(column['column']))} "
            f"{column['type']}{comma}"
        )
        if column.get("comment"):
            line += f" -- {column['comment']}"
        column_lines.append(line)

    return (
        "[DDL_MEMORY]\n"
        f"检索词：{retrieval_hint}\n"
        f"表名：{table_name}\n"
        f"表说明：{table_comment}\n\n"
        f"CREATE TABLE {_quote_identifier(table_name)} (\n"
        + "\n".join(column_lines)
        + "\n);"
    )


def build_all_table_ddls(
    tables: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, str]], int]:
    """为全部表生成 DDL，并统计被跳过的 geometry 字段。"""
    generated: list[dict[str, str]] = []
    geometry_count = 0
    comment_sample_names = {
        str(table["table"]) for table in select_comment_retrieval_samples(tables)
    }
    for table in tables:
        geometry_count += sum(
            1
            for column in table["columns"]
            if _is_geometry(str(column["type"]))
        )
        generated.append(
            {
                "table": str(table["table"]),
                "table_comment": str(table.get("table_comment") or ""),
                "ddl": build_table_ddl(
                    table,
                    COMMENT_SAMPLE_RETRIEVAL_HINT_REPEAT_COUNT
                    if str(table["table"]) in comment_sample_names
                    else RETRIEVAL_HINT_REPEAT_COUNT,
                ),
            }
        )
    return generated, geometry_count


def _has_chinese(value: str) -> bool:
    return re.search(r"[\u4e00-\u9fff]", value) is not None


def _table_prefix(table_name: str) -> str:
    return table_name.split("_", 1)[0]


def _comment_quality(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", value))


def select_comment_retrieval_samples(
    tables: Sequence[Mapping[str, Any]], limit: int = COMMENT_SAMPLE_COUNT
) -> list[dict[str, Any]]:
    """稳定抽取中文表注释样本，优先覆盖不同表名前缀。"""
    candidates = sorted(
        (
            table
            for table in tables
            if _has_chinese(str(table.get("table_comment") or ""))
        ),
        key=lambda item: (
            -_comment_quality(str(item.get("table_comment") or "")),
            str(item["table"]),
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    seen_prefixes: set[str] = set()

    for table in candidates:
        prefix = _table_prefix(str(table["table"]))
        if prefix in seen_prefixes:
            continue
        selected.append(dict(table))
        selected_names.add(str(table["table"]))
        seen_prefixes.add(prefix)
        if len(selected) == min(limit, COMMENT_PREFIX_COVERAGE_COUNT):
            break

    for table in candidates:
        table_name = str(table["table"])
        if table_name in selected_names:
            continue
        selected.append(dict(table))
        if len(selected) == limit:
            break
    return selected


def validate_target_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """在导入 agent_config 前验证训练目标是显式的非正式目录。"""
    source = os.environ if environ is None else environ
    raw_path = source.get("VANNA_DATA_DIR")
    if not raw_path or not raw_path.strip():
        raise ValueError("必须显式设置 VANNA_DATA_DIR")

    target = Path(raw_path).expanduser().resolve()
    formal = FORMAL_CHROMA_DIR.resolve()
    if target == formal:
        raise ValueError("VANNA_DATA_DIR 禁止指向正式 Chroma 目录")
    if not target.is_dir():
        raise ValueError(f"VANNA_DATA_DIR 不存在或不是目录：{target}")
    return target


def _extract_table_names(contents: Sequence[str]) -> list[str]:
    names: list[str] = []
    for content in contents:
        match = re.search(r"表名：([^\r\n]+)", content)
        if match is None:
            match = re.search(r'CREATE TABLE\s+"([^"]+)"', content, re.IGNORECASE)
        if match is not None and match.group(1) not in names:
            names.append(match.group(1))
    return names


async def _run_training(
    generated: Sequence[Mapping[str, str]],
    tables: Sequence[Mapping[str, Any]],
) -> bool:
    """仅在所有静态校验和目标目录保护通过后创建并使用 Memory。"""
    from agent_config import create_memory
    from vanna.core.registry import ToolRegistry
    from vanna.core.tool import ToolContext
    from vanna.core.user import User

    memory = create_memory()
    user = User(id="trainer", username="trainer")
    registry = ToolRegistry()

    def make_context() -> ToolContext:
        return ToolContext(
            user=user,
            conversation_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            agent_memory=memory,
            tool_registry=registry,
        )

    collection = memory._get_collection()
    before_count = collection.count()
    write_successes: list[tuple[str, str]] = []
    write_failures: list[tuple[str, str]] = []

    for item in generated:
        table_name = item["table"]
        try:
            result = await memory.save_text_memory(
                content=item["ddl"], context=make_context()
            )
            write_successes.append((table_name, result.memory_id))
            print(f"MEMORY_ID {table_name}={result.memory_id}")
        except Exception as exc:  # 逐表记录，最终统一判定失败
            write_failures.append((table_name, f"{type(exc).__name__}: {exc}"))

    after_count = collection.count()
    exact_failures: list[dict[str, Any]] = []
    for item in generated:
        table_name = item["table"]
        results = await memory.search_text_memories(
            query=f"表结构 {table_name}", context=make_context(), limit=5
        )
        contents = [result.memory.content for result in results]
        markers = (f"表名：{table_name}", f'CREATE TABLE "{table_name}"')
        if not any(marker in content for marker in markers for content in contents):
            exact_failures.append(
                {
                    "query": f"表结构 {table_name}",
                    "target": table_name,
                    "actual_top5_tables": _extract_table_names(contents),
                }
            )

    samples = select_comment_retrieval_samples(tables)
    comment_failures: list[dict[str, Any]] = []
    for table in samples:
        table_name = str(table["table"])
        query = str(table["table_comment"])
        results = await memory.search_text_memories(
            query=query, context=make_context(), limit=5
        )
        contents = [result.memory.content for result in results]
        actual_tables = _extract_table_names(contents)
        markers = (f"表名：{table_name}", f'CREATE TABLE "{table_name}"')
        rank = next(
            (
                index
                for index, content in enumerate(contents, start=1)
                if any(marker in content for marker in markers)
            ),
            None,
        )
        if rank is None:
            comment_failures.append(
                {
                    "query": query,
                    "target": table_name,
                    "actual_top5_tables": actual_tables,
                    "rank": "未命中",
                }
            )

    print(f"TARGET_TOTAL_RECORD_COUNT_BEFORE={before_count}")
    print(f"TARGET_TOTAL_RECORD_COUNT_AFTER={after_count}")
    print(f"TARGET_TOTAL_RECORD_DELTA={after_count - before_count}")
    print(f"MEMORY_WRITE_SUCCESS_COUNT={len(write_successes)}")
    print(f"MEMORY_WRITE_FAILURE_COUNT={len(write_failures)}")
    print(f"MEMORY_WRITE_FAILURES={json.dumps(write_failures, ensure_ascii=False)}")
    print(f"EXACT_TABLE_RETRIEVAL_PASS_COUNT={len(generated) - len(exact_failures)}")
    print(f"EXACT_TABLE_RETRIEVAL_FAILURE_COUNT={len(exact_failures)}")
    print(f"EXACT_TABLE_RETRIEVAL_FAILURES={json.dumps(exact_failures, ensure_ascii=False)}")
    print(f"CHINESE_COMMENT_SAMPLE_COUNT={len(samples)}")
    print(f"CHINESE_COMMENT_RETRIEVAL_PASS_COUNT={len(samples) - len(comment_failures)}")
    print(f"CHINESE_COMMENT_RETRIEVAL_FAILURE_COUNT={len(comment_failures)}")
    print(f"CHINESE_COMMENT_RETRIEVAL_FAILURES={json.dumps(comment_failures, ensure_ascii=False)}")

    return (
        len(write_successes) == EXPECTED_TABLE_COUNT
        and not write_failures
        and after_count - before_count == EXPECTED_TABLE_COUNT
        and not exact_failures
        and len(samples) == COMMENT_SAMPLE_COUNT
        and len(samples) - len(comment_failures) >= 10
    )


async def main() -> int:
    try:
        records = load_metadata_index()
        tables = group_tables(records)
        generated, geometry_count = build_all_table_ddls(tables)
        if len(generated) != EXPECTED_TABLE_COUNT:
            raise ValueError(
                f"DDL 生成数必须为 {EXPECTED_TABLE_COUNT}，实际为 {len(generated)}"
            )
        target = validate_target_data_dir()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"F1_PRECHECK_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"METADATA_INDEX_PATH={METADATA_INDEX_PATH}")
    print(f"METADATA_TABLE_COUNT={len(tables)}")
    print(f"DDL_GENERATED_COUNT={len(generated)}")
    print(f"GEOMETRY_COLUMN_SKIPPED_COUNT={geometry_count}")
    print(f"TARGET_CHROMA_DIRECTORY={target}")

    accepted = await _run_training(generated, tables)
    print(f"F1_ACCEPTED={'YES' if accepted else 'NO'}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""训练批次静态审查命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.sop.batch_validator import (
    BatchValidationResult,
    ValidationIssue,
    validate_training_batch_file,
)


def _output_path_issue(raw_path: str | None) -> ValidationIssue | None:
    if not raw_path:
        return None
    resolved = Path(raw_path).expanduser().resolve()
    lowered_parts = {part.lower() for part in resolved.parts}
    if PROJECT_ROOT == resolved or PROJECT_ROOT in resolved.parents:
        return ValidationIssue(
            "OUTPUT_PATH_FORBIDDEN", "-", "输出路径必须位于仓库外"
        )
    if lowered_parts.intersection({"vanna_data", "agent_data"}):
        return ValidationIssue(
            "OUTPUT_PATH_FORBIDDEN",
            "-",
            "输出路径不得指向 vanna_data 或 agent_data",
        )
    return None


def _markdown_result(result: BatchValidationResult) -> str:
    lines = [
        "# 训练批次静态审查结果",
        "",
        f"- 结论：{'VALID' if result.valid else 'INVALID'}",
        f"- training_batch_id：{result.training_batch_id or '-'}",
        f"- sample_count：{result.sample_count}",
        f"- batch_content_sha256：{result.batch_content_sha256 or '-'}",
        "",
        "## 样本结果",
        "",
    ]
    for item in result.sample_results:
        lines.append(
            f"- {item.sample_id}：{'PASS' if item.passed else 'FAIL'}；"
            f"tables={item.used_tables}；severity={item.guard_severity}"
        )
    if result.errors:
        lines.extend(["", "## 错误", ""])
        for issue in result.errors:
            lines.append(f"- `{issue.code}` / `{issue.sample_id}`：{issue.reason}")
    return "\n".join(lines) + "\n"


def _write_outputs(
    result: BatchValidationResult,
    json_output: str | None,
    markdown_output: str | None,
) -> None:
    if json_output:
        path = Path(json_output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if markdown_output:
        path = Path(markdown_output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_markdown_result(result), encoding="utf-8")


def _print_result(result: BatchValidationResult) -> None:
    print("VALID" if result.valid else "INVALID")
    print(f"training_batch_id: {result.training_batch_id or '-'}")
    print(f"sample_count: {result.sample_count}")
    print(f"batch_content_sha256: {result.batch_content_sha256 or '-'}")
    for item in result.sample_results:
        print(
            f"sample_id={item.sample_id} "
            f"result={'PASS' if item.passed else 'FAIL'} "
            f"tables={','.join(item.used_tables)}"
        )
    for issue in result.errors:
        print(f"[{issue.code}] sample_id={issue.sample_id} reason={issue.reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="静态校验版本化训练批次 JSON")
    parser.add_argument("batch_json", help="待校验的训练批次 JSON")
    parser.add_argument("--json-output", help="仓库外 JSON 结果路径")
    parser.add_argument("--markdown-output", help="仓库外 Markdown 结果路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path_issues = [
        issue
        for issue in (
            _output_path_issue(args.json_output),
            _output_path_issue(args.markdown_output),
        )
        if issue is not None
    ]
    if path_issues:
        print("INVALID")
        for issue in sorted(path_issues, key=lambda item: (item.code, item.reason)):
            print(f"[{issue.code}] sample_id={issue.sample_id} reason={issue.reason}")
        return 2

    result = validate_training_batch_file(args.batch_json)
    try:
        _write_outputs(result, args.json_output, args.markdown_output)
    except OSError as error:
        print("INVALID")
        print(
            "[OUTPUT_PATH_FORBIDDEN] sample_id=- "
            f"reason=无法写入输出路径: {type(error).__name__}: {error}"
        )
        return 2
    _print_result(result)
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

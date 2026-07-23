from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import (
    DEFAULT_INDEX_PATH,
    METADATA_INDEX_PATH_ENV,
    DeterministicMetadataRetriever,
)
from backend.sql_guard import SQLGuard


def _write_index(path: Path, table: str) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "table": table,
                    "column": "id",
                    "data_type": "integer",
                    "table_comment": "",
                    "column_comment": "",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> int:
    original_value = os.environ.get(METADATA_INDEX_PATH_ENV)
    try:
        os.environ.pop(METADATA_INDEX_PATH_ENV, None)
        default_retriever = DeterministicMetadataRetriever()
        assert default_retriever.index_path == DEFAULT_INDEX_PATH.resolve()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            environment_index = temp_root / "environment.json"
            explicit_index = temp_root / "explicit.json"
            _write_index(environment_index, "environment_table")
            _write_index(explicit_index, "explicit_table")

            os.environ[METADATA_INDEX_PATH_ENV] = str(environment_index)
            environment_retriever = DeterministicMetadataRetriever()
            environment_guard = SQLGuard()
            explicit_retriever = DeterministicMetadataRetriever(explicit_index)
            explicit_guard = SQLGuard(explicit_index)

            assert environment_retriever.index_path == environment_index.resolve()
            assert environment_guard.index_path == environment_index.resolve()
            assert "environment_table" in environment_retriever.tables
            assert "environment_table" in environment_guard.table_columns
            assert explicit_retriever.index_path == explicit_index.resolve()
            assert explicit_guard.index_path == explicit_index.resolve()
            assert "explicit_table" in explicit_retriever.tables
            assert "explicit_table" in explicit_guard.table_columns
    finally:
        if original_value is None:
            os.environ.pop(METADATA_INDEX_PATH_ENV, None)
        else:
            os.environ[METADATA_INDEX_PATH_ENV] = original_value

    print("METADATA_INDEX_PATH_TESTS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

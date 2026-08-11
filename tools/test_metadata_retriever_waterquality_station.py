from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.metadata_retriever import DeterministicMetadataRetriever


TABLES = (
    (
        "wm_station_info",
        "水质自动监测站基本信息表",
        "station_name",
        "监测站点名称",
    ),
    (
        "wm_section_info",
        "水质监测断面数据信息表",
        "section_name",
        "断面名称",
    ),
    (
        "wm_waterbody_info",
        "水体基本信息表",
        "waterbody_name",
        "水体名称",
    ),
)


def _write_fixture(path: Path) -> None:
    rows = [
        {
            "table": table,
            "table_comment": table_comment,
            "column": column,
            "type": "varchar(255)",
            "comment": column_comment,
        }
        for table, table_comment, column, column_comment in TABLES
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary_directory:
        metadata_path = Path(temporary_directory) / "metadata.json"
        _write_fixture(metadata_path)
        retriever = DeterministicMetadataRetriever(metadata_path)

        station_query = "水质自动监测站有多少个？"
        station_candidates = retriever.retrieve(station_query, top_n=3)
        station_scores = {
            item["table_name"]: item for item in station_candidates
        }
        assert station_candidates[0]["table_name"] == "wm_station_info"
        assert (
            "waterquality_automatic_station_entity_intent"
            in station_scores["wm_station_info"]["matched_by"]
        )
        assert (
            station_scores["wm_station_info"]["score"]
            > station_scores["wm_section_info"]["score"]
        )

        section_query = "水质监测断面有多少个？"
        section_candidates = retriever.retrieve(section_query, top_n=3)
        assert section_candidates[0]["table_name"] == "wm_section_info"
        station_candidate = retriever._score_table(
            section_query,
            retriever.tables["wm_station_info"],
        )
        assert (
            "waterquality_automatic_station_entity_intent"
            not in station_candidate["matched_by"]
        )

    print("metadata retriever waterquality station tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

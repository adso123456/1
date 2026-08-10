"""只读重算 Automatic Governance V2.1；不调用 Reviewer 或写 Catalog。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymysql
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_source_eligibility import evaluate_table_eligibility
from backend.data_source_table_scorer import compute_proposals


SOURCE_ID = "mysql-lzh-monitor"
CATALOG_PATH = os.getenv(
    "REPORT_CATALOG_PATH",
    "/agent_data/data_sources/catalog.sqlite3",
)


def _load_live_inputs() -> tuple[list[dict], dict, dict]:
    connection = sqlite3.connect(
        f"file:{CATALOG_PATH}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        profiles = [
            json.loads(row["profile_json"])
            for row in connection.execute(
                "SELECT profile_json FROM data_source_table_profiles "
                "WHERE source_id=? ORDER BY schema_name, table_name",
                (SOURCE_ID,),
            )
        ]
        reviews = {
            (row["schema_name"], row["table_name"]): dict(row)
            for row in connection.execute(
                "SELECT * FROM data_source_table_reviews WHERE source_id=?",
                (SOURCE_ID,),
            )
        }
        source = dict(
            connection.execute(
                "SELECT * FROM data_sources WHERE source_id=?",
                (SOURCE_ID,),
            ).fetchone()
        )
        return profiles, reviews, source
    finally:
        connection.close()


def _comment_ratios(source: dict) -> dict[tuple[str, str], float]:
    if source["credential_mode"] == "environment":
        references = json.loads(source["credential_reference_json"] or "{}")
        username = os.environ[references["username"]]
        password = os.environ[references["password"]]
    else:
        key = os.environ["DATA_SOURCE_CREDENTIAL_KEY"].encode("ascii")
        cipher = Fernet(key)
        username = cipher.decrypt(
            source["encrypted_username"].encode("ascii")
        ).decode()
        password = cipher.decrypt(
            source["encrypted_password"].encode("ascii")
        ).decode()
    connection = pymysql.connect(
        host=source["host"],
        port=int(source["port"]),
        user=username,
        password=password,
        database=source["database_name"],
        connect_timeout=int(source["connect_timeout"]),
        read_timeout=30,
        charset="utf8mb4",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_COMMENT "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s",
                (source["database_name"],),
            )
            total: Counter[tuple[str, str]] = Counter()
            commented: Counter[tuple[str, str]] = Counter()
            for schema, table, comment in cursor.fetchall():
                table_key = (str(schema), str(table))
                total[table_key] += 1
                if str(comment or "").strip():
                    commented[table_key] += 1
            return {
                table_key: commented[table_key] / count
                for table_key, count in total.items()
            }
    finally:
        connection.close()


def main() -> None:
    profiles, reviews, source = _load_live_inputs()
    ratios = _comment_ratios(source)
    proposals = compute_proposals(profiles, ratios, reviews)

    eligibility_counts: Counter[str] = Counter()
    for profile in profiles:
        result = evaluate_table_eligibility(
            profile,
            profile.get("quality") or {},
        )
        eligibility_counts[result.status] += 1
    proposed_counts = Counter(
        fields["proposed_decision"] for fields in proposals.values()
    )
    projected_effective = {
        "active": proposed_counts["active"],
        "standby": len(proposals) - proposed_counts["active"],
    }

    migration: dict[str, Counter[str]] = defaultdict(Counter)
    transitions: list[dict] = []
    for table_key, fields in proposals.items():
        old = str((reviews.get(table_key) or {}).get("effective_decision") or "")
        migration[old][fields["proposed_decision"]] += 1
        if (old == "active") != (fields["proposed_decision"] == "active"):
            transitions.append(
                {
                    "table": ".".join(table_key),
                    "old_effective": old,
                    "new_proposed": fields["proposed_decision"],
                    "score": fields["proposed_score"],
                    "reason": fields["proposed_reason"],
                }
            )

    shard_families: dict[str, list[dict]] = defaultdict(list)
    for (schema, table), fields in proposals.items():
        metrics = fields.get("quality_metrics_patch") or {}
        family = str(metrics.get("physical_shard_family") or "")
        if family:
            shard_families[f"{schema}.{family}"].append(
                {
                    "table": table,
                    "role": metrics["physical_shard_role"],
                    "closed_time_partition": metrics[
                        "closed_physical_time_partition"
                    ],
                    "score": fields["proposed_score"],
                    "proposed": fields["proposed_decision"],
                }
            )

    aliases = (
        "we_phytoplankton_records",
        "we_ecologynutrition_records",
        "wm_waterquality_day_records",
        "wm_waterquality_hour_records",
        "wm_waterquality_month_records",
        "rs_industrypollutant_records",
    )
    alias_results = {
        table: {
            "score": proposals[(source["database_name"], table)]["proposed_score"],
            "proposed": proposals[(source["database_name"], table)][
                "proposed_decision"
            ],
            "reason": proposals[(source["database_name"], table)][
                "proposed_reason"
            ],
        }
        for table in aliases
    }

    report = {
        "mode": "REPORT_ONLY",
        "profile_count": len(profiles),
        "eligibility": dict(sorted(eligibility_counts.items())),
        "proposed": dict(sorted(proposed_counts.items())),
        "projected_effective": projected_effective,
        "migration": {
            old or "unset": dict(sorted(counts.items()))
            for old, counts in sorted(migration.items())
        },
        "active_scope_transitions": sorted(
            transitions,
            key=lambda item: item["table"],
        ),
        "physical_shard_families": {
            family: sorted(items, key=lambda item: item["table"])
            for family, items in sorted(shard_families.items())
        },
        "compound_aliases": alias_results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

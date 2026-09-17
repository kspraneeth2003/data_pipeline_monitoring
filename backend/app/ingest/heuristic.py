"""Turns a parsed repository into concrete check proposals, by rule.

This is the deterministic half of repo ingestion. It runs first and always, and
its output is what the LLM is asked to improve on - so a failed, slow or absent
LLM costs quality, never the feature. That mirrors how RCA already degrades:
`graph.py` falls back from LLM synthesis to a heuristic, and ingestion should
not introduce a second, harder failure mode.

The guiding rule is that every proposal must be traceable to something the DDL
actually says. A MERGE states which rows should land where and keyed on what; a
task's SCHEDULE states how often; a CREATE TABLE states the column contract.
Where the DDL says nothing - what null rate is acceptable, which columns matter
most - this module proposes nothing, and leaves that to the LLM and the user.
"""

import re
from typing import TypedDict

from app.ingest.ddl_parser import ParsedMerge, ParsedRepo, ParsedTable

# Schema and table naming that marks a raw landing layer. A bronze->silver
# parity check is only meaningful when the source really is untyped landed
# data; run against two typed tables it just restates a row-count check.
LANDING_SCHEMAS = {"BRONZE", "RAW", "LANDING", "STAGE", "STAGING", "SRC", "SOURCE"}
LANDING_TABLE_SUFFIXES = ("_RAW", "_LANDING", "_STAGE")

# Column names that conventionally carry the load/update time.
LOADED_AT_COLUMNS = ("LOADED_AT", "INGESTED_AT", "LANDED_AT", "_LOADED_AT", "LOAD_TS")
UPDATED_AT_COLUMNS = ("UPDATED_AT", "MODIFIED_AT", "LAST_UPDATED", "REFRESHED_AT")
SEQUENCE_COLUMNS = ("RECORD_ID", "SEQ_ID", "INGEST_ID", "_ID")

TIMESTAMP_TYPES = ("TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "DATETIME")

# How far past a task's own cadence a table may fall before freshness fails.
# Generous on purpose: a check that fires on one slow run gets muted, and a
# muted check protects nothing.
FRESHNESS_SLACK = 6
MIN_FRESHNESS_MINUTES = 15

# A bronze row is allowed this long to reach silver before its absence counts
# as loss rather than ordinary pipeline latency.
DEFAULT_LAG_MINUTES = 5


class CheckProposal(TypedDict):
    key: str
    name: str
    description: str
    rationale: str
    type: str
    schedule: str
    database: str
    config: dict
    source: str
    concerns: list[str]


def _database_of(fqn: str) -> str:
    return fqn.split(".")[0]


def _schema_of(fqn: str) -> str:
    parts = fqn.split(".")
    return parts[1] if len(parts) > 2 else ""


def _table_of(fqn: str) -> str:
    return fqn.split(".")[-1]


def is_landing_object(fqn: str) -> bool:
    return _schema_of(fqn).upper() in LANDING_SCHEMAS or _table_of(fqn).upper().endswith(
        LANDING_TABLE_SUFFIXES
    )


def _find_column(table: ParsedTable | None, candidates: tuple[str, ...]) -> str | None:
    if not table:
        return None
    names = {c["name"].upper() for c in table["columns"]}
    for candidate in candidates:
        if candidate in names:
            return candidate
    return None


def _find_timestamp_column(table: ParsedTable | None) -> str | None:
    """Prefers a conventionally-named column, then any timestamp column."""
    named = _find_column(table, UPDATED_AT_COLUMNS) or _find_column(table, LOADED_AT_COLUMNS)
    if named or not table:
        return named
    for column in table["columns"]:
        if column["data_type"].upper().startswith(TIMESTAMP_TYPES):
            return column["name"]
    return None


def _freshness_minutes(cadence_minutes: float | None) -> float:
    if not cadence_minutes:
        return 90.0
    return max(MIN_FRESHNESS_MINUTES, round(cadence_minutes * FRESHNESS_SLACK))


def _tables_by_fqn(parsed: ParsedRepo) -> dict[str, ParsedTable]:
    return {t["fqn"]: t for t in parsed["tables"]}


def _payload_key(expression: str) -> str | None:
    """`RAW_PAYLOAD:warehouse::STRING` -> `warehouse`. None if not a VARIANT path."""
    match = re.search(r":([A-Za-z_][\w$]*)\s*(?:::|$)", expression)
    return match.group(1) if match else None


def _mapping_concerns(merge: ParsedMerge) -> list[str]:
    """Flags value columns whose payload key does not match the column name.

    This is the limit of deriving a check from a MERGE, and it is worth stating
    plainly: the generated check asserts that the MERGE did what the MERGE says,
    not that silver matches the source data. A MERGE reading the wrong payload
    field produces a check that agrees with it and passes.

    A name mismatch is the visible symptom of exactly that. It is not proof of a
    bug - a genuine rename looks identical - so this flags rather than fails,
    and leaves the judgement to the person reviewing the proposal.
    """
    concerns: list[str] = []
    for column in merge["value_columns"]:
        key = _payload_key(column["source_expr"])
        if key and key.upper() != column["name"].upper():
            concerns.append(
                f"{column['name']} is populated from payload key `{key}`. The names differ, so this "
                f"check will agree with the MERGE even if the MERGE reads the wrong field - confirm "
                f"`{key}` is really what the source sends."
            )
    return concerns


def _b2s_proposal(merge: ParsedMerge, tables: dict[str, ParsedTable]) -> CheckProposal | None:
    source, target = merge["source"], merge["target"]
    if not source or not merge["key_columns"]:
        return None

    bronze_table = tables.get(source)
    loaded_at = _find_column(bronze_table, LOADED_AT_COLUMNS)
    if not loaded_at:
        # Without a load timestamp there is no way to tell a row that is late
        # from a row that was lost, and every in-flight row reads as missing.
        return None

    return {
        "key": f"b2s:{source}->{target}",
        "name": f"{_table_of(target).title().replace('_', ' ')} bronze -> silver: dedup + parity",
        "description": (
            f"Every settled row in {source} should appear exactly once in {target}, "
            "with values intact."
        ),
        "rationale": (
            f"Derived from the MERGE in {merge['file_path']}. The key and the column "
            f"mapping are the contract that MERGE states; this check asserts it held."
        ),
        "type": "BRONZE_TO_SILVER_PARITY",
        "schedule": "*/10 * * * *",
        "database": _database_of(target),
        "config": {
            "bronzeObject": source,
            "silverObject": target,
            "bronzeLoadedAtColumn": loaded_at,
            "bronzeSequenceColumn": _find_column(bronze_table, SEQUENCE_COLUMNS),
            "lagMinutes": DEFAULT_LAG_MINUTES,
            "keyColumns": [
                {"name": k["name"], "bronze": k["source_expr"], "silver": k["target_expr"]}
                for k in merge["key_columns"]
            ],
            "valueColumns": [
                {"name": v["name"], "bronze": v["source_expr"], "silver": v["target_expr"]}
                for v in merge["value_columns"]
            ],
        },
        "source": "heuristic",
        "concerns": _mapping_concerns(merge),
    }


def _row_count_proposal(merge: ParsedMerge) -> CheckProposal | None:
    source, target = merge["source"], merge["target"]
    if not source or merge["filtered"]:
        # A filtered MERGE is *supposed* to produce fewer target rows than
        # source rows. Proposing parity there would fail forever on correct
        # data, which is the fastest way to teach someone to ignore this tool.
        return None

    return {
        "key": f"rowcount:{source}->{target}",
        "name": f"{_table_of(source).title().replace('_', ' ')}: {_schema_of(source).lower()} vs {_schema_of(target).lower()} row count",
        "description": f"Every row in {source} should show up in {target}.",
        "rationale": (
            f"The MERGE in {merge['file_path']} copies {source} into {target} with no "
            "row filter, so the two should stay in step."
        ),
        "type": "ROW_COUNT",
        "schedule": "*/5 * * * *",
        "database": _database_of(source),
        "config": {"object": source, "comparisonObject": target, "toleranceAbs": 0},
        "source": "heuristic",
        "concerns": [],
    }


def _freshness_proposal(
    target: str, tables: dict[str, ParsedTable], cadence: dict[str, float]
) -> CheckProposal | None:
    table = tables.get(target)
    timestamp_column = _find_timestamp_column(table)
    if not timestamp_column:
        return None

    cadence_minutes = cadence.get(target)
    max_age = _freshness_minutes(cadence_minutes)
    basis = (
        f"the task writing it runs every {cadence_minutes:g} minute(s), so {max_age:g} "
        f"minutes allows {FRESHNESS_SLACK} missed runs before alerting"
        if cadence_minutes
        else "no task cadence was found in the DDL, so this threshold is a default worth reviewing"
    )

    return {
        "key": f"freshness:{target}",
        "name": f"{_table_of(target).title().replace('_', ' ')} freshness",
        "description": f"{target} should be refreshed at least every {max_age:g} minutes.",
        "rationale": f"Derived from {timestamp_column} on the table; {basis}.",
        "type": "FRESHNESS",
        "schedule": "*/10 * * * *",
        "database": _database_of(target),
        "config": {
            "object": target,
            "timestampColumn": timestamp_column,
            "maxAgeMinutes": max_age,
        },
        "source": "heuristic",
        "concerns": [],
    }


def _schema_drift_proposal(table: ParsedTable) -> CheckProposal | None:
    if not table["columns"] or is_landing_object(table["fqn"]):
        # Landing tables are a VARIANT blob by design - there is no column
        # contract there to drift away from.
        return None

    return {
        "key": f"drift:{table['fqn']}",
        "name": f"{_table_of(table['fqn']).title().replace('_', ' ')}: schema drift",
        "description": f"Guards against unexpected schema changes to {table['fqn']}.",
        "rationale": (
            f"Column contract lifted from the CREATE TABLE in {table['file_path']} "
            f"({len(table['columns'])} columns)."
        ),
        "type": "SCHEMA_DRIFT",
        "schedule": "*/30 * * * *",
        "database": table["database"],
        "config": {
            "object": table["fqn"],
            "expectedColumns": [
                {"name": c["name"], "dataType": c["data_type"]} for c in table["columns"]
            ],
        },
        "source": "heuristic",
        "concerns": [],
    }


def propose_checks(parsed: ParsedRepo) -> list[CheckProposal]:
    """Generates every check the DDL justifies, in the order a reviewer reads them."""
    tables = _tables_by_fqn(parsed)
    proposals: list[CheckProposal] = []

    for merge in parsed["merges"]:
        source = merge["source"]
        if source and is_landing_object(source):
            proposal = _b2s_proposal(merge, tables)
        else:
            proposal = _row_count_proposal(merge)
        if proposal:
            proposals.append(proposal)

    for target in dict.fromkeys(m["target"] for m in parsed["merges"]):
        proposal = _freshness_proposal(target, tables, parsed["cadence"])
        if proposal:
            proposals.append(proposal)

    for table in parsed["tables"]:
        proposal = _schema_drift_proposal(table)
        if proposal:
            proposals.append(proposal)

    # A repo can define the same object in more than one file; the first
    # proposal for a key is the one whose file the parser already preferred.
    seen: set[str] = set()
    unique: list[CheckProposal] = []
    for proposal in proposals:
        if proposal["key"] in seen:
            continue
        seen.add(proposal["key"])
        unique.append(proposal)
    return unique


def propose_databases(parsed: ParsedRepo) -> list[dict]:
    """The databases the repo describes, each with the schemas it defines and
    the file that defines them - the per-project replacement for the hardcoded
    map in `rca/object_repo_map.py`."""
    databases: dict[str, dict] = {}
    for schema_fqn, file_path in sorted(parsed["schemas"].items()):
        database_name, schema_name = schema_fqn.split(".")
        entry = databases.setdefault(
            database_name, {"name": database_name, "repo_paths": {}, "tables": []}
        )
        entry["repo_paths"][schema_name] = file_path

    for table in parsed["tables"]:
        entry = databases.get(table["database"])
        if entry is not None:
            entry["tables"].append(table["fqn"])

    for entry in databases.values():
        layers = sorted({s.lower() for s in entry["repo_paths"]})
        entry["description"] = (
            f"{', '.join(layers)} layer{'s' if len(layers) != 1 else ''} "
            f"({len(entry['tables'])} table{'s' if len(entry['tables']) != 1 else ''}), "
            "derived from the repository."
        )
    return sorted(databases.values(), key=lambda d: d["name"])

"""Turns a parsed repository into concrete check proposals, by rule.

This is the deterministic half of repo ingestion. It runs first and always, and
its output is what the LLM is asked to improve on - so a failed, slow or absent
LLM costs quality, never the feature. That mirrors how RCA already degrades:
`graph.py` falls back from LLM synthesis to a heuristic, and ingestion should
not introduce a second, harder failure mode.

The guiding rule is that every proposal must be traceable to something the DDL
actually says. A MERGE states which rows should land where and keyed on what; a
task's SCHEDULE states how often; a CREATE TABLE states the column contract and,
where it says NOT NULL, the null contract too. Where the DDL says nothing - what
*non-zero* null rate is acceptable, which columns matter most - this module
proposes nothing, and leaves that to the LLM and the user.

The second rule is that silence is not coverage. `assess_coverage` reports the
tables this module could *not* cover and why, because a thin proposal list and a
clean pipeline look identical otherwise, and the thin list is the one that shows
up when the parser met a repo shape it does not understand.
"""

import re
from typing import TypedDict

from app.ingest.ddl_parser import ParsedColumn, ParsedMerge, ParsedRepo, ParsedTable

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


def _settle_column(table: ParsedTable | None, landing: bool) -> str | None:
    """The column saying when a source row became available to the MERGE.

    Parity needs this to tell a row that is *late* from a row that was *lost*:
    without it every in-flight row reads as loss and the check cries wolf on a
    healthy pipeline.

    Which column that is depends on the layer. Append-only landing tables stamp
    a load time; a typed table that is itself maintained by a MERGE stamps an
    update time instead, and using the wrong one silently shifts the settling
    window onto a column that does not advance.
    """
    if landing:
        return _find_column(table, LOADED_AT_COLUMNS) or _find_column(table, UPDATED_AT_COLUMNS)
    return _find_column(table, UPDATED_AT_COLUMNS) or _find_column(table, LOADED_AT_COLUMNS)


def _parity_proposal(merge: ParsedMerge, tables: dict[str, ParsedTable]) -> CheckProposal | None:
    """Key-and-value parity for one MERGE, at whatever layer it sits.

    This used to fire only when the source was a landing table, on the grounds
    that comparing two typed tables "just restates a row-count check". That was
    wrong, and it left every gold table checked by row count alone. A MERGE
    states its key in the ON clause and its column mapping in the SELECT at any
    layer; silver -> gold can lose a row or write the wrong value exactly the
    way bronze -> silver can, and a row count sees neither.

    What is genuinely bronze-specific is only that one side may be untyped
    VARIANT - and `ParityColumn` already carries an explicit expression per
    side to handle that, so it costs nothing when both sides are typed.
    """
    source, target = merge["source"], merge["target"]
    if not source or not merge["key_columns"]:
        return None

    landing = is_landing_object(source)
    source_table = tables.get(source)
    settle_column = _settle_column(source_table, landing)
    if not settle_column:
        # No timestamp on the source means no settling window, and without one
        # every row the MERGE has not reached yet counts as loss.
        return None

    layer_phrase = "bronze -> silver" if landing else (
        f"{_schema_of(source).lower()} -> {_schema_of(target).lower()}"
    )
    concerns = _mapping_concerns(merge)
    filter_note = ""
    if merge["filter_predicate"]:
        filter_note = (
            f" The MERGE only copies rows matching `{merge['filter_predicate']}`, so the same "
            "condition is applied to the source side here."
        )
        concerns.append(
            f"The source side is filtered by `{merge['filter_predicate']}`, lifted from the "
            f"MERGE. If that condition ever stops matching what the target actually holds - "
            f"say the filter is changed in one place and not the other - this check will "
            f"report loss that is not real. Confirm it reads correctly."
        )

    return {
        "key": f"parity:{source}->{target}",
        "name": f"{_table_of(target).title().replace('_', ' ')} {layer_phrase}: dedup + parity",
        "description": (
            f"Every settled row in {source} should appear exactly once in {target}, "
            "with values intact."
        ),
        "rationale": (
            f"Derived from the MERGE in {merge['file_path']}. The key and the column "
            f"mapping are the contract that MERGE states; this check asserts it held."
            f"{filter_note}"
        ),
        "type": "BRONZE_TO_SILVER_PARITY",
        "schedule": "*/10 * * * *",
        "database": _database_of(target),
        "config": {
            "bronzeObject": source,
            "silverObject": target,
            "bronzeLoadedAtColumn": settle_column,
            "bronzeSequenceColumn": _find_column(source_table, SEQUENCE_COLUMNS),
            "sourceFilter": merge["filter_predicate"],
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
        "concerns": concerns,
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


def _null_rate_proposal(table: ParsedTable, column: ParsedColumn) -> CheckProposal:
    """A NOT NULL column is a null contract the DDL states outright.

    Every other null-rate threshold is a judgement call - is 2% null on this
    column normal? - and this module deliberately does not guess at those. Zero
    is different: the DDL already said zero, so the check restates the contract
    rather than inventing one.

    Worth having even though the warehouse enforces the constraint on write:
    the constraint binds what the pipeline *can* insert, and this observes what
    the table actually holds. They come apart whenever the column is added
    later, backfilled, or the constraint is dropped in an ALTER nobody noticed
    - which is exactly the change this platform exists to catch.
    """
    return {
        "key": f"nullrate:{table['fqn']}.{column['name']}",
        "name": f"{_table_of(table['fqn']).title().replace('_', ' ')}: {column['name']} not null",
        "description": f"{column['name']} in {table['fqn']} should never be null.",
        "rationale": (
            f"The CREATE TABLE in {table['file_path']} declares {column['name']} NOT NULL, "
            "so any null is a contract violation rather than a threshold judgement."
        ),
        "type": "NULL_RATE",
        "schedule": "*/30 * * * *",
        "database": table["database"],
        "config": {"object": table["fqn"], "column": column["name"], "maxNullRatio": 0},
        "source": "heuristic",
        "concerns": [],
    }


def propose_checks(parsed: ParsedRepo) -> list[CheckProposal]:
    """Generates every check the DDL justifies, in the order a reviewer reads them."""
    tables = _tables_by_fqn(parsed)
    proposals: list[CheckProposal] = []

    for merge in parsed["merges"]:
        # Parity is attempted at every layer, not only from a landing table.
        # A row count is the fallback for a MERGE parity cannot describe -
        # no key in the ON clause, or no timestamp to settle against.
        proposal = _parity_proposal(merge, tables) or _row_count_proposal(merge)
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

    for table in parsed["tables"]:
        if is_landing_object(table["fqn"]):
            continue
        for column in table["columns"]:
            if not column["nullable"]:
                proposals.append(_null_rate_proposal(table, column))

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


class TableCoverage(TypedDict):
    table: str
    parity: bool
    freshness: bool
    schema_drift: bool
    # Why this table has no parity check, in a sentence a reviewer can act on.
    # Empty when it has one.
    gaps: list[str]


class CoverageReport(TypedDict):
    tables_total: int
    # Tables a parity check is *expected* for: everything but the landing
    # layer, which is append-only with no upstream in this repo. Reporting
    # against all tables instead would make a fully covered pipeline read as
    # partially covered forever.
    tables_expecting_parity: int
    tables_with_parity: int
    # Only the tables missing something. A reviewer reads this to decide
    # whether the proposal set is thin because the pipeline is simple or
    # because the parser did not understand the repo.
    uncovered: list[TableCoverage]
    summary: str


def _parity_gap_reason(
    table: ParsedTable, merges_by_target: dict[str, ParsedMerge], tables: dict[str, ParsedTable]
) -> str:
    """Why parity could not be derived for one table - specific, not generic.

    "3 tables have no parity check" tells a reviewer nothing they can act on.
    "PRODUCT_STOCK_SUMMARY is written by a MERGE with no ON clause the parser
    could read" tells them whether to fix the repo, fix the parser, or write
    the check by hand.
    """
    merge = merges_by_target.get(table["fqn"])
    if merge is None:
        return (
            "No MERGE in the repository writes this table. It may be loaded by a COPY, a "
            "view, or a tool outside this repo - parity needs a stated source to compare "
            "against, so this one has to be written by hand."
        )
    if not merge["source"]:
        return (
            f"The MERGE in {merge['file_path']} does not name a single source table the "
            "parser could resolve (a join or a subquery, most likely), so there is no one "
            "object to compare against."
        )
    if not merge["key_columns"]:
        return (
            f"The MERGE in {merge['file_path']} has no ON condition the parser could read as "
            "a key. Parity is keyed comparison, so without a key it falls back to row count."
        )
    source_table = tables.get(merge["source"])
    if source_table is None:
        return (
            f"The MERGE reads {merge['source']}, which is not defined by any CREATE TABLE in "
            "this repository - so its columns, and any load timestamp, are unknown here."
        )
    return (
        f"{merge['source']} has no load or update timestamp column, so there is no way to "
        "tell a row still in flight from a row that was lost. Every in-flight row would "
        "read as loss."
    )


def assess_coverage(parsed: ParsedRepo, proposals: list[CheckProposal]) -> CoverageReport:
    """What the rules covered, and what they could not.

    Ingestion's failure mode is quiet: a repo the parser does not understand
    yields a short proposal list, and a short list is indistinguishable from a
    simple pipeline. This makes the difference explicit, so "12 checks" is
    read alongside "and 4 tables no rule could cover, for these reasons."
    """
    tables = _tables_by_fqn(parsed)
    merges_by_target = {m["target"]: m for m in parsed["merges"]}

    covered: dict[str, set[str]] = {}
    for proposal in proposals:
        config = proposal["config"]
        # A parity check covers its target; every other type covers the single
        # object it names.
        objects = (
            [config.get("silverObject")]
            if proposal["type"] == "BRONZE_TO_SILVER_PARITY"
            else [config.get("object"), config.get("comparisonObject")]
        )
        for fqn in objects:
            if isinstance(fqn, str):
                covered.setdefault(fqn, set()).add(proposal["type"])

    uncovered: list[TableCoverage] = []
    with_parity = 0
    expecting = 0
    for table in parsed["tables"]:
        # A landing table is append-only with no upstream in this repo, so it
        # is not expected to have parity and is not counted as a gap.
        if is_landing_object(table["fqn"]):
            continue
        expecting += 1

        types = covered.get(table["fqn"], set())
        if "BRONZE_TO_SILVER_PARITY" in types:
            with_parity += 1
            continue

        uncovered.append(
            {
                "table": table["fqn"],
                "parity": False,
                "freshness": "FRESHNESS" in types,
                "schema_drift": "SCHEMA_DRIFT" in types,
                "gaps": [_parity_gap_reason(table, merges_by_target, tables)],
            }
        )

    total = len(parsed["tables"])
    landing = total - expecting
    if not expecting:
        summary = (
            f"No table in this repository is expected to have a parity check "
            f"({total} parsed, all of them landing tables). That usually means the "
            "repo defines the raw layer only, or the parser did not recognise its shape."
        )
    elif not uncovered:
        summary = (
            f"Every table that should have a parity check has one "
            f"({with_parity} of {expecting}; {landing} landing table(s) exempt)."
        )
    else:
        summary = (
            f"{with_parity} of {expecting} tables have a parity check. "
            f"{len(uncovered)} could not be covered by rule and need a decision - "
            "see the reason on each."
        )

    return {
        "tables_total": total,
        "tables_expecting_parity": expecting,
        "tables_with_parity": with_parity,
        "uncovered": uncovered,
        "summary": summary,
    }


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
